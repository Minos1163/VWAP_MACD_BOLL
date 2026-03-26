# 策略诊断书

**日期**：`2026-03-25`  
**诊断对象**：`fund_flow bot / macd_strategy_v2`  
**数据窗口**：`2026-02-23 ~ 2026-03-24`（约 30 天）  
**核心代码**：

- 策略主体：[macd_strategy_v2.py](/d:/AIDCA/AI2/src/fund_flow/macd_strategy_v2.py)
- Bot 执行链：[fund_flow_bot.py](/d:/AIDCA/AI2/src/app/fund_flow_bot.py)
- 参数装载：[decision_engine.py](/d:/AIDCA/AI2/src/fund_flow/decision_engine.py)
- Bot-like replay：[backtest_fund_flow_bot_like.py](/d:/AIDCA/AI2/scripts/backtest_fund_flow_bot_like.py)
- 策略级回测：[backtest_macd_v2.py](/d:/AIDCA/AI2/scripts/backtest_macd_v2.py)
- 主配置：[trading_config_fund_flow.json](/d:/AIDCA/AI2/config/trading_config_fund_flow.json)

**对比回测**：

| 版本 | 收益率 | 胜率 | PF | 最大回撤 | 交易数 |
|---|---:|---:|---:|---:|---:|
| **v2（策略级，无外层链）** | **+229.06%** | **78.49%** | **4.19** | **12.01%** | **265** |
| **bot-like（含外层链）** | +36.15% | 61.87% | 1.33 | 24.54% | 278 |

**数据来源**：

- [v2_summary_20260325_131704.json](/d:/AIDCA/AI2/output/backtest/v2_summary_20260325_131704.json)
- [v2_trades_20260325_131704.csv](/d:/AIDCA/AI2/output/backtest/v2_trades_20260325_131704.csv)
- [bot_like_summary_20260325_171201.json](/d:/AIDCA/AI2/output/backtest/bot_like_summary_20260325_171201.json)
- [bot_like_trades_20260325_171201.csv](/d:/AIDCA/AI2/output/backtest/bot_like_trades_20260325_171201.csv)

---

## 一、根因总结（先行结论）

> bot-like 与 v2 的收益差距达到 `6.3x`，核心不是先去微调入场阈值，而是**两个结构性问题叠加**：
>
> 1. **止损 / 止盈结构倒置**：bot-like 当前是 `大止损 + 固定小止盈`，直接切断了 v2 最核心的趋势跟踪 alpha
> 2. **外层开仓链未显著提升信号质量**：它没有把坏信号有效过滤掉，反而让部分优质信号退化，同时压缩了仓位规模

我对当前问题的判断是：

- 先修出场结构，比先调入场阈值更重要
- 先做外层链消融，比继续堆条件更重要
- `v2 = 策略 alpha`，`bot-like = 当前系统执行结果`
- 现在 bot-like 的问题主要在 `执行结构`，不是先天没有 alpha

---

## 二、量化诊断：数据对比

### 2.1 退出机制对比

| 退出方式 | bot-like 次数 | bot-like PnL | v2 次数 | v2 PnL |
|---|---:|---:|---:|---:|
| 止损（`stop_loss`） | **93** | **-11,322** | **216** | **-4,100** |
| 固定止盈（`take_profit_intrabar`） | 145 | +17,112 | 0 | — |
| **4H 缩量退出** | **15** | **+289** | **39** | **+26,519** |
| 信号反转退出 | 0 | — | 8 | -224 |

说明：

- bot-like 的止损统计包含：
  - `stop_loss_intrabar = 70`
  - `stop_loss_intrabar_both_hit = 23`
- bot-like 的 4H 缩量退出包含：
  - `decision_close:macd_v2_4h_shrink_exit_short = 9`
  - `decision_close:macd_v2_4h_shrink_exit_long = 6`
- v2 的趋势核心退出就是 `4h_shrink_exit`

**关键发现**：

- v2 的 `4h_shrink_exit` 39 笔贡献了 **26,519 PnL**，这是本策略最核心的 alpha 来源
- bot-like 只在 15 笔交易中吃到这类退出，而且总贡献只有 **+289**
- bot-like 把大量趋势单提前截断在固定 TP 上，导致大行情利润上限被硬封顶

