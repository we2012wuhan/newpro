# -*- coding: utf-8 -*-
# 页面设计助手：一句话需求 -> 可点击线框原型 + 结构化设计规格
# 页面在 /design-assistant，历史记录落 SQLite（history 表，tool='design-assistant'）
# 生成结果只输出结构化 JSON，前端负责画原型。
import json
import re

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import storage

try:
    from model_config import llm_endpoint, llm_key, llm_model
except Exception:  # 单独运行本文件时的兜底
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'


router = APIRouter()

TOOL = 'design-assistant'
BRIEF_MAX = 1200
INSTRUCTION_MAX = 600
LIST_MAX = 500
PAGE_SIZE = 6
PAGE_MIN = 4
PAGE_MAX = 30
QUERY_MAX = 60
SCREEN_MAX = 5
SECTION_MAX = 9
ITEM_MAX = 8
ACTION_MAX = 4

NO_KEY = (
    '服务端没配模型 Key（DEEPSEEK_API_KEY）。设计助手需要模型把需求拆成页面和交互，'
    '配好 Key 后再来。'
)

SCHEMA_HELP = r'''
只返回一个 JSON 对象，不要 Markdown 代码块，不要解释。结构必须严格为：
{
  "version": 1,
  "title": "方案名，2-18字",
  "summary": "两句话说清这个页面怎么帮用户完成事情",
  "audience": "主要使用者，一句话",
  "goals": ["3-5条可验收目标"],
  "assumptions": ["2-4条你替用户做出的合理假设"],
  "flow": [
    {"label": "用户动作短名", "from": "屏幕id", "to": "屏幕id", "trigger": "什么操作触发"}
  ],
  "screens": [
    {
      "id": "home",
      "name": "首页",
      "purpose": "这一屏只解决什么",
      "sections": [
        {
          "type": "nav|hero|form|stats|list|cards|tabs|editor|empty|detail|footer|note",
          "title": "区块标题，可空",
          "text": "区块说明，可空",
          "items": [
            {"title": "条目标题", "text": "条目说明", "meta": "状态或辅助信息"}
          ],
          "actions": [
            {"label": "按钮文字", "target": "另一个屏幕id或空", "kind": "primary|secondary|text"}
          ]
        }
      ],
      "primary": {"label": "主按钮", "target": "另一个屏幕id或空"}
    }
  ],
  "components": [
    {"name": "组件名", "purpose": "用途", "variants": ["变体"], "states": ["状态"]}
  ],
  "states": [
    {"screen": "屏幕id", "name": "空状态|加载中|错误|成功", "description": "页面怎么反馈"}
  ],
  "interactions": [
    {"trigger": "触发方式", "result": "发生什么", "boundary": "限制或例外"}
  ],
  "boundaries": ["明确不做什么，以及关键风险/取舍"],
  "data": [
    {"entity": "数据实体", "fields": ["字段或关系"]}
  ],
  "tokens": {
    "primary": "#5B6CFF",
    "surface": "#F7F8FC",
    "radius": "16px",
    "spacing": "8px",
    "font": "system-ui"
  }
}
'''

ROLE = '\n'.join([
    '你是一名产品设计师兼交互规格作者。用户只有一个粗需求，你要直接做一个可以讨论的第一版，',
    '不要反问，不要拖到下一轮。所有文字使用简体中文，除非用户明确要求其他语言。',
    '',
    '设计要求：',
    '1. 先抓核心任务，不做功能堆砌。2-4 个屏幕足够；单页工具可以只有 1-2 屏。',
    '2. 每个屏幕都要有明确目的、信息层级和主操作。按钮的 target 必须指向真实存在的屏幕 id。',
    '3. 手机优先，同时能在桌面展示。每个关键页面至少考虑空、加载、错误、成功中的相关状态。',
    '4. 交互写可执行的规则：点了发生什么、失败怎么办、什么情况不允许。',
    '5. 边界要诚实：写清这版不做什么，避免为了完整而虚构复杂能力。',
    '6. 颜色只作为一个克制的主色建议，原型会跟随工具箱浅色/深色主题。',
    '7. 不要输出 HTML、CSS 或脚本；输出结构化数据，由前端安全渲染。',
    '8. JSON 字符串内不要换行。数组不够时用空数组，不要省略字段。',
])

