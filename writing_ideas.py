# -*- coding: utf-8 -*-
# 写作选题工具（页面在 /writing-ideas）
# ------------------------------------------------------------
# 解决的是「想写点什么，但脑子里是空的」：不用先想清楚，给一点线索就行，
# 什么都不给也能出一批。生成走大模型；没配 Key 时用内置的「角度 × 素材」
# 配方兜底，照样能出结构化选题，不会白屏。
#
# 两条接口：
#   POST /writing-ideas/api/ideas    出一批选题（默认 8 个）
#   POST /writing-ideas/api/develop  把其中一个深挖成「写作启动包」
#
# 数据都留在浏览器 localStorage（wr_ 前缀），后端不落库。
#
# 后面想继续强化，几个现成的方向：
#   - 选题日历：把选题按「什么时候适合发」排开，避免攒一堆不写
#   - 反馈回路：记下哪条真写出来了、效果如何，下次优先生成同类型
#   - 素材库：单独维护「让我有反应的事」，生成时优先调用而不是临时输入
import json
import random
import re
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

try:
    from model_config import llm_endpoint, llm_key, llm_model
except Exception:  # 单独拷贝本文件运行时不带配置也能用（只是走配方）
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'writing-ideas.html'

MAX_IDEAS = 8
MAX_TITLE = 40

# =========================================================
# 选项：体裁与味道，前后端共用一份，前端只负责显示
# =========================================================
FORMS = [
    {'k': 'wechat', 'l': '公众号长文', 'icon': '📰'},
    {'k': 'xiaohongshu', 'l': '小红书', 'icon': '📕'},
    {'k': 'tech', 'l': '技术博客', 'icon': '🧩'},
    {'k': 'zhihu', 'l': '知乎回答', 'icon': '💬'},
    {'k': 'note', 'l': '短评 / 朋友圈', 'icon': '💭'},
    {'k': 'review', 'l': '复盘', 'icon': '🔁'},
    {'k': 'email', 'l': '邮件 / 汇报', 'icon': '✉️'},
    {'k': 'newsletter', 'l': '周报 / Newsletter', 'icon': '📮'},
]
FLAVORS = [
    {'k': 'contrarian', 'l': '反常识'},
    {'k': 'story', 'l': '讲故事'},
    {'k': 'howto', 'l': '干货方法'},
    {'k': 'rant', 'l': '吐槽'},
    {'k': 'reflect', 'l': '反思'},
    {'k': 'data', 'l': '用数据说话'},
    {'k': 'explain', 'l': '把一件事讲清'},
    {'k': 'warm', 'l': '温柔陪伴'},
]
_FORM_LABEL = dict((x['k'], x['l']) for x in FORMS)
_FLAVOR_LABEL = dict((x['k'], x['l']) for x in FLAVORS)

