# -*- coding: utf-8 -*-
"""
LangChain 测试台（页面在 /langchain-test）
==============================================================
给初学者的一页：四个最小可运行的例子，每个例子只讲透一个基础点，
而且 **页面上显示的代码就是从本文件里现读出来的那份**（inspect.getsource），
不是前端手抄一遍 —— 所以永远不会出现「看到的和跑的不是一回事」。

四个用例：
  chat     ① 大模型调用：invoke 一句话，看清返回的对象里到底有什么
  prompt   ② 提示词模板 + 链：把会变的部分挖成占位符，再用 | 串起来
  tools    ③ 工具调用：模型只负责「决定调哪个」，真正执行的是我们的代码
  context  ④ 上下文：模型自己没有记忆，是我们在每次请求里把历史一起发过去

三个设计取舍：
  1. 注释写在「能跑的那份代码」里，页面直接读它 —— 注释和代码不可能对不上。
  2. 工具是真工具（算术、时间、字数），返回真结果，跑完能自己核对，
     不搞「返回假数据假装调用了」那一套。
  3. 每次运行落一条 SQLite 记录（走 storage.py），右侧历史可以点回去看，
     换设备 / 换浏览器记录都还在。
"""
import inspect
import json
import textwrap
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import storage
from model_config import llm_base, llm_key, llm_model

router = APIRouter()

TOOL = 'langchain-test'          # 存库时用的工具名，跟别的工具区分开
HISTORY_MAX = 30                 # 右侧历史最多留多少条
MAX_INPUT = 300                  # 单个输入框的长度上限（防止有人往里灌一万字）

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'langchain-test.html'

# 两种「跑不起来」的原因，各给一句人话；页面上直接显示，不抛异常、不白屏
NO_KEY = '服务端没配模型 Key：把 DEEPSEEK_API_KEY 写进项目根目录的 .env，或者配成环境变量，再重启服务。'
NO_LC = '服务端没装 langchain：先 pip install -r requirements.txt，再重启服务。'

try:                                   # langchain 没装时只让这一页变提示，别把整个站点拖垮
    import langchain_core  # noqa: F401
    _LC_OK = True                      # noqa: F841  （只为探测能不能 import）
except Exception:
    _LC_OK = False                     # 导入失败就记一笔，后面接口里统一提示


# =========================================================
# 公共代码：模型是怎么来的（这一块页面会单独展示，四个用例都用它）
# =========================================================
def _llm(temperature=0.0):
    """建一个模型对象。整个文件里只有这一处决定「用哪个模型」。

    temperature：0 表示尽量稳定（示例用），1 表示更发散（写文案用）。
                 初学阶段先记住一句话：要可复现就调小，要创意就调大。
    三个敏感值都不写在代码里，而是从服务端配置读：
    model / api_key / base_url 分别来自 .env 或环境变量（见 model_config.py），
    所以页面上没有输入框，也不会把 Key 缓存到浏览器里。
    """
    # 放在函数里 import：这样即使环境里没装 langchain，本模块也能被 main.py 正常加载，
    # 只有在真去点「运行」的时候才会需要它。
    from langchain_openai import ChatOpenAI
    # ChatOpenAI 这个名字有点误导：它其实是一个「OpenAI 兼容协议」的客户端，
    # DeepSeek、通义、本地 vLLM、Ollama 只要兼容这个协议，都能用它连。
    return ChatOpenAI(
        model=llm_model(),                 # 模型名，例如 deepseek-chat
        api_key=llm_key(),                 # 密钥
        base_url=llm_base(),               # 接口地址
        temperature=temperature,           # 创造性
        timeout=60,                        # 单次请求最长等 60 秒，别让页面一直转圈
    )


# =========================================================
# 公共小工具
# =========================================================
def _clip(value, limit=MAX_INPUT):
    """把页面传来的输入收拾干净：None 变空串、去掉首尾空白、最多留 limit 个字。"""
    return str(value if value is not None else '').strip()[:limit]


def _blocks(*items):
    """结果统一成 [{'label':..., 'value':...}]，前端只认这一种格式，省得两边各写一套渲染。"""
    return [{'label': str(k), 'value': str(v)} for k, v in items]


def _render(messages):
    """把一串消息渲染成看得懂的文本，用来展示「真正发给模型的是什么」。"""
    # LangChain 的消息对象都有 .type（system / human / ai / tool）和 .content 两个属性
    return '\n'.join('%s: %s' % (m.type, m.content) for m in messages)


