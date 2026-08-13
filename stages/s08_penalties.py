"""步骤 8：惩罚项 —— 与正向准则互补的独立生成。

流程位置见 docs/design/rubric_pipeline_feishu_v2.md §8。Qworld §3.4 要求惩罚项覆盖
「事实性错误、逻辑矛盾、幻觉、格式违规」等即使满足了全部正向准则仍可能犯的错。

惩罚项与正向准则的区别：
- 正向准则：回答**应该有**什么（满足 = 加分，不满足 = 不加分）
- 惩罚项  ：回答**绝对不能有**什么（触发 = 扣分，不触发 = 不扣分）

惩罚项全题共享，不归属任何视角/场景。score 取负值（步骤 9 归一化会处理）。
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 6))
THINK = stage.envflag('RP_THINK', True)

SYS = '''你在为一道题生成**惩罚项**。

惩罚项是与正向准则互补的独立维度：即使回答满足了全部正向准则，
仍可能触发惩罚项（事实性错误、逻辑矛盾、幻觉、格式违规）。

【硬要求】
- 惩罚项必须**本题专属**：不要泛泛说「出现事实性错误」，而要说
  「将 Brep 的 Face 误认为几何曲面本身（实际是拓扑元素）」。
- 与正向准则互补：正向准则已经说「核心概念解释准确」，惩罚项就不能重复说
  「概念错误」。要找正向准则**不检查的角落**（如逻辑自洽性、格式合规）。
- 通常 2-4 条：事实性错误 1-2 条、逻辑矛盾 0-1 条、格式违规 0-1 条。

【positive = 触发条件，negative = 未触发】
- positive：回答出现什么现象时扣分（如「将 R 构型误判为 S 构型」）
- negative：未出现该现象（如「构型判断正确或未涉及构型」）

只输出 JSON：
{{"penalties": [{{"positive": "不超过60字", "negative": "不超过60字", "rationale": "不超过40字"}}]}}'''


def build(r):
    q = (r.get('query_eff') or r['question'])[:2000]
    subj = ' / '.join(r.get('subject') or []) or '未标注'
    base = '\n'.join(f'  - {c["positive"][:50]}' for c in r['criteria']
                     if c.get('criterion_type') == 'base')[:600]
    return [{'role': 'system', 'content': SYS},
            {'role': 'user', 'content':
                f'【学科】{subj}\n【提问意图】{r.get("intent", "")}\n\n'
                f'【题目】\n{q}\n\n'
                f'【已有的正向准则（前10条）】\n{base}'}]


def main():
    m = stage.pick('RP_M_GEN', 'generator')
    recs = stage.read_jsonl('s07_evolved.jsonl')
    print(f'步骤 8 惩罚项: {len(recs)} 条, 模型={m.name}, thinking={THINK}')

    def work(r):
        obj, _ = stage.json_call(m, build(r), stage='s08', thinking=THINK)
        pen = obj.get('penalties') or []
        if not isinstance(pen, list):
            pen = []
        out = []
        for i, p in enumerate(pen[:4]):
            if not isinstance(p, dict):
                continue
            out.append({'criterion_id': f'{r["rid"]}-p{i + 1}',
                        'positive': str(p.get('positive', ''))[:200],
                        'negative': str(p.get('negative', ''))[:200],
                        'rationale': str(p.get('rationale', ''))[:120],
                        'score': -3,                        # 惩罚项负分，归一化会处理
                        'perspective_id': '', 'scenario_id': '', 'block_id': '',
                        'criterion_type': 'penalty'})
        return {**r, 'criteria': r['criteria'] + out, 'penalty_n': len(out)}

    res, _ = stage.run(work, recs, workers=WORKERS, desc='s08')
    stage.write_jsonl('s08_penalties.jsonl', res)

    np = sum(r['penalty_n'] for r in res)
    print(f'\n=== 步骤 8 结果 ===')
    print(f'  惩罚项总数    : {np}')
    print(f'  惩罚项/题     : {np / len(res):.1f}')
    ex = next((r for r in res if r['penalty_n'] >= 2), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]}:')
        for c in [c for c in ex['criteria'] if c.get('criterion_type') == 'penalty']:
            print(f'    {c["criterion_id"]} (分={c["score"]})')
            print(f'      触发: {c["positive"][:54]}')


if __name__ == '__main__':
    main()
