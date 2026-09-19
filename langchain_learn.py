# -*- coding: utf-8 -*-
# LangChain 学习（页面在 /langchain-learn）
# ------------------------------------------------------------
# 七课入门实操，每一课都能在页面上点「跑一下」，看真实输出。
#
# 两个设计决定，改之前先读：
#   1. 课程内容（标题 / 概念 / 代码片段）写在前端 templates/langchain-learn.html 里，
#      服务端只负责执行。所以这里是一张 lesson id -> 函数 的白名单表，
#      页面传什么进来都只能跑到这几个函数，没有「执行任意代码」的口子。
#   2. 每一课只教一个新东西，代码故意写到最短。页面上展示的代码和这里跑的逻辑
#      是同一件事，改这边记得同步那边的字符串。
#
# 模型走 model_config，Key 从 .env / 环境变量取；没配 Key 时接口返回人话提示，
# 不抛异常、不白屏。
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

try:
    from model_config import llm_base, llm_key, llm_model
except Exception:  # 单独拷这个文件跑时的兜底
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_base():
        return 'https://api.deepseek.com'

try:  # langchain 没装上时整个站点也不能挂，只把这一页变成提示
    import langchain_core  # noqa: F401
    _LC_OK = True
except Exception:
    _LC_OK = False

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'langchain-learn.html'

MAX_INPUT = 300
NO_KEY = '服务端没配模型 Key：把 DEEPSEEK_API_KEY 写进项目根目录的 .env，或者配成环境变量，再重启服务。'


# =========================================================
# 小工具
# =========================================================
def _llm(temperature=0.0):
    """整个文件里唯一决定「模型是谁」的地方，每一课都复用它。"""
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=llm_model(), api_key=llm_key(), base_url=llm_base(),
                      temperature=temperature, timeout=60)


def _clip(value, limit=MAX_INPUT):
    return str(value if value is not None else '').strip()[:limit]


def _default(payload, key, fallback):
    return _clip(payload.get(key)) or fallback


def _blocks(*items):
    """要显示的东西统一成 [{label, value}]，前端只认这一种格式。"""
    return [{'label': str(k), 'value': str(v)} for k, v in items]


def _render_messages(messages):
    """把一组消息渲染成看得懂的文本，用来展示「真正发给模型的是什么」。"""
    return '\n'.join('%s: %s' % (m.type, m.content) for m in messages)


class LessonError(Exception):
    """课程自己抛出来的、可以直接给用户看的一句话。"""


# =========================================================
# 第 1 课：invoke —— 跟模型说一句话
# =========================================================
def _lesson_invoke(payload):
    text = _default(payload, 'text', '用一句话解释什么是链式调用')
    msg = _llm().invoke(text)
    usage = getattr(msg, 'usage_metadata', None)
    return _blocks(
        ('返回的东西是什么类型', type(msg).__name__ + '（不是字符串，是消息对象）'),
        ('msg.content', msg.content),
        ('msg.usage_metadata', json.dumps(usage, ensure_ascii=False) if usage else '这个模型没返回用量'),
    )


# =========================================================
# 第 2 课：提示词模板 —— 把变化的部分挖成槽位
# =========================================================
def _lesson_prompt(payload):
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    text = _default(payload, 'text', '我明天上午十点要去医院复查牙齿，可能得请半天假')
    try:
        n = max(2, min(30, int(payload.get('n'))))
    except (TypeError, ValueError):
        n = 6

    prompt = ChatPromptTemplate.from_messages([
        ('system', '你是一个把话说短的人，只输出改写后的句子，不要解释。'),
        ('user', '把这句话缩短到 {n} 个字：{text}'),
    ])
    filled = prompt.invoke({'n': n, 'text': text})
    out = (prompt | _llm() | StrOutputParser()).invoke({'n': n, 'text': text})
    return _blocks(
        ('prompt.input_variables（它认得出哪些槽位）', ', '.join(prompt.input_variables)),
        ('填完变量后真正发给模型的消息', _render_messages(filled.messages)),
        ('模型输出', out),
    )


