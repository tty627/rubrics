#!/bin/bash
# rubric 结构线：s04_rubric → s11_diagnose → s11b_remedy
#   → s04b_split → s04c_severity → 导出
# 前置：s00-s03 已跑完（s03_perspective_lean.jsonl 存在）。
# rubric 生成只读取原始 system/developer/user 指令、题目和评价轴。
# 候选回答及独立 canonical answer 只在 rubric 冻结后的 Phase 4 使用。
# 用法: bash scripts/rerun_lean_fixed.sh

set -e  # 遇错即停

REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"

echo "=== lean 主线重跑 ==="
echo "工作目录: $REPO"
echo

# ---- 1. 备份旧产出 ----
echo "[1/8] 备份旧产出..."
if [ -f outputs/rubrics_advisor_lean.jsonl ]; then
    cp outputs/rubrics_advisor_lean.jsonl outputs/rubrics_advisor_lean.jsonl.bak
    echo "  ✓ outputs/rubrics_advisor_lean.jsonl → .bak"
fi
echo

# ---- 2. 准则直出（候选回答隔离）----
# rubric 生成只读取题目、题面约束和视角；ref_responses 是后续待评分对象，
# 不得进入 s01-s04。独立答案在 Stage 20 解析，且不回流 rubric。
if [ "${RP_CLEAN:-0}" = "1" ]; then
    echo "  RP_CLEAN=1，清理 cache/s04L/ cache/s11L_*/"
    rm -rf cache/s04L/ cache/s11L_subj/ cache/s11L_atom/ cache/s11L_ungr/
fi

echo "[2/8] 准则直出（只读 s03 题目视角，不读取候选回答）..."
RP_S04L_SRC=s03_perspective_lean.jsonl python3 stages/s04_rubric.py
echo "  ✓ data/s04_rubric.jsonl"
echo

# ---- 3. 预留编号：rubric 已冻结；候选回答只在后续 judge/实测阶段使用 ----
# ---- 4. RIFT 诊断 ----
echo "[4/8] RIFT 诊断 (s11_diagnose.py)..."
python3 stages/s11_diagnose.py
echo "  ✓ data/s11_diagnosed.jsonl"
echo

# ---- 5. 诊断处置 ----
# subjective/ungrounded → 删；non-atomic → 落 _defect_queue.jsonl 待拆；闸门项豁免
echo "[5/8] 诊断处置 (s11b_remedy.py)..."
python3 stages/s11b_remedy.py
echo "  ✓ data/s11b_remedied.jsonl + data/_defect_queue.jsonl"
echo

# ---- 5. 缺陷重写 ----
# 消费 _defect_queue.jsonl（non-atomic 拆分 / factual 改对）
# + s04_rubric 的 _flag_* 质量标记（话题清单、空泛词、悬崖、主观阈值…）
echo "[6/8] 缺陷重写 (s04b_split.py)..."
python3 stages/s04b_split.py
echo "  ✓ data/s04b_split.jsonl"
echo

# ---- 6. 导出交付 ----
# ---- 6. 负项严重性分级 + veto 标记 ----
# 补偿式总分上的合取门：principle 级原子负项标 is_veto，
# 判分侧按 lib/rubric.VETO_RULE 聚合（命中即整题得分率归 0）
echo "[7/8] 负项分级 + veto 标记 (s04c_severity.py)..."
python3 stages/s04c_severity.py
echo "  ✓ data/s04c_severity.jsonl"
echo

# ---- 7. 导出交付 ----
# --src 必须是流水线末端（当前 = s04c_severity），否则后续步骤的产出静默丢失
echo "[8/8] 导出交付版本..."
python3 scripts/export_advisor_schema.py \
    --src data/s04c_severity.jsonl \
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
echo "  data/s04_rubric.jsonl        准则直出"
echo "  data/s11_diagnosed.jsonl     RIFT 诊断结果"
echo "  data/s11b_remedied.jsonl     处置后"
echo "  data/s04b_split.jsonl        缺陷重写后"
echo "  data/s04c_severity.jsonl     负项分级 + veto（交付源）"
echo "  data/_defect_queue.jsonl      待拆队列（非原子）"
echo "  outputs/rubrics_advisor_lean.jsonl  交付档（5 字段 + rubric_form/is_gate/blocks"
echo "                                      + 负项 severity/is_veto）"
echo "  outputs/rubrics_internal.jsonl      内部档（含血缘/诊断/质量标记）"
echo
echo "回滚: cp outputs/rubrics_advisor_lean.jsonl.bak outputs/rubrics_advisor_lean.jsonl"
