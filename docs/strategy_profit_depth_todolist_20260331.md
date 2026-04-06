# MACD V2 Profit Depth Todo

日期: 2026-03-31  
当前基线: `config/trading_config_fund_flow_round3_scale_target.json`  
基线结果: `264` 笔 / 胜率 `77.27%` / 收益 `+22.19%` / PF `2.24` / MDD `3.00%`

## 本轮目标

- 保持开仓量在 `250 ± 30` 区间
- 胜率保持 `>= 75%`
- 提升 `avg_win`
- 降低 `avg_loss`
- 将月收益从 `+22.19%` 往 `+26% ~ +30%` 推进

## Todo

- [x] 完成基线收益深度归因，确认主亏损口袋是 `red_bar_growing + long_dual_support`
- [x] 确认回测侧已有多档 `take_profit_levels` 能力
- [x] 确认回测侧缺少 `vwap_structure_overrides` 风控覆盖能力
- [x] 为回测引擎补 `vwap_structure_overrides`:
  - [x] `position_scale_override`
  - [x] `stop_loss_pct_override`
  - [x] `breakeven_trigger_pnl_ratio_override`
  - [x] `breakeven_lock_ratio_override`
  - [x] `take_profit_pct_levels_override`
  - [x] `take_profit_reduce_pct_levels_override`
- [x] 先做实验 #1: 三档主动 partial TP
- [x] 再做实验 #2: `long_dual_support` 更早保本
- [x] 再做实验 #3: `long_dual_support` 更紧止损 + 仓位缩比
- [x] 创建“不开 partial TP 的可比候选”:
  - [x] `trading_config_fund_flow_profit_depth_e2b_be_only.json`
  - [x] `trading_config_fund_flow_profit_depth_e3b_be_sl_only.json`
- [ ] 跑完 E2b/E3b 的 30 天完整对比
  - 当前阻塞: 本地 full-window 回测在这两份配置上持续 CPU-bound，暂未产出可信 summary
- [ ] 视结果决定是否继续做 symbol 级亏损治理
- [x] 将实验对比和最终归因写入新的 Claude 审核文档
- [x] 平仓风控 Round 2:
  - [x] 为回测引擎补 `take_profit_pct_override`
  - [x] 为回测引擎补分通道 `trailing_stop_profiles`
  - [x] 为布林带计算补尾部窗口优化，解决 full-window 回测性能阻塞
  - [x] 跑 E2b / E3b / E4（全通道版本）
  - [x] 跑 E2c / E3c（仅 long_dual_support 版本）
  - [x] 结论: 本轮没有产生优于基线的平仓风控配置，不推广到实盘
- [x] 将优化主线切换为 `entry alpha 提纯 + symbol × pocket 治理`
- [x] 新增 `symbol × pocket` 归因脚本基础设施
- [x] 为策略引擎补 `pocket_entry_overrides`
  - [x] 支持 `signal_type × vwap_state` 精确匹配
  - [x] 支持 `*|vwap_state` 状态级 fallback
  - [x] 支持 pocket 级 `min_signal_score`
  - [x] 支持 pocket 级 `min_vwap_score`
  - [x] 支持 pocket 级 `allow_neutral_1h_confirmation`
  - [x] 支持 pocket 级 `require_strict_1h_confirmation`
  - [x] 支持 pocket 级 `disallow_trial_entry`
- [x] 为回测配置读取补 `entry_filters.pocket_entry_overrides`
- [ ] 用 `symbol × pocket` 归因结果挑出 `long_dual_support` 的坏 symbol，做第一轮 symbol-level 禁用候选
- [ ] 只对 `long_dual_support` 做 entry-side 提纯候选
- [x] 用 `symbol × pocket` 归因结果挑出 `long_dual_support` 的坏 symbol，做第一轮 symbol-level 禁用候选
- [x] 只对 `long_dual_support` 做 entry-side 提纯候选
  - 更高 `min_vwap_score`
  - 更高 `min_signal_score`
  - 禁止 `1h neutral`
  - 必要时禁用 pocket trial
- [ ] 单独核对 live/backtest 的 trailing 执行顺序与 intrabar 命中逻辑
- [x] 将 `scripts/diagnostics/validate_live_backtest_alignment.py` 升级为“配置一致性 + 执行语义审计”脚本
- [x] 为对齐审计补单测，覆盖 trailing / intrabar / 优先级矩阵
- [x] 产出 live/backtest 平仓一致性 Markdown 审计报告
- [x] 为 live 增加 `trailing_activated / breakeven_activated / protection_priority_applied` 显式事件日志
- [x] 重新验证 `4h_shrink_exit` 的 live 等价路径
- [x] 为 same-bar `stop vs TP` 增加保护单快照与成交后证据日志
- [x] 补 same-bar `stop vs TP` 证据分析脚本，支持从 live 审计日志汇总样本
- [ ] 继续验证 same-bar `stop vs TP` 优先级是否能在 live 侧形成可证明契约

## 当前判断

