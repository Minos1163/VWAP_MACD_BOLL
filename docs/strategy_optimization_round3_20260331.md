# MACD V2 Round 2 审核 & Round 3 优化方向

**日期**: 2026-03-31  
**审核基础**: Round 2 最终配置 / 4 笔 / 胜率 75% / MDD 0.32% / return +0.87%  
**核心发现**: CVD 过滤器对 trial 路径存在结构性不兼容，全局应用会破坏核心 alpha 流  
**本轮目标**: 在 30 天 4 笔的基础上，识别下一个安全扩展方向

---

## 一、回答 5 个技术问题

### Q1：trial 路径 CVD 过滤当前机会成本是否过高？

**结论：是，且原因是结构性的，不是参数边界问题。**

```
关键证据：
  strict CVD (delta ≥ 0.03)  → 4 笔 → 1 笔
  relaxed CVD (delta ≥ 0.02) → 4 笔 → 1 笔   ← 降阈值毫无帮助

这个结果说明：不是 0.03 太严，而是 trial 信号本身天然缺少 CVD 积累。

原因（机制层面）：
  trial entry 的本质 = "4H 还没翻，提前轻仓预判翻转"
  在开仓时刻，价格刚刚开始反弹，CVD 的净买盘流入还没有积累
  → CVD 天然偏低，不是信号质量差，而是时序问题

  CVD 高的时候 = 趋势已经启动 = 不再是 trial，应该是 full-size entry
  所以"要求 trial 有高 CVD"本身就是逻辑矛盾

结论：
  全局 trial CVD 过滤 = 用错了工具
  正确用法 = 用 CVD 过滤 full-size flip_bullish（已经有效）
           = 或者用 CVD 作为特定高风险 symbol 的额外门（见 Q2）
```

---

### Q2：CVD 是否应该只作为 DOGE/BCH/AAVE 的 symbol-specific gate？

**结论：是，这是 CVD 在当前配置里最合理的用法。**

```
CVD 过滤器的有效作用场景：
  ✓ 高波动、易反转的币种（DOGE/BCH/AAVE 的典型失效模式）
  ✓ 需要确认真实买盘才开仓的场景
  ✗ 所有 trial entries（时序上不合理）
  ✗ 低波动、趋势明确的币种（反而过滤掉有效信号）

推荐架构：

  全局配置：
    enable_flip_bullish_cvd_context_filter = false   ← 保持关闭

  DOGE/BCH/AAVE 解禁时（未来 Round）：
    symbol_overrides.DOGEUSDT:
      disable_flip_bullish_trial: false
      require_cvd_confirm_for_trial: true   ← symbol 级别的 CVD 要求
      flip_bullish_min_cvd_1h_delta_ratio_override: 0.025
      flip_bullish_min_adx_4h_override: 30

  这样：
    其他币种的 trial → 走正常路径，不受 CVD 约束
    DOGE/BCH/AAVE 的 trial → 必须满足 CVD + ADX，才允许进
```

---

### Q3：boll_stop_atr_multiplier=0.8 是否合理，还是应该按行情自适应？

**结论：0.8 作为静态默认值合理，但应该是下一轮改造的优化方向。**

```
静态 0.8 的合理性：
  原来的 0.5 太紧，在趋势行情中频繁被扫出
  0.8 是向"让趋势跑"方向的合理修正
  当前只有 4 笔样本，不足以判断方向准确与否，先保持稳定

自适应的必要性（下一轮）：

  波动行情  ADX < 28, ATR% > 1.6%：
    止损应该紧 → multiplier = 0.6
    原因：振荡行情里宽止损会把盈利全部回吐

  趋势行情  ADX ≥ 32, ATR% ≤ 1.8%：
    止损应该宽 → multiplier = 1.0 ~ 1.2
    原因：正常回调幅度更大，0.8 仍然可能过早出局

  过渡态（其他）：
    multiplier = 0.8（当前默认）

伪代码（配合上轮双模风控框架）：

  def get_boll_stop_multiplier(adx: float, atr_pct: float) -> float:
      if adx >= 32 and atr_pct <= 0.018:
          return 1.1   # 强趋势：宽止损
      elif atr_pct >= 0.016 or adx < 28:
          return 0.6   # 波动行情：紧止损
      else:
          return 0.8   # 中性：当前默认

本轮建议：维持 0.8 静态，把自适应列入 Round 3 实验计划。
```

