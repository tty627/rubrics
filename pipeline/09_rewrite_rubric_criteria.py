"""步骤 4b：缺陷准则重写 —— 消费 s11b_remedy 的待拆队列 + s04_rubric 的质量标记。

**为什么单独一步**：s11b_remedy 把 RIFT 判为 non-atomic 的准则落到 _defect_queue.jsonl
而不是删掉（删是错误处置，实测会把 gated_answer 的答案项删掉）。这一步把它们拆开。
同时处理 s04_rubric 的 flag() 打的五类质量标记 —— 这两件事的处理单元都是「一条准则」，
输出形状也都是「1-2 条替换准则」，所以合并成一步，省一轮遍历。

只对命中的准则调 LLM，不动干净的准则 —— 这是不全量重跑 s04_rubric 的关键。

两类任务：
  split   非原子 → 拆成 2 条，分值按重要性拆分，和不变
  rewrite 质量标记 → 原地重写成 1 条，分值不变
          _flag_no_groundtruth  只说「与标准答案一致」→ 把应然内容写出来
          _flag_cliff           全量复合悬崖 → 拆成「规则对不对」+「条目全不全」
          _flag_vague           空泛词无锚点 → 换成可核对的具体表述
          _flag_mention_only    提及即得分 → 改成检查「说得对不对」
          _flag_subjective_threshold  负项主观阈值 → 给出具体判定线或降级为通用错误类型

**两条硬约束**：
1. 分值守恒。拆出来的子准则分值之和 = 原准则分值，否则满分会漂。
2. 题目总量不超 N_MAX。拆分会涨条数，超预算时只拆分值最高的几条 —— 拆不完的
   保留原样并打 _split_skipped，不静默丢弃。

输入: s11b_remedied.jsonl + _defect_queue.jsonl
输出: s04b_split.jsonl（结构同 s11b_remedied，rubrics 已重写）
"""
import json, os, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage, dimensions, rubric

WORKERS = int(os.environ.get('RP_WORKERS', 14))
THINK = stage.envflag('RP_THINK', True)
SRC = os.environ.get('RP_S04LB_SRC', 's11b_remedied.jsonl')
QUEUE = os.environ.get('RP_S04LB_QUEUE', '_defect_queue.jsonl')
N_MAX = int(os.environ.get('RP_RUBRIC_MAX', 8))

FLAG_HINT = {
    '_flag_no_groundtruth':
        '这条只说了「与标准答案一致」之类的空壳，判分器手上没有标准答案，执行不了。'
        '请把**应然内容本身**写出来（具体数值/选项/结论）。'
        '如果你无法从题目和草稿准则中确定正确答案是什么，就把这条改写成一个'
        '你能确定的、可核对的要求，不要保留空壳表述。',
    '_flag_cliff':
        '这条占了满分的一半以上，而且是「全部/每一个都对」式的全量要求 —— '
        '实际是 0/1 判定，中间态没有分辨力。请拆成「规则/方法对不对」和'
        '「条目全不全」两条，分值按重要性分配。',
    '_flag_vague':
        '这条含空泛词（准确/完整/清晰/合理…）且没有可核对的锚点。'
        '请换成本题专属、判分器能直接核对的具体表述。',
    '_flag_mention_only':
        '这条只检查「提没提到」，不检查「说得对不对」。'
        '请改成对内容本身的判定。',
    '_flag_subjective_threshold':
        '这条扣分项用「严重/显著/根本性」当判定线，判分器无法一致执行。'
        '请给出具体的判定线；如果给不出具体线，就改写成一个不依赖程度判断的'
        '通用错误类型描述。',
    '_flag_topic_list':
        '这条是**话题清单**，不是内容判定 —— 动词（列举/描述/比较/分析）只要求'
        '「谈到这个话题」，不要求「说对」。实测证据：把好回答删掉 30% 关键内容、'
        '或换成「每点只提一句」的浅回答，这类准则照样判满分，rubric 因此可被钻空子。\n'
        '  请改成**对具体内容的判定**：把「提到 X」换成「关于 X 的哪个具体判断成立」，'
        '要求一个能判对错的陈述。\n'
        '  ❌ "正确描述所举推进方式的核心原理"\n'
        '  ✅ "指出冲压发动机无压气机和涡轮，增压靠进气道速度冲压"\n'
        '  自检：一份把话题提了一句但说得很浅的回答，能通过你新写的这条吗？'
        '能通过就还没改对。',
}

