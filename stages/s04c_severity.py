"""步骤 4c：负项严重性分级 + veto 标记 —— 补偿式总分上的合取门。

流程位置：s04b_split 之后、判分（s12_judge）之前。消费交付源 data/s04b_split.jsonl，
给每条负向扣分项打 severity ∈ {principle, major, minor} 与 is_veto（bool），
输出 data/s04c_severity.jsonl。48 题试点链（s10_pool48）消费的正是
s04b_split，这一步跑一遍，交付档与 Phase C 验收链同时覆盖。

**为什么单独一步**（2026-08-14，导师反馈「负向扣分不够，触犯原则性错误的
回答仍拿高分」）：
  实测交付档 452 题负项 614 条（-3: 397、-2: 154、-1: 63），全部负项之和只占
  满分 34.3%（最坏 75%）——正向全中、同时踩满所有原则性错误的回答平均仍拿 66%。
  解法是**补偿式总分 + 指定 critical criteria 合取门**（Qwen Rubric Anchors 的
  veto + 教育测量 conjunctive/compensatory 混合模型）：is_veto 项一旦被判定
  成立，整题得分率归 0，不进补偿式求和。文献硬要求：veto 项必须**原子**且
  **规则显式声明**，不能藏在权重里——所以 veto 是独立字段（is_veto），
  与 is_gate（正向答案阀门）方向相反，判分侧必须能区分。

**只动负项**：正向准则与记录级字段逐字不变。无负项的记录整行原样写出，
改完可 diff 验证正向项零变动。

**veto 门槛（prompt 写清楚 + 代码兜底）**：
  1. 原子：单一错误，不含「且/或」串接（VETO_ATOMIC_BREAK 兜底）
  2. 判据是「触犯即整题不合格」，不是「扣分较多」→ 只有 principle 级可 veto
  3. 判定线清晰：复用 s04_rubric 的 SUBJ_DEG/ANCHOR，主观程度词不给 veto
  4. gated_answer 的「答案答错」是天然 veto 候选（s12_judge 有 lib/answer_check.py
     程序化核验兜底，可靠性最高）
  代码只做**否决**（拦下 LLM 给的 veto），不做**追加**（不替 LLM 新造 veto）。

模型：RP_M_S04LC（默认 judge 角色）。
"""
import json, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage, rubric
from stages.s04_rubric import SUBJ_DEG, ANCHOR

WORKERS = int(os.environ.get('RP_WORKERS', 14))
THINK = stage.envflag('RP_THINK', True)
SRC = os.environ.get('RP_S04LC_SRC', 's04b_split.jsonl')
OUT = os.environ.get('RP_S04LC_OUT', 's04c_severity.jsonl')

# 原子性兜底：串接多个错误的连接词（RIFT non-atomic 的「且」口径 + 「或」）。
# 一条里捆了多个独立错误，判分器无法对整条给出一致的成立/不成立，不能当 0/1 闸门。
VETO_ATOMIC_BREAK = re.compile(r'且|或|并且|或者|同时|以及')

SYS = f'''你是评分准则质量评审员。给定一道题和它的一条**负向扣分项**（描述「不该出现的错误」），
你要做两件事：判定这条错误的严重性等级 severity，并判定它能否作为「一票否决（veto）」项。

【severity 分级】
- principle（原则性错误）：**触犯即整题不合格**。核心结论答反、核心概念或方法用错、
  把标准答案写错、违反安全/合规红线。这类错误一旦出现，整份回答不再有任何价值，
  不管其它部分写得多好。
- major（较严重）：明显扣分、但不至于整题作废的错误。
- minor（轻微）：局部小瑕疵、表述不严谨。

【veto 门槛 —— 必须同时满足以下全部条件，才给 is_veto=true】
1. **原子**：这条只描述一个单一错误，不含「且/或/并且/同时」串接多个错误。
2. **判据是「触犯即整题不合格」**，不是「扣分较多」。只有 principle 级错误能 veto
   （major/minor 一律 is_veto=false）。
3. **判定线清晰**：不含「严重/显著/明显/大幅/根本性」等主观程度词——这类措辞
   没有可一致执行的判定线，不能当 0/1 闸门。
4. gated_answer 题的「答案答错」类扣分项是最可靠的 veto 候选（判分时有程序化
   答案核验兜底）。若它满足前三条，应当给 is_veto=true。

veto 的语义（文献硬要求：规则必须显式声明）：
**任一 is_veto 项被判定成立 → 整题得分率为 0，不进补偿式求和。**

只输出 JSON：
{{"severity": "principle", "is_veto": true, "reason": "不超过40字"}}
severity 只能取 principle / major / minor 之一。'''


def build(r, c):
    q = (r.get('query_eff') or r.get('question', ''))[:800]
    gated = r.get('rubric_form') == 'gated_answer'
    u = (f'【题目】\n{q}\n\n'
         f'【题型】{r.get("question_type", "")} → {r.get("rubric_form", "")}\n'
         f'【待评扣分项】−{abs(c.get("score", 0))} '
         f'[{c.get("dimension", "")}] {c.get("criteria", "")}\n')
    if gated:
        ans = (r.get('answer_canonical') or r.get('answer') or '').strip()
        u += (f'⚠️ 本题是 gated_answer（有唯一正确答案），标准答案是：'
              f'{ans[:200] if ans else "（未抽取，但题型本身有唯一答案）"}\n')
    return [{'role': 'system', 'content': SYS}, {'role': 'user', 'content': u}]


