# MACD V2 30天回测与 `long_dual_support` 结构归因审查

日期: `2026-04-01`  
配置文件: [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)  
30天回测摘要: [v2_summary_20260401_091624.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_091624.json)  
30天成交明细: [v2_trades_20260401_091624.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_091624.csv)  
`long_dual_support` 分 symbol 归因: [long_dual_support_symbol_attribution_20260401.csv](D:\AIDCA\AI8\output\analysis\long_dual_support_symbol_attribution_20260401.csv)

## 1. 这份文档的目的

这份文档不是为了请求“直接黑名单化最差 symbol”，而是为了请 Claude 审核当前 MACD V2 的结构因果链。

当前我的判断是：

- 策略整体并没有失去可交易性，30 天仍然有 `499` 笔、`83.17%` 胜率、`+7.00%` 收益、`2.87%` 真 MDD。
- 主盈利引擎依然非常清晰，主要利润来自趋势型 pocket，尤其是 `green_bar_growing`。
- 已知坏 pocket 也非常清晰，就是 `red_bar_growing + long_dual_support`。
- 但我不接受“直接把坏 symbol 筛掉就算优化”的做法，因为这更像鸵鸟政策，只是把问题移出可见范围，而不是解释为什么它会坏。

因此这份文档希望 Claude 聚焦评审：

- 当前开仓方向判定、评分权重、门槛设计是否在结构上会误放 `long_dual_support`
- 当前 1H 确认、VWAP 结构、trial/preflip 与 `long_dual_support` 的耦合是否过宽
- 当前风控和平仓是否只是“事后善后”，而不是修正入口 alpha
- 下一轮应该优先改 entry 结构、权重还是 pocket 级准入条件

## 2. 30天基线结果

回测窗口:

- `2026-03-02 00:00:00` 到 `2026-04-01 23:59:59`

核心指标:

- 总交易数: `499`
- 胜率: `83.17%`
- 收益率: `+7.00%`
- 盈利因子: `1.43`
- 真最大回撤: `2.87%`
- 平均盈利: `+8.72`
- 平均亏损: `-30.18`

信号类型表现:

- `green_bar_growing`: `224` 笔, 胜率 `86.16%`, PnL `+893.82`
- `flip_bullish`: `41` 笔, 胜率 `82.93%`, PnL `+155.18`
- `red_bar_growing`: `233` 笔, 胜率 `80.26%`, PnL `+21.75`
- `red_bar_shrinking`: `1` 笔, 胜率 `100%`, PnL `+12.54`

VWAP 状态表现:

- `short_dual_pressure`: `161` 笔, 胜率 `85%`, PnL `+671.19`
- `long_reclaim_confirmed`: `117` 笔, 胜率 `81%`, PnL `+367.49`
- `short_retest_reject`: `67` 笔, 胜率 `90%`, PnL `+279.59`
- `long_dual_support`: `154` 笔, 胜率 `80%`, PnL `-234.98`

这说明问题已经不是“策略整体失效”，而是：

- 正收益 pocket 在赚钱
- `long_dual_support` 在系统性拖后腿

## 3. 当前策略方法

### 3.1 开仓方向判定

当前策略是 `MACD MTF + VWAP + BOLL` 体系，核心方向判定逻辑是：

- `4H` 作为主方向框架
- `1H` 作为方向确认或轻确认
- `15M` 作为入场时机微调
- `VWAP` 负责结构位置信息
- `成交量` 负责参与确认

当前方向框架关键设置:

- `primary_direction_timeframe = 4h`
- `require_1h_confirmation_when_4h_primary = true`
- `allow_neutral_1h_confirmation = true`
- `light_1h_confirmation_when_4h_primary = true`

这意味着当前系统虽然“名义上要求 1H 确认”，但在实际执行上对部分信号是允许：

- `1H neutral`
- `1H light confirmation`

这对于提高覆盖率是有帮助的，但对于反弹型 pocket，尤其是 `long_dual_support`，也可能意味着：

- 4H 只是出现了初步收敛
- 1H 还没有明确同向
- 价格在支撑附近看起来“能反弹”
- 系统就已经允许进场

### 3.2 评分权重

当前评分权重来自策略实现与配置：

- `weight_1h_direction = 0.20`
- `weight_4h_direction = 0.40`
- `weight_4h_enhancement = 0.10`
- `weight_vwap = 0.20`
- `weight_15m_entry = 0.05`
- `weight_volume = 0.15`

解释:

- `4H direction` 是主趋势锚，权重最高
- `4H enhancement` 用来表达 preflip / 4H 动量增强
- `VWAP` 占比和 `1H` 同级，是结构的重要组成
- `15M` 和 `volume` 属于确认层，不是主导层

当前评分门槛:

- 全局 `long_open_threshold = 0.10`
- 全局 `short_open_threshold = 0.08`
- `close_threshold = 0.30`
- 策略级 `min_entry_score = 0.25`
- 策略级 `min_signal_score = 0.845`
- `entry_filters.min_signal_score = 0.87`
- `entry_filters.min_vwap_score_for_entry = 0.12`

