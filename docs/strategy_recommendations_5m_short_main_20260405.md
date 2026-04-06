# 5m Short-Main / Long-Pockets 策略改进建议

> 基于 `trading_config_fund_flow_review_5m_short_main_long_pockets_20260405.json`  
> 回测窗口：`2026-03-05` → `2026-04-04`（30天）  
> 审核日期：`2026-04-05`

---

## 一、当前状态 vs 目标状态总览

| 指标 | 当前状态 | 目标状态 | 差距评估 |
|---|---:|---:|---|
| 30天收益 | `+12.18%` | `100%+` | ❌ 严重不足 |
| 胜率 | `91.18%` | `80%+` | ✅ 已超达 |
| 30天实际开仓数 | `24` | `300–500` | ❌ 严重不足 |
| 杠杆 | 固定 `5X` | `2X/3X/4X` 浮动 | ❌ 不对齐 |
| 最大同时持仓对数 | `6` | `5` | ❌ 略超 |
| 单交易对仓位上限 | `30%` | `20%–30%` | ✅ 基本对齐 |
| 最大回撤 | `2.77%` | — | ✅ 极低 |

**核心诊断：策略目前处于"过度优化胜率、严重欠优化换手率"的状态。**  
胜率目标已达成，但开仓数量仅为目标的 5%，是制约收益的主因。

---

## 二、瓶颈优先级排序

根据漏斗数据，当前真实瓶颈从大到小排序如下：

```
Score 阈值压缩    →  311076 条原始信号 → 16731 条（通过率 5.38%）   ← 最大单点瓶颈
L1 结构门        →  4683 条 → 588 条  （通过率 12.56%）            ← 第二大瓶颈
最终仓位填充崩塌  →  33 候选 → 24 实际 （9 条被 min_open 阻断）     ← 第三大瓶颈
```

以下各节按优先级给出具体操作建议。

---

## 三、建议 1：分阶段降低信号 Score 阈值（最高优先级）

### 问题

当前阈值配置极度保守：

```yaml
min_signal_score_hard_floor: 0.845
flip_bullish_threshold:       0.84
red_bar_growing_threshold:    0.84
flip_bearish_threshold:       0.82
stable_*_continuation:        0.82
```

在 311,076 条原始信号中，仅 16,731 条（5.38%）能通过 Score 门。这是 24 实际开仓的根本原因。

### 建议

**分两阶段执行，每阶段独立回测验证后再推进。**

**阶段一（保守松绑）：**

```yaml
min_signal_score_hard_floor:         0.820   # 从 0.845 降低
flip_bullish_threshold:              0.820   # 从 0.840 降低
flip_bearish_threshold:              0.800   # 从 0.820 降低
stable_bull_continuation_threshold:  0.800   # 从 0.820 降低
stable_bear_continuation_threshold:  0.800   # 从 0.820 降低
red_bar_growing_threshold:           0.820   # 暂保持（见建议 3）
```

预期效果：开仓候选数量提升 3–5 倍，保持胜率 ≥ 85%。

**阶段二（中度松绑，验证阶段一无回撤劣化后执行）：**

```yaml
min_signal_score_hard_floor:  0.800
flip_bullish_threshold:       0.800
flip_bearish_threshold:       0.780
default_signal_threshold:     0.790  # 从 0.810 降低
```

### 迭代验证规则

每次调整遵循单参数隔离原则：

- 胜率 ≥ 80%（目标下限）
- 最大回撤 ≤ 10%
- Profit Factor ≥ 1.5
- 30天开仓数量 ≥ 150（阶段一验收线）

---

## 四、建议 2：修复 L1 结构门通过率（第二优先级）

### 问题

L1 结构门将 4,683 条信号压缩到 588 条（通过率仅 12.56%），是 Score 门之后的最大压缩点。

### 建议

**先诊断，再调整。** 需确认 L1 阻断的 4,095 条信号中，有多少是真正的低质量信号（应阻断）vs 有效机会损失（误阻断）。

诊断脚本（回测后置分析）：

```python
# 统计 L1 阻断信号的后续价格走势
blocked_l1 = ledger[ledger["block_stage"] == "l1_structural"]
for _, row in blocked_l1.iterrows():
    future_return = get_return_n_bars_later(row.symbol, row.ts, n=6)
    # 若 future_return 方向与信号方向一致 → 误阻断（false positive）
```

