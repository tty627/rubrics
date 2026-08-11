"""Phase 1 抽样：seed.jsonl → seed_phase1.jsonl（20 条）。

抽样规则来自 docs/PLAN.md Phase 1：open/closed 按 7:3，且必须含
1 条超短 query、1 条多子题、1 条非中文——这三类是检查点要盯的边界情形，
随机抽 20 条大概率一条都抽不到。学科上做轮转，避免全落在理学+工学。

固定随机种子，重跑结果一致（否则前序抽样一变，后面所有缓存全废）。
"""
import json, os, re, sys, random
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

N = int(os.environ.get('RP_N', 20))
OPEN_RATIO = 0.7
SEED_RANDOM = 0


def is_short(q):
    return len(q) < 15


def is_multipart(q):
    """多子题：显式编号、①②、或三个以上问号。只做召回，误报交给步骤 1 的 LLM 裁决。"""
    return (bool(re.search(r'[（(]?[1１][)）.、].{2,}?[（(]?[2２][)）.、]', q))
            or bool(re.search(r'①.*②', q))
            or q.count('？') + q.count('?') >= 3)


def is_nonzh(q):
    z = sum(1 for c in q if '一' <= c <= '鿿')
    return z / max(len(q), 1) < 0.3


TRAITS = [('超短', is_short), ('多子题', is_multipart), ('非中文', is_nonzh)]


def qtype(r):
    """用草稿 rubric 自带的 open/closed 分层。注意这与步骤 2.5 要判的
    verifiable/open/hybrid 是两套标签，这里只借它做抽样分层，不作为判定输入。"""
    d = r.get('draft_rubric') or {}
    return d.get('question_type') or 'open'


def main():
    recs = stage.read_jsonl('seed.jsonl')
    rng = random.Random(SEED_RANDOM)
    by_rid = {r['rid']: r for r in recs}
    picked, why = [], {}

    def take(r, tag):
        if r['rid'] in why:
            why[r['rid']] += '+' + tag
            return False
        picked.append(r)
        why[r['rid']] = tag
        return True

    # 1) 三类边界情形各保底一条，优先挑同时命中多个特征的（一条顶两条）
    for tag, fn in TRAITS:
        pool = [r for r in recs if fn(r['question']) and r['rid'] not in why]
        if not pool:
            print(f'  警告: 没有「{tag}」样本')
            continue
        pool.sort(key=lambda r: (-sum(1 for _, f in TRAITS if f(r['question'])), r['rid']))
        take(pool[0], tag)

    # 2) 按 open/closed 配额补齐，学科轮转以免全落在理学+工学
    need = {'open': round(N * OPEN_RATIO), 'closed': N - round(N * OPEN_RATIO)}
    for r in picked:
        need[qtype(r)] = max(0, need.get(qtype(r), 0) - 1)

    for t, k in need.items():
        buckets = defaultdict(list)
        for r in recs:
            if r['rid'] not in why and qtype(r) == t:
                buckets[(r['subject'] or ['未标'])[0]].append(r)
        for b in buckets.values():
            rng.shuffle(b)
        order = sorted(buckets, key=lambda s: (-len(buckets[s]), s))
        added = 0
        while added < k and any(buckets[s] for s in order):
            for s in order:                      # 轮转：每个学科先各取一条
                if added >= k:
                    break
                if buckets[s] and take(buckets[s].pop(), t):
                    added += 1
        if added < k:
            print(f'  警告: {t} 只凑到 {added}/{k} 条')

    picked.sort(key=lambda r: r['rid'])
    for r in picked:
        r['sample_reason'] = why[r['rid']]
    stage.write_jsonl('seed_phase1.jsonl', picked)

    print(f'\n=== Phase 1 子集 ({len(picked)} 条) ===')
    print('  open/closed :', dict(Counter(qtype(r) for r in picked)))
    print('  学科分布    :', dict(Counter((r['subject'] or ['未标'])[0] for r in picked)))
    print('  边界情形    :', {t: sum(1 for r in picked if f(r['question']))
                              for t, f in TRAITS})
    print('  双回复/单回复:', dict(Counter(len(r['ref_responses']) for r in picked)))
    print()
    for r in picked:
        print(f'  {r["rid"]}  {qtype(r):<7}{why[r["rid"]]:<12}{r["question"][:42]!r}')


if __name__ == '__main__':
    main()
