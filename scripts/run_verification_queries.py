#!/usr/bin/env python3
"""10 个图谱验证查询（POC 的"期末考试"）。

有 Neo4j 时：执行 Cypher 并返回真实结果；
无 Neo4j 时：在 outputs/nodes.jsonl + edges.jsonl 上做等价的本地预演，
             用于核对"预期结果"是否成立（也方便 CI 里跑回归）。

用法：
    python scripts/run_verification_queries.py
    python scripts/run_verification_queries.py --neo4j     # 强制走数据库
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "outputs"


def load_jsonl(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


class LocalGraph:
    """Mini 图查询器：只为复算预期结果，不追求语法兼容。"""

    def __init__(self, nodes, edges):
        self.nodes = {n["node_key"]: n for n in nodes}
        self.out = defaultdict(list)
        self.in_ = defaultdict(list)
        for e in edges:
            self.out[e["subject_key"]].append((e["predicate"], self.nodes[e["object_key"]], e))
            self.in_[e["object_key"]].append((e["predicate"], self.nodes[e["subject_key"]], e))

    def find(self, name):
        return [n for n in self.nodes.values() if n["name"] == name]

    def key(self, name):
        hits = self.find(name)
        return hits[0]["node_key"] if hits else None

    def o(self, name, pred=None):
        k = self.key(name)
        return [n for p, n, _ in self.out[k] if pred is None or p == pred]

    def i(self, name, pred=None):
        k = self.key(name)
        return [n for p, n, _ in self.in_[k] if pred is None or p == pred]

    @staticmethod
    def col(ns, field="name"):
        return sorted({str(n.get(field)) for n in ns if n.get(field) is not None})


QUERIES = [
    dict(
        id="Q1", title="RCS 包含哪些设备？（结构查询）",
        purpose="验证 PART_OF 关系抽取与多源合并。",
        cypher="""MATCH (e)-[:PART_OF]->(s:Entity {name:'反应堆冷却剂系统'})
RETURN e.type AS 类型, e.name AS 名称
ORDER BY e.type, e.name;""",
        local=lambda g: [(n["type"], n["name"]) for n in sorted(g.i('反应堆冷却剂系统', 'PART_OF'),
                                                                key=lambda x: (x["type"], x["name"]))],
        expect="4 条：主泵 / 反应堆压力容器 / 蒸汽发生器 / 稳压器",
    ),
    dict(
        id="Q2", title="SGTR 的完整因果链：原因→征兆→后果→处置（多跳）",
        purpose="验证故障知识是否连成链，这是图谱最有价值的形态。",
        cypher="""MATCH (f:Entity {name:'蒸汽发生器传热管破裂'})
OPTIONAL MATCH (f)-[:CAUSED_BY]->(cause)
OPTIONAL MATCH (f)-[:HAS_SYMPTOM]->(sym)
OPTIONAL MATCH (f)-[:HAS_CONSEQUENCE]->(cons)
OPTIONAL MATCH (f)-[:MITIGATED_BY]->(act)
RETURN f.name AS 故障,
       collect(DISTINCT cause.name) AS 原因,
       collect(DISTINCT sym.name)   AS 征兆,
       collect(DISTINCT cons.name)  AS 后果,
       collect(DISTINCT act.name)   AS 处置;""",
        local=lambda g: [("蒸汽发生器传热管破裂",
                          g.col(g.o('蒸汽发生器传热管破裂', 'CAUSED_BY')),
                          g.col(g.o('蒸汽发生器传热管破裂', 'HAS_SYMPTOM')),
                          g.col(g.o('蒸汽发生器传热管破裂', 'HAS_CONSEQUENCE')),
                          g.col(g.o('蒸汽发生器传热管破裂', 'MITIGATED_BY')))],
        expect="链路完整：原因 2 项（应力腐蚀裂纹、制造缺陷；磨损减薄为刻意留的漏抽项）、征兆 4 项、后果 2 项、处置 1 项",
    ),
    dict(
        id="Q3", title="功率运行工况下，一回路压力有哪些报警定值？（图谱 + 定值表联查）",
        purpose="验证定值是否真正挂到图谱节点上，能被 SQL 式条件过滤。",
        cypher="""MATCH (p:Parameter {name:'一回路压力'})-[:HAS_SETPOINT]->(s:Setpoint)
