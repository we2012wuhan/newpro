# -*- coding: utf-8 -*-
# 第一性原理：把「我认为只能这么做」拆到不可再分的硬约束，再从零算一遍。
# 页面在 /first-principles。
# ------------------------------------------------------------
# 它和隔壁两个工具的区别，不在提示词里，在**产物**上：
#   · 5Why        沿时间往回追  -> 产物是「根因 + 对策」
#   · 苏格拉底    只提问不给答案 -> 产物是「你自己说出来的结论」
#   · 第一性原理  沿结构往里拆  -> 产物是「零件表 + 下限」
# 所以这个工具每一步都必须往那张零件表里落东西；只剩聊天文字，就是退化成第二个 5Why 了。
#
# 五步（服务端定进度，不许跳步）：
#   1 摆目标   你到底认定「只能这么做」的那件事是什么
#   2 拆零件   这件事由什么构成 —— 每条打上类型：事实、价格、惯例、他人期待、我自己的假设
#   3 逐条拷问 只拷问被标成「事实」的那些，按固定清单问真值性问题（量出来的还是估的/谁说的/去掉最坏怎样/有没有人做到过）
#   4 从零重算 只拿剩下的硬约束，算一遍下限，并和当前实际值比
#   5 收尾     服务端拼一张对照表
#
# 两条刻意的设计：
#   · 「这件事没有可拆空间」是一个正常结论，不是失败。拷问完若一条都没被改判、一条都没被剔除，
#     就直接进 5 并明说约束是真的 —— 绝不硬凑一个「颠覆性方案」。这正是这类工具最容易变味的地方。
#   · 进度不交给模型判断。零件分没分类、事实条目拷问完没有，都是代码能算的，算出来比问模型稳。
#
# 数据落在 SQLite 的 history 表（tool = 'first_principles'），一行 = 一次拆解：
#   payload = {goal, items:[{id,text,kind,keep,probe,probed,pending,conclusion,pi}],
#              ask_item, probe_idx,
#              floor, now_value, plan, blocked, report, msgs:[...], started_at, updated_at}
# 零件表是权威数据，msgs 只是过程记录 —— 两者不互相推导（从对话里重解析会随模型输出漂移）。
#
# 「逐条拷问」是轮着来的：一次只问一条。
#   问完 PROBE_Q 那几问只是一轮走完，**不等于问出结论了** ——
#   走完最后一问时把他说过的收拢成一句（conclusion），然后挂起（pending=True），
#   等他点「这条问透了」（probed=True，进下一条）或「再追一轮」（pi 归零，四问重走）。
#   所以四条零件谁都没点头之前，第 3 步不会自己结束 —— 这是刻意的：
#   早先的版本只数轮数，四问问完就自动判「拷问完了」，答得含糊也照样跳下一条，
#   用户会觉得「我这条还在问呢，怎么就跳走了」。
#   · 零件上的 pi        这条问到第几问了 —— 换走时存回来，换回来接着问
#   · 零件上的 pending   四问问完、等他定收不收尾
#   · 零件上的 conclusion 收尾时落下来的那句「这条现在听到的是……」
#   · 会话上的 probe_idx 当前这条问到第几问 —— 旧记录没有 pi，它是唯一的来源，所以留着
#   · msgs 里每条带 item 这条消息说的是哪一条零件 —— 页面上把它显示出来，
#                        多条事实来回问的时候，聊天记录不会串
#
# 回复走 SSE 流式；模型生成的内容边收边推，生成结束才落库。
# 「空词」是边流边替换的：按住末尾几个字，够长了才吐出去，在吐出去之前把
# 本质/范式/认知升级/颠覆 这类词换成人话，这样「用户读到的」和「存进库的」始终是同一段话。
#
# 环境变量：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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

import requests

router = APIRouter()

TOOL = 'first_principles'
V = 1

GOAL_MAX = 200     # 第一步那句话
ITEM_MAX = 120     # 单条零件
NOTE_MAX = 200     # 一条拷问的回答
CONCL_MAX = 240    # 一条拷问收尾时的结论（把他说的收拢成一句）
FLOOR_MAX = 60     # 下限（最少的人 / 时间 / 钱）
PLAN_MAX = 300     # 从零做法
MSG_MAX = 1200     # 单条 AI 消息
REPLY_MAX = 400    # 用户一次发言
HIST_MAX = 24      # 一次塞给模型的最近几条
LIST_MAX = 80      # 右侧历史最多列几条
SCAN_MAX = 400     # 开了搜索时往后翻多少条再筛
QUERY_MAX = 60     # 搜索框那串最长多少字
QUERY_TERMS = 6    # 最多拆成几个关键词
ITEMS_MAX = 24     # 一次拆解最多留多少条零件
MIN_ITEMS = 2      # 少于两条不算拆开
PART_MAX = 12      # 一次最多从模型那里收几条零件

STEPS = ['摆目标', '拆零件', '逐条拷问', '从零重算', '收尾']

# 零件的五个类型。前一个值是存库用的 key，后一个是界面上给人看的名字。
KINDS = [
    ('fact', '事实'),
    ('price', '价格'),
    ('convention', '惯例'),
    ('expectation', '他人期待'),
    ('assumption', '我自己的假设'),
]
KIND_KEYS = [k for k, _ in KINDS]
KIND_NAME = dict(KINDS)

# 拷问清单：只问「这条到底是不是真的」，不问「为什么」。
# 四类角度一定要全覆盖（这是代码算的，不许跳），但**怎么问**交给模型：
# 每一条零件、每一次追问，问题都得长在他的原话上 ——
# 拿同一句话去套所有零件，用户的感觉就是「你根本没在听我说什么」。
PROBE_ANGLES = [
    {'name': '出处',
     'aim': '这个数是哪来的 —— 谁量的、怎么量的、什么时候量的，还是他自己估的',
     'ask': '这个数字是量出来的，还是估的，从哪来的？'},
    {'name': '立场',
     'aim': '这条是谁定的 —— 说这话的人是不是获利的一方、他为什么要这么说',
     'ask': '这条是谁定的，说这话的人有没有立场？'},
    {'name': '代价',
     'aim': '把它去掉最坏会怎样 —— 逼他看见这条到底挡住了什么',
     'ask': '如果把它去掉，最坏会怎样？'},
    {'name': '先例',
     'aim': '有没有人已经做到过 —— 分清是物理上做不到，还是只是没人试过',
     'ask': '有没有人已经做到过，是物理上做不到，还是只是没人试过？'},
]
PROBE_LEN = len(PROBE_ANGLES)
# 兜底用：模型没配 Key、或者这次没吐出问句时，退回这四句
PROBE_Q = [a['ask'] for a in PROBE_ANGLES]

def _pi_of(*vals):
    # 这条零件问到第几个问题了（0 起）。零件上存 pi，会话上存 probe_idx，两个都认。
    for v in vals:
        try:
            return max(0, min(int(v), PROBE_LEN - 1))
        except (TypeError, ValueError):
            continue
    return 0

NO_KEY = ('服务端没配模型 Key（DEEPSEEK_API_KEY）。这个工具靠模型拆零件，'
          '配好之后再来 —— 在项目根目录的 .env 里填上就行。')
# =============================================================
# 1. 护栏（规则层）：空词、不可操作表述、把「原因」误当目标
# =============================================================
SRC_NAME = {
    'five_why': '5Why 分析法',
    'socratic': '苏格拉底提问',
    'first_principles': '第一性原理',
}

# 空词表：模型爱说这些，说了等于没说。边流边换成人话，不留给用户看。
PLAIN_WORDS = [
    ('第一性原理', '从根上重算一遍'),
    ('认知升级', '换个看法'),
    ('底层逻辑', '背后的道理'),
    ('颗粒度', '粗细'),
    ('颠覆性', '换个做法'),
    ('颠覆', '换个做法'),
    ('范式', '做法'),
    ('本质', '说到底'),
    ('赋能', '帮忙'),
    ('闭环', '从头到尾走通'),
    ('对齐', '说清楚'),
    ('抓手', '办法'),
]
_PLAIN_MAX = max(len(k) for k, _ in PLAIN_WORDS)
EMPTY_WORD = re.compile('|'.join(re.escape(k) for k, _ in PLAIN_WORDS))

