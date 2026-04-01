# MACD V2 — long_dual_support E2 Structural Repair Review

日期：2026-04-01

## 1. 评审目的

这份文档用于请 Claude 审核 `long_dual_support` 的结构性修复方案 `E2`。

当前判断不是：

- `symbol` 黑名单问题
- `exit` 不够聪明
- 单一参数过松

而是：

> `red_bar_growing + long_dual_support` 这个 pocket 的准入逻辑，把“逆势反弹”当成了“可以沿用全局宽松逻辑的普通做多”，导致入口 alpha 失真。

`E2` 的目标是：

- 不做 symbol 筛选
- 不动主盈利 pocket 的 exit 结构
- 只重写 `long_dual_support` 的结构准入标准

核心思路是把它从“普通做多 pocket”重定义为“高门槛反弹确认 pocket”。

---

## 2. 基线问题定义

### 2.1 最新 30 天基线回测

基线配置：
[trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)

基线回测摘要：
[v2_summary_20260401_095344.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_095344.json)

基线成交明细：
[v2_trades_20260401_095344.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_095344.csv)

核心结果：

| 指标 | 基线 |
|---|---:|
| 总交易数 | 499 |
| 胜率 | 83.17% |
| 收益率 | +7.00% |
| Profit Factor | 1.43 |
| 真最大回撤 | 2.87% |

### 2.2 基线主盈利引擎

按 `signal_type_1h` 分解：

| signal_type_1h | count | win_rate | pnl |
|---|---:|---:|---:|
| `green_bar_growing` | 224 | 86.16% | +893.82 |
| `flip_bullish` | 41 | 82.93% | +155.18 |
| `red_bar_growing` | 233 | 80.26% | +21.75 |

这说明：

- 主盈利引擎仍然是 `green_bar_growing`
- 不能为了修 `long_dual_support` 去破坏整个趋势盈利结构

### 2.3 基线坏 pocket

按 `signal_type_1h + vwap_state` 看，最坏 pocket 是：

`red_bar_growing + long_dual_support`

它在最新 30 天里的表现：

| 指标 | 数值 |
|---|---:|
| count | 153 |
| 胜率 | 79.74% |
| pnl | -236.92 |
| avg_win | +6.52 |
| avg_loss | -33.28 |

这是一个典型的：

- 高胜率
- 低赔率
- 负期望

结构。

数学上它的期望值为：

`E = 0.7974 * 6.52 - 0.2026 * 33.28 = -1.54 / trade`

因此当前结论是：

> 该 pocket 的问题是入口质量不够，无法产生足够厚的正向推进，exit 只能修饰结果，不能修出 alpha。

---

## 3. 基线策略方法

### 3.1 开仓方向框架

当前主模式是 `4H primary`。

方向判定主链路：

1. `4H MACD` 给主方向和主评分
2. `1H MACD` 给确认方向
3. `VWAP/BOLL` 给结构位置约束
4. `15M` 给入场时机微调
5. `volume` 给质量补充

全局配置要点：

| 参数 | 值 |
|---|---:|
| `primary_direction_timeframe` | `4h` |
| `require_1h_confirmation_when_4h_primary` | `true` |
| `allow_neutral_1h_confirmation` | `true` |
| `light_1h_confirmation_when_4h_primary` | `true` |
| `enable_4h_preflip_trial_entries` | `true` |

这套设计对趋势类 pocket 是有效的，但对 `long_dual_support` 这种逆势反弹 pocket 过宽。

### 3.2 基线评分权重

来自：
[trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)

`fund_flow.macd_mtf_strategy_v2.scoring_weights`

| 权重项 | 值 |
|---|---:|
| `weight_1h_direction` | 0.20 |
| `weight_4h_direction` | 0.40 |
| `weight_vwap` | 0.20 |
| `weight_15m_entry` | 0.05 |
| `weight_volume` | 0.15 |

这里的结构矛盾在于：

