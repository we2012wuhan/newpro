# -*- coding: utf-8 -*-
# 出行攻略（页面在 /trip）
# ------------------------------------------------------------
# 选一个起点和目的地，由高德提供「事实」，模型只负责在事实里挑点、排序、写人话：
#   - 高德：地理编码、路线（驾车 / 步行 / 公交 / 骑行）、天气、周边景点和餐馆
#   - 模型：只从候选 POI 里挑 id，说明为什么去；不许编坐标、距离、耗时、票价、营业时间
#   - 服务端：校验 id、重算每个点的到达时间和停留时长，模型说错的时间一律不采信
#
# 没配高德 Key 时返回一份明确标注「演示」的行程，先把页面和交互跑通；
# 没配模型 Key 时仍用高德数据按评分自动排，不假装是 AI 的结论。
#
#   GET    /trip                      页面
#   GET    /trip/api/status           高德 / 模型是否就绪（不回传任何密钥）
#   POST   /trip/api/plan             生成行程（不自动保存）
#   GET    /trip/api/plans            历史列表
#   POST   /trip/api/plans            保存一条行程
#   GET    /trip/api/plans/{id}       取一条
#   DELETE /trip/api/plans/{id}       删除
#
# 数据存 SQLite（history 表，tool='trip'），按登录名隔离；页面认 ?r=<记录 id> 深链。
#
# 环境变量：
#   AMAP_WEB_KEY        高德「Web 服务」Key，只在服务端用
#   AMAP_JS_KEY         高德「Web 端（JS API）」Key，会下发到浏览器
#   AMAP_SECURITY_CODE  JS API 安全密钥，会下发到浏览器
from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import map_common
import storage

try:
    from model_config import llm_body, llm_endpoint, llm_key, llm_model
except Exception:  # 单独拿这个文件跑时的兜底
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'

    def llm_body(body, model=''):
        return body

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'trip.html'

TOOL = 'trip'
AMAP_BASE = 'https://restapi.amap.com'

MODES = {
    'driving': '驾车',
    'transit': '公交',
    'walking': '步行',
    'riding': '骑行',
}
DURATIONS = {'half': ('半天', 300), 'full': ('一天', 540)}
PREFS = ('亲子', '美食', '自然', '人文', '轻松', '紧凑')
PREFS_MAX = 3

SIGHT_TYPES = '110000|110100|110200|110300'
FOOD_TYPES = '050000'

# 每种交通方式在市区里的保守速度（米 / 分钟），只在腿与腿之间估算用。
_SPEED = {'driving': 420, 'transit': 260, 'walking': 78, 'riding': 240}
_BUFFER_MIN = {'driving': 8, 'transit': 14, 'walking': 2, 'riding': 3}
_MAX_STAY = 180
_MIN_STAY = 20

_CACHE: dict = {}
_CACHE_TTL = 600       # 地理编码 / POI 缓存 10 分钟，避免连续点两次就多扣一次配额
_WEATHER_TTL = 1800


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
    key = str(value or '').strip()
    return key if key in keys else default


def _num(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _cache_get(key: str, ttl: int = _CACHE_TTL):
    item = _CACHE.get(key)
    if not item:
        return None
    saved, value = item
    if time.time() - saved > ttl:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value) -> None:
    if len(_CACHE) > 240:
        _CACHE.clear()
    _CACHE[key] = (time.time(), value)


def _amap(path: str, params: dict):
    key = os.getenv('AMAP_WEB_KEY', '').strip()
    if not key:
        return None, '没有配置 AMAP_WEB_KEY'
    query = dict(params)
    query['key'] = key
    try:
        resp = requests.get(AMAP_BASE + path, params=query, timeout=(6, 15))
    except requests.RequestException as exc:
        return None, '高德请求失败：' + str(exc)[:80]
    # 实际发出去的请求记一笔，个人地图页能看到今日用量；存储不可用不影响主流程。
    map_common.note_quota(map_common.service_for_path(path))
    if resp.status_code >= 400:
        return None, '高德返回 HTTP ' + str(resp.status_code)
    try:
        data = resp.json()
    except ValueError:
        return None, '高德返回的不是 JSON'
    if 'status' in data:
        if str(data.get('status')) != '1':
            info = _clean(data.get('info') or '未知错误', 80)
            return None, '高德：' + info
    elif 'errcode' in data:
        # 骑行用的是 v4 接口：字段是 errcode / errmsg，没有 status
        if _int(data.get('errcode'), -1) != 0:
            return None, '高德：' + _clean(data.get('errmsg') or '未知错误', 80)
    else:
        return None, '高德返回格式异常'
    return data, ''


def _strip_html(text) -> str:
    text = re.sub(r'<[^>]+>', ' ', str(text or ''))
    text = text.replace('&nbsp;', ' ')
    return _clean(text, 180)


# ---------------------------------------------------------------
# 地理编码 / 逆地理编码
# ---------------------------------------------------------------
def _geo_from_poi(poi: dict) -> dict:
    location = _clean(poi.get('location'), 40)
    lng, lat = _split_location(location)
    return {
        'name': _clean(poi.get('name'), 80),
        'address': _clean(poi.get('address') or poi.get('name'), 120),
        'location': location,
        'lng': lng,
        'lat': lat,
        'city': _clean(poi.get('cityname'), 40),
        'district': _clean(poi.get('adname'), 40),
        'adcode': _clean(poi.get('adcode'), 20),
        'citycode': _clean(poi.get('citycode'), 20),
    }


