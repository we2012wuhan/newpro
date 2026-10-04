# -*- coding: utf-8 -*-
# 5Why 分析法（对话版）：输入一个问题，AI 一层层陪你问「为什么」，最后收成一份根因报告。
# 页面在 /five-why。
# ------------------------------------------------------------
# 为什么不是表单：这个工具真正稀缺的东西不是「一个能打字的地方」，是「下一个问题」。
# 说不清根因的人，缺的从来不是输入框，是想不出下一层该往哪儿问。
# 所以这一版由 AI 主导对话，它每一轮干三件事：
#   1. 问出下一个「为什么」，并且给 2–3 个不同角度的候选答案（挑一个、改一个、自己写都行）；
#   2. 顺读检查 —— 把上一层读成「所以……」，看这一层能不能真的推出它，跳步就打回重问；
#   3. 该停还是该继续 —— 落到「可以被直接改变的机制」就提示可以停，
#      还停在「某人不小心 / 沟通不畅 / 责任心不强」就带着一句具体的追问让他继续。
#
# 进度（第几层、在哪个阶段）由服务端定，不是模型自己报的：
#   · 正文（下一个问题）和判定（顺读 / 停继续）发两个请求并行跑，不额外等时间；
#   · 判定失败一律按「继续」处理 —— 宁可多问一层，也绝不让一次网络抖动把链掐断；
#   · 报告由 Python 拼，不让模型重写一遍 —— 交付物必须和他亲口说过的一字不差。
#
# 数据落在 SQLite 的 history 表（tool = 'five_why'），一行 = 一次分析：
#   payload = {v:2, problem, ph:{text}, stage, turns, level,
#              cur_parent, chain:[{id,parent,text,level,read,stop,why,ask,absence,root}],
#              actions:[{root,what,who,when,how}], msgs:[...], report, started_at, updated_at}
#   旧版（表单式）记录的 payload 里没有 v:2，列表接口直接跳过：不删、不显示、不受影响。
#
# 环境变量：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
import queue
import re
import threading

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

TOOL = 'five_why'
V = 2

PROBLEM_MAX = 200      # 原始输入
TEXT_MAX = 400         # 单层答案
MSG_MAX = 1200         # 单条 AI 消息
REPLY_MAX = 400        # 用户一次发言（和 TEXT_MAX 对齐：单层答案就是 400 封顶）
FIELD_MAX = 80         # 问题 / 对策四字段
ACT_MAX = 200          # 对策「做什么」可以长一点
HIST_MAX = 24          # 一次塞给模型的最近几条
LIST_MAX = 80         # 右侧历史最多列几条
SCAN_MAX = 400        # 开了搜索时往后翻多少条再筛（和 list_records 的上限对齐）
QUERY_MAX = 60        # 搜索框那串最长多少字
QUERY_TERMS = 6       # 最多拆成几个关键词，多了没意义还慢
MAX_LEVEL = 7          # 追到第几层开始提醒「也许该换一条链」
MAX_CHAIN = 60         # 一条链最多多少个节点，防脏数据

STAGES = ['确认问题', '一层层问', '根因验证', '配对策', '收尾']

NO_KEY = ('服务端没配模型 Key（DEEPSEEK_API_KEY）。这个工具全靠模型对话，'
          '配好之后再来 —— 在项目根目录的 .env 里填上就行。')

# 明显是在写「原因」而不是「问题」的说法：命中就先回去确认问题
CAUSE_LIKE = re.compile(
    r'(因为|由于|是因为|导致|造成|使得|所以|人手|人员|员工|不够|不足|太少|太慢|太差|'
    r'不负责|没责任|责任心|粗心|大意|马虎|沟通不|培训不|意识不|执行力|态度不|不重视|忽视)')

# 不可操作的表述：判定和候选都得拦住它们
VAGUE_WORD = re.compile(
    r'(责任心|不负责|不够重视|不重视|忽视|不上心|态度不|沟通不畅|沟通不到位|沟通不够|'
    r'沟通不足|配合不好|意识不足|意识不强|意识薄弱|培训不到位|培训不足|执行力差|'
    r'能力不足|人手不足|忙不过来|不小心|粗心|大意|马虎|麻痹|侥幸)')

PERSON_WORD = re.compile(
    r'(小王|小李|小张|小刘|小陈|小杨|小赵|张三|李四|王五|'
    r'某(个)?(人|员工|同事)|^他|^她|^他们|^大家|^员工|^同事们)')

GENERIC_ACT = re.compile(r'^(加强|提高|强化|重视|落实|加大|完善管理|加强管理|提升|做好|抓好)')


