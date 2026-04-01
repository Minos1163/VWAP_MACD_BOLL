# MACD V2 盈利提升与优化方向建议

日期：2026-03-31

## 1. 结论先行

基于你提供的 `MACD V2 Close-Risk Review`，我的核心判断是：

1. **当前基线本身已经是质量较高的版本**，`264 trades / 77.27% WR / +22.19% / PF 2.24 / MDD 3.00%` 说明系统并不缺“可交易性”，缺的是**对负收益 pocket 的进一步提纯**。
2. **这套系统的盈利引擎不是高频小赚，而是少数厚利润单**。从出场归因看，`25` 笔 `take_profit_intrabar` 贡献了 `+3135.35`，这是绝对主利润来源。
3. 因此，后续优化的首要原则不是“把亏损再切浅一点”，而是：
   - **保住厚利润结构**
   - **削弱负收益 pocket 的进入概率**
   - **避免 exit 改动带来 trade cadence 膨胀**
4. 本轮 close-risk ablation 已经证明：
   - 全局提前保本、提前 trailing、缩短 TP，虽然能让 `avg_loss` 变浅，
   - 但同时会更大幅度压缩 `avg_win`，
   - 最终把系统从“低频厚利”打成“高频薄利”，这是当前策略最不应该走的方向。

---

## 2. 对当前策略盈利结构的判断

## 2.1 基线的真实赚钱方式

从你给的数据看，当前基线的盈利结构有三个非常鲜明的特征：

### A. 依赖厚利润单

出场归因：

| reason | count | pnl |
|---|---:|---:|
| `take_profit_intrabar` | 25 | +3135.35 |
| `stop_loss_intrabar` | 218 | -653.53 |
| `4h_shrink_exit` | 16 | +21.91 |
| `signal_reverse` | 2 | -51.60 |

这说明：

- 真正把净值拉上去的不是大量小盈单；
- 而是少数吃到固定 TP 的盈利单；
- 所以 **任何让盈利单更早出场的机制，都天然有较高概率伤害总收益**。

### B. 当前系统对亏损的容忍，本质上是为了换取更高的盈亏比

基线：

- `avg_win = 22.35`
- `avg_loss = -33.90`
- `PF = 2.24`
- `WR = 77.27%`

这不是一个靠“亏损特别浅”赚钱的系统，而是一个靠：

- 胜率较高，
- 配合少数厚利润单，
- 拉出正向收益分布

来赚钱的系统。

### C. 负收益 pocket 已经被定位出来

最值得处理的是：

| signal_type_1h | vwap_state | count | pnl |
|---|---|---:|---:|
| `red_bar_growing` | `long_dual_support` | 91 | -67.25 |

这个 pocket 的问题不是“完全不会赢”，而是**赢得不够厚、亏的时候又会吃深**，因此长期净值为负。

这类 pocket 的正确处理方式通常不是“给它更聪明的平仓”，而是：

- **少做**
- **只在更优质条件下做**
- **把它限制在少数适合的 symbol 上做**

---

## 3. 为什么本轮 close-risk 优化没有提升盈利

## 3.1 你改的是 exit，但实际改坏了系统节奏

从 `264 trades` 扩大到 `405~438 trades`，说明这轮不是单纯在改“单笔出场”，而是在改：

- 资金释放速度
- 仓位占用时长
- 后续信号接入密度

在你当前架构下，由于：

- `max_active_symbols = 2`
- `default_target_portion = 0.18`
- 单笔仓位不小
- 并发数受限

所以 **exit 决定了资金周转率，而资金周转率会直接改变 entry 数量**。

因此：

> 在当前回测框架里，“只改平仓”并不等于“只改平仓效果”；它等价于同时改了资金使用效率和交易频率。

这也是为什么 close-risk 看起来只是保守一点，最后却把整套系统做成了另一种风格。

## 3.2 `avg_loss` 变浅，但 `avg_win` 被压扁得更厉害

实验结果最关键的事实是：

| 方案 | avg_win | avg_loss |
|---|---:|---:|
| baseline | 22.35 | -33.90 |
| E2b | 11.24 | -17.91 |
| E3b | 11.33 | -17.88 |
| E4 | 12.08 | -18.78 |
| E2c | 12.13 | -21.82 |
| E3c | 12.19 | -21.83 |

结论非常明确：

- 亏损确实被切浅了；
- 但盈利被砍得更厉害；
- 结果就是 PF 和 return 一起下去。

