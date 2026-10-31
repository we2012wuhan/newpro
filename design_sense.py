# -*- coding: utf-8 -*-
"""懂点设计：为非设计背景学习者生成学习路线和可检索关键词。"""
import json
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from model_config import MISSING_KEY_HINT, llm_endpoint, llm_key, llm_model

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'design-sense.html'
_MAX_CONCERN = 1200

SYSTEM_PROMPT = r"""# Role: 设计学习导师与灵感关键词生成专家

## Profile
- language: 中文
- description: 一位兼具设计教学经验与关键词提炼能力的专业导师，擅长帮助非设计背景的开发者系统建立视觉设计认知，并为其生成可操作、可检索的设计灵感关键词。
- background: 拥有多年视觉设计教学与设计工具开发经验，熟悉非设计专业学习者常见的认知盲区与成长路径，曾帮助多位开发者提升页面视觉表现力。
- personality: 耐心、务实、结构化思维强，善于把抽象的设计概念拆解为可执行的学习步骤与检索词。
- expertise: 视觉设计基础、版式与色彩理论、设计灵感采集方法、关键词体系构建、非设计师的设计进阶路径。
- target_audience: 希望开发“设计灵感学习小工具”并提升自身页面美观度的开发者或非设计背景学习者。

## Skills

1. 设计学习路径规划
   - 能力诊断: 识别学习者在视觉设计上的薄弱环节与起点水平
   - 阶段拆解: 将设计学习拆分为可执行的阶段目标与练习任务
   - 资源匹配: 推荐适合非设计背景的学习材料与练习方式
   - 反馈机制: 设计可自我检验的学习反馈与复盘方法

2. 设计关键词生成
   - 风格关键词: 生成覆盖主流与细分视觉风格的关键词
   - 技法关键词: 提炼版式、色彩、字体、间距等具体技法关键词
   - 场景关键词: 按页面类型与使用场景生成检索关键词
   - 关键词分层: 将关键词按主题、难度、用途进行结构化归类

3. 工具需求转化
   - 需求梳理: 将“想懂点设计”的模糊诉求转化为工具功能方向
   - 功能建议: 提出灵感采集、关键词管理、案例对照等工具模块建议
   - 优先级排序: 按学习价值与实现成本给出功能优先级建议

## Rules

1. 基本原则：
   - 实用性优先: 所有建议必须可落地执行，避免空泛口号
   - 结构化输出: 学习方向与关键词均需分类分层呈现
   - 面向非设计师: 术语需简要解释，避免默认读者具备设计基础
   - 聚焦视觉美观: 建议围绕页面美观度与视觉设计能力提升展开

2. 行为准则：
   - 先诊断后建议: 先说明非设计背景学习者的典型短板，再给出对应方向
   - 方向与关键词对应: 每个学习方向需配套可检索的关键词
   - 关键词可检索: 生成的关键词应适合在灵感平台与搜索引擎中使用
   - 兼顾工具开发: 建议需考虑如何嵌入到用户的小工具中

3. 限制条件：
   - 不替代系统课程: 不承诺速成，需说明学习周期与练习必要性
   - 不堆砌术语: 避免大量未解释的专业术语
   - 不生成无关关键词: 关键词需与视觉设计学习直接相关
   - 不输出代码实现: 仅提供方向、关键词与工具功能建议，不提供具体代码

## Workflows

- 目标: 为希望开发设计灵感小工具的非设计背景学习者，梳理学习努力方向并生成结构化的设计关键词
- 步骤 1: 分析非设计背景学习者在页面视觉设计上的典型短板与学习起点
- 步骤 2: 按版式、色彩、字体、间距、风格、案例采集等维度给出学习努力方向
- 步骤 3: 为每个方向生成可检索的关键词，并按主题与用途分层归类
- 步骤 4: 结合小工具定位，给出关键词管理与灵感采集的功能建议与优先级
- 预期结果: 用户获得一份清晰的学习方向清单、一套结构化关键词，以及小工具功能落地建议

## OutputFormat

1. 输出格式类型：
   - format: markdown
   - structure: 分为“学习努力方向”“关键词清单”“小工具功能建议”三大部分
   - style: 条理清晰、简明务实、面向非设计师
   - special_requirements: 关键词需分类呈现，便于直接复制检索

2. 格式规范：
   - indentation: 使用标题与列表分层，不使用多余缩进
   - sections: 每个方向下先写简要说明，再列关键词
   - highlighting: 关键词使用列表项呈现，重要概念可加粗

3. 验证规则：
   - validation: 检查是否覆盖版式、色彩、字体、间距、风格、案例采集等核心维度
   - constraints: 关键词需与视觉设计直接相关且可检索
   - error_handling: 若某维度信息不足，需说明并给出替代建议而非留空

4. 示例说明：
   1. 示例1：
      - 标题: 学习努力方向示例
      - 格式类型: markdown
      - 说明: 展示单个方向的说明与配套关键词写法
      - 示例内容: |
         **版式与布局**
         说明：先掌握对齐、亲密性、重复、对比四项原则，再练习栅格与留白。
         关键词：对齐原则、亲密性、视觉层级、栅格系统、留白节奏

   2. 示例2：
      - 标题: 小工具功能建议示例
      - 格式类型: markdown
      - 说明: 展示功能建议与优先级写法
      - 示例内容: |
         **关键词收藏夹（优先级：高）**
         说明：支持按风格、技法、场景分类收藏关键词，便于日常检索与复习。

## Initialization
作为设计学习导师与灵感关键词生成专家，你必须遵守上述Rules，按照Workflows执行任务，并按照OutputFormat输出。
"""


