"""步骤 3b：视角归并 —— 把 R_w 展开出的视角收敛到目标条数。

不是新增环节，是 RET 树的剪枝。放在步骤 3 末尾而非交给步骤 11，理由：
R_w 在 20 条试跑上净增 58% 的视角，但 analytic 题平均 26 个/题（最多 34），
而 docs §2.5 给 analytic 的目标形态是 5-8 条准则。让 34 个视角流进步骤 4
生成三十几条准则、再指望步骤 11 的 Redundant 诊断剪掉大半，既贵又不可靠。

**必须用语义归并，不能用字符去重**：实测 q0062 有 7 个视角都在讲公差
（「公差定义的准确性」「公差层级一致性」「公差动态更新与传播」…），
两两字符 Jaccard 全部 <0.6；全题 3141 个视角对里只有 5 对超过 0.6。
字面去重对这种主题级重复完全无效，所以这一步走 LLM。

归并保留血缘：合并后的视角记录它吞掉了哪些 perspective_id，
第 13 步按视角聚合、第 14 步回灌到第 3 步时才能追到源头。
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 20))
THINK = stage.envflag('RP_THINK', True)
STRATEGY = os.environ.get('RP_RET', 'hybrid')
SRC = os.environ.get('RP_S03_OUT', f's03_perspective_{STRATEGY}.jsonl')
OUT = f's03b_merged_{STRATEGY}.jsonl'
# 目标条数：analytic 对齐 docs §2.5 的 5-8 条准则；multi_part 按 block 摊薄
TARGET = {'analytic': (6, 10), 'multi_part': (8, 14), 'gated_answer': (3, 3)}

SYS = '''你在给一道题的评价视角清单做归并去重。

输入是一份视角清单，其中不少视角**评的是同一个底层要求**，只是措辞不同。
你的任务是把它们合并，收敛到 {lo}-{hi} 个。

【怎么判断该合并】
两个视角若满足以下任一条，就是同一个底层要求，必须合并：
- 一份回答只要满足了其中一个，几乎必然也满足另一个
- 它们指向回答里的同一段内容
- 它们是同一概念的不同侧面，而这道题并不需要分开考察这些侧面

例：「公差定义的准确性」「公差层级一致性」「公差动态更新与传播」「公差与几何逼近的关系」
——对一道问「Brep 结构是什么样的」的题，这四条合并成一条「公差机制的说明」即可。
公差只是 Brep 结构的一个组成部分，不该占四条。

【怎么判断不该合并】
- 一份回答可能满足其中一个而明显不满足另一个 → 保留为两条
- 它们分属题目要求的不同任务（如「算出答案」与「解释原理」）→ 保留

【合并后怎么写】
- name 取能涵盖被合并各条的说法，不要简单拼接，不超过 14 字
- desc 说明这一条要看回答里的什么，不超过 40 字
- 仍须**本题专属**。合并不是升级成「准确性」「完整性」这类通用词——
  那等于把 R_w 的产出全丢了。宁可保留稍具体的说法。

【重要性排序】按对本题的重要程度从高到低排列。若必须舍弃，舍弃最边缘的，
但不要因为凑数而删掉本题的关键考察点。

只输出 JSON：
{{"merged": [{{"name": "...", "desc": "...", "from": [被合并的视角编号，整数数组]}}]}}
from 里填输入清单中的编号。每个输入视角只能出现在一个 from 里，不能遗漏也不能重复。'''


def build(r, lo, hi):
    ps = r['perspectives']
    lst = '\n'.join(f'{i}. {p["name"]}：{p["desc"]}' for i, p in enumerate(ps))
    q = (r.get('query_eff') or r['question'])[:1500]
    return [{'role': 'system', 'content': SYS.format(lo=lo, hi=hi)},
            {'role': 'user', 'content':
                f'【题目】\n{q}\n\n【提问意图】{r.get("intent", "")}\n\n'
                f'【待归并的视角清单（共 {len(ps)} 条）】\n{lst}'}]


def main():
    m = stage.pick('RP_M_GEN', 'generator')
    recs = stage.read_jsonl(SRC)
    print(f'步骤 3b 视角归并: {len(recs)} 条, 源={SRC}, 模型={m.name}, thinking={THINK}')

    def work(r):
        ps = r['perspectives']
        lo, hi = TARGET.get(r['rubric_form'], (6, 10))
        if len(ps) <= hi:                          # 本来就没超，不花这次调用
            return {**r, 'perspectives_raw_n': len(ps), 'merge_calls': 0}
        obj, _ = stage.json_call(m, build(r, lo, hi), stage='s03b', thinking=THINK)
        out, used = [], set()
        for g in obj.get('merged') or []:
            if not isinstance(g, dict):
                continue
            src = [i for i in (g.get('from') or []) if isinstance(i, int) and 0 <= i < len(ps)]
            if not src:
                continue
            used.update(src)
            parents = [ps[i] for i in src]
            out.append({'perspective_id': f'{r["rid"]}-m{len(out) + 1}',
                        'name': str(g.get('name', ''))[:40],
                        'desc': str(g.get('desc', ''))[:120],
                        # 血缘：取第一个来源的场景归属，并记全部被吞的视角
                        'scenario_id': parents[0].get('scenario_id', ''),
                        'block_id': parents[0].get('block_id', ''),
                        'origin': '+'.join(sorted({p['origin'] for p in parents})),
                        'weight_hint': parents[0].get('weight_hint', 'normal'),
                        'merged_from': [p['perspective_id'] for p in parents]})
        # 模型漏掉的视角原样保留，宁可超一点也不能静默丢内容
        dropped = [i for i in range(len(ps)) if i not in used]
        for i in dropped:
            p = dict(ps[i])
            p['perspective_id'] = f'{r["rid"]}-m{len(out) + 1}'
            p['merged_from'] = [ps[i]['perspective_id']]
            out.append(p)
        return {**r, 'perspectives': out, 'perspectives_raw_n': len(ps),
                'merge_dropped': len(dropped), 'merge_calls': 1}

    res, _ = stage.run(work, recs, workers=WORKERS, desc='s03b')
    stage.write_jsonl(OUT, res)

    raw = sum(r['perspectives_raw_n'] for r in res)
    now = sum(len(r['perspectives']) for r in res)
    byform = {}
    for r in res:
        byform.setdefault(r['rubric_form'], []).append(len(r['perspectives']))
    print(f'\n=== 步骤 3b 结果（源策略={STRATEGY}）===')
    print(f'  视角数        : {raw} → {now}  (压缩到 {now / max(raw, 1):.0%})')
    print(f'  按 form       : ' + '  '.join(
        f'{k}={sum(v) / len(v):.1f}/题' for k, v in sorted(byform.items())))
    print(f'  LLM 调用      : {sum(r["merge_calls"] for r in res)} 次')
    print(f'  模型漏掉后补回: {sum(r.get("merge_dropped", 0) for r in res)} 条')
    over = [(r['rid'], len(r['perspectives'])) for r in res
            if len(r['perspectives']) > TARGET.get(r['rubric_form'], (6, 10))[1]]
    print(f'  仍超目标上限  : {over or "无"}')
    ex = next((r for r in res if r['rubric_form'] == 'analytic' and r['merge_calls']), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]}  {ex["perspectives_raw_n"]} → {len(ex["perspectives"])}：')
        for p in ex['perspectives']:
            print(f'    {p["name"]}: {p["desc"][:44]}')
            print(f'        ← 吞掉 {len(p["merged_from"])} 条 <{p["origin"]}>')


if __name__ == '__main__':
    main()
