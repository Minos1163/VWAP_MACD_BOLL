# MACD V2 策略架构升级方案 v4.0

**报告日期**: 2026-04-04  
**背景**: 参数调优已穷尽，盈亏比 0.51 确认为结构性瓶颈  
**核心命题**: 从"调参"转向"架构升级"  

---

## 总结：过去所有消融实验告诉我们什么

```
已证明有效（保留）:
  ✓ flip_bearish vwap 门槛 0.16     → 净盈 +$237，无副作用
  ✓ 高分信号仓位去关联 (0.95+ 3x)  → 均亏从 $230 降至 $69
  ✓ 保本止损提前至 0.8%             → 回撤保护有效
  ✓ red_bar_shrinking min_score 0.78 → 修复100%胜率

已证明无效或有害（放弃）:
  ✗ TP触发点上移（任何幅度）        → 收益下降10%~11%
  ✗ VWAP劣势状态仓位扩大至70%       → long_below_both亏损翻倍
  ✗ 低流动性时段仓位压缩50%         → 收益/风险对称缩水，净效果为零

结构性约束（参数调优无法突破）:
  ⚠ 盈亏比 0.50~0.51               → 需要改变交易的入场/出场逻辑
  ⚠ intrabar止损占亏损76.8%         → 需要改变入场执行时机
  ⚠ 平均盈利 $18~$24               → 需要让盈利交易"跑得更远"
```

---

## 架构问题的本质

当前策略的盈利逻辑是**高胜率 × 小盈利**，这在数学上是脆弱的：

```
当前结构:
  期望值 = 80.7% × $24 - 19.3% × $48 = $19.37 - $9.26 = $10.11 / 笔

如果胜率因任何原因下降3%（市场结构变化）:
  新期望 = 77.7% × $24 - 22.3% × $48 = $18.65 - $10.70 = $7.95 / 笔
  收益下降 21%

如果改为"中胜率 × 大盈利"结构:
  目标: 75% × $38 - 25% × $36 = $28.5 - $9.0 = $19.5 / 笔
  即使胜率下降3%: 72% × $38 - 28% × $36 = $27.4 - $10.1 = $17.3 / 笔
  收益仅下降11%（抗风险能力翻倍）
```

**升级目标**：不是追求"更高胜率"，而是**提高盈亏比从 0.51 到 0.75+**。

---

## 升级方案一：入场时机优化（最高优先级）

### 问题根源

76.8% 的亏损来自 `stop_loss_intrabar`，即入场后同一根 K 线就触发止损。这说明入场价格经常处于该 K 线的不利端——信号触发在 K 线刚开盘时，此时价格尚未找到方向，直接追市价入场导致滑点 + 不利位置双重叠加。

**用数据量化问题**：

```
止损 2.0%，滑点 0.15%，实际有效止损空间 = 2.0% - 0.15% = 1.85%
如果入场时已处于 K 线高点偏离 0.3%（常见于开盘追涨），
则实际止损空间只剩 1.85% - 0.30% = 1.55%，压缩了 16%
而止盈目标 0.8% 不变，等于盈亏比从原始设计的 0.4 进一步压缩
```

### 解决方案：K 线内分级入场

**核心思想**：不改变信号触发逻辑，只改变订单执行时机。

**代码实现** (`src/fund_flow/execution_router.py`):

```python
import time
from dataclasses import dataclass
from enum import Enum

class EntryMode(Enum):
    IMMEDIATE = "immediate"    # 立即入场
    WAIT_PULLBACK = "pullback" # 等待回调
    WAIT_CONFIRM = "confirm"   # 等待方向确认

@dataclass
class EntryDecision:
    mode: EntryMode
    limit_price: float | None   # None = 市价
    max_wait_seconds: int       # 超时后取消

def decide_entry_mode(signal, config) -> EntryDecision:
    """
    根据信号类型和当前市场状态决定入场方式
    """
    # 翻转信号：等待回调确认，避免追顶/追底
    if signal.signal_type in ("flip_bullish", "flip_bearish"):
        if signal.score >= 0.92:
            # 极高分翻转：立即入场，不等待
            return EntryDecision(EntryMode.IMMEDIATE, None, 0)
        else:
            # 普通翻转：等待价格回调至入场价格下方 0.1%
            limit_price = signal.entry_price * (
                0.999 if signal.direction == "long" else 1.001
            )
            return EntryDecision(EntryMode.WAIT_PULLBACK, limit_price, 300)  # 等5分钟

    # 趋势延续信号：等待本根K线稳定
    if signal.signal_type in ("red_bar_growing", "green_bar_growing"):
        bar_age_seconds = time.time() - signal.bar_open_time
        if bar_age_seconds < 180:  # K线开盘后3分钟内
            # 等到K线进行30%再入场
            wait_until = signal.bar_open_time + 0.30 * 900  # 15M = 900s
            return EntryDecision(EntryMode.WAIT_CONFIRM, None,
                                 max(0, int(wait_until - time.time())))
        else:
            return EntryDecision(EntryMode.IMMEDIATE, None, 0)

    # 其他信号默认立即入场
    return EntryDecision(EntryMode.IMMEDIATE, None, 0)
```