我认为这里存在一个需要 Claude 审核的结构问题：

- 当前 entry 同时有总分阈值、entry score 阈值、entry_filters 阈值、signal-type 阈值
- 这些层叠阈值虽然能工作，但解释性较弱
- 尤其在 pocket 归因时，很难直接说清楚一个坏 pocket 到底是“总分误放”，还是“1H 确认过松”，还是 “VWAP 结构判定过松”

## 4. 当前 signal / pocket 门槛

### 4.1 与 `long_dual_support` 高度相关的门槛

当前 `entry_filters` 里与反弹型结构最相关的条件包括：

- `min_signal_score = 0.87`
- `min_vwap_score_for_entry = 0.12`
- `preflip_trial_min_shrink_pct_long = 0.45`
- `preflip_trial_min_signal_score = 0.70`
- `preflip_trial_min_vwap_score = 0.06`
- `preflip_trial_entry_scale = 0.35`
- `soft_15m_entry_score = 0.30`

这里我最希望 Claude 审的是：

- `long_dual_support` 是否根本不应该共享这套 `preflip_trial` 放宽逻辑
- `long_dual_support` 是否应该有独立更高的 `VWAP` / `signal_score` / `1H confirmation` 门槛
- 当前 `allow_neutral_1h_confirmation = true` 是否在这个 pocket 上过于宽松

### 4.2 当前并未采用的“鸵鸟政策”

我不希望评审直接落到“哪些 symbol 该禁用”。

原因:

- 分 symbol 亏损集中只能说明“问题暴露在某些标的上更严重”
- 它并不能解释为什么 `long_dual_support` 这种结构会在这些币上持续失效
- 如果直接黑名单化，只是把问题转成筛选问题，而不是策略问题

我更关心的是：

- `long_dual_support` 的结构确认是否不足
- 这个 pocket 是否天然更接近均值回归而不是趋势延续
- 当前评分与确认链路是否错误地把它当成了“可扩展趋势入口”

## 5. 当前风控与平仓逻辑

### 5.1 资金与仓位

- `default_target_portion = 0.18`
- `max_symbol_position_portion = 0.25`
- `max_active_symbols = 2`
- 杠杆固定在 `2x`
- `reserve_pct = 20%`

这意味着当前不是高并发激进策略，仓位与杠杆已经相对保守。

### 5.2 止损 / 止盈 / 保本 / trailing

当前平仓风险框架:

- 固定止损: `stop_loss_pct = 1.2%`
- 固定主 TP: `take_profit_pct = 4.0%`
- 多档止盈:
  - `0.8%` 减 `25%`
  - `1.2%` 减 `30%`
  - `2.0%` 减 `20%`
- 保本:
  - `breakeven_trigger_pnl_ratio = 0.8%`
  - `breakeven_lock_ratio = 0.25%`
- 动态 trailing:
  - `trailing_stop_mode = dynamic`
  - `trailing_volatile`: `0.8%` 激活, `0.6 ATR`, 距离 `0.5%~1.0%`
  - `trailing_trending`: `1.8%` 激活, `1.8 ATR`, 距离 `1.2%~3.0%`

### 5.3 Entry hard gates

当前 entry 硬门:

- `ADX >= 22`
- `ATR% >= 0.006`
- `ATR% <= 0.02`
- `spread_bps <= 0.0008`
- `flow_min_pass = 2`
- `micro_min_pass = 2`

### 5.4 Pretrade gate

- `enabled = true`
- `use_hard_rules_only = true`
- `cvd_veto_enabled = false`
- `atr_ratio_hard_block = 3.5`
- `equity_usage_block = 0.85`
- `dd_exit_threshold = 0.10`

我的当前判断是：

- 这些风控已经足够多
- 所以 `long_dual_support` 继续亏，并不太像“风险放得太开”
- 更像“入口本身就缺少 alpha”

## 6. `long_dual_support` 的 30 天归因

### 6.1 pocket 总体表现

`red_bar_growing + long_dual_support` 的最新 30 天统计:

- `153` 笔
- 总 PnL `-236.92`
- 胜率 `79.74%`
- 平均 PnL `-1.55`
- 平均盈利 `+6.52`
- 平均亏损 `-33.28`

这是一种非常典型的“高胜率但负期望”结构：

- 它会赢
- 但赢得太薄
- 一旦错，单笔亏损会远大于平均盈利

### 6.2 exit reason 结构

该 pocket 的 exit reason 分布:

- `stop_loss_intrabar`: `116` 笔, 合计 `-299.12`
- `take_profit_level_intrabar`: `32` 笔, 合计 `+137.11`
- `4h_shrink_exit`: `2` 笔, 合计 `-55.45`
- `signal_reverse`: `2` 笔, 合计 `-64.74`
- `stop_loss_intrabar_both_hit`: `1` 笔, 合计 `+45.28`

