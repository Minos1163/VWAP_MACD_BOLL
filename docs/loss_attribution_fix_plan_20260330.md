# 亏损归因驱动的策略修订方案

**日期**：2026-03-30  
**亏损窗口**：2026-03-29 00:00 ~ 2026-03-30 15:15 (BJ)  
**窗口最大回撤**：-7.18%（峰值 103.43 → 低点 96.00）  
**核心结论**：不是行情问题，是系统把四层风险串联放大了

---

## 0. 问题优先级地图

| 优先级 | 问题 | 影响 | 修复难度 |
|---|---|---|---|
| 🔴 P0 | `direction_lock` 未成为全链路硬门闸 | 逆方向开仓，直接致命 | 中（需找绕过路径） |
| 🔴 P0 | DCA 不校验 `direction_lock`，逆势尝试加码 | 放大错误方向风险 | 低（加一次检查） |
| 🔴 P0 | 单笔名义风险过大（杠杆×仓位=205%权益） | 单笔可以吃掉整日止损 | 低（改配置） |
| 🟠 P1 | 平仓依赖慢确认，账户级风控成了策略止损器 | 每次亏损都亏够才止 | 中（加快速退出条件） |
| 🟡 P2 | 入场门槛缺少多维度硬验证，仅靠 score 通过 | 低质量信号混入 | 中（加硬门槛检查） |

**修复顺序**：P0 三项必须同步修复，上线前不允许只改其中一项。

---

## 1. P0 修复：direction_lock 成为全链路硬门闸

### 1.1 问题定位

`direction_lock = SHORT_ONLY` 下仍然执行了 `BUY`，绕过路径最可能发生在三处：

```
主决策链:  FundFlowDecisionEngine.decide()
               └── _should_apply_direction_lock()  ← 这里应该拦
                         ↓ 但可能存在 override 路径
DCA 补决策: _build_dca_decision()
               └── 直接按持仓方向生成 BUY/SELL，不重新校验 direction_lock
                         ↓
执行前改写: ExecutionRouter 中是否存在 direction override?
```

**验证方法**：在三处加日志，确认哪一层实际放行了 BUY：

```python
# 临时诊断补丁，三处都加，下次出现异常时可定位
def _should_apply_direction_lock(self, symbol, candidate_direction, context):
    lock = context.get("direction_lock")
    if lock == "SHORT_ONLY" and candidate_direction == Direction.LONG:
        logger.critical(
            f"[DIRECTION_LOCK_VIOLATION] {symbol}: lock=SHORT_ONLY but "
            f"candidate=LONG, caller={_get_caller()}"  # 打调用栈
        )
        # 不要只记录，必须 return False 阻断
        return False
    if lock == "LONG_ONLY" and candidate_direction == Direction.SHORT:
        logger.critical(
            f"[DIRECTION_LOCK_VIOLATION] {symbol}: lock=LONG_ONLY but "
            f"candidate=SHORT, caller={_get_caller()}"
        )
        return False
    return True
```

### 1.2 修复方案：在决策链入口加不可绕过的硬门闸

#### 伪代码（修复后的决策入口）

```python
# src/fund_flow/decision_engine.py
# FundFlowDecisionEngine.decide() 入口处，所有后续逻辑之前

def decide(self, symbol, context) -> DecisionResult:
    
    # ============================================================
    # GATE 0: direction_lock 硬门闸 —— 任何路径不得绕过
    # ============================================================
    direction_lock = context.get("direction_lock", "BOTH")
    candidate_direction = self._get_candidate_direction(context)
    
    if not _direction_lock_allows(direction_lock, candidate_direction):
        return DecisionResult(
            action=Action.HOLD,
            reason=f"direction_lock_hard_block: lock={direction_lock}, "
                   f"candidate={candidate_direction}",
            blocked_by="direction_gate",
        )
    
    # ============================================================
    # 以下所有逻辑才允许执行
    # ============================================================
    # ... 原有决策逻辑 ...


def _direction_lock_allows(lock: str, direction: Direction) -> bool:
    """
    严格的方向检查。BOTH 允许任意方向。
    只要不是明确允许，就返回 False。
    """
    if lock == "BOTH":
        return True
    if lock == "LONG_ONLY":
        return direction in (Direction.LONG, Direction.NEUTRAL, Direction.CLOSE)
    if lock == "SHORT_ONLY":
        return direction in (Direction.SHORT, Direction.NEUTRAL, Direction.CLOSE)
    # 未知 lock 值，默认拒绝（fail-safe）
    logger.error(f"Unknown direction_lock value: {lock!r}, defaulting to HOLD")
    return False
```

