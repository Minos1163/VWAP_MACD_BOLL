# MACD V2 30D 回测归因报告

- 生成时间: 2026-04-05 00:07:11
- 配置文件: `config/trading_config_fund_flow.json`
- 回测摘要: `output/backtest/v2_summary_20260404_235312.json`
- 交易明细: `output/backtest/v2_trades_20260404_235312.csv`
- 开仓归因: `output/backtest/macd_v2_attribution_20260404_30d.json`

## 1. 回测总览

| 指标    | 结果                       |
| ----- | ------------------------ |
| 回测窗口  | 2026-03-05 -> 2026-04-04 |
| 最终权益  | $15,057.27               |
| 收益率   | 50.57%                   |
| 总交易数  | 591                      |
| 胜率    | 82.23%                   |
| 盈利因子  | 2.54                     |
| 平均盈利  | $20.26                   |
| 平均亏损  | $-36.97                  |
| 最大回撤  | 3.71%                    |
| 生成信号数 | 8076                     |

核心结论:
- 30 天窗口结果为 `50.57%`，与此前 +48%~+49% 基线同量级，说明当前配置并没有把收益推到 +200% 轨道。
- 盈利主要来自 `long_reclaim_confirmed` 与 `short_retest_reject` 两个 VWAP 结构；负贡献主要来自 `long_below_both`。
- 胜率 `82.23%` 很高，但平均盈利仅 `$20.26`，平均亏损 `$-36.97`，盈亏比只有约 0.55。

## 2. 亏损归因

### 2.1 亏损退出原因

| reason                                | count | pnl        | avg_pnl  | loss_share |
| ------------------------------------- | ----- | ---------- | -------- | ---------- |
| stop_loss_intrabar                    | 69    | $-2,797.11 | $-40.54  | 72.05%     |
| signal_reverse                        | 11    | $-535.11   | $-48.65  | 13.78%     |
| stop_loss_intrabar_after_tp1_same_bar | 20    | $-243.91   | $-12.20  | 6.28%      |
| backtest_end                          | 2     | $-200.30   | $-100.15 | 5.16%      |
| 4h_shrink_reduce                      | 2     | $-96.49    | $-48.24  | 2.49%      |
| 4h_shrink_exit                        | 1     | $-9.25     | $-9.25   | 0.24%      |

- `stop_loss_intrabar` 单独占 `69/105` 笔亏损，是最主要的亏损来源。
- `stop_loss_intrabar_after_tp1_same_bar` 还有 `20` 笔，说明同 bar 先吃 TP1 再打止损的路径在结果里占比不低。

### 2.2 亏损按信号 / 结构 / 币种拆分

| signal_type       | count | pnl        | avg_pnl |
| ----------------- | ----- | ---------- | ------- |
| red_bar_growing   | 57    | $-2,369.17 | $-41.56 |
| green_bar_growing | 34    | $-1,068.55 | $-31.43 |
| flip_bullish      | 9     | $-259.81   | $-28.87 |
| flip_bearish      | 5     | $-184.64   | $-36.93 |

| vwap_state             | count | pnl        | avg_pnl |
| ---------------------- | ----- | ---------- | ------- |
| long_reclaim_confirmed | 51    | $-2,005.27 | $-39.32 |
| short_retest_reject    | 31    | $-1,135.08 | $-36.62 |
| long_below_both        | 14    | $-614.46   | $-43.89 |
| long_dual_support      | 6     | $-76.62    | $-12.77 |
| short_above_both       | 3     | $-50.75    | $-16.92 |

