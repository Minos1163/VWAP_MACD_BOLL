# 最新30天实盘配置回测审计

- 生成时间: 2026-04-05 15:28:36
- 配置文件: `config/trading_config_fund_flow_live_production.json`
- 回测窗口: `2026-03-05T03:00:00` -> `2026-04-04T03:00:00`

## 1. 核心结论

| 指标      | 结果         |
| ------- | ---------- |
| 收益率     | 0.72%      |
| 最终权益    | $10,072.04 |
| 总交易数    | 150        |
| 胜率      | 76.67%     |
| 盈利因子    | 1.17       |
| 平均盈利    | $19.04     |
| 平均亏损    | $-53.29    |
| 盈亏比     | 0.357      |
| 最大回撤    | 8.29%      |
| 生成交易信号数 | 188        |

- 当前实盘配置最近30天回放只拿到 `0.72%`，说明这套参数在最新窗口里没有把高胜率转成足够的净利润。
- 当前窗口是典型的“高胜率低收益”结构：胜率 `76.67%`，但平均亏损 `$-53.29` 约为平均盈利 `$19.04` 的 2.800 倍。
- 多头是主要拖累：按方向拆分时，`long` 净值为 `$-277.87`，`short` 净值为 `$601.82`。
- 最大回撤从 `2026-03-08 19:15:00` 开始，在 `2026-03-18 07:00:00` 触底，直到 `2026-04-02 13:00:00` 才修复。

## 2. 亏损归因

### 2.1 亏损退出原因

| reason                                                                           | count | pnl      | avg_pnl  |
| -------------------------------------------------------------------------------- | ----- | -------- | -------- |
| decision_close:time_exit hold=30m pnl=-0.0220 min_profit=0.0035 flow_expanding=0 | 1     | $-263.11 | $-263.11 |
| stop_loss_intrabar                                                               | 2     | $-229.26 | $-114.63 |
| decision_close:time_exit hold=30m pnl=-0.0142 min_profit=0.0035 flow_expanding=0 | 1     | $-174.27 | $-174.27 |
| decision_close:time_exit hold=30m pnl=-0.0146 min_profit=0.0035 flow_expanding=0 | 1     | $-171.59 | $-171.59 |
| stop_loss_intrabar_after_tp1_same_bar                                            | 8     | $-167.49 | $-20.94  |
| decision_close:time_exit hold=30m pnl=-0.0098 min_profit=0.0035 flow_expanding=0 | 1     | $-122.65 | $-122.65 |
| decision_close:time_exit hold=30m pnl=-0.0061 min_profit=0.0035 flow_expanding=0 | 2     | $-103.71 | $-51.85  |
| decision_close:time_exit hold=30m pnl=-0.0042 min_profit=0.0035 flow_expanding=0 | 2     | $-67.80  | $-33.90  |
| decision_close:time_exit hold=30m pnl=-0.0065 min_profit=0.0035 flow_expanding=0 | 1     | $-61.85  | $-61.85  |
| decision_close:time_exit hold=30m pnl=-0.0046 min_profit=0.0035 flow_expanding=0 | 1     | $-59.73  | $-59.73  |
| decision_close:time_exit hold=30m pnl=-0.0133 min_profit=0.0035 flow_expanding=0 | 1     | $-57.11  | $-57.11  |
| decision_close:time_exit hold=30m pnl=-0.0041 min_profit=0.0035 flow_expanding=0 | 1     | $-53.58  | $-53.58  |
| decision_close:time_exit hold=30m pnl=-0.0036 min_profit=0.0035 flow_expanding=0 | 1     | $-48.31  | $-48.31  |
| decision_close:time_exit hold=30m pnl=-0.0045 min_profit=0.0035 flow_expanding=0 | 1     | $-44.24  | $-44.24  |
| decision_close:time_exit hold=30m pnl=-0.0038 min_profit=0.0035 flow_expanding=0 | 1     | $-42.13  | $-42.13  |
| decision_close:time_exit hold=30m pnl=-0.0121 min_profit=0.0035 flow_expanding=0 | 1     | $-40.15  | $-40.15  |
| decision_close:macd_v2_close_short_1h_flip_bullish                               | 1     | $-37.36  | $-37.36  |
| decision_close:time_exit hold=30m pnl=-0.0092 min_profit=0.0035 flow_expanding=0 | 1     | $-32.91  | $-32.91  |
| decision_close:time_exit hold=30m pnl=-0.0017 min_profit=0.0035 flow_expanding=0 | 1     | $-25.62  | $-25.62  |
| decision_close:time_exit hold=30m pnl=-0.0056 min_profit=0.0035 flow_expanding=0 | 1     | $-24.49  | $-24.49  |
| decision_close:time_exit hold=30m pnl=-0.0015 min_profit=0.0035 flow_expanding=0 | 1     | $-16.99  | $-16.99  |
| decision_close:time_exit hold=30m pnl=-0.0008 min_profit=0.0035 flow_expanding=0 | 2     | $-13.63  | $-6.82   |
| decision_close:time_exit hold=30m pnl=-0.0007 min_profit=0.0035 flow_expanding=0 | 1     | $-4.49   | $-4.49   |
| decision_close:time_exit hold=30m pnl=0.0002 min_profit=0.0035 flow_expanding=0  | 1     | $-2.80   | $-2.80   |

