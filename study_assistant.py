# -*- coding: utf-8 -*-
# 学习模型助手：用户提出「想学什么」→ 分别用第一性原理 / 金字塔原理 / 贝叶斯定理
# 对问题做拆分解读 → 返回三棵可绘制思维导图的树形数据，前端用 ECharts tree 展示。
# 每次拆解存 SQLite（history 表，tool = 'study-assistant'），右侧历史可回看、清单可勾选。
import json
import os
import re
import time
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from model_config import MISSING_KEY_HINT, llm_endpoint, llm_key, llm_model

import storage

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'study_assistant.html'
_MAX_DEPTH = 4
_MAX_CHILDREN = 10
_BT = chr(96)  # 反引号，清理 Markdown 代码块标记

_MAP_IDS = ('first-principles', 'pyramid', 'bayes')

TOOL = 'study-assistant'
PLAN_MAX = 100        # 历史记录一次最多读回多少条

_SCHEMA = {
    'summary': '用 1~2 句话整体判断：这个学习目标的关键难点与最适合的入门路线。',
    'maps': [
        {
            'id': 'first-principles',
            'name': '第一性原理',
            'desc': '从不可再分的基本事实出发，拆到最小单元再重建',
            'root': {'name': '主题：想学什么（简短）', 'children': [
                {'name': '最终要解决的问题/目标', 'children': [
                    {'name': '学会后能做出什么成果'},
                    {'name': '衡量学成的标准是什么'},
                ]},
            ]},
        },
        {
            'id': 'pyramid',
            'name': '金字塔原理',
            'desc': '结论先行、以上统下、分组归类、逻辑递进',
            'root': {'name': '主题：想学什么（简短）', 'children': [
                {'name': '中心结论', 'children': [
                    {'name': '论点一', 'children': [{'name': '支撑论据'}]},
                ]},
            ]},
        },
        {
            'id': 'bayes',
            'name': '贝叶斯定理',
            'desc': '先验判断 → 收集证据 → 更新认知 → 迭代逼近',
            'root': {'name': '主题：想学什么（简短）', 'children': [
                {'name': '我的先验认知', 'children': [{'name': '现在会什么 / 相信什么'}]},
            ]},
        },
    ],
    'checklist': [
        '5 条以内的检查清单，每一条都是一个具体可执行的下一步',
    ],
}

_PROMPT_LINES = [
    '你是擅长「学习方法论 + 认知拆解」的思维教练。用户会告诉你想学什么、或抛出某个问题，',
    '例如“我想系统学会机器学习”“我想搞懂如何写好论文”“我想自学编程但不知道从哪开始”。',
    '你的任务：用下面三种思考模型，分别把用户的问题做拆解，输出结构清晰的思维导图树，',
    '帮助用户建立“先拆解、再学习、可验证”的方向。',
    '',
    '一、第一性原理（First Principles）：不停留在“别人怎么说 / 感觉很难”，',
    '   而是把目标拆到不可再分的基本事实与最小单元，再从这些单元出发设计学习路径。',
    '   建议分支：明确目标成果 → 识别当前认知 → 提炼底层原理/基本单元 → 最小可行验证 → 重建路径。',
    '二、金字塔原理（Pyramid Principle）：结论先行、以上统下、分组归类、逻辑递进。',
    '   用“核心结论 → 几个主要论点 → 每个论点下的支撑论据/学习任务 → 优先级”组织学习地图。',
    '三、贝叶斯定理（Bayes）：把学习看成“不断用证据更新概率判断”的过程。',
    '   建议分支：我的先验(现在会什么/相信什么) → 需要收集的关键证据(概念/练习/反馈) → 如何更新认知 → 下一步要验证的假设。',
    '',
    '输出要求：',
    '1. 只输出一个 JSON 对象，键名严格与示例一致，不要输出 Markdown 代码块标记，不要输出其它内容；',
    '2. maps 必须恰好包含 three 个对象，id 依次为 first-principles / pyramid / bayes；',
    '3. 每个 map 的 root 是树节点：{ "name": 文字, "children": [子节点] }，children 可省略表示叶子；',
    '4. 每个 root 至少 4 个分支，分支再往下 1~2 层，总节点数 18~45 个；',
    '5. 每个节点 name 必须是通顺简短的中文短语（建议 4~18 字，最长不超过 24 字），',
    '   且要具体、贴合用户主题，禁止用“论点一/示例”这种占位词；',
    '6. summary 用 1~2 句话给出整体路线判断；checklist 给 3~5 条马上能做的行动。',
]
SYSTEM_PROMPT = chr(10).join(_PROMPT_LINES)
SYSTEM_PROMPT += chr(10) + chr(10) + '请只输出一个 JSON 对象，结构如下（键名必须一致）：' + chr(10)
SYSTEM_PROMPT += json.dumps(_SCHEMA, ensure_ascii=False)
SYSTEM_PROMPT += chr(10) + chr(10) + '不要输出 JSON 之外的任何内容。'


