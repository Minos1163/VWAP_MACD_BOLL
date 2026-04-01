# MACD V2 — long_dual_support 结构性修复方案

**日期**: 2026-04-01  
**当前状态**: 499 笔/月 / 83.17% / +7.00% / MDD 2.87%  
**核心问题**: long_dual_support 153 笔 / 79.74% 胜率 / PnL -236.92 / avg_win +6.52 vs avg_loss -33.28  
**审核角度**: entry alpha 问题，不是 exit 问题，不是 symbol 问题

---

## 一、回答 6 个核心问题

### Q1：是否同意这是 entry alpha 问题而不是 exit 问题？

**完全同意，且可以从数学上证明。**

```
当前 long_dual_support 的期望值结构：

  胜率 p = 0.7974
  avg_win  W = +6.52
  avg_loss L = -33.28

  E[每笔] = p × W - (1-p) × L
           = 0.7974 × 6.52 - 0.2026 × 33.28
           = +5.20 - +6.74
           = -1.54  ← 负期望

  要让期望值 = 0，需要满足：
    p × W = (1-p) × L
    0.7974 × W = 0.2026 × 33.28
    W_min = 0.2026 × 33.28 / 0.7974 = +8.46

  也就是说，在当前止损结构下，avg_win 需要从 6.52 提升到 8.46
  才能让该 pocket 变成零期望。
  要达到正期望（比如 +2/笔），avg_win 需要 ≥ 11。

  结论：
    exit 层面（partial TP / trailing）能帮助提升 avg_win，
    但从 6.52 → 11 是 +69% 的提升，exit 优化不可能独立完成。
    根本原因在于：入口质量不够，大量进场是"假反弹"，
    没有足够的延续性，自然无法走出厚利润。
    exit 只能在 alpha 存在的前提下锦上添花，不能无中生有。
```

---

### Q2：allow_neutral_1h_confirmation + light_1h_confirmation 是否对该 pocket 过宽？

**是，且这是最主要的结构性漏洞。**

```
当前配置逻辑链：

  4H: 出现红柱（下跌中段 or 末段）
  VWAP: 价格处于双重支撑附近
  1H: allow_neutral = true → 1H 可以还是下跌方向
      light_1h_confirmation = true → 1H 只需要"方向未反转"就算通过

  问题：
    long_dual_support 的信号假设是"下跌接近支撑，可能反弹"
    在这种场景下，4H 方向分 (weight 0.40) 实际上是在为"下跌趋势"加分
    1H 即使是中性，也可能代表"下跌暂停而非反转"

    换句话说：
    系统在 4H 下跌 + 1H 中性的情况下，
    允许以"支撑反弹"名义开多头，
    这本质上是逆势做多，却没有要求足够强的逆势确认。

  正确的逻辑：
    long_dual_support = "下跌中做多"
    → 必须要求 1H 已经出现明确的底部结构（不是中性）
    → 或者 1H 方向已经明确翻多
    → allow_neutral_1h 对该 pocket 应该是 false
```

---

### Q3：long_dual_support 是否应该与 preflip_trial 解耦？

**必须解耦，两者是完全不同的逻辑假设。**

```
preflip_trial 的逻辑假设：
  4H 方向即将翻多（正在收敛中）
  提前以小仓位试探翻转
  → "等待趋势启动"的预测性入场
  → 方向本身是可期待转多的

long_dual_support 的逻辑假设：
  4H 当前仍是下跌方向（红柱生长）
  但价格在支撑位附近做反弹
  → "当前趋势中的反弹交易"
  → 方向本身没有改变，只是临时反弹

两者的放宽逻辑完全不同：
  preflip_trial 需要的放宽：更早进场，忍受等待翻转的不确定性
  long_dual_support 需要的放宽：更强的反弹确认，更短的持仓预期

当前系统把 preflip_trial 的"放宽入场条件"（shrink 45%、score 0.70 等）
隐式地也应用到了 long_dual_support 上（因为它们都属于做多信号）
→ 这是错的，long_dual_support 应该有更严格的独立入场条件
```

