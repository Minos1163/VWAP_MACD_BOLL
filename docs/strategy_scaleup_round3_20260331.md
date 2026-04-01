# MACD V2 Round 3 — 扩量方案

**日期**: 2026-03-31  
**当前基线**: 90 天 7 笔 / ≈ 2.3 笔/月 / 胜率 71.43% / MDD 0.38%  
**目标**: 30 天 200-300 笔 / 胜率 ≥ 75%  
**规模缺口**: 需要扩大 **87-130 倍**

---

## 零、先说结论

**目标 200-300 笔/月 + 75% 胜率，不是调参能解决的，是架构问题。**

当前配置的设计哲学是"质量优先、宁少勿滥"，  
目标配置的设计哲学是"多信号、多标的、分层质量控制"。  

两者之间的鸿沟，需要拆解成 4 个维度同时扩展：

```
维度一：信号类型  1 种 → 5-6 种          预估贡献倍数：×4
维度二：交易标的  ~4 个有效 → 40+ 个      预估贡献倍数：×8
维度三：分数窗口  全局单窗口 → 按类型校准   预估贡献倍数：×3
维度四：门槛松紧  多道硬拦 → 分层软硬结合   预估贡献倍数：×2

理论上限：4 × 8 × 3 × 2 ≈ 192 倍 → 进入 200-400 笔区间
实际约束（75% WR）：每一步放宽都需要验证胜率不崩
```

---

## 一、缺口拆解：2.3 笔/月 → 200-300 笔/月

### 1.1 当前信号在哪一层被拦截（估算）

没有漏斗数据，但可以基于当前配置推断：

```
假设 90 天的原始潜在信号量 = N（未知）

估算各层拦截比例：

  原始 MACD 信号（所有类型）         N         100%
  → 仅保留 flip_bullish              N × 0.20   20%   ← 信号类型单一
  → score window [0.80, 0.87]        N × 0.05    5%   ← 极窄窗口
  → symbol_overrides (无 DOGE/BCH/AAVE) × 0.70  3.5%
  → entry_filters (shrink/vwap/等)   × 0.50    1.75%
  → L1 (regime=TREND/ADX/ATR)        × 0.40    0.70%
  → L2/L3 flow+microstructure        × 0.60    0.42%
  → pretrade_risk_gate               × 0.80    0.34%
  → max_active_symbols 容量上限       × 0.70    0.24%

  最终成交 ≈ 原始信号的 0.24%

  如果要达到 200-300 笔/月：
  当前 2.3 笔 = 0.24% × N → N ≈ 958 个原始信号/月
  目标 250 笔 = 0.24% × N' → N' ≈ 104,000（不可能）

  → 必须提高通过率，目标通过率 ≈ 5-8%（提升 20-33 倍）
```

### 1.2 各维度扩展的胜率风险分级

```
风险低（扩量 + 胜率影响小）：
  ✓ 增加交易标的数量（symbol pool）
  ✓ 启用 stable_bull / stable_bear continuation
  ✓ 为每个信号类型设独立分数阈值

风险中（需要回测验证）：
  ~ 放宽 preflip_trial_min_shrink_pct
  ~ 放宽 L1 ADX 下限
  ~ 恢复 DOGE/BCH/AAVE（加质量门）
  ~ 拓宽 score window 上限

风险高（慎重）：
  ✗ 放宽 regime = TREND 要求
  ✗ 取消 L2/L3 gate
  ✗ 提高杠杆（当前胜率未稳）
```

---

## 二、第一优先：信号漏斗诊断（必须先做）

**在不知道哪一层拦截了最多信号之前，所有扩量操作都是盲目的。**

### 2.1 最小版漏斗 instrumentation 实现

