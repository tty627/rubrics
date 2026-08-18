#!/bin/bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
mkdir -p data/rubric outputs/current cache/numbered
export RP_CACHE=${RP_CACHE:-$ROOT/cache/numbered}
[ -f data/tasks/05_evaluation_axes.jsonl ] || { echo 'Run pipeline/01_run_task_preparation.sh first'; exit 1; }
cp data/tasks/05_evaluation_axes.jsonl data/s03_perspective_lean.jsonl
RP_S04L_SRC=s03_perspective_lean.jsonl python3 pipeline/06_generate_rubric_draft.py
cp data/s04_rubric.jsonl data/rubric/06_rubric_draft.jsonl
python3 pipeline/07_diagnose_rubric.py
cp data/s11_diagnosed.jsonl data/rubric/07_rubric_diagnosed.jsonl
python3 pipeline/08_apply_rubric_diagnosis.py
cp data/s11b_remedied.jsonl data/rubric/08_rubric_revised.jsonl
cp data/_defect_queue.jsonl data/rubric/08_criteria_rewrite_queue.jsonl
python3 pipeline/09_rewrite_rubric_criteria.py
cp data/s04b_split.jsonl data/rubric/09_rubric_criteria_rewritten.jsonl
python3 pipeline/10_classify_negative_criteria.py
cp data/s04c_severity.jsonl data/rubric/10_negative_criteria_classified.jsonl
cp data/s04c_severity.jsonl data/rubric/11_rubric_delivery_source.jsonl
python3 pipeline/11_export_rubric_delivery.py --src data/rubric/11_rubric_delivery_source.jsonl --out outputs/current/rubric_delivery.jsonl --full
[ -f outputs/rubrics_internal.jsonl ] && cp outputs/rubrics_internal.jsonl outputs/current/rubric_internal.jsonl