SYS_SPLIT = f'''你在修一条评分准则。它被诊断为**非原子** —— 一条里捆了多个能各自独立成立的判断点。

把它拆成 **2 条**独立准则。每条只测一件事，各自都能独立判定是/否。

【硬约束】
1. 两条的分值之和**必须等于**原准则分值。原分值只有 1 分时不要拆，
   返回 split=false 并原样保留。
2. 拆出来的每一条都必须是本题专属、判分器能直接核对的具体表述。
   禁止空泛词（准确/完整/清晰/合理/充分/全面）。
3. 不要引入题目没问的内容。拆分是把原准则切开，不是扩写。
4. 保持 is_positive 与原准则一致。

【拆不动就别拆】
如果拆开后有一半失去意义（比如题目问的就是 A 与 B 的区别，
拆成「说了A」「说了B」两条后就无法回答「区别是什么」），
返回 split=false —— 这说明诊断误判了，原准则其实是原子的。

**负向项（扣分项）不要拆**：扣分项分值只有 -2/-3，而扣分项不允许出现 -1，
拆成两条必然破坏分值下限或撑大扣分总量 —— 负向项一律返回 split=false。

{dimensions.prompt_block()}

只输出 JSON：
{{"split": true, "criteria": [
  {{"criteria": "不超过70字", "score": 2, "reason": "不超过30字", "dimension": "从上表选"}},
  {{"criteria": "...", "score": 1, "reason": "...", "dimension": "..."}}]}}
若判定不该拆：{{"split": false, "reason": "为什么它其实是原子的，不超过40字"}}'''

SYS_FACTFIX = f'''你在修一条评分准则。它被诊断为**事实错误** —— 准则里写死的答案、
数值、结论或引用是错的。这类准则危害最大：它会把正确回答判成错、错误回答判成对。

改写成 **1 条**，把内容改对。

【硬约束】
1. 分值不变。
2. **优先采信【人工草稿准则】和题干给出的条件**。这两个是真值来源，
   比你自己的记忆可靠。
3. 如果你无法确定正确内容是什么 —— 这是常见情况，不要硬猜 ——
   就把这条**降级成一个你能确定的、可核对的要求**。
   例：原「最终答案为 k=5」不确定正确值时，
       改成「给出 k 的具体数值并说明其满足最小性的依据」，
       而不是换一个你也没把握的数字。
   宁可弱一点，也绝不能写死一个新的错答案。
4. 不要引入题目没问的内容。禁止空泛词（准确/完整/清晰/合理/充分/全面）。
5. 保持 is_positive 与原准则一致。

{dimensions.prompt_block()}

只输出 JSON：
{{"criteria": [{{"criteria": "不超过70字", "score": 2, "reason": "不超过30字",
   "dimension": "从上表选"}}],
  "confident": true, "note": "若不确定正确值而做了降级处理，一句话说明"}}'''

SYS_REWRITE = f'''你在修一条评分准则。它有具体的质量缺陷，见下方【缺陷】。

原地改写成 **1 条**（除非【缺陷】里明确要求拆成 2 条）。

【硬约束】
1. 分值不变（拆成 2 条时，两条之和等于原分值）。
2. 必须是本题专属、判分器能直接核对的具体表述。
   禁止空泛词（准确/完整/清晰/合理/充分/全面/深入/有效）。
3. 不要引入题目没问的内容。
4. 保持 is_positive 与原准则一致。
5. **禁止编造**。不确定的数值、机理、条件依赖，宁可写得粗一点，也不要编。

{dimensions.prompt_block()}

只输出 JSON：
{{"criteria": [
  {{"criteria": "不超过70字", "score": 2, "reason": "不超过30字", "dimension": "从上表选"}}]}}'''