```python
# scripts/signal_funnel_analyzer.py
# 在现有 backtest 框架外，单独跑一遍"只记录不成交"的分析

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class FunnelCounter:
    """
    轻量信号漏斗计数器，每一层记录：通过数 / 拦截数 / 拦截原因分布
    """
    layers: dict = field(default_factory=lambda: defaultdict(lambda: {
        "pass": 0, "reject": 0, "reasons": defaultdict(int)
    }))

    def log(self, layer: str, passed: bool, reason: str = ""):
        key = "pass" if passed else "reject"
        self.layers[layer][key] += 1
        if not passed and reason:
            self.layers[layer]["reasons"][reason] += 1

    def report(self, output_path: Optional[str] = None) -> dict:
        report = {}
        total_input = None
        for layer, counts in self.layers.items():
            total = counts["pass"] + counts["reject"]
            if total_input is None:
                total_input = total
            pass_rate = counts["pass"] / total * 100 if total > 0 else 0
            retain_rate = counts["pass"] / total_input * 100 if total_input else 0
            report[layer] = {
                "pass":        counts["pass"],
                "reject":      counts["reject"],
                "pass_rate":   f"{pass_rate:.1f}%",
                "retain_rate": f"{retain_rate:.1f}%",   # 相对原始信号的保留比
                "top_reasons": dict(sorted(
                    counts["reasons"].items(), key=lambda x: -x[1]
                )[:5])
            }
        if output_path:
            with open(output_path, "w") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
        return report

# 使用方式：在 decision_engine.py 的每个 gate 判断点注入
funnel = FunnelCounter()

# 示例：在 L1 gate 处
l1_pass, l1_reason = check_l1_structural_gate(bar, signal_type)
funnel.log("L1_structural", l1_pass, l1_reason)
if not l1_pass:
    continue

# 在最终成交处
funnel.log("final_fill", True)
```

### 2.2 漏斗报告目标格式

运行后应该能得到如下表格（30 天窗口）：

```
Gate 层                            通过数   拦截数   通过率   保留率
─────────────────────────────────────────────────────────────────
0. 原始 MACD 信号（所有类型）       XXXX      -      100%    100%
1. 信号类型过滤                     XXXX    XXXX     XX%     XX%
2. flip_bullish_trial score_window  XXXX    XXXX     XX%     XX%
3. symbol_overrides                 XXXX    XXXX     XX%     XX%
4. entry_filters (shrink/vwap/etc)  XXXX    XXXX     XX%     XX%
5. L1 structural (regime/ADX/ATR)   XXXX    XXXX     XX%     XX%   ← 期望这里是大瓶颈
6. L2 flow (CVD/OI/VWAP 2/3)        XXXX    XXXX     XX%     XX%
7. L3 microstructure (2/3)          XXXX    XXXX     XX%     XX%
8. pretrade_risk_gate               XXXX    XXXX     XX%     XX%
9. capacity (max_active_symbols)    XXXX    XXXX     XX%     XX%
10. 最终成交                            7      -       -      0.X%
```

**拿到这张表之后，才能知道扩量的正确作用点。**

### 2.3 漏斗注入点地图

```
需要在以下代码位置注入 funnel.log()：

文件：src/fund_flow/decision_engine.py
  - 进入 entry_hard_gates 之前       → layer "0_raw_signal"
  - signal_type 过滤后               → layer "1_signal_type_filter"
  - symbol_overrides check 后        → layer "3_symbol_overrides"
  - L1 gate 后                       → layer "5_L1_structural"
  - L2 gate 后                       → layer "6_L2_flow"
  - L3 gate 后                       → layer "7_L3_microstructure"
  - pretrade_risk_gate 后            → layer "8_pretrade_gate"
  - capacity check 后                → layer "9_capacity"
  - 最终下单                         → layer "10_final_fill"

文件：src/fund_flow/macd_strategy_v2.py
  - threshold_check 后               → layer "2_score_window"
  - entry_filters check 后           → layer "4_entry_filters"
```

---

## 三、维度一：信号类型扩展

### 3.1 当前各信号类型的开放状态

