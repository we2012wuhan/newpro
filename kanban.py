# -*- coding: utf-8 -*-
# 任务看板（页面在 /kanban）
# ------------------------------------------------------------
# 一张属于当前账号的轻量看板：待办 / 进行中 / 已完成三列。
# 每条任务有标题、备注、状态、优先级、任务日期、截止日期六个字段——
# 不做多项目、不做子任务、不做协作，目的就是一条任务能快速写进来、
# 一眼看到现在该做什么，做完拖到右边。
#
#   GET    /kanban                    页面
#   GET    /kanban/api/tasks          全部任务 + 状态 / 优先级键名
#   POST   /kanban/api/tasks          新建任务
#   PATCH  /kanban/api/tasks/{id}     修改任务（只认白名单字段）
#   DELETE /kanban/api/tasks/{id}     删除任务
#
# 数据存 SQLite（history 表，tool='kanban'），走 storage.py，按登录名隔离。
# 每条任务占一行；标题存在 history.title，其余字段在 payload。
# 一个账号最多 500 条，满了先删再建。
#
# 浏览器访问 http://127.0.0.1:8000/kanban 即可使用。
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

import storage

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'kanban.html'

TOOL = 'kanban'
LIST_MAX = 500          # 一个账号最多保留的任务数；到顶后拒绝新建
TITLE_MAX = 120
NOTE_MAX = 1000

STATUSES = (
    ('todo', '待办'),
    ('doing', '进行中'),
    ('done', '已完成'),
)
PRIORITIES = (
    ('high', '高'),
    ('normal', '中'),
    ('low', '低'),
)
STATUS_KEYS = tuple(k for k, _ in STATUSES)
PRIORITY_KEYS = tuple(k for k, _ in PRIORITIES)


def _user(request: Request) -> str:
    """LoginGate 中间件把登录用户名写在这里；没登录根本走不到路由。"""
    return getattr(request.state, 'user', '') or ''


async def _body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _title(value) -> str:
    """标题折成一行：换行和连续空白都并成一个空格。"""
    return ' '.join(str(value or '').split())


def _note(value) -> str:
    """备注保留换行，只去掉首尾空白；太长由调用处拒绝。"""
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
    return text.strip()


def _valid_date(text: str) -> bool:
    if not text:
        return True
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _shape(payload) -> dict:
    """把库里存的 payload 归一化成完整字段：老记录或脏数据也能安全显示。"""
    payload = payload if isinstance(payload, dict) else {}
    status = str(payload.get('status') or '').strip()
    priority = str(payload.get('priority') or '').strip()
    day = str(payload.get('day') or '').strip()
    due = str(payload.get('due') or '').strip()
    if day and not _valid_date(day):
        day = ''
    if due and not _valid_date(due):
        due = ''
    return {
        'note': _note(payload.get('note'))[:NOTE_MAX],
        'status': status if status in STATUS_KEYS else 'todo',
        'priority': priority if priority in PRIORITY_KEYS else 'normal',
        'day': day,
        'due': due,
        'updatedAt': str(payload.get('updatedAt') or '')[:19],
        'doneAt': str(payload.get('doneAt') or '')[:19],
    }


def _out(row: dict) -> dict:
    """一行 history 变成前端要的任务字典。"""
    item = _shape(row.get('payload'))
    item['id'] = row.get('id')
    item['title'] = str(row.get('title') or '')
    item['createdAt'] = str(row.get('created_at') or '')
    return item


def _meta() -> dict:
    """状态 / 优先级的键名和中文名都由后端给，前端不另存一份。"""
    return {
        'statuses': [{'k': k, 'n': n} for k, n in STATUSES],
        'priorities': [{'k': k, 'n': n} for k, n in PRIORITIES],
        'titleMax': TITLE_MAX,
        'noteMax': NOTE_MAX,
        'limit': LIST_MAX,
    }


def _error(message: str, code: int = 400) -> JSONResponse:
    return JSONResponse({'ok': False, 'message': message}, status_code=code)


# ---------------- 页面 ----------------
@router.get('/kanban', response_class=HTMLResponse)
def kanban_page() -> HTMLResponse:
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="font-family:system-ui"><h2>模板文件缺失</h2>'
                '<p>请确认 templates/kanban.html 存在。</p></body></html>')
    return HTMLResponse(html)


# ---------------- 任务：四件套 ----------------
@router.get('/kanban/api/tasks')
def kanban_list(request: Request) -> JSONResponse:
    try:
        rows = storage.list_records(TOOL, _user(request), LIST_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)}, status_code=503)
    data = _meta()
    data['ok'] = True
    data['items'] = [_out(row) for row in rows]
    data['count'] = len(data['items'])
    return JSONResponse(data)


