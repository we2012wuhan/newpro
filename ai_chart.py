# -*- coding: utf-8 -*-
# AI 数据图表分析页面
# 输入分析主题 -> 调用 OpenAI 兼容协议的大模型(DeepSeek 等) ->
# 返回结构化数据 -> 前端 ECharts 绘制柱状/饼/折线图，并支持一键导出 Excel。
import json
import os
import re
import time
from pathlib import Path

import requests
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _BASE_DIR / 'static'
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'ai_chart.html'
_KEY_FILE = _BASE_DIR / 'ai_chart_key.txt'
_MAX_ROWS = 50
_DEFAULT_BASE = 'https://api.deepseek.com'
_DEFAULT_MODEL = 'deepseek-chat'
_BT = chr(96)  # 反引号，用于清理 Markdown 代码块标记

_SCHEMA = {
    'title': '各区域销售额与利润对比（示例标题）',
    'summary': '一句话总结数据结论',
    'unit': '万元',
    'estimated': False,
    'sources': [
        {
            'label': '来源名称（如：某机构 2024 年度报告 / 用户提供的数据）',
            'url': 'https://example.com 或留空字符串',
            'note': '说明口径与年份；若是常识估算，必须写：估算数据，无权威来源',
        },
    ],
    'columns': [
        {'key': 'region', 'label': '区域', 'type': 'dimension'},
        {'key': 'sales', 'label': '销售额', 'type': 'metric'},
        {'key': 'profit', 'label': '利润', 'type': 'metric'},
    ],
    'rows': [
        {'region': '华东', 'sales': 1280, 'profit': 210},
        {'region': '华南', 'sales': 960, 'profit': 150},
    ],
}

_PROMPT_LINES = [
    '你是资深的数据分析师。用户会提出一个分析主题或给出一些数据，',
    '请把结果整理成可直接绘制图表的 JSON 数据，不要输出无关内容。',
    '要求：',
    '1. columns 中恰好有一个 type 为 dimension 的分类列，其余为 metric 数值列；',
    '2. 若用户粘贴了原始数据，数值必须与用户数据完全一致，不得编造或修改；',
    '3. 若用户只给了主题、没有给数据，必须基于公开常识做合理估算，并把 estimated 设为 true，',
    '   同时在 summary 第一句写明“数据为估算值，仅用于演示分析思路”；',
    '4. dimension 通常是名称/年份/月份/地区/品类等，推荐 5 到 12 个，最多 30 行；',
    '5. 若主题本身存在多个相关数值维度（如金额/数量/利润/同比/门店数/用户数等），',
    '   请提供 2 到 4 个 metric 列，以便多图表联动分析；',
    '6. 数值最多保留两位小数；',
    '7. estimated 表示数据是否估算：基于真实来源填 false，常识估算填 true；',
    '8. sources 是数据来源数组，最多 20 条（建议 5 到 15 条）。每条包含 label（来源名称）、',
    '   url（尽量给出可点击访问的官网 / 报告 / 新闻 / 统计库真实链接，并完整写出 https 地址；',
    '   无法确定可访问链接时宁可留空字符串也不要编造）、note（说明统计口径与年份）；',
    '   用户自己提供数据时，label 写“用户提供的数据”；',
    '   若是常识估算，note 必须写“估算数据，无权威来源”，url 留空；',
    '   只能列出真实存在或明确标注为估算的来源，不得虚构机构与链接；',
    '9. summary 用中文写 2 到 4 句分析结论，突出最有价值的信息，',
    '   并如实说明该数据是真实来源还是估算。',
]
SYSTEM_PROMPT = chr(10).join(_PROMPT_LINES)
SYSTEM_PROMPT += chr(10) + chr(10) + '请只输出一个 JSON 对象，键名严格与下面的示例一致：' + chr(10)
SYSTEM_PROMPT += json.dumps(_SCHEMA, ensure_ascii=False)
SYSTEM_PROMPT += chr(10) + chr(10) + '不要输出 Markdown 代码块标记，不要输出 JSON 之外的任何内容。'


def _api_key(req_key):
    if req_key and str(req_key).strip():
        return str(req_key).strip()
    env_key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if env_key:
        return env_key
    if _KEY_FILE.exists():
        return _KEY_FILE.read_text(encoding='utf-8').strip()
    return ''


def _endpoint(base):
    base = (base or _DEFAULT_BASE).strip().rstrip('/')
    if base.endswith('/chat/completions'):
        return base
    return base + '/chat/completions'


def _extract_json(text):
    text = re.sub(_BT * 3 + '[a-zA-Z]*', '', text or '')
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        raise ValueError('返回内容里没有找到 JSON')
    return json.loads(text[start:end + 1])


def _auto_columns(rows):
    columns = []
    if not rows:
        return columns
    sample = rows[0]
    for key in list(sample.keys()):
        val = sample[key]
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            columns.append({'key': key, 'label': str(key), 'type': 'dimension'})
        else:
            columns.append({'key': key, 'label': str(key), 'type': 'metric'})
    return columns


