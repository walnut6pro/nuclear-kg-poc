# 任务
你是核电领域的参数定值管理专家。请从给定的表格及其上下文（表格前的说明文字）中抽取参数定值信息，输出严格符合定值 Schema 的 JSON 数组。

# 定值 Schema（JSON Schema）
{schema_block}

# 抽取规则（逐条遵守）
1. **数值保留原文精度**：原文 `17.23` 不得写作 `17.2`；范围写作 `normal_range.min/max`。
2. **单位换算要留痕**：如需换算（如 bar→MPa），`value` 填换算后的值，`value_raw` 保留原文写法（如 `"8.6 bar(a)"`）。无法换算时不要硬算，保留原文并在 `value_raw` 注明。
3. **报警级别归一化**：统一映射到 `低低 / 低 / 高 / 高高`。原文使用其它表述（如"整定值1/2""第一阈值/第二阈值""低—Ⅱ"）时，按语义映射并在 `level_raw` 注明原文表述；无法可靠映射时 level 填 `其他` 并在 `level_raw` 写原文。
4. **陷阱：区分"报警"与"保护动作"**。同一个阀门的不同动作（报警、停堆、安全阀动作、隔离）若对应不同整定值，必须拆成多条 alarm；若共用同一整定值，保留一条 alarm，`action` 用中文顿号并列。
5. **上下文补充**：表格中没有、但上下文（表格前的说明段落）明确提到的定值/适用工况也要抽；`source.evidence` 填写该句的逐字片段。
6. **缺失字段填 null，严禁编造**：猜出来的数值、猜出来的单位、猜出来的适用工况一律留 null。
7. **一行一对象**：每个参数生成一条记录，多个报警定值时合并进同一条记录的 `alarms` 数组；只有当单位或适用工况不同时才拆成多条。
8. **智能补ID**：`param_id` 若原文未提供，可由抽取出的数据生成，格式为 `{{系统代号}}-{{参数代号}}-{{三位序号}}`，如 `RCS-PT-001`。
9. 输出**纯 JSON 数组**，不要任何解释性文字、不要 markdown 代码块围栏。

# 输入
[文档元信息]
doc_id：{doc_id}
章节路径：{section_path}
页码：{page}
[表格上文]
{preceding_text}
[表格]
{table_content}

# 输出格式
[
  {{
    "param_id": "RCS-PT-001",
    "param_name": "",
    "param_name_en": "",
    "system": "",
    "equipment": "",
    "unit": "",
    "normal_range": {{"min": null, "max": null}},
    "alarms": [
      {{"level": "高高", "level_raw": null, "value": 0.0, "value_raw": null, "action": "", "applicable_mode": null}}
    ],
    "applicable_mode": "",
    "source": {{"doc": "", "page": null, "section": "", "evidence": ""}},
    "version": "v1.0",
    "confidence": 0.95
  }}
]