- `long_dual_support` 是逆势反弹 pocket
- 但 `4H` 方向权重仍然占 `0.40`
- 在该 pocket 中，`4H` 越强，实际上往往意味着下跌趋势背景越强

因此它的高分不一定代表更好的多头入场。

### 3.3 基线门槛

#### 全局与分信号阈值

| 参数 | 值 |
|---|---:|
| `entry_thresholds.default` | 0.845 |
| `entry_filters.min_signal_score` | 0.87 |
| `entry_filters.min_vwap_score_for_entry` | 0.12 |
| `entry_thresholds.red_bar_growing` | 0.855 |
| `entry_thresholds.flip_bullish` | 0.84 |
| `entry_thresholds.flip_bearish` | 0.84 |
| `soft_15m_entry_score` | 0.30 |

#### preflip / trial 宽松逻辑

| 参数 | 值 |
|---|---:|
| `preflip_trial_min_shrink_pct_long` | 0.45 |
| `preflip_trial_min_signal_score` | 0.70 |
| `preflip_trial_min_vwap_score` | 0.06 |
| `preflip_trial_entry_scale` | 0.35 |

基线里的问题是：

`long_dual_support` 作为反弹 pocket，不应该共享这种“更早、更松、更试探”的 trial 宽松逻辑。

### 3.4 基线风控与平仓

#### 仓位与杠杆

| 参数 | 值 |
|---|---:|
| `default_target_portion` | 0.18 |
| `max_symbol_position_portion` | 0.25 |
| `max_active_symbols` | 2 |
| `min_leverage / default / max` | 2 / 2 / 2 |

#### 止盈止损

| 参数 | 值 |
|---|---:|
| `stop_loss_pct` | 0.012 |
| `take_profit_pct` | 0.04 |
| `take_profit_pct_levels` | `[0.008, 0.012, 0.02]` |
| `take_profit_reduce_pct_levels` | `[0.25, 0.3, 0.2]` |
| `breakeven_trigger_pnl_ratio` | 0.008 |
| `breakeven_lock_ratio` | 0.0025 |
| `trailing_stop_enabled` | `true` |
| `trailing_stop_activation_pct` | 0.012 |
| `trailing_stop_atr_multiplier` | 1.0 |

#### pretrade risk gate

| 参数 | 值 |
|---|---:|
| `enabled` | `true` |
| `use_hard_rules_only` | `true` |
| `equity_usage_block` | 0.85 |
| `atr_ratio_hard_block` | 3.5 |
| `dd_exit_threshold` | 0.1 |

结论：

- 风控层并不是当前问题主因
- 基线坏点集中在 `long_dual_support` 准入

---

## 4. E2 修复思路

`E2` 配置文件：
[trading_config_fund_flow_ld_support_e2_cvd_vwap_score.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_ld_support_e2_cvd_vwap_score.json)

### 4.1 核心原则

`E2` 不做：

- symbol 黑名单
- 全局 exit 收紧
- 主盈利 pocket 的止盈止损改写

`E2` 只做：

- 针对 `red_bar_growing|long_dual_support` 建独立高标准准入门

### 4.2 E2 pocket 级独立 gate

来自：
`fund_flow.macd_mtf_strategy_v2.entry_filters.pocket_entry_overrides.red_bar_growing|long_dual_support`

| 覆盖项 | E2 值 | 基线 |
|---|---:|---:|
| `allow_neutral_1h_confirmation` | `false` | `true` |
| `require_strict_1h_confirmation` | `true` | `false` |
| `disallow_trial_entry` | `true` | `false` |
| `min_signal_score` | `0.88` | `0.87` |
| `min_vwap_score` | `0.16` | `0.12` |
| `min_entry_score` | `0.50` | 无 pocket 专属 |
| `require_cvd_ok` | `true` | 非必选 |
| `require_cvd_momentum_ok` | `true` | 非必选 |

### 4.3 E2 的方法含义

这组规则把 `long_dual_support` 从“普通做多”改成了“高置信度反弹确认”：

