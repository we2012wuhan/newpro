# -*- coding: utf-8 -*-
# 首页「工具分类」的接口
# ------------------------------------------------------------
# 为什么单独一个文件：分类以前存在浏览器 localStorage 里，换设备就没了。
# 现在改成落 SQLite（tool_cats / tool_cat_assign 两张表，见 storage.py），
# 增删改名、把卡片拖到别的分类，全部是这几个接口在做。
#
# 页面上的每个动作对应一个接口，接口一律返回「改动之后的完整状态」，
# 前端拿到就整块重画 —— 省得两边各算一份、慢慢对不上：
#   GET    /api/home/cats              当前状态：分类清单 + 卡片归属
#   POST   /api/home/cats              新建分类           {name}
#   POST   /api/home/cats/reset        恢复默认（清空这个用户在分类上的所有改动）
#   PATCH  /api/home/cats/{key}        改名               {name}
#   DELETE /api/home/cats/{key}        删除               {moves: {href: 分类key}}
#   PUT    /api/home/assign            批量改卡片归属      {moves: {href: 分类key}}
#
# moves 的值给空字符串 = 取消这条自定义归属，回落到首页 HTML 里写死的默认分类。
# 这些都是写操作，外层 LoginGate 已经挡住未登录，这里只管数据合不合法。
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import storage

router = APIRouter()

MAX_NAME = 10
MAX_KEY = 40
MAX_MOVES = 500          # 首页一共 21 张卡，给 500 已经非常宽松了


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


async def _body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _name(value) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:MAX_NAME]


def _key(value) -> str:
    return str(value or '').strip()[:MAX_KEY]


def _moves(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out = {}
    for href, cat in list(value.items())[:MAX_MOVES]:
        out[str(href)[:300]] = str(cat or '')[:MAX_KEY]
    return out


def _fail(exc: Exception, status: int = 503) -> JSONResponse:
    return JSONResponse({'ok': False, 'error': '没存进数据库：%s' % str(exc)[:160]},
                        status_code=status)


def _state(user: str) -> JSONResponse:
    """所有接口的统一出口：返回改动之后的完整状态。"""
    try:
        data = storage.cats_state(user)
    except storage.StorageUnavailable as exc:
        return _fail(exc)
    data['ok'] = True
    return JSONResponse(data)


@router.get('/api/home/cats')
def home_cats_state(request: Request) -> JSONResponse:
    return _state(_user(request))


@router.post('/api/home/cats')
async def home_cats_add(request: Request) -> JSONResponse:
    user = _user(request)
    name = _name((await _body(request)).get('name'))
    if not name:
        return JSONResponse({'ok': False, 'error': '分类名字不能是空的。'}, status_code=400)
    try:
        storage.cat_add(user, name)
    except storage.StorageUnavailable as exc:
        return _fail(exc)
    return _state(user)


@router.post('/api/home/cats/reset')
def home_cats_reset(request: Request) -> JSONResponse:
    user = _user(request)
    try:
        storage.cats_reset(user)
    except storage.StorageUnavailable as exc:
        return _fail(exc)
    return _state(user)


@router.patch('/api/home/cats/{ckey}')
async def home_cats_rename(ckey: str, request: Request) -> JSONResponse:
    user = _user(request)
    name = _name((await _body(request)).get('name'))
    if not name:
        return JSONResponse({'ok': False, 'error': '分类名字不能是空的。'}, status_code=400)
    try:
        storage.cat_rename(user, _key(ckey), name)
    except storage.StorageUnavailable as exc:
        return _fail(exc)
    return _state(user)


@router.delete('/api/home/cats/{ckey}')
async def home_cats_delete(ckey: str, request: Request) -> JSONResponse:
    user = _user(request)
    payload = await _body(request)
    try:
        storage.cat_delete(user, _key(ckey), _moves(payload.get('moves')))
    except ValueError as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)
    except storage.StorageUnavailable as exc:
        return _fail(exc)
    return _state(user)


@router.put('/api/home/assign')
async def home_assign(request: Request) -> JSONResponse:
    user = _user(request)
    moves = _moves((await _body(request)).get('moves'))
    if not moves:
        return JSONResponse({'ok': False, 'error': '没有要改的卡片。'}, status_code=400)
    try:
        storage.assign_apply(user, moves)
    except storage.StorageUnavailable as exc:
        return _fail(exc)
    return _state(user)