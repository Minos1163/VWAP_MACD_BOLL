# 三块能力优化建议：fast_exit / partial_tp / entry_hard_gates

**日期**：2026-03-30  
**前置状态**：P0 四项已完成（direction_lock 硬门闸、DCA 关闭、名义风险上限、配置收紧）  
**本文目标**：对三块待优化能力给出落地级建议，包含总体评价、关键修改点、参数校准、最小改动路径、验证清单

---

## 总体评价

| 模块 | 当前草稿质量 | 最大风险 | 优先级 |
|---|---|---|---|
| `fast_exit / time_exit` | 逻辑方向正确，但触发条件过于对称，缺分层 | 过于灵敏时会误杀正常回调 | 🔴 高，直接影响亏损控制 |
| `partial_tp / trailing_stop` | 结构合理，R-multiple 方向对，参数需校准 | trailing 距离 0.5% 在高波动标的上太紧 | 🟠 中，影响盈利效率 |
| `entry_hard_gates` | 覆盖全面但过严，全部 AND 会显著压缩交易量 | 机会集损失超预期，策略趋于不动 | 🟡 中，需要测完再上 |

---

## 一、`fast_exit / time_exit`

### 1.1 总体评价

草稿的核心逻辑正确：**用微观结构前置信号替代 4H 慢确认**。  
主要问题是把四个条件放在同等权重的“任意 2/4”里，这会导致两种对立的错误：

- `cond_lock`（方向锁反向）单独是**高置信强信号**，不需要等凑够第二个条件
- `cond_microstructure`（3 项微观恶化）单独是**噪声敏感信号**，需要更高的组合门槛

建议改为**分层优先级触发**，而不是简单投票。

### 1.2 核心修改：分层优先级 fast_exit

```python
def _check_fast_exit_conditions(self, symbol, position, context) -> tuple[bool, str]:
    """
    分层优先级触发，而非简单的 2/4 投票。
    返回 (should_exit, reason)
    """
    pos_dir = Direction.LONG if position.side == "long" else Direction.SHORT
    sign = 1 if pos_dir == Direction.LONG else -1

    # ── 第一层：单项即可触发（高置信强信号）─────────────────────

    # L1-A: direction_lock 明确反向，且已确认 N 根
    direction_lock = context.get("direction_lock")
    lock_is_against = (
        (pos_dir == Direction.LONG and direction_lock == "SHORT_ONLY") or
        (pos_dir == Direction.SHORT and direction_lock == "LONG_ONLY")
    )
    confirmed_bars = context.get("direction_lock_confirmed_bars", 0)
    # 15m 周期用 2 根，1h 周期用 1 根，按入场 timeframe 区分
    required_bars = 2 if context.get("entry_timeframe") == "15m" else 1
    if lock_is_against and confirmed_bars >= required_bars:
        return True, "L1_direction_lock_reversed"

    # L1-B: 入场信号完全失效（策略层标记）
    if context.get("entry_signal_invalidated", False):
        return True, "L1_entry_signal_invalidated"

    # L1-C: trap / fake breakout 高置信
    if context.get("trap_score", 0) >= 0.85:
        return True, "L1_trap_high_confidence"

    # ── 第二层：需要 2 项以上组合触发（中置信复合信号）──────────

    cvd_against = context.get("cvd_momentum", 0) * sign < 0
    oi_against = context.get("oi_delta_ratio", 0) * sign < 0
    book_against = context.get("imbalance", 0) * sign < -0.03
    micro_score = sum([cvd_against, oi_against, book_against])

    trap_moderate = context.get("trap_score", 0) >= 0.7
    lock_soft = lock_is_against and confirmed_bars >= 1

    # 修改：微观结构允许 2/3，不要求 3/3
    cond_micro_2of3 = micro_score >= 2

    l2_conditions = sum([
        cond_micro_2of3,
        trap_moderate,
        lock_soft,
    ])

    if l2_conditions >= 2:
        return True, f"L2_composite: micro={micro_score}/3, trap={trap_moderate}, lock_soft={lock_soft}"

    return False, ""
```

