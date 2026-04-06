# combo_validated 基线容量回补验证

- 日期: 2026-04-05
- 回放窗口: `2026-03-05T03:00:00` -> `2026-04-04T03:00:00`
- 基线配置: `config/candidates/trading_config_fund_flow_review_combo_validated_20260405.json`
- 目标: 在不重新引入明显负 edge 的前提下，放松门槛并回补交易量

## 1. 结果总表

| label | config | return_pct | win_rate_pct | trades | signals | profit_factor | max_drawdown_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| combo_validated | `config/candidates/trading_config_fund_flow_review_combo_validated_20260405.json` | 6.47% | 89.74% | 39 | 38 | 3.51 | 3.16% |
| capacity_r1 | `config/candidates/trading_config_fund_flow_review_capacity_r1_20260405.json` | 6.09% | 88.89% | 45 | 44 | 2.39 | 5.30% |
| capacity_r2 | `config/candidates/trading_config_fund_flow_review_capacity_r2_20260405.json` | 6.07% | 87.04% | 54 | 54 | 2.29 | 5.30% |
| capacity_r3 | `config/candidates/trading_config_fund_flow_review_capacity_r3_20260405.json` | 2.39% | 82.83% | 99 | 86 | 1.31 | 5.88% |
| live_production | `config/trading_config_fund_flow_live_production.json` | 0.72% | 76.67% | 150 | 188 | 1.17 | 8.29% |

## 2. 候选定义

### capacity_r1
- 恢复 short-oriented trial
- `max_active_symbols = 6`
- 温和放松 `default / min_signal / min_vwap`
- 保留 `disable_red_bar_growing_long_entries`
- 保留 `KASUSDT` blacklist

### capacity_r2
- 在 `r1` 基础上进一步放松阈值
- 恢复部分 `green_bar_growing` symbol 覆盖
- 仍保留 `disable_red_bar_growing_long_entries`
- 仍保留 `KASUSDT` blacklist

### capacity_r3
- 明显激进的容量测试
- `max_active_symbols = 8`
- 更低的 score / vwap 门槛
- 恢复 short trial
- 去掉 symbol overrides
- 仍保留 `disable_red_bar_growing_long_entries`
- 仍保留 `KASUSDT` blacklist

## 3. 结构拆分

### capacity_r1

| signal_type_1h | count | pnl | win_rate |
| --- | --- | --- | --- |
| flip_bullish | 18 | 102.05 | 77.78% |
| green_bar_growing | 24 | 520.58 | 95.83% |
| red_bar_growing | 3 | 63.84 | 100.00% |

trial:
- `False`: 45 trades / `+686.48`

### capacity_r2

| signal_type_1h | count | pnl | win_rate |
| --- | --- | --- | --- |
| flip_bullish | 18 | 101.62 | 77.78% |
| green_bar_growing | 33 | 525.08 | 90.91% |
| red_bar_growing | 3 | 63.69 | 100.00% |

trial:
- `False`: 46 trades / `+672.84`
- `True`: 8 trades / `+17.55`

### capacity_r3

| signal_type_1h | count | pnl | win_rate |
| --- | --- | --- | --- |
| flip_bullish | 18 | 98.32 | 77.78% |
| green_bar_growing | 77 | 455.27 | 84.42% |
| red_bar_growing | 4 | -185.57 | 75.00% |

trial:
- `False`: 59 trades / `+358.53`
- `True`: 40 trades / `+9.48`

## 4. 结论

- 在保留 `combo_validated` 核心 edge 的前提下，容量回补可以把交易数从 `39` 提到 `45-54`，且收益基本不变。
- 再继续激进放松，交易数最多拉到 `99`，但 `profit_factor` 已经从 `3.51` 掉到 `1.31`。
- 当前 30 天窗口下，想把 bot-like live-aligned 路径做到 `300-600 trades`，仅靠“放松门槛”做不到。
- 真正的容量瓶颈不是 `flip_bullish`，而是恢复后新增的 `green_bar_growing` 与 `trial`。但一旦放松过头，`red_bar_growing` 又会重新变成负贡献。

## 5. 建议

- 如果目标是保住收益质量，`capacity_r2` 是当前最合理的容量回补候选。
- 如果目标是硬冲交易数，接下来不能继续只放 score/vwap 门槛；必须改策略层级，例如更低时间框架、更多 universe、或不同的开仓状态机。
- 不要为了追 `300-600 trades` 直接恢复 `red_bar_growing` long 或取消 `KASUSDT` blacklist，那会重新引入已验证的负 edge。
