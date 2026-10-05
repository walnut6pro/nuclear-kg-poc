#!/usr/bin/env python3
"""端到端流水线：解析 → 抽取 → 后处理 → 评估 → 图谱导出（可选入库）。

离线演示（不需要任何 API key）：
    python scripts/run_pipeline.py

接真实大模型（DeepSeek 为例）：
    export DEEPSEEK_API_KEY=sk-xxxx
    python scripts/run_pipeline.py --provider deepseek

内网平移（vLLM 本地部署）：
    python scripts/run_pipeline.py --provider vllm_qwen
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kgpoc.config import LLMConfig, Ontology, PromptStore  # noqa: E402
from kgpoc.evaluate import Evaluator  # noqa: E402
from kgpoc.extract import Extractor  # noqa: E402
from kgpoc.graph import GraphBuilder  # noqa: E402
from kgpoc.llm import build_llm  # noqa: E402
from kgpoc.normalize import Normalizer  # noqa: E402
from kgpoc.parser import parse_markdown  # noqa: E402
from kgpoc.postprocess import PostProcessor  # noqa: E402


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def md_report(stats: dict, report: dict, n_chunks: int) -> str:
    t, s = report["triples"], report["setpoints"]
    lines = [
        "# 核电知识图谱 POC · 质量评估报告",
        "",
        f"- 切分 chunk 数：**{n_chunks}**",
        f"- 后处理统计：见下表",
        "",
        "## 一、后处理各环节的贡献与损耗",
        "",
        "| 环节 | 计数 | 说明 |",
        "|---|---:|---|",
        f"| 原始抽取三元组 | {stats['triples_in']} | LLM 直接产出 |",
        f"| Schema 校验丢弃 | {stats['schema_invalid']} | 字段缺失/枚举非法 |",
        f"| domain/range 异常 | {stats['range_violation']} | 关系两端类型错配 |",
        f"| 去重合并 | {stats['duplicates']} | 同一事实被多个 chunk 重复抽到 |",
        f"| NLI 剔除 | {stats['nli_rejected']} | 证据不支持，疑为幻觉 |",
        f"| NLI 修正 | {stats['nli_partially']} | 部分支持，按建议改写宾语 |",
        f"| **最终入图三元组** | **{stats['triples_out']}** |  |",
        f"| 原始定值记录 | {stats['setpoints_in']} |  |",
        f"| 定值 Schema 丢弃 | {stats['setpoint_schema_invalid']} |  |",
        f"| 跨 chunk 合并 | {stats['setpoints_merged']} | 同文档同参数多表合并 |",
        f"| 数值隔离 | {stats['numeric_violations']} | 超量级，已隔离告警行 |",
        f"| 顺序告警 | {stats['ordering_violations']} | 低低<低<正常<高<高高 被破坏 |",
        f"| 跨文档冲突 | {stats['cross_doc_conflicts']} | 同参数不同文档定值不一致 |",
        f"| **最终入图定值记录** | **{stats['setpoints_out']}** |  |",
        "",
        "## 二、对照金标准的整体指标",
        "",
        "| 指标 | 数值 | 目标 | 结论 |",
        "|---|---:|---|---|",
        f"| 三元组 Precision | {t['precision']:.3f} | ≥0.85 | {'达标' if t['precision']>=0.85 else '未达标'} |",
        f"| 三元组 Recall | {t['recall']:.3f} | ≥0.75 | {'达标' if t['recall']>=0.75 else '未达标'} |",
        f"| 三元组 F1 | {t['f1']:.3f} | — | — |",
        f"| 定表字段准确率 | {s['field_accuracy']:.3f} | ≥0.95 | {'达标' if s['field_accuracy']>=0.95 else '未达标'} |",
        f"| 定表行召回率 | {s['row_recall']:.3f} | ≥0.90 | {'达标' if s['row_recall']>=0.90 else '未达标'} |",
        "",
        f"- 金标准三元组 {t['gold_total']} 条 / 抽到 {t['pred_total']} 条 / 命中 {t['tp']} 条",
        f"- 金标准定值 {s['gold_rows']} 行 / 抽到 {s['pred_rows']} 行 / 匹配 {s['matched_rows']} 行",
        "",
        "## 三、按关系类型分桶（找弱项用）",
        "",
        "| 关系 | 金标准 | 抽取 | 命中 | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for b in t["buckets"]:
        lines.append(f"| {b['relation']} | {b['gold']} | {b['pred']} | {b['tp']} | "
                     f"{b['precision']:.3f} | {b['recall']:.3f} | {b['f1']:.3f} |")
    lines += ["", "## 四、待改进案例（召回缺口）", ""]
    for m in t["missed_gold"][:40]:
        lines.append(f"- 漏抽：`{' / '.join(map(str, m))}`")
    if not t["missed_gold"]:
        lines.append("- 无")
    lines += ["", "## 五、待改进案例（误抽）", ""]
    for m in t["false_positive"][:40]:
        lines.append(f"- 误抽：`{' / '.join(map(str, m))}`")
    if not t["false_positive"]:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/sample", help="语料目录（.md）")
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--provider", default=None, help="LLM 提供方：mock/deepseek/openai/vllm_qwen")
    ap.add_argument("--no-nli", action="store_true", help="跳过 NLI 溯源校验（对比用）")
    ap.add_argument("--import-neo4j", action="store_true", help="导出后直接写入 Neo4j")
    args = ap.parse_args()

    outdir = ROOT / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    print("[1/6] 解析与切分 ...")
    src = ROOT / args.input
    files = sorted(src.glob("*.md")) if src.is_dir() else [src]
    chunks = []
    for f in files:
        chunks.extend(parse_markdown(f))
    write_jsonl(outdir / "chunks.jsonl", [c.to_dict() for c in chunks])
    n_table = sum(1 for c in chunks if c.chunk_type == "table")
    print(f"      {len(files)} 份文档 → {len(chunks)} 个 chunk（其中表格 {n_table} 个）")

    print(f"[2/6] 接入 LLM（provider={args.provider or '配置默认'}） ...")
    llm_cfg = LLMConfig.load(args.provider)
    llm = build_llm(llm_cfg)
    onto = Ontology.load()
    prompts = PromptStore()

    print("[3/6] 抽取三元组与定值 ...")
    extractor = Extractor(llm, onto, prompts)
    raw_triples = extractor.extract_triples(chunks)
    raw_setpoints = extractor.extract_setpoints(chunks)
    write_jsonl(outdir / "raw_triples.jsonl", raw_triples)
    write_jsonl(outdir / "raw_setpoints.jsonl", raw_setpoints)
    print(f"      原始三元组 {len(raw_triples)} 条 / 原始定值记录 {len(raw_setpoints)} 条")

    print("[4/6] 后处理（校验 + 归一 + 去重 + NLI） ...")
    pp = PostProcessor(onto, llm, Normalizer(), prompts, enable_nli=not args.no_nli)
    triples = pp.run_triples(raw_triples)
    setpoints = pp.run_setpoints(raw_setpoints)
    write_jsonl(outdir / "triples.jsonl", triples)
    write_jsonl(outdir / "setpoints.jsonl", setpoints)
    write_jsonl(outdir / "quality_audit.jsonl", pp.audit)
    print(f"      最终三元组 {len(triples)} 条 / 定值记录 {len(setpoints)} 条")
    print(f"      NLI 剔除 {pp.stats['nli_rejected']} 条，去重合并 {pp.stats['duplicates']} 条，"
          f"数值隔离 {pp.stats['numeric_violations']} 条")

    print("[5/6] 对照金标准评估 ...")
    ev = Evaluator()
    report = ev.report(triples, setpoints)
    (outdir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "report.md").write_text(md_report(pp.stats, report, len(chunks)), encoding="utf-8")
    t = report["triples"]
    print(f"      三元组 P={t['precision']:.3f} R={t['recall']:.3f} F1={t['f1']:.3f}")
    print(f"      定值表 字段准确率={report['setpoints']['field_accuracy']:.3f} "
          f"行召回={report['setpoints']['row_recall']:.3f}")
    print(f"      验收：{'全部达标' if report['all_pass'] else '存在未达标项，需迭代 prompt'}")

    print("[6/6] 构建图谱并导出 ...")
    gb = GraphBuilder(Normalizer(), pp.stats)
    graph = gb.build(triples, setpoints)
    gb.write_jsonl(graph, args.outdir)
    cypher_path = gb.write_cypher(graph, f"{args.outdir}/graph.cypher")
    print(f"      节点 {len(graph['nodes'])} 个 / 边 {len(graph['edges'])} 条")
    print(f"      Cypher 已生成：{Path(cypher_path).relative_to(ROOT)}")

    if args.import_neo4j:
        try:
            from neo4j import GraphDatabase  # type: ignore
        except ImportError:
            print("[WARN] 未安装 neo4j 驱动，跳过入库。执行：pip install neo4j")
            return
        import os
        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        pwd = os.environ.get("NEO4J_PASSWORD", "password")
        drv = GraphDatabase.driver(uri, auth=(user, pwd))
        with drv.session() as sess:
            for line in Path(cypher_path).read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s and not s.startswith("//"):
                    sess.run(s)
        drv.close()
        print("      已写入 Neo4j")

    print("\n完成。产物目录：" + str(outdir.relative_to(ROOT)))


if __name__ == "__main__":
    main()
