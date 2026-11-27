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
from datetime import date, timedelta
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
    "CREATE INDEX IF NOT EXISTS idx_history_user_time ON history(user, created_at DESC)",
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
    CREATE TABLE IF NOT EXISTS podcast_jobs(
        id               TEXT PRIMARY KEY,
        user             TEXT NOT NULL,
        topic            TEXT NOT NULL,
        material         TEXT NOT NULL DEFAULT '',
        tone             TEXT NOT NULL DEFAULT 'casual',
        minutes          INTEGER NOT NULL DEFAULT 5,
        host_a           TEXT NOT NULL DEFAULT '',
        host_b           TEXT NOT NULL DEFAULT '',
        voice_a          TEXT NOT NULL DEFAULT '',
        voice_b          TEXT NOT NULL DEFAULT '',
        title            TEXT NOT NULL DEFAULT '',
        summary          TEXT NOT NULL DEFAULT '',
        status           TEXT NOT NULL DEFAULT 'queued',
        stage            TEXT NOT NULL DEFAULT 'queued',
        progress         INTEGER NOT NULL DEFAULT 0,
        total_turns      INTEGER NOT NULL DEFAULT 0,
        done_turns       INTEGER NOT NULL DEFAULT 0,
        script           TEXT NOT NULL DEFAULT '[]',
        audio            TEXT NOT NULL DEFAULT '',
        duration_seconds INTEGER NOT NULL DEFAULT 0,
        tts_words        INTEGER NOT NULL DEFAULT 0,
        model            TEXT NOT NULL DEFAULT '',
        error            TEXT NOT NULL DEFAULT '',
        created_at       TEXT NOT NULL,
        started_at       TEXT NOT NULL DEFAULT '',
        finished_at      TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_podcast_jobs_user ON podcast_jobs(user, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_podcast_jobs_status ON podcast_jobs(status, created_at)",
    """
    CREATE TABLE IF NOT EXISTS podcast_usage(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id     TEXT NOT NULL UNIQUE,
        user       TEXT NOT NULL,
        minutes    INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_podcast_usage_user ON podcast_usage(user, created_at)",
    """
    CREATE TABLE IF NOT EXISTS article_jobs(
        id               TEXT PRIMARY KEY,
        user             TEXT NOT NULL,
        topic            TEXT NOT NULL,
        material         TEXT NOT NULL DEFAULT '',
        genre            TEXT NOT NULL DEFAULT 'insight',
        tone             TEXT NOT NULL DEFAULT 'warm',
        length           TEXT NOT NULL DEFAULT 'medium',
        title            TEXT NOT NULL DEFAULT '',
        summary          TEXT NOT NULL DEFAULT '',
        outline          TEXT NOT NULL DEFAULT '{}',
        sections         TEXT NOT NULL DEFAULT '[]',
        images           TEXT NOT NULL DEFAULT '[]',
        status           TEXT NOT NULL DEFAULT 'queued',
        stage            TEXT NOT NULL DEFAULT 'queued',
        progress         INTEGER NOT NULL DEFAULT 0,
        total_sections   INTEGER NOT NULL DEFAULT 0,
        done_sections    INTEGER NOT NULL DEFAULT 0,
        total_images     INTEGER NOT NULL DEFAULT 0,
        done_images      INTEGER NOT NULL DEFAULT 0,
        image_count      INTEGER NOT NULL DEFAULT 0,
        image_style      TEXT NOT NULL DEFAULT 'illustration',
        image_model      TEXT NOT NULL DEFAULT '',
        model            TEXT NOT NULL DEFAULT '',
        body_chars       INTEGER NOT NULL DEFAULT 0,
        warning          TEXT NOT NULL DEFAULT '',
        error            TEXT NOT NULL DEFAULT '',
        created_at       TEXT NOT NULL,
        started_at       TEXT NOT NULL DEFAULT '',
        finished_at      TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_article_jobs_user ON article_jobs(user, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_article_jobs_status ON article_jobs(status, created_at)",
    """
    CREATE TABLE IF NOT EXISTS article_usage(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id     TEXT NOT NULL UNIQUE,
        user       TEXT NOT NULL,
        images     INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_article_usage_user ON article_usage(user, created_at)",
    """
    CREATE TABLE IF NOT EXISTS video_uploads(
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user          TEXT NOT NULL,
        original_name TEXT NOT NULL DEFAULT '',
        stored_name   TEXT NOT NULL,
        size          INTEGER NOT NULL DEFAULT 0,
        duration      REAL NOT NULL DEFAULT 0,
        width         INTEGER NOT NULL DEFAULT 0,
        height        INTEGER NOT NULL DEFAULT 0,
        fps           REAL NOT NULL DEFAULT 0,
        video_codec   TEXT NOT NULL DEFAULT '',
        audio_codec   TEXT NOT NULL DEFAULT '',
        has_audio     INTEGER NOT NULL DEFAULT 0,
        mime          TEXT NOT NULL DEFAULT 'application/octet-stream',
        probe         TEXT NOT NULL DEFAULT '{}',
        created_at    TEXT NOT NULL,
        last_used_at  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_video_uploads_user ON video_uploads(user, id DESC)",
    """
    CREATE TABLE IF NOT EXISTS video_jobs(
        id            TEXT PRIMARY KEY,
        user          TEXT NOT NULL,
        upload_id     INTEGER NOT NULL,
        kind          TEXT NOT NULL,
        params        TEXT NOT NULL DEFAULT '{}',
        status        TEXT NOT NULL DEFAULT 'queued',
        stage         TEXT NOT NULL DEFAULT 'queued',
        progress      INTEGER NOT NULL DEFAULT 0,
        note          TEXT NOT NULL DEFAULT '',
        outputs       TEXT NOT NULL DEFAULT '[]',
        primary_index INTEGER NOT NULL DEFAULT 0,
        output_size   INTEGER NOT NULL DEFAULT 0,
        error         TEXT NOT NULL DEFAULT '',
        created_at    TEXT NOT NULL,
        started_at    TEXT NOT NULL DEFAULT '',
        finished_at   TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_video_jobs_user ON video_jobs(user, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_video_jobs_status ON video_jobs(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_video_jobs_upload ON video_jobs(upload_id)",
    """
    CREATE TABLE IF NOT EXISTS video_usage(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id     TEXT NOT NULL UNIQUE,
        user       TEXT NOT NULL,
        kind       TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_video_usage_user ON video_usage(user, created_at)",
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
    """
    CREATE TABLE IF NOT EXISTS ai_memories(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        kind       TEXT NOT NULL DEFAULT 'fact',
        content    TEXT NOT NULL,
        norm       TEXT NOT NULL DEFAULT '',
        source     TEXT NOT NULL DEFAULT 'auto',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_memories_norm ON ai_memories(user, norm)",
    "CREATE INDEX IF NOT EXISTS idx_ai_memories_user ON ai_memories(user, id DESC)",
    # ---- 个人地图（/trip）----
    """
    CREATE TABLE IF NOT EXISTS map_places(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        name       TEXT NOT NULL,
        address    TEXT NOT NULL DEFAULT '',
        lng        REAL NOT NULL,
        lat        REAL NOT NULL,
        city       TEXT NOT NULL DEFAULT '',
        district   TEXT NOT NULL DEFAULT '',
        adcode     TEXT NOT NULL DEFAULT '',
        category   TEXT NOT NULL DEFAULT 'other',
        tags       TEXT NOT NULL DEFAULT '[]',
        note       TEXT NOT NULL DEFAULT '',
        source     TEXT NOT NULL DEFAULT 'manual',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_map_places_user ON map_places(user, category, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS map_routes(
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user            TEXT NOT NULL,
        name            TEXT NOT NULL,
        mode            TEXT NOT NULL DEFAULT 'driving',
        origin_name     TEXT NOT NULL DEFAULT '',
        origin_lng      REAL NOT NULL,
        origin_lat      REAL NOT NULL,
        dest_name       TEXT NOT NULL DEFAULT '',
        dest_lng        REAL NOT NULL,
        dest_lat        REAL NOT NULL,
        origin_place_id INTEGER,
        dest_place_id   INTEGER,
        distance_m      INTEGER NOT NULL DEFAULT 0,
        duration_min    INTEGER NOT NULL DEFAULT 0,
        polyline        TEXT NOT NULL DEFAULT '',
        steps           TEXT NOT NULL DEFAULT '[]',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_map_routes_user ON map_routes(user, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS map_route_runs(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        route_id   INTEGER NOT NULL,
        ran_on     TEXT NOT NULL,
        actual_min INTEGER NOT NULL DEFAULT 0,
        note       TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_map_runs_route ON map_route_runs(user, route_id, ran_on DESC)",
    """
    CREATE TABLE IF NOT EXISTS map_checkins(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        place_id   INTEGER,
        name       TEXT NOT NULL,
        lng        REAL NOT NULL,
        lat        REAL NOT NULL,
        checkin_on TEXT NOT NULL,
        note       TEXT NOT NULL DEFAULT '',
        tags       TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_map_checkins_user ON map_checkins(user, checkin_on DESC)",
    """
    CREATE TABLE IF NOT EXISTS map_cache(
        cache_key  TEXT PRIMARY KEY,
        payload    TEXT NOT NULL DEFAULT '{}',
        fetched_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS map_api_usage(
        day     TEXT NOT NULL,
        service TEXT NOT NULL,
        count   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(day, service)
    )
    """,
    # ---- 出题小工具（/quiz）的错题库与收藏：每题一份快照，不依赖原记录 ----
    """
    CREATE TABLE IF NOT EXISTS quiz_wrong(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        quiz_id    INTEGER NOT NULL,
        q_index    INTEGER NOT NULL,
        data       TEXT NOT NULL DEFAULT '{}',
        given      TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_quiz_wrong_key ON quiz_wrong(user, quiz_id, q_index)",
    "CREATE INDEX IF NOT EXISTS idx_quiz_wrong_user ON quiz_wrong(user, updated_at DESC, id DESC)",
    """
    CREATE TABLE IF NOT EXISTS quiz_fav(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        quiz_id    INTEGER NOT NULL,
        q_index    INTEGER NOT NULL,
        data       TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_quiz_fav_key ON quiz_fav(user, quiz_id, q_index)",
    "CREATE INDEX IF NOT EXISTS idx_quiz_fav_user ON quiz_fav(user, id DESC)",
    # ---- 每日笔记（/notes）：条目按天归档；图片本体落盘，这里只存元数据 ----
    """
    CREATE TABLE IF NOT EXISTS daily_notes(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        day        TEXT NOT NULL,
        content    TEXT NOT NULL DEFAULT '',
        tags       TEXT NOT NULL DEFAULT '[]',
        tags_text  TEXT NOT NULL DEFAULT '',
        importance INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_daily_notes_user_day ON daily_notes(user, day DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_daily_notes_user_time ON daily_notes(user, created_at DESC, id DESC)",
    """
    CREATE TABLE IF NOT EXISTS daily_note_images(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id     INTEGER NOT NULL,
        user        TEXT NOT NULL,
        stored_name TEXT NOT NULL,
        mime        TEXT NOT NULL,
        bytes       INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_daily_note_images_note ON daily_note_images(note_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_daily_note_images_user ON daily_note_images(user, id)",
    # ---- 每日笔记的 AI 分析：按周期缓存，笔记改动后靠 input_hash 判断是否过期 ----
    """
    CREATE TABLE IF NOT EXISTS note_analyses(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user        TEXT NOT NULL,
        period      TEXT NOT NULL,
        period_key  TEXT NOT NULL,
        start_day   TEXT NOT NULL,
        end_day     TEXT NOT NULL,
        input_hash  TEXT NOT NULL,
        note_count  INTEGER NOT NULL DEFAULT 0,
        truncated   INTEGER NOT NULL DEFAULT 0,
        model       TEXT NOT NULL DEFAULT '',
        result      TEXT NOT NULL DEFAULT '{}',
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_note_analyses_key ON note_analyses(user, period, period_key)",
    "CREATE INDEX IF NOT EXISTS idx_note_analyses_user_time ON note_analyses(user, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS note_analysis_usage(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL,
        day        TEXT NOT NULL,
        period     TEXT NOT NULL,
        period_key TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_note_analysis_usage_user_day ON note_analysis_usage(user, day)",
)


_ready = False


class StorageUnavailable(RuntimeError):
    """数据库连不上或写不进去（只读文件系统 / 没权限）。"""


def db_path() -> Path:
    raw = (os.environ.get('TB_DB_PATH') or '').strip()
    return Path(raw).expanduser() if raw else _DEFAULT_DB


def now_str() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """老库补列：CREATE TABLE IF NOT EXISTS 不会给已有表加新字段。"""
    columns = {row[1] for row in conn.execute('PRAGMA table_info(daily_notes)')}
    if 'importance' not in columns:
        conn.execute('ALTER TABLE daily_notes ADD COLUMN importance INTEGER NOT NULL DEFAULT 0')


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
            _ensure_columns(conn)
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
# 全站 AI 助手的长期记忆：按账号存，按 norm 去重，最多保留 200 条
# ---------------------------------------------------------------
MEMORY_MAX = 200


def add_memory(user: str, content: str, kind: str = 'fact',
               source: str = 'auto', norm: str = '') -> dict:
    """写一条长期记忆。同一账号下 norm 相同视为同一条，只刷新时间。"""
    content = ' '.join(str(content or '').split())[:240]
    if not content:
        raise ValueError('记忆内容不能为空')
    kind = (str(kind or 'fact').strip().lower() or 'fact')[:20]
    source = (str(source or 'auto').strip().lower() or 'auto')[:20]
    norm = (str(norm or '').strip().lower() or content.lower())[:240]
    now = now_str()
    try:
        conn = connect()
    except StorageUnavailable:
        raise
    try:
        row = conn.execute(
            'SELECT id, content, source, created_at FROM ai_memories'
            ' WHERE user = ? AND norm = ?', (user, norm)).fetchone()
        if row:
            conn.execute('UPDATE ai_memories SET kind = ?, updated_at = ?'
                         ' WHERE id = ?', (kind, now, row['id']))
            conn.commit()
            mid = row['id']
            content = row['content']
            source = row['source']
            created = row['created_at']
            duplicate = True
        else:
            cur = conn.execute(
                'INSERT INTO ai_memories(user, kind, content, norm, source,'
                ' created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
                (user, kind, content, norm, source, now, now))
            conn.commit()
            mid = cur.lastrowid
            created = now
            duplicate = False
        # 只留最新 200 条，防止长期使用后无限增长
        conn.execute(
            'DELETE FROM ai_memories WHERE user = ? AND id NOT IN ('
            ' SELECT id FROM ai_memories WHERE user = ?'
            ' ORDER BY id DESC LIMIT ?)', (user, user, MEMORY_MAX))
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return {'id': mid, 'kind': kind, 'content': content, 'source': source,
            'created_at': created, 'updated_at': now, 'duplicate': duplicate}


def list_memories(user: str, limit: int = MEMORY_MAX) -> list:
    limit = max(1, min(int(limit or MEMORY_MAX), 500))
    conn = connect()
    try:
        return _rows(
            conn,
            'SELECT id, kind, content, source, created_at, updated_at'
            ' FROM ai_memories WHERE user = ? ORDER BY id DESC LIMIT ?',
            (user, limit))
    finally:
        conn.close()


def delete_memory(user: str, memory_id: int) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM ai_memories WHERE id = ? AND user = ?',
                           (int(memory_id), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def clear_memories(user: str) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM ai_memories WHERE user = ?', (user,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def memory_count(user: str) -> int:
    conn = connect()
    try:
        return conn.execute('SELECT COUNT(*) FROM ai_memories WHERE user = ?',
                            (user,)).fetchone()[0]
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


def count_records(tool: str, user: str = '') -> int:
    """某工具（某账号）一共存过多少条，给分页用。"""
    sql = 'SELECT COUNT(*) FROM history WHERE tool = ?'
    args = [tool]
    if user:
        sql += ' AND user = ?'
        args.append(user)
    conn = connect()
    try:
        row = conn.execute(sql, args).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def list_records_page(tool: str, user: str = '', limit: int = 10,
                      offset: int = 0) -> list:
    """list_records 的分页版：按 id 倒序取一段。"""
    limit = max(1, min(int(limit or 10), 100))
    offset = max(0, int(offset or 0))
    sql = 'SELECT id, tool, user, title, payload, created_at FROM history WHERE tool = ?'
    args = [tool]
    if user:
        sql += ' AND user = ?'
        args.append(user)
    sql += ' ORDER BY id DESC LIMIT ? OFFSET ?'
    args.extend([limit, offset])
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


def list_records_since(user: str, since: str, limit: int = 1000) -> list:
    """按时间窗口读某个用户的跨工具历史；给复盘摘要用。

    created_at 和 usage_since 一样是本地时间字符串，直接按字典序比较。
    """
    limit = max(1, min(int(limit or 1000), 2000))
    sql = ('SELECT id, tool, user, title, payload, created_at FROM history '
           'WHERE user = ? AND created_at >= ? ORDER BY id DESC LIMIT ?')
    conn = connect()
    try:
        rows = _rows(conn, sql, [user, str(since or ''), limit])
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
# podcast_jobs：双人播客任务（/podcast）
#   脚本、进度、音频文件名都存这里；MP3 本体放 data/generated/podcast/ 下。
#   任务先落 queued，再由独立后台线程「写脚本 -> 逐段合成 -> 拼接」。
#   和 image_jobs 一样用 BEGIN IMMEDIATE 原子领取，避免重复计费。
# ---------------------------------------------------------------
_PODCAST_JOB_FIELDS = {
    'status', 'stage', 'progress', 'total_turns', 'done_turns', 'script',
    'audio', 'duration_seconds', 'tts_words', 'title', 'summary', 'model',
    'error', 'started_at', 'finished_at',
}


def _podcast_job_row(row) -> dict:
    data = dict(row)
    try:
        script = json.loads(data.get('script') or '[]')
    except ValueError:
        script = []
    data['script'] = script if isinstance(script, list) else []
    return data


def podcast_job_add(user: str, topic: str, material: str, tone: str, minutes: int,
                    host_a: str, host_b: str, voice_a: str, voice_b: str,
                    model: str) -> dict:
    job_id = uuid.uuid4().hex
    created = now_str()
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO podcast_jobs(id, user, topic, material, tone, minutes, '
            'host_a, host_b, voice_a, voice_b, title, summary, status, stage, '
            'progress, total_turns, done_turns, script, audio, duration_seconds, '
            'tts_words, model, error, created_at, started_at, finished_at) '
            "VALUES (?,?,?,?,?,?,?,?,?,?,'','','queued','queued',0,0,0,'[]','',0,0,?,'',?,'','')",
            (job_id, user, topic, material, tone, int(minutes), host_a, host_b,
             voice_a, voice_b, model, created))
        # 额度单独记一笔：删历史不会让当天的限额回血。
        conn.execute(
            'INSERT INTO podcast_usage(job_id, user, minutes, created_at) VALUES(?,?,?,?)',
            (job_id, user, int(minutes), created))
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return {
        'id': job_id, 'user': user, 'topic': topic, 'material': material,
        'tone': tone, 'minutes': int(minutes), 'host_a': host_a, 'host_b': host_b,
        'voice_a': voice_a, 'voice_b': voice_b, 'title': '', 'summary': '',
        'status': 'queued', 'stage': 'queued', 'progress': 0,
        'total_turns': 0, 'done_turns': 0, 'script': [], 'audio': '',
        'duration_seconds': 0, 'tts_words': 0, 'model': model, 'error': '',
        'created_at': created, 'started_at': '', 'finished_at': '',
    }


def podcast_job_get(user: str, job_id: str):
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM podcast_jobs WHERE id = ? AND user = ?',
                     (str(job_id), user))
    finally:
        conn.close()
    return _podcast_job_row(rows[0]) if rows else None


def podcast_job_list(user: str, limit: int = 20) -> list:
    limit = max(1, min(int(limit or 20), 100))
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM podcast_jobs WHERE user = ? '
                           'ORDER BY rowid DESC LIMIT ?', (user, limit))
    finally:
        conn.close()
    return [_podcast_job_row(row) for row in rows]


def podcast_job_claim_next():
    """原子领取最早的一条 queued 任务；没有就返回 None。"""
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            "SELECT * FROM podcast_jobs WHERE status = 'queued' "
            'ORDER BY rowid ASC LIMIT 1').fetchone()
        if not row:
            conn.rollback()
            return None
        started = now_str()
        conn.execute(
            "UPDATE podcast_jobs SET status = 'running', stage = 'script', started_at = ? "
            'WHERE id = ?', (started, row['id']))
        conn.commit()
        data = _podcast_job_row(row)
        data['status'] = 'running'
        data['stage'] = 'script'
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


def podcast_job_update(job_id: str, fields: dict) -> int:
    sets, args = [], []
    for key, value in (fields or {}).items():
        if key not in _PODCAST_JOB_FIELDS:
            continue
        if key == 'script':
            value = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)
        sets.append(key + ' = ?')
        args.append(value)
    if not sets:
        return 0
    args.append(str(job_id))
    conn = connect()
    try:
        cur = conn.execute('UPDATE podcast_jobs SET ' + ', '.join(sets) + ' WHERE id = ?', args)
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('更新失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def podcast_job_delete(user: str, job_id: str) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM podcast_jobs WHERE id = ? AND user = ?',
                           (str(job_id), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def podcast_job_delete_finished(user: str, job_id: str) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            "DELETE FROM podcast_jobs WHERE id = ? AND user = ? "
            "AND status NOT IN ('queued', 'running')",
            (str(job_id), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def podcast_jobs_since(user: str, since: str) -> int:
    """某用户从 since 起一共创建了多少期播客（按分钟累加，失败任务也算）。"""
    conn = connect()
    try:
        row = conn.execute(
            'SELECT COALESCE(SUM(minutes), 0) AS n FROM podcast_usage '
            'WHERE user = ? AND created_at >= ?', (user, since)).fetchone()
        return int(row['n'] or 0)
    finally:
        conn.close()


def podcast_job_recover_running(message: str) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE podcast_jobs SET status = 'failed', stage = 'failed', error = ?, "
            'finished_at = ? WHERE status = '
            "'running'", (message, now_str()))
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('恢复任务失败：%s' % exc) from exc
    finally:
        conn.close()


def podcast_job_expired(user: str, before: str) -> list:
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM podcast_jobs WHERE user = ? AND created_at < ? '
                           'ORDER BY rowid ASC', (user, before))
    finally:
        conn.close()
    return [_podcast_job_row(row) for row in rows]


# ---------------------------------------------------------------
# article_jobs：AI 文章写作任务（/article）
#   大纲由用户确认后才建任务；正文按节生成，配图逐张落盘。
#   和 podcast_jobs 一样用 BEGIN IMMEDIATE 原子领取，避免重复计费。
# ---------------------------------------------------------------
_ARTICLE_JOB_FIELDS = {
    'status', 'stage', 'progress', 'total_sections', 'done_sections',
    'total_images', 'done_images', 'title', 'summary', 'outline', 'sections',
    'images', 'body_chars', 'warning', 'error', 'image_model', 'model',
    'started_at', 'finished_at',
}
_ARTICLE_JSON_FIELDS = {'outline', 'sections', 'images'}


def _article_job_row(row) -> dict:
    data = dict(row)
    for key, fallback in (('outline', {}), ('sections', []), ('images', [])):
        try:
            value = json.loads(data.get(key) or ('{}' if key == 'outline' else '[]'))
        except ValueError:
            value = fallback
        if key == 'outline':
            data[key] = value if isinstance(value, dict) else {}
        else:
            data[key] = value if isinstance(value, list) else []
    return data


def article_job_add(user: str, topic: str, material: str, genre: str, tone: str,
                    length: str, image_count: int, image_style: str,
                    image_model: str, model: str, outline: dict,
                    total_images: int) -> dict:
    job_id = uuid.uuid4().hex
    created = now_str()
    outline = outline if isinstance(outline, dict) else {}
    sections = outline.get('sections') if isinstance(outline.get('sections'), list) else []
    total_sections = len(sections)
    title = str(outline.get('title') or '').strip()[:120]
    summary = str(outline.get('summary') or '').strip()[:300]
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO article_jobs(id, user, topic, material, genre, tone, length, '
            'title, summary, outline, sections, images, status, stage, progress, '
            'total_sections, done_sections, total_images, done_images, image_count, '
            'image_style, image_model, model, body_chars, warning, error, created_at, '
            'started_at, finished_at) '
            "VALUES (?,?,?,?,?,?,?,?,?,?,'[]','[]','queued','queued',0,?,0,?,0,?,?,?,?,0,'','',?,'','')",
            (job_id, user, topic, material, genre, tone, length, title, summary,
             json.dumps(outline, ensure_ascii=False), total_sections,
             int(total_images), int(image_count), image_style, image_model, model,
             created))
        # 额度单独记一笔：删除任务、过期清理都不会让当天额度回血。
        conn.execute(
            'INSERT INTO article_usage(job_id, user, images, created_at) VALUES(?,?,?,?)',
            (job_id, user, int(total_images), created))
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return {
        'id': job_id, 'user': user, 'topic': topic, 'material': material,
        'genre': genre, 'tone': tone, 'length': length, 'title': title,
        'summary': summary, 'outline': outline, 'sections': [], 'images': [],
        'status': 'queued', 'stage': 'queued', 'progress': 0,
        'total_sections': total_sections, 'done_sections': 0,
        'total_images': int(total_images), 'done_images': 0,
        'image_count': int(image_count), 'image_style': image_style,
        'image_model': image_model, 'model': model, 'body_chars': 0,
        'warning': '', 'error': '', 'created_at': created,
        'started_at': '', 'finished_at': '',
    }


def article_job_get(user: str, job_id: str):
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM article_jobs WHERE id = ? AND user = ?',
                     (str(job_id), user))
    finally:
        conn.close()
    return _article_job_row(rows[0]) if rows else None


def article_job_list(user: str, limit: int = 20) -> list:
    limit = max(1, min(int(limit or 20), 100))
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM article_jobs WHERE user = ? '
                           'ORDER BY rowid DESC LIMIT ?', (user, limit))
    finally:
        conn.close()
    return [_article_job_row(row) for row in rows]


def article_job_claim_next():
    """原子领取最早的一条 queued 任务；没有就返回 None。"""
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            "SELECT * FROM article_jobs WHERE status = 'queued' "
            'ORDER BY rowid ASC LIMIT 1').fetchone()
        if not row:
            conn.rollback()
            return None
        started = now_str()
        conn.execute(
            "UPDATE article_jobs SET status = 'running', stage = 'writing', "
            'started_at = ? WHERE id = ?', (started, row['id']))
        conn.commit()
        data = _article_job_row(row)
        data['status'] = 'running'
        data['stage'] = 'writing'
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


def article_job_update(job_id: str, fields: dict) -> int:
    sets, args = [], []
    for key, value in (fields or {}).items():
        if key not in _ARTICLE_JOB_FIELDS:
            continue
        if key in _ARTICLE_JSON_FIELDS:
            fallback = {} if key == 'outline' else []
            if key == 'outline':
                value = json.dumps(value if isinstance(value, dict) else {}, ensure_ascii=False)
            else:
                value = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)
        sets.append(key + ' = ?')
        args.append(value)
    if not sets:
        return 0
    args.append(str(job_id))
    conn = connect()
    try:
        cur = conn.execute('UPDATE article_jobs SET ' + ', '.join(sets) + ' WHERE id = ?', args)
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('更新失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def article_job_delete(user: str, job_id: str) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM article_jobs WHERE id = ? AND user = ?',
                           (str(job_id), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def article_job_delete_finished(user: str, job_id: str) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            "DELETE FROM article_jobs WHERE id = ? AND user = ? "
            "AND status NOT IN ('queued', 'running')",
            (str(job_id), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def article_job_images_since(user: str, since: str) -> int:
    """某用户从 since 起预定了多少张图；读 usage，删任务不会回血。"""
    conn = connect()
    try:
        row = conn.execute(
            'SELECT COALESCE(SUM(images), 0) AS n FROM article_usage '
            'WHERE user = ? AND created_at >= ?', (user, since)).fetchone()
        return int(row['n'] or 0)
    finally:
        conn.close()


def article_job_recover_running(message: str) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE article_jobs SET status = 'failed', stage = 'failed', error = ?, "
            'finished_at = ? WHERE status = '
            "'running'", (message, now_str()))
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('恢复任务失败：%s' % exc) from exc
    finally:
        conn.close()


def article_job_expired(user: str, before: str) -> list:
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM article_jobs WHERE user = ? AND created_at < ? '
                           'ORDER BY rowid ASC', (user, before))
    finally:
        conn.close()
    return [_article_job_row(row) for row in rows]


# ---------------------------------------------------------------
# video_uploads / video_jobs：视频小工具（/video-tools）
#   素材上传一次，多个任务复用同一份源文件；删除素材会级联删它的任务。
#   任务和用量同一事务写入，删除任务/素材都不会让当天额度回血。
# ---------------------------------------------------------------
_VIDEO_JOB_FIELDS = {
    'status', 'stage', 'progress', 'note', 'outputs', 'primary_index',
    'output_size', 'error', 'started_at', 'finished_at',
}


def _video_upload_row(row) -> dict:
    data = dict(row)
    try:
        probe = json.loads(data.get('probe') or '{}')
    except ValueError:
        probe = {}
    data['probe'] = probe if isinstance(probe, dict) else {}
    data['has_audio'] = 1 if data.get('has_audio') else 0
    return data


def _video_job_row(row) -> dict:
    data = dict(row)
    try:
        params = json.loads(data.get('params') or '{}')
    except ValueError:
        params = {}
    try:
        outputs = json.loads(data.get('outputs') or '[]')
    except ValueError:
        outputs = []
    data['params'] = params if isinstance(params, dict) else {}
    data['outputs'] = outputs if isinstance(outputs, list) else []
    return data


def video_upload_add(user: str, original_name: str, stored_name: str, size: int,
                     probe: dict) -> dict:
    probe = probe if isinstance(probe, dict) else {}
    created = now_str()
    try:
        duration = round(float(probe.get('duration') or 0), 3)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        width = max(0, int(probe.get('width') or 0))
    except (TypeError, ValueError):
        width = 0
    try:
        height = max(0, int(probe.get('height') or 0))
    except (TypeError, ValueError):
        height = 0
    try:
        fps = round(float(probe.get('fps') or 0), 3)
    except (TypeError, ValueError):
        fps = 0.0
    video_codec = str(probe.get('video_codec') or '')[:40]
    audio_codec = str(probe.get('audio_codec') or '')[:40]
    has_audio = 1 if probe.get('has_audio') else 0
    mime = str(probe.get('mime') or 'application/octet-stream')[:80]
    conn = connect()
    try:
        cur = conn.execute(
            'INSERT INTO video_uploads(user, original_name, stored_name, size, duration, '
            'width, height, fps, video_codec, audio_codec, has_audio, mime, probe, '
            'created_at, last_used_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (user, str(original_name or '')[:160], stored_name, int(size), duration,
             width, height, fps, video_codec, audio_codec, has_audio, mime,
             json.dumps(probe, ensure_ascii=False), created, created))
        conn.commit()
        upload_id = int(cur.lastrowid)
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return {
        'id': upload_id, 'user': user, 'original_name': str(original_name or '')[:160],
        'stored_name': stored_name, 'size': int(size), 'duration': duration,
        'width': width, 'height': height, 'fps': fps, 'video_codec': video_codec,
        'audio_codec': audio_codec, 'has_audio': has_audio, 'mime': mime,
        'probe': probe, 'created_at': created, 'last_used_at': created,
    }


def video_upload_get(user: str, upload_id):
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return None
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM video_uploads WHERE id = ? AND user = ?',
                     (upload_id, user))
    finally:
        conn.close()
    return _video_upload_row(rows[0]) if rows else None


def video_upload_list(user: str, limit: int = 10) -> list:
    limit = max(1, min(int(limit or 10), 50))
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM video_uploads WHERE user = ? '
                           'ORDER BY id DESC LIMIT ?', (user, limit))
    finally:
        conn.close()
    return [_video_upload_row(row) for row in rows]


def video_upload_count(user: str) -> int:
    conn = connect()
    try:
        row = conn.execute('SELECT COUNT(*) AS n FROM video_uploads WHERE user = ?',
                           (user,)).fetchone()
        return int(row['n'] or 0)
    finally:
        conn.close()


def video_upload_delete(user: str, upload_id) -> dict:
    """删除素材；还有排队/运行中的任务时返回 busy，否则级联删任务行。"""
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return {'result': 'missing', 'upload': None, 'jobs': []}
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM video_uploads WHERE id = ? AND user = ?',
                           (upload_id, user)).fetchone()
        if not row:
            conn.rollback()
            return {'result': 'missing', 'upload': None, 'jobs': []}
        jobs = _rows(conn, 'SELECT * FROM video_jobs WHERE upload_id = ? AND user = ?',
                     (upload_id, user))
        if any(job.get('status') in ('queued', 'running') for job in jobs):
            conn.rollback()
            return {'result': 'busy', 'upload': _video_upload_row(row), 'jobs': []}
        conn.execute('DELETE FROM video_jobs WHERE upload_id = ? AND user = ?',
                     (upload_id, user))
        conn.execute('DELETE FROM video_uploads WHERE id = ? AND user = ?',
                     (upload_id, user))
        conn.commit()
        return {'result': 'ok', 'upload': _video_upload_row(row),
                'jobs': [_video_job_row(job) for job in jobs]}
    except sqlite3.OperationalError as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise StorageUnavailable('删除素材失败：%s' % exc) from exc
    finally:
        conn.close()


def video_job_add(user: str, upload_id, kind: str, params: dict):
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return None
    job_id = uuid.uuid4().hex
    created = now_str()
    params = params if isinstance(params, dict) else {}
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT id FROM video_uploads WHERE id = ? AND user = ?',
                           (upload_id, user)).fetchone()
        if not row:
            conn.rollback()
            return None
        conn.execute(
            'INSERT INTO video_jobs(id, user, upload_id, kind, params, status, stage, '
            "progress, note, outputs, primary_index, output_size, error, created_at, "
            "started_at, finished_at) VALUES(?,?,?,?,?,'queued','queued',0,'','[]',0,0,'',?,'','')",
            (job_id, user, upload_id, kind, json.dumps(params, ensure_ascii=False), created))
        conn.execute(
            'INSERT INTO video_usage(job_id, user, kind, created_at) VALUES(?,?,?,?)',
            (job_id, user, kind, created))
        conn.execute('UPDATE video_uploads SET last_used_at = ? WHERE id = ? AND user = ?',
                     (created, upload_id, user))
        conn.commit()
    except sqlite3.OperationalError as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise StorageUnavailable('创建视频任务失败：%s' % exc) from exc
    finally:
        conn.close()
    return {
        'id': job_id, 'user': user, 'upload_id': upload_id, 'kind': kind,
        'params': params, 'status': 'queued', 'stage': 'queued', 'progress': 0,
        'note': '', 'outputs': [], 'primary_index': 0, 'output_size': 0,
        'error': '', 'created_at': created, 'started_at': '', 'finished_at': '',
    }


def video_job_get(user: str, job_id: str):
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM video_jobs WHERE id = ? AND user = ?',
                     (str(job_id), user))
    finally:
        conn.close()
    return _video_job_row(rows[0]) if rows else None


def video_job_list(user: str, limit: int = 20) -> list:
    limit = max(1, min(int(limit or 20), 100))
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM video_jobs WHERE user = ? '
                           'ORDER BY rowid DESC LIMIT ?', (user, limit))
    finally:
        conn.close()
    return [_video_job_row(row) for row in rows]


def video_jobs_for_upload(user: str, upload_id) -> list:
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return []
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM video_jobs WHERE upload_id = ? AND user = ? '
                           'ORDER BY rowid DESC', (upload_id, user))
    finally:
        conn.close()
    return [_video_job_row(row) for row in rows]


def video_job_claim_next():
    """原子领取最早的一条 queued 任务；素材已被删除的孤儿任务不会被领取。"""
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            'SELECT j.* FROM video_jobs j JOIN video_uploads u '
            'ON u.id = j.upload_id AND u.user = j.user '
            "WHERE j.status = 'queued' ORDER BY j.rowid ASC LIMIT 1").fetchone()
        if not row:
            conn.rollback()
            return None
        started = now_str()
        conn.execute(
            "UPDATE video_jobs SET status = 'running', stage = 'running', "
            'started_at = ? WHERE id = ?', (started, row['id']))
        conn.commit()
        data = _video_job_row(row)
        data['status'] = 'running'
        data['stage'] = 'running'
        data['started_at'] = started
        return data
    except sqlite3.OperationalError as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise StorageUnavailable('领取视频任务失败：%s' % exc) from exc
    finally:
        conn.close()


def video_job_update(job_id: str, fields: dict) -> int:
    sets, args = [], []
    for key, value in (fields or {}).items():
        if key not in _VIDEO_JOB_FIELDS:
            continue
        if key == 'outputs':
            value = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)
        sets.append(key + ' = ?')
        args.append(value)
    if not sets:
        return 0
    args.append(str(job_id))
    conn = connect()
    try:
        cur = conn.execute('UPDATE video_jobs SET ' + ', '.join(sets) + ' WHERE id = ?', args)
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('更新视频任务失败：%s' % exc) from exc
    finally:
        conn.close()


