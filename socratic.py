# -*- coding: utf-8 -*-
# 苏格拉底提问：一个只会提问、不给答案的对话引导者。
# 页面在 /socratic。
# ------------------------------------------------------------
# 这一版和以前不一样。以前是「生成一张问题清单，你自己挨个答」，
# 现在是真的对话：模型一次只问一个问题，按固定的四步走，走完一步才进下一步。
#
# 四步（不许跳步）：
#   1 澄清问题   你到底在纠结什么，把含糊的词问具体
#   2 已有信息   你知道什么、哪些是事实、哪些是没验证过的猜测
#   3 换个视角   你还没想到的角度、别人的立场
#   4 推导结论   让你自己说出打算怎么做
#
# 进度（第几步）是服务端定的，不是模型自己说了算：
#   · 每轮先发一个极小的「判够了没有」调用（max_tokens=6，几百毫秒），再决定这轮说什么。
#     顺序不能反：判定决定这一轮是继续问还是收尾，而收尾是另一段完全不同的文字 ——
#     并行跑的话正文可能白生成一场，流式下更会变成「刚读完一段就被换掉」；
#   · 一步至少问 2 个问题、最多 3 个，到点就往下走，绝不跳步、绝不倒退；
#   · 第 4 步走完那天，正文换成一段收尾——只用他自己说过的话串，不加任何建议。
#   为什么不直接让模型吐 JSON 带进度：实测它在多轮对话里不守格式，
#   偶发吐一串空格。宁可多花一次小调用，也不让它把话说坏。
#
# 数据落在 SQLite 的 history 表（tool = 'socratic'），一行 = 一个会话：
#   payload = {ask, step, turns, finished, msgs:[{role,text,at,step}], started_at, updated_at}
# 刷新页面、换台设备回来都还能接着聊。右侧历史列表读的就是这张表。
#
# 回复是流式的（SSE，text/event-stream）：模型边写边推，生成结束才落库 ——
# 前端就算中途断开，这一轮也已经存下来了。
# 「一条回复只准一个问号」这条规矩是边流边守的，不是事后改写：看到第一个问号后先按住不发，
# 再冒出第二个问号就把按住的那段整段丢掉。这样「用户读到的」和「存进库的」永远是同一段话。
#
# 环境变量：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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

import requests

router = APIRouter()

TOOL = 'socratic'
ASK_MAX = 200          # 原始那件事
MSG_MAX = 800          # 单条回复
REPLY_MAX = 1000       # 用户一次发言
HIST_MAX = 40          # 一次塞给模型的最近几条
LIST_MAX = 80          # 右侧历史最多列几条
MIN_TURNS = 2          # 一步至少问几个问题
FORCE_TURNS = 3        # 问到第几个就必须往下走

STEPS = ['澄清问题', '已有信息', '换个视角', '推导结论']
STEP_GOAL = [
    '他到底在纠结什么，把含糊的词问具体，排除歧义',
    '他手上已经知道什么、哪些是事实、哪些是他默认成立但没验证过的猜测',
    '帮他看到自己没想到的角度、别人的立场、相反的可能性',
    '让他自己把前面聊的串起来，说出他打算怎么做',
]

NO_KEY = ('服务端没配模型 Key（DEEPSEEK_API_KEY）。这个工具全靠模型对话，'
          '配好之后再来 —— 在项目根目录的 .env 里填上就行。')

ROLE = [
    '你在当一位「苏格拉底式提问引导者」。你的工作只有一个：用提问帮对方自己想清楚。',
    '你永远不给答案、不给建议、不替他做判断、不告诉他「应该」怎么办。',
    '',
    '【怎么说话】',
    '1. 一条回复只说一小段，两三句以内，而且只问一个问题——整条回复里最多出现一个问号。',
    '   复述对方原话时把他句尾的问号去掉（写「为什么我总拖延」，不要写「为什么我总拖延？」），',
    '   整条回复里那个问号只能是你问他的那一个。',
    '2. 问题要扣住他刚说的那句话里的具体细节。不许问「你怎么看」这种放到哪都成立的空话。',
    '3. 像朋友聊天，平实、口语。不用「本质」「范式」「认知」「赋能」这类词。',
    '4. 不复述他的原话，不评价对错，不说「很好的问题」「我理解你」这种客套。',
    '5. 他要求你直接给答案时，温和拒绝，比如「我不会直接给你答案哦，不过我可以陪你一步步',
    '   理清楚，你自己就能找到答案，我们再往下走一步？」，然后接着问当前这一步的问题。',
    '6. 只输出你要说的那段话本身。不要写 JSON、不要写标题、不要写进度标记。',
]


