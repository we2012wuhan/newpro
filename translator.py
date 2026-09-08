# -*- coding: utf-8 -*-
# 翻译助手：输入中文 -> 调用免费的公共翻译接口（MyMemory，无需密钥）-> 返回英文，
# 大小写 / 驼峰等格式转换在前端即时完成，方便复制成变量名、函数名。
import re
import time
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'translator.html'
_MYMEMORY_API = 'https://api.mymemory.translated.net/get'
_MAX_TEXT = 200  # 该免费接口单次请求限制 500 字节，词汇/短语场景足够


def _translate_one(text: str):
    """调用 MyMemory 免费翻译接口（无需密钥），返回英文译文。"""
    params = {'q': text, 'langpair': 'zh-CN|en'}
    resp = requests.get(_MYMEMORY_API, params=params, timeout=(10, 30),
                        headers={'User-Agent': 'Mozilla/5.0'})
    if resp.status_code in (429, 403):
        raise ValueError('免费翻译接口访问受限（频率过高），请稍后重试')
    if resp.status_code >= 400:
        raise ValueError('免费翻译接口返回 HTTP ' + str(resp.status_code))
    data = resp.json()
    status = data.get('responseStatus')
    if status != 200:
        detail = str(data.get('responseDetails') or '未知错误')[:120]
        raise ValueError('免费翻译接口返回错误：' + detail)
    translated = (data.get('responseData') or {}).get('translatedText') or ''
    # 额度耗尽标记
    if data.get('quotaFinished') or '{#-#}' in translated:
        raise ValueError('免费翻译接口的今日额度已用完，请明天再试，或换用其它翻译引擎')
    translated = re.sub(r'\{#-#\}|#\d+\b', '', translated)
    translated = re.sub(r'\s+', ' ', translated).strip().strip('。.,，')
    if not translated or not re.search(r'[A-Za-z]', translated):
        raise ValueError('未能识别翻译结果，请换一种输入试试')
    return translated


def _do_translate(text):
    text = str(text or '').strip()
    if not text:
        return JSONResponse({'ok': False, 'message': '请先输入要翻译的中文'})
    if len(text) > _MAX_TEXT:
        return JSONResponse({'ok': False, 'message': '输入太长了，请控制在 %d 字以内' % _MAX_TEXT})
    started = time.time()
    try:
        dst = _translate_one(text)
    except requests.exceptions.Timeout:
        return JSONResponse({'ok': False, 'message': '请求免费翻译接口超时，请稍后重试或检查网络'})
    except requests.exceptions.RequestException as exc:
        return JSONResponse({'ok': False, 'message': '无法连接翻译接口：' + str(exc)[:160]})
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)})
    return JSONResponse({
        'ok': True,
        'text': text,
        'src': text,
        'dst': dst,
        'provider': 'MyMemory（免费 · 无需密钥）',
        'elapsed': round(time.time() - started, 1),
    })


@router.get('/translator', response_class=HTMLResponse)
def translator_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/translator.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.post('/translator/api/translate')
async def translator_translate(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    text = payload.get('text') or ''
    return await run_in_threadpool(_do_translate, text)