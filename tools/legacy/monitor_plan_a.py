#!/usr/bin/env python3
"""方案A执行监控"""
import os
import time
from datetime import datetime

def check_file(path):
    if os.path.exists(path):
        size_mb = os.path.getsize(path) / 1024 / 1024
        with open(path) as f:
            lines = sum(1 for _ in f)
        return lines, size_mb
    return 0, 0

print("=" * 80)
print("方案A执行状态".center(80))
print("=" * 80)
print(f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

# 检查各步骤输出
steps = [
    ('s05_grounded.jsonl', 'Step 1: s05 grounding', 452),
    ('s07_evolved.jsonl', 'Step 2: s07 difficulty', 452),
    ('s08_penalties.jsonl', 'Step 3: s08 penalties', 451),
    ('s09_normalized.jsonl', 'Step 4: s09 normalize', 451),
    ('s11_diagnosed.jsonl', 'Step 5: s11 diagnose', 451),
    ('s11b_remedied.jsonl', 'Step 6: s11b remedy', 451),
]

completed = 0
for fname, desc, expected in steps:
    path = f'data/{fname}'
    lines, size = check_file(path)

    if lines >= expected:
        status = "✅"
        completed += 1
    elif lines > 0:
        status = "⏳"
    else:
        status = "⏸️"

    progress = min(100, 100 * lines / expected) if lines > 0 else 0
    print(f"{status} {desc:30s}: {lines:3d}/{expected} ({progress:5.1f}%) {size:6.1f}MB")

print(f"\n总进度: {completed}/{len(steps)} 步完成 ({100*completed/len(steps):.1f}%)")

# 日志最后几行
print("\n最新日志:")
if os.path.exists('logs/track_b_s05_remedy.log'):
    with open('logs/track_b_s05_remedy.log') as f:
        lines = f.readlines()
    for line in lines[-3:]:
        print(f"  {line.rstrip()}")

# 进程状态
import subprocess
result = subprocess.run("ps aux | grep -E 's05|s07|s08|s09|s11' | grep python | grep -v grep | wc -l",
                       shell=True, capture_output=True, text=True)
proc_count = int(result.stdout.strip())
print(f"\nPython 进程数: {proc_count}")

print("\n" + "=" * 80)