def _system_prompt(step, finished=False):
    """按当前进度拼这轮的系统提示。step: 1..4 是四步。"""
    lines = list(ROLE)
    lines.append('')
    if step == 0:
        lines += [
            '【现在】这是开场第一句。先用一句话把它变成「我理解你在纠结的是……」，',
            '然后问他「对吗？如果没问题我们就开始一步步梳理」。这一轮先别开始提问。',
        ]
    elif finished:
        lines += [
            '【现在】四步已经走完，你之前也帮他收过尾了。他要是接着说，就顺着他的话',
            '再问一个具体的问题（还在推导结论的范围里），不要退回去重走前面几步。',
        ]
    else:
        lines += [
            '【现在】第 %d 步：%s。' % (step, STEPS[step - 1]),
            '这一步要问清楚的：' + STEP_GOAL[step - 1] + '。',
            '就这一步问一个问题。',
        ]
        if step == 1:
            lines.append('他如果说你理解错了，先用一句话重新说一遍你听到的，再往下问。')
    return '\n'.join(lines)


WRAP_SYS = '\n'.join([
    '你是刚才那位「苏格拉底式提问引导者」。四步都走完了，现在只说最后一段话。',
    '只用他自己说过的话，串成一两句还给他，让他看见这是他自己得出来的结论。',
    '一个字都不要加你自己的判断、建议、点评或鼓励。不要提问，不要问他「你觉得呢」。',
    '平实、口语，像朋友把他刚才说的话还给他。',
])

JUDGE_SYS = '\n'.join([
    '你只做一件事：判断一场对话里，引导者当前这一步有没有问到位。',
    '只回答「够了」或者「没够」这两个词之一，不要解释，不要标点，不要别的字。',
])


def _judge_prompt(step):
    return '\n'.join([
        '引导者正在做第 %d 步：%s。' % (step, STEPS[step - 1]),
        '这一步要问清楚的是：' + STEP_GOAL[step - 1] + '。',
        '看下面这段对话，重点是对方最新的回答。',
        '如果这一步已经问清楚了、可以进入下一步，回答「够了」；',
        '如果还差得远、需要再问，回答「没够」。',
    ])


def _chat(system, messages, max_tokens=900, temperature=0.7):
    key = llm_key()
    if not key:
        return None, NO_KEY
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system}] + list(messages),
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    try:
        resp = requests.post(llm_endpoint(), json=body, timeout=(10, 120),
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


def _chat_text(system, messages, tries=2, **kw):
    """拿一段能用的文本。模型偶尔会吐一串空白（实测过），空就再试一次。"""
    last = ''
    for _ in range(max(1, tries)):
        text, err = _chat(system, messages, **kw)
        text = _clean(text, MSG_MAX)
        if text:
            return text, ''
        last = err or ''
    return None, last


def _chat_stream(system, messages, max_tokens=900, temperature=0.7):
    """流式拿一段文本：逐块 yield (片段, 错误)。

    只在出错时 yield 非空的错误，而且错误一定排在最后 —— 正常情况一个错都不出。
    """
    key = llm_key()
    if not key:
        yield '', NO_KEY
        return
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system}] + list(messages),
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': True,
    }
    resp = None
    try:
        resp = requests.post(llm_endpoint(), json=body, timeout=(10, 120),
                             headers={'Authorization': 'Bearer ' + key,
                                      'Content-Type': 'application/json'},
                             stream=True)
        if resp.status_code >= 400:
            yield '', '模型返回错误 ' + str(resp.status_code)
            return
        for raw in resp.iter_lines(decode_unicode=False):
            if not raw or not raw.startswith(b'data:'):
                continue
            data = raw[5:].strip()
            if not data:
                continue
            if data == b'[DONE]':
                break
            try:
                obj = json.loads(data.decode('utf-8'))
                piece = obj['choices'][0]['delta'].get('content') or ''
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                continue
            if piece:
                yield piece, ''
    except requests.RequestException as exc:
        yield '', '模型连接断了：' + str(exc)[:80]
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


def _count_q(text):
    return sum(1 for ch in text if ch in '？?')


def _cut_first_q(text):
    """兜底：一条回复里问号超过一个，就砍到第一个问号为止。"""
    for i, ch in enumerate(text):
        if ch in '？?':
            return text[:i + 1].strip()
    return text


def _first_q_at(text):
    for i, ch in enumerate(text):
        if ch in '？?':
            return i
    return -1


