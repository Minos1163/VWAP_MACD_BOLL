from __future__ import annotations

from datetime import datetime, timezone

import pytest
import numpy as np

from src.fund_flow.macd_strategy_v2 import (
    MACDStrategyV2Config,
    MACDStrategyV2Engine,
    build_macd_v2_config_from_runtime,
)


def test_config_defaults_reflect_iteration1_ablation_targets() -> None:
    cfg = MACDStrategyV2Config()

    assert cfg.preflip_trial_min_shrink_pct_long == pytest.approx(0.45, rel=1e-6)
    assert cfg.preflip_trial_min_shrink_pct_short == pytest.approx(0.22, rel=1e-6)
    assert cfg.preflip_trial_min_signal_score == pytest.approx(0.70, rel=1e-6)
    assert cfg.min_vwap_score_for_entry == pytest.approx(0.10, rel=1e-6)
    assert cfg.stable_bear_continuation_min_vwap_score == pytest.approx(0.07, rel=1e-6)
    assert cfg.q4_rsi_lead_preflip_allowed_symbols == ["ETHUSDT"]
    assert cfg.q4_rsi_lead_preflip_strict_eth_only is True
    assert cfg.min_signal_score == pytest.approx(0.80, rel=1e-6)
    assert cfg.red_bar_growing_min_signal_score == pytest.approx(0.92, rel=1e-6)
    assert cfg.flip_bearish_min_signal_score == pytest.approx(0.80, rel=1e-6)
    assert cfg.flip_bullish_min_signal_score == pytest.approx(0.80, rel=1e-6)
    assert cfg.stable_bear_continuation_min_signal_score == pytest.approx(0.80, rel=1e-6)
    assert cfg.stable_bull_continuation_min_signal_score == pytest.approx(0.80, rel=1e-6)


def test_analyze_uses_4h_as_primary_score_source() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.0,
            weight_4h_direction=0.5,
            weight_4h_enhancement=0.0,
            weight_vwap=0.0,
            weight_15m_entry=0.0,
            weight_volume=0.15,
            min_signal_score=0.1,
            min_entry_score=0.1,
            red_bar_growing_min_signal_score=0.1,
            flip_bullish_min_signal_score=0.1,
            min_vwap_score_for_entry=0.0,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="4h",
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([-0.10, -0.05, 0.02, 0.05]),
        macd_hist_1h=np.array([-0.20, -0.10, 0.05, 0.10]),
        macd_hist_4h=np.array([-0.30, -0.15, -0.05, 0.20]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=2.0,
        vwap=100.5,
        structural_vwap=100.0,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    assert signal.direction == "long"
    assert signal.signal_score == pytest.approx(0.65, rel=1e-6)
    assert signal.details["primary_timeframe"] == "4h"
    assert signal.details["score_4h"] == pytest.approx(0.5, rel=1e-6)
    assert signal.details["score_1h"] == pytest.approx(0.0, rel=1e-6)


def test_analyze_counts_1h_direction_score_and_boll_modifier() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.2,
            weight_4h_direction=0.35,
            weight_4h_enhancement=0.0,
            weight_vwap=0.0,
            weight_15m_entry=0.0,
            weight_volume=0.15,
            min_signal_score=0.1,
            min_entry_score=0.1,
            red_bar_growing_min_signal_score=0.1,
            flip_bullish_min_signal_score=0.1,
            min_vwap_score_for_entry=0.0,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="4h",
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([-0.10, -0.05, 0.02, 0.05]),
        macd_hist_1h=np.array([-0.20, -0.10, 0.05, 0.10]),
        macd_hist_4h=np.array([-0.30, -0.15, -0.05, 0.20]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=2.0,
        vwap=100.5,
        structural_vwap=100.0,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    assert signal.direction == "long"
    assert signal.details["score_1h"] == pytest.approx(0.175, rel=1e-6)
    assert signal.signal_score == pytest.approx(0.675, rel=1e-6)


def test_strong_boll_structure_does_not_boost_growing_signal_to_full_weight() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.2,
            weight_4h_direction=0.0,
            weight_4h_enhancement=0.0,
            weight_vwap=0.0,
            weight_15m_entry=0.0,
            weight_volume=0.0,
            min_signal_score=0.1,
            min_entry_score=0.1,
            red_bar_growing_min_signal_score=0.1,
            flip_bullish_min_signal_score=0.1,
            min_vwap_score_for_entry=0.0,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="1h",
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([-0.10, -0.05, 0.02, 0.05]),
        macd_hist_1h=np.array([-0.20, -0.10, 0.05, 0.10]),
        macd_hist_4h=np.array([-0.30, -0.15, -0.05, 0.20]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=2.0,
        vwap=100.5,
        structural_vwap=100.0,
        close_price=103.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    assert signal.details["ema_multiplier"] == pytest.approx(1.2, rel=1e-6)
    assert signal.details["score_1h"] == pytest.approx(0.175, rel=1e-6)


def test_flip_bullish_vwap_context_requires_long_reclaim_confirmed() -> None:
    engine = MACDStrategyV2Engine(MACDStrategyV2Config())

    ok, reasons, _details = engine.check_flip_bullish_vwap_context(
        signal_type_1h="flip_bullish",
        vwap_state="long_dual_support",
        vwap_score=0.16,
        structural_vwap=100.0,
        session_deviation=0.001,
        structural_deviation=0.001,
    )
    assert ok is False
    assert any("vwap_state=long_dual_support" in reason for reason in reasons)

    ok, reasons, _details = engine.check_flip_bullish_vwap_context(
        signal_type_1h="flip_bullish",
        vwap_state="long_reclaim_confirmed",
        vwap_score=0.16,
        structural_vwap=100.0,
        session_deviation=0.001,
        structural_deviation=0.001,
    )
    assert ok is True
    assert reasons == []


def test_flip_vwap_context_filters_are_disabled_when_thresholds_removed() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            disable_vwap_thresholds=True,
            flip_bullish_min_vwap_score=0.25,
            flip_bearish_retest_reject_min_vwap_score=0.25,
        )
    )

    bullish_ok, bullish_reasons, bullish_details = engine.check_flip_bullish_vwap_context(
        signal_type_1h="flip_bullish",
        vwap_state="vwap_disabled",
        vwap_score=0.0,
        structural_vwap=0.0,
        session_deviation=0.0,
        structural_deviation=0.0,
    )
    assert bullish_ok is True
    assert bullish_reasons == []
    assert bullish_details["vwap_thresholds_disabled"] is True

    bearish_ok, bearish_reasons, bearish_details = engine.check_flip_bearish_vwap_context(
        signal_type_1h="flip_bearish",
        market_quadrant="II",
        vwap_state="vwap_disabled",
        vwap_execution_state="unknown",
        vwap_score=0.0,
        structural_vwap=0.0,
        session_deviation=0.0,
        structural_deviation=0.0,
    )
    assert bearish_ok is True
    assert bearish_reasons == []
    assert bearish_details["vwap_thresholds_disabled"] is True


def test_flip_bullish_trial_score_window_blocks_out_of_window_scores() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            flip_bullish_trial_score_window_enabled=True,
            flip_bullish_trial_score_min=0.80,
            flip_bullish_trial_score_max=0.87,
        )
    )

    allowed, reason = engine.check_flip_bullish_trial_score_window(
        signal_type_1h="flip_bullish",
        is_trial_entry=True,
        signal_score=0.84,
    )
    assert allowed is True
    assert reason == ""

    allowed, reason = engine.check_flip_bullish_trial_score_window(
        signal_type_1h="flip_bullish",
        is_trial_entry=True,
        signal_score=0.90,
    )
    assert allowed is False
    assert "flip_bullish_trial_score_window" in reason


def test_red_bar_shrinking_global_disable_blocks_signal_family() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.2,
            weight_4h_direction=0.0,
            weight_4h_enhancement=0.0,
            weight_vwap=0.0,
            weight_15m_entry=0.0,
            weight_volume=0.0,
            min_signal_score=0.1,
            min_entry_score=0.1,
            red_bar_shrinking_min_signal_score=0.1,
            red_bar_growing_min_signal_score=0.1,
            flip_bullish_min_signal_score=0.1,
            min_vwap_score_for_entry=0.0,
            overheat_growing_penalty=0.0,
            disable_red_bar_shrinking_entries=True,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="1h",
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([0.01, 0.02, 0.03, 0.04]),
        macd_hist_1h=np.array([0.40, 0.30, 0.20, 0.10]),
        macd_hist_4h=np.array([0.10, 0.10, 0.10, 0.10]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=1.0,
        vwap=100.0,
        structural_vwap=100.0,
        close_price=100.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=100.0,
        bb_upper_4h=110.0,
        bb_lower_4h=90.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.0,
        adx_1h=20.0,
        adx_4h=20.0,
        atr_1h=1.0,
    )

    assert signal.direction == "neutral"
    assert signal.signal_type_1h == "red_bar_shrinking"
    assert signal.details["reason"] == "red_bar_shrinking_disabled"
    assert signal.details["reject_reason_code"] == "red_bar_shrinking_disabled"


