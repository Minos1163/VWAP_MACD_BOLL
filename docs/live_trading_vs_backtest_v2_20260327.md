# 实盘交易链路梳理与 `backtest_macd_v2.py` 差异说明

**日期**：`2026-03-27`  
**目的**：梳理当前实盘版本的开仓链路、门槛、风控逻辑、平仓逻辑，并与 [`scripts/backtest_macd_v2.py`](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py) 做结构性对比，供 Claude 继续给建议。  
**结论先行**：你当前看到的 `+132.40% / 73.7%`，是**策略级回测器**的结果，不是“完整实盘执行栈等价回放”的结果。它可以证明 `MACD V2` 信号层有 alpha，但**不能直接证明当前实盘外层链路与该回测完全同构**。

## 1. 代码入口与责任边界

### 1.1 实盘入口

- 主入口：[src/main.py](/D:/AIDCA/AI8/src/main.py)
- 实际运行对象：[src/app/fund_flow_bot.py](/D:/AIDCA/AI8/src/app/fund_flow_bot.py)
- 核心模块：
  - 决策引擎：[src/fund_flow/decision_engine.py](/D:/AIDCA/AI8/src/fund_flow/decision_engine.py)
  - 风控校验：[src/fund_flow/risk_engine.py](/D:/AIDCA/AI8/src/fund_flow/risk_engine.py)
  - 执行路由：[src/fund_flow/execution_router.py](/D:/AIDCA/AI8/src/fund_flow/execution_router.py)

### 1.2 回测入口

- 策略级回测器：[scripts/backtest_macd_v2.py](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py)
- 核心策略引擎：`MACDStrategyV2Engine`

### 1.3 最重要的边界

- **实盘不是直接把 `MACDStrategyV2Engine` 的信号拿去下单。**
- 实盘是：`市场数据/触发器 -> 决策引擎 -> 外层过滤/风控 -> 候选排序 -> 执行路由 -> 保护单/SLA修复`
- 回测是：`历史K线 -> MACD V2 信号 -> 本地模拟撮合 -> 本地止盈止损/缩量退出`

这两者共享一部分参数与同一份配置文件，但**行为层并不等价**。

## 2. 实盘链路梳理

### 2.1 启动与初始化

- [`src/main.py`](/D:/AIDCA/AI8/src/main.py) 会做 live confirmation，然后强制 `BINANCE_DRY_RUN=0`，启动 `TradingBot`。
- `TradingBot` 初始化时会加载：
  - `FundFlowAttributionEngine`
  - `FundFlowRiskEngine`
  - `FundFlowDecisionEngine`
  - `FundFlowExecutionRouter`
  - `MarketIngestionService`
  - `TriggerEngine`
  - `MarketStorage`

### 2.2 每轮循环的总体流程

关键函数：

- [`_prepare_cycle_context()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:6622)
- [`_handle_symbol_protection_and_sla()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:6829)
- [`_execute_symbol_signal_decision()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:7107)
- [`_finalize_entries()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:8406)

每轮的高层顺序是：

1. 重新加载配置与 symbol 集合。
2. 读取账户状态、持仓、风险守卫状态。
3. 判断当前是否允许新开仓。
4. 每个 symbol 拉市场数据。
5. 先检查保护单缺失、保护 SLA 超时、修复/强平逻辑。
6. 再做信号决策与开平仓分流。
7. 把新开仓候选先放入 `pending_new_entries`。
8. 统一排序、容量控制、可选 AI 终审后再下单。

### 2.3 实盘开仓链路

关键函数：

- [`_execute_symbol_signal_decision()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:7107)
- [`_apply_ma10_macd_entry_filter()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:2875)
- [`_apply_pretrade_risk_gate()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:3170)
- [`_resolve_dynamic_max_active_symbols()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:2259)

实盘开仓不是单一阈值，而是一条漏斗：

1. **市场快照与信号物化**
   - 先生成 `flow_snapshot / flow_context`
   - 评估极端波动冷却
   - 评估冲突 symbol 冷却
   - 触发去重，避免重复触发

2. **局部决策**
   - 调用 `FundFlowDecisionEngine.decide(...)`
   - 当前 bot 侧默认先跑本地决策：`use_weight_router=False, use_ai_weights=False`
   - 若配置启用，可对持仓或候选做 AI 复核

