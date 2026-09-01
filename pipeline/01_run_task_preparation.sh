#!/bin/bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
mkdir -p data/tasks cache/numbered
export RP_CACHE=${RP_CACHE:-$ROOT/cache/numbered}
if [ -n "${RP_SEED_ONLY:-}" ]; then
  cp "$RP_SEED_ONLY" data/tasks/01_task_dataset.jsonl
else
  python3 pipeline/01_build_task_dataset.py
fi
# 阶段 01 已直写编号路径，别再回填 data/seed.jsonl（那是旧线残留，含 ref_responses
# 等禁用字段），否则会把 10 条新产物覆盖成 237 条旧种子、且绕过 stage 01 的清洗。
cp data/tasks/01_task_dataset.jsonl data/seed.jsonl
python3 pipeline/02_filter_tasks.py
cp data/tasks/02_filtered_tasks.jsonl data/s01_filter.jsonl
python3 pipeline/03_extract_task_context.py
cp data/s02_context.jsonl data/tasks/03_task_context.jsonl
cp data/tasks/03_task_context.jsonl data/s02_context.jsonl
python3 pipeline/04_classify_task_type.py
cp data/s02b_route.jsonl data/tasks/04_task_types.jsonl
cp data/tasks/04_task_types.jsonl data/s02b_route.jsonl
RP_RET=lean python3 pipeline/05_generate_evaluation_axes.py
cp data/s03_perspective_lean.jsonl data/tasks/05_evaluation_axes.jsonl
