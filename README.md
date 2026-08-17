# Rubric 自动生成流水线

把一道题自动转成一份可逐条二元判定的评分标准（rubric），用于评估模型回复质量、挖掘 bad case。

骨架取自三篇论文：Qworld 的 RET 递归展开（arXiv:2603.23522）、RubricHub 的三阶段生成（arXiv:2601.08430）、RIFT 的失效模式诊断（arXiv:2604.01375）。在此之上加了一层题型路由——原论文都假定任务是开放题，这是对它们的扩展。

纯标准库实现，无第三方依赖（Python 3.12.3 验证）。xlsx 直接用 zipfile + ElementTree 解析，LLM 调用走自建的 OpenAI 兼容客户端。

## 当前状态

lean 主线已在 452 条跨学科种子集上跑通全量。产出 `outputs/rubrics_advisor_lean.jsonl`：

| 指标 | 草稿（人工，对照） | 本流水线 |
|------|-----------------|---------|
| 题数 | 453 | 452 |
| 准则数/题 | 6.1（min 2, max 62） | 5.53（min 3, p50 5, max 8） |
| 维度去重数 | 1（全是「知识正确性」） | 12 种，每题 mean 3.30 |
| 满分 | p50 21 | 5~15（原始权重，不归一） |

准则数不是「越多越好」的指标。旧的逐视角展开路径能做到 30.5 条/题，但 RIFT 判非原子命中 80.8%——条数是靠把多个要求捆在一条里堆出来的，逐条二元判定会失效。现在的目标是粒度对齐 + 维度铺开。

**Phase 4 实测全量已跑通（2026-08-17）**：388 道双回复题、6 档回复池 2328 条回复、16517 条判分、3 轮处置闭环 + 终态选择。无缺陷率 66.2% → 73.2%，无一题退步。残留 LowSignal 42 / floor 13 / Hackable 13 / skip 32（测量受限）。

**检查点 2（放行闸门）已通过**：新 rubric 与草稿 rubric 在 351 个可测对上的 pairwise 判别率 93.2% vs 77.2%、反转率 2.3% vs 2.8%，判分侧证据链闭合。详见 `docs/reports/PHASE4_CHECKPOINT2.md`。

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
python3 stages/s02b_route.py
RP_RET=lean python3 stages/s03_perspective.py

# 4. 跑准则生成与诊断段（已有 s03 产出时从这里开始）
bash scripts/rerun_lean_fixed.sh                   # 清缓存加 RP_CLEAN=1

# 5. 审计产出质量
python3 scripts/audit_rubrics.py

# 6. 语义核心单测（零 LLM，改 lib/rubric.py 前必跑）
python3 tests/test_rubric.py
```

`rerun_lean_fixed.sh` 覆盖 s04_rubric → s11_diagnose → s11b_remedy → 导出这一段，前四步需单独跑。缓存全命中时整段是秒级。

只想看结果，不跑流水线：

```bash
head -1 outputs/rubrics_advisor_lean.jsonl | python3 -m json.tool
```

## 交付 schema

每行一题。准则级字段共五项，加下游判分要用的标记（`is_gate` 全带，`severity` / `is_veto` 只挂负项）：

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
     "is_positive": false, "is_gate": false,
     "severity": "major", "is_veto": false}
  ]
}
```

- `score` 是原始整数权重，不在流水线内归一（正向 1-3，verifiable 的答案项 6-8；负向 -2/-3）。`full_mark = sum(正向 score)`，跨题可比性靠判分阶段算得分率解决。
- `is_gate` 标出 gated_answer 题的答案项是哪一条。它仍计入 `full_mark` 分母，0/1 语义由判分侧处理。
- `severity`（`principle` / `major` / `minor`）与 `is_veto` 只挂负向准则。veto 是补偿式总分上的合取门，聚合规则显式声明在 `lib/rubric.VETO_RULE`：**任一 `is_veto` 项被判定成立 → 整题得分率为 0，不进补偿式求和**。veto 项本身不进 `full_mark` 分母，归零由判分侧执行（`s12_judge` 走两票制：第二个异源模型复判确认才生效）。
- `multi_part` 题额外带 `blocks`，保留子题结构。
- 血缘标签（`_criterion_id` / `_perspective_ids` / `_scenario_ids`）、RIFT 诊断结果、质量标记只进 `outputs/rubrics_internal.jsonl`（`--full`），不进交付档。

## 数据流