3. **入场窗口约束**
   - 如果当前不在允许入场窗口，开仓信号可直接跳过或被降级为 `HOLD`

4. **外层过滤**
   - `signal_pool`
   - `MA10/MACD` 入场硬过滤
   - `pretrade_risk_gate`

5. **账户级与组合级门槛**
   - `max_active_symbols`
   - `max_symbol_position_portion`
   - `min_open_portion`
   - 账户冷却 / loss streak / protection gap block

6. **已有持仓时的分支**
   - 如果已有仓位，不一定直接反手
   - 可能走 `CLOSE`
   - 可能触发 `DCA`
   - 可能触发 `winner pyramiding`
   - 也可能被冲突保护降级为 `HOLD` / `REDUCE`

7. **新开仓候选统一排序**
   - 候选先放入 `pending_new_entries`
   - `_finalize_entries()` 里按 score 排序
   - 容量不足会跳过后排候选
   - 若启用 AI flat review，会做 shortlist / final review

### 2.4 实盘的主要开仓门槛

来自 [`FundFlowDecisionEngine`](/D:/AIDCA/AI8/src/fund_flow/decision_engine.py) 与 [`FundFlowRiskEngine`](/D:/AIDCA/AI8/src/fund_flow/risk_engine.py)：

- `long_open_threshold`
- `short_open_threshold`
- `close_threshold`
- `min_open_portion`
- `max_open_portion`
- `min_leverage / default_leverage / max_leverage`
- `price_deviation_limit_percent`
- `max_active_symbols`
- `max_symbol_position_portion`
- `entry_slippage`
- `take_profit_pct`
- `stop_loss_pct`
- `reverse_close_confirm_bars`

但实际是否能下单，还要经过 bot 外层链的二次门控，所以**实盘的真实门槛是“决策阈值 + 外层过滤阈值 + 账户/组合约束”的叠加**。

### 2.5 实盘风控逻辑

#### A. 执行前风险闸门

核心函数：[`_apply_pretrade_risk_gate()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:3170)

它会综合：

- 趋势强度
- CVD/动量强度
- ATR 波动占比
- 当前持仓回撤
- 价格变化
- 可用杠杆
- 当前权益占用
- 1m 执行质量

然后把交易降级为：

- 继续放行
- 缩仓 / 降杠杆
- 直接 `HOLD`
- 直接 `EXIT`

特别要注意：

- 对开仓，`pretrade_risk_gate` 可以直接把 `BUY/SELL` 打成 `HOLD`
- 对持仓，`EXIT` 也不一定立刻平，会再经过确认逻辑，例如：
  - 持仓时长
  - 得分阈值
  - drawdown override
  - trend hold
  - trap grace
  - profit lock
  - fast loss exit
  - MACD anchor hold

#### B. 仓位保护与 SLA

关键函数：

- [`_handle_symbol_protection_and_sla()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:6829)
- [`_post_execution_protection_hook()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:6457)

逻辑要点：

- 下单后会检查 TP/SL 是否真的挂上
- 若保护单缺失，会尝试修复
- 修复失败时，可根据配置强制平仓，避免裸仓
- 若某个 symbol 存在 protection gap，本轮可以直接禁止新的开仓候选

#### C. DCA / 马丁

关键函数：[`_build_dca_decision()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:3677)

实盘支持基于：

- 当前回撤阈值
- 当前 DCA stage
- stage multiplier
- 最大加仓次数
- 杠杆上限条件

生成新的加仓决策。

#### D. 盈利加仓

关键函数：[`_build_winner_pyramiding_decision()`](/D:/AIDCA/AI8/src/app/fund_flow_bot.py:3775)

实盘支持在满足下列条件时顺势加仓：

- 已有盈利达到阈值
- signal type 合规
- EMA 结构状态合规
- signal score 合规
- VWAP score 合规

这意味着实盘的持仓规模演化，**不一定等于首次入场时的单次仓位**。

### 2.6 实盘平仓逻辑

平仓来源不止一种：

1. `FundFlowDecisionEngine` 正常给出 `CLOSE`
2. `pretrade_risk_gate` 触发 `EXIT`
3. 冲突保护/风险保护触发减仓或平仓
4. 保护单缺失修复失败后触发强平

