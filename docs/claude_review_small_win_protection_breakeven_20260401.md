# MACD V2 — 小赢风控卖出归因与 Breakeven 延后优化评审稿

**日期**：2026-04-01  
**评审目标**：解释为何大量“风控卖出的小赢单”正在侵蚀真实收益，并说明为什么本轮选择把 `breakeven_trigger_pnl_ratio` 从 `0.008` 延后到 `0.010`。  
**当前实盘状态**：该优化已同步到 production 与 live production 配置。

---

## 1. 结论先行

本轮结论很明确：

1. 当前系统存在一批 **名义上盈利、但扣掉手续费和滑点后几乎没有真实收益** 的保护性卖出单。
2. 这批单子的核心来源不是“固定止损太宽或太窄”，也不是 “breakeven 锁盈太低”，而是：
   - `breakeven` **触发太早**
   - 导致很多仓位刚摸到小浮盈，就把 `stop_price` 抬到很近的位置
   - 随后在正常回撤中被保护性卖出
3. 直接提高 `breakeven_lock_ratio` 会伤害总体收益；更优解是 **延后 breakeven 的触发时机**。
4. 在当前 30 天、`4持仓 / 30% / 5x / SL=2.0% / TP=4.0%` 的 production 基线上：
   - 把 `breakeven_trigger_pnl_ratio` 从 `0.008` 调到 `0.010`
   - 收益从 `+97.55%` 提高到 `+140.18%`
   - 胜率从 `85.1%` 提高到 `87.3%`
   - 真最大回撤从 `3.46%` 降到 `2.97%`

所以本轮优化不是“更激进”，而是：

> **减少过早保护，允许正常盈利单先建立更充分的浮盈，再进入保本保护。**

---

## 2. 本轮评审回答的核心问题

### Q1：这些“小赢风控卖出”是否真实存在？

**是，且数量不小。**

基于 [v2_trades_20260401_120122.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_120122.csv) 的归因：

- 总成交腿数：`590`
- 正收益成交腿中，`pnl < 20 USDT` 的有 `197` 笔
- 其中主要来源是：
  - `stop_loss_intrabar`: `117`
  - `take_profit_level_intrabar`: `80`

这说明当前有一大批“赢了，但赢得非常薄”的保护性卖出。

---

### Q2：这些“小赢单”主要是谁造成的？

**主要是 breakeven 过早触发后的保护性 stop，而不是 fixed TP。**

在 `pnl < 20` 的正收益成交腿中：

- `stop_loss_intrabar` 有 `117` 笔
- 其中 `60` 笔的 `pnl_pct` 精确落在 `+0.25%`

当前 production 配置是：

- `breakeven_trigger_pnl_ratio = 0.008`（浮盈 `0.8%` 触发）
- `breakeven_lock_ratio = 0.0025`（将 stop 抬到成本上方 `0.25%`）

这与 `+0.25%` 的集中分布完全吻合，说明很大一部分小赢单是：

1. 浮盈先触发 breakeven
2. `stop_price` 被抬到 `entry_price * 1.0025`
3. 后续正常回撤打到保本锁盈位
4. 形成名义正收益，但真实利润很薄

---

### Q3：这些小赢单是不是“看起来小，其实整笔交易已经靠 partial TP 赚够了”？

**有一部分是，但不是全部。**

对 `117` 笔 `stop_loss_intrabar + 正收益 + pnl<20` 的小赢腿做整笔交易重组后：

- `50` 笔来自“先 partial TP，再保护性卖出剩余仓位”
- `67` 笔没有 partial TP，是真正的单腿保护性小赢

而且即便对整笔交易聚合后，这个问题仍然存在。

---

### Q4：如果把手续费和滑点算进去，这些小赢单是否还值得保留？

**当前基线下，有一批已经接近不值得。**

按“唯一开仓交易”聚合，并用保守口径扣除：

- `entry fee = 0.04%`
- `entry slippage = 0.15%`

对 [v2_trades_20260401_120122.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_120122.csv) 重新核算后：

