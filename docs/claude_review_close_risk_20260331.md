# MACD V2 Close-Risk Review

日期: 2026-03-31

## 1. 文档目的

这份文档面向 Claude 的目标不是只看回测结果，而是让它直接审当前基线策略的完整结构，包括：

- 开仓逻辑
- 评分权重
- 核心门槛
- 仓位与并发约束
- 风控链路
- 止盈止损逻辑
- 平仓优先级
- 本轮 close-risk 实验改动
- 失败归因
- 下一轮更合理的优化方向

## 2. 基线与评审范围

基线配置：

- [trading_config_fund_flow_round3_scale_target.json](D:\AIDCA\AI8\config\trading_config_fund_flow_round3_scale_target.json)

基线回测：

- [v2_summary_20260331_203913.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_203913.json)
- [v2_trades_20260331_203913.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260331_203913.csv)

基线核心指标：

| 指标 | 数值 |
|------|------:|
| trades | 264 |
| win_rate | 77.27% |
| return | +22.19% |
| PF | 2.24 |
| avg_win | +22.35 |
| avg_loss | -33.90 |
| MDD | 3.00% |

本轮目标：

- 不改开仓主逻辑
- 仅从 close-risk / exit 角度优化收益深度
- 目标本来是提高 `avg_win` 或降低 `avg_loss`
- 同时尽量不破坏基线的 `264 / 77.27% / +22.19%`

## 3. 当前基线配置全貌

### 3.1 标的池与交易范围

当前回测 universe 为 37 个 symbol：

`XRPUSDT, SOLUSDT, DOGEUSDT, ADAUSDT, HYPEUSDT, BCHUSDT, LINKUSDT, XLMUSDT, AVAXUSDT, DOTUSDT, LTCUSDT, ZECUSDT, SUIUSDT, TONUSDT, TAOUSDT, AAVEUSDT, ATOMUSDT, ICPUSDT, ETCUSDT, ONDOUSDT, PUMPUSDT, KASUSDT, POLUSDT, WLDUSDT, MORPHOUSDT, ENAUSDT, RENDERUSDT, TRUMPUSDT, ALGOUSDT, APTUSDT, FILUSDT, VETUSDT, ARBUSDT, JUPUSDT, ZROUSDT, JSTUSDT, FETUSDT`

symbol blacklist：

`QNTUSDT, UNIUSDT, NEARUSDT, HBARUSDT, XMRUSDT, TRXUSDT, ENAUSDT, TAOUSDT, LINKUSDT, ARBUSDT, LTCUSDT, HYPEUSDT, KASUSDT, DOTUSDT, FILUSDT, XRPUSDT, APTUSDT`

### 3.2 仓位、并发、杠杆

| 参数 | 值 |
|------|---:|
| `default_target_portion` | 0.18 |
| `max_symbol_position_portion` | 0.25 |
| `max_active_symbols` | 2 |
| `min_open_portion` | 0.06 |
| `reserve_pct` | 20% |
| `min_leverage` | 2 |
| `default_leverage` | 2 |
| `max_leverage` | 2 |
| `max_single_trade_nominal_ratio` | 0.6 |

解释：

- 这是低杠杆、低并发、较强质量优先的版本
- 交易数不是靠高并发堆出来的
- 所以平仓速度一旦变化，就很容易改变“并发槽位释放速度”，进而改变总交易数

### 3.3 评分权重

当前 MACD V2 综合评分权重：

| 权重项 | 值 |
|--------|---:|
| `weight_4h_direction` | 0.40 |
| `weight_1h_direction` | 0.20 |
| `weight_vwap` | 0.20 |
| `weight_15m_entry` | 0.05 |
| `weight_volume` | 0.15 |

解读：

- 当前系统高度依赖 `4h` 主方向
- VWAP 结构是第二大权重
- 15m 只负责细化入场，不负责主方向

### 3.4 entry threshold 与 signal threshold

#### 全局门槛

| 参数 | 值 |
|------|---:|
| `entry_thresholds.default` | 0.845 |
| `entry_thresholds.min_entry_score` | 0.25 |
| `entry_filters.min_signal_score` | 0.870 |
| `entry_filters.min_vwap_score_for_entry` | 0.12 |

