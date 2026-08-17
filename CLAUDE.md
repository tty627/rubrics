# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rubric 生成能力建设：基于学术论文实现的评估标准(rubric)自动生成流水线。骨架取自 Qworld RET 递归展开(arXiv:2603.23522)，嫁接 RubricHub 三阶段生成(arXiv:2601.08430)，诊断采用 RIFT 八失效模式(arXiv:2604.01375)。

## Core Architecture

### 三层组织结构

1. **lib/** - 基础库，纯标准库实现，零第三方依赖
   - `xlsx.py`: 直接解析 xlsx 文件(zipfile + ElementTree)，无需 openpyxl/pandas
   - `llm.py`: OpenAI 兼容客户端，带磁盘缓存(按 model+prompt+params 哈希)、并发控制、重试

2. **stages/** - lean 主线，每步独立读写 jsonl
   - 每步可单独重跑，不影响其他步骤
   - 结构线：s00_seed / s00b_sample / s01_filter / s02_context / s02_5_route
     / s03_perspective / s04L_rubric / s11L_diagnose / s11Lb_remedy
     / s04Lb_split / s04Lc_severity
   - Phase 4 实测线：s10L_pool / s12L_judge / s11Lc_consequential
     / s11Ld_remedy / s11Le_select
   - 旧全量线（s03b/s04/s07/s08/s09/s11/s11b）已归档到 `legacy/full_path/`

3. **config/** - 模型端点配置
   - `models.json`: 运行时配置，含 api_key，已在 .gitignore
     （`config/models.json.*` 通配同样被忽略，防 `.bak` 泄露密钥）
   - `models.json.example`: 配置模板

### 仓库布局（2026-08-13 整理后）

```
stages/      lean 主线 9 个脚本
lib/         基础库 5 个（含 dimensions.py 通用维度词表）
legacy/      已归档，不参与交付，保留可运行状态（见 legacy/README.md）
  full_path/   旧全量线 9 个（含确定作废的 s09_normalize）
  phase3/      多模型聚合支线 4 个
  phase4/      s05_grounding / s05b_anchor，Phase 4 启动时取回 stages/
  run_pipeline.py  只驱动 full_path/ 那条线
docs/
  design/    流程定稿与实施计划
  advisor/   给导师看的展示材料
  reports/   技术报告：审查、修复记录、phase 报告
outputs/
  excel/     填充后的 xlsx 产出
  samples/   样例展示
scripts/     辅助脚本 6 个；一次性/被取代的在 scripts/legacy/
tools/       watch.py；其余监控面板在 tools/legacy/
```

`scripts/` 下的脚本已改为基于 `__file__` 定位仓库根，可从任意目录调用。

### 数据流（lean 主线）

```
xlsx (input.xlsx)
  → s00_seed        → seed.jsonl + baseline.json
  → s01_filter      → s01_filter.jsonl
  → s02_context     → s02_context.jsonl          (intent + scenarios)
  → s02_5_route     → s02_5_route.jsonl          (question_type + rubric_form + blocks)
  → s03_perspective → s03_perspective_lean.jsonl (RP_RET=lean)
  → s04L_rubric     → s04L_rubric.jsonl          (准则直出，含血缘 + 质量标记)
  → s11L_diagnose   → s11L_diagnosed.jsonl       (RIFT 四失效模式)
  → s11Lb_remedy    → s11Lb_remedied.jsonl + _defect_queue.jsonl
  → s04Lb_split     → s04Lb_split.jsonl          (拆非原子 + 事实纠错 + 标记重写)
  → s04Lc_severity  → s04Lc_severity.jsonl       (负项 severity + is_veto)
```

Phase 4 实测线（`bash scripts/rerun_phase4.sh`，只跑 388 道双回复题）：

```
s04Lc_severity.jsonl（452）
  → 筛双回复      → s04Lc_phase4.jsonl          (388 题，硬约束 1)
  → s10L_pool     → s10L_pool388.jsonl          (6 档 × 388 = 2328 回复)
  → s12L_judge    → s12L_judged388.jsonl        (含 veto 两票 + 同源一致性修正)
  → s11Lc_cons    → s11Lc_cons388.jsonl         (Hackable / LowSignal / floor)
  → s11Ld_remedy ⇄ s12L 重判 ⇄ s11Lc 复诊       (×3 轮闭环，处置不收敛见下)
  → s11Le_select  → s11Le_final.jsonl           (各轮实测里挑每题最优)
  → s04Lc_severity（补分级）→ 合并 64 单回复题 → s11Le_all452.jsonl ← 交付源
  → export_advisor_schema.py
      → outputs/rubrics_advisor_lean.jsonl  交付档
      → outputs/rubrics_internal.jsonl      内部档（血缘/诊断/标记）
  → fill_xlsx_preserve_format.py
      → outputs/excel/*.xlsx                交付档同源，C 列人读版
```

**导出源必须是流水线末端**（跑过 Phase 4 = `s11Le_all452.jsonl`，
没跑 = `s04Lc_severity.jsonl`）。指向中间步会
静默丢掉后续产出 —— 已经踩过两次（RIFT 诊断未生效、severity/veto 全空）。
`export_advisor_schema.py --src` 默认值、`rerun_lean_fixed.sh`、
`fill_xlsx_preserve_format.py` 的默认源都已对齐；`audit_rubrics.py` 把
「负项缺 severity」计入指标，换错源审计里会亮。

一键重跑：`bash scripts/rerun_lean_fixed.sh`（清缓存加 `RP_CLEAN=1`）。

每步输出在 `data/` 下，缓存在 `cache/<stage>/<hash>.json`。

## Commands

### Phase 0 (已实现)

```bash
# 运行 Phase 0: 将 xlsx 转为种子集 + 计算基线指标
python3 stages/s00_seed.py

# 指定输入 xlsx 路径(默认 data/input.xlsx)
RP_XLSX=/path/to/input.xlsx python3 stages/s00_seed.py

# 指定输出目录(默认 data/)
RP_OUT=/path/to/output python3 stages/s00_seed.py
```

产出：
- `data/seed.jsonl`: 453 条记录，每条含 rid、question、subject、draft_rubric、ref_responses
- `data/baseline.json`: 草稿 rubric 的结构指标(维度数、准则数、满分分布、负向项统计)

### Phase 1-4 (待实现)

后续 phase 需要配置 `config/models.json`(见下文"模型端点配置")，详见 `docs/design/PLAN.md`。

## Development Workflow

### 添加新 stage

1. 在 `stages/` 下创建 `sXX_<name>.py`(XX 为步骤编号，如 02、03)
2. 从 `data/` 读前序步骤的 jsonl，写本步输出到新 jsonl
3. LLM 调用走 `lib.llm.call(model, messages, stage='sXX')`:
   - `stage` 参数决定缓存子目录，便于按步清理缓存
   - 返回 `(text, meta)`，meta 含 `cached`、`model`、`usage`
4. 若需并发，用 `lib.llm.parallel_map(fn, items, workers=8)`

### 模型端点配置

复制 `config/models.json.example` 为 `config/models.json`，填入实际端点：

```json
[
  {
    "name": "gen-main",           // 内部标识
    "model_id": "...",            // 传给 /v1/chat/completions 的 model 字段
    "base_url": "http://...:8000/v1",  // vLLM 等 OpenAI 兼容服务
    "api_key": "EMPTY",           // 无鉴权填 EMPTY
    "family": "qwen",             // 厂商/系列，用于异质性校验
    "roles": ["generator"],       // 角色标签
    "timeout": 180
  }
]
```

**硬约束**(流程强制要求，见 `docs/design/PLAN.md` §1)：
- 步骤 6(多模型聚合): 需 ≥2 个 `generator`，且 `family` 不同(同系列共享盲区)
- 步骤 11(RIFT 诊断): `diagnoser` 需异质组合(Gemini 系强于 Ungrounded/Subjective，GPT 系强于 Missing/Low Signal)
- 步骤 12(判分): `judge` 的 `family` 必须不同于 `generator`(避免自偏好偏差)

### 缓存机制

- 缓存键 = sha256(model_id + base_url + messages + temperature + max_tokens + extra)[:32]
- 路径: `cache/<stage>/<hash>.json`
- 调 prompt 后，只重算受影响的哈希；未变的调用直接命中缓存
- 清理缓存: `rm -rf cache/sXX/` (按 stage) 或 `rm -rf cache/` (全部)

### 监视面板

**watch.py** - 8 区块详尽模式：
```bash
python3 tools/watch.py          # 全屏刷新，Ctrl-C 退出
python3 tools/watch.py --once   # 快照，便于贴日志
python3 tools/watch.py --tokens # 展开 token 明细
```

显示：进程详情（pid/socket/环境变量）、在飞请求的完整 prompt/output、
最近完成的详细内容、产出文件列表、端点负载与调用成功率。

详细说明见 `tools/README_watch.md`。
（`panel.py` 等旧面板已归档到 `tools/legacy/`。曾经文档里提到的 `watch_v2.py` 从未存在。）

**特性**：
- 只读，不影响在跑的进程
- 全屏模式走终端备用屏(像 vim/htop)，退出后原内容恢复
- 自动发现 `cache/` 下的新步骤，无需修改面板代码
- 数据来源：`cache/<stage>/*.json` + `cache/_events.jsonl` + 端点 `/metrics`

**端点侧 token 有多副本坑**：某些 URL 后面挂多个副本，`/metrics` 随机命中一个，累计计数器会跳动。面板靠「计数器只增不减」识别副本并求和。

## Critical Constraints

以下各条违反即出错(前五条详见 `docs/design/rubric_pipeline_full_v2.md` §3.3)：

1. **锚定回复 ≠ 待评回复**(步骤 5): 锚和待评同源会导致 rubric 从待评回复自身衍生
2. **判分器 ≠ 生成器**(步骤 12): 同系列模型有自偏好偏差，判分虚高
3. **锚点集 ∉ 训练集**(步骤 14): 锚点集一旦参与训练就不再独立，失去参照作用
4. **血缘标签必须在步骤 4 挂**: 用于回溯每条准则来自哪个 Scenario，后续诊断依赖此标签
   —— s04L 挂在 `_criterion_id / _perspective_ids / _scenario_ids`，
   只进 `outputs/rubrics_internal.jsonl`（`--full`），不进交付档
5. ~~**闸门项不进 S_max 分母**(步骤 9)~~ —— **导师 2026-08-13 推翻**：
   score 直接当权重用，不在流水线内归一，归一化延后到判分阶段。
   因此 `full_mark = sum(正向 score)` 保持原始整数，闸门项计入分母；
   交付档用准则级 `is_gate` 标出闸门是哪一条，判分侧自行处理 0/1 语义。
   `legacy/full_path/s09_normalize.py` 随之作废。
   **字段语义（导师 2026-08-14 复核后确认）**：`is_positive` 是正向/负向（方向），
   `is_gate` 是 0/1 阀门标记。内部 `data/*.jsonl` 与 `outputs/` 导出档口径一致，
   不存在语义分叉。语义的唯一实现在 `lib/rubric.py`，业务代码不许内联公式。
6. **veto 是负项专属**（步骤 4Lc → 12）：`is_veto` 只能标在负向准则上
   （`lib/rubric.is_veto()` 强制这一条），与 `is_gate`（正向答案阀门）方向相反。
   聚合规则显式声明在 `lib/rubric.VETO_RULE`，交付档 / xlsx / 判分侧引用同一句：
   任一 `is_veto` 项成立 → 整题得分率 0，不进补偿式求和。
   veto 项本身不进 `full_mark` 分母。
   **诊断侧一律用 `raw_rate`**（不含 veto 的补偿式得分率）：veto 归零是聚合规则
   不是 rubric 质量信号，混进 gap/std/floor 会让强档一 veto 就成片假阳性。

## Implementation Status

lean 主线已跑通 452 条全量（2026-08-13）：
- ✅ Phase 0 数据层(s00_seed.py) + 基础库(xlsx.py, llm.py, dimensions.py, rubric.py)
- ✅ Phase 1 试跑(20 条)：验证 RET 能导出多样视角
- ✅ Phase 2 结构全量
- ✅ 步骤 4L 准则直出(s04L_rubric.py)：全题预算制，5.4 条/题
- ✅ 步骤 11L RIFT 免池诊断 + 11Lb 分级处置
- ✅ 步骤 4Lb 拆分/重写 + 4Lc 负项分级与 veto 标记：452 题 3226 条，
     负项 614 条全带 severity，195 条 veto 覆盖 166 题（36.7%）
- ✅ 交付导出(export_advisor_schema.py)：交付档 + 内部档双出，
     负项带 severity/is_veto；xlsx 与交付档同源
- ✅ **Phase 4 实测全量（2026-08-17）**：388 双回复题 6 档回复池 2328 条、
     判分 16517 条、区分度诊断 + 3 轮处置闭环 + 终态选择。
     无缺陷 66.2% → 73.2%，无一题退步。交付档 452 题 3207 条。

归档但未废：`legacy/phase3/` 多模型聚合、`legacy/phase4/` grounding + 锚点集。

待实现：
- **PLAN.md Phase 4 检查点 2**（放行闸门，尚未做）：新 rubric 与草稿 rubric 的
  成对一致性 —— 判分侧证据齐了，这一步才是「能不能交」的判据
- 步骤 13 badcase 聚合、步骤 14 回流
- 已知未修：cut 档 20.9% 造法失效（LLM 删段不可靠，要改程序化删段）；
  64 道单回复题按硬约束 1 无法进 Phase 4，缺实测证据

**2026-08-13 修复批次**（起因见下方「交付审查」）：

1. **交付导出走错源**（根因，影响面最大）：`rerun_lean_fixed.sh` 的临时 heredoc
   从 `data/s04L_rubric.jsonl`（**未经诊断**）导出，s11L/s11Lb 跑了但产出没进交付。
   交付版 2452 条准则一条没过 RIFT。修复：改调 `export_advisor_schema.py --src data/s11Lb_remedied.jsonl`。

2. **RIFT non-atomic 过触发**：判 defective 1511/2452 = 61.6%，其中 non-atomic 1399 条（57%）。
   把「A 如何、B 如何」这类**对比类准则**误判为非原子——而题目问的就是区别，拆开即失去意义。
   修复：`s11L_diagnose.py` 的 `SYS_ATOM` 改为单一判定原则（拆开后两半能否各自独立成立）
   + 四类原子白名单（对比/排除/列举完备/判断带限定）。

3. **处置策略错误**：旧 `s11Lb` 一律删除，导致 344/452 题因「删完不足 3 条」被静默跳过，
   真删的 108 题里 **4 道 gated_answer 的答案项被删掉**（q0008 满分 12→3）。
   修复：按失效模式分级 —— subjective/ungrounded 删、non-atomic 落 `_defect_queue.jsonl` 待拆、
   **闸门项一律豁免**、删后不足则回滚并打 `needs_regen`。

4. **交付 schema 缺字段**：`rubric_form` / `is_gate` / `blocks` / 血缘全被
   `DELIVER_FIELDS` 过滤掉了（数据一直在上游，不用重跑）。修复：导出层重写，加 `--src` / `--full`。

5. **程序化护栏**：prompt 管不住的用代码兜，`s04L_rubric.py` 的 `flag()` 打五类标记
   （`_flag_vague` / `_no_groundtruth` / `_cliff` / `_mention_only` / `_subjective_threshold`），
   只打标不删，交给 s04Lb 重写，避免全量重跑。

**2026-08-14 Phase 4 试点审计 + 测量工具修复**（详见 `docs/reports/AUDIT_48PILOT_PHASE4.md`）：

48 题试点（s10L_pool48 → s12L_judged48 → s11Lc_cons48）逐题审计发现
24 个 Hackable 信号里 15 个是 pool 造法失效、6 个是判分错误、只有 3 个真缺陷。
修复：
1. `lib/answer_check.py`（新）：程序化答案核验共享模块。修 option 正则 `A.`
   永不命中的 bug（q0179 闸门清零根因）；短数字（≤2 位）改「结论标记上下文
   + 行尾标点」匹配，防 `2` 命中公式常数 `2π`；提供 `has_correct_answer()`
   给 s10L 做对抗/弱档反向校验。
2. `s10L_pool.py`：strong 退化用最强模型重生成（跳过 10→1）；adv/weak 生成后
   反向校验「结论 ≠ answer_canonical」，答对自动重试、仍答对标 `answer_correct`
   （诊断侧剔除）；cut 删量 <8% 自动重试；两趟执行保证 cut 基于最终 strong。
3. `s12L_judge.py`：同源一致性护栏——trunc/cut 是 strong 字面子集，子集档 met
   而 strong 未 met 在逻辑上不可能，以 strong 为准修正（记 `judge_fixed`）。
4. `s11Lc_consequential.py`：gated 题 weak_mean 只用 weak+adv；trunc/cut 与
   strong 正向 met 集相同 → 构造失效剔除；gap 改 strong−weak 单档差；
   trunc/cut 平分降级为 `suspect_ties`（不计 defective）；floor 题抑制
   LowSignal/surface（处置方向相反）。
复测：Hackable 24→10、LowSignal 18→11、跳过 10→1。残留 = 3 真缺陷（q0167/
q0336/q0388）+ 7 弱档造法失败（canon 缺失无法程序化拦截，待 s11Ld 处置时
LLM 复核）+ 4 地板（q0149/q0377/q0408/q0440，处置方向=放松准则）。

**2026-08-14 傍晚 s11Ld 处置闭环**：新建 `stages/s11Ld_remedy.py`（只对实测
确认的缺陷动手：hackable→重写、floor→放松、pool 嫌疑→标记不重写、支持
`RP_S11LD_ONLY` 白名单；正向分值守恒、血缘逐槽继承）。处置审计确认的 7 题并
闭环复测（s11Ld → s12L 重判 → s11Lc 复诊）：**7/7 清零，floor=0**。
q0388 三轮收敛（过松→过严→适中）、q0377 两轮——LLM 重写会摆动，处置必须
闭环复测才算完成。48 题终态：Hackable=7（全是 canon 缺失的弱档造法失败，
已标 pool_suspect）、LowSignal=10（7 个同源 + 3 个阈值边缘）、跳过=1。
Phase 4 全量前最后一个工具缺口：s10L 对 canon 缺失的 adv/weak 档加廉价
LLM 复核（结论是否等于标准答案）。最终链：
sample48 → s10L_pool48 → s12L_judged48 → s11Lc_cons48 → s11Ld_remedied48。

**2026-08-14 深夜 试点闭环（48 题 Hackable 24→0）**：
- s10L 修复 F：canon 缺失的 adv/weak 档加 judge 模型相对复核（拿 strong 当
  参考答案，只比最终结论，宁放过不误杀），拦截 14 档「答对」造法失败。
  open 题不做「弱档不够弱」文本复核（文本质量≠rubric 得分，实测误杀）。
- s12L SYS 第 4 条纪律：答案类准则只认**最终结论**——对抗档「过程全对、
  最终答案写空集」曾拿 80% 与强档打平（q0301），复判后 80%→10%。
- s11Lc 修复 E：gated 题收紧——对抗档过程全对是设计使然，过程级准则在
  adv/weak 的翻转降级待复核；gated 弱档追平=疑似答对（weak 对 gap 作废）；
  无有效弱档时 LowSignal 抑制。
终态：Hackable=0、LowSignal=3（q0058/q0433 open 弱档不弱 + q0113 判分
方差边缘）、floor=0、跳过=1、待复核 4 处（gated 弱档疑似答对）。**测量
工具闭环：缺陷可处置、处置可复测、假信号被源头拦截，Phase 4 可放量 452。**

**2026-08-17 交付导出补 veto 链路**（判分侧要执行合取门，交付档得先带上字段）：
1. `DELIVER_FIELDS` 加 `severity` / `is_veto`（走 `NEGATIVE_FIELDS`，只挂负项；
   `is_veto` 经 `lib/rubric.is_veto()` 过一遍，正向项标了也不认）。此前两个字段
   被白名单过滤，交付档里 severity 全 None、veto 0 条。
2. `--src` 默认值 `s11Lb_remedied` → `s04Lc_severity`（流水线末端）；
   `rerun_lean_fixed.sh` 补第 6 步 s04Lc、导出改 7/7。
3. **白名单改规则**：内部档改带全部 `_` 前缀字段。白名单漏字段是静默的，
   已漏过两批（第一批两个 `_flag_*`，第二批 s04Lb 的 `_rewritten_from` /
   `_pending_split` / `_split_skipped` / `_factfix*` / `_needs_review` 加 s04Lc 的
   `_veto_block` / `_s04Lc_*`）。交付侧仍是严格白名单，所以放宽内部侧不影响口径。
4. `fill_xlsx_preserve_format.py` 默认源 `data/s04b_core.jsonl`（legacy 全量线，
   既没过 RIFT 也没有 is_gate/severity）→ 交付档本身。xlsx 与 jsonl 必须同源。
   人读版标 ⭐答案判据 / 🚫一票否决 + [严重性]，带 veto 的题末尾附 `VETO_RULE` 原文。
5. `audit_rubrics.py` 加四项：`severity` 覆盖度、veto 条数（`~` 前缀=中性覆盖度，
   对比区不判好坏）、veto 门槛三复核（非原子 / 主观阈值 / 非 principle）。

复核：交付档 452 题 3226 条，除新增两字段外与上一版逐字节相同；负项 614/614
带分级，195 条 veto 全 principle 级、门槛三项全 0；xlsx 只有 C 列变动（452 行）。

**2026-08-17 Phase 4 全量跑通（388 双回复题）**：链路
`s04Lc_phase4 → s10L_pool388 → s12L_judged388 → s11Lc_cons388 → s11Ld×3 轮闭环
→ s11Le_final → 合并 64 单回复题 → 交付`，一键：`bash scripts/rerun_phase4.sh`。

实测终态：**无缺陷 257/388 (66.2%) → 284/388 (73.2%)**，无一题退步，`s_max` 全守恒。
残留 LowSignal 42 / floor 13 / Hackable 7+6 / skip 32（测量受限）。

四个测量工具缺陷，都是**只有放量才暴露**的（48 试点上不存在或被掩盖）：

1. **mid 档序失效**（`s10L_pool.py` + `s11Lc`）：`SYS_MID` 只写「篇幅适中」，
   pool_mid 把「不做深入展开」读成「答简短」——中位 244 字 < weak 的 395 字
   （两档还是不同模型，篇幅无跨档约束），**65% 的题 mid ≤ weak**。修：
   `SYS_MID` 改绝对下限 600 字 + 「每个要点都要给依据」，把「中等」锚在覆盖
   深度而非篇幅；`s11Lc` 加档序护栏（mid ≤ weak/adv 即剔除）。
   注：mid 只进 std 与 ceiling，不进 gap/Hackable，所以它**掩盖信号而非制造
   假信号** —— 剔除后 LowSignal 仍 61，ceiling 3→5。
2. **strong 档答错**（`s11Lc`）：`strong_degenerate` 只查篇幅，查不出「答得长
   但答错」。22 道地板题里 5 道 strong 程序化核验就是错的（q0078/q0199/q0262/
   q0353/q0378），放松准则治不了。修：`answer_check` 进跳过逻辑，参照系坏了
   不给结论。跳过 18→32。
3. **锚可达性门**（`s11Ld.anchor_reachable`，最关键）：rubric 本就是从参考回复
   推出来的，**锚能拿到分就说明准则可满足**，地板来自 pool 侧。实测 8 道复测
   后仍地板的题，**6 道锚拿到 42%~100%**（q0032 73% / q0047 80% / q0050 100% /
   q0235 50% / q0263 42% / q0443 100%），只有 q0020/q0279 是连锚都拿不到的真过严。
   没这道门，那 6 道好用的 rubric 会被"放松"改坏。探针复用 `s12L.build` 的判分
   口径 —— 换口径测出来的分与地板判定不可比。
4. **答偏题门**（`s11Ld.check_on_target`）：q0105 题目函数有缺项，strong 自行
   补成 `sin(2πx)+x+1` 再作答。参照系偏了，rubric 不动。

**处置不收敛，是 2-循环**（新结论，比试点的「会摆动」更强）：q0221 走出
60%→0%→60%→0%，q0028/q0071 同型。「收紧」与「放松」是互逆操作，对这类题
不存在两头都满足的中间档，再多跑几轮只是在两个坏状态间来回。故新增
`stages/s11Le_select.py`：跑固定轮数（默认 3），再**在各轮实测证据里挑每题
最好的一版**（缺陷数少者优，同分取靠后轮次），残留照实记进 `_s11Le`。
选择逻辑单调 —— 实测无一题从无缺陷退步。

已知代价：hackable 重写把「提及即得分」改成内容核对式（「判定 X 并说明 Y」），
`提及即得分` 5→0，但 `疑似非原子` 152→180。**+28 全在那 55 道重写题内**
（397 道未动的题 396→396），落进既有 `_pending_split` 队列，不是新缺陷类型。

**交付审查发现**（`outputs/rubrics_advisor_lean.jsonl` 全量统计，2026-08-13）：
- 35 条准则引用「标准答案」但交付档里没有标准答案 → 判分器无法独立执行
- 115 题存在单条正向准则 ≥50% 满分，其中 72 条是「全部/且/每」全量复合 → 0/1 悬崖
- 29.6% 正项是「提及即得分」型；53/500 负项用「严重/显著」当阈值
- 13 条负项写死某个具体错误答案（过拟合参考错误）
- 根因之一：`s00_seed.py:50` 的 `ref_errors` 只存了 key 名，**错误内容被丢弃**，
  而 `s04L` 却告诉模型「本题有 N 条错误可参考」→ 模型只能编。待 T1 修。
- 遗留：`s04L_rubric.py` 的 prompt 正例 `"最终答案为7cm，与标准答案一致"`
  教会了模型「与标准答案一致」句式；正例 `"列出...64卦名称，无遗漏"` 教出了全量悬崖项。待 T1 修。

## Key Design Decisions

### 题型路由(步骤 2.5，新增)

判定 query 属于 verifiable(可验证) / open(开放) / hybrid(混合)，路由到不同 rubric 形态：

| 题型 | rubric_form | RET 执行策略 | 原因 |
|------|-------------|-------------|------|
| verifiable | `gated_answer` | 固定 3 视角，不跑 R_w | 数学题/代码题本质是 k=1 单准则，强行多维展开会稀释主准则 |
| open | `analytic` | 完整 RET(R_h + R_w) | 创作/建议/分析题需多维度覆盖 |
| hybrid | `multi_part` | 分 block 处理 | 每子题独立判型后选策略 |

这是对三篇骨架论文的扩展(它们假定所有任务都是 open)。

### RET 实现策略(`docs/design/PLAN.md` §3.1)

**建议**: R_h(层次展开)批量、R_w(水平展开「还漏了什么」)忠实。
- 批量: 一次调用出场景+视角骨架 → 约 2 次/题
- 忠实: R_w 单独调用，保住「覆盖全面性」的核心价值 → 约 5-6 次/题
- Phase 1 会对比两种策略的维度数，择优

### 单回复记录处理(`docs/design/PLAN.md` §3.2)

种子集中 64 条只有 1 条参考回复(无法做锚定与待评分离)：
- Phase 2/3(结构指标): 全部 453 条
- Phase 4(判分): 仅 389 条双回复记录

## Documentation

- `docs/design/rubric_pipeline_feishu_v2.md`: 流程定稿(14 步 + 题型判定)，简明版
- `docs/design/rubric_pipeline_full_v2.md`: 完整版，含论文依据、推导过程、逐步出处对照
- `docs/design/PLAN.md`: 在 453 条种子集上跑通全流程的分阶段实施计划，含成本估算与检查点
- `data/baseline.json`: 草稿 rubric 的基线结构指标(对照目标)

## Code Style

- 纯标准库，无第三方依赖(已在 Python 3.12.3 验证)
- 中文注释、中文变量名(仅核心概念如 `draft_rubric`、`ref_responses` 用英文)
- 每个 stage 脚本顶部注释说明其在流程中的位置(如 `"""Phase 0：xlsx → seed.jsonl..."""`)
- 错误处理: 解析失败计数但不中断，最后统一报告(如 `s00_seed.py` 中 `bad` 计数)

## Environment Variables

- `RP_XLSX`: 输入 xlsx 路径(默认 `data/input.xlsx`)
- `RP_OUT`: 输出目录(默认 `data/`)
- `RP_CACHE`: 缓存目录(默认 `cache/`)
- `RP_EVENTS`: 调用事件流水路径(默认 `cache/_events.jsonl`，设为空串可关闭)
- `RP_EV_CHARS`: 流水里输入/输出各留多少字(默认 400)
- `RP_EV_MAX`: 流水滚存阈值 MB(默认 64)

## Notes

- xlsx 的 A/B/C 列(need_rewrite, rewritten, gen_rubric)在 Phase 2+ 才填充
- `baseline.json` 中的指标是**要打败的对照**: 如维度去重数从 1 → ≥4、知识正确性占比从 100% → ≤50%
- Phase 1 的检查点是「单位 token 信息量最高」的验证: 若 RET 在散学科分布上导不出多样视角，整个骨架选择需重议

