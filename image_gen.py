# -*- coding: utf-8 -*-
"""
AI 文生图（火山方舟 / 豆包 Seedream）
=====================================
页面在 /image-gen，接口都在 /image-gen/api/*。

和图片工坊的分工
----------------
图片工坊是「本地处理」：图片像素不上传，服务端只存参数。
文生图不一样：提示词必须发给火山方舟，生成结果也会落在服务器上。
所以它是独立页面，页面上把这两件事写清楚，不和图片工坊混在一起。

任务为什么异步
--------------
出一张图通常要十几秒，四张更久。浏览器直接等一个长请求，很容易：
  - 被反向代理或浏览器超时掐断；
  - 用户刷新页面后，不知道任务到底还在不在。
所以改成「先建任务，再轮询」：

  POST /image-gen/api/jobs          -> 落一条 queued，返回 job_id
  GET  /image-gen/api/jobs/{id}     -> 查 queued / running / succeeded / failed
  GET  /image-gen/api/jobs/{id}/files/{n} -> 鉴权后取图片

后台只有一个调度线程，按创建顺序一张一张生成。个人低频使用足够了，
也避免一次点四下就同时打四个计费请求。

启动恢复
--------
进程重启时，上一次停在 running 的任务无法确认服务商是否已经出图、是否已经扣费。
这里不会自动重试，而是标成失败并留下提示，让用户先核对账单再决定。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import shutil
import threading
import time
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import model_config
import storage

router = APIRouter()

TOOL = 'image-gen'
MAX_PROMPT = 2000
MAX_NEGATIVE = 300
MAX_COUNT = 4
DEFAULT_DAILY_LIMIT = 40
DEFAULT_TTL_DAYS = 7
MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024
PROVIDER_TIMEOUT = (10, 120)

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'image-gen.html'
_JOB_ID_RE = re.compile(r'^[0-9a-f]{32}$')

# 风格只做「追加一句描述」，不替用户改原提示词。
# 想加新风格时，前后端各加一项即可；后端会拒绝未知 key。
STYLES = {
    'none': ('不指定', ''),
    'photo': ('摄影', '真实摄影风格，自然光，细节清晰'),
    'illustration': ('插画', '插画风格，干净线条，柔和配色'),
    '3d': ('3D 渲染', '3D 渲染风格，柔和材质，电影级光影'),
    'watercolor': ('水彩', '水彩画风格，纸张纹理，留白自然'),
    'minimal': ('极简', '极简设计风格，大留白，构图干净'),
    'poster': ('海报', '海报设计风格，强对比，视觉中心明确'),
}

# 宽高比 + 清晰度 -> 方舟接受的像素尺寸。
# 只选 5.0 Pro / Lite / 4.5 都能接受的 2K 档；1K 给草稿用。
RATIOS = {
    '1:1': {'label': '1:1', '1K': '1024x1024', '2K': '2048x2048'},
    '4:3': {'label': '4:3', '1K': '1152x864', '2K': '2304x1728'},
    '3:4': {'label': '3:4', '1K': '864x1152', '2K': '1728x2304'},
    '16:9': {'label': '16:9', '1K': '1424x800', '2K': '2560x1440'},
    '9:16': {'label': '9:16', '1K': '800x1424', '2K': '1440x2560'},
}
RESOLUTIONS = ('1K', '2K')


class ImageGenError(RuntimeError):
    """能直接展示给用户的一句话错误。"""


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(high, max(low, value))


def _daily_limit() -> int:
    return _env_int('IMAGE_GEN_DAILY_LIMIT', DEFAULT_DAILY_LIMIT, 1, 200)


def _ttl_days() -> int:
    return _env_int('IMAGE_GEN_TTL_DAYS', DEFAULT_TTL_DAYS, 1, 90)


def _today_start() -> str:
    return time.strftime('%Y-%m-%d 00:00:00')


def _cutoff(days: int) -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S',
                         time.localtime(time.time() - days * 86400))


def _short(value, limit: int = 300) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _text(value, limit: int) -> str:
    text = str(value or '')
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text).strip()
    return text[:limit]


def _output_root() -> Path:
    raw = (os.environ.get('IMAGE_GEN_DIR') or '').strip()
    if raw:
        return Path(raw).expanduser().resolve()
    # 和数据库放在同一个数据卷里：Docker 里 TB_DB_PATH 指向挂载卷，
    # 图片自然也跟着落进卷，重建容器不会丢。
    return storage.db_path().resolve().parent / 'generated'


def _user_key(user: str) -> str:
    # 用户名直接当目录名有路径穿越风险，这里只存哈希。
    return hashlib.sha256(user.encode('utf-8')).hexdigest()[:16]


def _job_dir(user: str, job_id: str) -> Path:
    job_id = str(job_id or '')
    if not _JOB_ID_RE.match(job_id):
        raise ValueError('任务 ID 不正确')
    return _output_root() / _user_key(user) / job_id


def _compose_prompt(prompt: str, style: str, negative: str) -> str:
    parts = [prompt.strip()]
    style_desc = (STYLES.get(style) or ('', ''))[1]
    if style_desc:
        parts.append(style_desc)
    if negative.strip():
        parts.append('画面中避免出现：' + negative.strip())
    return '；'.join(part for part in parts if part)


def _image_ext(data: bytes) -> str:
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return 'png'


def _media_type(ext: str) -> str:
    return {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}.get(
        ext, 'application/octet-stream')


def _provider_message(resp) -> str:
    """只取服务商错误里最有用的那一句，不把整包响应丢给页面。"""
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    message = ''
    if isinstance(payload, dict):
        error = payload.get('error')
        if isinstance(error, dict):
            message = error.get('message') or error.get('code') or ''
        elif error:
            message = str(error)
        if not message:
            message = payload.get('message') or payload.get('msg') or ''
    if not message:
        message = getattr(resp, 'text', '') or ''
    return _short(message) or '服务商没有返回具体原因'


def _provider_failure(resp) -> ImageGenError:
    status = getattr(resp, 'status_code', 0)
    detail = _provider_message(resp)
    if status in (401, 403):
        return ImageGenError('火山方舟鉴权失败（HTTP %d）：检查 ARK_API_KEY 是否有效、模型是否已开通。%s'
                             % (status, detail))
    if status == 429:
        return ImageGenError('火山方舟限流或额度不足（HTTP 429）：%s' % detail)
    if status == 400:
        return ImageGenError('火山方舟拒绝了这次请求（HTTP 400）：%s' % detail)
    return ImageGenError('火山方舟返回异常（HTTP %d）：%s' % (status, detail))


def _download_image(url: str) -> bytes:
    if not re.match(r'^https?://', str(url or ''), re.I):
        raise ImageGenError('服务商返回的图片地址不正确')
    try:
        resp = requests.get(url, timeout=(10, 60), stream=True)
    except requests.Timeout as exc:
        raise ImageGenError('下载生成图超时，请稍后重试') from exc
    except requests.RequestException as exc:
        raise ImageGenError('下载生成图失败：%s' % _short(exc)) from exc
    try:
        if resp.status_code != 200:
            raise ImageGenError('下载生成图失败（HTTP %d）' % resp.status_code)
        buf = bytearray()
        for chunk in resp.iter_content(64 * 1024):
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > MAX_DOWNLOAD_BYTES:
                raise ImageGenError('生成图超过 %d MB，已放弃保存' % (MAX_DOWNLOAD_BYTES // 1024 // 1024))
        if not buf:
            raise ImageGenError('服务商返回了空图片')
        return bytes(buf)
    finally:
        resp.close()


def _ark_generate(prompt: str, size: str) -> bytes:
    """调用火山方舟 images/generations，返回图片二进制。

    返回格式统一用 url：方舟的 URL 只保留 24 小时，所以这里立刻下载，
    页面只拿本站的鉴权地址，不依赖对方的临时链接。
    """
    key = model_config.ark_image_key()
    if not key:
        raise ImageGenError(model_config.image_gen_missing_hint())

    endpoint = model_config.ark_image_base() + '/images/generations'
    body = {
        'model': model_config.ark_image_model(),
        'prompt': prompt,
        'size': size,
        'response_format': 'url',
        'output_format': 'png',
        'watermark': False,
    }
    headers = {
        'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json',
    }
    try:
        resp = requests.post(endpoint, json=body, headers=headers, timeout=PROVIDER_TIMEOUT)
    except requests.Timeout as exc:
        raise ImageGenError('生成超时，请稍后重试（可能已产生费用）') from exc
    except requests.RequestException as exc:
        raise ImageGenError('连不上火山方舟接口：%s' % _short(exc)) from exc

    if resp.status_code != 200:
        raise _provider_failure(resp)

    try:
        payload = resp.json()
    except ValueError as exc:
        raise ImageGenError('火山方舟返回的不是 JSON，请稍后重试') from exc
    if not isinstance(payload, dict):
        raise ImageGenError('火山方舟返回格式不正确')
    if payload.get('error'):
        raise ImageGenError('火山方舟返回错误：%s' % _provider_message(resp))

    items = payload.get('data')
    if not isinstance(items, list) or not items:
        raise ImageGenError('火山方舟没有返回图片')
    item = items[0] if isinstance(items[0], dict) else {}

    raw = item.get('b64_json')
    if raw:
        text = str(raw)
        if text.startswith('data:') and ',' in text:
            text = text.split(',', 1)[1]
        try:
            data = base64.b64decode(text, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise ImageGenError('图片解码失败') from exc
        if not data:
            raise ImageGenError('服务商返回了空图片')
        return data

    url = item.get('url')
    if url:
        return _download_image(str(url))
    raise ImageGenError('火山方舟没有返回图片地址')


def _save_image(user: str, job_id: str, name: str, data: bytes) -> None:
    directory = _job_dir(user, job_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)


def _remove_job_dir(user: str, job_id: str) -> None:
    try:
        shutil.rmtree(_job_dir(user, job_id), ignore_errors=True)
    except ValueError:
        pass


def _cleanup_user(user: str) -> None:
    """顺手删掉过期任务和图片，不用额外配定时任务。"""
    cutoff = _cutoff(_ttl_days())
    try:
        rows = storage.image_job_expired(user, cutoff)
    except storage.StorageUnavailable:
        return
    for row in rows:
        if row.get('status') in ('queued', 'running'):
            continue
        _remove_job_dir(user, row['id'])
        try:
            storage.image_job_delete(user, row['id'])
        except storage.StorageUnavailable:
            return


def _remember_history(job: dict, files: list, error: str) -> None:
    """写一条全站历史，/overview 能看到「生成过什么」；图片本体不塞进库。"""
    payload = {
        'jobId': job['id'],
        'prompt': job['prompt'],
        'negativePrompt': job.get('negative_prompt') or '',
        'style': job.get('style') or 'none',
        'ratio': job.get('ratio') or '1:1',
        'resolution': job.get('resolution') or '2K',
        'count': job.get('count') or 1,
        'model': job.get('model') or '',
        'files': files,
        'error': error,
    }
    try:
        storage.add_record(TOOL, job['user'], job['prompt'][:80], payload)
    except storage.StorageUnavailable:
        pass


def _process_job(job: dict) -> None:
    user = job['user']
    job_id = job['id']
    prompt = _compose_prompt(job['prompt'],
                            job.get('style') or 'none',
                            job.get('negative_prompt') or '')
    ratio = RATIOS.get(job.get('ratio') or '1:1') or RATIOS['1:1']
    size = ratio.get(job.get('resolution') or '2K') or ratio['2K']

    files: list[str] = []
    error = ''
    for index in range(int(job.get('count') or 1)):
        try:
            data = _ark_generate(prompt, size)
            ext = _image_ext(data)
            name = '%02d.%s' % (index + 1, ext)
            _save_image(user, job_id, name, data)
            files.append(name)
            storage.image_job_update(job_id, {'files': files, 'done': len(files)})
        except ImageGenError as exc:
            error = str(exc)
            break
        except (OSError, storage.StorageUnavailable) as exc:
            error = '保存生成结果失败：%s' % _short(exc)
            break
        except Exception as exc:                       # 兜底：别让后台线程静默死掉
            error = '生成失败：%s' % _short(exc)
            break

    status = 'succeeded' if files else 'failed'
    if files and len(files) < int(job.get('count') or 1) and not error:
        error = '只生成成功 %d/%d 张' % (len(files), int(job.get('count') or 1))
    try:
        storage.image_job_update(job_id, {
            'status': status,
            'done': len(files),
            'files': files,
            'error': error,
            'finished_at': storage.now_str(),
        })
    except storage.StorageUnavailable:
        pass
    if files:
        _remember_history(job, files, error)


# ---------------------------------------------------------------
# 后台调度：一个线程，按顺序处理 queued
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
            storage.image_job_recover_running(
                '服务重启，生成任务中断；可能已经产生费用，请核对火山方舟账单后再重新生成')
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
            job = storage.image_job_claim_next()
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
        except Exception as exc:                       # 兜底，保证线程继续处理下一条
            try:
                storage.image_job_update(job['id'], {
                    'status': 'failed',
                    'error': '生成失败：' + _short(exc),
                    'finished_at': storage.now_str(),
                })
            except Exception:
                pass


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, name='image-gen-worker',
                                  daemon=True)
        thread.start()
        _worker_started = True


# ---------------------------------------------------------------
# 接口
# ---------------------------------------------------------------
def _public_job(job: dict) -> dict:
    files = job.get('files') or []
    images = [{
        'index': index + 1,
        'url': '/image-gen/api/jobs/%s/files/%d' % (job['id'], index + 1),
        # 文件名里带真实扩展名，前端下载时用得上（URL 本身不带后缀）
        'ext': str(files[index]).rsplit('.', 1)[-1].lower() if '.' in str(files[index]) else 'png',
    } for index in range(len(files))]
    return {
        'id': job['id'],
        'prompt': job.get('prompt') or '',
        'negativePrompt': job.get('negative_prompt') or '',
        'style': job.get('style') or 'none',
        'ratio': job.get('ratio') or '1:1',
        'resolution': job.get('resolution') or '2K',
        'count': int(job.get('count') or 1),
        'done': int(job.get('done') or len(images)),
        'status': job.get('status') or 'queued',
        'model': job.get('model') or '',
        'error': job.get('error') or '',
        'images': images,
        'createdAt': job.get('created_at') or '',
        'startedAt': job.get('started_at') or '',
        'finishedAt': job.get('finished_at') or '',
    }


def _clean_job_body(body) -> dict:
    if not isinstance(body, dict):
        raise ValueError('请求格式不正确')

    prompt = _text(body.get('prompt'), MAX_PROMPT)
    if not prompt:
        raise ValueError('先写一句画面描述')
    negative = _text(body.get('negative'), MAX_NEGATIVE)

    style = str(body.get('style') or 'none').strip().lower()
    if style not in STYLES:
        raise ValueError('不支持的风格')
    ratio = str(body.get('ratio') or '1:1').strip()
    if ratio not in RATIOS:
        raise ValueError('不支持的比例')
    resolution = str(body.get('resolution') or '2K').strip().upper()
    if resolution not in RESOLUTIONS:
        raise ValueError('清晰度只支持 1K / 2K')

    try:
        count = int(body.get('count', 1))
    except (TypeError, ValueError):
        raise ValueError('数量必须是 1 到 %d' % MAX_COUNT)
    if count < 1 or count > MAX_COUNT:
        raise ValueError('数量必须是 1 到 %d' % MAX_COUNT)

    return {
        'prompt': prompt,
        'negative': negative,
        'style': style,
        'ratio': ratio,
        'resolution': resolution,
        'count': count,
    }


@router.get('/image-gen', response_class=HTMLResponse)
def image_gen_page():
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="background:#0a0f1e;color:#e8edf7;font-family:system-ui">'
        '<h2>模板文件缺失</h2>'
        '<p>请确认 templates/image-gen.html 存在。</p>'
        '</body></html>'
    )


@router.get('/image-gen/api/status')
async def image_gen_status(request: Request):
    user = _user(request)
    _ensure_worker()
    used = 0
    storage_error = ''
    try:
        used = await run_in_threadpool(storage.image_job_images_since, user, _today_start())
    except storage.StorageUnavailable as exc:
        storage_error = str(exc)
    return JSONResponse({
        'ok': True,
        'ready': model_config.image_gen_ready(),
        'model': model_config.ark_image_model(),
        'dailyLimit': _daily_limit(),
        'usedToday': used,
        'ttlDays': _ttl_days(),
        'storageError': storage_error,
        'styles': [{'key': key, 'label': value[0]} for key, value in STYLES.items()],
        'ratios': list(RATIOS.keys()),
        'resolutions': list(RESOLUTIONS),
    })


@router.get('/image-gen/api/jobs')
async def list_jobs(request: Request):
    user = _user(request)
    _ensure_worker()
    try:
        await run_in_threadpool(_cleanup_user, user)
        rows = await run_in_threadpool(storage.image_job_list, user, 20)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': [_public_job(row) for row in rows]})


@router.post('/image-gen/api/jobs')
async def create_job(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        data = _clean_job_body(body)
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)

    if not model_config.image_gen_ready():
        return JSONResponse({'ok': False, 'message': model_config.image_gen_missing_hint()},
                            status_code=503)

    user = _user(request)
    try:
        await run_in_threadpool(_cleanup_user, user)
        used = await run_in_threadpool(storage.image_job_images_since, user, _today_start())
        limit = _daily_limit()
        if used + data['count'] > limit:
            return JSONResponse({
                'ok': False,
                'message': '今天的生成额度不够了（已用 %d / %d 张），明天再来或调大 IMAGE_GEN_DAILY_LIMIT'
                           % (used, limit),
            }, status_code=429)
        job = await run_in_threadpool(
            storage.image_job_add,
            user, data['prompt'], data['negative'], data['style'],
            data['ratio'], data['resolution'], data['count'],
            model_config.ark_image_model(),
        )
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)

    _ensure_worker()
    return JSONResponse({'ok': True, 'job': _public_job(job)}, status_code=202)


@router.get('/image-gen/api/jobs/{job_id}')
async def get_job(job_id: str, request: Request):
    user = _user(request)
    _ensure_worker()
    try:
        row = await run_in_threadpool(storage.image_job_get, user, job_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return JSONResponse({'ok': False, 'message': '任务不存在或已过期'}, status_code=404)
    return JSONResponse({'ok': True, 'job': _public_job(row)})


@router.delete('/image-gen/api/jobs/{job_id}')
async def delete_job(job_id: str, request: Request):
    user = _user(request)

    def _delete():
        row = storage.image_job_get(user, job_id)
        if not row:
            return 'missing'
        if row.get('status') in ('queued', 'running'):
            return 'busy'
        # 先原子删库记录，再清图片目录。条件里再次排除 queued / running，
        # 防止在 get 和 delete 之间任务状态刚好变化。
        if not storage.image_job_delete_finished(user, job_id):
            return 'busy'
        _remove_job_dir(user, job_id)
        return 'ok'

    try:
        result = await run_in_threadpool(_delete)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if result == 'missing':
        return JSONResponse({'ok': False, 'message': '任务不存在或已删除'}, status_code=404)
    if result == 'busy':
        return JSONResponse({'ok': False, 'message': '任务还在生成，等它结束再删'},
                            status_code=409)
    return JSONResponse({'ok': True})


@router.get('/image-gen/api/jobs/{job_id}/files/{index}')
async def get_job_file(job_id: str, index: int, request: Request):
    user = _user(request)
    try:
        row = await run_in_threadpool(storage.image_job_get, user, job_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return JSONResponse({'ok': False, 'message': '图片不存在或已过期'}, status_code=404)
    files = row.get('files') or []
    if index < 1 or index > len(files):
        return JSONResponse({'ok': False, 'message': '图片不存在'}, status_code=404)
    try:
        path = _job_dir(user, job_id) / str(files[index - 1])
    except ValueError:
        return JSONResponse({'ok': False, 'message': '图片不存在'}, status_code=404)
    if not path.is_file():
        return JSONResponse({'ok': False, 'message': '图片已被清理'}, status_code=404)
    ext = path.suffix.lstrip('.').lower()
    return FileResponse(
        str(path),
        media_type=_media_type(ext),
        headers={'Cache-Control': 'private, max-age=3600'},
    )
