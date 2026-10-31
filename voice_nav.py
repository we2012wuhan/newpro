# -*- coding: utf-8 -*-
# 语音导航：把「打开翻译」这类一句话，匹配成工具箱里的页面。
# ------------------------------------------------------------
# 设计：不做大模型。工具名/别名/关键词都在下面这张表里，规则打分：
#   别名精确 100 / 名称包含 85 / 名称被包含 75 / 关键词 60 / 模糊匹配按比例
# 分数接近就返回候选，低分不自动跳——防止听错一个字就打开错页面。
import difflib
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

# path / 名称 / 别名 / 关键词；external=True 表示会跳出工具箱（新窗口/浏览器）
TOOLS = [
    {'name': 'AI 生成图表', 'path': '/ai-chart',
     'aliases': ['图表', '生成图表', 'ai图表', '画图表', '数据图', '可视化'],
     'keywords': ['excel', '柱状图', '折线图']},
    {'name': '页面设计助手', 'path': '/design-assistant',
     'aliases': ['设计助手', '页面设计', '页面设计助手', '原型设计', '画原型', '线框图'],
     'keywords': ['设计', '原型', '交互', '线框']},
    {'name': '懂点设计', 'path': '/design-sense',
     'aliases': ['懂点设计', '设计学习', '设计导师', '设计关键词', '学习设计'],
     'keywords': ['版式', '色彩', '字体', '间距', '灵感']},
    {'name': '联网搜索', 'path': '/web-search',
     'aliases': ['联网搜索', '网上搜索', '查资料', 'iqs', 'cleversee'],
     'keywords': ['网页', '来源', '研究']},
    {'name': '3D 小作品实验室', 'path': '/3d-lab',
     'aliases': ['3d', '3D实验室', '三维实验室', '3d建模', '参数化建模', '自由绘制'],
     'keywords': ['stl', '3mf', '模型', '收纳盒', '手机支架', '挂钩', '轮廓', '圆孔']},
    {'name': '睡前打烊', 'path': '/sleep',
     'aliases': ['睡前打烊', '助眠', '睡觉', '白噪音', '睡眠'],
     'keywords': ['雨声', '风扇', '海浪', '放松']},
    {'name': '夜巷清场', 'path': '/beat-em-up',
     'aliases': ['夜巷清场', '双截龙', '格斗游戏', '横版格斗', '小游戏'],
     'keywords': ['出拳', '敌人', 'boss', '街机']},
    {'name': '抖音下载器', 'path': '/douyin-download',
     'aliases': ['抖音', '抖音下载', '下载抖音', '无水印视频'],
     'keywords': ['视频下载', '抖音视频']},
    {'name': 'NEXT DRAW IO', 'path': 'https://draw.jiangxinyu1688.com/zh', 'external': True,
     'aliases': ['drawio', 'draw io', '流程图', '架构图', '画图'],
     'keywords': ['图表', '示意图']},
    {'name': '购物比价助手', 'path': '/buy-helper',
     'aliases': ['比价', '购物', '买东西', '商品比价'],
     'keywords': ['淘宝', '京东', '价格']},
    {'name': '账单分析', 'path': '/bill-analysis',
     'aliases': ['账单', '记账', '消费分析', '省钱'],
     'keywords': ['支付宝', '微信账单', '支出']},
    {'name': '学习模型助手', 'path': '/study-assistant',
     'aliases': ['学习助手', '学习模型', '学习方法'],
     'keywords': ['费曼', '学习']},
    {'name': 'DeepTutor', 'path': 'https://tutor.jiangxinyu1688.com/chat', 'external': True,
     'aliases': ['deeptutor', '导师', '家教', '老师'],
     'keywords': ['答疑', '辅导']},
    {'name': '翻译助手', 'path': '/translator',
     'aliases': ['翻译', '中译英', '英译中', '英文', '英语翻译'],
     'keywords': ['英语', '单词']},
    {'name': 'OpenMic', 'path': 'https://openmic.jiangxinyu1688.com/', 'external': True,
     'aliases': ['openmic', '麦克风', '唱歌'],
     'keywords': ['音乐']},
    {'name': 'OCR 图片识别', 'path': '/ocr',
     'aliases': ['ocr', '图片识别', '识别文字', '提取文字', '识字'],
     'keywords': ['扫描', '截图']},
    {'name': '判断力教练', 'path': '/judgement-trainer',
     'aliases': ['判断力', '判断', '教练'],
     'keywords': ['决策', '判断']},
    {'name': '贝叶斯日记', 'path': '/predict-diary',
     'aliases': ['贝叶斯', '日记', '预测日记'],
     'keywords': ['概率', '预测']},
    {'name': 'LangChain 测试', 'path': '/langchain-test',
     'aliases': ['langchain', '链测试', '模型测试'],
     'keywords': ['langchain']},
    {'name': 'SDD 需求拆解器', 'path': '/sdd-decomposer',
     'aliases': ['sdd', '需求拆解', '需求拆解器'],
     'keywords': ['需求', '规格']},
    {'name': '五问分析', 'path': '/five-why',
     'aliases': ['五问', '5why', '五个为什么', '根因', '五问分析法'],
     'keywords': ['为什么', '分析']},
    {'name': '苏格拉底提问', 'path': '/socratic',
     'aliases': ['苏格拉底', '苏格拉底提问', '反问'],
     'keywords': ['提问', '追问']},
    {'name': '第一性原理', 'path': '/first-principles',
     'aliases': ['第一性', '第一性原理', '拆零件', '从零算'],
     'keywords': ['原理', '拆解']},
    {'name': '提问三件套', 'path': '/ask-hub',
     'aliases': ['提问三件套', '三件套', '提问工具'],
     'keywords': ['五问', '苏格拉底', '第一性']},
    {'name': '写作选题', 'path': '/writing-ideas',
     'aliases': ['写作', '选题', '灵感'],
     'keywords': ['文章', '标题']},
    {'name': '内耗拆解', 'path': '/rumination',
     'aliases': ['内耗', '焦虑', '情绪拆解'],
     'keywords': ['情绪', '反刍']},
    {'name': '实时语音对话', 'path': '/voice-chat',
     'aliases': ['实时语音', '语音对话', '语音', '豆包', '打电话'],
     'keywords': ['说话', '对话']},
    {'name': 'MoneyPrinterTurbo', 'path': 'https://turbo.jiangxinyu1688.com/', 'external': True,
     'aliases': ['moneyprinter', 'turbo', '视频生成', '自动视频'],
     'keywords': ['视频']},
    {'name': 'OPC 资讯', 'path': '/opc',
     'aliases': ['opc', '开源', 'github', '趋势', '资讯'],
     'keywords': ['项目', '开源项目']},
    {'name': '读书落地', 'path': '/reading-practice',
     'aliases': ['读书', '阅读', '看书', '读书落地'],
     'keywords': ['书', '拆书']},
    {'name': '库博学习圈', 'path': '/kolb',
     'aliases': ['库博', '学习圈', 'kolb', '库博学习圈'],
     'keywords': ['经验', '反思']},
    {'name': '目标拆解器', 'path': '/goal-split',
     'aliases': ['目标拆解', '目标', '拆目标', '目标拆解器'],
     'keywords': ['计划', '里程碑']},
    {'name': '记录总览', 'path': '/overview',
     'aliases': ['总览', '记录总览', '历史记录', '记录'],
     'keywords': ['汇总']},
    {'name': '数据浏览', 'path': '/db',
     'aliases': ['数据浏览', '数据库', 'db', '数据'],
     'keywords': ['表', 'sqlite']},
    {'name': '脚本库', 'path': '/script-library',
     'aliases': ['脚本库', '脚本'],
     'keywords': ['代码']},
    {'name': '网站收集', 'path': '/site-collect',
     'aliases': ['网站收集', '网站', '收藏网站'],
     'keywords': ['书签', '网址']},
    {'name': '工具箱首页', 'path': '/',
     'aliases': ['首页', '主页', '工具箱', '返回首页', '回首页'],
     'keywords': ['home']},
]

