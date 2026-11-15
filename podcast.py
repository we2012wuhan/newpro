# -*- coding: utf-8 -*-
"""
AI 双人播客（文字稿 -> 双音色语音合成 -> 一条 MP3）
=====================================================
页面在 /podcast，接口都在 /podcast/api/*。

做法
----
1. 一次模型调用写出完整逐字稿（JSON：标题 + 简介 + A/B 交替的 turns）；
2. 每一段按音色分别调用火山引擎 TTS，A 用女声、B 用男声；
3. 用 ffmpeg 把各段转成统一的 24kHz 单声道 WAV，中间按 pause 插入静音，
   最后压成一条 MP3，顺手做一次响度归一。

为什么不是两个 AI 自己聊
------------------------
两个模型互相接话很难控节奏、时长和事实边界，费用也翻倍。
这里用「一个编剧写完整脚本」的思路：故事线稳定，失败时只需要重跑一次。

任务为什么异步
--------------
5 分钟节目有二十多段语音，每段几秒，加起来要一两分钟。浏览器等长请求
容易被代理掐断，所以沿用文生图的套路：先落 queued，后台单线程依次处理，
页面轮询进度。进程重启时把 running 标成失败，不自动重试（可能已产生费用）。

隐私与边界
----------
主题和资料会发送给文本模型；逐字稿会发送给语音合成服务。两者都是服务端
调用，Key 不进前端。生成结果默认保留 7 天，需要长期保存请及时下载。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from urllib.parse import quote

import requests
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

import model_config
import storage
import volc_tts

router = APIRouter()

TOOL = 'podcast'
MAX_TOPIC = 200
MAX_MATERIAL = 8000
MIN_TURNS = 4
MAX_TURNS = 160
MAX_TURN_CHARS = 360
MAX_TOTAL_CHARS = 6500
TTS_CHAR_LIMIT = 380

# 真实对话里大量回合只有一两个字（“嗯”“对”“啊？”）。整理脚本时别把它们过滤掉，
# 只放行常见附和词，避免模型吐出的单个标点或残字混进音频。
_BACKCHANNELS = set('嗯啊呃哦噢对是好行哈嘿哎诶呀哇')
_EMOTION_ALIASES = {
    '开心': 'happy', '高兴': 'happy', '愉快': 'happy', '轻松': 'happy',
    '惊讶': 'surprised', '好奇': 'surprised',
    '平静': 'calm', '认真': 'calm', '沉稳': 'calm',
    '温柔': 'tender', '亲切': 'tender', '柔和': 'tender',
    '兴奋': 'excited', '激动': 'excited',
    '难过': 'sad', '低落': 'sad', '无奈': 'sad',
    '中性': '', 'neutral': '', '普通': '',
}
_EMOTIONS = {'happy', 'surprised', 'calm', 'tender', 'excited', 'sad'}
SAMPLE_RATE = 24000
DEFAULT_DAILY_LIMIT = 60          # 单位：分钟/天（按生成时长累加）
DEFAULT_TTL_DAYS = 7
LLM_TIMEOUT = (10, 180)
MAX_AUDIO_BYTES = 200 * 1024 * 1024

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'podcast.html'
_JOB_ID_RE = re.compile(r'^[0-9a-f]{32}$')

HOSTS = {
    'A': {
        'name': '小唯',
        'voice': 'zh_female_vv_uranus_bigtts',
        'speed': 1.03,
        'context': '像年轻女性朋友私下聊天：轻松、好奇、反应快、带点笑意，不要播音腔。',
    },
    'B': {
        'name': '刘飞',
        'voice': 'zh_male_liufei_uranus_bigtts',
        'speed': 0.98,
        'context': '像成熟一点的男性朋友私下聊天：松弛、口语化、偶尔吐槽，不要新闻播报腔。',
    },
}

TONES = {
    'casual': ('轻松闲聊', '像两个朋友边喝咖啡边聊，语气自然，可以开玩笑，但别油腻。'),
    'deep': ('深度对谈', '认真拆解问题，有观点、有追问，也允许承认不确定。'),
    'news': ('新闻解读', '先交代背景，再解释影响，最后给普通人一个观察角度。'),
    'story': ('故事叙事', '用具体场景和转折推进，少讲道理，多讲发生了什么。'),
    'science': ('科普拆解', '把复杂概念讲清楚，多用生活类比，专业词第一次出现要解释。'),
}

# 目标字数按实测语速反推：当前两把音色约 5.2 字/秒（含标点），
# 再算上每段之间的停顿，3 / 5 / 8 分钟分别约需 1000 / 1700 / 2700 字。
# 模型经常写短，提示里仍按这个目标要求，明显偏短时 _maybe_expand 会再补一次。
DURATIONS = {
    3: {'label': '3 分钟', 'chars': 1000, 'turns': '32～46',
        'max_tokens': 4000},
    5: {'label': '5 分钟', 'chars': 1700, 'turns': '52～72',
        'max_tokens': 5600},
    8: {'label': '8 分钟', 'chars': 2700, 'turns': '78～105',
        'max_tokens': 7600},
}

SCRIPT_SYSTEM = (
    '你是一位中文播客制作人，写的不是文章，而是两个朋友真正聊天时会说出来的话。'
    '你只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释或前后缀。'
)

_TURN_KEYS = ('turns', 'dialogue', 'script', 'lines')
_TEXT_KEYS = ('text', 'content', 'line', 'say', 'speech')
_SPEAKER_KEYS = ('speaker', 'role', 'who', 'host')


class PodcastError(RuntimeError):
    """能直接展示给用户的一句话错误。"""


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(high, max(low, value))


def _daily_limit() -> int:
    return _env_int('PODCAST_DAILY_LIMIT', DEFAULT_DAILY_LIMIT, 1, 600)


def _ttl_days() -> int:
    return _env_int('PODCAST_TTL_DAYS', DEFAULT_TTL_DAYS, 1, 90)


def _today_start() -> str:
    return time.strftime('%Y-%m-%d 00:00:00')


def _cutoff(days: int) -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S',
                         time.localtime(time.time() - days * 86400))


def _short(value, limit: int = 300) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _text(value, limit: int) -> str:
    text = str(value or '')
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text).strip()
    return text[:limit]


def _output_root() -> Path:
    raw = (os.environ.get('PODCAST_DIR') or '').strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return storage.db_path().resolve().parent / 'generated' / 'podcast'


def _user_key(user: str) -> str:
    return hashlib.sha256(user.encode('utf-8')).hexdigest()[:16]


def _job_dir(user: str, job_id: str) -> Path:
    job_id = str(job_id or '')
    if not _JOB_ID_RE.match(job_id):
        raise ValueError('任务 ID 不正确')
    return _output_root() / _user_key(user) / job_id


def _remove_job_dir(user: str, job_id: str) -> None:
    try:
        shutil.rmtree(_job_dir(user, job_id), ignore_errors=True)
    except ValueError:
        pass


def _cleanup_user(user: str) -> None:
    cutoff = _cutoff(_ttl_days())
    try:
        rows = storage.podcast_job_expired(user, cutoff)
    except storage.StorageUnavailable:
        return
    for row in rows:
        if row.get('status') in ('queued', 'running'):
            continue
        _remove_job_dir(user, row['id'])
        try:
            storage.podcast_job_delete(user, row['id'])
        except storage.StorageUnavailable:
            return


# ---------------------------------------------------------------
# 文本模型：生成结构化逐字稿
# ---------------------------------------------------------------
def _tone_instruction(tone: str) -> str:
    return (TONES.get(tone) or TONES['casual'])[1]


def _script_prompt(topic: str, material: str, tone: str, minutes: int) -> str:
    spec = DURATIONS.get(minutes) or DURATIONS[5]
    target = spec['chars']
    parts = [
        '请写一期双人播客的完整逐字稿。它不是一篇文章被拆成对话，而是两个人真的在聊天。',
        '',
        '【主题】' + topic,
        '【目标】约 %d 分钟；正文合计不少于 %d 个汉字，目标 %d 字左右；总回合数约 %s 段。'
        '篇幅不够时增加具体例子和回合，不要用重复句凑字数。'
        % (minutes, int(target * 0.85), target, spec['turns']),
        '【风格】%s：%s' % (TONES.get(tone, TONES['casual'])[0], _tone_instruction(tone)),
        '',
        '【主持人】',
        'A：小唯（女声）。好奇心强、反应快，爱追问，也会插话抢话。口头禅像“等一下”'
        '“你这么说我想起来”“真的假的”“为什么呀”。她负责把话题往前拱，不做总结陈词。',
        'B：刘飞（男声）。稳一点，但爱吐槽，喜欢用具体例子和亲身经历解释。口头禅像'
        '“说白了”“我前两天就遇到个事”“我倒不这么看”“你听我说完”。偶尔被 A 打断。'
        '两人要有真实的立场差异，不要从头到尾互相附和。',
        '',
        '【对谈要求】',
        '1. 节奏长短不齐：允许同一人连续说 2～3 段。全文至少安排 8 个回合只写 1～5 个字'
        '（例如“嗯”“对”“啊？”“然后呢”“真的假的”“哈哈”），这些短回合必须单独成段，'
        '不要把它们并进一句长话里；短回合之后可以再接长解释。',
        '2. 每段 1～120 个汉字：短回合 1～5 字，长解释 40～90 字，全文平均 22～35 字。'
        '不要每段一样长，不要一人一句打乒乓球。短回合多不等于节目短：长解释仍要占主要篇幅，'
        '全文总字数必须达到目标，不够就加长解释或增加回合，不要用重复句凑字数。',
        '3. 至少安排：一次具体例子或亲身经历；一次不同意、反驳或补充；一次追问；'
        '一次自然的跑题再拉回来。结尾像聊天自然停住，不要“总之”“最后我想说”式总结。',
        '4. 口语痕迹：可以有“其实”“怎么说呢”“你知道吗”“就……”这类口头禅，'
        '也可以自我修正，例如“我是说——不对，应该是……”。不要每句都主谓宾完整。',
        '5. 语气用标点和措辞表达，不要写舞台提示：被抢话用“——”结尾，犹豫用“……”，'
        '惊讶用“？！”，笑直接写“哈哈”。不要写“（笑）”“【音乐】”“主持人：”，'
        '不要 Markdown、表情符号、网址、广告或付费引导。',
        '6. 不要排比句，不要“首先 / 其次 / 最后”，不要念稿腔。',
        '7. 如果提供了参考资料，只依据资料中的事实展开；没有资料时，避免编造具体数字、'
        '人名、时间、研究结论等无法核实的信息，可以明确说“不确定”。',
    ]
    if material:
        parts += ['', '【参考资料】', material]
    parts += [
        '',
        '【节奏示例】只示范长短回合和接话方式，不要照抄内容，也不要因此把整期写短：',
        'A：我最近发现一个特别离谱的事。',
        'B：你说。',
        'A：我们组这周开了十一个会。真的，十一个。',
        'B：啊？',
        'A：而且有一半根本没必要开……',
        'B：等等，你先说哪个会最离谱。',
        '',
        '【输出格式】严格输出 JSON：',
        '{"title":"不超过 20 字","summary":"一句话简介","turns":['
        '{"speaker":"A","text":"要读出来的话","pause":0.3,'
        '"emotion":"happy","intensity":3},'
        '{"speaker":"B","text":"……","pause":0.15}]}',
        '',
        '字段说明：',
        '- speaker 只能是 A 或 B。',
        '- pause 是这段话之后的停顿时长，单位秒，范围 0.08～1.2：短附和 0.08～0.15，'
        '普通接话 0.2～0.35，话题转换或思考 0.6～1.2。不要每段都一样。',
        '- emotion 可选，只能从 happy / surprised / calm / tender / excited / sad 里选；'
        '大多数回合留空或写 calm，只在明显有情绪时标注。它只给语音合成用，不会被读出来。',
        '- intensity 可选，1～5 的整数，默认 3，表示情绪强度。',
        '- 不要输出 JSON 以外的任何字符。',
    ]
    return '\n'.join(parts)


def _provider_error(resp) -> PodcastError:
    status = getattr(resp, 'status_code', 0)
    detail = ''
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get('error')
        if isinstance(error, dict):
            detail = error.get('message') or error.get('code') or ''
        elif error:
            detail = str(error)
        detail = detail or payload.get('message') or payload.get('msg') or ''
    detail = _short(detail or getattr(resp, 'text', '') or '服务商没有返回具体原因')
    if status in (401, 403):
        return PodcastError('文本模型鉴权失败（HTTP %d），检查服务端的模型 Key。%s'
                            % (status, detail))
    if status == 429:
        return PodcastError('文本模型限流或额度不足（HTTP 429）：%s' % detail)
    return PodcastError('文本模型返回异常（HTTP %d）：%s' % (status, detail))


def _extract_json(raw: str):
    text = (raw or '').strip()
    if not text:
        raise PodcastError('模型没有返回脚本内容，请重试')
    # 去掉常见的 ```json ... ``` 包裹
    fence = re.match(r'^```[a-zA-Z]*\s*\n?(.*?)\n?```\s*$', text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            pass
    raise PodcastError('模型这次没有返回可用的 JSON 脚本，请重试')


def _clean_line(value) -> str:
    text = str(value or '')
    text = re.sub(r'```.*?```', '', text, flags=re.S)
    text = re.sub(r'[*_`#>~|]+', '', text)
    # 模型偶尔还是会塞舞台提示，去掉常见的括号注释和【音乐】一类标记。
    text = re.sub(
        r'[（(](?:笑|笑声|停顿|音乐|掌声|叹气|咳嗽|沉默|旁白|画外音)[^）)]{0,8}[）)]',
        '', text)
    text = re.sub(r'【[^】]{0,12}】', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # 去掉模型偶尔加在开头的「主持人：」「小唯：」
    text = re.sub(r'^(?:A|B|主持人\s*[AB12]?|小唯|刘飞)\s*[:：]\s*', '', text)
    return text.strip()


def _speaker(value, fallback: str) -> str:
    raw = str(value or '').strip().upper()
    if raw in ('A', '1', 'HOST_A', 'HOST1', '小唯', '女', 'FEMALE', 'W'):
        return 'A'
    if raw in ('B', '2', 'HOST_B', 'HOST2', '刘飞', '男', 'MALE', 'M'):
        return 'B'
    return fallback


def _split_text(text: str, limit: int = 220) -> list:
    """按标点把长句切成不超过 limit 的片段，避免 TTS 单次 400 字上限。"""
    text = (text or '').strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks = []
    rest = text
    marks = '。！？!?；;…'
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(ch) for ch in marks)
        if cut < limit // 2:
            comma = max(window.rfind('，'), window.rfind(','))
            cut = comma if comma >= limit // 3 else limit - 1
        chunks.append(rest[:cut + 1].strip())
        rest = rest[cut + 1:].strip()
    if rest:
        chunks.append(rest)
    return [chunk for chunk in chunks if chunk]


def _emotion(value) -> str:
    raw = str(value or '').strip().lower()
    raw = _EMOTION_ALIASES.get(raw, raw)
    return raw if raw in _EMOTIONS else ''


def _intensity(value) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, number))


def _normalize_script(payload, topic: str) -> dict:
    """把模型输出整理成稳定的 turns 列表；格式不对时抛人话错误。"""
    if isinstance(payload, list):
        data = {'turns': payload}
    elif isinstance(payload, dict):
        data = payload
    else:
        raise PodcastError('模型返回的脚本格式不正确，请重试')

    raw_turns = None
    for key in _TURN_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            raw_turns = value
            break
    if raw_turns is None:
        raise PodcastError('模型返回的脚本里没有对谈内容，请重试')

    turns = []
    total_chars = 0
    previous = 'B'          # 第一段默认让 A 说

    for item in raw_turns:
        if len(turns) >= MAX_TURNS:
            break
        raw_emotion, raw_intensity = '', 3
        if isinstance(item, str):
            raw_text, raw_speaker, raw_pause = item, '', 0.3
        elif isinstance(item, dict):
            raw_text = ''
            for key in _TEXT_KEYS:
                if item.get(key):
                    raw_text = item.get(key)
                    break
            raw_speaker = ''
            for key in _SPEAKER_KEYS:
                if item.get(key):
                    raw_speaker = item.get(key)
                    break
            raw_pause = item.get('pause', item.get('gap', 0.3))
            raw_emotion = item.get('emotion') or item.get('mood') or ''
            raw_intensity = item.get('intensity', item.get('emotion_scale', 3))
        else:
            continue

        text = _clean_line(raw_text)
        if not text:
            continue
        speaker = _speaker(raw_speaker, 'A' if previous == 'B' else 'B')
        try:
            pause = float(raw_pause)
        except (TypeError, ValueError):
            pause = 0.3
        pause = round(min(1.2, max(0.08, pause)), 2)
        emotion = _emotion(raw_emotion)
        intensity = _intensity(raw_intensity)

        # 超长段落按标点拆开，保持同一个说话人，停顿放在最后一段。
        pieces = _split_text(text, 220)
        for index, piece in enumerate(pieces):
            if len(turns) >= MAX_TURNS or total_chars >= MAX_TOTAL_CHARS:
                break
            room = MAX_TOTAL_CHARS - total_chars
            if len(piece) > room:
                piece = piece[:room].rstrip()
            # 单字只放行“嗯 / 对 / 啊”这类真实附和，避免标点或残字混进来。
            if len(piece) < 2 and not (len(piece) == 1 and piece in _BACKCHANNELS):
                continue
            turns.append({
                'speaker': speaker,
                'text': piece[:MAX_TURN_CHARS],
                'pause': pause if index == len(pieces) - 1 else 0.15,
                'emotion': emotion,
                'intensity': intensity,
            })
            total_chars += len(piece)
            previous = speaker
        if total_chars >= MAX_TOTAL_CHARS:
            break

    if len(turns) < MIN_TURNS:
        raise PodcastError('模型写出的对谈太短（只有 %d 段），请重新生成' % len(turns))

    title = _clean_line(data.get('title'))[:60] if isinstance(data, dict) else ''
    summary = _clean_line(data.get('summary'))[:160] if isinstance(data, dict) else ''
    return {
        'title': title or topic[:60],
        'summary': summary,
        'turns': turns,
        'chars': total_chars,
    }


def _maybe_expand(script: dict, topic: str, tone: str, minutes: int,
                  model: str) -> dict:
    """初稿明显偏短时再让模型扩写一次；失败就保留初稿，不影响主流程。"""
    spec = DURATIONS.get(minutes) or DURATIONS[5]
    target = spec['chars']
    current = int(script.get('chars') or 0)
    if current >= int(target * 0.9):
        return script

    draft = json.dumps({
        'title': script.get('title') or '',
        'summary': script.get('summary') or '',
        'turns': [{
            'speaker': turn.get('speaker') or 'A',
            'text': turn.get('text') or '',
            'pause': turn.get('pause') or 0.3,
            'emotion': turn.get('emotion') or '',
            'intensity': turn.get('intensity') or 3,
        } for turn in script.get('turns') or []],
    }, ensure_ascii=False)

    prompt = '\n'.join([
        '下面是一期双人播客的初稿，篇幅偏短（约 %d 字），目标是至少 %d 字、'
        '约 %d 字。请在保持原有观点、人物和口语节奏的前提下扩写：'
        % (current, int(target * 0.9), target),
        '1. 把长解释扩展到 40～90 字，补充具体例子、细节、反问或自我修正；',
        '2. 保留 1～5 字的短回合，并让它们单独成段，不要并进长句；',
        '3. 不要重复已有句子，不要加“总之”“最后我想说”式总结；',
        '4. 不要新增无法核实的具体数字、人名或研究结论；',
        '5. 风格保持：%s：%s' % (TONES.get(tone, TONES['casual'])[0],
                              _tone_instruction(tone)),
        '6. 只输出完整的 JSON 对象，格式与初稿一致。',
        '',
        '初稿：',
        draft,
    ])
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SCRIPT_SYSTEM},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.6,
        'max_tokens': spec['max_tokens'],
    }
    if model_config.llm_provider() == 'deepseek':
        body['thinking'] = {'type': 'disabled'}

    try:
        resp = requests.post(
            model_config.llm_endpoint(),
            json=model_config.llm_body(body),
            headers={'Authorization': 'Bearer ' + model_config.llm_key(),
                     'Content-Type': 'application/json'},
            timeout=LLM_TIMEOUT,
        )
        if resp.status_code >= 400:
            return script
        payload = resp.json()
        content = payload['choices'][0]['message']['content']
        if isinstance(content, list):
            content = ''.join(
                str(part.get('text') or '') if isinstance(part, dict) else str(part)
                for part in content)
        expanded = _normalize_script(_extract_json(str(content or '')), topic)
    except Exception:
        return script
    if expanded.get('chars', 0) <= current:
        return script
    expanded['model'] = model
    return expanded


def _llm_script(topic: str, material: str, tone: str, minutes: int) -> dict:
    key = model_config.llm_key()
    if not key:
        raise PodcastError(model_config.llm_missing_hint())
    model = model_config.llm_model()
    spec = DURATIONS.get(minutes) or DURATIONS[5]
    messages = [
        {'role': 'system', 'content': SCRIPT_SYSTEM},
        {'role': 'user', 'content': _script_prompt(topic, material, tone, minutes)},
    ]
    last_error = None

    # 第一次格式不对时再给模型一次机会：把问题说清楚、降低温度重写。
    # 正常情况只会请求一次，不额外增加费用。
    for attempt in (0, 1):
        body = {
            'model': model,
            'messages': messages,
            'temperature': 0.8 if attempt == 0 else 0.4,
            'max_tokens': spec['max_tokens'],
        }
        # DeepSeek 的推理型模型会把 max_tokens 全花在 reasoning 上，
        # 长篇逐字稿很容易拿到空的 content，这里明确关掉思考模式。
        if model_config.llm_provider() == 'deepseek':
            body['thinking'] = {'type': 'disabled'}
        try:
            resp = requests.post(
                model_config.llm_endpoint(),
                json=model_config.llm_body(body),
                headers={'Authorization': 'Bearer ' + key,
                         'Content-Type': 'application/json'},
                timeout=LLM_TIMEOUT,
            )
        except requests.exceptions.Timeout as exc:
            raise PodcastError('写脚本超时，请稍后重试') from exc
        except requests.exceptions.RequestException as exc:
            raise PodcastError('连不上文本模型：%s' % _short(exc)) from exc

        if resp.status_code >= 400:
            raise _provider_error(resp)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise PodcastError('文本模型返回的不是 JSON，请重试') from exc
        try:
            content = payload['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            content = ''
        if isinstance(content, list):
            content = ''.join(str(part.get('text') or '') if isinstance(part, dict) else str(part)
                              for part in content)
        content = str(content or '')

        try:
            script = _normalize_script(_extract_json(content), topic)
            script['model'] = model
            return _maybe_expand(script, topic, tone, minutes, model)
        except PodcastError as exc:
            last_error = exc
            if attempt == 0:
                if content:
                    messages = messages + [
                        {'role': 'assistant', 'content': content[:6000]},
                        {'role': 'user', 'content':
                            '上面的输出不符合要求（%s）。请重新输出一个完整合法的 JSON 对象，'
                            '严格按原格式，turns 至少 12 段，保留短附和和长短不齐的节奏，'
                            '不要输出 JSON 以外的任何字符。'
                            % _short(exc, 120)},
                    ]
                else:
                    messages = messages + [
                        {'role': 'user', 'content':
                            '上一次没有返回任何内容。请直接输出完整合法的 JSON 对象，'
                            '不要输出 JSON 以外的任何字符。'},
                    ]
                continue
            raise
    raise last_error or PodcastError('模型这次没有写出可用的脚本，请重试')


# ---------------------------------------------------------------
# 音频：TTS -> 统一 WAV -> 拼静音 -> MP3
# ---------------------------------------------------------------
def _ffmpeg_exe() -> str:
    raw = (os.environ.get('PODCAST_FFMPEG') or '').strip()
    if raw:
        path = Path(raw).expanduser()
        if path.is_file():
            return str(path)
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).is_file():
            return path
    except Exception:
        pass
    return ''


def _ffmpeg_ready() -> bool:
    return bool(_ffmpeg_exe())


def _run(cmd: list, timeout: int = 180) -> None:
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise PodcastError('音频处理超时，请稍后重试') from exc
    except OSError as exc:
        raise PodcastError('无法启动 ffmpeg：%s' % _short(exc)) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or b'').decode('utf-8', 'ignore')
        detail = _short(detail[-500:]) or '未知错误'
        raise PodcastError('音频处理失败：%s' % detail)


def _to_wav(ffmpeg: str, src: Path, dest: Path) -> None:
    _run([ffmpeg, '-y', '-loglevel', 'error', '-i', str(src),
          '-ac', '1', '-ar', str(SAMPLE_RATE), '-sample_fmt', 's16',
          str(dest)])


def _encode_mp3(ffmpeg: str, src: Path, dest: Path) -> None:
    head = [ffmpeg, '-y', '-loglevel', 'error', '-i', str(src)]
    tail = ['-ar', str(SAMPLE_RATE), '-ac', '1', '-c:a', 'libmp3lame',
            '-b:a', '96k', str(dest)]
    try:
        _run(head + ['-af', 'loudnorm=I=-16:TP=-1.5:LRA=11'] + tail)
    except PodcastError:
        # 某些精简版 ffmpeg 没编 loudnorm，退一步只做转码，不要整期失败。
        _run(head + tail)


def _turn_speed(host: dict, text: str) -> float:
    """给每段一点语速差：短附和稍快，长解释稍慢，避免整期一个节奏。"""
    base = float(host.get('speed') or 1.0)
    length = len(text or '')
    if length <= 6:
        base += 0.05
    elif length >= 90:
        base -= 0.02
    return round(max(0.9, min(1.12, base)), 2)


def _synthesize_script(job: dict, script: dict) -> tuple:
    """返回 (mp3 路径, 总秒数, tts 计费字数)。失败抛 PodcastError。"""
    user, job_id = job['user'], job['id']
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        raise PodcastError('服务器缺少 ffmpeg，无法拼接音频；请安装 ffmpeg 或配置 PODCAST_FFMPEG')

    directory = _job_dir(user, job_id)
    work = directory / 'work'
    work.mkdir(parents=True, exist_ok=True)
    merged = work / 'merged.wav'
    output = directory / 'podcast.mp3'
    turns = script['turns']
    total_turns = len(turns)

    words = 0
    frames = 0
    writer = None
    try:
        writer = wave.open(str(merged), 'wb')
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)

        for index, turn in enumerate(turns):
            host = HOSTS[turn['speaker']]
            voice = host['voice']
            text = turn['text']
            speed = _turn_speed(host, text)
            chunks = _split_text(text, TTS_CHAR_LIMIT)
            if not chunks:
                continue
            turn_audio = bytearray()
            for part_index, chunk in enumerate(chunks):
                if len(chunk) > TTS_CHAR_LIMIT:
                    chunk = chunk[:TTS_CHAR_LIMIT]
                try:
                    audio, billed = volc_tts.synthesize(
                        chunk,
                        voice=voice,
                        speed=speed,
                        emotion=turn.get('emotion') or '',
                        intensity=turn.get('intensity') or 3,
                        context=[host.get('context') or ''],
                    )
                except ValueError as exc:
                    raise PodcastError('第 %d 段语音合成失败：%s' % (index + 1, exc)) from exc
                words += int(billed or 0)
                turn_audio.extend(audio)
                time.sleep(0.15)     # 轻一点打接口，避免连续请求被限流

            mp3_path = work / ('turn-%03d.mp3' % (index + 1))
            wav_path = work / ('turn-%03d.wav' % (index + 1))
            mp3_path.write_bytes(bytes(turn_audio))
            try:
                _to_wav(ffmpeg, mp3_path, wav_path)
                with wave.open(str(wav_path), 'rb') as reader:
                    if reader.getnchannels() != 1 or reader.getframerate() != SAMPLE_RATE:
                        raise PodcastError('第 %d 段音频格式不一致' % (index + 1))
                    data = reader.readframes(reader.getnframes())
                writer.writeframes(data)
                frames += len(data) // 2
            finally:
                try:
                    mp3_path.unlink()
                except OSError:
                    pass
                try:
                    wav_path.unlink()
                except OSError:
                    pass

            if index < total_turns - 1:
                gap = int(round(SAMPLE_RATE * float(turn.get('pause') or 0.3)))
                if gap > 0:
                    silence = b'\x00\x00' * gap
                    writer.writeframes(silence)
                    frames += gap

            done = index + 1
            try:
                storage.podcast_job_update(job_id, {
                    'done_turns': done,
                    'progress': 15 + int(75 * done / max(1, total_turns)),
                    'tts_words': words,
                })
            except storage.StorageUnavailable:
                pass

        writer.close()
        writer = None
        if not frames:
            raise PodcastError('没有合成出任何音频')
        _encode_mp3(ffmpeg, merged, output)
        if not output.is_file() or output.stat().st_size < 1000:
            raise PodcastError('生成的 MP3 文件异常，请重试')
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        shutil.rmtree(work, ignore_errors=True)
    duration = int(round(frames / float(SAMPLE_RATE)))
    return output, duration, words


# ---------------------------------------------------------------
# 后台调度：独立线程，按顺序处理 queued
# ---------------------------------------------------------------
_worker_lock = threading.Lock()
_worker_started = False
_recover_lock = threading.Lock()
_recovered = False


def _recover_once() -> None:
    global _recovered
    if _recovered:
        return
    with _recover_lock:
        if _recovered:
            return
        try:
            storage.podcast_job_recover_running(
                '服务重启，播客生成中断；已用掉的部分可能已经产生费用，请核对账单后重新生成')
        except storage.StorageUnavailable:
            return
        _recovered = True


def _worker_loop() -> None:
    while not _recovered:
        _recover_once()
        if not _recovered:
            time.sleep(2)

    while True:
        try:
            job = storage.podcast_job_claim_next()
        except storage.StorageUnavailable:
            time.sleep(5)
            continue
        except Exception:
            time.sleep(2)
            continue
        if not job:
            time.sleep(1.2)
            continue
        try:
            _process_job(job)
        except Exception as exc:
            try:
                storage.podcast_job_update(job['id'], {
                    'status': 'failed',
                    'stage': 'failed',
                    'error': '生成失败：' + _short(exc),
                    'finished_at': storage.now_str(),
                })
            except Exception:
                pass


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, name='podcast-worker',
                                  daemon=True)
        thread.start()
        _worker_started = True


def _process_job(job: dict) -> None:
    user, job_id = job['user'], job['id']

    if not _ffmpeg_ready():
        storage.podcast_job_update(job_id, {
            'status': 'failed',
            'stage': 'failed',
            'error': '服务器缺少 ffmpeg，无法拼接音频；请安装 ffmpeg 或配置 PODCAST_FFMPEG',
            'finished_at': storage.now_str(),
        })
        return

    try:
        storage.podcast_job_update(job_id, {
            'stage': 'script',
            'progress': 5,
            'error': '',
        })
        script = _llm_script(job['topic'], job.get('material') or '',
                             job.get('tone') or 'casual',
                             int(job.get('minutes') or 5))
        storage.podcast_job_update(job_id, {
            'stage': 'synthesizing',
            'progress': 15,
            'title': script['title'],
            'summary': script['summary'],
            'script': script['turns'],
            'total_turns': len(script['turns']),
            'done_turns': 0,
            'model': script.get('model') or job.get('model') or '',
        })

        output, duration, words = _synthesize_script(job, script)
        relative = output.name
        storage.podcast_job_update(job_id, {
            'status': 'succeeded',
            'stage': 'done',
            'progress': 100,
            'audio': relative,
            'duration_seconds': duration,
            'tts_words': words,
            'finished_at': storage.now_str(),
        })
        _remember_history(job, script, duration, words)
    except PodcastError as exc:
        try:
            storage.podcast_job_update(job_id, {
                'status': 'failed',
                'stage': 'failed',
                'error': str(exc),
                'finished_at': storage.now_str(),
            })
        except storage.StorageUnavailable:
            pass
        _remove_job_dir(user, job_id)
    except (OSError, storage.StorageUnavailable) as exc:
        try:
            storage.podcast_job_update(job_id, {
                'status': 'failed',
                'stage': 'failed',
                'error': '保存生成结果失败：%s' % _short(exc),
                'finished_at': storage.now_str(),
            })
        except storage.StorageUnavailable:
            pass


def _remember_history(job: dict, script: dict, duration: int, words: int) -> None:
    payload = {
        'jobId': job['id'],
        'topic': job['topic'],
        'title': script['title'],
        'tone': job.get('tone') or 'casual',
        'minutes': job.get('minutes') or 5,
        'durationSeconds': duration,
        'ttsWords': words,
        'turns': len(script['turns']),
    }
    try:
        storage.add_record(TOOL, job['user'], script['title'][:80], payload)
    except storage.StorageUnavailable:
        pass


# ---------------------------------------------------------------
# 接口
# ---------------------------------------------------------------
def _transcript_text(job: dict) -> str:
    script = job.get('script') or []
    lines = [
        job.get('title') or job.get('topic') or '未命名播客',
        '主题：%s' % (job.get('topic') or ''),
        '风格：%s · 目标 %s 分钟' % (
            (TONES.get(job.get('tone') or 'casual') or TONES['casual'])[0],
            int(job.get('minutes') or 5)),
        '',
    ]
    for turn in script:
        if not isinstance(turn, dict):
            continue
        host = HOSTS.get(str(turn.get('speaker') or 'A').upper(), HOSTS['A'])
        lines.append('［%s］%s' % (host['name'], str(turn.get('text') or '').strip()))
        lines.append('')
    return '\n'.join(lines).strip() + '\n'


def _hosts_public() -> list:
    return [{'key': key, 'name': value['name']} for key, value in HOSTS.items()]


def _public_job(job: dict, with_script: bool = False) -> dict:
    ready = job.get('status') == 'succeeded' and bool(job.get('audio'))
    data = {
        'id': job['id'],
        'topic': job.get('topic') or '',
        'tone': job.get('tone') or 'casual',
        'toneLabel': (TONES.get(job.get('tone') or 'casual') or TONES['casual'])[0],
        'minutes': int(job.get('minutes') or 5),
        'title': job.get('title') or '',
        'summary': job.get('summary') or '',
        'status': job.get('status') or 'queued',
        'stage': job.get('stage') or 'queued',
        'progress': int(job.get('progress') or 0),
        'totalTurns': int(job.get('total_turns') or 0),
        'doneTurns': int(job.get('done_turns') or 0),
        'durationSeconds': int(job.get('duration_seconds') or 0),
        'ttsWords': int(job.get('tts_words') or 0),
        'model': job.get('model') or '',
        'error': job.get('error') or '',
        'hosts': _hosts_public(),
        'audioUrl': ('/podcast/api/jobs/%s/audio' % job['id']) if ready else '',
        'transcriptUrl': ('/podcast/api/jobs/%s/transcript' % job['id']) if ready else '',
        'createdAt': job.get('created_at') or '',
        'startedAt': job.get('started_at') or '',
        'finishedAt': job.get('finished_at') or '',
    }
    if with_script:
        data['transcript'] = [{
            'speaker': str(turn.get('speaker') or 'A').upper(),
            'name': HOSTS.get(str(turn.get('speaker') or 'A').upper(), HOSTS['A'])['name'],
            'text': str(turn.get('text') or ''),
            'pause': float(turn.get('pause') or 0.3),
            'emotion': str(turn.get('emotion') or ''),
        } for turn in (job.get('script') or []) if isinstance(turn, dict)]
    return data


def _clean_job_body(body) -> dict:
    if not isinstance(body, dict):
        raise ValueError('请求格式不正确')
    topic = _text(body.get('topic'), MAX_TOPIC)
    if not topic:
        raise ValueError('先写一个播客主题')
    material = _text(body.get('material'), MAX_MATERIAL)
    tone = str(body.get('tone') or 'casual').strip().lower()
    if tone not in TONES:
        raise ValueError('不支持的风格')
    try:
        minutes = int(body.get('minutes', 5))
    except (TypeError, ValueError):
        raise ValueError('时长只能是 3 / 5 / 8 分钟')
    if minutes not in DURATIONS:
        raise ValueError('时长只能是 3 / 5 / 8 分钟')
    return {'topic': topic, 'material': material, 'tone': tone, 'minutes': minutes}


def _ready_info() -> dict:
    llm_ok = model_config.llm_ready()
    tts_ok = model_config.tts_ready()
    ffmpeg_ok = _ffmpeg_ready()
    hints = []
    if not llm_ok:
        hints.append(model_config.llm_missing_hint())
    if not tts_ok:
        hints.append('服务端没有配置语音合成 Key（VOLC_TTS_API_KEY）')
    if not ffmpeg_ok:
        hints.append('服务器缺少 ffmpeg，无法拼接音频（可配置 PODCAST_FFMPEG）')
    return {
        'ready': llm_ok and tts_ok and ffmpeg_ok,
        'llmReady': llm_ok,
        'ttsReady': tts_ok,
        'ffmpegReady': ffmpeg_ok,
        'missing': '；'.join(hints),
    }


@router.get('/podcast', response_class=HTMLResponse)
def podcast_page():
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="background:#0f1422;color:#eff3fa;font-family:system-ui">'
        '<h2>模板文件缺失</h2>'
        '<p>请确认 templates/podcast.html 存在。</p>'
        '</body></html>'
    )


@router.get('/podcast/api/status')
async def podcast_status(request: Request):
    user = _user(request)
    _ensure_worker()
    used = 0
    storage_error = ''
    try:
        used = await run_in_threadpool(storage.podcast_jobs_since, user, _today_start())
    except storage.StorageUnavailable as exc:
        storage_error = str(exc)
    info = _ready_info()
    return JSONResponse({
        'ok': True,
        **info,
        'hosts': _hosts_public(),
        'tones': [{'key': key, 'label': value[0], 'hint': value[1]}
                  for key, value in TONES.items()],
        'durations': [{'minutes': key, 'label': value['label']}
                      for key, value in DURATIONS.items()],
        'dailyLimit': _daily_limit(),
        'usedToday': used,
        'ttlDays': _ttl_days(),
        'model': model_config.llm_model(),
        'storageError': storage_error,
    })


@router.get('/podcast/api/jobs')
async def list_jobs(request: Request):
    user = _user(request)
    _ensure_worker()
    try:
        await run_in_threadpool(_cleanup_user, user)
        rows = await run_in_threadpool(storage.podcast_job_list, user, 20)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': [_public_job(row) for row in rows]})


@router.post('/podcast/api/jobs')
async def create_job(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        data = _clean_job_body(body)
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)

    info = _ready_info()
    if not info['ready']:
        return JSONResponse({'ok': False, 'message': info['missing']}, status_code=503)

    user = _user(request)
    try:
        await run_in_threadpool(_cleanup_user, user)
        used = await run_in_threadpool(storage.podcast_jobs_since, user, _today_start())
        limit = _daily_limit()
        if used + data['minutes'] > limit:
            return JSONResponse({
                'ok': False,
                'message': '今天的播客额度不够了（已用 %d / %d 分钟），明天再来'
                           '或调大 PODCAST_DAILY_LIMIT' % (used, limit),
            }, status_code=429)
        job = await run_in_threadpool(
            storage.podcast_job_add,
            user, data['topic'], data['material'], data['tone'], data['minutes'],
            HOSTS['A']['name'], HOSTS['B']['name'],
            HOSTS['A']['voice'], HOSTS['B']['voice'],
            model_config.llm_model(),
        )
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)

    _ensure_worker()
    return JSONResponse({'ok': True, 'job': _public_job(job)}, status_code=202)


@router.get('/podcast/api/jobs/{job_id}')
async def get_job(job_id: str, request: Request):
    user = _user(request)
    _ensure_worker()
    try:
        row = await run_in_threadpool(storage.podcast_job_get, user, job_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return JSONResponse({'ok': False, 'message': '任务不存在或已过期'}, status_code=404)
    try:
        data = _public_job(row, with_script=True)
    except ValueError:
        return JSONResponse({'ok': False, 'message': '任务不存在或已过期'}, status_code=404)
    return JSONResponse({'ok': True, 'job': data})


@router.delete('/podcast/api/jobs/{job_id}')
async def delete_job(job_id: str, request: Request):
    user = _user(request)

    def _delete():
        row = storage.podcast_job_get(user, job_id)
        if not row:
            return 'missing'
        if row.get('status') in ('queued', 'running'):
            return 'busy'
        if not storage.podcast_job_delete_finished(user, job_id):
            return 'busy'
        _remove_job_dir(user, job_id)
        return 'ok'

    try:
        result = await run_in_threadpool(_delete)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if result == 'missing':
        return JSONResponse({'ok': False, 'message': '任务不存在或已删除'}, status_code=404)
    if result == 'busy':
        return JSONResponse({'ok': False, 'message': '播客还在生成，等它结束再删'},
                            status_code=409)
    return JSONResponse({'ok': True})


@router.get('/podcast/api/jobs/{job_id}/audio')
async def get_job_audio(job_id: str, request: Request):
    user = _user(request)
    try:
        row = await run_in_threadpool(storage.podcast_job_get, user, job_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row or row.get('status') != 'succeeded' or not row.get('audio'):
        return JSONResponse({'ok': False, 'message': '音频不存在或还没生成好'}, status_code=404)
    try:
        path = _job_dir(user, job_id) / str(row['audio'])
    except ValueError:
        return JSONResponse({'ok': False, 'message': '音频不存在'}, status_code=404)
    if not path.is_file():
        return JSONResponse({'ok': False, 'message': '音频已被清理'}, status_code=404)
    return FileResponse(
        str(path),
        media_type='audio/mpeg',
        headers={'Cache-Control': 'private, max-age=3600'},
    )


@router.get('/podcast/api/jobs/{job_id}/transcript')
async def get_job_transcript(job_id: str, request: Request):
    user = _user(request)
    try:
        row = await run_in_threadpool(storage.podcast_job_get, user, job_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return JSONResponse({'ok': False, 'message': '任务不存在或已过期'}, status_code=404)
    content = _transcript_text(row)
    name = (row.get('title') or row.get('topic') or 'podcast').strip()[:60] or 'podcast'
    name = re.sub(r'[\\/:*?"<>|\s]+', '-', name).strip('-') or 'podcast'
    return Response(
        content=content.encode('utf-8'),
        media_type='text/plain; charset=utf-8',
        headers={
            'Content-Disposition': "attachment; filename*=UTF-8''%s.txt" % quote(name),
            'Cache-Control': 'no-store',
        },
    )
