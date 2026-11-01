# -*- coding: utf-8 -*-
# 专注台（番茄钟升级版）
# ------------------------------------------------------------------
# 不是「再做一个 25 分钟倒计时」。工具箱里已经有目标拆解器、读书落地这些
# 「想清楚 / 拆到能动手」的工具，缺的是最后一步：真的坐下来做完。
#
# 所以这一页只干三件事：
#   ① 把别处拆出来的动作接进来：/focus?rid=<目标 id>&aid=<动作 id> 深链，
#      任务、产出、预估分钟直接带进计时器；
#   ② 计时本身可靠：按时间戳算，切标签页、刷新、锁屏都不会少走；
#      暂停、提前完成、放弃、记打断，都是显式状态；
#   ③ 做完之后写回：实际分钟加到目标动作的 actual 上，可选标成完成，
#      并留一条记录，让「实际投入」不再只靠自报。
#
# 一条完成的专注 = history 里的一行（tool='focus'）。
# 进行中的那段只存在浏览器 localStorage，不往服务器写半成品。
#
#   GET    /focus                    页面
#   GET    /focus/api/status         今日 / 近七天统计 + 最近记录
#   GET    /focus/api/sources        目标拆解器里今天该做的动作
#   GET    /focus/api/sessions       专注记录
#   POST   /focus/api/sessions       保存一段专注（可选回写目标）
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

import storage

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'focus.html'

TOOL = 'focus'
LIST_MAX = 60
TASK_MAX = 120
INTENT_MAX = 200
NOTE_MAX = 600
NEXT_MAX = 200
INTR_MAX = 20
INTR_TEXT_MAX = 80
PLANNED_MAX = 180
ACTUAL_MAX = 1440
BREAK_MAX = 60
SOURCE_TOOLS = ('goal-split', 'reading-practice')
TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')


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


def _int(value, lo: int, hi: int, default: int = 0) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _ts(value) -> str:
    """只认 YYYY-MM-DD HH:MM:SS；前端传 ISO 的 T 也接住。"""
    s = str(value or '').strip().replace('T', ' ')[:19]
    if len(s) == 16 and s[10] == ':':
        s += ':00'
    return s if TS_RE.match(s) else ''


def _today() -> str:
    return date.today().isoformat()


def _day_of(s: str) -> str:
    s = str(s or '')
    return s[:10] if re.match(r'^\d{4}-\d{2}-\d{2}', s) else ''


def _hm(minutes: int) -> str:
    minutes = int(minutes or 0)
    if minutes < 60:
        return '%d 分钟' % minutes
    return ('%.1f' % (minutes / 60.0)).rstrip('0').rstrip('.') + ' 小时'


# =============================================================
# 规范化
# =============================================================
def _norm_interruptions(raw) -> list:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[-INTR_MAX:]:
        if isinstance(item, str):
            text, at = _clean(item, INTR_TEXT_MAX), ''
        elif isinstance(item, dict):
            text = _clean(item.get('text'), INTR_TEXT_MAX)
            at = _ts(item.get('at'))
        else:
            continue
        if text:
            out.append({'text': text, 'at': at})
    return out


def _norm_session(raw: dict) -> dict:
    task = _clean(raw.get('task'), TASK_MAX)
    status = 'abandoned' if raw.get('status') == 'abandoned' else 'completed'
    planned = _int(raw.get('planned_min'), 1, PLANNED_MAX, 25)
    actual = _int(raw.get('actual_min'), 0, ACTUAL_MAX, planned)
    if status == 'completed' and actual < 1:
        actual = planned

    source_tool = raw.get('source_tool')
    source_tool = source_tool if source_tool in SOURCE_TOOLS else ''
    source_id = _int(raw.get('source_id'), 0, 10 ** 9, 0)
    if not source_id:
        source_tool = ''
    source_action_id = ''
    if source_tool == 'goal-split':
        source_action_id = _clean(raw.get('source_action_id'), 24)

    started = _ts(raw.get('started_at'))
    ended = _ts(raw.get('ended_at'))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if not ended:
        ended = now
    if not started:
        started = ended

    return {
        'version': 1,
        'task': task,
        'intent': _clean(raw.get('intent'), INTENT_MAX),
        'status': status,
        'planned_min': planned,
        'actual_min': actual,
        'break_min': _int(raw.get('break_min'), 0, BREAK_MAX, 5),
        'source_tool': source_tool,
        'source_id': source_id if source_tool else 0,
        'source_action_id': source_action_id,
        'source_title': _clean(raw.get('source_title'), TASK_MAX) if source_tool else '',
        'interruptions': _norm_interruptions(raw.get('interruptions')),
        'note': _clean(raw.get('note'), NOTE_MAX),
        'next_step': _clean(raw.get('next_step'), NEXT_MAX),
        'started_at': started,
        'ended_at': ended,
        'client_id': _clean(raw.get('client_id'), 64),
    }


