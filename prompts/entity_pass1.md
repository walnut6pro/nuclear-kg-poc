# 两遍式抽取 · 第一遍：仅抽实体
# 当三元组召回率不足（<75%）时，把 config/ontology.yaml 的 extraction.mode 改为 two_pass 启用本 prompt。

# 任务
从给定文本中识别全部属于下列类型的实体候选，不做关系判断。

# 实体类型
{entity_types_block}

# 规则
1. 抽取**文本中出现过的**实体，包括缩写形式与全称形式，分别作为独立候选项列出。
2. `surface` 填写与原文完全一致的写法（含缩写），`canonical` 填写你推荐的规范全称。
3. 若同一实体在文中以多种写法出现（如"SG""蒸汽发生器"），请都列出，并在 `aliases` 中互相登记。
4. 不要抽取代词（其、该设备）作为独立实体。
5. 输出纯 JSON 数组。

# 文本
doc_id：{doc_id}
章节路径：{section_path}
{chunk_text}

# 输出格式
[
  {{
    "surface": "",
    "canonical": "",
    "type": "",
    "aliases": []
  }}
]
