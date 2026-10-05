#!/usr/bin/env python3
"""生成 Word / Excel 交付物。

产物：
  docs/核电知识图谱POC实施方案.docx
  docs/关系类型抽取定义手册.docx
  docs/金标准标注规范.docx
  docs/10个验证查询与预期结果.docx
  docs/金标准标注模板.xlsx

依赖：pip install python-docx openpyxl
用法：python scripts/build_docs.py
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Cm

DOCS = ROOT / "docs"
DOCS.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 工具
def new_doc(title: str, subtitle: str = "") -> Document:
    doc = Document()
    # 正文默认中文字体
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    for s in ("Heading 1", "Heading 2", "Heading 3"):
        st = doc.styles[s]
        st.font.color.rgb = RGBColor(0x1F, 0x3B, 0x73)
        st.font.name = "微软雅黑"
        st.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.4)
    h = doc.add_heading(title, level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if subtitle:
        p = doc.add_paragraph(subtitle)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.runs[0].font.size = Pt(10)
        p.runs[0].font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    return doc


def add_table(doc, header: list, rows: list, widths=None, font_size=9):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Light Grid Accent 1"
    hdr = t.rows[0].cells
    for i, txt in enumerate(header):
        hdr[i].text = str(txt)
        for p in hdr[i].paragraphs:
            for r in p.runs:
                r.font.size = Pt(font_size)
                r.font.bold = True
    for row in rows:
        cells = t.add_row().cells
        for i, txt in enumerate(row):
            cells[i].text = "" if txt is None else str(txt)
            for p in cells[i].paragraphs:
                for r in p.runs:
                    r.font.size = Pt(font_size)
    if widths:
        for r in t.rows:
            for i, w in enumerate(widths):
                r.cells[i].width = Cm(w)
    doc.add_paragraph()
    return t


def bullets(doc, items, style="List Bullet"):
    for it in items:
        doc.add_paragraph(it, style=style)


def code_block(doc, text: str):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.name = "Consolas"
    run.font.size = Pt(8.5)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
    return p


# ---------------------------------------------------------------- 数据
def load_ontology():
    from kgpoc.config import Ontology
    return Ontology.load()


def load_gold():
    t = [json.loads(l) for l in io.open(ROOT / "data/gold/gold_triples.jsonl", encoding="utf-8") if l.strip()]
    s = [json.loads(l) for l in io.open(ROOT / "data/gold/gold_setpoints.jsonl", encoding="utf-8") if l.strip()]
    return t, s


def load_glossary():
    with io.open(ROOT / "data/terminology/glossary.csv", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_metrics():
    p = ROOT / "outputs/report.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ================================================================ 文档 1
def build_plan_doc(onto, metrics):
    doc = new_doc("核电领域「知识三元组 + 参数定值表」POC 实施方案",
                  "面向公开资料验证的流水线设计 · 可平移至内网生产环境")
    gold_t, gold_s = load_gold()
    m = metrics or {}

    doc.add_heading("一、背景、目标与验收标准", level=1)
    doc.add_heading("1.1 要解决的问题", level=2)
    bullets(doc, [
        "核电技术文档（系统设计手册、运行规程、定值清单）数量大、口径杂、跨文档引用多，全文检索难以回答“某故障的全部因果链”“某参数在两种工况下的定值差异”这类结构化问题。",
        "知识图谱把散落在叙述文本与表格中的事实抽成统一的（主语—关系—宾语）三元组和参数定值记录，使跨文档一致性校验、反向诊断、多跳追溯成为可能。",
        "本 POC 的目标不是产出真实数据，而是验证：在给定文档形态下，LLM 抽取 + 规则后处理能否稳定达到可用的质量水平。",
    ])

    doc.add_heading("1.2 合规前提（务必先做）", level=2)
    p = doc.add_paragraph()
    p.add_run("核电文档的保密管理通常非常严格，把文档导出到外网这个动作本身就可能有合规风险，即使文档本身不是涉密级别。"
              "正式启动前须与文档所有方 / 保密部门确认。").font.color.rgb = RGBColor(0xB0, 0x30, 0x20)
    doc.add_paragraph("更稳妥的做法：POC 用公开资料替代真实文档。流程、prompt、质检逻辑完全一样；公开替代品包括：")
    add_table(doc, ["类型", "推荐来源", "用途"], [
        ["大学教材", "《核电厂系统与设备》类公开教材（西安交大、清华版）", "长篇叙述性文本的三元组抽取"],
        ["公开标准", "GB/T 系列（如 GB/T 12727）等国标 / 行标", "规程逻辑 + 参数限值表"],
        ["国际报告", "IAEA 公开技术报告（NS-G 系列，官网免费下载）", "补充国际口径的表述多样性"],
        ["公开章节", "核电厂官网的系统介绍、FSAR 公开章节", "真实工程表述风格的样本"],
    ], widths=[2.5, 7.5, 6])

    doc.add_heading("1.3 量化验收标准", level=2)
    rows = [["三元组抽取质量", "对金标准集：准确率 P ≥ 85%，召回率 R ≥ 75%", "基于 NLI 校验 + 人工核对"],
            ["定值表抽取质量", "字段级准确率 ≥ 95%，行召回率 ≥ 90%", "逐字段比对"],
            ["图谱可用性", "10 个典型问题查询 ≥ 7 个能正确回答", "期末考"],
            ["可平移性", "流水线不依赖特定文档格式，换文档只需重跑", "配置驱动"]]
    add_table(doc, ["目标", "量化标准", "说明"], rows, widths=[3.2, 7.0, 5.8])
    if m:
        t = m["triples"]
        doc.add_paragraph(
            f"内置演示数据上的实跑结果：三元组 P={t['precision']:.3f} / R={t['recall']:.3f} / F1={t['f1']:.3f}；"
            f"定值表字段准确率={m['setpoints']['field_accuracy']:.3f}、行召回率={m['setpoints']['row_recall']:.3f}；"
            f"10 个验证查询全部返回预期结果。（详见 outputs/report.md）")

    doc.add_heading("二、整体架构", level=1)
    add_table(doc, ["步骤", "环节", "关键产物"], [
        ["①", "文档准备：3~5 份资料 + 1 份人工标注金标准", "金标准集、术语表 v1"],
        ["②", "解析切分：结构化 chunk，表格独立成块", "chunks.jsonl"],
        ["③", "本体设计：实体类型 + 关系类型 + 定值 schema", "ontology.yaml"],
        ["④", "LLM 抽取：JSON 约束输出，一次一个 chunk", "raw_triples / raw_setpoints"],
        ["⑤", "后处理：Schema 校验 + 归一 + 数值校验 + NLI", "triples / setpoints"],
        ["⑥", "质量闭环：金标准评估 + 分桶分析 + 人工抽检", "report.md"],
        ["⑦", "图谱构建：JSONL → Neo4j + 定值表入库 + 验证查询", "nodes / edges / graph.cypher"],
    ], widths=[1.2, 8.0, 6.8])

    doc.add_heading("三、数据准备与金标准", level=1)
    doc.add_paragraph("选 3~5 份文档，覆盖三种不同“文档性格”，验证才有代表性：")
    add_table(doc, ["文档角色", "典型公开替代品", "验证什么"], [
        ["系统描述类（长篇叙述）", "教材某一章（如蒸汽发生器 / 一回路系统）", "叙述文本中的三元组抽取"],
        ["规程 / 导则类（步骤 + 条件）", "运行导则或国标的操作部分", "流程类关系：触发、前置、执行"],
        ["参数 / 定值类（表格密集）", "国标限值表、教材参数表", "表格抽取 + 正文定值融合"],
    ], widths=[4.2, 6.0, 5.8])
    doc.add_paragraph(
        f"金标准：从其中 1 份文档选 2~3 章（5000~10000 字）人工全量标注，本工程内置 {len(gold_t)} 条三元组 + "
        f"{len(gold_s)} 条定值记录作为起步集。这一步不能省——它是唯一的质量标尺。标注口径见《金标准标注规范》。")

    doc.add_heading("四、本体设计（POC 核心交付物）", level=1)
    doc.add_heading("4.1 实体类型（12 类）", level=2)
    add_table(doc, ["类型", "英文标识", "示例", "可视化配色"],
              [[e["name_cn"], e["id"], "、".join(e.get("examples", [])[:3]), e.get("color", "—")]
               for e in onto.raw["entity_types"]], widths=[2.6, 2.4, 7.0, 4.0])
    doc.add_heading("4.2 关系类型（16 种）", level=2)
    add_table(doc, ["关系", "中文名", "定义摘要"],
              [[r["id"], r.get("name_cn", ""), r["definition"][:60] + ("…" if len(r["definition"]) > 60 else "")]
               for r in onto.raw["relation_types"]], widths=[3.2, 2.2, 10.6])
    doc.add_paragraph("每种关系的完整定义、正反例与易混淆点见《关系类型抽取定义手册》。")
    doc.add_heading("4.3 定值表 schema", level=2)
    code_block(doc, """{
  "param_id": "RCS-PT-001",        // 内部编号：系统代号-参数代号-三位序号
  "param_name": "一回路压力",
  "param_name_en": "RCS Pressure",
  "system": "反应堆冷却剂系统",
  "equipment": "稳压器",
  "unit": "MPa",
  "normal_range": {"min": 15.2, "max": 15.7},
  "alarms": [
    {"level": "低低", "value": 13.9, "action": "紧急停堆"},
    {"level": "高",   "value": 16.2, "action": "报警"},
    {"level": "高高", "value": 16.5, "action": "安全阀动作"}
  ],
  "applicable_mode": "功率运行",
  "source": {"doc": "xxx.pdf", "page": 45, "section": "3.2.1", "evidence": "原文逐字片段"},
  "version": "v1.0",
  "confidence": 0.95
}""")

    doc.add_heading("五、解析与切分", level=1)
    bullets(doc, [
        "工具：MinerU（中文 PDF 表格还原最好）→ Markdown；Word 用 pandoc。",
        "按标题层级切分，每个 chunk 带完整标题路径，如：[第三章 蒸汽发生器 > 3.2 主要参数 > 3.2.1 设计压力]。",
        "表格永远独立成 chunk，不与正文混合，并附上表格前的说明文字作为上下文。",
        "chunk 长度 800~1500 字，超出按段落二次切分。",
        "元数据：doc_id / page / section_path / chunk_type / security_level。",
    ])

    doc.add_heading("六、LLM 抽取", level=1)
    doc.add_heading("6.1 工程要点", level=2)
    bullets(doc, [
        "用支持 JSON mode / structured output 的 API（DeepSeek、GPT-4o 都支持），避免解析失败。",
        "temperature = 0（或极低），保证同一段文本重复运行结果可复现。",
        "一次只喂一个 chunk，不合并，保证 evidence 可溯源到具体章节。",
        "每条三元组必须给出逐字摘录的 evidence —— 这是后面 NLI 校验能起作用的前提。",
        "16 种关系一次给全效果尚可；召回率上不去时改两遍式：第一遍只抽实体，第二遍针对已知实体对抽关系。",
    ])
    doc.add_heading("6.2 提示词的三个关键约束", level=2)
    add_table(doc, ["约束", "写法", "解决什么问题"], [
        ["只抽明确陈述", "“只抽取文本明确陈述的事实，不做领域推断”", "抑制模型用核电常识补全原文没写的关系"],
        ["必须给证据", "“每条三元组必须给出原文逐字片段 evidence”", "使 NLI 校验与人工复核可行"],
        ["不确定就标 low", "“无法确定的关系标注 confidence: low”", "把风险显式化，便于后处理分级剔除"],
    ], widths=[3.0, 6.4, 6.6])

    doc.add_heading("七、后处理（决定数据质量的一层）", level=1)
    code_block(doc, """原始抽取结果
  ├─ ① Schema 校验：JSON 格式、类型枚举合法性 → 不合法的丢弃/修复
  ├─ ② 实体归一化：术语表映射 + embedding 相似度候选 → LLM 判定是否合并
  ├─ ③ 数值校验：单位合法性、量级合理性、低低<低<正常<高<高高
  ├─ ④ 去重：精确三元组去重；(主语,谓语) 相同但宾语不同的标记复核
  └─ ⑤ NLI 溯源校验：LLM 判断 "evidence 是否支持该三元组" → 不支持的剔除""")
    add_table(doc, ["环节", "本工程实现", "典型收益"], [
        ["Schema 校验", "kgpoc/postprocess.py :: schema_check_triples", "过滤字段缺失、谓词越界、类型非法的垃圾输出"],
        ["实体归一化", "kgpoc/normalize.py（术语表驱动）+ 预留 embedding 接口", "SG / 蒸发器 / 蒸汽发生器 合并为一个节点"],
        ["domain/range 校验", "config/relation_constraints.yaml", "抓住“蒸汽发生器 PART_OF 安全壳”这类方向错位"],
        ["去重", "按 (主语key, 关系, 宾语key) 合并，保留最完整 evidence", "同一事实被多个章节重复抽到"],
        ["数值校验", "config/ontology.yaml :: validation_rules", "拦下 165 MPa 这种量级错误"],
        ["NLI 溯源校验", "prompts/nli_verification.md", "专杀幻觉：证据不支持即剔除"],
    ], widths=[2.6, 6.2, 7.2])

    doc.add_heading("八、质量闭环与迭代", level=1)
    add_table(doc, ["指标", "计算方式", "怎么改"], [
        ["Precision", "抽取正确的三元组 / 总抽取数", "收紧 prompt 的“不得推断”条款；加强 NLI 口径"],
        ["Recall", "抽取正确的三元组 / 金标准总数", "改两遍式抽取；给该关系补充正反例"],
        ["分桶分析", "按关系类型分别算 P/R", "找出弱项关系，针对性改定义而非盲目换模型"],
        ["定值字段准确率", "正确字段数 / 总字段数", "逐字段比对，重点看单位换算与缺失"],
    ], widths=[2.6, 5.6, 7.8])
    doc.add_paragraph("迭代方式：跑金标准 → 分桶看弱项 → 修改关系定义 / prompt → 重跑 → 直到达标。"
                      "同时每批次抽 5% 人工 review，重点看 confidence=low 的样本。")

    doc.add_heading("九、图谱构建与下游验证", level=1)
    bullets(doc, [
        "三元组 JSONL 转成 Cypher 导入 Neo4j Community（免费、自带可视化）。",
        "定值作为 JSONL 存档，同时把参数 / 定值实体挂到图谱节点上，实现“图谱 + 定值表”联查。",
        "10 个验证查询作为期末考，其中跨文档冲突检测最能体现图谱相对原文检索的增量价值，务必演示。",
    ])
    code_block(doc, """CREATE (e:Equipment {name:"蒸汽发生器", aliases:["SG","Steam Generator"]})
