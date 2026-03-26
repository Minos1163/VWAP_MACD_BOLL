# MACD_MTF_Strategy_V6

> 文档用途：提交给虚拟货币投资专家组讨论当前策略版本  
> 当前代码版本：`A2 + 温和 D`  
> 当前主回测文件：`v2_summary_20260320_200952.json` / `v2_trades_20260320_200952.csv`

---

## 1. 当前策略定义

### 1.1 核心框架

当前策略仍运行在 `macd_mtf_strategy_v2` 实现上，但实际可视为新一轮讨论版本 `V6`。

当前有效结构是：

1. `4H MACD` 定主方向
2. `1H MACD` 做方向确认，允许 `neutral/light confirmation`
3. `1H/4H BOLL` 给结构强弱修正
4. `session VWAP + structural VWAP` 做位置过滤
5. `15M MACD + 15M BOLL` 做入场精化；在 `4H` 主导时允许 `soft 15m confirmation`
6. `short_dual_pressure` 使用温和仓位弹性

### 1.2 当前真实交易状态

在当前代码和配置下，`flip_bearish` 只允许以下 VWAP 状态参与开空：

- `short_retest_reject`
- `short_dual_pressure`

但由于已经实现了 A2 的硬否决：

- `short_retest_reject + vwap_score < 0.25` 直接被否决

所以当前回测里**实际成交已经全部变成**：

- `short_dual_pressure`

### 1.3 当前版本相对前版的变化

当前版本不是纯 A2，也不是激进 D，而是：

- 保留 A2：`short_retest_reject` 的 `vwap_score` 硬否决
- 在 D 上做温和化：只给 `short_dual_pressure` 适度加仓

也就是：

- 不再做负贡献的 `short_retest_reject`
- 只放大当前正贡献的 `short_dual_pressure`
- 但加仓幅度从激进版回收到温和版

---

## 2. 关键实现位置

当前策略核心代码位置：

- VWAP 评分：`src/fund_flow/macd_strategy_v2.py:574`
- `flip_bearish` VWAP 上下文过滤：`src/fund_flow/macd_strategy_v2.py:924`
- BOLL 结构层：`src/fund_flow/macd_strategy_v2.py:965`
- 综合分析入口：`src/fund_flow/macd_strategy_v2.py:1110`
- 状态化仓位计算：`src/fund_flow/macd_strategy_v2.py:2011`

配置与透传位置：

- 当前参数配置：`config/trading_config_fund_flow.json`
- 回测配置装载：`scripts/backtest_macd_v2.py`
- 实盘参数透传：`src/fund_flow/decision_engine.py`

---

## 3. 当前关键参数

### 3.1 MACD 参数

| 参数 | 数值 |
| --- | --- |
| 1H MACD | `12 / 26 / 9` |
| 4H MACD | `12 / 26 / 9` |
| 15M MACD | `12 / 26 / 9` |
| MACD threshold | `0.00005` |

### 3.2 BOLL 参数

| 参数 | 数值 |
| --- | --- |
| period | `20` |
| std_dev | `2.0` |
| multiplier_strong | `1.2` |
| multiplier_normal | `1.0` |
| multiplier_weak | `0.6` |
| middle_hard_block | `true` |
| strong_trend_leverage_mult | `0.8` |

### 3.3 VWAP 参数

| 参数 | 数值 |
| --- | --- |
| session anchor | `daily_utc0` |
| structural mode | `anchored_weekly` |
| structural rolling window | `20` |
| vwap_retest_tolerance | `0.003` |
| vwap_deviation_optimal | `0.005` |
| vwap_deviation_warning | `0.015` |
| vwap_deviation_hard_block | `0.03` |

### 3.4 评分权重

| 参数 | 数值 |
| --- | --- |
| weight_1h_direction | `0.00` |
| weight_4h_direction | `0.55` |
| weight_4h_enhancement | `0.00` |
| weight_vwap | `0.20` |
| weight_15m_entry | `0.05` |
| weight_volume | `0.20` |

### 3.5 入场阈值

| 参数 | 数值 |
| --- | --- |
| default min_signal_score | `0.850` |
| min_entry_score | `0.25` |
| red_bar_growing threshold | `0.850` |
| flip_bearish threshold | `0.840` |
| flip_bullish threshold | `0.840` |
| min_vwap_score_for_entry | `0.12` |

