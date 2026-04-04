# 资金流交易系统 - 实盘开仓链路完整分析

**报告日期**: 2026-04-04  
**配置文件**: `config/trading_config_fund_flow.json`  
**策略版本**: MACD V2.0 (VWAP + BOLL 增强版)  
**分析目的**: 找出48小时未开仓原因，梳理完整开仓链路，为门槛拆卸提供依据

---

## 🔍 一、48小时未开仓原因分析

### 1.1 可能原因排查

基于代码分析和配置参数，未开仓的可能原因：

| 原因 | 可能性 | 说明 |
|------|--------|------|
| **市场无趋势** | 🔴 高 | ADX < 30 触发趋势门控阻断 |
| **信号分不足** | 🔴 高 | 综合评分 < 0.38 或信号分 < 0.85 |
| **ATR 异常** | 🟠 中 | ATR < 0.12% 或 > 2% |
| **VWAP 偏离过大** | 🟠 中 | 价格偏离 VWAP > 3% |
| **持仓已满** | 🟡 低 | max_active_symbols = 4 已达上限 |
| **高危时段** | 🟡 低 | 处于 03:00-05:30 或 14:30-16:00 UTC |
| **BOLL 中轨阻挡** | 🟡 低 | 价格突破 BOLL 中轨被硬阻挡 |
| **黑名单过滤** | 🟢 极低 | 目标币种在黑名单中 |

### 1.2 关键门槛检查清单

```python
# 必须同时满足以下条件才能开仓：

✅ ADX >= 30                          # 趋势强度
✅ 0.12% <= ATR <= 2%                 # 波动率正常
✅ 信号分 >= 0.85                     # 信号质量
✅ 综合评分 >= 0.38                   # 开仓阈值
✅ VWAP 偏离 < 3%                     # 价值评估
✅ 价格未突破 BOLL 中轨               # 结构检查
✅ 不在高危时段 (03:00-05:30, 14:30-16:00 UTC)
✅ 当前持仓数 < 4                     # 容量检查
✅ 不在黑名单中                       # 币种过滤
```

**最可能的原因**: 
1. **ADX < 30**: 市场处于震荡状态，不满足趋势门控
2. **信号分 < 0.85**: 信号质量未达到最低要求
3. **综合评分 < 0.38**: 多维度加权后未达开仓阈值

---

## 🎯 二、实盘开仓完整链路

### 2.1 开仓决策流程图

```
┌─────────────────────────────────┐
│  Step 0: 数据准备                │
│  - 获取 K 线数据 (4H/1H/15M/5M) │
│  - 计算技术指标 (MACD/BOLL/VWAP)│
│  - 获取 CVD/资金流数据           │
└──────────────┬──────────────────┘
               ↓
┌─────────────────────────────────┐
│  Step 1: 市场状态识别            │
│  - 判断 TREND/RANGE/NO_TRADE    │
│  - ADX 趋势门控检查              │
│  - ATR 波动率检查                │
└──────────────┬──────────────────┘
               ↓
        ┌─────┴─────┐
        │ NO_TRADE? │──YES──→ ❌ 拒绝开仓
        └─────┬─────┘
              NO
              ↓
┌─────────────────────────────────┐
│  Step 2: 信号生成               │
│  - 4H MACD 方向评分 (35%)       │
│  - 4H 增强因子 (5%)             │
│  - 1H MACD 确认 (20%)           │
│  - VWAP 偏离评分 (20%)          │
│  - 成交量确认 (15%)             │
│  - 15M 入场时机 (5%)            │
└──────────────┬──────────────────┘
               ↓
┌─────────────────────────────────┐
│  Step 3: 信号分检查             │
│  signal_score >= 0.85?         │
└──────┬──────────────────┬──────┘
       NO                 YES
       ↓                  ↓
   ❌ 拒绝          ┌──────────────────┐
                    │  Step 4: 特殊信号过滤│
                    │  - Flip 严格过滤  │
                    │  - Shrinking 过滤 │
                    │  - Preflip 试仓   │
                    └──────┬───────────┘
                           ↓
┌─────────────────────────────────┐
│  Step 5: L1 硬过滤              │
│  - ADX >= 30                    │
│  - ATR 0.12%-2%                 │
│  - BOLL 中轨检查                │
│  - VWAP 偏离 < 3%               │
└──────┬──────────────────┬──────┘
       FAIL               PASS
       ↓                  ↓
   ❌ 拒绝          ┌──────────────────┐
                    │  Step 6: 综合评分  │
                    │  open_score >= 0.38?│
                    └──────┬───────────┘
                           ↓
                    ┌─────┴─────┐
                    │ < 0.38?   │──YES──→ ❌ 拒绝
                    └─────┬─────┘
                          NO
                          ↓
┌─────────────────────────────────┐
│  Step 7: 容量检查               │
│  当前持仓数 < max_active_symbols?│
└──────┬──────────────────┬──────┘
       NO                 YES
       ↓                  ↓
   ❌ 拒绝          ┌──────────────────┐
                    │  Step 8: pretrade  │
                    │  risk_gate 检查    │
                    │  - ATR 极端值      │
                    │  - 资金占用        │
                    └──────┬───────────┘
                           ↓
                    ┌─────┴─────┐
                    │ BLOCK?    │──YES──→ ❌ 拒绝
                    └─────┬─────┘
                          NO
                          ↓
┌─────────────────────────────────┐
│  Step 9: 执行下单               │
│  - 计算仓位大小                  │
│  - 确定杠杆倍数                  │
│  - IOC 订单提交                  │
│  - GTC 回退机制                  │
└─────────────────────────────────┘
```

