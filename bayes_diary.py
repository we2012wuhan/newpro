# -*- coding: utf-8 -*-
"""
贝叶斯日记 · 后端
==================
和这个仓库里其它小工具不一样：这个工具的计算**全部在 Python 里做**。

    前端 只负责画和收集输入
    Python 负责：似然比、后验、校准指标、体检报告、认知提醒

数据存 SQLite（data/bayes.db），所以换浏览器、换设备记录都还在。

三条不可动摇的规则，都是为了保住数据的可信度：
  1. 先验一旦写下就不能改 —— 否则你永远在事后修改自己的记忆
  2. 结算后不能再加证据 —— 结果出来之后再找证据，那是编故事
  3. 结果只能记一次，不能反悔 —— 反悔一次，校准曲线就废了
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

import bayes_math as M

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'bayes_diary.html'
_DB_FILE = _BASE_DIR / 'data' / 'bayes.db'

MAX_QUESTION = 200
MAX_EVIDENCE_TEXT = 200
DOMAINS = ['工作', '人际', '消费', '健康', '时间', '信息', '学习', '其他']

# ---------------------------------------------------------------
# 快速记证据用的五档：不需要用户去猜两个概率，选一个词就够了
# 每一档都写清楚 p_if_true / p_if_false 各是多少，界面上要展示出来
# ---------------------------------------------------------------
LEVELS = [
    {'key': 'strong_for', 'label': '强支持', 'lr': 5.0, 'p_if_true': 0.50, 'p_if_false': 0.10,
     'desc': '如果它会发生，我几乎一定会看到这条；如果不会发生，我基本看不到'},
    {'key': 'for', 'label': '支持', 'lr': 2.0, 'p_if_true': 0.40, 'p_if_false': 0.20,
     'desc': '如果它会发生，看到这条的可能性更大一些'},
    {'key': 'neutral', 'label': '说不清', 'lr': 1.0, 'p_if_true': 0.30, 'p_if_false': 0.30,
     'desc': '发生也好、不发生也好，我都会看到它 —— 那它就不是证据'},
    {'key': 'against', 'label': '反对', 'lr': 0.5, 'p_if_true': 0.20, 'p_if_false': 0.40,
     'desc': '如果它不会发生，看到这条的可能性更大一些'},
    {'key': 'strong_against', 'label': '强反对', 'lr': 0.2, 'p_if_true': 0.10, 'p_if_false': 0.50,
     'desc': '如果它不会发生，我几乎一定会看到这条'},
]
_LEVEL_BY_KEY = {x['key']: x for x in LEVELS}

# 具体例子：光讲公式没用，得看到"什么样的线索才算证据"
ANCHOR_EXAMPLES = [
    {'evidence': '出门时天已经阴沉下来', 'for_what': '今天下午会下雨',
     'p_if_true': 0.70, 'p_if_false': 0.30,
     'note': '阴天和下雨确实相关，但晴天转阴也很常见 —— 所以它有信息量，但不强。'},
    {'evidence': '他今天一整天没回我消息', 'for_what': '他不想理我',
     'p_if_true': 0.50, 'p_if_false': 0.45,
     'note': '关键点在这：他忙起来、手机没电、在开会的时候，同样不回消息。'
             '所以 LR≈1.1，这条几乎不是证据 —— 但我们最容易拿它当证据。'},
    {'evidence': '我提前一周就把稿子写完了', 'for_what': '这次能按时交付',
     'p_if_true': 0.60, 'p_if_false': 0.15,
     'note': '这条的 LR≈4，是真的有信息量：按时写完的人，最后按时交付的概率高得多。'},
    {'evidence': '天气预报说明天降水概率 90%', 'for_what': '明天下雨',
     'p_if_true': 0.90, 'p_if_false': 0.10,
     'note': 'LR=9。注意这条本身就是别人算好的概率，直接拿它当证据是合理的。'},
    {'evidence': '群里没人回复我的提议', 'for_what': '大家不支持这个提议',
     'p_if_true': 0.40, 'p_if_false': 0.45,
     'note': 'LR≈0.9，甚至微微反对。沉默更可能是因为消息被刷过去了 —— '
             '把"没有反应"当成"反对"，是最常见的过度解读。'},
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT    NOT NULL,
    prior       REAL    NOT NULL,
    domain      TEXT    NOT NULL DEFAULT '',
    due         TEXT    NOT NULL DEFAULT '',
    note        TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'open',
    created_at  TEXT    NOT NULL,
    settled_at  TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bet_id      INTEGER NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
    text        TEXT    NOT NULL DEFAULT '',
    p_if_true   REAL    NOT NULL,
    p_if_false  REAL    NOT NULL,
    lr          REAL    NOT NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_bet ON evidence(bet_id);
"""


