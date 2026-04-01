## MACD V2 short_dual_pressure Structural Gate Todo

Date: 2026-04-01

### Goal

Continue optimization after the latest Claude-style review:

- keep `runner-only trailing` as the active backtest close-path candidate
- address the remaining true negative pocket:
  - `short_dual_pressure`

### Diagnostic Basis

On the latest runner-only candidate:

- `short_dual_pressure`: 7 rows, 43% win rate, pnl `-1306.80`
- all 7 rows come from `red_bar_growing`
- the losing rows show:
  - weak VWAP support for short entries (`0.12 ~ 0.1528`)
  - inactive CVD fields in the trade log
  - no evidence that the current green-pocket gate is touching the real bad pocket

### Working Hypothesis

The current structural gate is aimed at:

- `green_bar_growing|short_dual_pressure`

but the real residual bad pocket is:

- `red_bar_growing|short_dual_pressure`

So the next correct structural repair is to add a dedicated pocket gate for the red-bar short-dual-pressure pocket.

### This Round

1. Add a direction-aware strict 1H pocket check:
   - support `strict_1h_direction = bearish`
2. Add a backtest candidate on top of current production:
   - `red_bar_growing|short_dual_pressure`
   - `strict_1h_direction = bearish`
   - `allow_neutral_1h_confirmation = false`
   - `require_strict_1h_confirmation = true`
   - `min_signal_score = 0.88`
   - `min_vwap_score = 0.155`
   - `require_cvd_ok = true`
   - `require_cvd_momentum_ok = true`
3. Run a fresh 30-day comparison against current production

### Acceptance Metrics

1. `short_dual_pressure` pnl should materially improve
2. overall return / PF should improve or hold
3. MDD should not materially worsen
4. major productive pockets should remain intact

### Non-Goals

- no symbol blacklist
- no production sync in this round
- no global threshold change
- no TP2/TP3 same-bar extension

### Result

- implemented a direction-aware pocket gate extension:
  - `strict_1h_direction = bearish`
- added candidate:
  - `red_bar_growing|short_dual_pressure`
  - `allow_neutral_1h_confirmation = false`
  - `require_strict_1h_confirmation = true`
  - `strict_1h_direction = bearish`
  - `min_signal_score = 0.88`
  - `min_vwap_score = 0.155`
  - `require_cvd_ok = true`
  - `require_cvd_momentum_ok = true`
- 30-day result versus runner-only baseline:
  - rows: 902 -> 897
  - return: +217.84% -> +236.02%
  - PF: 3.75 -> 4.52
  - MDD: 3.78% -> 3.78%
- unique-entry result:
  - unique count: 324 -> 321
  - unique total pnl: 23462.61 -> 25261.97
  - unique median pnl: 77.84 -> 78.52
  - unique non-positive trades: 65 -> 63
- `short_dual_pressure`:
  - rows: 7 -> 0
  - pnl: -1306.80 -> 0.0
- interpretation:
  - this round appears to remove a true negative residual pocket
  - the gain looks cleaner than further close-path-only tuning
