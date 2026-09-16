# -*- coding: utf-8 -*-
# 苏格拉底提问：把你已经认定的一句话，用六类问题追问到底，
# 逼出那些你从来没说出口的前提。
#
# 出题有两条路：
#   1. 大模型针对你的原话现出题（更贴，需要服务端配好模型 Key）
#   2. 内置题库：六类各 6 问，共 36 问，不联网、不花时间（模型不可用时自动兜底）
#
# 记录全部存在浏览器 localStorage（soc_ 前缀），后端不落库。
import json
import re
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

try:
    from model_config import llm_endpoint, llm_key, llm_model
except Exception:  # 单独拷贝本文件运行时不带配置也能用（只是走题库）
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'socratic.html'

# =========================================================
# 六类问题：每一类负责一种「追问动作」
# =========================================================
LENSES = [
    {
        'key': 'clarify', 'label': '澄清', 'icon': '🔍',
        'hint': '把你嘴里的那个词，拧成一个具体的东西',
        'bank': [
            '「{c}」里最关键的那个词，你能不能用一句不带形容词的话说清楚？',
            '这件事要做到什么程度，你才觉得「成了」？',
            '同一个说法，你身边的人会不会理解成另一件事？',
            '如果必须举一个具体的例子，你会举哪一个？',
            '这句话里有没有哪个词，是你其实没想清楚就先用上的？',
            '把这句话改成一句别人能验证的话，你会怎么写？',
        ],
    },
    {
        'key': 'assume', 'label': '前提', 'icon': '🧱',
        'hint': '挖出那些你默认成立、从没说出口的东西',
        'bank': [
            '这个结论要成立，得先默认哪件事是真的？',
            '这些前提里，哪一条你从来没有检验过？',
            '如果其中一条不成立，你的结论会变成什么？',
            '你默认了别人会怎么反应？这个默认有依据吗？',
            '你是不是默认了「现在的情况会一直持续下去」？',
            '这条前提是你自己想出来的，还是从别人那里顺来的？',
        ],
    },
    {
        'key': 'evidence', 'label': '证据', 'icon': '⚖️',
        'hint': '把「我就是这么觉得」换成能拿出来的东西',
        'bank': [
            '你是怎么知道这一点的？亲眼见过，还是听来的？',
            '支持它最硬的那条证据是什么？够硬吗？',
            '有没有哪一次经历，其实是在反对这个结论？',
            '换一个人来查，他会先去看什么？',
            '你判断「有效」用的是哪个标准？这个标准本身可能有问题吗？',
            '你是在看事实，还是在看你想看到的那部分？',
        ],
    },
    {
        'key': 'viewpoint', 'label': '视角', 'icon': '🔄',
        'hint': '换一双眼睛，看看同一件事长什么样',
        'bank': [
            '最不同意这句话的人，他会怎么说？',
            '如果换成你的对手、家人，或者五年后的你，他会先问什么？',
            '有没有一种解释，能同时说通你和反对你的人看到的现象？',
            '你这个立场里，有多少是身份带来的（职业、年龄、圈子）？',
            '换个行业、换个年代的人来看，这句话还成立吗？',
            '你更希望它成立，还是更想知道它到底成不成立？',
        ],
    },
    {
        'key': 'implication', 'label': '推演', 'icon': '🌊',
        'hint': '顺着它往前推，看看会走到哪里',
        'bank': [
            '如果它成立，接下来必然会发生什么？你准备好了吗？',
            '如果它不成立，你要付出什么代价？',
            '照这个结论做下去，一年后你会站在哪里？',
            '这个结论和你另外一些坚持，互相矛盾吗？',
            '最坏的情况是什么？你能承受吗？',
            '如果把它推到极端，会得出什么荒唐的结果？',
        ],
    },
    {
        'key': 'meta', 'label': '反思', 'icon': '🪞',
        'hint': '回过头问一句：我为什么在问这个问题',
        'bank': [
            '你为什么是现在问这个问题？是不是有更急的事被绕开了？',
            '你想要的其实是一个答案，还是一个许可？',
            '如果没有任何人能回答你，你会怎么做？',
            '这个问题，三个月前的你会怎么问？',
            '你是在解决它，还是在反复想它？',
            '假设明天这个问题消失了，你的生活哪里会不一样？',
        ],
    },
]

_LENS_BY_KEY = dict((x['key'], x) for x in LENSES)
_LENS_BY_LABEL = dict((x['label'], x) for x in LENSES)

_MAX_PER_LENS = 2  # 每类最多几问


def _short_claim(claim, limit=16):
    c = re.sub(r'\s+', '', claim or '')
    if len(c) <= limit:
        return c
    return c[:limit] + '…'


def _fill(tpl, claim):
    raw = re.sub(r'\s+', '', claim or '')
    if '{c}' not in tpl:
        return tpl
    if len(raw) > 24:  # 原话太长，塞进句子里会拗口，退成代词
        return tpl.replace('「{c}」', '你这句话').replace('{c}', '你这句话')
    return tpl.replace('{c}', _short_claim(claim))