#### 分信号门槛

| 信号 | 分数门槛 |
|------|--------:|
| `red_bar_growing` | 0.855 |
| `flip_bullish` | 0.840 |
| `flip_bearish` | 0.840 |
| `stable_bear_continuation` | 0.820 |
| `stable_bull_continuation` | 0.820 |

Claude 需要重点关注：

- 当前存在“entry_thresholds”与“entry_filters”两层门槛
- 两层门槛的覆盖关系是否清晰
- 是否存在重复过滤或阈值冲突

### 3.5 preflip / trial 相关参数

| 参数 | 值 |
|------|---:|
| `enable_4h_preflip_trial_entries` | true |
| `preflip_trial_min_shrink_pct_long` | 0.45 |
| `preflip_trial_min_shrink_pct_short` | 0.22 |
| `preflip_trial_min_signal_score` | 0.70 |
| `preflip_trial_min_vwap_score` | 0.06 |
| `preflip_trial_entry_scale` | 0.35 |
| `preflip_trial_max_leverage` | 2 |

### 3.6 开仓过滤器与质量门

#### 主方向逻辑

| 参数 | 值 |
|------|---:|
| `primary_direction_timeframe` | `4h` |
| `require_1h_confirmation_when_4h_primary` | true |
| `allow_neutral_1h_confirmation` | true |
| `light_1h_confirmation_when_4h_primary` | true |

这意味着：

- 当前不是所有单都要求强 `1h` 共振
- 某些情况下允许 `1h neutral`
- 这是当前 trade coverage 的来源之一

#### hard gates

| 参数 | 值 |
|------|---:|
| `entry_hard_gate_adx_min` | 22 |
| `entry_hard_gate_atr_min` | 0.006 |
| `entry_hard_gate_atr_max` | 0.02 |
| `entry_hard_gate_spread_bps_max` | 0.0008 |
| `entry_hard_gate_flow_min_pass` | 2 |
| `entry_hard_gate_micro_min_pass` | 2 |

### 3.7 VWAP 结构状态与已知 pocket

当前最重要的 `vwap_state`：

- `long_dual_support`
- `short_dual_pressure`
- `long_reclaim_confirmed`
- `short_retest_reject`

其中当前最主要的已知负收益 pocket 是：

| signal_type_1h | vwap_state | count | pnl |
|----------------|------------|------:|----:|
| `red_bar_growing` | `long_dual_support` | 91 | -67.25 |

### 3.8 symbol signal overrides

当前已经被局部禁用的 pocket：

- `DOTUSDT.disable_green_bar_growing = true`
- `ONDOUSDT.disable_green_bar_growing = true`
- `HYPEUSDT.disable_green_bar_growing = true`
- `WLDUSDT.disable_green_bar_growing = true`
- `DOGEUSDT.disable_green_bar_growing = true`
- `LTCUSDT.disable_green_bar_growing = true`

这说明：

- 当前基线本身已经是“清理过亏损 pocket”的版本
- 所以后续更自然的优化方向仍应是 pocket 治理，而不是全局改 exit

## 4. 当前基线的开仓逻辑

为了方便 Claude 审策略因果链，这里按顺序描述当前回测实际开仓流程。

### 4.1 开仓链路顺序

当前可概括为：

1. `4h` 决定主方向
2. `1h` 做确认或轻确认
3. BOLL 结构检查
4. VWAP 结构打分并生成 `vwap_state`
5. 综合评分
6. signal type threshold 过滤
7. hard gates 过滤
8. session risk scale
9. symbol override / watchlist / session tier
10. pretrade_risk_gate
11. 下单

也就是说，当前系统并不是“分数够了就开”。

### 4.2 开仓判定中的关键结构

#### BOLL

当前参数：

- `boll_period = 20`
- `boll_std_dev = 2.0`
- `multiplier_strong = 1.2`
- `multiplier_normal = 1.0`
- `multiplier_weak = 0.6`
- `middle_hard_block = true`

作用：

- 判断价格是否过度偏离中轨
- 判断结构是 `strong / normal / weak`
- 配合 `bb_middle_slope_1h / bb_middle_slope_4h` 评估趋势健康度

