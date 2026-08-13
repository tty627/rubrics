#!/usr/bin/env python3
"""并行 track 进度监控面板"""
import os
import sys
import time
from datetime import datetime

def check_file(path):
    """检查文件是否存在及大小"""
    if os.path.exists(path):
        size_mb = os.path.getsize(path) / 1024 / 1024
        with open(path) as f:
            lines = sum(1 for _ in f)
        return lines, size_mb
    return 0, 0

def count_cache(pattern):
    """统计缓存文件数"""
    import glob
    return len(glob.glob(f'cache/{pattern}/*.json'))

def tail_log(path, n=3):
    """获取日志最后n行"""
    if not os.path.exists(path):
        return ["(日志文件不存在)"]
    with open(path) as f:
        lines = f.readlines()
    return [line.rstrip() for line in lines[-n:]]

def main():
    print("\033[2J\033[H")  # 清屏
    print("=" * 80)
    print("并行 Track 监控面板".center(80))
    print("=" * 80)
    print(f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Track A: Phase 3
    print("【Track A: Phase 3 全量多模型聚合】")
    print("-" * 80)

    files_a = {
        's06_alt_context.jsonl': 'Step 1/4: context',
        's06_alt_perspective.jsonl': 'Step 2/4: RET',
        's06_alt_criteria.jsonl': 'Step 3/4: criteria',
        's06_aggregated.jsonl': 'Step 4/4: aggregate'
    }

    for fname, desc in files_a.items():
        lines, size = check_file(f'data/{fname}')
        status = "✅" if lines == 451 else "⏳" if lines > 0 else "⏸️"
        print(f"  {status} {desc:25s}: {lines:3d}/451 条 ({size:6.1f} MB)")

    # 缓存统计
    cache_6a = count_cache('s06a')
    cache_6c = count_cache('s06c')
    cache_6d = count_cache('s06d')
    print(f"\n  缓存: s06a={cache_6a}, s06c={cache_6c}, s06d={cache_6d}")

    # 日志尾部
    print("\n  最新日志:")
    for line in tail_log('logs/track_a_phase3.log', 2):
        print(f"    {line}")

    print()

    # Track B: dimension 传播
    print("【Track B: dimension 字段传播 (s04-s11)】")
    print("-" * 80)

    files_b = {
        's04_criteria.jsonl': 'Step 1/5: criteria',
        's07_evolved.jsonl': 'Step 2/5: difficulty',
        's08_penalties.jsonl': 'Step 3/5: penalties',
        's09_normalized.jsonl': 'Step 4/5: normalize',
        's11_diagnosed.jsonl': 'Step 5/5: diagnose'
    }

    for fname, desc in files_b.items():
        lines, size = check_file(f'data/{fname}')
        status = "✅" if lines >= 451 else "⏳" if lines > 0 else "⏸️"
        print(f"  {status} {desc:25s}: {lines:3d}/451 条 ({size:6.1f} MB)")

    # 验证 dimension 传播
    if os.path.exists('data/s04_criteria.jsonl'):
        import json
        with open('data/s04_criteria.jsonl') as f:
            rec = json.loads(f.readline())
        criteria = rec.get('criteria', [])
        has_dim = sum(1 for c in criteria if c.get('dimension'))
        print(f"\n  dimension 传播: {has_dim}/{len(criteria)} criteria 有字段")

    # 日志尾部
    print("\n  最新日志:")
    for line in tail_log('logs/track_b_dimension.log', 2):
        print(f"    {line}")

    print()
    print("=" * 80)
    print("按 Ctrl+C 退出 | 每 30 秒自动刷新")

if __name__ == '__main__':
    try:
        while True:
            main()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n\n监控已停止")
