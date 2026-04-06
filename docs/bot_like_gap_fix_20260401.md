# MACD V2 — Bot-Like vs 纯策略分离修复方案

**日期**: 2026-04-01  
**问题**: 纯策略 +298% / bot-like -25.8% / 两者分离 324 个百分点  
**执行人**: Codex  
**优先级**: P0，全部强制执行

---

## 零、诊断结论（必须理解，再动代码）

```
分离来源拆解：

  纯策略层 (+298%, WR 91.72%)
  ↓ AI review top-3 筛选
  ↓ pretrade_risk_gate      ← 当前: PASS 851/851，不是瓶颈
  ↓ entry_hard_gate         ← 当前: 0 次触发，不是瓶颈
  ↓ 微观结构缺失降级        ← replay 中 spread/depth/imbalance 全部 unavailable
  ↓ pocket 过度放量         ← long_dual_support 0.85/0.12 放出大量低质候选
  ↓ 杠杆 5x + 仓位 0.3     ← 高杠杆放大每笔亏损
  bot-like (-25.8%, WR 63.68%)

真正的分离层（从大到小）：

  #1  pocket 候选过度放量   red_bar_growing|long_dual_support 从 288→600 笔
      直接证据：0.88/0.16 → 0.85/0.12，收益从 -3.25% 崩到 -25.81%

  #2  live 主阻挡在 VWAP/4H 层
      直接证据：6h live 中 vwap_hard_block=695, volume_vwap_both_low=99
               entry_hard_gate_block=0

  #3  微观结构代理缺失
      直接证据：spread_bps/depth_ratio/imbalance 全部标记为 unavailable

  #4  高杠杆（5x）放大信号噪声
      任何低质量信号在 5x 下的亏损是 2x 时的 2.5 倍
```

---

## 一、改动 A：立即回滚 pocket 参数（最高优先级）

### 数据依据

```
三组对比（30 天 bot-like）：
  0.88 / 0.16  → -3.25%,  288 笔,  WR 67.71%,  MDD 13.49%
  0.85 / 0.10  → -26.67%, 600 笔,  WR 63.67%,  MDD 32.70%
  0.85 / 0.12  → -25.81%, 592 笔,  WR 63.68%,  MDD 31.91%

结论：0.10→0.12 只改善了 0.86pct，远未回到原始水平
     必须恢复到 0.88 / 0.16，或更严格
```

### Diff

```diff
# config/trading_config_fund_flow.json
# 路径: fund_flow.macd_mtf_strategy_v2.entry_filters.pocket_entry_overrides
#       ["red_bar_growing|long_dual_support"]

  "red_bar_growing|long_dual_support": {
    "label": "ld_support_restored_e2",
    "allow_neutral_1h_confirmation": false,
    "require_strict_1h_confirmation": true,
    "disallow_trial_entry": true,
-   "min_signal_score": 0.85,
+   "min_signal_score": 0.88,
-   "min_vwap_score": 0.12,
+   "min_vwap_score": 0.16,
    "min_entry_score": 0.50,
    "require_cvd_ok": true,
    "require_cvd_momentum_ok": true
  }
```

**预期效果**：bot-like 收益从 -25.81% 回到约 -3.25% 水平，等待后续修复。

---

## 二、改动 B：修复 VWAP/4H 层的真实阻挡（主瓶颈）

### 问题定位

```
live 6h attribution：
  vwap_hard_block:        695 次  (81.7% 的 hold)
  volume_vwap_both_low:    99 次  (11.6% 的 hold)
  4H预翻转缩短不足:         83 次  ( 9.8% 的 hold)
  信号评分低于阈值:          11 次  ( 1.3% 的 hold)

entry_hard_gate_block:      0 次   ← 不是问题

关键问题：vwap_hard_block 占 81.7%，但不知道触发阈值是什么
需要先弄清楚 vwap_hard_block 的判定逻辑，再决定是否放宽
```

### 诊断脚本（Codex 必须先跑这个，再动参数）

