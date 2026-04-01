# MACD V2 E2 — Codex 强制实施规范

**日期**: 2026-04-01  
**数据来源**: 已读取并验证全部上传文件  
**执行人**: Codex  
**优先级**: P0，逐条强制完成，不得跳过

---

## 数据验证结论（Codex 必须理解）

在实施任何代码之前，以下是从真实回测数据中提取的事实，Codex 必须以此为准：

```
基线 long_dual_support 全部 154 笔的 vwap_score 分布：
  min = 0.121,  max = 0.156,  mean = 0.143

结论 1：min_vwap_score = 0.16 一条规则就能拦截 154/154 笔（100%）
         因为该 pocket 中不存在 vwap_score >= 0.16 的样本

结论 2：cvd_1h_delta_ratio 在这 154 笔中全部为 0.0
         cvd_1h_pressure 全部为 "0.0"（inactive 状态）
         require_cvd_ok 和 require_cvd_momentum_ok 需要映射到
         decision_engine 的 flow_checks / micro_checks，而不是 trades CSV 字段

结论 3：E2 对 red_bar_growing|long_dual_support 产生了 0 笔成交
         3 笔存活的 long_dual_support 全部是 green_bar_growing，
         不受 pocket_entry_overrides["red_bar_growing|long_dual_support"] 约束

结论 4：E2 配置与基线的唯一差异是：
         新增 fund_flow.macd_mtf_strategy_v2.entry_filters.pocket_entry_overrides
         新增 fund_flow.vwap_structure_overrides
         其余所有参数完全一致

结论 5：E2 效果：499→438 笔 / 83.17%→84.02% / +7.00%→+12.31% / MDD 2.87%→2.34%
         red_bar_growing|long_reclaim_confirmed 同步改善：72→102 笔 / 80.6%→87.3% / +200→+658
```

---

## 一、目标：代码层实现 pocket_entry_overrides

**E2 配置文件已经存在**：  
`config/candidates/trading_config_fund_flow_ld_support_e2_cvd_vwap_score.json`

**基线配置文件**：  
`config/trading_config_fund_flow.json`

**唯一问题**：代码目前不读取也不执行 `pocket_entry_overrides` 和 `vwap_structure_overrides`。  
Codex 的任务是实现这两个字段的完整读取和执行逻辑。

---

## 二、配置结构（Codex 必须按此精确读取）

### 2.1 pocket_entry_overrides 完整结构

路径：`fund_flow.macd_mtf_strategy_v2.entry_filters.pocket_entry_overrides`

```json
{
  "red_bar_growing|long_dual_support": {
    "label": "ld_support_e2_cvd_vwap_score",
    "allow_neutral_1h_confirmation": false,
    "require_strict_1h_confirmation": true,
    "disallow_trial_entry": true,
    "min_signal_score": 0.88,
    "min_vwap_score": 0.16,
    "min_entry_score": 0.50,
    "require_cvd_ok": true,
    "require_cvd_momentum_ok": true
  }
}
```

### 2.2 vwap_structure_overrides 完整结构

路径：`fund_flow.vwap_structure_overrides`（fund_flow 顶层，与 macd_mtf_strategy_v2 同级）

```json
{
  "long_dual_support": {
    "position_scale_override": 0.80
  }
}
```

### 2.3 pocket key 构造规则

```
pocket_key = f"{signal_type_1h}|{vwap_state}"

示例：
  signal_type_1h = "red_bar_growing"
  vwap_state     = "long_dual_support"
  pocket_key     = "red_bar_growing|long_dual_support"
```

---

## 三、代码实现规范

### 3.1 新增函数：check_pocket_entry_override

**文件**：`src/fund_flow/macd_strategy_v2.py`

实现以下函数，不得有任何 TODO、pass、占位符：