这说明这套策略的盈利来源和你当前 close-risk 的优化方向是**错位**的。

---

## 4. 提高盈利的正确主线

我建议后续盈利提升，不再以“全局 exit 收紧”为主线，而改成下面三条主线。

## 4.1 主线一：保住厚利润引擎，而不是全局修剪

### 核心原则

后续任何优化，优先保护：

- `green_bar_growing` 的主盈利能力
- 已经证明能吃到 4% TP 的趋势型盈利单
- 像 `green_bar_growing + short_dual_pressure` 这类已经赚钱的口袋

### 明确不建议做的事

1. **不建议全局把固定 TP 从 4% 降到 2%**
2. **不建议全局提前 breakeven 到 0.5% 左右**
3. **不建议给所有通道统一上早激活 trailing**
4. **不建议按 `trend / oscillation` 这种过粗分类直接映射 trailing**

原因很简单：

- 这些动作都会优先杀掉能跑出来的单；
- 而你当前最大的利润来源恰恰就是少数能跑出来的单。

### 更合理的方向

后续如果要做 exit 优化，只能做：

- **局部**优化
- **口袋级**优化
- **不改变主盈利 pocket 的持仓形态**

也就是：

> 先圈定“有问题的口袋”，只在这些口袋上做处理；已经赚钱的口袋，原则上少动。

---

## 4.2 主线二：把 `long_dual_support` 当成 entry alpha 问题处理

从这轮实验看，我同意文档中的结论：

> `long_dual_support` 更像开仓 alpha 不足，而不是 exit 参数没调好。

理由有三点：

### A. 基线下它已经是负收益 pocket

基线：`-67.25`

说明即使在原本更宽松、更“有利润厚度”的退出体系下，它也没有产出正期望。

### B. 只修 exit，反而把它做得更差

| 方案 | `long_dual_support` pnl |
|---|---:|
| baseline | -67.25 |
| E2b | -238.98 |
| E3b | -219.00 |
| E2c | -399.59 |
| E3c | -383.64 |
| E4 | -32.21 |

这类结果通常意味着：

- 不是“止损太宽”；
- 而是“入场后没有足够稳定的后续推进”；
- 所以不管怎么修 exit，最终都很难把一个负 alpha pocket 修成正 alpha pocket。

### C. 它更像反弹结构，而不是趋势延续结构

`long_dual_support` 天然更接近：

- 支撑反弹
- 均值回归
- 结构回补

这种结构对：

- symbol 个性
- 波动环境
- 1H / 4H 同步性
- VWAP reclaim 质量

通常更敏感。

所以它更适合从“准入条件”做提纯，而不是从“出场技巧”做修饰。

---

## 4.3 主线三：下一轮盈利提升的最大机会在 symbol × pocket 治理

我认为你下一轮最值得做的，不是再改全局参数，而是做：

> `signal_type × vwap_state × symbol` 的三维归因

因为当前信息已经显示：

- 不是所有 pocket 都有问题；
- 不是所有 symbol 都不适合 `long_dual_support`；
- 你已经在 round3 之前通过 pocket 清理得到过提升；
- 所以下一步最可能的收益，来自更细颗粒度的局部禁用，而不是大改全局框架。

---

## 5. 我建议的具体优化方向

## 5.1 第一优先级：对 `long_dual_support` 做 symbol-level 局部禁用，而不是全局禁用

### 结论

**我不建议直接全局禁用 `long_dual_support`。**

更好的顺序是：

1. 先做 `long_dual_support` 的 symbol 级归因；
2. 只禁用其中持续亏损、样本数足够的 symbol；
3. 保留少数仍有正期望或接近盈亏平衡的 symbol；
4. 再观察总收益和 trade quality 的变化。

### 为什么不是直接全禁

因为：

- `count = 91`，样本不算小；
- 但它未必对所有 symbol 都同样差；
- 一刀切禁掉，可能会损失部分仍然有效的反弹通道；
- 局部禁用的风险更低，也更符合你当前“提纯而不是推翻”的优化路径。

### 建议动作

对 `red_bar_growing + long_dual_support`，按 symbol 统计：

- count
- win_rate
- pnl
- avg_win
- avg_loss
- MFE / MAE
- 是否集中亏在少数 symbol

然后只处理满足以下条件的 symbol：

- 样本数足够；
- 累计 pnl 明显为负；
- 且 `avg_win / abs(avg_loss)` 明显偏低。

### 预期效果

这种做法最有可能：

- 不显著影响主盈利口袋；
- 不放大 trade cadence；
- 在不改动主框架的情况下直接提纯收益。

