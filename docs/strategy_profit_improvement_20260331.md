# MACD V2 — 提升盈利深度方案

**日期**: 2026-03-31  
**当前状态**: 264 笔/月 / 胜率 77.27% / MDD 3.00% / return +22.19%  
**本轮目标**: 开仓量不变，胜率守住，提升单位胜利的留存利润  
**核心诊断**: 频率和胜率已达标，问题是"赢的太薄，输的太深"

---

## 零、一句话定性

```
当前策略的期望值结构：

  204 笔盈利  × 平均 +22.35   =  +4,559
   60 笔亏损  × 平均 -33.90   =  -2,034
  净利润                          +2,525  （约 +22% on 10k）

问题所在：

  平均盈利 / 平均亏损 = 22.35 / 33.90 = 0.66  ← 低于 1.0

  77% 的胜率在对抗 0.66 的赔率比，勉强维持正期望。
  如果赔率比能提升到 1.0，在胜率不变的情况下：
    期望值 = 0.7727 × 22.35 - 0.2273 × 22.35 = +12.27/笔
    vs 当前  = 0.7727 × 22.35 - 0.2273 × 33.90 = +9.57/笔
    提升约 28%，收益从 +22% 升至约 +28%

  如果赔率比能到 1.2（avg_win = avg_loss × 1.2）：
    收益升至约 +32%

  因此：提升利润的核心是"让盈利更厚"或"让亏损更浅"，而不是提高胜率。
```

---

## 一、回答 5 个关键问题

### Q1：red_bar_growing + long_dual_support 是否是主亏损口袋？

**结论：是，且是结构性问题，不是偶然噪声。**

```
数据证据：
  91 笔，胜率 73.63%，但总 PnL = -67.25

  出场结构的矛盾：
    stop_loss_intrabar: 81 笔，平均 PnL ≈ -498.62 / 81 = -6.16/笔
    take_profit_intrabar: 7 笔，贡献 +561.72 → 平均 +80.25/笔

    7 笔大 TP 撑起了整个通道的正 PnL 希望，
    但 81 笔止损单（含 74% 的小盈利止损）把利润全部侵蚀

  结构性原因：
    long_dual_support = 价格在双重支撑附近做多
    这是典型的"支撑位反弹"逻辑 → 行情特征是"窄幅震荡/小浮盈"
    不是"趋势延续"逻辑 → 不容易走出大 R 比率的单子

    4% TP 设计是为趋势单设计的，放在支撑反弹单上不匹配
    → 支撑反弹单的最优退出应该是"快速落袋 + 早止损"
```

---

### Q2：优先收紧止损、更早保本，还是提高门槛/降低仓位？

**结论：三者都要，但顺序是：更早保本 > 收紧止损 > 降低仓位。**

```
分析：

  更早保本（breakeven 提前触发）：
    ✓ 直接把"小浮盈后回落至亏损"的单子转化为保本
    ✓ 不影响胜率（止损前已保本 = 不算亏损）
    ✓ 不影响 TP 单的利润（TP 单不会触发 breakeven 止损）
    效果：最大，风险最小

  收紧初始止损（0.02 → 0.012）：
    ✓ 减少每笔亏损的绝对金额（-33.90 → 约 -20）
    ～ 可能轻微降低胜率（更容易被扫）
    ✓ 对 long_dual_support 这类震荡通道效果好
    效果：次之，需要验证不会大幅降胜率

  提高门槛 / 降低仓位（针对该通道）：
    ✓ 直接减少低质量信号数量
    ～ 开仓量会下降（不符合"保持开仓量不变"的目标）
    建议：只对 long_dual_support 降低 position scale，不是全通道

  推荐组合：
    Step 1: 更早 breakeven（最小副作用）
    Step 2: 收紧 long_dual_support 的初始止损（针对性）
    Step 3: long_dual_support 仓位缩比（保量但减风险）
```

---

### Q3：4% TP 是否应该保留，同时叠加主动 partial TP？

**结论：是，这是当前最确定性的收益提升手段。**