这意味着：

- 问题不是“不会出现盈利”
- 问题是这类结构更多是小反弹，不具备足够稳定的延续性
- 所以会大量走到 `partial TP`，却仍然无法抵消真正失败时的深亏

## 7. `long_dual_support` 的分 symbol 归因

当前最差的 symbol:

- `XLMUSDT`: `2` 笔, `-104.30`, 胜率 `50%`, `avg_win +1.97`, `avg_loss -106.26`
- `KASUSDT`: `7` 笔, `-88.99`, 胜率 `71.43%`, `avg_win +3.71`, `avg_loss -53.76`
- `FILUSDT`: `1` 笔, `-84.36`, 胜率 `0%`
- `HYPEUSDT`: `5` 笔, `-75.36`, 胜率 `60%`, `avg_win +5.72`, `avg_loss -46.26`
- `SOLUSDT`: `2` 笔, `-74.72`, 胜率 `50%`, `avg_win +4.73`, `avg_loss -79.45`
- `PUMPUSDT`: `4` 笔, `-60.84`, 胜率 `75%`, `avg_win +2.60`, `avg_loss -68.64`
- `MORPHOUSDT`: `4` 笔, `-48.97`
- `ARBUSDT`: `9` 笔, `-47.25`
- `TONUSDT`: `4` 笔, `-46.97`

这些数据说明两件事：

1. 坏表现确实集中在某些 symbol 上更严重  
2. 但即使在高胜率 symbol 上，payoff 结构仍然很差

例如:

- `PUMPUSDT` 胜率 `75%`，仍然亏 `-60.84`
- `KASUSDT` 胜率 `71.43%`，仍然亏 `-88.99`

所以我不认为“挑掉这些 symbol”就是正确答案。  
它们更像是在放大同一个结构性问题：

- 入口质量不够
- 反弹确认不足
- 但系统允许以较高频率继续尝试

## 8. 我当前的判断

### 8.1 不建议把问题简化为 symbol blacklist

原因:

- blacklist 只能隐藏问题
- 不能解释问题
- 不能保证未来新 symbol 不复现同样结构
- 也不能告诉我们 `long_dual_support` 到底应不应该继续存在

### 8.2 更像 entry alpha 问题，而不是 exit 问题

依据:

- 仓位、杠杆和 pretrade gate 已经相对保守
- 全局 close-risk 收紧已经被证明会伤害主盈利口袋
- 该 pocket 在高胜率下依然负期望，说明不是简单的风控过松
- 它更像“支撑反弹型入口本身并不稳定”

### 8.3 最值得怀疑的结构点

我希望 Claude 重点审这几个问题：

- `allow_neutral_1h_confirmation = true` 是否不适合 `long_dual_support`
- `light_1h_confirmation_when_4h_primary = true` 是否让该 pocket 过早入场
- `preflip_trial` 的放宽条件是否被错误复用于反弹类 pocket
- `VWAP score >= 0.12` 对于 `long_dual_support` 是否明显不够
- `flow_min_pass = 2` / `micro_min_pass = 2` 是否对该 pocket 太松
- 当前权重中 `4H direction=0.4` + `VWAP=0.2` 的组合，是否会对“支撑附近的小反弹”误加分

## 9. 请 Claude 重点回答的问题

1. 你是否同意 `red_bar_growing + long_dual_support` 应该被明确定义为 entry alpha 问题，而不是 exit 问题？
2. 对这种反弹型 pocket，当前 `1H neutral allowed + light confirmation` 是否过宽？
3. 这个 pocket 是否应该与 `preflip_trial` 逻辑解耦，避免“支撑反弹 + 提前抄底”双重放宽？
4. 当前 `VWAP >= 0.12`、`flow 2/3`、`micro 2/3` 是否对 `long_dual_support` 过松？
5. 你更建议：
   - 给 `long_dual_support` 建独立 entry gate
   - 重写其 scoring
   - 还是直接废弃该 pocket
6. 如果不采用 symbol blacklist，你会如何从“结构规则”而不是“标的筛选”角度修正这个 pocket？

## 10. 我当前倾向的优化方向

当前我更倾向于让 Claude 从以下方向评审，而不是给出“禁 symbol”建议：

- 为 `long_dual_support` 单独提高 `VWAP` / `signal_score` / `1H confirmation` 要求
- 禁止该 pocket 使用 `neutral 1H`
- 禁止该 pocket 共享 `preflip trial` 放宽
- 提高该 pocket 的 `flow / micro` 通过要求
- 如果这些都做了仍然无法转正，再讨论是否保留该 pocket

一句话总结:

> 当前问题不是“哪些币不该做”，而是“`long_dual_support` 这个入口结构是否被错误地当成了可扩展 alpha”。  
> 我希望 Claude 帮我评审的是结构本身，而不是帮我做一份更长的黑名单。
