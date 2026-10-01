# -*- coding: utf-8 -*-
# 数据浏览（页面在 /db）
# ------------------------------------------------------------
# 只读的 SQLite 查看器：翻表、跑 SQL、出图、导 CSV。
# 目的：SQLite 已经在用了，得有个地方能直观看到「到底存了什么」。
#
# 安全上做了三层（这是唯一一个能把整库摊开给人看的页面，必须防住）：
#   1. 连接用 mode=ro 打开，写操作由 SQLite 自己拒掉；
#   2. SQL 只放行 select / with / explain，且整句不许出现分号（禁多条语句）；
#   3. 表名、列名必须先在 sqlite_master / PRAGMA 里查到，才允许拼进 SQL。
# 接口全部要求登录（外层 LoginGate 把关）。
#
# 页面上的每个按钮对应一个接口：
#   GET  /db/api/overview                  库信息 + 表清单 + 每张表行数
#   GET  /db/api/table?name=&limit=&offset=  翻某张表的数据（含结构 / 索引）
#   POST /db/api/query                     跑一条只读 SQL
#   GET  /db/api/export?name=              把某张表导成 CSV
#   POST /db/api/export                    把查询结果导成 CSV
from __future__ import annotations

import csv
import io
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

import storage

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE = _BASE_DIR / 'templates' / 'db-viewer.html'

MAX_ROWS = 500          # 单次最多返回 / 导出多少行
DEFAULT_LIMIT = 50      # 翻表默认一页多少行
MAX_SQL_LEN = 4000      # SQL 最长多少字
OK_HEADS = ('select', 'with', 'explain')   # 只放行这三类语句

_FALLBACK = (
    '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
    '<body style="background:#0a0f1e;color:#e8edf7;font-family:system-ui">'
    '<h2>数据浏览</h2><p>页面模板缺失：templates/db-viewer.html</p></body></html>')


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({'ok': False, 'error': message}, status_code=status)


def _ro_uri() -> str:
    """拼一条只读 URI。Windows 下 D:\\a\\b.db 要写成 file:/D:/a/b.db。"""
    raw = str(storage.db_path().resolve()).replace('\\', '/')
    if not raw.startswith('/'):
        raw = '/' + raw
    return 'file:' + quote(raw) + '?mode=ro'


def connect_ro() -> sqlite3.Connection:
    """只读连接：这个连接上任何写操作都会被 SQLite 拒绝。"""
    try:
        conn = sqlite3.connect(_ro_uri(), uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise storage.StorageUnavailable('打不开数据库（只读）：%s' % exc) from exc
    conn.row_factory = sqlite3.Row
    return conn


def _db_error(exc: Exception) -> str:
    """库文件还不存在时给一句人话，而不是把 sqlite 的英文错抛给页面。"""
    text = str(exc)
    if 'unable to open database file' in text or 'does not exist' in text:
        return '数据库文件还不存在：先用任意一个工具存一条记录，它就会自动建出来。'
    return text

def _quote(name: str) -> str:
    """把标识符包成 "xxx"，内部的引号翻倍 —— 防注入的最后一道。"""
    return '"' + str(name).replace('"', '""') + '"'


def _tables(conn) -> list:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")
    return [r['name'] for r in rows]


def _columns(conn, table: str) -> list:
    """PRAGMA 不支持参数占位，所以表名必须先过 _tables 白名单再拼。"""
    out = []
    for r in conn.execute('PRAGMA table_info(%s)' % _quote(table)):
        out.append({
            'name': r['name'],
            'type': (r['type'] or '').upper() or '—',
            'pk': bool(r['pk']),
            'notnull': bool(r['notnull']),
            'default': r['dflt_value'],
        })
    return out


def _check_table(conn, name: str):
    """表名白名单校验，返回 (真实表名, 列清单)；不通过就抛 ValueError。"""
    name = (name or '').strip()
    if not name:
        raise ValueError('缺少表名')
    real = [t for t in _tables(conn) if t == name]
    if not real:
        raise ValueError('没有这张表：%s' % name)
    cols = _columns(conn, real[0])
    if not cols:
        raise ValueError('读不到 %s 的结构' % name)
    return real[0], cols


def _pick_column(cols: list, name: str, fallback: str = ''):
    """只在真实列里挑，挑不到就用 fallback —— 列名同样不允许凭空拼。"""
    wanted = (name or '').strip()
    if wanted:
        for c in cols:
            if c['name'] == wanted:
                return c['name']
    if fallback:
        for c in cols:
            if c['name'] == fallback:
                return c['name']
    return ''


def _search_cols(cols: list) -> list:
    """关键词搜索只扫文本类字段，数值列 LIKE 没意义还慢。"""
    out = []
    for c in cols:
        t = c['type']
        if not t or t == '—' or 'CHAR' in t or 'TEXT' in t or 'CLOB' in t or 'DATE' in t or 'TIME' in t:
            out.append(c['name'])
    return out[:8]


def _rows_to_lists(cursor, limit: int):
    """按位置取行：任意 SQL 出现重名列也不会取错，并报告有没有被截断。"""
    out, truncated = [], False
    for row in cursor.fetchmany(limit + 1):
        if len(out) >= limit:
            truncated = True
            break
        out.append(list(row))
    return out, truncated


def _cell(v):
    """二进制字段没法塞进 JSON，转成一句说明。"""
    if isinstance(v, (bytes, bytearray)):
        return '<BLOB %d 字节>' % len(v)
    return v


def _clean_rows(rows: list) -> list:
    return [[_cell(v) for v in row] for row in rows]


@router.get('/db', response_class=HTMLResponse)
def db_page() -> HTMLResponse:
    html = _TEMPLATE.read_text(encoding='utf-8') if _TEMPLATE.exists() else _FALLBACK
    return HTMLResponse(html)


@router.get('/db/api/overview')
def db_overview() -> JSONResponse:
    """库信息 + 表清单。库文件不存在不算错误，页面要能正常打开并给提示。"""
    path = storage.db_path()
    size = path.stat().st_size if path.exists() else 0
    mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(path.stat().st_mtime)) if path.exists() else ''
    db = {'path': str(path), 'exists': path.exists(), 'bytes': size, 'modified': mtime,
          'sqlite': sqlite3.sqlite_version, 'writable': True}
    tables = []
    try:
        conn = connect_ro()
        try:
            for name in _tables(conn):
                cols = _columns(conn, name)
                n = conn.execute('SELECT COUNT(*) FROM %s' % _quote(name)).fetchone()[0]
                tables.append({'name': name, 'rows': n, 'cols': len(cols)})
        finally:
            conn.close()
    except (storage.StorageUnavailable, sqlite3.Error) as exc:
        db['error'] = _db_error(exc)
        return JSONResponse({'ok': True, 'db': db, 'tables': [], 'total_rows': 0})
    return JSONResponse({'ok': True, 'db': db, 'tables': tables,
                         'total_rows': sum(t['rows'] for t in tables)})


