# -*- coding: utf-8 -*-
# 朗读（火山引擎 / 豆包语音合成）
# ------------------------------------------------------------
# 把一段中文交给火山引擎的「大模型语音合成」接口，拿回 mp3 字节，给 /static/voice.js 播。
#
# 为什么放在服务端而不是浏览器直连火山：
#   · Key 一旦写进前端 JS，任何人按 F12 就能抄走；放服务端，浏览器只见得到声音。
#   · 服务端能顺手做缓存 —— 同一句话读第二遍直接返回，不重复计费。
#
# 三个省钱 / 防呆设计：
#   1. 结果按 sha1(音色 + 语速 + 文本) 在内存里缓存，命中不计费（这是最省的一环）；
#   2. 前端按标点把整段回复切成小句（一句一次请求），句子短则首字出声快，
#      也避免把几百字的整段丢给接口白等 1 分钟；
#   3. 服务端再兜一层长度上限，超了直接拒绝，不让一次请求把额度吃光。
#
# 接口形态（实测）：POST 一次性提交文本，响应体是「一行一个 JSON」的分片流，
# 每片的 data 字段是 base64 的音频，最后一行带 usage.text_words 计费字数。
# 需要自己把分片拼起来。错误码只会是 HTTP 401 / 403（body 可能为空或一段 JSON），
# 接口不给人类可读的说明，所以下面的 _friendly_error 负责把它翻译成人话。
#
# 没配 Key 时 /api/tts 直接返回 ok:false，前端自己回落浏览器自带语音，朗读不会整个失效。
import base64
import collections
import hashlib
import json
import time
import uuid

import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from model_config import (
    ENV_TTS_KEY,
    tts_ready,
    volc_tts_key,
    volc_tts_resource,
    volc_tts_speed,
    volc_tts_voice,
)

router = APIRouter()

_API_URL = 'https://openspeech.bytedance.com/api/v3/tts/unidirectional'
_SAMPLE_RATE = 24000
_MAX_CHARS = 400                 # 一次请求最多这么多字（400 字约 30 秒音频，够用了）
_TIMEOUT = (10, 120)             # 连接 10 秒，读 120 秒（实测 400 字要 30 秒左右）
_CACHE_MAX_BYTES = 48 * 1024 * 1024   # 音频缓存上限，超了丢最老的
_KEY_HINT = ('需在服务端配置环境变量 %s（火山引擎语音合成控制台里开通后拿到）'
             % ENV_TTS_KEY)

# 有序字典当 LRU 用：命中就挪到末尾，满了从头丢
_cache = collections.OrderedDict()
_cache_bytes = 0
_session = requests.Session()


def _friendly_error(status, body, code=0):
    """把接口的英文 / 数字错误翻译成人能看懂的一句话。"""
    text = (body or '').strip()
    if status == 401:
        return '语音合成 Key 没通过校验（HTTP 401），' + _KEY_HINT
    if status == 403:
        if str(code) == '45000030' or 'not granted' in text:
            return ('语音合成的资源 ID 和音色不配套（HTTP 403）。'
                    '如果换过音色，检查 VOLC_TTS_RESOURCE_ID 是否要一起改')
        return '语音合成接口拒绝了这个请求（HTTP 403）。' + _KEY_HINT
    if status == 429:
        return '语音合成请求太频繁，请稍后再试'
    if status >= 500:
        return '语音合成服务端出错（HTTP %d），稍后再试' % status
    plain = ' '.join(text.split())
    return '语音合成接口返回错误（HTTP %d）：%s' % (status, plain[:160] or '未知错误')


def _speed_rate(speed):
    """把 1.0 当标准语速，换算成接口要的 speech_rate（-50 ~ 100）。"""
    try:
        val = float(speed)
    except (TypeError, ValueError):
        val = 1.0
    rate = int(round((val - 1.0) * 50))
    return max(-50, min(100, rate))


