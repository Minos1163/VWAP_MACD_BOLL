# 实盘链路收敛方案：向 backtest_macd_v2 靠拢

**日期**：2026-03-27  
**基准**：`backtest_macd_v2.py` → `+132.40% / 胜率 73.7%`  
**目标**：识别并修复实盘外层链路中最可能稀释 alpha 的环节，输出可操作的参数调整 + 伪代码 + diff

---

## 0. 核心判断框架

在动任何参数前，先确定每个外层机制对 alpha 的作用方向：

| 机制 | 作用方向假设 | 验证方式 |
|---|---|---|
| `pretrade_risk_gate` | ❓ 保护 or 过滤掉好单 | 统计被 gate 拦截的信号，事后看价格方向 |
| `signal_pool` 额外过滤 | ❓ 提升质量 or 降低频次 | 对比回测触发频率 vs 实盘触发频率 |
| `MA10/MACD` 硬过滤 | ❓ 与 V2 信号重合度高则冗余 | 分析 V2 信号中 MA10/MACD 同向比例 |
| `entry window` 约束 | ⚠️ 回测无此约束，直接削减机会集 | 统计被窗口拦截的信号数量占比 |
| DCA / pyramiding | ❓ 回测无此，增量风险未被验证 | 首批部署应关闭 |
| 保护单 SLA 强平 | ⚠️ 可能误杀趋势单 | 检查强平触发时的盈亏分布 |
| 账户冷却 / loss streak | ✅ 回测也有类似 cooldown | 对齐参数即可 |

---

## 1. 最高优先级修改：`pretrade_risk_gate` 松绑

### 1.1 问题诊断

当前 `_apply_pretrade_risk_gate()` 将多个维度（CVD、ATR、回撤、价格变化、权益占用）**串联**做出降级决策。每个维度都有独立的降级阈值，叠加下来极易把回测中会成交的信号打成 `HOLD`。

回测中 **CVD 逻辑默认关闭**（`disable_cvd_decision_logic: true`），而实盘 CVD 可能作为 veto 参与 gate 判断，导致两边有效特征集不同。

### 1.2 修改方案

**原则**：gate 只拦截"明确危险"，不拦截"暂时不确定"。

#### 伪代码（修改前）

```python
def _apply_pretrade_risk_gate(signal, context):
    score = 0
    if cvd_strength < CVD_MIN_THRESHOLD:
        score += CVD_PENALTY          # CVD 弱直接扣分
    if atr_ratio > ATR_MAX_RATIO:
        score += ATR_PENALTY          # 高波动扣分
    if current_drawdown > DD_WARN:
        score += DD_PENALTY           # 持仓回撤扣分
    if equity_usage > EQUITY_WARN:
        score += EQUITY_PENALTY       # 权益占用扣分
    
    if score >= HOLD_THRESHOLD:
        return Action.HOLD            # 任意组合超阈值 -> HOLD
    if score >= EXIT_THRESHOLD:
        return Action.EXIT
    return Action.PASS
```

#### 伪代码（修改后）

```python
def _apply_pretrade_risk_gate(signal, context):
    # === 硬性否决：仅保留真正高危场景 ===
    
    # 1. ATR 极端值：价格已经在剧烈震荡，滑点不可控
    if atr_ratio > ATR_HARD_BLOCK:          # 从原有阈值提高 30~50%
        log("gate: ATR extreme block")
        return Action.HOLD
    
    # 2. 权益占用过高：资金已经接近满仓，无法再开
    if equity_usage > EQUITY_HARD_BLOCK:    # 维持原值，这是资金安全底线
        log("gate: equity overload block")
        return Action.HOLD
    
    # 3. 持仓处于极端亏损：对已有持仓才做 EXIT，对新开仓不干预
    if is_existing_position and current_drawdown > DD_HARD_EXIT:
        log("gate: drawdown force exit")
        return Action.EXIT
    
    # === CVD：降级为参考，不做否决 ===
    # 与回测保持一致：CVD 弱不阻止入场，只记录 warning
    if cvd_strength < CVD_MIN_THRESHOLD:
        log("gate: CVD weak, pass through (aligned with backtest)")
        # 不返回 HOLD，继续执行
    
    # 其余情况全部放行
    return Action.PASS
```

