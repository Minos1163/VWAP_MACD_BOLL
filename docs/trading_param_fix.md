# 交易系统参数优化 — 代码修改方案

> 目标：提升信号质量，胜率从 66.7% → 70%+，盈利因子从 2.97 → 4.0+  
> 执行顺序：先改配置 → 再改代码 → 回测验证 → 上实盘

> 2026-03-25 同步说明：当前实盘继续使用 `config/trading_config_fund_flow.json`。
> 这份文档原本是旧的参数修正提案，下面未特别标注的“修改前/后”代码块不再作为 live 真值。
> 当前 live 口径以 `4H` 为主方向、`15m` 为软确认、`VWAP >= 0.12`、默认分值门槛 `0.85` 为准。

## 0. 当前 Live 参数基线

```json
"scoring_weights": {
  "weight_1h_direction": 0.00,
  "weight_4h_direction": 0.55,
  "weight_vwap": 0.20,
  "weight_15m_entry": 0.05,
  "weight_volume": 0.20
},
"entry_thresholds": {
  "default": 0.850,
  "red_bar_growing": 0.850,
  "flip_bearish": 0.840,
  "flip_bullish": 0.840,
  "min_entry_score": 0.25
},
"entry_filters": {
  "primary_direction_timeframe": "4h",
  "require_1h_confirmation_when_4h_primary": true,
  "allow_neutral_1h_confirmation": true,
  "light_1h_confirmation_when_4h_primary": true,
  "enable_soft_15m_confirmation_when_4h_primary": true,
  "soft_15m_entry_score": 0.28,
  "min_signal_score": 0.850,
  "min_vwap_score_for_entry": 0.12
}
```

---

## 1. 配置文件修改 `trading_config_fund_flow.json`

### 1.1 权重重分配（4H权重归零，VWAP权重提升）

```json
// 修改前
"weights": {
  "weight_1h_direction":    0.50,
  "weight_4h_enhancement":  0.10,
  "weight_vwap":            0.15,
  "weight_15m_entry":       0.10,
  "weight_volume":          0.15
}

// 修改后
"weights": {
  "weight_1h_direction":    0.50,
  "weight_4h_enhancement":  0.00,
  "weight_vwap":            0.25,
  "weight_15m_entry":       0.10,
  "weight_volume":          0.15
}
// 验证: 0.50 + 0.00 + 0.25 + 0.10 + 0.15 = 1.00 ✓
```

**原因**：4H增强评分在回测30天内全部为 0.00，weight_4h_enhancement=0.10 持续拉低总分却不提供任何信息。VWAP 是亏损最强相关因子（Top10亏损中9笔 vwap_score=0.10-0.12），提权至 0.25 可直接压制弱 VWAP 信号。

---

### 1.2 入场阈值（分信号类型设置动态阈值）

```json
// 修改前
"entry_thresholds": {
  "default":    0.825,
  "entry_filters": {
    "min_signal_score": 0.825
  }
}

// 修改后
"entry_thresholds": {
  "default":          0.850,
  "red_bar_growing":  0.870,
  "flip_bearish":     0.840,
  "flip_bullish":     0.840,
  "entry_filters": {
    "min_signal_score":        0.850,
    "min_vwap_score_for_entry": 0.20
  }
}
```

**原因**：`red_bar_growing` 做多是最大亏损来源（占总亏损68%），但其评分普遍高达0.82-0.96，说明评分体系对其过于宽松。提高专属阈值至0.87可在不影响其他信号的前提下过滤假突破。

---

### 1.3 黑名单扩展

```json
// 修改前
"blacklist": ["TRXUSDT"]

// 修改后
"blacklist": [
  "TRXUSDT",
  "ENAUSDT"
]
```

**原因**：
- `TRXUSDT`：已有，4次交易3次亏损，总亏损 -$201.99
- `ENAUSDT`：单笔最大亏损 -$135.94（最大单笔），且仅发生1次，无统计支撑持续做

---

## 2. 核心代码修改 `decision_engine.py`

### 2.1 权重计算函数 — 移除 4H 无效权重