### 2.2 详细步骤说明

#### Step 0: 数据准备

**数据来源**:
- Binance Futures API (K 线数据)
- 本地缓存 (`data/backtest_cache/*.parquet`)
- CVD 数据 (累积成交量偏差)

**时间框架**:
- 4H: 主趋势判断
- 1H: 辅助确认
- 15M: 入场时机
- 5M: 微观结构 (可选)

**技术指标**:
```python
{
  "MACD": {"fast": 12, "slow": 26, "signal": 9},
  "BOLL": {"period": 20, "std_dev": 2.0},
  "VWAP": {"anchor": "daily_utc0"},
  "EMA": [10, 20, 30, 50],
  "ADX": {"period": 14},
  "ATR": {"period": 14}
}
```

#### Step 1: 市场状态识别

**Regime Detection**:
```python
regime_timeframe = "1h"
adx_trend_on = 15.0      # ADX > 15 判定为趋势市
adx_range_on = 14.5      # ADX < 14.5 判定为区间市
adx_no_trade_low = 14.6  # ADX < 14.6 禁止交易
adx_no_trade_high = 15.0 # ADX > 15.0 允许交易
```

**趋势门控 (Trend Gate)**:
```json
{
  "trend_gate_enabled": true,
  "trend_gate_adx_min": 30,           // ADX 最低 30
  "trend_gate_atr_pct_min": 0.0012,   // ATR 最低 0.12%
  "trend_gate_atr_pct_max": 0.02      // ATR 最高 2%
}
```

**状态分类**:
- **TREND**: ADX >= 30, 有明确趋势
- **RANGE**: ADX < 30, 区间震荡
- **NO_TRADE**: ATR 异常或 ADX 过低

#### Step 2: 信号生成

**多维度评分体系**:

```python
signal_score = (
    score_4h_direction    × 0.35 +   # 4H 主趋势
    score_4h_enhancement  × 0.05 +   # 4H 增强
    score_1h_direction    × 0.20 +   # 1H 确认
    score_vwap            × 0.20 +   # VWAP 偏离
    score_volume          × 0.15 +   # 成交量
    score_15m_entry       × 0.05     # 15M 入场
)
```

**各维度评分逻辑**:

1. **4H 方向得分 (35%)**:
   - 基于 4H MACD 柱状图方向和强度
   - 多头: MACD histogram > 0 且增长 → 高分
   - 空头: MACD histogram < 0 且下降 → 高分

2. **4H 增强得分 (5%)**:
   - 额外的 4H 趋势确认
   - EMA 排列、价格结构等

3. **1H 方向得分 (20%)**:
   - 1H MACD 和 EMA 结构
   - 辅助确认 4H 方向

4. **VWAP 得分 (20%)**:
   - 计算价格相对 VWAP 的偏离度
   - 偏离越小，得分越高
   - `min_vwap_score_for_entry: 0.15`

5. **成交量得分 (15%)**:
   - 放量突破得分更高
   - 确认价格变动的有效性

6. **15M 入场得分 (5%)**:
   - 15M MACD 和 K 线形态
   - 选择最佳入场时机

#### Step 3: 信号分检查

**全局最低信号分**:
```json
{
  "min_signal_score": 0.85  // 必须 >= 0.85
}
```

**不同信号类型的阈值**:
| 信号类型 | 最低分数 | 说明 |
|----------|---------|------|
| default | 0.85 | 默认信号 |
| red_bar_growing | 0.86 | 红柱增长 |
| flip_bullish | 0.84 | 翻转做多 |
| flip_bearish | 0.84 | 翻转做空 |
| stable_bear_continuation | 0.83 | 稳定空头延续 |

