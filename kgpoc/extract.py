"""第 4 步：LLM 抽取（三元组 / 定值表）。

要点：
- 一个 chunk 一次调用，保证 evidence 可溯源；
- 输出走 JSON mode，解析失败有三级兜底（围栏 → 片段 → 逐对象抢救）；
- 每次调用都记录 token 消耗，用于后续规模化预算估算。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .config import Ontology, PromptStore, load_json
from .llm import BaseLLM, parse_json_array
from .parser import Chunk


class Extractor:
    def __init__(self, llm: BaseLLM, onto: Ontology, prompts: PromptStore | None = None,
                 mode: str | None = None, max_chars: int = 20000):
        self.llm = llm
        self.onto = onto
        self.prompts = prompts or PromptStore()
        self.mode = mode or onto.extraction_cfg.get("mode", "single")
        self.max_chars = max_chars
        self.schema_block = json.dumps(load_json("config/setpoint_schema.json"),
                                       ensure_ascii=False, indent=2)

    # ---------------- 三元组 ----------------
    def extract_triples(self, chunks: Iterable[Chunk]) -> list[dict]:
        out = []
        for c in chunks:
            if c.chunk_type in ("heading", "table"):
                continue
            if len(c.text) > self.max_chars:
                print(f"[WARN] chunk 过长已跳过：{c.chunk_id} ({len(c.text)} 字)")
                continue
            prompt = self.prompts.render(
                "triple_extraction.md",
                entity_types_block=self.onto.entity_types_block(),
                relation_types_block=self.onto.relation_types_block(),
                doc_id=c.doc_id,
                section_path=c.section_path,
                chunk_type=c.chunk_type,
                chunk_text=c.text,
            )
            raw = self.llm.complete(prompt, task="triple", key=c.chunk_id)
            for item in parse_json_array(raw):
                if not isinstance(item, dict):
                    continue
                rec = dict(item)
                rec.update({
                    "chunk_id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "section_path": c.section_path,
                    "page": c.page,
                    "security_level": c.security_level,
                })
                out.append(rec)
        return out

    # ---------------- 定值表 ----------------
    def extract_setpoints(self, chunks: Iterable[Chunk]) -> list[dict]:
        out = []
        for c in chunks:
            if c.chunk_type != "table":
                continue
            prompt = self.prompts.render(
                "setpoint_extraction.md",
                schema_block=self.schema_block,
                doc_id=c.doc_id,
                section_path=c.section_path,
                page=c.page if c.page is not None else "未知",
                preceding_text=c.preceding_text or "（无）",
                table_content=c.table_md or c.text,
            )
            raw = self.llm.complete(prompt, task="setpoint", key=c.chunk_id)
            for item in parse_json_array(raw):
                if not isinstance(item, dict):
                    continue
                rec = dict(item)
                src = rec.get("source") or {}
                src.setdefault("doc", c.doc_id)
                src.setdefault("page", c.page)
                src.setdefault("section", c.section_path)
                rec["source"] = src
                rec.update({
                    "chunk_id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "security_level": c.security_level,
                })
                out.append(rec)
        return out