def _usage(msg):
    """把 token 用量转成一行 JSON；模型没返回用量就说明这一点。"""
    usage = getattr(msg, 'usage_metadata', None)   # getattr 带默认值：不是所有模型都给
    if not usage:
        return '（这个模型没返回用量信息）'
    return json.dumps(usage, ensure_ascii=False)

# =========================================================
# ① 大模型调用：invoke 一句话，看清返回的到底是什么
# =========================================================
def demo_chat(payload):
    """最简的一次调用 —— 但大多数人第一次都会被「返回值」绊一下。"""
    # 1) 从页面传进来的参数里取问题；取不到就用一个默认问题兜底，保证点了就能跑
    question = _clip(payload.get('input')) or '用一句话说明什么是 LangChain'

    # 2) 拿一个模型对象。它是「可调用的」，但怎么调、传什么，是下一步的事
    model = _llm()

    # 3) invoke = 调用一次：进去一个输入，等一个完整输出。
    #    这里传的是纯字符串，LangChain 会自动把它包成一条 HumanMessage（人类说的话）
    message = model.invoke(question)

    # 4) 关键点，也是初学者最容易卡的地方：返回的不是 str，而是一个 AIMessage 对象。
    #    想拿文字要用 .content；直接打印对象只会看到一串对象表示
    text = message.content

    # 5) 用量信息（这次花了多少 token）挂在 .usage_metadata 上。
    #    这是「钱花在哪」最直接的答案，也是排查「这次为什么特别贵」的第一手数据
    usage = getattr(message, 'usage_metadata', None)

    # 6) 返回给页面看：按「我发出去的是什么 / 它回来的是什么 / 还能看到什么」排三块
    return _blocks(
        ('我发出去的', question),
        ('我传进去的类型', 'str（LangChain 自动包成 HumanMessage）'),
        ('它回来的类型', type(message).__name__ + '（不是 str，取文字要用 .content）'),
        ('message.content', text),
        ('message.usage_metadata', _usage(message)),
        ('除了 invoke 还能怎么调',
         'stream() 一个字一个字往外吐，做打字机效果；batch() 一次提交多条请求，并发更省时间'),
    )


# =========================================================
# ② 提示词模板 + 链：把「会变的部分」挖成占位符，再用竖线串起来
# =========================================================
def demo_prompt(payload):
    """写死提示词能跑通，但没法复用；模板就是用来解决这个的。"""
    # 1) 在函数里 import 需要的零件：谁用谁 import，读代码时一眼能看出这一步依赖什么
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    # 2) 取用户输入；空着就用默认话题
    topic = _clip(payload.get('input')) or '为什么洗完澡会觉得更清醒'

    # 3) 模板 = 一段固定的写法 + 几个 {占位符}。
    #    第一条 'system' 是给模型的身份设定（它决定语气和边界），
    #    第二条 'user' 才是「用户说的话」。这个区分很实用：改语气不用动用户的输入
    prompt = ChatPromptTemplate.from_messages([
        ('system', '你是一位讲人话的科普作者，回答控制在 80 字以内，不要小标题。'),
        ('user', '用大白话解释：{topic}'),
    ])

    # 4) 单独把模板跑一下：能亲眼看到「填完变量后真正发给模型的是什么东西」
    #    （初学阶段这一步特别值：以后提示词不生效，先看这里对不对）
    filled = prompt.invoke({'topic': topic})

    # 5) LCEL：用竖线 | 把几步接成一条链，左边一步的输出就是右边一步的输入。
    #    prompt 吐消息 -> 模型吐 AIMessage -> StrOutputParser 把 content 抠成纯字符串
    chain = prompt | _llm(0.3) | StrOutputParser()

    # 6) 整条链同样是一个 invoke：传变量字典进去，出来直接就是字符串
    answer = chain.invoke({'topic': topic})

    # 7) 把「模板长什么样 / 真正发出去的是什么 / 链怎么写 / 结果」一起给页面
    return _blocks(
        ('模板里挖的占位符', '{topic}'),
        ('填完变量后真正发给模型的内容', filled.to_string()),
        ('链的写法', 'prompt | model | StrOutputParser()'),
        ('最终答案（已经是纯字符串）', answer),
        ('为什么末尾要接 StrOutputParser',
         '不接它，链出来的是 AIMessage 对象，还得自己再 .content 一下；接上就直接拿到文字'),
    )