def _bank_questions(claim, lens_keys, seed=0):
    """从题库里按透镜取题。seed 用来「换一组」。"""
    picked = []
    for lens in LENSES:
        if lens['key'] not in lens_keys:
            continue
        bank = lens['bank']
        start = (seed * 2 + LENSES.index(lens)) % len(bank)
        for i in range(_MAX_PER_LENS):
            tpl = bank[(start + i * 3) % len(bank)]
            picked.append({'cat': lens['label'], 'icon': lens['icon'],
                           'q': _fill(tpl, claim)})
    return picked


def _lens_catalog(lens_keys):
    names = [x['label'] for x in LENSES if x['key'] in lens_keys]
    return names or [x['label'] for x in LENSES]


def _system_prompt(lens_keys):
    names = '、'.join(_lens_catalog(lens_keys))
    return '\n'.join([
        '你是苏格拉底式的追问者，只提问，不给答案。',
        '用户会写下一句他此刻认定的结论或判断，你要用六类问题追问它，',
        '帮他看见自己没说出口的前提。',
        '固定六类：澄清（把词变具体）、前提（挖默认假设）、证据（要依据）、',
        '视角（换立场）、推演（往前推结果）、反思（反问问题本身）。',
        '规则：',
        '1. 本轮只需要这几类：' + names + '；每类恰好 ' + str(_MAX_PER_LENS) + ' 问；',
        '2. 每个问题都要扣住用户原话的具体内容，不许出现「你的依据是什么」这类',
        '   放到任何话题上都成立的空话；',
        '3. 一问只问一件事，不超过 40 字，主语用「你」；',
        '4. 不给建议，不回答这个问题本身，不评价对错，不复述他的原话；',
        '5. 只输出 JSON，不要 Markdown 代码块，格式：',
        '{"questions":[{"cat":"澄清","q":"..."},{"cat":"前提","q":"..."}]}',
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


def _ask_llm(claim, lens_keys):
    """让大模型针对原话出题。返回 (题目列表, 失败原因)。"""
    key = llm_key()
    if not key:
        return None, '模型未配置，本次用题库'
    body = {
        'model': llm_model(),
        'messages': [
            {'role': 'system', 'content': _system_prompt(lens_keys)},
            {'role': 'user', 'content': '我现在的结论：' + claim},
        ],
        'temperature': 0.7,
        'max_tokens': 1600,
        'stream': False,
    }
    try:
        resp = requests.post(
            llm_endpoint(), json=body, timeout=(15, 90),
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
        )
    except requests.RequestException as exc:
        return None, '模型请求失败，本次用题库：' + str(exc)[:80]
    if resp.status_code >= 400:
        return None, '模型返回错误 ' + str(resp.status_code) + '，本次用题库'
    try:
        raw = resp.json()['choices'][0]['message']['content']
    except (ValueError, KeyError, IndexError, TypeError):
        return None, '模型返回格式异常，本次用题库'

    rows = _extract_json(raw).get('questions')
    if not isinstance(rows, list):
        return None, '模型返回的内容解析不了，本次用题库'

    per_lens = {}
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lens = _LENS_BY_LABEL.get(str(row.get('cat') or '').strip())
        q = str(row.get('q') or '').strip()
        if lens is None or lens['key'] not in lens_keys or len(q) < 6:
            continue
        used = per_lens.get(lens['key'], 0)
        if used >= _MAX_PER_LENS:
            continue
        per_lens[lens['key']] = used + 1
        out.append({'cat': lens['label'], 'icon': lens['icon'], 'q': q})
    if len(out) < 3:
        return None, '模型给出的题目太少，本次用题库'
    return out, ''


def _normalize_lenses(raw):
    if not isinstance(raw, list):
        return [x['key'] for x in LENSES]
    keys = [str(x) for x in raw if str(x) in _LENS_BY_KEY]
    return keys or [x['key'] for x in LENSES]


def _ask(payload):
    claim = str(payload.get('claim') or '').strip()
    if len(claim) < 2:
        return JSONResponse({'ok': False, 'message': '先写下一句你现在的看法'})
    lens_keys = _normalize_lenses(payload.get('lenses'))
    mode = str(payload.get('mode') or 'auto')
    try:
        seed = int(payload.get('seed') or 0)
    except (TypeError, ValueError):
        seed = 0

    note = ''
    questions = None
    if mode != 'bank':
        questions, note = _ask_llm(claim, lens_keys)
    source = 'llm' if questions else 'bank'
    if not questions:
        questions = _bank_questions(claim, lens_keys, seed)
    return JSONResponse({'ok': True, 'source': source, 'note': note,
                         'count': len(questions), 'questions': questions})


@router.get('/socratic', response_class=HTMLResponse)
def socratic_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/socratic.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.post('/socratic/api/ask')
async def socratic_ask(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    # 调大模型是同步阻塞请求，放线程池执行，别堵住事件循环
    return await run_in_threadpool(_ask, payload)


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='苏格拉底提问', description='六类追问 · 把结论追问到底', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8005)