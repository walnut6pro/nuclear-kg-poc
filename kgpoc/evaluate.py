"""第 6 步：质量闭环——对照人工金标准算 Precision / Recall / F1，并按关系类型分桶。

为什么必须分桶：整体指标是平均值，会把弱项关系藏起来。
分桶之后才能针对性改 prompt / 关系定义，而不是盲目调模型。
"""
from __future__ import annotations

import io
import json
from collections import defaultdict
from pathlib import Path

from .normalize import Normalizer, clean_surface

TRIPLE_SCALAR_FIELDS = []          # 三元组只做 (s,p,o) 匹配，不做字段级比对
SETPOINT_SCALAR_FIELDS = ["param_id", "param_name_en", "system", "equipment",
                          "unit", "applicable_mode", "version"]


def load_jsonl(path: str | Path) -> list[dict]:
    out = []
    for line in io.open(path, encoding="utf-8").read().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


class Evaluator:
    def __init__(self, gold_triples: str = "data/gold/gold_triples.jsonl",
                 gold_setpoints: str = "data/gold/gold_setpoints.jsonl",
                 normalizer: Normalizer | None = None):
        self.norm = normalizer or Normalizer()
        self.gold_t = load_jsonl(gold_triples)
        self.gold_s = load_jsonl(gold_setpoints)

    # ---------------- 三元组 ----------------
    @staticmethod
    def tkey(s: str, p: str, o: str, norm: Normalizer) -> tuple:
        return (norm.key(s), p, norm.key(o))

    def eval_triples(self, preds: list[dict]) -> dict:
        gold = {self.tkey(g["subject"], g["predicate"], g["object"], self.norm) for g in self.gold_t}
        gold_list = list(gold)
        pred_keys = [self.tkey(p["subject"], p["predicate"], p["object"], self.norm) for p in preds]
        pred_set = set(pred_keys)

        tp = gold & pred_set
        precision = len(tp) / len(pred_set) if pred_set else 0.0
        recall = len(tp) / len(gold) if gold else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

        # 按关系类型分桶
        gold_by_rel = defaultdict(set)
        for g in self.gold_t:
            gold_by_rel[g["predicate"]].add(
                self.tkey(g["subject"], g["predicate"], g["object"], self.norm))
        pred_by_rel = defaultdict(set)
        for k in pred_set:
            pred_by_rel[k[1]].add(k)
        buckets = []
        for rel in sorted(set(gold_by_rel) | set(pred_by_rel)):
            tp_r = gold_by_rel.get(rel, set()) & pred_by_rel.get(rel, set())
            p_r = len(tp_r) / len(pred_by_rel[rel]) if pred_by_rel.get(rel) else 0.0
            r_r = len(tp_r) / len(gold_by_rel[rel]) if gold_by_rel.get(rel) else 0.0
            buckets.append({
                "relation": rel, "gold": len(gold_by_rel.get(rel, set())),
                "pred": len(pred_by_rel.get(rel, set())), "tp": len(tp_r),
                "precision": round(p_r, 4), "recall": round(r_r, 4),
                "f1": round(2 * p_r * r_r / (p_r + r_r), 4) if (p_r + r_r) else 0.0,
            })

        missed = [list(k) for k in sorted(gold - pred_set)]
        extra = [list(k) for k in pred_set - gold]
        return {
            "gold_total": len(gold), "pred_total": len(pred_set), "tp": len(tp),
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "buckets": buckets, "missed_gold": missed, "false_positive": extra,
        }

    # ---------------- 定值与参数表 ----------------
    @staticmethod
    def skey(it: dict) -> tuple:
        src = it.get("source") or {}
        return (clean_surface(it.get("param_name", "")), clean_surface(it.get("unit", "")),
                src.get("doc", ""))

    def eval_setpoints(self, preds: list[dict]) -> dict:
        gold_map = {self.skey(g): g for g in self.gold_s}
        pred_map = {self.skey(p): p for p in preds}
        matched_keys = set(gold_map) & set(pred_map)
        row_recall = len(matched_keys) / len(gold_map) if gold_map else 0.0
        row_precision = len(matched_keys) / len(pred_map) if pred_map else 0.0

        total_fields = correct_fields = 0
        details = []
        for k in sorted(gold_map):
            g, p = gold_map[k], pred_map[k]
            row_total = row_correct = 0
            diffs = []
            for f in SETPOINT_SCALAR_FIELDS:
                gv, pv = g.get(f), p.get(f)
                row_total += 1
                if self._eq(gv, pv):
                    row_correct += 1
                else:
                    diffs.append(f"{f}: 期望 {gv!r} / 实际 {pv!r}")
            for sub in ("min", "max"):
                gv = (g.get("normal_range") or {}).get(sub)
                pv = (p.get("normal_range") or {}).get(sub)
                row_total += 1
                if self._eq(gv, pv):
                    row_correct += 1
                else:
                    diffs.append(f"normal_range.{sub}: 期望 {gv!r} / 实际 {pv!r}")
            g_alarms = {a.get("level"): a for a in g.get("alarms") or []}
            p_alarms = {a.get("level"): a for a in p.get("alarms") or []}
            for lv in set(g_alarms) | set(p_alarms):
                ga, pa = g_alarms.get(lv), p_alarms.get(lv)
                for f, weight in (("value", 1), ("action", 1)):
                    gv = ga.get(f) if ga else None
                    pv = pa.get(f) if pa else None
                    row_total += 1
                    if self._eq(gv, pv) and not (ga is None or pa is None):
                        row_correct += 1
                    else:
                        diffs.append(f"alarms[{lv}].{f}: 期望 {gv!r} / 实际 {pv!r}")
            total_fields += row_total
            correct_fields += row_correct
            details.append({"key": "|".join(map(str, k)), "row_accuracy": round(row_correct / row_total, 4),
                            "diffs": diffs})
        return {
            "gold_rows": len(gold_map), "pred_rows": len(pred_map), "matched_rows": len(matched_keys),
            "row_recall": round(row_recall, 4), "row_precision": round(row_precision, 4),
            "field_accuracy": round(correct_fields / total_fields, 4) if total_fields else 0.0,
            "total_fields": total_fields, "correct_fields": correct_fields,
            "details": details,
        }

    @staticmethod
    def _eq(a, b) -> bool:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return abs(float(a) - float(b)) < 1e-6
        return (a or None) == (b or None)

    # ---------------- 汇总 ----------------
    def report(self, triples: list[dict], setpoints: list[dict]) -> dict:
        t = self.eval_triples(triples)
        s = self.eval_setpoints(setpoints)
        targets = {
            "三元组准确率 >= 0.85": {"value": t["precision"], "pass": t["precision"] >= 0.85},
            "三元组召回率 >= 0.75": {"value": t["recall"], "pass": t["recall"] >= 0.75},
            "定表字段准确率 >= 0.95": {"value": s["field_accuracy"], "pass": s["field_accuracy"] >= 0.95},
            "定表行召回率 >= 0.90": {"value": s["row_recall"], "pass": s["row_recall"] >= 0.90},
        }
        return {"triples": t, "setpoints": s, "targets": targets,
                "all_pass": all(v["pass"] for v in targets.values())}