```python
# scripts/diagnostics/diagnose_vwap_layers.py
# 输出：触发 vwap_hard_block 时的 vwap_score 分布

import json
from pathlib import Path
from collections import defaultdict

def analyze_vwap_block_threshold(live_log_path: str) -> dict:
    """
    从 live decision log 中提取 vwap_hard_block 触发时的 vwap_score 分布
    目的：确定 vwap_hard_block 的实际生效阈值
    """
    blocked_scores = []
    passed_scores  = []

    with open(live_log_path) as f:
        for line in f:
            entry = json.loads(line)
            if entry.get("reject_reason") == "vwap_hard_block":
                blocked_scores.append(entry.get("vwap_score", -1))
            elif entry.get("action") in ("buy", "sell"):
                passed_scores.append(entry.get("vwap_score", -1))

    result = {
        "blocked_count":  len(blocked_scores),
        "blocked_max":    max(blocked_scores) if blocked_scores else None,
        "blocked_mean":   sum(blocked_scores)/len(blocked_scores) if blocked_scores else None,
        "passed_min":     min(passed_scores)  if passed_scores  else None,
        "gap":            (min(passed_scores) - max(blocked_scores))
                          if (passed_scores and blocked_scores) else None,
    }
    print(json.dumps(result, indent=2))
    return result
```

### 参数调整原则（基于诊断结果决定）

```python
# 伪代码：根据诊断结果决定放宽幅度

def decide_vwap_threshold_adjustment(diagnosis: dict) -> dict:
    """
    决策规则：
    - 如果 blocked_max < current_threshold - 0.02：当前阈值合理，不调整
    - 如果 blocked_max 在 [current_threshold-0.01, current_threshold)：可小幅放宽
    - 如果 passed_min > current_threshold + 0.03：当前阈值过严，放宽 0.01-0.02
    """
    gap = diagnosis.get("gap", 0)
    if gap is None or gap > 0.03:
        return {"action": "loosen", "delta": -0.01}
    elif gap > 0.01:
        return {"action": "loosen", "delta": -0.005}
    else:
        return {"action": "hold", "delta": 0}
```

### 4H 预翻转缩短不足的修复

```diff
# config/trading_config_fund_flow.json
# 路径: fund_flow.macd_mtf_strategy_v2.entry_filters

  # 当前值（83 次阻挡，约 9.8% hold）
  # 先诊断是 long 还是 short 方向触发更多，再决定方向

  "preflip_trial_min_shrink_pct_long":  0.45,   # 如果 long 方向阻挡 > 60%：
+                                                  # → 0.40（小幅放宽，不低于 0.35）
  "preflip_trial_min_shrink_pct_short": 0.22,   # short 方向暂不动
```

---

## 三、改动 C：微观结构代理补全

### 问题

```
bot-like replay 中：
  spread_bps  = unavailable (104401 次)
  depth_ratio = unavailable (104401 次)
  imbalance   = unavailable (104401 次)

当前处理方式：标记为 unavailable，不伪造值
但 entry_hard_gate 仍会因 spread_ok=False 阻挡部分信号

需要：为 replay 路径提供一套基于历史统计的代理值
目的：让 bot-like 更接近 live 的真实开仓率
```

### 代理值生成函数

