# rubrics

Rubric 生成能力建设：流程设计 + 实现代码。

## 目录结构

```
rubrics/
├── docs/                              # 设计文档
│   ├── rubric_pipeline_feishu_v2.md   # 流程定稿（14 步 + 第 2.5 步题型判定）
│   ├── rubric_pipeline_full_v2.md     # 完整版：论文依据与逐步出处对照
│   ├── PLAN.md                        # 种子集上跑通全流程的分阶段计划
│   └── README_docs.md                 # 文档索引（旧）
├── lib/                               # 基础库（纯 stdlib）
│   ├── xlsx.py                        # xlsx 读写
│   └── llm.py                         # OpenAI 兼容客户端 + 磁盘缓存
├── stages/                            # 流水线各步
│   └── s00_seed.py                    # Phase 0: xlsx → seed.jsonl + baseline
├── config/                            # 配置
│   └── models.json.example            # 模型端点配置示例
├── data/                              # 数据目录（.gitignore 已排，只入库 baseline.json）
│   └── baseline.json                  # 草稿 rubric 基线指标
└── README.md                          # 本文件
```

## 快速开始

把你的 xlsx 放到 `data/input.xlsx`（或设 `RP_XLSX` 环境变量），跑 Phase 0：

```bash
python3 stages/s00_seed.py
```

产出：`data/seed.jsonl` + `data/baseline.json`（草稿 rubric 基线指标）。

Phase 1-4 需要模型端点，配置见 `config/models.json.example`。详细运行说明见 `lib/`、`stages/` 目录下各文件的注释。

## 骨架来源

| 论文 | arXiv | 角色 |
|---|---|---|
| Qworld | 2603.23522 | 骨架：RET 递归展开（R_h 层次展开 + R_w 水平展开） |
| RubricHub | 2601.08430 | 嫁接：三阶段生成（response grounding / 多模型聚合 / difficulty evolution） |
| RIFT | 2604.01375 | 诊断：八失效模式分类学 |
| RaR | 2507.17746 | 题型判定的形式化依据（Remark 1：RLVR 是 k=1 的退化情形） |
| QUBRIC | 2606.03968 | 准则措辞原则（constitutive 而非 presupposing） |

## 五条硬约束

违反即出错，详见 [docs/rubric_pipeline_full_v2.md](docs/rubric_pipeline_full_v2.md) §3.3：

1. **锚定回复 ≠ 待评回复**（第 5 步）
2. **判分器 ≠ 生成器**（第 12 步）
3. **锚点集 ∉ 训练集**（第 14 步）
4. **血缘标签必须在第 4 步就挂**
5. **闸门项不进 S_max 分母**（第 9 步）

## 依赖

纯 Python 标准库，无第三方包（已在 Python 3.12.3 上测试）。

## 设计文档

- [流程定稿（飞书版）](docs/rubric_pipeline_feishu_v2.md) — 14 步 + 第 2.5 步题型判定
- [完整版](docs/rubric_pipeline_full_v2.md) — 论文依据、推导过程、逐步出处对照
- [实施计划](docs/PLAN.md) — 在种子集上跑通全流程的分阶段计划