def _row_session(row: dict) -> dict:
    data = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    return {
        'id': row.get('id'),
        'task': _clean(data.get('task'), TASK_MAX) or '（没写内容）',
        'intent': _clean(data.get('intent'), INTENT_MAX),
        'status': 'abandoned' if data.get('status') == 'abandoned' else 'completed',
        'planned_min': _int(data.get('planned_min'), 0, PLANNED_MAX, 0),
        'actual_min': _int(data.get('actual_min'), 0, ACTUAL_MAX, 0),
        'break_min': _int(data.get('break_min'), 0, BREAK_MAX, 0),
        'source_tool': data.get('source_tool') if data.get('source_tool') in SOURCE_TOOLS else '',
        'source_id': _int(data.get('source_id'), 0, 10 ** 9, 0),
        'source_action_id': _clean(data.get('source_action_id'), 24),
        'source_title': _clean(data.get('source_title'), TASK_MAX),
        'interruptions': _norm_interruptions(data.get('interruptions')),
        'note': _clean(data.get('note'), NOTE_MAX),
        'next_step': _clean(data.get('next_step'), NEXT_MAX),
        'started_at': _ts(data.get('started_at')),
        'ended_at': _ts(data.get('ended_at')),
        'client_id': _clean(data.get('client_id'), 64),
        'created_at': str(row.get('created_at') or ''),
    }


def _title(plan: dict) -> str:
    task = plan.get('task') or ''
    if task:
        return task[:120]
    return '专注 %d 分钟' % (plan.get('planned_min') or 25)


# =============================================================
# 统计：今日 / 近七天 / 连续天数 / 最近记录
# =============================================================
def _stats(user: str) -> dict:
    rows = storage.list_records(TOOL, user, 500)
    sessions = [_row_session(r) for r in rows]
    today = _today()

    day_rows = {}
    for s in sessions:
        d = _day_of(s.get('started_at')) or _day_of(s.get('created_at'))
        if not d:
            continue
        bucket = day_rows.setdefault(d, {'minutes': 0, 'completed': 0, 'abandoned': 0})
        bucket['minutes'] += s['actual_min']
        if s['status'] == 'completed':
            bucket['completed'] += 1
        else:
            bucket['abandoned'] += 1

    days = []
    today_d = date.today()
    weekdays = '一二三四五六日'
    for i in range(6, -1, -1):
        d = (today_d - timedelta(days=i)).isoformat()
        b = day_rows.get(d, {'minutes': 0, 'completed': 0, 'abandoned': 0})
        days.append({
            'date': d,
            'label': '周' + weekdays[date.fromisoformat(d).weekday()],
            'minutes': b['minutes'],
            'completed': b['completed'],
            'abandoned': b['abandoned'],
            'today': d == today,
        })
    max_min = max([x['minutes'] for x in days] + [1])

    # 连续天数：从今天往回数；今天还没专注时，从昨天开始数，别让「今天还没开始」把连续打回 0
    done_days = {d for d, b in day_rows.items() if b['completed'] > 0}
    cursor = today_d
    if cursor.isoformat() not in done_days:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor.isoformat() in done_days:
        streak += 1
        cursor -= timedelta(days=1)

    td = day_rows.get(today, {'minutes': 0, 'completed': 0, 'abandoned': 0})
    week = days
    week_minutes = sum(x['minutes'] for x in week)
    week_completed = sum(x['completed'] for x in week)
    last_next = ''
    for s in sessions:
        if s.get('next_step'):
            last_next = s['next_step']
            break

    return {
        'today': today,
        'today_minutes': td['minutes'],
        'today_completed': td['completed'],
        'today_abandoned': td['abandoned'],
        'week_minutes': week_minutes,
        'week_completed': week_completed,
        'week_days': len([x for x in week if x['minutes'] > 0]),
        'streak': streak,
        'days': days,
        'max_minutes': max_min,
        'last_next_step': last_next,
        'recent': sessions[:12],
        'total_sessions': len(sessions),
    }


