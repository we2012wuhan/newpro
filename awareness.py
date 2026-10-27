# -*- coding: utf-8 -*-
# 觉察 P1：只做「可核对的统计」，不调模型。
#   GET  /api/awareness         本周足迹 / 主题 / 徽标状态
#   POST /api/awareness/seen    标记已读（daily / weekly / all）
#   POST /api/awareness/mute    这周不提醒
#   POST /api/usage/hit         页面打开时记一笔（同页 5 分钟内去重）
import datetime
from collections import Counter

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import storage
from voice_nav import TOOLS

router = APIRouter()

MIN_EVENTS = 8      # 少于这么多条，不下观察结论
MIN_DAYS = 3        # 少于这么多天，也不下结论
PERIODS = [('深夜', 0, 6), ('上午', 6, 12), ('下午', 12, 18), ('晚上', 18, 24)]

# 主题词表：够用、可解释、不加分词依赖
HISTORY_NAMES = {
    'five_why': '五问分析', 'socratic': '苏格拉底提问', 'first_principles': '第一性原理',
    'bill_analysis': '账单分析', 'judgement-trainer': '判断力教练', 'predict-diary': '贝叶斯日记',
    'langchain-test': 'LangChain 测试', 'reading-practice': '读书落地', 'kolb': '库博学习圈',
    'goal-split': '目标拆解器', 'rumination': '内耗拆解', 'writing-ideas': '写作选题',
    'ocr': 'OCR 图片识别', 'study-assistant': '学习模型助手', 'ai-chart': 'AI 生成图表',
    'opc': 'OPC 资讯', 'douyin': '抖音下载器', 'ask-hub': '提问三件套',
    'design-assistant': '页面设计助手', 'document-generator': '文件生成',
}

THEMES = {
    '工作': ['工作', '同事', '老板', '加班', '职场', '项目'],
    '沟通': ['沟通', '表达', '说话', '交流'],
    '英语': ['英语', '英文', '翻译', '口语'],
    '学习': ['学习', '看书', '读书', '知识'],
    '账单': ['账单', '消费', '省钱', '支出'],
    '写作': ['写作', '选题', '文章'],
    '目标': ['目标', '计划', '拆解'],
    '情绪': ['内耗', '焦虑', '情绪', '烦躁'],
    '决策': ['判断', '选择', '决策'],
    '开源': ['开源', 'github', 'opc'],
    '视频': ['视频', '抖音', '剪辑'],
}


QUESTIONS = {
    '/five-why': '这周反复拆的那件事，今晚能做的最小一步是什么？',
    '/socratic': '这周你问自己最多的那个问题，真正想确认的是什么？',
    '/reading-practice': '这周记下的那件事，哪天可以真正试一次？',
    '/goal-split': '这周拆出来的目标，哪一个可以先推进 10 分钟？',
    '/rumination': '这周反复出现的那个念头，有没有可能只是累了？',
    '/bill-analysis': '这周花得最多的地方，值不值得下个月继续？',
    '/translator': '这周练的英语，什么时候真正用一次？',
    '/opc': '这周看到的开源项目，哪个值得真的跑起来？',
    '/voice-chat': '这周说出口的想法，有没有哪一句值得写下来？',
    '/design-assistant': '这周设计的页面，哪一个决定是来自真实需求，而不是先想好的样子？',
}
DEFAULT_QUESTION = '这周你最想推进的，是哪一件？'


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


def _names() -> dict:
    return {t['path']: t['name'] for t in TOOLS}


def _themes(records: list) -> list:
    parts = []
    for r in records:
        parts.append(str(r.get('title') or ''))
        p = r.get('payload') if isinstance(r.get('payload'), dict) else {}
        for k in ('question', 'problem', 'topic', 'goal', 'title'):
            if isinstance(p.get(k), str):
                parts.append(p[k][:200])
    text = ' '.join(parts).lower()
    hits = {}
    for label, words in THEMES.items():
        n = sum(text.count(w) for w in words)
        if n >= 2:
            hits[label] = n
    return [k for k, _ in sorted(hits.items(), key=lambda x: (-x[1], x[0]))]


def _weekly(user: str, week: str, enough: bool, visits: int, active_days: int,
            tools: list, top_period: str, top_share: float, themes: list, recent: list) -> dict:
    """P2a：纯规则的「这周想问自己」。同一周只生成一次并缓存。"""
    if not enough:
        return {'sufficient': False}
    try:
        cached = storage.weekly_get(user, week)
    except storage.StorageUnavailable:
        cached = None
    if cached and isinstance(cached.get('payload'), dict) and cached['payload'].get('question'):
        out = dict(cached['payload'])
        out['sufficient'] = True
        out['source'] = cached.get('source') or 'rules'
        out['generated_at'] = str(cached.get('created_at') or '')[:16]
        return out

    observations = []
    if visits:
        observations.append('这周来了 %d 次，分布在 %d 天' % (visits, active_days))
    if tools:
        observations.append('最常用：' + ' · '.join('%s %d 次' % (t['name'], t['n']) for t in tools[:3]))
    if top_period:
        if top_share >= 0.5:
            observations.append('一半以上的打开发生在%s' % top_period)
        else:
            observations.append('你最常在%s打开工具箱' % top_period)
    if themes:
        observations.append('反复出现：' + ' · '.join(themes[:3]))
    observations = observations[:3]

    top_tool = tools[0]['tool'] if tools else ''
    payload = {
        'sufficient': True,
        'observations': observations,
        'question': QUESTIONS.get(top_tool) or DEFAULT_QUESTION,
        'evidence': [{'name': r.get('name') or r.get('tool'), 'title': r.get('title') or '',
                      'at': str(r.get('at') or '')[:10]} for r in recent[:3]],
        'source': 'rules',
    }
    try:
        row = storage.weekly_set(user, week, payload, 'rules')
        payload['generated_at'] = str(row.get('created_at') or '')[:16]
    except storage.StorageUnavailable:
        payload['generated_at'] = ''
    return payload


