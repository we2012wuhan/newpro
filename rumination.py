# -*- coding: utf-8 -*-
# 内耗拆解（页面在 /rumination）
# ------------------------------------------------------------
# 给「因为工作上的破事反复想、越想越难受」的人用。一次 3-8 分钟走完。
#
# 设计依据（改之前先读，别把它改成又一个情绪日记）：
#   1. 内耗的本质是反刍：抽象、自我指涉、反复问「为什么我这样」。
#      它不会因为「想通了」而停，只会因为「被具体化」和「被行动化」而停。
#      所以这个工具从第一步起就在做两件事：把抽象压回具体，把焦虑换成下一步。
#   2. 顺序是刻意排的：先降生理唤起，再动认知。
#      人在高度唤起时讲道理是无效的，第一步必须先让身体下来。
#      所以 S1 是 90 秒呼吸/走动，不是提问。
#   3. 大模型只做三件「语言活」：拆句、判断能推动不能推动、生成收口的三句话。
#      流程、步数、存储、统计全由 Python/前端控制。
#      跟贝叶斯日记一样：数字和结构不由模型给，模型只负责听懂人话。
#   4. 没配模型 Key 时必须能走完全流程（本地规则兜底）。
#      一个心理工具如果因为没 Key 就卡死，等于没有。
#   5. 数据只存在浏览器 localStorage，服务端不存、不写日志。
#      唯一离开本机的是用户主动点「AI 拆」时发出去的那段文字。
import json
import re
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

try:
    from model_config import llm_endpoint, llm_key, llm_model
except Exception:  # 单独拷这个文件跑时的兜底
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'rumination.html'

MAX_RAW = 800          # 用户那段话最长收多少字
MAX_ITEM = 60          # 单条拆分结果最长多少字
MAX_ITEMS = 6          # 一次最多拆出几条，多了又变成一团

# 危机信号：命中就把热线卡片顶到最上面。
# 只做提示，不弹窗、不阻断、不上报——用户是来缓解的，不是来被处理的。
CRISIS_WORDS = ['自杀', '不想活', '活不下去', '活着没意思', '活着没意义', '结束生命',
                '伤害自己', '自残', '跳楼', '解脱了算了', '死了算了']

HOTLINE = '12356'

UNPACK_SYS = '\n'.join([
    '你帮一个正在内耗的人，把脑子里那团模糊的东西拆成一条条能看清的内容。',
    '规则：',
    '1. 每条不超过 30 字，用他自己的口吻和词，不要替他加工、不要加戏、不要补他没说的信息；',
    '2. kind 三选一：',
    '   fact  = 发生过的事，能说出时间/地点/人物（例：周二会上他说这块要重做）',
    '   guess = 他的推测（例：他可能觉得我能力不行）',
    '   eval  = 他给自己或整件事下的评价（例：我这点事都办不明白）',
    '3. 最多 6 条，按他原话里的先后顺序排；',
    '4. 如果他说的是「我就是个废物」这类自我评价，照样收下、标成 eval，',
    '   不要反驳、不要安慰、不要鼓励；',
    '5. 不要给建议，不要总结他这个人，不要写「其实你」开头的句子；',
    '6. 只输出 JSON，不要 Markdown 代码块，格式：',
    '{"items":[{"text":"...","kind":"fact","note":""}]}',
    '   note 只在「这条其实是猜测，但他说得像事实」时给一句不超过 20 字的提醒，',
    '   其余情况给空字符串。',
])

SORT_SYS = '\n'.join([
    '你在帮一个内耗的人分清：哪些是他本人真能推动的，哪些不是。',
    '规则：',
    '1. 逐条判断 control，三选一：',
    '   yes     = 他本人接下来能做点什么，让这件事有变化，哪怕很小',
    '   partial = 有一部分在他手上，但结果不由他一个人定',
    '   no      = 结果由别人、流程、时机或运气决定，他再想也改不了',
    '   能判 partial 就判 partial，不要硬塞进 yes 或 no；',
    '2. why 一句话，不超过 25 字，说清「为什么这在他手上或不在」，不要安慰、不要评价他；',
    '3. 不许出现「你应该」「别想太多」「放平心态」「想开点」这类话；',
    '4. 不许给行动建议（那是下一步的事）；',
    '5. 只输出 JSON，不要 Markdown，格式：',
    '{"sorted":[{"text":"...","control":"yes","why":"..."}],"insight":"..."}',
    '   insight 是不超过 40 字的大白话，点出这团事里能动的和动不了的各占什么位置，',
    '   要具体，不要写成「你要学会放下」。',
])