EDIT_ROLE = '\n'.join([
    '你是一名产品设计师。下面会给一份现有页面设计规格 JSON，以及用户的一条修改意见。',
    '请保留没有被意见否定的部分，做最小但完整的改版，然后返回整份新 JSON。',
    '不要解释改了什么，不要输出 Markdown，不要反问。所有屏幕 id 必须稳定；新增屏幕才使用新 id。',
    '修改后仍要满足原有结构约束。',
])


def _clean(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _clean_list(value, limit, most):
    if not isinstance(value, list):
        return []
    out = []
    for raw in value:
        text = _clean(raw, limit)
        if text and text not in out:
            out.append(text)
        if len(out) >= most:
            break
    return out


def _extract_json(text):
    raw = re.sub(r'```(?:json)?', '', text or '', flags=re.I).replace('```', '')
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _chat(system, user, max_tokens=3600, temperature=0.35):
    key = llm_key()
    if not key:
        return None, NO_KEY
    body = {
        'model': llm_model(),
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    try:
        resp = requests.post(
            llm_endpoint(),
            json=body,
            timeout=(10, 180),
            headers={
                'Authorization': 'Bearer ' + key,
                'Content-Type': 'application/json',
            },
        )
    except requests.RequestException as exc:
        return None, '模型请求失败：' + str(exc)[:100]
    if resp.status_code >= 400:
        return None, '模型返回错误 ' + str(resp.status_code)
    try:
        return resp.json()['choices'][0]['message']['content'], ''
    except (ValueError, KeyError, IndexError, TypeError):
        return None, '模型返回格式异常'


def _chat_spec(system, user):
    """模型偶尔会在 JSON 前后加话；先解析，失败再用更硬的格式提醒重试一次。"""
    first, err = _chat(system, user)
    spec = _norm_spec(_extract_json(first or ''))
    if spec:
        return spec, ''

    retry = user + '\n\n上一次没有返回可解析的完整 JSON。现在只输出一个合法 JSON 对象。'
    second, err2 = _chat(system, retry, temperature=0.15)
    spec = _norm_spec(_extract_json(second or ''))
    if spec:
        return spec, ''
    return None, err2 or err or '模型没有返回完整的设计规格，再试一次。'


def _slug(value, fallback):
    text = str(value or '').strip().lower()
    text = re.sub(r'[^a-z0-9_-]+', '-', text).strip('-_')
    return (text or fallback)[:40]


def _norm_item(raw):
    if isinstance(raw, str):
        text = _clean(raw, 180)
        return {'title': text, 'text': '', 'meta': ''} if text else None
    if not isinstance(raw, dict):
        return None
    item = {
        'title': _clean(raw.get('title') or raw.get('label') or raw.get('name'), 80),
        'text': _clean(raw.get('text') or raw.get('description') or raw.get('desc'), 220),
        'meta': _clean(raw.get('meta') or raw.get('status'), 60),
    }
    return item if (item['title'] or item['text']) else None


def _norm_action(raw):
    if not isinstance(raw, dict):
        return None
    label = _clean(raw.get('label'), 40)
    if not label:
        return None
    kind = _clean(raw.get('kind'), 12).lower()
    if kind not in ('primary', 'secondary', 'text'):
        kind = 'secondary'
    return {
        'label': label,
        'target': _slug(raw.get('target'), ''),
        'kind': kind,
    }


def _norm_section(raw):
    if not isinstance(raw, dict):
        return None
    kind = _clean(raw.get('type'), 20).lower()
    allowed = {
        'nav', 'hero', 'form', 'stats', 'list', 'cards', 'tabs',
        'editor', 'empty', 'detail', 'footer', 'note',
    }
    if kind not in allowed:
        kind = 'list'
    items = []
    for raw_item in (raw.get('items') or [])[:ITEM_MAX]:
        item = _norm_item(raw_item)
        if item:
            items.append(item)
    actions = []
    for raw_action in (raw.get('actions') or [])[:ACTION_MAX]:
        action = _norm_action(raw_action)
        if action:
            actions.append(action)
    out = {
        'type': kind,
        'title': _clean(raw.get('title'), 80),
        'text': _clean(raw.get('text'), 260),
        'items': items,
        'actions': actions,
    }
    return out if (out['title'] or out['text'] or items or actions) else None


def _norm_screen(raw, idx):
    if not isinstance(raw, dict):
        return None
    sid = _slug(raw.get('id'), 'screen-%d' % idx)
    name = _clean(raw.get('name') or raw.get('title'), 40) or ('页面 %d' % idx)
    sections = []
    for raw_section in (raw.get('sections') or [])[:SECTION_MAX]:
        section = _norm_section(raw_section)
        if section:
            sections.append(section)
    if not sections:
        return None
    primary = _norm_action(raw.get('primary'))
    return {
        'id': sid,
        'name': name,
        'purpose': _clean(raw.get('purpose'), 180),
        'sections': sections,
        'primary': primary,
    }


def _norm_component(raw):
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get('name'), 60)
    if not name:
        return None
    return {
        'name': name,
        'purpose': _clean(raw.get('purpose'), 180),
        'variants': _clean_list(raw.get('variants'), 50, 6),
        'states': _clean_list(raw.get('states'), 50, 8),
    }


def _norm_state(raw, screen_ids):
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get('name'), 40)
    desc = _clean(raw.get('description'), 220)
    if not name or not desc:
        return None
    screen = _slug(raw.get('screen'), '')
    if screen and screen not in screen_ids:
        screen = ''
    return {'screen': screen, 'name': name, 'description': desc}


