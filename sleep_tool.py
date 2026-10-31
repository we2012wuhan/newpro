# -*- coding: utf-8 -*-
"""睡前打烊：内置声音由浏览器合成，也支持数据卷永久音频和 SQLite 预设。"""
import hashlib
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import storage

router = APIRouter()

TOOL = 'sleep'
AUDIO_TOOL = 'sleep-audio'
MAX_PRESETS = 8
MAX_AUDIO_FILES = 20
MAX_AUDIO_BYTES = 100 * 1024 * 1024
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'sleep.html'

BASE_TYPES = {'white', 'pink', 'brown'}
AMBIENCE_TYPES = {'none', 'stream', 'rain', 'fan', 'waves', 'library'}
DURATIONS = {20, 30, 45, 60}
FADES = {0, 5, 10, 15}

AUDIO_TYPES = {
    '.mp3': 'audio/mpeg',
    '.m4a': 'audio/mp4',
    '.aac': 'audio/aac',
    '.ogg': 'audio/ogg',
    '.opus': 'audio/ogg',
    '.wav': 'audio/wav',
    '.flac': 'audio/flac',
    '.webm': 'audio/webm',
}


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


def _audio_root(user: str) -> Path:
    folder = hashlib.sha256(user.encode('utf-8')).hexdigest()[:20]
    return storage.db_path().resolve().parent / 'sleep-audio' / folder


def _audio_kind(extension: str, head: bytes):
    if extension == '.mp3' and (head.startswith(b'ID3') or
                               (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)):
        return True
    if extension in ('.m4a', '.aac') and (
        head[4:8] == b'ftyp' or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xF6 == 0xF0)
    ):
        return True
    if extension in ('.ogg', '.opus') and head.startswith(b'OggS'):
        return True
    if extension == '.wav' and head.startswith(b'RIFF') and head[8:12] == b'WAVE':
        return True
    if extension == '.flac' and head.startswith(b'fLaC'):
        return True
    if extension == '.webm' and head.startswith(b'\x1aE\xdf\xa3'):
        return True
    return False


def _public_audio(row: dict) -> dict:
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    return {
        'id': row['id'],
        'title': row.get('title') or payload.get('original_name') or '我的音频',
        'original_name': payload.get('original_name') or '',
        'size': payload.get('size') or 0,
        'mime': payload.get('mime') or 'audio/mpeg',
        'created_at': row.get('created_at') or '',
        'url': '/sleep/api/audio/' + str(row['id']),
    }


def _number(value, minimum, maximum, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _clean_preset(payload: dict):
    base_type = str(payload.get('base_type') or 'brown')
    ambience_type = str(payload.get('ambience_type') or 'stream')
    if base_type not in BASE_TYPES:
        base_type = 'brown'
    if ambience_type not in AMBIENCE_TYPES:
        ambience_type = 'stream'
    try:
        audio_id = int(payload.get('audio_id') or 0)
    except (TypeError, ValueError):
        audio_id = 0
    try:
        duration = int(payload.get('duration') or 30)
    except (TypeError, ValueError):
        duration = 30
    if duration not in DURATIONS:
        duration = 30
    try:
        fade = int(payload.get('fade') or 10)
    except (TypeError, ValueError):
        fade = 10
    if fade not in FADES:
        fade = 10

    title = str(payload.get('name') or '').strip()[:50]
    if not title:
        title = '睡前声音预设'
    return title, {
        'base_type': base_type,
        'base_level': round(_number(payload.get('base_level'), 0, 100, 0)),
        'ambience_type': ambience_type,
        'ambience_level': round(_number(payload.get('ambience_level'), 0, 100, 58)),
        'duration': duration,
        'fade': fade,
        'volume': round(_number(payload.get('volume'), 0, 100, 65)),
        'audio_id': max(0, audio_id),
    }


@router.get('/sleep', response_class=HTMLResponse)
def sleep_page():
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="background:#07111a;color:#e7f0f7;font-family:system-ui">'
        '<h2>模板文件缺失</h2><p>请确认 templates/sleep.html 存在。</p>'
        '</body></html>'
    )


@router.get('/sleep/api/presets')
def list_sleep_presets(request: Request):
    try:
        items = storage.list_records(TOOL, _user(request), limit=MAX_PRESETS)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': items, 'max': MAX_PRESETS})


def _save_preset(user: str, title: str, payload: dict):
    existing = storage.list_records(TOOL, user, limit=MAX_PRESETS)
    if len(existing) >= MAX_PRESETS:
        return None, '最多保存 %d 个声音预设，请先删除一个。' % MAX_PRESETS
    if payload.get('ambience_type') == 'library':
        audio = storage.get_record(user, payload.get('audio_id') or 0)
        if not audio or audio.get('tool') != AUDIO_TOOL:
            return None, '请选择一个已经上传的永久音频。'
    return storage.add_record(TOOL, user, title, payload), ''


