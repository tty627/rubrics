"""步骤 11d：Consequential 缺陷处置 —— 对实测确认的真缺陷重写 rubric。

消费 s11c_consequential 的 Consequential 诊断。**只对实测确认的缺陷动手**：
  - hackable（真缺陷）  → LLM 重写整份 rubric：提及型准则改成内容核对式，
                          缺深度梯度的补一条展开度要求；正向总分守恒。
  - floor（准则过严）    → LLM 放松：删掉锚定过细的精确数字/专有措辞，
                          一条准则只绑一个判断点；条数与分值不变。
  - pool 嫌疑            → 只标记不重写：gated/verifiable 题的 weak/对抗档
                          平分多是「弱档把答案答对了」的造法失败，而 canonical
                          缺失时程序化核验无从下手——这是 s10_pool 的活，不在这步
                          拿 rubric 开刀（48 试点审计：q0045/q0238/q0242/q0301/
                          q0445/q0448/q0058 都属此类）。
  - low_signal 单独命中  → 只标记 needs_review（阈值边缘案例，如 q0133/q0314）。

2026-08-14 首版（48 试点审计 §5.4 的处置目标）：
  真缺陷 3 题 q0167/q0336/q0388 + 地板 4 题 q0149/q0377/q0408/q0440。
  复测方式：s11Ld 输出 → s12L 重判（RP_S12L_SRC 指向本步输出）→ s11Lc 复诊。

输入: s11c_consequential.jsonl
输出: s11d_remedied.jsonl（rubrics 已重写 + s11d_remedy 处置记录）
"""
import json, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage, dimensions, rubric
# 锚可达性探针要用 s12_judge 的判分口径（同一套 SYS + 同一套准则渲染），
# 换口径测出来的分数与 s11c_consequential 的地板判定不可比。
from stages import s12_judge as s12

WORKERS = int(os.environ.get('RP_WORKERS', 8))
THINK = stage.envflag('RP_THINK', True)
SRC = os.environ.get('RP_S11LD_SRC', 's11c_consequential.jsonl')
OUT = os.environ.get('RP_S11LD_OUT', 's11d_remedied.jsonl')
# 白名单（逗号分隔 rid）：只处置这些题。留空走默认启发式。
ONLY = [x.strip() for x in os.environ.get('RP_S11LD_ONLY', '').split(',') if x.strip()]

SYS_HACK = f'''你在修一份评分标准（rubric）。它被**实测**判定为「可被钻空子」或「区分度不足」：
- 可被钻空子：一份面面俱到、但每点只有一句话的浅回答，拿到了与完整高质量
  回答相同的分数——准则大多是「提及即得分」式，没有内容与深度梯度。
- 区分度不足：弱回答的得分与好回答拉不开差距（弱档得分过高或强档得分过低），
  说明高分值准则的判定门槛太低。

请重写整份 rubric：

【改写要求】
1. 把「提到 X / 说明 X / 描述 X」式的准则改成**内容核对式**：
   要求「关于 X 的哪个具体判断成立」——判分器能凭这个判断直接判对错。
   自检：一份把话题提了一句但说得很浅、甚至说得不对的回答，能通过新准则吗？
   能通过就还没改对。
2. 若题目明确要求讲解/拆解/分析/论证，补上**深度或展开度**要求
   （如「对每个选项逐条给出判断理由，仅给结论不给依据不得分」），
   可替换最弱的一条或新增 1 条。**这类深度要求的判定必须排除「一句话带过」**。
3. 单条准则分值 ≥ 满分一半的，把判定门槛写实（不能一句话就满足），
   或把分值拆给区分性内容。
4. **正向总分守恒**：新 rubric 的正向分值之和必须等于原正向总分
   （你给出的分值我会程序化归一，请按重要度给相对大小即可）。
5. 每条准则 ≤70 字、必须是本题专属、判分器能直接核对的具体表述。
   禁止空泛词（准确/完整/清晰/合理/充分/全面/深入）。
6. **扣分项（is_positive=false）原样保留**，不要改动内容与分值。
7. 不要引入题目没问的内容；不确定的数值与机理不要写死。

{dimensions.prompt_block()}

只输出 JSON（顺序 = 新的准则顺序）：
{{"rubrics": [
  {{"criteria": "…", "score": 2, "reason": "…", "dimension": "从上表选", "is_positive": true}},
  …],
  "note": "一句话说明改了什么"}}'''