```
信号类型                      当前状态        预估额外信号/月
────────────────────────────────────────────────────────
flip_bullish (non-trial)      ？ 未确认        +20-40
flip_bullish trial            ✓ 开启          当前 2.3 笔
flip_bearish                  ？ 未确认        +20-40
stable_bull_continuation      ✗ 禁用          +30-60（3 月多头行情）
stable_bear_continuation      ✓ 开启但无成交   +10-20（需放宽 ADX）
preflip_trial_short           ？ 未确认        +15-30
────────────────────────────────────────────────────────
合计（全开放后估算）                          +95-190 笔/月
```

### 3.2 stable_bull_continuation 开启方案

```diff
# fund_flow.macd_mtf_strategy_v2.entry_filters

- enable_stable_bull_continuation: false
+ enable_stable_bull_continuation: true

# 门槛设置（与 stable_bear 对称）
+ stable_bull_continuation_min_signal_score: 0.83
+ stable_bull_continuation_min_vwap_score:   0.10
+ stable_bull_continuation_min_adx_1h:       25.0
+ stable_bull_continuation_min_4h_bars:      2
```

**为什么 stable_bull 是当前最安全的第一个扩展信号：**
```
- 3 月行情整体偏多，stable_bull 应该有最多机会
- 做多延续（顺势）信号，胜率天然高于逆势的 trial
- 与当前 flip_bullish 信号方向一致，不引入新的做空风险
- 门槛可以直接对称 stable_bear，参数逻辑已经验证过
```

### 3.3 flip_bearish 开放评估

```
flip_bearish = 4H 翻空后的正式做空入场
当前状态需要确认：
  - disable_flip_bearish_entries = ? （需要检查 config）
  - 如果当前是 true，可以解禁并设置与 flip_bullish 对称的门槛

建议门槛（先保守）：
  flip_bearish_min_signal_score:   0.84   # 与 flip_bullish 一致
  flip_bearish_min_vwap_score:     0.15
  flip_bearish_require_15m_growing: true  # 对应 15m 动量确认
```

### 3.4 各信号类型独立分数阈值（取代全局 score window）

**当前问题**：全局 score window [0.80, 0.87] 是从 flip_bullish trial 的 1 个月数据里推导出来的，强加到所有信号类型上会造成误杀。

```diff
# 废弃全局 score window，改为按信号类型独立设置

- flip_bullish_trial_score_window_enabled: true
- flip_bullish_trial_score_min: 0.80
- flip_bullish_trial_score_max: 0.87

+ signal_type_score_thresholds:
+   flip_bullish_trial:
+     min: 0.800
+     max: 0.880          # 略微放宽上限，给 90 天数据重新校准留空间
+   flip_bullish_full:
+     min: 0.840          # 正式入场要求更高
+     max: null           # 无上限
+   flip_bearish_trial:
+     min: 0.800
+     max: 0.880
+   flip_bearish_full:
+     min: 0.840
+     max: null
+   stable_bull_continuation:
+     min: 0.830
+     max: null           # 延续信号不需要上限（分高 = 趋势强 = 好）
+   stable_bear_continuation:
+     min: 0.830
+     max: null
```

**伪代码：**

```python
def get_score_threshold(signal_type: str, entry_type: str, cfg) -> tuple[float, Optional[float]]:
    """
    按信号类型返回独立分数 [min, max] 窗口
    max=None 表示无上限
    """
    key = f"{signal_type}_{entry_type}"   # e.g. "flip_bullish_trial"
    type_cfg = cfg.signal_type_score_thresholds.get(key, {})

    score_min = type_cfg.get("min", cfg.entry_thresholds.default)   # fallback 全局
    score_max = type_cfg.get("max", None)

    return score_min, score_max

def check_signal_score(signal_score: float, signal_type: str,
                       entry_type: str, cfg) -> tuple[bool, str]:
    lo, hi = get_score_threshold(signal_type, entry_type, cfg)
    if signal_score < lo:
        return False, f"SCORE_LOW: {signal_score:.3f} < {lo}"
    if hi is not None and signal_score > hi:
        return False, f"SCORE_HIGH: {signal_score:.3f} > {hi} ({signal_type} ceiling)"
    return True, "SCORE_OK"
```

