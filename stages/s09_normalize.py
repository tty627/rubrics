"""步骤 9：归一化 —— 算 S_max、归一化 score、合并同义准则。

流程位置见 docs/rubric_pipeline_feishu_v2.md §9。Qworld §3.5 要求：
  S_max = Σ(R_base 的 score) + Σ(R_dist 的 score)
  每条准则的 normalized_score = (原 score / S_max) × 100

惩罚项不参与 S_max 计算，但要归一化：
  normalized_score_penalty = (|score| / S_max) × 100 × (-1)

同义准则合并：视角名/准则 positive 的字符 Jaccard ≥0.75 时合并，
保留先出现的，被吞的 criterion_id 记到 merged_from。
"""
import json, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

SIM_THRESHOLD = 0.75


def norm(s):
    return set(re.sub(r'[\s，。、（）()的与和]', '', s or ''))


def main():
    recs = stage.read_jsonl('s08_penalties.jsonl')
    print(f'步骤 9 归一化: {len(recs)} 条')

    out = []
    for r in recs:
        # 算 S_max（只算 base + dist）
        s_max = sum(c['score'] for c in r['criteria']
                    if c.get('criterion_type') in ('base', 'dist'))
        if s_max == 0:
            s_max = 1          # 防止除零

        # 归一化所有准则
        for c in r['criteria']:
            if c.get('criterion_type') == 'penalty':
                c['normalized_score'] = -(abs(c['score']) / s_max) * 100
            else:
                c['normalized_score'] = (c['score'] / s_max) * 100

        # 同义合并：按 positive 的字符 Jaccard
        seen, keep = [], []
        for c in r['criteria']:
            k = norm(c.get('positive'))
            if not k:
                continue
            if any(len(k & s) / max(len(k | s), 1) >= SIM_THRESHOLD for s in seen):
                # 与已有某条相似，记到最近那条的 merged_from
                if keep:
                    keep[-1].setdefault('merged_from', []).append(c['criterion_id'])
                continue
            seen.append(k)
            c.setdefault('merged_from', [c['criterion_id']])
            keep.append(c)

        out.append({**r, 'criteria': keep, 's_max': s_max,
                    'criteria_before_merge': len(r['criteria']),
                    'criteria_after_merge': len(keep)})

    stage.write_jsonl('s09_normalized.jsonl', out)

    merged_away = sum(r['criteria_before_merge'] - r['criteria_after_merge'] for r in out)
    print(f'\n=== 步骤 9 结果 ===')
    print(f'  归一化完成    : {len(out)} 条')
    print(f'  同义合并      : {merged_away} 条准则被吞掉')
    ex = next((r for r in out if r['criteria_after_merge'] >= 5), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]}  S_max={ex["s_max"]:.1f}:')
        for c in ex['criteria'][:4]:
            typ = c.get('criterion_type', 'base')
            print(f'    {c["criterion_id"]} [{typ}] norm={c["normalized_score"]:.2f}  '
                  f'{c["positive"][:48]}')


if __name__ == '__main__':
    main()