def video_job_delete_finished(user: str, job_id: str) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            'DELETE FROM video_jobs WHERE id = ? AND user = ? '
            "AND status NOT IN ('queued', 'running')",
            (str(job_id), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def video_job_active_count(user: str) -> int:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM video_jobs WHERE user = ? "
            "AND status IN ('queued', 'running')", (user,)).fetchone()
        return int(row['n'] or 0)
    finally:
        conn.close()


def video_job_jobs_since(user: str, since: str) -> int:
    """某用户从 since 起提交了多少个视频任务；读 usage，删任务不会回血。"""
    conn = connect()
    try:
        row = conn.execute(
            'SELECT COUNT(*) AS n FROM video_usage WHERE user = ? AND created_at >= ?',
            (user, since)).fetchone()
        return int(row['n'] or 0)
    finally:
        conn.close()


def video_job_recover_running(message: str) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE video_jobs SET status = 'failed', stage = 'failed', error = ?, "
            "finished_at = ? WHERE status = 'running'", (message, now_str()))
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('恢复视频任务失败：%s' % exc) from exc
    finally:
        conn.close()


def video_job_expired(user: str, before: str) -> list:
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM video_jobs WHERE user = ? AND created_at < ? '
                           "AND status NOT IN ('queued', 'running') "
                           'ORDER BY rowid ASC', (user, before))
    finally:
        conn.close()
    return [_video_job_row(row) for row in rows]


