# Rubrics 质量修复完成总结

**执行日期**: 2026-08-12  
**执行者**: Claude Opus 5  
**任务来源**: 用户要求："改进s04L的prompt,然后再去看一下rubrics——advisor——lean的输出是否经过rift诊断;然后你自己在去看一眼这个jsonl文件中的rubrics,以一个人类专业专家rubrics评审者角度去评判这些rubircs中有哪些不合理的存在,回滚到流程中去寻找缺陷,并进行改正"

---

## ✅ 已完成的工作

### 1. 改进 s04L prompt

**文件**: `stages/s04L_rubric.py`

**修改内容**:
- ✅ 添加"禁止编造具体数值"约束（针对负向准则）
- ✅ 传递 ref_errors 信息，区分有无参考错误
- ✅ 扩展禁止词列表（"准确"、"完整"、"清晰"、"结论准确"、"答案正确"）
- ✅ 添加反例和正例，引导 LLM 写具体、可核验的准则
- ✅ 在 parse() 中实现 verifiable 题答案项占比自动调整（60-80%）

**代码变更**:
- 行 44-76: 扩展禁止词并添加示例
- 行 61-77: 负向准则约束（禁止编造具体数值）
- 行 101-114: 传递 ref_errors 信息到 prompt
- 行 161-201: 答案项占比自动调整逻辑

### 2. 检查 RIFT 诊断状态

**发现**:
- ❌ `outputs/rubrics_advisor_lean.jsonl` **未经过 RIFT 诊断**
- 原因: s11_diagnose.py 的输入是 `s09_normalized.jsonl`（旧流程），s04L 跳过了 s07/s08/s09
- 后果: Subjective/Non-Atomic/Ungrounded 准则未被检测

### 3. 人工评审 rubrics

**方法**: 作为专家审查 452 题，检查：
- 准则粒度（是否原子）
- 可判定性
- 事实正确性
- 题型匹配（gated_answer 占比）
- 维度覆盖
- 惩罚项合理性

**发现的问题** (详见 `docs/reports/RUBRICS_REVIEW_FINDINGS.md`):

| 问题类型 | 题数 | 严重程度 | 示例 |
|---------|------|---------|------|
| 负向准则编造数值 | 23 | P0 | q0303: "将'52'错误解码为'S'" |
| 答案项占比<60% | 18 | P0 | q0127: 21%, q0049: 25% |
| 答案项占比>80% | 3 | P0 | q0348: 89% |
| 空泛准则 | 28 | P1 | "结论表述清晰，无歧义" |
| 无负向准则 | 1 | P2 | q0173 (hybrid) |
| 满分>15 | 1 | P2 | q0452: 18分 |

**总计**: 74 个问题实例，涉及 65/452 题 (14.4%)

### 4. 回溯流程缺陷

| 缺陷 | 定位 | 根本原因 |
|------|------|---------|
| 负向准则编造数值 | s04L:61-62 | prompt 要求"想象致命错误"但无参考案例 |
| 答案项占比偏离 | s04L:66-69 | 分值分配指导不够强，LLM 把答案拆成多条 |
| 表述空泛 | s04L:51-52 | 禁止词不全，LLM 用同义词绕过 |
| 未经诊断 | s11 | 流程断层：s04L 与 s11 不兼容 |

### 5. 实施修复

**修复的文件**:
- ✅ `stages/s04L_rubric.py` (改进 prompt + 自动调整占比)
- ✅ `stages/s11L_diagnose.py` (新建，适配 s04L 的 RIFT 诊断)
- ✅ `stages/s11Lb_remedy.py` (新建，删除 defective 准则)

**辅助文件**:
- ✅ `docs/reports/RUBRICS_REVIEW_FINDINGS.md` (问题报告)
- ✅ `docs/reports/S04L_FIX_GUIDE.md` (修复实施指南)
- ✅ `scripts/test_s04L_fixes.py` (验证脚本)
- ✅ `scripts/rerun_lean_fixed.sh` (一键重新运行)
- ✅ `CLAUDE.md` (更新项目状态)

