# -*- coding: utf-8 -*-
# 划词问 AI（全站共用）
# ------------------------------------------------------------
# 任何工具页选中一段文字 -> 前端浮出「问 AI」-> POST /api/quick-ask
# 这里只干三件事：校验、拼提示词、把模型输出按 SSE 流回去。
#
# 设计约束：
#   - 只收「选中的那段 + 可选追问 + 页面标题」，不收整页内容（隐私 / token）；
#   - 输入硬上限，动作白名单，按账号限流；
#   - 同一段文字 10 分钟内走内存缓存，重复问不重复花钱；
#   - 前端用 SSE 增量渲染，体验像 ChatGPT 一样边写边出。
import collections
import hashlib
import json
import threading
import time

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from model_config import llm_endpoint, llm_key, llm_model

router = APIRouter()

TEXT_MAX = 4000
QUESTION_MAX = 500
PAGE_MAX = 200
RATE_PER_MIN = 20
CACHE_TTL = 600
CACHE_MAX = 200

_CACHE = {}                 # key -> (expire_ts, answer)
_RATE = {}                  # user -> deque[ts]
_LOCK = threading.Lock()

# 动作 -> (按钮文案, 给模型的指令)
ACTIONS = {
    'explain': (
        '解释',
        '用中文解释这段话在说什么。先给一句人话版结论，再补必要的背景；'
        '遇到术语就用一个日常例子说明。不要复述原文，不要编造原文没有的信息。'),
    'translate': (
        '翻译',
        '翻译这段文字：中文译成自然英文，英文译成中文。只给译文；'
        '专有名词、代码、型号保持原样。如果原文有歧义，最后用一行「注：」说明。'),
    'fact': (
        '查一下',
        '帮我核实这段话里的说法。先给结论（对 / 错 / 无法确认），再给依据；'
        '如果你不确定或者需要实时信息，就直接说「无法确认」，绝对不要编造出处。'),
    'summary': (
        '总结',
        '把这段文字压缩成 3~5 条要点，保留数字、结论和关键名词；'
        '不要加入原文没有的观点。'),
}

SYS_RULES = ('你是「我的工具箱」里的划词助手。回答用中文（翻译动作除外），'
             '简洁、说人话，不用「赋能」「一站式」这类词。'
             '只依据用户给出的文字；需要外部事实又无法确认时，明确说无法确认。'
             '输出是纯文本：不要用 ** 加粗、# 标题或 Markdown 表格；需要分点就用「- 」开头。')


def _sse(obj) -> str:
    """SSE 一行。JSON 包一层，前端不用猜边界。"""
    return 'data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n'


def _clean(value, limit: int) -> str:
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    return text[:limit]


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


def _rate_ok(user: str) -> bool:
    now = time.time()
    with _LOCK:
        q = _RATE.setdefault(user or '-', collections.deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_PER_MIN:
            return False
        q.append(now)
        return True


def _cache_get(key: str):
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
        if hit:
            _CACHE.pop(key, None)
    return None


def _cache_put(key: str, answer: str) -> None:
    with _LOCK:
        if len(_CACHE) >= CACHE_MAX:
            for k in sorted(_CACHE, key=lambda x: _CACHE[x][0])[:20]:
                _CACHE.pop(k, None)
        _CACHE[key] = (time.time() + CACHE_TTL, answer)


def _messages(action: str, text: str, question: str, page: str) -> list:
    _label, instruction = ACTIONS[action]
    user_parts = [instruction, '', '【选中的文字】', text]
    if page:
        user_parts += ['', '【所在页面】', page]
    if question:
        user_parts += ['', '【我还想问】', question]
    return [{'role': 'system', 'content': SYS_RULES},
            {'role': 'user', 'content': '\n'.join(user_parts)}]


def _stream_reply(action: str, text: str, question: str, page: str):
    """把模型输出按 SSE 推给前端；出错也走 SSE，前端统一处理。"""
    key = hashlib.sha1(('%s|%s|%s' % (action, text, question)).encode('utf-8')).hexdigest()
    cached = _cache_get(key)
    if cached:
        # 缓存命中：分几块发，前端照样是流式的感觉，但不再花钱
        step = max(40, len(cached) // 6 or 40)
        for i in range(0, len(cached), step):
            yield _sse({'t': cached[i:i + step]})
        yield _sse({'done': True, 'cached': True})
        return

    api_key = llm_key()
    if not api_key:
        yield _sse({'err': '服务端没配 DEEPSEEK_API_KEY，划词问答用不了。'})
        return

    body = {
        'model': llm_model(),
        'messages': _messages(action, text, question, page),
        'temperature': 0.3,
        'max_tokens': 700,
        'stream': True,
    }
    sent = []
    resp = None
    try:
        resp = requests.post(llm_endpoint(), json=body, timeout=(10, 90),
                             headers={'Authorization': 'Bearer ' + api_key,
                                      'Content-Type': 'application/json'},
                             stream=True)
        if resp.status_code >= 400:
            yield _sse({'err': '模型返回错误 %d，稍后再试。' % resp.status_code})
            return
        for raw in resp.iter_lines(decode_unicode=False):
            if not raw or not raw.startswith(b'data:'):
                continue
            data = raw[5:].strip()
            if not data or data == b'[DONE]':
                continue
            try:
                obj = json.loads(data.decode('utf-8'))
                piece = obj['choices'][0]['delta'].get('content') or ''
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                continue
            if piece:
                sent.append(piece)
                yield _sse({'t': piece})
    except requests.exceptions.Timeout:
        yield _sse({'err': '模型响应超时，稍后再试。'})
        return
    except requests.exceptions.RequestException as exc:
        yield _sse({'err': '连不上模型：%s' % str(exc)[:120]})
        return
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    answer = ''.join(sent).strip()
    if answer:
        _cache_put(key, answer)
        yield _sse({'done': True})
    else:
        yield _sse({'err': '模型这次没说出话来，再试一次。'})


@router.get('/api/quick-ask/status')
def quick_ask_status() -> JSONResponse:
    return JSONResponse({'ok': True, 'ai': bool(llm_key())})


@router.post('/api/quick-ask')
async def quick_ask(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    action = str(payload.get('action') or '').strip()
    text = _clean(payload.get('text'), TEXT_MAX)
    question = _clean(payload.get('question'), QUESTION_MAX)
    page = _clean(payload.get('page'), PAGE_MAX)
    if action not in ACTIONS:
        return JSONResponse({'ok': False, 'msg': '不认识的动作'})
    if len(text) < 2:
        return JSONResponse({'ok': False, 'msg': '先选中一段文字'})
    if not llm_key():
        return JSONResponse({'ok': False, 'msg': '服务端没配 DEEPSEEK_API_KEY'})
    if not _rate_ok(_user(request)):
        return JSONResponse({'ok': False, 'msg': '问得太快了，歇一分钟再问'})
    return StreamingResponse(_stream_reply(action, text, question, page),
                             media_type='text/event-stream; charset=utf-8',
                             headers={'Cache-Control': 'no-cache',
                                      'X-Accel-Buffering': 'no'})