---

## 四、维度二：交易标的扩展

### 4.1 当前有效标的分析

```
90 天回测实际成交标的：
  POLUSDT:  4 笔（57%的成交来自单一标的）
  ATOMUSDT: 1 笔（主要亏损源）
  FETUSDT:  1 笔（主要盈利源）
  ALGOUSDT: 1 笔

禁用的标的：
  DOGEUSDT / BCHUSDT / AAVEUSDT

潜在问题：
  - 实际只有 4 个标的在产生信号，symbol pool 可能本身就很小
  - 需要确认当前的 symbol_list 配置里有多少标的
```

### 4.2 Symbol Pool 扩展建议

```
目标：将有效交易标的从 ~10 扩展到 40+

扩展原则：
  1. 流动性优先：选择 24H 成交量 > 1 亿 USDT 的永续合约
  2. 不同板块分散：Layer1、DeFi、AI、基础设施、MEME（有门槛）
  3. 避免高度相关的标的同时持仓（相关系数 > 0.85 不同时开）

建议加入的高流动性标的（估算）：
  Layer1:  ETH、BNB、SOL、AVAX、ADA、DOT、NEAR、APT、SUI
  DeFi:    LINK、UNI、AAVE(加门槛)、MKR、SNX
  AI/新兴: FET、RENDER、WLD、TAO
  基础设施: MATIC/POL、OP、ARB、ATOM、INJ
  中高流动: XRP、LTC、BCH(加门槛)、ETC

预计有效标的：30-50 个
预计贡献额外信号：按当前 4 标的 2.3 笔/月推算，40 标的 → 约 23 笔/月（基线信号类型不变时）
```

### 4.3 DOGE/BCH/AAVE 有条件恢复

```diff
# 从"完全禁用"迁移到"高质量门"

  symbol_overrides:
    DOGEUSDT:
-     disable_flip_bullish_trial: true
-     preflip_trial_min_signal_score_override: 0.90
+     disable_flip_bullish_trial: false
+     flip_bullish_trial_score_min_override:  0.82   # 高于全局 0.80
+     flip_bullish_trial_score_max_override:  0.86   # 收紧上限（历史亏损区间）
+     require_cvd_confirm_for_trial:          true   # 必须 CVD 确认
+     preflip_trial_min_adx_4h_override:      32     # 高于全局，要求强趋势

    BCHUSDT:   (同上配置)
    AAVEUSDT:  (同上配置)

# 上线条件：
# 1. 漏斗诊断显示这三个币的信号在 score 和 CVD 门后质量达标
# 2. 用独立 90 天 ablation 验证这三个币的胜率 ≥ 55%
```

---

## 五、维度三 & 四：门槛系统性松紧调整

### 5.1 L1 Structural Gate — 核心瓶颈之一

```
当前 L1 要求（全部满足）：
  regime = TREND    ← 最严
  ADX ≥ 22
  0.006 ≤ ATR% ≤ 0.020
  spread_bps ≤ 0.0008

预估拦截比例：根据币圈行情，regime=TREND 的时间占比约 30-40%
即约 60-70% 的时间被 L1 拦截
```

```diff
# 分阶段放宽 L1

# 阶段 1（立即）：只放 ADX 下限
- ADX ≥ 22
+ ADX ≥ 18     # 允许趋势启动早期

# 阶段 2（验证 MDD 后）：ATR 上限放宽
- ATR% ≤ 0.020
+ ATR% ≤ 0.025  # 配合 ATR 自动缩仓（高 ATR 时仓位 × 0.65）

# 阶段 3（稳健验证后）：regime 条件分信号类型放宽
+ def check_regime_gate(regime, signal_type) -> bool:
+     if signal_type in ("stable_bull_continuation", "stable_bear_continuation"):
+         return regime in ("TREND", "TRANSITION")   # 延续信号允许 TRANSITION
+     elif signal_type in ("flip_bullish_trial", "flip_bearish_trial"):
+         return regime in ("TREND", "TRANSITION")   # preflip 也允许 TRANSITION
+     else:
+         return regime == "TREND"   # full-size flip 仍要求确认 TREND
```

