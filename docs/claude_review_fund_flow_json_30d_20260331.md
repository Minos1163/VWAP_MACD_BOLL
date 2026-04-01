# Claude 审核请求: `config/trading_config_fund_flow.json` 30 天回测与亏损归因

生成时间: 2026-03-31 16:55

本文件用于请 Claude 审核当前配置，并帮助寻找提升收益的方法。

## 1. 本次审查对象

- 回测配置: `config/trading_config_fund_flow.json`
- 配置快照: `config/trading_config_fund_flow_snapshot_20260331_163651.json`
- 回测窗口: `2026-03-01 00:00:00` 到 `2026-03-31 23:59:59`
- 回测方式: `scripts/backtest_fund_flow_bot_like.py`
- 回测输出:
  - `output/backtest/bot_like_summary_20260331_164319.json`
  - `output/backtest/bot_like_trades_20260331_164319.csv`
  - `output/backtest/bot_like_equity_curve_20260331_164319.csv`
- 辅助分析输出:
  - `output/analysis/bot_like_20260331_164319_analysis_summary.json`
  - `output/analysis/bot_like_20260331_164319_symbol_breakdown.csv`
  - `output/analysis/bot_like_20260331_164319_drawdown_breakdown.csv`
  - `output/analysis/bot_like_20260331_164319_true_drawdown_breakdown.csv`

## 2. 回测结果摘要

- 初始资金: `10000 USDT`
- 期末资金: `10023.35 USDT`
- 总收益率: `+0.2335%`
- 总交易数: `18`
- 胜率: `27.78%` (`5` 胜 `13` 负)
- Profit Factor: `1.73`
- 最大真实回撤: `-1.56%`
- 最大真实回撤区间: `2026-03-15 21:15:00` 到 `2026-03-23 11:45:00`

结论:
- 这套配置没有明显爆仓风险，回撤也不高。
- 但收益基本打平，胜率远低于目标，说明核心问题是“信号质量和开仓结构”而不是“仓位过大导致直接失控”。

## 3. 亏损归因

### 3.1 最直接的归因结论

- 18 笔交易全部是 `flip_bullish`
- 18 笔交易全部是 `trial entry`
- 17 笔以 `stop_loss_intrabar` 退出
- 只有 1 笔以 `take_profit_intrabar` 退出
- 13 笔亏损全部来自 `stop_loss_intrabar`

这说明:
- 亏损的主因不是止盈逻辑没兑现，而是开仓后很快被打止损。
- 当前样本中，策略几乎退化成“做多预翻转试仓策略”，没有形成多样化信号来源。

### 3.2 币种亏损集中度

亏损按币种分布:

- `DOGEUSDT`: `4` 笔，`-24.90`
- `BCHUSDT`: `3` 笔，`-12.33`
- `AAVEUSDT`: `2` 笔，`-9.26`（亏损笔）
- `XLMUSDT`: `1` 笔，`-3.12`
- `SUIUSDT`: `1` 笔，`-2.58`
- `ALGOUSDT`: `1` 笔，`-1.41`
- `POLUSDT`: `1` 笔，`-0.74`

关键观察:
- `DOGE + BCH + AAVE` 三个币种合计亏损约 `-46.49`
- 总亏损约 `-54.34`
- 即约 `85%+` 的总亏损来自这三个币

这说明:
- 当前收益问题高度集中，不是全市场普遍失效。
- 更像是“少数币种 + 特定信号类型”形成了亏损口袋。

### 3.3 信号分段归因

按 `signal_score` 分桶:

- `>=0.90`: `4` 笔，`1` 胜 `3` 负，PnL `-11.55`
- `0.85-0.90`: `8` 笔，`0` 胜 `8` 负，PnL `-34.11`
- `0.80-0.85`: `5` 笔，`3` 胜 `2` 负，PnL `+81.71`
- `<0.80`: `1` 笔，`1` 胜，PnL `+3.58`

关键异常:
- `0.85-0.90` 这段本应属于较高质量分数，但样本里 `8` 笔全亏。
- 分数越高并没有带来更高胜率，至少在 `flip_bullish` 试仓上没有。

这说明:
- 当前 `signal_score` 对 `flip_bullish` 试仓的排序能力可能失真。
- 也可能是高分来自某些会“误加分”的因子，但这些因子并不能提升真实收益。

### 3.4 盈亏结构归因

- 平均亏损: `-4.18`
- 平均盈利: `+18.79`
- Profit Factor 仍有 `1.73`

这说明:
- 不是“亏损单太大”，而是“盈利单太少”。
- 当前问题更接近入场筛选不足，而不是单笔风控彻底失效。

## 4. 当前开仓逻辑

### 4.1 核心门槛

