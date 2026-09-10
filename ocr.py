# -*- coding: utf-8 -*-
# OCR 文字识别：粘贴截图或上传图片 -> 本地 RapidOCR 离线识别 -> 输出可编辑文字。
# 完全免费：模型随依赖一起安装，无需密钥、无需联网。
import threading
import time
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'ocr.html'
_MAX_BYTES = 12 * 1024 * 1024
_ALLOWED_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff')

_engine = None
_lock = threading.Lock()


def _get_engine():
    """懒加载 OCR 引擎：首次调用时初始化模型，之后复用同一个实例。"""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        # 截图基本不会上下颠倒，关掉方向分类可以省一点耗时
        _engine = RapidOCR(use_cls=False)
    return _engine


def run_ocr(filename: str, data: bytes):
    """识别图片里的文字 -> {ok, text, lines, count, avg_score, elapsed}。"""
    if not data:
        raise ValueError('上传的图片是空的')
    if len(data) > _MAX_BYTES:
        raise ValueError('图片超过 %d MB，请压缩或裁剪后再试' % (_MAX_BYTES // (1024 * 1024)))
    ext = Path(filename or '').suffix.lower()
    if ext and ext not in _ALLOWED_EXT:
        raise ValueError('仅支持图片格式：png / jpg / webp / bmp / gif / tiff')

    started = time.time()
    try:
        engine = _get_engine()
        with _lock:
            result, _elapse = engine(data)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('识别失败：' + str(exc))

    lines = []
    for item in (result or []):
        if not item or len(item) < 3:
            continue
        text = str(item[1] or '').strip()
        if not text:
            continue
        try:
            score = round(float(item[2]), 3)
        except (TypeError, ValueError):
            score = 0.0
        lines.append({'text': text, 'score': score})

    scores = [ln['score'] for ln in lines if ln['score']]
    return {
        'ok': True,
        'text': '\n'.join(ln['text'] for ln in lines),
        'lines': lines,
        'count': len(lines),
        'avg_score': round(sum(scores) / len(scores), 3) if scores else 0.0,
        'elapsed': round(time.time() - started, 1),
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
async def ocr_recognize(file: UploadFile = File(...)):
    try:
        data = await file.read()
        return await run_in_threadpool(run_ocr, file.filename or 'screenshot.png', data)
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)})
    except Exception as exc:
        return JSONResponse({'ok': False, 'message': '识别失败：' + str(exc)})


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='OCR 文字识别', description='截图 / 图片转文字（本地离线、免费）', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8001)