def _split_location(location):
    parts = str(location or '').split(',')
    if len(parts) != 2:
        return None, None
    return _num(parts[0]), _num(parts[1])


def _geocode(address: str):
    address = _clean(address, 100)
    if not address:
        return None, '起点和目的地都要填。'
    cache_key = 'geo:' + address
    cached = _cache_get(cache_key)
    if cached:
        return cached, ''

    data, err = _amap('/v3/geocode/geo', {'address': address, 'batch': 'false'})
    if data:
        geos = data.get('geocodes') or []
        if geos:
            geo = geos[0]
            lng, lat = _split_location(geo.get('location'))
            if lng is not None and lat is not None:
                out = {
                    'name': _clean(geo.get('formatted_address') or address, 120),
                    'address': _clean(geo.get('formatted_address') or address, 120),
                    'location': _clean(geo.get('location'), 40),
                    'lng': lng,
                    'lat': lat,
                    'city': _clean(geo.get('city'), 40),
                    'district': _clean(geo.get('district'), 40),
                    'adcode': _clean(geo.get('adcode'), 20),
                    'citycode': _clean(geo.get('citycode'), 20),
                }
                _cache_set(cache_key, out)
                return out, ''

    # 地址里直接写景点名时，地理编码经常查不到；再用 POI 文本搜索兜底一次。
    data, err2 = _amap('/v3/place/text', {
        'keywords': address, 'offset': '5', 'page': '1', 'extensions': 'base',
    })
    if data:
        pois = data.get('pois') or []
        if pois:
            out = _geo_from_poi(pois[0])
            if out['lng'] is not None and out['lat'] is not None:
                out['address'] = out['address'] or address
                _cache_set(cache_key, out)
                return out, ''

    return None, err or err2 or ('找不到「' + address + '」，换个更具体的写法试试。')


def _regeo(lng: float, lat: float):
    cache_key = 'regeo:%.5f,%.5f' % (lng, lat)
    cached = _cache_get(cache_key)
    if cached:
        return cached, ''
    data, err = _amap('/v3/geocode/regeo', {
        'location': '%.6f,%.6f' % (lng, lat), 'extensions': 'base',
    })
    if not data:
        return None, err
    item = data.get('regeocode') or {}
    comp = item.get('addressComponent') or {}
    address = _clean(item.get('formatted_address') or '当前位置', 120)
    out = {
        'name': address,
        'address': address,
        'location': '%.6f,%.6f' % (lng, lat),
        'lng': lng,
        'lat': lat,
        'city': _clean(comp.get('city') or comp.get('province'), 40),
        'district': _clean(comp.get('district'), 40),
        'adcode': _clean(comp.get('adcode'), 20),
        'citycode': _clean(comp.get('citycode'), 20),
    }
    _cache_set(cache_key, out)
    return out, ''


# ---------------------------------------------------------------
# 路线
# ---------------------------------------------------------------
def _route(mode: str, origin: dict, dest: dict):
    cache_key = 'route:%s:%s>%s' % (mode, origin.get('location'), dest.get('location'))
    cached = _cache_get(cache_key, 300)
    if cached:
        return cached, ''

    params = {
        'origin': origin.get('location'),
        'destination': dest.get('location'),
        'extensions': 'base',
    }
    steps = []
    cost = ''
    path = ''

    if mode == 'driving':
        path = '/v3/direction/driving'
        params['strategy'] = '0'
    elif mode == 'walking':
        path = '/v3/direction/walking'
    elif mode == 'riding':
        path = '/v4/direction/bicycling'
    elif mode == 'transit':
        path = '/v3/direction/transit/integrated'
        params['city'] = origin.get('citycode') or origin.get('city') or ''
        params['cityd'] = dest.get('citycode') or dest.get('city') or ''
        params['strategy'] = '0'
        params.pop('extensions', None)

    data, err = _amap(path, params)
    if not data:
        return _estimate_route(mode, origin, dest, err)

    distance = 0
    duration = 0
    summary = ''
    try:
        route = (data.get('data') if mode == 'riding' else data.get('route')) or {}
        if mode == 'transit':
            transits = route.get('transits') or []
            if not transits:
                return _estimate_route(mode, origin, dest, '没有查到公交方案')
            best = transits[0]
            distance = _int(best.get('distance'))
            duration = _int(best.get('duration'))
            cost = best.get('cost')
            for seg in (best.get('segments') or [])[:8]:
                walk = _int((seg.get('walking') or {}).get('distance'))
                if walk:
                    steps.append({'text': '步行 %d 米' % walk, 'distance': walk})
                for bus in (seg.get('bus') or {}).get('buslines') or []:
                    name = _strip_html(bus.get('name'))
                    via = _int(bus.get('via_num'))
                    dep = _clean((bus.get('departure_stop') or {}).get('name'), 40)
                    arr = _clean((bus.get('arrival_stop') or {}).get('name'), 40)
                    if name:
                        steps.append({
                            'text': name + ('（%d 站）' % via if via else ''),
                            'detail': (dep + '上车' if dep else '') + (' · ' + arr + '下车' if arr else ''),
                            'distance': _int(bus.get('distance')),
                        })
            summary = '公交 / 地铁'
        else:
            paths = route.get('paths') or []
            if not paths:
                return _estimate_route(mode, origin, dest, '没有查到路线')
            best = paths[0]
            distance = _int(best.get('distance'))
            duration = _int(best.get('duration'))
            for step in (best.get('steps') or [])[:10]:
                text = _strip_html(step.get('instruction'))
                if text:
                    steps.append({'text': text, 'distance': _int(step.get('distance'))})
            if mode == 'driving':
                summary = '驾车'
            elif mode == 'walking':
                summary = '步行'
            else:
                summary = '骑行'
    except (TypeError, ValueError, AttributeError):
        return _estimate_route(mode, origin, dest, '路线数据格式异常')

    if not distance or distance <= 0:
        return _estimate_route(mode, origin, dest, '路线数据不完整')

    out = {
        'mode': mode,
        'label': MODES.get(mode, mode),
        'distance_m': distance,
        'distance_text': _distance_text(distance),
        'duration_min': max(1, round(duration / 60)) if duration else _travel_minutes(distance, mode),
        'summary': summary or MODES.get(mode, mode),
        'steps': steps,
        'estimated': False,
        'cost': _clean(cost, 20),
    }
    _cache_set(cache_key, out)
    return out, ''


