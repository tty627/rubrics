"""步骤 3：Perspective Elicitation —— RET 的 R_h（层次展开）+ R_w（水平展开）。

流程位置见 docs/design/rubric_pipeline_feishu_v2.md §3。**这一步是维度多样性的唯一来源**：
种子集 2765 条准则的维度去重数只有 1，病根就在没有这一层。

三种执行策略（RP_RET 切换），对应 docs/design/PLAN.md §3.1 那个待拍板的选择：
  batch    一次调用出整棵树，R_w 退化成 prompt 里一句「请覆盖全面」  ~1 次/题
  hybrid   R_h 批量、R_w 忠实（ℓ=1 与 ℓ=2 分别单独调用）           ~5-6 次/题
  faithful 每个树操作一次调用                                      ~9-12 次/题
R_w 是选 Qworld 骨架的唯一理由，若它净增视角接近 0，batch 就够了，
这一步的产出里带 origin 字段专门用来量这件事。

按 rubric_form 分流（步骤 2.5 的路由结果）：
  analytic     完整 RET
  gated_answer 固定 3 视角，不跑 R_w —— verifiable 任务本质是 k=1 单准则，
               强行多维展开会稀释「答案对不对」这个主准则
  multi_part   分 block：block 充当 ℓ=1 单元，verifiable block 用固定视角、
               open block 跑完整 RET
"""
import json, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 20))
THINK = stage.envflag('RP_THINK', True)
STRATEGY = os.environ.get('RP_RET', 'hybrid')      # batch | hybrid | faithful | lean
W1 = int(os.environ.get('RP_W1', 1))               # ℓ=1 水平展开轮数（lean 下恒 0）
W2 = int(os.environ.get('RP_W2', 1))               # ℓ=2 水平展开轮数（lean 下恒 0）
OUT = os.environ.get('RP_S03_OUT', f's03_perspective_{STRATEGY}.jsonl')
SIM = 0.7                                          # 视角名去重的字符 Jaccard 阈值

# gated_answer 的固定视角集，占分比例见 docs §2.5 表（答案正确性 60-80%）
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

# lean：只保留「该答到什么」的骨架轴。与 SYS_BATCH 的差别是数量收紧到 1-2、
# 且去掉「覆盖全面」自查——那句自查是膨胀的主要来源之一（它鼓励模型补边角）。
# 实测 hybrid 下 R_w 净增 61% 视角、最终 30.5 条准则/题，远超 §2.5 给的 5-8 条目标。
SYS_LEAN = f'''你在为一道题构建评估标准。给定若干评价场景，
为每一个场景拆出**最核心**的评价轴。

{_COMMON}

【只要骨架】
判据是一句话：「一份回答如果在这一轴上不合格，它还算合格吗？」
- 不合格 → 这是骨架轴，保留
- 仍合格 → 是细节加分项，不要输出

**每个场景只出 1-2 个轴**。宁少不多：全题的轴最终会变成 6-8 条准则，
轴太多会逼出过细的准则。不要为了覆盖边角而硬凑。

只输出 JSON：{{"expand": [{{"scenario_index": 0, "perspectives": [{{"name": "...", "desc": "..."}}]}}]}}
scenario_index 是场景在输入清单中的序号，从 0 开始。'''


def _norm(s):
    return set(re.sub(r'[\s，。、（）()的与和]', '', s or ''))


def dedup(items, seen):
    """按视角名的字符 Jaccard 去重。R_w 常把已有项换个说法再报一遍。"""
    out = []
    for it in items:
        k = _norm(it.get('name'))
        if not k:
            continue
        if any(len(k & s) / max(len(k | s), 1) >= SIM for s in seen):
            continue
        seen.append(k)
        out.append(it)
    return out


def _ctx(r):
    q = (r.get('query_eff') or r['question'])[:2500]
    return f'【学科】{" / ".join(r.get("subject") or []) or "未标注"}\n' \
           f'【提问意图】{r.get("intent", "")}\n' \
           f'【隐性约束】{json.dumps(r.get("implicit_constraints", {}), ensure_ascii=False)}\n\n' \
           f'【题目】\n{q}'


class Ctr:
    """本题的调用计数，用于对比三种策略的真实成本。"""
    def __init__(self):
        self.n = 0

    def call(self, m, msgs, **kw):
        self.n += 1
        obj, _ = stage.json_call(m, msgs, stage=f's03_{STRATEGY}', thinking=THINK, **kw)
        return obj


