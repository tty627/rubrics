"""步骤 10L：回复池 —— 造质量梯度回复，供 Hackable / Low Signal 诊断与判分实测。

**这一步的意义**：在它之前，所有「区分度」结论都是静态文本分析猜的（数「且」字、
看有没有「全部」）。那只能说明准则**写法**可疑，不能说明它**判不出好坏**。
回复池把猜变成测。

四个用途（设计文档 §10）：
  1. Hackable 诊断的载体：弱回复能不能钻空子拿高分
  2. Low Signal 诊断的载体：质量差异明显的回复得分是否拉得开
  3. 定 badcase 阈值：满分是理论值，实际回复能拿多少分要跑一遍才知道
  4. 反向校准步骤 2.5：gated 频繁 Low Signal → 题型判宽了

**弱档必须三种造法并存**（文档 §10，整个设计里最巧的一点）：
    trunc  强回复截断至 40%      —— 弱在「没说完」
    cut    强回复删掉一个关键论点 —— 弱在「漏了要点」
    weak   最弱模型重新生成       —— 弱在「质量低」
三种「弱」不是同一种弱。**若同一准则在三种造法下结论不一致，说明它测的是长度
或结构而非内容，本身就是 Hackable 的信号。** 只用一种造法，Low Signal 的结论
会随造法漂移。

**verifiable 题要另造对抗档**：截断和删论点对数学题没意义，要造「答案错但过程
看似完整」的回复 —— 这才是 gated_answer 真正要防的钻空子方式。

**硬约束第 1 条**：待评回复 ≠ 锚定回复。强档只用 s05L 没用过的那条
（s05L 已把 anchor_key 写进记录，这里排除它）。单回复题标 pool_shared。

档位设计（每题 6 条）：
    strong    现成强回复（排除锚）    —— 上界参照
    mid       中等模型生成            —— 中间档，看分数是否落在中间
    trunc     强回复截断 40%
    cut       强回复删关键论点
    weak      最弱模型生成
    adv       对抗档：verifiable 造「答案错过程全」，open 造「面面俱到但都很浅」
"""
import json, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 8))
SRC = os.environ.get('RP_S10L_SRC', 's04L_pilot.jsonl')
OUT = os.environ.get('RP_S10L_OUT', 's10L_pool.jsonl')
TRUNC_RATIO = float(os.environ.get('RP_TRUNC', 0.4))
# cut 档至少要删掉这么多，否则视为造法失效（实测模型常只删 1%-3%）
MIN_CUT = float(os.environ.get('RP_MIN_CUT', 0.08))
# strong 档短于此视为退化，不能当上界参照（实测 q0007 的 strong 只有 48 字）
MIN_STRONG = int(os.environ.get('RP_MIN_STRONG', 200))

SYS_MID = '''你在回答一道题。请给出一个**中等质量**的回答：
主要结论基本正确，覆盖题目的主干，但不做深入展开、不补充边界条件和例外情形。
篇幅适中。不要刻意犯错，也不要追求详尽。直接作答，不要说明你在做什么。'''

SYS_WEAK = '''你在回答一道题。请给出一个**质量偏低**的回答：
方向大致对，但笼统、缺少具体依据和数值，不展开机理，可能漏掉部分子问题。
篇幅简短。不要刻意胡编，就是答得浅。直接作答，不要说明你在做什么。'''

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

SYS_ADV_VER = '''你要造一份**对抗性回答**，用来测试评分标准有没有漏洞。

要求：过程写得详尽、专业、条理清晰，像一份高质量解答 ——
**但最终答案是错的**（数值算错、选项选错、结论反了都可以）。

关键：错误要藏在中间某一步，不要在开头就明显错。表面上要能骗过快速浏览的人。
不要标注哪里错了，也不要提示这是对抗样本。

只输出回答正文。'''

