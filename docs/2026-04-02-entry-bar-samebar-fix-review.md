# Entry-Bar Same-Bar Fix Review

日期: `2026-04-02`  
审查人: Claude（hostile reviewer 口径）  
审查对象: `backtest_fund_flow_bot_like.py` entry-bar same-bar TP/SL 修复

---

## Q1. 这次 entry-bar same-bar 修复是否逻辑正确，是否值得保留？

**结论：逻辑正确，值得保留。但有一处细节需要继续确认。**

修复逻辑本身是自洽的：

- 原缺口已被具体数据证实（`tp1_hit_before_exit_bar = 3`，100% 发生在 entry bar）
- 修复后该桶归零（`= 0`），说明缺口已被直接命中，不是间接消除
- 修复限定在 `partial TP / full TP / stop`，**不** 在 entry bar 启用 breakeven / trailing，这个边界划定是正确的，没有顺手把模型做乐观

需要继续确认的一处细节：

**`_close_position_bot_like` 接收 `exit_price` 后，后续 bar 的止损价、持仓残量、以及 partial TP 后的 runner 状态是否已经正确更新。**

具体要审的是：same-bar 触发了 `tp1_before_stop`（先 partial TP，再 stop）之后，下一根 bar 进入时，`position.size`、`position.stop_loss`、`position.partial_done` 三个字段是否反映了 entry bar 里已经发生的事情。如果这三个字段没有被 entry-bar same-bar 路径正确写回，后续 bar 可能会用一个"以为仓位完整"的状态继续管仓，导致 runner 被错误放大或止损被错误重置。这个问题不会在 entry bar 的 trade record 里暴露，只会在后续 bar 的持仓行为里发酵。

除此以外，修复本身没有发现逻辑错误。

---

## Q2. `tp1_before_stop` 在只有 OHLC、没有 tick 顺序时，是合理、偏乐观、还是偏保守？

**结论：对 reclaim 类单子，`tp1_before_stop` 是偏乐观的，但在可接受范围内，前提是不允许自己认为它是中性口径。**

分析如下：

在只有 OHLC 的前提下，同一根 bar 里触及了 TP1 又触及了 stop，真实的 tick 顺序是不可知的。此时有三种可能的口径：

| 口径 | 描述 | 现实倾向 |
|------|------|----------|
| `tp1_before_stop` | 先记 partial TP，再记 stop | 偏乐观 |
| `stop_before_tp1` | 先记 stop，全损 | 偏保守 |
| `50/50 random` | 随机决定顺序 | 无偏但噪声大 |

对于 `red_bar_growing | long_reclaim_confirmed` 这类追强入场单，reclaim 信号本身意味着价格在上行突破后确认，入场时的动量通常先向上再回撤。因此 `tp1_before_stop` 在统计上与这类信号的价格路径有一定吻合——先触及 TP1 再被止损，是这类单子的常见形态，不是纯粹假设。

但这不等于它是中性口径。它仍然是一个乐观假设，理由是：

- 你无法排除一批单子是"开了就直接跌穿 stop，TP1 根本没有触及机会"的
- OHLC 口径下你永远无法区分这两种情况
- `tp1_before_stop` 系统性地假设前者，会让收益偏高、回撤偏低

当前接受这个口径的理由只有一个：**它比旧模型更保守**（旧模型完全跳过了 entry bar 检查，等于系统性地假设 entry bar 所有 TP/SL 都没有发生）。在这个比较基准下，`tp1_before_stop` 是一个合理的中间态，而不是终态。

如果要做 A/B 对照，正确的对照组是 `stop_before_tp1` 或 `stop_only_same_bar`，而不是旧的"跳过 entry bar"。

---

## Q3. 新基线是否应该正式取代旧基线？

**结论：应该取代，没有商量余地。**

旧基线（`+13.30% / 97 trades / 80.41% WR / 2.65% MDD`）是一个已知包含执行漏洞的结果。在这个结果上做的任何后续调参，等于在一个被证实口径错误的系统上做优化，所有结论都是污染的。

