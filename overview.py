# -*- coding: utf-8 -*-
# 记录总览（页面在 /overview）
# ------------------------------------------------------------
# 各工具的历史记录本来就都落在 history 表里，用 tool 字段区分，
# 但分散在各自的页面右侧，跨工具看不见。这一页把它们汇到一处：
# 按时间倒序摊开、能按工具筛、能搜关键词、能只看「还没收尾」的。
#
# 定位是「只读汇总」——不新增任何记录入口，写数据仍然是各工具自己的事。
# 也正因为如此，这一页不会变成第二个记录坟场：它不鼓励你多写，只帮你回看。
#
#   GET /overview            页面
#   GET /overview/api/items  汇总数据（每条带状态和展开细节）
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

import storage

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE = _BASE_DIR / 'templates' / 'overview.html'

ITEM_MAX = 300          # 一次最多汇总多少条

# 表里出现的 tool 值必须在这里，不在的直接跳过（宁可少显示，也不要显示成空白卡片）。
# 注意几个工具的历史命名不统一：five_why / socratic 是下划线，其余是短横线。
# deep=True 表示这个工具的页面认 `?r=<记录 id>`，能从这一页直接跳到那一条。
# 将来哪个工具真的没有可回看的记录，把它标成 False，就不要「直接打开这一条」了，免得点了没反应。
TOOL_META = {
    'judgement-trainer': {'name': '判断力教练', 'ico': '🎯', 'href': '/judgement-trainer', 'deep': True},
    # five_why 和 socratic 已经并到一页了（/ask-hub），但库里始终是两个 tool 名，各存各的。
    # 所以这里保持两条、分开筛；href 指到合并页并把 tab 带上，点进去正好落在那一页。
    'five_why': {'name': '5Why 分析法', 'ico': '🔎', 'href': '/ask-hub?tab=why', 'deep': True},
    'socratic': {'name': '苏格拉底提问', 'ico': '💬', 'href': '/ask-hub?tab=soc', 'deep': True},
    'study-assistant': {'name': '学习模型助手', 'ico': '🧠', 'href': '/study-assistant', 'deep': True},
    'reading-practice': {'name': '读书落地', 'ico': '📖', 'href': '/reading-practice', 'deep': True},
    'kolb': {'name': '库博学习圈', 'ico': '🔄', 'href': '/kolb', 'deep': True},
    'ai-chart': {'name': 'AI 生成图表', 'ico': '📊', 'href': '/ai-chart', 'deep': True},
    'script-library': {'name': '脚本库', 'ico': '📜', 'href': '/script-library', 'deep': True},
    'goal-split': {'name': '目标拆解器', 'ico': '🎯', 'href': '/goal-split', 'deep': True},
    'site-collect': {'name': '网站收集', 'ico': '🔖', 'href': '/site-collect', 'deep': True},
    'langchain-test': {'name': 'LangChain 测试', 'ico': '🧪', 'href': '/langchain-test', 'deep': True},
    'first_principles': {'name': '第一性原理', 'ico': '🧩', 'href': '/ask-hub?tab=fp', 'deep': True},
    'design-assistant': {'name': '页面设计助手', 'ico': '✏️', 'href': '/design-assistant', 'deep': True},
    '3d-lab': {'name': '3D 小作品实验室', 'ico': '🧊', 'href': '/3d-lab', 'deep': False},
    'sleep': {'name': '睡前打烊', 'ico': '🌙', 'href': '/sleep', 'deep': False},
}

_FALLBACK = (
    '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
    '<body style="background:#0a0f1e;color:#e8edf7;font-family:system-ui">'
    '<h2>记录总览</h2><p>页面模板缺失：templates/overview.html</p></body></html>')


def _user(request: Request) -> str:
    """LoginGate 中间件把登录用户名写在这里；没登录根本走不到路由。"""
    return getattr(request.state, 'user', '') or ''


def _text(value, limit: int = 160) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _count(value) -> int:
    return len(value) if isinstance(value, list) else 0