### 5.2 preflip_trial_min_shrink_pct — 主要信号数量阀门

```
当前值：
  long:  0.60（多头需要缩量 ≥ 60%，极严）
  short: 0.30

预估影响：大量 4H 刚开始收敛但缩量 < 60% 的优质预翻转信号被拦截
```

```diff
# 分步放宽

# 阶段 1
- preflip_trial_min_shrink_pct_long:  0.60
+ preflip_trial_min_shrink_pct_long:  0.45   # 等价于：缩量 ≥ 45% 即可

# 阶段 2（验证后）
+ preflip_trial_min_shrink_pct_long:  0.35

# short 方向
- preflip_trial_min_shrink_pct_short: 0.30
+ preflip_trial_min_shrink_pct_short: 0.22
```

### 5.3 min_vwap_score_for_entry — VWAP 硬拦门

```diff
- min_vwap_score_for_entry: 0.15  （flip_bullish 专用）
+ min_vwap_score_for_entry: 0.10  # 放宽，VWAP 权重 0.20，0.10/0.20=50% 即可通过

- min_vwap_score_for_entry: 0.14  （全局）
+ min_vwap_score_for_entry: 0.09
```

### 5.4 L2 / L3 Gate — 针对 continuation 信号放宽

```python
def check_l2_flow_gate(checks: dict, signal_type: str) -> tuple[bool, str]:
    """
    L2 流向层：at least min_pass of 3 checks
    continuation 信号要求可以低于 flip 信号
    """
    passed = sum([checks["cvd_ok"], checks["oi_ok"], checks["vwap_ok"]])
    
    # 对延续信号稍微放宽
    if signal_type in ("stable_bull_continuation", "stable_bear_continuation"):
        min_pass = 1   # 延续信号：1/3 即可（趋势本身就是确认）
    elif signal_type in ("preflip_trial",):
        min_pass = 2   # trial 保持 2/3
    else:
        min_pass = 2   # flip full-size 保持 2/3

    if passed < min_pass:
        return False, f"L2_FLOW: {passed}/{3} < {min_pass}"
    return True, "L2_OK"
```

---

## 六、max_active_symbols 容量扩展

```
当前：max_active_symbols = 4
目标：200-300 笔/月，不可能全部串行

关键认知：
  200-300 笔/月 = 7-10 笔/天
  如果平均持仓时间 = 30 分钟，同时持仓数 = 7-10 × (0.5/24) ≈ 0.15-0.21
  也就是平均任何时刻只有 0-1 个持仓在跑

  但高峰期（行情活跃时）可能同时出现 3-5 个信号
  → max_active_symbols 需要扩展到 6-8
```

```diff
# fund_flow 顶层参数

- max_active_symbols: 4
+ max_active_symbols: 6    # 阶段 1
# 阶段 2（验证后）: 8

# 配合调整：降低单标的基础仓位，总暴露不变
- default_target_portion: 0.5
+ default_target_portion: 0.15   # 更多标的 × 更小仓位 = 总暴露可控

# 但 max_single_trade_nominal_ratio 需要对应调整：
+ pretrade_risk_gate.equity_usage_block: 0.70   # 稍微放宽（从 0.60）
```

---

## 七、胜率保护机制（防止扩量导致胜率崩盘）

**关键矛盾**：扩量 → 更多边际信号 → 胜率可能下降  
**解决路径**：每个新信号类型独立测试，确认胜率 ≥ 60% 才正式加入

### 7.1 信号类型独立胜率验证框架