def _clean(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _terms(raw) -> list:
    """把搜索框那串拆成关键词：空格分隔、转小写、去重、最多 QUERY_TERMS 个。

    多个关键词是「都要命中」的关系 —— 搜「发货 供应商」只会给出两样都提过的那几条。
    纯子串匹配，不走 SQL LIKE：不用操心 % 和 _ 的转义，正文本来也都在 payload 里。
    """
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
    """一条会话里所有能被搜到的字：标题、原始问题、每一层的问与答、对策、报告。"""
    parts = [str(title or ''), str(plan.get('problem') or ''),
             str(plan.get('report') or '')]
    ph = plan.get('ph') if isinstance(plan.get('ph'), dict) else {}
    parts.append(str(ph.get('text') or ''))
    for n in plan.get('chain') or []:
        if isinstance(n, dict):
            parts.append(str(n.get('text') or ''))
            parts.append(str(n.get('ask') or ''))
            parts.append(str(n.get('why') or ''))
    for a in plan.get('actions') or []:
        if isinstance(a, dict):
            parts.append(str(a.get('what') or ''))
            parts.append(str(a.get('who') or ''))
            parts.append(str(a.get('how') or ''))
    for m in plan.get('msgs') or []:
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
# =============================================================
# 1. 和模型打交道
# =============================================================
def _chat(system, messages, max_tokens=700, temperature=0.6):
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


def _chat_text(system, messages, tries=2, **kw):
    """拿一段能用的文本。模型偶尔会吐一串空白（实测过），空就再试一次。"""
    last = ''
    for _ in range(max(1, tries)):
        text, err = _chat(system, messages, **kw)
        text = _clean(text, MSG_MAX)
        if text:
            return text, ''
        last = err or ''
    return None, last


def _chat_lines(system, messages, tries=2, **kw):
    """拿一段多行文本。

    和 _chat_text 的关键区别：**不把换行压成空格**。
    「Q: / - 候选」和「READ: / STOP: / WHY:」这两套格式全靠换行分列，
    一旦被压成一行，候选会全被吞进问题里、判定也只会读到第一个键。
    """
    last = ''
    for _ in range(max(1, tries)):
        text, err = _chat(system, messages, **kw)
        text = re.sub(r'\r\n?', '\n', str(text or '')).strip()[:MSG_MAX]
        if text:
            return text, ''
        last = err or ''
    return None, last


def _parallel(job_a, job_b):
    """两个阻塞调用同时跑，都回来了再往下走。

    用守护线程 + 队列，不用 ThreadPoolExecutor：后者线程不是守护的，
    万一某个请求卡住，容器退出时会被它拖着。
    """
    box = queue.Queue()

    def run(tag, job):
        try:
            box.put((tag, job()))
        except Exception as exc:                      # 兜底，别让子线程静默死掉
            box.put((tag, (None, str(exc)[:80])))

    threads = [threading.Thread(target=run, args=(tag, job), daemon=True)
               for tag, job in (('a', job_a), ('b', job_b))]
    for t in threads:
        t.start()
    out = {}
    for _ in threads:
        tag, value = box.get()
        out[tag] = value
    return out['a'], out['b']


# =============================================================
# 2. 提示词
# =============================================================
# 确认问题：把「因为人手不足」这种一上来就给了个解释的说法，按回「你到底要追的是哪件事」
CALIB_SYS = '\n'.join([
    '你在陪一个人做 5Why 根因分析。他刚写下自己遇到的问题，你要先确认这句话是「问题」还是「原因」。',
    '',
    '问题（现象）：能被看见、别人也能独立认定的事。例如「上周有 3 单订单延迟发货」。',
    '原因：他对这件事的解释。例如「因为人手不够」「沟通不畅」「大家不重视」。',
    '5Why 必须从「问题」出发。起点是个猜出来的原因，后面整条链都在追这个猜测。',
    '',
    '【怎么说】',
    '1. 先用一句话把他说的话还给他，确认你听到的是什么。',
    '2. 如果他那句话其实是在说原因（出现「因为」「人手不够」「太慢」「不负责」这类），',
    '   直接说「这句是原因，不是要追的那件事」，然后让他改成一句能看到的事。',
    '3. 然后只问一个问题：让他把那件事说成一句「发生了什么」。',
    '4. 像同事说话，平实、口语，两三句以内。整条回复最多一个问号。',
    '5. 不夸奖、不客套、不写标题、不写编号。只输出你要说的那段话。',
])

# 下一个为什么 + 候选：这是整个工具最值钱的一步
WHY_SYS = '\n'.join([
    '你在陪一个人做 5Why 根因分析。你这一轮的活儿只有一件：',
    '问出下一个「为什么」，并给他几个可以挑、可以改的答案。',
    '',
    '【他手上的链条】',
    '现象：{phenomenon}',
    '{chain}',
    '',
    '【现在要问第 {level} 层】上一层是：{parent}',
    '{extra}',
    '【怎么问】',
    '1. 第一行写「Q: 为什么……？」，扣住上一层那句话里的具体细节，一句话，不超过 30 字。',
    '   不许问「为什么会这样」这种放到哪都成立的空话。整条回复只能有一个问号。',
    '2. 接着写 2 到 3 行候选，每行以「- 」开头，不超过 34 字。',
    '   · 候选要来自不同角度：流程 / 系统与规则 / 信息传递 / 动作与工具 / 资源与排期。',
    '   · 每个都要具体到能看见的东西：哪个环节、哪个字段、哪条规则、哪个时间点。',
    '   · 绝对不许出现「沟通不畅」「责任心不强」「培训不到位」「意识不足」「执行力差」',
    '     「不小心」这类词 —— 它们指不到任何能改的东西，是 5Why 最大的坑。',
    '   · 如果按现有信息，这一层最顺的答案只能是上面那种不可操作的说法，',
    '     那就别写它，直接写成再往下一层的具体机制（写「没有超时告警」而不是「没人管」）。',
    '3. 只输出这几行，不要解释、不要标题、不要总结、不要问他别的问题。',
])

JUDGE_SYS = '\n'.join([
    '你是 5Why 的复核员。对方刚回答了第 {level} 层的「为什么」，你只做两个判断。',
    '',
    '一、顺读检查：把上一层读成「所以……」，看这一层能不能真的推出它。',
    '   能推出 → 成立；中间跳了步、或者只是把上一层换了个说法 → 跳步。',
    '',
    '二、逆读检查：从他这一层的答案往上倒推，能不能解释最上面那个现象？',
    '   能解释 → 能解释；只能解释一半、或者根本对不上 → 解释不了。',
    '',
    '三、该停还是该继续：',
    '   · 落到「可以被直接改变的具体机制或条件」（哪条规则缺了、哪个字段没校验、',
    '     哪个阈值没设、哪一步该确认却没确认、哪个时间点没有任何东西提醒）→ 可以停',
    '   · 还停在「某人不小心 / 沟通不畅 / 责任心不强 / 培训不到位 / 意识不足 / 执行力差」，',
    '     或者只是写了个某人、某个部门的名字 → 继续',
    '   · 方向对但不够具体（例如只说「记录有问题」却没说哪一步出问题）→ 继续',
    '',
    '严格按下面五行输出，一行一个，不要别的话：',
    'READ: 成立',
    'BACK: 能解释',
    'STOP: 继续',
    'WHY: 一句话说清为什么，不超过 40 字',
    'ASK: 要继续时给一句具体的追问，可以停就留空',
    '',
    '（READ 只能填「成立」或「跳步」；BACK 只能填「能解释」或「解释不了」；',
    'STOP 只能填「可以停」或「继续」。）',
])
# 不可操作表述对应的一句追问：要问「是什么让它成为可能」
VAGUE_NUDGE = [
    (re.compile(r'(责任心|不负责|不够重视|不重视|忽视|不上心|态度不)'),
     '换个新人来做这一步，还会不会发生？如果会，说明缺的不是责任心，而是某一步没有校验 —— 哪一步本该拦住它？'),
    (re.compile(r'(沟通不畅|沟通不到位|沟通不够|沟通不足|配合不好)'),
     '具体是哪条信息、在哪个环节、没有到达谁？这条信息本来应该由哪一步、在哪一刻传出去？'),
    (re.compile(r'(培训不到位|培训不足|意识不足|意识不强|意识薄弱)'),
     '培训完有没有当场验证过他会做？没通过会怎样？如果没有任何一步在检查，那缺的是培训还是缺校验？'),
    (re.compile(r'(执行力差|能力不足|人手不足|忙不过来)'),
     '是哪一步的工作量超过了当班能完成的量？有没有一个量化的上限或排期规则？超了会怎样？'),
    (re.compile(r'(不小心|粗心|大意|马虎|麻痹|侥幸)'),
     '是什么让这个疏忽成为可能？哪一步本该拦住它、而没有被拦住？'),
]


def _nudge(text):
    for rx, ask in VAGUE_NUDGE:
        if rx.search(text or ''):
            return ask
    return '再往下问一层：哪一个环节缺了什么，才让这件事成为可能？'


def _grams(text):
    t = re.sub(r'[\s，。、；：！？,.;:!?（）()「」“”]', '', str(text or ''))
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def _similar(a, b):
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / float(min(len(ga), len(gb)))


# =============================================================
# 3. 解析模型输出
#   不用 JSON：多轮对话里模型吐 JSON 偶尔会带多余的话，
#   而「Q: / - 」和「READ: / STOP:」这种一行一个键的格式，坏了一行也还能救回来。
# =============================================================
def _parse_why(text):
    """把模型那几行拆成 (问题, [候选…])。"""
    q, cands = '', []
    for raw in str(text or '').split('\n'):
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'^(?:Q|q|问)\s*[:：]\s*(.+)$', line)
        if m:
            q = _clean(m.group(1), 120)
            continue
        m2 = re.match(r'^(?:[-*·•]+|\d+\s*[.、)]|[A-Za-z]\s*[.、)])\s*(.+)$', line)
        if m2:
            c = _clean(m2.group(1), 120)
            # 「1. 为什么……？」这种带序号的问句，其实是问题不是候选
            if not q and c and (c.endswith('？') or c.endswith('?')):
                q = _clean(re.sub(r'^为什么\s*', '', c), 120)
                continue
            if c and len(cands) < 3:
                cands.append(c)
    if not q:                       # 没写 Q:，退一步去找带问号的那一行
        for raw in str(text or '').split('\n'):
            line = raw.strip()
            if line and not re.match(r'^[-*·•]', line) and ('？' in line or '?' in line):
                q = _clean(re.sub(r'^(?:Q|q|问)\s*[:：]\s*', '', line), 120)
                break
    seen, out = [], []
    for c in cands:
        if c not in seen:
            seen.append(c)
            out.append(c)
    return q, out[:3]