执行细节在 [`FundFlowExecutionRouter.execute_decision()`](/D:/AIDCA/AI8/src/fund_flow/execution_router.py)：

- 先从交易所实时重查仓位，避免快照过期
- 计算部分平仓或全平数量
- 先走 `IOC reduce-only` 多次重试
- 若被 `reduce-only` 拒绝，会立刻再查交易所实时仓位
- IOC 耗尽后可退化到 `GTC reduce-only`
- 若配置启用，还可退化到 `MARKET reduce-only`

这说明实盘平仓是**交易所驱动的异步执行问题**，不是纯粹的本地逻辑分支。

### 2.7 实盘执行层特征

来自 [`FundFlowExecutionRouter`](/D:/AIDCA/AI8/src/fund_flow/execution_router.py)：

- 开仓前会先做 `risk.validate_decision`
- 会同步交易所杠杆；若同步失败，默认可直接阻止开仓
- 会做最小下单量 / 最小名义价值 / 数量格式化兜底
- 开仓退化链：
  - 初始 `LIMIT`
  - `IOC` 重试
  - `GTC` fallback
  - 可选 `MARKET` fallback
- 开仓成交后，会立即下 TP/SL
- TP/SL 不完整时，会尝试 rollback flatten

## 3. `backtest_macd_v2.py` 的实际链路

### 3.1 回测器的职责

关键函数：

- [`prepare_timeframe_data()`](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py:663)
- [`load_symbol_data()`](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py:751)
- [`filter_market_data_by_time_range()`](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py:817)
- [`analyze_bar()`](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py:1210)
- [`execute_trade()`](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py:1388)
- [`process_pending_orders()`](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py:1712)
- [`check_stops()`](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py:1754)
- [`run_backtest()`](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py:1956)

它的流程是：

1. 从本地 parquet 读取 `15m / 1h / 4h` 数据
2. 计算指标
3. 每个 `15m` bar 调 `MACDStrategyV2Engine.analyze(...)`
4. 若信号满足阈值，则创建一个本地 pending order
5. 下一根 bar 再尝试撮合
6. 用同一根 `15m OHLC` 近似 stop/tp/breakeven/shrink exit/reverse exit
7. 本地更新资金曲线

### 3.2 回测开仓条件

`execute_trade()` 的核心约束：

- `signal.direction != neutral`
- `signal.signal_score >= threshold`
- 当前没有已有持仓
- 当前没有该 symbol 的 pending order
- 未处于 entry cooldown
- 当前持仓数 + pending 数 < `max_positions`
- `calculate_position_size()` 算出仓位 > 0
- 保证金 + 预计手续费可覆盖

然后它会：

- 计算止损价
- 计算固定止盈价和可选分级 TP
- 预扣保证金
- 生成本地 pending order
- 价格用 `entry_slippage` 调整

### 3.3 回测平仓条件

`check_stops()` 的顺序是近似型的本地规则：

1. 先做 breakeven stop 更新
2. 先判 stop loss
3. 再判 TP levels / take profit
4. 再判 4H shrink exit
5. 再判 reverse signal exit
6. 再判 max hold

这和实盘交易所里的真实成交顺序不是一回事，只是一个**OHLC 近似模拟**。

### 3.4 回测里默认会弱化/关闭的东西

`build_strategy_config()` 里有一个很重要的差异：

- `backtest.disable_cvd_decision_logic` 默认是 `true`

也就是说，当前这个回测器常见的默认路径是：

- CVD bonus / veto 不按实盘完整决策逻辑参与
- AI 权重与外层 AI gate 也不参与

## 4. 实盘 vs 回测：差异总表

