# -*- coding: utf-8 -*-
# 库博学习圈（Kolb's Experiential Learning Cycle）
# ------------------------------------------------------------------
# 把一次真实经历走完一圈，榨出一条能用在别处的经验原则，再逼自己变成
# 下一个具体动作：
#   ① 具体经验 → ② 反思观察 → ③ 抽象概念化 → ④ 主动实验
#
# 页面在 /kolb，数据落在 SQLite 的 history 表（tool = 'kolb'），一行 = 一个圈。
#
# 和工具箱里其它工具的分工：
#   苏格拉底提问 → 把一件事想清楚（从不给答案）
#   5Why 分析法  → 一个问题的根因
#   判断力教练   → 一次决策的把握准不准
#   库博学习圈   → 一次经历里到底学到了什么，并且逼你去验证
#
# 两档（追问次数由前端按档位卡，服务端只保证每次只问一个问题）：
#   快速 quick → 每段最多追问 1 次，三五分钟能走完
#   深入 deep  → 每段最多追问 3 次
#
# 「主动实验」能一键送进判断力教练（POST /judgement-trainer/api/cases），
# 到期那边对完答案，这一页会把「已验证 / 被推翻」回显出来 —— 闭环就是这么合的。
#
# 环境变量：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
import re
import time
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import storage

try:
    from model_config import llm_endpoint, llm_key, llm_model
except Exception:  # 单独跑这个文件时的兜底
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'kolb.html'

TOOL = 'kolb'
LIST_MAX = 60          # 一次最多读回多少个圈
TITLE_MAX = 60         # 一句话概括这次经历
ANS_MAX = 600          # 每一段自己的结论
MSG_MAX = 700          # 单条追问 / 回答
HIST_MAX = 8           # 一次塞给模型的最近几条来回
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

DOMAINS = ('work', 'life', 'learn')
DOMAIN_NAME = {'work': '工作决策', 'life': '人际', 'learn': '学习'}
MODES = ('quick', 'deep')
PROBES_MAX = {'quick': 1, 'deep': 3}
ANSWERS = ('ce', 'ro', 'ac', 'ac_edge', 'ae_trigger', 'ae_action', 'ae_due', 'ae_check')
ANSWER_LABEL = {
    'ce': '具体经验', 'ro': '反思观察', 'ac': '经验原则', 'ac_edge': '边界',
    'ae_trigger': '触发条件', 'ae_action': '要做的事', 'ae_due': '验证日期',
    'ae_check': '怎么算有效',
}

NO_KEY = '服务端没配模型 Key（DEEPSEEK_API_KEY），配好之后再来。'

STAGES = [
    {'key': 'ce', 'name': '具体经验',
     'tip': '只写能被人看到、能量到的：你实际做了什么，结果是什么。写「他不上心」这种解释不算。'},
    {'key': 'ro', 'name': '反思观察',
     'tip': '回头看：哪些是事实、别人什么反应、你当时身体什么感觉、哪部分其实是你可控的。'},
    {'key': 'ac', 'name': '抽象概念化',
     'tip': '从这件事里说一条能用在别处的规律，再补一句：什么时候它不成立。'},
    {'key': 'ae', 'name': '主动实验',
     'tip': '把它变成一个下次能做的动作：遇到什么就做什么，多久内验证，怎么算有效。'},
]
STAGE_INDEX = {s['key']: i + 1 for i, s in enumerate(STAGES)}
STAGE_GOAL = {
    'ce': '逼他写可观察的现象和动作。他要是写「因为他不配合 / 不上心」，把他拉回「你看到他做了什么」。',
    'ro': '逼他分清事实和他当时的解释。问他：别人是什么反应？他身体什么感觉？哪部分是他可控的？',
    'ac': '逼他把这件事提炼成一条能迁移的规律，句式「当__时，如果我__，就更可能__」，并让他说出这条规律的边界。',
    'ae': '逼他把规律变成一个具体动作：触发条件、具体做法、多久能验证、怎么算有效。',
}

