# 实盘交易链路梳理与 `backtest_macd_v2.py` 差异说明（收敛后版）

日期: `2026-03-27`
目的: 基于当前已经完成的收敛修改，重新梳理实盘版本的开仓链路、门槛、风控逻辑、平仓逻辑，并与 `scripts/backtest_macd_v2.py` 做最新对比，供 Claude 继续审查。

---

## 1. 结论先行

当前 live stack 已经完成一轮明显收敛：

- 已对齐回测关键阈值: `long_open_threshold / short_open_threshold / close_threshold / stop_loss_pct / take_profit_pct / entry_slippage / reverse_close_confirm_bars / max_active_symbols`
- 已显式关闭 live 外层的 `MA10/MACD` 硬过滤
- 已显式关闭 `DCA` 和 `winner pyramiding`
- 已显式关闭 `entry_window`
- 已显式关闭 CVD 外层 veto / context filter
- 已新增 `alpha_dilution_monitor` 漏斗监控

并且当前对齐脚本已通过:

```text
[alignment] OK: live config matches the selected backtest baseline.
```

同时，我已经基于当前 live production 配置复制了一份回测专用配置，并完成最近 30 天回测：

- 回测配置副本: `config/trading_config_fund_flow_live_backtest_converged_20260327.json`
- 回测摘要: `output/backtest/v2_summary_20260327_191149.json`

本次 30 天结果：

- 收益率: `+63.10%`
- 胜率: `70.35%`
- 总交易数: `607`
- 盈利因子: `1.44`
- 真最大回撤: `14.52%`

但这不代表实盘已经与回测完全同构。当前仍然存在几类 live-only 差异：

- 实盘有真实交易所执行链，回测只有本地撮合近似
- 实盘仍保留 `pretrade_risk_gate`，只是已经降为 hard-rules-only
- 实盘有保护单修复和 `protection_sla` 强平链路，回测没有
- 实盘仍存在触发去重、账户冷却、保护单缺失阻断、可选 AI review 等外层运行时机制

所以更准确的说法是：

> 当前实盘已经从“明显偏离回测的多层外壳”收敛到“参数大体对齐，但仍保留少量 live-only 执行与安全机制”的状态。

---

## 2. 当前基线文件

- 实盘主循环: `src/app/fund_flow_bot.py`
- 实盘配置: `config/trading_config_fund_flow_live_production.json`
- 回测脚本: `scripts/backtest_macd_v2.py`
- 收敛 TODO: `docs/live_trading_convergence_todolist_20260327.md`
- 部署前对齐检查: `scripts/validate_live_backtest_alignment.py`

---

## 3. 当前实盘开仓链路

## 3.1 高层顺序

当前实盘每轮处理单个 symbol 的主要顺序是：

1. 物化 `flow_snapshot / flow_context`
2. 评估极端波动冷却
3. 评估冲突 symbol 冷却
4. 触发去重
5. 先处理保护单缺失 / SLA / 修复逻辑
6. 运行 `FundFlowDecisionEngine.decide(...)` 得到原始方向
7. 可选做持仓 AI review
8. 经过入场窗口阶段
9. 经过 `signal_pool`
10. 经过 `MA10/MACD` 外层过滤
11. 经过 `pretrade_risk_gate`
12. 进入已有持仓分支或新开仓候选池
13. 在 `_finalize_entries()` 中统一排序、容量检查、可选 AI final review
14. 交给 `FundFlowExecutionRouter` 做真实下单

## 3.2 当前 live 实际启用状态

虽然代码路径仍然保留完整漏斗，但当前配置状态已经变成：

- `entry_window.enabled = false`
- `ma10_macd_confluence.enabled = false`
- `ma10_macd_confluence.entry_hard_filter = false`
- `dca_martingale_enabled = false`
- `dca_max_additions = 0`
- `winner_pyramiding.enabled = false`
- `macd_mtf_strategy_v2.entry_filters.enable_flip_bullish_cvd_context_filter = false`
- `macd_mtf_strategy_v2.leverage_config.use_cvd_veto_filter = false`
- `macd_mtf_strategy_v2.cvd_filter_config.enabled = false`

也就是说，当前实盘开仓漏斗里，真正仍会实质改写回测信号的外层项，主要剩下：

- `signal_pool`
- `pretrade_risk_gate`
- 容量检查
- 账户冷却
- 保护单缺失阻断
- 可选 AI review
- 真实执行成交流程

## 3.3 当前关键开仓门槛

当前 live config 中已显式写死的核心门槛：