def _extract_json(text):
    text = re.sub(_BT * 3 + '[a-zA-Z]*', '', text or '')
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        raise ValueError('返回内容里没有找到 JSON')
    return json.loads(text[start:end + 1])


def _clean_node(node, depth=0):
    """递归清理树节点：控制层级与子节点数量，保证结构可渲染。"""
    if not isinstance(node, dict):
        return None
    name = str(node.get('name') or '').strip()
    name = re.sub(r'[\s\u3000]+', ' ', name)
    if not name:
        return None
    if len(name) > 26:
        name = name[:26]
    out = {'name': name}
    children_raw = node.get('children')
    if isinstance(children_raw, list) and children_raw and depth < _MAX_DEPTH:
        children = []
        for child in children_raw:
            cleaned = _clean_node(child, depth + 1)
            if cleaned:
                children.append(cleaned)
            if len(children) >= _MAX_CHILDREN:
                break
        if children:
            out['children'] = children
    return out


def _normalize(data):
    if not isinstance(data, dict):
        raise ValueError('模型返回的 JSON 顶层不是对象')
    maps_raw = data.get('maps')
    if not isinstance(maps_raw, list):
        raise ValueError('缺少 maps 数组（三种思维模型的拆解结果）')
    by_id = {}
    for item in maps_raw:
        if not isinstance(item, dict):
            continue
        mid = str(item.get('id') or '').strip()
        if mid not in _MAP_IDS:
            continue
        root_raw = item.get('root')
        root = _clean_node(root_raw) if isinstance(root_raw, dict) else None
        if not root:
            continue
        by_id[mid] = {
            'id': mid,
            'name': str(item.get('name') or mid).strip(),
            'desc': str(item.get('desc') or '').strip(),
            'root': root,
        }
    # 全部视角都未生成时也补齐占位图，避免前端空白
    # 未生成的模型自动补一条说明节点，保证前端始终展示三张图
    fallback_names = {
        'first-principles': '第一性原理',
        'pyramid': '金字塔原理',
        'bayes': '贝叶斯定理',
    }
    for mid in _MAP_IDS:
        if mid not in by_id:
            by_id[mid] = {
                'id': mid,
                'name': fallback_names[mid],
                'desc': '本次生成中该视角内容不足，建议换个方式重新提问',
                'root': {'name': fallback_names[mid], 'children': [
                    {'name': '重新明确你的学习目标'},
                    {'name': '补充你现在的水平与困惑'},
                ]},
            }
    maps = [by_id[mid] for mid in _MAP_IDS]

    summary = str(data.get('summary') or '').strip() or '已为你从三个角度完成拆解，下面三张思维导图分别代表不同的思考路径。'
    checklist_raw = data.get('checklist')
    checklist = []
    if isinstance(checklist_raw, list):
        for item in checklist_raw:
            s = str(item or '').strip()
            if s:
                checklist.append(s)
            if len(checklist) >= 6:
                break
    if not checklist:
        checklist = [
            '先画出三张图中与你现状最接近的一张，挑出 1 个分支作为本周目标。',
            '为每个分支找到 1~2 个可验证的小练习或小成果。',
            '每完成一步就回来更新一次自己的“贝叶斯认知”，记录证据与调整。',
        ]
    return {'summary': summary, 'maps': maps, 'checklist': checklist}