新基线（`+8.58% / 106 trades / 82.08% WR / 7.83% MDD`）有两个值得注意的变化：

**trades 从 97 增加到 106，而不是减少。**  
这是正常的：entry-bar same-bar 处理后，部分原先被"拖到后续 bar 才结算"的单子被提前结算，释放了仓位容量，导致后续开仓机会增加。这个方向是对的，不是 bug。

**MDD 从 2.65% 跳到 7.83%，涨幅显著。**  
这里需要单独审一下 MDD 的来源。有两种可能：  
一是 `stop_first` 类 same-bar 单子被正确记录后，原先被旧模型"推迟平仓"掩盖的止损现在一次性暴露，MDD 因此真实上升；  
二是 entry-bar same-bar 路径里的 `exit_price` 填充存在问题，导致个别单子的止损价格错误，产生虚假的大额亏损。  
在确认 Q1 里的持仓状态写回问题之前，7.83% MDD 的可信度需要保留疑问。如果持仓状态写回正确，MDD 上升是真实的，接受；如果有写回 bug，MDD 是虚高的，需要修复后重跑。

**综合判断：旧基线立即作废，新基线作为当前工作基线，但在 MDD 来源确认之前，不要在新基线上继续做执行链调优。**

---

## Q4. 下一步最该审的是不是 `tp1_hit_on_exit_bar_only` 的 bar 内顺序歧义？

**结论：是，但审之前必须先确认 Q1 里的状态写回问题。**

当前剩余的两个桶：

| 桶 | 数量 | 性质 |
|----|------|------|
| `tp1_hit_on_exit_bar_only` | 4 | bar 内顺序歧义 |
| `tp1_never_hit` | 3 | 价格扩张质量不足 |

**`tp1_never_hit` 这 3 笔不是执行口径问题，是信号质量问题。**  
它们说明这类 reclaim 单子在信号触发后根本没有足够的上行动量到达 TP1。这是策略层的问题，不是回测口径问题，不应该在这里修，应该放到 `long_reclaim_confirmed` 的信号质量专项拆解里处理。

**`tp1_hit_on_exit_bar_only` 这 4 笔才是 bar 内顺序歧义。**  
它们的含义是：TP1 不是在 entry bar 命中，而是在 exit bar（`time_exit` 或后续止损触发的那根 bar）才第一次命中。这意味着：

- 在 exit bar 上，TP1 和 exit 信号同时出现
- 当前逻辑判断是 exit 优先（`time_exit` 先触发，TP1 没有被执行）
- 正确的问题是：在 exit bar 上，`time_exit` 和 `TP1` 谁应该优先

这个问题的答案取决于：`time_exit` 是基于 bar close 触发，还是在 bar 内任意时刻触发。如果 `time_exit` 是"持仓时间超过 N 分钟就平"，那么在 exit bar 里，TP1 有可能在时间门控触发之前已经命中。如果这 4 笔的 TP1 命中价格在 bar 内确实先于 time_exit 触发时间，那么当前逻辑遗漏了 4 笔本可以 partial TP 的机会。

但注意：这 4 笔是在已经修复 entry-bar 缺口之后剩余的，数量少，影响有限。**在 Q1 的状态写回确认、以及 MDD 来源确认完成之前，不要优先处理这 4 笔，因为它们的影响量级低于当前已知的两个潜在 bug 的影响量级。**

---

## 优先级排序

```
第一步：审 Q1 的持仓状态写回（position.size / stop_loss / partial_done 在 entry-bar same-bar 后的正确性）
第二步：确认 MDD 7.83% 的来源（真实暴露 vs 状态 bug 导致的虚高）
第三步：在上述两项确认后，再审 tp1_hit_on_exit_bar_only 的 exit-bar 顺序歧义
```

`tp1_never_hit` 的 3 笔不在这条线上，归入策略信号质量专项。