### 2.2 单笔盈亏对比

| 指标 | bot-like | v2 |
|---|---:|---:|
| 平均盈利单笔 PnL | +103.16 | **+152.24** |
| 平均亏损单笔 PnL | **-126.06** | -132.52 |
| **盈亏比 R** | **0.82** | **1.15** |
| 平均仓位规模 | 2,437.55 | **4,063.60** |
| 平均单笔 PnL | 15.76 | **90.99** |

**关键发现**：

- bot-like 的平均盈利比平均亏损还小，`R < 1`
- 这意味着 bot-like 的正收益更多依赖“维持较高胜率”，而不是靠单笔盈亏结构赚钱
- v2 则是 `胜率高 + 单笔盈亏结构也更健康`
- bot-like 平均仓位规模比 v2 小约 40%，说明外层链不仅没明显提升质量，还在压缩有效风险暴露

### 2.3 止损幅度对比

| 指标 | bot-like | v2 |
|---|---:|---:|
| `stop_loss_pct` | **0.020** | **0.005** |
| `take_profit_pct` | **0.020** | **0.000** |
| `breakeven_trigger_pnl_ratio` | **0.020** | **0.008** |
| `breakeven_lock_ratio` | 0.005 | 0.002 |
| 止损触发平均 `pnl_pct` | **-1.587%** | **-0.089%** |

**关键发现**：

- bot-like 的止损宽度是 v2 的 `4x`
- bot-like 的保本触发点也更晚
- 结果不是“少止损更好”，而是“每次止损都更伤”
- v2 的逻辑更像：`小亏 + 尽量放大趋势盈利`
- bot-like 的逻辑更像：`大亏 + 小赚快跑`

### 2.4 信号类型质量退化

| 信号类型 | bot-like 胜率 | bot-like PnL | v2 胜率 | v2 PnL |
|---|---:|---:|---:|---:|
| `flip_bearish` | 50.0% | **-739** | **100.0%** | **+3,081** |
| `red_bar_shrinking` | 45.5% | **-206** | 81.8% | +3,697 |
| `green_bar_shrinking` | 63.6% | +564 | **95.7%** | **+4,804** |
| `green_bar_growing` | 63.4% | +1,115 | 80.8% | +4,604 |
| `red_bar_growing` | 67.0% | +3,393 | 70.8% | +7,024 |
| `flip_bullish` | 53.3% | +255 | 81.8% | +900 |

**关键发现**：

- `flip_bearish` 在 v2 是极强信号，在 bot-like 却退化成亏损信号
- `red_bar_shrinking`、`green_bar_shrinking` 也明显退化
- 这说明外层链没有把高质量信号保护好，反而可能在进入时机、候选筛选或仓位分配上引入了冲突

---

## 三、根因拆解

### 根因 1：止损设置过宽，固定止盈过早（主因）

当前 bot-like 运行时口径来自 [bot_like_summary_20260325_171201.json](/d:/AIDCA/AI2/output/backtest/bot_like_summary_20260325_171201.json)：

```json
"stop_loss_pct": 0.02,
"take_profit_pct": 0.02,
"breakeven_trigger_pnl_ratio": 0.02,
"breakeven_lock_ratio": 0.005
```

当前 v2 运行时口径来自 [v2_summary_20260325_131704.json](/d:/AIDCA/AI2/output/backtest/v2_summary_20260325_131704.json)：

```json
"stop_loss_pct": 0.005,
"take_profit_pct": 0.0,
"breakeven_trigger_pnl_ratio": 0.008,
"breakeven_lock_ratio": 0.002
```

**后果**：

- 固定 `2%` 止盈直接截断趋势收益
- 固定 `2%` 止损在 3x 杠杆下对应更大的保证金损失
- `breakeven_trigger_pnl_ratio = 0.02` 太晚，很多单子还没进入保护区就回撤掉了

一句话总结：

- v2 依赖 `让盈利奔跑`
- bot-like 依赖 `尽快兑现小利润`
- 对趋势策略来说，这是一种结构性错配

