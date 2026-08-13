"""步骤 5b：锚点集 —— 用异质闭源模型 + 多数票，为 s05 的 drift 判定建立参照。

流程位置见 docs/design/rubric_pipeline_feishu_v2.md「前置：锚点集（人在环）」。
设计文档要求一个**独立于生成模型的质量参照点**，否则第 11 步的指标没有分母。
s05 用的是 glm-ac —— 它同时也是 s04 准则的生成者，自己检查自己有自偏好。
这一步用 family=openai 的闭源模型复检，与 glm/deepseek 异质。

**为什么要多数票（LLMaJ-MV）**：实测同一条准则连判 4 次得到
clean/clean/drift/clean，temperature=0 仍有服务端波动。RIFT 论文对 Reliability
三项也用 N=5 多数票，正是为压住这种方差。单次跑的结论不可用。

**抽样设计**：drift / clean 各半（而非按 30.8% 真实占比），
因为要同时测 glm-ac 的精确率与召回率——两类都需要足够样本量。
每类内部再按 rubric_form 比例分层。算总体一致率时按真实占比回加权。

产出 data/s05b_anchor.jsonl：每条含 5 票明细、多数票结论、glm-ac 原判定。
校准报告见 scripts/anchor_report.py。
"""
import json, os, random, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

N_PER_CLASS = int(os.environ.get('RP_ANCHOR_N', 250))   # 每个 verdict 类的样本数
N_VOTES = int(os.environ.get('RP_ANCHOR_VOTES', 5))     # 多数票轮数
WORKERS = int(os.environ.get('RP_WORKERS', 8))          # 端点偶发 502，别压太满
SEED = 20260812                                          # 固定种子，抽样可复现

# 判定用的 system prompt 与 s05 保持一致 —— 两者判的必须是同一件事，
# 否则测出的分歧是 prompt 差异而非模型差异。
_s05_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 's05_grounding.py')
_ns = {'__name__': '_s05_import', '__file__': _s05_path}
exec(compile(open(_s05_path, encoding='utf-8').read(), _s05_path, 'exec'), _ns)
SYS = _ns['SYS']
build_msgs = _ns['build']


def build_sample(recs):
    """drift/clean 各 N_PER_CLASS 条，每类内按 rubric_form 比例分层。"""
    buckets = defaultdict(list)
    for r in recs:
        form = r.get('rubric_form', '?')
        for c in r.get('criteria', []):
            v = (c.get('grounding') or {}).get('verdict')
            if v in ('clean', 'drift'):
                buckets[(form, v)].append((r, c))

    rng = random.Random(SEED)
    picked = []
    for verdict in ('drift', 'clean'):
        forms = sorted({f for f, v in buckets if v == verdict})
        total = sum(len(buckets[(f, verdict)]) for f in forms)
        for i, f in enumerate(forms):
            pool = buckets[(f, verdict)]
            # 末个分层用减法补齐，避免逐层取整后总数偏离
            if i == len(forms) - 1:
                quota = N_PER_CLASS - sum(
                    1 for r, c in picked
                    if (c.get('grounding') or {}).get('verdict') == verdict)
            else:
                quota = round(N_PER_CLASS * len(pool) / total)
            quota = max(0, min(quota, len(pool)))
            picked += rng.sample(pool, quota)
    return picked


def main():
    m = stage.pick('RP_M_ANCHOR', 'grounder')
    recs = stage.read_jsonl('s05_grounded.jsonl')
    sample = build_sample(recs)

    print(f'步骤 5b 锚点集: 抽样 {len(sample)} 条 × {N_VOTES} 票 '
          f'= {len(sample) * N_VOTES} 次调用')
    print(f'  判定模型: {m.name} ({m.model_id}, family={m.family})')
    dist = Counter(((c.get("grounding") or {}).get("verdict"), r.get('rubric_form'))
                   for r, c in sample)
    print('  分层分布:')
    for (v, f), n in sorted(dist.items()):
        print(f'    glm={v:<6} {f:<14} {n}')

    # 摊平到 (样本, 票号)。同一逻辑请求靠不同 stage 名分开缓存：
    # 缓存键不含 stage，但缓存路径含，所以 5 票各存一份、可断点续跑。
    jobs = [(r, c, i) for r, c in sample for i in range(N_VOTES)]

    def one(job):
        r, c, i = job
        obj, _ = stage.json_call(m, build_msgs(r, c),
                                 stage=f's05b_v{i}', thinking=False)
        v = obj.get('verdict', '')
        if v not in ('clean', 'drift'):
            v = 'clean'          # 判定异常按不删处理，宁漏不错
        return c['criterion_id'], i, v, (obj.get('reason') or '')[:150]

    done, errs = stage.run(one, jobs, workers=WORKERS, desc='s05b')

    votes = defaultdict(dict)
    reasons = defaultdict(list)
    for cid, i, v, rsn in done:
        votes[cid][i] = v
        if v == 'drift' and rsn:
            reasons[cid].append(rsn)

    out = []
    for r, c in sample:
        cid = c['criterion_id']
        vs = votes.get(cid, {})
        if not vs:
            continue
        vl = list(vs.values())
        n_drift = vl.count('drift')
        majority = 'drift' if n_drift * 2 > len(vl) else 'clean'
        out.append({
            'rid': r['rid'],
            'criterion_id': cid,
            'rubric_form': r.get('rubric_form'),
            'question': (r.get('query_eff') or r['question'])[:300],
            'positive': c['positive'],
            'negative': c.get('negative', ''),
            'glm_verdict': (c.get('grounding') or {}).get('verdict'),
            'glm_reason': (c.get('grounding') or {}).get('reason', ''),
            'anchor_votes': vl,
            'anchor_n_drift': n_drift,
            'anchor_n_votes': len(vl),
            'anchor_verdict': majority,
            'anchor_unanimous': len(set(vl)) == 1,
            'anchor_reasons': reasons.get(cid, [])[:3],
        })

    stage.write_jsonl('s05b_anchor.jsonl', out)

    unan = sum(1 for o in out if o['anchor_unanimous'])
    print(f'\n=== 步骤 5b 结果 ===')
    print(f'  完成样本      : {len(out)} / {len(sample)}')
    if errs:
        print(f'  失败调用      : {len(errs)}')
    print(f'  五票一致      : {unan} ({unan / max(len(out),1) * 100:.1f}%)')
    print(f'  存在分歧      : {len(out) - unan} '
          f'({(len(out) - unan) / max(len(out),1) * 100:.1f}%)')
    print(f'\n  → 校准报告: python3 scripts/anchor_report.py')


if __name__ == '__main__':
    main()
