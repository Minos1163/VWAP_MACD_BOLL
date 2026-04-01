# MACD V2 第二轮优化 Todo

日期: 2026-03-31
配置基线: `config/trading_config_fund_flow.json`
目标: 在不显著抬高 MDD 的前提下，恢复一部分交易机会，并验证第二轮建议哪些能真实落地。

## Todo

- [x] 核查 TP 双配置是否冲突，确认运行时读取路径
- [x] 核查 `flip_bullish` 的 CVD 过滤是否覆盖 `trial entry`
- [x] 核查 `stable_bear_continuation_min_adx_1h` 的实际接线点
- [x] 核查 `boll_stop_atr_multiplier` 的实际使用位置
- [x] 先写失败测试，再改实现
- [x] 让 `flip_bullish` 的 CVD 过滤具备覆盖 `trial` 的能力
- [x] 修正配置中的 TP 默认值对齐问题
- [x] 放宽 `stable_bear_continuation_min_adx_1h: 30 -> 25`
- [x] 放宽 `boll_stop_atr_multiplier: 0.5 -> 0.8`
- [x] 对 `trial` CVD 过滤做 30 天 ablation
- [x] 对 `trial` CVD 过滤的放宽版 `delta_ratio=0.02` 做 30 天 ablation
- [x] 选择通过验证的主配置，回滚未通过验证的项
- [x] 更新 Claude 审核文档

## 关键结论

- `flip_bullish` 的 CVD 过滤原来只作用于非 `trial` 路径，代码层确实存在漏接。
- 将 CVD 过滤接入 `trial` 之后，严格版和放宽版在 30 天窗口都只剩 `1` 笔交易，说明它会过度压制当前主 alpha 通道。
- 第二轮最终保留的改动是:
  - `risk.take_profit_default_percent = 0.04`
  - `fund_flow.macd_mtf_strategy_v2.entry_filters.stable_bear_continuation_min_adx_1h = 25.0`
  - `fund_flow.macd_mtf_strategy_v2.stop_loss_config.boll_stop_atr_multiplier = 0.8`
- 第二轮未纳入主配置的改动是:
  - `enable_flip_bullish_cvd_context_filter = true`
  - 原因: 30 天回测会把交易数从 `4` 压到 `1`

## 结果摘要

- 第一轮主配置基线: `+0.87%`, `4` 笔, `75.00%` 胜率, `0.32%` MDD
- 第二轮严格 CVD 版: `+0.84%`, `1` 笔, `100.00%` 胜率, `0.08%` MDD
- 第二轮 CVD 放宽版 (`delta=0.02`): `+0.84%`, `1` 笔, `100.00%` 胜率, `0.08%` MDD
- 第二轮最终主配置: `+0.87%`, `4` 笔, `75.00%` 胜率, `0.32%` MDD

## 相关文件

- 主配置: `D:\AIDCA\AI8\config\trading_config_fund_flow.json`
- 严格 CVD 候选: `D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_opt_d_cvd_relaxed.json`
- 非 CVD 最终候选: `D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_opt_e_no_trial_cvd.json`
- 最终回测摘要: `D:\AIDCA\AI8\output\backtest\bot_like_summary_20260331_183034.json`
- 严格 CVD 回测摘要: `D:\AIDCA\AI8\output\backtest\bot_like_summary_20260331_180956.json`
- 放宽 CVD 回测摘要: `D:\AIDCA\AI8\output\backtest\bot_like_summary_20260331_181642.json`
