# 策略优化 TODO 2026-03-27

## 当前判断

这次不再沿用 [strategy_optimization_plan_20260327.md](/D:/AIDCA/AI8/docs/strategy_optimization_plan_20260327.md) 里的“高嫌疑项优先级”作为唯一依据，而是先对真实配置做差分。

已确认：

- `+132.40% / 73.66%` 的高收益基线来自 [trading_config_fund_flow.json](/D:/AIDCA/AI8/config/trading_config_fund_flow.json)
- `+63.10% / 70.35%` 的当前收敛基线来自 [trading_config_fund_flow_live_backtest_converged_20260327.json](/D:/AIDCA/AI8/config/trading_config_fund_flow_live_backtest_converged_20260327.json)
- 两者之间，`max_active_symbols / default_leverage / max_leverage / long_open_threshold / short_open_threshold / close_threshold / stop_loss_pct / take_profit_pct` 并不是当前最直接的配置差分

当前更值得优先验证的真实差分：

- [x] `trading.symbol_blacklist` 只出现在 current config，当前直接过滤了 17 个 symbol
- [x] `entry_filters.disable_flip_bullish_entries = true`
- [x] `entry_filters.disable_green_bar_growing_entries = true`
- [x] `entry_filters.disable_red_bar_growing_long_entries = true`
- [x] `entry_filters.min_vwap_score_for_entry = 0.10`，而 baseline 是 `0.14`
- [ ] `preflip_trial_min_shrink_pct_long / preflip_trial_min_signal_score / stop_loss_config.exit_4h_require_profit / session_risk_control` 与 baseline 也存在偏离

## Phase A: 根因消融

目标：先确认 `132% -> 63%` 的主要收益衰减到底来自哪里，再决定怎么优化。

- [x] 跑 `current_live_converged`
- [x] 跑 `restore_blacklist`
- [x] 跑 `restore_entry_blocks`
- [x] 跑 `restore_blacklist_and_entry_blocks`
- [ ] 跑 `restore_primary_signal_path`
- [x] 输出第一轮收益贡献排序表，优先看 `return_pct / total_trades / max_drawdown_pct / available_symbols`

执行工具：

- [x] 使用 [run_strategy_ablation_20260327.py](/D:/AIDCA/AI8/scripts/run_strategy_ablation_20260327.py) 固化实验流程

第一轮实测结果：

| Variant | 窗口 | Return | Win Rate | Trades | Max DD | 结论 |
|---|---|---:|---:|---:|---:|---|
| `current_live_converged` | `2026-02-25 18:59:01 -> 2026-03-27 18:59:01` | `63.10%` | `70.35%` | `607` | `14.52%` | 当前基线 |
| `restore_blacklist` | 同上 | `63.10%` | `70.35%` | `607` | `14.52%` | 无变化 |
| `restore_entry_blocks` | 同上 | `63.10%` | `70.35%` | `607` | `14.52%` | 无变化 |
| `restore_blacklist_and_entry_blocks` | 同上 | `63.10%` | `70.35%` | `607` | `14.52%` | 仍无变化 |
| `baseline_132_reference` | 同上 | `124.18%` | `73.23%` | `538` | `7.05%` | 同窗参考基线 |

阶段性结论：

- [x] `symbol_blacklist` 不是当前这条回测链路里的实际收益衰减源
- [x] 那三个 `disable_* entries` 开关也不是当前这条回测链路里的实际收益衰减源
- [ ] 下一步必须转向“策略引擎真实消费的信号路径差分”，不能继续把精力放在这两个假主因上

## Phase B: 优化候选

只有在 Phase A 明确主因后，才进入优化组合。

- [x] `restore_blacklist` 未抬升收益，降级处理
- [x] `restore_entry_blocks` 未抬升收益，降级处理
- [ ] 继续补跑 `restore_primary_signal_path`，重点验证 `min_vwap_score_for_entry / preflip_trial / 4H shrink exit / session risk`
- [ ] 必要时直接做 `build_strategy_config()` 输出级 diff，而不是继续看原始 JSON 表层差异

