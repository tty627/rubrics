# Rubric 自动生成流水线

把一道题自动转成一份可逐条二元判定的评分标准（rubric），用于评估模型回复质量、挖掘 bad case。

骨架取自三篇论文：Qworld 的 RET 递归展开（arXiv:2603.23522）、RubricHub 的三阶段生成（arXiv:2601.08430）、RIFT 的失效模式诊断（arXiv:2604.01375），另加一层题型路由（verifiable / open / hybrid）。

纯标准库实现，无第三方依赖（Python 3.12+）。LLM 调用走自建的 OpenAI 兼容客户端，带磁盘缓存与并发控制。

## 快速开始

### 1. 环境要求

- Python 3.12+，无需 pip install（零第三方依赖）
- 可访问的 OpenAI 兼容模型端点（生成 / 判分两类模型，见 `config/models.json.example`）

### 2. 配置模型端点

```bash
cp config/models.json.example config/models.json   # 填入实际 base_url / api_key
```

### 3. 准备输入数据

把题目表放到 `data/input.xlsx`（列：A=need_rewrite, B=rewritten, C=gen_rubric, D=question, E=dimension, F=draft_rubric, G=ref_response）。

如果数据是 OpenCompass 线上日志格式（`generation_ol_*.jsonl`，多轮 messages + label 标注），用转换脚本：

```bash
python3 scripts/convert_ol_logs.py --src <日志.jsonl> --out-dir <输出目录>
# 产出 input.xlsx + seed.jsonl（两者 s00_seed 往返一致）+ labels.jsonl（人工标注 sidecar）+ report.json
```

映射规则：question ← 第一条 user 消息；ref_responses ← assistant 消息（取 2 条最长的，双回复才能进 Phase 4）；subject ← label.domain；draft_rubric 留空（原数据无草稿，检查点 2 的对照需另行补）。

### 4. 一键跑全流程

```bash
bash scripts/rerun_all.sh            # 或 make all
RP_CLEAN=1 bash scripts/rerun_all.sh # 同时清结构线缓存（全部 LLM 调用重算）
```

一条命令跑完：种子 → 结构线 → 交付导出 → Phase 4 实测 → 检查点 2 → xlsx 填充 → 审计 → 单测。中途中断可随时重跑同一条命令，LLM 缓存保证已完成的部分不重复计费。

默认模型（均可被同名环境变量覆盖，判分器/veto 必须异源，各步启动时校验）：

| 角色 | 环境变量 | 默认 |
|------|---------|------|
| 生成（s01-s04） | `RP_M_GEN` / `RP_M_FILTER` / `RP_M_ROUTE` | glm-ac |
| 锚定 grounding（s05） | `RP_M_GROUND` | deepseek（原全量口径 by-ground） |
| 负项分级 | `RP_M_S04LC` | cn-judge |
| 判分 / 草稿判分 | `RP_M_JUDGE` | cn-judge |
| veto 复判（第二票） | `RP_M_VETO` | cn-veto |
| 处置重写 | `RP_M_S11LD` | cn-gen |
| 回复池复核 | `RP_M_POOL_CHECK` | cn-judge |

### 5. 分步跑（调试用）

```bash
python3 stages/s00_seed.py && python3 stages/s01_filter.py && \
  python3 stages/s02_context.py && python3 stages/s02b_route.py && \
  RP_RET=lean python3 stages/s03_perspective.py   # 结构线前置
bash scripts/rerun_lean_fixed.sh                  # 结构线主体 + 导出
bash scripts/rerun_phase4.sh                      # Phase 4 实测
bash scripts/rerun_checkpoint2.sh                 # 检查点 2 放行闸门
```

### 6. 审计与单测

```bash
make check                                        # 全仓编译检查 + 语义核心单测
python3 scripts/audit_rubrics.py                  # 交付档审计
```

每步独立读写 `data/` 下的 jsonl，任一步可单独重跑。LLM 调用按 `model + prompt + params` 哈希缓存到 `cache/<stage>/`，改一个 prompt 只重算受影响的哈希。

## 项目结构

### 目录

