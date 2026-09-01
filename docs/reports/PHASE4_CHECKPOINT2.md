# Phase 4 检查点 2：新 rubric vs 草稿 rubric 的 pairwise 一致率（放行闸门）

> 状态：**✅ 通过（2026-08-17）**
> 一键复现：`bash scripts/rerun_checkpoint2.sh`（草稿判分 s12Lb → pairwise 对比 s12Lc）

## 1. 检查点定义（PLAN.md §Phase 4 检查点 2 原文）

> **端到端**：用新 rubric 和草稿 rubric 分别给「gpt55 vs 弱档」打分，
> 比 pairwise 一致率。新 rubric 不超过草稿就不上线

落地口径：

- **样本对** = 每题 strong vs weak 两档，388 道双回复题（Phase 4 实测全集）
- **新 rubric 分数** = 实际交付的那一版（`_s11Le.chosen_round` 选中轮次）在实测里的
  `judged[strong|weak].rate`（含 veto 两票制后的最终得分率，交付语义）
- **草稿 rubric 分数** = seed 的 `draft_rubric`（xlsx F 列），由
  `s12Lb_draft_judge.py` 用**同一判分器、同一套 SYS 纪律**新判（776 次判分调用）。
  草稿没有 is_gate / is_veto / severity；判分分值 = score × weight
  （与 baseline.json 的满分口径 Σscore×weight 一致，负项 score<0）
- **判分器** = cn-judge (family=deepseek) ≠ 生成器 by-gen (family=openai)（硬约束第 2 条）
- **剔除**（测量受限，双方同口径对待）：
  - `_s11Le.skipped` 32 题（strong 档答错/答偏题等，参照系坏了不给结论）
  - judge_incomplete 4 题（q0062/q0098/q0263/q0327，草稿判分器漏返回部分准则）
  - 缺判分 1 题（q0329，新 rubric 侧缺 weak 档判分）
  - 可测对 **n = 351**

## 2. 判据（放行闸门）

1. 判别率（strong > weak 占比）：新 ≥ 草稿
2. 反转率（weak > strong 占比）：新 ≤ 草稿

## 3. 结果

| 指标 | 新 rubric | 草稿 rubric |
|---|---|---|
| 判别率 | **93.2%**（327/351） | 77.2%（271/351） |
| 平局 | 4.6%（16） | 19.9%（70） |
| 反转 | **2.3%**（8） | 2.8%（10） |
| 平均分差（strong−weak） | **51.0pp** | 39.4pp |

- 两 rubric 排序一致率：79.2%（277/351）
- 判据 1：93.2% ≥ 77.2% → ✓（+16.0pp）
- 判据 2：2.3% ≤ 2.8% → ✓（−0.5pp）
- **✅ 检查点 2 通过，新 rubric 可上线/可交付**

口径敏感性：raw_rate（不含 veto 归零）与 rate 逐对排序完全一致（0 对翻转）——
gate 集合里 85 题出现过 0 分档，但 veto 归零全部落在本就更低的那一档，
判别优势不是 veto 单点贡献。

## 4. 逐题退步审计（9 道新 rubric 劣于草稿）

| rid | 新 rubric | 草稿 rubric | 归属 |
|---|---|---|---|
| q0020 | rev 强22% vs 弱78% | win 强100% vs 弱0% | 2-循环题（处置 60%→0%→60%→0% 型） |
| q0047 | tie 0% vs 0% | win 强100% vs 弱62% | floor（锚可达性 80%，rubric 好、strong 侧坏） |
| q0050 | rev 强−18% vs 弱82% | win 强−33% vs 弱−39% | floor；草稿判 strong 更差，其「win」是 weak −39% 更惨的假胜利 |
| q0253 | tie 强100% vs 弱100% | win 强100% vs 弱58% | LowSignal 弱档不弱 |
| q0274 | tie 强100% vs 弱100% | win 强100% vs 弱37% | LowSignal 弱档不弱 |
| q0420 | tie 强90% vs 弱90% | win 强100% vs 弱91% | LowSignal 边缘（弱 91% 仅 9pp 差） |
| q0430 | rev 强70% vs 弱80% | win 强100% vs 弱83% | 弱档偏高 |
| q0435 | rev 强60% vs 弱80% | tie 强100% vs 弱100% | 弱档偏高 |
| q0443 | tie 强20% vs 弱20% | win 强0% vs 弱−50% | floor；草稿判 strong 0 分，其「win」是 weak −50% 更惨的假胜利 |

审计结论：

- 9 道**全部**落在 Phase 4 已登记的残留缺陷族（floor 13 / LowSignal 42 / 2-循环），
  不是新缺陷类型；新 rubric 在这些题上如实反映「两档都答不好/都答得一样好」。
- 草稿在这些题上的「判别优势」几乎全部来自 −8「是否出现事实性错误」大负项
  **对 weak 的惩罚**（q0050/q0443 草稿判 strong 比新 rubric 还低），
  而不是真的把 strong 判对了。
- 反转题交集：q0071/q0235/q0242/q0388 两 rubric 都反转（strong 侧本身有问题）。
- 反向收益：草稿另有 6 道反转（q0076/q0100/q0133/q0170/q0214/q0414）
  是新 rubric 修掉的。
- 这 9 道进入步骤 13（badcase 聚合）的输入队列。

## 5. 测量工具说明（本次新增/修复）

- 草稿判分 776 任务：首跑 768 成功、8 失败（3 道 JSON 解析重试耗尽、
  1 道 obj 非 dict 触发 AttributeError——已修：非 dict 输出按空处理、
  解析失败整档标 judge_incomplete 而不是崩任务；其余为瞬时网络错误）。
  修复后全量重跑（768 命中缓存），漏返回 7 处 → 4 题剔除。
- 判分器端点：config 第一个 judge 角色 by-judge（35.220.164.252）自
  2026-08-17 起持续 401，本检查点固定 `RP_M_JUDGE=cn-judge`（runner 已写死）。

## 6. 结论

判分侧证据链至此闭合：Phase 4 实测（无缺陷 66.2% → 73.2%，无一题退步）
+ 检查点 2（端到端 pairwise 判别 93.2% vs 草稿 77.2%，反转率更低）。
**新 rubric 在放行判据上全面超过草稿，可交付。**