def _guard(pieces):
    """守「一条回复只准一个问号」，边流边守。

    看到第一个问号之后，后面的一律先按住不发：
      · 再冒出第二个问号 → 它在多问 → 按住的那段整段丢掉，就此收工；
      · 流自然结束     → 第一个问题本来就是最后一句，把按住的补发出去。

    为什么必须边流边判、不能事后改写：事后再改，意味着用户已经读到的字会被换掉。
    「他读到的」和「存进库的」必须是同一段话。

    这里不看引号 —— 「复述原话时别把对方的问号带进来」是系统提示在约束的
    （开场那句最容易踩），所以这条兜底只会拦到真正多问的情况。
    """
    held = ''
    sealed = False
    for piece, err in pieces:
        if err:
            yield {'err': err}
            return
        if not piece:
            continue
        if sealed:
            held += piece
            if _first_q_at(piece) >= 0:
                return
            continue
        cut = _first_q_at(piece)
        if cut < 0:
            yield {'t': piece}
            continue
        yield {'t': piece[:cut + 1]}
        held = piece[cut + 1:]
        sealed = True
        if _first_q_at(held) >= 0:
            return
    if held:
        yield {'t': held}


def _reply(step, convo, finished=False):
    """生成本轮要说的话。返回 (文本, 错误)。"""
    sys_p = _system_prompt(step, finished)
    msg, err = _chat_text(sys_p, convo)
    if not msg:
        return None, (err or '模型这次没说出话来，再发一次试试。')

    # 一次只许问一个问题：违规就让它改一遍，改不过来才硬砍
    if _count_q(msg) > 1:
        fix = list(convo) + [
            {'role': 'assistant', 'content': msg},
            {'role': 'user',
             'content': '（规则提醒：一条回复只能有一个问号。请把刚才那条改成只问一个问题，'
                        '其余内容保留，直接说你改好的那段话。）'},
        ]
        msg2, _ = _chat_text(sys_p, fix, tries=1)
        if msg2:
            msg = msg2
    if _count_q(msg) > 1:
        msg = _cut_first_q(msg)
    return msg, ''


def _judge(step, convo):
    """这一步问到位没有。判不出来就当没到位（宁可多问一轮）。"""
    text, _err = _chat_text(_judge_prompt(step), convo, max_tokens=6,
                            temperature=0.1)
    return '够了' in _clean(text or '', 20)


WRAP_ASK = ('（系统提示，不是对话内容：四步已经走完了。请把我刚才说过的话串成一两句还给我，'
            '让我看见这是我自己理出来的。不要提问，不要给建议，不要点评。说完就停。）')


def _wrap(convo):
    """最后那段收尾。

    关键：指令要挂在最后一条**用户消息**上，而不是只写在 system 里。
    实测对话一长，模型会顺着「一问一答」的惯性继续提问，
    system 里的规矩它当没看见；挂在他自己那句话后面就老实了。
    收尾里出现问号就重写，两次都带问号就交给兜底。
    """
    ask = [dict(m) for m in (convo or [])]
    if ask and ask[-1].get('role') == 'user':
        ask[-1] = {'role': 'user', 'content': str(ask[-1].get('content') or '') + '\n\n' + WRAP_ASK}
    else:
        ask.append({'role': 'user', 'content': WRAP_ASK})
    err = ''
    for _ in range(2):
        text, err = _chat_text(WRAP_SYS, ask, max_tokens=400, temperature=0.5)
        if text and _count_q(text) == 0:
            return text, ''
    return None, (err or '收尾没写好')


def _wrap_fallback(msgs, last_text):
    """模型写不出收尾时的兜底：直接把他自己说过的话还给他。

    总比把一句提问当收尾强 —— 收尾的规矩是不许再问、不许加建议。
    """
    mine = [m.get('text') for m in msgs if isinstance(m, dict) and m.get('role') != 'ai']
    mine.append(last_text)
    picked = []
    for t in mine:
        t = _clean(t, 40)
        if len(t) >= 10:
            picked.append('「%s」' % t)
    body = '；'.join(picked[-3:])
    if not body:
        return '这四步走下来，话都是你自己说的。'
    return ('这是你自己说的：%s。结论是你自己理出来的，我没有替你加一个字。'
            % body)[:MSG_MAX]


def _user(request):
    return getattr(request.state, 'user', '') or ''


async def _body(request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _clamp_step(raw):
    try:
        n = int(raw or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, len(STEPS)))