**若误阻断率 > 30%**，考虑以下放宽：

```yaml
# 针对高胜率 pocket 设置 L1 豁免
l1_bypass_pockets:
  - "flip_bullish|long_reclaim_confirmed"
  - "green_bar_growing|short_retest_reject"
  - "green_bar_growing|short_below_session_above_structure"
```

**若误阻断率 < 15%**，说明 L1 门有效，转而从 Score 阶段放量。

---

## 五、建议 3：修复最终仓位填充崩塌（第三优先级）

### 问题

33 个候选中有 9 个因 `target_portion_below_min_open` 被阻断，最终仅 24 个成功开仓。这相当于 27% 的候选在最后一步被丢弃。

### 根因

```yaml
default_target_portion: 0.30
min_open_portion:        0.06
```

当 Score 偏低导致动态仓位计算结果 < `min_open_portion` 时，仓位被整体拒绝。

### 建议

```yaml
# 方案 A：降低 min_open_portion（直接解决填充阻断）
min_open_portion: 0.03   # 从 0.06 降低

# 方案 B：针对小仓位信号设置独立通道（保守方案）
small_position_fallback_portion: 0.04
small_position_score_min:        0.83
```

**推荐方案 A**，简单直接，配合目标 300–500 开仓数量时，单笔仓位本身就不需要很大。

---

## 六、建议 4：重建浮动杠杆梯度（必须修复）

### 问题

目标杠杆为 `2X/3X/4X` 浮动梯度，但当前配置：

```yaml
min_leverage:     5
default_leverage: 5
max_leverage:     5
```

实际运行为固定 `5X`，与目标完全不对齐。**当前回测 +12.18% 基于 5X 固定杠杆，若切换至 2–4X 浮动，同等开仓数下绝对收益将线性下降。**

### 建议杠杆梯度配置

```yaml
min_leverage:     2
default_leverage: 3
max_leverage:     4

# 根据信号 score 动态分配杠杆
leverage_score_tiers:
  - score_min: 0.80
    score_max: 0.84
    leverage:  2
  - score_min: 0.84
    score_max: 0.87
    leverage:  3
  - score_min: 0.87
    score_max: 1.00
    leverage:  4
```

### 配套说明

- `2X/3X/4X` 相比固定 `5X`，杠杆均值约为 `3X`，意味着同等胜率下总收益约为 `5X` 方案的 60%。
- 要实现 `100%+` 月收益目标，在 `3X` 均值杠杆下，需要开仓数量达到 `300–500` 次才能支撑足够的总 PnL。
- **建议：先修复开仓数量（建议 1–3），再上线新杠杆梯度，两步不要同时切换。**

---

## 七、建议 5：修复 `max_active_symbols` 配置

```yaml
# 当前
max_active_symbols: 6

# 目标
max_active_symbols: 5
```

此项调整较小，但有必要与目标对齐。同时建议：

```yaml
reserve_pct: 0.15   # 从 0.20 适度降低，释放更多资金用于开仓
```

---

## 八、建议 6：处理弱 Pocket `red_bar_growing|short_retest_reject`

### 当前表现

| pocket | 交易行数 | PnL | 胜率 |
|---|---:|---:|---:|
| `red_bar_growing\|short_retest_reject` | 2 | -98.24 | 50.0% |

**结论：该 pocket 当前是净亏损来源，胜率仅 50%，样本量少但方向性明确。**

### 建议

**短期（立即执行）**：

```yaml
# 提高该 pocket 专属阈值至强制过滤水位
red_bar_growing_threshold: 0.88   # 从 0.84 提高
```

**中期（完成阶段一扩量回测后评估）**：

```yaml
# 若样本量扩大后胜率仍 < 60%，禁用该 pocket
disable_red_bar_growing_short_entries: true
```

---

## 九、建议 7：Same-bar 模式切换（回测真实性修复）

### 问题

当前回测设置：

```yaml
same_bar_tp_priority_mode: tp1_before_stop
```

这是乐观假设，在同一根 K 线内先触 TP1 而非止损，导致 `+12.18%` 的回测结果存在高估。

### 建议

**在任何扩量实验之前，先切换为保守模式并记录差值：**

```yaml
same_bar_tp_priority_mode: stop_first
```

执行一次完整 30 天回测，记录新旧结果差值。若差值 > 3%，说明当前策略对同 bar 优先级有较强依赖，需在扩量时特别注意出口执行质量。

