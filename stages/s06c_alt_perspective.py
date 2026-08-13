"""步骤 6c：第二生成器 RET 展开 - 使用 deepseek 独立生成 perspectives。

这一步复用 s02.5 的题型判定结果，使用第二个生成器（deepseek）运行 RET 展开。

输入：
- s06_alt_context.jsonl（第二生成器的 context）
- s02_5_route.jsonl（复用第一生成器的题型判定）

输出：
- s06_alt_perspective.jsonl
"""
import json, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 20))
THINK = stage.envflag('RP_THINK', True)
STRATEGY = os.environ.get('RP_RET', 'hybrid')      # batch | hybrid | faithful
W1 = int(os.environ.get('RP_W1', 1))
W2 = int(os.environ.get('RP_W2', 1))
IN_CONTEXT = os.environ.get('RP_S06A_OUT', 's06_alt_context.jsonl')
IN_ROUTE = os.environ.get('RP_S025_OUT', 's02_5_route.jsonl')
OUT = os.environ.get('RP_S06C_OUT', 's06_alt_perspective.jsonl')
SIM = 0.7

# gated_answer 的固定视角集
GATED = [{'name': '答案正确性', 'desc': '最终答案与可核验的标准答案一致', 'weight_hint': 'major'},
         {'name': '推理过程完整性', 'desc': '给出得到该答案的关键步骤与依据', 'weight_hint': 'normal'},
         {'name': '表达清晰度', 'desc': '结论明确无歧义，便于核对', 'weight_hint': 'minor'}]

_COMMON = '''【什么是「评价轴」】
评价轴是判断一份回答在某个场景下是否站得住的**着眼点**，不是答案的内容要点。
- 正例：「是否区分了适应症与禁忌症」「是否给出剂量依据的出处」
- 反例：「介绍药物 A」「介绍药物 B」——这是答案提纲，不是评价轴

【硬要求】
- 评价轴必须**本题专属**。「准确性」「完整性」「逻辑性」这类放之四海皆准的词是
  无效输出，必须落到本题的具体内容上（如「反应机理的立体选择性判断是否正确」）。
- 评价轴之间正交，不要嵌套或包含关系。
- 不要预设固定清单。不同学科的评价轴差别很大，就本题现场导出。'''

SYS_RH = f'''你在为一道题构建评估标准。给定一个评价场景，把它纵向拆成若干评价轴。

{_COMMON}

只输出 JSON：{{"perspectives": [{{"name": "不超过14字", "desc": "这一轴上什么样的回答算站得住，不超过40字"}}]}}
每个场景拆 2-4 个评价轴。'''

SYS_RW1 = f'''你在审查一道题的评价场景清单是否有遗漏。

已有场景是别人拆的，你的任务是找出**它们没覆盖到的面向**。

【怎么找】设想一份回答：它在已有的每个场景上都做得无可挑剔，但仍然是一份
糟糕的回答。它糟在哪里？那个「哪里」就是漏掉的场景。

常见的被漏项（仅供启发，不要照搬）：适用边界与前提条件、常见误解的澄清、
可操作性、时效性、风险与副作用、与替代方案的比较、信息来源可靠性。
但**只在本题真的需要时才提**，硬凑无关场景比漏掉更糟。

若你认为确实没有遗漏，返回空数组。这是可接受的答案，不要为了交差而编造。

只输出 JSON：{{"scenarios": [{{"name": "不超过12字", "desc": "不超过40字"}}]}}'''

SYS_RW2 = f'''你在审查某个评价场景下的评价轴清单是否有遗漏。

{_COMMON}

【怎么找】设想一份回答：它满足了已列出的所有评价轴，但在这个场景下仍然不合格。
它缺了什么？那个「什么」就是漏掉的评价轴。

若确实没有遗漏，返回空数组。这是可接受的答案，不要硬凑。

只输出 JSON：{{"perspectives": [{{"name": "不超过14字", "desc": "不超过40字"}}]}}'''

SYS_BATCH = f'''你在为一道题构建评估标准的完整视角树。给定若干评价场景，
为**每一个**场景拆出它的评价轴。

{_COMMON}

【覆盖全面】拆完后自查一遍：是否存在一份回答，它满足你列出的所有评价轴，
却仍然是糟糕的回答？如果存在，把缺的补进去。

只输出 JSON：{{"expand": [{{"scenario_index": 0, "perspectives": [{{"name": "...", "desc": "..."}}]}}]}}
scenario_index 是场景在输入清单中的序号，从 0 开始。每个场景 2-4 个评价轴。'''