```python
# 修改前（约 L480-L530）
def calculate_signal_score(self, signal_data: dict) -> float:
    score_1h   = signal_data.get("score_1h", 0.0)
    score_4h   = signal_data.get("score_4h_enhancement", 0.0)
    score_vwap = signal_data.get("vwap_score", 0.0)
    score_15m  = signal_data.get("score_15m", 0.0)
    score_vol  = signal_data.get("volume_score", 0.0)

    w_1h   = self.config.get("weight_1h_direction",   0.50)
    w_4h   = self.config.get("weight_4h_enhancement", 0.10)
    w_vwap = self.config.get("weight_vwap",           0.15)
    w_15m  = self.config.get("weight_15m_entry",      0.10)
    w_vol  = self.config.get("weight_volume",         0.15)

    total = (score_1h   * w_1h  +
             score_4h   * w_4h  +
             score_vwap * w_vwap +
             score_15m  * w_15m  +
             score_vol  * w_vol)
    return round(total, 4)


# 修改后
def calculate_signal_score(self, signal_data: dict) -> float:
    score_1h   = signal_data.get("score_1h", 0.0)
    score_4h   = signal_data.get("score_4h_enhancement", 0.0)
    score_vwap = signal_data.get("vwap_score", 0.0)
    score_15m  = signal_data.get("score_15m", 0.0)
    score_vol  = signal_data.get("volume_score", 0.0)

    w_1h   = self.config.get("weight_1h_direction",   0.50)
    w_4h   = self.config.get("weight_4h_enhancement", 0.00)  # 已归零
    w_vwap = self.config.get("weight_vwap",           0.25)  # 提权
    w_15m  = self.config.get("weight_15m_entry",      0.10)
    w_vol  = self.config.get("weight_volume",         0.15)

    # 4H评分为0时记录警告，供后续修复4H数据采集参考
    if score_4h == 0.0 and w_4h > 0:
        self.logger.debug(f"[score] 4H enhancement=0.0 but weight={w_4h}, check 4H data source")

    total = (score_1h   * w_1h  +
             score_4h   * w_4h  +
             score_vwap * w_vwap +
             score_15m  * w_15m  +
             score_vol  * w_vol)
    return round(total, 4)
```

---

### 2.2 入场判断函数 — 增加 VWAP 硬过滤 + 信号类型动态阈值

```python
# 修改前（约 L516）
def should_enter(self, symbol: str, signal: dict, score: float) -> tuple[bool, str]:
    threshold = self.config.get("entry_thresholds", {}).get("default", 0.85)
    
    if score < threshold:
        return False, f"signal_score_below_threshold({score:.4f}<{threshold})"
    
    return True, "entry_approved"


# 修改后
def should_enter(self, symbol: str, signal: dict, score: float) -> tuple[bool, str]:
    thresholds   = self.config.get("entry_thresholds", {})
    entry_filters = self.config.get("entry_thresholds", {}).get("entry_filters", {})

    signal_type = signal.get("signal_type", "")
    direction   = signal.get("direction", "")
    vwap_score  = signal.get("vwap_score", 0.0)

    # --- 过滤1：VWAP 硬过滤 ---
    min_vwap = entry_filters.get("min_vwap_score_for_entry", 0.0)
    if min_vwap > 0 and vwap_score < min_vwap:
        return False, f"vwap_hard_block({vwap_score:.3f}<{min_vwap})"

    # --- 过滤2：信号类型动态阈值 ---
    # 优先读取信号专属阈值，不存在则回退 default
    threshold = thresholds.get(signal_type, thresholds.get("default", 0.85))

    # --- 过滤3：red_bar_growing 做多需额外条件 ---
    if signal_type == "red_bar_growing" and direction == "long":
        ema_direction = signal.get("ema_direction", "against")
        vwap_dev      = signal.get("vwap_deviation", -99.0)
        if ema_direction == "against":
            return False, f"red_bar_long_blocked(ema_against)"
        if vwap_dev < -3.0:
            return False, f"red_bar_long_blocked(vwap_dev={vwap_dev:.1f}%<-3%)"

    if score < threshold:
        return False, f"signal_score_below_threshold({score:.4f}<{threshold})"

    return True, "entry_approved"
```

---

### 2.3 日志增强 — 记录每次过滤原因（方便后续分析）

```python
# 在 should_enter 调用处（约 L560 附近）修改

# 修改前
approved, reason = self.should_enter(symbol, signal, score)
if not approved:
    hold_reasons.append(reason)
    continue


# 修改后
approved, reason = self.should_enter(symbol, signal, score)
if not approved:
    hold_reasons.append(reason)
    # 新增：结构化日志，方便后续统计分析
    self.logger.info(
        f"[HOLD] {symbol} | type={signal.get('signal_type','')} "
        f"dir={signal.get('direction','')} score={score:.4f} "
        f"vwap={signal.get('vwap_score',0):.3f} reason={reason}"
    )
    continue
```

---

## 3. 回测脚本修改 `backtest_15m.py`

### 3.1 同步新配置参数

