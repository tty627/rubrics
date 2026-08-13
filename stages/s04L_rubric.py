"""步骤 4L：准则直出（lean 版 s04）—— 一次调用产出全题最终 rubric。

与原 s04 的根本差别：**从「每视角展开 1-3 条」改为「全题一次出 6-8 条」**。

原流程的膨胀链（实测）：
    3.2 场景 → 17.8 视角 → 25.3 准则 → 30.5 最终
病根是每一环都在做加法：R_w 净增 61% 视角，s04 每视角再 ×1.42，
s07 加 R_dist、s08 加惩罚项。而 §2.5 给 analytic 的目标是 5-8 条。

这一步用「全题预算制」取代「逐视角展开」：把视角当作**覆盖提示**而非
展开单元，让模型在 6-8 条的总预算内自己决定哪些视角值得成为准则。
预算是硬约束，模型必须做取舍——这才是收敛的来源。

同时吸收两个下游职责，故 s07/s08/s09 在 lean 流程中跳过：
  - s08 惩罚项 → 本步直接出 1-2 条 is_positive=false
  - s09 归一化 → 不再需要：score 存原始整数，满分 = sum(正向 score)，
    归一化延后到判分时算得分率（跨题可比性由得分率保证）

输出即导师指定 schema（criteria/score/reason/dimension/is_positive），
内部字段 `_` 前缀保留血缘（设计文档硬约束第 4 条，步骤 13/14 依赖）。
"""
import json, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage, dimensions

WORKERS = int(os.environ.get('RP_WORKERS', 14))
THINK = stage.envflag('RP_THINK', True)
SRC = os.environ.get('RP_S04L_SRC', 's03_perspective_lean.jsonl')
N_MIN = int(os.environ.get('RP_RUBRIC_MIN', 6))
N_MAX = int(os.environ.get('RP_RUBRIC_MAX', 8))

