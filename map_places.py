# -*- coding: utf-8 -*-
# 个人地图：常用地点（/trip#places）
# ------------------------------------------------------------
#   GET    /trip/api/places               列表（q / category）
#   POST   /trip/api/places               新增
#   PATCH  /trip/api/places/{id}          修改
#   DELETE /trip/api/places/{id}          删除
#   POST   /trip/api/places/geocode       地址解析（显式）
#   GET    /trip/api/places/search        POI 搜索（显式，配额最紧）
#   GET    /trip/api/map/status           地图 / 配额状态（不泄露 Key）
#
# 坐标统一按 GCJ-02 存。前端如果传浏览器 GPS 坐标，需要先转成 GCJ-02；
# 目前页面只把「搜索选中的高德坐标」或已转换的当前位置传进来。
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import map_common
import storage

router = APIRouter()

CATEGORIES = {
    'home': '家',
    'work': '公司',
    'food': '餐饮',
    'scenic': '景点',
    'other': '其他',
}
_MAX_TAGS = 8


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


def _num(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _category(value) -> str:
    key = _clean(value, 20).lower()
    return key if key in CATEGORIES else 'other'


def _tags(value) -> list:
    if isinstance(value, str):
        value = value.replace('，', ',').split(',')
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        tag = _clean(item, 20)
        if tag and tag not in out:
            out.append(tag)
        if len(out) >= _MAX_TAGS:
            break
    return out


def _fail(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({'ok': False, 'message': message}, status_code=status)


def _place_out(row: dict) -> dict:
    """只返回页面需要的字段，数据库内部字段不全部外抛。"""
    return {
        'id': row.get('id'),
        'name': row.get('name') or '',
        'address': row.get('address') or '',
        'lng': row.get('lng'),
        'lat': row.get('lat'),
        'city': row.get('city') or '',
        'district': row.get('district') or '',
        'adcode': row.get('adcode') or '',
        'category': row.get('category') or 'other',
        'category_name': CATEGORIES.get(row.get('category') or 'other', '其他'),
        'tags': row.get('tags') or [],
        'note': row.get('note') or '',
        'source': row.get('source') or '',
        'created_at': row.get('created_at') or '',
        'updated_at': row.get('updated_at') or '',
    }


def _resolve_point(body: dict):
    """从请求里取坐标；两个都有且合法才返回，否则返回 (None, None)。"""
    lng = _num(body.get('lng'))
    lat = _num(body.get('lat'))
    if map_common.valid_lnglat(lng, lat):
        return lng, lat
    return None, None


@router.get('/trip/api/places')
def places_list(request: Request) -> JSONResponse:
    user = _user(request)
    q = _clean(request.query_params.get('q'), 60)
    category = _category(request.query_params.get('category')) if request.query_params.get('category') else ''
    try:
        rows = storage.map_place_list(user, q=q, category=category, limit=300)
    except storage.StorageUnavailable as exc:
        return _fail(str(exc), 500)
    return JSONResponse({
        'ok': True,
        'items': [_place_out(row) for row in rows],
        'categories': [{'key': key, 'name': name} for key, name in CATEGORIES.items()],
    })


@router.post('/trip/api/places')
async def places_add(request: Request) -> JSONResponse:
    user = _user(request)
    body = await _body(request)
    name = _clean(body.get('name'), 80)
    address = _clean(body.get('address'), 120)
    note = _clean(body.get('note'), 500)
    tags = _tags(body.get('tags'))
    category = _category(body.get('category'))
    lng, lat = _resolve_point(body)

    if body.get('lng') is not None or body.get('lat') is not None:
        if lng is None or lat is None:
            return _fail('坐标不完整或超出范围。')

    city = _clean(body.get('city'), 40)
    district = _clean(body.get('district'), 40)
    adcode = _clean(body.get('adcode'), 20)
    source = 'manual'
    if lng is not None:
        source = 'location'
        if not name and not address:
            return _fail('给这个地点起个名字，或者填一个地址。')
        # 有坐标但没地址时，尽量补一个地址；补不到不影响保存。
        if not address:
            geo, _err = await run_in_threadpool(map_common.regeo, lng, lat)
            if geo:
                address = geo.get('address') or ''
                city = geo.get('city') or ''
                district = geo.get('district') or ''
                adcode = geo.get('adcode') or ''
        if not name:
            name = address[:80]
    else:
        if not address:
            return _fail('填一个地址，或者从搜索结果里选一个地点。')
        geo, err = await run_in_threadpool(map_common.geocode, address)
        if not geo:
            return _fail(err or '地址没解析出来。')
        lng, lat = geo.get('lng'), geo.get('lat')
        address = geo.get('address') or address
        city = geo.get('city') or ''
        district = geo.get('district') or ''
        adcode = geo.get('adcode') or ''
        source = 'geocode'
        if not name:
            name = geo.get('name') or address[:80]

    if not name:
        return _fail('地点名称不能为空。')
    try:
        row = storage.map_place_add(
            user, name, lng, lat, address=address, city=city, district=district,
            adcode=adcode, category=category, tags=tags, note=note, source=source)
    except (ValueError, storage.StorageUnavailable) as exc:
        return _fail(str(exc), 500 if isinstance(exc, storage.StorageUnavailable) else 400)
    return JSONResponse({'ok': True, 'place': _place_out(row)})


@router.patch('/trip/api/places/{place_id}')
async def places_update(place_id: int, request: Request) -> JSONResponse:
    user = _user(request)
    try:
        current = storage.map_place_get(user, place_id)
    except storage.StorageUnavailable as exc:
        return _fail(str(exc), 500)
    if not current:
        return _fail('找不到这个地点。', 404)

    body = await _body(request)
    fields = {}
    if 'name' in body:
        name = _clean(body.get('name'), 80)
        if not name:
            return _fail('地点名称不能为空。')
        fields['name'] = name
    if 'note' in body:
        fields['note'] = _clean(body.get('note'), 500)
    if 'tags' in body:
        fields['tags'] = _tags(body.get('tags'))
    if 'category' in body:
        fields['category'] = _category(body.get('category'))

    has_lng = body.get('lng') is not None or body.get('lat') is not None
    if has_lng:
        lng, lat = _resolve_point(body)
        if lng is None or lat is None:
            return _fail('坐标不完整或超出范围。')
        fields['lng'] = lng
        fields['lat'] = lat
        if 'address' in body:
            fields['address'] = _clean(body.get('address'), 120)
        if not fields.get('address'):
            geo, _err = await run_in_threadpool(map_common.regeo, lng, lat)
            if geo:
                fields['address'] = geo.get('address') or ''
                fields['city'] = geo.get('city') or ''
                fields['district'] = geo.get('district') or ''
                fields['adcode'] = geo.get('adcode') or ''
    elif 'address' in body:
        address = _clean(body.get('address'), 120)
        if not address:
            return _fail('地址不能为空。')
        if address != (current.get('address') or ''):
            geo, err = await run_in_threadpool(map_common.geocode, address)
            if not geo:
                return _fail(err or '地址没解析出来。')
            fields['address'] = geo.get('address') or address
            fields['lng'] = geo.get('lng')
            fields['lat'] = geo.get('lat')
            fields['city'] = geo.get('city') or ''
            fields['district'] = geo.get('district') or ''
            fields['adcode'] = geo.get('adcode') or ''

    if not fields:
        return JSONResponse({'ok': True, 'place': _place_out(current)})
    try:
        row = storage.map_place_update(user, place_id, fields)
    except storage.StorageUnavailable as exc:
        return _fail(str(exc), 500)
    if not row:
        return _fail('找不到这个地点。', 404)
    return JSONResponse({'ok': True, 'place': _place_out(row)})


@router.delete('/trip/api/places/{place_id}')
def places_delete(place_id: int, request: Request) -> JSONResponse:
    user = _user(request)
    try:
        affected = storage.map_place_delete(user, place_id)
    except storage.StorageUnavailable as exc:
        return _fail(str(exc), 500)
    if not affected:
        return _fail('找不到这个地点。', 404)
    return JSONResponse({'ok': True})


@router.post('/trip/api/places/geocode')
async def places_geocode(request: Request) -> JSONResponse:
    body = await _body(request)
    address = _clean(body.get('address'), 120)
    if not address:
        return _fail('地址不能为空。')
    geo, err = await run_in_threadpool(map_common.geocode, address)
    if not geo:
        return _fail(err or '地址没解析出来。')
    return JSONResponse({'ok': True, 'place': geo})


@router.get('/trip/api/places/search')
async def places_search(request: Request) -> JSONResponse:
    """显式 POI 搜索。不要做成输入即搜，个人 Key 的 POI 配额很紧。"""
    q = _clean(request.query_params.get('q'), 100)
    city = _clean(request.query_params.get('city'), 40)
    if not q:
        return _fail('搜索词不能为空。')

    items, err = await run_in_threadpool(map_common.poi_search, q, city, 10)
    source = 'poi'
    if not items:
        geo, geo_err = await run_in_threadpool(map_common.geocode, q)
        if geo:
            items = [geo]
            source = 'geocode'
        elif err or geo_err:
            return _fail(err or geo_err)
    return JSONResponse({
        'ok': True,
        'items': items,
        'source': source,
    })


@router.get('/trip/api/map/status')
def map_status(request: Request) -> JSONResponse:
    web = bool(os.getenv('AMAP_WEB_KEY', '').strip())
    js_key = os.getenv('AMAP_JS_KEY', '').strip()
    security_code = os.getenv('AMAP_SECURITY_CODE', '').strip()
    js = bool(js_key)
    security = bool(security_code)
    user = _user(request)
    try:
        count = len(storage.map_place_list(user, limit=500))
    except storage.StorageUnavailable:
        count = 0
    quota = map_common.quota_status()
    return JSONResponse({
        'ok': True,
        'amap_ready': web,
        # 地图展示只依赖浏览器端 JS Key + 安全密钥；Web Key 只影响搜索。
        'map_ready': bool(js and security),
        # JS Key / 安全密钥本来就会出现在浏览器里，这里下发是设计如此；
        # 服务端 AMAP_WEB_KEY 永远不出现在响应中。
        'js_key': js_key,
        'security_code': security_code,
        'place_count': count,
        'quota': quota,
        'hint': (
            '高德已就绪，可以搜索和添加地点。' if web
            else '没有配置 AMAP_WEB_KEY，暂时不能搜索地址；可以先用地图坐标添加地点。'
        ),
    })
