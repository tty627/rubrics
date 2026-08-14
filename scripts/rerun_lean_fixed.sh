#!/bin/bash
# lean 主线一键重跑：s05L → s04L → s11L → s11Lb → s04Lb → 导出
# 用法: bash scripts/rerun_lean_fixed.sh
#
# 2026-08-13 改版：
#   - 删掉原第 5 步的临时 heredoc。那段从 data/s04L_rubric.jsonl（**未经诊断**）
#     导出交付文件，导致 s11L/s11Lb 跑了但产出没进交付 —— 交付版里
#     2452 条准则一条没过 RIFT，缺 rubric_form / is_gate / blocks / 血缘。
#   - 改为调 export_advisor_schema.py --src data/s11Lb_remedied.jsonl

set -e  # 遇错即停

REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"

echo "=== lean 主线重跑 ==="
echo "工作目录: $REPO"
echo

# ---- 1. 备份旧产出 ----
echo "[1/6] 备份旧产出..."
if [ -f outputs/rubrics_advisor_lean.jsonl ]; then
    cp outputs/rubrics_advisor_lean.jsonl outputs/rubrics_advisor_lean.jsonl.bak
    echo "  ✓ outputs/rubrics_advisor_lean.jsonl → .bak"
fi
echo

# ---- 2. 准则直出 ----
# 清缓存请显式指定：RP_CLEAN=1 bash scripts/rerun_lean_fixed.sh
if [ "${RP_CLEAN:-0}" = "1" ]; then
    echo "  RP_CLEAN=1，清理 cache/s04L/ cache/s11L_*/"
    rm -rf cache/s04L/ cache/s11L_subj/ cache/s11L_atom/ cache/s11L_ungr/
fi

echo "[2/6] 准则直出 (s04L_rubric.py)..."
python3 stages/s04L_rubric.py
echo "  ✓ data/s04L_rubric.jsonl"
echo

# ---- 3. RIFT 诊断 ----
echo "[3/6] RIFT 诊断 (s11L_diagnose.py)..."
python3 stages/s11L_diagnose.py
echo "  ✓ data/s11L_diagnosed.jsonl"
echo

# ---- 4. 诊断处置 ----
# subjective/ungrounded → 删；non-atomic → 落 _defect_queue.jsonl 待拆；闸门项豁免
echo "[4/6] 诊断处置 (s11Lb_remedy.py)..."
python3 stages/s11Lb_remedy.py
echo "  ✓ data/s11Lb_remedied.jsonl + data/_defect_queue.jsonl"
echo

# ---- 5. 缺陷重写 ----
# 消费 _defect_queue.jsonl（non-atomic 拆分 / factual 改对）
# + s04L 的 _flag_* 质量标记（话题清单、空泛词、悬崖、主观阈值…）
echo "[5/6] 缺陷重写 (s04Lb_split.py)..."
python3 stages/s04Lb_split.py
echo "  ✓ data/s04Lb_split.jsonl"
echo

# ---- 6. 导出交付 ----
echo "[6/6] 导出交付版本..."
python3 scripts/export_advisor_schema.py \
    --src data/s04Lb_split.jsonl \
    --out outputs/rubrics_advisor_lean.jsonl \
    --full
echo

echo "=== 质量审计 ==="
# 与上一版（.bak）对比，可判定性/区分度各项是升是降一目了然
if [ -f outputs/rubrics_advisor_lean.jsonl.bak ]; then
    python3 scripts/audit_rubrics.py outputs/rubrics_advisor_lean.jsonl \
        --base outputs/rubrics_advisor_lean.jsonl.bak || true
else
    python3 scripts/audit_rubrics.py outputs/rubrics_advisor_lean.jsonl || true
fi

echo
echo "✅ 完成。产出："
echo "  data/s04L_rubric.jsonl        准则直出"
echo "  data/s11L_diagnosed.jsonl     RIFT 诊断结果"
echo "  data/s11Lb_remedied.jsonl     处置后"
echo "  data/s04Lb_split.jsonl        缺陷重写后（交付源）"
echo "  data/_defect_queue.jsonl      待拆队列（非原子）"
echo "  outputs/rubrics_advisor_lean.jsonl  交付档（5 字段 + rubric_form/is_gate/blocks）"
echo "  outputs/rubrics_internal.jsonl      内部档（含血缘/诊断/质量标记）"
echo
echo "回滚: cp outputs/rubrics_advisor_lean.jsonl.bak outputs/rubrics_advisor_lean.jsonl"
