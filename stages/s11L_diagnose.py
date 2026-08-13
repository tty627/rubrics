"""步骤 11L：RIFT 免池诊断（lean 流程版）—— 适配 s04L_rubric.jsonl。

与 s11_diagnose.py 的差异：
1. 输入: s04L_rubric.jsonl（准则在 rubrics[] 里，schema 已规整）
2. 输出: s11L_diagnosed.jsonl（在原记录上添加 diagnoses[] 字段）
3. 准则格式: 已是 criteria/score/dimension/is_positive，不再是 positive/negative 分离

诊断逻辑完全一致：Subjective / Non-Atomic / Ungrounded 三失效模式。
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 20))
THINK = stage.envflag('RP_THINK', True)
SRC = os.environ.get('RP_S11L_SRC', 's04L_rubric.jsonl')

SYS_SUBJ = '''你是 RIFT 诊断器，检测准则是否**主观**（Subjective）。

主观准则：依赖判分者的个人偏好或美学品味，无法客观判定。
例：
  - 「解释生动有趣」←→「解释枯燥」     ← 主观，"生动"无客观标准
  - 「回答优雅简洁」←→「回答冗长」     ← 主观，"优雅"因人而异
  - 「回答给出了R构型的成因」←→「未给出」 ← 客观，可核验

只输出 JSON：{"verdict": "clean|defective", "reason": "若判 defective，一句话说为什么主观，不超过40字"}'''

SYS_ATOM = '''你是 RIFT 诊断器，检测准则是否**非原子**（Non-Atomic）。

非原子准则：一条准则里包含多个**能各自独立成立**的判断点，应拆成多条。

【判定原则——只用这一条】
把准则拆成两半，问：**拆开后的每一半，单独拿出来还是一条有意义、可判定的准则吗？**
- 两半都能独立成立 → 非原子（defective）
- 拆开后有一半失去意义、或两半必须合看才构成一个判断 → 原子（clean）

【原子（clean）——下面这几类不要判 defective】
1. 对比/辨析类：题目问的就是 A 与 B 的区别时，「A 如何、B 如何」合起来才是一个判断点。
   ✅「默认复用无需配置AFIO，部分/完全重映射必须配置」← 题目问的就是三者区别
   ✅「合同负债在履约时转收入，应付账款在支付时减负债」← 题目问的就是两者去向对比
   单说一半（只说 A 怎样）无法回答「区别是什么」，所以不可拆。
2. 排除/对照类：「是 X，而非 Y」是一个判断点。
   ✅「明确结论为反式(trans)构型，而非顺式(cis)」
   ✅「指出两者均为负债类科目，不是资产」
3. 列举+完备：「列出 a/b/c，无遗漏」是一个判断点。
   ✅「列出乾、坤、震、巽等64卦名称，无遗漏」
4. 判断+限定条件：主判断带一个约束修饰时，约束不算独立判断点。
   ✅「在小挠度理论前提下，结论为不能通过」

【非原子（defective）——只有这几类才判】
1. 空泛词并列：两个抽象评价词捆一起。
   ❌「回答准确且完整」← "准确"和"完整"各自都是独立准则
2. 不同层面的内容捆绑：结论和成因、事实和方法，各自都能单独考。
   ❌「给出了R构型及其成因」← "构型对不对"和"成因说没说清"是两道独立的检查
   ❌「识别出萘环、喹唑啉环等主要骨架，并正确描述各环之间的连接关系」
      ← "识别骨架"和"描述连接关系"能各自成立
3. 三个及以上并列的独立要求塞进一条。

⚠️ 宁可判 clean 也不要误判。准则本来就该有一定粒度，
把对比类、排除类误判成非原子会把 rubric 拆碎，反而失去可判定性。

只输出 JSON：{"verdict": "clean|defective", "reason": "若判 defective，一句话说明拆开后的两半分别是什么"}'''

SYS_UNGR = '''你是 RIFT 诊断器，检测准则是否**脱靶**（Ungrounded）。

脱靶准则：与题目要求无关，或超出题目范畴。
例：
  - 问「什么是 Brep」却检查「是否给出了 C++ 代码」← 脱靶，题目没要求代码
  - 问「合同负债和应付账款」却检查「预付款项」← 脱靶，超出范围

只输出 JSON：{"verdict": "clean|defective", "reason": "若判 defective，一句话说为什么脱靶"}'''

# 2026-08-13 新增第四个检测器。
# 起因：实测 318 条 ungrounded 里至少 57 条（17.9%）的判由根本不是「超范畴」，
# 而是「准则本身写错了」—— 模型把事实错误塞进了脱靶这个槽，因为 RIFT 原本的
# 三个检测器没有一个管事实正确性：
#   [q0073] 「最终答案为 k=5」→ 判由「公认答案应为 k=3，准则预设 k=5 属于事实错误」
#   [q0049] 「反映125...K后的局面」→ 判由「第三局PGN中不存在125...K这一步」
# 而且贴错标签会导致错误处置：ungrounded 的动作是删，但「答案写错了」该做的是
# 用正确答案重写 —— 删了这道题就没有答案判据了。
#
# **关键约束**：只有这个检测器拿到真值（draft_rubric）。另外三个判的是形式不是
# 事实，不需要真值 —— 这样它们 7356 次调用的缓存全部保住，只新增这一个模式的量。
#
# 诊断器自己会编：实测 q0045 同一题里，它给一条准则的判由说产物是三种，
# 给另一条说仅两种，自相矛盾。所以强制它交代**判定依据从哪来**，
# basis=model_knowledge 的结论在处置端降级为「待复核」而非直接改写。
SYS_FACT = '''你是 RIFT 诊断器，检测准则陈述的内容**是否与事实不符**（Factually-Wrong）。

事实错误准则：准则里写死的答案、数值、结论、引用是错的，或者引用了题目里
根本不存在的东西。这类准则会把正确回答判成错、把错误回答判成对，危害最大。

例：
  - 题目答案实为 k=3，准则却写「最终答案为 k=5」← 事实错误
  - 准则写「反映 125...K 后的局面」，但题目给的棋谱里没有这一步 ← 引用了不存在的内容
  - 准则写「生成三种产物」，但该反应只生成两种 ← 事实错误

【不属于本检测器的范围，一律判 clean】
- 准则太空泛、太主观 → 那是 Subjective 管的
- 准则捆了多个判断点 → 那是 Non-Atomic 管的
- 准则说的是对的，只是题目没问 → 那是 Ungrounded 管的
- 准则没写出具体答案，只说「与标准答案一致」→ 这是表述问题，不是事实错误

【判定依据必须交代来源，填 basis】
  draft            依据下方【人工草稿准则】—— 最可信
  question         依据题干本身给出的条件/数据
  model_knowledge  依据你自己的领域知识

⚠️ **你自己也会记错**。凡是只能靠 model_knowledge 判定的，把握不足就判 clean。
宁可漏判，不要把一条正确的准则打成事实错误 —— 误判的代价是删掉唯一正确的答案判据。
只有当你能明确指出「正确的应该是什么」时，才判 defective。

只输出 JSON：
{"verdict": "clean|defective",
 "basis": "draft|question|model_knowledge",
 "reason": "若判 defective，一句话说错在哪，不超过50字",
 "correct_value": "若判 defective 且你能确定正确内容，写出来；不确定填空串"}'''


def build(r, c, mode):
    """构建诊断 prompt。"""
    q = (r.get('query_eff') or r['question'])[:800]
    subj = ' / '.join(r.get('subject') or []) or '未标注'
    intent = r.get('intent', '')[:200]

    crit_txt = c['criteria']
    dim = c.get('dimension', '')
    reason = c.get('reason', '')[:100]

    sys_map = {
        'Subjective': SYS_SUBJ,
        'Non-Atomic': SYS_ATOM,
        'Ungrounded': SYS_UNGR,
        'Factual': SYS_FACT,
    }

    user = (f'【学科】{subj}\n'
            f'【提问意图】{intent}\n\n'
            f'【题目】\n{q}\n\n'
            f'【待诊断准则】\n'
            f'维度: {dim}\n'
            f'准则: {crit_txt}\n'
            f'理由: {reason}')

    # 只有事实检测器拿真值。另外三个判形式不判事实，prompt 保持原样，
    # 它们已有的缓存才不会失效。
    if mode == 'Factual':
        draft = r.get('draft_rubric') or {}
        gt = '\n'.join(f'- {x.get("criteria", "")}'
                       for x in (draft.get('rubrics') or [])[:8])
        user += (f'\n\n【人工草稿准则（本题的真值参照，人工写的，比你的记忆可靠）】\n'
                 f'{gt or "（本题无草稿，只能靠题干或你自己的知识判断——请从严，把握不足就判 clean）"}')

    return [{'role': 'system', 'content': sys_map[mode]},
            {'role': 'user', 'content': user}]


def main():
    m = stage.pick('RP_M_DIAGNOSER', 'diagnoser')
    recs = stage.read_jsonl(SRC)
    print(f'步骤 11L RIFT 免池诊断: {len(recs)} 条, 源={SRC}, 诊断器={m.name}, thinking={THINK}')

    nc_total = sum(len(r.get('rubrics', [])) for r in recs)
    print(f'  准则总数: {nc_total}')

    # 摊平到 (题, 准则, 诊断模式)
    MODES = ('Subjective', 'Non-Atomic', 'Ungrounded', 'Factual')
    jobs = []
    for r in recs:
        for c in r.get('rubrics', []):
            cid = c.get('_criterion_id', '')
            if not cid:
                continue
            for mode in MODES:
                jobs.append((r, c, cid, mode))

    print(f'  摊平后任务数: {len(jobs)} ({nc_total} 准则 × {len(MODES)} 模式)')

    def one(job):
        r, c, cid, mode = job
        obj, _ = stage.json_call(m, build(r, c, mode),
                                 stage=f's11L_{mode[:4].lower()}', thinking=THINK)
        verd = obj.get('verdict', 'clean')
        if verd not in ('clean', 'defective'):
            verd = 'clean'
        d = {'verdict': verd,
             'reason': str(obj.get('reason', ''))[:120] if verd == 'defective' else ''}
        if mode == 'Factual' and verd == 'defective':
            b = str(obj.get('basis', '') or 'model_knowledge')
            d['basis'] = b if b in ('draft', 'question', 'model_knowledge') else 'model_knowledge'
            d['correct_value'] = str(obj.get('correct_value', ''))[:200]
        return r['rid'], cid, mode.lower(), d

    done, _ = stage.run(one, jobs, workers=WORKERS, desc='s11L')

    # 汇总诊断结果
    diag = {}
    for rid, cid, mode, d in done:
        diag.setdefault((rid, cid), {})[mode] = d

    # 组装回原记录
    res = []
    defect_total = 0
    mode_stats = Counter()

    for r in recs:
        diagnoses = []
        for c in r.get('rubrics', []):
            cid = c.get('_criterion_id', '')
            if not cid:
                continue

            crit_diag = diag.get((r['rid'], cid), {})
            failure_modes = [m for m in ('subjective', 'non-atomic', 'ungrounded', 'factual')
                           if crit_diag.get(m, {}).get('verdict') == 'defective']

            is_defect = len(failure_modes) > 0
            if is_defect:
                defect_total += 1
                for fm in failure_modes:
                    mode_stats[fm] += 1

            diagnoses.append({
                '_criterion_id': cid,
                'is_defective': is_defect,
                'failure_modes': failure_modes,
                'details': crit_diag,
            })

        res.append({**r, 'diagnoses': diagnoses})

    stage.write_jsonl('s11L_diagnosed.jsonl', res)

    print(f'\n=== 步骤 11L 诊断结果 ===')
    print(f'  defective 准则: {defect_total}/{nc_total} ({defect_total/max(nc_total,1)*100:.1f}%)')
    print(f'  失效模式分布:')
    for mode, n in mode_stats.most_common():
        print(f'    {mode:<12} {n:5d} ({n/max(nc_total,1)*100:4.1f}%)')

    # 事实错误的判定依据分布：model_knowledge 那批不可全信（诊断器自己会记错），
    # 处置端据此降级为「待复核」而非直接改写。
    basis = Counter()
    has_fix = 0
    for r in res:
        for d in r['diagnoses']:
            f = d['details'].get('factual', {})
            if f.get('verdict') == 'defective':
                basis[f.get('basis', '?')] += 1
                if f.get('correct_value'):
                    has_fix += 1
    if basis:
        print(f'\n  事实错误的判定依据:')
        for b, n in basis.most_common():
            tag = ' ← 可直接改写' if b in ('draft', 'question') else ' ← 待人工复核'
            print(f'    {b:<16} {n:5d}{tag}')
        print(f'    其中给出了正确内容(correct_value)的: {has_fix}/{sum(basis.values())}')

    # 抽样展示
    bad_samples = [r for r in res if any(d['is_defective'] for d in r.get('diagnoses', []))]
    if bad_samples:
        ex = bad_samples[0]
        print(f'\n  抽样 {ex["rid"]} (有 defective):')
        for d in ex['diagnoses']:
            if d['is_defective']:
                c = next((c for c in ex['rubrics'] if c.get('_criterion_id') == d['_criterion_id']), None)
                if c:
                    print(f'    ❌ {d["_criterion_id"]}: {c["criteria"][:60]}...')
                    print(f'       失效模式: {", ".join(d["failure_modes"])}')
                    for fm in d['failure_modes']:
                        reason = d['details'].get(fm, {}).get('reason', '')
                        if reason:
                            print(f'         {fm}: {reason[:80]}')


if __name__ == '__main__':
    main()
