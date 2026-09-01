#!/bin/bash
# 阶段 02：rubric 生成与结构修订（06→10），产物在阶段 10 冻结。
#
# 冻结之后候选回答才允许进入流程（见 CLAUDE.md 硬约束第 1 条）。本阶段不导出交付档 ——
# 交付档要带实测证据，由阶段 27 在实测线末端出，这里只产出冻结版 rubric。
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
mkdir -p data/rubric outputs/current cache/numbered
export RP_CACHE=${RP_CACHE:-$ROOT/cache/numbered}
export RP_DATA_ROOT=${RP_DATA_ROOT:-$ROOT/data}
[ -f data/tasks/05_evaluation_axes.jsonl ] || { echo 'Run pipeline/01_run_task_preparation.sh first'; exit 1; }

# 06-09 的产出文件名写死在脚本里（没有 RP_*_OUT），故逐步 cp 到编号路径。
RP_S04L_SRC=tasks/05_evaluation_axes.jsonl python3 pipeline/06_generate_rubric.py
cp data/s04_rubric.jsonl data/rubric/06_rubric_draft.jsonl

RP_S11L_SRC=rubric/06_rubric_draft.jsonl python3 pipeline/07_diagnose_rubric.py
cp data/s11_diagnosed.jsonl data/rubric/07_rubric_diagnosed.jsonl

RP_S11LB_SRC=rubric/07_rubric_diagnosed.jsonl python3 pipeline/08_apply_rubric_diagnosis.py
cp data/s11b_remedied.jsonl data/rubric/08_rubric_revised.jsonl
cp data/_defect_queue.jsonl data/rubric/08_criteria_rewrite_queue.jsonl

RP_S04LB_SRC=rubric/08_rubric_revised.jsonl \
    RP_S04LB_QUEUE=rubric/08_criteria_rewrite_queue.jsonl \
    python3 pipeline/09_rewrite_rubric_criteria.py
cp data/s04b_split.jsonl data/rubric/09_rubric_criteria_rewritten.jsonl

RP_S04LC_SRC=rubric/09_rubric_criteria_rewritten.jsonl \
    RP_S04LC_OUT=rubric/10_negative_criteria_classified.jsonl \
    python3 pipeline/10_classify_negative_criteria.py

# 冻结点。阶段 03 以这个文件为入口，改名要同步改 lib/paths.py 与 03 的 FROZEN。
cp data/rubric/10_negative_criteria_classified.jsonl data/rubric/10_frozen_rubric.jsonl
echo "rubric 已冻结: data/rubric/10_frozen_rubric.jsonl"
