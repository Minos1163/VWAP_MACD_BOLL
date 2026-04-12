# DeepSeek Review Packet: Live Open Chain + 30D Backtest Attribution

## 0. Review Goal

This packet is for external review of the current live-config fund-flow stack.

Review focus:

1. Is the current live open chain logically coherent
2. Are the current thresholds / weights internally contradictory
3. Is the 30D backtest loss caused by weak alpha, weak gating, or bad sizing / risk logic
4. How far the current stack is from the target operating envelope

## 1. Target vs Current Reality

### Strategy target

- Leverage: `3x / 4x / 5x`
- Single-symbol position size: `20% - 30%`
- Max concurrent symbols: `5`
- 30D return target: `50%+`
- 30D win rate target: `80%+`
- 30D trade-count target: `30 - 120`

### Current live config

- `min_leverage = 3`
- `default_leverage = 4`
- `max_leverage = 5`
- `default_target_portion = 0.22`
- `max_symbol_position_portion = 0.30`
- `max_active_symbols = 5`

### Current 30D bot-like replay result

Window actually used:

- Trade window: `2026-03-02T00:00:00` to `2026-04-01T00:00:00`
- Data window: `2026-03-01T00:00:00` to `2026-04-01T00:00:00`

Reason for this window:

- Local backtest cache currently covers this range cleanly.
- It does **not** extend to `2026-04-12`, so pretending to run a “latest 30D” through `2026-04-12` would be fake.

Result:

- Return: `-11.00%`
- Win rate: `63.76%`
- Total trades: `516`
- Profit factor: `0.871`
- Max drawdown: `12.21%`

### Gap to target

- Leverage range: matched
- Per-symbol position target: matched on paper
- Max concurrent symbols: matched on paper
- 30D return target `50%+`: **failed hard**
- 30D win rate target `80%+`: **failed**
- 30D trade-count target `30-120`: **failed badly**, current system is over-trading

Bottom line: this stack is not currently aligned with the stated objective.

## 2. 30D Backtest Attribution

### 2.1 Headline finding

The 30D result is **not** a sparse-signal problem.

- `signals_generated = 1050`
- `open_candidates_seen = 580`
- `total_trades = 516`

This stack is trading a lot. The loss comes from **admitted trade quality**, not from “no setup”.

### 2.2 Main loss driver

The main leak is two shrink families:

- `red_bar_shrinking`: `210` trades, `59.05%` win rate, `-1054.42` pnl
- `green_bar_shrinking`: `108` trades, `46.30%` win rate, `-532.77` pnl

Combined:

- `318` trades
- `-1587.19` pnl

That single block is large enough to drown the profitable pockets.

### 2.3 Positive edge still exists

The system still contains real positive sub-edges:

- `green_bar_growing`: `141` trades, `76.60%` win rate, `+797.98`
- `flip_bullish`: `54` trades, `83.33%` win rate, `+238.91`

This means the stack is not uniformly bad. The real problem is **bad mix**, not total edge absence.

### 2.4 Loss asymmetry is fatal

- Average win: `+11.14`
- Average loss: `-22.50`
- Expectancy per trade: `-1.05`

So the system wins often enough to look acceptable superficially, but loss size is roughly 2x win size.

### 2.5 Side attribution

- Long: `158` trades, `53.16%` win rate, `-270.34`
- Short: `358` trades, `68.44%` win rate, `-272.73`

Interpretation:

- Long side is weak.
- Short side is **not healthy either**, because bad short shrink combinations are large and frequent enough to offset the better short continuation setups.

### 2.6 Worst signal / VWAP / side combos

- `red_bar_shrinking | short_dual_pressure | short`: `72` trades, `-548.59`
- `red_bar_shrinking | short_retest_reject | short`: `124` trades, `-302.47`
- `green_bar_shrinking | short_retest_reject | short`: `22` trades, `-296.74`
- `green_bar_shrinking | long_dual_support | long`: `34` trades, `-169.09`
- `red_bar_shrinking | long_reclaim_confirmed | long`: `11` trades, `-145.20`

### 2.7 Best combos

- `green_bar_growing | short_retest_reject | short`: `133` trades, `+822.17`
- `flip_bullish | long_reclaim_confirmed | long`: `54` trades, `+238.91`

These are the cleanest “continue / preserve” pockets.

### 2.8 Worst symbols