def _parse_kv(text):
    got = {'read': '', 'back': '', 'stop': '', 'why': '', 'ask': ''}
    for raw in str(text or '').split('\n'):
        m = re.match(r'^\s*(READ|BACK|STOP|WHY|ASK)\s*[:：]\s*(.*)$', raw.strip(), re.I)
        if m:
            got[m.group(1).lower()] = m.group(2).strip()
    return got


# =============================================================
# 4. 结构工具
# =============================================================
def _walk(nodes):
    """按父子关系把平铺的节点排成先根序，返回 [(深度, 节点)]。"""
    kids = {}
    for n in nodes:
        kids.setdefault(n.get('parent') or '', []).append(n)
    out = []

    def go(pid, depth):
        for n in kids.get(pid, []):
            out.append((depth, n))
            go(n.get('id'), depth + 1)

    go('', 0)
    return out


def _chain_lines(chain, for_model=True):
    rows = _walk(chain)
    if not rows:
        return '（还没有）'
    out = []
    for depth, n in rows[-14:]:
        pad = '  ' * depth
        out.append('%s- 第 %d 层：%s' % (pad, n.get('level') or 1, n.get('text') or ''))
    return '\n'.join(out)


def _nid(chain):
    return 'n%d' % (len(chain) + 1)


def _clamp_stage(raw):
    try:
        n = int(raw or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, len(STAGES)))


def _siblings(plan, parent, level, exclude=''):
    """同一父节点、同一层上已经有的其它答案 —— 用来让「并列原因」问出不同角度。"""
    out = []
    for n in plan.get('chain') or []:
        if (n.get('parent') or '') == (parent or '') and (n.get('level') or 1) == level:
            t = n.get('text') or ''
            if t and t != exclude:
                out.append(t)
    return out


# =============================================================
# 5. 对策护栏（和判定一样，走本地规则，模型挂了也拦得住）
# =============================================================
def _action_bad(act):
    what = _clean(act.get('what'), ACT_MAX)
    if not what:
        return '对策还没写。说清楚要改哪个动作。'
    if GENERIC_ACT.match(what) and not re.search(
            r'(时|后|前|小时|天|分钟|字段|规则|清单|校验|提醒|阈值|表单|模板|按钮|自动|周|月|每)', what):
        return '「加强培训 / 提高意识 / 重视」这类不是对策，可以一直说下去。写清楚改哪个动作、加哪个校验。'
    if len(what) < 6:
        return '太短了，写成一句能照着做的事。'
    if not _clean(act.get('who'), FIELD_MAX):
        return '还没写负责人。'
    if not _clean(act.get('when'), FIELD_MAX):
        return '还没写完成时间。'
    if len(_clean(act.get('how'), ACT_MAX)) < 4:
        return '「如何验证有效」要能被检查：看哪个数字、连续看多久。'
    return ''