def video_upload_expired(user: str, before: str) -> list:
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM video_uploads WHERE user = ? AND last_used_at < ? '
                           'ORDER BY id ASC', (user, before))
    finally:
        conn.close()
    return [_video_upload_row(row) for row in rows]


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


# ---------------------------------------------------------------
# 个人地图（/trip）：常用地点、缓存和高德配额计数
#   - 所有查询都带 user，页面之间不会串数据；
#   - 地点坐标统一存 GCJ-02（高德坐标系），由上层负责转换；
#   - map_cache 让地理编码 / POI 结果跨进程复用，少扣高德配额。
# ---------------------------------------------------------------
def _map_place_row(row) -> dict:
    data = dict(row)
    try:
        tags = json.loads(data.get('tags') or '[]')
    except ValueError:
        tags = []
    data['tags'] = tags if isinstance(tags, list) else []
    return data


def map_place_add(user: str, name: str, lng: float, lat: float,
                  address: str = '', city: str = '', district: str = '',
                  adcode: str = '', category: str = 'other', tags=None,
                  note: str = '', source: str = 'manual') -> dict:
    name = (name or '').strip()[:80]
    if not name:
        raise ValueError('地点名称不能为空')
    now = now_str()
    blob = json.dumps(tags if isinstance(tags, list) else [], ensure_ascii=False)
    conn = connect()
    try:
        cur = conn.execute(
            'INSERT INTO map_places(user, name, address, lng, lat, city, district, adcode, '
            'category, tags, note, source, created_at, updated_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (user, name, (address or '')[:120], float(lng), float(lat),
             (city or '')[:40], (district or '')[:40], (adcode or '')[:20],
             (category or 'other')[:20], blob, (note or '')[:500],
             (source or 'manual')[:20], now, now))
        conn.commit()
        row = conn.execute('SELECT * FROM map_places WHERE id = ?', (cur.lastrowid,)).fetchone()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return _map_place_row(row)


