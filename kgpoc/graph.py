"""第 7 步：图谱构建——三元组/定值 → Neo4j。

产物：
  1. outputs/nodes.jsonl   图谱节点（含 aliases / type / security_level）
  2. outputs/edges.jsonl   图谱边（含 source 溯源、confidence）
  3. outputs/graph.cypher  可直接粘贴到 Neo4j Browser 执行 / 或由 scripts/import_neo4j.py 导入
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .config import ROOT
from .normalize import Normalizer


def _esc(s) -> str:
    """Cypher 字符串转义。"""
    if s is None:
        return ""
    s = str(s).replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
    return s.replace("\n", " ")


class GraphBuilder:
    def __init__(self, normalizer: Normalizer | None = None, stats: dict | None = None):
        self.norm = normalizer or Normalizer()
        self.stats = stats or {}

    def build(self, triples: list[dict], setpoints: list[dict]) -> dict:
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        def ensure(name: str, etype: str, aliases: list | None = None, **kw):
            key = self.norm.key(name)
            if key not in nodes:
                nodes[key] = {
                    "node_key": key, "name": name, "type": etype,
                    "aliases": sorted(set((aliases or []) + [name])),
                    "security_level": "公开", **kw,
                }
            else:
                nodes[key]["aliases"] = sorted(set(nodes[key]["aliases"] + (aliases or []) + [name]))
                for k, v in kw.items():
                    if k not in nodes[key] or nodes[key][k] in (None, ""):
                        nodes[key][k] = v
                    elif nodes[key][k] != v and k in ("source_doc", "unit"):
                        # 同一参数可能出现在多个文档/单位：并列保留，便于发现跨界不一致
                        old = nodes[key][k]
                        nodes[key][k] = f"{old}; {v}" if str(v) not in str(old) else old
            return key

        for t in triples:
            ensure(t["subject"], t["subject_type"], [t.get("subject_raw")])
            ensure(t["object"], t["object_type"], [t.get("object_raw")])
            edges.append({
                "subject": t["subject"], "subject_key": self.norm.key(t["subject"]),
                "predicate": t["predicate"],
                "object": t["object"], "object_key": self.norm.key(t["object"]),
                "confidence": t.get("confidence"),
                "nli_verdict": t.get("nli_verdict"),
                "evidence": t.get("evidence", ""),
                "source": t.get("doc_id"),
                "section_path": t.get("section_path"),
                "page": t.get("page"),
                "security_level": t.get("security_level", "公开"),
            })

        # 定值/参数作为实体挂到图谱上
        for sp in setpoints:
            doc = (sp.get("source") or {}).get("doc")
            pkey = ensure(sp["param_name"], "Parameter", [sp.get("param_name_en")],
                          unit=sp.get("unit"), param_id=sp.get("param_id"), source_doc=doc)
            if sp.get("system"):
                skey = ensure(sp["system"], "System")
                edges.append({"subject": sp["param_name"], "subject_key": pkey,
                              "predicate": "MONITORS", "object": sp["system"],
                              "object_key": skey, "confidence": "high",
                              "nli_verdict": "RULE", "evidence": "（由定值表参数归属自动补全）",
                              "source": (sp.get("source") or {}).get("doc"),
                              "section_path": (sp.get("source") or {}).get("section"),
                              "page": (sp.get("source") or {}).get("page"),
                              "security_level": "公开"})
            if sp.get("equipment"):
                ekey = ensure(sp["equipment"], "Equipment")
                edges.append({"subject": sp["param_name"], "subject_key": pkey,
                              "predicate": "MONITORS", "object": sp["equipment"],
                              "object_key": ekey, "confidence": "medium",
                              "nli_verdict": "RULE", "evidence": "（由定值表设备归属自动补全）",
                              "source": (sp.get("source") or {}).get("doc"),
                              "section_path": (sp.get("source") or {}).get("section"),
                              "page": (sp.get("source") or {}).get("page"),
                              "security_level": "公开"})
            for a in sp.get("alarms") or []:
                sp_name = f"{sp['param_name']}{a.get('level')}定值"
                skey = ensure(sp_name, "Setpoint", source_doc=doc)
                node = nodes[skey]
                # 同一"参数+级别"定值可能来自多个文档：按文档分别登记，便于冲突检测
                docs = node.setdefault("source_docs", [])
                vals = node.setdefault("values", [])
                if doc not in docs:
                    docs.append(doc)
                    vals.append(a.get("value"))
                node["value"] = a.get("value")
                node["unit"] = sp.get("unit")
                node["action"] = a.get("action")
                node["applicable_mode"] = a.get("applicable_mode") or sp.get("applicable_mode")
                if len({str(v) for v in vals}) > 1:
                    node["conflict"] = True
                    node["conflict_detail"] = "; ".join(f"{d}: {v}" for d, v in zip(docs, vals))
                edges.append({"subject": sp["param_name"], "subject_key": pkey,
                              "predicate": "HAS_SETPOINT", "object": sp_name, "object_key": skey,
                              "confidence": "high", "nli_verdict": "RULE",
                              "evidence": (sp.get("source") or {}).get("evidence", ""),
                              "source": (sp.get("source") or {}).get("doc"),
                              "section_path": (sp.get("source") or {}).get("section"),
                              "page": (sp.get("source") or {}).get("page"),
                              "security_level": "公开"})
                if a.get("action"):
                    akey = ensure(a["action"], "Action")
                    edges.append({"subject": sp_name, "subject_key": skey,
                                  "predicate": "TRIGGERS", "object": a["action"], "object_key": akey,
                                  "confidence": "high", "nli_verdict": "RULE",
                                  "evidence": (sp.get("source") or {}).get("evidence", ""),
                                  "source": (sp.get("source") or {}).get("doc"),
                                  "section_path": (sp.get("source") or {}).get("section"),
                                  "page": (sp.get("source") or {}).get("page"),
                                  "security_level": "公开"})
        return {"nodes": list(nodes.values()), "edges": edges}

    # ---------------- 导出 ----------------
    def write_jsonl(self, graph: dict, outdir: str = "outputs"):
        d = ROOT / outdir
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "nodes.jsonl", "w", encoding="utf-8") as f:
            for n in graph["nodes"]:
                f.write(json.dumps(n, ensure_ascii=False) + "\n")
        with open(d / "edges.jsonl", "w", encoding="utf-8") as f:
            for e in graph["edges"]:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def write_cypher(self, graph: dict, path: str = "outputs/graph.cypher") -> str:
        lines = [
            "// 核电知识图谱 POC —— 自动生成，请勿手工编辑",
            "// 用法：Neo4j Browser 直接粘贴执行，或用 scripts/import_neo4j.py 批量导入",
            "",
            "// ---- 0. 清空（演示环境用；生产请改为 MERGE 增量） ----",
            "MATCH (n) DETACH DELETE n;",
            "",
            "// ---- 1. 约束 ----",
            "CREATE CONSTRAINT kg_poc_node_key IF NOT EXISTS FOR (n:Entity) REQUIRE n.node_key IS UNIQUE;",
            "",
            "// ---- 2. 节点 ----",
        ]
        for n in graph["nodes"]:
            props = {
                "node_key": n["node_key"], "name": n["name"], "type": n["type"],
                "aliases": n.get("aliases", []), "unit": n.get("unit"),
                "value": n.get("value"), "param_id": n.get("param_id"),
                "source_doc": n.get("source_doc"), "action": n.get("action"),
                "applicable_mode": n.get("applicable_mode"),
                "source_docs": n.get("source_docs"), "values": n.get("values"),
                "conflict": n.get("conflict"), "conflict_detail": n.get("conflict_detail"),
                "security_level": n.get("security_level", "公开"),
            }
            props = {k: v for k, v in props.items() if v not in (None, [])}
            kv = ", ".join(
                (f"{k}: {json.dumps(v, ensure_ascii=False)}" if isinstance(v, list)
                 else f"{k}: '{_esc(v)}'")
                for k, v in props.items()
            )
            lines.append(f"CREATE (:Entity:{n['type']} {{{kv}}});")
        lines.append("")
        lines.append("// ---- 3. 关系 ----")
        for e in graph["edges"]:
            kv = ", ".join([
                f"confidence: '{_esc(e.get('confidence'))}'",
                f"nli: '{_esc(e.get('nli_verdict'))}'",
                f"source: '{_esc(e.get('source'))}'",
                f"section: '{_esc(e.get('section_path'))}'",
                f"evidence: '{_esc(e.get('evidence'))}'",
                f"security_level: '{_esc(e.get('security_level'))}'",
            ])
            lines.append(
                f"MATCH (a:Entity {{node_key:'{_esc(e['subject_key'])}'}}), "
                f"(b:Entity {{node_key:'{_esc(e['object_key'])}'}}) "
                f"CREATE (a)-[:{e['predicate']} {{{kv}}}]->(b);"
            )
        p = ROOT / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines), encoding="utf-8")
        return str(p)