---

### Q4：stable_bear_continuation_min_adx_1h=25.0 没有产生成交，是否还有意义？

**结论：继续保留，无成交的原因是市场条件，不是参数错误。**

```
3 月没有 stable_bear 成交的可能原因分析：

  A. 3 月市场整体偏多 → 没有持续空头趋势 → 正常，参数无问题
  B. stable_bear 其他门槛仍然过高 → 需要进一步检查
  C. 回测的 symbol pool 里没有足够的空头机会标的

如何区分 A / B / C：

  # 诊断脚本
  python scripts/backtest_fund_flow_bot_like.py \
      --config config/trading_config_fund_flow.json \
      --log-level DEBUG \
      --filter-signal-type stable_bear_continuation \
      --output-rejected-reasons output/analysis/stable_bear_reject_reasons.csv

  # 查看被拒绝的 stable_bear 信号，按拒绝原因统计
  # 如果大量信号在 ADX 门被拒 → 参数还可以继续放宽
  # 如果大量信号在 VWAP 门被拒 → 应该放宽 stable_bear_continuation_min_vwap_score
  # 如果信号数量本身就少 → 市场条件问题，参数无需再动

建议：
  维持 adx_1h = 25.0
  运行上述诊断脚本，找到真实瓶颈
  下一个放宽应该针对诊断结果，而不是盲猜
```

---

### Q5：trial CVD 代码保留 merged 但 disabled，还是回滚？

**结论：保留 merged + disabled，不回滚。理由充分。**

```
保留的理由：
  ✓ 已有 3 个专项测试覆盖，代码质量有保障
  ✓ 未来 DOGE/BCH/AAVE 解禁时，这段代码是必要的基础设施
  ✓ disabled 在生产配置里等于零风险
  ✓ 回滚意味着未来重写，浪费精力

保留时需要注意的事项：

  1. 在代码里加明显注释，说明为什么 disabled
  2. 在 config 的 changelog 里记录这次决策

# macd_strategy_v2.py 建议注释
# CVD context filter for trial entries:
# Disabled in production config (enable_flip_bullish_cvd_context_filter = false)
# Rationale: trial entries are pre-emptive and structurally lack CVD accumulation.
# Intended use: symbol-specific gate for DOGE/BCH/AAVE when they are re-enabled.
# Re-enable only after validating on ≥ 90-day dataset showing CVD predicts trial quality.
# Decision date: 2026-03-31 / Round 2 ablation.
```

---

## 二、当前配置的完整评估

### 2.1 已经做对的部分

```
✓ flip_bullish trial score window [0.80, 0.87]
    → 数据验证的甜区，机制正确

✓ DOGE/BCH/AAVE disable
    → 切掉了 85%+ 的亏损来源

✓ pretrade_risk_gate hard_rules_only
    → volatility_cap + equity_usage_block 覆盖主要系统性风险

✓ TP 配置统一 (0.04 / 0.04)
    → 消除了双轨覆盖隐患

✓ boll_stop_atr_multiplier: 0.5 → 0.8
    → 方向正确，减少趋势行情的误扫

✓ stable_bear ADX: 30 → 25
    → 方向正确，等待合适市场条件触发

✓ CVD trial 代码 merged + disabled
    → 既保留基础设施，又不引入生产风险
```

### 2.2 仍然存在的问题

```
✗ 30 天 4 笔，样本量不足以做统计判断
    → PF=106.15、胜率 75% 都是噪声
    → 需要扩大样本，而不是继续调这 4 笔

✗ 策略多样性不足
    → 全部 flip_bullish，没有空头，没有延续类信号
    → 单一信号路径风险：一旦 flip_bullish 行情消失，策略失效

✗ 信号覆盖范围不清楚
    → 不知道当前每个 symbol 上有多少信号被各层 gate 拦截
    → 没有信号漏斗数据，无法判断下一个瓶颈在哪里
```

---

## 三、Round 3 优化方向

### 核心判断

