# -*- coding: utf-8 -*-
# 网站收集（页面在 /site-collect）
# ------------------------------------------------------------
# 平时刷到有用的网址，随手记下来的地方。它要解决的不是「存不下」，
# 而是「存了却不知道当初为什么要存，三个月后翻回来一脸茫然」——
# 所以每条都必须写清「收集理由」，理由空着不让存。
#
# AI 只干一件事：帮你判断这个网站值不值得收、值在哪。
# 判据是服务端真去抓这个页面（标题 / 描述 / 正文前若干字），抓不到就直说抓不到，
# 不许凭域名猜「资源丰富、值得收藏」这种废话。分析结果连依据一起存进这条记录。
#
#   GET    /site-collect                  页面
#   GET    /site-collect/api/status        有没有模型 Key / 分类与状态的键名
#   GET    /site-collect/api/items         列表
#   POST   /site-collect/api/items         新增
#   GET    /site-collect/api/items/{id}    取一条
#   PATCH  /site-collect/api/items/{id}    修改
#   DELETE /site-collect/api/items/{id}    删除
#   POST   /site-collect/api/analyze       抓页面 + 让模型评一句值不值得收
#
# 数据存 SQLite（history 表，tool='site-collect'），走 storage.py，按登录名隔离。
# 页面认 ?r=<记录 id> 深链，「记录总览」能直接跳过来。
#
# 浏览器访问 http://127.0.0.1:8000/site-collect 即可使用。
from __future__ import annotations

import html as html_lib
import ipaddress
import json
import re
import socket
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import storage

try:
    from model_config import llm_endpoint, llm_key, llm_model
except Exception:  # 单独拿这个文件跑时的兜底
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'site-collect.html'

TOOL = 'site-collect'
LIST_MAX = 300          # 个人收藏，一次全读回来，前端自己搜，省一个接口

# 卡片字段：元组是 (键, 长度上限)。想加字段只改这里，前端表单跟着加一行输入框。
FIELDS = (
    ('url', 500),       # 网址，必填
    ('name', 80),       # 我给它起的名字
    ('why', 600),       # 收集理由 —— 这条工具的重点，空着不让存
    ('tags', 160),      # 标签，逗号分隔
    ('note', 600),      # 备注：怎么用 / 什么场合用
)

CATS = (
    ('tool', '工具'),
    ('learn', '学习·教程'),
    ('doc', '文档'),
    ('design', '设计·灵感'),
    ('ai', 'AI'),
    ('data', '数据'),
    ('media', '影音'),
    ('other', '其他'),
)

STATES = (
    ('new', '待看'),
    ('using', '常用'),
    ('archived', '归档'),
)

CAT_KEYS = tuple(k for k, _ in CATS)
STATE_KEYS = tuple(k for k, _ in STATES)
VERDICTS = ('worth', 'maybe', 'skip')


def _user(request: Request) -> str:
    """LoginGate 中间件把登录用户名写在这里；没登录根本走不到路由。"""
    return getattr(request.state, 'user', '') or ''


async def _body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _clean(value, limit: int) -> str:
    return ' '.join(str(value or '').split())[:limit]


def _pick(value, keys, default: str) -> str:
    """分类 / 状态只认白名单里的键，传了别的就当没传。"""
    key = str(value or '').strip()
    return key if key in keys else default


def _count(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _today() -> str:
    return date.today().isoformat()
# ---------------------------------------------------------------
# 抓网页：只要拿到「这个站到底是干什么的」就够了
#   带 SSRF 防护 —— 这是一个能对外访问的服务，不能让任何人借它去读内网
#   （只放行 http/https、域名必须解析到公网 IP、每次跳转都重新校验、限时限量）
# ---------------------------------------------------------------
UA = 'Mozilla/5.0 (compatible; ToolboxSiteCollect/1.0)'
MAX_BYTES = 300 * 1024      # 最多读 300KB，别让一个超大页面把内存吃光
MAX_TEXT = 1800             # 喂给模型的正文最长就这么长


def _norm_url(value) -> str:
    """把用户粘进来的东西收拾成能用的网址。只写 example.com 也认。"""
    url = re.sub(r'\s+', '', str(value or ''))[:500]
    if not url:
        return ''
    if url.startswith('//'):
        url = 'https:' + url
    if not re.match(r'^https?://', url, re.I):
        if re.match(r'^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}([/:?#]|$)', url, re.I):
            url = 'https://' + url
        else:
            return ''
    parts = urlsplit(url)
    if not parts.hostname:
        return ''
    return url


def _ip_ok(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified)


def _host_ok(host: str):
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False, '这个域名解析不了'
    if not infos:
        return False, '这个域名解析不了'
    for info in infos:
        if not _ip_ok(info[4][0]):
            return False, '这个地址指向内网，不能抓'
    return True, ''


def _decode(raw: bytes, ctype: str) -> str:
    name = ''
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:2048], re.I)
    if m:
        name = m.group(1).decode('ascii', 'ignore')
    elif 'charset=' in ctype:
        name = ctype.split('charset=')[-1].split(';')[0].strip()
    for cand in (name, 'utf-8', 'gbk'):
        if not cand:
            continue
        try:
            return raw.decode(cand)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode('utf-8', 'replace')


