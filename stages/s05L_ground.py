"""步骤 5L：Response Grounding（lean 版）—— 用锚定回复抽出「可观察行为」，喂给 s04L。

**与 legacy/phase4/s05_grounding.py 的根本差别**：
  旧版插在 s04 **之后**，做事后 drift 检查（准则已经生成完了，再看哪条站不住）。
  但设计文档 §5 要求的是「用强模型参考回答**锚定**准则」—— 锚是生成的输入，不是
  事后的过滤器。而且旧版读的是 s04_criteria.jsonl 的旧 schema（positive/negative），
  和 lean 的 criteria/is_positive 不兼容，直接搬过来会崩。

  本步插在 s04L **之前**：从锚定回复里抽出「一份实际回答在这道题上覆盖了哪些
  可观察、可核对的内容点」，作为候选锚点交给 s04L。

**为什么这一步能治事实错误**：s11L 的第四检测器在全量上抓到 286 条事实错误
（准则里写死的答案是编的）。根因就是没有锚 —— 模型凭记忆写「最终答案为 k=5」。
有了锚点，准则的具体内容有实际文本可依，不用凭记忆。

**硬约束第 1 条：锚定回复 ≠ 待评回复**。
种子集每题最多 2 条参考回复，这里的分工是：
  - 排序后的第 1 条 → 锚（本步用）
  - 第 2 条          → 留给步骤 10 当待评对象，本步绝不读
单回复题（种子集里 64 题）没法分离，标 `anchor_shared=True` 显式暴露，
不静默降级 —— 这类题的判分结果不能和双回复题混在一起看。

锚点**不是满分答案**。它只是一份实际回答，可能有遗漏、有错。
所以抽取时同时标注 `confidence` 与 `gaps`（这份回答明显没覆盖的方面），
s04L 拿到后按置信度决定要不要采信。
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 8))
THINK = stage.envflag('RP_THINK', True)
SRC = os.environ.get('RP_S05L_SRC', 's02_5_route.jsonl')
OUT = os.environ.get('RP_S05L_OUT', 's05L_grounded.jsonl')
N_MAX = int(os.environ.get('RP_ANCHOR_MAX', 8))

SYS = f'''你在为一道题抽取「锚点」——即一份实际回答里**可观察、可核对**的内容点，
供后续写评分标准时参照。

【为什么要锚点】
写评分标准的模型如果只看题目，会凭记忆编具体答案（编错的数值、不存在的引用）。
锚点的作用是：让准则的具体内容有实际文本可依。

【锚点是什么】
从下方【锚定回答】里抽出最多 {N_MAX} 个**具体内容点**。每个锚点必须：
1. **可指认**：能指着回答里的某句话说「这里就是它」。禁止概括成「解释了原理」
   这种空话，要写出它具体说了什么。
2. **可核对**：含具体的数值、名称、结论、公式、步骤。
3. **与题目相关**：回答里跑题的部分不要抽。

【关键：锚定回答不是满分答案】
它只是**一份**回答，可能漏、可能错。所以：
- 你认为某个锚点内容**可能是错的** → 照样抽出来，但 `sound` 填 false 并说明疑点。
  下游会避免把它写成准则。
- 你发现这道题**明显该答但这份回答没答**的方面 → 写进 `gaps`。
  这是评分标准要覆盖的，恰恰是这份回答的不足。

【answer 字段】
如果这道题有唯一确定的答案（数学题的解、代码题的输出、选择题的选项），
把它从回答里摘出来填进 `answer`，并在 `answer_sound` 标你是否认可它。
没有唯一答案的开放题，`answer` 填空串。
这个字段最重要 —— 下游写「最终答案为 X」这类准则时直接引用它，不再凭记忆编。

【answer_kind / answer_canonical：给程序化核验用，判错代价很大，从严】
下游会拿 `answer_canonical` 去和待评回答做**字符串比对**来自动判分，不过模型。
比对错了会把正确回答判成错、或把错误回答判成对，所以**宁可留空也不要勉强填**。

  answer_kind 从下列里选：
    numeric     纯数值结论（含单位也算）。例：`7cm`、`38`、`1.5×10^3 Pa`
    option      选择题选项。例：`B`、`AC`
    token       **嵌在句子里的短标识**：IP、参数值、配置项、函数名、术语。
                例：`0.0.0.0`、`--host`、`O(n log n)`、`NaHCO3`
                判定方式是「回答里出现过这个串」，所以它必须足够特征化 ——
                像 `1`、`是`、`true` 这种到处都会出现的，改填 none
    formula     化学式/SMILES/数学表达式 —— 这类有多种等价写法，**不做程序化比对**
    exact_text  **整段必须逐字一致**的输出：解码结果、程序输出、完整命令行。
                判定方式是「回答里有一整行（或连续几行）与它完全相同」，
                多一个字符少一个字符都算错。
                ⚠️ 只有当答案本身就是「一段要照抄的文本」时才用它。
                如果答案是嵌在说明里的短标识（如 `0.0.0.0`），用 token，
                否则会因为它总是出现在句子中间而永远判不中。
    none        开放题，或答案是一句话/一段描述，无法用字符串判定

  answer_canonical：**最小的、能唯一判定对错的那个串**，不要带解释性文字。
    ✅ `0.0.0.0`                    （问「配什么 IP」）
    ✅ `Test_SR:00001.020486R\nWrite_IO:0,1,1,1,0,0,0,0,0,0,0,0,0,0,0`
    ❌ `将 host 配置为 0.0.0.0，例如 uvicorn main:app --host 0.0.0.0`
       ← 这是一句话，不同回答措辞不同，逐字比对必然误判
    kind 为 formula 或 none 时，`answer_canonical` 一律填空串。

  判断标准：如果你无法确定「另一份同样正确的回答里，这个串会原样出现」，
  就把 kind 填 none。

只输出 JSON：
{{"anchors": [{{"point": "这份回答具体说了什么，不超过60字",
               "sound": true, "note": "若 sound=false，一句话说疑点"}}],
  "answer": "唯一答案；开放题留空",
  "answer_sound": true,
  "answer_kind": "numeric|option|token|formula|exact_text|none",
  "answer_canonical": "最小可判定串；formula/none 填空串",
  "gaps": ["该答但这份回答没覆盖的方面，不超过30字"]}}'''

# 只有这两类做程序化比对。formula 等价写法太多（SMILES 可以有多种合法表示），
# none 是自然语言，都退回 LLM 判分。
PROGRAM_KINDS = ('numeric', 'option', 'token', 'exact_text')


def anchor_of(r):
    """挑锚定回复。返回 (键名, 文本, 是否与待评共用)。

    排序后取第 1 条作锚，第 2 条留给步骤 10 当待评对象 —— 硬约束第 1 条。
    键名排序保证同一题每次跑拿到的是同一条（否则缓存全废）。
    """
    refs = r.get('ref_responses') or {}
    keys = sorted(refs)
    if not keys:
        return '', '', False
    return keys[0], str(refs[keys[0]] or ''), len(keys) < 2


def build(r, anchor_text):
    q = (r.get('query_eff') or r['question'])[:1500]
    subj = ' / '.join(r.get('subject') or []) or '未标注'
    return [{'role': 'system', 'content': SYS},
            {'role': 'user', 'content':
                f'【学科】{subj}\n'
                f'【提问意图】{r.get("intent", "")}\n'
                f'【题型】{r.get("question_type", "")} → {r.get("rubric_form", "")}\n\n'
                f'【题目】\n{q}\n\n'
                f'【锚定回答】（一份实际回答，不是满分答案）\n{anchor_text[:6000]}'}]


def parse(obj):
    out = []
    for a in (obj.get('anchors') or [])[:N_MAX]:
        if not isinstance(a, dict):
            continue
        p = str(a.get('point', '')).strip()
        if not p:
            continue
        out.append({'point': p[:200],
                    'sound': bool(a.get('sound', True)),
                    'note': str(a.get('note', ''))[:100]})
    gaps = [str(g)[:80] for g in (obj.get('gaps') or [])[:6] if str(g).strip()]
    return out, gaps


def main():
    m = stage.pick('RP_M_GROUND', 'grounder')
    recs = stage.read_jsonl(SRC)
    print(f'步骤 5L Response Grounding: {len(recs)} 条, 源={SRC}, 锚定模型={m.name}')
    n_shared = sum(1 for r in recs if anchor_of(r)[2])
    print(f'  单回复题（锚与待评共用）: {n_shared}/{len(recs)}'
          f'  ← 违反硬约束第 1 条，已标 anchor_shared')

    def one(r):
        key, txt, shared = anchor_of(r)
        if not txt.strip():
            return r['rid'], {'anchors': [], 'gaps': [], 'answer': '',
                              'anchor_key': '', 'anchor_shared': False,
                              'anchor_missing': True}
        obj, _ = stage.json_call(m, build(r, txt), stage='s05L', thinking=THINK)
        anchors, gaps = parse(obj)

        kind = str(obj.get('answer_kind', 'none') or 'none').strip().lower()
        if kind not in ('numeric', 'option', 'token', 'formula', 'exact_text', 'none'):
            kind = 'none'
        canon = str(obj.get('answer_canonical', '') or '').strip()[:300]
        # 兜底：模型可能填了 formula/none 却仍给 canonical，或反过来。
        # 判错代价大，这里从严 —— 不属于可比对类型就清空，避免下游误用。
        if kind not in PROGRAM_KINDS:
            canon = ''
        elif not canon:
            kind = 'none'

        return r['rid'], {
            'anchors': anchors, 'gaps': gaps,
            'answer': str(obj.get('answer', ''))[:300],
            'answer_sound': bool(obj.get('answer_sound', True)),
            'answer_kind': kind, 'answer_canonical': canon,
            'anchor_key': key, 'anchor_shared': shared, 'anchor_missing': False}

    done, errs = stage.run(one, recs, workers=WORKERS, desc='s05L')
    by_rid = dict(done)

    res = [{**r, **by_rid.get(r['rid'], {'anchors': [], 'gaps': [], 'answer': '',
                                         'anchor_missing': True})} for r in recs]
    stage.write_jsonl(OUT, res)

    na = [len(r.get('anchors') or []) for r in res]
    print(f'\n=== 步骤 5L 结果 ===')
    if errs:
        print(f'  失败        : {len(errs)} 条')
    print(f'  锚点/题     : min={min(na)} max={max(na)} mean={sum(na) / len(na):.1f}')
    print(f'  有唯一答案  : {sum(1 for r in res if r.get("answer"))} 题'
          f'（下游写「最终答案为X」时直接引用，不再凭记忆）')
    kinds = Counter(r.get('answer_kind', 'none') for r in res)
    n_prog = sum(1 for r in res if r.get('answer_canonical'))
    print(f'  answer_kind : ' + '  '.join(f'{k}={v}' for k, v in kinds.most_common()))
    print(f'  可程序化核验: {n_prog} 题（其余走 LLM 判分，避免字符串比对误判）')
    unsound = sum(1 for r in res for a in (r.get('anchors') or []) if not a['sound'])
    print(f'  存疑锚点    : {unsound} 条（sound=false，下游避免写成准则）')
    print(f'  gaps        : {sum(len(r.get("gaps") or []) for r in res)} 条'
          f'（锚定回答没覆盖但该答的，正是 rubric 的价值所在）')
    print(f'  无锚可用    : {sum(1 for r in res if r.get("anchor_missing"))} 题')

    ex = next((r for r in res if r.get('anchors')), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]} (锚={ex.get("anchor_key")}, '
              f'shared={ex.get("anchor_shared")}):')
        if ex.get('answer'):
            print(f'    answer: {ex["answer"][:80]}  (sound={ex.get("answer_sound")})')
        for a in ex['anchors'][:5]:
            mark = '' if a['sound'] else '  ⚠️ ' + a['note'][:40]
            print(f'    · {a["point"][:64]}{mark}')
        for g in (ex.get('gaps') or [])[:3]:
            print(f'    gap: {g[:60]}')


if __name__ == '__main__':
    main()
