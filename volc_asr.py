# -*- coding: utf-8 -*-
# 语音输入（火山引擎 / 豆包语音识别）
# ------------------------------------------------------------
# 前端把一段录音（16k 单声道 wav）POST 上来，这里转成文字再还回去，
# 页面上填进输入框由用户自己确认了再发 —— 识别错一个字，改比删重打便宜。
#
# 为什么走服务端：Key 不能进前端（F12 就能抄走），而且服务端能做限长、报错翻译。
#
# 两条路，先快后稳：
#   1. recognize/flash —— 同步接口，一次请求直接出文字，短句首选；
#   2. submit + query  —— 异步接口，提交后轮询结果。flash 不通时走这条兜底。
# 两个接口都要 base64 的音频，格式支持 wav / mp3 / ogg。
#
# Resource ID：同一个账号里，火山把不同的识别能力拆开发、分开开通，开通了哪个就得用哪个的 ID
# （有的账号开的是流式 sauc.duration，有的开的是录音文件 auc）。所以这里不写死一个：
# 先用上次成功的，再用配置的 VOLC_ASR_RESOURCE_ID，最后按 _FALLBACK_RESOURCES 挨个试；
# 全都没开通才抛一句人话，让用户去控制台开。
import base64
import io
import json
import math
import struct
import time
import uuid
import wave

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from model_config import (
    ENV_ASR_KEY,
    ENV_ASR_RESOURCE,
    asr_ready,
    volc_asr_key,
    volc_asr_resource,
)

router = APIRouter()

_BASE = 'https://openspeech.bytedance.com'
_FLASH = '/api/v3/auc/bigmodel/recognize/flash'
_SUBMIT = '/api/v3/auc/bigmodel/submit'
_QUERY = '/api/v3/auc/bigmodel/query'

_MAX_BYTES = 8 * 1024 * 1024      # 8 MB 够录好几分钟的 16k wav 了
_TIMEOUT = (10, 90)
_POLL_TIMES = 12                  # 异步接口最多轮询这么多次
_POLL_GAP = 0.6                   # 每次间隔（秒）
_FORMATS = ('wav', 'mp3', 'ogg', 'pcm')

_KEY_HINT = ('需在服务端配置环境变量 %s（不填会复用朗读那把 VOLC_TTS_API_KEY），'
             '并到火山控制台开通「大模型语音识别」' % ENV_ASR_KEY)

# 账号里开通的是哪个 Resource ID 就用哪个。前三行是实测过的三个候选：
#   volc.bigasr.sauc.duration  大模型流式语音识别（小时版）—— 同一个 HTTP 接口也认它
#   volc.bigasr.auc_turbo      录音文件识别极速版
#   volc.bigasr.auc            录音文件识别
_FALLBACK_RESOURCES = ('volc.bigasr.sauc.duration', 'volc.bigasr.auc_turbo', 'volc.bigasr.auc')
_good_resource = ''          # 试出来能用的那个，本进程记住，后面直接从它开始


class _Denied(Exception):
    """这个 Resource ID 没开通（403 / 45000030），换下一个试。"""

    def __init__(self, resource):
        Exception.__init__(self, resource)
        self.resource = resource


def _resources():
    """要试的资源 ID：上次成功的 -> 配置的 -> 兜底列表，去重。"""
    out = []
    for rid in ((_good_resource, volc_asr_resource()) + _FALLBACK_RESOURCES):
        rid = (rid or '').strip()
        if rid and rid not in out:
            out.append(rid)
    return out


def _headers(rid, tid=None):
    """tid 是任务号：submit 和 query 必须用同一个，否则查不到那条任务。"""
    return {
        'X-Api-Key': volc_asr_key(),
        'X-Api-Resource-Id': rid,
        'X-Api-Request-Id': tid or str(uuid.uuid4()),
        'X-Api-Sequence': '-1',
        'Content-Type': 'application/json',
    }


def _code_of(body):
    """把接口返回里的数字错误码挖出来（两代返回格式都认）。"""
    if not isinstance(body, dict):
        return 0
    for box in (body.get('header'), body):
        if isinstance(box, dict):
            code = box.get('code')
            if isinstance(code, (int, float)) and int(code) not in (0, 20000000, 1000):
                return int(code)
    return 0


def _message_of(body, status):
    """从返回里挑一句能看的错误话。"""
    if isinstance(body, dict):
        for box in (body.get('header'), body):
            if isinstance(box, dict) and box.get('message'):
                return str(box['message'])
    return 'HTTP %d' % status


def _is_denied(status, body, text=''):
    """是不是「这个资源没开通」。这种错误换一个资源 ID 再试就有救。"""
    if _code_of(body) == 45000030:
        return True
    if 'not granted' in (text or ''):
        return True
    return status == 403 and _code_of(body) in (0, 45000010)