# 不可操作表述：命中就换一句针对性的追问，而不是把原来那个问题接着问下去。
VAGUE_NUDGE = [
    (re.compile(r'(责任心|不负责|不负责任|不上心|态度不|不重视|忽视)'),
     '换个人来做这一步，他也没那么上心，这件事还会不会发生？如果会，卡住你的就不是态度，'
     '而是哪一步没有校验 —— 哪一步本该拦住它？'),
    (re.compile(r'(沟通不畅|沟通不到位|沟通不够|沟通不足|配合不好|没沟通)'),
     '具体是哪条信息、在哪个环节、没送到谁那里？它本该在哪一步、由谁传出去？'),
    (re.compile(r'(培训不到位|培训不足|意识不足|意识不强|意识薄弱)'),
     '培训完有没有当场验过他会做？没通过会怎样？如果没有任何一步在检查，那缺的是培训，还是缺检查？'),
    (re.compile(r'(执行力差|能力不足|人手不足|人不够|忙不过来)'),
     '是哪一步的工作量超过了当班能做完的量？有没有一个量化的上限？超了会怎样？'),
    (re.compile(r'(不小心|粗心|大意|马虎|麻痹|侥幸)'),
     '是什么让这次疏忽成为可能？哪一步本该拦住它、而没拦住？'),
]
RE_ASSUME = re.compile(r'(我猜|瞎猜|我以为|我觉得吧|凭感觉|拍脑袋|想当然|差不多吧)')
RE_CONV = re.compile(r'(没量过|没查过|没统计|没数过|没有依据|没有根据|说不出|不知道从哪|不清楚哪来|'
                     r'没人说过|没谁说过|大家都|所有人都|同行都|一向都|一直都是|向来都|惯例|'
                     r'行业习惯|规定就这样|都是这么)')

def _vague(text):
    """命中不可操作表述 → 给一句针对性的追问；没命中返回空串。"""
    t = str(text or '')
    for rx, ask in VAGUE_NUDGE:
        if rx.search(t):
            return ask
    return ''

def _demote(text):
    """他在拷问里承认「这条其实没依据」→ 该改成哪一类；没承认返回空串。"""
    t = str(text or '')
    if RE_ASSUME.search(t):
        return 'assumption'
    if RE_CONV.search(t):
        return 'convention'
    return ''

# 答不上来：既没说清从哪来、也没承认没依据 —— 逼他给一个凭据，
# 别拿一句「不知道」把这条蒙过去。和 _vague 分开算：
# 命中 _vague 的（甩锅式表述）要接着追问，命中 _demote 的（承认没依据）已经是结论了，
# 这两种都不走 _stuck，所以它只在两个都没命中时才用得上。
RE_STUCK = re.compile(r'(不知道|不清楚|说不好|说不清|没想过|没考虑过|没细想|记不清|忘了|想不起来|'
                      r'差不多吧|大概吧|应该是吧|可能吧|也许吧|随便|无所谓|没什么特别的)')

def _stuck(text):
    if RE_STUCK.search(str(text or '')):
        return ('先给你能想到的最像的那个答案，哪怕是猜的 —— 猜也要说得出你是根据什么猜的。'
                '真没有，就把它说成「凭感觉，因为……」，把那个「因为」补上。')
    return ''

# 第一步：写成原因不算目标，得改写成一句「我打算做什么」。
CAUSE_GOAL = re.compile(r'(因为|由于|是因为)|(人手|人员|人)不够|没人做|没时间|来不及|太慢|太贵|做不完|忙不过来')
TARGET_HINT = re.compile(r'(我打算|我要|我想|我准备|我决定|我计划|打算把|计划把|想把|试试|改成|换成|拆成|做成)')

def _goal_problem(goal):
    """像原因不像目标时，返回一句让他改写的话；没问题返回空串。"""
    g = str(goal or '')
    if CAUSE_GOAL.search(g) and not TARGET_HINT.search(g):
        return ('这句更像「原因」，不是要拆的那件事。把它改成一句你打算做的事 —— '
                '「我打算把 ____ 做成 ____」，再来拆。')
    return ''
# =============================================================
# 2. 零件：分类猜词、解析模型输出、归一化
# =============================================================
NUM_UNIT = re.compile(r'\d+\s*(个|人|天|小时|分钟|分|秒|米|公里|公斤|吨|元|块|万|次|台|件|箱|页|条|%)')
PRICE_WORD = re.compile(r'(价格|报价|成本|预算|费用|多少钱|便宜|贵|打折|优惠|单价|收费|付费|毛利)')
CONV_WORD = re.compile(r'(惯例|规矩|规定|流程|一直|向来|大家都|都要|默认|必须|以前都|行业|合同这么写|老规矩)')
ASSUME_WORD = re.compile(r'(我觉得|我以为|我猜|估计|大概|可能|应该|担心|怕|说不定|没准|感觉)')
EXP_WORD = re.compile(r'(客户|老板|领导|上司|公司|同事|对方|甲方|人家|家里|家人).{0,8}'
                      r'(要求|希望|觉得|认为|期待|面子|不高兴|不同意|不接受|催)')

def _guess_kind(text):
    """模型没标类型时，按词面猜一个。猜不准宁可算「我自己的假设」——那是最需要被推翻的一类。"""
    t = str(text or '')
    if PRICE_WORD.search(t):
        return 'price'
    if CONV_WORD.search(t):
        return 'convention'
    if ASSUME_WORD.search(t):
        return 'assumption'
    if EXP_WORD.search(t):
        return 'expectation'
    if NUM_UNIT.search(t):
        return 'fact'
    return 'assumption'

KIND_ALIAS = [
    ('price', ('价格', '报价', '钱', '成本', '费用')),
    ('convention', ('惯例', '习惯', '流程', '规定', '行规', '规矩')),
    ('expectation', ('他人期待', '期待', '客户', '别人', '面子')),
    ('assumption', ('假设', '猜测', '我以为', '我认为')),
    ('fact', ('事实', '物理', '算术', '客观', '硬约束', '数字')),
]

def _kind_of_tag(tag):
    t = str(tag or '')
    for key, names in KIND_ALIAS:
        for n in names:
            if n in t:
                return key
    return ''

BULLET = re.compile(r'^\s*(?:[-*·•—–]|\d+\s*[.、)）])\s*')
TAG_RX = re.compile(r'^[\[【(（]\s*([^\]】)）]{1,14})\s*[\]】)）]\s*[:：、]?\s*')
TAG_ANY = re.compile(r'[\[【(（]\s*([^\]】)）]{1,14})\s*[\]】)）]')
# 行内边界：项目符号后面跟着一个 [类型]
BULLET_TAG = re.compile(r'\s*[-·•—–]\s*(?=[\[【(（]\s*[^\]】)）]{1,14}\s*[\]】)）])')

def _split_lines(text):
    """把模型吐的清单切成「一行一条」。

    模型偶尔不老实：它会把好几条用「-」并在同一行
    （例：`- [事实] A - [惯例] B - [他人期待] C`）。
    只按换行切的话，这一串会变成一条、再被长度上限截断 ——
    截断处正好落在句子中间，读起来就是「[他人期待] 别」这种半截话。
    所以行内的项目符号和 [类型] 标记也要当边界切开。
    """
    t = re.sub(r'\r\n?', '\n', str(text or ''))
    t = BULLET_TAG.sub('\n', t)

    def repl(m):
        if not _kind_of_tag(m.group(1)):
            return m.group(0)          # 正文里恰好写了 [备注]，不动它
        before = t[m.start() - 1:m.start()]
        return m.group(0) if before in ('', '\n') else '\n' + m.group(0)

    return TAG_ANY.sub(repl, t).split('\n')

def _parse_items(text):
    """把模型吐的清单解析成 [{text, kind}]。一行一条，容错优先。"""
    out = []
    for raw in _split_lines(text):
        line = BULLET.sub('', str(raw or '').strip()).strip()
        if not line or line.startswith('```'):
            continue
        kind = ''
        m = TAG_RX.match(line)
        if m:
            kind = _kind_of_tag(m.group(1))
            line = line[m.end():].strip()
        line = _swap(line)
        line = line.strip(' \t-"“”*·•')
        if len(line) < 2:
            continue
        out.append({'text': _clean(line, ITEM_MAX), 'kind': kind or _guess_kind(line)})
        if len(out) >= PART_MAX:
            break
    return out

