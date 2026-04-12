# 当前实盘参数 30D Bot-Like 回测归因

日期: 2026-04-11

## 1. 回测基线

- 配置: [config/trading_config_fund_flow.json](/D:/AIDCA/AI8/config/trading_config_fund_flow.json)
- 引擎: `scripts/backtest_fund_flow_bot_like.py`
- 交易窗口: `2026-03-05T03:15:00` -> `2026-04-04T03:00:00`
- 数据窗口: `2026-03-04 03:15:00` -> `2026-04-04T03:00:00`
- 结果文件:
  - [output/backtest/bot_like_summary_20260411_110500.json](/D:/AIDCA/AI8/output/backtest/bot_like_summary_20260411_110500.json)
  - [output/backtest/bot_like_trades_20260411_110500.csv](/D:/AIDCA/AI8/output/backtest/bot_like_trades_20260411_110500.csv)
  - [output/backtest/bot_like_candidate_ledger_20260411_110500.csv](/D:/AIDCA/AI8/output/backtest/bot_like_candidate_ledger_20260411_110500.csv)

## 2. 目标对照

目标:

- 浮动杠杆: `3x / 4x / 5x`
- 单交易对仓位: `20%-30%`
- 最大同时持仓交易对数: `5`
- 30 天收益: `50%+`
- 胜率: `80%+`
- 开仓数量: `90-120`

当前结果:

| 指标 | 目标 | 当前结果 | 结论 |
| --- | ---: | ---: | --- |
| 30D 收益 | `50%+` | `-6.62%` | 严重不达标 |
| 胜率 | `80%+` | `69.23%` | 明显不达标 |
| 开仓数量 | `90-120` | `265` | 过多，非稀疏问题 |
| 最大持仓交易对数 | `5` | `5` | 配置满足，且 30D 内多次命中容量 |
| 杠杆模式 | `3x/4x/5x` | 实际都有使用 | 结构满足但分布失衡 |
| 单交易对仓位 | `20%-30%` | 实际大多数低于目标 | 结构满足但落地偏低 |

补充:

- 开仓数量 `265` 是按真实开仓笔数口径（`symbol + side + entry_time` 聚合），不是分腿平仓行数
- 平均杠杆 `3.46x`
- 杠杆分布:
  - `3x`: `202`
  - `4x`: `5`
  - `5x`: `58`
- 单交易对落在 `20%-30%` 区间的仅 `29/265 = 10.94%`
- 开仓平均保证金占初始资金比重约 `12.11%`

结论:

- 当前系统不是“开仓太少”，而是“开仓过多但质量不够”
- 杠杆目标在机制层满足，但大多数开仓并没有落到你期望的 `20%-30%` 高效仓位带

## 3. 头部结果

原始汇总:

- 初始资金: `10000`
- 期末资金: `9338.21`
- 收益率: `-6.62%`
- 交易行数: `416`
- 开仓笔数: `265`
- 胜率（交易行）: `69.23%`
- 胜率（开仓笔数）: `55.09%`
- Profit Factor: `0.938`
- 最大回撤: `15.18%`

解读:

- 这不是“只差一点”的结果
- PF 已经掉到 `1.0` 以下，说明当前 admitted trade mix 没有正期望
- 回撤已经触及目标上限附近，但收益仍远低于目标

## 4. 漏斗归因

当前 30D funnel 关键层:

| 阶段 | passed | blocked | 结论 |
| --- | ---: | ---: | --- |
| `1_score_threshold` | `5921` | `91999` | 主前置门 |
| `2_vwap_threshold` | `94336` | `3584` | 有效，但不是首因 |
| `3_4h_preflip_shrink` | `80471` | `17449` | 对 preflip 有实质压制 |
| `4_pocket_entry_override` | `849` | `2` | 当前 pocket override 只直接挡极少数 |
| `5_pre_ai_candidate_filter` | `432` | `21` | 只硬拦少数毒性组合 |
| `6_L1_structural` | `970` | `1356` | 结构门仍有显著过滤 |
| `8_L3_micro` | `2207` | `119` | 微观结构不是主问题 |
| `9_pretrade_gate` | `727` | `34` | 账户前置风险门不是主问题 |
| `10_ai_review` | `407` | `25` | AI shortlist 不是主问题 |
| `11_capacity` | `308` | `99` | 容量已明显限制更优候选 |
| `12_final_fill` | `265` | `43` | 最终真实开仓笔数 |

核心判断:

1. 当前不是“没有机会”
2. 当前是“前段 admitted entries 太多，后段容量开始挤占，但 admitted quality 仍不够”
3. 真正的结构问题在信号类型和 pocket mix，而不是 AI review 或 pretrade gate

## 5. 信号类型归因

按真实开仓笔数聚合:

| signal_type | count | pnl | win_rate | avg_score |
| --- | ---: | ---: | ---: | ---: |
| `red_bar_shrinking` | `120` | `-995.22` | `49.17%` | `0.8554` |
| `green_bar_shrinking` | `35` | `-556.45` | `34.29%` | `0.8820` |
| `green_bar_growing` | `81` | `+798.62` | `64.20%` | `0.9012` |
| `flip_bullish` | `26` | `+529.32` | `80.77%` | `0.9443` |
| `flip_bearish` | `2` | `+7.55` | `50.00%` | `1.0000` |
| `red_bar_growing` | `1` | `+0.84` | `100%` | `0.9481` |

