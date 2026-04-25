# MACD V2 Q4 DeepSeek Phase Update

日期：2026-04-25

本轮目标：只实现 DeepSeek 这轮要求的 Q4 定向优化，不做全局阈值放松，不恢复 VWAP 门槛。

基准对比：

- 旧基线回测：`output/backtest/v2_summary_20260425_002453.json`
- 旧基线成交：`output/backtest/v2_trades_20260425_002453.csv`
- 本轮回测：`output/backtest/v2_summary_20260425_012238.json`
- 本轮成交：`output/backtest/v2_trades_20260425_012238.csv`

---

## 1. 本轮实际改动

本轮只改了 Q4 preflip long 专属链路，没有碰全局开仓阈值，也没有恢复任何 VWAP 实盘门槛。

### 1.1 Q4 专属逻辑

文件：`src/fund_flow/macd_strategy_v2.py`

已实现：

- Q4 preflip 只允许用于 `long` 或方向尚未确定的候选，不再给 `short` 污染 bonus
- 当 4H `hist` 仍为负时，新增 `hist_prev -> hist_current` 的持续收缩检查
- Q4 bonus 从固定比例改成动态 bonus：
  - 看 `4H hist shrink`
  - 看 `RSI_4h` 接近/站上 50 的程度
  - 看 `RSI_1h` 领先程度
- 普通 RSI veto 可对 Q4 preflip long 做专属豁免
- 但保留 `rsi_extreme_overbought` 的极端超买保护

### 1.2 运行时配置

文件：`config/trading_config_fund_flow.json`

已同步：

- `q4_rsi_lead_preflip_rsi_4h_near_buffer: 20.0`
- `q4_rsi_lead_preflip_min_atr_pct_1h: 0.003`
- `q4_rsi_lead_preflip_max_atr_pct_1h: 0.070`
- `rsi_gate_q4_exempt: true`

### 1.3 回测导出字段

文件：`scripts/backtest_macd_v2.py`

已补充导出：

- `q4_rsi_lead_preflip_dynamic_multiplier`
- `q4_rsi_lead_preflip_rsi_gate_exempted`
- `q4_rsi_lead_preflip_hist_4h_prev`
- `q4_rsi_lead_preflip_hist_4h_narrowing`

本轮最新成交导出里，这些列已经存在。

---

## 2. 测试结果

本轮新增和受影响的 Q4 定向测试已通过：

- `tests/test_macd_strategy_v2_4h_scoring.py`
  - Q4 bonus 动态缩放
  - 负 4H hist 不收缩时拦截
  - Q4 不再污染 short
  - Q4 RSI veto 豁免
  - 运行时配置读取 `rsi_gate_q4_exempt`

执行结果：

- `13 passed, 51 deselected`

说明：

- 整个 `tests/test_macd_strategy_v2_4h_scoring.py` 目前仍有一批和本轮无关的旧失败
- 这些失败集中在更早的 VWAP 退役断言，不是这次 Q4 改动新引入的
- 本轮没有重写整份历史测试文件，只验证 Q4 相关改动

---

## 3. 30天回测归因

窗口：`2026-03-01 -> 2026-03-31`

### 3.1 本轮回测结果

- 收益率：`+0.26%`
- 总交易数：`2`
- 胜率：`100%`
- 最大回撤：`0.15%`
- 全部成交仍然来自 `red_bar_growing short`

逐笔成交：

| symbol | side | pnl | q4_reason |
|---|---|---:|---|
| `SOLUSDT` | `short` | `+27.65` | `trade_direction_not_long` |
| `SUIUSDT` | `short` | `+4.03` | `trade_direction_not_long` |

### 3.2 与旧基线的关键差异

旧基线 `v2_summary_20260425_002453.json`：

- 收益率：`+8.56%`
- 总交易数：`3`
- 其中 `POLUSDT short` 一笔贡献 `+833.98`

关键事实：

- 旧基线的 `POLUSDT short` 同时带着 `q4_rsi_lead_preflip_passed = true`
- 这说明旧版本把 Q4 long 预翻转 bonus 错加到了 short 链路上
- 本轮修复 `trade_direction_not_long` 后，这种污染被切掉
- 旧基线的 `+8.56%` 不能再被解释为 “Q4 long 研究链路成功”

### 3.3 这轮结果的真正含义

这轮并不是“策略退化了”，而是：

1. 把本来错误记在 Q4 long 名下的 short bonus 拿掉了
2. 增加了 4H hist 持续收缩 gate，防止负 hist 但未继续收缩的伪候选
3. Q4 long 在这个 30 天窗口内依然没有形成真实成交样本

因此，本轮回测给出的结论是：

- `Q4 long 研究链路仍未得到成交级验证`
- `旧的 +8.56% 结果里含有 short 污染，不可继续作为 Q4 成功证据`

---

## 4. 现在能对 DeepSeek 明确说的话

可以明确说：

- VWAP 实盘门槛没有被恢复
- Q4 路径已经变成更干净的 long-only 研究通道
- RSI veto 豁免已经只对 Q4 long 生效
- 回测导出已经能直接看到 Q4 gate 字段

也必须明确说：

- 2026 年 3 月这 30 天窗口里，Q4 long 仍然没有跑出真实成交
- 当前主要拦截仍然是 `rsi_gate_fail: 284`
- 这轮实现修掉的是“错误污染”和“可观察性缺失”，不是已经把 ETH 主升段抓出来了

---

## 5. 下一步建议

如果继续往下做，最值钱的不是继续松全局参数，而是：

1. 单独拉 ETH `2026-03-08 20:00 -> 2026-03-17 08:00` 的候选级 ledger
2. 对这段窗口逐 bar 输出：
   - `q4_rsi_lead_preflip_reason`
   - `q4_rsi_lead_preflip_dynamic_multiplier`
   - `q4_rsi_lead_preflip_hist_4h_prev/current`
   - `q4_rsi_lead_preflip_hist_4h_narrowing`
   - `q4_rsi_lead_preflip_rsi_gate_exempted`
3. 确认是：
   - Q4 根本没进候选
   - 还是进了候选但没过总分
   - 还是进了仓但被退出链过早切掉

这一步比继续调全市场阈值更有信息量。