def _excerpt(tool: str, payload: dict) -> str:
    """每个工具「当时记的那句核心内容」存在不同字段里，这里统一取出来。"""
    if tool == 'judgement-trainer':
        return _text(payload.get('text'))
    if tool == 'five_why':
        return _text(payload.get('problem'))
    if tool == 'socratic':
        return _text(payload.get('ask'))
    if tool == 'study-assistant':
        return _text(payload.get('summary')) or _text(payload.get('topic'))
    if tool == 'reading-practice':
        book = _text(payload.get('book'), 40)
        point = _text(payload.get('point'), 120)
        if book and point:
            return book + '：' + point
        return point or book
    if tool == 'kolb':
        answer = payload.get('a') if isinstance(payload.get('a'), dict) else {}
        return _text(answer.get('ac')) or _text(answer.get('ce')) or _text(payload.get('title'))
    if tool == 'ai-chart':
        return _text(payload.get('prompt'))
    if tool == 'script-library':
        return _text(payload.get('desc')) or _text(payload.get('cmd'))
    if tool == 'goal-split':
        return _text(payload.get('statement')) or _text(payload.get('raw'))
    if tool == 'site-collect':
        return _text(payload.get('why')) or _text(payload.get('url'))
    if tool == 'first_principles':
        return _text(payload.get('goal'))
    if tool == 'langchain-test':
        return _text(payload.get('input')) or _text(payload.get('case_name'))
    if tool == 'design-assistant':
        spec = payload.get('spec') if isinstance(payload.get('spec'), dict) else {}
        return _text(spec.get('summary')) or _text(payload.get('brief'))
    if tool == '3d-lab':
        model = {'tray': '桌面收纳盒', 'stand': '手机支架', 'hook': '洞洞板挂钩',
                 'custom': '自由绘制'}.get(
            payload.get('model'), '3D 参数方案')
        params = payload.get('params') if isinstance(payload.get('params'), dict) else {}
        if payload.get('model') == 'custom':
            return model + '：%s 个轮廓点，%s 个圆孔' % (
                len(params.get('outline') or []), len(params.get('holes') or []))
        size = ' × '.join(str(params.get(k)) for k in ('width', 'depth', 'height') if params.get(k))
        return model + (('：' + size + ' mm') if size else '')
    if tool == 'sleep':
        base = {'white': '白噪音', 'pink': '粉红噪音', 'brown': '棕噪音'}.get(
            payload.get('base_type'), '底噪')
        ambience = {'none': '', 'stream': '溪流', 'rain': '细雨', 'fan': '风扇', 'waves': '海浪'}.get(
            payload.get('ambience_type'), '')
        return base + ((' + ' + ambience) if ambience else '') + \
            ' · %s 分钟' % (payload.get('duration') or 30)
    return ''


def _status(tool: str, payload: dict) -> dict:
    """这个工具自己认不认「收尾」这件事，只有认的工具才参与「没收尾」筛选。

    tracked=False 表示该工具没有闭环概念（比如 AI 图表出了图就算完），
    这类记录不会污染「没收尾」这个数字。
    """
    if tool == 'judgement-trainer':
        if payload.get('outcome'):
            return {'tracked': True, 'open': False, 'label': '已对答案'}
        due = _text(payload.get('due'), 20)
        return {'tracked': True, 'open': True,
                'label': ('等 ' + due + ' 对答案') if due else '还没对答案'}
    if tool == 'five_why':
        if payload.get('finished'):
            return {'tracked': True, 'open': False, 'label': '已出报告'}
        level = payload.get('level')
        return {'tracked': True, 'open': True,
                'label': ('问到第 %s 层' % level) if level else '还没问完'}
    if tool == 'socratic':
        if payload.get('finished'):
            return {'tracked': True, 'open': False, 'label': '已收尾'}
        step = payload.get('step')
        return {'tracked': True, 'open': True,
                'label': ('走到第 %s 步' % step) if step else '还没走完'}
    if tool == 'kolb':
        if payload.get('finished'):
            return {'tracked': True, 'open': False, 'label': '已走完一圈'}
        return {'tracked': True, 'open': True,
                'label': '走到第 %s 段' % (payload.get('stage') or 1)}
    if tool == 'goal-split':
        acts = payload.get('actions') if isinstance(payload.get('actions'), list) else []
        done = len([x for x in acts if isinstance(x, dict) and x.get('done')])
        if acts and done >= len(acts):
            return {'tracked': True, 'open': False, 'label': '动作已全部勾完'}
        if acts:
            return {'tracked': True, 'open': True, 'label': '还差 %s/%s 个动作' % (len(acts) - done, len(acts))}
        return {'tracked': True, 'open': True, 'label': '还没拆出动作'}
    if tool == 'first_principles':
        # 第 5 步就算收尾了；前四步都算还没走完
        step = payload.get('step') or 1
        try:
            step = int(step)
        except (TypeError, ValueError):
            step = 1
        if step >= 5:
            return {'tracked': True, 'open': False, 'label': '已收尾'}
        names = {2: '零件待拷问', 3: '拷问中', 4: '从零重算中'}
        return {'tracked': True, 'open': True,
                'label': names.get(step, '还没拆零件')}
    if tool == 'langchain-test':
        # 跑一次就是一次记录，没有「收尾」这一说；标签用来标是哪个用例
        return {'tracked': False, 'open': False, 'label': _text(payload.get('case_name'), 20)}
    if tool == 'site-collect':
        # 收藏没有「收尾」这一说，不参与「没收尾」筛选，但状态标签照样显示
        label = {'new': '待看', 'using': '常用', 'archived': '归档'}.get(payload.get('state'), '')
        return {'tracked': False, 'open': False, 'label': label}
    if tool == 'design-assistant':
        return {'tracked': False, 'open': False,
                'label': '第 %s 版' % (payload.get('revision') or 1)}
    return {'tracked': False, 'open': False, 'label': ''}