#### diff（decision_engine.py）

```diff
 def decide(self, symbol, context) -> DecisionResult:
+    # ── GATE 0: direction_lock 全链路硬门闸 ──────────────────────────
+    direction_lock = context.get("direction_lock", "BOTH")
+    candidate = self._get_candidate_direction(context)
+    if not _direction_lock_allows(direction_lock, candidate):
+        return DecisionResult(
+            action=Action.HOLD,
+            reason=f"direction_lock_hard_block:{direction_lock}",
+            blocked_by="direction_gate",
+        )
+    # ─────────────────────────────────────────────────────────────────
+
     # 原有主决策逻辑...
     flow_snapshot = self._build_flow_snapshot(symbol, context)
```

---

## 2. P0 修复：DCA 强制复用方向门闸

### 2.1 问题定位

```
fund_flow_bot.py:7876-7892  ← 已有持仓后触发 DCA 尝试
fund_flow_bot.py:4002-4086  ← _build_dca_decision() 直接用持仓方向生成 BUY/SELL
                                不重新校验 direction_lock
```

当持仓方向与 `direction_lock` 已经反向，DCA 会尝试在错误方向上继续摊薄。
即使被执行层用"已有持仓，跳过重复开仓"拦下，这说明系统有强烈的"逆势加仓意愿"。

### 2.2 修复方案

#### 选项 A（推荐）：彻底关闭 DCA，config 层 + 代码层双重保证

```diff
# config/trading_config_fund_flow_live_production.json
- "dca_martingale_enabled": true,
- "dca_max_additions": 2,
+ "dca_martingale_enabled": false,
+ "dca_max_additions": 0,
```

```diff
# src/app/fund_flow_bot.py:7876
 def _build_dca_decision(self, symbol, position, context):
+    # 代码层强制关闭，与配置双重保证，避免"配置 false 但代码仍执行"的灰区
+    if not self.config.get("dca_martingale_enabled", False):
+        return DecisionResult(action=Action.HOLD, reason="dca_disabled")
+
+    # 即使 DCA 开启，必须重新校验 direction_lock
+    direction_lock = context.get("direction_lock", "BOTH")
+    dca_direction = Direction.LONG if position.side == "long" else Direction.SHORT
+    if not _direction_lock_allows(direction_lock, dca_direction):
+        logger.warning(
+            f"[DCA_BLOCKED] {symbol}: DCA direction={dca_direction} "
+            f"conflicts with direction_lock={direction_lock}"
+        )
+        return DecisionResult(action=Action.HOLD, reason="dca_direction_lock_block")
+
     # 原有 DCA 构建逻辑...
```

#### 选项 B（若将来重新开启 DCA）：加完整的方向门闸

```python
def _build_dca_decision(self, symbol, position, context) -> DecisionResult:
    """
    DCA 决策必须经过与首次开仓完全相同的方向验证。
    不允许仅凭"已有持仓"就绕过 direction_lock。
    """
    # 1. 方向门闸（与首次开仓完全相同的逻辑）
    direction_lock = context.get("direction_lock", "BOTH")
    guide_direction = context.get("guide_direction")
    ev_direction = context.get("ev_direction")
    position_direction = Direction.LONG if position.side == "long" else Direction.SHORT
    
    # 三项方向必须全部一致，才允许 DCA
    directions_consistent = all([
        _direction_lock_allows(direction_lock, position_direction),
        guide_direction == position_direction or guide_direction is None,
        ev_direction == position_direction or ev_direction is None,
    ])
    
    if not directions_consistent:
        return DecisionResult(
            action=Action.HOLD,
            reason="dca_direction_mismatch",
            detail={
                "direction_lock": direction_lock,
                "guide": guide_direction,
                "ev": ev_direction,
                "position": position_direction,
            }
        )
    
    # 2. 额外门槛：DCA 时分数阈值提高 0.05
    dca_score_threshold = self.config.long_open_threshold + 0.05
    if context.get("signal_score", 0) < dca_score_threshold:
        return DecisionResult(action=Action.HOLD, reason="dca_score_insufficient")
    
    # 3. 原有 DCA 构建逻辑...
    return self._build_dca_order(symbol, position, context)
```

