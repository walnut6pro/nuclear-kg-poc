"""配置加载与 prompt 渲染。

设计原则：本体（实体/关系）、schema、LLM 连接全部外置为配置文件，
pipeline 代码里不写死任何领域知识，换项目只改 config/ 即可。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path: str | Path) -> dict:
    with open(ROOT / path if not Path(path).is_absolute() else path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_json(path: str | Path) -> Any:
    p = ROOT / path if not Path(path).is_absolute() else path
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class Ontology:
    raw: dict
    entity_types: dict[str, dict] = field(default_factory=dict)
    relation_types: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str = "config/ontology.yaml") -> "Ontology":
        raw = load_yaml(path)
        onto = cls(raw=raw)
        for e in raw.get("entity_types", []):
            onto.entity_types[e["id"]] = e
        for r in raw.get("relation_types", []):
            onto.relation_types[r["id"]] = r
        return onto

    @property
    def entity_ids(self) -> list[str]:
        return [e["id"] for e in self.raw.get("entity_types", [])]

    @property
    def relation_ids(self) -> list[str]:
        return [r["id"] for r in self.raw.get("relation_types", [])]

    @property
    def validation_rules(self) -> dict:
        return self.raw.get("validation_rules", {})

    @property
    def extraction_cfg(self) -> dict:
        return self.raw.get("extraction", {})

    # ---------- prompt 渲染块 ----------
    def entity_types_block(self) -> str:
        lines = []
        for e in self.raw.get("entity_types", []):
            ex = "、".join(e.get("examples", [])[:3])
            lines.append(f"- {e['id']}（{e['name_cn']}）：{e['desc']} 例：{ex}")
        return "\n".join(lines)

    def relation_types_block(self) -> str:
        lines = []
        for r in self.raw.get("relation_types", []):
            lines.append(f"- {r['id']}（{r.get('name_cn','')}）：{r['definition']}")
            for p in r.get("positive", [])[:2]:
                lines.append(f"    · 正例 {p}")
            for n in r.get("negative", [])[:1]:
                lines.append(f"    · 反例 {n}")
        return "\n".join(lines)


class PromptStore:
    def __init__(self, dir_path: str = "prompts"):
        self.dir = ROOT / dir_path

    def get(self, name: str) -> str:
        with open(self.dir / name, "r", encoding="utf-8") as f:
            return f.read()

    def render(self, name: str, **kwargs) -> str:
        tpl = self.get(name)
        # 只做简单占位符替换，避免依赖 jinja；模板里的 {{ }} 转义后会在本步还原
        out = tpl
        for k, v in kwargs.items():
            out = out.replace("{" + k + "}", str(v))
        return out


@dataclass
class LLMConfig:
    raw: dict
    name: str
    cfg: dict

    @classmethod
    def load(cls, provider: str | None = None, path: str = "config/llm.yaml") -> "LLMConfig":
        raw = load_yaml(path)
        p = provider or raw.get("default_provider", "mock")
        if p not in raw.get("providers", {}):
            raise KeyError(f"未定义的 provider: {p}，可用：{list(raw['providers'].keys())}")
        return cls(raw=raw, name=p, cfg=raw["providers"][p] | raw.get("runtime", {}))