- 唯一交易数：`388`
- 扣除 `entry fee + entry slippage` 后，`<= 0` 的交易：`114`
- 扣除 `entry fee + entry slippage` 后，`0 ~ 20 USDT` 的交易：`121`

这说明当前 production 基线里，确实有一批交易的“真实净利润质量”偏低。

---

### Q5：问题是 lock 太低，还是 trigger 太早？

**根因是 trigger 太早，不是 lock 太低。**

我做了两类对照实验：

#### 路线 A：提高 `breakeven_lock_ratio`

候选文件：

- [trading_config_fund_flow_be_lock_0035.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_be_lock_0035.json)
- [trading_config_fund_flow_be_lock_0040.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_be_lock_0040.json)
- [trading_config_fund_flow_be_lock_0050.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_be_lock_0050.json)

结果：

| 方案 | 交易数 | 胜率 | 收益 | MDD |
|---|---:|---:|---:|---:|
| 当前基线 `0.25% lock` | 590 | 85.1% | +97.55% | 3.46% |
| lock = 0.35% | 563 | 84.0% | +96.23% | 3.28% |
| lock = 0.40% | 537 | 83.4% | +94.78% | 3.18% |
| lock = 0.50% | 503 | 81.5% | +94.40% | 3.27% |

结论：

- 提高 lock 会减少交易数
- 也会压低总体收益
- 说明不是“锁太少”导致问题，而是保护进入得太早

#### 路线 B：延后 `breakeven_trigger_pnl_ratio`

候选文件：

- [trading_config_fund_flow_be_trigger_010_lock_0025.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_be_trigger_010_lock_0025.json)
- [trading_config_fund_flow_be_trigger_012_lock_0025.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_be_trigger_012_lock_0025.json)

结果：

| 方案 | 交易数 | 胜率 | 收益 | MDD |
|---|---:|---:|---:|---:|
| 当前基线 `trigger=0.8%` | 590 | 85.1% | +97.55% | 3.46% |
| `trigger=1.0%` | 656 | 87.3% | +140.18% | 2.97% |
| `trigger=1.2%` | 652 | 87.3% | +143.15% | 3.77% |

结论：

- 延后 trigger 明显优于提高 lock
- `1.0%` 是更稳的平衡点
- `1.2%` 虽然收益更高，但回撤开始抬头

---

### Q6：为什么本轮最终选择 `trigger=1.0%`，而不是 `1.2%`？

**因为 `1.0%` 已经明显改善质量，同时风险更稳。**

基于 `0.8% -> 1.0%` 的变化：

- 收益：`+97.55% -> +140.18%`
- 胜率：`85.1% -> 87.3%`
- MDD：`3.46% -> 2.97%`

而且“低质量小赢单”也明显下降：

按整笔交易扣 `entry fee + entry slippage` 后：

| 方案 | 唯一交易数 | `<=0` | `0~20 USDT` | 正利润中位数 |
|---|---:|---:|---:|---:|
| 基线 `trigger=0.8%` | 388 | 114 | 121 | 24.27 |
| `trigger=1.0%` | 366 | 90 | 80 | 35.51 |
| `trigger=1.2%` | 337 | 84 | 45 | 53.40 |

`1.2%` 确实更激进更赚钱，但：

- 交易样本进一步减少
- MDD 提高到 `3.77%`
- 更像下一轮 candidate，而不是当前 production 的最稳升级

因此本轮 production 选择：

> **`breakeven_trigger_pnl_ratio = 0.010`，保留 `breakeven_lock_ratio = 0.0025`**

---

## 3. 当前策略方法与平仓链路

### 3.1 当前实盘核心参数

当前 production / live production 配置：

- [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)
- [trading_config_fund_flow_live_production.json](D:\AIDCA\AI8\config\trading_config_fund_flow_live_production.json)

关键运行参数：

- `max_active_symbols = 4`
- `default_target_portion = 0.30`
- `default_leverage = 5`
- `stop_loss_pct = 0.02`
- `take_profit_pct = 0.04`
- `take_profit_pct_levels = [0.008, 0.012, 0.02]`
- `take_profit_reduce_pct_levels = [0.25, 0.3, 0.2]`
- `breakeven_trigger_pnl_ratio = 0.010`  ← 本轮新值
- `breakeven_lock_ratio = 0.0025`

