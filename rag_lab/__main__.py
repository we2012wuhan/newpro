# -*- coding: utf-8 -*-
"""RAG 实验台命令行入口。

用法：
  python -m rag_lab build
  python -m rag_lab ask "长期记忆最多保留多少条？"
  python -m rag_lab ask "..." --generate
  python -m rag_lab eval
  python -m rag_lab compare
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import corpus
from .evaluate import load_golden, run as run_eval
from .retrieval import build_retriever

_PKG = Path(__file__).resolve().parent
_ROOT = _PKG.parent
_DEFAULT_GOLDEN = _PKG / 'golden.jsonl'


def _setup_stdio() -> None:
    """Windows 终端有时不是 UTF-8，避免中文输出直接报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass


def _add_common(parser) -> None:
    parser.add_argument('--root', default=str(_ROOT), help='项目根目录')
    parser.add_argument('--sources', nargs='+', default=['README.md', 'docs'],
                        help='语料文件或目录，默认 README.md 和 docs')
    parser.add_argument('--strategy', choices=['heading', 'fixed'],
                        default='heading', help='切块策略')
    parser.add_argument('--max-chars', type=int, default=700, help='单块最大字数')
    parser.add_argument('--overlap', type=int, default=120, help='相邻块重叠字数')
    parser.add_argument('--retriever', choices=['bm25', 'bigram'],
                        default='bm25', help='检索器')
    parser.add_argument('--k', type=int, default=5, help='取前 k 个块')


def _load(args):
    docs = corpus.load_documents(Path(args.root), args.sources)
    chunks = corpus.chunk_documents(docs, args.strategy, args.max_chars,
                                    args.overlap)
    return docs, chunks


def cmd_build(args) -> None:
    docs, chunks = _load(args)
    average = (sum(len(chunk.body) for chunk in chunks) / len(chunks)
               if chunks else 0)
    print('文件：%d 个' % len(docs))
    print('块：%d 个（策略 %s，max_chars=%d，overlap=%d）'
          % (len(chunks), args.strategy, args.max_chars, args.overlap))
    print('平均块长：%.0f 字' % average)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open('w', encoding='utf-8') as handle:
            for chunk in chunks:
                handle.write(json.dumps({
                    'idx': chunk.idx,
                    'file': chunk.rel_path,
                    'heading': chunk.heading,
                    'line': chunk.line_start,
                    'chars': len(chunk.body),
                    'body': chunk.body,
                }, ensure_ascii=False) + '\n')
        print('已写出：%s' % out)


def cmd_ask(args) -> None:
    _docs, chunks = _load(args)
    retriever = build_retriever(args.retriever, chunks)
    hits = retriever.search(args.question, args.k)
    print('检索器：%s｜语料：%d 块｜问题：%s\n'
          % (args.retriever, len(chunks), args.question))
    if not hits:
        print('没有命中任何块。')
        return

    for number, (chunk, score) in enumerate(hits, 1):
        print('[%d] %.4f  %s:%d'
              % (number, score, chunk.rel_path, chunk.line_start))
        if chunk.heading:
            print('    %s' % chunk.heading)
        snippet = ' '.join(chunk.body.split())
        print('    %s%s' % (snippet[:180], '…' if len(snippet) > 180 else ''))
        print()

    if args.generate:
        from .generate import answer
        text, meta = answer(args.question, hits)
        print('--- 模型回答 ---')
        if text is None:
            print(meta)
            return
        print(text)
        print('\n引用编号：%s' % (meta['cited'] or '无'))
        if meta['invalid_citations']:
            print('无效引用：%s（可用范围 1-%d）'
                  % (meta['invalid_citations'], meta['max_citation']))


