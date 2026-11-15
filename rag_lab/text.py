# -*- coding: utf-8 -*-
"""轻量分词：中文按二元组、英文按单词，不依赖第三方库。"""
from __future__ import annotations

import re

_ASCII_RE = re.compile(r'[a-z0-9_]{2,}')
_CJK_RE = re.compile(r'[一-鿿]+')


def norm(text: str) -> str:
    """小写、空白折叠，用于短语包含判断。"""
    return ' '.join(str(text or '').lower().split())


def tokens(text: str) -> list[str]:
    """英文按单词，中文按二元组；单字中文也保留，短查询才有命中。"""
    text = str(text or '').lower()
    out = _ASCII_RE.findall(text)
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out