def _norm(s):
    return set(re.sub(r'[\s，。、（）()的与和]', '', s or ''))


def dedup(items, seen):
    """按视角名的字符 Jaccard 去重。R_w 常把已有项换个说法再报一遍。"""
    out = []
    for it in items:
        k = _norm(it.get('name'))
        if not k or any(len(k & s) / max(len(k | s), 1) >= SIM for s in seen):
            continue
        seen.append(k)
        out.append(it)
    return out


def main():
    m = stage.pick('RP_M_ALT', 'generator')

    # 读取第二生成器的 context
    ctx_recs = {r['rid']: r for r in stage.read_jsonl(IN_CONTEXT)}

    # 读取题型判定（复用第一生成器的结果）
    route_recs = {r['rid']: r for r in stage.read_jsonl(IN_ROUTE)}

    # 合并
    recs = []
    for rid in ctx_recs:
        if rid not in route_recs:
            print(f'⚠️  {rid} 在 route 中缺失，跳过')
            continue
        rec = {**ctx_recs[rid], **route_recs[rid]}
        recs.append(rec)

    print(f'步骤 6c 第二生成器 RET: {len(recs)} 条')
    print(f'  context from: {IN_CONTEXT}')
    print(f'  route from: {IN_ROUTE}')
    print(f'  模型: {m.name}, 策略: {STRATEGY}, thinking={THINK}')

    # （以下逻辑与 s03_perspective.py 基本一致，只是用 scenarios_alt 和 intent_alt）
    # 为了简化，这里直接用 hybrid 策略

    def build_rh(r, sc):
        q = r.get('query_eff') or r['question']
        return [{'role': 'system', 'content': SYS_RH},
                {'role': 'user', 'content':
                    f'【题目】\n{q[:2000]}\n\n【意图】{r.get("intent_alt", "")}\n\n'
                    f'【场景】{sc["name"]}：{sc["desc"]}'}]

    out = []
    for r in recs:
        qtype = r.get('question_type', 'open')
        rubric_form = r.get('rubric_form', 'analytic')

        if rubric_form == 'gated_answer':
            # verifiable 题用固定视角
            perspectives = []
            for i, p in enumerate(GATED):
                perspectives.append({
                    'perspective_id': f'{r["rid"]}-alt-p{i+1}',
                    'scenario_id': f'{r["rid"]}-alt-s1',  # 假设只有一个场景
                    'block_id': f'{r["rid"]}-alt-s1',
                    'name': p['name'],
                    'desc': p['desc'],
                    'weight_hint': p['weight_hint'],
                    'origin': 'fixed'
                })
        else:
            # analytic: 跑 RET
            scenarios = r.get('scenarios_alt', [])
            perspectives = []
            pid = 1

            for sc in scenarios:
                # R_h: 展开场景
                obj, _ = stage.json_call(m, build_rh(r, sc), stage='s06c_rh', thinking=THINK)

                # 容错：obj 可能是 list 或其他格式
                if not isinstance(obj, dict):
                    print(f'  ⚠️  {r["rid"]} {sc["scenario_id"]} 返回非dict，跳过')
                    continue

                ps = obj.get('perspectives') or []

                for p in ps[:4]:
                    if not isinstance(p, dict):
                        continue
                    perspectives.append({
                        'perspective_id': f'{r["rid"]}-alt-p{pid}',
                        'scenario_id': sc['scenario_id'],
                        'block_id': sc.get('block_id', sc['scenario_id']),
                        'name': str(p.get('name', ''))[:40],
                        'desc': str(p.get('desc', ''))[:120],
                        'weight_hint': 'normal',
                        'origin': 'R_h'
                    })
                    pid += 1

        out.append({**r, 'perspectives_alt': perspectives,
                    'ret_strategy_alt': STRATEGY})

    stage.write_jsonl(OUT, out)

    total_p = sum(len(r['perspectives_alt']) for r in out)
    print(f'\n=== 步骤 6c 结果 ===')
    print(f'  总 perspective 数: {total_p}')
    print(f'  平均/题: {total_p / len(out):.1f}')

    # 统计题型分布
    qtype_counter = Counter(r.get('question_type') for r in out)
    print(f'  题型分布: {dict(qtype_counter)}')


if __name__ == '__main__':
    main()