| symbol     | count | pnl      | avg_pnl  |
| ---------- | ----- | -------- | -------- |
| DOTUSDT    | 4     | $-383.75 | $-95.94  |
| TONUSDT    | 8     | $-312.22 | $-39.03  |
| ZROUSDT    | 6     | $-287.42 | $-47.90  |
| WLDUSDT    | 4     | $-281.58 | $-70.39  |
| ONDOUSDT   | 4     | $-278.48 | $-69.62  |
| BCHUSDT    | 6     | $-272.45 | $-45.41  |
| JSTUSDT    | 6     | $-228.66 | $-38.11  |
| XRPUSDT    | 3     | $-216.42 | $-72.14  |
| KASUSDT    | 4     | $-197.84 | $-49.46  |
| RENDERUSDT | 1     | $-151.84 | $-151.84 |

### 2.3 需要重点盯防的负贡献组合

| signal_type         | vwap_state             | count | pnl      | win_rate |
| ------------------- | ---------------------- | ----- | -------- | -------- |
| red_bar_growing     | long_below_both        | 45    | $-227.48 | 68.89%   |
| green_bar_growing   | long_dual_support      | 11    | $-15.50  | 45.45%   |
| green_bar_growing   | long_below_both        | 1     | $1.51    | 100.00%  |
| green_bar_shrinking | long_reclaim_confirmed | 2     | $28.22   | 100.00%  |
| red_bar_growing     | short_above_both       | 4     | $33.78   | 100.00%  |
| red_bar_shrinking   | short_retest_reject    | 4     | $64.86   | 100.00%  |
| green_bar_growing   | long_reclaim_confirmed | 3     | $85.87   | 100.00%  |
| red_bar_growing     | short_retest_reject    | 9     | $127.13  | 88.89%   |
| green_bar_growing   | short_above_both       | 13    | $148.80  | 76.92%   |
| flip_bearish        | short_retest_reject    | 18    | $334.25  | 72.22%   |

- `red_bar_growing + long_below_both` 单组合净亏 `$-227.48` 左右，是当前最清晰的结构性弱点。

### 2.4 Trial entry 与仓位侧证

| is_trial_entry | count | pnl       | avg_pnl | win_rate |
| -------------- | ----- | --------- | ------- | -------- |
| False          | 452   | $6,018.99 | $13.32  | 84.29%   |
| True           | 139   | $-56.62   | $-0.41  | 75.54%   |

- `trial entry` 分支不是锦上添花，而是净负贡献。它拉低了真实 edge 质量，只是因为缩了到 0.35 仓位所以没有把权益曲线直接打坏。

## 3. 开仓链路

| 阶段                 | 数量    | 说明                              |
| ------------------ | ----- | ------------------------------- |
| analysis_attempts  | 96705 | 进入 15m 时间轴的全部尝试点                |
| analysis_ready     | 93745 | 完成 warmup 且拿到 flow context 的分析点 |
| HOLD               | 91446 | 决策层直接放弃开仓/反手                    |
| BUY/SELL decisions | 2299  | 通过决策链进入执行层的买卖决策                 |
| closed trades      | 591   | 最终形成平仓记录的交易笔数                   |

### 3.1 HOLD 分类

| category                 | count | ratio  | note                              |
| ------------------------ | ----- | ------ | --------------------------------- |
| other_hold               | 85372 | 93.36% | 主要是 no-signal 与 vwap hard block   |
| L1_structure_failed      | 5400  | 5.91%  | 结构/波动/ADX/点差未过硬门槛                 |
| L3_microstructure_failed | 674   | 0.74%  | depth/imbalance/cvd_momentum 全部未过 |

### 3.2 最高频 hold reason

| reason                                                 | count |
| ------------------------------------------------------ | ----- |
| macd_v2_hold_none_score_0.00                           | 38417 |
| macd_v2_hold_vwap_hard_block_score_0.00                | 2469  |
| entry_hard_gate_block:L1_structure_failed:regime_trend | 2039  |
| macd_v2_hold_none_score_0.44                           | 1516  |
| macd_v2_hold_none_score_0.43                           | 1422  |
| macd_v2_hold_none_score_0.42                           | 1334  |
| macd_v2_hold_none_score_0.39                           | 1097  |
| macd_v2_hold_none_score_0.41                           | 1068  |
| macd_v2_hold_none_score_0.46                           | 1028  |
| macd_v2_hold_none_score_0.45                           | 1022  |

