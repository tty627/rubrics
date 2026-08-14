# Lean 流程数据存储位置清单

## 📂 输入数据（已存在，不会改变）

```
data/s03_perspective_lean.jsonl
  ├─ 来源: 步骤 3 视角生成（s03_perspective_lean.py）
  ├─ 内容: 452 条记录，每条含 perspectives[] (评价视角列表)
  └─ 大小: ~XX MB（查看: ls -lh data/s03_perspective_lean.jsonl）
```

---

## 🔄 中间数据（运行后新生成）

### 1. s04L - 准则直出

**主产出**:
```
data/s04L_rubric.jsonl
  ├─ 来源: python3 stages/s04L_rubric.py
  ├─ 内容: 452 条，每条含 rubrics[] (6-8 条准则)
  ├─ 字段: rid, rubrics, s_max, core_n, core_n_positive
  └─ 修复点: 
      - 答案项占比自动调整到 60-80%
      - 负向准则不编造具体数值
      - 准则表述具体化
```

**缓存**:
```
cache/s04L/
  ├─ *.json  ← 每个文件对应一次 LLM 调用
  ├─ 命名规则: {hash}.json (hash = sha256(model+prompt+params)[:32])
  └─ 用途: 避免重复调用，修改 prompt 后部分缓存失效
```

**调用事件流水** (可选):
```
cache/_events.jsonl
  ├─ 每行记录一次 LLM 调用
  ├─ 字段: stage, model, messages, output, usage, timestamp
  └─ 监控: python3 tools/watch_v2.py
```

---

### 2. s11L - RIFT 诊断

**主产出**:
```
data/s11L_diagnosed.jsonl
  ├─ 来源: python3 stages/s11L_diagnose.py
  ├─ 内容: 在 s04L_rubric.jsonl 基础上添加 diagnoses[] 字段
  ├─ diagnoses[] 包含:
  │   ├─ _criterion_id: 准则ID
  │   ├─ is_defective: true/false
  │   ├─ failure_modes: ['subjective', 'non-atomic', 'ungrounded']
  │   └─ details: 每个失效模式的诊断原因
  └─ 统计: 会输出 defective 准则数量和失效模式分布
```

**缓存**:
```
cache/s11L_subj/  ← Subjective (主观性) 诊断缓存
cache/s11L_non-/  ← Non-Atomic (非原子性) 诊断缓存
cache/s11L_ungr/  ← Ungrounded (脱靶) 诊断缓存
  └─ 每条准则 × 3 个模式 = ~1356 次调用
```

---

### 3. s11Lb - 诊断处置

**主产出**:
```
data/s11Lb_remedied.jsonl
  ├─ 来源: python3 stages/s11Lb_remedy.py
  ├─ 内容: 删除 is_defective=true 的准则，重算满分
  ├─ 新增字段:
  │   ├─ criteria_before_remedy: 删除前准则数
  │   ├─ criteria_after_remedy: 删除后准则数
  │   ├─ criteria_removed: 删除了几条
  │   ├─ removed_criterion_ids: 删除的准则ID列表
  │   └─ remedy_skipped: 是否跳过处置（删除后准则不足）
  └─ 统计: 会输出执行处置的题数、跳过的题数、删除准则总数
```

---

## 📤 最终产出（交付给导师）

```
outputs/rubrics_advisor_lean.jsonl
  ├─ 来源: 从 data/s04Lb_split.jsonl 导出（--src 指定，默认源是未拆分版，勿用）
  ├─ 内容: 只保留交付字段，去掉内部字段（_开头的）
  ├─ 交付字段:
  │   ├─ rid, xlsx_row, question, subject, question_type, intent
  │   ├─ full_mark: 满分
  │   └─ rubrics[]: 
  │       ├─ criteria: 准则文本
  │       ├─ score: 分值
  │       ├─ reason: 原因
  │       ├─ dimension: 维度
  │       ├─ is_positive: 正向/负向（方向）
  │       └─ is_gate: 0/1 阀门标记，gated_answer 题的答案判据
  └─ 用途: 给导师展示，填入 Excel
```

**备份**:
```
outputs/rubrics_advisor_lean.jsonl.bak  ← 运行前自动备份旧版本
```

---

## 🔍 验证和对比