def _split_merged(items):
    """老数据修复：把「好几条被并进同一条」拆回去。

    判据很硬 —— 一条零件的正文里还留着 [类型] 标记，说明它是被并进来的。
    正常的零件一条都不会被动。拆出来的第一条继承原来的类型和拷问记录。
    """
    out = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        text = _clean(it.get('text'), ITEM_MAX)
        if not TAG_ANY.search(text):
            out.append(it)
            continue
        pieces = _parse_items(text)
        if len(pieces) < 2:
            out.append(it)
            continue
        head = dict(it)
        head['text'] = pieces[0]['text']
        head['kind'] = it.get('kind') or pieces[0]['kind']
        out.append(head)
        for p in pieces[1:]:
            out.append({'text': p['text'], 'kind': p['kind']})
    return out

def _norm_items(raw, prev=None):
    """零件表归一化：五分类枚举、非法回落、截断、条数上限、去重、补 id。"""
    old = {}
    for it in (prev if isinstance(prev, list) else []):
        if isinstance(it, dict) and it.get('id'):
            old[str(it.get('id'))] = it
    out, seen, used, n = [], set(), set(), 0
    for it in _split_merged(raw if isinstance(raw, list) else []):
        if not isinstance(it, dict):
            continue
        text = _clean(it.get('text'), ITEM_MAX)
        if len(text) < 2:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        raw_kind = it.get('kind')
        if raw_kind in KIND_KEYS:
            kind = raw_kind
        elif raw_kind == '':
            kind = None                       # 明确「还没选类型」，挡着不让往下走
        else:
            kind = _guess_kind(text)
        oid = _clean(it.get('id'), 12)
        if not oid or oid in used:
            oid = ''
        else:
            used.add(oid)
        o = old.get(oid) or {}
        row = {
            'id': oid,
            'text': text,
            'kind': kind,
            'keep': kind == 'fact',
            'probe': _clean(it.get('probe'), NOTE_MAX) or _clean(o.get('probe'), NOTE_MAX),
            'probed': bool(it.get('probed')) if 'probed' in it else bool(o.get('probed')),
            'pending': bool(it.get('pending')) if 'pending' in it else bool(o.get('pending')),
            'conclusion': _clean(it.get('conclusion'), CONCL_MAX) or _clean(o.get('conclusion'), CONCL_MAX),
            'pi': _pi_of(it.get('pi'), o.get('pi')),
        }
        if row['probed']:
            row['pending'] = False        # 已经收尾的，不再挂「等你确认」
        out.append(row)
        if len(out) >= ITEMS_MAX:
            break
    for r in out:
        if r['id']:
            continue
        while True:
            n += 1
            oid = 'i%d' % n
            if oid not in used:
                break
        used.add(oid)
        r['id'] = oid
    return out
# =============================================================
# 3. 公共小件：清洗、搜索、零件表读数、报告
# =============================================================
def _clean(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]

def _terms(raw) -> list:
    """搜索框那串拆成关键词：空格分隔、转小写、去重、最多 QUERY_TERMS 个。全是「都要命中」。"""
    text = _clean(raw, QUERY_MAX).lower()
    if not text:
        return []
    out = []
    for part in text.split():
        if part and part not in out:
            out.append(part)
        if len(out) >= QUERY_TERMS:
            break
    return out

def _haystack(title, plan) -> str:
    """一条记录里能被搜到的字：标题 + 目标 + 每条零件 + 拷问回答 + 对话 + 报告。"""
    parts = [str(title or ''), str(plan.get('goal') or ''),
             str(plan.get('floor') or ''), str(plan.get('plan') or '')]
    for it in (plan.get('items') or []):
        if isinstance(it, dict):
            parts.append(str(it.get('text') or ''))
            parts.append(str(it.get('probe') or ''))
            parts.append(str(it.get('conclusion') or ''))
    for m in (plan.get('msgs') or []):
        if isinstance(m, dict):
            parts.append(str(m.get('text') or ''))
    return ' '.join(parts).lower()

def _matched(title, plan, terms) -> bool:
    if not terms:
        return True
    hay = _haystack(title, plan)
    for t in terms:
        if t not in hay:
            return False
    return True

def _user(request):
    return getattr(request.state, 'user', '') or ''

async def _body(request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}

def _clamp_step(raw):
    try:
        n = int(raw or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, len(STEPS)))

def _swap(text):
    """把空词换成人话。"""
    out = str(text or '')
    for k, v in PLAIN_WORDS:
        if k in out:
            out = out.replace(k, v)
    return out

def _safe_cut(buf):
    """_plain 用：切点往前挪，保证没有任何空词被切一半。"""
    cut = max(0, len(buf) - (_PLAIN_MAX - 1))
    again = True
    while again and cut > 0:
        again = False
        for i in range(max(0, cut - _PLAIN_MAX), cut):
            for k, _v in PLAIN_WORDS:
                if i + len(k) > cut and buf.startswith(k, i):
                    cut = i
                    again = True
                    break
            if again:
                break
    return cut

def _plain(events):
    """边流边换空词：末尾按住几个字，确认没半个空词再吐。用户读到的就是存进库的那段。"""
    buf = ''
    for ev in events:
        if 'err' in ev:
            if buf:
                yield {'t': _swap(buf)}
                buf = ''
            yield ev
            return
        buf += ev.get('t') or ''
        cut = _safe_cut(buf)
        if cut <= 0:
            continue
        head, buf = buf[:cut], buf[cut:]
        own = _swap(head)
        if own:
            yield {'t': own}
    if buf:
        own = _swap(buf)
        if own:
            yield {'t': own}

def _q_at(text):
    for i, ch in enumerate(text):
        if ch in '？?':
            return i
    return -1

def _one_q(pieces):
    """一条回复只准一个问号，边流边守（照抄苏格拉底那套）。"""
    held = ''
    sealed = False
    for piece, err in pieces:
        if err:
            yield {'err': err}
            return
        if not piece:
            continue
        if sealed:
            held += piece
            if _q_at(piece) >= 0:
                return
            continue
        cut = _q_at(piece)
        if cut < 0:
            yield {'t': piece}
            continue
        yield {'t': piece[:cut + 1]}
        held = piece[cut + 1:]
        sealed = True
        if _q_at(held) >= 0:
            return
    if held:
        yield {'t': held}

def _probe_sys(item_text, idx):
    """这一轮的系统提示：零件 + 落点。问法由模型自己定。"""
    a = PROBE_ANGLES[_pi_of(idx)]
    return PROBE_SYS % (item_text, a['name'], a['aim'])


def _one_q_text(text):
    """非流式路径用：留到第一个问号为止（跟 _one_q 一个规矩）。"""
    t = _swap(str(text or '')).strip()
    cut = _q_at(t)
    return t[:cut + 1].strip() if cut >= 0 else t


_OPEN_NOTE = '（这一条刚开问，他还没说话 —— 别写「你说得对」这种回应，直接问。）'


def _ask_question(item, idx, tries=1):
    """开场 / 换条 / 重来时的第一个问题：让模型照着这条零件写。

    没配 Key、或者这次没吐出问句，就退回兜底那句 —— 工具不能因此卡住。
    """
    a = PROBE_ANGLES[_pi_of(idx)]
    out, _err = _chat_text(_probe_sys(_clean(item.get('text'), ITEM_MAX), idx),
                           [{'role': 'user', 'content': _OPEN_NOTE}], tries=tries)
    q = _one_q_text(out)
    if not q or _q_at(q) < 0 or len(q) > 90:
        return a['ask']
    return q


def _pieces(text, size=16):
    """把一段固定文字切成小块推出去，前端看起来也像「在说话」。"""
    t = _swap(str(text or ''))
    for i in range(0, len(t), size):
        yield {'t': t[i:i + size]}

def _sse(obj):
    return 'data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n'

def _num(value):
    m = re.search(r'-?\d+(?:\.\d+)?', str(value or ''))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None

