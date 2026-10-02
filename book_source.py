# -*- coding: utf-8 -*-
"""
真实书讯抓取 —— 读书落地的「材料层」
====================================
为什么要有这一层：只给模型一个书名，它就会顺着记忆编——编出书里没有的章节、
实验、人名。先拿到这本书在网上的真实资料（简介、目录、读者短评、评分、出版社），
再让模型基于这份材料说话，它才编不动。

多源，谁通用谁：
    douban       豆瓣读书   境内可达，材料最全（简介 / 目录 / 短评 / 评分）
    googlebooks  Google Books  （境外可用）
    openlibrary  Open Library  （境外可用）
    在阿里云北京实测：只有 douban 通，另外两个会超时 —— 所以连不上的源会被
    记进「小黑屋」，10 分钟内不再试它，避免每次搜索都白等 4 秒。

统一成同一个形状（所有源都返回这个）：
    {source, sid, title, author, year, cover, rating, votes,
     publisher, isbn, pages, summary, toc[], reviews[], tags[], url}
抓不到的字段就是空字符串 / 空列表，页面按「没有」处理，不要编。

环境变量：
    BOOK_SOURCES   用哪些源、按什么顺序，逗号分隔，默认 douban,googlebooks,openlibrary
"""
from __future__ import annotations

import html as _html
import os
import queue
import re
import threading
import time

import requests

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
TIMEOUT = 6            # 单个请求超时（秒）
DEAD_TTL = 600         # 源不可用后，多久之内不再试
GRACE = 0.8            # 并行搜索时，等慢源的最后一点耐心
SEARCH_CAP = 8         # 搜索整体最多等多久

_cache = {}            # (source, sid) -> (时间戳, 材料)，省掉同一本书的重复抓取
_cache_lock = threading.Lock()
CACHE_TTL = 3600

_dead = {}
_dead_lock = threading.Lock()

SUMMARY_MAX = 1200
TOC_MAX = 40
REVIEW_MAX = 12


def _is_dead(name: str) -> bool:
    with _dead_lock:
        until = _dead.get(name, 0)
        if not until:
            return False
        if until > time.time():
            return True
        _dead.pop(name, None)
        return False


def _mark_dead(name: str, why: str = '') -> None:
    with _dead_lock:
        _dead[name] = time.time() + DEAD_TTL
    if why:
        print('[book_source] %s 暂时不可用：%s' % (name, why))


def enabled_sources() -> list:
    """配置里写了哪些源（不管当下通不通）。"""
    return _enabled()


def alive_sources() -> list:
    return [n for n in _enabled() if not _is_dead(n)]


def _enabled() -> list:
    raw = (os.environ.get('BOOK_SOURCES') or 'douban,googlebooks,openlibrary').strip()
    names = [x.strip().lower() for x in raw.split(',') if x.strip()]
    return [n for n in names if n in SOURCES]


def _text(raw) -> str:
    """把一段 HTML 变成纯文本：去标签、还原实体、把空行去掉。"""
    raw = re.sub(r'(?is)<(script|style).*?</\1>', ' ', raw or '')
    raw = re.sub(r'(?s)<[^>]+>', '\n', raw)
    for a, b in (('&nbsp;', ' '), ('&amp;', '&'), ('&quot;', '"'),
                 ('&lt;', '<'), ('&gt;', '>'), ('&#39;', "'"), ('&middot;', '·')):
        raw = raw.replace(a, b)
    return [x.strip() for x in raw.split('\n') if x.strip()]


def _join(raw, limit) -> str:
    return re.sub(r'\s+', ' ', ' '.join(_text(raw))).strip()[:limit]


def _get(url, params=None, timeout=TIMEOUT, encoding=None):
    resp = requests.get(url, params=params, timeout=timeout,
                        headers={'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9'})
    resp.raise_for_status()
    if encoding:
        resp.encoding = encoding
    return resp