```
数据告诉我们：
  25 笔 4% TP 单   → +3135.35   → 平均 +125.4/笔  ← 真正的利润引擎
  218 笔 stop 单   → -653.53    → 平均 -3.00/笔   ← 拖累
  
  也就是说：
  169 笔"盈利止损单"（赢了但止损出场）的平均盈利只有 +5.34/笔
  如果能把这 169 笔的平均利润提升到 +8~10，
  总利润提升约 (8-5.34) × 169 ≈ +450 USDT → 收益从 +22% 升到约 +27%

问题根因：
  当前 partial TP 设置（如果有的话）不够早
  大量单子走到 +0.5%~+1.2% 的浮盈区间，然后回落到止损价
  → 没有在浮盈区"锁住一部分"

解决方案（见第三章详细方案）：
  增加 Level 0 partial TP：+0.8%~+1.0% 时落袋 20%~30%
  保留 4% 远端 TP 给剩余仓位
  这样即使回落，也锁住了一部分浮盈
```

---

### Q4：flip_bullish 胜率 72.73% 但 PnL 仅 +10.07，是否应该降权/提纯？

**结论：不应该降权，而应该提纯到只保留高收益子条件。**

```
flip_bullish 的问题不是"方向错了"，而是"没有找到好的退出结构"

分析：22 笔，胜 16 负 6，总 PnL +10.07
  → 16 笔盈利的平均盈利 ≈ +1.26/笔  ← 极低
  → 6  笔亏损的平均亏损 ≈ -9.9/笔

这是典型的"赢小亏大"结构，但整体期望因为高胜率勉强为正。

flip_bullish 的两个子类（根据之前数据）：
  flip_bullish trial      → 当前 score window [0.80, 0.87]，仓位缩 35%
  flip_bullish full-size  → 4H 已确认翻多，正常仓位

  建议：
    flip_bullish trial → 维持当前逻辑（量少、轻仓、接受薄利润）
    flip_bullish full-size → 提高门槛，确保是真正的翻转而不是误判
      flip_bullish_min_signal_score: 0.84 → 0.86
      flip_bullish_require_pullback_bounce: true（已有）
      flip_bullish_min_cvd_1h_delta_ratio: 0.03 → 0.05（更严的买盘确认）
```

---

### Q5：SOLUSDT/BCHUSDT/ICPUSDT/FILUSDT 是否进入 symbol-specific 风控名单？

**结论：是，但处理方式不同。**

```
亏损原因分类：

  SOLUSDT (-140.29)：
    SOL 波动率高，止损容易被大 wick 扫到
    → 建议：收紧 initial SL（×0.8）+ 提高 min_signal_score 到 0.87
    → 不建议禁用（流动性好，好行情时是核心标的）

  BCHUSDT (-134.94)：
    Round 1 就出现的亏损口袋，在 flip_bullish 上表现差
    → 建议：禁用 flip_bullish trial（已有类似逻辑），保留 continuation
    → 对 BCH 的 long_dual_support 也额外收紧

  ICPUSDT (-95.75)：
    ICP 属于高波动、低流动性的中低市值标的
    → 建议：整体提高 min_signal_score 到 0.88，或暂时移出 symbol pool

  FILUSDT (-79.05)：
    FIL 属于存储板块，行情波动与大市相关性低，容易出现假趋势
    → 建议：禁用 long_dual_support 通道，保留 green_bar_growing short

汇总处理：
  高优先级禁用/限制：ICPUSDT（整体限制）
  中优先级提高门槛：SOLUSDT、BCHUSDT
  低优先级针对子通道：FILUSDT
```

---

## 二、核心改造：Partial TP 主动落袋体系

**这是当前最确定性的收益改善手段。**

### 2.1 现状分析

```
当前止盈结构（推测）：
  固定 TP: 4%（take_profit_pct = 0.04）
  止损:     2%（stop_loss_pct = 0.02，动态模式下由 boll 决定）
  保本:     浮盈 ≥ 0.8% 时移保本（breakeven_trigger_pnl_ratio = 0.008）

问题：
  0.8% 的保本触发，等于需要价格走 0.8% 才会保本
  但很多单子走了 0.5%~0.7% 就回撤，根本没触发保本
  → 这些单子最终以 -1%~-2% 的止损出场，但浮盈曾经是正的
```

### 2.2 三档 Partial TP 方案