#### diff（config 层）

```diff
# fund_flow_config.yaml 或对应配置段

pretrade_risk_gate:
-  cvd_veto_enabled: true
-  cvd_min_threshold: 0.3
-  atr_ratio_hold_threshold: 1.5
-  atr_ratio_hard_block: 2.5
-  equity_usage_warn: 0.65
-  equity_usage_block: 0.80
-  dd_warn_threshold: 0.04
-  dd_exit_threshold: 0.08
-  gate_score_hold: 2
-  gate_score_exit: 4

+  cvd_veto_enabled: false          # 与回测对齐，CVD 不参与 veto
+  cvd_min_threshold: 0.3           # 保留记录，但不影响决策
+  atr_ratio_hold_threshold: 999    # 实质禁用 ATR 软拦截
+  atr_ratio_hard_block: 3.5        # 硬拦截阈值上调，只拦截极端行情
+  equity_usage_warn: 0.70          # 宽松 warn（不影响决策）
+  equity_usage_block: 0.85         # 硬拦截轻微上调
+  dd_warn_threshold: 999           # 禁用回撤软警告对新开仓的影响
+  dd_exit_threshold: 0.10          # 现有持仓平仓阈值小幅宽松
+  gate_score_hold: 999             # 实质禁用积分式 HOLD
+  gate_score_exit: 999             # 实质禁用积分式 EXIT（改为硬规则）
```

---

## 2. 中优先级修改：`MA10/MACD` 硬过滤对齐

### 2.1 问题诊断

`_apply_ma10_macd_entry_filter()` 是 V2 信号之外的**额外**结构过滤。  
`MACDStrategyV2Engine` 内部本身已经有 MACD 动量判断，外层再叠一层 MA10/MACD 约束，相当于**双重 MACD 过滤**。  
回测中这层过滤不存在，可能导致实盘触发频率显著低于回测。

### 2.2 修改方案

**原则**：如果 V2 信号已经内含 MACD 条件，外层过滤只保留最宽松的版本或直接关闭。

#### 伪代码（修改前）

```python
def _apply_ma10_macd_entry_filter(signal, klines):
    ma10 = calc_ma(klines, 10)
    macd_hist = calc_macd_hist(klines)
    
    if signal.direction == LONG:
        if price < ma10:              # 价格在 MA10 下方，禁止做多
            return FilterResult.REJECT
        if macd_hist[-1] < 0:         # MACD 柱负数，禁止做多
            return FilterResult.REJECT
    
    if signal.direction == SHORT:
        if price > ma10:
            return FilterResult.REJECT
        if macd_hist[-1] > 0:
            return FilterResult.REJECT
    
    return FilterResult.PASS
```

#### 伪代码（修改后）

```python
def _apply_ma10_macd_entry_filter(signal, klines):
    # 选项 A（推荐首批部署）：完全关闭，与回测对齐
    if not config.ma10_macd_filter_enabled:
        return FilterResult.PASS
    
    # 选项 B（保守做法）：只保留极端结构过滤，宽松化判断
    ma10 = calc_ma(klines, 10)
    macd_hist = calc_macd_hist(klines)
    price_deviation_from_ma10 = abs(price - ma10) / ma10
    
    if signal.direction == LONG:
        # 仅在价格显著偏离 MA10（超过 N%）且 MACD 强烈背离时才拒绝
        # 轻微偏离直接放行
        if price < ma10 * (1 - MA10_HARD_REJECT_BAND) and macd_hist[-1] < MACD_HARD_REJECT_THRESHOLD:
            return FilterResult.REJECT
    
    if signal.direction == SHORT:
        if price > ma10 * (1 + MA10_HARD_REJECT_BAND) and macd_hist[-1] > -MACD_HARD_REJECT_THRESHOLD:
            return FilterResult.REJECT
    
    return FilterResult.PASS
```

#### diff（config 层）

