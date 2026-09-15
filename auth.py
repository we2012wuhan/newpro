# -*- coding: utf-8 -*-
"""
登录 / 会话 / 访问控制
=====================
这个模块给整个工具箱加一道门：没登录的人进不了首页，也调不到任何接口。

设计上只依赖 Python 标准库（不引入 passlib / python-jose 之类），
因为这里跑的是本机单用户的小工具，需要的是"够用且看得懂"：

  1. 密码不落明文 —— PBKDF2-HMAC-SHA256 + 每条账号独立随机盐，存 data/auth.json
  2. 会话不落库   —— HMAC-SHA256 签名的令牌放在 HttpOnly Cookie 里，服务端只验签
  3. 密钥不硬编码 —— data/secret.key 首次启动自动生成，重启后已登录的人不会被踢下线
  4. 上云靠环境变量 —— 部署到 Vercel 这种只读文件系统时，TB_USERNAME / TB_PASSWORD
     直接当账号、TB_SECRET 当签名密钥，一个字节都不落盘（详见下面 env_account）

三条安全底线：
  - 改密码会把所有旧会话作废（令牌里带了密码指纹）
  - 连续输错 6 次锁 5 分钟，防止有人在本机慢慢试密码
  - 跨站发起的写请求（POST / PATCH / DELETE）直接拒绝，防 CSRF
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'login.html'
_DATA_DIR = _BASE_DIR / 'data'
_ACCOUNT_FILE = _DATA_DIR / 'auth.json'
_SECRET_FILE = _DATA_DIR / 'secret.key'

# ---------------------------------------------------------------
# 环境变量优先：Vercel / 云函数的文件系统除 /tmp 外是只读的，
# 账号和密钥都没法落盘，所以给一条「配置即账号」的路：
#   TB_USERNAME + TB_PASSWORD             必配，服务端 PBKDF2 后比对
#   TB_PASSWORD_DIGEST / _SALT / _ROUNDS  只想放摘要时用，可替代 TB_PASSWORD
#   TB_SECRET                             可选，会话签名密钥；不配就从账号派生
# 一个都没配 -> 退回本机 data/auth.json，本地开发流程完全不变。
# ---------------------------------------------------------------
ENV_USER = 'TB_USERNAME'
ENV_PASSWORD = 'TB_PASSWORD'
ENV_DIGEST = 'TB_PASSWORD_DIGEST'
ENV_SALT = 'TB_PASSWORD_SALT'
ENV_ROUNDS = 'TB_PASSWORD_ROUNDS'
ENV_SECRET = 'TB_SECRET'

COOKIE_NAME = 'tb_session'
PBKDF2_ROUNDS = 200_000
SESSION_TTL = 7 * 86400          # 默认 7 天
SESSION_TTL_LONG = 30 * 86400    # 勾了「记住我」30 天
MIN_PASSWORD = 6
MAX_FAILS = 6                    # 同一 IP 连续失败次数
FAIL_WINDOW = 300                # 触发锁定后的冷却秒数

# 白名单：只有这些路径不登录也能访问
PUBLIC_PATHS = {
    '/login', '/favicon.ico', '/favicon.svg',
    '/api/auth/login', '/api/auth/logout', '/api/auth/me',
    '/api/auth/status', '/api/auth/setup',
}
PUBLIC_PREFIXES = ('/static/',)


def _now() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _data_dir() -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR


# ---------------------------------------------------------------
# 账号：只存盐和摘要，不存明文
#   配了环境变量 -> 用环境变量那份（只读文件系统上唯一可行的办法）
#   没配         -> 读本机的 data/auth.json
# ---------------------------------------------------------------
def _env(name: str) -> str:
    return (os.environ.get(name) or '').strip()


def _env_rounds() -> int:
    try:
        return int(_env(ENV_ROUNDS) or PBKDF2_ROUNDS)
    except ValueError:
        return PBKDF2_ROUNDS


def _env_salt(username: str) -> str:
    """没给 TB_PASSWORD_SALT 时的固定盐。

    这个盐不落盘也不对外，作用只是把「明文比明文」换成「摘要比摘要」。
    关键是它必须每次进程启动都一样：盐一变，令牌里的密码指纹就变，
    多实例之间、冷启动前后就会互相把对方踢下线。
    """
    return hashlib.sha256(('tb-env-salt:' + username).encode('utf-8')).hexdigest()[:32]


_ENV_CACHE: dict = {}


def env_mode() -> bool:
    """环境变量里有没有提到账号（决定还能不能走网页建号）。"""
    return bool(_env(ENV_USER) or _env(ENV_PASSWORD) or _env(ENV_DIGEST))


def env_account() -> dict | None:
    """账号来自环境变量时返回它，否则 None。"""
    username = _env(ENV_USER)
    if not username:
        return None
    digest = _env(ENV_DIGEST)
    salt = _env(ENV_SALT) or _env_salt(username)
    rounds = _env_rounds()
    if digest:
        key = ('d', username, digest, salt, rounds)
    else:
        password = _env(ENV_PASSWORD)
        if not password:
            return None
        key = ('p', username, password, salt, rounds)
    # PBKDF2 一次要一百多毫秒，进程内缓存住，别每个请求都算一遍
    if _ENV_CACHE.get('key') == key:
        return _ENV_CACHE['account']
    if not digest:
        digest = _digest(password, salt, rounds)
    account = {
        'username': username, 'salt': salt, 'digest': digest, 'rounds': rounds,
        'created_at': None, 'updated_at': None, 'from_env': True,
    }
    _ENV_CACHE['key'] = key
    _ENV_CACHE['account'] = account
    return account


def load_account() -> dict | None:
    account = env_account()
    if account is not None:
        return account
    try:
        data = json.loads(_ACCOUNT_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get('username') or not data.get('digest'):
        return None
    return data


def save_account(username: str, password: str) -> dict:
    salt = secrets.token_hex(16)
    digest = _digest(password, salt, PBKDF2_ROUNDS)
    old = load_account() or {}
    account = {
        'username': username,
        'salt': salt,
        'digest': digest,
        'rounds': PBKDF2_ROUNDS,
        'created_at': old.get('created_at') or _now(),
        'updated_at': _now(),
    }
    _data_dir()
    _ACCOUNT_FILE.write_text(json.dumps(account, ensure_ascii=False, indent=2), encoding='utf-8')
    return account


def _digest(password: str, salt: str, rounds: int) -> str:
    raw = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), int(rounds))
    return raw.hex()


def verify_password(account: dict, password: str) -> bool:
    if not account:
        return False
    try:
        expect = _digest(password, account['salt'], int(account.get('rounds') or PBKDF2_ROUNDS))
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(expect, str(account.get('digest') or ''))


def _fingerprint(account: dict) -> str:
    """密码指纹：写进令牌，改密码就自动作废旧会话。"""
    return str(account.get('digest') or '')[:12]


# ---------------------------------------------------------------
# 会话令牌：base64(用户名|过期时间|密码指纹|随机数) + HMAC 签名
# ---------------------------------------------------------------
def _secret() -> bytes:
    # 1) TB_SECRET：换成 32 字节摘要，填多长都行
    value = _env(ENV_SECRET)
    if value:
        return hashlib.sha256(('tb-session-key:' + value).encode('utf-8')).digest()
    # 2) 没给就从账号凭据派生：每个实例算出来都一样，登录态才不会飘
    account = env_account()
    if account:
        base = '|'.join([account['username'], account['salt'], account['digest']])
        return hashlib.sha256(('tb-session-key:' + base).encode('utf-8')).digest()
    # 3) 本机：data/secret.key 里那份随机密钥
    try:
        raw = _SECRET_FILE.read_bytes()
        if len(raw) >= 32:
            return raw
    except OSError:
        pass
    raw = secrets.token_bytes(32)
    _data_dir()
    _SECRET_FILE.write_bytes(raw)
    return raw


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode('ascii'), hashlib.sha256).hexdigest()[:43]


def make_token(account: dict, ttl: int) -> str:
    raw = '%s|%d|%s|%s' % (
        account['username'], int(time.time()) + int(ttl),
        _fingerprint(account), secrets.token_hex(6))
    body = base64.urlsafe_b64encode(raw.encode('utf-8')).decode('ascii').rstrip('=')
    return body + '.' + _sign(body)


def read_token(token: str | None) -> str | None:
    """验签 + 检查过期 + 比对密码指纹，任何一步不过都返回 None。"""
    if not token or token.count('.') != 1:
        return None
    body, sig = token.split('.')
    try:
        if not hmac.compare_digest(sig, _sign(body)):
            return None
    except (UnicodeEncodeError, TypeError):
        return None
    try:
        padded = body + '=' * (-len(body) % 4)
        raw = base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8')
    except Exception:
        return None
    parts = raw.split('|')
    if len(parts) != 4:
        return None
    username, expires, fingerprint, _nonce = parts
    try:
        if int(expires) < time.time():
            return None
    except ValueError:
        return None
    account = load_account()
    if not account or username != account.get('username'):
        return None
    if fingerprint != _fingerprint(account):
        return None
    return username

# ---------------------------------------------------------------
# Cookie
# ---------------------------------------------------------------
def _is_https(request: Request) -> bool:
    if request.url.scheme == 'https':
        return True
    return (request.headers.get('x-forwarded-proto') or '').split(',')[0].strip() == 'https'


def attach_session(response, token: str, ttl: int, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=int(ttl), httponly=True, samesite='lax',
        secure=bool(secure), path='/')


def drop_session(response) -> None:
    response.delete_cookie(COOKIE_NAME, path='/')


# ---------------------------------------------------------------
# 登录失败限流：内存里记一份就够，重启即清零
# ---------------------------------------------------------------
_FAILS: dict = {}


def _client_ip(request: Request) -> str:
    forward = (request.headers.get('x-forwarded-for') or '').split(',')[0].strip()
    if forward:
        return forward
    return request.client.host if request.client else 'unknown'


def _locked_left(ip: str) -> int:
    record = _FAILS.get(ip)
    if not record:
        return 0
    count, last = record
    left = int(FAIL_WINDOW - (time.time() - last))
    if count >= MAX_FAILS and left > 0:
        return left
    if left <= 0:
        _FAILS.pop(ip, None)
    return 0


def _record_fail(ip: str) -> None:
    count, _last = _FAILS.get(ip, (0, 0.0))
    _FAILS[ip] = (count + 1, time.time())


# ---------------------------------------------------------------
# 访问控制中间件：所有请求先过这道门
# ---------------------------------------------------------------
def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _header(scope, name: str) -> str:
    for key, value in scope.get('headers') or []:
        if key.decode('latin-1').lower() == name:
            return value.decode('latin-1')
    return ''


def _cookie_token(scope) -> str | None:
    raw = _header(scope, 'cookie')
    if not raw:
        return None
    for chunk in raw.split(';'):
        name, _, value = chunk.strip().partition('=')
        if name == COOKIE_NAME:
            return value
    return None


def _expects_json(scope) -> bool:
    path = scope.get('path', '')
    if '/api/' in path or path.startswith('/api/'):
        return True
    if path.endswith('.json'):
        return True
    method = (scope.get('method') or 'GET').upper()
    if method not in ('GET', 'HEAD'):
        return True
    return 'application/json' in _header(scope, 'accept').lower()


SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')


def _same_origin(scope) -> bool:
    """写请求必须来自本站，挡住「其它网站偷偷拿你的浏览器发请求」。

    两道判据，任意一道不过就拒绝：
      Origin 头 —— 浏览器对跨站写请求一定会带，且站点无法伪造
      Sec-Fetch-Site —— 现代浏览器直接标注 cross-site
    curl / 脚本这类非浏览器客户端不带这些头，交给 Cookie 本身把关。
    """
    site = _header(scope, 'sec-fetch-site').lower()
    if site in ('cross-site', 'same-site'):
        return False
    origin = _header(scope, 'origin')
    if not origin:
        return True
    if origin == 'null':
        return False
    host = _header(scope, 'host')
    return origin.split('://')[-1].lower() == host.lower()


async def _forbidden(scope, receive, send, detail: str) -> None:
    response = JSONResponse({'ok': False, 'detail': detail}, status_code=403)
    await response(scope, receive, send)


async def _deny(scope, receive, send) -> None:
    path = scope.get('path', '/')
    if _expects_json(scope):
        response = JSONResponse(
            {'ok': False, 'need_login': True, 'detail': '请先登录', 'login': '/login'},
            status_code=401)
    else:
        query = (scope.get('query_string') or b'').decode('latin-1')
        target = path + (('?' + query) if query else '')
        target = '' if target == '/' else ('?next=' + quote(target, safe=''))
        response = RedirectResponse('/login' + target, status_code=303)
        response.headers['Cache-Control'] = 'no-store'
    await response(scope, receive, send)


class LoginGate:
    """没登录就别想进来 —— 页面跳登录页，接口直接 401。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            await self.app(scope, receive, send)
            return

        path = scope.get('path', '/')
        method = (scope.get('method') or 'GET').upper()

        # 先查来源，再谈身份：跨站发来的写请求，登录了也不能放行
        if method not in SAFE_METHODS and not _same_origin(scope):
            await _forbidden(scope, receive, send, '跨站请求被拒绝')
            return

        user = read_token(_cookie_token(scope))
        state = scope.setdefault('state', {})
        state['user'] = user

        if user or is_public(path):
            await self.app(scope, receive, send)
            return

        await _deny(scope, receive, send)