# =============================================================
# 6. 判定：顺读 + 逆读 + 该停还是该继续
# =============================================================
def _judge_layer(parent_text, answer, level):
    """判定一层。

    本地规则先过一遍（deterministic，模型挂了也拦得住），模型再复核一次。
    模型说「可以停」但本地规则命中了不可操作表述时，以本地为准 —— 这类词没有解释空间。
    """
    out = {'read': '', 'back': '', 'stop': '继续', 'why': '', 'ask': ''}
    ans = _clean(answer, TEXT_MAX)

    if VAGUE_WORD.search(ans):
        out['why'] = '这句指不到任何能改的东西，还不能停。'
        out['ask'] = _nudge(ans)
        return out
    if PERSON_WORD.search(ans):
        out['why'] = '这是人名 / 部门名，不是原因。'
        out['ask'] = '「谁」做了什么本身改不了。什么机制让他这样做？哪一步本该拦住它？'
        return out
    if parent_text and _similar(parent_text, ans) >= 0.6:
        out['read'] = '跳步'
        out['why'] = '这一层和上一层几乎是同一句话，在原地打转。'
        out['ask'] = '换个说法没用。往下再问一层：什么让这件事成为可能？'
        return out

    sys_p = JUDGE_SYS.format(level=level)
    user = ('上一层的答案：%s\n\n他把这一层的「为什么」答成了：%s\n\n请复核这一层。'
            % (parent_text or '（最上面的现象）', ans))
    text, err = _chat_lines(sys_p, [{'role': 'user', 'content': user}],
                            max_tokens=280, temperature=0.2, tries=2)
    got = _parse_kv(text) if text else {}
    if '跳步' in got.get('read', ''):
        out['read'] = '跳步'
    elif '成立' in got.get('read', ''):
        out['read'] = '成立'
    if '解释不了' in got.get('back', ''):
        out['back'] = '解释不了'
    elif '能解释' in got.get('back', ''):
        out['back'] = '能解释'
    if '可以停' in got.get('stop', ''):
        out['stop'] = '可以停'
    out['why'] = _clean(got.get('why'), 120) or out['why']
    out['ask'] = _clean(got.get('ask'), 200) or out['ask']
    if not text:
        out['why'] = out['why'] or ('这次复核没跑起来（%s），先按「继续」往下走，不影响你接着答。'
                                    % (err or '模型没回话'))
    if not out['ask'] and out['stop'] != '可以停':
        out['ask'] = '再往下问一层：哪一个环节缺了什么，才让这件事成为可能？'
    return out


# =============================================================
# 7. 问下一层
# =============================================================
def _ask_why(plan, level, parent_text, exclude=None):
    """生成 (问题, [候选…])。生成不出来就退回一句兜底，绝不把链断在这儿。"""
    chain = plan.get('chain') or []
    ph = plan.get('ph') or {}
    extra = ''
    if exclude:
        extra += ('【注意】这一层已经有这些答案了：%s。\n'
                  '请换一个完全不同的角度问同一个「为什么」，不要和它们重复。\n'
                  % '；'.join(exclude))
    if level >= MAX_LEVEL:
        extra += ('（已经追到第 %d 层了。如果这一层还停在描述上，就把它往「哪条规则 / 哪个字段 / '
                  '哪个时间点」上收。）\n' % level)

    sys_p = WHY_SYS.format(
        phenomenon=_clean(ph.get('text') or plan.get('problem') or '（未写）', PROBLEM_MAX),
        chain=_chain_lines(chain),
        level=level,
        parent=_clean(parent_text, TEXT_MAX) or '（最上面的现象）',
        extra=extra,
    )
    text, _err = _chat_lines(sys_p, [{'role': 'user', 'content': '请给出这一层的追问和候选。'}],
                             max_tokens=460, temperature=0.7, tries=2)
    q, cands = _parse_why(text or '')
    if not cands and text:                       # 候选漏了就再要一次，只要候选
        again, _e2 = _chat_lines(
            sys_p, [{'role': 'user', 'content': '只给我 3 行候选答案，每行以「- 」开头，不要别的内容。'}],
            max_tokens=300, temperature=0.7, tries=1)
        _q2, cands2 = _parse_why(again or '')
        if cands2:
            cands = cands2
    if not q:
        q = '为什么会出现「%s」？' % (_clean(parent_text, 40) or '这个现象')
    return q, cands


# =============================================================
# 8. 确认问题
# =============================================================
def _calib_fallback(cause_like):
    if cause_like:
        return ('你说的这句更像原因，不是要追的那件事 —— 起点是个猜测的话，整条链都会追着这个猜测跑。'
                '换成一句能看到的事？')
    return '好，我们从第一个「为什么」开始。'


def _calib(plan, cause_like):
    user = ('他写的是：%s%s'
            % (plan.get('problem') or '（空）',
               '\n注意：他这句更像在说原因，不在说发生了什么。' if cause_like else ''))
    text, _err = _chat_lines(CALIB_SYS, [{'role': 'user', 'content': user}],
                             max_tokens=320, temperature=0.5, tries=2)
    return text or _calib_fallback(cause_like)
# =============================================================
# 9. 报告：由 Python 拼，不让模型重写 —— 交付物必须和他说过的一字不差
# =============================================================
def _cell(value, limit):
    return _clean(value, limit).replace('|', '／').replace('\n', ' ')