| 项目 | 当前 live 值 |
|---|---:|
| `default_target_portion` | `0.6` |
| `add_position_portion` | `0.6` |
| `max_symbol_position_portion` | `0.6` |
| `max_active_symbols` | `3` |
| `min_open_portion` | `0.06` |
| `min_leverage` | `2` |
| `default_leverage` | `3` |
| `max_leverage` | `4` |
| `long_open_threshold` | `0.09` |
| `short_open_threshold` | `0.07` |
| `close_threshold` | `0.3` |
| `entry_slippage` | `0.0015` |
| `stop_loss_pct` | `0.02` |
| `take_profit_pct` | `0.02` |
| `reverse_close_confirm_bars` | `2` |
| `breakeven_trigger_pnl_ratio` | `0.008` |
| `breakeven_lock_ratio` | `0.002` |

这组参数已经与当前回测基线一致。

---

## 4. 当前实盘风控逻辑

## 4.1 `pretrade_risk_gate`

当前最关键的 live-only 风控层是 `pretrade_risk_gate`。

现在它不是旧版本那种多项打分后轻易把信号压成 `HOLD` 的模式，而是：

- `enabled = true`
- `use_hard_rules_only = true`
- `cvd_veto_enabled = false`
- `atr_ratio_hard_block = 3.5`
- `equity_usage_block = 0.85`
- `dd_exit_threshold = 0.10`
- `entry_block_actions = ["BLOCK"]`
- `exit_close_ratio = 1.0`

这意味着它当前只保留三类硬规则：

1. ATR 极端异常时阻止新开仓
2. 资金占用过高时阻止新开仓
3. 已有持仓且深度亏损时允许直接退出

与旧版本相比，它已经明显不再是“软性压缩 alpha 的大过滤器”，但它仍然是回测里没有的一层 live-only 干预。

## 4.2 保护单与 `protection_sla`

当前 live 仍保留保护单完整性链路：

- `protection_sla_enabled = true`
- `protection_sla_seconds = 300`
- `protection_sla_force_flatten = true`
- `protection_sla_pnl_grace_threshold = -0.005`
- `protection_sla_api_health_check_before_force = true`

当前逻辑不是“保护修复失败立刻强平”，而是：

1. 先重试保护单修复
2. 只有超时后再进入强平判定
3. 当前持仓必须已经差到超过 `-0.5%`
4. 同时 API 健康检查必须正常

这层明显比旧逻辑温和，但回测里仍然完全不存在。

## 4.3 账户级 / 运行时风控

当前实盘还有这些回测中没有的运行时安全层：

- trigger dedupe
- 极端波动冷却
- 冲突 symbol 冷却
- 账户冷却 / 连续亏损限制
- 保护单缺失时阻断新开仓
- 容量检查前后的候选排序与截断

这些机制不是参数层差异，而是“运行时行为差异”。

---

## 5. 当前实盘平仓链路

当前 live 平仓不只来自一种来源，主要有：

1. 决策引擎给出 `CLOSE`
2. `pretrade_risk_gate` 对已有持仓给出 `EXIT`
3. 冲突保护 / 冷却逻辑触发减仓或退出
4. 保护单修复失败且满足 SLA 条件后强平
5. TP/SL 在交易所真实触发

执行层面，当前 live 仍是交易所真实执行路径：

- 先同步实时仓位
- 计算 reduce-only 数量
- 先走 `IOC reduce-only`
- 不行再尝试 `GTC reduce-only`
- 需要时再退化到 `MARKET reduce-only`

这一点和回测的“在同一根或下一根 15m OHLC 上近似命中”本质不同。

---

## 6. 当前回测链路

`scripts/backtest_macd_v2.py` 当前链路仍然是标准策略级回放：

1. 读取本地 parquet 数据
2. 构建 `BacktestConfig`
3. 用 `build_strategy_config()` 组装 `MACDStrategyV2Config`
4. 每个 `15m` bar 运行 `MACDStrategyV2Engine`
5. 用本地 pending order 做下一根 bar 的近似成交
6. 用本地 OHLC 顺序检查：
   - stop loss
   - take profit
   - breakeven
   - 4H shrink exit
   - reverse exit
   - max hold

当前回测器没有：

- 交易所杠杆同步失败
- 最小下单量/最小名义价值约束
- 保护单补挂 / 修复 / 回滚
- `protection_sla`
- 账户级触发去重
- 真实 reduce-only 成交失败
- 真正的网络/API 故障

---

## 7. 当前已经收敛的部分

这次收敛后，以下差异已经基本消掉：

| 项目 | 当前状态 |
|---|---|
| 开仓阈值 | 已对齐 |
| 平仓阈值 | 已对齐 |
| 仓位比例 | 已对齐 |
| 杠杆上下限 | 已对齐 |
| `entry_slippage` | 已对齐 |
| `stop_loss_pct / take_profit_pct` | 已对齐 |
| `reverse_close_confirm_bars` | 已对齐 |
| `entry_window` | 已关闭，避免削减机会集 |
| `MA10/MACD` 外层硬过滤 | 已关闭 |
| DCA | 已关闭 |
| winner pyramiding | 已关闭 |
| CVD 外层 veto / context filter | 已关闭 |

---

## 8. 当前仍然不同的部分