---

## 3. P0 修复：单笔名义风险硬上限

### 3.1 问题复现

```
POLUSDT 成交额: 210.18 USDT
账户权益:       102.31 USDT
名义风险比:     205.43%   ← 杠杆 × 仓位比例的实际结果
```

`target_portion = 0.6` × `leverage = 3` + 精度误差 = 实际名义暴露远超预期。

**当前定义问题**：`target_portion_of_balance` 是"保证金占比"，不是"名义风险占比"。
在 leverage=3 时，0.6 保证金 × 3 = 1.8 倍账户的名义敞口，单笔亏损 1% 就是账户 1.8%。

### 3.2 修复方案

#### 方法一（推荐）：增加名义风险硬上限检查

```python
# src/fund_flow/execution_router.py
# execute_decision() 执行前，在 risk.validate_decision 之后加一层

def _check_nominal_risk_limit(self, symbol, qty, price, leverage, account_equity) -> bool:
    """
    硬检查：单笔名义风险不允许超过账户权益的 N%。
    名义风险 = qty × price（不含杠杆，杠杆已在 qty 里体现）
    """
    nominal_value = qty * price
    nominal_ratio = nominal_value / account_equity
    
    MAX_NOMINAL_RATIO = self.config.get("max_single_trade_nominal_ratio", 0.60)
    
    if nominal_ratio > MAX_NOMINAL_RATIO:
        logger.error(
            f"[NOMINAL_RISK_BLOCK] {symbol}: nominal={nominal_value:.2f}, "
            f"equity={account_equity:.2f}, ratio={nominal_ratio:.2%} > "
            f"limit={MAX_NOMINAL_RATIO:.2%}"
        )
        return False
    return True
```

#### diff（config 层）

```diff
# config/trading_config_fund_flow_live_production.json

- "default_leverage": 3,
- "max_leverage": 4,
- "default_target_portion": 0.5,
- "max_symbol_position_portion": 0.5,
- "max_active_symbols": 4,

+ "default_leverage": 2,
+ "max_leverage": 2,
+ "default_target_portion": 0.18,
+ "max_symbol_position_portion": 0.25,
+ "max_active_symbols": 2,
+ "max_single_trade_nominal_ratio": 0.60,   // 新增：名义风险硬上限 60% 权益
+ "single_trade_risk_budget_pct": 0.006,    // 新增：单笔最大风险 0.6% 权益
```

#### 单笔风险预算自动计算仓位（替代固定比例）

```python
def calculate_position_size(self, symbol, entry_price, stop_loss_price, account_equity):
    """
    基于风险预算反算仓位，而不是固定 portion。
    确保单笔最大亏损 = 账户权益 × risk_budget_pct。
    """
    risk_budget = account_equity * self.config.single_trade_risk_budget_pct  # e.g. 0.6%
    risk_per_unit = abs(entry_price - stop_loss_price)  # 每单位亏损
    
    if risk_per_unit <= 0:
        return 0
    
    raw_qty = risk_budget / risk_per_unit
    
    # 还要受名义风险上限约束
    max_qty_by_nominal = (account_equity * self.config.max_single_trade_nominal_ratio) / entry_price
    
    final_qty = min(raw_qty, max_qty_by_nominal)
    return self._format_qty(symbol, final_qty)
```

---

## 4. P1 修复：平仓前移到"方向失效"而非"账户亏损"

### 4.1 问题诊断

```
3157 条 decision 事件中，close 只有 3 次
平仓理由几乎全是 macd_v2_4h_shrink_exit_long（4小时级别确认）
账户最终靠 daily_loss 冷却兜底
```

**根因**：当前平仓信号依赖 4H 慢确认，但方向失效信号（`direction_lock` 翻转、CVD 恶化）比 4H 确认早 1~2 小时出现。这 1~2 小时就是额外的亏损窗口。

