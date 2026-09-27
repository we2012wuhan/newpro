# -*- coding: utf-8 -*-
# 判断力教练（页面在 /judgement-trainer）
# ------------------------------------------------------------
# 这个工具练的是判断力，不是让 AI 替你决定。所以顺序是反过来的：
#   1. 你写一件正在犹豫的真事，先写下「我打算怎么做 / 凭什么 / 几成把握」——先押注；
#   2. 再把这件事交给模型拆：事实、猜测、你其实在赌什么、你漏掉的变量、反方视角、去查什么；
#   3. 存进本子，到期回访「当初那句凭什么还成立吗」。攒几条就能看见：你报的把握，
#      和实际发生的比例差多少——这才是判断力能被练出来的那部分。
# 分工（改之前先读）：
#   - 模型只做「把话说清」这一件事：分开事实和猜测、把赌注写成能被证伪的一句。
#     它不给建议、不选边，prompt 里明令禁止；
#   - 没配 DEEPSEEK_API_KEY 也能用，走本地规则拆（粗一些，页面上会标出来是哪种）；
#   - 案例、押注、回访记录只存浏览器 localStorage，服务端不存、不写日志。
# 环境变量：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
import json
import re
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

try:
    from model_config import llm_endpoint, llm_key, llm_model
except Exception:  # 单独拿这个文件跑时的兜底
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'judgement-trainer.html'

CASE_MAX = 900        # 案例原文最多收多少字
ITEM_MAX = 34         # 拆解里每条最长多少字
LIST_MAX = 3          # 事实 / 猜测最多几条

COACH_SYS = '\n'.join([
    '你在帮一个人练判断力。他把自己正犹豫的一件真事写给你。',
    '你的活是替他把这件事摊开，让他自己看清；不是替他做决定。',
    '【规矩】',
    '1. 只用他写的信息。他没写的，就写「你没写」或者干脆不提，绝不替他补细节。',
    '2. 把他原话里的「发生了什么」和「他怎么解释的」分开——这一条最重要。',
    '   事实是能核对的（有时间、有数字、有谁说了什么）；猜测是他自己加上去的解释。',
    '3. bet 要写成一句能被证伪的话，用第二人称，像「你在赌对方不会真的走流程」这样。',
    '   不要写成「你希望……」或者「你觉得……」——那还是原话，没拆开。',
    '4. unknowns 写他没提到、但会改变结果的变量，要具体：谁、什么时候、多少钱、哪种情况。',
    '5. counter 要具体到「如果 X 不成立，他的结论会怎么反过来」，不许写「要谨慎」「多想想」。',
    '6. check 给一件他这几天能真去查证的事，必须是动作：问谁、查哪份记录、翻哪条消息。',
    '7. trap 是这件事背后最像的思维陷阱，只写一个词加一句说明；看不出像哪个就写「不明显」。',
    '8. 不给建议、不说该选哪个、不评价他这个人，也不评价他报的把握是高是低。',
    '9. 说人话。不许用「赋能 / 闭环 / 认知升级 / 底层逻辑」这类词，也不许堆破折号。',
    '只输出 JSON，不要 Markdown 代码块，不要任何解释：',
    '{"kind":"...","trap":"...","facts":["..."],"guesses":["..."],',
    ' "bet":"...","unknowns":["..."],"counter":"...","check":"..."}',
    '字数上限（超了会被截断，宁短不长）：kind 16、trap 30、facts 和 guesses 每条 32 最多 3 条、',
    'bet 45、unknowns 每条 32 最多 2 条、counter 60、check 40。',
])

# ---------- 本地兜底：没配 Key 时用规则粗拆，别让工具卡死 ----------
GUESS_WORDS = ['可能', '应该', '估计', '大概', '也许', '肯定', '一定', '担心', '怕', '觉得',
               '以为', '感觉', '说不定', '反正', '其实', '无非', '八成', '多半', '不一定',
               '显然是', '必然是', '不愿意', '不会真', '会答应', '能搞定']

FACT_RE = re.compile(r'\d|周[一二三四五六日天]|今天|昨天|明天|上周|下周|这个月|上个月|上午|下午|晚上|'
                     r'电话|微信|邮件|消息|开会|会议|合同|报价|工资|房贷|块|元|万|点前|之前|以后')

# 认知偏差：命中关键词才提示，命中不了就写「不明显」——别硬套
TRAPS = [
    ('沉没成本', '已经投进去的时间和钱，正在替你决定下一步', ['已经投', '都花了', '都做到这', '不甘心', '都坚持', '都干到这']),
    ('锚定', '你先听到的那个数字，成了判断的起点', ['他说', '报价', '开价', '一开始说', '他给', '标价']),
    ('确认偏误', '你只捡了支持自己想法的那些信息', ['我就知道', '果然', '早就觉得', '本来就', '果然是']),
    ('从众', '别人都这么做，被你当成了理由', ['大家都在', '同事都', '别人都', '他们都', '群里都', '同行都']),
    ('损失厌恶', '怕丢掉的那部分，被放得比实际更重', ['舍不得', '怕失去', '不甘', '浪费', '白费', '怕丢掉']),
    ('急着收场', '想赶紧把这件事结束掉，比选对更占上风', ['赶紧', '快点', '烦死', '不想再', '算了', '拖太久']),
    ('面子', '怕开口之后不好看，正在左右你的选择', ['不好意思', '面子', '难看', '伤和气', '怕得罪', '拉不下脸']),
]


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


