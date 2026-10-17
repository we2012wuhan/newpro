# -*- coding: utf-8 -*-
# 实时语音对话（火山引擎 / 豆包 端到端实时语音大模型 · duplex）
# 浏览器 <--ws--> 本模块（转发 + 事件翻译） <--ws--> 火山
# 协议照官方 demo（python3.7_duplex_demo）：纯 JSON 事件 + base64 音频
#   上行 pcm 16k，下行 pcm_s16le 24k（正好对上前端 worklet / 播放器）
#
# 三道省钱闸（按会话分钟计费，连着不说话也扣）：
#   1. IDLE_SECONDS 秒没有任何语音动静 -> 自动挂断
#   2. 单次连接最长 MAX_SECONDS 秒 -> 无条件断
#   3. 挂断按钮 / 关页面（WS 断开）-> 立刻关会话 + 断上游
import asyncio
import base64
import io
import json
import os
import uuid

import websockets
from fastapi import APIRouter, Body, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import auth
import storage

TOOL = 'voice-chat'
_WS_URL = 'wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue'
ENV_KEY = 'VOLC_RT_ACCESS_KEY'
ENV_VOICE = 'VOLC_RT_VOICE'
ENV_VOICE_YUE = 'VOLC_RT_VOICE_YUE'   # 粤语场景单独的音色，留空就跟着 ENV_VOICE
ENV_VOICES = 'VOLC_RT_VOICES'         # 音色下拉额外追加，格式 id|名称,id|名称
ENV_MODEL = 'VOLC_RT_MODEL'
ENV_PROMPT = 'VOLC_RT_PROMPT'
_DEFAULT_MODEL = '1.2.6.1'
_DEFAULT_VOICE = 'zh_female_vv_jupiter_bigtts'
_DEFAULT_PROMPT = '你是一个说话干脆的中文语音助手。回答短一点，口语化，别念列表。'

# 场景：开会话（session.create）时把对应提示词塞给豆包。加一个新场景就是这里加一行。
SCENES = {
    'free': {'name': '自由聊天', 'prompt': _DEFAULT_PROMPT},
    'en': {'name': '英语陪练', 'prompt': (
        'You are my English speaking partner. Always answer in English, with simple everyday words and '
        'short sentences, like a friendly chat. If I say something wrong or unnatural in English, gently '
        'correct it in one short line first, then keep the conversation going. Never switch to Chinese.')},
    'yue': {'name': '粤语能手', 'voice_env': ENV_VOICE_YUE, 'prompt': (
        '你係一个广州本地人，全程只用粤语（广东话）同我倾偈：唔准夹杂普通话，唔准用书面语。'
        '发音、用词、语气都要似广州街坊日常讲嘢 —— 用粤语口语同粤语特有词（係、唔、嘅、咗、睇、嗰、边度、点解、'
        '冇、佢哋、畀、啲、咁、唔该），唔好用普通话腔去读粤语字。'
        '话题就讲日常生活：饮茶、买菜、搭地铁、返工、天气、睇戏，一次讲一两句，讲慢啲，好似朋友吹水咁，'
        '等我听得清楚、跟得上。'
        '我讲得唔地道、用错词、或者夹咗普通话，你要先用一句短话顺口纠正我（一样用粤语），再继续倾落去。'
        '我如果用普通话问你，你就照用粤语答我。')},
}
# 音色下拉：key -> {name, id}。id 空串 = 跟随 .env 的 VOLC_RT_VOICE（也就是现在这个默认音色）。
# 加一个音色就是这里加一行；不想改代码就用 .env 的 VOLC_RT_VOICES=id|名称,id|名称 追加。
# 只收「这个端到端模型真的会出声」的音色 —— 实测（deploy/check_voice.py）：
#   uranus 家族（Meilin / 豆包桃子）有声；moon 家族（甜美小园）、mars 家族（Candice）只回文字、
#   一个音频包都没有，所以那两个先不收。加新音色之前先跑一遍那个脚本，别直接往这里加。
VOICES = {
    'def': {'name': '默认音色', 'id': ''},
    'meilin': {'name': 'Meilin', 'id': 'zh_female_meilinvyou_uranus_bigtts'},
    'taozi': {'name': '豆包桃子', 'id': 'zh_female_tianmeitaozi_uranus_bigtts'},
}
_DEFAULT_SCENE = 'free'