---

### Q4：VWAP ≥ 0.12 / flow 2/3 / micro 2/3 是否对 long_dual_support 过松？

**全部过松，且原因不同。**

```
VWAP ≥ 0.12（全局门槛）：
  对 long_dual_support，VWAP 结构判定是核心 alpha 来源
  要求仅 0.12（满分 0.20 的 60%）等于接受"VWAP 支撑一般"的入场
  建议：long_dual_support 专用门槛 ≥ 0.16（满分的 80%）

flow 2/3（CVD/OI/VWAP 2 通过即可）：
  对反弹交易，需要确认真实买盘流入，不能只有 VWAP 位置合适
  CVD 是判断"买盘是否真的在支撑位净流入"的最直接指标
  建议：long_dual_support 必须 CVD_ok = true（从"2/3 之一"变成"必选项"）

micro 2/3（depth/imbalance/cvd_momentum 2 通过即可）：
  cvd_momentum 对反弹至关重要，代表"当下动量是否在转多"
  建议：long_dual_support 必须 cvd_momentum_ok = true
```

---

### Q5：给独立 entry gate / 重写 scoring / 还是废弃该 pocket？

**先给独立 entry gate，收紧后回测；如果仍然负期望，再讨论废弃。**

```
废弃前的最后机会：

  long_dual_support 代表一类真实存在的市场结构（双支撑防守多头）
  在强趋势行情中，这类结构有时会产出非常厚的利润
  问题不在于结构本身不存在，而在于当前准入条件太宽，
  把大量"假支撑反弹"也放进来了

  正确的处理顺序：
    Step 1：给 long_dual_support 建独立的高标准 entry gate（本文件方案）
    Step 2：90 天回测验证该 pocket 是否转正
    Step 3：如果仍然负期望 → 废弃该 pocket
    Step 4：如果转正 → 保留，但持续监控

  不应该跳过 Step 1-2 直接废弃，因为这等于放弃了这类结构在强趋势中的 alpha
```

---

### Q6：从"结构规则"角度修正，而不是 symbol 筛选？

**核心思路：把 long_dual_support 从"趋势延续类信号"重新定义为"反弹确认类信号"，并匹配对应的准入标准。**

```
两类信号的准入标准差异：

趋势延续（green_bar_growing, stable continuation）：
  - 4H 方向明确 → 高分
  - 1H 跟随即可
  - VWAP 站稳趋势侧即可
  - CVD 温和流入即可

反弹确认（long_dual_support 应该属于这类）：
  - 4H 方向可能逆势 → 需要额外的逆势确认
  - 1H 必须已经出现底部结构（不接受 neutral）
  - VWAP 必须强支撑确认（高于趋势延续的要求）
  - CVD 必须净买盘流入（不能只是成交量放大）
  - 15M 必须出现上涨动量（不接受软确认）
```

---

## 二、long_dual_support 独立 Entry Gate 方案

### 2.1 设计原则

```
当前 long_dual_support 的失效模式：
  场景 A：假支撑（价格跌破支撑后急跌，止损 -1.2%）
  场景 B：支撑有效但反弹幅度不够（走到 +0.5% 就滞留，partial TP 出场 +0.5%）
  场景 C：支撑有效且反弹有力（走到 4% TP，贡献大额利润）

当前大量入场是 A 和 B，C 是少数。
修复方向：提高准入标准，只保留更高概率是 C 的入场。
```

### 2.2 独立 Entry Gate 配置