def _estimate_route(mode: str, origin: dict, dest: dict, note: str = ''):
    """高德路线失败时只做直线估算，并且明确标出来，不冒充真实路线。"""
    distance = _haversine(origin.get('lng'), origin.get('lat'), dest.get('lng'), dest.get('lat'))
    if not distance:
        return None, note or '算不出路线'
    return {
        'mode': mode,
        'label': MODES.get(mode, mode),
        'distance_m': int(round(distance)),
        'distance_text': _distance_text(distance),
        'duration_min': _travel_minutes(distance, mode),
        'summary': MODES.get(mode, mode) + '（直线估算）',
        'steps': [],
        'estimated': True,
        'cost': '',
        'note': note or '高德没有返回路线，距离和耗时是按直线估算的',
    }, ''


def _distance_text(meters: float) -> str:
    meters = max(0, float(meters or 0))
    if meters < 1000:
        return '%d 米' % round(meters)
    return '%.1f 公里' % (meters / 1000)


def _travel_minutes(meters: float, mode: str) -> int:
    speed = _SPEED.get(mode, 300)
    return max(1, int(math.ceil(float(meters or 0) / speed)) + _BUFFER_MIN.get(mode, 5))


def _haversine(lng1, lat1, lng2, lat2) -> float:
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


# ---------------------------------------------------------------
# 天气
# ---------------------------------------------------------------
def _weather(adcode: str, day: str):
    adcode = _clean(adcode, 20)
    if not adcode:
        return None
    cache_key = 'weather:' + adcode
    data = _cache_get(cache_key, _WEATHER_TTL)
    if data is None:
        data, err = _amap('/v3/weather/weatherInfo', {
            'city': adcode, 'extensions': 'all',
        })
        if not data:
            return None
        _cache_set(cache_key, data)
    try:
        casts = ((data.get('forecasts') or [{}])[0]).get('casts') or []
    except (AttributeError, IndexError, TypeError):
        return None
    for cast in casts:
        if _clean(cast.get('date'), 10) == day:
            return {
                'date': day,
                'week': _clean(cast.get('week'), 10),
                'day': _clean(cast.get('dayweather'), 30),
                'night': _clean(cast.get('nightweather'), 30),
                'day_temp': _clean(cast.get('daytemp'), 10),
                'night_temp': _clean(cast.get('nighttemp'), 10),
                'wind': _clean(cast.get('daywind'), 20),
                'power': _clean(cast.get('daypower'), 20),
            }
    return None


# ---------------------------------------------------------------
# 周边 POI
# ---------------------------------------------------------------
def _poi_around(dest: dict, types: str, kind: str, tag: str):
    location = dest.get('location') or ''
    cache_key = 'poi:%s:%s' % (location, kind)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    data, _err = _amap('/v3/place/around', {
        'location': location,
        'types': types,
        'radius': '20000',
        'sortrule': 'weight',
        'offset': '25',
        'page': '1',
        'extensions': 'all',
    })
    if not data:
        return []

    out = []
    seen = set()
    for poi in data.get('pois') or []:
        pid = _clean(poi.get('id'), 40)
        name = _clean(poi.get('name'), 60)
        lng, lat = _split_location(poi.get('location'))
        if not pid or not name or lng is None or lat is None or pid in seen:
            continue
        seen.add(pid)
        biz = poi.get('biz_ext') if isinstance(poi.get('biz_ext'), dict) else {}
        out.append({
            'id': pid,
            'name': name,
            'kind': kind,
            'tag': tag,
            'type': _clean(str(poi.get('type') or '').split(';')[-1], 30),
            'address': _clean(poi.get('address'), 100),
            'location': '%.6f,%.6f' % (lng, lat),
            'lng': lng,
            'lat': lat,
            'distance_m': _int(poi.get('distance')),
            'rating': _clean(biz.get('rating'), 10),
            'cost': _clean(biz.get('cost'), 10),
        })
    _cache_set(cache_key, out)
    return out


def _pref_score(poi: dict, prefs) -> float:
    score = _num(poi.get('rating')) or 0
    score = score * 10 - min(8.0, (_num(poi.get('distance_m')) or 0) / 1500.0)
    text = (poi.get('name') or '') + (poi.get('type') or '')
    hints = {
        '亲子': ('动物园', '海洋', '乐园', '科技馆', '博物馆', '公园', '植物'),
        '美食': ('小吃', '美食', '餐厅', '街'),
        '自然': ('公园', '湖', '山', '森林', '湿地', '植物', '风景', '岛'),
        '人文': ('博物馆', '故居', '寺', '古', '文化', '纪念馆', '书院', '塔', '遗址'),
    }
    for pref in prefs:
        if pref == '轻松':
            continue
        if pref == '紧凑':
            continue
        for word in hints.get(pref, ()):
            if word in text:
                score += 9
    if poi.get('kind') == 'food':
        score += 4
    return score