SYS_FLOOR = f'''你在修一份评分标准（rubric）。它被**实测**判定为「过严」：
一份内容正确、覆盖完整的好回答（强档）只拿到很低的得分率。典型原因是
准则写死在锚定回复的具体细节上——精确数值、专有名词、多个细节捆绑在一条里，
换个好说法就判不满足。

请放松整份 rubric：

【放松要求】
1. 每条准则改写成该知识点的**核心判断**：删掉对具体数字/特定措辞的要求，
   同义表述、等价说法都算满足。
   例：「指出最优 pH 为 4.0-5.5」→「指出最适 pH 在弱酸性范围」。
2. 一条准则只绑**一个**判断点；捆绑多个细节的准则只保留最核心的那个。
3. 条数不变、顺序不变、每条分值不变（含扣分项）。
4. 每条 ≤70 字、判分器能直接核对。禁止空泛词（准确/完整/清晰/合理/充分/全面）。
5. 不要引入题目没问的内容。

{dimensions.prompt_block()}

只输出 JSON（顺序与输入一致）：
{{"rubrics": [{{"criteria": "…", "score": 原分值, "reason": "…",
   "dimension": "从上表选", "is_positive": 原方向}}],
  "note": "一句话说明放松了什么"}}'''


def select(r):
    """决定处置动作。返回 (action, info) 或 (None, None)。"""
    c = r.get('consequential') or {}
    if not c or c.get('skip_reason'):
        return None, None
    hk = c.get('hackable') or {}
    cal = c.get('calibration') or {}
    lo = c.get('low_signal') or {}
    if ONLY:
        if r['rid'] in ONLY:
            if hk.get('is_defective'):
                return 'hackable', hk
            if lo.get('is_defective'):
                # LowSignal（弱档与强档拉不开差距）与 Hackable 同方向处置：
                # 提高准则区分性 —— 复用 hackable 重写
                return 'hackable', lo
            if cal.get('issue') == 'floor':
                return 'floor', cal
            return None, None
        return None, None
    if cal.get('issue') == 'floor' and not hk.get('is_defective'):
        return 'floor', cal
    if hk.get('is_defective'):
        is_open = r.get('question_type') == 'open'
        if is_open or hk.get('surface_criteria'):
            return 'hackable', hk
        # gated/verifiable 且无准则级证据：弱档平分多为造法失败（答对答案），
        # canonical 缺失时无法程序化复核 → 标记，不动 rubric
        return 'pool_suspect', hk
    if lo.get('is_defective'):
        return 'review', lo
    return None, None


def ctx(r):
    q = (r.get('query_eff') or r['question'])[:1500]
    subj = ' / '.join(r.get('subject') or []) or '未标注'
    return (f'【学科】{subj}\n'
            f'【题型】{r.get("question_type", "")} → {r.get("rubric_form", "")}\n\n'
            f'【题目】\n{q}\n')


def rubric_lines(r):
    out = []
    for c in r.get('rubrics') or []:
        sign = '+' if c.get('is_positive') else '−'
        out.append(f'{sign}{abs(c.get("score", 0))} [{c.get("dimension", "")}] '
                   f'{c.get("criteria", "")}')
    return '\n'.join(out)


def build(r, action, info):
    u = ctx(r) + f'\n【当前 rubric】\n{rubric_lines(r)}\n'
    if action == 'hackable':
        j = r.get('judged') or {}
        rates = {t: v.get('rate', 0) for t, v in j.items() if isinstance(v, dict)}
        u += (f'\n【实测证据】各档得分率 { {t: round(v, 2) for t, v in rates.items()} }；\n'
              f'诊断理由：{info.get("reasons", [])}\n')
        if info.get('surface_criteria'):
            cid2txt = {c.get('_criterion_id'): c.get('criteria', '')
                       for c in r.get('rubrics') or []}
            u += '被钻空子的准则：\n' + '\n'.join(
                f'- [{x["_criterion_id"]}]（{x["tier"]} 档满足、强档未满足）'
                f'{cid2txt.get(x["_criterion_id"], "")}'
                for x in info['surface_criteria'][:6]) + '\n'
        return [{'role': 'system', 'content': SYS_HACK}, {'role': 'user', 'content': u}]
    if action == 'floor':
        j = r.get('judged') or {}
        rates = {t: v.get('rate', 0) for t, v in j.items() if isinstance(v, dict)}
        u += (f'\n【实测证据】{info.get("reason", "")}\n'
              f'各档得分率 { {t: round(v, 2) for t, v in rates.items()} }\n')
        return [{'role': 'system', 'content': SYS_FLOOR}, {'role': 'user', 'content': u}]
    return None


