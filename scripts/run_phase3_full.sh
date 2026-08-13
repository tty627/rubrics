#!/bin/bash
# Phase 3 全量执行脚本
set -e

echo "=== Phase 3 全量处理：451条 ==="
echo "开始时间: $(date)"
echo ""

# Step 1: 第二生成器 context
echo "Step 1/4: deepseek 生成 context..."
RP_M_ALT=deepseek \
RP_S06_IN=s01_filter.jsonl \
RP_S06A_OUT=s06_alt_context.jsonl \
RP_WORKERS=20 \
RP_THINK=false \
python3 stages/s06a_alt_context.py

echo ""
echo "Step 2/4: deepseek RET 展开..."
RP_M_ALT=deepseek \
RP_S06A_OUT=s06_alt_context.jsonl \
RP_S025_OUT=s02_5_route.jsonl \
RP_S06C_OUT=s06_alt_perspective.jsonl \
RP_WORKERS=20 \
RP_THINK=false \
python3 stages/s06c_alt_perspective.py

echo ""
echo "Step 3/4: deepseek 生成准则..."
RP_M_ALT=deepseek \
RP_S06C_OUT=s06_alt_perspective.jsonl \
RP_S06D_OUT=s06_alt_criteria.jsonl \
RP_WORKERS=20 \
RP_THINK=false \
python3 stages/s06d_alt_criteria.py

echo ""
echo "Step 4/4: 聚合两个生成器..."
RP_S04_OUT=s04_criteria.jsonl \
RP_S06D_OUT=s06_alt_criteria.jsonl \
RP_S06_OUT=s06_aggregated.jsonl \
python3 stages/s06_aggregate.py

echo ""
echo "=== Phase 3 全量完成 ==="
echo "完成时间: $(date)"
