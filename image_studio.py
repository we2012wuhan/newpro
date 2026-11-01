# -*- coding: utf-8 -*-
"""
图片工坊：压缩、裁剪、宫格切图、九张拼图
==========================================
和 3D 实验室一样，真正耗内存的活全在浏览器里做 —— 图片不会被上传，
服务端只负责两件事：

  1. 把页面 HTML 发出去；
  2. 存「怎么处理」的参数（预设），不存任何像素。

预设复用 storage.history 表（tool='image-studio'），
所以换设备登录也能拿回来，但库里永远只有数字和颜色值。

为什么把校验写得这么啰嗦
------------------------
前端传上来的是浏览器里的表单值，不能当可信数据。所有字段都要按
白名单挑、按范围夹，未知字段直接丢掉。这样即使有人手搓请求，
也不会把奇怪的 JSON 塞进库里。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import storage

router = APIRouter()

TOOL = 'image-studio'
MAX_PRESETS = 30
MAX_CONFIG_BYTES = 4096

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'image-studio.html'

KINDS = ('compress', 'crop', 'grid', 'collage')
FORMATS = ('jpeg', 'webp', 'png')
ASPECTS = ('free', '1:1', '4:3', '3:4', '16:9', '9:16')
COLLAGE_SIZES = (1080, 1440, 2048, 3000)
DEFAULT_NAMES = {
    'compress': '图片压缩方案',
    'crop': '图片裁剪方案',
    'grid': '宫格切图方案',
    'collage': '拼图方案',
}

_HEX = re.compile(r'^#[0-9a-fA-F]{6}$')


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


def _num(value, low: float, high: float, default: float) -> float:
    """把任意输入变成范围内的有限数字；坏值退回默认值。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return min(high, max(low, num))


def _int(value, low: int, high: int, default: int) -> int:
    return int(round(_num(value, low, high, default)))


def _enum(value, allowed, default: str) -> str:
    """枚举值：缺省用默认值，给了但不在白名单里就报错（不静默改）。"""
    if value is None or value == '':
        return default
    text = str(value).strip().lower()
    if text not in allowed:
        raise ValueError('不支持的取值：%s' % value)
    return text


def _hex_color(value, default: str) -> str:
    text = str(value or '').strip()
    return text.lower() if _HEX.match(text) else default


def _bool(value, default: bool = False) -> bool:
    # 只认真正的布尔值，避免字符串 "false" 被当成 True
    return value if isinstance(value, bool) else default


def _rotate(value) -> int:
    try:
        deg = int(value)
    except (TypeError, ValueError):
        raise ValueError('旋转角度必须是 0 / 90 / 180 / 270')
    if deg not in (0, 90, 180, 270):
        raise ValueError('旋转角度必须是 0 / 90 / 180 / 270')
    return deg


def _quality(value, default: float = 0.92) -> float:
    return round(_num(value, 0.5, 1.0, default), 2)