# =============================================================
# 来源：目标拆解器里今天该做的动作
# =============================================================
def _goal_sources(user: str) -> list:
    rows = storage.list_records('goal-split', user, 80)
    today = _today()
    items = []
    for row in rows:
        data = row.get('payload') if isinstance(row.get('payload'), dict) else {}
        goal_title = _clean(data.get('statement') or data.get('raw') or row.get('title'), 80)
        deadline = _clean(data.get('deadline'), 10)
        for idx, a in enumerate(data.get('actions') or []):
            if not isinstance(a, dict) or a.get('done'):
                continue
            aid = _clean(a.get('id'), 24)
            if not aid:
                continue
            on = _clean(a.get('on'), 10)
            if on and on > today:
                continue
            overdue = 0
            if on:
                try:
                    overdue = max(0, (date.fromisoformat(today) - date.fromisoformat(on)).days)
                except ValueError:
                    overdue = 0
            minutes = _int(a.get('min'), 5, PLANNED_MAX, 25)
            items.append({
                'rid': row.get('id'),
                'aid': aid,
                'task': _clean(a.get('text'), TASK_MAX) or '（这条动作没写内容）',
                'out': _clean(a.get('out'), INTENT_MAX),
                'min': minutes,
                'on': on,
                'overdue': overdue,
                'goal_title': goal_title,
                'deadline': deadline,
                'index': idx,
            })
    items.sort(key=lambda x: (0 if x['overdue'] > 0 else 1, x['on'] or '9999-12-31', x['goal_title']))
    return items[:30]


# =============================================================
# 路由
# =============================================================
@router.get('/focus', response_class=HTMLResponse)
def focus_page() -> HTMLResponse:
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="font-family:system-ui"><h2>模板文件缺失</h2>'
                '<p>请确认 templates/focus.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.get('/focus/api/status')
def focus_status(request: Request) -> JSONResponse:
    try:
        data = _stats(_user(request))
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'stats': data})


@router.get('/focus/api/sources')
def focus_sources(request: Request) -> JSONResponse:
    try:
        items = _goal_sources(_user(request))
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': items})


@router.get('/focus/api/sessions')
def focus_sessions(request: Request) -> JSONResponse:
    try:
        rows = storage.list_records(TOOL, _user(request), LIST_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': [_row_session(r) for r in rows]})


@router.post('/focus/api/sessions')
async def focus_save(request: Request) -> JSONResponse:
    payload = await _body(request)
    session = _norm_session(payload)
    if not session['task']:
        return JSONResponse({'ok': False, 'message': '先写这段时间要做什么。'}, status_code=400)
    user = _user(request)
    try:
        # 同一个 client_id 重复提交（网络重试 / 手滑点两次）只落一条，也不重复回写分钟
        if session['client_id']:
            for row in storage.list_records(TOOL, user, 100):
                data = row.get('payload') if isinstance(row.get('payload'), dict) else {}
                if data.get('client_id') and data.get('client_id') == session['client_id']:
                    return JSONResponse({'ok': True, 'item': _row_session(row), 'duplicate': True,
                                         'stats': _stats(user)})
        rec = storage.add_record(TOOL, user, _title(session), session)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)

    writeback = None
    mark_done = bool(payload.get('mark_done')) and session['status'] == 'completed'
    if session['source_tool'] == 'goal-split' and session['source_id'] and session['source_action_id']:
        if session['actual_min'] > 0 or mark_done:
            try:
                # 延迟导入：goal_split 不反向依赖 focus，这里只是为了把写回逻辑放在目标那一侧
                from goal_split import apply_focus
                writeback = apply_focus(user, session['source_id'], session['source_action_id'],
                                        session['actual_min'], mark_done=mark_done,
                                        note=session.get('note') or '')
            except Exception as exc:  # 目标被删了 / 写库失败，都不该让这段专注白记
                writeback = {'ok': False, 'message': str(exc)[:120]}

    try:
        stats = _stats(user)
    except storage.StorageUnavailable:
        stats = None
    return JSONResponse({'ok': True, 'item': _row_session(rec), 'writeback': writeback, 'stats': stats})