def candidate_reachable(m, r):
    """Check whether any post-freeze candidate demonstrates rubric reachability.

    This is measurement evidence only. Candidate responses do not define the rubric or
    canonical truth. If one reaches 30%, a floor signal may come from pool construction,
    so automatically relaxing the rubric would be premature.
    """
    refs = r.get('ref_responses') or {}
    texts = [v for _, v in sorted(refs.items()) if isinstance(v, str) and v.strip()]
    if not texts:
        return False, None
    rubrics = r.get('rubrics') or []
    best = None
    for t in texts:
        obj, _ = stage.json_call(m, judge_msgs(r, t, rubrics),
                                 stage='s11Ld', thinking=False)
        got = judge_rate(obj, rubrics)
        if got is None:
            continue
        best = got if best is None else max(best, got)
    if best is None:
        return False, None
    return best >= 0.3, best


def judge_msgs(r, text, rubrics):
    """复用 s12_judge 的判分口径，保持候选可达性与实测得分可比。"""
    return s12.build(r, {'text': text}, rubrics)


def judge_rate(obj, rubrics):
    """把判分 JSON 折成 raw_rate（补偿式，与 s11c_consequential 同口径）。"""
    if not isinstance(obj, dict):
        return None
    res = obj.get('results') or []
    if not res:
        return None
    got = 0
    for x in res:
        try:
            i = int(x.get('idx', 0)) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= i < len(rubrics)) or not x.get('met'):
            continue
        c = rubrics[i]
        got += c['score'] if rubric.is_positive(c) else -abs(c['score'])
    sm = rubric.s_max(rubrics)
    return got / sm if sm else None


SYS_ONTARGET = '''判断一份回答是否在回答题目问的那件事。

只看**主题是否对得上**，不评价质量、深度、篇幅、正误细节：
- 题目问的是 A 概念/A 情形，回答讲的是同名但不同领域的 B 概念 → off_target
- 题目给定的模型/前提/条件，回答换成了另一套自己设定的 → off_target
- 主题对上了，只是讲得浅、漏了点、某处算错 → on_target

只输出 JSON：{"on_target": true/false, "why": "≤30字"}'''


def check_on_target(m, r):
    """地板题前置门：strong 档是否在答同一件事。

    388 全量实测：22 道地板题里 5 道 strong 档答案程序化核验就是错的（已在
    s11c_consequential 跳过），另有 q0047（问相空间刘维尔定理，答的是复分析那个）、q0071
    （题给两对互补基因的分离比，答成单基因+抑制基因）这类**答偏题**的。
    参照系本身偏了，放松准则只会把 rubric 改坏 —— 不重写，标记出来。
    返回 (on_target: bool, why: str)。判不出来时按 True 放过（宁放过不误杀）。
    """
    st = [p for p in (r.get('pool') or []) if p['tier'] == 'strong']
    if not st:
        return True, ''
    q = (r.get('query_eff') or r['question'])[:1200]
    u = f'【题目】\n{q}\n\n【回答】\n{(st[0].get("text") or "")[:2500]}'
    obj, _ = stage.json_call(m, [{'role': 'system', 'content': SYS_ONTARGET},
                                 {'role': 'user', 'content': u}],
                            stage='s11Ld', thinking=False)
    if not isinstance(obj, dict) or 'on_target' not in obj:
        return True, ''
    return bool(obj.get('on_target')), str(obj.get('why', ''))[:60]