- `long_open_threshold = 0.09`
- `short_open_threshold = 0.07`
- `close_threshold = 0.3`
- `entry_slippage = 0.0015`
- `stop_loss_pct = 0.02`
- `take_profit_pct = 0.04`
- `reverse_close_confirm_bars = 2`
- `breakeven_enabled = true`
- `breakeven_trigger_pnl_ratio = 0.006`
- `breakeven_lock_ratio = 0.002`

### 4.2 资金与容量

- `default_target_portion = 0.5`
- `add_position_portion = 0.5`
- `max_symbol_position_portion = 0.5`
- `max_active_symbols = 4`
- `min_open_portion = 0.06`
- 杠杆范围: `2x ~ 4x`

### 4.3 当前样本下的实际交易特征

本次 30 天回测里:

- 所有成交都是 `long`
- 所有成交信号都是 `flip_bullish`
- 所有成交都是 `trial entry`
- 实际成交杠杆全部是 `2x`
- 没有出现短空成交
- 没有出现稳定延续类成交

这说明:
- 配置虽然允许较大仓位、较多持仓、较高杠杆，但当前样本实际成交集中在“4H preflip 多头试仓”。
- 当前收益低，不是因为策略分散太多，而是因为命中的成交形态过于单一。

## 5. 关键门槛与过滤条件

### 5.1 Pre-flip 试仓

- `enable_4h_preflip_trial_entries = true`
- `preflip_trial_min_shrink_pct_long = 0.6`
- `preflip_trial_min_signal_score = 0.75`
- `preflip_trial_min_vwap_score = 0.06`
- `preflip_trial_entry_scale = 0.35`
- `preflip_trial_max_leverage = 2`

### 5.2 Flip Bullish 过滤

- `enable_flip_bullish_strict_filter = true`
- `disable_flip_bullish_entries = false`
- `flip_bullish_min_vwap_score = 0.15`
- `flip_bullish_require_pullback_bounce = true`
- `flip_bullish_require_15m_growing = true`
- `enable_flip_bullish_cvd_context_filter = false`
- `flip_bullish_max_cvd_upper_wick_ratio = 0.2`
- `flip_bullish_min_cvd_1h_delta_ratio = 0.03`
- `min_signal_score = 0.85`
- `min_vwap_score_for_entry = 0.15`

解释:
- 名义上看，这套过滤已经不算很松。
- 但实际结果表明，它依然没能阻断亏损口袋，尤其没有挡住 `DOGE/BCH/AAVE` 上的多头试仓止损链。

## 6. 权限与运行时问题

### 6.1 `iflow` 权限配置

配置中存在以下设置:

- `file_access = true`
- `file_read_only = false`
- `file_allowed_dirs = ["d:\\AIDCA\\AI2", "d:\\AIDCA\\AI2\\config", "d:\\AIDCA\\AI2\\src"]`
- `cwd = "d:\\AIDCA\\AI2"`

这有一个明显问题:

- 当前工作目录是 `D:\AIDCA\AI8`
- 但 `iflow.file_allowed_dirs` 和 `cwd` 仍指向 `AI2`

可能影响:

- 如果实盘链路真的依赖这段权限配置，那么当前仓库路径与允许路径不一致，可能导致运行时文件访问异常或行为与预期不一致。
- 即使回测不依赖，也说明配置里残留了旧环境信息，存在运维与可复现风险。

建议:

- 审核是否应改为 `AI8` 路径
- 将策略参数与环境权限配置拆分，避免旧环境字段污染交易配置

## 7. 当前风控结构

### 7.1 账户级风控

- `account_circuit_enabled = true`
- `max_daily_loss_percent = 5`
- `max_consecutive_losses = 2`
- `daily_loss_cooldown_seconds = 28800`
- `consecutive_loss_cooldown_seconds = 2700`

### 7.2 持仓与退出

- `stop_loss_default_percent = 0.02`
- `take_profit_default_percent = 0.02`
- `stop_loss_pct = 0.02`
- `take_profit_pct = 0.04`
- `protection_sla_enabled = true`
- `protection_sla_seconds = 90`
- `protection_sla_force_flatten = true`

### 7.3 Pretrade 风控

当前配置里:

- `pretrade_risk_gate.enabled = false`

但同时保留了很多参数:

- `entry_threshold = 0.06`
- `max_drawdown = 0.02`
- `max_exposure_per_trade = 0.22`
- `volatility_cap = 0.012`
- `exit_drawdown_override = 0.015`

解释:

- 风控参数写了很多，但因为 `enabled = false`，实质上没有在开仓前形成硬门控。
- 在当前“试仓几乎全是 flip_bullish”的环境里，这相当于缺少了最后一道过滤器。

## 8. 我对亏损原因的判断

按优先级排序:

1. `flip_bullish` 试仓过多，且全部成交都集中在这一路径，策略多样性不足。
2. `preflip_trial` 门槛偏松，`signal_score` 与真实胜率脱钩，尤其 `0.85-0.90` 分段出现系统性亏损。
3. `DOGE/BCH/AAVE` 形成明显亏损口袋，但未被黑名单、分层风控或单币种阈值拦截。
4. `pretrade_risk_gate` 虽然配置丰富，但当前是关闭状态，导致开仓前没有额外硬限制。
5. 配置中仍残留 `AI2` 权限路径，说明配置治理不干净，可能影响真实运行一致性。

## 9. 提高收益的优先建议

### 方案 A: 先砍亏损口袋

优先级最高。

- 对 `DOGEUSDT`、`BCHUSDT`、`AAVEUSDT` 增加黑名单或 watchlist 风险层
- 或者仅对这几个币提高 `flip_bullish` 开仓门槛
- 或者仅允许它们在更高 `ADX_4H` / 更高 `VWAP score` / 更强 `CVD` 确认时开仓

原因:

- 这三个币贡献了绝大多数亏损
- 先切掉亏损口袋，通常比全局调参更快见效

### 方案 B: 收紧 pre-flip trial

- 提高 `preflip_trial_min_signal_score`: 例如从 `0.75` 提到 `0.78` 或 `0.80`
- 提高 `preflip_trial_min_shrink_pct_long`: 例如从 `0.6` 提到 `0.7` 或 `0.75`
- 提高 `preflip_trial_min_vwap_score`: 例如从 `0.06` 提到 `0.10`
- 将 `preflip_trial_entry_scale` 从 `0.35` 再下调，直到胜率恢复

原因:

- 当前样本全部成交都来自这一条通道
- 只要试仓质量提升，整体结果会显著改善

### 方案 C: 打开并收紧 `flip_bullish` 的上下文过滤

- 考虑启用 `enable_flip_bullish_cvd_context_filter`
- 审核 `flip_bullish_max_cvd_upper_wick_ratio = 0.2` 是否还需要更严
- 审核 `flip_bullish_min_cvd_1h_delta_ratio = 0.03` 是否需要提高

原因:

- 配置里已经存在这类针对亏损口袋的思路
- 当前却仍是关闭状态，说明已有经验没有真正落地到主配置

### 方案 D: 重新校准 score，而不是盲信 score

- 单独统计 `flip_bullish` 的分数分布与胜率映射
- 检查哪些子因子在高分亏损单里反而被错误加分
- 必要时下调这些因子的权重，或对 `flip_bullish` 建独立评分逻辑

原因:

- 现在 `0.85-0.90` 段 8 笔全亏，说明分数并未有效排序

### 方案 E: 启用 `pretrade_risk_gate` 的硬拦截版本

- 至少对试仓单启用波动率、暴露度、回撤、趋势健康度拦截
- 即使只拦 20% 的低质量试仓，也可能显著改善胜率

原因:

- 当前 gate 参数很多，但实际未启用
- 这通常意味着系统“看起来有风控，实际没执行”

## 10. 希望 Claude 重点审核的问题

请 Claude 重点帮我判断下面这些问题:

1. 当前 30 天结果里，`flip_bullish trial` 是否已经成为主要失血点，应该直接收紧甚至阶段性关闭吗？
2. `0.85-0.90` 分段 8 笔全亏，是否说明当前 `signal_score` 对该信号类型失真？
3. `DOGE/BCH/AAVE` 是否应做单币种黑名单，还是更适合做 symbol-specific threshold？
4. `pretrade_risk_gate.enabled = false` 是否是当前配置的关键缺口？
5. 当前 `max_active_symbols = 4`、`target_portion = 0.5`、`max_symbol_position_portion = 0.5` 是否过于激进，是否应该向更保守的 live-production 风格靠拢？
6. `iflow` 仍指向 `AI2` 路径，这是否意味着当前配置存在环境污染或运行风险？
7. 如果目标是“提高收益而不是只压回撤”，Claude 会优先建议哪 3 个改动？

## 11. 我的临时结论

如果只做最小改动，我会优先试这三件事:

1. 暂时收紧或限制 `flip_bullish` 试仓
2. 先处理 `DOGE/BCH/AAVE` 亏损口袋
3. 启用 `flip_bullish` 的 CVD 上下文过滤或 `pretrade_risk_gate`

原因很简单:

- 当前不是“全局完全无效”
- 而是“少数币种 + 单一试仓逻辑”在持续拖累收益

如果 Claude 认同这一判断，下一步建议直接做小范围 ablation:

- `禁用 flip_bullish 试仓`
- `仅保留 flip_bullish，但拉高 preflip_trial 门槛`
- `只黑名单 DOGE/BCH/AAVE`
- `开启 flip_bullish CVD filter`
- `开启 pretrade_risk_gate`

然后比较:

- 收益率
- 胜率
- trade count
- profit factor
- max drawdown

