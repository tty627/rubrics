#!/usr/bin/env python3
"""导出导师指定 schema 的 rubrics。

用法：
  python3 scripts/export_advisor_schema.py                        # 默认源 = s04Lc_severity
  python3 scripts/export_advisor_schema.py --src data/s04L_rubric.jsonl
  python3 scripts/export_advisor_schema.py --full                 # 另出一份带血缘的内部档

产出：
  outputs/rubrics_advisor_lean.jsonl        交付档，rubrics 只含交付字段
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

负项两个字段（2026-08-14 补，源自 s04Lc_severity）：
  severity     principle / major / minor —— 负向错误的严重性分级
  is_veto      true = 一票否决项。判分侧的聚合规则见 lib/rubric.VETO_RULE：
               「任一 is_veto 项被判定成立 → 整题得分率为 0，不进补偿式求和」
只挂在负向准则上（正向项不带这两个字段）。此前两个字段被 DELIVER_FIELDS
过滤掉，交付档里 severity 全 None、veto 0 条，判分侧无法执行合取门。

**关于归一化**：导师 2026-08-13 明确 score 直接当权重用，不在本步归一，
full_mark = sum(正向 score) 保持原始整数，归一化延后到判分阶段。
因此 is_gate 只是标记，闸门项仍计入 full_mark 分母（与硬约束第 5 条的差异，
是导师指定的口径，判分侧自行处理）。veto 同理只是标记，负项不进 full_mark 分母，
是否归零由判分侧按 VETO_RULE 聚合。

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
# 负项专属交付字段（s04Lc 打的标）。只挂负向准则，正向项不带。
NEGATIVE_FIELDS = ('severity', 'is_veto')

# 内部档带全部 `_` 前缀字段 —— 白名单改成规则（2026-08-14 二次修）。
# 白名单漏字段是**静默的**：上游打了标，导出层不认识就直接丢，审计看不出来。
# 已经漏过两批 —— _flag_subjective_threshold / _flag_topic_list（第一次），
# s04Lb 的 _rewritten_from / _pending_split / _split_skipped / _factfix* /
# _needs_review 与 s04Lc 的 _veto_block / _s04Lc_*（第二次）。
# 内部档的定位就是"上游所有标记的全集"，用规则表达这件事，新增标记自动带上。
# 交付档反过来仍是严格白名单（DELIVER_FIELDS + NEGATIVE_FIELDS + is_gate），
# 内部字段绝不会漏进交付档，所以放宽这一侧不影响交付口径。
def internal_fields(c):
    """内部档要额外带的准则级字段 = 所有 `_` 前缀字段。"""
    return [k for k in c if k.startswith('_')]


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
        # severity / is_veto 只对负向项有意义（正向项挂上去会让判分侧误以为
        # 正向准则也能一票否决）。is_veto 走 lib/rubric 口径：标在正向上不算。
        if not rubric.is_positive(c):
            for k in NEGATIVE_FIELDS:
                if k in c:
                    item[k] = c[k]
            item['is_veto'] = rubric.is_veto(c)
        if full:
            for k in internal_fields(c):
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
        # veto 覆盖情况留在记录级，审计时不用再遍历 rubrics 数组
        rec['_n_veto'] = len(rubric.veto_items(rubrics))
        # 记录级 `_` 前缀字段一律带上（与准则级 internal_fields() 同一条规则）。
        # 上面那几行是硬编码名单，加新步骤时会**静默**漏字段 —— 已经漏过：
        # Phase 4 的 `_s11Ld`（处置动作/原因）与 `_s11Le`（选中轮次/残留缺陷）
        # 都不在名单里，内部档里查不到某题为什么被改、改自哪一轮。
        for k in r:
            if k.startswith('_') and k not in rec:
                rec[k] = r[k]

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
    neg = [c for c in allc if not rubric.is_positive(c)]
    print(f'  正向/负向 : {npos} / {len(neg)}')

    # 负项分级与 veto —— 源里有就必须导出，缺了判分侧执行不了合取门
    sev = Counter(c.get('severity') for c in neg)
    n_sev = sum(1 for c in neg if c.get('severity'))
    print(f'  负项 severity: {n_sev}/{len(neg)} 条带分级  ' +
          '  '.join(f'{k or "(空)"}={sev[k]}' for k in
                   list(rubric.SEVERITY_LEVELS) + [None] if sev[k]))
    vetoes = [c for c in neg if c.get('is_veto')]
    q_veto = sum(1 for r in recs
                 if any(c.get('is_veto') for c in r['rubrics']))
    print(f'  is_veto     : {len(vetoes)} 条 / 覆盖 {q_veto} 题'
          f'（{q_veto / max(len(recs), 1) * 100:.1f}%）')
    print(f'    聚合规则  : {rubric.VETO_RULE}')
    if neg and not n_sev:
        print('    ⚠️  负项一条分级都没有 —— 源文件应是 s04Lc_severity.jsonl，'
              '否则判分侧无法执行 veto')
    bad_veto = [c for c in allc if c.get('is_veto') and rubric.is_positive(c)]
    if bad_veto:
        print(f'    ⚠️  {len(bad_veto)} 条正向项带 is_veto（方向错，应只标负项）')
    bad_sev = [c for c in vetoes if c.get('severity') != 'principle']
    if bad_sev:
        print(f'    ⚠️  {len(bad_sev)} 条 veto 不是 principle 级'
              f'（veto 门槛第 2 条：只有原则性错误能一票否决）')

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
    # 默认源 = 流水线末端。链路：s04L → s11L 诊断 → s11Lb 处置 → s04Lb 拆分/重写
    # → s04Lc 负项分级。默认值指向中间步会静默丢掉后续步骤的产出
    # （2026-08-13 交付档一条没过 RIFT、2026-08-14 severity/veto 全空，都是这个坑）。
    ap.add_argument('--src', default=os.path.join(REPO, 'data', 's04Lc_severity.jsonl'),
                    help='源 jsonl，默认 data/s04Lc_severity.jsonl（流水线末端：'
                         '已过 RIFT 诊断处置 + 缺陷重写 + 负项分级）')
    ap.add_argument('--out', default=os.path.join(REPO, 'outputs', 'rubrics_advisor_lean.jsonl'))
    ap.add_argument('--full', action='store_true',
                    help='另出一份 outputs/rubrics_internal.jsonl，带血缘/诊断/质量标记')
    a = ap.parse_args()

    src = a.src if os.path.isabs(a.src) else os.path.join(REPO, a.src)
    if not os.path.exists(src):
        sys.exit(f'缺少 {src}\n'
                 f'lean 线请先跑 stages/s04L_rubric.py → s11L_diagnose.py → '
                 f's11Lb_remedy.py → s04Lb_split.py → s04Lc_severity.py')

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

    # 上游标记汇总。同样按规则枚举（所有 `_` 前缀 + 值为真的字段），
    # 不写死名字 —— 写死会让新加的标记在这份对账里看不见（漏过两批）。
    # 血缘字段每条都有，逐条打印没信息量，排掉。
    LINEAGE = {'_criterion_id', '_dim_from_table',
               '_perspective_ids', '_scenario_ids'}
    flags = Counter()
    for r in raw:
        for c in r.get('rubrics') or []:
            for k, v in c.items():
                if k.startswith('_') and k not in LINEAGE and v:
                    flags[k] += 1
    if flags:
        print(f'\n  上游标记（_ 前缀，血缘除外）:')
        for k, n in flags.most_common():
            print(f'    {k:<28} {n:5d} 条')

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
