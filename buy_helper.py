# -*- coding: utf-8 -*-
# 购物比价助手：输入想买的商品与需求 -> 大模型按销量/价格给出跨平台对比与建议，
# 每个商品附可点击的平台实时搜索链接（保证能打开），并为“具体购买链接”保留模型直链字段。
import json
import os
import re
import time
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ai_chart import _api_key, _endpoint, _extract_json

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'buy_helper.html'
_DEFAULT_BASE = 'https://api.deepseek.com'
_DEFAULT_MODEL = 'deepseek-chat'
_MAX_PRODUCTS = 10

# 示例返回结构（页面会按此渲染）
_SCHEMA = {
    'keyword': '空气炸锅',
    'summary': '两句话给出结论：哪个平台/哪类商品更值得买、大概预算。',
    'reason': '为什么这么推荐：说明你如何权衡销量与价格。',
    'estimated': True,
    'products': [
        {
            'platform': '淘宝',
            'store': 'XX官方旗舰店',
            'name': '商品完整名称/规格',
            'price': 299,
            'sales_note': '月销 5万+（参考估算）',
            'score': 4.8,
            'pros': '一句话卖点',
            'link': '确定存在的商品直达链接，不确定就留空字符串',
        },
    ],
}

_PROMPT_LINES = [
    '你是资深的电商比价购物助手。用户会告诉你“想买的商品 + 需求/预算”。',
    '请把结果整理成可直接渲染的 JSON，不要输出无关内容。',
    '诚实约束（必须遵守）：',
    '1. 你无法实时访问淘宝/京东，价格与销量只能依据公开常识估算，估算值不得编造成“精确实时数据”；',
    '2. 每个销量/价格都尽量保守并注明“参考”，summary 第一句写明价格与销量是估算、以平台实时为准；',
    '3. products 至少 4 个、最多 8 个，淘宝系（淘宝/天猫）与京东各至少 2 个；',
    '4. platform 只允许：淘宝 / 天猫 / 京东；',
    '5. price 是估算参考价，只保留两位小数；score 是 0-5 的参考评分，保留一位小数；',
    '6. sales_note 用一句话写“月销/评价”参考量，例如“月销 2万+（参考）”；',
    '7. pros 用一句话写卖点；note 若需要可写口径提醒，可留空；',
    '8. link：只有当你对某个具体商品直达链接很有把握时才填写（形如 https://item.taobao.com/item.htm?id=xxx），',
    '   不确定就一律留空字符串，页面会自动为每个商品生成“平台实时搜索”的可点链接，保证用户能去核对；',
    '9. 不要虚构店铺名与销量数字；宁可用“参考”口径的概数；',
    '10. keyword 提炼用户想买的商品关键词（去掉口语），用于平台搜索。',
]
SYSTEM_PROMPT = chr(10).join(_PROMPT_LINES)
SYSTEM_PROMPT += chr(10) + chr(10) + '请只输出一个 JSON 对象，键名严格与下面的示例一致：' + chr(10)
SYSTEM_PROMPT += json.dumps(_SCHEMA, ensure_ascii=False)
SYSTEM_PROMPT += chr(10) + chr(10) + '不要输出 Markdown 代码块标记，不要输出 JSON 之外的任何内容。'


def _normalize(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError('模型返回的 JSON 顶层不是对象')
    keyword = str(data.get('keyword') or '').strip()
    summary = str(data.get('summary') or '').strip()
    reason = str(data.get('reason') or '').strip()
    estimated = bool(data.get('estimated', True))
    products = []
    for item in data.get('products') or []:
        if not isinstance(item, dict):
            continue
        platform = str(item.get('platform') or '').strip()
        name = str(item.get('name') or '').strip()
        if platform not in ('淘宝', '天猫', '京东') or not name:
            continue
        try:
            price = float(item.get('price'))
        except (TypeError, ValueError):
            price = None
        if price is None or price <= 0:
            continue
        products.append({
            'platform': platform,
            'store': str(item.get('store') or '').strip(),
            'name': name,
            'price': round(price, 2),
            'sales_note': str(item.get('sales_note') or '').strip(),
            'score': float(item.get('score') or 0),
            'pros': str(item.get('pros') or '').strip(),
            'link': str(item.get('link') or '').strip(),
        })
        if len(products) >= _MAX_PRODUCTS:
            break
    if not products:
        raise ValueError('没有可用的商品对比数据')
    return {
        'keyword': keyword,
        'summary': summary,
        'reason': reason,
        'estimated': estimated,
        'products': products,
    }


def _do_analyze(payload: dict):
    product = str(payload.get('product') or '').strip()
    needs = str(payload.get('needs') or '').strip()
    if not product:
        return JSONResponse({'ok': False, 'message': '请先输入你想买的商品'})
    user_content = '想买的商品：' + product
    if needs:
        user_content += chr(10) + '我的需求/预算/偏好：' + needs
    model = str(payload.get('model') or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    base = str(payload.get('base') or _DEFAULT_BASE).strip() or _DEFAULT_BASE
    key = _api_key(payload.get('key'))
    if not key:
        return JSONResponse({'ok': False, 'message': '缺少 API Key：请在页面「模型设置」填写，或设置环境变量 DEEPSEEK_API_KEY'})
    started = time.time()
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_content},
        ],
        'temperature': 0.3,
        'max_tokens': 3200,
        'stream': False,
    }
    try:
        resp = requests.post(_endpoint(base), json=body, headers=headers, timeout=(15, 180))
    except requests.exceptions.Timeout:
        return JSONResponse({'ok': False, 'message': '请求大模型超时，请稍后重试或检查网络'})
    except requests.exceptions.RequestException as exc:
        return JSONResponse({'ok': False, 'message': '无法连接大模型接口：' + str(exc)})
    if resp.status_code >= 400:
        detail = re.sub('\x1b\\[[0-9;]*m', '', resp.text)
        return JSONResponse({'ok': False, 'message': '大模型接口返回错误 ' + str(resp.status_code) + '：' + detail[:300]})
    try:
        raw = resp.json()['choices'][0]['message']['content']
    except Exception as exc:
        return JSONResponse({'ok': False, 'message': '大模型返回格式异常：' + str(exc)})
    try:
        data = _extract_json(raw)
        result = _normalize(data)
    except Exception as exc:
        snippet = re.sub('\\s+', ' ', raw)[:160]
        return JSONResponse({'ok': False, 'message': '数据解析失败：' + str(exc) + '。返回内容预览：' + snippet})
    result.update({'ok': True, 'model': model, 'elapsed': round(time.time() - started, 1)})
    return JSONResponse(result)


@router.get('/buy-helper', response_class=HTMLResponse)
def buy_helper_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8"><body style="background:#0f172a;color:#fff;font-family:system-ui"><h2>模板文件缺失</h2><p>请确认 templates/buy_helper.html 存在。</p></body></html>'
    return HTMLResponse(html)


@router.post('/buy-helper/api/analyze')
async def buy_helper_analyze(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return await run_in_threadpool(_do_analyze, payload)


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='购物比价助手', description='淘宝/京东 比价助手', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8000)