def _norm_interaction(raw):
    if not isinstance(raw, dict):
        return None
    trigger = _clean(raw.get('trigger'), 120)
    result = _clean(raw.get('result'), 220)
    if not trigger or not result:
        return None
    return {
        'trigger': trigger,
        'result': result,
        'boundary': _clean(raw.get('boundary'), 180),
    }


def _norm_data(raw):
    if not isinstance(raw, dict):
        return None
    entity = _clean(raw.get('entity'), 60)
    if not entity:
        return None
    return {'entity': entity, 'fields': _clean_list(raw.get('fields'), 100, 16)}


def _norm_tokens(raw):
    raw = raw if isinstance(raw, dict) else {}
    primary = _clean(raw.get('primary'), 20)
    surface = _clean(raw.get('surface'), 20)
    if not re.match(r'^#[0-9a-fA-F]{6}$', primary):
        primary = '#5B6CFF'
    if not re.match(r'^#[0-9a-fA-F]{6}$', surface):
        surface = '#F7F8FC'
    return {
        'primary': primary,
        'surface': surface,
        'radius': _clean(raw.get('radius'), 20) or '16px',
        'spacing': _clean(raw.get('spacing'), 20) or '8px',
        'font': _clean(raw.get('font'), 50) or 'system-ui',
    }


def _norm_spec(raw):
    """白名单归一化，防止模型偶发多吐字段把页面结构带乱。"""
    if not isinstance(raw, dict):
        return {}
    title = _clean(raw.get('title'), 60)
    summary = _clean(raw.get('summary'), 320)
    screens = []
    seen = set()
    for idx, raw_screen in enumerate((raw.get('screens') or [])[:SCREEN_MAX], 1):
        screen = _norm_screen(raw_screen, idx)
        if not screen:
            continue
        base = screen['id']
        n = 2
        while screen['id'] in seen:
            screen['id'] = '%s-%d' % (base, n)
            n += 1
        seen.add(screen['id'])
        screens.append(screen)
    if not screens:
        return {}

    # 把不存在的按钮目标清空；模型偶尔会引用没画出来的页面。
    for screen in screens:
        if screen.get('primary') and screen['primary'].get('target') not in seen:
            screen['primary']['target'] = ''
        for section in screen['sections']:
            for action in section['actions']:
                if action.get('target') not in seen:
                    action['target'] = ''

    complete = bool(title and summary)
    if not title:
        title = '未命名页面方案'
    if not summary:
        summary = '按核心任务拆成可点击页面，并保留后续继续修改的空间。'

    components = []
    for raw_component in (raw.get('components') or [])[:16]:
        component = _norm_component(raw_component)
        if component:
            components.append(component)

    states = []
    for raw_state in (raw.get('states') or [])[:20]:
        state = _norm_state(raw_state, seen)
        if state:
            states.append(state)

    interactions = []
    for raw_interaction in (raw.get('interactions') or [])[:20]:
        interaction = _norm_interaction(raw_interaction)
        if interaction:
            interactions.append(interaction)

    data = []
    for raw_data in (raw.get('data') or [])[:10]:
        one = _norm_data(raw_data)
        if one:
            data.append(one)

    flow = []
    for raw_flow in (raw.get('flow') or [])[:16]:
        if not isinstance(raw_flow, dict):
            continue
        frm = _slug(raw_flow.get('from'), '')
        to = _slug(raw_flow.get('to'), '')
        if frm not in seen or to not in seen:
            continue
        flow.append({
            'label': _clean(raw_flow.get('label'), 40) or '继续',
            'from': frm,
            'to': to,
            'trigger': _clean(raw_flow.get('trigger'), 100),
        })

    if not flow:
        for screen in screens:
            actions = []
            if screen.get('primary'):
                actions.append(screen['primary'])
            for section in screen['sections']:
                actions.extend(section['actions'])
            for action in actions:
                if action.get('target'):
                    flow.append({
                        'label': action['label'],
                        'from': screen['id'],
                        'to': action['target'],
                        'trigger': '点击「%s」' % action['label'],
                    })
                    break

    return {
        'version': 1,
        'title': title,
        'summary': summary,
        'audience': _clean(raw.get('audience'), 180),
        'goals': _clean_list(raw.get('goals'), 140, 6),
        'assumptions': _clean_list(raw.get('assumptions'), 180, 6),
        'flow': flow,
        'screens': screens,
        'components': components,
        'states': states,
        'interactions': interactions,
        'boundaries': _clean_list(raw.get('boundaries'), 220, 8),
        'data': data,
        'tokens': _norm_tokens(raw.get('tokens')),
        '_complete': complete,
    }


