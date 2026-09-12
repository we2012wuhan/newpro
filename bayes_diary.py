# -*- coding: utf-8 -*-
# 贝叶斯日记：给当天就能验证的小事下注 -> 随手记证据实时更新后验 -> 隔天结算 -> 画出校准曲线。
# 概率更新与数据存储全部在浏览器完成（localStorage），后端只负责返回页面，无数据库、无密钥。
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'bayes_diary.html'


@router.get('/bayes-diary', response_class=HTMLResponse)
def bayes_diary_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/bayes_diary.html 存在。</p></body></html>')
    return HTMLResponse(html)


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='贝叶斯日记', description='预测下注 · 证据更新 · 校准曲线', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8002)