### 1.3 关键修改点说明

**修改 1：trap_score 阈值从 0.7 提高到 0.85（L1 层）**  
0.7 在当前实盘中触发频率过高，会把正常的短期逆势回调也误判为 trap。  
L1 层要求高置信，0.85 以上的 trap 才有立即全平的价值。  
0.7 保留在 L2 层作为复合条件之一。

**修改 2：微观结构从 3/3 改为 2/3（L2 层）**  
CVD / OI / 盘口同时恶化确实发生，但在较宽的时间窗口里。  
2/3 既能提前捕捉方向恶化，又比 3/3 减少 1 到 2 根 K 线的延迟。  
在 L2 组合触发语境下，2/3 微观 + 另一个条件组合的误报率可接受。

**修改 3：direction_lock_confirmed_bars 按 timeframe 区分**  
15m 入场 → 需要 2 根确认（30 分钟）  
1h 入场 → 需要 1 根确认（1 小时）  
草稿用固定 2 根，对 1h 入场过于宽松，可能等 2 小时才触发 L1。

### 1.4 time_exit 修改：按入场类型区分观察期

```python
def _check_time_based_exit(self, symbol, position, context) -> tuple[bool, str]:
    """
    按入场类型区分观察期，避免 flip 类信号和 continuation 类信号用同一个时钟。
    """
    hold_minutes = (now() - position.open_time).total_seconds() / 60
    entry_type = position.metadata.get("entry_type", "unknown")
    sign = 1 if position.side == "long" else -1

    # 按入场类型定义观察期和最低浮盈要求
    ENTRY_TYPE_PARAMS = {
        "flip_bullish": {"observe_min": 20, "min_profit_pct": 0.003},
        "flip_bearish": {"observe_min": 20, "min_profit_pct": 0.003},
        "red_bar_growing": {"observe_min": 35, "min_profit_pct": 0.0035},
        "green_bar_growing": {"observe_min": 35, "min_profit_pct": 0.0035},
        "continuation": {"observe_min": 45, "min_profit_pct": 0.004},
        "unknown": {"observe_min": 30, "min_profit_pct": 0.0035},
    }

    params = ENTRY_TYPE_PARAMS.get(entry_type, ENTRY_TYPE_PARAMS["unknown"])
    observe_min = params["observe_min"]
    min_profit = params["min_profit_pct"]

    if hold_minutes < observe_min:
        return False, ""

    unrealized_pct = position.unrealized_pnl_pct
    flow_expanding = context.get("cvd_momentum", 0) * sign > 0

    if unrealized_pct >= min_profit or flow_expanding:
        return False, ""

    return True, (
        f"time_exit: hold={hold_minutes:.0f}min > {observe_min}min, "
        f"pnl={unrealized_pct:.3%} < {min_profit:.3%}, flow_expanding={flow_expanding}"
    )
```

### 1.5 fast_exit 与 4H shrink 的优先级排布

```python
def _decide_for_existing_position(self, symbol, position, context):

    # ① fast_exit 最优先：方向/信号/结构层面失效
    should_exit, reason = self._check_fast_exit_conditions(symbol, position, context)
    if should_exit:
        return DecisionResult(action=Action.CLOSE, reason=reason, close_ratio=1.0)

    # ② time_exit 次优先：时效无进展
    should_time_exit, te_reason = self._check_time_based_exit(symbol, position, context)
    if should_time_exit:
        return DecisionResult(action=Action.CLOSE, reason=te_reason, close_ratio=1.0)

    # ③ 防守态减仓（50% 减仓，不全平）
    if self._check_defensive_mode(symbol, position, context):
        return DecisionResult(action=Action.REDUCE, reason="defensive_mode", close_ratio=0.5)

    # ④ 分批止盈（partial TP）
    partial_tp = self._check_partial_tp(symbol, position, context)
    if partial_tp:
        return partial_tp

    # ⑤ 追踪止盈更新
    self._update_trailing_stop(symbol, position, context)

    # ⑥ 最后才是 4H shrink / MACD 慢确认
    return self._check_slow_exit_conditions(symbol, position, context)
```

