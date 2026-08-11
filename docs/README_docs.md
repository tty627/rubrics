# rubric_pipeline

Rubric 生成全流程实现：从种子集到最终交付。

## 依赖

**纯 Python 标准库**，无第三方包。已在 Python 3.12.3 上测试。

需要的标准库模块（均为内置）：
- `zipfile`, `xml.etree.ElementTree` — xlsx 读写
- `urllib.request`, `json`, `hashlib` — LLM 客户端
- `concurrent.futures` — 并发调用
- `os`, `sys`, `ast`, `collections` — 基础工具

## 目录结构

```
rubric_pipeline/
├── lib/                    # 基础库
│   ├── xlsx.py             # xlsx 读写（纯 stdlib，无 openpyxl 依赖）
│   ├── llm.py              # OpenAI 兼容客户端 + 磁盘缓存 + 并发
│   └── schema.py           # JSON schema 定义（待实现）
├── stages/                 # 流水线各步
│   ├── s00_seed.py         # Phase 0: xlsx → seed.jsonl + baseline.json
│   ├── s01_filter.py       # Step 1: 入口过滤（待实现）
│   ├── s02_context.py      # Step 2: 上下文标签抽取（待实现）
│   └── ...
├── prompts/                # 各步的 prompt 模板（待实现）
├── data/                   # 数据目录（.gitignore 已排，只入库 baseline.json）
│   ├── .gitignore          # 显式排掉所有生成物
│   ├── baseline.json       # 草稿 rubric 基线指标（已生成）
│   └── seed.jsonl          # 从 xlsx 导出的 453 条记录（本地，不入库）
├── cache/                  # LLM 调用缓存（.gitignore 已排）
└── config/                 # 配置（.gitignore 已排 models.json）
    └── models.json.example # 模型端点配置示例
```

## 配置

### 1. 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RP_XLSX` | `./data/input.xlsx` | 输入的 xlsx 文件路径 |
| `RP_OUT` | `./data` | 输出目录 |
| `RP_CACHE` | `./cache` | LLM 调用缓存目录 |

### 2. 模型端点

复制 `config/models.json.example` 为 `config/models.json`，填入你的模型端点：

```json
[
  {
    "name": "gen-main",
    "model_id": "qwen2.5-72b-instruct",
    "base_url": "http://your-vllm-server:8000/v1",
    "api_key": "EMPTY",
    "family": "qwen",
    "roles": ["generator"]
  },
  {
    "name": "gen-alt",
    "model_id": "internlm2.5-20b-chat",
    "base_url": "http://another-server:8001/v1",
    "api_key": "EMPTY",
    "family": "intern",
    "roles": ["generator", "diagnoser"]
  },
  {
    "name": "judge",
    "model_id": "glm-4-9b-chat",
    "base_url": "http://judge-server:8002/v1",
    "api_key": "EMPTY",
    "family": "glm",
    "roles": ["judge"]
  }
]
```

**硬约束**：
- `generator` 角色至少 2 个，且 `family` 不同（Stage 6 多模型聚合要求异质）
- `judge` 角色的 `family` 必须与所有 `generator` 不同（Stage 12 防自偏好偏差）

`base_url` 是 OpenAI 兼容的 `/v1` 地址，vLLM 启动时加 `--api-key EMPTY` 即可。

## 运行

### Phase 0：数据层（0 LLM 调用）

把你的 xlsx 文件放到 `data/input.xlsx`，或设 `RP_XLSX` 环境变量指向它。

表格结构要求（第一行是表头）：

| A | B | C | D | E | F | G |
|---|---|---|---|---|---|---|
| need_rewrite | rewritten | gen_rubric | **question** | **dimension** | **draft_rubric** | **ref_response** |

- **D (question)**：必填，用户提问
- **E (dimension)**：可选，学科标签列表（Python list 字符串，如 `["数学", "微积分"]`）
- **F (draft_rubric)**：可选，草稿 rubric（JSON 字符串）
- **G (ref_response)**：可选，参考回复（JSON dict，键为模型名如 `response_glm52`）

然后跑：

```bash
python3 stages/s00_seed.py
```

产出：
- `data/seed.jsonl` — 453 条记录，每条一行 JSON（`rid`, `question`, `subject`, `draft_rubric`, `ref_responses`）
- `data/baseline.json` — 草稿 rubric 的基线指标（维度数、准则数、满分分布等）

输出示例：

```
453 条          draft 解析失败 0
维度去重数      1        全部「知识正确性」
准则数/题       2–62     均值 6.1
每题满分        10–61    中位 21
负向项个数      444 题 1 条 / 9 题 2 条
参考回复数      389 题 2 条 / 64 题 1 条
```

### Phase 1–4：见 [docs/PLAN.md](../docs/PLAN.md)

**先跑 20 条试验**（约 400 次调用），调通 prompt 和 schema，确认 RET 能把维度从 1 涨到 ≥4。不达标就没必要跑全量。

---

## 架构特点

1. **每步独立读写 jsonl**：`s02_context.py` 读 `seed.jsonl` 写 `s02_context.jsonl`，任一步挂了可单独重跑
2. **按内容哈希缓存**：LLM 调用结果缓存在 `cache/<stage>/<hash>.json`，调 prompt 只重算受影响的记录
3. **无 pip 依赖**：本机没 pip 也能跑，便于直接搬到开发机
4. **异质性校验**：启动时检查 `models.json` 的 family 约束，违反即报错

---

## 已知问题

1. **xlsx 只支持 inlineStr 写出**：大单元格会慢（当前 453 条无压力）
2. **LLM 客户端无流式输出**：长生成会等整个 response 完成才返回
3. **并发无全局限流**：`parallel_map` 只在单步内并发，跨步串行

---

## 开发状态

- ✅ Phase 0 已跑通，453 条基线已产出
- 🚧 Phase 1–4 待实现（需模型端点）
- 📋 完整流程设计见 [docs/rubric_pipeline_full_v2.md](../docs/rubric_pipeline_full_v2.md)
