# -*- coding: utf-8 -*-
"""
模型配置：Key / 接口地址 / 模型名一律从服务端取，页面不再手填
==============================================================
读取顺序（前面有就不看后面）：

  1. 真实环境变量   —— 部署时在平台面板里配，优先级最高
  2. 项目根目录 .env —— 本地开发就改这个文件（已在 .gitignore 里，不会进仓库）
  3. 代码里的默认值 —— 只有接口地址和模型名有默认值，Key 没有

十个变量：

  DEEPSEEK_API_KEY     DeepSeek 的 Key                                 必填
  DEEPSEEK_BASE_URL    接口地址，默认 https://api.deepseek.com           可选
  DEEPSEEK_MODEL       模型名，默认 deepseek-chat                        可选
  OCR_SPACE_API_KEY    OCR.space 的 Key，不填就用公共免费 Key（会被限流）    可选
  VOLC_TTS_API_KEY     火山引擎（豆包）语音合成的 Key，不填就只剩浏览器本地朗读   可选
  VOLC_TTS_VOICE       音色 ID，默认 zh_female_vv_uranus_bigtts           可选
  VOLC_TTS_RESOURCE_ID 资源 ID，默认 seed-tts-2.0                        可选
  VOLC_TTS_SPEED       语速，默认 1.0（0.2 ~ 3.0）                        可选
  VOLC_ASR_API_KEY     语音识别的 Key，不填就复用朗读那把                    可选
  VOLC_ASR_RESOURCE_ID 识别的资源 ID，默认 volc.bigasr.sauc.duration         可选

页面里那些「模型设置 / API Key」输入框已经全部删掉，前端也不再缓存 Key。
"""
from __future__ import annotations

import os
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent
_ENV_FILE = _BASE_DIR / '.env'

ENV_KEY = 'DEEPSEEK_API_KEY'
ENV_BASE = 'DEEPSEEK_BASE_URL'
ENV_MODEL = 'DEEPSEEK_MODEL'
ENV_OCR_KEY = 'OCR_SPACE_API_KEY'

# 朗读（火山引擎 / 豆包语音合成）：只有 Key 是必填，不填就回落到浏览器自带语音
ENV_TTS_KEY = 'VOLC_TTS_API_KEY'
ENV_TTS_VOICE = 'VOLC_TTS_VOICE'
ENV_TTS_RESOURCE = 'VOLC_TTS_RESOURCE_ID'
ENV_TTS_SPEED = 'VOLC_TTS_SPEED'

# 语音输入（火山引擎语音识别）：Key 不填就复用朗读那把，反正一个账号
ENV_ASR_KEY = 'VOLC_ASR_API_KEY'
ENV_ASR_RESOURCE = 'VOLC_ASR_RESOURCE_ID'

_DEFAULT_BASE = 'https://api.deepseek.com'
_DEFAULT_MODEL = 'deepseek-chat'

_DEFAULT_TTS_VOICE = 'zh_female_vv_uranus_bigtts'   # 豆包「Vv」女声（大模型音色）
_DEFAULT_TTS_RESOURCE = 'seed-tts-2.0'              # 资源 ID：要和音色配套，错配会 403
_DEFAULT_TTS_SPEED = 1.0

_DEFAULT_ASR_RESOURCE = 'volc.bigasr.sauc.duration'   # 大模型流式语音识别（小时版）

# 没配 Key 时接口返回的错误文案：只说缺哪个变量，页面上不放解释性说明
MISSING_KEY_HINT = '服务端没有配置模型 Key（%s）' % ENV_KEY


def load_env_file(path=None) -> int:
    """把 .env 读进 os.environ。已经存在的环境变量不动（线上以平台面板为准）。"""
    target = Path(path) if path else _ENV_FILE
    try:
        text = target.read_text(encoding='utf-8')
    except OSError:
        return 0
    count = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        name, _, value = line.partition('=')
        name = name.strip()
        if not name or name in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        else:
            cut = value.find(' #')      # 没加引号时，行尾的注释也一起去掉
            if cut >= 0:
                value = value[:cut].rstrip()
        os.environ[name] = value
        count += 1
    return count


# 导入即生效：main.py / 各工具模块一加载，.env 就已经进环境变量了
load_env_file()


def llm_key() -> str:
    return os.environ.get(ENV_KEY, '').strip()


def llm_base() -> str:
    return (os.environ.get(ENV_BASE, '').strip() or _DEFAULT_BASE).rstrip('/')


def llm_model() -> str:
    return os.environ.get(ENV_MODEL, '').strip() or _DEFAULT_MODEL


def llm_endpoint(base: str = '') -> str:
    """Base URL 自动补 /chat/completions，填全了就不动。"""
    url = (base or llm_base()).strip().rstrip('/')
    if url.endswith('/chat/completions'):
        return url
    return url + '/chat/completions'


def llm_ready() -> bool:
    return bool(llm_key())


def ocr_key(default: str = '') -> str:
    return os.environ.get(ENV_OCR_KEY, '').strip() or default


# ---------------- 朗读（火山引擎豆包 TTS） ----------------
def volc_tts_key() -> str:
    return os.environ.get(ENV_TTS_KEY, '').strip()


def volc_tts_voice() -> str:
    return os.environ.get(ENV_TTS_VOICE, '').strip() or _DEFAULT_TTS_VOICE


def volc_tts_resource() -> str:
    return os.environ.get(ENV_TTS_RESOURCE, '').strip() or _DEFAULT_TTS_RESOURCE


def volc_tts_speed() -> float:
    """语速。填错了（字母、空、负数）不报错，退回默认 1.0。"""
    raw = os.environ.get(ENV_TTS_SPEED, '').strip()
    if not raw:
        return _DEFAULT_TTS_SPEED
    try:
        val = float(raw)
    except ValueError:
        return _DEFAULT_TTS_SPEED
    return min(3.0, max(0.2, val))


def tts_ready() -> bool:
    return bool(volc_tts_key())


# ---------------- 语音输入（火山引擎语音识别） ----------------
def volc_asr_key() -> str:
    return os.environ.get(ENV_ASR_KEY, '').strip() or volc_tts_key()


def volc_asr_resource() -> str:
    return os.environ.get(ENV_ASR_RESOURCE, '').strip() or _DEFAULT_ASR_RESOURCE


def asr_ready() -> bool:
    return bool(volc_asr_key())
