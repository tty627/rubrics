#!/usr/bin/env python3
"""
快速生成项目统计数据，用于向导师展示
用法: python3 scripts/quick_stats.py
"""

import json
import os
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main():
    # 统一切到仓库根，脚本可从任意目录调用
    os.chdir(REPO_ROOT)

    print("=" * 60)
    print("Rubric 自动生成项目 - 快速统计")
    print("=" * 60)
    print()

    # 1. 基线指标
    with open('data/baseline.json') as f:
        baseline = json.load(f)

    print("【1】 Baseline（草稿 rubric）")
    print(f"  - 记录数: {baseline['n_records']}")
    print(f"  - 维度数: {baseline['dimension_uniq']} 个")
    print(f"  - 准则数: 平均 {baseline['n_criteria_per_q']['mean']:.1f} 条")
    print(f"  - 满分中位数: {baseline['fullmark']['p50']:.0f} 分")
    print()

    # 2. Phase 3 完成情况
    s09_file = 'data/s09_normalized.jsonl'
    if not os.path.exists(s09_file):
        print("⚠️  s09_normalized.jsonl 不存在，Phase 3 未完成")
        return

    records = []
    with open(s09_file) as f:
        for line in f:
            records.append(json.loads(line))

    print("【2】 Phase 3 完成情况")
    print(f"  - 完成记录数: {len(records)}")

    # 题型分布
    question_types = Counter(r['question_type'] for r in records)
    rubric_forms = Counter(r['rubric_form'] for r in records)
    print(f"  - 题型分布:")
    for qt, cnt in question_types.most_common():
        print(f"      {qt}: {cnt} 条")
    print(f"  - Rubric 形态:")
    for rf, cnt in rubric_forms.most_common():
        print(f"      {rf}: {cnt} 条")
    print()

    # 3. 结构指标统计
    dims_per_record = []
    criteria_counts = []
    fullmarks = []

    for rec in records:
        unique_dims = set()
        for crit in rec.get('criteria', []):
            dim = crit.get('dimension', '').strip()
            if dim:
                unique_dims.add(dim)

        dims_per_record.append(len(unique_dims))
        criteria_counts.append(len(rec.get('criteria', [])))
        fullmarks.append(rec.get('s_max', 0))

    print("【3】 结构指标改进")
    print(f"  维度数:")
    print(f"    Baseline: 1 → Phase 3: 平均 {sum(dims_per_record)/len(dims_per_record):.1f} (min={min(dims_per_record)}, max={max(dims_per_record)})")
    print(f"    改善幅度: +{(sum(dims_per_record)/len(dims_per_record) - 1) * 100:.0f}%")
    print()
    print(f"  准则数:")
    print(f"    Baseline: 6.1 → Phase 3: 平均 {sum(criteria_counts)/len(criteria_counts):.1f}")
    print(f"    改善幅度: +{(sum(criteria_counts)/len(criteria_counts) / 6.1 - 1) * 100:.0f}%")
    print()
    print(f"  满分中位数:")
    median_fullmark = sorted(fullmarks)[len(fullmarks)//2]
    print(f"    Baseline: 21 → Phase 3: {median_fullmark}")
    print(f"    改善幅度: +{(median_fullmark / 21 - 1) * 100:.0f}%")
    print()

    # 4. 维度多样性
    all_dims = []
    for rec in records:
        for crit in rec.get('criteria', []):
            dim = crit.get('dimension', '').strip()
            if dim:
                all_dims.append(dim)

    unique_dims = len(set(all_dims))
    print("【4】 维度多样性")
    print(f"  - 唯一维度种类: {unique_dims} 种")
    print(f"  - 维度实例总数: {len(all_dims)}")
    print(f"  - Top 10 高频维度:")
    dim_dist = Counter(all_dims).most_common(10)
    for dim, cnt in dim_dist:
        if dim:
            print(f"      {cnt:4d} {dim}")
    print()

    # 5. LLM 调用统计
    cache_dir = Path('cache')
    if cache_dir.exists():
        stages = {}
        for stage_dir in cache_dir.iterdir():
            if stage_dir.is_dir() and not stage_dir.name.startswith('_'):
                json_files = list(stage_dir.glob('*.json'))
                if json_files:
                    stages[stage_dir.name] = len(json_files)

        print("【5】 LLM 调用统计")
        print(f"  - 总调用数: {sum(stages.values()):,} 次")
        print(f"  - 主要步骤:")
        for stage in sorted(stages.keys(), key=lambda s: stages[s], reverse=True)[:5]:
            print(f"      {stage:20s} {stages[stage]:6,} 次")
        print()

    # 6. 资源消耗
    import subprocess
    try:
        cache_size = subprocess.check_output(['du', '-sh', 'cache/'], text=True).split()[0]
        data_size = subprocess.check_output(['du', '-sh', 'data/'], text=True).split()[0]
        print("【6】 磁盘占用")
        print(f"  - 缓存目录: {cache_size}")
        print(f"  - 数据目录: {data_size}")
        print()
    except:
        pass

    print("=" * 60)
    print("提示: 详细审计见 python3 scripts/audit_rubrics.py")
    print("=" * 60)

if __name__ == '__main__':
    main()
