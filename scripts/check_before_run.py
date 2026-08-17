#!/usr/bin/env python3
"""运行前检查：展示当前数据状态和运行计划。"""
import os
import json
from pathlib import Path

REPO = Path(__file__).parent.parent
os.chdir(REPO)

def check_file(path, desc):
    """检查文件是否存在并显示信息。"""
    p = Path(path)
    if p.exists():
        size_mb = p.stat().st_size / 1024 / 1024
        # 如果是 jsonl，统计行数
        if p.suffix == '.jsonl':
            with open(p) as f:
                lines = sum(1 for _ in f)
            return f'✅ 存在 ({size_mb:.1f}MB, {lines}条)', True
        else:
            return f'✅ 存在 ({size_mb:.1f}MB)', True
    else:
        return '❌ 不存在', False

print('='*80)
print('  Lean 流程修复 - 运行前检查')
print('='*80)

print('\n📂 输入数据检查\n')
inputs = [
    ('data/s03_perspective_lean.jsonl', '步骤3输出（视角生成）'),
]
all_inputs_ok = True
for path, desc in inputs:
    status, ok = check_file(path, desc)
    print(f'{status:40s}  {path}')
    print(f'{"":40s}  └─ {desc}')
    all_inputs_ok = all_inputs_ok and ok

print('\n📊 当前中间数据状态\n')
intermediates = [
    ('data/s04_rubric.jsonl', '步骤4L输出（准则直出）'),
    ('data/s11_diagnosed.jsonl', '步骤11L输出（RIFT诊断）'),
    ('data/s11b_remedied.jsonl', '步骤11Lb输出（诊断处置）'),
]
for path, desc in intermediates:
    status, ok = check_file(path, desc)
    if ok:
        print(f'{status:40s}  {path}')
        print(f'{"":40s}  └─ {desc}（将被覆盖）')
    else:
        print(f'{status:40s}  {path}')
        print(f'{"":40s}  └─ {desc}（首次生成）')

print('\n📤 最终产出状态\n')
outputs = [
    ('outputs/rubrics_advisor_lean.jsonl', '交付版本'),
    ('outputs/rubrics_advisor_lean.jsonl.bak', '备份（自动生成）'),
]
for path, desc in outputs:
    status, ok = check_file(path, desc)
    print(f'{status:40s}  {path}')
    if ok and '.bak' not in path:
        print(f'{"":40s}  └─ {desc}（运行前会自动备份为 .bak）')
    else:
        print(f'{"":40s}  └─ {desc}')

print('\n🗂️  缓存目录\n')
cache_dirs = [
    ('cache/s04L', '步骤4L缓存'),
    ('cache/s11L_subj', '步骤11L缓存（Subjective）'),
    ('cache/s11L_non-', '步骤11L缓存（Non-Atomic）'),
    ('cache/s11L_ungr', '步骤11L缓存（Ungrounded）'),
]
total_cache_mb = 0
for path, desc in cache_dirs:
    p = Path(path)
    if p.exists():
        files = list(p.glob('*.json'))
        size_mb = sum(f.stat().st_size for f in files) / 1024 / 1024
        total_cache_mb += size_mb
        print(f'✅ 存在 ({len(files)}个文件, {size_mb:.1f}MB)'.ljust(40) + f'  {path}/')
        print(f'{"":40s}  └─ {desc}')
    else:
        print(f'❌ 不存在'.ljust(40) + f'  {path}/')
        print(f'{"":40s}  └─ {desc}（运行后生成）')

if total_cache_mb > 0:
    print(f'\n  缓存总大小: {total_cache_mb:.1f}MB')
    if total_cache_mb > 100:
        print(f'  💡 提示: 缓存较大，可运行 rm -rf cache/s04L/ cache/s11L_*/ 清理')

print('\n'+'='*80)
print('  运行计划')
print('='*80)

if all_inputs_ok:
    print('\n✅ 输入数据就绪，可以开始运行！\n')
    print('运行命令:')
    print('  bash scripts/rerun_lean_fixed.sh')
    print()
    print('流程步骤:')
    print('  1. 备份旧产出 (outputs/rubrics_advisor_lean.jsonl → .bak)')
    print('  2. 运行 s04_rubric.py     → data/s04_rubric.jsonl')
    print('  3. 运行 s11_diagnose.py   → data/s11_diagnosed.jsonl')
    print('  4. 运行 s11b_remedy.py    → data/s11b_remedied.jsonl')
    print('  5. 导出交付版本            → outputs/rubrics_advisor_lean.jsonl')
    print('  6. 运行验证脚本            → 对比修复前后')
    print()
    print('预估时间:')
    # 从 s03 文件统计记录数
    with open('data/s03_perspective_lean.jsonl') as f:
        n_recs = sum(1 for _ in f)

    # 预估调用次数
    s04L_calls = n_recs  # 每题1次
    s11L_calls = n_recs * 7 * 3  # 每题约7条准则 × 3个诊断模式

    print(f'  - s04_rubric: {n_recs} 题 × 1 次/题 = {s04L_calls} 次调用')
    print(f'  - s11_diagnose: {n_recs} 题 × ~7 条准则 × 3 模式 = ~{s11L_calls} 次调用')
    print(f'  - 总计: ~{s04L_calls + s11L_calls} 次 LLM 调用')
    print()
    if Path('cache/s04L').exists():
        print('  💡 存在缓存，实际调用次数会减少（仅重新调用 prompt 改变的部分）')
    print()
    print('监控进度（另开终端）:')
    print('  python3 tools/watch.py')
else:
    print('\n❌ 输入数据缺失，无法运行！\n')
    print('请先运行步骤3生成输入:')
    print('  python3 stages/s03_perspective_lean.py')

print('\n'+'='*80)
print('  详细说明')
print('='*80)
print('\n文档:')
print('  - 数据流向指南: docs/DATA_FLOW_GUIDE.md')
print('  - 修复实施指南: docs/reports/S04L_FIX_GUIDE.md')
print('  - 问题诊断报告: docs/reports/RUBRICS_REVIEW_FINDINGS.md')
print()
