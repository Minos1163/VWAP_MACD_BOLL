# MACD V2 第二轮策略优化建议

**日期**: 2026-03-31  
**当前状态**: 4 笔成交 / PF 106.15 / MDD 0.32% / 全为 flip_bullish  
**核心矛盾**: 第一轮优化成功切掉亏损口袋，但过度收紧导致信号稀疏（30 天仅 4 笔）  
**本轮目标**: 在不显著抬高 MDD 的前提下，系统性恢复一部分交易机会

---

## 一、回答 7 个技术问题

### Q1：weight_4h_direction=0.35 + weight_1h_direction=0.20 是否仍对 flip_bullish trial 产生高分误导？

**结论：仍然存在，但机制已经改变，当前的 score window [0.80, 0.87] 是对症下药的正确方式。**

flip_bullish trial 的本质是"4H 尚未翻多，提前预判翻转"的逆势轻仓行为。  
这意味着在开仓时，4H 方向分应该是**中性偏低**的，而不是高分。

```
高分误导路径（当前仍存在）：

  场景：4H 柱在快速收缩但方向分仍然偏多（旧趋势余温）
        + 1H 刚出现短暂反弹 → 1H 方向加分
        + VWAP 位置还在价格之下 → VWAP 小幅加分
  结果：总分 ≥ 0.88，但实际是跌势末段的反弹，不是真正翻转

  why [0.80, 0.87] 有效：
    真正好的 preflip 信号，4H 方向分通常是 0 或很低（还没翻）
    所以总分自然落在 0.80-0.85 区间
    高于 0.87 的，通常是旧趋势余温在虚抬分数
```

**建议**：当前 window 逻辑正确，但可以增加一个 4H 方向分的上限约束来从机制上消除误导（见第三章）。

---

### Q2：flip_bullish_trial_score_max = 0.87 是否过严，是否应放宽到 0.88/0.89？

**结论：不建议无条件放宽，但可以做条件性放宽。**

```
当前数据的可信度问题：

  4 笔交易，其中 FETUSDT 一笔贡献 +85.21
  PF = 106.15 严重受这一笔主导

  如果 FETUSDT 这笔不算：
    剩余 3 笔 PnL ≈ +3.02 + +2.75 + (POLUSDT 亏损笔)
    整体远没有 106.15 那么好

  结论：当前 PF 是小样本噪声，不能作为"参数设置优秀"的证据
```

| 方案 | 操作 | 预期影响 |
|------|------|---------|
| 直接放宽到 0.88 | 简单 | 可能把上轮亏损段的边缘信号放回来，风险不可控 |
| 条件性放宽：只在 CVD 确认时允许 0.88 | 增加一个质量门 | 更安全，能筛出真实买盘 |
| 维持 0.87，扩展其他信号类型 | 不改 window | 最保守，建议先走这个路径 |

**推荐**：本轮不动 score_max，通过恢复其他信号类型（stable continuation、非 trial 的 flip_bullish）来增加交易数，而不是放宽 trial 的门槛。

---

### Q3：DOGE/BCH/AAVE 直接禁用是否应该改成 symbol-specific threshold？

**结论：应该迁移，但迁移条件是先给这三个币加上 CVD 质量门。**

```
当前状态：disable_flip_bullish_trial = true
问题：完全禁用，丢失了这三个币在真正趋势行情中的 alpha

迁移条件（缺一不可）：
  1. preflip_trial_min_signal_score_override ≥ 0.90（已设置）
  2. 必须额外要求 CVD 确认（当前没有）
  3. 建议在 ADX_4H ≥ 30 时才允许（确保趋势足够强）

如果只满足条件 1，直接从 disable 改成 threshold，
等于把上轮亏损口袋重新打开了一半。
```

**建议**：本轮暂时维持 disable，等第三章的 CVD 增强落地并回测验证后，再迁移到 threshold 模式。

---

### Q4：enable_flip_bullish_cvd_context_filter = false 是否值得开启？

**结论：值得开启，且这是本轮最值得做的单一改动。**

CVD 过滤器会检查：
- `flip_bullish_max_cvd_upper_wick_ratio = 0.2`：1H K 线上影线占比，过大说明买盘被空头压制
- `flip_bullish_min_cvd_1h_delta_ratio = 0.03`：CVD 近 1H 增量，确认有真实买盘流入

