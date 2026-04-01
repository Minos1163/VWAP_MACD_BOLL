# MACD V2 — TP1-Before-Stop Candidate Review

**日期**：2026-04-01  
**评审目标**：审阅一个新的 close-path 候选：当同一根 15m bar 同时命中 partial TP 与 stop 时，是否应采用更保守的 **`TP1-before-stop`** 结算近似，而不是当前的 `stop-first`。  
**重要前提**：这次候选是 **backtest-only settlement model change**，不是 live 语义已验证升级。

---

## 1. 一句话结论

这轮最值得 Claude 审的，不再是：

- 要不要继续把 `breakeven_trigger` 从 `0.012` 再往后推

而是：

> **当前回测是否因为 same-bar `stop-first` 结算顺序，低估了部分本应先兑现 TP1 的盈利单。**

基于最新 30 天 production 基线，这个问题已经有比较强的证据支持。

---

## 2. 当前 production 基线

当前 production / live production：

- [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)
- [trading_config_fund_flow_live_production.json](D:\AIDCA\AI8\config\trading_config_fund_flow_live_production.json)

当前关键 close-risk 参数：

- `stop_loss_pct = 0.02`
- `take_profit_pct = 0.04`
- `take_profit_pct_levels = [0.008, 0.012, 0.02]`
- `take_profit_reduce_pct_levels = [0.25, 0.3, 0.2]`
- `breakeven_trigger_pnl_ratio = 0.012`
- `breakeven_lock_ratio = 0.0025`
- `max_active_symbols = 4`
- `default_target_portion = 0.30`
- `default_leverage = 5`

当前 30 天基线回测：

- [v2_summary_20260401_123210.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_123210.json)
- [v2_trades_20260401_123210.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_123210.csv)

结果：

- `652` 成交腿
- `337` 唯一开仓
- 胜率 `87.27%`（按成交腿）
- 收益 `+143.15%`
- Profit Factor `3.074`
- 真最大回撤 `3.77%`

---

## 3. 为什么这轮不再继续调 breakeven

我先补做了 `0.015` 的全局 `breakeven_trigger` 对照：

- 候选配置：
  - [trading_config_fund_flow_be_trigger_015_lock_0025.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_be_trigger_015_lock_0025.json)
- 回测结果：
  - [v2_summary_20260401_124450.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_124450.json)
  - [v2_trades_20260401_124450.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_124450.csv)

对比：

| 方案 | 成交腿 | 胜率 | 收益 | PF | MDD |
|---|---:|---:|---:|---:|---:|
| production `trigger=0.012` | 652 | 87.27% | +143.15% | 3.074 | 3.77% |
| candidate `trigger=0.015` | 652 | 87.27% | +143.12% | 3.074 | 3.77% |

结论：

- `0.015` 对 `0.012` 没有新增价值
- 继续全局推迟 `breakeven` 已进入平台区
- 下一步更值得处理的，是 close-path sequencing

---

## 4. 当前问题的证据链

### 4.1 same-bar 小赢 stop 扫描

我做了一个专门的 same-bar 扫描：

- [same_bar_small_stop_scan_20260401.json](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_20260401.json)
- [same_bar_small_stop_scan_be_012_20260401.csv](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_be_012_20260401.csv)
- [same_bar_small_stop_scan_be_015_20260401.csv](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_be_015_20260401.csv)

定义：

- `small_positive_stop_rows` = `0 < pnl < 20` 且 `reason in stop_loss_intrabar*`
- `no_partial` = 同一 `symbol + side + entry_time` 下，没有任何 `take_profit_level_intrabar`

在当前 production (`0.012`) 下：

- `55` 笔 small positive stop rows
- 其中 `17` 笔是 no-partial small-stop rows
- 而这 `17` 笔里：
  - `17/17` 在退出那根 bar 上越过了 TP1 (`0.8%`)
  - `17/17` 越过了 TP2 (`1.2%`)
  - `2/17` 甚至越过了 TP3 (`2.0%`)

而 `0.015` 下，这组数字完全一样。

### 4.2 当前执行顺序

在 [backtest_macd_v2.py](D:\AIDCA\AI8\scripts\backtest_macd_v2.py) 的 `check_stops()` 中，当前顺序是：

