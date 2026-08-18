"""步骤 12b：草稿 rubric 判分 —— Phase 4 检查点 2 的判分侧。

流程位置：Phase 4 实测线之后。检查点 2（PLAN.md §Phase 4）要求「用新 rubric
和草稿 rubric 分别给 strong vs weak 打分，比 pairwise 一致率」——新 rubric 的
判分数据已经在 s11c_cons388 / s11c_r1..r3 里（每轮闭环实测），**缺草稿这一半**。

草稿 rubric 来源：seed 的 draft_rubric（xlsx F 列，人工/草稿口径）：
  {"intent", "question_type", "rubrics": [{criteria, score, weight, reason, dimension}]}
口径对齐（s00_seed.baseline 同源）：
  - is_positive = score >= 0（负项 score < 0，措辞「是否出现X错误」）
  - 判分分值 = score × weight（baseline 的满分口径就是 Σ score×weight）
  - 草稿没有 is_gate / is_veto / severity —— 一律不标，veto 两票制不适用

判分口径复用 s12_judge 的 SYS + build()（同一套纪律：证据句、只判字面要求、
答案类准则只认最终结论），换口径测出来的分数与检查点不可比。
只判 strong + weak 两档（检查点原文「gpt55 vs 弱档」），不判 mid/trunc/cut/adv。

硬约束第 2 条：判分器 family ≠ 生成器。**注意**：config 里第一个 judge 角色是
by-judge（35.220.164.252 端点，2026-08-17 起持续 401），务必显式
RP_M_JUDGE=cn-judge 运行（scripts/rerun_checkpoint2.sh 已固定）。

输入: s10_pool388.jsonl（含 draft_rubric + pool + query_eff/answer_*）
输出: s12b_draft388.jsonl，每题 draft_judged = {strong, weak}，字段与 s12_judge 一致
      （score / s_max / raw_rate / rate / items / judge_incomplete / n_missing）。
"""
import json, os, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage, rubric
from stages import s12_judge as s12

WORKERS = int(os.environ.get('RP_WORKERS', 8))
THINK = stage.envflag('RP_THINK', True)
SRC = os.environ.get('RP_S12LB_SRC', 's10_pool388.jsonl')
OUT = os.environ.get('RP_S12LB_OUT', 's12b_draft388.jsonl')
TIERS = ('strong', 'weak')


def draft_rubrics(r):
    """把草稿 rubric 转成 s12_judge 判分器能吃的准则列表（口径见文件头）。"""
    d = r.get('draft_rubric') or {}
    out = []
    for i, c in enumerate(d.get('rubrics') or [], 1):
        score = c.get('score', 0) or 0
        weight = c.get('weight', 1) or 1
        eff = round(score * weight, 3)
        out.append({
            '_criterion_id': f"{r['rid']}-D{i}",
            'idx': i,
            'criteria': c.get('criteria', ''),
            'score': eff,
            'dimension': c.get('dimension') or '知识正确性',
            'is_positive': score >= 0,
            'is_veto': False,
            'severity': None,
            # 原始草稿字段留审计
            'draft_score': score,
            'draft_weight': weight,
            'reason': c.get('reason', ''),
        })
    return out


def one(job):
    """(rid, tier) → 判分结果。只判 strong/weak，无程序化核验、无 veto 复判。"""
    rid, tier = job
    r = by_rid[rid]
    resp = next((p for p in r.get('pool') or [] if p['tier'] == tier), None)
    if resp is None:
        return rid, tier, None
    rubrics = draft_rubrics(r)
    msgs = s12.build(r, resp, rubrics)
    need = {i for i in range(1, len(rubrics) + 1)}
    got = {}
    broken = ''
    for attempt in range(2):
        try:
            obj, _ = stage.json_call(m, msgs, stage='s12Lb', thinking=THINK)
            obj = obj if isinstance(obj, dict) else {}
        except ValueError as e:
            # JSON 解析重试耗尽：整档标 judge_incomplete（不进放行集合），
            # 不让整个任务崩掉（2026-08-17 实测：模型偶发输出非 JSON 数组/文本）
            broken = str(e)[:80]
            break
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
    if broken:
        missing = sorted(need)

    items, score = [], 0.0
    for i, c in enumerate(rubrics, 1):
        g = got.get(i) or {}
        if i in missing:
            met, ev, why, byp = False, '', '⚠️ 判分器未返回该条，非回复问题', False
        else:
            met = bool(g.get('met'))
            ev = str(g.get('evidence', ''))[:200]
            why = str(g.get('reason', ''))[:120]
            byp = False
            # 纪律 1（与 s12_judge 同）：正向项判 true 必须有原文证据
            if met and c['is_positive'] and not ev.strip():
                met, why = False, '判 true 但未给证据句，按未满足处理'
        if met:
            score += c['score']
        items.append({'_criterion_id': c['_criterion_id'], 'idx': i,
                      'is_positive': c['is_positive'], 'score': c['score'],
                      'dimension': c['dimension'], 'met': met,
                      'is_veto': False, 'severity': None,
                      'evidence': ev, 'reason': why, 'by_program': byp,
                      'judge_missing': i in missing})
    return rid, tier, (items, score, missing)


