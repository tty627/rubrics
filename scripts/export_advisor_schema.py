#!/usr/bin/env python3
"""导出导师指定 schema 的 rubrics。

用法：
  python3 scripts/export_advisor_schema.py                        # 默认源 = s11Lb_remedied
  python3 scripts/export_advisor_schema.py --src data/s04L_rubric.jsonl
  python3 scripts/export_advisor_schema.py --full                 # 另出一份带血缘的内部档

产出：
  outputs/rubrics_advisor_lean.jsonl        交付档，rubrics 只含 5 个交付字段
  outputs/rubrics_internal.jsonl  (--full)  内部档，带血缘 + 诊断 + 质量标记

交付 schema（导师给定）:
  criteria     准则文本
  score        原始整数分（正向 1-3，verifiable 的答案项 6-8；负向 -2/-3）
  reason       为什么这条是基本要求
  dimension    通用维度词表中的一项
  is_positive  true=该做到的  false=不该出现的

记录级字段除 question/subject/intent/full_mark 外，另带三项（2026-08-13 补）：
  rubric_form  gated_answer / analytic / multi_part —— 判分器据此决定闸门语义
  is_gate      准则级。标出 gated_answer 题的答案项是哪一条
  blocks       multi_part 题的分块结构，44 条 hybrid 此前被拍平成单一清单

**关于归一化**：导师 2026-08-13 明确 score 直接当权重用，不在本步归一，
full_mark = sum(正向 score) 保持原始整数，归一化延后到判分阶段。
因此 is_gate 只是标记，闸门项仍计入 full_mark 分母（与硬约束第 5 条的差异，
是导师指定的口径，判分侧自行处理）。

内部字段（`_` 前缀）只进 --full 档：
血缘标签是设计文档硬约束第 4 条，步骤 13 按维度聚合失败原因、步骤 14 回灌都依赖。
"""
import argparse
import json
import os
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib import rubric

DELIVER_FIELDS = ('criteria', 'score', 'reason', 'dimension', 'is_positive')
# 内部档额外带的准则级字段。
# 2026-08-14 修：此前漏了 _flag_subjective_threshold / _flag_topic_list，
# 两个质量标记被静默丢弃（源数据 1 条 subjective_threshold，内部档 0 条）。
INTERNAL_FIELDS = ('_criterion_id', '_dim_from_table', '_perspective_ids',
                   '_scenario_ids', '_flag_vague', '_flag_no_groundtruth',
                   '_flag_cliff', '_flag_mention_only',
                   '_flag_subjective_threshold', '_flag_topic_list')


def build_record(r, full=False):
    """把一条 stage 记录转成交付/内部 schema。返回 None 表示该题无有效准则。"""
    rubrics = r.get('rubrics') or []
    if not rubrics:
        return None

    form = r.get('rubric_form', '')
    pos = rubric.positives(rubrics)
    if not pos:
        return None

    # gated_answer 的答案项 = 正向里分值最高的那条（s04L 的分值规则给到 6-8 分）。
    # 要求 >=4 分且唯一：若答案项已被诊断删除，剩下全是 1 分支撑项，此时不该
    # 把某条支撑项冒充成闸门 —— 宁可不标，让下游看得出这题的闸门丢了。
    # 闸门可能不止一条：s04Lb 会把「全部/每一个都对」式的悬崖项拆成
    # 「规则对不对」+「条目全不全」两条各占一半分值（q0358 的 +8 → +4/+4）。
    # 拆完就没有唯一最高分了，但这两条合起来仍是这道题的答案判据，都该标 is_gate。
    # 判定口径 = lib/rubric 闸门规则：分值 >= 4 且 >= 满分的 30%（s_max 分母只算
    # 正向）。4 分下限避免答案项真被删掉时把 1 分的支撑项冒充成闸门。
    gate_idx = set()
    if form == 'gated_answer':
        gate_idx = set(rubric.gate_indices(rubrics))

    # cid → 诊断结果，供内部档挂回
    diag = {d['_criterion_id']: d for d in (r.get('diagnoses') or [])
            if d.get('_criterion_id')}

    out = []
    for i, c in enumerate(rubrics):
        item = {k: c[k] for k in DELIVER_FIELDS if k in c}
        item['is_gate'] = (i in gate_idx)
        if full:
            for k in INTERNAL_FIELDS:
                if k in c:
                    item[k] = c[k]
            d = diag.get(c.get('_criterion_id'))
            if d:
                item['_failure_modes'] = d.get('failure_modes') or []
                item['_is_defective'] = bool(d.get('is_defective'))
        out.append(item)

    rec = {
        'rid': r['rid'],
        'xlsx_row': r.get('xlsx_row'),
        'question': r.get('query_eff') or r.get('question', ''),
        'subject': r.get('subject', []),
        'question_type': r.get('question_type', ''),
        'rubric_form': form,
        'intent': r.get('intent', ''),
        'full_mark': rubric.s_max(rubrics),
        'rubrics': out,
    }
    # multi_part 才带 blocks，其余题型这个字段没有意义
    if form == 'multi_part' and r.get('blocks'):
        rec['blocks'] = r['blocks']

    if full:
        rec['_remedy_skipped'] = bool(r.get('remedy_skipped'))
        rec['_skip_reason'] = r.get('skip_reason', '')
        rec['_criteria_removed'] = r.get('criteria_removed', 0)
        rec['_needs_regen'] = bool(r.get('needs_regen'))

    return rec


