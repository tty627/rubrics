# Rubric 生成能力建设：完整方案（v2）

**阅读深度声明**：标 `[全文]` 的读了方法节正文；标 `[摘要]` 的只读摘要。
所有设计决策只依据 `[全文]` 的论文。

| 论文 | arXiv | 深度 | 在本方案中的角色 |
|---|---|---|---|
| Qworld | 2603.23522 | `[全文]` | 骨架：RET 递归展开算法 |
| RubricHub | 2601.08430 | `[全文]` | 嫁接：三阶段生成流程 |
| RIFT | 2604.01375 | `[全文]` | 诊断：八失效模式分类学 |
| RaR | 2507.17746 | `[全文]` | 题型判定的理论依据 |
| QUBRIC | 2606.03968 | `[全文]` | 准则措辞原则（constitutive） |
| SVR | 2606.08077 | `[全文]` | 病因诊断（不采用其方法） |
| AtomicDecomp | 2603.28005 | `[全文]` | 粒度约束的反面证据 |

---

## 0. v2 相对 v1 的两处修订

### 0.1 去掉「背景知识假设」

v1 的第 2 步抽三个上下文标签，其中「背景知识假设」（提问者已经知道什么）删掉。

**理由**：

它在 Qworld 原文的 Scenario Grounding 里确实存在——论文原话是推断
"the target audience, stakes, and assumed background knowledge"。但：

1. **论文没有单独验证它的贡献**。Qworld 的消融实验（§5.1）拆的是
   CoT / Tree Decomposition / 水平展开这三项，没有拆 Scenario Grounding 的三个子维度。
   所以「去掉它会掉多少」在论文里查不到。
2. **它的作用可被 Missing Criteria 诊断覆盖**。这个标签的用途是
   "决定哪些准则不该出现"，属于减法；而 RIFT 的 Missing Criteria 诊断做的是加法核验
   （题目隐含的可核验要求是否都有准则）。减法那一侧即使漏了，
   代价是多几条冗余准则，会被 Redundant Criteria 诊断兜住。
3. **推断可靠性低**。从一句 query 反推提问者的知识水平，本身是高方差判断，
   错误的推断比没有推断更糟——会误删该有的准则。

**保留的两个标签足够支撑 Scenarios 展开**：intent 决定评什么，隐性约束决定评的边界。

### 0.2 新增题型判定与路由（第 2.5 步）

这是 v1 的真实盲区。以下是完整推导。

---

## 1. 为什么必须做题型判定

### 1.1 三篇骨架论文的适用域都是开放域

| 论文 | 实验 benchmark | 域性质 |
|---|---|---|
| Qworld | HealthBench、Humanity's Last Exam | 医疗推理、开放问答 |
| RubricHub | Science / Instruction Following / Writing / Medical / Chat | 全部开放生成 |
| RIFT | AdvancedIF、ResearchRubrics、WildChecklists、OpenRubrics、AutoRubrics | 指令遵循、深度研究、创作 |

三篇都没有专门处理「有唯一正确答案」的任务。这不是疏漏，是**定位如此**——
下一节说明。

### 1.2 RaR 给出了形式化依据 `[全文]`

RaR（2507.17746）的 Remark 1 是本节的核心依据。论文原文：

> **Remark 1 (Rubrics as Rewards subsumes RLVR).** The RLVR setting is a special case
> of rubric-based rewards defined in Equation 1, where k = 1, w₁ = 1, and c₁(x, ŷ)
> reduces to a single verifiable correctness function that compares the model output ŷ
> against the known correct answer y.

形式化地：

```
r_RLVR(x, ŷ) = match(y, ŷ)        match ∈ {0, 1}
```

也就是说，**可验证任务在 rubric 框架下就是「k=1、w=1、单条准则」的退化情形**。

论文接着说 rubric 的价值在于：

> Rubric-based reward functions thus generalize RLVR by enabling multi-dimensional
> supervision, flexible weighting across criteria, and the incorporation of both objective
> and subjective aspects of response quality. [...] rubric-based rewards further enable
> structured supervision in settings where correctness is **multifaceted and may not be
> strictly verifiable**.

**这段直接推出了我们要做的事**：

