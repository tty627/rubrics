"""步骤 10L：回复池 —— 造质量梯度回复，供 Hackable / Low Signal 诊断与判分实测。

**这一步的意义**：在它之前，所有「区分度」结论都是静态文本分析猜的（数「且」字、
看有没有「全部」）。那只能说明准则**写法**可疑，不能说明它**判不出好坏**。
回复池把猜变成测。

四个用途（设计文档 §10）：
  1. Hackable 诊断的载体：弱回复能不能钻空子拿高分
  2. Low Signal 诊断的载体：质量差异明显的回复得分是否拉得开
  3. 定 badcase 阈值：满分是理论值，实际回复能拿多少分要跑一遍才知道
  4. 反向校准步骤 2.5：gated 频繁 Low Signal → 题型判宽了

**open 题弱档必须三种造法并存**（文档 §10，整个设计里最巧的一点）：
    trunc  强回复截断至 40%      —— 弱在「没说完」
    cut    强回复删掉一个关键论点 —— 弱在「漏了要点」
    weak   最弱模型重新生成       —— 弱在「质量低」
三种「弱」不是同一种弱。**若同一准则在三种造法下结论不一致，说明它测的是长度
或结构而非内容，本身就是 Hackable 的信号。** 只用一种造法，Low Signal 的结论
会随造法漂移。gated_answer 不造 trunc/cut，见下方档位设计。

**verifiable 题要另造对抗档**：截断和删论点对数学题没意义，要造「答案错但过程
看似完整」的回复 —— 这才是 gated_answer 真正要防的钻空子方式。

**六档全部现场生成，不读题目自带的候选回答。** 候选回答是待评对象，拿它当 strong
就把「被测的东西」当成了「测量基准」；而且一道题带几条候选回答纯属数据集偶然，
按此切分会凭空造出「不可测题」（旧线据此把 452 切成 388 + 64）。现在每题都有完整档位。

档位设计：
    strong    最强模型生成            —— 上界参照，可核验题必须答对
    mid       中等模型生成            —— 中间档，看分数是否落在中间
    trunc     强档截断 40%            —— 弱在「没说完」（仅 open 论证，gated 不造）
    cut       强档删关键论点          —— 弱在「漏了要点」（仅 open 论证，gated 不造）
    weak      最弱模型生成
    adv       对抗档：verifiable 造「答案错过程全」，open 造「面面俱到但都很浅」

gated_answer（可核验）题只造 strong/mid/weak/adv 四档：trunc/cut 是结构性弱档，
预设回答是多论点、可截断的论证。可核验题的强档可能只有一句结论（如「你好世界」），
删=空、截=断词，造不出「漏了要点」；其真正的弱档是 weak + adv（答案错过程全），
判据是闸门而非长度/结构。

造法护栏（实测踩出来的，每条都对应过一批假信号）：
  A. strong 答案核验：可核验题的 strong 答错则整题参照系失效（实测 22 道地板题
     里 5 道是「答得长但答错」，放松准则治不了）。答错用权威答案重生成一次，
     仍错标 strong_wrong_answer，诊断侧跳过该题。
  B. 对抗档反向核验：gated/verifiable 且答案可判定时，程序化校验
     「最终结论 ≠ answer_canonical」，答对了自动用强化提示词重造一次；
     仍答对 → 标 answer_correct，诊断侧剔除该档（实测 8 道 gated 里 5 道
     的对抗档「答案错」没造出来，adv 高分不是钻空子是造错失败）。
  C. gated 弱档反向核验：弱档把答案答对就失去「弱」的意义（q0238 代码、
     q0301 数学），同样重造一次，仍答对标 answer_correct。
  D. cut 删除量 <8% 自动重试一次（强化删除量指令）；仍不达标标 degraded。
  E. 两趟执行：strong/mid/weak/adv 先跑，定稿 strong 后再造 trunc/cut——
     它们是 strong 的字面派生档，必须基于最终 strong 才不会错位。
  F. canon 缺失时的 LLM 复核：独立答案阶段没有给出可程序核验 canonical
     答案的题（48 题里 11/16 道 gated），程序化反向核验无从下手。用 judge
     角色模型做**相对判定**——拿 strong 档当参考答案，只比较最终结论：
     - adv（verifiable 无 canon）：「对抗档的最终结论是否与强档一致/正确」
       → 一致则造法失败，重造一次，仍一致标 answer_correct；
     - weak（gated 无 canon）：「弱档是否把答案答对了」→ 同处理。
     open 题的弱档**不做**文本质量复核（文本完整 ≠ rubric 得分，实测误杀），
     弱档质量由 s11c_consequential 的 gap 实测反映。
     把审计残留的 pool 型假 Hackable（q0045/q0238/q0242/q0301/q0445/q0448）
     在源头挡掉。
"""
import json, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage, answer_check

