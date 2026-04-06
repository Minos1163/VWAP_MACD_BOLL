# MACD V2 策略参数回退优化方案

**基于**: 六项一次性优化回测结果 (2026-04-04)  
**问题**: 仓位压缩过于激进，收益 +98% → +36%，胜率 85.2% → 79.9%  
**目标**: 在保留风控成果的前提下，恢复收益能力  

| 指标 | 基线 | 当前优化后 | 本轮目标 |
|------|------|-----------|---------|
| 胜率 | 85.2% | 79.9% | **≥ 83%** |
| 收益率 | +98.23% | +36.09% | **≥ 65%** |
| 最大回撤 | 11.07% | 6.22% | **≤ 8%** |
| 盈利因子 | 2.06 | 1.90 | **≥ 1.95** |
| 交易数 | 742 | 567 | **≥ 650** |
| 最大单笔亏损 | -$592 | -$185 | **≤ -$280** |

> **核心原则**: 已验证有效且副作用小的优化项（flip_bearish、高分去关联、VWAP状态）**保持不动**。仅回退两个过度压缩的仓位参数，每个独立测试。

---

## 一、保留不动的优化项（已验证，不回退）

在开始任何回退测试前，明确锁定以下四项：

| 优化项 | 当前值 | 锁定原因 |
|--------|--------|---------|
| flip_bearish 过滤 | signal ≥ 0.88, vwap ≥ 0.20 | 57笔净亏信号清零，无副作用 |
| 高评分仓位去关联 | 0.95+ 杠杆 3x，仓位乘数 1.0 | 均亏从 $230→$61，无胜率代价 |
| long_below_both 仓位压缩 | 40% 仓位 / 3x 杠杆 | 亏损降 84.8%，方向正确，仅幅度可调 |
| short_above_both 仓位压缩 | 40% 仓位 / 3x 杠杆 | 亏损降 88.5%，同上 |
| 保本止损提前 | 触发 0.8%，锁定 0.4% | 回撤期保护有效，暂保留 |

---

## 二、Test-1：低流动性时段仓位回退

### 背景

UTC 22:00-04:00 仓位压缩至 50% 是收益下降的**最大单一来源**，该时段约占全天 25% 的交易时间，对盈利交易影响与对亏损的压缩是对称的，因此净效果是收益和风险同步缩水，并非真正改善了质量。

**数据支持**：

```
基线周日亏损:    $3,370（但盈利也相应高）
优化后周日亏损:  $1,806（-46%）
但周日盈利也同步缩水约 46%，净期望未改善
```

### 变更方案

```json
// config/trading_config_fund_flow.json
"session_risk_control": {
  "enabled": true,
  "high_risk_sessions": [
    {
      "utc_start": "22:00",
      "utc_end": "04:00",
      "position_scale": 0.65    // 从 0.50 放宽至 0.65
    },
    {
      "utc_start": "03:00",
      "utc_end": "05:30",
      "position_scale": 0.60    // 保留原亚洲深夜段（原值）
    },
    {
      "utc_start": "14:30",
      "utc_end": "16:00",
      "position_scale": 0.55    // 保留原美欧交接段（原值）
    }
  ],
  "apply_to_states": [
    "flip_bearish",
    "flip_bullish",
    "short_dual_pressure",
    "long_dual_support",
    "short_above_both",
    "long_below_both"
  ]
}
```

**注意**：`apply_to_states` 在 6 项优化中被扩展为全部信号状态，本次回退**仅调整 position_scale**，不缩减 apply_to_states 的覆盖范围，避免引入新变量。

### 预期效果

```
仓位从 50% → 65%，约恢复该时段 30% 的盈利能力
预计胜率恢复: +1.5% ~ +2.0%（约达 81.5%~82%）
预计收益恢复: +15% ~ +20%（约达 51%~56%）
预计最大回撤: 7.0% ~ 7.5%（仍在 8% 以内）
```

### 验证标准

```
接受条件（全部满足）:
  ✓ win_rate ≥ 81.5%
  ✓ max_drawdown ≤ 8.0%
  ✓ profit_factor ≥ 1.88
  ✓ trade_count ≥ 600

拒绝条件（任一触发）:
  ✗ max_drawdown > 8.5%（风控红线被突破）
  ✗ 周日最大单笔亏损 > $350（重回大额止损模式）
```

---

## 三、Test-2：劣势 VWAP 状态仓位回退

### 背景

`long_below_both` 和 `short_above_both` 仓位压缩至 40%（+杠杆 3x），亏损压缩效果极其显著（分别降 84.8% 和 88.5%）。但 40% 仓位可能过于保守，使该状态的盈利交易收益也大幅萎缩。

**关键问题**：这两个状态的**盈利交易胜率有多高**？如果该状态历史胜率仍有 80%+，则 55% 仓位已足够控制风险，不必压缩至 40%。