```python
# src/fund_flow/replay_utils.py（新建文件）

from dataclasses import dataclass
from typing import Optional
import statistics

@dataclass
class MicroStructureProxy:
    """
    历史统计代理微观结构字段
    基于各 symbol 的历史均值填充 replay 中的缺失字段
    """
    spread_bps:  float   # 典型值: 0.0002~0.0006
    depth_ratio: float   # 典型值: 0.8~1.2
    imbalance:   float   # 典型值: -0.1~+0.1


# 各 symbol 的历史均值（从过去 90 天成交数据中计算）
# Codex 必须从实际历史数据计算，不得凭空填写
SYMBOL_MICRO_PROXIES: dict[str, MicroStructureProxy] = {
    # 格式：symbol → (avg_spread_bps, avg_depth_ratio, avg_imbalance)
    # 示例占位，Codex 必须替换为真实统计值：
    "XRPUSDT":  MicroStructureProxy(0.0003, 1.05, 0.02),
    "SOLUSDT":  MicroStructureProxy(0.0002, 1.10, 0.01),
    # ... 其他 symbol
    "_default": MicroStructureProxy(0.0004, 1.00, 0.00),  # 无历史数据时的 fallback
}


def get_micro_structure_proxy(
    symbol: str,
    bar_time,
    historical_stats: Optional[dict] = None,
) -> MicroStructureProxy:
    """
    为 replay 路径提供微观结构代理值。
    优先使用当时段的历史统计，其次使用 symbol 均值，最后使用全局 default。

    Args:
        symbol:           交易对
        bar_time:         当前 bar 时间（用于时段匹配）
        historical_stats: 预加载的历史统计字典（可选）

    Returns:
        MicroStructureProxy，包含代理的 spread_bps/depth_ratio/imbalance
    """
    if historical_stats and symbol in historical_stats:
        # 按时段查找历史均值
        hour = bar_time.hour
        session_key = "asia" if 0 <= hour < 8 else ("europe" if 8 <= hour < 16 else "us")
        stats = historical_stats[symbol].get(session_key, {})
        if stats:
            return MicroStructureProxy(
                spread_bps  = stats.get("spread_bps_p50",  0.0004),
                depth_ratio = stats.get("depth_ratio_p50", 1.00),
                imbalance   = stats.get("imbalance_p50",   0.00),
            )

    return SYMBOL_MICRO_PROXIES.get(symbol, SYMBOL_MICRO_PROXIES["_default"])


def build_flow_context_with_proxy(
    symbol: str,
    bar_time,
    raw_flow_context: dict,
    historical_stats: Optional[dict] = None,
) -> dict:
    """
    在 replay 路径中，用代理值填充缺失的微观结构字段。
    不覆盖已有的真实值。
    在 flow_context 中标记 is_proxy=True，方便归因。
    """
    ctx = dict(raw_flow_context)
    proxy = get_micro_structure_proxy(symbol, bar_time, historical_stats)

    if ctx.get("spread_bps") is None:
        ctx["spread_bps"] = proxy.spread_bps
        ctx["spread_bps_is_proxy"] = True

    if ctx.get("depth_ratio") is None:
        ctx["depth_ratio"] = proxy.depth_ratio
        ctx["depth_ratio_is_proxy"] = True

    if ctx.get("imbalance") is None:
        ctx["imbalance"] = proxy.imbalance
        ctx["imbalance_is_proxy"] = True

    return ctx
```

### 在 bot-like 中启用代理

```diff
# scripts/backtest_fund_flow_bot_like.py
# 函数：_build_flow_context

  def _build_flow_context(self, symbol, bar_time, raw_data):
-     # 当前：缺失字段直接标记为 unavailable
-     ctx = {
-         "spread_bps":  raw_data.get("spread_bps"),   # 可能为 None
-         "depth_ratio": raw_data.get("depth_ratio"),  # 可能为 None
-         "imbalance":   raw_data.get("imbalance"),    # 可能为 None
-     }
+     # 新：用历史统计代理填充缺失字段
+     from src.fund_flow.replay_utils import build_flow_context_with_proxy
+     ctx = build_flow_context_with_proxy(
+         symbol=symbol,
+         bar_time=bar_time,
+         raw_flow_context={
+             "spread_bps":  raw_data.get("spread_bps"),
+             "depth_ratio": raw_data.get("depth_ratio"),
+             "imbalance":   raw_data.get("imbalance"),
+         },
+         historical_stats=self.micro_structure_stats,  # 预加载的历史统计
+     )
      return ctx
```

---

## 四、改动 D：信号候选前置过滤（AI review 前加硬门）

### 问题

```
当前流程：
  策略层产出 N 个候选 → AI review 筛到 top-3 → 开仓

问题：
  bot-like 中 open_candidates=591，说明策略层放出了 591 个候选
  其中大量是低质量的 long_dual_support 放量信号
  AI review 虽然做了 top-3 筛选，但 591 个候选里排前 3 的仍然可能是坏信号

修复：在 AI review 之前加一层基于结构规则的硬过滤
```

### 候选前置过滤函数