def test_market_quadrant_classification_uses_4h_macd_and_1h_boll_mid() -> None:
    engine = MACDStrategyV2Engine(MACDStrategyV2Config())

    assert engine._classify_market_quadrant(0.2, 101.0, 100.0) == "I"
    assert engine._classify_market_quadrant(0.2, 99.0, 100.0) == "II"
    assert engine._classify_market_quadrant(-0.2, 99.0, 100.0) == "III"
    assert engine._classify_market_quadrant(-0.2, 101.0, 100.0) == "IV"


def test_boll_position_score_prefers_midline_reclaim_over_upper_band_chase() -> None:
    engine = MACDStrategyV2Engine(MACDStrategyV2Config(weight_boll_position=0.25))

    near_mid = engine._calc_boll_position_score(
        close_price=100.8,
        bb_upper=110.0,
        bb_lower=90.0,
        bb_middle=100.0,
        direction="long",
    )
    near_upper = engine._calc_boll_position_score(
        close_price=109.5,
        bb_upper=110.0,
        bb_lower=90.0,
        bb_middle=100.0,
        direction="long",
    )
    below_mid = engine._calc_boll_position_score(
        close_price=98.0,
        bb_upper=110.0,
        bb_lower=90.0,
        bb_middle=100.0,
        direction="long",
    )

    assert near_mid == pytest.approx(0.25, rel=1e-6)
    assert near_upper < near_mid
    assert below_mid == pytest.approx(0.0, rel=1e-6)


def test_flip_bullish_bottom_structure_allows_recent_zero_cross_after_convergence() -> None:
    engine = MACDStrategyV2Engine(MACDStrategyV2Config())

    ok, bars = engine._check_flip_bullish_bottom_structure(
        macd_line_current=-0.05,
        macd_line_series=np.array([-0.20, -0.15, -0.10, -0.08, -0.05]),
    )
    assert ok is True
    assert bars >= 3

    crossing_ok, crossing_bars = engine._check_flip_bullish_bottom_structure(
        macd_line_current=0.02,
        macd_line_series=np.array([-0.20, -0.15, -0.10, 0.02, 0.03]),
    )
    assert crossing_ok is True
    assert crossing_bars >= 2

    bad_ok, bad_bars = engine._check_flip_bullish_bottom_structure(
        macd_line_current=0.02,
        macd_line_series=np.array([-0.20, -0.15, -0.16, 0.02, 0.03]),
    )
    assert bad_ok is False
    assert bad_bars < 2


def test_flip_bearish_vwap_context_allows_favorable_only_in_quadrant_iii() -> None:
    engine = MACDStrategyV2Engine(MACDStrategyV2Config(flip_bearish_retest_reject_min_vwap_score=0.18))

    ok, reasons, details = engine.check_flip_bearish_vwap_context(
        signal_type_1h="flip_bearish",
        market_quadrant="III",
        vwap_state="short_dual_pressure",
        vwap_execution_state="favorable",
        vwap_score=0.05,
        structural_vwap=0.0,
        session_deviation=-0.01,
        structural_deviation=-0.01,
    )
    assert ok is True
    assert reasons == []
    assert "favorable" in details["flip_bearish_allowed_vwap_states"]

    ok, reasons, _details = engine.check_flip_bearish_vwap_context(
        signal_type_1h="flip_bearish",
        market_quadrant="IV",
        vwap_state="short_dual_pressure",
        vwap_execution_state="favorable",
        vwap_score=0.05,
        structural_vwap=100.0,
        session_deviation=-0.01,
        structural_deviation=-0.01,
    )
    assert ok is False
    assert any("vwap_state=short_dual_pressure" in reason for reason in reasons)


def test_state_machine_gates_green_bar_growing_below_boll_mid_even_when_4h_home_is_long() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.25,
            weight_4h_direction=0.35,
            weight_boll_position=0.25,
            weight_vwap=0.05,
            weight_15m_entry=0.0,
            weight_volume=0.10,
            min_signal_score=0.1,
            min_entry_score=0.1,
            green_bar_growing_score_window_enabled=False,
            red_bar_growing_min_signal_score=0.1,
            flip_bullish_min_signal_score=0.1,
            min_vwap_score_for_entry=0.0,
            overheat_growing_penalty=0.0,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            require_macd_home_advantage=True,
            disable_red_bar_shrinking_entries=True,
            disable_green_bar_shrinking_entries=True,
            primary_direction_timeframe="4h",
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([0.01, 0.02, 0.03, 0.04]),
        macd_hist_1h=np.array([0.05, 0.08, 0.10, 0.12]),
        macd_hist_4h=np.array([0.10, 0.12, 0.15, 0.18]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=1.2,
        vwap=100.0,
        structural_vwap=100.0,
        close_price=99.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=99.0,
        close_1h_series=np.array([98.0, 98.5, 99.0, 99.5, 99.0]),
        close_4h_series=np.array([95.0, 96.0, 97.0, 98.0, 99.0]),
        adx_1h=22.0,
        adx_4h=25.0,
        atr_1h=1.0,
        macd_line_1h=0.08,
        macd_line_4h=0.20,
    )

    assert signal.direction == "neutral"
    assert signal.details["reject_reason_code"] == "quadrant_signal_block"
    assert signal.details["market_quadrant"] == "II"


def test_state_machine_assigns_tier1_to_quadrant_i_flip_bullish_with_favorable_vwap() -> None:
    engine = MACDStrategyV2Engine(MACDStrategyV2Config())

    tier = engine._resolve_entry_tier(
        market_quadrant="I",
        signal_type_1h="flip_bullish",
        vwap_execution_state="favorable",
    )
    assert tier == "tier1"

    fallback_tier = engine._resolve_entry_tier(
        market_quadrant="II",
        signal_type_1h="flip_bullish",
        vwap_execution_state="discount_reclaim_ok",
    )
    assert fallback_tier == "tier2"

    blocked_growing = engine._resolve_entry_tier(
        market_quadrant="I",
        signal_type_1h="green_bar_growing",
        vwap_execution_state="favorable",
    )
    assert blocked_growing == "blocked"


def test_entry_tier_maps_directly_to_leverage_and_target_portion() -> None:
    engine = MACDStrategyV2Engine(MACDStrategyV2Config())

    assert engine.calculate_leverage(0.2, entry_tier="tier1") == 5
    assert engine.calculate_leverage(0.2, entry_tier="tier2") == 4
    assert engine.calculate_leverage(0.2, entry_tier="tier3") == 3

    assert engine.calculate_position_portion(
        score=0.2,
        base_default_portion=0.22,
        base_max_symbol_position_portion=0.5,
        entry_tier="tier1",
    ) == pytest.approx(0.30, rel=1e-6)
    assert engine.calculate_position_portion(
        score=0.2,
        base_default_portion=0.22,
        base_max_symbol_position_portion=0.5,
        entry_tier="tier2",
    ) == pytest.approx(0.25, rel=1e-6)
    assert engine.calculate_position_portion(
        score=0.2,
        base_default_portion=0.22,
        base_max_symbol_position_portion=0.5,
        entry_tier="tier3",
    ) == pytest.approx(0.20, rel=1e-6)


def test_green_bar_growing_score_window_blocks_extreme_scores() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            green_bar_growing_score_window_enabled=True,
            green_bar_growing_score_min=0.86,
            green_bar_growing_score_max=0.95,
        )
    )

    allowed, reason = engine.check_green_bar_growing_score_window(
        signal_type_1h="green_bar_growing",
        signal_score=0.90,
    )
    assert allowed is True
    assert reason == ""

    allowed, reason = engine.check_green_bar_growing_score_window(
        signal_type_1h="green_bar_growing",
        signal_score=0.84,
    )
    assert allowed is False
    assert "green_bar_growing_score_window" in reason


def test_resolve_pocket_entry_requirements_supports_long_dual_support_strict_fields() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            min_entry_score=0.25,
            pocket_entry_overrides={
                "*|long_dual_support": {
                    "min_signal_score": 0.88,
                    "min_vwap_score": 0.16,
                    "min_entry_score": 0.50,
                    "allow_neutral_1h_confirmation": False,
                    "require_strict_1h_confirmation": True,
                    "disallow_trial_entry": True,
                    "require_cvd_ok": True,
                    "require_cvd_momentum_ok": True,
                }
            },
        )
    )

    requirements = engine.resolve_pocket_entry_requirements(
        signal_type_1h="red_bar_growing",
        vwap_state="long_dual_support",
        is_trial_entry=False,
    )

    assert requirements["signal_score_threshold"] == pytest.approx(0.88, rel=1e-6)
    assert requirements["min_vwap_score_for_entry"] == pytest.approx(0.16, rel=1e-6)
    assert requirements["min_entry_score"] == pytest.approx(0.50, rel=1e-6)
    assert requirements["allow_neutral_1h_confirmation"] is False
    assert requirements["require_strict_1h_confirmation"] is True
    assert requirements["disallow_trial_entry"] is True
    assert requirements["raw_override"]["require_cvd_ok"] is True
    assert requirements["raw_override"]["require_cvd_momentum_ok"] is True