def ctx(r):
    """题目上下文，两个 prompt 共用。draft_rubric 是本题唯一的真值来源。"""
    q = (r.get('query_eff') or r['question'])[:1500]
    subj = ' / '.join(r.get('subject') or []) or '未标注'
    draft = r.get('draft_rubric') or {}
    gt = '\n'.join(f'- {c.get("criteria", "")}'
                   for c in (draft.get('rubrics') or [])[:8])
    s = (f'【学科】{subj}\n'
         f'【提问意图】{r.get("intent", "")}\n'
         f'【题型】{r.get("question_type", "")} → {r.get("rubric_form", "")}\n\n'
         f'【题目】\n{q}\n')
    if gt:
        s += (f'\n【人工草稿准则（含本题应然内容，可据此写出具体答案；'
              f'粒度不必照抄）】\n{gt}\n')
    return s


def build_split(r, c):
    u = (ctx(r) +
         f'\n【待拆准则】{"+" if c["is_positive"] else "−"}{abs(c["score"])} '
         f'[{c["dimension"]}] {c["criteria"]}\n'
         f'【诊断理由】{c.get("_diag_reason", "捆了多个独立判断点")}\n')
    if c.get('is_gate'):
        u += ('\n⚠️ 这是本题的**闸门项**（唯一正确答案的判据）。拆分后，'
              '承载最终答案的那一条必须保住绝大部分分值。\n')
    return [{'role': 'system', 'content': SYS_SPLIT}, {'role': 'user', 'content': u}]


def build_rewrite(r, c, flags):
    hints = '\n'.join(f'- {FLAG_HINT[f]}' for f in flags if f in FLAG_HINT)
    u = (ctx(r) +
         f'\n【待改准则】{"+" if c["is_positive"] else "−"}{abs(c["score"])} '
         f'[{c["dimension"]}] {c["criteria"]}\n'
         f'【缺陷】\n{hints}\n')
    return [{'role': 'system', 'content': SYS_REWRITE}, {'role': 'user', 'content': u}]


def build_factfix(r, c, q):
    u = (ctx(r) +
         f'\n【待改准则】{"+" if c["is_positive"] else "−"}{abs(c["score"])} '
         f'[{c["dimension"]}] {c["criteria"]}\n'
         f'【诊断认为错在哪】{q.get("diag_reason", "")}\n')
    cv = (q.get('correct_value') or '').strip()
    if cv:
        u += f'【诊断给出的正确内容】{cv}\n'
    if q.get('needs_review'):
        u += ('\n⚠️ 诊断器是**凭自身知识**下的这个结论，没有真值依据，它可能记错。\n'
              '请优先核对上方【人工草稿准则】和题干。若草稿/题干支持不了诊断的说法，'
              '就按第 3 条做降级处理，不要照搬诊断给的值。\n')
    else:
        u += f'\n（诊断依据：{q.get("basis", "")}，比模型记忆可靠）\n'
    if q.get('is_gate'):
        u += '\n⚠️ 这是本题的**闸门项**（唯一正确答案的判据），必须保留答案判定的能力。\n'
    return [{'role': 'system', 'content': SYS_FACTFIX}, {'role': 'user', 'content': u}]


