# -*- coding: utf-8 -*-
"""RAG 实验台网页接口：检索、带引用回答、评估和对比。

页面在 /rag-lab，前端只传实验参数，切块 / 检索 / 评估全部复用包内的
纯函数，保证网页结果和命令行 `python -m rag_lab` 完全一致。
"""
from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import corpus
from .evaluate import load_golden, run as run_eval
from .retrieval import build_retriever

router = APIRouter()

_PKG = Path(__file__).resolve().parent
_ROOT = _PKG.parent
_PAGE = _ROOT / 'templates' / 'rag-lab.html'
_GOLDEN = _PKG / 'golden.jsonl'
_SOURCES = ('README.md', 'docs')

_STRATEGIES = ('heading', 'fixed')
_RETRIEVERS = ('bm25', 'bigram')
_DEFAULT_STRATEGY = 'heading'
_DEFAULT_RETRIEVER = 'bm25'
_DEFAULT_K = 5
_DEFAULT_MAX_CHARS = 700
_DEFAULT_OVERLAP = 120
_SAMPLE_IDS = ('arch-04', 'arch-01', 'arch-02', 'deploy-02', 'neg-01')


class AskBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    strategy: str = _DEFAULT_STRATEGY
    retriever: str = _DEFAULT_RETRIEVER
    k: int = _DEFAULT_K
    max_chars: int = _DEFAULT_MAX_CHARS
    overlap: int = _DEFAULT_OVERLAP
    generate: bool = True


class EvalBody(BaseModel):
    strategy: str = _DEFAULT_STRATEGY
    retriever: str = _DEFAULT_RETRIEVER
    k: int = _DEFAULT_K
    max_chars: int = _DEFAULT_MAX_CHARS
    overlap: int = _DEFAULT_OVERLAP


class CompareBody(BaseModel):
    k: int = _DEFAULT_K
    max_chars: int = _DEFAULT_MAX_CHARS
    overlap: int = _DEFAULT_OVERLAP


def _signature() -> tuple:
    """语料签名。文档改动后自动重建缓存，不用重启服务。"""
    parts = []
    for path in corpus.discover(_ROOT, list(_SOURCES)):
        try:
            stat = path.stat()
        except OSError:
            continue
        parts.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(parts)


@lru_cache(maxsize=16)
def _chunks(strategy: str, max_chars: int, overlap: int, _sig: tuple):
    docs = corpus.load_documents(_ROOT, list(_SOURCES))
    return corpus.chunk_documents(docs, strategy, max_chars, overlap)


