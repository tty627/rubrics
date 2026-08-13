# Rubric 自动生成流水线

基于多模型协作和递归展开的 Rubric 自动生成系统，用于线上回复质量评估和 Bad Case 挖掘。

---

## 🎯 项目简介

**核心能力**: 自动生成细粒度、多维度的评分标准（Rubric），用于评估 AI 回复质量

**应用场景**:
- 线上 Bad Case 自动挖掘（得分 < 60 的回复）
- 回复质量监控与对比
- 训练数据自动筛选
- 人工标注工作量减少 60-70%

**技术特点**:
- 多维度展开（RET 递归展开算法）
- 多模型聚合（减少视角偏差）
- RIFT 质量诊断（8 种失效模式）
- 满分归一化（所有题目统一 100 分）

---

## 📊 当前状态

**Phase 3 已完成** ✅

- ✅ 生成 452 个 rubrics（覆盖多学科）
- ✅ 满分统一为 100（可设置统一阈值）
- ✅ 平均 30.5 条准则/题（细粒度评估）
- ⚠️ RIFT 诊断已跑，但 87.5% 的准则命中缺陷（详见下方"已知问题"）

**Phase 4 待做** (判分验证)

- ⏸️ 生成回复池（多模型 × 多档质量）
- ⏸️ 判分验证（与人工标注对比）
- ⏸️ Bad Case 提取

---

## 🚀 快速开始

### 查看生成的 Rubrics

```bash
# 1. 打开 Excel 文件（推荐）
open "outputs/excel/Untitled spreadsheet (已填充rubrics).xlsx"

# 2. 或查看样例文档
cat docs/advisor/generated_rubrics_samples.md
```

### 给导师展示

```bash
# 从这里开始
cat docs/advisor/README_FOR_ADVISOR.md
```

### 运行 Pipeline

```bash
# 运行完整流程（需要 API 配额）
python run_pipeline.py

# 或逐步运行
python stages/s01_filter.py
python stages/s02_context.py
# ...
```

---

## 📁 目录结构

```
rubrics/
├── README.md                    # 本文件（项目概览）
├── CLAUDE.md                    # AI 开发上下文
├── run_pipeline.py              # 主运行脚本
│
├── stages/                      # 流水线步骤（核心代码）
│   ├── s01_filter.py           # 入口过滤
│   ├── s02_context.py          # 上下文标签
│   ├── s03_perspective.py      # 视角展开（RET）
│   ├── s04_criteria.py         # 准则实例化
│   ├── s05_grounding.py        # Response Grounding
│   ├── s06_aggregate.py        # 多模型聚合
│   ├── s07_difficulty.py       # 难度演化
│   ├── s08_penalties.py        # 惩罚项
│   ├── s09_normalize.py        # 归一化
│   ├── s11_diagnose.py         # RIFT 诊断
│   └── s11b_remedy.py          # 处置
│
├── lib/                         # 工具库
│   ├── stage.py                # 流水线工具
│   ├── xlsx.py                 # Excel 读写
│   └── rift.py                 # RIFT 诊断
│
├── scripts/                     # 辅助脚本
│   ├── fill_xlsx_preserve_format.py  # Excel 填充
│   └── quick_stats.py          # 快速统计
│
├── data/                        # 数据文件
│   ├── s09_normalized.jsonl    # Phase 3 最终产出
│   └── ...
│
├── docs/                        # 文档中心
│   ├── README.md               # 文档导航
│   ├── advisor/                # 给导师的材料 ⭐
│   ├── reports/                # 技术报告
│   └── design/                 # 设计文档
│
├── outputs/                     # 输出产物
│   ├── excel/                  # Excel 文件
│   └── samples/                # 样例展示
│
├── logs/                        # 日志
├── cache/                       # 缓存
└── config/                      # 配置
```

---

## 📖 文档导航

### 给导师/评审看

**推荐从这里开始** → [`docs/advisor/README_FOR_ADVISOR.md`](docs/advisor/README_FOR_ADVISOR.md)

或直接看：
- [生成的样例](docs/advisor/generated_rubrics_samples.md) - 3 个完整案例
- [演示文档](docs/advisor/presentation_for_advisor.md) - 5-10 分钟版本
- [如何演示](docs/advisor/HOW_TO_PRESENT.md) - 演示流程

### 了解 Rubrics 是什么

- [Rubrics 用途说明](docs/RUBRICS_PURPOSE_AND_USAGE.md) - 用来做什么？
- [分值详细说明](docs/RUBRICS_SCORE_BREAKDOWN.md) - 每条准则多少分？
- [Excel 使用说明](docs/RUBRICS_XLSX_README.md) - 如何使用生成的文件

### 技术细节

- [Phase 3 报告](docs/reports/phase3_report.md) - 完整技术报告
- [Pipeline 审查](docs/reports/PIPELINE_AUDIT_DETAILED.md) - 实现质量审查
- [修复报告](docs/reports/PIPELINE_FIX_COMPLETE.md) - 问题修复记录

### 设计方案

- [设计文档（简明版）](docs/design/rubric_pipeline_feishu_v2.md)
- [设计文档（完整版）](docs/design/rubric_pipeline_full_v2.md)

