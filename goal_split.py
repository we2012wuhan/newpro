# -*- coding: utf-8 -*-
# 目标拆解器
# ------------------------------------------------------------------
# 针对一个很具体的毛病：心里有个目标，但从来没拆过，它就一直停在
# 「我想……」这个状态。所以这个工具**不给建议** —— 给建议没用，你收藏完
# 就忘了。它只做三件事：
#
#   ① 逼你拆：五步一层层往下问，每一步你不填就走不到下一步。
#      模型在任何一步都不会替你写出答案，只问一个问题。
#   ② 验算你的拆解：可验证性、时间账、粒度、顺序、可控性、失败预案，
#      全部是 Python 按规则算出来的（_check），不是模型在夸你。
#      算完再让模型用中文说清「这几条为什么算问题」。
#   ③ 按日期盯你：今天该做什么、动作勾没勾、里程碑到没到、这周实际投入
#      跟计划差多少 —— 偏离数据会回填进下一次体检的时间账。
#
# 页面在 /goal-split，一条目标 = SQLite 的一行（tool = 'goal-split'）。
# 页面认 ?r=<记录 id> 深链，能从「记录总览」直接跳过来。
#
# 和工具箱里其它工具的分工：
#   苏格拉底提问 → 把一件事想清楚（不给答案）
#   目标拆解器   → 把一个目标拆到能今天动手，并且盯着你做完
#
# 环境变量：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import storage

try:
    from model_config import llm_endpoint, llm_key, llm_model
except Exception:  # 单独跑这个文件时的兜底
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'


router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'goal-split.html'

TOOL = 'goal-split'
LIST_MAX = 60
TITLE_MAX = 60
RAW_MAX = 200
TEXT_MAX = 160
WHY_MAX = 600
NOTE_MAX = 800
MILESTONE_MAX = 6
ACTION_MAX = 24
LOG_MAX = 60
DAY_MIN = 24 * 60
ACT_MIN_MAX = 300      # 单条动作超过 5 小时，那已经是一件事而不是一个动作
HOURS_MAX = 80         # 每周可投入小时数的上限，防手滑
STAGES = 5

NO_KEY = '服务端没配模型 Key（DEEPSEEK_API_KEY），这一页的追问和总评用不了，规则体检照样能跑。'

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# 判断「这条到底是不是可验收」用的词表。不求全，够挡住最常见的糊弄写法。
VAGUE_WORDS = ('提升', '加强', '优化', '改善', '尽量', '努力', '多', '好好',
               '差不多', '争取', '更好地', '变得更好', '提高')
# 以这些词开头的「动作」通常不是动作，是愿望
WISH_STARTS = ('要', '应该', '得', '尽量', '努力', '好好', '多', '少', '注意',
               '重视', '坚持', '准备', '开始学习')
# 出现这些词说明这条不在你自己手里
OUTSIDE_WORDS = ('等', '审批', '批准', '领导', '客户', '对方', '老板', '同事',
                 '甲方', '回复', '取决于', '需要别人', '配合', '排期', '面试结果')
# 动机不像自己的：外部压力型
SHOULD_WORDS = ('应该', '必须', '别人都', '大家都在', '不得不', '被要求', '不好意思')


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


async def _body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _clean(value, limit: int) -> str:
    return ' '.join(str(value or '').split())[:limit]


def _date(value):
    """只认 YYYY-MM-DD，别的当空。"""
    s = str(value or '').strip()[:10]
    return s if DATE_RE.match(s) else ''


def _today() -> str:
    return date.today().isoformat()


def _days_between(a: str, b: str) -> int:
    try:
        da = datetime.strptime(a, '%Y-%m-%d').date()
        db = datetime.strptime(b, '%Y-%m-%d').date()
    except Exception:
        return 0
    return (db - da).days


def _int(value, lo: int, hi: int, default: int = 0) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))
# =============================================================
# 数据进出的规范化：前端传什么上来都先过一遍，不整包往库里塞
# =============================================================
def _norm_action(raw, idx: int):
    if not isinstance(raw, dict):
        return None
    text = _clean(raw.get('text'), TEXT_MAX)
    if not text:
        return None
    return {
        'id': _clean(raw.get('id'), 24) or ('a%d' % (idx + 1)),
        'mid': _clean(raw.get('mid'), 24),
        'text': text,
        'min': _int(raw.get('min'), 5, DAY_MIN, 45),
        'out': _clean(raw.get('out'), TEXT_MAX),
        'on': _date(raw.get('on')),
        'done': bool(raw.get('done')),
        'done_at': _date(raw.get('done_at')) or (_today() if raw.get('done') else ''),
        'actual': _int(raw.get('actual'), 0, DAY_MIN, 0),
    }


def _norm_milestone(raw, idx: int):
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get('name'), TEXT_MAX)
    if not name:
        return None
    st = raw.get('status')
    return {
        'id': _clean(raw.get('id'), 24) or ('m%d' % (idx + 1)),
        'name': name,
        'done_when': _clean(raw.get('done_when'), TEXT_MAX),
        'by': _date(raw.get('by')),
        'why': _clean(raw.get('why'), TEXT_MAX),
        'status': st if st in ('todo', 'doing', 'done', 'missed') else 'todo',
        'review': _clean(raw.get('review'), WHY_MAX),
        'reviewed_at': _date(raw.get('reviewed_at')),
    }