```diff
# fund_flow.macd_mtf_strategy_v2.pocket_specific_gates  ← 新增节点

+ pocket_specific_gates:
+   long_dual_support:
+
+     # 1H 方向要求：不接受中性确认
+     require_1h_bullish_confirmation: true       # 必须 1H 方向明确做多
+     allow_neutral_1h_for_pocket: false          # 覆盖全局 allow_neutral_1h=true
+     allow_light_1h_for_pocket: false            # 覆盖全局 light_1h=true
+
+     # VWAP 独立门槛
+     min_vwap_score: 0.16                        # 旧全局: 0.12，提高到满分 80%
+
+     # Signal score 独立门槛
+     min_signal_score: 0.88                      # 旧全局: 0.87，略微收紧
+
+     # CVD 必选（从 flow 2/3 中变成必须通过）
+     require_cvd_ok: true                        # CVD 必须确认净买盘
+     require_cvd_momentum_ok: true               # 微结构层 CVD 动量必须向上
+
+     # 15M 不接受软确认
+     require_15m_strict: true                    # 不接受 soft_15m，要求明确 15m 向上
+     min_15m_entry_score: 0.50                   # 旧: soft 0.30，提高到 0.50
+
+     # 禁止使用 preflip_trial 放宽
+     allow_preflip_trial_relaxation: false        # 该 pocket 不共享 preflip 放宽逻辑
+
+     # 独立仓位缩比（在准入通过的前提下，仍然以 80% 仓位入场）
+     position_scale: 0.80
```

### 2.3 伪代码实现

```python
def check_long_dual_support_gate(
    signal, bar_1h, bar_15m, cvd_data,
    flow_checks: dict, micro_checks: dict,
    cfg
) -> tuple[bool, str]:
    """
    long_dual_support 的独立高标准入场门
    在全局 entry_hard_gates 通过后，额外执行这些检查
    """
    pocket_cfg = cfg.pocket_specific_gates.get("long_dual_support", {})
    if not pocket_cfg:
        return True, "NO_POCKET_GATE"

    # ── 1. 1H 方向：必须明确做多，不接受中性 ──────────────────────────────
    if pocket_cfg.get("require_1h_bullish_confirmation", False):
        if bar_1h.macd_direction not in ("BULLISH", "WEAKLY_BULLISH"):
            return False, (
                f"LD_SUPPORT_1H: direction={bar_1h.macd_direction} "
                f"not bullish (neutral/light not allowed for this pocket)"
            )

    # ── 2. VWAP 独立门槛 ────────────────────────────────────────────────────
    min_vwap = pocket_cfg.get("min_vwap_score", 0.16)
    if signal.vwap_score < min_vwap:
        return False, f"LD_SUPPORT_VWAP: {signal.vwap_score:.3f} < {min_vwap}"

    # ── 3. Signal score 独立门槛 ─────────────────────────────────────────────
    min_score = pocket_cfg.get("min_signal_score", 0.88)
    if signal.signal_score < min_score:
        return False, f"LD_SUPPORT_SCORE: {signal.signal_score:.3f} < {min_score}"

    # ── 4. CVD 必须确认净买盘 ────────────────────────────────────────────────
    if pocket_cfg.get("require_cvd_ok", True):
        if not flow_checks.get("cvd_ok", False):
            return False, "LD_SUPPORT_CVD: cvd_ok=False, no net buying confirmed"

    # ── 5. CVD 动量必须向上 ──────────────────────────────────────────────────
    if pocket_cfg.get("require_cvd_momentum_ok", True):
        if not micro_checks.get("cvd_momentum_ok", False):
            return False, "LD_SUPPORT_CVD_MOM: cvd_momentum not rising"

    # ── 6. 15M 严格确认 ──────────────────────────────────────────────────────
    if pocket_cfg.get("require_15m_strict", False):
        min_15m = pocket_cfg.get("min_15m_entry_score", 0.50)
        if signal.entry_score_15m < min_15m:
            return False, (
                f"LD_SUPPORT_15M: 15m_score={signal.entry_score_15m:.3f} "
                f"< {min_15m} (soft confirmation not accepted)"
            )

    return True, "LD_SUPPORT_GATE_OK"


def apply_pocket_gate(signal, bar_1h, bar_15m, cvd_data,
                      flow_checks, micro_checks, cfg) -> tuple[bool, str]:
    """
    总入口：根据 pocket 类型分发到对应的专项 gate
    在 entry_hard_gates 通过后调用
    """
    pocket_key = f"{signal.signal_type}_{signal.vwap_structure_type}"
    # e.g. "red_bar_growing_long_dual_support"

    if pocket_key in ("red_bar_growing_long_dual_support",
                      "flip_bullish_long_dual_support"):
        return check_long_dual_support_gate(
            signal, bar_1h, bar_15m, cvd_data,
            flow_checks, micro_checks, cfg
        )

    # 其他 pocket 目前走全局逻辑，后续可扩展
    return True, "NO_POCKET_GATE"
```

