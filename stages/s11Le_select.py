"""步骤 11Le：多轮处置的终态选择 —— 每题挑实测最好的那个 rubric 版本。

s11Ld 的 LLM 重写会**摆动**（48 试点已见，388 全量更明显）：
  q0221  紧(60%,Hackable) → 松(0%,地板) → 紧(60%) → 松(0%)  —— 2-循环
  q0028  27% → 27% → 45%(Hackable) → 0%(地板)
「收紧」与「放松」是互逆操作，对这些题不存在两头都满足的中间档，再多跑几轮
只是在两个坏状态之间来回。所以处置到此为止，改为**在已有实测证据里选最优**：

  每题取各轮 rubric 版本中诊断状态最好的一版（缺陷数少者优；同分取靠后轮次，
  因为靠后轮次的准则表述经过更多次打磨）。没进过处置的题保持原样。

这不是把问题掩盖过去 —— 选出来的版本是**实测过**的，附带它当轮的诊断结论，
残留缺陷照实记在 `_s11Le` 里，交给下游（pool 重造 / 人工复核）。

输入: 各轮 s11Lc 诊断（RP_S11LE_ROUNDS，逗号分隔，先基线后各轮）
输出: s11Le_final.jsonl
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

ROUNDS = [x.strip() for x in os.environ.get(
    'RP_S11LE_ROUNDS',
    's11Lc_cons388.jsonl,s11Lc_r1.jsonl,'
    's11Lc_r2.jsonl,s11Lc_r3.jsonl').split(',') if x.strip()]
OUT = os.environ.get('RP_S11LE_OUT', 's11Le_final.jsonl')


def flags(c):
    """诊断状态 → 缺陷标签集合。skip 单列（无法判定 ≠ 没缺陷）。"""
    if not c or c.get('skip_reason'):
        return {'skip'}
    f = set()
    if (c.get('hackable') or {}).get('is_defective'):
        f.add('H')
    if (c.get('low_signal') or {}).get('is_defective'):
        f.add('L')
    if (c.get('calibration') or {}).get('issue') == 'floor':
        f.add('F')
    elif (c.get('calibration') or {}).get('issue') == 'ceiling':
        f.add('C')
    return f


def cost(f):
    """越小越好。skip 记 1.5：比干净差、比确诊缺陷好（它是测量受限，不是缺陷）。"""
    if f == {'skip'}:
        return 1.5
    return len(f)


def main():
    rounds = []
    for name in ROUNDS:
        try:
            recs = stage.read_jsonl(name)
        except FileNotFoundError:
            print(f'  跳过不存在的轮次: {name}')
            continue
        rounds.append((name, {r['rid']: r for r in recs}))
    if not rounds:
        raise SystemExit('没有可用的诊断轮次')
    print(f'步骤 11Le 终态选择: {len(rounds)} 轮')
    for name, d in rounds:
        print(f'  {name:28} {len(d)} 题')

    base_name, base = rounds[0]
    res, stat, moved = [], Counter(), Counter()
    for rid, br in base.items():
        best_i, best_r = 0, br
        best_c = cost(flags(br.get('consequential')))
        for i, (name, d) in enumerate(rounds[1:], 1):
            r = d.get(rid)
            if r is None:
                continue
            c = cost(flags(r.get('consequential')))
            if c <= best_c:          # 同分取靠后轮次
                best_i, best_r, best_c = i, r, c
        f = flags(best_r.get('consequential'))
        stat['|'.join(sorted(f)) or 'OK'] += 1
        moved[ROUNDS[best_i]] += 1
        rec = dict(best_r)
        # s11Ld 的处置记录改挂 `_` 前缀：导出层按「`_` 前缀 = 内部字段」的规则
        # 决定进不进内部档，不带前缀就被静默丢掉（内部档里查不到某题为什么被改）
        if 's11Ld' in rec:
            rec['_s11Ld'] = rec.pop('s11Ld')
        rec['_s11Le'] = {'chosen_round': ROUNDS[best_i],
                         'residual': sorted(f - {'skip'}),
                         'skipped': f == {'skip'},
                         'rounds_seen': sum(1 for _, d in rounds if rid in d)}
        res.append(rec)
    stage.write_jsonl(OUT, res)

    print(f'\n=== 步骤 11Le 结果 ===')
    print(f'  终态 {len(res)} 题，按残留缺陷:')
    for k, v in stat.most_common():
        print(f'    {k:12} {v}')
    print(f'  选中轮次分布:')
    for k, v in moved.most_common():
        print(f'    {k:28} {v}')
    n_ok = stat.get('OK', 0)
    print(f'  无缺陷 {n_ok}/{len(res)} = {n_ok / len(res):.1%}'
          f'（含 skip {stat.get("skip", 0)} 题测量受限）')


if __name__ == '__main__':
    main()