### 根因 2：外层开仓链叠加过多，但没有证明它真的提升了质量（次因）

当前实盘/类实盘开仓漏斗大致是：

```text
策略信号
-> signal_pool
-> MA10/MACD entry_hard_filter
-> pretrade_risk_gate
-> cooldown / 持仓约束
-> flat_top_n = 2
-> AI final review
-> 执行
```

相关代码位置：

- `signal_pool` 评估：[trigger_engine.py:255](/d:/AIDCA/AI2/src/fund_flow/trigger_engine.py:255)
- MA10/MACD 外层过滤：[fund_flow_bot.py:2641](/d:/AIDCA/AI2/src/app/fund_flow_bot.py:2641)
- `pretrade_risk_gate`：[fund_flow_bot.py:2936](/d:/AIDCA/AI2/src/app/fund_flow_bot.py:2936)
- `flat_top_n` 候选收敛：[fund_flow_bot.py:8169](/d:/AIDCA/AI2/src/app/fund_flow_bot.py:8169)

**问题不在于“门很多”本身，而在于没有证据说明这些门提升了收益质量。**

当前数据反而显示：

- bot-like 的 `flip_bearish` 交易数更多，但质量更差
- bot-like 胜率更低、PF 更低、仓位更小
- 外层链至少没有把这批信号优化成更高质量版本

### 根因 3：`major_symbol -> trend_pool_short_only` 是结构性方向限制

当前配置中，大币趋势路径会落到 `trend_pool_short_only`，而它的关键门槛是：

```json
"min_long_score": 999.0,
"min_short_score": 0.06
```

相关配置：[trading_config_fund_flow.json](/d:/AIDCA/AI2/config/trading_config_fund_flow.json)

覆盖标的包括：

- `BTCUSDT`
- `ETHUSDT`
- `BNBUSDT`
- `SOLUSDT`
- `XAGUSDT`
- `LTCUSDT`
- `DOGEUSDT`

**含义非常直接**：

- 大币 `TREND` 模式下，做多候选会在 pool 层直接被封掉
- 这不是调权重能解决的问题，而是方向权限本身被锁死

### 根因 4：`weight_4h_enhancement` 存在参数口径漂移

在 [decision_engine.py:452](/d:/AIDCA/AI2/src/fund_flow/decision_engine.py:452) 的旧装载分支里，默认回退是 `0.20`。  
在 [decision_engine.py:526](/d:/AIDCA/AI2/src/fund_flow/decision_engine.py:526) 的 V2 装载分支里，默认回退是 `0.10`。

但当前主配置的 `scoring_weights` 没有显式写出这个参数。

**后果**：

- 人工理解的评分结构，可能和实际运行结构不一致
- 阈值调优讨论容易建立在错误前提上

---

## 四、修改建议

### 优先级 P0：先修止损 / 止盈结构

目标：恢复 v2 的趋势跟踪能力。

建议配置方向：

```text
stop_loss_pct = 0.005 ~ 0.008
take_profit_pct = 0.0
breakeven_trigger_pnl_ratio = 0.008 ~ 0.010
breakeven_lock_ratio = 0.002
```

建议理由：

- 缩小止损，降低每次错误入场的伤害
- 取消固定小 TP，把主要退出权交还给趋势结束逻辑
- 更早进入 break-even 保护区

前提提醒：

- 不能直接在 live 上粗暴切 `take_profit_pct = 0.0`
- 必须先确认 bot-like replay 能正确验证 `4h_shrink_exit` 触发链

### 优先级 P1：逐层消融测试外层开仓链

建议做最小消融集：

```text
测试 A：关闭 MA10/MACD entry_hard_filter
测试 B：关闭 pretrade_risk_gate
测试 C：flat_top_n = 2 -> 5
测试 D：关闭 AI final review
测试 E：关闭 B + C + D
```

目标不是一口气放开，而是回答两个问题：

- 哪一层真的在提升胜率
- 哪一层只是在砍量、砍仓位、砍优质信号

### 优先级 P1：对 flip 类信号做单独豁免评估

当前最值得单独审查的是：