#### Step 4: 特殊信号过滤

**Flip Bullish 严格过滤**:
```json
{
  "enable_flip_bullish_strict_filter": true,
  "flip_bullish_min_vwap_score": 0.15,
  "flip_bullish_require_pullback_bounce": true,
  "flip_bullish_require_15m_growing": true,
  "flip_bullish_max_cvd_upper_wick_ratio": 0.2,
  "flip_bullish_min_cvd_1h_delta_ratio": 0.03
}
```

**Shrinking State 过滤**:
```json
{
  "disable_green_bar_growing_entries": true,
  "disable_red_bar_growing_long_entries": true,
  "disable_green_bar_shrinking_short_dual_pressure_entries": true,
  "disable_red_bar_shrinking_long_dual_support_entries": true
}
```

**Preflip Trial Entries**:
```json
{
  "enable_4h_preflip_trial_entries": true,
  "preflip_trial_min_shrink_pct_long": 0.6,
  "preflip_trial_min_shrink_pct_short": 0.3,
  "preflip_trial_min_signal_score": 0.75,
  "preflip_trial_min_vwap_score": 0.06,
  "preflip_trial_entry_scale": 0.35,
  "preflip_trial_max_leverage": 2
}
```

#### Step 5: L1 硬过滤

**基础结构过滤**:
```json
{
  "entry_hard_gates_enabled": true,
  "entry_hard_gate_adx_min": 30,
  "entry_hard_gate_atr_min": 0.0012,
  "entry_hard_gate_atr_max": 0.02,
  "entry_hard_gate_spread_bps_max": 0.0008,
  "entry_hard_gate_flow_min_pass": 2,
  "entry_hard_gate_micro_min_pass": 2
}
```

**BOLL 中轨硬阻挡**:
```json
{
  "boll_middle_hard_block": true  // 启用
}
```

**VWAP 偏离硬阻挡**:
```json
{
  "vwap_deviation_hard_block": 0.03  // 3% 偏离直接阻断
}
```

#### Step 6: 综合评分

**开仓阈值**:
```json
{
  "open_threshold": 0.38,        // 综合开仓阈值
  "long_open_threshold": 0.09,   // 做多阈值
  "short_open_threshold": 0.07   // 做空阈值
}
```

**评分计算**:
```python
open_score = signal_score × weight_factor × regime_factor × ...
if open_score < open_threshold:
    return REJECT
```

#### Step 7: 容量检查

**最大持仓限制**:
```json
{
  "max_active_symbols": 4  // 最多同时持有 4 个币种
}
```

**检查逻辑**:
```python
if len(active_positions) >= max_active_symbols:
    return REJECT  # 持仓已满
```

#### Step 8: Pretrade Risk Gate

**风控门控** (Live-Only):
```json
{
  "pretrade_risk_gate": {
    "enabled": true,
    "use_hard_rules_only": true,
    "atr_ratio_hard_block": 3.5,
    "equity_usage_block": 0.85,
    "entry_block_actions": ["BLOCK"]
  }
}
```

**检查项**:
1. ATR 比率是否超过 3.5 倍
2. 资金占用是否超过 85%
3. 其他硬规则检查

#### Step 9: 执行下单

**仓位计算**:
```python
target_portion = default_target_portion  # 50%
leverage = calculate_leverage(signal_score)  # 2x-4x
position_size = (capital × target_portion × leverage) / price
```

**订单提交**:
```python
# IOC 订单 (立即成交或取消)
order = submit_order(
    symbol=symbol,
    side=side,
    quantity=position_size,
    type="LIMIT",
    time_in_force="IOC",
    price=current_price
)

# 如果 IOC 失败，回退到 GTC
if order.status != "FILLED":
    order = submit_order(
        ...,
        time_in_force="GTC"  # 一直有效直到取消
    )
```

---

## ⚖️ 三、权重体系详解

### 3.1 信号评分权重

| 维度 | 权重 | 说明 |
|------|------|------|
| 4H 方向 | 35% | 主趋势判断 |
| 4H 增强 | 5% | 额外趋势确认 |
| 1H 方向 | 20% | 中期趋势确认 |
| VWAP | 20% | 价值评估 |
| 成交量 | 15% | 动能确认 |
| 15M 入场 | 5% | 时机选择 |
| **总计** | **100%** | - |

### 3.2 动态杠杆权重