def _strip_tags(text: str) -> str:
    return html_lib.unescape(re.sub(r'<[^>]+>', ' ', text or ''))


def _meta_content(html: str, names) -> str:
    for name in names:
        m = re.search(r'<meta[^>]+(?:name|property)=["\']' + re.escape(name) + r'["\'][^>]*>',
                      html or '', re.I)
        if not m:
            continue
        c = re.search(r'content=["\'](.*?)["\']', m.group(0), re.I | re.S)
        if c:
            text = ' '.join(_strip_tags(c.group(1)).split())
            if text:
                return text[:300]
    return ''


def _visible(html: str) -> str:
    text = re.sub(r'(?is)<(script|style|noscript|template)[^>]*>.*?</\1>', ' ', html or '')
    text = re.sub(r'(?is)<!--.*?-->', ' ', text)
    text = ' '.join(_strip_tags(text).split())
    return text[:MAX_TEXT]


def _fetch(url: str, depth: int = 0) -> dict:
    """只读抓一个页面。返回 {'ok':True,title,desc,text} 或 {'ok':False,error}。"""
    if depth > 3:
        return {'ok': False, 'error': '这个地址跳转太多次了'}
    try:
        parts = urlsplit(url)
    except ValueError:
        return {'ok': False, 'error': '网址看不懂'}
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return {'ok': False, 'error': '只支持 http / https 的网址'}
    ok, err = _host_ok(parts.hostname)
    if not ok:
        return {'ok': False, 'error': err}
    try:
        resp = requests.get(url, timeout=(6, 12), allow_redirects=False, stream=True,
                            headers={'User-Agent': UA, 'Accept': 'text/html,*/*;q=0.8'})
    except requests.RequestException as exc:
        return {'ok': False, 'error': '连不上：' + str(exc)[:60]}
    try:
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get('Location') or ''
            if not loc:
                return {'ok': False, 'error': '这个地址只跳转，没有内容'}
            return _fetch(urljoin(url, loc), depth + 1)
        if resp.status_code >= 400:
            return {'ok': False, 'error': '服务返回 HTTP %d' % resp.status_code}
        ctype = (resp.headers.get('Content-Type') or '').lower()
        chunks, size = [], 0
        for chunk in resp.iter_content(8192):
            if not chunk:
                continue
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_BYTES:
                break
        raw = b''.join(chunks)
    except requests.RequestException as exc:
        return {'ok': False, 'error': '读到一半断了：' + str(exc)[:50]}
    finally:
        resp.close()

    if 'html' not in ctype and 'text' not in ctype and 'xml' not in ctype:
        return {'ok': False, 'error': '这个链接不是网页（%s）' % (ctype.split(';')[0] or '未知类型')}
    text = _decode(raw, ctype)
    title = ''
    m = re.search(r'(?is)<title[^>]*>(.*?)</title>', text)
    if m:
        title = ' '.join(_strip_tags(m.group(1)).split())[:120]
    return {'ok': True, 'title': title,
            'desc': _meta_content(text, ('description', 'og:description', 'twitter:description')),
            'text': _visible(text)}