def _chat(system, user, max_tokens=900, temperature=0.4):
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


def _clean(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _clean_list(value, limit=ITEM_MAX, most=LIST_MAX):
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = _clean(item, limit)
        if text and text not in out:
            out.append(text)
    return out[:most]


def _split_sentences(text):
    """本地兜底用：把一段话切成短句，太短的丢掉。"""
    parts = re.split(r'[。！？；;\n]+', text or '')
    out = []
    for part in parts:
        part = _clean(part, 70)
        if len(part) >= 4:
            out.append(part)
    return out[:12]


def _looks_guess(sentence):
    return ('?' in sentence) or ('？' in sentence) or any(w in sentence for w in GUESS_WORDS)


def _guess_kind(text):
    if re.search(r'买|卖|花|值不值|价|钱|贵', text):
        return '一次花钱的判断'
    if re.search(r'说|问|回|拒绝|开口|提|谈|沟通|微信|邮件|电话', text):
        return '一次要开口的判断'
    if re.search(r'要不要|去不去|接不接|做不做|该不该|还是', text):
        return '一个要不要的选择'
    if re.search(r'什么时候|多久|时间|拖|等', text):
        return '一个时间上的取舍'
    return '一件还没定的事'


def _local_analyze(text):
    """没有模型时的兜底拆解：粗糙，但事实和猜测这一刀还是要切。"""
    sents = _split_sentences(text)
    guesses = [s for s in sents if _looks_guess(s)][:LIST_MAX]
    facts = [s for s in sents if not _looks_guess(s) and FACT_RE.search(s)][:LIST_MAX]
    if not facts:
        facts = [s for s in sents if not _looks_guess(s)][:LIST_MAX]

    core = guesses[0] if guesses else (sents[0] if sents else _clean(text, 60))
    for word in GUESS_WORDS:  # 只留「在猜」的那半句，前面的事实部分不要
        cut = core.find(word)
        if 0 < cut and len(core) - cut >= 6:
            core = core[cut:]
            break
    bet = core
    trap = '不明显'
    for name, note, keys in TRAPS:
        if any(k in text for k in keys):
            trap = name + '：' + note
            break

    unknowns = []
    if not re.search(r'\d', text):
        unknowns.append('这条里没出现任何数字或日期，你的成本和时间其实是估的')
    unknowns.append('对方手上有什么、会怎么反应，你没写——这是另一半信息')

    if guesses:
        short = guesses[0][:18] + ('…' if len(guesses[0]) > 18 else '')
        counter = '「' + short + '」这条最像猜的。它要是不成立，你的结论会反过来。'
    else:
        counter = '你写的都是发生过的，没写你怎么解释它。先把那层解释找出来，再看结论站不站得住。'

    hit = FACT_RE.search(text)
    if hit:
        cut = max(0, hit.start() - 8)
        check = '去核实这句里的时间或数字：' + text[cut:cut + 14]
    else:
        check = '问最相关的那个人一句，问能定死你判断的那个问题'
    if len(check) > 40:
        check = check[:40]

    return {
        'kind': _guess_kind(text),
        'trap': trap[:30],
        'facts': facts,
        'guesses': guesses,
        'bet': _clean('你在赌：' + core, 45),
        'unknowns': unknowns[:2],
        'counter': counter[:60],
        'check': check,
    }

def _do_analyze(payload):
    text = re.sub(r'[ \t\u3000]+', ' ', str(payload.get('case') or '')).strip()[:CASE_MAX]
    if len(text) < 8:
        return JSONResponse({'ok': False, 'msg': '先把这件事写清楚一点，一两句话也行。'})
    lean = _clean(payload.get('lean'), 80)
    why = _clean(payload.get('why'), 80)
    try:
        conf = int(payload.get('conf') or 0)
    except (TypeError, ValueError):
        conf = 0
    conf = max(0, min(100, conf))

    user = '他写的这件事：\n' + text
    if lean:
        user += '\n\n他自己打算怎么做：' + lean
    if why:
        user += '\n他的理由：' + why
    if conf:
        user += '\n他自己报的把握：' + str(conf) + '%'

    content, _err = _chat(COACH_SYS, user, 900, 0.5)
    data = _extract_json(content) if content else {}

    result = {
        'kind': _clean(data.get('kind'), 16),
        'trap': _clean(data.get('trap'), 30),
        'facts': _clean_list(data.get('facts')),
        'guesses': _clean_list(data.get('guesses')),
        'bet': _clean(data.get('bet'), 45),
        'unknowns': _clean_list(data.get('unknowns'), 32, 2),
        'counter': _clean(data.get('counter'), 60),
        'check': _clean(data.get('check'), 40),
    }
    source = 'ai' if (result['bet'] and result['counter'] and (result['facts'] or result['guesses'])) else 'local'
    if source == 'local':
        result = _local_analyze(text)
    return JSONResponse({'ok': True, 'source': source, 'data': result})


# =========================================================
# 路由
# =========================================================
@router.get('/judgement-trainer', response_class=HTMLResponse)
def judgement_trainer_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/judgement-trainer.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.get('/judgement-trainer/api/status')
def judgement_trainer_status():
    """页面上用不到 Key，这里只是告诉前端「这次是 AI 拆还是本地规则拆」。"""
    return JSONResponse({'ai': bool(llm_key())})


@router.post('/judgement-trainer/api/analyze')
async def judgement_trainer_analyze(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return await run_in_threadpool(_do_analyze, payload)


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='判断力教练', description='先押注，再看牌', version='2.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8003)
