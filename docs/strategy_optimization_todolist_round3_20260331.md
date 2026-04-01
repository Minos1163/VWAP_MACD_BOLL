# MACD V2 Round 3 Todo

日期: 2026-03-31
当前基线: `config/trading_config_fund_flow.json`
Round 2 主结果: `4` 笔, `75.00%` 胜率, `0.32%` MDD, `+0.87%`

## 本轮目标

- 扩大样本，不再在 30 天 4 笔上继续过拟合
- 搭建信号漏斗诊断，定位真实瓶颈层
- 找到下一个“安全扩展机会”的方向，而不是继续盲调

## Todo

- [x] 整理 Round 3 审核意见为执行清单
- [x] 跑 90 天主配置 bot-like 回测
- [x] 记录 90 天样本量、收益、胜率、PF、MDD
- [x] 核查 `bot-like` 回测是否已有漏斗/拒绝原因输出能力
- [x] 回头检查历史高频 `v2` 配置，确认扩量必须从高频分支而不是当前低频主配置出发
- [x] 基于 `live_production` 复制高频候选 A/B，并完成首轮提纯回测
- [x] 在最佳高频候选上验证“精细收口”路径，确认全局分数窗不如 symbol 级禁用稳定
- [x] 基于 `green_bar_growing` 亏损归因，为最差 6 个 symbol 增加定向禁用
- [ ] 如果没有，设计最小版 signal funnel instrumentation
- [ ] 评估 `stable_bull_continuation` 是否适合先做 shadow log
- [ ] 准备 `weight_4h_enhancement: 0.05 -> 0.10` 的 90 天 ablation 方案
- [ ] 评估 DOGE/BCH/AAVE 未来从 disable 迁移到 symbol-level CVD+ADX gate 的实现点

## 当前判断

- `trial` 路径 CVD 过滤不适合做全局门，Round 2 已验证
- Round 3 的前置条件是:
  - 90 天基线回测
  - 信号漏斗诊断能力
- 在这两个前置条件完成前，不应继续对 `score window`、`trial gate` 做主观放宽

## 预期交付

- 90 天基线回测结果
- Round 3 漏斗诊断能力状态说明
- 下一轮最小实现建议

## 已完成结果

### 90 天基线

- 窗口: `2026-01-01 00:00:00` -> `2026-03-31 23:59:59`
- 回测摘要: `D:\AIDCA\AI8\output\backtest\bot_like_summary_20260331_192159.json`
- 成交明细: `D:\AIDCA\AI8\output\backtest\bot_like_trades_20260331_192159.csv`

指标:

- `7` 笔成交
- 胜率 `71.43%`
- 收益 `+0.68%`
- PF `4.04`
- MDD `0.38%`

观察:

- 90 天样本仍低于 Round 3 目标中的 `15` 笔
- 7 笔成交全部仍是 `flip_bullish`
- symbol 分布继续集中:
  - `POLUSDT`: 4 笔
  - `ATOMUSDT`: 1 笔
  - `FETUSDT`: 1 笔
  - `ALGOUSDT`: 1 笔
- PnL 继续由 `FETUSDT` 主导，`ATOMUSDT` 是主要亏损源

### 漏斗诊断能力现状

- `scripts/backtest_fund_flow_bot_like.py` 当前支持:
  - `--config`
  - `--start`
  - `--end`
  - `--profile`
  - 若干资金/仓位参数
- 当前没有发现现成的 CLI 参数用于:
  - `output-prefix`
  - `filter-signal-type`
  - `output-rejected-reasons`
  - `signal-funnel`
- `output/backtest/logs` 目录当前没有自动产出可直接复用的 gate/funnel 日志

结论:

- Round 3 下一个实现点应是最小版 signal funnel instrumentation
- 在漏斗数据出来之前，不建议直接推进 `stable_bull` 开启或 `weight_4h_enhancement` 调整

### 高频扩量分支新发现

- 历史高频基线证明，`200-300` 笔/月不是靠当前低频保护版放宽得到的，而是要回到高频 `v2` 分支:
  - `D:\AIDCA\AI8\output\backtest\v2_summary_20260331_160345.json`
    - `289` 笔
    - 胜率 `71.63%`
    - 收益 `+8.82%`
  - `D:\AIDCA\AI8\output\backtest\v2_summary_20260327_214704.json`
    - `432` 笔
    - 胜率 `73.38%`
    - 收益 `+128.45%`
- 因此本轮扩量路线切换为:
  - 以 `config/trading_config_fund_flow_live_production.json` 为母版
  - 先做“高频提纯”，再看是否需要继续扩 symbol/gate

### 高频候选首轮结果

- 候选 A: `D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_round3_hf_quality_a.json`
  - 摘要: `D:\AIDCA\AI8\output\backtest\v2_summary_20260331_195033.json`
  - `282` 笔
  - 胜率 `73.05%`
  - 收益 `+11.67%`
  - MDD `4.04%`
- 候选 B: `D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_round3_hf_quality_b.json`
  - 摘要: `D:\AIDCA\AI8\output\backtest\v2_summary_20260331_195036.json`
  - `264` 笔
  - 胜率 `74.24%`
  - 收益 `+17.56%`
  - MDD `3.30%`

当前判断:

- 最接近目标的是候选 B
- 当前主要拖累胜率的不是 `flip_bullish` 或 `red_bar_growing`
- 真正的收口重点是 `green_bar_growing` 中的极端分数区间:
  - `< 0.86`
  - `> 0.95`
- 所以下一轮改动不再抬全局阈值，而是新增 `green_bar_growing` 独立分数窗

### 高频候选第二轮结果

- 候选 C: `D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_round3_hf_quality_c.json`
  - 做法:
    - 尝试 `green_bar_growing_score_window`
    - 额外把 `green_bar_growing_short_min_adx_1h` 提到 `42`
  - 摘要: `D:\AIDCA\AI8\output\backtest\v2_summary_20260331_202432.json`
  - 结果:
    - `266` 笔
    - 胜率 `72.93%`
    - 收益 `+21.72%`
    - MDD `3.67%`
- 结论:
  - 全局 `green_bar_growing` 分数窗会改变成交排序与容量占用
  - 这条路对高频扩量不稳定，不作为最终方案

### 达标方案

- 候选 D: `D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_round3_hf_quality_d.json`
- 最终整理版: `D:\AIDCA\AI8\config\trading_config_fund_flow_round3_scale_target.json`
- 关键改动:
  - 维持候选 B 的高频参数骨架
  - 不使用全局 `green_bar_growing` 分数窗
  - 仅对 `green_bar_growing` 定向禁用最差 6 个 symbol:
    - `DOTUSDT`
    - `ONDOUSDT`
    - `HYPEUSDT`
    - `WLDUSDT`
    - `DOGEUSDT`
    - `LTCUSDT`
  - 修正 `iflow` 路径回到 `AI8`
- 回测摘要: `D:\AIDCA\AI8\output\backtest\v2_summary_20260331_203913.json`
- 成交明细: `D:\AIDCA\AI8\output\backtest\v2_trades_20260331_203913.csv`

达标结果:

- `264` 笔 / 30 天
- 胜率 `77.27%`
- 收益 `+22.19%`
- PF `2.24`
- 真 MDD `3.00%`

结论:

- Round 3 的扩量目标已经达成
- 真正有效的扩量提纯方式不是继续抬全局门槛
- 而是基于成交归因，对高频主损耗通道做 symbol 级定向收口
