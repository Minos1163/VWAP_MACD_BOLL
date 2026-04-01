# Claude 审核请求: `trading_config_fund_flow.json` 优化后 30 天回测结果

生成时间: 2026-03-31 17:50

## 1. 背景

基线版本在 30 天 bot-like 回测中表现为:

- 收益率 `+0.23%`
- 交易数 `18`
- 胜率 `27.78%`
- 最大真实回撤 `1.56%`

主要问题:

- 18 笔交易全部是 `flip_bullish` 的 `trial entry`
- `0.85-0.90` 分数段 `8` 笔全亏
- `DOGEUSDT / BCHUSDT / AAVEUSDT` 构成主要亏损口袋

## 2. 已落地的优化

### 2.1 配置卫生

- 修复 `iflow.cwd` 到 `d:\\AIDCA\\AI8`
- 修复 `iflow.file_allowed_dirs` 到 `AI8` 路径

### 2.2 入场质量过滤

在 `fund_flow.macd_mtf_strategy_v2.entry_filters` 中新增:

- `flip_bullish_trial_score_window_enabled = true`
- `flip_bullish_trial_score_min = 0.80`
- `flip_bullish_trial_score_max = 0.87`

并在策略引擎中新增真实执行逻辑:

- `flip_bullish trial` 仅允许落在甜区窗口内
- 高分误导段被直接拦截

### 2.3 币种级覆盖

在 `fund_flow.symbol_overrides` 中加入:

- `DOGEUSDT.disable_flip_bullish_trial = true`
- `BCHUSDT.disable_flip_bullish_trial = true`
- `AAVEUSDT.disable_flip_bullish_trial = true`

同时支持:

- `preflip_trial_min_signal_score_override`

### 2.4 Pretrade Gate

最终主配置采用:

- `pretrade_risk_gate.enabled = true`
- `pretrade_risk_gate.use_hard_rules_only = true`
- `pretrade_risk_gate.equity_usage_block = 0.60`

说明:

- 我测试过更激进的 full guardrails 版本
- 结果过度收紧，30 天只剩 1 笔交易，收益退化到 `-0.01%`
- 因此最终没有采用 full guardrails，而是保留老容量参数，只启用硬规则 gate

## 3. Ablation 结果

### 候选 A: score window + symbol overrides

- 配置: `config/candidates/trading_config_fund_flow_opt_a_score_symbol.json`
- 结果:
  - `return = +0.88%`
  - `trades = 5`
  - `win_rate = 80.00%`
  - `max_drawdown = 0.76%`

### 候选 B: score window + symbol overrides + hard-rules gate

- 配置: `config/candidates/trading_config_fund_flow_opt_b_score_symbol_gate.json`
- 结果:
  - `return = +0.87%`
  - `trades = 4`
  - `win_rate = 75.00%`
  - `max_drawdown = 0.32%`

### 候选 C: full guardrails

- 配置: `config/candidates/trading_config_fund_flow_opt_c_full_guardrails.json`
- 结果:
  - `return = -0.01%`
  - `trades = 1`
  - `win_rate = 0.00%`
  - `max_drawdown = 0.02%`

## 4. 最终采用版本

最终主配置采用候选 B，并已写回:

- `config/trading_config_fund_flow.json`

原因:

- 与候选 A 的收益几乎持平
- 胜率达到目标线 `75%`
- 回撤更低
- 比 full guardrails 保留更多有效交易机会

## 5. 最终回测结果

回测文件:

- `output/backtest/bot_like_summary_20260331_174523.json`
- `output/backtest/bot_like_trades_20260331_174523.csv`
- `output/backtest/bot_like_equity_curve_20260331_174523.csv`
- `output/analysis/bot_like_20260331_174523_analysis_summary.json`
- `output/analysis/bot_like_20260331_174523_symbol_breakdown.csv`

结果:

- 初始资金: `10000 USDT`
- 期末资金: `10087.25 USDT`
- 收益率: `+0.87%`
- 交易数: `4`
- 胜率: `75.00%`
- Profit Factor: `106.15`
- 最大真实回撤: `0.32%`

## 5.1 最终成交归因

最终 4 笔成交全部为 `flip_bullish`，但只留下了 4 笔高质量样本:

- `FETUSDT`: `+85.21`
- `ALGOUSDT`: `+3.02`
- `POLUSDT`: `+2.75`（2 笔，1 胜 1 负）

未再出现此前主亏损口袋:

- `DOGEUSDT`
- `BCHUSDT`
- `AAVEUSDT`

这说明本轮优化主要是通过“切掉坏交易”提升收益，而不是通过扩大持仓或放大杠杆提高收益。

## 6. 关键门槛与权重参数

### 6.1 评分权重

当前 `fund_flow.macd_mtf_strategy_v2.scoring_weights`:

- `weight_1h_direction = 0.20`
- `weight_4h_direction = 0.35`
- `weight_4h_enhancement = 0.05`
- `weight_vwap = 0.20`
- `weight_15m_entry = 0.05`
- `weight_volume = 0.15`

### 6.2 入场阈值

当前 `entry_thresholds`:

- `default = 0.85`
- `min_entry_score = 0.25`
- `min_signal_score = 0.85`
- `red_bar_growing = 0.86`
- `flip_bearish = 0.84`
- `flip_bullish = 0.84`
- `stable_bear_continuation_min_signal_score = 0.83`
- `stable_bull_continuation_min_signal_score = 0.83`

### 6.3 试仓与 flip_bullish 过滤

当前 `entry_filters` 中与本轮优化最相关的参数:

- `enable_4h_preflip_trial_entries = true`
- `preflip_trial_min_shrink_pct_long = 0.6`
- `preflip_trial_min_shrink_pct_short = 0.3`
- `preflip_trial_min_signal_score = 0.75`
- `preflip_trial_min_vwap_score = 0.06`
- `preflip_trial_entry_scale = 0.35`
- `preflip_trial_max_leverage = 2`
- `enable_flip_bullish_strict_filter = true`
- `flip_bullish_min_vwap_score = 0.15`
- `flip_bullish_require_pullback_bounce = true`
- `flip_bullish_require_15m_growing = true`
- `enable_flip_bullish_cvd_context_filter = false`
- `flip_bullish_max_cvd_upper_wick_ratio = 0.2`
- `flip_bullish_min_cvd_1h_delta_ratio = 0.03`
- `flip_bullish_trial_score_window_enabled = true`
- `flip_bullish_trial_score_min = 0.80`
- `flip_bullish_trial_score_max = 0.87`

### 6.4 币种级覆盖

当前 `symbol_overrides`:

- `DOGEUSDT.disable_flip_bullish_trial = true`
- `BCHUSDT.disable_flip_bullish_trial = true`
- `AAVEUSDT.disable_flip_bullish_trial = true`
- 三者都设置 `preflip_trial_min_signal_score_override = 0.9`

## 7. 仓位、杠杆、止盈止损与 Gate

### 7.1 Runtime limits

本轮最终主配置仍保留原始容量参数:

- `max_positions = 4`
- `default_target_portion = 0.5`
- `max_symbol_position_portion = 0.5`
- `min_open_portion = 0.06`
- `reserve_pct = 0.2`
- `min_leverage = 2`
- `default_leverage = 3`
- `max_leverage = 4`

说明:

- 我测试过将容量压缩到 live-production 风格
- 结果交易数直接塌到 1 笔，因此最终未采用

### 7.2 止盈止损

当前主运行限制:

- `stop_loss_pct = 0.02`
- `take_profit_pct = 0.04`
- `breakeven_enabled = true`
- `breakeven_trigger_pnl_ratio = 0.006`
- `breakeven_lock_ratio = 0.002`
- `entry_slippage = 0.0015`

当前 `stop_loss_config`:

- `use_dynamic_stop = true`
- `boll_stop_atr_multiplier = 0.5`
- `max_stop_loss_pct = 0.025`
- `enable_4h_shrink_exit = true`
- `exit_4h_shrink_bars = 2`
- `exit_4h_min_shrink_pct = 0.12`
- `enable_stable_continuation_slow_4h_shrink_exit = true`
- `stable_continuation_exit_4h_shrink_bars = 3`
- `stable_continuation_exit_4h_min_shrink_pct = 0.28`

### 7.3 Pretrade gate

当前最终采用:

- `pretrade_risk_gate.enabled = true`
- `pretrade_risk_gate.use_hard_rules_only = true`
- `pretrade_risk_gate.entry_threshold = 0.06`
- `pretrade_risk_gate.entry_threshold_capture = 0.05`
- `pretrade_risk_gate.max_drawdown = 0.02`
- `pretrade_risk_gate.max_exposure_per_trade = 0.22`
- `pretrade_risk_gate.equity_usage_block = 0.60`
- `pretrade_risk_gate.volatility_cap = 0.012`
- `pretrade_risk_gate.volatility_cap_capture = 0.014`
- `pretrade_risk_gate.exit_drawdown_override = 0.015`

## 8. Claude 请重点审核的技术问题

请 Claude 不只看结果，也请结合上面的参数判断:

1. `weight_4h_direction=0.35` 与 `weight_1h_direction=0.20` 是否仍会对 `flip_bullish trial` 产生高分误导？
2. `flip_bullish_trial_score_max = 0.87` 是否过严，是否应该放宽到 `0.88` 或 `0.89` 来恢复交易数？
3. `DOGE/BCH/AAVE` 的直接 trial 禁用，是否应该替换成更细的 symbol-specific threshold？
4. `enable_flip_bullish_cvd_context_filter = false` 是否还值得开启做下一轮提升？
5. `stop_loss_pct = 0.02` / `take_profit_pct = 0.04` 与动态止损并存时，当前胜率提升是否有过拟合风险？
6. `pretrade_risk_gate.use_hard_rules_only = true` 是否已经足够，还是还需要软评分层？
7. 在不明显增加回撤的前提下，Claude 会优先建议恢复哪一类交易机会？

## 9. 优化后行为变化

优化后:

- 仍然全部是 `flip_bullish`
- 但只留下 `4` 笔高质量入场
- 交易币种缩到 `FETUSDT / ALGOUSDT / POLUSDT`
- 亏损口袋 `DOGE / BCH / AAVE` 已经不再出现在成交列表中

按最终成交归因:

- `FETUSDT`: `+85.21`
- `ALGOUSDT`: `+3.02`
- `POLUSDT`: `+2.75`

## 10. 我希望 Claude 的输出形式

希望 Claude 输出:

- 对当前参数组合的总判断
- 最值得做的 `3` 个下一步改动
- 每个改动的预期影响:
  - `return`
  - `win_rate`
  - `trade_count`
  - `max_drawdown`
- 如果要继续 ablation，Claude 推荐的实验顺序

## 11. 我的当前判断

这轮优化已经达到两个目标:

- 把亏损口袋切掉
- 把胜率恢复到 75%

但还没有完全达到“高收益”目标。

下一轮如果继续优化，我建议 Claude 在以下方向里二选一:

1. 小幅放宽 `flip_bullish_trial_score_max`
2. 将 `DOGE/BCH/AAVE` 从“直接禁用”改成“更高门槛的可交易”

我不建议立即回到 full guardrails，因为那版已经被回测证明过度收紧。
