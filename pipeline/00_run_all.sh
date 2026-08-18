#!/bin/bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
RUN_ID=${RP_RUN_ID:-$(date +%Y%m%dT%H%M%S)}
export RP_RUN_ID
bash pipeline/01_run_task_preparation.sh
bash pipeline/02_run_rubric_generation.sh
bash pipeline/03_run_response_evaluation.sh
bash pipeline/04_run_release_verification.sh
mkdir -p "outputs/current" "outputs/runs/$RUN_ID"
python3 pipeline/write_run_manifest.py
cp -a outputs/current/. "outputs/runs/$RUN_ID/"
printf 'Full pipeline complete: %s\n' "$RUN_ID"