def apply(r, obj, action):
    """把模型返回的 rubric 套回记录：分值守恒 + 血缘继承 + 逐槽对齐。"""
    orig = r.get('rubrics') or []
    new = (obj.get('rubrics') or obj.get('criteria') or [])
    new = [x for x in new if isinstance(x, dict) and str(x.get('criteria', '')).strip()]
    if not new:
        return None, '模型未返回任何准则'

    pos_orig = rubric.positives(orig)
    s_max = rubric.s_max(orig)
    n_cap = len(orig) + (1 if action == 'hackable' else 0)
    new = new[:n_cap]

    # 先规范化新文本与方向
    slots = []
    for x in new:
        txt = str(x.get('criteria', '')).strip()[:200]
        is_pos = bool(x.get('is_positive', True))
        dim, hit = dimensions.normalize(str(x.get('dimension', '') or '知识正确性'))
        try:
            sc = abs(int(round(float(x.get('score', 1))))) or 1
        except (TypeError, ValueError):
            sc = 1
        slots.append({'criteria': txt, 'score': sc, 'is_positive': is_pos,
                      'dimension': dim, '_dim_from_table': hit,
                      'reason': str(x.get('reason', ''))[:100]})

    # 方向守恒：新列表的方向必须与原列表一致（扣分项位置对不上则按原方向覆盖）
    if len(slots) == len(orig):
        for s, o in zip(slots, orig):
            if o.get('is_positive') is False and s['is_positive']:
                s['is_positive'] = False
                s['score'] = abs(o['score'])
    else:
        # 条数变化：负向槽只能来自原负向项，其余按正向处理
        negs = [c for c in orig if not c.get('is_positive')]
        n_new_neg = sum(1 for s in slots if not s['is_positive'])
        if n_new_neg > len(negs):
            return None, '扣分项数量增加，不予通过'
        for s in slots:
            if not s['is_positive']:
                s['score'] = abs(negs[0]['score']) if negs else 1
                negs = negs[1:]

    # 正向分值守恒：按比例重分配到原 s_max
    pos_slots = [s for s in slots if s['is_positive']]
    if pos_slots and s_max:
        tot = sum(s['score'] for s in pos_slots) or len(pos_slots)
        alloc = [max(1, round(s_max * s['score'] / tot)) for s in pos_slots]
        diff = s_max - sum(alloc)
        while diff:
            i = max(range(len(alloc)), key=lambda k: alloc[k])
            step = 1 if diff > 0 else -1
            if alloc[i] + step < 1:
                break
            alloc[i] += step
            diff -= step
        for s, a in zip(pos_slots, alloc):
            s['score'] = a

    # 逐槽继承：位置对齐原准则，继承 _criterion_id 与全部血缘字段
    out = []
    for i, s in enumerate(slots):
        if i < len(orig):
            base = dict(orig[i])
            keep = {k: v for k, v in base.items() if k.startswith('_')
                    and k not in ('_dim_from_table', '_flag_vague',
                                  '_flag_no_groundtruth', '_flag_cliff',
                                  '_flag_mention_only', '_flag_subjective_threshold',
                                  '_flag_topic_list')}
        else:
            keep = {'_criterion_id': f"{r['rid']}-L{len(orig) + 1}",
                    '_s11Ld_new': True}
        neg = s['is_positive'] is False
        score = s['score'] if not neg else -s['score']
        out.append({**keep, 'criteria': s['criteria'], 'score': score,
                    'reason': s['reason'], 'dimension': s['dimension'],
                    'is_positive': s['is_positive'],
                    '_dim_from_table': s.get('_dim_from_table', False),
                    '_s11d_rewritten': True})
    return out, ''


