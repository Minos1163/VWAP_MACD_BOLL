# MACD V2 当前开仓逻辑 / 门槛 / 风控

**日期**: 2026-03-31  
**状态**: 已落地 `15m gate 移除`、`迭代 #1 门槛消融 A+B`、`迭代 #2 双模风控`、`迭代 #3 threshold_check 校准`

## 1. 当前开仓主链路

当前真实开仓顺序：

`4H/1H 方向判定 -> VWAP/结构过滤 -> score_aggregation -> threshold_check -> entry_hard_gates -> pretrade_risk_gate`

其中：

- `15m confirm` 已不再单独 veto 开仓
- `15m` 只保留为软评分和诊断字段
- `entry_hard_gates` 仍然在 `score` 之后执行
- `pretrade_risk_gate` 现在会在高 ATR 环境自动缩仓

## 2. 当前 MACD V2 权重

来自 [trading_config_fund_flow_live_production.json](/D:/AIDCA/AI8/config/trading_config_fund_flow_live_production.json)：

| 模块 | 当前值 |
|---|---:|
| `weight_4h_direction` | `0.40` |
| `weight_vwap` | `0.20` |
| `weight_1h_direction` | `0.20` |
| `weight_volume` | `0.15` |
| `weight_15m_entry` | `0.05` |

## 3. 已落地门槛消融

### 3.1 15m gate

- `require_15m_confirmation_gate = false`
- `15M未确认入场` 不再直接返回 `HOLD`
- `15M入场评分过低` 不再直接返回 `HOLD`

### 3.2 4H preflip

| 参数 | 当前值 |
|---|---:|
| `preflip_trial_min_shrink_pct_long` | `0.45` |
| `preflip_trial_min_shrink_pct_short` | `0.22` |
| `preflip_trial_min_signal_score` | `0.70` |
| `preflip_trial_min_vwap_score` | `0.06` |
| `preflip_trial_entry_scale` | `0.35` |

### 3.3 VWAP score filter

| 参数 | 当前值 |
|---|---:|
| `min_vwap_score_for_entry` | `0.10` |
| `stable_bear_continuation_min_vwap_score` | `0.07` |
| `preflip_trial_min_vwap_score` | `0.06` |

## 4. 当前 hard gates

当前仍保持原来的第一阶段 hard gates，不做 `#4~#5` 消融：

| 参数 | 当前值 |
|---|---:|
| `entry_hard_gate_adx_min` | `22` |
| `entry_hard_gate_atr_min` | `0.006` |
| `entry_hard_gate_atr_max` | `0.020` |
| `entry_hard_gate_spread_bps_max` | `0.0008` |
| `entry_hard_gate_flow_min_pass` | `2` |
| `entry_hard_gate_micro_min_pass` | `2` |

回测补丁里保持：

- `entry_hard_gate_skip_spread_if_missing = true`

生产配置里当前文档对应值不包含该 backtest-only 容错。

## 5. 当前双模持仓风控

### 5.1 Partial TP

静态兜底：

- `1R -> 30%`
- `2R -> 40%`

动态模式已启用：

- `partial_tp_mode = dynamic`

波动模式：

- `45% @ 0.7R`
- `30% @ 1.5R`

趋势模式：

- `15% @ 1.5R`
- `20% @ 3.0R`

### 5.2 Trailing stop

- `trailing_stop_mode = dynamic`
- trailing 只在已经发生 partial TP 后激活
- trailing 价格按 `high_water/low_water + ATR distance` 更新
- protection 层只收紧 SL，不在策略层直接市价平仓

波动模式：

| 参数 | 当前值 |
|---|---:|
| `activation_pct` | `0.008` |
| `atr_multiplier` | `0.6` |
| `min_distance` | `0.005` |
| `max_distance` | `0.010` |
| `breakeven_trigger` | `0.005` |
| `breakeven_lock` | `0.002` |

趋势模式：

| 参数 | 当前值 |
|---|---:|
| `activation_pct` | `0.018` |
| `atr_multiplier` | `1.8` |
| `min_distance` | `0.012` |
| `max_distance` | `0.030` |
| `breakeven_trigger` | `0.012` |
| `breakeven_lock` | `0.003` |

### 5.3 ATR 缩仓

已接到 `pretrade_risk_gate` 开仓链路：

| `atr_pct` 区间 | scale |
|---|---:|
| `<= 0.014` | `1.00` |
| `<= 0.018` | `0.85` |
| `<= 0.022` | `0.70` |
| `<= 0.025` | `0.55` |
| `> 0.025` | `0.00` |

## 6. 当前仍未消融部分

以下仍保持原门槛，等待下一轮回测后再决定：

- `L1 ADX / ATR / Regime`
- `L3 微结构门槛`

## 6.1 当前 threshold_check

| 参数 | 当前值 |
|---|---:|
| `default` | `0.830` |
| `min_signal_score` | `0.830` |
| `red_bar_growing` | `0.845` |
| `flip_bearish` | `0.825` |
| `flip_bullish` | `0.825` |
| `stable_bear_continuation_min_signal_score` | `0.800` |
| `stable_bull_continuation_min_signal_score` | `0.800` |

## 7. 对应文件

- [macd_strategy_v2.py](/D:/AIDCA/AI8/src/fund_flow/macd_strategy_v2.py)
- [fund_flow_bot.py](/D:/AIDCA/AI8/src/app/fund_flow_bot.py)
- [trading_config_fund_flow_live_production.json](/D:/AIDCA/AI8/config/trading_config_fund_flow_live_production.json)
- [patch_enabled_new_controls.json](/D:/AIDCA/AI8/output/backtest/compare_20260330/patch_enabled_new_controls.json)