def _candidates(dest: dict, prefs) -> tuple:
    sights = _poi_around(dest, SIGHT_TYPES, 'sight', '景点')
    foods = _poi_around(dest, FOOD_TYPES, 'food', '餐饮')
    sights = sorted(sights, key=lambda p: _pref_score(p, prefs), reverse=True)[:18]
    foods = sorted(foods, key=lambda p: _pref_score(p, prefs), reverse=True)[:10]
    return sights, foods


# ---------------------------------------------------------------
# 模型：只让它在候选里挑，时间和距离一概不让它算
# ---------------------------------------------------------------
PLAN_SYS = '\n'.join([
    '你是一个本地出行编辑。你要从给定的候选地点里挑几个点、排个顺序，并说明为什么值得去。',
    '',
    '【硬规则】',
    '1. 只能选候选列表里出现过的 id，不许编造任何地点。',
    '2. 不许写开放时间、门票价格、预约要求、排队时长 —— 这些数据没给你，写了就是编。',
    '3. 不许改坐标、距离、交通耗时、天气。时间安排由程序统一计算，你只给停留分钟数。',
    '4. 一天行程挑 4 到 6 个点，半天挑 2 到 4 个点；如果候选里有餐饮，饭点前后安排一个。',
    '5. 同一 id 不许重复；array 里只放 id，不要放整段地点信息。',
    '6. 说人话，不写「必打卡」「人间值得」「绝美」这类空话。理由要具体到：这里看什么、适合谁、为什么排在这个位置。',
    '',
    '【输出 JSON，不要 Markdown】',
    '{',
    '  "summary": "一句话概括这条路线怎么走，40 字以内",',
    '  "route_tip": "基于给定路线的一句具体提醒，40 字以内",',
    '  "items": [{"id": "候选 id", "stay_min": 60, "why": "15-40 字", "tip": "10-30 字，可空"}],',
    '  "alternatives": [{"id": "候选 id", "why": "15-30 字"}],',
    '  "warnings": ["0-3 条真实可核对的提醒"]',
    '}',
])


def _chat(system: str, user: str, max_tokens: int = 1600, temperature: float = 0.4):
    key = llm_key()
    if not key:
        return None, '服务端没有配置模型 Key'
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    try:
        resp = requests.post(llm_endpoint(), json=llm_body(body), timeout=(10, 120),
                             headers={'Authorization': 'Bearer ' + key,
                                      'Content-Type': 'application/json'})
    except requests.RequestException as exc:
        return None, '模型请求失败：' + str(exc)[:80]
    if resp.status_code >= 400:
        return None, '模型返回错误 ' + str(resp.status_code)
    try:
        return resp.json()['choices'][0]['message']['content'], ''
    except (ValueError, KeyError, IndexError, TypeError):
        return None, '模型返回格式异常'


def _extract_json(raw: str) -> dict:
    if not raw:
        return {}
    text = str(raw).strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _prompt(req: dict, origin: dict, dest: dict, route: dict, weather, sights, foods) -> str:
    candidates = []
    for poi in sights + foods:
        candidates.append({
            'id': poi['id'],
            'name': poi['name'],
            'kind': poi['kind'],
            'tag': poi['tag'] or poi['type'],
            'rating': poi['rating'],
            'cost': poi['cost'],
            'distance_m': poi['distance_m'],
            'address': poi['address'][:60],
        })
    lines = [
        '起点：%s（%s）' % (req['origin'], origin.get('name') or ''),
        '目的地：%s（%s）' % (req['destination'], dest.get('name') or ''),
        '日期：%s；出发时间：%s；可用时长：%s；交通：%s' % (
            req['date'], req['start_time'], DURATIONS[req['duration']][0],
            MODES[req['mode']]),
        '偏好：%s' % ('、'.join(req['prefs']) if req['prefs'] else '没有特别偏好'),
        '高德给的路线：%s，%s，约 %d 分钟%s' % (
            route.get('summary'), route.get('distance_text'),
            route.get('duration_min') or 0,
            '（直线估算，不是真实路线）' if route.get('estimated') else ''),
    ]
    if weather:
        lines.append('天气：%s %s，%s~%s℃' % (
            weather.get('day') or '', weather.get('week') or '',
            weather.get('night_temp') or '?', weather.get('day_temp') or '?'))
    else:
        lines.append('天气：未获取到')
    lines.append('')
    lines.append('候选地点（只能从这里选 id）：')
    lines.append(json.dumps(candidates, ensure_ascii=False, indent=1))
    lines.append('')
    lines.append('请按系统要求返回 JSON。')
    return '\n'.join(lines)


def _normalize_ai(data: dict, sights: list, foods: list) -> tuple:
    by_id = {p['id']: p for p in sights + foods}
    picked, used = [], set()
    for raw in (data.get('items') or []):
        if not isinstance(raw, dict):
            continue
        pid = _clean(raw.get('id') or raw.get('poi_id') or raw.get('place_id'), 40)
        if pid not in by_id or pid in used:
            continue
        used.add(pid)
        picked.append({
            'poi': by_id[pid],
            'stay_min': max(_MIN_STAY, min(_MAX_STAY, _int(raw.get('stay_min'), 60))),
            'why': _clean(raw.get('why'), 120),
            'tip': _clean(raw.get('tip'), 100),
        })
    alts = []
    for raw in (data.get('alternatives') or []):
        if not isinstance(raw, dict):
            continue
        pid = _clean(raw.get('id') or raw.get('poi_id') or raw.get('place_id'), 40)
        if pid in by_id and pid not in used:
            used.add(pid)
            alts.append({'poi': by_id[pid], 'why': _clean(raw.get('why'), 90)})
        if len(alts) >= 3:
            break
    warnings = [_clean(w, 100) for w in (data.get('warnings') or []) if _clean(w, 100)][:3]
    return picked, alts, warnings


