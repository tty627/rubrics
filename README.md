# Rubric 自动生成流水线

把一道题自动转成一份可逐条二元判定的评分标准（rubric），用于评估模型回复质量、挖掘 bad case。

骨架取自三篇论文：Qworld 的 RET 递归展开（arXiv:2603.23522）、RubricHub 的三阶段生成（arXiv:2601.08430）、RIFT 的失效模式诊断（arXiv:2604.01375）。在此之上加了一层题型路由，这是对原论文的扩展——它们都假定任务是开放题。

纯标准库实现，无第三方依赖（Python 3.12.3 验证）。xlsx 直接用 zipfile + ElementTree 解析，LLM 调用走自建的 OpenAI 兼容客户端。

## 当前状态

lean 主线已在 452 条跨学科种子集上跑通全量（2026-08-13）。产出 `outputs/rubrics_advisor_lean.jsonl`：

| 指标 | 草稿（人工，对照） | 本流水线 |
|------|-----------------|---------|
| 题数 | 453 | 452 |
| 准则数/题 | 6.1（min 2, max 62） | 5.53（min 3, p50 5, max 8） |
| 维度去重数 | 1（全是「知识正确性」） | 12 种，每题 mean 3.30 |
| 满分 | p50 21 | 5~15（原始权重，不归一） |

准则数不是「越多越好」的指标。旧的逐视角展开路径能做到 30.5 条/题，但 RIFT 判非原子命中 80.8%——条数是靠把多个要求捆在一条里堆出来的，逐条二元判定会失效。现在的目标是粒度对齐 + 维度铺开。

Phase 4（回复池 + 判分验证）未开工，见下方「待做」。

## 快速开始

```bash
# 1. 配置模型端点
cp config/models.json.example config/models.json   # 填入实际 base_url / api_key

# 2. 检查数据与端点就绪
python3 scripts/check_before_run.py

# 3. 从头跑（首次；s03 必须显式指定 lean，默认是 hybrid）
python3 stages/s00_seed.py
python3 stages/s01_filter.py
python3 stages/s02_context.py
python3 stages/s02_5_route.py
RP_RET=lean python3 stages/s03_perspective.py

# 4. 跑准则生成与诊断段（已有 s03 产出时从这里开始）
bash scripts/rerun_lean_fixed.sh                   # 清缓存加 RP_CLEAN=1

# 5. 审计产出质量
python3 scripts/audit_rubrics.py
```

`rerun_lean_fixed.sh` 只覆盖 s04L → s11L → s11Lb → 导出这一段，前四步需单独跑。缓存全命中时整段是秒级。

只想看结果，不跑流水线：

```bash
head -1 outputs/rubrics_advisor_lean.jsonl | python3 -m json.tool
cat docs/advisor/generated_rubrics_samples.md      # 3 个完整案例
```

## 交付 schema

每行一题。准则级字段是导师给定的五项，加两个下游判分要用的标记：

```json
{
  "rid": "q0007",
  "question": "...",
  "subject": ["理学(自然科学)"],
  "question_type": "verifiable",
  "rubric_form": "gated_answer",
  "intent": "...",
  "full_mark": 10,
  "rubrics": [
    {"criteria": "最终答案含 2-溴吡啶与(2-甲氧基-6-甲基苯基)硼酸的 SMILES，且格式正确",
     "score": 7, "reason": "...", "dimension": "答案准确性",
     "is_positive": true, "is_gate": true},
    {"criteria": "选用的偶联前体类型不属于常规交叉偶联反应的标配组合",
     "score": -2, "reason": "...", "dimension": "知识正确性",
     "is_positive": false, "is_gate": false}
  ]
}
```

- `score` 是原始整数权重，不在流水线内归一（正向 1-3，verifiable 的答案项 6-8；负向 -2/-3）。`full_mark = sum(正向 score)`，跨题可比性靠判分阶段算得分率解决。
- `is_gate` 标出 gated_answer 题的答案项是哪一条。它仍计入 `full_mark` 分母，0/1 语义由判分侧处理。
- `multi_part` 题额外带 `blocks`（121 题），保留子题结构。
- 血缘标签（`_criterion_id` / `_perspective_ids` / `_scenario_ids`）、RIFT 诊断结果、质量标记只进 `outputs/rubrics_internal.jsonl`（`--full`），不进交付档。

## 数据流

```
data/input.xlsx
  ↓ s00_seed          seed.jsonl 453 条 + baseline.json（草稿基线指标）
  ↓ s01_filter        真人 query 甄别 + 缺陷判定 → 直通 450 / 改写 3
  ↓ s02_context       intent + 隐性约束 + Scenarios（3.2/题）
  ↓ s02_5_route       题型判定 → verifiable 121 / open 287 / hybrid 44
  ↓ s03_perspective   RET 视角展开（RP_RET=lean，3.2 视角/题）
  ↓ s04L_rubric       准则直出，全题 6-8 条预算制 → 2452 条
  ↓ s11L_diagnose     RIFT 四检测器 → 760/2452 defective (31.0%)
  ↓ s11Lb_remedy      分级处置：删 147 条，561 条落 _defect_queue.jsonl 待重写
  ↓ s04Lb_split       消费队列：拆非原子 + 事实纠错 + 标记重写 → 2500 条
  ↓ export_advisor_schema.py
      outputs/rubrics_advisor_lean.jsonl   交付档
      outputs/rubrics_internal.jsonl       内部档（血缘 / 诊断 / 标记）
```

