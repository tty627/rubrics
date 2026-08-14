"""步骤 12L：判分 —— 用 rubric 逐条判定回复池里每条回复，输出 y/n + 证据句。

**硬约束第 2 条：判分器必须与生成器不同源。**
生成用 glm-ac（family=glm），判分用 openai 系。同系列模型有自偏好偏差，
拿 glm 判 glm 生成的 rubric 会虚高。本步启动时校验 family，不同源才跑。

**强制引用证据句**（设计文档 §12）：两个作用 —— 抑制判分漂移（不能凭印象给 y），
以及白送一条审计链（badcase 报告里能直接看到判据）。代价是 token 上升。

**gated_answer 的答案项走程序化核验**：数学答案精确匹配、代码看输出，
不必过 LLM。这是 RLVR 的做法，比 LLM 判分更可靠也更便宜。
判定依据是 s05L 抽出的 `answer` 字段（有锚点真值才敢这么做）。
命中程序化核验的准则记 `by_program=True`，与 LLM 判的分开统计。

**消除位置偏差**：同一批判分固定准则顺序（按 _criterion_id 排序）与呈现顺序。
一次调用判完一条回复的所有准则，而不是每条准则单独调 —— 后者会让判分器
失去「这份回复整体什么样」的上下文，对完整性类准则尤其不公。

输入: s10L_pool.jsonl（含 rubrics + pool）
输出: s12L_judged.jsonl，每题每条回复一份判分结果
"""
import json, os, re, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage, rubric

WORKERS = int(os.environ.get('RP_WORKERS', 8))
THINK = stage.envflag('RP_THINK', True)
SRC = os.environ.get('RP_S12L_SRC', 's10L_pool.jsonl')
OUT = os.environ.get('RP_S12L_OUT', 's12L_judged.jsonl')

SYS = '''你是评分器。给定一道题、一份回答、一组评分准则，逐条判定该回答是否满足。

【判定规则】
- 满足 → met = true，并**从回答原文中摘一句作为证据**（evidence，原文照抄，不要改写）
- 不满足 → met = false，evidence 留空，reason 一句话说明缺什么
- 扣分项（is_positive=false 的准则）：**判定「这个错误是否出现在回答里」**。
  出现了 met=true（会被扣分），没出现 met=false（不扣分）。注意这与正向项相反。

【三条纪律】
1. **证据必须是原文**。摘不出原文就说明没满足，判 false。不要凭印象给 true，
   也不要把「我觉得它大概表达了这个意思」当满足。
2. **只判准则字面要求的内容**。准则没要求的深度、篇幅、格式，不要额外加码。
3. **回答写得长、术语多、条理清晰，不等于满足准则**。
   有些回答把各方面都提一句但都没有实质内容 —— 这种情况下，要求具体内容的准则
   应判 false。你要看的是**说得对不对、够不够具体**，不是**提没提到**。
4. **答案类准则只认最终结论**（维度含「答案」的准则）。回答的**最终答案/结论**
   才算数：正文中间出现的正确结果、与最终结论矛盾的推导，都不能作为满足的依据。
   实测对抗样本：推导过程得出正确集合、最终答案却写空集 —— 这种必须判 false，
   证据只能从最终结论摘。

只输出 JSON：
{"results": [{"idx": 1, "met": true, "evidence": "原文摘句，不超过80字", "reason": ""}]}
idx 对应下方准则的编号，必须每条都给。'''

# 程序化核验。**原则：宁可不判，不可错判** —— 它的全部价值在于「判了就一定对」，
# 做不到就退回 LLM。
#
# 旧实现（归一化后子串包含）两头都错，实测：
#   q0303  正确答案是对抗回复的子串（对抗档末尾多加了 ",1"）→ 答错却判对
#   q0048  answer 存的是整句话「将 host 配置为 0.0.0.0，例如 uvicorn ...」，
#          强档回复写了 --host 0.0.0.0 但措辞不同 → 答对却判错，闸门项直接清零
# 现在改为按 s05L 给出的 answer_kind 分策略，且只认 answer_canonical（最小可判定串）。
# 2026-08-14 修复（48 试点审计）：
#   - option 正则负向断言禁止字母后跟句点 → `A.` 永不命中（q0179 闸门清零）
#   - 短数字（≤2 位）全文本匹配被公式常数命中（q0166 canon='2' 命中 '2π'）
#   核验逻辑已抽到 lib/answer_check.py，与 s10L 的反向校验共用。
from lib import answer_check
check_program = answer_check.check_program


def build(r, resp, rubrics):
    q = (r.get('query_eff') or r['question'])[:1500]
    lines = []
    for i, c in enumerate(rubrics, 1):
        kind = '应做到' if c['is_positive'] else '⚠️不该出现的错误'
        lines.append(f'{i}. [{kind}｜{c["dimension"]}] {c["criteria"]}')
    return [{'role': 'system', 'content': SYS},
            {'role': 'user', 'content':
                f'【题目】\n{q}\n\n'
                f'【回答】\n{resp["text"][:8000]}\n\n'
                f'【评分准则】共 {len(rubrics)} 条，逐条判定：\n' + '\n'.join(lines)}]


