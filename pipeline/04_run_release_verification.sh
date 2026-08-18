#!/bin/bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
mkdir -p data/release outputs/current cache/numbered
export RP_CACHE=${RP_CACHE:-$ROOT/cache/numbered}
bash scripts/rerun_checkpoint2.sh
cp data/s12b_draft388.jsonl data/release/30_draft_rubric_scores.jsonl
cp data/s12c_pairwise.jsonl data/release/31_pairwise_comparison.jsonl
RP_FILL_SRC=outputs/current/rubric_delivery.jsonl python3 pipeline/32_export_rubric_xlsx.py
LATEST_XLSX=$(ls -1t outputs/excel/*.xlsx | head -1)
cp "$LATEST_XLSX" outputs/current/rubric_delivery.xlsx
python3 pipeline/33_audit_rubric_delivery.py outputs/current/rubric_delivery.jsonl > data/release/33_delivery_audit.txt || true
python3 -m unittest discover -s tests -v