1. 更新 `breakeven`
2. 更新 `trailing_stop`
3. 计算 `stop_hit`
4. **若 stop_hit，则直接 stop 平仓并返回**
5. 之后才检查 `take_profit_levels`
6. 再检查固定 TP
7. 最后检查 `4h_shrink_reduce / 4h_shrink_exit`

这意味着：

> 当前逻辑是明确的 `stop-first`。

所以如果同一根 bar 先上冲越过 TP1，然后再回落打 stop，当前回测会倾向于把它记成：

- `stop_loss_intrabar`

而不是：

- 先 `take_profit_level_intrabar`
- 再对剩余仓位 stop

---

## 5. 为什么我不建议直接上 all-levels partial-before-stop

我这轮没有做“TP1/TP2/TP3 全部先于 stop”的激进版，原因是：

1. 当前数据只有 `15m OHLC`，没有 tick path  
2. 在没有真实同 bar 先后顺序的情况下：
   - `all-levels before stop` 会比当前规则更乐观
   - 特别是在同一根 bar 同时越过 TP2/TP3 又回落至 stop 时，容易高估收益
3. 这类逻辑更像：
   - **回测结算近似模型**
   - 而不是可直接映射到 live 的严格执行语义

所以我采用的是一个更保守、更局部的候选：

> **只允许 TP1 before stop，不放开 TP2/TP3。**

---

## 6. 这轮候选：TP1-before-stop

### 6.1 候选定义

新增 backtest-only 配置：

- [trading_config_fund_flow_same_bar_tp1_before_stop.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_same_bar_tp1_before_stop.json)

关键字段：

```json
"fund_flow": {
  "backtest": {
    "same_bar_tp_priority_mode": "tp1_before_stop"
  }
}
```

### 6.2 候选行为

当同一根 15m bar 同时命中 partial TP 与 stop 时：

1. **只允许 TP1 先兑现**
2. 然后再对剩余仓位按 stop 处理
3. TP2 / TP3 仍保持当前 stop-first 语义

新增审计 reason：

- `stop_loss_intrabar_after_tp1_same_bar`
- `stop_loss_intrabar_both_hit_after_tp1_same_bar`

### 6.3 为什么只做 TP1

因为当前最明显的伪影就是：

- 17 笔 residual no-partial small stops
- `17/17` 都越过 TP1

所以：

- 只修 TP1 就已经能直接命中当前最主要的问题
- 同时又比放开 TP2 / TP3 更保守

---

## 7. 这轮候选回测结果

回测文件：

- [v2_summary_20260401_130428.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_130428.json)
- [v2_trades_20260401_130428.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_130428.csv)

对比 production：

| 指标 | production `0.012` | `TP1-before-stop` 候选 |
|---|---:|---:|
| 成交腿 | 652 | 830 |
| 唯一开仓 | 337 | 332 |
| 胜率（成交腿） | 87.27% | 89.88% |
| 收益 | +143.15% | +168.52% |
| Profit Factor | 3.074 | 3.25 |
| MDD | 3.77% | 3.77% |

这里要特别注意：

- 成交腿数明显增加，是因为 partial TP 被更早记账
- 但**唯一开仓数几乎没变**
- 所以不能只看腿数增多就说它是“虚胖”

我还按唯一交易重算了整笔表现：

| 指标 | production | `TP1-before-stop` |
|---|---:|---:|
| unique entries | 337 | 332 |
| total pnl | 15801.43 | 18395.05 |
| median pnl | 51.30 | 63.46 |
| mean pnl | 46.89 | 55.41 |
| unique win rate | 78.04% | 80.12% |
| positive trades `< 20 USDT` | 28 | 23 |
| trades `<= 0` | 74 | 66 |

这说明这版候选不是单纯通过腿数膨胀来“美化结果”，而是对整笔收益质量也有明显改善。

---

## 8. 对 residual small-stop 的影响

候选版下重新扫描：

- `small_stop_rows`: `74`
- `no_partial small-stop rows`: `0`

解释：