def _diff(plan):
    """下限和当前值都能读出一个数才算差额，读不出来就不硬算。"""
    a, b = _num(plan.get('floor')), _num(plan.get('now_value'))
    if a is None or b is None:
        return None
    return b - a

def _fact_items(plan):
    return [it for it in (plan.get('items') or [])
            if isinstance(it, dict) and it.get('kind') == 'fact']

def _pending_facts(plan):
    """还没收尾的事实 —— 含「四个问题问完、等他确认」的那些。
    有它在，第 3 步就不会自己往下跳。"""
    return [it for it in _fact_items(plan) if not it.get('probed')]

def _open_facts(plan):
    """还能接着往下问的事实 —— 不含「问完了、等他确认」的那些。"""
    return [it for it in _pending_facts(plan) if not it.get('pending')]

def _find_item(plan, iid):
    for it in (plan.get('items') or []):
        if isinstance(it, dict) and str(it.get('id')) == str(iid):
            return it
    return None

def _hard_lines(plan):
    rows = _fact_items(plan)
    if not rows:
        return '（一条都没剩下 —— 原来的每一条都能被推翻，等于从零开始。）'
    return '\n'.join('- ' + _clean(it.get('text'), ITEM_MAX) for it in rows[:ITEMS_MAX])
def _cell(text, limit=ITEM_MAX):
    t = _clean(text, limit).replace('|', '\\|')
    return t or '—'

def _report(plan):
    """收尾对照表由 Python 拼，不交给模型 —— 它一写就会忍不住给建议。"""
    goal = _clean(plan.get('goal'), GOAL_MAX) or '（没写目标）'
    items = [it for it in (plan.get('items') or []) if isinstance(it, dict)]
    out = ['# 第一性原理拆解：' + goal, '']
    if plan.get('blocked'):
        out += ['## 结论：这件事没有可拆解的空间', '',
                '拆出来的 %d 条零件，一条都没能在拷问里被推翻 —— 它们确实是真的硬约束。' % len(items),
                '所以「只能这么做」在这里是成立的，不是借口。',
                '这个工具不会为了显得有用而硬凑一个方案。', '']
    out += ['## 零件表', '', '| # | 零件 | 类型 | 拷问结论 |', '| --- | --- | --- | --- |']
    for i, it in enumerate(items, 1):
        out.append('| %d | %s | %s | %s |' % (i, _cell(it.get('text')),
                   _cell(KIND_NAME.get(it.get('kind'), '未分类'), 20),
                   _cell(it.get('conclusion') or it.get('probe'), NOTE_MAX)))
    out.append('')
    hard = [it for it in items if it.get('kind') == 'fact']
    out += ['## 保留下来的硬约束（%d 条）' % len(hard), '']
    if hard:
        for it in hard:
            out.append('- ' + _clean(it.get('text'), ITEM_MAX))
    else:
        out.append('- 一条都没剩下 —— 原来的约束全部可以被推翻。')
    out.append('')
    if not plan.get('blocked') and (plan.get('floor') or plan.get('now_value') or plan.get('plan')):
        out += ['## 从零重算', '',
                '- 下限（最少的人 / 时间 / 钱）：' + (_clean(plan.get('floor'), FLOOR_MAX) or '—'),
                '- 当前实际值：' + (_clean(plan.get('now_value'), FLOOR_MAX) or '—')]
        d = _diff(plan)
        if d is not None:
            out.append('- 差额：%g' % d)
        out += ['', '### 从零开始的做法', '', _clean(plan.get('plan'), PLAN_MAX) or '—', '']
    return '\n'.join(out)

def _items_out(plan):
    raw = [it for it in (plan.get('items') or []) if isinstance(it, dict)]
    # 旧记录里可能塞着「一条正文里并了好几条」，读的时候顺手按 [类型] 拆开
    merged = _split_merged(raw)
    rows = raw if len(merged) == len(raw) else _norm_items(merged, raw)
    out = []
    for it in rows:
        kind = it.get('kind') if it.get('kind') in KIND_KEYS else ''
        out.append({
            'id': _clean(it.get('id'), 12),
            'text': _clean(it.get('text'), ITEM_MAX),
            'kind': kind,
            'keep': kind == 'fact',
            'probe': _clean(it.get('probe'), NOTE_MAX),
            'probed': bool(it.get('probed')),
            'pending': bool(it.get('pending')) and not bool(it.get('probed')),
            'conclusion': _clean(it.get('conclusion'), CONCL_MAX),
            'pi': _pi_of(it.get('pi')),
        })
    return out

def _msgs_out(msgs):
    out = []
    for m in (msgs or []):
        if not isinstance(m, dict):
            continue
        text = _clean(m.get('text'), MSG_MAX)
        if not text:
            continue
        out.append({'role': 'ai' if m.get('role') == 'ai' else 'me', 'text': text,
                    'at': _clean(m.get('at'), 20),
                    'item': _clean(m.get('item'), 12),
                    'step': m.get('step') if isinstance(m.get('step'), int) else 0})
    return out

def _src_out(plan):
    s = plan.get('src')
    if not isinstance(s, dict):
        return None
    frm = _clean(s.get('from'), 30)
    if frm not in SRC_NAME:
        return None
    return {'from': frm, 'name': SRC_NAME[frm], 'id': _clean(s.get('id'), 12)}

def _pack(rid, title, plan, full=True):
    msgs = plan.get('msgs') if isinstance(plan.get('msgs'), list) else []
    out = {
        'id': rid,
        'title': title or '',
        'goal': _clean(plan.get('goal'), GOAL_MAX),
        'step': _clamp_step(plan.get('step')),
        'blocked': bool(plan.get('blocked')),
        'floor': _clean(plan.get('floor'), FLOOR_MAX),
        'now_value': _clean(plan.get('now_value'), FLOOR_MAX),
        'diff': _diff(plan),
        'plan': _clean(plan.get('plan'), PLAN_MAX),
        'items': _items_out(plan),
        'src': _src_out(plan),
        'count': len(msgs),
        'started_at': plan.get('started_at') or '',
        'updated_at': plan.get('updated_at') or '',
    }
    if full:
        out['msgs'] = _msgs_out(msgs)
        out['ask_item'] = _clean(plan.get('ask_item'), 12)
        out['ask_pi'] = _pi_of(plan.get('probe_idx'))
        out['report'] = _report(plan)
    return out

def _row(row, full=True):
    plan = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    return _pack(row.get('id'), row.get('title'), plan, full)

def _is_v2(plan):
    return isinstance(plan, dict) and plan.get('v') == V
# =============================================================
# 4. 提示词与模型调用（模型层护栏：固化五类定义、禁止自己给方案）
# =============================================================
ITEM_SYS = '\n'.join([
    '你在帮一个人做「第一性原理拆解」。他说出一件事，并认为「只能这么做」。',
    '你的活是：把这件事拆成零件，并给每一条打上类型。',
    '',
    '五类，每条只能归一类：',
    '· 事实 —— 物理规律、算术、客观的数字 / 容量 / 时间（例：一车最多装 800 箱）',
    '· 价格 —— 钱、成本、报价、预算、便宜贵（例：换供应商要多花 3000）',
    '· 惯例 —— 一直这么做、行业都这么干、流程这么规定（例：必须走顺丰）',
    '· 他人期待 —— 某个具体的人在等你怎样、你怕谁不高兴'
    '（例：老板要我随叫随到、客户要求三天内送到）',
    '· 我自己的假设 —— 我觉得、我以为、我猜、担心（例：涨价客户肯定不接受）',
    '',
    '规矩：',
    '1. 每条一行，格式：- [类型] 一句话，不超过 25 字。至少要 %d 条，最多 %d 条。' % (MIN_ITEMS, PART_MAX),
    '2. 每条必须单独占一行 —— 不许把好几条用「-」连在同一行里。',
    '3. 每条都要主谓完整，单独拿出来也读得懂（谁 要 谁 做什么）。'
    '不许只写半句，不许用「别」「等等」「之类」收尾。',
    '4. 只拆他说的这件事，不做原因分析，不给建议，不给方案。',
    '5. 一条就是一个零件，不要用「因为…所以…」把几条串起来。',
    '6. 「大家都这么干」「行业都这样」算惯例，不算他人期待；'
    '他人期待得落到某个具体的人身上。',
    '7. 不许用「本质」「范式」「认知升级」「颠覆」这类词。',
    '8. 直接给清单，不要开场白、不要总结、不要解释。',
])
ITEM_ASK = '这件事是：%s'
ITEM_RETRY = '太少了，再补几条。每条一行，一共至少 %d 条。' % MIN_ITEMS