```diff
entry_filters:
-  ma10_macd_filter_enabled: true
-  ma10_reject_on_wrong_side: true     # 价格在 MA10 错误侧即拒绝
-  macd_hist_reject_on_negative: true  # MACD 柱方向错误即拒绝

+  ma10_macd_filter_enabled: false     # 首批部署：关闭，与回测对齐
+  # 若需保留，使用宽松版本：
+  # ma10_hard_reject_band: 0.03       # 仅在偏离 3% 以上才考虑拒绝
+  # macd_hard_reject_threshold: -0.002 # 仅在强烈背离时才拒绝
```

---

## 3. 高优先级修改：关闭 DCA 和 Winner Pyramiding

### 3.1 问题诊断

回测中不存在 DCA 和 pyramiding 逻辑。这两个机制会：

- 改变实际持仓的均价和规模，使实盘行为与回测**不可比**
- 在首批部署中引入未经回测验证的额外风险
- 使链路复杂度上升，排查问题困难

### 3.2 修改方案

#### diff（config 层）

```diff
position_management:
-  dca_enabled: true
-  dca_max_stages: 3
-  dca_stage_multiplier: 1.5
-  dca_drawdown_trigger: 0.02
-  winner_pyramiding_enabled: true
-  pyramiding_profit_threshold: 0.015
-  pyramiding_max_add_times: 2

+  dca_enabled: false                  # 首批实盘：关闭 DCA
+  dca_max_stages: 0
+  winner_pyramiding_enabled: false    # 首批实盘：关闭加仓
+  # 待实盘运行 2~4 周、基础链路验证后再逐步开启
```

#### 代码层保护（`_build_dca_decision` 入口）

```diff
def _build_dca_decision(self, symbol, position, context):
+   # 首批部署保护：强制关闭 DCA
+   if not self.config.get("dca_enabled", False):
+       return DecisionResult(action=Action.HOLD, reason="dca_disabled_for_initial_deploy")
+   
    # 原有逻辑...
    if position.drawdown < self.config.dca_drawdown_trigger:
        return DecisionResult(action=Action.HOLD)
    # ...
```

---

## 4. 中优先级修改：保护单 SLA 强平阈值宽松化

### 4.1 问题诊断

实盘中若 TP/SL 保护单挂单失败、且 SLA 超时，bot 会触发强平。这在正常趋势行情中会错误地打断持仓，回测里不存在此路径。

高频误杀场景：
- 交易所 API 短暂超时（非真实仓位风险）
- TP/SL 参数被交易所格式化拒绝但仓位安全

### 4.2 修改方案

#### 伪代码（修改前）

```python
def _handle_symbol_protection_and_sla(symbol, position):
    if not has_tp_sl(symbol):
        try_repair_protection(symbol)
        if repair_failed:
            if time_since_open > SLA_TIMEOUT_SECONDS:
                force_close(symbol, reason="protection_sla_timeout")  # 立刻强平
```

#### 伪代码（修改后）

```python
def _handle_symbol_protection_and_sla(symbol, position):
    if not has_tp_sl(symbol):
        repair_result = try_repair_protection(symbol)
        
        if repair_result.success:
            return  # 修复成功，不干预
        
        # === 修改：强平前增加安全性评估 ===
        time_unprotected = now() - position.open_time
        
        # 仅在同时满足以下条件时才强平：
        # 1. 保护单缺失时间超过 SLA（原有逻辑）
        # 2. 当前持仓处于亏损状态（避免误杀盈利趋势单）
        # 3. API 连通性正常（排除交易所瞬断）
        if (time_unprotected > SLA_TIMEOUT_SECONDS
                and position.unrealized_pnl_pct < -PROTECTION_SLA_PNL_GRACE   # 新增
                and api_health_check_ok()):                                     # 新增
            log_warning("protection SLA force close triggered")
            force_close(symbol, reason="protection_sla_timeout")
        else:
            # 不满足条件：记录告警但不强平，下轮继续重试修复
            log_warning(f"protection missing but grace active: pnl={position.unrealized_pnl_pct:.2%}")
            schedule_retry_repair(symbol)
```

#### diff（config 层）

