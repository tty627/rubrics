"""步骤 2.5：题型判定与路由 —— verifiable / open / hybrid → rubric_form。

流程位置见 docs/design/rubric_pipeline_feishu_v2.md §2.5。这一步是对三篇骨架论文的扩展，
它们都假定任务是 open（verifiable 交给 RLVR），我们要建统一能力所以在流程内分流。

不分流的代价很具体：数学题被强行套多视角展开，会产出「推理严谨度」
「数学表达规范性」这类次要准则，而「答案对不对」这个主准则被稀释。
verifiable 任务本质是 k=1、w=1 的单准则 rubric，硬拆成多维是错配。

**刻意不把草稿 rubric 自带的 open/closed 标签喂给模型**：那是另一套二分法
（且全量 453 条只有 open/closed 两值），喂进去会把模型锚死在原有划分上，
判定就失去了独立性。它只在事后作为对照打印，用于人工核对检查点。
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 20))
THINK = stage.envflag('RP_THINK', True)
TYPES = ['verifiable', 'open', 'hybrid']
FORM = {'verifiable': 'gated_answer', 'open': 'analytic', 'hybrid': 'multi_part'}

SYS = '''判定一道题的评价形态，路由到对应的 rubric 生成策略。

【判定依据，按此顺序问自己】
1. 这题有没有唯一正确答案，或能否通过程序、公式、查证核验对错？
2. 答案空间是离散有限（选择、填空、数值、判断），还是连续开放？
3. 评价标准是客观可验证，还是主观多维？

【三个标签】
- verifiable：有确定答案且可核验。数学计算、代码正确性、单选多选、
  事实性问答（「法国首都是哪里」）、化学反应产物、单位换算。
- open：无唯一答案，需多维度评价。创作、建议、分析、解释、方案设计、
  概念阐述（「如何提升团队士气」「解释一下 Brep 结构」）。
- hybrid：有确定性核心 + 开放性展开，或题面含多个子问题且子问题形态不同。
  例：「证明费马大定理并讨论其历史意义」「先算出答案，再说明这个结论对工程的启示」。

【关键区分，容易判错的地方】
- 「解释某个概念」通常是 open，不是 verifiable。概念解释没有唯一答案，
  好坏体现在准确性、完整性、可理解性多个维度上。只有当问的是一个
  离散事实点（「氢的原子序数是几」）才算 verifiable。
- 题面有多个问号不一定是 hybrid。若几个子问题形态相同（都在问概念解释），
  仍判 open；只有形态不同（一个要算、一个要论）才判 hybrid。
- 有标准答案但答案是一段论述（如「简述牛顿第二定律」），判 open 不判 verifiable。
  可核验指的是能机械比对，不是「专家能判断对错」。

【blocks 怎么给】
- 判 hybrid 时，blocks **必须至少两个**。hybrid 的定义就是形态不同的两部分并存，
  确定性核心一个 block、开放展开一个 block。这里不是按「题面有几个问号」拆，
  而是按**形态**拆：一道单选题要求「给出答案并逐句拆解讲解」，答案是一个
  verifiable block，讲解是一个 open block。拆不出两个就说明它不是 hybrid，
  应改判 verifiable 或 open。
- 判 verifiable 或 open 时，若题面含多个独立子问题，也要逐个列出，
  一个子问题一个 block；只有单一问题才给空数组。

【置信度】confidence 取 high / medium / low。判定依据之间打架时给 low——
低置信度的条目后续会单独复核，不要为了显得确定而硬给 high。

只输出 JSON：
{"question_type": "verifiable|open|hybrid",
 "confidence": "high|medium|low",
 "reason": "一句话，不超过40字",
 "blocks": [{"desc": "该子问题在问什么，不超过25字", "block_type": "verifiable|open"}]}'''


REPAIR = '''你把这道题判为 hybrid，但没有给出至少两个 block。

hybrid 意味着确定性核心与开放展开并存，因此必然拆得出两个 block：
一个装可核验的那部分，一个装需要论述的那部分。

请重判。若你确认它拆不出两个 block，那它就不是 hybrid，改判 verifiable 或 open。

只输出 JSON：{"question_type": "verifiable|open|hybrid",
 "blocks": [{"desc": "不超过25字", "block_type": "verifiable|open"}]}'''


def norm_block(b, bid):
    """归一化一个 block。模型可能给字符串而非对象，block_type 也可能越界。"""
    if not isinstance(b, dict):
        return {'block_id': bid, 'desc': str(b)[:80], 'block_type': 'open'}
    bt = b.get('block_type')
    return {'block_id': bid, 'desc': str(b.get('desc', ''))[:80],
            'block_type': bt if bt in ('verifiable', 'open') else 'open'}


def build(r):
    q = r.get('query_eff') or r['question']
    subj = ' / '.join(r.get('subject') or []) or '未标注'
    u = f'【学科】{subj}\n【提问意图】{r.get("intent", "")}\n\n【题目】\n{q[:4000]}'
    return [{'role': 'system', 'content': SYS}, {'role': 'user', 'content': u}]


def main():
    m = stage.pick('RP_M_ROUTE', 'generator')
    recs = stage.read_jsonl('s02_context.jsonl')
    print(f'步骤 2.5 题型路由: {len(recs)} 条, 模型={m.name}, thinking={THINK}')

    def work(r):
        obj, meta = stage.json_call(m, build(r), stage='s02_5', thinking=THINK)
        qt = obj.get('question_type')
        if qt not in TYPES:
            qt = 'open'                            # 越界一律落 open：多展开几个维度
        blocks = obj.get('blocks') or []            # 的代价，远小于把开放题误判成
        if not isinstance(blocks, list):            # gated_answer 而丢掉整片维度
            blocks = []
        bl = [norm_block(b, f'{r["rid"]}-b{i + 1}') for i, b in enumerate(blocks[:12])]

        # hybrid 却没拆出 block：追一轮修复。multi_part 带空 block 会让后续步骤
        # 拿不到可迭代的子块而静默压平，正是检查点 4 要防的情形
        repair = ''
        if qt == 'hybrid' and len(bl) < 2:
            obj2, meta = stage.json_call(
                m, build(r) + [{'role': 'assistant', 'content': json.dumps(obj, ensure_ascii=False)},
                               {'role': 'user', 'content': REPAIR}],
                stage='s02_5', thinking=THINK)
            qt2 = obj2.get('question_type')
            b2 = [norm_block(b, f'{r["rid"]}-b{i + 1}')
                  for i, b in enumerate((obj2.get('blocks') or [])[:12])]
            if qt2 in TYPES and (qt2 != 'hybrid' or len(b2) >= 2):
                qt, bl, repair = qt2, b2, f'hybrid缺block→重判{qt2}'
            else:
                bl, repair = b2, 'hybrid缺block→修复后仍不足'

        # 多子题即便类型判成单一形态，也得走 multi_part，否则子题会被压平成并列准则
        form = FORM[qt]
        if len(bl) >= 2 and form != 'multi_part':
            form = 'multi_part'
        if form == 'multi_part' and len(bl) < 2:
            # 兜底：宁可多展开几个维度，也不要把带确定性核心的题丢进空壳 multi_part
            form, repair = 'analytic', (repair or '') + '|降级analytic'
        if form != 'multi_part':
            bl = []
        return {**r, 'question_type': qt, 'rubric_form': form,
                'route_confidence': obj.get('confidence', 'medium'),
                'route_reason': obj.get('reason', ''), 'blocks': bl,
                'route_repair': repair, '_meta': meta}

    out, _ = stage.run(work, recs, workers=WORKERS, desc='s02_5')
    stage.stat_cached([r.pop('_meta') for r in out])
    stage.write_jsonl('s02b_route.jsonl', out)

    qt = Counter(r['question_type'] for r in out)
    fm = Counter(r['rubric_form'] for r in out)
    cf = Counter(r['route_confidence'] for r in out)
    print('\n=== 步骤 2.5 结果 ===')
    print(f'  question_type : {dict(qt)}')
    print(f'  rubric_form   : {dict(fm)}')
    print(f'  置信度        : {dict(cf)}')
    mp = [r for r in out if r['rubric_form'] == 'multi_part']
    print(f'  multi_part    : {len(mp)} 条，block 数 {[len(r["blocks"]) for r in mp]}')
    empty = [r['rid'] for r in mp if len(r['blocks']) < 2]
    print(f'  检查点4 空壳block: {empty or "无"}  (multi_part 必须 ≥2 block)')
    rp = [r for r in out if r.get('route_repair')]
    if rp:
        print(f'  触发修复      : {[(r["rid"], r["route_repair"]) for r in rp]}')
    if len(cf) == 1:
        print(f'  ⚠ 置信度只有一个取值 {list(cf)}，该信号无区分度，'
              f'PLAN 待定项「置信度阈值」暂时无从定')

    # 与草稿标签的交叉表：草稿只有 open/closed 两值，对不上是预期的，
    # 这张表用于人工核对检查点「题型判定错 ≤2 条」，不是准确率
    x = Counter((( r.get('draft_rubric') or {}).get('question_type'), r['question_type'])
                for r in out)
    print('\n  草稿标签 × 本步判定（供人工核对，非准确率）：')
    for (a, b), n in sorted(x.items(), key=lambda kv: -kv[1]):
        print(f'    {str(a):<8} → {b:<12}{n}')
    print('\n  逐条（核对用）：')
    for r in out:
        star = '*' if r['route_confidence'] == 'low' else ' '
        print(f'  {star}{r["rid"]} {r["question_type"]:<11}{r["rubric_form"]:<14}'
              f'{r["route_confidence"]:<8}{r["question"][:30]!r} | {r["route_reason"][:28]}')


if __name__ == '__main__':
    main()
