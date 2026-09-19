# -*- coding: utf-8 -*-
# 贝叶斯日记（页面在 /predict-diary）
# ------------------------------------------------------------
# 写下「你在赌什么」（0-100% 的把握），之后每来一条新消息，让大模型判断它值几个点，
# Python 做贝叶斯更新，把「判断被现实一点点修正」的过程记下来。
#
# 分工是这个工具的核心设计，改之前先读：
#   1. 大模型只负责「理解人话」——把模糊想法改写成到时候能点头/摇头的赌注、
#      判断一条新消息往哪个方向推、几成力度、结算后写一句大白话复盘。
#   2. 数字一律不由模型给。似然比（这条消息在「会发生」时出现的可能性，是「不会发生」
#      时的几倍）到后验把握，全部在 Python 里算，用户永远不用碰这些词。
#      （上一版让人自己填似然比，普通人做不到，所以没人用——别再走回去。）
#   3. 数据只存在浏览器 localStorage，服务端不存、不写日志，只做「理解」这一件事。
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
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'predict-diary.html'

# 强度 -> 似然比：越大表示「这条消息在事情会发生时出现的可能性，比不会发生时高多少倍」
STRENGTH_LR = {1: 1.3, 2: 1.8, 3: 2.5, 4: 3.5, 5: 5.0}
STRENGTH_LABEL = {1: '很弱', 2: '偏弱', 3: '中等', 4: '偏强', 5: '很强'}
STEP_CAP = 22          # 单条消息最多让把握动多少个百分点，防止一步跳到底
EDGE = 95              # 到 95% 以上就不再往上顶，留一条「还能被推翻」的缝
MAX_TEXT = 400
MIN_SAMPLE = 8         # 少于这么多条结算就不给结论——样本太小全是噪音

SHAPE_SYS = '\n'.join([
    '你帮普通人把一句模糊的想法，改写成一句「到时候能明确点头或摇头」的赌注。',
    '规则：',
    '1. 必须包含「结果 + 时间」，到那天能明确判断成没成；',
    '2. 用第一人称口语，不超过 40 字，不要出现「可能」「也许」「我觉得」这类没法验证的词；',
    '3. 只改语法、补上下文，绝不替用户加他没说的事；信息不够就用 need 问一句缺什么；',
    '4. conf 给一个建议：他现在大概有几成把握，50-95 的整数；',
    '5. days 给一个建议：大概多少天后能知道结果，1-365 的整数；说不准就给 0；',
    '6. 只输出 JSON，不要 Markdown 代码块，格式：',
    '{"bet":"...","conf":70,"days":7,"need":"..."}',
])

WEIGH_SYS = '\n'.join([
    '用户在跟踪一个自己的判断，他要判断一条新消息对这件事有多大的推动。',
    '规则：',
    '1. dir 三选一：support（让他更相信会发生）/ against（更不相信）/ none（没影响，',
    '   比如只是重复他已经知道的信息）；',
    '2. strength 是 1-5 的整数：1=几乎没影响，3=有点影响，5=几乎决定成败。',
    '   只有那种「事情本来就该发生 / 本来就不该发生」的消息，才配 4 或 5，别滥用；',
    '3. why 一句话，不超过 30 字，说人话，不许出现「概率」「先验」「后验」「似然」这类词；',
    '4. 不评价他判断得对不对，不给建议；',
    '5. 只输出 JSON，不要 Markdown：{"dir":"support","strength":3,"why":"..."}',
])

REVIEW_SYS = '\n'.join([
    '用户跟踪的一个判断出结果了，你要帮他看清这次判断里值得记住的一点。',
    '规则：',
    '1. 1-2 句话，总共不超过 60 字；',
    '2. 不评价他这个人（不许说「你很自信」「你太冲动」），只描述这次这件事；',
    '3. 如果他的把握和结果差得远，就把这一点说出来；如果判断得准，要说清是',
    '   「判断准」还是「这件事本来就容易猜对」；',
    '4. 不喊口号，不给建议清单，不许出现「概率」「先验」「认知偏差」这类词；',
    '5. 只输出 JSON：{"insight":"..."}',
])

