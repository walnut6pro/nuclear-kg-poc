"""第 5 步：后处理——决定最终数据质量的一层。

三元组流水线：
  ① Schema 校验  →  ② 实体归一化  →  ③ domain/range 软校验  →
  ④ 去重        →  ⑤ NLI 溯源校验（杀幻觉）
定值流水线：
  ① Schema 校验  →  ② 跨 chunk 合并  →  ③ 数值/逻辑校验（隔离异常值）→
  ④ 跨文档一致性（冲突检测）
每个环节都会往 stats 里写诊断计数，方便按环节定位问题。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import ROOT, Ontology, load_yaml
from .llm import BaseLLM, parse_json_array
from .normalize import Normalizer, clean_surface
from .config import PromptStore


TRIPLE_REQUIRED = ["subject", "subject_type", "predicate", "object", "object_type", "evidence"]
LEVELS_ORDER = ["低低", "低", "高", "高高"]


class PostProcessor:
    def __init__(self, onto: Ontology, llm: BaseLLM, normalizer: Normalizer | None = None,
                 prompts: PromptStore | None = None, enable_nli: bool = True):
        self.onto = onto
        self.llm = llm
        self.norm = normalizer or Normalizer()
        self.prompts = prompts or PromptStore()
        self.enable_nli = enable_nli
        self.stats: dict = {
            "triples_in": 0, "schema_invalid": 0, "range_violation": 0,
            "duplicates": 0, "nli_rejected": 0, "nli_partially": 0, "triples_out": 0,
            "setpoints_in": 0, "setpoints_merged": 0, "setpoint_schema_invalid": 0,
            "numeric_violations": 0, "ordering_violations": 0,
            "cross_doc_conflicts": 0, "setpoints_out": 0,
        }
        self.audit: list[dict] = []
        constraints_file = ROOT / "config/relation_constraints.yaml"
        self.constraints = load_yaml(constraints_file)["constraints"] if constraints_file.exists() else {}

    # ======================= 三元组 =======================
    def _log(self, stage: str, item: dict, reason: str, action: str):
        self.audit.append({"stage": stage, "reason": reason, "action": action,
                           "subject": item.get("subject"), "predicate": item.get("predicate"),
                           "object": item.get("object"),
                           "param_name": item.get("param_name")})

    def schema_check_triples(self, items: list[dict]) -> list[dict]:
        valid = []
        for it in items:
            self.stats["triples_in"] += 1
            missing = [k for k in TRIPLE_REQUIRED if not str(it.get(k, "")).strip()]
            if missing:
                self.stats["schema_invalid"] += 1
                self._log("schema", it, f"缺字段：{','.join(missing)}", "drop")
                continue
            if it["predicate"] not in self.onto.relation_ids:
                self.stats["schema_invalid"] += 1
                self._log("schema", it, f"未知关系：{it['predicate']}", "drop")
                continue
            for role in ("subject_type", "object_type"):
                if it[role] not in self.onto.entity_ids:
                    self.stats["schema_invalid"] += 1
                    self._log("schema", it, f"未知实体类型：{it[role]}", "drop")
                    break
            else:
                it.setdefault("confidence", "medium")
                valid.append(it)
        return valid

    def range_check_triples(self, items: list[dict]) -> list[dict]:
        kept = []
        for it in items:
            c = self.constraints.get(it["predicate"])
            bad = False
            if c:
                if c.get("domain") and it["subject_type"] not in c["domain"]:
                    bad = True
                if c.get("range") and it["object_type"] not in c["range"]:
                    bad = True
            if bad and c.get("hard"):
                self.stats["range_violation"] += 1
                self._log("range", it, "domain/range 硬约束不满足", "drop")
                continue
            if bad:
                self.stats["range_violation"] += 1
                it["range_warning"] = True
                self._log("range", it, "domain/range 软约束告警", "keep+warn")
            kept.append(it)
        return kept

    def normalize_triples(self, items: list[dict]) -> list[dict]:
        for it in items:
            it["subject_raw"] = it["subject"]
            it["object_raw"] = it["object"]
            it["subject"] = self.norm.canonical(it["subject"])
            it["object"] = self.norm.canonical(it["object"])
            it["subject_key"] = self.norm.key(it["subject"])
            it["object_key"] = self.norm.key(it["object"])
        return items

    def dedup_triples(self, items: list[dict]) -> list[dict]:
        seen: dict[tuple, dict] = {}
        for it in items:
            key = (it["subject_key"], it["predicate"], it["object_key"])
            if key in seen:
                self.stats["duplicates"] += 1
                prev = seen[key]
                # 保留 evidence 更完整的一条；同时登记另一条来源
                if len(it.get("evidence", "")) > len(prev.get("evidence", "")):
                    prev["evidence"], it["evidence"] = it["evidence"], prev["evidence"]
                prev.setdefault("also_seen_in", []).append(it.get("chunk_id"))
                self._log("dedup", it, "与已保留条目重复", "merge")
                continue
            seen[key] = it
        return list(seen.values())

    def nli_verify(self, items: list[dict]) -> list[dict]:
        kept = []
        for it in items:
            key = f"{it.get('chunk_id')}|{it['subject_key']}|{it['predicate']}|{it['object_key']}"
            prompt = self.prompts.render(
                "nli_verification.md",
                subject=it["subject"], predicate=it["predicate"], object=it["object"],
                evidence=it["evidence"],
            )
            raw = self.llm.complete(prompt, task="nli", key=key)
            try:
                obj = parse_json_array(raw)
                verdict_obj = obj[0] if isinstance(obj, list) and obj else {}
            except Exception:  # noqa: BLE001
                verdict_obj = {}
            verdict = str(verdict_obj.get("verdict", "SUPPORTED")).upper()
            it["nli_verdict"] = verdict
            it["nli_reason"] = verdict_obj.get("reason", "")
            if verdict == "NOT_SUPPORTED":
                self.stats["nli_rejected"] += 1
                self._log("nli", it, f"NLI 判定不支持：{verdict_obj.get('reason','')}", "drop")
                continue
            if verdict == "PARTIALLY":
                self.stats["nli_partially"] += 1
                sug = verdict_obj.get("suggestion")
                if sug:
                    it["object"] = sug
                    it["object_key"] = self.norm.key(sug)
                self._log("nli", it, f"NLI 部分支持，已修正宾语：{sug}", "rewrite")
            kept.append(it)
        return kept

    def run_triples(self, items: list[dict]) -> list[dict]:
        items = self.schema_check_triples(items)
        items = self.range_check_triples(items)
        items = self.normalize_triples(items)
        items = self.dedup_triples(items)
        if self.enable_nli:
            items = self.nli_verify(items)
        self.stats["triples_out"] = len(items)
        return items

    # ======================= 定值表 =======================
    def schema_check_setpoints(self, items: list[dict]) -> list[dict]:
        keep = []
        for it in items:
            self.stats["setpoints_in"] += 1
            ok = True
            if not str(it.get("param_name", "")).strip():
                ok = False
            if not str(it.get("unit", "")).strip():
                ok = False
            src = it.get("source") or {}
            if not isinstance(src, dict) or not src.get("doc"):
                ok = False
            for a in it.get("alarms") or []:
                if not isinstance(a, dict) or a.get("level") not in LEVELS_ORDER + ["其他"]:
                    ok = False
                if not isinstance(a, dict) or a.get("value") is None:
                    ok = False
            if not ok:
                self.stats["setpoint_schema_invalid"] += 1
                self._log("schema_setpoint", it, "不符合定值 Schema", "drop")
                continue
            it.setdefault("version", "v1.0")
            it.setdefault("confidence", 0.9)
            keep.append(it)
        return keep

    @staticmethod
    def _key_sp(it: dict) -> tuple:
        src = it.get("source") or {}
        return (clean_surface(it.get("param_name", "")), clean_surface(it.get("unit", "")), src.get("doc", ""))

    def merge_setpoints(self, items: list[dict]) -> list[dict]:
        """同一文档内同一参数的多条记录合并（不同文档保留独立记录，用于冲突检测）。"""
        buckets: dict[tuple, dict] = {}
        for it in items:
            k = self._key_sp(it)
            if k not in buckets:
                buckets[k] = json.loads(json.dumps(it))
                continue
            base = buckets[k]
            self.stats["setpoints_merged"] += 1
            for field_name in ("param_id", "param_name_en", "system", "equipment", "applicable_mode"):
                if not base.get(field_name) and it.get(field_name):
                    base[field_name] = it[field_name]
            nr_base, nr_new = base.get("normal_range") or {}, it.get("normal_range") or {}
            if not nr_base.get("min") and nr_new.get("min"):
                nr_base["min"] = nr_new["min"]
            if not nr_base.get("max") and nr_new.get("max"):
                nr_base["max"] = nr_new["max"]
            if nr_base:
                base["normal_range"] = nr_base
            existing = {(a.get("level"), str(a.get("value"))) for a in base.get("alarms") or []}
            for a in it.get("alarms") or []:
                if (a.get("level"), str(a.get("value"))) not in existing:
                    base.setdefault("alarms", []).append(a)
                    existing.add((a.get("level"), str(a.get("value"))))
            base["confidence"] = max(base.get("confidence", 0), it.get("confidence", 0))
        return list(buckets.values())

    def numeric_check(self, items: list[dict]) -> list[dict]:
        rules = self.onto.validation_rules.get("magnitude_checks", [])
        for it in items:
            name = it.get("param_name", "")
            unit = it.get("unit", "")
            notes = []
            surviving = []
            for a in it.get("alarms") or []:
                keep = True
                for r in rules:
                    if r["param_keyword"] in name and unit == r["unit"]:
                        try:
                            v = float(re.sub(r"[^0-9.\-]", "", str(a.get("value"))))
                        except ValueError:
                            keep = False
                            break
                        if not (r["plausible_min"] <= v <= r["plausible_max"]):
                            keep = False
                            notes.append(f"报警[{a.get('level')}] 数值 {v} {unit} 超出量级区间 "
                                         f"[{r['plausible_min']}, {r['plausible_max']}]，已隔离")
                            self.stats["numeric_violations"] += 1
                            self._log("numeric", it, notes[-1], "quarantine")
                            break
                if keep:
                    surviving.append(a)
            it["alarms"] = surviving
            # 逻辑顺序校验：低低 < 低 < 正常 < 高 < 高高
            order = self.onto.validation_rules.get("setpoint_ordering", {}).get("order", LEVELS_ORDER)
            vals = []
            for lv in order:
                for a in surviving:
                    if a.get("level") == lv:
                        try:
                            vals.append((lv, float(re.sub(r"[^0-9.\-]", "", str(a.get("value"))))))
                        except ValueError:
                            pass
            seq = [v for _, v in vals]
            if seq != sorted(seq):
                self.stats["ordering_violations"] += 1
                msg = "报警定值顺序异常：应为 " + " < ".join(order) + f"，实际 {vals}"
                notes.append(msg)
                self._log("ordering", it, msg, "warn")
            if notes:
                it.setdefault("validation_notes", []).extend(notes)
        return items

    def cross_doc_conflict(self, items: list[dict]) -> list[dict]:
        """同一参数在不同文档中是否给出冲突定值——图谱相对原文检索的独特价值。"""
        by_param: dict[tuple, list[dict]] = {}
        for it in items:
            by_param.setdefault((clean_surface(it.get("param_name", "")), clean_surface(it.get("unit", ""))), []).append(it)
        for key, group in by_param.items():
            if len(group) < 2:
                continue
            for lv in LEVELS_ORDER:
                vals = {}
                for it in group:
                    for a in it.get("alarms") or []:
                        if a.get("level") == lv:
                            vals[it["source"]["doc"]] = a.get("value")
                if len(set(map(str, vals.values()))) > 1:
                    self.stats["cross_doc_conflicts"] += 1
                    msg = f"参数[{key[0]}] 的 {lv} 定值在不同文档中不一致：{vals}"
                    for it in group:
                        it.setdefault("validation_notes", []).append(msg)
                    self._log("conflict", group[0], msg, "flag")
        return items

    def run_setpoints(self, items: list[dict]) -> list[dict]:
        items = self.schema_check_setpoints(items)
        items = self.merge_setpoints(items)
        items = self.numeric_check(items)
        items = self.cross_doc_conflict(items)
        self.stats["setpoints_out"] = len(items)
        return items
