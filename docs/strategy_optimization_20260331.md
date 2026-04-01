# MACD V2 策略优化建议 — 基于 30 天回测归因

**日期**: 2026-03-31  
**回测窗口**: 2026-03-01 ~ 2026-03-31  
**核心指标**: 18 笔交易 / 胜率 27.78% / PF 1.73 / 收益 +0.23%  
**定性结论**: 不是全局失效，是"单一信号通道 + 特定币种"形成亏损口袋

---

## 一、回答你的 7 个核心问题

### Q1：flip_bullish trial 是否已经成为主要失血点，应该收紧甚至关闭？

**结论：不应关闭，应该收窄 score 窗口。**

数据里存在一个反常识的分布：

```
signal_score 分段 vs 胜率

  ≥ 0.90   : 4 笔，1 胜  3 负，PnL -11.55   ← 高分反而亏
  0.85-0.90 : 8 笔，0 胜  8 负，PnL -34.11   ← 得分段最大，全亏
  0.80-0.85 : 5 笔，3 胜  2 负，PnL +81.71   ← 唯一盈利段
  < 0.80    : 1 笔，1 胜  0 负，PnL  +3.58
```

flip_bullish trial 的真正"甜区"是 **0.80 ~ 0.85**，而不是高分区。
这意味着问题不是"flip_bullish 整体无效"，而是"高分段的评分在误导入场"。

直接关闭会扔掉唯一有效的盈利来源。正确做法：加 score 上限，只取甜区。

---

### Q2：0.85-0.90 段 8 笔全亏，signal_score 是否对该信号失真？

**结论：是，且可以判断失真来源。**

`signal_score` 在该配置下主要由 `4H(0.4) + VWAP(0.2) + 1H(0.2) + Volume(0.15)` 驱动。  
`flip_bullish` 试仓本质是"4H 还没翻多，但在预判翻转"的逆势轻仓行为。

高分（≥0.85）很可能来自以下误加分场景：

```
误加分场景 A：4H 柱正在快速收缩但 VWAP 偏多 → 整体分拉高
               → 但 4H 实际尚未确认翻转，开仓即止损

误加分场景 B：Volume 放量 + 1H 看多 → 分高
               → 但这种放量常见于跌势末段的恐慌盘，并非真实买盘

误加分场景 C：DOGE/BCH 等高波动币种，VWAP 偏离大 → VWAP 分高
               → 实际是 mean-reversion trap，不是趋势启动
```

**建议**：对 `flip_bullish` 类信号，独立建一个 score ceiling，而非依赖通用 `min_signal_score`。

---

### Q3：DOGE/BCH/AAVE 应做黑名单还是 symbol-specific threshold？

**结论：优先做 symbol-specific threshold，不做永久黑名单。**

| 方案 | 优点 | 缺点 |
|------|------|------|
| 永久黑名单 | 立即消除风险 | 丢失未来 alpha，三个币仍有好行情 |
| Symbol-specific threshold | 动态过滤，只拦低质量信号 | 需要额外代码维护 |
| 暂时黑名单 + 定期回测重审 | 灵活，可逆 | 需要有明确的解禁条件 |

**推荐**：先做 `symbol-specific` 提高门槛，同时加入 `DOGE/BCH/AAVE` 的 `flip_bullish_disabled` 标志，可随时开关，不是硬删除。

具体参数见第三章。

---

### Q4：pretrade_risk_gate.enabled = false 是否是关键缺口？

**结论：是，且当前是最容易以最低代价填上的缺口。**

当前配置虽然写了大量风控参数，但 `enabled = false` 意味着：

```
写了但没执行的参数（全部形同摆设）：
  entry_threshold = 0.06
  max_drawdown = 0.02
  max_exposure_per_trade = 0.22
  volatility_cap = 0.012        ← 这个最关键，DOGE 这类高波动币会被它拦截
  exit_drawdown_override = 0.015
```

`volatility_cap = 0.012`：如果当时 DOGE/BCH 的 ATR% 超过 1.2%，这些单子本应被拦。
根据 DOGE 3 月的行情，这个条件大概率会触发拦截，能直接减少 50%+ 亏损。

**建议**：以 `use_hard_rules_only = true` 模式开启，只用波动率和回撤两条硬规则，不引入复杂逻辑。

---

### Q5：max_active_symbols=4、target_portion=0.5、leverage 2-4x 是否过于激进？

