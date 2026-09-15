# -*- coding: utf-8 -*-
"""
模型配置：Key / 接口地址 / 模型名一律从服务端取，页面不再手填
==============================================================
读取顺序（前面有就不看后面）：

  1. 真实环境变量   —— 部署时在平台面板里配，优先级最高
  2. 项目根目录 .env —— 本地开发就改这个文件（已在 .gitignore 里，不会进仓库）
  3. 代码里的默认值 —— 只有接口地址和模型名有默认值，Key 没有

四个变量：

  DEEPSEEK_API_KEY   DeepSeek 的 Key                            必填
  DEEPSEEK_BASE_URL  接口地址，默认 https://api.deepseek.com      可选
  DEEPSEEK_MODEL     模型名，默认 deepseek-chat                   可选
  OCR_SPACE_API_KEY  OCR.space 的 Key，不填就用公共免费 Key（会被限流）  可选

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

_DEFAULT_BASE = 'https://api.deepseek.com'
_DEFAULT_MODEL = 'deepseek-chat'

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
