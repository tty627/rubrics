# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rubric 生成能力建设：基于学术论文实现的评估标准(rubric)自动生成流水线。骨架取自 Qworld RET 递归展开(arXiv:2603.23522)，嫁接 RubricHub 三阶段生成(arXiv:2601.08430)，诊断采用 RIFT 八失效模式(arXiv:2604.01375)。

## Core Architecture

### 三层组织结构

1. **lib/** - 基础库，纯标准库实现，零第三方依赖
   - `xlsx.py`: 直接解析 xlsx 文件(zipfile + ElementTree)，无需 openpyxl/pandas
   - `llm.py`: OpenAI 兼容客户端，带磁盘缓存(按 model+prompt+params 哈希)、并发控制、重试

2. **stages/** - 14 步流水线，每步独立读写 jsonl
   - 每步可单独重跑，不影响其他步骤
   - 当前仅实现 Phase 0: `s00_seed.py` (xlsx → seed.jsonl + baseline.json)

3. **config/** - 模型端点配置
   - `models.json`: 运行时配置，含 api_key，已在 .gitignore
   - `models.json.example`: 配置模板

### 数据流

```
xlsx (input.xlsx)
  → s00_seed.py → seed.jsonl + baseline.json
  → s02_context.py → s02_context.jsonl
  → ... (14 个 stage，逐步产出中间 jsonl)
  → rubrics_final.jsonl + filled.xlsx
```

每步输出在 `data/` 下，缓存在 `cache/<stage>/<hash>.json`。

## Commands

### Phase 0 (已实现)

```bash
# 运行 Phase 0: 将 xlsx 转为种子集 + 计算基线指标
python3 stages/s00_seed.py

# 指定输入 xlsx 路径(默认 data/input.xlsx)
RP_XLSX=/path/to/input.xlsx python3 stages/s00_seed.py

# 指定输出目录(默认 data/)
RP_OUT=/path/to/output python3 stages/s00_seed.py
```

产出：
- `data/seed.jsonl`: 453 条记录，每条含 rid、question、subject、draft_rubric、ref_responses
- `data/baseline.json`: 草稿 rubric 的结构指标(维度数、准则数、满分分布、负向项统计)

### Phase 1-4 (待实现)

后续 phase 需要配置 `config/models.json`(见下文"模型端点配置")，详见 `docs/PLAN.md`。

## Development Workflow

### 添加新 stage

1. 在 `stages/` 下创建 `sXX_<name>.py`(XX 为步骤编号，如 02、03)
2. 从 `data/` 读前序步骤的 jsonl，写本步输出到新 jsonl
3. LLM 调用走 `lib.llm.call(model, messages, stage='sXX')`:
   - `stage` 参数决定缓存子目录，便于按步清理缓存
   - 返回 `(text, meta)`，meta 含 `cached`、`model`、`usage`
4. 若需并发，用 `lib.llm.parallel_map(fn, items, workers=8)`

### 模型端点配置

复制 `config/models.json.example` 为 `config/models.json`，填入实际端点：

```json
[
  {
    "name": "gen-main",           // 内部标识
    "model_id": "...",            // 传给 /v1/chat/completions 的 model 字段
    "base_url": "http://...:8000/v1",  // vLLM 等 OpenAI 兼容服务
    "api_key": "EMPTY",           // 无鉴权填 EMPTY
    "family": "qwen",             // 厂商/系列，用于异质性校验
    "roles": ["generator"],       // 角色标签
    "timeout": 180
  }
]
```

**硬约束**(流程强制要求，见 `docs/PLAN.md` §1)：
- 步骤 6(多模型聚合): 需 ≥2 个 `generator`，且 `family` 不同(同系列共享盲区)
- 步骤 11(RIFT 诊断): `diagnoser` 需异质组合(Gemini 系强于 Ungrounded/Subjective，GPT 系强于 Missing/Low Signal)
- 步骤 12(判分): `judge` 的 `family` 必须不同于 `generator`(避免自偏好偏差)

### 缓存机制

- 缓存键 = sha256(model_id + base_url + messages + temperature + max_tokens + extra)[:32]
- 路径: `cache/<stage>/<hash>.json`
- 调 prompt 后，只重算受影响的哈希；未变的调用直接命中缓存
- 清理缓存: `rm -rf cache/sXX/` (按 stage) 或 `rm -rf cache/` (全部)

### 监视面板

提供两个版本：

**watch_v2.py（推荐，概览模式）** - 三层结构，信息密度适中：
```bash
python3 tools/watch_v2.py        # 全屏刷新，Ctrl-C 退出
python3 tools/watch_v2.py --once # 一次性快照
python3 tools/watch_v2.py --all  # 展开所有步骤（含未开始/已完成）
```

显示内容：
- **状态栏**：端点健康度 | 当前任务 | 关键指标
- **进度表**：活跃步骤（带速率趋势 ↑↓→）+ 折叠已完成/未开始
- **资源统计**：Token用量 + 成本估算 + 端点负载 + 调用成功率

**watch.py（详尽模式）** - 8区块，适合深度调试：
```bash
python3 tools/watch.py          # 全屏刷新
python3 tools/watch.py --once   # 快照
python3 tools/watch.py --tokens # 展开 token 明细
```

额外显示：进程详情（pid/socket/环境变量）、在飞请求的完整prompt/output、最近完成的详细内容、产出文件列表。

详细说明见 `tools/README_watch.md`。

**共同特性**：
- 只读，不影响在跑的进程
- 全屏模式走终端备用屏(像 vim/htop)，退出后原内容恢复
- 自动发现 `cache/` 下的新步骤，无需修改面板代码
- 数据来源：`cache/<stage>/*.json` + `cache/_events.jsonl` + 端点 `/metrics`

**端点侧 token 有多副本坑**：某些 URL 后面挂多个副本，`/metrics` 随机命中一个，累计计数器会跳动。面板靠「计数器只增不减」识别副本并求和。

## Critical Constraints

以下五条违反即出错(详见 `docs/rubric_pipeline_full_v2.md` §3.3)：

1. **锚定回复 ≠ 待评回复**(步骤 5): 锚和待评同源会导致 rubric 从待评回复自身衍生
2. **判分器 ≠ 生成器**(步骤 12): 同系列模型有自偏好偏差，判分虚高
3. **锚点集 ∉ 训练集**(步骤 14): 锚点集一旦参与训练就不再独立，失去参照作用
4. **血缘标签必须在步骤 4 挂**: 用于回溯每条准则来自哪个 Scenario，后续诊断依赖此标签
5. **闸门项不进 S_max 分母**(步骤 9): 闸门是 0/1 判定，不参与满分归一化

## Implementation Status

当前已完成：
- ✅ Phase 0 数据层(s00_seed.py)
- ✅ 基础库(xlsx.py, llm.py)

待实现(按优先级)：
- Phase 1 试跑(20 条，约 400 次调用): 步骤 1-9(跳过 6、10、11 的需池项)
- Phase 2 结构全量(453 条，约 9k-12k 次): 步骤 1-9 + 11 的免池诊断
- Phase 3 多模型聚合 + RIFT(约 +10k-14k 次)
- Phase 4 回复池 + 判分(约 +10k-15k 次)

## Key Design Decisions

### 题型路由(步骤 2.5，新增)

判定 query 属于 verifiable(可验证) / open(开放) / hybrid(混合)，路由到不同 rubric 形态：

| 题型 | rubric_form | RET 执行策略 | 原因 |
|------|-------------|-------------|------|
| verifiable | `gated_answer` | 固定 3 视角，不跑 R_w | 数学题/代码题本质是 k=1 单准则，强行多维展开会稀释主准则 |
| open | `analytic` | 完整 RET(R_h + R_w) | 创作/建议/分析题需多维度覆盖 |
| hybrid | `multi_part` | 分 block 处理 | 每子题独立判型后选策略 |

这是对三篇骨架论文的扩展(它们假定所有任务都是 open)。

### RET 实现策略(`docs/PLAN.md` §3.1)

**建议**: R_h(层次展开)批量、R_w(水平展开「还漏了什么」)忠实。
- 批量: 一次调用出场景+视角骨架 → 约 2 次/题
- 忠实: R_w 单独调用，保住「覆盖全面性」的核心价值 → 约 5-6 次/题
- Phase 1 会对比两种策略的维度数，择优

### 单回复记录处理(`docs/PLAN.md` §3.2)

种子集中 64 条只有 1 条参考回复(无法做锚定与待评分离)：
- Phase 2/3(结构指标): 全部 453 条
- Phase 4(判分): 仅 389 条双回复记录

## Documentation

- `docs/rubric_pipeline_feishu_v2.md`: 流程定稿(14 步 + 题型判定)，简明版
- `docs/rubric_pipeline_full_v2.md`: 完整版，含论文依据、推导过程、逐步出处对照
- `docs/PLAN.md`: 在 453 条种子集上跑通全流程的分阶段实施计划，含成本估算与检查点
- `data/baseline.json`: 草稿 rubric 的基线结构指标(对照目标)

## Code Style

- 纯标准库，无第三方依赖(已在 Python 3.12.3 验证)
- 中文注释、中文变量名(仅核心概念如 `draft_rubric`、`ref_responses` 用英文)
- 每个 stage 脚本顶部注释说明其在流程中的位置(如 `"""Phase 0：xlsx → seed.jsonl..."""`)
- 错误处理: 解析失败计数但不中断，最后统一报告(如 `s00_seed.py` 中 `bad` 计数)

## Environment Variables

- `RP_XLSX`: 输入 xlsx 路径(默认 `data/input.xlsx`)
- `RP_OUT`: 输出目录(默认 `data/`)
- `RP_CACHE`: 缓存目录(默认 `cache/`)
- `RP_EVENTS`: 调用事件流水路径(默认 `cache/_events.jsonl`，设为空串可关闭)
- `RP_EV_CHARS`: 流水里输入/输出各留多少字(默认 400)
- `RP_EV_MAX`: 流水滚存阈值 MB(默认 64)

## Notes

- xlsx 的 A/B/C 列(need_rewrite, rewritten, gen_rubric)在 Phase 2+ 才填充
- `baseline.json` 中的指标是**要打败的对照**: 如维度去重数从 1 → ≥4、知识正确性占比从 100% → ≤50%
- Phase 1 的检查点是「单位 token 信息量最高」的验证: 若 RET 在散学科分布上导不出多样视角，整个骨架选择需重议

