# -*- coding: utf-8 -*-
# 出题小工具（/quiz）
# ------------------------------------------------------------
# 给一个主题（可选再贴一段资料），按选择的题型出一套题。答案和解析在生成时
# 一起写进 SQLite，发给页面的题目不带答案；作答提交后服务端本地判分，
# 客观题不二次调用模型，判分结果稳定，也省一半调用成本。
#
# 记录复用 history 表（tool = 'quiz'，按账号隔离），payload 结构：
#   topic / title / level / material_len / questions[] / attempts[]
#   questions: type/stem/options/answer/accept/explanation/optionWhy/point
#   attempts : at/answers/correct/score
#
# 错题库 / 收藏存 quiz_wrong、quiz_fav（每题一份快照，原题删了还能复习）：
#   quiz_wrong 在提交判分后自动维护——答错且作答过收录，答对移出；
#   quiz_fav 由页面上的星标切换。
#
# 环境变量沿用 DEEPSEEK_*（见 model_config.py），不新增配置。
import json
import re
import time
import unicodedata
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import storage
from model_config import (llm_body, llm_endpoint, llm_key, llm_missing_hint,
                          llm_model, llm_ready)

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'quiz.html'

TOOL = 'quiz'
VERSION = 1

TOPIC_MAX = 120
MATERIAL_MAX = 6000
COUNT_MIN = 3
COUNT_MAX = 10
STEM_MAX = 400
OPTION_MAX = 160
EXPLAIN_MAX = 800
WHY_MAX = 200
POINT_MAX = 80
BLANK_MAX = 60
ACCEPT_MAX = 6
HISTORY_PAGE = 10
HISTORY_PAGE_MAX = 50
ATTEMPT_MAX = 20

TYPES = (
    ('single', '单选题', '每题只有一个正确项'),
    ('multiple', '多选题', '每题至少两个正确项'),
    ('judge', '判断题', '判断正误'),
    ('blank', '填空题', '答案短且唯一'),
)
TYPE_LABEL = {key: label for key, label, _hint in TYPES}
TYPE_KEYS = tuple(TYPE_LABEL)

LEVELS = (('basic', '基础'), ('advanced', '进阶'))
LEVEL_LABEL = dict(LEVELS)

SYSTEM_PROMPT = '\n'.join([
    '你是一位严谨的出题老师。只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释。',
    '【出题规矩】',
    '1. 每道题都能独立作答，不依赖图片、链接或前一道题。',
    '2. 单选题只有一个正确项，至少 3 个选项；多选题至少 3 个选项、至少两个正确项，',
    '   而且不能所有选项都正确；判断题只判断正误；填空题用 ____ 表示空格，答案为短词或短语。',
    '3. 题干和选项不要出现「以上都对」「以上都不对」这类选项。',
    '4. 解析必须直接说明为什么对、为什么错，不要空话。单选和多选的每个错误选项都要写一句原因。',
    '5. 不要编造具体引文、数据、人名、年份。资料不足时，出该主题公认的基础知识题，',
    '   宁可简单也不要编。',
    '6. 题目要有代表性：优先覆盖该主题最常考、最容易混淆的知识点，避免偏题怪题。',
    '7. 填空题的 answer 是「可接受答案」数组，第一个是标准答案，其余是等价写法',
    '   （例如中英文、缩写、简写）；不同含义的答案不要混进去。',
    '8. 面向中文用户，专有名词可以保留英文。',
])

_JSON_TYPES = ','.join('"%s"' % key for key in TYPE_KEYS)