### 2.2 净值按方向 / 信号 / VWAP 结构拆分

| side  | count | pnl      | win_rate |
| ----- | ----- | -------- | -------- |
| long  | 89    | $-277.87 | 66.29%   |
| short | 61    | $601.82  | 91.80%   |

| signal_type_1h    | count | pnl      | win_rate |
| ----------------- | ----- | -------- | -------- |
| red_bar_growing   | 66    | $-142.47 | 66.67%   |
| flip_bullish      | 22    | $-104.90 | 68.18%   |
| green_bar_growing | 62    | $571.32  | 90.32%   |

| vwap_state                          | count | pnl      | win_rate |
| ----------------------------------- | ----- | -------- | -------- |
| long_reclaim_confirmed              | 85    | $-258.05 | 65.88%   |
| short_below_session_above_structure | 25    | $-150.75 | 80.00%   |
| long_above_session_below_structure  | 1     | $-57.11  | 0.00%    |
| long_dual_support                   | 3     | $37.30   | 100.00%  |
| short_under_structure_wait_reject   | 5     | $56.86   | 100.00%  |
| short_retest_reject                 | 31    | $695.70  | 100.00%  |

### 2.3 最差组合与最差币种

| signal_type_1h    | vwap_state                          | count | pnl      | win_rate |
| ----------------- | ----------------------------------- | ----- | -------- | -------- |
| green_bar_growing | short_below_session_above_structure | 25    | $-150.75 | 80.00%   |
| red_bar_growing   | long_reclaim_confirmed              | 62    | $-150.36 | 66.13%   |
| flip_bullish      | long_reclaim_confirmed              | 22    | $-104.90 | 68.18%   |
| red_bar_growing   | long_above_session_below_structure  | 1     | $-57.11  | 0.00%    |
| green_bar_growing | long_reclaim_confirmed              | 1     | $-2.80   | 0.00%    |
| green_bar_growing | long_dual_support                   | 3     | $37.30   | 100.00%  |
| green_bar_growing | short_under_structure_wait_reject   | 5     | $56.86   | 100.00%  |
| red_bar_growing   | short_retest_reject                 | 3     | $65.00   | 100.00%  |
| green_bar_growing | short_retest_reject                 | 28    | $630.70  | 100.00%  |

| symbol     | count | net_pnl  | win_rate |
| ---------- | ----- | -------- | -------- |
| KASUSDT    | 10    | $-400.65 | 50.00%   |
| BCHUSDT    | 4     | $-119.25 | 50.00%   |
| ONDOUSDT   | 3     | $-90.37  | 33.33%   |
| LTCUSDT    | 1     | $-40.15  | 0.00%    |
| TONUSDT    | 2     | $-30.92  | 50.00%   |
| SOLUSDT    | 1     | $-25.62  | 0.00%    |
| DOTUSDT    | 8     | $-2.52   | 62.50%   |
| JUPUSDT    | 1     | $13.07   | 100.00%  |
| TRUMPUSDT  | 11    | $13.66   | 81.82%   |
| RENDERUSDT | 20    | $18.55   | 70.00%   |

| symbol     | loss_count | gross_loss | avg_loss |
| ---------- | ---------- | ---------- | -------- |
| KASUSDT    | 5          | $-473.33   | $-94.67  |
| RENDERUSDT | 6          | $-353.74   | $-58.96  |
| PUMPUSDT   | 5          | $-175.78   | $-35.16  |
| TRUMPUSDT  | 2          | $-171.71   | $-85.85  |
| BCHUSDT    | 2          | $-137.52   | $-68.76  |
| FETUSDT    | 1          | $-110.14   | $-110.14 |
| AVAXUSDT   | 2          | $-106.09   | $-53.05  |
| ONDOUSDT   | 2          | $-101.86   | $-50.93  |
| DOTUSDT    | 3          | $-72.29    | $-24.10  |
| LTCUSDT    | 1          | $-40.15    | $-40.15  |