# =========================================================
# ③ 工具调用：模型只「决定调哪个」，真正执行的是我们的代码
# =========================================================
def _build_tools():
    """造三个真工具。工具 = 一个普通函数 + 一句说明 + @tool 装饰器。

    注意：模型看不到函数里的代码，它只看得到「工具名 + 那句说明 + 参数格式」，
    所以 docstring 写得越具体，它选得越准 —— 这就是「工具描述即提示词」。
    """
    # 在函数里 import，避免没装 langchain 时模块加载就失败
    from langchain_core.tools import tool

    @tool
    def add(a: int, b: int) -> str:
        """把两个整数相加。两个参数都传整数，返回它们的和。"""
        # 参数类型靠注解（a: int）告诉模型，它自己会把 "37 加 58" 拆成 a=37, b=58。
        # 写 int 而不是 float 是有意的：算了 95 就回 95，不会变成 95.0，示例读起来干净
        return str(a + b)

    @tool
    def now_time(dummy: str = '') -> str:
        """查询此刻的日期和时间（服务器所在时区）。不需要任何参数。"""
        # dummy 这个用不上的参数是故意的：不少模型对「零参数工具」支持不好，
        # 留一个可选的占位参数，兼容性会好很多，这是实战里很常见的一个小技巧
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    @tool
    def count_words(text: str) -> str:
        """统计一段文字有多少个字（不计空格和换行）。"""
        # 去掉空格和换行再数长度，中文场景下比按空格分词更符合直觉
        return '%d 个字' % len(str(text).replace(' ', '').replace('\n', ''))

    # 返回 dict 是为了「按名字取工具」；传给模型的要用 .values() 那串列表
    return {'add': add, 'now_time': now_time, 'count_words': count_words}


def demo_tools(payload):
    """模型没有任何执行能力 —— 它只会说「我想调 add，参数是 37 和 58」。执行是我们的活。"""
    # 1) 只 import 这一步用到的东西
    from langchain_core.messages import HumanMessage, ToolMessage

    # 2) 取问题；默认这个问法会同时触发两个工具，好看清「一次要调多个」的处理
    question = _clip(payload.get('input')) or '现在几点？另外 37 加 58 等于几？'

    # 3) 把三个工具造出来
    tools = _build_tools()

    # 4) bind_tools：把工具清单「挂」到模型上。这一步只是把「工具名 + 说明 + 参数格式」
    #    一起发给模型，模型并不会因此获得任何执行能力，也碰不到我们的代码
    model = _llm().bind_tools(list(tools.values()))

    # 5) 第一轮：把问题发过去。模型这时可能不直接回答，而是回一串 tool_calls（它想调的工具）
    first = model.invoke(question)

    # 6) tool_calls 在 AIMessage 上，是个列表；一个工具都不想调时是空的
    calls = getattr(first, 'tool_calls', None) or []

    # 7) 模型一个工具都不调也完全正常（比如问它常识题），那就把它的话原样返回
    if not calls:
        return _blocks(
            ('模型没有要调工具，直接回答了', first.content),
            ('想看到工具被调用，换个问法试试',
             '「现在几点」「37 加 58 等于几」「你好世界有几个字」'),
        )

    # 8) 两个列表分别记「模型的决定」和「我们执行的结果」，最后一起给页面看
    decided, executed = [], []

    # 9) 消息历史：先放「用户说的话」和「模型的第一轮决定」，
    #    然后每执行完一个工具，就往后面追加一条工具结果
    history = [HumanMessage(question), first]

    # 10) 一条问题可能同时要调好几个工具，所以这里用循环，不是只取第一个
    for call in calls:
        name = str(call.get('name') or '')          # 模型给的工具名
        args = call.get('args') or {}               # 模型给的参数（一个 dict）

        # 11) 白名单校验：模型有可能编出一个不存在的工具名。
        #     绝不能拿它去查表就直接调用，这一步是安全底线
        if name not in tools:
            decided.append('%s(%s)  ← 没有这个工具，已跳过'
                           % (name, json.dumps(args, ensure_ascii=False)))
            continue

        decided.append('%s(%s)' % (name, json.dumps(args, ensure_ascii=False)))

        # 12) 真正执行发生在这一行 —— 是我们（本地代码）在跑，不是模型在跑
        result = tools[name].invoke(args)
        executed.append('%s → %s' % (name, result))

        # 13) 把结果包成一条 ToolMessage 喂回去。
        #     tool_call_id 必须和上面那次调用对上，模型才知道「这条结果是哪次调用的答案」
        history.append(ToolMessage(content=str(result), tool_call_id=call['id']))

    # 14) 第二轮：把「问题 + 模型的决定 + 工具结果」整串发过去，模型这才会给出最终回答。
    #     这里故意用一个没绑工具的干净模型：它没法再调工具，只能老老实实把结果说成人话
    final = _llm().invoke(history)

    # 15) 四块按顺序摆出来，正好对应上面 1→14 的流程
    return _blocks(
        ('模型的决定（它没有执行，只是在「说」）', '\n'.join(decided)),
        ('我们自己执行，拿到', '\n'.join(executed)),
        ('第二轮真正发出去的消息', _render(history)),
        ('模型的最终回答', final.content),
    )