def expand_units(m, r, units, c):
    """把 ℓ=1 单元（场景或 block）展开成视角。units: [{'id','name','desc'}]"""
    got = {u['id']: [] for u in units}
    sys_prompt = SYS_LEAN if STRATEGY == 'lean' else SYS_BATCH
    if STRATEGY in ('batch', 'lean') or (STRATEGY == 'hybrid' and len(units) > 1):
        lst = '\n'.join(f'{i}. {u["name"]}：{u["desc"]}' for i, u in enumerate(units))
        obj = c.call(m, [{'role': 'system', 'content': sys_prompt},
                         {'role': 'user', 'content': f'{_ctx(r)}\n\n【评价场景】\n{lst}'}])
        for e in obj.get('expand') or []:
            i = e.get('scenario_index')
            if isinstance(i, int) and 0 <= i < len(units):
                got[units[i]['id']] = e.get('perspectives') or []
    else:
        for u in units:
            obj = c.call(m, [{'role': 'system', 'content': SYS_RH},
                             {'role': 'user', 'content':
                                 f'{_ctx(r)}\n\n【本次要拆的场景】{u["name"]}：{u["desc"]}'}])
            got[u['id']] = obj.get('perspectives') or []
    return got


def ret(m, r, units, c, run_rw):
    """对一组 ℓ=1 单元跑 RET，返回视角列表。units 为场景或 open block。"""
    if not units:
        return []
    # R_h：ℓ=1 → ℓ=2
    got = expand_units(m, r, units, c)
    persp = [dict(p, scenario_id=u['id'], origin='R_h')
             for u in units for p in (got.get(u['id']) or []) if isinstance(p, dict)]

    if not run_rw:
        return persp

    # R_w ℓ=1：还漏了什么场景。只在 ℓ=2 跑会漏掉整个场景维度，所以这一层必须跑
    seen1 = [_norm(u['name']) for u in units]
    new_units = []
    for _ in range(W1):
        lst = '\n'.join(f'- {u["name"]}：{u["desc"]}' for u in units + new_units)
        obj = c.call(m, [{'role': 'system', 'content': SYS_RW1},
                         {'role': 'user', 'content': f'{_ctx(r)}\n\n【已有场景】\n{lst}'}])
        fresh = dedup([s for s in (obj.get('scenarios') or []) if isinstance(s, dict)], seen1)
        for j, s in enumerate(fresh):
            new_units.append({'id': f'{r["rid"]}-sw{len(new_units) + 1}',
                              'name': str(s.get('name', ''))[:40],
                              'desc': str(s.get('desc', ''))[:120]})
    if new_units:
        got2 = expand_units(m, r, new_units, c)
        persp += [dict(p, scenario_id=u['id'], origin='R_w_l1')
                  for u in new_units for p in (got2.get(u['id']) or []) if isinstance(p, dict)]

    # R_w ℓ=2：每个场景下还漏了什么评价轴——并行跑，否则场景多的题会卡很久
    def rw_l2_one_unit(u):
        cur = [p for p in persp if p['scenario_id'] == u['id']]
        seen2 = [_norm(p.get('name')) for p in cur]
        add = []
        for _ in range(W2):
            lst = '\n'.join(f'- {p.get("name")}：{p.get("desc", "")}' for p in cur) or '（空）'
            obj = c.call(m, [{'role': 'system', 'content': SYS_RW2},
                             {'role': 'user', 'content':
                                 f'{_ctx(r)}\n\n【场景】{u["name"]}：{u["desc"]}\n\n'
                                 f'【该场景下已有的评价轴】\n{lst}'}])
            fresh = dedup([p for p in (obj.get('perspectives') or []) if isinstance(p, dict)], seen2)
            batch = [dict(p, scenario_id=u['id'], origin='R_w_l2') for p in fresh]
            add += batch
            cur += batch
        return add

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(units + new_units), 8)) as ex:
        l2_results = list(ex.map(rw_l2_one_unit, units + new_units))
    for batch in l2_results:
        persp += batch
    return persp