- `flip_bearish`
- `flip_bullish`
- `red_bar_shrinking`
- `green_bar_shrinking`

建议验证方向：

- flip 类信号是否应绕过部分 `MA10/MACD entry_hard_filter`
- shrinking 类信号是否被外层链错误延后，错过最佳进入窗口

这里不建议先直接改代码逻辑，而是先做回放消融验证。

### 优先级 P2：放宽大币趋势做多限制

建议方向：

- 将 `trend_pool_short_only` 的方向限制，改为更接近 `trend_pool_major` 的双向门槛
- 即允许大币做多，但提高大币双向评分要求

目标：

- 不再结构性漏掉大币趋势反弹
- 用更高门槛替代“完全禁止”

### 优先级 P2：统一 `weight_4h_enhancement`

建议：

- 在主配置 `scoring_weights` 中显式写出 `weight_4h_enhancement = 0.10`
- 同时统一 [decision_engine.py:452](/d:/AIDCA/AI2/src/fund_flow/decision_engine.py:452) 与 [decision_engine.py:526](/d:/AIDCA/AI2/src/fund_flow/decision_engine.py:526) 的默认值

### 优先级 P3：补齐 bot-like replay 的 live trailing 模拟

实盘里核心保护收紧函数是：

- [_tighten_protection_for_conflict](/d:/AIDCA/AI2/src/app/fund_flow_bot.py:5199)

而当前 replay 侧主要停留在：

- 固定止损
- break-even
- 固定 TP / TP levels

对应文件：

- [backtest_fund_flow_bot_like.py:365](/d:/AIDCA/AI2/scripts/backtest_fund_flow_bot_like.py:365)
- [backtest_macd_v2.py:1701](/d:/AIDCA/AI2/scripts/backtest_macd_v2.py:1701)

这一步应放到后面做，原因是：

- 当前最明显的问题已经是固定止盈/止损结构
- 先修主矛盾，再补 replay 细化模拟，更容易定位变量

---

## 五、修改路径建议（执行顺序）

```text
Step 1
  -> stop_loss_pct: 0.02 -> 0.005~0.008
  -> take_profit_pct: 0.02 -> 0.0
  -> breakeven_trigger_pnl_ratio: 0.02 -> 0.008
  -> 验证 bot-like replay 中 4h_shrink_exit 的触发链

Step 2
  -> 逐层消融测试外层开仓链
  -> 单独审查 flip / shrinking 信号是否被外层链错误过滤
  -> flat_top_n: 2 -> 3~5

Step 3
  -> 放开 major_symbol 做多限制，改成双向高门槛
  -> 显式写入 weight_4h_enhancement = 0.10

Step 4
  -> 补齐 bot-like replay 对 live trailing / protection tightening 的模拟
```

执行原则：

- 一次只改一个维度
- 每一步都要回放验证
- 不要在 replay 逻辑缺失时过度优化阈值

---

## 六、预期改善区间

| 指标 | 当前 bot-like | 目标（Step 1 后） | 目标（Step 1~3 后） |
|---|---:|---:|---:|
| 收益率 | +36% | +80% ~ +120% | +150% ~ +200% |
| 胜率 | 61.87% | 65% ~ 70% | 72% ~ 78% |
| PF | 1.33 | 2.0 ~ 2.5 | 3.0+ |
| 最大回撤 | 24.54% | 15% ~ 18% | 10% ~ 14% |
| 盈亏比 R | 0.82 | 1.0 ~ 1.2 | 1.1 ~ 1.3 |

这是一个**诊断目标区间**，不是当前已经验证完成的结果。

---

## 七、不建议做的事

1. 不要在 bot-like replay 仍保留当前错误出场结构时，继续先调信号阈值  
原因：当前主矛盾是出场结构，而不是先天信号不行。

2. 不要把 v2 的 `+229%` 直接视为 live 可复制结果  
原因：v2 不含完整外层链、AI 终审和账户状态约束。

3. 不要直接把 `take_profit_pct = 0.0` 上 live  
原因：必须先验证 bot-like replay 中 `4h_shrink_exit` 的完整性。

