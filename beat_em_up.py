# -*- coding: utf-8 -*-
"""夜巷清场：原创横向卷轴清版格斗小游戏。"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'beat-em-up.html'


@router.get('/beat-em-up', response_class=HTMLResponse)
def beat_em_up_page():
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="background:#080b12;color:#fff;font-family:system-ui">'
        '<h2>模板文件缺失</h2><p>请确认 templates/beat-em-up.html 存在。</p>'
        '</body></html>'
    )
