"""步骤 7：难度演化 —— analytic 题在 R_base（优秀回答标准）上补增量准则 R_dist。

流程位置见 docs/rubric_pipeline_feishu_v2.md §7。这一步只对 analytic 形态生效，
其他两种形态（gated_answer / multi_part）无需难度演化，直通。

**为什么要难度演化**：Qworld §3.3 区分了 R_base（什么算优秀）和 R_dist（区分梯度）。
前者准则密、主要靠模型判，后者准则疏、可以程序化检测（字数、公式数）。不分开的代价
是判分时会把「回答长度1200字」这种准则和「核心概念解释准确」混在一起算权重，模型会
困惑该给多少分。

R_base 已经在步骤 4 产出了，这一步补 R_dist。

**R_dist 的约束**：
- 只生成可程序化检测的准则（字数、格式、附带物，不涉及语义正确性）
- 权重远低于 R_base（单条 score=1，后续归一化会压到总分的 5-10%）
- 不与 R_base 重复
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 6))
THINK = stage.envflag('RP_THINK', True)

SYS = '''你在为一道题补充**难度区分准则**（R_dist）。

该题已有一份**优秀回答标准**（R_base），那些准则定义了什么算优秀。
你的任务是补充**区分难度梯度**的准则，用于把 80 分的回答和 95 分的回答分开。

【R_dist 的三条硬要求】
1. **可程序化检测**：字数、段落数、公式/代码数量、图表、引用文献数、
   结构化标记（列表/表格）、示例数量。绝对不能涉及语义正确性或逻辑质量
   ——那些已经在 R_base 里了。
2. **不与 R_base 重复**：R_base 已经说「核心概念解释准确」，R_dist 就不能再说
   「解释完整性」，那仍然是语义。只能说「回答字数 ≥800」「包含示例 ≥2 个」。
3. **本题专属**：不要泛泛说「回答字数充足」，而要根据本题复杂度给出具体阈值
   （如「≥1200 字」）。格式要求也要符合本题特点（如代码题就检查代码块数量）。

【通常补 2-4 条】对于简单概念解释题，可能只补 1-2 条（字数+结构）；
对于复杂分析题，可能补 3-4 条（字数+示例+公式+段落结构）。

只输出 JSON：
{{"dist_criteria": [{{"positive": "不超过60字", "negative": "不超过60字", "rationale": "不超过40字"}}]}}'''


def build(r):
    q = (r.get('query_eff') or r['question'])[:2000]
    subj = ' / '.join(r.get('subject') or []) or '未标注'
    base = '\n'.join(f'  - {c["positive"][:50]}' for c in r['criteria'][:12])
    return [{'role': 'system', 'content': SYS},
            {'role': 'user', 'content':
                f'【学科】{subj}\n【提问意图】{r.get("intent", "")}\n\n'
                f'【题目】\n{q}\n\n'
                f'【已有的 R_base 准则（优秀回答标准，前12条）】\n{base}'}]


def main():
    m = stage.pick('RP_M_GEN', 'generator')
    recs = stage.read_jsonl('s04_criteria.jsonl')
    analytic = [r for r in recs if r['rubric_form'] == 'analytic']
    others = [r for r in recs if r['rubric_form'] != 'analytic']
    print(f'步骤 7 难度演化: {len(recs)} 条, analytic={len(analytic)} 其他={len(others)}, '
          f'模型={m.name}, thinking={THINK}')

    def work(r):
        obj, _ = stage.json_call(m, build(r), stage='s07', thinking=THINK)
        dist = obj.get('dist_criteria') or []
        if not isinstance(dist, list):
            dist = []
        out = []
        for i, d in enumerate(dist[:4]):
            if not isinstance(d, dict):
                continue
            out.append({'criterion_id': f'{r["rid"]}-d{i + 1}',
                        'positive': str(d.get('positive', ''))[:200],
                        'negative': str(d.get('negative', ''))[:200],
                        'rationale': str(d.get('rationale', ''))[:120],
                        'score': 1,                         # R_dist 权重远低于 R_base
                        'perspective_id': '',                # R_dist 不归属任何视角
                        'scenario_id': '', 'block_id': '',
                        'criterion_type': 'dist'})
        # R_base 的准则标上 type=base
        for c in r['criteria']:
            c['criterion_type'] = 'base'
        return {**r, 'criteria': r['criteria'] + out, 'dist_n': len(out)}

    res_a, _ = stage.run(work, analytic, workers=WORKERS, desc='s07/analytic')
    # 其他形态直通，只标 type=base
    for r in others:
        for c in r['criteria']:
            c['criterion_type'] = 'base'
        r['dist_n'] = 0
    res = sorted(res_a + others, key=lambda x: x['rid'])
    stage.write_jsonl('s07_evolved.jsonl', res)

    nd = sum(r['dist_n'] for r in res_a)
    nb = sum(len([c for c in r['criteria'] if c.get('criterion_type') == 'base']) for r in res_a)
    print(f'\n=== 步骤 7 结果 ===')
    print(f'  analytic 题   : {len(res_a)} 条')
    print(f'  R_base 准则   : {nb} 条（已有）')
    print(f'  R_dist 准则   : {nd} 条（新增）')
    print(f'  R_dist/题     : {nd / len(res_a):.1f}')
    ex = next((r for r in res_a if r['dist_n'] >= 2), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]}  R_dist:')
        for c in [c for c in ex['criteria'] if c.get('criterion_type') == 'dist']:
            print(f'    {c["criterion_id"]} (分={c["score"]})')
            print(f'      ✓ {c["positive"][:56]}')


if __name__ == '__main__':
    main()