#### VWAP

当前参数：

- `vwap_anchor = daily_utc0`
- `structural_vwap_mode = anchored_weekly`
- `structural_vwap_rolling_window = 20`
- `vwap_retest_tolerance = 0.003`
- `vwap_deviation_hard_block = 0.03`

作用：

- 决定 `vwap_score`
- 决定 `vwap_state`
- 决定 `vwap_location_score`

#### 1h / 4h / 15m 分工

- `4h`: 主方向
- `1h`: 辅助方向与确认
- `15m`: 择时与微观入场

因此当前系统本质上不是超短策略，而是中高周期主导、15m 执行的结构策略。

## 5. 当前基线的风控、止盈、止损与平仓链路

### 5.1 当前风控框架

#### pretrade_risk_gate

| 参数 | 值 |
|------|---:|
| `enabled` | true |
| `use_hard_rules_only` | true |
| `atr_ratio_hard_block` | 3.5 |
| `equity_usage_block` | 0.85 |
| `dd_exit_threshold` | 0.1 |
| `entry_block_actions` | `["BLOCK"]` |
| `exit_close_ratio` | 1.0 |

#### protection SLA

| 参数 | 值 |
|------|---:|
| `protection_sla_enabled` | true |
| `protection_sla_seconds` | 300 |
| `protection_sla_force_flatten` | true |
| `protection_sla_pnl_grace_threshold` | -0.005 |
| `protection_sla_api_health_check_before_force` | true |

#### session risk

会对以下状态应用 session risk scale：

- `short_dual_pressure`
- `flip_bullish`
- `long_dual_support`

具体时间段：

- `03:00 ~ 05:30 UTC` -> `position_scale = 0.6`
- `14:30 ~ 16:00 UTC` -> `position_scale = 0.55`

#### vwap score position tier

只对 `long_dual_support` 额外做分层：

| VWAP score 区间 | position_mult |
|----------------|--------------:|
| `0.12 ~ 0.20` | 0.75 |
| `0.20 ~ 0.30` | 0.95 |
| `0.30 ~ 1.00` | 1.05 |

这意味着：

- `long_dual_support` 在基线里已经有额外的仓位抑制
- 所以它当前仍为负，不应简单理解为“只是仓位太大”

### 5.2 当前止盈止损参数

| 参数 | 值 |
|------|---:|
| `stop_loss_pct` | 0.012 |
| `take_profit_pct` | 0.04 |
| `max_stop_loss_pct` | 0.025 |
| `breakeven_enabled` | true |
| `breakeven_trigger_pnl_ratio` | 0.008 |
| `breakeven_lock_ratio` | 0.0025 |

解释：

- 浮盈达到 `0.8%` 后，stop 会抬到成本价上方 `0.25%`
- 所以很多 reason 为 `stop_loss_intrabar` 的成交，实际上已经是保本或微利止损

### 5.3 当前 TP 结构

当前基线主 TP 结构是：

- 固定 TP `4%`
- `take_profit_pct_levels = []`
- `take_profit_reduce_pct_levels = []`

因此：

- 基线并不依赖分层止盈
- 而是依赖单一远端固定 TP
- 当前系统的主利润引擎来自少数命中 4% TP 的仓位

### 5.4 当前 SL 结构

当前配置层显示：

- `use_dynamic_stop = true`
- `boll_stop_atr_multiplier = 0.5`
- `max_stop_loss_pct = 0.025`

回测脚本里的实际逻辑是：

1. 如果 `signal.suggested_stop_price` 存在，则优先用结构 stop
2. 否则回退到 `ATR × 1.5` 与固定 stop pct 的组合
3. 之后再叠加 breakeven 抬 stop

所以当前 stop 不是简单固定 SL，而是：

> 结构 stop 优先 -> ATR/fixed stop 兜底 -> breakeven 进一步抬 stop

### 5.5 当前 trailing 参数

基线 fund_flow 配置里存在 trailing 参数：

| 参数 | 值 |
|------|---:|
| `trailing_stop_enabled` | true |
| `trailing_stop_activation_pct` | 0.012 |
| `trailing_stop_atr_multiplier` | 1.0 |
| `trailing_stop_min_distance` | 0.007 |
| `trailing_stop_max_distance` | 0.015 |
| `trailing_stop_mode` | `dynamic` |

