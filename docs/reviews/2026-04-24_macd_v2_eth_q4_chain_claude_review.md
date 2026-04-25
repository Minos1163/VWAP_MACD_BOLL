# MACD V2 30天回测归因与实盘链路说明（DeepSeek送审版）

日期：2026-04-25

评审基准：

- 回测汇总：`output/backtest/v2_summary_20260425_002453.json`
- 逐笔成交：`output/backtest/v2_trades_20260425_002453.csv`
- 主策略实现：`src/fund_flow/macd_strategy_v2.py`
- 兼容配置实现：`src/config/macd_strategy_v2.py`
- 运行时配置：`config/trading_config_fund_flow.json`

---

## 1. 结论先行

当前 V2 可以继续保留为研究通道，但这份 30 天结果仍然**不能证明 ETH/Q4 做多链路已经验证成功**。

这次用最新代码重跑后的结论是：

- 30 天收益率：`+8.56%`
- 总交易数：`3`
- 胜率：`100%`
- Profit Factor：`Infinity`
- 最大回撤：`6.02%`
- 全部成交都来自 `red_bar_growing short`
- 全部成交的 `vwap_state = vwap_disabled`
- `VWAP` 已经不再参与实际开仓门槛、仓位乘数、方向门和执行惩罚

因此，这份结果的本质不是“VWAP 优化后 ETH 主升段被抓住了”，而是：

1. 当前窗口只打到了 3 笔 `red_bar_growing short`
2. 收益几乎全部由 `POLUSDT` 一笔长持仓贡献
3. ETH/Q4 预翻转路径在这个窗口里**没有形成真实 long 成交**

另外，这次回测还验证了一件重要的代码修正：

- 之前否决统计里出现的 `vwap_hard_block`，并不是 VWAP 真的还在挡单
- 真正原因是 `rsi_gate_fail` 被错误打成了 `VetoType.VWAP_HARD_BLOCK`
- 现在这条假标签已经修掉，最新回测里否决统计变成了真实的 `rsi_gate_fail: 284`

这意味着：**VWAP 的真实决策门槛已经退役，之前回测里残留的 “vwap_hard_block” 统计是代码标签 bug，不是策略口径 bug。**

---

## 2. 30天回测结果归因

### 2.1 总体结果

窗口：`2026-03-01` 到 `2026-03-31`

| 指标 | 数值 |
|---|---:|
| 初始资金 | `10000.00` |
| 最终权益 | `10856.24` |
| 收益率 | `+8.56%` |
| 总交易数 | `3` |
| 胜率 | `100.0%` |
| Profit Factor | `Infinity` |
| 最大回撤 | `6.02%` |
| 最大回撤区间 | `2026-03-23 09:00:00 -> 2026-03-25 16:15:00` |

### 2.2 按交易对归因

| 交易对 | 方向 | 开仓时间 | 平仓时间 | PnL |
|---|---|---|---|---:|
| `POLUSDT` | `short` | `2026-03-06 08:15:00` | `2026-03-31 00:00:00` | `+833.98` |
| `SOLUSDT` | `short` | `2026-03-03 19:30:00` | `2026-03-03 20:00:00` | `+27.65` |
| `SUIUSDT` | `short` | `2026-03-17 19:30:00` | `2026-03-17 19:45:00` | `+3.25` |

结论：

- 总利润几乎全部来自 `POLUSDT`
- `SOLUSDT`、`SUIUSDT` 只是边际补充
- 当前收益结构高度集中，不能视作稳定分散 alpha

### 2.3 按信号族归因

`signal_type_breakdown` 结果只有一类：

- `red_bar_growing`: `3` 笔，`3` 胜，合计 `+864.88`

这说明当前 30 天收益并不是由 `flip_bullish`、`flip_bearish`、Q4 preflip long 或稳定延续链路贡献，而是由最传统的 `red_bar_growing short` 撑起来的。

### 2.4 按执行链路归因

- `signals_generated = 12`
- 实际成交 = `3`
- `entry_degradation_breakdown`: 全部 `direct_fill`
- `entry_tif_breakdown`: 全部 `IOC->IOC`
- `cvd_bonus_breakdown`: 全部 `inactive`
- `cvd_veto_breakdown`: 全部 `inactive`

结论：

- 这批盈利不是 CVD bonus 放大的
- 也不是 CVD veto 过滤后剩下的
- 主要是主链路本身就只放了 3 笔 short

### 2.5 否决归因

最新回测否决统计：

- `rsi_gate_fail: 284`

这个结果非常重要，因为它说明：

