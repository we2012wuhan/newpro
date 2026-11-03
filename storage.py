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
import re
import sqlite3
import time
import uuid
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
    CREATE TABLE IF NOT EXISTS image_jobs(
        id              TEXT PRIMARY KEY,
        user            TEXT NOT NULL,
        prompt          TEXT NOT NULL,
        negative_prompt TEXT NOT NULL DEFAULT '',
        style           TEXT NOT NULL DEFAULT '',
        ratio           TEXT NOT NULL DEFAULT '1:1',
        resolution      TEXT NOT NULL DEFAULT '2K',
        count           INTEGER NOT NULL DEFAULT 1,
        done            INTEGER NOT NULL DEFAULT 0,
        status          TEXT NOT NULL DEFAULT 'queued',
        model           TEXT NOT NULL DEFAULT '',
        error           TEXT NOT NULL DEFAULT '',
        files           TEXT NOT NULL DEFAULT '[]',
        created_at      TEXT NOT NULL,
        started_at      TEXT NOT NULL DEFAULT '',
        finished_at     TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_image_jobs_user ON image_jobs(user, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_image_jobs_status ON image_jobs(status, created_at)",
    """
    CREATE TABLE IF NOT EXISTS image_usage(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id     TEXT NOT NULL UNIQUE,
        user       TEXT NOT NULL,
        count      INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_image_usage_user ON image_usage(user, created_at)",
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
    """
    CREATE TABLE IF NOT EXISTS tool_cats(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        ckey       TEXT NOT NULL,
        name       TEXT NOT NULL DEFAULT '',
        custom     INTEGER NOT NULL DEFAULT 0,
        hidden     INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_cats_key ON tool_cats(user, ckey)",
    """
    CREATE TABLE IF NOT EXISTS tool_cat_assign(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        href       TEXT NOT NULL,
        ckey       TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_cat_assign ON tool_cat_assign(user, href)",
    """
    CREATE TABLE IF NOT EXISTS usage_events(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        tool       TEXT NOT NULL,
        action     TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_usage_events_user ON usage_events(user, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_time ON usage_events(created_at)",
    """
    CREATE TABLE IF NOT EXISTS awareness_state(
        user       TEXT PRIMARY KEY,
        seen_daily TEXT NOT NULL DEFAULT '',
        seen_weekly TEXT NOT NULL DEFAULT '',
        mute_until TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS awareness_weekly(
        user       TEXT NOT NULL,
        week       TEXT NOT NULL,
        payload    TEXT NOT NULL DEFAULT '{}',
        source     TEXT NOT NULL DEFAULT 'rules',
        created_at TEXT NOT NULL,
        PRIMARY KEY(user, week)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS media_state(
        user       TEXT NOT NULL,
        path       TEXT NOT NULL,
        title      TEXT NOT NULL DEFAULT '',
        kind       TEXT NOT NULL DEFAULT '',
        favorite   INTEGER NOT NULL DEFAULT 0,
        position   REAL NOT NULL DEFAULT 0,
        duration   REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(user, path)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_media_state_user ON media_state(user, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_media_state_fav ON media_state(user, favorite, updated_at DESC)",
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


def list_records_all(user: str = '', limit: int = 300) -> list:
    """跨工具读 history —— 「记录总览」页要一屏看到所有工具存过什么。

    和 list_records 的区别只有两点：不按 tool 过滤，返回值里带 tool 字段。
    """
    limit = max(1, min(int(limit or 300), 1000))
    sql = 'SELECT id, tool, user, title, payload, created_at FROM history WHERE 1 = 1'
    args = []
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
# image_jobs：文生图任务（/image-gen）
#   任务状态和图片文件名放这里，图片本体放 data/generated/ 下。
#   「谁先抢到哪条 queued」用 BEGIN IMMEDIATE 做原子领取，
#   这样即使将来开了多个进程，也不会同一张图被扣两次费。
# ---------------------------------------------------------------
_IMAGE_JOB_FIELDS = {'status', 'error', 'files', 'done', 'started_at', 'finished_at', 'model'}


def _image_job_row(row) -> dict:
    data = dict(row)
    try:
        files = json.loads(data.get('files') or '[]')
    except ValueError:
        files = []
    data['files'] = files if isinstance(files, list) else []
    return data


def image_job_add(user: str, prompt: str, negative_prompt: str, style: str,
                  ratio: str, resolution: str, count: int, model: str) -> dict:
    job_id = uuid.uuid4().hex
    created = now_str()
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO image_jobs(id, user, prompt, negative_prompt, style, ratio, '
            'resolution, count, done, status, model, error, files, created_at, '
            'started_at, finished_at) '
            'VALUES (?,?,?,?,?,?,?,?,0,\'queued\',?,\'\',\'[]\',?,\'\',\'\')',
            (job_id, user, prompt, negative_prompt, style, ratio, resolution,
             int(count), model, created))
        # 额度单独记一笔：任务可以删，已经花掉的请求数不能跟着删，
        # 否则删掉历史就能绕过每日上限，账单会失控。
        conn.execute(
            'INSERT INTO image_usage(job_id, user, count, created_at) VALUES(?,?,?,?)',
            (job_id, user, int(count), created))
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return {
        'id': job_id, 'user': user, 'prompt': prompt,
        'negative_prompt': negative_prompt, 'style': style, 'ratio': ratio,
        'resolution': resolution, 'count': int(count), 'done': 0,
        'status': 'queued', 'model': model, 'error': '', 'files': [],
        'created_at': created, 'started_at': '', 'finished_at': '',
    }


def image_job_get(user: str, job_id: str):
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM image_jobs WHERE id = ? AND user = ?',
                     (str(job_id), user))
    finally:
        conn.close()
    return _image_job_row(rows[0]) if rows else None


def image_job_list(user: str, limit: int = 20) -> list:
    limit = max(1, min(int(limit or 20), 100))
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM image_jobs WHERE user = ? '
                           'ORDER BY rowid DESC LIMIT ?', (user, limit))
    finally:
        conn.close()
    return [_image_job_row(row) for row in rows]


def image_job_claim_next():
    """原子领取最早的一条 queued 任务；没有就返回 None。

    UPDATE 和 SELECT 放在同一个写事务里，两个进程同时抢只会有一个成功。
    """
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            "SELECT * FROM image_jobs WHERE status = 'queued' "
            'ORDER BY rowid ASC LIMIT 1').fetchone()
        if not row:
            conn.rollback()
            return None
        started = now_str()
        conn.execute(
            "UPDATE image_jobs SET status = 'running', started_at = ? WHERE id = ?",
            (started, row['id']))
        conn.commit()
        data = _image_job_row(row)
        data['status'] = 'running'
        data['started_at'] = started
        return data
    except sqlite3.OperationalError as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise StorageUnavailable('领取任务失败：%s' % exc) from exc
    finally:
        conn.close()


def image_job_update(job_id: str, fields: dict) -> int:
    """后台线程更新任务状态。只允许改白名单里的列，避免拼出奇怪 SQL。"""
    sets, args = [], []
    for key, value in (fields or {}).items():
        if key not in _IMAGE_JOB_FIELDS:
            continue
        if key == 'files':
            value = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)
        sets.append(key + ' = ?')
        args.append(value)
    if not sets:
        return 0
    args.append(str(job_id))
    conn = connect()
    try:
        cur = conn.execute('UPDATE image_jobs SET ' + ', '.join(sets) + ' WHERE id = ?', args)
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('更新失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def image_job_delete(user: str, job_id: str) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM image_jobs WHERE id = ? AND user = ?',
                           (str(job_id), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def image_job_delete_finished(user: str, job_id: str) -> int:
    """用户主动删任务时只删已结束的。

    queued / running 不能删：后台线程可能已经在向服务商请求，
    删掉记录不会取消这次计费，还会留下没人认领的图片。
    """
    conn = connect()
    try:
        cur = conn.execute(
            "DELETE FROM image_jobs WHERE id = ? AND user = ? "
            "AND status NOT IN ('queued', 'running')",
            (str(job_id), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def image_job_images_since(user: str, since: str) -> int:
    """某用户从 since 起一共请求了多少张图（按 count 累加，失败任务也算）。

    读 image_usage 而不是 image_jobs：任务删除、过期清理都不会让额度回血。
    """
    conn = connect()
    try:
        row = conn.execute(
            'SELECT COALESCE(SUM(count), 0) AS n FROM image_usage '
            'WHERE user = ? AND created_at >= ?', (user, since)).fetchone()
        return int(row['n'] or 0)
    finally:
        conn.close()


def image_job_recover_running(message: str) -> int:
    """进程重启后，把上一次没跑完的 running 任务标成失败。

    这些任务可能已经向服务商付过费，所以不自动重试，只留下一句提示让用户核对。
    """
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE image_jobs SET status = 'failed', error = ?, finished_at = ? "
            "WHERE status = 'running'", (message, now_str()))
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('恢复任务失败：%s' % exc) from exc
    finally:
        conn.close()


def image_job_expired(user: str, before: str) -> list:
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM image_jobs WHERE user = ? AND created_at < ? '
                           'ORDER BY rowid ASC', (user, before))
    finally:
        conn.close()
    return [_image_job_row(row) for row in rows]


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

# ---------------------------------------------------------------
# 首页「工具分类」：分类清单 + 卡片归属
#   HTML 里写死的那套只当「作者默认值」，这两张表只存用户自己的改动：
#     tool_cats        —— 改过的名字 / 自建的分类 / 删掉内置分类留下的墓碑
#     tool_cat_assign  —— 卡片被拖到别的分类之后的归属
#   所以「恢复默认」= 把这个 user 在这两张表里的行全删掉，前端自然回落到 HTML 默认值。
#
#   约定：custom=1 是用户自建分类（删的时候真删行）；
#         custom=0 是内置分类（删的时候只写一条 hidden=1 的墓碑，别把默认值弄丢）。
# ---------------------------------------------------------------
CAT_NAME_MAX = 10
CAT_KEY_MAX = 40
CAT_HREF_MAX = 300


def _cat_name(value) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:CAT_NAME_MAX]


def _cat_href(value) -> str:
    href = str(value or '').strip()[:CAT_HREF_MAX]
    if href.startswith('/') or href.startswith('http://') or href.startswith('https://'):
        return href
    return ''


def _write_cat_name(conn, user: str, ckey: str, name: str, custom: int, hidden: int) -> None:
    """有就改，没有就插 —— 不用 INSERT OR REPLACE，免得 id 一直涨。"""
    cur = conn.execute('UPDATE tool_cats SET name = ?, custom = ?, hidden = ? '
                       'WHERE user = ? AND ckey = ?', (name, custom, hidden, user, ckey))
    if cur.rowcount == 0:
        conn.execute('INSERT INTO tool_cats(user, ckey, name, custom, hidden, created_at) '
                     'VALUES (?,?,?,?,?,?)', (user, ckey, name, custom, hidden, now_str()))


def _apply_moves(conn, user: str, moves) -> None:
    """moves = {href: 分类key}；值是空字符串表示「取消自定义，回落 HTML 默认」。"""
    if not isinstance(moves, dict):
        return
    for raw_href, raw_cat in moves.items():
        href = _cat_href(raw_href)
        if not href:
            continue
        cat = str(raw_cat or '').strip()[:CAT_KEY_MAX]
        if not cat:
            conn.execute('DELETE FROM tool_cat_assign WHERE user = ? AND href = ?', (user, href))
            continue
        cur = conn.execute('UPDATE tool_cat_assign SET ckey = ?, updated_at = ? '
                           'WHERE user = ? AND href = ?', (cat, now_str(), user, href))
        if cur.rowcount == 0:
            conn.execute('INSERT INTO tool_cat_assign(user, href, ckey, updated_at) '
                         'VALUES (?,?,?,?)', (user, href, cat, now_str()))


def cats_state(user: str) -> dict:
    """前端拿它来摆分类条：cats 是用户改过的行，assign 是被拖过分类的卡片。"""
    conn = connect()
    try:
        cats = _rows(conn, 'SELECT ckey, name, hidden, custom FROM tool_cats '
                           'WHERE user = ? ORDER BY id', (user,))
        assigns = _rows(conn, 'SELECT href, ckey FROM tool_cat_assign '
                              'WHERE user = ? ORDER BY id', (user,))
    finally:
        conn.close()
    return {
        'cats': [{'key': r['ckey'], 'name': r['name'],
                  'hidden': bool(r['hidden']), 'custom': bool(r['custom'])} for r in cats],
        'assign': {r['href']: r['ckey'] for r in assigns},
    }


def cat_add(user: str, name: str) -> str:
    """新建一个自建分类，返回它的 key。"""
    name = _cat_name(name)
    if not name:
        raise ValueError('分类名字不能是空的。')
    ckey = 'c_' + uuid.uuid4().hex[:8]
    conn = connect()
    try:
        conn.execute('INSERT INTO tool_cats(user, ckey, name, custom, hidden, created_at) '
                     'VALUES (?,?,?,1,0,?)', (user, ckey, name, now_str()))
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return ckey


def cat_rename(user: str, ckey: str, name: str) -> None:
    name = _cat_name(name)
    if not name:
        raise ValueError('分类名字不能是空的。')
    ckey = str(ckey or '').strip()[:CAT_KEY_MAX]
    conn = connect()
    try:
        row = conn.execute('SELECT custom, hidden FROM tool_cats WHERE user = ? AND ckey = ?',
                           (user, ckey)).fetchone()
        custom = int(row['custom']) if row else 0
        hidden = int(row['hidden']) if row else 0
        _write_cat_name(conn, user, ckey, name, custom, hidden)
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def cat_delete(user: str, ckey: str, moves=None) -> None:
    """删分类。自建的删行，内置的写 hidden=1。moves 是「原来在里面的卡片去哪」。"""
    ckey = str(ckey or '').strip()[:CAT_KEY_MAX]
    if not ckey or ckey == 'all':
        raise ValueError('这个分类不能删。')
    conn = connect()
    try:
        if ckey.startswith('c_'):
            conn.execute('DELETE FROM tool_cats WHERE user = ? AND ckey = ?', (user, ckey))
        else:
            cur = conn.execute('UPDATE tool_cats SET hidden = 1 WHERE user = ? AND ckey = ?',
                               (user, ckey))
            if cur.rowcount == 0:
                conn.execute('INSERT INTO tool_cats(user, ckey, name, custom, hidden, created_at) '
                             'VALUES (?,?,\'\',0,1,?)', (user, ckey, now_str()))
        _apply_moves(conn, user, moves)
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def assign_apply(user: str, moves) -> None:
    conn = connect()
    try:
        _apply_moves(conn, user, moves)
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def cats_reset(user: str) -> None:
    """恢复默认：把这个用户在分类上的所有改动抹掉。"""
    conn = connect()
    try:
        conn.execute('DELETE FROM tool_cats WHERE user = ?', (user,))
        conn.execute('DELETE FROM tool_cat_assign WHERE user = ?', (user,))
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()

def checkpoint() -> None:
    """把 WAL 里的内容并回主文件，这样单独下载 .db 就是完整的。"""
    conn = connect()
    try:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        conn.close()


# ---------------------------------------------------------------
# 使用足迹 + 觉察状态（/awareness 用）
# ---------------------------------------------------------------
def log_usage(user: str, tool: str, action: str = 'open', dedupe_seconds: int = 300) -> bool:
    """记一次使用。同一用户+工具+动作在 dedupe_seconds 内只算一次。"""
    tool = (tool or '').strip()[:40] or 'unknown'
    action = (action or 'open').strip()[:20]
    conn = connect()
    try:
        if dedupe_seconds > 0:
            row = conn.execute(
                "SELECT id FROM usage_events WHERE user=? AND tool=? AND action=? "
                "AND created_at >= datetime('now','localtime','-%d seconds') "
                "ORDER BY id DESC LIMIT 1" % int(dedupe_seconds),
                (user, tool, action)).fetchone()
            if row:
                return False
        conn.execute('INSERT INTO usage_events(user, tool, action, created_at) VALUES (?,?,?,?)',
                     (user, tool, action, now_str()))
        # 顺手清掉 90 天前的足迹（表很小，每次删一下没关系）
        conn.execute("DELETE FROM usage_events WHERE created_at < datetime('now','localtime','-90 days')")
        conn.commit()
        return True
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def usage_since(user: str, since: str = '') -> list:
    """取某时间之后的使用事件（默认最近 7 天）。"""
    if not since:
        since = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 7 * 86400))
    conn = connect()
    try:
        return _rows(conn, 'SELECT tool, action, created_at FROM usage_events '
                           'WHERE user=? AND created_at >= ? ORDER BY created_at', (user, since))
    finally:
        conn.close()


def awareness_state(user: str) -> dict:
    """觉察的已读/免打扰状态；没有就建一条。"""
    conn = connect()
    try:
        conn.execute('INSERT OR IGNORE INTO awareness_state(user, updated_at) VALUES (?,?)',
                     (user, now_str()))
        conn.commit()
        row = conn.execute('SELECT * FROM awareness_state WHERE user=?', (user,)).fetchone()
        return dict(row) if row else {'user': user, 'seen_daily': '', 'seen_weekly': '', 'mute_until': ''}
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def awareness_mark(user: str, seen_daily=None, seen_weekly=None, mute_until=None) -> dict:
    """更新已读 / 免打扰状态；只改传进来的字段。"""
    conn = connect()
    try:
        conn.execute('INSERT OR IGNORE INTO awareness_state(user, updated_at) VALUES (?,?)',
                     (user, now_str()))
        sets, args = [], []
        for key, val in (('seen_daily', seen_daily), ('seen_weekly', seen_weekly), ('mute_until', mute_until)):
            if val is not None:
                sets.append('%s=?' % key)
                args.append(str(val)[:20])
        if sets:
            sets.append('updated_at=?')
            args.append(now_str())
            args.append(user)
            conn.execute('UPDATE awareness_state SET ' + ', '.join(sets) + ' WHERE user=?', args)
        conn.commit()
        row = conn.execute('SELECT * FROM awareness_state WHERE user=?', (user,)).fetchone()
        return dict(row) if row else {}
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def weekly_get(user: str, week: str) -> dict | None:
    """取某一周的觉察复盘缓存；没有返回 None。"""
    conn = connect()
    try:
        row = conn.execute('SELECT * FROM awareness_weekly WHERE user=? AND week=?',
                           (user, week)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    data = dict(row)
    try:
        import json as _json
        data['payload'] = _json.loads(data.get('payload') or '{}')
    except ValueError:
        data['payload'] = {}
    return data


# ---------------------------------------------------------------
# 影音库：收藏和断点续播
# 文件本身在阿里云盘，这里只存「哪个用户对哪条虚拟路径做了什么」。
# path 是 AList 里的绝对路径（如 /阿里云盘/音乐/xx.mp3），按账号隔离。
# ---------------------------------------------------------------
def _media_row(row) -> dict:
    data = dict(row) if row is not None else {}
    return {
        'path': data.get('path') or '',
        'title': data.get('title') or '',
        'kind': data.get('kind') or '',
        'favorite': bool(data.get('favorite')),
        'position': float(data.get('position') or 0),
        'duration': float(data.get('duration') or 0),
        'updated_at': data.get('updated_at') or '',
    }


def media_state_get(user: str, path: str) -> dict:
    conn = connect()
    try:
        row = conn.execute('SELECT * FROM media_state WHERE user=? AND path=?',
                           (user, path)).fetchone()
    finally:
        conn.close()
    return _media_row(row)


def media_state_rows(user: str, limit: int = 500) -> list:
    """按最近更新倒序，返回该用户的全部收藏 / 续播记录。"""
    limit = max(1, min(int(limit or 500), 1000))
    conn = connect()
    try:
        rows = conn.execute(
            'SELECT * FROM media_state WHERE user=? ORDER BY updated_at DESC, path ASC LIMIT ?',
            (user, limit)).fetchall()
    finally:
        conn.close()
    return [_media_row(row) for row in rows]


def media_state_set(user: str, path: str, title: str = '', kind: str = '',
                    favorite=None, position=None, duration=None) -> dict:
    """插入或更新一条影音状态；没传的字段保持原值。

    favorite=False 是显式取消收藏，和 None（不改）区分开；
    position=0 也是显式写 0（播完 / 从头开始），不能被 COALESCE 吞掉。
    """
    user = (user or '').strip()
    path = (path or '').strip()
    if not user or not path:
        raise ValueError('用户和路径不能为空')
    title = (title or '').strip()[:240]
    kind = (kind or '').strip()[:16]
    fav_new = 0 if favorite is None else (1 if favorite else 0)
    pos_new = 0.0 if position is None else max(0.0, float(position))
    dur_new = 0.0 if duration is None else max(0.0, float(duration))
    fav_arg = None if favorite is None else fav_new
    pos_arg = None if position is None else round(pos_new, 2)
    dur_arg = None if duration is None else round(dur_new, 2)
    now = now_str()
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO media_state(user, path, title, kind, favorite, position, duration, updated_at) '
            'VALUES (?,?,?,?,?,?,?,?) '
            'ON CONFLICT(user, path) DO UPDATE SET '
            " title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE media_state.title END, "
            " kind=CASE WHEN excluded.kind<>'' THEN excluded.kind ELSE media_state.kind END, "
            ' favorite=COALESCE(?, media_state.favorite), '
            ' position=COALESCE(?, media_state.position), '
            ' duration=COALESCE(?, media_state.duration), '
            ' updated_at=excluded.updated_at',
            (user, path[:2048], title, kind, fav_new, round(pos_new, 2), round(dur_new, 2),
             now, fav_arg, pos_arg, dur_arg))
        conn.commit()
        row = conn.execute('SELECT * FROM media_state WHERE user=? AND path=?',
                           (user, path[:2048])).fetchone()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return _media_row(row)


def weekly_set(user: str, week: str, payload: dict, source: str = 'rules') -> dict:
    """写某一周的复盘缓存（同周覆盖，正常只会写一次）。"""
    import json as _json
    blob = _json.dumps(payload or {}, ensure_ascii=False)
    now = now_str()
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO awareness_weekly(user, week, payload, source, created_at) VALUES (?,?,?,?,?) '
            'ON CONFLICT(user, week) DO UPDATE SET payload=excluded.payload, '
            'source=excluded.source, created_at=excluded.created_at',
            (user, week, blob, (source or 'rules')[:20], now))
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return {'user': user, 'week': week, 'payload': payload, 'source': source, 'created_at': now}
