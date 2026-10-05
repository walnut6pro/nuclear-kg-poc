"""解析与切分：Markdown → 结构化 chunk。

规则（对齐方案第 5 步）：
1. 按标题层级切分，每个 chunk 携带完整标题路径；
2. 表格永远独立成 chunk，不与正文混合，并携带表格前的说明文字作为上下文；
3. chunk 长度 800~1500 字，超出则按段落二次切分；
4. 每条 chunk 元数据：doc_id / page / section_path / chunk_type / chunk_id。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

MAX_CHUNK_CHARS = 1500
MIN_TABLE_CONTEXT = 1  # 表格前保留的说明段落数

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
PAGE_RE = re.compile(r"^<!--\s*page:\s*(\d+)\s*-->$")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    page: int | None
    section_path: str
    chunk_type: str          # text | table
    text: str
    preceding_text: str = ""  # 仅 table chunk 使用
    table_md: str = ""
    security_level: str = "公开"   # 预留：从第一天就养成分级打标的习惯

    def to_dict(self):
        return asdict(self)


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _merge_small(paras: Iterable[str], limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """把短段落合并到接近 800~1500 字的块；超长段落单独成块并再按句切。"""
    out, buf = [], ""
    for p in paras:
        if len(p) > limit:
            if buf:
                out.append(buf)
                buf = ""
            # 超长段落按句二次切分
            sentences = re.split(r"(?<=[。！？；])", p)
            sub = ""
            for s in sentences:
                if len(sub) + len(s) > limit and sub:
                    out.append(sub.strip())
                    sub = s
                else:
                    sub += s
            if sub.strip():
                out.append(sub.strip())
            continue
        if len(buf) + len(p) + 2 > limit and buf:
            out.append(buf)
            buf = p
        else:
            buf = (buf + "\n\n" + p).strip() if buf else p
    if buf:
        out.append(buf)
    return [o for o in out if o.strip()]


def parse_markdown(path: str | Path, *, security_level: str = "公开") -> list[Chunk]:
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()

    doc_id = path.stem
    chunks: list[Chunk] = []
    heading_stack: list[tuple[int, str]] = []
    page: int | None = None

    body: list[str] = []          # 当前节下的正文行（不含表格）
    table_buf: list[str] = []     # 当前正在收集的表格行
    in_table = False
    seq = 0

    def new_id() -> str:
        nonlocal seq
        seq += 1
        return f"{doc_id}::C{seq:03d}"

    def section_path() -> str:
        return " > ".join(t for _, t in heading_stack) if heading_stack else doc_id

    def trailing_context(n: int = MIN_TABLE_CONTEXT) -> str:
        """取表格前的说明文字作为上下文。"""
        txt = "\n".join(body).strip()
        paras = _split_paragraphs(txt)
        return "\n\n".join(paras[-n:]) if paras else ""

    def flush_body():
        nonlocal body
        if not body:
            return
        txt = "\n".join(body).strip()
        for piece in _merge_small(_split_paragraphs(txt)):
            chunks.append(
                Chunk(new_id(), doc_id, doc_id, page, section_path(), "text", piece,
                      security_level=security_level)
            )
        body = []

    def flush_table():
        nonlocal table_buf, in_table
        if not table_buf:
            return
        table_md = "\n".join(table_buf).strip()
        chunks.append(
            Chunk(new_id(), doc_id, doc_id, page, section_path(), "table",
                  table_md, preceding_text=trailing_context(), table_md=table_md,
                  security_level=security_level)
        )
        table_buf = []
        in_table = False

    for raw in lines:
        line = raw.rstrip()
        m_page = PAGE_RE.match(line.strip())
        if m_page:
            page = int(m_page.group(1))
            continue

        m_h = HEADING_RE.match(line)
        is_table_line = line.strip().startswith("|")

        if m_h:
            # 标题：先把未冲刷的内容写出去
            if in_table:
                flush_table()
            flush_body()
            level, title = len(m_h.group(1)), m_h.group(2).strip()
            doc_title = None
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            # 一级标题作为文档标题
            if level == 1:
                chunks.append(Chunk(new_id(), doc_id, doc_id, page, title, "heading", "",
                                    security_level=security_level))
            continue

        if is_table_line:
            if not in_table:
                flush_body()          # 表格前的正文作为上下文保留，不再混进表格 chunk
                in_table = True
            table_buf.append(line)
            continue

        if in_table:
            flush_table()

        if line.strip():
            body.append(line)

    if in_table:
        flush_table()
    flush_body()
    return chunks