### 3.6 当前 4H 主导入场过滤

| 参数 | 数值 |
| --- | --- |
| primary_direction_timeframe | `4h` |
| require_1h_confirmation_when_4h_primary | `true` |
| allow_neutral_1h_confirmation | `true` |
| light_1h_confirmation_when_4h_primary | `true` |
| enable_soft_15m_confirmation_when_4h_primary | `true` |
| soft_15m_entry_score | `0.28` |

### 3.7 A2 关键过滤参数

| 参数 | 数值 |
| --- | --- |
| flip_bearish_min_adx_1h | `16.5` |
| flip_bearish_retest_reject_min_vwap_score | `0.25` |
| flip_bearish_max_bb_middle_slope_1h | `0.0` |
| flip_bearish_max_bb_middle_slope_4h | `0.0005` |
| bb_slope_lookback_1h | `3` |
| bb_slope_lookback_4h | `2` |

### 3.8 风控参数

| 参数 | 数值 |
| --- | --- |
| use_dynamic_stop | `true` |
| boll_stop_atr_multiplier | `0.5` |
| max_stop_loss_pct | `0.025` |
| vwap_alert_deviation | `0.005` |

### 3.9 当前 D 参数（温和版）

| 参数 | 数值 |
| --- | --- |
| dual_pressure_target_portion_bonus | `0.08` |
| dual_pressure_max_symbol_position_portion | `0.68` |

### 3.10 组合与执行参数

| 参数 | 数值 |
| --- | --- |
| symbols | `36` |
| max_positions | `3` |
| default_target_portion | `60%` |
| max_symbol_position_portion | `60%` |
| min_open_portion | `6%` |
| reserve_pct | `20%` |
| leverage_range | `2x ~ 4x` |
| stop_loss_pct | `0.40%` |
| breakeven_trigger | `0.30%` |
| breakeven_lock | `0.10%` |
| entry_slippage | `0.15%` |
| entry TIF | `IOC` |

---

## 4. 当前回测结果

当前主结果文件：

- `output/backtest/v2_summary_20260320_200952.json`
- `output/backtest/v2_trades_20260320_200952.csv`

### 4.1 总体结果

| 指标 | 当前结果 |
| --- | --- |
| 初始资金 | `$10,000.00` |
| 最终资金 | `$10,632.57` |
| 总收益率 | `+6.33%` |
| 总交易数 | `52` |
| 胜率 | `75.0%` |
| 盈利因子 | `2.21` |
| 最大回撤 | `12.86%` |
| 信号总数 | `280` |

### 4.2 VWAP 状态拆解

| VWAP 状态 | 交易数 | 胜率 | 总盈亏 |
| --- | --- | --- | --- |
| `short_dual_pressure` | `52` | `75.0%` | `$+811.97` |

结论：

- 当前收益全部来自 `short_dual_pressure`
- `short_retest_reject` 已被 A2 逻辑实质性清空
- 当前版本已经从“混合子状态策略”变成“单一优选子状态策略”

### 4.3 BOLL 结构拆解

| BOLL multiplier | 交易数 | 胜率 |
| --- | --- | --- |
| `1.2x` | `52` | `75.0%` |

结论：

- 当前成交全部发生在 `BOLL=1.2x`
- 这说明当前策略实际上在做“强结构 + 双 VWAP 同向压制”的空头延续结构

### 4.4 当前主要亏损样本

| symbol | entry_time | pnl | reason | signal_score | vwap_score |
| --- | --- | --- | --- | --- | --- |
| KASUSDT | `2026-02-20 11:15:00` | `-352.40` | `stop_loss_intrabar` | `0.9282` | `0.1507` |
| FILUSDT | `2026-02-11 15:15:00` | `-120.59` | `stop_loss_intrabar` | `0.9482` | `0.1482` |
| TAOUSDT | `2026-02-24 04:15:00` | `-77.48` | `stop_loss_intrabar` | `0.9279` | `0.1504` |
| APTUSDT | `2026-02-23 16:15:00` | `-49.76` | `stop_loss_intrabar` | `0.8959` | `0.1424` |
| ZECUSDT | `2026-02-27 10:15:00` | `-21.18` | `stop_loss_intrabar` | `0.9111` | `0.1336` |

观察：