结论:

- 主要 alpha 来自:
  - `green_bar_growing`
  - `flip_bullish`
- 主要亏损来自:
  - `red_bar_shrinking`
  - `green_bar_shrinking`

这不是模糊结论，而是本轮 30D 的主导结构。

## 6. Pocket 归因

### 最差 pockets

| pocket | count | pnl | win_rate | avg_score |
| --- | ---: | ---: | ---: | ---: |
| `red_bar_shrinking|short_dual_pressure` | `40` | `-442.03` | `50.00%` | `0.8510` |
| `green_bar_shrinking|short_retest_reject` | `9` | `-400.58` | `33.33%` | `0.8936` |
| `red_bar_shrinking|short_retest_reject` | `68` | `-332.88` | `52.94%` | `0.8653` |
| `red_bar_shrinking|long_reclaim_confirmed` | `10` | `-144.95` | `30.00%` | `0.8391` |
| `green_bar_shrinking|long_reclaim_confirmed` | `14` | `-93.11` | `28.57%` | `0.9025` |
| `green_bar_shrinking|long_dual_support` | `11` | `-88.04` | `36.36%` | `0.8623` |

### 最好 pockets

| pocket | count | pnl | win_rate | avg_score |
| --- | ---: | ---: | ---: | ---: |
| `green_bar_growing|short_retest_reject` | `77` | `+817.67` | `66.23%` | `0.9029` |
| `flip_bullish|long_reclaim_confirmed` | `26` | `+529.32` | `80.77%` | `0.9443` |

结论:

- `green_bar_growing|short_retest_reject` 是当前最重要的 short alpha pocket
- `flip_bullish|long_reclaim_confirmed` 是当前最重要的 long alpha pocket
- 缩柱类 pocket 当前是结构性亏损源，不应再用“放宽阈值”方式去修

## 7. Symbol 归因

### 最差 symbols

| symbol | count | pnl | win_rate | avg_score |
| --- | ---: | ---: | ---: | ---: |
| `TAOUSDT` | `13` | `-412.26` | `38.46%` | `0.8437` |
| `PUMPUSDT` | `17` | `-321.41` | `35.29%` | `0.8686` |
| `APTUSDT` | `17` | `-216.99` | `52.94%` | `0.8901` |
| `ADAUSDT` | `12` | `-186.15` | `33.33%` | `0.8864` |
| `DOGEUSDT` | `9` | `-178.54` | `11.11%` | `0.8969` |

### 最好 symbols

| symbol | count | pnl | win_rate | avg_score |
| --- | ---: | ---: | ---: | ---: |
| `TRUMPUSDT` | `11` | `+307.01` | `63.64%` | `0.8756` |
| `SOLUSDT` | `9` | `+188.62` | `44.44%` | `0.9292` |
| `MORPHOUSDT` | `11` | `+164.81` | `72.73%` | `0.8613` |
| `DOTUSDT` | `11` | `+151.47` | `72.73%` | `0.8516` |
| `POLUSDT` | `10` | `+133.56` | `80.00%` | `0.8645` |

结论:

- 当前弱边不是少数噪音 symbol，而是多 symbol 同时被 shrink family 拖累
- 继续扩大 admitted trades，只会放大这些 symbol 的回撤

## 8. 多空归因

按真实开仓笔数:

| side | count | pnl | win_rate | avg_score |
| --- | ---: | ---: | ---: | ---: |
| `long` | `67` | `+108.80` | `49.25%` | `0.8943` |
| `short` | `198` | `-324.14` | `57.07%` | `0.8793` |

结论:

- 当前不是“多头太差，空头很好”
- 实际是 short trades 数量过多，且 shrink short family 把总体拉成负值
- long 侧虽然绝对收益为正，但体量不足，无法抵消 short 侧 admitted noise

## 9. 目标差距

按目标衡量，当前最关键的偏差顺序是：

1. `收益质量` 不达标  
   当前 `-6.62%`，目标 `50%+`

2. `胜率` 不达标  
   当前 `69.23%`（交易行）/ `55.09%`（开仓笔数），目标 `80%+`

3. `开仓次数` 严重超标  
   当前 `265` 开仓，目标 `90-120`

4. `杠杆与仓位结构不平衡`  
   机制上支持 `3x/4x/5x` 和 `20-30%` 目标仓位，但实际大多数开仓集中在：
   - `3x`
   - 低于 `20%` 的实际保证金占用

## 10. 结论

当前主配置不适合直接送审为“可上线优化版本”。给 DeepSeek 的核心问题应该是：

- 为什么 admitted shrink-family trades 在高分情况下仍持续亏损？
- 是否应优先削减：
  - `red_bar_shrinking|short_dual_pressure`
  - `red_bar_shrinking|short_retest_reject`
  - `green_bar_shrinking|short_retest_reject`
- 是否应把容量和排序继续向：
  - `green_bar_growing|short_retest_reject`
  - `flip_bullish|long_reclaim_confirmed`
  倾斜，而不是继续增加 admitted entries？
