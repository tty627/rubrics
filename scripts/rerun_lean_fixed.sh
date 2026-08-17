#!/bin/bash
# lean 主线一键重跑：s04L → s11L → s11Lb → s04Lb → s04Lc → 导出
# 用法: bash scripts/rerun_lean_fixed.sh
#
# 2026-08-13 改版：
#   - 删掉原第 5 步的临时 heredoc。那段从 data/s04L_rubric.jsonl（**未经诊断**）
#     导出交付文件，导致 s11L/s11Lb 跑了但产出没进交付 —— 交付版里
#     2452 条准则一条没过 RIFT，缺 rubric_form / is_gate / blocks / 血缘。
#   - 改为调 export_advisor_schema.py --src <流水线末端>
#
# 2026-08-14 改版：
#   - 补第 6 步 s04Lc_severity（负项分级 + veto 标记），导出源随之后移。
#     导出源必须始终指向流水线末端 —— 指向中间步会静默丢掉后续产出，
#     这个坑已经踩过两次（RIFT 未生效、severity/veto 全空）。

set -e  # 遇错即停

REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"

echo "=== lean 主线重跑 ==="
echo "工作目录: $REPO"
echo

# ---- 1. 备份旧产出 ----
echo "[1/7] 备份旧产出..."
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

echo "[2/7] 准则直出 (s04L_rubric.py)..."
python3 stages/s04L_rubric.py
echo "  ✓ data/s04L_rubric.jsonl"
echo

# ---- 3. RIFT 诊断 ----
echo "[3/7] RIFT 诊断 (s11L_diagnose.py)..."
python3 stages/s11L_diagnose.py
echo "  ✓ data/s11L_diagnosed.jsonl"
echo

# ---- 4. 诊断处置 ----
# subjective/ungrounded → 删；non-atomic → 落 _defect_queue.jsonl 待拆；闸门项豁免
echo "[4/7] 诊断处置 (s11Lb_remedy.py)..."
python3 stages/s11Lb_remedy.py
echo "  ✓ data/s11Lb_remedied.jsonl + data/_defect_queue.jsonl"
echo

# ---- 5. 缺陷重写 ----
# 消费 _defect_queue.jsonl（non-atomic 拆分 / factual 改对）
# + s04L 的 _flag_* 质量标记（话题清单、空泛词、悬崖、主观阈值…）
echo "[5/7] 缺陷重写 (s04Lb_split.py)..."
python3 stages/s04Lb_split.py
echo "  ✓ data/s04Lb_split.jsonl"
echo

# ---- 6. 导出交付 ----
# ---- 6. 负项严重性分级 + veto 标记 ----
# 补偿式总分上的合取门：principle 级原子负项标 is_veto，
# 判分侧按 lib/rubric.VETO_RULE 聚合（命中即整题得分率归 0）
echo "[6/7] 负项分级 + veto 标记 (s04Lc_severity.py)..."
python3 stages/s04Lc_severity.py
echo "  ✓ data/s04Lc_severity.jsonl"
echo

# ---- 7. 导出交付 ----
# --src 必须是流水线末端（当前 = s04Lc_severity），否则后续步骤的产出静默丢失
echo "[7/7] 导出交付版本..."
python3 scripts/export_advisor_schema.py \
    --src data/s04Lc_severity.jsonl \
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
echo "  data/s04Lb_split.jsonl        缺陷重写后"
echo "  data/s04Lc_severity.jsonl     负项分级 + veto（交付源）"
echo "  data/_defect_queue.jsonl      待拆队列（非原子）"
echo "  outputs/rubrics_advisor_lean.jsonl  交付档（5 字段 + rubric_form/is_gate/blocks"
echo "                                      + 负项 severity/is_veto）"
echo "  outputs/rubrics_internal.jsonl      内部档（含血缘/诊断/质量标记）"
echo
echo "回滚: cp outputs/rubrics_advisor_lean.jsonl.bak outputs/rubrics_advisor_lean.jsonl"