def _clean_config(kind: str, raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('方案参数格式不正确')

    if kind == 'compress':
        return {
            'targetKB': _int(raw.get('targetKB'), 5, 10240, 200),
            'maxEdge': _int(raw.get('maxEdge'), 0, 12000, 0),
            'format': _enum(raw.get('format'), FORMATS, 'jpeg'),
            'background': _hex_color(raw.get('background'), '#ffffff'),
        }

    if kind == 'crop':
        return {
            'aspect': _enum(raw.get('aspect'), ASPECTS, 'free'),
            'rotate': _rotate(raw.get('rotate', 0)),
            'flipX': _bool(raw.get('flipX')),
            'flipY': _bool(raw.get('flipY')),
            'format': _enum(raw.get('format'), FORMATS, 'jpeg'),
            'quality': _quality(raw.get('quality')),
            'maxEdge': _int(raw.get('maxEdge'), 0, 12000, 0),
        }

    if kind == 'collage':
        # 尺寸只认四档：存进库的值必须能原样回填到界面的下拉框
        size = _int(raw.get('size'), COLLAGE_SIZES[0], COLLAGE_SIZES[-1], 1080)
        if size not in COLLAGE_SIZES:
            raise ValueError('输出尺寸只支持 1080 / 1440 / 2048 / 3000')
        return {
            'size': size,
            'gap': _int(raw.get('gap'), 0, 200, 8),
            'margin': _int(raw.get('margin'), 0, 200, 0),
            'radius': _int(raw.get('radius'), 0, 200, 0),
            'background': _hex_color(raw.get('background'), '#ffffff'),
            'format': _enum(raw.get('format'), FORMATS, 'jpeg'),
            'quality': _quality(raw.get('quality')),
        }

    if kind == 'grid':
        rows = _int(raw.get('rows'), 1, 12, 3)
        cols = _int(raw.get('cols'), 1, 12, 3)
        if rows * cols > 144:
            raise ValueError('行列乘积不能超过 144')
        return {
            'rows': rows,
            'cols': cols,
            'gap': _int(raw.get('gap'), 0, 200, 4),
            'margin': _int(raw.get('margin'), 0, 200, 0),
            'radius': _int(raw.get('radius'), 0, 200, 0),
            'squareCrop': _bool(raw.get('squareCrop'), True),
            'background': _hex_color(raw.get('background'), '#ffffff'),
            'format': _enum(raw.get('format'), FORMATS, 'jpeg'),
            'quality': _quality(raw.get('quality')),
        }

    raise ValueError('不支持的处理方式')


def _clean_preset(body) -> tuple[dict | None, str]:
    """返回 (可直接落库的 payload, 名字)；失败时返回 (None, 错误说明)。"""
    if not isinstance(body, dict):
        return None, '请求格式不正确'

    kind = str(body.get('kind') or '').strip().lower()
    if kind not in KINDS:
        return None, '请选择处理方式（compress / crop / grid / collage）'

    version = body.get('v')
    if version is not None and version != 1:
        return None, '方案版本不支持'

    raw_config = body.get('config')
    if raw_config is None:
        raw_config = {}
    try:
        config = _clean_config(kind, raw_config)
    except ValueError as exc:
        return None, str(exc)

    blob = json.dumps(config, ensure_ascii=False).encode('utf-8')
    if len(blob) > MAX_CONFIG_BYTES:
        return None, '方案参数过大'

    name = re.sub(r'\s+', ' ', str(body.get('name') or '')).strip()[:60]
    if not name:
        name = DEFAULT_NAMES[kind]

    return {'v': 1, 'kind': kind, 'name': name, 'config': config}, name


def _owned_record(user: str, record_id: int):
    """按 id 取一条，但必须属于当前用户、且是本工具的记录。

    直接 storage.delete_record 不够安全：它只按 user 过滤，
    如果前端把别的工具的记录 id 传进来，会被误删。
    """
    row = storage.get_record(user, record_id)
    if not row or row.get('tool') != TOOL:
        return None
    return row


@router.get('/image-studio/api/presets')
async def list_presets(request: Request):
    user = _user(request)
    try:
        items = await run_in_threadpool(
            storage.list_records, TOOL, user, MAX_PRESETS
        )
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': items, 'max': MAX_PRESETS})


@router.get('/image-studio/api/presets/{record_id}')
async def get_preset(record_id: int, request: Request):
    user = _user(request)
    try:
        row = await run_in_threadpool(_owned_record, user, record_id)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not row:
        return JSONResponse({'ok': False, 'message': '方案不存在'}, status_code=404)
    return JSONResponse({'ok': True, 'item': row})


@router.post('/image-studio/api/presets')
async def save_preset(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    data, title_or_error = _clean_preset(body)
    if data is None:
        return JSONResponse({'ok': False, 'message': title_or_error}, status_code=400)

    user = _user(request)

    def _save():
        existing = storage.list_records(TOOL, user, MAX_PRESETS)
        if len(existing) >= MAX_PRESETS:
            return None, '方案最多存 %d 个，请先删掉不用的' % MAX_PRESETS
        return storage.add_record(TOOL, user, title_or_error, data), ''

    try:
        row, error = await run_in_threadpool(_save)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if row is None:
        return JSONResponse({'ok': False, 'message': error}, status_code=409)
    return JSONResponse({'ok': True, 'item': row})


@router.delete('/image-studio/api/presets/{record_id}')
async def delete_preset(record_id: int, request: Request):
    user = _user(request)

    def _delete():
        row = _owned_record(user, record_id)
        if not row:
            return 0
        return storage.delete_record(user, record_id)

    try:
        changed = await run_in_threadpool(_delete)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=503)
    if not changed:
        return JSONResponse({'ok': False, 'message': '方案不存在或已删除'}, status_code=404)
    return JSONResponse({'ok': True})


@router.get('/image-studio', response_class=HTMLResponse)
def image_studio_page():
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="background:#0a0f1e;color:#e8edf7;font-family:system-ui">'
        '<h2>模板文件缺失</h2>'
        '<p>请确认 templates/image-studio.html 存在。</p>'
        '</body></html>'
    )