同时还定义了两套 profile：

#### trailing_volatile

| 参数 | 值 |
|------|---:|
| activation | 0.008 |
| atr_multiplier | 0.6 |
| min_distance | 0.005 |
| max_distance | 0.01 |
| breakeven_trigger | 0.005 |
| breakeven_lock | 0.002 |

#### trailing_trending

| 参数 | 值 |
|------|---:|
| activation | 0.018 |
| atr_multiplier | 1.8 |
| min_distance | 0.012 |
| max_distance | 0.03 |
| breakeven_trigger | 0.012 |
| breakeven_lock | 0.003 |

但必须特别说明：

- 在本轮之前，回测链路并没有真正执行 trailing stop
- 本轮为了 close-risk ablation，才把 trailing profile 逻辑接进回测

这意味着 Claude 需要重点审一个问题：

> 当前实盘和旧回测，对 trailing 的执行口径是否一致？

### 5.6 当前回测实际平仓优先级

在 [backtest_macd_v2.py](D:\AIDCA\AI8\scripts\backtest_macd_v2.py) 当前版本中，平仓顺序是：

1. `breakeven` 更新 stop
2. `stop_loss_intrabar`
3. `take_profit_level_intrabar`  
   说明：基线当前为空，不生效
4. `take_profit_intrabar`
5. `4h_shrink_exit`
6. `signal_reverse`
7. `max_hold_time`  
   说明：基线关闭

这一顺序的真实含义是：

- 保本一旦把 stop 抬高，很多仓位会优先以 `stop_loss_intrabar` 名义出场
- 所以 `stop_loss_intrabar` 在报表里混合了：
  - 真止损
  - 保本止损
  - 微利保护出场

这也是为什么不能把 `stop_loss_intrabar` 简单理解成“全是亏损单”。

## 6. 当前基线的盈利与亏损归因

### 6.1 signal type 归因

来自 [v2_summary_20260331_203913.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_203913.json)：

| signal_type | count | win_rate | pnl |
|-------------|------:|---------:|----:|
| `green_bar_growing` | 104 | 79.81% | +1745.38 |
| `flip_bullish` | 22 | 72.73% | +10.07 |
| `red_bar_growing` | 137 | 75.91% | +763.12 |
| `red_bar_shrinking` | 1 | 100% | +6.28 |

解读：

- 主盈利引擎是 `green_bar_growing`
- `flip_bullish` 更像是高胜率但薄利润通道

### 6.2 vwap_state / pocket 归因

从成交明细聚合看，当前最需要关注的是：

| signal_type_1h | vwap_state | count | pnl |
|----------------|------------|------:|----:|
| `red_bar_growing` | `long_dual_support` | 91 | -67.25 |
| `red_bar_growing` | `short_dual_pressure` | 1 | -44.14 |
| `flip_bullish` | `long_reclaim_confirmed` | 22 | +10.07 |

### 6.3 出场原因归因

基线的出场原因：

| reason | count | pnl |
|--------|------:|----:|
| `stop_loss_intrabar` | 218 | -653.53 |
| `signal_reverse` | 2 | -51.60 |
| `stop_loss_intrabar_both_hit` | 1 | -21.19 |
| `4h_shrink_exit` | 16 | +21.91 |
| `backtest_end` | 2 | +93.92 |
| `take_profit_intrabar` | 25 | +3135.35 |

最关键的事实：

- 真正的净利润引擎来自 `25` 笔 `take_profit_intrabar`
- 因此任何会让盈利单更早离场的全局 exit 优化，都天然有较高风险伤害总收益

## 7. 本轮 close-risk 实验到底改了什么

### 7.1 本轮设计意图

原始假设是：

1. `long_dual_support` 的 stop 可能太宽
2. breakeven 对震荡反弹类 pocket 触发太晚
3. 趋势与震荡应使用不同 trailing
4. 某些反弹 pocket 可能不适合追 4% 固定 TP

### 7.2 本轮新增的回测能力

