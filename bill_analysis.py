# -*- coding: utf-8 -*-
# 账单分析工具：上传 Excel/CSV 月度账单 -> openpyxl/csv 解析成流水 ->
# 调用 DeepSeek 对每笔支出做消费分类 -> 本地汇总成图表数据 + 省钱建议。
import csv
import datetime as _dt
import io
import os
import re
import time
from pathlib import Path

import requests
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ai_chart import _api_key, _endpoint

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'bill_analysis.html'
_DEFAULT_BASE = 'https://api.deepseek.com'
_DEFAULT_MODEL = 'deepseek-chat'
_MAX_ROWS = 500
_MAX_RANK = 12
_CATEGORIES = [
    '餐饮美食', '交通出行', '购物消费', '居住住房', '水电燃气', '通讯话费',
    '休闲娱乐', '医疗健康', '学习教育', '人情往来', '其他',
]
_METHOD_HINT = '（AI 根据摘要推断，仅供参考）'

# ---------------- 表格列识别 ----------------
_DATE_PRIORITY = ['交易时间', '交易日期', '记账日期', '入账日期', '发生日期', '交易时间戳', '日期']
_EXPENSE_PRIORITY = ['支出金额', '支出(元)', '支出', '借方金额', '借方发生额', '交易金额(支出)', '金额(支出)']
_INCOME_PRIORITY = ['收入金额', '收入(元)', '收入', '贷方金额', '贷方发生额', '存入']
_DIRECTION_PRIORITY = ['收/支', '收支类型', '收/付款类型', '收支', '收付款类型', '资金流向', '出入账']
_AMOUNT_PRIORITY = ['金额(元)', '交易金额', '发生额', '金额', '本次金额', '人民币金额', '支付金额']
_DESC_PRIORITY = ['商品说明', '商品名称', '商品详情', '交易说明', '摘要', '交易对象', '交易对方',
                  '对方户名', '商户名称', '商品', '说明', '备注', '交易描述', '用途', '名称', '详情', '订单标题', '商户']
_METHOD_PRIORITY = ['收/付款方式', '收付款方式', '付款方式', '支付方式', '交易方式', '交易渠道',
                    '支付渠道', '渠道', '收支方式', '卡类别', '交易卡号']


def _clean(text) -> str:
    return re.sub(r'\s+', '', str(text or '').strip().replace('\u3000', ''))


def _score_row(row) -> dict:
    """返回该行各列的角色映射：角色 -> 列索引。每列只分配一个优先级最高的角色。"""
    best = {}
    for idx, raw in enumerate(row):
        text = _clean(raw).lower()
        if not text:
            continue
        for role, plist in [
            ('date', _DATE_PRIORITY), ('expense', _EXPENSE_PRIORITY),
            ('income', _INCOME_PRIORITY), ('direction', _DIRECTION_PRIORITY),
            ('amount', _AMOUNT_PRIORITY), ('desc', _DESC_PRIORITY),
            ('method', _METHOD_PRIORITY),
        ]:
            for rank, word in enumerate(plist):
                if word.lower() in text:
                    cur = best.get(role)
                    if cur is None or rank < cur[1]:
                        best[role] = (idx, rank)
                    break
    # 同一列同时命中多个角色时（如“支出金额”既是支出也是金额），按下方角色优先级只保留一个
    role_order = ['direction', 'expense', 'income', 'amount', 'date', 'desc', 'method']
    taken = set()
    out = {}
    for role in role_order:
        if role not in best:
            continue
        idx = best[role][0]
        if idx in taken:
            continue
        taken.add(idx)
        out[role] = idx
    return out


def _find_header(rows):
    best = None
    for i, row in enumerate(rows[:80]):
        score = _score_row(row)
        has_amount = any(r in score for r in ('expense', 'income', 'direction', 'amount'))
        if 'date' in score and has_amount:
            n = len(score)
            if best is None or n > best[0]:
                best = (n, i, score)
    if best is None:
        raise ValueError('没有找到表头行：请确认表格包含「日期」与「金额 / 支出」列。')
    return best[1], best[2]


