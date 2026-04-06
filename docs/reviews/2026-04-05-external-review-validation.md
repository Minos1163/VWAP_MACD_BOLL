# 外部审阅建议验证

- 日期: 2026-04-05
- 基线配置: `config/trading_config_fund_flow_live_production.json`
- 回放窗口: `2026-03-05T03:00:00` -> `2026-04-04T03:00:00`
- 方式: bot-like replay

## 1. 基线

| label | return_pct | win_rate_pct | trades | signals | profit_factor | max_drawdown_pct | expectancy_usd_per_trade |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 0.72% | 76.67% | 150 | 188 | 1.17 | 8.29% | 0.48 |

结论:
- 基线不是“不会赢”，而是 `expectancy` 太低。
- 最大瓶颈不是全局阈值，而是负贡献 cohort 和无效 symbol。

## 2. 单因子验证

| label | candidate | return_pct | win_rate_pct | trades | signals | profit_factor | max_drawdown_pct | expectancy_usd_per_trade | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| trial_hard_off | `config/candidates/trading_config_fund_flow_review_trial_hard_off_20260405.json` | 2.81% | 77.55% | 98 | 117 | 1.37 | 6.24% | 2.87 | valid |
| disable_flip_bullish | `config/candidates/trading_config_fund_flow_review_disable_flip_bullish_20260405.json` | 0.72% | 76.67% | 150 | 188 | 1.17 | 8.29% | 0.48 | no_effect |
| disable_red_bar_long | `config/candidates/trading_config_fund_flow_review_disable_red_bar_long_20260405.json` | 4.53% | 85.56% | 90 | 91 | 1.66 | 4.62% | 5.04 | strongest_single_factor |
| threshold_090 | `config/candidates/trading_config_fund_flow_review_threshold_090_20260405.json` | 0.04% | 76.92% | 117 | 138 | 1.12 | 5.79% | 0.03 | rejected |
| time_exit_15m | `config/candidates/trading_config_fund_flow_review_time_exit_15m_20260405.json` | -7.97% | 64.29% | 140 | 187 | 0.75 | 12.74% | -5.69 | rejected |
| fundflow_blacklist_kas | `config/candidates/trading_config_fund_flow_review_fundflow_blacklist_kas_20260405.json` | 5.30% | 78.57% | 140 | 178 | 1.54 | 5.65% | 3.79 | valid |

## 3. 组合验证

组合项:
- `trial hard off`
- `disable_red_bar_growing_long_entries`
- `fund_flow.symbol_blacklist += KASUSDT`

候选配置:
- `config/candidates/trading_config_fund_flow_review_combo_validated_20260405.json`

结果:

| label | return_pct | win_rate_pct | trades | signals | profit_factor | max_drawdown_pct | expectancy_usd_per_trade |
| --- | --- | --- | --- | --- | --- | --- | --- |
| combo_validated | 6.47% | 89.74% | 39 | 38 | 3.51 | 3.16% | 16.60 |

结构拆分:

| bucket | count | pnl | win_rate |
| --- | --- | --- | --- |
| long | 18 | 99.20 | 77.78% |
| short | 21 | 618.47 | 100.00% |
| flip_bullish | 18 | 99.20 | 77.78% |
| green_bar_growing | 18 | 549.70 | 100.00% |
| red_bar_growing | 3 | 68.77 | 100.00% |
| trial_entry=False | 39 | 717.67 | n/a |

结论:
- 组合后，多头不再是净拖累。
- `trial` 完全消失，`short` 仍是主利润来源。
- 改善是真实的，但交易数从 `150` 掉到 `39`，这是“极端精选”结果，不是能直接外推到 `200%+` 的证据。

## 4. 对外部审阅的逐条裁决

### A. “先砍 long / trial”

成立，但要更精确：
- `disable_red_bar_growing_long_entries` 有显著正贡献。
- `trial hard off` 有显著正贡献。
- 不能笼统说“关 flip_bullish long”就能改善，因为当前 `disable_flip_bullish_entries` 开关在本回放路径上没有产生效果。

### B. “统一把阈值抬到 0.90”

不成立。
- 单独把 `default/red_bar_growing/flip_bullish` 统一抬到 `0.90`，收益几乎归零。
- 问题不是“阈值不够高”，而是“错误 cohort 没被精准切掉”。

### C. “time_exit 从 30m 提到 15m”

不成立。
- 直接改成 `15m` 使收益转负，胜率和 PF 同时恶化。
- 当前 time-exit 问题不是“越早越好”，而是缺少更细的条件化退出逻辑。

### D. “做 kill-switch”

成立，而且发现了一个实现口径问题：
- 当前实盘配置把黑名单写在 `trading.symbol_blacklist`。
- 但 `ConfigLoader.get_trading_symbols()` 实际只读取 `fund_flow.symbol_blacklist`。
- 所以基线回测里 `KASUSDT` 仍然被交易，这不是审阅误判，而是 blacklist 层级未对齐。

## 5. 实施优先级

P0:
- 把 `review_combo_validated_20260405` 作为下一轮候选基线继续细化。
- 修正 blacklist 配置层级，至少保证 bot-like replay 与实盘 universe 一致。

P1:
- 单独定位为什么 `disable_flip_bullish_entries` 对当前 cohort 无效。
- 不再做“统一抬阈值”，改做 cohort-specific gating。

P2:
- 若要继续改善 `time_exit`，必须做条件化版本，而不是简单 `30 -> 15`。
- 在 close-risk 微调前，先把 same-bar / protection priority 的 live-backtest 语义缺口补齐。
