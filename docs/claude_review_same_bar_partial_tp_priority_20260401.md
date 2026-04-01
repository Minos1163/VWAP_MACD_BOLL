# MACD V2 — Same-Bar Partial TP vs Stop Priority Review

**日期**：2026-04-01  
**评审目标**：审阅当前回测中 `same-bar partial TP vs stop` 的结算优先级是否合理，以及“当同一根 15m bar 同时命中 partial TP level 与 stop 时，应否先记 partial TP 再处理剩余仓位 stop”的候选修复方案。  
**当前 production 状态**：  
- [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)  
- [trading_config_fund_flow_live_production.json](D:\AIDCA\AI8\config\trading_config_fund_flow_live_production.json)  
- 当前 `breakeven_trigger_pnl_ratio = 0.012`  
- 当前 `breakeven_lock_ratio = 0.0025`

---

## 1. 结论先行

当前最值得继续优化的 close-risk 点，已经不再是“把 `breakeven` 再往后推一点”，而是：

> **同 bar 内，partial TP 与 stop 的结算优先级。**

这次排查得到的最关键证据是：

1. `breakeven_trigger = 0.012` 与 `0.015` 的 30 天回测结果几乎完全一样，说明继续全局延后 `breakeven` 已经接近平台期。  
2. 仍然残留一批“小赢 protective stop”：
   - `55` 笔 `0 < pnl < 20` 的 `stop_loss_intrabar*`
   - 其中 `17` 笔是**没有 partial TP** 的 same-trade 小赢 stop
3. 更关键的是，这 `17` 笔在退出那根 15m bar 上：
   - `17/17` 已经越过 TP1 (`0.8%`)
   - `17/17` 已经越过 TP2 (`1.2%`)
   - `2/17` 甚至越过 TP3 (`2.0%`)
4. 但当前回测仍按 `stop_loss_intrabar` 结算，而不是先给 partial TP 再 stop 剩余仓位。

因此当前最合理的下一步不是继续调 `breakeven_trigger`，而是：

> **做一个候选版：当同 bar 同时命中 TP level 和 stop 时，先记 partial TP，再处理剩余仓位的 stop。**

---

## 2. 当前策略方法与生产基线

### 2.1 当前 production 风控参数

当前 production / live production 的关键 close-risk 参数为：

- `stop_loss_pct = 0.02`
- `take_profit_pct = 0.04`
- `take_profit_pct_levels = [0.008, 0.012, 0.02]`
- `take_profit_reduce_pct_levels = [0.25, 0.3, 0.2]`
- `breakeven_enabled = true`
- `breakeven_trigger_pnl_ratio = 0.012`
- `breakeven_lock_ratio = 0.0025`
- `max_active_symbols = 4`
- `default_target_portion = 0.30`
- `default_leverage = 5`

### 2.2 当前 30 天 production 回测

基于：
- [v2_summary_20260401_123210.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_123210.json)
- [v2_trades_20260401_123210.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_123210.csv)

当前 30 天结果：

- `652` 笔
- 胜率 `87.27%`
- 收益 `+143.15%`
- Profit Factor `3.074`
- 真最大回撤 `3.77%`

这版 production 已经比早前版本明显更强，但 residual small-win stop 仍在吞噬一部分可兑现利润。

---

## 3. 当前回测中的平仓顺序

在 [backtest_macd_v2.py](D:\AIDCA\AI8\scripts\backtest_macd_v2.py) 的 `check_stops()` 里，当前顺序是：

1. 更新 `breakeven`
2. 更新 `trailing_stop`
3. 计算 `stop_hit / target_hit`
4. **如果 `stop_hit`，立刻按 `stop_loss_intrabar` 或 `stop_loss_intrabar_both_hit` 平仓并返回**
5. 然后才检查 `take_profit_levels`
6. 再检查固定 `take_profit_intrabar`
7. 最后检查 `4h_shrink_reduce / 4h_shrink_exit`

也就是说，当前逻辑是明显的：

> **stop-first, partial-TP-later**

这会带来一个结构性后果：

- 如果同一根 bar 先上冲，理论上已经满足 TP1 或 TP2
- 随后回落，又打到被 breakeven / trailing 抬高的 `stop_price`
- 当前逻辑会先按 stop 结算，partial TP 来不及记录

