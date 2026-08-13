"""步骤 9：归一化 —— 算 S_max、归一化 score、合并同义准则，最终归一化到 100。

流程位置见 docs/design/rubric_pipeline_feishu_v2.md §9。关键要求：
  "归一后每题满分恒定，跨题可比。这是第 13 步 badcase 阈值能全局设一个的前提"

修正后的流程：
  1. 初步归一化：normalized_score = (原 score / 原始S_max) × 100
  2. 同义合并：删除相似准则
  3. **最终归一化**：重新归一化到 100，确保 sum(normalized_score) = 100

这样所有题目的满分都是 100，可以设置统一的 bad case 阈值（如 < 60）。

s_max_raw 保存原始分母（供调试），final_max 固定为 100。
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
        # 1. 初步归一化：算原始 S_max（只算 base + dist）
        s_max_raw = sum(c['score'] for c in r['criteria']
                        if c.get('criterion_type') in ('base', 'dist'))
        if s_max_raw == 0:
            s_max_raw = 1          # 防止除零

        # 初步归一化所有准则
        for c in r['criteria']:
            if c.get('criterion_type') == 'penalty':
                c['normalized_score'] = -(abs(c['score']) / s_max_raw) * 100
            else:
                c['normalized_score'] = (c['score'] / s_max_raw) * 100

        # 2. 同义合并：按 positive 的字符 Jaccard
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

        # 3. 最终归一化到 100
        # 计算合并后正向准则的 normalized_score 之和
        base_sum = sum(c['normalized_score'] for c in keep
                       if c.get('criterion_type') not in ('penalty',))

        if base_sum == 0:
            base_sum = 1  # 防止除零

        # 重新归一化，让正向准则总和 = 100
        for c in keep:
            if c.get('criterion_type') == 'penalty':
                # 负向项按同样比例调整（保持相对权重）
                c['normalized_score'] = (c['normalized_score'] / base_sum) * 100
            else:
                c['normalized_score'] = (c['normalized_score'] / base_sum) * 100

        # 验证：正向准则总和应该约等于 100
        final_sum = sum(c['normalized_score'] for c in keep
                       if c.get('criterion_type') not in ('penalty',))

        out.append({**r, 'criteria': keep,
                    's_max': 100,  # 最终满分固定为 100
                    's_max_raw': s_max_raw,  # 保留原始分母供调试
                    'criteria_before_merge': len(r['criteria']),
                    'criteria_after_merge': len(keep),
                    '_debug_final_sum': round(final_sum, 2)})  # 调试用

    stage.write_jsonl('s09_normalized.jsonl', out)

    merged_away = sum(r['criteria_before_merge'] - r['criteria_after_merge'] for r in out)

    # 验证归一化
    sums = [r['_debug_final_sum'] for r in out]
    print(f'\n=== 步骤 9 结果 ===')
    print(f'  归一化完成    : {len(out)} 条')
    print(f'  同义合并      : {merged_away} 条准则被吞掉')
    print(f'  最终归一化    : min={min(sums):.1f} max={max(sums):.1f} '
          f'(应该都接近 100.0)')

    # 检查是否有异常
    outliers = [r for r in out if abs(r['_debug_final_sum'] - 100) > 0.5]
    if outliers:
        print(f'  ⚠️  {len(outliers)} 条记录的归一化异常（偏离 100 超过 0.5）')

    ex = next((r for r in out if r['criteria_after_merge'] >= 5), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]}  满分=100 (原始分母={ex["s_max_raw"]:.0f}):')
        for c in ex['criteria'][:4]:
            typ = c.get('criterion_type', 'base')
            print(f'    {c["criterion_id"]} [{typ}] {c["normalized_score"]:.2f}分  '
                  f'{c["positive"][:48]}')

        base_total = sum(c['normalized_score'] for c in ex['criteria']
                        if c.get('criterion_type') != 'penalty')
        penalty_total = sum(c['normalized_score'] for c in ex['criteria']
                           if c.get('criterion_type') == 'penalty')
        print(f'    正向准则总分: {base_total:.2f}')
        print(f'    负向扣分总计: {penalty_total:.2f}')


if __name__ == '__main__':
    main()