### 3.2 当前回测平仓链路

在 [backtest_macd_v2.py](D:\AIDCA\AI8\scripts\backtest_macd_v2.py) 中，`check_stops()` 的顺序是：

1. 先更新 `breakeven`
2. 再更新 `trailing_stop`
3. 然后检查 `stop_hit`
4. 再检查 partial TP levels
5. 再检查 fixed TP
6. 最后检查 `4h_shrink_reduce / 4h_shrink_exit`

这意味着：

- `breakeven` 是很靠前的保护逻辑
- 一旦触发过早，会直接把很多正常波动单提前卖掉

---

## 4. 为什么这不是“SL 问题”

本轮我还额外验证了一个关键事实：

- 当前 30 天样本里，**所有初始 stop_price 都来自 `suggested_stop_price`**
- 没有任何一笔回退到全局固定 `fund_flow.stop_loss_pct`

统计文件：

- [initial_stop_source_20260401.json](D:\AIDCA\AI8\output\analysis\initial_stop_source_20260401.json)
- [initial_stop_source_entries_20260401.csv](D:\AIDCA\AI8\output\analysis\initial_stop_source_entries_20260401.csv)
- [initial_stop_source_trades_20260401.csv](D:\AIDCA\AI8\output\analysis\initial_stop_source_trades_20260401.csv)

结果：

- 唯一 filled entries：`388`
- `suggested_stop_price`: `388`
- `global fixed SL fallback`: `0`

因此：

> 这次“小赢保护卖出”的问题，不是 fixed SL 1.2% 或 2.0% 造成的，而是保护链上的 breakeven 触发时机造成的。

---

## 5. 本轮已落地的优化

### 5.1 已同步到 production

已修改：

- [trading_config_fund_flow.json](D:\AIDCA\AI8\config\trading_config_fund_flow.json)
- [trading_config_fund_flow_live_production.json](D:\AIDCA\AI8\config\trading_config_fund_flow_live_production.json)

本轮唯一实际策略变更：

```json
"breakeven_trigger_pnl_ratio": 0.01
```

保留不变：

```json
"breakeven_lock_ratio": 0.0025
```

### 5.2 最新 production 回测结果

最新同窗回测文件：

- [v2_summary_20260401_122512.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_122512.json)
- [v2_trades_20260401_122512.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_122512.csv)

结果：

- `656` 笔
- 胜率 `87.3%`
- 收益 `+140.18%`
- 盈利因子 `3.28`
- 真最大回撤 `2.97%`

对比本轮优化前基线：

- [v2_summary_20260401_120122.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_120122.json)

旧值：

- `590` 笔
- 胜率 `85.1%`
- 收益 `+97.55%`
- 盈利因子 `2.83`
- 真最大回撤 `3.46%`

---

## 6. 希望 Claude 重点回答的问题

请 Claude 优先回答以下问题：

1. 你是否同意这批“小赢风控卖出”的根因是 `breakeven trigger too early`，而不是 `lock too low`？
2. 在当前策略结构下，把 `breakeven_trigger_pnl_ratio` 从 `0.8%` 延后到 `1.0%`，是否属于逻辑上稳健的 production 优化？
3. 你是否同意本轮不应继续提高 `breakeven_lock_ratio`？
4. `trigger=1.2%` 虽然收益更高，但回撤更大。你是否也会优先选择 `1.0%` 作为 production，而把 `1.2%` 作为下一轮候选？
5. 对于那些仍然存在的 `stop_loss_intrabar` 小赢单，下一轮应继续优化：
   - `trailing activation`
   - `partial TP profile`
   - 还是 pocket-specific protection
6. 在不伤害 `long_reclaim_confirmed / short_retest_reject` 这些主盈利 pocket 的前提下，你最推荐的下一步 close-risk 优化是什么？

---

## 7. 一句话总结

> 本轮优化证明：当前 production 的问题不是“保护太弱”，而是“保护太早”；把 `breakeven` 从 `0.8%` 延后到 `1.0%`，比提高锁盈比例更有效，也更不伤主盈利引擎。