```diff
protection_sla:
-  sla_timeout_seconds: 120           # 2分钟超时即强平
-  force_close_on_sla_breach: true
-  pnl_grace_threshold: null          # 无 PNL 豁免

+  sla_timeout_seconds: 300           # 宽松到 5 分钟
+  force_close_on_sla_breach: true
+  pnl_grace_threshold: -0.005        # 新增：持仓亏损超过 -0.5% 才允许强平
+                                     # 盈利仓位不因保护单 SLA 被强平
+  api_health_check_before_force: true # 新增：强平前先确认 API 正常
```

---

## 5. 低优先级修改：`entry_window` 约束对齐

### 5.1 问题诊断

回测无 entry_window 约束，实盘若配置了入场时间窗口（如避免某些时段），会直接削减机会集，导致触发频率低于回测。

### 5.2 修改方案

#### diff（config 层）

```diff
entry_window:
-  enabled: true
-  allowed_hours_utc: [2, 3, 4, 8, 9, 10, 14, 15, 16, 20, 21, 22]  # 仅允许特定小时

+  enabled: false    # 首批部署：关闭时间窗口约束，与回测对齐
+  # 若需要保留，至少确保覆盖回测数据中主要信号分布的时段
```

---

## 6. 信号阈值对齐检查

### 6.1 核心参数对比表

确保以下参数与 `backtest_macd_v2.py` 的 `build_strategy_config()` 完全一致：

| 参数 | 回测值（需从代码确认） | 实盘当前值 | 操作 |
|---|---|---|---|
| `long_open_threshold` | 从回测配置读取 | — | 强制对齐 |
| `short_open_threshold` | 从回测配置读取 | — | 强制对齐 |
| `close_threshold` | 从回测配置读取 | — | 强制对齐 |
| `take_profit_pct` | 从回测配置读取 | — | 强制对齐 |
| `stop_loss_pct` | 从回测配置读取 | — | 强制对齐 |
| `entry_slippage` | 从回测配置读取 | — | 强制对齐 |
| `max_positions` | 从回测配置读取 | — | 对齐（实盘叫 `max_active_symbols`） |
| `entry_cooldown_bars` | 从回测配置读取 | — | 对齐 |

#### 验证脚本（伪代码）

```python
def validate_config_alignment(backtest_config, live_config):
    """
    部署前运行此函数，确保关键参数一致。
    差异输出到日志，由人工确认是否接受。
    """
    KEY_PARAMS = [
        "long_open_threshold",
        "short_open_threshold", 
        "close_threshold",
        "take_profit_pct",
        "stop_loss_pct",
        "entry_slippage",
        "reverse_close_confirm_bars",
    ]
    
    mismatches = []
    for key in KEY_PARAMS:
        bt_val = backtest_config.get(key)
        live_val = live_config.get(key)
        if bt_val != live_val:
            mismatches.append({
                "param": key,
                "backtest": bt_val,
                "live": live_val,
                "delta_pct": abs(bt_val - live_val) / bt_val * 100 if bt_val else "N/A"
            })
    
    if mismatches:
        raise ConfigMismatchError(
            f"发现 {len(mismatches)} 处参数不一致，部署前请确认：\n"
            + "\n".join(f"  {m['param']}: backtest={m['backtest']}, live={m['live']}" 
                        for m in mismatches)
        )
    
    return True
```

---

## 7. 监控：alpha 稀释率追踪

部署后需要追踪实盘 vs 回测的信号命中率，量化每层漏斗的稀释比例。

### 7.1 信号漏斗监控伪代码

```python
class AlphaDilutionTracker:
    """
    追踪每层过滤对信号数量的影响。
    目标：定位实盘触发率低于回测的根因。
    """
    
    def record_signal_flow(self, symbol, signal):
        counters = {
            "v2_engine_raw":        0,  # V2 引擎原始信号数
            "after_entry_window":   0,  # 经过时间窗口过滤后
            "after_ma10_macd":      0,  # 经过 MA10/MACD 过滤后
            "after_pretrade_gate":  0,  # 经过 pretrade_risk_gate 后
            "after_capacity_check": 0,  # 经过容量/max_symbols 检查后
            "actually_executed":    0,  # 实际下单数
        }
        # 每层递减对比，找到最大稀释层
    
    def compute_dilution_report(self, window_hours=24):
        """
        输出每层稀释率，与回测触发频率对比。
        若某层稀释率 > 30%，标记为"需要审查"。
        """
        pass
```