| 维度 | 实盘 `fund_flow_bot` | 回测 `backtest_macd_v2.py` | 影响 |
|---|---|---|---|
| 决策主体 | `FundFlowDecisionEngine` + bot 外层链 | `MACDStrategyV2Engine` + 本地模拟 | 实盘的信号不是纯 V2 原始信号 |
| 外层过滤 | 有：signal pool、MA10/MACD filter、pretrade risk gate、entry window、dedupe | 基本没有这层 bot 外壳 | 回测收益不能直接映射到实盘执行收益 |
| AI/权重路由 | 可选启用 | 无 | 实盘可能多一层非线性偏移 |
| 开仓执行 | 交易所真实下单，LIMIT/IOC/GTC/MARKET 退化链 | 本地 pending order，下一根 K 线撮合 | 成交率、滑点、排队完全不同 |
| 杠杆 | 会同步交易所杠杆 | 本地变量 | 实盘存在“同步失败即禁开仓” |
| 数量处理 | 真实最小数量/最小名义价值/precision 兜底 | 本地近似 | 小仓位行为不同 |
| 保护单 | 真正下 TP/SL，并做完整性检查 | 本地字段模拟 | 实盘多了保护单缺失、修复、回滚路径 |
| 保护 SLA | 有 | 无 | 实盘可能因保护失败而强平，回测不会 |
| 持仓加仓 | 有 DCA / winner pyramiding | 没有 | 实盘仓位演化更复杂 |
| 平仓执行 | 真实仓位同步 + IOC/GTC/MARKET reduce-only | 15m OHLC 近似命中 | 回测的 exit price 与 fill path 更理想化 |
| 平仓触发源 | 决策引擎 + pretrade EXIT + 冲突保护 + 保护失败强平 | stop/tp/shrink/reverse/max hold | 实盘 exit 来源更多、更杂 |
| CVD 逻辑 | 实盘可完整参与 | 回测默认常常关闭 | 两边有效特征集合不同 |
| 账户级风控 | 有账户冷却、risk guard、protection gap block | 仅有限本地 cooldown | 实盘会更频繁地“因为风险系统不让做” |

## 5. 这对当前 `132%` 回测结果意味着什么

当前 `+132.40% / 73.7%` 结果可以说明：

- `MACD V2` 在这 30 天窗口内，作为**策略级信号与出场规则**是有效的
- 配置里的 blacklist 在该窗口内帮助了组合质量

但它**不能直接说明**：

- 当前 `fund_flow_bot` 实盘外层链路会复现同等收益曲线
- 当前 live execution router 的真实成交、保护单、fallback、SLA 修复，会与策略回测收益等价
- 当前 `pretrade_risk_gate / signal_pool / AI review / DCA / pyramiding` 对收益是增强还是稀释

更准确地说：

- **132% 是“策略层 alpha”证明**
- **不是“完整实盘执行栈已被等价验证”的证明**

## 6. 我认为最该让 Claude 重点审查的问题

建议 Claude 重点回答这几件事：

1. 当前 `fund_flow_bot` 的外层开仓链，哪些环节最可能把 `backtest_macd_v2.py` 的 alpha 稀释掉？
2. `pretrade_risk_gate` 在当前参数下，更像“风险保护”还是“过度抑制入场”？
3. DCA / winner pyramiding 是否应该在首批实盘部署中默认关闭，先追求链路可解释性？
4. 实盘保护单 + SLA 修复 + 强平回滚，是否存在把正常趋势单误杀的风险？
5. 如果目标是“尽量贴近 132% 那条回测曲线”，实盘侧最应该先做哪三处收敛：
   - 关闭某些外层过滤
   - 收紧某些 live-only 风控
   - 或改用 bot-like 回放替代当前策略级回测作为部署基线

## 7. 给你的最短结论

- 可以部署，但请把这次部署理解为：**部署的是当前 live stack，不是部署那条纯策略级回测曲线。**
- 如果你要 Claude 给建议，最核心的问题不是“这个策略有没有 alpha”，而是：
  - **当前实盘外层链路是否过度改写了这条 alpha。**

## 8. 相关文件

- 实盘入口：[src/main.py](/D:/AIDCA/AI8/src/main.py)
- 实盘主循环：[src/app/fund_flow_bot.py](/D:/AIDCA/AI8/src/app/fund_flow_bot.py)
- 决策引擎：[src/fund_flow/decision_engine.py](/D:/AIDCA/AI8/src/fund_flow/decision_engine.py)
- 风控引擎：[src/fund_flow/risk_engine.py](/D:/AIDCA/AI8/src/fund_flow/risk_engine.py)
- 执行路由：[src/fund_flow/execution_router.py](/D:/AIDCA/AI8/src/fund_flow/execution_router.py)
- 策略级回测器：[scripts/backtest_macd_v2.py](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py)
- 30天高收益回测摘要：[output/backtest/v2_summary_20260327_174425.json](/D:/AIDCA/AI8/output/backtest/v2_summary_20260327_174425.json)