- 若一道题的正确性是 **single & strictly verifiable**（数学答案、代码测试用例），
  它的 rubric 天然接近 k=1。硬拆成多视角是**给退化情形强加结构**。
- 若正确性是 **multifaceted**（医疗建议、方案设计），才需要 RET 那样的多轴展开。

所以题型判定不是我们发明的额外环节，是 **RaR 的 Remark 1 在生成侧的直接推论**。
论文自己没写这个分流，因为它的数据集（RaR-Medicine、RaR-Science）已经预先
筛成了开放域，用不着判。

### 1.3 QUBRIC 从反面印证 `[全文]`

QUBRIC（2606.03968）做的是把开放 query 改写成「有明确评价标的」的 query。
它的失败分析写得很清楚：

> **Why naive narrowing fails.** Without grounding in what the answer should contain,
> naive narrowing systematically introduces unverifiable references—citing guidelines,
> glossaries, or documents that do not exist—after which the rubric generator defaults to
> testing whether the model refuses to answer, yielding uninformative reward signals.

注意它的方向：**开放 → 收窄**，而且收窄后仍是开放域（scenario-based question），
不是变成数学题。它专门警告了"收窄过度会导致 rubric 生成器退化"。

这从反面支持我们的判定：**题型是 query 的固有属性，不该靠改写来强行统一**。
数学题就是 verifiable 的，把它当开放题展开，或把开放题硬收窄成填空，
两个方向都会让 rubric 退化。

### 1.4 AtomicDecomp 给出粒度上界 `[全文]`

AtomicDecomp（2603.28005）的结论：

> the holistic judge matches or exceeds the atomic judge on two of three benchmarks
> [...] The holistic advantage is concentrated in **partially_supported cases —
> incompleteness detection**.

即**在需要完整性判断的任务上，细粒度分解未必优于整体判断**——分解会碎片化整体
判断，使全局遗漏更难检测。

这说明「越细越好」是错的，存在一个任务相关的最优粒度。verifiable 题的最优粒度
就是粗的（答案对不对），这与 RaR 的 Remark 1 一致。

### 1.5 不分流的具体代价

以一道数学题为例。若走完整 RET：

```
Scenario：学生求解定积分
  ├─ 视角 1 事实正确性  → 「是否正确应用换元法」
  ├─ 视角 2 推理质量    → 「每步是否给出依据」
  ├─ 视角 3 数学严谨度  → 「是否检查了积分区间」
  ├─ 视角 4 表达清晰度  → 「是否用 LaTeX 规范书写」
  └─ 视角 5 实用性      → 「是否说明该方法的适用范围」
```

假设 R_w 再补两个视角，共 7 个视角、每个 1–2 条准则，约 10 条准则。
「最终答案是否正确」只占其中 1 条，权重就算给到最高，**占分比例也不到 20%**。

结果：答案算错但过程写得漂亮的回复能拿 70+ 分，而这在数学题上应该是不合格。
这正是 RIFT 的 **Hackable**（靠虚增代理指标拿高分）与 **Misaligned**
（imposes unnecessarily strict or narrow requirements not asked for by the prompt）。

反过来，若把开放题当 verifiable 处理，则退化成单准则「答案是否正确」——
而开放题没有唯一答案，这条准则本身就不可判定，会触发 **Ungrounded**。

**两个方向的错配都会被 RIFT 诊断出来，但那是事后。第 2.5 步是把它挡在事前。**

---

## 2. 三种 rubric_form 的设计

### 2.1 定义

| form | 题型 | 结构 | RET 用量 | 判分方式 |
|---|---|---|---|---|
| `gated_answer` | verifiable | 答案正确性 60–80% + major 闸门；推理过程 15–30%；表达 5–10% | 固定 3 视角，**不跑 R_w** | 答案项可程序化核验，其余走 LLM |
| `analytic` | open | 多维度加权，5–8 条准则 + 1–2 闸门 | **完整 RET**，R_w 两层全开 | 全部 LLM 判分 + 引证据 |
| `multi_part` | hybrid / 多子题 | 每子题一 block，块内归一，块间等权 | **分 block**：确定性 block 走 gated_answer，开放 block 走 analytic | 混合 |