- 最高频 hold 是 `macd_v2_hold_none_score_0.00`，说明大部分时点根本没有成型信号，不是简单的门槛太高。
- `vwap_hard_block` 单独拦掉 2469 次。
- L1 结构门槛拦下 5400 次，L3 微结构门槛拦下 674 次，其中 `>=0.85` 的高分候选仍分别被拦下 1737 次和 186 次。

## 4. 门槛分数

### 4.1 打分权重

| weight              | value |
| ------------------- | ----- |
| weight_1h_direction | 0.200 |
| weight_4h_direction | 0.400 |
| weight_vwap         | 0.200 |
| weight_15m_entry    | 0.050 |
| weight_volume       | 0.150 |

### 4.2 关键阈值

| threshold                                 | value |
| ----------------------------------------- | ----- |
| default                                   | 0.8   |
| min_signal_score                          | 0.8   |
| red_bar_growing                           | 0.8   |
| red_bar_shrinking                         | 0.8   |
| flip_bullish                              | 0.76  |
| flip_bearish                              | 0.88  |
| stable_bull_continuation                  | 0.72  |
| stable_bear_continuation                  | 0.72  |
| preflip_trial_min_signal_score            | 0.6   |
| min_vwap_score_for_entry                  | 0.04  |
| flip_bullish_min_vwap_score               | 0.12  |
| flip_bearish_retest_reject_min_vwap_score | 0.16  |
| stable_bull_continuation_min_vwap_score   | 0.1   |

### 4.3 各分数桶通过率

| bucket     | accepted | hold  | total | accept_rate |
| ---------- | -------- | ----- | ----- | ----------- |
| 0.70-0.75  | 222      | 3655  | 3877  | 5.73%       |
| 0.75-0.80  | 203      | 3120  | 3323  | 6.11%       |
| 0.80-0.825 | 233      | 2466  | 2699  | 8.63%       |
| 0.825-0.83 | 55       | 677   | 732   | 7.51%       |
| 0.83-0.845 | 149      | 1696  | 1845  | 8.08%       |
| 0.845-0.85 | 36       | 524   | 560   | 6.43%       |
| <0.70      | 548      | 73714 | 74262 | 0.74%       |
| >=0.85     | 853      | 5594  | 6447  | 13.23%      |

### 4.4 已成交交易分数表现

| bucket    | count | pnl       | avg_pnl | win_rate |
| --------- | ----- | --------- | ------- | -------- |
| <0.80     | 71    | $22.12    | $0.31   | 78.87%   |
| 0.80-0.85 | 147   | $603.50   | $4.11   | 80.27%   |
| 0.85-0.90 | 149   | $2,011.12 | $13.50  | 83.89%   |
| 0.90-0.95 | 113   | $1,270.31 | $11.24  | 79.65%   |
| >=0.95    | 111   | $2,055.31 | $18.52  | 87.39%   |

- 真实成交里没有 `<0.75` 的常规信号；`<0.80` 这部分基本来自 trial entry。
- `>=0.85` 桶贡献了绝大多数净利润，但同样有大量高分候选被 L1 / L3 直接拦掉，说明当前系统不是纯阈值问题，而是结构门槛在主导样本选择。

## 5. 风控逻辑与风控分数

### 5.1 预交易 / 仓位 / 出场风控配置

