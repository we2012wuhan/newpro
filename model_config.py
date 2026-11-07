# -*- coding: utf-8 -*-
"""
模型配置：Key / 接口地址 / 模型名一律从服务端取，页面不再手填
==============================================================
读取顺序（前面有就不看后面）：

  1. 真实环境变量   —— 部署时在平台面板里配，优先级最高
  2. 项目根目录 .env —— 本地开发就改这个文件（已在 .gitignore 里，不会进仓库）
  3. 代码里的默认值 —— 只有接口地址和模型名有默认值，Key 没有

常用变量：

  DEEPSEEK_API_KEY     DeepSeek 的 Key                                 必填
  DEEPSEEK_BASE_URL    接口地址，默认 https://api.deepseek.com           可选
  DEEPSEEK_MODEL       模型名，默认 deepseek-flash                       可选
  OCR_SPACE_API_KEY    OCR.space 的 Key，不填就用公共免费 Key（会被限流）    可选
  ARK_API_KEY          火山方舟（文生图）的 Key，不填就只有图片工具不能用       可选
  ARK_BASE_URL         方舟接口地址，默认北京区 v3                      可选
  ARK_IMAGE_MODEL      默认图片模型，默认 doubao-seedream-5-0-pro-260628   可选
  ARK_IMAGE_MODELS     页面可切换的模型清单，逗号分隔，留空用内置清单       可选
  VOLC_TTS_API_KEY     火山引擎（豆包）语音合成的 Key，不填就只剩浏览器本地朗读   可选
  VOLC_TTS_VOICE       音色 ID，默认 zh_female_vv_uranus_bigtts           可选
  VOLC_TTS_RESOURCE_ID 资源 ID，默认 seed-tts-2.0                        可选
  VOLC_TTS_SPEED       语速，默认 1.0（0.2 ~ 3.0）                        可选
  VOLC_ASR_API_KEY     语音识别的 Key，不填就复用朗读那把                    可选
  VOLC_ASR_RESOURCE_ID 识别的资源 ID，默认 volc.bigasr.sauc.duration         可选
  OPENAI_API_KEY       OpenAI 兼容中转的 Key，配合首页「文本模型」切换用       可选
  OPENAI_BASE_URL      中转地址，填到 /v1 为止                                 可选
  OPENAI_MODEL         中转的默认模型名                                       可选
  OPENAI_MODELS        首页下拉里的模型清单，逗号分隔                          可选

页面里那些「模型设置 / API Key」输入框已经全部删掉，前端也不再缓存 Key。
首页只切换 data/llm.json 里的 provider + model，Key 始终留在服务端环境变量。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent
_ENV_FILE = _BASE_DIR / '.env'

ENV_KEY = 'DEEPSEEK_API_KEY'
ENV_BASE = 'DEEPSEEK_BASE_URL'
ENV_MODEL = 'DEEPSEEK_MODEL'
ENV_MODELS = 'DEEPSEEK_MODELS'
ENV_OCR_KEY = 'OCR_SPACE_API_KEY'

# 全局模型切换（首页搜索框旁边）。选择结果写到 data/llm.json。
# OPENAI_* 走的是「OpenAI 兼容中转」：官方的、第三方的、自建的都行，
# 只要接口形状是 /chat/completions。测试时可以用 LLM_CONFIG_PATH 指到临时文件。
ENV_LLM_CONFIG = 'LLM_CONFIG_PATH'
ENV_OPENAI_KEY = 'OPENAI_API_KEY'
ENV_OPENAI_BASE = 'OPENAI_BASE_URL'
ENV_OPENAI_MODEL = 'OPENAI_MODEL'
ENV_OPENAI_MODELS = 'OPENAI_MODELS'

# 文生图（火山方舟 / 豆包 Seedream）：Key 不填时页面会明确提示缺哪个变量
ENV_ARK_KEY = 'ARK_API_KEY'
ENV_ARK_BASE = 'ARK_BASE_URL'
ENV_ARK_IMAGE_MODEL = 'ARK_IMAGE_MODEL'
ENV_ARK_IMAGE_MODELS = 'ARK_IMAGE_MODELS'

# 朗读（火山引擎 / 豆包语音合成）：只有 Key 是必填，不填就回落到浏览器自带语音
ENV_TTS_KEY = 'VOLC_TTS_API_KEY'
ENV_TTS_VOICE = 'VOLC_TTS_VOICE'
ENV_TTS_RESOURCE = 'VOLC_TTS_RESOURCE_ID'
ENV_TTS_SPEED = 'VOLC_TTS_SPEED'

# 语音输入（火山引擎语音识别）：Key 不填就复用朗读那把，反正一个账号
ENV_ASR_KEY = 'VOLC_ASR_API_KEY'
ENV_ASR_RESOURCE = 'VOLC_ASR_RESOURCE_ID'

# 联网搜索（阿里云 CleverSee / IQS，API Key 方式）
ENV_IQS_KEY = 'ALIYUN_IQS_API_KEY'
ENV_IQS_ENDPOINT = 'ALIYUN_IQS_ENDPOINT'
ENV_IQS_ENGINE_FAST = 'ALIYUN_IQS_ENGINE_FAST'
ENV_IQS_ENGINE_DEEP = 'ALIYUN_IQS_ENGINE_DEEP'

_DEFAULT_BASE = 'https://api.deepseek.com'
_DEFAULT_MODEL = 'deepseek-flash'
_DEFAULT_PROVIDER = 'deepseek'
_SELECTION_FILE = _BASE_DIR / 'data' / 'llm.json'

# 首页下拉框里的文本模型。格式 (模型 ID, 显示名, 备注)。
# 模型迭代很快，线上直接改环境变量覆盖即可，不用动代码：
#   DEEPSEEK_MODELS / OPENAI_MODELS = 模型ID|显示名|备注,模型ID2,...
_DEFAULT_DEEPSEEK_MODELS = (
    ('deepseek-flash', 'DeepSeek Flash', '通用'),
)
# 中转不再内置 GPT 清单：各家中转支持的模型名不一样，统一用 OPENAI_MODEL /
# OPENAI_MODELS 配。两个都不填时，下拉里不显示这一组。
_DEFAULT_OPENAI_MODELS = ()

_DEFAULT_ARK_BASE = 'https://ark.cn-beijing.volces.com/api/v3'
# 2026 年可用的 Seedream 5.0 Pro；模型会迭代，线上直接改 ARK_IMAGE_MODEL 即可，
# 不用动代码。控制台里模型 ID 一般带日期后缀，抄完整的那一串。
_DEFAULT_ARK_IMAGE_MODEL = 'doubao-seedream-5-0-pro-260628'

# 页面下拉框里的模型清单：(模型 ID, 显示名, 一句备注)。
# 第一项之外都是比 Pro 便宜的档位，价格只是参考值，实际以方舟控制台账单为准。
# 模型下线、改名时不用改代码，设 ARK_IMAGE_MODELS 覆盖即可。
_DEFAULT_ARK_IMAGE_MODELS = (
    ('doubao-seedream-5-0-pro-260628', 'Seedream 5.0 Pro', '画质最好 · 约 ¥0.30/张'),
    ('doubao-seedream-5-0-flash-260915', 'Seedream 5.0 Flash', '速度快 · 约 ¥0.12/张'),
    ('doubao-seedream-4-5-251128', 'Seedream 4.5', '均衡 · 约 ¥0.25/张'),
    ('doubao-seedream-4-0-20260415', 'Seedream 4.0', '性价比高 · 约 ¥0.20/张'),
)
_DEFAULT_IQS_ENDPOINT = 'https://cloud-iqs.aliyuncs.com/search/unified'
_DEFAULT_IQS_ENGINE_FAST = 'CNLiteBasic'
_DEFAULT_IQS_ENGINE_DEEP = 'CNAuto'

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


# ---------------- 文本模型：服务商注册表 + 全局选择 ----------------
# 每个服务商只描述「怎么连」；页面能选哪些模型、当前选的是谁，
# 由 data/llm.json 和下面的 models 清单一起决定。
_MODEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,119}$')


def _provider_defs() -> dict:
    """服务商定义。base_default 留空表示必须自己填（比如中转地址）。"""
    return {
        'deepseek': {
            'id': 'deepseek',
            'label': 'DeepSeek',
            'key_env': ENV_KEY,
            'base_env': ENV_BASE,
            'base_default': _DEFAULT_BASE,
            'model_env': ENV_MODEL,
            'models_env': ENV_MODELS,
            'models_default': _DEFAULT_DEEPSEEK_MODELS,
            'hint': 'DEEPSEEK_API_KEY',
        },
        'openai': {
            'id': 'openai',
            'label': 'OpenAI / 中转',
            'key_env': ENV_OPENAI_KEY,
            'base_env': ENV_OPENAI_BASE,
            'base_default': '',
            'model_env': ENV_OPENAI_MODEL,
            'models_env': ENV_OPENAI_MODELS,
            'models_default': _DEFAULT_OPENAI_MODELS,
            'hint': 'OPENAI_API_KEY 和 OPENAI_BASE_URL（填到 /v1 为止）',
        },
    }


def _valid_model(model: str) -> bool:
    return bool(_MODEL_RE.match(str(model or '').strip()))


def _model_entries(raw: str, defaults) -> list:
    """解析「模型ID|显示名|备注,模型ID2」；空值用内置清单。"""
    entries = []
    seen = set()
    for chunk in re.split(r'[,;，；\n]+', str(raw or '')):
        part = chunk.strip()
        if not part:
            continue
        bits = [bit.strip() for bit in re.split(r'[|｜=]', part)]
        model_id = bits[0]
        if not _valid_model(model_id) or model_id in seen:
            continue
        seen.add(model_id)
        entries.append({
            'id': model_id,
            'label': bits[1] if len(bits) > 1 and bits[1] else model_id,
            'note': bits[2] if len(bits) > 2 else '',
        })
    if not entries:
        for model_id, label, note in defaults:
            if model_id in seen:
                continue
            seen.add(model_id)
            entries.append({'id': model_id, 'label': label, 'note': note})
    return entries


def _provider_state(defn: dict) -> dict:
    key = os.environ.get(defn['key_env'], '').strip()
    base = (os.environ.get(defn['base_env'], '').strip()
            or defn['base_default']).rstrip('/')
    models = _model_entries(os.environ.get(defn['models_env'], ''),
                            defn['models_default'])
    # 清单为空时不要拿 DeepSeek 的默认模型去顶替，否则中转组会多出一个
    # 它其实并不支持的 deepseek-* 选项。
    default_model = (os.environ.get(defn['model_env'], '').strip()
                     or (models[0]['id'] if models else ''))
    if default_model and default_model not in [m['id'] for m in models]:
        models.insert(0, {'id': default_model, 'label': default_model, 'note': '默认'})
    missing = []
    if not key:
        missing.append(defn['key_env'])
    if not base:
        missing.append(defn['base_env'])
    return {
        'key': key,
        'base': base,
        'models': models,
        'default_model': default_model,
        'ready': bool(key) and bool(base),
        'missing': ' / '.join(missing),
    }


def _selection_path() -> Path:
    raw = os.environ.get(ENV_LLM_CONFIG, '').strip()
    return Path(raw) if raw else _SELECTION_FILE


def _read_selection() -> dict:
    try:
        data = json.loads(_selection_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_selection(provider: str, model: str) -> None:
    path = _selection_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps({'provider': provider, 'model': model},
                              ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _effective() -> tuple:
    """当前生效的 (provider, model, 定义, 状态)。

    选择的 provider 如果后来没 Key / 没 base，就自动退回 DeepSeek，
    避免一次误配置把全站文本工具弄挂。
    """
    defs = _provider_defs()
    stored = _read_selection()
    pid = str(stored.get('provider') or '').strip()
    if pid not in defs:
        pid = _DEFAULT_PROVIDER
    state = _provider_state(defs[pid])
    stored_model = (str(stored.get('model') or '').strip()
                    if stored.get('provider') == pid else '')
    usable = _valid_model(stored_model) or bool(state['default_model'])
    if pid != _DEFAULT_PROVIDER and not (state['ready'] and usable):
        pid = _DEFAULT_PROVIDER
        state = _provider_state(defs[pid])
        stored_model = ''
    model = stored_model if _valid_model(stored_model) else state['default_model']
    return pid, model, defs[pid], state


def llm_provider() -> str:
    return _effective()[0]


def llm_provider_label() -> str:
    return _effective()[2]['label']


def llm_key() -> str:
    return _effective()[3]['key']


def llm_base() -> str:
    return _effective()[3]['base']


def llm_model() -> str:
    return _effective()[1]


def llm_endpoint(base: str = '') -> str:
    """Base URL 自动补 /chat/completions，填全了就不动。"""
    url = (base or llm_base()).strip().rstrip('/')
    if url.endswith('/chat/completions'):
        return url
    return url + '/chat/completions'


def llm_ready() -> bool:
    return _effective()[3]['ready']


def llm_missing_hint() -> str:
    pid, _model, defn, state = _effective()
    if state.get('missing'):
        return '服务端没有配置 %s 的模型 Key（%s）' % (defn['label'], state['missing'])
    return '当前文本模型不可用'


# 中转里的 gpt-5 / gpt-6 / o 系列走的是新参数规范：不再接受 max_tokens，
# 也不再接受自定义 temperature。DeepSeek 和别的兼容模型保持原样。
_MODERN_OPENAI_RE = re.compile(r'^(?:gpt-[56]|o[1-9])[A-Za-z0-9._:\-]*$', re.I)


def llm_modern_openai(model: str = '') -> bool:
    """当前是不是「中转 + gpt-5/gpt-6/o 系列」这类新参数规范的模型。"""
    if _effective()[0] != 'openai':
        return False
    name = str(model or llm_model()).strip()
    # 有的中转会带前缀（openai/gpt-6-sol），只看最后一段
    name = name.rsplit('/', 1)[-1]
    return bool(_MODERN_OPENAI_RE.match(name))


def llm_body(body: dict, model: str = '') -> dict:
    """按当前模型修正 chat/completions 请求体，原地修改并返回。

    gpt-5 / gpt-6 / o 系列：max_tokens → max_completion_tokens，
    去掉 temperature / top_p。其它模型（包括 DeepSeek）原样返回。
    """
    if not isinstance(body, dict):
        return body
    name = str(body.get('model') or model or '').strip()
    if not llm_modern_openai(name):
        return body
    limit = body.pop('max_tokens', None)
    if limit is not None and 'max_completion_tokens' not in body:
        body['max_completion_tokens'] = limit
    body.pop('temperature', None)
    body.pop('top_p', None)
    return body


def llm_state() -> dict:
    """给首页模型下拉框用的状态。Key 本身永远不返回给页面。"""
    pid, model, defn, _state = _effective()
    providers = []
    for key, item in _provider_defs().items():
        st = _provider_state(item)
        providers.append({
            'id': key,
            'label': item['label'],
            'ready': st['ready'],
            'key_env': item['key_env'],
            'base_env': item['base_env'],
            'hint': item['hint'],
            'missing': st['missing'],
            'models': st['models'],
            'selected': key == pid,
        })
    return {
        'provider': pid,
        'provider_label': defn['label'],
        'model': model,
        'ready': _state['ready'],
        'providers': providers,
    }


def set_llm_selection(provider: str, model: str = '') -> dict:
    """切换全站文本模型。写 data/llm.json，下一次请求立即生效。"""
    provider = str(provider or '').strip()
    defs = _provider_defs()
    if provider not in defs:
        raise ValueError('未知的模型服务商')
    state = _provider_state(defs[provider])
    if not state['ready']:
        raise ValueError('「%s」还没配置好：缺少 %s'
                         % (defs[provider]['label'], state['missing']))
    model = str(model or '').strip() or state['default_model']
    if not model:
        raise ValueError('「%s」还没有可用的模型，请先配置模型清单'
                         % defs[provider]['label'])
    if not _valid_model(model):
        raise ValueError('模型 ID 只能包含字母、数字和 . _ : / @ + -')
    _write_selection(provider, model)
    return llm_state()


def ocr_key(default: str = '') -> str:
    return os.environ.get(ENV_OCR_KEY, '').strip() or default


# ---------------- 文生图（火山方舟 / Seedream） ----------------
def ark_image_key() -> str:
    return os.environ.get(ENV_ARK_KEY, '').strip()


def ark_image_base() -> str:
    return (os.environ.get(ENV_ARK_BASE, '').strip() or _DEFAULT_ARK_BASE).rstrip('/')


def ark_image_model() -> str:
    return os.environ.get(ENV_ARK_IMAGE_MODEL, '').strip() or _DEFAULT_ARK_IMAGE_MODEL


def ark_image_models() -> list:
    """页面能选的模型清单。

    默认用代码里的清单；设了 ARK_IMAGE_MODELS 就以环境变量为准，格式：

        doubao-seedream-5-0-flash-260915,doubao-seedream-4-5-251128

    想自己起名、写价格备注，用竖线分隔（第二段显示名，第三段备注）：

        doubao-seedream-5-0-flash-260915|5.0 Flash|约 ¥0.12/张

    默认模型（ARK_IMAGE_MODEL）如果不在清单里，会自动补到第一项，
    避免页面没有选项、任务却仍用它。
    """
    raw = os.environ.get(ENV_ARK_IMAGE_MODELS, '').strip()
    entries = []
    if raw:
        for chunk in re.split(r'[,;，；\n]+', raw):
            part = chunk.strip()
            if not part:
                continue
            bits = [bit.strip() for bit in re.split(r'[|｜=]', part)]
            model_id = bits[0]
            if not model_id:
                continue
            label = bits[1] if len(bits) > 1 and bits[1] else model_id
            note = bits[2] if len(bits) > 2 else ''
            entries.append((model_id, label, note))
    if not entries:
        entries = list(_DEFAULT_ARK_IMAGE_MODELS)

    default_id = ark_image_model()
    if not any(item[0] == default_id for item in entries):
        entries.insert(0, (default_id, default_id, '默认模型'))

    seen = set()
    models = []
    for model_id, label, note in entries:
        if model_id in seen:
            continue
        seen.add(model_id)
        models.append({
            'id': model_id,
            'label': label,
            'note': note,
            'default': model_id == default_id,
        })
    return models


def ark_image_model_allowed(model_id: str) -> bool:
    return any(item['id'] == model_id for item in ark_image_models())


def image_gen_ready() -> bool:
    return bool(ark_image_key())


def image_gen_missing_hint() -> str:
    return '服务端没有配置火山方舟 Key（%s）' % ENV_ARK_KEY


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


# ---------------- 联网搜索（CleverSee / IQS） ----------------
def iqs_api_key() -> str:
    return os.environ.get(ENV_IQS_KEY, '').strip()


def iqs_endpoint() -> str:
    return os.environ.get(ENV_IQS_ENDPOINT, '').strip() or _DEFAULT_IQS_ENDPOINT


def iqs_engine_fast() -> str:
    return os.environ.get(ENV_IQS_ENGINE_FAST, '').strip() or _DEFAULT_IQS_ENGINE_FAST


def iqs_engine_deep() -> str:
    return os.environ.get(ENV_IQS_ENGINE_DEEP, '').strip() or _DEFAULT_IQS_ENGINE_DEEP


def iqs_ready() -> bool:
    return bool(iqs_api_key())