def _detail(tool: str, payload: dict) -> list:
    """展开时显示的几行关键信息 —— 不用点回原工具就能想起当时记了什么。"""
    out = []

    def add(label, value, limit=200):
        text = _text(value, limit)
        if text:
            out.append(label + '：' + text)

    if tool == 'judgement-trainer':
        add('当时的打算', payload.get('lean'))
        if payload.get('conf') not in (None, ''):
            add('把握', '%s%%' % payload.get('conf'))
        add('到期', payload.get('due'), 20)
        if payload.get('outcome'):
            add('结果', '发生了' if payload.get('outcome') == 'yes' else '没发生')
        add('回访备注', payload.get('note'))
    elif tool == 'five_why':
        add('阶段', payload.get('stage'), 20)
        add('已问层数', payload.get('level'), 8)
        roots = [n for n in (payload.get('chain') or [])
                 if isinstance(n, dict) and n.get('root')]
        if roots:
            add('根因', roots[0].get('text') or roots[0].get('name'))
            add('根因个数', len(roots), 8)
        add('是否出报告', '是' if payload.get('finished') else '否', 8)
    elif tool == 'socratic':
        add('已走步数', payload.get('step'), 8)
        add('对话轮数', _count(payload.get('turns')), 8)
        add('是否收尾', '是' if payload.get('finished') else '否', 8)
    elif tool == 'goal-split':
        add('目标', payload.get('statement') or payload.get('raw'))
        add('截止', payload.get('deadline'), 20)
        ms = payload.get('milestones') if isinstance(payload.get('milestones'), list) else []
        acts = payload.get('actions') if isinstance(payload.get('actions'), list) else []
        if ms:
            add('里程碑', '%s 站' % len(ms), 8)
        if acts:
            done = len([x for x in acts if isinstance(x, dict) and x.get('done')])
            add('动作进度', '%s/%s' % (done, len(acts)), 8)
    elif tool == 'site-collect':
        add('网址', payload.get('url'), 120)
        add('分类', {'tool': '工具', 'learn': '学习·教程', 'doc': '文档', 'design': '设计·灵感', 'ai': 'AI', 'data': '数据', 'media': '影音', 'other': '其他'}.get(payload.get('cat')), 20)
        add('为什么收', payload.get('why'))
        analysis = payload.get('analysis') if isinstance(payload.get('analysis'), dict) else {}
        if analysis:
            add('AI 结论', analysis.get('summary'))
            if analysis.get('score') not in (None, ''):
                add('AI 分数', analysis.get('score'), 8)
            add('AI 依据', analysis.get('basis'), 60)
        add('备注', payload.get('note'))
    elif tool == 'study-assistant':
        add('主题', payload.get('topic'))
        add('现在会的', payload.get('level'))
        add('卡在', payload.get('blocker'))
        maps = _count(payload.get('maps'))
        if maps:
            add('思维导图', '%s 张' % maps, 8)
        steps = payload.get('checklist') or []
        if steps:
            done = payload.get('done') or []
            finished = len([x for x in done if x])
            add('清单', '共 %s 步，已完成 %s 步' % (len(steps), finished))
    elif tool == 'reading-practice':
        add('书', payload.get('book'), 60)
        add('作者', payload.get('author'), 40)
        add('任务', payload.get('point'))
        add('适用场合', payload.get('scene'))
        steps = payload.get('steps') or []
        if steps:
            add('三步', ' / '.join(_text(x, 60) for x in steps if _text(x, 60)))
        add('算做到的标准', payload.get('done'))
    elif tool == 'kolb':
        answer = payload.get('a') if isinstance(payload.get('a'), dict) else {}
        add('场景', {'work': '工作决策', 'life': '人际', 'learn': '学习'}.get(payload.get('domain')), 20)
        add('具体经验', answer.get('ce'))
        add('经验原则', answer.get('ac'))
        add('什么时候不成立', answer.get('ac_edge'))
        add('下次实验', answer.get('ae_trigger'), 120)
        add('要做的事', answer.get('ae_action'), 120)
        add('验证日期', answer.get('ae_due'), 20)
    elif tool == 'first_principles':
        items = payload.get('items') if isinstance(payload.get('items'), list) else []
        add('到第几步', payload.get('step'), 8)
        if items:
            add('零件数', '%s 条' % len(items), 8)
            hard = [x for x in items if isinstance(x, dict) and x.get('kind') in ('fact', 'price')]
            add('其中硬约束', '%s 条' % len(hard), 8)
        add('从零重算下限', payload.get('floor'), 60)
        if payload.get('diff'):
            add('差额', payload.get('diff'), 60)
    elif tool == 'langchain-test':
        add('用例', payload.get('case_name'), 40)
        add('输入', payload.get('input'))
        add('返回块数', _count(payload.get('blocks')) or None, 8)
    elif tool == 'ai-chart':
        add('提示词', payload.get('prompt'))
        data = payload.get('data')
        if isinstance(data, dict):
            add('图表标题', data.get('title'), 60)
            rows = data.get('rows')
            add('数据行数', _count(rows) or None, 8)
    elif tool == 'design-assistant':
        spec = payload.get('spec') if isinstance(payload.get('spec'), dict) else {}
        add('原始需求', payload.get('brief'))
        add('方案', spec.get('title'), 60)
        screens = spec.get('screens') if isinstance(spec.get('screens'), list) else []
        if screens:
            add('页面数', len(screens), 8)
            add('页面', ' / '.join(_text(x.get('name'), 20) for x in screens
                                   if isinstance(x, dict) and _text(x.get('name'), 20)), 160)
        add('最近修改', payload.get('instruction'))
    elif tool == '3d-lab':
        names = {'tray': '桌面收纳盒', 'stand': '手机支架', 'hook': '洞洞板挂钩', 'custom': '自由绘制'}
        add('模板', names.get(payload.get('model'), payload.get('model')), 30)
        params = payload.get('params') if isinstance(payload.get('params'), dict) else {}
        for key, label in (
            ('width', '宽度'), ('depth', '深度'), ('height', '高度'),
            ('wall', '壁厚'), ('cols', '列数'), ('rows', '行数'),
            ('angle', '后仰角'), ('reach', '伸出'), ('plateHeight', '底板高度'),
        ):
            if params.get(key) is not None:
                add(label, params.get(key), 20)
    elif tool == 'sleep':
        names = {
            'base': {'white': '白噪音', 'pink': '粉红噪音', 'brown': '棕噪音'},
            'ambience': {'none': '无', 'stream': '溪流', 'rain': '细雨', 'fan': '风扇', 'waves': '海浪'},
        }
        add('底噪', names['base'].get(payload.get('base_type')), 20)
        add('底噪强度', payload.get('base_level'), 8)
        add('环境声', names['ambience'].get(payload.get('ambience_type')), 20)
        add('环境音量', payload.get('ambience_level'), 8)
        add('播放时长', payload.get('duration'), 8)
        add('淡出', payload.get('fade'), 8)
    return out


