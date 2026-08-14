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
   - 9 个脚本：s00_seed / s00b_sample / s01_filter / s02_context / s02_5_route
     / s03_perspective / s04L_rubric / s11L_diagnose / s11Lb_remedy
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
  → s11L_diagnose   → s11L_diagnosed.jsonl       (RIFT 三失效模式)
  → s11Lb_remedy    → s11Lb_remedied.jsonl + _defect_queue.jsonl
  → export_advisor_schema.py
      → outputs/rubrics_advisor_lean.jsonl  交付档
      → outputs/rubrics_internal.jsonl      内部档（血缘/诊断/标记）
```

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

以下五条违反即出错(详见 `docs/design/rubric_pipeline_full_v2.md` §3.3)：

1. **锚定回复 ≠ 待评回复**(步骤 5): 锚和待评同源会导致 rubric 从待评回复自身衍生
2. **判分器 ≠ 生成器**(步骤 12): 同系列模型有自偏好偏差，判分虚高
3. **锚点集 ∉ 训练集**(步骤 14): 锚点集一旦参与训练就不再独立，失去参照作用
4. **血缘标签必须在步骤 4 挂**: 用于回溯每条准则来自哪个 Scenario，后续诊断依赖此标签
   —— s04L 挂在 `_criterion_id / _perspective_ids / _scenario_ids`，
   只进 `outputs/rubrics_internal.jsonl`（`--full`），不进交付档
5. ~~**闸门项不进 S_max 分母**(步骤 9)~~ —— **导师 2026-08-13 推翻**：
   score 直接当权重用，不在流水线内归一，归一化延后到判分阶段。
   因此 `full_mark = sum(正向 score)` 保持原始整数，闸门项计入分母；
   判分侧自行处理闸门的 0/1 语义。`legacy/full_path/s09_normalize.py` 随之作废。
   **导师 2026-08-14 明确交付 schema 口径**：`score` 的正负号即加分/扣分（方向），
   `is_positive` 是 0/1 阀门标记（原 `is_gate` 语义，该字段已删）。
   ⚠️ 字段语义分叉：内部 `data/*.jsonl` 的 `is_positive` 仍是方向（改它要重跑生成），
   `outputs/` 两份导出档的 `is_positive` 是阀门；唯一转换点在 `export_advisor_schema.py`。

## Implementation Status

lean 主线已跑通 452 条全量（2026-08-13）：
- ✅ Phase 0 数据层(s00_seed.py) + 基础库(xlsx.py, llm.py, dimensions.py)
- ✅ Phase 1 试跑(20 条)：验证 RET 能导出多样视角
- ✅ Phase 2 结构全量
- ✅ 步骤 4L 准则直出(s04L_rubric.py)：全题预算制，5.4 条/题
- ✅ 步骤 11L RIFT 免池诊断 + 11Lb 分级处置
- ✅ 交付导出(export_advisor_schema.py)：交付档 + 内部档双出

归档但未废：`legacy/phase3/` 多模型聚合、`legacy/phase4/` grounding + 锚点集。

待实现：
- Phase 4 回复池 + 判分(约 +10k-15k 次)：步骤 10、12、13、14
- 步骤 4Lb 非原子准则拆分(`stages/s04Lb_split.py`)，消费 `data/_defect_queue.jsonl`

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