这两个条件组合，能有效过滤掉"形态像翻转但没有真实买盘"的假信号，正好针对上一轮 DOGE/BCH/AAVE 的失效模式。

```
预期影响：
  trade_count：-2 到 -4 笔（过滤掉伪买盘信号）
  win_rate：   提升 5-10 个百分点
  return：     可能轻微降低（笔数少）但质量显著提升
  MDD：        持平或微降
```

---

### Q5：stop_loss_pct=0.02 / take_profit_pct=0.04 与动态止损并存时，是否有过拟合风险？

**结论：有明显的双轨混用风险，当前配置存在隐患。**

```
当前止损结构：
  固定止损：  stop_loss_pct = 0.02（2% 硬止损）
  动态止损：  use_dynamic_stop = true
              boll_stop_atr_multiplier = 0.5
              max_stop_loss_pct = 0.025

潜在问题：
  boll_stop_atr_multiplier = 0.5 会生成一个很紧的动态止损
  在波动行情中，动态止损可能比固定 2% 更早触发
  → 实际止损 < 2%，但 TP 仍按 4% 计算
  → 实际 R 比率 > 2，比预设更激进

过拟合风险：
  4 笔样本里，FETUSDT 的大盈利可能正好是"动态止损没触发 + 趋势延续"的幸运组合
  在不同行情下，动态止损可能频繁过早出局
```

**建议**：在更多交易数据积累之前，将 `boll_stop_atr_multiplier` 从 `0.5` 提升到 `0.8`，减少过紧止损的概率。

---

### Q6：pretrade_risk_gate.use_hard_rules_only=true 是否足够？

**结论：当前硬规则已经覆盖主要风险，但缺一条最小 CVD 流量门。**

```
当前硬规则覆盖情况：
  ✓ volatility_cap = 0.012          → 拦截高波动入场
  ✓ equity_usage_block = 0.60       → 控制总暴露
  ✓ max_exposure_per_trade = 0.22   → 单笔上限
  ✓ max_drawdown = 0.02             → 账户回撤保护

缺失的一条：
  ✗ 没有 CVD 流量最低门槛
    → 在成交量极度萎缩的行情里（节假日、凌晨时段），
      当前配置仍会开仓，但这类时段信号可靠性显著降低
```

**建议**：新增一条软规则：`min_cvd_flow_gate`，要求开仓前 1H CVD 绝对值 ≥ 某个最低阈值。实现见第三章。

---

### Q7：在不显著增加回撤前提下，优先恢复哪类交易机会？

**优先级排序（从高到低）：**

```
#1  恢复 flip_bullish（非 trial）的正常入场
    理由：trial 是缩仓预判入场，non-trial 是确认后入场，胜率更高
    条件：当前配置里是否已开放？需要确认

#2  恢复 stable_bear_continuation 信号
    理由：做空延续信号，与 flip_bullish 方向互补，增加策略多样性
    条件：当前 enable_stable_bear_continuation = true，但本次 30 天没有成交
           说明 stable_bear 门槛太高或 3 月没有符合条件的做空机会

#3  恢复 DOGE/BCH/AAVE 但加 CVD + ADX 双重门
    理由：三个高流动性币种，有真实趋势时 alpha 丰厚
    条件：先在回测里验证 CVD 门能有效过滤，再解除 disable

#4  放宽 preflip_trial_min_shrink_pct_long（0.6 → 0.5）
    理由：适当提前试仓入场
    条件：最后做，因为这会直接提升亏损概率
```

---

## 二、TP 双配置冲突分析

**这是当前最需要立即处理的配置问题，与策略参数无关但可能直接影响实际盈亏。**

```
两组配置同时存在：

  配置 A（fund_flow 主配置）：
    take_profit_pct = 0.04       ← 4% TP

  配置 B（默认值层）：
    take_profit_default_percent = 0.02  ← 2% TP
```

### 验证方法

