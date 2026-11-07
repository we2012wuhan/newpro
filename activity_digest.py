# -*- coding: utf-8 -*-
# 使用复盘摘要：把最近一段时间的页面足迹和历史记录压成一段可读文本。
# ------------------------------------------------------------
# 只给 AI 助手「复盘 / 训练 / 计划」场景用，不单独对外提供接口。
# 设计约束：
#   - 只读当前账号的数据，不跨用户；
#   - 只取标题、摘要和少量详情，不把完整 payload / 长对话塞给模型；
#   - 长度有硬上限，超出按行截断；
#   - 页面足迹（/focus）和历史工具键（focus）分开统计，避免混算。
from __future__ import annotations

import datetime
from collections import Counter

import overview
import storage
from awareness import HISTORY_NAMES
from voice_nav import TOOLS

DIGEST_MAX = 4500
HISTORY_LIMIT = 1000
RECENT_ROWS = 10
DETAIL_LINES = 2
OPEN_ITEMS = 8
TOP_PATHS = 5
TOP_TOOLS = 6

PERIODS = [('深夜', 0, 6), ('上午', 6, 12), ('下午', 12, 18), ('晚上', 18, 24)]

PATH_NAMES = {str(t.get('path') or ''): str(t.get('name') or '') for t in TOOLS}

EXTRA_NAMES = {
    'image-studio': '图片工坊',
    'sleep-audio': '睡前打烊',
    'voice-profile': '实时语音',
    'voice-chat': '实时语音',
}


def _text(value, limit=120):
    text = str(value or '').replace('\r', ' ').replace('\n', ' ').strip()
    text = ' '.join(text.split())
    return text[:limit]


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _date(value):
    return str(value or '')[:10]


def _period(value):
    try:
        hour = int(str(value or '')[11:13])
    except ValueError:
        return ''
    for name, start, end in PERIODS:
        if start <= hour < end:
            return name
    return ''


def _path_name(path):
    return PATH_NAMES.get(str(path or '')) or str(path or '未知页面')


def _tool_name(tool):
    meta = overview.TOOL_META.get(str(tool or '')) or {}
    return (meta.get('name') or HISTORY_NAMES.get(str(tool or ''))
            or EXTRA_NAMES.get(str(tool or '')) or str(tool or '未知工具'))


def _title(item):
    return _text(item.get('title') or item.get('excerpt') or '（没有标题）', 80)


def _cut(text, limit):
    """按行截断，避免把半句话截给模型。"""
    text = str(text or '')
    if len(text) <= limit:
        return text
    suffix = '\n（摘要已截断）'
    room = max(0, limit - len(suffix))
    cut = text[:room]
    pos = cut.rfind('\n')
    if pos >= room * 0.6:
        cut = cut[:pos]
    return cut + suffix


def _item_for(row):
    """优先复用 /overview 的口径；未知工具只保留标题，不碰 payload。"""
    item = overview._item(row)
    if item:
        # 脚本库没写标题时，/overview 会退回展示命令；摘要里不暴露命令正文。
        if item.get('tool') == 'script-library' and not _text(row.get('title')):
            item['title'] = '（没有写描述）'
            item['excerpt'] = ''
        return item
    tool = str(row.get('tool') or '')
    return {
        'tool': tool,
        'name': _tool_name(tool),
        'title': _text(row.get('title'), 80),
        'excerpt': '',
        'created_at': str(row.get('created_at') or ''),
        'status': '',
        'open': False,
        'tracked': False,
        'detail': [],
    }


