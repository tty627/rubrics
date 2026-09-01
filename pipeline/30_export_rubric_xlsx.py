#!/usr/bin/env python3
"""
直接修改原始 xlsx 文件的 XML，只填充 C 列，保留所有格式
"""

import zipfile
import json
import shutil
import os
import sys
from xml.etree import ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib import rubric

SEV_LABEL = {'principle': '原则性', 'major': '较严重', 'minor': '轻微'}


def format_rubric(rubrics, full_mark):
    """格式化为导师指定 schema 的可读文本。

    rubrics 每条含 criteria / score / reason / dimension / is_positive；
    负项另带 severity / is_veto（s04c_severity 起）。
    正向准则按 score 从高到低排，负向准则统一列在末尾。
    闸门项标 ⭐（gated_answer 的答案判据），veto 项标 🚫 并在末尾声明聚合规则 ——
    xlsx 是人看的那份交付，一票否决的规则不能只藏在 jsonl 字段里。
    """
    pos = [c for c in rubrics if c.get('is_positive')]
    neg = [c for c in rubrics if not c.get('is_positive')]
    pos.sort(key=lambda c: -c.get('score', 0))
    vetoes = [c for c in neg if c.get('is_veto')]

    lines = [f"【满分 {full_mark} 分】共 {len(pos)} 条评分点"
             + (f" + {len(neg)} 条扣分项" if neg else "")
             + (f"（其中 {len(vetoes)} 条一票否决）" if vetoes else "")]

    for i, c in enumerate(pos, 1):
        lines.append("")
        gate = ' ⭐答案判据' if c.get('is_gate') else ''
        lines.append(f"{i}. [{c.get('dimension', '')}] {c.get('score', 0)} 分{gate}")
        lines.append(f"   {c.get('criteria', '')}")
        if c.get('reason'):
            lines.append(f"   （{c['reason']}）")

    if neg:
        lines.append("")
        lines.append("─── 扣分项 ───")
        for i, c in enumerate(neg, 1):
            lines.append("")
            sev = SEV_LABEL.get(c.get('severity'), '')
            tag = f" [{sev}]" if sev else ''
            if c.get('is_veto'):
                tag += ' 🚫一票否决'
            lines.append(f"{i}. [{c.get('dimension', '')}] "
                         f"{c.get('score', 0)} 分{tag}")
            lines.append(f"   {c.get('criteria', '')}")
            if c.get('reason'):
                lines.append(f"   （{c['reason']}）")

    if vetoes:
        lines.append("")
        lines.append(f"※ 🚫 项的判分规则：{rubric.VETO_RULE}")

    return '\n'.join(lines)

