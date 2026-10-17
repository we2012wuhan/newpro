# -*- coding: utf-8 -*-
"""把「我的工具箱」的几个能力包成一个 MCP server（stdio 版）。

给 Codex / Claude Desktop / Cursor 这类 MCP 客户端调用，本地进程、不开端口、不用登录。

跑法（由客户端自己拉起，也可以手动跑一下看有没有报错）：
    .\\.venv\\Scripts\\python.exe mcp_toolbox.py

注册到 Codex：
    codex mcp add toolbox -- D:\\pythonproject\\newpro\\.venv\\Scripts\\python.exe D:\\pythonproject\\newpro\\mcp_toolbox.py

注意：stdio 模式下 **stdout 只能走 JSON-RPC**，所以下面 import 工具箱模块时
把 print 全部拦到 stderr，否则协议会被搅乱。
"""
import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)                 # 保证 data/、templates/ 这类相对路径都对
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---- 导入工具箱模块（把它们的 print 引到 stderr）----
_noise = io.StringIO()
with contextlib.redirect_stdout(_noise):
    import opc_news
    import storage
    import translator
if _noise.getvalue().strip():
    sys.stderr.write('[toolbox-mcp] ' + _noise.getvalue())

# mcp>=2 把 FastMCP 改名成 MCPServer，这里两个版本都兼容
try:
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:                                    # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server(
    'toolbox',
    version='0.1.0',
    instructions=('我的工具箱（D:\\pythonproject\\newpro）里的能力。'
                  'opc_trending / translate 是真干活的工具，'
                  'list_toolbox_tools / db_stats 用来看清这个仓库里有什么。'),
)


def _unwrap(resp):
    """工具箱的函数都返回 FastAPI 的 JSONResponse，解析成 dict 再交给模型。"""
    body = getattr(resp, 'body', None)
    if body is None:
        return resp if isinstance(resp, dict) else {'result': str(resp)}
    try:
        return json.loads(body.decode('utf-8'))
    except (AttributeError, ValueError):
        return {'result': str(body)[:800]}


@mcp.tool()
def opc_trending(window: str = 'd7', field: str = '', lang: str = '',
                 sort: str = 'hot', top: int = 8) -> dict:
    """查 GitHub 上「这段时间新建、star 涨得最快」的开源项目（类似 trending）。

    window: d1=今天 / d3=三天内 / d7=本周 / d30=本月 / d90=近三个月
    field : 空=全部领域，或 ai / agent / saas / devtool / auto / selfhost / content
    lang  : 空=全部语言，或 Python / TypeScript / Go / Rust / Java ...
    sort  : hot=按每天涨的 star（默认）/ stars=按总 star
    top   : 返回前几条，1~30
    """
    payload = {'win': window, 'field': field, 'lang': lang, 'sort': sort,
               'per': max(1, min(30, int(top or 8)))}
    data = _unwrap(opc_news._do_trending(payload))
    if not data.get('ok'):
        return {'ok': False, 'msg': data.get('msg') or '拉不到数据'}
    items = []
    for r in data.get('repos') or []:
        items.append({
            'name': r.get('name'), 'stars': r.get('stars'),
            'per_day': r.get('per_day'), 'lang': r.get('lang'),
            'created': r.get('created'), 'url': r.get('url') or r.get('page'),
            'desc': (r.get('desc') or '')[:160],
        })
    return {'ok': True, 'window': data.get('win'), 'widened': data.get('widened'),
            'total': data.get('total'), 'query': data.get('q'), 'items': items}


@mcp.tool()
def translate(text: str) -> dict:
    """把一段中文翻成英文（工具箱 /translator 用的那个免费接口）。"""
    if not text or not text.strip():
        return {'ok': False, 'msg': '给我一段要翻译的中文'}
    return _unwrap(translator._do_translate(text))


@mcp.tool()
def list_toolbox_tools(keyword: str = '') -> dict:
    """列出这个工具箱仓库里有哪些工具和它们的接口路径（静态扫描，不起服务）。

    keyword: 可选，按模块名/路径过滤，比如 'voice'、'opc'
    """
    rows = []
    for path in sorted(ROOT.glob('*.py')):
        if path.name.startswith('_') or path.name in ('main.py',):
            continue
        try:
            src = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        routes = sorted(set(re.findall(r"@router\.(?:get|post|websocket)\(\s*'([^']+)'", src)))
        if not routes:
            continue
        head = ''
        m = re.match(r'\s*#\s*-\*-.*?\n(#.*\n|\n)*(?:"""(.*?)""")?', src, re.S)
        first = re.search(r'^(?:#\s*)?(.{4,80})$', src.splitlines()[1] if len(src.splitlines()) > 1 else '')
        head = (first.group(1).strip() if first else '')
        row = {'module': path.name, 'note': head[:60], 'routes': routes[:6]}
        if keyword and keyword.lower() not in (path.name + ' ' + ' '.join(routes)).lower():
            continue
        rows.append(row)
    return {'ok': True, 'count': len(rows), 'tools': rows[:60]}


@mcp.tool()
def db_stats() -> dict:
    """看一眼工具箱的 SQLite 库：路径、大小、有哪些表、各有多少行（只读）。"""
    info = storage.info() or {}
    tables = []
    for t in info.get('tables') or []:
        if isinstance(t, dict):
            tables.append({'name': t.get('name'), 'rows': t.get('rows')})
    return {'ok': True, 'path': info.get('path'), 'bytes': info.get('bytes'),
            'journal_mode': info.get('journal_mode'), 'tables': tables,
            'writable': info.get('writable'), 'error': info.get('error') or ''}


def main():
    sys.stderr.write('[toolbox-mcp] 启动（stdio），root=%s\n' % ROOT)
    mcp.run('stdio')


if __name__ == '__main__':
    main()
