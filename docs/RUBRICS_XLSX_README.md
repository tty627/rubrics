# Rubrics 填充 Excel 说明文档

## 文件说明

### 生成的文件
**文件名**: `rubrics_filled.xlsx`  
**位置**: `/home/tantianye/rubrics/rubrics_filled.xlsx`  
**大小**: 2.6 MB  
**日期**: 2026-08-12

### 原始文件
**来源**: `/home/tantianye/Untitled spreadsheet.xlsx` (1.7 MB)

---

## 填充内容

已将 Phase 3 生成的 452 条 rubrics 填充到 Excel 的 A/B/C 列：

### A列: Question是否需要改写
- **填充率**: 453/453 (100%)
- **内容**: 
  - "否" - 表示 query 语义完整，直通
  - "是" - 表示需要改写（实际数据中较少）

### B列: 改写后的Question
- **填充率**: 452/453 (99.8%)
- **内容**:
  - 改写后的问题（如果需要改写）
  - "(未改写: 原因)" - 如果不需要改写，显示原因

### C列: 生成的rubrics
- **填充率**: 452/453 (99.8%)
- **内容**: 完整的 rubric 文本，包括：
  - 总分
  - 各维度名称
  - 每条准则的达标/不达标表述
  - 每条准则的分值

### D-G列: 原始数据（保持不变）
- D列: question（原始问题）
- E列: dimension（学科分类）
- F列: rubrics（草稿 rubric JSON）
- G列: reference_response（参考回复 JSON）

---

## 示例数据

### 案例: 会计学题目（第2行，q0002）

**D列 - 原始问题**:
```
合同负债和应付账款是什么意思，哪个是资产和负债，后续转化成收入还是成本
```

**A列 - 是否需要改写**:
```
否
```

**B列 - 改写说明**:
```
(未改写: query语义完整，多个子问题属同一主题递进追问，参考回复已完整覆盖。)
```

**C列 - 生成的rubrics** (部分内容):
```
【总分: 85】

【维度 1】概念定义与业务实质区分
--------------------------------------------------

[准则 1] 5.9分
  达标: 明确指出合同负债产生于已收客户对价但尚未转让商品或提供服务（收钱未履约）的情形
  不达标: 未提及已收对价或未履约条件，或将其描述为已履约待付款、采购欠款等其他情形

[准则 2] 5.9分
  达标: 明确指出应付账款产生于企业已接受商品/服务但尚未支付款项的情形
  不达标: 未提及已收货/已接受服务，或将其与预收款项、合同负债等场景混淆

... (共19条准则，4个维度)
```

---

## 与草稿 rubric 的对比

| 指标 | 草稿 (F列) | 生成 (C列) | 改善 |
|------|------------|------------|------|
| 维度数 | 1 | 平均 3.5 | **+250%** |
| 准则数 | 平均 6.1 | 平均 30.5 | **+400%** |
| 满分 | 中位数 21 | 中位数 139 | **+562%** |
| 格式 | JSON | 可读文本 | - |

---

## 使用方法

### 在 Excel 中查看
```bash
# 如果有 Excel，直接打开
open rubrics_filled.xlsx

# 或用 LibreOffice
libreoffice rubrics_filled.xlsx
```

### 用 Python 读取
```python
import sys
sys.path.insert(0, '/home/tantianye/rubrics')
from lib import xlsx

rows = xlsx.read('rubrics_filled.xlsx')

# 读取第2行 (索引1) 的 rubric
row = rows[1]
question = row.get(3)  # D列: 原始问题
need_rewrite = row.get(0)  # A列: 是否需要改写
rubric_text = row.get(2)  # C列: 生成的rubrics

print(f"问题: {question}")
print(f"是否需要改写: {need_rewrite}")
print(f"Rubric:\n{rubric_text}")
```

---

## 统计数据

### 填充完成度
- **总行数**: 453 条（不含表头）
- **A列**: 453/453 (100%)
- **B列**: 452/453 (99.8%)
- **C列**: 452/453 (99.8%)

### 未填充的1条记录
- 原因: s09_normalized.jsonl 中有1条记录缺失（从 453 → 452）

### 文件大小对比
- 原始文件: 1.7 MB
- 填充后文件: 2.6 MB
- 增加: 0.9 MB（主要是C列的 rubric 文本）

---

## 技术说明

### 数据来源
- **Phase 3 产出**: `data/s09_normalized.jsonl`
- **原始 xlsx**: `/home/tantianye/Untitled spreadsheet.xlsx`

### 填充脚本
- **脚本**: `scripts/fill_xlsx.py`
- **核心逻辑**:
  1. 读取原始 xlsx（使用 `lib/xlsx.py`）
  2. 读取 s09_normalized.jsonl 中的 rubric 数据
  3. 按 xlsx_row 匹配记录
  4. 格式化 rubric 为可读文本
  5. 填充到 A/B/C 列
  6. 写入新的 xlsx 文件

### 格式化规则
每条 rubric 包含：
- 【总分: X】
- 【维度 N】维度名称
- [准则 N] X.X分
  - 达标: ...
  - 不达标: ...
- [扣分项 N] -X.X分
  - 条件: ...

---

## 给导师展示

### 推荐展示方式
1. 在 Excel 中打开 `rubrics_filled.xlsx`
2. 滚动到第2行（会计学案例）
3. 展示 C列的完整 rubric（4个维度，19条准则）
4. 与 F列的草稿 rubric 对比（1个维度，6条准则）

### 随机抽查几行
建议抽查不同学科的案例：
- 行 2: 管理学 > 会计学
- 行 10: 理学 > 化学
- 行 50: 工学 > 材料科学
- 行 100: 工学 > 矿业工程
- 行 200: 工学 > 电池材料

---

**生成时间**: 2026-08-12  
**生成脚本**: `scripts/fill_xlsx.py`  
**数据来源**: `data/s09_normalized.jsonl` (Phase 3 最终产出)