```
当前问题不是"参数不好"，而是"样本太小 + 信号太单一"。

继续在 4 笔样本上调参 = 过拟合

Round 3 的正确做法：
  1. 扩大回测窗口到 90 天（1 月 ~ 3 月）
  2. 运行信号漏斗诊断，找到真实的机会瓶颈
  3. 针对瓶颈做单点改动，而不是凭感觉盲调
```

### 3.1 第一优先：扩展回测窗口

```
当前问题：30 天 4 笔，统计意义为零。

建议：
  将主回测窗口扩展到 90 天（2025-12-01 ~ 2026-03-01）
  或者 60 天（2026-01-01 ~ 2026-03-01）

预期收益：
  样本量预计提升到 10-20 笔
  才能开始有意义地分析 win_rate 和 PF
  才能判断 score window / CVD / ADX 等参数是否真的有效
```

```bash
# 建议运行命令
python scripts/backtest_fund_flow_bot_like.py \
    --config config/trading_config_fund_flow.json \
    --start "2025-12-01 00:00:00" \
    --end   "2026-03-31 23:59:59" \
    --output-prefix output/backtest/bot_like_90d_
```

### 3.2 第二优先：运行信号漏斗诊断

```
不扩展任何参数，先搞清楚当前配置下，信号在哪一层被拦截最多。

目标：生成类似下面这张表：

  ┌─────────────────────────────────┬────────┬────────┐
  │ Gate 层                         │ 通过数 │ 拦截数 │
  ├─────────────────────────────────┼────────┼────────┤
  │ 原始信号                         │  800   │   -    │
  │ after score_window [0.80, 0.87] │  120   │  680   │ ← 这里是大瓶颈
  │ after symbol_overrides          │   90   │   30   │
  │ after entry_filters             │   25   │   65   │
  │ after L1 structural gate        │   12   │   13   │
  │ after L2 flow gate              │    8   │    4   │
  │ after L3 microstructure gate    │    5   │    3   │
  │ after pretrade_risk_gate        │    4   │    1   │ ← 最终成交
  └─────────────────────────────────┴────────┴────────┘

只有看到这张表，才能知道下一个放宽应该在哪一层。
```

```python
# 信号漏斗诊断伪代码（需要在 backtest 框架里加 instrumentation）
class SignalFunnelLogger:
    def __init__(self):
        self.counts = defaultdict(lambda: {"pass": 0, "reject": 0})

    def log(self, gate_name: str, passed: bool, reason: str = ""):
        key = "pass" if passed else "reject"
        self.counts[gate_name][key] += 1
        if not passed:
            self.reject_reasons[gate_name][reason] += 1

    def report(self):
        print(f"{'Gate':<40} {'Pass':>8} {'Reject':>8} {'Pass%':>8}")
        for gate, counts in self.counts.items():
            total = counts["pass"] + counts["reject"]
            pct = counts["pass"] / total * 100 if total > 0 else 0
            print(f"{gate:<40} {counts['pass']:>8} {counts['reject']:>8} {pct:>7.1f}%")
```

### 3.3 第三优先：stable_bull_continuation 评估

```
当前配置：enable_stable_bull_continuation = false

3 月行情以多头为主，为什么没有 stable_bull 成交？
原因：stable_bull 被完全禁用了。

建议：
  先不开启，先跑 stable_bull 的 shadow 诊断（只记录信号，不实际开仓）
  看 3 月里 stable_bull 有多少潜在信号，质量如何
  然后再决定是否开启

实现方式：
  enable_stable_bull_continuation: false
  stable_bull_continuation_shadow_log: true   # 新增：只记录不交易
```

```diff
# 如果决定开启（验证后）：
- enable_stable_bull_continuation: false
+ enable_stable_bull_continuation: true
+ stable_bull_continuation_min_signal_score: 0.83   # 与 stable_bear 对称
+ stable_bull_continuation_min_vwap_score:   0.12
+ stable_bull_continuation_min_adx_1h:       25.0   # 与 stable_bear 对称
```

### 3.4 第四优先：weight_4h_enhancement 重新评估