# =========================================================
# 第 3 课：LCEL 管道 —— prompt | llm | parser
# =========================================================
def _lesson_chain(payload):
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    text = _default(payload, 'text', '周末想去爬山，但天气预报说有雨')
    prompt = ChatPromptTemplate.from_messages([
        ('system', '你是帮人做决定的人，只给一句结论，20 字以内。'),
        ('user', '{text}。去还是不去？'),
    ])
    llm = _llm()
    parser = StrOutputParser()
    chain = prompt | llm | parser
    out = chain.invoke({'text': text})
    try:
        keys = ', '.join(chain.input_schema.model_json_schema().get('properties', {}))
    except Exception:
        keys = ', '.join(prompt.input_variables)
    return _blocks(
        ('这三样分别是什么', ' | '.join(type(x).__name__ for x in (prompt, llm, parser))),
        ('拼起来之后它是', type(chain).__name__ + '：本身也能 .invoke()，所以链可以再串链'),
        ('chain.input_schema（它自己知道要喂哪些变量）', keys),
        ('chain.invoke(...) 的结果', out),
    )


# =========================================================
# 第 4 课：结构化输出 —— 让模型填表，而不是写作文
# =========================================================
class MoodCard(BaseModel):
    """这一课要的字段。写一遍类，模型就会照着填。"""
    mood: str = Field(description='情绪，一个词')
    reason: str = Field(description='原因，不要超过 15 个字')
    score: int = Field(description='心情分，1 到 10 的整数')


def _lesson_structured(payload):
    text = _default(payload, 'text', '今天上线又出问题，改到十点才回家')
    # method='function_calling' 是给 DeepSeek 这类接口用的：它不支持默认的
    # response_format=json_schema，不写这一句会直接 400。
    got = _llm().with_structured_output(MoodCard, method='function_calling').invoke(text)
    return _blocks(
        ('返回的东西是什么类型', type(got).__name__ + '：一个对象，不是字符串'),
        ('整个对象', json.dumps(got.model_dump(), ensure_ascii=False, indent=2)),
        ('可以直接取字段，接着往下算', 'got.mood = %r   got.score = %r' % (got.mood, got.score)),
    )


# =========================================================
# 第 5 课：少样本 —— 给几个例子，比写规则管用
# =========================================================
def _lesson_fewshot(payload):
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate

    text = _default(payload, 'text', '排了四十分钟队，进去三分钟就出来了')
    examples = [
        {'review': '这个 bug 修了三天，人麻了', 'label': 'NEG'},
        {'review': '客户今天专门发消息夸了我们', 'label': 'POS'},
        {'review': '还行吧，跟说的差不多', 'label': 'NEG'},
    ]
    example_prompt = ChatPromptTemplate.from_messages([('human', '{review}'), ('ai', '{label}')])
    few = FewShotChatMessagePromptTemplate(example_prompt=example_prompt, examples=examples)
    prompt = ChatPromptTemplate.from_messages([
        ('system', '判断这条评论是好评还是差评，只回 POS 或 NEG，不要解释。'),
        few,
        ('human', '{review}'),
    ])
    filled = prompt.invoke({'review': text})
    out = (prompt | _llm() | StrOutputParser()).invoke({'review': text})
    return _blocks(
        ('你只写了 3 个例子，发出去时展开成', _render_messages(filled.messages)),
        ('模型照着例子的格式回答', out.strip()),
    )


# =========================================================
# 第 6 课：工具 —— 模型只决定「调哪个」，执行是你的活
# =========================================================
def _build_tools():
    from langchain_core.tools import tool

    @tool
    def today_weather(city: str) -> str:
        """查一个城市今天的天气。"""
        return '%s：晴，26 度' % city

    @tool
    def add(a: int, b: int) -> int:
        """把两个整数相加。"""
        return a + b

    return {'today_weather': today_weather, 'add': add}


