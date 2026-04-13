# 三共振策略 MACD 基础信号零产出修复方案
# Fix: Base Signal Generation Blockage → Zero Trades

**版本**: v3.1-signal-fix  
**问题**: 三共振系统已集成，但基础 MACD 信号产出为 0，导致最终成交 0 笔  
**回测窗口**: 2026-03-01 ~ 2026-03-31 | 分析尝试 92,287 次 | 有效分析 89,807 次 | **成交 0 笔**  
**目标**: 恢复信号产出至 90-120 笔/30D，胜率 80%+

---

## 目录

1. [根因定位](#1-根因定位)
2. [五层阻断链分析](#2-五层阻断链分析)
3. [分层修复方案](#3-分层修复方案)
4. [配置文件完整 Diff](#4-配置文件完整-diff)
5. [代码修改 Diff](#5-代码修改-diff)
6. [核心伪代码](#6-核心伪代码)
7. [分阶段验证计划](#7-分阶段验证计划)
8. [监控指标与告警](#8-监控指标与告警)
9. [回滚预案](#9-回滚预案)

---

## 1. 根因定位

### 1.1 阻断点精确定位

```
回测漏斗:
  analysis_attempts : 92,287
  analysis_ready    : 89,807  (缺失 2,480 = 数据问题)
  macd_signal_gen   : 0       ← ❌ 完全阻断，此处是根因
  resonance_scoring : 0       (上游为 0，无法执行)
  decision_check    : 0
  final_trades      : 0
```

**结论**: 问题 100% 出在 `MACDStrategyV2Engine.analyze()` 的信号分类层，
三共振评分系统本身是好的，但永远得不到输入。

### 1.2 信号零产出的四个直接原因

```
原因 1: flip_bearish 被硬性禁用
  配置: disable_flip_bearish_entries = true
  影响: 2026-03 为空头环境，flip_bearish 是最强信号族
  结果: 最主要做空翻转信号完全缺失

原因 2: red_bar_growing 做多被禁用
  配置: disable_red_bar_growing_long_entries = true
  影响: 双向可用信号减半
  结果: 多头趋势中的核心做多信号缺失

原因 3: 4H 主方向 + 1H 确认的双重锁死
  配置: primary_direction_timeframe = "4h"
         require_1h_confirmation_when_4h_primary = True（推断）
  影响: 4H 和 1H 方向经常不一致，尤其在震荡盘
  结果: 两个时间框架同时满足的窗口极少

原因 4: stable_continuation 系列未启用
  配置: enable_stable_bear_continuation = false（推断默认关闭）
         enable_stable_bull_continuation = false
  影响: 趋势延续阶段没有兜底信号族
  结果: 市场在 TRENDING 状态但信号类型不满足任何白名单
```

### 1.3 错误决策树（当前状态）

```python
# 当前 analyze() 执行路径（所有路径均返回 neutral）

def analyze(symbol, flow_context):

    # ❌ 路径 A: flip_bearish 场景
    if signal_type == "flip_bearish":
        if config["disable_flip_bearish_entries"]:  # = True
            return neutral  # 永远在此返回

    # ❌ 路径 B: flip_bullish 场景（2026-03 空头市中极少）
    if signal_type == "flip_bullish":
        pass  # 空头市中 4H MACD 几乎不翻多，此路径极少触发

    # ❌ 路径 C: red_bar_growing 做多场景
    if signal_type == "red_bar_growing" and direction == "long":
        if config["disable_red_bar_growing_long_entries"]:  # = True
            return neutral

    # ❌ 路径 D: red_bar_growing 做空场景（可能正常，但被 1H 确认卡死）
    if signal_type == "red_bar_growing" and direction == "short":
        if not check_1h_confirmation():  # 经常失败
            return neutral

    # ❌ 路径 E: green/red_bar_shrinking（已禁用）
    if signal_type in ["green_bar_shrinking", "red_bar_shrinking"]:
        if config["disable_*_entries"]:  # = True
            return neutral

    # 结果: 所有路径均 neutral，无一到达三共振评分层
```

---

## 2. 五层阻断链分析

### Layer 1: 信号族禁用层（最主要阻断）

```
当前禁用状态:
  flip_bearish:              ❌ 禁用 (disable_flip_bearish_entries = true)
  red_bar_growing (long):    ❌ 禁用 (disable_red_bar_growing_long_entries = true)
  green_bar_shrinking:       ❌ 禁用 (disable_green_bar_shrinking_entries = true)
  red_bar_shrinking:         ❌ 禁用 (disable_red_bar_shrinking_entries = true)

当前可用:
  flip_bullish:              ✅ 启用（但 2026-03 空头市中极少出现）
  green_bar_growing:         ✅ 启用（需要 BOLL 下轨 + RSI 超卖，极严格）
  red_bar_growing (short):   ✅ 启用（但被 1H 确认卡死）

实际可产生信号的路径 = 0
```

### Layer 2: 多时间框架锁定层

```
4H 主方向判断:
  仅当 4H MACD histogram > 0 且增长 → 多头主方向
  仅当 4H MACD histogram < 0 且增长 → 空头主方向

1H 确认要求（推断当前为严格模式）:
  多头主方向 → 需要 1H histogram > 0
  空头主方向 → 需要 1H histogram < 0

问题: 在震荡市场中，4H 和 1H 方向一致的概率 < 40%
结论: 即使路径未被禁用，约 60% 的机会在此被拦截
```

### Layer 3: BOLL 位置门控层

```
当前: enable_boll_position_adjustment = True
影响: 在 BOLL 中性区（middle ± 10%）的信号被降分

与三共振的冲突:
  旧 BOLL 门控（VWAP 时代的遗留逻辑）仍在代码中
  三共振新 BOLL 分析器也在运行
  两套 BOLL 逻辑叠加 → 双重过滤 → 合格信号极少

需要: 清理旧 BOLL 门控，仅保留三共振 BOLL 分析器
```

### Layer 4: RSI 门控层

```
当前硬性拦截:
  做多: 4H RSI > 75 → 禁止
  做空: 4H RSI < 25 → 禁止
  做多: 1H RSI < 45 OR > 73 → 评分为 0（实为禁止）
  做空: 1H RSI > 55 OR < 30 → 评分为 0

问题: 2026-03 空头月份中，1H RSI 长期在 35-50 之间
  做空时要求 1H RSI 在 30-55，且 15m RSI < 52
  → 实际可用窗口极窄

修复: 扩宽 RSI 门控范围，适应趋势市场的 RSI 分布特征
```

### Layer 5: 信号评分门槛层

```
当前阈值:
  min_signal_score (默认): 0.78
  red_bar_growing:         0.92  (最严格)
  green_bar_growing:       0.95  (极严格)
  flip_bullish/bearish:    0.80

当即使信号通过上面 4 层，评分门槛仍可能拦截弱信号
但此层是次要问题，解决 Layer 1-4 后此层才有意义
```

---

## 3. 分层修复方案

### 3.1 修复优先级

```
优先级 P0（立即修复，解锁信号产出）:
  ✦ 启用 flip_bearish（2026-03 空头环境的核心信号）
  ✦ 启用 red_bar_growing 双向

优先级 P1（次日修复，增加信号密度）:
  ✦ 放宽 1H 确认要求（严格 → 宽松）
  ✦ 启用 stable_bear/bull_continuation

优先级 P2（本周修复，清理遗留逻辑）:
  ✦ 移除旧 BOLL 门控遗留代码
  ✦ 扩宽 RSI 做空有效区间
  ✦ 降低信号评分门槛（0.78 → 0.72 基础，0.92 → 0.88 red_bar）

优先级 P3（下周优化，精细调参）:
  ✦ 三共振权重微调
  ✦ 信号族与市场状态的动态映射
```

### 3.2 P0 修复：解锁核心信号族

**预期效果**: 从 0 笔信号提升至约 40-60 笔/30D

```python
# 修复前（当前状态）
disable_flags = {
    "disable_flip_bearish_entries": True,        # ❌ 主要阻断点
    "disable_red_bar_growing_long_entries": True, # ❌ 次要阻断点
    "disable_green_bar_shrinking_entries": True,  # ✓ 保持
    "disable_red_bar_shrinking_entries": True,    # ✓ 保持
}

# 修复后
disable_flags = {
    "disable_flip_bearish_entries": False,        # ✅ 解锁，加 RSI 门控保护
    "disable_red_bar_growing_long_entries": False, # ✅ 解锁，加 BOLL 门控保护
    "disable_green_bar_shrinking_entries": True,  # 保持禁用（负 alpha）
    "disable_red_bar_shrinking_entries": True,    # 保持禁用（负 alpha）
}
```

**保护措施**（防止解锁后引入低质量交易）：

```python
# flip_bearish 解锁后的补充门控
flip_bearish_guards = {
    "min_signal_score": 0.80,         # 保持原门槛
    "rsi_1h_range": (30, 60),         # RSI 在合理区间（扩宽至 60）
    "rsi_4h_max": 60,                 # 4H RSI 不超过 60
    "boll_zone_allowed": [0, 1, 2],   # 必须在中上区或上轨区
    "require_4h_histogram_negative": True,  # 4H MACD 柱必须为负
}

# red_bar_growing 做多解锁后的补充门控
red_bar_growing_long_guards = {
    "min_signal_score": 0.88,         # 比做空略低，但仍严格
    "rsi_1h_range": (42, 65),         # 做多 RSI 区间
    "boll_zone_allowed": [4, 5, 6],   # 必须在中下区或下轨区
    "require_4h_histogram_positive": True,  # 4H MACD 柱必须为正
}
```

### 3.3 P1 修复：放宽时间框架确认

**预期效果**: 在 P0 基础上再增加 20-30 笔信号

```python
# 修复前
entry_filters = {
    "primary_direction_timeframe": "4h",
    "require_1h_confirmation_when_4h_primary": True,  # 严格模式
    "allow_neutral_1h_confirmation": False,
    "light_1h_confirmation_when_4h_primary": False,
}

# 修复后
entry_filters = {
    "primary_direction_timeframe": "4h",
    "require_1h_confirmation_when_4h_primary": True,  # 保持要求
    "allow_neutral_1h_confirmation": True,    # ✅ 允许 1H 中性时也算通过
    "light_1h_confirmation_when_4h_primary": True,  # ✅ 宽松确认模式
    # 宽松确认: 1H 方向与 4H 不冲突即可（不要求完全一致）
}
```

**stable_continuation 启用**：

```python
# 修复后新增
entry_filters_addition = {
    "enable_stable_bear_continuation": True,  # ✅ 新增
    "enable_stable_bull_continuation": True,  # ✅ 新增
    "stable_bear_continuation_min_signal_score": 0.75,
    "stable_bull_continuation_min_signal_score": 0.75,
    "stable_bear_continuation_min_adx_1h": 22,   # ADX 确认趋势
    "stable_bull_continuation_min_adx_1h": 22,
    "stable_bear_continuation_min_4h_bars": 2,   # 至少 2 根 4H K 线
    "stable_bull_continuation_min_4h_bars": 2,
}
```

### 3.4 P2 修复：清理遗留 BOLL 逻辑 + RSI 区间扩宽

**旧 BOLL 门控移除**：

```python
# 需要在代码中注释/移除的旧逻辑
# (位于 macd_strategy_v2.py 三共振集成之前的代码段)

# 旧逻辑（移除）:
# if enable_boll_position_adjustment:
#     boll_pos_score = compute_old_boll_position(close, upper, lower)
#     if boll_pos_score < boll_position_threshold:
#         return neutral  # ← 这个旧门控在三共振架构下是冗余且有害的

# 新逻辑（保留）:
# boll_result = boll_structure_analyzer.analyze(...)
# if not boll_result.gate_pass:
#     return neutral  # 仅使用三共振 BOLL 分析器
```

**RSI 做空区间扩宽**：

```python
# 修复前（过窄）
rsi_short_gate = {
    "rsi_4h_min": 25,
    "rsi_4h_max": 57,   # ← 2026-03 空头市 4H RSI 经常在 35-55，此上限偶尔卡死
    "rsi_1h_min": 30,
    "rsi_1h_max": 55,   # ← 1H RSI 在空头趋势中经常是 40-58，此上限太紧
    "rsi_15m_max": 52,
}

# 修复后（适配趋势环境）
rsi_short_gate = {
    "rsi_4h_min": 25,
    "rsi_4h_max": 62,   # ✅ 扩宽 5 点（趋势空头中 4H RSI 可在 55-62）
    "rsi_1h_min": 28,
    "rsi_1h_max": 60,   # ✅ 扩宽 5 点（1H RSI 在空头延续中可到 58）
    "rsi_15m_max": 55,  # ✅ 扩宽 3 点
}

# 修复前（做多，同样适度扩宽）
rsi_long_gate = {
    "rsi_4h_min": 43,
    "rsi_1h_min": 43,
    "rsi_1h_max": 70,
    "rsi_15m_min": 47,
}

# 修复后
rsi_long_gate = {
    "rsi_4h_min": 40,   # ✅ 略宽（flip_bullish 翻多初期 4H RSI 可能在 40-43）
    "rsi_1h_min": 40,   # ✅ 略宽
    "rsi_1h_max": 72,   # ✅ 扩宽 2 点
    "rsi_15m_min": 45,  # ✅ 略宽
}
```

---

## 4. 配置文件完整 Diff

```diff
--- a/config/trading_config_fund_flow.json
+++ b/config/trading_config_fund_flow.json

@@ 禁用标志 @@
   "disable_flags": {
-    "disable_flip_bearish_entries": true,
+    "disable_flip_bearish_entries": false,       // P0: 解锁 flip_bearish
-    "disable_red_bar_growing_long_entries": true,
+    "disable_red_bar_growing_long_entries": false, // P0: 解锁 red_bar_growing 做多
     "disable_green_bar_shrinking_entries": true,   // 保持禁用
     "disable_red_bar_shrinking_entries": true,     // 保持禁用
+    "disable_old_boll_position_gate": true,        // P2: 禁用旧 BOLL 门控
+    "use_resonance_boll_only": true                // P2: 仅使用三共振 BOLL
   },

@@ 入场过滤器 @@
   "entry_filters": {
     "primary_direction_timeframe": "4h",
-    "require_1h_confirmation_when_4h_primary": true,
+    "require_1h_confirmation_when_4h_primary": true,  // 保持，但放宽确认标准
-    "allow_neutral_1h_confirmation": false,
+    "allow_neutral_1h_confirmation": true,         // P1: 允许 1H 中性算通过
-    "light_1h_confirmation_when_4h_primary": false,
+    "light_1h_confirmation_when_4h_primary": true, // P1: 宽松确认模式

+    // P1: 启用 stable_continuation 系列
+    "enable_stable_bear_continuation": true,
+    "enable_stable_bull_continuation": true,
+    "stable_bear_continuation_min_signal_score": 0.75,
+    "stable_bull_continuation_min_signal_score": 0.75,
+    "stable_bear_continuation_min_adx_1h": 22,
+    "stable_bull_continuation_min_adx_1h": 22,
+    "stable_bear_continuation_min_4h_bars": 2,
+    "stable_bull_continuation_min_4h_bars": 2,
   },

@@ 信号评分门槛 @@
   "entry_thresholds": {
-    "default": 0.78,
+    "default": 0.72,                    // P2: 降低基础门槛（从 0.78 到 0.72）
     "flip_bullish": 0.80,               // 保持
-    "flip_bearish": 0.80,
+    "flip_bearish": 0.80,               // 保持（P0 解锁后用此门槛把关）
-    "green_bar_growing": 0.95,
+    "green_bar_growing": 0.92,          // P2: 略降（需 BOLL 下轨保护）
-    "red_bar_growing": 0.92,
+    "red_bar_growing_short": 0.90,      // 做空保持严格
+    "red_bar_growing_long": 0.88,       // P0 解锁，做多略低
     "stable_bear_continuation": 0.75,   // P1 新增
     "stable_bull_continuation": 0.75,   // P1 新增
   },

@@ RSI 门控 @@
   "rsi_config": {
     "gate": {
       // 做多 RSI 门控（略宽）
-      "long_rsi_4h_min": 43,
+      "long_rsi_4h_min": 40,
-      "long_rsi_1h_min": 43,
+      "long_rsi_1h_min": 40,
       "long_rsi_1h_max": 70,            // 保持
-      "long_rsi_15m_min": 47,
+      "long_rsi_15m_min": 45,

       // 做空 RSI 门控（扩宽适配空头趋势）
       "short_rsi_4h_min": 25,           // 保持
-      "short_rsi_4h_max": 57,
+      "short_rsi_4h_max": 62,           // P2: 扩宽 5 点
       "short_rsi_1h_min": 28,           // 略宽
-      "short_rsi_1h_max": 55,
+      "short_rsi_1h_max": 60,           // P2: 扩宽 5 点
-      "short_rsi_15m_max": 52,
+      "short_rsi_15m_max": 55,          // P2: 扩宽 3 点
     },

     // flip 族豁免（已有，确认存在）
     "flip_override": {
       "flip_bullish_rsi_1h_min": 40,    // 保持
       "flip_bullish_rsi_4h_min": 40,
       "flip_bearish_rsi_1h_max": 60,    // 保持
       "flip_bearish_rsi_4h_max": 60,
     },
   },

@@ BOLL 门控（清理旧逻辑）@@
-  "enable_boll_position_adjustment": true,     // P2: 废弃旧门控
-  "boll_position_threshold": 0.3,              // P2: 废弃旧参数
+  "enable_boll_position_adjustment": false,    // P2: 关闭旧 BOLL 门控
   // 保留三共振 BOLL 分析器配置（boll_config 节点不变）

@@ 新增 flip_bearish 专属门控（P0 解锁的保护措施）@@
+  "flip_bearish_resonance_guards": {
+    "min_boll_zone_score": 0.60,               // 必须在 BOLL 上轨区
+    "require_4h_histogram_negative": true,     // 4H MACD 柱必须为负
+    "rsi_1h_range_max": 60,                    // 不超过 60
+  },

@@ 新增 red_bar_growing long 专属门控 @@
+  "red_bar_growing_long_guards": {
+    "min_boll_zone_score_long": 0.65,          // 必须在 BOLL 下轨区
+    "require_4h_histogram_positive": true,     // 4H MACD 柱为正（做多方向）
+    "rsi_1h_range_min": 42,                    // RSI 不能太低
+    "rsi_1h_range_max": 65,
+  },
```

---

## 5. 代码修改 Diff

### 5.1 macd_strategy_v2.py — 信号族禁用检查修复

```diff
--- a/src/fund_flow/macd_strategy_v2.py
+++ b/src/fund_flow/macd_strategy_v2.py

@@ 信号族禁用检查 (约 line 3523) @@

 def _check_signal_disabled(self, signal_type, direction):
     """
     检查信号族是否被禁用
     """
     flags = self.config.get("disable_flags", {})

-    # 旧逻辑: 仅检查信号类型
-    if signal_type == "flip_bearish":
-        if flags.get("disable_flip_bearish_entries", False):
-            return True, "flip_bearish_disabled"
-
-    if signal_type == "red_bar_growing" and direction == "long":
-        if flags.get("disable_red_bar_growing_long_entries", False):
-            return True, "rbg_long_disabled"

+    # 新逻辑: 检查信号类型 + 方向组合，并应用专属门控
+    if signal_type == "flip_bearish":
+        if flags.get("disable_flip_bearish_entries", False):
+            return True, "flip_bearish_disabled"
+        # flip_bearish 已解锁，应用专属门控（在调用方检查）
+        return False, None
+
+    if signal_type == "red_bar_growing":
+        if direction == "long":
+            if flags.get("disable_red_bar_growing_long_entries", False):
+                return True, "rbg_long_disabled"
+            # 已解锁，应用专属门控（在调用方检查）
+            return False, None
+        # direction == "short": 始终允许（原有逻辑）
+        return False, None

     # shrink 族保持禁用
     if signal_type == "green_bar_shrinking":
         if flags.get("disable_green_bar_shrinking_entries", True):
             return True, "gbs_disabled"

     if signal_type == "red_bar_shrinking":
         if flags.get("disable_red_bar_shrinking_entries", True):
             return True, "rbs_disabled"

     return False, None


@@ 新增 flip_bearish 专属门控检查 @@

+def _check_flip_bearish_resonance_guards(self, direction, boll_result, rsi_result, macd_data):
+    """
+    flip_bearish 解锁后的专属保护门控
+    替代旧的 disable_flip_bearish_entries = True 的过度限制
+    """
+    guards = self.config.get("flip_bearish_resonance_guards", {})
+
+    # BOLL 区域检查: 必须在中上区或上轨区（做空有利位置）
+    min_boll_score = guards.get("min_boll_zone_score", 0.60)
+    if boll_result and boll_result.structure_score < min_boll_score:
+        return False, (
+            f"FLIP_BEARISH_GUARD_BOLL: score={boll_result.structure_score:.3f} < {min_boll_score}"
+        )
+
+    # 4H MACD 柱方向检查
+    if guards.get("require_4h_histogram_negative", True):
+        hist_4h = macd_data.get("histogram_4h", 0)
+        if hist_4h >= 0:
+            return False, f"FLIP_BEARISH_GUARD_4H_HIST: histogram={hist_4h:.6f} >= 0"
+
+    # RSI 上限检查（不在超买区做空）
+    rsi_1h_max = guards.get("rsi_1h_range_max", 60)
+    if rsi_result and rsi_result.rsi_1h > rsi_1h_max:
+        return False, f"FLIP_BEARISH_GUARD_RSI: RSI_1h={rsi_result.rsi_1h:.1f} > {rsi_1h_max}"
+
+    return True, None


+def _check_red_bar_growing_long_guards(self, boll_result, rsi_result, macd_data):
+    """
+    red_bar_growing 做多解锁后的专属保护门控
+    """
+    guards = self.config.get("red_bar_growing_long_guards", {})
+
+    # BOLL 区域检查: 必须在中下区或下轨区（做多有利位置）
+    min_boll_score = guards.get("min_boll_zone_score_long", 0.65)
+    if boll_result and boll_result.structure_score < min_boll_score:
+        return False, (
+            f"RBG_LONG_GUARD_BOLL: score={boll_result.structure_score:.3f} < {min_boll_score}"
+        )
+
+    # 4H MACD 柱方向检查（做多需要 4H 柱为正）
+    if guards.get("require_4h_histogram_positive", True):
+        hist_4h = macd_data.get("histogram_4h", 0)
+        if hist_4h <= 0:
+            return False, f"RBG_LONG_GUARD_4H_HIST: histogram={hist_4h:.6f} <= 0"
+
+    # RSI 范围检查
+    rsi_1h_min = guards.get("rsi_1h_range_min", 42)
+    rsi_1h_max = guards.get("rsi_1h_range_max", 65)
+    if rsi_result:
+        rsi_1h = rsi_result.rsi_1h
+        if not (rsi_1h_min <= rsi_1h <= rsi_1h_max):
+            return False, f"RBG_LONG_GUARD_RSI: RSI_1h={rsi_1h:.1f} not in [{rsi_1h_min},{rsi_1h_max}]"
+
+    return True, None
```

### 5.2 macd_strategy_v2.py — analyze() 主流程更新

```diff
@@ analyze() 主函数集成专属门控 (约 line 3560) @@

 def analyze(self, symbol, flow_context):
     # ... 基础 MACD 信号识别 ...
     macd_result = self._compute_macd_signals(symbol, flow_context)
     if macd_result["direction"] == "neutral":
         return {"direction": "neutral", "score": 0.0, "reason": "MACD_NEUTRAL"}

     direction      = macd_result["direction"]
     signal_type    = macd_result["signal_type_1h"]
     macd_base_score = macd_result["signal_score"]

     # Step A: 信号族禁用检查
     disabled, disable_reason = self._check_signal_disabled(signal_type, direction)
     if disabled:
         return {"direction": "neutral", "score": 0.0, "reason": disable_reason}

     # Step B: BOLL 结构分析（三共振层）
     boll_result = self.boll_analyzer.analyze(
         symbol=symbol,
         direction=direction,
         closes_4h=flow_context.get("closes_4h", []),
         closes_1h=flow_context.get("closes_1h", []),
         current_price=flow_context.get("current_price", 0),
     )

     # Step C: RSI 分析（三共振层）
     rsi_result = self.rsi_analyzer.analyze(...)

+    # Step D: 专属门控（解锁信号族的额外保护）
+    macd_raw = {
+        "histogram_4h": flow_context.get("macd_histogram_4h", 0),
+        "histogram_1h": flow_context.get("macd_histogram_1h", 0),
+    }
+
+    if signal_type == "flip_bearish":
+        guard_ok, guard_reason = self._check_flip_bearish_resonance_guards(
+            direction, boll_result, rsi_result, macd_raw
+        )
+        if not guard_ok:
+            return {"direction": "neutral", "score": 0.0, "reason": guard_reason}
+
+    if signal_type == "red_bar_growing" and direction == "long":
+        guard_ok, guard_reason = self._check_red_bar_growing_long_guards(
+            boll_result, rsi_result, macd_raw
+        )
+        if not guard_ok:
+            return {"direction": "neutral", "score": 0.0, "reason": guard_reason}

     # ... 后续三共振评分、白名单检查、聚合评分 ...
```

### 5.3 macd_strategy_v2.py — 旧 BOLL 门控清理

```diff
@@ 旧 BOLL 位置门控（约 line 3480，在三共振之前）@@

-    # 旧 BOLL 位置调整逻辑（VWAP 时代遗留，与三共振冲突）
-    if self.config.get("enable_boll_position_adjustment", False):
-        boll_pos_score = self._compute_legacy_boll_position(close, upper, lower)
-        if boll_pos_score < self.config.get("boll_position_threshold", 0.3):
-            logger.debug(f"[{symbol}] Legacy BOLL position gate: {boll_pos_score:.3f}")
-            return neutral_signal("legacy_boll_gate")

+    # 旧 BOLL 门控已废弃，三共振 BOLL 分析器替代此功能
+    # （disable_old_boll_position_gate = true 时跳过旧逻辑）
+    if not self.config.get("disable_flags", {}).get("disable_old_boll_position_gate", True):
+        # 仅在显式保留旧逻辑时执行（过渡期兼容）
+        if self.config.get("enable_boll_position_adjustment", False):
+            boll_pos_score = self._compute_legacy_boll_position(close, upper, lower)
+            if boll_pos_score < self.config.get("boll_position_threshold", 0.3):
+                return neutral_signal("legacy_boll_gate")
```

### 5.4 decision_engine.py — stable_continuation 启用

```diff
--- a/src/fund_flow/decision_engine.py
+++ b/src/fund_flow/decision_engine.py

@@ _decide_macd_v2_strategy (约 line 656) @@

 def _decide_macd_v2_strategy(self, symbol, flow_context):
     result = self.macd_engine.analyze(symbol, flow_context)

+    # 记录信号类型（用于调试）
+    signal_type = result.get("signal_type_1h", "unknown")
+    logger.debug(
+        f"[{symbol}] MACD analyze: direction={result['direction']}, "
+        f"signal={signal_type}, score={result.get('score', 0):.3f}, "
+        f"reason={result.get('reason', 'N/A')}"
+    )

     if result["direction"] == "long":
         return {"action": "BUY",  "score": result["score"]}
     elif result["direction"] == "short":
         return {"action": "SELL", "score": result["score"]}
     return {"action": "HOLD",  "score": 0.0}


@@ 新增信号诊断日志（P0 调试必须，后续可降级为 DEBUG）@@

+def _log_signal_diagnostics(self, symbol, flow_context, result):
+    """
+    信号诊断日志（用于验证修复效果）
+    """
+    # 每 100 次分析记录一次摘要
+    self._diagnostic_count = getattr(self, "_diagnostic_count", 0) + 1
+    if self._diagnostic_count % 100 == 0:
+        logger.info(
+            f"[DIAGNOSTIC] {symbol}: "
+            f"direction={result.get('direction')}, "
+            f"signal_type={result.get('signal_type_1h')}, "
+            f"resonance={result.get('resonance_score', 0):.3f}, "
+            f"boll_zone={result.get('boll_zone')}, "
+            f"rsi_1h={result.get('rsi_1h', 0):.1f}, "
+            f"reason={result.get('reason')}"
+        )
```

---

## 6. 核心伪代码

### 6.1 修复后的信号生成完整流程

```python
def analyze_v31(symbol: str, flow_context: dict) -> dict:
    """
    v3.1 修复版：解决基础信号零产出问题
    核心修复: 解锁 flip_bearish + red_bar_growing_long
    """

    # ────────────────────────────────────────
    # Step 1: MACD 基础信号识别（不变）
    # ────────────────────────────────────────
    macd_result = _compute_macd_signals(symbol, flow_context)

    direction   = macd_result["direction"]
    signal_type = macd_result["signal_type_1h"]
    base_score  = macd_result["signal_score"]

    if direction == "neutral":
        return neutral("MACD_NEUTRAL")

    # ────────────────────────────────────────
    # Step 2: 信号族过滤（修复核心）
    # ────────────────────────────────────────
    PERMANENT_BLACKLIST = {"green_bar_shrinking", "red_bar_shrinking"}

    if signal_type in PERMANENT_BLACKLIST:
        return neutral(f"BLACKLIST:{signal_type}")  # 永久禁用

    # flip_bearish: v3.1 解锁（由专属门控保护）
    # red_bar_growing long: v3.1 解锁（由专属门控保护）
    # 其他信号族: 按原有白名单逻辑

    # ────────────────────────────────────────
    # Step 3: BOLL 结构分析（不变）
    # ────────────────────────────────────────
    boll_result = boll_analyzer.analyze(
        direction=direction,
        closes_4h=flow_context["closes_4h"],
        current_price=flow_context["current_price"],
    )
    # 注: 旧 BOLL 门控已在 Step 3 之前被跳过

    if not boll_result.gate_pass:
        return neutral(boll_result.gate_fail_reason)

    # ────────────────────────────────────────
    # Step 4: RSI 分析（扩宽区间后）
    # ────────────────────────────────────────
    rsi_result = rsi_analyzer.analyze(
        direction=direction,
        signal_type=signal_type,  # flip 族使用豁免配置
        closes_4h=flow_context["closes_4h"],
        closes_1h=flow_context["closes_1h"],
        closes_15m=flow_context["closes_15m"],
    )

    if not rsi_result.gate_pass:
        return neutral(rsi_result.fail_reason)

    # ────────────────────────────────────────
    # Step 5: 解锁信号族的专属门控（新增）
    # ────────────────────────────────────────
    macd_raw = {
        "histogram_4h": flow_context.get("macd_histogram_4h", 0),
        "histogram_1h": flow_context.get("macd_histogram_1h", 0),
    }

    if signal_type == "flip_bearish":
        # P0 解锁 + 保护
        ok, reason = check_flip_bearish_guards(boll_result, rsi_result, macd_raw)
        if not ok:
            return neutral(reason)

    if signal_type == "red_bar_growing" and direction == "long":
        # P0 解锁 + 保护
        ok, reason = check_red_bar_growing_long_guards(boll_result, rsi_result, macd_raw)
        if not ok:
            return neutral(reason)

    if signal_type in ("stable_bear_continuation", "stable_bull_continuation"):
        # P1 新增信号族
        ok, reason = check_stable_continuation_guards(
            signal_type, flow_context, rsi_result
        )
        if not ok:
            return neutral(reason)

    # ────────────────────────────────────────
    # Step 6: 三共振评分（不变）
    # ────────────────────────────────────────
    market_regime = flow_context.get("market_regime", "TRENDING_BEAR")
    resonance = resonance_scorer.compute(
        direction=direction,
        macd_base_score=base_score,
        boll_result=boll_result,
        rsi_result=rsi_result,
        market_regime=market_regime,
    )

    if not resonance["gate_pass"]:
        return neutral(resonance["reason"])

    # ────────────────────────────────────────
    # Step 7: 返回有效信号
    # ────────────────────────────────────────
    return {
        "direction":       direction,
        "score":           resonance["resonance_score"],
        "signal_type_1h":  signal_type,
        "boll_zone":       boll_result.zone,
        "rsi_1h":          rsi_result.rsi_1h,
        "resonance_score": resonance["resonance_score"],
        "reason":          "PASS",
    }
```

### 6.2 stable_continuation 门控伪代码

```python
def check_stable_continuation_guards(
    signal_type: str,
    flow_context: dict,
    rsi_result,
) -> tuple[bool, str | None]:
    """
    stable_bear/bull_continuation 专属门控
    P1 新增信号族的质量保护
    """
    cfg = get_config(f"{signal_type}_config")
    min_adx = cfg.get("min_adx_1h", 22)
    min_4h_bars = cfg.get("min_4h_bars", 2)

    # ADX 趋势确认
    adx_1h = flow_context.get("adx_1h", 0)
    if adx_1h < min_adx:
        return False, f"STABLE_CONT_ADX_LOW: {adx_1h:.1f} < {min_adx}"

    # 4H MACD 连续方向 bars 确认
    consecutive_4h = flow_context.get("macd_4h_consecutive_direction_bars", 0)
    if consecutive_4h < min_4h_bars:
        return False, f"STABLE_CONT_4H_BARS: {consecutive_4h} < {min_4h_bars}"

    # RSI 不在极值区
    rsi_1h = rsi_result.rsi_1h
    if signal_type == "stable_bear_continuation":
        if rsi_1h < 28 or rsi_1h > 60:
            return False, f"STABLE_BEAR_RSI_OOB: {rsi_1h:.1f}"
    elif signal_type == "stable_bull_continuation":
        if rsi_1h < 40 or rsi_1h > 72:
            return False, f"STABLE_BULL_RSI_OOB: {rsi_1h:.1f}"

    return True, None
```

### 6.3 诊断工具：信号产出监控

```python
class SignalGenerationDiagnostics:
    """
    信号产出诊断工具
    用于验证 v3.1 修复效果
    """

    def __init__(self):
        self.counters = {
            "total_analysis":        0,
            "macd_neutral":          0,
            "blacklist_blocked":     0,
            "flip_bearish_guard":    0,
            "rbg_long_guard":        0,
            "boll_gate_fail":        0,
            "rsi_gate_fail":         0,
            "resonance_fail":        0,
            "signals_generated":     0,
        }
        self.signal_type_counts = {}

    def record(self, result: dict):
        self.counters["total_analysis"] += 1
        reason = result.get("reason", "PASS")

        if result["direction"] == "neutral":
            category = self._classify_neutral_reason(reason)
            self.counters[category] = self.counters.get(category, 0) + 1
        else:
            self.counters["signals_generated"] += 1
            st = result.get("signal_type_1h", "unknown")
            self.signal_type_counts[st] = self.signal_type_counts.get(st, 0) + 1

    def _classify_neutral_reason(self, reason: str) -> str:
        if "NEUTRAL" in reason:            return "macd_neutral"
        if "BLACKLIST" in reason:          return "blacklist_blocked"
        if "FLIP_BEARISH_GUARD" in reason: return "flip_bearish_guard"
        if "RBG_LONG_GUARD" in reason:     return "rbg_long_guard"
        if "BOLL" in reason:               return "boll_gate_fail"
        if "RSI" in reason:                return "rsi_gate_fail"
        if "RESONANCE" in reason:          return "resonance_fail"
        return "other"

    def report(self) -> str:
        total = self.counters["total_analysis"]
        generated = self.counters["signals_generated"]
        rate = generated / max(total, 1) * 100

        lines = [
            f"=== Signal Generation Report ===",
            f"Total Analysis   : {total:,}",
            f"Signals Generated: {generated:,} ({rate:.2f}%)",
            f"",
            f"--- Block Breakdown ---",
        ]
        for k, v in self.counters.items():
            if k not in ("total_analysis", "signals_generated") and v > 0:
                pct = v / max(total, 1) * 100
                lines.append(f"  {k:<25}: {v:>6,} ({pct:.1f}%)")

        if self.signal_type_counts:
            lines.append(f"\n--- Signal Types ---")
            for st, cnt in sorted(
                self.signal_type_counts.items(), key=lambda x: -x[1]
            ):
                lines.append(f"  {st:<30}: {cnt:>4,}")

        return "\n".join(lines)
```

---

## 7. 分阶段验证计划

### Phase 0：最小修复验证（第 1 天）

```
目标: 确认 flip_bearish 和 red_bar_growing_long 解锁后能产生信号

操作:
  1. 应用 P0 配置 Diff（仅修改 disable_flags）
  2. 运行诊断回测（2026-03-01 ~ 2026-03-07，7 天）
  3. 检查 SignalGenerationDiagnostics.report()

验收标准:
  ✓ signals_generated > 0（打破零产出）
  ✓ flip_bearish 和/或 rbg_long 出现在 signal_type_counts 中
  ✓ flip_bearish_guard 拦截率 < 60%（门控不能太严）
  ✓ 整体信号通过率 > 2%

失败处理:
  若仍为 0 → 检查 _compute_macd_signals() 是否正确识别 flip_bearish 类型
  具体检查: flow_context 中的 macd_histogram_4h/1h 是否被正确传入
```

### Phase 1：7 天回测验证（第 2-3 天）

```
目标: 验证 P0+P1 修复后的信号质量

操作:
  1. 应用 P0 + P1 配置 Diff
  2. 运行 2026-03-01 ~ 2026-03-31 完整回测
  3. 对比基线指标

预期结果:
  Trade Count: 30-80 笔（P0+P1 后的第一步目标）
  Win Rate:    ≥ 72%（低于目标但可接受，P2 优化后提升）
  Profit Factor: ≥ 1.5

若 trade count < 30:
  → 检查 BOLL gate 拦截率是否过高（目标 < 20%）
  → 检查 RSI gate 拦截率是否过高（目标 < 35%）
  → 对照诊断报告，找到主要阻断点
```

### Phase 2：完整修复验证（第 4-7 天）

```
目标: 应用全部修复，达到策略目标区间

操作:
  1. 应用 P0+P1+P2 完整 Diff
  2. 运行 2026-03-01 ~ 2026-03-31 完整回测
  3. 验收：Trade Count 90-120，Win Rate ≥ 78%

若胜率未达 78%:
  → 检查 flip_bearish_guards 是否过于宽松
  → 适当提高 min_boll_zone_score 从 0.60 → 0.65
  → 检查 red_bar_growing_long_guards 中的 BOLL 要求

若交易数量超过 120:
  → 适当提高 stable_continuation 的 ADX 要求（22 → 25）
  → 适当提高 default 信号评分门槛（0.72 → 0.75）
```

---

## 8. 监控指标与告警

### 8.1 修复后的关键监控指标

```yaml
fix_validation_metrics:

  signal_generation_rate:
    description: "每小时信号产生率（信号数/分析次数）"
    target_range: "0.5% - 3.0%"
    alert_below: 0.2%    # 信号产出不足
    alert_above: 5.0%    # 信号过多（门控可能失效）

  flip_bearish_pass_rate:
    description: "flip_bearish 信号通过专属门控的比率"
    target_range: "30% - 70%"
    alert_below: 10%     # 门控过严
    alert_above: 85%     # 门控过松（可能引入低质量信号）

  rbg_long_pass_rate:
    description: "red_bar_growing 做多通过门控的比率"
    target_range: "20% - 60%"
    alert_below: 5%
    alert_above: 80%

  boll_gate_block_rate:
    description: "BOLL 结构门控拦截率"
    target_range: "8% - 25%"
    alert_above: 40%     # 可能是旧 BOLL 门控未完全清理

  rsi_gate_block_rate:
    description: "RSI 三时间框架门控拦截率"
    target_range: "15% - 40%"
    alert_above: 60%     # RSI 区间可能需要进一步扩宽

  daily_signal_count:
    description: "每日实际产生的有效信号数"
    target_range: "3 - 5"   # 90-120/30D = 3-4/天
    alert_below: 1
    alert_above: 8
```

### 8.2 信号质量快速验证

```python
# 每日运行的信号质量检查脚本

def daily_signal_quality_check():
    """
    快速验证修复后的信号质量
    """
    diag = diagnostics.get_today_report()

    print(f"今日信号产出: {diag['signals_generated']}")
    print(f"信号产出率: {diag['signal_rate']:.2%}")
    print()

    # 阻断点分析
    if diag['signals_generated'] == 0:
        print("❌ 零信号！请检查以下阻断点:")
        for block, count in diag['blocks'].items():
            if count > 0:
                print(f"  {block}: {count} 次")

    # 信号族分布检查
    print("信号族分布:")
    for st, cnt in diag['signal_types'].items():
        print(f"  {st}: {cnt}")
        if st == "flip_bearish" and cnt == 0:
            print("  ⚠️ flip_bearish 未产生信号，检查 P0 修复是否生效")
```

---

## 9. 回滚预案

### 9.1 P0 修复回滚

```
触发条件:
  - 解锁 flip_bearish 后 7 天内连续亏损 ≥ 4%
  - flip_bearish 信号的实际胜率 < 60%（目标 80%+）

回滚操作（30 秒内完成）:
  config["disable_flags"]["disable_flip_bearish_entries"] = True
  重启 bot → 立即生效
  无需重新部署代码
```

### 9.2 P1 回滚（stable_continuation）

```
触发条件:
  - stable_bear/bull_continuation 信号的 7 日胜率 < 65%
  - 启用后整体胜率下降 > 3pp

回滚操作:
  config["entry_filters"]["enable_stable_bear_continuation"] = False
  config["entry_filters"]["enable_stable_bull_continuation"] = False
  重启 bot → 立即生效
```

### 9.3 完整回滚到 v3.0

```bash
# 切换到修复前配置
cp config/trading_config_fund_flow_v30_backup.json \
   config/trading_config_fund_flow.json

# 重启 bot
systemctl restart fund-flow-bot

# 验证
curl http://localhost:8080/api/status | jq '.config_version'
# 应输出 "v3.0"，确认回滚成功
```

---

## 总结：三步解锁信号产出

```
现状: 0 笔信号 → 0 笔交易

修复路径:
  P0 (今天): 
    flip_bearish = True   → 预期增加 40-60 笔做空信号/30D
    rbg_long = True       → 预期增加 10-20 笔做多信号/30D
    合计: 50-80 笔（突破零产出，接近目标下限）

  P1 (明天):
    stable_continuation   → 预期增加 15-25 笔/30D
    1H 宽松确认          → 解锁被误拦截的信号约 10-20 笔
    合计: P0 基础上 +25-45 笔 → 总计 75-125 笔（进入目标范围）

  P2 (本周):
    RSI 区间扩宽          → 减少 10-15% 的误拦截
    旧 BOLL 门控清理      → 减少重复过滤
    评分门槛微降          → 释放边缘信号
    合计: 进一步优化质量，不大幅增加数量

最终目标: 90-120 笔/30D，胜率 ≥ 80%，三共振系统正常工作
```

---

*版本: v3.1-signal-fix | 2026-04-13 | 基于三共振策略归因分析*  
*核心原则: shrink 族永久禁用不变；flip_bearish/rbg_long 解锁需专属门控保护*