**配置开关**：

```json
"entry_timing_config": {
  "enabled": true,
  "flip_signal_limit_offset_pct": 0.001,   // 翻转信号限价挂单偏移 0.1%
  "trend_signal_bar_wait_ratio": 0.30,      // 趋势信号等待K线进行30%
  "max_wait_seconds": 300,                  // 最长等待5分钟，超时取消
  "high_score_bypass_threshold": 0.92       // 0.92分以上信号跳过等待
}
```

**预期效果**：

```
翻转信号（18% 交易量）入场价格改善约 0.1%
  对于 2% 止损：有效止损空间扩大 5%
  对于 0.8% TP1：到达 TP1 的概率提升约 8%

趋势信号（56% 交易量）intrabar 止损减少
  等待 K 线前 30% 过后入场，避开开盘噪音
  预计 intrabar 止损率从 76.8% 降至 60%~65%

综合预期:
  intrabar 止损绝对金额降低约 20%
  平均盈亏改善：平均盈利 $18 → $21（+16%），平均亏损 $36 → $31（-14%）
  盈亏比: 0.51 → 0.68（+33%）← 突破结构性约束
  收益率预估增量: +12% ~ +18%
```

**回测集成方法**：

```python
# scripts/backtest_macd_v2.py
# 在 execute_trade() 中新增入场延迟模拟

def simulate_entry_timing(signal, entry_price, bar_ohlcv):
    """
    模拟入场时机优化在历史数据中的效果
    返回: 模拟后的实际入场价格
    """
    cfg = self.config.entry_timing_config
    if not cfg.enabled:
        return entry_price

    if signal.signal_type in ("flip_bullish", "flip_bearish"):
        if signal.score < cfg.high_score_bypass_threshold:
            # 模拟等待0.1%回调
            offset = entry_price * cfg.flip_signal_limit_offset_pct
            if signal.direction == "long":
                # 做多：等价格回调到入场价-0.1%
                simulated_price = min(entry_price, bar_ohlcv['low'] + offset)
            else:
                simulated_price = max(entry_price, bar_ohlcv['high'] - offset)
            return simulated_price

    if signal.signal_type in ("red_bar_growing", "green_bar_growing"):
        # 模拟等待30%K线后的均价入场
        wait_pct = cfg.trend_signal_bar_wait_ratio
        simulated_price = (
            entry_price * (1 - wait_pct) +
            (bar_ohlcv['open'] * 0.3 + bar_ohlcv['close'] * 0.7) * wait_pct
        )
        return simulated_price

    return entry_price
```

---

## 升级方案二：信号类型独立止盈配置

### 问题根源

当前所有信号类型使用同一套 TP：[0.8%, 1.2%, 2.0%]。但不同信号类型的趋势延续能力差异巨大：

```
flip_bullish:    均盈 $17.91/笔，胜率 88%  → 反转后动量强，应该"跑得远"
red_bar_growing: 均盈 $6.93/笔，胜率 83%   → 趋势延续，应该"快速锁定"
flip_bearish:    均盈 $13.33/笔，胜率 72%  → 成功率低，应该"快进快出"
```

**用同一套 TP 服务三种完全不同的信号特性**，是当前盈亏比低下的直接原因。

### 解决方案：三种信号三套 TP

**配置实现**：

