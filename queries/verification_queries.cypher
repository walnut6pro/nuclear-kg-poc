// 核电知识图谱 POC · 10 个验证查询
// 生成方式：python scripts/run_verification_queries.py（预期结果来自 outputs/nodes.jsonl + edges.jsonl 的本地预演）

// ===== Q1 · RCS 包含哪些设备？（结构查询） =====
// 目的：验证 PART_OF 关系抽取与多源合并。
// 预期结果：4 条：主泵 / 反应堆压力容器 / 蒸汽发生器 / 稳压器
// 本地预演实际返回：[["Equipment", "主泵"], ["Equipment", "反应堆压力容器"], ["Equipment", "稳压器"], ["Equipment", "蒸汽发生器"]]
MATCH (e)-[:PART_OF]->(s:Entity {name:'反应堆冷却剂系统'})
RETURN e.type AS 类型, e.name AS 名称
ORDER BY e.type, e.name;

// ===== Q2 · SGTR 的完整因果链：原因→征兆→后果→处置（多跳） =====
// 目的：验证故障知识是否连成链，这是图谱最有价值的形态。
// 预期结果：链路完整：原因 2 项（应力腐蚀裂纹、制造缺陷；磨损减薄为刻意留的漏抽项）、征兆 4 项、后果 2 项、处置 1 项
// 本地预演实际返回：[["蒸汽发生器传热管破裂", ["制造缺陷", "应力腐蚀裂纹"], ["SG液位快速上升", "一回路压力下降", "二回路放射性水平异常升高", "稳压器液位降低"], ["一回路冷却剂向二回路泄漏", "放射性释放"], ["隔离故障蒸汽发生器"]]]
MATCH (f:Entity {name:'蒸汽发生器传热管破裂'})
OPTIONAL MATCH (f)-[:CAUSED_BY]->(cause)
OPTIONAL MATCH (f)-[:HAS_SYMPTOM]->(sym)
OPTIONAL MATCH (f)-[:HAS_CONSEQUENCE]->(cons)
OPTIONAL MATCH (f)-[:MITIGATED_BY]->(act)
RETURN f.name AS 故障,
       collect(DISTINCT cause.name) AS 原因,
       collect(DISTINCT sym.name)   AS 征兆,
       collect(DISTINCT cons.name)  AS 后果,
       collect(DISTINCT act.name)   AS 处置;

// ===== Q3 · 功率运行工况下，一回路压力有哪些报警定值？（图谱 + 定值表联查） =====
// 目的：验证定值是否真正挂到图谱节点上，能被 SQL 式条件过滤。
// 预期结果：4 行：13.9MPa 触发紧急停堆 / 14.5MPa 报警 / 16.2MPa 报警 / 16.5MPa 稳压器安全阀动作
// 本地预演实际返回：[["一回路压力低低定值", 13.9, "MPa", "触发紧急停堆"], ["一回路压力低定值", 14.5, "MPa", "报警"], ["一回路压力高定值", 16.2, "MPa", "报警"], ["一回路压力高高定值", 16.5, "MPa", "稳压器安全阀动作"]]
MATCH (p:Parameter {name:'一回路压力'})-[:HAS_SETPOINT]->(s:Setpoint)
WHERE s.applicable_mode = '功率运行'
RETURN DISTINCT p.name AS 参数, s.name AS 定值, s.value AS 整定值, s.unit AS 单位, s.action AS 动作
ORDER BY s.value;

// ===== Q4 · 投入余热排出系统（RHR）的前置条件是什么？ =====
// 目的：验证 PRECONDITION_OF 的方向抽取是否正确。
// 预期结果：2 条：一回路温度低于180℃ / 一回路压力低于3.0MPa
// 本地预演实际返回：[["一回路温度低于180℃", "PlantMode"], ["一回路压力低于3.0MPa", "PlantMode"]]
MATCH (pre)-[:PRECONDITION_OF]->(a {name:'投入余热排出系统（RHR）'})
RETURN pre.name AS 前置条件, pre.type AS 类型;

