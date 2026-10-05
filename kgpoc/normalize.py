"""实体归一化。

POC 阶段用人工术语表（data/terminology/glossary.csv）做确定式归一；
规模化阶段在此处叠加 embedding(BGE-M3) 相似度召回 + LLM 判定（接口已预留）。
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT


def clean_surface(s: str) -> str:
    """去掉括号注释、空白与首尾标点，用于比较；不改变展示用原名。"""
    s = re.sub(r"[（(][^（()）]*[）)]", "", str(s)).strip()
    s = re.sub(r"\s+", "", s)
    return s.strip("，,。;；:：、")


@dataclass
class Normalizer:
    glossary_path: str = "data/terminology/glossary.csv"
    alias2canon: dict = field(default_factory=dict)
    canon2type: dict = field(default_factory=dict)
    canon2aliases: dict = field(default_factory=dict)

    def __post_init__(self):
        p = ROOT / self.glossary_path
        if not p.exists():
            return
        with io.open(p, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                canon = (row.get("canonical") or "").strip()
                if not canon:
                    continue
                self.canon2type[canon] = (row.get("type") or "").strip()
                alist = self.canon2aliases.setdefault(canon, [])
                for a in (row.get("aliases") or "").split("|"):
                    a = a.strip()
                    if not a:
                        continue
                    alist.append(a)
                    self.alias2canon[clean_surface(a)] = canon
                self.alias2canon.setdefault(clean_surface(canon), canon)
        # 长别名优先，避免短别名先替换导致碎片化
        self.alias2canon = dict(
            sorted(self.alias2canon.items(), key=lambda kv: (-len(kv[0]), kv[1]))
        )

    def canonical(self, surface: str) -> str:
        key = clean_surface(surface)
        return self.alias2canon.get(key, str(surface).strip())

    def type_of(self, surface: str) -> str:
        return self.canon2type.get(self.canonical(surface), "")

    def key(self, surface: str) -> str:
        """用于比较/去重的最终键：规范化名 + 去空白。"""
        canon = self.canonical(surface)
        return clean_surface(canon) or clean_surface(surface)