### 变更方案

```json
// config/trading_config_fund_flow.json
"vwap_structure_overrides": {
  "long_below_both": {
    "position_scale": 0.55,       // 从 0.40 放宽至 0.55
    "max_leverage_override": 3    // 保持 3x 杠杆上限不变（关键风控）
  },
  "short_above_both": {
    "position_scale": 0.55,       // 从 0.40 放宽至 0.55
    "max_leverage_override": 3    // 保持 3x 杠杆上限不变（关键风控）
  }
}
```

**重要**：杠杆上限 3x **不回退**。当前优化中，仓位和杠杆双重压缩是过激的，但单独放宽仓位（55%）同时保持 3x 杠杆上限，名义敞口从 `40% × 3x = 120%` 升至 `55% × 3x = 165%`，仍远低于基线的 `50% × 5x = 250%`。

### 预期效果

```
名义敞口对比:
  基线:      50% × 5x = 250%
  6项优化后: 40% × 3x = 120%  ← 过激
  本轮目标:  55% × 3x = 165%  ← 平衡点

亏损控制预估（与6项优化后对比）:
  long_below_both 亏损: $254 → ~$400（仍比基线 $1,671 低 76%）
  short_above_both 亏损: $78 → ~$130（仍比基线 $679 低 81%）

胜率恢复预估: +0.8% ~ +1.5%
收益恢复预估: +8% ~ +12%
```

### 验证标准

```
接受条件（全部满足）:
  ✓ long_below_both 总亏损 ≤ $600（低于基线 $1,671 的 36%）
  ✓ short_above_both 总亏损 ≤ $300（低于基线 $679 的 44%）
  ✓ win_rate 较 Test-1 基础上再提升 ≥ 0.5%
  ✓ max_drawdown ≤ 8.0%

拒绝条件（任一触发）:
  ✗ long_below_both 均亏 > $120（超过6项优化后 $50 的 2.4 倍）
  ✗ max_drawdown > 8.5%
```

---

## 四、Test-3：red_bar_shrinking 异常修复

### 背景

6 项优化后出现了严重的异常恶化：

```
基线:      12 笔，91.7% 胜率，净盈 +$381
优化后:    17 笔，58.8% 胜率，净亏 -$778 ← 异常
变化幅度:  -$1,159（相当于异常新增 $1,159 的亏损）
```

这不是正常的参数调整副作用，而是**行为异常**——胜率从 91.7% 崩溃至 58.8%，同时交易数反而增加（12→17笔），说明某个优化项的副作用改变了 `red_bar_shrinking` 的入场条件或筛选逻辑。

### 根因排查

**假设 1**：`session_risk_control` 扩展覆盖了 `red_bar_shrinking` 状态，但仓位压缩（50%）改变了持仓比例，导致保本触发点相对位移。

**假设 2**：`breakeven_trigger_pnl_ratio` 从 1.2% 降至 0.8%，对 `red_bar_shrinking` 这类短暂收缩信号影响更大（它们的浮盈可能在 0.8%~1.2% 区间波动后被打出）。

**排查脚本**：

```python
# 从回测交易记录中筛选 red_bar_shrinking 的时间分布
import pandas as pd

trades = pd.read_csv("output/backtest/v2_trades_20260404_195029.csv")
rbs = trades[trades['signal_type'] == 'red_bar_shrinking']

# 检查时间分布
print("按UTC小时分布:")
print(rbs.groupby(rbs['entry_time'].str[11:13])['is_win'].agg(['count', 'mean']))

# 检查退出原因
print("\n退出原因分布:")
print(rbs['exit_reason'].value_counts())

# 检查是否集中在 22:00-04:00
rbs['hour'] = pd.to_datetime(rbs['entry_time']).dt.hour
low_liq = rbs[rbs['hour'].isin([22, 23, 0, 1, 2, 3])]
print(f"\n低流动性时段 red_bar_shrinking: {len(low_liq)} 笔, 胜率: {low_liq['is_win'].mean():.1%}")
```

### 临时修复方案

在根因确认前，将 `red_bar_shrinking` 从 `session_risk_control` 的 `apply_to_states` 中移除：

```json
"session_risk_control": {
  "apply_to_states": [
    "flip_bearish",
    "flip_bullish",
    "short_dual_pressure",
    "long_dual_support",
    "short_above_both",
    "long_below_both"
    // 注意：移除 red_bar_shrinking 和 green_bar_shrinking
  ]
}
```

**验证标准**：

```
接受条件:
  ✓ red_bar_shrinking 胜率恢复至 ≥ 82%
  ✓ red_bar_shrinking 净盈亏转为正值

如果修复无效（胜率仍 < 70%）:
  → 根因在 breakeven 参数，而非 session_risk
  → 测试对 red_bar_shrinking 单独保留 breakeven_trigger=1.2%（信号类型差异化保本）
```