def _item(row: dict):
    tool = str(row.get('tool') or '')
    meta = TOOL_META.get(tool)
    if not meta:
        return None
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    excerpt = _excerpt(tool, payload)
    title = _text(row.get('title'), 80) or excerpt[:40] or '（没写标题）'
    status = _status(tool, payload)
    return {
        'id': row.get('id'),
        'tool': tool,
        'name': meta['name'],
        'ico': meta['ico'],
        'href': meta['href'],
        'deep': bool(meta.get('deep')),
        'title': title,
        'excerpt': excerpt,
        'created_at': str(row.get('created_at') or ''),
        'status': status['label'],
        'open': status['open'],
        'tracked': status['tracked'],
        'detail': _detail(tool, payload),
    }


@router.get('/overview/api/items')
def overview_items(request: Request) -> JSONResponse:
    try:
        rows = storage.list_records_all(_user(request), ITEM_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)}, status_code=503)
    items = [it for it in (_item(r) for r in rows) if it]
    counts = {}
    tracked = 0
    opened = 0
    for it in items:
        counts[it['tool']] = counts.get(it['tool'], 0) + 1
        if it['tracked']:
            tracked += 1
            if it['open']:
                opened += 1
    tools = [{'key': k, 'name': TOOL_META[k]['name'], 'ico': TOOL_META[k]['ico'], 'n': counts[k]}
             for k in TOOL_META if counts.get(k)]
    return JSONResponse({'ok': True, 'items': items, 'tools': tools, 'limit': ITEM_MAX,
                         'total': len(items), 'tracked': tracked, 'open': opened})


@router.get('/overview', response_class=HTMLResponse)
def overview_page() -> HTMLResponse:
    if _TEMPLATE.exists():
        html = _TEMPLATE.read_text(encoding='utf-8')
    else:
        html = _FALLBACK
    return HTMLResponse(html)
