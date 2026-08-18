# Rubric Pipeline

Rubric 自动生成、实测和交付流水线。当前主线使用稳定的分段编号：

- 01-05：题目准备
- 06-11：rubric 生成和结构修订
- 20-26：候选回答实测和反馈修订
- 30-33：发布检查和交付

旧的 s04L、s11Ld、lean、388/452 文件名只作为兼容历史，不再是用户接口。

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
  -> 06-11 rubric 生成并冻结
  -> 20-26 候选回答实测
  -> 30-33 发布检查
```

01-11 不得读取 ref_responses、候选回答锚点或从候选回答抽出的 canonical answer。完整 system/user 约束属于题目输入，应保存在 task context 中。候选回答只允许进入 rubric 冻结后的评测阶段。

## Numbered Stages

### 01-05 Task Preparation

| Stage | Entry | Output |
|---:|---|---|
| 01 | pipeline/01_build_task_dataset.py | data/tasks/01_task_dataset.jsonl |
| 02 | pipeline/02_filter_tasks.py | data/tasks/02_filtered_tasks.jsonl |
| 03 | pipeline/03_extract_task_context.py | data/tasks/03_task_context.jsonl |
| 04 | pipeline/04_classify_task_type.py | data/tasks/04_task_types.jsonl |
| 05 | pipeline/05_generate_evaluation_axes.py | data/tasks/05_evaluation_axes.jsonl |

### 06-11 Rubric Generation

| Stage | Entry | Output |
|---:|---|---|
| 06 | pipeline/06_generate_rubric_draft.py | data/rubric/06_rubric_draft.jsonl |
| 07 | pipeline/07_diagnose_rubric.py | data/rubric/07_rubric_diagnosed.jsonl |
| 08 | pipeline/08_apply_rubric_diagnosis.py | data/rubric/08_rubric_revised.jsonl |
| 09 | pipeline/09_rewrite_rubric_criteria.py | data/rubric/09_rubric_criteria_rewritten.jsonl |
| 10 | pipeline/10_classify_negative_criteria.py | data/rubric/10_negative_criteria_classified.jsonl |
| 11 | export | data/rubric/11_rubric_delivery_source.jsonl |

### 20-26 Response Evaluation

| Stage | Responsibility | Output |
|---:|---|---|
| 20 | Independently resolve canonical answers (`solver`, fallback `judge`), then select measurable tasks | data/evaluation/20_answer_resolved_tasks.jsonl / 20_evaluation_tasks.jsonl |
| 21 | Build response pool | data/evaluation/21_response_pool.jsonl |
| 22 | Score response pool | data/evaluation/22_response_scores.jsonl |
| 23 | Diagnose discrimination | data/evaluation/23_discrimination_diagnostics.jsonl |
| 24 | Revise from measurement | data/evaluation/24_rubric_measurement_revision_rNN.jsonl |
| 25 | Select best revision | data/evaluation/25_selected_rubrics.jsonl |
| 26 | Build measured delivery source | data/evaluation/26_rubric_delivery_source.jsonl |

### 30-33 Release Verification

| Stage | Responsibility | Output |
|---:|---|---|
| 30 | Score draft rubric | data/release/30_draft_rubric_scores.jsonl |
| 31 | Pairwise comparison | data/release/31_pairwise_comparison.jsonl |
| 32 | Export xlsx | outputs/current/rubric_delivery.xlsx |
| 33 | Audit delivery | data/release/33_delivery_audit.json |

## Output Layout

```text
data/
  tasks/       01-05 task artifacts
  rubric/      06-11 rubric artifacts
  evaluation/  20-26 measured evaluation artifacts
  release/     30-33 release artifacts

outputs/
  current/
    rubric_delivery.jsonl
    rubric_internal.jsonl
    rubric_delivery.xlsx
    run_manifest.json
  runs/<run_id>/
    immutable delivery snapshot and manifest

cache/
  numbered semantic stage directories for new runs
  existing sXX directories are legacy caches
```

题量不写进文件名。task_count、evaluation_task_count、delivery_task_count、Git commit 和 run_id 写在 outputs/current/run_manifest.json。

## Delivery Schema

每行一题，rubrics 包含 criteria、score、reason、dimension、is_positive、is_gate。负项可带 severity 和 is_veto。full_mark 等于所有正向 score 之和；任一已确认 veto 命中时整题最终得分率为 0。

## Compatibility

旧 stages/sXX_*.py、scripts/rerun_*.sh 和平铺 data/sXX*.jsonl 暂时保留兼容。新代码、文档和自动化只使用 pipeline/ 编号入口和规范目录。

映射表：docs/design/STAGE_MIGRATION_MAP.md

完整流程说明：docs/design/NUMBERED_PIPELINE.md

历史实现位于 legacy/，不属于当前交付主线，不得作为 outputs/current 的数据源。

## Validation

```bash
make check
python3 pipeline/33_audit_rubric_delivery.py outputs/current/rubric_delivery.jsonl
```

LLM stage 失败会写入 data/_stage_errors.jsonl 和对应产物的错误字段。发布前同时检查 manifest、审计结果和测试，不能只比较 JSONL 行数。