def norm(items, orig, n_want=None):
    """规整模型返回的替换准则：分值守恒 + 维度收敛 + 继承血缘。"""
    out = []
    for c in items[:2]:
        if not isinstance(c, dict):
            continue
        txt = str(c.get('criteria', '')).strip()
        if not txt:
            continue
        dim, hit = dimensions.normalize(c.get('dimension') or orig['dimension'])
        try:
            sc = abs(int(round(float(c.get('score', 1))))) or 1
        except (TypeError, ValueError):
            sc = 1
        out.append({'criteria': txt[:200], 'score': sc,
                    'reason': str(c.get('reason', ''))[:100],
                    'dimension': dim, 'is_positive': orig['is_positive'],
                    '_dim_from_table': hit})
    if not out:
        return []
    if n_want:
        out = out[:n_want]

    # 分值守恒：按模型给的比例重新分配原分值。
    # 正向每条至少 1 分；负向每条至少 2 分 —— prompt 口径「扣分项一律 -2 或 -3」，
    # 不允许拆出 -1 槽位（旧实现曾把 -2/-3 拆出 63 条 -1 流进交付档）。
    # 负向 -2/-3 拆两条保不住 2 分下限 → 返回 []，保留原条不拆。
    target = abs(orig['score'])
    if len(out) == 1:
        out[0]['score'] = target
    else:
        floor = 2 if not orig['is_positive'] else 1
        if target < floor * len(out):
            return []
        tot = sum(c['score'] for c in out) or len(out)
        alloc = [max(floor, round(target * c['score'] / tot)) for c in out]
        # 四舍五入后不等于 target：从模型给分最大的那条开始逐分补齐，不低于下限
        diff = target - sum(alloc)
        order = sorted(range(len(alloc)), key=lambda i: -out[i]['score'])
        for i in order:
            step = 1 if diff > 0 else -1
            while diff and alloc[i] + step >= floor:
                alloc[i] += step
                diff -= step
        if diff:          # 补不齐（target 太小拆不开）→ 不拆
            return []
        for c, a in zip(out, alloc):
            c['score'] = a

    for i, c in enumerate(out, 1):
        if not orig['is_positive']:
            c['score'] = -c['score']
        c['_criterion_id'] = f'{orig["_criterion_id"]}s{i}' if len(out) > 1 \
            else orig['_criterion_id']
        c['_perspective_ids'] = orig.get('_perspective_ids', [])
        c['_scenario_ids'] = orig.get('_scenario_ids', [])
        c['_rewritten_from'] = orig['_criterion_id']
    return out


