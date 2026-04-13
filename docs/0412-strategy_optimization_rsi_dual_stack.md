# 策略优化全文档：双指标集 RSI 融合方案
# MACD + RSI + VWAP / MACD + BOLL + VWAP 自适应切换

**版本**: v2.0-alpha  
**基准**: DeepSeek Review Packet + Shrink Entry Path Audit  
**目标窗口**: 30D 回测 2026-03-02 → 2026-04-01  
**当前基线**: Return `-11.00%` | Win Rate `63.76%` | Trades `516`  
**优化目标**: Return `50%+` | Win Rate `80%+` | Trades `30-120`

---

## 目录

1. [问题根因回顾](#1-问题根因回顾)
2. [优化总体思路](#2-优化总体思路)
3. [RSI 引入的理论依据](#3-rsi-引入的理论依据)
4. [双指标集设计](#4-双指标集设计)
   - 4.1 [MACD + RSI + VWAP 套件（动量确认型）](#41-macd--rsi--vwap-套件动量确认型)
   - 4.2 [MACD + BOLL + VWAP 套件（结构约束型）](#42-macd--boll--vwap-套件结构约束型)
5. [自适应切换逻辑](#5-自适应切换逻辑)
6. [信号评分架构重设计](#6-信号评分架构重设计)
7. [入场过滤链重设计](#7-入场过滤链重设计)
8. [风险控制更新](#8-风险控制更新)
9. [配置文件 Diff](#9-配置文件-diff)
10. [核心模块伪代码](#10-核心模块伪代码)
    - 10.1 [RSI 计算模块](#101-rsi-计算模块)
    - 10.2 [市场状态分类器](#102-市场状态分类器)
    - 10.3 [MACD+RSI+VWAP 分析器](#103-macdrsivwap-分析器)
    - 10.4 [MACD+BOLL+VWAP 分析器](#104-macdbollvwap-分析器)
    - 10.5 [自适应套件选择器](#105-自适应套件选择器)
    - 10.6 [综合评分聚合器](#106-综合评分聚合器)
    - 10.7 [Pretrade RSI 门控](#107-pretrade-rsi-门控)
11. [代码修改 Diff（src 层）](#11-代码修改-diffsrc-层)
    - 11.1 [macd_strategy_v2.py](#111-macd_strategy_v2py)
    - 11.2 [decision_engine.py](#112-decision_enginepy)
    - 11.3 [fund_flow_bot.py](#113-fund_flow_botpy)
    - 11.4 [新增 rsi_indicator.py](#114-新增-rsi_indicatorpy)
    - 11.5 [新增 market_regime_classifier.py](#115-新增-market_regime_classifierpy)
12. [信号族白名单重构](#12-信号族白名单重构)
13. [Pocket Override 更新](#13-pocket-override-更新)
14. [回测预期估算](#14-回测预期估算)
15. [分阶段上线计划](#15-分阶段上线计划)
16. [监控与告警指标](#16-监控与告警指标)
17. [已知风险与缓解](#17-已知风险与缓解)
18. [附录 A：RSI 参数敏感性分析](#附录-a-rsi-参数敏感性分析)
19. [附录 B：双套件绩效对比表](#附录-b-双套件绩效对比表)
20. [附录 C：完整伪代码参考实现](#附录-c-完整伪代码参考实现)

---

## 1. 问题根因回顾

### 1.1 三大核心失败点

```
失败维度          当前值       目标值        Gap
──────────────────────────────────────────────
30D Return       -11.00%      +50%+        -61pp
Win Rate          63.76%       80%+        -16pp
Trade Count         516       30-120      +396 超量
```

### 1.2 损失归因树

```
总损失 (PnL ≈ -543)
├── shrink 族入场 (已在当前 config 修复)
│   ├── red_bar_shrinking  : -1054.42
│   └── green_bar_shrinking:  -532.77
├── 胜负不对称 (未修复)
│   ├── avg_win  = +11.14
│   └── avg_loss = -22.50   ← 损失 2x 胜利
├── 方向性偏差 (未修复)
│   ├── Long  53.16% WR → 负
│   └── Short 68.44% WR → 仍负 (shrink 拖累)
└── 超量交易 (部分修复，77 trades 当前太少)
    └── 目标 30-120 trades，当前摆幅过大
```

### 1.3 当前 config 修复后的新问题

修复 shrink 族之后，30D replay 降至 **77 笔交易**，仅 `flip_bearish` 信号族有效。

这说明：
- 当前 config 是一个**过度收紧**的临时夹具，而不是可持续架构
- 机会集从 516 笔压缩到 77 笔，牺牲了大量正 alpha pocket
- `green_bar_growing` + `flip_bullish` 两个正 alpha 族被间接屏蔽
- 在不引入新的确认维度的情况下，无法同时达成 "高质量" + "足量交易数"

### 1.4 RSI 引入的必要性

```
当前指标覆盖面:
  MACD histogram  → 动量强度 + 方向
  VWAP            → 价格与成交量加权均价关系
  BOLL            → 价格结构波动带
  ADX             → 趋势强度 (gate 层)

缺失维度:
  × 超买/超卖识别  ← RSI 填补
  × 动量背离识别  ← RSI 填补
  × 信号族切换时机 ← RSI 状态机填补
  × BOLL 与 MACD 协调使用条件 ← 套件切换逻辑填补
```

---

## 2. 优化总体思路

### 2.1 核心策略改变

| 维度 | 旧架构 | 新架构 |
|------|--------|--------|
| 指标集 | 单套 MACD+BOLL+VWAP | 双套自适应（RSI 套件 / BOLL 套件）|
| 信号族 | 全族（含 shrink）→ 仅 flip_bearish | 白名单制（4 族）+ RSI 二次过滤 |
| 入场条件 | 单维度阈值 | 多维度联合门控 |
| 胜负比 | avg_win/avg_loss ≈ 0.50 | 目标 ≥ 1.5 |
| 交易频率 | 超量 / 过少 | 目标 30-120 / 30D |

### 2.2 双套件分工

```
市场状态
    │
    ├─ 趋势型市场 (ADX > 25, 波动有序)
    │   └─ 使用 MACD + RSI + VWAP 套件
    │       ├─ RSI 确认动量延续
    │       ├─ MACD 提供方向
    │       └─ VWAP 确认价格位置
    │
    └─ 震荡型市场 (ADX ≤ 25, 价格在 BOLL 内振荡)
        └─ 使用 MACD + BOLL + VWAP 套件
            ├─ BOLL 提供边界约束
            ├─ MACD 判断局部动量
            └─ VWAP 确认支撑/阻力有效性
```

### 2.3 优化优先级排序

```
Priority 1 (必须): 修复胜负比不对称
  → 引入 RSI 超买/超卖过滤，禁止逆动量入场
  → 重设 TP/SL 参数，目标 avg_win/avg_loss ≥ 1.5

Priority 2 (必须): 恢复正 alpha 信号族
  → green_bar_growing + flip_bullish 在 RSI 验证下重新开放
  → 不重开 shrink 族

Priority 3 (重要): 自适应套件切换
  → 趋势市 → RSI 套件
  → 震荡市 → BOLL 套件

Priority 4 (重要): 交易数量回到 30-120
  → 通过开放正 alpha 族而非降低质量门槛来增加交易数

Priority 5 (优化): Long 端质量修复
  → Long 53.16% WR 是系统最弱点，RSI 多头确认是核心解法
```

---

## 3. RSI 引入的理论依据

### 3.1 RSI 与 MACD 的互补关系

```
MACD histogram 正向增长时:
  ├─ RSI 50-70 → 动量健康，可做多
  ├─ RSI > 70  → 超买，避免追涨，等回踩
  └─ RSI < 50  → MACD 弱信号，可能假突破

MACD histogram 负向增长时:
  ├─ RSI 30-50 → 动量健康，可做空
  ├─ RSI < 30  → 超卖，避免追跌，等反弹衰竭
  └─ RSI > 50  → MACD 弱信号，可能假跌破

结论:
  MACD 给方向和强度，RSI 给超买/超卖状态
  两者结合可以显著减少"假信号"入场
```

### 3.2 RSI 背离信号的应用

```
看多背离 (Bullish Divergence):
  价格创新低 + RSI 未创新低 → 潜在反转，配合 flip_bullish 使用

看空背离 (Bearish Divergence):
  价格创新高 + RSI 未创新高 → 潜在反转，配合 flip_bearish 使用

隐藏多头背离 (Hidden Bullish):
  价格高点比高点高 + RSI 高点比高点低 → 趋势延续做多信号

隐藏空头背离 (Hidden Bearish):
  价格低点比低点低 + RSI 低点比低点高 → 趋势延续做空信号
```

### 3.3 RSI 时间框架选择

```
系统当前时间框架:
  4h  → 主方向判断
  1h  → 方向确认
  15m → 入场时机

RSI 时间框架分配:
  RSI_4h  → 趋势健康度判断（宏观状态）
  RSI_1h  → 入场动量确认（中观验证）
  RSI_15m → 精准入场时机（微观执行）

推荐 RSI 周期:
  标准: period=14
  短期: period=7  (15m 级别)
  中期: period=21 (4h 级别补充)
```

### 3.4 RSI 与不同信号族的配合策略

```
信号族              RSI 应用策略
─────────────────────────────────────────────────────────
flip_bullish       RSI_1h 从 < 50 穿越 50 → 确认翻多
flip_bearish       RSI_1h 从 > 50 穿越 50 → 确认翻空
green_bar_growing  RSI_1h 40-65 区间且上行 → 趋势延续
red_bar_growing    RSI_1h 35-60 区间且下行 → 趋势延续
green_bar_shrinking [已禁用] RSI 在 50+ 但背离 → 不可信
red_bar_shrinking  [已禁用] RSI 在 50- 但背离 → 不可信
```

---

## 4. 双指标集设计

### 4.1 MACD + RSI + VWAP 套件（动量确认型）

#### 适用场景

```
激活条件（ALL of）:
  ✓ ADX_1h > 25               → 趋势足够强
  ✓ ATR_pct 在 [0.006, 0.020] → 波动合理
  ✓ MACD_4h histogram 单边展开 → 方向明确
  ✓ 价格与 VWAP 偏离 < 3%    → 价格贴近基准

禁用条件（ANY of）:
  × ADX_1h < 20               → 趋势过弱
  × ATR_pct > 0.025           → 极度波动
  × BOLL 带宽收缩 < 均值 0.5x  → 挤压前夕，不适合趋势追踪
```

#### 核心评分公式

```
RSI_MRV_Score = (
    w_macd_4h   * macd_4h_direction_score     +  # 0.35
    w_macd_1h   * macd_1h_confirmation_score  +  # 0.20
    w_rsi_1h    * rsi_1h_momentum_score       +  # 0.25
    w_vwap      * vwap_position_score         +  # 0.15
    w_rsi_div   * rsi_divergence_bonus        +  # 0.05
)

其中:
  macd_4h_direction_score:
    histogram > 0 且增长 → 1.0
    histogram > 0 且收缩 → 0.5
    histogram < 0 且增长 → 0.0
    histogram < 0 且收缩 → -1.0 (做空 = 1.0)

  rsi_1h_momentum_score (做多方向):
    RSI 50-65      → 1.0  (动量健康区)
    RSI 40-50      → 0.7  (刚突破，可接受)
    RSI 65-70      → 0.5  (偏热，谨慎)
    RSI > 70       → 0.0  (超买，不入场)
    RSI < 40       → 0.0  (动量不支持)

  rsi_1h_momentum_score (做空方向):
    RSI 35-50      → 1.0  (动量健康区)
    RSI 50-60      → 0.7  (刚跌破，可接受)
    RSI 30-35      → 0.5  (偏冷，谨慎)
    RSI < 30       → 0.0  (超卖，不入场)
    RSI > 60       → 0.0  (动量不支持)

  vwap_position_score (做多):
    价格 > VWAP 且偏离 < 1%   → 1.0
    价格 > VWAP 且偏离 1-2%   → 0.7
    价格 > VWAP 且偏离 2-3%   → 0.3
    价格 ≤ VWAP               → 0.0

  rsi_divergence_bonus:
    多头背离存在             → +0.15
    隐藏多头背离存在         → +0.10
    无背离                   → 0.0
    看空背离（逆势）         → -0.20
```

#### 入场门控

```
RSI_MRV 套件入场要求（做多，ALL of）:
  RSI_4h > 45          → 宏观动量不弱
  RSI_1h 在 45-70      → 中观动量健康
  RSI_15m > 50         → 微观动量向上
  RSI_MRV_Score > 0.70 → 综合评分达标
  VWAP_score > 0.12    → VWAP 位置支撑
  价格 > VWAP_1h       → 价格在 VWAP 上方

RSI_MRV 套件入场要求（做空，ALL of）:
  RSI_4h < 55          → 宏观动量不强
  RSI_1h 在 30-55      → 中观动量健康
  RSI_15m < 50         → 微观动量向下
  RSI_MRV_Score > 0.70 → 综合评分达标
  VWAP_score > 0.12    → VWAP 位置施压
  价格 < VWAP_1h       → 价格在 VWAP 下方
```

### 4.2 MACD + BOLL + VWAP 套件（结构约束型）

#### 适用场景

```
激活条件（ALL of）:
  ✓ ADX_1h ≤ 25 OR BOLL 带宽处于收缩/中性状态
  ✓ 价格在 BOLL 带内（非极端位置）
  ✓ MACD histogram 有明确局部方向

禁用条件（ANY of）:
  × ADX_1h > 30 且 BOLL 带宽快速扩展  → 已是趋势市，用 RSI 套件
  × 价格突破 BOLL 上下轨且未回踩      → 等待确认
```

#### BOLL 位置评分

```
boll_position_score (做多):
  价格 < BOLL_lower (下轨外)      → -1.0  (不入场)
  价格在 lower ~ lower+5%         → 1.0   (支撑弹性区)
  价格在 lower+5% ~ mid-5%        → 0.8   (下半区健康)
  价格在 mid-5% ~ mid+5%          → 0.5   (中性区)
  价格在 mid+5% ~ upper-5%        → 0.2   (上半区，做多需谨慎)
  价格 > BOLL_upper - 5%          → 0.0   (接近上轨，做多不入)
  价格 > BOLL_upper               → -1.0  (不做多)

boll_position_score (做空):
  价格 > BOLL_upper (上轨外)      → -1.0  (不入场)
  价格在 upper-5% ~ upper         → 1.0   (阻力回落区)
  价格在 mid+5% ~ upper-5%        → 0.8   (上半区健康)
  价格在 mid-5% ~ mid+5%          → 0.5   (中性区)
  价格在 lower+5% ~ mid-5%        → 0.2   (下半区，做空需谨慎)
  价格 < BOLL_lower + 5%          → 0.0   (接近下轨，做空不入)
  价格 < BOLL_lower               → -1.0  (不做空)
```

#### BOLL 带宽状态分类

```
BOLL_bandwidth = (upper - lower) / mid

bandwidth_state:
  < 0.02  → "squeeze"     极度收缩，准备爆发，不入场
  0.02-0.04 → "narrow"    收缩中，可小仓观察
  0.04-0.08 → "normal"    正常区间，可正常入场
  0.08-0.12 → "wide"      扩张中，趋势初段，评估 ADX 切换套件
  > 0.12   → "expanding"  强趋势扩张，切换 RSI 套件
```

#### 核心评分公式

```
BOLL_MBV_Score = (
    w_macd_4h    * macd_4h_direction_score    +  # 0.30
    w_macd_1h    * macd_1h_confirmation_score +  # 0.20
    w_boll_pos   * boll_position_score        +  # 0.30
    w_vwap       * vwap_position_score        +  # 0.15
    w_bandwidth  * bandwidth_quality_score    +  # 0.05
)

bandwidth_quality_score:
  "normal"   → 1.0
  "narrow"   → 0.6
  "wide"     → 0.4
  "squeeze"  → 0.0
  "expanding"→ 0.2 (应切换套件)
```

#### 入场门控

```
BOLL_MBV 套件入场要求（做多，ALL of）:
  boll_position_score > 0.5    → 价格在有利位置
  bandwidth_state in ["normal", "narrow"]  → 带宽适合
  MACD_1h histogram > 0        → 局部动量向上
  VWAP_score > 0.10            → VWAP 不阻挡
  BOLL_MBV_Score > 0.65        → 综合评分达标

BOLL_MBV 套件入场要求（做空，ALL of）:
  boll_position_score > 0.5    → 价格在有利位置
  bandwidth_state in ["normal", "narrow"]  → 带宽适合
  MACD_1h histogram < 0        → 局部动量向下
  VWAP_score > 0.10            → VWAP 不支撑
  BOLL_MBV_Score > 0.65        → 综合评分达标
```

---

## 5. 自适应切换逻辑

### 5.1 市场状态分类器

```
市场状态 = classify_market_regime(symbol, tf="1h", tf2="4h")

输入特征:
  ADX_1h           → 趋势强度
  ATR_pct_1h       → 波动强度
  BOLL_bandwidth_4h → BOLL 带宽
  MACD_slope_4h    → MACD 斜率（方向一致性）
  Volume_ratio     → 成交量相对均值比

状态枚举:
  TRENDING_BULL    → 上升趋势
  TRENDING_BEAR    → 下降趋势
  RANGING          → 震荡横盘
  BREAKOUT_WATCH   → 突破准备（BOLL 收缩后）
  VOLATILE         → 极度波动（冷却期）
```

### 5.2 套件选择决策矩阵

```
市场状态          首选套件              备选套件        禁用
─────────────────────────────────────────────────────────────
TRENDING_BULL    RSI_MRV               BOLL_MBV(退出)   -
TRENDING_BEAR    RSI_MRV               BOLL_MBV(退出)   -
RANGING          BOLL_MBV              RSI_MRV(突破)    -
BREAKOUT_WATCH   BOLL_MBV (等待)       RSI_MRV(确认后)  shrink
VOLATILE         两套均禁止入场         -               全部
```

### 5.3 套件切换规则

```
切换触发器:

RSI_MRV → BOLL_MBV:
  条件: ADX_1h 从 >25 降至 <22 持续 3 根 1h K 线
  OR:   BOLL_bandwidth 从扩张转为收缩，持续 2 根 4h K 线
  切换动作: 不强制平仓，仅影响新开仓决策

BOLL_MBV → RSI_MRV:
  条件: ADX_1h 从 <25 升至 >28 持续 2 根 1h K 线
  AND:  BOLL_bandwidth 超过均值 1.3x
  切换动作: 不强制平仓，仅影响新开仓决策

切换冷却: 最短 4 根 1h K 线（防止频繁摇摆）
```

### 5.4 状态机图

```
                     ┌─────────────────────────────┐
                     │      VOLATILE STATE          │
                     │  两套套件全部禁止新开仓        │
                     └──────────┬──────────────────┘
                                │ ATR_pct < 0.018
                                ↓
         ┌──────────────────────────────────────────┐
         │           REGIME CLASSIFIER              │
         │                                          │
         │  ADX > 25 + bandwidth expanding          │
         │         ↓                                │
         │   TRENDING → RSI_MRV Suite               │
         │                                          │
         │  ADX < 25 + bandwidth normal/narrow      │
         │         ↓                                │
         │   RANGING → BOLL_MBV Suite               │
         │                                          │
         │  BOLL squeeze (<0.02)                    │
         │         ↓                                │
         │   BREAKOUT_WATCH → BOLL_MBV (小仓观察)   │
         └──────────────────────────────────────────┘
```

---

## 6. 信号评分架构重设计

### 6.1 旧评分权重

```yaml
# 旧配置
weight_1h_direction: 0.25
weight_4h_direction: 0.35
weight_boll_position: 0.25
weight_vwap: 0.05
weight_15m_entry: 0.00
weight_volume: 0.10
```

### 6.2 新评分权重（RSI_MRV 套件）

```yaml
# RSI_MRV 套件权重
weight_4h_direction: 0.30    # 略降，与 RSI 分担
weight_1h_direction: 0.15    # 降，由 RSI_1h 承接
weight_rsi_4h: 0.10          # 新增：宏观 RSI 状态
weight_rsi_1h: 0.20          # 新增：核心动量确认
weight_rsi_divergence: 0.05  # 新增：背离加分
weight_vwap: 0.15            # 提升：从 0.05 到 0.15
weight_15m_entry: 0.00       # 保持：权重为 0，仍作 gate
weight_volume: 0.05          # 降低：次要
```

### 6.3 新评分权重（BOLL_MBV 套件）

```yaml
# BOLL_MBV 套件权重
weight_4h_direction: 0.30    # 保持
weight_1h_direction: 0.20    # 保持
weight_boll_position: 0.25   # 保持（BOLL 核心）
weight_boll_bandwidth: 0.05  # 新增：带宽质量
weight_vwap: 0.15            # 提升：从 0.05 到 0.15
weight_15m_entry: 0.00       # 保持
weight_volume: 0.05          # 降低
```

### 6.4 RSI 分数计算细则

```python
def compute_rsi_score(rsi_value, direction, timeframe):
    """
    direction: "long" or "short"
    timeframe: "4h", "1h", "15m"
    返回: 0.0 ~ 1.0
    """
    if direction == "long":
        # 做多 RSI 评分映射
        if rsi_value >= 70:
            return 0.0   # 超买，禁止做多
        elif rsi_value >= 65:
            return 0.3   # 偏热，大幅折扣
        elif rsi_value >= 55:
            return 0.9   # 最优区间
        elif rsi_value >= 45:
            return 0.8   # 良好区间
        elif rsi_value >= 40:
            return 0.5   # 勉强可接受
        else:
            return 0.0   # 动量不足，禁止做多

    elif direction == "short":
        # 做空 RSI 评分映射
        if rsi_value <= 30:
            return 0.0   # 超卖，禁止做空
        elif rsi_value <= 35:
            return 0.3   # 偏冷，大幅折扣
        elif rsi_value <= 45:
            return 0.9   # 最优区间
        elif rsi_value <= 55:
            return 0.8   # 良好区间
        elif rsi_value <= 60:
            return 0.5   # 勉强可接受
        else:
            return 0.0   # 动量过强，禁止做空
```

### 6.5 RSI 联合门控逻辑

```
RSI 联合门控（三时间框架一致性检查）:

做多方向：
  RSI_4h > 45  AND RSI_1h > 45  AND RSI_15m > 48
  → 全通过：允许入场
  → 任一未通过：RSI gate = FAIL，终止该信号

做空方向：
  RSI_4h < 55  AND RSI_1h < 55  AND RSI_15m < 52
  → 全通过：允许入场
  → 任一未通过：RSI gate = FAIL，终止该信号

特殊豁免（仅 flip 族）：
  flip_bullish:
    允许 RSI_1h 在 40-45 区间（刚翻多，略低可接受）
    BUT RSI_4h 必须 > 42
  flip_bearish:
    允许 RSI_1h 在 55-60 区间（刚翻空，略高可接受）
    BUT RSI_4h 必须 < 58
```

---

## 7. 入场过滤链重设计

### 7.1 新过滤链总览

```
Signal Generation
    │
    ▼
[Gate 0] Market State Check
    ├─ VOLATILE → BLOCK ALL
    ├─ BREAKOUT_WATCH → 仅 BOLL_MBV 小仓
    └─ TRENDING/RANGING → 继续

    ▼
[Gate 1] Signal Family Whitelist
    ├─ 允许: flip_bullish, flip_bearish, green_bar_growing, red_bar_growing
    ├─ 禁止: red_bar_shrinking (disable flag)
    ├─ 禁止: green_bar_shrinking (disable flag)
    └─ 其他: 按信号评分阈值

    ▼
[Gate 2] Suite Selection
    ├─ TRENDING → RSI_MRV Suite
    └─ RANGING  → BOLL_MBV Suite

    ▼
[Gate 3] RSI Triple-Timeframe Gate
    ├─ PASS: RSI_4h + RSI_1h + RSI_15m 一致
    └─ FAIL: BLOCK

    ▼
[Gate 4] VWAP Position Check
    ├─ PASS: vwap_score > 0.12
    └─ FAIL: BLOCK (或仅惩罚分数)

    ▼
[Gate 5] Suite-Specific Score Gate
    ├─ RSI_MRV_Score > 0.70
    ├─ BOLL_MBV_Score > 0.65
    └─ FAIL: BLOCK

    ▼
[Gate 6] 4h Preflip / Home Advantage
    └─ 保持现有逻辑

    ▼
[Gate 7] Pocket Override
    └─ 保持并更新

    ▼
[Gate 8] L1/L2/L3 Structural Gates
    └─ 保持现有逻辑

    ▼
[Gate 9] AI Review
    └─ 保持现有逻辑

    ▼
[Gate 10] Pretrade Risk Gate + RSI Overbought/Oversold Veto
    ├─ RSI_1h > 75 → 做多 VETO
    ├─ RSI_1h < 25 → 做空 VETO
    └─ 通过继续

    ▼
Execution
```

### 7.2 各信号族的新门控参数

#### flip_bullish（RSI_MRV 套件优先）

```yaml
flip_bullish:
  # 原有参数保留
  min_vwap_score: 0.10
  require_pullback_bounce: true

  # 新增 RSI 参数
  rsi_1h_min: 42           # 允许翻多初期略低
  rsi_1h_max: 68           # 超买不入
  rsi_4h_min: 42           # 宏观不能太弱
  rsi_15m_min: 48          # 微观需向上
  rsi_cross_above_50: true  # RSI_1h 需从 <50 穿越 50（或刚穿越内 3 根 K 线）

  # 套件评分要求
  suite_score_min: 0.70
  preferred_suite: "RSI_MRV"
  fallback_suite: "BOLL_MBV"   # BOLL 收缩行情可用

  # 信号阈值（保留旧值）
  min_signal_score: 0.80
```

#### flip_bearish（RSI_MRV 套件优先）

```yaml
flip_bearish:
  min_vwap_score: 0.10

  # 新增 RSI 参数
  rsi_1h_max: 58           # 允许翻空初期略高
  rsi_1h_min: 32           # 超卖不入
  rsi_4h_max: 58
  rsi_15m_max: 52
  rsi_cross_below_50: true  # RSI_1h 需从 >50 穿越 50（或刚穿越内 3 根）

  suite_score_min: 0.70
  preferred_suite: "RSI_MRV"

  min_signal_score: 0.80
```

#### green_bar_growing（RSI_MRV 套件）

```yaml
green_bar_growing:
  min_vwap_score: 0.12
  require_strict_1h_confirmation: true

  # 新增 RSI 参数（趋势延续型，RSI 不能太高）
  rsi_1h_min: 48
  rsi_1h_max: 65           # 不追过热趋势
  rsi_4h_min: 45
  rsi_4h_max: 72           # 4h 允许略高（趋势中）
  rsi_slope_1h_positive: true  # RSI_1h 斜率必须正向

  suite_score_min: 0.72
  preferred_suite: "RSI_MRV"

  min_signal_score: 1.20
  require_cvd_ok: true
```

#### red_bar_growing（RSI_MRV 套件）

```yaml
red_bar_growing:
  min_vwap_score: 0.12
  require_strict_1h_confirmation: true

  # 新增 RSI 参数
  rsi_1h_max: 52
  rsi_1h_min: 35
  rsi_4h_max: 55
  rsi_4h_min: 28
  rsi_slope_1h_negative: true  # RSI_1h 斜率必须负向

  suite_score_min: 0.72
  preferred_suite: "RSI_MRV"

  min_signal_score: 1.20
  require_cvd_ok: true
```

### 7.3 BOLL_MBV 套件下的震荡市入场规则

```
RANGING 状态下（BOLL_MBV 套件）:

做多条件（ALL of）:
  价格从 BOLL_lower 反弹且站上 VWAP
  MACD_1h histogram 从负转正（金叉区域）
  RSI_1h 从 < 40 反弹至 > 45（超卖回升）
  BOLL bandwidth_state 在 "normal" 或 "narrow"
  BOLL_MBV_Score > 0.65

做空条件（ALL of）:
  价格从 BOLL_upper 回落且跌破 VWAP
  MACD_1h histogram 从正转负（死叉区域）
  RSI_1h 从 > 60 回落至 < 55（超买回落）
  BOLL bandwidth_state 在 "normal" 或 "narrow"
  BOLL_MBV_Score > 0.65

注意: RANGING 状态下 RSI 作为超买/超卖确认而非趋势确认
  → 做多时希望 RSI 从超卖区反弹（而非在 50+ 延续）
  → 做空时希望 RSI 从超买区回落（而非在 50- 延续）
```

---

## 8. 风险控制更新

### 8.1 TP/SL 重设计（解决胜负比不对称）

当前问题：avg_win `+11.14` vs avg_loss `-22.50`，比值 0.495

目标：avg_win / avg_loss ≥ 1.5

```
新 TP/SL 策略:

RSI_MRV 套件（趋势跟踪型）:
  止损: 1.5% (从 2% 收紧)
  初始 TP1: 1.0% → 平仓 20%
  初始 TP2: 1.8% → 平仓 25%
  初始 TP3: 3.0% → 平仓 20%
  跟踪止损激活: 1.0% (从 1.2% 降低)
  跟踪止损距离: 0.7% (从 0.7% 保持，但 ATR 乘数从 1.0 降到 0.8)

  目标 win/loss 比: (0.7 * 1.0% + 0.2 * 1.8% + 0.1 * 3.0%) / 1.5%
                  = (0.70 + 0.36 + 0.30) / 1.5%
                  ≈ 1.36 / 1.5% → 约 0.91
  加入跟踪止损延伸后，预计达到 1.2-1.5x

BOLL_MBV 套件（震荡反转型）:
  止损: 1.2% (更紧，BOLL 提供清晰支撑/阻力)
  TP1: 0.8% → 平仓 30% (快速锁定，震荡市不贪)
  TP2: 1.4% → 平仓 30%
  TP3: BOLL 对侧轨道 → 平仓 25%
  跟踪止损激活: 0.8%
  时间止损: 45 分钟 (震荡市不拖单)

  目标 win/loss 比: 约 1.3x
```

### 8.2 RSI 动态止损调整

```
RSI 动态 SL 调整规则:

做多仓位 SL 调整:
  RSI_1h 进入 65-70 时 → 将 SL 提升到 breakeven
  RSI_1h 进入 70+ 时   → 触发 "超买警告"，锁定 50% 利润
  RSI_1h 从 65 回落至 55 时 → 正常跟踪止损继续

做空仓位 SL 调整:
  RSI_1h 进入 30-35 时 → 将 SL 提升到 breakeven
  RSI_1h 进入 30- 时   → 触发 "超卖警告"，锁定 50% 利润
  RSI_1h 从 35 反弹至 45 时 → 正常跟踪止损继续
```

### 8.3 RSI 极值紧急平仓

```
RSI 极值紧急平仓规则（在 post-open 风控层执行）:

条件 1: 做多仓位 + RSI_1h 快速升至 > 78
  → 平仓 50%，剩余部分收紧 SL 到 0.5%

条件 2: 做空仓位 + RSI_1h 快速降至 < 22
  → 平仓 50%，剩余部分收紧 SL 到 0.5%

条件 3: RSI_1h 背离（仓位方向相反）且背离超过 5 根 K 线
  → 全平该仓位

"快速" 定义: 3 根 1h K 线内 RSI 变化超过 15 点
```

### 8.4 交易频率控制

```
当前问题: 516 笔（超量）→ 修复后 77 笔（过少）→ 目标 30-120 笔

新频率控制策略:

每日交易上限: 6 笔
  → 保证 30D 上限约 180 笔，但通过质量门控实际降至 60-120

连续亏损限制（更新）:
  consecutive_loss_halt_count: 3 → 4
  (给更多机会，因为单笔亏损会被 RSI 门控大幅减小)

信号冷却机制:
  同一 symbol 上一笔平仓后，冷却 30 分钟才可再开
  (防止在同一个不利位置反复入场)

RSI 套件日内限制:
  RSI_MRV 套件每日最多 3 笔做多 + 3 笔做空
  BOLL_MBV 套件每日最多 2 笔做多 + 2 笔做空
```

---

## 9. 配置文件 Diff

### 9.1 主配置文件（trading_config_fund_flow.json）

```diff
--- a/config/trading_config_fund_flow.json
+++ b/config/trading_config_fund_flow.json
@@ -1,8 +1,15 @@
 {
+  "_version": "2.0-rsi-dual-suite",
+  "_last_updated": "2026-04-12",
   "strategy": {
     "name": "macd_v2",
+    "indicator_suite": "adaptive",
+    "indicator_suite_config": {
+      "trending_suite": "RSI_MRV",
+      "ranging_suite": "BOLL_MBV",
+      "suite_switch_cooldown_bars_1h": 4,
+      "adx_trending_threshold": 25,
+      "adx_ranging_threshold": 22,
+      "boll_bandwidth_trending_ratio": 1.3
+    },
     "leverage": {
       "min_leverage": 3,
       "default_leverage": 4,
@@ -30,19 +37,60 @@
   },
   "signal_thresholds": {
-    "flip_bullish": 0.80,
-    "flip_bearish": 0.80,
-    "green_bar_growing": 1.20,
-    "red_bar_growing": 1.20,
-    "green_bar_shrinking": 1.50,
-    "red_bar_shrinking": 1.50
+    "flip_bullish": 0.80,
+    "flip_bearish": 0.80,
+    "green_bar_growing": 1.15,
+    "red_bar_growing": 1.15,
+    "green_bar_shrinking": 99.0,
+    "red_bar_shrinking": 99.0
   },
+  "rsi_config": {
+    "enabled": true,
+    "period_15m": 7,
+    "period_1h": 14,
+    "period_4h": 21,
+    "overbought_threshold": 70,
+    "oversold_threshold": 30,
+    "extreme_overbought": 78,
+    "extreme_oversold": 22,
+    "divergence_lookback_bars": 10,
+    "divergence_min_price_diff_pct": 0.005,
+    "divergence_min_rsi_diff": 3.0,
+    "rsi_gate": {
+      "enabled": true,
+      "long_rsi_4h_min": 45,
+      "long_rsi_1h_min": 45,
+      "long_rsi_1h_max": 70,
+      "long_rsi_15m_min": 48,
+      "short_rsi_4h_max": 55,
+      "short_rsi_1h_max": 55,
+      "short_rsi_1h_min": 30,
+      "short_rsi_15m_max": 52
+    },
+    "rsi_flip_override": {
+      "flip_bullish_rsi_1h_min": 42,
+      "flip_bullish_rsi_4h_min": 42,
+      "flip_bearish_rsi_1h_max": 58,
+      "flip_bearish_rsi_4h_max": 58
+    },
+    "rsi_dynamic_sl": {
+      "enabled": true,
+      "long_breakeven_trigger_rsi": 65,
+      "long_profit_lock_trigger_rsi": 70,
+      "long_profit_lock_pct": 0.50,
+      "short_breakeven_trigger_rsi": 35,
+      "short_profit_lock_trigger_rsi": 30,
+      "short_profit_lock_pct": 0.50
+    },
+    "rsi_emergency_exit": {
+      "enabled": true,
+      "long_exit_trigger_rsi": 78,
+      "short_exit_trigger_rsi": 22,
+      "fast_move_bars": 3,
+      "fast_move_rsi_delta": 15,
+      "exit_portion_on_trigger": 0.50
+    }
+  },
   "scoring_weights": {
-    "weight_1h_direction": 0.25,
-    "weight_4h_direction": 0.35,
-    "weight_boll_position": 0.25,
-    "weight_vwap": 0.05,
-    "weight_15m_entry": 0.00,
-    "weight_volume": 0.10
+    "RSI_MRV_suite": {
+      "weight_4h_direction": 0.30,
+      "weight_1h_direction": 0.15,
+      "weight_rsi_4h": 0.10,
+      "weight_rsi_1h": 0.20,
+      "weight_rsi_divergence": 0.05,
+      "weight_vwap": 0.15,
+      "weight_15m_entry": 0.00,
+      "weight_volume": 0.05
+    },
+    "BOLL_MBV_suite": {
+      "weight_4h_direction": 0.30,
+      "weight_1h_direction": 0.20,
+      "weight_boll_position": 0.25,
+      "weight_boll_bandwidth": 0.05,
+      "weight_vwap": 0.15,
+      "weight_15m_entry": 0.00,
+      "weight_volume": 0.05
+    }
   },
@@ -80,6 +128,24 @@
   "risk_management": {
     "stop_loss": {
-      "stop_loss_pct": 0.02,
-      "max_stop_loss_pct": 0.025,
+      "RSI_MRV": {
+        "stop_loss_pct": 0.015,
+        "max_stop_loss_pct": 0.020
+      },
+      "BOLL_MBV": {
+        "stop_loss_pct": 0.012,
+        "max_stop_loss_pct": 0.018
+      },
     },
     "take_profit": {
-      "take_profit_pct": 0.04,
-      "take_profit_pct_levels": [0.008, 0.012, 0.020],
-      "take_profit_reduce_pct_levels": [0.25, 0.30, 0.20]
+      "RSI_MRV": {
+        "take_profit_pct_levels": [0.010, 0.018, 0.030],
+        "take_profit_reduce_pct_levels": [0.20, 0.25, 0.20]
+      },
+      "BOLL_MBV": {
+        "take_profit_pct_levels": [0.008, 0.014, 0.022],
+        "take_profit_reduce_pct_levels": [0.30, 0.30, 0.25]
+      }
     },
+    "time_exit": {
+      "RSI_MRV_time_exit_minutes": 90,
+      "BOLL_MBV_time_exit_minutes": 45
+    },
+    "frequency_control": {
+      "daily_max_trades_total": 6,
+      "daily_max_trades_RSI_MRV_long": 3,
+      "daily_max_trades_RSI_MRV_short": 3,
+      "daily_max_trades_BOLL_MBV_long": 2,
+      "daily_max_trades_BOLL_MBV_short": 2,
+      "same_symbol_cooldown_minutes": 30
+    }
   },
   "disable_flags": {
     "disable_red_bar_shrinking_entries": true,
-    "disable_green_bar_shrinking_entries": true
+    "disable_green_bar_shrinking_entries": true,
+    "disable_entries_in_volatile_regime": true,
+    "disable_entries_on_boll_squeeze": true
   }
 }
```

---

## 10. 核心模块伪代码

### 10.1 RSI 计算模块

```python
# src/indicators/rsi_indicator.py

class RSIIndicator:
    """
    多时间框架 RSI 计算器
    支持背离检测
    """

    def __init__(self, config: dict):
        self.period_map = {
            "15m": config.get("period_15m", 7),
            "1h":  config.get("period_1h", 14),
            "4h":  config.get("period_4h", 21),
        }
        self.divergence_lookback = config.get("divergence_lookback_bars", 10)
        self.min_price_diff_pct = config.get("divergence_min_price_diff_pct", 0.005)
        self.min_rsi_diff = config.get("divergence_min_rsi_diff", 3.0)

    def compute_rsi(self, closes: list[float], period: int) -> list[float]:
        """
        标准 Wilder RSI 计算
        输入: 收盘价序列（时序升序）
        输出: RSI 序列，与输入等长（前 period 个为 NaN）
        """
        if len(closes) < period + 1:
            return [float("nan")] * len(closes)

        gains = []
        losses = []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i-1]
            gains.append(max(delta, 0))
            losses.append(max(-delta, 0))

        rsi_series = [float("nan")] * len(closes)

        # 初始 SMA
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

            if avg_loss == 0:
                rsi_series[i + 1] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_series[i + 1] = 100 - (100 / (1 + rs))

        return rsi_series

    def get_rsi_for_timeframe(
        self,
        symbol: str,
        timeframe: str,
        candles: list[dict]
    ) -> float:
        """
        获取最新 RSI 值
        """
        period = self.period_map[timeframe]
        closes = [c["close"] for c in candles]
        rsi_series = self.compute_rsi(closes, period)

        # 返回最新非 NaN 值
        for val in reversed(rsi_series):
            if not math.isnan(val):
                return val
        return float("nan")

    def get_rsi_slope(
        self,
        symbol: str,
        timeframe: str,
        candles: list[dict],
        slope_bars: int = 3
    ) -> float:
        """
        计算 RSI 斜率（最近 slope_bars 根 K 线的 RSI 变化率）
        正值: RSI 上升
        负值: RSI 下降
        """
        period = self.period_map[timeframe]
        closes = [c["close"] for c in candles]
        rsi_series = self.compute_rsi(closes, period)

        valid = [v for v in rsi_series if not math.isnan(v)]
        if len(valid) < slope_bars + 1:
            return 0.0

        return valid[-1] - valid[-1 - slope_bars]

    def detect_divergence(
        self,
        candles: list[dict],
        timeframe: str,
        direction: str
    ) -> dict:
        """
        检测 RSI 背离
        返回: {
            "regular_bullish": bool,   # 看多背离（价格新低，RSI 未创新低）
            "regular_bearish": bool,   # 看空背离
            "hidden_bullish": bool,    # 隐藏多头背离
            "hidden_bearish": bool,    # 隐藏空头背离
            "divergence_strength": float  # 0~1
        }
        """
        period = self.period_map[timeframe]
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        rsi_series = self.compute_rsi(closes, period)

        # 取最近 lookback 个有效 RSI
        valid_rsi = [(i, v) for i, v in enumerate(rsi_series)
                     if not math.isnan(v)]
        recent = valid_rsi[-self.divergence_lookback:]

        if len(recent) < 4:
            return {
                "regular_bullish": False,
                "regular_bearish": False,
                "hidden_bullish": False,
                "hidden_bearish": False,
                "divergence_strength": 0.0
            }

        # 最新点与前 lookback 期内的极值比较
        current_idx, current_rsi = recent[-1]
        current_price_low = lows[current_idx]
        current_price_high = highs[current_idx]

        # 找历史极值（排除最近 2 根）
        historical = recent[:-2]
        min_rsi_idx, min_rsi_val = min(historical, key=lambda x: x[1])
        max_rsi_idx, max_rsi_val = max(historical, key=lambda x: x[1])

        min_price_low = lows[min_rsi_idx]
        max_price_high = highs[max_rsi_idx]

        # 看多背离: 价格创新低，RSI 未创新低
        regular_bullish = (
            current_price_low < min_price_low * (1 - self.min_price_diff_pct)
            and current_rsi > min_rsi_val + self.min_rsi_diff
        )

        # 看空背离: 价格创新高，RSI 未创新高
        regular_bearish = (
            current_price_high > max_price_high * (1 + self.min_price_diff_pct)
            and current_rsi < max_rsi_val - self.min_rsi_diff
        )

        # 隐藏多头背离: 价格高点比高点高，RSI 高点比高点低
        hidden_bullish = (
            current_price_high > max_price_high * (1 + self.min_price_diff_pct)
            and current_rsi < max_rsi_val - self.min_rsi_diff
            and current_rsi > 45  # 需要在中性以上
        )

        # 隐藏空头背离: 价格低点比低点低，RSI 低点比低点高
        hidden_bearish = (
            current_price_low < min_price_low * (1 - self.min_price_diff_pct)
            and current_rsi > min_rsi_val + self.min_rsi_diff
            and current_rsi < 55  # 需要在中性以下
        )

        strength = 0.0
        if regular_bullish:
            price_diff = (min_price_low - current_price_low) / min_price_low
            rsi_diff = (current_rsi - min_rsi_val) / 100
            strength = min(price_diff * 10 + rsi_diff * 5, 1.0)
        elif regular_bearish:
            price_diff = (current_price_high - max_price_high) / max_price_high
            rsi_diff = (max_rsi_val - current_rsi) / 100
            strength = min(price_diff * 10 + rsi_diff * 5, 1.0)

        return {
            "regular_bullish": regular_bullish,
            "regular_bearish": regular_bearish,
            "hidden_bullish": hidden_bullish,
            "hidden_bearish": hidden_bearish,
            "divergence_strength": strength
        }

    def compute_rsi_score(
        self,
        rsi_value: float,
        direction: str,
        timeframe: str
    ) -> float:
        """
        将 RSI 值转换为 0~1 分数
        """
        if math.isnan(rsi_value):
            return 0.0

        if direction == "long":
            if rsi_value >= 70:   return 0.0
            elif rsi_value >= 65: return 0.3
            elif rsi_value >= 55: return 0.9
            elif rsi_value >= 45: return 0.8
            elif rsi_value >= 40: return 0.5
            else:                 return 0.0

        elif direction == "short":
            if rsi_value <= 30:   return 0.0
            elif rsi_value <= 35: return 0.3
            elif rsi_value <= 45: return 0.9
            elif rsi_value <= 55: return 0.8
            elif rsi_value <= 60: return 0.5
            else:                 return 0.0

        return 0.0
```

### 10.2 市场状态分类器

```python
# src/indicators/market_regime_classifier.py

class MarketRegimeClassifier:
    """
    市场状态分类器
    基于 ADX + BOLL 带宽 + ATR 综合判断
    """

    REGIME_TRENDING_BULL = "TRENDING_BULL"
    REGIME_TRENDING_BEAR = "TRENDING_BEAR"
    REGIME_RANGING = "RANGING"
    REGIME_BREAKOUT_WATCH = "BREAKOUT_WATCH"
    REGIME_VOLATILE = "VOLATILE"

    def __init__(self, config: dict):
        self.adx_trending_threshold = config.get("adx_trending_threshold", 25)
        self.adx_ranging_threshold = config.get("adx_ranging_threshold", 22)
        self.boll_trending_bandwidth_ratio = config.get(
            "boll_bandwidth_trending_ratio", 1.3
        )
        self.switch_cooldown_bars = config.get("suite_switch_cooldown_bars_1h", 4)

        # 状态记忆（防止频繁切换）
        self._last_regime = None
        self._last_regime_bar_count = 0
        self._regime_history = []

    def classify(
        self,
        symbol: str,
        adx_1h: float,
        atr_pct_1h: float,
        boll_bandwidth_4h: float,
        boll_bandwidth_4h_mean: float,
        macd_histogram_4h: float,
        macd_histogram_4h_prev: float,
    ) -> str:
        """
        分类当前市场状态
        """

        # 极度波动检测（优先级最高）
        if atr_pct_1h > 0.025:
            return self._update_regime(self.REGIME_VOLATILE, symbol)

        # BOLL squeeze 检测（潜在爆发前）
        bandwidth_ratio = (
            boll_bandwidth_4h / boll_bandwidth_4h_mean
            if boll_bandwidth_4h_mean > 0
            else 1.0
        )
        if boll_bandwidth_4h < 0.02:
            return self._update_regime(self.REGIME_BREAKOUT_WATCH, symbol)

        # 趋势检测
        is_trending = adx_1h >= self.adx_trending_threshold
        is_expanding = bandwidth_ratio >= self.boll_trending_bandwidth_ratio

        if is_trending and is_expanding:
            # 判断趋势方向
            if macd_histogram_4h > 0:
                return self._update_regime(self.REGIME_TRENDING_BULL, symbol)
            else:
                return self._update_regime(self.REGIME_TRENDING_BEAR, symbol)

        # 震荡检测
        is_ranging = adx_1h <= self.adx_ranging_threshold
        if is_ranging:
            return self._update_regime(self.REGIME_RANGING, symbol)

        # 过渡状态（ADX 在 22-25 之间）→ 保持上一状态或默认震荡
        if self._last_regime and self._last_regime_bar_count < self.switch_cooldown_bars:
            self._last_regime_bar_count += 1
            return self._last_regime

        return self._update_regime(self.REGIME_RANGING, symbol)

    def _update_regime(self, new_regime: str, symbol: str) -> str:
        if new_regime != self._last_regime:
            self._last_regime = new_regime
            self._last_regime_bar_count = 0
            self._regime_history.append({
                "symbol": symbol,
                "regime": new_regime,
                "timestamp": time.time()
            })
        else:
            self._last_regime_bar_count += 1
        return new_regime

    def select_suite(self, regime: str) -> str:
        """
        根据市场状态选择指标套件
        """
        suite_map = {
            self.REGIME_TRENDING_BULL:   "RSI_MRV",
            self.REGIME_TRENDING_BEAR:   "RSI_MRV",
            self.REGIME_RANGING:         "BOLL_MBV",
            self.REGIME_BREAKOUT_WATCH:  "BOLL_MBV",
            self.REGIME_VOLATILE:        "NONE",       # 禁止开仓
        }
        return suite_map.get(regime, "BOLL_MBV")

    def is_entry_allowed(self, regime: str) -> bool:
        return regime not in [self.REGIME_VOLATILE]
```

### 10.3 MACD+RSI+VWAP 分析器

```python
# src/indicators/rsi_mrv_analyzer.py

class RSIMRVAnalyzer:
    """
    MACD + RSI + VWAP 套件分析器
    适用于趋势型市场
    """

    def __init__(self, config: dict, rsi_indicator: RSIIndicator):
        self.config = config
        self.rsi = rsi_indicator

        # 权重
        self.w = {
            "macd_4h":    config["scoring_weights"]["RSI_MRV_suite"]["weight_4h_direction"],
            "macd_1h":    config["scoring_weights"]["RSI_MRV_suite"]["weight_1h_direction"],
            "rsi_4h":     config["scoring_weights"]["RSI_MRV_suite"]["weight_rsi_4h"],
            "rsi_1h":     config["scoring_weights"]["RSI_MRV_suite"]["weight_rsi_1h"],
            "rsi_div":    config["scoring_weights"]["RSI_MRV_suite"]["weight_rsi_divergence"],
            "vwap":       config["scoring_weights"]["RSI_MRV_suite"]["weight_vwap"],
            "volume":     config["scoring_weights"]["RSI_MRV_suite"]["weight_volume"],
        }

        self.rsi_gate_cfg = config["rsi_config"]["rsi_gate"]
        self.rsi_flip_cfg = config["rsi_config"]["rsi_flip_override"]

    def analyze(
        self,
        symbol: str,
        direction: str,
        signal_type_1h: str,
        candles_4h: list[dict],
        candles_1h: list[dict],
        candles_15m: list[dict],
        macd_histogram_4h: float,
        macd_histogram_1h: float,
        vwap_score: float,
        volume_ratio: float,
    ) -> dict:
        """
        RSI_MRV 套件分析
        返回: {
            "suite_score": float,
            "rsi_gate_pass": bool,
            "rsi_4h": float,
            "rsi_1h": float,
            "rsi_15m": float,
            "divergence": dict,
            "detail": dict
        }
        """

        # 计算各时间框架 RSI
        rsi_4h  = self.rsi.get_rsi_for_timeframe(symbol, "4h", candles_4h)
        rsi_1h  = self.rsi.get_rsi_for_timeframe(symbol, "1h", candles_1h)
        rsi_15m = self.rsi.get_rsi_for_timeframe(symbol, "15m", candles_15m)
        rsi_slope_1h = self.rsi.get_rsi_slope(symbol, "1h", candles_1h, slope_bars=3)

        # 背离检测
        divergence = self.rsi.detect_divergence(candles_1h, "1h", direction)

        # RSI 三时间框架门控
        rsi_gate_pass = self._check_rsi_gate(
            direction, signal_type_1h,
            rsi_4h, rsi_1h, rsi_15m
        )

        # 分项评分
        macd_4h_score = self._score_macd(macd_histogram_4h, direction)
        macd_1h_score = self._score_macd(macd_histogram_1h, direction)
        rsi_4h_score  = self.rsi.compute_rsi_score(rsi_4h, direction, "4h")
        rsi_1h_score  = self.rsi.compute_rsi_score(rsi_1h, direction, "1h")

        # 背离加分/减分
        div_bonus = 0.0
        if direction == "long":
            if divergence["regular_bullish"]:
                div_bonus = +0.15 * divergence["divergence_strength"]
            elif divergence["regular_bearish"]:
                div_bonus = -0.20   # 看空背离，对做多是减分
            elif divergence["hidden_bullish"]:
                div_bonus = +0.10
        elif direction == "short":
            if divergence["regular_bearish"]:
                div_bonus = +0.15 * divergence["divergence_strength"]
            elif divergence["regular_bullish"]:
                div_bonus = -0.20
            elif divergence["hidden_bearish"]:
                div_bonus = +0.10

        # VWAP 位置分（基于外部传入的 vwap_score）
        vwap_pos_score = self._score_vwap(vwap_score, direction)

        # 音量评分
        volume_score = min(volume_ratio / 1.5, 1.0) if volume_ratio > 0 else 0.0

        # 综合评分
        suite_score = (
            self.w["macd_4h"]  * macd_4h_score +
            self.w["macd_1h"]  * macd_1h_score +
            self.w["rsi_4h"]   * rsi_4h_score  +
            self.w["rsi_1h"]   * rsi_1h_score  +
            self.w["rsi_div"]  * max(div_bonus, -0.20) +
            self.w["vwap"]     * vwap_pos_score +
            self.w["volume"]   * volume_score
        )

        suite_score = max(0.0, min(1.0, suite_score))

        return {
            "suite": "RSI_MRV",
            "suite_score": suite_score,
            "rsi_gate_pass": rsi_gate_pass,
            "rsi_4h": rsi_4h,
            "rsi_1h": rsi_1h,
            "rsi_15m": rsi_15m,
            "rsi_slope_1h": rsi_slope_1h,
            "divergence": divergence,
            "detail": {
                "macd_4h_score": macd_4h_score,
                "macd_1h_score": macd_1h_score,
                "rsi_4h_score": rsi_4h_score,
                "rsi_1h_score": rsi_1h_score,
                "div_bonus": div_bonus,
                "vwap_pos_score": vwap_pos_score,
                "volume_score": volume_score,
            }
        }

    def _check_rsi_gate(
        self,
        direction: str,
        signal_type: str,
        rsi_4h: float,
        rsi_1h: float,
        rsi_15m: float
    ) -> bool:
        """三时间框架 RSI 联合门控"""
        cfg = self.rsi_gate_cfg
        flip_cfg = self.rsi_flip_cfg
        is_flip = signal_type in ["flip_bullish", "flip_bearish"]

        if direction == "long":
            rsi_4h_min = flip_cfg.get("flip_bullish_rsi_4h_min", 42) if is_flip else cfg["long_rsi_4h_min"]
            rsi_1h_min = flip_cfg.get("flip_bullish_rsi_1h_min", 42) if is_flip else cfg["long_rsi_1h_min"]
            return (
                rsi_4h >= rsi_4h_min
                and rsi_1h >= rsi_1h_min
                and rsi_1h <= cfg["long_rsi_1h_max"]
                and rsi_15m >= cfg["long_rsi_15m_min"]
            )
        elif direction == "short":
            rsi_4h_max = flip_cfg.get("flip_bearish_rsi_4h_max", 58) if is_flip else cfg["short_rsi_4h_max"]
            rsi_1h_max = flip_cfg.get("flip_bearish_rsi_1h_max", 58) if is_flip else cfg["short_rsi_1h_max"]
            return (
                rsi_4h <= rsi_4h_max
                and rsi_1h <= rsi_1h_max
                and rsi_1h >= cfg["short_rsi_1h_min"]
                and rsi_15m <= cfg["short_rsi_15m_max"]
            )
        return False

    def _score_macd(self, histogram: float, direction: str) -> float:
        """MACD histogram 方向评分"""
        if direction == "long":
            if histogram > 0:   return 1.0
            elif histogram > -0.0002: return 0.3   # 接近 0
            else:               return 0.0
        elif direction == "short":
            if histogram < 0:   return 1.0
            elif histogram < 0.0002: return 0.3
            else:               return 0.0
        return 0.0

    def _score_vwap(self, vwap_score: float, direction: str) -> float:
        """VWAP 位置评分（基于外部 vwap_score）"""
        if direction == "long":
            if vwap_score > 0.15: return 1.0
            elif vwap_score > 0.10: return 0.7
            elif vwap_score > 0.05: return 0.4
            else: return 0.0
        elif direction == "short":
            if vwap_score > 0.15: return 1.0
            elif vwap_score > 0.10: return 0.7
            elif vwap_score > 0.05: return 0.4
            else: return 0.0
        return 0.0
```

### 10.4 MACD+BOLL+VWAP 分析器

```python
# src/indicators/boll_mbv_analyzer.py

class BOLLMBVAnalyzer:
    """
    MACD + BOLL + VWAP 套件分析器
    适用于震荡型市场
    """

    def __init__(self, config: dict):
        self.config = config

        self.w = {
            "macd_4h":       config["scoring_weights"]["BOLL_MBV_suite"]["weight_4h_direction"],
            "macd_1h":       config["scoring_weights"]["BOLL_MBV_suite"]["weight_1h_direction"],
            "boll_pos":      config["scoring_weights"]["BOLL_MBV_suite"]["weight_boll_position"],
            "boll_bw":       config["scoring_weights"]["BOLL_MBV_suite"]["weight_boll_bandwidth"],
            "vwap":          config["scoring_weights"]["BOLL_MBV_suite"]["weight_vwap"],
            "volume":        config["scoring_weights"]["BOLL_MBV_suite"]["weight_volume"],
        }

    def analyze(
        self,
        symbol: str,
        direction: str,
        current_price: float,
        boll_upper_4h: float,
        boll_lower_4h: float,
        boll_mid_4h: float,
        boll_bandwidth_4h: float,
        boll_bandwidth_4h_mean: float,
        macd_histogram_4h: float,
        macd_histogram_1h: float,
        vwap_score: float,
        volume_ratio: float,
    ) -> dict:
        """
        BOLL_MBV 套件分析
        """

        # BOLL 位置评分
        boll_pos_score = self._score_boll_position(
            current_price, boll_upper_4h, boll_lower_4h, boll_mid_4h, direction
        )

        # BOLL 带宽状态
        bandwidth_state = self._classify_bandwidth(boll_bandwidth_4h)
        bw_quality_score = self._score_bandwidth(bandwidth_state)

        # MACD 方向评分
        macd_4h_score = self._score_macd(macd_histogram_4h, direction)
        macd_1h_score = self._score_macd(macd_histogram_1h, direction)

        # VWAP 评分
        vwap_pos_score = self._score_vwap(vwap_score, direction)

        # 音量评分
        volume_score = min(volume_ratio / 1.5, 1.0) if volume_ratio > 0 else 0.0

        # 综合评分
        suite_score = (
            self.w["macd_4h"]  * macd_4h_score  +
            self.w["macd_1h"]  * macd_1h_score  +
            self.w["boll_pos"] * boll_pos_score  +
            self.w["boll_bw"]  * bw_quality_score +
            self.w["vwap"]     * vwap_pos_score  +
            self.w["volume"]   * volume_score
        )

        # BOLL squeeze 时禁止入场
        entry_allowed = bandwidth_state not in ["squeeze", "expanding"]

        suite_score = max(0.0, min(1.0, suite_score))

        return {
            "suite": "BOLL_MBV",
            "suite_score": suite_score,
            "boll_pos_score": boll_pos_score,
            "bandwidth_state": bandwidth_state,
            "bw_quality_score": bw_quality_score,
            "entry_allowed": entry_allowed,
            "detail": {
                "macd_4h_score": macd_4h_score,
                "macd_1h_score": macd_1h_score,
                "boll_pos_score": boll_pos_score,
                "bw_quality_score": bw_quality_score,
                "vwap_pos_score": vwap_pos_score,
                "volume_score": volume_score,
                "bandwidth_state": bandwidth_state,
            }
        }

    def _score_boll_position(
        self,
        price: float,
        upper: float,
        lower: float,
        mid: float,
        direction: str
    ) -> float:
        """BOLL 位置评分"""
        band_width = upper - lower
        if band_width <= 0:
            return 0.0

        # 价格在带内的相对位置 (0=下轨, 1=上轨)
        relative_pos = (price - lower) / band_width

        if direction == "long":
            # 做多：价格越接近下轨越好
            if price < lower:             return -1.0  # 下轨外，不做多
            elif relative_pos < 0.15:     return 1.0   # 支撑弹性区
            elif relative_pos < 0.35:     return 0.8   # 下半区
            elif relative_pos < 0.50:     return 0.6   # 接近中线下方
            elif relative_pos < 0.65:     return 0.3   # 接近中线上方
            elif relative_pos < 0.80:     return 0.1   # 偏上，不理想
            else:                         return 0.0   # 接近上轨，禁止做多

        elif direction == "short":
            # 做空：价格越接近上轨越好
            if price > upper:             return -1.0  # 上轨外，不做空
            elif relative_pos > 0.85:     return 1.0   # 阻力回落区
            elif relative_pos > 0.65:     return 0.8   # 上半区
            elif relative_pos > 0.50:     return 0.6   # 接近中线上方
            elif relative_pos > 0.35:     return 0.3   # 接近中线下方
            elif relative_pos > 0.20:     return 0.1   # 偏下，不理想
            else:                         return 0.0   # 接近下轨，禁止做空

        return 0.0

    def _classify_bandwidth(self, bandwidth: float) -> str:
        """BOLL 带宽状态分类"""
        if bandwidth < 0.02:   return "squeeze"
        elif bandwidth < 0.04: return "narrow"
        elif bandwidth < 0.08: return "normal"
        elif bandwidth < 0.12: return "wide"
        else:                  return "expanding"

    def _score_bandwidth(self, state: str) -> float:
        """带宽质量评分"""
        scores = {
            "normal":    1.0,
            "narrow":    0.6,
            "wide":      0.4,
            "squeeze":   0.0,
            "expanding": 0.2,
        }
        return scores.get(state, 0.0)

    def _score_macd(self, histogram: float, direction: str) -> float:
        if direction == "long":
            return 1.0 if histogram > 0 else (0.3 if histogram > -0.0002 else 0.0)
        elif direction == "short":
            return 1.0 if histogram < 0 else (0.3 if histogram < 0.0002 else 0.0)
        return 0.0

    def _score_vwap(self, vwap_score: float, direction: str) -> float:
        if vwap_score > 0.15: return 1.0
        elif vwap_score > 0.10: return 0.7
        elif vwap_score > 0.05: return 0.4
        else: return 0.0
```

### 10.5 自适应套件选择器

```python
# src/fund_flow/adaptive_suite_selector.py

class AdaptiveSuiteSelector:
    """
    根据市场状态选择最合适的指标套件并执行分析
    """

    def __init__(
        self,
        config: dict,
        rsi_indicator: RSIIndicator,
        regime_classifier: MarketRegimeClassifier,
        rsi_mrv_analyzer: RSIMRVAnalyzer,
        boll_mbv_analyzer: BOLLMBVAnalyzer,
    ):
        self.config = config
        self.rsi = rsi_indicator
        self.classifier = regime_classifier
        self.rsi_mrv = rsi_mrv_analyzer
        self.boll_mbv = boll_mbv_analyzer

        # 套件评分最低门槛
        self.min_score_RSI_MRV = 0.70
        self.min_score_BOLL_MBV = 0.65

    def select_and_analyze(
        self,
        symbol: str,
        direction: str,
        signal_type_1h: str,
        market_data: dict,
    ) -> dict:
        """
        主入口：选择套件并执行分析
        返回: {
            "selected_suite": str,
            "regime": str,
            "suite_result": dict,
            "final_gate_pass": bool,
            "entry_score": float,
            "block_reason": str | None
        }
        """

        # Step 1: 市场状态分类
        regime = self.classifier.classify(
            symbol=symbol,
            adx_1h=market_data["adx_1h"],
            atr_pct_1h=market_data["atr_pct_1h"],
            boll_bandwidth_4h=market_data["boll_bandwidth_4h"],
            boll_bandwidth_4h_mean=market_data["boll_bandwidth_4h_mean"],
            macd_histogram_4h=market_data["macd_histogram_4h"],
            macd_histogram_4h_prev=market_data.get("macd_histogram_4h_prev", 0),
        )

        # Step 2: 检查是否允许入场
        if not self.classifier.is_entry_allowed(regime):
            return {
                "selected_suite": "NONE",
                "regime": regime,
                "suite_result": None,
                "final_gate_pass": False,
                "entry_score": 0.0,
                "block_reason": f"VOLATILE_REGIME: {regime}"
            }

        # Step 3: 选择套件
        selected_suite = self.classifier.select_suite(regime)

        # Step 4: 执行套件分析
        if selected_suite == "RSI_MRV":
            suite_result = self.rsi_mrv.analyze(
                symbol=symbol,
                direction=direction,
                signal_type_1h=signal_type_1h,
                candles_4h=market_data["candles_4h"],
                candles_1h=market_data["candles_1h"],
                candles_15m=market_data["candles_15m"],
                macd_histogram_4h=market_data["macd_histogram_4h"],
                macd_histogram_1h=market_data["macd_histogram_1h"],
                vwap_score=market_data["vwap_score"],
                volume_ratio=market_data["volume_ratio"],
            )

            # RSI gate 必须通过
            if not suite_result["rsi_gate_pass"]:
                return {
                    "selected_suite": "RSI_MRV",
                    "regime": regime,
                    "suite_result": suite_result,
                    "final_gate_pass": False,
                    "entry_score": suite_result["suite_score"],
                    "block_reason": (
                        f"RSI_GATE_FAIL: "
                        f"RSI_4h={suite_result['rsi_4h']:.1f}, "
                        f"RSI_1h={suite_result['rsi_1h']:.1f}, "
                        f"RSI_15m={suite_result['rsi_15m']:.1f}"
                    )
                }

            # 评分门槛
            if suite_result["suite_score"] < self.min_score_RSI_MRV:
                return {
                    "selected_suite": "RSI_MRV",
                    "regime": regime,
                    "suite_result": suite_result,
                    "final_gate_pass": False,
                    "entry_score": suite_result["suite_score"],
                    "block_reason": (
                        f"RSI_MRV_SCORE_LOW: "
                        f"{suite_result['suite_score']:.3f} < {self.min_score_RSI_MRV}"
                    )
                }

        elif selected_suite == "BOLL_MBV":
            suite_result = self.boll_mbv.analyze(
                symbol=symbol,
                direction=direction,
                current_price=market_data["current_price"],
                boll_upper_4h=market_data["boll_upper_4h"],
                boll_lower_4h=market_data["boll_lower_4h"],
                boll_mid_4h=market_data["boll_mid_4h"],
                boll_bandwidth_4h=market_data["boll_bandwidth_4h"],
                boll_bandwidth_4h_mean=market_data["boll_bandwidth_4h_mean"],
                macd_histogram_4h=market_data["macd_histogram_4h"],
                macd_histogram_1h=market_data["macd_histogram_1h"],
                vwap_score=market_data["vwap_score"],
                volume_ratio=market_data["volume_ratio"],
            )

            # BOLL squeeze 禁止入场
            if not suite_result["entry_allowed"]:
                return {
                    "selected_suite": "BOLL_MBV",
                    "regime": regime,
                    "suite_result": suite_result,
                    "final_gate_pass": False,
                    "entry_score": suite_result["suite_score"],
                    "block_reason": (
                        f"BOLL_BANDWIDTH_STATE: "
                        f"{suite_result['bandwidth_state']}"
                    )
                }

            # 评分门槛
            if suite_result["suite_score"] < self.min_score_BOLL_MBV:
                return {
                    "selected_suite": "BOLL_MBV",
                    "regime": regime,
                    "suite_result": suite_result,
                    "final_gate_pass": False,
                    "entry_score": suite_result["suite_score"],
                    "block_reason": (
                        f"BOLL_MBV_SCORE_LOW: "
                        f"{suite_result['suite_score']:.3f} < {self.min_score_BOLL_MBV}"
                    )
                }

        return {
            "selected_suite": selected_suite,
            "regime": regime,
            "suite_result": suite_result,
            "final_gate_pass": True,
            "entry_score": suite_result["suite_score"],
            "block_reason": None
        }
```

### 10.6 综合评分聚合器

```python
# 在 MACDStrategyV2Engine.analyze() 内集成

def analyze_with_dual_suite(
    self,
    symbol: str,
    flow_context: dict,
    suite_selector: AdaptiveSuiteSelector,
) -> dict:
    """
    双套件综合分析
    替代旧的 MACDStrategyV2Engine.analyze()
    """

    # 原有 MACD 信号分析（保留）
    macd_result = self._compute_macd_signals(symbol, flow_context)

    # 如果 MACD 本身无方向，直接返回中性
    if macd_result["direction"] == "neutral":
        return {"direction": "neutral", "score": 0.0, "reason": "MACD_NEUTRAL"}

    direction = macd_result["direction"]
    signal_type_1h = macd_result["signal_type_1h"]

    # 信号族白名单检查（shrink 族已禁）
    if not self._is_signal_family_allowed(signal_type_1h):
        return {
            "direction": "neutral",
            "score": 0.0,
            "reason": f"SIGNAL_FAMILY_BLOCKED: {signal_type_1h}"
        }

    # 构建市场数据包
    market_data = self._build_market_data(symbol, flow_context)

    # 套件选择与分析
    suite_analysis = suite_selector.select_and_analyze(
        symbol=symbol,
        direction=direction,
        signal_type_1h=signal_type_1h,
        market_data=market_data,
    )

    if not suite_analysis["final_gate_pass"]:
        return {
            "direction": "neutral",
            "score": suite_analysis["entry_score"],
            "reason": suite_analysis["block_reason"],
            "suite": suite_analysis["selected_suite"],
            "regime": suite_analysis["regime"],
        }

    # 将套件评分与原有信号评分融合
    macd_base_score = macd_result["signal_score"]
    suite_score = suite_analysis["entry_score"]

    # 融合公式：MACD 基础分 × 0.6 + 套件分 × 0.4
    final_score = macd_base_score * 0.6 + suite_score * 0.4

    return {
        "direction": direction,
        "score": final_score,
        "macd_base_score": macd_base_score,
        "suite_score": suite_score,
        "selected_suite": suite_analysis["selected_suite"],
        "regime": suite_analysis["regime"],
        "suite_result": suite_analysis["suite_result"],
        "signal_type_1h": signal_type_1h,
        "reason": "PASS",
    }

def _is_signal_family_allowed(self, signal_type: str) -> bool:
    """信号族白名单检查"""
    disabled_flag_map = {
        "red_bar_shrinking":   self.config.get("disable_red_bar_shrinking_entries", True),
        "green_bar_shrinking": self.config.get("disable_green_bar_shrinking_entries", True),
    }
    if signal_type in disabled_flag_map and disabled_flag_map[signal_type]:
        return False
    return True
```

### 10.7 Pretrade RSI 门控

```python
# 在 pretrade_risk_gate 中新增 RSI 极值检查

def pretrade_rsi_veto(
    self,
    symbol: str,
    direction: str,
    rsi_1h: float,
    rsi_config: dict
) -> dict:
    """
    预交易 RSI 极值否决
    """
    extreme_overbought = rsi_config.get("extreme_overbought", 78)
    extreme_oversold   = rsi_config.get("extreme_oversold", 22)

    if direction == "long" and rsi_1h > extreme_overbought:
        return {
            "veto": True,
            "reason": f"RSI_EXTREME_OVERBOUGHT: RSI_1h={rsi_1h:.1f} > {extreme_overbought}"
        }

    if direction == "short" and rsi_1h < extreme_oversold:
        return {
            "veto": True,
            "reason": f"RSI_EXTREME_OVERSOLD: RSI_1h={rsi_1h:.1f} < {extreme_oversold}"
        }

    return {"veto": False, "reason": None}
```

---

## 11. 代码修改 Diff（src 层）

### 11.1 macd_strategy_v2.py

```diff
--- a/src/fund_flow/macd_strategy_v2.py
+++ b/src/fund_flow/macd_strategy_v2.py
@@ -1,6 +1,12 @@
 import math
 import logging
+from src.indicators.rsi_indicator import RSIIndicator
+from src.indicators.market_regime_classifier import MarketRegimeClassifier
+from src.indicators.rsi_mrv_analyzer import RSIMRVAnalyzer
+from src.indicators.boll_mbv_analyzer import BOLLMBVAnalyzer
+from src.fund_flow.adaptive_suite_selector import AdaptiveSuiteSelector

 logger = logging.getLogger(__name__)

 class MACDStrategyV2Engine:

     def __init__(self, config: dict):
         self.config = config
+        # 初始化双套件组件
+        rsi_cfg = config.get("rsi_config", {})
+        suite_cfg = config.get("indicator_suite_config", {})
+
+        self.rsi_indicator    = RSIIndicator(rsi_cfg)
+        self.regime_classifier = MarketRegimeClassifier(suite_cfg)
+        self.rsi_mrv_analyzer = RSIMRVAnalyzer(config, self.rsi_indicator)
+        self.boll_mbv_analyzer = BOLLMBVAnalyzer(config)
+        self.suite_selector   = AdaptiveSuiteSelector(
+            config,
+            self.rsi_indicator,
+            self.regime_classifier,
+            self.rsi_mrv_analyzer,
+            self.boll_mbv_analyzer,
+        )

@@ -45,20 +57,45 @@
-    def analyze(self, symbol, flow_context):
+    def analyze(self, symbol: str, flow_context: dict) -> dict:
         """
-        Original MACD analysis
+        双套件增强版分析
+        保留原有 MACD 信号逻辑，新增 RSI 和 BOLL 套件确认
         """
-        # [原有代码保持]
-        direction = self._resolve_direction(flow_context)
-        score = self._compute_score(flow_context)
-        return {"direction": direction, "score": score}
+        # Step 1: 原有 MACD 信号分析（保持不变）
+        macd_result = self._compute_macd_signals(symbol, flow_context)
+
+        if macd_result["direction"] == "neutral":
+            return {"direction": "neutral", "score": 0.0,
+                    "reason": "MACD_NEUTRAL"}
+
+        direction     = macd_result["direction"]
+        signal_type_1h = macd_result["signal_type_1h"]
+
+        # Step 2: 信号族白名单过滤
+        if not self._is_signal_family_allowed(signal_type_1h):
+            logger.debug(f"[{symbol}] Signal family blocked: {signal_type_1h}")
+            return {"direction": "neutral", "score": 0.0,
+                    "reason": f"FAMILY_BLOCKED:{signal_type_1h}"}
+
+        # Step 3: 双套件分析
+        market_data = self._build_market_data(symbol, flow_context)
+        suite_analysis = self.suite_selector.select_and_analyze(
+            symbol=symbol,
+            direction=direction,
+            signal_type_1h=signal_type_1h,
+            market_data=market_data,
+        )
+
+        if not suite_analysis["final_gate_pass"]:
+            logger.debug(
+                f"[{symbol}] Suite gate failed: {suite_analysis['block_reason']}"
+            )
+            return {
+                "direction": "neutral",
+                "score": suite_analysis["entry_score"],
+                "reason": suite_analysis["block_reason"],
+                "suite": suite_analysis["selected_suite"],
+                "regime": suite_analysis["regime"],
+            }
+
+        # Step 4: 融合评分
+        macd_base_score = macd_result["signal_score"]
+        suite_score     = suite_analysis["entry_score"]
+        final_score = macd_base_score * 0.6 + suite_score * 0.4
+
+        result = {
+            "direction":      direction,
+            "score":          final_score,
+            "signal_type_1h": signal_type_1h,
+            "macd_base_score": macd_base_score,
+            "suite_score":    suite_score,
+            "selected_suite": suite_analysis["selected_suite"],
+            "regime":         suite_analysis["regime"],
+            "suite_result":   suite_analysis["suite_result"],
+            "reason":         "PASS",
+        }
+
+        # Step 5: 记录套件选择日志
+        logger.info(
+            f"[{symbol}] Suite={result['selected_suite']} "
+            f"Regime={result['regime']} "
+            f"Direction={direction} "
+            f"Score={final_score:.3f} "
+            f"(MACD={macd_base_score:.3f}, Suite={suite_score:.3f})"
+        )
+
+        return result

+    def _build_market_data(self, symbol: str, flow_context: dict) -> dict:
+        """从 flow_context 提取并组装市场数据包"""
+        ctx = flow_context
+        return {
+            "adx_1h":              ctx.get("adx_1h", 20),
+            "atr_pct_1h":          ctx.get("atr_pct_1h", 0.01),
+            "boll_upper_4h":       ctx.get("boll_upper_4h", 0),
+            "boll_lower_4h":       ctx.get("boll_lower_4h", 0),
+            "boll_mid_4h":         ctx.get("boll_mid_4h", 0),
+            "boll_bandwidth_4h":   ctx.get("boll_bandwidth_4h", 0.05),
+            "boll_bandwidth_4h_mean": ctx.get("boll_bandwidth_4h_mean", 0.05),
+            "macd_histogram_4h":   ctx.get("macd_histogram_4h", 0),
+            "macd_histogram_4h_prev": ctx.get("macd_histogram_4h_prev", 0),
+            "macd_histogram_1h":   ctx.get("macd_histogram_1h", 0),
+            "vwap_score":          ctx.get("vwap_score", 0),
+            "volume_ratio":        ctx.get("volume_ratio", 1.0),
+            "current_price":       ctx.get("current_price", 0),
+            "candles_4h":          ctx.get("candles_4h", []),
+            "candles_1h":          ctx.get("candles_1h", []),
+            "candles_15m":         ctx.get("candles_15m", []),
+        }
```

### 11.2 decision_engine.py

```diff
--- a/src/fund_flow/decision_engine.py
+++ b/src/fund_flow/decision_engine.py
@@ -50,6 +50,30 @@
     def _decide_macd_v2_strategy(self, symbol, flow_context):
         result = self.macd_engine.analyze(symbol, flow_context)

+        # 新增：记录套件选择信息到 flow_context
+        if "selected_suite" in result:
+            flow_context["_suite_metadata"] = {
+                "suite":   result.get("selected_suite"),
+                "regime":  result.get("regime"),
+                "suite_score": result.get("suite_score"),
+                "rsi_values": {
+                    "rsi_4h":  result.get("suite_result", {}).get("rsi_4h"),
+                    "rsi_1h":  result.get("suite_result", {}).get("rsi_1h"),
+                    "rsi_15m": result.get("suite_result", {}).get("rsi_15m"),
+                } if result.get("selected_suite") == "RSI_MRV" else {},
+            }
+
+        # 新增：pretrade RSI 极值否决（在转换为 BUY/SELL 之前）
+        if result["direction"] in ("long", "short"):
+            suite_result = result.get("suite_result") or {}
+            rsi_1h = suite_result.get("rsi_1h")
+            if rsi_1h is not None:
+                rsi_veto = self.pretrade_gate.pretrade_rsi_veto(
+                    symbol=symbol,
+                    direction=result["direction"],
+                    rsi_1h=rsi_1h,
+                    rsi_config=self.config.get("rsi_config", {}),
+                )
+                if rsi_veto["veto"]:
+                    logger.warning(
+                        f"[{symbol}] Pretrade RSI veto: {rsi_veto['reason']}"
+                    )
+                    return {"action": "HOLD", "reason": rsi_veto["reason"]}
+
         if result["direction"] == "long":
             return {"action": "BUY",  "score": result["score"]}
         elif result["direction"] == "short":
@@ -80,6 +110,40 @@
+    def _apply_rsi_dynamic_sl_adjustment(
+        self,
+        symbol: str,
+        position: dict,
+        rsi_1h: float,
+        rsi_config: dict,
+    ) -> dict:
+        """
+        RSI 动态止损调整（在 post-open 风控中调用）
+        """
+        dsl_cfg = rsi_config.get("rsi_dynamic_sl", {})
+        if not dsl_cfg.get("enabled", False):
+            return {}

+        direction = position.get("side")
+        sl_actions = {}

+        if direction == "long":
+            if rsi_1h >= dsl_cfg.get("long_profit_lock_trigger_rsi", 70):
+                lock_pct = dsl_cfg.get("long_profit_lock_pct", 0.50)
+                sl_actions["partial_close_pct"] = lock_pct
+                sl_actions["reason"] = f"RSI_PROFIT_LOCK: RSI_1h={rsi_1h:.1f}"
+            elif rsi_1h >= dsl_cfg.get("long_breakeven_trigger_rsi", 65):
+                sl_actions["move_sl_to_breakeven"] = True
+                sl_actions["reason"] = f"RSI_BREAKEVEN: RSI_1h={rsi_1h:.1f}"

+        elif direction == "short":
+            if rsi_1h <= dsl_cfg.get("short_profit_lock_trigger_rsi", 30):
+                lock_pct = dsl_cfg.get("short_profit_lock_pct", 0.50)
+                sl_actions["partial_close_pct"] = lock_pct
+                sl_actions["reason"] = f"RSI_PROFIT_LOCK: RSI_1h={rsi_1h:.1f}"
+            elif rsi_1h <= dsl_cfg.get("short_breakeven_trigger_rsi", 35):
+                sl_actions["move_sl_to_breakeven"] = True
+                sl_actions["reason"] = f"RSI_BREAKEVEN: RSI_1h={rsi_1h:.1f}"

+        return sl_actions
```

### 11.3 fund_flow_bot.py

```diff
--- a/src/app/fund_flow_bot.py
+++ b/src/app/fund_flow_bot.py
@@ -30,6 +30,22 @@
+    def _build_enhanced_flow_context(self, symbol, raw_context):
+        """
+        在原有 flow_context 基础上追加 BOLL 带宽均值等新字段
+        """
+        ctx = raw_context.copy()
+
+        # 计算 BOLL 带宽历史均值（用于带宽状态判断）
+        candles_4h = ctx.get("candles_4h", [])
+        if len(candles_4h) >= 20:
+            bw_history = []
+            for c in candles_4h[-20:]:
+                upper = c.get("boll_upper", 0)
+                lower = c.get("boll_lower", 0)
+                mid   = c.get("boll_mid", 1)
+                if mid > 0:
+                    bw_history.append((upper - lower) / mid)
+            if bw_history:
+                ctx["boll_bandwidth_4h_mean"] = sum(bw_history) / len(bw_history)
+            else:
+                ctx["boll_bandwidth_4h_mean"] = ctx.get("boll_bandwidth_4h", 0.05)
+        else:
+            ctx["boll_bandwidth_4h_mean"] = ctx.get("boll_bandwidth_4h", 0.05)
+
+        # 追加当前价格
+        ctx["current_price"] = ctx.get("last_price", 0)
+
+        return ctx

+    def _check_frequency_control(self, symbol: str, direction: str, suite: str) -> bool:
+        """
+        日内交易频率控制检查
+        返回 True = 允许开仓，False = 超出限制
+        """
+        freq_cfg = self.config.get("risk_management", {}).get("frequency_control", {})
+        today = datetime.utcnow().date()
+
+        # 同 symbol 冷却期检查
+        cooldown_min = freq_cfg.get("same_symbol_cooldown_minutes", 30)
+        last_close = self.position_tracker.get_last_close_time(symbol)
+        if last_close:
+            elapsed = (datetime.utcnow() - last_close).total_seconds() / 60
+            if elapsed < cooldown_min:
+                logger.info(
+                    f"[{symbol}] Cooldown active: {elapsed:.1f}min < {cooldown_min}min"
+                )
+                return False
+
+        # 日内总量检查
+        daily_total = self.trade_counter.get_daily_count(today)
+        max_daily = freq_cfg.get("daily_max_trades_total", 6)
+        if daily_total >= max_daily:
+            logger.info(f"Daily max trades reached: {daily_total}/{max_daily}")
+            return False
+
+        # 套件 + 方向检查
+        key = f"daily_max_trades_{suite}_{direction}"
+        suite_dir_max = freq_cfg.get(key, 3)
+        suite_dir_count = self.trade_counter.get_daily_count(
+            today, suite=suite, direction=direction
+        )
+        if suite_dir_count >= suite_dir_max:
+            logger.info(
+                f"[{suite}/{direction}] Daily limit: {suite_dir_count}/{suite_dir_max}"
+            )
+            return False
+
+        return True
```

### 11.4 新增 rsi_indicator.py

```python
# src/indicators/rsi_indicator.py
# （完整版见第 10.1 节）

"""
RSI 指标计算器

功能:
  - 多时间框架 RSI 计算 (7/14/21 周期)
  - RSI 斜率计算
  - 背离检测 (正则多空背离 + 隐藏多空背离)
  - RSI 评分映射（0~1）

依赖: math, logging
无外部 pip 依赖
"""

import math
import logging

logger = logging.getLogger(__name__)

# [完整实现见 10.1 节伪代码]
# 此处为文件头注释和模块声明
```

### 11.5 新增 market_regime_classifier.py

```python
# src/indicators/market_regime_classifier.py

"""
市场状态分类器

功能:
  - 基于 ADX + BOLL 带宽 + ATR 判断市场状态
  - 输出: TRENDING_BULL / TRENDING_BEAR / RANGING / BREAKOUT_WATCH / VOLATILE
  - 状态切换冷却（防止频繁摇摆）
  - 套件选择建议

依赖: time, logging
"""

import time
import logging

logger = logging.getLogger(__name__)

# [完整实现见 10.2 节伪代码]
```

---

## 12. 信号族白名单重构

### 12.1 当前状态

```
当前实际运行信号族（修复后 77 笔回测）:
  ✅ flip_bearish  → 唯一有效族
  ❌ flip_bullish  → 被间接屏蔽
  ❌ green_bar_growing → 被间接屏蔽
  ❌ red_bar_growing   → 被间接屏蔽
  ❌ green_bar_shrinking → 正确禁用
  ❌ red_bar_shrinking   → 正确禁用
```

### 12.2 目标状态（引入 RSI 后）

```
目标运行信号族（RSI + 套件双过滤）:
  ✅ flip_bullish    → RSI 翻多确认（RSI_MRV 套件）
  ✅ flip_bearish    → RSI 翻空确认（RSI_MRV 套件）
  ✅ green_bar_growing → RSI 动量延续确认（RSI_MRV 套件）
  ✅ red_bar_growing   → RSI 动量延续确认（RSI_MRV 套件）
  ⚠️  green_bar_growing（BOLL 边界回测）→ BOLL_MBV 套件
  ⚠️  red_bar_growing（BOLL 边界回测）→ BOLL_MBV 套件
  ❌ green_bar_shrinking → 永久禁用
  ❌ red_bar_shrinking   → 永久禁用
```

### 12.3 白名单配置

```diff
--- a/config/trading_config_fund_flow.json
+++ b/config/trading_config_fund_flow.json
+  "signal_family_whitelist": {
+    "flip_bullish": {
+      "enabled": true,
+      "preferred_suite": "RSI_MRV",
+      "allow_BOLL_MBV_fallback": true
+    },
+    "flip_bearish": {
+      "enabled": true,
+      "preferred_suite": "RSI_MRV",
+      "allow_BOLL_MBV_fallback": true
+    },
+    "green_bar_growing": {
+      "enabled": true,
+      "preferred_suite": "RSI_MRV",
+      "allow_BOLL_MBV_fallback": true,
+      "BOLL_MBV_condition": "price_near_lower_band"
+    },
+    "red_bar_growing": {
+      "enabled": true,
+      "preferred_suite": "RSI_MRV",
+      "allow_BOLL_MBV_fallback": true,
+      "BOLL_MBV_condition": "price_near_upper_band"
+    },
+    "green_bar_shrinking": {
+      "enabled": false,
+      "disable_reason": "negative_alpha_confirmed"
+    },
+    "red_bar_shrinking": {
+      "enabled": false,
+      "disable_reason": "negative_alpha_confirmed"
+    }
+  }
```

### 12.4 白名单代码实现

```python
def _check_signal_family_whitelist(
    self,
    signal_type_1h: str,
    selected_suite: str,
    market_data: dict,
    direction: str
) -> dict:
    """
    信号族白名单细化检查

    返回: {
        "allowed": bool,
        "reason": str | None
    }
    """
    whitelist_cfg = self.config.get("signal_family_whitelist", {})
    family_cfg = whitelist_cfg.get(signal_type_1h, {})

    if not family_cfg.get("enabled", False):
        return {
            "allowed": False,
            "reason": family_cfg.get("disable_reason", "FAMILY_DISABLED")
        }

    preferred_suite = family_cfg.get("preferred_suite", "RSI_MRV")
    allow_fallback = family_cfg.get("allow_BOLL_MBV_fallback", False)

    # 套件不匹配且不允许回退
    if selected_suite != preferred_suite and not allow_fallback:
        return {
            "allowed": False,
            "reason": f"SUITE_MISMATCH: need {preferred_suite}, got {selected_suite}"
        }

    # BOLL_MBV 回退时的特殊条件检查
    if selected_suite == "BOLL_MBV" and selected_suite != preferred_suite:
        boll_cond = family_cfg.get("BOLL_MBV_condition")
        if boll_cond == "price_near_lower_band":
            price = market_data.get("current_price", 0)
            lower = market_data.get("boll_lower_4h", 0)
            mid   = market_data.get("boll_mid_4h", 1)
            relative_pos = (price - lower) / max(mid - lower, 1)
            if relative_pos > 0.35:  # 价格不够接近下轨
                return {
                    "allowed": False,
                    "reason": "BOLL_MBV_CONDITION_FAIL: price not near lower band"
                }
        elif boll_cond == "price_near_upper_band":
            price = market_data.get("current_price", 0)
            upper = market_data.get("boll_upper_4h", 0)
            mid   = market_data.get("boll_mid_4h", 1)
            relative_pos = (upper - price) / max(upper - mid, 1)
            if relative_pos > 0.35:  # 价格不够接近上轨
                return {
                    "allowed": False,
                    "reason": "BOLL_MBV_CONDITION_FAIL: price not near upper band"
                }

    return {"allowed": True, "reason": None}
```

---

## 13. Pocket Override 更新

### 13.1 新增 RSI 字段到所有 pocket

```diff
--- pocket_overrides
+++ pocket_overrides_with_rsi

 "green_bar_growing|short_retest_reject":
+  rsi_gate_override:
+    short_rsi_1h_max: 52     # 比默认 55 更严
+    short_rsi_15m_max: 50

 "flip_bullish|long_reclaim_confirmed":
+  rsi_gate_override:
+    long_rsi_1h_min: 45      # 允许略低（刚翻多）
+    require_rsi_cross_above_50: true

 "green_bar_growing|long_dual_support":
   min_signal_score: 0.90
   min_vwap_score: 0.12
   min_entry_score: 0.35
   require_cvd_ok: true
   require_cvd_momentum_ok: true
   require_strict_1h_confirmation: true
   disallow_trial_entry: true
+  rsi_gate_override:
+    long_rsi_4h_min: 48      # 更严格
+    long_rsi_1h_min: 50      # 要求 RSI 在 50+
+    long_rsi_1h_max: 65

 "red_bar_growing|short_dual_pressure":
   min_signal_score: 0.90
   min_vwap_score: 0.16
   min_entry_score: 0.35
   require_cvd_ok: true
   require_cvd_momentum_ok: true
   require_strict_1h_confirmation: true
   disallow_trial_entry: true
+  rsi_gate_override:
+    short_rsi_4h_max: 52     # 更严格
+    short_rsi_1h_max: 50     # 要求 RSI 在 50-
+    short_rsi_1h_min: 32
```

### 13.2 新增震荡市专用 pocket

```yaml
# 新 pocket：BOLL 下轨做多（BOLL_MBV 套件专属）
"green_bar_growing|long_below_boll_lower_bounce":
  enabled: true
  preferred_suite: "BOLL_MBV"
  require_price_below_boll_mid: true
  boll_position_min_score: 0.8
  min_signal_score: 1.10
  min_vwap_score: 0.10
  rsi_gate_override:
    # 震荡市做多：RSI 超卖反弹
    long_rsi_1h_min: 35       # 允许从超卖区反弹
    long_rsi_1h_max: 55       # 不追过热
    long_rsi_slope_must_positive: true

# 新 pocket：BOLL 上轨做空（BOLL_MBV 套件专属）
"red_bar_growing|short_above_boll_upper_reject":
  enabled: true
  preferred_suite: "BOLL_MBV"
  require_price_above_boll_mid: true
  boll_position_min_score: 0.8
  min_signal_score: 1.10
  min_vwap_score: 0.10
  rsi_gate_override:
    # 震荡市做空：RSI 超买回落
    short_rsi_1h_max: 65      # 允许从超买区回落
    short_rsi_1h_min: 45      # 不追过冷
    short_rsi_slope_must_negative: true
```

---

## 14. 回测预期估算

### 14.1 交易数量预期

```
当前修复后: 77 笔（仅 flip_bearish）

引入 RSI 双套件后，重新开放信号族:
  flip_bullish     + RSI 过滤后通过率约 40%
  flip_bearish     + RSI 过滤后通过率约 50%
  green_bar_growing + RSI 过滤后通过率约 35%
  red_bar_growing  + RSI 过滤后通过率约 35%

估算:
  原始信号生成: ~1050 / 30D
  经套件 + RSI 过滤后剩余: 约 15-20%
  预计最终交易: 1050 × 0.17 × (过滤通过率) ≈ 80-130 笔

目标范围: 30-120 笔（基本匹配）
```

### 14.2 胜率预期

```
当前胜率: 63.76%（含 shrink 拖累）
修复后（仅 flip_bearish）: 预计 ~72-75%

引入 RSI 过滤后各族预期胜率:
  flip_bullish   （RSI 确认翻多）: ~78%
  flip_bearish   （RSI 确认翻空）: ~80%
  green_bar_growing（RSI 延续）: ~74%
  red_bar_growing  （RSI 延续）: ~72%

加权平均胜率预期: ~76%

目标: 80%+
差距: ~4pp

弥补方案:
  1. BOLL_MBV 套件在震荡市提供额外高胜率信号（震荡市边界反弹成功率高）
  2. RSI 背离加分进一步筛选高质量信号
  3. 如 BOLL_MBV 震荡 pocket 胜率达 82%+，加权后可到 78-80%
```

### 14.3 胜负比预期

```
当前:
  avg_win:  +11.14
  avg_loss: -22.50
  比值: 0.495（严重不对称）

优化后（新 TP/SL）:
  RSI_MRV 套件:
    预期 avg_win:  ~+16（TP 层提升 + 跟踪止损延伸）
    预期 avg_loss: ~-15（SL 从 2% 收到 1.5%）
    比值: ~1.07

  BOLL_MBV 套件:
    预期 avg_win:  ~+13（快速锁定）
    预期 avg_loss: ~-12（SL 从 2% 收到 1.2%）
    比值: ~1.08

  加权预期: avg_win/avg_loss ≈ 1.07-1.10

目标: ≥ 1.5
差距: 仍有约 0.4 的差距

补充方案: RSI 背离信号作为额外 TP 触发器
  → 看空背离出现时主动平仓做多仓 50% → 提升 avg_win
  → 看多背离出现时主动平仓做空仓 50% → 提升 avg_win
  → 预计可将比值提升到 1.2-1.3
```

### 14.4 收益预期

```
悲观情景 (新增保守):
  Trade count: 60
  Win rate: 74%
  avg_win/avg_loss: 1.1
  Expectancy: 0.74 × avg_win - 0.26 × avg_loss
            = 0.74 × X - 0.26 × (X/1.1)
            = X × (0.74 - 0.236) = X × 0.504
  若 avg_win ≈ 14 → Expectancy ≈ +7.1 / trade
  Total PnL: 60 × 7.1 ≈ +$426 (相对基准)
  Return: 取决于账户规模，估计 +12-18%

中性情景:
  Trade count: 90
  Win rate: 78%
  avg_win/avg_loss: 1.2
  Expectancy: ≈ +9.2 / trade
  Total PnL: 90 × 9.2 ≈ +$828
  Return: 估计 +28-35%

乐观情景:
  Trade count: 100
  Win rate: 82%
  avg_win/avg_loss: 1.4
  Expectancy: ≈ +13.1 / trade
  Total PnL: 100 × 13.1 ≈ +$1310
  Return: 估计 +45-55%

目标: 50%+（需要乐观情景实现）
```

---

## 15. 分阶段上线计划

### Phase 0：准备（1-2 天）

```
任务清单:
  □ 新增 src/indicators/rsi_indicator.py
  □ 新增 src/indicators/market_regime_classifier.py
  □ 新增 src/indicators/rsi_mrv_analyzer.py
  □ 新增 src/indicators/boll_mbv_analyzer.py
  □ 新增 src/fund_flow/adaptive_suite_selector.py
  □ 更新 config/trading_config_fund_flow.json（新增字段）
  □ 单元测试: RSI 计算准确性
  □ 单元测试: 背离检测逻辑
  □ 单元测试: 市场状态分类器

验收标准:
  RSI 计算误差 < 0.1（与 TradingView 对比）
  背离检测覆盖已知历史案例 ≥ 80%
  市场状态分类 < 10% 误分率（对比人工标注样本）
```

### Phase 1：RSI Gate 上线（3-4 天）

```
上线范围:
  仅上线 RSI 三时间框架门控
  暂不切换评分权重
  保持当前单套件（BOLL_MBV 参数）

目的:
  验证 RSI gate 的拦截率
  确保不误伤正 alpha 信号

监控指标:
  RSI gate 拦截率（预期 20-30%）
  gate 后信号的后续胜率变化
  无明显好信号被误伤

持续时间: 5-7 天回测验证 + 2-3 天沙盒运行
```

### Phase 2：双套件切换上线（5-7 天）

```
上线范围:
  上线市场状态分类器
  上线套件选择逻辑
  上线 RSI_MRV 评分权重（趋势市）
  上线 BOLL_MBV 评分权重（震荡市）

目的:
  验证套件切换时机的准确性
  确保趋势市和震荡市都能有效捕捉机会

监控指标:
  套件切换频率（预期每天 1-3 次 / 每 symbol）
  各套件的实际胜率
  切换延迟（预期 < 4h）
```

### Phase 3：正 Alpha 族重新开放（7-10 天）

```
上线范围:
  重新开放 flip_bullish + green_bar_growing + red_bar_growing
  所有族必须经过 RSI gate + 套件验证

目的:
  将交易数量恢复到 60-120 笔 / 30D 区间
  验证重开族的实际胜率 ≥ 74%

监控指标:
  总交易数量
  各信号族的实际胜率
  整体 win rate 变化趋势
  日内最大回撤
```

### Phase 4：TP/SL 优化上线（10-14 天）

```
上线范围:
  分套件的新 TP/SL 配置
  RSI 动态止损调整
  RSI 极值紧急平仓

目的:
  修复胜负比不对称问题
  将 avg_win/avg_loss 提升到 ≥ 1.1

监控指标:
  avg_win / avg_loss 比值
  Profit factor
  Max drawdown 是否收窄
  收益曲线平滑性
```

### Phase 5：全量调优（14-21 天）

```
上线范围:
  根据 Phase 1-4 数据调整 RSI 阈值
  调整套件切换触发条件
  调整频率控制参数

目的:
  将系统调整到接近目标操作区间
  最终验证 30D 指标

验收标准（Phase 5 结束）:
  Win rate ≥ 77%（目标 80%，允许 3pp 偏差）
  Profit factor ≥ 1.3
  Max drawdown ≤ 8%
  Trade count in [40, 130]
  30D return ≥ 30%（目标 50%，分阶段达成）
```

---

## 16. 监控与告警指标

### 16.1 新增监控指标

```
RSI 相关:
  rsi_gate_block_rate         每小时 RSI gate 拦截率
  rsi_extreme_exit_count      RSI 极值触发平仓次数
  rsi_dynamic_sl_trigger_count RSI 动态 SL 调整次数
  avg_rsi_at_entry_long       做多入场时的平均 RSI_1h
  avg_rsi_at_entry_short      做空入场时的平均 RSI_1h

套件相关:
  active_suite_RSI_MRV_pct    RSI_MRV 套件占比（%）
  active_suite_BOLL_MBV_pct   BOLL_MBV 套件占比（%）
  suite_switch_count_24h      24h 内套件切换次数
  regime_TRENDING_pct         趋势市场时间占比
  regime_RANGING_pct          震荡市场时间占比
  regime_VOLATILE_pct         极度波动时间占比

质量相关:
  avg_suite_score_at_entry    入场时平均套件评分
  suite_score_vs_winrate_corr 套件分与胜率的相关性
```

### 16.2 告警阈值

```yaml
alerts:
  # RSI gate 拦截率异常（可能 RSI 计算有误）
  rsi_gate_block_rate_too_high:
    threshold: 0.85
    window_hours: 4
    action: "ALERT + 人工检查 RSI 计算"

  rsi_gate_block_rate_too_low:
    threshold: 0.05
    window_hours: 4
    action: "ALERT + 检查 RSI gate 是否生效"

  # 套件切换过于频繁
  suite_switch_too_frequent:
    threshold: 8   # 24h 内超过 8 次
    action: "ALERT + 增加切换冷却期"

  # 套件分低于门槛时仍然入场（代码 bug 检测）
  entry_below_suite_min_score:
    threshold: 0   # 不允许任何此类入场
    action: "CRITICAL + 立即暂停"

  # RSI 极值仍然入场
  long_entry_with_rsi_above_75:
    threshold: 0
    action: "CRITICAL + 检查 RSI veto 逻辑"

  short_entry_with_rsi_below_25:
    threshold: 0
    action: "CRITICAL + 检查 RSI veto 逻辑"
```

### 16.3 日报指标面板

```
每日汇报应包含:

[ RSI 状态摘要 ]
  - 当日各 symbol 的 RSI_1h 分布（均值、P25、P75）
  - RSI gate 拦截情况（拦截数 / 总信号数）
  - RSI 背离信号数（多头背离 / 空头背离）

[ 套件使用摘要 ]
  - 当日 RSI_MRV 使用次数 vs BOLL_MBV 使用次数
  - 各套件的当日胜率
  - 套件切换时间点和触发原因

[ 交易质量摘要 ]
  - 当日交易数、胜率、avg_win、avg_loss
  - Profit factor
  - 最大单笔亏损
  - 连续亏损次数
```

---

## 17. 已知风险与缓解

### 17.1 RSI 滞后性风险

```
风险: RSI 是滞后指标，在快速行情中可能错过入场或延迟止损

缓解方案:
  1. 15m RSI 用最短周期 (7) 减少滞后
  2. RSI gate 使用多时间框架交叉确认，避免单一时间框架误判
  3. 对 flip 类信号给予 RSI 豁免范围（±5 点），允许翻转初期略微偏离
  4. 极端行情（ATR_pct > 0.025）切换到 VOLATILE 模式，不依赖 RSI 决策，直接停止入场
```

### 17.2 套件切换时机风险

```
风险: ADX 在阈值附近震荡时，套件频繁切换导致信号不稳定

缓解方案:
  1. 套件切换设置 4 根 1h K 线冷却期
  2. ADX 阈值设置迟滞区间（25 进入趋势，22 退出趋势）
  3. 切换时不强制平仓已有仓位，仅影响新开仓
  4. 监控套件切换频率，超过 8 次/24h 告警
```

### 17.3 BOLL 参数固化风险

```
风险: 不同 symbol 的 BOLL 参数差异较大，统一参数可能导致误判

缓解方案:
  1. BOLL 带宽使用相对值（当前带宽 / 历史均值），而非绝对值
  2. 各 symbol 的 BOLL 均值使用最近 20 根 4h K 线计算，动态更新
  3. 对 BOLL 结构特别异常的 symbol（极高/极低波动）设置 symbol 级别豁免
```

### 17.4 RSI 背离误判风险

```
风险: 背离信号在趋势延续行情中会持续出现误判（价格一直创新高，RSI 一直落后）

缓解方案:
  1. 背离检测要求价格差异 > 0.5%（min_price_diff_pct = 0.005）
  2. 背离检测要求 RSI 差异 > 3 点（min_rsi_diff = 3.0）
  3. 背离仅作为加分项（+0.05 权重），不单独作为入场信号
  4. 强趋势中（ADX > 35）禁用背离加分，只用趋势跟踪逻辑
```

### 17.5 Long 端风险未完全解决

```
风险: Long 端历史胜率仅 53.16%，是系统最弱点
      RSI 过滤能提升质量，但市场偏向（2026-03 周期偏空头）会持续影响多头

缓解方案:
  1. Long 端 RSI 门控比 Short 端更严（long_rsi_4h_min: 45 vs short: 45）
  2. Long 端额外要求价格站上 VWAP_1h
  3. Long 端仓位上限从 22% 降至 18%（direct config 调整）
  4. Long 端启用更严格的连续亏损停止（Long 连续亏 2 笔 → 暂停 Long 4 小时）
```

### 17.6 参数过拟合风险

```
风险: 基于 2026-03 的 30D 窗口调参，可能过拟合该市场环境

缓解方案:
  1. 核心 RSI 阈值（超买 70 / 超卖 30）保持行业标准，不基于历史数据调整
  2. 套件切换阈值（ADX 25/22）保持通用标准
  3. 仅对评分权重进行微调（±0.05 幅度）
  4. 上线后每 7 天对比新的市场环境，评估参数有效性
  5. A/B 测试：保留旧 config 在沙盒运行，对比差异
```

---

## 18. 附录 A：RSI 参数敏感性分析

### A.1 RSI 周期选择

```
周期          延迟(根K线)  超买/超卖频率  适合场景
─────────────────────────────────────────────────
7 (短期)      3-4 根      高（频繁触发）  15m 入场时机
9             4-5 根      中高           日内交易
14 (标准)     6-8 根      中             1h 确认（标准配置）
21 (长期)     10-12 根    低             4h 趋势健康度
```

### A.2 RSI 阈值灵敏度

```
做多阈值 (rsi_1h_min)    预期影响
─────────────────────────────────────────────────
42  (最宽松)             允许更多信号通过，胜率约降 3pp
45  (当前推荐)           平衡点
48  (偏严)               减少约 15% 信号，胜率约升 2pp
50  (严格)               减少约 25% 信号，胜率约升 4pp

建议: 初始上线用 45，观察 2 周后根据实际胜率调整
```

### A.3 套件切换 ADX 阈值灵敏度

```
ADX 进入趋势阈值    效果
───────────────────────────────────────────
22                 频繁切换，对震荡市敏感
25 (推荐)          平衡点
28                 趋势判断保守，RSI_MRV 使用率降低

ADX 退出趋势阈值    效果
───────────────────────────────────────────
20                 快速退出趋势，迟滞区间大
22 (推荐)          标准迟滞区间
24                 迟滞区间小，容易在过渡区摇摆
```

---

## 19. 附录 B：双套件绩效对比表

### B.1 理论绩效对比

```
指标                      RSI_MRV 套件      BOLL_MBV 套件
──────────────────────────────────────────────────────────
适合市场类型              趋势型            震荡型
入场频率                  中（每市场1-2次）  低（每市场0.5-1次）
预期胜率                  74-80%            76-84%
预期持仓时间              45-90 min         30-60 min
预期 avg_win              +14-18            +11-14
预期 avg_loss             -13-16            -10-13
预期 win/loss 比          1.0-1.2           1.1-1.3
最强信号族                flip_bullish/bearish green_bar_growing(边界)
弱点                      高 ADX 急跌急涨   BOLL 扩张期失效
```

### B.2 套件切换对绩效的影响估算

```
市场时间占比（基于历史）:
  TRENDING: 约 35%
  RANGING:  约 50%
  VOLATILE: 约 15%

30D 下的套件使用分布估算:
  RSI_MRV 套件主导: 约 35% × 30D = 10.5 天
  BOLL_MBV 套件主导: 约 50% × 30D = 15 天
  无信号（VOLATILE）: 约 15% × 30D = 4.5 天

加权预期胜率:
  0.35/(0.35+0.50) × 77% + 0.50/(0.35+0.50) × 80%
  = 0.41 × 77% + 0.59 × 80%
  = 31.6% + 47.2%
  ≈ 78.8%

注: 接近 80% 目标，但需要 BOLL_MBV 实际达到 80%
```

---

## 20. 附录 C：完整伪代码参考实现

### C.1 主流程完整伪代码

```python
# 完整的双套件信号分析主流程

def process_signal_for_symbol(symbol: str, flow_context: dict) -> TradeDecision:
    """
    完整信号处理流程（双套件版本）
    """

    # ─────────────────────────────────────────
    # Stage 0: 市场数据准备
    # ─────────────────────────────────────────
    enhanced_context = bot._build_enhanced_flow_context(symbol, flow_context)

    # ─────────────────────────────────────────
    # Stage 1: MACD 基础信号生成
    # ─────────────────────────────────────────
    macd_result = macd_engine._compute_macd_signals(symbol, enhanced_context)

    if macd_result["direction"] == "neutral":
        return TradeDecision(action="HOLD", reason="MACD_NEUTRAL")

    direction = macd_result["direction"]
    signal_type_1h = macd_result["signal_type_1h"]

    # ─────────────────────────────────────────
    # Stage 2: 信号族白名单
    # ─────────────────────────────────────────
    if signal_type_1h in DISABLED_FAMILIES:
        return TradeDecision(action="HOLD", reason=f"FAMILY_DISABLED:{signal_type_1h}")

    # ─────────────────────────────────────────
    # Stage 3: 市场状态分类
    # ─────────────────────────────────────────
    regime = classifier.classify(
        symbol=symbol,
        adx_1h=enhanced_context["adx_1h"],
        atr_pct_1h=enhanced_context["atr_pct_1h"],
        boll_bandwidth_4h=enhanced_context["boll_bandwidth_4h"],
        boll_bandwidth_4h_mean=enhanced_context["boll_bandwidth_4h_mean"],
        macd_histogram_4h=enhanced_context["macd_histogram_4h"],
        macd_histogram_4h_prev=enhanced_context.get("macd_histogram_4h_prev", 0),
    )

    if regime == MarketRegimeClassifier.REGIME_VOLATILE:
        return TradeDecision(action="HOLD", reason="VOLATILE_REGIME")

    selected_suite = classifier.select_suite(regime)

    # ─────────────────────────────────────────
    # Stage 4: 套件分析
    # ─────────────────────────────────────────
    if selected_suite == "RSI_MRV":

        suite_result = rsi_mrv_analyzer.analyze(
            symbol=symbol,
            direction=direction,
            signal_type_1h=signal_type_1h,
            candles_4h=enhanced_context["candles_4h"],
            candles_1h=enhanced_context["candles_1h"],
            candles_15m=enhanced_context["candles_15m"],
            macd_histogram_4h=enhanced_context["macd_histogram_4h"],
            macd_histogram_1h=enhanced_context["macd_histogram_1h"],
            vwap_score=enhanced_context["vwap_score"],
            volume_ratio=enhanced_context["volume_ratio"],
        )

        # RSI gate 检查
        if not suite_result["rsi_gate_pass"]:
            log_block(symbol, "RSI_GATE_FAIL", suite_result)
            return TradeDecision(action="HOLD", reason="RSI_GATE_FAIL")

        # 评分门槛
        if suite_result["suite_score"] < MIN_SCORE_RSI_MRV:
            return TradeDecision(action="HOLD", reason="RSI_MRV_SCORE_LOW")

    elif selected_suite == "BOLL_MBV":

        suite_result = boll_mbv_analyzer.analyze(
            symbol=symbol,
            direction=direction,
            current_price=enhanced_context["current_price"],
            boll_upper_4h=enhanced_context["boll_upper_4h"],
            boll_lower_4h=enhanced_context["boll_lower_4h"],
            boll_mid_4h=enhanced_context["boll_mid_4h"],
            boll_bandwidth_4h=enhanced_context["boll_bandwidth_4h"],
            boll_bandwidth_4h_mean=enhanced_context["boll_bandwidth_4h_mean"],
            macd_histogram_4h=enhanced_context["macd_histogram_4h"],
            macd_histogram_1h=enhanced_context["macd_histogram_1h"],
            vwap_score=enhanced_context["vwap_score"],
            volume_ratio=enhanced_context["volume_ratio"],
        )

        # BOLL 状态检查
        if not suite_result["entry_allowed"]:
            return TradeDecision(
                action="HOLD",
                reason=f"BOLL_STATE:{suite_result['bandwidth_state']}"
            )

        # 评分门槛
        if suite_result["suite_score"] < MIN_SCORE_BOLL_MBV:
            return TradeDecision(action="HOLD", reason="BOLL_MBV_SCORE_LOW")

    # ─────────────────────────────────────────
    # Stage 5: 白名单细化检查
    # ─────────────────────────────────────────
    whitelist_check = _check_signal_family_whitelist(
        signal_type_1h=signal_type_1h,
        selected_suite=selected_suite,
        market_data=enhanced_context,
        direction=direction,
    )

    if not whitelist_check["allowed"]:
        return TradeDecision(action="HOLD", reason=whitelist_check["reason"])

    # ─────────────────────────────────────────
    # Stage 6: 频率控制检查
    # ─────────────────────────────────────────
    if not bot._check_frequency_control(symbol, direction, selected_suite):
        return TradeDecision(action="HOLD", reason="FREQUENCY_LIMIT")

    # ─────────────────────────────────────────
    # Stage 7: Pretrade RSI 极值否决
    # ─────────────────────────────────────────
    if selected_suite == "RSI_MRV" and "rsi_1h" in suite_result:
        rsi_veto = pretrade_gate.pretrade_rsi_veto(
            symbol=symbol,
            direction=direction,
            rsi_1h=suite_result["rsi_1h"],
            rsi_config=config["rsi_config"],
        )
        if rsi_veto["veto"]:
            return TradeDecision(action="HOLD", reason=rsi_veto["reason"])

    # ─────────────────────────────────────────
    # Stage 8: 原有门控链（L1/L2/L3 + AI + Capacity）
    # ─────────────────────────────────────────
    # [保持现有逻辑，不在本文档重复]

    # ─────────────────────────────────────────
    # Stage 9: 融合评分与最终决策
    # ─────────────────────────────────────────
    macd_base_score = macd_result["signal_score"]
    suite_score = suite_result["suite_score"]
    final_score = macd_base_score * 0.6 + suite_score * 0.4

    # 确定套件对应的 TP/SL 参数
    risk_params = get_risk_params_for_suite(selected_suite)

    return TradeDecision(
        action="BUY" if direction == "long" else "SELL",
        score=final_score,
        suite=selected_suite,
        regime=regime,
        signal_type=signal_type_1h,
        risk_params=risk_params,
        metadata={
            "rsi_4h":  suite_result.get("rsi_4h"),
            "rsi_1h":  suite_result.get("rsi_1h"),
            "rsi_15m": suite_result.get("rsi_15m"),
            "boll_pos": suite_result.get("boll_pos_score"),
            "bandwidth_state": suite_result.get("bandwidth_state"),
            "divergence": suite_result.get("divergence"),
        }
    )
```

### C.2 Post-open RSI 监控循环伪代码

```python
# 每 5 分钟执行的仓位 RSI 监控

async def monitor_open_positions_rsi():
    """
    对所有开放仓位执行 RSI 状态监控
    触发动态止损调整和紧急平仓
    """
    for position in open_positions:
        symbol = position.symbol
        direction = position.side

        # 获取最新 RSI_1h
        candles_1h = await data_provider.get_candles(symbol, "1h", limit=30)
        rsi_1h = rsi_indicator.get_rsi_for_timeframe(symbol, "1h", candles_1h)

        if math.isnan(rsi_1h):
            continue

        # 紧急平仓检查
        exit_cfg = config["rsi_config"]["rsi_emergency_exit"]
        if exit_cfg["enabled"]:
            if (direction == "long" and rsi_1h > exit_cfg["long_exit_trigger_rsi"]):
                logger.warning(f"[{symbol}] RSI emergency exit: RSI_1h={rsi_1h:.1f}")
                await close_position_partial(
                    symbol, exit_cfg["exit_portion_on_trigger"], reason="RSI_EXTREME_OVERBOUGHT"
                )
                continue

            if (direction == "short" and rsi_1h < exit_cfg["short_exit_trigger_rsi"]):
                logger.warning(f"[{symbol}] RSI emergency exit: RSI_1h={rsi_1h:.1f}")
                await close_position_partial(
                    symbol, exit_cfg["exit_portion_on_trigger"], reason="RSI_EXTREME_OVERSOLD"
                )
                continue

        # 动态止损调整
        sl_actions = decision_engine._apply_rsi_dynamic_sl_adjustment(
            symbol=symbol,
            position=position,
            rsi_1h=rsi_1h,
            rsi_config=config["rsi_config"],
        )

        if sl_actions.get("partial_close_pct"):
            logger.info(f"[{symbol}] RSI dynamic partial close: {sl_actions}")
            await close_position_partial(
                symbol, sl_actions["partial_close_pct"], reason=sl_actions["reason"]
            )

        elif sl_actions.get("move_sl_to_breakeven"):
            logger.info(f"[{symbol}] RSI move SL to breakeven: {sl_actions}")
            await update_stop_loss_to_breakeven(symbol, reason=sl_actions["reason"])

        # 背离检测（做多仓位检测看空背离，做空仓位检测看多背离）
        divergence = rsi_indicator.detect_divergence(candles_1h, "1h", direction)

        # 逆向背离：持续 5 根 K 线以上，考虑平仓
        if direction == "long" and divergence["regular_bearish"]:
            bars_since_entry = calculate_bars_since_entry(position)
            if bars_since_entry >= 5:
                logger.info(f"[{symbol}] Bearish divergence against LONG, closing 50%")
                await close_position_partial(symbol, 0.50, reason="BEARISH_DIVERGENCE_AGAINST_LONG")

        elif direction == "short" and divergence["regular_bullish"]:
            bars_since_entry = calculate_bars_since_entry(position)
            if bars_since_entry >= 5:
                logger.info(f"[{symbol}] Bullish divergence against SHORT, closing 50%")
                await close_position_partial(symbol, 0.50, reason="BULLISH_DIVERGENCE_AGAINST_SHORT")
```

---

## 总结与行动项

### 🔴 立即执行（P1）

```
1. 实现 RSIIndicator 类（rsi_indicator.py）
   → 目标: 计算准确性验证通过
   → 时间: 1 天

2. 实现 MarketRegimeClassifier（market_regime_classifier.py）
   → 目标: 正确分类 2026-03 窗口内的市场状态
   → 时间: 1 天

3. 更新 config 新增 rsi_config + indicator_suite_config 字段
   → 目标: 零破坏性，现有逻辑不变
   → 时间: 0.5 天
```

### 🟡 本周执行（P2）

```
4. 实现 RSIMRVAnalyzer + BOLLMBVAnalyzer
   → 目标: 回测验证两套评分系统正确工作
   → 时间: 2 天

5. 实现 AdaptiveSuiteSelector
   → 目标: 套件切换逻辑验证
   → 时间: 1 天

6. 集成到 macd_strategy_v2.py + decision_engine.py
   → 目标: 完整流程 End-to-End 测试通过
   → 时间: 2 天
```

### 🟢 下周执行（P3）

```
7. 运行 2026-03-02 → 2026-04-01 完整回测
   → 验证: 交易数 60-120，胜率 ≥ 74%，无 shrink 族入场

8. 根据回测结果微调 RSI 阈值
   → 目标: 胜率达到 77%+

9. 沙盒实盘验证（7 天）
   → 监控所有新增指标

10. Phase 1 正式上线
```

---

**文档结束**

*本文档版本: v2.0-alpha | 创建: 2026-04-12 | 状态: 待实现*

---

> **重要提醒**: 本文档中的所有回测预期均为基于历史数据的理论估算，不代表实盘保证。RSI 参数应在上线后根据实际数据持续校准。shrink 族（red_bar_shrinking / green_bar_shrinking）的永久禁用决策是基于负 alpha 归因分析，不应因市场环境变化而轻易解禁。
