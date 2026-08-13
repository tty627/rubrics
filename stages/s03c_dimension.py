"""步骤 3c：Dimension Assignment —— 将 perspectives 归类到高层维度。

这是对原实现的补丁：s03 生成了 perspectives（评价轴），但缺少更高层的
dimension（维度）归类。这导致无法计算"维度去重数"这个核心指标。

设计决策：
- 批量处理一道题的所有 perspectives，一次 LLM 调用出维度分组
- 不预设固定维度列表，让 LLM 根据题目和 perspectives 动态生成
- 要求维度名称具体到本题，避免"准确性""完整性"这种空泛词
- 输出：每个 perspective 获得一个 dimension 字段

位置：s03_perspective → s03c_dimension → s04_criteria
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

WORKERS = int(os.environ.get('RP_WORKERS', 20))
THINK = stage.envflag('RP_THINK', True)
IN = os.environ.get('RP_S03_IN', 's03_perspective_hybrid.jsonl')
OUT = os.environ.get('RP_S03C_OUT', 's03c_dimensioned.jsonl')

SYS = '''你在为一道题的评价轴（perspectives）归类到高层维度（dimensions）。

【什么是维度】
维度是评价一份回答的**大方向**，比一个个具体的评价轴更抽象一层。
- 例：「是否区分了病因与症状」「是否给出诊断依据」是两个评价轴，
  但它们都属于「医学知识准确性」这个维度。
- 例：「是否覆盖了常见场景」「是否讨论了边界情况」是两个评价轴，
  但它们都属于「场景覆盖全面性」这个维度。

【硬要求】
1. 维度数量：将给定的评价轴归类到 **3-6 个维度**。太少说明没拆开，太多说明没归类。
2. 维度名称必须**具体到本题**。「准确性」「完整性」「逻辑性」这类放之四海皆准的词
   是无效输出。要说清楚「什么的准确性」「什么的完整性」。
   - ✓ 好例子：「化学反应机理的准确性」「临床诊断的完整性」「代码边界情况的覆盖度」
   - ✗ 坏例子：「知识准确性」「内容完整性」「逻辑严密性」
3. 每个评价轴只能归到**一个**维度。
4. 同一维度下的评价轴应该在**同一个大方向**上评价回答，即使它们关注不同的细节。

【常见维度类型（仅供启发，不要照搬）】
- 某领域知识的准确性
- 某方面的覆盖全面性
- 某类论证的严密性
- 某种表达的清晰度
- 某个目标的实用性
- 某类风险的识别

但**只在本题真的需要时才用**，根据实际评价轴动态生成，不要硬套。

只输出 JSON：
{
  "dimensions": [
    {
      "name": "维度名称，不超过12字，具体到本题",
      "desc": "这个维度在评价什么，不超过30字",
      "perspective_ids": ["p1", "p2", ...]  // 归到这个维度的评价轴 ID
    }
  ]
}

【自查清单】
- 维度数量是 3-6 个吗？
- 每个维度名称都具体到本题了吗（不是空泛词）？
- 每个评价轴都恰好在一个维度里吗？
- 同一维度下的评价轴确实在同一个大方向上吗？
'''


def main():
    recs = stage.read_jsonl(IN)
    print(f'步骤 3c 维度归类: {len(recs)} 条 (from {IN})')

    out = []
    for r in recs:
        persp = r.get('perspectives', [])
        if not persp:
            # 没有 perspectives，直接跳过
            out.append(r)
            continue

        # 构造 prompt
        query = r.get('query_eff', r.get('question', ''))
        prompt = f'''【题目】
{query}

【已生成的评价轴】
'''
        for p in persp:
            prompt += f"- [{p['perspective_id']}] {p['name']}: {p['desc']}\n"

        prompt += '\n请将这些评价轴归类到 3-6 个高层维度。'

        # 调用 LLM (json_call 已经解析好 JSON)
        m = stage.pick('RP_MODEL', 'generator')
        dims, meta = stage.json_call(m, [
            {'role': 'system', 'content': SYS},
            {'role': 'user', 'content': prompt}
        ], stage='s03c', thinking=THINK)

        if not dims or 'dimensions' not in dims:
            print(f'  ✗ {r["rid"]} JSON 解析失败，跳过维度归类')
            out.append(r)
            continue

        dimensions = dims['dimensions']

        # 验证每个 perspective 都被分配了维度
        pid_to_dim = {}
        for dim in dimensions:
            dim_name = dim.get('name', 'unknown')
            for pid in dim.get('perspective_ids', []):
                if pid in pid_to_dim:
                    print(f'  ⚠ {r["rid"]} perspective {pid} 被分配到多个维度')
                pid_to_dim[pid] = dim_name

        # 将 dimension 字段添加到每个 perspective
        assigned_count = 0
        for p in persp:
            pid = p['perspective_id']
            if pid in pid_to_dim:
                p['dimension'] = pid_to_dim[pid]
                assigned_count += 1
            else:
                # 未被分配，使用默认值
                p['dimension'] = '其他'

        # 记录维度信息到记录级别（便于后续统计）
        r['dimensions'] = dimensions
        r['dimension_count'] = len(dimensions)

        out.append(r)

    stage.write_jsonl(OUT, out)

    # 统计
    total_persp = sum(len(r.get('perspectives', [])) for r in out)
    total_dims = sum(r.get('dimension_count', 0) for r in out)

    # 收集所有 dimension 名称
    all_dim_names = []
    for r in out:
        for p in r.get('perspectives', []):
            dim = p.get('dimension')
            if dim:
                all_dim_names.append(dim)

    dim_counter = Counter(all_dim_names)
    dim_uniq = len(set(all_dim_names))

    print(f'\n=== 步骤 3c 结果 ===')
    print(f'  处理记录数      : {len(out)}')
    print(f'  总 perspective 数: {total_persp}')
    print(f'  平均维度数/题   : {total_dims / len(out):.1f}')
    print(f'  维度去重总数    : {dim_uniq}')
    print(f'\n  Top 15 维度:')
    for dim, count in dim_counter.most_common(15):
        pct = 100 * count / total_persp if total_persp else 0
        print(f'    {dim}: {count} ({pct:.1f}%)')


if __name__ == '__main__':
    main()