def _clean(body) -> dict:
    # CompareBody 只带 k / max_chars / overlap，策略和检索器由对比接口自己遍历。
    strategy = getattr(body, 'strategy', _DEFAULT_STRATEGY)
    retriever = getattr(body, 'retriever', _DEFAULT_RETRIEVER)
    if strategy not in _STRATEGIES:
        strategy = _DEFAULT_STRATEGY
    if retriever not in _RETRIEVERS:
        retriever = _DEFAULT_RETRIEVER
    k = max(1, min(int(getattr(body, 'k', _DEFAULT_K)), 10))
    max_chars = max(200, min(int(getattr(body, 'max_chars', _DEFAULT_MAX_CHARS)), 2000))
    overlap = max(0, min(int(getattr(body, 'overlap', _DEFAULT_OVERLAP)), max_chars // 2))
    return {
        'strategy': strategy,
        'retriever': retriever,
        'k': k,
        'max_chars': max_chars,
        'overlap': overlap,
    }


def _build(settings: dict):
    chunks = _chunks(settings['strategy'], settings['max_chars'],
                     settings['overlap'], _signature())
    return chunks, build_retriever(settings['retriever'], chunks)


def _doc_files() -> list[str]:
    root = _ROOT.resolve()
    files = []
    for path in corpus.discover(root, list(_SOURCES)):
        try:
            files.append(path.resolve().relative_to(root).as_posix())
        except ValueError:
            files.append(path.name)
    return files


@router.get('/rag-lab', response_class=HTMLResponse)
def rag_lab_page():
    if not _PAGE.exists():
        return HTMLResponse('<h1>RAG 实验台页面不存在</h1>', status_code=500)
    return HTMLResponse(_PAGE.read_text(encoding='utf-8'))


@router.get('/rag-lab/api/status')
def rag_lab_status() -> dict:
    from model_config import llm_key

    settings = {
        'strategy': _DEFAULT_STRATEGY,
        'retriever': _DEFAULT_RETRIEVER,
        'k': _DEFAULT_K,
        'max_chars': _DEFAULT_MAX_CHARS,
        'overlap': _DEFAULT_OVERLAP,
    }
    chunks, _ = _build(settings)
    golden = load_golden(_GOLDEN)
    by_id = {str(item.get('id')): item for item in golden}
    samples = [
        {
            'id': key,
            'question': by_id[key].get('question'),
            'answerable': bool(by_id[key].get('answerable', True)),
        }
        for key in _SAMPLE_IDS if key in by_id
    ]
    average = round(sum(len(c.body) for c in chunks) / len(chunks), 1) if chunks else 0
    return {
        'ok': True,
        'files': _doc_files(),
        'chunks': len(chunks),
        'avg_chars': average,
        'answerable': sum(1 for item in golden if item.get('answerable', True)),
        'negative': sum(1 for item in golden if not item.get('answerable', True)),
        'ai': bool(llm_key()),
        'samples': samples,
        'defaults': settings,
    }


@router.post('/rag-lab/api/ask')
def rag_lab_ask(body: AskBody) -> dict:
    question = body.question.strip()
    if not question:
        return {'ok': False, 'message': '问题不能为空。'}

    settings = _clean(body)
    chunks, retriever = _build(settings)
    started = time.perf_counter()
    results = retriever.search(question, settings['k'])
    elapsed = int((time.perf_counter() - started) * 1000)

    hits = []
    for rank, (chunk, score) in enumerate(results, 1):
        hits.append({
            'rank': rank,
            'score': round(score, 4),
            'file': chunk.rel_path,
            'heading': chunk.heading,
            'line': chunk.line_start,
            'chars': len(chunk.body),
            'body': chunk.body,
        })

    payload = {
        'ok': True,
        'question': question,
        'settings': settings,
        'chunks': len(chunks),
        'elapsed_ms': elapsed,
        'hits': hits,
        'answer': None,
        'answer_error': None,
        'cited': [],
        'invalid_citations': [],
        'max_citation': len(hits),
        'model': '',
    }

    if body.generate:
        from .generate import answer
        from model_config import llm_model

        started = time.perf_counter()
        text, meta = answer(question, results)
        payload['answer_elapsed_ms'] = int((time.perf_counter() - started) * 1000)
        payload['model'] = llm_model()
        if text is None:
            payload['answer_error'] = str(meta)
        else:
            payload['answer'] = text
            payload['cited'] = meta.get('cited') or []
            payload['invalid_citations'] = meta.get('invalid_citations') or []
    return payload


@router.post('/rag-lab/api/eval')
def rag_lab_eval(body: EvalBody) -> dict:
    settings = _clean(body)
    chunks, retriever = _build(settings)
    golden = load_golden(_GOLDEN)
    started = time.perf_counter()
    result = run_eval(chunks, retriever, golden, settings['k'])
    return {
        'ok': True,
        'settings': settings,
        'chunks': len(chunks),
        'golden': len(golden),
        'elapsed_ms': int((time.perf_counter() - started) * 1000),
        'summary': result['summary'],
        'misses': result['misses'],
        'negatives': result['negatives'],
    }


@router.post('/rag-lab/api/compare')
def rag_lab_compare(body: CompareBody) -> dict:
    settings = _clean(body)
    started = time.perf_counter()
    chunks_count = 0
    rows = []
    golden = load_golden(_GOLDEN)
    for strategy in _STRATEGIES:
        cell_settings = dict(settings, strategy=strategy)
        chunks, _ = _build(cell_settings)
        chunks_count = len(chunks)
        for retriever_name in _RETRIEVERS:
            retriever = build_retriever(retriever_name, chunks)
            summary = run_eval(chunks, retriever, golden,
                               settings['k'])['summary']
            rows.append({
                'strategy': strategy,
                'retriever': retriever_name,
                'summary': summary,
            })
    return {
        'ok': True,
        'k': settings['k'],
        'chunks': chunks_count,
        'golden': len(golden),
        'elapsed_ms': int((time.perf_counter() - started) * 1000),
        'rows': rows,
    }
