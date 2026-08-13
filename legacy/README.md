# legacy/ —— 已归档的流程分支

2026-08-13 整理。这里的代码**不再参与交付**，但保留可运行状态，需要复现历史结果时能直接跑。

活跃的 lean 主线在 `stages/`：
`s00_seed → s01_filter → s02_context → s02_5_route → s03_perspective(RP_RET=lean) → s04L_rubric → s11L_diagnose → s11Lb_remedy`

## full_path/ —— 旧全量线（被 s04L 取代）

原来的「逐视角展开」路径：`s03b_merge → s04_criteria → s07_difficulty → s08_penalties → s09_normalize → s11_diagnose → s11b_remedy`，
外加 `s03c_dimension`（维度收敛）和 `s04b_core`（核心筛选）。

废弃原因：每一环都在做加法，准则膨胀到 30.5 条/题（p50=33，max=82），
RIFT non-atomic 命中 80.8%。`s04L_rubric.py` 用「全题预算制」取代逐视角展开后，
收敛到 5.4 条/题，这条线整体不再需要。

`s09_normalize.py` 另有一层原因：导师 2026-08-13 明确 score 直接当权重用，
归一化延后到判分阶段做，这一步确定性作废。

## phase3/ —— 多模型聚合支线

`s06a_alt_context / s06c_alt_perspective / s06d_alt_criteria / s06_aggregate`。
用异质模型跑第二遍 context/perspective/criteria 再取并集，产出没有进入交付版本。
设计依据见 `docs/design/rubric_pipeline_full_v2.md` 步骤 6。

## phase4/ —— 等 Phase 4 启动时取回

`s05_grounding`（回复锚定，硬约束第 1 条）和 `s05b_anchor`（锚点集构建，硬约束第 3 条）。
不是废弃，是还没接进 lean 线。做步骤 10/12/13/14 时把这两个移回 `stages/`。

## run_pipeline.py

只驱动 full_path/ 那条线（断点续跑 + 自动重试 + 状态持久化）。
stage 脚本路径已改为在 `legacy/full_path/` 和 `stages/` 两处查找，仍可运行。
lean 主线的驱动是 `scripts/rerun_lean_fixed.sh`。
