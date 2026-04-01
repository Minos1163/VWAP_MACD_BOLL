# MACD V2 Profit Depth Review

日期: 2026-03-31

## 1. 本轮目标

在不牺牲当前高频基线的前提下，提升单位盈利厚度，核心目标是:

- 开仓量尽量维持在当前基线附近
- 胜率守住 `75%+`
- 改善 `avg_win / avg_loss`
- 把 30 天收益从 `+22.19%` 往 `+26%~+30%` 推进

## 2. 基线回测

基线配置:
- [trading_config_fund_flow_round3_scale_target.json](D:\AIDCA\AI8\config\trading_config_fund_flow_round3_scale_target.json)

基线输出:
- [v2_summary_20260331_203913.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_203913.json)
- [v2_trades_20260331_203913.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260331_203913.csv)

基线指标:

| 指标 | 数值 |
|------|------|
| 30 天交易数 | 264 |
| 胜率 | 77.27% |
| 收益率 | +22.19% |
| Profit Factor | 2.24 |
| 真 MDD | 3.00% |
| 平均盈利 | +22.35 USDT |
| 平均亏损 | -33.90 USDT |

核心结构问题:

- `204` 笔盈利对 `60` 笔亏损，命中率很高
- 但 `avg_win / avg_loss = 22.35 / 33.90 = 0.66`
- 正期望主要依赖高胜率，而不是赔率质量

## 3. 亏损归因

### 3.1 主要亏损 symbol

来自基线成交明细 [v2_trades_20260331_203913.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260331_203913.csv):

| symbol | count | pnl |
|--------|------:|----:|
| SOLUSDT | 6 | -140.29 |
| BCHUSDT | 10 | -134.94 |
| ICPUSDT | 6 | -95.75 |
| FILUSDT | 3 | -79.05 |

### 3.2 主要亏损通道

按 `signal_type_1h + vwap_state` 聚合:

| signal_type_1h | vwap_state | count | pnl |
|----------------|------------|------:|----:|
| red_bar_growing | long_dual_support | 91 | -67.25 |
| red_bar_growing | short_dual_pressure | 1 | -44.14 |
| flip_bullish | long_reclaim_confirmed | 22 | +10.07 |

关键结论:

- 真正的主亏损口袋是 `red_bar_growing + long_dual_support`
- 这个通道有 `73.63%` 的高胜率，但净 PnL 仍为负
- 说明问题不是方向错，而是退出结构不匹配

### 3.3 出场归因

按 `reason` 聚合:

| reason | count | pnl |
|--------|------:|----:|
| stop_loss_intrabar | 218 | -653.53 |
| signal_reverse | 2 | -51.60 |
| stop_loss_intrabar_both_hit | 1 | -21.19 |
| 4h_shrink_exit | 16 | +21.91 |
| backtest_end | 2 | +93.92 |
| take_profit_intrabar | 25 | +3135.35 |

结论:

- 真正的大利润来自 `25` 笔 `take_profit_intrabar`
- 大多数“赢单”实际上是小利润离场，不够厚
- 主拖累仍是大量 `stop_loss_intrabar`

## 4. 本轮优化假设

围绕 `long_dual_support` 做专项修复:

1. 更早 breakeven
2. 更紧 stop loss
3. 更小 position scale
4. 三档 partial TP 主动落袋

## 5. 代码与配置改动

### 5.1 代码

已在 [backtest_macd_v2.py](D:\AIDCA\AI8\scripts\backtest_macd_v2.py) 增加 `vwap_structure_overrides` 支持:

- `position_scale_override`
- `stop_loss_pct_override`
- `take_profit_pct_levels_override`
- `take_profit_reduce_pct_levels_override`
- `breakeven_trigger_pnl_ratio_override`
- `breakeven_lock_ratio_override`

并新增回归测试:
- [test_backtest_profit_depth.py](D:\AIDCA\AI8\tests\test_backtest_profit_depth.py)

### 5.2 候选配置

- [trading_config_fund_flow_profit_depth_e1_partial_tp.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_profit_depth_e1_partial_tp.json)
- [trading_config_fund_flow_profit_depth_e2_partial_tp_be.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_profit_depth_e2_partial_tp_be.json)
- [trading_config_fund_flow_profit_depth_e3_partial_tp_be_sl.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_profit_depth_e3_partial_tp_be_sl.json)
- [trading_config_fund_flow_profit_depth_e2b_be_only.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_profit_depth_e2b_be_only.json)
- [trading_config_fund_flow_profit_depth_e3b_be_sl_only.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_profit_depth_e3b_be_sl_only.json)

## 6. 已完成 ablation

### E1: partial TP only

输出:
- [v2_summary_20260331_213818.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_213818.json)
- [v2_trades_20260331_213818.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260331_213818.csv)

结果:

| 指标 | 基线 | E1 |
|------|------:|---:|
| trades | 264 | 529 |
| win_rate | 77.27% | 88.66% |
| return | +22.19% | +17.54% |
| PF | 2.24 | 2.01 |
| avg_win | +22.35 | +8.80 |
| avg_loss | -33.90 | -34.28 |
| MDD | 3.00% | 2.24% |

结论:

- partial TP 显著提高了胜率与回撤表现
- 但平均盈利大幅变薄，收益下降
- 且成交条数从 `264` 放大到 `529`

### E2: partial TP + long_dual_support 提前保本

输出:
- [v2_summary_20260331_213807.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_213807.json)
- [v2_trades_20260331_213807.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260331_213807.csv)

结果:

| 指标 | 基线 | E2 |
|------|------:|---:|
| trades | 264 | 570 |
| win_rate | 77.27% | 85.79% |
| return | +22.19% | +17.30% |
| PF | 2.24 | 2.01 |
| avg_win | +22.35 | +8.54 |
| avg_loss | -33.90 | -25.68 |
| MDD | 3.00% | 2.17% |

专项结果:

- `red_bar_growing + long_dual_support` 从 `-67.25` 提升到 `+36.09`

结论:

- 提前保本这条专项修复是有效的
- 但仍然被 partial TP 的“利润切薄 + 成交放大”副作用盖掉了整体收益

### E3: E2 + tighter SL + smaller scale

输出:
- [v2_summary_20260331_213808.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_213808.json)
- [v2_trades_20260331_213808.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260331_213808.csv)

结果:

| 指标 | 基线 | E3 |
|------|------:|---:|
| trades | 264 | 570 |
| win_rate | 77.27% | 85.79% |
| return | +22.19% | +17.44% |
| PF | 2.24 | 2.07 |
| avg_win | +22.35 | +8.26 |
| avg_loss | -33.90 | -24.04 |
| MDD | 3.00% | 2.04% |

专项结果:

- `red_bar_growing + long_dual_support` 保持转正，为 `+28.93`
- 平均亏损进一步收浅

结论:

- `long_dual_support` 的结构性止损/保本修复方向正确
- 但整体收益仍没有超过基线

## 7. 关键结论

### 7.1 已验证有效的结论

- `long_dual_support` 确实是主要亏损口袋
- 更早 breakeven 能把该通道从负收益拉回小幅正收益
- 更紧 stop 和更小仓位可以进一步改善 `avg_loss` 和 MDD

### 7.2 当前不能直接推广的部分

- `partial TP` 在当前回测口径下会把部分平仓记录拆成多笔成交
- 这会把 `trade_count` 从 `264` 放大到 `529/570`
- 因而和基线“每月开仓量”不能直接同口径比较
- 同时它也明显压薄了 `avg_win`

## 8. 当前阻塞

为了做真正可比的验证，我额外准备了:

- [trading_config_fund_flow_profit_depth_e2b_be_only.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_profit_depth_e2b_be_only.json)
- [trading_config_fund_flow_profit_depth_e3b_be_sl_only.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_profit_depth_e3b_be_sl_only.json)

它们只保留 `long_dual_support` 的 breakeven / SL / scale 修复，不启用新的 partial TP 层级，理论上最适合和基线比较。

但当前 full-window 回测在这两份配置上出现本地性能/稳定性阻塞:

- 运行持续 CPU-bound
- 未在合理时间内产出 summary 文件
- faulthandler 抓到热点在 [macd_strategy_v2.py](D:\AIDCA\AI8\src\fund_flow\macd_strategy_v2.py) 的布林带计算路径

因此，这两组“最关键的可比实验”还没有可靠结果，不适合直接上线。

## 9. 我希望 Claude 审核的重点

请 Claude 重点判断:

1. 在当前 high-frequency 配置下，`partial TP` 是否本质上会削弱利润厚度，而不是提升收益
2. `long_dual_support` 的最佳修复顺序是否确实应该是:
   `更早 breakeven -> 更紧 SL -> 更小仓位`
3. 是否应该把 `long_dual_support` 和其他趋势型通道完全拆成不同的退出框架
4. 在不降低当前 `264 trades / 77.27% WR` 基线的前提下，优先应该推广哪一项:
   - `long_dual_support` 提前保本
   - `long_dual_support` 更紧止损
   - symbol-specific 风控
   - 重新设计 partial TP 记账/统计口径
5. 当前 `avg_win` 太薄的根因，更像是:
   - 提前离场过多
   - partial TP 切得太碎
   - long_dual_support 类型本身不适合追求 4% TP
   - 还是 trailing/breakeven 的触发时机不对

## 10. 当前建议

在拿到 E2b/E3b 的可靠结果前，我不建议直接把这轮 profit-depth 配置同步到实盘。

当前最稳妥的下一步是:

1. 保留现有达标实盘配置不动
2. 单独解决 E2b/E3b 的 full-window 回测性能问题
3. 用同口径结果决定是否只推广 `long_dual_support` 风控修复
4. 暂不把新的 partial TP 直接上线