def _report(plan):
    ph = plan.get('ph') or {}
    chain = plan.get('chain') or []
    actions = plan.get('actions') or []
    title = _clean(ph.get('text') or plan.get('problem') or '（没写问题）', PROBLEM_MAX)

    lines = ['# 5Why：' + title, '']
    lines += ['## 问题', '', '- ' + title]
    lines.append('')

    rows = _walk(chain)
    lines += ['## 因果链', '']
    if rows:
        for depth, n in rows:
            pad = '  ' * depth
            tag = '　**← 根因**' if n.get('root') else ''
            lines.append('%s- 第 %d 层：%s%s'
                         % (pad, n.get('level') or 1, _clean(n.get('text'), TEXT_MAX), tag))
    else:
        lines.append('- （这次没往下追）')
    lines.append('')

    roots = [n for _d, n in rows if n.get('root')]
    lines += ['## 根因', '']
    if roots:
        for i, n in enumerate(roots, 1):
            verdict = {'no': '如果它不存在，问题就不会发生 → 验证通过',
                       'yes': '如果它不存在，问题还会发生 → 还没到底'}.get(
                           n.get('absence'), '还没做根因验证')
            lines += ['### 根因 %d（第 %s 层）' % (i, n.get('level') or 1), '',
                      '> ' + _clean(n.get('text'), TEXT_MAX), '',
                      '- 根因验证：' + verdict, '']
    else:
        lines += ['- （这次没确认到根因）', '']

    lines += ['## 对策', '']
    if actions:
        lines += ['| 做什么 | 负责人 | 完成时间 | 如何验证有效 |',
                  '| --- | --- | --- | --- |']
        for a in actions:
            lines.append('| %s | %s | %s | %s |'
                         % (_cell(a.get('what'), ACT_MAX), _cell(a.get('who'), FIELD_MAX),
                            _cell(a.get('when'), FIELD_MAX), _cell(a.get('how'), ACT_MAX)))
        lines.append('')
    else:
        lines += ['- （还没有对策）', '']

    lines += ['## 待办', '']
    if actions:
        for a in actions:
            lines.append('- [ ] %s · %s · %s'
                         % (_clean(a.get('who'), FIELD_MAX) or '（负责人未定）',
                            _clean(a.get('when'), FIELD_MAX) or '（时间未定）',
                            _clean(a.get('what'), ACT_MAX)))
    else:
        lines.append('- [ ] （还没有待办）')
    lines += ['', '---', '', '由「5Why 分析法」导出 · ' + storage.now_str()]
    return '\n'.join(lines)


# =============================================================
# 10. 存 / 取
# =============================================================
def _find(chain, node_id):
    for n in chain:
        if isinstance(n, dict) and n.get('id') == node_id:
            return n
    return None


def _text_of(chain, node_id):
    n = _find(chain, node_id)
    return _clean(n.get('text'), TEXT_MAX) if n else ''


def _sibling_labels(plan, parent, level, exclude=''):
    """「并列原因」是按层看：同一父节点、同一层上已经有的答案。"""
    return _siblings(plan, parent, level, exclude)


def _verdict_line(v):
    bits = []
    if v.get('read') == '成立':
        bits.append('顺读：成立 —— 把上一层读成「所以」，这一层确实能推出它。')
    elif v.get('read') == '跳步':
        bits.append('顺读：跳步 —— 中间缺了一环，这一层的答案还推不出上一层。')
    if v.get('back') == '能解释':
        bits.append('逆读：倒着推，能解释最上面那个现象。')
    elif v.get('back') == '解释不了':
        bits.append('逆读：倒着推，解释不了最上面那个现象 —— 这条链可能串偏了。')
    if v.get('stop') == '可以停':
        bits.append('判断：可以停在这里了 —— 这一层已经是能直接改的东西。')
    elif v.get('why'):
        bits.append('判断：还得往下追 —— ' + v['why'])
    return '\n'.join(bits)


def _action_line(a):
    return ('做什么：%s\n负责人：%s\n完成时间：%s\n如何验证有效：%s'
            % (a.get('what') or '（空）', a.get('who') or '（空）',
               a.get('when') or '（空）', a.get('how') or '（空）'))


def _ph_line(ph):
    return _clean(ph.get('text'), PROBLEM_MAX)


def _chain_out(chain):
    out = []
    for n in (chain or [])[:MAX_CHAIN]:
        if not isinstance(n, dict):
            continue
        out.append({
            'id': _clean(n.get('id'), 20),
            'parent': _clean(n.get('parent'), 20),
            'text': _clean(n.get('text'), TEXT_MAX),
            'level': max(1, min(int(n.get('level') or 1), 99)),
            'read': _clean(n.get('read'), 8),
            'back': _clean(n.get('back'), 8),
            'stop': _clean(n.get('stop'), 8),
            'why': _clean(n.get('why'), 120),
            'ask': _clean(n.get('ask'), 200),
            'absence': _clean(n.get('absence'), 8),
            'root': bool(n.get('root')),
        })
    return out


def _actions_out(raw):
    out = []
    for a in (raw or [])[:20]:
        if not isinstance(a, dict):
            continue
        item = {'root': _clean(a.get('root'), 20),
                'what': _clean(a.get('what'), ACT_MAX),
                'who': _clean(a.get('who'), FIELD_MAX),
                'when': _clean(a.get('when'), FIELD_MAX),
                'how': _clean(a.get('how'), ACT_MAX)}
        if any(item.values()):
            out.append(item)
    return out


def _msg_clean(value, limit):
    """消息用：压掉多余空格，但保留换行 —— 气泡要按行显示。"""
    t = re.sub(r'[ \t]+', ' ', str(value or ''))
    t = re.sub(r'\n{3,}', '\n\n', t).strip()
    return t[:limit]


def _msgs_out(msgs):
    out = []
    for m in (msgs or [])[:400]:
        if not isinstance(m, dict):
            continue
        text = _msg_clean(m.get('text'), MSG_MAX)
        if not text:
            continue
        item = {'role': 'ai' if m.get('role') == 'ai' else 'me',
                'text': text, 'at': _clean(m.get('at'), 20),
                'stage': m.get('stage') if isinstance(m.get('stage'), int) else 0}
        ui = m.get('ui') if isinstance(m.get('ui'), dict) else {}
        kind = ui.get('kind')
        if kind in ('calib', 'answer', 'verify', 'action', 'next', 'done'):
            item['ui'] = {'kind': kind}
            if kind == 'answer':
                cands = [_clean(c, 120) for c in (ui.get('cands') or [])[:3]]
                item['ui']['cands'] = [c for c in cands if c]
            if kind == 'action':
                item['ui']['root'] = _clean(ui.get('root'), 20)
        out.append(item)
    return out