def _public_spec(spec):
    out = dict(spec or {})
    out.pop('_complete', None)
    return out


def _generate(brief, instruction, current_spec=None):
    if not llm_key():
        return None, NO_KEY
    if current_spec:
        user = '\n'.join([
            '【现有设计规格】',
            json.dumps(_public_spec(current_spec), ensure_ascii=False, separators=(',', ':')),
            '',
            '【本次修改意见】',
            instruction,
            '',
            '【输出要求】',
            '返回修改后的整份 JSON。保留原屏幕 id；只改需要改的部分。',
            SCHEMA_HELP,
        ])
        return _chat_spec(EDIT_ROLE, user)

    user = '\n'.join([
        '【用户需求】',
        brief,
        '',
        '【输出要求】',
        '现在直接完成第一版设计。不要反问，假设写进 assumptions。',
        SCHEMA_HELP,
    ])
    return _chat_spec(ROLE, user)


def _user(request):
    return getattr(request.state, 'user', '') or ''


async def _body(request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _pack(rid, row, full=True):
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    spec = payload.get('spec') if isinstance(payload.get('spec'), dict) else {}
    screens = spec.get('screens') if isinstance(spec.get('screens'), list) else []
    out = {
        'id': rid,
        'title': spec.get('title') or row.get('title') or '未命名页面方案',
        'brief': _clean(payload.get('brief'), BRIEF_MAX),
        'summary': _clean(spec.get('summary'), 320),
        'revision': int(payload.get('revision') or 1),
        'screens': len(screens),
        'created_at': row.get('created_at') or '',
        'updated_at': payload.get('updated_at') or row.get('created_at') or '',
    }
    if full:
        out['spec'] = _public_spec(spec)
    return out


def _row(row, full=True):
    return _pack(row.get('id'), row, full)


def _haystack(title, payload):
    spec = payload.get('spec') if isinstance(payload.get('spec'), dict) else {}
    parts = [
        str(title or ''),
        str(payload.get('brief') or ''),
        str(payload.get('instruction') or ''),
        str(spec.get('title') or ''),
        str(spec.get('summary') or ''),
        str(spec.get('audience') or ''),
    ]
    for screen in spec.get('screens') or []:
        if not isinstance(screen, dict):
            continue
        parts.extend([str(screen.get('name') or ''), str(screen.get('purpose') or '')])
    return ' '.join(parts).lower()


def _page_args(page, size):
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = PAGE_SIZE
    return max(1, page), min(PAGE_MAX, max(PAGE_MIN, size))


def _paged(items, page, size):
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = min(max(1, page), pages)
    start = (page - 1) * size
    return items[start:start + size], page, pages, total


@router.get('/design-assistant', response_class=HTMLResponse)
def design_assistant_page():
    from pathlib import Path
    path = Path(__file__).resolve().parent / 'templates' / 'design-assistant.html'
    if path.exists():
        return HTMLResponse(path.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="background:#101322;color:#fff;font-family:system-ui">'
        '<h2>模板文件缺失</h2><p>请确认 templates/design-assistant.html 存在。</p></body></html>'
    )


@router.get('/design-assistant/api/status')
def design_assistant_status():
    return JSONResponse({'ok': True, 'ai': bool(llm_key())})


@router.get('/design-assistant/api/designs')
def design_assistant_list(request: Request, q: str = '', page: int = 1, size: int = PAGE_SIZE):
    page, size = _page_args(page, size)
    query = _clean(q, QUERY_MAX)
    terms = [x for x in query.lower().split() if x][:6]
    try:
        rows = storage.list_records(TOOL, _user(request), LIST_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse(
            {'ok': False, 'items': [], 'message': str(exc)}, status_code=503
        )
    if terms:
        rows = [
            row for row in rows
            if all(
                term in _haystack(row.get('title'), row.get('payload') or {})
                for term in terms
            )
        ]
    items = [_row(row, False) for row in rows]
    one, page, pages, total = _paged(items, page, size)
    return JSONResponse({
        'ok': True,
        'items': one,
        'q': query,
        'page': page,
        'pages': pages,
        'size': size,
        'total': total,
    })


@router.post('/design-assistant/api/designs')
async def design_assistant_create(request: Request):
    payload = await _body(request)
    brief = _clean(payload.get('brief'), BRIEF_MAX)
    if len(brief) < 4:
        return JSONResponse(
            {'ok': False, 'message': '把你想做的页面多写一点，至少四个字。'},
            status_code=400,
        )

    spec, err = await run_in_threadpool(_generate, brief, '', None)
    if not spec:
        return JSONResponse({'ok': False, 'message': err}, status_code=502)

    now = storage.now_str()
    record = {
        'brief': brief,
        'instruction': '',
        'revision': 1,
        'spec': _public_spec(spec),
        'created_at': now,
        'updated_at': now,
    }
    try:
        saved = storage.add_record(
            TOOL, _user(request), spec.get('title') or brief[:60], record
        )
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    return JSONResponse({
        'ok': True,
        'design': _pack(saved['id'], {
            'title': saved['title'],
            'payload': record,
            'created_at': saved['created_at'],
        }),
    })


@router.get('/design-assistant/api/designs/{rid}')
def design_assistant_one(rid: int, request: Request):
    try:
        row = storage.get_record(_user(request), rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not row or row.get('tool') != TOOL:
        return JSONResponse(
            {'ok': False, 'message': '找不到这个设计方案。'}, status_code=404
        )
    return JSONResponse({'ok': True, 'design': _row(row)})


@router.post('/design-assistant/api/designs/{rid}/refine')
async def design_assistant_refine(rid: int, request: Request):
    payload = await _body(request)
    instruction = _clean(payload.get('instruction'), INSTRUCTION_MAX)
    if len(instruction) < 2:
        return JSONResponse(
            {'ok': False, 'message': '说一下具体想改哪里。'}, status_code=400
        )
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not row or row.get('tool') != TOOL:
        return JSONResponse(
            {'ok': False, 'message': '找不到这个设计方案。'}, status_code=404
        )
    old = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    old_spec = old.get('spec') if isinstance(old.get('spec'), dict) else {}
    if not old_spec:
        return JSONResponse(
            {'ok': False, 'message': '这份设计记录里没有可修改的规格。'},
            status_code=409,
        )

    spec, err = await run_in_threadpool(
        _generate, _clean(old.get('brief'), BRIEF_MAX), instruction, old_spec
    )
    if not spec:
        return JSONResponse({'ok': False, 'message': err}, status_code=502)

    now = storage.now_str()
    record = dict(old)
    record.update({
        'instruction': instruction,
        'revision': int(old.get('revision') or 1) + 1,
        'spec': _public_spec(spec),
        'updated_at': now,
    })
    try:
        changed = storage.update_record(
            user, rid, payload=record, title=spec.get('title') or row.get('title')
        )
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not changed:
        return JSONResponse(
            {'ok': False, 'message': '这条设计不属于当前账号。'}, status_code=403
        )
    return JSONResponse({
        'ok': True,
        'design': _pack(rid, {
            'title': spec.get('title') or row.get('title'),
            'payload': record,
            'created_at': row.get('created_at') or now,
        }),
    })


@router.delete('/design-assistant/api/designs/{rid}')
def design_assistant_delete(rid: int, request: Request):
    try:
        changed = storage.delete_record(_user(request), rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not changed:
        return JSONResponse(
            {'ok': False, 'message': '找不到这个设计方案。'}, status_code=404
        )
    return JSONResponse({'ok': True})


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI

    _app = FastAPI(title='页面设计助手', version='1.0.0')
    _app.include_router(router)
    uvicorn.run(_app, host='127.0.0.1', port=8014)
