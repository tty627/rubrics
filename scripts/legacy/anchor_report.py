#!/usr/bin/env python3
"""锚点集校准报告 —— 用 s05b 的多数票结论评估 s05(glm-ac) 的判定质量。

抽样是 drift/clean 各半，不是真实占比（真实 drift 30.8%）。
所以「原始一致率」只反映抽样内部，跨类比较无意义；
「加权一致率」按真实占比回加权，才是全量 13,788 条上的预期一致率。

把锚点的多数票当作参照（非绝对真值），据此算 glm-ac 的：
  精确率 = glm 判 drift 中，锚点也判 drift 的比例（判错杀了多少）
  召回率 = 锚点判 drift 中，glm 也判 drift 的比例（漏掉了多少）
"""
import json
import os
import sys
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRUE_DRIFT_RATE = 0.308      # s05 全量实测：3514/11421


def main():
    path = os.path.join(REPO, 'data', 's05b_anchor.jsonl')
    if not os.path.exists(path):
        sys.exit('缺少 data/s05b_anchor.jsonl，先跑 stages/s05b_anchor.py')

    rows = [json.loads(l) for l in open(path, encoding='utf-8')]
    n = len(rows)

    print('=' * 62)
    print(f'锚点集校准报告   样本 {n} 条')
    print('=' * 62)

    # 投票可靠性：分歧越多，说明这批判定本身越不确定
    unan = sum(1 for r in rows if r['anchor_unanimous'])
    split = Counter(r['anchor_n_drift'] for r in rows)
    print(f'\n【1】锚点投票可靠性（{rows[0]["anchor_n_votes"]} 票制）')
    print(f'  全票一致    : {unan} ({unan / n * 100:.1f}%)')
    print(f'  存在分歧    : {n - unan} ({(n - unan) / n * 100:.1f}%)')
    print(f'  drift 票数分布: ', end='')
    print('  '.join(f'{k}票={v}' for k, v in sorted(split.items())))

    # 混淆矩阵
    cm = Counter((r['glm_verdict'], r['anchor_verdict']) for r in rows)
    tp = cm[('drift', 'drift')]      # 都判 drift
    fp = cm[('drift', 'clean')]      # glm 误杀
    fn = cm[('clean', 'drift')]      # glm 漏检
    tn = cm[('clean', 'clean')]

    print(f'\n【2】混淆矩阵（行=glm-ac，列=锚点多数票）')
    print(f'  {"":<10}{"anchor:drift":>14}{"anchor:clean":>14}')
    print(f'  {"glm:drift":<10}{tp:>14}{fp:>14}')
    print(f'  {"glm:clean":<10}{fn:>14}{tn:>14}')

    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
    print(f'\n【3】glm-ac 的 drift 判定质量')
    print(f'  精确率 P    : {prec * 100:.1f}%   (判 drift 中真的脱靶)')
    print(f'  召回率 R    : {rec * 100:.1f}%   (真脱靶中被抓到)')
    print(f'  F1          : {f1 * 100:.1f}%')
    print(f'  误杀 FP     : {fp} 条（glm 判 drift，锚点认为 clean）')
    print(f'  漏检 FN     : {fn} 条（glm 判 clean，锚点认为 drift）')

    # 一致率：抽样内 vs 按真实占比加权
    raw = (tp + tn) / n
    n_d, n_c = tp + fn, fp + tn
    acc_d = tp / n_d if n_d else 0      # drift 类内一致率
    acc_c = tn / n_c if n_c else 0      # clean 类内一致率
    weighted = TRUE_DRIFT_RATE * acc_d + (1 - TRUE_DRIFT_RATE) * acc_c

    print(f'\n【4】一致率')
    print(f'  抽样内原始  : {raw * 100:.1f}%  '
          f'(样本是 drift/clean 各半，非真实分布)')
    print(f'  按真实占比加权: {weighted * 100:.1f}%  '
          f'(drift {TRUE_DRIFT_RATE * 100:.1f}% 加权，代表全量预期)')

    # Cohen's kappa —— 扣除随机一致后的真实一致程度
    pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
    kappa = (raw - pe) / (1 - pe) if pe < 1 else 0
    level = ('很差' if kappa < 0.2 else '一般' if kappa < 0.4 else
             '中等' if kappa < 0.6 else '良好' if kappa < 0.8 else '优秀')
    print(f"  Cohen's kappa: {kappa:.3f}  ({level})")

    # 分层看，定位问题集中在哪种题型
    print(f'\n【5】按 rubric_form 分层')
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        b = by[r['rubric_form']]
        b[1] += 1
        if r['glm_verdict'] == r['anchor_verdict']:
            b[0] += 1
    print(f'  {"form":<16}{"一致":>8}{"合计":>8}{"一致率":>10}')
    for f, (a, t) in sorted(by.items()):
        print(f'  {f:<16}{a:>8}{t:>8}{a / t * 100:>9.1f}%')

    # 结论
    print(f'\n【6】结论')
    if kappa >= 0.6:
        print(f'  ✅ kappa {kappa:.2f} 达良好，glm-ac 的全量判定可直接沿用')
    elif kappa >= 0.4:
        print(f'  ⚠️  kappa {kappa:.2f} 中等。建议：只处置 glm 与锚点都判 drift')
        print(f'     的交集（{tp} 条），对 FP/FN 高发的类型改 prompt 重跑')
    else:
        print(f'  ❌ kappa {kappa:.2f} 偏低，glm-ac 判定不可直接用于处置')
        print(f'     建议改 s05 prompt 后重跑，或全量改用锚点模型')

    if fn > fp * 1.5:
        print(f'  → 漏检({fn})明显多于误杀({fp})：glm-ac 判定偏松，'
              f'真脱靶的准则漏了不少')
    elif fp > fn * 1.5:
        print(f'  → 误杀({fp})明显多于漏检({fn})：glm-ac 判定偏严，'
              f'会砍掉本该保留的准则')
    else:
        print(f'  → 误杀({fp})与漏检({fn})基本对称，无系统性偏向')

    # 漏检样本最有价值：这些是 glm 看不见的问题类型
    fns = [r for r in rows
           if r['glm_verdict'] == 'clean' and r['anchor_verdict'] == 'drift']
    if fns:
        print(f'\n【7】glm-ac 漏检样例（锚点判 drift 但 glm 判 clean）')
        for r in fns[:6]:
            print(f'\n  [{r["criterion_id"]}] {r["anchor_n_drift"]}/'
                  f'{r["anchor_n_votes"]} 票判 drift')
            print(f'    准则: {r["positive"][:66]}')
            if r['anchor_reasons']:
                print(f'    锚点理由: {r["anchor_reasons"][0][:88]}')

    print()


if __name__ == '__main__':
    main()