ROLE = [
    '你在当「库博学习圈」的陪练。库博圈只有四段：具体经验 → 反思观察 → 抽象概念化 → 主动实验。',
    '你的活只有一件：针对他刚写的东西，问一个能把他逼得更具体的问题。',
    '【规矩】',
    '1. 一次只问一个问题 —— 整段回复最多出现一个问号，两三句以内。',
    '2. 不给答案、不给建议、不评价对错，不说「你说得很好」「我理解你」这类客套。',
    '3. 只用他写过的东西问。他没写的，就问他，绝不替他补细节。',
    '4. 他要求你直接给结论时，温和挡回去，然后接着问当前这个问题。',
    '5. 平实、口语。不用「本质」「范式」「认知」「赋能」这类词。',
    '6. 只输出你说的那段话本身。不要标题、不要序号、不要 JSON。',
]

CONDENSE_SYS = [
    '你在当「库博学习圈」的整理者。他会把这一圈里自己写过的东西给你。',
    '你的活：只用他自己写过的话，把里面的规律收成一句话，句式是「当____时，如果我____，就更可能____」。',
    '【规矩】',
    '1. 只准用他写过的内容，一个字都不要加他没说过的细节。',
    '2. 缺哪半截，就把那半截写成「（这里你还没写）」，不要替他编。',
    '3. 一句话，不超过 50 个字，不要问号，不要解释，不要加引号。',
    '4. 只输出这一句话。',
]


# ---------------- 小工具 ----------------
def _user(request: Request) -> str:
    """LoginGate 中间件把登录用户写在 request.state.user 上。"""
    return getattr(request.state, 'user', '') or ''


async def _body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _clean(value, limit: int) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _date(value):
    text = str(value or '').strip()[:10]
    return text if DATE_RE.match(text) else None


def _stage_num(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(5, n))


def _one_q(text: str) -> str:
    """整段只留到第一个问号为止 —— 「一次只问一个问题」这条规矩得有人守。"""
    for i, ch in enumerate(text):
        if ch in '？?':
            return text[:i + 1].strip()
    return text.strip()


def _norm_msgs(raw) -> list:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[-40:]:
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        if role not in ('ai', 'user'):
            continue
        text = _clean(item.get('text'), MSG_MAX)
        if not text:
            continue
        out.append({
            'stage': _stage_num(item.get('stage')),
            'role': role,
            'text': text,
            'at': _clean(item.get('at'), 24),
        })
    return out[-HIST_MAX * 4:]


def _merge_answers(current: dict, raw) -> bool:
    """白名单合并：只有明确传过来的键才动，空串 = 删掉这个键。"""
    if not isinstance(raw, dict):
        return False
    changed = False
    for key in ANSWERS:
        if key not in raw:
            continue
        value = _date(raw.get(key)) if key == 'ae_due' else _clean(raw.get(key), ANS_MAX)
        if value:
            if current.get(key) != value:
                current[key] = value
                changed = True
        elif key in current:
            current.pop(key)
            changed = True
    return changed


def _norm_jt(raw):
    if not isinstance(raw, dict):
        return None
    try:
        rid = int(raw.get('id'))
    except (TypeError, ValueError):
        return None
    if rid <= 0:
        return None
    return {'id': rid, 'due': _date(raw.get('due')) or ''}


def _norm_cycle(raw):
    """新建一圈：洗干净，洗不干净就拒掉。"""
    if not isinstance(raw, dict):
        return None
    title = _clean(raw.get('title'), TITLE_MAX)
    if len(title) < 2:
        return None
    answers = {}
    _merge_answers(answers, raw.get('a'))
    if 'ce' not in answers:
        answers['ce'] = title        # 起手那句话就是第一段的种子，不用再抄一遍
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    return {
        'title': title,
        'domain': raw.get('domain') if raw.get('domain') in DOMAINS else 'work',
        'mode': raw.get('mode') if raw.get('mode') in MODES else 'quick',
        'stage': 1,
        'a': answers,
        'msgs': _norm_msgs(raw.get('msgs')),
        'jt': None,
        'finished': False,
        'createdAt': now,
        'updatedAt': now,
    }


