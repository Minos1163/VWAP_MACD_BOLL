# 当前实盘开仓链路、详细门槛、权重与风控说明

日期: 2026-04-11

## 1. 当前主配置

配置文件:
[config/trading_config_fund_flow.json](/D:/AIDCA/AI8/config/trading_config_fund_flow.json)

当前关键目标参数:

- `default_leverage = 4`
- `min_leverage = 3`
- `max_leverage = 5`
- `default_target_portion = 0.22`
- `position_size_config.score_tiers = 0.24 / 0.28 / 0.30`
- `max_active_symbols = 5`
- `entry_thresholds.default = 0.85`

当前策略语义:

- `strategy_mode = macd_mtf_strategy_v2`
- `long_entry_mode = all`
- `disable_flip_bullish_trial_entries = false`

注意:

- long 侧已经不是旧版 `whitelist_only`
- 但 symbol 级 `trial_only` / `enabled_with_strict_threshold` 仍然存在，属于真实前置挡板

## 2. 实盘开仓链路

当前真实调用顺序:

```text
调度对齐
  -> get_market_data_for_symbol()
  -> _build_fund_flow_context()
  -> _apply_timeframe_context()
  -> 决策前硬跳过:
       extreme_volatility_cooldown
       conflict cooldown
       trigger dedupe
  -> FundFlowDecisionEngine.decide()
       -> _detect_regime()
       -> _decide_macd_v2_strategy()
            -> MACDStrategyV2Engine.analyze()
            -> symbol override / pocket gate / hard gate
            -> regime entry fallback / direction lock
  -> signal_pool（仅有 BUY/SELL 候选时）
  -> pretrade_risk_gate
  -> AI shortlist / capacity / final fill
  -> TradeExecutor / OrderGateway
  -> TP/SL / partial TP / breakeven / trailing / protection SLA
```

## 3. 多时间框架数据链路

### 3.1 当前代码已经会主动拉 1h/4h 数据

入口:
[fund_flow_bot.py](/D:/AIDCA/AI8/src/app/fund_flow_bot.py)

关键事实:

- `get_market_data_for_symbol()` 会按规则请求:
  - `primary_trend_timeframe`
  - `entry_timeframe`
  - `regime_timeframe`
  - `dual_risk_timeframe`
- 结果写入 `trend_filters_by_timeframe`
- `_apply_timeframe_context()` 会把这些快照注入 `flow_context["timeframes"]`

### 3.2 trend_filter 快照当前已包含 series

来源:
[market_data.py](/D:/AIDCA/AI8/src/data/market_data.py)

`get_trend_filter_metrics()` 当前返回:

- `macd_hist_series`
- `macd_hist_array`
- `macd_hist_prev`
- `close_series`
- `vwap_series`

所以，如果运行时仍出现 `missing_tf_data`，根因更可能是:

- 特定 symbol/timeframe 请求失败
- 外部数据空洞
- 注入到 `timeframes` 时 해당 timeframe 为空

而不是“代码根本没拉 1h/4h 序列”。

### 3.3 当前新增的运行时诊断字段

`decision.metadata["macd_tf_diagnostics"]`:

```json
{
  "15m": {
    "timeframe_present": true,
    "series_source": "series|array|fallback_pair|missing",
    "series_len": 60,
    "macd_hist_present": true,
    "macd_hist_prev_present": true,
    "close_series_len": 60
  },
  "1h": { ... },
  "4h": { ... }
}
```

`macd_v2_debug["score_1h_source"]`:

- `neutral_allowed_light_credit`
- `signal_type_flip`
- `signal_type_growing`
- `aligned_direction_strength`
- `no_direction_credit`

runtime 打印:

```text
MACD_V2时框诊断:
score_1h_source=...,
15m=series/len=.../hist=.../prev=...
1h=series/len=.../hist=.../prev=...
4h=series/len=.../hist=.../prev=...
```

## 4. 详细门槛分数

### 4.1 全局基础门槛

| 层级 | 参数 | 当前值 | 说明 |
| --- | --- | ---: | --- |
| MACD V2 | `entry_thresholds.default` | `0.85` | 默认 signal_score 门槛 |
| MACD V2 | `entry_thresholds.min_signal_score` | `0.86` | 配置层最小 signal score |
| MACD V2 | `min_entry_score` | `0.25` | 15m 原始 entry 分门槛 |
| MACD V2 | `min_vwap_score_for_entry` | `0.12` | 全局 VWAP 门槛 |
| Decision resolve | `long_open_threshold` | `0.10` | final long resolve 阈值 |
| Decision resolve | `short_open_threshold` | `0.10` | final short resolve 阈值 |

### 4.2 signal type 阈值

| 类型 | 阈值 |
| --- | ---: |
| `red_bar_growing` | `0.94` |
| `red_bar_shrinking` | `0.80` |
| `flip_bearish` | `1.20` |
| `flip_bullish` | `0.80` |
| `green_bar_growing` | `0.86` |
| `stable_bear_continuation` | `0.72` |
| `stable_bull_continuation` | `0.72` |