每步独立读写 `data/` 下的 jsonl，可单独重跑不影响其他步。LLM 调用按 `model + prompt + params` 哈希缓存到 `cache/<stage>/`，改一个 prompt 只重算受影响的哈希。

`s02_5_route` 少一条（q0222）是该条调用失败被 `stage.run` 丢弃，不是过滤判弃用。重跑该步可补回。

注意：`scripts/rerun_lean_fixed.sh` 第 5 步当前仍从 `data/s11Lb_remedied.jsonl` 导出，而现有交付档是从 `data/s04Lb_split.jsonl` 导出的（含 s04Lb 的拆分与纠错）。脚本里那处 TODO 尚未接线，重跑前需手动指定 `--src`。

## 题型路由

判定 query 属于 verifiable / open / hybrid，路由到不同 rubric 形态：

| 题型 | rubric_form | RET 策略 | 为什么这样分 |
|------|-------------|---------|-------------|
| verifiable | `gated_answer` (112) | 固定 3 视角，不跑 R_w | 数学/代码题本质是 k=1 单准则，强行多维展开会稀释「答案对不对」这个主准则 |
| open | `analytic` (219) | 完整 RET（R_h + R_w） | 创作/建议/分析题需要多维覆盖 |
| hybrid | `multi_part` (121) | 分 block，每块独立判型 | 子题题型可能不同 |

刻意不把草稿 rubric 自带的 open/closed 标签喂给模型——那是另一套二分法，喂进去会把判定锚死在原有划分上。

## RIFT 诊断与处置

四个检测器逐条判准则，实测命中（2452 条，可多标）：

| 失效模式 | 命中 | 处置 |
|---------|------|------|
| non-atomic | 356 | 落队列拆成 2 条，分值守恒。不删——非原子的正确解法是拆 |
| ungrounded | 318 | 删。脱靶准则在评不相干的东西 |
| factual | 286 | 落队列拿真值重写。删了这道题就没有答案判据了 |
| subjective | 12 | 删。主观准则无法客观判定，留着只增加判分方差 |

优先级 `factual > drop > split`，**闸门项一律豁免**。删后不足 3 条则整题回滚并打 `needs_regen`（15 题）。

`factual` 是第四个检测器，2026-08-13 加的。原本三个检测器没有一个管事实正确性，模型只能把「准则本身写错了」塞进 ungrounded 这个槽——318 条脱靶判定里至少 57 条（17.9%）是这种误分类。指纹是「答案准确性」维度的 ungrounded 命中率 29.5%，是全局均值的两倍多，而检查最终答案的准则最不可能超范畴。贴错标签会导致错误处置：脱靶的动作是删，事实错误该做的是重写。

诊断器只凭自身知识判定的（`basis=model_knowledge`，5 条）另打 `needs_review`，下游只做保守改写——实测它会在同一题内自相矛盾。

## 已知问题

`scripts/audit_rubrics.py` 在当前交付档上的结果：

| 问题 | 规模 | 说明 |
|------|------|------|
| 疑似非原子 | 130 条 / 106 题 | 启发式扫描，以 RIFT 诊断为准 |
| 空泛词无锚点 | 14 条 / 12 题 | 判分器难以一致判定 |
| 负项写死具体错误答案 | 12 条 / 12 题 | 过拟合参考错误，答成别的就逃掉 |
| 全量复合悬崖 | 10 条 / 10 题 | 单条 ≥50% 满分且要求「全部/且」，实为 0/1 |
| 提及即得分 | 3 条 / 2 题 | 只查说了没说，不查说得对不对 |
| 闸门丢失 | 2 题（q0251 / q0358） | 答案项被诊断删除，`is_gate` 110 条 / gated_answer 112 题 |
| 拆分未完成 | 33 条 | 超题目条数预算，保留原样并打 `_split_skipped` |
| intent 含臆测措辞 | 156 题 (34.5%) | 上下文标签替用户想了没说的需求 |

数据侧的一个根因还没修：`s00_seed.py` 的 `ref_errors` 只存了 key 名，错误内容被丢弃，而 `s04L` 却告诉模型「本题有 N 条错误可参考」——模型只能编。

种子集本身学科集中，前二占 83.2%（理学、工学与计算机），维度多样性的验证结论在别的分布上未必成立。

## 五条硬约束

违反即出错，详见 `docs/design/rubric_pipeline_full_v2.md` §3.3：

1. **锚定回复 ≠ 待评回复**（步骤 5）——同源会导致 rubric 从待评回复自身衍生
2. **判分器 ≠ 生成器**（步骤 12）——同系列模型有自偏好偏差，判分虚高
3. **锚点集 ∉ 训练集**（步骤 14）——参与训练就不再独立，失去参照作用
4. **血缘标签必须在步骤 4 挂**——后续按维度聚合失败原因、回灌视角全依赖它
5. ~~闸门项不进 S_max 分母（步骤 9）~~ ——导师 2026-08-13 推翻：score 直接当权重，归一化延后到判分阶段。`legacy/full_path/s09_normalize.py` 随之作废