# 给模型的只有「这一轮的落点」，剩下的用他的话问出来。
PROBE_SYS = '\n'.join([
    '你在帮一个人做「第一性原理拆解」，现在到「逐条拷问」这一步。',
    '他认定这件事只能这么做，理由里有一条被他当成「事实」：%s',
    '',
    '这一轮要逼他答的落点是【%s】：%s',
    '',
    '你只做两件事：',
    '1. 先用一句话接住他刚说的（不评价、不表扬、不给建议、不下结论）；',
    '2. 再问一个问题，只问上面这个落点。',
    '',
    '问题怎么问是最要紧的：',
    '· 必须长在他说过的话上 —— 把他原话里的数字、名词、时间、人名抄进去，',
    '  让他一眼看出你在问「他这一条」，而不是在念一句谁都能用的通用问题；',
    '· 他答得含糊、答非所问，就照着他的原话追漏洞；答得实在，就往深一层要细节；',
    '· 上面已经问过同一个落点的，换个切入点再问，别把上一轮那句原话又说一遍；',
    '· 不许把落点本身当句子念出来，也不许出现「一般来说」「据说」这种和这条零件无关的套话；',
    '',
    '参照（左边是他的零件，右边是问法 —— 问法要跟着零件变）：',
    '· 「一车最多装 800 箱」→「800 箱是按你们那种车实测出来的，还是照体积估的？」',
    '· 「顺丰要求 24 小时内出库」→「24 小时这条是顺丰合同里写死的，还是仓库自己怕超时定的？」',
    '· 「客户不接受涨价」→「这个客户跟你明说过不接受，还是你觉得他接受不了？」',
    '反面例子（这种等于没问，一律不许）：「这个数字是量出来的，还是估的？」',
    '',
    '规矩：全篇不超过 70 字；只许有这一个问号；不要开场白、不要总结；',
    '不许给答案、不许给方案、不许说「你应该」。',
])

# 一条问完四个问题之后，不再自动跳下一条：先把「听到的」收拢成一句，交给他自己定收没收。
RECAP_SYS = '\n'.join([
    '你在帮一个人做「第一性原理拆解」，现在到「逐条拷问」这一步。',
    '你正在拷问这一条被标成「事实」的零件：%s',
    '',
    '四个问题都问完了，他的回答都在上面。',
    '你只做一件事：把他自己说过的话收拢成一句「这条现在听到的是：……」',
    '',
    '规矩：',
    '1. 只许用他给的字和数字，不许补他没说的，不许评价对错，不许给建议、方案或结论；',
    '2. 不许出现「应该」「建议」「其实」「说明」「可见」这类词；',
    '3. 收拢完另起一行，只问一句：这条问透了吗？',
    '4. 全篇不超过 90 字，只许有这一个问号，不要开场白、不要总结。',
])

WAIT_CLOSE = ('「%s」这条四个问题已经问完了，收没收尾你说了算。\n\n'
              '在上面点一下：「这条问透了」我就接着问下一条；'
              '觉得没问透，就点「再追一轮」，这四个问题再走一遍。')

RECOMPUTE_SYS = '\n'.join([
    '你在帮一个人做「第一性原理拆解」，现在到「从零重算」这一步。',
    '前面已经把不少零件判定为「其实没那么硬」。剩下的真硬约束是：',
    '%s',
    '',
    '你只做一件事：用提问帮他自己算出「这件事最少需要多少」（人 / 时间 / 钱），',
    '以及「如果今天从零开始做，他会怎么做」。',
    '一次只问一个问题，问得具体；不给答案、不给方案、不替他算。',
    '如果他要你直接给做法，就回他：这个数只能你自己报，我报了不算你的。然后接着问。',
])

def _chat(system, messages, max_tokens=900, temperature=0.7):
    key = llm_key()
    if not key:
        return None, NO_KEY
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system}] + list(messages),
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

def _chat_text(system, messages, tries=2, **kw):
    """拿一段能用的文本。模型偶尔吐一串空白，空就再试一次。"""
    last = ''
    for _ in range(max(1, tries)):
        text, err = _chat(system, messages, **kw)
        text = _clean(text, MSG_MAX)
        if text:
            return text, ''
        last = err or ''
    return None, last

def _chat_stream(system, messages, max_tokens=400, temperature=0.7):
    """流式：逐块 yield (片段, 错误)。错误一定排在最后。"""
    key = llm_key()
    if not key:
        yield '', NO_KEY
        return
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system}] + list(messages),
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': True,
    }
    resp = None
    try:
        resp = requests.post(llm_endpoint(), json=body, timeout=(10, 120),
                             headers={'Authorization': 'Bearer ' + key,
                                      'Content-Type': 'application/json'}, stream=True)
        if resp.status_code >= 400:
            yield '', '模型返回错误 ' + str(resp.status_code)
            return
        for raw in resp.iter_lines(decode_unicode=False):
            if not raw or not raw.startswith(b'data:'):
                continue
            data = raw[5:].strip()
            if not data:
                continue
            if data == b'[DONE]':
                break
            try:
                obj = json.loads(data.decode('utf-8'))
                piece = obj['choices'][0]['delta'].get('content') or ''
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                continue
            if piece:
                yield piece, ''
    except requests.RequestException as exc:
        yield '', '模型连接断了：' + str(exc)[:80]
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

def _convo(plan, text):
    """把存下来的对话拼成给模型看的消息列表，返回 (消息列表, 原始 msgs)。"""
    out = [{'role': 'user', 'content': '这件事是：' + _clean(plan.get('goal'), GOAL_MAX)}]
    msgs = [m for m in (plan.get('msgs') or []) if isinstance(m, dict)]
    for m in msgs[-HIST_MAX:]:
        t = _clean(m.get('text'), MSG_MAX)
        if not t:
            continue
        out.append({'role': 'assistant' if m.get('role') == 'ai' else 'user', 'content': t})
    if text:
        out.append({'role': 'user', 'content': text})
    return out, list(msgs)

def _handoff(plan, cur):
    # 上一条问完、换到下一条时明确说一声 —— 不然用户看不出来问题换对象了。
    prev = ''
    for m in reversed(plan.get('msgs') or []):
        if isinstance(m, dict) and m.get('item'):
            prev = str(m.get('item'))
            break
    if not prev or prev == str(cur.get('id')):
        return ''
    old = ''
    for it in (plan.get('items') or []):
        if isinstance(it, dict) and str(it.get('id')) == prev:
            old = _clean(it.get('text'), ITEM_MAX)
    return '（%s这条问完了，接着看下一条。）\n\n' % (('「%s」' % old) if old else '上一条')
# =============================================================
# 5. 阶段机（服务端定进度，不许跳步）
# =============================================================
def _enter_probe(plan):
    """从「拆零件」进「逐条拷问」：记下进门时的事实条数，问「有没有可拆空间」就靠它。"""
    facts = _fact_items(plan)
    plan['step'] = 3
    plan['fact_total'] = len(facts)
    plan['changed'] = bool(plan.get('changed'))
    plan['probe_idx'] = 0
    plan['nudged'] = False
    plan['ask_item'] = facts[0]['id'] if facts else ''
    return facts[0] if facts else None

def _maybe_finish_probe(plan):
    """事实全收尾了：要么进第 4 步，要么给「没有可拆空间」的诚实结论。返回一句附加说明。

    注意：_pending_facts 把「问完四问、等他确认」的那些也算在内，
    所以他自己没点「这条问透了」之前，这一步不会自己往下跳。
    """
    if _pending_facts(plan):
        return ''
    total = int(plan.get('fact_total') or 0)
    if total and not plan.get('changed'):
        plan['blocked'] = True
        plan['step'] = 5
        plan['ask_item'] = ''
        return '（这些条目一条都没被推翻 —— 按这个工具的规矩，我不给你凑方案，直接出结论。）'
    plan['blocked'] = False
    plan['step'] = 4
    plan['ask_item'] = ''
    return '（硬约束都问过一遍了。下面进第 4 步：只拿剩下的硬约束，从零算一遍。）'