def _parse_date(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, _dt.datetime):
        return v.date().isoformat()
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, (int, float)):
        try:
            from openpyxl.utils.datetime import from_excel
            return from_excel(float(v)).date().isoformat()
        except Exception:
            return None
    s = str(v).strip().replace('\u3000', ' ')
    # 统一“年月日”分隔并去掉时间部分，保留首段日期
    s = s.replace('年', '-').replace('月', '-').replace('日', ' ')
    s = re.split(r'\s+|T', s)[0].strip().strip('-')
    if re.fullmatch(r'\d{8}', s):
        return s[:4] + '-' + s[4:6] + '-' + s[6:8]
    m = re.fullmatch(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', s)
    if m:
        return '%s-%02d-%02d' % (m.group(1), int(m.group(2)), int(m.group(3)))
    return None

def _to_float(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    neg = s.startswith('(') and s.endswith(')')
    s = s.replace('(', '').replace(')', '').replace('￥', '').replace('¥', '').replace(',', '').replace('，', '').replace(' ', '').replace('元', '')
    s = s.replace('＋', '+').replace('－', '-').replace('−', '-')
    try:
        f = float(s)
    except ValueError:
        return None
    if neg:
        f = -abs(f)
    return f


def _fmt_date(dstr):
    return dstr if dstr else '未知日期'


# ---------------- 文件解析 ----------------
def _parse_csv(text_rows_iter):
    return [list(r) for r in text_rows_iter if any((c is not None and str(c).strip()) for c in r)]


def parse_bill(filename: str, data: bytes):
    """解析上传账单 -> (正常化支出流水, 解析说明 dict)。"""
    name = (filename or '').lower()
    if name.endswith('.xls'):
        raise ValueError('暂不支持老版本 .xls 文件，请用 Excel / WPS 打开后「另存为 .xlsx」再上传。')
    rows = []
    notes = []
    if name.endswith('.csv'):
        # 兼顾 GBK / UTF-8 编码
        text = None
        for enc in ('utf-8-sig', 'gb18030'):
            try:
                text = data.decode(enc)
                break
            except Exception:
                continue
        if text is None:
            text = data.decode('utf-8', errors='ignore')
        raw_rows = list(csv.reader(io.StringIO(text)))
        rows = _parse_csv(raw_rows)
        if len(rows) < 2:
            raise ValueError('CSV 内容为空或只有表头。')
    elif name.endswith(('.xlsx', '.xlsm', '.xlsb')):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        except Exception as exc:
            raise ValueError('Excel 解析失败（文件可能已损坏或加密）：' + str(exc))
        ws = wb.worksheets[0] if wb.worksheets else None
        if ws is None:
            raise ValueError('Excel 里没有任何工作表。')
        max_row = ws.max_row or 0
        if max_row > 4000:
            max_row = 4000
        for r in ws.iter_rows(min_row=1, max_row=max_row, values_only=True):
            row = list(r)
            if any(c is not None and str(c).strip() for c in row):
                rows.append(row)
        wb.close()
        if len(rows) < 2:
            raise ValueError('Excel 里没有数据行。')
    else:
        raise ValueError('仅支持 .xlsx / .csv 文件（.xls 请先另存为 .xlsx）。')

    hdr_idx, cols = _find_header(rows)
    header_row = rows[hdr_idx]
    data_rows = rows[hdr_idx + 1:]
    di = cols.get('date')
    desc_i = cols.get('desc')
    method_i = cols.get('method')
    expense_i = cols.get('expense')
    income_i = cols.get('income')
    direction_i = cols.get('direction')
    amount_i = cols.get('amount')

    def cell(row, idx):
        return row[idx] if idx is not None and idx < len(row) else None

    # 若同时存在“交易对方/商户”与“商品/摘要”等描述列，合并两列提高分类准确度
    desc_i2 = None
    _detail_words = ('交易对方', '对方户名', '商户名称', '商品', '备注', '说明', '摘要', '用途')
    for idx, raw in enumerate(header_row):
        if idx == desc_i:
            continue
        text = _clean(raw)
        if any(w in text for w in _detail_words):
            desc_i2 = idx
            break

    # 支付方式列缺失提示
    method_missing = method_i is None

    out = []
    neutral = []
    skipped = 0
    for row in data_rows:
        dstr = _parse_date(cell(row, di))
        desc = _clean(cell(row, desc_i)) if desc_i is not None else ''
        if desc_i2 is not None:
            extra = _clean(cell(row, desc_i2))
            if extra and extra != desc:
                desc = (desc + ' / ' + extra).strip(' /') if desc else extra
        method = _clean(cell(row, method_i)) if method_i is not None else ''
        if len(method) > 30:
            method = method[:30]

        amt = None
        is_neutral = False
        direction = _clean(cell(row, direction_i)) if direction_i is not None else ''
        # 1) 专用支出列
        if expense_i is not None:
            amt = _to_float(cell(row, expense_i))
            if amt is not None and amt < 0:
                amt = None  # 负的支出列一般是退款/冲正，忽略
        # 2) 有“收/支”指示列 + 通用金额列（微信/支付宝导出格式）
        elif direction_i is not None and amount_i is not None:
            raw_amt = _to_float(cell(row, amount_i))
            if (not direction) or direction == '/' or '不计' in direction:
                # “不计收支”（转账/理财/退款等）：单独统计，不计入消费
                amt = abs(raw_amt) if raw_amt is not None else None
                is_neutral = True
            elif '收' in direction and '支' not in direction:
                # 收入行：不计入支出分析
                skipped += 1
                continue
            elif '支' in direction:
                amt = abs(raw_amt) if raw_amt is not None else None
            else:
                # 状态列也可能是 “支出”“收入”文字；无法判断则跳过
                skipped += 1
                continue
        # 3) 通用金额列带正负号（常见银行卡流水）
        elif amount_i is not None:
            raw = _to_float(cell(row, amount_i))
            if raw is None:
                skipped += 1
                continue
            if raw < 0:
                amt = -raw
            else:
                # 正数一般代表收入；若无独立收入列也找不到符号规律，则按支出处理并提示
                amt = None
                skipped += 1
                continue
        elif income_i is not None:
            amt = None
            skipped += 1
            continue

        if amt is None or amt <= 0:
            skipped += 1
            continue
        if not dstr:
            skipped += 1
            continue
        rec = {'date': dstr, 'desc': desc or ('未命名记录' if is_neutral else '未命名消费'),
               'amount': round(amt, 2), 'method': method}
        if is_neutral:
            neutral.append(rec)
        else:
            out.append(rec)

    if not out:
        if neutral:
            raise ValueError('只识别到 %d 笔「不计收支」记录，没有可分析的消费支出流水，无法生成消费分析。' % len(neutral))
        raise ValueError('没有识别到任何支出流水：请检查是否只包含收入、或列名不含「日期/金额/支出/收/支」。')

    if neutral:
        notes.append('另有 %d 笔「不计收支」记录（转账/理财/退款等）已单独统计，不计入总消费与每日图表。' % len(neutral))
    if method_missing:
        notes.append('表格里没有「支付方式」列，支付方式将由 AI 按消费摘要推断，可能不够准确。')

    return out, neutral, notes


# ---------------- 大模型：消费分类 ----------------
_SYSTEM_PROMPT = (
    '你是资深的个人财务记账助手。用户上传的是某个月的个人账单流水，每行一笔支出。\n'
    '你需要完成两件事：\n'
    '一、把每一笔支出归入固定分类，分类只能是以下之一（不要自创）：'
    + '、'.join(_CATEGORIES) + '。\n'
    '    归类依据：优先看“摘要/说明”里的商户与商品关键词；生活缴费、餐饮外卖、交通出行等要分得合情合理。\n'
    '二、确定每一笔的支付方式：输入行里如果已经带有支付方式就直接采用；如果显示“未知”，'
    '请根据摘要中的常见标识（如“支付宝”“微信支付/财付通”“银联”“云闪付”“京东支付”等）推断为'
    '「微信支付/支付宝/银行卡/云闪付/京东支付/现金/其他」之一；实在无法判断就写“其他”。\n'
    '输出格式（严格按下面三段，不要输出多余内容）：\n'
    '【分类】\n'
    '每行一笔：序号|分类，序号必须来自输入行首，行数与输入完全一致，不得遗漏、重复或编造。\n'
    '【方式】\n'
    '每行一笔：序号|支付方式，同样要求一一对应。\n'
    '【建议】\n'
    '用 3~6 条中文建议给出针对性省钱意见，每条一行，以“- ”开头，必须结合账单里的真实数据，'
    '不要泛泛而谈，例如点出最大的支出类别、最高单笔、外卖/打车等可优化项、订阅与会员等。'
)

# 上面的 _SYSTEM_PROMPT 已足够详细，这里保持独立变量便于后续维护
SYSTEM_PROMPT = _SYSTEM_PROMPT


def _classify_with_llm(rows, key, base, model):
    """调用 DeepSeek 对流水逐笔分类，返回 (categories, methods, suggestions)。"""
    lines = []
    for i, r in enumerate(rows):
        method = r.get('method') or '未知'
        lines.append('%d|%s|%s|%s|%s' % (i, r['date'], r['desc'], r['amount'], method))
    user_content = '以下是本月的支出流水（序号|日期|摘要|金额|支付方式）：\n' + '\n'.join(lines)
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_content},
        ],
        'temperature': 0.2,
        'max_tokens': 9000,
        'stream': False,
    }
    try:
        resp = requests.post(_endpoint(base), json=body, headers=headers, timeout=(15, 240))
    except requests.exceptions.Timeout:
        raise ValueError('请求大模型超时，请稍后重试或检查网络')
    except requests.exceptions.RequestException as exc:
        raise ValueError('无法连接大模型接口：' + str(exc))
    if resp.status_code >= 400:
        detail = re.sub('\x1b\\[[0-9;]*m', '', resp.text)
        raise ValueError('大模型接口返回错误 ' + str(resp.status_code) + '：' + detail[:300])
    try:
        raw = resp.json()['choices'][0]['message']['content']
    except Exception as exc:
        raise ValueError('大模型返回格式异常：' + str(exc))

    n = len(rows)
    categories = [None] * n
    methods = [None] * n
    suggestions = []

    def parse_map(text, default_map=None):
        mapping = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^(\d+)\s*[|｜,，:\s]\s*(.+)$', line)
            if m:
                mapping[int(m.group(1))] = m.group(2).strip()
        return mapping

    seg_cat = ''
    seg_method = ''
    seg_sugg = ''
    mode = ''
    for line in raw.splitlines():
        if '【分类】' in line or '【方式】' in line or '【建议】' in line:
            if '【分类】' in line:
                mode, seg_cat = 'cat', ''
            elif '【方式】' in line:
                mode, seg_method = 'method', ''
            elif '【建议】' in line:
                mode, seg_sugg = 'sugg', ''
            continue
        if mode == 'cat':
            seg_cat += line + '\n'
        elif mode == 'method':
            seg_method += line + '\n'
        elif mode == 'sugg':
            seg_sugg += line + '\n'

    cat_map = parse_map(seg_cat)
    method_map = parse_map(seg_method)
    for line in seg_sugg.splitlines():
        s = re.sub(r'^[-*•\d、.\s]+', '', line.strip()).strip()
        if s and s not in suggestions:
            suggestions.append(s)
    suggestions = suggestions[:8]

    for i in range(n):
        raw_cat = (cat_map.get(i) or '').strip()
        categories[i] = raw_cat if raw_cat in _CATEGORIES else '其他'
        methods[i] = (method_map.get(i) or rows[i].get('method') or '其他').strip() or '其他'

    known = sum(1 for c in categories if c)
    if known < n:
        # 模型漏归类 -> 其余置为“其他”，避免整体失败
        for i in range(n):
            if not categories[i]:
                categories[i] = '其他'
    if not methods:
        methods = ['其他'] * n
    if not suggestions:
        suggestions = [
            '从汇总看，本月最大支出类别建议优先复盘，看看是否有可削减的空间。',
            '单笔金额较高的消费建议再次确认是否必要，避免冲动消费。',
        ]
    return categories, methods, suggestions