CLOSE_SYS = '\n'.join([
    '一个内耗的人已经把事分成了「我能动的」和「我动不了的」，现在要收口。',
    '规则：',
    '1. next_step 只给一条，不要给清单：',
    '   what = 他 5 分钟内就能开始的第一步。必须具体到「给谁发一条什么消息」',
    '          「打开哪个文件改哪一行」这种程度，不许写「好好沟通」「调整心态」；',
    '   优先从「他能动的」里挑。如果那栏是空的，就从「他只能推一部分的」里挑一件'
    '   能核实的小事（比如去问清楚一个猜测）。绝对不要从「他动不了的」里挑。',
    '   when = 具体时间，比如「今天午休」「明天 10 点前」「下班路上」；',
    '   what 不超过 40 字；如果「我能动的」是空的，what 写「今天先不做任何决定」，',
    '   when 写「明天再看」；',
    '2. defusion = 一句帮他跟念头拉开距离的话，句式必须是「我注意到我脑子里又在说……」',
    '   或「我注意到我又在想……」，不超过 35 字；',
    '   【关键】不要否定那个念头，不要说「但这不是真的」「其实不会的」——',
    '   去中心化的意思是「我看见了它」，不是「我驳倒它」；',
    '3. compassion = 「如果是我最好的朋友遇到这件事，我会对他说」的一句话，'
    '不超过 35 字。',
    '   要具体、像人话。禁止出现「你已经很棒了」「相信自己」「加油」这类空话，'
    '也不要写成鸡汤；',
    '4. worries = 他那些「动不了的」里面，值得留到固定时间再想的，最多 3 条，'
    '每条不超过 20 字；',
    '   如果都不值得留，就给空数组，不要硬凑；',
    '5. 只说这些，不要额外安慰、不要额外建议；',
    '6. 只输出 JSON，不要 Markdown 代码块，格式：',
    '{"next_step":{"what":"...","when":"..."},"defusion":"...",'
    '"compassion":"...","worries":["..."]}',
])

# 本地兜底用的词表（没配 Key，或者模型挂了的时候照样能走完）
GUESS_WORDS = ['可能', '也许', '大概', '估计', '是不是', '会不会', '应该是', '我觉得他',
               '感觉他', '说不定', '搞不好', '是不是在']
EVAL_WORDS = ['废物', '没用', '不行', '失败', '差劲', '丢人', '懦弱', '菜', '垃圾',
              '总是', '从来', '永远', '我就是', '我这人']
NO_WORDS = ['他', '他们', '领导', '老板', '公司', '同事', '别人', '客户', '结果',
            '上面', '绩效', '考核', '流程', '规定', '运气', '别人怎么看']
YES_WORDS = ['我可以', '我能', '我要', '我准备', '我打算', '我决定', '去问', '去说',
             '去改', '去确认', '发消息', '问一下', '写', '沟通', '汇报']