```python
# 每个新信号类型上线前必须通过这个验证
def validate_signal_type_quality(
    signal_type: str,
    backtest_trades: pd.DataFrame,
    min_count: int = 30,
    min_win_rate: float = 0.60
) -> dict:
    """
    新信号类型的最低质量标准验证
    """
    subset = backtest_trades[backtest_trades["signal_type"] == signal_type]
    
    if len(subset) < min_count:
        return {"status": "INSUFFICIENT_DATA", "count": len(subset)}
    
    win_rate = (subset["pnl"] > 0).mean()
    profit_factor = subset[subset["pnl"] > 0]["pnl"].sum() / abs(
        subset[subset["pnl"] < 0]["pnl"].sum()
    )
    
    passed = win_rate >= min_win_rate and profit_factor >= 1.2
    
    return {
        "status": "PASS" if passed else "FAIL",
        "signal_type": signal_type,
        "count": len(subset),
        "win_rate": f"{win_rate:.2%}",
        "profit_factor": f"{profit_factor:.2f}",
    }
```

### 7.2 Symbol 级别的持续风控

```python
def check_symbol_rolling_quality(
    symbol: str,
    recent_trades: list,
    window: int = 20,
    min_win_rate: float = 0.50,
    max_consecutive_losses: int = 4
) -> tuple[bool, str]:
    """
    滚动检查某个 symbol 近期胜率，质量不达标时降权或暂停
    """
    recent = [t for t in recent_trades if t.symbol == symbol][-window:]
    
    if len(recent) < 5:
        return True, "INSUFFICIENT_HISTORY"   # 数据不足不限制
    
    win_rate = sum(1 for t in recent if t.pnl > 0) / len(recent)
    if win_rate < min_win_rate:
        return False, f"SYMBOL_WIN_RATE_LOW: {symbol} {win_rate:.2%} < {min_win_rate:.2%}"
    
    # 连续亏损检查
    consecutive_losses = 0
    for t in reversed(recent):
        if t.pnl < 0:
            consecutive_losses += 1
        else:
            break
    
    if consecutive_losses >= max_consecutive_losses:
        return False, f"SYMBOL_CONSECUTIVE_LOSS: {symbol} {consecutive_losses} losses in a row"
    
    return True, "OK"
```

---

## 八、分阶段执行计划

```
目标：从当前 2.3 笔/月，分 4 阶段达到 200-300 笔/月

每个阶段必须完成验收才能进入下一阶段。
验收标准：trade_count 提升 + win_rate ≥ 63%（分阶段放宽，最终 ≥ 75%）
```

### 阶段 0（前置，本周完成）：搭建漏斗诊断

```
目标：拿到完整的信号漏斗数据（见第二章实现方案）
输出：漏斗表 + 各层拦截原因分布

验收：能看到每一层的 pass/reject 数量
耗时预估：1-2 天开发
```

### 阶段 1（基于漏斗数据）：低风险扩量

```
变更（同时执行）：
  + enable_stable_bull_continuation = true    → 预估 +10-30 笔/月
  + ADX 下限: 22 → 18                         → 预估 +20% 信号通过率
  + preflip_trial_min_shrink_pct_long: 0.60 → 0.45  → 预估 +30% trial 信号
  + signal_type_score_thresholds 分类独立化    → 防止误杀其他类型信号

目标：trade_count → 10-20 笔/月
验收：
  ✓ win_rate ≥ 63%
  ✓ MDD 增加 ≤ 1%
  ✓ profit_factor ≥ 1.3
```

### 阶段 2：中级扩量

```
变更：
  + symbol pool 扩展到 30+ 标的             → 预估 ×5 信号量
  + flip_bearish 信号开放                   → 预估 +20-40 笔/月
  + ATR 上限: 0.020 → 0.025 + ATR 缩仓     → 预估 +15% 高波动信号
  + DOGE/BCH/AAVE 以 CVD+ADX 门恢复        → 预估 +10-20 笔/月
  + max_active_symbols: 4 → 6              → 解除容量瓶颈

目标：trade_count → 50-100 笔/月
验收：
  ✓ win_rate ≥ 65%
  ✓ MDD 增加 ≤ 2%
  ✓ 各信号类型独立 win_rate 均 ≥ 60%
```

