# CLAUDE.md

本文件为 Claude Code 在本仓库工作时的指引。

## 项目概述

Rubric 生成能力建设：从题目自动生成评估标准（rubric），再用它评估模型回答。
骨架取自 Qworld RET 递归展开（arXiv:2603.23522），嫁接 RubricHub 三阶段生成
（arXiv:2601.08430），诊断采用 RIFT 八失效模式（arXiv:2604.01375）。

## 唯一主线：零起点生成

**rubric 只从题目本身和题面明确约束生成。** 没有既定 rubric、草稿 rubric、
参考 rubric 作为输入 —— 这不是降级模式，是唯一模式。代码里不存在
「有参考 / 无参考」的开关，也不要再引入。

可核验题（数学 / 代码 / 选择）的答案没有外部来源，由 `grounder` 角色现场解出
并经异源交叉复核，见下方「模型角色」。

## 仓库布局

```
pipeline/    唯一入口，编号阶段自包含（01-05 题目 / 06-10 rubric / 20-27 实测与交付 / 30-31 发布）
lib/         基础库，纯标准库：xlsx / llm / config / stage / dimensions / rubric / answer_check / task_input
tests/       零 LLM 纯逻辑单测，make check 全绿才允许推送
config/      models.json（含 api_key，已 gitignore，连 models.json.* 一起忽略防 .bak 泄露）
docs/design/ 流程定稿与实施计划
data/        阶段产物，按 tasks/ rubric/ evaluation/ release/ 分目录
outputs/     交付档、内部档、xlsx、run_manifest
cache/       LLM 缓存，按阶段名分目录
```

`stages/`、`legacy/`、`scripts/` 已删除，不要再引用。跨阶段复用口径走
`lib.stage.load('22_score_response_pool.py')` —— 编号文件名不是合法标识符，
`import` 用不了。

## 数据流

```
data/input.xlsx（+ RP_SOURCE_JSONL 提供原始会话，保留 system/user 约束）
  → 01 build_task_dataset      → tasks/01_task_dataset.jsonl
  → 02 filter_tasks            → tasks/02_filtered_tasks.jsonl
  → 03 extract_task_context    → tasks/03_task_context.jsonl      (intent + 隐含约束)
  → 04 classify_task_type      → tasks/04_task_types.jsonl        (question_type + rubric_form)
  → 05 generate_evaluation_axes→ tasks/05_evaluation_axes.jsonl   (RET 视角)
  → 06 generate_rubric         → rubric/06_rubric_draft.jsonl     (含血缘 + 质量标记)
  → 07 diagnose_rubric         → rubric/07_rubric_diagnosed.jsonl (RIFT)
  → 08 apply_rubric_diagnosis  → rubric/08_rubric_revised.jsonl
  → 09 rewrite_rubric_criteria → rubric/09_rubric_criteria_rewritten.jsonl
  → 10 classify_negative_criteria → rubric/10_negative_criteria_classified.jsonl
     ── rubric 在此冻结，候选回答才允许进入 ──
  → 20 resolve_canonical_answers → evaluation/20_evaluation_tasks.jsonl
  → 21 build_response_pool     → evaluation/21_response_pool.jsonl        (六档 × 全部题目)
  → 22 score_response_pool     → evaluation/22_response_scores.jsonl      (含 veto 两票)
  → 23 diagnose_rubric_discrimination → evaluation/23_discrimination_diagnostics.jsonl
  → 24 revise ⇄ 22 重判 ⇄ 23 复诊  ×3 轮固定闭环
  → 25 select_rubric_revision  → evaluation/25_selected_rubrics.jsonl
  → 26 build_evaluation_delivery_source → evaluation/26_rubric_delivery_source.jsonl
  → 27 export_rubric_delivery  → outputs/current/rubric_delivery.jsonl + rubric_internal.jsonl
  → 30 export_rubric_xlsx      → outputs/current/rubric_delivery.xlsx
  → 31 audit_rubric_delivery   → release/31_delivery_audit.txt（文本报告，由 runner 重定向）
```

一键：`bash pipeline/00_run_all.sh`。分段：`01_run_task_preparation.sh` /
`02_run_rubric_generation.sh` / `03_run_response_evaluation.sh` /
`04_run_release_verification.sh`。Makefile 同名入口。

**导出源必须是流水线末端。** 指向中间步会静默丢掉后续产出 —— 已经踩过两次
（RIFT 诊断未生效、severity/veto 全空）。审计把「负项缺 severity」计入指标，
换错源审计里会亮。

## 关键约束