# =========================================================
# 兜底配方：12 种「切入角度」，每种带标题/观点/提纲/开头/要补的素材模板
# ---------------------------------------------------------
# {s} = 素材（一件事、一个说法、一个困扰），{f} = 体裁
# 目的不是替代大模型，而是让没配 Key 的人也能拿到能直接开写的东西
# =========================================================
ANGLES = [
    {
        'l': '反常识',
        'title': '{s}，其实不是你以为的那样',
        'claim': '大家对「{s}」的第一反应基本都是错的，我想说清楚错在哪。',
        'angle': '先摆出那个最流行的说法，再当场把它拆掉',
        'why': '越没人怀疑的说法，写出来越有转发理由',
        'outline': ['先写大家默认的说法长什么样，举一个最常见的例子',
                    '说清这个说法在什么条件下不成立',
                    '给出我自己现在用的判断标准'],
        'hook': '「关于{s}，我被纠正过三次，最后一次是我自己动的手。」',
        'need': '一个具体到时间、地点、人物的反例',
    },
    {
        'l': '踩坑复盘',
        'title': '我在{s}上踩过的三个坑',
        'claim': '这三个坑我都真踩过，写出来是想让别人少走一遍。',
        'angle': '不写成功经验，只写失败过程，把代价写在明面上',
        'why': '失败细节比成功经验可信得多，也更容易被同行转发',
        'outline': ['第一个坑：当时我以为问题出在 A，其实出在 B',
                    '第二个坑：花了多少时间、多少钱，怎么发现的',
                    '现在我会怎么开头，具体到第一步做什么'],
        'hook': '「{s}这件事，我一开始完全做错了，错得还挺有代表性。」',
        'need': '三个坑各自的时间点和当时的判断依据',
    },
    {
        'l': '给外行讲清',
        'title': '用一个比喻讲清楚{s}',
        'claim': '把「{s}」讲成外行也能听懂的样子，比讲得专业更难。',
        'angle': '找一个日常生活里的比喻，从头用到尾，不中途换',
        'why': '「讲给外行听」是刚需，人人都要跟不熟这行的人解释自己',
        'outline': ['先写我自己一开始是怎么误解它的',
                    '用一个比喻重新讲一遍，逐条对应',
                    '说清这个比喻在哪里会失效'],
        'hook': '「如果只能用一样东西来解释{s}，我会选厨房里的那个。」',
        'need': '一个能撑完全篇的比喻，提前想好它在哪失效',
    },
    {
        'l': '两种做法对比',
        'title': '{s}的两种做法，差的到底是什么',
        'claim': '同样是做「{s}」，两种做法看起来只差一点，结果差很远。',
        'angle': '并排放两条路径，把分岔点卡在某一句话上',
        'why': '对比结构自带张力，读者会自动站队，评论区自己就长出来了',
        'outline': ['把两种做法的前两步并排写出来，别评价',
                    '指出真正的分岔点在哪一步',
                    '什么情况下该选哪条，给判断条件'],
        'hook': '「做{s}有两种人，前三天看不出区别，三个月后是两个结果。」',
        'need': '两条路径各自的一个真实案例',
    },
    {
        'l': '时间线',
        'title': '{s}：从开始到现在，我经历了什么',
        'claim': '把「{s}」按时间摊开，才发现拐点根本不在我以为的那一天。',
        'angle': '按时间推进，中间夹一句当时的心里的判断',
        'why': '时间线是最容易写、也最容易让人看完的结构',
        'outline': ['起点：当时我为什么要碰这件事',
                    '中间：哪一步开始不对，我当时怎么想的',
                    '现在：回看这段，我会改哪一步'],
        'hook': '「{s}这件事，真正改变方向的不是最后那一下，是中间某个平常的下午。」',
        'need': '三到五个关键时间点，越具体越好',
    },
    {
        'l': '清单',
        'title': '关于{s}，我真正在用的几条',
        'claim': '不讲道理，只列我每天真在用的几条，能照做的那种。',
        'angle': '每条只写一句结论 + 一句怎么做，不铺垫',
        'why': '清单是收藏率最高的形式，读者会存下来反复看',
        'outline': ['开头一句说清这份清单的适用场景',
                    '逐条写，每条一句话结论加一句操作',
                    '最后写哪一条最容易被跳过、为什么不该跳'],
        'hook': '「关于{s}，我看过很多道理，真正留下来的只有这几条。」',
        'need': '至少五条自己验证过、能写清操作步骤的条目',
    },
    {
        'l': '反对主流',
        'title': '我不同意{s}上最流行的那种说法',
        'claim': '「{s}」上最流行的那套说法，我照着做过，结果是反的。',
        'angle': '点名那个说法，说出它成立的前提，再指出前提不成立',
        'why': '明确的反对意见自带讨论度，前提是你得给出替代方案',
        'outline': ['先把我要反对的说法原原本本复述一遍，不曲解',
                    '说清它成立需要什么前提',
                    '我的替代做法，以及它各自的代价'],
        'hook': '「关于{s}，有句话我听了太多次了，今天想认真反驳一次。」',
        'need': '那套说法的出处，以及你实践过的具体结果',
    },
    {
        'l': '拆一个数字',
        'title': '{s}里那个数字，才是最关键的',
        'claim': '「{s}」里真正决定结果的是那个不起眼的数字，不是方法。',
        'angle': '从一个具体数字切进去，把它拆成能感知的量',
        'why': '具体数字比形容词有说服力，也容易被引用',
        'outline': ['那个数字是多少，先说结论',
                    '把它折算成日常能感知的东西',
                    '如果这个数字变了，结论会怎么变'],
        'hook': '「在{s}上，真正决定结果的只有一个数字，大部分人从来不问它。」',
        'need': '一个能查证的数字，以及它的来源',
    },
    {
        'l': '回答一个问题',
        'title': '有人问我{s}怎么办，我是这样回答的',
        'claim': '被问到「{s}」的时候，我发现大部分人真正卡住的不是这件事本身。',
        'angle': '从一次真实提问开场，先纠正问题本身',
        'why': '问答体裁天然好读，提问者多半不只他一个',
        'outline': ['复述那次提问，包括对方当时的处境',
                    '我为什么先反问他一个问题',
                    '给出我的答案，并说明它在什么情况下不适用'],
        'hook': '「有人问我{s}该怎么办，我反问他了一句别的。」',
        'need': '那次提问的真实场景（时间、对方身份、你当时怎么答的）',
    },
    {
        'l': '把小事放大',
        'title': '{s}这件小事，暴露了一个更大的问题',
        'claim': '「{s}」看着是件小事，它背后那套默认规则才是真问题。',
        'angle': '从小到不值一提的细节写起，最后落到结构上',
        'why': '小切口好开头，大结论容易被讨论',
        'outline': ['把那个小场景写到具体、能看见',
                    '指出它其实是某个更大规则的缩影',
                    '这个规则对普通人意味着什么'],
        'hook': '「{s}，看起来是件小事，我盯着它看了很久。」',
        'need': '一个细节足够清楚的场景，最好有对话',
    },
    {
        'l': '把大事缩小',
        'title': '别把{s}想得那么复杂',
        'claim': '「{s}」被讲得太玄了，其实能做的就那几件。',
        'angle': '把一堆术语翻译成人话，给出最小可行动作',
        'why': '「减法类」内容在信息过载的时候最受欢迎',
        'outline': ['先说清它被讲复杂的原因',
                    '剥到只剩最关键的一件事',
                    '今天就能做的第一步，小到不用下决心'],
        'hook': '「关于{s}，我看过太多复杂的说法，其实用不着。」',
        'need': '你自己试过的那个「最小一步」，以及真实反馈',
    },
    {
        'l': '一句话拆开',
        'title': '「{s}」这句话，我越想越不对劲',
        'claim': '「{s}」听起来很对，但把其中两个词换掉，结论就塌了。',
        'angle': '拿一句常见的话反复咀嚼，逐词检验',
        'why': '把熟悉的话重新拆开，读者会有「我怎么没想到」的感觉',
        'outline': ['先写这句话为什么听起来有道理',
                    '指出它的漏洞藏在哪个词上',
                    '换成更准确的说法，会变成什么样'],
        'hook': '「「{s}」这话我信了很多年，直到我自己撞上去。」',
        'need': '那句话的出处，以及你撞上去的那次经历',
    },
]

