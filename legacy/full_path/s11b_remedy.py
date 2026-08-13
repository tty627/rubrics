"""步骤 11b：RIFT 诊断后处置 —— 根据诊断结果修复或删除有问题的准则。

流程位置见 docs/design/rubric_pipeline_feishu_v2.md §11 行 209-216。

s11 只做诊断，这一步根据诊断结果进行处置：
- Subjective / Ungrounded / Non-Atomic → 删除（标记为 status='removed'）
- Redundant Criteria → 合并（步骤 9 已做，这里标记）
- Missing Criteria → 标记需要补充（需要退回步骤 3，Phase 4 处理）
- Misaligned or Rigid → 标记需要人工审核（需要退回步骤 2，Phase 4 处理）

处置原则：
1. **自动删除**：Reliability 类（Subjective/Non-Atomic/Ungrounded）判定为 defective
2. **标记待处理**：Content Validity 类（Missing/Misaligned）需要人工介入
3. **保留原诊断**：所有诊断信息保留在 diagnostics 字段

Phase 3 定位：自动化处理 Reliability 类，Content Validity 类留给 Phase 4。
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

# mark（默认）：只标注待处置动作，不删准则；delete：直接删除命中项。
# 默认不删的原因见下方注释与 docs/design/rubric_pipeline_feishu_v2.md §11。
POLICY = os.environ.get('RP_REMEDY_POLICY', 'mark')


def main():
    recs = stage.read_jsonl('s11_diagnosed.jsonl')
    print(f'步骤 11b 处置: {len(recs)} 条')

    stats = {
        'total_criteria': 0,
        'flagged_subjective': 0,
        'flagged_non_atomic': 0,
        'flagged_ungrounded': 0,
        'flagged_total': 0,
        'total_removed': 0,
        'retained': 0,
    }

    out = []
    for r in recs:
        criteria_before = len(r['criteria'])
        kept = []

        for c in r['criteria']:
            stats['total_criteria'] += 1
            # s11 写出的键名是小写（subjective / non-atomic / ungrounded）。
            # 早先这里按首字母大写查找，恒 miss，导致「删除 0 条」的假象。
            # 统一小写化后再查，兼容两种写法。
            diag = {k.lower(): v for k, v in (c.get('diagnostics') or {}).items()}

            def _defective(mode):
                return (diag.get(mode) or {}).get('verdict') == 'defective'

            is_subjective = _defective('subjective')
            is_non_atomic = _defective('non-atomic')
            is_ungrounded = _defective('ungrounded')

            # 标注待处置动作，按 POLICY 决定是否真删。
            # 设计文档 §11：Subjective/Ungrounded/Non-Atomic → 先重写或拆分，
            # 重写后仍不过才删。直接删会一次性抹掉大部分准则。
            flags = []
            if is_subjective:
                flags.append(('subjective', (diag.get('subjective') or {}).get('reason', '')))
                stats['flagged_subjective'] += 1
            if is_non_atomic:
                flags.append(('non-atomic', (diag.get('non-atomic') or {}).get('reason', '')))
                stats['flagged_non_atomic'] += 1
            if is_ungrounded:
                flags.append(('ungrounded', (diag.get('ungrounded') or {}).get('reason', '')))
                stats['flagged_ungrounded'] += 1

            if not flags:
                c['status'] = 'active'
                c['remedy_action'] = 'none'
                stats['retained'] += 1
                kept.append(c)
                continue

            modes = [m for m, _ in flags]
            c['diagnosis_flags'] = [{'mode': m, 'reason': rsn} for m, rsn in flags]

            # non-atomic 的处置是「拆分」，需要 s11c 生成拆分后的子准则；
            # ungrounded 判定含相当比例的过严误判，需复核而非直接删。
            if 'non-atomic' in modes:
                c['remedy_action'] = 'split'      # 待 s11c 拆分
            elif 'ungrounded' in modes:
                c['remedy_action'] = 'review'     # 待复核是否真超范畴
            else:
                c['remedy_action'] = 'rewrite'    # 仅 subjective，重写措辞

            if POLICY == 'delete' :
                c['status'] = 'removed'
                c['removal_reason'] = '; '.join(f'{m}: {rsn}' for m, rsn in flags)
                stats['total_removed'] += 1
                continue

            # mark 模式（默认）：保留准则，只标注待处置
            c['status'] = 'flagged'
            stats['retained'] += 1
            stats['flagged_total'] += 1
            kept.append(c)

        # 重新归一化（删除准则后需要重新归一化到 100）
        base_sum = sum(c['normalized_score'] for c in kept
                       if c.get('criterion_type') != 'penalty')

        if base_sum > 0:
            for c in kept:
                if c.get('criterion_type') == 'penalty':
                    c['normalized_score'] = (c['normalized_score'] / base_sum) * 100
                else:
                    c['normalized_score'] = (c['normalized_score'] / base_sum) * 100

        # 验证归一化
        final_sum = sum(c['normalized_score'] for c in kept
                       if c.get('criterion_type') != 'penalty')

        out.append({**r,
                    'criteria': kept,
                    'criteria_before_remedy': criteria_before,
                    'criteria_after_remedy': len(kept),
                    'criteria_removed': criteria_before - len(kept),
                    '_debug_final_sum': round(final_sum, 2)})

    stage.write_jsonl('s11b_remedied.jsonl', out)

    t = stats['total_criteria']
    pct = lambda n: f'{n} ({n / t * 100:.1f}%)' if t else str(n)
    print(f'\n=== 步骤 11b 结果 (policy={POLICY}) ===')
    print(f'  总准则数            : {t}')
    print(f'  命中 subjective     : {pct(stats["flagged_subjective"])}')
    print(f'  命中 non-atomic     : {pct(stats["flagged_non_atomic"])}')
    print(f'  命中 ungrounded     : {pct(stats["flagged_ungrounded"])}')
    print(f'  标注待处置(去重)    : {pct(stats["flagged_total"])}')
    print(f'  实际删除            : {pct(stats["total_removed"])}')
    print(f'  保留                : {pct(stats["retained"])}')

    # 处置动作分布
    acts = Counter(c.get('remedy_action', 'none')
                   for r in out for c in r['criteria'])
    print(f'\n  待处置动作分布:')
    for a, n in acts.most_common():
        print(f'    {a:10s} {n}')

    # 检查归一化
    sums = [r['_debug_final_sum'] for r in out if r['criteria_after_remedy'] > 0]
    if sums:
        print(f'\n  重新归一化        : min={min(sums):.1f} max={max(sums):.1f} (应接近 100)')

    # 抽样
    ex = next((r for r in out if r['criteria_removed'] > 0), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]}:')
        print(f'    删除前: {ex["criteria_before_remedy"]} 条')
        print(f'    删除后: {ex["criteria_after_remedy"]} 条')
        print(f'    删除数: {ex["criteria_removed"]} 条')


if __name__ == '__main__':
    main()