### 4.2 快速退出条件

#### 伪代码

```python
# src/fund_flow/decision_engine.py
# 在每轮 decide() 对已有持仓的处理中，优先于 4H 慢确认检查

def _check_fast_exit_conditions(self, symbol, position, context) -> bool:
    """
    返回 True 表示应该立即全平，不等待 4H shrink 确认。
    """
    pos_dir = Direction.LONG if position.side == "long" else Direction.SHORT
    
    # 条件 1: direction_lock 已经明确反向（连续 2 根确认）
    direction_lock = context.get("direction_lock")
    lock_is_against = (
        (pos_dir == Direction.LONG  and direction_lock == "SHORT_ONLY") or
        (pos_dir == Direction.SHORT and direction_lock == "LONG_ONLY")
    )
    lock_confirmed_bars = context.get("direction_lock_confirmed_bars", 0)
    cond_lock = lock_is_against and lock_confirmed_bars >= 2
    
    # 条件 2: CVD + OI + 盘口三者同时转坏
    cvd_against  = context.get("cvd_momentum", 0) * pos_dir.value < 0
    oi_against   = context.get("oi_delta_ratio", 0) * pos_dir.value < 0
    book_against = context.get("imbalance", 0) * pos_dir.value < -0.03
    cond_microstructure = sum([cvd_against, oi_against, book_against]) >= 3
    
    # 条件 3: 15m 入场信号失效（触发方向反转信号）
    cond_signal_invalidated = context.get("entry_signal_invalidated", False)
    
    # 条件 4: trap / fake breakout 特征出现
    cond_trap = context.get("trap_score", 0) >= 0.7
    
    # 任意两个条件同时满足 → 立即全平
    conditions_met = sum([cond_lock, cond_microstructure,
                          cond_signal_invalidated, cond_trap])
    
    if conditions_met >= 2:
        logger.warning(
            f"[FAST_EXIT] {symbol}: conditions_met={conditions_met}, "
            f"lock={cond_lock}, micro={cond_microstructure}, "
            f"signal={cond_signal_invalidated}, trap={cond_trap}"
        )
        return True
    
    return False


# 调用位置：在 decide() 对持仓的处理里，放在 4H shrink 检查之前
def decide(self, symbol, context):
    # ... GATE 0 方向门闸 ...
    
    if position := self._get_existing_position(symbol):
        # 快速退出优先于所有慢确认
        if self._check_fast_exit_conditions(symbol, position, context):
            return DecisionResult(
                action=Action.CLOSE,
                reason="fast_exit_conditions_met",
                close_ratio=1.0,  # 全平
            )
        
        # 防守态：减半仓
        if self._check_defensive_conditions(symbol, position, context):
            return DecisionResult(
                action=Action.REDUCE,
                reason="defensive_mode_triggered",
                close_ratio=0.5,
            )
        
        # 之后才是原有的 4H shrink / MACD 慢确认逻辑
        # ...
```

### 4.3 开仓后时效风控

```python
def _check_time_based_exit(self, symbol, position, context) -> bool:
    """
    开仓 30 分钟内若浮盈不足 0.35% 且 flow 未扩张，直接平仓。
    避免"慢热型死扛"。
    """
    hold_minutes = (now() - position.open_time).total_seconds() / 60
    
    if hold_minutes < 30:
        return False  # 还在观察期
    
    if hold_minutes > 30 and hold_minutes < 60:
        unrealized_pct = position.unrealized_pnl_pct
        flow_expanding = context.get("cvd_momentum", 0) * (
            1 if position.side == "long" else -1
        ) > 0
        
        if unrealized_pct < 0.0035 and not flow_expanding:
            logger.info(f"[TIME_EXIT] {symbol}: 30min no progress, exiting")
            return True
    
    return False
```

---

## 5. P1 修复：分批止盈替代等额止盈

### 5.1 当前问题

当前 `take_profit_pct = 0.02` 是一刀切的单点止盈。在趋势行情中，经常出现：
- 利润到了 TP 点被全平
- 后续行情继续走
- 而在震荡行情中，又因为等满额 TP 而把浮盈全部回吐

### 5.2 分批止盈方案