- `TAOUSDT`: `21` trades, `-327.35`
- `DOGEUSDT`: `14` trades, `-236.62`
- `APTUSDT`: `22` trades, `-217.16`
- `PUMPUSDT`: `29` trades, `-196.86`
- `ADAUSDT`: `23` trades, `-186.42`

### 2.9 Best symbols

- `MORPHOUSDT`: `20` trades, `+377.11`
- `TRUMPUSDT`: `22` trades, `+209.74`
- `SOLUSDT`: `22` trades, `+194.06`
- `DOTUSDT`: `29` trades, `+178.93`
- `POLUSDT`: `22` trades, `+166.27`

### 2.10 Drawdown attribution

Max drawdown:

- Start: `2026-03-08 16:45:00`
- Trough: `2026-03-22 06:15:00`
- MDD: `12.21%`

Inside drawdown window:

- `red_bar_shrinking`: `90` trades, `-1005.12`
- `green_bar_shrinking`: `45` trades, `-318.25`
- `green_bar_growing`: `45` trades, `+204.76`
- `flip_bullish`: `22` trades, `+209.79`

Conclusion:

- The drawdown is not random.
- It is structurally driven by shrink families.
- The profitable families were active during the same window but could not offset the shrink losses.

## 3. Effective Funnel Attribution

Observed 30D funnel from bot-like replay:

- `0_raw_signal`: `97954` passed
- `1_score_threshold`: `90574` blocked
- `2_vwap_threshold`: `3984` blocked
- `3_4h_preflip_shrink`: `16656` blocked
- `4_pocket_entry_override`: `2` blocked
- `5_pre_ai_candidate_filter`: `29` blocked
- `6_L1_structural`: `1981` blocked
- `7_L2_flow`: `0` blocked
- `8_L3_micro`: `222` blocked
- `9_pretrade_gate`: `64` blocked
- `10_ai_review`: `5` blocked
- `11_capacity`: `116` blocked
- `12_final_fill`: `81` blocked

### What this means

1. The current system is already heavily gated before execution.
2. AI review is **not** the main blocker.
3. Pretrade gate is **not** the main blocker.
4. Capacity and minimum-open-size do matter, but they are secondary.
5. The biggest realized issue is still weak admitted trade quality from the wrong signal families.

## 4. Real Live Open Chain

This section describes the effective live opening path, not the old doc version.

### 4.1 Upstream bot guards

Before an order candidate is even worth discussing, the bot can skip or degrade at the outer layer:

1. Market data build / flow-context build
2. `extreme_volatility_cooldown`
3. time-window filter
4. symbol-specific campaign / schedule restrictions

If `extreme_volatility_cooldown` fires, the symbol is skipped before normal entry handling.

### 4.2 Core decision path

Then the code path is:

1. `TradingBot` builds `flow_context`
2. `FundFlowDecisionEngine._decide_macd_v2_strategy(...)`
3. `MACDStrategyV2Engine.analyze(...)`
4. Direction lock enforcement
5. Entry hard gate
6. Candidate pre-filter
7. AI shortlist / final review
8. Capacity check (`max_active_symbols`)
9. Pretrade risk gate
10. Final sizing / minimum-open-size check
11. Execution-quality 1m policy
12. Order submission

### 4.3 Inside `MACDStrategyV2Engine.analyze(...)`

The internal signal flow is:

1. Verify `15m / 1h / 4h` context exists
2. Build 15m / 1h / 4h MACD histogram series
3. Resolve 4h primary direction + 1h confirmation
4. Evaluate 15m entry follow / softening
5. Apply default signal threshold
6. Apply global VWAP threshold
7. Apply 4h shrink / preflip logic
8. Apply pocket-specific entry override
9. Aggregate weighted score
10. Return `long` / `short` / `neutral`

Only after that does DecisionEngine convert it into `BUY / SELL / HOLD / CLOSE`.

## 5. Current Detailed Thresholds

## 5.1 Portfolio / execution target

- `min_leverage = 3`
- `default_leverage = 4`
- `max_leverage = 5`
- `default_target_portion = 0.22`
- `add_position_portion = 0.20`
- `max_symbol_position_portion = 0.30`
- `max_active_symbols = 5`
- `min_open_portion = 0.03`
- `long_open_threshold = 0.10`
- `short_open_threshold = 0.10`
- `close_threshold = 0.30`

These values match the user’s intended leverage / position envelope, but they do **not** match the user’s target trade-count or win-rate outcome.