def _user_message(topic, level='', blocker=''):
    """把「想学什么」和两个可选上下文拼成一段给模型看的话。

    只补已有的信息，没填的就不提 —— 免得模型自己编。
    """
    text = '我想学习 / 想弄明白的是：' + topic
    if level:
        text += chr(10) + chr(10) + '我现在的水平 / 已经会的东西：' + level
    if blocker:
        text += chr(10) + '我具体卡在：' + blocker
    return text


def _do_analyze(topic, level='', blocker=''):
    topic = str(topic or '').strip()
    level = re.sub(r'\s+', ' ', str(level or '')).strip()[:200]
    blocker = re.sub(r'\s+', ' ', str(blocker or '')).strip()[:200]
    if not topic:
        return JSONResponse({'ok': False, 'message': '请先描述你想学什么或想解决的问题'})
    if len(topic) > 800:
        return JSONResponse({'ok': False, 'message': '输入太长了，请控制在 800 字以内'})
    model = llm_model()
    key = llm_key()
    if not key:
        return JSONResponse({'ok': False, 'message': MISSING_KEY_HINT})

    started = time.time()
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': _user_message(topic, level, blocker)},
        ],
        'temperature': 0.4,
        'max_tokens': 6000,
        'stream': False,
    }
    raw = ''
    try:
        resp = requests.post(llm_endpoint(), json=body, headers=headers, timeout=(15, 240))
    except requests.exceptions.Timeout:
        return JSONResponse({'ok': False, 'message': '请求大模型超时，请稍后重试或检查网络'})
    except requests.exceptions.RequestException as exc:
        return JSONResponse({'ok': False, 'message': '无法连接大模型接口：' + str(exc)})
    if resp.status_code >= 400:
        detail = re.sub('\x1b\\[[0-9;]*m', '', resp.text)
        return JSONResponse({'ok': False, 'message': '大模型接口返回错误 ' + str(resp.status_code) + '：' + detail[:300]})
    try:
        data = resp.json()
        raw = data['choices'][0]['message']['content']
    except Exception as exc:
        return JSONResponse({'ok': False, 'message': '大模型返回格式异常：' + str(exc)})
    try:
        payload = _extract_json(raw)
        result = _normalize(payload)
    except Exception as exc:
        snippet = re.sub(r'\s+', ' ', raw)[:180]
        return JSONResponse({'ok': False, 'message': '思维导图解析失败：' + str(exc) + '。返回内容预览：' + snippet})
    result.update({'ok': True, 'topic': topic, 'model': model, 'elapsed': round(time.time() - started, 1)})
    return JSONResponse(result)


# =========================================================
# 拆解记录：存 SQLite（history 表，tool = 'study-assistant'）
# ---------------------------------------------------------
# payload：topic / level / blocker / summary / maps / checklist / done / model / elapsed
# =========================================================


def _user(request: Request) -> str:
    """LoginGate 中间件把登录用户写在 request.state.user 上。"""
    return getattr(request.state, 'user', '') or ''