已在 [backtest_macd_v2.py](D:\AIDCA\AI8\scripts\backtest_macd_v2.py) 中新增：

- `vwap_structure_overrides.stop_loss_pct_override`
- `vwap_structure_overrides.breakeven_trigger_pnl_ratio_override`
- `vwap_structure_overrides.breakeven_lock_ratio_override`
- `vwap_structure_overrides.position_scale_override`
- `vwap_structure_overrides.take_profit_pct_override`
- `vwap_structure_overrides.take_profit_pct_levels_override`
- `vwap_structure_overrides.take_profit_reduce_pct_levels_override`
- `trailing_stop_profiles`
- `trailing_stop_profile_map`

测试文件：

- [test_backtest_profit_depth.py](D:\AIDCA\AI8\tests\test_backtest_profit_depth.py)
  - `4 passed`

### 7.3 性能修复

本轮还修复了：

- [macd_strategy_v2.py](D:\AIDCA\AI8\src\fund_flow\macd_strategy_v2.py)

把布林带计算从整段重算改成尾部窗口计算，解决了 full-window 回测卡死的问题。

## 8. 本轮实验配置与详细改动

### 8.1 全通道方案

#### E2b

配置：

- [trading_config_fund_flow_close_risk_e2b.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_close_risk_e2b.json)

改动：

- `long_dual_support`
  - `stop_loss_pct_override = 0.012`
  - `breakeven_trigger_pnl_ratio_override = 0.005`
  - `breakeven_lock_ratio_override = 0.003`
  - `position_scale_override = 0.8`
- `short_dual_pressure`
  - 同上
- `long_reclaim_confirmed`
  - `stop_loss_pct_override = 0.018`
  - `breakeven_trigger_pnl_ratio_override = 0.01`
  - `breakeven_lock_ratio_override = 0.002`
- `short_retest_reject`
  - 同上

不改：

- 固定 TP 仍是 4%
- 不启用新的 trailing profile

#### E3b

配置：

- [trading_config_fund_flow_close_risk_e3b.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_close_risk_e3b.json)

在 E2b 基础上增加：

- `long_dual_support.take_profit_pct_override = 0.02`
- `short_dual_pressure.take_profit_pct_override = 0.02`

#### E4

配置：

- [trading_config_fund_flow_close_risk_e4.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_close_risk_e4.json)

在 E3b 基础上增加：

- `oscillation` trailing profile
- `trending` trailing profile
- `flip` trailing profile
- 通过 `trailing_stop_profile_map` 将:
  - `long_dual_support -> oscillation`
  - `short_dual_pressure -> oscillation`
  - `long_reclaim_confirmed -> trending`
  - `short_retest_reject -> trending`
  - `flip_bullish / flip_bearish -> flip`

### 8.2 仅修 long_dual_support 的方案

#### E2c

配置：

- [trading_config_fund_flow_close_risk_e2c_long_only.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_close_risk_e2c_long_only.json)

只对 `long_dual_support` 做：

- `stop_loss_pct_override = 0.012`
- `breakeven_trigger_pnl_ratio_override = 0.005`
- `breakeven_lock_ratio_override = 0.003`
- `position_scale_override = 0.8`

#### E3c

配置：

- [trading_config_fund_flow_close_risk_e3c_long_only_tp.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_close_risk_e3c_long_only_tp.json)

在 E2c 基础上增加：

- `long_dual_support.take_profit_pct_override = 0.02`

## 9. 本轮实验结果

### 9.1 汇总表

| 方案 | trades | win_rate | return | PF | avg_win | avg_loss | MDD |
|------|-------:|---------:|-------:|---:|--------:|---------:|----:|
| baseline | 264 | 77.27% | +22.19% | 2.24 | 22.35 | -33.90 | 3.00% |
| E2b | 438 | 70.55% | +7.29% | 1.50 | 11.24 | -17.91 | 3.53% |
| E3b | 438 | 70.55% | +7.58% | 1.52 | 11.33 | -17.88 | 3.53% |
| E4 | 414 | 69.81% | +7.42% | 1.49 | 12.08 | -18.78 | 3.52% |
| E2c | 405 | 71.85% | +6.27% | 1.42 | 12.13 | -21.82 | 2.39% |
| E3c | 405 | 71.85% | +6.43% | 1.43 | 12.19 | -21.83 | 2.39% |