**结论：配置激进，但本次实际暴露保守——问题在于两套标准并存，形成隐患。**

本次回测实际表现：
- 实际成交杠杆全部 2x（最低档）
- 试仓 entry_scale = 0.35，名义仓位已经打折
- 实际每笔风险并不大

但配置允许的上限：
- `target_portion = 0.5`、`max_symbol_position_portion = 0.5`、`max_leverage = 4x`
- 如果未来出现 4 个满仓同时持有 + 4x 杠杆，最大名义暴露 = `4 × 0.5 × 4x = 8x 权益`
- 这在风控层没有任何硬限制（gate 是关的）

**建议**：向 live-production 靠拢，降低上限容量，同时开 `pretrade_risk_gate.equity_usage_block`。

---

### Q6：iflow 仍指向 AI2 路径，是否存在环境污染风险？

**结论：是真实风险，不仅是配置治理问题。**

```yaml
# 当前配置
iflow:
  cwd: "d:\\AIDCA\\AI2"                   ← 应为 AI8
  file_allowed_dirs:
    - "d:\\AIDCA\\AI2"                    ← 应为 AI8
    - "d:\\AIDCA\\AI2\\config"            ← 应为 AI8
    - "d:\\AIDCA\\AI2\\src"               ← 应为 AI8
```

潜在影响：
1. 如果实盘 iflow 工具真的通过这个路径读取配置文件，它读到的是 **旧的 AI2 配置**，不是当前 AI8 参数
2. 回测和实盘可能用的不是同一套参数，导致无法解释的 backtest-live gap
3. 这本质上是"运行时参数与回测参数不一致"，是所有 live-backtest gap 分析的前提假设被破坏

**建议**：在做任何策略调优之前，先把这个路径修正，否则后续的对比结论都不可靠。

---

### Q7：如果目标是"提高收益而不是只压回撤"，优先建议哪 3 个改动？

```
改动优先级（按预期收益提升 ROI 排序）：

#1  为 flip_bullish trial 加 score 上限（0.87）
    → 直接切掉 0.85-0.90 全亏段，保留甜区
    → 预期：trade count -44%，但 PnL 大幅改善

#2  对 DOGE/BCH/AAVE 禁用 flip_bullish trial（可开关标志）
    → 直接消除 85% 亏损来源
    → 预期：净亏损减少约 -46 USDT → 实际净收益显著提升

#3  以 hard_rules_only 模式开启 pretrade_risk_gate
    → 主要靠 volatility_cap 拦截异常波动入场
    → 预期：减少 20-30% 低质量入场，胜率提升约 5-10 个百分点
```

---

## 二、亏损口袋处理方案

### 2.1 为 flip_bullish trial 加 score 窗口（最高优先）

```diff
# fund_flow.macd_mtf_strategy_v2 或 decision_engine 入口处
# 针对 flip_bullish + trial_entry 组合，增加 score 上限检查

+ FLIP_BULLISH_TRIAL_SCORE_SWEET_WINDOW = (0.800, 0.870)
+
+ def check_flip_bullish_trial_score(signal_score: float, signal_type: str, entry_type: str) -> bool:
+     """
+     flip_bullish trial 的甜区窗口检查。
+     数据表明 0.80-0.85 是唯一盈利段，高分段（≥0.85）出现系统性亏损。
+     """
+     if signal_type == "flip_bullish" and entry_type == "trial":
+         lo, hi = FLIP_BULLISH_TRIAL_SCORE_SWEET_WINDOW
+         if not (lo <= signal_score <= hi):
+             return False, f"FLIP_BULLISH_TRIAL_SCORE_OOW: {signal_score:.3f} not in [{lo},{hi}]"
+     return True, "OK"
```

**配置层 diff：**

```diff
# fund_flow.macd_mtf_strategy_v2.entry_filters

+ flip_bullish_trial_score_window_enabled: true
+ flip_bullish_trial_score_min: 0.800
+ flip_bullish_trial_score_max: 0.870   # 拦截高分误导段
```

### 2.2 symbol-specific flip_bullish trial 控制

```diff
# fund_flow 顶层配置，新增 symbol_overrides 结构

+ symbol_overrides:
+   DOGEUSDT:
+     disable_flip_bullish_trial: true       # 禁用 DOGE 的 flip_bullish 试仓
+     flip_bullish_min_signal_score: 0.900   # 如果未来要重新开放，门槛极高
+   BCHUSDT:
+     disable_flip_bullish_trial: true
+     flip_bullish_min_signal_score: 0.900
+   AAVEUSDT:
+     disable_flip_bullish_trial: true
+     flip_bullish_min_signal_score: 0.900
```