def test_resolve_pocket_scoring_weights_prefers_long_dual_support_override() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            pocket_scoring_overrides={
                "*|long_dual_support": {
                    "weight_4h_direction": 0.15,
                    "weight_4h_enhancement": 0.10,
                    "weight_1h_direction": 0.35,
                    "weight_vwap": 0.25,
                    "weight_15m_entry": 0.10,
                    "weight_volume": 0.05,
                }
            }
        )
    )

    weights = engine.resolve_pocket_scoring_weights(
        signal_type_1h="red_bar_growing",
        vwap_state="long_dual_support",
    )

    assert weights["weight_4h_direction"] == pytest.approx(0.15, rel=1e-6)
    assert weights["weight_1h_direction"] == pytest.approx(0.35, rel=1e-6)
    assert weights["weight_vwap"] == pytest.approx(0.25, rel=1e-6)
    assert weights["weight_15m_entry"] == pytest.approx(0.10, rel=1e-6)
    assert weights["weight_volume"] == pytest.approx(0.05, rel=1e-6)


def test_analyze_uses_pocket_specific_scoring_weights_for_long_dual_support() -> None:
    baseline_engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.2,
            weight_4h_direction=0.4,
            weight_4h_enhancement=0.1,
            weight_vwap=0.2,
            weight_15m_entry=0.05,
            weight_volume=0.15,
            min_signal_score=0.1,
            min_entry_score=0.1,
            red_bar_growing_min_signal_score=0.1,
            flip_bullish_min_signal_score=0.1,
            min_vwap_score_for_entry=0.0,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="4h",
        )
    )
    override_engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.2,
            weight_4h_direction=0.4,
            weight_4h_enhancement=0.1,
            weight_vwap=0.2,
            weight_15m_entry=0.05,
            weight_volume=0.15,
            min_signal_score=0.1,
            min_entry_score=0.1,
            red_bar_growing_min_signal_score=0.1,
            flip_bullish_min_signal_score=0.1,
            min_vwap_score_for_entry=0.0,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="4h",
            pocket_scoring_overrides={
                "*|long_dual_support": {
                    "weight_4h_direction": 0.15,
                    "weight_4h_enhancement": 0.10,
                    "weight_1h_direction": 0.35,
                    "weight_vwap": 0.25,
                    "weight_15m_entry": 0.10,
                    "weight_volume": 0.05,
                }
            },
        )
    )

    kwargs = dict(
        macd_hist_15m=np.array([-0.10, -0.05, 0.02, 0.05]),
        macd_hist_1h=np.array([-0.20, -0.10, 0.05, 0.10]),
        macd_hist_4h=np.array([-0.30, -0.15, -0.05, 0.20]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=2.0,
        vwap=100.5,
        structural_vwap=100.0,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    baseline_signal = baseline_engine.analyze(**kwargs)
    override_signal = override_engine.analyze(**kwargs)

    assert baseline_signal.vwap_state == "long_dual_support"
    assert override_signal.vwap_state == "long_dual_support"
    assert override_signal.details["score_4h"] < baseline_signal.details["score_4h"]
    assert override_signal.details["score_1h"] > baseline_signal.details["score_1h"]
    assert override_signal.details["score_boll_rsi_resonance"] > baseline_signal.details["score_boll_rsi_resonance"]
    assert override_signal.details["score_vwap"] == pytest.approx(
        override_signal.details["score_boll_rsi_resonance"], rel=1e-6
    )
    assert override_signal.details["score_15m"] > baseline_signal.details["score_15m"]
    assert override_signal.details["score_volume"] < baseline_signal.details["score_volume"]


def test_stable_continuation_requires_flip_signal_and_reclaim_retest_state() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            enable_stable_bull_continuation=True,
            enable_stable_bear_continuation=True,
        )
    )

    long_eval = engine._evaluate_stable_continuation(
        primary_mode="4h",
        trade_direction="long",
        signal_type_1h="red_bar_growing",
        entry_type_15m="red_bar_growing",
        vwap_score=0.2,
        vwap_state="long_reclaim_confirmed",
        adx_1h=30.0,
        stable_trend_context={"bull_active": True, "positive_bars": 3},
        is_trial_entry=False,
    )
    assert long_eval["stable_continuation_active"] is False
    assert long_eval["stable_continuation_reason"] == "1h_signal_not_supported"

    short_eval = engine._evaluate_stable_continuation(
        primary_mode="4h",
        trade_direction="short",
        signal_type_1h="flip_bearish",
        entry_type_15m="green_bar_growing",
        vwap_score=0.2,
        vwap_state="short_dual_pressure",
        adx_1h=30.0,
        stable_trend_context={"bear_active": True, "negative_bars": 3},
        is_trial_entry=False,
    )
    assert short_eval["stable_continuation_active"] is False
    assert short_eval["stable_continuation_reason"] == "vwap_state_not_supported"


