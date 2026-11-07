# -*- coding: utf-8 -*-
# 首页「全站文本模型」切换接口
# ------------------------------------------------------------
# 选择结果写进 data/llm.json，所有调用 model_config.llm_* 的文本工具
# 下一次请求就会跟着变，不需要重启服务。
#
#   GET  /api/model/state    当前选择 + 各服务商是否可用 + 可选模型
#   POST /api/model/select   切换 {provider, model}
#   POST /api/model/test     用当前选择发一句最短的请求，验证 Key / 地址
#
# Key 只在服务端环境变量里，接口不读也不返回 Key 内容。
from __future__ import annotations

import time

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import model_config

router = APIRouter()

# 首页下拉框里那段说明。文生图 / 语音 / OCR / 搜索各有自己的配置，
# 不经过 model_config，所以切文本模型不会影响它们。
SCOPE_NOTE = ('切换后，所有调用 model_config 的文本工具（对话、分析、复盘等）一起生效；'
              '文生图（ARK_*）、朗读与语音识别（VOLC_*）、OCR、联网搜索（IQS）'
              '是各自独立的配置，不受影响。')


def _fail(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({'ok': False, 'error': text}, status_code=status)


def _resp_text(resp) -> str:
    """模型服务报错时，只回一小段可读文本，避免把整页 HTML 甩给前端。"""
    try:
        text = resp.text or ''
    except Exception:
        text = ''
    text = ' '.join(str(text).split())
    return text[:200] or '（没有返回内容）'


@router.get('/api/model/state')
def model_state() -> JSONResponse:
    try:
        data = model_config.llm_state()
    except Exception as exc:  # 配置读不了时不要把首页整挂
        return _fail('读取模型配置失败：%s' % str(exc)[:160], 503)
    data['ok'] = True
    data['scope'] = SCOPE_NOTE
    return JSONResponse(data)


@router.post('/api/model/select')
async def model_select(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        data = model_config.set_llm_selection(payload.get('provider'),
                                              payload.get('model'))
    except ValueError as exc:
        return _fail(str(exc))
    except OSError as exc:
        return _fail('保存失败：%s' % str(exc)[:160], 503)
    data['ok'] = True
    data['scope'] = SCOPE_NOTE
    return JSONResponse(data)


@router.post('/api/model/test')
def model_test() -> JSONResponse:
    """发一句「回复两个字」验证当前模型，不写任何记录。"""
    state = model_config.llm_state()
    if not model_config.llm_ready():
        return _fail(model_config.llm_missing_hint())

    body = {
        'model': state['model'],
        'messages': [{'role': 'user', 'content': '回复两个字：可用'}],
        'max_tokens': 16,
        'stream': False,
    }
    started = time.monotonic()
    try:
        resp = requests.post(
            model_config.llm_endpoint(),
            json=model_config.llm_body(body),
            timeout=(8, 25),
            headers={
                'Authorization': 'Bearer ' + model_config.llm_key(),
                'Content-Type': 'application/json',
            },
        )
    except requests.RequestException as exc:
        ms = int((time.monotonic() - started) * 1000)
        return _fail('连接失败（%dms）：%s' % (ms, str(exc)[:160]))

    ms = int((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        return _fail('接口返回 %s（%dms）：%s'
                     % (resp.status_code, ms, _resp_text(resp)))

    try:
        data = resp.json()
        reply = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
    except (ValueError, AttributeError, IndexError):
        return _fail('接口返回的不是 OpenAI 兼容格式（%dms）' % ms)

    return JSONResponse({
        'ok': True,
        'ms': ms,
        'model': state['model'],
        'reply': str(reply or '').strip()[:80],
    })
