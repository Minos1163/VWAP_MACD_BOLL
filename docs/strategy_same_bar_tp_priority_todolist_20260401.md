## MACD V2 Same-Bar TP Priority Todo

Date: 2026-04-01

### Goal

Continue close-risk optimization without further loosening global protection.

Primary hypothesis:

- global `breakeven_trigger` tuning has entered a plateau at `0.012`
- the next high-value leak is same-bar settlement ordering
- current backtest likely under-realizes some profitable pockets because stop is processed before partial TP

### This Round

1. Keep production unchanged at `breakeven_trigger_pnl_ratio = 0.012`
2. Build a **backtest-only** candidate:
   - `fund_flow.backtest.same_bar_tp_priority_mode = "tp1_before_stop"`
3. Do **not** implement strong `all_levels_before_stop`
4. Add explicit reason tags for auditability
5. Run 30-day comparison against current production

### Why TP1-Only First

- safer than `TP1/TP2/TP3 all before stop`
- directly targets the currently observed artifact
- avoids excessive optimism under 15m OHLC path ambiguity
- preserves current production/live semantics

### Candidate Design

- Mode name: `tp1_before_stop`
- Scope: backtest only
- Behavior:
  - if same bar hits both stop and one or more TP levels
  - realize TP1 first
  - then stop the remaining position
  - keep TP2/TP3 under existing stop-first semantics

### Acceptance Metrics

1. `small_positive_stop_rows_no_partial` should decrease
2. `same_bar_tp1_cross_no_partial_count` should decrease
3. new reason `stop_loss_intrabar_after_tp1_same_bar` should appear with traceable count
4. profitable reclaim/retest pockets should thicken
5. total return / PF should improve without material MDD deterioration

### Follow-up Priority After This Round

1. `partial-aware breakeven`
2. `pocket-specific breakeven trigger`
3. `runner-only trailing`

### Explicit Non-Goals

- no production sync in this round
- no live semantic change
- no global BE trigger change
- no TP2/TP3 priority change yet