# =========================================================
# ④ 上下文：模型自己没有记忆，是我们在每次请求里把历史一起发过去
# =========================================================
def demo_context(payload):
    """「它记得我」是个错觉：模型是无状态的，记忆是我们每次重新发一遍。"""
    # 1) 从 langchain_core 拿三种消息类型，它们就是「对话历史」的积木
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    # 2) 第一轮说一件关于自己的事（这件事实只存在于我们这边的消息列表里）
    fact = _clip(payload.get('input')) or '我叫小林，最近在学 LangChain'

    # 3) 第二轮的追问：不带上文的话，模型根本无从回答
    follow_up = '我叫什么？顺便说一句我是干什么的。'

    # 4) 先把第一轮真的跑一遍，拿到它真实的回答 —— 后面要把它当作历史原样发回去
    first = _llm(0.2).invoke(fact)

    # 5) ❌ 反面示范：只发这一句追问，不给任何背景。
    #    注意这里是一次全新的请求，跟我们上一轮的对话没有任何关系
    blind = _llm(0.2).invoke(follow_up)

    # 6) ✅ 正确做法：把整串消息一起发过去。
    #    SystemMessage 是角色设定（每轮都可以带），
    #    HumanMessage 是我说的话，AIMessage 是它上一轮的回答
    messages = [
        SystemMessage('你是我的助手，回答简短一点。'),
        HumanMessage(fact),                 # 第一轮我说的话
        AIMessage(first.content),           # 第一轮它的回答（放回历史里）
        HumanMessage(follow_up),            # 第二轮我的追问
    ]

    # 7) 同一个模型、同一个问题，只是多带了历史，回答就完全不一样了
    aware = _llm(0.2).invoke(messages)

    # 8) 把两种结果并排摆出来，差别一眼就能看见
    return _blocks(
        ('第 1 轮 · 我说', fact),
        ('第 1 轮 · 它回答', first.content),
        ('第 2 轮 · 我追问', follow_up),
        ('❌ 不带上下文时它的回答', blind.content),
        ('✅ 带上上下文时它的回答', aware.content),
        ('带上下文时真正发出去的消息', _render(messages)),
        ('一句话结论',
         '模型是无状态的；所谓「记忆」是我们每一轮都把历史重新发一遍，不是它自己存下来的。'
         '历史越长越贵，所以真实产品里要做截断或摘要'),
    )