# 完全没线索时用的通用素材
MATERIALS = [
    '一次返工', '一次被拒绝', '一次买错东西', '一件我拖了半年的事',
    '一个我最近才想明白的道理', '一次会议上的沉默', '一条让我不舒服的评论',
    '一个月度账单', '一次加班到很晚的晚上', '一个我以为很简单结果很麻烦的任务',
    '一次跟家人的争执', '一份自己写的方案被推翻', '一个反复出现的小麻烦',
    '一次别人的成功让我焦虑的瞬间', '一次我硬撑着说「没问题」的时候',
    '一个我装了又卸的软件', '一次搬家', '一次体检报告',
    '一个我教别人时才发现自己没懂的词', '一次排了很久的队',
]


# =========================================================
# 上下文整理
# =========================================================
def _clip(text, limit):
    s = re.sub(r'\s+', ' ', str(text or '')).strip()
    return s[:limit]


def _pick_keys(raw, allowed):
    if not isinstance(raw, list):
        return []
    out = []
    for x in raw:
        k = str(x).strip()
        if k in allowed and k not in out:
            out.append(k)
    return out


def _clean_ctx(payload):
    if not isinstance(payload, dict):
        payload = {}
    forms = _pick_keys(payload.get('forms'), _FORM_LABEL)
    flavors = _pick_keys(payload.get('flavors'), _FLAVOR_LABEL)
    return {
        'seed': _clip(payload.get('seed'), 600),
        'audience': _clip(payload.get('audience'), 120),
        'trigger': _clip(payload.get('trigger'), 200),
        'forms': forms,
        'flavors': flavors,
        'exclude': [_clip(x, MAX_TITLE) for x in (payload.get('exclude') or []) if str(x).strip()][:40],
    }


