# MACD V2 — 门槛消融 & 风控改造方案

**日期**: 2026-03-30  
**基础版本**: 15m gate 已取消，权重已调整为 4H(0.4) / VWAP(0.2) / 1H(0.2) / Volume(0.15) / 15m(0.05)  
**目标**: 提升信号覆盖率 + 实现"波动行情快速落袋、趋势行情移动止盈吃大波段"双模风控

---

## 目录

1. [门槛消融优先级地图](#1-门槛消融优先级地图)
2. [消融 A — 4H Preflip 缩量门槛](#2-消融-a--4h-preflip-缩量门槛)
3. [消融 B — VWAP Score Filter](#3-消融-b--vwap-score-filter)
4. [消融 C — threshold_check](#4-消融-c--threshold_check)
5. [消融 D — L1 ADX / ATR / Regime](#5-消融-d--l1-adx--atr--regime)
6. [消融 E — L3 微结构门槛](#6-消融-e--l3-微结构门槛)
7. [风控改造 — 双模止盈止损](#7-风控改造--双模止盈止损)
8. [消融回测迭代顺序](#8-消融回测迭代顺序)

---

## 1. 门槛消融优先级地图

```
当前仍在硬拦截的门槛（按信号损耗从大到小估算）：

┌─────────────────────────────────────────┬────────────┬──────────────┐
│ 门槛                                    │ 估算信号  │ 建议操作      │
│                                         │ 损耗       │              │
├─────────────────────────────────────────┼────────────┼──────────────┤
│ A. 4H preflip 缩量不足                  │ 高         │ 先消融       │
│ B. VWAP score filter (min 0.14)         │ 中         │ 同步消融     │
│ C. threshold_check (signal 0.85)        │ 中         │ 第二轮       │
│ D. L1 ADX≥22 / ATR range / regime=TREND│ 中低       │ 第三轮       │
│ E. L3 imbalance/depth/cvd_momentum 2/3  │ 低         │ 最后消融     │
│ F. pretrade_risk_gate (hard rules only) │ 极低       │ 不动         │
└─────────────────────────────────────────┴────────────┴──────────────┘

消融原则：每轮只动一个门槛，跑完回测再决定是否接受。
```

---

## 2. 消融 A — 4H Preflip 缩量门槛

### 现状

```yaml
# 当前配置
enable_4h_preflip_trial_entries: true
preflip_trial_min_shrink_pct_long:  0.60   # 多头方向：要求柱缩量 ≥ 60%
preflip_trial_min_shrink_pct_short: 0.30   # 空头方向：要求柱缩量 ≥ 30%
preflip_trial_min_signal_score:     0.75
preflip_trial_min_vwap_score:       0.06
preflip_trial_entry_scale:          0.35   # 预翻转仓位缩比到 35%
```

### 问题

`preflip_trial_min_shrink_pct_long = 0.60` 对多头要求极严，
大量 4H 刚开始收敛但尚未缩 60% 的入场点被漏掉。
空头 `0.30` 已相对宽松，但 `min_signal_score = 0.75` 在新权重下偏高。

### 消融建议

```diff
# fund_flow.macd_mtf_strategy_v2.entry_filters

- preflip_trial_min_shrink_pct_long:  0.60
+ preflip_trial_min_shrink_pct_long:  0.45   # 放宽多头缩量要求

- preflip_trial_min_shrink_pct_short: 0.30
+ preflip_trial_min_shrink_pct_short: 0.22   # 空头同步小幅放宽

- preflip_trial_min_signal_score:     0.75
+ preflip_trial_min_signal_score:     0.70   # 新权重下降低总分门槛

  preflip_trial_min_vwap_score:       0.06   # 暂不动
  preflip_trial_entry_scale:          0.35   # 暂不动（预翻转仓位保守不动）
```

### 伪代码（策略层逻辑）

```python
def check_4h_preflip_trial(bar_4h, signal_score, vwap_score, direction):
    """
    判断是否满足 4H 预翻转试探入场条件
    """
    # 旧逻辑
    # min_shrink = 0.60 if direction == LONG else 0.30
    
    # 新逻辑 — 消融后
    min_shrink = 0.45 if direction == LONG else 0.22

    bar_shrink_ratio = bar_4h.current_hist_abs / bar_4h.prev_hist_abs
    shrink_ok = bar_shrink_ratio <= (1 - min_shrink)   # 当前柱比上根柱缩了多少

    score_ok   = signal_score >= 0.70   # 旧: 0.75
    vwap_ok    = vwap_score   >= 0.06   # 不变

    return shrink_ok and score_ok and vwap_ok
```

### 预期效果

- 预翻转信号覆盖率预估提升 **+20~35%**
- 仓位仍然缩比 35%，风险可控
- 回测验收指标：win_rate 不低于当前 -1%，MDD 不超 +1%

---

## 3. 消融 B — VWAP Score Filter

### 现状

```yaml
# 当前配置
min_vwap_score_for_entry:              0.14
stable_bear_continuation_min_vwap_score: 0.10
preflip_trial_min_vwap_score:           0.06
```

### 问题

`min_vwap_score_for_entry = 0.14` 在 VWAP 权重已经是 0.20 的情况下，
等于要求 VWAP 分项至少满足 70% 才能开仓（0.14 / 0.20），门槛偏高。

### 消融建议

```diff
# fund_flow.macd_mtf_strategy_v2.entry_filters

- min_vwap_score_for_entry: 0.14
+ min_vwap_score_for_entry: 0.10   # 放宽到 50% VWAP 权重即可通过

- stable_bear_continuation_min_vwap_score: 0.10
+ stable_bear_continuation_min_vwap_score: 0.07   # 做空延续条件同步放宽

  preflip_trial_min_vwap_score: 0.06   # 已经较宽，暂不动
```

### 伪代码

```python
def check_vwap_entry_gate(vwap_score, signal_type):
    """
    VWAP 最低开仓门槛检查
    signal_type: "normal" | "stable_bear_continuation" | "preflip_trial"
    """
    thresholds = {
        "normal":                  0.10,   # 旧: 0.14
        "stable_bear_continuation": 0.07,  # 旧: 0.10
        "preflip_trial":            0.06,  # 不变
    }
    return vwap_score >= thresholds[signal_type]
```

### 预期效果

- 主要解除 VWAP 轻微偏离时的不必要拦截
- 配合新权重（VWAP 占 0.20），总分依然能区分好坏信号
- 注意：此消融与消融 A 可同轮进行，但需同轮回测验证

---

## 4. 消融 C — threshold_check

### 现状

```yaml
# 当前配置
entry_thresholds:
  default:                              0.850
  min_signal_score:                     0.850
  red_bar_growing:                      0.86
  flip_bearish:                         0.84
  flip_bullish:                         0.84
  stable_bear_continuation_min_signal_score: 0.82
  stable_bull_continuation_min_signal_score: 0.82
```

### 问题

权重改为 4H(0.4)/VWAP(0.2)/1H(0.2)/Volume(0.15)/15m(0.05) 后，
总分天花板在信号不完美时会系统性降低（1H weight 从 0 升到 0.2），
旧的 0.85 门槛相当于变相收紧了。需要重新校准基准线。

### 消融建议

```diff
# fund_flow.macd_mtf_strategy_v2.entry_thresholds

- default:           0.850
+ default:           0.830

- min_signal_score:  0.850
+ min_signal_score:  0.830

- red_bar_growing:   0.86
+ red_bar_growing:   0.845  # 高风险信号只小幅放宽

- flip_bearish:      0.84
+ flip_bearish:      0.825

- flip_bullish:      0.84
+ flip_bullish:      0.825

- stable_bear_continuation_min_signal_score: 0.82
+ stable_bear_continuation_min_signal_score: 0.800

- stable_bull_continuation_min_signal_score: 0.82
+ stable_bull_continuation_min_signal_score: 0.800
```

### 伪代码

```python
def get_signal_score_threshold(signal_type: str) -> float:
    """
    根据信号类型返回对应的 min_signal_score 门槛
    消融后版本：整体下调约 0.015~0.020
    """
    thresholds = {
        "red_bar_growing":              0.845,  # 旧: 0.86
        "flip_bearish":                 0.825,  # 旧: 0.84
        "flip_bullish":                 0.825,  # 旧: 0.84
        "stable_bear_continuation":     0.800,  # 旧: 0.82
        "stable_bull_continuation":     0.800,  # 旧: 0.82
        "default":                      0.830,  # 旧: 0.85
    }
    return thresholds.get(signal_type, thresholds["default"])
```

### 回测验证要点

```
验收条件（相比消融前）：
  ✓ trade_count 提升 ≥ 10%
  ✓ win_rate   下降 ≤ 1.5%
  ✓ profit_factor ≥ 1.3
  ✓ MDD 增加 ≤ 1.5%
  
如果 win_rate 下降 > 2%，退回到 default=0.840 重测。
```

---

## 5. 消融 D — L1 ADX / ATR / Regime

### 现状

```yaml
# L1 结构层（全部必须满足）
regime:     == TREND   # 必须
adx:        >= 22      # 必须
atr_pct:    0.006 ~ 0.020  # 必须在此区间
spread_bps: <= 0.0008  # 必须
```

### 问题

- `adx >= 22` 会漏掉趋势刚启动时 ADX 尚未拉升的早期入场
- `atr_pct <= 0.020` 会在波动率稍高时（0.021~0.025）全部拦截
- `regime == TREND` 是最强硬的门，但 TREND 判定本身有滞后

### 消融建议（分级放宽，保守起步）

```diff
# decision_engine 入口的 L1 gates

# --- ADX ---
- adx >= 22
+ adx >= 18   # 放宽：趋势启动早期可进

# --- ATR upper bound ---
- atr_pct <= 0.020
+ atr_pct <= 0.025  # 放宽：允许轻微高波动入场
                    # 注意：高 ATR 时配合仓位缩比（见风控章节）

# --- Regime ---
# 第一阶段：不动 regime gate，只放 ADX + ATR
# 第二阶段（若回测达标）：允许 TRANSITION 状态下的预翻转信号
# 伪代码如下：

def check_regime_gate(regime, signal_type):
    if signal_type == "preflip_trial":
        # 预翻转信号允许 TRANSITION 状态
        return regime in ("TREND", "TRANSITION")
    else:
        return regime == "TREND"   # 其他信号仍要求完整 TREND
```

### 完整 L1 伪代码（消融后）

```python
def check_l1_structural_gate(bar, signal_type) -> tuple[bool, str]:
    """
    L1 结构层门槛检查 — 消融版本
    返回 (passed: bool, reject_reason: str)
    """
    # Regime check
    regime_ok = check_regime_gate(bar.regime, signal_type)
    if not regime_ok:
        return False, f"L1_REGIME: {bar.regime} not allowed for {signal_type}"

    # ADX check — 放宽到 18
    adx_min = 18   # 旧: 22
    if bar.adx < adx_min:
        return False, f"L1_ADX: {bar.adx:.1f} < {adx_min}"

    # ATR range check — 上限放宽到 0.025
    atr_lo, atr_hi = 0.006, 0.025   # 旧上限: 0.020
    if not (atr_lo <= bar.atr_pct <= atr_hi):
        return False, f"L1_ATR: {bar.atr_pct:.4f} out of [{atr_lo},{atr_hi}]"

    # Spread check — 不动
    if bar.spread_bps > 0.0008:
        return False, f"L1_SPREAD: {bar.spread_bps:.5f} > 0.0008"

    return True, "L1_OK"
```

---

## 6. 消融 E — L3 微结构门槛

### 现状

```yaml
# L3 微结构层：2/3 通过
checks: [depth_ok, imbalance_ok, cvd_momentum_ok]
require: 2 of 3
```

### 消融建议

```diff
# 第一步：维持 2/3，但对 preflip_trial 信号特殊处理
# 第二步（若回测达标）：对主趋势延续信号降到 1/3

def check_l3_gate(checks: dict, signal_type: str) -> bool:
    """
    L3 微结构层门槛 — 消融版本
    """
    passed = sum([
        checks["depth_ok"],
        checks["imbalance_ok"],
        checks["cvd_momentum_ok"]
    ])

    # preflip_trial 和 continuation 信号要求低于主翻转信号
    if signal_type in ("preflip_trial", "stable_bear_continuation"):
-       min_pass = 2
+       min_pass = 1   # 消融：微结构不完美时仍允许低仓位进场
    else:
        min_pass = 2   # 主翻转信号：保持 2/3

    return passed >= min_pass
```

> ⚠️ L3 消融风险最小，但预期收益也最小。建议最后操作。

---

## 7. 风控改造 — 双模止盈止损

### 7.1 设计目标

| 行情类型 | 特征                         | 目标行为                           |
|---------|-----------------------------|------------------------------------|
| 波动行情  | ATR 高、ADX 中低、价格反复    | 快速落袋浮盈，收紧 trailing stop   |
| 趋势行情  | ADX 高、4H 方向明确、连续 K 线 | 保留头寸，宽 trailing stop 吃大波段 |

### 7.2 行情模式识别（新增辅助函数）

```python
def classify_market_regime(bar) -> str:
    """
    基于 ATR 和 ADX 判断当前行情模式
    返回: "VOLATILE" | "TRENDING"
    """
    # 趋势行情条件：ADX 强 + ATR 不极端
    if bar.adx >= 32 and bar.atr_pct <= 0.018:
        return "TRENDING"
    
    # 波动行情条件：ATR 偏高 或 ADX 中等
    if bar.atr_pct >= 0.016 or bar.adx < 28:
        return "VOLATILE"
    
    # 中间态默认走波动处理（保守）
    return "VOLATILE"
```

### 7.3 双模 Partial TP 配置

```diff
# 当前配置（单一模式）
- partial_tp_levels[0]: 30% @ 1R
- partial_tp_levels[1]: 40% @ 2R

# 新配置（双模切换）
+ partial_tp_mode: "dynamic"   # 新增字段，取代固定 levels

+ partial_tp_volatile:
+   level_0: {ratio: 0.45, at_r: 0.7}   # 波动行情：更早锁 45% @ 0.7R
+   level_1: {ratio: 0.30, at_r: 1.5}   # 波动行情：剩 25% 跑到 1.5R
+
+ partial_tp_trending:
+   level_0: {ratio: 0.15, at_r: 1.5}   # 趋势行情：轻仓位锁利润
+   level_1: {ratio: 0.20, at_r: 3.0}   # 趋势行情：保留大头吃长线
```

### 7.4 双模 Trailing Stop 配置

```diff
# 当前配置（单一模式）
- trailing_stop_activation_pct:   0.012
- trailing_stop_atr_multiplier:   1.0
- trailing_stop_min_distance:     0.007
- trailing_stop_max_distance:     0.015
- breakeven_trigger_pnl_ratio:    0.008
- breakeven_lock_ratio:           0.0025

# 新配置（双模）
+ trailing_stop_mode: "dynamic"   # 新增字段

# 波动行情 — 收紧止盈，快速保护浮盈
+ trailing_volatile:
+   activation_pct:     0.008    # 旧: 0.012，更早激活
+   atr_multiplier:     0.6      # 旧: 1.0，更紧的跟踪距离
+   min_distance:       0.005    # 旧: 0.007
+   max_distance:       0.010    # 旧: 0.015
+   breakeven_trigger:  0.005    # 旧: 0.008，更早移保本
+   breakeven_lock:     0.002    # 旧: 0.0025

# 趋势行情 — 宽松止盈，让利润奔跑
+ trailing_trending:
+   activation_pct:     0.018    # 旧: 0.012，更晚激活（不被小幅回调扫出）
+   atr_multiplier:     1.8      # 旧: 1.0，宽跟踪距离
+   min_distance:       0.012    # 旧: 0.007
+   max_distance:       0.030    # 旧: 0.015，允许更大回撤空间
+   breakeven_trigger:  0.012    # 旧: 0.008，需要更确定的利润再保本
+   breakeven_lock:     0.003    # 旧: 0.0025
```

### 7.5 双模风控伪代码（完整实现）

```python
class DualModeRiskManager:
    """
    波动/趋势双模止盈止损管理器
    集成到 fund_flow_bot.py 的持仓管理循环中
    """

    def __init__(self, config):
        self.volatile_cfg  = config.trailing_volatile
        self.trending_cfg  = config.trailing_trending
        self.vol_tp_levels = config.partial_tp_volatile
        self.trend_tp_levels = config.partial_tp_trending

    def get_active_config(self, position, bar):
        """
        根据当前行情状态返回对应风控参数
        可选：行情模式变化时平滑切换（不突然改止损价）
        """
        mode = classify_market_regime(bar)
        
        # 如果已有持仓，只在模式发生持续变化时切换（防止频繁抖动）
        if position.market_mode is not None:
            if mode != position.market_mode:
                position.mode_change_bars = getattr(position, "mode_change_bars", 0) + 1
                if position.mode_change_bars >= 2:   # 连续 2 根 bar 确认切换
                    position.market_mode = mode
                    position.mode_change_bars = 0
            else:
                position.mode_change_bars = 0
        else:
            position.market_mode = mode

        cfg = self.volatile_cfg if position.market_mode == "VOLATILE" else self.trending_cfg
        tp_levels = self.vol_tp_levels if position.market_mode == "VOLATILE" else self.trend_tp_levels
        return cfg, tp_levels, position.market_mode

    def update_trailing_stop(self, position, bar):
        """
        更新移动止损价格
        """
        cfg, _, mode = self.get_active_config(position, bar)
        
        pnl_ratio = position.unrealized_pnl / position.initial_risk   # 用 R 为单位

        # 激活检查
        if pnl_ratio < cfg.activation_pct / position.entry_atr_pct:
            return   # 还未到激活阈值

        # 计算跟踪距离 = ATR × multiplier，并限制在 [min, max] 区间
        trail_dist = bar.atr * cfg.atr_multiplier
        trail_dist = max(cfg.min_distance * bar.close,
                        min(cfg.max_distance * bar.close, trail_dist))

        # 更新止损价
        if position.side == "LONG":
            new_stop = bar.high - trail_dist
            position.trailing_stop = max(position.trailing_stop or 0, new_stop)
        else:
            new_stop = bar.low + trail_dist
            position.trailing_stop = min(position.trailing_stop or float("inf"), new_stop)

        # 保本检查
        if pnl_ratio >= cfg.breakeven_trigger:
            breakeven_price = position.entry_price + (
                position.entry_price * cfg.breakeven_lock * position.direction
            )
            if position.side == "LONG":
                position.trailing_stop = max(position.trailing_stop, breakeven_price)
            else:
                position.trailing_stop = min(position.trailing_stop, breakeven_price)

    def check_partial_tp(self, position, bar) -> list[dict]:
        """
        检查是否触达部分止盈价格
        返回需要执行的平仓动作列表
        """
        _, tp_levels, mode = self.get_active_config(position, bar)
        actions = []

        for i, level in enumerate(tp_levels):
            if position.tp_taken[i]:
                continue   # 已执行
            
            r_target = level["at_r"]
            target_pnl = position.initial_risk * r_target

            if position.unrealized_pnl >= target_pnl:
                close_ratio = level["ratio"]
                actions.append({
                    "action":       "partial_close",
                    "ratio":        close_ratio,
                    "reason":       f"PartialTP_L{i}_{mode}",
                    "level_index":  i,
                })
                position.tp_taken[i] = True

        return actions
```

### 7.6 ATR 高时自动缩仓（与双模配合）

```diff
# 在 pretrade_risk_gate 或开仓前加入 ATR-based position scaling

+ def get_atr_position_scale(atr_pct: float) -> float:
+     """
+     ATR 偏高时自动缩减仓位，降低波动行情暴露
+     """
+     if atr_pct <= 0.014:
+         return 1.0     # 正常仓位
+     elif atr_pct <= 0.018:
+         return 0.85    # 轻度缩仓
+     elif atr_pct <= 0.022:
+         return 0.70    # 中度缩仓
+     elif atr_pct <= 0.025:
+         return 0.55    # 重度缩仓（L1 放宽到 0.025 后的保护）
+     else:
+         return 0.0     # 超出范围，不开仓（已被 L1 拦截）

# 在开仓计算时调用：
+ atr_scale = get_atr_position_scale(bar.atr_pct)
+ actual_position_size = target_portion * atr_scale
```

---

## 8. 消融回测迭代顺序

```
迭代 #0（基线）
  目的：记录当前权重调整后的基准数据
  变更：仅更新权重（已完成）
  记录：total_return, win_rate, trades, mdd, profit_factor

迭代 #1（消融 A + B）
  变更：preflip_trial_min_shrink 放宽 + min_vwap_score 放宽
  验收：
    ✓ trade_count +15% 以上
    ✓ win_rate 下降 ≤ 1.5%
    ✓ MDD 增加 ≤ 1.5%

迭代 #2（双模风控）
  变更：引入 DualModeRiskManager
  验收：
    ✓ avg_win / avg_loss 比值提升
    ✓ 趋势行情中 avg_hold_bars 延长
    ✓ 波动行情中 max_adverse_excursion 降低
  注意：这轮变更与信号门槛无关，可并行测试

迭代 #3（消融 C）
  变更：threshold_check 整体下调约 0.02
  验收：trade_count 再增 ≥ 10%，win_rate 下降 ≤ 1.5%

迭代 #4（消融 D）
  变更：L1 ADX 放宽到 18，ATR 上限到 0.025（配合 ATR 缩仓）
  验收：MDD 严格不超 +2%

迭代 #5（消融 E，可选）
  变更：L3 preflip/continuation 信号降到 1/3
  验收：与 #4 合并评估，主要看 win_rate 是否守住

──────────────────────────────────────────────────
决策树：
  win_rate 下降 > 2%？  → 回滚该迭代，找具体拦截原因
  MDD > 15%？          → 回滚，或收紧 ATR 缩仓比例
  trade_count 下降？    → 检查是否有新的隐式 gate 被触发
──────────────────────────────────────────────────
```

---

## 附录：关键参数变更速查表

| 参数 | 旧值 | 新值 | 所属迭代 |
|------|------|------|----------|
| `preflip_trial_min_shrink_pct_long` | 0.60 | 0.45 | #1 |
| `preflip_trial_min_shrink_pct_short` | 0.30 | 0.22 | #1 |
| `preflip_trial_min_signal_score` | 0.75 | 0.70 | #1 |
| `min_vwap_score_for_entry` | 0.14 | 0.10 | #1 |
| `stable_bear_continuation_min_vwap_score` | 0.10 | 0.07 | #1 |
| `trailing_stop_activation_pct` (volatile) | 0.012 | 0.008 | #2 |
| `trailing_stop_atr_multiplier` (volatile) | 1.0 | 0.6 | #2 |
| `trailing_stop_activation_pct` (trending) | 0.012 | 0.018 | #2 |
| `trailing_stop_atr_multiplier` (trending) | 1.0 | 1.8 | #2 |
| `trailing_stop_max_distance` (trending) | 0.015 | 0.030 | #2 |
| `partial_tp_levels[0]` (volatile) | 30%@1R | 45%@0.7R | #2 |
| `partial_tp_levels[0]` (trending) | 30%@1R | 15%@1.5R | #2 |
| `default` signal threshold | 0.850 | 0.830 | #3 |
| `red_bar_growing` threshold | 0.860 | 0.845 | #3 |
| `flip_bearish/bullish` threshold | 0.840 | 0.825 | #3 |
| `adx` minimum | 22 | 18 | #4 |
| `atr_pct` upper bound | 0.020 | 0.025 | #4 |
| L3 gate (preflip/continuation) | 2/3 | 1/3 | #5 |
