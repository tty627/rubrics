# 数据存储位置 - 快速查找

## 🎯 你最关心的文件

### 输入（已存在）
```
data/s03_perspective_lean.jsonl
  ✅ 当前: 5.5MB, 452条
  📝 作用: 输入数据，包含每题的评价视角
```

### 最终产出（运行后查看这个）
```
outputs/rubrics_advisor_lean.jsonl
  ✅ 当前: 1.2MB, 452条（旧版本，运行后会更新）
  📝 作用: 修复后的最终 rubrics，给导师看的
  🔄 备份: 运行前自动备份为 .jsonl.bak
```

---

## 📊 中间数据（按顺序生成）

### 第1步：s04L - 准则生成（修复后）
```bash
python3 stages/s04L_rubric.py
```
**产出位置**:
```
data/s04L_rubric.jsonl
  ✅ 当前: 6.4MB, 452条（旧版本，运行后会覆盖）
  📝 内容: 每题 6-8 条准则，修复了：
          - 答案项占比自动调整到 60-80%
          - 负向准则不编造具体数值
          - 准则表述具体化
```

**缓存位置**:
```
cache/s04L/
  ✅ 当前: 455个文件, 1.0MB
  📝 作用: LLM 调用缓存，避免重复调用
  💡 提示: prompt 改变后，只有受影响的部分会重新调用
```

---

### 第2步：s11L - RIFT 诊断
```bash
python3 stages/s11L_diagnose.py
```
**产出位置**:
```
data/s11L_diagnosed.jsonl
  ❌ 当前: 不存在（运行后会生成）
  📝 内容: 在 s04L 基础上添加 diagnoses[] 字段
          每条准则标记是否 defective（主观/非原子/脱靶）
  🔍 查看: grep '"is_defective": true' data/s11L_diagnosed.jsonl | wc -l
```

**缓存位置**:
```
cache/s11L_subj/  ← Subjective (主观性) 诊断
cache/s11L_non-/  ← Non-Atomic (非原子性) 诊断  
cache/s11L_ungr/  ← Ungrounded (脱靶) 诊断
  ❌ 当前: 不存在（运行后会生成）
  📝 作用: 每条准则 × 3 个诊断模式 = ~3000+ 次调用
  💾 预估: 约 6-8 MB
```

---

### 第3步：s11Lb - 删除 defective 准则
```bash
python3 stages/s11Lb_remedy.py
```
**产出位置**:
```
data/s11Lb_remedied.jsonl
  ❌ 当前: 不存在（运行后会生成）
  📝 内容: 删除 is_defective=true 的准则，重新计算满分
          保留删除记录（criteria_before_remedy / after / removed）
  🔍 查看: grep '"criteria_removed"' data/s11Lb_remedied.jsonl | head -5
```

---

## 🗺️ 数据流向（一图看懂）

```
┌─────────────────────────────────────────────────────────────┐
│  输入: data/s03_perspective_lean.jsonl (5.5MB)              │
│  452 题，每题含 perspectives[] (评价视角)                   │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
         ┌────────────────────────────────┐
         │  步骤 4L: 准则生成（修复后）    │
         │  python3 stages/s04L_rubric.py │
         └────────────────┬───────────────┘
                          │ 缓存: cache/s04L/ (1.0MB)
                          ▼
         ┌────────────────────────────────────────┐
         │  data/s04L_rubric.jsonl (6.4MB)        │
         │  452 题，每题 6-8 条准则                │
         │  ✅ 答案项占比 60-80%                   │
         │  ✅ 负向准则不编造数值                  │
         └────────────────┬───────────────────────┘
                          │
                          ▼
         ┌────────────────────────────────┐
         │  步骤 11L: RIFT 诊断            │
         │  python3 stages/s11L_diagnose.py│
         └────────────────┬───────────────┘
                          │ 缓存: cache/s11L_*/ (~6MB)
                          ▼
         ┌─────────────────────────────────────────┐
         │  data/s11L_diagnosed.jsonl              │
         │  添加 diagnoses[] 字段                   │
         │  标记 Subjective/Non-Atomic/Ungrounded  │
         └────────────────┬────────────────────────┘
                          │
                          ▼
         ┌────────────────────────────────┐
         │  步骤 11Lb: 诊断处置            │
         │  python3 stages/s11Lb_remedy.py │
         └────────────────┬───────────────┘
                          │
                          ▼
         ┌──────────────────────────────────────┐
         │  data/s11Lb_remedied.jsonl           │
         │  删除 defective 准则，重新计算满分   │
         └────────────────┬─────────────────────┘
                          │
                          ▼
         ┌────────────────────────────────────┐
         │  导出交付 schema                   │
         │  去掉内部字段（_开头）             │
         └────────────────┬───────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  outputs/rubrics_advisor_lean.jsonl (1.2MB)                  │
│  最终交付版本，给导师看                                       │
│  备份: .jsonl.bak                                            │
└──────────────────────────────────────────────────────────────┘
```

