"""步骤 4b：核心准则筛选 —— 从细碎准则里收敛出「该答到的基本内容」。

**为什么要这一步**：原流程只有加法没有减法 ——
  R_h 展开 → R_w「还漏了什么」→ 每视角 1-3 条 → s06 取并集 → s07 加 R_dist → s08 加惩罚项
唯一的减法是 s09 同义合并（Jaccard≥0.75 过严，全量只合并掉 23 条）。
结果准则数 30.5 条/题（p50=33，max=82），RIFT non-atomic 命中 80.8%。

导师反馈与锚点集实测指向同一问题：
  「划分太细」→ 准则 30.5 条/题
  「规范太严苛」→ 锚点集外推真 drift 约 48.8%
  「不知道规范是否正确」→ 锚点抓到的幻觉类（如「T1 诊断判断激发态适用性」）

这一步做三件事：
  1. 筛核心：漏了它回答就不合格的，才留。目标 6-10 条
  2. 合并同源：被 non-atomic 判定为「捆了多个点」的，合并为一条粗粒度表述
  3. 剔幻觉：涉及具体机理/数值且题目未给依据的，直接弃

输出为导师指定的 schema（criteria / is_positive / score / reason / dimension），
内部字段以 `_` 前缀保留（血缘标签是设计文档硬约束第 4 条，步骤 13/14 依赖）。
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage, dimensions

WORKERS = int(os.environ.get('RP_WORKERS', 12))
THINK = stage.envflag('RP_THINK', True)
SRC = os.environ.get('RP_S04B_SRC', 's09_normalized.jsonl')
TARGET_MIN = int(os.environ.get('RP_CORE_MIN', 6))
TARGET_MAX = int(os.environ.get('RP_CORE_MAX', 10))

SYS = f'''你在精简一份评分标准（rubric）。现有准则划分过细、部分要求过于严苛，
需要收敛成「一份合格回答该答到的基本内容」。

【核心判据】对每条准则问一句：
  「如果一份回答漏了这条，它还算合格吗？」
  - 漏了就不合格 → 保留
  - 漏了仍然合格 → 删掉（这是细节加分项，不是基本要求）

【必须删掉的三类】
1. **过细的枝节**：要求具体数值、经验公式、特定试剂牌号、专家级细节，
   而题目并未要求这种深度
2. **不确定的机理断言**：断言某个具体机制/因果关系，但这结论依赖未给定的条件
   （温度、气氛、浓度等），或本身在该领域并非共识
   例：「指出高温下漆酚氧化交联成膜导致不溶不熔」——题目未给温度气氛，不能断定
3. **超出题目范围**：题目没问的内容
   例：题目问「合同负债和应付账款」，准则却要求解释「预付款项」

【必须合并的一类】
多条准则测的是同一件事的不同侧面时，合并为一条**粗粒度**表述。
  原：①指出合同负债对应履约义务 ②指出应付账款对应付款义务
  合并：明确区分合同负债的履约义务与应付账款的付款义务

【保留的准则怎么写】
- 覆盖「该答到什么」，允许比原准则更粗，不要追求穷尽细节
- 必须是本题专属的可核对内容，不能是「回答准确完整」这类空泛词
- 一条只测一件事，不要用「且」「并」捆两个独立判断点

【负向准则】
最多保留 1-2 条，只留**真正致命**的错误（答反了、核心概念用错）。
表述直接写错误现象本身。

{dimensions.prompt_block()}

【分值】按题型给，规则不同：

- **verifiable（有唯一正确答案）**：答案正确性必须占总分 60-80%。
  给「最终答案是否正确」这条 6-8 分，其余支撑项各 1 分。
  理由：这类题本质是「答对没答对」，多项均分会稀释主准则。

- **open / hybrid（开放题）**：正向准则 1-3 分。
  核心结论 3 分，重要支撑 2 分，一般要点 1 分。

负向准则一律 -2 或 -3 分。

【数量】保留 {TARGET_MIN}-{TARGET_MAX} 条（含负向）。题目简单可以更少，不要硬凑。

只输出 JSON：
{{"rubrics": [{{"criteria": "不超过70字", "score": 2, "reason": "为什么这条是基本要求，不超过30字", "dimension": "从上表选", "is_positive": true, "from": [1, 3]}}]}}
from 填该条合并自哪几个原准则的编号（从 1 开始），用于追溯血缘。'''


def build(r):
    q = (r.get('query_eff') or r['question'])[:1800]
    subj = ' / '.join(r.get('subject') or []) or '未标注'

    lines = []
    for i, c in enumerate(r['criteria'], 1):
        is_pen = c.get('criterion_type') == 'penalty'
        tag = '负向' if is_pen else '正向'
        # 把 non-atomic 诊断结论带给模型，它比模型自己重判更准
        diag = {k.lower(): v for k, v in (c.get('diagnostics') or {}).items()}
        na = (diag.get('non-atomic') or {}).get('verdict') == 'defective'
        hint = ' ⚠捆了多个判断点' if na else ''
        lines.append(f'{i}. [{tag}{hint}] {c["positive"][:120]}')

    # gated_answer 要把答案项撑到总分 60-80%，得在 prompt 里点明，
    # 否则模型按开放题的 1-3 分给，答案项占比会掉到 40% 左右
    gate_hint = ('  ⚠这是有唯一正确答案的题：答案正确性那条给 6-8 分，'
                 '其余支撑项各 1 分'
                 if r.get('rubric_form') == 'gated_answer' else '')

    user = (f'【学科】{subj}\n'
            f'【提问意图】{r.get("intent", "")}\n'
            f'【题型】{r.get("question_type", "")} → '
            f'{r.get("rubric_form", "")}{gate_hint}\n\n'
            f'【题目】\n{q}\n\n'
            f'【现有 {len(r["criteria"])} 条准则（过细，需收敛）】\n'
            + '\n'.join(lines))

    return [{'role': 'system', 'content': SYS},
            {'role': 'user', 'content': user}]


def main():
    m = stage.pick('RP_M_GEN', 'generator')
    recs = stage.read_jsonl(SRC)
    print(f'步骤 4b 核心筛选: {len(recs)} 条, 源={SRC}, 模型={m.name}')
    n_in = sum(len(r['criteria']) for r in recs)
    print(f'  输入准则: {n_in} 条 (均 {n_in / len(recs):.1f}/题)')
    print(f'  目标: {TARGET_MIN}-{TARGET_MAX} 条/题')

    def one(r):
        obj, _ = stage.json_call(m, build(r), stage='s04b', thinking=THINK)
        raw = obj.get('rubrics') or []
        if not isinstance(raw, list):
            return r['rid'], []

        old = r['criteria']
        is_verifiable = r.get('rubric_form') == 'gated_answer'
        out = []
        for j, c in enumerate(raw, 1):
            if not isinstance(c, dict):
                continue
            txt = str(c.get('criteria', '')).strip()
            if not txt:
                continue
            dim, hit = dimensions.normalize(c.get('dimension'))
            pos = c.get('is_positive')
            pos = True if pos is None else bool(pos)

            try:
                sc = abs(int(round(float(c.get('score', 2))))) or 2
            except (TypeError, ValueError):
                sc = 2
            if pos:
                # verifiable 的答案项要占 60-80%，需要 6-8 分撑起来，
                # 故这类放宽到 8；open/hybrid 仍夹在 1-3。
                sc = min(sc, 8 if is_verifiable else 3)
            else:
                sc = -min(max(sc, 2), 3)      # 负向一律 -2 或 -3

            # 追溯血缘：合并自哪几条原准则
            src_idx = [i for i in (c.get('from') or [])
                       if isinstance(i, int) and 1 <= i <= len(old)]
            src = [old[i - 1] for i in src_idx]

            out.append({
                # ↓ 交付 schema
                'criteria': txt[:200],
                'score': sc,
                'reason': str(c.get('reason', ''))[:100],
                'dimension': dim,
                'is_positive': pos,
                # ↓ 内部字段，不进交付（血缘为硬约束第 4 条，步骤 13/14 依赖）
                '_criterion_id': f'{r["rid"]}-k{j}',
                '_dim_from_table': hit,
                '_merged_from': [c0.get('criterion_id') for c0 in src],
                '_perspective_ids': sorted({c0.get('perspective_id') for c0 in src
                                            if c0.get('perspective_id')}),
                '_scenario_ids': sorted({c0.get('scenario_id') for c0 in src
                                         if c0.get('scenario_id')}),
                # negative 内部保留供判分器界定「什么算不满足」
                '_negative': (src[0].get('negative', '') if src else '')[:200],
            })
        return r['rid'], out

    done, errs = stage.run(one, recs, workers=WORKERS, desc='s04b')
    by_rid = dict(done)

    res = []
    for r in recs:
        core = by_rid.get(r['rid'], [])
        pos = [c for c in core if c['is_positive']]
        res.append({**{k: v for k, v in r.items() if k != 'criteria'},
                    'rubrics': core,
                    '_criteria_full': r['criteria'],      # 保留细粒度版本备查
                    'core_n': len(core),
                    'core_n_positive': len(pos),
                    's_max': sum(c['score'] for c in pos)})
    stage.write_jsonl('s04b_core.jsonl', res)

    kept = [r['core_n'] for r in res if r['core_n']]
    n_out = sum(kept)
    print(f'\n=== 步骤 4b 结果 ===')
    if errs:
        print(f'  失败          : {len(errs)} 条')
    print(f'  输入 → 输出   : {n_in} → {n_out} 条 (压缩 {(1 - n_out / n_in) * 100:.0f}%)')
    if kept:
        print(f'  准则/题       : min={min(kept)} p50={sorted(kept)[len(kept) // 2]} '
              f'max={max(kept)} mean={n_out / len(kept):.1f}')
    empty = sum(1 for r in res if not r['core_n'])
    if empty:
        print(f'  ⚠️  空结果     : {empty} 条')

    allc = [c for r in res for c in r['rubrics']]
    npos = sum(1 for c in allc if c['is_positive'])
    print(f'  正向/负向     : {npos} / {len(allc) - npos}')

    dc = Counter(c['dimension'] for c in allc)
    off = sum(1 for c in allc if not c['_dim_from_table'])
    print(f'  维度命中词表  : {len(allc) - off}/{len(allc)} '
          f'({(len(allc) - off) / max(len(allc), 1) * 100:.1f}%)')
    print(f'  唯一维度数    : {len(dc)} (原 1385 种)')
    print(f'\n  维度分布:')
    for d, n in dc.most_common():
        print(f'    {d:<14} {n:5d} ({n / len(allc) * 100:4.1f}%)')

    ex = next((r for r in res if r['core_n'] >= 6), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]} ({len(ex["_criteria_full"])} → {ex["core_n"]} 条, '
              f'满分 {ex["s_max"]}):')
        for c in ex['rubrics']:
            sign = '+' if c['is_positive'] else '−'
            print(f'    {sign}{abs(c["score"])} [{c["dimension"]}] {c["criteria"][:52]}')


if __name__ == '__main__':
    main()