```python
# src/fund_flow/candidate_filter.py（新建文件）

from dataclasses import dataclass
from typing import NamedTuple

class FilterResult(NamedTuple):
    passed: bool
    reason: str

def pre_ai_candidate_filter(
    signal,
    cfg: dict,
) -> FilterResult:
    """
    在 AI review 之前执行基于结构规则的候选质量硬过滤。
    目的：减少进入 AI review 的低质量候选数量，提高 top-3 的实际质量。

    过滤规则（按执行顺序）：
    1. signal_score 必须高于类型专属下限（比全局阈值更严格）
    2. vwap_score 必须高于类型专属下限
    3. 对 long 类信号：4H 方向必须不是强烈空头
    4. 对 trial 信号：额外要求 4H shrink 质量
    """
    pocket_key = f"{signal.signal_type}|{signal.vwap_state}"

    # ── 规则 1：signal_score 候选层最低门槛 ─────────────────────────────────
    candidate_min_scores = cfg.get("candidate_filter_min_signal_scores", {})
    min_score = candidate_min_scores.get(
        pocket_key,
        candidate_min_scores.get(signal.signal_type, 0.87)
    )
    if signal.signal_score < min_score:
        return FilterResult(False,
            f"PRE_AI_SCORE:{signal.signal_score:.4f}<{min_score} [{pocket_key}]")

    # ── 规则 2：vwap_score 候选层最低门槛 ───────────────────────────────────
    candidate_min_vwap = cfg.get("candidate_filter_min_vwap_scores", {})
    min_vwap = candidate_min_vwap.get(
        pocket_key,
        candidate_min_vwap.get("_default", 0.10)
    )
    if signal.vwap_score < min_vwap:
        return FilterResult(False,
            f"PRE_AI_VWAP:{signal.vwap_score:.4f}<{min_vwap} [{pocket_key}]")

    # ── 规则 3：long 类信号的 4H 方向保护 ───────────────────────────────────
    if signal.side == "long":
        max_4h_bear_score = cfg.get("candidate_filter_max_4h_bear_score_for_long", 0.85)
        if getattr(signal, "score_4h_direction_bear", 0) > max_4h_bear_score:
            return FilterResult(False,
                f"PRE_AI_4H_BEAR:{signal.score_4h_direction_bear:.4f}>{max_4h_bear_score}")

    # ── 规则 4：trial 信号的 4H shrink 质量 ─────────────────────────────────
    if signal.is_trial_entry:
        min_shrink = cfg.get("candidate_filter_trial_min_4h_shrink_pct", 0.45)
        if getattr(signal, "macd_4h_shrink_pct", 0) < min_shrink:
            return FilterResult(False,
                f"PRE_AI_TRIAL_SHRINK:{signal.macd_4h_shrink_pct:.3f}<{min_shrink}")

    return FilterResult(True, "PRE_AI_PASS")
```

### 在 bot-like 中插入前置过滤

```diff
# scripts/backtest_fund_flow_bot_like.py
# 函数：pick_open_candidates 或等效候选排序函数

+ from src.fund_flow.candidate_filter import pre_ai_candidate_filter

  def pick_open_candidates(self, decisions: list) -> list:
      candidates = [d for d in decisions if d.action in ("buy", "sell")]

+     # ── 候选前置过滤（AI review 之前）────────────────────────────────────
+     pre_filter_cfg = self.cfg.get("fund_flow", {}).get("candidate_pre_filter", {})
+     filtered = []
+     for c in candidates:
+         result = pre_ai_candidate_filter(c.signal, pre_filter_cfg)
+         if result.passed:
+             filtered.append(c)
+         else:
+             self._log_candidate_reject(c, result.reason)
+     candidates = filtered
+     # ── End 候选前置过滤 ──────────────────────────────────────────────────

      # 原有逻辑：按 score 排序，AI review，top-N 限制
      candidates.sort(key=lambda x: x.signal.signal_score, reverse=True)
      ...
```

### 对应配置

```diff
# config/trading_config_fund_flow.json
# 路径: fund_flow（顶层新增节点）

+ "candidate_pre_filter": {
+   "enabled": true,
+   "candidate_filter_min_signal_scores": {
+     "red_bar_growing|long_dual_support": 0.88,
+     "red_bar_growing":                  0.855,
+     "green_bar_growing":                0.845,
+     "_default":                         0.87
+   },
+   "candidate_filter_min_vwap_scores": {
+     "red_bar_growing|long_dual_support": 0.16,
+     "long_dual_support":                 0.14,
+     "_default":                          0.10
+   },
+   "candidate_filter_max_4h_bear_score_for_long": 0.85,
+   "candidate_filter_trial_min_4h_shrink_pct": 0.45
+ }
```

---

## 五、改动 E：杠杆风险约束（与 bot-like 质量问题联动）

### 问题