```diff
# fund_flow 止盈配置

+ partial_tp_enabled: true
+ partial_tp_mode: "tiered"

+ partial_tp_levels:
+
+   # Level 0：极早落袋（针对震荡通道）
+   - trigger_pnl_ratio: 0.008    # 浮盈 ≥ 0.8%
+     close_ratio:       0.25     # 平掉 25% 仓位
+     only_for_vwap_types:        # 仅对震荡结构触发
+       - long_dual_support
+       - short_dual_pressure
+
+   # Level 1：常规落袋（所有信号类型）
+   - trigger_pnl_ratio: 0.012    # 浮盈 ≥ 1.2%（约 0.6R）
+     close_ratio:       0.30     # 平掉 30% 仓位
+
+   # Level 2：趋势单保留模式
+   - trigger_pnl_ratio: 0.020    # 浮盈 ≥ 2.0%（1R）
+     close_ratio:       0.20     # 再平掉 20%
+
+   # 剩余 50%（Level 1+2 后）：继续跑远端 4% TP 或 trailing stop
```

### 2.3 伪代码实现

```python
@dataclass
class PartialTPLevel:
    trigger_pnl_ratio: float    # 触发浮盈比例
    close_ratio: float          # 平仓比例
    only_for_vwap_types: list   # 为空则所有类型都触发

class TieredPartialTPManager:
    """
    三档主动落袋管理器
    核心逻辑：不同浮盈深度，分批锁利润，剩余仓位继续跑趋势
    """

    def __init__(self, cfg):
        self.levels = [PartialTPLevel(**l) for l in cfg.partial_tp_levels]

    def check_and_execute(self, position, current_price, bar) -> list[dict]:
        """
        检查是否触达各级部分止盈，返回需要执行的平仓动作列表
        """
        actions = []
        pnl_ratio = position.get_pnl_ratio(current_price)   # 当前浮盈比例

        for i, level in enumerate(self.levels):
            # 已经执行过这一档
            if position.partial_tp_taken.get(i, False):
                continue

            # vwap_type 过滤：如果设置了 only_for_vwap_types，检查信号类型
            if level.only_for_vwap_types:
                if position.vwap_structure_type not in level.only_for_vwap_types:
                    continue

            # 触达浮盈阈值
            if pnl_ratio >= level.trigger_pnl_ratio:
                # 计算实际平仓数量
                qty_to_close = position.remaining_qty * level.close_ratio

                actions.append({
                    "action":       "partial_close",
                    "qty":          qty_to_close,
                    "reason":       f"PartialTP_L{i}_{pnl_ratio:.3f}",
                    "level_index":  i,
                    "pnl_locked":   qty_to_close * position.entry_price * pnl_ratio,
                })
                position.partial_tp_taken[i] = True

        return actions

    def get_effective_breakeven_after_partial(self, position) -> float:
        """
        执行过 partial TP 后，自动把止损移到更高位置
        因为已经锁了部分利润，持仓的账面成本已经降低
        """
        locked_profit = position.get_total_locked_partial_profit()
        adjusted_cost = position.entry_price - (locked_profit / position.initial_qty)

        # 新的保本价 = 调整后的成本价 + 微薄保护
        if position.side == "LONG":
            return adjusted_cost * (1 + 0.001)   # 略高于调整后成本
        else:
            return adjusted_cost * (1 - 0.001)
```

---

## 三、核心改造：red_bar_growing + long_dual_support 专项修复

### 3.1 诊断

```
这个通道的失效模式：

  入场条件：4H 出现红柱（下跌），但在 VWAP 支撑 + 第二重支撑附近
  逻辑：双重支撑 → 反弹做多
  失效场景：支撑被假反弹后再次破位下跌

  当前止损结构不匹配：
    2% 的初始止损对"支撑反弹"来说太宽，支撑一旦破位就是深跌
    4% 的 TP 对"支撑反弹"来说也太远，这类信号大多只反弹 1-2%

  正确的结构应该是：
    初始止损：更紧（1.0~1.2%）
    目标止盈：更近（1.5~2.0%）或采用快速 partial TP
```

### 3.2 针对性配置改动

```diff
# 方案一：为 long_dual_support 设置独立的止损参数

# 在 signal_type 或 vwap_structure 层面的参数覆盖
+ vwap_structure_overrides:
+   long_dual_support:
+     stop_loss_pct_override:            0.012   # 旧: 0.020，收紧 40%
+     breakeven_trigger_pnl_ratio_override: 0.005  # 旧: 0.008，更早保本
+     breakeven_lock_ratio_override:     0.003   # 旧: 0.0025，锁更多
+     position_scale_override:           0.80    # 仓位缩比 80%（保量不保险）
+     partial_tp_early:                  true    # 强制走 Level 0 极早落袋
```

### 3.3 伪代码

