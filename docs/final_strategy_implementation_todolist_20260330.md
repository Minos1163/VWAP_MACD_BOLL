# MACD V2 实施 TodoList

**日期**：2026-03-30  
**本轮执行范围**：只落地方案中的 `迭代 #1（门槛消融 A+B）` 与 `迭代 #2（双模风控）`，`#3~#5` 保持待验证状态

## 本轮目标

- [x] 取消 `15m confirm` 作为硬门槛
- [x] 放宽 `4H preflip` 缩量与试探入场总分门槛
- [x] 放宽 `VWAP score filter`
- [x] 引入双模 `partial_tp`
- [x] 引入双模 `trailing_stop`
- [x] 引入 `ATR` 高波动缩仓
- [x] 输出最新开仓逻辑 / 门槛 / 风控 MD 文档
- [x] 运行 30D bot-like 回测

## 测试先行

- [x] `macd_strategy_v2` 默认消融参数测试
- [x] `TradingBot` volatile 模式 partial TP 测试
- [x] `TradingBot` trending 模式 partial TP 测试
- [x] `TradingBot` 双模 trailing 距离测试
- [x] `TradingBot` ATR 缩仓测试

## 代码改造

- [x] 更新 `src/fund_flow/macd_strategy_v2.py` 默认参数
- [x] 更新 `config/trading_config_fund_flow_live_production.json` 对应策略参数
- [x] 更新回测补丁配置 `output/backtest/compare_20260330/patch_enabled_new_controls.json`
- [x] 在 `src/app/fund_flow_bot.py` 增加市场模式识别
- [x] 在 `src/app/fund_flow_bot.py` 增加双模 partial TP / trailing 配置读取
- [x] 在 `src/app/fund_flow_bot.py` 接入 ATR 缩仓到开仓前风控链路
- [x] 更新 partial TP 状态持久化字段

## 回归验证

- [x] 跑 targeted pytest
- [x] 跑 30D bot-like backtest
- [x] 记录 trade_count / win_rate / avg_win / avg_loss / max_drawdown

## 本轮结论

- [x] 接受 `#1 + #2 + #3` 组合
- [x] 拒绝 `#4`（trade count 提升，但收益 / win rate / MDD 变差，已回滚）
- [x] 当前最佳 30D 结果对应 `bot_like_summary_20260331_094721.json`

## 后续待办（本轮不做）

- [ ] `迭代 #3` threshold_check 下调
- [ ] `迭代 #4` L1 ADX / ATR / Regime 放宽
- [ ] `迭代 #5` L3 微结构门槛消融
