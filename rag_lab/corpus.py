# -*- coding: utf-8 -*-
"""语料加载与切块。

两种切法：
- heading：先按 Markdown 标题分节，再把过长的节切开（推荐）
- fixed：整篇文档按固定长度滑窗切开（用于做对比实验）
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*$')
_SENT_END = '。！？；!?;\n'
_SKIP_PARTS = {'_build', '.venv', '__pycache__', 'node_modules', '.git'}


@dataclass
class Document:
    rel_path: str
    text: str


@dataclass
class Chunk:
    idx: int
    rel_path: str
    heading: str
    line_start: int
    text: str          # 标题路径 + 正文，用于检索
    body: str          # 纯正文，用于展示和生成上下文


def discover(root: Path, sources: list[str]) -> list[Path]:
    """找出 sources 里的 Markdown 文件，去重并跳过构建目录。"""
    root = Path(root)
    found: list[Path] = []
    seen: set[Path] = set()
    for source in sources:
        path = (root / source).resolve()
        if path.is_dir():
            candidates = sorted(path.rglob('*.md'))
        elif path.is_file():
            candidates = [path]
        else:
            continue
        for item in candidates:
            resolved = item.resolve()
            if resolved in seen:
                continue
            if any(part in _SKIP_PARTS for part in resolved.parts):
                continue
            seen.add(resolved)
            found.append(resolved)
    return found


def load_documents(root: Path, sources: list[str]) -> list[Document]:
    root = Path(root).resolve()
    docs: list[Document] = []
    for path in discover(root, sources):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.name
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        docs.append(Document(rel_path=rel, text=text))
    return docs


def _fence_kind(line: str, previous: str, in_fence: bool):
    """判断这行是不是代码块围栏。

    README 里有一个漏了开头围栏、只剩结尾 ``` 的代码块。如果把这种孤立的
    裸围栏当成“开始”，后面所有标题都会被错当成代码内容。这里用一个小启发：
    不在围栏里时，带语言标记或前面是空行的 ``` 才当成开始；前面紧挨着代码
    的裸 ``` 更像多余的结束符，直接忽略。
    """
    stripped = line.strip()
    if not stripped.startswith('```'):
        return None
    rest = stripped[3:].strip()
    if in_fence:
        return 'close' if not rest else None
    if rest:
        return 'open'
    return 'open' if not previous.strip() else None


def _sections(text: str):
    """按 Markdown 标题分节，返回 (标题路径, 起始行, 正文)。代码块里的 # 不算标题。"""
    lines = text.splitlines()
    in_fence = False
    stack: list[tuple[int, str]] = []
    sections = []
    current_path: list[str] = []
    current_lines: list[str] = []
    start_line = 1
    previous = ''

    for lineno, line in enumerate(lines, 1):
        kind = _fence_kind(line, previous, in_fence)
        if kind == 'open':
            in_fence = True
            current_lines.append(line)
        elif kind == 'close':
            in_fence = False
            current_lines.append(line)
        else:
            match = None if in_fence else _HEADING_RE.match(line)
            # README 里还有一个漏了开头围栏的代码块，里面的 `# 或只启动...`
            # 会被当成一级标题。Markdown 标题在实际文档里几乎总是跟在空行后，
            # 用这个条件把代码注释排除掉；连续标题仍然允许。
            if match and lineno > 1 and previous.strip() and not _HEADING_RE.match(previous):
                match = None
            if not match:
                current_lines.append(line)
            else:
                body = '\n'.join(current_lines).strip()
                if body:
                    sections.append((list(current_path), start_line, body))

                level = len(match.group(1))
                title = match.group(2).strip()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                current_path = [item[1] for item in stack]
                current_lines = []
                start_line = lineno + 1
        previous = line

    body = '\n'.join(current_lines).strip()
    if body:
        sections.append((list(current_path), start_line, body))
    return sections


def _split_body(body: str, max_chars: int, overlap: int, base_line: int):
    """把一段正文切成不超过 max_chars 的块，尽量在句末断开。"""
    if len(body) <= max_chars:
        return [(base_line, body)]

    out = []
    pos = 0
    size = len(body)
    while pos < size:
        end = min(size, pos + max_chars)
        if end < size:
            window = body[pos:end]
            cut = max(window.rfind(ch) for ch in _SENT_END)
            if cut >= int(max_chars * 0.45):
                end = pos + cut + 1
        piece = body[pos:end].strip()
        if piece:
            line = base_line + body[:pos].count('\n')
            out.append((line, piece))
        if end >= size:
            break
        pos = max(end - overlap, pos + 1)
    return out


def chunk_documents(docs: list[Document], strategy: str = 'heading',
                    max_chars: int = 700, overlap: int = 120) -> list[Chunk]:
    """把文档切成块；chunk.idx 在整次构建里唯一。"""
    chunks: list[Chunk] = []
    for doc in docs:
        if strategy == 'fixed':
            for line, body in _split_body(doc.text, max_chars, overlap, 1):
                chunks.append(Chunk(len(chunks), doc.rel_path, '', line, body, body))
            continue

        for path, start_line, body in _sections(doc.text):
            heading = ' > '.join(path)
            for line, piece in _split_body(body, max_chars, overlap, start_line):
                text = (heading + '\n' + piece) if heading else piece
                chunks.append(Chunk(len(chunks), doc.rel_path, heading, line,
                                    text, piece))
    return chunks