# ---------------------------------------------------------------
# 让模型评一句「值不值得收」
# ---------------------------------------------------------------
def _extract_json(text: str) -> dict:
    raw = re.sub(r'```[a-zA-Z]*', '', text or '')
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _chat(system: str, user: str, max_tokens: int = 1400, temperature: float = 0.4):
    key = llm_key()
    if not key:
        return None, '服务端没有配置模型 Key'
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    try:
        resp = requests.post(llm_endpoint(), json=body, timeout=(10, 120),
                             headers={'Authorization': 'Bearer ' + key,
                                      'Content-Type': 'application/json'})
    except requests.RequestException as exc:
        return None, '模型请求失败：' + str(exc)[:80]
    if resp.status_code >= 400:
        return None, '模型返回错误 ' + str(resp.status_code)
    try:
        return resp.json()['choices'][0]['message']['content'], ''
    except (ValueError, KeyError, IndexError, TypeError):
        return None, '模型返回格式异常'


ANALYZE_SYS = '\n'.join([
    '有人往自己的收藏夹里存网址。你要判断的只有一件事：这个网站值不值得他收，值在哪。',
    '',
    '【第一条，也是最重要的一条】',
    '只根据我给你的材料说话。我看不到的地方（要不要注册、有没有免费额度、更新频率、',
    '是不是已经停更），你不许猜；实在要说，就写成「不确定」。我宁可你少说，也不要你编。',
    '如果材料里写着「没抓到页面」，那就只能凭网址本身说，并且在 basis 里写明',
    '「只有网址，没抓到页面内容」。',
    '',
    '【不许写的词】资源丰富、干货满满、非常实用、值得收藏、提升效率、',
    '赋能、闭环、底层逻辑。这些话等于没说，换成人话：它具体能给你什么。',
    '',
    '【要给的】',
    '  verdict —— worth（值得留）/ maybe（看你用不用得上）/ skip（别收了），三选一；',
    '  score   —— 0-100。评的是「对一个平时收集有用网址、之后要回看整理的人」的价值，',
    '             不是这个网站本身有多牛。说不出所以然就给 50-70，不许凑整到 90；',
    '  summary —— 一句话结论，30 字以内，说人话；',
    '  value   —— 2 到 4 条，每条 point（它到底能给你什么，20 字内）',
    '             + who（什么情况下该打开它，25 字内）。要具体到场景，',
    '             比如「写周报前找现成的图表配色」；',
    '  scene   —— 一句话：什么场合该想起它；',
    '  risk    —— 最可能让它变成「收藏了再也没打开」的原因，一句话。这条必须写；',
    '  suggest_why  —— 替他把收集理由写成一句第一人称的话，25-45 字，',
    '                  要像他自己会写的，比如「以后做数据图表可以直接抄这家的配色」；',
    '  suggest_tags —— 2 到 4 个标签，每个 2-6 字，比如「配色」「图表」「免费」；',
    '  basis        —— 你这条判断是从材料的哪来的，15 字以内，必填。',
    '                  它在哪一段写着，你就说哪一段，比如「页面描述」「正文第 3 段」。',
    '',
    '只输出 JSON，不要 Markdown 代码块：',
    '{"verdict":"maybe","score":70,"summary":"","value":[{"point":"","who":""}],',
    ' "scene":"","risk":"","suggest_why":"","suggest_tags":[""],"basis":""}',
])


def _material(url: str, name: str, page: dict) -> str:
    """把抓到的东西拼成给模型看的材料。有多少给多少，没抓到就明说。"""
    lines = ['网址：' + url]
    if name:
        lines.append('我给它起的名字：' + name)
    if page.get('ok'):
        if page.get('title'):
            lines.append('页面标题：' + page['title'])
        if page.get('desc'):
            lines.append('页面描述：' + page['desc'])
        body = page.get('text') or ''
        if body:
            lines.append('正文片段（已截断）：')
            lines.append(body)
        else:
            lines.append('正文：没抓到文字（可能是纯图片，或者要 JS 渲染才有内容）')
    else:
        lines.append('页面没抓到：' + str(page.get('error') or '未知原因'))
        lines.append('（注意：你看不到这个网站的任何内容，只能凭网址说话，'
                     'basis 里必须写明「只有网址，没抓到页面内容」）')
    return '\n'.join(lines)