```
rubrics/
├── stages/         流水线 20 个 stage，每步独立读写 data/*.jsonl，任一步可单独重跑
├── lib/            基础库 7 个：xlsx / llm / config / stage / dimensions / rubric / answer_check
├── scripts/        一键与分步入口、导出、审计、xlsx 填充
├── tests/          语义核心纯逻辑单测（零 LLM）：test_rubric / test_s04_flags
├── config/         模型端点配置（models.json 含 api_key，gitignore；example 为模板）
├── docs/           design/ 流程定稿与实施计划；reports/ 技术报告
├── legacy/         已归档的旧实现（full_path / phase3 / phase4），保留可运行状态
├── data/           中间产物与种子（gitignore，随侧车 tar 走）
├── outputs/        交付档 + 内部档 + 填充后的 xlsx（gitignore）
├── cache/          LLM 调用缓存，按 stage 分目录（gitignore）
├── logs/           运行日志（gitignore）
├── Makefile        常用入口：make all / check / seed / phase4 / checkpoint2 / export
└── pyproject.toml  项目元数据（纯标准库，零依赖）
```

### 流水线

14 步流程（stage 编号 = PLAN.md 的步骤位），实际实现分两条线：

**结构线**（452 题全量）：

```
data/input.xlsx
  ↓ s00_seed        xlsx → seed.jsonl + baseline.json（草稿基线指标）
  ↓ s01_filter      过滤
  ↓ s02_context     intent + scenarios
  ↓ s02b_route      题型路由（步骤 2.5）→ question_type + rubric_form + blocks
  ↓ s03_perspective RET 视角展开（RP_RET=lean）
  ↓ s05_ground      锚定 grounding：抽 anchors / answer_canonical / anchor_key
  ↓ s04_rubric      准则直出（读 s05_grounded，带锚生成；含血缘 + 质量标记）
  ↓ s11_diagnose    RIFT 四失效模式诊断
  ↓ s11b_remedy     诊断后分级处置
  ↓ s04b_split      拆非原子 + 事实纠错 + 标记重写
  ↓ s04c_severity   负项 severity 分级 + veto 标记
  ↓ export_advisor_schema.py → outputs/rubrics_advisor_lean.jsonl 交付档
```

**Phase 4 实测线**（388 双回复题）：

```
s04c_severity.jsonl（452）
  ↓ 筛双回复        s04c_phase4.jsonl（单回复题按硬约束 1 排除）
  ↓ s10_pool        6 档回复池（strong / mid / trunc / cut / weak / adv）
  ↓ s12_judge       判分（veto 两票制 + 同源一致性修正）
  ↓ s11c_consequential  区分度诊断（Hackable / LowSignal / floor）
  ↓ s11d_remedy ⇄ s12_judge 重判 ⇄ s11c_consequential 复诊（×3 轮闭环）
  ↓ s11e_select     各轮实测证据里挑每题最优
  ↓ s04c_severity（补分级）→ 合并 64 单回复题 → s11e_all452.jsonl ← 最终交付源
  ↓ s12b_draft_judge  草稿 rubric 判分（检查点 2）
  ↓ s12c_pairwise     新 vs 草稿 pairwise 放行闸门（检查点 2）
```

### stage 职责表

| stage | 职责 |
|---|---|
| s00_seed | xlsx → 种子集 + 草稿基线 |
| s00b_sample / s00c_pilot | 抽样 / 试点工具 |
| s01_filter | 题目过滤 |
| s02_context | intent + scenarios |
| s02b_route | 题型路由（步骤 2.5） |
| s03_perspective | RET 视角展开 |
| s04_rubric | 准则直出（含血缘 + 质量标记） |
| s04b_split | 拆非原子 + 事实纠错 |
| s04c_severity | 负项分级 + veto 标记 |
| s05_ground | 锚定 grounding（s03 之后、s04 之前，抽锚点与标准答案） |
| s10_pool | 6 档回复池 |
| s11_diagnose | RIFT 失效模式诊断 |
| s11b_remedy | 诊断后分级处置 |
| s11c_consequential | 区分度诊断 |
| s11d_remedy | 实测闭环处置 |
| s11e_select | 终态选择（挑每题最优） |
| s12_judge | 判分（veto 两票制） |
| s12b_draft_judge | 草稿 rubric 判分（检查点 2） |
| s12c_pairwise | 新 vs 草稿 pairwise 闸门（检查点 2） |

## 输出物

| 文件 | 说明 |
|------|------|
| `outputs/rubrics_advisor_lean.jsonl` | 交付档：每行一题，452 题 × 准则数组 |
| `outputs/rubrics_internal.jsonl` | 内部档：额外带血缘、RIFT 诊断、质量标记 |
| `outputs/excel/*.xlsx` | 交付档同源的人读版，C 列填 rubric，保留原格式 |
| `data/s11e_all452.jsonl` | 流水线末端数据源（跑过 Phase 4 后） |