### 2.4 Trial entry 与分数桶

| is_trial_entry | count | pnl      | win_rate |
| -------------- | ----- | -------- | -------- |
| True           | 52    | $-118.98 | 75.00%   |
| False          | 98    | $442.93  | 77.55%   |

| score_bucket | count | pnl     | win_rate |
| ------------ | ----- | ------- | -------- |
| <0.75        | 0     | $0.00   | nan%     |
| 0.75-0.80    | 36    | $-55.78 | 83.33%   |
| 0.80-0.85    | 7     | $-21.22 | 57.14%   |
| 0.85-0.90    | 43    | $-12.53 | 69.77%   |
| >=0.90       | 64    | $413.48 | 79.69%   |

- `trial entry` 需要单独审计，因为它通常代表方向未完全确认时的试探仓，而不是主 edge。

## 3. 开仓链路

| stage             | passed | blocked | pass_rate |
| ----------------- | ------ | ------- | --------- |
| 1_score_threshold | 8695   | 97902   | 8.16%     |
| 6_L1_structural   | 635    | 3927    | 13.92%    |
| 7_L2_flow         | 4457   | 105     | 97.70%    |
| 8_L3_micro        | 4246   | 316     | 93.07%    |
| 9_pretrade_gate   | 140    | 13      | 91.50%    |
| 10_ai_review      | 87     | 2       | 97.75%    |
| 11_capacity       | 87     | 0       | 100.00%   |
| 12_final_fill     | 74     | 13      | 85.06%    |

| candidate_stage          | count | note                        |
| ------------------------ | ----- | --------------------------- |
| generated_open_candidate | 89    | 决策层给出的开仓候选总数                |
| pre_filter_passed        | 89    | 通过 candidate pre-filter 的候选 |
| ai_shortlisted           | 87    | 进入 AI shortlist 的候选         |
| ai_allowed               | 87    | AI 终审允许继续的候选                |
| capacity_selected        | 87    | 通过容量限制的候选                   |
| final_opened             | 74    | 最终真正开出的仓位                   |

| hold_category            | count  | ratio  |
| ------------------------ | ------ | ------ |
| other_hold               | 102096 | 95.91% |
| L1_structure_failed      | 3928   | 3.69%  |
| L3_microstructure_failed | 316    | 0.30%  |
| L2_flow_failed           | 105    | 0.10%  |

| top_hold_reason                                        | count |
| ------------------------------------------------------ | ----- |
| macd_v2_hold_none_score_0.00                           | 43140 |
| macd_v2_hold_vwap_hard_block_score_0.00                | 4132  |
| macd_v2_hold_none_score_0.27                           | 2143  |
| macd_v2_hold_none_score_0.29                           | 1775  |
| macd_v2_hold_none_score_0.28                           | 1741  |
| macd_v2_hold_none_score_0.84                           | 1397  |
| macd_v2_hold_none_score_0.83                           | 1349  |
| macd_v2_hold_none_score_0.25                           | 1337  |
| macd_v2_hold_none_score_0.26                           | 1285  |
| entry_hard_gate_block:L1_structure_failed:atr_in_range | 1210  |

| final_reject_reason           | count |
| ----------------------------- | ----- |
| nan                           | 76    |
| target_portion_below_min_open | 13    |

| shortlist_block_reason | count |
| ---------------------- | ----- |
| nan                    | 87    |
| ai_shortlist_topn      | 2     |

## 4. 打分权重与门槛

| weight              | value |
| ------------------- | ----- |
| weight_1h_direction | 0.400 |
| weight_4h_direction | 0.200 |
| weight_vwap         | 0.200 |
| weight_15m_entry    | 0.050 |
| weight_volume       | 0.150 |

| entry_threshold                           | value |
| ----------------------------------------- | ----- |
| default                                   | 0.845 |
| min_entry_score                           | 0.250 |
| min_signal_score                          | 0.845 |
| stable_bear_continuation_min_signal_score | 0.820 |
| stable_bull_continuation_min_signal_score | 0.820 |
| red_bar_growing                           | 0.855 |
| flip_bearish                              | 0.840 |
| flip_bullish                              | 0.840 |