### 7.2 关键告警阈值

```yaml
# 监控配置
alpha_dilution_monitor:
  enabled: true
  alert_if_layer_dilution_exceeds: 0.30   # 单层稀释超 30% 触发告警
  alert_if_daily_signal_count_below: N    # N = 回测日均触发数 × 0.5
  report_interval_hours: 6
```

---

## 8. 分阶段部署路线图

### Phase 1（第 1~2 周）：最小化外层干预，贴近回测

| 动作 | 具体修改 | 风险 |
|---|---|---|
| 关闭 CVD veto | `cvd_veto_enabled: false` | 低。与回测对齐 |
| 关闭 MA10/MACD 硬过滤 | `ma10_macd_filter_enabled: false` | 低。回测无此过滤 |
| 关闭 DCA | `dca_enabled: false` | 低。回测无 DCA |
| 关闭 pyramiding | `winner_pyramiding_enabled: false` | 低。回测无加仓 |
| 关闭 entry_window | `entry_window.enabled: false` | 低。回测无窗口 |
| 宽松 SLA 强平 | `sla_timeout_seconds: 300` + PNL grace | 低。减少误杀 |
| 对齐核心参数 | 运行 `validate_config_alignment()` | 无。验证性操作 |

### Phase 2（第 3~4 周）：数据驱动地重新开启过滤层

基于 Phase 1 采集到的实盘数据：

- 统计 `pretrade_risk_gate` 在 Phase 1 中没有被触发的情况下，亏损信号的比例
- 如果 CVD 弱信号的胜率明显低于均值，考虑重新开启 CVD soft filter（非 veto）
- 如果 MA10 偏离信号的胜率明显低，考虑重新开启但放宽阈值

### Phase 3（第 5 周后）：逐步引入 DCA，验证增量价值

- 先开启 `dca_max_stages: 1`（单次加仓）
- 观察 DCA 触发的仓位最终损益分布
- 仅当 DCA 仓位的损益 > 原始仓位的损益均值时，才扩大至 `max_stages: 2`

---

## 9. 修改优先级总览

| 优先级 | 修改项 | 预期效果 | 实施难度 |
|---|---|---|---|
| 🔴 立刻 | 关闭 CVD veto | 恢复被 CVD 过滤的信号 | 配置修改，1 分钟 |
| 🔴 立刻 | 关闭 MA10/MACD 硬过滤 | 恢复被双重 MACD 过滤的信号 | 配置修改，1 分钟 |
| 🔴 立刻 | 关闭 DCA + pyramiding | 链路可解释，与回测等价 | 配置修改，1 分钟 |
| 🔴 立刻 | 对齐核心阈值参数 | 确保入场/出场条件与回测一致 | 验证 + 配置，30 分钟 |
| 🟡 本周 | 宽松 SLA 强平逻辑 | 减少趋势单被误杀 | 代码修改，1~2 小时 |
| 🟡 本周 | 关闭 entry_window | 恢复全时段信号 | 配置修改，1 分钟 |
| 🟡 本周 | 部署 alpha 稀释监控 | 实时发现下一处稀释根因 | 代码修改，2~4 小时 |
| 🟢 Phase 2 | ATR gate 阈值校准 | 基于实盘数据精调 | 数据驱动，下阶段 |
| 🟢 Phase 3 | 重新引入 DCA（单阶段） | 验证 DCA 增量价值 | 代码 + 数据，下阶段 |

---

## 10. 最重要的一句话

> **第一步不是"让实盘跑出 132%"，而是"让实盘的每一笔交易都能在回测中找到对应的信号根因"。**
>
> 只要外层链路对信号的处理方式与回测等价，实盘的损益才有解释性。有解释性的损益才能被改进。

---

*本文档自动生成于 2026-03-27，基于 `实盘交易链路梳理与 backtest_macd_v2.py 差异说明`。*
