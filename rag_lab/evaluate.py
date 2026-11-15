# -*- coding: utf-8 -*-
"""golden set 评估：Recall@k、MRR、Context Precision 和失败归因。"""
from __future__ import annotations

import json
from pathlib import Path

from .corpus import Chunk


def load_golden(path: Path) -> list[dict]:
    items = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        items.append(json.loads(line))
    return items


def _resolve_gold(chunks: list[Chunk], refs):
    """把 golden 里的 file + contains 标注解析成块下标。"""
    ids = set()
    missing = []
    for ref in refs or []:
        file_suffix = str(ref.get('file') or '').replace('\\', '/').lstrip('./')
        needle = str(ref.get('contains') or '')
        hits = [
            chunk.idx for chunk in chunks
            if chunk.rel_path.replace('\\', '/').endswith(file_suffix)
            and needle in chunk.text
        ]
        if not hits:
            missing.append(ref)
        ids.update(hits)
    return ids, missing


def run(chunks: list[Chunk], retriever, golden: list[dict],
        k: int = 5, attr_k: int = 20) -> dict:
    rows = []
    answerable = []
    negatives = []

    for item in golden:
        question = str(item.get('question') or '').strip()
        if not question:
            continue
        results = retriever.search(question, max(k, attr_k))

        if not item.get('answerable', True):
            top_score = results[0][1] if results else 0.0
            negatives.append({
                'id': item.get('id'),
                'question': question,
                'top_score': round(top_score, 4),
                'top1': results[0][0].rel_path if results else '',
            })
            continue

        gold_ids, missing = _resolve_gold(chunks, item.get('gold'))
        rank = None
        for pos, (chunk, _score) in enumerate(results, 1):
            if chunk.idx in gold_ids:
                rank = pos
                break
        hits_in_k = [1 for chunk, _ in results[:k] if chunk.idx in gold_ids]

        if not gold_ids:
            failure = 'gold 缺失'
        elif rank is None:
            failure = '召回失败'
        elif rank > k:
            failure = '排序失败'
        else:
            failure = '成功'

        top_chunk = results[0][0] if results else None
        row = {
            'id': item.get('id'),
            'question': question,
            'rank': rank,
            'recall': bool(rank is not None and rank <= k),
            'recall20': bool(rank is not None and rank <= attr_k),
            'mrr': (1.0 / rank) if rank else 0.0,
            'context_precision': (len(hits_in_k) / k) if k else 0.0,
            'failure': failure,
            'missing_gold': missing,
            'top1': {
                'file': top_chunk.rel_path if top_chunk else '',
                'heading': top_chunk.heading if top_chunk else '',
                'line': top_chunk.line_start if top_chunk else 0,
                'score': round(results[0][1], 4) if results else 0.0,
            },
        }
        answerable.append(row)
        rows.append(row)

    count = len(answerable) or 1
    summary = {
        'answerable': len(answerable),
        'negative': len(negatives),
        'k': k,
        'recall@k': round(sum(r['recall'] for r in answerable) / count, 4),
        'recall@20': round(sum(r['recall20'] for r in answerable) / count, 4),
        'mrr': round(sum(r['mrr'] for r in answerable) / count, 4),
        'context@k': round(sum(r['context_precision'] for r in answerable) / count, 4),
        'failure': {
            name: sum(1 for r in answerable if r['failure'] == name)
            for name in ('成功', '排序失败', '召回失败', 'gold 缺失')
        },
        'negative_top_score_avg': round(
            sum(n['top_score'] for n in negatives) / len(negatives), 4
        ) if negatives else 0.0,
    }
    return {
        'summary': summary,
        'rows': rows,
        'misses': [r for r in answerable if r['failure'] != '成功'],
        'negatives': negatives,
    }
