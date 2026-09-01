# Rubric Pipeline

Rubric 自动生成、实测和交付流水线。当前主线使用稳定的分段编号：

- 01-05：题目准备
- 06-10：rubric 生成和结构修订
- 20-27：候选回答实测和反馈修订
- 30-31：发布检查和交付

## 零起点生成

**rubric 只从题目本身和题面明确约束生成**，不存在既定 rubric、草稿 rubric 或
参考 rubric 作为输入。这不是某种降级模式，而是唯一模式 —— 流程里没有开关可以
切换到「有参考 rubric」那条路。

可核验题（数学、代码、选择）的答案由 `grounder` 角色现场解出并经异源交叉复核，
详见下方 Model Roles。

## Quick Start

配置 config/models.json，并将输入放在 data/input.xlsx。若原始数据包含完整 messages，必须通过 `RP_SOURCE_JSONL` 提供原始会话 JSONL，以保留 system/developer/user 约束：

```bash
export RP_SOURCE_JSONL=/path/to/generation.jsonl
```

未提供时，Stage 01 会把记录明确标为 `task_message_status=question_only`；同一用户题目匹配多个会话时标为 `ambiguous`，不会猜选。然后运行：

```bash
bash pipeline/00_run_all.sh
```

分段运行：

```bash
bash pipeline/01_run_task_preparation.sh
# 或从已构建 seed 开始：RP_SEED_ONLY=/path/to/seed.jsonl bash pipeline/01_run_task_preparation.sh
bash pipeline/02_run_rubric_generation.sh
bash pipeline/03_run_response_evaluation.sh
bash pipeline/04_run_release_verification.sh
```

Makefile 入口：

```bash
make all
make tasks
make rubric
make evaluate
make release
make check
```

## Data Boundary

候选 AI 回答是 rubric 冻结后要评分的对象，不是 rubric 生成输入。

```text
题目 + 题面明确约束
  -> 01-05 题目准备
  -> 06-10 rubric 生成并冻结
  -> 20-27 候选回答实测与交付导出
  -> 30-31 发布检查
```

01-10 不得读取 ref_responses、候选回答锚点或从候选回答抽出的 canonical answer。完整 system/user 约束属于题目输入，应保存在 task context 中。候选回答只允许进入 rubric 冻结后的评测阶段。

## Numbered Stages

### 01-05 Task Preparation

| Stage | Entry | Output |
|---:|---|---|
| 01 | pipeline/01_build_task_dataset.py | data/tasks/01_task_dataset.jsonl |
| 02 | pipeline/02_filter_tasks.py | data/tasks/02_filtered_tasks.jsonl |
| 03 | pipeline/03_extract_task_context.py | data/tasks/03_task_context.jsonl |
| 04 | pipeline/04_classify_task_type.py | data/tasks/04_task_types.jsonl |
| 05 | pipeline/05_generate_evaluation_axes.py | data/tasks/05_evaluation_axes.jsonl |

### 06-10 Rubric Generation

| Stage | Entry | Output |
|---:|---|---|
| 06 | pipeline/06_generate_rubric.py | data/rubric/06_rubric_draft.jsonl |
| 07 | pipeline/07_diagnose_rubric.py | data/rubric/07_rubric_diagnosed.jsonl |
| 08 | pipeline/08_apply_rubric_diagnosis.py | data/rubric/08_rubric_revised.jsonl |
| 09 | pipeline/09_rewrite_rubric_criteria.py | data/rubric/09_rubric_criteria_rewritten.jsonl |
| 10 | pipeline/10_classify_negative_criteria.py | data/rubric/10_negative_criteria_classified.jsonl |

阶段 10 之后 rubric 冻结，候选回答才允许进入流程。

### 20-27 Response Evaluation