```python
def get_vwap_structure_risk_params(vwap_structure_type: str, base_cfg, overrides_cfg) -> dict:
    """
    根据 VWAP 结构类型，返回覆盖后的风险参数
    """
    override = overrides_cfg.vwap_structure_overrides.get(vwap_structure_type, {})

    return {
        "stop_loss_pct": override.get("stop_loss_pct_override",
                                       base_cfg.stop_loss_pct),
        "breakeven_trigger": override.get("breakeven_trigger_pnl_ratio_override",
                                           base_cfg.breakeven_trigger_pnl_ratio),
        "breakeven_lock": override.get("breakeven_lock_ratio_override",
                                        base_cfg.breakeven_lock_ratio),
        "position_scale": override.get("position_scale_override", 1.0),
        "partial_tp_early": override.get("partial_tp_early", False),
    }

def calculate_position_size(signal, bar, cfg, overrides_cfg) -> float:
    """
    计算实际仓位大小，应用 vwap_structure 的缩比覆盖
    """
    base_portion = cfg.default_target_portion
    risk_params = get_vwap_structure_risk_params(
        signal.vwap_structure_type, cfg, overrides_cfg
    )
    scale = risk_params["position_scale"]
    return base_portion * scale   # long_dual_support → 0.18 × 0.80 = 0.144
```

---

## 四、核心改造：Trailing Stop 自适应增强

### 4.1 当前问题

```
当前配置（推测）：
  trailing_stop_activation_pct:   0.012   # 浮盈 1.2% 才激活
  trailing_stop_atr_multiplier:   0.8     # 上轮改动后的值
  trailing_stop_min_distance:     0.007
  trailing_stop_max_distance:     0.015

问题：
  激活条件 1.2% 对于"只走 0.8-1.5% 的单子"来说太迟
  很多单子浮盈到 1.0%，然后回落，trailing 还没激活就止损出场
```

### 4.2 按 VWAP 结构分类的 trailing 参数

```diff
# 两套 trailing 参数，按 VWAP 结构类型动态切换

+ trailing_stop_profiles:
+
+   # 震荡/反弹结构：早激活，紧跟踪
+   oscillation:                          # 用于 long_dual_support, short_dual_pressure
+     activation_pnl_ratio:  0.007        # 浮盈 0.7% 即激活（比当前更早）
+     atr_multiplier:        0.5          # 紧跟踪
+     min_distance_pct:      0.004
+     max_distance_pct:      0.008
+
+   # 趋势/翻转结构：晚激活，宽跟踪
+   trending:                             # 用于 green_bar_growing short_dual_pressure
+                                         # long_reclaim_confirmed 等
+     activation_pnl_ratio:  0.015        # 浮盈 1.5% 才激活
+     atr_multiplier:        1.2          # 宽跟踪，让趋势跑
+     min_distance_pct:      0.010
+     max_distance_pct:      0.025
```

### 4.3 伪代码

```python
TRAILING_PROFILE_MAP = {
    "long_dual_support":           "oscillation",
    "short_dual_pressure":         "oscillation",
    "long_reclaim_confirmed":      "trending",
    "short_retest_reject":         "trending",
    "green_bar_growing":           "trending",   # 默认按信号类型
    "red_bar_growing":             "trending",
    "flip_bullish":                "trending",
}

def get_trailing_profile(signal, cfg) -> dict:
    """
    根据信号的 VWAP 结构类型或信号类型，选择 trailing 参数集合
    """
    # 优先按 vwap_structure_type 匹配
    profile_key = TRAILING_PROFILE_MAP.get(
        signal.vwap_structure_type,
        TRAILING_PROFILE_MAP.get(signal.signal_type, "trending")
    )
    return cfg.trailing_stop_profiles[profile_key]

def update_trailing_stop(position, current_bar, cfg) -> None:
    """
    更新移动止损价格（自适应版本）
    """
    profile = get_trailing_profile(position.signal, cfg)
    pnl_ratio = position.get_pnl_ratio(current_bar.close)

    # 激活检查
    if pnl_ratio < profile["activation_pnl_ratio"]:
        return

    # 跟踪距离 = ATR × multiplier，限制在 [min, max]
    trail_dist = current_bar.atr * profile["atr_multiplier"]
    trail_dist = max(
        profile["min_distance_pct"] * current_bar.close,
        min(profile["max_distance_pct"] * current_bar.close, trail_dist)
    )

    if position.side == "LONG":
        new_stop = current_bar.high - trail_dist
        if position.trailing_stop is None or new_stop > position.trailing_stop:
            position.trailing_stop = new_stop
    else:
        new_stop = current_bar.low + trail_dist
        if position.trailing_stop is None or new_stop < position.trailing_stop:
            position.trailing_stop = new_stop
```

