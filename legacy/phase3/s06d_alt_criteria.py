"""步骤 6d：第二生成器准则生成 - 使用 deepseek 生成 criteria。

输入：s06_alt_perspective.jsonl
输出：s06_alt_criteria.jsonl
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 20))
THINK = stage.envflag('RP_THINK', True)
IN = os.environ.get('RP_S06C_OUT', 's06_alt_perspective.jsonl')
OUT = os.environ.get('RP_S06D_OUT', 's06_alt_criteria.jsonl')

SYS = '''你在把一个评价视角展开成 1-3 条二元准则。

【什么是二元准则】
一条准则由 positive 和 negative 两个描述组成，它们是同一件事的两面：
- positive：回答**满足**该准则时的**可观察现象**（不是抽象评价）
- negative：**不满足**时的对称现象，与 positive 互斥

例子：
  视角：「反应机理的立体选择性判断是否正确」
  准则1 positive：「回答明确指出了主产物的R/S构型及其成因」
       negative：「未指出构型，或指错构型，或未说明成因」

【硬要求】
- positive/negative 必须**本题专属**，不能是「准确」「完整」「清晰」这类通用词。
  必须落到这道题的具体内容上。
- 一个视角通常拆 1-2 条准则；只有视角覆盖多个独立点时才拆 3 条。
- 准则之间正交，不要有包含或依赖关系。
- positive 和 negative 必须互斥：一份回答不可能同时满足两者。

只输出 JSON：
{{"criteria": [{{"positive": "不超过60字", "negative": "不超过60字", "rationale": "为什么这条重要，不超过40字"}}]}}
每个视角展开 1-3 条准则。'''

WEIGHT_MAP = {'major': 10, 'normal': 5, 'minor': 3}


def build(r, p):
    q = (r.get('query_eff') or r['question'])[:2000]
    subj = ' / '.join(r.get('subject') or []) or '未标注'
    return [{'role': 'system', 'content': SYS},
            {'role': 'user', 'content':
                f'【学科】{subj}\n【提问意图】{r.get("intent_alt", "")}\n\n'
                f'【题目】\n{q}\n\n'
                f'【待展开的视角】{p["name"]}：{p["desc"]}'}]


def main():
    m = stage.pick('RP_M_ALT', 'generator')

    recs = stage.read_jsonl(IN)
    print(f'步骤 6d 第二生成器准则: {len(recs)} 条 (from {IN})')
    print(f'  模型: {m.name}, thinking={THINK}')

    total_p = sum(len(r.get('perspectives_alt', [])) for r in recs)
    print(f'  视角总数: {total_p}')

    # 摊平
    jobs = [(r, p) for r in recs for p in r.get('perspectives_alt', [])]
    print(f'  摊平后任务数: {len(jobs)}')

    def one(job):
        r, p = job
        obj, _ = stage.json_call(m, build(r, p), stage='s06d', thinking=THINK)
        cri = obj.get('criteria') or []
        if not isinstance(cri, list):
            cri = []
        out = []
        for c in cri[:3]:
            if not isinstance(c, dict):
                continue
            out.append({'perspective_id': p.get('perspective_id', ''),
                        'scenario_id': p.get('scenario_id', ''),
                        'block_id': p.get('block_id', ''),
                        'positive': str(c.get('positive', ''))[:200],
                        'negative': str(c.get('negative', ''))[:200],
                        'rationale': str(c.get('rationale', ''))[:120],
                        'score': WEIGHT_MAP.get(p.get('weight_hint'), 5)})
        return r['rid'], out

    done, _ = stage.run(one, jobs, workers=WORKERS, desc='s06d')
    by_rid = {}
    for rid, cs in done:
        by_rid.setdefault(rid, []).extend(cs)

    res = []
    for r in recs:
        cs = by_rid.get(r['rid'], [])
        for i, c in enumerate(cs):
            c['criterion_id'] = f'{r["rid"]}-alt-c{i + 1}'  # alt- 标记来自第二生成器
        res.append({**r, 'criteria_alt': cs,
                    'criteria_raw_perspectives_alt': len(r.get('perspectives_alt', []))})

    stage.write_jsonl(OUT, res)

    total_c = sum(len(r['criteria_alt']) for r in res)
    print(f'\n=== 步骤 6d 结果 ===')
    print(f'  总准则数: {total_c}')
    print(f'  平均/题: {total_c / len(res):.1f}')
    print(f'  平均/视角: {total_c / max(total_p, 1):.1f}')


if __name__ == '__main__':
    main()