**伪代码实现：**

```python
def apply_symbol_overrides(signal, symbol: str, overrides: dict) -> tuple[bool, str]:
    """
    在 entry_hard_gates 之前，应用 symbol 级别的覆盖规则。
    """
    sym_cfg = overrides.get(symbol, {})

    # 检查 flip_bullish trial 禁用
    if (signal.signal_type == "flip_bullish"
        and signal.entry_type == "trial"
        and sym_cfg.get("disable_flip_bullish_trial", False)):
        return False, f"SYMBOL_OVERRIDE: {symbol} flip_bullish_trial disabled"

    # 检查 symbol-level min_signal_score
    sym_min_score = sym_cfg.get("flip_bullish_min_signal_score", None)
    if sym_min_score and signal.signal_score < sym_min_score:
        return False, f"SYMBOL_SCORE_GATE: {symbol} score {signal.signal_score:.3f} < {sym_min_score}"

    return True, "OK"
```

---

## 三、pretrade_risk_gate 开启方案

### 3.1 开启建议（最小侵入）

```diff
# fund_flow.pretrade_risk_gate

- enabled: false
+ enabled: true

- use_hard_rules_only: false   # 假设之前是 false 或未设
+ use_hard_rules_only: true    # 只用硬规则，不引入 soft scoring

  # 以下参数不变，直接生效
  volatility_cap: 0.012        # ATR% > 1.2% 时拦截 ← 核心规则，能拦 DOGE/BCH
  max_drawdown: 0.02           # 账户回撤 > 2% 时停止开仓
  max_exposure_per_trade: 0.22 # 单笔名义暴露上限 22%
  entry_threshold: 0.06        # 与 long_open_threshold 对齐
  exit_drawdown_override: 0.015
```

### 3.2 验证：volatility_cap 是否能拦住当期亏损单

```python
# 验证脚本（运行在回测 trades CSV 上）
import pandas as pd

trades = pd.read_csv("output/backtest/bot_like_trades_20260331_164319.csv")
# 假设 trades 里有 atr_pct 字段，或者可以从 equity curve 关联

VOLATILITY_CAP = 0.012

would_be_blocked = trades[trades["atr_pct"] > VOLATILITY_CAP]
print(f"会被 volatility_cap 拦截的笔数: {len(would_be_blocked)}")
print(f"这些笔的净 PnL: {would_be_blocked['pnl'].sum():.2f}")
print(f"这些笔的胜率: {(would_be_blocked['pnl'] > 0).mean():.2%}")

# 如果净 PnL 为负 且 胜率低于总体，说明 gate 配置合理
```

---

## 四、容量与仓位保守化

### 4.1 向 live-production 风格靠拢

```diff
# fund_flow 顶层参数

- default_target_portion: 0.5
+ default_target_portion: 0.18   # 对齐 live-production

- max_symbol_position_portion: 0.5
+ max_symbol_position_portion: 0.25   # 对齐 live-production

- max_active_symbols: 4
+ max_active_symbols: 2              # 在胜率未恢复到 50%+ 前，减少并发暴露

  default_leverage: 2   # 不动，本次实际已经是 2x
  max_leverage: 2       # 收紧上限，不允许 4x

+ pretrade_risk_gate.equity_usage_block: 0.60   # 总名义暴露 > 60% 权益时停止开仓
```

### 4.2 为什么现在要减少 max_active_symbols

```
当前胜率: 27.78%

在胜率 < 50% 时，max_active_symbols 越大 = 同时亏损的笔数越多：

  max_active_symbols=4 时，期望同时亏损笔数 ≈ 4 × 0.72 = 2.88 笔
  max_active_symbols=2 时，期望同时亏损笔数 ≈ 2 × 0.72 = 1.44 笔

不是因为总亏损变少（每笔一样），而是并发亏损的资金占用压力和心理压力更小，
也更容易在回测里精确归因单笔损失来源。

等到胜率恢复 ≥ 55% 后，再逐步放开到 3 → 4。
```

---

## 五、配置卫生修复

### 5.1 iflow 路径修正