SYS = f'''你在为一道题写评分标准（rubric），用于判断一份回答是否合格。

【总量硬约束】{N_MIN}-{N_MAX} 条（含扣分项）。这是预算，不是建议。
题目简单可以更少，但绝不能超。超了说明你在写细节加分项而不是基本要求。

【每条准则的判据】
写之前先问：「一份回答如果漏了这条，它还算合格吗？」
- 漏了就不合格 → 写
- 漏了仍合格 → 不写（这是加分项，不属于基本要求）

【六条禁止】
1. **禁止写不确定的机理断言**。只写该领域教科书级共识。
   涉及具体机制、数值、条件依赖的结论，若题目未给出前提，不要写。
   反例：「指出高温下漆酚氧化交联成膜导致不溶不熔」——题目没给温度气氛，
   这个因果不成立。
2. **禁止超出题目范围**。题目没问的不写。
   反例：题目问「合同负债和应付账款」，却要求解释「预付款项」。
3. **禁止空泛词**。不能是「回答准确」「解释完整」「逻辑清晰」「结论准确」「答案正确」。
   必须是本题专属、判分器能直接核对的内容。

   反例（空泛）：
   ❌ "结论表述清晰，无歧义或模棱两可"
   ❌ "答案准确"
   ❌ "完整列出全部六十四卦"

   正例（具体）：
   ✅ "明确指出合同负债属于负债类科目，而非资产"
   ✅ "最终答案为7cm"

4. **禁止写「与标准答案一致」这类空壳**。判分器手上没有标准答案，执行不了。
   准则里必须把**应然内容本身**写出来。
   ❌ "最终答案与标准答案一致"        ← 判分器无从核对
   ✅ "最终答案为 λ=√2"
   若【锚点】里给了本题答案，直接用它；没给且你不确定，就改写一条你能确定的要求。

5. **禁止「全部/每一个都对」压进一条高分准则**。那是 0/1 判定，中间态没有分辨力。
   ❌ "列出全部64卦名称，无遗漏"（一条 8 分）
   ✅ 拆两条："八经卦二进制编码正确（乾111、坤000等）" + "条目数达到64条"

6. **禁止「提到就给分」**。准则要检查**说得对不对**，不是**提没提**。
   ❌ "提及显存占用的构成"            ← 出现"显存"二字就得分
   ✅ "显存构成的拆分中包含优化器状态与参考模型副本两项"

【最容易犯的错：写成「话题清单」而不是「内容判定」】
这是实测中最严重的问题。用回复池做过验证：把一份好回答**删掉 30% 的关键内容**，
用下面这类准则去判，**得分仍是满分**；让模型造一份「每个方面都提一句、
但都没有实质内容」的回答，也是满分。原因是这些准则的动词只要求「提到」。

  ❌ "列举至少2种无桨叶推进方式"          ← 出现两个名词就过
  ❌ "正确描述所举方式的核心原理"          ← "描述"没有判定门槛
  ❌ "按速度范围比较不同方式的适用性"       ← 提一句"低速不能工作"就过
  ❌ "提及噪声、振动等实际使用限制"        ← 罗列缺点词就过

**改法：把「提到 X」换成「关于 X 的哪个具体判断成立」**，要求一个能对错的陈述。

  ✅ "指出冲压发动机无压气机和涡轮，增压靠进气道速度冲压"
     （不是"描述原理"，而是指定了必须说对的两个事实）
  ✅ "指出冲压发动机静止时无法工作，需其他动力先加速到高速"
     （不是"比较适用性"，而是指定了一个可判对错的结论）

【自检】写完每条准则，问自己：
  「一份把这个话题提了一句、但说得很浅或说错了的回答，能通过这条吗？」
  能通过 → 这条是话题清单，重写成对具体内容的判定。

【但也不要走到另一个极端：一条准则只设**一道**门槛】
把多个要求串成一条长句，等于要求回答逐字按你设想的方式组织，
一份内容完全正确、只是表述顺序或用词不同的好回答会被判不合格。
实测：这类准则会让强回答的得分率掉到 20% 以下，rubric 失去可用性。

  ❌ "指出界面氧化层、水合层和微间隙**共同**引起反射散射，**并**降低入射光通量和QE"
     ← 回答分别讲了氧化层、水残留、微空洞的影响，但没按「共同归因」这个说法组织，
       就被判不满足。它要求的是**行文方式**，不是内容。
  ✅ "指出界面氧化层或微间隙会引起额外反射散射，降低 QE"
     （核心判断点保留，不强求它按某种方式归纳）

  ❌ "给出GaAs热膨胀系数约6×10⁻⁶/K与玻璃约0.5～3×10⁻⁶/K的差异，指出热失配应力
      改变能带并诱发缺陷、缩短少数载流子扩散长度"
     ← 一条里塞了：两个数值 + 应力改变能带 + 诱发缺陷 + 缩短扩散长度，共 5 个门槛
  ✅ "指出 GaAs 与玻璃热膨胀系数不匹配会引入热应力，损伤界面或降低 QE"

判断方法：数一数这条准则里有几个「必须同时成立」的点。
**超过 2 个就要拆开或放宽** —— 否则它测的是运气，不是质量。

【可达性校准】
写完整份 rubric，设想一份该领域**专家写的好回答**：它应当能拿到 80% 以上的分。
如果你觉得它也会丢掉一半分数，说明准则过严 —— 回头把那些要求「面面俱到」
或「必须这样表述」的条目放宽。
rubric 是用来区分好坏的，不是用来为难所有人的。

允许的动词：指出 / 说明 / 得出 / 计算出 / 区分（后面必须跟**具体的应然内容**）
高危动词：列举、描述、涉及、提及、包含、覆盖、比较、分析（几乎必然写成话题清单）

【一条只测一件事】
不要用「且」「并」把两个独立判断点捆在一条里——判分器无法对捆绑准则
给出一致的是/否。
  反例：「指出合同负债对应履约义务，应付账款对应付款义务」（捆了两个）
  可选做法：合并成一条粗粒度表述「明确区分两者的义务性质」，
            或只保留更核心的那一个

【扣分项】1-2 条，只写**真正致命**的错误（结论答反、核心概念用错）。
直接写错误现象本身，is_positive 填 false。

⚠️ **扣分项禁止编造具体细节**：
- 如果题目有参考错误回复，可基于真实错误写具体准则
- 如果没有参考错误，只写**通用错误类型**，不要编造具体数值/字节/参数

  错误示例（verifiable题无参考错误时）：
  ❌ "将十六进制'52'错误解码为字符'S'"  ← 编造了具体字节和错误结果
  ✅ "解码结果与标准答案不一致，存在字符转换错误"  ← 通用描述

  错误示例（开放题无参考错误时）：
  ❌ "将DNA复制速度错误写为500bp/s"  ← 编造了具体错误数值
  ✅ "对DNA复制速度的数量级判断错误（如比实际快/慢10倍以上）"  ← 通用范围

{dimensions.prompt_block()}

【分值规则】按题型不同：
- 有唯一正确答案的题（gated_answer）：答案正确性那条给 6-8 分，
  其余支撑项各 1 分。这类题本质是「答对没答对」，均分会稀释主准则。
- 开放题：核心结论 3 分，重要支撑 2 分，一般要点 1 分。
- 扣分项一律 -2 或 -3 分。

只输出 JSON：
{{"rubrics": [{{"criteria": "不超过70字", "score": 2, "reason": "为什么这条是基本要求，不超过30字", "dimension": "从上表选", "is_positive": true, "perspective_ids": ["q0001-p1"]}}]}}
perspective_ids 填这条准则覆盖了哪些视角的 id（可空、可多个），用于追溯血缘。'''


