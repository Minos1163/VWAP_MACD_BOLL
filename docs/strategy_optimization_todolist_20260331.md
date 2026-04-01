# MACD V2 策略优化 TodoList

日期: 2026-03-31

## 执行清单

- [x] 固化回测基线，保留原始配置快照
- [x] 修复 `iflow` 路径，统一到 `D:\AIDCA\AI8`
- [x] 为 `flip_bullish trial` 增加甜区 score window 支持
- [x] 为 `symbol_overrides` 增加顶层配置读取支持
- [x] 增加 `disable_flip_bullish_trial` 和 `preflip_trial_min_signal_score_override`
- [x] 对 `DOGEUSDT` / `BCHUSDT` / `AAVEUSDT` 增加 trial 禁用覆盖
- [x] 启用 `pretrade_risk_gate.enabled = true`
- [x] 启用 `pretrade_risk_gate.use_hard_rules_only = true`
- [x] 增加 `pretrade_risk_gate.equity_usage_block = 0.60`
- [x] 为新增行为补定向回归测试
- [x] 跑 30 天回测验证完整收紧方案
- [x] 发现 full guardrails 过度收紧，追加 3 组 ablation
- [x] 选出收益/胜率/回撤更平衡的最终方案
- [x] 将最终最优方案写回主配置 `config/trading_config_fund_flow.json`
- [x] 生成最终审核 Markdown

## 候选结果

基线:

- `return = +0.23%`
- `trades = 18`
- `win_rate = 27.78%`
- `max_drawdown = 1.56%`

候选 A: `score window + symbol overrides`

- `return = +0.88%`
- `trades = 5`
- `win_rate = 80.00%`
- `max_drawdown = 0.76%`

候选 B: `score window + symbol overrides + hard-rules gate`

- `return = +0.87%`
- `trades = 4`
- `win_rate = 75.00%`
- `max_drawdown = 0.32%`

候选 C: `full guardrails`

- `return = -0.01%`
- `trades = 1`
- `win_rate = 0.00%`
- `max_drawdown = 0.02%`

## 最终采用

采用候选 B。

原因:

- 与候选 A 收益几乎相同
- 胜率达到 75%
- 回撤显著更低
- 比 full guardrails 保留了足够的交易机会