模型配置侧的强制要求（`lib/config.py` 校验）：步骤 6 多模型聚合需 ≥2 个 `generator` 且 `family` 不同；步骤 11 诊断需异质组合；步骤 12 `judge` 的 `family` 必须不同于 `generator`。

## 仓库布局

```
stages/      lean 主线 10 个脚本
lib/         基础库 5 个：xlsx / llm / stage / config / dimensions
config/      模型端点配置（models.json 含 api_key，已 gitignore）
scripts/     辅助脚本：导出、审计、统计、xlsx 填充、一键重跑
tools/       watch.py 监视面板
docs/
  design/      流程定稿（简明版 / 完整版）+ 实施计划
  advisor/     给导师看的展示材料
  reports/     技术报告：审查、修复记录、phase 报告
legacy/      已归档，不参与交付，保留可运行状态（见 legacy/README.md）
  full_path/   旧全量线（被 s04L 取代）
  phase3/      多模型聚合支线
  phase4/      s05_grounding / s05b_anchor，Phase 4 启动时取回
outputs/     交付档 + 内部档 + 填充后的 xlsx
data/ cache/ logs/    中间产物、LLM 缓存、日志（均 gitignore）
```

`data/`、`outputs/`、`cache/` 不入库，克隆后需自备 `data/input.xlsx` 并从 s00 跑起。

## 监视面板

```bash
python3 tools/watch.py            # 全屏刷新，走终端备用屏，Ctrl-C 恢复
python3 tools/watch.py --once     # 快照，便于贴日志
python3 tools/watch.py --tokens   # 展开 token 明细
```

只读，不影响在跑的进程。显示进程详情、在飞请求的完整 prompt/output、产出文件、端点负载与成功率。自动发现 `cache/` 下的新步骤，无需改面板代码。详见 `tools/README_watch.md`。

端点侧 token 有多副本坑：某些 URL 后面挂多个副本，`/metrics` 随机命中一个，累计计数器会跳动。面板靠「计数器只增不减」识别副本并求和。

## 环境变量

| 变量 | 默认 | 作用 |
|------|------|------|
| `RP_XLSX` | `data/input.xlsx` | 输入 xlsx |
| `RP_OUT` | `data/` | 中间产物目录 |
| `RP_CACHE` | `cache/` | 缓存目录 |
| `RP_WORKERS` | 20 | 并发数 |
| `RP_RET` | `hybrid` | RET 策略：lean / batch / hybrid / faithful。**lean 主线必须显式设 `lean`** |
| `RP_RUBRIC_MIN` / `MAX` | 6 / 8 | s04L 每题准则预算 |
| `RP_CLEAN` | 0 | 重跑脚本清缓存 |
| `RP_EVENTS` | `cache/_events.jsonl` | 调用事件流水，设空串关闭 |
| `RP_EV_CHARS` / `RP_EV_MAX` | 400 / 64 | 流水留字数 / 滚存阈值 MB |

各 stage 另有 `RP_<STAGE>_SRC` 用于换输入源，如 `RP_S04LB_SRC`。

## 待做

**Phase 4 判分验证**（约 +10k-15k 次调用）：把 `legacy/phase4/` 的 `s05_grounding`、`s05b_anchor` 移回 `stages/`，接步骤 10（回复池）、12（判分）、13（按维度聚合失败原因）、14（回灌）。这是验证 rubric 有效性的必要环节——目前所有指标都是结构指标，还没有「用它判分是否与人工标注一致」的证据。

**修完剩余缺陷**：`_split_skipped` 33 条、闸门丢失 2 题、`ref_errors` 内容丢弃、`rerun_lean_fixed.sh` 接 s04Lb。

**清理陈旧文档**：`docs/RUBRICS_SCORE_BREAKDOWN.md`、`docs/RUBRICS_PURPOSE_AND_USAGE.md`、`docs/advisor/README_FOR_ADVISOR.md` 仍按「满分统一 100、30.5 条/题」写，那是旧全量线的口径，已不成立。

## 参考论文

| 论文 | arXiv | 在本项目中的作用 |
|------|-------|----------------|
| Qworld | 2603.23522 | RET 递归展开（R_h 层次 + R_w 水平） |
| RubricHub | 2601.08430 | 三阶段生成流程 |
| RIFT | 2604.01375 | 失效模式诊断 |
| RaR | 2507.17746 | 题型判定理论 |
| QUBRIC | 2606.03968 | 准则措辞原则 |

## 文档

- `docs/design/rubric_pipeline_feishu_v2.md` — 流程定稿（14 步 + 题型判定），简明版
- `docs/design/rubric_pipeline_full_v2.md` — 完整版，含论文依据与逐步出处对照
- `docs/design/PLAN.md` — 分阶段实施计划，含成本估算与检查点
- `docs/advisor/` — 展示材料与案例
- `CLAUDE.md` — 开发上下文（架构、约定、修复记录）
