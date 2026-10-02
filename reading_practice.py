# -*- coding: utf-8 -*-
# 读书落地（页面在 /reading-practice）
# ------------------------------------------------------------
# 先抓真实书讯，再让模型说话。「不实用」的根因不是模型不行，是它手里没有材料——
# 只给一个书名，它只能顺着记忆编章节、编实验、编人名。现在流程是：
#
#   1. 用书名去 book_source 搜（豆瓣 / Google Books / Open Library，谁能通用谁）
#   2. 用户从候选里挑一本（同名书太多，这一步不能省）
#   3. 抓这本书的真实资料：简介、目录、读者短评、评分、出版社
#   4. 把资料整块喂给模型，让它只说资料里有的东西，每张卡还要写清依据
#   5. 书讯存进 SQLite（book_cache），第二次问同一本书直接读库
#
# 分工（改之前先读）：
#   - 模型最容易编的是：资料里没有的章节名、实验、人名、年代。prompt 明令禁止，
#     资料太薄时要求在 note 里直说「资料太少，拆法比较通用」；
#   - 抓不到书讯时不装：明确告诉用户「没拿到真实资料」，让他自己决定要不要继续；
#   - 没配 DEEPSEEK_API_KEY 时不编卡片，但仍然把真实书讯给出来（这部分本身就值钱）。
# 环境变量：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL / BOOK_SOURCES
import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import book_source
import storage

try:
    from model_config import llm_endpoint, llm_key, llm_model
except Exception:  # 单独拿这个文件跑时的兜底
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'

import requests

router = APIRouter()

TOOL = 'reading-practice'      # SQLite history 表里的工具名
BOOK_MAX = 60
ASK_MAX = 160
CARD_MAX = 4
STEP_MAX = 3
PLAN_MAX = 200                 # 一个人的计划最多留多少条
REVIEW_DAYS = 7

SHEET = [
    '书里哪一句话，是你今天或这周就能用上的？把原文抄下来，别改写成自己的话。',
    '你会在什么场合用它？写清时间、人物、触发条件（「周三下午领导临时插活」这样）。',
    '第一步是什么？一个动词开头的动作，15 分钟内能做完。',
    '怎么算做到？写一个别人能看见的结果，不是「我记住了」。',
    '最容易在哪里变形？（比如变成列一张 20 条的清单，一条都做不完）',
    '7 天后问自己哪一句？',
]

