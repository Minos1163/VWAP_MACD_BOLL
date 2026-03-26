# 策略诊断书

**日期**：2026-03-25  
**诊断对象**：fund_flow bot / macd_strategy_v2  
**数据窗口**：2026-02-23 ~ 2026-03-24（约 30 天）  
**对比回测**：

| 版本 | 收益率 | 胜率 | PF | 最大回撤 | 交易数 |
|---|---:|---:|---:|---:|---:|
| **v2（策略级，无外层链）** | **+229.06%** | **78.49%** | **4.19** | 12.01% | 265 |
| **bot-like（含外层链）** | +36.15% | 61.87% | 1.33 | 24.54% | 278 |

---

## 一、根因总结（先行结论）

> 收益差距 **6.3 倍**，不是信号质量问题，而是**两个互相叠加的结构性缺陷**共同造成的：
>
> 1. **止损/止盈结构倒置**：bot-like 用 **大止损 + 固定小止盈**，直接消灭了趋势跟踪能力
> 2. **外层开仓链未提升信号质量**，反而引入了更多低质量信号，同时压缩了仓位规模

---

## 二、量化诊断：数据对比

### 2.1 退出机制对比

| 退出方式 | bot-like 次数 | bot-like PnL | v2 次数 | v2 PnL |
|---|---:|---:|---:|---:|
| 止损（stop_loss） | **93** | **-11,322** | 216 | -4,100 |
| 固定止盈（take_profit_intrabar） | 145 | +17,112 | 0 | — |
| **4H 缩量退出（趋势完结）** | 15 | +288 | **39** | **+26,519** |
| 信号反转退出 | 0 | — | 8 | -224 |

**关键发现**：

- v2 的 `4h_shrink_exit` 39 笔贡献了 **26,519 PnL**，占总盈利 ~85%  
  → 这正是策略的核心 alpha：**跟随 4H 趋势，等到趋势减弱才退出**
- bot-like 把这个 alpha 用固定 2% 止盈全部斩断了，145 笔 TP 只拿到 17,112

### 2.2 单笔盈亏对比

| 指标 | bot-like | v2 |
|---|---:|---:|
| 平均盈利单笔 PnL | +103.16 | **+152.24** |
| 平均亏损单笔 PnL | **-126.06** | -132.52 |
| **盈亏比 R** | **0.82（<1！）** | **1.15** |
| 平均仓位规模 | 2,437 | **4,063** |
| 平均单笔 PnL | 15.76 | **90.99** |

**致命点**：bot-like 的盈亏比 R = 0.82，**小于 1**。  
即使胜率 62%，数学上也难以持续盈利：`62% × 103 + 38% × (-126) = 63.86 - 47.88 = 16`，几乎只是勉强正期望，一旦胜率波动就可能亏损。

### 2.3 止损幅度对比

| 指标 | bot-like | v2 |
|---|---|---|
| `stop_loss_pct` 配置 | **0.02（2%）** | **0.005（0.5%）** |
| `take_profit_pct` 配置 | 0.02（固定） | **0.0（无固定止盈）** |
| `breakeven_trigger_pnl_ratio` | 0.02 | 0.008 |
| 止损触发平均 pnl_pct | **-1.587%** | -0.089% |

v2 的止损是 bot-like 的 **1/4**，但亏损笔数更多（216 vs 93），因为每笔止损都很小，不伤根本。  
bot-like 止损次数少但每笔巨大，这是"大亏小赚"的负期望结构。

### 2.4 信号类型质量退化

| 信号类型 | bot-like 胜率 | bot-like PnL | v2 胜率 | v2 PnL |
|---|---:|---:|---:|---:|
| flip_bearish | 50.0% | **-739** | **100.0%** | **+3,081** |
| red_bar_shrinking | 45.5% | **-206** | 81.8% | +3,697 |
| green_bar_shrinking | 63.6% | +564 | **95.7%** | **+4,804** |
| green_bar_growing | 63.4% | +1,115 | 80.8% | +4,604 |
| red_bar_growing | 67.0% | +3,393 | 70.8% | +7,024 |

**关键发现**：  
- `flip_bearish` 在 v2 是 **100% 胜率 / +3,081 PnL** 的优质信号  
- 在 bot-like 变成了 **50% 胜率 / -739 PnL 的亏损信号**  
- `red_bar_shrinking` 和 `green_bar_shrinking` 同样出现了严重退化  
- 说明外层链并没有过滤掉坏信号，反而在某些信号类型上引入了时间滞后或条件冲突

---

## 三、根因拆解

### 根因 1：止损设置过宽（主因）

**当前 bot-like 配置**：
```
stop_loss_pct = 0.02   # 2%，× leverage(3) = 实际亏损约 6%
take_profit_pct = 0.02 # 固定 2% 止盈
breakeven_trigger_pnl_ratio = 0.02
```

**v2 配置**：
```
stop_loss_pct = 0.005  # 0.5%，× leverage(3) = 实际亏损约 1.5%
take_profit_pct = 0.0  # 无固定止盈，依赖 4H 缩量退出
breakeven_trigger_pnl_ratio = 0.008
```