# ---------------- 本地汇总 ----------------
def build_result(rows, categories, methods, suggestions, notes=None, meta_extra=None, neutral_rows=None):
    notes = list(notes or [])
    neutral_rows = list(neutral_rows or [])
    neutral_total = 0.0
    for r in neutral_rows:
        neutral_total += float(r['amount'])
    neutral_top = sorted(neutral_rows, key=lambda r: -float(r['amount']))[:6]
    neutral = {
        'total': round(neutral_total, 2),
        'count': len(neutral_rows),
        'items': [{
            'date': r['date'],
            'desc': r['desc'],
            'amount': round(float(r['amount']), 2),
            'method': r.get('method', ''),
        } for r in neutral_top],
    }
    total = 0.0
    cat_sum = {}
    method_sum = {}
    for i, r in enumerate(rows):
        amt = float(r['amount'])
        total += amt
        cat_sum.setdefault(categories[i], 0.0)
        cat_sum[categories[i]] += amt
        method_sum.setdefault(methods[i], 0.0)
        method_sum[methods[i]] += amt

    def to_ratio(mapping):
        items = sorted(mapping.items(), key=lambda kv: -kv[1])
        out = []
        for name, val in items:
            out.append({'name': name, 'value': round(val, 2), 'pct': round(val / total * 100, 1) if total else 0})
        return out

    category_total = to_ratio(cat_sum)
    method_total = to_ratio(method_sum)

    # 每日合计 + 每日分类细分（柱状图堆叠）
    day_map = {}
    for i, r in enumerate(rows):
        day_map.setdefault(r['date'], {}).setdefault(categories[i], 0.0)
        day_map[r['date']][categories[i]] += float(r['amount'])
    days = sorted(day_map.keys())
    top_cats = [c['name'] for c in category_total[:7]]
    rest_name = '其他'
    if len(category_total) > 7 and rest_name not in top_cats:
        top_cats.append(rest_name)
    series = {}
    for c in top_cats:
        series.setdefault(c, [0.0] * len(days))
    for di, d in enumerate(days):
        for cat, val in day_map[d].items():
            key = cat if cat in top_cats else rest_name
            series[key][di] += val
    daily_series = []
    for c in top_cats:
        daily_series.append({'name': c, 'data': [round(v, 2) for v in series[c]]})
    daily_totals = [round(sum(day_map[d].values()), 2) for d in days]

    # 单笔排名
    indexed = sorted(
        [{'amount': float(r['amount']), 'date': r['date'], 'desc': r['desc'],
          'category': categories[i], 'method': methods[i]} for i, r in enumerate(rows)],
        key=lambda x: -x['amount'])
    rank = indexed[:_MAX_RANK]

    max_one = indexed[0] if indexed else None
    meta = {
        'month': (rows[0]['date'][:7] if rows else ''),
        'total': round(total, 2),
        'count': len(rows),
        'day_count': len(days),
        'avg_day': round(total / len(days), 2) if days else 0,
        'max_single': max_one,
        'top_categories': [c['name'] for c in category_total[:3]],
        'days': days,
    }
    if meta_extra:
        meta.update(meta_extra)

    # 智能补充建议（基于真实数据）
    auto = []
    if category_total:
        top = category_total[0]
        auto.append('本月「%s」共支出 %.2f 元、占 %.1f%%，是最大开销来源，建议重点复盘、设置月度预算上限。'
                    % (top['name'], top['value'], top['pct']))
    if max_one and max_one['amount'] >= total * 0.1:
        auto.append('单笔最高消费 %.2f 元（%s，%s）占比超过总支出 10%%，下单前建议比价与冷静期确认。'
                    % (max_one['amount'], max_one['desc'], max_one['date']))
    if '餐饮美食' in cat_sum and '外卖' in ''.join(r['desc'] for r in rows):
        auto.append('检测到较多外卖/餐饮支出，可试试「整周备餐 + 优惠券集中下单」，通常每月能省下一笔可观的费用。')
    advice = auto + suggestions
    seen = set()
    advice = [a for a in advice if not (a in seen or seen.add(a))]

    return {
        'ok': True,
        'meta': meta,
        'neutral': neutral,
        'category_total': category_total,
        'method_total': method_total,
        'daily_totals': daily_totals,
        'daily_series': daily_series,
        'rank': rank,
        'advice': advice[:12],
        'notes': notes,
    }