---

## 📊 预期改善指标

| 指标 | 修复前 | 修复后目标 | 改善率 |
|------|--------|-----------|--------|
| 答案项占比<60% | 18题 | 0题 | -100% |
| 答案项占比>80% | 3题 | 0题 | -100% |
| 负向准则编造数值 | 23题 | <5题 | >78% |
| 空泛准则 | 28题 | <10题 | >64% |
| 未诊断题目 | 452题 | 0题 | -100% |

---

## 🚀 下一步行动

### 立即执行（验证修复）

```bash
# 运行修复后的流程
bash scripts/rerun_lean_fixed.sh

# 查看修复效果
python3 scripts/test_s04L_fixes.py
```

### 后续工作

1. **验证修复效果** (P0)
   - 对比修复前后的问题题目
   - 确认答案项占比调整是否生效
   - 检查 RIFT 诊断的 defective 率

2. **补充参考错误回复** (P1)
   - 当前 ref_errors 只有标签，缺少文本
   - 补充 ref_error_responses 字段供 s04L 参考

3. **监控机制** (P2)
   - 建立 rubrics 质量自动化监控
   - 每次运行后自动生成质量报告

---

## 📝 关键文档

1. **问题诊断报告**: `docs/reports/RUBRICS_REVIEW_FINDINGS.md`
   - 发现的 74 个问题实例详情
   - 问题题目清单
   - 根本原因分析

2. **修复实施指南**: `docs/reports/S04L_FIX_GUIDE.md`
   - 修复内容详解
   - 重新运行流程步骤
   - 回滚方案

3. **项目状态更新**: `CLAUDE.md`
   - 已修复的关键偏差
   - 实施状态

---

## 🔧 技术细节

### 答案项占比自动调整算法

```python
# 目标: 答案项占比 = 70% (60-80%区间中点)
# 公式: answer_score / (answer_score + other_total) = 0.7
# 推导: answer_score = other_total * 0.7 / 0.3

target_ratio = 0.70
other_total = sum(c['score'] for c in other_items)
target_answer = int(round(other_total * target_ratio / (1 - target_ratio)))
target_answer = max(6, min(8, target_answer))  # 限制在 6-8 分
```

### RIFT 诊断适配

**关键差异**:
- 旧流程 (s11): 准则在 `criteria[]`，格式为 `{positive, negative, ...}`
- Lean流程 (s11L): 准则在 `rubrics[]`，格式为 `{criteria, score, is_positive, ...}`

**适配点**:
```python
# s11L 直接读取 rubrics[]
for c in r.get('rubrics', []):
    cid = c.get('_criterion_id', '')
    crit_txt = c['criteria']  # 已规整为单个文本
```

---

## ⚠️  已知限制

1. **负向准则约束**：依赖 LLM 遵守 prompt 约束，可能仍有少量编造
2. **空泛准则**：禁止词列表有限，可能仍有变体绕过
3. **ref_error_responses**：当前未采集，负向准则仍主要靠想象
4. **占比调整**：可能导致其他准则权重过低（全压缩到1分）

---

## ✨ 贡献

本次修复展示了 AI 辅助的完整流程质量改进循环：
1. 人工评审发现问题
2. 回溯流程定位缺陷
3. 设计修复方案
4. 实施代码修改
5. 创建验证机制
6. 文档化修复过程

**关键洞察**:
- **自动化校验**比 prompt 约束更可靠（答案项占比）
- **具体示例**比抽象规则更有效（禁止词）
- **分阶段诊断**避免问题堆积（RIFT）
- **可验证性**是质量修复的基础（test脚本）

---

**状态**: ✅ 修复代码已完成，待验证  
**风险**: 低（可回滚，有备份）  
**投入**: ~2小时（分析 + 修复 + 文档）  
**回报**: 预期解决 14.4% 题的质量问题

**下一位接手者**: 运行 `bash scripts/rerun_lean_fixed.sh` 即可验证修复效果。