def work(m, r):
    c, form = Ctr(), r['rubric_form']
    if form == 'gated_answer':
        persp = [dict(g, scenario_id=(r['scenarios'][0]['scenario_id'] if r['scenarios']
                                      else f'{r["rid"]}-s1'), origin='fixed') for g in GATED]
    elif form == 'analytic':
        units = [{'id': s['scenario_id'], 'name': s['name'], 'desc': s['desc']}
                 for s in r['scenarios']]
        persp = ret(m, r, units, c, run_rw=STRATEGY not in ('batch', 'lean'))
    else:                                          # multi_part：block 充当 ℓ=1 单元
        persp = []
        open_units = [{'id': b['block_id'], 'name': b['desc'], 'desc': b['desc']}
                      for b in r['blocks'] if b['block_type'] == 'open']
        persp += ret(m, r, open_units, c, run_rw=STRATEGY not in ('batch', 'lean'))
        for b in r['blocks']:
            if b['block_type'] == 'verifiable':     # 确定性 block 走 gated_answer 那套
                persp += [dict(g, scenario_id=b['block_id'], origin='fixed') for g in GATED]
        for p in persp:
            p['block_id'] = p['scenario_id']

    # 全题级去重：不同场景导出同名视角时，保留先出现的，避免第 4 步生成重复准则
    seen, keep = [], []
    for p in persp:
        k = _norm(p.get('name'))
        if not k or any(len(k & s) / max(len(k | s), 1) >= SIM for s in seen):
            continue
        seen.append(k)
        keep.append({'perspective_id': f'{r["rid"]}-p{len(keep) + 1}',
                     'scenario_id': p.get('scenario_id', ''),
                     'block_id': p.get('block_id', ''),
                     'name': str(p.get('name', ''))[:40],
                     'desc': str(p.get('desc', ''))[:120],
                     'origin': p.get('origin', ''),
                     'weight_hint': p.get('weight_hint', 'normal')})
    return {**r, 'perspectives': keep, 'ret_strategy': STRATEGY, 'ret_calls': c.n}


def main():
    m = stage.pick('RP_M_GEN', 'generator')
    recs = stage.read_jsonl(os.environ.get('RP_S03_SRC', 's02b_route.jsonl'))
    print(f'步骤 3 视角展开: {len(recs)} 条, 模型={m.name}, 策略={STRATEGY}, '
          f'w1={W1} w2={W2}, thinking={THINK}')

    out, _ = stage.run(lambda r: work(m, r), recs, workers=WORKERS, desc=f's03/{STRATEGY}')
    stage.write_jsonl(OUT, out)

    npp = [len(r['perspectives']) for r in out]
    names = Counter(p['name'] for r in out for p in r['perspectives'])
    origin = Counter(p['origin'] for r in out for p in r['perspectives'])
    calls = sum(r['ret_calls'] for r in out)
    byform = {}
    for r in out:
        byform.setdefault(r['rubric_form'], []).append(len(r['perspectives']))

    print(f'\n=== 步骤 3 结果（策略={STRATEGY}）===')
    print(f'  视角总数      : {sum(npp)}')
    print(f'  视角数/题     : min={min(npp)} p50={sorted(npp)[len(npp) // 2]} '
          f'max={max(npp)} mean={sum(npp) / len(npp):.1f}')
    print(f'  按 form       : ' + '  '.join(
        f'{k}={sum(v) / len(v):.1f}/题({len(v)}条)' for k, v in sorted(byform.items())))
    print(f'  视角名去重    : {len(names)} / {sum(npp)} = {len(names) / max(sum(npp), 1):.0%}')
    print(f'  来源分布      : {dict(origin)}')
    rw = origin.get('R_w_l1', 0) + origin.get('R_w_l2', 0)
    print(f'  R_w 净增视角  : {rw} / {sum(npp)} = {rw / max(sum(npp), 1):.0%}'
          f'   ← PLAN §3.1 的判据：接近 0 则 batch 就够，R_w 不值这个成本')
    print(f'  LLM 调用      : {calls} 次，{calls / len(out):.1f} 次/题')
    print(f'  跨题重名 Top6 : {[(k, v) for k, v in names.most_common(6) if v > 1]}')
    print('\n  抽样一条：')
    ex = max(out, key=lambda r: len(r['perspectives']) if r['rubric_form'] == 'analytic' else 0)
    print(f'    {ex["rid"]} [{ex["rubric_form"]}] {ex["question"][:40]!r}')
    for p in ex['perspectives']:
        print(f'      {p["perspective_id"]} <{p["origin"]:<7}> {p["name"]}: {p["desc"][:40]}')


if __name__ == '__main__':
    main()