def map_place_list(user: str, q: str = '', category: str = '', limit: int = 200) -> list:
    limit = max(1, min(int(limit or 200), 500))
    sql = 'SELECT * FROM map_places WHERE user = ?'
    args = [user]
    q = (q or '').strip()
    if q:
        like = '%' + q + '%'
        sql += ' AND (name LIKE ? OR address LIKE ? OR note LIKE ?)'
        args += [like, like, like]
    category = (category or '').strip()
    if category:
        sql += ' AND category = ?'
        args.append(category)
    sql += ' ORDER BY updated_at DESC, id DESC LIMIT ?'
    args.append(limit)
    conn = connect()
    try:
        rows = _rows(conn, sql, args)
    finally:
        conn.close()
    return [_map_place_row(row) for row in rows]


def map_place_get(user: str, place_id: int):
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT * FROM map_places WHERE id = ? AND user = ?',
                     (int(place_id), user))
    finally:
        conn.close()
    return _map_place_row(rows[0]) if rows else None


def map_place_update(user: str, place_id: int, fields: dict):
    fields = fields if isinstance(fields, dict) else {}
    sets, args = [], []
    for key in ('name', 'address', 'city', 'district', 'adcode', 'category', 'note', 'source'):
        if key in fields:
            sets.append(key + ' = ?')
            args.append(str(fields.get(key) or '')[:500])
    for key in ('lng', 'lat'):
        if key in fields:
            sets.append(key + ' = ?')
            args.append(float(fields[key]))
    if 'tags' in fields:
        sets.append('tags = ?')
        args.append(json.dumps(fields.get('tags') if isinstance(fields.get('tags'), list) else [],
                               ensure_ascii=False))
    if not sets:
        return map_place_get(user, place_id)
    sets.append('updated_at = ?')
    args.append(now_str())
    args.extend([int(place_id), user])
    conn = connect()
    try:
        cur = conn.execute('UPDATE map_places SET ' + ', '.join(sets) +
                           ' WHERE id = ? AND user = ?', args)
        conn.commit()
        affected = cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('更新失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return map_place_get(user, place_id) if affected else None


def map_place_delete(user: str, place_id: int) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM map_places WHERE id = ? AND user = ?',
                           (int(place_id), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def map_cache_get(key: str, ttl_seconds: int = 600):
    """取一条高德缓存；过期就删掉并返回 None。"""
    key = (key or '')[:300]
    if not key:
        return None
    conn = connect()
    try:
        row = conn.execute('SELECT payload, fetched_at FROM map_cache WHERE cache_key = ?',
                           (key,)).fetchone()
        if not row:
            return None
        if time.time() - float(row['fetched_at'] or 0) > max(0, int(ttl_seconds)):
            conn.execute('DELETE FROM map_cache WHERE cache_key = ?', (key,))
            conn.commit()
            return None
        try:
            return json.loads(row['payload'] or 'null')
        except ValueError:
            return None
    finally:
        conn.close()


def map_cache_put(key: str, payload) -> None:
    key = (key or '')[:300]
    if not key:
        return
    blob = json.dumps(payload, ensure_ascii=False)
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO map_cache(cache_key, payload, fetched_at) VALUES (?,?,?) '
            'ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, '
            'fetched_at=excluded.fetched_at',
            (key, blob, time.time()))
        # 个人使用，缓存不会太大；顺手清掉 30 天前的旧条目
        conn.execute('DELETE FROM map_cache WHERE fetched_at < ?', (time.time() - 30 * 86400,))
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def map_cache_clear() -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM map_cache')
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def map_quota_get(day: str, service: str) -> int:
    conn = connect()
    try:
        row = conn.execute('SELECT count FROM map_api_usage WHERE day = ? AND service = ?',
                           (day, service)).fetchone()
        return int(row['count'] or 0) if row else 0
    finally:
        conn.close()


def map_quota_bump(day: str, service: str, amount: int = 1) -> int:
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO map_api_usage(day, service, count) VALUES (?,?,?) '
            'ON CONFLICT(day, service) DO UPDATE SET count = count + excluded.count',
            (day, service[:20], max(0, int(amount))))
        row = conn.execute('SELECT count FROM map_api_usage WHERE day = ? AND service = ?',
                           (day, service[:20])).fetchone()
        conn.execute("DELETE FROM map_api_usage WHERE day < date('now','localtime','-90 days')")
        conn.commit()
        return int(row['count'] or 0) if row else 0
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def map_quota_today() -> dict:
    day = time.strftime('%Y-%m-%d')
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT service, count FROM map_api_usage WHERE day = ?', (day,))
    finally:
        conn.close()
    return {'date': day, 'services': {row['service']: int(row['count'] or 0) for row in rows}}


