# 归一化修正完成报告

**修正日期**: 2026-08-12  
**修正内容**: 步骤 9 归一化逻辑  

---

## 问题描述

**原实现问题**:
- s_max 保存为绝对值（18-374 不等）
- 不同题目满分不统一，无法跨题目比较
- 无法设置统一的 bad case 阈值

**示例**:
- 会计学题目: s_max = 85
- STM32 题目: s_max = 174  
- 化学合成题目: s_max = 374

这导致无法直接比较回复质量，也无法设置"< 60 分就是 bad case"这样的统一阈值。

---

## 修正方案

### 核心改动

**修改文件**: `stages/s09_normalize.py`

**修正逻辑**:
1. **初步归一化**: 基于原始 s_max 归一化
   ```python
   normalized_score = (score / s_max_raw) * 100
   ```

2. **同义合并**: 删除相似准则（Jaccard ≥ 0.75）

3. **最终归一化**: 重新归一化到 100
   ```python
   base_sum = sum(normalized_score for c in keep if c.type != 'penalty')
   for c in keep:
       c['normalized_score'] = (c['normalized_score'] / base_sum) * 100
   ```

4. **输出**:
   - `s_max = 100` (所有题目统一)
   - `s_max_raw = 原始值` (保留供调试)
   - `normalized_score` (重新归一化后的分值)

---

## 修正结果

### 验证数据

**修正前**:
```
案例 q0002: s_max = 85
案例 q0004: s_max = 174
案例 q0282: s_max = 374

正向准则总和: 88.24 (不等于 100)
```

**修正后**:
```
案例 q0002: s_max = 100, s_max_raw = 85
案例 q0004: s_max = 100, s_max_raw = 174
案例 q0282: s_max = 100, s_max_raw = 374

正向准则总和: 100.00 (精确)
```

### Excel 展示

**修正前**:
```
【总分: 85】
```

**修正后**:
```
【满分: 100】（归一化前: 85）
```

---

## 实际应用

### 线上 Bad Case 挖掘

现在可以设置统一阈值：

```python
# 判分（Phase 4）
matched_scores = sum(c['normalized_score'] for c in matched_criteria)
final_score = matched_scores  # 已经是 0-100

# Bad case 判定
if final_score < 60:  # 统一阈值
    mark_as_badcase(query, response)
```

### 跨题目比较

```python
# 题目 A: 85 分原始分母 → 归一化后 100 分
# 回复得分: 72 分 → 72% (72/100)

# 题目 B: 174 分原始分母 → 归一化后 100 分
# 回复得分: 65 分 → 65% (65/100)

# 可以直接比较: 72% > 65%
```

---

## 影响的文件

### 已修改
1. ✅ `stages/s09_normalize.py` - 修正归一化逻辑
2. ✅ `data/s09_normalized.jsonl` - 重新生成（452 条）
3. ✅ `scripts/fill_xlsx_preserve_format.py` - 更新显示格式
4. ✅ `/home/tantianye/Untitled spreadsheet (已填充rubrics).xlsx` - 重新填充

### 需要更新
- 📋 `PIPELINE_AUDIT_DETAILED.md` - 标记 P0 问题已修复
- 📋 `presentation_for_advisor.md` - 更新满分说明
- 📋 文档中所有提到"满分不统一"的地方

---

## 对比：修正前后

| 指标 | 修正前 | 修正后 |
|------|--------|--------|
| 满分范围 | 18-374（不统一） | 100（所有题目统一） |
| 正向准则总和 | 88.24（不精确） | 100.00（精确） |
| Bad case 阈值 | 无法设置 | 可以统一设置（如 < 60） |
| 跨题目比较 | 不可比 | 可比 |
| 符合设计文档 | ❌ | ✅ |

---

## 设计文档依据

**来源**: `docs/rubric_pipeline_feishu_v2.md` 步骤 9 (行 169-176)

> S(A,Q) = F_norm(Σ s_c) = Σ s_c / S_max
> 
> - S_max = Σ α_c（仅正向准则）
> - **归一后每题满分恒定，跨题可比**
> - 这是第 13 步 badcase 阈值能全局设一个的前提
>   ——不归一则阈值只能逐题手调
> - **三种 form 归一后同量纲**

修正后的实现完全符合设计文档要求。

---

## 下一步

### Phase 4 实现建议

**步骤 12 (判分)**:
```python
def judge(query, response, rubric):
    matched_criteria = []
    for criterion in rubric['criteria']:
        if is_satisfied(response, criterion):
            matched_criteria.append(criterion)
    
    # 计算得分（已经是 0-100）
    score = sum(c['normalized_score'] for c in matched_criteria
                if c.get('criterion_type') != 'penalty')
    
    penalty = sum(c['normalized_score'] for c in matched_criteria
                  if c.get('criterion_type') == 'penalty')
    
    final_score = score + penalty  # penalty 是负值
    return final_score  # 0-100
```

**步骤 13 (Bad Case 提取)**:
```python
THRESHOLD = 60  # 统一阈值

for query, response in dataset:
    score = judge(query, response, rubric)
    if score < THRESHOLD:
        badcases.append({
            'query': query,
            'response': response,
            'score': score,
            'unmatched_criteria': [...]
        })
```

---

**修正人**: Claude  
**验证状态**: ✅ 已验证（452 条记录全部归一化到 100）  
**Excel 状态**: ✅ 已重新填充