def run_analyze(filename: str, data: bytes, key: str, base: str = '', model: str = ''):
    base = (base or _DEFAULT_BASE).strip() or _DEFAULT_BASE
    model = (model or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    rows, neutral, notes = parse_bill(filename, data)
    if len(rows) > _MAX_ROWS:
        raise ValueError('检测到 %d 笔支出，单次最多分析 %d 笔。请把账单按月拆分后再上传。' % (len(rows), _MAX_ROWS))
    key = _api_key(key)
    if not key:
        raise ValueError('缺少 API Key：请在页面「模型设置」填写，或设置环境变量 DEEPSEEK_API_KEY')
    started = time.time()
    categories, methods, suggestions = _classify_with_llm(rows, key, base, model)
    result = build_result(rows, categories, methods, suggestions, notes=notes, neutral_rows=neutral)
    result['meta']['elapsed'] = round(time.time() - started, 1)
    result['meta']['model'] = model
    result['method_estimated'] = any('推断' in n for n in notes)
    return result


# ---------------- 路由 ----------------
@router.get('/bill-analysis', response_class=HTMLResponse)
def bill_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="background:#0f172a;color:#fff;font-family:system-ui">'
                '<h2>模板文件缺失</h2><p>请确认 templates/bill_analysis.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.post('/bill-analysis/api/analyze')
async def bill_analyze(
    request: Request,
    file: UploadFile = File(...),
    key: str = Form(''),
    base: str = Form(''),
    model: str = Form(''),
):
    try:
        data = await file.read()
        if not data:
            return JSONResponse({'ok': False, 'message': '上传的文件是空的'})
        filename = file.filename or 'bill.xlsx'
        return await run_in_threadpool(run_analyze, filename, data, key, base, model)
    except ValueError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)})
    except Exception as exc:
        return JSONResponse({'ok': False, 'message': '处理失败：' + str(exc)})


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='账单分析', description='月度账单消费分类与分析', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8000)