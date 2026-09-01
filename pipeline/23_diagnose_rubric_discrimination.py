"""步骤 11c：Consequential 类诊断 —— Hackable 与 Low Signal。

RIFT 八失效模式里唯一需要回复池的两个，所以单独一步，跑在 s12_judge 之后。
另外六个（Subjective/Non-Atomic/Ungrounded/Factual/Missing/Redundant）在 s11_diagnose。

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
  2. weak 档得分率 >= 强档
  3. 准则级：某条正向准则在弱/对抗档上判 met，但在强档上判未 met —— 这条准则
     测的是表面特征，不是内容

2026-08-14 修复（48 试点审计，docs/reports/AUDIT_48PILOT_PHASE4.md）：
  A. gated 题弱档口径：weak_mean 只认 weak+adv，剔除 trunc/cut（对齐 s10_pool
     自己写明的「截断和删论点对数学题没意义」——截断前 40% 仍含答案，
     天然高分，制造 LowSignal 假阳性）。
  B. 构造失效剔除（用判分数据做后验）：
     - s10_pool 标的 degraded（cut 删量不足）、answer_correct（adv/weak 把答案
       答对了）的档位直接剔除；
     - trunc/cut 与 strong 的正向 met 集完全相同时 → 删/截没有删掉任何
       得分点，该档造法失效，剔除（审计：15/24 的假 Hackable 来自此类）。
  C. gap 主判据改为 strong − weak 单档差：trunc/cut 是 strong 的子集，
     得分天然偏高，等权平均系统性稀释 gap（q0113 实际强弱差 37pp，
     被 trunc 拉成 21pp）。
  D. trunc/cut 平分降级为「待复核」（suspect_ties），不再直接判 defective：
     这类平分多为构造失效伪影，之前 24 个 Hackable 里 15 个是它。
     adv/weak 平分与准则级翻转仍是 defective 级信号。
  E. （2026-08-14 深夜）gated 题收紧：对抗档「过程全对、答案错」是设计使然，
     gated 的闸门才是判据 —— 过程级准则在 adv/weak 上的翻转降级为待复核
     （实测 q0301 对抗档正确推导+空集结论，过程准则翻转造成假 Hackable）；
     gated 弱档追平强档 = 疑似把答案答对（canon 缺失拦不住），弱档对 gap
     度量作废并降级待复核；无有效弱档时 LowSignal 抑制（测量受限非 rubric 缺陷）。
  F. （2026-08-14 深夜）veto 隔离：s12_judge 的 veto 会把最终 rate 打到 0，
     若直接喂给 gap / std / floor 三条判据，强档一旦 veto，strong_rate=0
     立即触发 FLOOR_RATE 地板判定、LowSignal 成片假阳性。区分度诊断一律
     改用 raw_rate（不含 veto 的补偿式得分率），veto 命中单独统计
     （consequential.vetoed），不参与任何区分度判据。
"""
import json, os, statistics, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage, answer_check

SRC = os.environ.get('RP_S11LC_SRC', 's12_judged.jsonl')
OUT = os.environ.get('RP_S11LC_OUT', 's11c_consequential.jsonl')

LOW_GAP = float(os.environ.get('RP_LOW_GAP', 0.25))
LOW_STD = float(os.environ.get('RP_LOW_STD', 0.12))
# 强档低于此视为「准则过严」，不再判 Hackable（否则 strong=0% 时恒真）
FLOOR_RATE = float(os.environ.get('RP_FLOOR_RATE', 0.3))
WEAK_TIERS = ('trunc', 'cut', 'weak')


