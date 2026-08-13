# 🎯 给用户的快速开始指南

## 是的，修复已完整完成！现在可以重新运行。

---

## 📍 关键文件位置（一目了然）

### 🏃 运行这个命令
```bash
bash scripts/rerun_lean_fixed.sh
```

### 📁 数据会存在这些地方

| 文件 | 位置 | 当前状态 | 运行后 |
|------|------|---------|--------|
| **输入** | `data/s03_perspective_lean.jsonl` | ✅ 5.5MB | 不变 |
| **中间1** | `data/s04L_rubric.jsonl` | ✅ 6.4MB（旧） | 🔄 覆盖（新） |
| **中间2** | `data/s11L_diagnosed.jsonl` | ❌ 无 | ⭐ 新生成 |
| **中间3** | `data/s11Lb_remedied.jsonl` | ❌ 无 | ⭐ 新生成 |
| **最终产出** | `outputs/rubrics_advisor_lean.jsonl` | ✅ 1.2MB（旧） | 🔄 覆盖（新） |
| **备份** | `outputs/rubrics_advisor_lean.jsonl.bak` | ❌ 无 | ⭐ 自动备份 |

### 💾 缓存位置（自动生成）
```
cache/s04L/          ← 已有 455 个文件（1.0MB）
cache/s11L_subj/     ← 运行后生成（约 2MB）
cache/s11L_non-/     ← 运行后生成（约 2MB）
cache/s11L_ungr/     ← 运行后生成（约 2MB）
```

---

## 🚀 运行步骤（只需3步）

### 1️⃣ 运行前检查（可选）
```bash
python3 scripts/check_before_run.py
```
会显示：
- ✅ 输入是否存在
- 📊 当前数据状态
- 📈 预估调用次数和时间

### 2️⃣ 一键运行修复流程
```bash
bash scripts/rerun_lean_fixed.sh
```
会自动执行：
1. 备份旧产出（→ `.bak`）
2. 运行 s04L（准则生成，修复后）
3. 运行 s11L（RIFT 诊断）
4. 运行 s11Lb（删除 defective 准则）
5. 导出交付版本
6. 运行验证对比

⏱️ **预估时间**：
- 有缓存：约 10-20 分钟（只运行 prompt 改变的部分）
- 无缓存：约 1-2 小时（全新调用 ~10000 次）

### 3️⃣ 查看修复效果
```bash
python3 scripts/test_s04L_fixes.py
```
会对比：
- ✅ 答案项占比修复情况
- ✅ 负向准则编造数值减少情况
- ✅ 空泛准则改善情况

---

## 📊 实时监控（可选）

运行过程中，另开一个终端：
```bash
python3 tools/watch_v2.py
```
可以看到：
- 当前进度（步骤 / 完成数 / 总数）
- Token 用量和成本
- 调用速率和成功率

---

## 🔍 查看生成的数据

### 快速查看文件
```bash
# 查看新生成的文件列表
ls -lh data/s04L_rubric.jsonl
ls -lh data/s11L_diagnosed.jsonl
ls -lh data/s11Lb_remedied.jsonl
ls -lh outputs/rubrics_advisor_lean.jsonl
```

### 查看某条记录（以 q0303 为例）
```bash
# 查看修复后的准则
grep '"rid": "q0303"' outputs/rubrics_advisor_lean.jsonl | python3 -m json.tool

# 查看诊断结果
grep '"rid": "q0303"' data/s11L_diagnosed.jsonl | python3 -m json.tool | grep -A 5 diagnoses
```

### 统计关键指标
```bash
# 统计删除了多少 defective 准则
grep '"is_defective": true' data/s11L_diagnosed.jsonl | wc -l

# 统计每题准则数分布
grep -o '"core_n": [0-9]*' data/s04L_rubric.jsonl | cut -d: -f2 | sort | uniq -c
```

---

## 📖 详细文档（需要时查看）

1. **数据流向详解**: `DATA_LOCATIONS.md` 或 `docs/DATA_FLOW_GUIDE.md`
2. **发现的问题**: `docs/reports/RUBRICS_REVIEW_FINDINGS.md`
3. **修复实施指南**: `docs/reports/S04L_FIX_GUIDE.md`
4. **工作总结**: `docs/reports/FIX_SUMMARY_20260812.md`

---

## 🆘 遇到问题？

### 清理缓存重新运行
```bash
rm -rf cache/s04L/ cache/s11L_*/
bash scripts/rerun_lean_fixed.sh
```

### 回滚到修复前
```bash
cp outputs/rubrics_advisor_lean.jsonl.bak outputs/rubrics_advisor_lean.jsonl
```

### 查看运行日志
```bash
# 查看最近的 LLM 调用
tail -20 cache/_events.jsonl | jq .
```

---

## ✅ 确认清单

- [x] 代码修复完成
- [x] 文档已更新
- [x] 运行脚本已就绪
- [x] 验证脚本已就绪
- [ ] **您需要做的**：运行 `bash scripts/rerun_lean_fixed.sh`

---

## 🎉 预期结果

运行完成后，您会得到：

1. **修复后的 rubrics**: `outputs/rubrics_advisor_lean.jsonl`
   - ✅ verifiable 题答案项占比 60-80%
   - ✅ 负向准则不编造具体数值
   - ✅ 准则表述更具体
   - ✅ 删除了 defective 准则

2. **对比报告**: 自动显示修复前后的改善
   - 答案项占比问题：21题 → 0题
   - 负向准则编造：23题 → <5题
   - 空泛准则：28题 → <10题

3. **完整的中间数据**: 可追溯每一步的处理结果

---

**准备好了吗？运行这个命令开始：**
```bash
bash scripts/rerun_lean_fixed.sh
```