def build_digest(user: str, days: int = 7, now=None, scrub=None) -> str:
    """返回一段中文摘要。数据不足时也返回可读说明，不抛异常。"""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7
    if days not in (7, 14, 30):
        days = 7

    now = now or datetime.datetime.now()
    since_dt = now - datetime.timedelta(days=days)
    since = since_dt.strftime('%Y-%m-%d %H:%M:%S')

    events = storage.usage_since(user, since)
    rows = storage.list_records_since(user, since, HISTORY_LIMIT)
    items = [_item_for(row) for row in rows]

    # 页面足迹
    visit_counts = Counter(str(e.get('tool') or '') for e in events)
    active_days = len({_date(e.get('created_at')) for e in events if e.get('created_at')})
    period_counts = Counter()
    for event in events:
        period = _period(event.get('created_at'))
        if period:
            period_counts[period] += 1

    # 历史记录
    tool_counts = Counter(str(row.get('tool') or '') for row in rows)
    focus_rows = [row for row in rows if str(row.get('tool') or '') == 'focus']
    focus_minutes = 0.0
    focus_done_minutes = 0.0
    focus_done = 0
    focus_abandoned = 0
    for row in focus_rows:
        payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
        minutes = max(0.0, _num(payload.get('actual_min')))
        focus_minutes += minutes
        if str(payload.get('status') or '') == 'abandoned':
            focus_abandoned += 1
        else:
            focus_done += 1
            focus_done_minutes += minutes

    open_items = [item for item in items if item.get('open')]
    recent_items = items[:RECENT_ROWS]

    lines = ['【最近 %d 天活动摘要（%s ~ %s，仅当前账号）】' % (
        days, since[:10], now.strftime('%Y-%m-%d'))]

    if len(events) < 8 or len(rows) < 3:
        lines.append('提示：最近记录较少，结论只能作为参考，不要过度推断。')

    if events:
        bits = ['打开工具 %d 次，活跃 %d 天' % (len(events), active_days)]
        periods = ['%s %d 次' % (name, period_counts[name])
                   for name, _start, _end in PERIODS if period_counts.get(name)]
        if periods:
            bits.append('时段：' + '、'.join(periods))
        lines.append('足迹：' + '；'.join(bits))
        top_paths = [(path, n) for path, n in visit_counts.most_common(TOP_PATHS) if path]
        if top_paths:
            lines.append('打开最多：' + ' · '.join(
                '%s %d 次' % (_path_name(path), n) for path, n in top_paths))
    else:
        lines.append('足迹：最近没有页面打开记录。')

    if rows:
        lines.append('记录：新增 %d 条历史记录。' % len(rows))
        top_tools = [(tool, n) for tool, n in tool_counts.most_common(TOP_TOOLS) if tool]
        if top_tools:
            lines.append('记录最多：' + ' · '.join(
                '%s %d 条' % (_tool_name(tool), n) for tool, n in top_tools))
        if len(rows) >= HISTORY_LIMIT:
            lines.append('注：记录数量达到 %d 条上限，更早的条目未计入。' % HISTORY_LIMIT)
    else:
        lines.append('记录：最近没有新增历史记录。')

    if focus_rows:
        avg = (focus_done_minutes / focus_done) if focus_done else 0
        lines.append('专注：%d 段，共 %d 分钟；完成 %d 段，中断 %d 段%s。' % (
            len(focus_rows), int(round(focus_minutes)), focus_done, focus_abandoned,
            ('，完成段平均 %d 分钟' % int(round(avg))) if focus_done else ''))

    if open_items:
        lines.append('还没收尾：')
        for item in open_items[:OPEN_ITEMS]:
            bits = [_date(item.get('created_at')), item.get('name') or _tool_name(item.get('tool')),
                    _title(item)]
            if item.get('status'):
                bits.append(item['status'])
            lines.append('- ' + '｜'.join([b for b in bits if b]))

    if recent_items:
        lines.append('最近记录：')
        for item in recent_items:
            bits = [_date(item.get('created_at')),
                    item.get('name') or _tool_name(item.get('tool')), _title(item)]
            line = '- ' + '｜'.join([b for b in bits if b])
            if item.get('status'):
                line += '（%s）' % item['status']
            lines.append(line)
            details = item.get('detail') if isinstance(item.get('detail'), list) else []
            for detail in details[:DETAIL_LINES]:
                detail = _text(detail, 80)
                if detail:
                    lines.append('  ' + detail)

    if not events and not rows:
        lines.append('过去 %d 天没有可用的使用记录或历史记录。' % days)

    text = '\n'.join(lines)
    if scrub:
        text = scrub(text)
    return _cut(text, DIGEST_MAX)