---

## 5.2 第二优先级：重写 `long_dual_support` 的 entry 条件，而不是继续修 exit

如果 symbol-level 局部禁用之后，这个 pocket 仍然拖后腿，我建议直接重写其 entry 条件。

### 推荐的改写方向

#### 方向 A：提高准入质量

只对 `long_dual_support` 增加更严的准入，不动其他 pocket：

- 提高该 pocket 的 `entry_threshold`
- 提高该 pocket 的 `min_signal_score`
- 提高该 pocket 的 `min_vwap_score_for_entry`
- 要求 `1h` 不允许 neutral，只允许明确同向确认
- 提高该 pocket 的 flow / micro pass 数

### 可尝试的参数方向

不是最终值，只是建议试验范围：

| 项目 | 当前 | 建议试验方向 |
|---|---:|---:|
| `entry_threshold` | 0.845 | `0.855 ~ 0.865`（仅该 pocket） |
| `min_signal_score` | 0.870 | `0.885 ~ 0.895`（仅该 pocket） |
| `min_vwap_score_for_entry` | 0.12 | `0.18 ~ 0.22`（仅该 pocket） |
| `entry_hard_gate_flow_min_pass` | 2 | `3`（仅该 pocket） |
| `entry_hard_gate_micro_min_pass` | 2 | `3`（仅该 pocket） |
| `allow_neutral_1h_confirmation` | true | 对该 pocket 关闭 |

### 为什么这条线更有希望

因为 `long_dual_support` 的问题更像：

- 进入时机太松；
- 结构确认还不够；
- 反弹还没真正站稳就进场。

这种情况下，修 exit 只是“善后”，修 entry 才是“少犯错”。

#### 方向 B：切断 preflip / trial 与该 pocket 的耦合

如果 `long_dual_support` 中包含较多 preflip 性质的早期试单，我建议：

- 不让该 pocket 使用试探性 preflip entry；
- 或者给它单独更严的 preflip shrink / score / vwap 要求。

反弹类口袋如果再叠加 preflip，会很容易演化成：

- 提前摸底
- 小反弹即进
- 随后继续走弱

这是最典型的“以为抄底，其实接 falling knife”的结构。

---

## 5.3 第三优先级：只对问题 pocket 设计“分层止盈”，不要直接砍主 TP

### 核心判断

我不建议把 `long_dual_support` 或其他口袋直接改成 `2%` 固定 TP。

因为这样做的本质是：

- 用更小的盈利上限，
- 去换更高的兑现率。

而你的实验已经证明，这种交换在当前系统里不划算。

### 更合理的做法：分层止盈，而不是整体缩 TP

可以考虑只对 `long_dual_support` 这类反弹结构试：

- 第一层：`+2.0% ~ +2.4%` 先减仓 `25% ~ 35%`
- 第二层：剩余仓位继续保留原主 TP `4%`
- 不触发第二层时，剩余仓位再交给更保守的保护逻辑

### 这样做的优点

1. 先锁住一部分均值回归型利润；
2. 但不把全部仓位都砍在 2%；
3. 能最大程度避免 `avg_win` 被腰斩；
4. 对真正能继续走成趋势的单，保留上涨空间。

### 注意

这个动作只建议用于：

- 已经证明“容易回吐”的 pocket；
- 不建议扩展到主盈利 pocket。

---

## 5.4 第四优先级：把 trailing 从“早保护”改成“晚保护”

E4 的问题不是 trailing 这个工具本身错了，而是：

- 激活太早；
- 分类太粗；
- 覆盖面太大；
- 结果把好口袋也一起打坏了。

### 我建议的 trailing 原则

1. **只在已经明显盈利后才激活**
2. **只给特定 pocket 使用**
3. **优先作为 runner protection，而不是 entry repair**
4. **不要用它去修负 alpha 的入口**

### 可尝试的设计思路

#### 方案 A：runner trailing

仅对已经达到一定浮盈的仓位启动 trailing，例如：

- 浮盈达到 `+2.0%` 以后才允许 trailing；
- 且只对未被部分止盈的剩余仓位生效。

这类 trailing 的职责是：

- 保护已经跑起来的单；
- 而不是在小幅波动时把单子提早洗掉。

#### 方案 B：结构性 trailing

不要按 `trend / oscillation` 粗分类，而按：

- `signal_type`
- `vwap_state`
- 是否已发生 partial TP

来决定 trailing 是否开启。

