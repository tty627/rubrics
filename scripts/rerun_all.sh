#!/bin/bash
# rubrics 一键全流程：xlsx → 交付（结构线 + Phase 4 实测 + 检查点 2）。
# 前置：1) config/models.json 已配好；2) data/input.xlsx 已就位。
# 用法：
#   bash scripts/rerun_all.sh             # 全流程（按现有缓存续跑，命中即秒过）
#   RP_CLEAN=1 bash scripts/rerun_all.sh  # 同时清结构线缓存（全部 LLM 调用重算）
# 模型可逐个覆盖（不设则用下面的安全默认；判分器/veto 必须异源，各步启动时校验）：
#   RP_M_GEN RP_M_FILTER RP_M_ROUTE RP_M_DIAGNOSER RP_M_S04LC
#   RP_M_JUDGE RP_M_VETO RP_M_S11LD RP_M_POOL_STRONG/MID/WEAK/CHECK
#
# 说明：与三个子脚本（rerun_lean_fixed / rerun_phase4 / rerun_checkpoint2）的
# 关系是「按顺序全部跑一遍」，每步产出与单独跑完全一致，可随时中断后从对应
# 子脚本续跑（缓存保证不重复计费）。

set -e
REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"

# ---- 模型默认（候选名不存在于本机 config/models.json 时自动回退角色默认；
#      判分器/veto 必须异源，各步启动时校验）----
cfg_has() { python3 - "$1" <<'PYEOF'
import json, sys
cfg = json.load(open('config/models.json', encoding='utf-8'))
print('1' if any(m.get('name') == sys.argv[1] for m in cfg) else '0')
PYEOF
}
set_default() {  # $1=环境变量名  $2=候选模型名
  if [ -z "${!1}" ] && [ "$(cfg_has "$2")" = "1" ]; then
    export "$1=$2"
  fi
}
set_default RP_M_GEN glm-ac
set_default RP_M_FILTER glm-ac
set_default RP_M_ROUTE glm-ac
set_default RP_M_S04LC cn-judge      # 开发机无 → 角色默认（deepseek，与 cn-judge 同模型）
set_default RP_M_JUDGE cn-judge
set_default RP_M_VETO cn-veto        # 开发机无 → 不设，s12 自动挑第三 family
set_default RP_M_S11LD cn-gen        # 开发机无 → 角色默认（glm-ac）
set_default RP_M_POOL_CHECK cn-judge
set_default RP_M_POOL_MID deepseek   # glm-ad 在开发机 404（2026-08-17 实测），用两机都通的 deepseek
set_default RP_M_POOL_WEAK glm-ac    # by-pool-weak 的 by 凭证已失效；用 glm-ac + 弱化 prompt
set_default RP_M_POOL_STRONG glm-ac
export RP_M_GEN RP_M_FILTER RP_M_ROUTE RP_M_S04LC RP_M_JUDGE RP_M_S11LD \
       RP_M_POOL_CHECK RP_M_POOL_MID RP_M_POOL_WEAK RP_M_POOL_STRONG

T0=$(date +%s)
echo "=== rubrics 一键全流程 ==="
echo "开始: $(date '+%F %T')"
echo "模型: GEN=$RP_M_GEN  JUDGE=$RP_M_JUDGE  VETO=$RP_M_VETO  重写=$RP_M_S11LD"
echo "      FILTER=$RP_M_FILTER  ROUTE=$RP_M_ROUTE  SEVERITY=$RP_M_S04LC  POOL_CHECK=$RP_M_POOL_CHECK"
echo

# ---- 0. 就绪检查 ----
[ -f config/models.json ] || { echo "✗ 缺少 config/models.json：cp config/models.json.example config/models.json 并填入 base_url / api_key"; exit 1; }
[ -f data/input.xlsx ] || { echo "✗ 缺少 data/input.xlsx：把题目表放到 data/input.xlsx（列 A-G，见 README「准备输入数据」）"; exit 1; }
python3 scripts/check_before_run.py || true
echo

# ---- 1. 结构线前置（s00 → s03）----
echo "[1/6] 种子与前置（s00 → s03）..."
python3 stages/s00_seed.py
python3 stages/s01_filter.py
python3 stages/s02_context.py
python3 stages/s02b_route.py
RP_RET=lean python3 stages/s03_perspective.py
echo

# ---- 2. 结构线主体 + 导出（候选回答隔离）----
echo "[2/6] 结构线（s04 → s04c_severity → 导出审计）..."
bash scripts/rerun_lean_fixed.sh
echo

# ---- 3. Phase 4 实测 ----
echo "[3/6] Phase 4 实测全量（回复池 → 判分 → 处置闭环 → 交付源）..."
bash scripts/rerun_phase4.sh
echo

# ---- 4. 检查点 2 ----
echo "[4/6] 检查点 2（草稿判分 + pairwise 放行闸门）..."
bash scripts/rerun_checkpoint2.sh
echo

# ---- 5. 交付物 ----
echo "[5/6] 交付物（xlsx 填充 + 审计）..."
python3 scripts/fill_xlsx_preserve_format.py
python3 scripts/audit_rubrics.py outputs/rubrics_advisor_lean.jsonl || true
echo

# ---- 6. 单测 ----
echo "[6/6] 语义核心单测..."
python3 tests/test_rubric.py

echo
echo "✅ 全流程完成（用时 $(( $(date +%s) - T0 )) 秒）"
echo "  交付档:   outputs/rubrics_advisor_lean.jsonl"
echo "  内部档:   outputs/rubrics_internal.jsonl"
echo "  人读版:   outputs/excel/Untitled spreadsheet (已填充rubrics).xlsx"
echo "  检查点2:  data/s12c_pairwise.jsonl（逐题明细）"
