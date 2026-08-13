#!/usr/bin/env python3
"""
将生成的 rubrics 填回原始 xlsx 文件
从 s09_normalized.jsonl 读取数据，填充到 xlsx 的 A/B/C 列
"""

import json
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
    print("正在读取原始 xlsx...")
    xlsx_path = '/home/tantianye/Untitled spreadsheet.xlsx'
    rows = xlsx.read(xlsx_path)

    if len(rows) < 2:
        print("错误: xlsx 文件为空")
        return

    # 读取生成的 rubrics
    print("正在读取生成的 rubrics...")
    s09_path = 'data/s09_normalized.jsonl'
    rubrics_by_row = {}  # xlsx_row -> rubric data

    with open(s09_path) as f:
        for line in f:
            rec = json.loads(line)
            xlsx_row = rec.get('xlsx_row')
            if xlsx_row is not None:
                rubrics_by_row[xlsx_row] = {
                    'verdict': rec.get('verdict', ''),
                    'rewritten': rec.get('rewritten', ''),
                    'filter_reason': rec.get('filter_reason', ''),
                    'criteria': rec.get('criteria', []),
                    's_max': rec.get('s_max', 0)
                }

    print(f"共读取 {len(rubrics_by_row)} 条 rubric 数据")

    # 填充 A/B/C 列
    print("正在填充 xlsx...")
    filled_count = 0

    for i, row in enumerate(rows):
        if i == 0:  # 表头行保持不变
            continue

        # xlsx_row 在 seed.jsonl 中是从1开始的行号（Excel行号）
        # rows[0] 是表头，rows[1] 对应 xlsx_row=2
        xlsx_row_idx = i + 1  # 转换为 xlsx_row

        if xlsx_row_idx in rubrics_by_row:
            rdata = rubrics_by_row[xlsx_row_idx]

            # A列: Question是否需要改写
            verdict = rdata['verdict']
            if verdict == '直通':
                row[0] = '否'
            elif verdict == '改写':
                row[0] = '是'
            else:
                row[0] = verdict

            # B列: 改写后的Question
            rewritten = rdata['rewritten']
            if rewritten:
                row[1] = rewritten
            elif rdata['filter_reason']:
                row[1] = f"(未改写: {rdata['filter_reason']})"
            else:
                row[1] = ''

            # C列: 生成的rubrics
            if rdata['criteria']:
                rubric_text = format_rubric_for_excel(rdata['criteria'], rdata['s_max'])
                row[2] = rubric_text
                filled_count += 1
            else:
                row[2] = '(无准则数据)'

    print(f"已填充 {filled_count} 条 rubric")

    # 转换 rows 格式: list[dict] -> list[list]
    print("正在转换格式...")
    max_col = max(max(row.keys()) if row else 0 for row in rows)
    rows_list = []
    for row_dict in rows:
        row_list = []
        for col_idx in range(max_col + 1):
            row_list.append(row_dict.get(col_idx, ''))
        rows_list.append(row_list)

    # 写入新的 xlsx
    output_path = 'rubrics_filled.xlsx'
    print(f"正在写入 {output_path}...")

    xlsx.write(output_path, rows_list)
    print(f"✅ 完成！已生成 {output_path}")
    print(f"\n统计:")
    print(f"  - 总行数: {len(rows)-1}")
    print(f"  - 已填充 rubrics: {filled_count}")

if __name__ == '__main__':
    main()
