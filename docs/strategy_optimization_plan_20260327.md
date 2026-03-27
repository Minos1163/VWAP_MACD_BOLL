# 策略优化方案：132% → 63% 衰减分析与修复路径

**日期**：2026-03-27  
**背景**：参数收敛操作后，策略级回测从 `+132.40% / 73.7%` 降至 `+63.10% / 70.35%`  
**核心判断**：衰减来自参数变化，不是执行层问题，可被精确溯源

---

## 第一步：定位衰减根因

两次回测使用**同一回测器、同一段数据**，唯一变量是配置文件。衰减完全来自参数变化，可以通过消融实验精确定位。

### 消融实验脚本（伪代码）

```python
configs_to_test = [
    ("baseline_132",      original_backtest_config),
    ("live_converged",    live_backtest_converged_config),

    # 逐项还原，观察每步收益变化
    ("restore_threshold", live_config.override(
        long_open_threshold=original_config.long_open_threshold,
        short_open_threshold=original_config.short_open_threshold,
    )),
    ("restore_tp_sl", live_config.override(
        take_profit_pct=original_config.take_profit_pct,
        stop_loss_pct=original_config.stop_loss_pct,
    )),
    ("restore_leverage", live_config.override(
        default_leverage=original_config.default_leverage,
        max_leverage=original_config.max_leverage,
    )),
    ("restore_max_symbols", live_config.override(
        max_active_symbols=original_config.max_active_symbols,
    )),
    ("restore_close_threshold", live_config.override(
        close_threshold=original_config.close_threshold,
    )),
]

for name, cfg in configs_to_test:
    result = run_backtest(cfg, start="2026-02-25", end="2026-03-27")
    print(f"{name}: {result.total_return:.2%} / {result.win_rate:.2%} / "
          f"trades={result.trade_count} / mdd={result.max_drawdown:.2%}")
```

运行后得到**收益归因表**，直接看哪一步还原让收益跳回最多，再做定向修复。

---

## 第二步：高嫌疑衰减来源分析

### 嫌疑一：`max_active_symbols = 3`（🔴 高嫌疑）

这是**最容易被低估的收益压缩源**。

原始 `132%` 回测若允许同时持有更多 symbol（如 5~8 个），在趋势行情中会并行捕获多条主升浪。收紧到 3 个后：

- 容量瓶颈频繁触发，高分信号被截断
- 组合机会集直接缩小

**验证方式**：把 `max_active_symbols` 从 3 恢复到原始值，观察收益变化幅度。

---

### 嫌疑二：`take_profit_pct / stop_loss_pct = 0.02` 对称设置（🟠 中高嫌疑）

对称的 2% TP / 2% SL 意味着**盈亏比为 1:1**，策略只能靠胜率盈利。若原始配置 TP 更大或 SL 更紧，盈亏比会显著更好。

| 配置场景 | 期望值计算 | 期望收益 |
|---|---|---|
| 原始：TP=0.03 / SL=0.015 | `0.7×0.03 − 0.3×0.015` | **+1.65%** |
| 当前：TP=0.02 / SL=0.02 | `0.7×0.02 − 0.3×0.02` | **+0.80%** |

盈亏比变化可以在**不改变胜率**的情况下，让期望收益减半。

---

### 嫌疑三：入场阈值收紧（🟡 中嫌疑）

```
当前：long_open_threshold = 0.09 / short_open_threshold = 0.07
```

如果原始基线阈值更低（更容易触发），`132%` 那条曲线可能来自更高的交易频率。

**验证方式**：对比两次回测的 `total_trades`。若原始明显多于当前 607 笔，说明阈值收紧正在压缩机会集。

---

### 嫌疑四：杠杆上限（🟡 中嫌疑）

```
当前：default_leverage = 3 / max_leverage = 4
```

杠杆直接线性影响名义收益。若原始使用更高杠杆（如 default=5, max=10），收益天然更高但回撤也更大。这不一定是需要恢复的项，但**需要量化它贡献了多少收益差**，避免把杠杆收缩误判为策略失效。

---

## 第三步：优化方向建议

在消融实验结果出来之前，以下方向可**安全尝试**：

### 方向 A：放开 `max_active_symbols`（最优先验证）

```diff
- max_active_symbols: 3
+ max_active_symbols: 5   # 先试 5，观察 MDD 变化
```

预期效果：触发频率不变，但高分信号被截断的比例下降，总收益提升。  
风险：最大回撤可能随并发持仓数增加而上升，需确认 MDD 仍在可接受范围（当前基线 14.52%）。

---

### 方向 B：非对称 TP/SL，改善盈亏比

```diff
- take_profit_pct: 0.02
- stop_loss_pct:   0.02

+ take_profit_pct: 0.03   # 扩大 TP
+ stop_loss_pct:   0.018  # 略微收紧 SL
```

不追求完全还原原始参数，而是在当前胜率基础上改善期望值。盈亏比从 1:1 → 1.67:1，在 70% 胜率下期望收益显著提升。

---

### 方向 C：差异化入场阈值（需消融实验确认）

```diff
# 如果消融实验确认阈值收紧减少了交易次数
- long_open_threshold:  0.09
- short_open_threshold: 0.07

+ long_open_threshold:  0.07
+ short_open_threshold: 0.06
```

前提：消融实验确认阈值收紧是主要衰减来源之一。

---

### 方向 D：`close_threshold` 方向待确认

```
当前：close_threshold = 0.3
```

若原始配置的平仓阈值更低（如 0.2），说明现在**持仓更久才平**，在趋势反转时可能吃更多回撤；若原始更高，说明现在过早平仓。此方向需根据消融实验结果确定修改方向，不建议盲调。

---

## 第四步：关于 `pretrade_risk_gate` 和 `signal_pool`

这两项在纯策略级回测中**不存在**，所以它们**不是** `132% → 63%` 这段衰减的原因（该衰减纯粹由参数变化导致）。

但它们是**实盘 vs 回测**之间的额外稀释层，会在 63% 基础上进一步拉低实际收益：

- `pretrade_risk_gate`：当前 hard-rules-only 配置已较克制，暂不是优先优化目标
- `signal_pool`：值得检查一次实际触发频率，若过滤超过 10% 的信号，需要具体分析被过滤信号的事后表现

---

## 最短行动清单

| 步骤 | 时间 | 动作 |
|---|---|---|
| 第一步 | 今天 | 运行消融实验，输出每个参数变化的收益贡献表 |
| 第二步 | 明天 | 根据结果决定：还原原始参数，还是采用新优化值 |
| 第三步 | 本周 | 在策略级回测中找到收益 ≥ 80%、MDD ≤ 15% 的参数组合，作为新实盘基线 |
| 第四步 | 下周 | 部署新基线，保留 `alpha_dilution_monitor` 追踪实盘 vs 回测的信号漏损 |

---

## 核心原则

> **不要在没有消融实验数据的情况下盲调参数。**
>
> `132% → 63%` 这段衰减可以被精确溯源。先把根因找清楚，再做定向优化。每次只改一个变量，每次改动都跑完整 30 天回测，用数据驱动决策，而不是靠直觉连续调参。

---

*本文档生成于 2026-03-27，基于实盘收敛后回测衰减分析。*