def _denied_message(denied):
    """全试完还是没开通 —— 把试过的列出来，让人知道该去开哪一个。"""
    return ('语音识别服务还没开通（火山返回「资源未授权」）：这几个资源 ID 都试过了 —— %s。'
            '去火山控制台「语音技术 → 大模型语音识别」开通任意一个，'
            '再把开通的那个填进环境变量 %s。' % ('、'.join(denied) or volc_asr_resource(), ENV_ASR_RESOURCE))


def _friendly(status, body, text='', rid=''):
    """翻译成人话。开通 / Key 这两件事是用户最可能撞上的，优先说清。"""
    raw = (text or '') + ' ' + json.dumps(body, ensure_ascii=False) if body else (text or '')
    code = _code_of(body)
    if status == 401:
        return '语音识别的 Key 没通过校验（HTTP 401），' + _KEY_HINT
    if status == 403 or code in (45000010, 45000030):
        return ('语音识别接口拒绝了这个请求（HTTP 403，资源 ID %s）。'
                % (rid or volc_asr_resource())) + _KEY_HINT
    if status == 429:
        return '语音识别请求太频繁，稍等一下再录'
    if status >= 500:
        return '语音识别服务端出错（HTTP %d），稍后再试' % status
    return '语音识别失败：' + _message_of(body, status)[:160]


def _pick_text(body):
    """把识别结果里的文字挖出来（几种可能的层级都试一遍）。"""
    if not isinstance(body, dict):
        return ''
    stack = [body]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            text = cur.get('text')
            if isinstance(text, str) and text.strip():
                return text.strip()
            for key in ('result', 'data', 'payload'):
                if isinstance(cur.get(key), (dict, list)):
                    stack.append(cur[key])
            for key in ('utterances', 'sentences'):
                if isinstance(cur.get(key), list):
                    parts = [str(x.get('text', '')).strip() for x in cur[key] if isinstance(x, dict)]
                    joined = ''.join(p for p in parts if p)
                    if joined:
                        return joined
        elif isinstance(cur, list):
            stack.extend(cur)
    return ''


def _post(path, payload, rid, tid=None, timeout=None):
    return requests.post(_BASE + path, headers=_headers(rid, tid), json=payload,
                         timeout=timeout or _TIMEOUT)


def _payload(data_b64, fmt, extra=None):
    req = {'model_name': 'bigmodel', 'enable_itn': True, 'enable_punc': True}
    if extra:
        req.update(extra)
    return {
        'user': {'uid': 'toolbox'},
        'audio': {'format': fmt, 'data': data_b64, 'rate': 16000, 'bits': 16, 'channel': 1},
        'request': req,
    }


def _attempt(rid, data_b64, fmt, started):
    """拿一个资源 ID 走完：先同步极速版，不通再「提交 + 轮询」。"""
    global _good_resource

    # ---- 第一条路：同步接口，一次出结果 ----
    try:
        resp = _post(_FLASH, _payload(data_b64, fmt), rid, timeout=(10, 60))
    except requests.exceptions.Timeout:
        resp = None                            # 超时就试试异步接口
    except requests.exceptions.RequestException as exc:
        raise ValueError('连不上语音识别接口：' + str(exc)[:160])

    if resp is not None:
        body = _safe_json(resp)
        if _is_denied(resp.status_code, body, resp.text):
            raise _Denied(rid)
        if resp.status_code in (401, 403) or _code_of(body) in (45000010, 45000030):
            raise ValueError(_friendly(resp.status_code, body, resp.text, rid))
        if resp.status_code < 400:
            text = _pick_text(body)
            if text:
                _good_resource = rid
                return {'ok': True, 'text': text, 'elapsed': round(time.time() - started, 2),
                        'engine': 'flash', 'resource': rid}
            # 200 但没文字：静音（或者全是没人说话的噪音）就是这种，当作空结果
            if _code_of(body) == 0:
                _good_resource = rid
                return {'ok': True, 'text': '', 'elapsed': round(time.time() - started, 2),
                        'engine': 'flash', 'empty': True, 'resource': rid}

    # ---- 第二条路：提交任务 + 轮询 ----
    tid = str(uuid.uuid4())                    # submit 和 query 必须是同一个任务号
    try:
        sub = _post(_SUBMIT, _payload(data_b64, fmt), rid, tid=tid)
        sbody = _safe_json(sub)
        if _is_denied(sub.status_code, sbody, sub.text):
            raise _Denied(rid)
        if sub.status_code >= 400 or _code_of(sbody) in (45000010, 45000030):
            raise ValueError(_friendly(sub.status_code, sbody, sub.text, rid))
        for _ in range(_POLL_TIMES):
            time.sleep(_POLL_GAP)
            q = _post(_QUERY, {'user': {'uid': 'toolbox'}}, rid, tid=tid, timeout=(10, 60))
            qbody = _safe_json(q)
            text = _pick_text(qbody)
            if text:
                _good_resource = rid
                return {'ok': True, 'text': text, 'elapsed': round(time.time() - started, 2),
                        'engine': 'submit', 'resource': rid}
            code = _code_of(qbody)
            if code and code not in (2000, 2001):       # 还在跑（2000/2001 是处理中）
                raise ValueError(_friendly(q.status_code, qbody, q.text, rid))
    except requests.exceptions.RequestException as exc:
        raise ValueError('连不上语音识别接口：' + str(exc)[:160])

    _good_resource = rid
    return {'ok': True, 'text': '', 'elapsed': round(time.time() - started, 2),
            'engine': 'submit', 'empty': True, 'resource': rid}