```json
{
  "leverage_config": {
    "score_0.75_plus": 5,    // 信号分 >= 0.75: 5x 杠杆
    "score_0.60_plus": 4,    // 信号分 >= 0.60: 4x 杠杆
    "score_0.50_plus": 3,    // 信号分 >= 0.50: 3x 杠杆
    "below_threshold": 0     // 低于阈值：不开仓
  },
  "high_signal_leverage_cap": 3  // 实际杠杆上限 3x
}
```

**实际应用**:
- 由于 `high_signal_leverage_cap: 3`，实际杠杆不会超过 3x
- 这与 `default_leverage: 3` 保持一致

### 3.3 仓位调整权重

#### 双压/双支奖励
```json
{
  "dual_pressure_target_portion_bonus": 0.08,         // +8% 仓位
  "dual_pressure_max_symbol_position_portion": 0.68   // 单币种最大 68%
}
```

#### 会话级别仓位调整
```json
{
  "session_risk_control": {
    "high_risk_sessions": [
      {"utc_start": "03:00", "utc_end": "05:30", "position_scale": 0.6},
      {"utc_start": "14:30", "utc_end": "16:00", "position_scale": 0.55}
    ]
  }
}
```

#### VWAP 评分仓位分层
```json
{
  "vwap_score_position_tiers": {
    "tiers": [
      {"min": 0.12, "max": 0.2, "position_mult": 0.75},
      {"min": 0.2,  "max": 0.3, "position_mult": 0.95},
      {"min": 0.3,  "max": 1.0, "position_mult": 1.05}
    ]
  }
}
```

---

## 🛡️ 四、风控逻辑详解

### 4.1 止损体系

#### 基础止损
```json
{
  "stop_loss_default_percent": 0.02,  // 固定止损 2%
  "stop_loss_pct": 0.02,
  "use_dynamic_stop": true,
  "max_stop_loss_pct": 0.025,         // 最大止损 2.5%
  "boll_stop_atr_multiplier": 0.5     // BOLL 止损使用 0.5 倍 ATR
}
```

#### 动态止损
- 基于 BOLL 带宽和 ATR 动态计算
- 趋势市场中自动放宽止损
- 震荡市场中收紧止损

#### 4H 收缩退出
```json
{
  "enable_4h_shrink_exit": true,
  "exit_4h_shrink_bars": 2,
  "exit_4h_min_shrink_pct": 0.12,
  "exit_4h_require_profit": false
}
```

### 4.2 止盈体系

#### 基础止盈
```json
{
  "take_profit_default_percent": 0.02,  // 默认止盈 2%
  "take_profit_pct": 0.04               // 资金流策略止盈 4%
}
```

#### 保本止损
```json
{
  "breakeven_enabled": true,
  "breakeven_trigger_pnl_ratio": 0.006,  // 浮盈 0.6% 触发
  "breakeven_lock_ratio": 0.002          // 锁定 0.2% 利润
}
```

### 4.3 冲突保护机制

这是实盘配置的核心风控层，包含 30+ 参数：

```json
{
  "conflict_protection": {
    "light_confirm_bars": 2,
    "hard_confirm_bars": 6,
    "cooldown_sec": 180,
    "trend_light_tighten": true,
    "tighten_min_atr_multiple": 2.2,
    "ev_conflict_light_min": 0.14,
    "ev_conflict_hard_min": 0.4,
    "light_take_profit_enabled": true,
    "light_take_profit_min_hold_seconds": 300,
    "light_take_profit_min_mfe": 0.0035,
    "light_take_profit_pct": 0.55,
    "breakeven_fee_buffer": 0.0008,
    "state_circuit_cvd_norm": 0.92,
    "hard_exit_min_hold_seconds": 900,
    "hard_exit_trap_force": 0.94,
    // ... 更多参数
  }
}
```

### 4.4 账户级风控

```json
{
  "risk": {
    "account_circuit_enabled": true,
    "max_daily_loss_percent": 5,
    "max_consecutive_losses": 2,
    "daily_loss_cooldown_seconds": 28800,
    "consecutive_loss_cooldown_seconds": 2700
  }
}
```

---

## 📋 五、当前配置参数汇总

### 5.1 资金管理

| 参数 | 当前值 | 建议值 | 说明 |
|------|--------|--------|------|
| default_target_portion | 50% | 20-30% | 单笔目标仓位 |
| max_symbol_position_portion | 50% | 25-30% | 单币种最大仓位 |
| min_open_portion | 6% | 6% | 最小开仓比例 |
| max_open_portion | 100% | 100% | 最大开仓比例 |
| reserve_percent | 20% | 20% | 预留现金比例 |
| max_active_symbols | 4 | 3 | 最大持仓数 |

### 5.2 杠杆设置