---

## 二、`partial_tp / trailing_stop`

### 2.1 总体评价

R-multiple 做分批止盈比固定收益率更合理，因为它会随 `stop_loss_pct` 自动调整，不需要在 SL 变化时重新校准 TP。  
主要问题是：**trailing stop distance 0.5% 在 ATR 窗口（0.6% 到 1.8%）里过小**，会在正常波动中频繁触发，把趋势仓过早锁死。

### 2.2 trailing_stop_distance 校准

```text
当前配置：trailing_stop_distance_pct = 0.005
ATR 范围：0.006 ~ 0.018
问题：0.5% 距离 < 单根 K 线的最小 ATR（0.6%），高频触发

推荐：trailing 距离应 = 1.0 × ATR_current，动态调整
最小值 = 0.007，最大值 = 0.015
```

```python
def _calculate_trailing_distance(self, symbol, context) -> float:
    """
    基于当前 ATR 动态计算 trailing 距离。
    不使用固定百分比，避免在不同波动率行情下行为不一致。
    """
    atr_pct = context.get("atr_pct", 0.010)
    atr_multiplier = self.config.get("trailing_stop_atr_multiplier", 1.0)

    raw_distance = atr_pct * atr_multiplier

    min_dist = self.config.get("trailing_stop_min_distance", 0.007)
    max_dist = self.config.get("trailing_stop_max_distance", 0.015)

    return max(min_dist, min(max_dist, raw_distance))
```

### 2.3 R-multiple 分批止盈完整实现

```python
class PartialTPState:
    """记录每个 symbol 的分批止盈完成状态。"""
    def __init__(self):
        self.levels_completed: list[int] = []
        self.trailing_activated: bool = False
        self.trailing_high_water: float = 0.0


def _check_partial_tp(self, symbol, position, context) -> DecisionResult | None:
    """
    检查是否触发分批止盈。
    返回 DecisionResult 或 None（不触发）。
    """
    if not self.config.partial_tp_enabled:
        return None

    state: PartialTPState = self._get_or_create_tp_state(symbol)
    sl_pct = self.config.stop_loss_pct
    unrealized_pct = position.unrealized_pnl_pct
    sign = 1 if position.side == "long" else -1

    for i, level in enumerate(self.config.partial_tp_levels):
        if i in state.levels_completed:
            continue

        trigger_profit = sl_pct * level["trigger_r_multiple"]

        if unrealized_pct * sign >= trigger_profit:
            close_ratio = level["close_ratio"]
            state.levels_completed.append(i)

            logger.info(
                f"[PARTIAL_TP] {symbol}: level={i+1}, "
                f"trigger={trigger_profit:.3%}, pnl={unrealized_pct:.3%}, "
                f"close_ratio={close_ratio:.0%}"
            )

            return DecisionResult(
                action=Action.REDUCE,
                reason=f"partial_tp_level_{i+1}",
                close_ratio=close_ratio,
            )

    return None


def _update_trailing_stop(self, symbol, position, context) -> None:
    """
    更新追踪止盈高水位。在 _check_partial_tp 之后调用。
    实际的 trailing stop 触发由 protection 层（TP/SL orders）处理。
    这里只更新 high_water_mark，供 protection 层使用。
    """
    if not self.config.trailing_stop_enabled:
        return

    state: PartialTPState = self._get_or_create_tp_state(symbol)
    activation_pct = self.config.trailing_stop_activation_pct
    unrealized_pct = position.unrealized_pnl_pct
    sign = 1 if position.side == "long" else -1
    current_price = context.get("price", position.avg_entry_price)

    if not state.trailing_activated:
        if unrealized_pct * sign >= activation_pct:
            state.trailing_activated = True
            state.trailing_high_water = current_price
            logger.info(f"[TRAILING_ACTIVATED] {symbol}: price={current_price:.6f}")
        return

    if position.side == "long":
        if current_price > state.trailing_high_water:
            state.trailing_high_water = current_price
    else:
        if current_price < state.trailing_high_water:
            state.trailing_high_water = current_price

    trail_dist = self._calculate_trailing_distance(symbol, context)
    if position.side == "long":
        trail_stop_price = state.trailing_high_water * (1 - trail_dist)
    else:
        trail_stop_price = state.trailing_high_water * (1 + trail_dist)

    self._request_sl_update(symbol, trail_stop_price, reason="trailing_stop")
```