def main():
    # 原始 xlsx 路径可用 RP_XLSX_SRC 覆盖；产出统一落到 outputs/excel/
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 2026-08-17：默认模板从 $HOME 硬编码改为仓库内 data/input.xlsx（原数据源，
    # 已从模板恢复并验证与 seed.jsonl 往返一致），仓库自包含、换机可跑。
    original_file = os.environ.get('RP_XLSX_SRC',
                                   os.path.join(repo_root, 'data', 'input.xlsx'))
    out_dir = os.path.join(repo_root, 'outputs', 'excel')
    os.makedirs(out_dir, exist_ok=True)
    output_file = os.path.join(out_dir, 'Untitled spreadsheet (已填充rubrics).xlsx')

    # 复制原始文件
    print("正在复制原始文件...")
    shutil.copy(original_file, output_file)

    # 读取 rubric 数据。默认 = 阶段 27 的交付档 —— xlsx 和 jsonl 必须是同一份
    # 交付内容，各读一个源迟早对不上。别把默认改回中间产物：踩过的两次都是
    # 「源没过 RIFT 处置、没有 is_gate / severity / is_veto」。
    # 设 RP_FILL_SRC 可指向别的 jsonl（相对仓库根，或绝对路径）。
    src_name = os.environ.get('RP_FILL_SRC', 'outputs/current/rubric_delivery.jsonl')
    src_path = (src_name if os.path.isabs(src_name)
                else os.path.join(repo_root, src_name))
    if not os.path.exists(src_path):
        raise SystemExit(f'缺少 {src_path}\n'
                         f'先跑 bash pipeline/00_run_all.sh（或至少跑到阶段 27）')

    print(f"正在读取 rubric 数据（源: {src_name}）...")
    rubrics_by_row = {}
    with open(src_path, encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            xlsx_row = rec.get('xlsx_row')
            rubrics = rec.get('rubrics') or []
            if xlsx_row and rubrics:
                rubrics_by_row[xlsx_row] = {
                    'rubrics': rubrics,
                    # 满分口径走 lib/rubric（sum(正向 score)），不再内联
                    'full_mark': rec.get('full_mark') or rubric.s_max(rubrics),
                }

    n_veto = sum(1 for d in rubrics_by_row.values()
                 for c in d['rubrics'] if c.get('is_veto'))
    print(f"共读取 {len(rubrics_by_row)} 条数据"
          f"（{n_veto} 条一票否决项将在扣分项里标出）")

    # 修改 xlsx 中的 XML
    print("正在修改 xlsx 文件...")

    NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

    with zipfile.ZipFile(output_file, 'r') as zip_read:
        # 读取 sheet1.xml
        sheet_xml = zip_read.read('xl/worksheets/sheet1.xml')
        root = ET.fromstring(sheet_xml)

        # 遍历所有行
        filled = 0
        for row in root.iter(NS + 'row'):
            row_num = int(row.get('r'))

            # 跳过表头行
            if row_num == 1:
                continue

            # 检查是否有这一行的 rubric 数据
            if row_num in rubrics_by_row:
                rdata = rubrics_by_row[row_num]
                if rdata['rubrics']:
                    rubric_text = format_rubric(rdata['rubrics'], rdata['full_mark'])

                    # 找到或创建 C 列单元格
                    c_cell = None
                    for cell in row.iter(NS + 'c'):
                        if cell.get('r').startswith('C'):
                            c_cell = cell
                            break

                    if c_cell is None:
                        # 创建新的 C 列单元格
                        c_cell = ET.SubElement(row, NS + 'c')
                        c_cell.set('r', f'C{row_num}')

                    # 设置单元格类型为 inlineStr
                    c_cell.set('t', 'inlineStr')

                    # 清除旧内容
                    for child in list(c_cell):
                        c_cell.remove(child)

                    # 添加新内容
                    is_elem = ET.SubElement(c_cell, NS + 'is')
                    t_elem = ET.SubElement(is_elem, NS + 't')
                    t_elem.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                    t_elem.text = rubric_text

                    filled += 1

        print(f"已填充 {filled} 条")

        # 将修改后的 XML 转回字符串
        sheet_xml_new = ET.tostring(root, encoding='utf-8', xml_declaration=True)

    # 重新打包 xlsx（保留其他文件）
    print("正在重新打包 xlsx...")
    temp_file = output_file + '.temp'

    with zipfile.ZipFile(output_file, 'r') as zip_read:
        with zipfile.ZipFile(temp_file, 'w', zipfile.ZIP_DEFLATED) as zip_write:
            for item in zip_read.infolist():
                if item.filename == 'xl/worksheets/sheet1.xml':
                    # 写入修改后的 sheet1.xml
                    zip_write.writestr(item, sheet_xml_new)
                else:
                    # 其他文件原样复制
                    zip_write.writestr(item, zip_read.read(item.filename))

    # 替换原文件
    os.replace(temp_file, output_file)

    print(f"\n✅ 完成！已保存到: {output_file}")
    print(f"   - 已填充 {filled} 条 rubric 到 C 列")
    print(f"   - 保留了所有原始格式（颜色、边框、行高等）")

if __name__ == '__main__':
    main()