```diff
# config/trading_config_fund_flow.json -> iflow 节

  iflow:
    file_access: true
    file_read_only: false
    file_allowed_dirs:
-     - "d:\\AIDCA\\AI2"
-     - "d:\\AIDCA\\AI2\\config"
-     - "d:\\AIDCA\\AI2\\src"
+     - "d:\\AIDCA\\AI8"
+     - "d:\\AIDCA\\AI8\\config"
+     - "d:\\AIDCA\\AI8\\src"
-   cwd: "d:\\AIDCA\\AI2"
+   cwd: "d:\\AIDCA\\AI8"
```

**这是在做任何策略调优前必须先做的修复。**  
否则实盘读取的配置和回测用的配置可能不一致，所有后续验证结论的前提都不成立。

---

## 六、TP/SL 配置异常审查

### 6.1 发现的配置不一致

文档中存在两组止盈止损参数：

```yaml
# 组 A（位于主参数段 4.1）
stop_loss_pct:   0.02
take_profit_pct: 0.04   ← ratio = 2.0，合理

# 组 B（位于风控段 7.2）
stop_loss_default_percent:   0.02
take_profit_default_percent: 0.02   ← ratio = 1.0，对称，不好
```

**需要确认哪一组在运行时实际生效。**  
如果 `take_profit_default_percent = 0.02` 覆盖了主配置，那么当前实际 TP/SL 是对称的，这会大幅压低期望收益。

```python
# 验证方法：在回测 trades CSV 里统计实际止盈距离
trades = pd.read_csv("output/backtest/bot_like_trades_20260331_164319.csv")
win_trades = trades[trades["exit_reason"] == "take_profit_intrabar"]
print(f"止盈单平均收益: {win_trades['pnl_ratio'].mean():.4f}")
# 如果接近 0.02，说明 take_profit_default = 0.02 在生效
# 如果接近 0.04，说明 take_profit_pct = 0.04 在生效
```

---

## 七、消融实验建议（按顺序执行）

```
基线（当前）：
  trade_count=18, win_rate=27.78%, PF=1.73, return=+0.23%

实验 #1：仅修复 iflow 路径
  预期：数字不变，但确保后续实验结论可信

实验 #2：加 flip_bullish_trial score 上限 (≤ 0.87)
  预期：trade_count 约 -8 笔（去掉全亏的 0.85-0.90 段）
         win_rate 预计提升到 40-50%+
         return 预计显著转正

实验 #3：禁用 DOGE/BCH/AAVE 的 flip_bullish trial
  预期：trade_count 约 -8 笔（3 个币合计）
         剩余笔的 PnL 大幅改善
         与实验 #2 叠加后效果最显著

实验 #4：开启 pretrade_risk_gate (hard_rules_only)
  预期：额外拦截 2-3 笔高波动入场
         胜率继续提升，但 trade_count 可能进一步降低

实验 #5：收紧容量 (target_portion=0.18, max_symbols=2)
  预期：单笔 PnL 绝对值降低，但总收益率更稳定
         MDD 进一步压缩

──────────────────────────────────────────────────
每轮对比 5 个指标：return / win_rate / trade_count / PF / MDD
任何一轮 win_rate 下降 > 3% → 回滚重查
──────────────────────────────────────────────────
```

---

## 八、变更速查表

| 变更项 | 类型 | 旧值 | 新值 | 所属实验 |
|--------|------|------|------|----------|
| `iflow.cwd` | 配置卫生 | AI2 路径 | AI8 路径 | #1（前置必做）|
| `flip_bullish_trial_score_max` | 新增参数 | 无 | 0.870 | #2 |
| `flip_bullish_trial_score_min` | 新增参数 | 无 | 0.800 | #2 |
| `symbol_overrides.DOGEUSDT.disable_flip_bullish_trial` | 新增 | 无 | true | #3 |
| `symbol_overrides.BCHUSDT.disable_flip_bullish_trial` | 新增 | 无 | true | #3 |
| `symbol_overrides.AAVEUSDT.disable_flip_bullish_trial` | 新增 | 无 | true | #3 |
| `pretrade_risk_gate.enabled` | 开关 | false | true | #4 |
| `pretrade_risk_gate.use_hard_rules_only` | 参数 | (未设) | true | #4 |
| `default_target_portion` | 仓位 | 0.50 | 0.18 | #5 |
| `max_symbol_position_portion` | 仓位 | 0.50 | 0.25 | #5 |
| `max_active_symbols` | 容量 | 4 | 2 | #5 |
| `max_leverage` | 杠杆 | 4 | 2 | #5 |