4. 不要同时改多个大项  
原因：同时动止损、外层门、候选收敛、AI 审核，会让因果关系完全不可解释。

---

## 八、附：关键数据速查

### 8.1 bot-like 各信号类型表现

| 信号类型 | 笔数 | 胜率 | PnL |
|---|---:|---:|---:|
| `red_bar_growing` | 115 | 67.0% | +3,393 |
| `green_bar_growing` | 82 | 63.4% | +1,115 |
| `green_bar_shrinking` | 22 | 63.6% | +564 |
| `flip_bullish` | 15 | 53.3% | +255 |
| `red_bar_shrinking` | 22 | 45.5% | **-206** |
| `flip_bearish` | 22 | 50.0% | **-739** |

### 8.2 v2 各信号类型表现

| 信号类型 | 笔数 | 胜率 | PnL |
|---|---:|---:|---:|
| `flip_bearish` | 11 | **100.0%** | **+3,081** |
| `green_bar_shrinking` | 23 | **95.7%** | **+4,804** |
| `red_bar_shrinking` | 22 | 81.8% | +3,698 |
| `flip_bullish` | 11 | 81.8% | +900 |
| `green_bar_growing` | 78 | 80.8% | +4,604 |
| `red_bar_growing` | 120 | 70.8% | +7,024 |

### 8.3 运行时止损 / 止盈口径

| 项目 | bot-like | v2 |
|---|---:|---:|
| `stop_loss_pct` | 0.020 | 0.005 |
| `take_profit_pct` | 0.020 | 0.000 |
| `breakeven_trigger_pnl_ratio` | 0.020 | 0.008 |
| `breakeven_lock_ratio` | 0.005 | 0.002 |

---

*诊断书生成时间：2026-03-25*  
*文档版本：diagnosis-book v1*  
*说明：本文件用于讨论和制定下一步验证路径，不代表所有结论都已在 live 完整复现实证。*

---

## 九、171201 基线还原审计

### 9.1 审计目标

目标不是继续猜测 `171201` 对应的运行口径，而是把它**精确复现**出来，再反向确认当前主配置到底混入了哪些额外漂移。

本次审计直接使用以下文件作为取证源：

- [bot_like_summary_20260325_171201.json](/d:/AIDCA/AI2/output/backtest/bot_like_summary_20260325_171201.json)
- [bot_like_trades_20260325_171201.csv](/d:/AIDCA/AI2/output/backtest/bot_like_trades_20260325_171201.csv)
- [bot_like_ai_advice_20260325_171201.csv](/d:/AIDCA/AI2/output/backtest/bot_like_ai_advice_20260325_171201.csv)

### 9.2 取证结论

从 `171201` summary 可直接确认，当时运行时风险口径是：

```text
stop_loss_pct = 0.02
take_profit_pct = 0.02
breakeven_trigger_pnl_ratio = 0.02
breakeven_lock_ratio = 0.005
```

同时，`171201` 的 AI 终审当时**不是关闭状态**，证据是：

- `stats.ai_advice_logs = 5148`
- 存在 [bot_like_ai_advice_20260325_171201.csv](/d:/AIDCA/AI2/output/backtest/bot_like_ai_advice_20260325_171201.csv)
- CSV 中 `rank` 实际出现到 `3`

这说明当时：

- `ai_review.enabled = true`
- `flat_top_n = 3`

### 9.3 精确复现结果

本次审计构造的精确复现快照文件是：

- [reconstruct_171201_candidate_b.json](/d:/AIDCA/AI2/output/backtest/ablation_20260325_matrix/reconstruct_171201_candidate_b.json)

基于该快照重跑得到：

- [bot_like_summary_20260325_231858.json](/d:/AIDCA/AI2/output/backtest/bot_like_summary_20260325_231858.json)

重跑结果与 `171201` **完全一致**：

| 指标 | 171201 原始结果 | candidate_b 重跑 |
|---|---:|---:|
| 收益率 | +36.15% | +36.15% |
| 胜率 | 61.87% | 61.87% |
| PF | 1.328 | 1.328 |
| 最大回撤 | 24.54% | 24.54% |
| 交易数 | 278 | 278 |
| open_candidates | 10317 | 10317 |
| ai_advice_logs | 5148 | 5148 |