1. `1H` 必须明确转多，不接受 neutral / light
2. 不允许使用 preflip / trial 的宽松逻辑
3. `VWAP` 支撑质量必须更高
4. `signal_score` 需要更高
5. 必须看到 `CVD` 的净买盘确认
6. `15M` 入场强度也必须更高

### 4.4 E2 的仓位处理

来自：
`fund_flow.vwap_structure_overrides.long_dual_support`

| 参数 | 值 |
|---|---:|
| `position_scale_override` | 0.80 |

这不是修复主因，只是风险折扣。
真正起作用的是前面的结构准入门。

### 4.5 E2 没有做的事

`E2` 没有启用 `pocket_scoring_overrides`。

原因是本轮结果显示：

- 单靠结构 gate 已经足够清掉坏 pocket
- 再上 pocket 权重覆盖并没有带来额外增益

---

## 5. 回测数据对照

### 5.1 E2 30 天结果

E2 回测摘要：
[v2_summary_20260401_095720.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_095720.json)

E2 成交明细：
[v2_trades_20260401_095720.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_095720.csv)

| 指标 | 基线 | E2 | 变化 |
|---|---:|---:|---:|
| 总交易数 | 499 | 438 | -61 |
| 胜率 | 83.17% | 84.02% | +0.85pct |
| 收益率 | +7.00% | +12.31% | +5.31pct |
| Profit Factor | 1.43 | 1.76 | +0.33 |
| 真最大回撤 | 2.87% | 2.34% | -0.53pct |

### 5.2 signal_type 分布变化

#### 基线

| signal_type_1h | count | win_rate | pnl |
|---|---:|---:|---:|
| `red_bar_growing` | 233 | 80.26% | +21.75 |
| `green_bar_growing` | 224 | 86.16% | +893.82 |
| `flip_bullish` | 41 | 82.93% | +155.18 |

#### E2

| signal_type_1h | count | win_rate | pnl |
|---|---:|---:|---:|
| `red_bar_growing` | 111 | 86.49% | +679.84 |
| `green_bar_growing` | 274 | 82.12% | +576.03 |
| `flip_bullish` | 52 | 88.46% | +332.47 |

这里说明：

- `red_bar_growing` 被显著提纯
- 从接近零贡献，变成了强正收益来源

### 5.3 VWAP pocket 分布变化

#### 基线

| vwap_state | count | win_rate | pnl |
|---|---:|---:|---:|
| `short_dual_pressure` | 161 | 85% | +671.19 |
| `long_dual_support` | 154 | 80% | -234.98 |
| `long_reclaim_confirmed` | 117 | 81% | +367.49 |
| `short_retest_reject` | 67 | 90% | +279.59 |

#### E2

| vwap_state | count | win_rate | pnl |
|---|---:|---:|---:|
| `short_dual_pressure` | 193 | 81% | +276.43 |
| `long_reclaim_confirmed` | 158 | 88% | +1013.10 |
| `short_retest_reject` | 84 | 83% | +298.89 |
| `long_dual_support` | 3 | 100% | +13.08 |

关键点：

- 基线的坏 pocket 基本就是 `long_dual_support`
- E2 把这个 pocket 从 `154` 笔压到 `3` 笔
- 剩余 `3` 笔为正收益

### 5.4 pocket 级数学对照

#### 基线 `red_bar_growing + long_dual_support`

| 指标 | 数值 |
|---|---:|
| count | 153 |
| win_rate | 79.74% |
| pnl | -236.92 |
| avg_win | +6.52 |
| avg_loss | -33.28 |

#### E2 `red_bar_growing + long_dual_support`

| 指标 | 数值 |
|---|---:|
| count | 0 |
| pnl | 0.00 |

更准确地说：

> E2 并不是把这个 pocket “修成正 alpha”，而是用结构 gate 把当前 30 天样本里的低质量 `long_dual_support` 基本全部拦掉了。

---

## 6. 对 E2 的当前判断

### 6.1 我认为 E2 做对了什么