def _norm_log(raw, idx: int):
    if not isinstance(raw, dict):
        return None
    kind = raw.get('kind') if raw.get('kind') in ('drift', 'review', 'note') else 'note'
    text = _clean(raw.get('text'), WHY_MAX)
    if kind != 'drift' and not text:
        return None
    return {
        'id': _clean(raw.get('id'), 24) or ('g%d' % (int(time.time() * 1000) + idx)),
        'kind': kind,
        'text': text,
        'hours': _int(raw.get('hours'), 0, 168, 0),
        'missed': _int(raw.get('missed'), 0, 99, 0),
        'at': _date(raw.get('at')) or _today(),
    }


def _norm_plan(raw: dict) -> dict:
    """把库里的 payload（或前端刚提交的）整成一份干净的。"""
    out = {}
    for key, limit in (('raw', RAW_MAX), ('metric', TEXT_MAX), ('cur', TEXT_MAX),
                       ('target', TEXT_MAX), ('why', WHY_MAX), ('cost', WHY_MAX),
                       ('fallback', WHY_MAX), ('note', NOTE_MAX)):
        out[key] = _clean(raw.get(key), limit)
    out['deadline'] = _date(raw.get('deadline'))
    out['hours_week'] = _int(raw.get('hours_week'), 0, HOURS_MAX, 0)
    out['stage'] = _int(raw.get('stage'), 1, STAGES, 1)

    seen, ms = set(), []
    for i, m in enumerate(raw.get('milestones') or []):
        if len(ms) >= MILESTONE_MAX:
            break
        x = _norm_milestone(m, i)
        if not x or x['id'] in seen:
            continue
        seen.add(x['id'])
        ms.append(x)
    out['milestones'] = ms

    seen, acts = set(), []
    for i, a in enumerate(raw.get('actions') or []):
        if len(acts) >= ACTION_MAX:
            break
        x = _norm_action(a, i)
        if not x or x['id'] in seen:
            continue
        seen.add(x['id'])
        acts.append(x)
    out['actions'] = acts

    logs = []
    for i, g in enumerate(raw.get('log') or []):
        if len(logs) >= LOG_MAX:
            break
        x = _norm_log(g, i)
        if x:
            logs.append(x)
    out['log'] = logs

    out['created_at'] = _date(raw.get('created_at')) or _today()
    out['updated_at'] = _today()
    out['statement'] = _statement(out)
    return out


def _statement(plan: dict) -> str:
    """把三段式拼成一句能验收的话。缺件就返回空，交给体检去报。"""
    d, m, t = plan.get('deadline'), plan.get('metric'), plan.get('target')
    if not (d and m and t):
        return ''
    cur = plan.get('cur') or '现在的水平'
    return '在 %s 之前，把「%s」从 %s 做到「%s」' % (d, m, cur, t)


def _goal_out(row: dict) -> dict:
    plan = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    out = _norm_plan(plan)
    out['id'] = row.get('id')
    out['title'] = row.get('title') or ''
    out['saved_at'] = row.get('created_at') or ''
    out['check'] = _check(out)
    out['progress'] = _progress(out)
    today = _today()
    out['days_left'] = _days_between(today, out['deadline']) if out['deadline'] else None
    out['todo'] = [a['id'] for a in out['actions']
                 if not a['done'] and (not a['on'] or a['on'] <= today)]
    out['behind'] = [m['id'] for m in out['milestones']
                     if m['status'] != 'done' and m['by'] and m['by'] < today]
    return out


# =============================================================
# 监督：今天该做什么 / 进度 / 偏离
# =============================================================
def _progress(plan: dict) -> dict:
    acts = plan.get('actions') or []
    ms = plan.get('milestones') or []
    done_a = [a for a in acts if a.get('done')]
    plan_min = sum(a.get('min') or 0 for a in acts)
    real_min = sum((a.get('actual') or a.get('min') or 0) for a in done_a)
    today = _today()
    return {
        'actions': len(acts),
        'actions_done': len(done_a),
        'milestones': len(ms),
        'milestones_done': len([m for m in ms if m.get('status') == 'done']),
        'plan_min': plan_min,
        'real_min': real_min,
        'overdue': len([a for a in acts if not a.get('done') and a.get('on') and a['on'] < today]),
    }


def _drift(plan: dict) -> dict:
    """把「偏离登记」里的实际投入算成最近的平均值，体检时用它而不是自报数。"""
    rows = [g for g in (plan.get('log') or []) if g.get('kind') == 'drift'][-4:]
    if not rows:
        return {'n': 0, 'avg_hours': None, 'avg_missed': None}
    return {
        'n': len(rows),
        'avg_hours': round(sum(g.get('hours') or 0 for g in rows) / len(rows), 1),
        'avg_missed': round(sum(g.get('missed') or 0 for g in rows) / len(rows), 1),
    }

def _hm(minutes: int) -> str:
    """分钟转成人话：不到一小时就说分钟。"""
    minutes = int(minutes or 0)
    if minutes < 60:
        return '%d 分钟' % minutes
    return ('%.1f' % (minutes / 60.0)).rstrip('0').rstrip('.') + ' 小时'


def _has_digit(text: str) -> bool:
    return bool(re.search(r'\d', text or ''))


def _hit(text: str, words) -> str:
    for w in words:
        if w and w in (text or ''):
            return w
    return ''