违反即出错。前五条详见 `docs/design/rubric_pipeline_full_v2.md` §3.3。

1. **数据边界（最重要）**：阶段 01-10 不得读取 `ref_responses`、`draft_rubric`、
   `ref_errors`，也不得使用从候选回答抽出的 canonical answer。候选回答是待评对象，
   拿它当生成输入等于用被测的东西当测量基准。阶段 04 的放行闸门会拿每条
   `ref_responses` 的 60 字探针去比对交付档 rubric 文本，泄漏就报错。
2. **判分器 ≠ 生成器**（阶段 22）：同系列模型有自偏好偏差，判分虚高。
3. **grounder 必须是闭源最强模型**：`lib/config.inspect()` 硬失败。原因见「模型角色」。
   闭源凭据失效时可用 `RP_ALLOW_OPEN_GROUNDER=1` 临时放行，但 canonical 答案正确性
   不保证 —— 这是临时开关，凭据恢复后应删掉放行、回到闭源。
4. **血缘标签必须在阶段 06 挂**：`_criterion_id / _perspective_ids / _scenario_ids`，
   后续诊断依赖它。只进 `rubric_internal.jsonl`，不进交付档。
5. **score 直接当权重，不在流水线内归一**（导师 2026-08-13 定）：
   `full_mark = sum(正向 score)` 保持原始整数，闸门项计入分母，归一化延后到判分阶段。
   `is_positive` 是方向，`is_gate` 是 0/1 阀门标记，内部与导出口径一致。
   语义的唯一实现在 `lib/rubric.py`，业务代码不许内联公式。
6. **veto 是负项专属**（阶段 10 → 22）：`is_veto` 只能标在负向准则上，
   与 `is_gate`（正向答案阀门）方向相反。规则显式声明在 `lib/rubric.VETO_RULE`：
   任一 `is_veto` 成立 → 整题得分率 0，不进补偿式求和；veto 项本身不进分母。
   **诊断侧一律用 `raw_rate`**（不含 veto 的补偿式得分率）—— veto 归零是聚合规则
   不是 rubric 质量信号，混进 gap/std/floor 会让强档一 veto 就成片假阳性。
7. **判定线词表只有一份**：`lib/rubric.ANCHOR` / `SUBJ_DEG` 由阶段 06 的质量标记、
   阶段 10 的 veto 门槛、阶段 31 的审计共用。各写一套必然漂移。

## 模型角色

`config/models.json` 按角色绑定端点。**缺角色直接报错，不静默退回**——
退回会让 mid 档与 strong 档同模型，档序失效且不可见（388 全量实测踩过：
65% 的题 mid ≤ weak）。

| 角色 | 用途 | 硬约束 |
|---|---|---|
| grounder | 可核验题权威答案源（阶段 20、24 的锚） | family ∈ {anthropic, openai, google} |
| generator | rubric 生成与重写 | ≥2 个且 family 互异 |
| diagnoser | RIFT 诊断 | family 异质（Gemini 系强于 Ungrounded/Subjective，GPT 系强于 Missing/Low Signal）|
| judge | 判分 | family ≠ generator |
| veto | veto 复核第二票 | family 异于生成器与判分器 |
| pool_mid / pool_weak | 回复池中档 / 弱档 | 与 strong 档不同模型 |

**共识准入**：权威答案 + 2 个异源家族交叉复核，`answer_canonical` 归一化后
**三方全一致**才准入。分歧的题写进 `data/_answer_dispute.jsonl`，canonical 置空、
不参与判分。要求全一致而非多数票：两个模型共享盲区时多数票会把错答案锁死，
而阶段 22 的程序化核验无条件相信 `answer_canonical`。

## 回复池六档

档位**全部现场生成**（没有「单回复题」这个概念，旧线按 `ref_responses` 条数
把数据集切成 388 + 64 的做法已删除）：

| 档 | 造法 | 作用 |
|---|---|---|
| strong | 最强模型生成 | 上界参照，可核验题必须答对 |
| mid | 中等模型生成 | 分数应落在中间 |
| trunc | strong 截断 40% | 弱在「没说完」 |
| cut | strong 删一个关键论点 | 弱在「漏了要点」 |
| weak | 最弱模型生成 | 弱在「质量低」 |
| adv | 对抗档 | verifiable 造「答案错过程全」，open 造「面面俱到但都很浅」 |