# ---------------------------------------------------------------
# quiz_wrong / quiz_fav：出题小工具的错题库与收藏
#   每题存一份快照，原题被删也还能复习；按 user 隔离。
# ---------------------------------------------------------------
QUIZ_COLLECT_MAX = 300


def _quiz_entry_row(row) -> dict:
    data = dict(row)
    try:
        data['data'] = json.loads(data.get('data') or '{}')
    except ValueError:
        data['data'] = {}
    if 'given' in data:
        try:
            data['given'] = json.loads(data.get('given') or '[]')
        except ValueError:
            data['given'] = ''
    return data


def _quiz_prune(conn, table: str, user: str):
    conn.execute(
        'DELETE FROM %s WHERE user = ? AND id NOT IN ('
        'SELECT id FROM %s WHERE user = ? ORDER BY id DESC LIMIT ?)' % (table, table),
        (user, user, QUIZ_COLLECT_MAX))


def quiz_wrong_put(user: str, quiz_id: int, q_index: int, data: dict, given) -> dict:
    blob = json.dumps(data or {}, ensure_ascii=False)
    given_blob = json.dumps(given, ensure_ascii=False)
    now = now_str()
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO quiz_wrong(user, quiz_id, q_index, data, given, created_at, updated_at) '
            'VALUES (?,?,?,?,?,?,?) '
            'ON CONFLICT(user, quiz_id, q_index) DO UPDATE SET '
            'data = excluded.data, given = excluded.given, updated_at = excluded.updated_at',
            (user, int(quiz_id), int(q_index), blob, given_blob, now, now))
        _quiz_prune(conn, 'quiz_wrong', user)
        row = conn.execute(
            'SELECT id, quiz_id, q_index, data, given, created_at, updated_at '
            'FROM quiz_wrong WHERE user = ? AND quiz_id = ? AND q_index = ?',
            (user, int(quiz_id), int(q_index))).fetchone()
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return _quiz_entry_row(row)