在进入差异表之前，先看这次“按当前 live 参数复制的策略级回测”与此前高收益基线的关系：

| 版本 | 配置语义 | 30天收益 | 胜率 | 说明 |
|---|---|---:|---:|---|
| 高收益基线 | 旧的策略级回测基线 | `+132.40%` | `73.7%` | 更接近“信号层 alpha 上限” |
| 当前 live 参数复制回测 | 使用 `trading_config_fund_flow_live_backtest_converged_20260327.json` | `+63.10%` | `70.35%` | 代表“当前 live 参数在纯策略级回测中的表现” |

这组新结果说明两件事：

1. 当前 live 参数本身依然有明显 alpha，不是已经失效的参数集
2. 即使不引入真实交易所执行和 live-only 外层机制，仅仅换成当前这套 live 参数，策略级收益也已经显著低于早先那条 `+132.40%` 基线

所以现在要把问题拆成两层：

- 参数层差异: 已经足以让 `132% -> 63%`
- 执行层差异: 会在此基础上继续拉低 live 实际表现

换句话说，Claude 后续不能只盯着执行层，也必须审查“当前 live 参数集本身是否已经偏离那条高收益回测基线”。

这部分是现在最值得 Claude 继续审查的地方。

| 差异项 | live 当前状态 | backtest 当前状态 | 影响判断 |
|---|---|---|---|
| `pretrade_risk_gate` | 开启，hard-rules-only | 无 | 仍可能少量稀释 alpha |
| `protection_sla` | 开启，300s + PnL/API 宽限 | 无 | 防裸仓，但会改变极端行情下的退出分布 |
| 真实交易所执行 | 有 | 无 | 成交率、滑点、挂单路径不可能完全同构 |
| TP/SL 真实挂单与修复 | 有 | 无 | live 特有执行风险 |
| trigger dedupe / 冷却 / protection gap block | 有 | 无 | live 触发频率会低于纯策略回放 |
| 可选 AI review | live 路径保留 | 无 | 如果实际开启，会进一步偏离纯回测 |
| alpha dilution logging | 有 | 无 | 这是监控层，不影响信号，但说明 live 是多层漏斗 |

---

## 9. 当前最准确的判断

如果用一句话概括现在的状态：

> 当前 live stack 已经不再是“很多外层过滤叠加在回测之上”，而是“核心阈值基本与回测对齐，只保留少量部署安全与真实执行相关的 live-only 层”。

所以现在更适合问 Claude 的问题，不再是：

- “为什么实盘和回测完全不一样？”

而应该变成：

- “在当前已经收敛后的 live-only 差异里，哪一层最可能继续稀释回测 alpha？”

---

## 10. 建议 Claude 重点审查的问题

建议让 Claude 重点回答下面 6 个问题：

1. 当前 `pretrade_risk_gate` 的 hard-rules-only 版本，是否还可能过度拦截本应成交的优质信号？
2. 当前 `protection_sla` 的 `300s + -0.5% + API health` 逻辑，是否仍然可能误杀趋势单？
3. `signal_pool` 在当前 live 路径中是否仍然存在隐性过滤，是否值得继续下沉或关闭？
4. `schedule.ai_review_enabled = true` 的前提下，live 是否仍存在 AI review 对纯策略信号的偏移？
5. 当前 backtest 是否需要增加一个“bot-like replay”层，来更真实模拟 live 的：
   - trigger dedupe
   - protection gap block
   - pretrade hard rules
   - execution fallback
6. 当前最应该优先观察的 alpha 稀释层，是不是：
   - `after_signal_pool`
   - `after_pretrade_gate`
   - `after_capacity_check`
   - `actually_executed`

---

## 11. Claude 可直接接手的结论

- 当前 live 与 backtest 的“参数层差异”已经大幅缩小
- 但“按当前 live 参数复制的策略级回测”只有 `+63.10% / 70.35%`，已经显著低于前面的 `+132.40% / 73.7%`
- 这说明问题不再只是 live 执行链，还包括当前 live 参数集本身的收益压缩
- 当前真正剩下的差异，主要集中在“真实执行”和“部署安全层”
- 如果实盘收益仍明显落后于回测，下一步不该再盲调策略阈值，而应优先检查：
  - 当前 live 参数集与 `132%` 基线参数相比，到底哪些改变贡献了主要收益下滑
  - `pretrade_risk_gate`
  - `protection_sla`
  - `signal_pool`
  - AI review 是否仍在改写候选
  - 实际成交漏损是否集中在执行层

---

## 12. 相关文件

- `src/app/fund_flow_bot.py`
- `config/trading_config_fund_flow_live_production.json`
- `config/trading_config_fund_flow_live_backtest_converged_20260327.json`
- `scripts/backtest_macd_v2.py`
- `scripts/validate_live_backtest_alignment.py`
- `docs/live_trading_convergence_todolist_20260327.md`
- `output/backtest/v2_summary_20260327_191149.json`