| module             | key                               | value                 |
| ------------------ | --------------------------------- | --------------------- |
| entry_hard_gate    | enabled                           | True                  |
| ADX floor          | entry_hard_gate_adx_min           | 18                    |
| ATR band           | entry_hard_gate_atr_min / max     | 0.003 / 0.025         |
| spread gate        | entry_hard_gate_spread_bps_max    | 0.001                 |
| pretrade gate      | atr_ratio_hard_block              | 3.5                   |
| pretrade gate      | equity_usage_block                | 0.85                  |
| pretrade gate      | dd_exit_threshold                 | 0.1                   |
| session risk       | high_risk_sessions                | 2                     |
| ATR position scale | bands                             | 4                     |
| dynamic position   | lookback_trades                   | 5                     |
| stop               | stop_loss_pct / max_stop_loss_pct | 0.02 / 0.025          |
| breakeven          | trigger / lock                    | 0.008 / 0.004         |
| trailing           | activation / min / max            | 0.012 / 0.007 / 0.015 |
| time exit          | minutes / min_profit              | 30 / 0.0035           |
| same-bar mode      | same_bar_tp_priority_mode         | tp1_before_stop       |

### 5.2 VWAP 结构缩仓

| vwap_state        | position_scale_override | max_leverage_override |
| ----------------- | ----------------------- | --------------------- |
| long_dual_support | 0.8                     | -                     |
| long_below_both   | 0.6                     | 5                     |
| short_above_both  | 0.6                     | 5                     |

### 5.3 ATR 仓位缩放

| atr_pct_max | scale |
| ----------- | ----- |
| 0.014       | 1.0   |
| 0.018       | 0.85  |
| 0.022       | 0.7   |
| 0.025       | 0.55  |

### 5.4 风控后的实际表现

| session_scale | count | pnl       | avg_pnl | win_rate |
| ------------- | ----- | --------- | ------- | -------- |
| 0.55          | 18    | $309.59   | $17.20  | 94.44%   |
| 0.60          | 68    | $326.43   | $4.80   | 80.88%   |
| 1.00          | 505   | $5,326.35 | $10.55  | 81.98%   |

| dynamic_mult | count | pnl       | avg_pnl | win_rate |
| ------------ | ----- | --------- | ------- | -------- |
| 0.65         | 10    | $-114.68  | $-11.47 | 60.00%   |
| 0.80         | 30    | $-20.66   | $-0.69  | 80.00%   |
| 1.00         | 5     | $58.19    | $11.64  | 60.00%   |
| 1.05         | 105   | $329.19   | $3.14   | 78.10%   |
| 1.15         | 441   | $5,710.33 | $12.95  | 84.13%   |

| leverage | count | pnl       | avg_pnl | win_rate |
| -------- | ----- | --------- | ------- | -------- |
| 2        | 254   | $646.59   | $2.55   | 79.13%   |
| 4        | 252   | $4,652.88 | $18.46  | 86.11%   |
| 5        | 85    | $662.90   | $7.80   | 80.00%   |

- `dynamic_position_mult=1.15` 是主要盈利区；`0.65` 和 `0.80` 说明动态降仓是在亏损簇之后被动收缩，并没有把这部分信号修成正期望。
- `session_position_scale` 压缩后的样本仍然是正收益，说明时段风控在当前窗口不是主要问题。
- `same_bar_tp_priority_mode = tp1_before_stop` 会抬高一部分同 bar 结果，这部分必须在评审时被单独挑战。

## 6. 给 Claude 的重点评审问题

- `trial entry` 分支 139 笔净亏 `$56.62`，是否应直接关闭，还是只保留能被 pocket override 提升的子集。
- `red_bar_growing + long_below_both` 45 笔净亏 `$227.48`，是否说明这类结构的 position_scale 仍偏高，或者本身不该交易。
- 当前 L1/L3 对 `>=0.85` 高分样本仍拦掉 1923 次，高分样本被拦是正确风险控制，还是过度抑制 alpha。
- `same_bar_tp_priority_mode = tp1_before_stop` 是否应改成更保守的 stop-first / no-double-fill 语义重新回测。
- 当前收益主要来自 `long_reclaim_confirmed` 与 `short_retest_reject`，是否应主动砍掉负贡献结构，而不是继续拉高杠杆追 +200%。