# =============================================================
# 豆瓣
# =============================================================
def _douban_search(query, limit):
    # 注意：这个接口带空格会返回空数组，所以只拿书名去搜，作者在本地筛
    resp = _get('https://book.douban.com/j/subject_suggest', {'q': query}, timeout=TIMEOUT)
    try:
        rows = resp.json()
    except ValueError:
        return []
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get('type') != 'b':
            continue
        sid = str(row.get('id') or '').strip()
        title = (row.get('title') or '').strip()
        if not sid or not title:
            continue
        out.append({
            'source': 'douban', 'sid': sid, 'title': title,
            'author': (row.get('author_name') or '').strip(),
            'year': (row.get('year') or '').strip(),
            'cover': (row.get('pic') or '').strip(),
            'rating': '', 'votes': 0,
        })
        if len(out) >= limit:
            break
    return out


def _douban_fetch(sid):
    resp = _get('https://book.douban.com/subject/%s/' % sid, timeout=TIMEOUT, encoding='utf-8')
    if resp.status_code != 200:
        return None
    raw = resp.text

    def pick(pattern, flags=re.S):
        m = re.search(pattern, raw, flags)
        return m.group(1).strip() if m else ''

    info_raw = pick(r'id="info"(.*?)</div>\s*</div>')
    if not info_raw:
        info_raw = pick(r'id="info"(.*?)</div>')

    def info_field(label):
        # 豆瓣这里两种写法混用：「出版社:」冒号在 span 里，「作者」冒号在 span 外，
        # 所以两边的冒号都要写成可选，否则作者永远抓不到。
        m = re.search(r'<span class="pl">\s*%s\s*[：:]?\s*</span>\s*[：:]?\s*(.*?)<br' % label,
                      raw, re.S)
        return _join(m.group(1), 80) if m else ''

    toc = []
    toc_raw = pick(r'id="dir_\d+_full"(.*?)</div>\s*</div>') or pick(r'id="dir_\d+_full"(.*?)</div>')
    if toc_raw:
        for t in _text(toc_raw):
            if 'display:none' in t or 'style=' in t:
                continue
            if t in ('收起', '(', ')', '展开全部'):
                continue
            # 「· · · · · ·」这种是省略号按钮，不是目录项
            if not re.search(r'[\u4e00-\u9fffA-Za-z0-9]', t):
                continue
            toc.append(t)
        toc = toc[:TOC_MAX]

    reviews = []
    try:
        cr = _get('https://book.douban.com/subject/%s/comments/?status=P' % sid,
                  timeout=TIMEOUT, encoding='utf-8')
        if cr.status_code == 200:
            reviews = [_join(x, 120) for x in re.findall(r'<span class="short">(.*?)</span>', cr.text, re.S)]
            reviews = [r for r in reviews if r][:REVIEW_MAX]
    except requests.RequestException:
        pass

    tags = []
    m = re.search(r"criteria\s*=\s*'([^']*)'", raw)
    if m:
        tags = [p.split(':', 1)[1] for p in m.group(1).split('|')
                if p.startswith('7:') and len(p) > 2][:10]

    return {
        'source': 'douban', 'sid': str(sid),
        'title': pick(r'<span property="v:itemreviewed">([^<]+)</span>'),
        'author': info_field('作者'),
        'year': info_field('出版年'),
        'cover': pick(r'<img src="([^"]+)"[^>]*rel="v:image"') or pick(r'id="mainpic".*?<img src="([^"]+)"'),
        'rating': pick(r'property="v:average"[^>]*>\s*([\d.]+)'),
        'votes': int(pick(r'property="v:votes">(\d+)') or 0),
        'publisher': info_field('出版社'),
        'isbn': info_field('ISBN'),
        'pages': info_field('页数'),
        'summary': _join(pick(r'id="link-report"(.*?)</div>\s*</div>') or pick(r'id="link-report"(.*?)</div>'),
                         SUMMARY_MAX).lstrip('> ').strip(),
        'toc': toc,
        'reviews': reviews,
        'tags': tags,
        'url': 'https://book.douban.com/subject/%s/' % sid,
    }

