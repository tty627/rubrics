"""步骤 2：上下文标签抽取 + R⁰_h（根节点 → ℓ=1 层 Scenarios）。

流程位置见 docs/rubric_pipeline_feishu_v2.md §2。

两个上下文标签（intent、隐性约束）不是产出物，是**下一步展开的输入**：
Scenarios 必须从 intent + 隐性约束推出，而不是从 query 字面直接联想，
否则展开会退化成对题面关键词的同义改写。

scenario_id 由代码按 `{rid}-s{序号}` 生成，不交给模型。第 4 步的血缘标签、
第 13 步按视角聚合、第 14 步回灌到第 3 步全靠它，模型自造的 id 不稳定，
换一次 prompt 就全对不上了。
"""
import os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 8))
THINK = stage.envflag('RP_THINK', True)           # 展开需要推理，默认开
N_MIN, N_MAX = 2, 4

SYS = f'''你在为一道题构建评估标准的第一层结构。分两件事做。

【第一件：抽上下文标签】
- intent：提问者的真实目的。不是复述题面，而是判断他实际要解决什么。
  例：「Brep的结构是什么样的」→ intent 是「理解边界表示法的数据组织方式，
  多半为了写几何处理代码或读懂 CAD 内核」，而不是「想知道 Brep 结构」。
- 隐性约束：题面没写但答案必须满足的条件，三项：
  - audience：答案该按什么水平的人来写（判断依据是提问用词的专业程度）
  - format：期望的组织形式（步骤/对比/推导/清单/代码），题面没暗示就填「不限」
  - risk_boundary：答错或答偏的代价。医疗、法律、金融、安全类要写明；
    纯知识题填「无特殊风险」

【第二件：拆 ℓ=1 层 Scenarios】
把这道题拆成 {N_MIN}-{N_MAX} 个「评价场景」——即一份好回答需要分别站得住脚的几个面向。

场景**必须从 intent 和隐性约束推出**，不能是题面关键词的同义改写。
反例：题问「合同负债和应付账款的区别」，拆出「合同负债场景」「应付账款场景」——
这是把题面切碎，不是场景。
正例：拆出「会计科目定义的准确性」「二者在报表中的归属判断」
「后续结转方向的业务场景还原」——这才是回答要分别站住的几个面向。

场景之间要正交，不要嵌套或包含。场景是**评价的面向**，不是答案的章节。

只输出 JSON：
{{"intent": "一句话，不超过60字",
  "implicit_constraints": {{"audience": "...", "format": "...", "risk_boundary": "..."}},
  "scenarios": [{{"name": "不超过12字", "desc": "这个场景下什么样的回答算站得住，不超过40字"}}]}}'''


def build(r):
    q = r.get('query_eff') or r['question']
    subj = ' / '.join(r.get('subject') or []) or '未标注'
    return [{'role': 'system', 'content': SYS},
            {'role': 'user', 'content': f'【学科】{subj}\n\n【题目】\n{q}'}]


def main():
    m = stage.pick('RP_M_GEN', 'generator')
    recs = [r for r in stage.read_jsonl('s01_filter.jsonl') if r['verdict'] != '弃用']
    print(f'步骤 2 上下文+R⁰_h: {len(recs)} 条（已排除弃用）, 模型={m.name}, thinking={THINK}')

    def work(r):
        obj, meta = stage.json_call(m, build(r), stage='s02', thinking=THINK)
        scs = obj.get('scenarios') or []
        if not isinstance(scs, list) or not scs:
            raise ValueError(f'{r["rid"]} 没拆出 scenarios')
        out = []
        for i, s in enumerate(scs[:N_MAX]):
            if isinstance(s, str):                # 容忍模型退化成字符串数组
                s = {'name': s, 'desc': ''}
            out.append({'scenario_id': f'{r["rid"]}-s{i + 1}',   # id 由代码定，不信模型
                        'name': str(s.get('name', ''))[:40],
                        'desc': str(s.get('desc', ''))[:120]})
        ic = obj.get('implicit_constraints') or {}
        return {**r, 'intent': obj.get('intent', ''),
                'implicit_constraints': {k: str(ic.get(k, ''))[:80]
                                         for k in ('audience', 'format', 'risk_boundary')},
                'scenarios': out, '_meta': meta}

    out, _ = stage.run(work, recs, workers=WORKERS, desc='s02')
    stage.stat_cached([r.pop('_meta') for r in out])
    stage.write_jsonl('s02_context.jsonl', out)

    ns = [len(r['scenarios']) for r in out]
    names = Counter(s['name'] for r in out for s in r['scenarios'])
    print('\n=== 步骤 2 结果 ===')
    print(f'  场景数/题     : {dict(sorted(Counter(ns).items()))}  (均值 {sum(ns) / max(len(ns), 1):.1f})')
    print(f'  场景名去重    : {len(names)} / {sum(ns)}  '
          f'(去重率低说明展开在套模板，是 R_h 失效的早期信号)')
    print(f'  最常见场景名  : {names.most_common(5)}')
    print(f'  风险边界非空  : {sum(1 for r in out if "无特殊风险" not in r["implicit_constraints"]["risk_boundary"])}')
    print('\n  抽样两条：')
    for r in out[:2]:
        print(f'    {r["rid"]} intent: {r["intent"][:56]}')
        for s in r['scenarios']:
            print(f'      {s["scenario_id"]}  {s["name"]}: {s["desc"][:44]}')


if __name__ == '__main__':
    main()