def quiz_wrong_drop(user: str, quiz_id: int, q_index: int) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            'DELETE FROM quiz_wrong WHERE user = ? AND quiz_id = ? AND q_index = ?',
            (user, int(quiz_id), int(q_index)))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def quiz_wrong_remove(user: str, rid: int) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM quiz_wrong WHERE id = ? AND user = ?',
                           (int(rid), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def quiz_wrong_get(user: str, quiz_id: int, q_index: int):
    conn = connect()
    try:
        row = conn.execute(
            'SELECT id, quiz_id, q_index, data, given, created_at, updated_at '
            'FROM quiz_wrong WHERE user = ? AND quiz_id = ? AND q_index = ?',
            (user, int(quiz_id), int(q_index))).fetchone()
    finally:
        conn.close()
    return _quiz_entry_row(row) if row else None


def quiz_wrong_list(user: str, limit: int = QUIZ_COLLECT_MAX) -> list:
    limit = max(1, min(int(limit or QUIZ_COLLECT_MAX), QUIZ_COLLECT_MAX))
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT id, quiz_id, q_index, data, given, created_at, updated_at '
                           'FROM quiz_wrong WHERE user = ? ORDER BY updated_at DESC, id DESC LIMIT ?',
                     (user, limit))
    finally:
        conn.close()
    return [_quiz_entry_row(row) for row in rows]


