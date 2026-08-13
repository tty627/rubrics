#!/usr/bin/env python3
"""
复制原始 xlsx 文件，并填充 C 列（生成的rubrics）
"""

import json
import shutil
import sys
sys.path.insert(0, '/home/tantianye/rubrics')
from lib import xlsx

def format_rubric_for_excel(criteria, s_max):
    """将准则列表格式化为可读的文本"""
    # 按维度分组
    dims = {}
    for crit in criteria:
        dim = crit.get('dimension', '未分类')
        if dim not in dims:
            dims[dim] = []
        dims[dim].append(crit)

    lines = [f"【总分: {s_max}】\n"]

    for dim_idx, (dim_name, crits) in enumerate(dims.items(), 1):
        lines.append(f"\n【维度 {dim_idx}】{dim_name}")
        lines.append("-" * 50)

        for i, crit in enumerate(crits, 1):
            ctype = crit.get('criterion_type', 'base')
            score = crit.get('normalized_score', crit.get('score', 0))

            if ctype == 'penalty':
                lines.append(f"\n[扣分项 {i}] -{abs(score):.1f}分")
                lines.append(f"  条件: {crit.get('positive', '')[:150]}")
            else:
                lines.append(f"\n[准则 {i}] {score:.1f}分")
                lines.append(f"  达标: {crit.get('positive', '')[:150]}")
                lines.append(f"  不达标: {crit.get('negative', '')[:150]}")

    return '\n'.join(lines)

def main():
    original_file = '/home/tantianye/Untitled spreadsheet.xlsx'
    output_file = '/home/tantianye/Untitled spreadsheet (已填充rubrics).xlsx'

    print(f"正在复制原始文件...")
    shutil.copy(original_file, output_file)
    print(f"✅ 已创建副本: {output_file}")

    print("\n正在读取副本...")
    rows = xlsx.read(output_file)

    if len(rows) < 2:
        print("错误: xlsx 文件为空")
        return

    # 读取生成的 rubrics
    print("正在读取生成的 rubrics...")
    s09_path = 'data/s09_normalized.jsonl'
    rubrics_by_row = {}

    with open(s09_path) as f:
        for line in f:
            rec = json.loads(line)
            xlsx_row = rec.get('xlsx_row')
            if xlsx_row is not None:
                rubrics_by_row[xlsx_row] = {
                    'criteria': rec.get('criteria', []),
                    's_max': rec.get('s_max', 0)
                }

    print(f"共读取 {len(rubrics_by_row)} 条 rubric 数据")

    # 只填充 C 列（索引2）
    print("正在填充 C 列...")
    filled_count = 0

    for i, row in enumerate(rows):
        if i == 0:  # 表头行保持不变
            continue

        xlsx_row_idx = i + 1  # 转换为 xlsx_row

        if xlsx_row_idx in rubrics_by_row:
            rdata = rubrics_by_row[xlsx_row_idx]

            # C列: 生成的rubrics
            if rdata['criteria']:
                rubric_text = format_rubric_for_excel(rdata['criteria'], rdata['s_max'])
                row[2] = rubric_text  # C列，索引2
                filled_count += 1
            else:
                row[2] = '(无准则数据)'

    print(f"已填充 {filled_count} 条 rubric 到 C 列")

    # 转换格式: list[dict] -> list[list]
    print("正在转换格式并保存...")
    max_col = max(max(row.keys()) if row else 0 for row in rows)
    rows_list = []
    for row_dict in rows:
        row_list = []
        for col_idx in range(max_col + 1):
            row_list.append(row_dict.get(col_idx, ''))
        rows_list.append(row_list)

    # 覆盖写入副本文件
    xlsx.write(output_file, rows_list)

    print(f"\n✅ 完成！已生成: {output_file}")
    print(f"\n统计:")
    print(f"  - 总行数: {len(rows)-1}")
    print(f"  - C列已填充: {filled_count}")
    print(f"  - 保留原始文件所有列: ✅")

if __name__ == '__main__':
    main()
