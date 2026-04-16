---
created: 2026-04-16T09:31:24.870Z
title: Close MACD V4 execution loop
area: general
files:
  - src/fund_flow/macd_strategy_v4.py
  - src/fund_flow/decision_engine.py
  - src/app/fund_flow_bot.py
  - scripts/backtest_macd_v4_runner.py
  - scripts/backtest_macd_v2.py
  - scripts/diagnostics/analyze_macd_v4_quadrant_research.py
  - scripts/diagnostics/analyze_macd_v4_lsl_exit_overlay.py
  - src/fund_flow/v4/score_templates.py
  - src/fund_flow/risk_packages.py
---

## Problem

MACD V4 的主矛盾已经不是继续微调 confirmed flip / preflip 分数阈值，而是执行闭环没有打通。当前研究链已经能给出 early/confirmed flip、pending-confirm outcome、LSL overlay zero-trigger diagnosis 等结果，但 runner / execution 侧仍存在三处硬缺口：

1. `pending_confirm` 只会打开 `waiting`，没有形成 `ready_to_add -> confirmed_add / expired / invalidated / cancelled_*` 的完整生命周期，导致研究口径里的 early -> confirmed 样本无法稳定落到真实 add-on。
2. LSL exit overlay 的 candidate families 与 baseline 持仓家族几乎没有交集，zero trigger 的根因在 candidate universe，而不是 evaluator。
3. `neutral_bars` 已经在 runner 结果中影响真实退出，但 live / runner / research 还没有共享同一套稳定状态字段和冷却语义。

策略方向仍然成立：以 MACD + BOLL + RSI 的四象限框架量化区分市场状态，用 RSI 补偿 MACD 的滞后，同时不能因为 RSI 与 MACD 暂时矛盾就抹掉 RSI 的敏捷性。真正要做的是先把执行状态机、样本覆盖和状态语义打通，再讨论 confirmed flip 的 EMA 支撑、preflip penalty/bonus、risk package 等二阶优化。

## Solution

优先按执行闭环顺序推进，而不是继续做解释力很弱的权重微调：

1. 建立 shared pending-confirm state machine。
   - 在 strategy / runner / decision / bot 共用字段上补齐 `pending_confirm_id`、`pending_confirm_status`、deadline、deviation、confirm metadata、cancel reason、block reason。
   - 明确 `ready_to_add` 只是中间态，必须统计 `waiting -> ready_to_add` 通过率与 `ready_to_add -> confirmed_add` 成交率。
   - 解决 `waiting_opened > 0` 但 `ready_to_add = confirmed_add = expired = invalidated = 0` 这类悬空状态。

2. 重构 LSL overlay 的 candidate universe。
   - 将 “哪些 baseline 持仓值得监控” 与 “什么 exit 信号允许触发 selective exit” 解耦。
   - 先让 candidate 覆盖真实 baseline families，再评估 evaluator 阈值是否需要改。
   - 输出 candidate failure / evaluator rejection 的逐层统计，避免继续把 0 trigger 误判成阈值问题。

3. 把 neutral state 升级为稳定共享状态。
   - 在 live / runner / research 中统一 `current_quadrant`、`neutral_bars`、`last_quadrant_bar`、`neutral_exit_last_fire_bar` 等字段。
   - 给 neutral exit 增加冷却，避免单笔仓位在连续 N0 bar 下被切得过碎。

4. 只在 P0 闭环完成后再做二阶结构优化。
   - confirmed flip：重点补 EMA confirm 的解释力，避免长期接近 0 且过度依赖 residual。
   - preflip：保持 label-only，不恢复 veto，但提高 growing family 的 mismatch / shrink warning penalty。
   - risk package：暂不做大改，只允许 confirm add / neutral exit 的保护性补充。

5. 验收必须绑定指标，而不是凭感觉。
   - `waiting_opened > 0` 持续成立。
   - `ready_to_add > 0` 与 `confirmed_add > 0` 必须出现。
   - `expired + invalidated + confirmed_add + cancelled_*` 应接近 `waiting_opened`。
   - `candidate_enabled_count > 0` 必须出现，之后才有资格看 overlay trigger。
   - `ema_cross_decayed_avg` 要摆脱长期近 0，但不能靠阈值放水换 pass count。

优先执行顺序：

1. 先补 runner / bot / decision 的 pending-confirm 终态与统计。
2. 再修 overlay candidate 与 evaluator 解耦。
3. 再固化 neutral state 语义。
4. 最后做 confirmed flip / preflip 的结构再平衡实验。