def _materials(ctx):
    """素材池：优先用用户自己给的线索切出来的片段，不够再用通用素材补。"""
    pool = []
    for text in (ctx.get('trigger'), ctx.get('seed')):
        for piece in re.split(r'[，。！？；、,.!?;\n\r]+', text or ''):
            piece = piece.strip()
            if 3 <= len(piece) <= 22:
                pool.append(piece)
    if ctx.get('audience'):
        pool.append('讲给' + ctx['audience'] + '听')
    if not pool:
        pool = list(MATERIALS)
    random.shuffle(pool)
    return pool[:12] or list(MATERIALS)


def _dedupe(ideas, exclude):
    seen = set(x.strip() for x in exclude if x.strip())
    out = []
    for it in ideas:
        t = str(it.get('title') or '').strip()
        if len(t) < 4 or t in seen:
            continue
        seen.add(t)
        out.append(it)
    return out


# =========================================================
# 兜底配方
# =========================================================
def _recipe_ideas(ctx, n, note):
    mats = _materials(ctx)
    forms = ctx['forms'] or ['wechat']
    flavors = [_FLAVOR_LABEL[k] for k in ctx['flavors']]
    angles = list(ANGLES)
    random.shuffle(angles)
    out = []
    for i in range(n * 2):
        if len(out) >= n:
            break
        a = angles[i % len(angles)]
        s = mats[i % len(mats)]
        title = _clip(a['title'].replace('{s}', s), MAX_TITLE)
        if title in [x['title'] for x in out]:
            continue
        form = _FORM_LABEL[forms[i % len(forms)]]
        idea = {
            'title': title,
            'claim': a['claim'].replace('{s}', s),
            'why_now': a['why'],
            'angle': a['angle'].replace('{s}', s),
            'form': form,
            'flavor': (flavors[i % len(flavors)] if flavors else a['l']),
            'level': ['好写', '中等', '要下功夫'][i % 3],
            'outline': [x.replace('{s}', s) for x in a['outline']],
            'hook': a['hook'].replace('{s}', s),
            'material': a['need'],
            'tag': a['l'],
        }
        out.append(idea)
    out = _dedupe(out, ctx['exclude'])[:n]
    return out, note


# =========================================================
# 大模型
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


def _post_llm(system, user, max_tokens, temperature):
    key = llm_key()
    if not key:
        return None, '没配模型 Key，这次用内置配方出题'
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    try:
        resp = requests.post(
            llm_endpoint(), json=body, timeout=(15, 120),
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
        )
    except requests.RequestException as exc:
        return None, '模型请求失败，这次用内置配方：' + str(exc)[:80]
    if resp.status_code >= 400:
        return None, '模型返回错误 ' + str(resp.status_code) + '，这次用内置配方'
    try:
        return resp.json()['choices'][0]['message']['content'], ''
    except (ValueError, KeyError, IndexError, TypeError):
        return None, '模型返回格式异常，这次用内置配方'


def _ctx_lines(ctx):
    lines = []
    if ctx['seed']:
        lines.append('我正在做 / 我关心的事：' + ctx['seed'])
    if ctx['audience']:
        lines.append('想写给谁看：' + ctx['audience'])
    if ctx['trigger']:
        lines.append('最近让我有反应的一件事：' + ctx['trigger'])
    if ctx['forms']:
        lines.append('想要的体裁：' + '、'.join(_FORM_LABEL[k] for k in ctx['forms']))
    if ctx['flavors']:
        lines.append('想要的味道：' + '、'.join(_FLAVOR_LABEL[k] for k in ctx['flavors']))
    if ctx['exclude']:
        lines.append('下面这些选题已经有了，不要重复：' + '；'.join(ctx['exclude']))
    return lines


IDEAS_SYSTEM = '\n'.join([
    '你是写作者的选题编辑。用户往往说不清自己想写什么，你的任务是从一点点线索里',
    '找出「值得写、写得动、有人看」的选题。',
    '硬性要求：',
    '1. 标题必须具体、有信息量，禁止「浅谈」「那些事」「你应该知道」「论」这类空话，',
    '   禁止励志鸡汤，标题不超过 24 个字；',
    '2. claim 要是一个能被反对的观点，不能是「很重要」「值得关注」这种废话；',
    '3. outline 恰好 3 条，每条是一个能直接往下写的要点，不是标题的重复；',
    '4. hook 是文章的第一句话，要让人想读第二句；',
    '5. material 写「要写这个还得先补什么」，具体到可执行（要查什么数据、要回忆哪件事）；',
    '6. 选题之间角度要拉开，不要八个都是同一个套路；',
    '7. 只输出 JSON，不要 Markdown 代码块，不要任何解释。',
    '输出格式：',
    '{"ideas":[{"title":"","claim":"","why_now":"","angle":"","form":"","flavor":"",'
    '"level":"好写|中等|要下功夫","outline":["","",""],"hook":"","material":""}]}',
])