def build(r):
    q = (r.get('query_eff') or r['question'])[:2000]
    subj = ' / '.join(r.get('subject') or []) or '未标注'
    form = r.get('rubric_form', '')

    persp = '\n'.join(
        f'- [{p["perspective_id"]}] {p["name"]}：{p.get("desc", "")[:80]}'
        for p in r.get('perspectives', []))

    gate = ('  ⚠有唯一正确答案：答案正确性给 6-8 分，其余各 1 分'
            if form == 'gated_answer' else '')

    # 检查是否有参考错误回复（用于指导扣分项的具体程度）
    ref_errs = r.get('ref_errors', [])
    err_hint = ''
    if ref_errs and len(ref_errs) > 0:
        err_hint = f'\n\n【参考错误】本题有 {len(ref_errs)} 条错误回复可参考，扣分项可基于真实错误模式编写。'
    else:
        err_hint = '\n\n【⚠️ 无参考错误】本题无错误回复样本，扣分项必须写通用错误类型，禁止编造具体数值/字节/参数。'

    user = (f'【学科】{subj}\n'
            f'【提问意图】{r.get("intent", "")}\n'
            f'【题型】{r.get("question_type", "")} → {form}{gate}\n\n'
            f'【题目】\n{q}\n\n'
            f'【候选评价轴（供参考，不必每条都变成准则）】\n{persp or "（无）"}'
            f'{err_hint}'
            f'{anchor_block(r)}')

    return [{'role': 'system', 'content': SYS},
            {'role': 'user', 'content': user}]


