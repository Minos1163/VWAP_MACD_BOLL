# MACD V2 E2 — 30 Day Pocket Attribution Review

日期：2026-04-01

配置文件：
[trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)

30 天回测摘要：
[v2_summary_20260401_104001.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_104001.json)

30 天成交明细：
[v2_trades_20260401_104001.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_104001.csv)

E2 候选配置：
[trading_config_fund_flow_ld_support_e2_cvd_vwap_score.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_ld_support_e2_cvd_vwap_score.json)

之前的 E2 结构修复评审稿：
[claude_review_long_dual_support_e2_20260401.md](D:\AIDCA\AI8\docs\claude_review_long_dual_support_e2_20260401.md)

## 1. 这份文档的目的

这份文档用于请 Claude 基于 **当前实盘配置** 的最新 30 天结果，重新审阅 pocket 层面的收益结构。

这次不是再讨论：

- 是否做 symbol 黑名单
- 是否继续全局收紧 exit
- 是否继续做“更聪明的平仓”

而是想请 Claude 评估下面这件事是否已经被数据证实：

> `long_dual_support` 的核心问题是 entry alpha，而 E2 用 pocket 级独立 gate 修复这个问题是有效的。

同时，我也希望 Claude 帮我判断：

- 当前 E2 是否已经足够稳健，适合继续作为 production 基线
- 在 `long_dual_support` 基本被清理后，下一轮盈利优化应该优先打哪个 pocket

## 2. 本次 30 天结果

回测窗口：

- `2026-03-02 00:00:00`
- `2026-04-01 23:59:59`

核心指标：

| 指标 | 数值 |
|---|---:|
| 总交易数 | 438 |
| 胜率 | 84.02% |
| 收益率 | +12.31% |
| Profit Factor | 1.76 |
| 真最大回撤 | 2.34% |
| 平均盈利 | +10.07 |
| 平均亏损 | -30.04 |
| 初始资金 | 10,000 |
| 最终权益 | 11,230.97 |

与 E2 提升时的目标结果一致：

- `499 -> 438` 笔
- `83.17% -> 84.02%`
- `+7.00% -> +12.31%`
- `MDD 2.87% -> 2.34%`

说明当前 production 文件与 E2 candidate 的行为是一致的，不是偶然重现。

## 3. 当前 production 的策略方法

### 3.1 开仓方向框架

当前仍然是 `MACD MTF + VWAP + BOLL` 体系。

主判定链路：

1. `4H` 负责主方向与主评分
2. `1H` 负责方向确认
3. `VWAP` 负责结构位置
4. `15M` 负责入场时机
5. `volume` 负责质量补充

关键配置：

| 项目 | 值 |
|---|---:|
| `primary_direction_timeframe` | `4h` |
| `require_1h_confirmation_when_4h_primary` | `true` |
| `allow_neutral_1h_confirmation` | `true` |
| `light_1h_confirmation_when_4h_primary` | `true` |
| `enable_4h_preflip_trial_entries` | `true` |

### 3.2 评分权重

| 权重项 | 值 |
|---|---:|
| `weight_4h_direction` | 0.40 |
| `weight_4h_enhancement` | 0.10 |
| `weight_1h_direction` | 0.20 |
| `weight_vwap` | 0.20 |
| `weight_15m_entry` | 0.05 |
| `weight_volume` | 0.15 |

### 3.3 全局门槛

| 参数 | 值 |
|---|---:|
| `long_open_threshold` | 0.10 |
| `short_open_threshold` | 0.08 |
| `close_threshold` | 0.30 |
| `min_signal_score` | 0.845 |
| `entry_filters.min_signal_score` | 0.87 |
| `min_vwap_score_for_entry` | 0.12 |
| `min_entry_score` | 0.25 |

### 3.4 仓位与风控

| 参数 | 值 |
|---|---:|
| `default_target_portion` | 0.18 |
| `max_symbol_position_portion` | 0.25 |
| `max_active_symbols` | 2 |
| `min/default/max leverage` | 2 / 2 / 2 |
| `stop_loss_pct` | 0.012 |
| `take_profit_pct` | 0.04 |
| `take_profit_pct_levels` | `[0.008, 0.012, 0.02]` |
| `take_profit_reduce_pct_levels` | `[0.25, 0.3, 0.2]` |
| `breakeven_trigger_pnl_ratio` | 0.008 |
| `breakeven_lock_ratio` | 0.0025 |
| `pretrade_risk_gate.enabled` | `true` |
| `pretrade_risk_gate.use_hard_rules_only` | `true` |

结论：

- 当前问题不再是“风控不够”
- 主要收益变化来自结构准入变化

## 4. E2 的唯一结构改动

这次 production 是由 E2 candidate 提升而来。

除 metadata 外，production 与候选配置一致。关键新增只有两类：

### 4.1 pocket_entry_overrides

