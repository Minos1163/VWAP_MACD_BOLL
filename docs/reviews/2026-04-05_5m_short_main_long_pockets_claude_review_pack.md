# 5m Short-Main / Long-Pockets Claude Review Pack

## 1. Scope

- Strategy config under review:
  - `config/candidates/trading_config_fund_flow_review_5m_short_main_long_pockets_20260405.json`
- Verified replay window:
  - Trade window: `2026-03-05T00:00:00` to `2026-04-04T00:00:00`
  - Data warmup window: `2026-03-04 00:00:00` to `2026-04-04T00:00:00`
- Verified replay artifacts:
  - `output/backtest/bot_like_summary_20260405_233325.json`
  - `output/backtest/bot_like_trades_20260405_233325.csv`
  - `output/backtest/bot_like_candidate_ledger_20260405_233325.csv`
  - `output/backtest/bot_like_ai_advice_20260405_233325.csv`

This document is for external strategy review. It focuses on:

- what the 30-day result is actually coming from
- the live opening chain and gating path
- current score thresholds, weights, leverage, position sizing, and risk controls
- the gap between current state and target state

## 2. Verified 30-Day Result

### 2.1 Headline metrics

- 30d return: `+12.18%`
- Win rate: `91.18%`
- Profit factor: `4.26`
- Max drawdown: `2.77%`
- Trade rows: `68`
- Open candidates seen: `33`
- Final opened entries: `24`
- Capacity blocked: `0`

### 2.2 Important interpretation

The result is not a high-frequency engine yet.

- The strategy is currently high win rate, low turnover, low drawdown.
- The real open count is `24`, not `68`.
- `68` is inflated by partial take-profit and staged exits.
- The current engine is far away from the target `300-500` opens / 30d.

### 2.3 Pocket attribution

PnL is concentrated in a small number of pockets:

| side | signal_type_1h | vwap_state | trade_rows | pnl | win_rate |
|---|---|---:|---:|---:|---:|
| long | flip_bullish | long_reclaim_confirmed | 30 | 717.74 | 90.0% |
| long | green_bar_growing | long_dual_support | 1 | 6.52 | 100.0% |
| short | green_bar_growing | short_retest_reject | 27 | 463.74 | 92.59% |
| short | green_bar_growing | short_below_session_above_structure | 8 | 229.87 | 100.0% |
| short | red_bar_growing | short_retest_reject | 2 | -98.24 | 50.0% |

Takeaway:

- Long PnL is almost entirely `flip_bullish|long_reclaim_confirmed`.
- Short PnL is mostly `green_bar_growing` variants.
- `red_bar_growing|short_retest_reject` is currently weak and likely a drag pocket.

### 2.4 Symbol attribution

Top PnL symbols:

| symbol | trade_rows | pnl | win_rate |
|---|---:|---:|---:|
| PUMPUSDT | 13 | 320.84 | 92.31% |
| ZROUSDT | 13 | 284.93 | 92.31% |
| AVAXUSDT | 7 | 218.13 | 100.0% |
| XLMUSDT | 5 | 216.66 | 100.0% |
| TRUMPUSDT | 3 | 139.87 | 100.0% |

Weak symbol:

| symbol | trade_rows | pnl | win_rate |
|---|---:|---:|---:|
| TONUSDT | 5 | -127.98 | 80.0% |

### 2.5 Exit attribution

Positive PnL is dominated by staged take profit:

- `take_profit_level_intrabar`: `41` rows, `+1025.04`
- `decision_close:partial_tp_level_1`: `9` rows, `+504.87`
- `take_profit_intrabar`: `2` rows, `+96.22`

Main negative rows:

- `stop_loss_intrabar`: `7` rows, `-246.77`
- `stop_loss_intrabar_after_tp1_same_bar`: `3` rows, `-47.76`
- `time_exit pnl=-0.0051`: `1` row, `-66.61`

Critical caveat:

- Backtest still uses `same_bar_tp_priority_mode = tp1_before_stop`.
- This is more optimistic than `stop_first`.
- Current `+12.18%` is still likely somewhat flattering.

## 3. Funnel Attribution

### 3.1 Replay funnel

From `output/backtest/bot_like_summary_20260405_233325.json`:

- `analysis_attempts = 321444`
- `analysis_ready = 312804`
- `decision_counts = hold 310937 / sell 87 / buy 37 / close 15`

Signal funnel:

| stage | passed | blocked | pass_rate | note |
|---|---:|---:|---:|---|
| raw signal | 311076 | 0 | 100.00% | raw engine outputs |
| score threshold | 16731 | 294345 | 5.38% | main compression |
| vwap threshold | 299028 | 12048 | 96.13% | hard VWAP overextension veto |
| 4h preflip shrink | 256738 | 54338 | 82.53% | shrink gating |
| pocket entry override | 217 | 0 | 100.00% | override path only logs |
| L1 structural | 588 | 4095 | 12.56% | strongest post-score bottleneck |
| L2 flow | 4658 | 25 | 99.47% | current flow filters mostly pass |
| L3 micro | 4337 | 346 | 92.61% | microstructure removes edge cases |
| pretrade gate | 124 | 3 | 97.64% | low impact currently |
| AI review | 33 | 0 | 100.00% | no effective block in this replay |
| capacity | 33 | 0 | 100.00% | capacity not binding |
| final fill | 24 | 9 | 72.73% | blocked by `target_portion_below_min_open` |