**gated_answer（可核验）题只造 strong/mid/weak/adv 四档。** trunc/cut 是结构性
弱档，预设回答是多论点、可截断的论证；可核验题的强档可能只有一句结论（如
「你好世界」），删=空、截=断词，造出的不是「漏了要点」而是「内容缺失/结构破损」，
`SYS_CUT` 甚至直接返回空正文触发 `pool_errors`。gated 真正的弱档是 weak + adv
（答案错过程全），判据是闸门而非长度/结构。放行闸门与阶段 21 同口径：gated 只
校验四档齐全。

**open 题弱档三种造法必须并存**：同一准则在三种造法下结论不一致，说明它测的是长度或
结构而非内容，本身就是 Hackable 信号。只用一种造法，结论会随造法漂移。

两趟执行：strong/mid/weak/adv 先跑，定稿 strong 后再造 trunc/cut ——
它们是 strong 的字面派生档，必须基于最终 strong 才不会错位。

## 实测中的四个测量工具缺陷（放量才暴露，别改回去）

1. **mid 档序失效**：`SYS_MID` 只写「篇幅适中」时，模型把「不做深入展开」读成
   「答简短」，中位 244 字 < weak 的 395 字，65% 的题 mid ≤ weak。故改为绝对字数
   下限 600 + 「每个要点都要给依据」，把「中等」锚在覆盖深度而非篇幅。
2. **strong 答错**：篇幅检查查不出「答得长但答错」。22 道地板题里 5 道 strong
   程序化核验就是错的，放松准则治不了。故 strong 生成后做答案核验，答错用权威
   答案重生成一次，仍错标 `strong_wrong_answer`，诊断侧跳过该题。
3. **锚可达性门**（最关键）：地板信号有两种成因 —— rubric 真过严，或回复池造得不行。
   用 grounder 现场作答当锚去打分，**锚能拿到分就说明准则可满足**，地板来自 pool 侧。
   388 全量实测：8 道复测后仍地板的题里 6 道锚拿到 42%~100%。没这道门，那 6 道
   好用的 rubric 会被「放松」改坏。探针必须复用阶段 22 的 build —— 换口径不可比。
4. **答偏题门**：strong 自行改写题目前提再作答时，参照系偏了，rubric 不动，只标记。

## 处置不收敛是 2-循环

q0221 走出 60%→0%→60%→0%，q0028/q0071 同型。「收紧」与「放松」是互逆操作，
对这类题不存在两头都满足的中间档，多跑几轮只是在两个坏状态间来回。故阶段 24
跑**固定 3 轮**，再由阶段 25 在各轮实测证据里挑每题最好的一版（缺陷数少者优，
同分取靠后轮次），残留照实记进 `_s11Le`。选择逻辑单调，实测无一题退步。

## 放行闸门

`pipeline/04_run_release_verification.sh` 四步：结构完整性（open 六档 / gated 四档
缺档即失败）、数据边界泄漏断言、xlsx 导出、审计 + 单测。

**没有「新 rubric vs 草稿 rubric」的 pairwise 闸门** —— 本线不存在草稿 rubric，
对比无对象，相关阶段已删除。

## 开发约定

- 纯标准库，无第三方依赖（Python 3.12.3 验证）
- 中文注释；仅核心概念用英文（`draft_rubric`、`ref_responses` 等）
- 每个阶段脚本顶部注释说明它在流程中的位置
- 错误处理：解析失败计数但不中断，最后统一报告；LLM 失败写
  `data/_stage_errors.jsonl` 和产物的错误字段
- 注释里写「为什么」而不是「做了什么」，尤其是实测踩出来的护栏 —— 每条护栏都
  对应过一批假信号，删注释等于让下一个人再踩一遍
- 改动后跑 `make check`，全绿才推送（当前 33 个测试，其中 1 个 skip）

## 三方同步

代码在三处：本地 `/home/tantianye/rubrics`、GitLab `gitlab.pjlab.org.cn/tantianye/rubrics`、
开发机 `h.pjlab.org.cn:/mnt/shared-storage-user/tantianye/rubrics`（属主 `root`，
直接用 `git`，`runuser -u tantianye` 反而触发 dubious ownership）。

**GitLab 是唯一中枢，方向单一：本地改 → `make check` 全绿 → 推 GitLab →
开发机 `git fetch && git reset --hard origin/main`。** 开发机只跑流水线、不产生代码，
永不作为源 —— 曾经两边同时改且方向相反（本地在删 `stages/`，开发机在给它加开关），
开发机那批改动最后整批作废。未全绿不推，否则把坏代码一次扩散到三方。