def main():
    m = stage.pick('RP_M_S11LD', 'generator')
    recs = stage.read_jsonl(SRC)
    print(f'步骤 11d Consequential 处置: {len(recs)} 题, 源={SRC}')
    print(f'  重写模型={m.name}  白名单={"(" + ",".join(ONLY) + ")" if ONLY else "默认启发式"}')

    jobs, by_rid = [], {r['rid']: r for r in recs}
    plan = {}
    for r in recs:
        action, info = select(r)
        plan[r['rid']] = (action, info)
        if action in ('hackable', 'floor'):
            jobs.append(r['rid'])
    stat = Counter(a or 'none' for a, _ in plan.values())
    print(f'  处置计划    : ' + '  '.join(f'{k}={v}' for k, v in stat.most_common()))

    def one(rid):
        r = by_rid[rid]
        action, info = plan[rid]
        # 地板题先过「答的是不是同一件事」门：strong 答偏题时放松准则等于把
        # rubric 往错的方向改，只标记不重写。
        if action == 'floor':
            ok, why = check_on_target(m, r)
            if not ok:
                return rid, None, f'strong 档答偏题：{why}', 'off_target'
            # 冻结后候选回答能拿到分，说明地板可能来自 pool 构造，不自动放松 rubric
            reach, best = candidate_reachable(m, r)
            if reach:
                return rid, None, f'候选回答在本 rubric 上得 {best:.0%}，准则可达到', 'candidate_ok'
        msgs = build(r, action, info)
        last = None
        for attempt in range(2):
            obj, _ = stage.json_call(m, msgs, stage='s11Ld', thinking=THINK)
            rubs, err = apply(r, obj, action)
            if rubs:
                note = str(obj.get('note', ''))[:120]
                return rid, rubs, note, err
            last = err
            if attempt == 0:
                msgs = msgs + [
                    {'role': 'assistant', 'content': json.dumps(obj, ensure_ascii=False)[:1500]},
                    {'role': 'user', 'content':
                        f'返回的 rubric 未通过校验（{err}）。请重新输出完整 JSON，'
                        f'格式：{{"rubrics": [...], "note": "..."}}'}]
        return rid, None, '', last or '校验失败'

    done, errs = stage.run(one, jobs, workers=WORKERS, desc='s11Ld')
    new_rubrics = {rid: rubs for rid, rubs, note, err in done if rubs}
    failed = {jobs[index]: str(message)[:500] for index, message in errs}
    # 答偏题的地板题：不重写，记原因
    off_target = {rid: note for rid, rubs, note, err in done
                  if not rubs and err in ('off_target', 'candidate_ok')}

    res, n_ok = [], 0
    for r in recs:
        action, info = plan[r['rid']]
        rec = {'action': action or 'none',
               'rid': r['rid'],
               'reasons': (info or {}).get('reasons', []) if isinstance(info, dict) else [],
               'criteria_before': len(r.get('rubrics') or []),
               's_max_before': rubric.s_max(r.get('rubrics') or [])}
        if rid_out := new_rubrics.get(r['rid']):
            rec.update({'rewritten': True, 'criteria_after': len(rid_out),
                        's_max_after': rubric.s_max(rid_out),
                        'note': ''})
            res.append({**r, 'rubrics': rid_out, 's11Ld': rec})
            n_ok += 1
        elif r['rid'] in off_target:
            # 参照系偏了，rubric 不动：这是 pool 侧的问题，等 strong 重生成
            rec.update({'action': 'pool_off_target', 'rewritten': False,
                        'note': off_target[r['rid']]})
            res.append({**r, 's11Ld': rec})
        else:
            if action in ('hackable', 'floor'):
                rec['rewritten'] = False
                rec['fail'] = True
            if r['rid'] in failed:
                rec['stage_error'] = failed[r['rid']]
            out = {**r, 's11Ld': rec}
            if r['rid'] in failed:
                out = stage.add_stage_error(out, 's11Ld', r['rid'], failed[r['rid']])
            res.append(out)
    stage.write_jsonl(OUT, res)

    print(f'\n=== 步骤 11d 结果 ===')
    if errs:
        print(f'  LLM 失败    : {len(errs)} 题，已写入 s11Ld.stage_error 和 _stage_errors')
        for index, message in errs[:12]:
            print(f'    {jobs[index]}: {str(message)[:120]}')
    print(f'  重写成功    : {n_ok}/{len(jobs)} 题')
    for r in res:
        d = r['s11Ld']
        if d['action'] in ('hackable', 'floor', 'pool_suspect', 'review',
                           'pool_off_target'):
            mark = '✓ 已重写' if d.get('rewritten') else ('✗ 失败' if d.get('fail') else '（仅标记）')
            if d['action'] == 'pool_off_target':
                mark = f'（不重写：{d.get("note", "")}）'
            print(f'    {r["rid"]}  {d["action"]:<14} {mark}  '
                  f'{d["criteria_before"]}→{d.get("criteria_after", d["criteria_before"])} 条')


if __name__ == '__main__':
    main()