def test_trial_entry_still_blocks_red_bar_growing_long() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.2,
            weight_4h_direction=0.35,
            weight_4h_enhancement=0.0,
            weight_vwap=0.2,
            weight_15m_entry=0.15,
            weight_volume=0.15,
            min_signal_score=0.1,
            min_entry_score=0.1,
            min_vwap_score_for_entry=0.0,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_red_bar_growing_long_entries=True,
            primary_direction_timeframe="4h",
            require_1h_confirmation_when_4h_primary=True,
            light_1h_confirmation_when_4h_primary=False,
            allow_neutral_1h_confirmation=False,
            enable_4h_preflip_trial_entries=True,
            preflip_trial_min_signal_score=0.1,
            preflip_trial_min_vwap_score=0.0,
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([-0.20, -0.10, 0.05, 0.10]),
        macd_hist_1h=np.array([-0.30, -0.18, 0.08, 0.12]),
        macd_hist_4h=np.array([-0.90, -1.20, -1.40, -1.50, -1.40, -1.20, -0.90, -0.70, -0.50, -0.35]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=9,
        volume_ratio=2.0,
        vwap=100.5,
        structural_vwap=100.0,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    assert signal.direction == "neutral"
    assert "red_bar_growing_long_disabled" in str(signal.details.get("reason"))


def test_analyze_blocks_when_4h_primary_but_1h_confirmation_is_opposite() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.0,
            weight_4h_direction=0.5,
            weight_4h_enhancement=0.0,
            weight_vwap=0.0,
            weight_15m_entry=0.0,
            weight_volume=0.15,
            min_signal_score=0.1,
            min_entry_score=0.1,
            red_bar_growing_min_signal_score=0.1,
            flip_bearish_min_signal_score=0.1,
            min_vwap_score_for_entry=0.0,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="4h",
            require_1h_confirmation_when_4h_primary=True,
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([0.10, 0.05, -0.02, -0.05]),
        macd_hist_1h=np.array([0.20, 0.10, -0.05, -0.10]),
        macd_hist_4h=np.array([-0.30, -0.15, -0.05, 0.20]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=2.0,
        vwap=100.5,
        structural_vwap=100.0,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    assert signal.direction == "neutral"
    assert "1H方向反向" in str(signal.details.get("reason"))


def test_light_1h_confirmation_skips_flip_bullish_disable_filter() -> None:
    base_kwargs = dict(
        weight_1h_direction=0.0,
        weight_4h_direction=0.5,
        weight_4h_enhancement=0.0,
        weight_vwap=0.0,
        weight_15m_entry=0.15,
        weight_volume=0.15,
        min_signal_score=0.1,
        min_entry_score=0.1,
        red_bar_growing_min_signal_score=0.1,
        flip_bullish_min_signal_score=0.1,
        flip_bullish_min_vwap_score=0.0,
        min_vwap_score_for_entry=0.0,
        overheat_growing_penalty=0.0,
        enable_flip_bullish_strict_filter=False,
        disable_flip_bullish_entries=True,
        disable_green_bar_growing_entries=False,
        primary_direction_timeframe="4h",
        require_1h_confirmation_when_4h_primary=True,
    )

    strict_engine = MACDStrategyV2Engine(MACDStrategyV2Config(**base_kwargs))
    light_engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            **base_kwargs,
            light_1h_confirmation_when_4h_primary=True,
        )
    )

    analyze_kwargs = dict(
        macd_hist_15m=np.array([-0.10, -0.05, 0.02, 0.05]),
        macd_hist_1h=np.array([-0.20, -0.10, -0.05, 0.10]),
        macd_hist_4h=np.array([-0.30, -0.15, -0.05, 0.20]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=2.0,
        vwap=100.5,
        structural_vwap=100.0,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        close_1h_series=np.array([100.4, 100.3, 100.5, 101.0]),
        vwap_1h_series=np.array([100.5, 100.5, 100.5, 100.5]),
        structural_vwap_1h_series=np.array([100.0, 100.0, 100.0, 100.0]),
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    strict_signal = strict_engine.analyze(**analyze_kwargs)
    light_signal = light_engine.analyze(**analyze_kwargs)

    assert strict_signal.direction == "neutral"
    assert "flip_bullish_disabled" in str(strict_signal.details.get("reason"))
    assert light_signal.direction == "long"


def test_force_disable_flip_bullish_blocks_stable_continuation_long() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.0,
            weight_4h_direction=0.5,
            weight_4h_enhancement=0.0,
            weight_vwap=0.2,
            weight_15m_entry=0.15,
            weight_volume=0.15,
            min_signal_score=0.1,
            min_entry_score=0.1,
            red_bar_growing_min_signal_score=0.1,
            flip_bullish_min_signal_score=0.1,
            flip_bullish_min_vwap_score=0.0,
            min_vwap_score_for_entry=0.0,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=True,
            force_disable_flip_bullish_entries=True,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="4h",
            require_1h_confirmation_when_4h_primary=True,
            light_1h_confirmation_when_4h_primary=True,
            enable_stable_bull_continuation=True,
            stable_bull_continuation_min_signal_score=0.1,
            stable_bull_continuation_min_vwap_score=0.0,
            stable_bull_continuation_min_adx_1h=10.0,
            stable_bull_continuation_min_4h_bars=2,
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([-0.10, -0.05, 0.02, 0.05]),
        macd_hist_1h=np.array([-0.20, -0.10, -0.05, 0.10]),
        macd_hist_4h=np.array([-0.30, -0.15, -0.05, 0.20, 0.25, 0.30]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=5,
        volume_ratio=2.0,
        vwap=100.5,
        structural_vwap=100.0,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        close_1h_series=np.array([100.4, 100.3, 100.5, 101.0]),
        vwap_1h_series=np.array([100.5, 100.5, 100.5, 100.5]),
        structural_vwap_1h_series=np.array([100.0, 100.0, 100.0, 100.0]),
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    assert signal.details["stable_continuation_active"] is True
    assert signal.direction == "neutral"
    assert "flip_bullish_disabled" in str(signal.details.get("reason"))


def test_preflip_trial_entry_allows_4h_green_shrinking_long() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.0,
            weight_4h_direction=0.5,
            weight_4h_enhancement=0.0,
            weight_vwap=0.20,
            weight_15m_entry=0.15,
            weight_volume=0.15,
            min_signal_score=0.85,
            min_entry_score=0.1,
            min_vwap_score_for_entry=0.12,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="4h",
            require_1h_confirmation_when_4h_primary=True,
            light_1h_confirmation_when_4h_primary=True,
            enable_4h_preflip_trial_entries=True,
            preflip_trial_min_shrink_pct_long=0.75,
            preflip_trial_min_signal_score=0.78,
            preflip_trial_min_vwap_score=0.06,
            preflip_trial_entry_scale=0.35,
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([-0.20, -0.10, 0.05, 0.10]),
        macd_hist_1h=np.array([-0.30, -0.18, -0.08, 0.12]),
        macd_hist_4h=np.array([-0.90, -1.20, -1.40, -1.50, -1.40, -1.20, -0.90, -0.70, -0.50, -0.35]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=9,
        volume_ratio=2.0,
        vwap=100.5,
        structural_vwap=100.0,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        close_1h_series=np.array([100.2, 100.3, 100.5, 101.0]),
        vwap_1h_series=np.array([100.5, 100.5, 100.5, 100.5]),
        structural_vwap_1h_series=np.array([100.0, 100.0, 100.0, 100.0]),
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    assert signal.direction == "long"
    assert signal.is_trial_entry is True
    assert signal.entry_scale == pytest.approx(0.35, rel=1e-6)


def test_q4_rsi_lead_preflip_long_can_bypass_negative_4h_hist_guard() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            enable_q4_rsi_lead_preflip_long=True,
            q4_rsi_lead_preflip_bonus_score=0.18,
            q4_rsi_lead_preflip_entry_scale=0.35,
            q4_rsi_lead_preflip_min_4h_shrink_pct=0.75,
            q4_rsi_lead_preflip_rsi_1h_min=50.0,
            q4_rsi_lead_preflip_rsi_4h_min=50.0,
            q4_rsi_lead_preflip_rsi_4h_near_buffer=2.0,
        )
    )

    result = engine._evaluate_q4_rsi_lead_preflip_long(
        market_quadrant="IV",
        trade_direction="long",
        signal_type_1h="red_bar_growing",
        symbol="ETHUSDT",
        macd_line_4h=-0.20,
        macd_4h_shrink_pct=0.82,
        close_price=101.0,
        bb_middle_1h=100.0,
        rsi_1h=61.0,
        rsi_4h=50.5,
    )

    assert result["q4_rsi_lead_preflip_passed"] is True
    assert result["q4_rsi_lead_preflip_reason"] == "passed"
    assert result["q4_rsi_lead_preflip_symbol_gate_pass"] is True
    assert result["q4_rsi_lead_preflip_bonus_score"] == pytest.approx(0.18, rel=1e-6)
    assert result["q4_rsi_lead_preflip_entry_scale"] == pytest.approx(0.35, rel=1e-6)
    assert result["q4_rsi_lead_preflip_raw_score"] > 0.0


def test_q4_rsi_lead_preflip_long_still_requires_reclaim_above_1h_midline() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(enable_q4_rsi_lead_preflip_long=True)
    )

    result = engine._evaluate_q4_rsi_lead_preflip_long(
        market_quadrant="IV",
        trade_direction="long",
        signal_type_1h="red_bar_growing",
        symbol="ETHUSDT",
        macd_line_4h=-0.20,
        macd_4h_shrink_pct=0.82,
        close_price=99.0,
        bb_middle_1h=100.0,
        rsi_1h=61.0,
        rsi_4h=50.5,
    )

    assert result["q4_rsi_lead_preflip_passed"] is False
    assert result["q4_rsi_lead_preflip_reason"] == "price_below_1h_midline"


def test_q4_rsi_lead_preflip_long_accepts_positive_4h_hist_without_preset_long_direction() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            enable_q4_rsi_lead_preflip_long=True,
            q4_rsi_lead_preflip_bonus_score=0.18,
            q4_rsi_lead_preflip_entry_scale=0.35,
            q4_rsi_lead_preflip_min_4h_shrink_pct=0.75,
            q4_rsi_lead_preflip_rsi_1h_min=50.0,
            q4_rsi_lead_preflip_rsi_4h_min=50.0,
            q4_rsi_lead_preflip_rsi_4h_near_buffer=15.0,
        )
    )

    result = engine._evaluate_q4_rsi_lead_preflip_long(
        market_quadrant="IV",
        trade_direction=None,
        signal_type_1h="red_bar_growing",
        symbol="ETHUSDT",
        macd_line_4h=-10.0,
        macd_4h_shrink_pct=0.10,
        macd_hist_4h=0.11,
        close_price=1977.31,
        bb_middle_1h=1952.0445,
        rsi_1h=62.39569523512433,
        rsi_4h=36.08136386016742,
    )

    assert result["q4_rsi_lead_preflip_passed"] is True
    assert result["q4_rsi_lead_preflip_reason"] == "passed"
    assert result["q4_rsi_lead_preflip_hist_4h_positive"] is True


def test_q4_rsi_lead_preflip_long_blocks_non_whitelist_symbol() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            enable_q4_rsi_lead_preflip_long=True,
            q4_rsi_lead_preflip_allowed_symbols=["ETHUSDT"],
        )
    )

    result = engine._evaluate_q4_rsi_lead_preflip_long(
        market_quadrant="IV",
        trade_direction="long",
        signal_type_1h="red_bar_growing",
        symbol="SOLUSDT",
        macd_line_4h=-0.20,
        macd_4h_shrink_pct=0.82,
        close_price=101.0,
        bb_middle_1h=100.0,
        rsi_1h=61.0,
        rsi_4h=50.5,
    )

    assert result["q4_rsi_lead_preflip_passed"] is False
    assert result["q4_rsi_lead_preflip_reason"] == "symbol_not_allowed"
    assert result["q4_rsi_lead_preflip_symbol"] == "SOLUSDT"
    assert result["q4_rsi_lead_preflip_symbol_gate_pass"] is False