```python
# 在回测 trade log 里验证实际生效的 TP
import pandas as pd

trades = pd.read_csv("output/backtest/bot_like_trades_20260331_164319.csv")
wins = trades[trades["exit_reason"].str.contains("take_profit")]

print("止盈单实际盈利分布：")
print(wins["pnl_ratio"].describe())

# 如果 mean ≈ 0.02 → take_profit_default = 0.02 在生效（被覆盖）
# 如果 mean ≈ 0.04 → fund_flow.take_profit_pct = 0.04 在生效（正确）
# 如果分布极度分散 → 动态止盈在主导，固定 TP 没有被触发
```

### 修复方案

```diff
# 方案：统一 TP 配置，消除双轨隐患

# 在 fund_flow 配置层显式覆盖默认值
+ risk_params_override:
+   take_profit_default_percent: 0.04   # 明确与 fund_flow.take_profit_pct 保持一致
+   stop_loss_default_percent:   0.02   # 与 stop_loss_pct 保持一致

# 同时在代码里加断言（建议加到 backtest 启动脚本）
+ assert cfg.take_profit_pct == cfg.risk_params.take_profit_default_percent, \
+     f"TP config mismatch: {cfg.take_profit_pct} vs {cfg.risk_params.take_profit_default_percent}"
```

---

## 三、本轮核心改动建议

### 3.1 改动 A：开启 CVD 上下文过滤（最高优先级）

```diff
# fund_flow.macd_mtf_strategy_v2.entry_filters

- enable_flip_bullish_cvd_context_filter: false
+ enable_flip_bullish_cvd_context_filter: true

  flip_bullish_max_cvd_upper_wick_ratio: 0.2    # 保持不动
  flip_bullish_min_cvd_1h_delta_ratio:   0.03   # 保持不动

# 如果初次开启后 trade_count 降到 < 3/月，适当放宽：
# flip_bullish_min_cvd_1h_delta_ratio: 0.02  （回退档位）
```

**伪代码（CVD 过滤逻辑）：**

```python
def check_flip_bullish_cvd_context(bar_1h, cvd_data, cfg) -> tuple[bool, str]:
    """
    flip_bullish 的 CVD 上下文过滤
    目的：确认有真实买盘，而非纯技术形态触发
    """
    # 条件 1：1H 上影线占比检查（过大说明买盘被压制）
    upper_wick = bar_1h.high - max(bar_1h.open, bar_1h.close)
    total_range = bar_1h.high - bar_1h.low
    wick_ratio = upper_wick / total_range if total_range > 0 else 0

    if wick_ratio > cfg.flip_bullish_max_cvd_upper_wick_ratio:
        return False, f"CVD_WICK: {wick_ratio:.3f} > {cfg.flip_bullish_max_cvd_upper_wick_ratio}"

    # 条件 2：CVD 近 1H 增量（确认买盘流入）
    cvd_1h_delta_ratio = cvd_data.delta_1h / cvd_data.baseline_volume
    if cvd_1h_delta_ratio < cfg.flip_bullish_min_cvd_1h_delta_ratio:
        return False, f"CVD_FLOW: {cvd_1h_delta_ratio:.4f} < {cfg.flip_bullish_min_cvd_1h_delta_ratio}"

    return True, "CVD_OK"
```

---

### 3.2 改动 B：新增 pretrade CVD 流量最低门（soft rule）

```diff
# fund_flow.pretrade_risk_gate

  enabled: true
  use_hard_rules_only: true

# 新增软规则层（在 hard_rules_only 通过后执行）
+ soft_rules_enabled: true
+ soft_rules:
+   min_cvd_flow_gate:
+     enabled: true
+     min_cvd_volume_ratio: 0.005   # 1H CVD 绝对值 ≥ 0.5% 的基准成交量
+     apply_to_signal_types:
+       - flip_bullish
+       - preflip_trial
+     # 不应用到 stable_continuation（那类信号是跟随趋势，成交量可以偏低）
```

**伪代码：**

