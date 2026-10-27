# -*- coding: utf-8 -*-
# Streamlit 代码运行器：在隔离临时目录启动一个子进程，并通过同源 HTTP/WebSocket 代理嵌入。
import asyncio
import ast
import atexit
import json
import os
import re
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.websocket import websocket_connect

import auth

router = APIRouter()

TOOL = 'streamlit'
CODE_MAX = 50000
SESSION_TTL = 30 * 60
START_TIMEOUT = 35
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'streamlit.html'
_LOCK = threading.Lock()
_SESSIONS = {}

ALLOWED_IMPORTS = {
    'streamlit',
    'pandas',
    'numpy',
    'math',
    'random',
    'json',
    're',
    'datetime',
    'time',
    'statistics',
    'itertools',
    'functools',
    'collections',
    'typing',
    'altair',
}
BLOCKED_CALLS = {
    'eval', 'exec', 'compile', 'open', 'input', 'breakpoint',
    '__import__', 'globals', 'locals', 'vars',
}
HOP_HEADERS = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade', 'host',
    'content-length', 'accept-encoding',
}


@dataclass
class Session:
    token: str
    user: str
    port: int
    base: str
    tempdir: Path
    process: subprocess.Popen
    created: float
    timer: threading.Timer | None = None


def _clean(value, limit):
    return str(value or '').replace('\r\n', '\n').replace('\r', '\n')[:limit]


def _check_code(code):
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        line = exc.lineno or '?'
        return 'Python 语法错误（第 %s 行）：%s' % (line, exc.msg)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                root = item.name.split('.')[0]
                if root not in ALLOWED_IMPORTS:
                    return '不允许导入模块：%s' % item.name
        elif isinstance(node, ast.ImportFrom):
            root = str(node.module or '').split('.')[0]
            if root not in ALLOWED_IMPORTS:
                return '不允许导入模块：%s' % (node.module or '')
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BLOCKED_CALLS:
                return '不允许调用：%s()' % node.func.id
        elif isinstance(node, ast.Attribute) and node.attr.startswith('__'):
            return '不允许访问双下划线属性'
        elif isinstance(node, ast.Name) and node.id.startswith('__'):
            return '不允许使用双下划线名称'
    return ''


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _safe_env(tempdir):
    keep = ('PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP',
            'PYTHONUTF8', 'PYTHONIOENCODING')
    env = {k: os.environ[k] for k in keep if os.environ.get(k)}
    env.update({
        'HOME': str(tempdir),
        'USERPROFILE': str(tempdir),
        'STREAMLIT_BROWSER_GATHER_USAGE_STATS': 'false',
        'STREAMLIT_SERVER_HEADLESS': 'true',
    })
    return env


def _stop(session):
    if not session:
        return
    if session.timer:
        session.timer.cancel()
    _stop_tree(session.process)
    shutil.rmtree(session.tempdir, ignore_errors=True)