CREATE (s:System {name:"反应堆冷却剂系统", aliases:["RCS"]})
CREATE (e)-[:PART_OF {source:"教材p.45", confidence:"high"}]->(s)""")

    doc.add_heading("十、技术栈清单", level=1)
    add_table(doc, ["环节", "工具", "备注"], [
        ["解析", "MinerU", "免费开源，中文 PDF 表格还原最好"],
        ["LLM", "DeepSeek-V3 / GPT-4o", "JSON mode，temperature=0"],
        ["Embedding", "BGE-M3", "实体归一化用（规模化阶段接入）"],
        ["图数据库", "Neo4j Community", "自带可视化，docker compose 已备"],
        ["编排", "纯 Python 脚本", "POC 不需要 LangChain 等框架，越简单越好排查"],
        ["版本管理", "git 管理 prompt + schema", "prompt 也是代码"],
    ], widths=[2.4, 4.4, 8.2])

    doc.add_heading("十一、四周执行计划", level=1)
    add_table(doc, ["周次", "任务", "产出"], [
        ["W1", "文档收集、解析跑通、人工标注金标准", f"金标准集（{len(gold_t)} 条三元组 + {len(gold_s)} 条定值）、术语表 v1"],
        ["W2", "本体定稿、抽取 prompt 开发、后处理脚本", "可运行的完整 pipeline"],
        ["W3", "全量抽取、质量校验、按分桶结果迭代 prompt", "达标的数据集（预计 2000~5000 三元组）"],
        ["W4", "Neo4j 入库、10 个验证查询、评估报告、demo 展示", "POC 结项材料"],
    ], widths=[1.6, 6.6, 7.8])

    doc.add_heading("十二、往真实场景平移要留的接口", level=1)
    bullets(doc, [
        "LLM 可替换：调用统一封装为 OpenAI 兼容接口，内网换成 vLLM 部署的本地模型只改 endpoint。",
        "本体可扩展：真实文档会逼你增加实体类型（逻辑图、仪控信号、电缆），schema 用配置文件管理而非写死在 prompt 里。",
        "保密分级打标：元数据里预留 security_level 字段，从第一天就养成习惯。",
        "规模化成本预估：POC 跑通后记录“每份文档 → 抽取 token 消耗”，方便估算几百份文档的预算（本工程自动写入 outputs/token_usage.jsonl）。",
        "检索方案：图谱适合关系型问题；纯原文问答仍应保留 RAG 路线，两者互补而不是替代。",
    ])

    doc.add_heading("十三、主要风险与对策", level=1)
    add_table(doc, ["风险", "影响", "对策"], [
        ["文档导出合规", "项目无法启动 / 违规", "用公开资料做 POC；真实数据在内网跑"],
        ["关系方向抽反", "图谱问答全错", "domain/range 约束校验 + 关系定义里写清正反例"],
        ["模型用常识补全", "准确率虚高但不可信", "强制 evidence + NLI 校验 + 人工抽检"],
        ["术语口径不一致", "同一实体分裂成多个节点", "术语表 + 实体归一化 + 定期人工维护"],
        ["表格跨页/合并单元格", "定值抽取缺行", "MinerU 表格还原 + 每行必须有 evidence 溯源"],
        ["指标虚高（金标准泄露）", "上线后质量崩塌", "金标准独立于 prompt 迭代；另留一份未参与调优的测试集"],
    ], widths=[3.0, 4.4, 7.6])

    doc.add_heading("附录：本工程的交付物清单", level=1)
    add_table(doc, ["文件", "说明"], [
        ["nuclear_kg_poc/", "端到端可运行流水线（离线即可 demo）"],
        ["docs/核电知识图谱POC实施方案.docx", "本文档"],
        ["docs/关系类型抽取定义手册.docx", "16 种关系的完整抽取定义与正反例"],
        ["docs/金标准标注规范.docx", "标注口径说明"],
        ["docs/金标准标注模板.xlsx", "标注模板 + 示例数据 + 术语表"],
        ["docs/10个验证查询与预期结果.docx", "期末考题与答案"],
        ["queries/verification_queries.cypher", "可直接执行的 Cypher"],
        ["outputs/report.md", "最近一次运行的质量评估报告"],
    ], widths=[6.4, 8.6])

    path = DOCS / "核电知识图谱POC实施方案.docx"
    doc.save(path)
    return path


# ================================================================ 文档 2
REL_EXTRA = {
    "PART_OF": dict(pit="不要把空间位置当作组成；不要把“包含的对象清单”写成反向关系。",
                    extra_pos=["(管板, PART_OF, 蒸汽发生器)"], extra_neg=["(反应堆冷却剂系统, PART_OF, 蒸汽发生器)"]),
    "LOCATED_IN": dict(pit="宾语是构筑物/房间/厂房等空间，不是系统。",
                       extra_pos=["(主泵, LOCATED_IN, 反应堆厂房)"], extra_neg=["(稳压器, LOCATED_IN, 一回路)"]),
    "HAS_FUNCTION": dict(pit="宾语必须是原文明确写出的功能描述；不要把自己总结的功能写进去。",
                         extra_pos=["(辅助给水系统, HAS_FUNCTION, 满足事故工况下的给水需求)"],
                         extra_neg=["(主泵, HAS_FUNCTION, 维持一回路压力)"]),
    "MONITORS": dict(pit="主语必须是 Parameter，不能是仪表/传感器本体（仪表本身另作实体）",
                     extra_pos=["(蒸汽发生器液位, MONITORS, 蒸汽发生器)"],
                     extra_neg=["(蒸汽发生器液位, MONITORS, 功率运行)"]),
    "HAS_SETPOINT": dict(pit="定值必须能指向具体数值；只说“设有定值”而无数值时不抽，或抽到参数级占位。",
                         extra_pos=["(蒸汽发生器液位, HAS_SETPOINT, 液位低低定值)"],
                         extra_neg=["(蒸汽发生器液位, HAS_SETPOINT, 报警)"]),
    "TRIGGERS": dict(pit="区分“触发报警”与“触发保护动作”，同一数值可触发多个动作，按 action 拆/合并。",
                     extra_pos=["(液位低低定值, TRIGGERS, 投入辅助给水)"],
                     extra_neg=["(稳压器安全阀动作, TRIGGERS, 压力高高定值)"]),
    "OCCURS_IN": dict(pit="主语必须是 FaultMode；部位在设备上时宾语用 Equipment，在零件上用 Component。",
                      extra_pos=["(泵密封泄漏, OCCURS_IN, 主泵)"],
                      extra_neg=["(SGTR处置导则, OCCURS_IN, 蒸汽发生器)"]),
    "CAUSED_BY": dict(pit="机理类词（裂纹、老化、磨损）都是原因；不要把“由XX导致”写成反向。",
                      extra_pos=["(泵密封泄漏, CAUSED_BY, 密封磨损)"],
                      extra_neg=["(应力腐蚀裂纹, CAUSED_BY, 传热管破裂)"]),
    "HAS_SYMPTOM": dict(pit="征兆是可观测现象；后果是进一步发展的影响，两者不可互换。",
                        extra_pos=["(SGTR, HAS_SYMPTOM, 稳压器液位降低)"],
                        extra_neg=["(SGTR, HAS_SYMPTOM, 放射性释放)"]),
    "HAS_CONSEQUENCE": dict(pit="必须写递进后的影响，不要把“泄漏”本身既当后果又当征兆重复标注（可分别标注，但要各自有证据）。",
                            extra_pos=["(SGTR, HAS_CONSEQUENCE, 放射性释放)"],
                            extra_neg=["(SGTR, HAS_CONSEQUENCE, 应力腐蚀裂纹)"]),
    "MITIGATED_BY": dict(pit="宾语可以是动作、也可以是规程/系统；一句话中出现“通过XX处置”即成立。",
                         extra_pos=["(SGTR, MITIGATED_BY, SGTR处置导则)"],
                         extra_neg=["(隔离故障蒸汽发生器, MITIGATED_BY, SGTR)"]),
    "PRECONDITION_OF": dict(pit="方向最容易错：条件在前，动作在后。注意“禁止…”“方可…”这类否定/许可句式的转换。",
                            extra_pos=["(完成故障蒸汽发生器隔离, PRECONDITION_OF, 一回路降压操作)"],
                            extra_neg=["(投入余热排出系统, PRECONDITION_OF, 一回路温度低于180℃)"]),
    "STEP_OF": dict(pit="只有当规程明确编号/列步骤时才抽；不要从“应考虑…”这类建议性语句抽步骤。",
                    extra_pos=["(投入稳压器喷淋阀, STEP_OF, SGTR处置导则)"],
                    extra_neg=["(核电厂运行, STEP_OF, SGTR处置导则)"]),
    "APPLIES_TO": dict(pit="主语只能是规程/文档/定值；读者看到“适用于…”句式的方向别写反。",
                       extra_pos=["(SGTR处置导则, APPLIES_TO, 功率运行)"],
                       extra_neg=["(蒸汽发生器, APPLIES_TO, SGTR处置导则)"]),
    "REDUNDANT_WITH": dict(pit="必须功能等价且可互相替代；“并列配置”不等于“冗余”。",
                           extra_pos=["(应急柴油发电机A, REDUNDANT_WITH, 应急柴油发电机B)"],
                           extra_neg=["(主泵, REDUNDANT_WITH, 蒸汽发生器)"]),
    "DEFINED_IN": dict(pit="用于溯源：定值/术语/技术事实 → 出处文档；不要把设备/系统的归属写成 DEFINED_IN。",
                       extra_pos=["(液位低低定值, DEFINED_IN, 压水堆核电厂系统与设备)"],
                       extra_neg=["(蒸汽发生器, DEFINED_IN, SGTR处置导则)"]),
}


def build_relation_doc(onto):
    doc = new_doc("关系类型抽取定义手册", "16 种关系的判定口径 · 正反例 · 易混淆点")
    doc.add_paragraph(
        "使用方式：标注人员先读“判定要点”，再看正例与反例；prompt 调优时优先从这里找歧义来源。"
        "分桶评估发现某个关系 P/R 偏低时，回到该关系的定义条款做针对性修订，而不是笼统地换模型。")

    from kgpoc.config import load_yaml
    cons = load_yaml("config/relation_constraints.yaml")["constraints"]
    gold_t, _ = load_gold()
    gold_stat = {}
    for g in gold_t:
        gold_stat[g["predicate"]] = gold_stat.get(g["predicate"], 0) + 1

    doc.add_heading("一、关系总表", level=1)
    add_table(doc, ["关系", "中文名", "主语类型（domain）", "宾语类型（range）", "硬约束", "金标准条数"],
              [[r["id"], r.get("name_cn", ""), "、".join(cons.get(r["id"], {}).get("domain", [])),
                "、".join(cons.get(r["id"], {}).get("range", [])),
                "是" if cons.get(r["id"], {}).get("hard") else "否", gold_stat.get(r["id"], 0)]
               for r in onto.raw["relation_types"]], widths=[2.6, 2.0, 3.6, 3.8, 1.4, 1.8])

    doc.add_heading("二、逐个关系的定义", level=1)
    for i, r in enumerate(onto.raw["relation_types"], 1):
        ex = REL_EXTRA.get(r["id"], {})
        doc.add_heading(f"2.{i} {r['id']}（{r.get('name_cn','')}）", level=2)
        doc.add_paragraph(f"【定义】{r['definition']}")
        doc.add_paragraph("【正例】")
        bullets(doc, r.get("positive", []) + ex.get("extra_pos", []))
        doc.add_paragraph("【反例】")
        bullets(doc, r.get("negative", []) + ex.get("extra_neg", []))
        if ex.get("pit"):
            p = doc.add_paragraph()
            p.add_run("【易错点】").bold = True
            p.add_run(ex["pit"])

    doc.add_heading("三、跨关系的判定顺序建议", level=1)
    doc.add_paragraph("遇到一句话可能同时命中多个关系时，按下列顺序判断，避免重复与错配：")
    bullets(doc, [
        "先看是不是“构成关系”：能回答“它是什么的一部分” → PART_OF；只是“它在哪儿” → LOCATED_IN。",
        "再看是不是“知识描述”：讲功能 → HAS_FUNCTION；讲监测 → MONITORS；讲阈值 → HAS_SETPOINT。",
        "再看是不是“故障链”：现象 → HAS_SYMPTOM；机理 → CAUSED_BY；影响 → HAS_CONSEQUENCE；处置 → MITIGATED_BY。",
        "再看是不是“流程”：条件在句首且有“前提/方可/禁止” → PRECONDITION_OF；规程编号步骤 → STEP_OF。",
        "最后补“溯源”：讲明出处的 → DEFINED_IN；讲适用对象的 → APPLIES_TO。",
    ])

    doc.add_heading("四、统一的 evidence 与置信度规则", level=1)
    bullets(doc, [
        "evidence 必须是原文逐字片段（10~120 字），不得改写、不得跨句拼接。",
        "一句话同时支撑多条三元组时，允许多条共用同一 evidence。",
        "需要跨句合并才能得到的事实，evidence 取包含该事实的最小连续片段，confidence 记为 medium。",
        "high：原文有明确句式标记（包括/由…组成/功能是/原因是/导致/禁止）；medium：需合并两句话；low：隐含、指代不清。",
    ])

    path = DOCS / "关系类型抽取定义手册.docx"
    doc.save(path)
    return path


# ================================================================ 文档 3
def build_annotation_doc(onto):
    doc = new_doc("金标准标注规范", "什么叫“标对了”——标注前必读")
    gold_t, gold_s = load_gold()

    doc.add_heading("第一章 目的、范围与角色", level=1)
    doc.add_paragraph(
        "本规范用于统一核电文档「知识三元组 + 参数定值」人工金标准的标注口径。"
        "金标准有两个用途：一是评估 LLM 抽取结果（算 Precision / Recall），二是作为 prompt 迭代的参照。"
        "因此它的第一要求是口径稳定——同一个标注员前后一致，不同标注员之间可比。")
    add_table(doc, ["术语", "含义"], [
        ["金标准（Gold Standard）", "人工标注的、作为唯一质量标尺的正确答案集"],
        ["标注单元", "一条三元组或一条定值记录（含 evidence 与来源）"],
        ["样本单元", "用于标注的一段连续文本，通常是一个章节"],
        ["IAA", "标注一致性（Inter-Annotator Agreement），双人独立标注同一批样本后的吻合率"],
    ], widths=[4.0, 11.0])

    doc.add_heading("第二章 标注前的准备", level=1)
    bullets(doc, [
        "通读《关系类型抽取定义手册》，尤其是每条关系的反例。",
        "先建立术语表：把样本中出现的中英文名称、缩写、俗称登记为「缩写/别称 → 规范全称」映射。",
        "确认样本边界：以章节为最小单位，不跨文档抽关系。",
        "准备标注模板 docs/金标准标注模板.xlsx，不要在 Word 里自由发挥。",
    ])

    doc.add_heading("第三章 实体标注规则", level=1)
    doc.add_heading("3.1 实体边界", level=2)
    bullets(doc, [
        "R1 用规范全称：样本里写“SG”，实体写“蒸汽发生器（SG）”；同一实体在全文中统一写法。",
        "R2 不留修饰语的尾巴：“故障蒸汽发生器”不作为一个独立实体，实体仍是“蒸汽发生器”，故障状态用关系表达。",
        "R3 编号设备单独成实体：A 列 / B 列等区分列别的设备必须分别标注（如辅助给水泵A、辅助给水泵B）。",
        "R4 参数名不带数值：“一回路压力 15.5 MPa”中实体是“一回路压力”，数值进定值表。",
        "R5 动作短语保留动词：如“投入余热排出系统（RHR）”，不简写成“RHR”。",
        "R6 部件 vs 设备：设备可独立标识、可单独采购/检修；部件是设备的组成部分，按此边界判定。",
        "R7 工况/条件是实体：如“功率运行”“一回路温度低于180℃”作为 PlantMode 实体参与三元组。",
    ])
    doc.add_heading("3.2 实体类型判定优先级", level=2)
    bullets(doc, [
        "同一表述可归入多类时按此顺序：设备/部件 > 参数 > 定值 > 工况 > 故障/事件 > 动作 > 文档。",
        "既是设备又承担功能描述时，取实体类型 Equipment，功能用 HAS_FUNCTION 表达。",
        "无法归类时不强行归类，宁可不标，也不要制造噪声。",
    ])

    doc.add_heading("第四章 关系标注规则", level=1)
    doc.add_heading("4.1 基本原则", level=2)
    bullets(doc, [
        "只标原文明确陈述的事实，不标需要领域推断才能得出的关系。这是与 LLM 输出口径保持一致的前提。",
        "严格区分方向：PRECONDITION_OF 是“条件 → 动作”，写反会让图谱问答全错。",
        "一句话派生多条三元组是允许的：“SG 的主要部件包括传热管束、管板” 应拆成两条 PART_OF。",
        "同义重复不重复标：同一（主语，关系，宾语）在同一文档中只标一次，记录最完整的一处 evidence。",
        "列表式表述逐项标注，不合并成“包含A、B、C”一条三元组。",
    ])
    doc.add_heading("4.2 五类典型句式与对应处理", level=2)
    add_table(doc, ["句式特征", "举例", "处理"], [
        ["“A 是 B 的主要设备/部件”", "RCS 的主要设备包括…，蒸汽发生器…，稳压器", "逐设备拆 PART_OF，共用 evidence"],
        ["“A 的功能是…”", "稳压器的功能是维持一回路压力", "HAS_FUNCTION，宾语用动作短语"],
        ["“A 导致/造成 B”", "传热管破裂会造成…向二回路泄漏", "HAS_CONSEQUENCE"],
        ["“在 … 时方可/禁止 …”", "隔离完成前，禁止对一回路进行降压操作", "PRECONDITION_OF，注意否定转换"],
        ["“步骤 N：做 X”", "步骤 2 的操作内容包括：投入稳压器喷淋阀", "该动作 STEP_OF 该规程"],
    ], widths=[3.6, 5.6, 5.8])

    doc.add_heading("4.3 疑问处理规则", level=2)
    bullets(doc, [
        "拿不准的关系：标 confidence=low，并在备注列写清疑点，交由复核人裁决。不要凭直觉删掉。",
        "章程/标准中的“推荐”“宜”等表述：属于软性要求，仍按实际语义标注（倾向 HAS_FUNCTION / APPLIES_TO）。",
        "原文用表格表达的关系：按单元格内容逐行标注，evidence 填表格对应行的原文。",
        "跨段落才能成立的事实：一般不标；若确有必要，evidence 必须覆盖全部支撑内容，并把 confidence 降到 medium。",
    ])

    doc.add_heading("第五章 evidence 与来源标注", level=1)
    bullets(doc, [
        "evidence 必须是原文逐字片段（10~120 字），标点照抄，不得改写、不得省略中间内容用省略号连接。",
        "每条三元组必须有 evidence；没有原文的推断一条都不标。",
        "来源字段填到「文档 + 章节路径」粒度；有页码的补页码。",
        "同一 evidence 支撑多条三元组时允许重复引用。",
    ])

    doc.add_heading("第六章 定值表标注规则", level=1)
    bullets(doc, [
        "每个参数一条记录；同一参数的多个报警定值合并进 alarms 数组，不拆成多条记录。",
        "单位不同或适用工况不同时，才拆成多条记录。",
        "原文用其他表述（如“整定值1/2”）时，按语义映射到 低低/低/高/高高，并在 level_raw 注明原文。",
        "需要的换算必须保留原始值：value 填换算后数值，value_raw 保留原文写法。",
        "表格中没有但上下文提到的定值也要标注，evidence 指到那句话。",
        "缺失字段留空，严禁根据经验补全 —— 这是与 LLM 结果做逐字段比对的基础。",
    ])

    doc.add_heading("第七章 一致性与质检", level=1)
    doc.add_paragraph("建议流程：")
    bullets(doc, [
        "双人独立标注同一批 20% 样本，算 IAA；低于 0.85 时必须回到本规范重新对齐口径，再继续。",
        "标注过程中每完成 50 条做一次自查，重点查方向、查类型枚举、查 evidence 是否逐字。",
        "全部完成后由第三方抽 10% 复核，复核采用“先不看原标注、只看原文”的方式，避免锚定效应。",
        "所有争议条目必须留下结论与理由，形成「争议处理记录」，供后续 prompt 调优参考。",
    ])
    doc.add_heading("7.1 十大常见错误", level=2)
    add_table(doc, ["#", "错误", "正确做法"], [
        ["1", "凭常识把没写的关系标出来", "只读原文，原文的沉默就是没有"],
        ["2", "证据里没有宾语却标了关系", "evidence 必须同时支持主谓宾三要素"],
        ["3", "同一事实重复标注多条", "按 (主语,关系,宾语) 去重，保留证据最完整的一条"],
        ["4", "PRECONDITION_OF / STEP_OF 方向写反", "条件在前、动作在后；步骤在前、规程在后"],
        ["5", "把别名当成新实体", "走术语表归一，登记别名而不是新增实体"],
        ["6", "实体带上了工况/状态修饰", "状态用关系表达，实体保持本名"],
        ["7", "数值与单位一起塞进实体名", "数值进定值表，实体只留参数名"],
        ["8", "表格跨页/合并单元格导致缺行", "核对原件表格，逐行标注，每行都能溯源"],
        ["9", "用概括性句子当 evidence", "evidence 必须是逐字片段，不是总结"],
        ["10", "漏标隐含的“必要条件”", "否定句（禁止/方可/不得）往往是前置条件的来源，重点扫一遍"],
    ], widths=[1.0, 5.0, 9.0])

    doc.add_heading("第八章 交付格式", level=1)
    doc.add_paragraph(f"标注结果按两份 JSONL 交付：gold_triples.jsonl（本工程内置 {len(gold_t)} 条）与 "
                      f"gold_setpoints.jsonl（本工程内置 {len(gold_s)} 条）。字段要求：")
    add_table(doc, ["字段", "必填", "说明"], [
        ["gold_id", "是", "样本唯一编号，如 GT-001 / GS-001"],
        ["subject / object", "是", "规范化后的实体全称"],
        ["subject_type / object_type", "是", "必须是本体定义的 12 类之一"],
        ["predicate", "是", "必须是本体定义的 16 种之一"],
        ["evidence", "是", "原文逐字片段"],
        ["source_doc / section", "是", "来源定位"],
        ["confidence", "是", "high / medium / low"],
    ], widths=[4.0, 1.6, 9.4])

    doc.add_heading("第九章 标注作业 SOP（按这个节拍做）", level=1)
    add_table(doc, ["步骤", "动作", "产出 / 检查点"], [
        ["S1", "通读整章，先把术语表补齐", "术语表无遗漏别名"],
        ["S2", "第一遍只标实体，不标关系", "实体清单"],
        ["S3", "第二遍按句标注关系，遇到疑惑标 low", "三元组初稿"],
        ["S4", "逐条回填 evidence（逐字片段）", "无 evidence 的条目一律作废"],
        ["S5", "表格单独走一遍定值标注", "定值记录"],
        ["S6", "自查：方向、类型枚举、去重", "按 7.1 的十大错误清单自查"],
        ["S7", "交叉复核（另一人独立看原文）", "争议记录 + 最终版"],
    ], widths=[1.6, 5.4, 8.0])

    doc.add_heading("第十章 好坏标注对照示例", level=1)
    add_table(doc, ["原文", "❌ 错误标注", "问题", "✅ 正确标注"], [
        ["RCS 的主要设备包括反应堆压力容器、蒸汽发生器、主泵和稳压器。",
         "(反应堆冷却剂系统, HAS_PART, 压力容器、SG、主泵、稳压器)",
         "①关系名不在本体；②未逐项拆开；③实体名用了缩写",
         "拆成 4 条：(反应堆压力容器, PART_OF, 反应堆冷却剂系统) 等"],
        ["稳压器的功能是维持一回路压力。",
         "(主泵, HAS_FUNCTION, 维持一回路压力)",
         "主语错位：证据里写的是稳压器，不是主泵",
         "(稳压器, HAS_FUNCTION, 维持一回路压力)"],
        ["隔离完成前，禁止对一回路进行降压操作。",
         "(一回路降压操作, PRECONDITION_OF, 完成隔离)",
         "方向写反：应先 -条件- 后 -动作-",
         "(完成故障蒸汽发生器隔离, PRECONDITION_OF, 一回路降温降压)"],
        ["传热管破裂会造成一回路冷却剂持续向二回路泄漏，导致放射性向二回路释放。",
         "(传热管破裂, HAS_SYMPTOM, 放射性释放)",
         "混用征兆与后果，且没用规范实体全称",
         "(蒸汽发生器传热管破裂, HAS_CONSEQUENCE, 放射性向二回路释放)"],
        ["| 压力高高 | 16.5 | MPa | 稳压器安全阀动作 |",
         "param=一回路压力高高定值, unit=MPa.a, value=16.5bar",
         "凭熟悉程度改单位，且实体名写了数值",
         "param=一回路压力, level=高高, value=16.5, unit=MPa, action=稳压器安全阀动作"],
    ], widths=[4.6, 4.2, 4.0, 6.2])

    doc.add_heading("第十一章 交付前自检清单", level=1)
    bullets(doc, [
        "所有 predicate 都取自 16 种关系的英文标识，无自创关系名。",
        "所有 type 都取自 12 类实体，无出现 Function 之类的临时类型。",
        "每条三元组都有逐字 evidence，且 evidence 同时支持主、谓、宾。",
        "同一 (主语,关系,宾语) 无重复行；同一参数的多个定值已合并进一条记录。",
        "所有缺失字段为空，没有任何“看起来合理”的补全。",
        "confidence=low 的条目已在备注列写明疑点。",
        "两份 CSV 以 UTF-8 编码导出，可直接放入 data/gold/ 参与评估。",
    ])

    path = DOCS / "金标准标注规范.docx"
    doc.save(path)
    return path


# ================================================================ 文档 4
def build_query_doc():
    cy = (ROOT / "queries/verification_queries.cypher").read_text(encoding="utf-8")
    blocks = re.findall(r"// ===== (.*?) =====\n(.*?)\n(.*?;)", cy, re.S)
    doc = new_doc("10 个图谱验证查询与预期结果", "POC 的期末考试题 · 可直接粘贴到 Neo4j Browser")
    doc.add_paragraph(
        "使用说明：① 先在 Neo4j 中执行 outputs/graph.cypher 或运行 scripts/import_neo4j.py；"
        "② 逐题执行下列 Cypher；③ 比对“预期结果”一列。"
        "没有 Neo4j 时运行 python scripts/run_verification_queries.py 可做等价的本地预演。")

    add_table(doc, ["#", "查询主题", "验证目标"],
              [[f"Q{i}", re.sub(r"^Q\d+ · ", "", t.split("（")[0]), re.sub(r"^// 目的：", "", p.strip())]
               for i, (t, p, _) in enumerate(blocks, 1)]
              if blocks else [["—", "—", "—"]], widths=[1.2, 6.0, 7.8])

    for i, (title, purpose, cypher) in enumerate(blocks, 1):
        lines = purpose.strip().split("\n")
        exp = [l for l in lines if l.startswith("// 预期结果：")]
        act = [l for l in lines if l.startswith("// 本地预演实际返回：")]
        aim = [l for l in lines if l.startswith("// 目的：")]
        doc.add_heading(f"Q{i} · {title}", level=1)
        doc.add_paragraph(re.sub(r"^// 目的：", "目的：", aim[0]) if aim else "")
        p = doc.add_paragraph()
        p.add_run("预期结果：").bold = True
        if exp:
            p.add_run(re.sub(r"^// 预期结果：", "", exp[0]))
        doc.add_paragraph("Cypher：")
        code_block(doc, cypher.strip())
        p = doc.add_paragraph()
        p.add_run("本地预演实际返回：").bold = True
        if act:
            p.add_run(re.sub(r"^// 本地预演实际返回：", "", act[0]))

    doc.add_heading("补充说明：Q5 暴露的归一化待办", level=1)
    doc.add_paragraph(
        "Q5 返回两行：三元组抽出的「压力高高定值16.5MPa」与定值表回填的「一回路压力高高定值」，两者数值相同、节点未合并。"
        "这暴露了一个真实待办：建议规定定值的统一命名模板 <参数名><级别>定值，"
        "在实体归一化阶段把两条来源统一后合并，避免同一物理定值在图谱中分裂。")

    path = DOCS / "10个验证查询与预期结果.docx"
    doc.save(path)
    return path


# ================================================================ Excel
HEAD_FILL = PatternFill("solid", fgColor="1F3B73")
EX_FILL = PatternFill("solid", fgColor="FFF2CC")


def build_xlsx(onto):
    gold_t, gold_s = load_gold()
    glossary = load_glossary()
    wb = Workbook()

    def style_header(ws, ncol, widths):
        for c in range(1, ncol + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = Font(color="FFFFFF", bold=True, name="微软雅黑", size=10)
            cell.fill = HEAD_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.column_dimensions[get_column_letter(c)].width = widths[c - 1]
        ws.freeze_panes = "A2"

    # --- 说明 ---
    ws = wb.active
    ws.title = "填写说明"
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 96
    rows = [
        ("文件名", "金标准标注模板.xlsx"),
        ("用途", "核电文档「知识三元组 + 参数定值表」的人工标注金标准生产模板"),
        ("配套文档", "《金标准标注规范.docx》《关系类型抽取定义手册.docx》"),
        ("", ""),
        ("工作表 1：三元组标注表", "每行一条三元组。gold_id 用 GT-001 递增；predicate 必须从「关系速查」表取；"
                             "subject_type/object_type 必须从「实体类型速查」表取。"),
        ("工作表 2：定值标注表", "每个参数一条记录；同一参数的多个报警定值合并进同一条，写在 alarms_json 列。"
                            "缺失字段留空，严禁猜测补全。"),
        ("工作表 3：术语表", "别名统一登记到「aliases」列，多个别名用竖线分隔。新出现的别称必须回写到本表。"),
        ("工作表 4：关系速查", "16 种关系的定义与正反例，标注时不确定就先查这一表。"),
        ("工作表 5：实体类型速查", "12 类实体的定义与示例。"),
        ("", ""),
        ("evidence 规则", "必须是原文逐字片段（10~120 字），不得改写；缺失 evidence 的条目视为无效。"),
        ("confidence 规则", "high=原文句式明确；medium=需跨两句合并；low=隐含/指代不清，需备注疑点。"),
        ("交付格式", "两张表分别另存为 gold_triples.csv / gold_setpoints.csv（UTF-8），放入 data/gold/ 即可参与评估。"),
        ("示例数据", "前若干行黄色底色为已完成的示例，正式标注时请覆盖或删除。"),
    ]
    for r, (a, b) in enumerate(rows, 1):
        ws.cell(row=r, column=1, value=a).font = Font(bold=True, name="微软雅黑", size=10)
        ws.cell(row=r, column=2, value=b).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=r, column=2).font = Font(name="微软雅黑", size=10)

    # --- 三元组标注表 ---
    ws = wb.create_sheet("三元组标注表")
    header = ["gold_id", "subject", "subject_type", "predicate", "object", "object_type",
              "evidence", "source_doc", "section", "confidence", "备注/疑点"]
    ws.append(header)
    style_header(ws, len(header), [10, 20, 12, 16, 26, 12, 46, 24, 34, 10, 20])
    for g in gold_t:
        ws.append([g["gold_id"], g["subject"], g["subject_type"], g["predicate"], g["object"],
                   g["object_type"], g["evidence"], g["source_doc"], g["section"], g["confidence"], ""])
    for row in ws.iter_rows(min_row=2, max_row=1 + len(gold_t)):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.font = Font(name="微软雅黑", size=9)
            c.fill = EX_FILL

    # --- 定值标注表 ---
    ws = wb.create_sheet("定值标注表")
    header = ["gold_id", "param_id", "param_name", "param_name_en", "system", "equipment", "unit",
              "normal_min", "normal_max", "alarms_json", "applicable_mode",
              "source_doc", "source_section", "source_evidence", "version", "confidence", "备注"]
    ws.append(header)
    style_header(ws, len(header), [10, 12, 20, 22, 20, 16, 10, 10, 10, 56, 14, 24, 34, 40, 10, 10, 20])
    for g in gold_s:
        nr = g.get("normal_range") or {}
        ws.append([g["gold_id"], g.get("param_id"), g.get("param_name"), g.get("param_name_en"),
                   g.get("system"), g.get("equipment"), g.get("unit"), nr.get("min"), nr.get("max"),
                   json.dumps(g.get("alarms") or [], ensure_ascii=False), g.get("applicable_mode"),
                   (g.get("source") or {}).get("doc"), (g.get("source") or {}).get("section"),
                   (g.get("source") or {}).get("evidence"), g.get("version"), g.get("confidence"), ""])
    for row in ws.iter_rows(min_row=2, max_row=1 + len(gold_s)):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.font = Font(name="微软雅黑", size=9)
            c.fill = EX_FILL

    # --- 术语表 ---
    ws = wb.create_sheet("术语表")
    header = ["canonical（规范全称）", "type", "aliases（别名，竖线分隔）", "source"]
    ws.append(header)
    style_header(ws, len(header), [28, 14, 60, 16])
    for r in glossary:
        ws.append([r["canonical"], r["type"], r["aliases"], r["source"]])
    for row in ws.iter_rows(min_row=2, max_row=1 + len(glossary)):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.font = Font(name="微软雅黑", size=9)

    # --- 关系速查 ---
    ws = wb.create_sheet("关系速查")
    header = ["predicate", "中文名", "定义", "正例", "反例", "是否硬校验"]
    ws.append(header)
    style_header(ws, len(header), [18, 12, 54, 46, 40, 12])
    cons_row = {}
    from kgpoc.config import load_yaml
    cons = load_yaml("config/relation_constraints.yaml")["constraints"]
    for r in onto.raw["relation_types"]:
        cons_row[r["id"]] = "是" if cons.get(r["id"], {}).get("hard") else "否"
    for r in onto.raw["relation_types"]:
        ws.append([r["id"], r.get("name_cn", ""), r["definition"],
                   "\n".join(r.get("positive", [])), "\n".join(r.get("negative", [])),
                   cons_row[r["id"]]])
    for row in ws.iter_rows(min_row=2, max_row=1 + len(onto.raw["relation_types"])):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.font = Font(name="微软雅黑", size=9)

    # --- 实体类型速查 ---
    ws = wb.create_sheet("实体类型速查")
    header = ["type", "中文名", "定义", "示例"]
    ws.append(header)
    style_header(ws, len(header), [16, 14, 60, 46])
    for e in onto.raw["entity_types"]:
        ws.append([e["id"], e["name_cn"], e["desc"], "、".join(e.get("examples", []))])
    for row in ws.iter_rows(min_row=2, max_row=1 + len(onto.raw["entity_types"])):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.font = Font(name="微软雅黑", size=9)

    p = DOCS / "金标准标注模板.xlsx"
    wb.save(p)
    return p


def main():
    onto = load_ontology()
    metrics = load_metrics()
    paths = [
        build_plan_doc(onto, metrics),
        build_relation_doc(onto),
        build_annotation_doc(onto),
        build_query_doc(),
        build_xlsx(onto),
    ]
    print("[OK] 已生成文档：")
    for p in paths:
        print("     " + str(p.relative_to(ROOT)))


if __name__ == "__main__":
    main()