```python
def check_pocket_entry_override(
    signal_type: str,
    vwap_state: str,
    is_trial_entry: bool,
    signal_score: float,
    vwap_score: float,
    entry_score: float,
    bar_1h_direction: str,
    flow_cvd_ok: bool,
    micro_cvd_momentum_ok: bool,
    pocket_entry_overrides: dict,
) -> tuple[bool, str]:
    """
    在 entry_hard_gates 通过后执行 pocket 级独立准入检查。

    参数说明：
      signal_type           : trades CSV 中的 signal_type_1h 字段对应值
      vwap_state            : trades CSV 中的 vwap_state 字段对应值
      is_trial_entry        : 当前信号是否为 trial entry
      signal_score          : 信号总分
      vwap_score            : VWAP 结构分（trades CSV 中的 vwap_score）
      entry_score           : 15M 入场分
      bar_1h_direction      : 1H MACD 方向字符串，如 "BULLISH"/"NEUTRAL"/"BEARISH"
      flow_cvd_ok           : L2 flow gate 中 cvd_ok 的结果（bool）
      micro_cvd_momentum_ok : L3 micro gate 中 cvd_momentum_ok 的结果（bool）
      pocket_entry_overrides: cfg["fund_flow"]["macd_mtf_strategy_v2"]
                               ["entry_filters"]["pocket_entry_overrides"]

    返回：(passed: bool, reason: str)
    """
    pocket_key = f"{signal_type}|{vwap_state}"
    pocket_cfg = pocket_entry_overrides.get(pocket_key)

    if not pocket_cfg:
        return True, "POCKET_GATE:NO_OVERRIDE"

    # 检查 1：trial entry 禁用
    if pocket_cfg.get("disallow_trial_entry", False) and is_trial_entry:
        return False, (
            f"POCKET_GATE[{pocket_key}]:TRIAL_DISALLOWED "
            f"is_trial_entry={is_trial_entry}"
        )

    # 检查 2：1H 方向严格确认
    if pocket_cfg.get("require_strict_1h_confirmation", False):
        allowed = {"BULLISH", "WEAKLY_BULLISH"}
        if bar_1h_direction not in allowed:
            return False, (
                f"POCKET_GATE[{pocket_key}]:1H_NOT_BULLISH "
                f"bar_1h_direction={bar_1h_direction} "
                f"allowed={sorted(allowed)}"
            )
    elif pocket_cfg.get("allow_neutral_1h_confirmation") is False:
        if bar_1h_direction == "NEUTRAL":
            return False, (
                f"POCKET_GATE[{pocket_key}]:1H_NEUTRAL_BLOCKED "
                f"bar_1h_direction={bar_1h_direction}"
            )

    # 检查 3：signal_score 下限
    min_signal_score = pocket_cfg.get("min_signal_score")
    if min_signal_score is not None and signal_score < min_signal_score:
        return False, (
            f"POCKET_GATE[{pocket_key}]:SIGNAL_SCORE_LOW "
            f"{signal_score:.4f} < {min_signal_score}"
        )

    # 检查 4：vwap_score 下限（数据验证：此条拦截基线全部 154 笔）
    min_vwap_score = pocket_cfg.get("min_vwap_score")
    if min_vwap_score is not None and vwap_score < min_vwap_score:
        return False, (
            f"POCKET_GATE[{pocket_key}]:VWAP_SCORE_LOW "
            f"{vwap_score:.4f} < {min_vwap_score}"
        )

    # 检查 5：entry_score（15M 入场分）下限
    min_entry_score = pocket_cfg.get("min_entry_score")
    if min_entry_score is not None and entry_score < min_entry_score:
        return False, (
            f"POCKET_GATE[{pocket_key}]:ENTRY_SCORE_LOW "
            f"{entry_score:.4f} < {min_entry_score}"
        )

    # 检查 6：CVD 净买盘（flow gate L2）
    if pocket_cfg.get("require_cvd_ok", False) and not flow_cvd_ok:
        return False, (
            f"POCKET_GATE[{pocket_key}]:CVD_NOT_OK "
            f"flow_cvd_ok={flow_cvd_ok}"
        )

    # 检查 7：CVD 动量（micro gate L3）
    if pocket_cfg.get("require_cvd_momentum_ok", False) and not micro_cvd_momentum_ok:
        return False, (
            f"POCKET_GATE[{pocket_key}]:CVD_MOMENTUM_NOT_OK "
            f"micro_cvd_momentum_ok={micro_cvd_momentum_ok}"
        )

    return True, f"POCKET_GATE[{pocket_key}]:PASS"
```

