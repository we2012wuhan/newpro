# -*- coding: utf-8 -*-
# 个人地图的公共高德层（/trip 的地点、路线、足迹共用）
# ------------------------------------------------------------
# 这一层只做四件事：
#   1. 统一带上服务端 AMAP_WEB_KEY 请求高德，Key 绝不下发浏览器；
#   2. 地理编码 / 逆编码 / POI 搜索的结果写进 SQLite 缓存，少扣配额；
#   3. 按天统计每个服务的调用次数，个人免费额度有数可查；
#   4. 把高德的字段整理成页面能直接用的结构。
#
# 配额保护默认开启，可用 MAP_QUOTA_GUARD=0 临时关掉。
# 个人开发者最紧的是 POI 搜索，默认软上限 80 次/天，可在 .env 调。
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from typing import Optional

import requests

import storage

AMAP_BASE = 'https://restapi.amap.com'

SERVICE_LABELS = {
    'geocode': '地理编码',
    'regeo': '逆地理编码',
    'poi': 'POI 搜索',
    'route': '路线规划',
    'weather': '天气',
    'convert': '坐标转换',
    'other': '其他',
}

_DEFAULT_CAPS = {
    'geocode': 4000,
    'regeo': 4000,
    'poi': 80,          # 官方个人口径约 100 次/天，留一点余量
    'route': 4000,
    'weather': 1000,
    'convert': 4000,
    'other': 10000,
}
_CAP_ENV = {
    'geocode': 'MAP_GEOCODE_DAILY_CAP',
    'regeo': 'MAP_REGEO_DAILY_CAP',
    'poi': 'MAP_POI_DAILY_CAP',
    'route': 'MAP_ROUTE_DAILY_CAP',
    'weather': 'MAP_WEATHER_DAILY_CAP',
    'convert': 'MAP_CONVERT_DAILY_CAP',
    'other': 'MAP_OTHER_DAILY_CAP',
}


def web_key() -> str:
    return os.getenv('AMAP_WEB_KEY', '').strip()


def amap_ready() -> bool:
    return bool(web_key())


def _today() -> str:
    return time.strftime('%Y-%m-%d')