def _save(user, sid, plan, title=None):
    plan['updated_at'] = storage.now_str()
    try:
        storage.update_record(user, sid, payload=plan, title=title)
    except storage.StorageUnavailable as exc:
        return str(exc)
    return ''

# =============================================================
# 6. 建会话：第一步「摆目标」→ 第二步「拆零件」
# =============================================================
def _do_create(goal, user, src=None):
    if not llm_key():
        return JSONResponse({'ok': False, 'message': NO_KEY}, status_code=503)
    text, err = _chat_text(ITEM_SYS, [{'role': 'user', 'content': ITEM_ASK % goal}], tries=2)
    raw = _parse_items(text or '')
    if len(raw) < MIN_ITEMS:
        more, _e = _chat_text(ITEM_SYS, [
            {'role': 'user', 'content': ITEM_ASK % goal},
            {'role': 'assistant', 'content': text or ''},
            {'role': 'user', 'content': ITEM_RETRY},
        ], tries=1)
        raw2 = _parse_items(more or '')
        if len(raw2) > len(raw):
            raw = raw2
    if not raw:
        return JSONResponse({'ok': False, 'message': (err or '模型这次没拆出零件来，再点一次试试。')},
                            status_code=502)
    items = _norm_items(raw)
    now = storage.now_str()
    lines = ['把「%s」拆成 %d 条零件：' % (goal, len(items)), '']
    for it in items:
        lines.append('· %s —— %s' % (it['text'], KIND_NAME.get(it['kind'], '未分类')))
    lines += ['', '逐条看一眼类型对不对：不对的点一下改过来，改完点「分类完成，开始拷问」。']
    plan = {
        'v': V,
        'goal': goal,
        'step': 2,
        'items': items,
        'ask_item': '',
        'probe_idx': 0,
        'nudged': False,
        'fact_total': 0,
        'changed': False,
        'floor': '',
        'now_value': '',
        'plan': '',
        'blocked': False,
        'report': '',
        'msgs': [{'role': 'ai', 'text': '\n'.join(lines), 'at': now, 'step': 2}],
        'started_at': now,
        'updated_at': now,
    }
    if src:
        plan['src'] = src
    try:
        rec = storage.add_record(TOOL, user, goal[:60], plan)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    return JSONResponse({'ok': True, 'session': _pack(rec['id'], goal[:60], plan)})
# =============================================================
# 7. SSE 流式回复：第 3 步逐条拷问 / 第 4 步从零重算
# =============================================================
def _stream_probe(sid, text, user, plan):
    items = _norm_items(plan.get('items'), plan.get('items'))
    plan['items'] = items
    cur = None
    for it in items:
        if it.get('id') == plan.get('ask_item') and it.get('kind') == 'fact' and not it.get('probed'):
            cur = it
            break
    if cur is None:
        pend = _pending_facts(plan)
        cur = pend[0] if pend else None

    sent, err, tail = [], '', ''

    if cur is not None and cur.get('pending'):
        # 四个问题都问完了，他还没点「问透了 / 再追一轮」—— 这里不再自己往下问
        plan['ask_item'] = cur['id']
        for ev in _pieces(WAIT_CLOSE % _clean(cur.get('text'), ITEM_MAX)):
            sent.append(ev['t'])
            yield _sse(ev)
    elif cur is None:
        tail = _maybe_finish_probe(plan)
        if not tail:
            yield _sse({'err': '这一步的拷问已经做完了。'})
            return
        for ev in _pieces('这一步的拷问已经做完了。' + tail):
            sent.append(ev['t'])
            yield _sse(ev)
    else:
        plan['ask_item'] = cur['id']
        idx = _pi_of(plan.get('probe_idx'), cur.get('pi'))
        demote = _demote(text)
        nudge = _vague(text) if not plan.get('nudged') else ''
        if not nudge and not demote and not plan.get('nudged'):
            nudge = _stuck(text)          # 答不上来：也拦一下，别拿「不知道」蒙过去

        if nudge:
            # 命中不可操作表述：换成一句针对性的追问，这一轮不推进
            plan['nudged'] = True
            for ev in _pieces(nudge):
                sent.append(ev['t'])
                yield _sse(ev)
        else:
            for ev in _pieces(_handoff(plan, cur), 30):
                sent.append(ev['t'])
                yield _sse(ev)
            convo, _msgs = _convo(plan, text)
            # 这一条只剩最后一问了：不问下一问，改成把他说的收拢成一句，交给他自己定
            last_q = idx >= PROBE_LEN - 1
            # idx 是「他正在答的那一问」：开场问的是第 0 问，所以这里要问下一问（idx+1）。
            # 这里以前用的是 idx，等于把刚答完的那一问再问一遍 —— 第一问会被连着问两次，
            # 最后一问（先例）反而永远轮不到。
            sys_p = RECAP_SYS % _clean(cur.get('text'), ITEM_MAX) if last_q else \
                _probe_sys(_clean(cur.get('text'), ITEM_MAX), idx + 1)
            said = []
            for ev in _plain(_one_q(_chat_stream(sys_p, convo, max_tokens=300))):
                if 'err' in ev:
                    err = ev['err']
                    break
                said.append(ev['t'])
                sent.append(ev['t'])
                yield _sse({'t': ev['t']})
            if not said:
                yield _sse({'err': err or '模型这次没说出话来，再发一次试试。'})
                return
            plan['nudged'] = False
            cur['probe'] = _clean((cur.get('probe') + ' ' if cur.get('probe') else '') + text, NOTE_MAX)
            demoted_now = bool(demote and cur.get('kind') == 'fact')
            if last_q and not demoted_now:
                # 「问完了」不等于「问出结论了」—— 挂起，等他自己点收尾
                cur['pending'] = True
                plan['probe_idx'] = PROBE_LEN - 1
                cur['pi'] = PROBE_LEN - 1
                ctext = _clean(''.join(said), CONCL_MAX)
                if not ctext:
                    ctext = '模型这次没说出话来。先把你自己答的留着：%s' % _clean(cur.get('probe'), NOTE_MAX)
                cur['conclusion'] = ctext
            else:
                nxt = min(idx + 1, PROBE_LEN - 1)
                plan['probe_idx'] = nxt
                cur['pi'] = nxt
            if demote and cur.get('kind') == 'fact':
                cur['kind'] = demote
                cur['keep'] = False
                plan['changed'] = True
                note = '\n\n（这条按你说的，从「事实」改成「%s」，不再当硬约束。）' % KIND_NAME[demote]
                for ev in _pieces(note, 30):
                    sent.append(ev['t'])
                    yield _sse(ev)
            pend = _pending_facts(plan)
            nxt_item = None
            for x in pend:
                if str(x.get('id')) == str(cur.get('id')):
                    nxt_item = x          # 这一条还没问完，接着问它
                    break
            if nxt_item is None:
                opens = _open_facts(plan)
                nxt_item = opens[0] if opens else (pend[0] if pend else None)
            if nxt_item is not None and str(nxt_item.get('id')) != str(cur.get('id')):
                plan['probe_idx'] = _pi_of(nxt_item.get('pi'))
            plan['ask_item'] = nxt_item['id'] if nxt_item else ''
            if not pend:
                tail = _maybe_finish_probe(plan)
                if tail:
                    for ev in _pieces('\n\n' + tail, 30):
                        sent.append(ev['t'])
                        yield _sse(ev)

    msg = ''.join(sent)
    if not msg:
        yield _sse({'err': err or '模型这次没说出话来，再发一次试试。'})
        return
    now = storage.now_str()
    msgs = list(plan.get('msgs') or [])
    which = _clean(cur.get('id'), 12) if cur else ''
    msgs.append({'role': 'me', 'text': text, 'at': now, 'step': 3, 'item': which})
    msgs.append({'role': 'ai', 'text': msg, 'at': now, 'step': plan.get('step'), 'item': which})
    plan['msgs'] = msgs
    bad = _save(user, sid, plan)
    if bad:
        yield _sse({'err': bad})
        return
    if err:
        yield _sse({'cut': '模型中途断了，这次只说出半段：' + err})
    yield _sse({'done': 1, 'id': sid})