PLAN_SYS = '\n'.join([
    '你在帮一个想把书用起来的人拆书。我会给你书名，以及这本书在网上的真实资料',
    '（简介、目录、读者短评、评分、出版信息）。',
    '',
    '【第一条，也是最重要的一条】',
    '只根据我给你的资料说话。资料里没有的章节名、实验、人名、年代、数据、原话，',
    '一个字都不许编。你看不到正文，手上只有简介和目录，所以你要拆的是这本书',
    '「明显在讲什么」，不要假装读过具体章节。',
    '资料太薄（比如只有一句话简介）时，在 note 里直说「资料太少，下面的拆法比较通用」。',
    '',
    '【要给的不是读书笔记，是动作】他读完今天就能照着做的那种。',
    '',
    '【每张卡】',
    '  point  —— 这本书对应的那个观点，一句话；',
    '  basis  —— 你这条是从资料哪来的，比如「目录·第三部分」「简介里提到的方法」，',
    '            15 字以内。这条必填，它是我判断你有没有在编的唯一依据；',
    '  scene  —— 具体场景，写清时间 / 人物 / 触发条件，像「周三下午，领导临时插活，',
    '            你手上已经排满」。不许写「工作中」「日常生活中」这种等于没写的；',
    '  steps  —— 正好 3 步，每步以动词开头，能做完，带时间或数量；',
    '  done   —— 怎么算做到了。要能被别人看见，比如「下班前把明天 3 件事发到组里」；',
    '  trap   —— 这件事最容易变形的地方，具体，比如「变成列 20 条的清单，一条都做不完」；',
    '  review —— 7 天后问自己的一个问题。',
    '',
    '【另外】',
    '  gist  —— 这本书这套方法最核心的一句，说人话；',
    '  today —— 今天就能做的最小动作，带时长；',
    '  note  —— 只在两种情况下写：资料太少；或者他写的诉求和这本书关系不大（那就直说）。',
    '',
    '给 2 到 4 张卡，不要多。不许用「赋能 / 闭环 / 底层逻辑 / 认知升级」这类词，不堆破折号。',
    '只输出 JSON，不要 Markdown 代码块：',
    '{"gist":"...","note":"","today":"...","cards":[{"point":"","basis":"","scene":"",',
    ' "steps":["","",""],"done":"","trap":"","review":""}]}',
    '字数：gist 40、note 60、today 30、point 24、basis 15、scene 50、每步 30、done 30、trap 30、review 24。',
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


def _chat(system, user, max_tokens=2400, temperature=0.6):
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
        resp = requests.post(llm_endpoint(), json=body, timeout=(10, 180),
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


def _user(request):
    return getattr(request.state, 'user', '') or ''


async def _body(request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _material(book, ask):
    """把真实资料拼成一段给模型看的材料。有多少给多少，没有的就不写。"""
    lines = ['《%s》' % (book.get('title') or '未命名')]
    if book.get('author'):
        lines.append('作者：' + book['author'])
    meta = ' / '.join([x for x in [book.get('publisher'), book.get('year'),
                                   (book.get('pages') + ' 页') if book.get('pages') else ''] if x])
    if meta:
        lines.append('出版：' + meta)
    if book.get('rating'):
        lines.append('评分：%s（%s 人评价）' % (book['rating'], book.get('votes') or 0))
    if book.get('tags'):
        lines.append('标签：' + '、'.join(book['tags'][:8]))
    lines.append('资料来源：' + book_source.label_of(book.get('source') or ''))

    if book.get('summary'):
        lines.append('')
        lines.append('【内容简介】')
        lines.append(book['summary'])
    else:
        lines.append('')
        lines.append('【内容简介】没有抓到。')

    if book.get('toc'):
        lines.append('')
        lines.append('【目录】')
        for i, t in enumerate(book['toc'][:24], 1):
            lines.append('%d. %s' % (i, t))

    if book.get('reviews'):
        lines.append('')
        lines.append('【读者短评（网络节选，不代表书的观点）】')
        for r in book['reviews'][:8]:
            lines.append('- ' + r)

    if ask:
        lines.append('')
        lines.append('【他想用这本书解决的问题】' + ask)
    return '\n'.join(lines)

# =============================================================
# 生成：抓真实材料 -> 喂给模型
# =============================================================
def _load_book(source, sid, refresh=False):
    """拿一本书的真实资料：先查 SQLite，没有再出去抓，抓到的写回库。

    返回 (书, 是否来自缓存)；查不到就是 (None, False)。
    """
    cached = None
    try:
        row = storage.book_get(source, sid)
        if row and isinstance(row.get('payload'), dict) and row['payload'].get('title'):
            cached = row['payload']
    except storage.StorageUnavailable:
        cached = None

    if cached and not refresh:
        return cached, True

    book = book_source.fetch(source, sid)
    if book:
        # 重新抓资料别把上次拆的结果冲掉：点「最近查过的书」回看靠的就是它
        if cached:
            for key in ('analysis', 'ask'):
                if cached.get(key) and not book.get(key):
                    book[key] = cached[key]
        try:
            storage.book_put(source, sid, book.get('title', ''), book.get('author', ''), book)
        except storage.StorageUnavailable:
            pass
    return book, False


NO_MATERIAL = ('没抓到这本书的真实资料（书源连不上，或者书源里没有这本）。'
               '与其让模型凭记忆瞎编，这里就不往下走了 —— 换个书名，或者稍后再试。')


def _do_book(payload):
    """只抓书讯，不调模型。让用户先看清楚拿到的是什么材料，再决定要不要生成。"""
    source = _clean(payload.get('source'), 20)
    sid = _clean(payload.get('sid'), 40)
    if not source or not sid:
        return JSONResponse({'ok': False, 'message': '先选一本书。'})
    book, cached = _load_book(source, sid, bool(payload.get('refresh')))
    if not book:
        return JSONResponse({'ok': False, 'message': NO_MATERIAL})
    return JSONResponse({'ok': True, 'book': book, 'cached': cached,
                         'label': book_source.label_of(source)})


def _do_analyze(payload):
    source = _clean(payload.get('source'), 20)
    sid = _clean(payload.get('sid'), 40)
    ask = re.sub(r'\s+', ' ', str(payload.get('ask') or '')).strip()[:ASK_MAX]
    if not source or not sid:
        return JSONResponse({'ok': False, 'message': '先选一本书。'})

    book, cached = _load_book(source, sid, bool(payload.get('refresh')))

    if not book:
        return JSONResponse({
            'ok': False,
            'message': NO_MATERIAL,
        })

    content, err = _chat(PLAN_SYS, _material(book, ask))
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
            'basis': _clean(raw.get('basis'), 15),
            'scene': _clean(raw.get('scene'), 50),
            'steps': steps,
            'done': _clean(raw.get('done'), 30),
            'trap': _clean(raw.get('trap'), 30),
            'review': _clean(raw.get('review'), 24),
        })

    result = {
        'gist': _clean(data.get('gist'), 40),
        'note': _clean(data.get('note'), 60),
        'today': _clean(data.get('today'), 30),
        'cards': cards,
        'sheet': [] if cards else SHEET,
    }

    # 把这次拆的挂在书的缓存里。下次从「最近查过的书」点回来，
    # 卡片能原样摆出来 —— 只存书讯、回看不到东西，等于白存。
    stored = False
    if cards:
        book['analysis'] = {'data': result, 'src': 'ai', 'ask': ask,
                            'at': storage.now_str()[:10]}
        try:
            storage.book_put(source, sid, book.get('title', ''),
                             book.get('author', ''), book)
            stored = True
        except storage.StorageUnavailable:
            stored = False

    out = {
        'ok': True,
        'cached': cached,
        'stored': stored,
        'book': book,
        'labels': {n: book_source.label_of(n) for n in book_source.SOURCES},
        'source': 'ai' if cards else 'local',
        'data': result,
    }
    if not cards:
        # 没模型也能用：真实书讯照给，再配一份自己拆的六问模板
        out['message'] = err or '模型这次没给出完整卡片，下面是让你自己拆的六问模板。'
    return JSONResponse(out)


# =============================================================
# 计划：存 SQLite（history 表，tool = reading-practice）
# =============================================================
PLAN_KEYS = ('book', 'author', 'source', 'sid', 'cover', 'rating', 'url',
             'point', 'basis', 'scene', 'steps', 'done', 'trap', 'review',
             'at', 'reviewAt', 'reviewed', 'ok', 'result')


def _norm_steps(value):
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:STEP_MAX]:
        if isinstance(item, dict):
            text = _clean(item.get('t'), 40)
            if text:
                out.append({'t': text, 'ok': bool(item.get('ok'))})
        elif isinstance(item, str):
            text = _clean(item, 40)
            if text:
                out.append({'t': text, 'ok': False})
    return out


def _norm_plan(raw):
    if not isinstance(raw, dict):
        return None
    plan = {k: raw[k] for k in PLAN_KEYS if k in raw}
    steps = _norm_steps(plan.get('steps'))
    point = _clean(plan.get('point'), 40)
    if not steps or not point:
        return None
    plan['steps'] = steps
    plan['point'] = point
    plan['book'] = _clean(plan.get('book'), BOOK_MAX)
    plan['basis'] = _clean(plan.get('basis'), 20)
    plan['reviewed'] = bool(plan.get('reviewed'))
    plan['ok'] = plan.get('ok') if plan.get('ok') in (True, False) else None
    plan['result'] = _clean(plan.get('result'), 200)
    return plan


# =============================================================
# 路由
# =============================================================
@router.get('/reading-practice', response_class=HTMLResponse)
def reading_practice_page():
    from pathlib import Path
    path = Path(__file__).resolve().parent / 'templates' / 'reading-practice.html'
    if path.exists():
        return HTMLResponse(path.read_text(encoding='utf-8'))
    return HTMLResponse('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                        '<body style="background:#241a14;color:#fff;font-family:system-ui">'
                        '<h2>模板文件缺失</h2><p>请确认 templates/reading-practice.html 存在。</p>'
                        '</body></html>')


@router.get('/reading-practice/api/status')
def reading_practice_status():
    return JSONResponse({
        'ai': bool(llm_key()),
        'sources': [{'name': n, 'label': book_source.label_of(n)}
                    for n in book_source.enabled_sources()],
        'alive': book_source.alive_sources(),
    })


@router.post('/reading-practice/api/search')
async def reading_practice_search(request: Request):
    payload = await _body(request)
    query = _clean(payload.get('q'), BOOK_MAX)
    author = _clean(payload.get('author'), 40)
    if len(query) < 1:
        return JSONResponse({'ok': False, 'items': [], 'message': '先把书名写上。'})
    try:
        items, used = await run_in_threadpool(book_source.search, query, 8, author)
    except Exception as exc:  # 书源抽风不该让页面 500
        return JSONResponse({'ok': False, 'items': [],
                             'message': '搜索出错了：' + str(exc)[:100]})
    message = ''
    if not items:
        message = '没搜到。试试只写书名、去掉副标题，或者换个常见译名。'
    elif author and not any(author in (x.get('author') or '') for x in items):
        message = '没找到这个作者的书，下面是同名书，核对一下再选。'
    return JSONResponse({'ok': True, 'items': items, 'used': used, 'message': message,
                         'labels': {n: book_source.label_of(n) for n in book_source.SOURCES}})


@router.post('/reading-practice/api/book')
async def reading_practice_book(request: Request) -> JSONResponse:
    return await run_in_threadpool(_do_book, await _body(request))


@router.post('/reading-practice/api/analyze')
async def reading_practice_analyze(request: Request):
    return await run_in_threadpool(_do_analyze, await _body(request))


@router.get('/reading-practice/api/books')
def reading_practice_books() -> JSONResponse:
    """最近抓过哪些书 —— book_cache 也是这个工具用数据库的凭证。"""
    try:
        rows = storage.book_list(20)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)})
    return JSONResponse({'ok': True, 'items': rows})


