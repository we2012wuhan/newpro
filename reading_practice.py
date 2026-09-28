# -*- coding: utf-8 -*-
# 读书落地（页面在 /reading-practice）
# ------------------------------------------------------------
# 输入书名 + 作者，模型不给读书笔记、不给观点摘要，只给「今天能照着做的动作」。
# 每张卡 = 书里的一个观点 + 一个具体场景 + 三步动作 + 怎么算做到 + 最容易变形的地方
#          + 7 天后问自己什么。页面可以勾步骤、到期回访。
# 分工（改之前先读）：
#   - 模型最容易在这里编内容：编书里没有的实验、章节、人名、年份。prompt 里明令禁止，
#     不知道就写「我没把握这本书的内容」，宁可笼统；
#   - 没配 DEEPSEEK_API_KEY 时不给假内容，改成给一份「自己拆」的六问模板；
#   - 实践卡只存浏览器 localStorage，服务端不存、不写日志。
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
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'reading-practice.html'

BOOK_MAX = 60         # 书名最长多少字
ASK_MAX = 160         # 他想解决的问题最长多少字
CARD_MAX = 4          # 一次最多几张实践卡
STEP_MAX = 3          # 每张卡固定三步

SHEET = [
    '书里哪一句话，是你今天或这周就能用上的？把原文抄下来，别改写成自己的话。',
    '你会在什么场合用它？写清时间、人物、触发条件（「周三下午领导临时插活」这样）。',
    '第一步是什么？一个动词开头的动作，15 分钟内能做完。',
    '怎么算做到？写一个别人能看见的结果，不是「我记住了」。',
    '最容易在哪里变形？（比如变成列一张 20 条的清单，一条都做不完）',
    '7 天后问自己哪一句？',
]

PLAN_SYS = '\n'.join([
    '你在帮一个想把书用起来的人拆书。他给你书名和作者，有时还写了想解决的问题。',
    '你要给的不是读书笔记、不是观点摘要，而是动作：他读完之后今天就能照着做的那种。',
    '【最重要的一条】只写这本书里确实有的方法。不许编细节——不许编书里没有的实验、章节、',
    '人名、年代、数据、原话。你要是不确定这本书讲了什么，就在 note 里直说',
    '「我没把握这本书的具体内容」，然后给通用的落地拆法，绝不许假装读过。',
    '【每张卡】',
    '  point  —— 书里对应的那个观点，一句话；',
    '  scene  —— 具体场景，写清时间 / 人物 / 触发条件，像「周三下午，领导临时插活，你手上已经排满」；',
    '            不许写「工作中」「日常生活中」这种等于没写的；',
    '  steps  —— 正好 3 步，每步以动词开头，能做完，带时间或数量；',
    '  done   —— 怎么算做到了。要能被别人看见，比如「下班前把明天 3 件事发到组里」；',
    '  trap   —— 这件事最容易变形的地方，具体，比如「变成列 20 条的清单，一条都做不完」；',
    '  review —— 7 天后问自己的一个问题。',
    '【另外】',
    '  gist  —— 这本书这套方法最核心的一句，说人话；',
    '  today —— 今天就能做的最小动作，带时长；',
    '  note  —— 只在两种情况下写：不确定这本书的内容；或者他写的诉求和这本书关系不大（那就直说）。',
    '都给 2 到 4 张卡，不要多。不许用「赋能 / 闭环 / 底层逻辑 / 认知升级」这类词，不堆破折号。',
    '只输出 JSON，不要 Markdown 代码块：',
    '{"gist":"...","note":"","today":"...","cards":[{"point":"","scene":"","steps":["","",""],',
    ' "done":"","trap":"","review":""}]}',
    '字数：gist 40、note 60、today 30、point 24、scene 50、每步 30、done 30、trap 30、review 24。',
])

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


def _chat(system, user, max_tokens=1800, temperature=0.6):
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


def _clean(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _clean_list(value, limit, most):
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = _clean(item, limit)
        if text and text not in out:
            out.append(text)
    return out[:most]


def _do_analyze(payload):
    book = _clean(payload.get('book'), BOOK_MAX)
    if len(book) < 2:
        return JSONResponse({'ok': False, 'msg': '先把书名写上。'})
    author = _clean(payload.get('author'), 40)
    ask = re.sub(r'\s+', ' ', str(payload.get('ask') or '')).strip()[:ASK_MAX]

    user = '书名：《' + book + '》'
    if author:
        user += '\n作者：' + author
    if ask:
        user += '\n他想用这本书解决的问题：' + ask

    content, _err = _chat(PLAN_SYS, user, 1800, 0.6)
    data = _extract_json(content) if content else {}

    cards = []
    for raw in (data.get('cards') or [])[:CARD_MAX]:
        if not isinstance(raw, dict):
            continue
        steps = _clean_list(raw.get('steps'), 34, STEP_MAX)
        point = _clean(raw.get('point'), 24)
        if not steps or not point:
            continue
        cards.append({
            'point': point,
            'scene': _clean(raw.get('scene'), 50),
            'steps': steps,
            'done': _clean(raw.get('done'), 30),
            'trap': _clean(raw.get('trap'), 30),
            'review': _clean(raw.get('review'), 24),
        })

    if cards:
        return JSONResponse({'ok': True, 'source': 'ai', 'data': {
            'gist': _clean(data.get('gist'), 40),
            'note': _clean(data.get('note'), 60),
            'today': _clean(data.get('today'), 30),
            'cards': cards,
            'sheet': [],
        }})

    # 没模型（或模型抽风）时不给假内容，给一份自己拆的模板
    return JSONResponse({'ok': True, 'source': 'local', 'data': {
        'gist': '', 'note': '', 'today': '', 'cards': [], 'sheet': SHEET,
    }})


# =========================================================
# 路由
# =========================================================
@router.get('/reading-practice', response_class=HTMLResponse)
def reading_practice_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#241a14;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/reading-practice.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.get('/reading-practice/api/status')
def reading_practice_status():
    """页面用它决定按钮上写「拆成能做的事」还是「给我自己拆的模板」。"""
    return JSONResponse({'ai': bool(llm_key())})


@router.post('/reading-practice/api/analyze')
async def reading_practice_analyze(request: Request):
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
    _standalone = FastAPI(title='读书落地', description='把书里的东西变成今天能做的事', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8012)