FILLERS = ['请帮我', '帮我', '麻烦', '请', '跳转到', '跳到', '打开', '进入', '我要', '我想',
           '看一下', '看看', '一下', '那个', '这个']
_PUNCT = re.compile(r'[\s，。！？、,.!?;；:："“”‘’（）()【】\[\]《》<>]+')


def _norm(text: str) -> str:
    s = _PUNCT.sub('', str(text or '').strip().lower())
    for f in FILLERS:
        s = s.replace(f, '')
    return s.strip()


def _score(text: str, tool: dict) -> int:
    if not text:
        return 0
    best = 0
    for alias in [tool['name']] + list(tool.get('aliases') or []):
        a = _norm(alias)
        if not a:
            continue
        if text == a:
            return 100
        if a in text:
            best = max(best, 85)
        elif text in a and len(text) >= 2:
            best = max(best, 75)
        else:
            best = max(best, int(difflib.SequenceMatcher(None, text, a).ratio() * 70))
    for kw in tool.get('keywords') or []:
        k = _norm(kw)
        if k and k in text:
            best = max(best, 60)
    return best


def match(text: str) -> dict:
    raw = str(text or '').strip()
    t = _norm(raw)
    if not t:
        return {'ok': False, 'msg': '没听清，再说一次？'}
    scored = []
    for tool in TOOLS:
        s = _score(t, tool)
        if s > 0:
            scored.append({'name': tool['name'], 'path': tool['path'],
                           'score': s, 'external': bool(tool.get('external'))})
    scored.sort(key=lambda x: x['score'], reverse=True)
    best = scored[0] if scored else None
    second = scored[1] if len(scored) > 1 else None
    if not best or best['score'] < 50:
        return {'ok': True, 'text': raw, 'normalized': t, 'action': 'none',
                'candidates': [], 'msg': '没找到对应的工具'}
    # 高分，或明显领先第二名 -> 直接打开；否则让用户选
    if best['score'] >= 80 or (best['score'] >= 60 and second and best['score'] - second['score'] >= 15):
        return {'ok': True, 'text': raw, 'normalized': t, 'action': 'open',
                'name': best['name'], 'path': best['path'], 'external': best['external'],
                'score': best['score'], 'candidates': []}
    cands = [x for x in scored if x['score'] >= 45][:3]
    return {'ok': True, 'text': raw, 'normalized': t, 'action': 'choose',
            'candidates': cands or scored[:1], 'msg': '你是指哪个？'}


@router.post('/api/voice-nav')
async def voice_nav(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    text = payload.get('text') if isinstance(payload, dict) else ''
    return JSONResponse(match(str(text or '')[:200]))