def _print_eval(result, args, verbose: bool) -> None:
    summary = result['summary']
    print('RAG 检索评估 · %s · %s · k=%d'
          % (args.strategy, args.retriever, summary['k']))
    print('可答问题：%d｜不可答问题：%d'
          % (summary['answerable'], summary['negative']))
    print()
    print('  Recall@%-2d      %.4f' % (summary['k'], summary['recall@k']))
    print('  Recall@20       %.4f' % summary['recall@20'])
    print('  MRR             %.4f' % summary['mrr'])
    print('  Context@%-2d     %.4f' % (summary['k'], summary['context@k']))
    print()
    failure = summary['failure']
    print('失败归因：成功 %d｜排序失败 %d｜召回失败 %d｜gold 缺失 %d'
          % (failure['成功'], failure['排序失败'],
             failure['召回失败'], failure['gold 缺失']))
    if summary['negative']:
        print('不可答样本的最高分均值：%.4f（可用于观察拒答阈值）'
              % summary['negative_top_score_avg'])
    print()

    show = result['rows'] if verbose else result['misses']
    if not show:
        print('没有失败项。')
        return
    print('明细：')
    for row in show:
        mark = '✓' if row['failure'] == '成功' else '✗'
        rank = ('第 %d 位' % row['rank']) if row['rank'] else '未命中'
        print('%s %-6s [%s] %s'
              % (mark, row['failure'], row['id'], row['question']))
        print('    gold %s｜top1：%s:%d %.4f'
              % (rank, row['top1']['file'], row['top1']['line'],
                 row['top1']['score']))
        if row['missing_gold']:
            print('    ⚠ 标注里的 gold 没在语料中找到：%s'
                  % json.dumps(row['missing_gold'], ensure_ascii=False))


def cmd_eval(args) -> None:
    _docs, chunks = _load(args)
    retriever = build_retriever(args.retriever, chunks)
    golden = load_golden(Path(args.golden))
    if args.limit:
        golden = golden[:args.limit]
    result = run_eval(chunks, retriever, golden, args.k)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    _print_eval(result, args, args.verbose)


def cmd_compare(args) -> None:
    golden = load_golden(Path(args.golden))
    if args.limit:
        golden = golden[:args.limit]
    docs = corpus.load_documents(Path(args.root), args.sources)

    print('策略        检索     Recall@%-2d   MRR      Context@%-2d  Recall@20  排序失败  召回失败'
          % (args.k, args.k))
    print('-' * 88)
    for strategy in ('heading', 'fixed'):
        chunks = corpus.chunk_documents(docs, strategy, args.max_chars,
                                        args.overlap)
        for name in ('bm25', 'bigram'):
            retriever = build_retriever(name, chunks)
            summary = run_eval(chunks, retriever, golden, args.k)['summary']
            print('%-10s  %-6s  %.4f      %.4f   %.4f       %.4f     %-8d  %-8d'
                  % (strategy, name, summary['recall@k'], summary['mrr'],
                     summary['context@k'], summary['recall@20'],
                     summary['failure']['排序失败'],
                     summary['failure']['召回失败']))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m rag_lab',
        description='在本项目文档上做可重复的 RAG 检索实验',
    )
    sub = parser.add_subparsers(dest='command', required=True)

    p_build = sub.add_parser('build', help='查看语料和切块统计')
    _add_common(p_build)
    p_build.add_argument('--out', default='', help='把切块写到 JSONL 方便检查')
    p_build.set_defaults(func=cmd_build)

    p_ask = sub.add_parser('ask', help='检索一个问题')
    _add_common(p_ask)
    p_ask.add_argument('question')
    p_ask.add_argument('--generate', action='store_true',
                       help='调用当前大模型生成带引用的回答')
    p_ask.set_defaults(func=cmd_ask)

    p_eval = sub.add_parser('eval', help='在 golden set 上评估检索')
    _add_common(p_eval)
    p_eval.add_argument('--golden', default=str(_DEFAULT_GOLDEN))
    p_eval.add_argument('--json', action='store_true', help='输出 JSON')
    p_eval.add_argument('--verbose', action='store_true',
                        help='打印全部问题，而不只是失败项')
    p_eval.add_argument('--limit', type=int, default=0, help='只跑前 N 条')
    p_eval.set_defaults(func=cmd_eval)

    p_compare = sub.add_parser('compare', help='对比两种切法和两种检索器')
    _add_common(p_compare)
    p_compare.add_argument('--golden', default=str(_DEFAULT_GOLDEN))
    p_compare.add_argument('--limit', type=int, default=0, help='只跑前 N 条')
    p_compare.set_defaults(func=cmd_compare)

    return parser


def main(argv=None) -> None:
    _setup_stdio()
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
