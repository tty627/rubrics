"""步骤 4：准则实例化 —— 把每个视角展开成 1-3 条二元准则（Binary Descriptor）。

流程位置见 docs/design/rubric_pipeline_feishu_v2.md §4。视角（perspective）是抽象的评价着眼点
（如「反应机理的立体选择性判断是否正确」），准则（criterion）是可操作的二元判断
（「回答是否明确指出了主产物的R/S构型」←→「未指出或指错构型」）。

二元准则的 positive/negative 遵循 Qworld §3.2 要求：
  positive   : 该准则「满足」时，回答应该有什么（不是抽象评价）
  negative   : 未满足时的现象，与 positive 互斥
不是「好 ←→ 坏」，是「满足 ←→ 不满足」的对称现象描述。

权重（score）暂时从 weight_hint 映射：major → 10, normal → 5, minor → 3；
步骤 9 归一化会重算。此处给默认值只是为了让步骤 7 的难度演化能跑。

血缘标签（perspective_id, scenario_id）一路传下来：
第 13 步要按视角聚合、第 14 步要回灌到第 3 步，全靠这些 id 追溯。
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 20))
THINK = stage.envflag('RP_THINK', True)
# 优先从 s03c (带dimension) 读取，否则回退到 s03b
SRC = os.environ.get('RP_S03_OUT',
                     's03c_dimensioned.jsonl' if os.path.exists(stage._path('s03c_dimensioned.jsonl'))
                     else 's03b_merged_hybrid.jsonl')

SYS = '''你在把一个评价视角展开成 1-3 条二元准则。

【什么是二元准则】
一条准则由 positive 和 negative 两个描述组成，它们是同一件事的两面：
- positive：回答**满足**该准则时的**可观察现象**（不是抽象评价）
- negative：**不满足**时的对称现象，与 positive 互斥

例子：
  视角：「反应机理的立体选择性判断是否正确」
  准则1 positive：「回答明确指出了主产物的R/S构型及其成因」
       negative：「未指出构型，或指错构型，或未说明成因」

【硬要求】
- positive/negative 必须**本题专属**，不能是「准确」「完整」「清晰」这类通用词。
  必须落到这道题的具体内容上。
- 一个视角通常拆 1-2 条准则；只有视角覆盖多个独立点时才拆 3 条。
- 准则之间正交，不要有包含或依赖关系。
- positive 和 negative 必须互斥：一份回答不可能同时满足两者。

只输出 JSON：
{{"criteria": [{{"positive": "不超过60字", "negative": "不超过60字", "rationale": "为什么这条重要，不超过40字"}}]}}
每个视角展开 1-3 条准则。'''

WEIGHT_MAP = {'major': 10, 'normal': 5, 'minor': 3}


def build(r, p):
    q = (r.get('query_eff') or r['question'])[:2000]
    subj = ' / '.join(r.get('subject') or []) or '未标注'
    return [{'role': 'system', 'content': SYS},
            {'role': 'user', 'content':
                f'【学科】{subj}\n【提问意图】{r.get("intent", "")}\n\n'
                f'【题目】\n{q}\n\n'
                f'【待展开的视角】{p["name"]}：{p["desc"]}'}]


def main():
    m = stage.pick('RP_M_GEN', 'generator')
    recs = stage.read_jsonl(SRC)
    print(f'步骤 4 准则实例化: {len(recs)} 条, 源={SRC}, 模型={m.name}, thinking={THINK}')

    total_p = sum(len(r['perspectives']) for r in recs)
    print(f'  视角总数: {total_p}')

    # 按 (题, 视角) 摊平后再并发：若按题并发、题内视角串行，则墙钟由「视角最多的
    # 那道题」决定——实测有题展开 47 个视角，全量跑会拖成几小时。摊平后并发度
    # 由 WORKERS 决定而与题的视角数无关。
    jobs = [(r, p) for r in recs for p in r['perspectives']]
    print(f'  摊平后任务数: {len(jobs)}')

    def one(job):
        r, p = job
        obj, _ = stage.json_call(m, build(r, p), stage='s04', thinking=THINK)
        cri = obj.get('criteria') or []
        if not isinstance(cri, list):
            cri = []
        out = []
        for c in cri[:3]:
            if not isinstance(c, dict):
                continue
            out.append({'perspective_id': p.get('perspective_id', ''),
                        'scenario_id': p.get('scenario_id', ''),
                        'block_id': p.get('block_id', ''),
                        'dimension': p.get('dimension', ''),  # 新增：传递维度字段
                        'positive': str(c.get('positive', ''))[:200],
                        'negative': str(c.get('negative', ''))[:200],
                        'rationale': str(c.get('rationale', ''))[:120],
                        'score': WEIGHT_MAP.get(p.get('weight_hint'), 5)})
        return r['rid'], out

    done, _ = stage.run(one, jobs, workers=WORKERS, desc='s04')
    by_rid = {}
    for rid, cs in done:
        by_rid.setdefault(rid, []).extend(cs)

    res = []
    for r in recs:
        cs = by_rid.get(r['rid'], [])
        for i, c in enumerate(cs):          # criterion_id 在汇总后统一编号，保证连续
            c['criterion_id'] = f'{r["rid"]}-c{i + 1}'
        res.append({**r, 'criteria': cs,
                    'criteria_raw_perspectives': len(r['perspectives'])})
    stage.write_jsonl('s04_criteria.jsonl', res)

    nc = sum(len(r['criteria']) for r in res)
    ratio = [len(r['criteria']) / max(r['criteria_raw_perspectives'], 1) for r in res]
    byform = {}
    for r in res:
        byform.setdefault(r['rubric_form'], []).append(len(r['criteria']))
    print(f'\n=== 步骤 4 结果 ===')
    print(f'  准则总数      : {nc}')
    print(f'  准则/视角     : min={min(ratio):.1f} p50={sorted(ratio)[len(ratio) // 2]:.1f} '
          f'max={max(ratio):.1f} mean={sum(ratio) / len(ratio):.1f}')
    print(f'  按 form       : ' + '  '.join(
        f'{k}={sum(v) / len(v):.1f}/题' for k, v in sorted(byform.items())))
    print(f'  分数分布      : {dict(Counter(c["score"] for r in res for c in r["criteria"]))}')
    ex = next((r for r in res if r['rubric_form'] == 'analytic' and len(r['criteria']) >= 5), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]}  {len(ex["criteria"])} 条准则：')
        for c in ex['criteria'][:4]:
            print(f'    {c["criterion_id"]} (分={c["score"]})')
            print(f'      ✓ {c["positive"][:56]}')
            print(f'      ✗ {c["negative"][:56]}')


if __name__ == '__main__':
    main()