---

## 三、评分权重调整

### 3.1 当前权重对 long_dual_support 的误导

```
当前权重：
  weight_4h_direction    = 0.40
  weight_4h_enhancement  = 0.10
  weight_1h_direction    = 0.20
  weight_vwap            = 0.20
  weight_15m_entry       = 0.05
  weight_volume          = 0.15

在 long_dual_support 场景下：
  4H 方向是"下跌"→ weight_4h_direction 给的是"下跌强度"的分
  如果 4H 下跌越强，理论上总分的 4H 方向分越高
  但这恰好意味着"做多的基础越弱"
  
  这是一个分数结构上的矛盾：
  下跌越强 → 4H 方向分越高 → 总分可能越高 → 越容易触发多头入场
  这对趋势延续信号是合理的（顺势做多），对支撑反弹信号是反直觉的
```

### 3.2 pocket 级评分权重覆盖

```diff
# 为 long_dual_support 设置独立的评分权重
# 核心思路：降低 4H 方向权重，提升 CVD/VWAP/15M 权重

+ pocket_specific_weights:
+   long_dual_support:
+     weight_4h_direction:    0.15   # 大幅降低（当前 0.40）
+                                    # 理由：4H 方向在此处是逆势背景，不应主导分数
+     weight_4h_enhancement:  0.10   # 不变（preflip 动量，若有仍然有用）
+     weight_1h_direction:    0.35   # 大幅提升（当前 0.20）
+                                    # 理由：反弹入场，1H 方向才是真正的确认层
+     weight_vwap:            0.25   # 提升（当前 0.20）
+                                    # 理由：支撑位质量是该 pocket 的核心 alpha
+     weight_15m_entry:       0.10   # 提升（当前 0.05）
+                                    # 理由：反弹需要更多短周期动量确认
+     weight_volume:          0.05   # 降低（当前 0.15）
+                                    # 理由：支撑反弹时成交量不一定大
```

**伪代码（评分聚合层注入）：**

```python
def get_scoring_weights(signal_type: str, vwap_structure_type: str, cfg) -> dict:
    """
    根据信号 + VWAP 结构类型返回对应的评分权重
    允许 pocket 级别覆盖全局权重
    """
    pocket_key = vwap_structure_type   # 优先按 VWAP 结构覆盖
    pocket_weights = cfg.pocket_specific_weights.get(pocket_key, None)

    if pocket_weights:
        return pocket_weights

    # fallback 到全局权重
    return cfg.scoring_weights

def compute_signal_score(features: dict, signal_type: str,
                         vwap_structure_type: str, cfg) -> float:
    """
    计算信号总分（支持 pocket 级权重覆盖）
    """
    weights = get_scoring_weights(signal_type, vwap_structure_type, cfg)

    score = (
        features["score_1h_direction"]   * weights["weight_1h_direction"]   +
        features["score_4h_direction"]   * weights["weight_4h_direction"]   +
        features["score_4h_enhancement"] * weights["weight_4h_enhancement"] +
        features["score_vwap"]           * weights["score_vwap"]            +
        features["score_15m_entry"]      * weights["weight_15m_entry"]      +
        features["score_volume"]         * weights["weight_volume"]
    )
    return score
```

---

## 四、1H 确认逻辑专项修复

### 4.1 当前 1H 确认链的问题