路径：
`fund_flow.macd_mtf_strategy_v2.entry_filters.pocket_entry_overrides`

当前只定义了一个 pocket：

`red_bar_growing|long_dual_support`

其规则为：

| 覆盖项 | 值 |
|---|---:|
| `allow_neutral_1h_confirmation` | `false` |
| `require_strict_1h_confirmation` | `true` |
| `disallow_trial_entry` | `true` |
| `min_signal_score` | `0.88` |
| `min_vwap_score` | `0.16` |
| `min_entry_score` | `0.50` |
| `require_cvd_ok` | `true` |
| `require_cvd_momentum_ok` | `true` |

### 4.2 vwap_structure_overrides

路径：
`fund_flow.vwap_structure_overrides`

当前只定义：

| vwap_state | 覆盖项 |
|---|---|
| `long_dual_support` | `position_scale_override = 0.80` |

### 4.3 方法含义

这不是 symbol 筛选，也不是 exit 技巧。

它的本质是把：

`red_bar_growing + long_dual_support`

从“普通做多”改成：

> 只有在 1H 已明确转多、VWAP 支撑质量更强、15M 入场更强、且 CVD 有确认时，才允许入场的高标准反弹 pocket。

## 5. 最新 30 天的 pocket 归因

### 5.1 按 `signal_type_1h × vwap_state` 归因

| pocket | count | win_rate | pnl | avg_win | avg_loss |
|---|---:|---:|---:|---:|---:|
| `red_bar_growing + long_reclaim_confirmed` | 102 | 87.25% | +658.60 | +9.94 | -17.39 |
| `flip_bullish + long_reclaim_confirmed` | 52 | 88.46% | +332.47 | +10.17 | -22.58 |
| `green_bar_growing + short_dual_pressure` | 192 | 81.25% | +315.97 | +10.56 | -36.98 |
| `green_bar_growing + short_retest_reject` | 75 | 82.67% | +224.95 | +9.42 | -27.61 |
| `red_bar_growing + short_retest_reject` | 8 | 87.50% | +60.79 | +10.37 | -11.77 |
| `green_bar_growing + long_reclaim_confirmed` | 4 | 100.00% | +22.03 | +5.51 | 0 |
| `red_bar_shrinking + short_retest_reject` | 1 | 100.00% | +13.16 | +13.16 | 0 |
| `green_bar_growing + long_dual_support` | 3 | 100.00% | +13.08 | +4.36 | 0 |
| `red_bar_growing + short_dual_pressure` | 1 | 0.00% | -39.54 | 0 | -39.54 |

### 5.2 这次最关键的结构变化

最重要的事实不是收益增加了，而是：

> `red_bar_growing + long_dual_support` 在这次 30 天结果里已经是 `0` 笔。

也就是说：

- 之前的主亏损 pocket 被 E2 的 pocket gate 成功挡掉了
- 还存活的 `long_dual_support` 只有 `3` 笔
- 这 `3` 笔全部来自 `green_bar_growing`
- 它们不受 `red_bar_growing|long_dual_support` 这条 pocket override 约束

这和我们之前的结构判断完全一致：

- 问题不是 `long_dual_support` 这个 vwap_state 本身绝对无效
- 问题是 **`red_bar_growing + long_dual_support` 这种“下跌过程中在支撑附近抢反弹”的入口条件过宽**

### 5.3 surviving `long_dual_support` 的实际样本

仅存 3 笔：

| symbol | signal_type_1h | pnl | signal_score | vwap_score | reason | is_trial_entry |
|---|---|---:|---:|---:|---|---|
| `ZECUSDT` | `green_bar_growing` | +7.34 | 0.8935 | 0.1495 | `stop_loss_intrabar` | `false` |
| `XLMUSDT` | `green_bar_growing` | +3.98 | 0.8989 | 0.1249 | `stop_loss_intrabar` | `false` |
| `ZROUSDT` | `green_bar_growing` | +1.76 | 0.8467 | 0.1537 | `stop_loss_intrabar` | `false` |

这些样本的共同点：

- 全部不是 `red_bar_growing`
- 全部不是 `trial entry`
- 全部是小正收益 stop out

这进一步说明：

> E2 当前修掉的是“坏的反弹型 long_dual_support”，而不是把所有 `long_dual_support` 一刀切抹掉。

## 6. 按信号类型与 vwap 状态看当前盈利引擎

### 6.1 按 `signal_type_1h`

| signal_type_1h | count | win_rate | pnl |
|---|---:|---:|---:|
| `red_bar_growing` | 111 | 86.49% | +679.84 |
| `green_bar_growing` | 274 | 82.12% | +576.03 |
| `flip_bullish` | 52 | 88.46% | +332.47 |
| `red_bar_shrinking` | 1 | 100.00% | +13.16 |

最值得注意的不是 `green_bar_growing`，而是：

> `red_bar_growing` 本身已经从之前的薄利甚至拖累通道，变成了当前最大的 signal-type 盈利来源。