def quiz_fav_get(user: str, quiz_id: int, q_index: int):
    conn = connect()
    try:
        row = conn.execute(
            'SELECT id, quiz_id, q_index, data, created_at '
            'FROM quiz_fav WHERE user = ? AND quiz_id = ? AND q_index = ?',
            (user, int(quiz_id), int(q_index))).fetchone()
    finally:
        conn.close()
    return _quiz_entry_row(row) if row else None


def quiz_fav_put(user: str, quiz_id: int, q_index: int, data: dict) -> dict:
    blob = json.dumps(data or {}, ensure_ascii=False)
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO quiz_fav(user, quiz_id, q_index, data, created_at) VALUES (?,?,?,?,?) '
            'ON CONFLICT(user, quiz_id, q_index) DO NOTHING',
            (user, int(quiz_id), int(q_index), blob, now_str()))
        _quiz_prune(conn, 'quiz_fav', user)
        row = conn.execute(
            'SELECT id, quiz_id, q_index, data, created_at '
            'FROM quiz_fav WHERE user = ? AND quiz_id = ? AND q_index = ?',
            (user, int(quiz_id), int(q_index))).fetchone()
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return _quiz_entry_row(row)


def quiz_fav_drop(user: str, quiz_id: int, q_index: int) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            'DELETE FROM quiz_fav WHERE user = ? AND quiz_id = ? AND q_index = ?',
            (user, int(quiz_id), int(q_index)))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def quiz_fav_remove(user: str, rid: int) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM quiz_fav WHERE id = ? AND user = ?',
                           (int(rid), user))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def quiz_fav_list(user: str, limit: int = QUIZ_COLLECT_MAX) -> list:
    limit = max(1, min(int(limit or QUIZ_COLLECT_MAX), QUIZ_COLLECT_MAX))
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT id, quiz_id, q_index, data, created_at '
                           'FROM quiz_fav WHERE user = ? ORDER BY id DESC LIMIT ?',
                     (user, limit))
    finally:
        conn.close()
    return [_quiz_entry_row(row) for row in rows]


def quiz_fav_indices(user: str, quiz_id: int) -> list:
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT q_index FROM quiz_fav WHERE user = ? AND quiz_id = ? '
                           'ORDER BY q_index', (user, int(quiz_id)))
    finally:
        conn.close()
    return [int(row['q_index']) for row in rows]


def quiz_fav_keys(user: str) -> set:
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT quiz_id, q_index FROM quiz_fav WHERE user = ?', (user,))
    finally:
        conn.close()
    return {(int(row['quiz_id']), int(row['q_index'])) for row in rows}


# ---------------------------------------------------------------
# daily_notes / daily_note_images：每日笔记（/notes）
#   每条记录是一段带时间的文字，可附图片；day 是归档用的 YYYY-MM-DD。
#   tags 存 JSON 给前端，tags_text 存换行分隔的索引给搜索 / 精确筛选用。
#   图片本体由 daily_notes.py 写到 data/notes/，这里只管元数据。
# ---------------------------------------------------------------
def _daily_tags_parts(tags) -> tuple:
    """标签去重并折成一行，同时生成 JSON 和搜索索引。"""
    clean, seen = [], set()
    for raw in (tags or []):
        text = ' '.join(str(raw or '').split())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(text)
    blob = json.dumps(clean, ensure_ascii=False)
    index = '\n' + '\n'.join(clean) + '\n' if clean else ''
    return clean, blob, index


def _daily_note_row(row) -> dict:
    data = dict(row)
    try:
        tags = json.loads(data.get('tags') or '[]')
    except ValueError:
        tags = []
    data['tags'] = [str(t) for t in tags if str(t).strip()] if isinstance(tags, list) else []
    try:
        data['importance'] = max(0, min(2, int(data.get('importance') or 0)))
    except (TypeError, ValueError):
        data['importance'] = 0
    data.pop('tags_text', None)
    return data


def daily_note_add(user: str, day: str, content: str, tags, importance: int = 0) -> dict:
    clean, blob, index = _daily_tags_parts(tags)
    importance = max(0, min(2, int(importance or 0)))
    now = now_str()
    conn = connect()
    try:
        cur = conn.execute(
            'INSERT INTO daily_notes(user, day, content, tags, tags_text, importance, '
            'created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
            (user, day, content, blob, index, importance, now, now))
        conn.commit()
        rid = cur.lastrowid
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return {'id': rid, 'user': user, 'day': day, 'content': content, 'tags': clean,
            'importance': importance, 'created_at': now, 'updated_at': now}


def daily_note_get(user: str, note_id) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            'SELECT id, user, day, content, tags, importance, created_at, updated_at '
            'FROM daily_notes WHERE id = ? AND user = ?', (int(note_id), user)).fetchone()
    finally:
        conn.close()
    return _daily_note_row(row) if row else None


def daily_note_list(user: str, day: str = '', q: str = '', tag: str = '',
                    limit: int = 300, level=None) -> list:
    limit = max(1, min(int(limit or 300), 500))
    sql = ('SELECT id, user, day, content, tags, importance, created_at, updated_at '
           'FROM daily_notes WHERE user = ?')
    args = [user]
    if day:
        sql += ' AND day = ?'
        args.append(day)
    q = (q or '').strip()
    if q:
        like = '%' + q + '%'
        sql += ' AND (content LIKE ? OR tags_text LIKE ?)'
        args.extend([like, like])
    tag = (tag or '').strip()
    if tag:
        sql += ' AND tags_text LIKE ?'
        args.append('%\n' + tag + '\n%')
    if level is not None:
        sql += ' AND importance = ?'
        args.append(max(0, min(2, int(level))))
    sql += ' ORDER BY day DESC, created_at DESC, id DESC LIMIT ?'
    args.append(limit)
    conn = connect()
    try:
        rows = _rows(conn, sql, args)
    finally:
        conn.close()
    return [_daily_note_row(row) for row in rows]