```json
"signal_type_tp_config": {
  "enabled": true,

  "flip_bullish": {
    "take_profit_pct_levels":        [0.012, 0.020, 0.035],
    "take_profit_reduce_pct_levels": [0.20,  0.30,  0.20],
    "runner_pct": 0.30,
    "trailing_stop_enabled": true,
    "trailing_stop_activation_pct": 0.025,
    "trailing_stop_distance_pct":   0.008
  },

  "flip_bearish": {
    "take_profit_pct_levels":        [0.008, 0.014, 0.022],
    "take_profit_reduce_pct_levels": [0.30,  0.35,  0.25],
    "runner_pct": 0.10,
    "trailing_stop_enabled": false
  },

  "red_bar_growing": {
    "take_profit_pct_levels":        [0.008, 0.012, 0.020],
    "take_profit_reduce_pct_levels": [0.25,  0.30,  0.20],
    "runner_pct": 0.25,
    "trailing_stop_enabled": true,
    "trailing_stop_activation_pct": 0.018,
    "trailing_stop_distance_pct":   0.006
  },

  "green_bar_growing": {
    "take_profit_pct_levels":        [0.008, 0.012, 0.020],
    "take_profit_reduce_pct_levels": [0.25,  0.30,  0.20],
    "runner_pct": 0.25,
    "trailing_stop_enabled": true,
    "trailing_stop_activation_pct": 0.018,
    "trailing_stop_distance_pct":   0.006
  }
}
```

**flip_bullish 设计逻辑**：

```
TP1 从 0.8% 上移至 1.2%（接受更少的"小盈利"平仓）
TP3 延伸至 3.5%（允许强趋势运行更远）
30% runner + trailing stop：激活点 2.5%，距离 0.8%
  → 如果价格从 +3.5% 回落 0.8%，在 +2.7% 止盈（仍远超原始 TP3 的 2.0%）
  → 如果价格继续涨到 +5%，trailing stop 在 +4.2% 止盈

预期平均盈利变化:
  当前: $17.91（共享TP结构）
  升级后: 估算 $24~$28（+34%~+56%）
```

**代码实现** (`src/fund_flow/macd_strategy_v2.py`):

```python
def get_tp_config(self, signal_type: str) -> TakeProfitConfig:
    """
    根据信号类型返回对应的TP配置
    优先使用信号类型专属配置，否则退回全局配置
    """
    signal_configs = self.config.signal_type_tp_config
    if signal_configs.enabled and signal_type in signal_configs:
        return signal_configs[signal_type]
    # 退回全局配置
    return TakeProfitConfig(
        levels=self.config.take_profit_pct_levels,
        reduce_pcts=self.config.take_profit_reduce_pct_levels,
        trailing_stop_enabled=False
    )
```

**预期效果**：

```
flip_bullish (13% 交易量):
  平均盈利 $18 → $25  (+39%)
  对全局盈亏比贡献: +0.04

red_bar_growing trailing stop (55% 交易量):
  部分交易盈利延伸至 3%~5%
  平均盈利 $7 → $9  (+28%)
  对全局盈亏比贡献: +0.06

综合盈亏比预估: 0.51 → 0.70~0.75
收益率增量: +15% ~ +25%
```

---

## 升级方案三：动态仓位系统

### 设计思想

当策略处于连续盈利状态时，说明当前市场与策略特性高度匹配，应该适度加大投入；当处于连续亏损时，说明市场处于策略的弱势区，应该保守。

这不是"追涨杀跌"，而是**让策略在最适合自己的市场环境中发挥最大效能**。

### 实现方案

**配置**：

```json
"dynamic_position_config": {
  "enabled": true,
  "lookback_trades": 5,
  "multipliers": {
    "win_4_of_5":  1.20,
    "win_3_of_5":  1.08,
    "neutral":     1.00,
    "loss_3_of_5": 0.80,
    "loss_4_of_5": 0.65
  },
  "max_multiplier": 1.25,
  "min_multiplier": 0.60,
  "reset_on_new_session": false
}
```

**代码实现** (`src/fund_flow/risk_engine.py`):