```python
# 修改前（回测脚本硬编码参数区域）
BACKTEST_CONFIG = {
    "min_signal_score":        0.825,
    "weight_1h_direction":     0.50,
    "weight_4h_enhancement":   0.10,
    "weight_vwap":             0.15,
    "weight_15m_entry":        0.10,
    "weight_volume":           0.15,
    "min_vwap_score_for_entry": 0.0,
    "blacklist": ["TRXUSDT"],
}


# 修改后
BACKTEST_CONFIG = {
    # --- 阈值 ---
    "min_signal_score":        0.850,
    "entry_thresholds": {
        "default":          0.850,
        "red_bar_growing":  0.870,
        "flip_bearish":     0.840,
        "flip_bullish":     0.840,
    },
    # --- 权重 ---
    "weight_1h_direction":     0.50,
    "weight_4h_enhancement":   0.00,   # 归零
    "weight_vwap":             0.25,   # 提权
    "weight_15m_entry":        0.10,
    "weight_volume":           0.15,
    # --- 过滤 ---
    "min_vwap_score_for_entry": 0.20,  # 先测 0.20，稳定后升至 0.25
    # --- 黑名单 ---
    "blacklist": ["TRXUSDT", "ENAUSDT"],
}
```

### 3.2 回测结果输出 — 增加 VWAP 分段统计

```python
# 在回测报告生成函数末尾追加以下分析块

def print_vwap_analysis(trades: list):
    """按 VWAP 评分分段统计胜率，验证过滤阈值是否合理"""
    buckets = {
        "0.00-0.15": {"win": 0, "loss": 0},
        "0.15-0.20": {"win": 0, "loss": 0},
        "0.20-0.25": {"win": 0, "loss": 0},
        "0.25+":     {"win": 0, "loss": 0},
    }
    for t in trades:
        vs = t.get("vwap_score", 0)
        if   vs < 0.15: key = "0.00-0.15"
        elif vs < 0.20: key = "0.15-0.20"
        elif vs < 0.25: key = "0.20-0.25"
        else:           key = "0.25+"
        if t["pnl"] > 0:
            buckets[key]["win"]  += 1
        else:
            buckets[key]["loss"] += 1

    print("\n=== VWAP 评分分段胜率 ===")
    for k, v in buckets.items():
        total = v["win"] + v["loss"]
        wr    = v["win"] / total * 100 if total else 0
        print(f"  vwap {k:12s} | 总计 {total:3d} | 胜率 {wr:5.1f}% | 盈{v['win']} 亏{v['loss']}")
```

```python
# 在主回测流程末尾调用
print_vwap_analysis(all_trades)
```

---

## 4. 执行顺序 & 验证 Checklist

```
阶段 1：回测验证（不上实盘）
  [ ] 修改 BACKTEST_CONFIG（3.1）
  [ ] 运行 backtest_15m.py，观察信号总数是否 > 60（低于60说明过滤过严）
  [ ] 查看 VWAP 分段统计（3.2），确认 vwap<0.20 区间胜率 < 50%
  [ ] 对比新旧关键指标：胜率 ≥ 68%、盈利因子 ≥ 3.5、最大回撤 < 8%
  [ ] 若信号数过少，将 min_vwap_score_for_entry 从 0.20 回调至 0.18

阶段 2：代码合并（验证通过后）
  [ ] 修改 decision_engine.py（2.1、2.2、2.3）
  [ ] 修改 trading_config_fund_flow.json（1.1、1.2、1.3）
  [ ] 重启交易系统，观察前 24H 日志中 vwap_hard_block 出现频率
  [ ] 确认 ENAUSDT、TRXUSDT 不再出现在开仓日志中

阶段 3：4H数据问题修复（独立任务）
  [ ] 检查 4H OHLCV 数据采集逻辑，确认 score_4h_enhancement 计算是否有 bug
  [ ] 若4H数据修复，用修复后数据重新回测，再决定是否恢复 weight_4h_enhancement
  [ ] 4H权重恢复建议值：0.05-0.08（不超过0.10）
```

---

## 5. 回测目标指标（通过才上实盘）

| 指标 | 当前值 | 目标值 | 说明 |
|------|--------|--------|------|
| 胜率 | 63.2% | ≥ 68% | 低于此值不上实盘 |
| 盈利因子 | 2.97 | ≥ 3.5 | 核心质量指标 |
| 盈亏比 | 1.73 | ≥ 1.8 | 平均盈/亏比 |
| 最大回撤 | 8.39% | ≤ 7% | 风险控制线 |
| 月收益率 | 33.00% | ≥ 40% | 参数优化阶段目标 |
| 信号总数 | 125笔 | ≥ 80笔 | 过少则过滤太严 |

> 月收益 120-200% 的目标需在上述指标达标后，通过阶段性提升仓位比例实现，而非依靠单次参数调整。