def report(recs, src):
    """把交付档的结构指标打出来，便于和上一版对账。"""
    allc = [c for r in recs for c in r['rubrics']]
    per_q = [len(r['rubrics']) for r in recs]
    print(f'\n  源文件    : {src}')
    print(f'  题目数    : {len(recs)}')
    print(f'  准则总数  : {len(allc)}')
    if per_q:
        s = sorted(per_q)
        print(f'  准则/题   : min={s[0]} p50={s[len(s) // 2]} max={s[-1]} '
              f'mean={len(allc) / len(recs):.1f}')

    npos = sum(1 for c in allc if rubric.is_positive(c))
    print(f'  正向/负向 : {npos} / {len(allc) - npos}')

    forms = Counter(r['rubric_form'] or '(空)' for r in recs)
    print(f'  rubric_form: ' + '  '.join(f'{k}={v}' for k, v in forms.most_common()))
    print(f'  带 blocks : {sum(1 for r in recs if r.get("blocks"))} 题')
    n_gate_form = sum(1 for r in recs if r['rubric_form'] == 'gated_answer')
    n_gate_mark = sum(1 for c in allc if c.get('is_gate'))
    print(f'  is_gate 标出: {n_gate_mark} 条 / gated_answer {n_gate_form} 题')
    if n_gate_mark < n_gate_form:
        lost = [r['rid'] for r in recs
                if r['rubric_form'] == 'gated_answer'
                and not any(c.get('is_gate') for c in r['rubrics'])]
        print(f'    ⚠️  闸门丢失 {len(lost)} 题（答案项被诊断删除）: {lost[:8]}')

    dims = Counter(c['dimension'] for c in allc if 'dimension' in c)
    print(f'\n  维度分布 ({len(dims)} 种):')
    for d, n in dims.most_common():
        print(f'    {d:<14} {n:5d} ({n / max(len(allc), 1) * 100:4.1f}%)')

    # gated 答案项占比（设计要求 60-80%）
    shares = []
    for r in recs:
        if r['rubric_form'] != 'gated_answer' or not r['full_mark']:
            continue
        g = [c['score'] for c in r['rubrics'] if c.get('is_gate')]
        if g:
            # 拆分后闸门可能是两条，合起来才是答案判据，按和算占比
            shares.append(sum(g) / r['full_mark'] * 100)
    if shares:
        ok = sum(1 for x in shares if 60 <= x <= 80)
        print(f'\n  gated 答案项占比: mean={sum(shares) / len(shares):.1f}%  '
              f'落在 60-80% 的: {ok}/{len(shares)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=os.path.join(REPO, 'data', 's11Lb_remedied.jsonl'),
                    help='源 jsonl，默认 data/s11Lb_remedied.jsonl（已过 RIFT 诊断处置）')
    ap.add_argument('--out', default=os.path.join(REPO, 'outputs', 'rubrics_advisor_lean.jsonl'))
    ap.add_argument('--full', action='store_true',
                    help='另出一份 outputs/rubrics_internal.jsonl，带血缘/诊断/质量标记')
    a = ap.parse_args()

    src = a.src if os.path.isabs(a.src) else os.path.join(REPO, a.src)
    if not os.path.exists(src):
        sys.exit(f'缺少 {src}\n'
                 f'lean 线请先跑 stages/s04L_rubric.py → s11L_diagnose.py → s11Lb_remedy.py')

    with open(src, encoding='utf-8') as f:
        raw = [json.loads(l) for l in f if l.strip()]

    deliver, internal, empty = [], [], []
    for r in raw:
        d = build_record(r, full=False)
        if d is None:
            empty.append(r['rid'])
            continue
        deliver.append(d)
        if a.full:
            internal.append(build_record(r, full=True))

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8') as f:
        for r in deliver:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'已写出 {a.out}')

    if a.full:
        p = os.path.join(os.path.dirname(a.out), 'rubrics_internal.jsonl')
        with open(p, 'w', encoding='utf-8') as f:
            for r in internal:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        print(f'已写出 {p}（含血缘/诊断/质量标记）')

    report(deliver, os.path.relpath(src, REPO))
    if empty:
        print(f'\n  ⚠️  空结果 : {len(empty)} 题 {empty[:8]}')

    # 质量标记汇总（s04L 的 flag() 护栏打的标，只在源里存在时才有）
    flags = Counter()
    for r in raw:
        for c in r.get('rubrics') or []:
            for k in ('_flag_vague', '_flag_no_groundtruth', '_flag_cliff',
                      '_flag_mention_only', '_flag_subjective_threshold',
                      '_flag_topic_list'):
                if c.get(k):
                    flags[k] += 1
    if flags:
        print(f'\n  质量标记（待 s04Lb 处理）:')
        for k, n in flags.most_common():
            print(f'    {k:<24} {n:5d} 条')

    # 与草稿对比 —— 注意准则数不再是"越多越好"的指标
    base = os.path.join(REPO, 'data', 'baseline.json')
    if os.path.exists(base) and deliver:
        b = json.load(open(base, encoding='utf-8'))
        n_all = sum(len(r['rubrics']) for r in deliver)
        n_dim = len({c['dimension'] for r in deliver for c in r['rubrics']
                     if 'dimension' in c})
        print(f'\n  对比草稿:')
        print(f'    准则数  {b["n_criteria_per_q"]["mean"]:.1f} → '
              f'{n_all / len(deliver):.1f}  (粒度对齐，不再追求更多)')
        print(f'    维度数  {b["dimension_uniq"]} → {n_dim}  '
              f'(草稿全是「知识正确性」单一维度)')


if __name__ == '__main__':
    main()