---

## 🔍 快速查看数据

### 查看某一步的第一条记录
```bash
# s04L 输出
head -1 data/s04L_rubric.jsonl | python3 -m json.tool | less

# 诊断结果
head -1 data/s11L_diagnosed.jsonl | python3 -m json.tool | less

# 最终产出
head -1 outputs/rubrics_advisor_lean.jsonl | python3 -m json.tool | less
```

### 统计关键指标
```bash
# 每题准则数
grep -o '"core_n": [0-9]*' data/s04L_rubric.jsonl | cut -d: -f2 | sort -n | uniq -c

# defective 准则数
grep '"is_defective": true' data/s11L_diagnosed.jsonl | wc -l

# 删除了多少准则
grep -o '"criteria_removed": [0-9]*' data/s11Lb_remedied.jsonl | cut -d: -f2 | awk '{sum+=$1} END {print sum}'
```

### 查找特定题目
```bash
# 查找 q0303
grep '"rid": "q0303"' data/s04L_rubric.jsonl | python3 -m json.tool
```

---

## 📂 目录树（关键路径）

```
rubrics/
│
├── data/                               ← 中间数据（按步骤）
│   ├── s03_perspective_lean.jsonl      ✅ 输入（已存在）
│   ├── s04L_rubric.jsonl               🔄 步骤4L产出（将覆盖）
│   ├── s11L_diagnosed.jsonl            ⭐ 步骤11L产出（新生成）
│   └── s11Lb_remedied.jsonl            ⭐ 步骤11Lb产出（新生成）
│
├── outputs/                            ← 最终交付
│   ├── rubrics_advisor_lean.jsonl      🎯 给导师的版本
│   └── rubrics_advisor_lean.jsonl.bak  💾 自动备份
│
├── cache/                              ← LLM 缓存
│   ├── s04L/                           ✅ 已有缓存（1.0MB）
│   ├── s11L_subj/                      ⭐ 运行后生成
│   ├── s11L_non-/                      ⭐ 运行后生成
│   └── s11L_ungr/                      ⭐ 运行后生成
│
└── docs/                               ← 说明文档
    ├── DATA_FLOW_GUIDE.md              📖 本文档详细版
    └── reports/
        ├── RUBRICS_REVIEW_FINDINGS.md  🔍 发现的问题
        └── S04L_FIX_GUIDE.md           🛠️  修复指南
```

---

## ⚡ 一键运行 + 查看结果

### 运行
```bash
bash scripts/rerun_lean_fixed.sh
```

### 查看新生成的文件
```bash
ls -lh data/s04L_rubric.jsonl
ls -lh data/s11L_diagnosed.jsonl
ls -lh data/s11Lb_remedied.jsonl
ls -lh outputs/rubrics_advisor_lean.jsonl
```

### 对比修复前后
```bash
python3 scripts/test_s04L_fixes.py
```

---

## 💡 提示

1. **缓存机制**: 修改 prompt 后，只有受影响的调用会重新执行，其他走缓存
2. **监控进度**: 运行过程中可以用 `python3 tools/watch_v2.py` 实时查看
3. **备份安全**: 旧版本自动备份为 `.bak`，可随时回滚
4. **分步运行**: 也可以单独运行每个脚本，而不是用一键脚本

---

## 🆘 需要帮助？

- 详细数据流向: `cat docs/DATA_FLOW_GUIDE.md`
- 修复实施指南: `cat docs/reports/S04L_FIX_GUIDE.md`
- 运行前检查: `python3 scripts/check_before_run.py`
