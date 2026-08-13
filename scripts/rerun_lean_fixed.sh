#!/bin/bash
# 一键重新运行修复后的 lean 流程
# 用法: bash scripts/rerun_lean_fixed.sh

set -e  # 遇错即停

REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"

echo "=== 修复后的 Lean 流程重新运行 ==="
echo "工作目录: $REPO"
echo

# 备份旧产出
echo "[1/5] 备份旧产出..."
if [ -f outputs/rubrics_advisor_lean.jsonl ]; then
    cp outputs/rubrics_advisor_lean.jsonl outputs/rubrics_advisor_lean.jsonl.bak
    echo "  ✓ 已备份 outputs/rubrics_advisor_lean.jsonl → .bak"
fi

# 清理旧缓存（可选，取消注释以启用）
# echo "[2/5] 清理 s04L 缓存..."
# rm -rf cache/s04L/
# echo "  ✓ 已清理 cache/s04L/"

echo "[2/5] 运行修复后的 s04L_rubric.py..."
python3 stages/s04L_rubric.py
echo "  ✓ 产出: data/s04L_rubric.jsonl"
echo

echo "[3/5] 运行 RIFT 诊断 (s11L_diagnose.py)..."
python3 stages/s11L_diagnose.py
echo "  ✓ 产出: data/s11L_diagnosed.jsonl"
echo

echo "[4/5] 运行诊断处置 (s11Lb_remedy.py)..."
python3 stages/s11Lb_remedy.py
echo "  ✓ 产出: data/s11Lb_remedied.jsonl"
echo

echo "[5/5] 导出交付版本..."
# TODO: 等 export_advisor_schema.py 支持 --src 参数后改为:
# python3 scripts/export_advisor_schema.py --src data/s11Lb_remedied.jsonl

# 临时方案：手动复制字段
python3 << 'EOF'
import json
import os

# 读取 s04L_rubric.jsonl（未诊断版，用于快速验证占比修复）
with open('data/s04L_rubric.jsonl') as f:
    recs = [json.loads(line) for line in f]

# 导出交付 schema
DELIVER_FIELDS = ('criteria', 'score', 'reason', 'dimension', 'is_positive')
result = []
for r in recs:
    rubrics = [
        {k: c[k] for k in DELIVER_FIELDS if k in c}
        for c in r.get('rubrics', [])
    ]
    if not rubrics:
        continue

    pos = [c for c in rubrics if c.get('is_positive')]
    result.append({
        'rid': r['rid'],
        'xlsx_row': r.get('xlsx_row'),
        'question': r.get('query_eff') or r.get('question', ''),
        'subject': r.get('subject', []),
        'question_type': r.get('question_type', ''),
        'intent': r.get('intent', ''),
        'full_mark': sum(c['score'] for c in pos),
        'rubrics': rubrics,
    })

with open('outputs/rubrics_advisor_lean.jsonl', 'w', encoding='utf-8') as f:
    for r in result:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')

print(f'  ✓ 已导出 {len(result)} 题到 outputs/rubrics_advisor_lean.jsonl')
EOF
echo

echo "=== 验证修复效果 ==="
python3 scripts/test_s04L_fixes.py

echo
echo "✅ 流程重新运行完成！"
echo
echo "产出文件:"
echo "  - data/s04L_rubric.jsonl (修复后的 rubrics)"
echo "  - data/s11L_diagnosed.jsonl (诊断结果)"
echo "  - data/s11Lb_remedied.jsonl (删除 defective 后)"
echo "  - outputs/rubrics_advisor_lean.jsonl (交付版本)"
echo
echo "下一步:"
echo "  1. 查看修复对比: python3 scripts/test_s04L_fixes.py"
echo "  2. 查看完整报告: cat docs/reports/RUBRICS_REVIEW_FINDINGS.md"
echo "  3. 如需回滚: cp outputs/rubrics_advisor_lean.jsonl.bak outputs/rubrics_advisor_lean.jsonl"
