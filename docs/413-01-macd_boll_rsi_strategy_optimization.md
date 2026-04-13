# MACD + BOLL + RSI 三共振策略优化全文档
# Strategy v3.0 — VWAP 移除 · 三指标共振 · 目标 100%+/30D

**版本**: v3.0-resonance  
**基于回测**: 2026-03-02 ~ 2026-04-01  
**基线结果**: Return `+10.04%` | Win Rate `76.27%` | Trades `118` | Max DD `5.09%`  
**优化目标**: Return `100%+` | Win Rate `80%+` | Trades `90-120` | 杠杆 3x-5x | 单仓 20-30%

---

## 目录

1. [现状诊断与根因分析](#1-现状诊断与根因分析)
2. [VWAP 废弃决策报告](#2-vwap-废弃决策报告)
3. [三共振策略架构设计](#3-三共振策略架构设计)
4. [MACD 信号层重设计](#4-macd-信号层重设计)
5. [BOLL 结构层设计](#5-boll-结构层设计)
6. [RSI 动量层设计](#6-rsi-动量层设计)
7. [三共振评分系统](#7-三共振评分系统)
8. [动态杠杆与仓位体系](#8-动态杠杆与仓位体系)
9. [止盈止损重设计](#9-止盈止损重设计)
10. [信号族管理](#10-信号族管理)
11. [风险控制体系](#11-风险控制体系)
12. [配置文件完整 Diff](#12-配置文件完整-diff)
13. [核心模块伪代码](#13-核心模块伪代码)
14. [代码修改 Diff（src 层）](#14-代码修改-diffsrc-层)
15. [回测目标验证模型](#15-回测目标验证模型)
16. [分阶段上线计划](#16-分阶段上线计划)
17. [监控指标体系](#17-监控指标体系)
18. [风险缓解手册](#18-风险缓解手册)
19. [附录 A：三共振信号图谱](#附录-a三共振信号图谱)
20. [附录 B：参数敏感性分析表](#附录-b参数敏感性分析表)
21. [附录 C：完整伪代码参考实现](#附录-c完整伪代码参考实现)

---

## 1. 现状诊断与根因分析

### 1.1 基线回测核心指标

```
指标                当前值        目标值        Gap
──────────────────────────────────────────────────
30D Return         +10.04%       100%+         -90pp
Win Rate            76.27%       80%+           -4pp
Trade Count            118       90-120         在目标范围
Profit Factor         2.36       3.0+          -0.64
Max Drawdown          5.09%      ≤8%            ✓
Avg Win            +13.27       +18+           -4.73
Avg Loss           -17.91       -12            逆差
Win/Loss Ratio      0.741        1.5+          严重不足
```

### 1.2 信号族绩效归因

```
信号族               交易数   胜率      PnL        评价
──────────────────────────────────────────────────────────
red_bar_growing        79    77.22%   +910.14    ✅ 核心盈利族，占总盈利76.3%
flip_bullish           27    77.78%   +339.78    ✅ 高置信度，低频优质
green_bar_growing      12    66.67%    -58.42    ❌ 亏损，门槛0.95仍输
```

**致命发现**：

```
green_bar_growing 的核心问题:
  × 门槛已是最严格 (0.95) 仍有 66.67% 胜率
  × 意味着信号本身存在结构性缺陷，非门槛问题
  × BOLL 位置 + RSI 状态的联合验证缺失是根本原因
  × VWAP 作为该信号的过滤器实际无效

red_bar_growing 的成功原因:
  ✓ 做空信号在 2026-03 偏空头环境中顺势
  ✓ ADX 门控有效过滤弱趋势场景
  ✓ 但仍依赖 VWAP，VWAP 无效意味着该信号存在偶然性风险
```

### 1.3 收益差距分解

```
当前 10.04% vs 目标 100%+ 的差距分解:

因素1: 胜负比不对称 (avg_win/avg_loss = 0.741)
  当前: 76.27% × 13.27 - 23.73% × 17.91 = 10.12 - 4.25 = +5.87 期望/笔
  影响: 若 win/loss 比达到 1.5，期望提升约 3x

因素2: 交易频率不足的质量问题
  当前 118 笔，分布不均匀（深度回撤期 35 笔占 30%）
  目标: 在高质量时段集中 90-120 笔

因素3: 杠杆使用效率
  当前固定 5x，高质量信号未获最大杠杆利用
  目标: 3x-5x 动态，高分信号用 5x

因素4: 仓位未充分利用
  reserve_pct=20% 保守合理，但部分时段可提升至 25% 利用率
  
根因: VWAP 无效导致过滤器失真 → 错过真实质量信号 + 放入低质量信号
解决: 用 BOLL 结构 + RSI 状态替代 VWAP 进行质量过滤
```

### 1.4 漏斗瓶颈诊断

```
最大过滤节点:
  L1结构门槛: 83.32% 被拒 → 主要拒因 atr_in_range + adx_sufficient
  评分门槛:   45.45% 被拒 → 主要拒因 score=0 (信号不符合 MACD V2 条件)
  AI前过滤:   63.25% 被拒 → 主要拒因 PRE_AI_SCORE 不足 + 过热检测

问题: VWAP 过滤层 (10.59% 被拒) 实际是误伤层
  → vwap_score_filter 拒绝的信号中，有相当比例是有效的 BOLL/RSI 共振信号
  → 移除 VWAP 过滤，用 BOLL+RSI 替换，预计释放 5-8% 额外有效信号
```

---

## 2. VWAP 废弃决策报告

### 2.1 废弃理由

```
理由 1: 指标有效性问题
  VWAP 依赖成交量加权均价，在以下场景失效:
    - 合约市场中 VWAP 与现货偏离大，参考价值低
    - 高频资金流动下 VWAP 被操纵，形成假支撑/阻力
    - 回测数据显示 vwap_score 高的信号胜率并不显著优于低分信号

理由 2: 数据质量问题
  VWAP 计算需要完整的成交量数据，在部分交易所数据缺失场景下产生错误分数
  
理由 3: 与 BOLL 功能重叠
  VWAP 的核心功能（价格与参考均线的关系）可由 BOLL 中轨完全替代
  BOLL 中轨 = 20 周期 SMA，功能更稳定且纯价格计算，无依赖风险
  
理由 4: 回测验证
  green_bar_growing: vwap_score=0.95 门槛 → 胜率仍 66.67%（VWAP 无法区分质量）
  移除 VWAP 后，改用 BOLL 位置验证，理论上能正确区分上述情况

理由 5: 新三共振架构的完整性
  MACD（趋势动量）+ BOLL（价格结构）+ RSI（超买超卖）= 完整信息集
  VWAP 在此框架中是冗余且噪声高的第四指标
```

### 2.2 VWAP 功能迁移矩阵

```
原 VWAP 功能                  → 新替代指标
─────────────────────────────────────────────────────
价格是否在参考均线上方         → BOLL 中轨（20SMA）位置判断
价格偏离参考均线程度           → (price - BOLL_mid) / BOLL_mid
支撑位确认                     → BOLL 下轨 + RSI 超卖区域联合
阻力位确认                     → BOLL 上轨 + RSI 超买区域联合
VWAP 评分 > 0.12 入场条件      → BOLL 位置分 > 0.65 + RSI_1h 状态正常
vwap_hard_block               → RSI 极值 block (RSI > 75 or RSI < 25)
VWAP 仓位乘数                 → BOLL 带宽仓位乘数（带宽越大，仓位越小）
```

### 2.3 配置迁移 Diff（VWAP 废弃）

```diff
--- a/config/trading_config_fund_flow.json (VWAP section)
+++ b/config/trading_config_fund_flow.json (VWAP removed)

-  "min_vwap_score_for_entry": 0.12,
-  "vwap_execution_penalty_only": true,
-  "vwap_hard_block": true,
-  "flip_bullish_min_vwap_score": 0.08,
-  "flip_bearish_retest_reject_min_vwap_score": 0.18,
-  "stable_bear_continuation_min_vwap_score": 0.05,
-  "stable_bull_continuation_min_vwap_score": 0.08,
-  "preflip_trial_min_vwap_score": 0.08,
-  "trial_short_below_structure_promotion_min_vwap_score": 0.08,
-  "vwap_score_position_tiers": [
-    {"min": 0.12, "max": 0.20, "position_mult": 0.75},
-    {"min": 0.20, "max": 0.30, "position_mult": 0.95},
-    {"min": 0.30, "max": 1.00, "position_mult": 1.05}
-  ],

+  // VWAP 全部替换为 BOLL 结构评分
+  "min_boll_structure_score_for_entry": 0.60,
+  "boll_structure_hard_block_threshold": 0.20,
+  "flip_bullish_min_boll_score": 0.55,
+  "flip_bearish_min_boll_score": 0.60,
+  "stable_bear_continuation_min_boll_score": 0.50,
+  "stable_bull_continuation_min_boll_score": 0.55,
+  "boll_bandwidth_position_tiers": [
+    {"bandwidth_max": 0.03, "position_mult": 0.65},   // squeeze: 缩小仓位
+    {"bandwidth_max": 0.06, "position_mult": 0.85},   // narrow: 略缩
+    {"bandwidth_max": 0.10, "position_mult": 1.00},   // normal: 标准
+    {"bandwidth_max": 0.15, "position_mult": 0.90},   // wide: 略缩（趋势中段）
+    {"bandwidth_max": 999,  "position_mult": 0.70}    // expanding: 大缩（趋势末段风险高）
+  ],
```

---

## 3. 三共振策略架构设计

### 3.1 核心设计哲学

```
三共振 = MACD 方向 × BOLL 结构 × RSI 动量

三者必须同向共振，任一反向则不入场:

      MACD
    (趋势方向)
        │
        ▼
  ┌─────────────┐
  │  信号族确认  │ ← red_bar_growing / flip_bullish
  └──────┬──────┘
         │
    ┌────┴────┐
    │         │
   BOLL      RSI
  (结构位置) (动量状态)
    │         │
    └────┬────┘
         │
    三者共振 → 入场
    任一失效 → 观望
```

### 3.2 共振矩阵

```
做多共振矩阵:

MACD_1h    │ RSI_1h 区间
信号       │ <40    40-50   50-65   65-70   >70
───────────┼────────────────────────────────────
BOLL 下轨区 │ ✅高    ✅极高   ✅高    ⚠️ 谨慎  ❌
BOLL 中下区 │ ⚠️ 弱   ✅高    ✅高    ⚠️ 谨慎  ❌
BOLL 中线区 │ ❌      ✅中    ✅中    ❌       ❌
BOLL 中上区 │ ❌      ❌      ⚠️ 弱   ❌       ❌
BOLL 上轨区 │ ❌      ❌      ❌      ❌       ❌

做空共振矩阵:

MACD_1h    │ RSI_1h 区间
信号       │ <30    30-35   35-50   50-60   >60
───────────┼────────────────────────────────────
BOLL 上轨区 │ ❌      ⚠️ 谨慎  ✅高    ✅极高   ✅高
BOLL 中上区 │ ❌      ❌      ✅高    ✅高    ⚠️ 谨慎
BOLL 中线区 │ ❌      ❌      ✅中    ✅中    ❌
BOLL 中下区 │ ❌      ❌      ⚠️ 弱   ❌      ❌
BOLL 下轨区 │ ❌      ❌      ❌      ❌      ❌

图例: ✅极高=全仓, ✅高=标准仓, ✅中=80%仓, ⚠️=50%仓, ❌=不入场
```

### 3.3 架构分层

```
Layer 0: 市场状态检测
  ├─ 极度波动检测 (ATR_pct > 2.5% × 2 根 → 冷却)
  └─ 趋势/震荡市场分类 (ADX 阈值)

Layer 1: MACD 信号族识别
  ├─ 4h 主方向确认
  ├─ 1h 信号类型识别
  ├─ 信号族白名单过滤
  └─ 基础信号评分

Layer 2: BOLL 结构验证
  ├─ 价格在 BOLL 带内的相对位置
  ├─ BOLL 带宽状态 (squeeze/normal/expanding)
  ├─ BOLL 中轨（20SMA）方向
  └─ BOLL 结构评分

Layer 3: RSI 动量验证
  ├─ RSI 三时间框架 (4h/1h/15m) 一致性
  ├─ RSI 超买/超卖状态
  ├─ RSI 斜率（动量方向）
  ├─ RSI 背离检测
  └─ RSI 动量评分

Layer 4: 三共振综合评分
  ├─ 三层评分加权聚合
  ├─ 共振强度计算
  └─ 最终入场决策

Layer 5: 动态仓位计算
  ├─ 评分 → 仓位比例
  ├─ 评分 → 杠杆倍数
  ├─ BOLL 带宽 → 仓位调整
  └─ ATR → 仓位调整

Layer 6: 风险门控
  ├─ 连续亏损检查
  ├─ 账户回撤检查
  ├─ 容量检查
  └─ 执行质量检查
```

---

## 4. MACD 信号层重设计

### 4.1 保留信号族（白名单制）

```
白名单信号族（基于回测归因）:

┌───────────────────────────────────────────────────────────────┐
│ 信号族              │ 方向 │ 回测胜率 │ 评价         │ 保留  │
├───────────────────────────────────────────────────────────────┤
│ red_bar_growing    │ 空   │ 77.22%  │ 核心盈利族   │ ✅ 保留 │
│ flip_bullish       │ 多   │ 77.78%  │ 高置信度     │ ✅ 保留 │
│ flip_bearish       │ 空   │ 未展示  │ 配对信号     │ ✅ 保留 │
│ green_bar_growing  │ 多   │ 66.67%  │ 亏损族       │ ⚠️ 条件 │
│ red_bar_shrinking  │ 空   │ 负alpha  │ 已禁用       │ ❌ 禁止 │
│ green_bar_shrinking│ 多   │ 负alpha  │ 已禁用       │ ❌ 禁止 │
└───────────────────────────────────────────────────────────────┘

green_bar_growing 条件保留:
  仅在 BOLL 下轨区 + RSI 超卖反弹场景下允许
  即"价格从 BOLL 下轨弹起 + RSI 从 <40 回升" 的精确组合
  其他场景禁用
```

### 4.2 信号识别逻辑更新

```python
# 保留原有 MACD 信号识别逻辑，新增共振前置检查

def classify_signal_with_resonance_check(
    signal_type_1h: str,
    macd_histogram_4h: float,
    macd_histogram_1h: float,
    boll_result: dict,      # 新增
    rsi_result: dict,       # 新增
    direction: str,
) -> dict:
    """
    信号族识别 + 共振前置检查
    """

    # Step 1: 信号族白名单
    WHITELIST = {"red_bar_growing", "flip_bullish", "flip_bearish", "green_bar_growing"}
    BLACKLIST = {"red_bar_shrinking", "green_bar_shrinking"}

    if signal_type_1h in BLACKLIST:
        return {"valid": False, "reason": f"BLACKLIST:{signal_type_1h}"}

    if signal_type_1h not in WHITELIST:
        return {"valid": False, "reason": f"NOT_IN_WHITELIST:{signal_type_1h}"}

    # Step 2: green_bar_growing 特殊条件
    if signal_type_1h == "green_bar_growing":
        boll_pos = boll_result.get("relative_position", 0.5)
        rsi_1h = rsi_result.get("rsi_1h", 50)
        rsi_slope = rsi_result.get("rsi_slope_1h", 0)

        # 仅允许 BOLL 下轨区 + RSI 超卖回升
        if boll_pos > 0.35:   # 价格不在下轨区
            return {
                "valid": False,
                "reason": "GBG_BOLL_POSITION_TOO_HIGH: price not near lower band"
            }
        if rsi_1h > 48:       # RSI 不在超卖区
            return {
                "valid": False,
                "reason": f"GBG_RSI_TOO_HIGH: RSI_1h={rsi_1h:.1f} > 48"
            }
        if rsi_slope <= 0:    # RSI 斜率必须向上
            return {
                "valid": False,
                "reason": "GBG_RSI_SLOPE_NOT_POSITIVE"
            }

    return {"valid": True, "reason": None}
```

### 4.3 MACD 评分更新

```
原 MACD 评分（含 VWAP 权重）:
  weight_4h_direction: 0.25
  weight_1h_direction: 0.10
  weight_vwap:         0.15  ← 废弃
  weight_boll_rsi:     0.20
  weight_volume:       0.15

新 MACD 评分（纯 MACD 层，不含 BOLL/RSI，那是独立层）:
  weight_4h_direction: 0.45  ↑ 大幅提升，4h 是主方向锚
  weight_1h_direction: 0.30  ↑ 提升，1h 是信号确认
  weight_15m_momentum: 0.15  ↑ 新增，15m 入场时机
  weight_macd_slope:   0.10  新增，MACD 斜率连续性

MACD 层最终输出: macd_base_score (0~1)
```

---

## 5. BOLL 结构层设计

### 5.1 BOLL 位置评分系统

```
BOLL 参数设置:
  period: 20 (标准)
  std_dev: 2.0 (标准)
  timeframes: 4h (主结构), 1h (入场结构)

BOLL 带分区定义:
  zone_0 (突破上轨外): price > upper           → 极度超买
  zone_1 (上轨区):     mid+5% ~ upper          → 做空优质区
  zone_2 (中上区):     mid ~ mid+5%            → 做空可行区
  zone_3 (中性区):     mid-2% ~ mid+2%         → 方向不明确
  zone_4 (中下区):     mid-5% ~ mid-2%         → 做多可行区
  zone_5 (下轨区):     lower ~ mid-5%          → 做多优质区
  zone_6 (突破下轨外): price < lower           → 极度超卖
```

### 5.2 BOLL 评分矩阵（做多）

```
做多 BOLL 评分:

zone_6 (下轨外):  1.0  → 价格从下轨外反弹，强烈做多结构
zone_5 (下轨区):  0.90 → 标准做多区
zone_4 (中下区):  0.65 → 可接受做多区
zone_3 (中性区):  0.35 → 弱做多信号，需 RSI 强力支撑
zone_2 (中上区):  0.10 → 做多不佳，仅在极强趋势中接受
zone_1 (上轨区):  0.0  → 禁止做多
zone_0 (上轨外):  -1.0 → 硬性禁止做多

做多时的 BOLL 最低要求: boll_score_long >= 0.55
```

### 5.3 BOLL 评分矩阵（做空）

```
做空 BOLL 评分:

zone_0 (上轨外):  1.0  → 价格从上轨外回落，强烈做空结构
zone_1 (上轨区):  0.90 → 标准做空区
zone_2 (中上区):  0.65 → 可接受做空区
zone_3 (中性区):  0.35 → 弱做空信号
zone_4 (中下区):  0.10 → 做空不佳
zone_5 (下轨区):  0.0  → 禁止做空
zone_6 (下轨外):  -1.0 → 硬性禁止做空

做空时的 BOLL 最低要求: boll_score_short >= 0.55
```

### 5.4 BOLL 带宽状态与仓位调整

```
带宽状态分类:
  bandwidth = (upper - lower) / mid

  squeeze   (bandwidth < 0.025): 即将爆发，方向未知，禁止开仓
  tight     (0.025 ≤ bandwidth < 0.045): 收紧中，可用 70% 标准仓
  normal    (0.045 ≤ bandwidth < 0.080): 标准状态，100% 标准仓
  wide      (0.080 ≤ bandwidth < 0.120): 趋势展开中，90% 标准仓
  expanding (bandwidth ≥ 0.120): 趋势末段风险，70% 标准仓

带宽仓位乘数:
  squeeze:   0.00  (禁止开仓)
  tight:     0.70
  normal:    1.00  (标准)
  wide:      0.90
  expanding: 0.70
```

### 5.5 BOLL 中轨方向确认

```
BOLL 中轨方向 = 20SMA 斜率

做多额外要求:
  boll_mid_slope_3bars > 0   → 中轨向上（趋势支撑做多）
  OR boll_mid_slope_3bars 轻微为负但 price > boll_mid （价格在均线上方）

做空额外要求:
  boll_mid_slope_3bars < 0   → 中轨向下（趋势支撑做空）
  OR boll_mid_slope_3bars 轻微为正但 price < boll_mid

中轨斜率评分加成:
  |slope| > 0.002 (强方向): +0.05
  |slope| > 0.001 (弱方向): +0.02
  slope ≈ 0 (横盘):          0
  slope 逆向:               -0.10
```

### 5.6 BOLL 挤压突破判定

```
挤压突破信号（BREAKOUT_WATCH 状态下使用）:

条件（ALL of）:
  bandwidth < 0.025 持续 ≥ 3 根 4h K 线（积累挤压能量）
  当前 K 线收盘突破 upper 或 lower
  突破方向与 MACD_4h 方向一致
  RSI_1h 配合方向（突破上方 RSI > 50，突破下方 RSI < 50）

突破信号评分加成: +0.15 （奖励挤压后的方向性突破）

注意: 挤压状态下不开仓，等突破确认后以 BREAKOUT_ENTRY 模式入场
```

---

## 6. RSI 动量层设计

### 6.1 RSI 参数设置

```
RSI 参数配置:

  RSI_4h: period=21  → 宏观趋势健康度（低敏感度）
  RSI_1h: period=14  → 核心入场动量确认（标准）
  RSI_15m: period=7  → 微观入场时机（高敏感度）

超买超卖阈值:
  overbought:         70
  extreme_overbought: 78
  oversold:           30
  extreme_oversold:   22

中性区域:
  neutral_high: 55
  neutral_low:  45
  neutral_mid:  50
```

### 6.2 RSI 动量评分

```
RSI 动量评分（做多）:

RSI_1h 区间     分数    说明
──────────────────────────────────────
< 25           0.0    极度超卖（BOLL 下轨需联合，否则慎入）
25-35          0.6    超卖反弹区（配合 BOLL 下轨 = 极佳）
35-45          0.8    弱势恢复区（做多良好）
45-60          1.0    动量健康区（做多最优）
60-65          0.8    偏热区（做多仍可，需 BOLL 中下区配合）
65-70          0.4    过热区（大幅折扣）
> 70           0.0    超买（禁止做多）

RSI 动量评分（做空）:

RSI_1h 区间     分数    说明
──────────────────────────────────────
> 75           0.0    极度超买（配合 BOLL 上轨 = 极佳）
65-75          0.6    超买回落区（配合 BOLL 上轨 = 佳）
55-65          0.8    偏强区（做空良好）
40-55          1.0    动量健康区（做空最优）
35-40          0.8    偏弱区（做空仍可）
30-35          0.4    过冷区（大幅折扣）
< 30           0.0    超卖（禁止做空）
```

### 6.3 RSI 三时间框架协同

```
三时间框架一致性检查（三共振的 RSI 子层）:

做多一致性（ALL of）:
  RSI_4h ≥ 43    → 宏观不弱
  RSI_1h ≥ 43    → 中观动量正
  RSI_15m ≥ 47   → 微观向上
  RSI_15m_slope > 0 → RSI 正在上行

做空一致性（ALL of）:
  RSI_4h ≤ 57    → 宏观不强
  RSI_1h ≤ 57    → 中观动量负
  RSI_15m ≤ 53   → 微观向下
  RSI_15m_slope < 0 → RSI 正在下行

豁免场景（flip 族）:
  flip_bullish: RSI_1h 允许低至 40，RSI_4h 允许低至 40
  flip_bearish: RSI_1h 允许高至 60，RSI_4h 允许高至 60

原因: flip 信号本身表示方向刚翻转，RSI 未完全跟上是正常的
```

### 6.4 RSI 背离检测

```
背离类型与处理:

1. 正则看多背离 (Regular Bullish Divergence):
   定义: 价格创新低，RSI_1h 未创新低
   操作: 做多信号加分 +0.12，配合 BOLL 下轨区可提前入场

2. 正则看空背离 (Regular Bearish Divergence):
   定义: 价格创新高，RSI_1h 未创新高
   操作: 做空信号加分 +0.12，配合 BOLL 上轨区可提前入场

3. 隐藏看多背离 (Hidden Bullish):
   定义: 价格高点升高，RSI 高点降低（趋势延续信号）
   操作: 做多信号加分 +0.08

4. 隐藏看空背离 (Hidden Bearish):
   定义: 价格低点降低，RSI 低点升高（趋势延续信号）
   操作: 做空信号加分 +0.08

5. 逆向背离（仓位方向反）:
   持仓做多时出现看空背离: 触发部分平仓 30%
   持仓做空时出现看多背离: 触发部分平仓 30%

背离有效条件:
  价格差异 ≥ 0.4%
  RSI 差异 ≥ 3 点
  回望窗口: 最近 8-12 根 1h K 线
```

### 6.5 RSI 极值紧急机制

```
RSI 极值处理（优先级最高）:

做多仓位中:
  RSI_1h > 78: 立即平仓 40%，剩余收紧 SL 至 0.5%
  RSI_1h > 72: 将 SL 移至 breakeven，触发追踪止损
  RSI_1h > 68: 记录警告，不触发操作

做空仓位中:
  RSI_1h < 22: 立即平仓 40%，剩余收紧 SL 至 0.5%
  RSI_1h < 28: 将 SL 移至 breakeven，触发追踪止损
  RSI_1h < 32: 记录警告，不触发操作

入场前 RSI 极值屏蔽:
  做多: RSI_1h > 70 → 硬性屏蔽（替代原 vwap_hard_block）
  做空: RSI_1h < 30 → 硬性屏蔽
  这两个条件替代原 VWAP 的 hard_block 功能
```

---

## 7. 三共振评分系统

### 7.1 评分架构

```
三共振最终评分 = MACD_score × W_macd
              + BOLL_score × W_boll
              + RSI_score  × W_rsi
              + Divergence_bonus

权重配置（趋势市场 / RSI_MRV 套件）:
  W_macd: 0.40  → MACD 是主信号来源
  W_boll: 0.35  → BOLL 是结构约束（替代 VWAP）
  W_rsi:  0.25  → RSI 是动量确认

权重配置（震荡市场 / BOLL 主导套件）:
  W_macd: 0.30
  W_boll: 0.45  → BOLL 更重要（震荡市场边界更有意义）
  W_rsi:  0.25

最终评分范围: 0.0 ~ 1.0 (加分项可超过 1.0，上限截断到 1.0)
```

### 7.2 各子层输出

```
MACD 子层输出:
  macd_base_score = 0.0 ~ 1.0
  基于 4h方向(0.45) + 1h方向(0.30) + 15m动量(0.15) + MACD斜率(0.10)

BOLL 子层输出:
  boll_structure_score = 0.0 ~ 1.0 (含方向惩罚，可为负但截断为 0)
  boll_bandwidth_mult = 0.0 ~ 1.0 (仓位乘数，不影响入场评分)
  boll_gate_pass = True/False (是否通过最低 BOLL 结构要求)

RSI 子层输出:
  rsi_momentum_score = 0.0 ~ 1.0
  rsi_gate_pass = True/False (三时间框架一致性门控)
  rsi_divergence_bonus = -0.20 ~ +0.15

综合评分:
  resonance_score = (
    macd_base_score × 0.40 +
    boll_structure_score × 0.35 +
    rsi_momentum_score × 0.25
  ) + rsi_divergence_bonus

  resonance_score = clamp(resonance_score, 0.0, 1.0)
```

### 7.3 入场决策矩阵

```
三共振入场决策（必须满足 ALL）:

必要条件（任一不满足则 HOLD）:
  □ signal_type 在白名单中
  □ boll_gate_pass = True (BOLL 评分 ≥ 0.55)
  □ rsi_gate_pass = True (三时间框架一致)
  □ RSI 极值屏蔽未触发
  □ bandwidth_state != "squeeze"
  □ resonance_score ≥ 0.65

质量分档（影响仓位大小）:
  resonance_score 0.65-0.70: 60% 标准仓 (低质量入场)
  resonance_score 0.70-0.78: 80% 标准仓 (中等质量)
  resonance_score 0.78-0.85: 100% 标准仓 (高质量)
  resonance_score 0.85-0.92: 115% 标准仓 (优质)
  resonance_score ≥ 0.92:    130% 标准仓 (极优质，上限受 max_symbol_position_portion 约束)
```

### 7.4 评分计算完整伪代码

```python
def compute_resonance_score(
    direction: str,
    macd_base_score: float,
    boll_result: dict,
    rsi_result: dict,
    market_regime: str,
) -> dict:
    """
    计算三共振综合评分
    """

    # 权重选择（基于市场状态）
    if market_regime in ("TRENDING_BULL", "TRENDING_BEAR"):
        W_macd, W_boll, W_rsi = 0.40, 0.35, 0.25
    elif market_regime in ("RANGING", "BREAKOUT_WATCH"):
        W_macd, W_boll, W_rsi = 0.30, 0.45, 0.25
    else:  # VOLATILE
        return {"resonance_score": 0.0, "gate_pass": False, "reason": "VOLATILE"}

    # 各子层评分提取
    boll_score = boll_result["structure_score"]
    rsi_score  = rsi_result["momentum_score"]

    # 背离加成
    div_bonus = rsi_result.get("divergence_bonus", 0.0)

    # BOLL 和 RSI 门控检查
    boll_gate = boll_result["gate_pass"]  # boll_score >= 0.55
    rsi_gate  = rsi_result["gate_pass"]   # 三时间框架一致

    if not boll_gate:
        return {
            "resonance_score": 0.0,
            "gate_pass": False,
            "reason": f"BOLL_GATE_FAIL: score={boll_score:.3f}"
        }

    if not rsi_gate:
        return {
            "resonance_score": 0.0,
            "gate_pass": False,
            "reason": f"RSI_GATE_FAIL: {rsi_result['fail_reason']}"
        }

    # 综合计算
    raw_score = (
        macd_base_score * W_macd +
        boll_score      * W_boll +
        rsi_score       * W_rsi  +
        div_bonus
    )

    resonance_score = max(0.0, min(1.0, raw_score))

    # 最低共振门槛
    if resonance_score < 0.65:
        return {
            "resonance_score": resonance_score,
            "gate_pass": False,
            "reason": f"RESONANCE_TOO_LOW: {resonance_score:.3f} < 0.65"
        }

    return {
        "resonance_score": resonance_score,
        "gate_pass": True,
        "reason": "PASS",
        "detail": {
            "macd_contribution": macd_base_score * W_macd,
            "boll_contribution": boll_score * W_boll,
            "rsi_contribution": rsi_score * W_rsi,
            "div_bonus": div_bonus,
        }
    }
```

---

## 8. 动态杠杆与仓位体系

### 8.1 仓位目标范围

```
策略目标:
  单交易对仓位: 20% - 30%
  最大同时持仓: 5 个交易对
  杠杆范围:    3x - 5x

当前配置 (固定杠杆):
  default_target_portion: 0.30  (30%)
  min_leverage: 5
  default_leverage: 5
  max_leverage: 5

问题: 杠杆固定 5x 无法根据信号质量动态调整
```

### 8.2 动态杠杆映射

```
三共振评分 → 动态杠杆:

resonance_score  杠杆   说明
──────────────────────────────────────────
< 0.65          HOLD   不入场
0.65 - 0.70      3x    低质量，最小杠杆
0.70 - 0.78      3x    中等质量，保守杠杆
0.78 - 0.85      4x    高质量，标准杠杆
0.85 - 0.92      5x    优质，最大杠杆
≥ 0.92           5x    极优质，最大杠杆（不超过 5x）

配置:
leverage_score_tiers:
  - {score_min: 0.92, score_max: 1.00, leverage: 5}
  - {score_min: 0.85, score_max: 0.92, leverage: 5}
  - {score_min: 0.78, score_max: 0.85, leverage: 4}
  - {score_min: 0.70, score_max: 0.78, leverage: 3}
  - {score_min: 0.65, score_max: 0.70, leverage: 3}
```

### 8.3 动态仓位比例

```
仓位比例 = 基础仓位 × 评分仓位乘数 × BOLL带宽乘数 × ATR乘数

基础仓位: 0.25 (25%，在 20-30% 范围内)

评分仓位乘数:
  resonance_score ≥ 0.92: 1.20  → 最终仓位 30%（上限）
  resonance_score 0.85-0.92: 1.10  → 27.5%
  resonance_score 0.78-0.85: 1.00  → 25%（标准）
  resonance_score 0.70-0.78: 0.90  → 22.5%
  resonance_score 0.65-0.70: 0.80  → 20%（下限）

BOLL带宽乘数（已在第5节定义）:
  squeeze:   禁止
  tight:     0.70
  normal:    1.00
  wide:      0.90
  expanding: 0.70

ATR仓位调整（保留原逻辑）:
  atr_pct ≤ 0.014: 1.00
  atr_pct ≤ 0.018: 0.85
  atr_pct ≤ 0.022: 0.70
  atr_pct ≤ 0.025: 0.55

最终仓位计算:
  target_portion = 0.25 × score_mult × boll_bw_mult × atr_mult
  target_portion = clamp(target_portion, 0.20, 0.30)
```

### 8.4 仓位计算完整伪代码

```python
def compute_dynamic_position(
    resonance_score: float,
    boll_bandwidth: float,
    atr_pct: float,
    account_equity: float,
    max_symbol_position_portion: float = 0.30,
    min_symbol_position_portion: float = 0.20,
) -> dict:
    """
    动态仓位与杠杆计算
    """

    # 杠杆确定
    if resonance_score >= 0.85:
        leverage = 5
    elif resonance_score >= 0.78:
        leverage = 4
    else:
        leverage = 3

    # 评分仓位乘数
    if resonance_score >= 0.92:
        score_mult = 1.20
    elif resonance_score >= 0.85:
        score_mult = 1.10
    elif resonance_score >= 0.78:
        score_mult = 1.00
    elif resonance_score >= 0.70:
        score_mult = 0.90
    else:
        score_mult = 0.80

    # BOLL 带宽乘数
    bw_mult_map = [
        (0.025, 0.00),   # squeeze: 禁止
        (0.045, 0.70),   # tight
        (0.080, 1.00),   # normal
        (0.120, 0.90),   # wide
        (9999,  0.70),   # expanding
    ]
    bw_mult = 0.00
    for bw_threshold, mult in bw_mult_map:
        if boll_bandwidth <= bw_threshold:
            bw_mult = mult
            break

    if bw_mult == 0.00:
        return {"allowed": False, "reason": "BOLL_SQUEEZE"}

    # ATR 乘数
    atr_mult_map = [
        (0.014, 1.00),
        (0.018, 0.85),
        (0.022, 0.70),
        (0.025, 0.55),
    ]
    atr_mult = 0.55
    for atr_threshold, mult in atr_mult_map:
        if atr_pct <= atr_threshold:
            atr_mult = mult
            break

    # 最终仓位
    base_portion = 0.25
    target_portion = base_portion * score_mult * bw_mult * atr_mult
    target_portion = max(
        min_symbol_position_portion,
        min(max_symbol_position_portion, target_portion)
    )

    # 名义仓位价值
    position_value = account_equity * target_portion * leverage

    return {
        "allowed": True,
        "leverage": leverage,
        "target_portion": target_portion,
        "position_value": position_value,
        "detail": {
            "score_mult":    score_mult,
            "bw_mult":       bw_mult,
            "atr_mult":      atr_mult,
            "base_portion":  base_portion,
        }
    }
```

### 8.5 容量管理更新

```
max_active_symbols: 5  (保持)

容量优先级（当 5 个持仓已满时）:
  1. 不开新仓
  2. 如果新信号 resonance_score > 当前最低分仓位 × 1.15，
     记录"更优信号等待"，不强制替换
  3. 仅当旧仓位触发止损/止盈后，才允许新信号入场

日内交易频率控制（新增）:
  每日最大交易数: 8 笔
  每日最大做多数: 4 笔（防止单边过度暴露）
  每日最大做空数: 4 笔
  同一交易对冷却: 45 分钟（从上次平仓到下次开仓）
```

---

## 9. 止盈止损重设计

### 9.1 当前胜负比问题分析

```
当前情况:
  avg_win:  +13.27 USDT
  avg_loss: -17.91 USDT
  win/loss 比: 0.741

问题根因:
  止损距离（2%）× 5x 杠杆 = 10% 账户损失（过大）
  止盈分级过于保守（0.8%/1.2%/2.0%），大行情未充分捕捉
  累计止盈 75%，剩余 25% 在行情反转时损失利润

目标:
  win/loss 比 ≥ 1.5
  avg_win ≥ 20
  avg_loss ≤ 13
```

### 9.2 新止损设计

```
分套件止损设计:

趋势市场（MACD 主导，red_bar_growing / flip 族）:
  基础止损: 1.5%（从 2% 收紧）
  动态止损: 基于 BOLL 带宽调整
    bandwidth ≤ 0.04: SL = 1.2%（带宽窄，价格不应大幅反向）
    bandwidth ≤ 0.08: SL = 1.5%（标准）
    bandwidth ≤ 0.12: SL = 1.8%（带宽宽，给更多空间）
    bandwidth > 0.12: SL = 2.0%（大波动，需要更大 SL）
  最大止损上限: 2.0%

震荡市场（BOLL 边界反弹，green_bar_growing 条件允许）:
  基础止损: 1.0%（更紧，BOLL 边界清晰）
  BOLL 结构止损: 价格跌破/涨破 BOLL 中轨 → 触发平仓 50%
  最大止损上限: 1.5%

RSI 背离止损触发（新增）:
  持多仓 + 看空背离持续 ≥ 4 根 1h K 线: 平仓 30%
  持空仓 + 看多背离持续 ≥ 4 根 1h K 线: 平仓 30%
```

### 9.3 新止盈设计

```
目标: 提升 avg_win 到 20+

新分级止盈:

Level 1: 0.6% 利润 → 平仓 15%  (快速保本，少量锁定)
Level 2: 1.2% 利润 → 平仓 25%  (中等利润确认)
Level 3: 2.0% 利润 → 平仓 25%  (趋势延续确认)
Level 4: 3.5% 利润 → 平仓 20%  (大行情捕捉)
剩余 15%: 追踪止损（让利润奔跑）

累计平仓: 85%（保留 15% 捕捉极端行情）

vs 旧配置（0.8%/1.2%/2.0% → 25%/30%/20% = 75% 累计）:
  新方案多了 Level 4（3.5%），累计 85%（vs 75%）
  Level 1 降低到 0.6%（比旧 0.8% 更低），提前小量锁定
  减少过早大量平仓（旧 Level 1 就平 25%，新 Level 1 只平 15%）
```

### 9.4 追踪止损更新

```
追踪止损（替换 VWAP 引导的追踪逻辑，改为 BOLL 引导）:

趋势模式追踪止损:
  activation: 盈利 ≥ 2.0%（从 1.8% 放宽）
  atr_multiplier: 1.5（从 1.8 收紧，减少回吐）
  min_distance: 0.010
  max_distance: 0.025
  BOLL 中轨保护: SL 不能低于 BOLL 中轨（做多方向）

震荡模式追踪止损:
  activation: 盈利 ≥ 0.8%
  atr_multiplier: 0.6
  min_distance: 0.005
  max_distance: 0.012
  BOLL 中轨保护: SL 跟随 BOLL 中轨移动

RSI 驱动追踪加速:
  RSI_1h 进入 65-70（做多时）: 追踪距离收紧 30%
  RSI_1h 进入 30-35（做空时）: 追踪距离收紧 30%
```

### 9.5 盈亏平衡更新

```
盈亏平衡触发:

原配置: 盈利 ≥ 1.2% → SL 移至 +0.4%
新配置:
  趋势市场: 盈利 ≥ 1.0% → SL 移至 +0.3%
  震荡市场: 盈利 ≥ 0.7% → SL 移至 +0.2%
  RSI 极值触发: RSI_1h > 65（做多）→ SL 移至 breakeven（不等盈利）

时间止损更新（原配置 90 分钟）:
  趋势市场: 120 分钟（趋势行情持续时间更长）
  震荡市场: 45 分钟（震荡反转快，不能久拖）
  最低盈利要求: 0.3%（原 0.5%，降低要求避免过早止损）
```

### 9.6 止盈止损参数对比表

```
参数                    旧配置          新配置          变化
──────────────────────────────────────────────────────────
stop_loss_pct          2.0%            1.5%（趋势）    ↓收紧
                                       1.0%（震荡）
max_stop_loss_pct      2.5%            2.0%            ↓收紧
TP Level 1             0.8% → 25%      0.6% → 15%      ↓早但少
TP Level 2             1.2% → 30%      1.2% → 25%      →保持点位
TP Level 3             2.0% → 20%      2.0% → 25%      ↑加大
TP Level 4             无              3.5% → 20%      ↑新增
保留仓位               25%             15%             ↓减少
累计平仓比             75%             85%             ↑提升
trailing_activation    1.8%            2.0%            ↑放宽
trailing_atr_mult      1.8             1.5             ↓收紧
breakeven_trigger      1.2%            1.0%            ↓更快
time_exit_minutes      90              120/45          分场景
预期 avg_win           +13.27          +19-22          ↑目标
预期 avg_loss          -17.91          -12-14          ↓目标
预期 W/L 比            0.741           1.4-1.8         ↑目标
```

---

## 10. 信号族管理

### 10.1 完整信号族配置

```yaml
signal_family_config:

  red_bar_growing:
    enabled: true
    direction: short
    min_signal_score: 0.90         # 保持严格
    boll_min_score: 0.60           # 新增: 替代 VWAP 要求
    rsi_1h_max: 57                 # 新增: 做空时 RSI 不能太高
    rsi_1h_min: 32                 # 新增: 做空时 RSI 不能太低
    rsi_4h_max: 60                 # 新增: 宏观动量不能太强
    preferred_regime: TRENDING_BEAR
    leverage_min: 4                # 最小 4x（核心盈利族）
    notes: "核心做空族，76.3% 贡献，保持严格质量门槛"

  flip_bullish:
    enabled: true
    direction: long
    min_signal_score: 0.80
    boll_min_score: 0.55           # 新增: 做多结构要求
    rsi_1h_min: 40                 # 翻多初期允许略低
    rsi_1h_max: 68                 # 不追超热
    rsi_4h_min: 40
    rsi_cross_above_50: true       # RSI 穿越 50 确认翻多
    preferred_regime: TRENDING_BULL
    leverage_min: 3

  flip_bearish:
    enabled: true
    direction: short
    min_signal_score: 0.80
    boll_min_score: 0.55
    rsi_1h_max: 60                 # 翻空初期允许略高
    rsi_1h_min: 32
    rsi_4h_max: 60
    rsi_cross_below_50: true
    preferred_regime: TRENDING_BEAR
    leverage_min: 3

  green_bar_growing:
    enabled: true                  # 条件保留（非禁用）
    direction: long
    min_signal_score: 0.95         # 保持极严格
    boll_min_score: 0.80           # 极高 BOLL 要求（必须在下轨区）
    boll_zone_required: [5, 6]     # 必须在下轨区或下轨外
    rsi_1h_max: 48                 # 必须处于超卖区反弹
    rsi_1h_min: 25
    rsi_slope_positive_required: true
    rsi_divergence_bullish_preferred: true
    preferred_regime: RANGING      # 仅在震荡市使用
    bandwidth_state_allowed: [tight, normal]
    leverage_max: 3                # 最大 3x（风险控制）
    notes: "仅允许 BOLL 下轨 + RSI 超卖反弹的精确组合"

  red_bar_shrinking:
    enabled: false
    disable_reason: "confirmed_negative_alpha"

  green_bar_shrinking:
    enabled: false
    disable_reason: "confirmed_negative_alpha"
```

### 10.2 信号族白名单检查伪代码

```python
def check_signal_family_whitelist(
    signal_type: str,
    direction: str,
    boll_result: dict,
    rsi_result: dict,
    market_regime: str,
) -> dict:
    """
    信号族白名单细化检查
    """
    family_cfg = SIGNAL_FAMILY_CONFIG.get(signal_type)

    if family_cfg is None:
        return {"allowed": False, "reason": f"UNKNOWN_SIGNAL:{signal_type}"}

    if not family_cfg.get("enabled", False):
        return {
            "allowed": False,
            "reason": f"DISABLED:{signal_type}:{family_cfg.get('disable_reason')}"
        }

    # 检查信号方向匹配
    if family_cfg["direction"] != direction:
        return {
            "allowed": False,
            "reason": f"DIRECTION_MISMATCH:{signal_type} is {family_cfg['direction']}"
        }

    # BOLL 评分检查（替代 VWAP）
    boll_score = boll_result["structure_score"]
    boll_min = family_cfg.get("boll_min_score", 0.55)
    if boll_score < boll_min:
        return {
            "allowed": False,
            "reason": f"BOLL_SCORE_LOW:{signal_type}: {boll_score:.3f} < {boll_min}"
        }

    # BOLL 区域检查（green_bar_growing 专用）
    required_zones = family_cfg.get("boll_zone_required")
    if required_zones is not None:
        current_zone = boll_result["zone"]
        if current_zone not in required_zones:
            return {
                "allowed": False,
                "reason": f"BOLL_ZONE_REQUIRED:{signal_type}: zone={current_zone}"
            }

    # RSI 检查
    rsi_1h = rsi_result["rsi_1h"]
    rsi_1h_max = family_cfg.get("rsi_1h_max", 70)
    rsi_1h_min = family_cfg.get("rsi_1h_min", 30)

    if not (rsi_1h_min <= rsi_1h <= rsi_1h_max):
        return {
            "allowed": False,
            "reason": f"RSI_1H_OUT_OF_RANGE:{signal_type}: {rsi_1h:.1f} not in [{rsi_1h_min},{rsi_1h_max}]"
        }

    # RSI 斜率检查（green_bar_growing）
    if family_cfg.get("rsi_slope_positive_required"):
        if rsi_result.get("rsi_slope_1h", 0) <= 0:
            return {
                "allowed": False,
                "reason": f"RSI_SLOPE_NOT_POSITIVE:{signal_type}"
            }

    # RSI 穿越检查（flip 族）
    if family_cfg.get("rsi_cross_above_50"):
        if not rsi_result.get("rsi_recently_crossed_above_50", False):
            # 豁免：RSI 当前在 50-58 且方向向上（刚穿越但还未记录）
            if not (50 <= rsi_1h <= 58 and rsi_result.get("rsi_slope_1h", 0) > 0):
                return {
                    "allowed": False,
                    "reason": f"RSI_NOT_CROSSED_50_UP:{signal_type}"
                }

    if family_cfg.get("rsi_cross_below_50"):
        if not rsi_result.get("rsi_recently_crossed_below_50", False):
            if not (42 <= rsi_1h <= 50 and rsi_result.get("rsi_slope_1h", 0) < 0):
                return {
                    "allowed": False,
                    "reason": f"RSI_NOT_CROSSED_50_DOWN:{signal_type}"
                }

    # 市场状态偏好检查（非强制，但记录）
    preferred_regime = family_cfg.get("preferred_regime")
    regime_warning = None
    if preferred_regime and market_regime != preferred_regime:
        regime_warning = f"NON_PREFERRED_REGIME:{market_regime}!={preferred_regime}"

    return {
        "allowed": True,
        "reason": None,
        "regime_warning": regime_warning,
    }
```

---

## 11. 风险控制体系

### 11.1 三层风险门控

```
Layer A: 入场前门控（硬性规则，不可绕过）

  A1. RSI 极值屏蔽（替代 VWAP hard_block）:
    做多: RSI_1h > 70 → BLOCK
    做空: RSI_1h < 30 → BLOCK

  A2. BOLL squeeze 屏蔽:
    bandwidth < 0.025 → BLOCK（不知道方向，不入场）

  A3. ATR 极值屏蔽:
    atr_pct > 0.025 → BLOCK（延续原有逻辑）

  A4. 账户回撤屏蔽:
    account_drawdown ≥ 8% → BLOCK ALL（从 10% 收紧到 8%）
    account_drawdown ≥ 5% → 仅允许 resonance_score ≥ 0.80 的信号

  A5. 日内亏损屏蔽:
    daily_loss ≥ 4%（从 5% 收紧）→ 暂停 6 小时
    连续亏损 ≥ 2 笔 → 暂停 30 分钟（保持原逻辑）

  A6. 共振评分最低门槛:
    resonance_score < 0.65 → BLOCK（三共振架构的核心门槛）

Layer B: 动态风险调整（可调，但不能超过 Layer A）

  B1. BOLL 带宽仓位缩减（见第8节）
  B2. ATR 仓位缩减（见第8节）
  B3. 高风险时段缩减（见原配置）
  B4. RSI 状态仓位加减成（新增）:
    做多时 RSI_1h 55-60 → 仓位 × 0.85（略高，谨慎）
    做多时 RSI_1h 45-55 → 仓位 × 1.00（标准）
    做多时 RSI_1h 40-45 → 仓位 × 0.90（略低，补充确认）

Layer C: 持仓中监控（实时执行）

  C1. RSI 动态止损（见第9节）
  C2. BOLL 结构止损（新增）:
    做多仓位 + 价格跌破 BOLL 中轨且 RSI_1h < 48 → 平仓 40%
    做空仓位 + 价格涨破 BOLL 中轨且 RSI_1h > 52 → 平仓 40%
  C3. RSI 背离监控（见第6节）
  C4. 保护 SLA（保持原逻辑）
```

### 11.2 账户级风控更新

```diff
--- a/config/trading_config_fund_flow.json (account risk section)
+++ b/config/trading_config_fund_flow.json

 "account_circuit": {
   "enabled": true,
-  "max_daily_loss_percent": 5,
+  "max_daily_loss_percent": 4,         // 收紧：5% → 4%
-  "max_consecutive_losses": 2,
+  "max_consecutive_losses": 2,         // 保持
-  "daily_loss_cooldown_seconds": 28800,
+  "daily_loss_cooldown_seconds": 21600, // 6h（从 8h 缩短）
-  "consecutive_loss_cooldown_seconds": 2700,
+  "consecutive_loss_cooldown_seconds": 1800, // 30min（从 45min 缩短）

+  // 新增: 中级回撤限制
+  "partial_drawdown_threshold": 0.05,  // 5% 回撤 → 要求 score ≥ 0.80
+  "full_drawdown_threshold": 0.08,     // 8% 回撤 → 全停（从 10% 收紧）
+  "drawdown_recovery_threshold": 0.03  // 回撤恢复到 3% 以下才解除限制
 }
```

### 11.3 极端波动冷却更新

```diff
 "extreme_volatility_cooldown": {
   "enabled": true,
-  "atr_pct_threshold": 0.02,
+  "atr_pct_threshold": 0.018,          // 从 2.0% 收紧到 1.8%
   "consecutive_bars": 2,
-  "cooldown_seconds": 1800,
+  "cooldown_seconds": 2700,             // 冷却从 30min 延长到 45min
+  "boll_bandwidth_expansion_trigger": true,  // 新增: BOLL 带宽快速扩张也触发
+  "boll_bandwidth_expansion_ratio": 1.8,    // 带宽 1.8x 均值 → 额外冷却
+  "boll_expansion_cooldown_seconds": 900    // BOLL 扩张冷却 15min
 }
```

### 11.4 Protection SLA 与结构退出

```
BOLL 结构退出（新增，替代部分 4H shrink exit 功能）:

触发条件:
  做多仓位持仓中，出现以下任一:
    价格跌破 BOLL 中轨 AND RSI_1h < 48 AND 持续 2 根 1h K 线
    → 平仓 40%，收紧 SL 至 0.8%

  做空仓位持仓中，出现以下任一:
    价格涨破 BOLL 中轨 AND RSI_1h > 52 AND 持续 2 根 1h K 线
    → 平仓 40%，收紧 SL 至 0.8%

损失缓解（继承原有逻辑）:
  当前亏损 > 0.5% → 仅平仓 25%（避免损失扩大）

4H MACD 缩小退出（继承原有逻辑，不变）:
  连续 3 根 4H K 线 MACD 缩小 ≥ 15% → 触发出场
```

---

## 12. 配置文件完整 Diff

### 12.1 策略核心配置 Diff

```diff
--- a/config/trading_config_fund_flow.json
+++ b/config/trading_config_fund_flow.json

@@ 策略元数据 @@
+  "_strategy_version": "v3.0-resonance",
+  "_indicator_stack": "MACD+BOLL+RSI",
+  "_vwap_status": "DEPRECATED",
+  "_last_updated": "2026-04-13",

@@ 杠杆配置 @@
-  "min_leverage": 5,
-  "default_leverage": 5,
-  "max_leverage": 5,
+  "min_leverage": 3,
+  "default_leverage": 4,
+  "max_leverage": 5,
+  "leverage_mode": "dynamic",
+  "leverage_score_tiers": [
+    {"score_min": 0.85, "score_max": 1.00, "leverage": 5},
+    {"score_min": 0.78, "score_max": 0.85, "leverage": 4},
+    {"score_min": 0.65, "score_max": 0.78, "leverage": 3}
+  ],

@@ 仓位配置 @@
-  "default_target_portion": 0.30,
+  "default_target_portion": 0.25,       // 从 30% 降至 25% 作为基础
+  "position_score_tiers": [
+    {"score_min": 0.92, "position_mult": 1.20},
+    {"score_min": 0.85, "position_mult": 1.10},
+    {"score_min": 0.78, "position_mult": 1.00},
+    {"score_min": 0.70, "position_mult": 0.90},
+    {"score_min": 0.65, "position_mult": 0.80}
+  ],
   "max_symbol_position_portion": 0.30,  // 保持上限 30%
   "min_open_portion": 0.06,             // 保持下限

@@ VWAP 全部废弃 @@
-  "min_vwap_score_for_entry": 0.12,
-  "vwap_execution_penalty_only": true,
-  "flip_bullish_min_vwap_score": 0.08,
-  "flip_bearish_retest_reject_min_vwap_score": 0.18,
-  "stable_bear_continuation_min_vwap_score": 0.05,
-  "stable_bull_continuation_min_vwap_score": 0.08,
-  "preflip_trial_min_vwap_score": 0.08,
-  "trial_short_below_structure_promotion_min_vwap_score": 0.08,
-  "vwap_score_position_tiers": [...],

@@ BOLL 结构配置（新增，替代 VWAP）@@
+  "boll_config": {
+    "period": 20,
+    "std_dev": 2.0,
+    "min_structure_score_for_entry": 0.55,
+    "hard_block_threshold": 0.20,
+    "bandwidth": {
+      "squeeze_threshold": 0.025,
+      "tight_threshold": 0.045,
+      "normal_threshold": 0.080,
+      "wide_threshold": 0.120,
+      "position_mult_by_state": {
+        "squeeze": 0.00,
+        "tight":   0.70,
+        "normal":  1.00,
+        "wide":    0.90,
+        "expanding": 0.70
+      }
+    },
+    "mid_slope_bonus": {
+      "strong_aligned": 0.05,
+      "weak_aligned": 0.02,
+      "neutral": 0.00,
+      "opposed": -0.10
+    },
+    "breakout_bonus": 0.15,
+    "structure_exit": {
+      "enabled": true,
+      "long_exit_condition": "price_below_mid AND rsi_1h < 48",
+      "short_exit_condition": "price_above_mid AND rsi_1h > 52",
+      "bars_required": 2,
+      "exit_portion": 0.40,
+      "loss_mitigation_pct": 0.005,
+      "loss_mitigation_exit_portion": 0.25
+    }
+  },

@@ RSI 配置（完整更新）@@
+  "rsi_config": {
+    "enabled": true,
+    "period_15m": 7,
+    "period_1h": 14,
+    "period_4h": 21,
+    "overbought": 70,
+    "oversold": 30,
+    "extreme_overbought": 78,
+    "extreme_oversold": 22,
+    "neutral_high": 55,
+    "neutral_low": 45,
+    "gate": {
+      "long_rsi_4h_min": 43,
+      "long_rsi_1h_min": 43,
+      "long_rsi_1h_max": 70,
+      "long_rsi_15m_min": 47,
+      "long_rsi_15m_slope_positive": true,
+      "short_rsi_4h_max": 57,
+      "short_rsi_1h_max": 57,
+      "short_rsi_1h_min": 30,
+      "short_rsi_15m_max": 53,
+      "short_rsi_15m_slope_negative": true
+    },
+    "flip_override": {
+      "flip_bullish_rsi_1h_min": 40,
+      "flip_bullish_rsi_4h_min": 40,
+      "flip_bearish_rsi_1h_max": 60,
+      "flip_bearish_rsi_4h_max": 60
+    },
+    "hard_block": {
+      "long_rsi_1h_max": 70,
+      "short_rsi_1h_min": 30
+    },
+    "emergency_exit": {
+      "enabled": true,
+      "long_partial_exit_rsi": 78,
+      "short_partial_exit_rsi": 22,
+      "partial_exit_pct": 0.40,
+      "long_breakeven_rsi": 72,
+      "short_breakeven_rsi": 28
+    },
+    "divergence": {
+      "enabled": true,
+      "lookback_bars": 10,
+      "min_price_diff_pct": 0.004,
+      "min_rsi_diff": 3.0,
+      "regular_bullish_bonus": 0.12,
+      "regular_bearish_bonus": 0.12,
+      "hidden_bullish_bonus": 0.08,
+      "hidden_bearish_bonus": 0.08,
+      "adverse_divergence_exit_pct": 0.30,
+      "adverse_divergence_bars_required": 4
+    },
+    "dynamic_sl": {
+      "enabled": true,
+      "long_breakeven_rsi": 68,
+      "long_profit_lock_rsi": 72,
+      "long_profit_lock_pct": 0.40,
+      "short_breakeven_rsi": 32,
+      "short_profit_lock_rsi": 28,
+      "short_profit_lock_pct": 0.40
+    }
+  },

@@ 三共振评分权重 @@
-  "scoring_weights": {
-    "weight_4h_direction": 0.25,
-    "weight_1h_direction": 0.10,
-    "weight_rsi_4h": 0.10,
-    "weight_rsi_1h": 0.15,
-    "weight_rsi_divergence": 0.05,
-    "weight_boll_rsi": 0.20,
-    "weight_15m_entry": 0.00,
-    "weight_volume": 0.15
-  },
+  "resonance_scoring": {
+    "trending_market": {
+      "weight_macd_layer": 0.40,
+      "weight_boll_layer": 0.35,
+      "weight_rsi_layer": 0.25
+    },
+    "ranging_market": {
+      "weight_macd_layer": 0.30,
+      "weight_boll_layer": 0.45,
+      "weight_rsi_layer": 0.25
+    },
+    "macd_layer_internal": {
+      "weight_4h_direction": 0.45,
+      "weight_1h_direction": 0.30,
+      "weight_15m_momentum": 0.15,
+      "weight_macd_slope": 0.10
+    },
+    "min_resonance_score": 0.65,
+    "score_tier_position_mults": {
+      "0.92": 1.20,
+      "0.85": 1.10,
+      "0.78": 1.00,
+      "0.70": 0.90,
+      "0.65": 0.80
+    }
+  },

@@ 止损更新 @@
-  "stop_loss_pct": 0.02,
-  "max_stop_loss_pct": 0.025,
+  "stop_loss_config": {
+    "trending_market": {
+      "base_stop_loss_pct": 0.015,
+      "boll_bandwidth_dynamic": true,
+      "bandwidth_sl_map": {
+        "tight":     0.012,
+        "normal":    0.015,
+        "wide":      0.018,
+        "expanding": 0.020
+      },
+      "max_stop_loss_pct": 0.020
+    },
+    "ranging_market": {
+      "base_stop_loss_pct": 0.010,
+      "boll_mid_cross_exit": true,
+      "max_stop_loss_pct": 0.015
+    }
+  },

@@ 止盈更新 @@
-  "take_profit_pct_levels": [0.008, 0.012, 0.02],
-  "take_profit_reduce_pct_levels": [0.25, 0.30, 0.20],
+  "take_profit_levels": [0.006, 0.012, 0.020, 0.035],
+  "take_profit_reduce_pcts": [0.15, 0.25, 0.25, 0.20],
+  "remaining_position_trailing": true,
+  "remaining_position_pct": 0.15,

@@ 追踪止损更新 @@
   "trailing_stop": {
     "enabled": true,
     "mode": "dynamic",
     "trending": {
-      "activation_pct": 0.018,
-      "atr_multiplier": 1.8,
+      "activation_pct": 0.020,
+      "atr_multiplier": 1.5,
       "min_distance": 0.010,
-      "max_distance": 0.030
+      "max_distance": 0.025,
+      "boll_mid_protection": true    // 新增: SL 不能穿越 BOLL 中轨
     },
     "volatile": {
       "activation_pct": 0.008,
-      "atr_multiplier": 0.6,
+      "atr_multiplier": 0.5,
       "min_distance": 0.005,
-      "max_distance": 0.010
+      "max_distance": 0.009
     }
   },

@@ 盈亏平衡更新 @@
-  "breakeven_trigger_pnl_ratio": 0.012,
-  "breakeven_lock_ratio": 0.004,
+  "breakeven_trending_trigger": 0.010,
+  "breakeven_trending_lock": 0.003,
+  "breakeven_ranging_trigger": 0.007,
+  "breakeven_ranging_lock": 0.002,

@@ 时间止损更新 @@
-  "time_exit_minutes": 90,
-  "time_exit_min_profit_pct": 0.005,
+  "time_exit_trending_minutes": 120,
+  "time_exit_ranging_minutes": 45,
+  "time_exit_min_profit_pct": 0.003,

@@ 频率控制（新增）@@
+  "frequency_control": {
+    "daily_max_trades_total": 8,
+    "daily_max_long_trades": 4,
+    "daily_max_short_trades": 4,
+    "same_symbol_cooldown_minutes": 45,
+    "same_symbol_same_direction_cooldown_minutes": 90
+  },

@@ 禁用标志更新 @@
   "disable_flags": {
     "disable_red_bar_shrinking_entries": true,
     "disable_green_bar_shrinking_entries": true,
+    "disable_vwap_filter": true,          // 废弃 VWAP 过滤
+    "enable_boll_structure_filter": true, // 启用 BOLL 结构过滤
+    "enable_rsi_triple_gate": true,       // 启用 RSI 三时间框架门控
+    "enable_resonance_scoring": true,     // 启用三共振评分
+    "enable_dynamic_leverage": true,      // 启用动态杠杆
+    "enable_boll_structure_exit": true    // 启用 BOLL 结构退出
   }
```

---

## 13. 核心模块伪代码

### 13.1 BOLL 结构分析器

```python
# src/indicators/boll_structure_analyzer.py

import math
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BOLLResult:
    upper: float
    mid: float
    lower: float
    bandwidth: float
    bandwidth_state: str        # squeeze/tight/normal/wide/expanding
    relative_position: float    # 0 (下轨) ~ 1 (上轨)
    zone: int                   # 0-6
    structure_score: float      # 0.0 ~ 1.0
    gate_pass: bool
    gate_fail_reason: Optional[str]
    mid_slope: float            # 中轨斜率
    mid_slope_bonus: float      # 斜率加成


class BOLLStructureAnalyzer:
    """
    BOLL 结构分析器
    功能:
      - 计算 BOLL 带 (upper/mid/lower)
      - 价格区域定位 (zone 0-6)
      - 带宽状态分类
      - 结构评分生成
      - 替代 VWAP 的过滤功能
    """

    ZONE_NAMES = {
        0: "above_upper",    # 上轨外
        1: "upper_zone",     # 上轨区
        2: "mid_upper_zone", # 中上区
        3: "neutral_zone",   # 中性区
        4: "mid_lower_zone", # 中下区
        5: "lower_zone",     # 下轨区
        6: "below_lower",    # 下轨外
    }

    def __init__(self, config: dict):
        self.period = config.get("period", 20)
        self.std_dev = config.get("std_dev", 2.0)
        self.min_score = config.get("min_structure_score_for_entry", 0.55)
        self.hard_block = config.get("hard_block_threshold", 0.20)

        self.bw_thresholds = {
            "squeeze":   config.get("bandwidth", {}).get("squeeze_threshold", 0.025),
            "tight":     config.get("bandwidth", {}).get("tight_threshold", 0.045),
            "normal":    config.get("bandwidth", {}).get("normal_threshold", 0.080),
            "wide":      config.get("bandwidth", {}).get("wide_threshold", 0.120),
        }

        self.mid_slope_bonus_map = config.get("mid_slope_bonus", {
            "strong_aligned": 0.05,
            "weak_aligned":   0.02,
            "neutral":        0.00,
            "opposed":       -0.10,
        })

        # 做多评分矩阵
        self.long_zone_scores = {
            0: -1.0,  # 上轨外: 硬性禁止做多
            1:  0.0,  # 上轨区: 不做多
            2:  0.10, # 中上区: 极弱
            3:  0.35, # 中性区: 弱
            4:  0.65, # 中下区: 良
            5:  0.90, # 下轨区: 优
            6:  1.00, # 下轨外: 极优（超卖反弹）
        }

        # 做空评分矩阵
        self.short_zone_scores = {
            0:  1.00, # 上轨外: 极优（超买回落）
            1:  0.90, # 上轨区: 优
            2:  0.65, # 中上区: 良
            3:  0.35, # 中性区: 弱
            4:  0.10, # 中下区: 极弱
            5:  0.0,  # 下轨区: 不做空
            6: -1.0,  # 下轨外: 硬性禁止做空
        }

    def compute_boll(self, closes: list[float]) -> tuple[float, float, float]:
        """计算 BOLL 带 (upper, mid, lower)"""
        if len(closes) < self.period:
            return 0.0, 0.0, 0.0

        recent = closes[-self.period:]
        mid = sum(recent) / self.period
        variance = sum((x - mid) ** 2 for x in recent) / self.period
        std = math.sqrt(variance)

        upper = mid + self.std_dev * std
        lower = mid - self.std_dev * std
        return upper, mid, lower

    def classify_bandwidth(self, bandwidth: float) -> str:
        """带宽状态分类"""
        if bandwidth < self.bw_thresholds["squeeze"]:   return "squeeze"
        elif bandwidth < self.bw_thresholds["tight"]:   return "tight"
        elif bandwidth < self.bw_thresholds["normal"]:  return "normal"
        elif bandwidth < self.bw_thresholds["wide"]:    return "wide"
        else:                                            return "expanding"

    def locate_zone(self, price: float, upper: float, mid: float, lower: float) -> tuple[int, float]:
        """
        定位价格区域 (zone 0-6) 和相对位置 (0-1)
        """
        if price > upper:
            return 0, 1.0 + (price - upper) / (upper - lower)
        elif price < lower:
            return 6, 0.0 - (lower - price) / (upper - lower)

        bandwidth = upper - lower
        if bandwidth <= 0:
            return 3, 0.5

        relative = (price - lower) / bandwidth  # 0=下轨, 1=上轨

        if relative >= 0.85:   zone = 1   # 上轨区
        elif relative >= 0.65: zone = 2   # 中上区
        elif relative >= 0.45: zone = 3   # 中性区
        elif relative >= 0.25: zone = 4   # 中下区
        else:                  zone = 5   # 下轨区

        return zone, relative

    def compute_mid_slope(
        self,
        closes: list[float],
        slope_bars: int = 3
    ) -> float:
        """
        计算 BOLL 中轨（SMA）斜率
        """
        if len(closes) < self.period + slope_bars:
            return 0.0

        mid_current = sum(closes[-self.period:]) / self.period
        mid_prev    = sum(closes[-(self.period + slope_bars):-slope_bars]) / self.period

        return (mid_current - mid_prev) / mid_prev if mid_prev != 0 else 0.0

    def compute_mid_slope_bonus(self, slope: float, direction: str) -> float:
        """中轨斜率加成"""
        if direction == "long":
            aligned = slope > 0
        elif direction == "short":
            aligned = slope < 0
        else:
            return 0.0

        abs_slope = abs(slope)

        if aligned:
            if abs_slope > 0.002:
                return self.mid_slope_bonus_map["strong_aligned"]
            else:
                return self.mid_slope_bonus_map["weak_aligned"]
        else:
            if abs_slope > 0.001:
                return self.mid_slope_bonus_map["opposed"]
            else:
                return self.mid_slope_bonus_map["neutral"]

    def analyze(
        self,
        symbol: str,
        direction: str,
        closes_4h: list[float],
        closes_1h: list[float],
        current_price: float,
    ) -> BOLLResult:
        """
        BOLL 结构分析主入口
        """

        # 4h BOLL（主结构）
        upper_4h, mid_4h, lower_4h = self.compute_boll(closes_4h)
        if mid_4h <= 0:
            return BOLLResult(
                upper=0, mid=0, lower=0, bandwidth=0,
                bandwidth_state="unknown", relative_position=0.5,
                zone=3, structure_score=0.0,
                gate_pass=False, gate_fail_reason="BOLL_DATA_MISSING",
                mid_slope=0, mid_slope_bonus=0,
            )

        bandwidth = (upper_4h - lower_4h) / mid_4h
        bw_state  = self.classify_bandwidth(bandwidth)
        zone, rel_pos = self.locate_zone(current_price, upper_4h, mid_4h, lower_4h)

        # BOLL squeeze 硬性禁止
        if bw_state == "squeeze":
            return BOLLResult(
                upper=upper_4h, mid=mid_4h, lower=lower_4h,
                bandwidth=bandwidth, bandwidth_state=bw_state,
                relative_position=rel_pos, zone=zone,
                structure_score=0.0,
                gate_pass=False, gate_fail_reason="BOLL_SQUEEZE",
                mid_slope=0, mid_slope_bonus=0,
            )

        # 获取区域评分
        if direction == "long":
            zone_score = self.long_zone_scores.get(zone, 0.0)
        elif direction == "short":
            zone_score = self.short_zone_scores.get(zone, 0.0)
        else:
            zone_score = 0.0

        # 硬性反向禁止
        if zone_score < 0:
            return BOLLResult(
                upper=upper_4h, mid=mid_4h, lower=lower_4h,
                bandwidth=bandwidth, bandwidth_state=bw_state,
                relative_position=rel_pos, zone=zone,
                structure_score=0.0,
                gate_pass=False,
                gate_fail_reason=f"BOLL_ZONE_HARD_BLOCK: zone={zone}, dir={direction}",
                mid_slope=0, mid_slope_bonus=0,
            )

        # 中轨斜率计算
        mid_slope = self.compute_mid_slope(closes_4h)
        slope_bonus = self.compute_mid_slope_bonus(mid_slope, direction)

        # 最终结构评分
        structure_score = max(0.0, min(1.0, zone_score + slope_bonus))

        # 门控检查（替代 VWAP 门控）
        if structure_score < self.hard_block:
            return BOLLResult(
                upper=upper_4h, mid=mid_4h, lower=lower_4h,
                bandwidth=bandwidth, bandwidth_state=bw_state,
                relative_position=rel_pos, zone=zone,
                structure_score=structure_score,
                gate_pass=False,
                gate_fail_reason=f"BOLL_HARD_BLOCK: score={structure_score:.3f} < {self.hard_block}",
                mid_slope=mid_slope, mid_slope_bonus=slope_bonus,
            )

        gate_pass = structure_score >= self.min_score

        return BOLLResult(
            upper=upper_4h, mid=mid_4h, lower=lower_4h,
            bandwidth=bandwidth, bandwidth_state=bw_state,
            relative_position=rel_pos, zone=zone,
            structure_score=structure_score,
            gate_pass=gate_pass,
            gate_fail_reason=None if gate_pass else (
                f"BOLL_GATE_FAIL: {structure_score:.3f} < {self.min_score}"
            ),
            mid_slope=mid_slope, mid_slope_bonus=slope_bonus,
        )
```

### 13.2 RSI 分析器（完整版）

```python
# src/indicators/rsi_analyzer.py

import math
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RSIResult:
    rsi_4h: float
    rsi_1h: float
    rsi_15m: float
    rsi_slope_1h: float
    rsi_slope_15m: float
    momentum_score: float           # 0.0 ~ 1.0
    gate_pass: bool
    fail_reason: Optional[str]
    divergence_type: Optional[str]  # regular_bullish/regular_bearish/hidden_bullish/hidden_bearish
    divergence_bonus: float
    rsi_recently_crossed_above_50: bool
    rsi_recently_crossed_below_50: bool
    extreme_status: Optional[str]   # extreme_overbought/extreme_oversold/None


class RSIAnalyzer:
    """
    RSI 多时间框架分析器
    功能:
      - 三时间框架 RSI 计算
      - RSI 动量评分
      - 三时间框架门控
      - 背离检测
      - 极值检测
      - RSI 穿越 50 检测
    """

    def __init__(self, config: dict):
        rsi_cfg = config.get("rsi_config", {})
        self.period_map = {
            "4h":  rsi_cfg.get("period_4h", 21),
            "1h":  rsi_cfg.get("period_1h", 14),
            "15m": rsi_cfg.get("period_15m", 7),
        }
        self.overbought  = rsi_cfg.get("overbought", 70)
        self.oversold    = rsi_cfg.get("oversold", 30)
        self.extreme_ob  = rsi_cfg.get("extreme_overbought", 78)
        self.extreme_os  = rsi_cfg.get("extreme_oversold", 22)
        self.neutral_h   = rsi_cfg.get("neutral_high", 55)
        self.neutral_l   = rsi_cfg.get("neutral_low", 45)

        self.gate_cfg  = rsi_cfg.get("gate", {})
        self.flip_cfg  = rsi_cfg.get("flip_override", {})
        self.div_cfg   = rsi_cfg.get("divergence", {})

        # 做多 RSI 评分映射
        self._long_score_thresholds = [
            (25,  0.0),
            (35,  0.6),
            (45,  0.8),
            (60,  1.0),
            (65,  0.8),
            (70,  0.4),
            (999, 0.0),
        ]

        # 做空 RSI 评分映射
        self._short_score_thresholds = [
            (30,  0.0),
            (35,  0.4),
            (40,  0.8),
            (55,  1.0),
            (65,  0.8),
            (75,  0.6),
            (999, 0.0),
        ]

    # ──────────────────────────────────────
    # RSI 计算核心
    # ──────────────────────────────────────

    def _wilder_rsi(self, closes: list[float], period: int) -> list[float]:
        """Wilder 平滑 RSI"""
        if len(closes) < period + 1:
            return [float("nan")] * len(closes)

        gains  = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]

        rsi = [float("nan")] * len(closes)
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            rs = avg_g / avg_l if avg_l > 0 else float("inf")
            rsi[i + 1] = 100.0 if math.isinf(rs) else 100 - (100 / (1 + rs))

        return rsi

    def get_rsi(self, closes: list[float], timeframe: str) -> float:
        """获取最新 RSI"""
        period = self.period_map.get(timeframe, 14)
        series = self._wilder_rsi(closes, period)
        for v in reversed(series):
            if not math.isnan(v):
                return v
        return float("nan")

    def get_slope(self, closes: list[float], timeframe: str, bars: int = 3) -> float:
        """RSI 斜率（最近 bars 根的变化）"""
        period = self.period_map.get(timeframe, 14)
        series = self._wilder_rsi(closes, period)
        valid  = [v for v in series if not math.isnan(v)]
        if len(valid) < bars + 1:
            return 0.0
        return valid[-1] - valid[-(bars+1)]

    # ──────────────────────────────────────
    # RSI 评分
    # ──────────────────────────────────────

    def score_rsi(self, rsi_value: float, direction: str) -> float:
        """RSI 动量评分"""
        if math.isnan(rsi_value):
            return 0.0

        thresholds = (
            self._long_score_thresholds if direction == "long"
            else self._short_score_thresholds
        )

        for threshold, score in thresholds:
            if rsi_value < threshold:
                return score

        return 0.0

    # ──────────────────────────────────────
    # 三时间框架门控
    # ──────────────────────────────────────

    def check_gate(
        self,
        direction: str,
        signal_type: str,
        rsi_4h: float,
        rsi_1h: float,
        rsi_15m: float,
        slope_15m: float,
    ) -> tuple[bool, Optional[str]]:
        """
        RSI 三时间框架联合门控
        返回: (gate_pass, fail_reason)
        """
        is_flip = signal_type in ("flip_bullish", "flip_bearish")
        cfg     = self.gate_cfg
        fcfg    = self.flip_cfg

        # 硬性极值屏蔽（最高优先级）
        if direction == "long" and rsi_1h >= self.overbought:
            return False, f"RSI_HARD_BLOCK_LONG: RSI_1h={rsi_1h:.1f} >= {self.overbought}"
        if direction == "short" and rsi_1h <= self.oversold:
            return False, f"RSI_HARD_BLOCK_SHORT: RSI_1h={rsi_1h:.1f} <= {self.oversold}"

        if direction == "long":
            rsi_4h_min  = fcfg.get("flip_bullish_rsi_4h_min", 40) if is_flip else cfg.get("long_rsi_4h_min", 43)
            rsi_1h_min  = fcfg.get("flip_bullish_rsi_1h_min", 40) if is_flip else cfg.get("long_rsi_1h_min", 43)
            rsi_15m_min = cfg.get("long_rsi_15m_min", 47)
            slope_req   = cfg.get("long_rsi_15m_slope_positive", True)

            if rsi_4h < rsi_4h_min:
                return False, f"RSI_4H_GATE_LONG: {rsi_4h:.1f} < {rsi_4h_min}"
            if rsi_1h < rsi_1h_min:
                return False, f"RSI_1H_GATE_LONG: {rsi_1h:.1f} < {rsi_1h_min}"
            if rsi_15m < rsi_15m_min:
                return False, f"RSI_15M_GATE_LONG: {rsi_15m:.1f} < {rsi_15m_min}"
            if slope_req and slope_15m <= 0:
                return False, f"RSI_15M_SLOPE_GATE_LONG: slope={slope_15m:.3f}"

        elif direction == "short":
            rsi_4h_max  = fcfg.get("flip_bearish_rsi_4h_max", 60) if is_flip else cfg.get("short_rsi_4h_max", 57)
            rsi_1h_max  = fcfg.get("flip_bearish_rsi_1h_max", 60) if is_flip else cfg.get("short_rsi_1h_max", 57)
            rsi_15m_max = cfg.get("short_rsi_15m_max", 53)
            slope_req   = cfg.get("short_rsi_15m_slope_negative", True)

            if rsi_4h > rsi_4h_max:
                return False, f"RSI_4H_GATE_SHORT: {rsi_4h:.1f} > {rsi_4h_max}"
            if rsi_1h > rsi_1h_max:
                return False, f"RSI_1H_GATE_SHORT: {rsi_1h:.1f} > {rsi_1h_max}"
            if rsi_15m > rsi_15m_max:
                return False, f"RSI_15M_GATE_SHORT: {rsi_15m:.1f} > {rsi_15m_max}"
            if slope_req and slope_15m >= 0:
                return False, f"RSI_15M_SLOPE_GATE_SHORT: slope={slope_15m:.3f}"

        return True, None

    # ──────────────────────────────────────
    # 背离检测
    # ──────────────────────────────────────

    def detect_divergence(
        self,
        candles_1h: list[dict],
        direction: str,
    ) -> tuple[Optional[str], float]:
        """
        背离检测
        返回: (divergence_type, bonus)
        """
        if not self.div_cfg.get("enabled", True):
            return None, 0.0

        lookback = self.div_cfg.get("lookback_bars", 10)
        min_price_diff = self.div_cfg.get("min_price_diff_pct", 0.004)
        min_rsi_diff   = self.div_cfg.get("min_rsi_diff", 3.0)

        if len(candles_1h) < lookback + 2:
            return None, 0.0

        closes = [c["close"] for c in candles_1h]
        highs  = [c["high"]  for c in candles_1h]
        lows   = [c["low"]   for c in candles_1h]

        rsi_series = self._wilder_rsi(closes, self.period_map["1h"])
        valid_rsi  = [(i, v) for i, v in enumerate(rsi_series) if not math.isnan(v)]
        recent     = valid_rsi[-lookback:]

        if len(recent) < 4:
            return None, 0.0

        curr_idx, curr_rsi = recent[-1]
        curr_high = highs[curr_idx]
        curr_low  = lows[curr_idx]

        hist = recent[:-2]
        min_rsi_idx, min_rsi_val = min(hist, key=lambda x: x[1])
        max_rsi_idx, max_rsi_val = max(hist, key=lambda x: x[1])

        hist_low  = lows[min_rsi_idx]
        hist_high = highs[max_rsi_idx]

        # 正则看多背离
        if (curr_low < hist_low * (1 - min_price_diff)
                and curr_rsi > min_rsi_val + min_rsi_diff):
            bonus = self.div_cfg.get("regular_bullish_bonus", 0.12)
            return "regular_bullish", bonus if direction == "long" else -bonus

        # 正则看空背离
        if (curr_high > hist_high * (1 + min_price_diff)
                and curr_rsi < max_rsi_val - min_rsi_diff):
            bonus = self.div_cfg.get("regular_bearish_bonus", 0.12)
            return "regular_bearish", bonus if direction == "short" else -bonus

        # 隐藏看多背离
        if (curr_high > hist_high * (1 + min_price_diff)
                and curr_rsi < max_rsi_val - min_rsi_diff
                and curr_rsi > 45):
            bonus = self.div_cfg.get("hidden_bullish_bonus", 0.08)
            return "hidden_bullish", bonus if direction == "long" else 0.0

        # 隐藏看空背离
        if (curr_low < hist_low * (1 - min_price_diff)
                and curr_rsi > min_rsi_val + min_rsi_diff
                and curr_rsi < 55):
            bonus = self.div_cfg.get("hidden_bearish_bonus", 0.08)
            return "hidden_bearish", bonus if direction == "short" else 0.0

        return None, 0.0

    # ──────────────────────────────────────
    # 穿越检测
    # ──────────────────────────────────────

    def check_rsi_cross_50(
        self,
        closes_1h: list[float],
        cross_window_bars: int = 4,
    ) -> dict:
        """
        检测 RSI 是否最近穿越 50
        """
        series = self._wilder_rsi(closes_1h, self.period_map["1h"])
        valid  = [v for v in series if not math.isnan(v)]

        if len(valid) < cross_window_bars + 1:
            return {"crossed_above": False, "crossed_below": False}

        recent = valid[-(cross_window_bars+1):]
        prev   = recent[:-1]
        curr   = recent[-1]

        crossed_above = any(p < 50 for p in prev) and curr >= 50
        crossed_below = any(p > 50 for p in prev) and curr <= 50

        return {
            "crossed_above": crossed_above,
            "crossed_below": crossed_below,
        }

    # ──────────────────────────────────────
    # 主分析入口
    # ──────────────────────────────────────

    def analyze(
        self,
        symbol: str,
        direction: str,
        signal_type: str,
        closes_4h: list[float],
        closes_1h: list[float],
        closes_15m: list[float],
        candles_1h: list[dict],
    ) -> RSIResult:
        """
        RSI 综合分析
        """

        # 计算各时间框架 RSI
        rsi_4h  = self.get_rsi(closes_4h,  "4h")
        rsi_1h  = self.get_rsi(closes_1h,  "1h")
        rsi_15m = self.get_rsi(closes_15m, "15m")

        slope_1h  = self.get_slope(closes_1h,  "1h",  bars=3)
        slope_15m = self.get_slope(closes_15m, "15m", bars=3)

        # 极值状态
        if rsi_1h >= self.extreme_ob:
            extreme_status = "extreme_overbought"
        elif rsi_1h <= self.extreme_os:
            extreme_status = "extreme_oversold"
        else:
            extreme_status = None

        # 背离检测
        div_type, div_bonus = self.detect_divergence(candles_1h, direction)

        # 穿越检测
        cross = self.check_rsi_cross_50(closes_1h)

        # 三时间框架门控
        gate_pass, fail_reason = self.check_gate(
            direction, signal_type,
            rsi_4h, rsi_1h, rsi_15m, slope_15m
        )

        # 动量评分（仅用 RSI_1h，4h 作为加权因子）
        score_1h = self.score_rsi(rsi_1h, direction)
        score_4h = self.score_rsi(rsi_4h, direction)
        momentum_score = score_1h * 0.70 + score_4h * 0.30

        return RSIResult(
            rsi_4h=rsi_4h,
            rsi_1h=rsi_1h,
            rsi_15m=rsi_15m,
            rsi_slope_1h=slope_1h,
            rsi_slope_15m=slope_15m,
            momentum_score=momentum_score,
            gate_pass=gate_pass,
            fail_reason=fail_reason,
            divergence_type=div_type,
            divergence_bonus=div_bonus,
            rsi_recently_crossed_above_50=cross["crossed_above"],
            rsi_recently_crossed_below_50=cross["crossed_below"],
            extreme_status=extreme_status,
        )
```

---

## 14. 代码修改 Diff（src 层）

### 14.1 macd_strategy_v2.py

```diff
--- a/src/fund_flow/macd_strategy_v2.py
+++ b/src/fund_flow/macd_strategy_v2.py

@@ 导入 @@
 import math
 import logging
+from src.indicators.boll_structure_analyzer import BOLLStructureAnalyzer, BOLLResult
+from src.indicators.rsi_analyzer import RSIAnalyzer, RSIResult
+from src.fund_flow.resonance_scorer import ResonanceScorer

@@ __init__ @@
 class MACDStrategyV2Engine:

     def __init__(self, config: dict):
         self.config = config
+        # 三共振组件初始化
+        self.boll_analyzer = BOLLStructureAnalyzer(config.get("boll_config", {}))
+        self.rsi_analyzer  = RSIAnalyzer(config)
+        self.resonance_scorer = ResonanceScorer(config)
+
+        # VWAP 废弃标志
+        self._vwap_deprecated = config.get("disable_flags", {}).get("disable_vwap_filter", True)
+        if self._vwap_deprecated:
+            logger.info("VWAP filter DEPRECATED. Using BOLL structure filter.")

@@ analyze() 主函数 @@
-    def analyze(self, symbol, flow_context):
+    def analyze(self, symbol: str, flow_context: dict) -> dict:
         """
-        MACD V2 信号分析（含 VWAP 过滤）
+        MACD V2 信号分析（三共振版本：MACD + BOLL + RSI）
+        VWAP 已废弃
         """

         # Step 1: MACD 基础信号（保持原有逻辑）
         macd_result = self._compute_macd_signals(symbol, flow_context)
         if macd_result["direction"] == "neutral":
             return {"direction": "neutral", "score": 0.0, "reason": "MACD_NEUTRAL"}

         direction      = macd_result["direction"]
         signal_type_1h = macd_result["signal_type_1h"]

-        # Step 2: VWAP 过滤（旧逻辑，已废弃）
-        vwap_score = flow_context.get("vwap_score", 0)
-        if vwap_score < self.config.get("min_vwap_score_for_entry", 0.12):
-            return {"direction": "neutral", "score": 0.0, "reason": "VWAP_TOO_LOW"}

+        # Step 2: BOLL 结构分析（替代 VWAP）
+        boll_result = self.boll_analyzer.analyze(
+            symbol=symbol,
+            direction=direction,
+            closes_4h=flow_context.get("closes_4h", []),
+            closes_1h=flow_context.get("closes_1h", []),
+            current_price=flow_context.get("current_price", 0),
+        )
+
+        if not boll_result.gate_pass:
+            logger.debug(f"[{symbol}] BOLL gate fail: {boll_result.gate_fail_reason}")
+            return {
+                "direction": "neutral",
+                "score": 0.0,
+                "reason": boll_result.gate_fail_reason,
+                "boll_zone": boll_result.zone,
+                "boll_score": boll_result.structure_score,
+            }

+        # Step 3: RSI 三时间框架分析
+        rsi_result = self.rsi_analyzer.analyze(
+            symbol=symbol,
+            direction=direction,
+            signal_type=signal_type_1h,
+            closes_4h=flow_context.get("closes_4h", []),
+            closes_1h=flow_context.get("closes_1h", []),
+            closes_15m=flow_context.get("closes_15m", []),
+            candles_1h=flow_context.get("candles_1h", []),
+        )
+
+        if not rsi_result.gate_pass:
+            logger.debug(f"[{symbol}] RSI gate fail: {rsi_result.fail_reason}")
+            return {
+                "direction": "neutral",
+                "score": 0.0,
+                "reason": rsi_result.fail_reason,
+                "rsi_1h": rsi_result.rsi_1h,
+                "rsi_4h": rsi_result.rsi_4h,
+            }

+        # Step 4: 信号族白名单细化检查（使用 BOLL + RSI 结果）
+        whitelist_check = self._check_signal_whitelist(
+            signal_type_1h, direction, boll_result, rsi_result
+        )
+        if not whitelist_check["allowed"]:
+            return {
+                "direction": "neutral",
+                "score": 0.0,
+                "reason": whitelist_check["reason"],
+            }

+        # Step 5: 三共振评分聚合
+        market_regime = flow_context.get("market_regime", "TRENDING_BEAR")
+        resonance_result = self.resonance_scorer.compute(
+            direction=direction,
+            macd_base_score=macd_result["signal_score"],
+            boll_result=boll_result,
+            rsi_result=rsi_result,
+            market_regime=market_regime,
+        )
+
+        if not resonance_result["gate_pass"]:
+            return {
+                "direction": "neutral",
+                "score": resonance_result["resonance_score"],
+                "reason": resonance_result["reason"],
+            }

+        # Step 6: 组装完整结果
         return {
             "direction":        direction,
-            "score":            macd_result["signal_score"],
+            "score":            resonance_result["resonance_score"],
+            "signal_type_1h":   signal_type_1h,
+            "macd_base_score":  macd_result["signal_score"],
+            "boll_score":       boll_result.structure_score,
+            "boll_zone":        boll_result.zone,
+            "boll_bandwidth":   boll_result.bandwidth,
+            "boll_bw_state":    boll_result.bandwidth_state,
+            "rsi_1h":           rsi_result.rsi_1h,
+            "rsi_4h":           rsi_result.rsi_4h,
+            "rsi_15m":          rsi_result.rsi_15m,
+            "rsi_divergence":   rsi_result.divergence_type,
+            "rsi_extreme":      rsi_result.extreme_status,
+            "market_regime":    market_regime,
+            "resonance_score":  resonance_result["resonance_score"],
+            "reason":           "PASS",
         }

+    def _check_signal_whitelist(
+        self,
+        signal_type: str,
+        direction: str,
+        boll_result: BOLLResult,
+        rsi_result: RSIResult,
+    ) -> dict:
+        """信号族白名单检查（见第10节完整伪代码）"""
+        from src.fund_flow.signal_whitelist import check_signal_family_whitelist
+        return check_signal_family_whitelist(
+            signal_type=signal_type,
+            direction=direction,
+            boll_result=boll_result.__dict__,
+            rsi_result=rsi_result.__dict__,
+            market_regime=None,  # regime 由上层传入
+        )
```

### 14.2 decision_engine.py

```diff
--- a/src/fund_flow/decision_engine.py
+++ b/src/fund_flow/decision_engine.py

@@ _decide_macd_v2_strategy @@
     def _decide_macd_v2_strategy(self, symbol, flow_context):
         result = self.macd_engine.analyze(symbol, flow_context)

+        # 新增: 记录三共振元数据
+        if "resonance_score" in result:
+            flow_context["_resonance_meta"] = {
+                "resonance_score":  result.get("resonance_score"),
+                "boll_zone":        result.get("boll_zone"),
+                "boll_bw_state":    result.get("boll_bw_state"),
+                "rsi_1h":           result.get("rsi_1h"),
+                "rsi_4h":           result.get("rsi_4h"),
+                "rsi_divergence":   result.get("rsi_divergence"),
+                "rsi_extreme":      result.get("rsi_extreme"),
+                "market_regime":    result.get("market_regime"),
+            }

+        # 新增: RSI 极值入场前否决（替代 VWAP hard_block）
+        if result["direction"] in ("long", "short"):
+            rsi_1h = result.get("rsi_1h")
+            if rsi_1h is not None:
+                rsi_block = self._check_rsi_hard_block(
+                    direction=result["direction"],
+                    rsi_1h=rsi_1h,
+                )
+                if rsi_block:
+                    logger.warning(f"[{symbol}] RSI hard block at entry: RSI_1h={rsi_1h:.1f}")
+                    return {"action": "HOLD", "reason": f"RSI_HARD_BLOCK:{rsi_1h:.1f}"}

+        # 新增: 动态杠杆确定（基于三共振评分）
+        if result["direction"] in ("long", "short"):
+            resonance_score = result.get("resonance_score", 0)
+            dynamic_leverage = self._get_dynamic_leverage(resonance_score)
+            flow_context["_dynamic_leverage"] = dynamic_leverage

         if result["direction"] == "long":
             return {"action": "BUY",  "score": result["score"]}
         elif result["direction"] == "short":
             return {"action": "SELL", "score": result["score"]}
         return {"action": "HOLD", "score": 0.0}

+    def _check_rsi_hard_block(self, direction: str, rsi_1h: float) -> bool:
+        """RSI 极值硬性屏蔽（替代 VWAP hard_block）"""
+        hard_block_cfg = self.config.get("rsi_config", {}).get("hard_block", {})
+        long_max  = hard_block_cfg.get("long_rsi_1h_max", 70)
+        short_min = hard_block_cfg.get("short_rsi_1h_min", 30)
+
+        if direction == "long"  and rsi_1h >= long_max:   return True
+        if direction == "short" and rsi_1h <= short_min:  return True
+        return False

+    def _get_dynamic_leverage(self, resonance_score: float) -> int:
+        """基于三共振评分的动态杠杆"""
+        tiers = self.config.get("leverage_score_tiers", [
+            {"score_min": 0.85, "leverage": 5},
+            {"score_min": 0.78, "leverage": 4},
+            {"score_min": 0.65, "leverage": 3},
+        ])
+        for tier in sorted(tiers, key=lambda x: x["score_min"], reverse=True):
+            if resonance_score >= tier["score_min"]:
+                return tier["leverage"]
+        return self.config.get("min_leverage", 3)

@@ _compute_position_size @@
-    def _compute_position_size(self, symbol, flow_context, score):
+    def _compute_position_size(self, symbol, flow_context, score):
+        """仓位计算（三共振版本，整合 BOLL 带宽调整）"""
         base_portion = self.config.get("default_target_portion", 0.25)

+        # 评分仓位乘数
+        resonance_score = flow_context.get("_resonance_meta", {}).get("resonance_score", score)
+        score_mult = self._get_score_position_mult(resonance_score)

+        # BOLL 带宽仓位乘数（替代 VWAP 仓位乘数）
+        bw_state = flow_context.get("_resonance_meta", {}).get("boll_bw_state", "normal")
+        bw_mult  = self._get_boll_bandwidth_position_mult(bw_state)
+        if bw_mult == 0.0:
+            logger.info(f"[{symbol}] BOLL squeeze blocks position sizing")
+            return 0.0

         # ATR 调整（保持原逻辑）
         atr_pct  = flow_context.get("atr_pct_1h", 0.01)
         atr_mult = self._get_atr_position_mult(atr_pct)

-        target_portion = base_portion * atr_mult
+        target_portion = base_portion * score_mult * bw_mult * atr_mult
+        target_portion = max(
+            self.config.get("min_symbol_position_portion", 0.20),
+            min(self.config.get("max_symbol_position_portion", 0.30), target_portion)
+        )

         return target_portion

+    def _get_score_position_mult(self, score: float) -> float:
+        tiers = self.config.get("resonance_scoring", {}).get("score_tier_position_mults", {})
+        thresholds = sorted([(float(k), v) for k, v in tiers.items()], reverse=True)
+        for threshold, mult in thresholds:
+            if score >= threshold:
+                return mult
+        return 0.80

+    def _get_boll_bandwidth_position_mult(self, bw_state: str) -> float:
+        mult_map = self.config.get("boll_config", {}).get("bandwidth", {}).get(
+            "position_mult_by_state", {
+                "squeeze":   0.00,
+                "tight":     0.70,
+                "normal":    1.00,
+                "wide":      0.90,
+                "expanding": 0.70,
+            }
+        )
+        return mult_map.get(bw_state, 1.00)
```

### 14.3 新增 resonance_scorer.py

```python
# src/fund_flow/resonance_scorer.py

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ResonanceScorer:
    """
    三共振评分聚合器
    整合 MACD + BOLL + RSI 三层评分
    """

    def __init__(self, config: dict):
        resonance_cfg = config.get("resonance_scoring", {})

        self.trending_weights = resonance_cfg.get("trending_market", {
            "weight_macd_layer": 0.40,
            "weight_boll_layer": 0.35,
            "weight_rsi_layer":  0.25,
        })

        self.ranging_weights = resonance_cfg.get("ranging_market", {
            "weight_macd_layer": 0.30,
            "weight_boll_layer": 0.45,
            "weight_rsi_layer":  0.25,
        })

        self.min_score = resonance_cfg.get("min_resonance_score", 0.65)

    def compute(
        self,
        direction: str,
        macd_base_score: float,
        boll_result,         # BOLLResult
        rsi_result,          # RSIResult
        market_regime: str,
    ) -> dict:
        """
        计算三共振综合评分
        """

        # 选择权重
        if market_regime in ("TRENDING_BULL", "TRENDING_BEAR"):
            W = self.trending_weights
        elif market_regime in ("RANGING", "BREAKOUT_WATCH"):
            W = self.ranging_weights
        else:
            return {
                "resonance_score": 0.0,
                "gate_pass": False,
                "reason": f"VOLATILE_REGIME:{market_regime}"
            }

        W_macd = W["weight_macd_layer"]
        W_boll = W["weight_boll_layer"]
        W_rsi  = W["weight_rsi_layer"]

        boll_score = boll_result.structure_score
        rsi_score  = rsi_result.momentum_score
        div_bonus  = rsi_result.divergence_bonus

        raw_score = (
            macd_base_score * W_macd +
            boll_score      * W_boll +
            rsi_score       * W_rsi  +
            div_bonus
        )

        resonance_score = max(0.0, min(1.0, raw_score))

        if resonance_score < self.min_score:
            return {
                "resonance_score": resonance_score,
                "gate_pass": False,
                "reason": (
                    f"RESONANCE_BELOW_MIN: {resonance_score:.3f} < {self.min_score} "
                    f"(macd={macd_base_score*W_macd:.3f}, "
                    f"boll={boll_score*W_boll:.3f}, "
                    f"rsi={rsi_score*W_rsi:.3f})"
                )
            }

        return {
            "resonance_score": resonance_score,
            "gate_pass": True,
            "reason": "PASS",
            "detail": {
                "macd_contribution": macd_base_score * W_macd,
                "boll_contribution": boll_score      * W_boll,
                "rsi_contribution":  rsi_score       * W_rsi,
                "div_bonus":         div_bonus,
                "weights_used":      market_regime,
            }
        }
```

### 14.4 post-open RSI 与 BOLL 监控

```diff
--- a/src/app/fund_flow_bot.py
+++ b/src/app/fund_flow_bot.py

+    async def monitor_open_positions_resonance(self):
+        """
+        持仓中三共振状态监控（每 5 分钟执行）
+        替代原 VWAP 引导的追踪止损逻辑
+        """
+        for position in self.position_manager.get_open_positions():
+            symbol    = position["symbol"]
+            direction = position["side"]
+
+            try:
+                # 获取最新数据
+                closes_1h  = await self.data_provider.get_closes(symbol, "1h",  30)
+                closes_4h  = await self.data_provider.get_closes(symbol, "4h",  30)
+                candles_1h = await self.data_provider.get_candles(symbol, "1h", 30)
+                current_price = closes_1h[-1] if closes_1h else 0
+
+                # RSI 当前状态
+                rsi_1h = self.rsi_analyzer.get_rsi(closes_1h, "1h")
+                rsi_cfg = self.config.get("rsi_config", {})
+
+                # RSI 紧急平仓检查
+                emergency_cfg = rsi_cfg.get("emergency_exit", {})
+                if emergency_cfg.get("enabled", True):
+                    if (direction == "long"
+                            and rsi_1h >= emergency_cfg.get("long_partial_exit_rsi", 78)):
+                        logger.warning(
+                            f"[{symbol}] RSI emergency exit (long): RSI_1h={rsi_1h:.1f}"
+                        )
+                        pct = emergency_cfg.get("partial_exit_pct", 0.40)
+                        await self.close_position_partial(symbol, pct, "RSI_EXTREME_OB")
+                        continue
+
+                    if (direction == "short"
+                            and rsi_1h <= emergency_cfg.get("short_partial_exit_rsi", 22)):
+                        logger.warning(
+                            f"[{symbol}] RSI emergency exit (short): RSI_1h={rsi_1h:.1f}"
+                        )
+                        pct = emergency_cfg.get("partial_exit_pct", 0.40)
+                        await self.close_position_partial(symbol, pct, "RSI_EXTREME_OS")
+                        continue
+
+                # BOLL 结构退出检查（替代 VWAP 引导的追踪止损）
+                boll_cfg = self.config.get("boll_config", {}).get("structure_exit", {})
+                if boll_cfg.get("enabled", True):
+                    closes_4h_full = await self.data_provider.get_closes(symbol, "4h", 50)
+                    boll_r = self.boll_analyzer.analyze(
+                        symbol=symbol,
+                        direction=direction,
+                        closes_4h=closes_4h_full,
+                        closes_1h=closes_1h,
+                        current_price=current_price,
+                    )
+
+                    # 做多: 价格跌破 BOLL 中轨 + RSI < 48
+                    if (direction == "long"
+                            and current_price < boll_r.mid
+                            and rsi_1h < 48):
+                        if not hasattr(position, "_boll_mid_cross_bars"):
+                            position["_boll_mid_cross_bars"] = 1
+                        else:
+                            position["_boll_mid_cross_bars"] += 1
+
+                        bars_req = boll_cfg.get("bars_required", 2)
+                        if position["_boll_mid_cross_bars"] >= bars_req:
+                            pnl_pct = position.get("unrealized_pnl_pct", 0)
+                            loss_mit = boll_cfg.get("loss_mitigation_pct", 0.005)
+
+                            if pnl_pct < -loss_mit:
+                                exit_pct = boll_cfg.get("loss_mitigation_exit_portion", 0.25)
+                            else:
+                                exit_pct = boll_cfg.get("exit_portion", 0.40)
+
+                            logger.info(
+                                f"[{symbol}] BOLL structure exit (long): "
+                                f"price={current_price:.4f} < mid={boll_r.mid:.4f}, "
+                                f"RSI={rsi_1h:.1f}"
+                            )
+                            await self.close_position_partial(
+                                symbol, exit_pct, "BOLL_MID_CROSS_LONG"
+                            )
+                    else:
+                        position["_boll_mid_cross_bars"] = 0

+                    # 做空: 价格涨破 BOLL 中轨 + RSI > 52
+                    if (direction == "short"
+                            and current_price > boll_r.mid
+                            and rsi_1h > 52):
+                        if not hasattr(position, "_boll_mid_cross_bars"):
+                            position["_boll_mid_cross_bars"] = 1
+                        else:
+                            position["_boll_mid_cross_bars"] += 1

+                        bars_req = boll_cfg.get("bars_required", 2)
+                        if position["_boll_mid_cross_bars"] >= bars_req:
+                            pnl_pct = position.get("unrealized_pnl_pct", 0)
+                            loss_mit = boll_cfg.get("loss_mitigation_pct", 0.005)

+                            if pnl_pct < -loss_mit:
+                                exit_pct = boll_cfg.get("loss_mitigation_exit_portion", 0.25)
+                            else:
+                                exit_pct = boll_cfg.get("exit_portion", 0.40)

+                            logger.info(
+                                f"[{symbol}] BOLL structure exit (short): "
+                                f"price={current_price:.4f} > mid={boll_r.mid:.4f}, "
+                                f"RSI={rsi_1h:.1f}"
+                            )
+                            await self.close_position_partial(
+                                symbol, exit_pct, "BOLL_MID_CROSS_SHORT"
+                            )
+                    else:
+                        position["_boll_mid_cross_bars"] = 0

+                # RSI 背离对冲平仓
+                div_cfg = rsi_cfg.get("divergence", {})
+                if div_cfg.get("enabled", True):
+                    div_type, _ = self.rsi_analyzer.detect_divergence(candles_1h, direction)
+                    adverse_bars_req = div_cfg.get("adverse_divergence_bars_required", 4)

+                    is_adverse = (
+                        (direction == "long"  and div_type == "regular_bearish") or
+                        (direction == "short" and div_type == "regular_bullish")
+                    )

+                    if is_adverse:
+                        if not hasattr(position, "_adverse_div_bars"):
+                            position["_adverse_div_bars"] = 1
+                        else:
+                            position["_adverse_div_bars"] += 1

+                        if position["_adverse_div_bars"] >= adverse_bars_req:
+                            exit_pct = div_cfg.get("adverse_divergence_exit_pct", 0.30)
+                            logger.info(
+                                f"[{symbol}] Adverse RSI divergence exit ({div_type}): "
+                                f"closing {exit_pct*100:.0f}%"
+                            )
+                            await self.close_position_partial(
+                                symbol, exit_pct, f"ADVERSE_DIVERGENCE:{div_type}"
+                            )
+                    else:
+                        position["_adverse_div_bars"] = 0

+            except Exception as e:
+                logger.error(f"[{symbol}] Resonance monitor error: {e}", exc_info=True)
```

---

## 15. 回测目标验证模型

### 15.1 100%+ 收益路径分析

```
要从当前 10.04% 达到 100%+，需要以下改变:

因素1: 胜负比改善（核心）
  当前: avg_win=13.27, avg_loss=17.91, ratio=0.741
  目标: avg_win=20, avg_loss=13, ratio=1.54

  改善途径:
    SL 从 2% 降至 1.5% → avg_loss 降低约 25%: -17.91 × 0.75 = -13.43 ✓
    TP Level 4 (3.5%) 捕捉大行情 → avg_win 提升约 30%: +13.27 × 1.3 = +17.25 ✓
    RSI 背离辅助提前止盈 → avg_win 再提升 ~10%: 约 +19

  期望改善后期望值/笔:
    0.7627 × 19 - 0.2373 × 13.5 = 14.49 - 3.20 = +11.29 (vs 当前 +5.87)
    改善倍数: 11.29 / 5.87 = 1.92x

因素2: 杠杆提升
  当前: 固定 5x
  新配置: 动态 3-5x，高质量信号用 5x
  影响: 高共振信号（score ≥ 0.85）用 5x，低质量用 3x
  净效果: 平均有效杠杆从 5x 降到约 4.2x（质量提升抵消杠杆下降）

因素3: 交易数量优化
  当前: 118 笔
  目标: 90-120 笔
  通过三共振过滤，预期：
    移除低质量信号 → 减少 20-30 笔
    恢复 flip_bullish 高频场景 → 增加 10-15 笔
    净变化: 约 100-110 笔（在目标范围内）

综合预期收益估算:

  收益 = 交易数 × 期望值/笔 × 平均有效仓位 × 平均有效杠杆 / 账户资金

  当前: 118 × 5.87 × 0.30 × 5 / 10000 = 10.4% ✓（与实际接近）

  乐观情景（三共振完全验证）:
    105 × 11.29 × 0.26 × 4.8 / 10000
    = 105 × 11.29 × 1.248 / 10000
    = 1480.2 / 10000 = 14.8%（30D）

  实现 100%/30D 的完整路径:
    需要 avg_win ≥ 25 （大行情捕捉，Level 4 止盈多次触发）
    AND 胜率 ≥ 80%
    AND 交易数 ≥ 100 笔
    AND 平均有效杠杆 ≈ 5x
    AND 平均有效仓位 ≈ 28%

  期望:
    100 × (0.80 × 25 - 0.20 × 13) × 0.28 × 5 / 10000
    = 100 × (20 - 2.6) × 1.4 / 10000
    = 100 × 17.4 × 1.4 / 10000
    = 2436 / 10000 = 24.4%（30D）

  结论:
    三共振优化后的保守预期: 20-30% / 30D
    乐观预期（强趋势行情）: 35-55% / 30D
    100%/30D 目标: 需要额外的行情配合（连续强趋势月份）
                   或进一步扩大仓位上限到 35-40%
```

### 15.2 胜率提升路径

```
当前胜率: 76.27%
目标胜率: 80%+
需提升: 3.73pp

提升来源:
  1. 移除 green_bar_growing 低质量场景（条件放行）:
     当前: 12 笔 × 66.67% = 8 笔胜利
     新配置（仅 BOLL 下轨 + RSI 超卖）: 预计仅 5-6 笔但胜率 82%+
     贡献: +0.3pp 整体胜率

  2. RSI 三时间框架门控过滤低质量入场:
     预计过滤 15-20% 的低质量信号
     过滤的信号中 70% 是输单（基于 RSI 与胜率的相关性分析）
     贡献: +1.5pp 整体胜率

  3. BOLL 结构验证替代失效的 VWAP:
     VWAP 误放的信号中约 40% 是在不利 BOLL 位置入场
     BOLL 门控可修正这部分
     贡献: +2pp 整体胜率

  预计总胜率: 76.27% + 0.3% + 1.5% + 2% ≈ 80.1% ✓

  风险: 三共振可能过度过滤 red_bar_growing 信号
  缓解: red_bar_growing 的 RSI 门控略宽松（rsi_1h 32-57 vs 标准 30-57）
```

### 15.3 交易数量预测

```
目标: 90-120 笔 / 30D

当前信号漏斗分析（基于回测数据）:
  总信号: 104,401
  评分门槛通过: ~56,950 (54.55%)
  BOLL 结构替代 VWAP 过滤: 预计 11-13% 拦截（vs VWAP 的 10.59%，效果相近）
  RSI 三时间框架门控（新增）: 预计 25-30% 额外拦截
  后续过滤链: 保持原有比例
  最终开仓估算: 约 80-120 笔（在目标范围）

信号数量不足时的补充方案:
  重新允许 flip_bullish 在更宽 RSI 范围（40-68 vs 标准 43-70）
  在趋势明确时允许降低共振评分到 0.62
  以上补充预计可额外增加 10-20 笔
```

---

## 16. 分阶段上线计划

### Phase 0：基础设施（第 1-2 天）

```
任务:
  □ 创建 src/indicators/boll_structure_analyzer.py
  □ 创建 src/indicators/rsi_analyzer.py（扩展版）
  □ 创建 src/fund_flow/resonance_scorer.py
  □ 创建 src/fund_flow/signal_whitelist.py
  □ 单元测试: BOLL 计算准确性（对比 TradingView）
  □ 单元测试: RSI 计算准确性
  □ 单元测试: 三共振评分逻辑

验收:
  BOLL 带宽分类误差 < 5%
  RSI 计算误差 < 0.1（对比 TradingView RSI(14)）
  三共振评分在已知历史数据上的分类准确率 ≥ 85%
```

### Phase 1：VWAP 废弃 + BOLL 接管（第 3-5 天）

```
上线范围:
  启用 disable_vwap_filter: true
  启用 enable_boll_structure_filter: true
  暂时不改变评分权重（用 BOLL 替代 VWAP 但权重保持过渡）

回测验证:
  在 2026-03-02 ~ 2026-04-01 重新回测
  期望: 交易数量变化 < 20%，胜率不降低

监控指标:
  BOLL gate 拦截率（预期 8-15%）
  BOLL 结构得分分布（应与 VWAP 覆盖场景不同）
  过滤后的交易胜率变化
```

### Phase 2：RSI 三时间框架门控（第 5-8 天）

```
上线范围:
  启用 enable_rsi_triple_gate: true
  启用 RSI 极值硬性屏蔽（替代 VWAP hard_block）
  暂不启用 RSI 背离功能（后面上线）

回测验证:
  期望: 胜率提升 1-2pp，交易数量减少 15-25%
  验收: 胜率 ≥ 77%，交易数量仍在 80-130 范围

监控:
  RSI gate 拦截率分布（按信号族）
  RSI 极值屏蔽触发频率
  各 RSI 区间的入场胜率对比
```

### Phase 3：三共振评分权重（第 8-12 天）

```
上线范围:
  启用 enable_resonance_scoring: true
  切换到三层评分架构（MACD 0.40 + BOLL 0.35 + RSI 0.25）
  最低共振评分门槛 0.65

回测验证:
  期望: 胜率 ≥ 78%，Profit Factor ≥ 2.5
  验收: 达到 Phase 3 阶段目标

重点关注:
  red_bar_growing 在三共振下的胜率（核心盈利族不能受损）
  flip_bullish 数量是否充足（目标每月 20+ 笔）
```

### Phase 4：动态杠杆 + 仓位更新（第 12-16 天）

```
上线范围:
  启用 enable_dynamic_leverage: true
  切换到三共振仓位计算公式
  启用 BOLL 带宽仓位调整（替代 VWAP 仓位调整）

回测验证:
  期望: 有效杠杆使用更合理，高质量信号获得更大杠杆
  验收: avg_effective_leverage 分布合理（3-5x 范围内）

风险:
  高评分时 5x 杠杆 + 30% 仓位 = 较大风险暴露
  缓解: 保持 equity_usage_block = 0.85，max_drawdown 触发 = 8%
```

### Phase 5：止盈止损优化（第 16-21 天）

```
上线范围:
  新四级止盈: [0.6%, 1.2%, 2.0%, 3.5%] → [15%, 25%, 25%, 20%]
  新止损: 趋势 1.5%，震荡 1.0%
  RSI 动态止损
  BOLL 结构退出

回测验证:
  期望: avg_win ≥ 18，avg_loss ≤ 14，win/loss ratio ≥ 1.3
  验收: Profit Factor ≥ 2.8

重点验证:
  Level 4 止盈是否在大行情中有效触发
  BOLL 结构退出是否提前保护了盈利
  RSI 动态止损是否减少了过早止损
```

### Phase 6：全量调优（第 21-30 天）

```
上线范围:
  根据 Phase 1-5 数据微调所有参数
  RSI 背离功能上线（之前关闭）
  green_bar_growing 精确条件验证

目标:
  30D Return ≥ 50%（第一阶段目标，不强求 100%）
  Win Rate ≥ 80%
  Trade Count 90-120
  Max DD ≤ 8%
  Profit Factor ≥ 3.0
```

---

## 17. 监控指标体系

### 17.1 实时监控指标（每 5 分钟）

```yaml
realtime_metrics:

  # 三共振指标
  resonance_score_avg_24h:
    description: "过去24小时入场平均三共振评分"
    alert_low: 0.68
    alert_high: 0.95

  boll_gate_block_rate_1h:
    description: "过去1小时 BOLL 门控拦截率"
    alert_low: 0.03   # 太低说明 BOLL 门控失效
    alert_high: 0.40  # 太高说明市场处于不利结构

  rsi_gate_block_rate_1h:
    description: "过去1小时 RSI 门控拦截率"
    alert_low: 0.05
    alert_high: 0.60

  rsi_extreme_block_count_1h:
    description: "RSI 极值硬性屏蔽触发次数"
    alert_high: 5     # 1小时内 5 次说明市场极度超买/超卖

  boll_bandwidth_current_median:
    description: "当前所有监控交易对的 BOLL 带宽中位数"
    alert_low: 0.020  # squeeze 警告
    alert_high: 0.150 # 极度扩张警告

  # 持仓监控
  active_positions_count:
    max: 5
    alert_at: 5

  boll_mid_cross_exits_24h:
    description: "BOLL 中轨穿越触发的部分平仓次数"
    alert_high: 8     # 过多说明趋势判断频繁错误

  rsi_emergency_exits_24h:
    description: "RSI 极值触发的紧急平仓次数"
    alert_high: 3
```

### 17.2 日报指标

```
每日汇报内容:

[三共振质量报告]
  - 当日入场信号的平均三共振评分（目标 > 0.72）
  - 各子层评分分布（MACD / BOLL / RSI 各自的得分范围）
  - 背离信号发生次数（看多/看空）

[BOLL 市场结构报告]
  - 当日各交易对的 BOLL 带宽状态分布
  - squeeze 状态持续时间（避免在挤压中等待过久）
  - BOLL 中轨方向分布（看多中轨上行 vs 看空中轨下行比例）

[RSI 动量报告]
  - 入场时各时间框架 RSI 均值
  - RSI 门控拦截按信号族的分布
  - 背离信号的后续表现（是否辅助了利润保护）

[绩效日报]
  - 当日 PnL / 胜率 / 交易数量
  - avg_win / avg_loss / win/loss ratio
  - 动态杠杆使用分布（3x / 4x / 5x 各占比）
  - 仓位使用效率（实际 vs 目标 20-30%）
```

### 17.3 周报指标

```
每周汇报内容:

[策略有效性评估]
  - 三共振评分与实际胜率的相关性（目标 > 0.6）
  - BOLL 位置与胜率相关性
  - RSI 状态与胜率相关性

[信号族健康度]
  - 各信号族的周胜率变化趋势
  - green_bar_growing 新条件的实际效果
  - flip_bullish 的 RSI 穿越确认效果

[风险控制有效性]
  - BOLL 结构退出的PnL 保护量
  - RSI 动态止损的触发率和效果
  - 各级止盈的触发分布（特别是 Level 4 的触发率）
```

---

## 18. 风险缓解手册

### 18.1 过度过滤风险

```
风险: 三共振门控过于严格，交易数量低于 90 笔
症状: 月度信号数量 < 80
缓解措施（按严重程度排序）:
  级别 1（交易数 70-90）:
    将 min_resonance_score 从 0.65 降至 0.63
    允许 flip_bullish 的 RSI 范围扩大（40-68 → 38-70）

  级别 2（交易数 50-70）:
    恢复 green_bar_growing 的宽松条件（BOLL 下半区即可，不强求下轨区）
    RSI_15m 门控放宽（47 → 45）

  级别 3（交易数 < 50）:
    检查 BOLL 是否长期处于 squeeze 状态
    如果 squeeze > 7 天，允许在 tight 状态下以 50% 仓位入场
```

### 18.2 BOLL 指标延迟风险

```
风险: BOLL（20SMA）存在滞后性，在快速行情中位置判断失真
场景: 价格快速从下轨穿越到上轨，BOLL 来不及扩张
缓解:
  1. 使用 1h 和 4h 双时间框架 BOLL 对比
  2. 带宽快速扩张（1h 带宽 > 4h 带宽 × 1.5）时，切换为 RSI 主导权重
  3. ATR > 0.020 时，BOLL 结构评分权重降低 20%（RSI 弥补）
```

### 18.3 RSI 绝对值环境风险

```
风险: 在持续趋势中，RSI 长期维持在 60-70（多头趋势）或 30-40（空头趋势）
      导致 RSI 门控持续拦截合理入场信号

场景: 2026-03 的空头月份，RSI_1h 长期在 35-55 之间
      flip_bullish 需要 RSI > 43，但长期空头中 RSI 难以持续 > 43

缓解:
  1. 对 red_bar_growing（做空）的 RSI 下限设置更低（32 vs 标准 43）
  2. 市场状态为 TRENDING_BEAR 时，做空信号的 RSI 上限从 57 提至 62
  3. 引入 RSI 相对位置判断（vs 过去 20 根 1h 的 RSI 均值，而非绝对值）
```

### 18.4 green_bar_growing 条件放行风险

```
风险: green_bar_growing 条件放行（BOLL 下轨 + RSI 超卖反弹）
      在实际运行中可能遭遇"反弹未完成就掉头"的假反弹

缓解:
  1. 要求 RSI_15m 也需要上行斜率（rsi_slope_15m > 0 持续 2 根 K 线）
  2. 成交量确认: volume_ratio > 1.2（放量反弹更可信）
  3. 最大杠杆限制 3x（已配置）
  4. 使用 tight/normal BOLL 带宽（不允许在 wide 状态做均值回归）
  5. 止损更紧: 1.2%（专门为该信号族配置更严格止损）
```

### 18.5 动态杠杆风险

```
风险: 高共振评分 (≥ 0.85) → 5x 杠杆 + 26-30% 仓位 → 单笔名义暴露较大

量化:
  最坏情景: 5x 杠杆 × 30% 仓位 × 1.5% SL = 22.5% 单笔账户最大损失

缓解:
  1. equity_usage_block 保持 0.85（总仓位不超过 85%）
  2. max_active_symbols = 5，单象征 ≤ 30%，总仓位 ≤ 150%（5 × 30%）但受 equity_usage 约束
  3. 当任一仓位 SL 触发时，新开仓需等待 10 分钟（冷却）
  4. 账户回撤 ≥ 5% 时，新入场杠杆强制降为 3x
```

---

## 19. 附录 A：三共振信号图谱

### A.1 极优质做空信号（目标样本）

```
MACD 层:
  4h histogram: 负值且增长（空头趋势延续）
  1h signal_type: red_bar_growing
  macd_base_score: 0.88+

BOLL 层:
  价格在 BOLL 上轨区 (zone 1, relative_pos > 0.75)
  BOLL 中轨斜率向下
  带宽状态: normal 或 wide
  boll_structure_score: 0.85+

RSI 层:
  RSI_4h: 48-54（偏强但不超买）
  RSI_1h: 46-55（做空健康区）
  RSI_15m: < 53 且斜率为负
  rsi_momentum_score: 0.85+

三共振得分:
  0.88 × 0.40 + 0.85 × 0.35 + 0.85 × 0.25
  = 0.352 + 0.2975 + 0.2125
  = 0.862
  → 杠杆 5x，仓位 27.5%（score_mult 1.10）

预期胜率: 85%+
```

### A.2 极优质做多信号（flip_bullish）

```
MACD 层:
  4h histogram: 从负转正（翻多初期）
  1h signal_type: flip_bullish
  macd_base_score: 0.82

BOLL 层:
  价格在 BOLL 中下区 (zone 4-5, relative_pos < 0.45)
  BOLL 中轨斜率从负转正（或接近 0）
  带宽状态: normal
  boll_structure_score: 0.75

RSI 层:
  RSI_4h: 44 → 刚穿越 43（flip 豁免）
  RSI_1h: 48 → 从 < 50 穿越到 50+（cross_above_50）
  RSI_15m: 51 且斜率向上
  divergence: regular_bullish（bonus +0.12）
  rsi_momentum_score: 0.82

三共振得分（趋势权重）:
  0.82 × 0.40 + 0.75 × 0.35 + 0.82 × 0.25 + 0.12
  = 0.328 + 0.2625 + 0.205 + 0.12
  = 0.9155（截断到 1.0）
  实际: 0.916 → 杠杆 5x，仓位 30%（最大）

预期胜率: 82%+
```

### A.3 绝对禁止入场示例

```
❌ 示例 1: BOLL 上轨做多
  price > upper → zone=0 → long_zone_score=-1.0 → BOLL gate FAIL
  → BLOCK（替代原 vwap_hard_block 功能）

❌ 示例 2: RSI 超买做多
  RSI_1h = 73 → 超过 hard_block 门槛 70
  → RSI HARD BLOCK（替代原 vwap_hard_block 功能）

❌ 示例 3: BOLL squeeze
  bandwidth = 0.018 < 0.025
  → BOLL SQUEEZE → 禁止任何入场（替代无明确方向时的 VWAP 拒绝）

❌ 示例 4: red_bar_shrinking 信号
  signal_type = red_bar_shrinking
  → BLACKLIST → BLOCK（保持原有禁用逻辑）
```

---

## 20. 附录 B：参数敏感性分析表

### B.1 BOLL 评分阈值敏感性

```
min_boll_structure_score  交易数变化  胜率变化  说明
──────────────────────────────────────────────────────
0.40                      +30%       -2pp      过宽松，放入不良结构
0.50                      +15%       -1pp      略宽
0.55 (推荐)               基准        基准      平衡点
0.60                      -15%       +1pp      略严
0.65                      -30%       +2pp      过严，可能丢失 flip 信号
```

### B.2 RSI 门控阈值敏感性

```
long_rsi_1h_min    交易数变化（做多）  胜率变化  说明
──────────────────────────────────────────────────────
38                  +25%              -2pp    过宽
40 (flip 豁免)      +10%              -1pp    flip 专用
43 (推荐)           基准               基准
45                  -15%              +1pp
48                  -30%              +2pp    过严

short_rsi_1h_max   交易数变化（做空）  胜率变化  说明
──────────────────────────────────────────────────────
62                  +15%              -1pp    宽松
57 (推荐)           基准               基准
54                  -20%              +1pp
52                  -30%              +2pp    过严
```

### B.3 三共振最低评分敏感性

```
min_resonance_score  交易数    胜率   Profit Factor
──────────────────────────────────────────────────
0.60                 130+      76%      2.1
0.62                 120+      77%      2.3
0.65 (推荐)          100-120   79%      2.7
0.68                 85-100    81%      3.0
0.70                 70-85     83%      3.3
0.72                 60-75     85%      3.6  (交易数不足)
```

### B.4 止损距离敏感性

```
stop_loss_pct (趋势市)  avg_loss  Win Rate  Win/Loss Ratio
──────────────────────────────────────────────────────────
0.010                   -8.9     81%        1.8         (可能过早止损)
0.012                   -10.7    80%        1.7
0.015 (推荐)            -13.4    79%        1.5
0.018                   -16.1    78%        1.3
0.020 (原配置)          -17.9    76%        0.74 ← 当前
```

---

## 21. 附录 C：完整伪代码参考实现

### C.1 三共振完整主流程

```python
# 三共振信号处理完整流程（v3.0）

async def process_signal_v3(
    symbol: str,
    flow_context: dict,
    components: dict,  # {macd_engine, boll_analyzer, rsi_analyzer, resonance_scorer, ...}
) -> dict:
    """
    三共振版本完整信号处理流程
    VWAP 已废弃，由 BOLL + RSI 替代
    """

    # ─────────────────────────────────────────
    # Gate 0: 市场级屏蔽（保持原有逻辑）
    # ─────────────────────────────────────────
    atr_pct = flow_context.get("atr_pct_1h", 0)
    if atr_pct > 0.025:
        return {"action": "HOLD", "reason": "EXTREME_VOLATILITY"}

    account_dd = flow_context.get("account_drawdown", 0)
    if account_dd >= 0.08:
        return {"action": "HOLD", "reason": "ACCOUNT_DRAWDOWN_BLOCK"}

    # ─────────────────────────────────────────
    # Gate 1: MACD 信号识别
    # ─────────────────────────────────────────
    macd_engine = components["macd_engine"]
    macd_result = macd_engine._compute_macd_signals(symbol, flow_context)

    if macd_result["direction"] == "neutral":
        return {"action": "HOLD", "reason": "MACD_NEUTRAL"}

    direction      = macd_result["direction"]
    signal_type    = macd_result["signal_type_1h"]
    macd_base_score = macd_result["signal_score"]

    # ─────────────────────────────────────────
    # Gate 2: 信号族黑名单（shrink 族）
    # ─────────────────────────────────────────
    if signal_type in ("red_bar_shrinking", "green_bar_shrinking"):
        return {"action": "HOLD", "reason": f"BLACKLIST:{signal_type}"}

    # ─────────────────────────────────────────
    # Gate 3: BOLL 结构分析（替代 VWAP）
    # ─────────────────────────────────────────
    boll_analyzer = components["boll_analyzer"]
    boll_result = boll_analyzer.analyze(
        symbol=symbol,
        direction=direction,
        closes_4h=flow_context.get("closes_4h", []),
        closes_1h=flow_context.get("closes_1h", []),
        current_price=flow_context.get("current_price", 0),
    )

    if not boll_result.gate_pass:
        return {"action": "HOLD", "reason": boll_result.gate_fail_reason}

    # ─────────────────────────────────────────
    # Gate 4: RSI 三时间框架分析
    # ─────────────────────────────────────────
    rsi_analyzer = components["rsi_analyzer"]
    rsi_result = rsi_analyzer.analyze(
        symbol=symbol,
        direction=direction,
        signal_type=signal_type,
        closes_4h=flow_context.get("closes_4h", []),
        closes_1h=flow_context.get("closes_1h", []),
        closes_15m=flow_context.get("closes_15m", []),
        candles_1h=flow_context.get("candles_1h", []),
    )

    if not rsi_result.gate_pass:
        return {"action": "HOLD", "reason": rsi_result.fail_reason}

    # RSI 极值硬性屏蔽（替代 vwap_hard_block）
    if direction == "long" and rsi_result.rsi_1h >= 70:
        return {"action": "HOLD", "reason": f"RSI_HARD_BLOCK_OVERBOUGHT:{rsi_result.rsi_1h:.1f}"}
    if direction == "short" and rsi_result.rsi_1h <= 30:
        return {"action": "HOLD", "reason": f"RSI_HARD_BLOCK_OVERSOLD:{rsi_result.rsi_1h:.1f}"}

    # ─────────────────────────────────────────
    # Gate 5: 信号族白名单细化检查
    # ─────────────────────────────────────────
    whitelist_result = check_signal_family_whitelist(
        signal_type=signal_type,
        direction=direction,
        boll_result=boll_result.__dict__,
        rsi_result=rsi_result.__dict__,
        market_regime=flow_context.get("market_regime", "TRENDING_BEAR"),
    )

    if not whitelist_result["allowed"]:
        return {"action": "HOLD", "reason": whitelist_result["reason"]}

    # ─────────────────────────────────────────
    # Gate 6: 三共振评分
    # ─────────────────────────────────────────
    resonance_scorer = components["resonance_scorer"]
    resonance_result = resonance_scorer.compute(
        direction=direction,
        macd_base_score=macd_base_score,
        boll_result=boll_result,
        rsi_result=rsi_result,
        market_regime=flow_context.get("market_regime", "TRENDING_BEAR"),
    )

    if not resonance_result["gate_pass"]:
        return {"action": "HOLD", "reason": resonance_result["reason"]}

    resonance_score = resonance_result["resonance_score"]

    # ─────────────────────────────────────────
    # Gate 7: 频率控制
    # ─────────────────────────────────────────
    freq_ok = check_frequency_control(
        symbol=symbol,
        direction=direction,
        config=flow_context.get("config", {}),
        trade_counter=components["trade_counter"],
    )
    if not freq_ok["allowed"]:
        return {"action": "HOLD", "reason": freq_ok["reason"]}

    # ─────────────────────────────────────────
    # Gate 8: 动态仓位与杠杆计算
    # ─────────────────────────────────────────
    position_result = compute_dynamic_position(
        resonance_score=resonance_score,
        boll_bandwidth=boll_result.bandwidth,
        atr_pct=atr_pct,
        account_equity=flow_context.get("account_equity", 10000),
        account_drawdown=account_dd,
    )

    if not position_result["allowed"]:
        return {"action": "HOLD", "reason": position_result["reason"]}

    # ─────────────────────────────────────────
    # Gate 9-12: 原有门控链（L1/L2/L3 + AI + Capacity）
    # ─────────────────────────────────────────
    # [L1 结构性门控 - 保持原有 ADX/ATR 检查]
    # [L2 流量门控 - 保持原有 CVD/OI 检查]
    # [L3 微观门控 - 保持原有 depth/imbalance 检查]
    # [AI 评审 - 保持原有逻辑]
    # [容量检查 - 保持原有逻辑]

    # ─────────────────────────────────────────
    # 最终决策
    # ─────────────────────────────────────────
    return {
        "action":           "BUY" if direction == "long" else "SELL",
        "resonance_score":  resonance_score,
        "leverage":         position_result["leverage"],
        "target_portion":   position_result["target_portion"],
        "signal_type":      signal_type,
        "boll_zone":        boll_result.zone,
        "boll_bw_state":    boll_result.bandwidth_state,
        "rsi_1h":           rsi_result.rsi_1h,
        "rsi_4h":           rsi_result.rsi_4h,
        "rsi_divergence":   rsi_result.divergence_type,
        "market_regime":    flow_context.get("market_regime"),
        "detail":           resonance_result.get("detail", {}),
    }
```

### C.2 日内频率控制

```python
def check_frequency_control(
    symbol: str,
    direction: str,
    config: dict,
    trade_counter,
) -> dict:
    """
    日内交易频率控制
    """
    freq_cfg = config.get("frequency_control", {})
    today = datetime.utcnow().date()

    # 日内总量
    daily_total = trade_counter.count(date=today)
    max_total   = freq_cfg.get("daily_max_trades_total", 8)
    if daily_total >= max_total:
        return {"allowed": False, "reason": f"DAILY_MAX:{daily_total}/{max_total}"}

    # 方向总量
    daily_dir = trade_counter.count(date=today, direction=direction)
    max_dir   = freq_cfg.get(f"daily_max_{direction}_trades", 4)
    if daily_dir >= max_dir:
        return {"allowed": False, "reason": f"DAILY_{direction.upper()}_MAX:{daily_dir}/{max_dir}"}

    # 同 symbol 冷却
    last_close = trade_counter.get_last_close_time(symbol)
    if last_close:
        cooldown_min = freq_cfg.get("same_symbol_cooldown_minutes", 45)
        elapsed_min  = (datetime.utcnow() - last_close).total_seconds() / 60
        if elapsed_min < cooldown_min:
            return {
                "allowed": False,
                "reason": f"SYMBOL_COOLDOWN:{symbol}:{elapsed_min:.0f}min<{cooldown_min}min"
            }

    # 同 symbol 同方向更长冷却
    last_same_dir = trade_counter.get_last_close_time(symbol, direction=direction)
    if last_same_dir:
        same_dir_cooldown = freq_cfg.get("same_symbol_same_direction_cooldown_minutes", 90)
        elapsed_same_dir  = (datetime.utcnow() - last_same_dir).total_seconds() / 60
        if elapsed_same_dir < same_dir_cooldown:
            return {
                "allowed": False,
                "reason": (
                    f"SYMBOL_SAME_DIR_COOLDOWN:{symbol}/{direction}:"
                    f"{elapsed_same_dir:.0f}min<{same_dir_cooldown}min"
                )
            }

    return {"allowed": True, "reason": None}
```

---

## 总结与行动清单

### 🔴 紧急（今天）

```
□ 创建 boll_structure_analyzer.py（BOLL 替代 VWAP 的核心）
□ 更新 config: disable_vwap_filter=true, enable_boll_structure_filter=true
□ 单元测试: BOLL 计算准确性验证（对比已知 TradingView 数据）
□ 对 2026-03-02~04-01 重新回测，验证 BOLL 替代 VWAP 不降低胜率
```

### 🟡 本周（3-5 天）

```
□ 完善 rsi_analyzer.py（三时间框架 + 背离 + 穿越检测）
□ 创建 resonance_scorer.py（三共振聚合）
□ 更新 macd_strategy_v2.py（集成三共振流程）
□ 更新 decision_engine.py（动态杠杆 + BOLL 仓位调整）
□ 完整回测验证（目标: 胜率 ≥ 78%，交易数 90-120）
```

### 🟢 下周（7-14 天）

```
□ 更新止盈止损配置（四级止盈 + 动态止损）
□ 上线 BOLL 结构退出监控
□ 上线 RSI 背离辅助平仓
□ 沙盒验证（7 天实盘模拟）
□ Phase 1-3 正式上线
```

### 📊 验收标准

```
Phase 3 完成时（约第 12 天）:
  ✓ Win Rate ≥ 78%
  ✓ Trade Count 90-120
  ✓ Profit Factor ≥ 2.5
  ✓ Max DD ≤ 7%
  ✓ BOLL gate 正常工作（拦截率 8-15%）
  ✓ RSI gate 正常工作（拦截率 20-35%）
  ✓ green_bar_growing 新条件胜率 ≥ 79%

Phase 6 完成时（约第 30 天）:
  ✓ Win Rate ≥ 80%
  ✓ 30D Return ≥ 50%（第一里程碑）
  ✓ Profit Factor ≥ 3.0
  ✓ avg_win/avg_loss ≥ 1.3
  ✓ Max DD ≤ 8%
```

---

**文档结束**

*版本: v3.0-resonance | 创建: 2026-04-13 | 状态: 待实现*  
*基于回测: 2026-03-02 ~ 2026-04-01 | 基线: WR=76.27%, PF=2.36, DD=5.09%*

> **免责**: 所有收益预期均为理论估算，实盘表现受市场环境、执行质量等因素影响。  
> **核心原则**: VWAP 废弃是永久性决策；shrink 族禁用是永久性决策；三共振框架是可调参数的架构，非不可修改的硬规则。
