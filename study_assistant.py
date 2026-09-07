# -*- coding: utf-8 -*-
# 学习模型助手：用户提出「想学什么」→ 分别用第一性原理 / 金字塔原理 / 贝叶斯定理
# 对问题做拆分解读 → 返回三棵可绘制思维导图的树形数据，前端用 ECharts tree 展示。
import json
import os
import re
import time
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ai_chart import _api_key, _endpoint

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'study_assistant.html'
_DEFAULT_BASE = 'https://api.deepseek.com'
_DEFAULT_MODEL = 'deepseek-chat'
_MAX_DEPTH = 4
_MAX_CHILDREN = 10
_BT = chr(96)  # 反引号，清理 Markdown 代码块标记

_MAP_IDS = ('first-principles', 'pyramid', 'bayes')

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


def _do_analyze(topic, base, model, key):
    topic = str(topic or '').strip()
    if not topic:
        return JSONResponse({'ok': False, 'message': '请先描述你想学什么或想解决的问题'})
    if len(topic) > 800:
        return JSONResponse({'ok': False, 'message': '输入太长了，请控制在 800 字以内'})
    model = str(model or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    base = str(base or _DEFAULT_BASE).strip() or _DEFAULT_BASE
    key = _api_key(key)
    if not key:
        return JSONResponse({'ok': False, 'message': '缺少 API Key：请在页面「模型设置」里填写，或设置环境变量 DEEPSEEK_API_KEY'})

    started = time.time()
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': '我想学习 / 想弄明白的是：' + topic},
        ],
        'temperature': 0.4,
        'max_tokens': 6000,
        'stream': False,
    }
    raw = ''
    try:
        resp = requests.post(_endpoint(base), json=body, headers=headers, timeout=(15, 240))
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
    base = payload.get('base') or ''
    model = payload.get('model') or ''
    key = payload.get('key') or ''
    return await run_in_threadpool(_do_analyze, topic, base, model, key)