@router.post('/sleep/api/presets')
async def save_sleep_preset(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    title, payload = _clean_preset(body)
    try:
        row, error = await run_in_threadpool(_save_preset, _user(request), title, payload)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if error:
        return JSONResponse({'ok': False, 'message': error}, status_code=409)
    return JSONResponse({'ok': True, 'item': row})


@router.delete('/sleep/api/presets/{record_id}')
async def delete_sleep_preset(record_id: int, request: Request):
    try:
        changed = await run_in_threadpool(
            storage.delete_record, _user(request), record_id
        )
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not changed:
        return JSONResponse({'ok': False, 'message': '记录不存在'}, status_code=404)
    return JSONResponse({'ok': True})


@router.get('/sleep/api/audio')
def list_sleep_audio(request: Request):
    try:
        rows = storage.list_records(AUDIO_TOOL, _user(request), limit=MAX_AUDIO_FILES)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({
        'ok': True,
        'items': [_public_audio(row) for row in rows],
        'max': MAX_AUDIO_FILES,
        'max_bytes': MAX_AUDIO_BYTES,
    })


@router.post('/sleep/api/audio')
async def upload_sleep_audio(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(''),
):
    user = _user(request)
    original = Path(file.filename or 'audio').name
    extension = Path(original).suffix.lower()
    if extension not in AUDIO_TYPES:
        return JSONResponse(
            {'ok': False, 'message': '支持 MP3、M4A、AAC、OGG、WAV、FLAC、WEBM。'},
            status_code=400,
        )
    try:
        existing = await run_in_threadpool(
            storage.list_records, AUDIO_TOOL, user, MAX_AUDIO_FILES
        )
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if len(existing) >= MAX_AUDIO_FILES:
        return JSONResponse(
            {'ok': False, 'message': '最多保存 %d 个音频，请先删除一个。' % MAX_AUDIO_FILES},
            status_code=409,
        )

    head = await file.read(16)
    if not _audio_kind(extension, head):
        return JSONResponse({'ok': False, 'message': '文件内容不是可识别的音频格式。'},
                            status_code=400)
    await file.seek(0)

    folder = _audio_root(user)
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = secrets.token_urlsafe(18) + extension
    target = folder / stored_name
    part = target.with_suffix(extension + '.part')
    size = 0
    try:
        with part.open('wb') as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_AUDIO_BYTES:
                    raise ValueError('音频超过 100MB')
                await run_in_threadpool(output.write, chunk)
        part.replace(target)
    except ValueError as exc:
        part.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=413)
    except Exception as exc:
        part.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        return JSONResponse({'ok': False, 'message': '保存音频失败：' + str(exc)[:160]},
                            status_code=500)

    clean_title = str(title or '').strip()[:80] or Path(original).stem[:80] or '我的音频'
    payload = {
        'original_name': original[:160],
        'stored_name': stored_name,
        'size': size,
        'mime': AUDIO_TYPES[extension],
    }
    try:
        row = await run_in_threadpool(
            storage.add_record, AUDIO_TOOL, user, clean_title, payload
        )
    except storage.StorageUnavailable as exc:
        target.unlink(missing_ok=True)
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'item': _public_audio(row)})


@router.get('/sleep/api/audio/{record_id}')
def play_sleep_audio(record_id: int, request: Request):
    user = _user(request)
    try:
        row = storage.get_record(user, record_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row or row.get('tool') != AUDIO_TOOL:
        return JSONResponse({'ok': False, 'message': '音频不存在'}, status_code=404)
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    target = (_audio_root(user) / str(payload.get('stored_name') or '')).resolve()
    if not target.is_file() or not target.is_relative_to(_audio_root(user)):
        return JSONResponse({'ok': False, 'message': '音频文件已丢失'}, status_code=404)
    filename = quote(str(payload.get('original_name') or target.name))
    return FileResponse(
        target,
        media_type=payload.get('mime') or 'application/octet-stream',
        headers={
            'Content-Disposition': "inline; filename*=UTF-8''" + filename,
            'Cache-Control': 'private, no-cache',
        },
    )


@router.delete('/sleep/api/audio/{record_id}')
async def delete_sleep_audio(record_id: int, request: Request):
    user = _user(request)
    try:
        row = await run_in_threadpool(storage.get_record, user, record_id)
        if not row or row.get('tool') != AUDIO_TOOL:
            return JSONResponse({'ok': False, 'message': '音频不存在'}, status_code=404)
        presets = await run_in_threadpool(storage.list_records, TOOL, user, 500)
        if any(isinstance(item.get('payload'), dict) and
               item['payload'].get('audio_id') == record_id for item in presets):
            return JSONResponse(
                {'ok': False, 'message': '有声音预设正在使用这个音频，请先删除相关预设。'},
                status_code=409,
            )
        changed = await run_in_threadpool(storage.delete_record, user, record_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not changed:
        return JSONResponse({'ok': False, 'message': '音频不存在'}, status_code=404)
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    target = (_audio_root(user) / str(payload.get('stored_name') or '')).resolve()
    if target.is_file() and target.is_relative_to(_audio_root(user)):
        target.unlink(missing_ok=True)
    return JSONResponse({'ok': True})