### 9.4 171201 的精确配置口径

复现成功后，可以确认 `171201` 对应的关键口径是：

```text
fund_flow.stop_loss_pct = 0.02
fund_flow.take_profit_pct = 0.02
fund_flow.breakeven_trigger_pnl_ratio = 0.02
fund_flow.breakeven_lock_ratio = 0.005

fund_flow.engine_params.TREND.take_profit_pct = 0.02
fund_flow.engine_params.TREND.short_stop_loss_min_pct = 0.004
fund_flow.engine_params.TREND.short_stop_loss_max_pct = 0.004
fund_flow.engine_params.TREND.tp_break_even_trigger_pnl_ratio = 0.003
fund_flow.engine_params.TREND.tp_break_even_lock_ratio = 0.001
fund_flow.engine_params.TREND.tp_trailing_activate_mfe_ratio = 0.0045
fund_flow.engine_params.TREND.tp_trailing_distance_ratio = 0.0014
fund_flow.engine_params.TREND.stop_loss_pct = 0.004
fund_flow.engine_params.TREND.dynamic_stop_loss.default_risk_pct = 0.01
fund_flow.engine_params.TREND.dynamic_stop_loss.max_risk_pct = 0.015
fund_flow.engine_params.TREND.dynamic_stop_loss.circuit_breaker_fixed_pct = 0.015

fund_flow.major_symbol_signal_pool.trend_pool_id = trend_pool_short_only
fund_flow.ma10_macd_confluence.enabled = false
fund_flow.ma10_macd_confluence.entry_hard_filter = false
fund_flow.pretrade_risk_gate.enabled = false
fund_flow.ai_review.enabled = true
fund_flow.ai_review.flat_top_n = 3
```

### 9.5 混入当前主配置的额外漂移

当前主配置 [trading_config_fund_flow.json](/d:/AIDCA/AI2/config/trading_config_fund_flow.json) 相比 `171201` 基线，已经混入的关键漂移是：

| 配置项 | 171201 基线 | 当前主配置 |
|---|---|---|
| `fund_flow.stop_loss_pct` | `0.02` | `0.005` |
| `fund_flow.take_profit_pct` | `0.02` | `0.0` |
| `fund_flow.breakeven_trigger_pnl_ratio` | `0.02` | `0.008` |
| `fund_flow.breakeven_lock_ratio` | `0.005` | `0.002` |
| `fund_flow.major_symbol_signal_pool.trend_pool_id` | `trend_pool_short_only` | `trend_pool_major` |
| `fund_flow.ai_review.enabled` | `true` | `false` |
| `fund_flow.engine_params.TREND.take_profit_pct` | `0.02` | `0.0` |
| `fund_flow.engine_params.TREND.short_stop_loss_min_pct` | `0.004` | `0.005` |
| `fund_flow.engine_params.TREND.short_stop_loss_max_pct` | `0.004` | `0.005` |
| `fund_flow.engine_params.TREND.tp_break_even_trigger_pnl_ratio` | `0.003` | `0.008` |
| `fund_flow.engine_params.TREND.tp_break_even_lock_ratio` | `0.001` | `0.002` |
| `fund_flow.engine_params.TREND.tp_trailing_activate_mfe_ratio` | `0.0045` | `0.02` |
| `fund_flow.engine_params.TREND.tp_trailing_distance_ratio` | `0.0014` | `0.01` |
| `fund_flow.engine_params.TREND.stop_loss_pct` | `0.004` | `0.005` |
| `fund_flow.engine_params.TREND.dynamic_stop_loss.default_risk_pct` | `0.01` | `0.005` |
| `fund_flow.engine_params.TREND.dynamic_stop_loss.max_risk_pct` | `0.015` | `0.005` |
| `fund_flow.engine_params.TREND.dynamic_stop_loss.circuit_breaker_fixed_pct` | `0.015` | `0.005` |

### 9.6 对原诊断的修正

这次基线还原审计有一个很重要的结论，会直接影响后续讨论方向：