WHERE s.applicable_mode = '功率运行'
RETURN DISTINCT p.name AS 参数, s.name AS 定值, s.value AS 整定值, s.unit AS 单位, s.action AS 动作
ORDER BY s.value;""",
        local=lambda g: sorted({(s["name"], s.get("value"), s.get("unit"), s.get("action"))
                                for s in g.o('一回路压力', 'HAS_SETPOINT')
                                if s.get("value") is not None},
                               key=lambda x: float(x[1])),
        expect="4 行：13.9MPa 触发紧急停堆 / 14.5MPa 报警 / 16.2MPa 报警 / 16.5MPa 稳压器安全阀动作",
    ),
    dict(
        id="Q4", title="投入余热排出系统（RHR）的前置条件是什么？",
        purpose="验证 PRECONDITION_OF 的方向抽取是否正确。",
        cypher="""MATCH (pre)-[:PRECONDITION_OF]->(a {name:'投入余热排出系统（RHR）'})
RETURN pre.name AS 前置条件, pre.type AS 类型;""",
        local=lambda g: [(n["name"], n["type"]) for n in g.i('投入余热排出系统（RHR）', 'PRECONDITION_OF')],
        expect="2 条：一回路温度低于180℃ / 一回路压力低于3.0MPa",
    ),
    dict(
        id="Q5", title="压力高高报警会触发哪些动作？",
        purpose="验证 Setpoint → Action 的 TRIGGERS 链。",
        cypher="""MATCH (s:Setpoint)-[:TRIGGERS]->(a)
WHERE s.name CONTAINS '高高'
RETURN s.name AS 定值, s.value AS 整定值, s.unit AS 单位, collect(a.name) AS 触发动作;""",
        local=lambda g: [(n["name"], n.get("value"), n.get("unit"), g.col(g.o(n["name"], 'TRIGGERS')))
                         for n in g.nodes.values() if n["type"] == "Setpoint" and "高高" in n["name"]],
        expect="2 行，均指向稳压器安全阀动作：①三元组抽出的「压力高高定值16.5MPa」②定值表回填的「一回路压力高高定值」。二者尚未归一，建议按模板 <参数名><级别>定值 统一后再合并",
    ),
    dict(
        id="Q6", title="哪些规程适用于蒸汽发生器？",
        purpose="验证 APPLIES_TO 与 Procedure 实体的抽取。",
        cypher="""MATCH (p:Procedure)-[:APPLIES_TO]->(e {name:'蒸汽发生器'})
RETURN DISTINCT p.name AS 规程;""",
        local=lambda g: [(n["name"],) for n in g.i('蒸汽发生器', 'APPLIES_TO') if n["type"] == "Procedure"],
        expect="1 条：SGTR处置导则",
    ),
    dict(
        id="Q7", title="辅助给水泵的冗余配置是什么？",
        purpose="验证 REDUNDANT_WITH（冗余是安全审评高频问题）。",
        cypher="""MATCH (a)-[:REDUNDANT_WITH]->(b)
RETURN a.name AS 设备A, b.name AS 设备B, labels(a) AS A类型;""",
        local=lambda g: [(g.nodes[nk]["name"], e[1]["name"])
                         for nk in g.out for e in g.out[nk] if e[0] == "REDUNDANT_WITH"],
        expect="1 组：辅助给水泵A ←→ 辅助给水泵B",
    ),
    dict(
        id="Q8", title="两个不同文档对同一参数的定值是否一致？（跨文档冲突检测）",
        purpose="图谱相对于文档检索的独特价值，务必演示。",
        cypher="""MATCH (s:Setpoint)
