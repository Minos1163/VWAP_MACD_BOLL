## MACD V2 Breakeven Gap Review

Date: 2026-04-01

### Scope

- Compare `breakeven_trigger_pnl_ratio = 0.012` vs `0.015`
- Audit remaining "small positive protective stop" cases
- Identify the next highest-value optimization point

### Findings

1. `0.015` does not outperform `0.012` on the current 30-day window.
   - `0.012`: 652 trades / 87.27% WR / +143.15% / PF 3.074 / MDD 3.77%
   - `0.015`: 652 trades / 87.27% WR / +143.12% / PF 3.074 / MDD 3.77%
   - Interpretation:
     - further delaying global breakeven has effectively hit a plateau
     - `0.015` is slightly worse than `0.012`, not better

2. The residual small-win problem is now concentrated in a very specific execution-order artifact.
   - Positive small stop rows (`0 < pnl < 20`, `stop_loss_intrabar*`): 55
   - Of those, rows with no partial TP on the same trade: 17
   - For those 17 rows:
     - `17/17` crossed TP1 (`0.8%`) on the same exit bar
     - `17/17` crossed TP2 (`1.2%`) on the same exit bar
     - `2/17` also crossed TP3 (`2.0%`) on the same exit bar

3. The issue is no longer "breakeven too early" alone.
   - That issue was real and has already been materially improved by moving from `0.008` to `0.012`.
   - The next leak is:
     - stop-first ordering inside `check_stops()`
     - partial TP levels are evaluated only after stop hit is processed

### Impacted Pockets

The 17 no-partial small-stop rows that still crossed TP1/TP2 on the same bar are concentrated in:

- `green_bar_growing | short_retest_reject`: 7
- `red_bar_growing | long_reclaim_confirmed`: 5
- `green_bar_growing | short_below_session_above_structure`: 2
- `red_bar_growing | long_above_session_below_structure`: 2
- `flip_bullish | long_reclaim_confirmed`: 1

These are mostly profitable trend/reclaim pockets, which means the remaining issue is not low-alpha entry contamination. It is close-path sequencing.

### Recommendation

Next optimization priority should be:

1. Audit and test same-bar priority between partial TP and stop protection.
2. Build a candidate that allows TP level fills before stop execution when both are hit on the same bar.
3. Only after that, consider more advanced stateful breakeven such as:
   - partial-aware breakeven
   - pocket-specific breakeven triggers

### Production Decision

- Keep production at `breakeven_trigger_pnl_ratio = 0.012`
- Do not promote `0.015`
- Treat "same-bar TP-before-stop handling" as the next high-value optimization target