对应输出：

- [v2_summary_20260331_231440.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_231440.json)
- [v2_summary_20260331_231654.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_231654.json)
- [v2_summary_20260331_231655.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_231655.json)
- [v2_summary_20260331_231943.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_231943.json)
- [v2_summary_20260331_231947.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_231947.json)

### 9.2 结果解读

#### 发现 1：close-risk 改动并没有“只改单笔收益”

最重要的结构现象是：

- trades 从 `264` 被放大到 `405~438`

说明：

- 提前 exit 改变了资金占用时长
- 在 `max_active_symbols = 2` 的系统里，这等价于改变并发槽位释放速度
- 资金更早释放后，系统接入了更多后续信号

所以这轮实验不是纯粹的“exit quality experiment”，而是：

> exit quality + trade cadence 共同变化

#### 发现 2：avg_loss 变浅了，但 avg_win 被压扁得更厉害

- `avg_loss` 从 `-33.90` 改善到 `-17.88 ~ -21.83`
- 但 `avg_win` 同时从 `22.35` 掉到 `11.24 ~ 12.19`

这说明：

- 当前系统是“靠少数厚利润单拉净值”的结构
- 这类系统不适合做全局提前保护式 exit 收紧

#### 发现 3：long_dual_support 不是简单的 stop 太宽

| 方案 | long_dual_support pnl |
|------|----------------------:|
| baseline | -67.25 |
| E2b | -238.98 |
| E3b | -219.00 |
| E4 | -32.21 |
| E2c | -399.59 |
| E3c | -383.64 |

结论：

- 如果它只是“止损宽”，E2c / E3c 理应改善
- 但结果反而显著恶化
- 所以更像：
  - entry alpha 不足
  - 某些 symbol 上不适配
  - 或 pocket 本身应被更严格筛选

#### 发现 4：trailing 分类把原本的盈利 pocket 也打坏了

E4 里最明显的坏结果：

- `green_bar_growing + short_dual_pressure` 变成 `-111.05`

说明：

- “按 trend / oscillation / flip 分类 trailing”在逻辑上看似合理
- 但在当前真实 alpha 分层上并不准确
- 结果伤害了本来就赚钱的 pocket

## 10. 当前结论

这轮 close-risk 优化没有产出可推广配置。

更重要的结论不是“参数还没调到位”，而是：

1. 当前基线的利润引擎是少数厚利润单，不适合做全局 exit 收紧
2. `long_dual_support` 更像 entry alpha 问题，而不是 exit 参数问题
3. close-risk 改动会通过资金释放速度强烈改变 trade cadence
4. 后续提升收益更合理的方向应回到：
   - symbol 级 pocket 治理
   - entry-side 提纯
   - 局部而非全局的退出优化

## 11. 我希望 Claude 重点回答的问题

请 Claude 重点审核以下几点：

1. 当前基线的开仓链路、权重、门槛、风控顺序是否存在结构性矛盾
2. 当前配置里 trailing 参数一直存在，但旧回测此前未真实执行，这对 live/backtest 一致性意味着什么
3. 为什么“只改平仓风控”会把 `264` 笔放大到 `405~438` 笔，Claude 认为最可能的因果链是什么
4. `long_dual_support` 是否应明确视为 entry alpha 问题，而不是 close-risk 问题
5. 是否建议下一轮直接转向：
   - `symbol × pocket` 归因
   - `long_dual_support` entry gate 提纯
   - symbol-level 禁用
6. 如果仍要做 exit-layer 优化，Claude 会如何设计，才能避免：
   - 伤害厚利润单
   - 放大 trade cadence
   - 破坏已盈利 pocket

## 12. 当前建议

我不建议把这轮任何 close-risk 候选同步到实盘。

更合理的后续顺序是：

1. 保持当前 baseline 实盘配置不动
2. 先做 live/backtest trailing 对齐检查
3. 先做 `symbol × pocket` 归因
4. 先治理 `long_dual_support` 的 entry alpha
5. exit 优化只做局部 pocket，且必须控制 cadence 干扰