### 3.2 新增函数：get_vwap_structure_position_scale

**文件**：`src/fund_flow/decision_engine.py` 或 `fund_flow_bot.py`（仓位计算所在文件）

```python
def get_vwap_structure_position_scale(
    vwap_state: str,
    vwap_structure_overrides: dict,
) -> float:
    """
    从 fund_flow.vwap_structure_overrides 读取仓位缩比。
    vwap_state 不在 overrides 中时返回 1.0（不缩放）。

    参数：
      vwap_state              : 当前信号的 vwap_state 字段值
      vwap_structure_overrides: cfg["fund_flow"]["vwap_structure_overrides"]
    返回：float，乘以 default_target_portion 得到实际仓位
    """
    structure_cfg = vwap_structure_overrides.get(vwap_state, {})
    return float(structure_cfg.get("position_scale_override", 1.0))
```

### 3.3 在决策主链路中插入 pocket gate 调用

**文件**：`src/fund_flow/decision_engine.py`（或等效的信号决策函数）

**插入位置**：entry_hard_gates（L1/L2/L3）全部通过之后，生成 BUY/SELL action 之前。

调用代码如下，必须完整插入，不得简化：

```python
# ── Pocket-level entry override gate ─────────────────────────────────────────
_pocket_overrides = (
    self.cfg
    .get("fund_flow", {})
    .get("macd_mtf_strategy_v2", {})
    .get("entry_filters", {})
    .get("pocket_entry_overrides", {})
)

_pocket_passed, _pocket_reason = check_pocket_entry_override(
    signal_type=signal.signal_type,          # e.g. "red_bar_growing"
    vwap_state=signal.vwap_state,            # e.g. "long_dual_support"
    is_trial_entry=signal.is_trial_entry,    # bool
    signal_score=signal.signal_score,
    vwap_score=signal.vwap_score,
    entry_score=signal.entry_score_15m,
    bar_1h_direction=bar_1h.macd_direction,
    flow_cvd_ok=flow_check_results.get("cvd_ok", False),
    micro_cvd_momentum_ok=micro_check_results.get("cvd_momentum_ok", False),
    pocket_entry_overrides=_pocket_overrides,
)

if not _pocket_passed:
    self._record_reject(signal, _pocket_reason)
    continue   # 或 return，视函数结构决定
# ── End pocket gate ───────────────────────────────────────────────────────────
```

### 3.4 在仓位计算路径中插入 vwap_structure 缩比

**文件**：仓位计算函数所在文件

**插入位置**：`default_target_portion` 确定之后、提交订单之前

```python
# ── vwap_structure position scale ─────────────────────────────────────────────
_vwap_structure_overrides = self.cfg.get("fund_flow", {}).get("vwap_structure_overrides", {})
_position_scale = get_vwap_structure_position_scale(
    vwap_state=signal.vwap_state,
    vwap_structure_overrides=_vwap_structure_overrides,
)
actual_target_portion = cfg.default_target_portion * _position_scale
# ── End vwap_structure scale ──────────────────────────────────────────────────
```

---

## 四、单元测试（必须全部实现，必须全部通过）

**新建文件**：`tests/test_pocket_entry_override.py`

以下 9 个测试用例全部实现，不得跳过，不得使用任何形式的 skip：