**后果**：
- 固定 2% 止盈截断了所有大趋势行情（4H shrink exit 才是核心 alpha 来源）
- 2% 止损在 3× 杠杆下意味着每次止损约亏掉 6% 保证金，一旦连续止损，本金快速侵蚀
- breakeven 触发太晚（0.02），在行情短暂反弹后又折回时，保本保护未能及时启动

### 根因 2：外层开仓链叠加过多（次因）

**多层串行漏斗（每层乘法压缩通过率）**：
```
策略信号
→ signal_pool（major_symbol short_only 直接封锁做多）
→ MA10/MACD entry_hard_filter（5m 共振过滤）
→ pretrade_risk_gate（波动率/评分综合判断）
→ cooldown / 持仓约束
→ flat_top_n = 2（只送 2 个候选进终审）
→ AI final review
```

**数据佐证**：
- bot-like `flip_bearish` 有 22 笔（v2 只有 11 笔），说明**外层链没有减少 flip_bearish 信号，反而更多**
- 但 bot-like 的 flip_bearish 胜率 50% vs v2 的 100%，说明外层链**让劣质的 flip_bearish 进来了，而把优质的部分错误延迟或阻断了**
- 平均仓位 2,437 vs 4,063，外层链压缩了约 40% 的仓位规模

### 根因 3：major_symbol → trend_pool_short_only（结构性方向限制）

**配置**：
```json
"trend_pool_short_only.min_long_score = 999.0"
```

**影响标的**：`BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XAGUSDT, LTCUSDT, DOGEUSDT`

在当前回测窗口（2026-02-23 ~ 03-24），部分大币出现了阶段性反弹行情，但所有做多信号在 signal_pool 层直接被封锁。这是一个结构性的机会遗漏。

### 根因 4：weight_4h_enhancement 参数口径漂移

**问题**：
- `decision_engine.py:452` 旧装载默认回退 `0.20`
- `decision_engine.py:526` V2 装载默认回退 `0.10`
- 主配置 `scoring_weights` 未显式写出此参数

**后果**：实际运行时评分结构与人工认知不一致，导致阈值调优讨论时基于错误前提。

---

## 四、修改建议

### 优先级 P0：修正止损/止盈结构（直接收益影响最大）

**目标**：恢复 v2 的趋势跟踪 alpha

```
# 建议配置（对齐 v2 结构）
stop_loss_pct = 0.005 ~ 0.008     # 降低止损，减少每笔亏损幅度
take_profit_pct = 0.0             # 取消固定止盈，改用 4H 缩量退出
breakeven_trigger_pnl_ratio = 0.008~0.010  # 更早触发保本保护
breakeven_lock_ratio = 0.002      # 保持
```

**注意**：
- 止损缩小后，止损频率会上升（v2 有 216 次止损），需要保证单笔止损的亏损足够小
- 取消固定止盈前，必须确认 bot-like replay 正确实现了 `4h_shrink_exit` 逻辑

---

### 优先级 P1：重新评估外层开仓链各层的实际贡献

**建议逐层做消融测试（Ablation）**：

```
测试组 A：关闭 MA10/MACD entry_hard_filter，其余保留
测试组 B：关闭 pretrade_risk_gate，其余保留
测试组 C：flat_top_n = 2 → 5
测试组 D：关闭 AI final review
测试组 E：同时关闭 B + C + D（最接近 v2 无外层的状态）
```

**目标**：找出哪一层实际在提升胜率，哪一层只在砍量

---

### 优先级 P1：修正 flip_bearish / shrinking 信号退化

**问题分析**：外层链在处理 flip 类信号时存在时间滞后或条件冲突

```python
# 伪代码：建议在 signal_pool 过滤层对 flip 类信号特殊处理

def filter_signal_pool(signal, pool_config):
    # flip_bearish / flip_bullish 是高质量的方向性信号
    # 不应被 MA10/MACD short-term 共振过滤拦截
    # 因为 flip 本身就代表方向已经改变
    
    if signal.signal_type in ['flip_bearish', 'flip_bullish']:
        # 跳过 MA10/MACD entry_hard_filter
        # flip 信号自身已经包含了方向确认
        bypass_ma10_macd_filter = True
    else:
        bypass_ma10_macd_filter = False
    
    return apply_filters(signal, bypass_ma10_macd_filter)
```

---

### 优先级 P2：取消或放宽 major_symbol short_only 限制

**建议**：将 `trend_pool_short_only` 改为 `trend_pool_major`（双向但评分要求更高）

```json
// 当前（建议修改）
"trend_pool_short_only": {
    "min_long_score": 999.0,   // 实际封锁做多
    "min_short_score": 0.06
}

// 建议改为
"trend_pool_major": {
    "min_long_score": 0.10,    // 允许做多但门槛更高
    "min_short_score": 0.10
}
```

**前提条件**：需要在回测中验证放开大币做多方向后，整体胜率是否维持在 70% 以上。

---

### 优先级 P2：统一 weight_4h_enhancement 口径