def _clean(value, limit: int) -> str:
    return ' '.join(str(value or '').split())[:limit]


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _num(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def valid_lnglat(lng, lat) -> bool:
    lng, lat = _num(lng), _num(lat)
    if lng is None or lat is None:
        return False
    return -180 <= lng <= 180 and -90 <= lat <= 90


def split_location(value):
    parts = str(value or '').split(',')
    if len(parts) != 2:
        return None, None
    lng, lat = _num(parts[0]), _num(parts[1])
    if not valid_lnglat(lng, lat):
        return None, None
    return lng, lat


def distance_text(meters) -> str:
    meters = max(0, float(meters or 0))
    if meters < 1000:
        return '%d 米' % round(meters)
    return '%.1f 公里' % (meters / 1000)


def quota_cap(service: str) -> int:
    env_name = _CAP_ENV.get(service, '')
    raw = os.getenv(env_name, '').strip() if env_name else ''
    try:
        return max(0, int(float(raw))) if raw else _DEFAULT_CAPS.get(service, 10000)
    except ValueError:
        return _DEFAULT_CAPS.get(service, 10000)


def quota_guard_enabled() -> bool:
    return os.getenv('MAP_QUOTA_GUARD', '1').strip() not in ('0', 'false', 'off', 'no')


def quota_status() -> dict:
    """给页面看的今日调用量；不包含任何 Key。"""
    try:
        used = storage.map_quota_today()
    except storage.StorageUnavailable:
        used = {'date': _today(), 'services': {}}
    services = {}
    for key, label in SERVICE_LABELS.items():
        services[key] = {
            'label': label,
            'used': int(used.get('services', {}).get(key, 0)),
            'cap': quota_cap(key),
        }
    return {'date': used.get('date') or _today(), 'services': services,
            'guard': quota_guard_enabled()}


def note_quota(service: str, amount: int = 1) -> int:
    """给现有 trip.py 也记一笔；存储不可用时不影响主流程。"""
    service = (service or 'other')[:20]
    try:
        return storage.map_quota_bump(_today(), service, amount)
    except storage.StorageUnavailable:
        return 0


def service_for_path(path: str) -> str:
    path = str(path or '')
    if '/geocode/regeo' in path:
        return 'regeo'
    if '/geocode/' in path:
        return 'geocode'
    if '/place/' in path:
        return 'poi'
    if '/direction/' in path:
        return 'route'
    if '/weather/' in path:
        return 'weather'
    if '/assistant/coordinate/convert' in path:
        return 'convert'
    return 'other'


def _cache_key(path: str, params: dict) -> str:
    raw = path + '|' + json.dumps(params, ensure_ascii=False, sort_keys=True)
    return 'amap:' + hashlib.sha1(raw.encode('utf-8')).hexdigest()


def _friendly_error(info: str) -> str:
    text = str(info or '').strip()
    upper = text.upper()
    if 'CUQPS' in upper or 'QPS' in upper or 'OVER_LIMIT' in upper or 'QUOTA' in upper:
        return '高德调用太频繁或今日额度已到，稍等一会儿再试。'
    return '高德：' + (_clean(text, 80) or '未知错误')


def amap(path: str, params: dict, service: str = 'other',
         ttl: int = 600, use_cache: bool = True):
    """请求高德 Web 服务，返回 (data, error)。

    - ttl > 0 时读写 SQLite 缓存；
    - 先检查当天软上限，避免个人 Key 被一个循环打爆；
    - 只有真正发出的请求才计数。
    """
    if not amap_ready():
        return None, '没有配置 AMAP_WEB_KEY'

    clean_params = dict(params or {})
    clean_params['key'] = web_key()
    cache_key = _cache_key(path, {k: v for k, v in clean_params.items() if k != 'key'})
    if use_cache and ttl > 0:
        try:
            cached = storage.map_cache_get(cache_key, ttl)
        except storage.StorageUnavailable:
            cached = None
        if cached is not None:
            return cached, ''

    cap = quota_cap(service)
    if quota_guard_enabled() and cap > 0:
        try:
            used = storage.map_quota_get(_today(), service)
        except storage.StorageUnavailable:
            used = 0
        if used >= cap:
            label = SERVICE_LABELS.get(service, service)
            return None, '今天的%s调用已经到自设上限（%d 次），明天再试或调高 MAP_%s_DAILY_CAP。' % (
                label, cap, service.upper())

    try:
        resp = requests.get(AMAP_BASE + path, params=clean_params, timeout=(6, 15))
    except requests.RequestException as exc:
        return None, '高德请求失败：' + str(exc)[:80]
    if resp.status_code >= 400:
        note_quota(service)
        return None, '高德返回 HTTP ' + str(resp.status_code)
    try:
        data = resp.json()
    except ValueError:
        note_quota(service)
        return None, '高德返回的不是 JSON'

    # 实际发出去并拿到了响应，就算一次调用；接口报错通常也占配额。
    note_quota(service)

    if 'status' in data:
        if str(data.get('status')) != '1':
            return None, _friendly_error(data.get('info') or '未知错误')
    elif 'errcode' in data:
        # 骑行等 v4 接口用 errcode / errmsg
        if _int(data.get('errcode'), -1) != 0:
            return None, _friendly_error(data.get('errmsg') or '未知错误')
    else:
        return None, '高德返回格式异常'

    if use_cache and ttl > 0:
        try:
            storage.map_cache_put(cache_key, data)
        except storage.StorageUnavailable:
            pass
    return data, ''


# ---------------------------------------------------------------
# 地理编码 / 逆地理编码
# ---------------------------------------------------------------
def _geo_from_poi(poi: dict, fallback_name: str = '') -> dict:
    lng, lat = split_location(poi.get('location'))
    name = _clean(poi.get('name') or fallback_name, 80)
    address = _clean(poi.get('address') or name, 120)
    return {
        'name': name,
        'address': address,
        'lng': lng,
        'lat': lat,
        'city': _clean(poi.get('cityname'), 40),
        'district': _clean(poi.get('adname'), 40),
        'adcode': _clean(poi.get('adcode'), 20),
        'citycode': _clean(poi.get('citycode'), 20),
        'source': 'poi',
    }


def geocode(address: str):
    """地址 → {name, address, lng, lat, ...}。地址本身是景点名时用 POI 兜底。"""
    address = _clean(address, 100)
    if not address:
        return None, '地址不能为空。'

    data, err = amap('/v3/geocode/geo', {'address': address, 'batch': 'false'},
                     service='geocode', ttl=30 * 86400)
    if data:
        geos = data.get('geocodes') or []
        if geos:
            geo = geos[0]
            lng, lat = split_location(geo.get('location'))
            if lng is not None and lat is not None:
                return {
                    'name': _clean(geo.get('formatted_address') or address, 120),
                    'address': _clean(geo.get('formatted_address') or address, 120),
                    'lng': lng,
                    'lat': lat,
                    'city': _clean(geo.get('city'), 40),
                    'district': _clean(geo.get('district'), 40),
                    'adcode': _clean(geo.get('adcode'), 20),
                    'citycode': _clean(geo.get('citycode'), 20),
                    'source': 'geocode',
                }, ''

    pois, _poi_err = poi_search(address, limit=3)
    if pois:
        return pois[0], ''

    return None, err or ('找不到「' + address + '」，换个更具体的写法试试。')


def convert_gps(lng: float, lat: float):
    """浏览器 GPS（WGS84）→ 高德 GCJ-02。

    浏览器 navigator.geolocation 给的是 WGS84，直接拿去高德会偏移几十到几百米，
    所以只要来源标成 gps，就先转换再逆编码 / 入库。
    """
    if not valid_lnglat(lng, lat):
        return None, '坐标不合法。'
    data, err = amap('/v3/assistant/coordinate/convert', {
        'locations': '%.6f,%.6f' % (float(lng), float(lat)),
        'coordsys': 'gps',
    }, service='convert', ttl=86400)
    if not data:
        return None, err
    converted = split_location(data.get('locations'))
    if converted[0] is None or converted[1] is None:
        return None, '高德坐标转换返回格式异常'
    return converted, ''


def regeo(lng: float, lat: float):
    """坐标 → 地址。坐标必须是 GCJ-02（高德坐标系）。"""
    if not valid_lnglat(lng, lat):
        return None, '坐标不合法。'
    data, err = amap('/v3/geocode/regeo', {
        'location': '%.6f,%.6f' % (float(lng), float(lat)),
        'extensions': 'base',
    }, service='regeo', ttl=30 * 86400)
    if not data:
        return None, err
    item = data.get('regeocode') or {}
    comp = item.get('addressComponent') or {}
    address = _clean(item.get('formatted_address') or '当前位置', 120)
    return {
        'name': address,
        'address': address,
        'lng': float(lng),
        'lat': float(lat),
        'city': _clean(comp.get('city') or comp.get('province'), 40),
        'district': _clean(comp.get('district'), 40),
        'adcode': _clean(comp.get('adcode'), 20),
        'citycode': _clean(comp.get('citycode'), 20),
        'source': 'regeo',
    }, ''


def poi_search(keyword: str, city: str = '', limit: int = 10):
    """POI 关键字搜索。个人 Key 最紧的一项，只在用户显式搜索时调用。"""
    keyword = _clean(keyword, 100)
    if not keyword:
        return [], '搜索词不能为空。'
    limit = max(1, min(int(limit or 10), 20))
    params = {
        'keywords': keyword,
        'offset': str(limit),
        'page': '1',
        'extensions': 'base',
        'citylimit': 'false',
    }
    city = _clean(city, 40)
    if city:
        params['city'] = city
    data, err = amap('/v3/place/text', params, service='poi', ttl=86400)
    if not data:
        return [], err

    out, seen = [], set()
    for poi in data.get('pois') or []:
        pid = _clean(poi.get('id'), 40)
        name = _clean(poi.get('name'), 80)
        lng, lat = split_location(poi.get('location'))
        if not name or lng is None or lat is None or (pid and pid in seen):
            continue
        if pid:
            seen.add(pid)
        out.append({
            'id': pid,
            'name': name,
            'address': _clean(poi.get('address') or name, 120),
            'lng': lng,
            'lat': lat,
            'city': _clean(poi.get('cityname'), 40),
            'district': _clean(poi.get('adname'), 40),
            'adcode': _clean(poi.get('adcode'), 20),
            'type': _clean(str(poi.get('type') or '').split(';')[-1], 30),
            'typecode': _clean(poi.get('typecode'), 20),
            'source': 'poi',
        })
        if len(out) >= limit:
            break
    return out, ''


def haversine(lng1, lat1, lng2, lat2) -> float:
    try:
        lng1, lat1, lng2, lat2 = float(lng1), float(lat1), float(lng2), float(lat2)
    except (TypeError, ValueError):
        return 0.0
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))