def diagnose(r):
    j = dict(r.get('judged') or {})
    expected = {p.get('tier') for p in (r.get('pool') or []) if p.get('tier')}
    expected.update((r.get('pool_errors') or {}).keys())
    missing_tiers = sorted(expected - set(j))
    if missing_tiers:
        return {'low_signal': None, 'hackable': None,
                'skip_reason': '判分档缺失：' + ','.join(missing_tiers)}
    failed_tiers = sorted(t for t, value in j.items()
                          if value.get('judge_error'))
    if failed_tiers:
        return {'low_signal': None, 'hackable': None,
                'skip_reason': '判分失败：' + ','.join(failed_tiers)}
    incomplete_tiers = sorted(t for t, value in j.items()
                              if value.get('judge_incomplete'))
    if incomplete_tiers:
        return {'low_signal': None, 'hackable': None,
                'skip_reason': '判分不完整：' + ','.join(incomplete_tiers)}
    if 'strong' not in j:
        return {'low_signal': None, 'hackable': None,
                'skip_reason': '无强档，无法比较'}
    # strong 是所有比较的上界参照。它退化了（过短 / 明显不如锚）就没有可比性 ——
    # 弱档会轻易追平，Hackable 大面积误报。宁可跳过，不给假结论。
    if r.get('strong_degenerate'):
        return {'low_signal': None, 'hackable': None,
                'skip_reason': f'strong 档退化：{r.get("strong_degenerate_reason", "")}'}
    # strong_degenerate 只查篇幅，查不出「答得长但答错了」。388 全量实测：22 道
    # 地板题里 5 道是 strong 档答案本身错的（q0078/q0199/q0262/q0353/q0378），
    # 放松准则治不了 —— 参照系坏了就没有区分度可谈，跳过。
    _st = [p for p in (r.get('pool') or []) if p['tier'] == 'strong']
    _canon = r.get('answer_canonical')
    if _st and _canon:
        _app, _ok = answer_check.check_program(
            r.get('answer_kind'), _canon, _st[0].get('text') or '')
        if _app and not _ok:
            return {'low_signal': None, 'hackable': None,
                    'skip_reason': f'strong 档答案核验不通过（应为 {_canon[:20]}），'
                                   f'参照系失效'}

    # ---- 构造失效剔除（修复 B）----
    excluded = {}
    for p in (r.get('pool') or []):
        if p.get('degraded'):
            excluded[p['tier']] = '删量不足，造法失效'
        if p.get('answer_correct'):
            excluded[p['tier']] = '答案核验：把答案答对了，造法失效'
        if p.get('weak_not_weak'):
            excluded[p['tier']] = '弱档复核：不够弱，造法失效'
    # trunc/cut 与 strong 正向 met 集完全相同 → 删/截没删掉任何得分点
    def mset(t):
        v = j.get(t)
        if not v:
            return None
        return frozenset(x['_criterion_id'] for x in (v.get('items') or [])
                         if x.get('_criterion_id') and x.get('is_positive')
                         and x.get('met') and not x.get('judge_missing'))
    ss = mset('strong')
    for t in ('trunc', 'cut'):
        if t in j and t not in excluded and ss and mset(t) == ss:
            excluded[t] = '删/截后得分点全保留，造法失效'
    # mid 档序失效（388 全量实测）：SYS_MID 的「不做深入展开」被 pool_mid 模型
    # 读成「答简短」，中位 244 字 < weak 的 395 字（两档还是不同模型，篇幅无统一
    # 约束）→ 65% 的题 mid ≤ weak。档位序坏了，mid 的分不代表「中等质量」，
    # 只会抬高 std 掩盖 LowSignal、压低 min 掩盖 ceiling。剔除，不参与任何判据。
    if 'mid' in j and 'mid' not in excluded:
        mr = j['mid'].get('raw_rate', j['mid'].get('rate'))
        for lower in ('weak', 'adv'):
            v = j.get(lower)
            if v is None or lower in excluded:
                continue
            if mr is not None and mr <= v.get('raw_rate', v.get('rate')):
                excluded['mid'] = f'mid 档不高于 {lower} 档，档位序失效'
                break
    for t in excluded:
        j.pop(t, None)

    # 修复 F：区分度诊断一律用 raw_rate（不含 veto 的补偿式得分率）。
    # veto 命中的档 rate=0，是聚合规则不是 rubric 质量信号，
    # 混进 gap/std/floor 会制造成片假阳性。veto 单独统计。
    rate = {t: v.get('raw_rate', v.get('rate')) for t, v in j.items()
            if isinstance(v.get('raw_rate', v.get('rate')), (int, float))}
    if 'strong' not in rate:
        return {'low_signal': None, 'hackable': None,
                'skip_reason': 'strong 档没有有效得分率'}
    strong = rate['strong']
    veto_tiers = [t for t, v in j.items() if v.get('vetoed')]
    veto_by = {t: v.get('veto_by', []) for t, v in j.items() if v.get('vetoed')}
    is_gated = r.get('rubric_form') == 'gated_answer'
    # gated 题弱档追平强档 = 弱档疑似把答案答对了（canon 缺失时程序化核验拦不住）。
    # 这是 pool 造法问题，不是 rubric 缺陷 —— gap 度量随之失效，弱档作废。
    weak_invalid = is_gated and 'weak' in rate and rate['weak'] >= strong
    weak_cands = ('weak',) if is_gated else WEAK_TIERS
    # gated 弱档疑似答对时，它不能参与 gap/std/floor；Hackable 的 suspect
    # 记录仍保留在原始 rate 上，供 pool 侧复核。
    measure_rate = {t: value for t, value in rate.items()
                    if not (t == 'weak' and weak_invalid)}
    weaks = [measure_rate[t] for t in weak_cands if t in measure_rate]
    allr = list(measure_rate.values())

    # ---- Low Signal（修复 A/C：口径 + 单档差）----
    reasons = []
    if 'weak' in measure_rate:
        gap = strong - measure_rate['weak']
        gap_ref = 'weak 单档'
    elif weaks:
        gap = strong - (sum(weaks) / len(weaks))
        gap_ref = f'有效弱档均值(n={len(weaks)})'
    else:
        gap = None
        gap_ref = ''
    if weaks and gap is not None and gap < LOW_GAP:
        reasons.append(f'强档与{gap_ref}差 {gap:.1%} < {LOW_GAP:.0%}')
    std = statistics.pstdev(allr) if len(allr) > 1 else 0.0
    if len(allr) > 1 and std < LOW_STD:
        reasons.append(f'各档得分率标准差 {std:.3f} < {LOW_STD}')

    low = {'is_defective': bool(reasons), 'reasons': reasons,
           'strong_rate': round(strong, 4),
           'weak_mean': round(statistics.mean(weaks), 4) if weaks else None,
           'gap': round(gap, 4) if gap is not None else None,
           'gap_ref': gap_ref,
           'std': round(std, 4),
           'no_weak': not weaks}

    # 无有效弱档（gated 弱档答对被作废 / 全部造法失效）时，各档挤在一起是
    # 测量受限，不是 rubric 拉不开分 —— 抑制 defective，数据保留。
    if low['no_weak'] and low['is_defective']:
        low['is_defective'] = False
        low['suppressed_no_weak'] = True

    # ---- 标定问题：天花板 / 地板 ----
    cal, cal_why = None, ''
    if strong < FLOOR_RATE:
        cal, cal_why = 'floor', (f'强档仅 {strong:.1%}，准则过严 —— '
                                 f'好回答也拿不到分，先放松再谈区分度')
    elif allr and min(allr) >= 0.9:
        cal, cal_why = 'ceiling', '所有档位都 ≥90%，准则过松，无分辨力'
    calib = {'issue': cal, 'reason': cal_why}

    # 地板时 LowSignal 是冗余噪声：强档都拿不到分，各档挤在一起是必然结果，
    # 处置方向（放松准则）已由 floor 给出，再报「拉不开分」会误导下游
    # 往「收紧准则」方向改。数据保留，is_defective 抑制。
    if cal == 'floor' and low['is_defective']:
        low['is_defective'] = False
        low['suppressed_by_floor'] = True

    # ---- Hackable（题级）----
    hreasons = []
    suspect = []
    floor = strong < FLOOR_RATE
    if not floor:
        if 'adv' in rate and rate['adv'] >= strong:
            hreasons.append(f'对抗档 {rate["adv"]:.1%} ≥ 强档 {strong:.1%}')
        if 'weak' in rate and rate['weak'] >= strong:
            if is_gated:
                suspect.append(f'weak 档 {rate["weak"]:.1%} ≥ 强档 {strong:.1%}'
                               f'（gated：疑似把答案答对，pool 造法问题）')
            else:
                hreasons.append(f'weak 档 {rate["weak"]:.1%} ≥ 强档 {strong:.1%}')
        # trunc/cut 平分降级为待复核（修复 D）：多为构造失效伪影
        for t in ('trunc', 'cut'):
            if t in rate and rate[t] >= strong:
                suspect.append(f'{t} 档 {rate[t]:.1%} ≥ 强档 {strong:.1%}')

    # ---- Hackable（准则级）+ 弱档造法一致性 ----
    pos_cids = {c.get('_criterion_id') for c in (r.get('rubrics') or [])
                if c.get('is_positive')}
    met_of = {}
    for t, v in j.items():
        for x in (v.get('items') or []):
            if x.get('judge_missing') or not x.get('_criterion_id'):
                continue
            met_of[(x['_criterion_id'], t)] = bool(x.get('met'))

    surface, inconsistent = [], []
    for cid in sorted(pos_cids & {c for c, _ in met_of}):
        s_met = met_of.get((cid, 'strong'))
        if s_met is None:
            continue
        # 在弱/对抗档满足、强档反而不满足 → 测的是表面特征
        for t in list(WEAK_TIERS) + ['adv']:
            if met_of.get((cid, t)) and not s_met:
                surface.append({'_criterion_id': cid, 'tier': t})
                break
        # 三种造法结论不一致 → 测的是长度或结构而非内容。
        avail = [t for t in WEAK_TIERS if (cid, t) in met_of]
        if len(avail) == len(WEAK_TIERS):
            vals = {t: met_of[(cid, t)] for t in avail}
            if len(set(vals.values())) > 1:
                inconsistent.append({'_criterion_id': cid, **vals})

    if surface:
        # gated 题的对抗档过程本来就该全对（「答案错但过程完整」），过程级
        # 准则在 adv/weak 上翻转是设计使然，闸门才是判据 —— 降级待复核。
        # open 题无闸门，翻转仍是最强的准则级 Hackable 信号（q0167/q0336）。
        if is_gated:
            suspect.append(f'{len(surface)} 条正向准则在弱/对抗档满足但强档未满足'
                           f'（gated：过程级翻转，闸门才是判据）')
        elif not floor:
            hreasons.append(f'{len(surface)} 条正向准则在弱/对抗档满足但强档未满足')

    hack = {'is_defective': bool(hreasons), 'reasons': hreasons,
            'suspect_ties': suspect,
            'surface_criteria': surface,
            'inconsistent_across_weak': inconsistent,
            'suppressed_by_floor': floor}
    vetoed = {'n_tiers': len(veto_tiers), 'tiers': veto_tiers, 'by': veto_by}
    return {'low_signal': low, 'hackable': hack, 'calibration': calib,
            'vetoed': vetoed, 'skip_reason': '',
            'excluded_tiers': {t: why for t, why in sorted(excluded.items())}}