```python
# 伪代码：在 decision_engine.py 两处装载逻辑中统一默认值

# decision_engine.py:452（旧 MTF）
weight_4h_enhancement = config.get('scoring_weights', {}).get(
    'weight_4h_enhancement', 0.10  # 统一改为 0.10，与 V2 对齐
)

# decision_engine.py:526（V2）
weight_4h_enhancement = config.get('scoring_weights', {}).get(
    'weight_4h_enhancement', 0.10  # 保持 0.10
)
```

同时在主配置 `trading_config_fund_flow.json` 中显式写出：
```json
"scoring_weights": {
    "weight_4h_direction": 0.55,
    "weight_vwap": 0.20,
    "weight_15m_entry": 0.05,
    "weight_volume": 0.20,
    "weight_4h_enhancement": 0.10   // 新增，明确写出
}
```

---

### 优先级 P3：补齐 bot-like replay 对 live trailing 的模拟

**当前缺失**：`_tighten_protection_for_conflict()` 的趋势保护收紧逻辑

```python
# 伪代码：在 backtest_fund_flow_bot_like.py 的 _check_stops_bot_like() 中补充

def _check_stops_bot_like(position, current_bar):
    # 现有逻辑
    check_fixed_stop_loss(position, current_bar)
    check_breakeven_lock(position, current_bar)
    check_take_profit_levels(position, current_bar)
    
    # 补充：模拟 live 的趋势保护收紧
    if should_tighten_protection(position, current_bar):
        tighten_trailing_stop(position, current_bar)
        # 当出现反向信号或趋势减弱时，动态收紧止损到更近位置
```

---

## 五、修改路径建议（执行顺序）

```
Step 1（立即可做）
  ├── 修改 stop_loss_pct: 0.02 → 0.005~0.008
  ├── 修改 take_profit_pct: 0.02 → 0.0
  ├── 修改 breakeven_trigger_pnl_ratio: 0.02 → 0.008
  └── 验证 bot-like replay 中 4h_shrink_exit 是否正确触发
      → 预期：PF 从 1.33 提升至 2.5+

Step 2（验证 Step 1 后再做）
  ├── 逐层消融测试外层开仓链
  ├── 对 flip 类信号豁免 MA10/MACD entry_hard_filter
  └── flat_top_n 从 2 提升至 3~4
      → 预期：胜率从 62% 提升至 70%+

Step 3（基于 Step 2 数据再做）
  ├── 放开 major_symbol 做多方向（改用双向 major pool）
  └── 统一 weight_4h_enhancement = 0.10 到主配置
      → 预期：减少方向性机会遗漏

Step 4（最后做）
  └── 补齐 bot-like replay 的 live trailing 模拟
      → 前提：Step 1~3 已经稳定，避免在未完整模拟的回测环境里过度优化
```

---

## 六、预期改善区间

| 指标 | 当前 bot-like | 目标（Step 1 后） | 目标（Step 1~3 后） |
|---|---:|---:|---:|
| 收益率 | +36% | +80%~120% | +150%~200% |
| 胜率 | 61.87% | 65%~70% | 72%~78% |
| PF | 1.33 | 2.0~2.5 | 3.0+ |
| 最大回撤 | 24.54% | 15%~18% | 10%~14% |
| 盈亏比 R | 0.82 | 1.0~1.2 | 1.1~1.3 |

---

## 七、不建议做的事

1. **不要在 bot-like replay 未修正止损/止盈结构的情况下调信号阈值**  
   → 当前最大问题不是信号质量，而是出场机制。先修出场，再谈入场阈值。

2. **不要把 v2 的 229% 直接视为实盘可复制的目标**  
   → v2 不含外层链、不含 AI 终审、不含账户状态约束，口径本质不同。

3. **不要直接在实盘开启 take_profit_pct = 0.0**  
   → 必须先在 bot-like replay 中验证 4h_shrink_exit 逻辑完整且正确触发，否则持仓可能无限持有。

4. **不要同时修改多个参数**  
   → 每次只改一个维度，否则无法定位真正起作用的变量。

---

## 八、附：关键数据速查

### bot-like 各信号类型表现
| 信号类型 | 笔数 | 胜率 | PnL |
|---|---:|---:|---:|
| red_bar_growing | 115 | 67.0% | +3,392 |
| green_bar_growing | 82 | 63.4% | +1,114 |
| green_bar_shrinking | 22 | 63.6% | +564 |
| flip_bullish | 15 | 53.3% | +254 |
| red_bar_shrinking | 22 | 45.5% | **-206** |
| flip_bearish | 22 | 50.0% | **-739** |

### v2 各信号类型表现
| 信号类型 | 笔数 | 胜率 | PnL |
|---|---:|---:|---:|
| **flip_bearish** | 11 | **100%** | **+3,081** |
| **green_bar_shrinking** | 23 | **95.7%** | **+4,804** |
| red_bar_shrinking | 22 | 81.8% | +3,697 |
| flip_bullish | 11 | 81.8% | +899 |
| green_bar_growing | 78 | 80.8% | +4,604 |
| red_bar_growing | 120 | 70.8% | +7,024 |

---

*诊断书生成时间：2026-03-25*  
*数据来源：bot_like_summary_20260325_171201.json / v2_summary_20260325_131704.json / 对应 trades CSV*
