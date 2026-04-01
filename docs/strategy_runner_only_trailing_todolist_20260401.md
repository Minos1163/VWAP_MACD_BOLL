## MACD V2 Runner-Only Trailing Todo

Date: 2026-04-01

### Goal

Continue close-risk optimization after confirming:

- global breakeven delay has plateaued
- `partial-aware breakeven` adds zero incremental value on top of `tp1_before_stop`
- the next higher-value line is to restrict trailing so it only protects runner inventory

### Working Hypothesis

- current trailing may still participate too early in some close paths
- trailing is more defensible as a **runner protection tool**
- the cleanest next candidate is a **backtest-only** mode:
  - trailing disabled until at least TP1 has been realized
  - once TP1 is filled, trailing can protect the remaining position

### This Round

1. Keep production unchanged
2. Write tests first for:
   - trailing blocked before any partial
   - trailing restored after TP1 is filled
3. Implement a backtest-only candidate:
   - `runner_only_trailing_enabled = true`
   - `runner_only_trailing_min_completed_levels = 1`
4. Run a fresh 30-day comparison
5. Compare against both:
   - current production baseline
   - `tp1_before_stop` candidate

### Acceptance Metrics

1. unique trade PnL improves, or at minimum does not deteriorate materially
2. MDD does not worsen materially
3. no new explosion in low-value small-stop rows
4. effect should be explainable as runner protection, not just leg-count inflation

### Non-Goals

- no production sync in this round
- no live semantic change
- no global trailing activation change
- no pocket-specific trailing yet

### Result

- candidate implemented as backtest-only
- preserves `tp1_before_stop`
- adds:
  - `runner_only_trailing_enabled = true`
  - `runner_only_trailing_min_completed_levels = 1`
- 30-day result:
  - 902 trade legs
  - 90.9% leg win rate
  - +217.84% return
  - PF 3.75
  - MDD 3.78%
- unique-entry result versus `tp1_before_stop`:
  - unique entries: 332 -> 324
  - unique total pnl: 18395.05 -> 23462.61
  - unique median pnl: 63.46 -> 77.84
  - unique small positive trades: 23 -> 16
  - unique non-positive trades: 66 -> 65
- interpretation:
  - this candidate appears to improve runner quality materially
  - same-bar no-partial small-stop artifact remains fixed at zero
  - next decision should be whether this remains a backtest-only upper/mid model or deserves a broader review before any production consideration