def main():
    m = stage.pick('RP_M_JUDGE', 'judge')
    m_gen = stage.pick('RP_M_GEN', 'generator')
    recs = stage.read_jsonl(SRC)

    print(f'步骤 12L 判分: {len(recs)} 题, 源={SRC}')
    print(f'  判分器={m.name} (family={m.family})  生成器={m_gen.name} (family={m_gen.family})')
    if m.family == m_gen.family:
        sys.exit(f'✗ 违反硬约束第 2 条：判分器与生成器同为 {m.family} 系，'
                 f'自偏好偏差会让判分虚高。请用 RP_M_JUDGE 指定异源模型。')
    print(f'  ✓ 硬约束第 2 条：判分器与生成器异源')

    # 摊平成 (rid, 档位)。准则按 _criterion_id 排序固定顺序，消除位置偏差
    jobs = []
    for r in recs:
        for p in r.get('pool') or []:
            jobs.append((r['rid'], p['tier']))
    print(f'  判分任务: {len(jobs)} (题 × 回复档位)')

    by_rid = {r['rid']: r for r in recs}

    def rubrics_of(r):
        return sorted(r.get('rubrics') or [],
                      key=lambda c: c.get('_criterion_id', ''))

    def one(job):
        rid, tier = job
        r = by_rid[rid]
        resp = next(p for p in r['pool'] if p['tier'] == tier)
        rubrics = rubrics_of(r)

        # 1) 先做程序化核验：只对 gated_answer 的**唯一**闸门项，且答案可比对时。
        #    要求唯一：闸门被拆成「规则对不对」+「条目全不全」两条后，
        #    拿同一个答案串比两次必然得到相同结果，对第二条毫无意义。
        prog = {}
        kind = r.get('answer_kind', 'none')
        canon = (r.get('answer_canonical') or '').strip()
        if (r.get('rubric_form') == 'gated_answer' and canon
                and r.get('answer_sound', True)):
            # 闸门判定口径 = lib/rubric.gate_indices
            # （与 s11Lb 豁免、交付档 is_gate 同一规则），下标转 1 起对齐 idx
            gates = [i + 1 for i in rubric.gate_indices(rubrics)]
            if len(gates) == 1:
                ok, hit = check_program(kind, canon, resp['text'])
                if ok:
                    prog[gates[0]] = hit

        # 2) 其余交给 LLM。缺项要重试 —— 静默判 false 会把「模型漏答」
        #    算成「回复不满足」，直接扭曲得分率。
        msgs = build(r, resp, rubrics)
        need = {i for i in range(1, len(rubrics) + 1) if i not in prog}
        got = {}
        for attempt in range(2):
            obj, _ = stage.json_call(m, msgs, stage='s12L', thinking=THINK)
            for x in (obj.get('results') or []):
                if isinstance(x, dict) and isinstance(x.get('idx'), int):
                    got[x['idx']] = x
            miss = sorted(need - set(got))
            if not miss:
                break
            if attempt == 0:
                msgs = msgs + [
                    {'role': 'assistant', 'content': json.dumps(
                        {'results': list(got.values())}, ensure_ascii=False)[:1500]},
                    {'role': 'user', 'content':
                        f'你漏了第 {miss} 条准则的判定。请**只输出**这些编号的结果，'
                        f'格式同前：{{"results": [{{"idx": N, "met": ..., '
                        f'"evidence": "...", "reason": "..."}}]}}'}]

        missing = sorted(need - set(got))

        items, score = [], 0
        for i, c in enumerate(rubrics, 1):
            g = got.get(i) or {}
            if i in prog:
                met, ev, byp = prog[i], '', True
                why = f'程序化核验（{kind}）：{"命中" if met else "未命中"} {canon[:60]}'
            elif i in missing:
                # 判分器两轮都没返回这条 —— 标出来，不要当成「未满足」悄悄扣分
                met, ev, why, byp = False, '', '⚠️ 判分器未返回该条，非回复问题', False
            else:
                met = bool(g.get('met'))
                ev = str(g.get('evidence', ''))[:200]
                why = str(g.get('reason', ''))[:120]
                byp = False
                # 纪律 1：正向项判 true 必须有原文证据，没证据不给分
                if met and c['is_positive'] and not ev.strip():
                    met, why = False, '判 true 但未给证据句，按未满足处理'
            if met:
                score += c['score']            # 负向项 score 本身是负数
            items.append({'_criterion_id': c.get('_criterion_id'), 'idx': i,
                          'is_positive': c['is_positive'], 'score': c['score'],
                          'dimension': c['dimension'], 'met': met,
                          'evidence': ev, 'reason': why, 'by_program': byp,
                          'judge_missing': i in missing})
        return rid, tier, items, score, missing

    done, errs = stage.run(one, jobs, workers=WORKERS, desc='s12L')

    agg = defaultdict(dict)
    for rid, tier, items, score, missing in done:
        agg[rid][tier] = {'items': items, 'score': score, 'missing': missing}

    # ---- 同源一致性护栏（48 试点审计 q0174/q0242/q0287/q0314/q0440）----
    # trunc/cut 是 strong 的字面子集：同一内容在子集档 met、超集档未 met
    # 在逻辑上不可能，只可能是判分器跨档双标（同句一✓一✗）。以 strong 为准
    # 修正子集档，记 judge_fixed，不额外调 LLM。
    n_fixed = 0
    for rid, tiers in agg.items():
        if 'strong' not in tiers:
            continue
        strong_items = {x['_criterion_id']: x for x in tiers['strong']['items']}
        for t in ('trunc', 'cut'):
            j = tiers.get(t)
            if not j:
                continue
            changed = False
            for x in j['items']:
                s = strong_items.get(x['_criterion_id'])
                if not s or s.get('judge_missing') or x.get('judge_missing'):
                    continue
                if x['met'] and not s['met']:
                    x['met'] = False
                    x['judge_fixed'] = True
                    x['reason'] = ('判分一致性修正：该档是强档的字面子集，'
                                   '同内容强档未满足，以强档为准')
                    x['evidence'] = ''
                    changed = True
            if changed:
                j['score'] = sum(x['score'] for x in j['items'] if x['met'])
                n_fixed += 1

    res = []
    for r in recs:
        pos = rubric.positives(r.get('rubrics') or [])
        s_max = rubric.s_max(r.get('rubrics') or [])
        scored = {}
        for p in r.get('pool') or []:
            j = agg[r['rid']].get(p['tier'])
            if not j:
                continue
            scored[p['tier']] = {
                'score': j['score'], 's_max': s_max,
                'rate': rubric.rate(j['score'], s_max),
                'judge_incomplete': bool(j.get('missing')),
                'n_missing': len(j.get('missing') or []),
                'n_met': sum(1 for x in j['items'] if x['met'] and x['is_positive']),
                'n_pos': len(pos),
                'n_penalty': sum(1 for x in j['items'] if x['met'] and not x['is_positive']),
                'items': j['items']}
        res.append({**r, 's_max': s_max, 'judged': scored})
    stage.write_jsonl(OUT, res)

    print(f'\n=== 步骤 12L 结果 ===')
    if errs:
        print(f'  失败        : {len(errs)} 条')
    n_prog = sum(1 for r in res for t in r['judged'].values()
                 for x in t['items'] if x['by_program'])
    n_all = sum(len(t['items']) for r in res for t in r['judged'].values())
    print(f'  判定总数    : {n_all}  其中程序化核验 {n_prog} 条'
          f'（gated 答案项，不过 LLM）')
    n_noev = sum(1 for r in res for t in r['judged'].values() for x in t['items']
                 if x['reason'].startswith('判 true 但未给证据'))
    if n_noev:
        print(f'  ⚠️  判 true 但无证据: {n_noev} 条，已按未满足处理')
    if n_fixed:
        print(f'  ⚙️  同源一致性修正: {n_fixed} 档（trunc/cut 与 strong 同内容'
              f'判分冲突，已以 strong 为准，见 items[].judge_fixed）')
    inc = [(r['rid'], t, v['n_missing']) for r in res
           for t, v in r['judged'].items() if v.get('judge_incomplete')]
    if inc:
        print(f'  ⚠️  判分器漏返回: {len(inc)} 处（重试后仍缺，非回复问题，'
              f'得分率偏低）: ' + ', '.join(f'{a}/{b}({c})' for a, b, c in inc[:6]))

    print(f'\n  各档得分率（应当单调下降；不降就是 rubric 区分不开）:')
    for tier in ('strong', 'mid', 'trunc', 'cut', 'weak', 'adv'):
        rs = [r['judged'][tier]['rate'] for r in res if tier in r['judged']]
        if rs:
            rs_s = sorted(rs)
            print(f'    {tier:<8} mean={sum(rs) / len(rs):6.1%}  '
                  f'min={rs_s[0]:6.1%}  max={rs_s[-1]:6.1%}  (n={len(rs)})')

    ex = res[0]
    print(f'\n  抽样 {ex["rid"]} (满分 {ex["s_max"]}):')
    for tier in ('strong', 'mid', 'trunc', 'cut', 'weak', 'adv'):
        j = ex['judged'].get(tier)
        if j:
            print(f'    {tier:<8} {j["score"]:3d}/{j["s_max"]} = {j["rate"]:6.1%}  '
                  f'满足 {j["n_met"]}/{j["n_pos"]} 条，触发扣分 {j["n_penalty"]}')


if __name__ == '__main__':
    main()