- 当前主要问题仍不是低分信号误开，而是少数大亏单拉大回撤
- 最大亏损单集中在 `signal_score` 高、`vwap_score` 中低区间
- 亏损方式仍主要是 `stop_loss_intrabar`

---

## 5. 与前两版关键结果对比

为了便于讨论，下面给出三个关键版本：

| 版本 | 描述 | 收益率 | 最大回撤 | 盈利因子 | 交易数 |
| --- | --- | --- | --- | --- | --- |
| A2 基线 | 仅做 `retest_reject` 硬否决 | `+5.74%` | `11.75%` | `2.21` | `52` |
| 激进 D | `dual_pressure` 明显加仓 | `+6.80%` | `13.77%` | `2.21` | `52` |
| 当前 V6 | 温和 D | `+6.33%` | `12.86%` | `2.21` | `52` |

结论：

- D 确实能放大收益
- 但 D 目前只是“线性放大仓位”，并没有改善结构质量
- 所以收益和回撤几乎同步放大
- 当前 V6 是收益/回撤的折中版本，但还不是明显优于 A2 的版本

---

## 6. 当前版本的优点

### 6.1 优点

- 已经清掉 `short_retest_reject` 这个历史负贡献子状态
- 当前交易对象更纯，策略解释性更强
- 当前收益显著高于最初 BOLL 替换版本
- 盈利因子维持在 `2.21`，说明结构质量并未明显恶化

### 6.2 不足

- 最大回撤仍偏高：`12.86%`
- 大亏单仍集中在少数品种和少数时段
- 当前 D 只是在放大 `short_dual_pressure`，并没有解决尾部风险

---

## 7. 当前我对策略的判断

当前我对 V6 的判断是：

1. 主框架已经基本稳定
2. A2 是正确方向，已经完成对子状态的净化
3. D 是可行方向，但当前版本只是“仓位加成”，还不够精细
4. 当前最值得继续优化的不是信号筛选，而是：
   - `short_dual_pressure` 的分级仓位曲线
   - 单笔大亏单的约束方式
   - 哪些品种/哪些时间段更容易出现尾部损失

---

## 8. 希望专家组重点讨论的问题

请专家组重点给出以下建议：

### 8.1 当前 V6 是否值得保留，而不是回退到 A2

核心问题：

- `+0.59%` 的额外收益，是否值得接受 `+1.11%` 的额外回撤？

### 8.2 D 应该怎么做得更精细

当前 D 只用了两条静态参数：

- `dual_pressure_target_portion_bonus`
- `dual_pressure_max_symbol_position_portion`

问题：

- 是否应该把 `short_dual_pressure` 按 `signal_score` 再分成 2~3 档仓位？
- 是否应该只对高分段（例如 `>=0.92`）加仓，而不是整类一起加？

### 8.3 是否应引入品种级上限

当前最大亏损单集中在：

- KASUSDT
- FILUSDT
- TAOUSDT
- APTUSDT

问题：

- 是否应对高波动币种单独设更低仓位上限？
- 是否应在 `BOLL=1.2x + short_dual_pressure` 下对某些币种做降仓？

### 8.4 是否应引入时间段过滤

虽然当前主策略已经变成纯 `short_dual_pressure`，但亏损仍有集中时段特征。

问题：

- 是否应对特定 UTC 时段降低 `dual_pressure` 的加仓系数？

### 8.5 是否应把 D 改成非线性映射

问题：

- 当前 `bonus` 是固定加法，是否应改成：
  - 低分不加
  - 中分小加
  - 高分明显加

---

## 9. 我建议的下一步方向

如果继续优化，我建议优先级如下：

1. 不动 A2
2. 保留 D 框架，但把 D 从“固定 bonus”改成“按分数分档 bonus”
3. 对最大亏损品种做单独复盘
4. 在必要时再考虑时间段过滤

我的倾向是：

- 不建议直接回到激进 D
- 当前 V6 可作为讨论版保留
- 真正更优的版本，应该是“分档 D”，不是“统一加仓 D”

---

## 10. 附件建议

建议随文档一起给专家组的文件：

- `output/backtest/v2_summary_20260320_200952.json`
- `output/backtest/v2_trades_20260320_200952.csv`
- `output/backtest/v2_summary_20260320_185419.json`
- `output/backtest/v2_summary_20260320_195141.json`
- 当前 `macd_mtf_strategy_v2` 配置段

---

*文档版本：V6 / 日期：2026-03-20*