- 本轮最该优先优化的是“盈利厚度”，不是频率
- `take_profit_intrabar` 只有 `25` 笔，却贡献了主要利润
- 大量盈利单在 `stop_loss_intrabar` 下只留下 `+5.34` 左右的小盈利
- 因此:
  - 更早保本
  - 更早分级止盈
  - 针对 `long_dual_support` 更紧风险控制
  是最符合当前数据结构的三件事

## 当前实验结论

- `partial TP` 方向本身有效:
  - 胜率从 `77.27%` 升到 `85.79% ~ 88.66%`
  - MDD 从 `3.00%` 降到 `2.04% ~ 2.24%`
- 但当前回测口径会把部分平仓拆成多笔成交:
  - `264` 笔被放大到 `529 / 570` 笔
  - `avg_win` 从 `22.35` 变成 `8.26 ~ 8.80`
  - 总收益反而降到 `17.30% ~ 17.54%`
- `long_dual_support` 专项修复被证明方向正确:
  - 基线该通道 `-67.25`
  - E2 提升到 `+36.09`
  - E3 仍为 `+28.93`
- 因此本轮最重要的结论不是“直接上线 partial TP”，而是:
  - `long_dual_support` 的 breakeven / stop / scale 修复值得继续验证
  - 但必须先拿到不受 partial-close 记账影响的可比回测

## 平仓风控 Round 2 结果

基线:
- `264` 笔 / `77.27%` / `+22.19%` / `avg_win 22.35` / `avg_loss -33.90` / `MDD 3.00%`

全通道方案:
- E2b [v2_summary_20260331_231440.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_231440.json)
  - `438` 笔 / `70.55%` / `+7.29%` / `avg_win 11.24` / `avg_loss -17.91` / `MDD 3.53%`
- E3b [v2_summary_20260331_231654.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_231654.json)
  - `438` 笔 / `70.55%` / `+7.58%` / `avg_win 11.33` / `avg_loss -17.88` / `MDD 3.53%`
- E4 [v2_summary_20260331_231655.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_231655.json)
  - `414` 笔 / `69.81%` / `+7.42%` / `avg_win 12.08` / `avg_loss -18.78` / `MDD 3.52%`

仅修 long_dual_support:
- E2c [v2_summary_20260331_231943.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_231943.json)
  - `405` 笔 / `71.85%` / `+6.27%` / `avg_win 12.13` / `avg_loss -21.82` / `MDD 2.39%`
- E3c [v2_summary_20260331_231947.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260331_231947.json)
  - `405` 笔 / `71.85%` / `+6.43%` / `avg_win 12.19` / `avg_loss -21.83` / `MDD 2.39%`

本轮结论:
- 平仓风控收紧确实压低了 `avg_loss` 和部分回撤
- 但同时显著压薄了 `avg_win`，并把交易数从 `264` 推高到 `405~438`
- 最终所有候选都明显落后于基线收益 `+22.19%`
- `long_dual_support` 在“只修它自己”的版本里反而进一步恶化到 `-399.59 / -383.64`
- 因此这轮没有可推广配置，后续应优先回到:
  - symbol 级亏损治理
  - 开仓侧提纯
  - 而不是继续全局改退出参数

## 新主线

- baseline 先不动，不再继续全局 close-risk 收紧
- 把 `long_dual_support` 明确当作 entry alpha 问题处理
- 以后优先做:
  - `symbol × pocket` 归因
  - pocket 级别 entry gate 提纯
  - 局部 partial TP / runner protection
  - re-entry cooldown
- 暂不再做:
  - 全局提前 breakeven
  - 全局提前 trailing
  - 全局砍 `4% TP`

## 2026-04-01 对照回测

- 新增候选:
  - `config/candidates/trading_config_fund_flow_entry_alpha_s1_symbol_disable.json`
  - `config/candidates/trading_config_fund_flow_entry_alpha_s2_pocket_override.json`
  - `config/candidates/trading_config_fund_flow_entry_alpha_s3_combo.json`
- `symbol-level disable` 第一版禁用清单:
  - `MORPHOUSDT`
  - `BCHUSDT`
  - `FETUSDT`
  - `PUMPUSDT`
  - `SOLUSDT`
  - `FILUSDT`
- 候选结果:
  - baseline: `857` 笔 / `72.6%` / `+0.64%` / MDD `8.96%`
  - s1 symbol_disable: `840` 笔 / `72.5%` / `+3.81%` / MDD `9.73%`
  - s2 pocket_override: `723` 笔 / `75.2%` / `+16.42%` / MDD `3.41%`
  - s3 combo: 与 s2 完全一致，说明当前 pocket override 已经把 `long_dual_support` 全部清掉
- 当前判断:
  - s1 说明 `long_dual_support` 的坏收益确实高度集中在少数 symbol
  - s2 说明 entry-side 提纯比单纯 symbol 禁用更有效，但当前版本过于激进
  - 下一步更值得做的是:
    - 将 wildcard `*|long_dual_support` 缩成 `red_bar_growing|long_dual_support`
    - 或者保留 `long_dual_support`，只取消 `neutral 1h` 与 `trial`
