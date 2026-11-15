# -*- coding: utf-8 -*-
"""两种纯标准库检索器：BM25 和中文二元组重合度。"""
from __future__ import annotations

import math
from collections import Counter

from .corpus import Chunk
from .text import norm, tokens


class BigramRetriever:
    """项目里 ai_memory 的同款思路：词元重合度 + 短语命中加分。"""

    name = 'bigram'

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.sets = [set(tokens(c.text)) for c in chunks]
        self.norms = [norm(c.text) for c in chunks]

    def search(self, query: str, k: int = 5):
        query_tokens = set(tokens(query))
        query_norm = norm(query)
        scored = []
        for idx, doc_tokens in enumerate(self.sets):
            hit = len(query_tokens & doc_tokens)
            if not hit:
                continue
            score = 2.0 * hit / math.sqrt(len(doc_tokens) + 1)
            if query_norm and query_norm in self.norms[idx]:
                score += 0.5
            scored.append((idx, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [(self.chunks[idx], score) for idx, score in scored[:k]]


class BM25Retriever:
    """标准 BM25Okapi；k1=1.5、b=0.75。"""

    name = 'bm25'

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.docs = [tokens(c.text) for c in chunks]
        self.tfs = [Counter(doc) for doc in self.docs]
        self.dls = [len(doc) for doc in self.docs]
        self.avgdl = (sum(self.dls) / len(self.dls)) if self.dls else 0.0
        self.df: Counter = Counter()
        for doc in self.docs:
            self.df.update(set(doc))
        self.n = len(self.docs)
        self.idf = {
            term: math.log(1 + (self.n - freq + 0.5) / (freq + 0.5))
            for term, freq in self.df.items()
        }

    def search(self, query: str, k: int = 5):
        query_terms = set(tokens(query))
        if not query_terms:
            return []
        scored = []
        for idx, tf in enumerate(self.tfs):
            if not tf:
                continue
            dl = self.dls[idx] or 1
            score = 0.0
            for term in query_terms:
                freq = tf.get(term)
                if not freq:
                    continue
                idf = self.idf.get(term)
                if idf is None:
                    continue
                denom = freq + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                score += idf * (freq * (self.k1 + 1)) / denom
            if score > 0:
                scored.append((idx, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [(self.chunks[idx], score) for idx, score in scored[:k]]


def build_retriever(name: str, chunks: list[Chunk]):
    if name == 'bigram':
        return BigramRetriever(chunks)
    return BM25Retriever(chunks)
