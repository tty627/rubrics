# Rubric 自动生成流水线

把一道题自动转成一份可逐条二元判定的评分标准（rubric），用于评估模型回复质量、挖掘 bad case。

骨架取自三篇论文：Qworld 的 RET 递归展开（arXiv:2603.23522）、RubricHub 的三阶段生成（arXiv:2601.08430）、RIFT 的失效模式诊断（arXiv:2604.01375），另加了一层题型路由（verifiable / open / hybrid）。

纯标准库实现，无第三方依赖（Python 3.12+）。LLM 调用走自建的 OpenAI 兼容客户端，带磁盘缓存与并发控制。

## 快速开始

### 1. 环境要求

- Python 3.12+，无需 pip install（零第三方依赖）
- 可访问的 OpenAI 兼容模型端点（生成 / 判分两类模型，见 `config/models.json.example`）

### 2. 配置模型端点

```bash
cp config/models.json.example config/models.json   # 填入实际 base_url / api_key
python3 scripts/check_before_run.py               # 检查数据与配置就绪
```

### 3. 准备输入数据

把题目表放到 `data/input.xlsx`（列：A=need_rewrite, B=rewritten, C=gen_rubric, D=question, E=dimension, F=draft_rubric, G=ref_response），然后：

```bash
python3 stages/s00_seed.py        # xlsx → data/seed.jsonl + baseline.json（草稿基线）
```

### 4. 跑流水线

**从头跑**（结构线：过滤 → 上下文 → 题型路由 → 视角展开）：

```bash
python3 stages/s01_filter.py
python3 stages/s02_context.py
python3 stages/s02b_route.py
RP_RET=lean python3 stages/s03_perspective.py   # lean 主线必须显式设 lean
bash scripts/rerun_lean_fixed.sh                # 准则生成 + RIFT 诊断处置 + 导出（清缓存加 RP_CLEAN=1）
```

**Phase 4 实测**（回复池 + 判分 + 区分度诊断，只跑双回复题；LLM 密集）：

```bash
bash scripts/rerun_phase4.sh
```

**检查点 2**（新 rubric vs 草稿的 pairwise 放行闸门）：

```bash
bash scripts/rerun_checkpoint2.sh
```

### 5. 审计与单测

```bash
python3 scripts/audit_rubrics.py                # 交付档结构与可判定性审计
python3 tests/test_rubric.py                    # 语义核心单测（零 LLM，改 lib/rubric.py 前必跑）
make check                                      # 以上两条 + 全仓编译检查
```

每步独立读写 `data/` 下的 jsonl，任一步可单独重跑。LLM 调用按 `model + prompt + params` 哈希缓存到 `cache/<stage>/`，改一个 prompt 只重算受影响的哈希，命中缓存时整段秒级。

## 输出物

| 文件 | 说明 |
|------|------|
| `outputs/rubrics_advisor_lean.jsonl` | 交付档：每行一题，452 题 × 准则数组 |
| `outputs/rubrics_internal.jsonl` | 内部档：额外带血缘、RIFT 诊断、质量标记（`--full` 导出） |
| `outputs/excel/*.xlsx` | 交付档同源的人读版，C 列填 rubric，保留原格式 |
| `data/s11e_all452.jsonl` | 流水线末端数据源（跑过 Phase 4 后） |

交付档准则字段：`criteria`（判定文本）、`score`（原始整数权重，正向 1-3、答案项 6-8、负向 -2/-3）、`reason`、`dimension`、`is_positive`（方向）、`is_gate`（gated_answer 题的答案阀门）、`severity` / `is_veto`（只挂负项；veto 命中 → 整题得分率 0）。`full_mark = sum(正向 score)`。看样例：`head -1 outputs/rubrics_advisor_lean.jsonl | python3 -m json.tool`。

## 一键脚本

| 脚本 | 覆盖范围 | 说明 |
|------|---------|------|
| `rerun_lean_fixed.sh` | s04_rubric → 诊断处置 → 导出 | 结构线一键；前四步（s00-s03）需先跑 |
| `rerun_phase4.sh` | 回复池 → 判分 → 处置闭环 → 交付 | Phase 4 实测全量；判分器/veto 复判器默认 cn-judge / cn-veto |
| `rerun_checkpoint2.sh` | 草稿判分 + pairwise 对比 | 放行闸门；判分器固定 cn-judge |

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
| `RP_CLEAN` | 0 | 重跑脚本清缓存 |
| `RP_EVENTS` | `cache/_events.jsonl` | 调用事件流水，设空串关闭 |
| `RP_EV_CHARS` / `RP_EV_MAX` | 400 / 64 | 流水留字数 / 滚存阈值 MB |
| `RP_<STAGE>_SRC` | 各步默认 | 换输入源，如 `RP_S04LB_SRC` |

## 命名规则

stage 文件命名 `sNN[a-e]_语义词.py`：`NN` = PLAN.md 14 步计划里的步骤位，`a-e` = 同一步骤内的子步（按执行顺序）。数据文件默认名跟随 stage 前缀且不带基数（`s12_judged.jsonl`），具体数据集由 rerun 脚本传带基数的 env。

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

## 仓库布局

```
stages/      20 个 stage，命名 sNN[a-e]_语义词.py
lib/         基础库 7 个：xlsx / llm / config / stage / dimensions / rubric / answer_check
scripts/     辅助脚本：导出、审计、xlsx 填充、三个一键重跑
tests/       语义核心纯逻辑单测（零 LLM）
config/      模型端点配置（models.json 含 api_key，已 gitignore）
docs/        design/ 流程定稿与实施计划；reports/ 技术报告
legacy/      已归档的旧实现，保留可运行状态
data/ outputs/ cache/ logs/   运行产物（均 gitignore）
```

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
