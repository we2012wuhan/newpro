# -*- coding: utf-8 -*-
"""
AI 文章写作（主题 -> 大纲 -> 带配图的完整文章）
=================================================
页面在 /article，接口都在 /article/api/*。

为什么分两步
------------
一次生成整篇长文，用户很难在开头就把方向掰回来。这里先只生成大纲：
标题、导语、每节小标题和要点。用户确认或改完之后，再消耗正文和配图费用。
大纲接口不落库，确认后的版本随创建任务一起提交。

生成流程
--------
1. 按大纲逐节调用文本模型写正文；
2. 用便宜一些的 Seedream Flash 生成 1 张封面 + 若干张内文图；
3. 保存到 data/generated/article/ 下，页面轮询进度；
4. 导出网页版、公众号内联样式版、Markdown 和 ZIP。

边界
----
- 公众号只做到「复制正文 + 图片包」；图片要手动上传，不接公众号 API、不自动发布。
- 所有模型输出在导出前都会 HTML 转义，不会被当成 HTML 注入。
- 缺失 Key、服务商报错都只返回人话提示，Key 永远不会出现在前端。
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import shutil
import threading
import time
import zipfile
from collections import deque
from pathlib import Path
from urllib.parse import quote

import requests
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

import image_gen
import model_config
import storage

router = APIRouter()

TOOL = 'article'
MAX_TOPIC = 200
MAX_MATERIAL = 8000
MIN_SECTIONS = 3
MAX_SECTIONS = 6
MAX_IMAGE_COUNT = 6
DEFAULT_IMAGE_COUNT = 3
DEFAULT_DAILY_IMAGES = 24
DEFAULT_TTL_DAYS = 7
LLM_TIMEOUT = (10, 180)
OUTLINE_LIMIT = 6              # 每用户每分钟最多请求几次大纲
REFINE_LIMIT = 12              # 每用户每分钟最多润色 / 扩写几次
MAX_EDIT_PARAGRAPH = 2000      # 编辑后的单段上限；原生成段落通常不超过 500 字
MAX_SELECTION = 1600
FLASH_MODEL = 'doubao-seedream-5-0-flash-260915'

REFINE_ACTIONS = {
    'polish': ('润色', '保持原意、事实和段落位置不变，让表达更准确、顺畅、有节奏；'
                       '不添加新的信息或例子，长度和原文接近。'),
    'expand': ('扩展', '在保持原意和语气的前提下，把选中内容写得更充分：'
                       '补充解释、具体场景、因果或自然过渡；长度约为原文的 1.5～2 倍，'
                       '不要重复，不要编造无法核实的数字、人名、时间或研究结论。'),
}

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'article.html'
_JOB_ID_RE = re.compile(r'^[0-9a-f]{32}$')

GENRES = {
    'insight': ('观点', '提出明确判断，用事实、例子和反例推进，避免空泛说教。'),
    'tutorial': ('教程', '按步骤讲清做法，交代前置条件、常见坑和验收标准。'),
    'story': ('故事', '用具体场景、人物和转折推进，少总结，多呈现。'),
    'news': ('资讯', '交代背景、关键变化和影响，区分事实与判断。'),
    'science': ('科普', '把复杂概念讲清楚，多用生活类比，专业词第一次出现要解释。'),
}

TONES = {
    'warm': ('温暖', '语气亲切、有耐心，像和读者坐下来慢慢聊。'),
    'professional': ('专业', '用词准确、结构清楚，不堆术语，也不油滑。'),
    'calm': ('克制', '少用感叹和夸张，把力气放在事实和逻辑上。'),
    'light': ('轻松', '节奏明快，可以有轻微的幽默，但不轻浮。'),
    'sharp': ('犀利', '观点鲜明，敢下判断，但不攻击具体个人。'),
}

LENGTHS = {
    'short': {'label': '短', 'sections': '3', 'paragraphs': '2～3', 'target': 1200},
    'medium': {'label': '中', 'sections': '4～5', 'paragraphs': '2～4', 'target': 2200},
    'long': {'label': '长', 'sections': '6', 'paragraphs': '3～4', 'target': 3200},
}

IMAGE_STYLES = {
    'illustration': '插画',
    'photo': '摄影',
    'watercolor': '水彩',
    'minimal': '极简',
    '3d': '3D 渲染',
    'notion': 'Notion 信息图',
    'comic': '漫画信息图',
}

# 信息图风格需要中文标注，所以不能像普通配图那样一律禁止文字。
INFOGRAPHIC_STYLES = ('notion', 'comic')
IMAGE_NEGATIVE = '文字、字母、水印、logo、边框、低清晰度、畸形'
STYLE_NEGATIVES = {
    'notion': '水印、logo、乱码错字、低清晰度、畸形、密集小字、粗黑边框',
    'comic': '水印、logo、乱码错字、低清晰度、畸形、照片写实、3D 效果',
}
JSON_SYSTEM = (
    '你是一位资深中文编辑。你只输出一个合法的 JSON 对象，'
    '不要 Markdown 代码块，不要解释，不要任何前后缀。'
)

_TITLE_KEYS = ('title', 'headline', 'name')
_SUMMARY_KEYS = ('summary', 'intro', 'lead', 'description')
_SECTIONS_KEYS = ('sections', 'outline', 'parts', 'chapters')
_HEADING_KEYS = ('heading', 'title', 'name', 'section')
_POINTS_KEYS = ('points', 'keyPoints', 'bullets', 'ideas', 'items')
_IMAGE_KEYS = ('imageIdea', 'image_idea', 'image', 'visual', 'imagePrompt', 'image_prompt')
_PARAGRAPH_KEYS = ('paragraphs', 'body', 'content', 'text', 'paragraph')


class ArticleError(RuntimeError):
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
    return _env_int('ARTICLE_DAILY_IMAGES', DEFAULT_DAILY_IMAGES, 1, 200)


def _ttl_days() -> int:
    return _env_int('ARTICLE_TTL_DAYS', DEFAULT_TTL_DAYS, 1, 90)


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


def _clean_text(value, limit: int) -> str:
    """清理模型文本：去代码块、HTML 标签、Markdown 标记，压缩空白。"""
    text = str(value or '')
    text = re.sub(r'```.*?```', '', text, flags=re.S)
    text = re.sub(r'<[^>]{0,300}>', '', text)
    text = re.sub(r'[*_`#>~|]+', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit]


def _first(data: dict, keys) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ''):
            return value if isinstance(value, str) else str(value)
    return ''


# ---------------------------------------------------------------
# 文件与目录
# ---------------------------------------------------------------
def _output_root() -> Path:
    raw = (os.environ.get('ARTICLE_DIR') or '').strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return storage.db_path().resolve().parent / 'generated' / 'article'


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
        rows = storage.article_job_expired(user, cutoff)
    except storage.StorageUnavailable:
        return
    for row in rows:
        if row.get('status') in ('queued', 'running'):
            continue
        _remove_job_dir(user, row['id'])
        try:
            storage.article_job_delete(user, row['id'])
        except storage.StorageUnavailable:
            return


def _job_image_url(job_id: str, index: int) -> str:
    return '/article/api/jobs/%s/images/%d' % (job_id, index)


def _image_path(job: dict, image: dict) -> Path:
    name = str(image.get('name') or '')
    if not name or Path(name).name != name:
        raise ValueError('图片文件名不正确')
    return _job_dir(job['user'], job['id']) / name


# ---------------------------------------------------------------
# 文本模型
# ---------------------------------------------------------------
def _provider_error(resp) -> ArticleError:
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
        return ArticleError('文本模型鉴权失败（HTTP %d），检查服务端的模型 Key。%s'
                            % (status, detail))
    if status == 429:
        return ArticleError('文本模型限流或额度不足（HTTP 429）：%s' % detail)
    return ArticleError('文本模型返回异常（HTTP %d）：%s' % (status, detail))


def _chat(messages: list, temperature: float, max_tokens: int) -> str:
    key = model_config.llm_key()
    if not key:
        raise ArticleError(model_config.llm_missing_hint())
    model = model_config.llm_model()
    body = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
    }
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
        raise ArticleError('写文章超时，请稍后重试') from exc
    except requests.exceptions.RequestException as exc:
        raise ArticleError('连不上文本模型：%s' % _short(exc)) from exc
    if resp.status_code >= 400:
        raise _provider_error(resp)
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ArticleError('文本模型返回的不是 JSON，请重试') from exc
    try:
        content = payload['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        content = ''
    if isinstance(content, list):
        content = ''.join(
            str(part.get('text') or '') if isinstance(part, dict) else str(part)
            for part in content)
    return str(content or '')


def _extract_json(raw: str):
    text = (raw or '').strip()
    if not text:
        raise ArticleError('模型没有返回内容，请重试')
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
    raise ArticleError('模型这次没有返回可用的 JSON，请重试')


def _outline_prompt(topic: str, material: str, genre: str, tone: str,
                    length: str) -> str:
    spec = LENGTHS.get(length) or LENGTHS['medium']
    genre_label, genre_hint = GENRES.get(genre) or GENRES['insight']
    tone_label, tone_hint = TONES.get(tone) or TONES['warm']
    parts = [
        '请为一篇中文文章设计大纲。现在只设计结构和要点，不要写正文。',
        '',
        '【主题】' + topic,
        '【文章类型】%s：%s' % (genre_label, genre_hint),
        '【语气】%s：%s' % (tone_label, tone_hint),
        '【篇幅】%s，正文约 %d 字，分 %s 节。' % (spec['label'], spec['target'],
                                              spec['sections']),
        '',
        '【要求】',
        '1. 标题具体、有信息量，不要“浅谈”“漫谈”“关于……的思考”这种空标题。',
        '2. 导语用 1～2 句话说明文章要解决什么问题，不超过 120 字。',
        '3. 每节要有明确推进关系：提出问题、展开、转折或收束；不要同义反复。',
        '4. 每节给 2～4 个要点，每个要点一句话，便于后面扩写成段。',
        '5. 每节给一个“画面想法”：具体、可视、适合插画，不要写抽象概念。',
        '6. 没有参考资料时，不要编造具体数字、人名、时间、研究结论。',
        '7. 不要写小标题编号，不要输出 Markdown。',
        '',
        '【输出格式】严格输出 JSON：',
        '{"title":"标题","summary":"导语","sections":['
        '{"heading":"小标题","points":["要点1","要点2"],"imageIdea":"画面想法"}]}',
        '只输出 JSON 本身。',
    ]
    if material:
        parts += ['', '【参考资料】', material[:6000]]
    return '\n'.join(parts)


def _normalize_outline(payload, topic: str) -> dict:
    if isinstance(payload, dict) and isinstance(payload.get('outline'), dict):
        payload = payload['outline']
    if not isinstance(payload, dict):
        raise ArticleError('大纲格式不正确，请重试')

    title = _clean_text(_first(payload, _TITLE_KEYS), 80) or _clean_text(topic, 80)
    summary = _clean_text(_first(payload, _SUMMARY_KEYS), 300)

    raw_sections = None
    for key in _SECTIONS_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            raw_sections = value
            break
    if raw_sections is None:
        raise ArticleError('大纲里没有章节，请重试')

    sections = []
    for item in raw_sections:
        if len(sections) >= MAX_SECTIONS:
            break
        if isinstance(item, str):
            heading, points, idea = item, [], ''
        elif isinstance(item, dict):
            heading = _clean_text(_first(item, _HEADING_KEYS), 50)
            raw_points = None
            for key in _POINTS_KEYS:
                if isinstance(item.get(key), list):
                    raw_points = item[key]
                    break
                if isinstance(item.get(key), str):
                    raw_points = re.split(r'[\n；;]+', item[key])
                    break
            points = []
            for point in raw_points or []:
                text = _clean_text(point, 100)
                if text and text not in points:
                    points.append(text)
                if len(points) >= 4:
                    break
            idea = _clean_text(_first(item, _IMAGE_KEYS), 200)
        else:
            continue
        if not heading:
            continue
        if not points:
            # 有些模型会漏掉 points；用标题补一个最低限度的提纲，后面正文仍能写。
            points = [heading]
        sections.append({
            'heading': heading,
            'points': points[:4],
            'imageIdea': idea or heading,
        })

    if len(sections) < MIN_SECTIONS:
        raise ArticleError('大纲只生成了 %d 节，至少需要 %d 节，请重新生成'
                           % (len(sections), MIN_SECTIONS))

    return {
        'title': title,
        'summary': summary,
        'sections': sections,
    }


def _llm_outline(topic: str, material: str, genre: str, tone: str,
                 length: str) -> dict:
    messages = [
        {'role': 'system', 'content': JSON_SYSTEM},
        {'role': 'user', 'content': _outline_prompt(topic, material, genre, tone, length)},
    ]
    last_error = None
    for attempt in (0, 1):
        content = _chat(messages, 0.75 if attempt == 0 else 0.35, 3200)
        try:
            return _normalize_outline(_extract_json(content), topic)
        except ArticleError as exc:
            last_error = exc
            if attempt == 0:
                messages = messages + [
                    {'role': 'assistant', 'content': content[:5000]},
                    {'role': 'user', 'content':
                        '上面的输出不符合要求（%s）。请重新输出一个完整合法的 JSON 对象，'
                        '至少 %d 节，每节要有 heading、2～4 条 points 和 imageIdea；'
                        '只输出 JSON。' % (_short(exc, 100), MIN_SECTIONS)},
                ]
                continue
            raise
    raise last_error or ArticleError('大纲生成失败，请重试')


def _section_prompt(job: dict, section: dict, index: int, previous_tail: str) -> str:
    outline = job.get('outline') if isinstance(job.get('outline'), dict) else {}
    spec = LENGTHS.get(job.get('length') or 'medium') or LENGTHS['medium']
    genre_label, genre_hint = GENRES.get(job.get('genre') or 'insight') or GENRES['insight']
    tone_label, tone_hint = TONES.get(job.get('tone') or 'warm') or TONES['warm']
    parts = [
        '你在写一篇中文文章的第 %d 节。只写这一节，不要写标题页、目录或全文总结。'
        % index,
        '',
        '【全文标题】' + str(outline.get('title') or job.get('title') or job.get('topic') or ''),
        '【全文导语】' + str(outline.get('summary') or job.get('summary') or ''),
        '【本节小标题】' + str(section.get('heading') or ''),
        '【本节要点】',
    ]
    parts += ['- ' + str(point) for point in (section.get('points') or [])]
    parts += [
        '',
        '【文章类型】%s：%s' % (genre_label, genre_hint),
        '【语气】%s：%s' % (tone_label, tone_hint),
        '【篇幅】本节写 %s 段，每段 80～300 字；全文约 %d 字。'
        % (spec['paragraphs'], spec['target']),
        '',
        '【写法】',
        '1. 第一段直接进入本节问题，不要“首先”“接下来”“总而言之”。',
        '2. 用具体例子、场景或因果链推进；不要只写正确的空话。',
        '3. 如果上一节有结尾，本节开头要和它自然衔接。',
        '4. 只写纯文本段落：不要 Markdown、列表、小标题、链接、HTML、表情符号。',
        '5. 不编造无法核实的具体数字、人名、时间或研究结论。',
        '6. 同时给这一节配一张图：imagePrompt 描述具体画面，不要抽象概念，'
        '画面里不要出现文字。',
    ]
    if previous_tail:
        parts += ['', '【上一节结尾】' + previous_tail]
    material = str(job.get('material') or '')
    if material:
        parts += ['', '【参考资料节选】', material[:3000]]
    parts += [
        '',
        '【输出格式】严格输出 JSON：',
        '{"heading":"本节小标题","paragraphs":["第一段","第二段"],'
        '"imagePrompt":"给这一节配图的画面描述"}',
        '只输出 JSON 本身。',
    ]
    return '\n'.join(parts)


def _normalize_section(payload, fallback: dict) -> dict:
    if not isinstance(payload, dict):
        raise ArticleError('正文格式不正确，请重试')
    heading = _clean_text(_first(payload, _HEADING_KEYS), 50) or fallback.get('heading') or ''

    raw_paragraphs = None
    for key in _PARAGRAPH_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            raw_paragraphs = value
            break
        if isinstance(value, str):
            raw_paragraphs = re.split(r'\n\s*\n|\n', value)
            break
    paragraphs = []
    for item in raw_paragraphs or []:
        text = _clean_text(item, 500)
        if text:
            paragraphs.append(text)
        if len(paragraphs) >= 4:
            break
    if not paragraphs:
        raise ArticleError('模型没有写出本节正文，请重试')

    image_prompt = _clean_text(_first(payload, _IMAGE_KEYS), 300)
    return {
        'heading': heading,
        'paragraphs': paragraphs,
        'imagePrompt': image_prompt or str(fallback.get('imageIdea') or heading),
    }


def _llm_section(job: dict, section: dict, index: int, previous_tail: str) -> dict:
    messages = [
        {'role': 'system', 'content': JSON_SYSTEM},
        {'role': 'user', 'content': _section_prompt(job, section, index, previous_tail)},
    ]
    last_error = None
    for attempt in (0, 1):
        content = _chat(messages, 0.8 if attempt == 0 else 0.35, 2200)
        try:
            return _normalize_section(_extract_json(content), section)
        except ArticleError as exc:
            last_error = exc
            if attempt == 0:
                messages = messages + [
                    {'role': 'assistant', 'content': content[:5000]},
                    {'role': 'user', 'content':
                        '上面的输出不符合要求（%s）。请只输出一个合法 JSON 对象，'
                        '包含 heading、paragraphs（2～4 段纯文本）和 imagePrompt；'
                        '不要 Markdown、列表或任何解释。' % _short(exc, 100)},
                ]
                continue
            raise
    raise last_error or ArticleError('正文生成失败，请重试')


# ---------------------------------------------------------------
# 段落润色 / 扩展
# ---------------------------------------------------------------
def _refine_prompt(job: dict, section: dict, paragraph: str, selected: str,
                   action: str) -> str:
    outline = job.get('outline') if isinstance(job.get('outline'), dict) else {}
    action_label, action_hint = REFINE_ACTIONS[action]
    genre_label, genre_hint = GENRES.get(job.get('genre') or 'insight') or GENRES['insight']
    tone_label, tone_hint = TONES.get(job.get('tone') or 'warm') or TONES['warm']
    parts = [
        '你在修改一篇中文文章中的一段文字。只替换「选中文字」，并让它和整段自然衔接。',
        '',
        '【全文标题】' + str(outline.get('title') or job.get('title') or ''),
        '【全文导语】' + str(outline.get('summary') or ''),
        '【本节小标题】' + str(section.get('heading') or ''),
        '【文章类型】%s：%s' % (genre_label, genre_hint),
        '【语气】%s：%s' % (tone_label, tone_hint),
        '',
        '【这一段完整原文】',
        paragraph,
        '',
        '【需要处理的选中文字】',
        selected,
        '',
        '【任务】%s：%s' % (action_label, action_hint),
        '',
        '【输出要求】',
        '1. 只输出替换「选中文字」后的那一小段文字本身，不要输出整段或上下文。',
        '2. 不要加引号、标签、标题、解释或 Markdown，不要写“润色如下”之类的话。',
        '3. 和前后文衔接自然，不要重复相邻句子。',
        '4. 不编造无法核实的具体数字、人名、时间或研究结论。',
    ]
    points = section.get('points')
    if isinstance(points, list) and points:
        parts.insert(5, '【本节要点】' + '；'.join(str(point) for point in points[:4]))
    return '\n'.join(parts)


def _llm_refine(job: dict, section: dict, paragraph: str, selected: str,
                action: str) -> str:
    messages = [
        {'role': 'system', 'content':
            '你是一位资深中文编辑。你只输出修改后的文字本身，不要解释，不要 Markdown。'},
        {'role': 'user', 'content':
            _refine_prompt(job, section, paragraph, selected, action)},
    ]
    temperature = 0.55 if action == 'polish' else 0.7
    max_tokens = max(600, min(3000, int(len(selected) * 2.2) + 300))
    raw = _chat(messages, temperature, max_tokens)
    text = _clean_text(raw, MAX_EDIT_PARAGRAPH + 1)
    text = text.strip('“”"\'「」『』')
    if not text:
        raise ArticleError('模型没有返回可用文字，请重试')
    if len(text) > MAX_EDIT_PARAGRAPH:
        raise ArticleError('改写结果太长了，缩小选中范围再试')
    return text


def _replace_selection(paragraph: str, start: int, end: int,
                       replacement: str) -> str:
    merged = _clean_text(paragraph[:start] + replacement + paragraph[end:],
                         MAX_EDIT_PARAGRAPH + 1)
    if len(merged) > MAX_EDIT_PARAGRAPH:
        raise ArticleError('替换后这一段太长了，缩小选中范围或改用润色')
    return merged


def _clean_selection(body) -> dict:
    if not isinstance(body, dict):
        raise ValueError('请求格式不正确')
    try:
        section_index = int(body.get('sectionIndex'))
        paragraph_index = int(body.get('paragraphIndex'))
        start = int(body.get('start'))
        end = int(body.get('end'))
    except (TypeError, ValueError):
        raise ValueError('选中的文字位置不正确，请重新选择')
    if section_index < 0 or paragraph_index < 0 or start < 0 or end <= start:
        raise ValueError('选中的文字位置不正确，请重新选择')
    if end - start > MAX_SELECTION:
        raise ValueError('一次最多处理 %d 字，请缩小选中范围' % MAX_SELECTION)
    action = str(body.get('action') or '').strip().lower()
    if action not in REFINE_ACTIONS:
        raise ValueError('不支持的润色方式')
    return {
        'section_index': section_index,
        'paragraph_index': paragraph_index,
        'start': start,
        'end': end,
        'action': action,
    }


def _edit_target(job: dict, section_index: int, paragraph_index: int) -> tuple:
    sections = job.get('sections') if isinstance(job.get('sections'), list) else []
    if section_index >= len(sections) or not isinstance(sections[section_index], dict):
        raise ValueError('找不到这一段，文章可能已经更新')
    section = sections[section_index]
    paragraphs = section.get('paragraphs')
    if not isinstance(paragraphs, list) or paragraph_index >= len(paragraphs):
        raise ValueError('找不到这一段，文章可能已经更新')
    paragraph = str(paragraphs[paragraph_index] or '')
    if not paragraph:
        raise ValueError('这一段是空的，不能编辑')
    return sections, section, paragraph


def _sync_history(job: dict, sections: list, body_chars: int) -> None:
    try:
        records = storage.list_records(TOOL, job.get('user') or '', 100)
        for record in records:
            payload = record.get('payload')
            if not isinstance(payload, dict) or payload.get('jobId') != job['id']:
                continue
            payload = dict(payload)
            payload['bodyChars'] = int(body_chars)
            payload['sections'] = len(sections)
            storage.update_record(job['user'], record['id'], payload=payload)
            break
    except (storage.StorageUnavailable, ValueError, TypeError):
        pass


# ---------------------------------------------------------------
# 图片
# ---------------------------------------------------------------
def _default_image_model() -> str:
    models = [item['id'] for item in model_config.ark_image_models()]
    if FLASH_MODEL in models:
        return FLASH_MODEL
    return model_config.ark_image_model()


def _outline_section(job: dict, index: int) -> dict:
    outline = job.get('outline') if isinstance(job.get('outline'), dict) else {}
    sections = outline.get('sections') if isinstance(outline.get('sections'), list) else []
    if 0 <= index < len(sections) and isinstance(sections[index], dict):
        return sections[index]
    return {}


def _points_text(entry: dict) -> str:
    points = entry.get('points')
    if not isinstance(points, list):
        return ''
    return '；'.join(str(point).strip() for point in points[:4] if str(point).strip())


def _cover_prompt(job: dict) -> str:
    outline = job.get('outline') if isinstance(job.get('outline'), dict) else {}
    style = job.get('image_style') or 'illustration'
    title = str(outline.get('title') or job.get('topic') or '')
    summary = str(outline.get('summary') or '')
    if style == 'notion':
        # 风格前后缀由 image_gen 负责；这里只描述「画什么」，并允许中文标注。
        return (
            '为文章《%s》制作横版 Notion 风格信息图封面。文章导语：%s。'
            '把文章的核心判断画成 3～4 个手绘图标、简笔人物或小图示，'
            '用箭头、便签和简短中文标签串起来；突出标题关键词和一句话结论，'
            '中文文字少而大，留白充足。'
            % (title, summary or _points_text(_outline_section(job, 0)))
        )
    if style == 'comic':
        return (
            '为文章《%s》制作竖版三格漫画信息图封面，中文手写标题。'
            '三格依次呈现：读者熟悉的问题或场景、一个转折或发现、一句话结论。'
            '用人物或场景围绕主题展开，每格配简短中文标签或对话气泡；导语：%s。'
            % (title, summary or _points_text(_outline_section(job, 0)))
        )

    genre_label = (GENRES.get(job.get('genre') or 'insight') or GENRES['insight'])[0]
    return (
        '为一篇中文%s文章制作编辑级封面插画。标题：%s。导语：%s。'
        '构图要有明确视觉中心、留白和杂志感，气质与文章一致；'
        '画面里不要出现任何文字、字母或水印。'
        % (genre_label, title, summary)
    )


def _inline_prompt(job: dict, section: dict, index: int) -> str:
    outline = job.get('outline') if isinstance(job.get('outline'), dict) else {}
    style = job.get('image_style') or 'illustration'
    title = str(outline.get('title') or job.get('topic') or '')
    heading = str(section.get('heading') or '')
    idea = section.get('imagePrompt') or section.get('imageIdea') or heading
    if style in INFOGRAPHIC_STYLES:
        points = (_points_text(_outline_section(job, index))
                  or str(section.get('imagePrompt') or heading))
        if style == 'notion':
            return (
                '为文章《%s》的小节“%s”制作横版 Notion 风格信息图。本节要点：%s。'
                '围绕 2～3 个核心点，用手绘图标、箭头、便签和小图示组织成清晰的信息图；'
                '每块配简短的中文标签，中文文字少而大，不要整段文字。'
                % (title, heading, points)
            )
        return (
            '为文章《%s》的小节“%s”制作竖版三格漫画信息图，中文手写标题。'
            '按“场景或问题 → 转折 → 结论”分配三格，人物与场景围绕本节内容：%s；'
            '画面参考：%s。每格配简短的中文标签或对话气泡。'
            % (title, heading, points, str(idea))
        )
    return (
        '为文章《%s》的小节“%s”制作配图。画面内容：%s。'
        '构图干净、有编辑感，和全文气质一致；不要出现文字、字母、水印或 logo。'
        % (title, heading, str(idea))
    )


def _image_tasks(job: dict) -> list:
    style = job.get('image_style') or 'illustration'
    # 信息图有自己的版式，沿用 /image-gen 的固定比例；中文标注需要更清晰，
    # 所以内文图也用 2K。普通配图仍保持封面 2K、内文 1K。
    fixed_ratio = image_gen.STYLE_RATIOS.get(style)
    cover_ratio = fixed_ratio or '16:9'
    inline_ratio = fixed_ratio or '4:3'
    inline_resolution = '2K' if fixed_ratio else '1K'
    tasks = [{
        'role': 'cover',
        'sectionIndex': None,
        'ratio': cover_ratio,
        'resolution': '2K',
        'prompt': _cover_prompt(job),
        'name': 'cover',
    }]
    sections = job.get('sections') or []
    # image_count 是用户选的“总配图数（含封面）”，内文图最多每节一张。
    requested = max(1, int(job.get('image_count') or 1))
    inline_count = min(max(requested - 1, 0), len(sections))
    for index in range(inline_count):
        section = sections[index]
        tasks.append({
            'role': 'inline',
            'sectionIndex': index,
            'ratio': inline_ratio,
            'resolution': inline_resolution,
            'prompt': _inline_prompt(job, section, index),
            'name': 'section-%02d' % (index + 1),
        })
    return tasks


def _generate_images(job: dict) -> tuple:
    """逐张出图；单张失败只记 warning，不拖垮已经写好的正文。"""
    job_id = job['id']
    tasks = _image_tasks(job)
    total = max(1, len(tasks))
    images, warnings = [], []
    style = job.get('image_style') or 'illustration'
    negative = STYLE_NEGATIVES.get(style, IMAGE_NEGATIVE)
    model = job.get('image_model') or _default_image_model()
    directory = _job_dir(job['user'], job_id)
    directory.mkdir(parents=True, exist_ok=True)

    for position, task in enumerate(tasks, start=1):
        try:
            data, ext = image_gen.generate_image_bytes(
                task['prompt'], ratio=task['ratio'], resolution=task['resolution'],
                model=model, style=style, negative=negative)
            name = '%s.%s' % (task['name'], ext)
            (directory / name).write_bytes(data)
            images.append({
                'index': position,
                'role': task['role'],
                'sectionIndex': task['sectionIndex'],
                'name': name,
            })
        except image_gen.ImageGenError as exc:
            label = ('封面' if task['role'] == 'cover'
                     else '第 %d 张内文图' % (task.get('sectionIndex', 0) + 1))
            warnings.append('%s：%s' % (label, _short(exc, 120)))
        except Exception as exc:                       # 保存失败也不要丢正文
            warnings.append('%s：%s' % (task['name'], _short(exc, 120)))
        try:
            storage.article_job_update(job_id, {
                'images': images,
                'done_images': len(images),
                'progress': 60 + int(35 * position / total),
            })
        except storage.StorageUnavailable:
            pass

    warning = ''
    if warnings:
        warning = '部分配图没有生成成功：' + '；'.join(warnings[:3])
        if len(warnings) > 3:
            warning += '；另有 %d 张失败' % (len(warnings) - 3)
    return images, warning[:800]


# ---------------------------------------------------------------
# 后台调度
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
            storage.article_job_recover_running(
                '服务重启，文章生成中断；已经用掉的模型和图片可能产生费用，请核对账单后重新生成')
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
            job = storage.article_job_claim_next()
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
                storage.article_job_update(job['id'], {
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
        thread = threading.Thread(target=_worker_loop, name='article-worker',
                                  daemon=True)
        thread.start()
        _worker_started = True


def _process_job(job: dict) -> None:
    job_id = job['id']
    outline = job.get('outline') if isinstance(job.get('outline'), dict) else {}
    outline_sections = outline.get('sections') if isinstance(outline.get('sections'), list) else []
    if len(outline_sections) < MIN_SECTIONS:
        storage.article_job_update(job_id, {
            'status': 'failed',
            'stage': 'failed',
            'error': '大纲不完整，无法写正文',
            'finished_at': storage.now_str(),
        })
        return

    sections = []
    body_chars = 0
    previous_tail = ''
    total_sections = len(outline_sections)
    try:
        storage.article_job_update(job_id, {
            'stage': 'writing',
            'progress': 5,
            'total_sections': total_sections,
            'error': '',
        })
        for index, section in enumerate(outline_sections):
            written = _llm_section(job, section, index + 1, previous_tail)
            sections.append(written)
            body_chars += sum(len(paragraph) for paragraph in written['paragraphs'])
            previous_tail = written['paragraphs'][-1][-160:] if written['paragraphs'] else ''
            storage.article_job_update(job_id, {
                'sections': sections,
                'done_sections': len(sections),
                'body_chars': body_chars,
                'progress': 5 + int(55 * len(sections) / total_sections),
            })

        storage.article_job_update(job_id, {
            'stage': 'images',
            'progress': 60,
            'sections': sections,
            'body_chars': body_chars,
        })
        images, warning = _generate_images({**job, 'sections': sections})

        storage.article_job_update(job_id, {
            'status': 'succeeded',
            'stage': 'done',
            'progress': 100,
            'sections': sections,
            'images': images,
            'done_images': len(images),
            'body_chars': body_chars,
            'warning': warning,
            'error': '',
            'finished_at': storage.now_str(),
        })
        _remember_history({**job, 'title': outline.get('title') or job.get('title') or ''},
                          sections, images, body_chars, warning)
    except ArticleError as exc:
        try:
            storage.article_job_update(job_id, {
                'status': 'failed',
                'stage': 'failed',
                'sections': sections,
                'done_sections': len(sections),
                'body_chars': body_chars,
                'error': str(exc),
                'finished_at': storage.now_str(),
            })
        except storage.StorageUnavailable:
            pass
    except (OSError, storage.StorageUnavailable) as exc:
        try:
            storage.article_job_update(job_id, {
                'status': 'failed',
                'stage': 'failed',
                'sections': sections,
                'error': '保存生成结果失败：%s' % _short(exc),
                'finished_at': storage.now_str(),
            })
        except storage.StorageUnavailable:
            pass


def _remember_history(job: dict, sections: list, images: list, body_chars: int,
                      warning: str) -> None:
    outline = job.get('outline') if isinstance(job.get('outline'), dict) else {}
    payload = {
        'jobId': job['id'],
        'topic': job.get('topic') or '',
        'title': job.get('title') or job.get('topic') or '',
        'genre': job.get('genre') or 'insight',
        'length': job.get('length') or 'medium',
        'bodyChars': int(body_chars),
        'sections': len(sections),
        'images': len(images),
        'warning': warning,
        'sourceIdea': str(outline.get('sourceIdea') or ''),
    }
    try:
        storage.add_record(TOOL, job['user'],
                           str(payload['title'])[:80], payload)
    except storage.StorageUnavailable:
        pass


# ---------------------------------------------------------------
# 公开数据
# ---------------------------------------------------------------
def _public_image(job_id: str, image: dict) -> dict:
    index = int(image.get('index') or 0)
    return {
        'index': index,
        'role': image.get('role') or 'inline',
        'sectionIndex': image.get('sectionIndex'),
        'name': image.get('name') or '',
        'url': _job_image_url(job_id, index) if index else '',
    }


def _public_job(job: dict, with_content: bool = False) -> dict:
    outline = job.get('outline') if isinstance(job.get('outline'), dict) else {}
    data = {
        'id': job['id'],
        'topic': job.get('topic') or '',
        'title': job.get('title') or job.get('topic') or '',
        'summary': job.get('summary') or '',
        'sourceIdea': str(outline.get('sourceIdea') or ''),
        'genre': job.get('genre') or 'insight',
        'genreLabel': (GENRES.get(job.get('genre') or 'insight') or GENRES['insight'])[0],
        'tone': job.get('tone') or 'warm',
        'toneLabel': (TONES.get(job.get('tone') or 'warm') or TONES['warm'])[0],
        'length': job.get('length') or 'medium',
        'lengthLabel': (LENGTHS.get(job.get('length') or 'medium') or LENGTHS['medium'])['label'],
        'status': job.get('status') or 'queued',
        'stage': job.get('stage') or 'queued',
        'progress': int(job.get('progress') or 0),
        'totalSections': int(job.get('total_sections') or 0),
        'doneSections': int(job.get('done_sections') or 0),
        'totalImages': int(job.get('total_images') or 0),
        'doneImages': int(job.get('done_images') or 0),
        'imageCount': int(job.get('image_count') or 0),
        'imageStyle': job.get('image_style') or 'illustration',
        'imageModel': job.get('image_model') or '',
        'model': job.get('model') or '',
        'bodyChars': int(job.get('body_chars') or 0),
        'warning': job.get('warning') or '',
        'error': job.get('error') or '',
        'images': [_public_image(job['id'], image)
                   for image in (job.get('images') or []) if isinstance(image, dict)],
        'createdAt': job.get('created_at') or '',
        'startedAt': job.get('started_at') or '',
        'finishedAt': job.get('finished_at') or '',
    }
    if with_content:
        data['outline'] = job.get('outline') if isinstance(job.get('outline'), dict) else {}
        data['sections'] = [section for section in (job.get('sections') or [])
                            if isinstance(section, dict)]
        ready = bool(data['sections'])
        data['export'] = {
            'html': '/article/api/jobs/%s/export/html' % job['id'],
            'wechat': '/article/api/jobs/%s/export/wechat' % job['id'],
            'markdown': '/article/api/jobs/%s/export/md' % job['id'],
            'zip': '/article/api/jobs/%s/export/zip' % job['id'],
        } if ready else {}
    return data


def _ready_info() -> dict:
    llm_ok = model_config.llm_ready()
    image_ok = model_config.image_gen_ready()
    hints = []
    if not llm_ok:
        hints.append(model_config.llm_missing_hint())
    if not image_ok:
        hints.append(model_config.image_gen_missing_hint())
    return {
        'ready': llm_ok and image_ok,
        'llmReady': llm_ok,
        'imageReady': image_ok,
        'missing': '；'.join(hints),
    }


# ---------------------------------------------------------------
# 导出渲染
# ---------------------------------------------------------------
def _images_by_role(job: dict) -> tuple:
    cover = None
    inline = {}
    for image in job.get('images') or []:
        if not isinstance(image, dict):
            continue
        if image.get('role') == 'cover' and cover is None:
            cover = image
        elif image.get('sectionIndex') not in (None, ''):
            inline[int(image.get('sectionIndex'))] = image
    return cover, inline


def _image_src(job: dict, image: dict, mode: str) -> str:
    name = str(image.get('name') or '')
    if mode == 'url':
        return _job_image_url(job['id'], int(image.get('index') or 0))
    return 'images/' + name


def _web_document(job: dict, image_mode: str = 'relative') -> str:
    cover, inline = _images_by_role(job)
    genre = (GENRES.get(job.get('genre') or 'insight') or GENRES['insight'])[0]
    title = html.escape(str(job.get('title') or job.get('topic') or '未命名文章'))
    summary = html.escape(str(job.get('summary') or ''))
    pieces = [
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>%s</title>' % title,
        '<style>',
        'body{margin:0;background:#f5f1e8;color:#2c3038;'
        'font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;}',
        '.article{max-width:720px;margin:0 auto;padding:44px 24px 64px;}',
        '.kicker{font-size:12px;letter-spacing:.18em;color:#a3834a;margin:0 0 10px;}',
        'h1{font-size:32px;line-height:1.35;margin:0 0 16px;color:#1f2430;}',
        '.summary{padding:16px 18px;background:#fffaf0;border-left:4px solid #c8a96a;'
        'font-size:15px;line-height:1.8;color:#5c5346;margin:0 0 24px;}',
        '.cover{width:100%;border-radius:8px;display:block;margin:0 0 30px;}',
        'h2{font-size:22px;line-height:1.45;margin:36px 0 14px;color:#1f2430;}',
        'p{font-size:16.5px;line-height:1.95;margin:0 0 16px;}',
        '.inline{width:100%;border-radius:8px;display:block;margin:8px 0 22px;}',
        '.caption{font-size:12px;color:#8a8172;text-align:center;margin:-12px 0 22px;}',
        '.foot{margin-top:42px;padding-top:18px;border-top:1px solid #ddd3c1;'
        'font-size:12px;color:#8a8172;}',
        '</style></head><body><article class="article">',
        '<p class="kicker">AI 文章写作 · %s</p>' % html.escape(genre),
        '<h1>%s</h1>' % title,
    ]
    if summary:
        pieces.append('<p class="summary">%s</p>' % summary)
    if cover:
        pieces.append('<img class="cover" src="%s" alt="%s">'
                      % (html.escape(_image_src(job, cover, image_mode)), title))
    for index, section in enumerate(job.get('sections') or []):
        if not isinstance(section, dict):
            continue
        heading = html.escape(str(section.get('heading') or ''))
        if heading:
            pieces.append('<h2>%s</h2>' % heading)
        image = inline.get(index)
        if image:
            pieces.append('<img class="inline" src="%s" alt="%s">'
                          % (html.escape(_image_src(job, image, image_mode)), heading))
        for paragraph in section.get('paragraphs') or []:
            pieces.append('<p>%s</p>' % html.escape(str(paragraph or '')))
    pieces.append('<p class="foot">本文由 AI 辅助生成，配图由 AI 生成，请发布前核对事实与版权。</p>')
    pieces.append('</article></body></html>')
    return ''.join(pieces)


def _wechat_document(job: dict, image_mode: str = 'relative', fragment: bool = False) -> str:
    cover, inline = _images_by_role(job)
    genre = (GENRES.get(job.get('genre') or 'insight') or GENRES['insight'])[0]
    title = html.escape(str(job.get('title') or job.get('topic') or '未命名文章'))
    summary = html.escape(str(job.get('summary') or ''))
    root = 'max-width:680px;margin:0 auto;padding:8px 4px;font-family:-apple-system,' \
           'BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;' \
           'color:#2c3038;font-size:16px;line-height:1.85;'
    pieces = [
        '<section style="%s">' % root,
        '<p style="font-size:12px;letter-spacing:2px;color:#a3834a;margin:0 0 10px;">'
        'AI 文章写作 · %s</p>' % html.escape(genre),
        '<p style="font-size:24px;line-height:1.45;margin:0 0 18px;color:#1f2430;'
        'font-weight:700;">%s</p>' % title,
    ]
    if summary:
        pieces.append(
            '<section style="padding:14px 16px;margin:0 0 24px;background:#fffaf0;'
            'border-left:3px solid #c8a96a;color:#5c5346;font-size:15px;line-height:1.8;">'
            '<span style="display:block;">%s</span></section>' % summary)
    if cover:
        if image_mode == 'placeholder':
            pieces.append(_wechat_placeholder(cover.get('name') or '', '封面'))
        else:
            pieces.append('<img src="%s" style="width:100%%;display:block;'
                          'border-radius:6px;margin:0 0 28px;" alt="%s">'
                          % (html.escape(_image_src(job, cover, image_mode)), title))
    for index, section in enumerate(job.get('sections') or []):
        if not isinstance(section, dict):
            continue
        heading = html.escape(str(section.get('heading') or ''))
        if heading:
            pieces.append('<p style="font-size:20px;line-height:1.5;margin:34px 0 14px;'
                          'color:#1f2430;font-weight:700;">%s</p>' % heading)
        image = inline.get(index)
        if image:
            if image_mode == 'placeholder':
                pieces.append(_wechat_placeholder(image.get('name') or '', heading))
            else:
                pieces.append('<img src="%s" style="width:100%%;display:block;'
                              'border-radius:6px;margin:8px 0 20px;" alt="%s">'
                              % (html.escape(_image_src(job, image, image_mode)), heading))
        for paragraph in section.get('paragraphs') or []:
            pieces.append('<p style="font-size:16px;line-height:1.9;margin:0 0 16px;'
                          'color:#2c3038;">%s</p>' % html.escape(str(paragraph or '')))
    pieces.append(
        '<p style="margin-top:38px;padding-top:16px;border-top:1px solid #ddd3c1;'
        'font-size:12px;line-height:1.7;color:#8a8172;">'
        '本文由 AI 辅助生成，配图由 AI 生成，请发布前核对事实与版权。</p>')
    pieces.append('</section>')
    body = ''.join(pieces)
    if fragment:
        return body
    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title></head><body style="margin:0;padding:18px;background:#fff;">'
            '%s</body></html>' % (title, body))


def _wechat_placeholder(name: str, label: str) -> str:
    return (
        '<section style="margin:8px 0 20px;padding:22px 14px;border:1px dashed #c8a96a;'
        'background:#fffaf0;text-align:center;color:#8a6a31;font-size:13px;line-height:1.7;">'
        '［图片占位：%s，请上传图片后替换］%s</section>'
        % (html.escape(name or '配图'), ('<br>' + html.escape(label)) if label else ''))


def _markdown(job: dict) -> str:
    """Markdown 也把模型文本里的 < > & 转成实体，避免被渲染器当成 HTML。"""
    def text(value):
        return html.escape(str(value or ''), quote=False)

    cover, inline = _images_by_role(job)
    lines = ['# ' + text(job.get('title') or job.get('topic') or '未命名文章'), '']
    if job.get('summary'):
        lines += ['> ' + text(job.get('summary')), '']
    if cover:
        lines += ['![封面](images/%s)' % (cover.get('name') or ''), '']
    for index, section in enumerate(job.get('sections') or []):
        if not isinstance(section, dict):
            continue
        heading = text(section.get('heading'))
        lines += ['## ' + heading, '']
        image = inline.get(index)
        if image:
            lines += ['![%s](images/%s)' % (heading or '配图',
                                            image.get('name') or ''), '']
        for paragraph in section.get('paragraphs') or []:
            lines += [text(paragraph), '']
    lines += ['---', '', '本文由 AI 辅助生成，配图由 AI 生成，请发布前核对事实与版权。', '']
    return '\n'.join(lines)


def _safe_name(job: dict, ext: str) -> str:
    name = str(job.get('title') or job.get('topic') or 'article').strip()[:60]
    name = re.sub(r'[\\/:*?"<>|\s]+', '-', name).strip('-') or 'article'
    return '%s.%s' % (name, ext)


def _attachment(name: str) -> str:
    return "attachment; filename*=UTF-8''%s" % quote(name)


def _zip_bytes(job: dict) -> bytes:
    buffer = io.BytesIO()
    readme = '\n'.join([
        'AI 文章写作 · 公众号图片包',
        '================================',
        '',
        '文件说明：',
        '  article.html   网页版，图片用 images/ 里的相对路径。',
        '  wechat.html    公众号内联样式版，图片同样用相对路径。',
        '  article.md     Markdown 版。',
        '  images/        封面和内文配图。',
        '',
        '发布到公众号的步骤：',
        '  1. 浏览器打开 wechat.html，全选正文并复制。',
        '  2. 粘贴到公众号编辑器（或先粘贴到记事本再复制，避免带入网页样式）。',
        '  3. 按正文里的占位顺序，在公众号编辑器手动上传 images/ 中的图片并替换。',
        '  4. 封面图单独在公众号后台上传。',
        '  5. 发布前核对事实、数字和配图版权，并标注 AI 辅助生成。',
        '',
        '说明：图片不会自动上传，工具也不会自动发布到公众号。',
        '本文由 AI 辅助生成，配图由 AI 生成。',
    ])
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('article.html', _web_document(job, 'relative'))
        archive.writestr('wechat.html', _wechat_document(job, 'relative'))
        archive.writestr('article.md', _markdown(job))
        archive.writestr('README.txt', readme)
        for image in job.get('images') or []:
            if not isinstance(image, dict):
                continue
            try:
                path = _image_path(job, image)
                if path.is_file():
                    archive.write(str(path), 'images/' + path.name)
            except (OSError, ValueError):
                continue
    return buffer.getvalue()


# ---------------------------------------------------------------
# 请求校验
# ---------------------------------------------------------------
def _clean_options(body) -> dict:
    if not isinstance(body, dict):
        raise ValueError('请求格式不正确')
    topic = _text(body.get('topic'), MAX_TOPIC)
    if not topic:
        raise ValueError('先写一个文章主题')
    material = _text(body.get('material'), MAX_MATERIAL)
    genre = str(body.get('genre') or 'insight').strip().lower()
    if genre not in GENRES:
        raise ValueError('不支持的文章类型')
    tone = str(body.get('tone') or 'warm').strip().lower()
    if tone not in TONES:
        raise ValueError('不支持的语气')
    length = str(body.get('length') or 'medium').strip().lower()
    if length not in LENGTHS:
        raise ValueError('不支持的篇幅')
    return {
        'topic': topic, 'material': material, 'genre': genre,
        'tone': tone, 'length': length,
    }


def _clean_job_body(body) -> dict:
    data = _clean_options(body)
    try:
        image_count = int(body.get('imageCount', DEFAULT_IMAGE_COUNT))
    except (TypeError, ValueError):
        raise ValueError('配图数量必须是 1 到 %d 张（含封面）' % MAX_IMAGE_COUNT)
    if image_count < 1 or image_count > MAX_IMAGE_COUNT:
        raise ValueError('配图数量必须是 1 到 %d 张（含封面）' % MAX_IMAGE_COUNT)
    image_style = str(body.get('imageStyle') or 'illustration').strip().lower()
    if image_style not in IMAGE_STYLES:
        raise ValueError('不支持的配图风格')
    image_model = str(body.get('imageModel') or '').strip() or _default_image_model()
    if not model_config.ark_image_model_allowed(image_model):
        raise ValueError('不支持的图片模型')
    outline = body.get('outline')
    if not isinstance(outline, dict):
        raise ValueError('先生成大纲，再开始写正文')
    outline = _normalize_outline(outline, data['topic'])
    # 选题工具交接过来的原标题，只做来源标记；不参与提示词，也不改正文流程。
    source_idea = _clean_text(body.get('sourceIdea'), 120)
    if source_idea:
        outline['sourceIdea'] = source_idea
    inline_count = min(max(image_count - 1, 0), len(outline['sections']))
    data.update({
        'image_count': image_count,          # 总配图数（含封面）
        'image_style': image_style,
        'image_model': image_model,
        'outline': outline,
        'total_images': 1 + inline_count,
    })
    return data


# ---------------------------------------------------------------
# 大纲限流：防止连续点击把文本模型刷爆
# ---------------------------------------------------------------
_outline_lock = threading.Lock()
_outline_hits = {}


def _outline_allowed(user: str) -> bool:
    now = time.time()
    with _outline_lock:
        hits = _outline_hits.setdefault(user, deque())
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= OUTLINE_LIMIT:
            return False
        hits.append(now)
        return True


_refine_lock = threading.Lock()
_refine_hits = {}


def _refine_allowed(user: str) -> bool:
    now = time.time()
    with _refine_lock:
        hits = _refine_hits.setdefault(user, deque())
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= REFINE_LIMIT:
            return False
        hits.append(now)
        return True


# ---------------------------------------------------------------
# 接口
# ---------------------------------------------------------------
@router.get('/article', response_class=HTMLResponse)
def article_page():
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="background:#f6f4fb;color:#1f2430;font-family:system-ui">'
        '<h2>模板文件缺失</h2>'
        '<p>请确认 templates/article.html 存在。</p>'
        '</body></html>'
    )


@router.get('/article/api/status')
async def article_status(request: Request):
    user = _user(request)
    _ensure_worker()
    used = 0
    storage_error = ''
    try:
        used = await run_in_threadpool(storage.article_job_images_since, user, _today_start())
    except storage.StorageUnavailable as exc:
        storage_error = str(exc)
    info = _ready_info()
    return JSONResponse({
        'ok': True,
        **info,
        'llmModel': model_config.llm_model(),
        'imageModel': model_config.ark_image_model(),
        'defaultImageModel': _default_image_model(),
        'imageModels': model_config.ark_image_models(),
        'genres': [{'key': key, 'label': value[0], 'hint': value[1]}
                   for key, value in GENRES.items()],
        'tones': [{'key': key, 'label': value[0], 'hint': value[1]}
                  for key, value in TONES.items()],
        'lengths': [{'key': key, 'label': value['label'], 'hint': value['sections'] + ' 节'}
                    for key, value in LENGTHS.items()],
        'imageStyles': [{'key': key, 'label': label}
                        for key, label in IMAGE_STYLES.items()],
        'dailyLimit': _daily_limit(),
        'usedToday': used,
        'ttlDays': _ttl_days(),
        'maxImageCount': MAX_IMAGE_COUNT,
        'defaultImageCount': DEFAULT_IMAGE_COUNT,
        'maxTopic': MAX_TOPIC,
        'maxMaterial': MAX_MATERIAL,
        'refineLimit': REFINE_LIMIT,
        'maxSelection': MAX_SELECTION,
        'maxEditParagraph': MAX_EDIT_PARAGRAPH,
        'storageError': storage_error,
    })


@router.post('/article/api/outline')
async def make_outline(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        data = _clean_options(body)
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)
    if not model_config.llm_ready():
        return JSONResponse({'ok': False, 'message': model_config.llm_missing_hint()},
                            status_code=503)
    user = _user(request)
    if not _outline_allowed(user):
        return JSONResponse({
            'ok': False,
            'message': '大纲生成有点频繁，等一分钟再试（每分钟最多 %d 次）' % OUTLINE_LIMIT,
        }, status_code=429)
    try:
        outline = await run_in_threadpool(
            _llm_outline, data['topic'], data['material'], data['genre'],
            data['tone'], data['length'])
    except ArticleError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=502)
    return JSONResponse({
        'ok': True,
        'outline': outline,
        'estimated': {
            'sections': len(outline['sections']),
            'images': min(DEFAULT_IMAGE_COUNT, 1 + len(outline['sections'])),
            'note': '这一步只生成大纲；确认后再开始正文和配图，按实际图片张数计费。',
        },
    })


@router.get('/article/api/jobs')
async def list_jobs(request: Request):
    user = _user(request)
    _ensure_worker()
    try:
        await run_in_threadpool(_cleanup_user, user)
        rows = await run_in_threadpool(storage.article_job_list, user, 20)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': [_public_job(row) for row in rows]})


@router.post('/article/api/jobs')
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
        used = await run_in_threadpool(storage.article_job_images_since, user, _today_start())
        limit = _daily_limit()
        if used + data['total_images'] > limit:
            return JSONResponse({
                'ok': False,
                'message': '今天的配图额度不够了（已用 %d / %d 张），明天再来'
                           '或调大 ARTICLE_DAILY_IMAGES' % (used, limit),
            }, status_code=429)
        job = await run_in_threadpool(
            storage.article_job_add,
            user, data['topic'], data['material'], data['genre'], data['tone'],
            data['length'], data['image_count'], data['image_style'],
            data['image_model'], model_config.llm_model(), data['outline'],
            data['total_images'],
        )
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)

    _ensure_worker()
    return JSONResponse({'ok': True, 'job': _public_job(job, with_content=True)},
                        status_code=202)


@router.get('/article/api/jobs/{job_id}')
async def get_job(job_id: str, request: Request):
    user = _user(request)
    _ensure_worker()
    try:
        row = await run_in_threadpool(storage.article_job_get, user, job_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return JSONResponse({'ok': False, 'message': '任务不存在或已过期'}, status_code=404)
    return JSONResponse({'ok': True, 'job': _public_job(row, with_content=True)})


@router.post('/article/api/jobs/{job_id}/refine')
async def refine_job(job_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        data = _clean_selection(body)
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)
    if not model_config.llm_ready():
        return JSONResponse({'ok': False, 'message': model_config.llm_missing_hint()},
                            status_code=503)
    user = _user(request)
    try:
        row = await run_in_threadpool(storage.article_job_get, user, job_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return JSONResponse({'ok': False, 'message': '文章不存在或已过期'},
                            status_code=404)
    if row.get('status') in ('queued', 'running'):
        return JSONResponse({'ok': False, 'message': '文章还在生成，等写完再润色'},
                            status_code=409)
    try:
        sections, section, paragraph = _edit_target(
            row, data['section_index'], data['paragraph_index'])
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)
    if data['end'] > len(paragraph):
        return JSONResponse({'ok': False, 'message': '选中的文字位置不正确，请重新选择'},
                            status_code=400)
    selected = paragraph[data['start']:data['end']]
    if not selected.strip():
        return JSONResponse({'ok': False, 'message': '请选择一段有内容的文字'},
                            status_code=400)
    if not _refine_allowed(user):
        return JSONResponse({
            'ok': False,
            'message': '润色有点频繁，等一分钟再试（每分钟最多 %d 次）' % REFINE_LIMIT,
        }, status_code=429)
    try:
        refined = await run_in_threadpool(
            _llm_refine, row, section, paragraph, selected, data['action'])
        replacement = _replace_selection(paragraph, data['start'], data['end'], refined)
    except ArticleError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=502)
    action_label = REFINE_ACTIONS[data['action']][0]
    return JSONResponse({
        'ok': True,
        'action': data['action'],
        'actionLabel': action_label,
        'sectionIndex': data['section_index'],
        'paragraphIndex': data['paragraph_index'],
        'start': data['start'],
        'end': data['end'],
        'selection': selected,
        'text': refined,
        'replacement': replacement,
        'remaining': max(0, MAX_EDIT_PARAGRAPH - len(replacement)),
    })


@router.post('/article/api/jobs/{job_id}/paragraph')
async def save_paragraph(job_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        section_index = int(body.get('sectionIndex'))
        paragraph_index = int(body.get('paragraphIndex'))
    except (TypeError, ValueError):
        return JSONResponse({'ok': False, 'message': '段落位置不正确，请重新打开文章'},
                            status_code=400)
    if section_index < 0 or paragraph_index < 0:
        return JSONResponse({'ok': False, 'message': '段落位置不正确，请重新打开文章'},
                            status_code=400)
    text = _clean_text(body.get('text'), MAX_EDIT_PARAGRAPH + 1)
    if not text:
        return JSONResponse({'ok': False, 'message': '段落不能为空'}, status_code=400)
    if len(text) > MAX_EDIT_PARAGRAPH:
        return JSONResponse({
            'ok': False,
            'message': '这一段超过 %d 字，请缩短后再保存' % MAX_EDIT_PARAGRAPH,
        }, status_code=400)

    user = _user(request)

    def _save():
        row = storage.article_job_get(user, job_id)
        if not row:
            return 'missing', None
        if row.get('status') in ('queued', 'running'):
            return 'busy', None
        sections, _section, _paragraph = _edit_target(
            row, section_index, paragraph_index)
        sections[section_index]['paragraphs'][paragraph_index] = text
        body_chars = 0
        for item in sections:
            if not isinstance(item, dict):
                continue
            for paragraph in item.get('paragraphs') or []:
                body_chars += len(str(paragraph or ''))
        storage.article_job_update(job_id, {
            'sections': sections,
            'body_chars': body_chars,
        })
        _sync_history(row, sections, body_chars)
        updated = storage.article_job_get(user, job_id)
        return 'ok', updated

    try:
        result, updated = await run_in_threadpool(_save)
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if result == 'missing':
        return JSONResponse({'ok': False, 'message': '文章不存在或已过期'},
                            status_code=404)
    if result == 'busy':
        return JSONResponse({'ok': False, 'message': '文章还在生成，等写完再修改'},
                            status_code=409)
    return JSONResponse({
        'ok': True,
        'job': _public_job(updated, with_content=True),
        'sectionIndex': section_index,
        'paragraphIndex': paragraph_index,
    })


@router.delete('/article/api/jobs/{job_id}')
async def delete_job(job_id: str, request: Request):
    user = _user(request)

    def _delete():
        row = storage.article_job_get(user, job_id)
        if not row:
            return 'missing'
        if row.get('status') in ('queued', 'running'):
            return 'busy'
        if not storage.article_job_delete_finished(user, job_id):
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
        return JSONResponse({'ok': False, 'message': '文章还在生成，等它结束再删'},
                            status_code=409)
    return JSONResponse({'ok': True})


@router.get('/article/api/jobs/{job_id}/images/{index}')
async def get_job_image(job_id: str, index: int, request: Request):
    user = _user(request)
    try:
        row = await run_in_threadpool(storage.article_job_get, user, job_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return JSONResponse({'ok': False, 'message': '图片不存在或已过期'}, status_code=404)
    images = row.get('images') or []
    if index < 1 or index > len(images) or not isinstance(images[index - 1], dict):
        return JSONResponse({'ok': False, 'message': '图片不存在'}, status_code=404)
    try:
        path = _image_path(row, images[index - 1])
    except ValueError:
        return JSONResponse({'ok': False, 'message': '图片不存在'}, status_code=404)
    if not path.is_file():
        return JSONResponse({'ok': False, 'message': '图片已被清理'}, status_code=404)
    ext = path.suffix.lstrip('.').lower()
    return FileResponse(
        str(path),
        media_type=image_gen._media_type(ext),
        headers={'Cache-Control': 'private, max-age=3600'},
    )


async def _export_row(job_id: str, request: Request):
    user = _user(request)
    try:
        row = await run_in_threadpool(storage.article_job_get, user, job_id)
    except storage.StorageUnavailable as exc:
        return None, JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return None, JSONResponse({'ok': False, 'message': '任务不存在或已过期'},
                                  status_code=404)
    if not row.get('sections'):
        return None, JSONResponse({'ok': False, 'message': '正文还没生成好，请稍等'},
                                  status_code=409)
    return row, None


@router.get('/article/api/jobs/{job_id}/export/html')
async def export_html(job_id: str, request: Request, images: str = 'url',
                      download: int = 0):
    row, error = await _export_row(job_id, request)
    if error:
        return error
    mode = images if images in ('url', 'relative', 'placeholder') else 'url'
    content = _web_document(row, mode)
    headers = {'Cache-Control': 'no-store'}
    if download:
        headers['Content-Disposition'] = _attachment(_safe_name(row, 'html'))
    return Response(content=content.encode('utf-8'),
                    media_type='text/html; charset=utf-8', headers=headers)


@router.get('/article/api/jobs/{job_id}/export/wechat')
async def export_wechat(job_id: str, request: Request, images: str = 'relative',
                        fragment: int = 0):
    row, error = await _export_row(job_id, request)
    if error:
        return error
    mode = images if images in ('url', 'relative', 'placeholder') else 'relative'
    content = _wechat_document(row, mode, fragment=bool(fragment))
    return Response(content=content.encode('utf-8'),
                    media_type='text/html; charset=utf-8',
                    headers={'Cache-Control': 'no-store'})


@router.get('/article/api/jobs/{job_id}/export/md')
async def export_md(job_id: str, request: Request):
    row, error = await _export_row(job_id, request)
    if error:
        return error
    return Response(
        content=_markdown(row).encode('utf-8'),
        media_type='text/markdown; charset=utf-8',
        headers={
            'Content-Disposition': _attachment(_safe_name(row, 'md')),
            'Cache-Control': 'no-store',
        },
    )


@router.get('/article/api/jobs/{job_id}/export/zip')
async def export_zip(job_id: str, request: Request):
    row, error = await _export_row(job_id, request)
    if error:
        return error
    try:
        data = await run_in_threadpool(_zip_bytes, row)
    except (OSError, ValueError) as exc:
        return JSONResponse({'ok': False, 'message': '打包失败：%s' % _short(exc)},
                            status_code=503)
    return Response(
        content=data,
        media_type='application/zip',
        headers={
            'Content-Disposition': _attachment(_safe_name(row, 'zip')),
            'Cache-Control': 'no-store',
        },
    )