class QuizError(RuntimeError):
    """带着中文提示的错误，路由里统一转成 JSON。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------
# 基础清洗
# ---------------------------------------------------------
def _clean(value, limit):
    text = str(value if value is not None else '')
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def _norm_text(value):
    """判分用的归一化：全角转半角、去空白、去尾部标点、英文小写。"""
    text = unicodedata.normalize('NFKC', str(value if value is not None else ''))
    text = re.sub(r'\s+', '', text).strip().lower()
    return text.rstrip('。．.，,；;、:：')


def _extract_json(text):
    raw = re.sub(r'```[a-zA-Z]*', '', str(text or ''))
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------
# 模型调用
# ---------------------------------------------------------
def _chat(system, user, max_tokens=3200, temperature=0.5):
    key = llm_key()
    if not key:
        raise QuizError(llm_missing_hint(), 503)
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    try:
        resp = requests.post(llm_endpoint(), json=llm_body(body), timeout=(10, 120),
                             headers={'Authorization': 'Bearer ' + key,
                                      'Content-Type': 'application/json'})
    except requests.Timeout as exc:
        raise QuizError('模型响应超时，请稍后重试。', 504) from exc
    except requests.RequestException as exc:
        raise QuizError('模型请求失败：' + str(exc)[:80], 502) from exc
    if resp.status_code >= 400:
        raise QuizError('模型返回错误 %s，请稍后重试。' % resp.status_code, 502)
    try:
        content = resp.json()['choices'][0]['message']['content']
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise QuizError('模型返回格式异常，请重试。', 502) from exc
    if not isinstance(content, str) or not content.strip():
        raise QuizError('模型这次没有给出内容，请重试。', 502)
    return content


def _type_plan(types, count):
    """把总题数尽量平均分给选中的题型。"""
    plan = []
    index = 0
    while len(plan) < count:
        plan.append(types[index % len(types)])
        index += 1
    return plan


def _build_prompt(topic, material, plan, level):
    counts = []
    for key in TYPE_KEYS:
        n = plan.count(key)
        if n:
            counts.append('%s %d 道' % (TYPE_LABEL[key], n))
    lines = [
        '主题：' + topic,
        '难度：' + LEVEL_LABEL.get(level, '基础'),
        '题型与数量：' + '，'.join(counts),
        '总题数：%d' % len(plan),
    ]
    if material:
        lines += [
            '',
            '参考资料（优先基于它出题；资料没有覆盖到的知识点不要硬编，可以出该主题公认的基础题）：',
            '<<<资料开始>>>',
            material,
            '<<<资料结束>>>',
        ]
    else:
        lines += ['', '参考资料：未提供。请围绕该主题的常考、基础知识点出题，不要编造细节。']
    lines += [
        '',
        '只输出如下 JSON（所有题目都必须有 explanation；判断题的 options 可以省略）：',
        '{"title":"一句话标题","questions":[',
        ' {"type":"single","stem":"题干","options":["选项1","选项2","选项3","选项4"],'
        '"answer":["A"],"explanation":"为什么 A 对、其他错",'
        '"optionWhy":{"B":"B 为什么错","C":"C 为什么错","D":"D 为什么错"},"point":"考点"},',
        ' {"type":"multiple","stem":"题干","options":["选项1","选项2","选项3","选项4"],'
        '"answer":["A","C"],"explanation":"...","optionWhy":{"B":"...","D":"..."},"point":"考点"},',
        ' {"type":"judge","stem":"陈述句","answer":["A"],"explanation":"...","point":"考点"},',
        ' {"type":"blank","stem":"含有 ____ 的题干","answer":["标准答案","等价写法"],'
        '"explanation":"...","point":"考点"}',
        ']}',
        '题型只能是 %s 之一；answer 用选项字母；不要输出 JSON 以外的任何文字。' % _JSON_TYPES,
    ]
    return '\n'.join(lines)


# ---------------------------------------------------------
# 题目校验与归一化
# ---------------------------------------------------------
def _type_of(value):
    text = str(value or '').strip().lower()
    table = {
        'single': 'single', 'single_choice': 'single', '单选': 'single',
        'choice': 'single', 'radio': 'single',
        'multiple': 'multiple', 'multi': 'multiple', '多选': 'multiple',
        'multi_choice': 'multiple', 'checkbox': 'multiple',
        'judge': 'judge', 'true_false': 'judge', 'truefalse': 'judge',
        '判断': 'judge', 'tf': 'judge',
        'blank': 'blank', 'fill': 'blank', 'fill_blank': 'blank',
        '填空': 'blank', 'cloze': 'blank',
    }
    return table.get(text, '')


def _options(raw):
    """支持 ["甲","乙"] 或 [{"key":"A","text":"甲"}] 两种形状。"""
    if not isinstance(raw, list):
        return []
    items = []
    for item in raw[:6]:
        key = ''
        text = ''
        if isinstance(item, dict):
            key = _clean(item.get('key') or item.get('label'), 2).upper()
            text = _clean(item.get('text') or item.get('content'), OPTION_MAX)
        else:
            text = _clean(item, OPTION_MAX)
        if not text:
            continue
        items.append({'key': key, 'text': text})
    letters = 'ABCDEF'
    used = set()
    for item in items:
        key = item['key']
        if not key or key in used:
            key = next((letter for letter in letters if letter not in used), '')
        if not key:
            break
        used.add(key)
        item['key'] = key
    return [item for item in items if item.get('key')]


def _answer_keys(raw, options):
    """把 ["A"] / "A" / ["选项原文"] 统一成选项字母。"""
    if isinstance(raw, (str, int)):
        parts = re.split(r'[,，、;；\s]+', str(raw))
    elif isinstance(raw, list):
        parts = raw
    else:
        parts = []
    by_text = {_norm_text(item['text']): item['key'] for item in options}
    keys = []
    for part in parts:
        if isinstance(part, dict):
            part = part.get('key') or part.get('text')
        text = _clean(part, OPTION_MAX)
        if not text:
            continue
        key = text.upper() if text.upper() in {item['key'] for item in options} else ''
        if not key:
            key = by_text.get(_norm_text(text), '')
        if key and key not in keys:
            keys.append(key)
    return keys


def _accept_list(raw_answer, raw_accept):
    values = []
    for source in (raw_answer, raw_accept):
        if isinstance(source, list):
            values.extend(source)
        elif source is not None:
            values.append(source)
    result = []
    for value in values:
        if isinstance(value, dict):
            value = value.get('text') or value.get('answer') or ''
        text = _clean(value, BLANK_MAX)
        if text and _norm_text(text) not in {_norm_text(item) for item in result}:
            result.append(text)
        if len(result) >= ACCEPT_MAX:
            break
    return result


def _ensure_blank(stem):
    if re.search(r'_{2,}|＿{2,}', stem):
        return re.sub(r'_{2,}|＿{2,}', '____', stem, count=1)
    if stem.endswith(('。', '？', '?', '！', '!')):
        return stem[:-1] + ' ____' + stem[-1]
    return stem + ' ____'


def _why_map(raw, options):
    by_text = {_norm_text(item['text']): item['key'] for item in options}
    valid = {item['key'] for item in options}
    result = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            key = str(key or '').strip().upper()
            if key not in valid:
                key = by_text.get(_norm_text(key), '')
            text = _clean(value, WHY_MAX)
            if key in valid and text:
                result[key] = text
    elif isinstance(raw, list):
        for item, option in zip(raw, options):
            text = _clean(item, WHY_MAX)
            if text:
                result[option['key']] = text
    return result


def _normalize_question(raw, allowed):
    if not isinstance(raw, dict):
        return None
    kind = _type_of(raw.get('type'))
    if kind not in allowed:
        return None
    stem = _clean(raw.get('stem') or raw.get('question'), STEM_MAX)
    if len(stem) < 4:
        return None

    if kind == 'judge':
        options = [{'key': 'A', 'text': '正确'}, {'key': 'B', 'text': '错误'}]
        answer = _answer_keys(raw.get('answer'), options)
        if not answer:
            # 模型常把判断题答案写成 ["对"] / ["true"]，先取出单个值再比对
            raw_answer = raw.get('answer')
            if isinstance(raw_answer, list):
                raw_answer = raw_answer[0] if raw_answer else ''
            truth = _norm_text(raw_answer)
            if truth in ('true', 't', '对', '正确', '是', 'yes', 'y', '1'):
                answer = ['A']
            elif truth in ('false', 'f', '错', '错误', '否', 'no', 'n', '0'):
                answer = ['B']
        if len(answer) != 1:
            return None
        accept = []
    elif kind == 'blank':
        # 填空题不需要选项，必须在「至少 3 个选项」的校验之前分出来
        accept = _accept_list(raw.get('answer'), raw.get('accept'))
        if not accept:
            return None
        options = []
        answer = []
        stem = _ensure_blank(stem)
    else:
        options = _options(raw.get('options'))
        if len(options) < 3:
            return None
        answer = _answer_keys(raw.get('answer'), options)
        accept = []
        if kind == 'single':
            if len(answer) != 1:
                return None
        else:
            if len(answer) < 2 or len(answer) >= len(options):
                return None

    explanation = _clean(raw.get('explanation') or raw.get('analysis'), EXPLAIN_MAX)
    if not explanation:
        if kind == 'blank':
            explanation = '参考答案：%s。' % accept[0]
        else:
            explanation = '正确答案：%s。' % '、'.join(answer)
    question = {
        'type': kind,
        'stem': stem,
        'options': options,
        'answer': answer,
        'accept': accept,
        'explanation': explanation,
        'optionWhy': _why_map(raw.get('optionWhy') or raw.get('why'), options),
        'point': _clean(raw.get('point') or raw.get('knowledge'), POINT_MAX),
    }
    return question


def _normalize_quiz(data, types, count):
    raw_items = data.get('questions')
    if not isinstance(raw_items, list):
        raw_items = []
    questions = []
    seen = set()
    for raw in raw_items:
        question = _normalize_question(raw, set(types))
        if not question:
            continue
        signature = _norm_text(question['stem'])[:60]
        if signature in seen:
            continue
        seen.add(signature)
        questions.append(question)
        if len(questions) >= count:
            break
    need = max(3, count - 2)
    if len(questions) < need:
        raise QuizError('模型这次只生成了 %d 道有效题目，请重试一次。' % len(questions), 502)
    title = _clean(data.get('title'), 48) or ('%s · %d 题' % ('练习', len(questions)))
    return {'title': title, 'questions': questions}


# ---------------------------------------------------------
# 出题 + 保存
# ---------------------------------------------------------
def _validate_request(payload):
    topic = _clean(payload.get('topic'), TOPIC_MAX + 1)
    if len(topic) < 2:
        raise QuizError('先写一个主题，至少 2 个字。')
    if len(topic) > TOPIC_MAX:
        raise QuizError('主题最多 %d 个字。' % TOPIC_MAX)
    material = str(payload.get('material') or '').strip()
    if len(material) > MATERIAL_MAX:
        raise QuizError('参考资料最多 %d 个字，可以先删减一些。' % MATERIAL_MAX)
    material = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', material)

    raw_types = payload.get('types')
    if not isinstance(raw_types, list):
        raw_types = []
    types = []
    for item in raw_types:
        key = _type_of(item)
        if key and key not in types:
            types.append(key)
    if not types:
        raise QuizError('至少选择一种题型。')
    try:
        count = int(payload.get('count') or 5)
    except (TypeError, ValueError):
        count = 5
    count = max(COUNT_MIN, min(COUNT_MAX, count))
    level = payload.get('level') if payload.get('level') in LEVEL_LABEL else 'basic'
    return topic, material, types, count, level


def _generate_quiz(topic, material, types, count, level):
    plan = _type_plan(types, count)
    prompt = _build_prompt(topic, material, plan, level)
    content = _chat(SYSTEM_PROMPT, prompt,
                    max_tokens=min(4500, 500 + 420 * count), temperature=0.5)
    data = _extract_json(content)
    if not data:
        raise QuizError('模型没有返回可读的题目，请重试一次。', 502)
    quiz = _normalize_quiz(data, types, count)
    quiz.update({
        'version': VERSION,
        'topic': topic,
        'level': level,
        'material_len': len(material),
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'attempts': [],
    })
    return quiz


def _public_question(question, index):
    return {
        'index': index,
        'type': question['type'],
        'typeLabel': TYPE_LABEL.get(question['type'], question['type']),
        'stem': question['stem'],
        'options': [{'key': item['key'], 'text': item['text']}
                    for item in question.get('options') or []],
    }


def _type_counts(questions):
    counts = {}
    for question in questions:
        counts[question['type']] = counts.get(question['type'], 0) + 1
    return [{'key': key, 'label': TYPE_LABEL.get(key, key), 'n': counts[key]}
            for key in TYPE_KEYS if counts.get(key)]


def _public_quiz(rid, payload, favorites=None):
    questions = payload.get('questions') if isinstance(payload.get('questions'), list) else []
    return {
        'id': rid,
        'topic': payload.get('topic', ''),
        'title': payload.get('title', ''),
        'level': payload.get('level', 'basic'),
        'levelLabel': LEVEL_LABEL.get(payload.get('level'), '基础'),
        'createdAt': payload.get('created_at', ''),
        'count': len(questions),
        'typeCounts': _type_counts(questions),
        'favorites': list(favorites or []),
        'questions': [_public_question(q, i) for i, q in enumerate(questions)],
    }


def _save_quiz(user, quiz):
    payload = dict(quiz)
    rec = storage.add_record(TOOL, user, quiz['title'] or quiz['topic'], payload)
    return rec


# ---------------------------------------------------------
# 错题库 / 收藏
# ---------------------------------------------------------
def _snapshot(payload, rid, index, question):
    """存进错题库 / 收藏的题目快照；原题删掉也能复习。"""
    return {
        'quizId': rid,
        'index': index,
        'title': payload.get('title') or payload.get('topic', ''),
        'topic': payload.get('topic', ''),
        'type': question['type'],
        'typeLabel': TYPE_LABEL.get(question['type'], question['type']),
        'stem': question['stem'],
        'options': [{'key': item['key'], 'text': item['text']}
                    for item in question.get('options') or []],
        'answer': list(question.get('answer') or []),
        'accept': list(question.get('accept') or []),
        'answerText': _answer_text(question),
        'explanation': question.get('explanation', ''),
        'optionWhy': question.get('optionWhy') or {},
        'point': question.get('point', ''),
    }


def _given_text(question, given):
    if question.get('type') == 'blank':
        return _blank_text(given)
    lookup = {item['key']: item['text'] for item in question.get('options') or []}
    keys = _given_keys(given, question.get('options') or [])
    return '；'.join('%s. %s' % (key, lookup.get(key, '')) for key in keys)


def _entry_public(row, with_given=False):
    data = dict(row.get('data') or {})
    question = {
        'type': data.get('type', ''),
        'typeLabel': data.get('typeLabel', ''),
        'stem': data.get('stem', ''),
        'options': data.get('options') or [],
        'answer': data.get('answer') or [],
        'accept': data.get('accept') or [],
        'answerText': data.get('answerText', ''),
        'explanation': data.get('explanation', ''),
        'optionWhy': data.get('optionWhy') or {},
        'point': data.get('point', ''),
    }
    entry = {
        'id': row['id'],
        'quizId': row['quiz_id'],
        'index': row['q_index'],
        'title': data.get('title', '') or data.get('topic', ''),
        'topic': data.get('topic', ''),
        'at': row.get('updated_at') or row.get('created_at', ''),
    }
    entry.update(question)
    if with_given:
        given = row.get('given')
        entry['given'] = given
        entry['givenText'] = _given_text(question, given)
    return entry


def _question_snapshot(user, quiz_id, index):
    """优先从原题记录取；原题已删则退回错题库里的快照。"""
    row = storage.get_record(user, quiz_id)
    if row and row.get('tool') == TOOL:
        payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
        questions = payload.get('questions') if isinstance(payload.get('questions'), list) else []
        if 0 <= index < len(questions) and isinstance(questions[index], dict):
            return _snapshot(payload, quiz_id, index, questions[index])
    wrong = storage.quiz_wrong_get(user, quiz_id, index)
    if wrong:
        return dict(wrong.get('data') or {})
    return None


# ---------------------------------------------------------
# 判分
# ---------------------------------------------------------
def _given_keys(value, options):
    if isinstance(value, list):
        parts = value
    elif value is None:
        parts = []
    else:
        parts = re.split(r'[,，、;；\s]+', str(value))
    valid = {item['key'] for item in options}
    keys = []
    for part in parts:
        key = str(part or '').strip().upper()
        if key in valid and key not in keys:
            keys.append(key)
    return sorted(keys)


def _blank_text(value):
    """填空题的作答统一成一段文字；兼容前端误传数组的情况。"""
    if isinstance(value, list):
        value = value[0] if value else ''
    return _clean(value, 200)


def _blank_match(value, accept):
    text = _norm_text(_blank_text(value))
    if not text:
        return False
    return text in {_norm_text(item) for item in accept}


def _answer_empty(given):
    if isinstance(given, list):
        return not given
    return not str(given or '').strip()


def _grade_question(question, given):
    kind = question['type']
    options = question.get('options') or []
    if kind == 'blank':
        correct = _blank_match(given, question.get('accept') or [])
        return correct, _blank_text(given)
    keys = _given_keys(given, options)
    correct = keys == sorted(question['answer'])
    return correct, keys


def _answer_text(question):
    if question['type'] == 'blank':
        return ' / '.join(question.get('accept') or [])
    lookup = {item['key']: item['text'] for item in question.get('options') or []}
    return '；'.join('%s. %s' % (key, lookup.get(key, ''))
                     for key in question['answer'])


def _grade_payload(payload, answers):
    questions = payload.get('questions') if isinstance(payload.get('questions'), list) else []
    if not questions:
        raise QuizError('这套题没有题目数据，请重新生成。', 409)
    if not isinstance(answers, dict):
        answers = {}
    items = []
    correct_count = 0
    stored = {}
    for index, question in enumerate(questions):
        raw = answers.get(str(index), answers.get(index))
        correct, shown = _grade_question(question, raw)
        if question.get('type') == 'blank':
            stored[str(index)] = _blank_text(raw)
        else:
            stored[str(index)] = _given_keys(raw, question.get('options') or [])
        if correct:
            correct_count += 1
        items.append({
            'index': index,
            'type': question['type'],
            'typeLabel': TYPE_LABEL.get(question['type'], question['type']),
            'correct': correct,
            'given': shown,
            'answer': question['answer'],
            'answerText': _answer_text(question),
            'explanation': question.get('explanation', ''),
            'optionWhy': question.get('optionWhy') or {},
            'point': question.get('point', ''),
        })
    total = len(questions)
    score = {
        'correct': correct_count,
        'total': total,
        'percent': int(round(correct_count * 100.0 / total)) if total else 0,
    }
    return {'score': score, 'items': items, 'answers': stored}


# ---------------------------------------------------------
# 历史记录
# ---------------------------------------------------------
def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


async def _body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _attempt_summary(attempt, number):
    score = attempt.get('score') if isinstance(attempt, dict) else {}
    return {
        'no': number,
        'at': attempt.get('at', '') if isinstance(attempt, dict) else '',
        'score': score if isinstance(score, dict) else {},
    }


def _summary(row):
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    questions = payload.get('questions') if isinstance(payload.get('questions'), list) else []
    attempts = payload.get('attempts') if isinstance(payload.get('attempts'), list) else []
    last = attempts[-1] if attempts else {}
    return {
        'id': row['id'],
        'topic': payload.get('topic', ''),
        'title': payload.get('title') or row.get('title', ''),
        'levelLabel': LEVEL_LABEL.get(payload.get('level'), '基础'),
        'createdAt': row.get('created_at', ''),
        'count': len(questions),
        'typeCounts': _type_counts(questions),
        'attempts': len(attempts),
        'lastScore': last.get('score') if isinstance(last, dict) else None,
        'lastAt': last.get('at', '') if isinstance(last, dict) else '',
    }


# ---------------------------------------------------------
# 路由
# ---------------------------------------------------------
@router.get('/quiz', response_class=HTMLResponse)
def quiz_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="font-family:system-ui;padding:40px">'
                '<h2>模板文件缺失</h2><p>请确认 templates/quiz.html 存在。</p></body></html>')
    return HTMLResponse(html)


@router.get('/quiz/api/status')
def quiz_status():
    ready = False
    hint = ''
    try:
        ready = bool(llm_ready())
        hint = '' if ready else llm_missing_hint()
    except Exception:
        ready = False
        hint = '模型配置读取失败。'
    return JSONResponse({
        'ok': True,
        'ai': ready,
        'hint': hint,
        'types': [{'key': key, 'label': label, 'hint': tip}
                  for key, label, tip in TYPES],
        'levels': [{'key': key, 'label': label} for key, label in LEVELS],
        'counts': [COUNT_MIN, 5, 10],
        'countMin': COUNT_MIN,
        'countMax': COUNT_MAX,
        'topicMax': TOPIC_MAX,
        'materialMax': MATERIAL_MAX,
    })


_LAST_GENERATE = {}
_COOLDOWN = 4.0


@router.post('/quiz/api/generate')
async def quiz_generate(request: Request):
    user = _user(request)
    now = time.time()
    if now - _LAST_GENERATE.get(user, 0) < _COOLDOWN:
        return JSONResponse({'ok': False, 'msg': '刚出过一套，喘口气再试。'}, status_code=429)
    payload = await _body(request)
    try:
        topic, material, types, count, level = _validate_request(payload)
        if not llm_ready():
            raise QuizError(llm_missing_hint(), 503)
        _LAST_GENERATE[user] = now
        quiz = await run_in_threadpool(_generate_quiz, topic, material, types,
                                       count, level)
        rec = await run_in_threadpool(_save_quiz, user, quiz)
    except QuizError as exc:
        return JSONResponse({'ok': False, 'msg': exc.message}, status_code=exc.status)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)}, status_code=503)
    except Exception:
        return JSONResponse({'ok': False, 'msg': '出题失败，请稍后重试。'}, status_code=500)
    public = _public_quiz(rec['id'], quiz)
    return JSONResponse({'ok': True, 'quiz': public})


@router.get('/quiz/api/quizzes')
def quiz_list(request: Request, page: int = 1, pageSize: int = HISTORY_PAGE):
    user = _user(request)
    size = max(1, min(int(pageSize or HISTORY_PAGE), HISTORY_PAGE_MAX))
    try:
        total = storage.count_records(TOOL, user)
        pages = (total + size - 1) // size if total else 1
        page = max(1, min(int(page or 1), pages))
        rows = storage.list_records_page(TOOL, user, size, (page - 1) * size)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'msg': str(exc)}, status_code=503)
    items = [_summary(row) for row in rows]
    return JSONResponse({
        'ok': True,
        'items': items,
        'page': page,
        'pageSize': size,
        'pages': pages,
        'total': total,
        'hasPrev': page > 1,
        'hasNext': page < pages,
    })


@router.get('/quiz/api/quizzes/{rid}')
def quiz_detail(rid: int, request: Request):
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)}, status_code=503)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'msg': '找不到这套题。'}, status_code=404)
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    attempts = payload.get('attempts') if isinstance(payload.get('attempts'), list) else []
    try:
        favorites = storage.quiz_fav_indices(user, row['id'])
    except storage.StorageUnavailable:
        favorites = []
    data = {
        'ok': True,
        'quiz': _public_quiz(row['id'], payload, favorites),
        'attempts': [_attempt_summary(attempt, i + 1)
                     for i, attempt in enumerate(attempts)],
    }
    if attempts:
        last = attempts[-1]
        answers = last.get('answers') if isinstance(last, dict) else {}
        try:
            data['lastResult'] = _grade_payload(payload, answers)
            data['lastResult']['at'] = last.get('at', '')
        except QuizError:
            data['lastResult'] = None
    return JSONResponse(data)


@router.post('/quiz/api/quizzes/{rid}/submit')
async def quiz_submit(rid: int, request: Request):
    user = _user(request)
    payload = await _body(request)
    answers = payload.get('answers')
    if not isinstance(answers, dict):
        return JSONResponse({'ok': False, 'msg': '没有收到作答内容。'}, status_code=400)
    try:
        row = storage.get_record(user, rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)}, status_code=503)
    if not row or row.get('tool') != TOOL:
        return JSONResponse({'ok': False, 'msg': '找不到这套题。'}, status_code=404)
    data = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    try:
        result = _grade_payload(data, answers)
    except QuizError as exc:
        return JSONResponse({'ok': False, 'msg': exc.message}, status_code=exc.status)
    attempts = data.get('attempts') if isinstance(data.get('attempts'), list) else []
    attempt = {
        'at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'answers': result['answers'],
        'score': result['score'],
    }
    attempts.append(attempt)
    data['attempts'] = attempts[-ATTEMPT_MAX:]
    try:
        changed = storage.update_record(user, rid, payload=data)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)}, status_code=503)
    if not changed:
        return JSONResponse({'ok': False, 'msg': '找不到这套题。'}, status_code=404)
    # 错题库：答错且真的作答过就收进去；答对则移出。辅助功能失败不影响判分。
    questions = data.get('questions') if isinstance(data.get('questions'), list) else []
    for item in result['items']:
        index = item['index']
        if not (0 <= index < len(questions)):
            continue
        try:
            if item['correct']:
                storage.quiz_wrong_drop(user, rid, index)
            elif not _answer_empty(item['given']):
                storage.quiz_wrong_put(
                    user, rid, index,
                    _snapshot(data, rid, index, questions[index]), item['given'])
        except storage.StorageUnavailable:
            pass
    result['at'] = attempt['at']
    result['attemptNo'] = len(attempts)
    return JSONResponse({'ok': True, 'result': result})


@router.delete('/quiz/api/quizzes/{rid}')
def quiz_delete(rid: int, request: Request):
    user = _user(request)
    try:
        row = storage.get_record(user, rid)
        if not row or row.get('tool') != TOOL:
            return JSONResponse({'ok': False, 'msg': '找不到这套题。'}, status_code=404)
        changed = storage.delete_record(user, rid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)}, status_code=503)
    if not changed:
        return JSONResponse({'ok': False, 'msg': '找不到这套题。'}, status_code=404)
    return JSONResponse({'ok': True})


@router.get('/quiz/api/wrong')
def quiz_wrong_list(request: Request):
    user = _user(request)
    try:
        rows = storage.quiz_wrong_list(user)
        fav_keys = storage.quiz_fav_keys(user)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'msg': str(exc)}, status_code=503)
    items = []
    for row in rows:
        entry = _entry_public(row, with_given=True)
        entry['fav'] = (int(row['quiz_id']), int(row['q_index'])) in fav_keys
        items.append(entry)
    return JSONResponse({'ok': True, 'items': items})


@router.delete('/quiz/api/wrong/{wid}')
def quiz_wrong_delete(wid: int, request: Request):
    user = _user(request)
    try:
        changed = storage.quiz_wrong_remove(user, wid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)}, status_code=503)
    if not changed:
        return JSONResponse({'ok': False, 'msg': '找不到这道错题。'}, status_code=404)
    return JSONResponse({'ok': True})


@router.get('/quiz/api/fav')
def quiz_fav_list(request: Request):
    user = _user(request)
    try:
        rows = storage.quiz_fav_list(user)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'items': [], 'msg': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'items': [_entry_public(row) for row in rows]})


@router.post('/quiz/api/fav/toggle')
async def quiz_fav_toggle(request: Request):
    user = _user(request)
    payload = await _body(request)
    try:
        quiz_id = int(payload.get('quizId'))
        index = int(payload.get('index'))
    except (TypeError, ValueError, OverflowError):
        return JSONResponse({'ok': False, 'msg': '题目参数不完整。'}, status_code=400)
    if quiz_id < 1 or index < 0:
        return JSONResponse({'ok': False, 'msg': '题目参数不完整。'}, status_code=400)
    try:
        if storage.quiz_fav_get(user, quiz_id, index):
            storage.quiz_fav_drop(user, quiz_id, index)
            return JSONResponse({'ok': True, 'fav': False})
        snapshot = _question_snapshot(user, quiz_id, index)
        if not snapshot:
            return JSONResponse({'ok': False, 'msg': '找不到这道题。'}, status_code=404)
        row = storage.quiz_fav_put(user, quiz_id, index, snapshot)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)}, status_code=503)
    return JSONResponse({'ok': True, 'fav': True, 'id': row['id']})


@router.delete('/quiz/api/fav/{fid}')
def quiz_fav_delete(fid: int, request: Request):
    user = _user(request)
    try:
        changed = storage.quiz_fav_remove(user, fid)
    except storage.StorageUnavailable as exc:
        return JSONResponse({'ok': False, 'msg': str(exc)}, status_code=503)
    if not changed:
        return JSONResponse({'ok': False, 'msg': '找不到这条收藏。'}, status_code=404)
    return JSONResponse({'ok': True})


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='出题小工具', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8004)
