# S04L Rubrics 质量修复实施指南

**修复日期**: 2026-08-12  
**问题来源**: `docs/reports/RUBRICS_REVIEW_FINDINGS.md`  
**修复文件**: `stages/s04L_rubric.py`, `stages/s11L_diagnose.py`, `stages/s11Lb_remedy.py`

---

## 修复内容概览

| 修复项 | 问题规模 | 严重程度 | 状态 |
|--------|---------|---------|------|
| 答案项占比自动调整 | 21题 | P0 | ✅ 已实现 |
| 禁止负向准则编造数值 | 23题 | P0 | ✅ 已实现 |
| 扩展禁止词（空泛准则） | 28题 | P1 | ✅ 已实现 |
| 接入 RIFT 诊断 | 14.4%题 | P1 | ✅ 已实现 |

---

## 修复1: 答案项占比自动调整

### 问题描述
verifiable 题型的答案项（最高分准则）应占满分的 60-80%，但实际：
- 18题 <60%（最低21%）
- 3题 >80%（最高89%）

### 修复方案
在 `parse()` 函数中添加后处理逻辑：

```python
# gated_answer 答案项占比校验与自动调整
if is_gate and final:
    pos_final = [c for c in final if c['is_positive']]
    if pos_final:
        max_score = max(c['score'] for c in pos_final)
        total = sum(c['score'] for c in pos_final)
        ratio = max_score / total if total > 0 else 0

        # 目标区间 60-80%，若偏离则调整
        if ratio < 0.6 or ratio > 0.8:
            # 找到答案项
            answer_item = next(c for c in pos_final if c['score'] == max_score)
            other_items = [c for c in pos_final if c['score'] != max_score]

            # 计算目标答案项分值（取70%中点）
            target_ratio = 0.70
            if other_items:
                other_total = sum(c['score'] for c in other_items)
                target_answer = int(round(other_total * target_ratio / (1 - target_ratio)))
                target_answer = max(6, min(8, target_answer))

                # 调整
                answer_item['score'] = target_answer
                for c in other_items:
                    if c['score'] > 1:
                        c['score'] = 1

            final = [answer_item] + other_items + neg_l
```

### 预期效果
- 所有 verifiable 题的答案项占比自动调整到 60-80% 区间
- 其他辅助准则压缩到 1 分

---

## 修复2: 禁止负向准则编造数值

### 问题描述
23题的负向准则包含编造的具体数值/字节（如"52"、"170-180°C"），无参考依据。

### 修复方案

#### 2.1 prompt 添加约束（已修改）

```python
【扣分项】1-2 条，只写**真正致命**的错误（结论答反、核心概念用错）。
直接写错误现象本身，is_positive 填 false。

⚠️ **扣分项禁止编造具体细节**：
- 如果题目有参考错误回复，可基于真实错误写具体准则
- 如果没有参考错误，只写**通用错误类型**，不要编造具体数值/字节/参数

  错误示例（verifiable题无参考错误时）：
  ❌ "将十六进制'52'错误解码为字符'S'"  ← 编造了具体字节和错误结果
  ✅ "解码结果与标准答案不一致，存在字符转换错误"  ← 通用描述
```

#### 2.2 传递 ref_errors 信息（已修改）

```python
def build(r):
    # ...
    ref_errs = r.get('ref_errors', [])
    err_hint = ''
    if ref_errs and len(ref_errs) > 0:
        err_hint = f'\n\n【参考错误】本题有 {len(ref_errs)} 条错误回复可参考，扣分项可基于真实错误模式编写。'
    else:
        err_hint = '\n\n【⚠️ 无参考错误】本题无错误回复样本，扣分项必须写通用错误类型，禁止编造具体数值/字节/参数。'

    user = (f'【学科】{subj}\n'
            f'【提问意图】{r.get("intent", "")}\n'
            f'【题型】{r.get("question_type", "")} → {form}{gate}\n\n'
            f'【题目】\n{q}\n\n'
            f'【候选评价轴（供参考，不必每条都变成准则）】\n{persp or "（无）"}'
            f'{err_hint}')
```

### 预期效果
- 无参考错误的题目，负向准则只描述错误类型，不编造具体值
- 有参考错误的题目，可基于真实错误写具体准则

---

## 修复3: 扩展禁止词

