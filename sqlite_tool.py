# -*- coding: utf-8 -*-
# SQLite 测试台（页面在 /sqlite）
# ------------------------------------------------------------
# 目的：证明「数据真的写进了 SQLite 文件，再原样读回来」，把可复用的
#       storage.py 演示一遍 —— 其他工具照着调就能存历史记录。
#
# 页面上的每个按钮都对应下面一个接口：
#   GET    /sqlite/api/status        库状态（路径 / 大小 / 表 / 行数）
#   GET    /sqlite/api/notes         读我的记录（支持关键词）
#   POST   /sqlite/api/notes         写一条
#   POST   /sqlite/api/notes/bulk    连写 N 条（压一下写入）
#   DELETE /sqlite/api/notes/{id}    删一条
#   DELETE /sqlite/api/notes         清空我的
#   GET    /sqlite/api/history       读通用历史表
#   POST   /sqlite/api/history       用通用接口写一条
#   DELETE /sqlite/api/history/{id}  删一条
#   GET    /sqlite/api/download      下载 .db 文件本身
#
# 接口全部要求登录（外层 LoginGate 把关），记录按登录名隔离，互相看不见。
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import storage

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE = _BASE_DIR / 'templates' / 'sqlite.html'

TITLE_MAX = 120        # 标题最长多少字
BODY_MAX = 4000        # 内容最长多少字
BULK_MAX = 50          # 一次最多连写几条
DEMO_TOOL = 'sqlite-test'

_FALLBACK = (
    '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
    '<body style="background:#0a0f1e;color:#e8edf7;font-family:system-ui">'
    '<h2>SQLite 测试台</h2><p>页面模板缺失：templates/sqlite.html</p></body></html>')


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({'ok': False, 'error': message}, status_code=status)


@router.get('/sqlite', response_class=HTMLResponse)
def sqlite_page() -> HTMLResponse:
    html = _TEMPLATE.read_text(encoding='utf-8') if _TEMPLATE.exists() else _FALLBACK
    return HTMLResponse(html)


@router.get('/sqlite/api/status')
def sqlite_status() -> JSONResponse:
    return JSONResponse({'ok': True, 'db': storage.info()})


@router.get('/sqlite/api/notes')
def sqlite_list_notes(request: Request, q: str = '', limit: int = 100) -> JSONResponse:
    try:
        rows = storage.list_notes(_user(request), q=q, limit=limit)
    except storage.StorageUnavailable as exc:
        return _err(str(exc), 500)
    return JSONResponse({'ok': True, 'notes': rows})


@router.post('/sqlite/api/notes')
async def sqlite_add_note(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    title = str(payload.get('title') or '').strip()[:TITLE_MAX]
    content = str(payload.get('content') or '').strip()[:BODY_MAX]
    if not title:
        return _err('标题不能为空')
    try:
        note = storage.add_note(_user(request), title, content)
    except storage.StorageUnavailable as exc:
        return _err(str(exc), 500)
    except ValueError as exc:
        return _err(str(exc))
    return JSONResponse({'ok': True, 'note': note})


@router.post('/sqlite/api/notes/bulk')
async def sqlite_bulk(request: Request) -> JSONResponse:
    """连写 N 条，返回耗时 —— 顺便看一眼写入速度。"""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        count = int(payload.get('count') or 20)
    except (TypeError, ValueError):
        count = 20
    count = max(1, min(count, BULK_MAX))
    user = _user(request)
    started = time.perf_counter()
    try:
        for i in range(1, count + 1):
            storage.add_note(user, '批量写入 #%d' % i, '第 %d 条，用来测写入速度。' % i)
    except storage.StorageUnavailable as exc:
        return _err(str(exc), 500)
    cost = (time.perf_counter() - started) * 1000
    return JSONResponse({'ok': True, 'count': count, 'ms': round(cost, 1)})


@router.delete('/sqlite/api/notes/{note_id}')
def sqlite_delete_note(request: Request, note_id: int) -> JSONResponse:
    try:
        n = storage.delete_note(_user(request), note_id)
    except storage.StorageUnavailable as exc:
        return _err(str(exc), 500)
    return JSONResponse({'ok': True, 'deleted': n})


@router.delete('/sqlite/api/notes')
def sqlite_clear_notes(request: Request) -> JSONResponse:
    try:
        n = storage.clear_notes(_user(request))
    except storage.StorageUnavailable as exc:
        return _err(str(exc), 500)
    return JSONResponse({'ok': True, 'deleted': n})


@router.get('/sqlite/api/history')
def sqlite_history(request: Request, limit: int = 20) -> JSONResponse:
    try:
        rows = storage.list_records(DEMO_TOOL, user=_user(request), limit=limit)
    except storage.StorageUnavailable as exc:
        return _err(str(exc), 500)
    return JSONResponse({'ok': True, 'records': rows})


@router.post('/sqlite/api/history')
async def sqlite_add_history(request: Request) -> JSONResponse:
    """演示通用接口：其他工具存历史记录就是这一行。"""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    title = str(payload.get('title') or '').strip()[:TITLE_MAX] or '一条测试历史'
    try:
        rec = storage.add_record(DEMO_TOOL, _user(request), title,
                                 {'from': 'sqlite-test-page', 'note': payload.get('note') or ''})
    except storage.StorageUnavailable as exc:
        return _err(str(exc), 500)
    return JSONResponse({'ok': True, 'record': rec})


@router.delete('/sqlite/api/history/{rid}')
def sqlite_delete_history(request: Request, rid: int) -> JSONResponse:
    try:
        n = storage.delete_record(_user(request), rid)
    except storage.StorageUnavailable as exc:
        return _err(str(exc), 500)
    return JSONResponse({'ok': True, 'deleted': n})


@router.get('/sqlite/api/download')
def sqlite_download() -> FileResponse:
    """把 .db 文件本身给你 —— 眼见为实，历史记录就在这个文件里。"""
    path = storage.db_path()
    if not path.exists():
        return _err('数据库文件还不存在，先写一条记录。', 404)
    try:
        storage.checkpoint()          # 先把 WAL 并回主文件，保证下载到的是完整的
    except storage.StorageUnavailable:
        pass
    return FileResponse(str(path), media_type='application/octet-stream',
                        filename='app.db')


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _alone = FastAPI(title='SQLite 测试台', version='1.0.0')
    _alone.include_router(router)
    uvicorn.run(_alone, host='127.0.0.1', port=8013)