```python
# config 层新增
PARTIAL_TP_LEVELS = [
    {"ratio": 0.30, "trigger_r": 1.0},   # 盈利 1R 时平 30%
    {"ratio": 0.40, "trigger_r": 2.0},   # 盈利 2R 时再平 40%
    # 剩余 30% 走追踪止盈
]

TRAILING_STOP = {
    "activation_pct": 0.012,   # 盈利 1.2% 后激活追踪
    "distance_pct":   0.005,   # 追踪距离 0.5%
}
```

```diff
# config/trading_config_fund_flow_live_production.json

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
+ "trailing_stop_distance_pct": 0.005,
+ "breakeven_trigger_pnl_ratio": 0.008,
+ "breakeven_lock_ratio": 0.0025,
```

---

## 6. P2 修复：入场门槛加多维硬验证

### 6.1 新的入场验证函数

```python
# src/fund_flow/decision_engine.py

def _validate_entry_hard_gates(self, direction: Direction, context: dict) -> tuple[bool, str]:
    """
    硬门槛验证。任何一项不满足直接拒绝，不看 score。
    返回 (passed, reject_reason)
    """
    is_long = direction == Direction.LONG
    
    checks = {
        # 趋势结构
        "regime_trend":  context.get("regime") == "TREND",
        "adx_sufficient": context.get("adx", 0) >= 22,
        
        # 波动率窗口
        "atr_in_range": 0.006 <= context.get("atr_pct", 0) <= 0.018,
        
        # 盘口结构（做多/做空方向相反）
        "depth_ok": (context.get("depth_ratio", 1.0) >= 1.02
                     if is_long else
                     context.get("depth_ratio", 1.0) <= 0.98),
        
        "imbalance_ok": (context.get("imbalance", 0) >= 0.03
                         if is_long else
                         context.get("imbalance", 0) <= -0.03),
        
        # 点差
        "spread_ok": context.get("spread_bps", 999) <= 0.0005,
        
        # VWAP 结构
        "vwap_ok": (context.get("price", 0) >= context.get("anchored_vwap", 0)
                    if is_long else
                    context.get("price", 0) <= context.get("anchored_vwap", 0)),
        
        # CVD 方向
        "cvd_ok": (context.get("cvd_ratio", 0) > -0.05
                   if is_long else
                   context.get("cvd_ratio", 0) < 0.05),
        
        # OI 方向
        "oi_ok": (context.get("oi_delta_ratio", 0) >= 0
                  if is_long else
                  context.get("oi_delta_ratio", 0) <= 0),
    }
    
    failed = [name for name, passed in checks.items() if not passed]
    
    if failed:
        return False, f"hard_gate_failed: {', '.join(failed)}"
    
    return True, ""


# 调用位置：在 score 计算之后，下单之前
def decide(self, symbol, context):
    # ... GATE 0 方向门闸 ...
    
    direction = self._determine_direction(context)
    score = self._calculate_score(direction, context)
    
    # GATE 1: score 阈值
    threshold = self.config.long_open_threshold if direction == Direction.LONG \
                else self.config.short_open_threshold
    if score < threshold:
        return DecisionResult(action=Action.HOLD, reason=f"score_insufficient:{score:.3f}")
    
    # GATE 2: 硬门槛验证（score 达标后仍需通过）
    passed, reject_reason = self._validate_entry_hard_gates(direction, context)
    if not passed:
        return DecisionResult(action=Action.HOLD, reason=reject_reason)
    
    # 通过所有门槛，生成开仓决策
    return self._build_entry_decision(direction, score, context)
```

---

## 7. 风控参数全量 diff

