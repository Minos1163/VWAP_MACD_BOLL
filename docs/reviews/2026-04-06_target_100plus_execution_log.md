# 2026-04-06 Target 100 Plus Execution Log

## Goal

- leverage: `2X / 3X / 4X`
- single-symbol target portion: `20%-30%`
- max active symbols: `5`
- 30d return: `100%+`
- 30d win rate: `80%+`
- 30d actual opens: `300-500`

## Best Candidate So Far

- Config:
  - `config/candidates/trading_config_fund_flow_review_5m_target_align_stage6_fill_20260406.json`
- Replay summary:
  - `output/backtest/bot_like_summary_20260406_042124.json`

### Best verified 30d bot-like result

- return: `+66.81%`
- win rate: `85.93%`
- actual opens: `393`
- trade rows: `981`
- max drawdown: `4.40%`
- profit factor: `2.73`

### Target check

- leverage `2/3/4`: pass
- max active symbols `5`: pass
- actual opens `300-500`: pass (`393`)
- win rate `80%+`: pass (`85.93%`)
- return `100%+`: fail (`+66.81%`)

## Candidate progression

| stage | config | return | win rate | opens | notes |
|---|---|---:|---:|---:|---|
| baseline pockets | `...long_pockets_20260405.json` | `+12.18%` | `91.18%` | `24` | too sparse |
| stopfirst audit | `...long_pockets_stopfirst_20260406.json` | `+11.24%` | `90.77%` | `24` | same-bar optimism modest |
| stage1 target align | `...stage1_20260406.json` | `+4.91%` | `88.33%` | `24` | target alignment alone crushed return |
| stage2 frequency | `...stage2_freq_20260406.json` | `+27.14%` | `82.31%` | `435` | first successful turnover breakout |
| stage4 efficiency | `...stage4_efficiency_20260406.json` | `+56.53%` | `81.63%` | `409` | position tiers + long cleanup improved throughput |
| stage5 return | `...stage5_return_20260406.json` | `+61.56%` | `85.31%` | `388` | exact long reclaim pockets improved quality |
| stage6 fill recovery | `...stage6_fill_20260406.json` | `+66.81%` | `85.93%` | `393` | current best |
| stage7 final push | `...stage7_finalpush_20260406.json` | `+66.83%` | `85.83%` | not better | marginal/no improvement |

## What changed in the best candidate

- switched to `same_bar_tp_priority_mode = stop_first`
- enabled configurable leverage tiers and used `2/3/4`
- added configurable position score tiers and used `0.20 / 0.25 / 0.30`
- set `max_active_symbols = 5`
- expanded short-side opportunity count aggressively
- pruned weak long states back to exact reclaim pockets
- lowered `min_open_portion` enough to recover blocked profitable short candidates

## Remaining bottleneck

The strategy now satisfies structure, turnover, and win-rate goals. The remaining gap is pure return.

- current return gap to target: `33.19%`
- current engine is still short-dominant
- long side is no longer toxic, but still much weaker than short side
- best result still depends mainly on:
  - `green_bar_growing|short_retest_reject`
  - `green_bar_growing|short_below_session_above_structure`
  - `flip_bearish|short_retest_reject`

## Recommended next iteration

If continuing from the current best candidate:

1. raise average short expectancy, not raw open count
2. inspect whether `short_below_session_above_structure` exits are leaving too much on table
3. test slightly less aggressive time-exit on strong short pockets only
4. test higher TP depth for the strongest short pockets while preserving stop discipline
5. do not broaden long pockets again until long expectancy improves structurally

