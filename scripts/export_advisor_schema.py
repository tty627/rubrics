#!/usr/bin/env python3
"""导出导师指定 schema 的 rubrics。

源: data/s04b_core.jsonl（核心筛选后）
产出:
  outputs/rubrics_advisor.jsonl  每行一题，rubrics 只含 5 个交付字段
  outputs/excel/*.xlsx           填回原表 C 列（另由 fill_xlsx 脚本处理）

交付 schema（导师给定）:
  criteria     准则文本
  score        原始整数分（正向 1-3，verifiable 的答案项 6-8；负向 -2/-3）
  reason       为什么这条是基本要求
  dimension    通用维度词表中的一项
  is_positive  true=该做到的  false=不该出现的

内部字段（`_` 前缀）不进交付，但保留在 s04b_core.jsonl 里：
血缘标签是设计文档硬约束第 4 条，步骤 13 按维度聚合失败原因、步骤 14 回灌都依赖。
"""
import json
import os
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DELIVER_FIELDS = ('criteria', 'score', 'reason', 'dimension', 'is_positive')


def main():
    src = os.path.join(REPO, 'data', 's04b_core.jsonl')
    if not os.path.exists(src):
        sys.exit('缺少 data/s04b_core.jsonl，先跑 stages/s04b_core.py')

    out_dir = os.path.join(REPO, 'outputs')
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, 'rubrics_advisor.jsonl')

    n_rub = 0
    dims = Counter()
    per_q = []
    empty = []

    with open(src, encoding='utf-8') as fi, open(dst, 'w', encoding='utf-8') as fo:
        for line in fi:
            r = json.loads(line)
            rubrics = [
                {k: c[k] for k in DELIVER_FIELDS if k in c}
                for c in r.get('rubrics', [])
            ]
            if not rubrics:
                empty.append(r['rid'])
                continue

            pos = [c for c in rubrics if c.get('is_positive')]
            rec = {
                'rid': r['rid'],
                'xlsx_row': r.get('xlsx_row'),
                'question': r.get('query_eff') or r.get('question', ''),
                'subject': r.get('subject', []),
                'question_type': r.get('question_type', ''),
                'intent': r.get('intent', ''),
                'full_mark': sum(c['score'] for c in pos),
                'rubrics': rubrics,
            }
            fo.write(json.dumps(rec, ensure_ascii=False) + '\n')

            n_rub += len(rubrics)
            per_q.append(len(rubrics))
            for c in rubrics:
                dims[c['dimension']] += 1

    print(f'已写出 {dst}')
    print(f'  题目数    : {len(per_q)}')
    print(f'  准则总数  : {n_rub}')
    if per_q:
        s = sorted(per_q)
        print(f'  准则/题   : min={s[0]} p50={s[len(s) // 2]} max={s[-1]} '
              f'mean={n_rub / len(per_q):.1f}')
    if empty:
        print(f'  ⚠️  空结果 : {len(empty)} 题 {empty[:8]}')

    print(f'\n  维度分布 ({len(dims)} 种):')
    for d, n in dims.most_common():
        print(f'    {d:<14} {n:5d} ({n / n_rub * 100:4.1f}%)')

    # 与草稿对比 —— 注意准则数不再是"越多越好"的指标
    base = os.path.join(REPO, 'data', 'baseline.json')
    if os.path.exists(base) and per_q:
        b = json.load(open(base, encoding='utf-8'))
        print(f'\n  对比草稿:')
        print(f'    准则数  {b["n_criteria_per_q"]["mean"]:.1f} → '
              f'{n_rub / len(per_q):.1f}  (粒度对齐，不再追求更多)')
        print(f'    维度数  {b["dimension_uniq"]} → {len(dims)}  '
              f'(草稿全是「知识正确性」单一维度)')


if __name__ == '__main__':
    main()