```
当前实盘参数：
  default_leverage = 5
  max_leverage     = 5
  default_target_portion = 0.3

在 bot-like 63.68% 胜率 + 5x 杠杆下：
  单笔最大亏损 = position_value × stop_loss_pct × leverage
               = (capital × 0.3) × 0.012 × 5
               = capital × 1.8%

  如果连续 5 笔亏损（36.32% 概率下不罕见）：
  累计亏损 ≈ 9%，接近 MDD 容忍上限

结论：在 bot-like 质量未收敛前，不应该用 5x 运行
```

### 分阶段杠杆策略

```python
# 伪代码：基于近期胜率动态调整杠杆上限

def get_max_leverage_by_recent_performance(
    recent_trades: list,
    window: int = 20,
    cfg: dict = None,
) -> int:
    """
    根据近期 N 笔的实际胜率动态限制最大杠杆。
    这是一个安全阀，防止在策略表现差时继续以高杠杆运行。

    映射规则：
      WR >= 75%: max_leverage = 5  (正常运行)
      WR >= 65%: max_leverage = 3  (降杠杆保护)
      WR >= 55%: max_leverage = 2  (最低档保护)
      WR  < 55%: max_leverage = 0  (暂停开仓)
    """
    if len(recent_trades) < 10:
        return 2  # 样本不足时默认保守

    win_rate = sum(1 for t in recent_trades[-window:] if t.pnl > 0) / min(window, len(recent_trades))

    leverage_map = [
        (0.75, 5),
        (0.65, 3),
        (0.55, 2),
        (0.00, 0),
    ]
    for threshold, lev in leverage_map:
        if win_rate >= threshold:
            return lev
    return 0
```

### 配置 Diff

```diff
# config/trading_config_fund_flow.json
# 路径: fund_flow

  "min_leverage":     5,
  "default_leverage": 5,
  "max_leverage":     5,

+ # 动态杠杆约束（基于近期胜率）
+ "dynamic_leverage_enabled": true,
+ "dynamic_leverage_window": 20,
+ "dynamic_leverage_map": [
+   {"min_win_rate": 0.75, "max_leverage": 5},
+   {"min_win_rate": 0.65, "max_leverage": 3},
+   {"min_win_rate": 0.55, "max_leverage": 2},
+   {"min_win_rate": 0.00, "max_leverage": 0}
+ ]
```

---

## 六、改动 F：bot-like 信号漏斗日志（归因必须项）

### 问题

```
当前 live attribution 有：
  vwap_hard_block:      695
  volume_vwap_both_low:  99
  4H预翻转缩短不足:       83

但不知道：
  这 695 次 vwap_hard_block 中，vwap_score 的实际分布是多少
  哪个 vwap_threshold 参数在生效
  放宽多少能恢复多少信号

必须先有精确的漏斗数据，才能做参数调整
```

### 漏斗日志格式

```python
# src/fund_flow/signal_funnel_logger.py（新建文件）

from collections import defaultdict
import json
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class FunnelLayer:
    name:    str
    passed:  int = 0
    blocked: int = 0
    block_reasons: dict = field(default_factory=lambda: defaultdict(int))
    block_score_samples: list = field(default_factory=list)  # 最多保留 100 个样本

    def log(self, passed: bool, reason: str = "", score: float = 0.0):
        if passed:
            self.passed += 1
        else:
            self.blocked += 1
            self.block_reasons[reason] += 1
            if len(self.block_score_samples) < 100:
                self.block_score_samples.append(round(score, 4))

    @property
    def pass_rate(self) -> float:
        total = self.passed + self.blocked
        return self.passed / total if total > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "passed":       self.passed,
            "blocked":      self.blocked,
            "pass_rate":    f"{self.pass_rate:.2%}",
            "top_reasons":  dict(sorted(
                                self.block_reasons.items(),
                                key=lambda x: -x[1])[:5]),
            "score_p50":    sorted(self.block_score_samples)[
                                len(self.block_score_samples)//2
                            ] if self.block_score_samples else None,
            "score_max":    max(self.block_score_samples)
                            if self.block_score_samples else None,
        }


class SignalFunnelLogger:
    """
    完整的信号漏斗记录器。
    在 bot-like 回放和 live 决策链路中，对每一层 gate 的通过/拦截进行计数。
    """
    LAYERS = [
        "0_raw_signal",
        "1_score_threshold",
        "2_vwap_threshold",
        "3_4h_preflip_shrink",
        "4_pocket_entry_override",
        "5_pre_ai_candidate_filter",
        "6_L1_structural",
        "7_L2_flow",
        "8_L3_micro",
        "9_pretrade_gate",
        "10_capacity",
        "11_final_fill",
    ]

    def __init__(self):
        self.layers = {name: FunnelLayer(name) for name in self.LAYERS}

    def log(self, layer: str, passed: bool, reason: str = "", score: float = 0.0):
        if layer not in self.layers:
            self.layers[layer] = FunnelLayer(layer)
        self.layers[layer].log(passed, reason, score)

    def report(self, output_path: Optional[str] = None) -> dict:
        result = {name: layer.to_dict() for name, layer in self.layers.items()}
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
        return result

    def print_summary(self):
        print(f"\n{'Layer':<35} {'Passed':>8} {'Blocked':>8} {'PassRate':>10}")
        print("-" * 65)
        for name, layer in self.layers.items():
            print(f"{name:<35} {layer.passed:>8} {layer.blocked:>8} "
                  f"{layer.pass_rate:>9.1%}")
```