def _plan_with_meta(row):
    plan = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    plan['id'] = row['id']
    plan['created_at'] = row.get('created_at', '')
    return plan


@router.get('/reading-practice/api/plans')
def reading_practice_plans(request: Request) -> JSONResponse:
    try:
        rows = storage.list_records(TOOL, _user(request), PLAN_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'message': str(exc)})
    return JSONResponse({'ok': True, 'items': [_plan_with_meta(r) for r in rows]})


@router.post('/reading-practice/api/plans')
async def reading_practice_plan_add(request: Request) -> JSONResponse:
    payload = await _body(request)
    plan = _norm_plan(payload.get('plan'))
    if not plan:
        return JSONResponse({'ok': False, 'message': '这条计划不完整：至少要有一个观点和三步动作。'},
                            status_code=400)
    try:
        rec = storage.add_record(TOOL, _user(request), plan.get('book') or plan['point'], plan)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    plan['id'] = rec['id']
    plan['created_at'] = rec['created_at']
    return JSONResponse({'ok': True, 'plan': plan})


@router.patch('/reading-practice/api/plans/{rid}')
async def reading_practice_plan_update(rid: int, request: Request) -> JSONResponse:
    """勾步骤 / 标记复盘走这里。只认几个白名单字段，不让前端改书的来源。"""
    payload = await _body(request)
    patch = payload.get('patch') if isinstance(payload.get('patch'), dict) else {}
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not row:
        return JSONResponse({'ok': False, 'message': '找不到这条计划。'}, status_code=404)

    plan = row['payload'] if isinstance(row['payload'], dict) else {}
    if isinstance(patch.get('steps'), list):
        steps = _norm_steps(patch['steps'])
        if steps:
            plan['steps'] = steps
    if 'reviewed' in patch:
        plan['reviewed'] = bool(patch['reviewed'])
    if 'ok' in patch:
        plan['ok'] = patch['ok'] if patch['ok'] in (True, False) else None
    if 'result' in patch:
        plan['result'] = _clean(patch['result'], 200)
    try:
        changed = storage.update_record(user, rid, payload=plan)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not changed:
        return JSONResponse({'ok': False, 'message': '这条计划不属于当前账号。'}, status_code=403)
    plan['id'] = rid
    plan['created_at'] = row.get('created_at', '')
    return JSONResponse({'ok': True, 'plan': plan})


@router.delete('/reading-practice/api/plans/{rid}')
def reading_practice_plan_delete(rid: int, request: Request) -> JSONResponse:
    try:
        changed = storage.delete_record(_user(request), rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=500)
    if not changed:
        return JSONResponse({'ok': False, 'message': '找不到这条计划。'}, status_code=404)
    return JSONResponse({'ok': True})


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _app = FastAPI(title='读书落地', version='2.0.0')
    _app.include_router(router)
    uvicorn.run(_app, host='127.0.0.1', port=8012)