- 当前主要拦截器是 `RSI` 门，而不是 VWAP
- 旧版本里出现的 `vwap_hard_block` 统计属于错误标签，不应再用于后续评审

### 2.6 仍需 DeepSeek 重点关注的异常

逐笔成交里仍然存在一个值得继续审查的现象：

- `POLUSDT short`
- 同时 `q4_rsi_lead_preflip_passed = True`
- `score_q4_rsi_lead_preflip = 0.155186...`

这不表示 “Q4 long 已成交”，更准确的解释是：

1. 该 bar 上 Q4 诊断链路被命中过
2. 但最终实际成交方向仍然是 `short`
3. 因此 Q4 字段目前仍然更接近“并行诊断标记”，而不是“最终成交方向的唯一来源”

这也是为什么当前结果**不能被当成 ETH/Q4 long 成功样本**。

---

## 3. VWAP 当前真实口径

本轮代码调整后的目标很明确：

- `VWAP 只保留 telemetry`
- `VWAP 不再参与开仓门槛`
- `VWAP 不再参与仓位乘数`
- `VWAP 不再参与 state-machine entry tier`
- `VWAP 不再参与 red_bar_growing long 的硬拦截`
- `VWAP 不再参与 execution soft penalty`

已经落到代码里的关键点如下。

### 3.1 已退役的门槛

在 `src/fund_flow/macd_strategy_v2.py` 与 `src/config/macd_strategy_v2.py` 中：

- `weight_vwap = 0.0`
- `min_vwap_score_for_entry = 0.0`
- `flip_bullish_min_vwap_score = 0.0`
- `preflip_trial_min_vwap_score = 0.0`
- `trial_short_below_structure_promotion_min_vwap_score = 0.0`
- `stable_bear_continuation_min_vwap_score = 0.0`
- `stable_bull_continuation_min_vwap_score = 0.0`
- `resolve_vwap_score_position_multiplier()` 固定返回 `1.0`
- `_vwap_thresholds_disabled()` 固定返回 `True`

### 3.2 已拆除的真实挡单逻辑

已经从主链路上移除或钝化的项：

- `quadrant_vwap_execution_block`
- `red_bar_growing long` 上的 `vwap_deviation` 硬拦截
- `vwap_execution_penalty_only`
- `flip_bullish / flip_bearish` 的 VWAP context 硬门

### 3.3 保留但仅用于兼容/诊断的字段

下面这些字段还会出现在导出里，但不应该再被解释成“实际门槛”：

- `vwap_state`
- `vwap_score`
- `vwap_execution_state`
- `score_vwap`

其中 `score_vwap` 现在只是兼容字段，语义上等价于：

`score_vwap == score_boll_rsi_resonance`

因此后续评审里，如果还把 `score_vwap` 当成独立的 VWAP 评分槽位，会得出错误结论。

---

## 4. Q1 / Q2 / Q3 / Q4 象限定义

主实现：`src/fund_flow/macd_strategy_v2.py::_classify_market_quadrant()`

判定维度只有两个：

- `macd_line_4h`
- `close_price` 相对 `1H BOLL 中轨`

| 象限 | 条件 | 含义 |
|---|---|---|
| `Q1 / I` | `macd_line_4h > 0` 且 `close >= bb_middle_1h` | 4H 多头区，价格也在 1H 中轨上方 |
| `Q2 / II` | `macd_line_4h > 0` 且 `close < bb_middle_1h` | 4H 多头区，但价格回踩 1H 中轨下方 |
| `Q3 / III` | `macd_line_4h < 0` 且 `close < bb_middle_1h` | 4H 空头区，价格也在 1H 中轨下方 |
| `Q4 / IV` | `macd_line_4h < 0` 且 `close >= bb_middle_1h` | 4H 仍偏空，但价格已重新站回 1H 中轨上方 |

Q4 的策略意义最强：

- 4H 还没正式翻多
- 1H 价格结构先修复
- RSI 可能已经领先
- 这是 V2 里专门为 `RSI 领先预翻转做多` 预留的研究区

---

## 5. 实盘开仓链路

下面这部分不是对 CSV 的二次猜测，而是直接来自当前代码链路。

### 5.1 多时间框架输入

策略同时使用：

- `4H MACD`：主趋势、象限、shrink
- `1H MACD`：当前信号族
- `15M MACD`：执行时机
- `1H / 4H BOLL`：位置结构、中轨、带宽
- `1H / 4H RSI`：RSI gate 与 Q4 preflip
- `ADX / volume_ratio`：趋势强度与量能确认

### 5.2 1H 信号族判定

主链路识别的 1H 家族包括：