```python
import pytest
from src.fund_flow.macd_strategy_v2 import check_pocket_entry_override
from src.fund_flow.decision_engine import get_vwap_structure_position_scale

# ── E2 配置常量（来自真实 E2 config 文件）────────────────────────────────────
E2_OVERRIDES = {
    "red_bar_growing|long_dual_support": {
        "label": "ld_support_e2_cvd_vwap_score",
        "allow_neutral_1h_confirmation": False,
        "require_strict_1h_confirmation": True,
        "disallow_trial_entry": True,
        "min_signal_score": 0.88,
        "min_vwap_score": 0.16,
        "min_entry_score": 0.50,
        "require_cvd_ok": True,
        "require_cvd_momentum_ok": True,
    }
}

E2_VWAP_OVERRIDES = {
    "long_dual_support": {"position_scale_override": 0.80}
}

# 数据验证：基线 long_dual_support 的 vwap_score 最大值是 0.156，
# 所以 min_vwap_score=0.16 会拦截全部 154 笔。
# 以下测试用实际数据中的边界值验证这一行为。

PASS_KWARGS = dict(
    signal_type="red_bar_growing",
    vwap_state="long_dual_support",
    is_trial_entry=False,
    signal_score=0.89,
    vwap_score=0.17,          # 明确高于 0.16
    entry_score=0.55,
    bar_1h_direction="BULLISH",
    flow_cvd_ok=True,
    micro_cvd_momentum_ok=True,
    pocket_entry_overrides=E2_OVERRIDES,
)


def test_vwap_score_156_blocked():
    """
    实测：基线 long_dual_support 的 vwap_score 最大值为 0.156 < 0.16，
    必须被 min_vwap_score=0.16 拦截。
    """
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "vwap_score": 0.156}
    )
    assert not passed, "vwap_score=0.156 (实测最大值) 必须被拦截"
    assert "VWAP_SCORE_LOW" in reason


def test_vwap_score_at_threshold_passes():
    """vwap_score 精确等于 0.16 时必须通过（含边界）"""
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "vwap_score": 0.16}
    )
    assert passed, f"vwap_score=0.16 应通过，原因: {reason}"


def test_trial_entry_blocked():
    """is_trial_entry=True 必须被 disallow_trial_entry 拦截"""
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "is_trial_entry": True}
    )
    assert not passed
    assert "TRIAL_DISALLOWED" in reason


def test_neutral_1h_blocked():
    """bar_1h_direction=NEUTRAL 必须被 require_strict_1h_confirmation 拦截"""
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "bar_1h_direction": "NEUTRAL"}
    )
    assert not passed
    assert "1H_NOT_BULLISH" in reason


def test_bearish_1h_blocked():
    """bar_1h_direction=BEARISH 必须被拦截"""
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "bar_1h_direction": "BEARISH"}
    )
    assert not passed


def test_signal_score_below_088_blocked():
    """signal_score=0.879 必须被 min_signal_score=0.88 拦截"""
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "signal_score": 0.879}
    )
    assert not passed
    assert "SIGNAL_SCORE_LOW" in reason


def test_cvd_not_ok_blocked():
    """flow_cvd_ok=False 必须被 require_cvd_ok 拦截"""
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "flow_cvd_ok": False}
    )
    assert not passed
    assert "CVD_NOT_OK" in reason


def test_cvd_momentum_not_ok_blocked():
    """micro_cvd_momentum_ok=False 必须被 require_cvd_momentum_ok 拦截"""
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "micro_cvd_momentum_ok": False}
    )
    assert not passed
    assert "CVD_MOMENTUM_NOT_OK" in reason


def test_all_conditions_met_passes():
    """所有条件满足时必须通过，且 reason 包含 PASS"""
    passed, reason = check_pocket_entry_override(**PASS_KWARGS)
    assert passed, f"全部条件满足时应通过，实际原因: {reason}"
    assert "PASS" in reason


def test_no_override_always_passes():
    """没有对应 pocket override 时，无论其他参数如何，必须通过"""
    passed, reason = check_pocket_entry_override(
        signal_type="green_bar_growing",
        vwap_state="short_dual_pressure",
        is_trial_entry=True,
        signal_score=0.50,
        vwap_score=0.05,
        entry_score=0.10,
        bar_1h_direction="NEUTRAL",
        flow_cvd_ok=False,
        micro_cvd_momentum_ok=False,
        pocket_entry_overrides=E2_OVERRIDES,  # 无对应 key
    )
    assert passed
    assert "NO_OVERRIDE" in reason


def test_vwap_structure_scale_long_dual_support():
    """long_dual_support 的 position_scale_override 必须返回 0.80"""
    scale = get_vwap_structure_position_scale("long_dual_support", E2_VWAP_OVERRIDES)
    assert scale == 0.80, f"Expected 0.80, got {scale}"


def test_vwap_structure_scale_unknown_returns_one():
    """无 override 的 vwap_state 必须返回 1.0（不缩放）"""
    scale = get_vwap_structure_position_scale("short_dual_pressure", E2_VWAP_OVERRIDES)
    assert scale == 1.0, f"Expected 1.0, got {scale}"
```

