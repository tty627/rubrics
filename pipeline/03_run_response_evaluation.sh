#!/bin/bash
# 阶段 03：冻结后的实测线。回复池 → 判分 → 区分度诊断 → 处置闭环 → 终态选择 → 交付源。
#
# 处置必须闭环复测（24 修订 → 22 重判 → 23 复诊），这不是保险而是必需：LLM 重写会摆动，
# 实测中出现过 60%→0%→60%→0% 的 2-循环。「收紧」与「放松」是互逆操作，对这类题不存在
# 两头都满足的中间档。所以跑固定轮数（默认 3），再由 25 在各轮实测证据里挑每题最好的一版。
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
mkdir -p data/evaluation outputs/current cache/numbered
export RP_CACHE=${RP_CACHE:-$ROOT/cache/numbered}
export RP_DATA_ROOT=${RP_DATA_ROOT:-$ROOT/data}

FROZEN=data/rubric/10_frozen_rubric.jsonl
[ -f "$FROZEN" ] || { echo 'Run pipeline/02_run_rubric_generation.sh first'; exit 1; }

: "${RP_WORKERS:=6}"
: "${RP_ROUNDS:=3}"
export RP_WORKERS

# 20 从题面独立求解可核验答案；绝不读候选回答（见脚本首行声明）。
python3 pipeline/20_resolve_canonical_answers.py \
    --src "$FROZEN" --out data/evaluation/20_evaluation_tasks.jsonl

# 21→23：回复池 6 档全部现场生成 → 判分 → 区分度诊断（基线轮）。
RP_S10L_SRC=evaluation/20_evaluation_tasks.jsonl RP_S10L_OUT=evaluation/21_response_pool.jsonl \
    python3 pipeline/21_build_response_pool.py
RP_S12L_SRC=evaluation/21_response_pool.jsonl RP_S12L_OUT=evaluation/22_response_scores.jsonl \
    python3 pipeline/22_score_response_pool.py
RP_S11LC_SRC=evaluation/22_response_scores.jsonl \
    RP_S11LC_OUT=evaluation/23_discrimination_diagnostics.jsonl \
    python3 pipeline/23_diagnose_rubric_discrimination.py

# 24⇄22⇄23 处置闭环。只把被重写的题送去重判：其余题 rubric 没变，重判是白烧 token。
ROUNDS="evaluation/23_discrimination_diagnostics.jsonl"
CUR="evaluation/23_discrimination_diagnostics.jsonl"
for i in $(seq 1 "$RP_ROUNDS"); do
    RP_S11LD_SRC="$CUR" RP_S11LD_OUT="evaluation/24_rubric_measurement_revision_r0$i.jsonl" \
        python3 pipeline/24_revise_rubric_from_measurement.py
    python3 - "$i" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, '.')
from lib import paths
i = sys.argv[1]
src = paths.EVALUATION_DATA / f'24_rubric_measurement_revision_r0{i}.jsonl'
out = paths.EVALUATION_DATA / f'24_rewritten_r0{i}.jsonl'
rows = [r for r in (json.loads(l) for l in src.open(encoding='utf-8') if l.strip())
        if (r.get('s11Ld') or {}).get('rewritten')]
with out.open('w', encoding='utf-8') as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
print(f'  第 {i} 轮重写 {len(rows)} 题')
PY
    if [ ! -s "data/evaluation/24_rewritten_r0$i.jsonl" ]; then
        echo "  本轮无重写，处置收敛，提前结束"
        break
    fi
    RP_S12L_SRC="evaluation/24_rewritten_r0$i.jsonl" \
        RP_S12L_OUT="evaluation/22_response_scores_r0$i.jsonl" \
        python3 pipeline/22_score_response_pool.py
    RP_S11LC_SRC="evaluation/22_response_scores_r0$i.jsonl" \
        RP_S11LC_OUT="evaluation/23_discrimination_diagnostics_r0$i.jsonl" \
        python3 pipeline/23_diagnose_rubric_discrimination.py
    ROUNDS="$ROUNDS,evaluation/23_discrimination_diagnostics_r0$i.jsonl"
    CUR="evaluation/23_discrimination_diagnostics_r0$i.jsonl"
done

# 25 终态选择；处置重写只保正向分值守恒，负项 severity 要靠 10 补回。
RP_S11LE_ROUNDS="$ROUNDS" RP_S11LE_OUT=evaluation/25_selected_rubrics.jsonl \
    python3 pipeline/25_select_rubric_revision.py
RP_S04LC_SRC=evaluation/25_selected_rubrics.jsonl \
    RP_S04LC_OUT=evaluation/25_selected_rubrics_classified.jsonl \
    python3 pipeline/10_classify_negative_criteria.py

python3 pipeline/26_build_evaluation_delivery_source.py \
    --measured data/evaluation/25_selected_rubrics_classified.jsonl \
    --fallback "$FROZEN" \
    --out data/evaluation/26_rubric_delivery_source.jsonl

python3 pipeline/27_export_rubric_delivery.py \
    --src data/evaluation/26_rubric_delivery_source.jsonl \
    --out outputs/current/rubric_delivery.jsonl
python3 pipeline/27_export_rubric_delivery.py \
    --src data/evaluation/26_rubric_delivery_source.jsonl \
    --out outputs/current/rubric_internal.jsonl --full