### 注入点

```python
# 在 decision_engine.py 和 backtest_fund_flow_bot_like.py 中

funnel = SignalFunnelLogger()

# 每个 gate 判断后注入：
funnel.log("1_score_threshold",
    passed=(signal.signal_score >= threshold),
    reason=f"score={signal.signal_score:.4f}<{threshold}",
    score=signal.signal_score)

funnel.log("2_vwap_threshold",
    passed=(signal.vwap_score >= vwap_min),
    reason=f"vwap={signal.vwap_score:.4f}<{vwap_min}",
    score=signal.vwap_score)

# 回放结束后输出：
funnel.report(output_path="output/analysis/signal_funnel_30d.json")
funnel.print_summary()
```

---

## 七、执行顺序

```
立即执行（同一次提交）：
  Step 1: 改动 A — 回滚 pocket 参数到 0.88/0.16
  Step 2: 运行 30 天 bot-like，验证收益回到约 -3.25% 水平
  验收：bot-like return > -5%, MDD < 15%

第二次提交（Step 1 验收通过后）：
  Step 3: 改动 F — 实现 SignalFunnelLogger 并注入所有 gate 节点
  Step 4: 运行 30 天 bot-like，输出漏斗报告
  Step 5: 分析漏斗报告，确认 vwap_hard_block 的实际阈值
  验收：拿到完整漏斗数据，知道每层的实际拦截数

第三次提交（漏斗数据出来后）：
  Step 6: 改动 B — 基于漏斗数据，决定 VWAP/4H 层的精确调整幅度
  Step 7: 改动 D — 候选前置过滤，减少 AI review 的低质候选
  验收：bot-like trades 提升且 WR >= 70%, MDD < 15%

第四次提交（第三次验收通过后）：
  Step 8: 改动 C — 微观结构代理（需要先从历史数据计算真实均值）
  Step 9: 改动 E — 动态杠杆约束
  验收：bot-like 与 live 的胜率差距 < 10pct
```

---

## 八、单元测试要求（Codex 必须全部实现）

**新建文件**: `tests/test_candidate_filter.py`