def _apply_patch(data: dict, patch) -> bool:
    """改一圈：只认白名单字段，别让前端整包覆盖。"""
    if not isinstance(patch, dict):
        return False
    changed = False
    if 'title' in patch:
        title = _clean(patch.get('title'), TITLE_MAX)
        if title and title != data.get('title'):
            data['title'] = title
            changed = True
    if patch.get('domain') in DOMAINS and patch['domain'] != data.get('domain'):
        data['domain'] = patch['domain']
        changed = True
    if patch.get('mode') in MODES and patch['mode'] != data.get('mode'):
        data['mode'] = patch['mode']
        changed = True
    if 'stage' in patch and _stage_num(patch['stage']) != data.get('stage'):
        data['stage'] = _stage_num(patch['stage'])
        changed = True
    if 'a' in patch and _merge_answers(data.setdefault('a', {}), patch['a']):
        changed = True
    if 'msgs' in patch:
        msgs = _norm_msgs(patch['msgs'])
        if msgs != data.get('msgs'):
            data['msgs'] = msgs
            changed = True
    if 'jt' in patch:
        jt = _norm_jt(patch['jt'])
        if jt != data.get('jt'):
            data['jt'] = jt
            changed = True
    if 'finished' in patch and bool(patch['finished']) != data.get('finished'):
        data['finished'] = bool(patch['finished'])
        changed = True
    if changed:
        data['updatedAt'] = time.strftime('%Y-%m-%d %H:%M:%S')
    return changed


def _cycle_out(row: dict) -> dict:
    """把库里的行摊平：payload 的字段提到顶层，前端只认一层。"""
    data = dict(row.get('payload') or {})
    data.setdefault('a', {})
    data.setdefault('msgs', [])
    data['id'] = row['id']
    data['created_at'] = row.get('created_at', '')
    return data


def _jt_state(user: str, jt) -> str:
    """读一眼关联的那条判断力教练记录，看对完答案没有。"""
    if not isinstance(jt, dict) or not jt.get('id'):
        return ''
    try:
        row = storage.get_record(user, int(jt['id']))
    except (storage.StorageUnavailable, TypeError, ValueError):
        return ''
    if not row or row.get('tool') != 'judgement-trainer':
        return ''
    outcome = (row.get('payload') or {}).get('outcome')
    if outcome == 'yes':
        return 'yes'
    if outcome == 'no':
        return 'no'
    return 'open'

# ---------------- 模型 ----------------
def _chat(system: str, user_text: str, max_tokens=500, temperature=0.7):
    key = llm_key()
    if not key:
        return None, NO_KEY
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user_text}],
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


def _ask_model(system: str, user_text: str, single_question=True):
    """模型偶尔吐一串空白（实测过），空就再试一次。"""
    last = ''
    for _ in range(2):
        text, err = _chat(system, user_text)
        text = _clean(text, MSG_MAX)
        if text:
            return (_one_q(text) if single_question else text), ''
        last = err or ''
    return None, last


def _probe_system(stage: str, mode: str) -> str:
    num = STAGE_INDEX.get(stage) or 1
    lines = list(ROLE)
    lines += [
        '',
        '【现在这一段】第 %d 段 · %s' % (num, STAGES[num - 1]['name']),
        '这一段要问清楚的：' + STAGE_GOAL.get(stage, ''),
    ]
    if mode == 'quick':
        lines.append('【档位】快速档：这是这一段唯一的一次追问，挑最关键的那个问题问。')
    else:
        lines.append('【档位】深入档：他答完你可能还有机会再问，一次也别贪多。')
    return '\n'.join(lines)


def _filled(answers: dict) -> str:
    out = []
    for key in ANSWERS:
        value = _clean(answers.get(key), ANS_MAX)
        if value:
            out.append('· %s：%s' % (ANSWER_LABEL[key], value))
    return '\n'.join(out)


def _probe_input(stage: str, answers: dict, text: str, msgs: list) -> str:
    parts = []
    done = _filled(answers)
    if done:
        parts.append('【他前面已经写下的】\n' + done)
    recent = [m for m in msgs if m.get('stage') == STAGE_INDEX.get(stage)][-HIST_MAX:]
    if recent:
        lines = ['%s：%s' % ('你' if m['role'] == 'ai' else '他', m['text']) for m in recent]
        parts.append('【这一段刚才的来回】\n' + '\n'.join(lines))
    parts.append('【他刚写下的】\n' + text)
    return '\n\n'.join(parts)


def _condense_input(answers: dict, title: str) -> str:
    body = _filled(answers)
    if title and title not in body:
        body = '· 这次经历：' + title + ('\n' + body if body else '')
    return '【他这一圈写下的东西】\n' + (body or '（他什么都还没写）')


# ---------------- 页面 ----------------
@router.get('/kolb', response_class=HTMLResponse)
def kolb_page() -> HTMLResponse:
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="font-family:system-ui"><h2>模板文件缺失</h2>'
                '<p>请确认 templates/kolb.html 存在。</p></body></html>')
    return HTMLResponse(html)