这会比当前的 profile map 更贴近真实 alpha 分层。

---

## 5.5 第五优先级：增加“反复打脸”的 re-entry 抑制，而不是继续早止损

因为你当前 exit 改动已经引发了 trade cadence 扩张，所以我建议下一轮不要再用“更快出场”来减少亏损，而改成：

> 出场之后，减少同类错误的再次进入。

### 推荐机制

对高风险 pocket（优先 `long_dual_support`）增加：

- 同 symbol 同方向 stopout 后冷却若干 bar
- 同 symbol 在一定时间窗内连续失败达到 2 次后暂停该 pocket
- 同一结构连续两次未形成有效推进时，临时提高 entry threshold

### 优点

- 不会像早止盈那样直接压缩盈利单；
- 但能减少连续试错带来的亏损和频率膨胀；
- 对低并发系统尤其有效。

---

## 6. 如果还要继续做 exit-layer 优化，应该怎么设计

如果你仍然希望保留 exit-layer 研究，我建议必须满足以下原则。

## 6.1 原则一：exit 研究必须“冻结 cadence 影响”

当前最大的问题是：exit 一改，trade 数量也被改了。

这会导致你根本分不清：

- 是 exit 本身更优，
- 还是只是因为资金更早释放，吃到了更多后续信号。

### 建议的研究方法

#### 方法 A：固定 entry list 回放 exit

先用 baseline 生成一份固定入场清单，然后：

- 所有候选 exit 只在这份固定入场清单上做回放；
- 不允许因为提前平仓而新开额外单。

这样才能真正看清 exit 是否改善了单笔收益分布。

#### 方法 B：加入“影子占用”

即使候选 exit 提前平仓，也让资金在统计上继续占用到 baseline 的相近时长。

这不是为了模拟真实交易，而是为了做因果隔离：

- 把 exit 质量变化
- 和 cadence 变化

拆开看。

## 6.2 原则二：只允许局部 pocket 改动，不做全局改动

未来 exit-layer 的任何实验，应只对以下对象之一生效：

- 指定 `signal_type`
- 指定 `vwap_state`
- 指定 `symbol`
- 指定 `symbol × pocket`

而不应该再做“全局提早保本 / 全局收紧 TP / 全局早 trailing”。

## 6.3 原则三：新 exit 的目标不是提高胜率，而是改善 payoff shape

对你这类系统而言，exit 优化真正应该追求的是：

- 保持厚利润单不明显受损；
- 适度减少坏单尾部损失；
- 不显著增加交易频率。

所以评估新 exit 时，不要只看：

- win_rate
- avg_loss

更要看：

- `avg_win` 是否被压缩
- TP 命中笔数是否下降
- Top decile winners 是否明显变薄
- trade cadence 是否上升
- 单位持仓时长收益是否下降

---

## 7. 关于 live/backtest 一致性的建议

你文档里有一个非常关键的问题：

- 配置里一直有 trailing 参数；
- 但旧回测链路之前并未真实执行 trailing；
- 本轮才把 trailing profile 补进回测。

### 这意味着什么

这意味着历史上你对基线的理解，可能存在以下风险：

1. **如果实盘真的在执行 trailing，而回测没有执行**，那历史回测对真实收益分布的描述就是不完整的；
2. **如果实盘也没真正执行 trailing，只是配置里有参数**，那至少 live/backtest 行为此前是“表面一致、配置不一致”；
3. 无论哪种情况，今后所有关于 close-risk / trailing 的结论，都必须建立在**同一执行口径**上，否则很容易误判。

### 建议动作

在继续做盈利优化前，先做一个单独校验：

- 实盘当前 trailing 是否真的触发
- 触发顺序是否与回测一致
- intrabar 命中逻辑是否一致
- 保护触发的优先级是否一致

### 建议原则

今后任何参数，只要配置文件里存在且被认为“有意义”，都应满足：

- 实盘执行
- 回测执行
- 单测覆盖
- 事件日志可追踪

否则优化会建立在错位口径上。

---

## 8. 我建议的下一轮实验顺序

## P0：先做一致性与归因，不先改参数

### 任务 1：live/backtest trailing 对齐检查

目标：确认当前基线的真实执行口径。

### 任务 2：做 `symbol × pocket` 归因表

重点输出：

- `red_bar_growing + long_dual_support` 分 symbol 的 pnl / WR / avg_win / avg_loss
- `green_bar_growing + short_dual_pressure` 分 symbol 的表现
- 看亏损是否高度集中在少数 symbol

