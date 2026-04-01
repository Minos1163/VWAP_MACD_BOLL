# MACD V2 Production Review — Candidate C Sync

Date: 2026-04-01

## Purpose

This document records the promotion of candidate C into the current production config and summarizes:

- what changed
- what did not change
- why candidate C was selected
- the latest 30-day backtest evidence tied to this production baseline

## Production Status

Current production config:
- [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)

Promoted source:
- [trading_config_fund_flow_e2_followup_c_short_pressure_gate_shrink_reduce_size022.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_e2_followup_c_short_pressure_gate_shrink_reduce_size022.json)

Sync check:
- production and candidate C were re-read after copy
- exact result: `MATCH True`

## What Changed

This sync is not a full strategy rewrite. It is an E2 follow-up production promotion built on top of the existing `long_dual_support` structural repair.

### 1. Position sizing

Changed:
- `fund_flow.default_target_portion: 0.18 -> 0.22`

Unchanged:
- `fund_flow.max_active_symbols = 2`
- `fund_flow.max_symbol_position_portion = 0.25`
- `fund_flow.min_leverage / default_leverage / max_leverage = 2 / 2 / 2`

Interpretation:
- this is a controlled increase in per-trade capital allocation
- it does not increase concurrency
- it does not increase leverage

### 2. Pocket-level entry filtering

Added production pocket gate:
- `green_bar_growing|short_dual_pressure`

Current production override:

```json
{
  "label": "sdp_a_strict_gate",
  "min_signal_score": 0.88,
  "min_vwap_score": 0.18,
  "require_cvd_ok": true,
  "require_strict_1h_confirmation": true
}
```

Intent:
- reduce weak short-dual-pressure entries
- require higher structural quality
- require stronger 1H confirmation
- require CVD flow confirmation

### 3. 4H shrink loss mitigation

Added production config:

```json
{
  "shrink_exit_loss_mitigation_enabled": true,
  "shrink_exit_loss_mitigation_pnl_threshold": -0.005,
  "shrink_exit_loss_mitigation_exit_ratio": 0.6,
  "shrink_exit_loss_mitigation_ignore_if_pnl_gt": 0.01
}
```

Intent:
- when a position is already losing and the 4H shrink exit condition appears,
  do not force a full close immediately
- reduce `60%` first
- keep the remaining portion alive only when structurally justified

This sits on top of the existing shrink framework and does not alter the global stop-loss or take-profit model.

## What Did Not Change

The following core production mechanics were intentionally left unchanged:

- global `stop_loss_pct = 0.012`
- global `take_profit_pct = 0.04`
- `take_profit_pct_levels = [0.008, 0.012, 0.02]`
- `take_profit_reduce_pct_levels = [0.25, 0.3, 0.2]`
- `breakeven_enabled = true`
- `breakeven_trigger_pnl_ratio = 0.008`
- `breakeven_lock_ratio = 0.0025`
- all E2 `red_bar_growing|long_dual_support` structural repair logic
- overall symbol universe
- concurrency cap
- leverage cap

This is important because candidate C is meant to be a measured production extension, not a reset of the payoff structure.

## Backtest Evidence

### Previous production baseline

Source:
- [v2_summary_20260401_baseline.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_baseline.json)
- [v2_trades_20260401_baseline.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_baseline.csv)

Window:
- `2026-03-02 00:00:00` to `2026-04-01 23:59:59`

Metrics:
- `438 trades`
- `84.02%` win rate
- `+12.31%` return
- `PF 1.76`
- `MDD 2.34%`

Pocket notes:
- `green_bar_growing|short_dual_pressure`: `192 trades`, `+315.97`, `avg_loss -36.98`
- `4h_shrink_exit`: `7 trades`, `-302.73`

### Candidate C / current production target baseline

Source:
- [v2_summary_20260401_cand_c.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_cand_c.json)
- [v2_trades_20260401_cand_c.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_cand_c.csv)
- [v2_equity_curve_20260401_cand_c.csv](D:\AIDCA\AI8\output\backtest\v2_equity_curve_20260401_cand_c.csv)

Window:
- `2026-03-02 00:00:00` to `2026-04-01 23:59:59`

Metrics:
- `401 trades`
- `85.54%` win rate
- `+21.62%` return
- `PF 3.12`
- `MDD 1.70%`

Signal breakdown:
- `green_bar_growing`: `150 trades`, `+890.21`
- `flip_bullish`: `76 trades`, `+786.10`
- `red_bar_growing`: `173 trades`, `+775.77`

VWAP-state breakdown:
- `long_reclaim_confirmed`: `212 trades`, `+1694.78`
- `short_retest_reject`: `144 trades`, `+1025.64`
- `long_dual_support`: `5 trades`, `-23.73`
- `short_dual_pressure`: `5 trades`, `-214.15`

### Interpreting the result

Candidate C improves headline performance versus the prior production baseline:

- return: `+12.31% -> +21.62%`
- win rate: `84.02% -> 85.54%`
- profit factor: `1.76 -> 3.12`
- drawdown: `2.34% -> 1.70%`

The improvement comes from two layers:

1. structural alpha improvement inherited from candidate A/B
   - weak `short_dual_pressure` flow is heavily suppressed
   - 4H shrink losses are reduced by moving to partial reduce behavior

2. controlled size increase
   - `default_target_portion` is increased from `0.18` to `0.22`

So candidate C is not “pure alpha only”; it is:
- alpha cleanup
- exit leak mitigation
- moderate capital intensity increase

## Why Candidate C Was Promoted

Candidate B was the cleaner quality-improvement baseline.

Source:
- [v2_summary_20260401_cand_b.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_cand_b.json)

Candidate B metrics:
- `324 trades`
- `86.42%` win rate
- `+18.06%` return
- `PF 3.62`
- `MDD 1.15%`

Candidate C was promoted instead because the chosen operating preference was:
- keep the structural improvements from A/B
- accept a moderate sizing increase
- target higher absolute monthly return while staying under low drawdown

That tradeoff is explicit and should be treated as intentional.

## Risk Notes

Even after promotion, a few points remain important:

- `short_dual_pressure` is not fully “solved”; in the candidate C sample it still exists as a tiny but negative bucket
- `long_dual_support` remains largely repaired relative to pre-E2, but it is no longer the current dominant issue
- candidate C still relies on backtest semantics for:
  - trailing
  - intrabar assumptions
  - same-bar stop-vs-TP inference

Those areas already have audit infrastructure, but they are not fully proven live-equivalent.

## Recommended Next Review Questions

If this document is sent for review, the most useful review questions are:

1. Is `default_target_portion = 0.22` the right production step, or should B remain the cleaner baseline?
2. Is the `green_bar_growing|short_dual_pressure` pocket gate logically correct, or too aggressive?
3. Is `4h_shrink_reduce` the right live behavior for losing positions, or should the reduce ratio differ by pocket?
4. Should candidate C be considered a new baseline, or a higher-risk overlay on top of B?
5. What should be the next optimization target after this promotion: residual `short_dual_pressure`, `long_dual_support`, or exit-semantics convergence?

## File Set

Production:
- [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)

Promoted source:
- [trading_config_fund_flow_e2_followup_c_short_pressure_gate_shrink_reduce_size022.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_e2_followup_c_short_pressure_gate_shrink_reduce_size022.json)

Comparison baselines:
- [v2_summary_20260401_baseline.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_baseline.json)
- [v2_summary_20260401_cand_b.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_cand_b.json)
- [v2_summary_20260401_cand_c.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_cand_c.json)
