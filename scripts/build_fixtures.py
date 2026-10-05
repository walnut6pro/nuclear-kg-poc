"""生成离线演示用的预置抽取结果（data/fixtures/mock_extractions.json）。

为什么要这个文件：
  在没有 API key 的内网/离线环境下，MockLLM 直接返回这里的"模拟 LLM 输出"，
  使解析 → 抽取 → 后处理 → 评估 → 入图全链路可端到端跑通。

刻意植入的"脏数据"（用于验证后处理各环节真的工作）：
  1. C003 幻觉三元组     → 应由 NLI 溯源校验剔除
  2. C003 同 chunk 重复   → 应由去重环节合并
  3. C005 推理过度三元组  → 证据不支持，应由 NLI 剔除
  4. C007 跨 chunk 重复   → 应由去重环节合并（且需别名归一才认得出）
  5. BC007 多余三元组     → 证据支持但金标准没有，属于 FP，压低 Precision
  6. 定值 165 MPa        → 超量级，应由数值校验隔离
  7. 故意漏抽 5 条金标准  → 压低 Recall，演示确有改进空间

用法：python scripts/build_fixtures.py
"""
from __future__ import annotations

import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD_T = ROOT / "data/gold/gold_triples.jsonl"
GOLD_S = ROOT / "data/gold/gold_setpoints.jsonl"
OUT = ROOT / "data/fixtures/mock_extractions.json"

# 每个 chunk 预期产出哪些金标准（按 gold_id），未列出的 chunk 返回空
CHUNK_TO_GOLD = {
    "DOC_A_RCS_SG_overview::C003": ["GT-001", "GT-002", "GT-003", "GT-004", "GT-005",
                                    "GT-006", "GT-007", "GT-003"],          # GT-003 重复一次
    "DOC_A_RCS_SG_overview::C004": ["GT-008", "GT-009"],
    "DOC_A_RCS_SG_overview::C005": [],
    "DOC_A_RCS_SG_overview::C007": ["GT-010", "GT-011", "GT-003"],
    "DOC_A_RCS_SG_overview::C008": ["GT-012", "GT-013", "GT-014", "GT-015", "GT-017", "GT-018"],
    "DOC_A_RCS_SG_overview::C009": ["GT-019", "GT-020", "GT-021", "GT-022", "GT-023"],
    "DOC_A_RCS_SG_overview::C010": ["GT-024", "GT-026"],
    "DOC_A_RCS_SG_overview::C011": ["GT-027", "GT-029"],
    "DOC_A_RCS_SG_overview::C012": ["GT-030"],
    "DOC_A_RCS_SG_overview::C015": ["GT-031", "GT-032"],
    "DOC_A_RCS_SG_overview::C018": ["GT-033", "GT-034"],
    "DOC_B_SGTR_procedure::C003": ["GT-035", "GT-036"],
    "DOC_B_SGTR_procedure::C005": ["GT-037", "GT-039", "GT-040"],
    "DOC_B_SGTR_procedure::C006": ["GT-041", "GT-042"],
    "DOC_B_SGTR_procedure::C007": ["GT-043"],
    "DOC_B_SGTR_procedure::C008": ["GT-044", "GT-045", "GT-046"],
}

# 额外植入的脏数据
EXTRA_NOISE = {
    # 幻觉：证据只说"设备包含"，没说主泵的功能 —— NLI 应判 NOT_SUPPORTED
    "DOC_A_RCS_SG_overview::C003": [{
        "subject": "主泵", "subject_type": "Equipment",
        "predicate": "HAS_FUNCTION", "object": "维持一回路压力", "object_type": "Action",
        "evidence": "RCS 的主要设备包括反应堆压力容器、蒸汽发生器、反应堆冷却剂泵（以下简称主泵）和稳压器。",
        "confidence": "low",
    }],
    # 推理过度：证据里根本没有"反应堆冷却剂系统"这个词 —— NLI 应判 NOT_SUPPORTED
    "DOC_A_RCS_SG_overview::C005": [{
        "subject": "堆芯出口温度", "subject_type": "Parameter",
        "predicate": "MONITORS", "object": "反应堆冷却剂系统", "object_type": "System",
        "evidence": "堆芯出口冷却剂平均温度约为 310 ℃，堆芯入口约为 290 ℃。",
        "confidence": "low",
    }],
    # 证据支持但金标准未收录 —— 算 FP
    "DOC_B_SGTR_procedure::C007": [{
        "subject": "停止上充泵补水", "subject_type": "Action",
        "predicate": "STEP_OF", "object": "SGTR处置导则", "object_type": "Procedure",
        "evidence": "步骤 3 的操作内容包括：稳定一回路压力使其略高于故障蒸汽发生器二次侧压力，停止上充泵补水，最终实现一、二回路压力平衡，终止泄漏。",
        "confidence": "medium",
    }],
}