def _ask_ideas_llm(ctx, n):
    user = '\n'.join(_ctx_lines(ctx) + [
        '请给我 ' + str(n) + ' 个选题。',
        'form 从这些里选一个填：' + '、'.join(_FORM_LABEL.values()) + '。',
        'level 只能是：好写、中等、要下功夫。',
    ])
    raw, note = _post_llm(IDEAS_SYSTEM, user, 3200, 0.95)
    if raw is None:
        return None, note
    rows = _extract_json(raw).get('ideas')
    if not isinstance(rows, list):
        return None, '模型返回的内容解析不了，这次用内置配方'
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _clip(row.get('title'), MAX_TITLE)
        claim = _clip(row.get('claim'), 200)
        if len(title) < 4 or len(claim) < 4:
            continue
        outline = [_clip(x, 120) for x in (row.get('outline') or []) if str(x).strip()][:3]
        out.append({
            'title': title,
            'claim': claim,
            'why_now': _clip(row.get('why_now'), 140),
            'angle': _clip(row.get('angle'), 140),
            'form': _clip(row.get('form'), 20),
            'flavor': _clip(row.get('flavor'), 20),
            'level': _clip(row.get('level'), 10),
            'outline': outline,
            'hook': _clip(row.get('hook'), 200),
            'material': _clip(row.get('material'), 200),
            'tag': _clip(row.get('angle'), 12),
        })
    if len(out) < 3:
        return None, '模型给的选题太少，这次用内置配方'
    return _dedupe(out, ctx['exclude'])[:n], ''


PACK_SYSTEM = '\n'.join([
    '你是写作者的选题编辑。用户挑中了一个选题，你要把它拆成一份可以直接开写的启动包。',
    '硬性要求：',
    '1. 每一步都要具体：说给谁看，就写清他的处境；说查证据，就写清查什么、去哪查；',
    '2. titles 给 3 个备选标题，风格要不一样（一个直白、一个带冲突、一个带数字或场景）；',
    '3. outline 是 4 节，每节 h 是小标题，points 是 2 条要点；',
    '4. objections 是写完可能被反驳的地方，每条后面用一句话说明你打算怎么回应；',
    '5. series 是这个选题可以往外延伸的 2 个后续选题；',
    '6. 只输出 JSON，不要 Markdown 代码块，不要解释。',
    '输出格式：',
    '{"audience":"","titles":["","",""],'
    '"outline":[{"h":"","points":["",""]}],'
    '"evidence":["","",""],"objections":["","",""],"ending":"","series":["",""]}',
])


def _ask_pack_llm(idea, ctx):
    user = '\n'.join(_ctx_lines(ctx) + [
        '我选中的选题：' + idea.get('title', ''),
        '核心观点：' + idea.get('claim', ''),
        '切入点：' + idea.get('angle', ''),
        '体裁：' + idea.get('form', ''),
        '请把它拆成写作启动包。',
    ])
    raw, note = _post_llm(PACK_SYSTEM, user, 2400, 0.8)
    if raw is None:
        return None, note
    data = _extract_json(raw)
    if not data.get('outline'):
        return None, '模型返回的内容解析不了，这次用内置模板'
    outline = []
    for sec in data.get('outline') or []:
        if not isinstance(sec, dict):
            continue
        outline.append({
            'h': _clip(sec.get('h'), 60),
            'points': [_clip(x, 160) for x in (sec.get('points') or []) if str(x).strip()][:3],
        })
    pack = {
        'audience': _clip(data.get('audience'), 200),
        'titles': [_clip(x, MAX_TITLE) for x in (data.get('titles') or []) if str(x).strip()][:3],
        'outline': outline[:5],
        'evidence': [_clip(x, 160) for x in (data.get('evidence') or []) if str(x).strip()][:5],
        'objections': [_clip(x, 180) for x in (data.get('objections') or []) if str(x).strip()][:4],
        'ending': _clip(data.get('ending'), 240),
        'series': [_clip(x, MAX_TITLE) for x in (data.get('series') or []) if str(x).strip()][:3],
    }
    return pack, ''