这也解释了为何总收益能从 `+7.00%` 拉到 `+12.31%`：

- E2 不是“减少了坏单而已”
- 它还让 `red_bar_growing` 的剩余样本更加集中到了 `long_reclaim_confirmed` 这种正收益 pocket

### 6.2 按 `vwap_state`

| vwap_state | count | win_rate | pnl |
|---|---:|---:|---:|
| `long_reclaim_confirmed` | 158 | 87.97% | +1013.10 |
| `short_retest_reject` | 84 | 83.33% | +298.89 |
| `short_dual_pressure` | 193 | 80.83% | +276.43 |
| `long_dual_support` | 3 | 100.00% | +13.08 |

因此当前最强 pocket 很明确：

- `long_reclaim_confirmed`
- `short_retest_reject`
- `short_dual_pressure`

而 `long_dual_support` 已经从“明显亏损 pocket”变成了“几乎不参与”的边缘 pocket。

## 7. 当前 exit 归因

### 7.1 exit reason 分布

| reason | count | pnl | avg_pnl |
|---|---:|---:|---:|
| `take_profit_level_intrabar` | 141 | +1154.95 | +8.19 |
| `stop_loss_intrabar` | 284 | +747.52 | +2.63 |
| `stop_loss_intrabar_both_hit` | 2 | +36.14 | +18.07 |
| `backtest_end` | 2 | +8.27 | +4.13 |
| `signal_reverse` | 2 | -42.64 | -21.32 |
| `4h_shrink_exit` | 7 | -302.73 | -43.25 |

### 7.2 这次 exit 结构说明了什么

这次结果再次支持一个关键判断：

> 这轮收益改善的主因不是 exit 更聪明，而是 bad pocket 被 entry-side gate 清掉了。

原因：

- 我们没有继续做新的全局 exit 收紧
- `take_profit_level_intrabar` 仍然是主要正收益来源
- `stop_loss_intrabar` 整体甚至是正和
- 结构改善主要来自 pocket 分布重构，不是单笔退出技巧

唯一还值得警惕的是：

- `4h_shrink_exit` 只有 7 笔，却合计 `-302.73`

这说明下一轮如果还要继续优化，`4h_shrink_exit` 的触发场景和 pocket 分布值得单独审查。

## 8. 我当前的判断

### 8.1 已被这轮数据证实的事情

我认为下面 4 点已经基本被证实：

1. `long_dual_support` 的核心问题是 entry alpha，而不是 exit 不够聪明。
2. 问题集中在 `red_bar_growing + long_dual_support` 这种结构，而不是所有 `long_dual_support` 全部无效。
3. 用 pocket 级独立准入 gate 修这个问题，是比 symbol 筛选更正确的方法。
4. E2 没有破坏主盈利引擎，反而把 `red_bar_growing` 重新导向了正收益 pocket。

### 8.2 还没有被充分回答的问题

我希望 Claude 继续帮我判断：

1. `red_bar_growing + long_dual_support` 被直接压到 0 笔，在方法论上是否过于激进，还是恰好说明 `min_vwap_score=0.16` 是正确硬阈值。
2. `green_bar_growing + long_dual_support` 这 3 笔小正收益样本，是否说明 `long_dual_support` 更适合作为顺势 pocket，而不是逆势 pocket。
3. 当前下一轮最值得继续优化的是不是 `4h_shrink_exit`，而不是再碰 `long_dual_support`。
4. `red_bar_growing + long_reclaim_confirmed` 从之前 `72` 笔 / `+200` 到现在 `102` 笔 / `+658`，这是否意味着 E2 实际上提升了样本迁移质量，而不仅仅是减少了坏单。
5. 是否应该把 pocket 级 gate 的方法，复制到其他潜在弱 pocket，而不是继续做 symbol 级治理。

## 9. 希望 Claude 重点回答的 6 个问题

1. 你是否同意：这次 30 天结果已经足以说明 `red_bar_growing + long_dual_support` 是 entry alpha 问题，而 E2 的修复方向是对的？
2. `min_vwap_score=0.16` 在这个 pocket 上把样本压到 0，应该理解为“正确切掉假支撑”，还是“阈值过于暴力”？
3. 当前仅存的 `green_bar_growing + long_dual_support` 3 笔小正收益，是否说明 `long_dual_support` 应该被重定义成“顺势支撑 pocket”，而不是“逆势反弹 pocket”？
4. 你会优先继续优化哪个结构：`4h_shrink_exit`、`short_dual_pressure` 的 payoff、还是 `flip_bullish` 的 runner 保护？
5. 如果下一轮继续用 pocket 级方法扩展优化，你最建议先打哪个 pocket，应该收紧哪一层门槛？
6. 当前 production/E2 是否已经足够稳，可以继续作为实盘基线，而不需要再回到 symbol 筛选路线？