- `171201` 这个正收益 bot-like 基线，**并不是“完整外层链 + 老风险参数”**
- 它当时已经处于：
  - `pretrade_risk_gate = false`
  - `ma10_macd_confluence.enabled = false`
  - `entry_hard_filter = false`

也就是说，之前如果把 `171201` 理解成“外层链完整开启时的 bot-like 基线”，这是不准确的。  
更精确的说法应该是：

- `171201 = 老风险参数 + short_only + AI review 开启 + pretrade gate 关闭 + MA10/MACD entry hard filter 关闭`

这意味着：

1. `171201` 本身已经不是“完整外层链”的结果
2. 后续所有基于 `171201` 的归因讨论，都必须先承认这个前提
3. 第二批消融里真正有边际影响的层，前面矩阵已经证明主要是 `pretrade_risk_gate`

## 十、pretrade_risk_gate 专项审计

### 10.1 审计目标

本节只回答一个问题：

- `pretrade_risk_gate` 到底是不是把 `171201` 那套正收益基线打坏的主因

为了避免再被混合漂移误导，这里分成两条证据链：

1. `171201` 精确基线之上，只单开 `pretrade_risk_gate`
2. 回看 `p0_only -> p0_pretrade_off` 那轮矩阵里，gate 在当前 P0 口径下到底拦了什么

### 10.2 纯隔离回放：171201 基线只开 gate

基于 [reconstruct_171201_candidate_b.json](/d:/AIDCA/AI2/output/backtest/ablation_20260325_matrix/reconstruct_171201_candidate_b.json) 复制出只改一个开关的审计快照：

- [reconstruct_171201_pretrade_gate_on.json](/d:/AIDCA/AI2/output/backtest/pretrade_gate_audit_20260325/reconstruct_171201_pretrade_gate_on.json)

唯一修改：

```text
fund_flow.pretrade_risk_gate.enabled: false -> true
```

基于该快照重跑得到：

- [bot_like_summary_20260326_001011.json](/d:/AIDCA/AI2/output/backtest/bot_like_summary_20260326_001011.json)
- [bot_like_trades_20260326_001011.csv](/d:/AIDCA/AI2/output/backtest/bot_like_trades_20260326_001011.csv)
- [bot_like_equity_curve_20260326_001011.csv](/d:/AIDCA/AI2/output/backtest/bot_like_equity_curve_20260326_001011.csv)

结果与 [bot_like_summary_20260325_231858.json](/d:/AIDCA/AI2/output/backtest/bot_like_summary_20260325_231858.json) **完全一致**：

| 指标 | gate off 基线 | 只开 gate |
|---|---:|---:|
| 收益率 | +36.15% | +36.15% |
| 胜率 | 61.87% | 61.87% |
| PF | 1.328 | 1.328 |
| 交易数 | 278 | 278 |
| open_candidates | 10317 | 10317 |
| ai_advice_logs | 5148 | 5148 |

并且交易明细也完全一致：

```text
len_base = 278
len_new = 278
exact_equal = True
pre_risk_reasons = 0
```

这说明：

- 在 `171201` 的真实盈利基线里，`pretrade_risk_gate` **没有产生净边际影响**
- 它不是把 `171201` 从正收益打坏的根因

### 10.3 为什么 gate 开着却没有影响

代码路径在：

- [_pretrade_risk_gate_config()](/d:/AIDCA/AI2/src/app/fund_flow_bot.py:1536)
- [_apply_pretrade_risk_gate()](/d:/AIDCA/AI2/src/app/fund_flow_bot.py:2936)
- [trend_capture_config 读取 gate 参数](/d:/AIDCA/AI2/src/fund_flow/decision_engine.py:834)

从 [trading_risk_gate.log](/d:/AIDCA/AI2/output/backtest/logs/trading_risk_gate.log) 可以看到，gate 在 `171201` 基线下并不是完全不打分，而是大量返回：

- `action = HOLD`
- `enter = false`
- `exit = false`

也就是说，它在做评估，但这些评估并没有改变最终进入 `open_candidates` 并成交的那批交易。

更准确地说：

- `gate 有动作`
- 但 `gate 没有改变最终成交集合`

### 10.4 P0 口径下，gate 确实会大规模拦截

