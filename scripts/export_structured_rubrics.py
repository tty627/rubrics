#!/usr/bin/env python3
"""
导出 rubrics 结构化展示 jsonl
源数据: data/s09_normalized.jsonl（与 xlsx C 列同源，s11b 经核对未删除任何准则）
产出:   outputs/rubrics_structured.jsonl

每条记录为一份完整 rubric，按维度分组，扣分项单独列出。
字段说明见 docs/advisor/ 或文件尾部示例。
"""

import json
import os

def round2(x):
    if isinstance(x, (int, float)):
        return round(x, 2)
    return x

def build_record(r):
    # 有效问题：改写后的优先，否则用原题
    rewritten = r.get('rewritten', '') or ''
    question = rewritten if rewritten else r.get('question', '')
    rec = {
        'rid': r.get('rid'),
        'xlsx_row': r.get('xlsx_row'),
        'question': question,
        'subject': r.get('subject', []),
        'question_type': r.get('question_type'),
        'rubric_form': r.get('rubric_form'),
        's_max': r.get('s_max'),
        's_max_raw': r.get('s_max_raw'),
    }
    # 若经过改写，保留原题供参照
    if rewritten and rewritten != r.get('question'):
        rec['question_original'] = r.get('question')

    # 按维度分组 base 准则，penalty 单独收集
    dims = {}
    dim_order = []
    penalties = []
    for c in r.get('criteria', []):
        ctype = c.get('criterion_type', 'base')
        if ctype == 'penalty':
            penalties.append({
                'criterion_id': c.get('criterion_id'),
                'condition': c.get('positive'),
                'score': round2(c.get('score')),
                'normalized_score': round2(c.get('normalized_score')),
                'rationale': c.get('rationale'),
            })
        else:
            dname = c.get('dimension', '未分类')
            if dname not in dims:
                dims[dname] = []
                dim_order.append(dname)
            dims[dname].append({
                'criterion_id': c.get('criterion_id'),
                'positive': c.get('positive'),
                'negative': c.get('negative'),
                'score': round2(c.get('score')),
                'normalized_score': round2(c.get('normalized_score')),
                'rationale': c.get('rationale'),
            })

    rec['dimension_count'] = len(dim_order)
    rec['criteria_count'] = sum(len(v) for v in dims.values())
    rec['penalty_count'] = len(penalties)
    rec['dimensions'] = [
        {'name': name, 'criteria': dims[name]} for name in dim_order
    ]
    rec['penalties'] = penalties
    return rec

def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(repo_root, 'data', 's09_normalized.jsonl')
    out_dir = os.path.join(repo_root, 'outputs')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, 'rubrics_structured.jsonl')

    n = 0
    with open(src) as f, open(out, 'w', encoding='utf-8') as g:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            g.write(json.dumps(build_record(r), ensure_ascii=False) + '\n')
            n += 1

    print(f'✅ 已生成 {out}')
    print(f'   记录数: {n}')
    print(f'   文件大小: {os.path.getsize(out)/1024/1024:.1f} MB')

if __name__ == '__main__':
    main()