```
scripts/test_s04L_fixes.py
  ├─ 对比: outputs/rubrics_advisor_lean.jsonl.bak (修复前)
  │         vs
  │         data/s04L_rubric.jsonl (修复后)
  ├─ 检查:
  │   ├─ 答案项占比偏离
  │   ├─ 负向准则编造数值
  │   ├─ 空泛准则
  │   └─ 改善统计
  └─ 运行: python3 scripts/test_s04L_fixes.py
```

---

## 📊 监控和日志

### 实时监控（运行过程中）
```bash
# 另开一个终端
python3 tools/watch_v2.py
```
显示:
- 当前步骤进度
- Token 用量
- 调用成功率
- 速率趋势

### 事件流水（事后查看）
```bash
# 查看最近 20 条调用
tail -20 cache/_events.jsonl | jq .

# 查看某个步骤的所有调用
grep '"stage": "s04L"' cache/_events.jsonl | wc -l
```

---

## 🗂️ 完整目录结构

```
rubrics/
├── data/                         ← 中间数据（每步产出）
│   ├── s03_perspective_lean.jsonl      输入（已存在）
│   ├── s04L_rubric.jsonl               ← 运行后生成
│   ├── s11L_diagnosed.jsonl            ← 运行后生成
│   └── s11Lb_remedied.jsonl            ← 运行后生成
│
├── outputs/                      ← 最终交付
│   ├── rubrics_advisor_lean.jsonl      ← 运行后生成
│   └── rubrics_advisor_lean.jsonl.bak  自动备份
│
├── cache/                        ← LLM 调用缓存
│   ├── s04L/                     ← 运行后生成
│   ├── s11L_subj/                ← 运行后生成
│   ├── s11L_non-/                ← 运行后生成
│   ├── s11L_ungr/                ← 运行后生成
│   └── _events.jsonl             调用流水（可选）
│
├── stages/                       ← 执行脚本
│   ├── s04L_rubric.py            修复后
│   ├── s11L_diagnose.py          新建
│   └── s11Lb_remedy.py           新建
│
└── scripts/                      ← 工具脚本
    ├── rerun_lean_fixed.sh       ← 一键运行
    └── test_s04L_fixes.py        验证脚本
```

---

## ⚡ 快速开始

### 1. 查看输入是否存在
```bash
ls -lh data/s03_perspective_lean.jsonl
```

### 2. 运行修复流程
```bash
bash scripts/rerun_lean_fixed.sh
```

### 3. 查看生成的文件
```bash
ls -lh data/s04L_rubric.jsonl
ls -lh data/s11L_diagnosed.jsonl
ls -lh data/s11Lb_remedied.jsonl
ls -lh outputs/rubrics_advisor_lean.jsonl
```

### 4. 验证修复效果
```bash
python3 scripts/test_s04L_fixes.py
```

---

## 📈 预期文件大小（参考）

| 文件 | 预期大小 | 记录数 |
|------|---------|--------|
| s03_perspective_lean.jsonl | ~500KB | 452 |
| s04L_rubric.jsonl | ~1.2MB | 452 |
| s11L_diagnosed.jsonl | ~1.8MB | 452 |
| s11Lb_remedied.jsonl | ~1.5MB | 452 |
| rubrics_advisor_lean.jsonl | ~1.2MB | 452 |

缓存目录大小:
- cache/s04L/: ~2MB (452 次调用)
- cache/s11L_*/: ~6MB (1356 次调用)

---

## 🚨 常见问题

### Q: 缓存太多，磁盘占用大？
```bash
# 清理旧缓存（会重新调用 LLM）
rm -rf cache/s04L/
rm -rf cache/s11L_*/
```

### Q: 想看某一步的详细输出？
```bash
# 查看 s04L 第一条记录
head -1 data/s04L_rubric.jsonl | python3 -m json.tool | less

# 查看诊断了多少 defective
grep '"is_defective": true' data/s11L_diagnosed.jsonl | wc -l
```

### Q: 运行失败了，怎么回滚？
```bash
# 恢复旧版本
cp outputs/rubrics_advisor_lean.jsonl.bak outputs/rubrics_advisor_lean.jsonl

# 删除中间产出
rm data/s04L_rubric.jsonl data/s11L_diagnosed.jsonl data/s11Lb_remedied.jsonl
```