// ===== Q5 · 压力高高报警会触发哪些动作？ =====
// 目的：验证 Setpoint → Action 的 TRIGGERS 链。
// 预期结果：2 行，均指向稳压器安全阀动作：①三元组抽出的「压力高高定值16.5MPa」②定值表回填的「一回路压力高高定值」。二者尚未归一，建议按模板 <参数名><级别>定值 统一后再合并
// 本地预演实际返回：[["压力高高定值16.5MPa", null, null, ["稳压器安全阀动作"]], ["一回路压力高高定值", 16.5, "MPa", ["稳压器安全阀动作"]]]
MATCH (s:Setpoint)-[:TRIGGERS]->(a)
WHERE s.name CONTAINS '高高'
RETURN s.name AS 定值, s.value AS 整定值, s.unit AS 单位, collect(a.name) AS 触发动作;

// ===== Q6 · 哪些规程适用于蒸汽发生器？ =====
// 目的：验证 APPLIES_TO 与 Procedure 实体的抽取。
// 预期结果：1 条：SGTR处置导则
// 本地预演实际返回：[["SGTR处置导则"]]
MATCH (p:Procedure)-[:APPLIES_TO]->(e {name:'蒸汽发生器'})
RETURN DISTINCT p.name AS 规程;

// ===== Q7 · 辅助给水泵的冗余配置是什么？ =====
// 目的：验证 REDUNDANT_WITH（冗余是安全审评高频问题）。
// 预期结果：1 组：辅助给水泵A ←→ 辅助给水泵B
// 本地预演实际返回：[["辅助给水泵A", "辅助给水泵B"]]
MATCH (a)-[:REDUNDANT_WITH]->(b)
RETURN a.name AS 设备A, b.name AS 设备B, labels(a) AS A类型;

// ===== Q8 · 两个不同文档对同一参数的定值是否一致？（跨文档冲突检测） =====
// 目的：图谱相对于文档检索的独特价值，务必演示。
// 预期结果：内置两份文档的一回路压力定值完全一致 → 返回 0 行；一旦口径不一致会立即返回冲突明细
// 本地预演实际返回：[]
MATCH (s:Setpoint)
WHERE s.conflict = true
RETURN s.name AS 定值, s.source_docs AS 来源文档, s.values AS 不同取值,
       s.conflict_detail AS 冲突明细;

// ===== Q9 · 从「二回路放射性水平异常升高」出发，能反推出哪些可能故障？（反向诊断） =====
// 目的：验证 HAS_SYMPTOM 反向追溯能力。
// 预期结果：1 条：蒸汽发生器传热管破裂（FaultMode）
// 本地预演实际返回：[["蒸汽发生器传热管破裂", "FaultMode"]]
MATCH (f)-[:HAS_SYMPTOM]->(s {name:'二回路放射性水平异常升高'})
RETURN DISTINCT f.name AS 可能故障, f.type AS 类型;

// ===== Q10 · 反应堆冷却剂系统的完整监测参数清单？ =====
// 目的：验证 MONITORS 关系 + 定值表回填产生的参数-系统关联。
// 预期结果：5 个参数：一回路压力 / 一回路设计压力 / 堆芯出口温度 / 堆芯入口温度 / 单环路冷却剂流量
// 本地预演实际返回：[["一回路压力", "MPa", "RCS-PT-001"], ["一回路设计压力", "MPa", "RCS-PT-002"], ["单环路冷却剂流量", "m³/h", "RCS-FT-001"], ["堆芯入口温度", "℃", "RCS-TE-002"], ["堆芯出口温度", "℃", "RCS-TE-001"]]
MATCH (p:Parameter)-[:MONITORS]->(s {name:'反应堆冷却剂系统'})
RETURN DISTINCT p.name AS 参数, p.unit AS 单位, p.param_id AS 编号
ORDER BY p.name;