def _need(plan):
    for m in reversed(plan.get('msgs') or []):
        if isinstance(m, dict) and m.get('role') == 'ai':
            ui = m.get('ui') if isinstance(m.get('ui'), dict) else {}
            if ui.get('kind'):
                return ui['kind']
            return 'answer'
    return 'answer'


def _pack(rid, title, plan, full=True):
    ph = plan.get('ph') if isinstance(plan.get('ph'), dict) else {}
    msgs = plan.get('msgs') if isinstance(plan.get('msgs'), list) else []
    stage = _clamp_stage(plan.get('stage'))
    out = {
        'id': rid,
        'title': title or '',
        'problem': _clean(plan.get('problem'), PROBLEM_MAX),
        'ph': {'text': _clean(ph.get('text'), PROBLEM_MAX)},
        'stage': stage,
        'stageName': STAGES[stage - 1],
        'level': max(1, int(plan.get('level') or 1)),
        'finished': bool(plan.get('finished')),
        'chain': _chain_out(plan.get('chain')),
        'actions': _actions_out(plan.get('actions')),
        'count': len(msgs),
        'started_at': _clean(plan.get('started_at'), 20),
        'updated_at': _clean(plan.get('updated_at'), 20),
        'need': _need(plan),
    }
    if full:
        out['msgs'] = _msgs_out(msgs)
        out['report'] = str(plan.get('report') or '')
    return out


def _row(row, full=True):
    plan = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    return _pack(row.get('id'), row.get('title'), plan, full)


def _is_v2(plan):
    return isinstance(plan, dict) and plan.get('v') == V
# =============================================================
# 11. 状态推进
# =============================================================
def _new_plan(problem, now):
    return {
        'v': V, 'problem': problem,
        'ph': {'text': problem},
        'stage': 1, 'turns': 0, 'level': 1, 'cur_parent': '',
        'root_id': '', 'action_root': '',
        'chain': [], 'actions': [], 'msgs': [], 'report': '',
        'finished': False, 'started_at': now, 'updated_at': now,
    }


def _save(user, sid, plan, title=None):
    plan['updated_at'] = storage.now_str()
    plan['msgs'] = (plan.get('msgs') or [])[-400:]
    try:
        storage.update_record(user, sid, payload=plan, title=title)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'session': _pack(sid, title, plan)})


def _do_create(problem, user):
    if not llm_key():
        return JSONResponse({'ok': False, 'message': NO_KEY}, status_code=503)
    now = storage.now_str()
    plan = _new_plan(problem, now)
    if CAUSE_LIKE.search(problem):
        msg = _calib(plan, True)
        plan['stage'] = 1
        ui = {'kind': 'calib'}
    else:
        q, cands = _ask_why(plan, 1, '')
        plan['stage'] = 2
        msg = '第 1 个为什么：%s' % q
        ui = {'kind': 'answer', 'cands': cands}
    plan['msgs'] = [{'role': 'ai', 'text': msg, 'at': now, 'stage': plan['stage'], 'ui': ui}]
    try:
        rec = storage.add_record(TOOL, user, problem[:60], plan)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'session': _pack(rec['id'], problem[:60], plan)})


def _bad(msg, code=400):
    return JSONResponse({'ok': False, 'message': msg}, status_code=code)