# =========================================================
# 通用小工具
# =========================================================
def _extract_json(text):
    raw = re.sub(r'```[a-zA-Z]*', '', text or '')
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _chat(system, user, max_tokens=800, temperature=0.4):
    """返回 (内容, 错误说明)。没配 Key 时直接返回 None，交给本地兜底。"""
    key = llm_key()
    if not key:
        return None, '没配模型 Key'
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    try:
        resp = requests.post(llm_endpoint(), json=body, timeout=(10, 60),
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


def _clean(value, limit):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return text[:limit]


def _has_crisis(*texts):
    blob = ' '.join(str(t or '') for t in texts)
    return any(w in blob for w in CRISIS_WORDS)


# =========================================================
# 本地兜底：没配 Key / 模型挂了时，靠规则也能走完全流程
# 不追求拆得多准，只保证「这台机器上现在就能用」。
# =========================================================
def _local_split(raw):
    parts = re.split(r'[\n\r]+|(?<=[。！？!?；;])', raw or '')
    out = []
    for piece in parts:
        text = piece.strip(' \t　，,、。；;！!？?')
        if len(text) >= 4:
            out.append(text)
        if len(out) >= MAX_ITEMS:
            break
    if not out and (raw or '').strip():
        out = [_clean(raw, MAX_ITEM)]
    return out


def _local_kind(text):
    """先判推测，再判自我评价，剩下算事实。"""
    if any(w in text for w in GUESS_WORDS):
        return 'guess'
    if any(w in text for w in EVAL_WORDS):
        return 'eval'
    return 'fact'


def _local_unpack(raw):
    items = []
    for text in _local_split(raw):
        kind = _local_kind(text)
        note = ''
        if kind == 'guess':
            note = '这句是猜的，不是他说的'
        elif kind == 'eval':
            note = '这是评价，不是发生的事'
        items.append({'text': _clean(text, MAX_ITEM), 'kind': kind, 'note': note})
    return items


def _local_control(text):
    has_yes = any(w in text for w in YES_WORDS)
    has_no = any(w in text for w in NO_WORDS)
    if has_yes and has_no:
        return 'partial', '你能做一部分，结果不由你定'
    if has_yes:
        return 'yes', '这条有你自己的动作可以推'
    if has_no:
        return 'no', '结果在别人手上，你推不动'
    return 'partial', '你只能做其中一部分'


def _local_when(raw):
    """按时间词挑一个说得过去的「什么时候做」。"""
    if '今天' in raw or '今晚' in raw:
        return '今天下班前'
    if '明天' in raw:
        return '明天上午'
    return '今天下班前'


COMPASSION_POOL = [
    '这事搁谁身上都难受，你已经扛了一阵了。',
    '你现在这么累，是因为你一直在认真对待它。',
    '换成我碰上这个，我大概也睡不好。',
    '你没有做错什么，你只是撞上了一个难缠的局面。',
]


def _local_insight(rows):
    yes = sum(1 for r in rows if r['control'] == 'yes')
    no = sum(1 for r in rows if r['control'] == 'no')
    if no > yes:
        return '这团事里大部分结果不在你手上，反复想也推不动它。'
    if yes > no:
        return '这团事里有几条是你真能推的，先把它们挑出来动一下。'
    return '能动的和动不了的大概各一半，先把能动的那几条挑出来。'


def _local_worries(no_list):
    return [_clean(x, 22) for x in no_list if _clean(x, 22)][:3]


def _pick_thought(cands):
    """挑一条最像「念头」的：猜测/评价优先，别拿事实句当念头念回去。"""
    for text in cands:
        if _local_kind(text) in ('guess', 'eval'):
            return _clean(text, 20)
    return _clean(cands[0] if cands else '这件事', 20)


# =========================================================
# 三个接口的处理函数
# =========================================================
def _do_unpack(payload):
    raw = re.sub(r'[ \t\u3000]+', ' ', str(payload.get('raw') or '')).strip()[:MAX_RAW]
    if len(raw) < 6:
        return JSONResponse({'ok': False, 'msg': '先写一句发生了什么，六个字以上就行。'})

    items, source, note = [], 'local', ''
    content, err = _chat(UNPACK_SYS, '他写下的原话是：\n' + raw, 700, 0.3)
    rows = (_extract_json(content) or {}).get('items') if content else None
    if isinstance(rows, list):
        for row in rows[:MAX_ITEMS]:
            if not isinstance(row, dict):
                continue
            text = _clean(row.get('text'), MAX_ITEM)
            if len(text) < 3:
                continue
            kind = str(row.get('kind') or '').strip().lower()
            if kind not in ('fact', 'guess', 'eval'):
                kind = _local_kind(text)
            items.append({'text': text, 'kind': kind, 'note': _clean(row.get('note'), 24)})
        if items:
            source = 'ai'
    if not items:
        items = _local_unpack(raw)
        note = err if err and err != '没配模型 Key' else ''
    return JSONResponse({'ok': True, 'items': items, 'source': source,
                         'note': note, 'crisis': _has_crisis(raw)})


def _do_sort(payload):
    base = []
    for row in (payload.get('items') or [])[:MAX_ITEMS]:
        if isinstance(row, dict):
            text = _clean(row.get('text'), MAX_ITEM)
        else:
            text = _clean(row, MAX_ITEM)
        if len(text) >= 3:
            base.append(text)
    if not base:
        return JSONResponse({'ok': False, 'msg': '先有拆出来的内容，才能分堆。'})

    lines = '\n'.join('%d. %s' % (i + 1, t) for i, t in enumerate(base))
    content, err = _chat(SORT_SYS, '他这团事拆出来是这几条：\n' + lines, 900, 0.3)
    rows = (_extract_json(content) or {}).get('sorted') if content else None

    result, source, insight = [], 'local', ''
    if isinstance(rows, list) and len(rows) == len(base):
        ok_all = True
        for text, row in zip(base, rows):
            if not isinstance(row, dict):
                ok_all = False
                break
            control = str(row.get('control') or '').strip().lower()
            if control not in ('yes', 'partial', 'no'):
                ok_all = False
                break
            why = _clean(row.get('why'), 26) or _local_control(text)[1]
            result.append({'text': text, 'control': control, 'why': why})
        if ok_all:
            source = 'ai'
            insight = _clean((_extract_json(content) or {}).get('insight'), 48)

    if source != 'ai':
        result = []
        for text in base:
            control, why = _local_control(text)
            result.append({'text': text, 'control': control, 'why': why})
    if not insight:
        insight = _local_insight(result)
    return JSONResponse({'ok': True, 'items': result, 'insight': insight, 'source': source})


def _do_close(payload):
    raw = re.sub(r'[ \t\u3000]+', ' ', str(payload.get('raw') or '')).strip()[:MAX_RAW]
    yes = [_clean(x, MAX_ITEM) for x in (payload.get('control_yes') or []) if _clean(x, MAX_ITEM)]
    part = [_clean(x, MAX_ITEM) for x in (payload.get('control_partial') or []) if _clean(x, MAX_ITEM)]
    no = [_clean(x, MAX_ITEM) for x in (payload.get('control_no') or []) if _clean(x, MAX_ITEM)]

    lines = ['他原来的处境：', raw or '（没写）', '', '他能动的：']
    lines += (['- ' + x for x in yes] or ['- （没有）'])
    lines += ['', '他只能推动一部分、结果不由他一个人定的：']
    lines += (['- ' + x for x in part] or ['- （没有）'])
    lines += ['', '他动不了的：']
    lines += (['- ' + x for x in no] or ['- （没有）'])
    content, err = _chat(CLOSE_SYS, '\n'.join(lines), 900, 0.5)
    data = _extract_json(content) if content else {}

    step = data.get('next_step') if isinstance(data.get('next_step'), dict) else {}
    what = _clean(step.get('what'), 44)
    when = _clean(step.get('when'), 16)
    defusion = _clean(data.get('defusion'), 40)
    compassion = _clean(data.get('compassion'), 40)
    worries = [_clean(x, 22) for x in (data.get('worries') or []) if _clean(x, 22)][:3]
    source = 'ai' if (what and when and defusion and compassion) else 'local'

    if source == 'local':
        if not what:
            if yes:
                what = '先做一件小事：' + yes[0]
            elif part or no:
                what = '把「' + _clean((part + no)[0], 18) + '」找当事人问清楚'
            else:
                what = '今天先不做任何决定'
        if not when:
            when = _local_when(raw + ' ' + ' '.join(yes + part + no)) if (yes or part or no) else '明天再看'
        if not defusion:
            defusion = '我注意到我脑子里又在说：' + _pick_thought(no or part or yes or [raw])
        if not compassion:
            seed = sum(ord(c) for c in raw) if raw else 0
            compassion = COMPASSION_POOL[seed % len(COMPASSION_POOL)]
        if not worries:
            worries = _local_worries(no)

    return JSONResponse({'ok': True, 'source': source,
                         'next_step': {'what': what[:44], 'when': when[:16]},
                         'defusion': defusion[:40], 'compassion': compassion[:40],
                         'worries': worries})


# =========================================================
# 路由
# =========================================================
@router.get('/rumination', response_class=HTMLResponse)
def rumination_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#12101c;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/rumination.html 存在。</p>'
                '</body></html>')
    return HTMLResponse(html)


async def _payload(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@router.get('/rumination/api/ready')
def rumination_ready():
    """前端用它决定按钮上写「AI 拆」还是「本地拆」。"""
    return JSONResponse({'ai': bool(llm_key()), 'hotline': HOTLINE})


@router.post('/rumination/api/unpack')
async def rumination_unpack(request: Request):
    return await run_in_threadpool(_do_unpack, await _payload(request))


@router.post('/rumination/api/sort')
async def rumination_sort(request: Request):
    return await run_in_threadpool(_do_sort, await _payload(request))


@router.post('/rumination/api/close')
async def rumination_close(request: Request):
    return await run_in_threadpool(_do_close, await _payload(request))


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='内耗拆解', description='先把身体拉回来，再把那团事拆开', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8009)