def _stream_recompute(sid, text, user, plan):
    sent, err = [], ''
    nudge = _vague(text)
    if nudge:
        for ev in _pieces(nudge):
            sent.append(ev['t'])
            yield _sse(ev)
    else:
        convo, _msgs = _convo(plan, text)
        sys_p = RECOMPUTE_SYS % _hard_lines(plan)
        for ev in _plain(_one_q(_chat_stream(sys_p, convo, max_tokens=400))):
            if 'err' in ev:
                err = ev['err']
                break
            sent.append(ev['t'])
            yield _sse({'t': ev['t']})
    msg = ''.join(sent)
    if not msg:
        yield _sse({'err': err or '模型这次没说出话来，再发一次试试。'})
        return
    now = storage.now_str()
    msgs = list(plan.get('msgs') or [])
    msgs.append({'role': 'me', 'text': text, 'at': now, 'step': 4})
    msgs.append({'role': 'ai', 'text': msg, 'at': now, 'step': 4})
    plan['msgs'] = msgs
    bad = _save(user, sid, plan)
    if bad:
        yield _sse({'err': bad})
        return
    if err:
        yield _sse({'cut': '模型中途断了，这次只说出半段：' + err})
    yield _sse({'done': 1, 'id': sid})

def _stream_reply(sid, text, user, row):
    plan = dict(row.get('payload') or {})
    step = _clamp_step(plan.get('step'))
    if step == 3:
        for ev in _stream_probe(sid, text, user, plan):
            yield ev
    elif step == 4:
        for ev in _stream_recompute(sid, text, user, plan):
            yield ev
    else:
        yield _sse({'err': '现在这一步不用对话 —— 按上面的按钮往下走。'})
# =============================================================
# 8. 改名 / 改零件 / 进拷问 / 收尾
# =============================================================
def _norm_src(payload):
    """互送来源：只认三个工具名，且不能是自己。"""
    frm = _clean((payload or {}).get('from'), 30)
    if frm not in SRC_NAME or frm == TOOL:
        return None
    src = {'from': frm}
    rid = _clean((payload or {}).get('src'), 12)
    if rid:
        src['id'] = rid
    return src

def _do_patch(sid, payload, user, row):
    plan = dict(row.get('payload') or {})
    title = row.get('title')
    act = _clean(payload.get('act'), 20)
    now = storage.now_str()

    if 'title' in payload:
        title = _clean(payload.get('title'), 60) or title
    if 'items' in payload:
        old = [it for it in (plan.get('items') or []) if isinstance(it, dict)]
        oldmap = {str(it.get('id')): it.get('kind') for it in old}
        new = _norm_items(payload.get('items'), old)
        if _clamp_step(plan.get('step')) >= 3:
            for it in new:
                if it.get('kind') == 'fact' and oldmap.get(it.get('id')) != 'fact':
                    it['probed'] = False          # 新变成硬约束的，得重新拷问
                    it['pending'] = False
                    it['conclusion'] = ''
                    it['probe'] = ''
                if oldmap.get(it.get('id')) == 'fact' and it.get('kind') != 'fact':
                    plan['changed'] = True        # 改判掉了一条
            newids = set(it.get('id') for it in new)
            for it in old:
                if it.get('kind') == 'fact' and str(it.get('id')) not in newids:
                    plan['changed'] = True        # 直接删掉了一条硬约束
        plan['items'] = new
    if 'floor' in payload:
        plan['floor'] = _clean(payload.get('floor'), FLOOR_MAX)
    if 'now_value' in payload:
        plan['now_value'] = _clean(payload.get('now_value'), FLOOR_MAX)
    if 'plan_text' in payload:
        plan['plan'] = _clean(payload.get('plan_text'), PLAN_MAX)

    step = _clamp_step(plan.get('step'))
    if act == 'probe':
        if step != 2:
            return JSONResponse({'ok': False, 'message': '现在不在「拆零件」这一步。'}, status_code=409)
        items = plan.get('items') or []
        if len(items) < MIN_ITEMS:
            return JSONResponse({'ok': False,
                                 'message': '至少要有 %d 条零件才算拆开。' % MIN_ITEMS}, status_code=400)
        if any(not it.get('kind') for it in items):
            return JSONResponse({'ok': False, 'message': '还有零件没选类型，全都选好再往下走。'}, status_code=400)
        first = _enter_probe(plan)
        plan['msgs'] = list(plan.get('msgs') or [])
        if first is None:
            plan['step'] = 4
            plan['msgs'].append({'role': 'ai', 'at': now, 'step': 4, 'text':
                                 '拆出来的零件里没有一条是「事实」—— 全是价格、惯例、他人期待和你自己的假设。'
                                 '\n\n把这些全放下之后，这件事最少需要多少？进第 4 步，从零算一遍。'})
        else:
            plan['msgs'].append({'role': 'ai', 'at': now, 'step': 3,
                                 'item': _clean(first.get('id'), 12), 'text':
                                 '先看这一条：\n\n「%s」\n\n%s'
                                 % (_clean(first.get('text'), ITEM_MAX), _ask_question(first, 0))})
    elif act == 'finish':
        if step < 4:
            return JSONResponse({'ok': False, 'message': '还没到收尾这一步。'}, status_code=409)
        plan['step'] = 5
        plan['blocked'] = False
        plan['report'] = _report(plan)
        plan['msgs'] = list(plan.get('msgs') or [])
        plan['msgs'].append({'role': 'ai', 'at': now, 'step': 5, 'text':
                             '收尾了。这次拆出来的零件都在上面那张表里。'})
    elif act == 'switch':
        if step != 3:
            return JSONResponse({'ok': False, 'message': '现在不在「逐条拷问」这一步。'}, status_code=409)
        want = _clean(payload.get('item'), 12)
        cur, dst = None, None
        for it in (plan.get('items') or []):
            if not isinstance(it, dict):
                continue
            if str(it.get('id')) == str(plan.get('ask_item')):
                cur = it
            if want and str(it.get('id')) == str(want):
                dst = it
        if dst is None:
            return JSONResponse({'ok': False, 'message': '找不到这一条零件。'}, status_code=404)
        if dst.get('kind') != 'fact':
            return JSONResponse({'ok': False, 'message': '只有标成「事实」的零件才拷问。'}, status_code=400)
        if dst.get('probed'):
            return JSONResponse({'ok': False, 'message': '这一条已经拷问完了。'}, status_code=409)
        if cur is not None and cur is not dst:
            cur['pi'] = _pi_of(plan.get('probe_idx'))   # 换走之前先把进度存回这一条身上
        pi = _pi_of(dst.get('pi'))
        plan['ask_item'] = dst.get('id')
        plan['probe_idx'] = pi
        plan['nudged'] = False
        plan['msgs'] = list(plan.get('msgs') or [])
        plan['msgs'].append({'role': 'ai', 'at': now, 'step': 3,
                             'item': _clean(dst.get('id'), 12), 'text': (
                             WAIT_CLOSE % _clean(dst.get('text'), ITEM_MAX) if dst.get('pending')
                             else '%s\n\n「%s」\n\n%s'
                             % ('接着问这一条：' if pi else '换到这一条：',
                                _clean(dst.get('text'), ITEM_MAX), _ask_question(dst, pi)))})
    elif act == 'close':
        # 「这条问透了」：四个问题走完只是问过，收没收尾得他自己点头
        if step != 3:
            return JSONResponse({'ok': False, 'message': '现在不在「逐条拷问」这一步。'}, status_code=409)
        cur = _find_item(plan, _clean(payload.get('item'), 12) or plan.get('ask_item'))
        if cur is None:
            return JSONResponse({'ok': False, 'message': '找不到这一条零件。'}, status_code=404)
        if cur.get('kind') != 'fact':
            return JSONResponse({'ok': False, 'message': '只有标成「事实」的零件才拷问。'}, status_code=400)
        if cur.get('probed'):
            return JSONResponse({'ok': False, 'message': '这一条已经拷问完了。'}, status_code=409)
        if not cur.get('pending'):
            return JSONResponse({'ok': False, 'message': '这一条四个问题还没问完，先答完再收。'}, status_code=409)
        cur['probed'] = True
        cur['pending'] = False
        plan['ask_item'] = ''
        plan['nudged'] = False
        plan['msgs'] = list(plan.get('msgs') or [])
        plan['msgs'].append({'role': 'ai', 'at': now, 'step': 3,
                             'item': _clean(cur.get('id'), 12), 'text':
                             '「%s」这条收下了。\n\n收尾结论：%s'
                             % (_clean(cur.get('text'), ITEM_MAX),
                                cur.get('conclusion') or '（这条没留下结论）')})
        # 接着看下一条：挑一条还能继续问的；没有就看看还有谁在等他定
        opens = _open_facts(plan)
        rest = opens or _pending_facts(plan)
        if rest:
            dst = rest[0]
            plan['ask_item'] = dst.get('id')
            plan['probe_idx'] = _pi_of(dst.get('pi'))
            plan['msgs'].append({'role': 'ai', 'at': now, 'step': 3,
                                 'item': _clean(dst.get('id'), 12), 'text': (
                                 WAIT_CLOSE % _clean(dst.get('text'), ITEM_MAX) if dst.get('pending')
                                 else '接着看下一条：\n\n「%s」\n\n%s'
                                 % (_clean(dst.get('text'), ITEM_MAX),
                                    _ask_question(dst, dst.get('pi'))))})
    elif act == 'redo':
        # 「还没问透」：这一条四个问题再走一遍；他之前答的留着，不清空
        if step != 3:
            return JSONResponse({'ok': False, 'message': '现在不在「逐条拷问」这一步。'}, status_code=409)
        cur = _find_item(plan, _clean(payload.get('item'), 12) or plan.get('ask_item'))
        if cur is None:
            return JSONResponse({'ok': False, 'message': '找不到这一条零件。'}, status_code=404)
        if cur.get('kind') != 'fact':
            return JSONResponse({'ok': False, 'message': '只有标成「事实」的零件才拷问。'}, status_code=400)
        if cur.get('probed'):
            return JSONResponse({'ok': False, 'message': '这一条已经拷问完了。'}, status_code=409)
        if not cur.get('pending'):
            return JSONResponse({'ok': False, 'message': '这一条四个问题还没问完，不用重来。'}, status_code=409)
        cur['pending'] = False
        cur['conclusion'] = ''
        cur['pi'] = 0
        plan['ask_item'] = cur.get('id')
        plan['probe_idx'] = 0
        plan['nudged'] = False
        plan['msgs'] = list(plan.get('msgs') or [])
        plan['msgs'].append({'role': 'ai', 'at': now, 'step': 3,
                             'item': _clean(cur.get('id'), 12), 'text':
                             '再来一遍这一条，这回挑重点答：\n\n「%s」\n\n%s'
                             % (_clean(cur.get('text'), ITEM_MAX), _ask_question(cur, 0))})
    elif act not in ('', 'items'):
        return JSONResponse({'ok': False, 'message': '不认识这个操作。'}, status_code=400)

    step = _clamp_step(plan.get('step'))
    pend = _pending_facts(plan)
    if step == 3 and not pend:
        _maybe_finish_probe(plan)
    elif step >= 4 and pend:
        plan['step'] = 3
        plan['ask_item'] = pend[0]['id']
        plan['probe_idx'] = _pi_of(pend[0].get('pi'))
        plan['fact_total'] = len(_fact_items(plan))
    elif step == 3 and str(plan.get('ask_item')) not in set(str(x.get('id')) for x in pend):
        # 没指定、或者指定的那条已经不在待拷问里了，才回到第一条；
        # 手动点着换过去的那条要留住。
        plan['ask_item'] = pend[0]['id']
        plan['probe_idx'] = _pi_of(pend[0].get('pi'))

    bad = _save(user, sid, plan, title)
    if bad:
        return JSONResponse({'ok': False, 'message': bad}, status_code=500)
    return JSONResponse({'ok': True, 'session': _pack(sid, title, plan)})
