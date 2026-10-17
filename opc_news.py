# -*- coding: utf-8 -*-
# OPC 资讯（页面在 /opc）
# ------------------------------------------------------------
# 给做「一人公司 / 独立开发」的人用：按主题去 GitHub 捞仓库，按 star 排，
# 再让大模型翻成人话，告诉你哪个值得点开。
#
# 分工（改之前先读）：
#   1. GitHub 那只手是 Python 在干：带 Token、限流、缓存、算「活跃度」。
#      活跃度是按 pushed_at 距今多少天算出来的，不是模型猜的。
#   2. 大模型只做一件事：把「名称/描述/语言/star/最近更新/话题标签」翻成中文解读。
#      prompt 里明令禁止它补充没看到的细节——这类任务模型最爱编造功能清单，
#      而我们能喂给它的永远只有作者自己写的那一行描述。
#   3. 没配 DEEPSEEK_API_KEY 也能用，只是没有中文解读，仓库列表照样能看。
#
# 两个口子：
#   /opc/api/search     关键词搜索（原来的）
#   /opc/api/trending   趋势榜：按「新建时间窗 + 领域 + 语言」筛，按 star 涨速排
#                        —— GitHub 没有 trending 接口，这里用 created:>= 限定新项目，
#                        再用「star / 项目年龄」当涨速，口径和 trending 的常见近似一致。
#
# 环境变量（都可选）：
#   GITHUB_TOKEN       配了就把 GitHub 限额从 60/小时 提到 5000/小时，搜索从 10/分钟提到 30/分钟
#   DEEPSEEK_API_KEY   不配就只显示原始描述，没有中文解读
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

try:
    from model_config import llm_endpoint, llm_key, llm_model
except Exception:  # 单独拷这个文件跑时的兜底
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'opc.html'

GITHUB_API = 'https://api.github.com/search/repositories'
ENV_GH_TOKEN = 'GITHUB_TOKEN'

PER_PAGE = 12            # 一次给几个仓库
MAX_PER_PAGE = 30
QUERY_MAX = 120          # 关键词最长多少字
CACHE_TTL = 600          # 同一个查询 10 分钟内不重复打 GitHub
DIGEST_MAX = 12          # 一次最多让模型解读几个

# 活跃度分档（按 pushed_at 距今的天数）
FRESH_DAYS = 90          # 三个月内有提交 -> 活跃
ALIVE_DAYS = 730         # 两年内 -> 维护中，再久算停更

_CACHE = {}              # {key: (expire_ts, payload)}

# ---- 趋势榜 ----
# GitHub 没有公开的 trending 接口，只能自己近似：
#   1) created:>= 限定「这段时间里新建的」项目；
#   2) 排序用 star / 项目年龄 = 平均每天涨多少 star（涨速），
#      比单纯按总 star 排更接近 trending 的口味。
TREND_WINDOWS = [
    ('d1', '今天', 1),
    ('d3', '三天内', 3),
    ('d7', '本周', 7),
    ('d30', '本月', 30),
    ('d90', '近三个月', 90),
]
TREND_DAYS = {key: days for key, _label, days in TREND_WINDOWS}
# 领域 = 一个关键词。实测 GitHub 搜索既不认括号分组（返回 0 条），
# 也不认限定符之间裸写 OR（直接 422），所以每个领域只放一个词，宁宽勿空。
TREND_FIELDS = [
    ('', '全部领域', ''),
    ('ai', 'AI / LLM', 'ai'),
    ('agent', 'Agent', 'agent'),
    ('saas', 'SaaS', 'saas'),
    ('devtool', '开发工具', 'cli'),
    ('auto', '自动化', 'automation'),
    ('selfhost', '自部署', 'self-hosted'),
    ('content', '内容 / 写作', 'writing'),
]
TREND_FIELD_Q = {key: q for key, _label, q in TREND_FIELDS}
TREND_LANGS = ['', 'Python', 'TypeScript', 'JavaScript', 'Go', 'Rust', 'Java',
               'C++', 'C#', 'PHP', 'Swift', 'Kotlin', 'Shell', 'Vue', 'Jupyter Notebook']
TREND_MIN_STARS = 5      # 太新的仓库常有 0~2 star 的噪音，砍掉
TREND_FETCH_X = 2        # 按总 star 多拉一倍，再在本地按涨速重排


def _gh_token():
    return (os.environ.get(ENV_GH_TOKEN) or '').strip()