### 2.4 是否需要区分入场类型

**结论：第一阶段不区分，第二阶段可选。**

原因：

- `flip` 类信号（快进快出）自然会被 `time_exit` 的短观察期覆盖
- `continuation` 类信号持有更久，trailing 的价值更大
- 区分入场类型的代价是配置复杂度倍增，回测噪声增加
- 先用统一参数跑 2 周实盘，再根据分类统计决定是否差异化

### 2.5 配置 diff（校准后）

```diff
- "take_profit_pct": 0.02,
- "stop_loss_pct": 0.02,

+ "stop_loss_pct": 0.012,

+ "partial_tp_enabled": true,
+ "partial_tp_levels": [
+     {"close_ratio": 0.30, "trigger_r_multiple": 1.0},
+     {"close_ratio": 0.40, "trigger_r_multiple": 2.0}
+ ],

+ "trailing_stop_enabled": true,
+ "trailing_stop_activation_pct": 0.012,

- "trailing_stop_distance_pct": 0.005,
+ "trailing_stop_atr_multiplier": 1.0,
+ "trailing_stop_min_distance": 0.007,
+ "trailing_stop_max_distance": 0.015,

+ "breakeven_trigger_pnl_ratio": 0.008,
+ "breakeven_lock_ratio": 0.0025,
```

---

## 三、`entry_hard_gates`

### 3.1 总体评价

这是三块里**最需要谨慎的一块**。  
当前草稿把 9 个条件全部 AND，意味着任何一个不满足都拒绝入场。  
在真实行情中，`spread_bps`、`depth_ratio`、`imbalance` 这类盘口条件有很高的短期波动性，全部 AND 会让系统在正常行情里频繁不开仓。

建议把 9 个条件分成三层，层级之间 AND，层级内部允许部分不满足。

### 3.2 三层 hard gates 架构

```python
def _validate_entry_hard_gates(
    self, direction: Direction, context: dict
) -> tuple[bool, str]:
    """
    三层架构：
    - 第一层（结构层）：全部必须满足，不可妥协
    - 第二层（流向层）：需要 2/3 满足
    - 第三层（盘口层）：需要 2/3 满足
    任意一层不通过则拒绝入场。
    """
    is_long = direction == Direction.LONG
    sign = 1 if is_long else -1

    # ── 第一层：结构层（全部必须满足）────────────────────────────
    layer1 = {
        "regime_trend": context.get("regime") == "TREND",
        "adx_sufficient": context.get("adx", 0) >= 22,
        "atr_in_range": 0.006 <= context.get("atr_pct", 0) <= 0.020,
        "spread_ok": context.get("spread_bps", 999) <= 0.0008,
    }
    layer1_failed = [k for k, v in layer1.items() if not v]
    if layer1_failed:
        return False, f"L1_structure_failed: {', '.join(layer1_failed)}"

    # ── 第二层：流向层（需要 2/3 满足）───────────────────────────
    cvd_ok = context.get("cvd_ratio", 0) * sign > -0.05
    oi_ok = context.get("oi_delta_ratio", 0) * sign >= 0
    vwap_ok = (
        context.get("price", 0) >= context.get("anchored_vwap", 0)
        if is_long else
        context.get("price", 0) <= context.get("anchored_vwap", 0)
    )
    layer2_score = sum([cvd_ok, oi_ok, vwap_ok])
    if layer2_score < 2:
        return False, (
            f"L2_flow_failed: cvd={cvd_ok}, oi={oi_ok}, vwap={vwap_ok} "
            f"({layer2_score}/3 < 2)"
        )

    # ── 第三层：盘口层（需要 2/3 满足）───────────────────────────
    depth_ok = (
        context.get("depth_ratio", 1.0) >= 1.02
        if is_long else
        context.get("depth_ratio", 1.0) <= 0.98
    )
    imbalance_ok = (
        context.get("imbalance", 0) >= 0.03
        if is_long else
        context.get("imbalance", 0) <= -0.03
    )
    cvd_momentum_ok = context.get("cvd_momentum", 0) * sign > 0

    layer3_score = sum([depth_ok, imbalance_ok, cvd_momentum_ok])
    if layer3_score < 2:
        return False, (
            f"L3_microstructure_failed: depth={depth_ok}, "
            f"imbalance={imbalance_ok}, cvd_momentum={cvd_momentum_ok} "
            f"({layer3_score}/3 < 2)"
        )

    return True, ""
```