# =============================================================
# 9. 接口
# =============================================================
@router.get('/first-principles', response_class=HTMLResponse)
def first_principles_page():
    from pathlib import Path
    path = Path(__file__).resolve().parent / 'templates' / 'first-principles.html'
    if path.exists():
        return HTMLResponse(path.read_text(encoding='utf-8'))
    return HTMLResponse('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                        '<body style="background:#1F2A37;color:#fff;font-family:system-ui">'
                        '<h2>模板文件缺失</h2><p>请确认 templates/first-principles.html 存在。</p>'
                        '<p>隔壁两个工具照旧可用：<a href="/five-why" style="color:#9CC7AF">/five-why</a> · '
                        '<a href="/socratic" style="color:#9CC7AF">/socratic</a></p>'
                        '</body></html>')

@router.get('/first-principles/api/status')
def first_principles_status():
    return JSONResponse({'ai': bool(llm_key()), 'steps': STEPS,
                         'kinds': [{'key': k, 'name': n} for k, n in KINDS],
                         'probe': PROBE_Q})

@router.get('/first-principles/api/sessions')
def first_principles_sessions(request: Request, q: str = ''):
    """右侧历史。带 q= 时在目标、每条零件、拷问回答、对话正文里做模糊匹配。"""
    terms = _terms(q)
    try:
        rows = storage.list_records(TOOL, _user(request), SCAN_MAX if terms else LIST_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)})
    scanned = len(rows)
    items = []
    for r in rows:
        plan = r.get('payload') if isinstance(r.get('payload'), dict) else {}
        if not _is_v2(plan):
            continue
        if terms and not _matched(r.get('title'), plan, terms):
            continue
        items.append(_row(r, False))
        if len(items) >= LIST_MAX:
            break
    return JSONResponse({'ok': True, 'items': items, 'q': _clean(q, QUERY_MAX),
                         'total': len(items), 'scanned': scanned})

@router.post('/first-principles/api/sessions')
async def first_principles_create(request: Request):
    payload = await _body(request)
    goal = _clean(payload.get('goal') or payload.get('ask'), GOAL_MAX)
    if len(goal) < 4:
        return JSONResponse({'ok': False, 'message': '把那件事写长一点，四个字以上。'}, status_code=400)
    bad = _goal_problem(goal)
    if bad:
        return JSONResponse({'ok': False, 'message': bad}, status_code=400)
    return await run_in_threadpool(_do_create, goal, _user(request), _norm_src(payload))

@router.get('/first-principles/api/sessions/{sid}')
def first_principles_session(sid: int, request: Request):
    user = _user(request)
    try:
        row = storage.get_record(user, sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'message': '找不到这条拆解记录。'}, status_code=404)
    return JSONResponse({'ok': True, 'session': _row(row)})

@router.post('/first-principles/api/sessions/{sid}/reply')
async def first_principles_reply(sid: int, request: Request):
    payload = await _body(request)
    text = _clean(payload.get('text'), REPLY_MAX)
    if not text:
        return JSONResponse({'ok': False, 'message': '先写点什么再发。'}, status_code=400)
    user = _user(request)
    if not llm_key():
        return JSONResponse({'ok': False, 'message': NO_KEY}, status_code=503)
    try:
        row = storage.get_record(user, sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'message': '找不到这条拆解记录。'}, status_code=404)
    return StreamingResponse(_stream_reply(sid, text, user, row),
                             media_type='text/event-stream; charset=utf-8',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@router.patch('/first-principles/api/sessions/{sid}')
async def first_principles_update(sid: int, request: Request):
    payload = await _body(request)
    user = _user(request)
    try:
        row = storage.get_record(user, sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'message': '找不到这条拆解记录。'}, status_code=404)
    return await run_in_threadpool(_do_patch, sid, payload, user, row)

@router.delete('/first-principles/api/sessions/{sid}')
def first_principles_delete(sid: int, request: Request):
    try:
        changed = storage.delete_record(_user(request), sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not changed:
        return JSONResponse({'ok': False, 'message': '找不到这条拆解记录。'}, status_code=404)
    return JSONResponse({'ok': True})

if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _app = FastAPI(title='第一性原理', version='1.0.0')
    _app.include_router(router)
    uvicorn.run(_app, host='127.0.0.1', port=8014)