# =============================================================
# Google Books（境外可用；境内连不上，会被自动跳过）
# =============================================================
def _gbooks_search(query, limit):
    resp = _get('https://www.googleapis.com/books/v1/volumes',
                {'q': 'intitle:' + query, 'maxResults': max(1, min(limit, 20)), 'country': 'US'},
                timeout=TIMEOUT)
    rows = (resp.json() or {}).get('items') or []
    out = []
    for item in rows:
        vi = item.get('volumeInfo') or {}
        sid = str(item.get('id') or '')
        if not sid or not vi.get('title'):
            continue
        out.append({
            'source': 'googlebooks', 'sid': sid,
            'title': vi.get('title', ''),
            'author': ' / '.join(vi.get('authors') or []),
            'year': (vi.get('publishedDate') or '')[:4],
            'cover': ((vi.get('imageLinks') or {}).get('thumbnail') or ''),
            'rating': str(vi.get('averageRating') or ''),
            'votes': int(vi.get('ratingsCount') or 0),
        })
    return out


def _gbooks_fetch(sid):
    resp = _get('https://www.googleapis.com/books/v1/volumes/%s' % sid, timeout=TIMEOUT)
    vi = (resp.json() or {}).get('volumeInfo') or {}
    ids = {}
    for x in (vi.get('industryIdentifiers') or []):
        if isinstance(x, dict):
            ids[x.get('type')] = x.get('identifier')
    return {
        'source': 'googlebooks', 'sid': str(sid),
        'title': vi.get('title', ''),
        'author': ' / '.join(vi.get('authors') or []),
        'year': (vi.get('publishedDate') or '')[:4],
        'cover': ((vi.get('imageLinks') or {}).get('thumbnail') or ''),
        'rating': str(vi.get('averageRating') or ''),
        'votes': int(vi.get('ratingsCount') or 0),
        'publisher': vi.get('publisher', ''),
        'isbn': ids.get('ISBN_13') or ids.get('ISBN_10') or '',
        'pages': str(vi.get('pageCount') or ''),
        'summary': _join(vi.get('description') or '', SUMMARY_MAX),
        'toc': [], 'reviews': [],
        'tags': [str(t) for t in (vi.get('categories') or [])][:10],
        'url': vi.get('infoLink') or '',
    }


# =============================================================
# Open Library（境外可用）
# =============================================================
def _openlib_search(query, limit):
    resp = _get('https://openlibrary.org/search.json',
                {'q': query, 'limit': max(1, min(limit, 20)),
                 'fields': 'key,title,author_name,first_publish_year,cover_i'},
                timeout=TIMEOUT)
    rows = (resp.json() or {}).get('docs') or []
    out = []
    for d in rows:
        key = str(d.get('key') or '').replace('/works/', '')
        if not key or not d.get('title'):
            continue
        out.append({
            'source': 'openlibrary', 'sid': key,
            'title': d.get('title', ''),
            'author': ' / '.join((d.get('author_name') or [])[:2]),
            'year': str(d.get('first_publish_year') or ''),
            'cover': ('https://covers.openlibrary.org/b/id/%s-M.jpg' % d['cover_i']
                      if d.get('cover_i') else ''),
            'rating': '', 'votes': 0,
        })
    return out


def _openlib_fetch(sid):
    resp = _get('https://openlibrary.org/works/%s.json' % sid, timeout=TIMEOUT)
    d = resp.json() or {}
    desc = d.get('description')
    if isinstance(desc, dict):
        desc = desc.get('value') or ''
    return {
        'source': 'openlibrary', 'sid': str(sid),
        'title': d.get('title', ''), 'author': '', 'year': '',
        'cover': '', 'rating': '', 'votes': 0,
        'publisher': '', 'isbn': '', 'pages': '',
        'summary': _join(desc or '', SUMMARY_MAX),
        'toc': [], 'reviews': [],
        'tags': [str(s) for s in (d.get('subjects') or [])][:10],
        'url': 'https://openlibrary.org/works/%s' % sid,
    }


SOURCES = {
    'douban':      {'label': '豆瓣读书',     'search': _douban_search,  'fetch': _douban_fetch},
    'googlebooks': {'label': 'Google Books', 'search': _gbooks_search,  'fetch': _gbooks_fetch},
    'openlibrary': {'label': 'Open Library', 'search': _openlib_search, 'fetch': _openlib_fetch},
}


def label_of(name: str) -> str:
    return (SOURCES.get(name) or {}).get('label') or name


