# MACD V2 long_dual_support Structural Repair Todo

日期：2026-04-01

## Todo

- [x] 复查 `long_dual_support` 在最新 30 天回测中的 pocket 归因，确认问题是 entry alpha 而非 exit 或 symbol 黑名单
- [x] 盘点现有策略层能力，确认哪些规则可直接复用 `pocket_entry_overrides`
- [x] 在策略层补齐 `pocket_scoring_overrides`，允许 `long_dual_support` 独立调整 4H/1H/VWAP/15M/Volume 权重
- [x] 在策略层补齐 `long_dual_support` 的 pocket 级附加 gate：
  - 更严格的 1H 确认
  - 独立 `min_signal_score`
  - 独立 `min_vwap_score`
  - 禁止共享 `trial` 放宽
  - `require_cvd_ok`
  - `require_cvd_momentum_ok`
  - 独立 `min_entry_score`
- [x] 在回测构建器中接入 `pocket_scoring_overrides`
- [x] 补定向测试，覆盖 pocket 级 entry gate 与 scoring override
- [x] 生成 `long_dual_support` 第一轮结构修复候选配置
- [x] 跑 30 天基线对照回测
- [x] 比较 `long_dual_support` pocket PnL、整体收益、胜率、交易数、MDD
- [x] 形成结论并给出是否值得同步到实盘的建议

## 实验设计

- `E1`: 只收紧 `1H neutral/light` 与 `trial`，不动权重
- `E2`: 在 `E1` 基础上加入 `CVD / VWAP / score / 15m` 独立门槛
- `E3`: 在 `E2` 基础上加入 `pocket_scoring_overrides`
- `E4`: 若 `E3` 仍不能让 pocket 转正，再考虑更高的 `min_signal_score`

## 验收标准

- `long_dual_support` PnL 相比基线显著改善，优先目标是转正
- 整体 `win_rate >= 83%`
- 整体 `trade_count >= 380`
- 整体 `return` 相比基线提升
- `MDD <= 3.5%`

## 结果

- 基线：
  - `499` 笔 / `83.17%` / `+7.00%` / `MDD 2.87%`
  - `red_bar_growing + long_dual_support`: `153` 笔 / `-236.92` / `avg_win +6.52` / `avg_loss -33.28`
- `E1` 仅收紧 `1H neutral/light + trial`：
  - `499` 笔 / `83.17%` / `+7.74%` / `MDD 2.96%`
  - `red_bar_growing + long_dual_support`: `153` 笔 / `-190.88`
  - 结论：只改 1H 严格度能改善 pocket payoff，但不能从根上修复
- `E2` 加入 `CVD + VWAP + score + 15m` 独立门槛：
  - `438` 笔 / `84.02%` / `+12.31%` / `MDD 2.34%`
  - `red_bar_growing + long_dual_support`: `0` 笔 / `0.00`
  - 结论：结构 gate 已经把坏 pocket 在当前 30 天样本内完全清掉，整体收益明显提升
- `E3` 再叠加 pocket 级评分权重：
  - 与 `E2` 完全一致
  - 结论：在当前样本中，entry gate 已经足够严格，评分权重调整没有额外增益
- `E4` 再提高 `min_signal_score`：
  - 与 `E2` 完全一致
  - 结论：高门槛没有带来增益，当前瓶颈不在更高 score，而在准入结构本身

## 暂定结论

- 当前最优候选是 `E2`
- 它不是 symbol 筛选，也不是 exit 修饰，而是对 `long_dual_support` 做了结构级重定义：
  - 不接受 neutral/light 1H
  - 不共享 trial 放宽
  - 必须有更高的 VWAP / score / CVD / 15m 质量
- `E2` 已经达到“提升收益、抬高胜率、降低回撤”的目标
- 现阶段不建议把 `E3/E4` 继续堆上去，因为它们在当前样本下没有新增价值
