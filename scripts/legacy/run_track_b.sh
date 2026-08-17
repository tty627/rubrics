#!/bin/bash
# Track B: 重跑 s04-s11，传播 dimension 字段
set -e

echo "=== Track B: 重跑 s04-s11（传播 dimension）==="
echo "开始时间: $(date)"
echo ""

# 备份原文件
echo "备份原有输出..."
mkdir -p data/backup_no_dimension
for file in s04_criteria s07_evolved s08_penalties s09_normalized s11_diagnosed; do
    if [ -f "data/${file}.jsonl" ]; then
        cp "data/${file}.jsonl" "data/backup_no_dimension/${file}.jsonl"
        echo "  备份 ${file}.jsonl"
    fi
done

echo ""
echo "Step 1/5: s04 准则生成（从 s03c_dimensioned）..."
RP_S03_OUT=s03c_dimensioned.jsonl \
RP_WORKERS=20 \
RP_THINK=false \
python3 stages/s04_criteria.py

echo ""
echo "Step 2/5: s07 难度演化..."
RP_WORKERS=20 \
RP_THINK=false \
python3 stages/s07_difficulty.py

echo ""
echo "Step 3/5: s08 负向项..."
RP_WORKERS=20 \
RP_THINK=false \
python3 stages/s08_penalties.py

echo ""
echo "Step 4/5: s09 归一化..."
python3 stages/s09_normalize.py

echo ""
echo "Step 5/5: s11 RIFT 诊断..."
RP_WORKERS=20 \
python3 stages/s11_diagnose.py

echo ""
echo "=== Track B 完成 ==="
echo "完成时间: $(date)"

# 验证 dimension 传播
echo ""
echo "验证 dimension 字段传播..."
python3 << 'PYEOF'
import json

files = ['s04_criteria.jsonl', 's09_normalized.jsonl', 's11_diagnosed.jsonl']
for fname in files:
    with open(f'data/{fname}') as f:
        rec = json.loads(f.readline())

    criteria = rec.get('criteria', [])
    has_dim = sum(1 for c in criteria if c.get('dimension'))
    print(f"  {fname}: {has_dim}/{len(criteria)} criteria 有 dimension")
PYEOF