### 阶段 3：中高级扩量

```
变更：
  + regime gate: TREND only → 含 TRANSITION（continuation 和 trial 类型）
  + min_vwap_score_for_entry: 0.15 → 0.10
  + L2/L3 gate: continuation 类型降到 1/3
  + symbol pool 扩展到 50+ 标的

目标：trade_count → 100-200 笔/月
验收：
  ✓ win_rate ≥ 70%
  ✓ MDD ≤ 5%
  ✓ 连续亏损 ≤ 5 笔时自动熔断
```

### 阶段 4：精调阶段

```
变更：
  + 基于漏斗数据找到最后一层主要瓶颈
  + 调整各信号类型的独立分数窗口
  + 引入 boll_stop 自适应 (volatile/trending 双模)
  + 开启 pretrade CVD 流量软门

目标：trade_count → 200-300 笔/月，win_rate ≥ 75%
验收：
  ✓ 连续 30 天 ≥ 200 笔
  ✓ win_rate ≥ 75%（需要足够样本，至少 200 笔才有统计意义）
  ✓ MDD ≤ 8%（高频策略允许适当放宽）
  ✓ Sharpe ≥ 1.5
```

---

## 九、胜率 vs 交易数的数学约束

**重要提示**：200-300 笔/月 + ≥ 75% 胜率是一个非常苛刻的组合目标，需要正视。

```
行业参考数据：
  高频 MACD 类策略（50-200 笔/月）：典型胜率 55-65%
  中频趋势跟踪（10-50 笔/月）：典型胜率 60-72%
  低频高质量（1-10 笔/月）：典型胜率 70-80%

当前策略定位（7 笔/90 天）处于"极低频高质量"区间，胜率 71% 合理。
目标（200-300 笔/月）处于"中高频"区间，75% 胜率是高端水平。

能否两者兼得？
  理论上可以，如果：
  1. 50+ 标的里大多数信号本身质量高
  2. 信号类型多样化后，每类的胜率独立 ≥ 70%
  3. 分层质量控制能有效过滤边际信号

实际挑战：
  随着交易数增加，会越来越多地拾取边际信号
  需要为每一笔增量信号设立独立的质量门
  这也是为什么漏斗数据如此关键 —— 不能光靠拍脑袋放宽门槛
```

---

## 十、改动速查表

| 参数/改动 | 当前值 | 阶段 1 | 阶段 2 | 阶段 3 | 预期贡献 |
|-----------|--------|--------|--------|--------|---------|
| 漏斗诊断能力 | 无 | **建立** | — | — | 前提条件 |
| stable_bull_continuation | 禁用 | **开启** | — | — | +10-30笔/月 |
| L1 ADX 下限 | 22 | **18** | — | — | +20%通过率 |
| preflip_trial_min_shrink_long | 0.60 | **0.45** | 0.35 | — | +30%trial信号 |
| signal_type 独立分数窗口 | 无 | **建立** | — | — | 防误杀 |
| symbol pool | ~10 | — | **30+** | **50+** | ×5-8信号量 |
| flip_bearish | ？ | — | **开放** | — | +20-40笔/月 |
| ATR 上限 | 0.020 | — | **0.025** | — | +15%高波动 |
| DOGE/BCH/AAVE | 禁用 | — | **CVD+ADX门** | — | +10-20笔/月 |
| max_active_symbols | 4 | — | **6** | **8** | 解容量瓶颈 |
| regime gate | TREND only | — | — | **+TRANSITION** | +30%时间覆盖 |
| min_vwap_score | 0.15 | — | — | **0.10** | +20%通过率 |
| L2/L3 continuation | 2/3 | — | — | **1/3** | +延续信号 |
| 目标 trade_count/月 | 2.3 | **10-20** | **50-100** | **100-200** | → 200-300 |
| 目标 win_rate | 71% | **≥63%** | **≥65%** | **≥70%** | → ≥75% |