def _norm_analysis(raw) -> dict:
    """把模型吐的东西（或前端回传的）归一化成能入库的 dict。不认识的一律丢掉。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    v = str(raw.get('verdict') or '').strip()
    out['verdict'] = v if v in VERDICTS else 'maybe'
    out['score'] = max(0, min(100, _count(raw.get('score'))))
    out['summary'] = _clean(raw.get('summary'), 200)
    values = []
    items = raw.get('value') if isinstance(raw.get('value'), list) else []
    for item in items:
        if isinstance(item, dict):
            point = _clean(item.get('point'), 80)
            who = _clean(item.get('who'), 80)
        else:
            point, who = _clean(item, 80), ''
        if point:
            values.append({'point': point, 'who': who})
    out['value'] = values[:4]
    out['scene'] = _clean(raw.get('scene'), 120)
    out['risk'] = _clean(raw.get('risk'), 120)
    out['suggest_why'] = _clean(raw.get('suggest_why'), 200)
    tags = raw.get('suggest_tags') if isinstance(raw.get('suggest_tags'), list) else []
    clean_tags = [_clean(t, 16) for t in tags]
    out['suggest_tags'] = [t for t in dict.fromkeys(clean_tags) if t][:4]
    out['basis'] = _clean(raw.get('basis'), 60)
    out['at'] = _clean(raw.get('at'), 20)
    if not (out['summary'] or out['value']):
        return {}
    return out


def _do_analyze(payload: dict) -> dict:
    """抓页面 + 问模型。慢活儿，调用处用 run_in_threadpool 包一层。"""
    url = _norm_url(payload.get('url'))
    if not url:
        return {'ok': False, 'message': '先填一个网址，比如 https://example.com'}
    name = _clean(payload.get('name'), 80)
    if not llm_key():
        return {'ok': False, 'message': '服务端没有配置模型 Key，AI 评估用不了（其他功能照常）'}
    page = _fetch(url)
    content, err = _chat(ANALYZE_SYS, _material(url, name, page))
    if content is None:
        return {'ok': False, 'message': err or '模型这次没说话', 'page': _page_out(page)}
    data = _extract_json(content)
    analysis = _norm_analysis(data)
    if not analysis:
        return {'ok': False, 'message': '模型返回的东西读不懂，再点一次试试', 'page': _page_out(page)}
    analysis['at'] = _today()
    return {'ok': True, 'analysis': analysis, 'page': _page_out(page)}


def _page_out(page: dict) -> dict:
    """抓取结果里能露给前端的部分（正文不进前端，太长没用）。"""
    if not page.get('ok'):
        return {'ok': False, 'error': str(page.get('error') or '没抓到'), 'title': '',
                'desc': '', 'chars': 0}
    return {'ok': True, 'error': '', 'title': page.get('title') or '',
            'desc': page.get('desc') or '', 'chars': len(page.get('text') or '')}
def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or '').replace('www.', '')
    except ValueError:
        return ''


def _shape(payload: dict) -> dict:
    """把库里存的 payload 归一化成完整的一条：老记录缺字段也能补齐。"""
    data = {}
    for key, limit in FIELDS:
        data[key] = _clean(payload.get(key), limit)
    # 网址只认 _norm_url 的结论：它说不行就是不行，不要退回去存原始字符串，
    # 否则 localhost:8000 这种会被原样塞进库（曾经真的漏过）
    data['url'] = _norm_url(data['url'])
    data['cat'] = _pick(payload.get('cat'), CAT_KEYS, 'other')
    data['state'] = _pick(payload.get('state'), STATE_KEYS, 'new')
    data['analysis'] = _norm_analysis(payload.get('analysis'))
    return data


def _out(row: dict) -> dict:
    """一行 history 变成前端要的字典。"""
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    item = _shape(payload)
    item['id'] = row.get('id')
    item['created_at'] = str(row.get('created_at') or '')
    item['title'] = str(row.get('title') or item['name'] or _host_of(item['url']))
    return item


def _meta() -> dict:
    """分类 / 状态的键名和中文名都由后端给，前端不另存一份。"""
    return {
        'cats': [{'k': k, 'n': n} for k, n in CATS],
        'states': [{'k': k, 'n': n} for k, n in STATES],
    }


# ---------------- 页面 ----------------
@router.get('/site-collect', response_class=HTMLResponse)
def site_collect_page() -> HTMLResponse:
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="font-family:system-ui"><h2>模板文件缺失</h2>'
                '<p>请确认 templates/site-collect.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.get('/site-collect/api/status')
def site_collect_status() -> JSONResponse:
    data = _meta()
    data['ok'] = True
    data['ai'] = bool(llm_key())
    return JSONResponse(data)


# ---------------- 记录：五件套 ----------------
@router.get('/site-collect/api/items')
def site_collect_list(request: Request) -> JSONResponse:
    try:
        rows = storage.list_records(TOOL, _user(request), LIST_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)}, status_code=503)
    data = _meta()
    data['ok'] = True
    data['ai'] = bool(llm_key())
    data['items'] = [_out(row) for row in rows]
    return JSONResponse(data)


@router.post('/site-collect/api/items')
async def site_collect_create(request: Request) -> JSONResponse:
    payload = await _body(request)
    data = _shape(payload)
    if not data['url']:
        return JSONResponse({'ok': False, 'message': '网址看着不对，写全一点，比如 https://example.com'},
                            status_code=400)
    if len(data['why']) < 4:
        return JSONResponse({'ok': False, 'message': '把收集理由写上（至少四个字）—— 这条空着，三个月后你就不记得为什么收它了。'},
                            status_code=400)
    if not data['name']:
        data['name'] = _host_of(data['url'])
    title = data['name'] or data['url']
    try:
        rec = storage.add_record(TOOL, _user(request), title, data)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    data['id'] = rec['id']
    data['created_at'] = rec['created_at']
    data['title'] = title
    return JSONResponse({'ok': True, 'item': data})


@router.get('/site-collect/api/items/{rid}')
def site_collect_one(rid: int, request: Request) -> JSONResponse:
    try:
        row = storage.get_record(_user(request), rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'message': '找不到这条收藏。'}, status_code=404)
    return JSONResponse({'ok': True, 'item': _out(row)})


@router.patch('/site-collect/api/items/{rid}')
async def site_collect_update(rid: int, request: Request) -> JSONResponse:
    """只认白名单字段，别让前端整包覆盖。"""
    patch = (await _body(request)).get('patch')
    if not isinstance(patch, dict):
        return JSONResponse({'ok': False, 'message': '没有要改的内容。'}, status_code=400)
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return JSONResponse({'ok': False, 'message': '找不到这条收藏。'}, status_code=404)
        payload = row['payload'] if isinstance(row['payload'], dict) else {}
        data = _shape(payload)
        changed = False

        for key, limit in FIELDS:
            if key in patch:
                value = _clean(patch.get(key), limit)
                if key == 'url':
                    value = _norm_url(value) or value
                if data[key] != value:
                    data[key] = value
                    changed = True

        if 'cat' in patch:
            value = _pick(patch.get('cat'), CAT_KEYS, data['cat'])
            if value != data['cat']:
                data['cat'] = value
                changed = True

        if 'state' in patch:
            value = _pick(patch.get('state'), STATE_KEYS, data['state'])
            if value != data['state']:
                data['state'] = value
                changed = True

        if 'analysis' in patch:
            value = _norm_analysis(patch.get('analysis'))
            if value != data['analysis']:
                data['analysis'] = value
                changed = True

        if not changed:
            return JSONResponse({'ok': True, 'item': _out(row)})
        if not data['url']:
            return JSONResponse({'ok': False, 'message': '网址不能空着。'}, status_code=400)
        if len(data['why']) < 4:
            return JSONResponse({'ok': False, 'message': '收集理由不能空着，至少四个字。'}, status_code=400)
        if not data['name']:
            data['name'] = _host_of(data['url'])

        affected = storage.update_record(user, rid, payload=data, title=data['name'] or data['url'])
        if not affected:
            return JSONResponse({'ok': False, 'message': '这条不属于当前账号。'}, status_code=403)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)

    item = dict(data)
    item['id'] = rid
    item['created_at'] = str(row.get('created_at') or '')
    item['title'] = data['name'] or data['url']
    return JSONResponse({'ok': True, 'item': item})


@router.delete('/site-collect/api/items/{rid}')
def site_collect_delete(rid: int, request: Request) -> JSONResponse:
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return JSONResponse({'ok': False, 'message': '找不到这条收藏。'}, status_code=404)
        affected = storage.delete_record(user, rid)
        if not affected:
            return JSONResponse({'ok': False, 'message': '这条不属于当前账号。'}, status_code=403)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    return JSONResponse({'ok': True})


@router.post('/site-collect/api/analyze')
async def site_collect_analyze(request: Request) -> JSONResponse:
    """抓页面 + 让模型评一句值不值得收。慢活儿丢线程池，别卡住事件循环。"""
    return JSONResponse(await run_in_threadpool(_do_analyze, await _body(request)))


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _app = FastAPI(title='网站收集', version='1.0.0')
    _app.include_router(router)
    uvicorn.run(_app, host='127.0.0.1', port=8031)