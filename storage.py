# -*- coding: utf-8 -*-
"""
SQLite 存储层 —— 全站唯一的落盘入口
====================================
一个 .db 文件 + Python 自带的 sqlite3，不需要额外进程、不需要装数据库。

给其他工具用的两个入口（照抄这两行就能存历史记录）：
    from storage import add_record, list_records
    add_record(tool='ai-chart', user=user, title=主题, payload={'prompt': ..., 'option': ...})
    rows = list_records(tool='ai-chart', user=user, limit=10)

设计上的几条硬规矩
------------------
1. 库文件默认在 data/app.db，可用环境变量 TB_DB_PATH 改（Docker 里指向挂载卷）；
2. 每次操作开短连接，WAL 模式 + busy_timeout，读不挡写、写不互相打架；
3. 所有记录都带 user 字段：登录之后一个库服务多个人，不隔离就会串号；
4. 表结构只在首次使用时建一次（IF NOT EXISTS），不做迁移框架，够用就行。

Vercel 这类只读文件系统上写不进去，会抛 StorageUnavailable，
调用方捕获后返回人话提示即可，别让页面 500。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent
_DEFAULT_DB = _BASE_DIR / 'data' / 'app.db'

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS notes(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        title      TEXT NOT NULL,
        content    TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user, id DESC)",
    """
    CREATE TABLE IF NOT EXISTS history(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        tool       TEXT NOT NULL,
        user       TEXT NOT NULL,
        title      TEXT NOT NULL DEFAULT '',
        payload    TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_history_tool ON history(tool, user, id DESC)",
    """
    CREATE TABLE IF NOT EXISTS book_cache(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        source     TEXT NOT NULL,
        sid        TEXT NOT NULL,
        title      TEXT NOT NULL DEFAULT '',
        author     TEXT NOT NULL DEFAULT '',
        payload    TEXT NOT NULL DEFAULT '{}',
        fetched_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_book_cache_key ON book_cache(source, sid)",
)


_ready = False


class StorageUnavailable(RuntimeError):
    """数据库连不上或写不进去（只读文件系统 / 没权限）。"""


def db_path() -> Path:
    raw = (os.environ.get('TB_DB_PATH') or '').strip()
    return Path(raw).expanduser() if raw else _DEFAULT_DB


def now_str() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')


def connect() -> sqlite3.Connection:
    """开一条短连接，顺手把库建好。"""
    global _ready
    path = db_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5.0)
    except (OSError, sqlite3.Error) as exc:
        raise StorageUnavailable('打不开数据库文件 %s：%s' % (path, exc)) from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL')     # 读写并行，不互相锁死
        conn.execute('PRAGMA busy_timeout=3000')    # 撞上写锁先等 3 秒
        conn.execute('PRAGMA foreign_keys=ON')
        if not _ready:
            for sql in SCHEMA:
                conn.execute(sql)
            conn.commit()
            _ready = True
    except sqlite3.Error as exc:
        conn.close()
        raise StorageUnavailable('初始化数据库失败：%s' % exc) from exc
    return conn


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ---------------------------------------------------------------
# 状态：页面靠它证明「数据真的写进文件了」
# ---------------------------------------------------------------
def info() -> dict:
    """页面靠它证明「数据真的写进文件了」：路径、大小、表、行数、日志模式。"""
    path = db_path()
    out = {
        'path': str(path), 'exists': False, 'bytes': 0, 'wal_bytes': 0, 'total_bytes': 0, 'modified': '',
        'sqlite': sqlite3.sqlite_version, 'journal_mode': '', 'tables': [],
        'writable': True, 'error': '',
    }
    # 先连一次：库文件不存在时它会顺手建出来，这样下面的 stat 才是准的
    try:
        conn = connect()
    except StorageUnavailable as exc:
        out['writable'] = False
        out['error'] = str(exc)
        out['exists'] = path.exists()
        return out
    try:
        out['journal_mode'] = conn.execute('PRAGMA journal_mode').fetchone()[0]
        names = [r['name'] for r in _rows(
            conn, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in names:
            count = conn.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
            out['tables'].append({'name': name, 'rows': count})
    finally:
        conn.close()
    if path.exists():
        stat = path.stat()
        out['exists'] = True
        out['bytes'] = stat.st_size
        out['modified'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))
    # WAL 模式下刚写入的内容先落在这个 -wal 文件里，算总量时得带上它
    wal = Path(str(path) + '-wal')
    if wal.exists():
        out['wal_bytes'] = wal.stat().st_size
    out['total_bytes'] = out['bytes'] + out['wal_bytes']
    return out
    try:
        out['journal_mode'] = conn.execute('PRAGMA journal_mode').fetchone()[0]
        names = [r['name'] for r in _rows(
            conn, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in names:
            count = conn.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
            out['tables'].append({'name': name, 'rows': count})
    finally:
        conn.close()
    return out

# ---------------------------------------------------------------
# 示例表 notes：给「写进去 → 拿出来」留的抄写模板，没有页面在用它
# ---------------------------------------------------------------
def add_note(user: str, title: str, content: str = '') -> dict:
    title = (title or '').strip()[:120]
    content = (content or '').strip()[:4000]
    if not title:
        raise ValueError('标题不能为空')
    created = now_str()
    try:
        conn = connect()
    except StorageUnavailable:
        raise
    try:
        cur = conn.execute(
            'INSERT INTO notes(user, title, content, created_at) VALUES (?,?,?,?)',
            (user, title, content, created))
        conn.commit()
        rowid = cur.lastrowid
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return {'id': rowid, 'user': user, 'title': title, 'content': content,
            'created_at': created}


def list_notes(user: str, q: str = '', limit: int = 100) -> list:
    limit = max(1, min(int(limit or 100), 500))
    sql = 'SELECT id, title, content, created_at FROM notes WHERE user = ?'
    args = [user]
    if (q or '').strip():
        sql += ' AND (title LIKE ? OR content LIKE ?)'
        like = '%' + q.strip() + '%'
        args += [like, like]
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(limit)
    conn = connect()
    try:
        return _rows(conn, sql, args)
    finally:
        conn.close()


def delete_note(user: str, note_id: int) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM notes WHERE id = ? AND user = ?', (int(note_id), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def clear_notes(user: str) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM notes WHERE user = ?', (user,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------
# 通用表 history：其他工具存历史记录，直接调这两个函数
#   add_record(tool, user, title, payload)   写一条
#   list_records(tool, user, limit)          读最近几条（payload 已还原成 dict）
# ---------------------------------------------------------------
def add_record(tool: str, user: str, title: str = '', payload=None) -> dict:
    tool = (tool or '').strip()[:60] or 'unknown'
    title = (title or '').strip()[:200]
    blob = json.dumps(payload if payload is not None else {}, ensure_ascii=False)
    created = now_str()
    conn = connect()
    try:
        cur = conn.execute(
            'INSERT INTO history(tool, user, title, payload, created_at) VALUES (?,?,?,?,?)',
            (tool, user, title, blob, created))
        conn.commit()
        rowid = cur.lastrowid
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return {'id': rowid, 'tool': tool, 'user': user, 'title': title,
            'payload': payload if payload is not None else {}, 'created_at': created}


def list_records(tool: str, user: str = '', limit: int = 50) -> list:
    limit = max(1, min(int(limit or 50), 500))
    sql = 'SELECT id, tool, user, title, payload, created_at FROM history WHERE tool = ?'
    args = [tool]
    if user:
        sql += ' AND user = ?'
        args.append(user)
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(limit)
    conn = connect()
    try:
        rows = _rows(conn, sql, args)
    finally:
        conn.close()
    for row in rows:
        try:
            row['payload'] = json.loads(row['payload'] or '{}')
        except ValueError:
            row['payload'] = {}
    return rows


def delete_record(user: str, rid: int) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM history WHERE id = ? AND user = ?', (int(rid), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def clear_records(tool: str, user: str) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM history WHERE tool = ? AND user = ?', (tool, user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------
# history 的补充：按 id 取一条 / 改一条
#   update_record 只改传进来的字段，没传的保持原样
# ---------------------------------------------------------------
def get_record(user: str, rid: int):
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT id, tool, user, title, payload, created_at '
                           'FROM history WHERE id = ? AND user = ?', (int(rid), user))
    finally:
        conn.close()
    if not rows:
        return None
    row = rows[0]
    try:
        row['payload'] = json.loads(row['payload'] or '{}')
    except ValueError:
        row['payload'] = {}
    return row


def update_record(user: str, rid: int, payload=None, title=None) -> int:
    """返回受影响行数；0 表示没这条记录，或者这条不属于该用户。"""
    sets, args = [], []
    if payload is not None:
        sets.append('payload = ?')
        args.append(json.dumps(payload, ensure_ascii=False))
    if title is not None:
        sets.append('title = ?')
        args.append(str(title).strip()[:200])
    if not sets:
        return 0
    args.extend([int(rid), user])
    conn = connect()
    try:
        cur = conn.execute('UPDATE history SET ' + ', '.join(sets) +
                           ' WHERE id = ? AND user = ?', args)
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('更新失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


# ---------------------------------------------------------------
# book_cache：书讯缓存，按 (source, sid) 唯一
#   抓一次豆瓣要一两秒，还会被限流。同一本书第二次直接读库，
#   所以这张表既是缓存，也算「我看过哪些书」的底账。
# ---------------------------------------------------------------
def book_get(source: str, sid: str):
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT source, sid, title, author, payload, fetched_at '
                           'FROM book_cache WHERE source = ? AND sid = ?', (source, sid))
    finally:
        conn.close()
    if not rows:
        return None
    row = rows[0]
    try:
        row['payload'] = json.loads(row['payload'] or '{}')
    except ValueError:
        row['payload'] = {}
    return row


def book_put(source: str, sid: str, title: str = '', author: str = '', payload=None) -> None:
    blob = json.dumps(payload if payload is not None else {}, ensure_ascii=False)
    conn = connect()
    try:
        # 用 INSERT OR REPLACE 而不是 ON CONFLICT：对 sqlite 版本要求最低
        conn.execute(
            'INSERT OR REPLACE INTO book_cache(source, sid, title, author, payload, fetched_at) '
            'VALUES (?,?,?,?,?,?)',
            (source, sid, (title or '')[:200], (author or '')[:120], blob, now_str()))
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def book_list(limit: int = 50) -> list:
    """最近查过的书。has_plan = 这本书存着 AI 拆出来的卡片（点进去能回看）。"""
    limit = max(1, min(int(limit or 50), 500))
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT source, sid, title, author, payload, fetched_at '
                           'FROM book_cache ORDER BY id DESC LIMIT ?', (limit,))
    finally:
        conn.close()
    for row in rows:
        raw = row.pop('payload', '') or '{}'
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        row['has_plan'] = bool(isinstance(payload, dict) and payload.get('analysis'))
    return rows

def checkpoint() -> None:
    """把 WAL 里的内容并回主文件，这样单独下载 .db 就是完整的。"""
    conn = connect()
    try:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        conn.close()