def anchor_block(r):
    """步骤 5L 的锚点。没跑 s05L 时返回空串，行为与之前完全一致（缓存不失效）。

    锚点是治事实错误的根本手段：全量诊断抓到 286 条「准则里写死的答案是编的」，
    根因就是模型只看题目、凭记忆写具体数值。有锚之后，具体内容有实际文本可依。
    """
    anchors = r.get('anchors') or []
    ans = (r.get('answer') or '').strip()
    gaps = r.get('gaps') or []
    if not (anchors or ans or gaps):
        return ''

    s = ['\n\n【锚点：一份实际回答里可核对的内容点】',
         '写具体数值/名称/结论时**以锚点为准**，不要凭记忆另写一个。']
    ok = [a for a in anchors if a.get('sound')]
    bad = [a for a in anchors if not a.get('sound')]
    for a in ok:
        s.append(f'  · {a["point"]}')
    if bad:
        s.append('  ⚠️ 以下锚点存疑，**不要**据此写准则：')
        for a in bad:
            s.append(f'  · {a["point"]}（疑点：{a.get("note", "")}）')
    if ans:
        sound = r.get('answer_sound', True)
        if sound:
            s.append(f'\n【本题的答案】{ans}')
            s.append('写答案类准则时直接用它，禁止写「与标准答案一致」这种空壳。')
        else:
            s.append(f'\n【本题答案（存疑，勿写死）】{ans}')
            s.append('这个答案不可靠，答案类准则请写成可核对的要求，不要写死数值。')
    if gaps:
        s.append('\n【锚定回答没覆盖但该答的方面】——这些正是 rubric 的价值所在，'
                 '优先考虑写成准则：')
        for g in gaps:
            s.append(f'  · {g}')
    return '\n'.join(s)


# ---- 程序化护栏（2026-08-13 加）----------------------------------------
# prompt 里已经禁了这几类写法，但实测仍有残留（空泛词 17.3% 题、空壳答案 35 条、
# 全量复合悬崖 72 条、提及即得分 29.6% 正项）。模型管不住的用代码兜：
# 这里只打标不删，交给 s04Lb_split 按标重写，避免全量 452 题重跑。
VAGUE = ('准确', '完整', '清晰', '合理', '充分', '恰当', '适当', '全面',
         '深入', '有效', '规范', '严谨', '系统性', '条理', '详细')
# 「可核验锚点」：数字、拉丁串、公式符号、引号包裹的字面量
ANCHOR = re.compile(r'[0-9A-Za-z=＝≈±<>≤≥$]|["“”]')
# 只引用「标准答案」却没写出答案本身
REF_ONLY = re.compile(r'与?\s*(标准|参考)答案\s*(一致|不一致|相符|不符)|标准答案')
# 全量复合：一条里要求「全部/所有/每一个都对」
BULK = re.compile(r'全部|所有|每一?[卦条项个]|无遗漏|完全一致')
# 提及即得分：动词开头、只问「提没提」
MENTION = re.compile(r'^(提及|提到|涉及|包含)')
# 话题清单型：动词只要求「谈到这个话题」，不要求说对。
# 实测（回复池 + 判分）：这类准则在「删掉30%关键内容」和「每点一句话」的
# 对抗回复上全部判过，是 Hackable 的主要来源。放在句首或「至少N种」句式里最典型。
TOPIC_VERB = re.compile(r'^(列举|列出|描述|比较|分析|涉及|覆盖|说明了)'
                        r'|至少\s*\d+\s*[种个条项]'
                        r'|^正确描述|^简要说明')
# 扣分项的主观阈值词
SUBJ_DEG = re.compile(r'严重|显著|根本性|明显|大幅|过度')