这正是本轮怀疑的“same-bar 顺序伪影”。

---

## 4. 本轮证据链

### 4.1 `0.012` vs `0.015` 已进入平台区

对比：
- [v2_summary_20260401_123210.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_123210.json)
- [v2_summary_20260401_124450.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_124450.json)

结果：

| 方案 | 交易数 | 胜率 | 收益 | PF | MDD |
|---|---:|---:|---:|---:|---:|
| `breakeven_trigger = 0.012` | 652 | 87.27% | +143.15% | 3.074 | 3.77% |
| `breakeven_trigger = 0.015` | 652 | 87.27% | +143.12% | 3.074 | 3.77% |

结论：

- `0.015` 不优于 `0.012`
- 继续全局延后 `breakeven` 的边际价值已经很低

### 4.2 same-bar 小赢 stop 扫描

基于：
- [same_bar_small_stop_scan_20260401.json](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_20260401.json)
- [same_bar_small_stop_scan_be_012_20260401.csv](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_be_012_20260401.csv)
- [same_bar_small_stop_scan_be_015_20260401.csv](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_be_015_20260401.csv)

定义：

- `small_positive_stop_rows` = `0 < pnl < 20` 且 `reason in stop_loss_intrabar*`
- `no_partial` = 同一 `symbol + side + entry_time` 下没有任何 `take_profit_level_intrabar`

扫描结果：

| 指标 | `0.012` | `0.015` |
|---|---:|---:|
| small positive stop rows | 55 | 55 |
| no-partial small stop rows | 17 | 17 |
| same-bar crossed TP1 | 17 | 17 |
| same-bar crossed TP2 | 17 | 17 |
| same-bar crossed TP3 | 2 | 2 |

这说明：

1. `0.015` 没有消掉这批 residual small stops
2. 这批问题单不是“还需要再晚一点 breakeven”可以解决的
3. 它们更像是 **same-bar order-of-operations artifact**

---

## 5. 受影响 pocket

这 17 笔 “无 partial 的 same-bar 小赢 stop” 主要集中在：

| signal_type_1h | vwap_state | count | pnl |
|---|---|---:|---:|
| `green_bar_growing` | `short_retest_reject` | 7 | +88.32 |
| `red_bar_growing` | `long_reclaim_confirmed` | 5 | +71.50 |
| `green_bar_growing` | `short_below_session_above_structure` | 2 | +11.80 |
| `red_bar_growing` | `long_above_session_below_structure` | 2 | +21.34 |
| `flip_bullish` | `long_reclaim_confirmed` | 1 | +2.22 |

这点非常关键：

- 这些并不是低质量 pocket
- 它们大多数来自当前策略里已经赚钱的主 pocket

所以问题并不是“坏 alpha 的单子太多”，而是：

> **盈利 pocket 的 close-path 里，部分 same-bar 盈利没有被正确兑现成 partial TP。**

---

## 6. 为什么这值得优先优化

### 6.1 它比继续调 breakeven 更贴近当前剩余问题

前一阶段的核心问题确实是：

- `breakeven` 介入太早

但经过从 `0.008 -> 0.010 -> 0.012` 的逐步后移后，这条线已经得到实质性修复。  
现在剩余的问题是更细的 close-path sequencing。

### 6.2 它理论上对主盈利 pocket 更友好

如果改成：

> 同 bar 先记 TP level，再让剩余仓位去走 stop / breakeven / trailing

那么：

- 已经触达 TP1/TP2 的盈利结构能先兑现一部分
- 剩余 runner 仍然可以被 stop 保护
- 不需要再全局放松 `breakeven`
- 不会像继续后移 trigger 那样把全部 pocket 都推向更激进风险

---

## 7. 候选修复方案

### 7.1 目标行为

当前行为：

1. 同 bar 先更新 BE / trailing
2. 若 stop_hit = true，则直接 stop 平仓
3. partial TP 不再结算

候选行为：

1. 同 bar 先更新 BE / trailing
2. 若 same bar 同时命中 TP levels 与 stop：
   - **先结算命中的 partial TP**
   - 再对剩余仓位按 stop_price 处理
3. 若剩余仓位已归零，则不再 stop

### 7.2 候选伪代码

