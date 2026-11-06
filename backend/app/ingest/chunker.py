"""Syntax-aware chunking (after cAST, arXiv 2506.15655).

Code files are split along tree-sitter node boundaries with a recursive split-then-merge pass:
nodes that fit the size budget are packed together greedily, oversized nodes are split into their
children. The resulting line ranges partition the file, so concatenating the chunks reproduces it.

Each chunk carries a header with its file path, enclosing scope and the symbols it defines. That
header goes into both the embedding input and the keyword index.
"""

import re
from dataclasses import dataclass, field

from tree_sitter import Node, Tree

from app.ingest.symbols import Symbol


@dataclass
class Chunk:
    path: str
    language: str | None
    start_line: int  # 1-based, inclusive
    end_line: int
    content: str
    scope: str | None = None
    defines: list[str] = field(default_factory=list)

    @property
    def header(self) -> str:
        # No line numbers here: chunks that only shifted position keep the same embedding cache key.
        lines = [f"File: {self.path}"]
        if self.scope:
            lines.append(f"Scope: {self.scope}")
        if self.defines:
            lines.append(f"Defines: {', '.join(self.defines[:12])}")
        return "\n".join(lines)

    @property
    def embed_text(self) -> str:
        return f"{self.header}\n\n{self.content}"


def _size(text: bytes | str) -> int:
    """Budget in non-whitespace characters, so indentation does not eat the budget."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return len(re.sub(r"\s+", "", text))


def _split_merge(nodes: list[Node], source: bytes, max_chars: int) -> list[list[Node]]:
    groups: list[list[Node]] = []
    current: list[Node] = []
    current_size = 0
    for node in nodes:
        size = _size(source[node.start_byte:node.end_byte])
        if size > max_chars:
            if current:
                groups.append(current)
                current, current_size = [], 0
            if node.child_count:
                groups.extend(_split_merge(list(node.children), source, max_chars))
            else:
                groups.append([node])
        elif current and current_size + size > max_chars:
            groups.append(current)
            current, current_size = [node], size
        else:
            current.append(node)
            current_size += size
    if current:
        groups.append(current)
    return groups


def _ranges_from_groups(groups: list[list[Node]], line_count: int) -> list[tuple[int, int]]:
    """Turn node groups into 0-based inclusive line ranges that exactly partition the file."""
    raw: list[tuple[int, int]] = []
    for group in groups:
        start = group[0].start_point.row
        end_node = group[-1]
        end = end_node.end_point.row
        if end_node.end_point.column == 0 and end > start:
            end -= 1
        raw.append((start, end))
    raw.sort()

    ranges: list[tuple[int, int]] = []
    for start, end in raw:
        if ranges:
            prev_start, prev_end = ranges[-1]
            if start <= prev_end:  # two groups share a line: keep it in the earlier chunk
                start = prev_end + 1
                if start > end:
                    continue
            elif start > prev_end + 1:  # gap (blank lines, comments): attach to the next chunk
                start = prev_end + 1
        else:
            start = 0
        ranges.append((start, end))
    if not ranges:
        return [(0, max(line_count - 1, 0))]
    last_start, last_end = ranges[-1]
    if last_end < line_count - 1:
        ranges[-1] = (last_start, line_count - 1)
    return ranges


def _merge_small(ranges: list[tuple[int, int]], lines: list[str], max_chars: int) -> list[tuple[int, int]]:
    """Fold tiny ranges (a lone `class Foo:` line, a closing brace) into a neighbour."""
    min_chars = max_chars // 4

    def size(r: tuple[int, int]) -> int:
        return _size("\n".join(lines[r[0]:r[1] + 1]))

    merged: list[tuple[int, int]] = []
    for r in ranges:
        if merged:
            prev = merged[-1]
            combined = (prev[0], r[1])
            if (size(prev) < min_chars or size(r) < min_chars) and size(combined) <= max_chars:
                merged[-1] = combined
                continue
        merged.append(r)
    return merged


def _split_long_ranges(ranges: list[tuple[int, int]], lines: list[str], max_chars: int) -> list[tuple[int, int]]:
    """A single node can still be too big (a huge string literal); fall back to line windows."""
    out: list[tuple[int, int]] = []
    for start, end in ranges:
        if _size("\n".join(lines[start:end + 1])) <= max_chars * 1.5:
            out.append((start, end))
            continue
        out.extend(_line_windows(lines, start, end, max_chars))
    return out


def _line_windows(lines: list[str], start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    window_start, budget = start, 0
    for i in range(start, end + 1):
        size = _size(lines[i])
        if budget and budget + size > max_chars:
            windows.append((window_start, i - 1))
            window_start, budget = i, 0
        budget += size
    windows.append((window_start, end))
    return windows


def _scope_for(start: int, end: int, symbols: list[Symbol]) -> tuple[str | None, list[str]]:
    """Innermost symbol enclosing the chunk, and the symbols that start inside it (1-based lines)."""
    enclosing = [s for s in symbols if s.start_line < start <= s.end_line]
    scope = None
    if enclosing:
        inner = max(enclosing, key=lambda s: s.start_line)
        scope = f"{inner.kind} {inner.qualified}"
    defines = [s.qualified for s in symbols if start <= s.start_line <= end]
    return scope, defines


def _build(path: str, language: str | None, lines: list[str], ranges: list[tuple[int, int]],
           symbols: list[Symbol]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for start, end in ranges:
        content = "\n".join(lines[start:end + 1])
        if not content.strip():
            continue
        scope, defines = _scope_for(start + 1, end + 1, symbols)
        chunks.append(Chunk(path, language, start + 1, end + 1, content, scope, defines))
    return chunks


def chunk_code(path: str, language: str, text: str, tree: Tree, symbols: list[Symbol],
               max_chars: int) -> list[Chunk]:
    source = text.encode("utf-8")
    lines = text.split("\n")
    groups = _split_merge(list(tree.root_node.children), source, max_chars)
    ranges = _ranges_from_groups(groups, len(lines))
    ranges = _merge_small(ranges, lines, max_chars)
    ranges = _split_long_ranges(ranges, lines, max_chars)
    return _build(path, language, lines, ranges, symbols)


def chunk_line_windows(path: str, language: str | None, text: str, window: int = 40,
                       overlap: int = 10) -> list[Chunk]:
    """Naive fixed-size chunking: `window` lines with `overlap` lines shared between neighbours.
    Used only as the evaluation baseline that syntax-aware chunking is compared against."""
    lines = text.split("\n")
    chunks: list[Chunk] = []
    start = 0
    while start < len(lines):
        end = min(start + window, len(lines)) - 1
        content = "\n".join(lines[start:end + 1])
        if content.strip():
            chunks.append(Chunk(path, language, start + 1, end + 1, content))
        if end == len(lines) - 1:
            break
        start = end + 1 - overlap
    return chunks


_MD_HEADING = re.compile(r"^#{1,6}\s")


def chunk_text(path: str, language: str | None, text: str, max_chars: int) -> list[Chunk]:
    """Markdown splits at headings; everything else uses line windows."""
    lines = text.split("\n")
    if not lines:
        return []
    if language == "markdown":
        starts = [i for i, line in enumerate(lines) if _MD_HEADING.match(line)] or [0]
        if starts[0] != 0:
            starts.insert(0, 0)
        sections = [(s, (starts[i + 1] - 1) if i + 1 < len(starts) else len(lines) - 1)
                    for i, s in enumerate(starts)]
        ranges = _merge_small(sections, lines, max_chars)
        ranges = _split_long_ranges(ranges, lines, max_chars)
    else:
        ranges = _line_windows(lines, 0, len(lines) - 1, max_chars)
    return _build(path, language, lines, ranges, [])