# 定值：每个表格 chunk 产出哪些金标准
TABLE_TO_GOLD = {
    "DOC_A_RCS_SG_overview::C006": ["GS-003", "GS-004", "GS-005", "GS-006", "GS-001"],
    "DOC_A_RCS_SG_overview::C014": ["GS-001"],
    "DOC_A_RCS_SG_overview::C017": ["GS-002"],
    "DOC_B_SGTR_procedure::C010": ["GS-007"],
}


def load_jsonl(p: Path) -> dict:
    out = {}
    for line in io.open(p, encoding="utf-8").read().splitlines():
        if line.strip():
            o = json.loads(line)
            out[o["gold_id"]] = o
    return out


def strip_gold(o: dict, keep_optional: bool = True):
    o = dict(o)
    o.pop("gold_id", None)
    o.pop("source_doc", None)
    o.pop("section", None)
    if not keep_optional:
        pass
    return o


def main():
    gold_t = load_jsonl(GOLD_T)
    gold_s = load_jsonl(GOLD_S)

    triple_bucket = {}
    for chunk_id, gids in CHUNK_TO_GOLD.items():
        items = [strip_gold(gold_t[g]) for g in gids if g in gold_t]
        for n in EXTRA_NOISE.get(chunk_id, []):
            items.append(n)
        triple_bucket[chunk_id] = items

    # 补 massive — 未列出的 chunk 也显式写 [], 便于阅读 fixture
    setpoint_bucket = {}
    for chunk_id, gids in TABLE_TO_GOLD.items():
        items = []
        for g in gids:
            o = strip_gold(gold_s[g])
            if chunk_id == "DOC_A_RCS_SG_overview::C006" and g == "GS-001":
                # 从"设计参数表"抽到的运行范围只给 normal_range，模拟 LLM 只看到部分信息
                o = {"param_id": "RCS-PT-001", "param_name": "一回路压力",
                     "param_name_en": "RCS Pressure", "system": "反应堆冷却剂系统",
                     "equipment": "稳压器", "unit": "MPa",
                     "normal_range": {"min": 15.2, "max": 15.7}, "alarms": [],
                     "applicable_mode": "功率运行",
                     "source": {"doc": "DOC_A_RCS_SG_overview.md", "page": None,
                                "section": "第三章 反应堆冷却剂系统 > 3.2 主要参数",
                                "evidence": "| 一回路运行压力正常范围 | 15.2~15.7 | MPa |"},
                     "version": "v1.0", "confidence": 0.95}
            if chunk_id == "DOC_A_RCS_SG_overview::C014" and g == "GS-001":
                # 故意植入数值错误：16.5 → 165，应由量级校验隔离
                o = json.loads(json.dumps(o))
                for a in o["alarms"]:
                    if a["level"] == "高高":
                        a["value"] = 165
                o["confidence"] = 0.72
            items.append(o)
        setpoint_bucket[chunk_id] = items

    payload = {"triple": triple_bucket, "setpoint": setpoint_bucket, "nli": {}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 已生成 {OUT.relative_to(ROOT)}")
    print(f"     triple chunks : {len(triple_bucket)} 条, 共 {sum(len(v) for v in triple_bucket.values())} 个三元组")
    print(f"     setpoint chunks: {len(setpoint_bucket)} 条, 共 {sum(len(v) for v in setpoint_bucket.values())} 条定值记录")


if __name__ == "__main__":
    main()