# 长期记忆 / 人设：一个账号一条，存 history 表（tool='voice-profile'）。
# 每次开会话拼到 instructions 尾巴上，跨场景、跨会话都生效。
# 注意：页面上的入口（⚙ 抽屉）按用户要求去掉了，接口和注入都留着 —— 想恢复只差前端那一块。
PROFILE_TOOL = 'voice-profile'
PROFILE_MAX = 1000
PROFILE_HEAD = '\n\n[长期记忆·关于我，请一直记着，别念出来，也别因为它换语言]\n'
IDLE_SECONDS = 30
MAX_SECONDS = 600
_BASE = os.path.dirname(os.path.abspath(__file__))
_ENV_FILE = os.path.join(_BASE, '.env')
_PAGE = os.path.join(_BASE, 'templates', 'voice-chat.html')
_env_cache = None
_page_cache = None

router = APIRouter()


def _env(name: str) -> str:
    global _env_cache
    if os.environ.get(name):
        return os.environ[name].strip()
    if _env_cache is None:
        _env_cache = {}
        try:
            with io.open(_ENV_FILE, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        _env_cache[k.strip()] = v.strip()
        except OSError:
            pass
    return _env_cache.get(name, '')


def rt_ready() -> bool:
    return bool(_env(ENV_KEY))


def rt_error() -> str:
    return '没配实时语音凭据：.env 里要填 %s（火山控制台 → 实时语音大模型 → 接入信息里的 Access Token）' % ENV_KEY


# 场景列表：下拉框由前端按这个画，加场景只改服务端一处
@router.get('/voice-chat/api/scenes')
def voice_scenes() -> JSONResponse:
    return JSONResponse({'ok': True, 'default': _DEFAULT_SCENE,
                         'scenes': [{'key': k, 'name': v['name']} for k, v in SCENES.items()]})


def _voice_table() -> list:
    """(key, 名称, 音色ID) 的有序列表：代码里的在前，.env 追加的在后。"""
    rows = [(k, v['name'], v['id']) for k, v in VOICES.items()]
    n = 0
    for part in _env(ENV_VOICES).split(','):
        part = part.strip()
        if not part:
            continue
        vid, _, vname = part.partition('|')
        vid = vid.strip()
        if not vid:
            continue
        n += 1
        rows.append(('env%d' % n, vname.strip() or vid, vid))
    return rows


def voice_id_of(key: str) -> str:
    """下拉里选的 key -> 真正发给豆包的音色 ID；没选或没对上就返回空串（交给默认那层）。"""
    if not key:
        return ''
    for k, _name, vid in _voice_table():
        if k == key:
            return vid
    return ''


def _user_of(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


def _norm_profile(text) -> str:
    s = str(text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    while '\n\n\n' in s:
        s = s.replace('\n\n\n', '\n\n')
    return s[:PROFILE_MAX]


def load_profile(user: str) -> str:
    """读这个账号的人设。没有、或者库读不了，都当空串 —— 人设不该拖累通话。"""
    if not user:
        return ''
    try:
        rows = storage.list_records(PROFILE_TOOL, user, 1)
    except Exception:
        return ''
    if not rows:
        return ''
    return _norm_profile((rows[0].get('payload') or {}).get('text'))


def save_profile(user: str, text) -> str:
    text = _norm_profile(text)
    rows = storage.list_records(PROFILE_TOOL, user, 1)
    if rows:
        n = storage.update_record(user, rows[0]['id'], payload={'text': text}, title='人设')
        if not n:
            raise storage.StorageUnavailable('这条人设不属于当前账号，没存上')
    else:
        storage.add_record(PROFILE_TOOL, user, '人设', {'text': text})
    return text


@router.get('/voice-chat/api/voices')
def voice_list() -> JSONResponse:
    rows = _voice_table()
    return JSONResponse({'ok': True, 'default': rows[0][0] if rows else '',
                         'voices': [{'key': k, 'name': n} for k, n, _ in rows]})


@router.get('/voice-chat/api/profile')
async def voice_profile_get(request: Request) -> JSONResponse:
    try:
        text = await run_in_threadpool(load_profile, _user_of(request))
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)})
    return JSONResponse({'ok': True, 'text': text, 'max': PROFILE_MAX})


