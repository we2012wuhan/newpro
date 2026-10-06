# -*- coding: utf-8 -*-
# 5Why 分析 + 苏格拉底提问（合并页，页面在 /ask-hub）
# ------------------------------------------------------------
# 这两个工具本来就是一对：都靠提问干活，只是一个往下追根因，一个陪你把话说清楚。
# 以前想从这边换到那边，得先回工具箱再点另一张卡，来回两次很烦，所以就合成一页。
#
# 为什么用 iframe 装、而不是把两份代码拼进一个 html：
#   · 两个模板各自定义了一整套同名变量和类（--bg / --acc / --ink、.card / .msg /
#     .bubble / .composer / .toast …），DOM id 也全是 chat / hq / toast；
#   · 拼在一起必然是互相污染，改一个地方要同时担心另一个工具 —— 合并的收益远不抵风险；
#   · iframe 里各自是一份独立文档，两边的 CSS 和 JS 一个字都不用动，切 tab 只是显示 / 隐藏，
#     切回来也不重新加载，聊到一半的状态还在。
# 代价：两个页面各跑一份脚本。所以被嵌的那两页加了 ?embed=1 模式（藏自己的顶栏、
# 不再铺一层粒子背景），见 templates/five-why.html 和 templates/socratic.html 里的
# html[data-embed] 那几条规则。
#
# 两个工具自己的接口一个都没动：/five-why/api/*、/socratic/api/* 照旧，
# 直接访问 /five-why 和 /socratic 也照旧能用（老链接、书签不会失效）。
#
# 深链：/ask-hub?tab=why|soc&r=<记录 id>
#   「记录总览」里的「直接打开这一条」就是拼成这个形状进来的，
#   这一页再把 tab 和 r 透传给对应的 iframe。
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'ask-hub.html'


@router.get('/ask-hub', response_class=HTMLResponse)
def ask_hub_page():
    """返回合并页的 HTML；模板文件丢了也不白屏，给一句人话。"""
    if _TEMPLATE_FILE.exists():
        return HTMLResponse(_TEMPLATE_FILE.read_text(encoding='utf-8'))
    return HTMLResponse(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="font-family:system-ui;padding:24px">'
        '<h2>模板文件缺失</h2><p>请确认 templates/ask-hub.html 存在。</p>'
        '<p>两个工具本身还能单独用：<a href="/five-why">/five-why</a> · '
        '<a href="/socratic">/socratic</a></p>'
        '</body></html>')


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='5Why + 苏格拉底提问', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8012)