def _fallback_picks(sights: list, foods: list, duration: str) -> tuple:
    """没有模型时，按高德评分和类别自动排一条基础行程。"""
    want = 6 if duration == 'full' else 4
    sights = sights[:want]
    foods = foods[:1] if want >= 3 else []
    picked = []
    food_at = None
    if foods:
        food_at = len(sights) // 2
    for index, poi in enumerate(sights):
        if food_at is not None and index == food_at:
            food = foods.pop(0)
            picked.append({
                'poi': food, 'stay_min': 60,
                'why': '沿途解决一顿饭，按高德周边评分选出的餐馆。',
                'tip': '出发前再确认营业状态。',
            })
        why = '高德周边评分较高的%s，离目的地约 %s。' % (
            poi.get('type') or '地点', _distance_text(poi.get('distance_m') or 0))
        picked.append({
            'poi': poi, 'stay_min': 90 if poi.get('kind') == 'sight' else 60,
            'why': why, 'tip': '',
        })
    if food_at is not None and foods:
        food = foods.pop(0)
        picked.append({
            'poi': food, 'stay_min': 60,
            'why': '沿途解决一顿饭，按高德周边评分选出的餐馆。',
            'tip': '出发前再确认营业状态。',
        })
    return picked, [], []


# ---------------------------------------------------------------
# 排程：所有时间都在这里算，模型只给停留分钟
# ---------------------------------------------------------------
def _fmt_min(minutes: int) -> str:
    minutes = max(0, int(minutes))
    if minutes >= 24 * 60:
        return '23:59'
    return '%02d:%02d' % (minutes // 60, minutes % 60)


def _parse_clock(value: str) -> int:
    match = re.match(r'^(\d{1,2}):(\d{2})$', str(value or '').strip())
    if not match:
        return 8 * 60 + 30
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return 8 * 60 + 30
    return hour * 60 + minute


def _build_schedule(picked: list, req: dict, dest: dict, route: dict) -> tuple:
    start_min = _parse_clock(req['start_time'])
    end_min = min(start_min + DURATIONS[req['duration']][1], 23 * 60 + 59)
    cursor = start_min + max(1, _int(route.get('duration_min'), 30)) + 10
    items, skipped = [], []
    prev = dest

    for entry in picked:
        poi = entry['poi']
        distance = _haversine(
            prev.get('lng'), prev.get('lat'), poi.get('lng'), poi.get('lat'))
        travel = _travel_minutes(distance, req['mode']) if distance else 0
        arrive = cursor + travel
        leave = arrive + entry['stay_min']
        if arrive >= end_min or leave > end_min:
            skipped.append(entry)
            continue
        items.append({
            'poi': poi,
            'start': _fmt_min(arrive),
            'end': _fmt_min(leave),
            'stay_min': entry['stay_min'],
            'travel_min': travel,
            'travel_m': int(round(distance)),
            'travel_text': _distance_text(distance) if distance else '',
            'from_prev': prev.get('name') or '',
            'why': entry.get('why') or '',
            'tip': entry.get('tip') or '',
        })
        cursor = leave
        prev = poi

    return items, skipped


def _build_plan(req: dict, origin: dict, dest: dict, route: dict, weather,
                picked: list, alts: list, ai_warnings: list,
                sights: list, foods: list, ai_used: bool) -> dict:
    items, skipped = _build_schedule(picked, req, dest, route)
    if not items:
        return {}

    used = {item['poi']['id'] for item in items}
    # 因为时间不够没排进去的点会在「skipped」里单独展示，不要又在备选里重复一次。
    skipped_ids = {s['poi']['id'] for s in skipped}
    blocked = used | skipped_ids
    alternatives = []
    for alt in alts:
        if alt['poi']['id'] not in blocked:
            alternatives.append({
                'poi': alt['poi'],
                'why': alt.get('why') or '',
            })
        if len(alternatives) >= 3:
            break
    if len(alternatives) < 2:
        for poi in sorted(sights + foods, key=lambda p: _pref_score(p, req['prefs']),
                          reverse=True):
            if poi['id'] in blocked:
                continue
            if any(a['poi']['id'] == poi['id'] for a in alternatives):
                continue
            alternatives.append({
                'poi': poi,
                'why': '顺路且评分较高，时间紧时可以替换其中一站。',
            })
            if len(alternatives) >= 2:
                break

    warnings = list(ai_warnings)
    if route.get('estimated'):
        warnings.append('高德没有返回完整路线，去程距离和耗时按直线估算，出发前请用导航再确认一次。')
    if weather:
        kind = (weather.get('day') or '') + (weather.get('night') or '')
        if any(word in kind for word in ('雨', '雪', '雷')):
            warnings.append('当天天气可能出现「%s」，带伞，并把户外点放在天气最好的时段。' % (
                weather.get('day') or kind))
    if not llm_key():
        warnings.append('未配置模型 Key，当前行程按高德评分自动排；可以配置后再生成，让模型补充理由和取舍。')
    if not any('开放' in w or '营业' in w or '预约' in w for w in warnings):
        warnings.append('开放时间、票价和预约要求没有实时数据，出发前请再确认。')
    if skipped:
        warnings.append('有 %d 个候选点因为时间不够没排进主路线，可在「备选」里查看。' % len(skipped))

    total_m = 0
    for index, item in enumerate(items):
        if index > 0:
            total_m += item['travel_m']
    total_travel = sum(item['travel_min'] for item in items[1:])
    known_end = items[-1]['end']

    if ai_used:
        summary = _clean(req.pop('_summary', ''), 120)
        route_tip = _clean(req.pop('_route_tip', ''), 120)
    else:
        summary = route_tip = ''
    if not summary:
        summary = '%s出发，%s到%s，排了 %d 个点，路上换乘约 %d 分钟。' % (
            req['start_time'], DURATIONS[req['duration']][0], req['destination'],
            len(items), total_travel)
    if not route_tip:
        route_tip = '%s约 %d 分钟，建议按时间线留出 10 分钟机动。' % (
            route.get('summary') or MODES[req['mode']],
            route.get('duration_min') or 0)

    return {
        'summary': summary,
        'route_tip': route_tip,
        'route': route,
        'weather': weather,
        'items': items,
        'alternatives': alternatives,
        'warnings': warnings[:6],
        'skipped': [{'poi': s['poi'], 'why': s.get('why') or ''} for s in skipped][:4],
        'origin': origin,
        'destination': dest,
        'stats': {
            'stops': len(items),
            'travel_min': total_travel,
            'travel_text': _distance_text(total_m) if total_m else '0 米',
            'end_time': known_end,
            'budget_min': DURATIONS[req['duration']][1],
        },
        'ai_used': ai_used,
        'demo': False,
    }


# ---------------------------------------------------------------
# 生成
# ---------------------------------------------------------------
def _generate(raw: dict) -> tuple:
    req = _normalize_request(raw)
    if not req:
        return None, '起点、目的地和日期至少要填对，时长和交通方式只能从选项里选。'

    start_min = _parse_clock(req['start_time'])
    if start_min + DURATIONS[req['duration']][1] > 23 * 60 + 59:
        return None, '这个出发时间加上所选时长会跨到第二天，换早一点的时间或缩短时长再试。'

    if not os.getenv('AMAP_WEB_KEY', '').strip():
        demo = _demo_plan(req)
        if not demo:
            return None, '可用时间内排不进任何地点，把出发时间提前一点再试。'
        return demo, ''

    origin = None
    if (req.get('origin_coordsys') == 'gps'
            and req.get('origin_lng') is not None and req.get('origin_lat') is not None):
        # 浏览器定位是 WGS84，先转成高德的 GCJ-02，再逆编码和算路线。
        converted, conv_err = map_common.convert_gps(req['origin_lng'], req['origin_lat'])
        if not converted:
            return None, '定位坐标转换失败：' + (conv_err or '高德没有返回结果') + '，也可以直接手填起点。'
        req['origin_lng'], req['origin_lat'] = converted
    if req.get('origin_lng') is not None and req.get('origin_lat') is not None:
        origin, origin_err = _regeo(req['origin_lng'], req['origin_lat'])
        if origin:
            req['origin'] = req['origin'] or origin['name']
    else:
        origin, origin_err = _geocode(req['origin'])
    if not origin:
        return None, '起点没找到：' + origin_err

    dest, dest_err = _geocode(req['destination'])
    if not dest:
        return None, '目的地没找到：' + dest_err

    route, route_err = _route(req['mode'], origin, dest)
    if not route:
        return None, '路线查不到：' + (route_err or '高德没有返回可用方案')

    weather = _weather(dest.get('adcode'), req['date'])
    sights, foods = _candidates(dest, req['prefs'])
    if not sights and not foods:
        return None, '目的地周边没有搜到可安排的地点，换个更具体的地名试试。'

    picked, alts, ai_warnings, ai_used = [], [], [], False
    if llm_key():
        prompt = _prompt(req, origin, dest, route, weather, sights, foods)
        content, err = _chat(PLAN_SYS, prompt)
        data = _extract_json(content) if content else {}
        if data:
            picked, alts, ai_warnings = _normalize_ai(data, sights, foods)
            if len(picked) >= 2:
                ai_used = True
                req['_summary'] = _clean(data.get('summary'), 120)
                req['_route_tip'] = _clean(data.get('route_tip'), 120)
            else:
                picked, alts, ai_warnings = [], [], []
                ai_warnings.append('模型没有从候选里选出足够的点，已改用高德评分自动排。')
        elif err:
            ai_warnings.append(err + '，已改用高德评分自动排。')
    if not picked:
        picked, alts, fallback_warnings = _fallback_picks(sights, foods, req['duration'])
        ai_warnings.extend(fallback_warnings)

    plan = _build_plan(req, origin, dest, route, weather, picked, alts,
                       ai_warnings, sights, foods, ai_used)
    if not plan:
        return None, '可用时间内排不进任何地点，试试把出发时间提前或选择「一天」。'
    return plan, ''


def _normalize_request(raw: dict) -> Optional[dict]:
    origin = _clean(raw.get('origin'), 100)
    destination = _clean(raw.get('destination'), 100)
    if not destination:
        return None
    if not origin and (raw.get('origin_lng') is None or raw.get('origin_lat') is None):
        return None

    day = _clean(raw.get('date'), 20)
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', day):
        day = date.today().isoformat()
    else:
        try:
            date.fromisoformat(day)
        except ValueError:
            day = date.today().isoformat()

    start_time = _clean(raw.get('start_time'), 10) or '08:30'
    if not re.match(r'^\d{1,2}:\d{2}$', start_time):
        start_time = '08:30'

    mode = _pick(raw.get('mode'), tuple(MODES), 'driving')
    duration = _pick(raw.get('duration'), tuple(DURATIONS), 'full')

    prefs_raw = raw.get('prefs')
    if isinstance(prefs_raw, str):
        prefs_raw = prefs_raw.split(',')
    prefs = []
    if isinstance(prefs_raw, list):
        for item in prefs_raw:
            word = _clean(item, 10)
            if word in PREFS and word not in prefs:
                prefs.append(word)
    prefs = prefs[:PREFS_MAX]

    origin_lng = _num(raw.get('origin_lng'))
    origin_lat = _num(raw.get('origin_lat'))
    if origin_lng is not None and not (-180 <= origin_lng <= 180):
        origin_lng = None
    if origin_lat is not None and not (-90 <= origin_lat <= 90):
        origin_lat = None

    return {
        'origin': origin,
        'destination': destination,
        'date': day,
        'start_time': start_time,
        'mode': mode,
        'duration': duration,
        'prefs': prefs,
        'origin_lng': origin_lng,
        'origin_lat': origin_lat,
        # 旧请求 / 地点收藏里的坐标都是高德坐标；只有显式标 gps 才转换。
        'origin_coordsys': 'gps' if raw.get('origin_coordsys') == 'gps' else 'gcj02',
    }


def _demo_plan(req: dict) -> dict:
    """没配高德 Key 时给一份明确标注的演示数据，只负责把页面跑通。"""
    demo_origin = {
        'name': req['origin'] or '演示起点', 'address': req['origin'] or '演示起点',
        'location': '120.169000,30.244000', 'lng': 120.169, 'lat': 30.244,
        'city': '杭州市', 'district': '上城区', 'adcode': '330102', 'citycode': '0571',
    }
    demo_dest = {
        'name': req['destination'] or '西湖', 'address': req['destination'] or '西湖',
        'location': '120.150000,30.250000', 'lng': 120.150, 'lat': 30.250,
        'city': '杭州市', 'district': '西湖区', 'adcode': '330106', 'citycode': '0571',
    }
    route = {
        'mode': req['mode'],
        'label': MODES[req['mode']],
        'distance_m': 6800,
        'distance_text': '6.8 公里',
        'duration_min': 26,
        'summary': MODES[req['mode']] + '（演示）',
        'steps': [{'text': '演示路线：真实路线需要配置 AMAP_WEB_KEY 后才会查询', 'distance': 0}],
        'estimated': False,
        'cost': '',
        'note': '演示数据，未调用高德',
    }
    demo_pois = [
        {'id': 'demo-1', 'name': '断桥残雪', 'kind': 'sight', 'tag': '景点',
         'type': '风景名胜', 'address': '演示地址', 'location': '120.155300,30.259000',
         'lng': 120.1553, 'lat': 30.2590, 'distance_m': 900, 'rating': '4.6', 'cost': ''},
        {'id': 'demo-2', 'name': '白堤', 'kind': 'sight', 'tag': '景点',
         'type': '风景名胜', 'address': '演示地址', 'location': '120.151000,30.258500',
         'lng': 120.1510, 'lat': 30.2585, 'distance_m': 700, 'rating': '4.7', 'cost': ''},
        {'id': 'demo-3', 'name': '孤山', 'kind': 'sight', 'tag': '景点',
         'type': '风景名胜', 'address': '演示地址', 'location': '120.145800,30.255000',
         'lng': 120.1458, 'lat': 30.2550, 'distance_m': 1100, 'rating': '4.5', 'cost': ''},
        {'id': 'demo-4', 'name': '曲院风荷', 'kind': 'sight', 'tag': '景点',
         'type': '风景名胜', 'address': '演示地址', 'location': '120.137000,30.247000',
         'lng': 120.1370, 'lat': 30.2470, 'distance_m': 2400, 'rating': '4.6', 'cost': ''},
        {'id': 'demo-5', 'name': '演示餐馆', 'kind': 'food', 'tag': '餐饮',
         'type': '中餐厅', 'address': '演示地址', 'location': '120.145000,30.253000',
         'lng': 120.1450, 'lat': 30.2530, 'distance_m': 800, 'rating': '4.4', 'cost': '80'},
    ]
    picked, alts, warnings = _fallback_picks(demo_pois[:4], [demo_pois[4]], req['duration'])
    plan = _build_plan(req, demo_origin, demo_dest, route, None, picked, alts,
                       warnings, demo_pois[:4], [demo_pois[4]], False)
    if not plan:
        # 时间实在太晚时不硬塞，交给调用方提示换时间
        return {}
    plan['demo'] = True
    plan['warnings'].insert(0, '当前是演示行程：没有配置 AMAP_WEB_KEY，地点、路线和天气都是示例，不是真实查询结果。')
    plan['summary'] = '演示行程：' + plan['summary']
    return plan


# ---------------------------------------------------------------
# 路由
# ---------------------------------------------------------------
@router.get('/trip', response_class=HTMLResponse)
def trip_page(request: Request) -> HTMLResponse:
    try:
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    except OSError:
        html = (
            '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
            '<body style="background:#0a0f1e;color:#e8edf7;font-family:system-ui">'
            '<h2>出行攻略</h2><p>页面模板缺失：templates/trip.html</p></body></html>')
    return HTMLResponse(html)


@router.get('/trip/api/status')
def trip_status() -> JSONResponse:
    web_key = bool(os.getenv('AMAP_WEB_KEY', '').strip())
    js_key = bool(os.getenv('AMAP_JS_KEY', '').strip())
    security = bool(os.getenv('AMAP_SECURITY_CODE', '').strip())
    allow_map = web_key and js_key and security
    ai_ready = bool(llm_key())
    missing = []
    if not web_key:
        missing.append('AMAP_WEB_KEY')
    if not js_key:
        missing.append('AMAP_JS_KEY')
    if not security:
        missing.append('AMAP_SECURITY_CODE')
    if not ai_ready:
        missing.append('模型 Key（DEEPSEEK_API_KEY / ARK_API_KEY 等）')
    if not web_key:
        hint = '还没配置高德 Web 服务 Key，当前只能用演示行程预览界面。'
    elif not (js_key and security):
        hint = '高德路线可用，但没配置 JS API Key / 安全密钥，结果页暂时不显示地图。'
    elif not ai_ready:
        hint = '高德已就绪；模型未配置，会按高德评分自动排基础行程。'
    else:
        hint = '高德和模型都已就绪。'
    return JSONResponse({
        'ok': True,
        'amap_ready': web_key,
        'map_ready': bool(web_key and js_key and security),
        'ai_ready': ai_ready,
        'missing': missing,
        'hint': hint,
        # JS API Key 和安全密钥本来就要下发到浏览器；Web 服务 Key 绝不下发。
        'js_key': os.getenv('AMAP_JS_KEY', '').strip() if allow_map else '',
        'security_code': os.getenv('AMAP_SECURITY_CODE', '').strip() if allow_map else '',
    })


@router.post('/trip/api/plan')
async def trip_plan(request: Request) -> JSONResponse:
    body = await _body(request)
    plan, err = await run_in_threadpool(_generate, body)
    if not plan:
        return JSONResponse({'ok': False, 'message': err}, status_code=400)
    public_req = _public_request(_normalize_request(body) or {})
    origin_name = ((plan.get('origin') or {}).get('name') or '').strip()
    if origin_name and (not public_req.get('origin') or
                        public_req.get('origin') in ('我的位置', '当前位置')):
        public_req['origin'] = origin_name
    plan['request'] = public_req
    return JSONResponse({'ok': True, 'plan': plan})


@router.get('/trip/api/plans')
def trip_plans(request: Request) -> JSONResponse:
    user = _user(request)
    try:
        rows = storage.list_records(tool=TOOL, user=user, limit=50)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    items = []
    for row in rows:
        payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
        digest = payload.get('digest') if isinstance(payload.get('digest'), dict) else {}
        items.append({
            'id': row.get('id'),
            'title': row.get('title') or '',
            'created_at': row.get('created_at') or '',
            'summary': digest.get('summary') or '',
            'stops': digest.get('stops') or 0,
            'date': digest.get('date') or '',
        })
    return JSONResponse({'ok': True, 'items': items})


@router.post('/trip/api/plans')
async def trip_save(request: Request) -> JSONResponse:
    user = _user(request)
    body = await _body(request)
    plan = body.get('plan') if isinstance(body.get('plan'), dict) else {}
    req = plan.get('request') if isinstance(plan.get('request'), dict) else {}
    if not plan or not plan.get('items'):
        return JSONResponse({'ok': False, 'message': '没有可保存的行程。'}, status_code=400)
    title = '%s → %s · %s' % (
        _clean(req.get('origin'), 30) or '起点',
        _clean(req.get('destination'), 30) or '目的地',
        _clean(req.get('date'), 20) or date.today().isoformat())
    payload = {
        'request': req,
        'plan': plan,
        'digest': {
            'summary': _clean(plan.get('summary'), 120),
            'stops': len(plan.get('items') or []),
            'date': _clean(req.get('date'), 20),
        },
    }
    try:
        row = storage.add_record(tool=TOOL, user=user, title=title[:200], payload=payload)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    return JSONResponse({'ok': True, 'id': row.get('id'), 'title': title,
                         'created_at': row.get('created_at')})


@router.get('/trip/api/plans/{rid}')
def trip_get(rid: int, request: Request) -> JSONResponse:
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'message': '找不到这条行程。'}, status_code=404)
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    return JSONResponse({'ok': True, 'id': row.get('id'), 'title': row.get('title'),
                         'created_at': row.get('created_at'),
                         'request': payload.get('request') or {},
                         'plan': payload.get('plan') or {}})


@router.delete('/trip/api/plans/{rid}')
def trip_delete(rid: int, request: Request) -> JSONResponse:
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return JSONResponse({'ok': False, 'message': '找不到这条行程。'}, status_code=404)
        affected = storage.delete_record(user, rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not affected:
        return JSONResponse({'ok': False, 'message': '这条不属于当前账号。'}, status_code=403)
    return JSONResponse({'ok': True})


def _public_request(req: dict) -> dict:
    return {
        'origin': req.get('origin') or '',
        'destination': req.get('destination') or '',
        'date': req.get('date') or '',
        'start_time': req.get('start_time') or '',
        'mode': req.get('mode') or 'driving',
        'duration': req.get('duration') or 'full',
        'prefs': req.get('prefs') or [],
    }


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _app = FastAPI(title='出行攻略', version='1.0.0')
    _app.include_router(router)
    uvicorn.run(_app, host='127.0.0.1', port=8032)