### 3.2 Real bottlenecks

Current bottlenecks are:

1. Score threshold compression
2. L1 structural gate
3. Final fill collapse from `target_portion_below_min_open`

Current non-bottlenecks are:

1. Capacity
2. AI review
3. Pretrade hard gate

## 4. Live Opening Chain

This is the actual opening path that matters for production behavior.

### 4.1 Bot outer loop

Entry path starts in `src/app/fund_flow_bot.py`:

1. Materialize market snapshot and flow context.
2. Apply volatility cooldown and conflict cooldown.
3. Apply trigger dedupe.
4. Build portfolio snapshot.
5. Apply entry-window gating.
6. Call `FundFlowDecisionEngine.decide(...)`.
7. Apply signal pool.
8. Apply MA10/MACD entry filter.
9. Apply pretrade risk gate.
10. Queue candidate if no live position exists.
11. In `_finalize_entries`, do AI review, capacity check, and final execution.

### 4.2 Decision engine

Core decision comes from `src/fund_flow/decision_engine.py` and `src/fund_flow/macd_strategy_v2.py`.

For `strategy_mode = macd_mtf_strategy_v2`, the engine builds a signal using:

1. 1H MACD direction state
2. 4H MACD regime and shrink context
3. BOLL structure
4. VWAP state and location score
5. 15m entry confirmation / soft confirmation
6. strict filters by signal family
7. whitelist block for long entries
8. leverage and position sizing
9. stop-loss / TP plan

### 4.3 Candidate selection and execution

In the bot finalization stage:

1. Close candidates are prioritized.
2. Open candidates are score-sorted.
3. Flat-candidate AI shortlist is computed.
4. AI final review can veto or log.
5. Capacity check blocks new entries if active symbols exceed limit.
6. Final execution uses IOC-style price constraints.

## 5. Current Signal Model

### 5.1 Timeframes

- decision timeframe: `5m`
- anchor direction timeframe: `4h`
- confirmation timeframe: `1h`
- entry refine timeframe: `15m`

### 5.2 MACD parameters

- 1H MACD: `12 / 26 / 9`
- 4H MACD: `12 / 26 / 9`
- 15m MACD: `12 / 26 / 9`
- threshold: `0.00005`

### 5.3 Scoring weights

Current scoring weights:

- `weight_1h_direction = 0.40`
- `weight_4h_direction = 0.20`
- `weight_vwap = 0.20`
- `weight_15m_entry = 0.05`
- `weight_volume = 0.15`

Interpretation:

- The engine is still primarily direction + VWAP driven.
- 15m execution timing has very low weight.
- Volume matters, but not enough to create large turnover.

### 5.4 Thresholds

Current entry thresholds:

- default signal threshold: `0.81`
- min entry score: `0.25`
- min signal score hard floor: `0.845`
- red_bar_growing threshold: `0.84`
- flip_bearish threshold: `0.82`
- flip_bullish threshold: `0.84`
- stable_bear_continuation threshold: `0.82`
- stable_bull_continuation threshold: `0.82`

Current effect:

- Thresholds are too selective for a `300-500 opens / 30d` target.
- The replay currently generates only `33` open candidates and `24` fills.

## 6. Current Long and Short Entry Logic

### 6.1 Long side

Current long mode:

- `long_entry_mode = whitelist_only`

Current long allowlist:

- `flip_bullish|long_reclaim_confirmed`
- `green_bar_growing|long_dual_support`

Additional long controls:

- `disable_red_bar_growing_long_entries = true`
- `disable_flip_bullish_trial_entries = true`
- `flip_bullish_min_vwap_score = 0.12`
- `flip_bullish_require_pullback_bounce = true`
- `flip_bullish_require_15m_growing = true`

Interpretation:

- Long side is no longer broad trend-following.
- It is a pocket-allowlisted reclaim model.

### 6.2 Short side

Short side is effectively the main engine.

Observed profitable short pockets:

- `green_bar_growing|short_retest_reject`
- `green_bar_growing|short_below_session_above_structure`

Weak short pocket:

- `red_bar_growing|short_retest_reject`

Important short controls:

- `enable_green_bar_growing_short_adx_1h_range_filter = true`
- `green_bar_growing_short_min_adx_1h = 40`
- `green_bar_growing_short_max_adx_1h = 50`
- `flip_bearish_retest_reject_min_vwap_score = 0.25`

## 7. Position Sizing and Leverage

### 7.1 Current configured portfolio limits

Current runtime limits:

- `default_target_portion = 0.30`
- `max_symbol_position_portion = 0.30`
- `min_open_portion = 0.06`
- `max_active_symbols = 6`
- `reserve_pct = 0.20`

