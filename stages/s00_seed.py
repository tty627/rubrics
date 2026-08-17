"""Phase 0：xlsx → seed.jsonl。同时算草稿 rubric 的基线结构指标。"""
import json, os, sys, ast
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import xlsx

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX = os.environ.get('RP_XLSX', os.path.join(_ROOT, 'data', 'input.xlsx'))
OUT = os.environ.get('RP_OUT', os.path.join(_ROOT, 'data'))

# xlsx 列映射：按你实际表的表头顺序调整，或设表头行自动读取
# 当前假定：A=need_rewrite, B=rewritten, C=gen_rubric, D=question,
#           E=dimension, F=draft_rubric, G=ref_response
COL = {'need_rewrite': 0, 'rewritten': 1, 'gen_rubric': 2,
       'question': 3, 'dimension': 4, 'draft_rubric': 5, 'ref_response': 6}


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = xlsx.read(XLSX)
    hdr, data = rows[0], rows[1:]
    print(f'表头: {[hdr.get(i, "") for i in range(7)]}')
    print(f'数据行: {len(data)}')

    recs, bad = [], 0
    for i, r in enumerate(data):
        rid = f'q{i + 2:04d}'          # 与 xlsx 行号对齐，便于回查
        draft_raw = r.get(COL['draft_rubric'], '')
        try:
            draft = json.loads(draft_raw) if draft_raw else None
        except Exception:
            draft, bad = None, bad + 1
        try:
            dims = ast.literal_eval(r.get(COL['dimension'], '') or '[]')
        except Exception:
            dims = []
        refs = {}
        try:
            refs = json.loads(r.get(COL['ref_response'], '') or '{}')
        except Exception:
            pass
        recs.append({
            'rid': rid, 'xlsx_row': i + 2,
            'question': r.get(COL['question'], ''),
            'subject': dims,
            'draft_rubric': draft,
            'ref_responses': {k: v for k, v in refs.items() if not k.endswith('_error')},
            'ref_errors': [k for k in refs if k.endswith('_error')],
        })
    print(f'draft_rubric 解析失败: {bad}')

    with open(f'{OUT}/seed.jsonl', 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    baseline(recs)
    print(f'\n写出 {OUT}/seed.jsonl')


def baseline(recs):
    """草稿 rubric 的基线指标，作为验收对照。"""
    dims, ncrit, fullmarks, negcnt, qtype = Counter(), [], [], Counter(), Counter()
    nref = Counter()
    for r in recs:
        nref[len(r['ref_responses'])] += 1
        d = r['draft_rubric']
        if not d:
            continue
        qtype[d.get('question_type')] += 1
        cl = d.get('rubrics', [])
        ncrit.append(len(cl))
        pos = [c for c in cl if c.get('score', 0) > 0]
        neg = [c for c in cl if c.get('score', 0) < 0]
        negcnt[len(neg)] += 1
        fullmarks.append(sum(c['score'] * c['weight'] for c in pos))
        for c in cl:
            dims[c.get('dimension')] += 1

    fm = sorted(fullmarks)
    total = sum(dims.values())
    # 草稿 rubric 允许整批缺失（新数据集可能没有草稿，检查点 2 的对照另行补）
    crit_stat = ({'min': min(ncrit), 'max': max(ncrit),
                  'mean': round(sum(ncrit) / len(ncrit), 2)} if ncrit
                 else {'min': 0, 'max': 0, 'mean': 0.0})
    fm_stat = ({'min': fm[0], 'p50': fm[len(fm) // 2], 'max': fm[-1]} if fm
               else {'min': 0, 'p50': 0, 'max': 0})
    rep = {
        'n_records': len(recs),
        'question_type': dict(qtype),
        'n_criteria_total': total,
        'n_criteria_per_q': crit_stat,
        'dimension_uniq': len(dims),
        'dimension_dist': dict(dims.most_common()),
        'fullmark': fm_stat,
        'neg_item_count_dist': dict(sorted(negcnt.items())),
        'ref_response_count_dist': dict(sorted(nref.items())),
    }
    with open(f'{OUT}/baseline.json', 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print('\n=== 基线（草稿 rubric）===')
    print(f'  维度去重数     : {rep["dimension_uniq"]}   {list(dims)[:3]}')
    print(f'  准则数/题      : {rep["n_criteria_per_q"]}')
    print(f'  每题满分       : {rep["fullmark"]}')
    print(f'  负向项个数分布 : {rep["neg_item_count_dist"]}')
    print(f'  参考回复数分布 : {rep["ref_response_count_dist"]}')


if __name__ == '__main__':
    main()