def grade(c, obj):
    """把模型输出套上代码兜底。兜底只否决 LLM 给的 veto，不追加新 veto。"""
    text = c.get('criteria', '')
    sev = (str(obj.get('severity', '')).strip()
           if isinstance(obj.get('severity'), str) else '')
    ok_sev = sev in rubric.SEVERITY_LEVELS
    veto = bool(obj.get('is_veto'))
    block = []

    if veto:
        if VETO_ATOMIC_BREAK.search(text):
            veto = False
            block.append('non_atomic')
        elif SUBJ_DEG.search(text) and not ANCHOR.search(text):
            veto = False
            block.append('subjective_threshold')
        elif not ok_sev:
            veto = False
            block.append('severity_missing')
        elif sev != 'principle':
            veto = False
            block.append('not_principle')

    added = {'severity': sev if ok_sev else None, 'is_veto': veto,
             '_s04Lc_reason': str(obj.get('reason', ''))[:120]}
    if not ok_sev:
        # LLM 没给有效分级：按分值兜底（-3→principle、-2→major、其余 minor）。
        # 兜底一律不给 veto —— veto 必须由模型显式确认，宁可漏也不误杀。
        sc = c.get('score', 0)
        added['severity'] = ('principle' if sc <= -3 else
                             ('major' if sc == -2 else 'minor'))
        added['_s04Lc_fallback'] = True
        added['is_veto'] = False
    if block:
        added['_veto_block'] = ','.join(block)
    return added


def main():
    m = stage.pick('RP_M_S04LC', 'judge')
    p = SRC if os.path.isabs(SRC) else os.path.join(stage.DATA, SRC)
    with open(p, encoding='utf-8') as f:
        raw_lines = [l.rstrip('\n') for l in f if l.strip()]
    recs = [json.loads(l) for l in raw_lines]

    jobs = []
    for i, r in enumerate(recs):
        for j, c in enumerate(r.get('rubrics') or []):
            if not rubric.is_positive(c):
                jobs.append((i, j))

    print(f'步骤 4c 负项严重性分级: {len(recs)} 题, 源={SRC}')
    print(f'  分级模型={m.name} (family={m.family})')
    print(f'  负向准则: {len(jobs)} 条')

    def one(job):
        i, j = job
        r, c = recs[i], recs[i]['rubrics'][j]
        obj, _ = stage.json_call(m, build(r, c), stage='s04Lc', thinking=THINK)
        return i, j, grade(c, obj)

    done, errs = stage.run(one, jobs, workers=WORKERS, desc='s04Lc')
    by_key = {(i, j): g for i, j, g in done}

    # 失败重试一次；仍失败走兜底（记 _s04Lc_failed）
    retry = [(i, j) for (i, j) in jobs if (i, j) not in by_key]
    if retry:
        print(f'  失败重试: {len(retry)} 条')
        done2, _ = stage.run(one, retry, workers=WORKERS, desc='s04Lc-retry')
        for i, j, g in done2:
            by_key[(i, j)] = g

    failed = [(i, j) for (i, j) in jobs if (i, j) not in by_key]
    for i, j in failed:
        c = recs[i]['rubrics'][j]
        sc = c.get('score', 0)
        by_key[(i, j)] = {'severity': ('principle' if sc <= -3 else
                                       ('major' if sc == -2 else 'minor')),
                          'is_veto': False, '_s04Lc_failed': True,
                          '_s04Lc_reason': ''}

    # 写回：无负项的记录整行原样；有负项的重建 rubrics（只给负项加字段）。
    out_path = OUT if os.path.isabs(OUT) else os.path.join(stage.DATA, OUT)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    sev_stat, block_stat, veto_out, fb = Counter(), Counter(), [], 0
    with open(out_path, 'w', encoding='utf-8') as f:
        for i, line in enumerate(raw_lines):
            neg_idx = [j for (ii, j) in jobs if ii == i]
            if not neg_idx:
                f.write(line + '\n')
                continue
            r, rub = recs[i], list(recs[i]['rubrics'])
            for j in neg_idx:
                c = dict(rub[j])
                g = by_key[(i, j)]
                for k, v in g.items():
                    c[k] = v
                rub[j] = c
                sev_stat[c['severity']] += 1
                if c['is_veto']:
                    veto_out.append((r['rid'], c))
                if c.get('_veto_block'):
                    block_stat[tuple(c['_veto_block'].split(','))] += 1
                if c.get('_s04Lc_fallback'):
                    fb += 1
            out = dict(r)
            out['rubrics'] = rub
            f.write(json.dumps(out, ensure_ascii=False) + '\n')
    print(f'写出 {out_path}')

    print(f'\n=== 步骤 4c 结果 ===')
    print(f'  严重性分布 : ' + '  '.join(f'{k}={sev_stat[k]}' for k in rubric.SEVERITY_LEVELS))
    print(f'  is_veto    : {len(veto_out)} 条')
    if block_stat:
        print(f'  veto 被代码否决: ' + '  '.join(
            f'{"+".join(k)}={v}' for k, v in block_stat.most_common()))
    if fb:
        print(f'  兜底(LLM 未给有效分级): {fb} 条  调用失败: {len(failed)} 条')
    print(f'\n  veto 样例（前 12 条）:')
    for rid, c in veto_out[:12]:
        print(f'    [{rid}] {c["severity"]:>9} −{abs(c["score"])} '
              f'{c["criteria"][:58]}')


if __name__ == '__main__':
    main()