@router.post('/voice-chat/api/profile')
async def voice_profile_set(request: Request, payload: dict = Body(...)) -> JSONResponse:
    try:
        text = await run_in_threadpool(save_profile, _user_of(request), payload.get('text'))
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)})
    return JSONResponse({'ok': True, 'text': text, 'max': PROFILE_MAX})


@router.get('/voice-chat', response_class=HTMLResponse)
def voice_chat_page() -> HTMLResponse:
    """整页 HTML（单文件）；没配凭据时页面自己会提示。"""
    global _page_cache
    if _page_cache is None:
        with io.open(_PAGE, encoding='utf-8') as fh:
            _page_cache = fh.read()
    return HTMLResponse(_page_cache)


# 服务端事件类型 -> 给浏览器的名字（前端只认前面几种）
_KIND = {
    'session.created': 'session_started',
    'session.closed': 'session_closed',
    'input_audio_buffer.committed': 'audio_committed',
    'conversation.item.input_audio_transcription.delta': 'asr_text',
    'conversation.item.input_audio_transcription.completed': 'asr_text',
    'response.output_text.delta': 'chat_text',
    'response.output_text.done': 'chat_text',
    'response.output_audio.started': 'tts_started',
    'response.output_audio.done': 'tts_ended',
    'response.done': 'response_done',
    'response.canceled': 'response_canceled',
    'error': 'error',
}
# 收到这些算「有人说话/它在说」，刷新静默计时
_ACTIVE = ('asr_text', 'chat_text', 'tts_started')


def _text_of(ev: dict) -> str:
    for k in ('text', 'delta', 'transcript'):
        v = ev.get(k)
        if isinstance(v, str) and v:
            return v
    it = ev.get('item')
    if isinstance(it, dict):
        for c in (it.get('content') or []):
            if isinstance(c, dict) and isinstance(c.get('text'), str):
                return c['text']
    return ''


def _session_event(sid: str, prompt: str = '', voice: str = '') -> dict:
    return {
        'type': 'session.create',
        'event_id': 'event_1',
        'session': {
            'id': sid,
            'model': _env(ENV_MODEL) or _DEFAULT_MODEL,
            'instructions': prompt or _env(ENV_PROMPT) or _DEFAULT_PROMPT,
            'audio': {
                'input': {'format': {'type': 'pcm', 'rate': 16000}},
                'output': {'format': {'type': 'pcm_s16le', 'rate': 24000},
                           'voice': voice or _env(ENV_VOICE) or _DEFAULT_VOICE},
            },
            'tools': [],
        },
        'extension': {'asr': {'extra': {}}, 'tts': {'extra': {}}, 'dialog': {'extra': {}}},
    }