### 3.3 关键参数调整说明

**ATR 上限：0.018 → 0.020**  
草稿上限 0.018 在有新闻、公告或流动性事件时会频繁被突破，导致系统在较好的趋势行情里拒绝入场。0.020 是更合理的噪声过滤边界。

**spread_bps：0.0005 → 0.0008**  
0.0005（0.5 bps）在非主流标的或行情快速移动时常态性超标，会把正常流动性下的机会全部过滤掉。0.0008 仍然是严格的点差控制，但不会因为一个 bps 的波动就拒绝入场。

**VWAP / CVD / OI 降级为软约束（2/3 通过）**  
这三者本身就有相关性，且在趋势刚建立时 VWAP 偏移本就有延迟。全部 AND 会在趋势初期系统性错过入场机会。2/3 既保留了多维流向验证，又允许一个维度滞后。

### 3.4 调用顺序修正

**建议：hard gates 放在 score 阈值之后，而不是之前。**

原因：如果 hard gates 放在 score 计算之前，score 偏低的信号会被 hard gates 直接拒绝，但拒绝理由会记录为 “hard gate failed” 而不是 “score insufficient”，导致监控日志混淆，无法区分是信号质量不够还是结构不满足。

```python
def decide(self, symbol, context):
    # GATE 0: direction_lock（已有，不变）
    ...

    direction = self._determine_direction(context)

    # GATE 1: score 阈值
    score = self._calculate_score(direction, context)
    threshold = self._get_threshold(direction)
    if score < threshold:
        return DecisionResult(action=Action.HOLD, reason=f"score_low:{score:.3f}")

    # GATE 2: hard gates（score 达标后才检查）
    if self.config.entry_hard_gates_enabled:
        passed, reason = self._validate_entry_hard_gates(direction, context)
        if not passed:
            return DecisionResult(action=Action.HOLD, reason=reason)

    # 通过所有门槛
    return self._build_entry_decision(direction, score, context)
```

### 3.5 配置 diff

```diff
+ "entry_hard_gates_enabled": true,

  // 第一层：结构层（全部必须满足）
+ "entry_hard_gate_adx_min": 22,
- "entry_hard_gate_atr_min": 0.006,
- "entry_hard_gate_atr_max": 0.018,
+ "entry_hard_gate_atr_min": 0.006,
+ "entry_hard_gate_atr_max": 0.020,
- "entry_hard_gate_spread_bps_max": 0.0005,
+ "entry_hard_gate_spread_bps_max": 0.0008,

  // 第二层：流向层（2/3 通过）
+ "entry_hard_gate_flow_min_pass": 2,

  // 第三层：盘口层（2/3 通过）
+ "entry_hard_gate_micro_min_pass": 2,
```

---

## 四、上线前验证清单