| Stage | Responsibility | Output |
|---:|---|---|
| 20 | 定权威答案：`grounder` 出解 + 2 个异源交叉复核，三方一致才准入 | data/evaluation/20_answer_resolved_tasks.jsonl / 20_evaluation_tasks.jsonl |
| 21 | Build response pool | data/evaluation/21_response_pool.jsonl |
| 22 | Score response pool | data/evaluation/22_response_scores.jsonl |
| 23 | Diagnose discrimination | data/evaluation/23_discrimination_diagnostics.jsonl |
| 24 | Revise from measurement | data/evaluation/24_rubric_measurement_revision_rNN.jsonl |
| 25 | Select best revision | data/evaluation/25_selected_rubrics.jsonl |
| 26 | Build measured delivery source | data/evaluation/26_rubric_delivery_source.jsonl |
| 27 | 导出交付档 + 内部档 | outputs/current/rubric_delivery.jsonl / rubric_internal.jsonl |

### 30-31 Release Verification

| Stage | Responsibility | Output |
|---:|---|---|
| 30 | Export xlsx | outputs/current/rubric_delivery.xlsx |
| 31 | Audit delivery | data/release/31_delivery_audit.txt |

没有「新 rubric vs 草稿 rubric」的 pairwise 闸门：本线不存在草稿 rubric，
对比无对象。放行判据是阶段 04 的四步闸门（结构完整性、数据边界、导出、审计 + 单测）。

## Output Layout

```text
data/
  tasks/       01-05 task artifacts
  rubric/      06-10 rubric artifacts
  evaluation/  20-27 measured evaluation artifacts
  release/     30-31 release artifacts

outputs/
  current/
    rubric_delivery.jsonl
    rubric_internal.jsonl
    rubric_delivery.xlsx
    run_manifest.json
  runs/<run_id>/
    immutable delivery snapshot and manifest

cache/
  按阶段名分目录，清单个阶段的缓存直接删对应目录
```

题量不写进文件名。task_count、evaluation_task_count、delivery_task_count、Git commit 和 run_id 写在 outputs/current/run_manifest.json。

## Delivery Schema

每行一题，rubrics 包含 criteria、score、reason、dimension、is_positive、is_gate。负项可带 severity 和 is_veto。full_mark 等于所有正向 score 之和；任一已确认 veto 命中时整题最终得分率为 0。

## Model Roles

`config/models.json` 按角色绑定端点，缺角色直接报错，不静默退回其他角色
（退回会让 mid 档与 strong 档同模型，档序失效且不可见）。

| 角色 | 用途 | 硬约束 |
|---|---|---|
| grounder | 可核验题的权威答案源（阶段 20、24 的锚） | family 必须是闭源厂商（anthropic / openai / google） |
| generator | rubric 生成与重写 | ≥2 个且 family 互异 |
| diagnoser | RIFT 诊断 | family 异质组合 |
| judge | 判分 | family 必须异于 generator |
| veto | veto 复核第二票 | family 异于生成器与判分器 |
| pool_mid / pool_weak | 回复池中档 / 弱档 | 与 strong 档不同模型 |

grounder 限定闭源最强模型的原因：rubric 只从题目生成，可核验题的答案对不对
没有外部答案可比，全靠这个模型；自建开源端点答错了没人拦，而阶段 22 的程序化
核验无条件相信 `answer_canonical`。三方一致（权威 + 2 异源）才准入，分歧的题
写进 `data/_answer_dispute.jsonl` 并挂起，不参与判分 —— 多数票会在两个模型共享
盲区时把错答案锁死。

## Validation

```bash
make check
python3 pipeline/31_audit_rubric_delivery.py outputs/current/rubric_delivery.jsonl
```

LLM stage 失败会写入 data/_stage_errors.jsonl 和对应产物的错误字段。发布前同时检查 manifest、审计结果和测试，不能只比较 JSONL 行数。

`make check` 当前 33 个测试（1 个 skip）。审计脚本 `31_audit_rubric_delivery.py`
只出报告、不以非零码失败，runner 里也带 `|| true`，所以它是观测手段不是闸门 ——
放行判据看阶段 04 的前两步（结构完整性、数据边界）和单测。

已知缺陷（含 `RP_RUN_ID` 不传子进程、manifest 里 audit 路径写成 33）
记在 `CLAUDE.md` 的「已知缺陷」一节。

`docs/reports/` 下是带日期的历史报告，其中的 `stages/` 布局已不存在，按史料读，
不要照着改代码；当前布局见 `CLAUDE.md`「仓库布局」。