def flag(final, s_max):
    """给准则打质量标记。改标记不改内容，下游 s04Lb_split 据此重写。"""
    for c in final:
        t = c['criteria']
        anchored = bool(ANCHOR.search(t))

        # ① 空泛词且无可核验锚点
        if any(w in t for w in VAGUE) and not anchored:
            c['_flag_vague'] = True
        # ② 只说「与标准答案一致」，判分器手上没有标准答案，无法执行。
        #    不看 anchored —— 只要判定动作本身挂在「标准答案」上就得改，哪怕句子里
        #    另有数字。实测 q0008 的「每卦二进制码与标准答案完全一致，规则为从下到上
        #    阳爻为1阴爻为0」带着 1/0 命中了 ANCHOR 而漏过，但它要判的仍是
        #    「和标准答案一不一样」，判分器照样执行不了。
        if REF_ONLY.search(t):
            c['_flag_no_groundtruth'] = True
        # ③ 分数悬崖：单条占满分 ≥50% 且本身是全量/复合要求 → 实为 0/1 判定
        if (c['is_positive'] and s_max and c['score'] / s_max >= 0.5
                and (BULK.search(t) or '且' in t)):
            c['_flag_cliff'] = True
        # ④ 提及即得分：只校验关键词出现，不校验说得对不对。
        #    限短句 —— 长句多半已经写了具体主张（哪怕全中文没命中 ANCHOR）；
        #    带「全部/无遗漏」的是完备性检查，可核验，不算这一类。
        if (c['is_positive'] and MENTION.match(t) and not anchored
                and len(t) <= 20 and not BULK.search(t)):
            c['_flag_mention_only'] = True
        # ⑤ 扣分项用主观程度词当判定线
        if not c['is_positive'] and SUBJ_DEG.search(t) and not anchored:
            c['_flag_subjective_threshold'] = True
        # ⑥ 话题清单型：动词只要求「谈到」不要求「说对」。
        #    这是 Hackable 的主要来源 —— 实测删掉 30% 关键内容仍判满分。
        if c['is_positive'] and TOPIC_VERB.search(t):
            c['_flag_topic_list'] = True
    return final


def parse(r, raw):
    """把模型输出规整成交付 schema + 内部血缘字段。"""
    is_gate = r.get('rubric_form') == 'gated_answer'
    valid_pids = {p['perspective_id'] for p in r.get('perspectives', [])}
    pid2sid = {p['perspective_id']: p.get('scenario_id', '')
               for p in r.get('perspectives', [])}

    out = []
    for j, c in enumerate(raw[:N_MAX + 2], 1):     # 容忍略微超量，后面按分值截断
        if not isinstance(c, dict):
            continue
        txt = str(c.get('criteria', '')).strip()
        if not txt:
            continue

        pos = c.get('is_positive')
        pos = True if pos is None else bool(pos)
        dim, hit = dimensions.normalize(c.get('dimension'))

        try:
            sc = abs(int(round(float(c.get('score', 2))))) or 2
        except (TypeError, ValueError):
            sc = 2
        if pos:
            sc = min(sc, 8 if is_gate else 3)      # gated 的答案项要撑到 60-80%
        else:
            sc = -min(max(sc, 2), 3)

        pids = [p for p in (c.get('perspective_ids') or []) if p in valid_pids]
        out.append({
            'criteria': txt[:200],
            'score': sc,
            'reason': str(c.get('reason', ''))[:100],
            'dimension': dim,
            'is_positive': pos,
            '_criterion_id': f'{r["rid"]}-L{j}',
            '_dim_from_table': hit,
            '_perspective_ids': pids,
            '_scenario_ids': sorted({pid2sid[p] for p in pids if pid2sid.get(p)}),
        })

    # 超预算时按 |score| 降序保留，负向至多留 2 条
    pos_l = sorted([c for c in out if c['is_positive']],
                   key=lambda x: -x['score'])
    neg_l = sorted([c for c in out if not c['is_positive']],
                   key=lambda x: x['score'])[:2]
    keep_pos = N_MAX - len(neg_l)
    final = pos_l[:keep_pos] + neg_l

    # gated_answer 答案项占比校验与自动调整
    if is_gate and final:
        pos_final = [c for c in final if c['is_positive']]
        if pos_final:
            max_score = max(c['score'] for c in pos_final)
            total = sum(c['score'] for c in pos_final)
            ratio = max_score / total if total > 0 else 0

            # 目标区间 60-80%，若偏离则调整
            if ratio < 0.6 or ratio > 0.8:
                # 找到答案项（分值最高的那条）
                answer_item = next(c for c in pos_final if c['score'] == max_score)
                other_items = [c for c in pos_final if c['score'] != max_score]

                # 计算目标答案项分值（取70%中点）
                target_ratio = 0.70
                if other_items:
                    other_total = sum(c['score'] for c in other_items)
                    # 根据 answer_score / (answer_score + other_total) = 0.7 反推
                    target_answer = int(round(other_total * target_ratio / (1 - target_ratio)))
                    target_answer = max(6, min(8, target_answer))  # 限制在 6-8

                    # 调整
                    answer_item['score'] = target_answer
                    # 其他项压缩到1分（除非本就是1）
                    for c in other_items:
                        if c['score'] > 1:
                            c['score'] = 1

                # 重新构建final（保持顺序）
                final = [answer_item] + other_items + neg_l

    return flag(final, sum(c['score'] for c in final if c['is_positive']))