def test_q4_rsi_lead_preflip_long_allows_major_large_cap_symbol_with_dynamic_admission() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            enable_q4_rsi_lead_preflip_long=True,
            q4_rsi_lead_preflip_strict_eth_only=False,
            q4_rsi_lead_preflip_allowed_symbols=[],
            q4_rsi_lead_preflip_allowed_categories=["major_large_cap"],
            q4_rsi_lead_preflip_min_atr_pct_1h=0.005,
            q4_rsi_lead_preflip_max_atr_pct_1h=0.050,
            symbol_risk_watchlist_symbols=["LINKUSDT"],
        )
    )

    result = engine._evaluate_q4_rsi_lead_preflip_long(
        market_quadrant="IV",
        trade_direction="long",
        signal_type_1h="red_bar_growing",
        symbol="SOLUSDT",
        macd_line_4h=-0.20,
        macd_4h_shrink_pct=0.82,
        close_price=101.0,
        bb_middle_1h=100.0,
        rsi_1h=61.0,
        rsi_4h=50.5,
        atr_pct_1h=0.020,
    )

    assert result["q4_rsi_lead_preflip_passed"] is True
    assert result["q4_rsi_lead_preflip_reason"] == "passed"
    assert result["q4_rsi_lead_preflip_symbol_gate_pass"] is True


def test_q4_rsi_lead_preflip_long_dynamic_admission_blocks_watchlist_symbol() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            enable_q4_rsi_lead_preflip_long=True,
            q4_rsi_lead_preflip_strict_eth_only=False,
            q4_rsi_lead_preflip_allowed_symbols=[],
            q4_rsi_lead_preflip_allowed_categories=["major_large_cap"],
            q4_rsi_lead_preflip_min_atr_pct_1h=0.005,
            q4_rsi_lead_preflip_max_atr_pct_1h=0.050,
            symbol_risk_watchlist_symbols=["LINKUSDT"],
        )
    )

    result = engine._evaluate_q4_rsi_lead_preflip_long(
        market_quadrant="IV",
        trade_direction="long",
        signal_type_1h="red_bar_growing",
        symbol="LINKUSDT",
        macd_line_4h=-0.20,
        macd_4h_shrink_pct=0.82,
        close_price=101.0,
        bb_middle_1h=100.0,
        rsi_1h=61.0,
        rsi_4h=50.5,
        atr_pct_1h=0.020,
    )

    assert result["q4_rsi_lead_preflip_passed"] is False
    assert result["q4_rsi_lead_preflip_reason"] == "symbol_not_allowed"
    assert "categories" in result["q4_rsi_lead_preflip_symbol_gate_reason"]


def test_q4_rsi_lead_preflip_long_dynamic_admission_blocks_atr_out_of_range() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            enable_q4_rsi_lead_preflip_long=True,
            q4_rsi_lead_preflip_strict_eth_only=False,
            q4_rsi_lead_preflip_allowed_symbols=[],
            q4_rsi_lead_preflip_allowed_categories=["major_large_cap"],
            q4_rsi_lead_preflip_min_atr_pct_1h=0.010,
            q4_rsi_lead_preflip_max_atr_pct_1h=0.030,
        )
    )

    result = engine._evaluate_q4_rsi_lead_preflip_long(
        market_quadrant="IV",
        trade_direction="long",
        signal_type_1h="red_bar_growing",
        symbol="BTCUSDT",
        macd_line_4h=-0.20,
        macd_4h_shrink_pct=0.82,
        close_price=101.0,
        bb_middle_1h=100.0,
        rsi_1h=61.0,
        rsi_4h=50.5,
        atr_pct_1h=0.005,
    )

    assert result["q4_rsi_lead_preflip_passed"] is False
    assert result["q4_rsi_lead_preflip_reason"] == "symbol_not_allowed"
    assert "atr_pct_1h" in result["q4_rsi_lead_preflip_symbol_gate_reason"]


def test_long_whitelist_config_blocks_non_whitelist_long_combos() -> None:
    cfg = MACDStrategyV2Config(
        long_entry_mode="whitelist_only",
        long_whitelist_signal_types=["green_bar_growing"],
        long_whitelist_vwap_states=["long_dual_support"],
    )

    assert cfg.is_long_entry_whitelisted("green_bar_growing", "short_retest_reject") is True
    assert cfg.is_long_entry_whitelisted("flip_bullish", "long_dual_support") is True
    assert cfg.is_long_entry_whitelisted("flip_bullish", "long_reclaim_confirmed") is False


def test_long_whitelist_pockets_require_exact_match_when_configured() -> None:
    cfg = MACDStrategyV2Config(
        long_entry_mode="whitelist_only",
        long_whitelist_signal_types=["green_bar_growing"],
        long_whitelist_vwap_states=["long_dual_support"],
        long_whitelist_pockets=["flip_bullish|long_reclaim_confirmed"],
    )

    assert cfg.is_long_entry_whitelisted("flip_bullish", "long_reclaim_confirmed") is True
    assert cfg.is_long_entry_whitelisted("green_bar_growing", "long_dual_support") is False
    assert cfg.is_long_entry_whitelisted("flip_bullish", "long_dual_support") is False


def test_build_macd_v2_config_from_runtime_loads_long_whitelist_controls() -> None:
    runtime_cfg = {
        "fund_flow": {
            "macd_mtf_strategy_v2": {
                "entry_filters": {
                    "long_entry_mode": "whitelist_only",
                    "long_whitelist_signal_types": ["green_bar_growing"],
                    "long_whitelist_vwap_states": ["long_dual_support"],
                    "long_whitelist_pockets": ["flip_bullish|long_reclaim_confirmed"],
                }
            }
        }
    }

    config = build_macd_v2_config_from_runtime(runtime_cfg)

    assert config.long_entry_mode == "whitelist_only"
    assert config.long_whitelist_signal_types == ["green_bar_growing"]
    assert config.long_whitelist_vwap_states == ["long_dual_support"]
    assert config.long_whitelist_pockets == ["flip_bullish|long_reclaim_confirmed"]


def test_build_macd_v2_config_from_runtime_loads_q4_symbol_whitelist() -> None:
    runtime_cfg = {
        "fund_flow": {
            "macd_mtf_strategy_v2": {
                "entry_filters": {
                    "enable_q4_rsi_lead_preflip_long": True,
                    "q4_rsi_lead_preflip_allowed_symbols": ["ETHUSDT", "BTCUSDT"],
                }
            }
        }
    }

    config = build_macd_v2_config_from_runtime(runtime_cfg)

    assert config.enable_q4_rsi_lead_preflip_long is True
    assert config.q4_rsi_lead_preflip_allowed_symbols == ["ETHUSDT", "BTCUSDT"]


def test_build_macd_v2_config_from_runtime_loads_dynamic_q4_admission_and_resonance_weight() -> None:
    runtime_cfg = {
        "fund_flow": {
            "macd_mtf_strategy_v2": {
                "scoring_weights": {
                    "weight_boll_rsi_resonance": 0.18,
                },
                "entry_filters": {
                    "enable_q4_rsi_lead_preflip_long": True,
                    "q4_rsi_lead_preflip_strict_eth_only": False,
                    "q4_rsi_lead_preflip_allowed_categories": ["major_large_cap"],
                    "q4_rsi_lead_preflip_min_atr_pct_1h": 0.01,
                    "q4_rsi_lead_preflip_max_atr_pct_1h": 0.03,
                },
            }
        }
    }

    config = build_macd_v2_config_from_runtime(runtime_cfg)

    assert config.weight_boll_rsi_resonance == pytest.approx(0.18, rel=1e-6)
    assert config.q4_rsi_lead_preflip_strict_eth_only is False
    assert config.q4_rsi_lead_preflip_allowed_categories == ["major_large_cap"]
    assert config.q4_rsi_lead_preflip_min_atr_pct_1h == pytest.approx(0.01, rel=1e-6)
    assert config.q4_rsi_lead_preflip_max_atr_pct_1h == pytest.approx(0.03, rel=1e-6)


