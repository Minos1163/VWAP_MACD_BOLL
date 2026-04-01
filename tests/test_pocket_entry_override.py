import pytest

from src.fund_flow.macd_strategy_v2 import check_pocket_entry_override
from src.fund_flow.decision_engine import get_vwap_structure_position_scale


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

BEARISH_OVERRIDES = {
    "red_bar_growing|short_dual_pressure": {
        "require_strict_1h_confirmation": True,
        "strict_1h_direction": "bearish",
        "min_signal_score": 0.88,
        "min_vwap_score": 0.155,
        "require_cvd_ok": True,
        "require_cvd_momentum_ok": True,
    }
}

PASS_KWARGS = dict(
    signal_type="red_bar_growing",
    vwap_state="long_dual_support",
    is_trial_entry=False,
    signal_score=0.89,
    vwap_score=0.17,
    entry_score=0.55,
    bar_1h_direction="BULLISH",
    flow_cvd_ok=True,
    micro_cvd_momentum_ok=True,
    pocket_entry_overrides=E2_OVERRIDES,
)


def test_vwap_score_156_blocked():
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "vwap_score": 0.156}
    )
    assert not passed, "vwap_score=0.156 (实测最大值) 必须被拦截"
    assert "VWAP_SCORE_LOW" in reason


def test_vwap_score_at_threshold_passes():
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "vwap_score": 0.16}
    )
    assert passed, f"vwap_score=0.16 应通过，原因: {reason}"


def test_trial_entry_blocked():
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "is_trial_entry": True}
    )
    assert not passed
    assert "TRIAL_DISALLOWED" in reason


def test_neutral_1h_blocked():
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "bar_1h_direction": "NEUTRAL"}
    )
    assert not passed
    assert "1H_NOT_BULLISH" in reason


def test_bearish_1h_blocked():
    passed, _ = check_pocket_entry_override(
        **{**PASS_KWARGS, "bar_1h_direction": "BEARISH"}
    )
    assert not passed


def test_signal_score_below_088_blocked():
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "signal_score": 0.879}
    )
    assert not passed
    assert "SIGNAL_SCORE_LOW" in reason


def test_cvd_not_ok_blocked():
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "flow_cvd_ok": False}
    )
    assert not passed
    assert "CVD_NOT_OK" in reason


def test_cvd_momentum_not_ok_blocked():
    passed, reason = check_pocket_entry_override(
        **{**PASS_KWARGS, "micro_cvd_momentum_ok": False}
    )
    assert not passed
    assert "CVD_MOMENTUM_NOT_OK" in reason


def test_all_conditions_met_passes():
    passed, reason = check_pocket_entry_override(**PASS_KWARGS)
    assert passed, f"全部条件满足时应通过，实际原因: {reason}"
    assert "PASS" in reason


def test_no_override_always_passes():
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
        pocket_entry_overrides=E2_OVERRIDES,
    )
    assert passed
    assert "NO_OVERRIDE" in reason


def test_vwap_structure_scale_long_dual_support():
    scale = get_vwap_structure_position_scale("long_dual_support", E2_VWAP_OVERRIDES)
    assert scale == 0.80, f"Expected 0.80, got {scale}"


def test_vwap_structure_scale_unknown_returns_one():
    scale = get_vwap_structure_position_scale("short_dual_pressure", E2_VWAP_OVERRIDES)
    assert scale == 1.0, f"Expected 1.0, got {scale}"


def test_strict_1h_direction_bearish_blocks_bullish_bar():
    passed, reason = check_pocket_entry_override(
        signal_type="red_bar_growing",
        vwap_state="short_dual_pressure",
        is_trial_entry=False,
        signal_score=0.90,
        vwap_score=0.16,
        entry_score=0.55,
        bar_1h_direction="BULLISH",
        flow_cvd_ok=True,
        micro_cvd_momentum_ok=True,
        pocket_entry_overrides=BEARISH_OVERRIDES,
    )
    assert not passed
    assert "1H_NOT_BEARISH" in reason


def test_strict_1h_direction_bearish_accepts_bearish_bar():
    passed, reason = check_pocket_entry_override(
        signal_type="red_bar_growing",
        vwap_state="short_dual_pressure",
        is_trial_entry=False,
        signal_score=0.90,
        vwap_score=0.16,
        entry_score=0.55,
        bar_1h_direction="BEARISH",
        flow_cvd_ok=True,
        micro_cvd_momentum_ok=True,
        pocket_entry_overrides=BEARISH_OVERRIDES,
    )
    assert passed, reason
    assert "PASS" in reason