def _stop_tree(proc):
    """停止 Streamlit 进程树；Windows 下 streamlit 会再派生一个 Python 进程。"""
    if not proc or proc.poll() is not None:
        return
    try:
        if os.name == 'nt':
            subprocess.run(
                ['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _stop_all():
    with _LOCK:
        items = list(_SESSIONS.values())
        _SESSIONS.clear()
    for item in items:
        _stop(item)


atexit.register(_stop_all)


def _prune():
    now = time.time()
    expired = []
    with _LOCK:
        for token, item in list(_SESSIONS.items()):
            if now - item.created > SESSION_TTL or item.process.poll() is not None:
                expired.append(_SESSIONS.pop(token))
    for item in expired:
        _stop(item)


def _expire(token, user):
    with _LOCK:
        session = _SESSIONS.get(token)
        if not session or session.user != user:
            return
        _SESSIONS.pop(token, None)
    _stop(session)


def _touch(session):
    """从最后一次页面请求重新计算闲置时间。"""
    with _LOCK:
        if _SESSIONS.get(session.token) is not session:
            return
        if session.timer:
            session.timer.cancel()
        timer = threading.Timer(SESSION_TTL, _expire, args=(session.token, session.user))
        timer.daemon = True
        session.timer = timer
    timer.start()


def _launch(user, code):
    error = _check_code(code)
    if error:
        return None, error
    _prune()
    with _LOCK:
        previous = [s for s in _SESSIONS.values() if s.user == user]
        for item in previous:
            _SESSIONS.pop(item.token, None)
    for item in previous:
        _stop(item)

    tempdir = Path(tempfile.mkdtemp(prefix='tb-streamlit-'))
    script = tempdir / 'app.py'
    script.write_text(code, encoding='utf-8', newline='\n')
    token = secrets.token_urlsafe(16)
    base = '/streamlit-app/' + token
    port = _free_port()
    log_path = tempdir / 'streamlit.log'
    log = log_path.open('w', encoding='utf-8')
    cmd = [
        sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.headless=true',
        '--server.address=127.0.0.1',
        '--server.port=' + str(port),
        '--server.baseUrlPath=' + base,
        '--server.enableCORS=false',
        '--server.enableXsrfProtection=false',
        '--server.fileWatcherType=none',
        '--server.allowedHosts=127.0.0.1',
        '--server.allowedHosts=localhost',
        '--browser.gatherUsageStats=false',
        '--client.toolbarMode=minimal',
    ]
    try:
        popen_args = {
            'cwd': str(tempdir),
            'env': _safe_env(tempdir),
            'stdout': log,
            'stderr': subprocess.STDOUT,
        }
        if os.name == 'nt':
            popen_args['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        else:
            popen_args['start_new_session'] = True
        proc = subprocess.Popen(cmd, **popen_args)
    except Exception as exc:
        log.close()
        shutil.rmtree(tempdir, ignore_errors=True)
        return None, '启动 Streamlit 失败：' + str(exc)[:180]

    health_urls = [
        'http://127.0.0.1:%d%s/_stcore/health' % (port, base),
        'http://127.0.0.1:%d/_stcore/health' % port,
    ]
    deadline = time.time() + START_TIMEOUT
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        for url in health_urls:
            try:
                resp = requests.get(url, timeout=1)
                if resp.status_code == 200 and resp.text.strip().lower() == 'ok':
                    ready = True
                    break
            except requests.RequestException:
                pass
        if ready:
            break
        time.sleep(0.35)
    log.close()
    if not ready:
        try:
            detail = log_path.read_text(encoding='utf-8', errors='replace')[-800:]
        except OSError:
            detail = ''
        _stop_tree(proc)
        shutil.rmtree(tempdir, ignore_errors=True)
        return None, 'Streamlit 没有正常启动。' + (('\n' + detail) if detail else '')

    session = Session(token, user, port, base, tempdir, proc, time.time())
    with _LOCK:
        _SESSIONS[token] = session
    _touch(session)
    return session, ''


def _user(request):
    return getattr(request.state, 'user', '') or ''


async def _body(request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


@router.get('/streamlit', response_class=HTMLResponse)
def streamlit_page():
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="background:#101322;color:#fff;font-family:system-ui">'
        '<h2>模板文件缺失</h2><p>请确认 templates/streamlit.html 存在。</p>'
        '</body></html>'
    )


@router.get('/streamlit/api/status')
def streamlit_status():
    try:
        import streamlit as _st  # noqa: F401
        ready = True
    except Exception:
        ready = False
    return JSONResponse({'ok': True, 'ready': ready, 'max_chars': CODE_MAX})


@router.post('/streamlit/api/run')
async def streamlit_run(request: Request):
    payload = await _body(request)
    code = _clean(payload.get('code'), CODE_MAX + 1)
    if len(code.strip()) < 8:
        return JSONResponse({'ok': False, 'message': '先粘贴一段 Streamlit Python 代码。'}, status_code=400)
    if len(code) > CODE_MAX:
        return JSONResponse({'ok': False, 'message': '代码太长，请控制在 50000 字符以内。'}, status_code=400)
    session, error = await run_in_threadpool(_launch, _user(request), code)
    if error:
        return JSONResponse({'ok': False, 'message': error}, status_code=503)
    return JSONResponse({
        'ok': True,
        'token': session.token,
        'url': session.base + '/',
        'expires_in': SESSION_TTL,
    })


@router.post('/streamlit/api/stop/{token}')
def streamlit_stop(token: str, request: Request):
    with _LOCK:
        item = _SESSIONS.get(token)
        if not item or item.user != _user(request):
            return JSONResponse({'ok': True})
        _SESSIONS.pop(token, None)
    _stop(item)
    return JSONResponse({'ok': True})


@router.api_route('/streamlit-app/{token}/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
                  include_in_schema=False)
async def streamlit_proxy(token: str, path: str, request: Request):
    with _LOCK:
        session = _SESSIONS.get(token)
    if not session or session.user != _user(request):
        return Response('Streamlit session not found', status_code=404)
    _touch(session)
    base_path = session.base.strip('/')
    suffix = path.lstrip('/')
    target = 'http://127.0.0.1:%d/%s/' % (session.port, base_path)
    if suffix:
        target += suffix
    if request.url.query:
        target += '?' + request.url.query
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_HEADERS and k.lower() != 'cookie'
    }
    body = await request.body()
    try:
        upstream = await AsyncHTTPClient().fetch(
            HTTPRequest(
                url=target,
                method=request.method,
                headers=headers,
                body=body if body else None,
                follow_redirects=False,
                request_timeout=60,
            ),
            raise_error=False,
        )
    except Exception as exc:
        return Response('Streamlit upstream error: ' + str(exc)[:160], status_code=502)
    out_headers = {
        k: v for k, v in upstream.headers.get_all()
        if k.lower() not in HOP_HEADERS and k.lower() not in ('content-encoding',)
    }
    return Response(upstream.body, status_code=upstream.code, headers=out_headers)


@router.websocket('/streamlit-app/{token}/_stcore/stream')
async def streamlit_ws(ws: WebSocket, token: str):
    with _LOCK:
        session = _SESSIONS.get(token)
    user = auth.read_token(ws.cookies.get(auth.COOKIE_NAME)) or ''
    if not session or session.user != user:
        await ws.close(code=1008)
        return
    base_path = session.base.strip('/')
    target = 'ws://127.0.0.1:%d/%s/_stcore/stream' % (session.port, base_path)
    try:
        upstream = await websocket_connect(
            HTTPRequest(
                url=target,
                headers={'Origin': 'http://127.0.0.1:%d' % session.port},
                request_timeout=15,
            ),
            subprotocols=ws.scope.get('subprotocols') or None,
        )
    except Exception:
        await ws.close(code=1011)
        return
    await ws.accept(subprotocol=upstream.selected_subprotocol)

    async def client_to_upstream():
        try:
            while True:
                msg = await ws.receive()
                if msg.get('type') == 'websocket.disconnect':
                    break
                if msg.get('bytes') is not None:
                    upstream.write_message(msg['bytes'], binary=True)
                elif msg.get('text') is not None:
                    upstream.write_message(msg['text'])
        except WebSocketDisconnect:
            pass

    async def upstream_to_client():
        try:
            while True:
                msg = await upstream.read_message()
                if msg is None:
                    break
                if isinstance(msg, bytes):
                    await ws.send_bytes(msg)
                else:
                    await ws.send_text(str(msg))
        except Exception:
            pass

    tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        upstream.close()
        try:
            await ws.close()
        except Exception:
            pass


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI

    _app = FastAPI(title='Streamlit 运行器', version='1.0.0')
    _app.include_router(router)
    uvicorn.run(_app, host='127.0.0.1', port=8015)
