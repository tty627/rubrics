# 文档导航

本目录包含项目的所有文档，按用途分类。

---

## 📂 目录结构

```
docs/
├── README.md                           # 本文件（导航）
├── advisor/                            # 给导师的材料
├── reports/                            # 技术报告
├── design/                             # 设计文档
├── dev/                                # 开发文档
├── RUBRICS_PURPOSE_AND_USAGE.md       # Rubrics 用途说明
├── RUBRICS_SCORE_BREAKDOWN.md         # 分值详细说明
└── RUBRICS_XLSX_README.md             # Excel 使用说明
```

---

## 🎯 给导师的材料 (`advisor/`)

**快速开始**: 从 `README_FOR_ADVISOR.md` 开始

| 文档 | 用途 | 推荐阅读时间 |
|------|------|--------------|
| `README_FOR_ADVISOR.md` | 快速导航，推荐从这里开始 | 2 分钟 |
| `presentation_for_advisor.md` | 5-10 分钟演示文档 | 5-10 分钟 |
| `HOW_TO_PRESENT.md` | 演示流程指南 | 3 分钟 |
| `generated_rubrics_samples.md` | 3 个完整案例展示 | 10 分钟 |
| `case_example.md` | 会计学案例详细对比 | 5 分钟 |
| `CHECKLIST.md` | 交付清单 | 2 分钟 |

**推荐阅读顺序**:
1. `README_FOR_ADVISOR.md` - 了解全貌
2. `generated_rubrics_samples.md` - 看实际效果
3. `presentation_for_advisor.md` - 完整报告

---

## 📊 技术报告 (`reports/`)

| 文档 | 内容 | 适用场景 |
|------|------|----------|
| `phase3_report.md` | Phase 3 完整技术报告 | 了解实现细节 |
| `PIPELINE_AUDIT_DETAILED.md` | Pipeline 实现审查报告 | 质量保证 |
| `PIPELINE_FIX_COMPLETE.md` | 问题修复完成报告 | 了解修复内容 |
| `NORMALIZATION_FIX_REPORT.md` | 归一化修复详细报告 | 归一化问题 |
| `ANCHOR_SET_REPORT.md` | 锚点集建设与 glm-ac 判定校准 | 判定器可信度 |
| `PHASE_REDO_CHECKLIST.md` | Phase 重做检查清单 | 迁移指南 |

---

## 📐 设计文档 (`design/`)

| 文档 | 内容 | 来源 |
|------|------|------|
| `rubric_pipeline_feishu_v2.md` | Pipeline 设计（简明版）| 原始设计 |
| `rubric_pipeline_full_v2.md` | Pipeline 设计（完整版）| 原始设计 |
| `PLAN.md` | 实施计划 | 原始设计 |

---

## 💡 用途说明（根目录）

| 文档 | 内容 |
|------|------|
| `RUBRICS_PURPOSE_AND_USAGE.md` | Rubrics 是什么？用来做什么？|
| `RUBRICS_SCORE_BREAKDOWN.md` | 每条准则的分值详细表格 |
| `RUBRICS_XLSX_README.md` | Excel 文件使用说明 |

---

## 🛠️ 开发文档 (`dev/`)

（当前为空，未来可添加开发笔记、调试指南等）

---

## 🔗 相关资源

- **代码**: `../stages/` - 流水线步骤实现
- **数据**: `../data/` - 生成的数据文件
- **输出**: `../outputs/` - Excel 和样例输出
- **脚本**: `../scripts/` - 辅助脚本
- **开发指南**: `../CLAUDE.md` - AI 开发上下文

---

## 📖 推荐阅读路径

### 快速了解（10 分钟）
1. 根目录 `README.md` - 项目概览
2. `advisor/README_FOR_ADVISOR.md` - 成果展示
3. `advisor/generated_rubrics_samples.md` - 实际案例

### 深入了解（30 分钟）
4. `advisor/presentation_for_advisor.md` - 完整报告
5. `RUBRICS_PURPOSE_AND_USAGE.md` - 用途说明
6. `reports/phase3_report.md` - 技术细节

### 技术审查（1 小时）
7. `design/rubric_pipeline_feishu_v2.md` - 设计方案
8. `reports/PIPELINE_AUDIT_DETAILED.md` - 实现审查
9. `reports/PIPELINE_FIX_COMPLETE.md` - 修复记录

---

**文档版本**: 2026-08-12  
**最后更新**: 仓库整理后