---

## 五、Test-4：组合验证（三项回退合并）

在 Test-1、Test-2、Test-3 分别通过单变量验证后，合并执行完整回测。

### 目标配置状态

```
保留（不变）:
  ✓ flip_bearish: signal ≥ 0.88, vwap ≥ 0.20
  ✓ 高分仓位去关联: 0.95+ → 3x / 1.0x
  ✓ 保本止损: 触发 0.8%，锁定 0.4%

回退（放宽）:
  ✓ 低流动性时段: 50% → 65%
  ✓ long_below_both/short_above_both: 40% → 55%（杠杆仍 3x）
  ✓ red_bar_shrinking 移出 session_risk apply_to_states
```

### 组合目标指标

| 指标 | 6项优化后 | 组合目标 | 允许范围 |
|------|----------|---------|---------|
| 胜率 | 79.9% | **83%** | 82%~85% |
| 收益率 | +36.09% | **+65%** | +60%~+75% |
| 最大回撤 | 6.22% | **≤ 8%** | ≤ 8.5% |
| 盈利因子 | 1.90 | **≥ 1.95** | 1.90~2.10 |
| 交易数 | 567 | **≥ 650** | 640~720 |
| 最大单亏 | -$185 | **≤ -$280** | ≤ -$320 |

---

## 六、执行顺序（严格单变量）

```
Week 1（当前周）:

  [Step 0] red_bar_shrinking 根因排查（脚本分析，无需回测）
           ↓ 确认根因后
  [Test-1] 仅改: 低流动性时段 50% → 65%
           记录: win_rate, drawdown, profit_factor, trade_count
           ↓ 通过后
  [Test-2] 仅改: long_below_both/short_above_both 40% → 55%
           记录: 同上 + 两个状态的专项亏损数据
           ↓ 通过后
  [Test-3] 仅改: red_bar_shrinking 移出 session_risk
           记录: red_bar_shrinking 胜率和净盈亏

Week 2:

  [Test-4] 合并 Test-1 + Test-2 + Test-3
           全量回测，验证组合是否达到目标区间
           ↓ 若仍未达到 83% 胜率
  [Test-5] 在 Test-4 基础上，flip_bearish vwap 门槛 0.20 → 0.16
           预计恢复约 20~30 笔 flip_bearish 高质量信号
```

---

## 七、如果组合测试仍未达到 83% 胜率

在 Test-4 后若胜率仍在 81%~82%，有两条可选路径：

**路径 A：小幅放宽 flip_bearish 门槛（推荐）**

```json
"flip_bearish_retest_reject_min_vwap_score": 0.16  // 从 0.20 降至 0.16
// 预计恢复约 20~30 笔中高质量 flip_bearish 信号
// 这部分信号 VWAP 分在 0.16~0.20，历史胜率待验证
```

**路径 B：放宽保本触发点（谨慎）**

```json
"breakeven_trigger_pnl_ratio": 0.010  // 从 0.8% 放宽至 1.0%（折中值）
// 减少趋势延续信号被过早保本出场的概率
// 但会增加 stop_loss_intrabar_after_tp1 的亏损笔数
```

选择标准：如果排查确认 `red_bar_shrinking` 异常来自 breakeven，则优先走路径 B；否则走路径 A。

---

## 八、每次回测记录模板

```markdown
## [Test-N] 参数变更记录

**变更项**: [参数名] [旧值] → [新值]
**回测区间**: 2026-03-05 ~ 2026-04-04（与基线和6项优化保持相同区间）

| 指标 | 基线 | 6项优化后 | 本次结果 | 与目标差距 |
|------|------|----------|---------|-----------|
| win_rate | 85.2% | 79.9% | ? | 目标≥83% |
| total_return | +98.23% | +36.09% | ? | 目标≥65% |
| max_drawdown | 11.07% | 6.22% | ? | 目标≤8% |
| profit_factor | 2.06 | 1.90 | ? | 目标≥1.95 |
| trade_count | 742 | 567 | ? | 目标≥650 |
| max_single_loss | -$592 | -$185 | ? | 目标≤$280 |
| red_bar_shrinking_wr | 91.7% | 58.8% | ? | 目标≥82% |
| long_below_both_loss | -$1,671 | -$254 | ? | 目标≤$600 |

**结论**: [接受 / 拒绝 / 继续调整]
**下一步**: [下一个测试项]
```

---

**报告版本**: v1.1 | 2026-04-04  
**前置数据**: `output/backtest/v2_summary_20260404_195029.json`（6项优化后基线）  
**下次更新**: 完成 Test-1（低流动性时段 50%→65%）回测后