`config/models.json` 是 gitignore 的，**同步代码不同步凭据**。所以开发机的
`make check` 会在 `test_configured_models_satisfy_role_hard_constraints` 上失败：
那份配置只有内网自建端点，`grounder` 的闭源硬约束（关键约束第 3 条）拦住了它。
这个失败是设计如此，不是回归 —— 判断开发机是否同步成功看 `git rev-parse HEAD`
和 `git status --porcelain`，不看这条测试。

`reset --hard` 不动未跟踪文件：删掉的目录若只剩 `__pycache__/*.pyc` 会留在开发机上，
残留 `.pyc` 会 shadow 已删模块，删目录时要一并 `rm -rf`。

## 环境变量

- `RP_XLSX` 输入 xlsx（默认 `data/input.xlsx`）
- `RP_SOURCE_JSONL` 原始会话 JSONL，保留 system/developer/user 约束
- `RP_DATA_ROOT` 产物根目录 / `RP_OUTPUT_ROOT` 交付根目录（`lib/paths.py` 读的是
  `RP_OUTPUT_ROOT`，不是 `RP_OUT` —— 后者只在 `lib/stage.py` 的注释里作历史称呼出现）
- `RP_RUN_ID` 本次运行标识（写进 manifest 与 `outputs/runs/<run_id>/`）。
  当前只有从环境显式传入才生效，见下方「已知缺陷」第 1 条
- `RP_CACHE` 缓存目录（runner 默认 `cache/numbered`）。没有「清缓存」开关：
  缓存按阶段名分子目录，重跑某阶段直接删对应目录
- `RP_WORKERS` 并发数
- `RP_M_*` 显式指定某角色/阶段用哪个模型（覆盖角色选择）
- `RP_ANSWER_CROSS_CHECKS` 交叉复核数（默认 2）
- `RP_ROUNDS` 处置闭环轮数（默认 3，见「处置不收敛是 2-循环」）
- `RP_SEED_ONLY` 跳过阶段 01 的构建，直接用已有 seed jsonl
- `RP_ALLOW_OPEN_GROUNDER` 临时放行：闭源凭据失效时用开源 grounder 占位跑通机制。
  默认关；打开后 `canonical` 答案正确性不保证（见关键约束第 3 条）
- `RP_EVENTS` 调用流水路径 / `RP_EV_CHARS` 留字数 / `RP_EV_MAX` 滚存 MB

## 已知缺陷（2026-08-31 审阅，未修）

按影响排序。前两条是真 bug，后三条是命名漂移，都不影响 full-237 已出的产物。

1. **`RP_RUN_ID` 不会传给子进程**（`pipeline/00_run_all.sh:5-6`）：
   `RUN_ID=${RP_RUN_ID:-$(date ...)}` 把回退值赋给了 `RUN_ID`，紧接着的
   `export RP_RUN_ID` 导出的却是**原变量**。未显式设 `RP_RUN_ID` 时它仍是空，
   于是 `write_run_manifest.py:37` 走自己的 `datetime.now()`，与 shell 的
   `$RUN_ID` 是两个时间戳 —— manifest 里的 `run_id` 和 `outputs/runs/<dir>/`
   目录名不一致，且脚本末尾的 `cp` 与 python 各写一个 run 目录。
   full-237 没暴露是因为 `start_full237.sh` 显式 `export RP_RUN_ID=full-237`。
   修法：`export RP_RUN_ID="$RUN_ID"`。
2. **manifest 里 audit 路径是死的**（`pipeline/write_run_manifest.py:59`）：
   写的是 `data/release/33_delivery_audit.txt`，实际产物是
   `31_delivery_audit.txt`（`04_run_release_verification.sh:104`、
   `lib/paths.RELEASE_FILES['audit']` 都是 31）。编号从 33 改 31 时漏了这处。
3. `lib/paths.RUBRIC_FILES['initial']` 指向 `06_initial_rubric.jsonl`，
   但阶段 06 实际产出 `06_rubric_draft.jsonl`。该键全仓库无人引用，是死条目。
4. `pipeline/03_run_response_evaluation.sh:37` 的 `desc='s10L 趟2(cut)'` 现在还
   跑 adv/weak 复核，描述只提 cut。
5. `01_run_task_preparation.sh:16-22` 有若干 `cp A B; cp B A` 的自反拷贝
   （如 18/19 行、21/22 行），是编号迁移期的兼容垫片，读起来像 bug。

## 文档

- `docs/design/rubric_pipeline_feishu_v2.md` 流程定稿简明版
- `docs/design/rubric_pipeline_full_v2.md` 完整版，含论文依据与逐步出处对照
- `docs/design/PLAN.md` 分阶段实施计划，含成本估算与检查点
- `docs/design/NUMBERED_PIPELINE.md` 编号流水线说明