```
data/input.xlsx
  ↓ s00_seed          seed.jsonl 453 条 + baseline.json（草稿基线指标）
  ↓ s01_filter        真人 query 甄别 + 缺陷判定 → 直通 450 / 改写 3
  ↓ s02_context       intent + 隐性约束 + Scenarios（3.2/题）
  ↓ s02b_route       题型判定 → verifiable 121 / open 287 / hybrid 44
  ↓ s03_perspective   RET 视角展开（RP_RET=lean，3.2 视角/题）
  ↓ s04_rubric       准则直出，全题 6-8 条预算制 → 2452 条
  ↓ s11_diagnose     RIFT 四检测器 → 760/2452 defective (31.0%)
  ↓ s11b_remedy      分级处置：删 147 条，561 条落 _defect_queue.jsonl 待重写
  ↓ s04b_split       消费队列：拆非原子 + 事实纠错 + 标记重写 → 2500 条
  ↓ s04c_severity    负项分级（614 条）+ veto 标记（195 条）→ 452 题交付源
  ↓ export_advisor_schema.py
      outputs/rubrics_advisor_lean.jsonl   交付档
      outputs/rubrics_internal.jsonl       内部档（血缘 / 诊断 / 标记）
```

Phase 4 实测线（388 双回复题）：s10_pool → s12_judge → s11c_consequential → s11d_remedy ×3 轮闭环 → s11e_select → 合并 64 单回复题 → `data/s11e_all452.jsonl` ← 最终交付源。检查点 2：s12b_draft_judge → s12c_pairwise。一键：`bash scripts/rerun_phase4.sh` / `bash scripts/rerun_checkpoint2.sh`（完整数据流见 CLAUDE.md）。

每步独立读写 `data/` 下的 jsonl，可单独重跑不影响其他步。LLM 调用按 `model + prompt + params` 哈希缓存到 `cache/<stage>/`，改一个 prompt 只重算受影响的哈希。

导出源必须是流水线末端（跑过 Phase 4 是 `data/s11e_all452.jsonl`，没跑是 `data/s04c_severity.jsonl`）。`export_advisor_schema.py` 的 `--src` 默认值与 `scripts/rerun_lean_fixed.sh` 已对齐到这一步——指向中间步会静默丢掉后续产出（这个坑踩过两次：RIFT 诊断未生效、`severity`/`is_veto` 全空）。`scripts/audit_rubrics.py` 把「负项缺 severity」计入指标，换错源会在审计里亮出来。

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

`factual` 是第四个检测器。原本三个检测器没有一个管事实正确性，模型只能把「准则本身写错了」塞进 ungrounded 这个槽——318 条脱靶判定里至少 57 条（17.9%）是这种误分类。指纹是「答案准确性」维度的 ungrounded 命中率 29.5%，是全局均值的两倍多，而检查最终答案的准则最不可能超范畴。贴错标签会导致错误处置：脱靶的动作是删，事实错误该做的是重写。

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

数据侧的一个根因还没修：`s00_seed.py` 的 `ref_errors` 只存了 key 名，错误内容被丢弃，而 `s04_rubric` 却告诉模型「本题有 N 条错误可参考」——模型只能编。

种子集本身学科集中，前二占 83.2%（理学、工学与计算机），维度多样性的验证结论在别的分布上未必成立。

## 五条硬约束

违反即出错，详见 `docs/design/rubric_pipeline_full_v2.md` §3.3：

1. **锚定回复 ≠ 待评回复**（步骤 5）——同源会导致 rubric 从待评回复自身衍生
2. **判分器 ≠ 生成器**（步骤 12）——同系列模型有自偏好偏差，判分虚高
3. **锚点集 ∉ 训练集**（步骤 14）——参与训练就不再独立，失去参照作用
4. **血缘标签必须在步骤 4 挂**——后续按维度聚合失败原因、回灌视角全依赖它
5. ~~闸门项不进 S_max 分母（步骤 9）~~ ——已调整为：score 直接当权重，归一化延后到判分阶段。`legacy/full_path/s09_normalize.py` 随之作废

模型配置侧的强制要求（`lib/config.py` 校验）：步骤 6 多模型聚合需 ≥2 个 `generator` 且 `family` 不同；步骤 11 诊断需异质组合；步骤 12 `judge` 的 `family` 必须不同于 `generator`。

## 命名规则

stage 文件命名 `sNN[a-e]_语义词.py`：

