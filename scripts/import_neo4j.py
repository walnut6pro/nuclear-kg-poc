#!/usr/bin/env python3
"""把 outputs/nodes.jsonl + edges.jsonl 增量写入 Neo4j（幂等，可重复执行）。

依赖：pip install neo4j
配置：环境变量 NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD（默认 bolt://localhost:7687 neo4j/password）

用法：
    python scripts/import_neo4j.py                  # 导入
    python scripts/import_neo4j.py --wipe           # 先清空再导入（演示环境用）
    python scripts/import_neo4j.py --dry-run        # 只打印将执行的语句
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "outputs"


def load_jsonl(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def build_statements(nodes, edges, wipe=False):
    stmts = []
    if wipe:
        stmts.append(("MATCH (n) DETACH DELETE n", {}))
    stmts.append((
        "CREATE CONSTRAINT kg_poc_node_key IF NOT EXISTS "
        "FOR (n:Entity) REQUIRE n.node_key IS UNIQUE", {}))
    for n in nodes:
        props = {k: v for k, v in n.items() if v not in (None, [])}
        stmts.append((
            "MERGE (x:Entity {node_key: $node_key}) "
            "SET x += $props, x:%s" % n.get("type", "Entity"),
            {"node_key": n["node_key"], "props": props}))
    for e in edges:
        stmts.append((
            "MATCH (a:Entity {node_key: $sk}), (b:Entity {node_key: $ok}) "
            f"MERGE (a)-[r:{e['predicate']}]->(b) "
            "SET r.confidence = $confidence, r.source = $source, r.section = $section, "
            "    r.evidence = $evidence, r.nli = $nli, r.security_level = $security_level",
            {"sk": e["subject_key"], "ok": e["object_key"],
             "confidence": e.get("confidence"), "source": e.get("source"),
             "section": e.get("section_path"), "evidence": e.get("evidence", ""),
             "nli": e.get("nli_verdict"), "security_level": e.get("security_level", "公开")}))
    return stmts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", default=str(OUT / "nodes.jsonl"))
    ap.add_argument("--edges", default=str(OUT / "edges.jsonl"))
    ap.add_argument("--wipe", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=500)
    args = ap.parse_args()

    nodes = load_jsonl(Path(args.nodes))
    edges = load_jsonl(Path(args.edges))
    stmts = build_statements(nodes, edges, args.wipe)
    print(f"节点 {len(nodes)} 个，边 {len(edges)} 条，共 {len(stmts)} 条语句")

    if args.dry_run:
        for q, p in stmts[:5]:
            print(f"{q}\n  params={json.dumps(p, ensure_ascii=False)[:160]}")
        print("...")
        return

    try:
        from neo4j import GraphDatabase  # type: ignore
    except ImportError:
        print("[ERROR] 缺少 neo4j 驱动。执行：pip install neo4j")
        sys.exit(1)

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pwd = os.environ.get("NEO4J_PASSWORD", "password")
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    try:
        driver.verify_connectivity()
        with driver.session() as sess:
            for i in range(0, len(stmts), args.batch):
                chunk = stmts[i : i + args.batch]
                sess.execute_write(lambda tx, c=chunk: [tx.run(q, **p) for q, p in c])
                print(f"  已写入 {min(i + args.batch, len(stmts))}/{len(stmts)}")
        print("[OK] 导入完成。下一步：执行 queries/verification_queries.cypher 做期末考试。")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