# =========================================================
# 用例登记表：页面左侧的四个标签页就是按这里生成的
#   fn      真正要执行的函数
#   source  这一段要不要在页面上展示源码（就是上面那个函数本身，顺序 = 阅读顺序）
# =========================================================
CASES = [
    {
        'key': 'chat',
        'name': '① 大模型调用',
        'desc': '最基础的一步：发一句话，拿回一个回答。这一步要看清「返回的是什么对象」。',
        'points': ['ChatOpenAI', 'invoke()', 'AIMessage', '.content', 'token 用量', 'stream / batch'],
        'input_label': '你想问模型什么',
        'placeholder': '用一句话说明什么是 LangChain',
        'fn': demo_chat,
        'source': [demo_chat],
    },
    {
        'key': 'prompt',
        'name': '② 提示词模板 + 链',
        'desc': '把会变的部分挖成占位符，再用竖线把几步串成一条链。这是 LangChain 最常用的写法。',
        'points': ['ChatPromptTemplate', '{占位符}', 'system / user', 'LCEL 竖线 |', 'StrOutputParser'],
        'input_label': '让模型解释什么',
        'placeholder': '为什么洗完澡会觉得更清醒',
        'fn': demo_prompt,
        'source': [demo_prompt],
    },
    {
        'key': 'tools',
        'name': '③ 工具调用',
        'desc': '模型自己决定「调哪个工具、参数填什么」，但真正执行的是我们的代码。重点看白名单那一步。',
        'points': ['@tool 装饰器', 'docstring 就是工具说明', 'bind_tools', 'tool_calls', 'ToolMessage', 'tool_call_id'],
        'input_label': '问一个需要「查」或「算」的问题',
        'placeholder': '现在几点？另外 37 加 58 等于几？',
        'fn': demo_tools,
        'source': [_build_tools, demo_tools],
    },
    {
        'key': 'context',
        'name': '④ 上下文（多轮对话）',
        'desc': '同一个问题，不带历史和带历史，答案完全不同。看完你就明白「记忆」是怎么来的。',
        'points': ['消息列表', 'SystemMessage', 'HumanMessage', 'AIMessage', '无状态', '历史越长越贵'],
        'input_label': '第一轮：先告诉它一件关于你的事',
        'placeholder': '我叫小林，最近在学 LangChain',
        'fn': demo_context,
        'source': [demo_context],
    },
]

CASES_BY_KEY = {c['key']: c for c in CASES}


# =========================================================
# 把「真正在跑的那份代码」读出来给页面展示
# =========================================================
def _source_of(obj):
    """读一段真实源码（含注释）。

    @tool 装饰过的对象，真函数藏在 .func 上，所以要绕一下；
    读不出来也不抛错，返回一句说明就行 —— 展示不出源码不该让整页挂掉。
    """
    target = getattr(obj, 'func', obj)
    try:
        code = inspect.getsource(target)
    except (OSError, TypeError):
        return '# 这段源码读不出来，可以直接看项目里的 langchain_test.py\n'
    # 文件在 Windows 上可能是 \r\n，统一成 \n，页面的 <pre> 才不会被多出来的空行撑开
    code = code.replace('\r\n', '\n').replace('\r', '\n')
    # textwrap.dedent 去掉函数体整体那段缩进，不然贴到页面上左边会空一大片
    return textwrap.dedent(code).strip('\n') + '\n'


def _cases_payload():
    """页面要的用例清单（含源码）。源码每次现读，改了代码页面自动跟着变。"""
    items = []
    for case in CASES:
        items.append({
            'key': case['key'],
            'name': case['name'],
            'desc': case['desc'],
            'points': case['points'],
            'input_label': case['input_label'],
            'placeholder': case['placeholder'],
            # 一个用例可能要看几段代码（比如工具那个：先看怎么造工具，再看怎么用）
            'code': '\n\n'.join(_source_of(fn) for fn in case['source']),
        })
    return items


# =========================================================
# 接口里用的小工具
# =========================================================
def _user(request):
    """登录用户名：由 LoginGate 中间件写在 request.state 上，没登录根本走不到路由。"""
    return getattr(request.state, 'user', '') or ''


async def _payload(request):
    """把请求体读成 dict；不是 JSON 或格式不对就当空字典，绝不因此 500。"""
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _fail(message, status=200):
    """失败统一长这样：ok=False + 一句人话，前端只认这个结构。"""
    return JSONResponse({'ok': False, 'message': message, 'blocks': []}, status_code=status)


# =========================================================
# 跑一个用例，并落一条库
# =========================================================
def _save(user, case, payload, blocks):
    """把这次运行存进 SQLite。存不进去（比如文件系统只读）也不影响这次结果。"""
    # 标题取「用例名 + 输入摘要」，右侧列表一眼能认出是哪次
    title = '%s｜%s' % (case['name'], _clip(payload.get('input'), 40) or '默认输入')
    body = {
        'case': case['key'],
        'case_name': case['name'],
        'input': _clip(payload.get('input')),
        'blocks': blocks,
    }
    try:
        rec = storage.add_record(TOOL, user, title, body)     # 走统一的 storage.py
    except storage.StorageUnavailable:
        return None                                           # 存库失败不算事故
    return {'id': rec['id'], 'title': rec['title'], 'created_at': rec['created_at']}