# ---------------------------------------------------------------
# 路由
# ---------------------------------------------------------------
router = APIRouter()

_LOGIN_FALLBACK = (
    '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
    '<body style="background:#0a0f1e;color:#e8edf7;font-family:system-ui">'
    '<h2>需要登录</h2><p>页面模板缺失：templates/login.html</p></body></html>')


def _safe_next(value: str) -> str:
    """只允许跳到站内路径，挡住 //evil.com 这种开放重定向。"""
    target = str(value or '').strip()
    if not target.startswith('/') or target.startswith('//') or target.startswith('/\\'):
        return '/'
    return target


def _login_html(setup: bool, username: str, nxt: str) -> str:
    try:
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    except OSError:
        return _LOGIN_FALLBACK
    boot = json.dumps({'setup': bool(setup), 'username': username, 'next': nxt}, ensure_ascii=False)
    return html.replace('"__AUTH_BOOT__"', boot)


@router.get('/login', response_class=HTMLResponse)
def login_page(request: Request, next: str = '/', error: str = '') -> HTMLResponse:
    account = load_account()
    nxt = _safe_next(next)
    if getattr(request.state, 'user', None):
        return RedirectResponse(nxt, status_code=303)
    response = HTMLResponse(_login_html(account is None, (account or {}).get('username', ''), nxt))
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/api/auth/status')
def api_status(request: Request) -> JSONResponse:
    account = load_account()
    user = getattr(request.state, 'user', None)
    return JSONResponse({
        'ok': True,
        'setup': account is None,
        'username': (account or {}).get('username'),
        'logged_in': bool(user),
        'user': user,
    })