def _recipe_pack(idea, ctx, note):
    """模型不可用时的启动包：用选题自己的字段撑起来，不是空壳。"""
    title = idea.get('title', '')
    claim = idea.get('claim', '')
    outline = idea.get('outline') or []
    pack = {
        'audience': ((ctx['audience'] and ('主要写给：' + ctx['audience'])) or
                     '跟你有同样处境、但还没动手的人；他会先看你有没有真做过'),
        'titles': [title,
                   claim[:MAX_TITLE] if claim else title,
                   (idea.get('tag', '') + '：' + title)[:MAX_TITLE]],
        'outline': [
            {'h': '开头：把场景摆出来',
             'points': [idea.get('hook') or ('从' + title + '这件事的现场写起'),
                        '一句话交代你在其中有过的错误判断']},
            {'h': '中间：说清你的观点', 'points': outline[:2] or ['把你的结论写在最前面，别绕']},
            {'h': '展开：给出依据', 'points': [idea.get('material') or '补一个具体例子',
                                              '说清这个结论在什么条件下不成立']},
            {'h': '结尾：落到可执行的一步',
             'points': ['给一个今天就能做的小动作', '留一句让人想反驳的话']},
        ],
        'evidence': [idea.get('material') or '一个具体到时间地点的例子',
                     '一个能查证的数字或出处',
                     '一段别人说过、你可以直接引用的话'],
        'objections': ['「你说得太绝对了」——补上适用条件',
                       '「这是你运气好」——把当时的具体代价写出来',
                       '「道理我都懂」——落到第一步怎么做'],
        'ending': '别总结大道理，回到开头那个场景，说一句现在你会怎么做。',
        'series': ['把这篇里没写透的一段单独展开',
                   '换一个完全相反的立场再写一遍'],
    }
    return pack, note


# =========================================================
# 两个动作
# =========================================================
def _ideas(payload):
    ctx = _clean_ctx(payload)
    try:
        n = int(payload.get('n') or MAX_IDEAS)
    except (TypeError, ValueError):
        n = MAX_IDEAS
    n = max(3, min(MAX_IDEAS, n))
    ideas, note = _ask_ideas_llm(ctx, n)
    source = 'llm'
    if not ideas:
        source = 'recipe'
        ideas, note = _recipe_ideas(ctx, n, note or '这次用内置配方出题')
    return JSONResponse({'ok': True, 'source': source, 'note': note,
                         'count': len(ideas), 'ideas': ideas})


def _develop(payload):
    if not isinstance(payload, dict):
        payload = {}
    idea = payload.get('idea') or {}
    if not isinstance(idea, dict) or len(str(idea.get('title') or '').strip()) < 2:
        return JSONResponse({'ok': False, 'message': '先选一个选题'})
    idea = {
        'title': _clip(idea.get('title'), MAX_TITLE),
        'claim': _clip(idea.get('claim'), 200),
        'angle': _clip(idea.get('angle'), 140),
        'form': _clip(idea.get('form'), 20),
        'outline': [_clip(x, 120) for x in (idea.get('outline') or [])][:3],
        'hook': _clip(idea.get('hook'), 200),
        'material': _clip(idea.get('material'), 200),
        'tag': _clip(idea.get('tag'), 12),
    }
    ctx = _clean_ctx(payload)
    pack, note = _ask_pack_llm(idea, ctx)
    source = 'llm'
    if not pack:
        source = 'recipe'
        pack, note = _recipe_pack(idea, ctx, note or '这次用内置模板')
    return JSONResponse({'ok': True, 'source': source, 'note': note, 'pack': pack})


# =========================================================
# 路由
# =========================================================
@router.get('/writing-ideas', response_class=HTMLResponse)
def writing_ideas_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="font-family:system-ui"><h2>模板文件缺失</h2>'
                '<p>请确认 templates/writing-ideas.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.post('/writing-ideas/api/ideas')
async def writing_ideas_api(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    # 调大模型是同步阻塞请求，放线程池里跑，别堵住事件循环
    return await run_in_threadpool(_ideas, payload)


@router.post('/writing-ideas/api/develop')
async def writing_develop_api(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return await run_in_threadpool(_develop, payload)


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='写作选题', description='没灵感也能开始', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8006)