def main():
    m = stage.pick('RP_M_GEN', 'generator')
    recs = stage.read_jsonl(SRC)
    print(f'步骤 4L 准则直出: {len(recs)} 条, 源={SRC}, 模型={m.name}')
    npp = sum(len(r.get('perspectives') or []) for r in recs)
    print(f'  输入视角: {npp} 条 ({npp / len(recs):.1f}/题)')
    print(f'  目标准则: {N_MIN}-{N_MAX} 条/题')

    def one(r):
        obj, _ = stage.json_call(m, build(r), stage='s04L', thinking=THINK)
        raw = obj.get('rubrics') or []
        return r['rid'], (parse(r, raw) if isinstance(raw, list) else [])

    done, errs = stage.run(one, recs, workers=WORKERS, desc='s04L')
    by_rid = dict(done)

    res = []
    for r in recs:
        rub = by_rid.get(r['rid'], [])
        pos = [c for c in rub if c['is_positive']]
        res.append({**r, 'rubrics': rub,
                    'core_n': len(rub),
                    'core_n_positive': len(pos),
                    's_max': sum(c['score'] for c in pos)})
    stage.write_jsonl('s04L_rubric.jsonl', res)

    allc = [c for r in res for c in r['rubrics']]
    kept = [r['core_n'] for r in res if r['core_n']]
    print(f'\n=== 步骤 4L 结果 ===')
    if errs:
        print(f'  失败          : {len(errs)} 条')
    empty = [r['rid'] for r in res if not r['core_n']]
    if empty:
        print(f'  ⚠️  空结果     : {len(empty)} 条 {empty[:6]}')
    if kept:
        print(f'  准则/题       : min={min(kept)} p50={sorted(kept)[len(kept) // 2]} '
              f'max={max(kept)} mean={sum(kept) / len(kept):.1f}')
    npos = sum(1 for c in allc if c['is_positive'])
    print(f'  正向/负向     : {npos} / {len(allc) - npos}')
    off = sum(1 for c in allc if not c['_dim_from_table'])
    print(f'  维度命中词表  : {len(allc) - off}/{len(allc)} '
          f'({(len(allc) - off) / max(len(allc), 1) * 100:.1f}%)')

    dc = Counter(c['dimension'] for c in allc)
    print(f'  唯一维度数    : {len(dc)}')
    for d, n in dc.most_common():
        print(f'    {d:<14} {n:5d} ({n / max(len(allc),1) * 100:4.1f}%)')

    # gated_answer 的答案项占比是否达标（设计要求 60-80%）
    gates = [r for r in res if r.get('rubric_form') == 'gated_answer' and r['s_max']]
    if gates:
        shares = []
        for r in gates:
            p = [c for c in r['rubrics'] if c['is_positive']]
            if p:
                shares.append(max(c['score'] for c in p) / r['s_max'] * 100)
        ok = sum(1 for s in shares if 60 <= s <= 80)
        print(f'\n  gated 答案项占比: mean={sum(shares) / len(shares):.1f}%  '
              f'落在 60-80% 的: {ok}/{len(shares)}')

    ex = next((r for r in res if r['core_n'] >= N_MIN), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]} ({len(ex.get("perspectives") or [])} 视角 → '
              f'{ex["core_n"]} 条, 满分 {ex["s_max"]}):')
        for c in ex['rubrics']:
            sign = '+' if c['is_positive'] else '−'
            print(f'    {sign}{abs(c["score"])} [{c["dimension"]}] {c["criteria"][:52]}')


if __name__ == '__main__':
    main()