1. 它沿着“结构规则”修复，而不是做 symbol 鸵鸟政策
2. 它没有破坏全局 exit 层
3. 它没有碰主盈利引擎 `green_bar_growing`
4. 它直接命中了坏 pocket 的准入根因
5. 它让整体收益、胜率、PF、MDD 同时改善

### 6.2 我认为 E2 的潜在风险

1. 它把 `long_dual_support` 在当前 30 天几乎完全清空了
2. 这意味着：
   - 可能说明这个 pocket 在当前市场阶段根本没有足够高质量样本
   - 也可能说明门槛过严，丢失了未来强趋势中的少数好单
3. 因此它还需要更长窗口验证，至少 `90d`

### 6.3 我对 E3 / E4 的判断

`E3`：
[v2_summary_20260401_095907.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_095907.json)

`E4`：
[v2_summary_20260401_100055.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_100055.json)

两者与 `E2` 完全一致。

这说明在当前样本下：

- 结构准入 gate 已经是决定性因素
- 再改 pocket 评分权重或继续提分数上限，没有额外收益

---

## 7. 想请 Claude 重点回答的问题

### Q1

你是否同意：

> `long_dual_support` 当前是 entry alpha 问题，而不是 exit 问题？

### Q2

你是否同意 `E2` 的方法论：

> 用 pocket 级独立 gate 重定义 `long_dual_support`，而不是做 symbol 筛选或全局 exit 收紧？

### Q3

`E2` 当前把 `long_dual_support` 从 `153` 笔压到几乎 `0` 笔。

你认为这更可能表示：

1. 当前市场里这个 pocket 本来就缺少真 alpha  
2. 这组门槛过严，误杀了未来可能有效的好样本  
3. 二者兼有

### Q4

在你看来，`E2` 之后是否还值得做 `pocket_scoring_overrides`？

还是说：

> 既然 `E3` 与 `E2` 完全一致，就说明当前真正有效的是 gate，而不是 score 重权重？

### Q5

如果要避免“把 pocket 清空得太彻底”，你更建议放松哪一项？

候选项：

- `min_signal_score: 0.88`
- `min_vwap_score: 0.16`
- `min_entry_score: 0.50`
- `require_cvd_ok`
- `require_cvd_momentum_ok`
- `require_strict_1h_confirmation`

### Q6

你是否建议在 `E2` 之后继续做 90 天验证，而不是直接同步实盘？

如果做 90 天验证，你建议重点看：

- pocket 是否持续接近 0 笔
- 剩余 `red_bar_growing` 是否稳定转正
- `green_bar_growing` 主引擎是否被间接伤害
- 还是别的指标

---

## 8. 我的暂定结论

当前我的结论是：

> `E2` 是一版有效的结构修复，不是通过回避 symbol，而是通过重写 `long_dual_support` 的准入定义，把它从“宽松反弹尝试”改成了“高置信度反弹确认”。

在 30 天样本里，它的结果明显优于基线：

- 收益从 `+7.00%` 提到 `+12.31%`
- 胜率从 `83.17%` 提到 `84.02%`
- PF 从 `1.43` 提到 `1.76`
- MDD 从 `2.87%` 降到 `2.34%`

但我没有直接把它同步实盘，因为它对 `long_dual_support` 的过滤强到几乎清空该 pocket，仍需更长窗口确认不是过拟合。

---

## 9. 附件建议

建议和这份文档一起发给 Claude 的文件：

- [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)
- [trading_config_fund_flow_ld_support_e2_cvd_vwap_score.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_ld_support_e2_cvd_vwap_score.json)
- [v2_summary_20260401_095344.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_095344.json)
- [v2_trades_20260401_095344.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_095344.csv)
- [v2_summary_20260401_095720.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_095720.json)
- [v2_trades_20260401_095720.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_095720.csv)
- [claude_review_long_dual_support_30d_20260401.md](D:\AIDCA\AI8\docs\claude_review_long_dual_support_30d_20260401.md)
