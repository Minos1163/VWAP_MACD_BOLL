# MACD V2 — Runner-Only Trailing Candidate Review

Date: 2026-04-01

## Status

This candidate has now been synced into:

- [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)
- [trading_config_fund_flow_live_production.json](D:\AIDCA\AI8\config\trading_config_fund_flow_live_production.json)

Important caveat:

> the new uplift still comes from a **backtest-only settlement / close-path model upgrade**, not from a live-verified semantic change.

In other words:

- production config now includes the candidate fields
- but the new logic lives in the backtest engine
- so this should still be reviewed as a close-path modeling upgrade, not as a proven live improvement

## Why This Candidate Exists

The recent close-risk sequence produced three stages:

1. **Global breakeven delay**
   - moving `breakeven_trigger_pnl_ratio` from `0.008` to `0.012`
   - improved results materially
2. **same-bar TP1-before-stop**
   - fixed the strongest remaining settlement artifact
   - materially improved unique-entry economics
3. **partial-aware breakeven**
   - tested next
   - added **zero incremental value** on top of `tp1_before_stop`

That led to the next hypothesis:

> trailing should not act as an early whole-position protector; it should only protect **runner inventory after TP1 has already been realized**.

## Current Production Base Before This Candidate

The relevant production baseline before this round was the `tp1_before_stop` candidate:

- [v2_summary_20260401_130428.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_130428.json)
- [v2_trades_20260401_130428.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_130428.csv)

Headline result:

- 830 trade legs
- leg win rate 89.88%
- return +168.52%
- PF 3.25
- MDD 3.77%

Unique-entry recomputation:

- 332 unique entries
- unique total pnl = 18395.05
- unique median pnl = 63.46
- unique win rate = 80.12%
- unique small positive trades = 23
- unique non-positive trades = 66

## New Candidate Design

Candidate config:

- [trading_config_fund_flow_runner_only_trailing.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_runner_only_trailing.json)

Core backtest-only fields:

- `fund_flow.backtest.same_bar_tp_priority_mode = "tp1_before_stop"`
- `fund_flow.backtest.runner_only_trailing_enabled = true`
- `fund_flow.backtest.runner_only_trailing_min_completed_levels = 1`

Mechanism:

- trailing is blocked before any partial TP has been filled
- once TP1 is completed, trailing can activate normally for the remaining position

This is intended to preserve early trend formation while still protecting the residual runner.

## Code Path

Implementation file:

- [backtest_macd_v2.py](D:\AIDCA\AI8\scripts\backtest_macd_v2.py)

Main additions:

1. config fields in `BacktestConfig`
2. `_runner_only_trailing_ready(pos)` helper
3. gating trailing activation inside `check_stops()`

Tests:

- [test_backtest_profit_depth.py](D:\AIDCA\AI8\tests\test_backtest_profit_depth.py)

Added test cases:

1. trailing blocked before any partial TP
2. trailing allowed after TP1 is already filled

Verification:

- `python -m pytest tests/test_backtest_profit_depth.py tests/test_fund_flow_decision_engine.py tests/test_macd_strategy_v2_4h_scoring.py -q`
- result: `80 passed`

## 30-Day Backtest Result

Runner-only trailing candidate:

- [v2_summary_20260401_134319.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_134319.json)
- [v2_trades_20260401_134319.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_134319.csv)

Headline result:

- 902 trade legs
- leg win rate 90.9%
- return +217.84%
- PF 3.75
- MDD 3.78%

### Headline Comparison vs `tp1_before_stop`

`tp1_before_stop`:

- 830 trade legs
- 89.88% win rate
- +168.52%
- PF 3.25
- MDD 3.77%

`runner_only_trailing`:

- 902 trade legs
- 90.9% win rate
- +217.84%
- PF 3.75
- MDD 3.78%

This is a very large uplift with almost no drawdown penalty at the summary level.

## Unique-Entry Recalculation

To reduce the risk of “leg-count inflation” bias, results were recomputed by unique entry key:

- `symbol + side + entry_time`

### Unique-entry comparison

`tp1_before_stop`:

- unique entries: 332
- unique total pnl: 18395.05
- unique median pnl: 63.46
- unique small positive trades: 23
- unique non-positive trades: 66

`runner_only_trailing`:

- unique entries: 324
- unique total pnl: 23462.61
- unique median pnl: 77.84
- unique small positive trades: 16
- unique non-positive trades: 65

Interpretation:

- the uplift is **not just leg-count inflation**
- unique trade economics also improved materially
- remaining “tiny positive” unique trades got cleaner

## Pocket-Level Reading

The main productive pockets thickened further:

- `long_reclaim_confirmed`: +14726.35
- `short_retest_reject`: +9911.33

The main residual negative pocket remains:

- `short_dual_pressure`: 7 trades, 43% win rate, pnl `-1306.80`

This means the new candidate seems to improve **good alpha pockets** rather than just suppress bad pockets further.

## What Changed Mechanically

The previous logic allowed trailing to participate as soon as its activation conditions were met, even before any partial realization had occurred.

The new logic changes that interpretation:

- before TP1: no trailing runner protection
- after TP1: trailing becomes active and only protects the remaining runner

This is conceptually cleaner because trailing is now treated as a **runner-management tool**, not as an early whole-position exit tool.

## Risks / Caveats

This candidate still carries a major boundary:

1. It is still **backtest-only**
2. It compounds the prior `tp1_before_stop` close-path upgrade
3. The uplift may still contain some modeling optimism because live and backtest close semantics remain not fully proven equivalent

So while the result is strong, it should not yet be read as:

> “live production has been proven to improve by +49 percentage points of return”

Instead it should be read as:

> “the current backtest close-path model suggests runner-only trailing is the next most promising close-risk upgrade after `tp1_before_stop`.”

## Questions for Claude

Please review with emphasis on mechanism and model risk:

1. Do you agree that `runner-only trailing` is a cleaner trailing semantics than allowing trailing before any partial TP has been realized?
2. Does this candidate appear to improve genuine runner capture, or could the uplift still be mostly a modeling artifact compounded on top of `tp1_before_stop`?
3. Given the unique-entry recomputation, do you agree that the gain is not merely leg-count inflation?
4. Is it methodologically acceptable that this candidate was synced into production config while still being a backtest-only semantic upgrade, as long as the live caveat is explicit?
5. Would you prioritize next:
   - formalizing `runner-only trailing`
   - auditing `short_dual_pressure`
   - deeper same-bar TP2/TP3 experiments
   and in what order?
6. Do you think this candidate should remain backtest-only until a stronger live/backtest close-path audit exists?