def test_flip_bullish_cvd_context_filter_blocks_preflip_trial_entry() -> None:
    base_kwargs = dict(
        weight_1h_direction=0.0,
        weight_4h_direction=0.5,
        weight_4h_enhancement=0.0,
        weight_vwap=0.20,
        weight_15m_entry=0.15,
        weight_volume=0.15,
        min_signal_score=0.85,
        min_entry_score=0.1,
        min_vwap_score_for_entry=0.12,
        overheat_growing_penalty=0.0,
        enable_flip_bullish_strict_filter=False,
        disable_flip_bullish_entries=False,
        disable_green_bar_growing_entries=False,
        primary_direction_timeframe="4h",
        require_1h_confirmation_when_4h_primary=True,
        light_1h_confirmation_when_4h_primary=True,
        enable_4h_preflip_trial_entries=True,
        preflip_trial_min_shrink_pct_long=0.75,
        preflip_trial_min_signal_score=0.78,
        preflip_trial_min_vwap_score=0.06,
        preflip_trial_entry_scale=0.35,
    )

    plain_engine = MACDStrategyV2Engine(MACDStrategyV2Config(**base_kwargs))
    filtered_engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            **base_kwargs,
            enable_flip_bullish_cvd_context_filter=True,
            flip_bullish_max_cvd_upper_wick_ratio=0.20,
            flip_bullish_min_cvd_1h_delta_ratio=0.03,
        )
    )

    analyze_kwargs = dict(
        macd_hist_15m=np.array([-0.20, -0.10, 0.05, 0.10]),
        macd_hist_1h=np.array([-0.30, -0.18, -0.08, 0.12]),
        macd_hist_4h=np.array([-0.90, -1.20, -1.40, -1.50, -1.40, -1.20, -0.90, -0.70, -0.50, -0.35]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=9,
        volume_ratio=2.0,
        vwap=100.5,
        structural_vwap=100.0,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        close_1h_series=np.array([100.2, 100.3, 100.5, 101.0]),
        vwap_1h_series=np.array([100.5, 100.5, 100.5, 100.5]),
        structural_vwap_1h_series=np.array([100.0, 100.0, 100.0, 100.0]),
        adx_1h=20.0,
        adx_4h=22.0,
        cvd_upper_wick_ratio=0.25,
        cvd_1h_delta_ratio=0.01,
        atr_1h=1.0,
    )

    plain_signal = plain_engine.analyze(**analyze_kwargs)
    filtered_signal = filtered_engine.analyze(**analyze_kwargs)

    assert plain_signal.direction == "long"
    assert plain_signal.is_trial_entry is True
    assert filtered_signal.direction == "neutral"
    assert "flip_bullish_cvd_context_filter" in str(filtered_signal.details.get("reason"))


def test_neutral_signal_carries_4h_shrink_exit_metadata() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.0,
            weight_4h_direction=0.5,
            weight_4h_enhancement=0.0,
            weight_vwap=0.20,
            weight_15m_entry=0.15,
            weight_volume=0.15,
            min_signal_score=0.85,
            min_entry_score=0.1,
            min_vwap_score_for_entry=0.12,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="4h",
            require_1h_confirmation_when_4h_primary=True,
            light_1h_confirmation_when_4h_primary=True,
            enable_4h_preflip_trial_entries=True,
            enable_4h_shrink_exit=True,
            exit_4h_shrink_bars=2,
            exit_4h_min_shrink_pct=0.20,
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([0.20, 0.16, 0.12, 0.08]),
        macd_hist_1h=np.array([0.30, 0.24, 0.18, 0.12]),
        macd_hist_4h=np.array([0.90, 1.20, 1.40, 1.50, 1.40, 1.20, 1.00, 0.82, 0.70, 0.58]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=9,
        volume_ratio=1.1,
        vwap=100.0,
        structural_vwap=100.5,
        close_price=99.8,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=101.0,
        bb_upper_4h=111.0,
        bb_lower_4h=91.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=99.8,
        adx_1h=18.0,
        adx_4h=20.0,
        atr_1h=1.0,
    )

    assert signal.direction == "neutral"
    assert signal.details["shrink_exit_direction"] == "long"
    assert signal.details["shrink_exit_ready"] is True


def test_neutral_signal_populates_structured_reject_metadata_defaults() -> None:
    engine = MACDStrategyV2Engine(MACDStrategyV2Config())

    signal = engine._neutral_signal(
        reason="pocket_min_entry_score_block",
        score=0.83,
        signal_type_1h="red_bar_growing",
        entry_type_15m="pullback",
        entry_score_15m=0.22,
        vwap_score=0.11,
        vwap_state="long_dual_support",
        details={
            "stage": "pocket_entry_requirements",
            "stage_path": ["score_calc", "pocket_entry_requirements"],
            "signal_score_threshold": 0.84,
            "min_vwap_score_for_entry": 0.12,
            "pocket_min_entry_score": 0.35,
            "pocket_entry_override_label": "ld_support_e2_cvd_vwap_score",
        },
    )

    assert signal.direction == "neutral"
    assert signal.details["reject_reason_code"] == "pocket_min_entry_score_block"
    assert signal.details["reject_stage"] == "pocket_entry_requirements"
    assert signal.details["pocket_entry_override_label"] == "ld_support_e2_cvd_vwap_score"
    assert signal.details["signal_score_threshold_used"] == pytest.approx(0.84, rel=1e-6)
    assert signal.details["min_vwap_score_used"] == pytest.approx(0.12, rel=1e-6)
    assert signal.details["min_entry_score_used"] == pytest.approx(0.35, rel=1e-6)
    assert signal.details["direction_lock_applied"] is False
    assert signal.details["entry_hard_filter_blocked"] is False
    assert signal.details["entry_hard_filters"] == []
    assert signal.details["regime_fallback_allowed"] is False
    assert signal.details["regime_fallback_score"] == pytest.approx(0.0, rel=1e-6)


def test_soft_15m_confirmation_allows_4h_primary_entry() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.0,
            weight_4h_direction=0.55,
            weight_4h_enhancement=0.0,
            weight_vwap=0.20,
            weight_15m_entry=0.05,
            weight_volume=0.20,
            min_signal_score=0.85,
            min_entry_score=0.1,
            min_vwap_score_for_entry=0.12,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="4h",
            require_1h_confirmation_when_4h_primary=True,
            allow_neutral_1h_confirmation=True,
            light_1h_confirmation_when_4h_primary=True,
            enable_soft_15m_confirmation_when_4h_primary=True,
            soft_15m_entry_score=0.28,
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([-0.00040, -0.00025, -0.00018, -0.00010]),
        macd_hist_1h=np.array([-0.20, -0.10, 0.05, 0.10]),
        macd_hist_4h=np.array([-0.30, -0.15, -0.05, 0.20]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=2.0,
        vwap=100.0,
        structural_vwap=99.6,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    assert signal.direction == "long"
    assert signal.signal_score >= 0.85
    assert signal.details["entry_score_15m"] == pytest.approx(0.28, rel=1e-6)
    assert signal.details["vwap_score"] >= 0.12
    assert str(signal.details["entry_type_15m"]).startswith("soft_long_")


def test_15m_confirmation_no_longer_blocks_entry_when_gate_disabled() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.0,
            weight_4h_direction=0.55,
            weight_4h_enhancement=0.0,
            weight_vwap=0.20,
            weight_15m_entry=0.05,
            weight_volume=0.20,
            min_signal_score=0.40,
            min_entry_score=0.1,
            min_vwap_score_for_entry=0.12,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="4h",
            require_1h_confirmation_when_4h_primary=True,
            allow_neutral_1h_confirmation=True,
            light_1h_confirmation_when_4h_primary=True,
            enable_soft_15m_confirmation_when_4h_primary=True,
            require_15m_confirmation_gate=False,
            soft_15m_entry_score=0.28,
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([-0.0040, -0.0032, -0.0028, -0.0024]),
        macd_hist_1h=np.array([-0.20, -0.10, 0.05, 0.10]),
        macd_hist_4h=np.array([-0.30, -0.15, -0.05, 0.20]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=2.0,
        vwap=100.0,
        structural_vwap=99.6,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    assert signal.direction == "long"
    assert signal.details["entry_type_15m"] == ""
    assert signal.details["entry_score_15m"] == pytest.approx(0.0, rel=1e-6)


def test_neutral_1h_confirmation_receives_light_score_credit_when_allowed() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            weight_1h_direction=0.4,
            weight_4h_direction=0.2,
            weight_4h_enhancement=0.0,
            weight_vwap=0.2,
            weight_15m_entry=0.05,
            weight_volume=0.15,
            min_signal_score=0.75,
            min_entry_score=0.1,
            min_vwap_score_for_entry=0.12,
            overheat_growing_penalty=0.0,
            enable_flip_bullish_strict_filter=False,
            disable_flip_bullish_entries=False,
            disable_green_bar_growing_entries=False,
            primary_direction_timeframe="4h",
            require_1h_confirmation_when_4h_primary=True,
            allow_neutral_1h_confirmation=True,
            light_1h_confirmation_when_4h_primary=True,
            enable_soft_15m_confirmation_when_4h_primary=True,
            soft_15m_entry_score=0.28,
        )
    )

    signal = engine.analyze(
        macd_hist_15m=np.array([-0.00040, -0.00025, -0.00018, -0.00010]),
        macd_hist_1h=np.array([0.0, 0.0, 0.0, 0.0]),
        macd_hist_4h=np.array([-0.30, -0.15, -0.05, 0.20]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=2.0,
        vwap=100.0,
        structural_vwap=99.6,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.0,
        bb_upper_4h=109.0,
        bb_lower_4h=89.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        adx_1h=20.0,
        adx_4h=22.0,
        atr_1h=1.0,
    )

    assert signal.direction == "long"
    assert signal.details["score_1h"] == pytest.approx(0.30, rel=1e-6)
    assert signal.details["score_1h_source"] == "neutral_allowed_light_credit"
    assert signal.signal_score >= 0.75


def test_green_bar_growing_short_adx_range_filter_blocks_full_size() -> None:
    base_kwargs = dict(
        weight_1h_direction=0.5,
        weight_4h_direction=0.0,
        weight_4h_enhancement=0.0,
        weight_vwap=0.0,
        weight_15m_entry=0.15,
        weight_volume=0.15,
        min_signal_score=0.1,
        flip_bullish_min_signal_score=0.1,
        min_entry_score=0.1,
        flip_bullish_min_vwap_score=0.0,
        min_vwap_score_for_entry=0.0,
        overheat_growing_penalty=0.0,
        enable_flip_bullish_strict_filter=False,
        disable_flip_bullish_entries=False,
        disable_green_bar_growing_entries=False,
        primary_direction_timeframe="1h",
    )

    plain_engine = MACDStrategyV2Engine(MACDStrategyV2Config(**base_kwargs))
    filtered_engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            **base_kwargs,
            enable_green_bar_growing_short_adx_1h_range_filter=True,
            green_bar_growing_short_min_adx_1h=25.0,
            green_bar_growing_short_max_adx_1h=30.0,
        )
    )

    analyze_kwargs = dict(
        macd_hist_15m=np.array([0.10, 0.05, -0.03, -0.08]),
        macd_hist_1h=np.array([-0.01, -0.02, -0.05, -0.10]),
        macd_hist_4h=np.array([-0.02, -0.04, -0.08, -0.12]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=2.0,
        vwap=99.5,
        structural_vwap=100.0,
        close_price=99.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=100.0,
        bb_upper_4h=110.0,
        bb_lower_4h=90.0,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=99.2,
        adx_1h=27.0,
        adx_4h=24.0,
        atr_1h=1.0,
    )

    plain_signal = plain_engine.analyze(**analyze_kwargs)
    filtered_signal = filtered_engine.analyze(**analyze_kwargs)

    assert plain_signal.direction == "short"
    assert filtered_signal.direction == "neutral"
    assert "green_bar_growing_short_adx_1h_range_filter" in str(filtered_signal.details.get("reason"))


def test_flip_bullish_cvd_context_filter_blocks_full_size() -> None:
    base_kwargs = dict(
        weight_1h_direction=0.5,
        weight_4h_direction=0.0,
        weight_4h_enhancement=0.0,
        weight_vwap=0.0,
        weight_15m_entry=0.15,
        weight_volume=0.15,
        min_signal_score=0.1,
        flip_bullish_min_signal_score=0.1,
        min_entry_score=0.1,
        flip_bullish_min_vwap_score=0.0,
        min_vwap_score_for_entry=0.0,
        overheat_growing_penalty=0.0,
        enable_flip_bullish_strict_filter=False,
        disable_flip_bullish_entries=False,
        disable_green_bar_growing_entries=False,
        primary_direction_timeframe="1h",
    )

    plain_engine = MACDStrategyV2Engine(MACDStrategyV2Config(**base_kwargs))
    filtered_engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            **base_kwargs,
            enable_flip_bullish_cvd_context_filter=True,
            flip_bullish_max_cvd_upper_wick_ratio=0.20,
            flip_bullish_min_cvd_1h_delta_ratio=0.03,
        )
    )

    analyze_kwargs = dict(
        macd_hist_15m=np.array([-0.10, -0.05, 0.03, 0.08]),
        macd_hist_1h=np.array([-0.08, -0.04, -0.02, 0.06]),
        macd_hist_4h=np.array([-0.10, -0.05, 0.04, 0.09]),
        idx_15m=3,
        idx_1h=3,
        idx_4h=3,
        volume_ratio=2.0,
        vwap=100.5,
        structural_vwap=100.0,
        close_price=101.0,
        bb_middle_1h=100.0,
        bb_upper_1h=110.0,
        bb_lower_1h=90.0,
        bb_middle_4h=99.5,
        bb_upper_4h=109.5,
        bb_lower_4h=89.5,
        bb_middle_15m=100.0,
        bb_upper_15m=103.0,
        bb_lower_15m=97.0,
        close_15m=100.8,
        close_1h_series=np.array([100.3, 100.4, 100.5, 101.0]),
        vwap_1h_series=np.array([100.5, 100.5, 100.5, 100.5]),
        structural_vwap_1h_series=np.array([100.0, 100.0, 100.0, 100.0]),
        adx_1h=28.0,
        adx_4h=24.0,
        cvd_upper_wick_ratio=0.25,
        cvd_1h_delta_ratio=0.01,
        atr_1h=1.0,
    )

    plain_signal = plain_engine.analyze(**analyze_kwargs)
    filtered_signal = filtered_engine.analyze(**analyze_kwargs)

    assert plain_signal.direction == "long"
    assert filtered_signal.direction == "neutral"
    assert "flip_bullish_cvd_context_filter" in str(filtered_signal.details.get("reason"))


