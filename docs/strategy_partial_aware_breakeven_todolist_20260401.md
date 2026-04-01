## MACD V2 Partial-Aware Breakeven Todo

Date: 2026-04-01

### Goal

Continue close-risk optimization after `tp1_before_stop` without changing live semantics.

Primary hypothesis:

- global `breakeven_trigger` tuning has plateaued at `0.012`
- same-bar `TP1-before-stop` fixed the clearest settlement artifact
- the next worthwhile close-path refinement is **state-aware breakeven**
- positions that have not realized any partial TP should require a later BE trigger
- runner legs that already realized TP1 should keep the current BE behavior

### This Round

1. Keep production unchanged
2. Build a **backtest-only** candidate on top of current production:
   - preserve `fund_flow.backtest.same_bar_tp_priority_mode = "tp1_before_stop"`
   - add `partial_aware_breakeven_enabled = true`
   - add `partial_aware_no_partial_trigger_pnl_ratio = 0.015`
3. Do not change live execution semantics
4. Add unit tests for:
   - delayed BE before any partial
   - base BE restored after TP1 has already been filled
5. Run a fresh 30-day comparison

### Candidate Design

- Scope: backtest only
- Behavior:
  - if a position has TP levels and **no partial level has been filled yet**
  - use `max(base_trigger, no_partial_trigger_override)` as the effective BE trigger
  - once any TP level is filled, revert to the normal BE trigger immediately

### Acceptance Metrics

1. unique trade PnL should improve versus current production baseline
2. small positive stop rows should not increase materially
3. no significant MDD deterioration
4. gains should not come only from leg-count inflation
5. tests must lock the state-aware BE semantics

### Result

- implemented as a backtest-only candidate
- preserves `tp1_before_stop`
- adds `partial_aware_breakeven_enabled = true`
- adds `partial_aware_no_partial_trigger_pnl_ratio = 0.015`
- unit tests passed
- 30-day candidate result matched the existing `tp1_before_stop` candidate exactly
- conclusion: under the current sample, the next binding issue is still same-bar settlement ordering; partial-aware BE adds no incremental edge after TP1-before-stop is already active

### Non-Goals

- no production sync in this round
- no new live priority semantics
- no global trailing change
- no pocket-specific BE yet