DIGEST_SYS = '\n'.join([
    '你在帮一个做「一人公司 / 独立开发」的人筛 GitHub 项目。我给你一批仓库的原始信息，',
    '你要用中文说清每个项目是什么、对他有没有用。',
    '【最重要的一条】只能依据我给你的字段：名称、作者写的描述、语言、star 数、',
    '最近更新时间、话题标签、是否已归档。',
    '绝对不许补充你没看到的细节——不要编造它有哪些功能、支持什么平台、有多少用户、',
    '收不收费、是谁做的、是不是开源协议。作者描述写得含糊时，就笼统地说，',
    '或者直接写「作者描述比较笼统，建议点进去看」。',
    '每个项目给三段，都很短：',
    '  what —— 这是什么。不超过 30 字，说人话，别用「赋能」「一站式」「助力」这类词；',
    '  use  —— 对一人公司 / 独立开发者有什么用。不超过 35 字；',
    '          如果从描述看不出用途，就写「看不出直接用途」；',
    '  fit  —— 适合谁、什么时候值得点开。不超过 25 字；',
    '另外给一句 insight，不超过 60 字：如果这批里有同一类东西（都是清单、都是脚手架、',
    '都是 AI 工具），点出来，并说清这批里最值得先看哪一个、为什么。',
    '不要用 star 多少来判断好坏——star 高不等于对他有用，star 低也可能正合用。',
    '只输出 JSON，不要 Markdown 代码块：',
    '{"items":[{"what":"...","use":"...","fit":"..."}],"insight":"..."}',
    'items 的顺序必须和我给你的顺序一一对应，不多不少。',
])


