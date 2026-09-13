# -*- coding: utf-8 -*-
# 判断力小工具：在流水账里认出判断点 -> 把模糊感觉拆成预测 / 证据 / 假设 ->
# 记录决策账本并回看校准。纯前端工具，数据存在浏览器 localStorage，
# 后端只负责返回页面，无数据库、无密钥。
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'judgement-trainer.html'


@router.get('/judgement-trainer', response_class=HTMLResponse)
def judgement_trainer_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/judgement-trainer.html 存在。</p></body></html>')
    return HTMLResponse(html)


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='判断力小工具', description='察觉 / 拆解 / 校准', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8003)