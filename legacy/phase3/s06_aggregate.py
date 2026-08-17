"""步骤 6：多模型聚合 - 合并两个生成器的 criteria。

输入：
- s04_criteria.jsonl（第一生成器：glm-ac）
- s06_alt_criteria.jsonl（第二生成器：deepseek）

输出：
- s06_aggregated.jsonl

聚合策略：
1. 按 positive 文本的字符 Jaccard 判断同义（≥0.75）
2. 同义的保留一条，标记来源 sources: ['glm', 'deepseek']
3. 不同义的都保留（取并集）
4. 最终每道题的 criteria 是两个生成器的并集
"""
import json, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

IN_MAIN = os.environ.get('RP_S04_OUT', 's04_criteria.jsonl')
IN_ALT = os.environ.get('RP_S06D_OUT', 's06_alt_criteria.jsonl')
OUT = os.environ.get('RP_S06_OUT', 's06_aggregated.jsonl')
SIM_THRESHOLD = 0.75


def norm(s):
    """字符集归一化，用于 Jaccard 计算"""
    return set(re.sub(r'[\s，。、（）()的与和]', '', s or ''))


def jaccard(s1, s2):
    """计算两个字符串的 Jaccard 相似度"""
    set1, set2 = norm(s1), norm(s2)
    if not set1 or not set2:
        return 0.0
    return len(set1 & set2) / len(set1 | set2)


def main():
    # 读取两个生成器的 criteria
    main_recs = {r['rid']: r for r in stage.read_jsonl(IN_MAIN)}
    alt_recs = {r['rid']: r for r in stage.read_jsonl(IN_ALT)}

    print(f'步骤 6 多模型聚合')
    print(f'  主生成器 (glm): {len(main_recs)} 条 from {IN_MAIN}')
    print(f'  辅生成器 (deepseek): {len(alt_recs)} 条 from {IN_ALT}')

    # 找到两者的交集
    common_rids = set(main_recs.keys()) & set(alt_recs.keys())
    print(f'  共同记录: {len(common_rids)}')

    out = []
    total_main_criteria = 0
    total_alt_criteria = 0
    total_merged = 0
    total_final = 0

    for rid in sorted(common_rids):
        main_r = main_recs[rid]
        alt_r = alt_recs[rid]

        main_criteria = main_r.get('criteria', [])
        alt_criteria = alt_r.get('criteria_alt', [])

        total_main_criteria += len(main_criteria)
        total_alt_criteria += len(alt_criteria)

        # 聚合逻辑
        merged = []
        used_alt = set()

        # 遍历主生成器的准则
        for mc in main_criteria:
            mc_positive = mc.get('positive', '')
            best_match = None
            best_sim = 0

            # 找最佳匹配
            for i, ac in enumerate(alt_criteria):
                if i in used_alt:
                    continue
                ac_positive = ac.get('positive', '')
                sim = jaccard(mc_positive, ac_positive)
                if sim >= SIM_THRESHOLD and sim > best_sim:
                    best_match = i
                    best_sim = sim

            if best_match is not None:
                # 找到同义准则，合并
                ac = alt_criteria[best_match]
                merged_item = {
                    **mc,
                    'sources': ['glm', 'deepseek'],
                    'similarity': best_sim,
                    'alt_positive': ac.get('positive', ''),
                    'alt_negative': ac.get('negative', '')
                }
                merged.append(merged_item)
                used_alt.add(best_match)
                total_merged += 1
            else:
                # 没找到同义，保留主生成器的
                merged.append({**mc, 'sources': ['glm']})

        # 添加辅生成器未匹配的准则
        for i, ac in enumerate(alt_criteria):
            if i not in used_alt:
                # 重新编号 criterion_id
                merged.append({
                    'perspective_id': ac.get('perspective_id', ''),
                    'scenario_id': ac.get('scenario_id', ''),
                    'block_id': ac.get('block_id', ''),
                    'positive': ac.get('positive', ''),
                    'negative': ac.get('negative', ''),
                    'rationale': ac.get('rationale', ''),
                    'score': ac.get('score', 5),
                    'sources': ['deepseek']
                })

        # 重新编号
        for i, c in enumerate(merged):
            c['criterion_id'] = f'{rid}-agg-c{i + 1}'

        total_final += len(merged)

        # 合并记录（保留第一生成器的其他字段）
        out.append({
            **main_r,
            'criteria': merged,
            'criteria_main_count': len(main_criteria),
            'criteria_alt_count': len(alt_criteria),
            'criteria_merged_count': len([c for c in merged if len(c.get('sources', [])) > 1]),
            'criteria_final_count': len(merged)
        })

    stage.write_jsonl(OUT, out)

    print(f'\n=== 步骤 6 结果 ===')
    print(f'  主生成器准则总数: {total_main_criteria}')
    print(f'  辅生成器准则总数: {total_alt_criteria}')
    print(f'  同义合并数: {total_merged}')
    print(f'  最终准则总数: {total_final}')
    print(f'  增幅: {100 * (total_final - total_main_criteria) / total_main_criteria:.1f}%')
    print(f'  平均/题: {total_final / len(out):.1f}')

    # 统计 sources 分布
    source_counter = Counter()
    for r in out:
        for c in r['criteria']:
            sources = tuple(sorted(c.get('sources', [])))
            source_counter[sources] += 1

    print(f'\n  来源分布:')
    for sources, count in source_counter.most_common():
        print(f'    {" + ".join(sources)}: {count}')

    # 统计维度数（如果有）
    dimensions = []
    for r in out:
        for c in r['criteria']:
            dim = c.get('dimension', '')
            if dim:
                dimensions.append(dim)

    if dimensions:
        dim_uniq = len(set(dimensions))
        print(f'\n  维度去重数: {dim_uniq}')
        print(f'  （如果相比单模型增加，说明聚合有效）')


if __name__ == '__main__':
    main()
