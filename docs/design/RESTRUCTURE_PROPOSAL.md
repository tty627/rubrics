# 代码结构重构与打包方案（2026-08-17，待定稿）

> 目标：把仓库整理成「正常项目」的样子（命名可读、结构自解释、换机可跑），
> 然后打包上 GitLab、搬到开发机跑下一轮生成。**本方案不改任何数据口径、不重跑任何 LLM。**

## 1. 现状评估

### 已经是正常项目的地方（不动）

- `lib/ stages/ scripts/ docs/ config/ legacy/ data/ outputs/` 分层清晰，职责单一；
- 根目录有 `README.md`（人类快速开始）+ `CLAUDE.md`（agent 指南）；
- 纯标准库、零第三方依赖，`config/models.json` 密钥已 gitignore；
- stage 之间只靠 jsonl 文件耦合，任一步可独立重跑。

### 确实存在的问题

1. **stage 命名不统一**（你指出的核心问题）：
   - `s02_5_route.py` 文件名里混了小数点；
   - `s04L_rubric / s10L_pool / s11L_diagnose / s12L_judge` 里的 `L` 是「lean 主线」的
     历史痕迹——legacy 已归档，这个字母现在是纯噪声；
   - 子步后缀 `b/c/d/e` 是位置编号、无语义，`s11Lb_remedy` 与 `s11Ld_remedy`
     两个同名 remedy，`s12Lb` 与 `s11Lb` 的 `b` 含义不同，新人不查文档分不清。
2. **数据文件默认名带基数**：`s10L_pool388.jsonl` 里的 `388` 是当前数据集基数，
   换数据集就要改名；默认名应该不带基数，具体基数由 rerun 脚本传 env。
3. **缺失的正常项目标配**：无 `tests/`（语义核心 `lib/rubric.py` 零单测）、
   无 `pyproject.toml`（一句「纯标准库」没落成文件）、`scripts/test_s04L_fixes.py`
   是一个测试却混在 scripts/ 里。
4. **打包断层**：`data/ outputs/ *.xlsx` 全在 .gitignore，纯 git push 到开发机
   拿不到种子和交付物——上一轮已确认。

## 2. 命名方案（核心）

### 规则

`sNN[a-e]_语义词.py`：`NN` = **PLAN.md 14 步计划里的步骤位**（与 legacy 编号、
数据文件前缀、报告里的「步骤 12」说法全部对齐）；`a-e` = 同一步骤内的子步，按
执行顺序编号；去 `L`；`s02_5` 归位为 `s02b`。

### 映射表

| 现名 | 新名 | 职责（一句话） |
|---|---|---|
| s00_seed.py | s00_seed.py | xlsx → 种子集 + 草稿基线 |
| s00b_sample.py | s00b_sample.py | 全量前小样本（工具用） |
| s00c_pilot.py | s00c_pilot.py | 按 rubric_form 分层的试点抽样 |
| s01_filter.py | s01_filter.py | 题目过滤 |
| s02_context.py | s02_context.py | intent + scenarios |
| s02_5_route.py | **s02b_route.py** | 题型路由（步骤 2.5） |
| s03_perspective.py | s03_perspective.py | RET 视角展开 |
| s04L_rubric.py | **s04_rubric.py** | 准则直出（含血缘+质量标记） |
| s04Lb_split.py | **s04b_split.py** | 拆非原子 + 事实纠错 |
| s04Lc_severity.py | **s04c_severity.py** | 负项分级 + veto 标记 |
| s05L_ground.py | **s05_ground.py** | 锚定 grounding（当前主链未启用） |
| s10L_pool.py | **s10_pool.py** | 6 档回复池 |
| s11L_diagnose.py | **s11_diagnose.py** | RIFT 失效模式诊断 |
| s11Lb_remedy.py | **s11b_remedy.py** | 诊断后分级处置 |
| s11Lc_consequential.py | **s11c_consequential.py** | 区分度诊断（Hackable/LowSignal/floor） |
| s11Ld_remedy.py | **s11d_remedy.py** | 实测闭环处置（处置 → 重判 → 复诊） |
| s11Le_select.py | **s11e_select.py** | 各轮实测里挑每题最优 |
| s12L_judge.py | **s12_judge.py** | 判分（veto 两票制） |
| s12Lb_draft_judge.py | **s12b_draft_judge.py** | 草稿 rubric 判分（检查点 2） |
| s12Lc_pairwise.py | **s12c_pairwise.py** | 新 vs 草稿 pairwise 放行闸门 |