### 问题描述
28题的准则含空泛词（"准确"、"完整"、"清晰"等），判分器无法一致判定。

### 修复方案
在 prompt 中添加反例和正例：

```python
3. **禁止空泛词**。不能是「回答准确」「解释完整」「逻辑清晰」「结论准确」「答案正确」。
   必须是本题专属、判分器能直接核对的内容。

   反例（空泛）：
   ❌ "结论表述清晰，无歧义或模棱两可"
   ❌ "答案准确"
   ❌ "完整列出全部六十四卦"

   正例（具体）：
   ✅ "明确指出合同负债属于负债类科目，而非资产"
   ✅ "列出乾、坤、震、巽、坎、离、艮、兑等64卦名称，无遗漏"
   ✅ "最终答案为7cm，与标准答案一致"
```

### 预期效果
- LLM 学会将空泛词转化为具体、可核验的表述

---

## 修复4: 接入 RIFT 诊断

### 问题描述
`rubrics_advisor_lean.jsonl` 未经过 RIFT 诊断，Subjective/Non-Atomic/Ungrounded 准则未被检测。

### 修复方案

#### 4.1 创建 s11L_diagnose.py

适配 s04L 输出格式的 RIFT 诊断脚本：
```bash
python3 stages/s11L_diagnose.py
# 输入: data/s04L_rubric.jsonl
# 输出: data/s11L_diagnosed.jsonl
```

#### 4.2 创建 s11Lb_remedy.py

删除 defective 准则并重算满分：
```bash
python3 stages/s11Lb_remedy.py
# 输入: data/s11L_diagnosed.jsonl
# 输出: data/s11Lb_remedied.jsonl
```

#### 4.3 修改导出脚本

```bash
# 旧: 直接从 s04L 导出
python3 scripts/export_advisor_schema.py

# 新: 从诊断后的版本导出
RP_SRC=s11Lb_remedied.jsonl python3 scripts/export_advisor_schema.py
```

### 预期效果
- 非原子准则被检测并删除
- 主观准则被检测并删除
- 脱靶准则被检测并删除

---

## 重新运行流程

### 清理旧缓存（可选）
```bash
rm -rf cache/s04L/
```

### 运行修复后的流程
```bash
# 1. 重新生成 rubrics（使用修复后的 s04L）
python3 stages/s04L_rubric.py

# 2. RIFT 诊断
python3 stages/s11L_diagnose.py

# 3. 删除 defective 准则
python3 stages/s11Lb_remedy.py

# 4. 导出交付版本
python3 scripts/export_advisor_schema.py
# 或从诊断后版本导出:
# TODO: 修改 export_advisor_schema.py 支持 --src 参数
```

### 验证修复效果
```bash
python3 scripts/test_s04L_fixes.py
```

---

## 预期改善指标

| 指标 | 修复前 | 修复后目标 |
|------|--------|-----------|
| 答案项占比<60% | 18题 | 0题 |
| 答案项占比>80% | 3题 | 0题 |
| 负向准则编造数值 | 23题 | <5题 |
| 空泛准则 | 28题 | <10题 |
| 未诊断 | 100% | 0% |
| defective准则 | 未知 | 检测并删除 |

---

## 待完成工作

### 短期
- [ ] 修改 `export_advisor_schema.py` 支持 `--src` 参数
- [ ] 重新运行 s04L → s11L → s11Lb 流程
- [ ] 验证修复效果并更新报告

### 中期
- [ ] 补充 ref_error_responses 字段（采集真实错误回复文本）
- [ ] 对比 lean 流程与原流程的质量差异

### 长期
- [ ] 将占比校验逻辑泛化到其他题型
- [ ] 建立 rubrics 质量自动化监控面板

---

## 回滚方案

如果修复后效果不佳：

```bash
# 1. 恢复原 s04L_rubric.py
git checkout stages/s04L_rubric.py

# 2. 删除新增脚本
rm stages/s11L_diagnose.py stages/s11Lb_remedy.py

# 3. 使用旧产出
cp outputs/rubrics_advisor_lean.jsonl.bak outputs/rubrics_advisor_lean.jsonl
```

---

## 修复责任人

- **修复实施**: Claude (Opus 5)
- **评审**: 待人工确认
- **集成测试**: 待运行

**下一步**: 运行 `python3 stages/s04L_rubric.py` 并验证修复效果。