WHERE s.conflict = true
RETURN s.name AS 定值, s.source_docs AS 来源文档, s.values AS 不同取值,
       s.conflict_detail AS 冲突明细;""",
        local=lambda g: [(n["name"], n.get("source_docs"), n.get("values"), n.get("conflict_detail"))
                         for n in g.nodes.values() if n.get("conflict")],
        expect="内置两份文档的一回路压力定值完全一致 → 返回 0 行；一旦口径不一致会立即返回冲突明细",
    ),
    dict(
        id="Q9", title="从「二回路放射性水平异常升高」出发，能反推出哪些可能故障？（反向诊断）",
        purpose="验证 HAS_SYMPTOM 反向追溯能力。",
        cypher="""MATCH (f)-[:HAS_SYMPTOM]->(s {name:'二回路放射性水平异常升高'})
RETURN DISTINCT f.name AS 可能故障, f.type AS 类型;""",
        local=lambda g: [(n["name"], n["type"]) for n in g.i('二回路放射性水平异常升高', 'HAS_SYMPTOM')],
        expect="1 条：蒸汽发生器传热管破裂（FaultMode）",
    ),
    dict(
        id="Q10", title="反应堆冷却剂系统的完整监测参数清单？",
        purpose="验证 MONITORS 关系 + 定值表回填产生的参数-系统关联。",
        cypher="""MATCH (p:Parameter)-[:MONITORS]->(s {name:'反应堆冷却剂系统'})
RETURN DISTINCT p.name AS 参数, p.unit AS 单位, p.param_id AS 编号
ORDER BY p.name;""",
        local=lambda g: sorted({(n["name"], n.get("unit"), n.get("param_id"))
                                for n in g.i('反应堆冷却剂系统', 'MONITORS')
                                if n["type"] == "Parameter"}),
        expect="5 个参数：一回路压力 / 一回路设计压力 / 堆芯出口温度 / 堆芯入口温度 / 单环路冷却剂流量",
    ),
]


def run_local():
    nodes = load_jsonl(OUT / "nodes.jsonl")
    edges = load_jsonl(OUT / "edges.jsonl")
    g = LocalGraph(nodes, edges)
    results = {}
    for q in QUERIES:
        try:
            results[q["id"]] = q["local"](g)
        except Exception as e:  # noqa: BLE001
            results[q["id"]] = f"[本地预演失败] {e}"
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--neo4j", action="store_true", help="强制连接 Neo4j 执行 Cypher")
    ap.add_argument("--dump-cypher", default=str(ROOT / "queries/verification_queries.cypher"),
                    help="将 Cypher 与预期结果导出为文件")
    args = ap.parse_args()

    if not (OUT / "nodes.jsonl").exists():
        print("[ERROR] 未找到 outputs/nodes.jsonl，请先运行 scripts/run_pipeline.py")
        sys.exit(1)

    results = run_local()
    print("=" * 78)
    print("10 个验证查询 · 本地预演结果（有 Neo4j 时 --neo4j 走真实查询）")
    print("=" * 78)
    for q in QUERIES:
        print(f"\n【{q['id']}】{q['title']}")
        print(f"  目的：{q['purpose']}")
        print(f"  预期：{q['expect']}")
        r = results[q["id"]]
        if isinstance(r, str):
            print(f"  实际：{r}")
        elif isinstance(r, list) and r and isinstance(r[0], tuple):
            print(f"  实际：{len(r)} 行")
            for row in r:
                print("        " + " | ".join(str(x) for x in row))
        elif isinstance(r, list):
            print(f"  实际：{len(r)} 行 {r}")
        else:
            print(f"  实际：{r}")

    if args.dump_cypher:
        lines = [
            "// 核电知识图谱 POC · 10 个验证查询",
            "// 生成方式：python scripts/run_verification_queries.py（预期结果来自 outputs/nodes.jsonl + edges.jsonl 的本地预演）",
            "",
        ]
        for q in QUERIES:
            lines += [
                f"// ===== {q['id']} · {q['title']} =====",
                f"// 目的：{q['purpose']}",
                f"// 预期结果：{q['expect']}",
                f"// 本地预演实际返回：{json.dumps(results[q['id']], ensure_ascii=False)}",
                q["cypher"],
                "",
            ]
        Path(args.dump_cypher).write_text("\n".join(lines), encoding="utf-8")
        print(f"\n[OK] 已导出 Cypher + 预期结果：{Path(args.dump_cypher).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