@router.get('/api/auth/me')
def api_me(request: Request) -> JSONResponse:
    user = getattr(request.state, 'user', None)
    return JSONResponse({'ok': bool(user), 'user': user})


@router.post('/api/auth/setup')
def api_setup(request: Request, payload: dict = Body(...)) -> JSONResponse:
    if env_mode() and env_account() is None:
        raise HTTPException(status_code=400,
                            detail='环境变量只配了一半：TB_USERNAME 和 TB_PASSWORD 要一起配')
    if load_account() is not None:
        detail = ('账号已经由环境变量配好了，直接登录就行' if env_mode()
                  else '账号已经创建过了，直接登录就行')
        raise HTTPException(status_code=400, detail=detail)
    username = str(payload.get('username') or '').strip()[:32]
    password = str(payload.get('password') or '')
    if len(username) < 2:
        raise HTTPException(status_code=400, detail='用户名至少 2 个字符')
    if len(password) < MIN_PASSWORD:
        raise HTTPException(status_code=400, detail='密码至少 %d 位' % MIN_PASSWORD)
    if password != str(payload.get('confirm') or ''):
        raise HTTPException(status_code=400, detail='两次输入的密码不一样')
    account = save_account(username, password)
    ttl = SESSION_TTL
    response = JSONResponse({'ok': True, 'username': username})
    attach_session(response, make_token(account, ttl), ttl, _is_https(request))
    return response


