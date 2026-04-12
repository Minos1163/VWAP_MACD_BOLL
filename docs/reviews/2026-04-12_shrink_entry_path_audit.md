# Shrink Entry Path Audit

## Scope

Audit target:

- `red_bar_shrinking`
- `green_bar_shrinking`

Question:

- Are these shrink entries still leaking into the **current** live-config stack
- If they leaked before, which branch admitted them

## Result

### Verdict

The previously observed shrink trades were **not** admitted by the current config.

They came from an **older backtest artifact** generated before the current live-config hardening was written to disk.

### Evidence

Old artifact:

- `output/backtest/bot_like_summary_20260411_102025.json`
- timestamp: `2026-04-11 10:20:25`

Current config:

- `config/trading_config_fund_flow.json`
- timestamp: `2026-04-12 00:35:56`

This means the old 30D replay result predated the current config by roughly 14 hours.

## Old Artifact Behavior

Old 30D replay:

- total trades: `516`
- shrink trades present:
  - `red_bar_shrinking = 210`
  - `green_bar_shrinking = 108`

Candidate-ledger evidence shows these were **direct local entry decisions**, not fallback decorations.

Examples:

- `macd_v2_short_1h_red_bar_shrinking_15m_green_bar_growing_vwap_0.13`
- `macd_v2_short_1h_red_bar_shrinking_15m_flip_bearish_vwap_0.12`
- `macd_v2_long_1h_green_bar_shrinking_15m_red_bar_growing_vwap_0.11`

This identifies the admission branch clearly:

- direct `signal.direction == 'long' / 'short'` branch in `FundFlowDecisionEngine._decide_macd_v2_strategy`
- not `regime_fallback`
- not post-entry metadata corruption

## Current Config Behavior

Fresh rerun with current config:

- command window: `2026-03-02T00:00:00` to `2026-04-01T00:00:00`
- summary: `output/backtest/bot_like_summary_20260412_194244.json`
- trades: `output/backtest/bot_like_trades_20260412_194244.csv`
- candidate ledger: `output/backtest/bot_like_candidate_ledger_20260412_194244.csv`

Fresh rerun result:

- total trades: `77`
- signal families traded: only `flip_bearish`
- `red_bar_shrinking` trades: `0`
- `green_bar_shrinking` trades: `0`
- candidate-ledger shrink rows: `0`

So under the current config, shrink family entries are not leaking.

## Why Current Config Blocks Them

Current config now includes:

- `disable_red_bar_shrinking_entries = true`
- `disable_green_bar_shrinking_entries = true`

And current strategy flow contains explicit early neutralization:

- if `signal_type_1h == "red_bar_shrinking"` and disable flag is on -> return neutral
- if `signal_type_1h == "green_bar_shrinking"` and disable flag is on -> return neutral

In addition, current state-machine mode is active because config now enables:

- `require_macd_home_advantage = true`
- `vwap_execution_penalty_only = true`

Inside that state-machine path, only the flip families are eligible for directional entry:

- Quadrant I / II -> `flip_bullish`
- Quadrant III / IV -> `flip_bearish`

Shrink families do not get a directional pass there.

## What The Old Leak Was

The old leak was **not** a hidden branch in the current code.

The old leak was:

1. old config / old replay artifact still allowed shrink-family direct entries
2. those entries were emitted through the normal `macd_v2_long_1h_*` / `macd_v2_short_1h_*` path
3. the old artifact was then used as if it reflected the current config, which it did not

## Practical Conclusion

### What is ruled out

- current config still leaking shrink entries: **ruled out**
- 4h regime fallback re-introducing shrink entries under current config: **ruled out by rerun**
- candidate-ledger / trades csv label corruption: **ruled out**

### What remains true

- the old 30D loss attribution based on shrink families was valid **for the old artifact**
- it is **not valid as a direct statement about the current config**
- the current config is much more restrictive and now trades only `flip_bearish` in this 30D replay window

## Review Focus For DeepSeek

What DeepSeek should inspect next:

1. Whether trading only `flip_bearish` in the current 30D replay is now too restrictive
2. Whether current config over-collapsed the opportunity set from `516` trades to `77` trades
3. Whether the current stack can ever reach the target `50%+ / 80%+ / 30-120 trades` without reintroducing weak families
4. Whether the current state-machine + disable flags are coherent or just a temporary hard clamp