交付档准则字段：`criteria`（判定文本）、`score`（原始整数权重，正向 1-3、答案项 6-8、负向 -2/-3）、`reason`、`dimension`、`is_positive`（方向）、`is_gate`（gated_answer 题的答案阀门）、`severity` / `is_veto`（只挂负项；veto 命中 → 整题得分率 0）。`full_mark = sum(正向 score)`。看样例：`head -1 outputs/rubrics_advisor_lean.jsonl | python3 -m json.tool`。

## 脚本入口

| 脚本 | 覆盖范围 | 说明 |
|------|---------|------|
| `rerun_all.sh` | 全流程 | **一键入口**：s00 → 交付 + Phase 4 + 检查点 2 + 审计单测 |
| `rerun_lean_fixed.sh` | s05_ground → s04 → 诊断处置 → 导出 | 结构线一键；前四步（s00-s03）需先跑 |
| `rerun_phase4.sh` | 回复池 → 判分 → 处置闭环 → 交付 | Phase 4 实测全量 |
| `rerun_checkpoint2.sh` | 草稿判分 + pairwise 对比 | 放行闸门 |

模型硬约束（脚本启动时校验）：判分器 family 必须 ≠ 生成器；veto 复判第二票 family 必须 ≠ 生成器与判分器。

## 环境变量

| 变量 | 默认 | 作用 |
|------|------|------|
| `RP_XLSX` | `data/input.xlsx` | 输入 xlsx |
| `RP_OUT` | `data/` | 中间产物目录 |
| `RP_CACHE` | `cache/` | 缓存目录 |
| `RP_WORKERS` | 20 | 并发数 |
| `RP_RET` | `hybrid` | RET 策略：lean / batch / hybrid / faithful。**lean 主线必须显式设 `lean`** |
| `RP_RUBRIC_MIN` / `MAX` | 6 / 8 | 每题准则条数预算 |
| `RP_CLEAN` | 0 | 一键/结构线脚本清缓存 |
| `RP_EVENTS` | `cache/_events.jsonl` | 调用事件流水，设空串关闭 |
| `RP_<STAGE>_SRC` | 各步默认 | 换输入源，如 `RP_S04LB_SRC` |

模型选择变量见「快速开始」第 4 步的默认模型表。

## 命名规则

stage 文件命名 `sNN[a-e]_语义词.py`：`NN` = PLAN.md 14 步计划里的步骤位，`a-e` = 同一步骤内的子步（按执行顺序）。数据文件默认名跟随 stage 前缀且不带基数（`s12_judged.jsonl`），具体数据集由脚本传带基数的 env。

2026-08-17 归一化改名对照（旧 → 新，供对照历史文档用）：

| 旧 | 新 | 旧 | 新 |
|---|---|---|---|
| s02_5_route | s02b_route | s11Lc_consequential | s11c_consequential |
| s04L_rubric | s04_rubric | s11Ld_remedy | s11d_remedy |
| s04Lb_split | s04b_split | s11Le_select | s11e_select |
| s04Lc_severity | s04c_severity | s12L_judge | s12_judge |
| s05L_ground | s05_ground | s12Lb_draft_judge | s12b_draft_judge |
| s10L_pool | s10_pool | s12Lc_pairwise | s12c_pairwise |
| s11L_diagnose | s11_diagnose | s11Lb_remedy | s11b_remedy |

## 参考论文

| 论文 | arXiv | 在本项目中的作用 |
|------|-------|----------------|
| Qworld | 2603.23522 | RET 递归展开（R_h 层次 + R_w 水平） |
| RubricHub | 2601.08430 | 三阶段生成流程 |
| RIFT | 2604.01375 | 失效模式诊断 |
| RaR | 2507.17746 | 题型判定理论 |
| QUBRIC | 2606.03968 | 准则措辞原则 |

## 文档

- `docs/design/rubric_pipeline_feishu_v2.md` — 流程定稿（14 步 + 题型判定），简明版
- `docs/design/rubric_pipeline_full_v2.md` — 完整版，含论文依据与逐步出处对照
- `docs/design/PLAN.md` — 分阶段实施计划，含成本估算与检查点
- `CLAUDE.md` — 开发上下文（架构、硬约束、修复记录）