### 4.3 pocket_entry_overrides

| pocket | 当前门槛 / 要求 |
| --- | --- |
| `red_bar_growing|long_dual_support` | `min_signal_score=0.84`, `min_vwap_score=0.12`, `min_entry_score=0.35`, `require_cvd_ok`, `require_cvd_momentum_ok`, `require_strict_1h_confirmation`, `disallow_trial_entry` |
| `green_bar_growing|short_dual_pressure` | `min_signal_score=0.84`, `min_vwap_score=0.14`, `require_cvd_ok`, `require_strict_1h_confirmation` |
| `red_bar_growing|short_dual_pressure` | `min_signal_score=0.84`, `min_vwap_score=0.12`, `require_cvd_ok`, `require_cvd_momentum_ok`, `require_strict_1h_confirmation` |
| `green_bar_growing|short_under_structure_wait_reject` | `min_signal_score=0.90`, `min_vwap_score=0.14`, `require_strict_1h_confirmation` |
| `green_bar_growing|short_below_session_above_structure` | `min_signal_score=0.90`, `min_vwap_score=0.14`, `require_strict_1h_confirmation` |

### 4.4 symbol_signal_overrides

#### `flip_bullish_mode = trial_only`

共 `18` 个:

`DOGEUSDT`, `APTUSDT`, `LINKUSDT`, `BCHUSDT`, `JUPUSDT`, `PUMPUSDT`, `RENDERUSDT`, `ZROUSDT`, `LTCUSDT`, `XRPUSDT`, `HYPEUSDT`, `TAOUSDT`, `ICPUSDT`, `ETCUSDT`, `KASUSDT`, `WLDUSDT`, `MORPHOUSDT`, `JSTUSDT`

#### `green_bar_growing_mode = enabled_with_strict_threshold`

共 `20` 个:

`ZECUSDT`, `TONUSDT`, `FETUSDT`, `DOGEUSDT`, `ONDOUSDT`, `APTUSDT`, `AVAXUSDT`, `LINKUSDT`, `ADAUSDT`, `PUMPUSDT`, `ZROUSDT`, `LTCUSDT`, `SOLUSDT`, `ICPUSDT`, `KASUSDT`, `POLUSDT`, `WLDUSDT`, `ALGOUSDT`, `VETUSDT`, `JSTUSDT`

#### 额外分数/VWAP override

共 `5` 个:

`ZECUSDT`, `DOGEUSDT`, `LINKUSDT`, `ZROUSDT`, `LTCUSDT`

## 5. 详细权重评分

### 5.1 当前主评分权重

来源:
[macd_strategy_v2.py](/D:/AIDCA/AI8/src/fund_flow/macd_strategy_v2.py)

| 评分项 | 权重 |
| --- | ---: |
| `weight_1h_direction` | `0.40` |
| `weight_4h_direction` | `0.20` |
| `weight_4h_enhancement` | `0.00` |
| `weight_vwap` | `0.20` |
| `weight_15m_entry` | `0.05` |
| `weight_volume` | `0.15` |

总分:

```text
signal_score
  = score_1h
  + score_4h
  + score_4h_enhancement
  + score_vwap
  + score_15m
  + score_volume
```

### 5.2 1H 分数来源

当前新增诊断字段可区分:

- `neutral_allowed_light_credit`
- `signal_type_flip`
- `signal_type_growing`
- `aligned_direction_strength`
- `no_direction_credit`

这能直接回答:

- 是 `1H` 数据缺失导致 0 分
- 还是 `1H` 有数据，但逻辑上不给分

### 5.3 杠杆与仓位权重

#### 杠杆 score tiers

| score 区间 | leverage |
| --- | ---: |
| `1.00` | `5x` |
| `[0.84, 1.00)` | `4x` |
| `[0.78, 0.84)` | `3x` |

#### 仓位 score tiers

| score 下限 | target_portion |
| --- | ---: |
| `0.85` | `0.30` |
| `0.75` | `0.28` |
| `0.68` | `0.24` |
| 默认 | `0.22` |

#### watchlist 风险钳制

| 参数 | 当前值 |
| --- | ---: |
| `watchlist_max_position_portion` | `0.15` |
| `watchlist_max_leverage` | `3` |
| `watchlist_session_scale_multiplier` | `0.6` |

## 6. 详细风控逻辑

### 6.1 开仓前风控

#### A. 极端波动冷却

| 参数 | 当前值 |
| --- | ---: |
| `extreme_volatility_cooldown_enabled` | `true` |
| `timeframe` | `15m` |
| `atr_pct_threshold` | `0.02` |
| `consecutive_bars` | `2` |
| `cooldown_seconds` | `1800` |
| `first_trigger_seconds` | `900` |
| `quantile_enabled` | `true` |
| `quantile_value` | `0.95` |
| `quantile_window` | `96` |
| `quantile_min_samples` | `24` |