def _check(plan: dict) -> dict:
    """六条规则体检。全部是 Python 按规则算的，跟模型没关系 —— 模型只负责把
    这些结论讲成人话，不许它自己加戏。"""
    items = []
    ms = plan.get('milestones') or []
    acts = plan.get('actions') or []
    metric = plan.get('metric') or ''
    cur = plan.get('cur') or ''
    target = plan.get('target') or ''
    deadline = plan.get('deadline') or ''
    today = _today()

    def add(key, level, title, detail, fix=''):
        items.append({'key': key, 'level': level, 'title': title,
                      'detail': detail, 'fix': fix})

    # ① 可验证性 —— 做完能不能点头
    if not metric:
        add('verifiable', 'bad', '没说清用哪个数字验收',
            '整份拆解里没有「指标」。做到没做到，最后只能靠感觉。',
            '写一个能量出来的数：篇数、通过率、体重、收入、次数。')
    if not target:
        add('verifiable', 'bad', '没说清要做到多少',
            '只有方向没有刻度，「更好了」是没法验收的。', '写成「从 X 到 Y」。')
    elif not _has_digit(target):
        add('verifiable', 'warn', '目标值里没有数字',
            '「%s」这种写法，到验收那天两个人能吵起来。' % target, '塞一个数进去。')
    vague = _hit(target, VAGUE_WORDS)
    if vague:
        add('verifiable', 'bad', '目标里有「%s」这类词' % vague,
            '「%s」是方向不是终点。留着它，你永远不知道哪天算做完。' % vague,
            '把「%s」换成一个能被看到的事，或者一个数。' % vague)
    if not cur:
        add('verifiable', 'warn', '没写现在在什么水平',
            '不知道起点，就算到了终点也不知道走了多远，速度更算不出来。', '先量一次现在是多少。')
    if not deadline:
        add('verifiable', 'bad', '没有截止日期',
            '没有日期，这件事会被一直往后推，而且推的时候你自己都不觉得。', '给一个具体的日子。')
    elif deadline < today:
        add('verifiable', 'bad', '截止日期已经过去了',
            '你写的是 %s，今天已经是 %s。' % (deadline, today), '改成一个未来的日子。')

    # ② 时间账 —— 说得下的活，和剩下能做的工，对不对得上
    if not acts:
        add('time', 'bad', '还没有拆出任何动作',
            '目标还是那句目标，今天该干什么完全不知道。',
            '先把第一个里程碑拆成本周能动的手。')
    total_min = sum(a.get('min') or 0 for a in acts)
    hours = plan.get('hours_week') or 0
    if not hours:
        add('time', 'warn', '没填每周能投入多少小时',
            '不填这个，就没法判断拆出来的活排不排得下 —— 而排不下正是放弃的头号原因。',
            '按最近三周的真实情况估，别按理想状态。')
    if total_min and hours and deadline and deadline > today:
        days = _days_between(today, deadline) + 1
        capacity = int(days / 7.0 * hours * 60)
        if total_min > capacity:
            add('time', 'bad', '时间账对不上',
                '现有动作合计 %s；从今天到 %s 只剩 %d 天，按每周 %s 小时算，一共只有约 %s。缺口约 %s。'
                % (_hm(total_min), deadline, days, hours, _hm(capacity), _hm(total_min - capacity)),
                '砍动作、把截止日往后挪、提高每周投入 —— 三选一，别硬扛。')
        elif total_min > capacity * 0.8:
            add('time', 'warn', '时间账卡得太紧',
                '现有动作 %s，可用约 %s，只剩不到两成余量。'
                % (_hm(total_min), _hm(capacity)),
                '留点缓冲：加班、生病、突发事都会吃掉时间。')
    drift = _drift(plan)
    if drift['n'] >= 2:
        if drift['avg_missed'] is not None and drift['avg_missed'] >= 2:
            add('time', 'warn', '实际情况比计划差得远',
                '最近 %d 次偏离登记里，平均每次漏掉 %s 件事。' % (drift['n'], drift['avg_missed']),
                '这不是意志力问题，是这周的活排多了。先砍掉一半再谈坚持。')
        if drift['avg_hours'] is not None and hours and drift['avg_hours'] < hours * 0.6:
            add('time', 'warn', '实际投入只有自报的 %d%%' % int(drift['avg_hours'] * 100 / hours),
                '你填的是每周 %s 小时，但最近 %d 次登记平均只有 %s 小时。'
                % (hours, drift['n'], drift['avg_hours']),
                '把「每周能投入」改成真实值，再按真实值重排一遍。')

    # ③ 粒度 —— 动作是不是小到能动手
    bad_grain = []
    for a in acts:
        t = a.get('text') or ''
        if a.get('min') and a['min'] > ACT_MIN_MAX:
            bad_grain.append((t, '预估 %s，这已经是一件事、不是一个动作，做到一半会停住。' % _hm(a['min'])))
        elif not a.get('min'):
            bad_grain.append((t, '没填预估分钟，排不进任何一天，也就不可能被认真执行。'))
        else:
            w = _hit(t, WISH_STARTS)
            if w:
                bad_grain.append((t, '以「%s」开头，是愿望不是动作 —— 完不成也没痕迹。' % w))
            elif _hit(t, VAGUE_WORDS):
                bad_grain.append((t, '里面有「%s」，做完没人判断得出你到底做没做。' % _hit(t, VAGUE_WORDS)))
            elif not (a.get('out') or ''):
                bad_grain.append((t, '没写「做完能看见什么」。产出看不见，勾不勾得动全靠心情。'))
    for t, why in bad_grain[:3]:
        add('grain', 'warn', '「%s」还不像一个动作' % t[:16], why,
            '动作要满足三条：今天就能开始、有能看见的产出、一小时上下做得完。')

    # ④ 顺序 —— 里程碑和动作的先后对不对
    if not ms:
        add('order', 'bad', '一个里程碑都没有',
            '从目标直接跳到本周动作，中间没有检查点，走偏了要很久才发现。',
            '至少倒推 2–3 站，每站写清「做到什么算过」。')
    elif len(ms) == 1:
        add('order', 'warn', '只有一个里程碑',
            '倒推其实没发生 —— 中间没有可以对照的检查点，落后了你也看不出来。',
            '再往回推一到两站。')
    prev = ''
    for m in ms:
        name = (m.get('name') or '')[:14]
        b = m.get('by') or ''
        if not b:
            add('order', 'warn', '里程碑「%s」没写日期' % name, '没日期，就没法判断这件事是快了还是慢了。', '给个日子。')
            continue
        if not (m.get('done_when') or ''):
            add('order', 'warn', '里程碑「%s」没写验收标准' % name,
                '「做到什么算过」没写，到时候只能凭感觉，感觉通常偏乐观。', '写一句能被人点头的话。')
        if deadline and b > deadline:
            add('order', 'warn', '里程碑「%s」排在截止日之后' % name,
                '它的日期是 %s，比总截止日 %s 还晚。' % (b, deadline), '把日期往前挪。')
        if prev and b < prev:
            add('order', 'warn', '里程碑的日期倒序了',
                '「%s」比上一站的日期还早，顺序和倒推对不上。' % name, '按时间先后重排。')
        prev = b
    first_by = ms[0].get('by') if ms else ''
    late_on = ''
    for a in acts:
        on = a.get('on') or ''
        if not on:
            late_on = late_on or ('miss', a.get('text') or '')
            continue
        if first_by and on > first_by:
            late_on = late_on or ('late', a.get('text') or '', on)
    if late_on:
        if late_on[0] == 'miss':
            add('order', 'warn', '动作「%s」没写哪天做' % late_on[1][:14],
                '没写日子等于没排期，它会被别的急事挤掉，然后你自己都记不起来。', '给个具体的日子。')
        else:
            add('order', 'warn', '动作「%s」排在第一个里程碑之后' % late_on[1][:14],
                '它得赶在 %s 之前完成，但你写的是 %s。' % (first_by, late_on[2]),
                '挪到里程碑之前；挪不动，说明里程碑本身定早了。')

    # ⑤ 可控性 —— 这条是不是真的在你手里
    for a in acts:
        t = a.get('text') or ''
        w = _hit(t, OUTSIDE_WORDS)
        if w:
            add('control', 'warn', '「%s」不全在你手里' % t[:16],
                '里面有「%s」—— 别人不动，这条就永远挂着，然后你会把它算成自己的失败。' % w,
                '换成你能做的那一步：发条消息、催一次、把材料准备好。')
            break
    w = _hit(plan.get('why') or '', SHOULD_WORDS)
    if w:
        add('control', 'warn', '这个目标的动机像是别人的',
            '你的理由里出现了「%s」，说明它更可能是别人的期待。' % w,
            '问自己一句：如果没人知道我在做这件事，我还做吗？')

    # ⑥ 失败预案 —— 掉链子之后怎么办
    if not (plan.get('why') or ''):
        add('fallback', 'warn', '没写为什么要做这个',
            '没有理由的目标，遇到第一个阻力就会被丢掉，而且丢得很自然。',
            '写一句真实的、甚至不太好意思说出口的理由。')
    if not (plan.get('cost') or ''):
        add('fallback', 'warn', '没写愿意为此放弃什么',
            '代价不写出来，说明这个决定还没真的做。', '写一件你打算少做、或者不做的事。')
    if not (plan.get('fallback') or ''):
        add('fallback', 'warn', '没写「做不下去怎么办」',
            '真实情况是一定会有几周掉链子。没有预案，第一次掉链子就直接结束了。',
            '写一句：如果连续几天没动，我第一步先做什么。')

    rank = {'bad': 0, 'warn': 1}
    items.sort(key=lambda x: rank.get(x['level'], 2))
    n_bad = len([x for x in items if x['level'] == 'bad'])
    n_warn = len([x for x in items if x['level'] == 'warn'])
    score = max(0, 100 - 18 * n_bad - 8 * n_warn)
    level = 'good' if score >= 85 else ('warn' if score >= 60 else 'bad')
    return {'score': score, 'level': level, 'items': items,
            'bad': n_bad, 'warn': n_warn, 'checked_at': today}

