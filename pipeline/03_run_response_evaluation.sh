#!/bin/bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
mkdir -p data/evaluation outputs/current cache/numbered
export RP_CACHE=${RP_CACHE:-$ROOT/cache/numbered}
[ -f data/rubric/10_negative_criteria_classified.jsonl ] || { echo 'Run pipeline/02_run_rubric_generation.sh first'; exit 1; }
cp data/rubric/10_negative_criteria_classified.jsonl data/s04c_severity.jsonl
python3 pipeline/20_resolve_canonical_answers.py --src data/s04c_severity.jsonl --out data/evaluation/20_answer_resolved_tasks.jsonl
python3 pipeline/20_select_evaluation_tasks.py --src data/evaluation/20_answer_resolved_tasks.jsonl --out data/evaluation/20_evaluation_tasks.jsonl
cp data/evaluation/20_answer_resolved_tasks.jsonl data/s04c_severity.jsonl
bash scripts/rerun_phase4.sh
python3 pipeline/compare_jsonl.py data/s04c_phase4.jsonl data/evaluation/20_evaluation_tasks.jsonl
cp data/s10_pool388.jsonl data/evaluation/21_response_pool.jsonl
cp data/s12_judged388.jsonl data/evaluation/22_response_scores.jsonl
cp data/s11c_cons388.jsonl data/evaluation/23_discrimination_diagnostics.jsonl
for i in 1 2 3; do [ -f "data/s11d_r$i.jsonl" ] && cp "data/s11d_r$i.jsonl" "data/evaluation/24_rubric_measurement_revision_r0$i.jsonl"; done
cp data/s11e_final.jsonl data/evaluation/25_selected_rubrics.jsonl
python3 pipeline/26_build_evaluation_delivery_source.py --measured data/s11e_final_sev.jsonl --fallback data/rubric/10_negative_criteria_classified.jsonl --out data/evaluation/26_rubric_delivery_source.jsonl
python3 pipeline/compare_jsonl.py data/s11e_all452.jsonl data/evaluation/26_rubric_delivery_source.jsonl

cp outputs/rubrics_advisor_lean.jsonl outputs/current/rubric_delivery.jsonl
[ -f outputs/rubrics_internal.jsonl ] && cp outputs/rubrics_internal.jsonl outputs/current/rubric_internal.jsonl