def test_session_risk_position_scale_matches_target_states() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            session_risk_control_enabled=True,
            session_risk_high_risk_sessions=[
                {"utc_start": "14:30", "utc_end": "16:00", "position_scale": 0.65}
            ],
            session_risk_apply_to_states=["short_dual_pressure", "flip_bullish"],
        )
    )

    in_window = datetime(2026, 3, 21, 15, 0, tzinfo=timezone.utc)
    out_window = datetime(2026, 3, 21, 17, 0, tzinfo=timezone.utc)

    assert engine.resolve_session_position_scale(in_window, signal_type_1h="flip_bullish") == pytest.approx(0.65, rel=1e-6)
    assert engine.resolve_session_position_scale(in_window, signal_type_1h="green_bar_growing", vwap_state="short_dual_pressure") == pytest.approx(0.65, rel=1e-6)
    assert engine.resolve_session_position_scale(in_window, signal_type_1h="green_bar_growing", vwap_state="long_bias") == pytest.approx(1.0, rel=1e-6)
    assert engine.resolve_session_position_scale(out_window, signal_type_1h="flip_bullish") == pytest.approx(1.0, rel=1e-6)


def test_calculate_position_portion_applies_session_scale_after_trial_scale() -> None:
    engine = MACDStrategyV2Engine(MACDStrategyV2Config())

    portion = engine.calculate_position_portion(
        score=0.90,
        base_default_portion=0.60,
        base_max_symbol_position_portion=0.60,
        is_trial_entry=True,
        entry_scale=0.35,
        session_scale=0.65,
    )

    assert portion == pytest.approx(0.60 * 0.35 * 0.65, rel=1e-6)


def test_watchlist_symbol_risk_caps_leverage_and_session_scaled_portion() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            symbol_risk_watchlist_symbols=["LINKUSDT"],
            symbol_risk_watchlist_max_position_portion=0.40,
            symbol_risk_watchlist_max_leverage=2,
            symbol_risk_watchlist_apply_session_scale_double=True,
            symbol_risk_watchlist_session_scale_multiplier=0.80,
            dual_pressure_target_portion_bonus=0.08,
            dual_pressure_max_symbol_position_portion=0.68,
        )
    )

    leverage = engine.calculate_leverage(
        score=0.90,
        ema_multiplier=1.0,
        signal_type_1h="green_bar_growing",
        symbol="LINKUSDT",
    )
    portion = engine.calculate_position_portion(
        score=0.90,
        base_default_portion=0.60,
        base_max_symbol_position_portion=0.60,
        symbol="LINKUSDT",
        vwap_state="short_dual_pressure",
        session_scale=0.65,
    )

    assert leverage == 2
    assert portion == pytest.approx(0.40 * (0.65 * 0.80), rel=1e-6)


def test_calculate_leverage_uses_configured_score_tiers_before_caps() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            leverage_score_tiers=[
                {"score_min": 0.87, "leverage": 4},
                {"score_min": 0.84, "leverage": 3},
                {"score_min": 0.80, "leverage": 2},
            ]
        )
    )

    assert engine.calculate_leverage(score=0.90, ema_multiplier=1.0, signal_type_1h="flip_bullish") == 4
    assert engine.calculate_leverage(score=0.85, ema_multiplier=1.0, signal_type_1h="flip_bullish") == 3
    assert engine.calculate_leverage(score=0.81, ema_multiplier=1.0, signal_type_1h="flip_bullish") == 2
    assert engine.calculate_leverage(score=0.79, ema_multiplier=1.0, signal_type_1h="flip_bullish") == 0