#### B. entry_hard_gate

##### L1 结构门

| 参数 | 当前值 |
| --- | ---: |
| `adx_min` | `15` |
| `atr_min` | `0.003` |
| `atr_max` | `0.025` |
| `spread_bps_max` | `0.001` |

##### L2 flow 门

| 参数 | 当前值 |
| --- | ---: |
| `entry_hard_gate_flow_min_pass` | `1` |

至少通过以下一项:

- `cvd_ok`
- `oi_ok`
- `vwap_ok`

##### L3 micro 门

| 参数 | 当前值 |
| --- | ---: |
| `entry_hard_gate_micro_min_pass` | `1` |

至少通过以下一项:

- `depth_ok`
- `imbalance_ok`
- `cvd_momentum_ok`

#### C. pretrade_risk_gate

| 参数 | 当前值 |
| --- | ---: |
| `enabled` | `true` |
| `use_hard_rules_only` | `true` |
| `cvd_veto_enabled` | `false` |
| `atr_ratio_hard_block` | `3.5` |
| `equity_usage_block` | `0.85` |
| `dd_exit_threshold` | `0.10` |
| `entry_block_actions` | `["BLOCK"]` |

结论:

- 当前实盘开仓路径不是 composite 风险分数驱动
- 当前实盘开仓路径是 hard rules only

#### D. execution_quality_1m

当前虽未在配置中显式展开，但 live 路径存在:

- `BLOCK`
- `PASSIVE_ONLY`
- `NEUTRAL`

语义:

- `BLOCK`: 禁止 entry
- `PASSIVE_ONLY`: 只允许被动挂单，禁用 market fallback

### 6.2 开仓后风控

#### 基础保护

| 参数 | 当前值 |
| --- | ---: |
| `stop_loss_pct` | `0.02` |
| `take_profit_pct` | `0.04` |
| `take_profit_pct_levels` | `[0.008, 0.012, 0.02]` |
| `take_profit_reduce_pct_levels` | `[0.25, 0.30, 0.20]` |

#### partial TP

| level | close_ratio | trigger |
| --- | ---: | ---: |
| `0` | `0.30` | `1.0R` |
| `1` | `0.40` | `2.0R` |

#### breakeven

| 参数 | 当前值 |
| --- | ---: |
| `breakeven_enabled` | `true` |
| `breakeven_trigger_pnl_ratio` | `0.008` |
| `breakeven_lock_ratio` | `0.004` |

#### trailing

| 参数 | 当前值 |
| --- | ---: |
| `trailing_stop_enabled` | `true` |
| `trailing_stop_activation_pct` | `0.012` |
| `trailing_stop_atr_multiplier` | `1.0` |
| `trailing_stop_min_distance` | `0.007` |
| `trailing_stop_max_distance` | `0.015` |

#### time exit / fast exit

| 参数 | 当前值 |
| --- | ---: |
| `time_exit_enabled` | `true` |
| `time_exit_minutes` | `30` |
| `time_exit_min_profit_pct` | `0.0035` |
| `fast_exit_enabled` | `true` |
| `fast_exit_direction_lock_bars` | `2` |
| `fast_exit_trap_high_confidence` | `0.85` |

#### protection SLA

| 参数 | 当前值 |
| --- | ---: |
| `protection_sla_seconds` | `300` |
| `protection_sla_force_flatten` | `true` |
| `protection_sla_pnl_grace_threshold` | `-0.005` |
| `protection_sla_api_health_check_before_force` | `true` |

#### conflict protection

close 决策权重:

| 因子 | 权重 |
| --- | ---: |
| `fund_flow` | `0.55` |
| `macd` | `0.30` |
| `kdj` | `0.15` |

## 7. 给 DeepSeek 的审阅重点

建议让 DeepSeek 重点回答 4 个问题:

1. 当前 admitted loss 的主根因，是否就是 shrink family 本身，而不是门槛太高？
2. `flip_bullish_mode = trial_only` 里，哪些 symbol 值得升级为 normal？
3. `green_bar_growing_mode = enabled_with_strict_threshold` 里，哪些 symbol 应保留，哪些应收紧？
4. 当前 `3x/4x/5x` 杠杆和 `20%-30%` 目标仓位虽然机制存在，但真实落地偏低，根因是:
   - watchlist caps
   - session scale
   - capacity crowding
   - 还是 admitted score 不够高？

## 8. 结论

当前实盘开仓链路的主要事实是：

- 多时间框架数据链路在代码上是完整的
- 当前真实前置挡板是:
  - `trial_only`
  - `enabled_with_strict_threshold`
  - pocket override
  - entry_hard_gate
- 当前系统目标和结果的最大偏差不是“不开仓”，而是：
  - 开仓过多
  - shrink family admitted trades 质量差
  - 真实杠杆/仓位利用效率偏低

这份文档可直接作为 DeepSeek 的规则与链路输入。