```python
import pytest
from src.fund_flow.candidate_filter import pre_ai_candidate_filter, FilterResult

CFG = {
    "candidate_filter_min_signal_scores": {
        "red_bar_growing|long_dual_support": 0.88,
        "_default": 0.87,
    },
    "candidate_filter_min_vwap_scores": {
        "red_bar_growing|long_dual_support": 0.16,
        "_default": 0.10,
    },
    "candidate_filter_max_4h_bear_score_for_long": 0.85,
    "candidate_filter_trial_min_4h_shrink_pct": 0.45,
}

class FakeSignal:
    def __init__(self, **kwargs):
        self.signal_type = kwargs.get("signal_type", "red_bar_growing")
        self.vwap_state  = kwargs.get("vwap_state", "long_dual_support")
        self.signal_score = kwargs.get("signal_score", 0.89)
        self.vwap_score   = kwargs.get("vwap_score", 0.17)
        self.side         = kwargs.get("side", "long")
        self.is_trial_entry = kwargs.get("is_trial_entry", False)
        self.score_4h_direction_bear = kwargs.get("score_4h_direction_bear", 0.5)
        self.macd_4h_shrink_pct = kwargs.get("macd_4h_shrink_pct", 0.5)


def test_pocket_specific_score_threshold_blocks():
    sig = FakeSignal(signal_score=0.879)
    r = pre_ai_candidate_filter(sig, CFG)
    assert not r.passed and "PRE_AI_SCORE" in r.reason


def test_pocket_specific_vwap_threshold_blocks():
    sig = FakeSignal(vwap_score=0.155)
    r = pre_ai_candidate_filter(sig, CFG)
    assert not r.passed and "PRE_AI_VWAP" in r.reason


def test_4h_bear_score_blocks_long():
    sig = FakeSignal(score_4h_direction_bear=0.90)
    r = pre_ai_candidate_filter(sig, CFG)
    assert not r.passed and "PRE_AI_4H_BEAR" in r.reason


def test_trial_shrink_blocks():
    sig = FakeSignal(is_trial_entry=True, macd_4h_shrink_pct=0.30)
    r = pre_ai_candidate_filter(sig, CFG)
    assert not r.passed and "PRE_AI_TRIAL_SHRINK" in r.reason


def test_all_pass():
    sig = FakeSignal()
    r = pre_ai_candidate_filter(sig, CFG)
    assert r.passed and "PRE_AI_PASS" in r.reason


def test_non_long_dual_support_uses_default_threshold():
    sig = FakeSignal(
        signal_type="green_bar_growing",
        vwap_state="short_dual_pressure",
        signal_score=0.872,   # 高于 green 的 default 0.845，低于 ld_support 的 0.88
        vwap_score=0.15,
    )
    r = pre_ai_candidate_filter(sig, CFG)
    # green_bar_growing 没有 pocket-specific score，用 _default=0.87
    # 0.872 < 0.87 → blocked
    assert not r.passed
```

**新建文件**: `tests/test_signal_funnel_logger.py`

```python
import pytest
from src.fund_flow.signal_funnel_logger import SignalFunnelLogger

def test_funnel_counts_correctly():
    f = SignalFunnelLogger()
    f.log("1_score_threshold", True)
    f.log("1_score_threshold", True)
    f.log("1_score_threshold", False, reason="low_score", score=0.82)

    layer = f.layers["1_score_threshold"]
    assert layer.passed == 2
    assert layer.blocked == 1
    assert layer.block_reasons["low_score"] == 1
    assert 0.82 in layer.block_score_samples

def test_pass_rate():
    f = SignalFunnelLogger()
    for _ in range(3): f.log("2_vwap_threshold", True)
    for _ in range(1): f.log("2_vwap_threshold", False)
    assert abs(f.layers["2_vwap_threshold"].pass_rate - 0.75) < 0.001

def test_report_generates_dict():
    f = SignalFunnelLogger()
    f.log("0_raw_signal", True)
    report = f.report()
    assert "0_raw_signal" in report
    assert "passed" in report["0_raw_signal"]
```

---

## 九、禁止事项

```
禁止 1：不得修改纯策略层（MACDStrategyV2Engine）的任何打分逻辑
禁止 2：改动 A 的 pocket 参数必须严格回滚到 0.88/0.16，不得停在 0.86/0.14 等中间值
禁止 3：微观结构代理值不得凭空填写，必须从历史数据统计，或明确标注为占位符
禁止 4：动态杠杆逻辑不得修改已开仓的持仓杠杆，只能约束新开仓
禁止 5：SignalFunnelLogger 的 log() 调用不得改变任何 gate 的判断结果，只记录不干预
禁止 6：测试文件不得使用任何形式的 skip，全部测试必须 pass
禁止 7：candidate_pre_filter 的 enabled=false 时必须完全跳过，不影响原有逻辑
```

---

## 十、验收指标

```
改动 A 验收（Step 1-2）：
  bot-like return:  > -5%
  bot-like MDD:     < 15%
  bot-like trades:  200-320（与原始 live 配置接近）

改动 F 验收（Step 3-5）：
  漏斗报告存在于 output/analysis/signal_funnel_30d.json
  每一层均有 passed/blocked/pass_rate/top_reasons 字段
  vwap_hard_block 层能看到被拦截信号的 score_p50 和 score_max

改动 B/D 验收（Step 6-7）：
  bot-like WR:      >= 70%
  bot-like trades:  >= 350
  bot-like MDD:     < 15%
  bot-like return:  > 0%

最终目标：
  bot-like WR:      >= 75%
  live vs bot-like WR 差距: < 10pct
  MDD:              < 10%
```