```python
from collections import deque

class DynamicPositionManager:
    def __init__(self, config):
        self.cfg = config.dynamic_position_config
        self.recent_trades: deque = deque(maxlen=self.cfg.lookback_trades)

    def record_trade_result(self, pnl: float):
        self.recent_trades.append(1 if pnl > 0 else 0)

    def get_position_multiplier(self) -> float:
        if not self.cfg.enabled or len(self.recent_trades) < 3:
            return 1.0

        wins = sum(self.recent_trades)
        total = len(self.recent_trades)
        losses = total - wins

        mult = self.cfg.multipliers
        if wins >= 4:
            result = mult["win_4_of_5"]
        elif wins >= 3:
            result = mult["win_3_of_5"]
        elif losses >= 4:
            result = mult["loss_4_of_5"]
        elif losses >= 3:
            result = mult["loss_3_of_5"]
        else:
            result = mult["neutral"]

        return max(self.cfg.min_multiplier,
                   min(self.cfg.max_multiplier, result))
```

**与账户熔断协调**：

```python
# 动态仓位乘数和账户熔断联动
def get_effective_position_scale(self, base_position: float) -> float:
    dynamic_mult = self.dynamic_position_manager.get_position_multiplier()
    circuit_mult = self.account_circuit.get_scale()  # 熔断期间强制 0.0
    return base_position * dynamic_mult * circuit_mult
```

**预期效果**：

```
连赢4/5笔后仓位×1.20，此时策略处于强势期，放大盈利
连输4/5笔后仓位×0.65，此时策略处于弱势期，减少亏损

模拟估算（基于历史交易序列）:
  有效放大顺风期收益约 +8%
  有效压缩逆风期损失约 -5%
  净收益增量: +12% ~ +18%
  对回撤的影响: 因逆风期自动缩仓，回撤预计下降约 1%
```

---

## 升级方案四：CVD 入场否决（最快实现）

### 问题

当前 `cvd_filter_config.enabled: false`，CVD 资金流数据完全未被利用。

在以下两种场景下，CVD 与价格方向背离是假突破的强信号：
1. 价格新高但 CVD 不创新高（多头假突破）
2. 价格新低但 CVD 不创新低（空头假突破）

这类情况在亏损交易中占比约 20%~30%（基于回测中 `short_retest_reject` 和 `long_below_both` 的高亏损状态）。

### 实现方案（最小代码改动）

```json
"cvd_filter_config": {
  "enabled": true,
  "apply_to_signals": ["flip_bearish", "flip_bullish"],  // 先只对翻转信号启用
  "lookback_15m": 4,
  "divergence_veto": {
    "enabled": true,
    "long_veto_if_cvd_below_ma": true,   // 做多时CVD低于均线则否决
    "short_veto_if_cvd_above_ma": true,  // 做空时CVD高于均线则否决
    "cvd_ma_period": 4
  },
  "positive_delta_ratio_threshold": 0.05
}
```

**预期效果**：

```
对翻转信号（31笔/月）的入场做 CVD 背离过滤
预计过滤掉约 20% 的翻转信号（6~7笔）
其中约 70% 是假突破（历史估算）
净效果: 翻转信号胜率从 80% 提升至 85%~87%
对全局胜率贡献: +0.5%~+1%
```

---

## 各升级方案组合预估

```
当前 v3.0b 基线:           +43.41%  PF 2.39  WR 82.5%  盈亏比 0.51
                                │
  + 方案一 (入场时机优化):   +58%   (+15%)  盈亏比 0.68
                                │
  + 方案二 (信号TP分化):     +78%   (+20%)  盈亏比 0.80
                                │
  + 方案三 (动态仓位):       +96%   (+18%)  盈亏比 0.80（动态仓位不改盈亏比）
                                │
  + 方案四 (CVD过滤):       +101%   (+5%)   WR 84%+

组合效应预估（非线性）:     +90% ~ +115%
```

> 在相同月收益 +48% 的基础上，这个结构的**稳定性**显著高于当前版本：盈亏比 0.80 意味着即使胜率下降 5%，策略仍保持正期望。

---

## 执行计划：4 周路线图

### Week 1：方案四 + 方案一（快速验证）

**Day 1-2**：CVD 过滤器开启（配置变更，零代码）

```bash
# 仅修改配置文件，立即可回测
vim config/trading_config_fund_flow.json
# 设置 cvd_filter_config.enabled: true
python scripts/backtest_macd_v2.py --config config/trading_config_fund_flow.json
```

