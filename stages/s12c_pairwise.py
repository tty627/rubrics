"""步骤 12c：Phase 4 检查点 2 —— 新 rubric vs 草稿 rubric 的 pairwise 一致率。

流程位置：判分侧证据齐了之后的放行闸门（PLAN.md §Phase 4 检查点 2）：
「用新 rubric 和草稿 rubric 分别给 gpt55 vs 弱档打分，比 pairwise 一致率。
新 rubric 不超过草稿就不上线」。

数据来源：
  - 新 rubric 判分：_s11Le.chosen_round 指向的轮次文件（s11c_cons388 / r1..r3），
    即**实际交付的那一版** rubric 在实测里的判分（judged[strong|weak].rate，
    含 veto 两票制后的最终得分率）
  - 草稿 rubric 判分：s12b_draft388.jsonl（s12b_draft_judge.py 产出）

样本口径：strong vs weak 对（检查点原文「gpt55 vs 弱档」）。剔除三类测量受限：
  - _s11Le.skipped=32（strong 档答错/答偏题等，参照系坏了不给结论）
  - 任一侧缺 strong/weak 判分
  - 任一侧 judge_incomplete（判分器漏返回，得分率被低估）

判据（放行闸门，gate 集合 = 剔除三类后的可测对）：
  1. 判别率（strong > weak 占比）_新 ≥ _草稿
  2. 反转率（weak > strong 占比）_新 ≤ _草稿
补充指标：一致率（strong ≥ weak）、平均分差、两 rubric 排序一致率、raw_rate
口径的敏感性（veto 归零不是唯一驱动）。逐题结果落盘，供 badcase 审计。

输入: s11e_all452.jsonl + s11c_cons388/r1/r2/r3.jsonl + s12b_draft388.jsonl
输出: s12c_pairwise.jsonl + 控制台放行结论
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

OUT = os.environ.get('RP_S12LC_OUT', 's12c_pairwise.jsonl')
ROUND_FILES = ('s11c_cons388.jsonl', 's11c_r1.jsonl', 's11c_r2.jsonl',
               's11c_r3.jsonl')


def state_of(s, w):
    return 'win' if s > w else ('rev' if s < w else 'tie')


def main():
    all452 = {r['rid']: r for r in stage.read_jsonl('s11e_all452.jsonl')}
    rounds = {}
    for fn in ROUND_FILES:
        try:
            rounds[fn] = {r['rid']: r for r in stage.read_jsonl(fn)}
        except FileNotFoundError:
            rounds[fn] = {}
    draft = {r['rid']: r for r in stage.read_jsonl('s12b_draft388.jsonl')}

    p4 = {rid: r for rid, r in all452.items()
          if (r.get('_s11Le') or {}).get('chosen_round') not in
          (None, '未参与 Phase 4（单回复题）')}
    print(f'Phase 4 实测题: {len(p4)}')

    rows = []
    for rid, r in sorted(p4.items()):
        le = r.get('_s11Le') or {}
        chosen = le.get('chosen_round', '')
        src = rounds.get(chosen, {}).get(rid)
        d = draft.get(rid)
        row = {'rid': rid, 'chosen_round': chosen, 'excluded': False,
               'exclude_reason': ''}
        n = (src or {}).get('judged') or {}
        ns, nw = n.get('strong'), n.get('weak')
        dj = (d or {}).get('draft_judged') or {}
        ds, dw = dj.get('strong'), dj.get('weak')

        if le.get('skipped'):
            row.update(excluded=True, exclude_reason='skip_测量受限')
        elif not ns or not nw or not ds or not dw:
            row.update(excluded=True, exclude_reason='缺判分')
        else:
            row.update(
                n_strong=ns['rate'], n_weak=nw['rate'],
                n_strong_raw=ns['raw_rate'], n_weak_raw=nw['raw_rate'],
                d_strong=ds['rate'], d_weak=dw['rate'],
                d_strong_raw=ds['raw_rate'], d_weak_raw=dw['raw_rate'],
                n_gap=round(ns['rate'] - nw['rate'], 4),
                d_gap=round(ds['rate'] - dw['rate'], 4),
                n_state=state_of(ns['rate'], nw['rate']),
                d_state=state_of(ds['rate'], dw['rate']),
            )
            if ns.get('judge_incomplete') or nw.get('judge_incomplete') \
                    or ds.get('judge_incomplete') or dw.get('judge_incomplete'):
                row.update(excluded=True, exclude_reason='judge_incomplete')
        rows.append(row)

    inc = [r for r in rows if not r['excluded']]
    n = len(inc)

    def agg(states, gaps):
        c = Counter(states)
        return {'win': c['win'], 'tie': c['tie'], 'rev': c['rev'],
                'gap': sum(gaps)}

    a_new = agg([r['n_state'] for r in inc], [r['n_gap'] for r in inc])
    a_drf = agg([r['d_state'] for r in inc], [r['d_gap'] for r in inc])

    def fmt(a, name):
        print(f'  {name:<12} n={n} 判别={a["win"]/n:6.1%} 平局={a["tie"]/n:6.1%} '
              f'反转={a["rev"]/n:6.1%} 平均分差={a["gap"]/n:6.1%}')

    print('\n=== 检查点 2：strong vs weak pairwise ===')
    fmt(a_new, '新 rubric')
    fmt(a_drf, '草稿 rubric')

    agree = sum(1 for r in inc if r['n_state'] == r['d_state'])
    print(f'  两 rubric 排序一致率: {agree/n:6.1%} ({n} 对)')

    ex = Counter(r['exclude_reason'] for r in rows if r['excluded'])
    print(f'  剔除: {dict(ex)}')

    pass1 = a_new['win'] / n >= a_drf['win'] / n
    pass2 = a_new['rev'] / n <= a_drf['rev'] / n
    print(f'\n  判据 1 判别率: 新 {a_new["win"]/n:6.1%} vs 草稿 {a_drf["win"]/n:6.1%} '
          f'→ {"✓" if pass1 else "✗"}')
    print(f'  判据 2 反转率: 新 {a_new["rev"]/n:6.1%} vs 草稿 {a_drf["rev"]/n:6.1%} '
          f'→ {"✓" if pass2 else "✗"}')
    verdict = '✅ 检查点 2 通过：新 rubric 可上线' if (pass1 and pass2) \
        else '❌ 检查点 2 未通过：新 rubric 不超过草稿，不上线'
    print(f'\n  {verdict}')

    worse = [r for r in inc if
             (r['d_state'] == 'win' and r['n_state'] != 'win') or
             (r['n_state'] == 'rev' and r['d_state'] != 'rev')]
    print(f'\n  新 rubric 相对草稿退步的题: {len(worse)}')
    for r in worse[:20]:
        print(f"    {r['rid']}  新 {r['n_state']} (强{r['n_strong']:.0%} vs "
              f"弱{r['n_weak']:.0%})  草稿 {r['d_state']} "
              f"(强{r['d_strong']:.0%} vs 弱{r['d_weak']:.0%})")

    for r in inc:
        r['n_state_raw'] = state_of(r['n_strong_raw'], r['n_weak_raw'])
    sraw = Counter(r['n_state_raw'] for r in inc)
    print(f'\n  raw_rate 口径（无 veto）: 判别 {sraw["win"]} 平局 {sraw["tie"]} '
          f'反转 {sraw["rev"]} / {n}')

    stage.write_jsonl(OUT, rows)
    print(f'  逐题明细 → {OUT}')


if __name__ == '__main__':
    main()
