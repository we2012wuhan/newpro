# -*- coding: utf-8 -*-
# SDD 需求拆解器：把一团模糊的「我想做个小工具」按七段式逼成结构化规格，
# 最后拼成一段可以直接丢给 agent 的提示词。纯前端工具，数据存在浏览器
# localStorage（sdd_ 前缀），后端只负责返回页面，无数据库、无密钥。
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'sdd-decomposer.html'


@router.get('/sdd-decomposer', response_class=HTMLResponse)
def sdd_decomposer_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2>'
                '<p>请确认 templates/sdd-decomposer.html 存在。</p></body></html>')
    return HTMLResponse(html)


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='SDD 需求拆解器', description='七段式 · 从模糊想法到可交付规格', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8004)