# =============================================================
# 调模型：只让它问问题、讲人话，不让它替用户写答案
# =============================================================
def _chat(system: str, user_text: str, max_tokens=400, temperature=0.7):
    key = llm_key()
    if not key:
        return None, NO_KEY
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user_text}],
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    try:
        resp = requests.post(llm_endpoint(), json=body, timeout=(10, 90),
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


def _one_q(text: str) -> str:
    """整段只留到第一个问号为止 —— 「一次只问一个问题」这条规矩得有人守。"""
    for i, ch in enumerate(text):
        if ch in '？?':
            return text[:i + 1].strip()
    return text.strip()


def _ask(system: str, user_text: str, single=True, max_tokens=400):
    """模型偶尔吐一串空白（实测过），空就再试一次。"""
    last = ''
    for _ in range(2):
        text, err = _chat(system, user_text, max_tokens=max_tokens)
        text = _clean(text, 600)
        if text:
            return (_one_q(text) if single else text), ''
        last = err or ''
    return None, last


def _extract_json(text):
    if not text:
        return None
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


ROLE = [
    '你在当「目标拆解器」的陪练。你只做一件事：把这个人下一步必须想清楚的东西，变成一个具体的问题问出来。',
    '【规矩】',
    '1. 一次只问一个问题 —— 整段最多出现一个问号，两三句以内。',
    '2. 不给方案、不给建议、不举例、不评价。他想不出来是他自己的事，你的活是把问题问准。',
    '3. 只用他写过的东西问。他没写的就去问他，绝不替他补任何细节。',
    '4. 他让你直接给答案时，温和挡回去，再接着问当前这个问题。',
    '5. 平实、口语。不用「抓手」「闭环」「赋能」「底层逻辑」「颗粒度」这类词。',
    '6. 只输出问题本身。不要开场白，不要编号，不要复述他说过的话，不要问他感受。',
]
STAGE_NAME = {1: '把目标说成一句能验收的话', 2: '为什么是它', 3: '倒推里程碑',
              4: '把第一个里程碑拆到本周', 5: '失败预案'}
STAGE_GOAL = {
    1: '目标得收成一句能验收的话：在什么日子之前，把哪个数从他现在的水平做到多少。'
       '追问方向：用什么数字来量？现在是几？想做到几？什么时候截止？'
       '如果他说的是「提升」「变得更好」这类词，就问他那个词具体指什么、怎么量。',
    2: '问清楚「为什么是这个目标」和「愿意为它付什么代价」。'
       '追问方向：不做会怎样？这是你自己的还是别人的期待？'
       '如果今年只能保住一个目标，还保它吗？为此你打算少做什么？',
    3: '帮他把目标倒推成几站，每一站都要有「做到什么算过」和日期。'
       '追问方向：要到那个终点，倒数第二站该停在哪？每一站凭什么算过关？'
       '这一站和上一站之间，还缺不缺一步？',
    4: '现在只拆第一个里程碑，把它变成这周能动的手。'
       '追问方向：这件事小到能今天开始吗？做完能看见什么？大概要多少分钟？哪天做？'
       '他说出的动作要是还很大，就让他继续往下切。',
    5: '做失败预案。追问方向：最可能死在哪一步？怎么才能提早发现自己在掉链子？'
       '掉链子之后，第一步先做什么而不是直接放弃？',
}
STAGE_Q = {
    1: '这句话到什么时候、用什么数字，能算你真的做到了？',
    2: '如果今年只能保住这一个目标，你还保它吗？为什么？',
    3: '要在截止之前到终点，倒数第二站该停在哪？做到什么算过？',
    4: '这件事小到什么程度算「今天就能开始」？做完你能看见什么？',
    5: '最可能死在哪一步？真掉链子了，你第一步先做什么？',
}


def _plan_brief(plan: dict) -> str:
    """把用户已经填的整理给模型看 —— 只整理他写过的，不加解读。"""
    out = []
    stmt = plan.get('statement') or ''
    if stmt:
        out.append('· 目标：' + stmt)
    elif plan.get('raw'):
        out.append('· 他一开始说的是：' + plan['raw'])
    for key, label in (('why', '为什么做'), ('cost', '愿意付的代价'), ('fallback', '失败预案'),
                       ('note', '他另外备注的')):
        v = plan.get(key)
        if v:
            out.append('· %s：%s' % (label, v))
    if plan.get('hours_week'):
        out.append('· 每周能投入：%s 小时' % plan['hours_week'])
    for m in plan.get('milestones') or []:
        out.append('· 里程碑「%s」：%s 之前完成；做到什么算过：%s'
                   % (m.get('name'), m.get('by') or '没写日期', m.get('done_when') or '没写'))
    for a in plan.get('actions') or []:
        out.append('· 动作「%s」：%s 做；预估 %s 分钟；做完能看见：%s'
                   % (a.get('text'), a.get('on') or '没写哪天',
                      a.get('min') or '?', a.get('out') or '没写'))
    return '\n'.join(out) or '（他什么都还没写）'


def _probe_system(stage: int, check=None) -> str:
    n = max(1, min(STAGES, int(stage or 1)))
    lines = list(ROLE)
    lines += ['', '【这一步要问清楚的是】第 %d 步 · %s' % (n, STAGE_NAME[n]), STAGE_GOAL[n]]
    if n == 5 and check and check.get('items'):
        lines.append('【程序刚算出来的体检结果 —— 别复述，只用来决定你该问什么】')
        for it in check['items'][:6]:
            tag = '严重' if it['level'] == 'bad' else '提醒'
            lines.append('- [%s] %s：%s' % (tag, it['title'], it['detail']))
    return '\n'.join(lines)


def _probe_input(stage: int, plan: dict, msgs: list, text: str) -> str:
    parts = ['【他到目前为止写下的】\n' + _plan_brief(plan)]
    recent = [m for m in msgs if m.get('stage') == stage][-6:]
    if recent:
        parts.append('【这一步刚才的来回】\n' + '\n'.join(
            '%s：%s' % ('你' if m['role'] == 'ai' else '他', m['text']) for m in recent))
    parts.append('【他刚写下的】\n' + (text or '（他什么也没写）'))
    return '\n\n'.join(parts)


def _ask_probe(stage: int, plan: dict, msgs: list, text: str, check):
    return _ask(_probe_system(stage, check), _probe_input(stage, plan, msgs, text))


START_SYS = '\n'.join([
    '你在帮一个人把一句话的目标，整理成「一句能验收的话」。',
    '重要：你只做整理，不做创作。',
    '从他的话里挑出已经明说出来的四样东西：用哪个数字量（metric）、现在是多少（cur）、想做到多少（target）、什么时候截止（deadline）。',
    '他明说了的才填，没说的一律留空字符串。绝对不许替他编数字、编日期、编单位。',
    '然后把「接下来要问他的第一个问题」写进 question —— 只问一个，两三句以内，平实口语。',
    '只输出一个 JSON 对象，不要解释，不要代码块标记：',
    '{"metric":"","cur":"","target":"","deadline":"","question":""}',
    'deadline 只认 YYYY-MM-DD 格式；他要是只说「年底」「下个月」这种，就留空，改成在 question 里问清楚。',
])


def _ask_start(raw: str, plan: dict):
    text, err = _chat(START_SYS, '他说的是：' + raw, max_tokens=400, temperature=0.3)
    data = _extract_json(text or '')
    question = ''
    if isinstance(data, dict):
        question = _clean(data.get('question'), 200)
        for key in ('metric', 'cur', 'target'):
            v = _clean(data.get(key), TEXT_MAX)
            if v:
                plan[key] = v
        d = _clean(data.get('deadline'), 10)
        if DATE_RE.match(d):
            plan['deadline'] = d
        plan['statement'] = _statement(plan)
    return _one_q(question) if question else '', err


REVIEW_SYS = '\n'.join([
    '你在给一份「目标拆解」做体检讲评。',
    '下面列出的每一条，都是程序按规则算出来的问题（不是你的判断）。',
    '你的活：把每一条用大白话讲清楚 —— 它为什么算问题、会有什么后果、最小的改法是什么。',
    '要求：',
    '1. 一条一行，以「· 」开头，按严重程度排，最多讲 5 条。',
    '2. 不夸他，不客套，不重复规则原文，不自己新增问题。',
    '3. 每条两三句，平实口语，不用「抓手」「闭环」「赋能」这类词。',
    '4. 最后另起一行，用一句「现在最该先改的那一件事是：」收尾。',
    '5. 如果一条问题都没有，就不列表，直接说这份拆解现在能开工，以及唯一还需要盯住的地方。',
])


def _review(plan: dict, check: dict):
    lines = ['【这份目标拆解】\n' + _plan_brief(plan), '', '【程序算出来的问题】']
    for it in check['items'][:8]:
        lines.append('- [%s] %s：%s（改法：%s）'
                     % ('严重' if it['level'] == 'bad' else '提醒', it['title'], it['detail'], it['fix']))
    return _ask(REVIEW_SYS, '\n'.join(lines), single=False, max_tokens=900)


def _review_fallback(check: dict) -> str:
    """没配 Key 时的讲评 —— 直接拿规则里已经写好的话拼，不编。"""
    if not check['items']:
        return '规则这一关没有挑出问题。现在缺的不是计划，是今天把第一条动作勾掉。'
    out = []
    for it in check['items'][:5]:
        head = '· ' + it['title'] + '：' + it['detail']
        if it.get('fix'):
            head += ' 改法：' + it['fix']
        out.append(head)
    out.append('现在最该先改的那一件事是：' + check['items'][0]['title'] + '。')
    return '\n'.join(out)

# =============================================================
# 记录：五件套 + 深链（?r=<id>）
# =============================================================
def _title_of(plan: dict) -> str:
    metric, target = plan.get('metric') or '', plan.get('target') or ''
    if metric and target:
        return _clean('%s → %s' % (metric, target), TITLE_MAX)
    if metric:
        return _clean(metric, TITLE_MAX)
    return _clean(plan.get('raw') or '目标', TITLE_MAX) or '目标'


def _norm_msgs(raw) -> list:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[-40:]:
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        if role not in ('ai', 'user'):
            continue
        text = _clean(item.get('text'), WHY_MAX)
        if not text:
            continue
        out.append({'stage': _int(item.get('stage'), 1, STAGES, 1), 'role': role, 'text': text})
    return out[-24:]


def _merge(data: dict, patch: dict) -> dict:
    """把前端传上来的 plan 合并进库里那份：白名单标量 + 两个列表整体替换。"""
    merged = dict(data)
    for key in ('raw', 'metric', 'cur', 'target', 'why', 'cost', 'fallback', 'note'):
        if key in patch:
            merged[key] = patch.get(key)
    for key in ('deadline', 'hours_week', 'stage'):
        if key in patch:
            merged[key] = patch.get(key)
    if isinstance(patch.get('milestones'), list):
        merged['milestones'] = patch['milestones']
    if isinstance(patch.get('actions'), list):
        merged['actions'] = patch['actions']
    log = merged.get('log') if isinstance(merged.get('log'), list) else []
    if isinstance(patch.get('log'), list):
        log = patch['log']
    add = patch.get('log_add')
    if isinstance(add, dict):
        log = log + [add]
    merged['log'] = log
    return merged


def _row_goal(row: dict) -> dict:
    '''库里的一行 -> 前端要的扁平结构。'''
    return _goal_out(row)


def _flat_goal(rid, title, created_at, plan: dict) -> dict:
    '''还没落库 / 刚写完的 plan 也走同一条出口，免得两条路径长得不一样。'''
    return _goal_out({'id': rid, 'title': title, 'created_at': created_at, 'payload': plan})


# ---------------- 页面 ----------------
@router.get('/goal-split', response_class=HTMLResponse)
def goal_split_page() -> HTMLResponse:
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="font-family:system-ui"><h2>模板文件缺失</h2>'
                '<p>请确认 templates/goal-split.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.get('/goal-split/api/status')
def goal_split_status() -> JSONResponse:
    return JSONResponse({'ok': True, 'key': bool(llm_key())})


# ---------------- AI：智能起步 / 追问 / 体检讲评 ----------------
@router.post('/goal-split/api/start')
async def goal_split_start(request: Request) -> JSONResponse:
    payload = await _body(request)
    raw = _clean(payload.get('raw'), RAW_MAX)
    if len(raw) < 4:
        return JSONResponse({'ok': False, 'message': '先把你想做的那件事写一句，几个字也行。'},
                            status_code=400)
    plan = _norm_plan({'raw': raw, 'stage': 1,
                       'deadline': payload.get('deadline'),
                       'hours_week': payload.get('hours_week')})
    question, err = '', ''
    if llm_key():
        question, err = await run_in_threadpool(_ask_start, raw, plan)
    fallback = False
    if not question:
        question = STAGE_Q[1]
        fallback = True
    return JSONResponse({'ok': True, 'plan': plan, 'question': question,
                         'fallback': fallback, 'key': bool(llm_key()),
                         'message': (err or '') if fallback else ''})


@router.post('/goal-split/api/ask')
async def goal_split_ask(request: Request) -> JSONResponse:
    payload = await _body(request)
    raw_plan = payload.get('plan') if isinstance(payload.get('plan'), dict) else {}
    plan = _norm_plan(raw_plan)
    stage = _int(payload.get('stage'), 1, STAGES, plan['stage'])
    if stage > plan['stage']:
        plan['stage'] = stage
    msgs = _norm_msgs(payload.get('msgs'))
    text = _clean(payload.get('text'), WHY_MAX)
    if not llm_key():
        return JSONResponse({'ok': True, 'plan': plan, 'question': STAGE_Q[stage],
                             'fallback': True, 'message': NO_KEY})
    check = _check(plan)
    question, err = await run_in_threadpool(_ask_probe, stage, plan, msgs, text, check)
    if not question:
        return JSONResponse({'ok': True, 'plan': plan, 'question': STAGE_Q[stage],
                             'fallback': True, 'message': err or ''})
    return JSONResponse({'ok': True, 'plan': plan, 'question': question, 'fallback': False})


@router.post('/goal-split/api/review')
async def goal_split_review(request: Request) -> JSONResponse:
    payload = await _body(request)
    plan = _norm_plan(payload.get('plan') if isinstance(payload.get('plan'), dict) else {})
    check = _check(plan)
    verdict = ''
    if llm_key() and check['items']:
        verdict, _err = await run_in_threadpool(_review, plan, check)
    if not verdict:
        verdict = _review_fallback(check)
    return JSONResponse({'ok': True, 'plan': plan, 'check': check, 'verdict': verdict,
                         'key': bool(llm_key())})


# ---------------- 记录：五件套 ----------------
@router.get('/goal-split/api/goals')
def goals_list(request: Request) -> JSONResponse:
    try:
        rows = storage.list_records(TOOL, _user(request), LIST_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': [_row_goal(r) for r in rows]})


@router.post('/goal-split/api/goals')
async def goals_save(request: Request) -> JSONResponse:
    payload = await _body(request)
    raw_plan = payload.get('plan') if isinstance(payload.get('plan'), dict) else {}
    plan = _norm_plan(raw_plan)
    if len(plan['raw']) < 2:
        return JSONResponse({'ok': False, 'message': '这一条还没有「目标」本身，先写一句话。'},
                            status_code=400)
    try:
        rec = storage.add_record(TOOL, _user(request), _title_of(plan), plan)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    return JSONResponse({'ok': True, 'item': _flat_goal(rec['id'], _title_of(plan), rec['created_at'], plan)})


@router.get('/goal-split/api/goals/{rid}')
def goals_one(rid: int, request: Request) -> JSONResponse:
    try:
        row = storage.get_record(_user(request), rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'message': '找不到这条目标。'}, status_code=404)
    return JSONResponse({'ok': True, 'item': _row_goal(row)})


@router.patch('/goal-split/api/goals/{rid}')
async def goals_update(rid: int, request: Request) -> JSONResponse:
    payload = await _body(request)
    patch = payload.get('plan') if isinstance(payload.get('plan'), dict) else None
    if patch is None:
        return JSONResponse({'ok': False, 'message': '没有要改的内容。'}, status_code=400)
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return JSONResponse({'ok': False, 'message': '找不到这条目标。'}, status_code=404)
        data = row['payload'] if isinstance(row['payload'], dict) else {}
        plan = _norm_plan(_merge(data, patch))
        plan['created_at'] = data.get('created_at') or plan['created_at']
        affected = storage.update_record(user, rid, payload=plan, title=_title_of(plan))
        if not affected:
            return JSONResponse({'ok': False, 'message': '这条不属于当前账号。'}, status_code=403)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    return JSONResponse({'ok': True, 'item': _flat_goal(rid, _title_of(plan), row.get('created_at', ''), plan)})


@router.delete('/goal-split/api/goals/{rid}')
def goals_delete(rid: int, request: Request) -> JSONResponse:
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return JSONResponse({'ok': False, 'message': '找不到这条目标。'}, status_code=404)
        affected = storage.delete_record(user, rid)
        if not affected:
            return JSONResponse({'ok': False, 'message': '这条不属于当前账号。'}, status_code=403)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    return JSONResponse({'ok': True})

# ---------- 把对话里他说过的话，整理成这一步的字段 ----------
EXTRACT_KEYS = {
    1: ('metric', 'cur', 'target', 'deadline'),
    2: ('why', 'cost'),
    3: ('milestones',),
    4: ('actions',),
    5: ('fallback',),
}
EXTRACT_SHAPE = {
    1: '{"metric":"","cur":"","target":"","deadline":""}',
    2: '{"why":"","cost":""}',
    3: '{"milestones":[{"name":"","done_when":"","by":"","why":""}]}',
    4: '{"actions":[{"text":"","min":0,"out":"","on":""}]}',
    5: '{"fallback":""}',
}
EXTRACT_HINT = {
    1: 'metric = 用哪个数字来量；cur = 现在是多少；target = 想做到多少；deadline = 截止日期（只认 YYYY-MM-DD）。',
    2: 'why = 他为什么非要做这件事；cost = 他愿意为此少做、不做的那件事。',
    3: 'milestones = 他倒推的那几站，最多 6 条；done_when = 做到什么算过；by = 那一站的日期（YYYY-MM-DD）；why = 为什么是这一站。',
    4: 'actions = 他说的这周要动的手，最多 24 条；min = 预估分钟数（整数，没说他估的就填 0）；out = 做完能看见什么；on = 打算哪天做（YYYY-MM-DD）。',
    5: 'fallback = 掉链子之后他打算先做的那一步。',
}
EXTRACT_ROLE = [
    '你在把一段对话里「他本人说过的话」整理成字段。',
    '【最重要的一条】只抄他说过的。他没说的，一律留空 —— 绝对不许替他编数字、编日期、编动作。',
    '不要美化，不要总结成漂亮话，尽量用他的原词。',
    '只输出一个 JSON 对象，不要解释，不要代码块标记。',
]


def _extract_sys(stage: int) -> str:
    n = max(1, min(STAGES, int(stage or 1)))
    lines = list(EXTRACT_ROLE)
    lines += ['', '【要输出的字段】' + EXTRACT_HINT[n], '【格式】' + EXTRACT_SHAPE[n]]
    return '\n'.join(lines)


def _extract_input(stage: int, plan: dict, msgs: list) -> str:
    parts = ['【他前面已经填下的】\n' + _plan_brief(plan)]
    recent = [m for m in msgs if m.get('stage') == stage][-10:]
    if recent:
        parts.append('【这一步的对话】\n' + '\n'.join(
            '%s：%s' % ('你' if m['role'] == 'ai' else '他', m['text']) for m in recent))
    return '\n\n'.join(parts)


def _ask_extract(stage: int, plan: dict, msgs: list):
    return _ask(_extract_sys(stage), _extract_input(stage, plan, msgs),
                single=False, max_tokens=900)


def _extract_apply(plan: dict, stage: int, data) -> bool:
    """把模型整理出来的东西合并进 plan —— 空值不覆盖已填的，动作按文字保留勾选状态。"""
    if not isinstance(data, dict):
        return False
    n = max(1, min(STAGES, int(stage or 1)))
    changed = False
    if n in (1, 2, 5):
        for key in EXTRACT_KEYS[n]:
            if key == 'deadline':
                d = _clean(data.get(key), 10)
                if DATE_RE.match(d) and plan.get('deadline') != d:
                    plan['deadline'] = d
                    changed = True
                continue
            v = _clean(data.get(key), WHY_MAX if key == 'why' else TEXT_MAX)
            if v and plan.get(key) != v:
                plan[key] = v
                changed = True
    elif n == 3:
        rows = data.get('milestones')
        if isinstance(rows, list) and rows:
            old = {}
            for m in plan.get('milestones') or []:
                old[_clean(m.get('name'), TEXT_MAX)] = m
            out = []
            for i, m in enumerate(rows):
                if not isinstance(m, dict):
                    continue
                name = _clean(m.get('name'), TEXT_MAX)
                if not name:
                    continue
                prev = old.get(name) or {}
                item = {
                    'id': prev.get('id') or ('m%d' % (int(time.time() * 1000) + i)),
                    'name': name,
                    'done_when': _clean(m.get('done_when'), TEXT_MAX) or prev.get('done_when', ''),
                    'by': _date(m.get('by')) or prev.get('by', ''),
                    'why': _clean(m.get('why'), TEXT_MAX) or prev.get('why', ''),
                }
                for k in ('status', 'review', 'reviewed_at'):
                    if k in prev:
                        item[k] = prev[k]
                out.append(item)
            if out:
                plan['milestones'] = out
                changed = True
    elif n == 4:
        rows = data.get('actions')
        if isinstance(rows, list) and rows:
            old = {}
            for a in plan.get('actions') or []:
                old[_clean(a.get('text'), TEXT_MAX)] = a
            out = []
            for i, a in enumerate(rows):
                if not isinstance(a, dict):
                    continue
                text = _clean(a.get('text'), TEXT_MAX)
                if not text:
                    continue
                prev = old.get(text) or {}
                item = {
                    'id': prev.get('id') or ('a%d' % (int(time.time() * 1000) + i)),
                    'text': text,
                    'min': _int(a.get('min'), 0, DAY_MIN, prev.get('min') or 0),
                    'out': _clean(a.get('out'), TEXT_MAX) or prev.get('out', ''),
                    'on': _date(a.get('on')) or prev.get('on', ''),
                    'done': bool(prev.get('done')),
                    'done_at': prev.get('done_at', ''),
                    'actual': prev.get('actual') or 0,
                }
                out.append(item)
            if out:
                plan['actions'] = out
                changed = True
    if changed:
        plan['statement'] = _statement(plan)
    return changed


@router.post('/goal-split/api/extract')
async def goal_split_extract(request: Request) -> JSONResponse:
    payload = await _body(request)
    raw_plan = payload.get('plan') if isinstance(payload.get('plan'), dict) else {}
    plan = _norm_plan(raw_plan)
    stage = _int(payload.get('stage'), 1, STAGES, plan['stage'])
    msgs = _norm_msgs(payload.get('msgs'))
    if not llm_key():
        return JSONResponse({'ok': False, 'plan': plan, 'message': NO_KEY}, status_code=503)
    text, err = await run_in_threadpool(_ask_extract, stage, plan, msgs)
    data = _extract_json(text or '')
    if not isinstance(data, dict):
        return JSONResponse({'ok': False, 'plan': plan,
                             'message': err or '模型这次没整理出东西，再点一次试试。'}, status_code=502)
    changed = _extract_apply(plan, stage, data)
    plan = _norm_plan(plan)
    plan['stage'] = max(plan['stage'], stage)
    return JSONResponse({'ok': True, 'plan': plan, 'changed': changed, 'stage': stage})