```python
def check_pretrade_cvd_flow_gate(signal, cvd_data, cfg) -> tuple[bool, str]:
    """
    开仓前的 CVD 最低流量门
    防止在成交量极度萎缩时（节假日、极低流动性时段）开仓
    """
    if signal.signal_type not in cfg.min_cvd_flow_gate.apply_to_signal_types:
        return True, "CVD_FLOW_GATE_SKIP"

    flow_ratio = cvd_data.abs_cvd_1h / cvd_data.avg_volume_24h
    min_ratio  = cfg.min_cvd_flow_gate.min_cvd_volume_ratio

    if flow_ratio < min_ratio:
        return False, f"PRETRADE_CVD_FLOW: {flow_ratio:.4f} < {min_ratio} (low liquidity)"

    return True, "CVD_FLOW_OK"
```

---

### 3.3 改动 C：修复动态止损过紧问题

```diff
# fund_flow.stop_loss_config

  use_dynamic_stop: true
- boll_stop_atr_multiplier: 0.5
+ boll_stop_atr_multiplier: 0.8   # 放宽：减少在趋势行情中的过早出局

  max_stop_loss_pct: 0.025        # 不动

# 逻辑说明：
# boll_stop = boll_lower - ATR × multiplier (多头)
# multiplier 从 0.5 → 0.8，止损价更远离当前价格
# 代价：单笔最大亏损略增，但趋势单被扫出的概率降低
```

**双模应用（配合上轮双模风控建议）：**

```python
def get_dynamic_stop_multiplier(market_mode: str) -> float:
    """
    根据行情模式动态调整 ATR 止损乘数
    """
    return {
        "VOLATILE":  0.6,   # 波动行情：止损偏紧，快速保护浮盈
        "TRENDING":  1.0,   # 趋势行情：止损宽松，让趋势跑
    }.get(market_mode, 0.8)  # 默认取中间值

# 在 stop_loss_config 里不写死，由运行时注入：
# boll_stop_atr_multiplier = get_dynamic_stop_multiplier(current_market_mode)
```

---

### 3.4 改动 D：恢复 stable_bear_continuation 成交机会

3 月没有 stable_bear 成交，可能是门槛过高。检查当前参数：

```yaml
# 当前 stable_bear 相关参数
enable_stable_bear_continuation: true
stable_bear_continuation_min_vwap_score:   0.10
stable_bear_continuation_min_adx_1h:       30.0   ← 可能偏高
stable_bear_continuation_min_4h_bars:       2
stable_bear_continuation_min_signal_score:  0.83
```

```diff
# 第一步：只放宽 ADX 门槛，其他不动

- stable_bear_continuation_min_adx_1h: 30.0
+ stable_bear_continuation_min_adx_1h: 25.0   # 放宽，允许趋势启动早期入场

# 如果放宽后仍无成交，第二步再审查 min_vwap_score
```

---

### 3.5 改动 E：DOGE/BCH/AAVE 迁移路线图（条件性，不是本轮立即执行）

**迁移前提**：改动 A（CVD 过滤）和改动 B（CVD 流量门）必须先落地并验证通过。

```diff
# 满足前提后，将 disable 改为高门槛可交易

  symbol_overrides:
    DOGEUSDT:
-     disable_flip_bullish_trial: true
-     preflip_trial_min_signal_score_override: 0.90
+     disable_flip_bullish_trial: false
+     preflip_trial_min_signal_score_override: 0.88   # 高门槛
+     flip_bullish_require_cvd_confirm: true           # 必须 CVD 确认
+     flip_bullish_min_adx_4h_override: 30             # 必须强趋势
+
    BCHUSDT:
-     disable_flip_bullish_trial: true
-     preflip_trial_min_signal_score_override: 0.90
+     disable_flip_bullish_trial: false
+     preflip_trial_min_signal_score_override: 0.88
+     flip_bullish_require_cvd_confirm: true
+     flip_bullish_min_adx_4h_override: 30
+
    AAVEUSDT:
-     disable_flip_bullish_trial: true
-     preflip_trial_min_signal_score_override: 0.90
+     disable_flip_bullish_trial: false
+     preflip_trial_min_signal_score_override: 0.88
+     flip_bullish_require_cvd_confirm: true
+     flip_bullish_min_adx_4h_override: 30
```

---

## 四、本轮 ablation 实验顺序

