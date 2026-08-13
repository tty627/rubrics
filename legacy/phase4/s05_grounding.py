"""步骤 5：Response Grounding —— 用强模型参考回答锚定准则，防止 rubric drift。

流程位置见 docs/design/rubric_pipeline_feishu_v2.md §5 行 119-130。

**什么是 rubric drift**：准则逐渐变笼统、幻觉、脱离实际输出。
- 例：准则要求「回答必须包含量子纠缠的数学形式」，但这道题只是入门科普，
  强模型的回答也不会写数学公式，这条准则就是 drift 了。

**锚定方法**：
1. 用强模型生成一个参考回答（或使用已有的高质量回答）
2. 检查每条准则在这个参考回答上是否合理
3. Meta-principles 约束：准则必须对应回复中可观察到的行为

**硬约束**：
- **锚定用的回复必须与待评回复不同源**
- 种子集阶段：用 glm52/gpt55 作锚（它们不是被评对象）✅
- 真实流阶段：不能拿日志回复当锚（它就是待评对象）❌

Phase 3 实现：轻量级 drift 检查，标记有问题的准则。
Phase 4 要求：用异质强模型生成参考回答，完整锚定。

注意：Phase 3 的数据未经过 s05 处理（因为是后补的步骤），
但由于种子集的锚定回复来自 glm52/gpt55（不是待评对象），暂时可接受。
Phase 4 必须补上完整的 s05。
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 20))
THINK = stage.envflag('RP_THINK', True)

# Meta-principles（来自 RubricHub）
META_PRINCIPLES = """
【Meta-Principles】准则设计的上层原则：
1. 准则必须对应回复中**可观察到的行为**（能指着回复的某句话说"这里满足了"）
2. 准则不得要求回答超出题目范围的额外内容
3. 准则的严格程度必须与题目复杂度匹配（入门题不能要求专家级细节）
4. 准则必须是判分器能客观判定的（不能依赖判分器自己去推理或查证）
"""

SYS = f'''你在检查一份 rubric 的准则是否发生了 **drift（脱靶漂移）**。

{META_PRINCIPLES}

【最重要的一条：参考回答未满足 ≠ drift】
参考回答只是**一份**回答，不是满分答案。它没写到的内容，可能正是它的不足
——rubric 的价值就在于能指出这种不足。

所以：
- 准则具体、在题目范围内，但参考回答没覆盖 → **clean**（这是有效的区分点）
- 只有当准则本身站不住时，才判 drift

若把「参考回答没满足」判成 drift，rubric 就塌缩成「参考回答里有什么」，
覆盖率虚高、漏检必然发生。这是要极力避免的。

【什么才算 drift】只有以下三种：
1. **超出题目范畴**：准则检查题目根本没问的东西
   例：题目问「合同负债和应付账款是什么」，准则却要求解释「预付款项」
2. **笼统空泛**：准则用「准确」「完整」「清晰」这类放之四海皆准的词，
   没落到本题的具体内容上，判分器无从核对
   例：「回答对该概念的解释是否科学准确」
3. **幻觉**：准则要求的内容与题目所属领域的事实不符，或指向不存在的东西

【明确不算 drift 的情形】
- 准则要求的细节比参考回答更深 → clean（参考回答不够好而已）
- 准则要求专业术语或机理，只要题目属于该专业领域 → clean
- 准则要求界定概念、区分易混项，只要与题目主题相关 → clean

【你的任务】
给定题目、一条准则、一份参考回答，只判断这条准则**本身**是否站得住。
参考回答仅供你理解题目的实际语境，**不是判定准则的标准**。

判定规则：
- 准则具体、在题目范畴内、可客观核对 → verdict = "clean"
- 命中上述三种 drift 之一 → verdict = "drift"

只输出 JSON：
{{"verdict": "clean|drift", "reason": "若判 drift，指明命中哪一种（超范畴/笼统/幻觉）并一句话说明，不超过50字；若 clean，留空"}}
'''


def build(r, c):
    q = (r.get('query_eff') or r['question'])[:1500]
    # 使用已有的参考回复（来自 glm52 或 gpt55）
    # 注意：seed中的字段名是 ref_responses，不是 reference_response
    ref_resp = r.get('ref_responses', r.get('reference_response', {}))
    # 优先用 gpt55，其次 glm52
    ref_text = (ref_resp.get('response_gpt55') or
                ref_resp.get('response_glm52') or
                '(无参考回复)')[:3000]

    return [{'role': 'system', 'content': SYS},
            {'role': 'user', 'content':
                f'【题目】\n{q}\n\n'
                f'【一份参考回答】（仅供理解题目语境，**不是**判定准则的标准；\n'
                f'它没覆盖的内容可能正是它的不足）\n{ref_text}\n\n'
                f'【待检查的准则】\n'
                f'positive: {c["positive"]}\n'
                f'negative: {c["negative"]}\n'
                f'rationale: {c.get("rationale", "")}\n\n'
                f'请只判断这条准则本身是否站得住。'}]


def main():
    # 使用 glm-ac 作为判定模型（通过环境变量可指定其他模型）
    m = stage.pick('RP_M_GEN', 'generator')

    recs = stage.read_jsonl('s04_criteria.jsonl')
    print(f'步骤 5 Response Grounding: {len(recs)} 条, 模型={m.name}, thinking={THINK}')

    # 检查是否有参考回复
    has_ref = sum(1 for r in recs if r.get('ref_responses') or r.get('reference_response'))
    print(f'  有参考回复: {has_ref}/{len(recs)} 条')

    if has_ref < len(recs) * 0.5:
        print(f'\n⚠️  警告：超过一半的记录没有参考回复')
        print(f'     Phase 3 种子集应该有 glm52/gpt55 回复')
        print(f'     如果是真实流数据，需要先生成参考回复')

    nc_total = sum(len(r['criteria']) for r in recs)
    print(f'  准则总数: {nc_total}')

    # 摊平到 (题, 准则)
    jobs = [(r, c) for r in recs for c in r['criteria']]

    def one(job):
        r, c = job
        obj, _ = stage.json_call(m, build(r, c), stage='s05', thinking=THINK)
        verd = obj.get('verdict', 'clean')
        if verd not in ('clean', 'drift'):
            verd = 'clean'  # 默认通过
        reason = obj.get('reason', '') if verd == 'drift' else ''
        return r['rid'], c['criterion_id'], verd, reason

    done, _ = stage.run(one, jobs, workers=WORKERS, desc='s05')

    # 按 rid 和 criterion_id 建索引
    drift_map = {}
    for rid, cid, verd, reason in done:
        drift_map.setdefault(rid, {})[cid] = {'verdict': verd, 'reason': reason}

    # 更新记录
    out = []
    drift_count = 0
    for r in recs:
        for c in r['criteria']:
            drift_info = drift_map.get(r['rid'], {}).get(c['criterion_id'], {})
            c['grounding'] = drift_info
            if drift_info.get('verdict') == 'drift':
                drift_count += 1
        out.append(r)

    stage.write_jsonl('s05_grounded.jsonl', out)

    print(f'\n=== 步骤 5 结果 ===')
    print(f'  检查准则数    : {nc_total}')
    print(f'  判定 drift    : {drift_count} ({drift_count/nc_total*100:.1f}%)')
    print(f'  判定 clean    : {nc_total - drift_count} ({(nc_total-drift_count)/nc_total*100:.1f}%)')

    if drift_count > 0:
        print(f'\n  ⚠️  发现 {drift_count} 条 drift 准则')
        print(f'     建议：在步骤 6 聚合时过滤掉这些准则')

    # 抽样
    ex_rec = next((r for r in out if any(c.get('grounding', {}).get('verdict') == 'drift'
                                         for c in r['criteria'])), None)
    if ex_rec:
        drift_criteria = [c for c in ex_rec['criteria']
                         if c.get('grounding', {}).get('verdict') == 'drift']
        print(f'\n  抽样 {ex_rec["rid"]}  发现 {len(drift_criteria)} 条 drift:')
        for c in drift_criteria[:2]:
            print(f'    {c["criterion_id"]}')
            print(f'      准则: {c["positive"][:60]}')
            print(f'      原因: {c["grounding"]["reason"]}')


if __name__ == '__main__':
    main()