WORKERS = int(os.environ.get('RP_WORKERS', 8))
SRC = os.environ.get('RP_S10L_SRC', 's04c_phase4.jsonl')
OUT = os.environ.get('RP_S10L_OUT', 's10_pool.jsonl')
TRUNC_RATIO = float(os.environ.get('RP_TRUNC', 0.4))
# cut 档至少要删掉这么多，否则视为造法失效（实测模型常只删 1%-3%）
MIN_CUT = float(os.environ.get('RP_MIN_CUT', 0.08))
# strong 档短于此视为退化，不能当上界参照（实测 q0007 的 strong 只有 48 字）
MIN_STRONG = int(os.environ.get('RP_MIN_STRONG', 200))

SYS_STRONG = '''你在回答一道题。请给出一个**高质量**的回答：
内容正确、覆盖完整、论证充分、篇幅充实（几百字以上，能展开的都展开）。
直接作答，不要说明你在做什么。'''

# strong 档答错时的重生成口径。给出已由权威模型 + 异源交叉复核确认的答案，
# 要求把推导补完整——目的是修好参照系，不是替被测模型作答。
SYS_STRONG_ANCHORED = '''你在回答一道题，题目末尾附了已核实的正确答案。
请围绕这个正确答案给出完整回答：推导或论证过程写全，最终结论必须与给定答案一致。
内容正确、覆盖完整、篇幅充实。直接作答，不要提及"给定答案"这件事。'''

# 388 全量实测：原 SYS_MID 只写「篇幅适中」，pool_mid 模型把「不做深入展开」
# 读成「答简短」——中位 244 字，比 weak 的 395 字还短，65% 的题 mid ≤ weak，
# 档位序直接坏掉。两档还是不同模型，篇幅没有跨档约束。故改为绝对字数下限 +
# 显式「每个要点都要给依据」，把「中等」锚在覆盖深度而不是篇幅上。
SYS_MID = '''你在回答一道题。请给出一个**中等质量**的回答：
主要结论正确，题目问到的每个点都要答到，每个要点都给出具体依据（数值/公式/
机理/出处任一），但不做延伸展开、不补充边界条件和例外情形、不讨论替代方案。

【篇幅】不少于 600 字。要点答全、依据给足，只是不往深处和边界处延伸。
不要刻意犯错，也不要追求详尽。直接作答，不要说明你在做什么。'''

SYS_WEAK = '''你在回答一道题。请给出一个**质量偏低**的回答：
方向大致对，但笼统、缺少具体依据和数值，不展开机理，可能漏掉部分子问题。
篇幅简短，不超过 400 字。不要刻意胡编，就是答得浅。
直接作答，不要说明你在做什么。'''

SYS_CUT = '''你要从一份回答里**整段删掉一个最关键的论点**，造出一份「漏了要点」的回答。

【先定位，再删】
1. 通读回答，找出这道题**最核心**的那个论点（最终结论、关键数值、主要机理、
   或题目直接问的那个点）。
2. 把承载它的**整段或整句全部删除** —— 连同它的论证、举例、推导一起删。
3. 其余部分逐字保留，不要改写、精简或补充。

【删除量要求】
被删内容通常占原文的 **10%-30%**。如果你只删了一两个词，那是没删对 ——
说明你删的不是论点而是措辞。宁可多删一整段，也不要只删几个字。

【不要留断口】
删完后上下文要接得上，读起来仍像一份完整（但漏了要点）的回答。
不要留下「如上所述」「综上」却没有对应内容的悬空引用，
也不要留下 "1. 2. 4." 这种断号 —— 否则测出来的是「结构破损」而不是「内容缺失」。

只输出删改后的回答正文，不要任何说明，不要标注你删了什么。'''

