"""步骤 11Lb：诊断处置（lean 流程版）—— 删除 defective 准则并重归一。

输入: s11L_diagnosed.jsonl
输出: s11Lb_remedied.jsonl

处置策略（与 s11b 一致）：
1. 删除 is_defective=true 的准则
2. 若删除后正向准则<3条，保留原始 rubrics（标记 remedy_skipped）
3. 重算满分 s_max = sum(正向 score)
4. 保留删除前后对比字段：criteria_before_remedy / criteria_after_remedy
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

SRC = os.environ.get('RP_S11LB_SRC', 's11L_diagnosed.jsonl')


def main():
    recs = stage.read_jsonl(SRC)
    print(f'步骤 11Lb 诊断处置: {len(recs)} 条, 源={SRC}')

    res = []
    remedied_count = 0
    skipped_count = 0
    removed_total = 0

    for r in recs:
        rubrics = r.get('rubrics', [])
        diagnoses = r.get('diagnoses', [])

        # 构建 cid → diagnosis 映射
        diag_map = {d['_criterion_id']: d for d in diagnoses}

        # 分离 defective 和 clean
        defective_ids = {d['_criterion_id'] for d in diagnoses if d.get('is_defective')}
        clean_rubrics = [c for c in rubrics if c.get('_criterion_id') not in defective_ids]
        removed_rubrics = [c for c in rubrics if c.get('_criterion_id') in defective_ids]

        # 检查删除后是否还有足够的正向准则
        clean_pos = [c for c in clean_rubrics if c['is_positive']]

        if len(removed_rubrics) == 0:
            # 无需处置
            res.append({**r,
                        'criteria_before_remedy': len(rubrics),
                        'criteria_after_remedy': len(rubrics),
                        'criteria_removed': 0,
                        'remedy_skipped': False,
                        'skip_reason': ''})
            continue

        if len(clean_pos) < 3:
            # 删除后正向准则不足，跳过处置
            skipped_count += 1
            res.append({**r,
                        'criteria_before_remedy': len(rubrics),
                        'criteria_after_remedy': len(rubrics),
                        'criteria_removed': len(removed_rubrics),
                        'remedy_skipped': True,
                        'skip_reason': f'删除{len(removed_rubrics)}条后仅剩{len(clean_pos)}条正向准则'})
            continue

        # 执行删除
        remedied_count += 1
        removed_total += len(removed_rubrics)

        # 重算满分
        s_max_new = sum(c['score'] for c in clean_pos)

        res.append({**r,
                    'rubrics': clean_rubrics,
                    's_max': s_max_new,
                    'core_n': len(clean_rubrics),
                    'core_n_positive': len(clean_pos),
                    'criteria_before_remedy': len(rubrics),
                    'criteria_after_remedy': len(clean_rubrics),
                    'criteria_removed': len(removed_rubrics),
                    'removed_criterion_ids': [c.get('_criterion_id') for c in removed_rubrics],
                    'remedy_skipped': False,
                    'skip_reason': ''})

    stage.write_jsonl('s11Lb_remedied.jsonl', res)

    print(f'\n=== 步骤 11Lb 结果 ===')
    print(f'  执行处置      : {remedied_count} 题')
    print(f'  跳过处置      : {skipped_count} 题（删除后准则不足）')
    print(f'  删除准则总数  : {removed_total}')
    if remedied_count > 0:
        print(f'  平均删除/题   : {removed_total / remedied_count:.1f}')

    # 抽样展示
    ex = next((r for r in res if r.get('criteria_removed', 0) > 0 and not r.get('remedy_skipped')), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]} (删除了 {ex["criteria_removed"]} 条):')
        print(f'    删除ID: {ex.get("removed_criterion_ids", [])}')
        print(f'    满分: {ex.get("criteria_before_remedy")} → {ex["s_max"]}')


if __name__ == '__main__':
    main()