### 2.2 `gated_answer` 为什么设成三个固定视角

不是拍的，对应 RaR 的 desiderata（§3.1）四条中的三条：

| RaR desiderata | 对应视角 |
|---|---|
| Grounded in Expert Guidance（捕捉正确性所需的关键事实、推理步骤、结论） | 答案正确性 |
| Comprehensive Coverage（factual accuracy, logical coherence, ...） | 推理过程完整性 |
| （style 一项，RaR 列在 Coverage 里但权重最低） | 表达清晰度 |
| Criterion Importance（factual correctness must outweigh secondary aspects） | → 决定了 60–80% 的占分 |

第四条 Self-Contained Evaluation 是所有 form 共有的要求，不单独成视角。

RaR 明确写了 **"factual correctness must outweigh secondary aspects such as stylistic
clarity"**，这是答案正确性占主导的直接依据。

### 2.3 `multi_part` 的必要性

来自种子集的实测：有一道 3122 字的填空题被拆成 **62 条并列准则**（满分 61，全集最高）。
这不是 rubric，是答案清单被塞进了 rubric 字段。

按 RIFT 的定义这是 **Non-Atomic**——"does not provide a parseable, consistently
scorable structure"。62 条并列时单空答错的影响在量表上不可控。

正确形态是分层归一：20 个空 → 20 个 block，各占 5%，块内再判。

### 2.4 判定的置信度处理

题型判定本身会错。三种处理：

- **高置信度** → 直接路由
- **低置信度** → 默认走 `analytic`（多视角比单准则安全：多余的视角会被
  Redundant 诊断删掉，而缺失的视角是 Missing，代价更大）
- **判定为 hybrid** → 走 `multi_part`，本质上是"两种都做"的保守选择

这个默认方向的选择依据是 RIFT 各失效模式的可修复性：Redundant Criteria 的处置是
合并（无损），Missing Criteria 的处置是退回重展开（要重跑）。所以宁可多不可少。

---

## 3. 完整流程

### 3.1 总图

```
   query
     │
     ▼
┌────────────────────────────────────────────────┐
│ 1  入口过滤                                    │
│    真人甄别 → 缺陷判定（直通/改写/弃用）      │
└───────────────────┬────────────────────────────┘
                    ▼
┌────────────────────────────────────────────────┐
│ 2  上下文标签抽取                              │
│    intent + 隐性约束     ← 已去掉背景知识假设  │
│    R⁰_h：Q → ℓ=1 Scenarios                     │
└───────────────────┬────────────────────────────┘
                    ▼
┌────────────────────────────────────────────────┐
│ 2.5 题型判定与路由        ← 新增，依据 RaR      │
│     verifiable / open / hybrid                 │
└──────┬──────────────┬──────────────┬───────────┘
       │              │              │
   gated_answer   analytic      multi_part
       │              │              │
       ▼              ▼              ▼
┌──────────────┐ ┌─────────────┐ ┌──────────────┐
│ 3a 固定3视角 │ │ 3b 完整 RET │ │ 3c 分 block  │
│    不跑 R_w  │ │  R_h + R_w  │ │  各走 a 或 b │
└──────┬───────┘ └──────┬──────┘ └──────┬───────┘
       └────────────────┼───────────────┘
                        ▼
┌────────────────────────────────────────────────┐
│ 4  视角实例化为可测准则                        │
│    二元陈述 + α_c + 血缘标签                   │
└───────────────────┬────────────────────────────┘
                    ▼
┌────────────────────────────────────────────────┐
│ 5  Response Grounding + meta-principles        │
│    ※ 锚定回复必须 ≠ 待评回复                  │
└───────────────────┬────────────────────────────┘
                    ▼
┌────────────────────────────────────────────────┐
│ 6  Multi-Model Aggregation → R_base            │
│    每个模型独立跑完 2–5                        │
└───────────────────┬────────────────────────────┘
                    ▼
┌────────────────────────────────────────────────┐
│ 7  Difficulty Evolution → R_add                │
│    R_final = R_base ∪ R_add                    │
│    ※ gated_answer 可跳过                       │
└───────────────────┬────────────────────────────┘
                    ▼
┌────────────────────────────────────────────────┐
│ 8  惩罚项（独立环节，instance-specific）       │
│    fatal 置 0 / major 截上限 / minor 不设闸门  │
└───────────────────┬────────────────────────────┘
                    ▼
┌────────────────────────────────────────────────┐
│ 9  归一  S = Σ s_c / S_max                     │
│    闸门项不进分母；三种 form 同量纲            │
└───────────────────┬────────────────────────────┘
                    ▼
┌────────────────────────────────────────────────┐
│ 10 回复池：多模型 × 多档质量                   │
└───────────────────┬────────────────────────────┘
                    ▼
┌────────────────────────────────────────────────┐
│ 11 RIFT 全量诊断（八模式）+ 处置 + 冻结        │
└───────────────────┬────────────────────────────┘
                    ▼
┌────────────────────────────────────────────────┐
│ 12 判分：逐条二元 + 引证据                     │
└───────────────────┬────────────────────────────┘
                    ▼
        ┌───────────┴───────────┐
        ▼                       ▼
┌───────────────┐      ┌─────────────────┐
│ 13 badcase    │      │ 14 回灌         │
│  按视角/form  │─────▶│  训练集 + 反馈  │
│  聚合         │      │  到 2.5 / 3     │
└───────────────┘      └─────────────────┘
```

