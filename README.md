# rubrics

Rubric 生成能力建设。

## 文档

| 文件 | 用途 |
|---|---|
| [docs/rubric_pipeline_feishu_v2.md](docs/rubric_pipeline_feishu_v2.md) | 流程定稿（14 步 + 第 2.5 步题型判定），飞书同步用 |
| [docs/rubric_pipeline_full_v2.md](docs/rubric_pipeline_full_v2.md) | 完整版：论文依据、推导过程、逐步出处对照 |
| [docs/PLAN.md](docs/PLAN.md) | 在种子集上跑通全流程的分阶段实施计划 |

## 骨架来源

| 论文 | arXiv | 角色 |
|---|---|---|
| Qworld | 2603.23522 | 骨架：RET 递归展开（R_h 层次展开 + R_w 水平展开） |
| RubricHub | 2601.08430 | 嫁接：三阶段生成（response grounding / 多模型聚合 / difficulty evolution） |
| RIFT | 2604.01375 | 诊断：八失效模式分类学 |
| RaR | 2507.17746 | 题型判定的形式化依据（Remark 1：RLVR 是 k=1 的退化情形） |
| QUBRIC | 2606.03968 | 准则措辞原则（constitutive 而非 presupposing） |

## 五条硬约束

违反即出错，详见完整版 §3.3：

1. 锚定回复 ≠ 待评回复（第 5 步）
2. 判分器 ≠ 生成器（第 12 步）
3. 锚点集 ∉ 训练集（第 14 步）
4. 血缘标签必须在第 4 步就挂
5. 闸门项不进 S_max 分母（第 9 步）
