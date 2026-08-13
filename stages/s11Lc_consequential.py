"""步骤 11Lc：Consequential 类诊断 —— Hackable 与 Low Signal。

RIFT 八失效模式里唯一需要回复池的两个，所以单独一步，跑在 s12L 之后。
另外六个（Subjective/Non-Atomic/Ungrounded/Factual/Missing/Redundant）在 s11L。

**这一步的价值**：在它之前，「区分度」全靠静态文本分析猜（数「且」字、看有没有
「全部」、看是不是「提及」开头）。那只能说明写法可疑。这里用实测得分说话。

## Low Signal（整份 rubric 级）
质量差异明显的回复，得分是否拉得开。判据三条，任一命中即 defective：
  1. 强档与弱档均值之差 < LOW_GAP（默认 0.25）—— 拉不开
  2. 全部档位得分率的标准差 < LOW_STD（默认 0.12）—— 挤在一起
  3. 所有档位得分率都 >= 0.9 或都 <= 0.1 —— 天花板/地板效应，无分辨力

## Hackable（可到准则级）
低质量回复靠虚增表面特征就能拿高分。判据：
  1. **对抗档得分率 >= 强档** —— 最强信号。verifiable 的对抗档答案是错的，
     open 的对抗档每点只有一句话，它们不该赢过强回复。
  2. 任一弱档（trunc/cut/weak）得分率 >= 强档
  3. 准则级：某条准则在对抗/弱档上判 met，但在强档上判未 met —— 这条准则
     测的是表面特征，不是内容

## 三种弱档造法的一致性检查（设计文档 §10 的关键设计）
若同一条准则在 trunc / cut / weak 三种造法下结论不一致，说明它测的是**长度或
结构**而非内容，本身就是 Hackable 的信号。这是只用一种造法测不出来的。

输入: s12L_judged.jsonl
输出: s11Lc_consequential.jsonl
"""
import json, os, statistics, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

SRC = os.environ.get('RP_S11LC_SRC', 's12L_judged.jsonl')
OUT = os.environ.get('RP_S11LC_OUT', 's11Lc_consequential.jsonl')

LOW_GAP = float(os.environ.get('RP_LOW_GAP', 0.25))
LOW_STD = float(os.environ.get('RP_LOW_STD', 0.12))
WEAK_TIERS = ('trunc', 'cut', 'weak')


def diagnose(r):
    j = dict(r.get('judged') or {})
    if 'strong' not in j:
        return {'low_signal': None, 'hackable': None,
                'skip_reason': '无强档，无法比较'}

    # 排除造法失效的档位。实测 cut 档常只删掉 1%-3% 的字（模型倾向最小改动），
    # 这种「弱档」其实和强档几乎一样，会被读成「删了关键论点仍给分」，
    # 得出完全反向的 Hackable 结论。s10L 已标 degraded，这里据此剔除。
    degraded = {p['tier'] for p in (r.get('pool') or []) if p.get('degraded')}
    for t in degraded:
        j.pop(t, None)

    rate = {t: v['rate'] for t, v in j.items()}
    strong = rate['strong']
    weaks = [rate[t] for t in WEAK_TIERS if t in rate]
    allr = list(rate.values())

    # ---- Low Signal ----
    reasons = []
    gap = strong - (statistics.mean(weaks) if weaks else strong)
    if weaks and gap < LOW_GAP:
        reasons.append(f'强档与弱档均值差 {gap:.1%} < {LOW_GAP:.0%}')
    std = statistics.pstdev(allr) if len(allr) > 1 else 0.0
    if len(allr) > 1 and std < LOW_STD:
        reasons.append(f'各档得分率标准差 {std:.3f} < {LOW_STD}')
    if allr and min(allr) >= 0.9:
        reasons.append('所有档位都 ≥90%，天花板效应')
    if allr and max(allr) <= 0.1:
        reasons.append('所有档位都 ≤10%，地板效应')

    low = {'is_defective': bool(reasons), 'reasons': reasons,
           'strong_rate': round(strong, 4),
           'weak_mean': round(statistics.mean(weaks), 4) if weaks else None,
           'gap': round(gap, 4), 'std': round(std, 4)}

    # ---- Hackable（题级）----
    hreasons = []
    if 'adv' in rate and rate['adv'] >= strong:
        hreasons.append(f'对抗档 {rate["adv"]:.1%} ≥ 强档 {strong:.1%}')
    for t in WEAK_TIERS:
        if t in rate and rate[t] >= strong:
            hreasons.append(f'{t} 档 {rate[t]:.1%} ≥ 强档 {strong:.1%}')

    # ---- Hackable（准则级）+ 弱档造法一致性 ----
    met_of = {}
    for t, v in j.items():
        for x in v['items']:
            met_of[(x['_criterion_id'], t)] = x['met']
    cids = sorted({cid for cid, _ in met_of})

    surface, inconsistent = [], []
    for cid in cids:
        s_met = met_of.get((cid, 'strong'))
        if s_met is None:
            continue
        # 在弱/对抗档满足、强档反而不满足 → 测的是表面特征
        for t in list(WEAK_TIERS) + ['adv']:
            if met_of.get((cid, t)) and not s_met:
                surface.append({'_criterion_id': cid, 'tier': t})
                break
        # 三种造法结论不一致 → 测的是长度或结构而非内容。
        # 必须三档齐全才判：少一档时「不一致」可能只是缺数据，
        # 而 degraded 档已在上面被剔除，这里天然只看有效档。
        avail = [t for t in WEAK_TIERS if (cid, t) in met_of]
        if len(avail) == len(WEAK_TIERS):
            vals = {t: met_of[(cid, t)] for t in avail}
            if len(set(vals.values())) > 1:
                inconsistent.append({'_criterion_id': cid, **vals})

    if surface:
        hreasons.append(f'{len(surface)} 条准则在弱/对抗档满足但强档未满足')

    hack = {'is_defective': bool(hreasons), 'reasons': hreasons,
            'surface_criteria': surface,
            'inconsistent_across_weak': inconsistent}
    return {'low_signal': low, 'hackable': hack, 'skip_reason': '',
            'degraded_tiers': sorted(degraded)}