async def _body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _tidy(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _tidy_done(value, total):
    """勾选状态：只收和清单等长的布尔数组，缺的补 False。"""
    out = []
    if isinstance(value, list):
        for item in value[:total]:
            out.append(bool(item))
    while len(out) < total:
        out.append(False)
    return out


def _norm_maps(value):
    """前端回传的三棵树重新清洗一遍 —— 浏览器来的结构一律不信。"""
    if not isinstance(value, list):
        return []
    by_id = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        mid = str(item.get('id') or '').strip()
        if mid not in _MAP_IDS or mid in by_id:
            continue
        root = _clean_node(item.get('root')) if isinstance(item.get('root'), dict) else None
        if not root:
            continue
        by_id[mid] = {
            'id': mid,
            'name': _tidy(item.get('name'), 30) or mid,
            'desc': _tidy(item.get('desc'), 120),
            'root': root,
        }
    return [by_id[mid] for mid in _MAP_IDS if mid in by_id]


def _norm_plan(raw):
    if not isinstance(raw, dict):
        return None
    topic = _tidy(raw.get('topic'), 800)
    summary = _tidy(raw.get('summary'), 400)
    maps = _norm_maps(raw.get('maps'))
    if not topic or not summary or not maps:
        return None
    checklist = []
    if isinstance(raw.get('checklist'), list):
        for item in raw['checklist'][:6]:
            text = _tidy(item, 120)
            if text:
                checklist.append(text)
    try:
        elapsed = round(float(raw.get('elapsed') or 0), 1)
    except (TypeError, ValueError):
        elapsed = 0.0
    return {
        'topic': topic,
        'level': _tidy(raw.get('level'), 200),
        'blocker': _tidy(raw.get('blocker'), 200),
        'summary': summary,
        'maps': maps,
        'checklist': checklist,
        'done': _tidy_done(raw.get('done'), len(checklist)),
        'model': _tidy(raw.get('model'), 60),
        'elapsed': elapsed,
    }


def _plan_out(row):
    """把库里的行摊平成前端要用的形状：payload 的字段提到顶层。"""
    data = dict(row.get('payload') or {})
    data['id'] = row['id']
    data['created_at'] = row.get('created_at', '')
    return data


# ---------------- 拆解记录：五件套 ----------------
@router.get('/study-assistant/api/plans')
def study_assistant_plans(request: Request) -> JSONResponse:
    try:
        rows = storage.list_records(TOOL, _user(request), PLAN_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': [_plan_out(r) for r in rows]})


@router.post('/study-assistant/api/plans')
async def study_assistant_plan_add(request: Request) -> JSONResponse:
    plan = _norm_plan(await _body(request))
    if not plan:
        return JSONResponse({'ok': False, 'message': '这条拆解不完整，存不了。'}, status_code=400)
    try:
        rec = storage.add_record(TOOL, _user(request), plan['topic'][:60], plan)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    plan['id'] = rec['id']
    plan['created_at'] = rec['created_at']
    return JSONResponse({'ok': True, 'item': plan})


@router.patch('/study-assistant/api/plans/{rid}')
async def study_assistant_plan_update(rid: int, request: Request) -> JSONResponse:
    """只用来勾清单里的步骤（白名单）。"""
    patch = (await _body(request)).get('patch')
    if not isinstance(patch, dict):
        return JSONResponse({'ok': False, 'message': '没有要改的内容。'}, status_code=400)
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row:
            return JSONResponse({'ok': False, 'message': '找不到这条拆解。'}, status_code=404)
        data = row['payload'] if isinstance(row['payload'], dict) else {}
        if 'done' in patch:
            data['done'] = _tidy_done(patch.get('done'), len(data.get('checklist') or []))
        changed = storage.update_record(user, rid, payload=data)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not changed:
        return JSONResponse({'ok': False, 'message': '这条拆解不属于当前账号。'}, status_code=403)
    fresh = {'id': rid, 'payload': data, 'created_at': row.get('created_at', '')}
    return JSONResponse({'ok': True, 'item': _plan_out(fresh)})


@router.delete('/study-assistant/api/plans/{rid}')
def study_assistant_plan_delete(rid: int, request: Request) -> JSONResponse:
    try:
        changed = storage.delete_record(_user(request), rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not changed:
        return JSONResponse({'ok': False, 'message': '找不到这条拆解。'}, status_code=404)
    return JSONResponse({'ok': True})


@router.get('/study-assistant', response_class=HTMLResponse)
def study_assistant_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/study_assistant.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.post('/study-assistant/api/analyze')
async def study_assistant_analyze(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    topic = payload.get('topic') or ''
    level = payload.get('level') or ''
    blocker = payload.get('blocker') or ''
    return await run_in_threadpool(_do_analyze, topic, level, blocker)