---

## 五、Symbol 级风控改造

### 5.1 四个亏损 symbol 的处置方案

```diff
# fund_flow.symbol_overrides 新增

+ SOLUSDT:
+   min_signal_score_override:             0.87   # 高于全局，要求更强信号
+   stop_loss_pct_override:                0.015  # 收紧（SOL 日内波幅大）
+   breakeven_trigger_pnl_ratio_override:  0.007  # 更早保本

+ BCHUSDT:
+   disable_flip_bullish_trial: true               # 已有，保留
+   disable_long_dual_support: true                # 新增：禁用 BCH 的双支撑做多
+   min_signal_score_override: 0.87

+ ICPUSDT:
+   min_signal_score_override: 0.88               # 高门槛（高波动中低流动性）
+   max_position_scale_override: 0.60             # 仓位最多 60%，降低单笔风险
+   disable_long_dual_support: true               # 禁用震荡通道

+ FILUSDT:
+   disable_long_dual_support: true               # 禁用 FIL 的双支撑做多
+   disable_flip_bullish: true                    # FIL flip_bullish 历史表现差
```

### 5.2 Symbol 滚动质量检查（自动化）

```python
def auto_apply_symbol_risk_override(
    symbol: str,
    rolling_trades: list,
    cfg,
    window: int = 15
) -> dict:
    """
    基于近期成交质量，自动生成 symbol-level 风控覆盖
    可作为 static overrides 的动态补充
    """
    recent = [t for t in rolling_trades
              if t.symbol == symbol][-window:]

    if len(recent) < 5:
        return {}   # 数据不足，不干预

    win_rate = sum(1 for t in recent if t.pnl > 0) / len(recent)
    avg_pnl  = sum(t.pnl for t in recent) / len(recent)

    # 触发规则
    if win_rate < 0.45 or avg_pnl < -15:
        # 严重亏损：暂停该 symbol 2 小时
        return {
            "suspend_until": time.time() + 7200,
            "reason": f"rolling_wr={win_rate:.2%}, avg_pnl={avg_pnl:.2f}"
        }
    elif win_rate < 0.55 or avg_pnl < -8:
        # 一般亏损：降低仓位
        return {
            "position_scale_override": 0.60,
            "reason": f"rolling_wr={win_rate:.2%}"
        }

    return {}
```

---

## 六、breakeven 提前触发优化

### 6.1 分通道的 breakeven 参数

```
当前问题：
  breakeven_trigger_pnl_ratio = 0.008（浮盈 0.8% 时移保本）
  对于 long_dual_support 这类最大浮盈只有 1.0~1.5% 的单子，0.8% 太迟

目标：
  支撑反弹类信号：0.5% 触发保本
  趋势延续类信号：0.8% 触发保本（不变）
  翻转信号：0.8%~1.0%（需要更大缓冲）
```

```diff
+ breakeven_profiles:
+   oscillation:   # long_dual_support, short_dual_pressure
+     trigger_pnl_ratio: 0.005    # 浮盈 0.5% 即移保本
+     lock_ratio:        0.003    # 保本后止损锁在成本价 +0.3%
+   trending:      # green/red bar growing, continuation
+     trigger_pnl_ratio: 0.008    # 保持不变
+     lock_ratio:        0.002
+   flip:          # flip_bullish/bearish
+     trigger_pnl_ratio: 0.010    # 稍晚，等信号更确认
+     lock_ratio:        0.002
```

---

## 七、期望值改善测算