def synthesize(text, voice=None, speed=None):
    """合成一段文字 -> (mp3 字节, 计费字数)。失败抛 ValueError（消息可以直接给用户看）。"""
    text = (text or '').strip()
    if not text:
        raise ValueError('没有要朗读的内容')
    if len(text) > _MAX_CHARS:
        raise ValueError('这一段有 %d 个字，超过单次 %d 字上限，请分句朗读'
                         % (len(text), _MAX_CHARS))
    key = volc_tts_key()
    if not key:
        raise ValueError('服务端没有配置语音合成 Key（%s）' % ENV_TTS_KEY)

    use_voice = (voice or '').strip() or volc_tts_voice()
    use_speed = volc_tts_speed() if speed is None else speed

    hit = _cache_get(text, use_voice, use_speed)
    if hit is not None:
        return hit

    payload = {
        'req_params': {
            'text': text,
            'speaker': use_voice,
            'audio_params': {
                'format': 'mp3',
                'sample_rate': _SAMPLE_RATE,
                'speech_rate': _speed_rate(use_speed),
            },
        }
    }
    headers = {
        'X-Api-Key': key,
        'X-Api-Resource-Id': volc_tts_resource(),
        'X-Api-Request-Id': str(uuid.uuid4()),
        'X-Control-Require-Usage-Tokens-Return': '*',   # 让接口回传计费字数
        'Content-Type': 'application/json',
    }
    try:
        resp = _session.post(_API_URL, headers=headers, json=payload, timeout=_TIMEOUT)
    except requests.exceptions.Timeout:
        raise ValueError('语音合成响应超时，请稍后再试')
    except requests.exceptions.RequestException as exc:
        raise ValueError('连不上语音合成接口：' + str(exc)[:160])

    if resp.status_code >= 400:
        code = 0
        try:
            code = (resp.json() or {}).get('code') or 0
        except ValueError:
            code = 0
        raise ValueError(_friendly_error(resp.status_code, resp.text, code))

    chunks = []
    words = 0
    for line in (resp.text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        usage = item.get('usage') or {}
        if isinstance(usage, dict) and usage.get('text_words'):
            words = int(usage['text_words'])
        piece = item.get('data')
        if piece:
            try:
                chunks.append(base64.b64decode(piece))
            except (ValueError, TypeError):
                continue

    audio = b''.join(chunks)
    if not audio:
        raise ValueError('语音合成没有返回音频，稍后再试')
    if not words:
        words = len(text)
    _cache_put(text, use_voice, use_speed, audio, words)
    return audio, words


# ---------------- 音频缓存（内存，重启即清） ----------------
def _cache_key(text, voice, speed):
    raw = '%s|%s|%s' % (voice, speed, text)
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def _cache_get(text, voice, speed):
    item = _cache.get(_cache_key(text, voice, speed))
    if item is None:
        return None
    _cache.move_to_end(_cache_key(text, voice, speed))
    return item


def _cache_put(text, voice, speed, audio, words):
    global _cache_bytes
    k = _cache_key(text, voice, speed)
    if k in _cache:
        _cache_bytes -= len(_cache[k][0])
        del _cache[k]
    _cache[k] = (audio, words)
    _cache_bytes += len(audio)
    while _cache_bytes > _CACHE_MAX_BYTES and _cache:
        _, (old, _w) = _cache.popitem(last=False)
        _cache_bytes -= len(old)


def cache_stat():
    return {'entries': len(_cache), 'bytes': _cache_bytes}


# ---------------- 路由 ----------------
@router.post('/api/tts')
async def tts_speak(payload: dict):
    """{text} -> mp3 音频。失败返回 {'ok': false, 'message': '...'}（HTTP 200，不炸 5xx）。"""
    text = ''
    voice = ''
    speed = None
    if isinstance(payload, dict):
        text = str(payload.get('text') or '')
        voice = str(payload.get('voice') or '')
        if payload.get('speed') is not None:
            speed = payload.get('speed')
    if not tts_ready():
        return JSONResponse({'ok': False, 'message': '服务端没有配置语音合成 Key（%s）' % ENV_TTS_KEY})
    try:
        audio, words = await run_in_threadpool(synthesize, text, voice, speed)
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)})
    except Exception as exc:                      # 兜底：任何意外都别把页面读不出声变成 500
        return JSONResponse({'ok': False, 'message': '朗读失败：' + str(exc)[:160]})
    return Response(
        content=audio,
        media_type='audio/mpeg',
        headers={
            'Cache-Control': 'no-store',
            'X-TTS-Words': str(words),           # 本次计费字数，方便自己对账
            'X-TTS-Voice': volc_tts_voice(),
        },
    )


@router.get('/api/tts/health')
async def tts_health():
    """自检：真合成一句话，回来报字节数 / 耗时 / 计费字数。不吐 Key。"""
    if not tts_ready():
        return {'ok': False, 'message': '服务端没有配置语音合成 Key（%s）' % ENV_TTS_KEY}
    probe = '朗读自检，接口正常。'
    started = time.time()
    try:
        audio, words = await run_in_threadpool(synthesize, probe)
    except ValueError as exc:
        return {'ok': False, 'message': str(exc)}
    return {
        'ok': True,
        'bytes': len(audio),
        'words': words,
        'elapsed': round(time.time() - started, 2),
        'voice': volc_tts_voice(),
        'resource': volc_tts_resource(),
        'speed': volc_tts_speed(),
        'cache': cache_stat(),
    }


if __name__ == '__main__':
    import sys

    if '--test' in sys.argv:              # 只验 Key：python volc_tts.py --test
        t0 = time.time()
        cut, bill = synthesize('这是一次语音合成自检。')
        print('ok  bytes=%d  words=%d  elapsed=%.2fs  voice=%s'
              % (len(cut), bill, time.time() - t0, volc_tts_voice()))
        sys.exit(0)

    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='朗读（火山引擎豆包 TTS）', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8013)