def _do_reply(sid, payload, user):
    try:
        row = storage.get_record(user, sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row or row.get('tool') != TOOL:
        return _bad('找不到这次分析。', 404)
    plan = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    if not _is_v2(plan):
        return _bad('这是旧版（表单式）的记录，新版读不了它。数据还在库里，没被删。', 409)

    now = storage.now_str()
    title = row.get('title')
    msgs = plan.get('msgs') if isinstance(plan.get('msgs'), list) else []
    chain = plan.get('chain') if isinstance(plan.get('chain'), list) else []
    text = _clean(payload.get('text'), REPLY_MAX)
    stage = _clamp_stage(plan.get('stage'))

    if plan.get('finished'):
        msgs.append({'role': 'me', 'text': text or '（又说了一句）', 'at': now, 'stage': stage})
        msgs.append({'role': 'ai', 'at': now, 'stage': stage, 'ui': {'kind': 'done'},
                     'text': '这次分析已经收尾了。要追新的问题，点上面的「新的分析」。'})
        plan['msgs'] = msgs
        return _save(user, sid, plan, title)

    # ---------- 阶段 1：确认问题（只校准一轮，不反复拦着人） ----------
    if stage == 1:
        ph = plan.get('ph') if isinstance(plan.get('ph'), dict) else {}
        if text:
            ph['text'] = text
        plan['ph'] = ph
        plan['problem'] = _clean(ph.get('text'), PROBLEM_MAX) or plan.get('problem') or ''
        title = _clean(ph.get('text'), 60) or title
        msgs.append({'role': 'me', 'text': _ph_line(ph), 'at': now, 'stage': 1})
        q, cands = _ask_why(plan, 1, '')
        plan['stage'] = 2
        plan['level'] = 1
        plan['cur_parent'] = ''
        msgs.append({'role': 'ai', 'at': now, 'stage': 2, 'ui': {'kind': 'answer', 'cands': cands},
                     'text': '要追的就是：%s\n\n第 1 个为什么：%s' % (_ph_line(ph), q)})
        plan['msgs'] = msgs
        return _save(user, sid, plan, title)

    # ---------- 阶段 2：一层层问 ----------
    if stage == 2:
        if payload.get('undo'):
            if not chain:
                return _bad('还没有可以撤掉的层。')
            last = chain.pop()
            plan['chain'] = chain
            plan['cur_parent'] = last.get('parent') or ''
            plan['level'] = max(1, int(last.get('level') or 1))
            while msgs and msgs[-1].get('role') == 'ai':
                msgs.pop()
            if msgs and msgs[-1].get('role') == 'me':
                msgs.pop()
            parent_text = _text_of(chain, plan['cur_parent'])
            q, cands = _ask_why(plan, plan['level'], parent_text, None)
            msgs.append({'role': 'ai', 'at': now, 'stage': 2, 'ui': {'kind': 'answer', 'cands': cands},
                         'text': '上一层撤掉了，重新问一次。\n\n第 %d 个为什么：%s' % (plan['level'], q)})
            plan['msgs'] = msgs
            return _save(user, sid, plan, title)

        if not text:
            return _bad('先写一句答案再发 —— 一层只写一个原因。')
        branch = bool(payload.get('branch'))
        parent_id = plan.get('cur_parent') or ''
        level = max(1, int(plan.get('level') or 1))
        parent_text = _text_of(chain, parent_id)
        node = {'id': _nid(chain), 'parent': parent_id, 'level': level, 'text': text,
                'read': '', 'back': '', 'stop': '', 'why': '', 'ask': '',
                'absence': '', 'root': False}
        chain.append(node)
        plan['chain'] = chain
        msgs.append({'role': 'me', 'text': text, 'at': now, 'stage': 2})

        next_level = level if branch else level + 1
        next_parent_text = parent_text if branch else text
        exclude = _sibling_labels(plan, parent_id, level, text) if branch else None
        (verdict, _e1), (qa, _e2) = _parallel(
            lambda: (_judge_layer(parent_text, text, level), ''),
            lambda: (_ask_why(plan, next_level, next_parent_text, exclude), ''),
        )
        node.update({'read': verdict.get('read') or '', 'back': verdict.get('back') or '',
                     'stop': verdict.get('stop') or '', 'why': verdict.get('why') or '',
                     'ask': verdict.get('ask') or ''})
        head = _verdict_line(verdict)

        if (not branch) and verdict.get('stop') == '可以停':
            plan['stage'] = 3
            plan['root_id'] = node['id']
            msgs.append({'role': 'ai', 'at': now, 'stage': 3, 'ui': {'kind': 'verify'},
                         'text': head + '\n\n这一层已经落到能被直接改变的东西上了。先做一次根因验证 ——'
                                       '\n如果这条不存在，问题还会发生吗？'})
        else:
            q, cands = qa
            plan['cur_parent'] = parent_id if branch else node['id']
            plan['level'] = next_level
            warn = ''
            if verdict.get('ask'):
                warn += verdict['ask'] + '\n\n'
            if not branch and next_level > MAX_LEVEL:
                warn += ('（已经到第 %d 层了。5 层是经验值，不是硬规定；但追到这儿还在描述，'
                         '也许该换一条链，或者问题本身描述得太大。）\n\n' % next_level)
            msgs.append({'role': 'ai', 'at': now, 'stage': 2, 'ui': {'kind': 'answer', 'cands': cands},
                         'text': head + '\n\n' + warn + '第 %d 个为什么：%s' % (next_level, q)})
        plan['msgs'] = msgs
        return _save(user, sid, plan, title)
    # ---------- 阶段 3：根因验证 ----------
    if stage == 3:
        ans = str(payload.get('verify') or '')
        if ans not in ('yes', 'no'):
            return _bad('选一下：还会发生，还是不会发生了？')
        node = _find(chain, plan.get('root_id') or '')
        if not node:
            plan['stage'] = 2
            plan['msgs'] = msgs
            return _save(user, sid, plan, title)
        node['absence'] = ans
        msgs.append({'role': 'me', 'at': now, 'stage': 3,
                     'text': '如果它不存在，问题还会发生。' if ans == 'yes'
                             else '如果它不存在，问题就不会发生了。'})
        if ans == 'yes':
            level = max(1, int(node.get('level') or 1)) + 1
            plan['stage'] = 2
            plan['cur_parent'] = node['id']
            plan['level'] = level
            q, cands = _ask_why(plan, level, node.get('text') or '')
            msgs.append({'role': 'ai', 'at': now, 'stage': 2, 'ui': {'kind': 'answer', 'cands': cands},
                         'text': '那它还不是根因 —— 要「它不存在，问题就不再发生」才算到底。'
                                 '\n\n第 %d 个为什么：%s' % (level, q)})
        else:
            node['root'] = True
            plan['stage'] = 4
            plan['action_root'] = node['id']
            msgs.append({'role': 'ai', 'at': now, 'stage': 4,
                         'ui': {'kind': 'action', 'root': node['id']},
                         'text': '根因确认：%s\n\n给它配一条对策。四样都得写清楚，少一样这条对策就等于没写 ——'
                                 '\n做什么、负责人、完成时间、如何验证有效。' % (node.get('text') or '')})
        plan['msgs'] = msgs
        return _save(user, sid, plan, title)

    # ---------- 阶段 4：配对策 ----------
    if stage == 4:
        if payload.get('next') == 'done':
            plan['report'] = _report(plan)
            plan['stage'] = 5
            plan['finished'] = True
            msgs.append({'role': 'me', 'at': now, 'stage': 4, 'text': '收尾，生成报告。'})
            msgs.append({'role': 'ai', 'at': now, 'stage': 5, 'ui': {'kind': 'done'},
                         'text': plan['report']})
            plan['msgs'] = msgs
            return _save(user, sid, plan, title)

        if payload.get('next') == 'root':
            last = chain[-1] if chain else None
            if not last:
                return _bad('这次还没追出链条，没有别的根因可找。')
            level = max(1, int(last.get('level') or 1))
            parent_id = last.get('parent') or ''
            plan['stage'] = 2
            plan['cur_parent'] = parent_id
            plan['level'] = level
            exclude = _sibling_labels(plan, parent_id, level, last.get('text') or '')
            q, cands = _ask_why(plan, level, _text_of(chain, parent_id), exclude)
            msgs.append({'role': 'me', 'at': now, 'stage': 4, 'text': '还有别的根因要处理。'})
            msgs.append({'role': 'ai', 'at': now, 'stage': 2, 'ui': {'kind': 'answer', 'cands': cands},
                         'text': '好，回到第 %d 层，找一条并列的原因。\n\n第 %d 个为什么：%s'
                                 % (level, level, q)})
            plan['msgs'] = msgs
            return _save(user, sid, plan, title)

        raw = payload.get('action') if isinstance(payload.get('action'), dict) else {}
        act = {'what': _clean(raw.get('what'), ACT_MAX), 'who': _clean(raw.get('who'), FIELD_MAX),
               'when': _clean(raw.get('when'), FIELD_MAX), 'how': _clean(raw.get('how'), ACT_MAX)}
        bad = _action_bad(act)
        if bad:
            msgs.append({'role': 'me', 'at': now, 'stage': 4, 'text': _action_line(act)})
            msgs.append({'role': 'ai', 'at': now, 'stage': 4,
                         'ui': {'kind': 'action', 'root': plan.get('action_root') or ''},
                         'text': bad + '\n\n改一下再交 —— 一条对策至少要能回答'
                                       '「谁、什么时候、做什么、怎么看它有没有用」。'})
            plan['msgs'] = msgs
            return _save(user, sid, plan, title)

        act['root'] = plan.get('action_root') or ''
        plan['actions'] = _actions_out((plan.get('actions') or []) + [act])
        msgs.append({'role': 'me', 'at': now, 'stage': 4, 'text': _action_line(act)})
        msgs.append({'role': 'ai', 'at': now, 'stage': 4, 'ui': {'kind': 'next'},
                     'text': '这条对策记下了。\n\n还有别的根因要处理吗？'
                             '没有的话就收尾，我把它拼成一份能直接拿去开会的报告。'})
        plan['msgs'] = msgs
        return _save(user, sid, plan, title)

    return _bad('这次分析的状态不对，刷新一下页面。', 409)

# =============================================================
# 12. 接口
# =============================================================
@router.get('/five-why', response_class=HTMLResponse)
def five_why_page():
    from pathlib import Path
    path = Path(__file__).resolve().parent / 'templates' / 'five-why.html'
    if path.exists():
        return HTMLResponse(path.read_text(encoding='utf-8'))
    return HTMLResponse('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                        '<body style="background:#1F2A37;color:#fff;font-family:system-ui">'
                        '<h2>模板文件缺失</h2><p>请确认 templates/five-why.html 存在。</p>'
                        '</body></html>')


@router.get('/five-why/api/status')
def five_why_status():
    return JSONResponse({'ai': bool(llm_key()), 'stages': STAGES, 'maxLevel': MAX_LEVEL})


@router.get('/five-why/api/sessions')
def five_why_sessions(request: Request, q: str = ''):
    """右侧历史。带 q= 时在标题、原始问题、每一层的问答、对策和报告里做模糊匹配。

    不带 q 就是原样。带了 q 会先把最近 SCAN_MAX 条全捞出来筛一遍，
    命中条数照样截到 LIST_MAX，另外把扫了多少条一起返回，免得用户以为「就这些」。
    """
    terms = _terms(q)
    try:
        rows = storage.list_records(TOOL, _user(request), SCAN_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)})
    items = []
    scanned = 0
    for r in rows:
        plan = r.get('payload') if isinstance(r.get('payload'), dict) else {}
        if not _is_v2(plan):
            continue            # 旧版表单记录：不显示、也不删，数据还在库里
        scanned += 1
        if terms and not _matched(r.get('title'), plan, terms):
            continue
        items.append(_row(r, False))
        if len(items) >= LIST_MAX:
            break
    return JSONResponse({'ok': True, 'items': items, 'q': _clean(q, QUERY_MAX),
                         'total': len(items), 'scanned': scanned})