### 新的最高优先级：策略引擎真正消费的信号路径差分

后续不再优先看原始 JSON 哪些字段“看起来不一样”，而是直接看回测器真正喂给策略引擎的有效配置差分。

- [ ] 对 [backtest_macd_v2.py](/D:/AIDCA/AI8/scripts/backtest_macd_v2.py) 的 `build_strategy_config()` 产物做 baseline vs current diff
- [ ] 对 `MACDStrategyV2Config` 的有效字段做逐项表格，明确哪些字段真的进入了策略判定
- [ ] 优先检查 `entry_filters / entry_thresholds / scoring_weights / stop_loss_config / session_risk_control` 这 5 组
- [ ] 把“存在于 JSON 但未被回测器消费”的字段单独标记，避免继续浪费时间做假消融
- [ ] 只对“已确认被策略引擎消费且存在差分”的字段继续做单变量回测

## Phase C: 计划文档里的候选项

这些项仍然值得测，但优先级下调，因为它们不是当前配置差分里的第一主因。

- [x] `max_active_symbols: 3 -> 5`
- [x] `take_profit_pct: 0.02 -> 0.03`
- [x] `stop_loss_pct: 0.02 -> 0.018`
- [ ] 仅在消融结果支持时，再考虑调整 `long_open_threshold / short_open_threshold`

第二轮实测结果：

| Variant | 窗口 | Return | Win Rate | Trades | Max DD | 结论 |
|---|---|---:|---:|---:|---:|---|
| `opt_capacity_5` | `2026-02-25 18:59:01 -> 2026-03-27 18:59:01` | `49.78%` | `71.67%` | `953` | `24.38%` | 明显更差，且回撤失控 |
| `opt_asymmetric_tp_sl` | 同上 | `51.82%` | `70.40%` | `544` | `21.04%` | 明显更差 |

第三轮联合回测结果：

| Variant | 窗口 | Return | Win Rate | Trades | Max DD | 结论 |
|---|---|---:|---:|---:|---:|---|
| `baseline132_hybrid_tp4` | `2026-02-25 18:59:01 -> 2026-03-27 18:59:01` | `128.45%` | `73.38%` | `432` | `9.75%` | 明显优于 current live-copy，且接近 baseline 档位 |

联合配置说明：

- [x] 以当前 live-converged 回测配置为底
- [x] 覆盖 baseline_132 在回测器里真正消费的策略层有效参数
- [x] 明确改为 `take_profit_pct = 0.04`
- [x] 保持 `stop_loss_pct = 0.02`
- [x] 候选配置文件: [trading_config_fund_flow_live_backtest_baseline132_hybrid_tp4_20260327.json](/D:/AIDCA/AI8/config/trading_config_fund_flow_live_backtest_baseline132_hybrid_tp4_20260327.json)

阶段性结论：

- [x] `max_active_symbols=5` 不是当前最优方向，至少在这 30 天窗口里明显劣化
- [x] `0.03 / 0.018` 的非对称 TP/SL 不是当前最优方向，至少在这 30 天窗口里明显劣化
- [x] “baseline_132 有效策略参数 + 当前 live 框架 + TP4/SL2” 可以显著恢复收益曲线
- [ ] 后续优化重心仍应放在 baseline 与 current 在 `MACDStrategyV2Config` 层的真实行为差异，但当前已经找到一条可行候选线

## 验收标准

- [ ] 新候选组合策略级回测收益重新回到 `80%+`
- [ ] 最大回撤控制在 `15%` 内
- [ ] 交易数不要靠“极端放大频率”虚高，而要确认 `profit_factor` 仍显著高于 current baseline
- [ ] 最终只把通过回测验证的改动推进到 live 候选，不直接覆盖 production

## 结论

本轮优化的核心不是“继续盲调 TP/SL”，而是：

1. `blacklist + blocked entry families` 已验证不是主衰减源。
2. `max_active_symbols=5` 与 `非对称 TP/SL` 已验证不是修复方向。
3. 下一步应直接审计 baseline 与 current 在 `build_strategy_config()` 之后的有效配置差异。