```python
hit_levels = collect_partial_tp_levels(...)
stop_hit = evaluate_stop(...)

if hit_levels and stop_hit:
    # 先兑现 partial TP
    for level in hit_levels:
        close_position(..., reason="take_profit_level_intrabar")

    # 再对剩余仓位做 stop
    if remaining_position_exists(symbol):
        close_position(..., reason="stop_loss_intrabar_after_partial")
    return True

if stop_hit:
    close_position(..., reason="stop_loss_intrabar")
    return True

if hit_levels:
    ...
```

### 7.3 评估目标

这版候选的理想结果不是“交易数暴增”，而是：

1. `same_bar_tp1_cross_no_partial_count` 明显下降
2. `take_profit_level_intrabar` 占比上升，但属于合理增加
3. 主 pocket 的整笔收益更厚
4. `small positive stop rows` 下降
5. 总收益、PF 提升，同时 MDD 不显著恶化

---

## 8. 风险与注意点

### 风险 1：回测会不会过度乐观？

会有这个风险。因为 15m bar 只有 `OHLC`，没有真实 tick path。  
当同一根 bar 同时命中 TP 和 stop 时，我们并不知道真实先后次序。

所以这类优化本质上是在定义一种**回测近似结算规则**，而不是发现确定真相。

这也是我希望 Claude 重点审的地方：

- 这种近似是更合理，还是更乐观？
- 是否应该只允许 TP1 优先，而不允许 TP2/TP3 也优先？
- 是否应该增加额外约束，比如：
  - 只有当 `high` 超出 TP1 足够多时才承认 partial TP
  - 或只在趋势型 pocket 里启用

### 风险 2：可能影响 same-bar `stop_loss_intrabar_both_hit`

当前 `both_hit` 是 stop-first 逻辑下的命名。  
如果改成 TP-first，`both_hit` 的含义会改变，日志与统计口径需要同步调整。

### 风险 3：live/backtest 语义仍不严格等价

当前 live/backtest 对齐审计仍提示：

- `intrabar_hit_logic = MISMATCH_RISK`
- `same-bar stop vs TP priority = UNVERIFIED`

所以即便这版候选在回测中成立，也应视为：

> **一种更合理的回测 close-path 近似，而不是 live 已证明的真实执行语义。**

---

## 9. 希望 Claude 重点回答的问题

请 Claude 重点回答以下问题：

1. 你是否同意：当前 residual small-win 问题的主因，已经从 “breakeven too early” 转移为 “same-bar stop-first ordering artifact”？
2. 在只有 15m OHLC、没有 tick path 的前提下，`partial TP before stop` 是否比当前 `stop before partial TP` 更合理？
3. 若要做 same-bar 优先级修正，你建议：
   - 全部 TP levels 优先
   - 仅 TP1 优先
   - 仅趋势型 pocket 优先
   - 还是采用别的折中方案？
4. 这类规则是否会过度乐观，从而夸大回测收益？
5. 是否应该把这类逻辑放在 backtest 专用层，而不是 strategy/live 公共语义层？
6. 在当前生产基线下，你最推荐的下一步是：
   - same-bar partial-before-stop
   - partial-aware breakeven
   - pocket-specific breakeven trigger
   - runner-only trailing

---

## 10. 附件建议

建议和这份 md 一起提交给 Claude 的文件：

- [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)
- [trading_config_fund_flow_live_production.json](D:\AIDCA\AI8\config\trading_config_fund_flow_live_production.json)
- [v2_summary_20260401_123210.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_123210.json)
- [v2_trades_20260401_123210.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_123210.csv)
- [v2_summary_20260401_124450.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_124450.json)
- [v2_trades_20260401_124450.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_124450.csv)
- [same_bar_small_stop_scan_20260401.json](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_20260401.json)
- [same_bar_small_stop_scan_be_012_20260401.csv](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_be_012_20260401.csv)
- [same_bar_small_stop_scan_be_015_20260401.csv](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_be_015_20260401.csv)
- [strategy_breakeven_gap_review_20260401.md](D:\AIDCA\AI8\docs\strategy_breakeven_gap_review_20260401.md)

---

## 11. 一句话总结

> 这轮最值得审的，不再是 “breakeven 要不要再晚一点”，而是：**在同一根 bar 同时命中 TP level 和 stop 时，当前 stop-first 结算是否错误地吞掉了本该兑现的 partial TP。**