---

## 十、Pocket 扩展路线图（提升换手率）

当前所有 PnL 来自 5 个 pocket，远不够支撑 300–500 次/月的目标。建议按以下顺序研究新 pocket：

### 优先级 1：扩展已验证 Short Pocket 的参数范围

```yaml
# 当前 ADX 范围过窄
green_bar_growing_short_min_adx_1h: 40
green_bar_growing_short_max_adx_1h: 50

# 建议扩展
green_bar_growing_short_min_adx_1h: 35
green_bar_growing_short_max_adx_1h: 60
```

### 优先级 2：开放 Long 侧 `stable_bull_continuation` pocket

当前 Long 侧仅有 2 个白名单 pocket，`stable_bull_continuation` 尚未开放。  
可先以 **高阈值 + 小仓位** 方式试验：

```yaml
long_stable_bull_continuation_enabled: true
stable_bull_continuation_threshold:    0.86   # 保守起点
stable_bull_continuation_max_portion:  0.15   # 仓位上限
```

### 优先级 3：研究 `flip_bearish` Short Pocket 扩展

当前 `flip_bearish` 阈值为 `0.82`，若 ADX 和 VWAP 过滤条件同步收紧，可在不损失胜率的情况下扩大样本。

---

## 十一、执行路线图

```
阶段 0（立即，单次回测）
  → 切换 same_bar_tp_priority_mode = stop_first
  → 记录真实基准：return / win_rate / drawdown

阶段 1（第 1–2 周）
  → 降低 Score 阈值（建议 1 阶段一）
  → 修复 min_open_portion（建议 3 方案 A）
  → 下调 red_bar_growing_threshold = 0.88
  → 回测验证：目标开仓 ≥ 100 次 / 30d，胜率 ≥ 83%

阶段 2（第 2–3 周，阶段 1 验收后）
  → 重建浮动杠杆 2X/3X/4X（建议 4）
  → 修复 max_active_symbols = 5
  → 降低 Score 阈值（建议 1 阶段二）
  → 回测验证：目标开仓 ≥ 200 次 / 30d

阶段 3（第 3–4 周）
  → L1 门诊断与选择性豁免（建议 2）
  → ADX 范围扩展（建议 6 优先级 1）
  → 开放 stable_bull_continuation 试验 pocket
  → 回测验证：目标开仓 ≥ 300 次 / 30d，胜率 ≥ 80%

阶段 4（第 4 周+，达标后）
  → 逐步上线 live 验证
  → 监控 protection_sla 误触发率
  → 监控 alpha 稀释漏斗各层通过率
```

---

## 十二、关键风险提示

| 风险 | 描述 | 缓解措施 |
|---|---|---|
| 胜率下滑 | 放量后低质量信号混入 | 分阶段验证，每阶段设胜率下限 80% |
| 同 bar 乐观性 | 当前 +12.18% 含 TP 优先偏差 | 先跑 `stop_first` 模式获取真实基准 |
| 杠杆切换冲击 | 从固定 5X 切换为 2–4X 后收益线性下降 | 开仓数量扩量与杠杆切换分开两个阶段 |
| L1 松绑风险 | 误阻断率未确认前不应直接修改 L1 规则 | 先跑 L1 诊断，确认 false positive 率 |
| TONUSDT 亏损 | 单个符号贡献 -127.98 | 考虑暂时加入黑名单或单独设更高阈值 |

---

## 十三、量化目标验收标准

各阶段 **全部满足** 以下条件方可推进到下一阶段：

```
阶段 1 验收：
  ✅ 30d 开仓数 ≥ 100
  ✅ 胜率 ≥ 83%
  ✅ Profit Factor ≥ 1.8
  ✅ 最大回撤 ≤ 6%

阶段 2 验收：
  ✅ 30d 开仓数 ≥ 200
  ✅ 胜率 ≥ 82%
  ✅ Profit Factor ≥ 1.6
  ✅ 最大回撤 ≤ 8%

阶段 3 验收（上线前）：
  ✅ 30d 开仓数 ≥ 300
  ✅ 胜率 ≥ 80%
  ✅ Profit Factor ≥ 1.5
  ✅ 最大回撤 ≤ 10%
  ✅ stop_first 模式下 30d 收益 ≥ 50%（保守基准）
```

---

*文档生成时间：2026-04-05 | 基于 bot_like_summary_20260405_233325.json 及策略审计文档*