# ---------------------------------------------------------------
# 存储
# ---------------------------------------------------------------
def _now() -> str:
    return _dt.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


def _today() -> str:
    return _dt.date.today().isoformat()


def _connect() -> sqlite3.Connection:
    _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_FILE), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _as_text(value, limit: int) -> str:
    return str(value or '').strip()[:limit]


def _as_prob(value, default: float) -> float:
    try:
        return round(M.clamp(float(value), 0.01, 0.99), 4)
    except (TypeError, ValueError):
        return default


def _as_due(value) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    try:
        return _dt.date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return ''


def _days_left(due: str):
    if not due:
        return None
    try:
        return (_dt.date.fromisoformat(due) - _dt.date.today()).days
    except ValueError:
        return None


# ---------------------------------------------------------------
# 序列化：后验永远由 Python 现算，不存"结果值"，避免它和证据脱节
# ---------------------------------------------------------------
def _read_bets(conn) -> list:
    bets = [dict(r) for r in conn.execute('SELECT * FROM bets ORDER BY id DESC')]
    if not bets:
        return []
    rows = [dict(r) for r in conn.execute('SELECT * FROM evidence ORDER BY id ASC')]
    bucket = {}
    for r in rows:
        bucket.setdefault(r['bet_id'], []).append(r)

    today = _today()
    for b in bets:
        evs = bucket.get(b['id'], [])
        lrs = [e['lr'] for e in evs]
        path = M.step_posteriors(b['prior'], lrs)
        for i, e in enumerate(evs):
            e['posterior_after'] = round(path[i + 1], 6)
            e['move'] = round(path[i + 1] - path[i], 6)
        post = path[-1]
        b['evidence'] = evs
        b['posterior'] = round(post, 6)
        b['n_evidence'] = len(evs)
        b['move'] = round(post - b['prior'], 6)
        b['days_left'] = _days_left(b['due'])
        b['overdue'] = bool(b['status'] == 'open' and b['due'] and b['due'] < today)
        b['due_soon'] = bool(b['status'] == 'open' and b['days_left'] is not None and 0 <= b['days_left'] <= 1)
    return bets


def _stats(conn, bets) -> dict:
    settled = [b for b in bets if b['status'] in ('yes', 'no')]
    records = [{
        'prior': b['prior'],
        'posterior': b['posterior'],
        'outcome': 1 if b['status'] == 'yes' else 0,
        'domain': b['domain'],
        'created_at': b['created_at'],
        'settled_at': b['settled_at'] or b['created_at'],
        'n_evidence': b['n_evidence'],
    } for b in settled]
    return M.analyze(records, open_count=len([b for b in bets if b['status'] == 'open']))


def state_payload() -> dict:
    init_db()
    conn = _connect()
    try:
        bets = _read_bets(conn)
        stats = _stats(conn, bets)
    finally:
        conn.close()
    return {
        'ok': True,
        'today': _today(),
        'bets': bets,
        'stats': stats,
        'levels': LEVELS,
        'anchors': {'examples': ANCHOR_EXAMPLES, 'domains': DOMAINS},
        'db': str(_DB_FILE),
    }


# ---------------------------------------------------------------
# 页面
# ---------------------------------------------------------------
@router.get('/bayes-diary', response_class=HTMLResponse)
def bayes_diary_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/bayes_diary.html 存在。</p></body></html>')
    return HTMLResponse(html)


# ---------------------------------------------------------------
# API
# ---------------------------------------------------------------
@router.get('/api/bayes/state')
def api_state():
    return JSONResponse(state_payload())