def main():
    recs = stage.read_jsonl(SRC)
    print(f'步骤 11Lc Consequential 诊断: {len(recs)} 题, 源={SRC}')
    print(f'  判据: Low Signal(gap<{LOW_GAP:.0%} 或 std<{LOW_STD}) / '
          f'Hackable(对抗档或弱档 ≥ 强档)')

    res, low_n, hack_n = [], 0, 0
    all_surface, all_incons = [], []
    for r in recs:
        d = diagnose(r)
        if (d['low_signal'] or {}).get('is_defective'):
            low_n += 1
        if (d['hackable'] or {}).get('is_defective'):
            hack_n += 1
        all_surface += [{**x, 'rid': r['rid']}
                        for x in (d['hackable'] or {}).get('surface_criteria', [])]
        all_incons += [{**x, 'rid': r['rid']}
                       for x in (d['hackable'] or {}).get('inconsistent_across_weak', [])]
        res.append({**r, 'consequential': d})
    stage.write_jsonl(OUT, res)

    n = len(recs)
    print(f'\n=== 步骤 11Lc 结果 ===')
    print(f'  Low Signal  : {low_n}/{n} 题 ({low_n / n:.0%}) 区分不开')
    print(f'  Hackable    : {hack_n}/{n} 题 ({hack_n / n:.0%}) 可被钻空子')
    print(f'  表面特征准则: {len(all_surface)} 条（弱/对抗档满足但强档未满足）')
    print(f'  造法不一致  : {len(all_incons)} 条（trunc/cut/weak 结论打架 → '
          f'测的是长度或结构）')

    if all_surface:
        cid2txt = {c.get('_criterion_id'): c['criteria']
                   for r in res for c in r.get('rubrics') or []}
        print(f'\n  表面特征准则明细:')
        for x in all_surface[:8]:
            print(f'    [{x["rid"]}] {x["tier"]:<6} {cid2txt.get(x["_criterion_id"], "")[:58]}')

    print(f'\n  逐题:')
    for r in res:
        c = r['consequential']
        if c.get('skip_reason'):
            print(f'    {r["rid"]}  跳过：{c["skip_reason"]}')
            continue
        lo, ha = c['low_signal'], c['hackable']
        marks = []
        if lo['is_defective']:
            marks.append('LowSignal')
        if ha['is_defective']:
            marks.append('Hackable')
        print(f'    {r["rid"]}  {r.get("rubric_form",""):<13}'
              f'强={lo["strong_rate"]:6.1%} 弱均={lo["weak_mean"] or 0:6.1%} '
              f'差={lo["gap"]:6.1%} std={lo["std"]:.3f}  '
              f'{" + ".join(marks) if marks else "✓ 通过"}')
        for why in (lo['reasons'] + ha['reasons'])[:3]:
            print(f'         ↳ {why}')


if __name__ == '__main__':
    main()