def test_build_macd_v2_config_from_runtime_loads_leverage_score_tiers() -> None:
    config = build_macd_v2_config_from_runtime(
        {
            "fund_flow": {
                "macd_mtf_strategy_v2": {
                    "leverage_config": {
                        "score_tiers": [
                            {"score_min": 0.87, "leverage": 4},
                            {"score_min": 0.84, "leverage": 3},
                            {"score_min": 0.80, "leverage": 2},
                        ]
                    }
                }
            }
        }
    )

    assert config.leverage_score_tiers == [
        {"score_min": 0.87, "leverage": 4},
        {"score_min": 0.84, "leverage": 3},
        {"score_min": 0.80, "leverage": 2},
    ]


def test_calculate_position_portion_uses_configured_score_tiers() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            position_score_tiers=[
                {"score_min": 0.85, "target_portion": 0.30},
                {"score_min": 0.75, "target_portion": 0.25},
                {"score_min": 0.68, "target_portion": 0.20},
            ]
        )
    )

    assert engine.calculate_position_portion(
        score=0.90,
        base_default_portion=0.20,
        base_max_symbol_position_portion=0.30,
    ) == pytest.approx(0.30, rel=1e-6)
    assert engine.calculate_position_portion(
        score=0.80,
        base_default_portion=0.20,
        base_max_symbol_position_portion=0.30,
    ) == pytest.approx(0.25, rel=1e-6)
    assert engine.calculate_position_portion(
        score=0.70,
        base_default_portion=0.20,
        base_max_symbol_position_portion=0.30,
    ) == pytest.approx(0.20, rel=1e-6)


def test_build_macd_v2_config_from_runtime_loads_position_score_tiers() -> None:
    config = build_macd_v2_config_from_runtime(
        {
            "fund_flow": {
                "macd_mtf_strategy_v2": {
                    "position_size_config": {
                        "score_tiers": [
                            {"score_min": 0.85, "target_portion": 0.30},
                            {"score_min": 0.75, "target_portion": 0.25},
                            {"score_min": 0.68, "target_portion": 0.20},
                        ]
                    }
                }
            }
        }
    )

    assert config.position_score_tiers == [
        {"score_min": 0.85, "target_portion": 0.30},
        {"score_min": 0.75, "target_portion": 0.25},
        {"score_min": 0.68, "target_portion": 0.20},
    ]


def test_build_macd_v2_config_from_runtime_loads_pocket_management_overrides() -> None:
    config = build_macd_v2_config_from_runtime(
        {
            "fund_flow": {
                "macd_mtf_strategy_v2": {
                    "pocket_management_overrides": {
                        "green_bar_growing|short_retest_reject": {
                            "leverage_floor": 4,
                            "tp_pct": 0.05,
                            "take_profit_pct_levels": [0.01, 0.016, 0.028],
                            "take_profit_reduce_pct_levels": [0.2, 0.25, 0.15],
                        }
                    }
                }
            }
        }
    )

    assert config.pocket_management_overrides["green_bar_growing|short_retest_reject"]["leverage_floor"] == 4


def test_non_watchlist_symbol_keeps_original_dual_pressure_cap() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            symbol_risk_watchlist_symbols=["LINKUSDT"],
            symbol_risk_watchlist_max_position_portion=0.40,
            dual_pressure_target_portion_bonus=0.08,
            dual_pressure_max_symbol_position_portion=0.68,
        )
    )

    portion = engine.calculate_position_portion(
        score=0.90,
        base_default_portion=0.60,
        base_max_symbol_position_portion=0.60,
        symbol="SOLUSDT",
        vwap_state="short_dual_pressure",
        session_scale=1.0,
    )

    assert portion == pytest.approx(0.68, rel=1e-6)


def test_vwap_score_position_tiers_apply_only_to_target_states() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            vwap_score_tier_apply_to_states=["short_dual_pressure", "flip_bullish"],
            vwap_score_position_tiers=[
                {"min": 0.12, "max": 0.20, "position_mult": 0.80},
                {"min": 0.20, "max": 0.30, "position_mult": 1.00},
                {"min": 0.30, "max": 1.00, "position_mult": 1.15},
            ],
        )
    )

    weak_dual_pressure = engine.calculate_position_portion(
        score=0.90,
        base_default_portion=0.60,
        base_max_symbol_position_portion=0.60,
        signal_type_1h="green_bar_growing",
        vwap_score=0.15,
        vwap_state="short_dual_pressure",
    )
    strong_flip = engine.calculate_position_portion(
        score=0.85,
        base_default_portion=0.60,
        base_max_symbol_position_portion=0.60,
        signal_type_1h="flip_bullish",
        vwap_score=0.35,
        vwap_state="long_reclaim_confirmed",
    )
    untouched = engine.calculate_position_portion(
        score=0.90,
        base_default_portion=0.60,
        base_max_symbol_position_portion=0.60,
        signal_type_1h="red_bar_growing",
        vwap_score=0.35,
        vwap_state="long_dual_support",
    )

    assert weak_dual_pressure == pytest.approx(0.60 * 0.80, rel=1e-6)
    assert strong_flip == pytest.approx(0.60, rel=1e-6)
    assert untouched == pytest.approx(0.60, rel=1e-6)


def test_pocket_entry_override_prefers_exact_match_then_state_fallback() -> None:
    cfg = MACDStrategyV2Config(
        pocket_entry_overrides={
            "*|long_dual_support": {"min_vwap_score": 0.18},
            "red_bar_growing|long_dual_support": {
                "min_signal_score": 0.89,
                "allow_neutral_1h_confirmation": False,
            },
        }
    )

    exact = cfg.resolve_pocket_entry_override("red_bar_growing", "long_dual_support")
    assert exact["min_signal_score"] == pytest.approx(0.89, rel=1e-6)
    assert exact["allow_neutral_1h_confirmation"] is False

    fallback = cfg.resolve_pocket_entry_override("green_bar_growing", "long_dual_support")
    assert fallback["min_vwap_score"] == pytest.approx(0.18, rel=1e-6)


def test_resolve_pocket_entry_requirements_applies_trial_and_override_rules() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            min_signal_score=0.83,
            preflip_trial_min_signal_score=0.70,
            min_vwap_score_for_entry=0.10,
            preflip_trial_min_vwap_score=0.06,
            allow_neutral_1h_confirmation=True,
            pocket_entry_overrides={
                "red_bar_growing|long_dual_support": {
                    "min_signal_score": 0.895,
                    "min_vwap_score": 0.20,
                    "allow_neutral_1h_confirmation": False,
                    "disallow_trial_entry": True,
                    "require_strict_1h_confirmation": True,
                }
            },
        )
    )

    requirements = engine.resolve_pocket_entry_requirements(
        signal_type_1h="red_bar_growing",
        vwap_state="long_dual_support",
        is_trial_entry=True,
    )

    assert requirements["signal_score_threshold"] == pytest.approx(0.895, rel=1e-6)
    assert requirements["min_vwap_score_for_entry"] == pytest.approx(0.20, rel=1e-6)
    assert requirements["allow_neutral_1h_confirmation"] is False
    assert requirements["disallow_trial_entry"] is True
    assert requirements["require_strict_1h_confirmation"] is True


def test_resolve_pocket_entry_requirements_zeroes_vwap_floor_when_thresholds_removed() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            disable_vwap_thresholds=True,
            min_signal_score=0.83,
            preflip_trial_min_signal_score=0.70,
            min_vwap_score_for_entry=0.10,
            preflip_trial_min_vwap_score=0.06,
        )
    )

    base = engine.resolve_pocket_entry_requirements(
        signal_type_1h="red_bar_growing",
        vwap_state="long_dual_support",
        is_trial_entry=False,
    )
    trial = engine.resolve_pocket_entry_requirements(
        signal_type_1h="red_bar_growing",
        vwap_state="long_dual_support",
        is_trial_entry=True,
    )

    assert base["min_vwap_score_for_entry"] == pytest.approx(0.0, rel=1e-6)
    assert trial["min_vwap_score_for_entry"] == pytest.approx(0.0, rel=1e-6)


def test_resolve_pocket_entry_requirements_supports_disabled_flag() -> None:
    engine = MACDStrategyV2Engine(
        MACDStrategyV2Config(
            pocket_entry_overrides={
                "*|long_dual_support": {"disabled": True, "label": "disabled_long_dual_support"}
            }
        )
    )

    requirements = engine.resolve_pocket_entry_requirements(
        signal_type_1h="red_bar_growing",
        vwap_state="long_dual_support",
        is_trial_entry=False,
    )

    assert requirements["disabled"] is True
    assert requirements["override_label"] == "disabled_long_dual_support"