```
当前四种 1H 确认模式（按宽松程度排序）：

  模式 A：1H 明确做多（BULLISH）
  模式 B：1H 弱做多（WEAKLY_BULLISH）
  模式 C：1H 中性（NEUTRAL）← allow_neutral_1h = true 时允许
  模式 D：1H 轻确认（LIGHT）← light_1h_confirmation = true 时允许

  当前配置允许 C 和 D，这对 green_bar_growing 这类顺势信号合理，
  但对 long_dual_support（逆势做多）不合理。
```

### 4.2 按 pocket 类型分发 1H 确认要求

```python
def check_1h_confirmation(bar_1h, signal_type: str,
                          vwap_structure_type: str, cfg) -> tuple[bool, str]:
    """
    1H 方向确认检查，按 pocket 类型应用不同的宽严程度
    """
    pocket_key = vwap_structure_type

    # long_dual_support：必须明确做多，不接受 neutral/light
    if pocket_key == "long_dual_support":
        allowed = ("BULLISH", "WEAKLY_BULLISH")
        if bar_1h.macd_direction not in allowed:
            return False, (
                f"1H_STRICT: {bar_1h.macd_direction} not in {allowed} "
                f"(long_dual_support requires explicit 1H bullish)"
            )
        return True, "1H_OK_STRICT"

    # short_dual_pressure：对称逻辑，必须明确做空
    if pocket_key == "short_dual_pressure":
        allowed = ("BEARISH", "WEAKLY_BEARISH")
        if bar_1h.macd_direction not in allowed:
            return False, f"1H_STRICT: {bar_1h.macd_direction} not in {allowed}"
        return True, "1H_OK_STRICT"

    # 趋势延续类（green/red bar growing，continuation）：接受 neutral/light
    if cfg.allow_neutral_1h_confirmation:
        return True, "1H_OK_NEUTRAL_ALLOWED"

    # 默认：不接受 neutral
    if bar_1h.macd_direction in ("NEUTRAL",):
        return False, f"1H_NEUTRAL: not allowed by default"

    return True, "1H_OK"
```

---

## 五、预期收益改善测算

```
以下基于当前 30 天数据估算各项改造的效果：

改造一：pocket 独立 entry gate（1H 严格 + CVD 必选 + VWAP 0.16）

  预计拦截效果：long_dual_support 153 笔中，
  - 无明确 1H 看多的占比估算 40-50% → 拦截约 60-75 笔
  - CVD 不满足的占比估算 20-30% → 与 1H 有重叠，净额外拦截约 15-20 笔
  - VWAP 不足 0.16 的占比估算 15-20% → 净额外拦截约 10-15 笔
  
  总计：拦截约 70-90 笔（其中预期大部分是亏损单）
  剩余约 60-80 笔 long_dual_support

  预计效果（乐观估算）：
    剩余笔数中，亏损笔从 31 笔降到约 10-12 笔
    avg_win 可能因样本更干净略升到 8-10
    PnL 从 -236 转为 0 ~ +100

改造二：pocket 级评分权重覆盖

  效果：进一步过滤"4H 下跌强但 1H 未明确确认"的假高分信号
  预计额外减少 10-20 笔边缘信号
  PnL 改善约 +50-80

改造三：长期影响（全局收益）

  如果 long_dual_support 从 -236 转为 +50~+100：
    改善幅度: +286 ~ +336 USDT
    对应收益率提升: +2.86% ~ +3.36%（10k 本金）
    全月收益: +7.00% → +9.86% ~ +10.36%

  trade_count 会减少（153 → 约 65-80 笔 long_dual_support）：
    总 trade_count: 499 → 约 410-430 笔
    仍然在 200-300+ 的目标范围内

  win_rate 预计变化：
    亏损笔数减少比盈利笔数减少更多（拦截了大量亏损单）
    预计整体 win_rate: 83% → 85-87%
```

---

## 六、ablation 实验顺序

