# MACD V2 — Partial-Aware Breakeven Zero-Increment Review

Date: 2026-04-01

## Purpose

This note summarizes a negative but important result:

- after implementing `tp1_before_stop` as a backtest-only settlement upgrade
- adding a second backtest-only refinement, `partial-aware breakeven`
- produced **zero incremental benefit** on the current 30-day sample

The goal of this review is to let Claude verify whether this interpretation is correct, and whether the next optimization line should move away from breakeven and toward runner-only trailing.

## Production Baseline

Current production config:

- [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)
- [trading_config_fund_flow_live_production.json](D:\AIDCA\AI8\config\trading_config_fund_flow_live_production.json)

Relevant close-risk fields:

- `breakeven_trigger_pnl_ratio = 0.012`
- `breakeven_lock_ratio = 0.0025`
- `take_profit_pct_levels = [0.008, 0.012, 0.02]`
- `take_profit_reduce_pct_levels = [0.25, 0.3, 0.2]`
- `partial_tp_enabled = true`
- `trailing_stop_enabled = true`

Current 30-day production baseline:

- [v2_summary_20260401_123210.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_123210.json)
- [v2_trades_20260401_123210.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_123210.csv)

Baseline result:

- 652 trade legs
- win rate 87.27%
- return +143.15%
- PF 3.074
- MDD 3.77%

## Prior Finding: Global BE Delay Plateau

We already compared:

- `breakeven_trigger = 0.012`
- `breakeven_trigger = 0.015`

Files:

- [v2_summary_20260401_124450.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_124450.json)
- [v2_trades_20260401_124450.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_124450.csv)

Conclusion:

- `0.015` was effectively flat relative to `0.012`
- therefore further global breakeven delay was not the shortest path

## Prior Finding: same-bar stop-first artifact

We identified a more specific issue:

- 55 small positive stop rows remained
- 17 of them had no partial TP at all
- all 17 crossed TP1 and TP2 on the exit bar

Evidence:

- [same_bar_small_stop_scan_20260401.json](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_20260401.json)
- [same_bar_small_stop_scan_be_012_20260401.csv](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_be_012_20260401.csv)
- [same_bar_small_stop_scan_be_015_20260401.csv](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_be_015_20260401.csv)

That led to the backtest-only candidate:

- `fund_flow.backtest.same_bar_tp_priority_mode = "tp1_before_stop"`

Candidate files:

- [trading_config_fund_flow_same_bar_tp1_before_stop.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_same_bar_tp1_before_stop.json)
- [v2_summary_20260401_130428.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_130428.json)
- [v2_trades_20260401_130428.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_130428.csv)

That candidate improved results materially.

## New Candidate: Partial-Aware Breakeven

After `tp1_before_stop`, the next hypothesis was:

- positions that have not realized any partial TP yet
- should require a later BE trigger
- while positions that already realized TP1
- should keep the normal BE trigger

Design:

- backtest-only
- preserve `same_bar_tp_priority_mode = "tp1_before_stop"`
- add:
  - `partial_aware_breakeven_enabled = true`
  - `partial_aware_no_partial_trigger_pnl_ratio = 0.015`

Candidate config:

- [trading_config_fund_flow_partial_aware_be_after_tp1.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_partial_aware_be_after_tp1.json)

Implementation:

- [backtest_macd_v2.py](D:\AIDCA\AI8\scripts\backtest_macd_v2.py)

Tests:

- [test_backtest_profit_depth.py](D:\AIDCA\AI8\tests\test_backtest_profit_depth.py)

Two explicit tests were added:

1. trailing/BE logic before any partial uses the delayed trigger
2. once TP1 is already filled, the base trigger is restored

## Result

Backtest result:

- [v2_summary_20260401_133420.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_133420.json)
- [v2_trades_20260401_133420.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_133420.csv)

Headline summary:

- 830 trade legs
- win rate 89.88%
- return +168.52%
- PF 3.25
- MDD 3.77%

At first glance this looks strong, but the crucial comparison is against the prior `tp1_before_stop` candidate, not against production baseline.

When directly compared with:

- [v2_trades_20260401_130428.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_130428.csv)

the result is:

- trade rows: identical
- unique-entry economics: identical
- full trade CSV equality: `True`

In other words:

> on the current 30-day sample, `partial-aware breakeven` adds zero incremental benefit beyond `tp1_before_stop`.

## Interpretation

This negative result is useful because it narrows the causal chain:

1. global BE delay improved results up to `0.012`
2. additional global BE delay plateaued
3. same-bar settlement ordering was the next real bottleneck
4. after correcting that bottleneck with `tp1_before_stop`
5. state-aware BE added nothing further

This suggests:

- the remaining edge is no longer in global or state-aware BE timing
- the next higher-value line is likely **runner-only trailing**
- i.e. making trailing act only on residual inventory after TP1 has already been realized

## Questions for Claude

Please review this result with emphasis on mechanism, not just summary metrics:

1. Do you agree that `partial-aware breakeven` showing zero incremental effect means BE is no longer the binding bottleneck after `tp1_before_stop`?
2. Is it methodologically sound to interpret this as evidence that the next close-risk line should move to runner-only trailing rather than more BE tuning?
3. Does the “exact equality” between the `tp1_before_stop` trade log and the `partial-aware breakeven` trade log support the conclusion strongly enough, or is there another plausible explanation?
4. Would you prioritize:
   - runner-only trailing
   - pocket-specific BE
   - deeper same-bar TP2/TP3 experiments
   and in what order?
5. Do you agree that `partial-aware breakeven` should stay as a rejected candidate and not be promoted to production?