def daily_note_update(user: str, note_id, day: str, content: str, tags,
                      importance: int = 0) -> int:
    clean, blob, index = _daily_tags_parts(tags)
    importance = max(0, min(2, int(importance or 0)))
    conn = connect()
    try:
        cur = conn.execute(
            'UPDATE daily_notes SET day = ?, content = ?, tags = ?, tags_text = ?, '
            'importance = ?, updated_at = ? WHERE id = ? AND user = ?',
            (day, content, blob, index, importance, now_str(), int(note_id), user))
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('更新失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def daily_note_delete(user: str, note_id) -> int:
    conn = connect()
    try:
        conn.execute('DELETE FROM daily_note_images WHERE note_id = ? AND user = ?',
                     (int(note_id), user))
        cur = conn.execute('DELETE FROM daily_notes WHERE id = ? AND user = ?',
                           (int(note_id), user))
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('删除失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def daily_note_range(user: str, start_day: str, end_day: str, limit: int = 3000) -> list:
    """按日期区间取笔记，给日 / 周 / 月分析用；时间正序方便模型阅读。"""
    limit = max(1, min(int(limit or 3000), 5000))
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT id, user, day, content, tags, importance, created_at, updated_at '
                           'FROM daily_notes WHERE user = ? AND day >= ? AND day <= ? '
                           'ORDER BY day ASC, created_at ASC, id ASC LIMIT ?',
                     (user, start_day, end_day, limit))
    finally:
        conn.close()
    return [_daily_note_row(row) for row in rows]


def daily_note_image_period_total(user: str, start_day: str, end_day: str) -> dict:
    """区间内的图片总数和有图笔记数，不返回图片内容。"""
    conn = connect()
    try:
        row = conn.execute(
            'SELECT COUNT(*) AS total, COUNT(DISTINCT i.note_id) AS notes '
            'FROM daily_note_images i JOIN daily_notes n '
            'ON n.id = i.note_id AND n.user = i.user '
            'WHERE n.user = ? AND n.day >= ? AND n.day <= ?',
            (user, start_day, end_day)).fetchone()
    finally:
        conn.close()
    return {'total': int(row['total'] or 0) if row else 0,
            'notes': int(row['notes'] or 0) if row else 0}


def _note_analysis_row(row) -> dict:
    data = dict(row)
    try:
        result = json.loads(data.get('result') or '{}')
    except ValueError:
        result = {}
    data['result'] = result if isinstance(result, dict) else {}
    data['truncated'] = bool(data.get('truncated'))
    return data


def note_analysis_get(user: str, period: str, period_key: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            'SELECT id, user, period, period_key, start_day, end_day, input_hash, '
            'note_count, truncated, model, result, created_at, updated_at '
            'FROM note_analyses WHERE user = ? AND period = ? AND period_key = ?',
            (user, period, period_key)).fetchone()
    finally:
        conn.close()
    return _note_analysis_row(row) if row else None


def note_analysis_save(user: str, period: str, period_key: str, start_day: str,
                       end_day: str, input_hash: str, note_count: int,
                       truncated: bool, model: str, result: dict) -> dict:
    now = now_str()
    payload = json.dumps(result if isinstance(result, dict) else {},
                         ensure_ascii=False)
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO note_analyses(user, period, period_key, start_day, end_day, '
            'input_hash, note_count, truncated, model, result, created_at, updated_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(user, period, period_key) DO UPDATE SET '
            'start_day = excluded.start_day, end_day = excluded.end_day, '
            'input_hash = excluded.input_hash, note_count = excluded.note_count, '
            'truncated = excluded.truncated, model = excluded.model, '
            'result = excluded.result, updated_at = excluded.updated_at',
            (user, period, period_key, start_day, end_day, input_hash,
             int(note_count or 0), 1 if truncated else 0, model, payload, now, now))
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入分析失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()
    return note_analysis_get(user, period, period_key) or {}


def note_analysis_delete(user: str, period: str, period_key: str) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            'DELETE FROM note_analyses WHERE user = ? AND period = ? AND period_key = ?',
            (user, period, period_key))
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('删除分析失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def note_analysis_usage_add(user: str, day: str, period: str, period_key: str) -> None:
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO note_analysis_usage(user, day, period, period_key, created_at) '
            'VALUES (?,?,?,?,?)',
            (user, day, period, period_key, now_str()))
        conn.commit()
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('记录分析额度失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def note_analysis_usage_count(user: str, day: str) -> int:
    conn = connect()
    try:
        return int(conn.execute(
            'SELECT COUNT(*) FROM note_analysis_usage WHERE user = ? AND day = ?',
            (user, day)).fetchone()[0])
    finally:
        conn.close()


def daily_note_days(user: str, limit: int = 90) -> list:
    limit = max(1, min(int(limit or 90), 365))
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT day, COUNT(*) AS count FROM daily_notes '
                           'WHERE user = ? GROUP BY day ORDER BY day DESC LIMIT ?',
                     (user, limit))
    finally:
        conn.close()
    return [{'day': row['day'], 'count': int(row['count'] or 0)} for row in rows]


def daily_note_month(user: str, month: str) -> list:
    """某个月每天的记录条数；month 形如 2026-11。"""
    text = str(month or '')
    start = text + '-01'
    year = int(text[:4])
    mon = int(text[5:7])
    if mon == 12:
        end = '%04d-01-01' % (year + 1)
    else:
        end = '%04d-%02d-01' % (year, mon + 1)
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT day, COUNT(*) AS count FROM daily_notes '
                           'WHERE user = ? AND day >= ? AND day < ? '
                           'GROUP BY day ORDER BY day',
                     (user, start, end))
    finally:
        conn.close()
    return [{'day': row['day'], 'count': int(row['count'] or 0)} for row in rows]


def daily_note_streak(user: str, today: str = '') -> dict:
    """连续记录天数：当前链允许从今天或昨天开始，未来日期不计。"""
    try:
        anchor = date.fromisoformat(str(today or ''))
    except ValueError:
        anchor = date.today()
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT DISTINCT day FROM daily_notes WHERE user = ?', (user,))
    finally:
        conn.close()
    seen = set()
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get('day') or ''))
        except ValueError:
            continue
        if day <= anchor:
            seen.add(day)
    if not seen:
        return {'current': 0, 'longest': 0, 'last': ''}
    ordered = sorted(seen)
    longest = run = 1
    for prev, cur in zip(ordered, ordered[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        if run > longest:
            longest = run
    cursor = anchor if anchor in seen else anchor - timedelta(days=1)
    current = 0
    while cursor in seen:
        current += 1
        cursor -= timedelta(days=1)
    return {'current': current, 'longest': longest, 'last': ordered[-1].isoformat()}


def daily_note_count(user: str) -> int:
    conn = connect()
    try:
        return int(conn.execute('SELECT COUNT(*) FROM daily_notes WHERE user = ?',
                                (user,)).fetchone()[0])
    finally:
        conn.close()


def daily_note_tags(user: str, limit: int = 80) -> list:
    """最近 2000 条里出现过的标签，按使用次数排序。"""
    limit = max(1, min(int(limit or 80), 200))
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT tags_text FROM daily_notes WHERE user = ? '
                           'ORDER BY id DESC LIMIT 2000', (user,))
    finally:
        conn.close()
    counts = {}
    for row in rows:
        for tag in str(row.get('tags_text') or '').split('\n'):
            tag = tag.strip()
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{'name': name, 'count': count} for name, count in ordered[:limit]]


def daily_note_image_add(user: str, note_id, stored_name: str, mime: str, size: int) -> dict:
    now = now_str()
    conn = connect()
    try:
        cur = conn.execute(
            'INSERT INTO daily_note_images(note_id, user, stored_name, mime, bytes, created_at) '
            'VALUES (?,?,?,?,?,?)',
            (int(note_id), user, stored_name, mime, int(size), now))
        conn.commit()
        return {'id': cur.lastrowid, 'note_id': int(note_id), 'stored_name': stored_name,
                'mime': mime, 'bytes': int(size), 'created_at': now}
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def daily_note_images_add(user: str, note_id, items) -> list:
    """一次插入多张图，失败时整批回滚。"""
    now = now_str()
    rows = []
    conn = connect()
    try:
        for item in (items or []):
            cur = conn.execute(
                'INSERT INTO daily_note_images(note_id, user, stored_name, mime, bytes, created_at) '
                'VALUES (?,?,?,?,?,?)',
                (int(note_id), user, item['stored_name'], item['mime'],
                 int(item['bytes']), now))
            rows.append({'id': cur.lastrowid, 'note_id': int(note_id),
                         'stored_name': item['stored_name'], 'mime': item['mime'],
                         'bytes': int(item['bytes']), 'created_at': now})
        conn.commit()
        return rows
    except sqlite3.OperationalError as exc:
        conn.rollback()
        raise StorageUnavailable('写入失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def daily_note_image_list(user: str, note_id) -> list:
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT id, note_id, user, stored_name, mime, bytes, created_at '
                           'FROM daily_note_images WHERE note_id = ? AND user = ? '
                           'ORDER BY id ASC', (int(note_id), user))
    finally:
        conn.close()
    return rows


def daily_note_image_groups(user: str, note_ids) -> dict:
    """一次取多条记录的图片，列表页避免每条记录查一次库。"""
    ids = [int(note_id) for note_id in (note_ids or [])]
    if not ids:
        return {}
    marks = ','.join('?' for _ in ids)
    conn = connect()
    try:
        rows = _rows(conn, 'SELECT id, note_id, user, stored_name, mime, bytes, created_at '
                           'FROM daily_note_images WHERE user = ? AND note_id IN (%s) '
                           'ORDER BY id ASC' % marks, [user] + ids)
    finally:
        conn.close()
    groups = {}
    for row in rows:
        groups.setdefault(int(row['note_id']), []).append(row)
    return groups


def daily_note_image_get(user: str, image_id) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            'SELECT id, note_id, user, stored_name, mime, bytes, created_at '
            'FROM daily_note_images WHERE id = ? AND user = ?',
            (int(image_id), user)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def daily_note_image_delete(user: str, image_id) -> int:
    conn = connect()
    try:
        cur = conn.execute('DELETE FROM daily_note_images WHERE id = ? AND user = ?',
                           (int(image_id), user))
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError as exc:
        raise StorageUnavailable('删除失败（文件系统只读？）：%s' % exc) from exc
    finally:
        conn.close()


def daily_note_image_total(user: str) -> int:
    conn = connect()
    try:
        row = conn.execute('SELECT COALESCE(SUM(bytes), 0) AS total FROM daily_note_images '
                           'WHERE user = ?', (user,)).fetchone()
    finally:
        conn.close()
    return int(row['total'] or 0) if row else 0