@router.websocket('/ws/voice')
async def voice_ws(ws: WebSocket) -> None:
    await ws.accept()
    if not rt_ready():
        await ws.send_json({'type': 'error', 'message': rt_error()})
        await ws.close()
        return
    try:
        up = await websockets.connect(_WS_URL, additional_headers={'X-Api-Key': _env(ENV_KEY)},
                                      max_size=None, ping_interval=None, open_timeout=10)
    except Exception as exc:
        await ws.send_json({'type': 'error', 'message': '连不上豆包实时语音：%s' % str(exc)[:160]})
        await ws.close()
        return

    loop = asyncio.get_event_loop()
    last_active = loop.time()
    started = last_active
    closed = asyncio.Event()
    sid = str(uuid.uuid4())
    speaking = False          # 豆包正在回话（从 output_audio.started 到 done/canceled）
    n = {'v': 1}

    # WS 不走 LoginGate（中间件只管 http），所以自己从登录 Cookie 认人
    user = auth.read_token(ws.cookies.get(auth.COOKIE_NAME)) or ''
    try:
        profile = await run_in_threadpool(load_profile, user) if user else ''
    except Exception:
        profile = ''

    def nid() -> str:
        n['v'] += 1
        return 'event_%d' % n['v']

    async def send(ev: dict) -> None:
        await up.send(json.dumps(ev, ensure_ascii=False, separators=(',', ':')))

    async def to_upstream():
        nonlocal last_active
        while True:
            msg = await ws.receive()
            if msg.get('type') == 'websocket.disconnect':
                print('[voice] client disconnected session=%s speaking=%s' % (sid[:8], speaking), flush=True)
                return
            if msg.get('bytes') is not None:
                last_active = loop.time()
                await send({'type': 'input_audio_buffer.append',
                            'audio': base64.b64encode(msg['bytes']).decode('ascii')})
                continue
            text = msg.get('text') or ''
            if not text:
                continue
            try:
                cmd = json.loads(text)
            except ValueError:
                continue
            what = cmd.get('cmd')
            if what == 'start':
                key = str(cmd.get('scenario') or _DEFAULT_SCENE)
                scene = SCENES.get(key) or SCENES[_DEFAULT_SCENE]
                prompt = scene['prompt']
                if profile:
                    prompt += PROFILE_HEAD + profile
                voice = voice_id_of(str(cmd.get('voice') or '')) or (
                    _env(scene['voice_env']) if scene.get('voice_env') else '')
                print('[voice] start session=%s user=%s scene=%s voice=%s' %
                      (sid[:8], (user or '-')[:20], scene['name'], voice or 'default'), flush=True)
                await send(_session_event(sid, prompt, voice))
            elif what == 'commit':
                await send({'type': 'input_audio_buffer.commit', 'event_id': nid()})
                # 这个协议没有 response.create：补一段静音，让服务端 VAD 判断句尾、自动接话
                await send({'type': 'input_audio_buffer.append', 'audio': base64.b64encode(b'\\x00' * 32000).decode('ascii')})
                await asyncio.sleep(0.15)
            elif what == 'interrupt':
                await send({'type': 'response.cancel', 'event_id': nid()})
                last_active = loop.time()
            elif what == 'hangup':
                await send({'type': 'session.close', 'event_id': nid()})
                closed.set()
                return

    async def from_upstream():
        nonlocal last_active, speaking
        async for data in up:
            try:
                ev = json.loads(data)
            except (ValueError, TypeError):
                continue
            kind = ev.get('type', '')
            if kind == 'response.output_audio.delta':
                speaking = True
                b64 = ev.get('delta') or ev.get('audio') or ''
                try:
                    await ws.send_bytes(base64.b64decode(b64))
                except Exception:
                    pass
                last_active = loop.time()
                continue
            name = _KIND.get(kind)
            if not name:
                continue
            if name == 'tts_started':
                speaking = True
            elif name in ('tts_ended', 'response_done', 'response_canceled'):
                speaking = False
            if name in _ACTIVE:
                last_active = loop.time()
            if name == 'error':
                speaking = False
                print('[voice] upstream error session=%s: %s' %
                      (sid[:8], str(ev.get('error') or ev)[:160]), flush=True)
                await ws.send_json({'type': 'error', 'message': str(ev.get('error') or ev)[:200]})
                closed.set()
                return
            if name == 'session_closed':
                speaking = False
                print('[voice] upstream closed session=%s' % sid[:8], flush=True)
                await ws.send_json({'type': 'event', 'name': name, 'data': ''})
                closed.set()
                return
            await ws.send_json({'type': 'event', 'name': name, 'data': _text_of(ev)})

    async def watchdog():
        while not closed.is_set():
            await asyncio.sleep(2)
            now = loop.time()
            # 正在回话时，哪怕上游音频包断了也不按静默挂断；等它说完再重新计时
            if (not speaking) and now - last_active > IDLE_SECONDS:
                print('[voice] idle close session=%s gap=%.1fs speaking=%s' %
                      (sid[:8], now - last_active, speaking), flush=True)
                await ws.send_json({'type': 'closed', 'reason': 'idle',
                                    'message': '%d 秒没听到声音，已自动挂断（防跑钱）' % IDLE_SECONDS})
                closed.set()
                return
            if now - started > MAX_SECONDS:
                print('[voice] max duration close session=%s speaking=%s' % (sid[:8], speaking), flush=True)
                await ws.send_json({'type': 'closed', 'reason': 'timeout',
                                    'message': '到 %d 分钟上限，自动挂断' % (MAX_SECONDS // 60)})
                closed.set()
                return

    async def closer():
        await closed.wait()
        try:
            await send({'type': 'session.close', 'event_id': nid()})
        except Exception:
            pass
        for sock in (ws, up):
            try:
                await sock.close()
            except Exception:
                pass

    tasks = [asyncio.create_task(x()) for x in (to_upstream, from_upstream, watchdog, closer)]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        closed.set()
        for t in tasks:
            t.cancel()
        try:
            await up.close()
        except Exception:
            pass