Watchlist throttles:

- watchlist max position portion = `0.18`
- watchlist max leverage = `3`

### 7.2 Current leverage reality

User target:

- floating leverage ladder: `2X / 3X / 4X`

Current actual state:

- config is `min/default/max = 5 / 5 / 5`
- replay also ran at fixed `5X`

This is a hard mismatch with the target state.

Even though `calculate_leverage()` contains branching logic, the current config clamps everything to `5X`.

### 7.3 Position sizing modifiers

Current position sizing is modified by:

1. score multiplier
2. `short_dual_pressure` bonus
3. watchlist cap
4. VWAP-score tier multiplier
5. trial entry scale
6. session risk scale
7. VWAP structure override

Current VWAP-score tiers for `long_dual_support`:

- `0.12-0.20 -> 0.75x`
- `0.20-0.30 -> 0.95x`
- `0.30-1.00 -> 1.05x`

## 8. Risk Controls

### 8.1 Entry hard gates

Current hard gates:

- ADX min = `22`
- ATR min = `0.006`
- ATR max = `0.02`
- spread max = `0.0008`
- flow min pass = `2`
- micro min pass = `2`

### 8.2 Pretrade risk gate

Current pretrade gate:

- enabled = `true`
- hard rules only = `true`
- ATR ratio hard block = `3.5`
- equity usage block = `0.85`
- DD exit threshold = `0.10`
- entry block action = `BLOCK`

Observed effect in 30d replay:

- only `3` blocks after pretrade stage
- not a major bottleneck currently

### 8.3 Live protection / SLA

Current live protection logic:

- `protection_sla_enabled = true`
- `protection_sla_seconds = 300`
- `protection_sla_force_flatten = true`
- `protection_sla_pnl_grace_threshold = -0.005`
- API health check before force close = `true`

Interpretation:

- If protection orders are missing for too long, bot can force flatten.
- This improves live safety, but can damage realized expectancy in unstable infrastructure windows.

### 8.4 Exit stack

Current exit stack includes:

- fixed stop loss baseline = `2%`
- fixed TP baseline = `4%`
- TP ladder = `0.8% / 1.2% / 2.0%`
- partial TP enabled
- breakeven enabled
- trailing stop enabled
- time exit enabled
- 4H shrink exits enabled

Current time exit:

- `time_exit_minutes = 30`
- `time_exit_min_profit_pct = 0.0035`

### 8.5 Same-bar modeling caveat

Current backtest setting:

- `same_bar_tp_priority_mode = tp1_before_stop`

This is optimistic.

If same-bar ambiguity is material, current backtest return is likely overstated.

## 9. Gap Versus Strategy Target

Target state requested:

- leverage ladder: `2X / 3X / 4X`
- position per symbol: `20%-30%`
- max active symbols: `5`
- 30d return: `100%+`
- win rate: `80%+`
- 30d opens: `300-500`

Current verified state:

| metric | current | target | verdict |
|---|---:|---:|---|
| leverage | fixed `5X` | `2X/3X/4X` float | not aligned |
| per-symbol position | `30%` cap | `20-30%` | aligned |
| max active symbols | `6` | `5` | not aligned |
| 30d return | `12.18%` | `100%+` | far below target |
| win rate | `91.18%` | `80%+` | above target |
| 30d opens | `24` actual opened | `300-500` | far below target |

Main implication:

- The strategy is currently over-optimized for hit rate and under-optimized for turnover and capital throughput.
- It already clears the win-rate target.
- It misses the return target mainly because opportunity count is too low.

## 10. What Claude Should Audit

Questions for external review:

1. Which component is the real bottleneck for `100%+ / 300-500 opens`:
   - score thresholds
   - L1 structure gate
   - 15m entry softness
   - pocket whitelist width
   - min-open fill collapse

2. Is `flip_bullish|long_reclaim_confirmed` a structurally valid long pocket, or is current PnL still too dependent on same-bar TP optimism?

3. Should `red_bar_growing|short_retest_reject` be removed, downgraded, or separately re-scored?

4. How should the leverage ladder be rebuilt so that live path matches the target `2X / 3X / 4X` instead of fixed `5X`?

5. How should `default_target_portion`, `min_open_portion`, and score-to-portion mapping be changed so the engine does not lose `9/33` candidates at final fill due to `target_portion_below_min_open`?

6. Which exact new pockets, if any, should be added next to raise turnover without collapsing win rate below `80%`?

7. Should same-bar handling be switched to `stop_first` for a conservative audit before any aggressive expansion?

## 11. Reviewer Warning

The current replay should not be interpreted as a path to the target metrics.

It is a conservative, high-win-rate, low-turnover engine with:

- good pocket selectivity
- low drawdown
- low capacity pressure
- very low open count

It is not yet a `100%+ / 300-500 opens / 30d` strategy.

The next work should focus on:

1. removing fake optimism
2. increasing true opportunity count
3. restoring target-aligned leverage
4. fixing final-fill collapse