def _extract_json(text):
    raw = re.sub(r'```[a-zA-Z]*', '', text or '')
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _chat(system, user, max_tokens=800, temperature=0.4):
    key = llm_key()
    if not key:
        return None, '没配模型 Key'
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    try:
        resp = requests.post(llm_endpoint(), json=body, timeout=(10, 90),
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


def _clean(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _days_since(iso):
    """距今多少天。算不出来给 -1。"""
    text = str(iso or '')[:10]
    try:
        when = datetime.strptime(text, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        return -1
    return int((datetime.now(timezone.utc) - when).total_seconds() // 86400)


def _activity(days):
    """活跃度是算出来的，不是模型猜的。"""
    if days < 0:
        return '未知'
    if days <= FRESH_DAYS:
        return '活跃'
    if days <= ALIVE_DAYS:
        return '维护中'
    return '停更'


def _github(query, per_page, sort='stars'):
    """问 GitHub 要数据。返回 (数据, 错误说明, 限流信息)。"""
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'newpro-opc'}
    token = _gh_token()
    if token:
        headers['Authorization'] = 'Bearer ' + token
    params = {'q': query, 'sort': sort, 'order': 'desc', 'per_page': per_page}
    try:
        resp = requests.get(GITHUB_API, params=params, headers=headers, timeout=(10, 30))
    except requests.RequestException as exc:
        return None, '连不上 GitHub：' + str(exc)[:80], {}
    remaining = resp.headers.get('X-RateLimit-Remaining', '')
    if resp.status_code in (403, 429):
        tip = ''
        try:
            left = int(resp.headers.get('X-RateLimit-Reset') or 0)
            if left:
                tip = '，大约 %d 分钟后恢复' % max(1, (left - int(time.time())) // 60 + 1)
        except (TypeError, ValueError):
            pass
        hint = ('配一个 GITHUB_TOKEN 环境变量，额度能从 10 次/分钟提到 30 次、'
                '每小时 60 次提到 5000 次')
        return None, 'GitHub 限流了%s。%s。' % (tip, hint), {'remaining': remaining}
    if resp.status_code >= 400:
        return None, 'GitHub 返回错误 ' + str(resp.status_code), {'remaining': remaining}
    try:
        data = resp.json()
    except ValueError:
        return None, 'GitHub 返回的内容看不懂', {'remaining': remaining}
    return data, '', {'remaining': remaining}


def _norm(item):
    pushed = str(item.get('pushed_at') or '')[:10]
    days = _days_since(pushed)
    created = str(item.get('created_at') or '')[:10]
    age = _days_since(created)
    topics = item.get('topics') or []
    return {
        'name': str(item.get('full_name') or '')[:80],
        'url': str(item.get('html_url') or '')[:200],
        'desc': _clean(item.get('description'), 220),
        'stars': int(item.get('stargazers_count') or 0),
        'forks': int(item.get('forks_count') or 0),
        'lang': str(item.get('language') or '')[:20],
        'topics': [str(t)[:24] for t in topics[:6]],
        'pushed': pushed,
        'days': days,
        'created': created,
        'age': age,
        'activity': _activity(days),
        'archived': bool(item.get('archived')),
        'homepage': str(item.get('homepage') or '')[:160],
    }


def _do_search(payload):
    query = re.sub(r'\s+', ' ', str(payload.get('q') or '')).strip()[:QUERY_MAX]
    if len(query) < 2:
        return JSONResponse({'ok': False, 'msg': '先给个关键词，或者点上面的主题。'})

    try:
        per = int(payload.get('per') or PER_PAGE)
    except (TypeError, ValueError):
        per = PER_PAGE
    per = max(3, min(MAX_PER_PAGE, per))

    try:
        min_stars = int(payload.get('minStars'))
        if min_stars < 0:
            min_stars = 0
    except (TypeError, ValueError):
        min_stars = 50
    min_stars = min(200000, min_stars)

    only_alive = bool(payload.get('alive'))

    # 用户自己写了 stars: 限定就别再加了，避免冲突
    q = query if 'stars:' in query else ('%s stars:>=%d' % (query, min_stars) if min_stars else query)

    key = '%s|%d|%s' % (q, per, only_alive)
    hit = _CACHE.get(key)
    now = time.time()
    if hit and hit[0] > now:
        cached = dict(hit[1])
        cached['cached'] = True
        return JSONResponse(cached)

    data, err, meta = _github(q, per)
    if data is None:
        return JSONResponse({'ok': False, 'msg': err, 'remaining': meta.get('remaining', '')})

    repos, seen = [], set()
    for item in data.get('items') or []:
        if not isinstance(item, dict):
            continue
        row = _norm(item)
        if not row['name'] or row['name'] in seen:
            continue
        seen.add(row['name'])
        repos.append(row)

    total = int(data.get('total_count') or 0)
    repos.sort(key=lambda r: r['stars'], reverse=True)
    if only_alive:
        repos = [r for r in repos if r['activity'] in ('活跃', '维护中')]

    out = {
        'ok': True, 'q': q, 'total': total, 'count': len(repos), 'repos': repos,
        'remaining': meta.get('remaining', ''), 'cached': False,
        'token': bool(_gh_token()),
    }
    _CACHE[key] = (now + CACHE_TTL, out)
    return JSONResponse(out)


def _trend_query(days, lang, field):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d')
    parts = ['created:>=%s' % since, 'stars:>=%d' % TREND_MIN_STARS]
    if lang:
        parts.append('language:"%s"' % lang if ' ' in lang else 'language:%s' % lang)
    frag = TREND_FIELD_Q.get(field) or ''
    if frag:
        parts.append(frag)
    return ' '.join(parts)


def _do_trending(payload):
    """趋势榜：新建时间窗 + 领域 + 语言 -> 按涨速（star/天）排。"""
    win = str(payload.get('win') or 'd7').strip()
    if win not in TREND_DAYS:
        win = 'd7'
    days = TREND_DAYS[win]
    lang = str(payload.get('lang') or '').strip()[:32]
    field = str(payload.get('field') or '').strip()[:20]
    sort = 'stars' if str(payload.get('sort') or '').strip() == 'stars' else 'hot'

    try:
        per = int(payload.get('per') or PER_PAGE)
    except (TypeError, ValueError):
        per = PER_PAGE
    per = max(3, min(MAX_PER_PAGE, per))

    # 先看缓存：同一组条件 10 分钟内不重复打 GitHub（缓存键按「用户点的那个窗口」算）
    key = 'trend|%s|%s|%s|%s|%d' % (win, lang, field, sort, per)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        cached = dict(hit[1])
        cached['cached'] = True
        return JSONResponse(cached)

    # 时间窗里一条都没捞到时自动放宽一档，别让用户点进去看空列表
    chain = [win] + [w for w in ('d1', 'd3', 'd7', 'd30', 'd90') if TREND_DAYS[w] > days]
    fetch = max(per, min(MAX_PER_PAGE, per * TREND_FETCH_X))
    query, used, widened, data, meta = '', win, False, None, {}
    for cand in chain:
        query = _trend_query(TREND_DAYS[cand], lang, field)
        data, err, meta = _github(query, fetch)
        if data is None:
            return JSONResponse({'ok': False, 'msg': err, 'remaining': meta.get('remaining', '')})
        if data.get('items'):
            used, widened = cand, cand != win
            break
        used = cand
    days = TREND_DAYS[used]

    repos, seen = [], set()
    for item in data.get('items') or []:
        if not isinstance(item, dict):
            continue
        row = _norm(item)
        if not row['name'] or row['name'] in seen:
            continue
        seen.add(row['name'])
        age = row['age'] if row['age'] and row['age'] > 0 else 1
        row['per_day'] = round(row['stars'] / float(age), 1)
        repos.append(row)

    if sort == 'stars':
        repos.sort(key=lambda r: (r['stars'], r['per_day']), reverse=True)
    else:
        repos.sort(key=lambda r: (r['per_day'], r['stars']), reverse=True)
    repos = repos[:per]

    out = {
        'ok': True, 'q': query, 'win': used, 'asked': win, 'widened': widened,
        'days': days, 'sort': sort, 'field': field, 'lang': lang,
        'total': int(data.get('total_count') or 0), 'count': len(repos), 'repos': repos,
        'remaining': meta.get('remaining', ''), 'cached': False, 'token': bool(_gh_token()),
    }
    _CACHE[key] = (now + CACHE_TTL, out)
    return JSONResponse(out)


def _do_digest(payload):
    rows = [r for r in (payload.get('repos') or []) if isinstance(r, dict)][:DIGEST_MAX]
    if not rows:
        return JSONResponse({'ok': False, 'msg': '先搜出仓库再来总结。'})
    if not llm_key():
        return JSONResponse({'ok': False, 'msg': '服务端没配模型 Key，中文解读用不了。'})

    lines = []
    for i, row in enumerate(rows):
        lines.append('%d. 名称：%s' % (i + 1, _clean(row.get('name'), 80)))
        lines.append('   作者描述：%s' % (_clean(row.get('desc'), 200) or '（作者没写描述）'))
        lines.append('   语言：%s ｜ star：%s ｜ 最近更新：%s（算下来：%s）'
                     % (_clean(row.get('lang'), 20) or '未标注',
                        row.get('stars') or 0,
                        _clean(row.get('pushed'), 10) or '未知',
                        _clean(row.get('activity'), 8) or '未知'))
        if row.get('per_day'):
            lines.append('   涨速：创建于 %s，约每天 %s 个 star'
                         % (_clean(row.get('created'), 10) or '未知', row.get('per_day')))
        topics = row.get('topics') or []
        if topics:
            lines.append('   话题标签：%s' % ', '.join(_clean(t, 24) for t in topics[:6]))
        lines.append('   是否已归档：%s' % ('是' if row.get('archived') else '否'))

    content, err = _chat(DIGEST_SYS, '这批仓库是：\n' + '\n'.join(lines), 1800, 0.4)
    data = _extract_json(content) if content else {}
    got = data.get('items') if isinstance(data.get('items'), list) else []

    items = []
    for i in range(len(rows)):
        row = got[i] if i < len(got) and isinstance(got[i], dict) else {}
        items.append({
            'what': _clean(row.get('what'), 60),
            'use': _clean(row.get('use'), 70),
            'fit': _clean(row.get('fit'), 50),
        })
    filled = sum(1 for x in items if x['what'])
    return JSONResponse({'ok': True, 'items': items, 'insight': _clean(data.get('insight'), 120),
                         'source': 'ai' if filled else 'none'})


# =========================================================
# 路由
# =========================================================
@router.get('/opc', response_class=HTMLResponse)
def opc_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f1117;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/opc.html 存在。</p>'
                '</body></html>')
    return HTMLResponse(html)


async def _payload(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@router.get('/opc/api/status')
def opc_status():
    """前端用它决定显示「AI 解读」按钮还是提示没配 Key；顺带把趋势榜的筛选项带过去。"""
    return JSONResponse({
        'ai': bool(llm_key()),
        'github_token': bool(_gh_token()),
        'trend': {
            'windows': [{'key': k, 'label': lab} for k, lab, _d in TREND_WINDOWS],
            'fields': [{'key': k, 'label': lab} for k, lab, _q in TREND_FIELDS],
            'langs': TREND_LANGS,
            'min_stars': TREND_MIN_STARS,
        },
    })


@router.post('/opc/api/search')
async def opc_search(request: Request):
    return await run_in_threadpool(_do_search, await _payload(request))


@router.post('/opc/api/trending')
async def opc_trending(request: Request):
    return await run_in_threadpool(_do_trending, await _payload(request))


@router.post('/opc/api/digest')
async def opc_digest(request: Request):
    return await run_in_threadpool(_do_digest, await _payload(request))


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='OPC 资讯', description='一人公司 / 独立开发，GitHub 上值得看的项目',
                          version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8011)
