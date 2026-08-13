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
from lib import stage

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

只输出 JSON：
{"results": [{"idx": 1, "met": true, "evidence": "原文摘句，不超过80字", "reason": ""}]}
idx 对应下方准则的编号，必须每条都给。'''

# 程序化核验：把答案与回复都归一化后做包含匹配
_NORM = re.compile(r'[\s　,，、;；:：\'"“”‘’()（）\[\]【】<>《》*`#\\]+')


def norm_ans(t):
    return _NORM.sub('', str(t or '')).lower()


def check_program(ans, text):
    """答案是否出现在回复里。返回 (可判定, 命中)。

    只在答案足够特征化时才敢用：太短（如「3」）会在长文里随机命中，
    这种交给 LLM 判。
    """
    a, t = norm_ans(ans), norm_ans(text)
    if len(a) < 6:
        return False, False
    return True, a in t


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

        # 1) 先做程序化核验：gated_answer 的答案项（is_gate 那条）
        prog = {}
        ans = (r.get('answer') or '').strip()
        if r.get('rubric_form') == 'gated_answer' and ans and r.get('answer_sound', True):
            pos = [c for c in rubrics if c['is_positive']]
            if pos:
                s_max = sum(c['score'] for c in pos)
                for i, c in enumerate(rubrics, 1):
                    if (c['is_positive'] and c['score'] >= 4
                            and s_max and c['score'] / s_max >= 0.3):
                        ok, hit = check_program(ans, resp['text'])
                        if ok:
                            prog[i] = hit

        # 2) 其余交给 LLM
        obj, _ = stage.json_call(m, build(r, resp, rubrics), stage='s12L', thinking=THINK)
        got = {}
        for x in (obj.get('results') or []):
            if isinstance(x, dict) and isinstance(x.get('idx'), int):
                got[x['idx']] = x

        items, score = [], 0
        for i, c in enumerate(rubrics, 1):
            g = got.get(i) or {}
            if i in prog:
                met, ev, why, byp = prog[i], '', '程序化核验：答案字符串匹配', True
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
                          'is_gate': bool(i in prog)})
        return rid, tier, items, score

    done, errs = stage.run(one, jobs, workers=WORKERS, desc='s12L')

    agg = defaultdict(dict)
    for rid, tier, items, score in done:
        agg[rid][tier] = {'items': items, 'score': score}

    res = []
    for r in recs:
        pos = [c for c in r.get('rubrics') or [] if c['is_positive']]
        s_max = sum(c['score'] for c in pos)
        scored = {}
        for p in r.get('pool') or []:
            j = agg[r['rid']].get(p['tier'])
            if not j:
                continue
            scored[p['tier']] = {
                'score': j['score'], 's_max': s_max,
                'rate': round(j['score'] / s_max, 4) if s_max else 0.0,
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