```
以下是各项改造的预期数字贡献（估算，需回测验证）：

改造一：三档 Partial TP（Level 0 + Level 1 + Level 2）
  效果：169 笔"盈利止损单"的平均利润从 +5.34 → +8~10
  数学：(8.5 - 5.34) × 169 = +534 USDT
  回测目标：total return +22% → +27%

改造二：long_dual_support 专项修复
  效果：该通道 PnL 从 -67 → 趋近 0 或小正
  数学：+67 USDT（消除亏损口袋）
  回测目标：不影响开仓量，胜率可能轻微提升

改造三：Trailing Stop 自适应（震荡类更紧，趋势类更宽）
  效果：震荡类单子减少回吐，趋势类单子跑得更远
  预期：avg_win 从 22.35 → 25-28
  数学：(26 - 22.35) × 204 = +745 USDT
  回测目标：total return +22% → +29%

改造四：4 个 symbol 亏损治理
  效果：削减 SOL+BCH+ICP+FIL 共 -450 USDT 的净亏损
  注意：部分禁用会减少开仓量（需要确认能补回来）

三项改造叠加（保守估算）：
  当前净利润: ~+2,525 USDT / 月
  改造后预估: ~+3,500~+4,000 USDT / 月（+38% ~ +58% 提升）
  对应收益率: +22% → +35% ~ +40%（10k 本金）
```

---

## 八、ablation 实验顺序

```
每轮验收指标（相比当前基线）：
  ✓ trade_count:  250 ± 30（不能大幅偏离目标区间）
  ✓ win_rate:     ≥ 75%（不能低于目标）
  ✓ avg_win:      ≥ +24（比当前 22.35 有改善）
  ✓ avg_loss:     ≤ -28（比当前 -33.90 有改善）
  ✓ total_return: ≥ +26%（比当前 22.19% 有明显改善）
  ✓ MDD:          ≤ 4.0%（允许小幅增加）

实验 #1：三档 Partial TP
  改动：加入 partial_tp_levels (L0/L1/L2)
  验收：avg_win 提升，win_rate ≥ 75%，return 提升
  风险：trade_count 不变（partial TP 不影响开仓）

实验 #2：long_dual_support breakeven 提前（0.008 → 0.005）
  改动：仅针对该 VWAP 结构类型
  验收：该通道 PnL 转正，整体 MDD 微降
  风险：极低（只影响保本触发时机）

实验 #3：long_dual_support 初始止损收紧（0.020 → 0.012）
  改动：针对 long_dual_support 的 stop_loss_pct_override
  验收：avg_loss 降低，win_rate 不低于 73%
  风险：如果 SOL 大波动，可能增加"止损后继续上涨"的情况

实验 #4：Trailing Stop 自适应化
  改动：oscillation 档 activation 0.012 → 0.007
  验收：avg_win 进一步提升，趋势单 PnL 不降低
  风险：中（需要调整激活阈值，可能影响部分趋势单）

实验 #5：symbol 亏损治理（SOL/BCH/ICP/FIL）
  改动：symbol_overrides 新增各项覆盖
  注意：禁用某些通道后，trade_count 可能下降 10-20 笔，
        需要确认能被其他 symbol 补回

实验 #6（最后）：flip_bullish full-size 提纯
  改动：min_cvd_1h_delta_ratio 0.03 → 0.05
  验收：flip_bullish PnL 明显提升，交易数只轻微降低
```

---

## 九、变更速查表

| 参数 / 改动 | 当前值 | 建议值 | 实验 | 预期效果 |
|-------------|--------|--------|------|---------|
| `partial_tp_levels` Level 0 | 无 | 0.8%, 25%, 限 oscillation | #1 | avg_win ↑ |
| `partial_tp_levels` Level 1 | 无 | 1.2%, 30% | #1 | avg_win ↑ |
| `partial_tp_levels` Level 2 | 无 | 2.0%, 20% | #1 | avg_win ↑ |
| `long_dual_support` breakeven trigger | 0.008 | 0.005 | #2 | MDD ↓ |
| `long_dual_support` stop_loss | 0.020 | 0.012 | #3 | avg_loss ↓ |
| `long_dual_support` position_scale | 1.0 | 0.80 | #3 | 风险敞口 ↓ |
| trailing oscillation activation | 0.012 | 0.007 | #4 | avg_win ↑ |
| trailing trending atr_multiplier | 0.8 | 1.2 | #4 | 趋势单利润 ↑ |
| `SOLUSDT` min_signal_score | 全局 | 0.87 | #5 | SOL亏损 ↓ |
| `BCHUSDT` disable_long_dual_support | 无 | true | #5 | BCH亏损 ↓ |
| `ICPUSDT` max_position_scale | 1.0 | 0.60 | #5 | ICP风险 ↓ |
| `FILUSDT` disable_long_dual_support | 无 | true | #5 | FIL亏损 ↓ |
| `flip_bullish` min_cvd_delta | 0.03 | 0.05 | #6 | flip质量 ↑ |