def _normalize(data):
    if not isinstance(data, dict):
        raise ValueError('模型返回的 JSON 顶层不是对象')
    rows_raw = data.get('rows')
    if not isinstance(rows_raw, list):
        raise ValueError('缺少 rows 数组')
    columns_raw = data.get('columns')
    if not isinstance(columns_raw, list) or not columns_raw:
        columns = _auto_columns(rows_raw)
    else:
        columns = []
        for c in columns_raw:
            if not isinstance(c, dict):
                continue
            key = str(c.get('key') or '').strip()
            if not key:
                continue
            ctype = 'metric' if str(c.get('type') or '').strip() == 'metric' else 'dimension'
            columns.append({'key': key, 'label': str(c.get('label') or key), 'type': ctype})
    dim_cols = [c for c in columns if c['type'] == 'dimension']
    metric_cols = [c for c in columns if c['type'] == 'metric']
    if not dim_cols:
        raise ValueError('缺少 dimension 分类列')
    if not metric_cols:
        raise ValueError('缺少 metric 数值列')
    rows = []
    for item in rows_raw:
        if not isinstance(item, dict):
            continue
        new_row = {}
        ok = True
        for c in columns:
            raw_val = item.get(c['key'])
            if c['type'] == 'metric':
                try:
                    num = float(raw_val)
                except (TypeError, ValueError):
                    num = None
                if num is None:
                    ok = False
                    break
                new_row[c['key']] = round(num, 2)
            else:
                new_row[c['key']] = str(raw_val if raw_val is not None else '').strip()
        if ok and new_row.get(dim_cols[0]['key']):
            rows.append(new_row)
    if not rows:
        raise ValueError('没有可绘制的有效数据行')
    truncated = len(rows) > _MAX_ROWS
    if truncated:
        rows = rows[: _MAX_ROWS]
    title = str(data.get('title') or '数据分析结果').strip()
    summary = str(data.get('summary') or '').strip()
    unit = str(data.get('unit') or '').strip()
    estimated = bool(data.get('estimated'))
    sources = []
    sources_raw = data.get('sources')
    if isinstance(sources_raw, list):
        for src in sources_raw:
            if not isinstance(src, dict):
                continue
            label = str(src.get('label') or '').strip()
            url = str(src.get('url') or '').strip()
            note = str(src.get('note') or '').strip()
            if label or url or note:
                sources.append({
                    'label': label or '未命名来源',
                    'url': url,
                    'note': note,
                })
            if len(sources) >= 20:
                break
    return {
        'title': title,
        'summary': summary,
        'unit': unit,
        'estimated': estimated,
        'sources': sources,
        'columns': columns,
        'rows': rows,
        'truncated': truncated,
    }


def _do_query(payload):
    prompt = str(payload.get('prompt') or '').strip()
    if not prompt:
        return JSONResponse({'ok': False, 'message': '请输入要分析的主题或数据'})
    model = str(payload.get('model') or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    base = str(payload.get('base') or _DEFAULT_BASE).strip() or _DEFAULT_BASE
    key = _api_key(payload.get('key'))
    if not key:
        return JSONResponse({'ok': False, 'message': '缺少 API Key：请在页面「模型设置」里填写，或设置环境变量 DEEPSEEK_API_KEY'})
    started = time.time()
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.3,
        'max_tokens': 3200,
        'stream': False,
    }
    raw = ''
    try:
        resp = requests.post(_endpoint(base), json=body, headers=headers, timeout=(15, 150))
    except requests.exceptions.Timeout:
        return JSONResponse({'ok': False, 'message': '请求大模型超时，请稍后重试或检查网络'})
    except requests.exceptions.RequestException as exc:
        return JSONResponse({'ok': False, 'message': '无法连接大模型接口：' + str(exc)})
    if resp.status_code >= 400:
        detail = re.sub('\x1b\\[[0-9;]*m', '', resp.text)
        return JSONResponse({'ok': False, 'message': '大模型接口返回错误 ' + str(resp.status_code) + '：' + detail[:300]})
    try:
        data = resp.json()
        raw = data['choices'][0]['message']['content']
    except Exception as exc:
        return JSONResponse({'ok': False, 'message': '大模型返回格式异常：' + str(exc)})
    try:
        payload_data = _extract_json(raw)
        result = _normalize(payload_data)
    except Exception as exc:
        snippet = re.sub('\\s+', ' ', raw)[:160]
        return JSONResponse({'ok': False, 'message': '数据解析失败：' + str(exc) + '。返回内容预览：' + snippet})
    result.update({'ok': True, 'model': model, 'elapsed': round(time.time() - started, 1)})
    return JSONResponse(result)


@router.get('/ai-chart', response_class=HTMLResponse)
def ai_chart_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8"><body style="background:#0f172a;color:#fff;font-family:system-ui"><h2>模板文件缺失</h2><p>请确认 templates/ai_chart.html 存在。</p></body></html>'
    return HTMLResponse(html)


@router.post('/ai-chart/api/query')
async def ai_chart_query(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return await run_in_threadpool(_do_query, payload)

app = FastAPI(title='AI 数据图表分析', description='输入主题调用大模型生成图表数据', version='1.0.0')
try:
    from fastapi.staticfiles import StaticFiles
    app.mount('/static', StaticFiles(directory=str(_STATIC_DIR)), name='static')
except Exception:
    pass
app.include_router(router)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8000)