@router.post('/api/auth/login')
def api_login(request: Request, payload: dict = Body(...)) -> JSONResponse:
    ip = _client_ip(request)
    left = _locked_left(ip)
    if left:
        raise HTTPException(status_code=429, detail='密码错太多次了，%d 秒后再试' % left)

    account = load_account()
    if account is None:
        raise HTTPException(status_code=400, detail='还没有创建账号，刷新页面先设置一个')

    username = str(payload.get('username') or '').strip()
    password = str(payload.get('password') or '')
    if username != account['username'] or not verify_password(account, password):
        _record_fail(ip)
        remain = MAX_FAILS - _FAILS.get(ip, (0, 0))[0]
        tip = '用户名或密码不对'
        if 0 < remain <= 2:
            tip += '（再错 %d 次要等 5 分钟）' % remain
        raise HTTPException(status_code=401, detail=tip)

    _FAILS.pop(ip, None)
    ttl = SESSION_TTL_LONG if payload.get('remember') else SESSION_TTL
    response = JSONResponse({'ok': True, 'username': account['username']})
    attach_session(response, make_token(account, ttl), ttl, _is_https(request))
    return response


@router.post('/api/auth/logout')
def api_logout() -> JSONResponse:
    response = JSONResponse({'ok': True})
    drop_session(response)
    return response


@router.post('/api/auth/password')
def api_change_password(request: Request, payload: dict = Body(...)) -> JSONResponse:
    user = getattr(request.state, 'user', None)
    if not user:
        raise HTTPException(status_code=401, detail='请先登录')
    if env_mode():
        raise HTTPException(status_code=400,
                            detail='密码来自环境变量，要改就去改 TB_PASSWORD，改完重新部署')
    account = load_account()
    if not verify_password(account, str(payload.get('old') or '')):
        raise HTTPException(status_code=400, detail='原密码不对')
    new = str(payload.get('new') or '')
    if len(new) < MIN_PASSWORD:
        raise HTTPException(status_code=400, detail='新密码至少 %d 位' % MIN_PASSWORD)
    if new != str(payload.get('confirm') or ''):
        raise HTTPException(status_code=400, detail='两次输入的新密码不一样')
    account = save_account(user, new)
    ttl = SESSION_TTL
    response = JSONResponse({'ok': True, 'detail': '密码已更新，其它设备上的登录已失效'})
    attach_session(response, make_token(account, ttl), ttl, _is_https(request))
    return response