### 3.2 各步的论文出处对照

| 步 | 出处 | 原文机制 | 我们的改动 |
|---|---|---|---|
| 1 | 无 | — | 自建。真实流特有问题（多数非真人提问） |
| 2 | Qworld Scenario Grounding | 推断 intent、隐含约束、背景知识假设 | **去掉背景知识假设** |
| 2.5 | RaR Remark 1 的推论 | 论文只给形式化，未给分流实现 | **自建分流** |
| 3a | RaR §3.1 desiderata | 四条 desiderata | 落为三个固定视角 |
| 3b | Qworld RET §3.2 | R_h 层次展开 + R_w 水平展开，三层树 | 原样采用 |
| 3c | 无直接出处 | — | 自建。应对种子集里的多子题 |
| 4 | Qworld §3.1 + QUBRIC §3.2 | 二元陈述 + α_c；constitutive 原则 | 加血缘标签 |
| 5 | RubricHub Stage 1 | response-grounded + principle-guided | **加"锚定回复 ≠ 待评回复"约束** |
| 6 | RubricHub Stage 2 | 多模型候选 → 聚合 prompt | 明确要求跑完 2–5 而非只重跑 4 |
| 7 | RubricHub Stage 3 | 取高分对抽增量准则 | 加"gated_answer 可跳过" |
| 8 | RubricHub Appendix D.2 + RaR Pitfall 标签 | 独立的惩罚项生成器模板 | 严重度分级改为闸门机制 |
| 9 | Qworld Eq.1 + RubricHub Eq.7 | F_norm(Σ s_c) / S_max | 明确闸门不进分母 |
| 10 | RIFT §4 的信号载体 | 多模型多回复 | 加三种弱档造法 |
| 11 | RIFT §3 + §4 | 八失效模式 + 自动诊断器 | **加处置动作**（论文只诊断不处置） |
| 12 | RULERS 证据锚定 `[摘要]` | 强制引用可审计证据 | 加"判分器 ≠ 生成器" |
| 13 | 无 | — | 自建。这是我们的目标产出 |
| 14 | RubricHub RuFT 思路 | 高分数据回灌 | 加"锚点集不进训练集" |

### 3.3 关键约束汇总（违反即出错的）

1. **锚定回复 ≠ 待评回复**（第 5 步）。真实流上日志回复就是待评对象，
   拿它锚定会让 rubric 从待评回复自身衍生，漏检必然发生。
2. **判分器 ≠ 生成器**（第 12 步）。自偏好偏差会让判分虚高。
3. **锚点集 ∉ 训练集**（第 14 步）。它是唯一的独立参照点。
4. **血缘标签必须在第 4 步就挂**。第 13、14 步依赖它，后补补不上。
5. **闸门项不进 S_max 分母**（第 9 步）。它作用于总分而非贡献分值。

---

## 4. 冷启动