## 5.2 Core score / signal thresholds

Current config:

- `entry_thresholds.default = 0.85`
- `entry_thresholds.min_signal_score = 0.86`
- `min_entry_score = 0.25`
- `min_vwap_score_for_entry = 0.12`

Important clarification:

- `min_entry_score = 0.25` compares against the **raw** `entry_score_15m`
- It does **not** compare against `weight_15m_entry`
- In current live config, `weight_15m_entry = 0.0`, but the raw 15m entry gate still exists independently

That is not a contradiction; it is a deliberate split between:

- “15m must not be terrible” as a hard gate
- “15m contributes zero weighted alpha credit” in the final score

## 5.3 Signal-type thresholds

- `flip_bullish = 0.80`
- `flip_bearish = 0.80`
- `green_bar_growing = 1.20`
- `red_bar_growing = 1.20`
- `green_bar_shrinking = 1.50`
- `red_bar_shrinking = 1.50`
- `stable_bear_continuation_min_signal_score = 0.72`
- `stable_bull_continuation_min_signal_score = 0.72`

Interpretation:

- Shrink families are configured with extremely high nominal signal thresholds.
- But because multiple shrink paths are still present elsewhere in the decision mix, the stack is still over-trading and losing on shrink families.
- This suggests the current suppression is incomplete or being bypassed by alternate branches / states.

## 5.4 Entry-filter thresholds

- `primary_direction_timeframe = 4h`
- `require_1h_confirmation_when_4h_primary = true`
- `allow_neutral_1h_confirmation = true`
- `light_1h_confirmation_when_4h_primary = true`
- `require_macd_home_advantage = true`
- `vwap_execution_penalty_only = true`
- `require_15m_confirmation_gate = false`

Trial / promotion:

- `preflip_trial_min_signal_score = 0.80`
- `preflip_trial_min_vwap_score = 0.08`
- `preflip_trial_entry_scale = 0.20`
- `preflip_trial_max_leverage = 3`
- `trial_short_below_structure_promotion_min_signal_score = 0.80`
- `trial_short_below_structure_promotion_min_vwap_score = 0.08`
- `trial_short_below_structure_promotion_min_adx_1h = 30`

Continuation:

- `stable_bear_continuation_min_vwap_score = 0.05`
- `stable_bear_continuation_min_adx_1h = 25`
- `stable_bear_continuation_min_4h_bars = 1`
- `stable_bull_continuation_min_vwap_score = 0.08`
- `stable_bull_continuation_min_adx_1h = 25`
- `stable_bull_continuation_min_4h_bars = 1`

Flip bullish:

- `flip_bullish_min_vwap_score = 0.08`
- `flip_bullish_require_pullback_bounce = true`
- `flip_bullish_require_15m_growing = false`
- `disable_flip_bullish_trial_entries = false`
- `long_entry_mode = all`

## 5.5 Pocket overrides currently active

Notable strict pockets:

- `red_bar_growing|long_dual_support`
  - `min_signal_score = 0.84`
  - `min_vwap_score = 0.12`
  - `min_entry_score = 0.35`
  - `require_cvd_ok = true`
  - `require_cvd_momentum_ok = true`
  - `require_strict_1h_confirmation = true`
  - `disallow_trial_entry = true`

- `green_bar_growing|long_dual_support`
  - `min_signal_score = 0.90`
  - `min_vwap_score = 0.12`
  - `min_entry_score = 0.35`
  - `require_cvd_ok = true`
  - `require_cvd_momentum_ok = true`
  - `require_strict_1h_confirmation = true`
  - `disallow_trial_entry = true`

- `green_bar_growing|short_dual_pressure`
  - `min_signal_score = 0.84`
  - `min_vwap_score = 0.14`
  - `require_cvd_ok = true`
  - `require_strict_1h_confirmation = true`

- `red_bar_growing|short_dual_pressure`
  - `min_signal_score = 0.90`
  - `min_vwap_score = 0.16`
  - `min_entry_score = 0.35`
  - `require_cvd_ok = true`
  - `require_cvd_momentum_ok = true`
  - `require_strict_1h_confirmation = true`
  - `disallow_trial_entry = true`

Disabled pockets:

- `green_bar_growing|long_below_both`
- `red_bar_growing|short_retest_reject`
- `red_bar_growing|short_under_structure_wait_reject`
- `red_bar_growing|short_above_both`
- `green_bar_growing|long_above_structure_wait_reclaim`
- `green_bar_shrinking|short_retest_reject`
- `red_bar_shrinking|short_retest_reject`

## 6. Current Weighted Scoring

Current live config scoring weights:

- `weight_1h_direction = 0.25`
- `weight_4h_direction = 0.35`
- `weight_boll_position = 0.25`
- `weight_vwap = 0.05`
- `weight_15m_entry = 0.0`
- `weight_volume = 0.10`

### Important implication

Current scoring architecture is telling the model:

- 4h direction is dominant
- 1h direction is secondary
- Boll structure matters a lot
- VWAP contributes only a little direct score
- 15m contributes **no direct weighted alpha**
- volume contributes a small confirmation score

### 15m is still present via hard gating

Even though `weight_15m_entry = 0.0`, the following still hold:

- `min_entry_score = 0.25`
- pocket-specific `min_entry_score` can be `0.35`
- preflip-trial gating still uses 15m structure

So the current stack uses 15m primarily as a **permission gate**, not as a weighted conviction source.

## 7. Current Risk Logic

## 7.1 Opening risk gates

### Extreme volatility cooldown

- `timeframe = 15m`
- base trigger `atr_pct >= 0.02`
- `consecutive_bars = 2`
- first lock = `900s`
- repeated lock = `1800s`
- dynamic quantile guard enabled:
  - `quantile = 0.95`
  - `window = 96`
  - `min_samples = 24`

### Entry hard gate

- `adx_min = 15`
- `atr_min = 0.003`
- `atr_max = 0.025`
- `spread_bps_max = 0.001`
- `flow_min_pass = 1`
- `micro_min_pass = 1`

Observed 30D blocker composition:

- L1 structural was the main hard-gate blocker
- L2 flow blocked almost nothing
- L3 micro blocked `222`

### Candidate pre-filter

Current config includes candidate pre-filter hard rejects, for example:

- `HYPEUSDT green_bar_growing | short_retest_reject`
- `RENDERUSDT green_bar_growing | short_retest_reject`

Observed 30D:

- `29` candidates blocked

### AI review

- `flat_top_n = 6`
- `final_min_score = 0.06`
- `final_same_side_add_min_score = 0.10`
- `final_trend_weak_score = 0.15`
- `final_trend_min_structure_votes = 1`
- `final_max_trap_score = 0.5`

Observed 30D:

- only `5` blocked

This is not the main bottleneck.

### Capacity

- `max_active_symbols = 5`

Observed 30D:

- `116` blocked by capacity

### Final fill size gate

- `min_open_portion = 0.03`

Observed 30D:

- `81` blocked by `target_portion_below_min_open`

## 7.2 Pretrade risk gate

Current config:

- `enabled = true`
- `use_hard_rules_only = true`
- `cvd_veto_enabled = false`
- `atr_ratio_hard_block = 3.5`
- `equity_usage_block = 0.85`
- `dd_exit_threshold = 0.10`
- `entry_block_actions = ["BLOCK"]`

Observed 30D:

- `64` blocked

So it matters, but it is still not the primary 30D failure source.

## 7.3 Execution-quality 1m gate

This gate is active by code default even without a dedicated config block.

Default thresholds:

- `block_spread_bps = 12.0`
- `block_spread_z = 2.2`
- `block_vpin = 0.72`
- `block_flow_toxicity = 0.72`
- `block_trap_score = 0.72`

Degrade thresholds:

- `degrade_spread_bps = 6.0`
- `degrade_spread_z = 1.2`
- `degrade_vpin = 0.48`
- `degrade_flow_toxicity = 0.48`
- `degrade_trap_score = 0.45`

Passive preference thresholds:

- `passive_max_spread_bps = 4.0`
- `passive_max_spread_z = 0.8`
- `passive_max_vpin = 0.35`
- `passive_max_flow_toxicity = 0.35`
- `passive_max_trap_score = 0.30`
- `passive_max_bb_pos_norm = 0.65`

Effect:

- If `block_entry = true`, pretrade stage forces `HOLD`
- If not blocked but degraded, execution is pushed toward passive entry / stricter TIF

## 7.4 Position / account risk controls

- `max_single_trade_nominal_ratio = 0.60`
- `daily_loss_limit_pct = 0.03`
- `consecutive_loss_halt_count = 3`
- `atr_position_scale_enabled = true`

