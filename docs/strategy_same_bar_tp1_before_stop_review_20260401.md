## MACD V2 TP1-Before-Stop Candidate Review

Date: 2026-04-01

### Candidate

- Config:
  - [trading_config_fund_flow_same_bar_tp1_before_stop.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_same_bar_tp1_before_stop.json)
- Scope:
  - backtest only
  - `fund_flow.backtest.same_bar_tp_priority_mode = "tp1_before_stop"`

### Baseline vs Candidate

Baseline:
- [v2_summary_20260401_123210.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_123210.json)
- `652` trade legs / `337` unique entries
- `87.27%` leg win rate
- `+143.15%` return
- `PF 3.074`
- `MDD 3.77%`

Candidate:
- [v2_summary_20260401_130428.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_130428.json)
- `830` trade legs / `332` unique entries
- `89.88%` leg win rate
- `+168.52%` return
- `PF 3.25`
- `MDD 3.77%`

### Important Interpretation

Trade legs increased materially because same-bar TP1 is now realized before the remaining position is stopped.

This is not pure leg-count inflation:

- unique entries: `337 -> 332` (essentially flat)
- unique trade win rate: `78.04% -> 80.12%`
- unique trade median pnl: `51.30 -> 63.46`
- unique trade total pnl: `15801.43 -> 18395.05`
- positive unique trades under `20 USDT`: `28 -> 23`
- unique trades `<= 0`: `74 -> 66`

### Same-Bar Artifact Removal

Baseline scan showed:
- `55` small positive stop rows
- `17` no-partial small-stop rows
- `17/17` crossed TP1 and TP2 on the same exit bar

Candidate scan showed:
- `74` small positive stop rows
- `0` no-partial small-stop rows

Interpretation:
- the original same-bar undercount has been removed
- residual positive stop rows are now attached to trades that already realized partial TP
- this is much closer to the intended runner-protection behavior

### New Reason Tags

Candidate introduces:
- `stop_loss_intrabar_after_tp1_same_bar`
- `stop_loss_intrabar_both_hit_after_tp1_same_bar`

These make the changed execution path auditable.

### Current Recommendation

This candidate is strong enough to justify a formal review round.

However, it should still be treated as:
- a backtest settlement-model upgrade
- not a live semantic change

Recommended next step:
1. keep production unchanged
2. submit this candidate for review
3. if approved, compare with a stricter variant only later