def main():
    global m, by_rid
    m = stage.pick('RP_M_JUDGE', 'judge')
    m_gen = stage.pick('RP_M_GEN', 'generator')
    recs = stage.read_jsonl(SRC)
    print(f'步骤 12b 草稿判分: {len(recs)} 题, 源={SRC}')
    print(f'  判分器={m.name} (family={m.family})  生成器={m_gen.name} '
          f'(family={m_gen.family})')
    if m.family == m_gen.family:
        sys.exit(f'✗ 违反硬约束第 2 条：判分器与生成器同为 {m.family} 系，'
                 f'请用 RP_M_JUDGE 指定异源模型。')
    print('  ✓ 硬约束第 2 条：判分器与生成器异源')
    if m.name.startswith('by-'):
        print('  ⚠️ 判分器走 by-* 端点：2026-08-17 起该代理对多数模型 401，'
              '建议 RP_M_JUDGE=cn-judge')

    by_rid = {r['rid']: r for r in recs}
    nodraft = {r['rid']: '缺草稿 rubric'
               for r in recs if not (r.get('draft_rubric') or {}).get('rubrics')}
    if nodraft:
        print(f'  ⚠️ 缺草稿 rubric: {len(nodraft)} 题: {list(nodraft)[:10]}')
    jobs = [(r['rid'], t) for r in recs for t in TIERS
            if r['rid'] not in nodraft
            and any(p['tier'] == t for p in r.get('pool') or [])]
    print(f'  判分任务: {len(jobs)} (仅有草稿且有 strong/weak 的题)')

    done, errs = stage.run(one, jobs, workers=WORKERS, desc='s12Lb')
    agg = defaultdict(dict)
    for rid, tier, result in done:
        if result is None:
            continue
        items, score, missing = result
        s_max = sum(c['score'] for c in draft_rubrics(by_rid[rid])
                    if c['is_positive'])
        agg[rid][tier] = {
            'score': score, 's_max': s_max,
            'raw_rate': rubric.rate(score, s_max),
            'vetoed': False, 'veto_by': [],
            'rate': rubric.rate(score, s_max),
            'judge_incomplete': bool(missing),
            'n_missing': len(missing),
            'n_met': sum(1 for x in items if x['met'] and x['is_positive']),
            'n_pos': sum(1 for x in items if x['is_positive']),
            'n_penalty': sum(1 for x in items if x['met'] and not x['is_positive']),
            'n_veto': 0,
            'items': items}

    failed_jobs = {}
    for index, message in errs:
        rid, tier = jobs[index]
        failed_jobs[(rid, tier)] = str(message)[:500]
    for (rid, tier), message in failed_jobs.items():
        rubrics = draft_rubrics(by_rid[rid])
        missing = list(range(1, len(rubrics) + 1))
        s_max = sum(c['score'] for c in rubrics if c['is_positive'])
        agg[rid][tier] = {
            'score': 0.0, 's_max': s_max,
            'raw_rate': None, 'rate': None, 'vetoed': False, 'veto_by': [],
            'judge_incomplete': True, 'n_missing': len(missing),
            'n_met': 0, 'n_pos': sum(1 for c in rubrics if c['is_positive']),
            'n_penalty': 0, 'n_veto': 0, 'items': [],
            'judge_error': message}

    res = []
    for r in recs:
        dr = draft_rubrics(r)
        s_max = sum(c['score'] for c in dr if c['is_positive'])
        reasons = []
        if r['rid'] in nodraft:
            reasons.append(nodraft[r['rid']])
        pool_tiers = {p['tier'] for p in r.get('pool') or []}
        for tier in TIERS:
            if tier not in pool_tiers:
                reasons.append(f'缺回复档 {tier}')
        if any(rid == r['rid'] for rid, _ in failed_jobs):
            reasons.append('判分任务失败')
        rec = {**r, 'draft_s_max': s_max,
               'draft_rubrics': dr, 'draft_judged': dict(agg[r['rid']])}
        if reasons:
            rec['_checkpoint2'] = {'excluded': True,
                                   'exclude_reason': '；'.join(dict.fromkeys(reasons))}
        own_errors = [stage.error_entry('s12Lb', tier, message)
                      for (rid, tier), message in failed_jobs.items()
                      if rid == r['rid']]
        res.append(stage.add_stage_errors(rec, own_errors))
    stage.write_jsonl(OUT, res)

    print(f'\n=== 步骤 12b 结果 ===')
    if errs:
        print(f'  失败: {len(errs)} 条，已写入 draft_judged[*].judge_error 和 _stage_errors')
        for index, message in errs[:12]:
            rid, tier = jobs[index]
            print(f'    {rid}/{tier}: {str(message)[:120]}')
    if nodraft:
        print(f'  检查点 2 显式排除无草稿题: {len(nodraft)}')
    for tier in TIERS:
        rs = [r['draft_judged'][tier]['rate'] for r in res
              if tier in r['draft_judged']
              and isinstance(r['draft_judged'][tier].get('rate'), (int, float))]
        if rs:
            print(f'    {tier:<8} mean={sum(rs)/len(rs):6.1%}  '
                  f'min={min(rs):6.1%}  max={max(rs):6.1%}  (n={len(rs)})')
    inc = [(r['rid'], t, v['n_missing']) for r in res
           for t, v in r['draft_judged'].items() if v.get('judge_incomplete')]
    if inc:
        print(f'  ⚠️ 判分器漏返回: {len(inc)} 处: '
              + ', '.join(f'{a}/{b}({c})' for a, b, c in inc[:8]))
    print(f'  n_criteria 分布: {Counter(len(r["draft_rubrics"]) for r in res).most_common(6)}')
    excluded = Counter((r.get('_checkpoint2') or {}).get('exclude_reason', '')
                       for r in res if r.get('_checkpoint2'))
    if excluded:
        print(f'  检查点 2 排除原因: {dict(excluded)}')


if __name__ == '__main__':
    main()
