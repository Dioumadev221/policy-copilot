"""Structure-aware chunking.

Policy documents are hierarchical, and an answer is only useful if it can be traced to a
section. So we split on headings first, keep tables whole, then pack paragraphs up to a
token budget with a sentence-level overlap. Every chunk keeps its heading path, which is
also prepended to the text we embed (contextual embedding): "Carryover" alone is
ambiguous, "Leave Policy > Annual Leave > Carryover" is not.
"""

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"\w+|[^\w\s]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def count_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (no tokenizer dependency)."""
    return len(_TOKEN_RE.findall(text))


@dataclass(frozen=True)
class Chunk:
    ord: int
    section_path: str
    content: str
    token_count: int

    @property
    def embedding_text(self) -> str:
        return f"{self.section_path}\n{self.content}"


@dataclass
class _Section:
    path: list[str]
    blocks: list[str]


def _split_sections(text: str, default_title: str) -> tuple[str, list[_Section]]:
    title = default_title
    stack: list[tuple[int, str]] = []
    sections: list[_Section] = [_Section(path=[], blocks=[])]
    buf: list[str] = []
    in_code = False

    def flush_block() -> None:
        if buf:
            block = "\n".join(buf).strip()
            if block:
                sections[-1].blocks.append(block)
            buf.clear()

    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING_RE.match(line)
        if m:
            flush_block()
            level, heading = len(m.group(1)), m.group(2).strip()
            if level == 1 and title == default_title:
                title = heading
            stack = [(lvl, h) for lvl, h in stack if lvl < level] + [(level, heading)]
            sections.append(_Section(path=[h for _, h in stack], blocks=[]))
        elif not line.strip() and not in_code:
            flush_block()
        else:
            # A table row directly after a paragraph line starts a new block.
            if buf and line.lstrip().startswith("|") != buf[-1].lstrip().startswith("|"):
                flush_block()
            buf.append(line)
    flush_block()

    for s in sections:
        if not s.path or s.path[0] != title:
            s.path = [title] + s.path
    return title, [s for s in sections if s.blocks]


def _split_long(block: str, max_tokens: int) -> list[str]:
    if count_tokens(block) <= max_tokens or block.lstrip().startswith("|"):
        return [block]  # tables are never split: a half table is a wrong table
    pieces, cur = [], ""
    for sent in _SENTENCE_RE.split(block):
        candidate = f"{cur} {sent}".strip()
        if cur and count_tokens(candidate) > max_tokens:
            pieces.append(cur)
            cur = sent
        else:
            cur = candidate
    if cur:
        pieces.append(cur)
    return pieces


def _tail_overlap(text: str, overlap_tokens: int) -> str:
    if overlap_tokens <= 0:
        return ""
    out: list[str] = []
    for sent in reversed(_SENTENCE_RE.split(text)):
        if count_tokens(" ".join([sent, *out])) > overlap_tokens:
            break
        out.insert(0, sent)
    return " ".join(out)


def chunk_markdown(
    text: str, default_title: str, max_tokens: int = 220, overlap_tokens: int = 40
) -> tuple[str, list[Chunk]]:
    title, sections = _split_sections(text, default_title)
    chunks: list[Chunk] = []
    for section in sections:
        path = " > ".join(section.path)
        units = [u for b in section.blocks for u in _split_long(b, max_tokens - overlap_tokens)]
        cur: list[str] = []
        for unit in units:
            if cur and count_tokens("\n\n".join([*cur, unit])) > max_tokens:
                content = "\n\n".join(cur)
                chunks.append(Chunk(len(chunks), path, content, count_tokens(content)))
                overlap = _tail_overlap(cur[-1], overlap_tokens)
                cur = [overlap] if overlap and not cur[-1].lstrip().startswith("|") else []
                if cur and count_tokens("\n\n".join([*cur, unit])) > max_tokens:
                    cur = []  # overlap would push this chunk over budget: drop it
            cur.append(unit)
        if cur:
            content = "\n\n".join(cur)
            chunks.append(Chunk(len(chunks), path, content, count_tokens(content)))
    return title, chunks