def _call(name, kind, *args):
    """统一兜住网络异常：连不上就把这个源关进小黑屋，别拖累后面的请求。"""
    src = SOURCES.get(name)
    if not src:
        return None
    try:
        return src[kind](*args)
    except (requests.Timeout, requests.ConnectionError) as exc:
        # 只有「连不上」才拉黑。404 / 500 说明源好好的，只是没这本书，
        # 要是连坐，查一次不存在的书就会把整个源关掉十分钟。
        _mark_dead(name, '%s %s' % (kind, type(exc).__name__))
        return None
    except requests.RequestException:
        return None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _norm(s) -> str:
    return re.sub(r'\s+', '', str(s or '')).lower()


def _dedupe(items):
    seen, out = set(), []
    for it in items:
        key = (_norm(it.get('title')), _norm(it.get('author'))[:14])
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _prefer_author(items, author):
    """用户写了作者，就把对得上的排前面，但结果不丢——同名书本来就多，让他自己挑。"""
    want = _norm(author)
    if not want:
        return items
    hit, rest = [], []
    for it in items:
        (hit if want in _norm(it.get('author')) else rest).append(it)
    return hit + rest


def search(query, limit=8, author=''):
    """并行问所有还活着的源。返回 (候选列表, 真正出结果的源名)。"""
    query = (query or '').strip()[:80]
    if not query:
        return [], []
    names = alive_sources()
    if not names:
        names = _enabled()[:1]      # 全在小黑屋里就硬试一个，不然永远恢复不了
    if not names:
        return [], []

    # 用守护线程而不是线程池：连不上的源会卡在 DNS 解析上（requests 的 timeout
    # 管不到 DNS），线程池的线程是非守护的，进程退出时会一直等它们，实测能拖到 90 秒。
    box = queue.Queue()
    for name in names:
        threading.Thread(target=_worker, args=(box, name, query, limit), daemon=True).start()

    got = {}
    start = time.time()
    first_at = None
    while len(got) < len(names):
        now = time.time()
        budget = (start + SEARCH_CAP - now) if first_at is None else (first_at + GRACE - now)
        if budget <= 0:
            break
        try:
            name, rows = box.get(timeout=budget)
        except queue.Empty:
            break
        got[name] = rows or []
        if first_at is None:
            first_at = time.time()      # 有源答复了，再给慢源一点宽限就往下走

    items, used = [], []
    for name, rows in got.items():
        if rows:
            used.append(name)
        items.extend(rows)

    items = _prefer_author(_dedupe(items), author)
    return items[:limit], used


def _worker(box, name, query, limit):
    try:
        box.put((name, _call(name, 'search', query, limit) or []))
    except Exception:
        box.put((name, []))


def fetch(source, sid, ttl=CACHE_TTL):
    """抓一本书的完整材料。进程内缓存一小时，省掉重复抓取。"""
    key = (str(source), str(sid))
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]

    data = _call(key[0], 'fetch', key[1])
    if not data or not (data.get('title') or data.get('summary')):
        return None
    with _cache_lock:
        _cache[key] = (now, data)
        if len(_cache) > 200:
            for old in list(_cache)[:100]:
                _cache.pop(old, None)
    return data


def health() -> dict:
    return {'sources': _enabled(), 'alive': alive_sources(),
            'dead': {k: round(v - time.time()) for k, v in _dead.items() if v > time.time()}}


if __name__ == '__main__':
    import json
    q = os.sys.argv[1] if len(os.sys.argv) > 1 else '活着'
    hits, used = search(q, author=os.sys.argv[2] if len(os.sys.argv) > 2 else '')
    print('搜索 %r -> %d 条，出结果的源：%s' % (q, len(hits), used or '无'))
    for h in hits[:5]:
        print('   %-12s %-8s %-24s %s' % (h['source'], h.get('year', ''), h['title'], h['author']))
    if hits:
        first = hits[0]
        book = fetch(first['source'], first['sid'])
        print()
        print('抓取 %s/%s ->' % (first['source'], first['sid']))
        print(json.dumps(book, ensure_ascii=False, indent=2)[:2000] if book else '  抓不到')
    print()
    print('源状态：', health())