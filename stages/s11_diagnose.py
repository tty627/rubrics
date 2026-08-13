"""步骤 11：RIFT 免池诊断 —— Subjective / Non-Atomic / Ungrounded。

流程位置见 docs/design/rubric_pipeline_feishu_v2.md §11。RIFT 有 5 个失效模式，
其中 3 个可以在没有回复池的情况下诊断（Phase 1-2 用这个），另外 2 个
（Redundant / Conflict）需要回复池，留到 Phase 3 再做。

**三个免池失效模式**：
- **Subjective**（主观）：准则依赖判分者的主观偏好，无法客观判定
  例：「解释生动有趣」←→「解释枯燥乏味」，「生动」无客观标准
- **Non-Atomic**（非原子）：一条准则实际包含多个独立判断点，应拆分
  例：「回答准确且完整」其实是两条：「回答准确」+ 「回答完整」
- **Ungrounded**（脱靶）：准则与题目要求无关，或超出题目范畴
  例：问「什么是 Brep」却检查「是否给出了 C++ 代码示例」（题目没要求代码）

诊断器返回 verdict（clean / defective）和 reason。defective 的准则标记
到 criteria[].diagnostics，但**不删除**——删不删是 Phase 3 人工复核的事，
这一步只负责标记。

Phase 1 用这个产出两个东西：
1. 每条准则的诊断结果（写进 criteria[].diagnostics）
2. 汇总统计（步骤 11 末尾报告，Phase 2 会对比 baseline）
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 20))
THINK = stage.envflag('RP_THINK', True)

SYS_SUBJ = '''你是 RIFT 诊断器，检测准则是否**主观**（Subjective）。

主观准则：依赖判分者的个人偏好或美学品味，无法客观判定。
例：
  - 「解释生动有趣」←→「解释枯燥」     ← 主观，"生动"无客观标准
  - 「回答优雅简洁」←→「回答冗长」     ← 主观，"优雅"因人而异
  - 「回答给出了R构型的成因」←→「未给出」 ← 客观，可核验

只输出 JSON：{{"verdict": "clean|defective", "reason": "若判 defective，一句话说为什么主观，不超过40字"}}'''

SYS_ATOM = '''你是 RIFT 诊断器，检测准则是否**非原子**（Non-Atomic）。

非原子准则：一条准则里实际包含多个独立判断点，应拆成多条。
例：
  - 「回答准确且完整」← 非原子，包含"准确"和"完整"两个独立点
  - 「给出了构型及其成因」← 非原子，"构型"和"成因"应分别检查
  - 「回答明确指出了主产物的R构型」← 原子，只检查一件事

判定时允许合理的逻辑连接：
  - 「解释了A并说明了其与B的关系」← 若A和关系是一个完整论述链，可算原子
  - 「列出了3个要点」← 可算原子，"列出3个"是单一要求

只输出 JSON：{{"verdict": "clean|defective", "reason": "若判 defective，一句话说应拆成哪几个独立点，不超过50字"}}'''

SYS_UNG = '''你是 RIFT 诊断器，检测准则是否**脱靶**（Ungrounded）。

脱靶准则：与题目要求无关，或超出题目范畴。
例：
  - 题目问「什么是Brep」，准则检查「是否给出C++代码」← 脱靶，题目没要求代码
  - 题目问「解释牛顿第二定律」，准则检查「是否列举了应用案例」← 若题目未要求案例，则脱靶

注意：准则检查的是回答**应该包含什么**，不是回答**不该包含什么**。
若题目隐含要求（如「简述」隐含「给出定义+关键要点」），则相应准则不算脱靶。

只输出 JSON：{{"verdict": "clean|defective", "reason": "若判 defective，一句话说为什么与题目无关，不超过40字"}}'''

SYSTEMS = {'Subjective': SYS_SUBJ, 'Non-Atomic': SYS_ATOM, 'Ungrounded': SYS_UNG}


def build(r, c, mode):
    q = (r.get('query_eff') or r['question'])[:1500]
    return [{'role': 'system', 'content': SYSTEMS[mode]},
            {'role': 'user', 'content':
                f'【题目】\n{q}\n\n'
                f'【待诊断的准则】\n'
                f'positive: {c["positive"]}\n'
                f'negative: {c["negative"]}'}]


def main():
    m = stage.pick('RP_M_DIAGNOSER', 'diagnoser')
    recs = stage.read_jsonl('s09_normalized.jsonl')
    print(f'步骤 11 RIFT 免池诊断: {len(recs)} 条, 诊断器={m.name}, thinking={THINK}')

    nc_total = sum(len(r['criteria']) for r in recs)
    print(f'  准则总数: {nc_total}')

    # 摊平到 (题, 准则, 诊断模式) 再并发。这一步的调用量是全流程最大的
    # （准则数 × 3），若题内串行，全量 453 条要跑十几小时。
    MODES = ('Subjective', 'Non-Atomic', 'Ungrounded')
    jobs = [(r, c, mode) for r in recs for c in r['criteria'] for mode in MODES]
    print(f'  摊平后任务数: {len(jobs)} ({nc_total} 准则 × {len(MODES)} 模式)')

    def one(job):
        r, c, mode = job
        obj, _ = stage.json_call(m, build(r, c, mode),
                                 stage=f's11_{mode[:4].lower()}', thinking=THINK)
        verd = obj.get('verdict', 'clean')
        if verd not in ('clean', 'defective'):
            verd = 'clean'
        return (r['rid'], c['criterion_id'], mode.lower(),
                {'verdict': verd,
                 'reason': str(obj.get('reason', ''))[:120] if verd == 'defective' else ''})

    done, _ = stage.run(one, jobs, workers=WORKERS, desc='s11')
    diag = {}
    for rid, cid, mode, d in done:
        diag.setdefault((rid, cid), {})[mode] = d

    for r in recs:
        for c in r['criteria']:
            got = diag.get((r['rid'], c['criterion_id']), {})
            # 缺的模式按 clean 兜底并标记，避免下游把「没诊断」当成「诊断通过」
            c['diagnostics'] = {m.lower(): got.get(m.lower(),
                                                   {'verdict': 'clean', 'reason': '',
                                                    'missing': True})
                                for m in MODES}
    res = recs
    stage.write_jsonl('s11_diagnosed.jsonl', res)

    # 汇总统计
    stats = {mode.lower(): {'clean': 0, 'defective': 0} for mode in SYSTEMS}
    for r in res:
        for c in r['criteria']:
            for mode in stats:
                v = c.get('diagnostics', {}).get(mode, {}).get('verdict', 'clean')
                stats[mode][v] += 1

    print(f'\n=== 步骤 11 结果 ===')
    print(f'  诊断完成      : {nc_total} 条准则')
    for mode, st in stats.items():
        tot = st['clean'] + st['defective']
        pct = st['defective'] / max(tot, 1) * 100
        print(f'  {mode.capitalize():<12}  : defective {st["defective"]}/{tot} = {pct:.1f}%')

    # 抽一条 defective 的
    for r in res:
        for c in r['criteria']:
            if any(c.get('diagnostics', {}).get(m, {}).get('verdict') == 'defective'
                   for m in stats):
                print(f'\n  抽样一条被标记的准则 {c["criterion_id"]}:')
                print(f'    positive: {c["positive"][:60]}')
                for mode in stats:
                    d = c.get('diagnostics', {}).get(mode, {})
                    if d.get('verdict') == 'defective':
                        print(f'    [{mode}] {d.get("reason", "")}')
                return


if __name__ == '__main__':
    main()