```
基线（当前）：
  trade_count=4, PF=106.15 (小样本噪声), MDD=0.32%, return=+0.23%

实验 #1：统一 TP 配置（先修 take_profit_default_percent → 0.04）
  预期变化：trade_count 不变，return 可能变化（取决于实际生效的是哪个值）
  目的：确保后续所有实验的基础参数一致，消除隐患
  验收：确认 trade log 里止盈单 pnl_ratio ≈ 0.04

实验 #2：开启 CVD 上下文过滤（enable_flip_bullish_cvd_context_filter = true）
  预期变化：trade_count -0 到 -2，win_rate 提升
  验收：trade_count ≥ 3，PF ≥ 2.0，win_rate ≥ 50%

实验 #3：放宽 stable_bear ADX 门（25.0）
  预期变化：trade_count +1 到 +3（增加做空信号）
  验收：新增 stable_bear 单 win_rate ≥ 50%，MDD 增加 ≤ 0.5%

实验 #4：修复动态止损乘数（0.5 → 0.8）
  预期变化：trade_count 不变，avg_win 增加，avg_loss 略增
  验收：profit_factor 改善，MDD 增加 ≤ 0.3%

实验 #5：新增 pretrade CVD 流量门（soft rule）
  预期变化：trade_count -1 到 0（只拦低流动性时段），质量提升
  验收：被拦截的信号事后验证 ≥ 60% 是亏损单

实验 #6（条件性）：DOGE/BCH/AAVE 从 disable 迁移到高门槛 threshold
  前提：实验 #2 已验证 CVD 过滤有效
  预期变化：trade_count +2 到 +5，win_rate 可能轻微下降
  验收：这三个币新增交易的 win_rate ≥ 40%，MDD 增加 ≤ 0.5%

──────────────────────────────────────────────────
每轮验收标准（5 个指标全部检查）：

  ✓ trade_count 方向符合预期
  ✓ win_rate ≥ 50%（当前样本太小，暂不要求 75%）
  ✓ profit_factor ≥ 1.5
  ✓ MDD 增加 ≤ 0.5% per 实验
  ✗ 任何实验导致 MDD > 1% → 立即回滚
──────────────────────────────────────────────────
```

---

## 五、整体参数变更速查表

| 参数 | 当前值 | 建议值 | 实验 | 优先级 |
|------|--------|--------|------|--------|
| `take_profit_default_percent` | 0.02 | 0.04 | #1 | 🔴 立即修复 |
| `enable_flip_bullish_cvd_context_filter` | false | true | #2 | 🔴 高 |
| `stable_bear_continuation_min_adx_1h` | 30.0 | 25.0 | #3 | 🟡 中 |
| `boll_stop_atr_multiplier` | 0.5 | 0.8 | #4 | 🟡 中 |
| `pretrade.soft_rules.min_cvd_flow_gate` | 无 | 新增 | #5 | 🟡 中 |
| `DOGE/BCH/AAVE.disable_flip_bullish_trial` | true | false (条件) | #6 | 🟢 低（条件性）|
| `flip_bullish_trial_score_max` | 0.87 | **不动** | — | — |
| `preflip_trial_min_shrink_pct_long` | 0.60 | **不动** | — | — |

---

## 六、对当前参数组合的总判断

```
优点：
  ✓ 成功切掉亏损口袋，MDD 压到 0.32%
  ✓ score window 机制正确，甜区识别有效
  ✓ pretrade gate 的硬规则覆盖主要风险点
  ✓ 动态止损框架已就位，方向正确

问题：
  ✗ 样本量过小（4 笔），PF=106.15 是噪声，不可信
  ✗ TP 双配置存在潜在冲突，需要立即验证
  ✗ 动态止损乘数偏紧（0.5），趋势行情容易被扫出
  ✗ CVD 过滤器虽然有配置但未开启，等于放弃了一道质量门
  ✗ stable_bear 通道完全没有成交，策略多样性不足

总体判断：
  当前配置是"干净但过度保守"的状态。
  下一步的核心任务不是继续收紧，而是有选择地、
  带质量门地扩展信号覆盖范围。

  建议最优先做的 3 件事：
    1. 修 TP 双配置冲突（零风险，立即做）
    2. 开 CVD 过滤器（提升质量，不降 MDD）
    3. 放宽 stable_bear ADX 门（恢复多样性）
```