- 原本那 17 笔“无 partial 的 same-bar 小赢 stop”已经被吃掉
- 现在 residual positive stop rows 都已经属于：
  - 先 partial TP
  - 再对 runner 做保护 stop

这反而更符合当前策略想要表达的 runner-protection 结构。

---

## 9. 为什么这轮值得优先推进

### 9.1 它更贴近当前真正的问题

当前问题已经不是：

- `breakeven too early`

而是：

- **same-bar close ordering under-realizes profitable pockets**

### 9.2 它主要影响的是好 pocket，而不是坏 pocket

被命中的 17 笔 residual no-partial small stops，主要来自：

- `green_bar_growing | short_retest_reject`
- `red_bar_growing | long_reclaim_confirmed`
- `flip_bullish | long_reclaim_confirmed`

这些本来就是当前 production 的主盈利 pocket。  
因此现在要修的不是 entry alpha，而是：

> **盈利 pocket 的 close-path 被保守结算低估。**

### 9.3 它比继续调 breakeven / trailing 更局部、更可解释

这版候选：

- 不改 production
- 不改 live
- 不改全局 BE
- 不改全局 trailing
- 只改一个回测结算近似点

因此可解释性和因果隔离都比较好。

---

## 10. 风险与边界

### 风险 1：这仍然是回测近似，不是真实 tick truth

这一点最重要：

- 15m OHLC 不能证明同 bar 内真实先后
- `TP1-before-stop` 只是比当前 `stop-first` 更合理的中间近似
- 不能把它直接等同于 live 真实行为

### 风险 2：它应当保持 backtest-only，而不是直接进入 strategy/live 公共语义

我当前的实现就是这样做的：

- 仅 backtest 读取 `same_bar_tp_priority_mode`
- 当前 production/live 语义不变

### 风险 3：不能据此直接推导 TP2/TP3 也该优先

我认为当前证据只足够支持：

- TP1-before-stop 候选值得测试

并不足够支持：

- all-levels partial-before-stop

---

## 11. 希望 Claude 重点回答的问题

请 Claude 重点回答：

1. 你是否同意：当前 residual small-win 问题的主因，已经从 `breakeven too early` 转移为 `same-bar stop-first ordering artifact`？
2. 在只有 15m OHLC、没有 tick path 的前提下，`TP1-before-stop` 是否比当前 `stop-first` 更合理？
3. 你是否同意我不直接上 `TP2/TP3-before-stop`，而是先只做 TP1 的保守版？
4. 这版候选是否会过度乐观？如果会，最可能的偏差来自哪里？
5. 这类逻辑是否应严格留在 backtest-only 层，而不进入 live / shared strategy semantics？
6. 在当前 production 基线下，下一步你更推荐：
   - 推进 `TP1-before-stop`
   - 还是改做 `partial-aware breakeven`
   - 还是做 `pocket-specific breakeven trigger`

---

## 12. 建议附件

建议和这份 md 一起提交给 Claude：

- [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)
- [trading_config_fund_flow_live_production.json](D:\AIDCA\AI8\config\trading_config_fund_flow_live_production.json)
- [trading_config_fund_flow_same_bar_tp1_before_stop.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_same_bar_tp1_before_stop.json)
- [v2_summary_20260401_123210.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_123210.json)
- [v2_trades_20260401_123210.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_123210.csv)
- [v2_summary_20260401_124450.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_124450.json)
- [v2_trades_20260401_124450.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_124450.csv)
- [v2_summary_20260401_130428.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_130428.json)
- [v2_trades_20260401_130428.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_130428.csv)
- [same_bar_small_stop_scan_20260401.json](D:\AIDCA\AI8\output\analysis\same_bar_small_stop_scan_20260401.json)
- [strategy_same_bar_tp1_before_stop_review_20260401.md](D:\AIDCA\AI8\docs\strategy_same_bar_tp1_before_stop_review_20260401.md)

---

## 13. 一句话总结

> 这轮最值得审的候选，不是继续推迟 `breakeven`，而是一个更保守、仅回测层生效的 **`TP1-before-stop`**：它直接修复当前 same-bar 下最明显的 partial TP 低估问题，同时避免一步走到过度乐观的 `all-levels before stop`。