- `flip_bullish`
- `flip_bearish`
- `red_bar_growing`
- `green_bar_growing`
- `red_bar_shrinking`
- `green_bar_shrinking`

这一层决定后面 1H 分值上限和可用分支。

### 5.3 方向与象限

主方向规则：

- 默认以 `4H` 为主时间框架
- `1H` 做确认
- 若 `trade_direction is None` 且 `q4_rsi_lead_preflip_passed=True`，允许 Q4 路径把方向覆盖成 `long`

当前 entry tier 已经不再看 VWAP，只看：

| 条件 | entry_tier |
|---|---|
| `Q1/Q3 + flip_bullish/flip_bearish` | `tier1` |
| `Q2/Q4 + flip_bullish/flip_bearish` | `tier2` |
| `Q1/Q3 + red/green_bar_growing` | `tier2` |
| `Q1/Q3 + red/green_bar_shrinking` | `tier2` |
| 其他 | `blocked` |

### 5.4 BOLL 结构与 RSI gate

主链路会先过：

1. `check_boll_structure()`
2. `check_boll_deviation_veto()`
3. `RSI` 分析与 `RSI gate`
4. `signal_whitelist`

当前主要否决来源已经确认是 `RSI gate`，而不是 VWAP。

### 5.5 Pocket entry requirements

当前 pocket entry override 仍然存在，但 `VWAP` floor 已被统一压到 `0.0`。

`resolve_pocket_entry_requirements()` 当前实际返回的关键字段：

- `signal_score_threshold`
- `min_vwap_score_for_entry = 0.0`
- `min_entry_score`
- `allow_neutral_1h_confirmation`
- `require_strict_1h_confirmation`
- `disallow_trial_entry`

这意味着 pocket override 现在仍可提升：

- 最低信号分
- 最低 entry score
- 1H 确认严格度

但不能再通过 `min_vwap_score` 重新点亮旧 VWAP 门槛。

---

## 6. 详细打分与权重

### 6.1 当前运行权重

来自 `config/trading_config_fund_flow.json`

| 评分项 | 当前权重 |
|---|---:|
| `weight_1h_direction` | `0.40` |
| `weight_4h_direction` | `0.20` |
| `weight_4h_enhancement` | `0.10` |
| `weight_boll_position` | `0.25` |
| `weight_boll_rsi_resonance` | `0.0` |
| `weight_vwap` | `0.0` |
| `weight_15m_entry` | `0.05` |
| `weight_volume` | `0.15` |

当前总分口径：

```text
total_score
= score_1h
+ score_4h
+ score_4h_enhancement
+ score_boll_position
+ score_boll_rsi_resonance
+ score_15m
+ score_volume
+ score_q4_rsi_lead_preflip
- overheat_penalty
```

其中：

- `score_vwap` 只是兼容字段，当前等于 `score_boll_rsi_resonance`
- `VWAP` 本身不再单独提供有效分数槽位

### 6.2 基础阈值

当前运行时主阈值：

| 项目 | 数值 |
|---|---:|
| `min_entry_score` | `0.25` |
| `min_signal_score` | `0.55` |
| `red_bar_growing` | `0.55` |
| `flip_bearish` | `0.55` |
| `flip_bullish` | `0.55` |
| `stable_bear_continuation_min_signal_score` | `0.60` |
| `stable_bull_continuation_min_signal_score` | `0.60` |
| `preflip_trial_min_signal_score` | `0.80` |

### 6.3 各评分槽位的实际含义

`score_1h`

- flip 家族给最高信用
- growing 家族给次一级信用
- 若只是方向一致但不是强家族，用 `signal_strength_1h`

`score_4h`

- 主趋势分
- 4H 与成交方向一致时提供主信用
- Q4 preflip 试单时，会改成 `preflip_4h_strength`

`score_4h_enhancement`

- 4H 增强确认分
- 用于趋势强化而非主趋势本体

`score_boll_position`

- 看价格在 1H 布林带中的相对位置
- 多头偏好中轨上方但不过热
- 空头做镜像

`score_boll_rsi_resonance`

- 当前真正取代旧 `VWAP` 评分位的共振分
- 核心取决于：
  - 价格相对 1H/4H BOLL 中轨的位置
  - 中轨斜率
  - `RSI_1h`
  - `RSI_4h`

`score_q4_rsi_lead_preflip`

- 仅 Q4 预翻转路径通过时才会加
- 当前配置上限：`0.18`

---

## 7. Q4 / IV -> Q1 / I 预翻转做多路径

当前配置：

