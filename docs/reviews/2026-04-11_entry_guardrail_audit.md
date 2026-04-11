# 前置挡板审计

日期: 2026-04-11

## 基线

- 主配置: [config/trading_config_fund_flow.json](/D:/AIDCA/AI8/config/trading_config_fund_flow.json)
- 当前实盘主配置关键项:
  - `entry_thresholds.default = 0.85`
  - `entry_filters.long_entry_mode = "all"`
  - `entry_filters.disable_flip_bullish_trial_entries = false`
  - `entry_filters.disable_flip_bullish_entries = false`
  - `entry_filters.disable_green_bar_growing_entries = false`
  - `entry_filters.min_vwap_score_for_entry = 0.12`
- 30 天 bot-like 基线:
  - 窗口: `2026-03-05T03:15:00 -> 2026-04-04T03:00:00`
  - 收益: `-6.62%`
  - 交易数: `416`
  - 胜率: `69.23%`
  - Profit Factor: `0.93`
  - MDD: `15.18%`
- 结果文件:
  - [output/backtest/bot_like_summary_20260411_110500.json](/D:/AIDCA/AI8/output/backtest/bot_like_summary_20260411_110500.json)
  - [output/backtest/bot_like_trades_20260411_110500.csv](/D:/AIDCA/AI8/output/backtest/bot_like_trades_20260411_110500.csv)

## 当前真实挡板矩阵

### Symbol overrides

| 挡板类型 | 数量 | 说明 |
| --- | ---: | --- |
| `flip_bullish_mode = trial_only` | 18 | 多头 `flip_bullish` 仍受 symbol 级 trial 约束 |
| `green_bar_growing_mode = enabled_with_strict_threshold` | 20 | `green_bar_growing` 允许，但受更严格 symbol 门槛控制 |
| `min_signal_score_override / min_vwap_score_override` | 5 | 仅少数 symbol 有额外分数/位置门槛 |

### 关键列表

- `flip_bullish_mode = trial_only`
  - `DOGEUSDT`, `APTUSDT`, `LINKUSDT`, `BCHUSDT`, `JUPUSDT`, `PUMPUSDT`, `RENDERUSDT`, `ZROUSDT`, `LTCUSDT`, `XRPUSDT`, `HYPEUSDT`, `TAOUSDT`, `ICPUSDT`, `ETCUSDT`, `KASUSDT`, `WLDUSDT`, `MORPHOUSDT`, `JSTUSDT`
- `green_bar_growing_mode = enabled_with_strict_threshold`
  - `ZECUSDT`, `TONUSDT`, `FETUSDT`, `DOGEUSDT`, `ONDOUSDT`, `APTUSDT`, `AVAXUSDT`, `LINKUSDT`, `ADAUSDT`, `PUMPUSDT`, `ZROUSDT`, `LTCUSDT`, `SOLUSDT`, `ICPUSDT`, `KASUSDT`, `POLUSDT`, `WLDUSDT`, `ALGOUSDT`, `VETUSDT`, `JSTUSDT`
- 额外 score/vwap override
  - `ZECUSDT`, `DOGEUSDT`, `LINKUSDT`, `ZROUSDT`, `LTCUSDT`

### Pocket entry overrides

- `red_bar_growing|long_dual_support`
- `green_bar_growing|short_dual_pressure`
- `red_bar_growing|short_dual_pressure`
- `green_bar_growing|short_under_structure_wait_reject`
- `green_bar_growing|short_below_session_above_structure`

## 建议分组

### 应保留的防御项

1. 保留 `green_bar_growing` 的 symbol 级 strict-threshold 机制，不做全量放开。
证据:
- `green_bar_growing|short_retest_reject` 总体为正: `129` 笔, `+817.67`, 胜率 `79.84%`
- 但按 symbol 分化显著:
  - 正向: `TRUMPUSDT +267.20`, `DOTUSDT +138.19`, `ATOMUSDT +92.91`
  - 负向: `APTUSDT -50.46`, `DOGEUSDT -53.42`, `ADAUSDT -58.37`, `LTCUSDT -60.35`, `FETUSDT -62.66`
- 结论: `green_bar_growing` 不是全局问题，防御项应按 symbol 保留，而不是整体解除

2. 保留 `flip_bullish` 的 symbol 级 trial 语义作为默认保护壳，不做全量 normal 化。
证据:
- `flip_bullish|long_reclaim_confirmed` 总体为正: `47` 笔, `+529.32`, 胜率 `89.36%`
- 但按 symbol 分化:
  - 正向: `SOLUSDT +157.00`, `JUPUSDT +129.17`, `BCHUSDT +82.62`, `ONDOUSDT +72.92`
  - 负向: `PUMPUSDT -45.29`
- 结论: `trial_only` 仍有必要作为默认壳层，后续只应做 symbol 级升级

### 应从 `trial_only` 升级为正常放开的项

以下 symbol 可作为下一轮最小实验候选，优先从 `flip_bullish_mode = trial_only` 升级为正常放开:

| symbol | 30d count | pnl | win rate | 结论 |
| --- | ---: | ---: | ---: | --- |
| `SOLUSDT` | 5 | `+157.00` | `100%` | 第一优先级 |
| `JUPUSDT` | 7 | `+129.17` | `100%` | 第一优先级 |
| `ONDOUSDT` | 9 | `+72.92` | `100%` | 第一优先级 |
| `BCHUSDT` | 7 | `+82.62` | `85.7%` | 第二优先级 |

要求:
- 仅做 symbol 级升级
- 不同时改全局阈值
- 每轮不超过 2 到 3 个 symbol
- 必须重新跑 30 天 bot-like 对照

### 应进一步收紧或禁用的弱边 pocket

1. `green_bar_shrinking|short_retest_reject`

当前基线:
- `13` 笔
- `-400.58`
- 胜率 `53.85%`
- 平均分数 `0.8903`

最差 symbol:
- `TAOUSDT`: `4` 笔, `-208.52`
- `PUMPUSDT`: `4` 笔, `-166.35`
- `APTUSDT`: `1` 笔, `-63.37`

结论:
- 这是高分但负收益 pocket
- 默认方向应是新增 pocket 级禁用或极限收紧，而不是继续放宽

2. `red_bar_shrinking|short_retest_reject`

当前基线:
- `114` 笔
- `-332.88`
- 胜率 `68.42%`
- 平均分数 `0.8623`

结论:
- 这是第二个需要优先收口的缩柱类弱边
- 后续应优先新增 pocket 级限制，而不是改全局默认阈值

## 最小回归测试集

- 锁 `long_entry_mode = all` 后 whitelist 不再是主挡板
  - 现有: [tests/test_fund_flow_bot_regressions.py](/D:/AIDCA/AI8/tests/test_fund_flow_bot_regressions.py)
- 锁 `trial_only / enabled_with_strict_threshold` 语义不会退回成旧的 `disable_*`
  - 现有: [tests/test_fund_flow_decision_engine.py](/D:/AIDCA/AI8/tests/test_fund_flow_decision_engine.py)
- 锁 `green_bar_shrinking|short_retest_reject` 不应被当作默认放量 pocket
  - 目前只有回测证据，后续如执行改动需新增专门回归测试

## 本轮结论

- 当前真实前置挡板已经不是“whitelist_only + disable_*”旧语义
- 当前主挡板是 `trial_only`、`enabled_with_strict_threshold` 和 pocket gate
- 下一轮应做的是 symbol 级精细化升级和弱边 pocket 收紧
- 不应再通过全局默认阈值做放量