def main():
    recs = stage.read_jsonl(SRC)
    print(f'步骤 11c Consequential 诊断: {len(recs)} 题, 源={SRC}')
    print(f'  判据: Low Signal(gap<{LOW_GAP:.0%} 或 std<{LOW_STD}) / '
          f'Hackable(对抗档或 weak 档 ≥ 强档，或准则级翻转)')

    res, low_n, hack_n, skip_n, susp_n = [], 0, 0, 0, 0
    cal_stat = Counter()
    excl_stat = Counter()
    all_surface, all_incons = [], []
    for r in recs:
        d = diagnose(r)
        if d.get('skip_reason'):
            skip_n += 1
        if (d['low_signal'] or {}).get('is_defective'):
            low_n += 1
        if (d['hackable'] or {}).get('is_defective'):
            hack_n += 1
        susp_n += len((d['hackable'] or {}).get('suspect_ties', []))
        issue = (d.get('calibration') or {}).get('issue')
        if issue:
            cal_stat[issue] += 1
        for t, why in d.get('excluded_tiers', {}).items():
            excl_stat[f'{t}({why})'] += 1
        all_surface += [{**x, 'rid': r['rid']}
                        for x in (d['hackable'] or {}).get('surface_criteria', [])]
        all_incons += [{**x, 'rid': r['rid']}
                       for x in (d['hackable'] or {}).get('inconsistent_across_weak', [])]
        res.append({**r, 'consequential': d})
    stage.write_jsonl(OUT, res)

    n = len(recs)
    valid = n - skip_n
    print(f'\n=== 步骤 11c 结果 ===')
    if skip_n:
        print(f'  跳过        : {skip_n}/{n} 题（strong 档不可用，无比较基准）')
    print(f'  有效样本    : {valid} 题')
    print(f'  Low Signal  : {low_n}/{valid} 题 区分不开')
    print(f'  Hackable    : {hack_n}/{valid} 题 可被钻空子')
    if susp_n:
        print(f'  待复核      : {susp_n} 处 trunc/cut 平分（多为构造失效伪影，'
              f'不计入 Hackable）')
    if cal_stat:
        print(f'  标定问题    : ' + '  '.join(
            f'{"地板(准则过严)" if k == "floor" else "天花板(准则过松)"}={v}'
            for k, v in cal_stat.most_common()))
    if excl_stat:
        print(f'  剔除档位    : ' + '  '.join(f'{k}={v}' for k, v in excl_stat.most_common()))
    print(f'  表面特征准则: {len(all_surface)} 条（**正向**准则在弱/对抗档满足'
          f'但强档未满足）')
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
        cal = c.get('calibration') or {}
        marks = []
        if cal.get('issue') == 'floor':
            marks.append('地板(过严)')
        if cal.get('issue') == 'ceiling':
            marks.append('天花板(过松)')
        if lo['is_defective']:
            marks.append('LowSignal')
        if ha['is_defective']:
            marks.append('Hackable')
        if ha.get('suspect_ties'):
            marks.append(f'待复核×{len(ha["suspect_ties"])}')
        if (c.get('vetoed') or {}).get('n_tiers'):
            marks.append('VETO×{}({})'.format(c['vetoed']['n_tiers'],
                                              '/'.join(c['vetoed']['tiers'])))
        weak_mean = f'{lo["weak_mean"]:.1%}' if lo['weak_mean'] is not None else '—'
        gap = f'{lo["gap"]:.1%}' if lo['gap'] is not None else '—'
        print(f'    {r["rid"]}  {r.get("rubric_form",""):<13}'
              f'强={lo["strong_rate"]:6.1%} 弱均={weak_mean:>6} '
              f'差={gap:>6} std={lo["std"]:.3f}  '
              f'{" + ".join(marks) if marks else "✓ 通过"}')
        why_all = ([cal['reason']] if cal.get('reason') else []) \
            + lo['reasons'] + ha['reasons']
        for why in why_all[:3]:
            print(f'         ↳ {why}')
        if ha.get('suppressed_by_floor'):
            print(f'         ↳ (强档过低，Hackable 判定已抑制 —— 先解决过严问题)')


if __name__ == '__main__':
    main()