def _convo(plan, text):
    """把存下来的对话拼成给模型看的消息列表。"""
    out = [{'role': 'user', 'content': '我想理清的是：' + _clean(plan.get('ask'), ASK_MAX)}]
    msgs = [m for m in (plan.get('msgs') or []) if isinstance(m, dict)]
    for m in msgs[-HIST_MAX:]:
        t = _clean(m.get('text'), MSG_MAX)
        if not t:
            continue
        out.append({'role': 'assistant' if m.get('role') == 'ai' else 'user', 'content': t})
    if text:
        out.append({'role': 'user', 'content': text})
    return out, msgs


def _pack(rid, title, plan, full=True):
    """把库里的 payload 变成前端要的形状。full=False 时不带消息（列表用）。"""
    msgs = plan.get('msgs') if isinstance(plan.get('msgs'), list) else []
    out = {
        'id': rid,
        'title': title or '',
        'ask': _clean(plan.get('ask'), ASK_MAX),
        'step': _clamp_step(plan.get('step')),
        'finished': bool(plan.get('finished')),
        'started_at': plan.get('started_at') or '',
        'updated_at': plan.get('updated_at') or '',
        'count': len(msgs),
    }
    if full:
        rows = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            text = _clean(m.get('text'), MSG_MAX)
            if not text:
                continue
            rows.append({'role': 'ai' if m.get('role') == 'ai' else 'me',
                         'text': text, 'at': _clean(m.get('at'), 20),
                         'step': m.get('step') if isinstance(m.get('step'), int) else 0})
        out['msgs'] = rows
    return out


def _row(row, full=True):
    plan = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    return _pack(row.get('id'), row.get('title'), plan, full)


# =============================================================
# 接口
# =============================================================
def _do_create(ask, user):
    if not llm_key():
        return JSONResponse({'ok': False, 'message': NO_KEY}, status_code=503)
    convo = [{'role': 'user', 'content': '我想理清的是：' + ask}]
    msg, err = _reply(0, convo)
    if not msg:
        return JSONResponse({'ok': False, 'message': err}, status_code=502)

    now = storage.now_str()
    plan = {
        'ask': ask,
        'step': 1,
        'turns': 0,
        'finished': False,
        'msgs': [{'role': 'ai', 'text': msg, 'at': now, 'step': 0}],
        'started_at': now,
        'updated_at': now,
    }
    try:
        rec = storage.add_record(TOOL, user, ask[:60], plan)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    return JSONResponse({'ok': True, 'session': _pack(rec['id'], ask[:60], plan)})


def _sse(obj):
    """SSE 一行。用 JSON 包一层，省得前端自己猜边界。"""
    return 'data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n'


def _stream_reply(sid, text, user, row):
    """一轮回复的流式版本。生成结束才落库。

    顺序：先判定 → 再决定这轮说什么 → 边流边发 → 存。
    判定必须先跑：它决定这一轮是继续问还是收尾，而这是两段完全不同的文字。
    """
    plan = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    plan = dict(plan)
    step = _clamp_step(plan.get('step'))
    finished = bool(plan.get('finished'))
    try:
        turns = max(0, int(plan.get('turns') or 0))
    except (TypeError, ValueError):
        turns = 0
    convo, msgs = _convo(plan, text)

    enough = False if finished else _judge(step, convo)

    if finished:
        nxt, nturns, nfin, mode = step, turns, True, 'text'
    else:
        turns += 1
        move = (turns >= MIN_TURNS) and (bool(enough) or turns >= FORCE_TURNS)
        if move and step >= len(STEPS):
            nxt, nturns, nfin, mode = len(STEPS), 0, True, 'wrap'
        elif move:
            nxt, nturns, nfin, mode = step + 1, 0, False, 'text'
        else:
            nxt, nturns, nfin, mode = step, turns, False, 'text'

    sent = []
    err = ''

    if mode == 'wrap':
        # 收尾是另一段文字，而且规矩是「一句都不许再问」，本来就短 ——
        # 生成完一次性发出去，落地的分量比一个字一个字蹦出来更足。
        msg, _werr = _wrap(convo)
        if not msg:
            msg = _wrap_fallback(msgs, text)
        sent.append(msg)
        yield _sse({'t': msg})
    else:
        for ev in _guard(_chat_stream(_system_prompt(step, finished), convo)):
            if 'err' in ev:
                err = ev['err']
                break
            sent.append(ev['t'])
            yield _sse({'t': ev['t']})

    msg = ''.join(sent)
    if not msg:
        # 一个字都没出来：什么都不写库，前端会把草稿还给他
        yield _sse({'err': err or '模型这次没说出话来，再发一次试试。'})
        return

    now = storage.now_str()
    msgs = list(msgs)
    msgs.append({'role': 'me', 'text': text, 'at': now, 'step': step})
    msgs.append({'role': 'ai', 'text': msg, 'at': now, 'step': nxt})
    plan['msgs'] = msgs
    plan['step'] = nxt
    plan['turns'] = nturns
    plan['finished'] = nfin
    plan['updated_at'] = now
    try:
        storage.update_record(user, sid, payload=plan)
    except storage.StorageUnavailable as exc:
        yield _sse({'err': str(exc)})
        return

    if err:
        # 说了一半断的：这段照样存，但告诉他是残缺的
        yield _sse({'cut': '模型中途断了，这次只说出半段：' + err})
    yield _sse({'done': 1, 'id': sid})