def _build(user: str) -> dict:
    now = datetime.datetime.now()
    day = now.strftime('%Y-%m-%d')
    week = now.strftime('%G-W%V')
    since = (now - datetime.timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    names = _names()

    events = storage.usage_since(user, since)
    visits = len(events)
    active_days = len({str(e.get('created_at') or '')[:10] for e in events if e.get('created_at')})
    counts = Counter(str(e.get('tool') or '') for e in events)

    days = []
    for i in range(6, -1, -1):
        d = (now - datetime.timedelta(days=i)).strftime('%Y-%m-%d')
        days.append({'date': d, 'n': sum(1 for e in events if str(e.get('created_at') or '')[:10] == d)})

    period_counts = Counter()
    for e in events:
        try:
            hour = int(str(e.get('created_at') or '')[11:13])
        except ValueError:
            continue
        for name, a, b in PERIODS:
            if a <= hour < b:
                period_counts[name] += 1
                break
    top_period = period_counts.most_common(1)[0][0] if period_counts else ''
    top_share = (period_counts.most_common(1)[0][1] / float(visits)) if period_counts and visits else 0

    tools = [{'tool': t, 'name': names.get(t, t), 'n': n} for t, n in counts.most_common(6)]

    records = storage.list_records_all(user, 300)
    themes = _themes(records)[:4]
    recent = [{'tool': r.get('tool'),
               'name': names.get(r.get('tool')) or HISTORY_NAMES.get(r.get('tool')) or r.get('tool'),
               'title': str(r.get('title') or '')[:80], 'at': str(r.get('created_at') or '')}
              for r in records[:5]]

    enough = visits >= MIN_EVENTS and active_days >= MIN_DAYS
    candidates = []
    if enough:
        if tools:
            candidates.append('这周最常用的是「%s」，%d 次' % (tools[0]['name'], tools[0]['n']))
        if top_period:
            if top_share >= 0.5:
                candidates.append('%d 次里有一半以上发生在%s' % (visits, top_period))
            else:
                candidates.append('你最常在%s打开工具箱' % top_period)
        candidates.append('这周来了 %d 次，分布在 %d 天' % (visits, active_days))
        if themes:
            candidates.append('最近反复出现的主题：' + ' · '.join(themes[:3]))
    fact = candidates[now.toordinal() % len(candidates)] if candidates else ''

    facts = []
    if visits:
        facts.append('来了 %d 次，活跃 %d 天' % (visits, active_days))
    if tools:
        facts.append('最常用：' + ' · '.join('%s %d' % (t['name'], t['n']) for t in tools[:3]))
    if top_period:
        facts.append('最常来：' + top_period)
    if themes:
        facts.append('最近主题：' + ' · '.join(themes[:3]))

    weekly = _weekly(user, week, enough, visits, active_days, tools, top_period, top_share, themes, recent)

    state = storage.awareness_state(user)
    muted = bool(state.get('mute_until')) and state['mute_until'] >= day
    weekly_unread = bool(weekly.get('sufficient') and state.get('seen_weekly') != week and not muted)
    daily_unread = bool(fact and state.get('seen_daily') != day and not muted)
    badge = 'weekly' if weekly_unread else ('daily' if daily_unread else '')
    unread_count = int(weekly_unread) + int(daily_unread)

    return {
        'ok': True, 'generated_at': now.strftime('%H:%M'), 'day': day, 'week': week,
        'enough': enough, 'visits': visits, 'active_days': active_days,
        'days': days, 'tools': tools, 'themes': themes, 'recent': recent,
        'fact': fact, 'facts': facts, 'weekly': weekly,
        'mute_until': state.get('mute_until') or '',
        'badge': badge, 'unread_count': unread_count,
        'unread': {'weekly': weekly_unread, 'daily': daily_unread},
    }


async def _payload(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@router.get('/api/awareness')
async def awareness_get(request: Request):
    user = _user(request)
    try:
        return JSONResponse(await run_in_threadpool(_build, user))
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)})


@router.post('/api/awareness/seen')
async def awareness_seen(request: Request):
    payload = await _payload(request)
    kind = str(payload.get('kind') or 'all')
    now = datetime.datetime.now()
    args = {}
    if kind in ('daily', 'all'):
        args['seen_daily'] = now.strftime('%Y-%m-%d')
    if kind in ('weekly', 'all'):
        args['seen_weekly'] = now.strftime('%G-W%V')
    try:
        state = await run_in_threadpool(lambda: storage.awareness_mark(_user(request), **args))
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)})
    return JSONResponse({'ok': True, 'state': state})


@router.post('/api/awareness/mute')
async def awareness_mute(request: Request):
    payload = await _payload(request)
    try:
        days = max(1, min(30, int(payload.get('days') or 7)))
    except (TypeError, ValueError):
        days = 7
    until = (datetime.datetime.now() + datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    try:
        await run_in_threadpool(lambda: storage.awareness_mark(_user(request), mute_until=until))
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)})
    return JSONResponse({'ok': True, 'mute_until': until})


@router.post('/api/usage/hit')
async def usage_hit(request: Request):
    payload = await _payload(request)
    tool = str(payload.get('tool') or '').strip()[:60]
    if not tool.startswith('/'):
        return JSONResponse({'ok': False, 'msg': 'tool 必须是以 / 开头的路径'})
    try:
        wrote = await run_in_threadpool(storage.log_usage, _user(request), tool, 'open')
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)})
    return JSONResponse({'ok': True, 'recorded': bool(wrote)})
