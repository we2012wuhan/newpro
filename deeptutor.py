# -*- coding: utf-8 -*-
# DeepTutor 集成壳：DeepTutor（HKUDS 开源的个人化 AI 辅导工作区）是独立自托管应用，
# 前端面板在 http://localhost:3782，后端 API 在 http://localhost:8001。
# 本模块不做二次实现，只负责：在工具箱里提供统一入口页 -> 探测服务是否在运行 ->
# 未运行时给出启动指引；运行时支持「新标签打开」或「页面内嵌入」。
import json
import os
import time
from pathlib import Path

import requests
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'deeptutor.html'

# 可通过环境变量覆盖默认地址（例如部署在别的机器/端口时）
_DEFAULT_FRONT = (os.environ.get('DEEPTUTOR_URL') or 'http://localhost:3782').strip()
_DEFAULT_API = (os.environ.get('DEEPTUTOR_API_URL') or 'http://localhost:8001').strip()
_CHECK_TIMEOUT = 3.0
_GITHUB = 'https://github.com/HKUDS/DeepTutor'


def _normalize_url(url):
    url = str(url or '').strip()
    if not url:
        return ''
    if '://' not in url:
        url = 'http://' + url
    return url.rstrip('/')


def _probe(url):
    """探测某个地址是否可达：返回字典，ok=True 表示能连上服务。"""
    url = _normalize_url(url)
    if not url:
        return {'url': '', 'ok': False, 'ms': 0, 'error': '地址为空'}
    started = time.time()
    try:
        resp = requests.get(
            url, timeout=_CHECK_TIMEOUT, allow_redirects=True,
            headers={'User-Agent': 'toolbox-status-check'},
        )
        ms = round((time.time() - started) * 1000)
        # 4xx/3xx 说明服务本身在运行，只是某个路径不存在；只有连不上才算失败
        ok = resp.status_code < 500
        return {'url': url, 'ok': ok, 'status': resp.status_code, 'ms': ms}
    except requests.exceptions.Timeout:
        return {'url': url, 'ok': False, 'ms': round((time.time() - started) * 1000), 'error': '连接超时'}
    except requests.exceptions.ConnectionError:
        return {'url': url, 'ok': False, 'ms': round((time.time() - started) * 1000), 'error': '无法连接（服务未启动）'}
    except requests.exceptions.RequestException as exc:
        return {'url': url, 'ok': False, 'ms': round((time.time() - started) * 1000), 'error': str(exc)[:120]}


def _default_config():
    return {
        'front': _DEFAULT_FRONT,
        'api': _DEFAULT_API,
        'doc': _GITHUB,
    }


@router.get('/deep-tutor', response_class=HTMLResponse)
def deep_tutor_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
        html = html.replace('__DT_CONFIG__', json.dumps(_default_config(), ensure_ascii=False))
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/deeptutor.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.post('/deep-tutor/api/status')
def deep_tutor_status(payload: dict):
    """探测 DeepTutor 前端面板 / 后端 API 是否可达。"""
    front = _probe((payload or {}).get('front'))
    api = _probe((payload or {}).get('api'))
    return JSONResponse({
        'ok': bool(front.get('ok') or api.get('ok')),
        'front': front,
        'api': api,
    })