@router.post('/kanban/api/tasks')
async def kanban_create(request: Request) -> JSONResponse:
    payload = await _body(request)
    title = _title(payload.get('title'))
    if not title:
        return _error('给任务起个名字。')
    if len(title) > TITLE_MAX:
        return _error('标题太长了，控制在 %d 个字以内。' % TITLE_MAX)
    note = _note(payload.get('note'))
    if len(note) > NOTE_MAX:
        return _error('备注太长了，控制在 %d 个字以内。' % NOTE_MAX)

    status = str(payload.get('status') or 'todo').strip()
    if status not in STATUS_KEYS:
        return _error('状态只能是待办、进行中或已完成。')
    priority = str(payload.get('priority') or 'normal').strip()
    if priority not in PRIORITY_KEYS:
        return _error('优先级只能是高、中或低。')
    day = str(payload.get('day') or '').strip() or date.today().isoformat()
    if not _valid_date(day):
        return _error('任务日期格式不对，用 YYYY-MM-DD。')
    due = str(payload.get('due') or '').strip()
    if not _valid_date(due):
        return _error('截止日期格式不对，用 YYYY-MM-DD。')

    user = _user(request)
    try:
        if len(storage.list_records(TOOL, user, LIST_MAX)) >= LIST_MAX:
            return _error('任务已经有 %d 条，先删掉一些再新建。' % LIST_MAX, 409)
        now = storage.now_str()
        data = {
            'note': note,
            'status': status,
            'priority': priority,
            'day': day,
            'due': due,
            'updatedAt': now,
            'doneAt': now if status == 'done' else '',
        }
        rec = storage.add_record(TOOL, user, title, data)
    except storage.StorageUnavailable as exc:
        return _error(str(exc), 500)

    item = dict(data)
    item['id'] = rec['id']
    item['title'] = title
    item['createdAt'] = rec['created_at']
    return JSONResponse({'ok': True, 'item': item})


@router.patch('/kanban/api/tasks/{rid}')
async def kanban_update(rid: int, request: Request) -> JSONResponse:
    """只认标题 / 备注 / 状态 / 优先级 / 任务日期 / 截止日期，别让前端整包覆盖。"""
    patch = (await _body(request)).get('patch')
    if not isinstance(patch, dict):
        return _error('没有要改的内容。')

    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return _error('找不到这条任务。', 404)

        data = _shape(row.get('payload'))
        title = str(row.get('title') or '')
        changed = False

        if 'title' in patch:
            value = _title(patch.get('title'))
            if not value:
                return _error('标题不能空着。')
            if len(value) > TITLE_MAX:
                return _error('标题太长了，控制在 %d 个字以内。' % TITLE_MAX)
            if value != title:
                title = value
                changed = True

        if 'note' in patch:
            value = _note(patch.get('note'))
            if len(value) > NOTE_MAX:
                return _error('备注太长了，控制在 %d 个字以内。' % NOTE_MAX)
            if value != data['note']:
                data['note'] = value
                changed = True

        if 'status' in patch:
            value = str(patch.get('status') or '').strip()
            if value not in STATUS_KEYS:
                return _error('状态只能是待办、进行中或已完成。')
            if value != data['status']:
                data['status'] = value
                data['doneAt'] = storage.now_str() if value == 'done' else ''
                changed = True

        if 'priority' in patch:
            value = str(patch.get('priority') or '').strip()
            if value not in PRIORITY_KEYS:
                return _error('优先级只能是高、中或低。')
            if value != data['priority']:
                data['priority'] = value
                changed = True

        if 'day' in patch:
            value = str(patch.get('day') or '').strip()
            if not _valid_date(value):
                return _error('任务日期格式不对，用 YYYY-MM-DD。')
            if value != data['day']:
                data['day'] = value
                changed = True

        if 'due' in patch:
            value = str(patch.get('due') or '').strip()
            if not _valid_date(value):
                return _error('截止日期格式不对，用 YYYY-MM-DD。')
            if value != data['due']:
                data['due'] = value
                changed = True

        if not changed:
            return JSONResponse({'ok': True, 'item': _out(row)})

        data['updatedAt'] = storage.now_str()
        affected = storage.update_record(user, rid, payload=data, title=title)
        if not affected:
            return _error('这条任务不属于当前账号。', 403)
    except storage.StorageUnavailable as exc:
        return _error(str(exc), 500)

    item = dict(data)
    item['id'] = rid
    item['title'] = title
    item['createdAt'] = str(row.get('created_at') or '')
    return JSONResponse({'ok': True, 'item': item})


@router.delete('/kanban/api/tasks/{rid}')
def kanban_delete(rid: int, request: Request) -> JSONResponse:
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return _error('找不到这条任务。', 404)
        affected = storage.delete_record(user, rid)
        if not affected:
            return _error('这条任务不属于当前账号。', 403)
    except storage.StorageUnavailable as exc:
        return _error(str(exc), 500)
    return JSONResponse({'ok': True})


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _app = FastAPI(title='任务看板', version='1.0.0')
    _app.include_router(router)
    uvicorn.run(_app, host='127.0.0.1', port=8032)
