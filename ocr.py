# -*- coding: utf-8 -*-
# OCR 图片识别：粘贴截图 / 上传图片 -> 调用免费的云端 OCR 接口（OCR.space）-> 输出可编辑文字。
# 本地不装任何模型，只依赖 requests，部署体积很小；
# 默认用公共免费 Key（无需注册，但会被限流），也可填自己申请的免费 Key 提升额度。
import os
import re
import time
from pathlib import Path

import requests
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'ocr.html'
_API_URL = 'https://api.ocr.space/parse/image'
_DEMO_KEY = 'helloworld'        # OCR.space 公共免费 Key：免注册，但高峰期会被限流
_ENV_KEY = 'OCR_SPACE_API_KEY'  # 也可用环境变量配置自己的免费 Key（Vercel 里加到环境变量即可）
_MAX_BYTES = 1024 * 1024        # 免费额度单张 1 MB
_ALLOWED_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.pdf')
_KEY_HINT = '也可以在页面「自定义 Key」里填入自己申请的免费 Key（ocr.space/ocrapi，每月 25000 次）。'


def _api_key(req_key):
    """优先级：页面填写的 Key > 环境变量 > 公共免费 Key。"""
    if req_key and str(req_key).strip():
        return str(req_key).strip()
    return os.environ.get(_ENV_KEY, '').strip() or _DEMO_KEY


def _friendly_error(status, text):
    """把接口返回的英文错误码翻译成用户看得懂的中文提示。"""
    up = (text or '').upper()
    if 'E555' in up or 'NOT VALID' in up or status == 403:
        return 'OCR Key 无效。' + _KEY_HINT
    if 'E551' in up or 'E556' in up:
        return '公共免费 Key 当前被限流，请稍后重试。' + _KEY_HINT
    if 'E552' in up or 'LIMIT' in up:
        return '免费额度已用完，请明天再试。' + _KEY_HINT
    if 'E554' in up:
        return '图片超过 1 MB，请裁剪或压缩后再试'
    if status == 429:
        return '请求太频繁，请稍后再试'
    plain = re.sub(r'\s+', ' ', text or '').strip()
    return 'OCR 接口返回错误 %d：%s' % (status, plain[:200] or '未知错误')


def run_ocr(filename: str, data: bytes, key: str = ''):
    """把图片交给云端 OCR 接口识别 -> {ok, text, lines, count, elapsed, provider}。"""
    data = data or b''
    if not data:
        raise ValueError('图片是空的，请重新粘贴或上传')
    if len(data) > _MAX_BYTES:
        raise ValueError('图片 %.1f MB，超过免费接口 1 MB 上限，请裁剪或压缩后再试'
                         % (len(data) / 1048576.0))
    ext = Path(filename or '').suffix.lower() or '.png'
    if ext not in _ALLOWED_EXT:
        raise ValueError('不支持 %s 格式，请用 png / jpg / gif / bmp / tiff 图片' % ext)

    payload = {
        'apikey': _api_key(key),
        'language': 'chs',          # 简体中文（同时也能识别英文与数字）
        'OCREngine': '2',           # 引擎 2：对中文与混排文字更准
        'scale': 'true',
        'isTable': 'false',
        'detectOrientation': 'true',
        'isOverlayRequired': 'false',
        'filetype': ext.lstrip('.').upper(),
    }
    started = time.time()
    try:
        resp = requests.post(
            _API_URL, data=payload,
            files={'file': (filename or ('image' + ext), data, 'application/octet-stream')},
            timeout=(10, 90),
        )
    except requests.exceptions.Timeout:
        raise ValueError('OCR 接口响应超时，请稍后重试')
    except requests.exceptions.RequestException as exc:
        raise ValueError('无法连接 OCR 接口：' + str(exc)[:160])

    raw = resp.text or ''
    try:
        body = resp.json()
    except ValueError:
        body = {}

    if resp.status_code >= 400:
        raise ValueError(_friendly_error(resp.status_code, raw))
    if not isinstance(body, dict):
        raise ValueError('OCR 接口返回内容异常')
    if body.get('IsErroredOnProcessing'):
        msg = body.get('ErrorMessage') or body.get('ErrorDetails') or raw
        if isinstance(msg, list):
            msg = '；'.join(str(x) for x in msg)
        raise ValueError(_friendly_error(resp.status_code, str(msg)))

    texts = []
    for item in (body.get('ParsedResults') or []):
        if not isinstance(item, dict):
            continue
        if item.get('FileParseExitCode') not in (1, '1', None):
            continue
        seg = (item.get('ParsedText') or '').replace('\r\n', '\n').replace('\r', '\n').strip()
        if seg:
            texts.append(seg)
    text = '\n'.join(texts).strip()
    lines = [ln for ln in text.split('\n') if ln.strip()] if text else []
    return {
        'ok': True,
        'text': text,
        'lines': lines,
        'count': len(lines),
        'elapsed': round(time.time() - started, 1),
        'provider': 'OCR.space',
    }


# ---------------- 路由 ----------------
@router.get('/ocr', response_class=HTMLResponse)
def ocr_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/ocr.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.post('/ocr/api/recognize')
async def ocr_recognize(file: UploadFile = File(...), key: str = Form('')):
    try:
        data = await file.read()
        return await run_in_threadpool(run_ocr, file.filename or 'screenshot.png', data, key)
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)})
    except Exception as exc:
        return JSONResponse({'ok': False, 'message': '识别失败：' + str(exc)})


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='OCR 图片识别', description='截图 / 图片转文字（云端免费接口）', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8001)