def transcribe(raw, fmt='wav'):
    """音频字节 -> {ok, text, elapsed, engine, resource}。失败抛 ValueError（消息可直接给用户看）。"""
    raw = raw or b''
    if not raw:
        raise ValueError('没有录到声音，再试一次')
    if len(raw) > _MAX_BYTES:
        raise ValueError('这段录音有 %.1f MB，太大了（上限 %d MB），录短一点'
                         % (len(raw) / 1048576.0, _MAX_BYTES // 1048576))
    fmt = (fmt or 'wav').lower().lstrip('.')
    if fmt not in _FORMATS:
        raise ValueError('不认识的音频格式 %s，只支持 %s' % (fmt, '/'.join(_FORMATS)))
    if not asr_ready():
        raise ValueError('服务端没有配置语音识别 Key（%s 或 VOLC_TTS_API_KEY）' % ENV_ASR_KEY)

    data_b64 = base64.b64encode(raw).decode('ascii')
    started = time.time()

    denied = []
    for rid in _resources():
        try:
            return _attempt(rid, data_b64, fmt, started)
        except _Denied as exc:
            denied.append(exc.resource)
    raise ValueError(_denied_message(denied))


def _safe_json(resp):
    try:
        body = resp.json()
    except ValueError:
        try:
            return json.loads((resp.text or '').strip().splitlines()[-1])
        except Exception:
            return {}
    return body if isinstance(body, dict) else {}


def _silent_wav(seconds=1.0, rate=16000):
    """造一段 200Hz 的测试音（自检用）：不是人声，识别不出字正好说明接口通了。"""
    buf = io.BytesIO()
    w = wave.open(buf, 'wb')
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    frames = bytearray()
    for i in range(int(rate * seconds)):
        frames += struct.pack('<h', int(600 * math.sin(2 * math.pi * 200 * i / rate)))
    w.writeframes(bytes(frames))
    w.close()
    return buf.getvalue()


# ---------------- 路由 ----------------
@router.post('/api/asr')
async def asr_recognize(request: Request):
    """收音频 -> 返回文字。

    两种送法都行：
      · 直接发二进制音频，Content-Type: audio/wav（前端走的就是这条）
      · 发 JSON {"audio": "<base64>", "format": "wav"}（自己调试方便）
    失败一律 HTTP 200 + {'ok': false, 'message': '人话'}，前端照着弹提示。
    """
    ctype = (request.headers.get('content-type') or '').lower()
    raw, fmt = b'', 'wav'
    try:
        body = await request.body()
        if 'json' in ctype:
            data = json.loads(body.decode('utf-8') or '{}')
            raw = base64.b64decode(str(data.get('audio') or ''))
            fmt = str(data.get('format') or 'wav')
        else:
            raw = body
            if 'mp3' in ctype or 'mpeg' in ctype:
                fmt = 'mp3'
            elif 'ogg' in ctype or 'opus' in ctype or 'webm' in ctype:
                fmt = 'ogg'
    except Exception as exc:
        return JSONResponse({'ok': False, 'message': '读不到这段录音：' + str(exc)[:120]})

    try:
        got = await run_in_threadpool(transcribe, raw, fmt)
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)})
    except Exception as exc:
        return JSONResponse({'ok': False, 'message': '语音识别失败：' + str(exc)[:160]})
    return JSONResponse(got)


@router.get('/api/asr/health')
async def asr_health():
    """自检：发一段 1 秒测试音过去，看服务通不通（不回显 Key）。"""
    if not asr_ready():
        return {'ok': False, 'message': '服务端没有配置语音识别 Key'}
    started = time.time()
    try:
        got = await run_in_threadpool(transcribe, _silent_wav(), 'wav')
    except ValueError as exc:
        return {'ok': False, 'message': str(exc)}
    return {'ok': True, 'text': got.get('text', ''), 'engine': got.get('engine'),
            'elapsed': round(time.time() - started, 2), 'resource': got.get('resource'),
            'message': '接口通了（这段是自检的测试音，听不出字是对的）'}


if __name__ == '__main__':
    import sys

    if '--test' in sys.argv:                     # python volc_asr.py --test 录音文件
        args = [a for a in sys.argv[1:] if not a.startswith('--')]
        if not args:
            print('用法：python volc_asr.py --test 你的录音.wav')
            sys.exit(2)
        blob = open(args[0], 'rb').read()
        out = transcribe(blob, args[0].rsplit('.', 1)[-1])
        print('识别结果：%r（%s，%.2fs）' % (out['text'], out['engine'], out['elapsed']))
        sys.exit(0)

    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='语音输入（火山引擎语音识别）', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8014)