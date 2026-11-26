# -*- coding: utf-8 -*-
"""
视频小工具（/video-tools）
==========================
上传一次视频，然后复用服务器已有的 ffmpeg 做：
截取片段、转 GIF、提取音频、抽帧 / 九宫格、压缩瘦身。

- ffmpeg 来自 imageio-ffmpeg（播客已经在用），不新增任何依赖，镜像体积不变。
- 用户输入只变成白名单里的数字和枚举，再拼进 argv 列表，绝不经过 shell。
- 源文件按账号哈希隔离；任务走 SQLite 单线程队列，成品落盘后按 TTL 清理。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import storage

router = APIRouter()

TOOL = 'video-tools'
_ROOT = Path(__file__).resolve().parent
_TEMPLATE_FILE = _ROOT / 'templates' / 'video-tools.html'
_JOB_ID_RE = re.compile(r'^[0-9a-f]{32}$')
_PART_SUFFIX = '.part'

UPLOAD_TYPES = {
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.mov': 'video/quicktime',
    '.webm': 'video/webm',
    '.mkv': 'video/x-matroska',
    '.avi': 'video/x-msvideo',
    '.flv': 'video/x-flv',
    '.wmv': 'video/x-ms-wmv',
    '.mpeg': 'video/mpeg',
    '.mpg': 'video/mpeg',
    '.ts': 'video/mp2t',
    '.mts': 'video/mp2t',
    '.3gp': 'video/3gpp',
}

KINDS = {
    'clip': '截取片段',
    'gif': '转 GIF',
    'audio': '提取音频',
    'frames': '抽帧',
    'contact': '九宫格',
    'compress': '压缩瘦身',
}

KIND_LIST = [
    {'key': 'clip', 'label': '截取片段', 'hint': '按起止时间导出 MP4，可快速复制或精确重编码'},
    {'key': 'gif', 'label': '转 GIF', 'hint': '1–10 秒，自动生成调色板，动图更干净'},
    {'key': 'audio', 'label': '提取音频', 'hint': 'm4a / mp3，或转写用的 16k 单声道 WAV'},
    {'key': 'frames', 'label': '抽帧', 'hint': '按时间点抓 1–9 张 JPG，多张自动打包 ZIP'},
    {'key': 'contact', 'label': '九宫格', 'hint': '均匀取帧拼成一张总览图，快速扫完长视频'},
    {'key': 'compress', 'label': '压缩瘦身', 'hint': '三档画质，最高 1080p，源视频最多 10 分钟'},
]

_CLIP_MAX_SPAN = 600.0
_GIF_MAX_SPAN = 10.0
_FRAMES_MAX = 9
_CONTACT_MAX_SOURCE = 1800.0
_COMPRESS_MAX_SOURCE = 600.0

_CAP_VIDEO = 512 * 1024 * 1024
_CAP_AUDIO = 300 * 1024 * 1024
_CAP_GIF = 100 * 1024 * 1024
_CAP_IMAGE = 50 * 1024 * 1024
_CAP_ZIP = 300 * 1024 * 1024


class VideoError(RuntimeError):
    """可以原样展示给用户的处理错误。"""


# ---------------------------------------------------------------
# 配置
# ---------------------------------------------------------------
def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = (os.environ.get(name) or '').strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _max_bytes() -> int:
    return _env_int('VIDEO_MAX_MB', 300, 1, 2048) * 1024 * 1024


def _daily_limit() -> int:
    return _env_int('VIDEO_DAILY_JOBS', 30, 1, 500)


def _ttl_days() -> int:
    return _env_int('VIDEO_TTL_DAYS', 2, 1, 30)


def _max_active() -> int:
    return _env_int('VIDEO_MAX_ACTIVE', 2, 1, 8)


def _max_uploads() -> int:
    return _env_int('VIDEO_MAX_UPLOADS', 3, 1, 10)


def _video_root() -> Path:
    raw = (os.environ.get('VIDEO_DIR') or '').strip()
    if raw:
        return Path(raw).expanduser()
    return storage.db_path().parent / 'generated' / 'video'


def _user_key(user: str) -> str:
    return hashlib.sha256(('video:' + str(user)).encode('utf-8')).hexdigest()[:16]


def _user_root(user: str) -> Path:
    return _video_root() / _user_key(user)


def _upload_dir(user: str, upload_id) -> Path:
    return _user_root(user) / 'uploads' / str(int(upload_id))


def _job_dir(user: str, job_id: str):
    if not _JOB_ID_RE.match(str(job_id or '')):
        return None
    return _user_root(user) / 'jobs' / str(job_id)


def _source_path(upload: dict) -> Path:
    stored = Path(str(upload.get('stored_name') or '')).name
    return _upload_dir(upload['user'], upload['id']) / stored


def _remove_dir(path) -> None:
    if path:
        shutil.rmtree(str(path), ignore_errors=True)


def _today_start() -> str:
    return time.strftime('%Y-%m-%d 00:00:00')


def _cutoff(days: int) -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S',
                         time.localtime(time.time() - days * 86400))


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


# ---------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------
_encoder_lock = threading.Lock()
_encoder_cache = {'path': '', 'have': None}


def _ffmpeg_exe() -> str:
    for name in ('VIDEO_FFMPEG', 'PODCAST_FFMPEG'):
        raw = (os.environ.get(name) or '').strip()
        if raw and Path(raw).is_file():
            return raw
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).is_file():
            return path
    except Exception:
        pass
    return ''


def _encoders() -> set:
    path = _ffmpeg_exe()
    with _encoder_lock:
        if _encoder_cache['path'] == path and _encoder_cache['have'] is not None:
            return _encoder_cache['have']
    have = set()
    if path:
        try:
            proc = subprocess.run(
                [path, '-hide_banner', '-encoders'], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=20, check=False)
            text = (proc.stdout or b'').decode('utf-8', 'replace')
            for name in ('libx264', 'aac', 'libmp3lame'):
                if re.search(r'\b' + name + r'\b', text):
                    have.add(name)
        except (OSError, subprocess.SubprocessError):
            have = set()
    with _encoder_lock:
        _encoder_cache['path'] = path
        _encoder_cache['have'] = have
    return have


def _ready_info() -> dict:
    if not _ffmpeg_exe():
        return {
            'ready': False, 'ffmpegReady': False,
            'missing': '服务器缺少 ffmpeg，无法处理视频。',
        }
    have = _encoders()
    missing = [name for name in ('libx264', 'aac', 'libmp3lame') if name not in have]
    if missing:
        return {
            'ready': False, 'ffmpegReady': True,
            'missing': '这台服务器的 ffmpeg 缺少编码器：%s。' % '、'.join(missing),
        }
    return {'ready': True, 'ffmpegReady': True, 'missing': ''}


def _run(cmd: list, timeout: int = 300) -> None:
    """运行 ffmpeg；参数全是列表，不经过 shell。"""
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise VideoError('ffmpeg 处理超时（%d 秒），请缩短视频或降低画质。' % timeout) from exc
    except OSError as exc:
        raise VideoError('无法启动 ffmpeg：%s' % str(exc)[:160]) from exc
    if proc.returncode != 0:
        err = (proc.stderr or b'').decode('utf-8', 'replace').strip().replace('\n', ' ')
        raise VideoError('ffmpeg 处理失败：' + (err[-400:] or '未知错误'))


def _base_cmd() -> list:
    return [_ffmpeg_exe(), '-y', '-hide_banner', '-loglevel', 'error', '-nostdin']


# ---------------------------------------------------------------
# 探测
# ---------------------------------------------------------------
_DURATION_RE = re.compile(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)')
_VIDEO_RE = re.compile(r'Video:\s*([A-Za-z0-9_]+)')
_AUDIO_RE = re.compile(r'Audio:\s*([A-Za-z0-9_]+)')
_SIZE_RE = re.compile(r'(\d{2,5})x(\d{2,5})')
_FPS_RE = re.compile(r'(\d+(?:\.\d+)?)\s*fps')


def _parse_probe_stderr(text: str) -> dict:
    duration = 0.0
    match = _DURATION_RE.search(text or '')
    if match:
        duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 \
            + float(match.group(3))
    video_codec = ''
    width = height = 0
    fps = 0.0
    video_line = ''
    for line in (text or '').splitlines():
        if 'Video:' in line:
            video_line = line
            codec = _VIDEO_RE.search(line)
            if codec:
                video_codec = codec.group(1).lower()
            size = _SIZE_RE.search(line)
            if size:
                width, height = int(size.group(1)), int(size.group(2))
            rate = _FPS_RE.search(line)
            if rate:
                fps = float(rate.group(1))
            break
    audio_codec = ''
    for line in (text or '').splitlines():
        if 'Audio:' in line:
            codec = _AUDIO_RE.search(line)
            if codec:
                audio_codec = codec.group(1).lower()
            break
    return {
        'duration': round(duration, 3),
        'width': width,
        'height': height,
        'fps': round(fps, 3),
        'video_codec': video_codec,
        'audio_codec': audio_codec,
        'has_audio': bool(audio_codec),
        'video_line': video_line,
    }


def _probe(path: Path) -> dict:
    """用 ffmpeg -i 读时长/分辨率/编码；ffprobe 不在镜像里。"""
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        raise VideoError('服务器缺少 ffmpeg，无法读取视频信息。')
    text = ''
    try:
        proc = subprocess.run(
            [ffmpeg, '-hide_banner', '-nostdin', '-i', str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
        text = (proc.stderr or b'').decode('utf-8', 'replace')
    except subprocess.TimeoutExpired as exc:
        raise VideoError('读取视频信息超时，请换一个文件或先压缩。') from exc
    except OSError as exc:
        raise VideoError('无法启动 ffmpeg：%s' % str(exc)[:160]) from exc

    info = _parse_probe_stderr(text)

    if not info['video_codec'] or info['width'] <= 0 or info['height'] <= 0:
        # 极少数容器 ffmpeg 的 -i 输出不完整，退回 imageio 的元数据解析。
        try:
            import imageio_ffmpeg
            gen = imageio_ffmpeg.read_frames(str(path))
            try:
                meta = next(gen)
            finally:
                try:
                    gen.close()
                except Exception:
                    pass
            size = meta.get('size') or (0, 0)
            info['width'] = info['width'] or int(size[0] or 0)
            info['height'] = info['height'] or int(size[1] or 0)
            info['fps'] = info['fps'] or float(meta.get('fps') or 0)
            info['duration'] = info['duration'] or float(meta.get('duration') or 0)
            info['video_codec'] = info['video_codec'] or str(meta.get('codec') or '')
        except Exception:
            pass

    if not info['video_codec'] or info['width'] <= 0 or info['height'] <= 0:
        raise VideoError('没有识别到视频画面，请换一个 MP4 / MOV / MKV 文件。')
    if info['duration'] <= 0:
        try:
            import imageio_ffmpeg
            _frames, secs = imageio_ffmpeg.count_frames_and_secs(str(path))
            info['duration'] = round(float(secs or 0), 3)
        except Exception:
            pass
    if info['duration'] <= 0:
        raise VideoError('无法读取视频时长，请换一个文件。')

    mime = UPLOAD_TYPES.get(path.suffix.lower(), 'video/mp4')
    return {
        'duration': info['duration'],
        'width': info['width'],
        'height': info['height'],
        'fps': info['fps'],
        'video_codec': info['video_codec'],
        'audio_codec': info['audio_codec'],
        'has_audio': info['has_audio'],
        'mime': mime,
    }


def _looks_like_video(head: bytes) -> bool:
    if len(head) < 12:
        return False
    if b'ftyp' in head[:32]:
        return True
    if head[:4] == b'\x1aE\xdf\xa3':           # EBML / MKV / WebM
        return True
    if head[:4] == b'RIFF' and head[8:12] == b'AVI ':
        return True
    if head[:3] == b'FLV':
        return True
    if head[:4] in (b'\x00\x00\x01\xba', b'\x00\x00\x01\xb3'):  # MPEG PS / ES
        return True
    if head[:4] == b'\x30\x26\xb2\x75':       # ASF / WMV
        return True
    if head[0] == 0x47:                        # MPEG-TS
        return True
    if head[4:8] in (b'moov', b'mdat', b'wide', b'free', b'skip'):
        return True
    return False


# ---------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------
def _need_num(raw: dict, key: str, default=None) -> float:
    if key not in raw or raw.get(key) in (None, ''):
        if default is None:
            raise ValueError('缺少参数：%s' % key)
        return float(default)
    try:
        value = float(raw.get(key))
    except (TypeError, ValueError):
        raise ValueError('参数格式不对：%s' % key)
    if not math.isfinite(value):
        raise ValueError('参数格式不对：%s' % key)
    return value


def _clean_params(kind: str, raw, upload: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    duration = float(upload.get('duration') or 0)

    if kind == 'clip':
        start = round(_need_num(raw, 'start', 0), 3)
        end = round(_need_num(raw, 'end'), 3)
        if start < 0:
            raise ValueError('开始时间不能小于 0。')
        if duration > 0 and start >= duration:
            raise ValueError('开始时间已经超过视频时长。')
        if duration > 0:
            end = min(end, round(duration, 3))
        if end <= start:
            raise ValueError('结束时间必须晚于开始时间。')
        span = round(end - start, 3)
        if span < 0.1:
            raise ValueError('截取时长至少 0.1 秒。')
        if span > _CLIP_MAX_SPAN:
            raise ValueError('截取时长最多 %d 秒。' % int(_CLIP_MAX_SPAN))
        mode = str(raw.get('mode') or 'fast')
        if mode not in ('fast', 'precise'):
            raise ValueError('截取方式只能是 fast 或 precise。')
        return {'start': start, 'end': end, 'mode': mode}

    if kind == 'gif':
        start = round(_need_num(raw, 'start', 0), 3)
        length = round(_need_num(raw, 'duration', 3), 3)
        if start < 0:
            raise ValueError('开始时间不能小于 0。')
        if duration > 0 and start >= duration:
            raise ValueError('开始时间已经超过视频时长。')
        if length < 1:
            raise ValueError('GIF 时长至少 1 秒。')
        if length > _GIF_MAX_SPAN:
            raise ValueError('GIF 时长最多 %d 秒。' % int(_GIF_MAX_SPAN))
        if duration > 0:
            length = min(length, round(duration - start, 3))
            if length < 1:
                raise ValueError('从开始时间到结尾不足 1 秒。')
        width = int(_need_num(raw, 'width', 480))
        if width not in (480, 640):
            raise ValueError('GIF 宽度只能选 480 或 640。')
        fps = int(_need_num(raw, 'fps', 15))
        if fps not in (12, 15):
            raise ValueError('GIF 帧率只能选 12 或 15。')
        return {'start': start, 'duration': length, 'width': width, 'fps': fps}

    if kind == 'audio':
        fmt = str(raw.get('format') or 'mp3').lower()
        if fmt not in ('m4a', 'mp3', 'wav'):
            raise ValueError('音频格式只能选 m4a、mp3 或 wav。')
        if not upload.get('has_audio'):
            raise ValueError('这个视频没有音轨，提取不了音频。')
        return {'format': fmt}

    if kind == 'frames':
        times = raw.get('times')
        if isinstance(times, str):
            times = re.split(r'[,，\s]+', times)
        if not isinstance(times, list):
            raise ValueError('时间点格式不对，用逗号分隔秒数。')
        values = []
        for item in times:
            if item in (None, ''):
                continue
            try:
                value = float(item)
            except (TypeError, ValueError):
                raise ValueError('时间点格式不对：%s' % item)
            if not math.isfinite(value) or value < 0:
                raise ValueError('时间点格式不对：%s' % item)
            if duration > 0 and value >= duration:
                raise ValueError('时间点超出视频时长：%s' % item)
            value = round(value, 3)
            if value not in values:
                values.append(value)
        values.sort()
        if not values:
            raise ValueError('至少填一个时间点。')
        if len(values) > _FRAMES_MAX:
            raise ValueError('一次最多抽 %d 帧。' % _FRAMES_MAX)
        return {'times': values}

    if kind == 'contact':
        grid = str(raw.get('grid') or '3x3')
        if grid not in ('3x3', '4x4'):
            raise ValueError('九宫格只能选 3x3 或 4x4。')
        if duration > _CONTACT_MAX_SOURCE:
            raise ValueError('九宫格最多处理 %d 分钟的视频。' % int(_CONTACT_MAX_SOURCE // 60))
        return {'grid': grid}

    if kind == 'compress':
        quality = str(raw.get('quality') or 'balanced')
        if quality not in ('small', 'balanced', 'clear'):
            raise ValueError('压缩画质只能是 small / balanced / clear。')
        cap = int(_need_num(raw, 'cap', 720))
        if cap not in (720, 1080):
            raise ValueError('分辨率上限只能选 720 或 1080。')
        if duration > _COMPRESS_MAX_SOURCE:
            raise ValueError('压缩最多处理 %d 分钟的视频。' % int(_COMPRESS_MAX_SOURCE // 60))
        return {'quality': quality, 'cap': cap}

    raise ValueError('不支持的操作类型。')


# ---------------------------------------------------------------
# ffmpeg 命令与执行
# ---------------------------------------------------------------
def _output(index: int, name: str, label: str, path: Path, mime: str) -> dict:
    if not path.is_file():
        raise VideoError('没有生成结果文件。')
    size = path.stat().st_size
    if size <= 0:
        raise VideoError('生成的结果是空文件。')
    return {'index': index, 'name': name, 'label': label, 'size': int(size), 'mime': mime}


def _check_cap(path: Path, cap: int) -> None:
    if path.stat().st_size > cap:
        path.unlink(missing_ok=True)
        raise VideoError('生成的文件超过 %d MB，请缩短时长或降低画质。'
                         % (cap // 1048576))


def _run_clip(src: Path, out_dir: Path, params: dict, upload: dict):
    out = out_dir / 'clip.mp4'
    start = '%.3f' % params['start']
    span = '%.3f' % (params['end'] - params['start'])
    common = _base_cmd() + ['-ss', start, '-i', str(src), '-t', span,
                            '-map', '0:v:0', '-map', '0:a?']
    fast = common + ['-c', 'copy', '-avoid_negative_ts', 'make_zero',
                     '-movflags', '+faststart', str(out)]
    precise = common + [
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt',
        'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-threads', '2',
        '-movflags', '+faststart', str(out),
    ]
    note = ''
    if params['mode'] == 'precise':
        _run(precise, timeout=900)
    else:
        try:
            _run(fast, timeout=300)
        except VideoError:
            out.unlink(missing_ok=True)
            _run(precise, timeout=900)
            note = '源视频的编码无法直接复制到 MP4，已自动改为精确重编码。'
    _check_cap(out, _CAP_VIDEO)
    return [_output(1, out.name, '截取片段', out, 'video/mp4')], note


def _run_gif(src: Path, out_dir: Path, params: dict, upload: dict):
    out = out_dir / 'animation.gif'
    vf = ('fps=%d,scale=%d:-1:flags=lanczos,split[s0][s1];'
          '[s0]palettegen=max_colors=256:stats_mode=diff[p];'
          '[s1][p]paletteuse=dither=bayer:bayer_scale=3'
          % (params['fps'], params['width']))
    cmd = _base_cmd() + [
        '-ss', '%.3f' % params['start'], '-t', '%.3f' % params['duration'],
        '-i', str(src), '-an', '-vf', vf, '-loop', '0', '-threads', '2', str(out),
    ]
    _run(cmd, timeout=300)
    _check_cap(out, _CAP_GIF)
    return [_output(1, out.name, 'GIF 动图', out, 'image/gif')], ''


def _run_audio(src: Path, out_dir: Path, params: dict, upload: dict):
    fmt = params['format']
    name = {'m4a': 'audio.m4a', 'mp3': 'audio.mp3', 'wav': 'audio.wav'}[fmt]
    mime = {'m4a': 'audio/mp4', 'mp3': 'audio/mpeg', 'wav': 'audio/wav'}[fmt]
    out = out_dir / name
    common = _base_cmd() + ['-i', str(src), '-vn', '-map', '0:a:0']
    if fmt == 'm4a':
        note = ''
        if str(upload.get('audio_codec') or '').lower() == 'aac':
            try:
                _run(common + ['-c:a', 'copy', str(out)], timeout=300)
            except VideoError:
                out.unlink(missing_ok=True)
                _run(common + ['-c:a', 'aac', '-b:a', '128k', str(out)], timeout=600)
                note = '原始音轨无法直接封装，已重新编码为 AAC。'
        else:
            _run(common + ['-c:a', 'aac', '-b:a', '128k', str(out)], timeout=600)
            note = '原始音轨不是 AAC，已重新编码为 AAC。'
    elif fmt == 'mp3':
        _run(common + ['-c:a', 'libmp3lame', '-b:a', '128k', '-ar', '44100', str(out)],
             timeout=600)
        note = ''
    else:
        _run(common + ['-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(out)],
             timeout=600)
        note = ''
    _check_cap(out, _CAP_AUDIO)
    return [_output(1, out.name, '提取的音频', out, mime)], note


def _run_frames(src: Path, out_dir: Path, params: dict, upload: dict):
    times = params['times']
    do_scale = int(upload.get('width') or 0) > 1920
    paths = []
    for pos, when in enumerate(times, 1):
        path = out_dir / ('frame_%02d.jpg' % pos)
        cmd = _base_cmd() + [
            '-ss', '%.3f' % when, '-i', str(src), '-map', '0:v:0',
            '-frames:v', '1', '-q:v', '3',
        ]
        if do_scale:
            cmd += ['-vf', "scale=w='min(1920,iw)':h=-2"]
        cmd.append(str(path))
        _run(cmd, timeout=180)
        _check_cap(path, _CAP_IMAGE)
        paths.append(path)

    outputs = [
        _output(pos, path.name, '第 %d 帧（%s 秒）' % (pos, ('%.3f' % times[pos - 1]).rstrip('0').rstrip('.')),
                path, 'image/jpeg')
        for pos, path in enumerate(paths, 1)
    ]
    if len(paths) <= 1:
        return outputs, ''

    zip_path = out_dir / 'frames.zip'
    readme = '视频抽帧\n\n原始文件：%s\n时间点（秒）：%s\n\n' % (
        upload.get('original_name') or '视频',
        ', '.join(('%.3f' % t).rstrip('0').rstrip('.') for t in times))
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for path in paths:
                zf.write(str(path), arcname=path.name)
            zf.writestr('README.txt', readme.encode('utf-8'))
    except OSError as exc:
        raise VideoError('打包抽帧结果失败：%s' % str(exc)[:160]) from exc
    _check_cap(zip_path, _CAP_ZIP)
    zip_index = len(outputs) + 1
    outputs.append(_output(zip_index, zip_path.name, '全部帧（ZIP）', zip_path,
                           'application/zip'))
    return outputs, ''


def _run_contact(src: Path, out_dir: Path, params: dict, upload: dict):
    out = out_dir / 'contact.jpg'
    duration = float(upload.get('duration') or 0)
    if duration <= 0:
        raise VideoError('无法读取视频时长，生成不了九宫格。')
    grid = params['grid']
    count = 9 if grid == '3x3' else 16
    rate = count / duration
    vf = 'fps=%.6f,scale=320:-2,tile=%s:padding=4:margin=4' % (rate, grid)
    cmd = _base_cmd() + ['-i', str(src), '-vf', vf, '-frames:v', '1',
                         '-q:v', '3', '-threads', '2', str(out)]
    _run(cmd, timeout=600)
    _check_cap(out, _CAP_IMAGE)
    return [_output(1, out.name, '九宫格总览', out, 'image/jpeg')], ''


def _run_compress(src: Path, out_dir: Path, params: dict, upload: dict):
    out = out_dir / 'compressed.mp4'
    crf = {'small': '30', 'balanced': '24', 'clear': '20'}[params['quality']]
    source_h = int(upload.get('height') or 0)
    cap_h = int(params['cap'])
    if source_h > 0:
        cap_h = min(cap_h, source_h)
    cap_h -= cap_h % 2
    cap_h = max(2, cap_h)
    cmd = _base_cmd() + ['-i', str(src), '-map', '0:v:0', '-map', '0:a?']
    if source_h != cap_h or source_h % 2:
        cmd += ['-vf', 'scale=-2:%d' % cap_h]
    cmd += [
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', crf,
        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k',
        '-threads', '2', '-movflags', '+faststart', str(out),
    ]
    _run(cmd, timeout=1800)
    _check_cap(out, _CAP_VIDEO)
    return [_output(1, out.name, '压缩后的视频', out, 'video/mp4')], ''


_RUNNERS = {
    'clip': _run_clip,
    'gif': _run_gif,
    'audio': _run_audio,
    'frames': _run_frames,
    'contact': _run_contact,
    'compress': _run_compress,
}


# ---------------------------------------------------------------
# 后台调度
# ---------------------------------------------------------------
_worker_lock = threading.Lock()
_worker_started = False
_recover_lock = threading.Lock()
_recovered = False


def _recover_once() -> None:
    global _recovered
    if _recovered:
        return
    with _recover_lock:
        if _recovered:
            return
        try:
            storage.video_job_recover_running('服务重启，视频任务中断，请重新提交。')
        except storage.StorageUnavailable:
            return
        _recovered = True


def _worker_loop() -> None:
    while not _recovered:
        _recover_once()
        if not _recovered:
            time.sleep(2)

    while True:
        try:
            job = storage.video_job_claim_next()
        except storage.StorageUnavailable:
            time.sleep(5)
            continue
        except Exception:
            time.sleep(2)
            continue
        if not job:
            time.sleep(1.2)
            continue
        try:
            _process_job(job)
        except Exception as exc:
            _remove_dir(_job_dir(job['user'], job['id']))
            try:
                storage.video_job_update(job['id'], {
                    'status': 'failed',
                    'stage': 'failed',
                    'error': '处理失败：' + str(exc)[:400],
                    'finished_at': storage.now_str(),
                })
            except Exception:
                pass


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, name='video-worker',
                                  daemon=True)
        thread.start()
        _worker_started = True


def _remember_history(job: dict, upload: dict, outputs: list) -> None:
    title = '%s · %s' % (upload.get('original_name') or '视频',
                         KINDS.get(job['kind'], job['kind']))
    payload = {
        'jobId': job['id'],
        'kind': job['kind'],
        'kindLabel': KINDS.get(job['kind'], job['kind']),
        'uploadId': job['upload_id'],
        'uploadName': upload.get('original_name') or '',
        'params': job.get('params') or {},
        'duration': upload.get('duration') or 0,
        'size': upload.get('size') or 0,
        'outputs': [{'name': out.get('name'), 'label': out.get('label'),
                     'size': out.get('size')} for out in outputs],
        'outputSize': sum(int(out.get('size') or 0) for out in outputs),
    }
    try:
        storage.add_record(TOOL, job['user'], title[:80], payload)
    except storage.StorageUnavailable:
        # 任务本身已经成功，历史记录写不进去不影响产物下载。
        pass


def _process_job(job: dict) -> None:
    upload = storage.video_upload_get(job['user'], job['upload_id'])
    if not upload:
        raise VideoError('源视频已删除，请重新上传。')
    src = _source_path(upload)
    if not src.is_file():
        raise VideoError('源视频文件已丢失，请重新上传。')

    out_dir = _job_dir(job['user'], job['id'])
    if not out_dir:
        raise VideoError('任务编号异常。')
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        free = shutil.disk_usage(str(out_dir)).free
    except OSError:
        free = 0
    need = max(500 * 1024 * 1024, int(upload.get('size') or 0) * 2)
    if free and free < need:
        raise VideoError('服务器磁盘空间不足，请先删除一些旧素材或任务。')

    kind = job.get('kind') or ''
    runner = _RUNNERS.get(kind)
    if not runner:
        raise VideoError('不支持的操作类型。')

    storage.video_job_update(job['id'], {'stage': 'transcoding', 'progress': 10})
    outputs, note = runner(src, out_dir, job.get('params') or {}, upload)
    for out in outputs:
        path = out_dir / str(out.get('name') or '')
        if not path.is_file() or not path.resolve().is_relative_to(out_dir.resolve()):
            raise VideoError('生成的结果文件异常。')

    total = sum(int(out.get('size') or 0) for out in outputs)
    primary = outputs[-1]['index'] if kind == 'frames' and len(outputs) > 1 else 1
    storage.video_job_update(job['id'], {
        'status': 'succeeded',
        'stage': 'done',
        'progress': 100,
        'outputs': outputs,
        'primary_index': primary,
        'output_size': total,
        'note': note,
        'finished_at': storage.now_str(),
    })
    _remember_history(job, upload, outputs)


# ---------------------------------------------------------------
# 清理
# ---------------------------------------------------------------
def _cleanup_temp(user: str) -> None:
    """上传中途被打断会留下 .tmp-* 暂存目录，超过 6 小时就清掉。"""
    parent = _user_root(user) / 'uploads'
    if not parent.is_dir():
        return
    cutoff = time.time() - 6 * 3600
    try:
        children = list(parent.iterdir())
    except OSError:
        return
    for child in children:
        if not child.name.startswith('.tmp-'):
            continue
        try:
            if child.stat().st_mtime < cutoff:
                _remove_dir(child)
        except OSError:
            continue


def _cleanup_user(user: str) -> None:
    _cleanup_temp(user)
    cutoff = _cutoff(_ttl_days())
    try:
        jobs = storage.video_job_expired(user, cutoff)
    except storage.StorageUnavailable:
        return
    for job in jobs:
        try:
            if storage.video_job_delete_finished(user, job['id']):
                _remove_dir(_job_dir(user, job['id']))
        except storage.StorageUnavailable:
            return

    try:
        uploads = storage.video_upload_expired(user, cutoff)
    except storage.StorageUnavailable:
        return
    for upload in uploads:
        try:
            result = storage.video_upload_delete(user, upload['id'])
        except storage.StorageUnavailable:
            return
        if result.get('result') != 'ok':
            continue
        for job in result.get('jobs') or []:
            _remove_dir(_job_dir(user, job['id']))
        _remove_dir(_upload_dir(user, upload['id']))


# ---------------------------------------------------------------
# 对外 JSON
# ---------------------------------------------------------------
def _public_upload(upload: dict) -> dict:
    return {
        'id': int(upload['id']),
        'name': upload.get('original_name') or '未命名视频',
        'size': int(upload.get('size') or 0),
        'duration': round(float(upload.get('duration') or 0), 3),
        'width': int(upload.get('width') or 0),
        'height': int(upload.get('height') or 0),
        'fps': round(float(upload.get('fps') or 0), 3),
        'videoCodec': upload.get('video_codec') or '',
        'audioCodec': upload.get('audio_codec') or '',
        'hasAudio': bool(upload.get('has_audio')),
        'mime': upload.get('mime') or 'video/mp4',
        'createdAt': upload.get('created_at') or '',
        'lastUsedAt': upload.get('last_used_at') or '',
    }


def _public_job(job: dict, upload=None) -> dict:
    outputs = []
    for item in job.get('outputs') or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get('index') or 0)
        except (TypeError, ValueError):
            continue
        if index <= 0:
            continue
        outputs.append({
            'index': index,
            'name': str(item.get('name') or ''),
            'label': str(item.get('label') or ''),
            'size': int(item.get('size') or 0),
            'mime': str(item.get('mime') or 'application/octet-stream'),
            'url': '/video-tools/api/jobs/%s/files/%d' % (job['id'], index),
        })
    return {
        'id': job['id'],
        'uploadId': int(job.get('upload_id') or 0),
        'uploadName': (upload or {}).get('original_name') or '',
        'kind': job.get('kind') or '',
        'kindLabel': KINDS.get(job.get('kind') or '', job.get('kind') or ''),
        'params': job.get('params') if isinstance(job.get('params'), dict) else {},
        'status': job.get('status') or 'queued',
        'stage': job.get('stage') or '',
        'progress': int(job.get('progress') or 0),
        'note': job.get('note') or '',
        'error': job.get('error') or '',
        'outputs': outputs,
        'primaryIndex': int(job.get('primary_index') or 0),
        'outputSize': int(job.get('output_size') or 0),
        'createdAt': job.get('created_at') or '',
        'startedAt': job.get('started_at') or '',
        'finishedAt': job.get('finished_at') or '',
    }


def _public_jobs(user: str, rows: list) -> list:
    cache = {}
    out = []
    for row in rows:
        upload_id = row.get('upload_id')
        if upload_id not in cache:
            cache[upload_id] = storage.video_upload_get(user, upload_id)
        out.append(_public_job(row, cache[upload_id]))
    return out


# ---------------------------------------------------------------
# 页面与接口
# ---------------------------------------------------------------
@router.get('/video-tools', response_class=HTMLResponse)
def video_tools_page():
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="background:#0b1220;color:#e8edf7;font-family:system-ui">'
        '<h2>模板文件缺失</h2>'
        '<p>请确认 templates/video-tools.html 存在。</p>'
        '</body></html>'
    )


@router.get('/video-tools/api/status')
async def video_status(request: Request):
    user = _user(request)
    _ensure_worker()
    info = _ready_info()
    used = 0
    storage_error = ''
    try:
        await run_in_threadpool(_cleanup_user, user)
        used = await run_in_threadpool(storage.video_job_jobs_since, user, _today_start())
    except storage.StorageUnavailable as exc:
        storage_error = str(exc)
    return JSONResponse({
        'ok': True,
        **info,
        'maxMb': _max_bytes() // 1048576,
        'dailyLimit': _daily_limit(),
        'usedToday': used,
        'ttlDays': _ttl_days(),
        'maxActive': _max_active(),
        'maxUploads': _max_uploads(),
        'kinds': KIND_LIST,
        'storageError': storage_error,
    })


@router.post('/video-tools/api/uploads')
async def upload_video(request: Request, file: UploadFile = File(...)):
    user = _user(request)
    original = Path(file.filename or 'video').name
    extension = Path(original).suffix.lower()
    if extension not in UPLOAD_TYPES:
        await file.close()
        return JSONResponse({
            'ok': False,
            'message': '支持 ' + '、'.join(sorted(ext.lstrip('.').upper()
                                                for ext in UPLOAD_TYPES)) + ' 等视频格式。',
        }, status_code=400)

    cap = _max_bytes()
    try:
        declared = int(request.headers.get('content-length') or 0)
    except ValueError:
        declared = 0
    if declared and declared > cap + 8 * 1024 * 1024:
        await file.close()
        return JSONResponse({
            'ok': False, 'message': '视频超过 %d MB 上限。' % (cap // 1048576),
        }, status_code=413)

    try:
        await run_in_threadpool(_cleanup_user, user)
        count = await run_in_threadpool(storage.video_upload_count, user)
    except storage.StorageUnavailable as exc:
        await file.close()
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if count >= _max_uploads():
        await file.close()
        return JSONResponse({
            'ok': False,
            'message': '最多同时保留 %d 个素材，请先删除一个。' % _max_uploads(),
        }, status_code=409)

    head = await file.read(32)
    if not _looks_like_video(head):
        await file.close()
        return JSONResponse({'ok': False, 'message': '文件内容不像视频。'},
                            status_code=400)
    await file.seek(0)

    staging = _user_root(user) / 'uploads' / ('.tmp-' + uuid.uuid4().hex)
    stored_name = 'source' + extension
    target = staging / stored_name
    part = staging / (stored_name + _PART_SUFFIX)
    size = 0
    try:
        staging.mkdir(parents=True, exist_ok=True)
        with part.open('wb') as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > cap:
                    raise ValueError('视频超过 %d MB 上限。' % (cap // 1048576))
                await run_in_threadpool(output.write, chunk)
        part.replace(target)
    except ValueError as exc:
        _remove_dir(staging)
        await file.close()
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=413)
    except Exception as exc:
        _remove_dir(staging)
        await file.close()
        return JSONResponse({'ok': False, 'message': '保存视频失败：' + str(exc)[:160]},
                            status_code=500)
    finally:
        try:
            await file.close()
        except Exception:
            pass

    try:
        probe = await run_in_threadpool(_probe, target)
    except VideoError as exc:
        _remove_dir(staging)
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)

    if size <= 0:
        _remove_dir(staging)
        return JSONResponse({'ok': False, 'message': '上传的文件是空的。'},
                            status_code=400)

    try:
        row = await run_in_threadpool(
            storage.video_upload_add, user, original, stored_name, size, probe)
    except storage.StorageUnavailable as exc:
        _remove_dir(staging)
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)

    final_dir = _upload_dir(user, row['id'])
    try:
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(final_dir)
    except OSError as exc:
        _remove_dir(staging)
        try:
            storage.video_upload_delete(user, row['id'])
        except storage.StorageUnavailable:
            pass
        return JSONResponse({'ok': False, 'message': '保存视频目录失败：' + str(exc)[:160]},
                            status_code=500)

    return JSONResponse({'ok': True, 'upload': _public_upload(row)}, status_code=201)


@router.get('/video-tools/api/uploads')
async def list_uploads(request: Request):
    user = _user(request)
    _ensure_worker()
    try:
        await run_in_threadpool(_cleanup_user, user)
        rows = await run_in_threadpool(storage.video_upload_list, user, 10)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({
        'ok': True,
        'items': [_public_upload(row) for row in rows],
        'max': _max_uploads(),
        'maxMb': _max_bytes() // 1048576,
    })


@router.delete('/video-tools/api/uploads/{upload_id}')
async def delete_upload(upload_id: str, request: Request):
    user = _user(request)
    try:
        result = await run_in_threadpool(storage.video_upload_delete, user, upload_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if result.get('result') == 'missing':
        return JSONResponse({'ok': False, 'message': '素材不存在或已过期。'},
                            status_code=404)
    if result.get('result') == 'busy':
        return JSONResponse({
            'ok': False, 'message': '还有任务在排队或处理中，等它结束再删素材。',
        }, status_code=409)
    upload = result.get('upload') or {}
    for job in result.get('jobs') or []:
        _remove_dir(_job_dir(user, job['id']))
    _remove_dir(_upload_dir(user, upload['id']))
    return JSONResponse({'ok': True})


@router.post('/video-tools/api/jobs')
async def create_job(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    kind = str(body.get('kind') or '')
    if kind not in KINDS:
        return JSONResponse({'ok': False, 'message': '不支持的操作类型。'},
                            status_code=400)
    user = _user(request)
    try:
        upload_id = int(body.get('uploadId'))
    except (TypeError, ValueError):
        return JSONResponse({'ok': False, 'message': '缺少视频素材编号。'},
                            status_code=400)

    try:
        upload = await run_in_threadpool(storage.video_upload_get, user, upload_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not upload:
        return JSONResponse({'ok': False, 'message': '视频素材不存在或已过期。'},
                            status_code=404)

    try:
        params = _clean_params(kind, body.get('params'), upload)
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)

    info = _ready_info()
    if not info['ready']:
        return JSONResponse({'ok': False, 'message': info['missing']}, status_code=503)

    try:
        await run_in_threadpool(_cleanup_user, user)
        active = await run_in_threadpool(storage.video_job_active_count, user)
        if active >= _max_active():
            return JSONResponse({
                'ok': False,
                'message': '前面还有 %d 个任务在处理，等它结束再提交。' % active,
            }, status_code=409)
        used = await run_in_threadpool(storage.video_job_jobs_since, user, _today_start())
        if used >= _daily_limit():
            return JSONResponse({
                'ok': False,
                'message': '今天的视频任务额度用完了（%d / %d），明天再来。'
                           % (used, _daily_limit()),
            }, status_code=429)
        job = await run_in_threadpool(
            storage.video_job_add, user, upload_id, kind, params)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not job:
        return JSONResponse({'ok': False, 'message': '视频素材不存在或已删除。'},
                            status_code=404)

    _ensure_worker()
    return JSONResponse({'ok': True, 'job': _public_job(job, upload)},
                        status_code=202)


@router.get('/video-tools/api/jobs')
async def list_jobs(request: Request):
    user = _user(request)
    _ensure_worker()
    try:
        await run_in_threadpool(_cleanup_user, user)
        rows = await run_in_threadpool(storage.video_job_list, user, 20)
        items = await run_in_threadpool(_public_jobs, user, rows)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': items})


@router.get('/video-tools/api/jobs/{job_id}')
async def get_job(job_id: str, request: Request):
    user = _user(request)
    _ensure_worker()
    try:
        row = await run_in_threadpool(storage.video_job_get, user, job_id)
        upload = None
        if row:
            upload = await run_in_threadpool(
                storage.video_upload_get, user, row.get('upload_id'))
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return JSONResponse({'ok': False, 'message': '任务不存在或已过期。'},
                            status_code=404)
    return JSONResponse({'ok': True, 'job': _public_job(row, upload)})


@router.delete('/video-tools/api/jobs/{job_id}')
async def delete_job(job_id: str, request: Request):
    user = _user(request)
    if not _JOB_ID_RE.match(str(job_id or '')):
        return JSONResponse({'ok': False, 'message': '任务不存在或已过期。'},
                            status_code=404)
    try:
        row = await run_in_threadpool(storage.video_job_get, user, job_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return JSONResponse({'ok': False, 'message': '任务不存在或已过期。'},
                            status_code=404)
    if row.get('status') in ('queued', 'running'):
        return JSONResponse({'ok': False, 'message': '任务还在处理中，等它结束再删。'},
                            status_code=409)
    try:
        changed = await run_in_threadpool(
            storage.video_job_delete_finished, user, job_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not changed:
        return JSONResponse({'ok': False, 'message': '任务不存在或已过期。'},
                            status_code=404)
    _remove_dir(_job_dir(user, job_id))
    return JSONResponse({'ok': True})


@router.get('/video-tools/api/jobs/{job_id}/files/{index}')
def download_file(job_id: str, index: int, request: Request):
    user = _user(request)
    if not _JOB_ID_RE.match(str(job_id or '')):
        return JSONResponse({'ok': False, 'message': '文件不存在。'}, status_code=404)
    try:
        row = storage.video_job_get(user, job_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return JSONResponse({'ok': False, 'message': '文件不存在。'}, status_code=404)
    wanted = None
    for item in row.get('outputs') or []:
        if isinstance(item, dict) and int(item.get('index') or 0) == int(index):
            wanted = item
            break
    if not wanted:
        return JSONResponse({'ok': False, 'message': '文件不存在。'}, status_code=404)
    name = Path(str(wanted.get('name') or '')).name
    folder = _job_dir(user, job_id)
    target = (folder / name).resolve() if folder else None
    if not target or not target.is_file() or not target.is_relative_to(folder.resolve()):
        return JSONResponse({'ok': False, 'message': '文件已丢失。'}, status_code=404)
    return FileResponse(
        target,
        media_type=wanted.get('mime') or 'application/octet-stream',
        headers={
            'Content-Disposition': "inline; filename*=UTF-8''" + quote(name),
            'Cache-Control': 'private, max-age=3600',
        },
    )
