# 核电领域「知识三元组 + 参数定值表」POC 流水线

端到端可运行的最小实现：**解析 → 抽取 → 后处理 → 质量评估 → 图谱入库**。
本体、prompt、LLM 连接全部配置化，代码里不含任何写死的领域知识。

> ⚠️ 合规提醒：核电文档保密管理严格，"把文件导出到外网"这一步本身就可能有合规风险。
> 本仓库内置语料均为依据公开教材/公开导则通用体例编写的演示数据，**不含任何涉密或电厂专有信息**。
> POC 的目标是验证流水线可行性与质量水平，通过后再把 LLM 换成内网本地部署模型即可平移。
> 真实文档入库前请务必与文档所有方 / 保密部门确认。

---

## 一、30 秒跑通（离线演示，不需要任何 API key）

```bash
pip install -r requirements.txt
python scripts/run_pipeline.py
python scripts/run_verification_queries.py
```

预期输出（内置演示数据下的实际结果）：

```
2 份文档 → 29 个 chunk（其中表格 4 个）
原始三元组 47 条 / 原始定值记录 8 条
最终三元组 43 条 / 定值记录 7 条
  NLI 剔除 2 条，去重合并 2 条，数值隔离 1 条
三元组 P=0.977  R=0.913  F1=0.944
定值表 字段准确率=0.977  行召回=1.000
验收：全部达标
节点 65 个 / 边 76 条
```

## 二、接真实大模型

```bash
export DEEPSEEK_API_KEY=sk-xxxx
python scripts/run_pipeline.py --provider deepseek
```

| 场景 | provider | 说明 |
|---|---|---|
| 离线演示 | `mock` | 读 `data/fixtures/mock_extractions.json`，无需网络 |
| 外网 POC | `deepseek` | 便宜、中文好、支持 JSON mode |
| 备选 | `openai` | GPT-4o |
| 内网平移 | `vllm_qwen` | vLLM / Xinference 本地部署，只改 `config/llm.yaml` 的 base_url |

LLM 调用统一走 OpenAI 兼容协议（`kgpoc/llm.py`），内网换模型**不需要改一行代码**。

## 三、目录结构

```
config/
  ontology.yaml              12 类实体 + 16 种关系定义（含正反例）+ 数值校验规则
  relation_constraints.yaml  关系的 domain/range 约束（后置软校验）
  setpoint_schema.json       定值表 JSON Schema
  llm.yaml                   LLM 提供方与并发/重试配置
prompts/
  triple_extraction.md       三元组抽取
  setpoint_extraction.md     定值表抽取
  nli_verification.md        溯源校验（杀幻觉）
  entity_pass1.md            两遍式抽取的第一遍（召回率不足时启用）
kgpoc/
  config.py      配置加载与 prompt 渲染
  parser.py      Markdown → 结构化 chunk（标题路径 / 表格独立）
  llm.py         OpenAI 兼容客户端 + 离线 MockLLM
  extract.py     调用 LLM，输出原始抽取结果
  postprocess.py Schema/归一/去重/NLI/数值/冲突检测
  normalize.py   实体归一化（术语表驱动）
  evaluate.py    P/R/F1 + 分桶分析 + 定值字段级比对
  graph.py       JSONL 图谱 + Cypher 导出
data/
  sample/        内置演示语料（2 份，覆盖叙述/规程/参数表三种文档性格）
  gold/          人工金标准：46 条三元组 / 7 条定值记录
  fixtures/      离线演示用的预置抽取结果
  terminology/glossary.csv  术语表 61 条（中英文 + 缩写映射）
scripts/
  build_fixtures.py              生成/重建演示 fixture
  run_pipeline.py                端到端流水线
  import_neo4j.py                增量入库（幂等）
  run_verification_queries.py    10 个验证查询 + 预期结果
queries/verification_queries.cypher   10 个期末考试题（含预期结果）
outputs/         运行产物（chunks / raw / clean / 评估报告 / 图谱 / Cypher）
docs/            正式方案文档、关系定义手册、标注规范、标注模板
```

## 四、每个环节在哪里、改什么

| 环节 | 代码 | 想调什么 | 改哪里 |
|---|---|---|---|
| 1 解析切分 | `kgpoc/parser.py` | chunk 长度、表格是否独立 | `MAX_CHUNK_CHARS`；MinerU→Markdown 后接这里 |
| 2 本体 | `config/ontology.yaml` | 加实体/关系类型 | 改 yaml，prompt 与校验自动跟随 |
| 3 抽取 | `prompts/*.md` | 抽取口径 | 改 prompt（prompt 也是代码，用 git 管版本） |
| 4 后处理 | `kgpoc/postprocess.py` | 校验松紧、别名合并 | 术语表 + `validation_rules` |
| 5 评估 | `kgpoc/evaluate.py` | 达标线 | `targets` 部分 |
| 6 入图 | `kgpoc/graph.py` | 图模型 | 节点/边生成逻辑 |

## 五、后处理各环节的实际贡献（内置演示数据）

| 环节 | 计数 | 说明 |
|---|---:|---|
| 原始抽取三元组 | 47 | LLM 直接产出 |
| NLI 剔除 | 2 | 证据不支持，疑为幻觉 |
| 去重合并 | 2 | 同一事实被多个 chunk 重复抽到 |
| 数值隔离 | 1 | 165 MPa 超量级（植入的错误数据） |
| 最终入图三元组 | 43 |  |

演示 fixture 里**刻意植入了脏数据**（幻觉 / 重复 / 推理过度 / 数值错误 / 4 条漏抽），
用来证明后处理每个环节真的在工作，而不是摆设。详见 `scripts/build_fixtures.py` 顶部注释。

## 六、10 个验证查询

```bash
python scripts/run_verification_queries.py          # 本地预演（无需 Neo4j）
python scripts/run_verification_queries.py --neo4j  # 连接真实数据库
```

覆盖：结构查询、多跳因果链、图谱+定值表联查、前置条件、定值触发链、适用规程、
冗余配置、**跨文档冲突检测**、反向诊断、参数清单。每条都带目的 + 预期结果 + 实际返回值。

## 七、往真实场景平移要留的接口

1. **LLM 可替换**：统一 OpenAI 兼容接口，内网改 `base_url` 即可。
2. **本体可扩展**：schema 在配置文件里，真实文档逼你加类型（逻辑图、仪控信号、电缆）时改 yaml。
3. **保密分级打标**：所有 chunk / 节点 / 边都带 `security_level` 字段，从第一天就养成习惯。
4. **规模化成本**：每次调用都写 `outputs/token_usage.jsonl`，POC 跑完即可估算几百份文档的预算。

## 八、可选：本地起一个 Neo4j

```bash
docker compose up -d neo4j          # http://localhost:7474，neo4j / password
python scripts/import_neo4j.py --wipe
```