---

## 五、回归测试

实现上述代码和测试后，执行以下命令，**0 failure 0 error** 才允许提交：

```powershell
pytest tests/test_pocket_entry_override.py `
       tests/test_macd_strategy_v2_4h_scoring.py `
       tests/test_fund_flow_decision_engine.py `
       tests/test_fund_flow_bot_regressions.py `
       -v --tb=short 2>&1 | tail -30
```

---

## 六、Production 配置文件生成

将 E2 candidate 配置提升为 production：

```powershell
copy config\candidates\trading_config_fund_flow_ld_support_e2_cvd_vwap_score.json `
     config\trading_config_fund_flow.json
```

在文件的 `_metadata`（若存在）或顶部注释位置追加：

```json
"_e2_promotion_date": "2026-04-01",
"_e2_baseline": "499 trades / 83.17% WR / +7.00% / MDD 2.87%",
"_e2_result":   "438 trades / 84.02% WR / +12.31% / MDD 2.34%"
```

---

## 七、验收清单（提交前逐项确认）

Codex 必须在提交前完成以下所有检查，缺一不可：

```
[ ] check_pocket_entry_override() 已实现，包含全部 7 个检查分支
    每个分支的 reject reason 字符串格式为 "POCKET_GATE[{key}]:{REASON}"
[ ] get_vwap_structure_position_scale() 已实现
[ ] pocket gate 已在决策主链路正确插入
    插入位置：L1/L2/L3 hard gates 全部通过后，BUY/SELL 生成前
[ ] vwap_structure 仓位缩比已在仓位计算路径正确插入
[ ] tests/test_pocket_entry_override.py 已创建
    包含全部 12 个测试用例（含边界值 vwap_score=0.156 和 vwap_score=0.16）
[ ] pytest 全量运行结果：0 failure，0 error
[ ] production 配置文件已更新
```

---

## 八、禁止事项（违反即拒绝合并）

```
禁止 1：不得修改 entry_hard_gates (L1/L2/L3) 的任何现有逻辑
禁止 2：不得修改 green_bar_growing / short_dual_pressure / long_reclaim_confirmed
         / short_retest_reject 任何相关逻辑
禁止 3：不得修改全局 stop_loss_pct / take_profit_pct / partial_tp_levels
禁止 4：不得修改全局 allow_neutral_1h_confirmation / light_1h_confirmation_when_4h_primary
         （只允许在 pocket_entry_overrides 层面覆盖，不改全局字段）
禁止 5：check_pocket_entry_override() 内不得有任何 pass / TODO / raise NotImplementedError
禁止 6：测试文件内不得有任何 pytest.skip / @pytest.mark.skip / unittest.skip
禁止 7：不得以"暂时硬编码"替代读取 pocket_entry_overrides 配置
         所有 pocket key 和阈值必须从配置动态读取，不得写死在代码里
```