def _clean(value, limit):
    return str(value or '').replace('\r\n', '\n').replace('\r', '\n').strip()[:limit]


def _context(payload):
    page_type = _clean(payload.get('page_type'), 40) or '通用 Web 页面'
    level = _clean(payload.get('level'), 40) or '尚未系统学习设计'
    focus = payload.get('focus')
    if not isinstance(focus, list):
        focus = []
    focus = [_clean(item, 30) for item in focus if _clean(item, 30)][:8]
    concern = _clean(payload.get('concern'), _MAX_CONCERN)
    lines = [
        '请根据下面的真实学习背景完成一次设计能力诊断，并严格按系统提示词输出。',
        '',
        '目标页面类型：' + page_type,
        '当前设计水平：' + level,
        '最想改善的方向：' + ('、'.join(focus) if focus else '尚未确定，请结合典型短板诊断'),
        '具体困扰或项目背景：' + (concern or '暂时没有更多信息，请给出适合起步的通用方案'),
        '',
        '要求：说人话；关键词必须能直接复制到 Pinterest、Behance、Dribbble、Google 或中文设计平台搜索；'
        '不要输出代码，不要承诺三天速成。',
    ]
    return '\n'.join(lines)


def _sse(data):
    return 'data: ' + json.dumps(data, ensure_ascii=False) + '\n\n'


def _stream_report(user_content):
    key = llm_key()
    if not key:
        yield _sse({'error': MISSING_KEY_HINT})
        return
    body = {
        'model': llm_model(),
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_content},
        ],
        'temperature': 0.55,
        'max_tokens': 4200,
        'stream': True,
    }
    response = None
    received = False
    try:
        response = requests.post(
            llm_endpoint(),
            json=body,
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
            stream=True,
            timeout=(15, 240),
        )
        if response.status_code >= 400:
            detail = _clean(response.text, 180)
            yield _sse({'error': '模型接口返回错误 %d：%s' % (response.status_code, detail)})
            return
        for raw in response.iter_lines(decode_unicode=False):
            if not raw or not raw.startswith(b'data:'):
                continue
            data = raw[5:].strip()
            if not data or data == b'[DONE]':
                if data == b'[DONE]':
                    break
                continue
            try:
                event = json.loads(data.decode('utf-8'))
                piece = event['choices'][0]['delta'].get('content') or ''
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                continue
            if piece:
                received = True
                yield _sse({'t': piece})
        if received:
            yield _sse({'done': True})
        else:
            yield _sse({'error': '模型没有返回内容，请重新生成。'})
    except requests.RequestException as exc:
        yield _sse({'error': '模型连接中断：' + str(exc)[:160]})
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


@router.get('/design-sense', response_class=HTMLResponse)
def design_sense_page():
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="background:#0c1020;color:#fff;font-family:system-ui">'
        '<h2>模板文件缺失</h2><p>请确认 templates/design-sense.html 存在。</p>'
        '</body></html>'
    )


@router.post('/design-sense/api/generate')
async def design_sense_generate(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if not llm_key():
        return JSONResponse({'ok': False, 'message': MISSING_KEY_HINT}, status_code=503)
    return StreamingResponse(
        _stream_report(_context(payload)),
        media_type='text/event-stream; charset=utf-8',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
