# -*- coding: utf-8 -*-
# 脚本库：页面在 /script-library
# ------------------------------------------------------------
# 解决的是「想不起来」这件事，不是「没有脚本」。
# 把散在本机各处的小脚本登记成一张张卡片：它干什么、怎么跑、依赖什么、放在哪。
# 三个月后翻回来，一眼就知道有这么个东西、也知道怎么把它跑起来。
#
# 数据存 SQLite（history 表，tool='script-library'），走 storage.py，按登录名隔离。
# 字段白名单在 FIELDS / CATS / STATUS，前端多传的键一律丢掉。
# 页面认 ?r=<记录 id>，「记录总览」里的「直接打开这一条」能跳过来。
#
# 浏览器访问 http://127.0.0.1:8000/script-library 即可使用。
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

import storage

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'script-library.html'

TOOL = 'script-library'
LIST_MAX = 200          # 个人脚本库，一次全读回来，前端自己筛，省一个接口

# 卡片字段：元组是 (键, 长度上限)。想加字段只改这里，前端表单跟着加一行输入框。
FIELDS = (
    ('name', 60),       # 脚本名，唯一必填项
    ('desc', 200),      # 干什么
    ('cmd', 220),       # 怎么跑（这条最关键，别的都能省，它不能）
    ('path', 160),      # 放在哪
    ('deps', 120),      # 依赖什么
    ('notes', 600),     # 备注 / 踩过的坑
)

CATS = (
    ('media', '媒体'),
    ('file', '文件'),
    ('data', '数据'),
    ('ops', '运维'),
    ('dev', '开发'),
    ('other', '其他'),
)

STATUS = (
    ('idea', '想做'),
    ('wip', '在做'),
    ('ready', '能用'),
    ('dead', '弃用'),
)

CAT_KEYS = tuple(k for k, _ in CATS)
STATUS_KEYS = tuple(k for k, _ in STATUS)


def _user(request: Request) -> str:
    """LoginGate 中间件把登录用户名写在这里；没登录根本走不到路由。"""
    return getattr(request.state, 'user', '') or ''


async def _body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _clean(value, limit: int) -> str:
    return ' '.join(str(value or '').split())[:limit]


def _pick(value, keys, default: str) -> str:
    """分类 / 状态只认白名单里的键，传了别的就当没传。"""
    key = str(value or '').strip()
    return key if key in keys else default


def _today() -> str:
    return date.today().isoformat()


def _count(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _shape(payload: dict) -> dict:
    """把库里存的 payload 归一化成完整的一张卡：老记录缺字段也能补齐。"""
    data = {}
    for key, limit in FIELDS:
        data[key] = _clean(payload.get(key), limit)
    data['cat'] = _pick(payload.get('cat'), CAT_KEYS, 'other')
    data['status'] = _pick(payload.get('status'), STATUS_KEYS, 'idea')
    data['used_n'] = _count(payload.get('used_n'))
    data['used_at'] = _clean(payload.get('used_at'), 10)
    return data


def _out(row: dict) -> dict:
    """一行 history 变成前端要的字典。"""
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    item = _shape(payload)
    item['id'] = row.get('id')
    item['created_at'] = str(row.get('created_at') or '')
    item['title'] = str(row.get('title') or item['name'])
    return item


def _meta() -> dict:
    """分类 / 状态的键名和中文名都由后端给，前端不另存一份。"""
    return {
        'cats': [{'k': k, 'n': n} for k, n in CATS],
        'status': [{'k': k, 'n': n} for k, n in STATUS],
    }
# ---------------- 页面 ----------------
@router.get('/script-library', response_class=HTMLResponse)
def script_library_page() -> HTMLResponse:
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="font-family:system-ui"><h2>模板文件缺失</h2>'
                '<p>请确认 templates/script-library.html 存在。</p></body></html>')
    return HTMLResponse(html)


# ---------------- 记录：五件套 ----------------
@router.get('/script-library/api/items')
def script_library_list(request: Request) -> JSONResponse:
    try:
        rows = storage.list_records(TOOL, _user(request), LIST_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)}, status_code=503)
    data = _meta()
    data['ok'] = True
    data['items'] = [_out(row) for row in rows]
    return JSONResponse(data)


@router.post('/script-library/api/items')
async def script_library_create(request: Request) -> JSONResponse:
    payload = await _body(request)
    data = _shape(payload)
    if len(data['name']) < 2:
        return JSONResponse({'ok': False, 'message': '先给它起个名字，至少两个字。'}, status_code=400)
    data['used_n'] = 0
    data['used_at'] = ''
    try:
        rec = storage.add_record(TOOL, _user(request), data['name'], data)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    data['id'] = rec['id']
    data['created_at'] = rec['created_at']
    data['title'] = data['name']
    return JSONResponse({'ok': True, 'item': data})


@router.get('/script-library/api/items/{rid}')
def script_library_one(rid: int, request: Request) -> JSONResponse:
    try:
        row = storage.get_record(_user(request), rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'message': '找不到这个脚本。'}, status_code=404)
    return JSONResponse({'ok': True, 'item': _out(row)})


@router.patch('/script-library/api/items/{rid}')
async def script_library_update(rid: int, request: Request) -> JSONResponse:
    """只认白名单字段；patch 里带 touch=True 表示「用过了」，次数 +1。"""
    patch = (await _body(request)).get('patch')
    if not isinstance(patch, dict):
        return JSONResponse({'ok': False, 'message': '没有要改的内容。'}, status_code=400)
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return JSONResponse({'ok': False, 'message': '找不到这个脚本。'}, status_code=404)
        payload = row['payload'] if isinstance(row['payload'], dict) else {}
        data = _shape(payload)
        changed = False

        for key, limit in FIELDS:
            if key in patch:
                value = _clean(patch.get(key), limit)
                if data[key] != value:
                    data[key] = value
                    changed = True

        if 'cat' in patch:
            value = _pick(patch.get('cat'), CAT_KEYS, data['cat'])
            if value != data['cat']:
                data['cat'] = value
                changed = True

        if 'status' in patch:
            value = _pick(patch.get('status'), STATUS_KEYS, data['status'])
            if value != data['status']:
                data['status'] = value
                changed = True

        if patch.get('touch'):
            data['used_n'] = data['used_n'] + 1
            data['used_at'] = _today()
            changed = True

        if not changed:
            return JSONResponse({'ok': True, 'item': _out(row)})
        if len(data['name']) < 2:
            return JSONResponse({'ok': False, 'message': '名字不能空着。'}, status_code=400)

        affected = storage.update_record(user, rid, payload=data, title=data['name'])
        if not affected:
            return JSONResponse({'ok': False, 'message': '这条不属于当前账号。'}, status_code=403)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)

    item = dict(data)
    item['id'] = rid
    item['created_at'] = str(row.get('created_at') or '')
    item['title'] = data['name']
    return JSONResponse({'ok': True, 'item': item})


@router.delete('/script-library/api/items/{rid}')
def script_library_delete(rid: int, request: Request) -> JSONResponse:
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return JSONResponse({'ok': False, 'message': '找不到这个脚本。'}, status_code=404)
        affected = storage.delete_record(user, rid)
        if not affected:
            return JSONResponse({'ok': False, 'message': '这条不属于当前账号。'}, status_code=403)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    return JSONResponse({'ok': True})