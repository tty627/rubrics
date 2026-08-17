#!/usr/bin/env python3
"""rubrics 质量审计 —— 把交付档的结构与可判定性问题一次性查出来。

用法：
  python3 scripts/audit_rubrics.py                          # 审计当前交付档
  python3 scripts/audit_rubrics.py outputs/rubrics_advisor_lean.jsonl
  python3 scripts/audit_rubrics.py 新.jsonl --base 旧.jsonl  # 两版对比

替代 tests/test_s04_flags.py（那个只查 3 类，且写死了「修复前=交付档」的比较对象）。

检查项分三类：
  结构   题数/准则数/维度分布/分数尺度/schema 完整性
  可判定 判分器能不能拿着这条准则独立给出一致的是否判定
  区分度 这条准则能不能把好回答和差回答分开

判定用的正则直接从 stages/s04_rubric.py 引，避免护栏和审计各写一套导致漂移。
"""
import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lib import rubric

# 复用 s04_rubric 护栏的判定口径
import importlib.util
_spec = importlib.util.spec_from_file_location(
    's04L', os.path.join(REPO, 'stages', 's04_rubric.py'))
_s04mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s04mod)
VAGUE, ANCHOR, REF_ONLY = _s04mod.VAGUE, _s04mod.ANCHOR, _s04mod.REF_ONLY
BULK, MENTION, SUBJ_DEG = _s04mod.BULK, _s04mod.MENTION, _s04mod.SUBJ_DEG

# 审计独有的判定（护栏里没有，因为这些不适合在生成期硬拦）
NONATOMIC_HINT = re.compile(r'且|并且|同时')
OVERFIT_NEG = re.compile(r'如答|答\s*\d|为\s*\d+\s*个|误答为|误读为|如估算|如偏离|选项[A-D]')
SPECULATIVE = re.compile(r'多半|可能是|大概率|推测|想必|应该是')