### fast_exit / time_exit

```text
[ ] 模拟场景：direction_lock=SHORT_ONLY，持多仓，confirmed_bars=2
    → 预期：L1_direction_lock_reversed，立即全平
[ ] 模拟场景：trap_score=0.90
    → 预期：L1_trap_high_confidence，立即全平
[ ] 模拟场景：trap_score=0.75 + cond_micro 2/3
    → 预期：L2_composite 触发，全平
[ ] 模拟场景：trap_score=0.75 单独触发
    → 预期：不触发（只是 L2 单项，不满足 2 项组合）
[ ] 模拟 time_exit：flip_bullish 入场，20 分钟后 pnl=0.002%，flow 未扩张
    → 预期：time_exit 触发
[ ] 模拟 time_exit：flip_bullish 入场，15 分钟后
    → 预期：未超过观察期，不触发
[ ] 确认 fast_exit 在代码调用顺序中位于 4H shrink 之前
```

### partial_tp / trailing_stop

```text
[ ] 模拟：持多仓，pnl 达到 1.2%（1R × 100%）
    → 预期：partial TP level 1 触发，减仓 30%
[ ] 模拟：pnl 继续达到 2.4%（2R）
    → 预期：partial TP level 2 触发，再减仓 40%（剩余 30%）
[ ] 确认 levels_completed 防止同一 level 重复触发
[ ] 模拟：trailing 激活后，price 回落超过 ATR × 1.0
    → 预期：SL 更新请求被发出
[ ] 模拟：ATR=0.010，trailing distance 应为 0.010（1x ATR）
[ ] 确认 position 平仓后 PartialTPState 被清理，不残留状态
```

### entry_hard_gates

```text
[ ] 回测对比：关闭 hard gates vs 开启 hard gates，30天，记录交易数差异
    → 预期：交易数减少 15%~35%，win_rate 提升 1%~3%
    → 警告：若交易数减少 > 40%，说明某层过严，需要放宽
[ ] 验证 L2 流向层：CVD+OI+VWAP 全部反向时，应拒绝入场（0/3 < 2）
[ ] 验证 L2 流向层：CVD+OI 满足、VWAP 不满足时，应通过（2/3 ≥ 2）
[ ] 验证 L3 盘口层：depth+imbalance 满足、cvd_momentum 不满足时，应通过（2/3 ≥ 2）
[ ] 确认拒绝日志清晰区分 L1/L2/L3，便于后续归因
[ ] 运行 alpha_dilution_monitor，确认 after_hard_gates 层的稀释率 ≤ 35%
    → 若超过 35%，优先检查 L1 的 spread_bps 阈值是否过严
```

---

## 五、参数过严 / 过松风险汇总

| 参数 | 当前草稿值 | 本文建议值 | 风险方向 | 原因 |
|---|---|---|---|---|
| `trap_score` L1 阈值 | 0.7 | **0.85** | 草稿偏松 | 0.7 在正常行情频繁触发，误杀回调 |
| `trailing_stop_distance` | 0.005 固定 | **ATR×1.0，min=0.007** | 草稿偏紧 | 0.5% < 最小 ATR，高频触发 |
| `entry_hard_gate_atr_max` | 0.018 | **0.020** | 草稿偏紧 | 事件行情被排除在外 |
| `entry_hard_gate_spread_bps` | 0.0005 | **0.0008** | 草稿偏紧 | 非主流标的常态性超标 |
| 微观结构触发数（fast_exit） | 3/3 | **2/3** | 草稿偏紧 | 3/3 同时恶化等待时间太长 |
| hard gates AND 层数 | 9 项全 AND | **分三层，L2/L3 各 2/3** | 草稿偏紧 | 全 AND 会过度压缩机会集 |
| time_exit 观察期 | 30 分钟固定 | **按入场类型 20~45 分钟** | 草稿偏简单 | flip 类不需要等 30 分钟 |

---

*生成于 2026-03-30，基于策略待优化项评审请求。*