# ---------------- 记录：五件套 ----------------
@router.get('/kolb/api/cycles')
def kolb_list(request: Request) -> JSONResponse:
    user = _user(request)
    try:
        rows = storage.list_records(TOOL, user, LIST_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)}, status_code=503)
    items = []
    for row in rows:
        item = _cycle_out(row)
        item['jtState'] = _jt_state(user, item.get('jt'))
        items.append(item)
    return JSONResponse({'ok': True, 'items': items})


@router.post('/kolb/api/cycles')
async def kolb_create(request: Request) -> JSONResponse:
    data = _norm_cycle(await _body(request))
    if not data:
        return JSONResponse({'ok': False, 'message': '先写一句「这次发生了什么」，至少两个字。'},
                            status_code=400)
    try:
        rec = storage.add_record(TOOL, _user(request), data['title'], data)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    data['id'] = rec['id']
    data['created_at'] = rec['created_at']
    data['jtState'] = ''
    return JSONResponse({'ok': True, 'item': data})


@router.get('/kolb/api/cycles/{rid}')
def kolb_one(rid: int, request: Request) -> JSONResponse:
    try:
        row = storage.get_record(_user(request), rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'message': '找不到这个圈。'}, status_code=404)
    item = _cycle_out(row)
    item['jtState'] = _jt_state(_user(request), item.get('jt'))
    return JSONResponse({'ok': True, 'item': item})


@router.patch('/kolb/api/cycles/{rid}')
async def kolb_update(rid: int, request: Request) -> JSONResponse:
    patch = (await _body(request)).get('patch')
    if not isinstance(patch, dict):
        return JSONResponse({'ok': False, 'message': '没有要改的内容。'}, status_code=400)
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return JSONResponse({'ok': False, 'message': '找不到这个圈。'}, status_code=404)
        data = row['payload'] if isinstance(row['payload'], dict) else {}
        changed = _apply_patch(data, patch)
        if changed:
            title = _clean(data.get('title'), TITLE_MAX) or '学习圈'
            affected = storage.update_record(user, rid, payload=data, title=title)
            if not affected:
                return JSONResponse({'ok': False, 'message': '这条不属于当前账号。'}, status_code=403)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    item = dict(data)
    item['id'] = rid
    item['created_at'] = row.get('created_at', '')
    item['jtState'] = _jt_state(user, item.get('jt'))
    return JSONResponse({'ok': True, 'item': item})


@router.delete('/kolb/api/cycles/{rid}')
def kolb_delete(rid: int, request: Request) -> JSONResponse:
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return JSONResponse({'ok': False, 'message': '找不到这个圈。'}, status_code=404)
        affected = storage.delete_record(user, rid)
        if not affected:
            return JSONResponse({'ok': False, 'message': '这条不属于当前账号。'}, status_code=403)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    return JSONResponse({'ok': True})


# ---------------- AI：追问 / 收成一句原则 ----------------
@router.post('/kolb/api/ask')
async def kolb_ask(request: Request) -> JSONResponse:
    payload = await _body(request)
    task = payload.get('task') if payload.get('task') in ('probe', 'condense') else 'probe'
    mode = payload.get('mode') if payload.get('mode') in MODES else 'quick'
    stage = payload.get('stage') if payload.get('stage') in STAGE_INDEX else 'ce'
    text = _clean(payload.get('text'), ANS_MAX)
    msgs = _norm_msgs(payload.get('msgs'))
    answers = {}
    _merge_answers(answers, payload.get('a'))
    title = _clean(payload.get('title'), TITLE_MAX)

    if not llm_key():
        return JSONResponse({'ok': False, 'message': NO_KEY}, status_code=503)

    if task == 'condense':
        system = '\n'.join(CONDENSE_SYS)
        user_text = _condense_input(answers, title)
        single = False
    else:
        if not text:
            return JSONResponse({'ok': False, 'message': '先把你这一段想写的写上，再来问。'},
                                status_code=400)
        system = _probe_system(stage, mode)
        user_text = _probe_input(stage, answers, text, msgs)
        single = True

    reply, err = await run_in_threadpool(_ask_model, system, user_text, single)
    if not reply:
        return JSONResponse({'ok': False, 'message': err or '模型没给出内容，再试一次。'},
                            status_code=502)
    return JSONResponse({'ok': True, 'text': reply, 'stage': stage, 'task': task})