SYS_CUT_RETRY = '''上一版你删得太少（不足 8%），「删关键论点」造法失效。重写：

这次必须**整段删掉**承载最终结论/答案/核心数值的那一整段（连同它的论证与推导），
被删内容必须 ≥ 15%，其余部分逐字保留、不要改写。宁可删一整段，也不要只删几个词。

只输出删改后的回答正文，不要任何说明。'''

SYS_ADV_VER = '''你要造一份**对抗性回答**，用来测试评分标准有没有漏洞。

要求：过程写得详尽、专业、条理清晰，像一份高质量解答 ——
**但最终答案是错的**（数值算错、选项选错、结论反了都可以）。

关键：错误要藏在中间某一步，不要在开头就明显错。表面上要能骗过快速浏览的人。
不要标注哪里错了，也不要提示这是对抗样本。

只输出回答正文。'''

SYS_ADV_VER_RETRY = '''上一版你造的对抗性回答最终答案居然是对的，造法失败。重写：

过程要详尽、专业、条理清晰，但**最终结论必须与正确答案不同**——
数值要算错、选项要选错的、结论要反着说。错误藏在推导靠后的步骤里，
开头看起来要专业。不要标注哪里错了。

只输出回答正文。'''

SYS_ADV_OPEN = '''你要造一份**对抗性回答**，用来测试评分标准有没有漏洞。

要求：把题目相关的各个方面**都提到一句**，术语用对、覆盖面看起来很全 ——
**但每一点都只有一句话，不给任何具体依据、数值、机理或例证**。

目标是：让「是否提及某方面」这类准则全部通过，但实际信息量很低。
不要标注这是对抗样本。

只输出回答正文。'''

SYS_WEAK_GATED_RETRY = '''上一版你把答案写对了，没造出「弱档」。重写：

给一份**质量偏低**的回答：方向大致对、表面像认真作答，但**关键处出错**——
计算错、漏掉关键条件、结论不完整或偏差，最终答案不能正确。
篇幅简短。不要标注这是弱档。

只输出回答正文。'''

# ---- LLM 复核（canon 缺失时的兜底，见 docstring F）----
SYS_CHECK_ADV = '''你在检查一份**对抗性回答**的造法是否失败。造法要求：
过程写得详尽专业，但**最终结论是错的**。给你强档参考回答（可视为正确答案）。

只比较**最终结论/答案**（通常在末尾）。过程相似不算。
- 对抗档的最终答案与强档**完全相同或同样正确** → flag=true（造法失败）
- 最终答案不同/确实错了 → flag=false
- 无法确定 → flag=false（宁放过不误杀）

只输出 JSON：{"flag": true/false, "why": "不超过30字"}'''

SYS_CHECK_WEAK_GATED = '''你在检查一份「弱档回答」的造法是否失败。造法要求：
弱档应方向大致对但关键处出错、最终答案不能正确。给你强档参考回答
（可视为正确答案）。

只比较**最终答案/结论**（通常在末尾）。过程相似不算。
- 弱档的最终答案与强档**完全相同或同样正确** → flag=true（造法失败）
- 确实答错/残缺 → flag=false
- 无法确定 → flag=false（宁放过不误杀）

只输出 JSON：{"flag": true/false, "why": "不超过30字"}'''


def strong_degenerate(r, strong):
    """strong 档是否退化到不能当上界参照。返回 (是否退化, 原因)。

    退化的 strong 会让弱档轻易追平，Hackable 被大面积误报。
    诊断侧据此跳过该题，而不是给出一个假的结论。

    gated_answer（可核验）题的 strong 由「答案对不对」判退化，不看篇幅：
    正确但很短的答案（如「你好世界」）是有效参照，篇幅检查会误杀它——
    而篇幅是 open 题「模型没在作答」的代理信号，对可核验题不适用。
    """
    n = len(strong.strip())
    if r.get('rubric_form') == 'gated_answer':
        return False, ''
    if n < MIN_STRONG:
        return True, f'strong 仅 {n} 字，短于下限 {MIN_STRONG}'
    return False, ''


def truncate(t, ratio=TRUNC_RATIO):
    """按句边界截断，避免截在半个词上（那测的是格式破损不是内容缺失）。"""
    n = int(len(t) * ratio)
    if n <= 0:
        return t
    cut = t[:n]
    for sep in ('\n\n', '。', '\n', '. '):
        i = cut.rfind(sep)
        if i > n * 0.5:
            return cut[:i + len(sep)].rstrip()
    return cut