@router.post('/five-why/api/sessions')
async def five_why_create(request: Request):
    payload = await _body(request)
    problem = _clean(payload.get('problem') or payload.get('ask'), PROBLEM_MAX)
    if len(problem) < 4:
        return _bad('把问题写长一点，四个字以上 —— 最好是一句能看见的问题。')
    return await run_in_threadpool(_do_create, problem, _user(request))


@router.get('/five-why/api/sessions/{sid}')
def five_why_session(sid: int, request: Request):
    try:
        row = storage.get_record(_user(request), sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row or row.get('tool') != TOOL:
        return _bad('找不到这次分析。', 404)
    plan = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    if not _is_v2(plan):
        return _bad('这是旧版（表单式）的记录，新版读不了它。数据还在库里，没被删。', 409)
    return JSONResponse({'ok': True, 'session': _row(row)})


@router.post('/five-why/api/sessions/{sid}/reply')
async def five_why_reply(sid: int, request: Request):
    payload = await _body(request)
    return await run_in_threadpool(_do_reply, sid, payload, _user(request))


@router.patch('/five-why/api/sessions/{sid}')
async def five_why_update(sid: int, request: Request):
    payload = await _body(request)
    user = _user(request)
    try:
        row = storage.get_record(user, sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row or row.get('tool') != TOOL:
        return _bad('找不到这次分析。', 404)
    plan = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    if not _is_v2(plan):
        return _bad('这是旧版（表单式）的记录，新版读不了它。', 409)
    title = row.get('title')
    if 'title' in payload:
        title = _clean(payload.get('title'), 60) or title
    try:
        storage.update_record(user, sid, payload=plan, title=title)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'session': _pack(sid, title, plan)})


@router.delete('/five-why/api/sessions/{sid}')
def five_why_delete(sid: int, request: Request):
    try:
        changed = storage.delete_record(_user(request), sid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not changed:
        return _bad('找不到这次分析。', 404)
    return JSONResponse({'ok': True})


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _app = FastAPI(title='5Why 分析法', version='2.0.0')
    _app.include_router(router)
    uvicorn.run(_app, host='127.0.0.1', port=8014)