@router.get('/db/api/table')
def db_table(name: str = '', limit: int = DEFAULT_LIMIT, offset: int = 0,
             order: str = '', dir: str = 'desc', q: str = '') -> JSONResponse:
    """翻一张表：返回结构 + 一页数据 + 总行数。"""
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_ROWS))
    offset = max(0, int(offset or 0))
    direction = 'ASC' if str(dir).lower() == 'asc' else 'DESC'
    try:
        conn = connect_ro()
    except storage.StorageUnavailable as exc:
        return _err(_db_error(exc), 500)
    started = time.perf_counter()
    try:
        try:
            table, cols = _check_table(conn, name)
        except ValueError as exc:
            return _err(str(exc))
        names = [c['name'] for c in cols]
        where, args = '', []
        keyword = (q or '').strip()
        if keyword:
            scols = _search_cols(cols)
            if scols:
                where = ' WHERE (' + ' OR '.join('%s LIKE ?' % _quote(c) for c in scols) + ')'
                args = ['%' + keyword + '%'] * len(scols)
            else:
                where = ''
        total = conn.execute('SELECT COUNT(*) FROM %s%s' % (_quote(table), where), args).fetchone()[0]
        order_col = _pick_column(cols, order, 'id')
        sql = 'SELECT * FROM %s%s' % (_quote(table), where)
        if order_col:
            sql += ' ORDER BY %s %s' % (_quote(order_col), direction)
        sql += ' LIMIT ? OFFSET ?'
        cursor = conn.execute(sql, args + [limit, offset])
        raw = cursor.fetchall()
        rows = [[_cell(r[c]) for c in names] for r in raw]
        indexes = [r['name'] for r in conn.execute('PRAGMA index_list(%s)' % _quote(table))]
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
            (table,)).fetchone()
    except sqlite3.Error as exc:
        return _err('查询失败：%s' % exc, 500)
    finally:
        conn.close()
    return JSONResponse({
        'ok': True, 'table': table, 'columns': cols, 'rows': rows,
        'total': total, 'limit': limit, 'offset': offset,
        'order': order_col, 'dir': direction.lower(), 'search': keyword,
        'indexes': indexes, 'ddl': (ddl['sql'] if ddl else '') or '',
        'ms': round((time.perf_counter() - started) * 1000, 1),
    })

def _clean_sql(sql: str) -> str:
    """只读校验：空白 / 超长 / 多条语句 / 非查询语句，全部拦在这里。"""
    s = (sql or '').strip()
    if not s:
        raise ValueError('SQL 不能为空')
    if len(s) > MAX_SQL_LEN:
        raise ValueError('SQL 太长（最多 %d 字）' % MAX_SQL_LEN)
    body = s.rstrip().rstrip(';').strip()
    if not body:
        raise ValueError('SQL 不能为空')
    if ';' in body:
        raise ValueError('一次只能跑一条语句（检测到分号）')
    head = body.split(None, 1)[0].lower()
    if head not in OK_HEADS:
        raise ValueError('这个页面是只读的，只允许 %s 开头的语句，收到的是「%s」'
                         % (' / '.join(OK_HEADS), head[:20]))
    return body


@router.post('/db/api/query')
async def db_query(request: Request) -> JSONResponse:
    """跑一条只读 SQL，把结果集原样返回。"""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        sql = _clean_sql(str(payload.get('sql') or ''))
    except ValueError as exc:
        return _err(str(exc))
    try:
        limit = int(payload.get('limit') or MAX_ROWS)
    except (TypeError, ValueError):
        limit = MAX_ROWS
    limit = max(1, min(limit, MAX_ROWS))

    try:
        conn = connect_ro()
    except storage.StorageUnavailable as exc:
        return _err(_db_error(exc), 500)
    started = time.perf_counter()
    try:
        cursor = conn.execute(sql)
        columns = [d[0] for d in (cursor.description or [])]
        if not columns:
            return _err('这条语句没有返回结果集。这个页面只读，写操作不会被执行。')
        rows, truncated = _rows_to_lists(cursor, limit)
    except sqlite3.Error as exc:
        return _err('SQL 报错：%s' % exc)
    finally:
        conn.close()
    return JSONResponse({
        'ok': True, 'columns': columns, 'rows': _clean_rows(rows),
        'shown': len(rows), 'truncated': truncated, 'limit': limit,
        'ms': round((time.perf_counter() - started) * 1000, 1),
    })


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _alone = FastAPI(title='数据浏览', version='1.0.0')
    _alone.include_router(router)
    uvicorn.run(_alone, host='127.0.0.1', port=8014)