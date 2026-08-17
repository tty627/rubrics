#!/bin/bash
# Phase 4 检查点 2：新 rubric vs 草稿 rubric 的 pairwise 一致率（放行闸门）。
# 用法: bash scripts/rerun_checkpoint2.sh
#
# 判分器必须异于生成器（硬约束第 2 条），且 config 里第一个 judge 角色
# by-judge（35.220.164.252 代理）自 2026-08-17 起持续 401 —— 固定 cn-judge。

set -e
REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"

# 判分器：候选 cn-judge 不在本机 config 里就回退角色默认（开发机=deepseek，同模型）
cfg_has() { python3 - "$1" <<'PYEOF'
import json, sys
cfg = json.load(open('config/models.json', encoding='utf-8'))
print('1' if any(m.get('name') == sys.argv[1] for m in cfg) else '0')
PYEOF
}
if [ -z "${RP_M_JUDGE:-}" ] && [ "$(cfg_has cn-judge)" = "1" ]; then
    export RP_M_JUDGE=cn-judge
fi
: "${RP_WORKERS:=8}"
export RP_M_JUDGE RP_WORKERS

echo "=== Phase 4 检查点 2：新 rubric vs 草稿 rubric ==="
echo "判分=$RP_M_JUDGE  并发=$RP_WORKERS"
echo

echo "[1/2] 草稿 rubric 判分（strong+weak，s12_judge 同口径）..."
RP_S12LB_SRC=s10_pool388.jsonl RP_S12LB_OUT=s12b_draft388.jsonl \
  python3 stages/s12b_draft_judge.py
echo

echo "[2/2] pairwise 一致率对比 + 放行判据..."
RP_S12LC_OUT=s12c_pairwise.jsonl python3 stages/s12c_pairwise.py
echo
echo "✅ 检查点 2 跑完。逐题明细: data/s12c_pairwise.jsonl"