def _lesson_tools(payload):
    from langchain_core.messages import HumanMessage, ToolMessage

    text = _default(payload, 'text', '北京今天天气怎么样')
    tools = _build_tools()
    llm = _llm()
    first = llm.bind_tools(list(tools.values())).invoke(text)
    calls = getattr(first, 'tool_calls', None) or []
    if not calls:
        return _blocks(
            ('模型没有要调工具，直接回答了', first.content),
            ('换个问法试试', '比如「北京今天天气怎么样」「37 加 58 等于几」'),
        )
    call = calls[0]
    name = str(call.get('name') or '')
    if name not in tools:  # 模型给的名字必须在白名单里才执行
        raise LessonError('模型想调一个不存在的工具：' + name)
    result = tools[name].invoke(call.get('args') or {})
    final = llm.invoke([HumanMessage(text), first,
                        ToolMessage(content=str(result), tool_call_id=call['id'])])
    return _blocks(
        ('模型的决定（它没有真的执行，只是告诉你）', '%s(%s)' % (name, json.dumps(call.get('args'), ensure_ascii=False))),
        ('我们自己执行，拿到', result),
        ('把结果再喂回去，模型的最终回答', final.content),
    )


LESSONS = {
    'invoke': _lesson_invoke,
    'prompt': _lesson_prompt,
    'chain': _lesson_chain,
    'structured': _lesson_structured,
    'fewshot': _lesson_fewshot,
    'tools': _lesson_tools,
}

STREAM_LESSON = 'stream'
STREAM_DEFAULT = '用三句话讲清楚什么是链式调用，说人话'


# =========================================================
# 接口
# =========================================================
def _fail(message, status=200):
    return JSONResponse({'ok': False, 'message': message, 'blocks': []}, status_code=status)


def _do_run(payload):
    if not _LC_OK:
        return _fail('服务端没装 langchain：pip install langchain langchain-openai 之后重启服务。')
    if not llm_key():
        return _fail(NO_KEY)
    fn = LESSONS.get(str(payload.get('lesson') or ''))
    if fn is None:
        return _fail('没有这一课')
    try:
        return JSONResponse({'ok': True, 'blocks': fn(payload), 'message': ''})
    except LessonError as exc:
        return _fail(str(exc))
    except Exception as exc:  # 模型报错、网络超时都收敛成一句话
        return _fail('跑失败了：' + str(exc)[:180])


def _do_stream(payload):
    """第 7 课：流式输出。逐块吐纯文本，前端边收边显示。"""
    if not _LC_OK:
        return StreamingResponse(iter(['服务端没装 langchain，先 pip install langchain langchain-openai。']),
                                 media_type='text/plain; charset=utf-8')
    if not llm_key():
        return StreamingResponse(iter([NO_KEY]), media_type='text/plain; charset=utf-8')

    from langchain_core.prompts import ChatPromptTemplate

    text = _default(payload, 'text', STREAM_DEFAULT)
    prompt = ChatPromptTemplate.from_messages([
        ('system', '说人话，别堆术语，别用 Markdown 标题。'),
        ('user', '{text}'),
    ])

    def gen():
        try:
            for chunk in (prompt | _llm(0.7)).stream({'text': text}):
                piece = getattr(chunk, 'content', '') or ''
                if piece:
                    yield piece
        except Exception as exc:
            yield '\n[断了：%s]' % str(exc)[:120]

    return StreamingResponse(gen(), media_type='text/plain; charset=utf-8',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


async def _payload(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@router.get('/langchain-learn', response_class=HTMLResponse)
def langchain_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/langchain-learn.html 存在。</p>'
                '</body></html>')
    return HTMLResponse(html)


@router.post('/langchain-learn/api/run')
async def langchain_run(request: Request):
    return await run_in_threadpool(_do_run, await _payload(request))


@router.post('/langchain-learn/api/stream')
async def langchain_stream(request: Request):
    return await run_in_threadpool(_do_stream, await _payload(request))


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='LangChain 学习', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8009)