### 数据文件默认名同步清理（连带项）

数据文件名跟随 stage 前缀改名并**去掉基数**（`s12L_judged388.jsonl` →
`s12_judged.jsonl`）；rerun 脚本按当前数据集传带基数的 env（如
`RP_S12L_OUT=s12_judged388.jsonl`），下一轮换数据集只改脚本不改代码。
`llm.call(stage=...)` 的缓存目录名**不动**（`s12L` 等），改了就丢缓存。

## 3. 结构方案（目标树）

```
rubrics/
  README.md            # 更新：新命名映射表 + 快速开始
  CLAUDE.md            # 更新：数据流图 + 状态
  pyproject.toml       # 新增：仅元数据，requires-python>=3.12、零依赖
  Makefile             # 新增（可选）：make check / phase4 / checkpoint2 / export
  config/models.json.example
  lib/                 # 不动
  stages/              # 20 个 stage，按 §2 改名
  scripts/             # rerun_*.sh + 导出/审计；test_s04L_fixes.py 移走
  tests/               # 新增：test_rubric.py（lib/rubric.py 纯逻辑单测，零 LLM）
  docs/{design,reports,advisor}
  legacy/              # 不动（历史归档，含自己的 README）
  data/ outputs/ logs/ cache/   # 运行产物（gitignore），随行走 tar
```

新增内容量：`tests/test_rubric.py`（约 12 条断言：s_max / gate_indices / is_veto /
apply_veto / aggregate / rate）+ `pyproject.toml`（10 行）。`lib/rubric.py` 是
「语义的唯一实现」，开发机下一轮改动前有这层测试兜底，值得。

## 4. 打包与搬迁方案

到开发机需要三样东西，载体分开：

1. **代码**：重构后 git push GitLab（含 `config/models.json.example`，
   **绝不含 `config/models.json`**——api_key 不进版本库）。
2. **数据**：`tar czf data_outputs.tar.gz data/ outputs/`（478M+19M；
   `cache/ 618M` 可重建，**不带**）。若采纳 §5 选项 B，种子类小文件进 git，
   tar 只需带中间产物（或整个不带，开发机重跑）。
3. **密钥**：开发机本地从 `models.json.example` 复制填 key（或走你自己的密钥下发）。

搬完后的就绪检查：`python3 scripts/check_before_run.py` + `python3 tests/test_rubric.py`。

## 5. 需要你拍板的三个选择

> **最终拍板（2026-08-17）**：A = ②（stage + 数据文件名一起改）；B = 改主意为
> **种子不入库**（GitLab 只传可运行源码，data/ 全忽略，数据随侧车 tar）；C = ②。
> 下文选项保留当时原文，B 以本行为准。

- **A. 命名范围**：①只改 stage 文件名（数据文件名不动，风险最低）；
  ②stage + 数据文件默认名一起改（推荐，本方案 §2 完整版）；
  ③不改名，只加 README 映射表。
- **B. 种子是否入库**：①`.gitignore` 加白名单让 `data/seed.jsonl`、
  `data/input.xlsx`、`data/baseline.json` 进 git——clone 后直接可跑下一轮，
  不再依赖侧车 tar（推荐，共约 2MB）；②维持全忽略，数据全靠 tar。
- **C. 结构新增范围**：①最小（重命名 + 文档同步 + tests/test_rubric.py）；
  ②标准（① + pyproject.toml + Makefile）；③仅重命名。

## 6. 执行与风险控制（确认后一次性做完）

1. `git mv` 逐个改名 + 全仓 sed 更新引用（2 处 stage import、6 个脚本、
   README/CLAUDE.md；`legacy/` 与 `docs/reports/` 历史报告不追改，
   README 里注明旧名对照）。
2. 静态验证：`python3 -m py_compile stages/*.py scripts/*.py lib/*.py`、
   `bash -n` 三个 rerun、全仓 grep 确认无残留旧名。
3. 零 LLM 冒烟：重跑 `s12c_pairwise.py` 并与现有 `s12Lc_pairwise.jsonl`
   逐题一致；`s00_seed` 往返与 `seed.jsonl` diff 一致；`check_before_run.py` 通过。
4. 一个提交（`refactor: 命名归一 + 结构整理`），不混业务改动。

**不做的**：`src/` 包化（repo-root import 是零安装设计，包化要动全部 import 且
开发机要 pip install，收益为负）；重排步骤编号（`sNN` 与 PLAN.md 步骤位一一对应，
是特性不是缺陷）；追改历史报告里的旧名。