```
当前评分权重：
  weight_4h_direction:    0.35
  weight_4h_enhancement:  0.05   ← 极低
  weight_1h_direction:    0.20
  weight_vwap:            0.20
  weight_15m_entry:       0.05
  weight_volume:          0.15

问题：
  4H enhancement（preflip 动量、4H 翻转质量）对 trial 信号最直接相关
  但它的权重只有 0.05，几乎没有区分能力

  对比：4H direction 权重是 0.35，但 trial 入场时 direction 还没翻，
  这个权重对应的是"旧趋势余温"，不是翻转质量

建议（Round 3 实验）：
  在 90 天数据上，测试以下权重调整：
```

```diff
# 实验版本（需要 90 天数据验证）
  weight_4h_direction:   0.30   # 轻微降低
  weight_4h_enhancement: 0.10   # 翻倍：让 preflip 动量更有区分度
  weight_1h_direction:   0.20   # 不动
  weight_vwap:           0.20   # 不动
  weight_15m_entry:      0.05   # 不动
  weight_volume:         0.15   # 不动
```

---

## 四、Round 3 完整实验计划

```
前置条件（必须先完成，才能做后面的实验）：

  PRE-1：扩展回测窗口到 90 天
    命令：见 3.1
    完成标志：样本量 ≥ 15 笔

  PRE-2：运行信号漏斗诊断
    完成标志：得到每一层的 pass/reject 分布表
    决策：根据表里最大瓶颈层决定下一个实验目标

─────────────────────────────────────────────────────

实验顺序（依赖 PRE-1 和 PRE-2 完成后执行）：

  #R3-1：stable_bull shadow 诊断
    变更：只加日志，不开 stable_bull
    目的：摸清楚 3 月的 stable_bull 机会有多少

  #R3-2：weight_4h_enhancement: 0.05 → 0.10
    预期：trial 信号分数分布改变，score window 效果更准确
    验收：score 0.80-0.87 段的胜率提升（用 90 天数据衡量）

  #R3-3：开启 stable_bull（如果 shadow 日志显示质量合格）
    预期：trade_count 增加，信号多样性提升
    验收：stable_bull 单独胜率 ≥ 50%，不拉低整体 win_rate

  #R3-4：boll_stop 自适应（依赖 #R3-1~3 稳定后）
    变更：multiplier 由 ADX + ATR% 动态决定
    预期：趋势行情 avg_win 提升，波动行情 MDD 降低

  #R3-5：DOGE/BCH/AAVE 从 disable 迁移到 CVD+ADX 双门 threshold
    前提：#R3-2 证明 CVD 作为 symbol-level gate 有效
    预期：增加 2-5 笔/月，整体 win_rate 不低于 55%

─────────────────────────────────────────────────────

每轮验收标准（90 天数据）：
  ✓ trade_count ≥ 15 笔（统计意义基准）
  ✓ win_rate ≥ 55%
  ✓ profit_factor ≥ 1.5
  ✓ MDD 增加 ≤ 0.5% per 实验
  ✗ win_rate < 45% → 立即回滚
```

---

## 五、Round 2 → Round 3 变更对比速查

| 参数 | Round 1 | Round 2 | Round 3 建议 | 时序 |
|------|---------|---------|-------------|------|
| `take_profit_default_percent` | 0.02 | **0.04** ✓ | 保持 | 已完成 |
| `boll_stop_atr_multiplier` | 0.5 | **0.8** ✓ | → 自适应 0.6/0.8/1.1 | R3-4 |
| `stable_bear_continuation_min_adx_1h` | 30.0 | **25.0** ✓ | 保持，等诊断 | 已完成 |
| `enable_flip_bullish_cvd_context_filter` | false | false（rejected）| symbol-level only | R3-5 前置 |
| `enable_stable_bull_continuation` | false | false | → shadow → true | R3-1/3 |
| `weight_4h_enhancement` | 0.05 | 0.05 | → 0.10 | R3-2 |
| `DOGE/BCH/AAVE disable` | true | true | → CVD+ADX threshold | R3-5 |
| 回测窗口 | 30 天 | 30 天 | **→ 90 天** | PRE-1 |

---

## 六、一句话总结

```
Round 2 的核心贡献不是数字改善，而是发现了 CVD 与 trial 路径的结构性不兼容，
避免了在错误方向上继续消耗迭代次数。

Round 3 的正确起点是：先扩大样本（90 天），再跑漏斗诊断，
再针对诊断结果做单点改动，而不是在 4 笔样本上继续猜测。
```