```
基线（当前）：
  499 笔 / 83.17% WR / +7.00% / MDD 2.87%
  long_dual_support: 153 笔 / -236.92 PnL

──────────────────────────────────────────────────────
实验 #1：禁用 long_dual_support 的 neutral/light 1H 确认

  改动：pocket_specific_gates.long_dual_support.allow_neutral_1h = false
  预期：trade_count -40~60，long_dual_support PnL 转正
  验收：
    ✓ long_dual_support PnL > 0
    ✓ 整体 win_rate ≥ 83%（不能下降）
    ✓ 整体 return 提升 ≥ +1.5%

实验 #2：在 #1 基础上，要求 CVD 必须确认

  改动：require_cvd_ok = true（flow gates 中 CVD 从可选变必选）
  预期：trade_count 再减 -15~25，质量进一步提升
  验收：
    ✓ long_dual_support 单笔平均 PnL > 0
    ✓ 整体 MDD 不上升

实验 #3：VWAP 门槛提升（0.12 → 0.16，针对该 pocket）

  改动：pocket_specific_gates.long_dual_support.min_vwap_score = 0.16
  预期：额外减少 10-15 笔边缘 VWAP 支撑不足的入场
  验收：avg_win 提升 ≥ +1（从 6.52 → ≥ 7.5）

实验 #4：评分权重覆盖（4H 降权，1H/VWAP 升权）

  改动：pocket_specific_weights.long_dual_support 完整替换
  预期：score 分布重新校准，高分区更对应真实反弹质量
  验收：long_dual_support 的 score 分布中，高分段（≥0.88）胜率 ≥ 70%

实验 #5（条件性）：如果 #1~#4 后 long_dual_support 仍然负期望

  选项 A：进一步提高门槛（min_signal_score → 0.90，require_15m_strict = true）
  选项 B：将 long_dual_support 从常规信号降级为"高置信度专用信号"
          只在 ADX ≥ 30 + 1H 方向明确 + CVD 净买盘 + VWAP ≥ 0.17 时才允许
  选项 C：废弃该 pocket（最后手段，在 A/B 都无法转正时）

──────────────────────────────────────────────────────
每轮验收 5 项指标：
  ✓ long_dual_support PnL 方向（核心目标）
  ✓ 整体 win_rate ≥ 83%
  ✓ 整体 trade_count ≥ 380（保量）
  ✓ 整体 return 相比基线提升
  ✓ MDD 不超过 3.5%
```

---

## 七、变更速查表

| 改动 | 旧值 | 新值 | 实验 | 机制说明 |
|------|------|------|------|---------|
| LD 的 allow_neutral_1h | true（全局） | **false** | #1 | 逆势做多不接受中性 1H |
| LD 的 allow_light_1h | true（全局） | **false** | #1 | 同上 |
| LD 的 require_cvd_ok | 可选 | **必选** | #2 | 支撑反弹必须有买盘确认 |
| LD 的 require_cvd_momentum_ok | 可选 | **必选** | #2 | 微结构动量必须向上 |
| LD 的 min_vwap_score | 0.12（全局） | **0.16** | #3 | 支撑质量更高要求 |
| LD 的 min_signal_score | 0.87（全局） | **0.88** | #3 | 略微收紧 |
| LD 的 weight_4h_direction | 0.40 | **0.15** | #4 | 4H 逆势背景降权 |
| LD 的 weight_1h_direction | 0.20 | **0.35** | #4 | 1H 确认层升权 |
| LD 的 weight_vwap | 0.20 | **0.25** | #4 | 支撑质量升权 |
| LD 的 weight_15m_entry | 0.05 | **0.10** | #4 | 短周期动量升权 |
| LD 的 weight_volume | 0.15 | **0.05** | #4 | 支撑反弹成交量降权 |
| LD 的 position_scale | 1.0 | **0.80** | #1 | 保量前提下降低单笔风险 |

> **LD = long_dual_support pocket 专属覆盖，不影响其他 pocket 的全局参数。**