SYS_ADV_OPEN = '''你要造一份**对抗性回答**，用来测试评分标准有没有漏洞。

要求：把题目相关的各个方面**都提到一句**，术语用对、覆盖面看起来很全 ——
**但每一点都只有一句话，不给任何具体依据、数值、机理或例证**。

目标是：让「是否提及某方面」这类准则全部通过，但实际信息量很低。
不要标注这是对抗样本。

只输出回答正文。'''


def strong_of(r):
    """强档 = 现成回复里排除锚定那条。硬约束第 1 条：待评 ≠ 锚。

    多条候选时取**最长**的那条：strong 是所有比较的上界参照，
    实测 q0007 的 gpt55 回复只有 48 字（锚 656 字），拿 48 字当上界，
    弱档轻易追平，Hackable 判定失真。
    """
    refs = r.get('ref_responses') or {}
    anchor = r.get('anchor_key', '')
    keys = [k for k in sorted(refs) if k != anchor]
    if keys:
        k = max(keys, key=lambda x: len(str(refs[x] or '')))
        return k, str(refs[k] or ''), False
    # 单回复题：只能与锚共用，显式标记，不静默降级
    keys = sorted(refs)
    if keys:
        return keys[0], str(refs[keys[0]] or ''), True
    return '', '', False


def strong_degenerate(r, strong):
    """strong 档是否退化到不能当上界参照。返回 (是否退化, 原因)。

    退化的 strong 会让弱档轻易追平，Hackable 被大面积误报。
    诊断侧据此跳过该题，而不是给出一个假的结论。
    """
    n = len(strong.strip())
    if n < MIN_STRONG:
        return True, f'strong 仅 {n} 字，短于下限 {MIN_STRONG}'
    refs = r.get('ref_responses') or {}
    ak = r.get('anchor_key', '')
    na = len(str(refs.get(ak) or ''))
    if na and n * 2 < na:
        return True, f'strong {n} 字 < 锚 {na} 字的一半，锚才是更好的回答'
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
    m_mid = stage.pick('RP_M_POOL_MID', 'pool_mid')
    m_weak = stage.pick('RP_M_POOL_WEAK', 'pool_weak')
    recs = stage.read_jsonl(SRC)
    print(f'步骤 10L 回复池: {len(recs)} 题, 源={SRC}')
    print(f'  中档={m_mid.name}  弱档={m_weak.name}')
    n_shared = sum(1 for r in recs if strong_of(r)[2])
    if n_shared:
        print(f'  ⚠️  单回复题 {n_shared} 个：强档与锚共用，违反硬约束第 1 条，已标 pool_shared')

    # 摊平成 (rid, 档位) 任务；strong/trunc 不调 LLM
    jobs = []
    for r in recs:
        for tier in ('mid', 'weak', 'cut', 'adv'):
            jobs.append((r['rid'], tier))
    print(f'  LLM 任务: {len(jobs)} ({len(recs)} 题 × 4 档，strong/trunc 本地生成)')

    by_rid = {r['rid']: r for r in recs}

    def one(job):
        rid, tier = job
        r = by_rid[rid]
        q = (r.get('query_eff') or r['question'])[:2000]
        _, strong, _ = strong_of(r)
        ver = r.get('question_type') == 'verifiable'

        if tier == 'mid':
            sys_p, mdl, usr = SYS_MID, m_mid, q
        elif tier == 'weak':
            sys_p, mdl, usr = SYS_WEAK, m_weak, q
        elif tier == 'cut':
            if not strong.strip():
                return rid, tier, ''
            sys_p, mdl = SYS_CUT, m_mid
            usr = f'【题目】\n{q}\n\n【待改写的回答】\n{strong[:6000]}'
        else:
            sys_p = SYS_ADV_VER if ver else SYS_ADV_OPEN
            mdl, usr = m_mid, q

        txt, _ = stage.llm.call(mdl, [{'role': 'system', 'content': sys_p},
                                      {'role': 'user', 'content': usr}],
                                stage=f's10L_{tier}')
        return rid, tier, (txt or '').strip()

    done, errs = stage.run(one, jobs, workers=WORKERS, desc='s10L')
    gen = {(rid, tier): txt for rid, tier, txt in done}

    res, stat = [], Counter()
    for r in recs:
        key, strong, shared = strong_of(r)
        ver = r.get('question_type') == 'verifiable'
        pool = []

        def add(tier, text, how):
            text = (text or '').strip()
            if not text:
                return
            pool.append({'tier': tier, 'text': text, 'how': how,
                         'n_chars': len(text)})
            stat[tier] += 1

        add('strong', strong, f'现成回复 {key}')
        add('mid', gen.get((r['rid'], 'mid'), ''), f'{m_mid.name} 生成')
        add('trunc', truncate(strong), f'强回复截断至 {TRUNC_RATIO:.0%}')

        # cut 档要校验删除量：实测模型常只删十几个字（1%-3%），这一档就失效了，
        # 而失效的 cut 会被 Hackable 诊断读成「删了关键论点仍给分」，得出反向结论。
        # 删得太少就标 degraded，诊断侧据此排除，不让它污染结论。
        cut_txt = (gen.get((r['rid'], 'cut')) or '').strip()
        if cut_txt and strong:
            ratio = 1 - len(cut_txt) / max(len(strong), 1)
            if ratio < MIN_CUT:
                add('cut', cut_txt, f'删关键论点（⚠️仅删 {ratio:.1%}，未达 '
                                    f'{MIN_CUT:.0%}，本档失效）')
                pool[-1]['degraded'] = True
                pool[-1]['cut_ratio'] = round(ratio, 4)
            else:
                add('cut', cut_txt, f'强回复删关键论点（删 {ratio:.1%}）')
                pool[-1]['cut_ratio'] = round(ratio, 4)
        add('weak', gen.get((r['rid'], 'weak'), ''), f'{m_weak.name} 生成')
        add('adv', gen.get((r['rid'], 'adv'), ''),
            '对抗：答案错但过程完整' if ver else '对抗：面面俱到但都很浅')

        deg, why = strong_degenerate(r, strong)
        res.append({**r, 'pool': pool, 'pool_shared': shared,
                    'pool_strong_key': key,
                    'strong_degenerate': deg, 'strong_degenerate_reason': why})

    stage.write_jsonl(OUT, res)

    npool = [len(r['pool']) for r in res]
    print(f'\n=== 步骤 10L 结果 ===')
    if errs:
        print(f'  失败        : {len(errs)} 条')
    print(f'  回复/题     : min={min(npool)} max={max(npool)} '
          f'mean={sum(npool) / len(npool):.1f}  总计 {sum(npool)} 条')
    print(f'  各档条数    : ' + '  '.join(f'{k}={v}' for k, v in stat.most_common()))

    # cut 档有效性：失效的档位会让 Hackable 诊断得出反向结论，必须显式报出来
    deg = [(r['rid'], p.get('cut_ratio')) for r in res for p in r['pool']
           if p['tier'] == 'cut' and p.get('degraded')]
    cut_all = [p.get('cut_ratio') for r in res for p in r['pool']
               if p['tier'] == 'cut' and p.get('cut_ratio') is not None]
    if cut_all:
        print(f'\n  cut 档删除比例: mean={sum(cut_all) / len(cut_all):.1%}  '
              f'达标(≥{MIN_CUT:.0%}) {len(cut_all) - len(deg)}/{len(cut_all)}')
    if deg:
        print(f'  ⚠️  cut 档失效 {len(deg)} 题（删得太少，已标 degraded，'
              f'诊断侧会排除）: ' + ', '.join(f'{r}({x:.1%})' for r, x in deg[:8]))

    sd = [(r['rid'], r['strong_degenerate_reason']) for r in res
          if r.get('strong_degenerate')]
    if sd:
        print(f'\n  ⚠️  strong 档退化 {len(sd)} 题（不能当上界参照，诊断侧跳过）:')
        for rid, why in sd:
            print(f'    {rid}: {why}')

    # 长度梯度：弱档该明显短于强档，否则「弱」没造出来
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