---

## P1：先做最小破坏的盈利优化

### 实验 1：`long_dual_support` 的 symbol-level 禁用

做法：

- 只禁用最差的一小组 symbol
- 不动全局 entry / exit
- 不动主盈利 pocket

成功标准：

- trades 不大幅变化
- `avg_win` 基本不受损
- 总 return / PF 上升

### 实验 2：`long_dual_support` 的 entry gate 提高

做法：

- 仅该 pocket 提高 score / vwap / 1h confirmation / flow / micro 要求

成功标准：

- 该 pocket count 下降
- 该 pocket pnl 改善
- 全局收益不被明显拖累

---

## P2：再做轻量 exit 优化

### 实验 3：仅该 pocket 的 partial TP

做法：

- `2.0% ~ 2.4%` 先减仓 `25% ~ 35%`
- 保留 runner 到 `4%`
- trailing 只保护剩余仓位

成功标准：

- `avg_win` 不出现大幅腰斩
- pocket pnl 改善
- trades 不显著膨胀

### 实验 4：stopout 后 re-entry cooldown

做法：

- 仅对 `long_dual_support` 或问题 symbol 启用

成功标准：

- 连续亏损簇减少
- trades 不失控
- 总收益改善

---

## P3：最后才考虑是否全禁 `long_dual_support`

只有在下面两种情况同时成立时，才建议考虑全禁：

1. symbol-level 禁用后，剩余 `long_dual_support` 仍持续负收益；
2. 提高 entry gate 后，样本质量仍不能转正。

也就是说：

- **先做局部治理**
- **再做条件提纯**
- **最后才考虑全禁**

这比直接砍掉整个 pocket 更稳健。

---

## 9. 我对你提出的几个关键问题的直接回答

## 9.1 `long_dual_support` 应不应该视为 entry alpha 问题？

**是。**

从你本轮实验结果看，它更像 entry alpha 不足，而不是 exit 参数问题。

## 9.2 我是否建议直接禁用 `long_dual_support`？

**暂时不建议直接全禁。**

我更建议：

1. 先做 symbol-level 局部禁用；
2. 再做该 pocket 的 entry 条件提纯；
3. 如果两轮后仍然持续负收益，再考虑全禁。

## 9.3 未来是否还值得继续做 exit-layer 优化？

**可以做，但必须换研究方法。**

要求：

- 固定 entry list
- 控制 cadence 干扰
- 只做 pocket 级局部改动
- 目标是改善 payoff shape，而不是单纯提高胜率

## 9.4 最有希望提升盈利的方向是什么？

按优先级排序，我认为是：

1. `long_dual_support` 的 symbol-level 治理
2. `long_dual_support` 的 entry gate 重写
3. 仅对问题 pocket 的 partial TP + runner 保留
4. stopout 后 cooldown / re-entry 抑制
5. trailing 仅作为晚期利润保护，而不是早期修 entry

---

## 10. 最终建议

如果目标是“提高盈利”，而不是“让曲线更平”，那么当前最优路线不是继续打磨全局 close-risk，而是：

### 建议路线

1. **保持当前 baseline 实盘不动**
2. **先完成 live/backtest trailing 对齐检查**
3. **马上做 `symbol × pocket` 归因，优先处理 `long_dual_support`**
4. **先做 symbol-level 局部禁用，再做 entry gate 提纯**
5. **exit 只做局部 partial TP / 晚激活 trailing，不做全局收紧**

### 一句话总结

> 这套系统要提高盈利，关键不是“更会止盈止损”，而是“少让负 alpha 的反弹单进来，同时别碰已经能跑出厚利润的主引擎”。

---

## 11. 最值得立刻执行的三件事

### 1. 产出 `red_bar_growing + long_dual_support` 的分 symbol 归因表

这是下一轮收益提升最有价值的数据基础。

### 2. 做一个只改 `long_dual_support` entry gate 的候选版本

建议先试：

- 更高 score
- 更高 vwap 要求
- 不允许 neutral 1h confirmation
- 更高 flow / micro pass

### 3. 做一个只对 `long_dual_support` 生效的 partial TP 版本

建议：

- `25% ~ 35%` at `2.0% ~ 2.4%`
- remainder 继续保留 `4%` 目标
- trailing 只保护 remainder

---

如果只允许我给一个最核心判断，那就是：

> **下一轮最可能提升收益的方向，是 entry-side alpha 提纯 + symbol-level pocket 治理，而不是继续做全局 exit-layer 收紧。**