def audit(path):
    recs = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
    allc = [(r, c) for r in recs for c in r.get('rubrics', [])]
    pos = [(r, c) for r, c in allc if rubric.is_positive(c)]
    neg = [(r, c) for r, c in allc if not rubric.is_positive(c)]
    n_q, n_c = len(recs), len(allc)
    m = {'_path': path, '_n_q': n_q, '_n_c': n_c}

    print(f'\n{"=" * 72}\n审计 {os.path.relpath(path, REPO)}\n{"=" * 72}')

    # ---- 结构 ----
    per_q = [len(r.get('rubrics', [])) for r in recs]
    print(f'\n【结构】')
    print(f'  题目 {n_q}  准则 {n_c}  正/负 {len(pos)}/{len(neg)}')
    if per_q:
        s = sorted(per_q)
        print(f'  准则/题  min={s[0]} p50={s[len(s) // 2]} max={s[-1]} '
              f'mean={n_c / n_q:.2f}')
    dims = Counter(c.get('dimension') for _, c in allc)
    per_dim = [len({c.get('dimension') for c in r.get('rubrics', [])}) for r in recs]
    print(f'  维度  {len(dims)} 种，每题去重 mean={statistics.mean(per_dim):.2f}'
          f'  单一维度的题={sum(1 for x in per_dim if x == 1)}')
    top2 = sum(v for k, v in dims.most_common(2))
    print(f'  最大两维占比 {100 * top2 / max(n_c, 1):.1f}% '
          f'({" / ".join(k for k, _ in dims.most_common(2))})')
    fm = [r.get('full_mark', 0) for r in recs]
    if fm:
        print(f'  full_mark  {min(fm)}~{max(fm)}  '
              f'(导师口径：原始权重，不归一)')
    bad_fm = sum(1 for r in recs
                 if r.get('full_mark') != rubric.s_max(r.get('rubrics', [])))
    m['full_mark 与正项和不符'] = bad_fm

    # ---- schema 完整性 ----
    print(f'\n【schema】')
    for f in ('rubric_form', 'blocks'):
        n = sum(1 for r in recs if r.get(f))
        print(f'  {f:<12} {n}/{n_q} 题')
    n_gate = sum(1 for _, c in allc if c.get('is_gate'))
    n_gform = sum(1 for r in recs if r.get('rubric_form') == 'gated_answer')
    print(f'  is_gate      {n_gate} 条 / gated_answer {n_gform} 题')
    m['闸门丢失'] = max(0, n_gform - n_gate)
    # 负项分级 + veto（s04c_severity 起进交付档）。缺了判分侧执行不了合取门，
    # 而缺失是静默的 —— 导出源指向 s04c_severity 之前的步骤就会全空，所以计入指标对账。
    n_sev = sum(1 for _, c in neg if c.get('severity'))
    sev = Counter(c.get('severity') for _, c in neg if c.get('severity'))
    print(f'  severity     {n_sev}/{len(neg)} 条负项  ' +
          '  '.join(f'{k}={sev[k]}' for k in rubric.SEVERITY_LEVELS if sev[k]))
    n_veto = sum(1 for _, c in allc if c.get('is_veto'))
    q_veto = len({r['rid'] for r, c in allc if c.get('is_veto')})
    print(f'  is_veto      {n_veto} 条 / 覆盖 {q_veto} 题')
    m['负项缺 severity'] = len(neg) - n_sev
    # `~` 前缀 = 中性覆盖度指标，不是缺陷。对比区里"增加"对缺陷是坏事、
    # 对覆盖度是好事，两者混在一起看会把 veto 铺开误报成回归。
    m['~veto 条数'] = n_veto

    # ---- 可判定性 ----
    print(f'\n【可判定性】判分器能否独立给出一致判定')
    hits = {}

    def hit(name, pairs, note=''):
        hits[name] = pairs
        m[name] = len(pairs)
        qs = len({r['rid'] for r, _ in pairs})
        flag = '❌' if pairs else '✅'
        print(f'  {flag} {name:<22} {len(pairs):4d} 条  涉及 {qs:3d} 题  {note}')

    hit('引用标准答案未写出',
        [(r, c) for r, c in allc
         if REF_ONLY.search(c['criteria']) and not ANCHOR.search(c['criteria'])],
        '判分器手上没有标准答案')
    hit('空泛词无锚点',
        [(r, c) for r, c in allc
         if any(w in c['criteria'] for w in VAGUE) and not ANCHOR.search(c['criteria'])])
    hit('负项主观阈值',
        [(r, c) for r, c in neg
         if SUBJ_DEG.search(c['criteria']) and not ANCHOR.search(c['criteria'])],
        '严重/显著/根本性，无判定线')
    hit('疑似非原子',
        [(r, c) for r, c in allc if NONATOMIC_HINT.search(c['criteria'])],
        '启发式，以 RIFT 诊断为准')
    # veto 项的门槛比普通负项高一档：一票否决整题，判定线必须可一致执行。
    # s04c_severity 有代码兜底，这里独立复核一遍（导出层若换源、门槛若放松，这里会亮）。
    veto_pairs = [(r, c) for r, c in allc if c.get('is_veto')]
    hit('veto 项非原子',
        [(r, c) for r, c in veto_pairs
         if NONATOMIC_HINT.search(c['criteria']) or '或' in c['criteria']],
        'veto 必须单一错误，捆多个判不一致')
    hit('veto 项主观阈值',
        [(r, c) for r, c in veto_pairs
         if SUBJ_DEG.search(c['criteria']) and not ANCHOR.search(c['criteria'])],
        'veto 门槛第 3 条：无判定线不能当 0/1 门')
    hit('veto 项非 principle 级',
        [(r, c) for r, c in veto_pairs if c.get('severity') != 'principle'],
        'veto 门槛第 2 条：只有原则性错误能否决')

    # ---- 区分度 ----
    print(f'\n【区分度】能否把好回答和差回答分开')
    cliff = [(r, c) for r, c in pos
             if r.get('full_mark') and c['score'] / r['full_mark'] >= 0.5
             and (BULK.search(c['criteria']) or '且' in c['criteria'])]
    hit('全量复合悬崖', cliff, '单条≥50%满分且要求"全部/且"，实为0/1')
    hit('提及即得分',
        [(r, c) for r, c in pos
         if MENTION.match(c['criteria']) and not ANCHOR.search(c['criteria'])
         and len(c['criteria']) <= 20 and not BULK.search(c['criteria'])])
    hit('负项写死具体错误答案',
        [(r, c) for r, c in neg if OVERFIT_NEG.search(c['criteria'])],
        '过拟合参考错误，答成别的就逃掉')

    # verifiable 答案项占比。
    # 口径按 is_gate 而非 dimension=='答案准确性'：s04b_split 拆悬崖项时会把一条
    # +8 拆成「规则对不对」+「条目全不全」，后者常落到「要点完整性」维度，
    # 但它仍是答案判据的一半。只认单一维度会把拆分误报成占比下降。
    ver = [r for r in recs if r.get('question_type') == 'verifiable' and r.get('full_mark')]
    if ver:
        ratios = []
        for r in ver:
            a = sum(c['score'] for c in r['rubrics'] if c.get('is_gate'))
            if not a:      # 没标闸门的（如 multi_part 路由）退回按维度算
                a = sum(c['score'] for c in r['rubrics']
                        if c.get('is_positive') and c.get('dimension') == '答案准确性')
            ratios.append(a / r['full_mark'])
        off = sum(1 for x in ratios if not 0.6 <= x <= 0.8)
        print(f'  {"❌" if off > len(ver) * 0.2 else "✅"} '
              f'{"verifiable答案项占比":<22} mean={statistics.mean(ratios):.2f}  '
              f'落在60-80%外的 {off}/{len(ver)} 题')
        m['verifiable答案项占比偏离'] = off

    # ---- 其他 ----
    print(f'\n【其他】')
    spec = [r for r in recs if SPECULATIVE.search(r.get('intent', ''))]
    m['intent 臆测'] = len(spec)
    print(f'  intent 含臆测措辞  {len(spec)}/{n_q} 题 ({100 * len(spec) / max(n_q, 1):.1f}%)')
    subj = Counter((r.get('subject') or ['?'])[0] for r in recs)
    t2 = sum(v for _, v in subj.most_common(2))
    print(f'  学科集中度        前二占 {100 * t2 / max(n_q, 1):.1f}% '
          f'({" / ".join(k for k, _ in subj.most_common(2))})')

    # 抽样
    print(f'\n【抽样】每类各两条')
    for name, pairs in hits.items():
        if not pairs:
            continue
        print(f'  {name}:')
        for r, c in pairs[:2]:
            print(f'    [{r["rid"]}] {c["score"]:+d} {c["criteria"][:62]}')
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path', nargs='?',
                    default=os.path.join(REPO, 'outputs', 'rubrics_advisor_lean.jsonl'))
    ap.add_argument('--base', help='对比基准 jsonl（如上一版）')
    a = ap.parse_args()

    if not os.path.exists(a.path):
        sys.exit(f'找不到 {a.path}')
    new = audit(a.path)

    if a.base and os.path.exists(a.base):
        old = audit(a.base)
        print(f'\n{"=" * 72}\n对比（基准 → 当前）\n{"=" * 72}')
        keys = [k for k in new if not k.startswith('_')]
        w = max(len(k) for k in keys)
        for k in keys:
            o, n = old.get(k, 0), new[k]
            if o == n == 0:
                continue
            d = n - o
            if k.startswith('~'):      # 中性覆盖度：只报变化，不判好坏
                arrow = '  '
            else:
                arrow = '✅' if d < 0 else ('⚠️ ' if d > 0 else '  ')
            print(f'  {arrow} {k:<{w}}  {o:5d} → {n:5d}  ({d:+d})')
        print(f'\n  准则总数  {old["_n_c"]} → {new["_n_c"]}   '
              f'题目数 {old["_n_q"]} → {new["_n_q"]}')


if __name__ == '__main__':
    main()