def main():
    m = stage.pick('RP_M_GEN', 'generator')
    recs = stage.read_jsonl(SRC)
    try:
        queue = stage.read_jsonl(QUEUE)
    except FileNotFoundError:
        queue = []
    by_rid = {r['rid']: r for r in recs}
    split_cids = defaultdict(dict)
    for q in queue:
        split_cids[q['rid']][q['_criterion_id']] = q

    # 摊平成任务：(rid, cid, 'split'|'rewrite', flags)
    jobs, skipped = [], []
    for r in recs:
        rubrics = r.get('rubrics') or []
        room = N_MAX - len(rubrics)          # 还能再涨几条
        cand = []
        for c in rubrics:
            cid = c.get('_criterion_id')
            flags = [k for k in FLAG_HINT if c.get(k)]
            q = split_cids[r['rid']].get(cid)
            if q:
                # 队列里的 kind：factual 原地改写（不涨条数），split 拆成 2 条
                cand.append((c, q.get('kind', 'split'), flags))
            elif flags:
                cand.append((c, 'rewrite', flags))
        # 拆分会涨条数，超预算时优先拆分值高的
        cand.sort(key=lambda x: -abs(x[0]['score']))
        for c, kind, flags in cand:
            grows = kind == 'split' or (kind == 'rewrite' and '_flag_cliff' in flags)
            if grows and room <= 0:
                skipped.append((r['rid'], c['_criterion_id']))
                c['_split_skipped'] = True
                continue
            if grows:
                room -= 1
            jobs.append((r['rid'], c['_criterion_id'], kind, flags))

    print(f'步骤 4b 缺陷重写: {len(recs)} 题, 模型={m.name}')
    print(f'  待拆队列   : {len(queue)} 条')
    print(f'  任务数     : {len(jobs)}  '
          f'(split={sum(1 for j in jobs if j[2] == "split")}, '
          f'rewrite={sum(1 for j in jobs if j[2] == "rewrite")})')
    if skipped:
        print(f'  ⚠️  超预算跳过: {len(skipped)} 条（题目已达 {N_MAX} 条上限，保留原样）')

    cid2c = {c['_criterion_id']: c for r in recs for c in (r.get('rubrics') or [])
             if c.get('_criterion_id')}

    def one(job):
        rid, cid, kind, flags = job
        r, c = by_rid[rid], cid2c[cid]
        q = split_cids[rid].get(cid) or {}

        if kind == 'factual':
            obj, _ = stage.json_call(m, build_factfix(r, c, q),
                                     stage='s04Lb', thinking=THINK)
            new = norm(obj.get('criteria') or [], c, 1)
            for x in new:
                x['_factfix'] = True
                x['_factfix_confident'] = bool(obj.get('confident', True))
                if obj.get('note'):
                    x['_factfix_note'] = str(obj['note'])[:120]
                if q.get('needs_review'):
                    x['_needs_review'] = True
            return cid, new, ''

        if kind == 'split':
            c = {**c, '_diag_reason': q.get('diag_reason') or
                 (q.get('failure_modes') or [''])[0],
                 'is_gate': q.get('is_gate')}
            obj, _ = stage.json_call(m, build_split(r, c), stage='s04Lb', thinking=THINK)
            if not obj.get('split'):
                return cid, None, obj.get('reason', '')          # 诊断误判，保留原条
            return cid, norm(obj.get('criteria') or [], c), ''

        obj, _ = stage.json_call(m, build_rewrite(r, c, flags), stage='s04Lb', thinking=THINK)
        n_want = 2 if '_flag_cliff' in flags else 1
        return cid, norm(obj.get('criteria') or [], c, n_want), ''

    done, errs = stage.run(one, jobs, workers=WORKERS, desc='s04Lb')
    repl = {cid: new for cid, new, _ in done if new}
    kept = [cid for cid, new, _ in done if new is None]

    res, n_new = [], 0
    for r in recs:
        out = []
        for c in r.get('rubrics') or []:
            new = repl.get(c.get('_criterion_id'))
            if new:
                out.extend(new)
                n_new += len(new) - 1
            else:
                out.append(c)
        pos = rubric.positives(out)
        res.append({**r, 'rubrics': out, 'core_n': len(out),
                    'core_n_positive': len(pos),
                    's_max': rubric.s_max(out)})
    stage.write_jsonl('s04b_split.jsonl', res)

    n_before = sum(len(r.get('rubrics') or []) for r in recs)
    n_after = sum(len(r['rubrics']) for r in res)
    print(f'\n=== 步骤 4b 结果 ===')
    if errs:
        print(f'  失败        : {len(errs)} 条')
    print(f'  重写成功    : {len(repl)} 条 → {len(repl) + n_new} 条')
    print(f'  诊断误判    : {len(kept)} 条（模型判定其实是原子的，保留原样）')
    print(f'  准则总数    : {n_before} → {n_after}')
    per = [len(r['rubrics']) for r in res]
    print(f'  准则/题     : min={min(per)} p50={sorted(per)[len(per) // 2]} '
          f'max={max(per)} mean={n_after / len(res):.1f}')
    over = [r['rid'] for r in res if len(r['rubrics']) > N_MAX]
    print(f'  超 {N_MAX} 条的题 : {len(over)} {over[:6] or ""}')

    sm_b = sum(r.get('s_max', 0) for r in recs)
    sm_a = sum(r['s_max'] for r in res)
    print(f'  总满分      : {sm_b} → {sm_a}  '
          f'{"✓ 守恒" if sm_a == sm_b else "⚠️ 漂了 %+d" % (sm_a - sm_b)}')

    dc = Counter(c['dimension'] for r in res for c in r['rubrics'])
    print(f'  唯一维度数  : {len(dc)}')

    ex = next((r for r in res if any(c.get('_rewritten_from') for c in r['rubrics'])), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]} (满分 {ex["s_max"]}):')
        for c in ex['rubrics']:
            mark = ' ←改' if c.get('_rewritten_from') else ''
            sign = '+' if c['is_positive'] else '−'
            print(f'    {sign}{abs(c["score"])} [{c["dimension"]}] {c["criteria"][:52]}{mark}')


if __name__ == '__main__':
    main()