```
第一步  锚点集（人在环）
        小规模人工审定 rubric，作 Coverage / Uniqueness 的参照
        分层抽样：三种 form 按实际占比，学科覆盖理/工/医/人文
        重点审 RIFT 自动诊断最弱的两类：
          - Misaligned or Rigid（PWA 80%，最低）
          - Hackable（自动诊断在单次跑时明显偏差）
        并额外审题型判定的正确性 —— 这是 v2 新增环节，没有历史数据
          │
第二步  蒸馏生成器
        RET 是多轮 LLM 调用（每层 w_ℓ 轮 × 三层），成本随真人 query 量线性放大
        用大模型跑 RET 产出数据，蒸馏到小模型作线上生成器
        RubricHub 与 CARMO `[摘要]` 都验证了可蒸馏
        ※ 题型判定可以用更小的模型，它是分类任务不是生成任务
          │
第三步  自举
        生成器跑真实流 → RIFT 诊断 → 通过者回灌 → 迭代
        边界：Reliability 三项可自举
              Content / Consequential 两类需同步扩锚点集
```

---

## 5. 待定项

**结构类**

| 项 | 参考值 | 来源 |
|---|---|---|
| R_w 展开轮数 w_1 / w_2 | 论文未给固定值 | Qworld 的消融只证明"有 R_w 优于无" |
| 准则条数 | RaR 用 7–20 条 | RaR §3.2 |
| gated_answer 答案项占分 | 暂定 60–80% | 由 RaR "factual correctness must outweigh" 推出，具体比例待测 |
| 惩罚项严重度上限值 | — | 自定 |
| 视角清单稳定条目 | Qworld 给 6 个示例 | 长尾学科靠 R_w 现场导出 |

**阈值类**

- RIFT 八项判定阈值。**论文的 F1 是在 GPT-5.2 / Gemini 3 Pro 上、
  50 条测试集上取的最优阈值**。换我们集群的模型后要重测，数字不可搬。
- badcase 分数阈值（归一后可全局设一个）
- LLMaJ-MV 的 N（论文用 5）
- 题型判定的置信度阈值

**规模类**

- 锚点集题数
- 回复池每档条数、三种弱档造法配比
- 第 6 步的异质模型个数（RubricHub 用 GPT-5.1 + Gemini 3 Pro 两个）
- 三种 form 的实际占比（决定产能分配）

---

## 6. 已知不确定项

1. **题型判定是自建环节，无论文验证过它的准确率**。RaR 的 Remark 1 只给了
   形式化依据，没给判定方法和错误率。这是 v2 最大的新增风险源，
   缓解办法是第 11 步按 form 分别看诊断结果（gated_answer 频繁 Low Signal
   = 判定过宽，analytic 频繁 Misaligned = 判定过窄），以及锚点集里额外审这一项。

2. **去掉背景知识假设的代价无法先验测量**。Qworld 的消融没拆到这一层。
   缓解办法是首轮跑完看 Redundant Criteria 的触发率——若显著偏高，
   说明确实需要这个减法环节。

3. **Qworld 的强项在 HealthBench 这类有明确专业准则的领域**。我们的种子集
   学科分布很散（两百多个学科词），RET 在长尾学科上能否同样导出有效视角，
   没有依据可推。首轮要看的第一件事。

4. **RIFT 的 F1 数字不可搬**。分类学可以用，自动诊断器的表现依赖模型档位。

5. **RET 是多轮调用，成本比单次生成高一个量级**。蒸馏是必要的，不是优化项。
   题型判定的一个附带收益是省成本：gated_answer 路径不跑 R_w，
   若数据里确定性题占比高，整体成本会显著低于全量跑 RET。

6. **我们的两条强模型回复可能不足以支撑 Difficulty Evolution**。
   RubricHub 用的是从候选池按共识高分挑的一对；我们是固定两个模型，多样性有限。

7. **读全文的是七篇，排除决定有基于摘要的**。若首轮在维度多样性上仍不达标，
   下一步该细看 Sanders et al. 的 error taxonomy 路线（2602.06795，`[摘要]`），
   那篇专做细粒度错误分类，对第 8 步的惩罚项生成可能更有用。