前面矩阵里的 `p0_only` 与 `p0_pretrade_off` 差异，并不是错觉。  
在当前 P0 口径下，gate 的确很激进，证据来自：

- [pretrade_gate_block_audit_20260325.csv](/d:/AIDCA/AI2/output/backtest/pretrade_gate_block_audit_20260325.csv)
- [p0_only.json](/d:/AIDCA/AI2/output/backtest/ablation_20260325_matrix/p0_only.json)
- [p0_pretrade_off.json](/d:/AIDCA/AI2/output/backtest/ablation_20260325_matrix/p0_pretrade_off.json)

该审计文件显示：

```text
raw BUY/SELL intents = 12414
gated_to_hold = 9803
candidate_blocked = 9783
candidate_kept = 1137
```

其中最主要被拦掉的是：

- `buy + red_bar_growing`：3856
- `sell + green_bar_growing`：3116
- `sell + red_bar_shrinking`：1021

也就是说，在 `p0_only` 这套口径里，gate 不是“轻微筛选”，而是在大规模把原始开仓意图直接改写成 `HOLD`。

### 10.5 为什么前面的矩阵会误导

问题不在矩阵本身，而在矩阵的基座。

`p0_only` 并不是 `171201` 基线加上一个 gate，而是已经混入了整套 P0 风险结构和外层链变化：

| 配置项 | 171201 基线 | p0_only |
|---|---|---|
| `fund_flow.stop_loss_pct` | `0.02` | `0.005` |
| `fund_flow.take_profit_pct` | `0.02` | `0.0` |
| `fund_flow.breakeven_trigger_pnl_ratio` | `0.02` | `0.008` |
| `fund_flow.breakeven_lock_ratio` | `0.005` | `0.002` |
| `fund_flow.ma10_macd_confluence.enabled` | `false` | `true` |
| `fund_flow.ma10_macd_confluence.entry_hard_filter` | `false` | `true` |
| `fund_flow.pretrade_risk_gate.enabled` | `false` | `true` |
| `fund_flow.engine_params.TREND.take_profit_pct` | `0.02` | `0.0` |
| `fund_flow.engine_params.TREND.stop_loss_pct` | `0.004` | `0.005` |
| `fund_flow.engine_params.TREND.dynamic_stop_loss.default_risk_pct` | `0.01` | `0.005` |
| `fund_flow.engine_params.TREND.dynamic_stop_loss.max_risk_pct` | `0.015` | `0.005` |
| `fund_flow.engine_params.TREND.tp_trailing_activate_mfe_ratio` | `0.0045` | `0.02` |
| `fund_flow.engine_params.TREND.tp_trailing_distance_ratio` | `0.0014` | `0.01` |

所以前面的矩阵真正证明的是：

- 在 `P0 风险结构 + MA10/MACD 外层开启` 这套新口径里，`pretrade_risk_gate` 会产生显著边际影响

它**没有证明**：

- `pretrade_risk_gate` 是把 `171201` 正收益基线打坏的元凶

### 10.6 本轮专项审计结论

本轮最重要的结论是：

1. `pretrade_risk_gate` 在 `171201` 精确盈利基线上，净边际影响为 `0`
2. `pretrade_risk_gate` 在 `p0_only` 新口径下，确实会大规模拦截开仓意图
3. 因此前面矩阵里的“gate 很重要”，只能解释为：
   - `gate 对 P0 新结构很敏感`
   - 不是 `gate 单独毁掉了 171201`

### 10.7 对下一步工作的约束

下一步如果继续优化，方向应该改成：

1. 不要再把 `171201` 的失败归因默认甩给 `pretrade_risk_gate`
2. 应该围绕 `P0 风险结构 + MA10/MACD outer chain + gate 输入尺度` 这个组合做联合归因
3. 真正值得做的是专项检查：
   - 为什么 `p0_only` 下会出现 `12414` 个 raw BUY/SELL intents
   - 为什么 gate 在该口径下把其中 `9803` 个直接改写成 `HOLD`
   - 这些被拦掉的，大部分究竟是坏单，还是被新风险结构放出来的伪信号