@router.get('/api/bayes/anchors')
def api_anchors():
    """似然比锚点：把"这条线索到底算不算证据"算给你看。"""
    rows = []
    for ex in ANCHOR_EXAMPLES:
        lr = M.lr_from_pair(ex['p_if_true'], ex['p_if_false'])
        rows.append({
            **ex,
            'lr': round(lr, 4),
            'verdict': _lr_verdict(lr),
        })
    return JSONResponse({'ok': True, 'levels': LEVELS, 'examples': rows})


def _lr_verdict(lr: float) -> str:
    if lr >= 4:
        return '强证据'
    if lr >= 1.5:
        return '有点信息量'
    if lr > 0.67:
        return '基本不是证据'
    if lr > 0.25:
        return '反向有点信息量'
    return '强反证'


@router.post('/api/bayes/bets')
def api_create_bet(payload: dict = Body(...)):
    question = _as_text(payload.get('question'), MAX_QUESTION)
    if len(question) < 2:
        raise HTTPException(status_code=400, detail='预测至少要写 2 个字')
    prior = _as_prob(payload.get('prior'), 0.7)
    domain = _as_text(payload.get('domain'), 12) or '其他'
    due = _as_due(payload.get('due'))
    note = _as_text(payload.get('note'), 500)
    init_db()
    conn = _connect()
    try:
        conn.execute(
            'INSERT INTO bets (question, prior, domain, due, note, status, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (question, prior, domain, due, note, 'open', _now()))
        conn.commit()
    finally:
        conn.close()
    return JSONResponse(state_payload())


@router.patch('/api/bayes/bets/{bet_id}')
def api_update_bet(bet_id: int, payload: dict = Body(...)):
    init_db()
    conn = _connect()
    try:
        row = conn.execute('SELECT * FROM bets WHERE id = ?', (bet_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='没有这条预测')
        bet = dict(row)

        # 结算：只能记一次，记完锁死。反悔会毁掉校准曲线的可信度。
        if 'outcome' in payload:
            outcome = str(payload.get('outcome') or '').strip()
            if outcome not in ('yes', 'no'):
                raise HTTPException(status_code=400, detail='结果只能是 yes / no')
            if bet['status'] != 'open':
                raise HTTPException(status_code=409, detail='这条已经结算过了，结果不能改。记错了就删掉重来。')
            conn.execute('UPDATE bets SET status = ?, settled_at = ? WHERE id = ?',
                         (outcome, _now(), bet_id))
            conn.commit()
            return JSONResponse(state_payload())

        # 结算前可以改描述类字段；先验不在可改之列
        fields, values = [], []
        if 'question' in payload:
            q = _as_text(payload.get('question'), MAX_QUESTION)
            if len(q) < 2:
                raise HTTPException(status_code=400, detail='预测至少要写 2 个字')
            fields.append('question = ?'); values.append(q)
        if 'domain' in payload:
            fields.append('domain = ?'); values.append(_as_text(payload.get('domain'), 12) or '其他')
        if 'due' in payload:
            fields.append('due = ?'); values.append(_as_due(payload.get('due')))
        if 'note' in payload:
            fields.append('note = ?'); values.append(_as_text(payload.get('note'), 500))
        if 'prior' in payload:
            raise HTTPException(status_code=400, detail='先验写下之后不能改 —— 这是这个工具的底线')
        if fields:
            values.append(bet_id)
            conn.execute('UPDATE bets SET ' + ', '.join(fields) + ' WHERE id = ?', values)
            conn.commit()
    finally:
        conn.close()
    return JSONResponse(state_payload())


@router.delete('/api/bayes/bets/{bet_id}')
def api_delete_bet(bet_id: int):
    init_db()
    conn = _connect()
    try:
        conn.execute('DELETE FROM evidence WHERE bet_id = ?', (bet_id,))
        conn.execute('DELETE FROM bets WHERE id = ?', (bet_id,))
        conn.commit()
    finally:
        conn.close()
    return JSONResponse(state_payload())


@router.post('/api/bayes/bets/{bet_id}/evidence')
def api_add_evidence(bet_id: int, payload: dict = Body(...)):
    init_db()
    conn = _connect()
    try:
        row = conn.execute('SELECT * FROM bets WHERE id = ?', (bet_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='没有这条预测')
        if row['status'] != 'open':
            raise HTTPException(status_code=409,
                                detail='已经结算了，不能再补证据 —— 结果出来之后再找线索，那是编故事')

        level = _as_text(payload.get('level'), 24)
        if level:
            if level not in _LEVEL_BY_KEY:
                raise HTTPException(status_code=400, detail='不认识这个证据档位')
            spec = _LEVEL_BY_KEY[level]
            p_true, p_false = spec['p_if_true'], spec['p_if_false']
        else:
            if 'p_if_true' not in payload or 'p_if_false' not in payload:
                raise HTTPException(status_code=400, detail='要么给档位，要么给两个可能性')
            p_true = M.clamp(payload.get('p_if_true'), 0.0, 1.0)
            p_false = M.clamp(payload.get('p_if_false'), 0.0, 1.0)

        lr = M.lr_from_pair(p_true, p_false)
        conn.execute(
            'INSERT INTO evidence (bet_id, text, p_if_true, p_if_false, lr, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (bet_id, _as_text(payload.get('text'), MAX_EVIDENCE_TEXT),
             round(p_true, 4), round(p_false, 4), round(lr, 6), _now()))
        conn.commit()
    finally:
        conn.close()
    return JSONResponse(state_payload())


@router.delete('/api/bayes/evidence/{evidence_id}')
def api_delete_evidence(evidence_id: int):
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            'SELECT e.id, b.status FROM evidence e JOIN bets b ON b.id = e.bet_id WHERE e.id = ?',
            (evidence_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='没有这条证据')
        if row['status'] != 'open':
            raise HTTPException(status_code=409, detail='已经结算了，证据记录不能再动')
        conn.execute('DELETE FROM evidence WHERE id = ?', (evidence_id,))
        conn.commit()
    finally:
        conn.close()
    return JSONResponse(state_payload())


@router.post('/api/bayes/demo/sequence')
def api_demo_sequence(payload: dict = Body(...)):
    """顺序不改变结论 —— 让 Python 现场算给你看，而不是前端写死的动画。"""
    prior = M.clamp(payload.get('prior'), 0.01, 0.99)
    steps = payload.get('steps') or []
    if not isinstance(steps, list) or len(steps) != 2:
        raise HTTPException(status_code=400, detail='需要两步证据')
    lrs, metas = [], []
    for s in steps:
        p_true = M.clamp(s.get('p_if_true'), 0.0, 1.0)
        p_false = M.clamp(s.get('p_if_false'), 0.0, 1.0)
        lr = M.lr_from_pair(p_true, p_false)
        lrs.append(lr)
        metas.append({'p_if_true': p_true, 'p_if_false': p_false, 'lr': round(lr, 4)})
    res = M.sequence_demo(prior, [lrs[0]], [lrs[1]])
    return JSONResponse({
        'ok': True,
        'prior': prior,
        'steps': metas,
        'path_ab': [round(x, 6) for x in res['path1']],
        'path_ba': [round(x, 6) for x in res['path2']],
        'final': round(res['final'], 6),
        'identical': res['identical'],
        'explain': ('两条路线的中间值不同，但终点一定相同：'
                    '贝叶斯更新本质上只是把胜算连乘，而乘法满足交换律。'
                    '所以你不必纠结"该先看哪条证据"—— 该纠结的是每条证据的 LR 估得准不准。'),
    })


@router.get('/api/bayes/export')
def api_export():
    payload = state_payload()
    payload['exported_at'] = _now()
    payload['app'] = '贝叶斯日记'
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return HTMLResponse(
        content=body,
        media_type='application/json',
        headers={'Content-Disposition': 'attachment; filename=bayes-diary.json'})


@router.post('/api/bayes/reset')
def api_reset(payload: dict = Body(default={})):
    if str(payload.get('confirm') or '') != '清空':
        raise HTTPException(status_code=400, detail='需要确认字符串')
    init_db()
    conn = _connect()
    try:
        conn.execute('DELETE FROM evidence')
        conn.execute('DELETE FROM bets')
        conn.commit()
    finally:
        conn.close()
    return JSONResponse(state_payload())


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='贝叶斯日记', description='Python 做贝叶斯分析 · 预测下注 · 证据更新 · 校准报告', version='2.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8002)