ATR position scale bands:

- `atr_pct <= 0.014` -> scale `1.00`
- `atr_pct <= 0.018` -> scale `0.85`
- `atr_pct <= 0.022` -> scale `0.70`
- `atr_pct <= 0.025` -> scale `0.55`

## 7.5 Post-open risk controls

### Stop loss

- `stop_loss_pct = 0.02`
- `max_stop_loss_pct = 0.025`
- `stop_loss_config.use_dynamic_stop = true`
- `boll_stop_atr_multiplier = 0.3`
- `max_stop_distance_pct = 0.025`

### Partial take profit

Global TP:

- `take_profit_pct = 0.04`
- `take_profit_pct_levels = [0.008, 0.012, 0.020]`
- `take_profit_reduce_pct_levels = [0.25, 0.30, 0.20]`

Dynamic partial TP:

- volatile level 0: `45% at 0.7R`
- volatile level 1: `30% at 1.5R`
- trending level 0: `15% at 1.5R`
- trending level 1: `20% at 3.0R`

### Breakeven

- `breakeven_enabled = true`
- `breakeven_trigger_pnl_ratio = 0.008`
- `breakeven_lock_ratio = 0.004`

### Trailing stop

Global:

- `trailing_stop_enabled = true`
- `activation_pct = 0.012`
- `atr_multiplier = 1.0`
- `min_distance = 0.007`
- `max_distance = 0.015`

Volatile mode:

- `activation_pct = 0.008`
- `atr_multiplier = 0.6`
- `min_distance = 0.005`
- `max_distance = 0.010`
- `breakeven_trigger = 0.005`
- `breakeven_lock = 0.002`

Trending mode:

- `activation_pct = 0.018`
- `atr_multiplier = 1.8`
- `min_distance = 0.012`
- `max_distance = 0.030`
- `breakeven_trigger = 0.012`
- `breakeven_lock = 0.003`

### Time exit

- `time_exit_enabled = true`
- `time_exit_minutes = 60`
- `time_exit_min_profit_pct = 0.0035`

### Protection SLA

- `enabled = true`
- `timeout_seconds = 300`
- `force_flatten = true`
- `alert_cooldown_seconds = 30`
- `pnl_grace_threshold = -0.005`
- `api_health_check_before_force = true`

Meaning:

- If protection orders remain unhealthy too long, the bot can force flatten.
- This is a real live safety layer, not a cosmetic log.

## 8. Direct Review Call

### Current verdict

This direction is **not ready for target deployment quality**.

Why:

1. 30D return is `-11.00%`, far from `50%+`
2. Win rate is `63.76%`, far from `80%+`
3. Trade count is `516`, far above `30-120`
4. The system still mixes profitable continuation / reclaim pockets with structurally bad shrink families
5. Loss asymmetry is severe enough to make a superficially acceptable hit rate still lose money

### The real bottleneck

The real bottleneck is not AI review, and not pretrade gate.

The real bottleneck is:

- bad admitted alpha from shrink families
- especially `red_bar_shrinking` and `green_bar_shrinking`
- especially in `short_dual_pressure`, `short_retest_reject`, and weak long reclaim / dual-support contexts

### What DeepSeek should audit first

1. Why shrink-family suppression is still incomplete despite high nominal thresholds and several disabled pockets
2. Whether `red_bar_shrinking` / `green_bar_shrinking` should be removed from live entry entirely instead of partially gated
3. Whether current `weight_15m_entry = 0.0` plus `min_entry_score` gating is structurally coherent, or just a half-disabled design
4. Whether `capacity_block=116` is hiding good continuation trades while weak shrink trades still enter
5. Whether the target trade-count `30-120 / 30D` requires a much harder entry-family whitelist rather than threshold nudging

## 9. Source Files

Backtest artifacts:

- `output/backtest/bot_like_summary_20260411_102025.json`
- `output/backtest/bot_like_trades_20260411_102025.csv`
- `output/backtest/bot_like_candidate_ledger_20260411_102025.csv`
- `output/backtest/bot_like_ai_advice_20260411_102025.csv`
- `output/analysis/signal_funnel_30d.json`

Config / code:

- `config/trading_config_fund_flow.json`
- `src/fund_flow/macd_strategy_v2.py`
- `src/fund_flow/decision_engine.py`
- `src/app/fund_flow_bot.py`
