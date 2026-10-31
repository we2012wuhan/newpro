# -*- coding: utf-8 -*-
"""参数化 3D 小作品实验室：模板、参数和模型完全在浏览器本地生成。"""
import math
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import storage

router = APIRouter()

TOOL = '3d-lab'
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / '3d-lab.html'
MAX_DESIGNS = 12

MODEL_FIELDS = {
    'tray': {
        'width': (80, 300), 'depth': (60, 240), 'height': (30, 180),
        'wall': (1.2, 8), 'cols': (1, 4), 'rows': (1, 3),
    },
    'stand': {
        'width': (60, 160), 'depth': (70, 180), 'height': (70, 150),
        'angle': (5, 35), 'lip': (8, 30), 'cable': (0, 60),
        'thickness': (2, 6),
    },
    'hook': {
        'width': (30, 120), 'plateHeight': (50, 180), 'reach': (25, 100),
        'plateThickness': (4, 12), 'hookThickness': (5, 15),
        'tipHeight': (8, 30), 'screwDiameter': (3, 8),
    },
    'custom': {
        'extrusion': (1, 40),
    },
}


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


def _clean_design(payload: dict):
    model = str(payload.get('model') or '').strip()
    if model not in MODEL_FIELDS:
        return None, '不支持的 3D 模板'
    raw_params = payload.get('params')
    if not isinstance(raw_params, dict):
        return None, '参数格式不正确'

    params = {}
    for key, (minimum, maximum) in MODEL_FIELDS[model].items():
        try:
            value = float(raw_params[key])
        except (KeyError, TypeError, ValueError):
            return None, '缺少参数：' + key
        if not math.isfinite(value):
            return None, '参数必须是有限数字：' + key
        value = min(maximum, max(minimum, value))
        params[key] = int(round(value)) if key in ('cols', 'rows') else round(value, 2)

    if model == 'custom':
        if not bool(raw_params.get('closed')):
            return None, '请先闭合外轮廓再保存'
        raw_outline = raw_params.get('outline')
        if not isinstance(raw_outline, list) or not 3 <= len(raw_outline) <= 120:
            return None, '闭合轮廓需要 3 到 120 个点'
        outline = []
        for point in raw_outline:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                return None, '轮廓点格式不正确'
            try:
                x, y = float(point[0]), float(point[1])
            except (TypeError, ValueError):
                return None, '轮廓点必须是数字'
            if not math.isfinite(x) or not math.isfinite(y):
                return None, '轮廓点必须是有限数字'
            outline.append([round(min(500, max(-500, x)), 2),
                            round(min(500, max(-500, y)), 2)])
        raw_holes = raw_params.get('holes') or []
        if not isinstance(raw_holes, list) or len(raw_holes) > 30:
            return None, '圆孔最多 30 个'
        holes = []
        for hole in raw_holes:
            if not isinstance(hole, dict):
                return None, '圆孔格式不正确'
            try:
                x, y, radius = float(hole['x']), float(hole['y']), float(hole['r'])
            except (KeyError, TypeError, ValueError):
                return None, '圆孔参数不完整'
            if not all(math.isfinite(n) for n in (x, y, radius)):
                return None, '圆孔参数必须是有限数字'
            holes.append({
                'x': round(min(500, max(-500, x)), 2),
                'y': round(min(500, max(-500, y)), 2),
                'r': round(min(200, max(0.5, radius)), 2),
            })
        params['outline'] = outline
        params['holes'] = holes
        params['closed'] = True

    color = str(payload.get('color') or '#55b8aa').strip().lower()
    if not re.fullmatch(r'#[0-9a-f]{6}', color):
        color = '#55b8aa'
    title = str(payload.get('name') or '').strip()[:80]
    if not title:
        title = '未命名 3D 参数'
    return {'model': model, 'params': params, 'color': color}, title


@router.get('/3d-lab/api/designs')
def list_three_d_designs(request: Request):
    user = _user(request)
    try:
        rows = storage.list_records(TOOL, user, limit=MAX_DESIGNS)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': rows})


@router.post('/3d-lab/api/designs')
async def save_three_d_design(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    data, title_or_error = _clean_design(payload)
    if data is None:
        return JSONResponse({'ok': False, 'message': title_or_error}, status_code=400)
    user = _user(request)
    try:
        row = await run_in_threadpool(
            storage.add_record, TOOL, user, title_or_error, data
        )
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'item': row})


@router.delete('/3d-lab/api/designs/{record_id}')
async def delete_three_d_design(record_id: int, request: Request):
    user = _user(request)
    try:
        changed = await run_in_threadpool(storage.delete_record, user, record_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not changed:
        return JSONResponse({'ok': False, 'message': '记录不存在'}, status_code=404)
    return JSONResponse({'ok': True})


@router.get('/3d-lab', response_class=HTMLResponse)
def three_d_lab_page():
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="background:#101322;color:#fff;font-family:system-ui">'
        '<h2>模板文件缺失</h2><p>请确认 templates/3d-lab.html 存在。</p>'
        '</body></html>'
    )