def main():
    m_strong = stage.pick('RP_M_POOL_STRONG', 'generator')
    m_mid = stage.pick('RP_M_POOL_MID', 'pool_mid')
    m_weak = stage.pick('RP_M_POOL_WEAK', 'pool_weak')
    m_check = stage.pick('RP_M_POOL_CHECK', 'judge')
    recs = stage.read_jsonl(SRC)
    print(f'步骤 21 回复池: {len(recs)} 题, 源={SRC}')
    print(f'  强档={m_strong.name}  中档={m_mid.name}  弱档={m_weak.name}'
          f'  复核={m_check.name}')

    by_rid = {r['rid']: r for r in recs}

    # 六档全部现场生成。strong 不再取自题目自带的 ref_responses：那些回复是待评对象，
    # 拿它当上界参照就把「被测回答」混进了「测量基准」，而且题目有几条回复纯属偶然
    # （曾据此把数据集切成 388/452 两条线）。现在每题都有 strong，无单回复题概念。
    jobs1 = [(r['rid'], tier) for r in recs
             for tier in ('strong', 'mid', 'weak', 'adv')]
    print(f'  第一趟 LLM 任务: {len(jobs1)} （strong/mid/weak/adv 全部现场生成）')

    # rid -> 最终 strong（cut/trunc 都以它为准）
    strong_map = {}

    def llm_check(sys_p, q, strong_txt, cand_txt):
        """廉价复核：flag=true 表示造法失败（答对了/不够弱）。"""
        u = (f'【题目】\n{q}\n\n'
             f'【强档参考回答】\n{(strong_txt or "")[:4000]}\n\n'
             f'【待检查回答】\n{(cand_txt or "")[:4000]}\n')
        obj, _ = stage.json_call(m_check,
                                 [{'role': 'system', 'content': sys_p},
                                  {'role': 'user', 'content': u}],
                                 stage='s10L_check')
        return bool(obj.get('flag')), str(obj.get('why', ''))[:60]

    def review(rid, tier, q, ver, canon_ok):
        """相对复核第一趟的 adv/weak：拿定稿 strong 当参考答案比最终结论。

        只在 canonical 缺失时兜底（有 canonical 时第一趟已程序化核验过）。
        返回的 tier 是被复核的原档名，直接覆盖第一趟结果。
        """
        base = 'adv' if tier == 'adv_review' else 'weak'
        txt, meta = gen.get((rid, base), ('', {}))
        meta = dict(meta)
        strong_ref = strong_map[rid][1]
        if not (ver and not canon_ok and txt and strong_ref):
            return rid, base, txt, meta
        sys_check = SYS_CHECK_ADV if base == 'adv' else SYS_CHECK_WEAK_GATED
        sys_retry = SYS_ADV_VER_RETRY if base == 'adv' else SYS_WEAK_GATED_RETRY
        mdl = m_mid if base == 'adv' else m_weak
        if llm_check(sys_check, q, strong_ref, txt)[0]:
            meta['retried'] = True
            txt2, _ = stage.llm.call(mdl,
                                     [{'role': 'system', 'content': sys_retry},
                                      {'role': 'user', 'content': q}],
                                     stage=f's10L_{base}_retry2')
            txt2 = (txt2 or '').strip()
            if txt2 and not llm_check(sys_check, q, strong_ref, txt2)[0]:
                txt, meta['fixed'] = txt2, True
            else:
                meta['answer_correct'] = True
        return rid, base, txt, meta

    def one(job):
        rid, tier = job
        r = by_rid[rid]
        q = (r.get('query_eff') or r['question'])[:2000]
        ver = r.get('question_type') == 'verifiable'
        canon_ok = (ver and r.get('rubric_form') == 'gated_answer'
                    and (r.get('answer_canonical') or '').strip()
                    and r.get('answer_sound', True))
        kind, canon = r.get('answer_kind'), r.get('answer_canonical')
        meta = {}

        if tier == 'strong':
            txt, _ = stage.llm.call(m_strong,
                                    [{'role': 'system', 'content': SYS_STRONG},
                                     {'role': 'user', 'content': q}],
                                    stage='s10L_strong')
            txt = (txt or '').strip()
            # 可核验题的 strong 必须答对，否则整题参照系是坏的（22 道地板题里 5 道
            # 就是「答得长但答错」）。答错就用权威答案重生成一次。
            if canon_ok and not answer_check.has_correct_answer(kind, canon, txt):
                meta['retried'] = True
                txt2, _ = stage.llm.call(
                    m_strong,
                    [{'role': 'system', 'content': SYS_STRONG_ANCHORED},
                     {'role': 'user', 'content': f'{q}\n\n【已核实的正确答案】{canon}'}],
                    stage='s10L_strong_retry')
                txt2 = (txt2 or '').strip()
                if txt2 and answer_check.has_correct_answer(kind, canon, txt2):
                    txt, meta['fixed'] = txt2, True
                else:
                    meta['strong_wrong_answer'] = True
            return rid, tier, txt, meta

        # 相对复核档：不生成新回复，只复核第一趟的 adv/weak，必须在 strong 定稿后跑。
        if tier in ('adv_review', 'weak_review'):
            return review(rid, tier, q, ver, canon_ok)

        if tier == 'mid':
            sys_p, mdl, usr = SYS_MID, m_mid, q
        elif tier == 'weak':
            sys_p, mdl, usr = SYS_WEAK, m_weak, q
        elif tier == 'cut':
            _, strong, _, _ = strong_map[rid]
            if not strong.strip():
                return rid, tier, '', meta
            sys_p, mdl = SYS_CUT, m_mid
            usr = f'【题目】\n{q}\n\n【待改写的回答】\n{strong[:6000]}'
        else:
            sys_p = SYS_ADV_VER if ver else SYS_ADV_OPEN
            mdl, usr = m_mid, q

        txt, _ = stage.llm.call(mdl, [{'role': 'system', 'content': sys_p},
                                      {'role': 'user', 'content': usr}],
                                stage=f's10L_{tier}')
        txt = (txt or '').strip()

        # ---- 反向核验 + 重试（审计修复 B/C/D）----
        if tier == 'adv' and canon_ok and \
                answer_check.has_correct_answer(kind, canon, txt):
            meta['retried'] = True
            txt2, _ = stage.llm.call(mdl,
                                     [{'role': 'system', 'content': SYS_ADV_VER_RETRY},
                                      {'role': 'user', 'content': q}],
                                     stage='s10L_adv_retry')
            txt2 = (txt2 or '').strip()
            if txt2 and not answer_check.has_correct_answer(kind, canon, txt2):
                txt, meta['fixed'] = txt2, True
            else:
                meta['answer_correct'] = True
        if tier == 'weak' and canon_ok and \
                answer_check.has_correct_answer(kind, canon, txt):
            meta['retried'] = True
            txt2, _ = stage.llm.call(m_weak,
                                     [{'role': 'system', 'content': SYS_WEAK_GATED_RETRY},
                                      {'role': 'user', 'content': q}],
                                     stage='s10L_weak_retry')
            txt2 = (txt2 or '').strip()
            if txt2 and not answer_check.has_correct_answer(kind, canon, txt2):
                txt, meta['fixed'] = txt2, True
            else:
                meta['answer_correct'] = True

        # ---- LLM 相对复核（canon 缺失时的兜底，审计修复 F）----
        # 只对 gated/verifiable 且 canonical 缺失的题做「结论是否等于强档」复核。
        # 拿最终 strong 当参考答案，所以必须在第二趟跑（第一趟 strong 还没定稿）。
        # open 题不做「弱档不够弱」复核：文本完整性 ≠ rubric 得分（实测 q0005
        # 弱档文本完整但判分仅 20%，文本复核会误杀）；open 题的弱档质量由
        # 23 的 gap 实测直接反映。
        if tier == 'cut' and txt:
            strong = strong_map[rid][1]
            ratio = 1 - len(txt) / max(len(strong), 1)
            if ratio < MIN_CUT:
                meta['retried'] = True
                txt2, _ = stage.llm.call(m_mid,
                                         [{'role': 'system', 'content': SYS_CUT_RETRY},
                                          {'role': 'user', 'content': usr}],
                                         stage='s10L_cut_retry')
                txt2 = (txt2 or '').strip()
                if txt2 and 1 - len(txt2) / max(len(strong), 1) >= MIN_CUT:
                    txt, meta['fixed'] = txt2, True
        return rid, tier, txt, meta

    done1, errs1 = stage.run(one, jobs1, workers=WORKERS, desc='s10L 趟1')
    gen = {(rid, tier): (txt, meta) for rid, tier, txt, meta in done1}
    pool_errors = {}
    for index, message in errs1:
        rid, tier = jobs1[index]
        pool_errors.setdefault(rid, {})[tier] = str(message)[:500]

    # ---- 定稿 strong（cut/trunc 都以它为准）----
    n_wrong = 0
    for r in recs:
        strong, meta = gen.get((r['rid'], 'strong'), ('', {}))
        strong = (strong or '').strip()
        deg, why = strong_degenerate(r, strong)
        if meta.get('strong_wrong_answer'):
            deg, why, n_wrong = True, '强档答案与权威答案不一致', n_wrong + 1
        strong_map[r['rid']] = (f'{m_strong.name} 生成', strong, deg, why)

    # ---- 第二趟：cut 与 adv/weak 相对复核，都要基于最终 strong ----
    # gated_answer 不造 cut/trunc：它们是「删一个关键论点 / 截断 40%」的结构性弱档，
    # 预设回答是多论点、可截断的论证。可核验题（尤其答案仅几个字的平凡题）没有
    # 可删的论点——强档只有一句结论，删=空、截=断词，造出的不是「漏了要点」而是
    # 「内容缺失/结构破损」，SYS_CUT 甚至会直接返回空正文触发 pool_errors。gated 的
    # 弱档是 weak + adv（答案错过程全），闸门才是判据（docstring 开头已声明）。
    jobs2 = []
    for r in recs:
        if not strong_map[r['rid']][1].strip():
            continue
        if r.get('rubric_form') != 'gated_answer':
            jobs2.append((r['rid'], 'cut'))
        if r.get('question_type') == 'verifiable':
            jobs2 += [(r['rid'], 'adv_review'), (r['rid'], 'weak_review')]
    done2, errs2 = stage.run(one, jobs2, workers=WORKERS, desc='s10L 趟2(cut)')
    gen.update({(rid, tier): (txt, meta) for rid, tier, txt, meta in done2})
    for index, message in errs2:
        rid, tier = jobs2[index]
        pool_errors.setdefault(rid, {})[tier] = str(message)[:500]

    # ---- 汇总 ----
    res, stat = [], Counter()
    for r in recs:
        key, strong, deg, why = strong_map[r['rid']]
        ver = r.get('question_type') == 'verifiable'
        pool = []

        def add(tier, text, how, meta=None):
            text = (text or '').strip()
            if not text:
                return
            p = {'tier': tier, 'text': text, 'how': how, 'n_chars': len(text)}
            meta = meta or {}
            if meta.get('answer_correct'):
                p['answer_correct'] = True
            if meta.get('weak_not_weak'):
                p['weak_not_weak'] = True
            if meta.get('retried') and meta.get('fixed'):
                p['how'] += '（已触发重造）'
            pool.append(p)
            stat[tier] += 1

        strong_meta = gen.get((r['rid'], 'strong'), ('', {}))[1]
        s_how = key
        if strong_meta.get('strong_wrong_answer'):
            s_how += '（⚠️答案核验：重生成后仍与权威答案不一致，参照系失效）'
        add('strong', strong, s_how, strong_meta)
        add('mid', gen.get((r['rid'], 'mid'), ('', {}))[0],
            f'{m_mid.name} 生成')

        # trunc/cut 是结构性弱档，只对 open 论证成立；gated_answer 的弱档是
        # weak + adv（见第二趟 jobs2 的注释），这里不再造，闸门也按此口径校验。
        if r.get('rubric_form') != 'gated_answer':
            add('trunc', truncate(strong), f'强回复截断至 {TRUNC_RATIO:.0%}')

            cut_txt, cut_meta = gen.get((r['rid'], 'cut'), ('', {}))
            cut_txt = (cut_txt or '').strip()
            if cut_txt and strong:
                ratio = 1 - len(cut_txt) / max(len(strong), 1)
                if ratio < MIN_CUT:
                    add('cut', cut_txt,
                        f'删关键论点（⚠️仅删 {ratio:.1%}，未达 {MIN_CUT:.0%}，'
                        f'重试后仍失效）', cut_meta)
                    pool[-1]['degraded'] = True
                else:
                    add('cut', cut_txt, f'强回复删关键论点（删 {ratio:.1%}）', cut_meta)
                pool[-1]['cut_ratio'] = round(ratio, 4)

        wt, wm = gen.get((r['rid'], 'weak'), ('', {}))
        w_how = f'{m_weak.name} 生成'
        if wm.get('answer_correct'):
            w_how += '（⚠️答案核验：把答案答对了，本档失效）'
        if wm.get('weak_not_weak'):
            w_how += '（⚠️复核：不够弱，本档失效）'
        add('weak', wt, w_how, wm)

        at, am = gen.get((r['rid'], 'adv'), ('', {}))
        a_how = '对抗：答案错但过程完整' if ver else '对抗：面面俱到但都很浅'
        if am.get('answer_correct'):
            a_how += '（⚠️答案核验：结论是正确答案，本档失效）'
        add('adv', at, a_how, am)

        rec = {**r, 'pool': pool, 'pool_strong_key': key,
               'strong_degenerate': deg, 'strong_degenerate_reason': why}
        failed = pool_errors.get(r['rid'], {})
        if failed:
            rec['pool_errors'] = dict(sorted(failed.items()))
            rec = stage.add_stage_errors(rec, [
                stage.error_entry('s10L', tier, message)
                for tier, message in sorted(failed.items())])
        res.append(rec)

    stage.write_jsonl(OUT, res)

    npool = [len(r['pool']) for r in res]
    print(f'\n=== 步骤 21 结果 ===')
    if errs1 or errs2:
        print(f'  失败        : {len(errs1) + len(errs2)} 条')
    print(f'  回复/题     : min={min(npool)} max={max(npool)} '
          f'mean={sum(npool) / len(npool):.1f}  总计 {sum(npool)} 条')
    print(f'  各档条数    : ' + '  '.join(f'{k}={v}' for k, v in stat.most_common()))
    if n_wrong:
        print(f'  ⚠️  strong 答案核验失败 {n_wrong} 题（参照系失效，诊断侧将跳过）')

    deg = [(r['rid'], p.get('cut_ratio')) for r in res for p in r['pool']
           if p['tier'] == 'cut' and p.get('degraded')]
    cut_all = [p.get('cut_ratio') for r in res for p in r['pool']
               if p['tier'] == 'cut' and p.get('cut_ratio') is not None]
    if cut_all:
        print(f'  cut 档删除比例: mean={sum(cut_all) / len(cut_all):.1%}  '
              f'达标(≥{MIN_CUT:.0%}) {len(cut_all) - len(deg)}/{len(cut_all)}')
    if deg:
        print(f'  ⚠️  cut 档失效 {len(deg)} 题（重试后仍删太少，已标 degraded）: '
              + ', '.join(f'{r}({x:.1%})' for r, x in deg[:8]))

    ac = [(r['rid'], p['tier']) for r in res for p in r['pool']
          if p.get('answer_correct')]
    if ac:
        print(f'  ⚠️  答案核验失败 {len(ac)} 档（重试后仍把答案答对，诊断侧将剔除）: '
              + ', '.join(f'{a}/{b}' for a, b in ac))
    nw = [(r['rid'], p['tier']) for r in res for p in r['pool']
          if p.get('weak_not_weak')]
    if nw:
        print(f'  ⚠️  弱档复核失败 {len(nw)} 档（重试后仍不够弱，诊断侧将剔除）: '
              + ', '.join(f'{a}/{b}' for a, b in nw))

    sd = [(r['rid'], r['strong_degenerate_reason']) for r in res
          if r.get('strong_degenerate')]
    if sd:
        print(f'\n  ⚠️  strong 档仍退化 {len(sd)} 题（不能当上界参照，诊断侧跳过）:')
        for rid, why in sd:
            print(f'    {rid}: {why}')

    print(f'\n  平均长度（字）:')
    for tier in ('strong', 'mid', 'trunc', 'cut', 'weak', 'adv'):
        L = [p['n_chars'] for r in res for p in r['pool'] if p['tier'] == tier]
        if L:
            print(f'    {tier:<8} {sum(L) / len(L):7.0f}  (n={len(L)})')

    ex = res[0]
    print(f'\n  抽样 {ex["rid"]} ({ex.get("question_type")}):')
    for p in ex['pool']:
        print(f'    {p["tier"]:<8} {p["n_chars"]:5d}字  {p["how"]}')
        print(f'             {p["text"][:70]!r}')


if __name__ == '__main__':
    main()