| 参数 | 当前值 | 建议值 | 说明 |
|------|--------|--------|------|
| min_leverage | 2x | 2x | 最小杠杆 |
| default_leverage | 3x | 2x/3x/4x | 默认杠杆 |
| max_leverage | 4x | 4x | 最大杠杆 |
| high_signal_leverage_cap | 3x | 根据信号调整 | 高信号杠杆上限 |

### 5.3 开仓门槛

| 参数 | 当前值 | 建议值 | 说明 |
|------|--------|--------|------|
| open_threshold | 0.38 | 待优化 | 综合开仓阈值 |
| long_open_threshold | 0.09 | 待优化 | 做多阈值 |
| short_open_threshold | 0.07 | 待优化 | 做空阈值 |
| min_signal_score | 0.85 | 待优化 | 最低信号分 |
| min_entry_score | 0.25 | 0.25 | 最低入场评分 |
| close_threshold | 0.30 | 0.30 | 平仓阈值 |

### 5.4 止损止盈

| 参数 | 当前值 | 建议值 | 说明 |
|------|--------|--------|------|
| stop_loss_pct | 2% | 2% | 固定止损 |
| take_profit_pct | 4% | 4% | 固定止盈 |
| use_dynamic_stop | true | true | 动态止损 |
| max_stop_loss_pct | 2.5% | 2.5% | 最大止损 |
| breakeven_enabled | true | true | 保本止损 |

---

## 💡 六、门槛拆卸建议

根据您的要求：**仓位 20-30%，最大持仓 3 个，杠杆 2x/3x/4x**

### 6.1 需要修改的配置参数

```json
{
  "trading": {
    "default_target_portion": 0.25,        // 从 0.50 降至 0.25
    "max_symbol_position_portion": 0.30,   // 从 0.50 降至 0.30
    "max_active_symbols": 3,               // 从 4 降至 3
    "min_leverage": 2,                      // 保持 2x
    "default_leverage": 3,                  // 保持 3x (可调整为 2/3/4)
    "max_leverage": 4,                      // 保持 4x
    "high_signal_leverage_cap": 4           // 从 3 提升至 4 (允许高信号用 4x)
  },
  "fund_flow": {
    "open_threshold": 0.30,                // 从 0.38 降至 0.30 (降低门槛)
    "min_signal_score": 0.80,              // 从 0.85 降至 0.80
    "long_open_threshold": 0.07,           // 从 0.09 降至 0.07
    "short_open_threshold": 0.05           // 从 0.07 降至 0.05
  }
}
```

### 6.2 预期效果

| 指标 | 当前配置 | 调整后配置 | 变化 |
|------|---------|-----------|------|
| 单笔风险 | 50% × 3x × 2% = 3% | 25% × 3x × 2% = 1.5% | ↓ 50% |
| 最大持仓 | 4 个 | 3 个 | ↓ 25% |
| 开仓频率 | 低 (门槛高) | 中 (门槛适中) | ↑ 提升 |
| 日交易数 | 预计 0-2 笔 | 预计 3-5 笔 | ↑ 提升 |

### 6.3 风险评估

**当前配置风险**:
- 单笔风险：50% × 3x × 2% = **3% 账户风险** (过高)
- 满仓风险：4 个币种 × 3% = **12% 账户风险** (极高)

**调整后风险**:
- 单笔风险：25% × 3x × 2% = **1.5% 账户风险** (合理)
- 满仓风险：3 个币种 × 1.5% = **4.5% 账户风险** (可控)

---

## 🎯 七、下一步行动

### 7.1 立即执行

1. ✅ **备份当前配置**: `cp config/trading_config_fund_flow.json config/trading_config_fund_flow_backup_20260404.json`
2. ✅ **创建新配置**: 基于上述建议创建 `config/trading_config_fund_flow_optimized.json`
3. ✅ **小范围测试**: 使用新配置运行 24-48 小时观察

### 7.2 监控指标

- 开仓频率 (预期提升至 3-5 笔/天)
- 胜率 (目标 > 45%)
- 盈亏比 (目标 > 1.5:1)
- 最大回撤 (控制在 10% 以内)

### 7.3 验证实验

**实验 1: 仅调整仓位**
```json
{"default_target_portion": 0.25, "max_symbol_position_portion": 0.30}
```

**实验 2: 仅调整持仓数**
```json
{"max_active_symbols": 3}
```

**实验 3: 组合调整**
```json
{
  "default_target_portion": 0.25,
  "max_active_symbols": 3,
  "open_threshold": 0.30
}
```

---

**报告生成**: AI Assistant  
**审核人**: 待 Claude 评审  
**联系方式**: 通过 GitHub Issue 或邮件反馈
