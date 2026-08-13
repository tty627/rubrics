# 方案A执行状态 - 按流程修改重跑

**更新时间**: 2026-08-12 11:48  
**执行方案**: 补跑 s05 grounding，然后重跑 s07-s11b

---

## 流程变更说明

用户添加了2个新步骤：
1. **s05_grounding.py** - Response Grounding（在s04之后）
2. **s11b_remedy.py** - RIFT诊断后处置（在s11之后）

### 正确的流程

```
s04 criteria
  ↓
✨ s05 grounding (新增！检查rubric drift)
  ↓
s07 difficulty
  ↓
s08 penalties
  ↓
s09 normalize
  ↓
s11 diagnose
  ↓
✨ s11b remedy (新增！删除有问题的准则)
```

---

## 当前执行状态

### Track B (方案A重跑)

| 步骤 | 状态 | 说明 |
|------|------|------|
| s05 grounding | ⏳ **运行中** | 使用 glm-ac 判定drift |
| s07 difficulty | ⏸️ 等待 | s05完成后自动开始 |
| s08 penalties | ⏸️ 等待 | |
| s09 normalize | ⏸️ 等待 | |
| s11 diagnose | ⏸️ 等待 | |
| s11b remedy | ⏸️ 等待 | |

**当前**: s05 运行中，有1个Python进程

**预计耗时**:
- s05: 约2-3小时 (452条 × 17.7准则/条 ≈ 8k准则检查)
- s07-s11b: 约1-2小时
- **总计**: 3-5小时

### Track A (Phase 3多模型聚合)

| 步骤 | 状态 | 说明 |
|------|------|------|
| s06a context | ✅ 完成 | 452条 |
| s06c RET | ⏳ **运行中** | deepseek展开perspectives |
| s06d criteria | ⏸️ 等待 | |
| s06_aggregate | ⏸️ 等待 | |

**说明**: Track A 应该也需要补 s05，但先让当前流程跑完。

---

## s05 grounding 详情

### 功能
检查每条准则是否发生 **drift（脱靶漂移）**：
- 准则变得过于理想化
- 脱离实际回答
- 要求了题目没有的东西

### 判定方法
- 对照参考回答（seed中的 glm52/gpt55 回答）
- 使用 glm-ac 模型判定
- 输出: `verdict = "clean" | "drift"`

### 预期结果
- 标记有问题的准则
- 为后续步骤提供过滤依据
- 改善最终rubric质量

---

## 监控命令

```bash
# 实时日志
tail -f logs/track_b_s05_remedy.log

# 进度监控
python3 tools/monitor_plan_a.py

# 检查s05输出
ls -lh data/s05_grounded.jsonl
wc -l data/s05_grounded.jsonl

# 进程状态
ps aux | grep s05 | grep python
```

---

## 完成后的工作

### 1. 验证 s05 效果
```bash
python3 << 'EOF'
import json
from collections import Counter

with open('data/s05_grounded.jsonl') as f:
    recs = [json.loads(line) for line in f]

drift_count = 0
total_criteria = 0

for rec in recs:
    for crit in rec['criteria']:
        total_criteria += 1
        grounding = crit.get('grounding', {})
        if grounding.get('verdict') == 'drift':
            drift_count += 1

print(f"总准则数: {total_criteria}")
print(f"判定drift: {drift_count} ({100*drift_count/total_criteria:.1f}%)")
EOF
```

### 2. 对比 s11b 最终结果
- s05 过滤掉 drift 准则
- s11b 删除 Subjective/Non-Atomic/Ungrounded 准则
- 两者叠加的效果

### 3. Track A 补充
考虑是否需要为 Track A 也补跑 s05：
- s06a/c/d 生成的 criteria
- 聚合前先 grounding
- 再进行聚合

---

## 风险与应对

### 风险1: s05 drift率过高
- 如果 >30% 准则被判drift
- 说明 s04 生成的准则质量有问题
- 需要调整 s04 的 prompt

### 风险2: s05 耗时过长
- 当前设置: WORKERS=20, THINK=false
- 如果太慢: 检查是否卡在某条记录
- 可以提高 WORKERS 到 30-40

### 风险3: Track A 与 Track B 不一致
- Track B 有 s05，Track A 没有
- 最终聚合时可能不对等
- 建议: Track A 完成后也补 s05

---

**当前状态**: ⏳ s05 运行中，自动化流程正常执行
**预计完成**: 今天 14:00-16:00
