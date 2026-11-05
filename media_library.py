# -*- coding: utf-8 -*-
"""
影音库（AList / OpenList 只读代理）
==================================
把阿里云盘里的音乐和视频放到工具箱里播放，但**不碰云盘本身**：

    浏览器 /media
      -> LoginGate 登录校验
      -> 本模块向内部 AList/OpenList 要目录和临时直链
      -> /media/stream 带 Range 转发，视频才能拖动进度条
      -> 收藏 / 断点续播只写本地 SQLite（media_state）

为什么用 AList/OpenList 而不是直接调阿里云盘 Open API：
阿里云盘的 refresh_token 获取和续期对个人用户不稳定，AList/OpenList 已经
把这些脏活封装好了；我们只把它当成一个内网文件网关，不开放公网端口。

配置（.env）：
    ALIST_URL=http://alist:5244     Docker 里用服务名；本地调试可用 127.0.0.1:5244
    ALIST_USERNAME=admin
    ALIST_PASSWORD=...              建议单独建一个只读 AList 账号
    ALIST_ROOT=/                    只暴露这个虚拟目录及其子目录

硬边界：
    1. 只发 /api/auth/login、/api/fs/list、/api/fs/get 和流式 GET，不调用任何写入接口；
    2. 路径必须落在 ALIST_ROOT 下，`..` 等片段直接丢弃；
    3. 流式响应 64 KB 一片，不把整个视频读进内存；
    4. AList 密码只在服务端，页面和流地址都不暴露它。
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

import storage

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'media.html'

TOOL = 'media'
LIST_MAX = 500

# 能出现在影音库里的扩展名（全部小写）
AUDIO_EXT = {
    '.mp3', '.m4a', '.aac', '.flac', '.wav', '.ogg', '.oga', '.opus', '.wma', '.ape', '.alac',
}
VIDEO_EXT = {
    '.mp4', '.m4v', '.webm', '.ogv', '.mov', '.mkv', '.avi', '.wmv', '.flv', '.ts',
    '.mts', '.m2ts', '.mpg', '.mpeg', '.rm', '.rmvb', '.3gp',
}
# 浏览器通常能直接解码的格式；其余的仍然列出来，但提示「浏览器可能不支持」
AUDIO_PLAYABLE = {'.mp3', '.m4a', '.aac', '.flac', '.wav', '.ogg', '.oga', '.opus', '.webm'}
VIDEO_PLAYABLE = {'.mp4', '.m4v', '.webm', '.ogv', '.mov'}

MIME_FALLBACK = {
    '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.aac': 'audio/aac',
    '.flac': 'audio/flac', '.wav': 'audio/wav', '.ogg': 'audio/ogg',
    '.oga': 'audio/ogg', '.opus': 'audio/ogg', '.wma': 'audio/x-ms-wma',
    '.ape': 'audio/ape', '.alac': 'audio/mp4',
    '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.webm': 'video/webm',
    '.ogv': 'video/ogg', '.mov': 'video/quicktime', '.mkv': 'video/x-matroska',
    '.avi': 'video/x-msvideo', '.wmv': 'video/x-ms-wmv', '.flv': 'video/x-flv',
    '.ts': 'video/mp2t', '.mts': 'video/mp2t', '.m2ts': 'video/mp2t',
    '.mpg': 'video/mpeg', '.mpeg': 'video/mpeg', '.3gp': 'video/3gpp',
}

_TOKEN_LOCK = threading.Lock()
_TOKEN = {'key': None, 'value': ''}
_LIST_CACHE = {}
_LIST_LOCK = threading.Lock()
_LIST_TTL = 12          # AList 自己也有缓存，这里只挡页面反复请求
_LIST_CACHE_MAX = 80


class MediaError(RuntimeError):
    """可以直接显示给用户的一句话错误。"""


def _env(name: str, default: str = '') -> str:
    return (os.environ.get(name) or '').strip() or default


def _alist_url() -> str:
    raw = _env('ALIST_URL', 'http://127.0.0.1:5244').rstrip('/')
    if not raw.startswith(('http://', 'https://')):
        raw = 'http://' + raw
    return raw


def _root() -> str:
    return _norm_path(_env('ALIST_ROOT', '/'))


def _norm_path(raw) -> str:
    """把路径规整成 AList 的绝对虚拟路径。

    只保留正常片段，`..` 和 `.` 直接丢掉 —— 与其尝试解析，不如让越界路径
    根本构造不出来。文件名里真带点号（如 a..b.mp3）不受影响。
    """
    text = str(raw or '/').replace('\\', '/')
    parts = [part for part in text.split('/') if part and part not in ('.', '..')]
    return '/' + '/'.join(parts)


def _safe_path(raw) -> str:
    path = _norm_path(raw)
    root = _root()
    if root != '/' and path != root and not path.startswith(root + '/'):
        raise MediaError('这个路径不在影音库范围内')
    return path


def _join(parent: str, name: str) -> str:
    parent = _norm_path(parent)
    name = str(name or '').replace('/', '').strip()
    if not name:
        return parent
    return parent + '/' + name if parent != '/' else '/' + name


def _ext(name: str) -> str:
    return Path(str(name or '')).suffix.lower()


def _kind_of(name: str) -> str:
    ext = _ext(name)
    if ext in AUDIO_EXT:
        return 'audio'
    if ext in VIDEO_EXT:
        return 'video'
    return ''


def _playable(name: str) -> bool:
    ext = _ext(name)
    return ext in AUDIO_PLAYABLE or ext in VIDEO_PLAYABLE


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


async def _body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _clean(value, limit: int) -> str:
    return ' '.join(str(value or '').split())[:limit]


# ---------------------------------------------------------------
# AList 客户端：登录一次，缓存 token；401 时强制重登一次
# ---------------------------------------------------------------
def _credentials():
    return (_alist_url(), _env('ALIST_USERNAME', 'admin'), _env('ALIST_PASSWORD'))


def _clear_token():
    with _TOKEN_LOCK:
        _TOKEN['key'] = None
        _TOKEN['value'] = ''


def _token(force: bool = False) -> str:
    base, username, password = _credentials()
    if not password:
        raise MediaError('还没有配置 ALIST_PASSWORD，影音库无法登录 AList。')
    key = (base, username, password)
    with _TOKEN_LOCK:
        if not force and _TOKEN['key'] == key and _TOKEN['value']:
            return _TOKEN['value']
        try:
            resp = requests.post(
                base + '/api/auth/login',
                json={'username': username, 'password': password},
                timeout=10,
            )
        except requests.RequestException:
            raise MediaError('连不上 AList，请检查 ALIST_URL 和容器状态。') from None
        try:
            data = resp.json()
        except ValueError:
            raise MediaError('AList 登录接口返回了非 JSON 内容。') from None
        if not isinstance(data, dict):
            raise MediaError('AList 登录接口返回格式不对。')
        if resp.status_code >= 400 or data.get('code') != 200:
            msg = str(data.get('message') or data.get('msg') or '账号或密码不正确')[:120]
            raise MediaError('AList 登录失败：%s' % msg)
        payload = data.get('data') if isinstance(data.get('data'), dict) else {}
        token = str(payload.get('token') or '')
        if not token:
            raise MediaError('AList 登录成功，但没有返回 token。')
        _TOKEN['key'] = key
        _TOKEN['value'] = token
        return token


def _request(path: str, payload: dict | None = None, timeout: int = 20) -> dict:
    """调用 AList 的 POST JSON 接口，返回 data 字段。"""
    base = _alist_url()
    last_msg = ''
    for attempt in range(2):
        try:
            token = _token(force=attempt > 0)
        except MediaError:
            raise
        headers = {'Authorization': token, 'Content-Type': 'application/json'}
        try:
            resp = requests.post(base + path, headers=headers, json=payload or {}, timeout=timeout)
        except requests.RequestException:
            raise MediaError('连不上 AList，请检查 ALIST_URL 和容器状态。') from None
        try:
            body = resp.json()
        except ValueError:
            raise MediaError('AList 返回了非 JSON 内容，请检查 ALIST_URL 是否指向 AList。') from None
        if not isinstance(body, dict):
            raise MediaError('AList 返回格式不对。')
        code = body.get('code')
        if (resp.status_code in (401, 403) or code == 401) and attempt == 0:
            _clear_token()
            continue
        if resp.status_code >= 400 or code not in (200, None):
            last_msg = str(body.get('message') or body.get('msg') or '')[:160]
            raise MediaError(last_msg or 'AList 请求失败（HTTP %s）' % resp.status_code)
        data = body.get('data')
        return data if isinstance(data, dict) else {}
    raise MediaError(last_msg or 'AList 登录已失效，请重试。')


def _list_dir(path: str, refresh: bool = False) -> list:
    """取目录内容（AList 原始条目），带 12 秒缓存。"""
    key = (_alist_url(), path)
    now = time.time()
    if not refresh:
        with _LIST_LOCK:
            hit = _LIST_CACHE.get(key)
        if hit and now - hit[0] < _LIST_TTL:
            return hit[1]
    data = _request('/api/fs/list', {
        'path': path, 'password': '', 'page': 1, 'per_page': 0, 'refresh': bool(refresh),
    })
    content = data.get('content')
    if content is None:
        content = []
    if not isinstance(content, list):
        raise MediaError('AList 返回的目录格式不对。')
    with _LIST_LOCK:
        if len(_LIST_CACHE) >= _LIST_CACHE_MAX:
            oldest = min(_LIST_CACHE.items(), key=lambda pair: pair[1][0])[0]
            _LIST_CACHE.pop(oldest, None)
        _LIST_CACHE[key] = (now, content)
    return content


def _clear_cache():
    with _LIST_LOCK:
        _LIST_CACHE.clear()


def _file_info(path: str) -> dict:
    data = _request('/api/fs/get', {'path': path, 'password': ''})
    if not data:
        raise MediaError('AList 没有返回这个文件的信息，可能已被移动或删除。')
    if data.get('is_dir'):
        raise MediaError('这是一个文件夹，不能直接播放。')
    return data


def _resolve_stream_url(raw: str) -> tuple[str, bool]:
    """把 AList 的 raw_url 修成容器里能访问的地址。

    返回 (url, 是否 AList 自身地址)。AList 在 Docker 里可能把 raw_url 写成
    http://127.0.0.1:5244/p/...，对 app 容器来说那是它自己，必须换回服务名。
    """
    raw = str(raw or '').strip()
    if not raw:
        raise MediaError('AList 没有返回播放地址，请检查存储是否开启 Web 代理。')
    base = _alist_url()
    parsed = urlparse(raw)
    if not parsed.scheme:
        if raw.startswith('/'):
            return base + raw, True
        return base + '/' + raw.lstrip('/'), True
    if parsed.path.startswith(('/p/', '/d/')):
        query = ('?' + parsed.query) if parsed.query else ''
        return base + parsed.path + query, True
    same_host = parsed.netloc == urlparse(base).netloc
    return raw, same_host


# ---------------------------------------------------------------
# 页面与接口
# ---------------------------------------------------------------
@router.get('/media', response_class=HTMLResponse)
def media_page(request: Request):
    try:
        storage.log_usage(_user(request), TOOL, 'open')
    except Exception:
        pass
    try:
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    except OSError:
        return HTMLResponse(
            '<!doctype html><meta charset="utf-8"><title>影音库</title>'
            '<p>缺少 templates/media.html 模板文件。</p>', status_code=500)
    return HTMLResponse(html)


@router.get('/media/api/status')
def media_status():
    configured = bool(_env('ALIST_PASSWORD'))
    root = _root()
    out = {
        'ok': configured, 'configured': configured, 'connected': False,
        'root': root, 'error': '',
    }
    if not configured:
        out['error'] = '还没有配置 ALIST_PASSWORD。'
        return JSONResponse(out)
    try:
        _request('/api/fs/list', {
            'path': root, 'password': '', 'page': 1, 'per_page': 1, 'refresh': False,
        }, timeout=12)
        out['connected'] = True
        out['ok'] = True
    except MediaError as exc:
        out['error'] = str(exc)
    except Exception:
        out['error'] = '影音库暂时连不上 AList。'
    return JSONResponse(out)


@router.get('/media/api/list')
def media_list(request: Request, path: str = Query('/'), kind: str = Query('all'),
               refresh: int = Query(0)):
    kind = kind if kind in ('music', 'video', 'all') else 'all'
    try:
        target = _safe_path(path)
        raw_items = _list_dir(target, refresh=bool(refresh))
        states = {row['path']: row for row in storage.media_state_rows(_user(request))}
        items = []
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get('name') or '')
            if not name:
                continue
            is_dir = bool(entry.get('is_dir'))
            entry_kind = 'dir' if is_dir else _kind_of(name)
            if not is_dir and not entry_kind:
                continue
            if kind == 'music' and entry_kind == 'video':
                continue
            if kind == 'video' and entry_kind == 'audio':
                continue
            item_path = _join(target, name)
            state = states.get(item_path) or {}
            items.append({
                'name': name,
                'path': item_path,
                'is_dir': is_dir,
                'kind': entry_kind,
                'size': int(entry.get('size') or 0),
                'modified': str(entry.get('modified') or ''),
                'created': str(entry.get('created') or ''),
                'playable': is_dir or _playable(name),
                'ext': _ext(name),
                'favorite': bool(state.get('favorite')),
                'position': float(state.get('position') or 0),
                'duration': float(state.get('duration') or 0),
            })
        items.sort(key=lambda row: (not row['is_dir'], row['name'].lower()))
        truncated = len(items) > LIST_MAX
        items = items[:LIST_MAX]
        return JSONResponse({
            'ok': True, 'path': target, 'kind': kind,
            'items': items, 'crumbs': _crumbs(target), 'root': _root(),
            'truncated': truncated,
        })
    except MediaError as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)
    except storage.StorageUnavailable:
        return JSONResponse({'ok': False, 'error': '本地数据库暂时不可用。'}, status_code=503)
    except Exception:
        return JSONResponse({'ok': False, 'error': '读取影音目录失败。'}, status_code=502)


def _crumbs(path: str) -> list:
    root = _root()
    if root == '/':
        rel = path.strip('/')
        label = '影音库'
    else:
        rel = path[len(root):].strip('/') if path != root else ''
        label = root.rstrip('/').rsplit('/', 1)[-1] or '影音库'
    out = [{'name': label, 'path': root}]
    if rel:
        acc = root.rstrip('/')
        for part in rel.split('/'):
            acc += '/' + part
            out.append({'name': part, 'path': acc})
    return out


@router.get('/media/api/favorites')
def media_favorites(request: Request):
    try:
        rows = [row for row in storage.media_state_rows(_user(request)) if row.get('favorite')]
        return JSONResponse({'ok': True, 'items': [_state_item(row) for row in rows]})
    except storage.StorageUnavailable:
        return JSONResponse({'ok': False, 'error': '本地数据库暂时不可用。'}, status_code=503)


@router.get('/media/api/recent')
def media_recent(request: Request):
    try:
        rows = [
            row for row in storage.media_state_rows(_user(request))
            if not row.get('favorite') and float(row.get('position') or 0) > 1
        ]
        rows.sort(key=lambda row: row.get('updated_at') or '', reverse=True)
        return JSONResponse({'ok': True, 'items': [_state_item(row) for row in rows[:40]]})
    except storage.StorageUnavailable:
        return JSONResponse({'ok': False, 'error': '本地数据库暂时不可用。'}, status_code=503)


def _state_item(row: dict) -> dict:
    path = str(row.get('path') or '')
    name = path.rstrip('/').rsplit('/', 1)[-1]
    entry_kind = row.get('kind') if row.get('kind') in ('audio', 'video') else _kind_of(name)
    return {
        'name': name,
        'title': row.get('title') or name,
        'path': path,
        'is_dir': False,
        'kind': entry_kind,
        'size': 0,
        'modified': row.get('updated_at') or '',
        'created': '',
        'playable': _playable(name),
        'ext': _ext(name),
        'favorite': bool(row.get('favorite')),
        'position': float(row.get('position') or 0),
        'duration': float(row.get('duration') or 0),
    }


@router.post('/media/api/favorite')
async def media_favorite(request: Request):
    body = await _body(request)
    try:
        path = _safe_path(body.get('path'))
        if not _kind_of(path):
            raise MediaError('只有音乐或视频文件可以收藏。')
        favorite = bool(body.get('favorite'))
        title = _clean(body.get('title'), 240) or path.rstrip('/').rsplit('/', 1)[-1]
        kind = _kind_of(path)
        row = storage.media_state_set(
            _user(request), path, title=title, kind=kind, favorite=favorite)
        return JSONResponse({'ok': True, 'item': _state_item(row)})
    except MediaError as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)
    except storage.StorageUnavailable:
        return JSONResponse({'ok': False, 'error': '本地数据库暂时不可用。'}, status_code=503)
    except Exception:
        return JSONResponse({'ok': False, 'error': '收藏没有保存成功。'}, status_code=500)


@router.post('/media/api/progress')
async def media_progress(request: Request):
    body = await _body(request)
    try:
        path = _safe_path(body.get('path'))
        if not _kind_of(path):
            raise MediaError('只有音乐或视频文件能记录进度。')
        try:
            position = max(0.0, float(body.get('position') or 0))
            duration = max(0.0, float(body.get('duration') or 0))
        except (TypeError, ValueError):
            raise MediaError('播放进度格式不对。')
        if duration > 24 * 3600 or position > 24 * 3600 + 60:
            raise MediaError('播放进度超出范围。')
        if duration and position > duration + 5:
            position = duration
        if duration and position > max(0.0, duration - 3):
            position = 0.0   # 已经播完，下次从头开始
        title = _clean(body.get('title'), 240) or path.rstrip('/').rsplit('/', 1)[-1]
        row = storage.media_state_set(
            _user(request), path, title=title, kind=_kind_of(path),
            position=round(position, 2), duration=round(duration, 2))
        return JSONResponse({'ok': True, 'item': _state_item(row)})
    except MediaError as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)
    except storage.StorageUnavailable:
        return JSONResponse({'ok': False, 'error': '本地数据库暂时不可用。'}, status_code=503)
    except Exception:
        return JSONResponse({'ok': False, 'error': '播放进度没有保存成功。'}, status_code=500)


@router.get('/media/stream')
def media_stream(request: Request, path: str = Query('')):
    try:
        target = _safe_path(path)
        if not _kind_of(target):
            return Response('只支持音频或视频文件', status_code=404, media_type='text/plain; charset=utf-8')
        info = _file_info(target)
        stream_url, is_alist = _resolve_stream_url(info.get('raw_url'))
    except MediaError as exc:
        return Response(str(exc), status_code=502, media_type='text/plain; charset=utf-8')
    except Exception:
        return Response('读取播放地址失败', status_code=502, media_type='text/plain; charset=utf-8')

    headers = {'Accept-Encoding': 'identity'}
    range_header = request.headers.get('range')
    if range_header:
        headers['Range'] = range_header
    if_range = request.headers.get('if-range')
    if if_range:
        headers['If-Range'] = if_range
    if is_alist:
        try:
            headers['Authorization'] = _token()
        except MediaError as exc:
            return Response(str(exc), status_code=502, media_type='text/plain; charset=utf-8')

    try:
        upstream = requests.get(
            stream_url, headers=headers, stream=True, timeout=(10, 90), allow_redirects=True)
    except requests.RequestException:
        return Response('连接云盘失败', status_code=502, media_type='text/plain; charset=utf-8')

    if upstream.status_code not in (200, 206, 416):
        upstream.close()
        return Response('云盘没有返回可播放的数据', status_code=502,
                        media_type='text/plain; charset=utf-8')

    passthrough = ('Content-Length', 'Content-Range', 'Accept-Ranges', 'Last-Modified', 'ETag')
    out_headers = {}
    for key in passthrough:
        value = upstream.headers.get(key)
        if value:
            out_headers[key] = value
    ctype = upstream.headers.get('Content-Type') or MIME_FALLBACK.get(_ext(target))
    if ctype:
        out_headers['Content-Type'] = ctype
    out_headers['Cache-Control'] = 'private, max-age=60'
    out_headers['X-Content-Type-Options'] = 'nosniff'

    if upstream.status_code == 416:
        upstream.close()
        return Response(status_code=416, headers=out_headers)

    return StreamingResponse(
        upstream.iter_content(chunk_size=64 * 1024),
        status_code=upstream.status_code,
        headers=out_headers,
        background=BackgroundTask(upstream.close),
    )