def _do_run(payload, user):
    """一个用例的完整流程：校验 -> 执行 -> 入库 -> 返回。"""
    # 1) 两个前置条件先检查，缺哪个就说缺哪个（页面会照原样显示这句话）
    if not _LC_OK:
        return _fail(NO_LC)
    if not llm_key():
        return _fail(NO_KEY)

    # 2) 用例 key 必须在登记表里，防止前端乱传
    case = CASES_BY_KEY.get(str(payload.get('case') or ''))
    if case is None:
        return _fail('没有这个用例，刷新页面重试。')

    # 3) 真正执行。模型报错、网络超时、Key 失效……全在这一层收成一句人话
    try:
        blocks = case['fn'](payload)
    except Exception as exc:
        return _fail('跑失败了：%s' % str(exc)[:180])

    # 4) 成功才入库：失败也存一条的话，历史里全是红的，反而不想看了
    record = _save(user, case, payload, blocks)

    # 5) 把结果和「刚存的那条」一起回给页面，前端不用再请求一次
    return JSONResponse({'ok': True, 'blocks': blocks, 'message': '', 'record': record})


# =========================================================
# 路由
# =========================================================
@router.get('/langchain-test', response_class=HTMLResponse)
def langchain_test_page():
    """返回页面 HTML；模板文件丢了也不白屏，给一句人话。"""
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="font-family:system-ui;padding:24px">'
        '<h2>模板文件缺失</h2><p>请确认 templates/langchain-test.html 存在。</p>'
        '</body></html>')


@router.get('/langchain-test/api/cases')
def langchain_test_cases():
    """用例清单 + 每个用例要展示的源码 + 公共代码 + 当前环境状态。"""
    return JSONResponse({
        'ok': True,
        'cases': _cases_payload(),
        'common': _source_of(_llm),          # 「模型是怎么来的」这段单独展示一次
        'lc_ok': _LC_OK,                     # langchain 装了没
        'key_ok': bool(llm_key()),           # Key 配了没
        'model': llm_model(),                # 只回模型名，绝不回 Key
    })


@router.post('/langchain-test/api/run')
async def langchain_test_run(request: Request):
    """跑一个用例。里面是同步的模型调用，必须丢线程池，不能卡住事件循环。"""
    return await run_in_threadpool(_do_run, await _payload(request), _user(request))


@router.get('/langchain-test/api/history')
def langchain_test_history(request: Request):
    """最近跑过的记录（只回列表需要的字段，blocks 等点开再看）。"""
    try:
        rows = storage.list_records(TOOL, _user(request), HISTORY_MAX)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': '读不到数据库：%s' % exc, 'items': []})
    items = []
    for row in rows:
        body = row.get('payload') or {}
        items.append({
            'id': row['id'],
            'title': row['title'],
            'created_at': row['created_at'],
            'case': body.get('case', ''),          # 用来在列表上标是哪个用例
            'case_name': body.get('case_name', ''),
        })
    return JSONResponse({'ok': True, 'items': items})


@router.get('/langchain-test/api/history/{rid}')
def langchain_test_history_one(rid: int, request: Request):
    """点历史里的某一条，把它当时的结果完整取回来。"""
    try:
        row = storage.get_record(_user(request), rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        # 注意：storage.get_record 带了 user 条件，别人的记录这里也是「找不到」
        return JSONResponse({'ok': False, 'message': '没这条记录（或者它不属于当前账号）。'},
                            status_code=404)
    body = row.get('payload') or {}
    return JSONResponse({
        'ok': True,
        'id': row['id'],
        'title': row['title'],
        'created_at': row['created_at'],
        'case': body.get('case', ''),
        'case_name': body.get('case_name', ''),
        'input': body.get('input', ''),
        'blocks': body.get('blocks') or [],
    })


@router.delete('/langchain-test/api/history/{rid}')
def langchain_test_history_delete(rid: int, request: Request):
    """删一条。storage 返回 0 说明这条不存在或不属于当前账号，要当 404 处理，不能当成功。"""
    try:
        changed = storage.delete_record(_user(request), rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not changed:
        return JSONResponse({'ok': False, 'message': '没这条记录，可能已经被删了。'},
                            status_code=404)
    return JSONResponse({'ok': True})


# =========================================================
# 单独运行本文件时（python langchain_test.py）只挂这一页，方便调试
# =========================================================
if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='LangChain 测试台', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8011)