@router.get('/socratic', response_class=HTMLResponse)
def socratic_page():
    from pathlib import Path
    path = Path(__file__).resolve().parent / 'templates' / 'socratic.html'
    if path.exists():
        return HTMLResponse(path.read_text(encoding='utf-8'))
    return HTMLResponse('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                        '<body style="background:#1F2A37;color:#fff;font-family:system-ui">'
                        '<h2>模板文件缺失</h2><p>请确认 templates/socratic.html 存在。</p>'
                        '</body></html>')


@router.get('/socratic/api/status')
def socratic_status():
    return JSONResponse({'ai': bool(llm_key()), 'steps': STEPS})


@router.get('/socratic/api/sessions')
def socratic_sessions(request: Request):
    try:
        rows = storage.list_records(TOOL, _user(request), LIST_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)})
    return JSONResponse({'ok': True, 'items': [_row(r, False) for r in rows]})


@router.post('/socratic/api/sessions')
async def socratic_create(request: Request):
    payload = await _body(request)
    ask = _clean(payload.get('ask'), ASK_MAX)
    if len(ask) < 4:
        return JSONResponse({'ok': False, 'message': '把那件事写长一点，四个字以上。'},
                            status_code=400)
    return await run_in_threadpool(_do_create, ask, _user(request))


@router.get('/socratic/api/sessions/{sid}')
def socratic_session(sid: int, request: Request):
    user = _user(request)
    try:
        row = storage.get_record(user, sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'message': '找不到这个会话。'}, status_code=404)
    return JSONResponse({'ok': True, 'session': _row(row)})


@router.post('/socratic/api/sessions/{sid}/reply')
async def socratic_reply(sid: int, request: Request):
    payload = await _body(request)
    text = _clean(payload.get('text'), REPLY_MAX)
    if not text:
        return JSONResponse({'ok': False, 'message': '先写点什么再发。'}, status_code=400)
    user = _user(request)
    if not llm_key():
        return JSONResponse({'ok': False, 'message': NO_KEY}, status_code=503)
    try:
        row = storage.get_record(user, sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'message': '找不到这个会话。'}, status_code=404)
    # 先校验再开流：出错时前端拿到的还是正常 JSON，不用在流里认错
    return StreamingResponse(_stream_reply(sid, text, user, row),
                             media_type='text/event-stream; charset=utf-8',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.patch('/socratic/api/sessions/{sid}')
async def socratic_update(sid: int, request: Request):
    payload = await _body(request)
    user = _user(request)
    try:
        row = storage.get_record(user, sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'message': '找不到这个会话。'}, status_code=404)

    plan = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    title = row.get('title')
    if 'finished' in payload:
        plan['finished'] = bool(payload.get('finished'))
        plan['updated_at'] = storage.now_str()
    if 'step' in payload:
        plan['step'] = _clamp_step(payload.get('step'))
        plan['turns'] = 0
    if 'title' in payload:
        title = _clean(payload.get('title'), 60) or title
    try:
        storage.update_record(user, sid, payload=plan, title=title)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    return JSONResponse({'ok': True, 'session': _pack(sid, title, plan)})


@router.delete('/socratic/api/sessions/{sid}')
def socratic_delete(sid: int, request: Request):
    try:
        changed = storage.delete_record(_user(request), sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not changed:
        return JSONResponse({'ok': False, 'message': '找不到这个会话。'}, status_code=404)
    return JSONResponse({'ok': True})


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _app = FastAPI(title='苏格拉底提问', version='2.0.0')
    _app.include_router(router)
    uvicorn.run(_app, host='127.0.0.1', port=8013)