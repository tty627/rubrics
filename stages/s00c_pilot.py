"""试点抽样：从已跑通的全量结果里抽 N 条，用于在小样本上打通完整 14 步。

与 s00b_sample.py 的区别：
  s00b 从 seed.jsonl 抽（跑流程之前），按草稿的 open/closed 分层；
  本脚本从 s02_5_route.jsonl 抽（题型路由之后），按 **rubric_form 三态**分层。

为什么按 rubric_form 分：验证链（步骤 10-13）对三种形态的处理完全不同 ——
  gated_answer 要验程序化核验 + 对抗档（答案错但过程完整）能不能钻空子；
  analytic     要验 Low Signal（多维度能否把质量梯度拉开）；
  multi_part   要验分块判分。
按 open/closed 抽会导致某一形态一条都没有，验证链的对应分支就没跑到。

硬性要求（缺一不可，否则验证链有分支跑不到）：
  - 双回复优先：单回复题做不了「锚定回复≠待评回复」（硬约束第 1 条）
  - RP_PILOT_MUST 里指定的 rid 必须入选
  - 至少各 1 条：非中文 / 超短 / 已知含事实错误（验证链要能抓到它）

固定种子，重跑一致。
"""
import json, os, re, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

N = int(os.environ.get('RP_N', 10))
SRC = os.environ.get('RP_PILOT_SRC', 's02_5_route.jsonl')
OUT = os.environ.get('RP_PILOT_OUT', 'seed_pilot.jsonl')
# 必须入选的 rid，逗号分隔
MUST = [x.strip() for x in os.environ.get('RP_PILOT_MUST', 'q0303').split(',') if x.strip()]
# 形态配额：gated 验程序化核验、analytic 验区分度、multi_part 验分块
QUOTA = {'gated_answer': 4, 'analytic': 4, 'multi_part': 2}
# 已诊断出事实错误的题（可选，用于确保验证链能抓到已知问题）
DIAG = os.environ.get('RP_PILOT_DIAG', 's11L_diagnosed.jsonl')


def is_short(q):
    return len(q) < 15


def is_nonzh(q):
    z = sum(1 for c in q if '一' <= c <= '鿿')
    return z / max(len(q), 1) < 0.3


def n_resp(r):
    return len(r.get('ref_responses') or {})


def main():
    recs = stage.read_jsonl(SRC)
    by_rid = {r['rid']: r for r in recs}

    # 带已知事实错误的题，优先纳入 —— 验证链要能把它们抓出来
    factual = set()
    try:
        for r in stage.read_jsonl(DIAG):
            for d in r.get('diagnoses') or []:
                if 'factual' in (d.get('failure_modes') or []):
                    factual.add(r['rid'])
                    break
    except FileNotFoundError:
        print(f'  （未找到 {DIAG}，跳过「含事实错误」这一保底类）')

    picked, why = [], {}

    def take(r, tag):
        if r is None or r['rid'] in why:
            if r is not None:
                why[r['rid']] += '+' + tag
            return False
        picked.append(r)
        why[r['rid']] = tag
        return True

    # 1) 指定必选
    for rid in MUST:
        if rid in by_rid:
            take(by_rid[rid], 'must')
        else:
            print(f'  ⚠️  指定的 {rid} 不在 {SRC} 里')

    # 2) 三类边界情形各保底一条。双回复优先，其次命中特征多的
    def pick_one(pool, tag):
        pool = [r for r in pool if r['rid'] not in why]
        if not pool:
            print(f'  ⚠️  没有「{tag}」样本')
            return
        pool.sort(key=lambda r: (-n_resp(r), r['rid']))
        take(pool[0], tag)

    q_of = lambda r: (r.get('query_eff') or r['question'])
    pick_one([r for r in recs if is_nonzh(q_of(r))], '非中文')
    pick_one([r for r in recs if is_short(q_of(r))], '超短')
    pick_one([r for r in recs if r['rid'] in factual], '含事实错误')

    # 3) 按 rubric_form 配额补齐。双回复优先、学科轮转避免全落在理学+工学
    need = dict(QUOTA)
    for r in picked:
        f = r.get('rubric_form')
        if f in need:
            need[f] = max(0, need[f] - 1)

    for form, k in need.items():
        if k <= 0:
            continue
        buckets = defaultdict(list)
        for r in recs:
            if r['rid'] in why or r.get('rubric_form') != form:
                continue
            buckets[(r.get('subject') or ['未标'])[0]].append(r)
        # 桶内：双回复优先，再按 rid 稳定排序（不用 random，保证可重跑）
        for b in buckets.values():
            b.sort(key=lambda r: (-n_resp(r), r['rid']))
        order = sorted(buckets, key=lambda s: (-len(buckets[s]), s))
        added = 0
        while added < k and any(buckets[s] for s in order):
            for s in order:                      # 轮转：每学科先各取一条
                if added >= k:
                    break
                if buckets[s] and take(buckets[s].pop(0), form):
                    added += 1
        if added < k:
            print(f'  ⚠️  {form} 只凑到 {added}/{k} 条')

    # 4) 超额时砍掉非必选、非保底的
    protected = set(MUST) | {r['rid'] for r in picked
                             if why[r['rid']] in ('非中文', '超短', '含事实错误')}
    if len(picked) > N:
        keep = [r for r in picked if r['rid'] in protected]
        rest = [r for r in picked if r['rid'] not in protected]
        picked = (keep + rest)[:N]

    picked.sort(key=lambda r: r['rid'])
    for r in picked:
        r['pilot_reason'] = why[r['rid']]
    stage.write_jsonl(OUT, picked)

    print(f'\n=== 试点子集 ({len(picked)} 条) ===')
    print('  rubric_form :', dict(Counter(r.get('rubric_form') for r in picked)))
    print('  question_type:', dict(Counter(r.get('question_type') for r in picked)))
    print('  学科        :', dict(Counter((r.get('subject') or ['未标'])[0] for r in picked)))
    print('  双回复/单回复:', dict(Counter(n_resp(r) for r in picked)))
    n2 = sum(1 for r in picked if n_resp(r) >= 2)
    print(f'  可做锚定分离: {n2}/{len(picked)} 题（单回复题步骤 5/10 会退化为共用一条）')
    print()
    for r in picked:
        print(f'  {r["rid"]}  {r.get("rubric_form",""):<13}{why[r["rid"]]:<12}'
              f'resp={n_resp(r)}  {q_of(r)[:44]!r}')


if __name__ == '__main__':
    main()
