# -*- coding: utf-8 -*-
"""参数化 3D 小作品实验室：模板、参数和模型完全在浏览器本地生成。"""
import json
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

# v2 文档限制，与 static/3d/core/document.js 保持一致。
V2_TEMPLATE_KEYS = ('tray', 'stand', 'hook')
MAX_BODIES = 6
MAX_OUTLINES = 20
MAX_CIRCLES = 60
MAX_VERTICES = 400
MAX_COORD = 500.0
MAX_ARC_CENTER = 2000.0
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
DEFAULT_COLOR = '#55b8aa'


class _DesignError(ValueError):
    """用户提交的数据不合法，直接返回给前端。"""


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


def _finite(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _DesignError(f'{label}必须是数字')
    if not math.isfinite(number):
        raise _DesignError(f'{label}必须是有限数字')
    return number


def _clamp_round(value: float, minimum: float, maximum: float) -> float:
    return round(min(maximum, max(minimum, value)), 2)


def _clean_color(value) -> str:
    color = str(value or DEFAULT_COLOR).strip().lower()
    if not re.fullmatch(r'#[0-9a-f]{6}', color):
        return DEFAULT_COLOR
    return color


def _clean_title(payload: dict, fallback: str = '未命名 3D 作品') -> str:
    title = str(payload.get('name') or '').strip()[:80]
    return title or fallback


def _clean_id(value, prefix: str, used: set) -> str:
    text = str(value or '').strip()[:40]
    if not text or text in used:
        index = 1
        while f'{prefix}-{index}' in used:
            index += 1
        text = f'{prefix}-{index}'
    used.add(text)
    return text


def _clean_template_params(model: str, raw) -> dict:
    if not isinstance(raw, dict):
        raise _DesignError('参数格式不正确')
    params = {}
    for key, (minimum, maximum) in MODEL_FIELDS[model].items():
        if key not in raw:
            raise _DesignError('缺少参数：' + key)
        value = _finite(raw[key], '参数 ' + key + ' ')
        if key in ('cols', 'rows'):
            params[key] = int(round(min(maximum, max(minimum, value))))
        else:
            params[key] = round(min(maximum, max(minimum, value)), 2)
    return params


# ---------------------------------------------------------------
# v1：{ model, params, color }。老记录仍然可以保存和被浏览器读取。
# ---------------------------------------------------------------
def _clean_v1_body(payload: dict):
    model = str(payload.get('model') or '').strip()
    if model not in MODEL_FIELDS:
        raise _DesignError('不支持的 3D 模板')
    raw_params = payload.get('params')
    params = _clean_template_params(model, raw_params)

    if model == 'custom':
        if not bool(raw_params.get('closed')):
            raise _DesignError('请先闭合外轮廓再保存')
        raw_outline = raw_params.get('outline')
        if not isinstance(raw_outline, list) or not 3 <= len(raw_outline) <= 120:
            raise _DesignError('闭合轮廓需要 3 到 120 个点')
        outline = []
        for point in raw_outline:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise _DesignError('轮廓点格式不正确')
            x = _clamp_round(_finite(point[0], '轮廓点 X '), -MAX_COORD, MAX_COORD)
            y = _clamp_round(_finite(point[1], '轮廓点 Y '), -MAX_COORD, MAX_COORD)
            outline.append([x, y])

        raw_holes = raw_params.get('holes') or []
        if not isinstance(raw_holes, list) or len(raw_holes) > 30:
            raise _DesignError('圆孔最多 30 个')
        holes = []
        for hole in raw_holes:
            if not isinstance(hole, dict):
                raise _DesignError('圆孔格式不正确')
            x = _clamp_round(_finite(hole.get('x'), '圆孔圆心 X '), -MAX_COORD, MAX_COORD)
            y = _clamp_round(_finite(hole.get('y'), '圆孔圆心 Y '), -MAX_COORD, MAX_COORD)
            radius = _clamp_round(_finite(hole.get('r'), '圆孔半径'), 0.5, 200)
            holes.append({'x': x, 'y': y, 'r': radius})

        params['outline'] = outline
        params['holes'] = holes
        params['closed'] = True

    return {
        'model': model,
        'params': params,
        'color': _clean_color(payload.get('color')),
    }, _clean_title(payload, '未命名 3D 参数')


def _clean_v1_design(payload: dict):
    try:
        return _clean_v1_body(payload)
    except _DesignError as exc:
        return None, str(exc)


# ---------------------------------------------------------------
# v2：{ version: 2, bodies: [template | sketch] }
# ---------------------------------------------------------------
def _clean_segment(raw) -> dict:
    if not isinstance(raw, dict) or raw.get('type') != 'arc':
        return {'type': 'line'}
    cx = _clamp_round(
        _finite(raw.get('cx'), '圆弧圆心 X '), -MAX_ARC_CENTER, MAX_ARC_CENTER)
    cy = _clamp_round(
        _finite(raw.get('cy'), '圆弧圆心 Y '), -MAX_ARC_CENTER, MAX_ARC_CENTER)
    radius = _finite(raw.get('r'), '圆弧半径')
    if radius <= 0:
        raise _DesignError('圆弧半径必须大于 0')
    return {
        'type': 'arc',
        'cx': cx,
        'cy': cy,
        'r': _clamp_round(radius, 0.01, MAX_ARC_CENTER),
        'clockwise': bool(raw.get('clockwise')),
    }


def _clean_outline(raw, index: int, used: set) -> dict:
    if not isinstance(raw, dict):
        raise _DesignError('轮廓格式不正确')
    raw_vertices = raw.get('vertices')
    if not isinstance(raw_vertices, list) or not raw_vertices:
        raise _DesignError('轮廓至少需要 1 个点')
    if len(raw_vertices) > MAX_VERTICES:
        raise _DesignError(f'单个轮廓最多 {MAX_VERTICES} 个点')

    vertices = []
    for point in raw_vertices:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise _DesignError('轮廓点格式不正确')
        x = _clamp_round(_finite(point[0], '轮廓点 X '), -MAX_COORD, MAX_COORD)
        y = _clamp_round(_finite(point[1], '轮廓点 Y '), -MAX_COORD, MAX_COORD)
        vertices.append([x, y])

    closed = bool(raw.get('closed'))
    if closed and len(vertices) < 3:
        closed = False
    edge_count = len(vertices) if closed else len(vertices) - 1

    raw_segments = raw.get('segments')
    if raw_segments is None:
        raw_segments = []
    if not isinstance(raw_segments, list):
        raise _DesignError('线段格式不正确')
    segments = []
    for edge in range(edge_count):
        segments.append(_clean_segment(
            raw_segments[edge] if edge < len(raw_segments) else None))

    return {
        'id': _clean_id(raw.get('id'), f'outline-{index + 1}', used),
        'role': 'hole' if raw.get('role') == 'hole' else 'outer',
        'closed': closed,
        'vertices': vertices,
        'segments': segments,
    }


def _clean_circle(raw, index: int, used: set) -> dict:
    if not isinstance(raw, dict):
        raise _DesignError('圆孔格式不正确')
    return {
        'id': _clean_id(raw.get('id'), f'hole-{index + 1}', used),
        'x': _clamp_round(_finite(raw.get('x'), '圆孔圆心 X '), -MAX_COORD, MAX_COORD),
        'y': _clamp_round(_finite(raw.get('y'), '圆孔圆心 Y '), -MAX_COORD, MAX_COORD),
        'r': _clamp_round(_finite(raw.get('r'), '圆孔半径'), 0.5, 200),
    }


def _clean_sketch(raw) -> dict:
    if not isinstance(raw, dict):
        raise _DesignError('草图格式不正确')
    raw_outlines = raw.get('outlines')
    raw_circles = raw.get('circles')
    if raw_outlines is None:
        raw_outlines = []
    if raw_circles is None:
        raw_circles = []
    if not isinstance(raw_outlines, list) or len(raw_outlines) > MAX_OUTLINES:
        raise _DesignError(f'轮廓最多 {MAX_OUTLINES} 个')
    if not isinstance(raw_circles, list) or len(raw_circles) > MAX_CIRCLES:
        raise _DesignError(f'圆孔最多 {MAX_CIRCLES} 个')

    used = set()
    outlines = [_clean_outline(item, index, used)
                for index, item in enumerate(raw_outlines)]
    circles = [_clean_circle(item, index, used)
               for index, item in enumerate(raw_circles)]

    if not any(
        outline['closed'] and outline['role'] == 'outer'
        and len(outline['vertices']) >= 3
        for outline in outlines
    ):
        raise _DesignError('请先画完并闭合外轮廓再保存')
    return {'outlines': outlines, 'circles': circles}


def _clean_v2_body(raw, index: int, used_body_ids: set) -> dict:
    if not isinstance(raw, dict):
        raise _DesignError('模型格式不正确')
    kind = raw.get('kind')
    if kind not in ('template', 'sketch'):
        raise _DesignError('不支持的模型类型')
    body = {
        'id': _clean_id(raw.get('id'), f'body-{index + 1}', used_body_ids),
        'kind': kind,
        'color': _clean_color(raw.get('color')),
    }

    if kind == 'template':
        template = str(raw.get('template') or '').strip()
        if template not in V2_TEMPLATE_KEYS:
            raise _DesignError('不支持的 3D 模板')
        body['template'] = template
        body['params'] = _clean_template_params(template, raw.get('params'))
        return body

    extrusion = _clamp_round(_finite(raw.get('extrusion'), '拉伸厚度'), 1, 40)
    body['extrusion'] = extrusion
    body['sketch'] = _clean_sketch(raw.get('sketch'))
    return body


def _clean_v2_design(payload: dict):
    try:
        raw_bodies = payload.get('bodies')
        if not isinstance(raw_bodies, list) or not 1 <= len(raw_bodies) <= MAX_BODIES:
            raise _DesignError(f'作品需要 1 到 {MAX_BODIES} 个模型')
        used_body_ids = set()
        bodies = [
            _clean_v2_body(raw, index, used_body_ids)
            for index, raw in enumerate(raw_bodies)
        ]
        return {'version': 2, 'bodies': bodies}, _clean_title(payload)
    except _DesignError as exc:
        return None, str(exc)


def _clean_design(payload: dict):
    if payload.get('version') == 2:
        return _clean_v2_design(payload)
    return _clean_v1_design(payload)


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
    content_length = request.headers.get('content-length')
    if content_length:
        try:
            if int(content_length) > MAX_PAYLOAD_BYTES:
                return JSONResponse(
                    {'ok': False, 'message': '保存内容过大'}, status_code=413)
        except ValueError:
            pass

    try:
        raw = await request.body()
    except Exception:
        raw = b''
    if len(raw) > MAX_PAYLOAD_BYTES:
        return JSONResponse(
            {'ok': False, 'message': '保存内容过大'}, status_code=413)

    try:
        payload = json.loads(raw) if raw else {}
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