| 项目 | 数值 |
|---|---:|
| `enable_q4_rsi_lead_preflip_long` | `true` |
| `q4_rsi_lead_preflip_min_4h_shrink_pct` | `0.75` |
| `q4_rsi_lead_preflip_rsi_1h_min` | `50.0` |
| `q4_rsi_lead_preflip_rsi_4h_min` | `50.0` |
| `q4_rsi_lead_preflip_rsi_4h_near_buffer` | `15.0` |
| `q4_rsi_lead_preflip_bonus_score` | `0.18` |
| `q4_rsi_lead_preflip_entry_scale` | `0.35` |
| `q4_rsi_lead_preflip_strict_eth_only` | `false` |
| `q4_rsi_lead_preflip_allowed_categories` | `major_large_cap` |
| `q4_rsi_lead_preflip_min_atr_pct_1h` | `0.005` |
| `q4_rsi_lead_preflip_max_atr_pct_1h` | `0.050` |
| `enable_q4_rsi_lead_preflip_hold` | `true` |
| `q4_rsi_lead_preflip_hold_exit_metric` | `rsi_21` |
| `q4_rsi_lead_preflip_hold_rsi_4h_exit_threshold` | `70.0` |
| `q4_rsi_lead_preflip_hold_rsi_4h_pullback` | `3.0` |

当前通过条件可以概括为：

1. 必须处于 `Q4 / IV`
2. `signal_type_1h == red_bar_growing`
3. `macd_line_4h < 0`
4. `4H shrink_pct >= 0.75`
5. 价格重新站上 `1H BOLL 中轨`
6. `RSI_1h >= 50`
7. `RSI_4h` 至少接近 50
8. 符号满足当前准入条件

但本轮回测的真实成交告诉我们：

- Q4 路径字段已经会输出
- 但最终还没有形成 ETH long 成交样本
- 因此它仍然是研究通道，不是已验证 alpha

---

## 8. 仓位管理与风控

### 8.1 仓位框架

当前运行时限制：

| 项目 | 数值 |
|---|---:|
| `max_positions` | `5` |
| `default_target_portion` | `0.22` |
| `max_symbol_position_portion` | `0.30` |
| `min_open_portion` | `0.03` |
| `reserve_pct` | `0.20` |

### 8.2 杠杆框架

| 项目 | 数值 |
|---|---:|
| `default_leverage` | `4x` |
| `max_leverage` | `5x` |
| `preflip_trial_max_leverage` | `3x` |
| `boll_strong_trend_leverage_mult` | `0.8` |

### 8.3 止损止盈

| 项目 | 数值 |
|---|---:|
| `stop_loss_pct` | `2.0%` |
| `take_profit_pct` | `5.0%` |
| `take_profit_pct_levels` | `[1.5%, 2.5%, 4.0%]` |
| `take_profit_reduce_pct_levels` | `[20%, 25%, 20%]` |
| `breakeven_trigger_pnl_ratio` | `0.8%` |
| `breakeven_lock_ratio` | `0.4%` |
| `max_stop_loss_pct` | `2.5%` |

### 8.4 额外风险控制

- `enable_rsi_hard_block = true`
- `boll_middle_hard_block = true`
- `overheat_growing_penalty = 0.12`
- `shrink_exit_loss_mitigation_enabled = true`
- `enable_4h_shrink_exit = true`
- `enable_stable_continuation_slow_4h_shrink_exit = true`

当前这套风控里，真正还在起主作用的是：

- BOLL 结构
- RSI gate
- 4H shrink exit
- 通用仓位/杠杆约束

不是 VWAP。

---

## 9. 本轮给 DeepSeek 的明确问题

建议 DeepSeek 重点审三件事：

1. `POLUSDT short` 同时带 `q4_rsi_lead_preflip_passed=True`，这条并行诊断链是否该继续保留在同一条成交导出里，还是需要单独口径拆分。
2. 当前 30 天只有 `3` 笔交易，且全部是 `red_bar_growing short`，这是否意味着 V2 主链路在当前参数下机会集过窄。
3. 在 `VWAP` 彻底退役之后，Q4 preflip long 仍未形成实际样本，下一步应该优先优化的是：
   - Q4 long 的触发密度
   - Q4 long 的最终方向切换时点
   - 还是 RSI gate 对 Q4 路径的压制强度

---

## 10. 送审摘要

如果只保留一句话给 DeepSeek：

**V2 当前已经把 VWAP 从真实决策门槛中拆掉，30 天结果仍然只由 3 笔 `red_bar_growing short` 支撑，ETH/Q4 long 还没有拿到真实成交样本；因此这条链路目前只能继续做研究，不适合实盘自动切换。**