SUMMARY_SYS = '\n'.join([
    '用户在记录自己对各种事情的判断，现在攒下了一批有结果的记录。',
    '你要用大白话点出他的判断有什么特点。',
    '规则：',
    '1. 1-2 句话，总共不超过 70 字；',
    '2. 具体到数字，不要说套话（「继续保持」这种不要）；',
    '3. 不评价他这个人，不喊口号，不给建议清单；',
    '4. 不许出现「贝叶斯」「概率」「先验」「后验」「偏差」这类词；',
    '5. 样本少于 8 条时，直接说还看不出什么，让他多记几条；',
    '6. 只输出 JSON：{"insight":"..."}',
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


def _chat(system, user, max_tokens=600, temperature=0.3):
    key = llm_key()
    if not key:
        return None, '服务端没配模型 Key'
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    try:
        resp = requests.post(llm_endpoint(), json=body, timeout=(10, 45),
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


def _as_int(value, default, low, high):
    try:
        num = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    if num <= 0 and low <= 0:
        return 0
    return max(low, min(high, num))


def _posterior(conf, direction, strength):
    """一条新消息之后把握变成多少。返回 (新把握, 似然比, 变了几个点)。"""
    conf = max(1, min(99, int(round(float(conf)))))
    if direction not in ('support', 'against') or not strength:
        return conf, 1.0, 0
    lr = STRENGTH_LR.get(int(strength), STRENGTH_LR[3])
    if direction == 'against':
        lr = 1.0 / lr
    odds = conf / (100.0 - conf)
    new = odds * lr / (1.0 + odds * lr) * 100.0
    if new - conf > STEP_CAP:
        new = conf + STEP_CAP
    if conf - new > STEP_CAP:
        new = conf - STEP_CAP
    if lr > 1 and new > EDGE:                      # 到顶了就别再往上顶
        new = min(new, max(conf, EDGE) + 1)
    if lr < 1 and new < 100 - EDGE:
        new = max(new, min(conf, 100 - EDGE) - 1)
    new = int(round(max(1, min(99, new))))
    if new == conf:
        new = max(1, min(99, conf + (1 if lr > 1 else -1)))
    return new, round(lr, 2), new - conf


def _domain_of(text):
    """按关键词猜一个领域，只用来分组统计，猜错也不影响使用。"""
    groups = (
        ('工作', ('老板', '同事', '项目', '会议', '评审', '客户', '方案', '需求', '加班', '升职', '面试')),
        ('人际', ('朋友', '家里', '爸妈', '同学', '约', '回我', '关系', '对象', '他', '她')),
        ('钱', ('股票', '基金', '涨', '跌', '投资', '工资', '奖金', '房价', '买', '卖', '钱')),
        ('健康', ('睡', '跑', '健身', '体重', '体检', '感冒', '戒烟', '酒', '疼')),
        ('学习', ('考试', '学', '书', '课', '证书', '英语')),
    )
    for name, words in groups:
        for word in words:
            if word in text:
                return name
    return '其他'


# =========================================================
# 动作
# =========================================================
def _do_shape(payload):
    """把一句模糊想法变成能验证的赌注。模型不可用时原样退回，不挡路。"""
    text = str(payload.get('text') or '').strip()[:MAX_TEXT]
    if len(text) < 2:
        return JSONResponse({'ok': False, 'message': '先写一句你现在的想法'})
    raw, err = _chat(SHAPE_SYS, '我现在的想法：' + text, 400, 0.3)
    data = _extract_json(raw or '')
    bet = str(data.get('bet') or '').strip().strip('"')[:120]
    if len(bet) < 4:
        return JSONResponse({'ok': False, 'source': 'off', 'message': err,
                             'bet': text, 'conf': 60, 'days': 0, 'need': ''})
    return JSONResponse({
        'ok': True, 'source': 'llm', 'bet': bet,
        'conf': _as_int(data.get('conf'), 60, 50, 95),
        'days': _as_int(data.get('days'), 0, 0, 365),
        'need': str(data.get('need') or '').strip()[:60],
        'domain': _domain_of(text + bet),
    })


def _do_weigh(payload):
    """判断一条新消息往哪边推、推多重。只给建议，最终由用户拍板。"""
    bet = str(payload.get('bet') or '').strip()[:200]
    text = str(payload.get('text') or '').strip()[:MAX_TEXT]
    if len(text) < 2:
        return JSONResponse({'ok': False, 'message': '先写一句这条新消息'})
    conf = _as_int(payload.get('conf'), 60, 1, 99)
    user = '赌注：%s\n我现在的把握：%d%%\n新消息：%s' % (bet or '（没写）', conf, text)
    raw, err = _chat(WEIGH_SYS, user, 300, 0.2)
    data = _extract_json(raw or '')
    direction = str(data.get('dir') or '').strip().lower()
    if direction not in ('support', 'against', 'none'):
        # 模型没给出可用判断时，交回前端让用户自己选
        return JSONResponse({'ok': False, 'source': 'off', 'manual': True,
                             'message': err or '模型这次没给出判断'})
    strength = _as_int(data.get('strength'), 3, 1, 5)
    if direction == 'none':
        strength = 0
    return JSONResponse({
        'ok': True, 'source': 'llm', 'dir': direction, 'strength': strength,
        'why': str(data.get('why') or '').strip()[:60],
        'strength_label': STRENGTH_LABEL.get(strength, ''),
    })


def _do_apply(payload):
    """纯算术：一条消息之后把握变成多少。不调模型，没配 Key 也能用。"""
    conf = _as_int(payload.get('conf'), 60, 1, 99)
    direction = str(payload.get('dir') or 'none').strip().lower()
    if direction not in ('support', 'against', 'none'):
        direction = 'none'
    strength = 0 if direction == 'none' else _as_int(payload.get('strength'), 3, 1, 5)
    after, lr, delta = _posterior(conf, direction, strength)
    return JSONResponse({'ok': True, 'before': conf, 'after': after, 'delta': delta,
                         'lr': lr, 'dir': direction, 'strength': strength,
                         'strength_label': STRENGTH_LABEL.get(strength, '')})


def _do_review(payload):
    """结算后写一句大白话复盘。模型不可用就留空，不挡结算。"""
    bet = str(payload.get('bet') or '').strip()[:200]
    outcome = str(payload.get('outcome') or '').strip().lower()
    if outcome not in ('yes', 'no'):
        return JSONResponse({'ok': False, 'message': '结果只有「发生了」和「没发生」'})
    prior = _as_int(payload.get('prior'), 60, 1, 99)
    final = _as_int(payload.get('final'), prior, 1, 99)
    times = _as_int(payload.get('times'), 0, 0, 99)
    user = ('赌注：%s\n我最开始说 %d%%，知道结果前最后停在 %d%%，中间记了 %d 条消息。\n结果是：%s'
            % (bet or '（没写）', prior, final, times, '发生了' if outcome == 'yes' else '没发生'))
    raw, err = _chat(REVIEW_SYS, user, 300, 0.5)
    insight = str(_extract_json(raw or '').get('insight') or '').strip().strip('"')[:120]
    return JSONResponse({'ok': bool(insight), 'insight': insight,
                         'message': '' if insight else (err or '这次没生成复盘')})


def _do_summary(payload):
    """把已结算的记录汇总成「你的准头」。统计全在 Python 算，模型只写一句人话。"""
    rows = payload.get('items')
    if not isinstance(rows, list):
        rows = []
    pairs = []                       # (知道结果前最后说的把握, 实际发生=1/没发生=0)
    support = against = none_cnt = 0
    for row in rows[:500]:
        if not isinstance(row, dict):
            continue
        try:
            conf = max(1, min(99, int(round(float(row.get('conf'))))))
        except (TypeError, ValueError):
            continue
        outcome = str(row.get('outcome') or '').strip().lower()
        if outcome not in ('yes', 'no'):
            continue
        pairs.append((conf, 1.0 if outcome == 'yes' else 0.0))
        for d in (row.get('dirs') or [])[:50]:
            if d == 'support':
                support += 1
            elif d == 'against':
                against += 1
            else:
                none_cnt += 1

    n = len(pairs)
    stats = {'n': n, 'enough': n >= MIN_SAMPLE, 'support': support,
             'against': against, 'none': none_cnt, 'ev_n': support + against + none_cnt,
             'insight': '', 'message': ''}
    if n:
        hits = int(sum(a for _, a in pairs))
        avg = sum(c for c, _ in pairs) / float(n)
        # Brier 分数：每条判断的误差平方取平均，0 最好，0.25 相当于闭眼瞎猜
        brier = sum((c / 100.0 - a) ** 2 for c, a in pairs) / float(n)
        stats.update({'hits': hits, 'avg': int(round(avg)),
                      'rate': int(round(hits * 100.0 / n)), 'brier': round(brier, 3)})
        if support + against:
            stats['support_rate'] = int(round(support * 100.0 / (support + against)))
        raw, err = _chat(
            SUMMARY_SYS,
            ('他一共有 %d 条判断知道了结果，平均说 %d%% 把握的事，实际发生了 %d%%；'
             '平均误差 %s（0 最好，0.25 相当于闭眼瞎猜）。'
             '他记的 %d 条消息里，%d 条是支持自己原有看法的。'
             % (n, stats['avg'], stats['rate'], stats['brier'], stats['ev_n'], support)),
            300, 0.6)
        insight = str(_extract_json(raw or '').get('insight') or '').strip().strip('"')[:160]
        stats['insight'] = insight
        stats['message'] = '' if insight else err
    return JSONResponse({'ok': True, 'stats': stats})


# =========================================================
# 路由
# =========================================================
@router.get('/predict-diary', response_class=HTMLResponse)
def predict_diary_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/predict-diary.html 存在。</p>'
                '</body></html>')
    return HTMLResponse(html)


async def _payload(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@router.post('/predict-diary/api/shape')
async def predict_shape(request: Request):
    return await run_in_threadpool(_do_shape, await _payload(request))


@router.post('/predict-diary/api/weigh')
async def predict_weigh(request: Request):
    return await run_in_threadpool(_do_weigh, await _payload(request))


@router.post('/predict-diary/api/apply')
async def predict_apply(request: Request):
    return await run_in_threadpool(_do_apply, await _payload(request))


@router.post('/predict-diary/api/review')
async def predict_review(request: Request):
    return await run_in_threadpool(_do_review, await _payload(request))


@router.post('/predict-diary/api/summary')
async def predict_summary(request: Request):
    return await run_in_threadpool(_do_summary, await _payload(request))


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='贝叶斯日记', description='写下你在赌什么，让现实来更新它', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8008)