| entry_filter                                           | value                                                                                                         |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| primary_direction_timeframe                            | 4h                                                                                                            |
| require_1h_confirmation_when_4h_primary                | True                                                                                                          |
| allow_neutral_1h_confirmation                          | True                                                                                                          |
| light_1h_confirmation_when_4h_primary                  | True                                                                                                          |
| require_15m_confirmation_gate                          | False                                                                                                         |
| soft_15m_entry_score                                   | 0.300                                                                                                         |
| min_signal_score                                       | 0.870                                                                                                         |
| min_vwap_score_for_entry                               | 0.120                                                                                                         |
| preflip_trial_min_signal_score                         | 0.700                                                                                                         |
| preflip_trial_min_vwap_score                           | 0.060                                                                                                         |
| preflip_trial_entry_scale                              | 0.350                                                                                                         |
| trial_short_below_structure_promotion_min_signal_score | 0.790                                                                                                         |
| trial_short_below_structure_promotion_min_vwap_score   | 0.075                                                                                                         |
| stable_bear_continuation_min_vwap_score                | 0.070                                                                                                         |
| stable_bull_continuation_min_vwap_score                | 0.100                                                                                                         |
| flip_bullish_min_vwap_score                            | 0.120                                                                                                         |
| flip_bearish_retest_reject_min_vwap_score              | 0.250                                                                                                         |
| disable_flip_bullish_entries                           | False                                                                                                         |
| disable_red_bar_growing_long_entries                   | False                                                                                                         |
| disable_green_bar_growing_entries                      | False                                                                                                         |
| symbol_signal_overrides_count                          | 6                                                                                                             |
| pocket_entry_override_keys                             | green_bar_growing|short_dual_pressure, red_bar_growing|long_dual_support, red_bar_growing|short_dual_pressure |

- 这里有两个容易混淆的分数门槛层：`entry_thresholds.default` 是策略默认分数阈值，`entry_filters.min_signal_score` 是更外层的过滤配置。两者都要单独看，不能把其中一个当作唯一开仓线。

## 5. 风控与执行现实性

| risk_key                                | value           |
| --------------------------------------- | --------------- |
| entry_hard_gates_enabled                | True            |
| entry_hard_gate_adx_min                 | 22              |
| entry_hard_gate_atr_min                 | 0.006           |
| entry_hard_gate_atr_max                 | 0.02            |
| entry_hard_gate_spread_bps_max          | 0.0008          |
| pretrade_risk_gate.atr_ratio_hard_block | 3.5             |
| pretrade_risk_gate.equity_usage_block   | 0.85            |
| pretrade_risk_gate.dd_exit_threshold    | 0.1             |
| stop_loss_pct                           | 0.02            |
| take_profit_pct                         | 0.04            |
| breakeven_trigger_pnl_ratio             | 0.012           |
| breakeven_lock_ratio                    | 0.0025          |
| time_exit_minutes                       | 30              |
| time_exit_min_profit_pct                | 0.0035          |
| same_bar_tp_priority_mode               | tp1_before_stop |

| stop_loss_config                               | value  |
| ---------------------------------------------- | ------ |
| use_dynamic_stop                               | True   |
| boll_stop_atr_multiplier                       | 0.5    |
| max_stop_loss_pct                              | 0.025  |
| vwap_alert_deviation                           | 0.005  |
| enable_4h_shrink_exit                          | True   |
| exit_4h_shrink_bars                            | 2      |
| exit_4h_min_shrink_pct                         | 0.15   |
| exit_4h_require_profit                         | False  |
| enable_stable_continuation_slow_4h_shrink_exit | True   |
| stable_continuation_exit_4h_shrink_bars        | 3      |
| stable_continuation_exit_4h_min_shrink_pct     | 0.35   |
| shrink_exit_loss_mitigation_enabled            | True   |
| shrink_exit_loss_mitigation_pnl_threshold      | -0.005 |
| shrink_exit_loss_mitigation_exit_ratio         | 0.6    |
| shrink_exit_loss_mitigation_ignore_if_pnl_gt   | 0.01   |

- 当前回放的微结构不是实时盘口回放，而是 `historical proxy`。代理样本数为 `107041`，代理字段为 `{'depth_ratio': 107041, 'imbalance': 107041, 'spread_bps': 107041}`。
- 执行语义审计里，配置错配数为 `0`，但仍有 `{'ALIGNED_WITH_GAPS': 5, 'AMBIGUOUS_PRIORITY': 1, 'MISMATCH_RISK': 2, 'UNVERIFIED': 1}` 这类未闭合的执行风险标签。
- Trailing exists in both live and backtest, but trigger order parity is not proven.
- Backtest intrabar matching is explicit 15m OHLC simulation; live uses exchange protection behavior, so semantics are not identical by construction.
- Protection priority in live is multi-layered and richer than the simplified backtest chain.
- Config-value alignment alone is insufficient for close-risk optimization decisions.