- `NN` = **PLAN.md 14 步计划里的步骤位**（与 legacy 编号、数据文件前缀、报告里「步骤 12」的说法一一对应，故意保留编号而不是只留语义名——顺序和步骤位就是信息）；
- `a-e` = 同一步骤内的子步，按执行顺序编号（如 s11b → s11c → s11d → s11e）；
- 数据文件默认名跟随 stage 前缀且不带基数（`s12_judged.jsonl`），实际跑某个数据集时由 rerun 脚本传带基数的 env（`RP_S12L_OUT=s12_judged388.jsonl`）；
- 环境变量名（`RP_S04LC_SRC` 等）与 LLM 缓存目录名（`cache/s04L` 等）是历史接口，**刻意未改**——改了会丢全部缓存。

2026-08-17 归一化改名对照（旧 → 新）：

| 旧 | 新 | 旧 | 新 |
|---|---|---|---|
| s02_5_route | s02b_route | s11Lc_consequential | s11c_consequential |
| s04L_rubric | s04_rubric | s11Ld_remedy | s11d_remedy |
| s04Lb_split | s04b_split | s11Le_select | s11e_select |
| s04Lc_severity | s04c_severity | s12L_judge | s12_judge |
| s05L_ground | s05_ground | s12Lb_draft_judge | s12b_draft_judge |
| s10L_pool | s10_pool | s12Lc_pairwise | s12c_pairwise |
| s11L_diagnose | s11_diagnose | s11Lb_remedy | s11b_remedy |

历史文档（`docs/reports/`、`legacy/`）里的旧名不追改，按上表对照。

## 仓库布局

```
stages/      20 个 stage，命名 `sNN[a-e]_语义词.py`（见「命名规则」）
lib/         基础库 7 个：xlsx / llm / stage / config / dimensions / rubric / answer_check
tests/       语义核心纯逻辑单测（零 LLM）：test_rubric.py / test_s04_flags.py
pyproject.toml  项目元数据（纯标准库，零依赖）；Makefile  常用入口（make check / seed / phase4 / ...）
config/      模型端点配置（models.json 含 api_key，已 gitignore）
scripts/     辅助脚本：导出、审计、统计、xlsx 填充、一键重跑
docs/
  design/      流程定稿（简明版 / 完整版）+ 实施计划
  reports/     技术报告：审查、修复记录、phase 报告
legacy/      已归档，不参与交付，保留可运行状态（见 legacy/README.md）
  full_path/   旧全量线（被 s04_rubric 取代）
  phase3/      多模型聚合支线
  phase4/      s05_grounding / s05b_anchor，Phase 4 启动时取回
outputs/     交付档 + 内部档 + 填充后的 xlsx
data/        中间产物与种子（全 gitignore，随侧车 tar 走）
cache/ logs/  LLM 缓存、日志（均 gitignore）
```

`data/`、`outputs/`、`cache/`、`logs/` 全部不入库：仓库只传可运行源码，种子与中间产物随侧车 tar（`rubrics_data_outputs_20260817.tar.gz`）走，解压到仓库根即可。打包方案见 `docs/design/RESTRUCTURE_PROPOSAL.md` §4。

## 环境变量

| 变量 | 默认 | 作用 |
|------|------|------|
| `RP_XLSX` | `data/input.xlsx` | 输入 xlsx |
| `RP_OUT` | `data/` | 中间产物目录 |
| `RP_CACHE` | `cache/` | 缓存目录 |
| `RP_WORKERS` | 20 | 并发数 |
| `RP_RET` | `hybrid` | RET 策略：lean / batch / hybrid / faithful。**lean 主线必须显式设 `lean`** |
| `RP_RUBRIC_MIN` / `MAX` | 6 / 8 | s04_rubric 每题准则预算 |
| `RP_CLEAN` | 0 | 重跑脚本清缓存 |
| `RP_EVENTS` | `cache/_events.jsonl` | 调用事件流水，设空串关闭 |
| `RP_EV_CHARS` / `RP_EV_MAX` | 400 / 64 | 流水留字数 / 滚存阈值 MB |

各 stage 另有 `RP_<STAGE>_SRC` 用于换输入源，如 `RP_S04LB_SRC`。

## 待做

**步骤 13 badcase 聚合 + 步骤 14 回流**：把 Phase 4 残留缺陷（LowSignal 42 / floor 13 / Hackable 13、检查点 2 里 9 道新 rubric 劣于草稿的题）按维度聚合失败原因，回灌视角库。

**已知未修**：cut 档 20.9% 造法失效（LLM 删段不可靠，要改程序化删段）；64 道单回复题按硬约束 1 无法进 Phase 4，缺实测证据；`ref_errors` 内容丢弃（s00_seed 只存 key 名）。

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
- `CLAUDE.md` — 开发上下文（架构、约定、修复记录）