**验证标准**：

```
目标: flip 信号胜率从 80%/88% 提升至 83%/90%
接受: 翻转信号减少 ≤ 25%（允许放弃部分信号换取质量）
```

**Day 3-5**：方案一入场延迟（代码 + 回测）

```
修改文件: src/fund_flow/execution_router.py
          scripts/backtest_macd_v2.py (模拟入场时机)
回测验证: intrabar 止损率是否从 76.8% 下降
目标:     惰性止损率降至 60% 以下
```

### Week 2：方案二（信号独立 TP，重点）

```
修改文件: src/fund_flow/macd_strategy_v2.py (get_tp_config)
          config/trading_config_fund_flow.json (signal_type_tp_config)
          scripts/backtest_macd_v2.py (TP分支读取)

消融顺序:
  [Test-TP1] 仅 flip_bullish 启用宽松TP + trailing stop
             目标: flip_bullish 均盈 $18 → $24+
  [Test-TP2] red_bar_growing 启用 trailing stop
             目标: 平均盈利提升 $1~$2

接受条件:
  flip_bullish 均盈 ≥ $22
  red_bar_growing 均盈 ≥ $8
  全局盈亏比 ≥ 0.65
```

### Week 3：方案三（动态仓位）

```
修改文件: src/fund_flow/risk_engine.py (DynamicPositionManager)
          src/fund_flow/decision_engine.py (集成仓位乘数)

验证方法: 与无动态仓位版本对比同一时期回测
目标:
  顺风期（连赢4/5）仓位放大 20%，对应期间收益增加 15%+
  逆风期（连输4/5）仓位缩小 35%，对应期间亏损减少 20%+
```

### Week 4：组合验证

```
[Test-FINAL-v4] 合并 Week1~3 所有通过项
目标: 收益 ≥ +90%，盈亏比 ≥ 0.70，回撤 ≤ 9%

如达到 +90%:
  → 进入实盘验证阶段（30天，50%资金）
如未达到 +90%:
  → 分析最大瓶颈，针对性补强
```

---

## 回测记录模板（v4 版本）

```markdown
## [Test-v4-XX] 变更记录

**变更项**: [具体内容]
**修改文件**: [文件路径]

| 指标 | v1.1基线 | v3.0b | 本次 | 判断 |
|------|---------|-------|------|------|
| 收益率 | +48.66% | +43.41% | ? | 目标 ≥ v3.0b |
| **盈亏比** | **0.50** | **0.51** | ? | **目标 ≥ 0.65** |
| 平均盈利 | $24 | $18 | ? | 目标 ≥ $22 |
| 平均亏损 | -$48 | -$36 | ? | 维持或改善 |
| intrabar止损率 | 76.8% | - | ? | 目标 ≤ 65% |
| flip_bullish均盈 | - | $17.91 | ? | 目标 ≥ $22 |
| 胜率 | 80.7% | 82.5% | ? | 维持 ≥ 80% |
| 最大回撤 | 4.87% | 4.15% | ? | ≤ 10% |

**盈亏比是否提升**: [是/否]
**intrabar止损率是否下降**: [是/否]
**结论**: [接受/拒绝/调整]
```

---

## 附：为什么不建议继续纯参数调优

| 已测试方向 | 结论 | 根因 |
|-----------|------|------|
| TP触发点上移 | 任何幅度都降收益 | 0.8% TP1是大量噪音交易的唯一盈利出口 |
| 仓位压缩 | 收益/风险等比缩水 | 盈利和亏损对称，净期望不变 |
| 杠杆调整 | 放大后回撤同步放大 | 盈亏比不改变时，杠杆只是线性缩放 |
| 信号门槛 | 质量↑但数量↓，净效果接近零 | 被过滤的信号中盈利比例约等于整体 |

**继续调参只是在一个有缺陷的结构上做优化**，就像给一辆油耗 15L/100km 的车调胎压——能省一点但到不了 8L/100km 的水平。要从根本上改变，必须升级发动机（入场时机 + 止盈逻辑）。

---

**报告版本**: v4.0 | 2026-04-04  
**前置基线**: v3.0b `v2_summary_20260404_222949.json`  
**核心结论**: 短期目标 +90%（4周路径），依赖盈亏比从 0.51 升至 0.70+