---

## 🎓 核心概念

### Rubric 是什么？

**Rubric** = 评分标准，包含多条准则，每条准则判定"满足/不满足"

**例子**（会计学题目）:
```
满分: 100
准则 1 (6.67分): 明确指出合同负债产生于已收客户对价但尚未履约
准则 2 (6.67分): 明确指出应付账款产生于已接受商品但尚未付款
...
准则 15 (6.67分): 明确指出合同负债随履约义务完成结转为收入
```

### 如何使用？

**Phase 4 判分**（待实现）:
```python
# 用 rubric 给回复打分
score = judge(response, rubric)  # 0-100

# 识别 bad case
if score < 60:
    mark_as_badcase(response)
```

**线上应用**:
- 实时监控回复质量
- 自动识别问题回复
- 诊断失败原因（哪些准则没满足）

### 为什么需要？

**传统方式**:
- ❌ 人工评审（成本高、速度慢）
- ❌ 简单打分（颗粒度粗，无法诊断）

**Rubrics 优势**:
- ✅ 自动化（秒级判分）
- ✅ 细粒度（15-30 条准则）
- ✅ 可诊断（差在哪里）
- ✅ 可扩展（百万级流量）

---

## 🔢 核心数据

### Phase 3 成果

| 指标 | 草稿 (F列) | 生成 (C列) | 改善 |
|------|------------|------------|------|
| 维度数 | 1 | 3.5 | +246% |
| 准则数 | 6.1 | 30.5 | +400% |
| 满分 | 21 | 100 | 统一 |

### 质量指标

- ✅ **452 条 rubrics** 生成完成，13,788 条准则
- ✅ **满分统一** 所有题目 100 分（可全局设 badcase 阈值）
- ⚠️ **RIFT 诊断待处置**：87.5% 命中缺陷，尚未修复

### ⚠️ 已知问题（Phase 4 前需处理）

RIFT 免池诊断（s11）的实际结果：

| 失效模式 | 命中率 | 判定可信度 |
|----------|--------|-----------|
| non-atomic | **80.8%** | 高 —— 对比题常把两个主体捆在一条准则里，二元判定会失效 |
| ungrounded | 35.1% | 中 —— 含相当比例过严误判，需复核 |
| subjective | 1.9% | 高 |

s11b 默认 `policy=mark`：只标注待处置动作（`split` / `review` / `rewrite`），
不删准则。若设 `RP_REMEDY_POLICY=delete` 会删掉 87.5%，故不作为默认。

**待做**：实现 s11c 按 `remedy_action=split` 拆分非原子准则。这是 Phase 4
判分前的必要前置 —— 非原子准则会让逐条二元判定失去意义。

---

## 🛠️ 技术栈

### 核心算法

- **RET** (Recursive Expansion Tree) - 递归展开
- **RIFT** (Rubric Inspection Framework) - 质量诊断
- **RubricHub** - 三阶段生成

### 模型架构

- **生成器**: GLM-4, DeepSeek（多模型协作）
- **判分器**: 异质模型（Phase 4）
- **诊断器**: 异质组合（按失效模式选型）

### 关键设计

- **满分归一化**: 所有题目 100 分（可设统一阈值）
- **血缘标签**: 准则可追溯到视角和场景
- **二元判定**: 每条准则"满足/不满足"（无模糊地带）

---

## 📝 参考论文

| 论文 | arXiv | 在本项目中的作用 |
|------|-------|------------------|
| Qworld | 2603.23522 | RET 递归展开算法 |
| RubricHub | 2601.08430 | 三阶段生成流程 |
| RIFT | 2604.01375 | 八失效模式诊断 |
| RaR | 2507.17746 | 题型判定理论 |
| QUBRIC | 2606.03968 | 准则措辞原则 |

---

## 🔧 开发相关

### 环境要求

- Python 3.8+
- API 配额（GLM-4, DeepSeek）
- 标准库 only（无第三方依赖）

### 运行成本

- **Phase 3**: ~¥500-800（已完成）
- **Phase 4**: ~¥1,500-2,000（待做）

### 配置

```bash
# 设置 API keys
export GLM_API_KEY="your-key"
export DEEPSEEK_API_KEY="your-key"

# 并发数（可选）
export RP_WORKERS=20
```

---

## 🤝 五条硬约束

违反即出错（详见设计文档）：

1. **锚定回复 ≠ 待评回复**（第 5 步）
2. **判分器 ≠ 生成器**（第 12 步）
3. **锚点集 ∉ 训练集**（第 14 步）
4. **血缘标签必须在第 4 步就挂**
5. **闸门项不进 S_max 分母**（第 9 步）

---

## 📧 联系

- 时间: 2026-08-12

---

**快速链接**:
- [给导师的材料](docs/advisor/) ⭐ 推荐从这里开始
- [生成的 Excel](outputs/excel/)
- [技术报告](docs/reports/)
- [设计文档](docs/design/)

---

**项目状态**: Phase 3 已完成，Phase 4 待启动  
**最后更新**: 2026-08-12（仓库整理）
