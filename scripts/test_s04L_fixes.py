#!/usr/bin/env python3
"""测试修复后的 s04L：验证答案项占比自动调整和负向准则约束。

运行前：备份现有 s04L_rubric.jsonl
运行后：对比修复前后的问题题目
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from collections import Counter


def check_issues(jsonl_path):
    """检查 rubrics 的问题。"""
    with open(jsonl_path) as f:
        recs = [json.loads(line) for line in f]

    issues = {
        '答案项占比<60%': [],
        '答案项占比>80%': [],
        '负向准则含具体数值': [],
        '空泛准则': [],
    }

    for r in recs:
        rid = r['rid']
        qtype = r.get('question_type', 'open')
        rubrics = r.get('rubrics', [])
        pos = [c for c in rubrics if c['is_positive']]
        neg = [c for c in rubrics if not c['is_positive']]

        # 检查1: verifiable 答案项占比
        if qtype == 'verifiable' and pos:
            max_score = max(c['score'] for c in pos)
            total = sum(c['score'] for c in pos)
            ratio = max_score / total * 100 if total > 0 else 0
            if ratio < 60:
                issues['答案项占比<60%'].append((rid, f'{ratio:.0f}%', max_score, total))
            elif ratio > 80:
                issues['答案项占比>80%'].append((rid, f'{ratio:.0f}%', max_score, total))

        # 检查2: 负向准则具体数值
        import re
        for c in neg:
            txt = c['criteria']
            if re.search(r"'[0-9A-Fa-f]{2}'|[0-9]+\.[0-9]+|[0-9]{3,}", txt):
                if not re.search(r'\$|\^|_|\{|\}', txt):
                    issues['负向准则含具体数值'].append((rid, txt[:50]))
                    break

        # 检查3: 空泛准则
        vague = ['准确', '完整', '清晰', '合理', '恰当', '正确']
        for c in pos:
            txt = c['criteria']
            if len(txt) < 20 and any(w in txt for w in vague):
                issues['空泛准则'].append((rid, txt))
                break

    return issues, len(recs)


def main():
    original = os.path.join(REPO, 'outputs', 'rubrics_advisor_lean.jsonl')
    new = os.path.join(REPO, 'data', 's04L_rubric.jsonl')

    if not os.path.exists(original):
        print(f'❌ 找不到原始文件: {original}')
        return

    print('=== 修复前（原始 rubrics_advisor_lean.jsonl）===\n')
    issues_old, n_old = check_issues(original)
    for issue_type, samples in issues_old.items():
        if samples:
            print(f'{issue_type}: {len(samples)} 题')
            for item in samples[:3]:
                print(f'  - {item[0]}: {item[1]}')
            if len(samples) > 3:
                print(f'  ... 及其他 {len(samples)-3} 题')
            print()

    if os.path.exists(new):
        print('\n=== 修复后（新生成 s04L_rubric.jsonl）===\n')
        issues_new, n_new = check_issues(new)
        for issue_type, samples in issues_new.items():
            if samples:
                print(f'{issue_type}: {len(samples)} 题')
                for item in samples[:3]:
                    print(f'  - {item[0]}: {item[1]}')
                if len(samples) > 3:
                    print(f'  ... 及其他 {len(samples)-3} 题')
                print()

        # 对比改善
        print('\n=== 改善统计 ===\n')
        for issue_type in issues_old.keys():
            old_n = len(issues_old[issue_type])
            new_n = len(issues_new[issue_type])
            delta = old_n - new_n
            pct = (delta / old_n * 100) if old_n > 0 else 0
            status = '✅' if delta > 0 else ('⚠️' if delta == 0 else '❌')
            print(f'{status} {issue_type}: {old_n} → {new_n} (减少 {delta}, {pct:.0f}%)')

    else:
        print(f'\n⚠️  新文件不存在: {new}')
        print('   请先运行: python3 stages/s04L_rubric.py')


if __name__ == '__main__':
    main()