```diff
# config/trading_config_fund_flow_live_production.json

  // ── 方向控制 ──────────────────────────────────
+ "direction_lock_hard_gate_enabled": true,   // 新增：全链路硬门闸开关
+ "dca_recheck_direction_lock": true,         // 新增：DCA 必须重新校验方向

  // ── 杠杆与仓位 ────────────────────────────────
- "default_leverage": 3,
- "max_leverage": 4,
- "default_target_portion": 0.5,
- "max_symbol_position_portion": 0.5,
- "max_active_symbols": 4,
+ "default_leverage": 2,
+ "max_leverage": 2,
+ "default_target_portion": 0.18,
+ "max_symbol_position_portion": 0.25,
+ "max_active_symbols": 2,
+ "max_single_trade_nominal_ratio": 0.60,     // 新增：名义风险硬上限
+ "single_trade_risk_budget_pct": 0.006,      // 新增：单笔最大亏损 0.6% 权益

  // ── DCA ───────────────────────────────────────
- "dca_martingale_enabled": true,
- "dca_max_additions": 2,
+ "dca_martingale_enabled": false,
+ "dca_max_additions": 0,

  // ── 止损 / 止盈 ───────────────────────────────
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
+ "trailing_stop_distance_pct": 0.005,
  "breakeven_trigger_pnl_ratio": 0.008,
- "breakeven_lock_ratio": 0.002,
+ "breakeven_lock_ratio": 0.0025,

  // ── 账户级风控 ────────────────────────────────
- "daily_loss_limit_pct": 0.05,
+ "daily_loss_limit_pct": 0.03,               // 单日止损从 5% 收紧到 3%
+ "consecutive_loss_halt_count": 3,           // 新增：连续 3 笔亏损停止新开仓
+ "consecutive_loss_size_reduction": 0.5,     // 新增：连续 2 笔亏损后仓位减半

  // ── 快速退出 ──────────────────────────────────
+ "fast_exit_enabled": true,                  // 新增：快速退出逻辑总开关
+ "fast_exit_direction_lock_bars": 2,         // 新增：direction_lock 反向确认 N 根
+ "fast_exit_microstructure_count": 3,        // 新增：微结构恶化指标需要 N 项同时触发
+ "time_exit_enabled": true,                  // 新增：时效退出
+ "time_exit_minutes": 30,                    // 新增：30 分钟无进展
+ "time_exit_min_profit_pct": 0.0035,         // 新增：30 分钟内最低浮盈要求

  // ── 入场硬门槛 ────────────────────────────────
+ "entry_hard_gates_enabled": true,           // 新增：入场硬门槛总开关
+ "entry_hard_gate_adx_min": 22,
+ "entry_hard_gate_atr_min": 0.006,
+ "entry_hard_gate_atr_max": 0.018,
+ "entry_hard_gate_spread_bps_max": 0.0005,
```

---

## 8. 上线验证清单

在将修改推向生产前，必须逐项确认：

```
方向门闸
[ ] 在 SHORT_ONLY 上下文中，向 decide() 传入 LONG 候选，确认返回 HOLD
[ ] 在 SHORT_ONLY 上下文中，触发 DCA 路径，确认返回 HOLD
[ ] 在 BOTH 上下文中，LONG 和 SHORT 候选均能正常通过
[ ] 在日志里搜索 DIRECTION_LOCK_VIOLATION，确认不再出现

仓位风控
[ ] 计算：2× 杠杆 × 0.18 portion = 0.36 名义比，远低于 60% 上限
[ ] 在回测中确认单笔最大亏损 ≤ 账户的 0.6%
[ ] 确认 max_active_symbols=2 时，最大并发名义敞口 ≤ 72% 权益

快速退出
[ ] 模拟 direction_lock 反向 2 根，确认触发 CLOSE
[ ] 模拟 CVD+OI+盘口三者恶化，确认触发 CLOSE
[ ] 确认快速退出在 4H shrink 检查之前执行

DCA 关闭
[ ] dca_martingale_enabled=false 时，_build_dca_decision 立即返回 HOLD
[ ] 搜索代码，确认无其他路径可以绕过 dca_martingale_enabled 开关

账户级风控收紧
[ ] daily_loss_limit_pct 已更新为 0.03
[ ] consecutive_loss_halt_count=3 逻辑已实现并测试
```

---

## 9. 最短结论

> 这次亏损不是参数问题，是**流程漏洞**。  
> `direction_lock` 写在元数据里但没有真正拦截，  
> DCA 在错误方向尝试加码，  
> 仓位乘以杠杆后名义风险超过 200% 权益，  
> 平仓等到账户级风控触发才兜底。  
>
> 四个问题必须同步修复，任何一个单独修复都不够。

---

*生成于 2026-03-30，基于 `2026-03-29 00:00 ~ 2026-03-30 15:15 (BJ)` 亏损归因报告。*
