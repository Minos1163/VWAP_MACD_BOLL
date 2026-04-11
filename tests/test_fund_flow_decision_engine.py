from types import SimpleNamespace

from src.fund_flow.decision_engine import FundFlowDecisionEngine
from src.fund_flow.models import FundFlowDecision, Operation
from src.fund_flow.macd_strategy_v2 import MACDSignalV2
from scripts.backtest_macd_v2 import build_strategy_config


def _cfg():
    return {
        "trading": {"default_leverage": 2},
        "risk": {"max_position_pct": 0.2},
        "fund_flow": {
            "default_target_portion": 0.2,
            "open_threshold": 0.2,
            "close_threshold": 0.3,
            "entry_slippage": 0.001,
            "deepseek_weight_router": {"enabled": False},
        },
    }


def _trend_context(
    *,
    cvd_ratio: float,
    cvd_momentum: float,
    oi_delta_ratio: float,
    funding_rate: float,
    depth_ratio: float,
    imbalance: float,
    ema_fast: float,
    ema_slow: float,
    adx: float = 30.0,
    atr_pct: float = 0.005,
):
    tf_ctx = {
        "cvd_ratio": cvd_ratio,
        "cvd_momentum": cvd_momentum,
        "oi_delta_ratio": oi_delta_ratio,
        "funding_rate": funding_rate,
        "depth_ratio": depth_ratio,
        "imbalance": imbalance,
    }
    tf_15m = {**tf_ctx, "ema_fast": ema_fast, "ema_slow": ema_slow, "adx": adx, "atr_pct": atr_pct}
    return {"timeframes": {"15m": tf_15m, "5m": dict(tf_ctx)}}


def test_macd_mtf_default_4h_enhancement_weight_is_aligned_to_v2_default():
    cfg = _cfg()
    cfg["fund_flow"]["macd_mtf_strategy"] = {}

    engine = FundFlowDecisionEngine(cfg)

    assert engine.macd_mtf_strategy_config.weight_4h_enhancement == 0.10


def test_collect_symbol_signal_override_items_reads_top_level_symbol_overrides() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    cfg["fund_flow"]["macd_mtf_strategy_v2"] = {
        "entry_filters": {
            "min_signal_score": 0.85,
        }
    }
    cfg["fund_flow"]["symbol_overrides"] = {
        "DOGEUSDT": {
            "disable_flip_bullish_trial": True,
            "preflip_trial_min_signal_score_override": 0.90,
        }
    }

    engine = FundFlowDecisionEngine(cfg)
    override = engine.symbol_signal_override_registry.get_override("DOGEUSDT")

    assert override is not None
    assert override.disable_flip_bullish_trial is True
    assert override.preflip_trial_min_signal_score_override == 0.90


def test_macd_v2_engine_for_symbol_applies_trial_specific_overrides() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    cfg["fund_flow"]["macd_mtf_strategy_v2"] = {
        "entry_filters": {
            "enable_4h_preflip_trial_entries": True,
            "preflip_trial_min_signal_score": 0.75,
        }
    }
    cfg["fund_flow"]["symbol_overrides"] = {
        "DOGEUSDT": {
            "disable_flip_bullish_trial": True,
            "preflip_trial_min_signal_score_override": 0.90,
        }
    }

    engine = FundFlowDecisionEngine(cfg)
    local_engine, override = engine._macd_v2_engine_for_symbol("DOGEUSDT")

    assert override["disable_flip_bullish_trial"] is True
    assert local_engine.config.disable_flip_bullish_trial_entries is True
    assert local_engine.config.preflip_trial_min_signal_score == 0.90


def test_macd_v2_engine_for_symbol_marks_explicit_flip_bullish_disable_as_force_override() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    cfg["fund_flow"]["macd_mtf_strategy_v2"] = {
        "entry_filters": {
            "disable_flip_bullish_entries": False,
        }
    }
    cfg["fund_flow"]["symbol_overrides"] = {
        "BCHUSDT": {
            "disable_flip_bullish": True,
        }
    }

    engine = FundFlowDecisionEngine(cfg)
    local_engine, override = engine._macd_v2_engine_for_symbol("BCHUSDT")

    assert override["disable_flip_bullish"] is True
    assert local_engine.config.disable_flip_bullish_entries is True
    assert local_engine.config.force_disable_flip_bullish_entries is True


def test_macd_v2_engine_for_symbol_supports_signal_mode_overrides() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    cfg["fund_flow"]["macd_mtf_strategy_v2"] = {
        "entry_filters": {
            "disable_flip_bullish_entries": False,
            "disable_flip_bullish_trial_entries": False,
            "disable_green_bar_growing_entries": True,
            "min_signal_score": 0.85,
            "flip_bullish_min_vwap_score": 0.12,
        }
    }
    cfg["fund_flow"]["symbol_overrides"] = {
        "DOGEUSDT": {
            "flip_bullish_mode": "trial_only",
            "green_bar_growing_mode": "enabled_with_strict_threshold",
            "min_signal_score_override": 0.91,
            "min_vwap_score_override": 0.15,
        }
    }

    engine = FundFlowDecisionEngine(cfg)
    local_engine, override = engine._macd_v2_engine_for_symbol("DOGEUSDT")

    assert override["flip_bullish_mode"] == "trial_only"
    assert override["green_bar_growing_mode"] == "enabled_with_strict_threshold"
    assert local_engine.config.disable_flip_bullish_entries is True
    assert local_engine.config.force_disable_flip_bullish_entries is False
    assert local_engine.config.disable_flip_bullish_trial_entries is False
    assert local_engine.config.disable_green_bar_growing_entries is False
    assert local_engine.config.min_signal_score == 0.91
    assert local_engine.config.flip_bullish_min_vwap_score == 0.15


def test_enforce_direction_lock_on_decision_marks_structured_primary_blocker() -> None:
    engine = FundFlowDecisionEngine(_cfg())
    decision = FundFlowDecision(
        operation=Operation.BUY,
        symbol="BTCUSDT",
        target_portion_of_balance=0.2,
        leverage=2,
        reason="macd_v2_long_signal",
        metadata={"strategy_mode": "macd_mtf_strategy_v2"},
    )

    blocked = engine._enforce_direction_lock_on_decision(
        decision,
        {"direction": "SHORT_ONLY"},
        source="macd_v2",
    )

    assert blocked.operation == Operation.HOLD
    assert blocked.metadata["direction_lock_block"] is True
    assert blocked.metadata["primary_open_blocker"] == "direction_lock_block"
    assert blocked.metadata["blocked_reason"] == "direction_lock_hard_block:SHORT_ONLY"


def test_macd_v2_config_keeps_long_whitelist_controls_in_decision_engine() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    cfg["fund_flow"]["macd_mtf_strategy_v2"] = {
        "entry_filters": {
            "long_entry_mode": "whitelist_only",
            "long_whitelist_signal_types": ["green_bar_growing"],
            "long_whitelist_vwap_states": ["long_dual_support"],
            "long_whitelist_pockets": ["flip_bullish|long_reclaim_confirmed"],
        }
    }

    engine = FundFlowDecisionEngine(cfg)
    strategy_config = build_strategy_config(cfg)

    assert engine.macd_v2_config.long_entry_mode == "whitelist_only"
    assert engine.macd_v2_config.long_whitelist_signal_types == ["green_bar_growing"]
    assert engine.macd_v2_config.long_whitelist_vwap_states == ["long_dual_support"]
    assert engine.macd_v2_config.long_whitelist_pockets == ["flip_bullish|long_reclaim_confirmed"]
    assert engine.macd_v2_config.long_entry_mode == strategy_config.long_entry_mode
    assert engine.macd_v2_config.long_whitelist_signal_types == strategy_config.long_whitelist_signal_types
    assert engine.macd_v2_config.long_whitelist_vwap_states == strategy_config.long_whitelist_vwap_states
    assert engine.macd_v2_config.long_whitelist_pockets == strategy_config.long_whitelist_pockets


def test_pocket_leverage_floor_cannot_override_watchlist_max_leverage() -> None:
    engine = FundFlowDecisionEngine(_cfg())
    engine.min_leverage = 3
    engine.max_leverage = 5
    macd_v2_engine = SimpleNamespace(
        config=SimpleNamespace(symbol_risk_watchlist_max_leverage=3),
        is_watchlist_symbol=lambda symbol: str(symbol).upper() == "TONUSDT",
    )

    leverage = engine._apply_pocket_leverage_constraints(
        leverage=3,
        pocket_management_override={"leverage_floor": 5},
        macd_v2_engine=macd_v2_engine,
        symbol="TONUSDT",
    )

    assert leverage == 3


def test_decide_hold_when_long_score_lacks_breakout_or_pullback():
    engine = FundFlowDecisionEngine(_cfg())
    decision = engine.decide(
        symbol="BTCUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context=_trend_context(
            cvd_ratio=0.8,
            cvd_momentum=0.6,
            oi_delta_ratio=0.4,
            funding_rate=-0.1,
            depth_ratio=1.2,
            imbalance=0.7,
            ema_fast=101.0,
            ema_slow=100.0,
        ),
        trigger_context={"trigger_type": "signal"},
    )
    assert decision.operation == Operation.HOLD


def test_decide_close_long_when_short_reversal():
    engine = FundFlowDecisionEngine(_cfg())
    decision = engine.decide(
        symbol="BTCUSDT",
        portfolio={"positions": {"BTCUSDT": {"side": "LONG"}}},
        price=100.0,
        market_flow_context=_trend_context(
            cvd_ratio=-0.9,
            cvd_momentum=-0.8,
            oi_delta_ratio=0.5,
            funding_rate=0.2,
            depth_ratio=0.8,
            imbalance=-0.7,
            ema_fast=99.0,
            ema_slow=100.0,
        ),
        trigger_context={"trigger_type": "signal"},
    )
    assert decision.operation == Operation.HOLD
    assert decision.reason == "macd_mtf_missing_tf_data"


def test_macd_v2_missing_tf_data_includes_timeframe_diagnostics() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    engine = FundFlowDecisionEngine(cfg)

    decision = engine._decide_macd_v2_strategy(
        symbol="BTCUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context={"timeframes": {"15m": {"macd_hist": 0.1}, "1h": {}, "4h": {}}},
        regime_info={"regime": "TREND", "direction": "BOTH", "guide_direction": "BOTH"},
    )

    assert decision.operation == Operation.HOLD
    assert decision.reason == "macd_v2_missing_tf_data"
    diag = decision.metadata["macd_tf_diagnostics"]
    assert diag["15m"]["timeframe_present"] is True
    assert diag["1h"]["timeframe_present"] is False
    assert diag["4h"]["timeframe_present"] is False


def test_decide_macd_v2_hard_blocks_long_when_direction_lock_is_short_only():
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    engine = FundFlowDecisionEngine(cfg)
    engine.macd_v2_enabled = True
    engine.macd_v2_engine = object()
    engine._detect_regime = lambda *_args, **_kwargs: {
        "regime": "TREND",
        "direction": "SHORT_ONLY",
        "guide_direction": "SHORT_ONLY",
    }
    engine._decide_macd_v2_strategy = lambda *_args, **_kwargs: SimpleNamespace(
        operation=Operation.BUY,
        symbol="BTCUSDT",
        target_portion_of_balance=0.2,
        leverage=2,
        max_price=None,
        min_price=None,
        time_in_force=None,
        take_profit_price=None,
        stop_loss_price=None,
        tp_execution=None,
        sl_execution=None,
        reason="macd_v2_long_signal",
        metadata={"strategy_mode": "macd_mtf_strategy_v2"},
    )

    decision = engine.decide(
        symbol="BTCUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context={"timeframes": {"15m": {}, "1h": {}, "4h": {}}},
        trigger_context={"trigger_type": "signal"},
    )

    assert decision.operation == Operation.HOLD
    assert "direction_lock_hard_block" in decision.reason
    assert decision.metadata["direction_lock"] == "SHORT_ONLY"
    assert decision.metadata["blocked_operation"] == Operation.BUY.value


def test_macd_v2_decision_metadata_includes_timeframe_diagnostics_when_series_exist() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    cfg["fund_flow"]["macd_mtf_strategy_v2"] = {
        "entry_thresholds": {
            "default": 0.1,
            "min_entry_score": 0.1,
            "min_signal_score": 0.1,
            "red_bar_growing": 0.1,
            "red_bar_shrinking": 0.1,
            "flip_bearish": 0.1,
            "flip_bullish": 0.1,
            "green_bar_growing": 0.1,
        },
        "entry_filters": {
            "primary_direction_timeframe": "4h",
            "require_1h_confirmation_when_4h_primary": True,
            "allow_neutral_1h_confirmation": True,
            "light_1h_confirmation_when_4h_primary": True,
            "require_15m_confirmation_gate": False,
            "disable_flip_bullish_entries": False,
            "disable_flip_bullish_trial_entries": False,
            "disable_green_bar_growing_entries": False,
            "min_signal_score": 0.1,
            "min_vwap_score_for_entry": 0.0,
        },
    }
    engine = FundFlowDecisionEngine(cfg)

    timeframes = {
        "15m": {
            "timestamp": "2026-04-01T00:00:00+00:00",
            "macd_hist_series": [-0.00040, -0.00025, -0.00018, -0.00010],
            "volume": 200.0,
            "avg_volume": 100.0,
            "bb_middle": 100.0,
            "bb_upper": 103.0,
            "bb_lower": 97.0,
            "close": 100.8,
        },
        "1h": {
            "timestamp": "2026-04-01T00:00:00+00:00",
            "macd_hist_series": [0.0, 0.0, 0.0, 0.0],
            "close": 101.0,
            "vwap": 100.0,
            "structural_vwap": 99.6,
            "bb_middle": 100.0,
            "bb_upper": 110.0,
            "bb_lower": 90.0,
            "atr": 1.0,
            "adx": 20.0,
        },
        "4h": {
            "timestamp": "2026-04-01T00:00:00+00:00",
            "macd_hist_series": [-0.30, -0.15, -0.05, 0.20],
            "bb_middle": 99.0,
            "bb_upper": 109.0,
            "bb_lower": 89.0,
            "adx": 22.0,
        },
    }

    decision = engine._decide_macd_v2_strategy(
        symbol="BTCUSDT",
        portfolio={"positions": {}},
        price=101.0,
        market_flow_context={"timeframes": timeframes},
        regime_info={"regime": "TREND", "direction": "BOTH", "guide_direction": "BOTH", "adx": 22.0, "atr_pct": 0.01},
    )

    assert decision.metadata["strategy_mode"] == "macd_mtf_strategy_v2"
    diag = decision.metadata["macd_tf_diagnostics"]
    assert diag["15m"]["series_source"] == "series"
    assert diag["1h"]["series_source"] == "series"
    assert diag["4h"]["series_source"] == "series"


def test_decide_hold_when_signal_not_enough():
    engine = FundFlowDecisionEngine(_cfg())
    decision = engine.decide(
        symbol="BTCUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context={"cvd_ratio": 0.0},
    )
    assert decision.operation == Operation.HOLD


def test_entry_hard_gate_allows_missing_spread_for_configured_pocket_match() -> None:
    cfg = _cfg()
    cfg["fund_flow"].update(
        {
            "entry_hard_gates_enabled": True,
            "entry_hard_gate_adx_min": 22,
            "entry_hard_gate_atr_min": 0.006,
            "entry_hard_gate_atr_max": 0.02,
            "entry_hard_gate_spread_bps_max": 0.0008,
            "entry_hard_gate_flow_min_pass": 2,
            "entry_hard_gate_micro_min_pass": 1,
            "entry_hard_gate_skip_spread_if_missing": False,
            "entry_hard_gate_skip_spread_if_missing_rules": [
                {
                    "direction": "long",
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_dual_support",
                    "min_vwap_score": 0.12,
                }
            ],
        }
    )
    engine = FundFlowDecisionEngine(cfg)

    passed, reason, _ = engine._validate_macd_v2_entry_hard_gates(
        symbol="BTCUSDT",
        direction="long",
        price=101.0,
        structural_vwap=100.0,
        market_flow_context={"cvd_ratio": 0.1},
        regime_info={"regime": "TREND", "atr_pct": 0.01},
        tf_15m={
            "spread_bps": None,
            "cvd_ratio": 0.1,
            "depth_ratio": 1.03,
            "imbalance": 0.04,
            "cvd_momentum": 0.1,
        },
        tf_1h={"adx": 30.0, "oi_delta_ratio": 0.1},
        signal_type_1h="red_bar_growing",
        vwap_state="long_dual_support",
        vwap_score=0.1535,
    )

    assert passed is True
    assert reason == ""


def test_entry_hard_gate_keeps_missing_spread_block_when_vwap_below_rule_floor() -> None:
    cfg = _cfg()
    cfg["fund_flow"].update(
        {
            "entry_hard_gates_enabled": True,
            "entry_hard_gate_adx_min": 22,
            "entry_hard_gate_atr_min": 0.006,
            "entry_hard_gate_atr_max": 0.02,
            "entry_hard_gate_spread_bps_max": 0.0008,
            "entry_hard_gate_flow_min_pass": 2,
            "entry_hard_gate_micro_min_pass": 1,
            "entry_hard_gate_skip_spread_if_missing": False,
            "entry_hard_gate_skip_spread_if_missing_rules": [
                {
                    "direction": "long",
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_dual_support",
                    "min_vwap_score": 0.12,
                }
            ],
        }
    )
    engine = FundFlowDecisionEngine(cfg)

    passed, reason, _ = engine._validate_macd_v2_entry_hard_gates(
        symbol="BTCUSDT",
        direction="long",
        price=101.0,
        structural_vwap=100.0,
        market_flow_context={"cvd_ratio": 0.1},
        regime_info={"regime": "TREND", "atr_pct": 0.01},
        tf_15m={
            "spread_bps": None,
            "cvd_ratio": 0.1,
            "depth_ratio": 1.03,
            "imbalance": 0.04,
            "cvd_momentum": 0.1,
        },
        tf_1h={"adx": 30.0, "oi_delta_ratio": 0.1},
        signal_type_1h="red_bar_growing",
        vwap_state="long_dual_support",
        vwap_score=0.10,
    )

    assert passed is False
    assert reason == "L1_structure_failed:spread_ok"


def test_entry_hard_gate_can_skip_regime_trend_requirement_but_keep_other_l1_checks() -> None:
    cfg = _cfg()
    cfg["fund_flow"].update(
        {
            "entry_hard_gates_enabled": True,
            "entry_hard_gate_require_regime_trend": False,
            "entry_hard_gate_adx_min": 22,
            "entry_hard_gate_atr_min": 0.006,
            "entry_hard_gate_atr_max": 0.02,
            "entry_hard_gate_spread_bps_max": 0.0008,
            "entry_hard_gate_flow_min_pass": 2,
            "entry_hard_gate_micro_min_pass": 1,
        }
    )
    engine = FundFlowDecisionEngine(cfg)

    passed, reason, _ = engine._validate_macd_v2_entry_hard_gates(
        symbol="BTCUSDT",
        direction="long",
        price=101.0,
        structural_vwap=100.0,
        market_flow_context={"cvd_ratio": 0.1},
        regime_info={"regime": "RANGE", "atr_pct": 0.01},
        tf_15m={
            "spread_bps": 0.0005,
            "cvd_ratio": 0.1,
            "depth_ratio": 1.03,
            "imbalance": 0.04,
            "cvd_momentum": 0.1,
        },
        tf_1h={"adx": 30.0, "oi_delta_ratio": 0.1},
        signal_type_1h="green_bar_growing",
        vwap_state="long_reclaim_confirmed",
        vwap_score=0.1535,
    )

    assert passed is True
    assert reason == ""


def test_entry_hard_gate_can_bypass_selected_l1_failures_for_target_long_symbols() -> None:
    cfg = _cfg()
    cfg["fund_flow"].update(
        {
            "entry_hard_gates_enabled": True,
            "entry_hard_gate_adx_min": 18,
            "entry_hard_gate_atr_min": 0.003,
            "entry_hard_gate_atr_max": 0.02,
            "entry_hard_gate_spread_bps_max": 0.0008,
            "entry_hard_gate_flow_min_pass": 1,
            "entry_hard_gate_micro_min_pass": 1,
            "entry_hard_gate_bypass_rules": [
                {
                    "direction": "long",
                    "symbols": ["TRUMPUSDT", "SUIUSDT"],
                    "signal_type_1h": "flip_bullish",
                    "vwap_state": "long_reclaim_confirmed",
                    "min_signal_score": 0.90,
                    "min_vwap_score": 0.12,
                    "allow_l1_failures": ["regime_trend", "adx_sufficient", "spread_ok"],
                }
            ],
        }
    )
    engine = FundFlowDecisionEngine(cfg)

    passed, reason, meta = engine._validate_macd_v2_entry_hard_gates(
        symbol="TRUMPUSDT",
        direction="long",
        price=101.0,
        structural_vwap=100.0,
        market_flow_context={"cvd_ratio": 0.1},
        regime_info={"regime": "RANGE", "atr_pct": 0.01},
        tf_15m={
            "spread_bps": 0.0012,
            "cvd_ratio": 0.1,
            "depth_ratio": 1.03,
            "imbalance": 0.04,
            "cvd_momentum": 0.1,
        },
        tf_1h={"adx": 15.0, "oi_delta_ratio": 0.01},
        signal_type_1h="flip_bullish",
        vwap_state="long_reclaim_confirmed",
        vwap_score=0.16,
        signal_score=0.95,
    )

    assert passed is True
    assert reason == ""
    assert meta["entry_hard_gate_bypass_rule"]["signal_type_1h"] == "flip_bullish"
    assert set(meta["entry_hard_gate_bypassed_l1_failures"]) == {"regime_trend", "adx_sufficient", "spread_ok"}


def test_entry_hard_gate_can_bypass_microstructure_failure_for_target_long_symbols() -> None:
    cfg = _cfg()
    cfg["fund_flow"].update(
        {
            "entry_hard_gates_enabled": True,
            "entry_hard_gate_adx_min": 15,
            "entry_hard_gate_atr_min": 0.003,
            "entry_hard_gate_atr_max": 0.02,
            "entry_hard_gate_spread_bps_max": 0.0008,
            "entry_hard_gate_flow_min_pass": 1,
            "entry_hard_gate_micro_min_pass": 1,
            "entry_hard_gate_bypass_rules": [
                {
                    "direction": "long",
                    "symbol": "TRUMPUSDT",
                    "signal_type_1h": "flip_bullish",
                    "vwap_state": "long_reclaim_confirmed",
                    "min_signal_score": 0.90,
                    "min_vwap_score": 0.12,
                    "allow_microstructure_fail": True,
                }
            ],
        }
    )
    engine = FundFlowDecisionEngine(cfg)

    passed, reason, meta = engine._validate_macd_v2_entry_hard_gates(
        symbol="TRUMPUSDT",
        direction="long",
        price=101.0,
        structural_vwap=100.0,
        market_flow_context={"cvd_ratio": 0.1},
        regime_info={"regime": "TREND", "atr_pct": 0.01},
        tf_15m={
            "spread_bps": 0.0004,
            "cvd_ratio": 0.1,
            "depth_ratio": 0.95,
            "imbalance": 0.00,
            "cvd_momentum": -0.1,
        },
        tf_1h={"adx": 20.0, "oi_delta_ratio": 0.01},
        signal_type_1h="flip_bullish",
        vwap_state="long_reclaim_confirmed",
        vwap_score=0.16,
        signal_score=0.95,
    )

    assert passed is True
    assert reason == ""
    assert meta["entry_hard_gate_bypass_rule"]["symbol"] == "TRUMPUSDT"
    assert meta["entry_hard_gate_bypassed_microstructure"] is True


def test_entry_hard_gate_bypass_rule_can_require_stable_continuation_active() -> None:
    cfg = _cfg()
    cfg["fund_flow"].update(
        {
            "entry_hard_gates_enabled": True,
            "entry_hard_gate_adx_min": 15,
            "entry_hard_gate_atr_min": 0.003,
            "entry_hard_gate_atr_max": 0.02,
            "entry_hard_gate_spread_bps_max": 0.0008,
            "entry_hard_gate_flow_min_pass": 1,
            "entry_hard_gate_micro_min_pass": 1,
            "entry_hard_gate_bypass_rules": [
                {
                    "direction": "long",
                    "symbol": "SOLUSDT",
                    "signal_type_1h": "flip_bullish",
                    "vwap_state": "long_reclaim_confirmed",
                    "min_signal_score": 0.95,
                    "min_vwap_score": 0.13,
                    "require_stable_continuation_active": True,
                    "allow_l1_failures": ["regime_trend"],
                }
            ],
        }
    )
    engine = FundFlowDecisionEngine(cfg)

    passed, reason, _ = engine._validate_macd_v2_entry_hard_gates(
        symbol="SOLUSDT",
        direction="long",
        price=101.0,
        structural_vwap=100.0,
        market_flow_context={"cvd_ratio": 0.1},
        regime_info={"regime": "RANGE", "atr_pct": 0.01},
        tf_15m={"spread_bps": 0.0004, "cvd_ratio": 0.1, "depth_ratio": 1.03, "imbalance": 0.04, "cvd_momentum": 0.1},
        tf_1h={"adx": 20.0, "oi_delta_ratio": 0.01},
        signal_type_1h="flip_bullish",
        vwap_state="long_reclaim_confirmed",
        vwap_score=0.16,
        signal_score=1.0,
        stable_continuation_active=False,
    )

    assert passed is False
    assert reason == "L1_structure_failed:regime_trend"


def test_direction_lock_bypass_rule_can_release_target_long_signal() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["direction_lock_bypass_rules"] = [
        {
            "direction_lock": "SHORT_ONLY",
            "decision_side": "long",
            "symbol": "SOLUSDT",
            "signal_type_1h": "flip_bullish",
            "vwap_state": "long_reclaim_confirmed",
            "min_signal_score": 0.95,
            "min_vwap_score": 0.13,
            "require_stable_continuation_active": True,
        }
    ]
    engine = FundFlowDecisionEngine(cfg)
    decision = FundFlowDecision(
        operation=Operation.BUY,
        symbol="SOLUSDT",
        target_portion_of_balance=0.2,
        leverage=3,
        reason="macd_v2_long_signal",
        metadata={
            "signal_type_1h": "flip_bullish",
            "vwap_state": "long_reclaim_confirmed",
            "signal_score": 1.0,
            "vwap_score": 0.16,
            "stable_continuation_active": True,
        },
    )

    out = engine._enforce_direction_lock_on_decision(
        decision,
        {"direction": "SHORT_ONLY"},
        source="macd_v2",
    )

    assert out.operation == Operation.BUY
    assert out.metadata["direction_lock_bypass_rule"]["symbol"] == "SOLUSDT"


def test_pure_strategy_runtime_bypasses_entry_hard_gate() -> None:
    cfg = _cfg()
    cfg["fund_flow"].update(
        {
            "entry_hard_gates_enabled": True,
            "pure_strategy_runtime": {
                "enabled": True,
                "bypass_entry_hard_gate": True,
            },
        }
    )
    engine = FundFlowDecisionEngine(cfg)

    passed, reason, metadata = engine._validate_macd_v2_entry_hard_gates(
        symbol="BTCUSDT",
        direction="long",
        price=100.0,
        structural_vwap=200.0,
        market_flow_context={},
        regime_info={"regime": "RANGE", "atr_pct": 0.0001},
        tf_15m={"spread_bps": 99.0},
        tf_1h={"adx": 1.0, "oi_delta_ratio": -1.0},
        signal_type_1h="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
        vwap_score=0.01,
    )

    assert passed is True
    assert reason == ""
    assert metadata["pure_strategy_runtime_entry_hard_gate_bypassed"] is True


def test_decide_macd_v2_pure_runtime_clears_signal_pool_metadata() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    cfg["fund_flow"]["pure_strategy_runtime"] = {
        "enabled": True,
        "bypass_signal_pool_metadata": True,
    }
    engine = FundFlowDecisionEngine(cfg)
    engine.macd_v2_enabled = True
    engine.macd_v2_engine = object()
    engine._detect_regime = lambda *_args, **_kwargs: {
        "regime": "TREND",
        "direction": "BOTH",
        "guide_direction": "BOTH",
    }
    engine._decide_macd_v2_strategy = lambda *_args, **_kwargs: FundFlowDecision(
        operation=Operation.BUY,
        symbol="BTCUSDT",
        target_portion_of_balance=0.2,
        leverage=2,
        reason="macd_v2_long_signal",
        metadata={"strategy_mode": "macd_mtf_strategy_v2", "signal_pool_id": "trend_pool", "selected_pool_id": "trend_pool"},
    )

    decision = engine.decide(
        symbol="BTCUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context={"timeframes": {"15m": {}, "1h": {}, "4h": {}}},
        trigger_context={"trigger_type": "signal"},
    )

    assert decision.operation == Operation.BUY
    assert decision.metadata["pure_strategy_runtime_enabled"] is True
    assert decision.metadata["signal_pool_id"] is None
    assert decision.metadata["selected_pool_id"] is None


def test_trend_capture_keeps_partial_score_without_micro_confirm():
    cfg = _cfg()
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "partial_confirm_enabled": True,
        "partial_confirm_min_align": 2,
        "partial_confirm_penalty": 0.03,
        "depth_ratio_neutral": 1.0,
        "depth_ratio_buffer": 0.0,
    }
    engine = FundFlowDecisionEngine(cfg)
    capture = engine._compute_trend_capture(
        "BTCUSDT",
        market_flow_context={
            "timeframes": {
                "5m": {
                    "close": 105.0,
                    "hh_n": 105.0,
                    "ll_n": 100.0,
                    "ema_fast": 104.0,
                    "ema_slow": 102.0,
                    "ret_period": 0.01,
                    "cvd_momentum": 0.02,
                    "oi_delta_ratio": 0.0,
                    "depth_ratio": 1.02,
                    "imbalance": 0.03,
                },
                "3m": {
                    "ret_period": 0.0,
                },
            },
            "microstructure_features": {
                "micro_delta": 0.0,
                "microprice_bias": 0.0,
                "trap_score": 0.1,
                "phantom_score": 0.1,
                "spread_z": 0.1,
            },
        },
        regime_info={},
        trend_pending={},
    )
    assert capture["trend_capture_breakout_long"] is True
    assert capture["trend_capture_confirm_3m_long"] is False


def test_macd_v2_shrink_exit_loss_mitigation_returns_partial_close() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    cfg["fund_flow"]["macd_mtf_strategy_v2"] = {
        "stop_loss_config": {
            "enable_4h_shrink_exit": True,
            "exit_4h_require_profit": False,
            "exit_4h_weak_loss_threshold": -1.0,
            "shrink_exit_loss_mitigation_enabled": True,
            "shrink_exit_loss_mitigation_pnl_threshold": -0.005,
            "shrink_exit_loss_mitigation_exit_ratio": 0.6,
            "shrink_exit_loss_mitigation_ignore_if_pnl_gt": 0.01,
        }
    }
    engine = FundFlowDecisionEngine(cfg)
    signal = MACDSignalV2(
        direction="neutral",
        signal_score=0.0,
        signal_type_1h="green_bar_growing",
        entry_type_15m="green_bar_growing",
        entry_score_15m=0.4,
        vwap_score=0.2,
        vwap_deviation=0.0,
        vwap_state="short_dual_pressure",
        vwap_location_score=0.6,
        ema_multiplier=1.0,
        ema_structure_status="normal",
        details={
            "shrink_exit_direction": "LONG",
            "shrink_exit_ready": True,
            "macd_4h_shrink_pct": 0.2,
            "macd_4h_shrink_bars": 2,
        },
    )
    engine._macd_v2_engine_for_symbol = lambda _symbol: (
        SimpleNamespace(
            analyze=lambda **_kwargs: signal,
            resolve_4h_shrink_exit_policy=lambda **_kwargs: {
                "active": True,
                "mode": "default",
                "shrink_exit_direction": "LONG",
                "required_bars": 2,
                "required_pct": 0.15,
                "shrink_bars": 2,
                "shrink_pct": 0.2,
                "stable_continuation_active": False,
            }
        ),
        {},
    )
    engine._rule_position_pnl_ratio = lambda *_args, **_kwargs: -0.006
    engine._detect_regime = lambda *_args, **_kwargs: {"regime": "TREND"}
    engine._build_macd_v2_regime_entry_decision = lambda **_kwargs: None

    decision = engine._decide_macd_v2_strategy(
        "BTCUSDT",
        {"positions": {"BTCUSDT": {"side": "LONG"}}},
        100.0,
        {
            "timeframes": {
                "15m": {"timestamp": "2026-04-01 00:00:00", "macd_hist": 0.0, "macd_hist_prev": 0.0},
                "1h": {"timestamp": "2026-04-01 00:00:00", "macd_hist": 0.0, "macd_hist_prev": 0.0},
                "4h": {"timestamp": "2026-04-01 00:00:00", "macd_hist": 0.0, "macd_hist_prev": 0.0},
            }
        },
        {"regime": "TREND"},
    )

    assert decision.operation == Operation.CLOSE
    assert decision.target_portion_of_balance == 0.6
    assert decision.reason == "macd_v2_4h_shrink_reduce_long"


def test_macd_v2_shrink_exit_loss_mitigation_can_be_disabled_for_main_short_pocket() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    cfg["fund_flow"]["macd_mtf_strategy_v2"] = {
        "stop_loss_config": {
            "enable_4h_shrink_exit": True,
            "exit_4h_require_profit": False,
            "exit_4h_weak_loss_threshold": -1.0,
            "shrink_exit_loss_mitigation_enabled": True,
            "shrink_exit_loss_mitigation_pnl_threshold": -0.005,
            "shrink_exit_loss_mitigation_exit_ratio": 0.6,
            "shrink_exit_loss_mitigation_ignore_if_pnl_gt": 0.01,
        },
        "pocket_management_overrides": {
            "green_bar_growing|short_retest_reject": {
                "disable_shrink_loss_mitigation": True
            }
        },
    }
    engine = FundFlowDecisionEngine(cfg)
    signal = MACDSignalV2(
        direction="neutral",
        signal_score=0.0,
        signal_type_1h="green_bar_growing",
        entry_type_15m="green_bar_growing",
        entry_score_15m=0.4,
        vwap_score=0.2,
        vwap_deviation=0.0,
        vwap_state="short_retest_reject",
        vwap_location_score=0.6,
        ema_multiplier=1.0,
        ema_structure_status="normal",
        details={
            "shrink_exit_direction": "SHORT",
            "shrink_exit_ready": True,
            "macd_4h_shrink_pct": 0.2,
            "macd_4h_shrink_bars": 2,
        },
    )
    engine._macd_v2_engine_for_symbol = lambda _symbol: (
        SimpleNamespace(
            analyze=lambda **_kwargs: signal,
            resolve_4h_shrink_exit_policy=lambda **_kwargs: {
                "active": True,
                "mode": "default",
                "shrink_exit_direction": "SHORT",
                "required_bars": 2,
                "required_pct": 0.15,
                "shrink_bars": 2,
                "shrink_pct": 0.2,
                "stable_continuation_active": False,
            },
        ),
        {},
    )
    engine._rule_position_pnl_ratio = lambda *_args, **_kwargs: -0.006
    engine._detect_regime = lambda *_args, **_kwargs: {"regime": "TREND"}
    engine._build_macd_v2_regime_entry_decision = lambda **_kwargs: None

    decision = engine._decide_macd_v2_strategy(
        "BTCUSDT",
        {"positions": {"BTCUSDT": {"side": "SHORT"}}},
        100.0,
        {
            "timeframes": {
                "15m": {"timestamp": "2026-04-01 00:00:00", "macd_hist": 0.0, "macd_hist_prev": 0.0},
                "1h": {"timestamp": "2026-04-01 00:00:00", "macd_hist": 0.0, "macd_hist_prev": 0.0},
                "4h": {"timestamp": "2026-04-01 00:00:00", "macd_hist": 0.0, "macd_hist_prev": 0.0},
            }
        },
        {"regime": "TREND"},
    )

    assert decision.reason == "macd_v2_4h_shrink_exit_short"


def test_macd_v2_normal_shrink_exit_can_be_disabled_for_main_short_pocket() -> None:
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    cfg["fund_flow"]["macd_mtf_strategy_v2"] = {
        "stop_loss_config": {
            "enable_4h_shrink_exit": True,
            "exit_4h_require_profit": False,
            "exit_4h_weak_loss_threshold": -1.0,
            "shrink_exit_loss_mitigation_enabled": False,
        },
        "pocket_management_overrides": {
            "green_bar_growing|short_retest_reject": {
                "enable_4h_shrink_exit": False
            }
        },
    }
    engine = FundFlowDecisionEngine(cfg)
    signal = MACDSignalV2(
        direction="neutral",
        signal_score=0.0,
        signal_type_1h="green_bar_growing",
        entry_type_15m="green_bar_growing",
        entry_score_15m=0.4,
        vwap_score=0.2,
        vwap_deviation=0.0,
        vwap_state="short_retest_reject",
        vwap_location_score=0.6,
        ema_multiplier=1.0,
        ema_structure_status="normal",
        details={
            "shrink_exit_direction": "SHORT",
            "shrink_exit_ready": True,
            "macd_4h_shrink_pct": 0.2,
            "macd_4h_shrink_bars": 2,
        },
    )
    engine._macd_v2_engine_for_symbol = lambda _symbol: (
        SimpleNamespace(
            analyze=lambda **_kwargs: signal,
            resolve_4h_shrink_exit_policy=lambda **_kwargs: {
                "active": True,
                "mode": "default",
                "shrink_exit_direction": "SHORT",
                "required_bars": 2,
                "required_pct": 0.15,
                "shrink_bars": 2,
                "shrink_pct": 0.2,
                "stable_continuation_active": False,
            },
        ),
        {},
    )
    engine._rule_position_pnl_ratio = lambda *_args, **_kwargs: -0.006
    engine._detect_regime = lambda *_args, **_kwargs: {"regime": "TREND"}
    engine._build_macd_v2_regime_entry_decision = lambda **_kwargs: None

    decision = engine._decide_macd_v2_strategy(
        "BTCUSDT",
        {"positions": {"BTCUSDT": {"side": "SHORT", "signal_type_1h": "green_bar_growing", "vwap_state": "short_retest_reject"}}},
        100.0,
        {
            "timeframes": {
                "15m": {"timestamp": "2026-04-01 00:00:00", "macd_hist": 0.0, "macd_hist_prev": 0.0},
                "1h": {"timestamp": "2026-04-01 00:00:00", "macd_hist": 0.0, "macd_hist_prev": 0.0},
                "4h": {"timestamp": "2026-04-01 00:00:00", "macd_hist": 0.0, "macd_hist_prev": 0.0},
            }
        },
        {"regime": "TREND"},
    )

    assert decision.operation == Operation.HOLD


def test_pick_leverage_uses_discrete_config_levels():
    engine = FundFlowDecisionEngine(_cfg())
    assert engine._pick_leverage(0.11, 0.10, 4, 8, 6) == 4
    assert engine._pick_leverage(0.55, 0.10, 4, 8, 6) == 6
    assert engine._pick_leverage(0.95, 0.10, 4, 8, 6) == 4


def test_resolve_entry_mode_uses_base_score_floor_for_trend_entry():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.085
    cfg["fund_flow"]["short_open_threshold"] = 0.085
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="BTCUSDT",
        regime_info={
            "regime": "TREND",
            "cvd_norm": 0.3,
            "combo_compare": {"feature_snapshot": {"macd_hist_sign": 1, "kdj_cross_sign": 1}},
        },
        base_scores={"long_score": 0.10, "short_score": 0.0},
        trend_pending={"trend_pending_side": "NONE", "trend_pending_score": 0.0},
        trend_capture={
            "trend_capture_score_long": 0.0,
            "trend_capture_score_short": 0.0,
            "trend_capture_breakout_long": True,
            "trend_capture_pullback_resume_long": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": False,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.BUY
    assert resolved.metadata["final_long_score"] >= 0.085


def test_confluence_ignores_ma10_bias_for_hard_block():
    engine = FundFlowDecisionEngine(_cfg())
    confluence = engine._compute_entry_confluence_v2(
        "BTCUSDT",
        market_flow_context={
            "_ma10_macd_confluence": {
                "last_close_1h": 99.0,
                "ma10_1h": 100.0,
                "ma10_1h_bias": -1,
                "macd_5m": 0.5,
                "macd_5m_signal": 0.1,
                "macd_5m_hist": 0.6,
                "macd_5m_hist_delta": 0.1,
                "kdj_k": 55.0,
                "kdj_d": 50.0,
                "kdj_j": 65.0,
            },
            "timeframes": {"5m": {}, "1h": {}},
        },
        cfg=engine._trend_capture_config(),
    )
    assert confluence["confluence_hard_block_long"] is False
    assert confluence["confluence_macd_trigger_long"] is True


def test_resolve_entry_mode_prunes_opposite_short_capture_when_confluence_fallback_turns_long():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["short_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="BNBUSDT",
        regime_info={
            "regime": "TREND",
            "guide_direction": "LONG_ONLY",
            "allow_entry_window": True,
            "flow_confirm": 0.5,
            "consistency_3bars": 0,
            "cvd_norm": 0.25,
            "combo_compare": {"feature_snapshot": {"macd_hist_sign": 1, "kdj_cross_sign": 1}},
            "lw": {"components": {"primary_flat": True, "backup_source": "ma10_macd_confluence_5m"}},
            "ev": {"components": {"primary_flat": True, "backup_source": "ma10_macd_confluence_5m"}},
        },
        base_scores={"long_score": 0.09, "short_score": 0.0},
        trend_pending={"trend_pending_side": "LONG", "trend_pending_score": 0.6},
        trend_capture={
            "trend_capture_side": "SHORT",
            "trend_capture_score_long": 0.0,
            "trend_capture_score_short": 0.2,
            "trend_capture_breakout_short": True,
            "trend_capture_cvd_align_short": True,
            "trend_capture_depth_align_short": True,
            "trend_capture_breakout_long": True,
            "trend_capture_pullback_resume_long": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": False,
            "confluence_macd_trigger_long": True,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.BUY
    assert resolved.metadata["trend_capture_score_short"] == 0.0
    assert resolved.metadata["trend_capture_side"] in {"LONG", "NONE"}
    assert resolved.metadata["trend_capture_directional_prune"] is True
    assert resolved.metadata["trend_capture_pruned_side"] == "SHORT"


def test_resolve_entry_mode_prunes_opposite_long_capture_when_confluence_fallback_turns_short():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["short_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="BNBUSDT",
        regime_info={
            "regime": "TREND",
            "guide_direction": "SHORT_ONLY",
            "allow_entry_window": True,
            "flow_confirm": 0.5,
            "consistency_3bars": 0,
            "combo_compare": {"feature_snapshot": {"macd_hist_sign": 1, "kdj_cross_sign": 1}},
            "lw": {"components": {"primary_flat": True, "backup_source": "ma10_macd_confluence_5m"}},
            "ev": {"components": {"primary_flat": True, "backup_source": "ma10_macd_confluence_5m"}},
        },
        base_scores={"long_score": 0.0, "short_score": 0.09},
        trend_pending={"trend_pending_side": "SHORT", "trend_pending_score": 0.6},
        trend_capture={
            "trend_capture_side": "LONG",
            "trend_capture_score_long": 0.2,
            "trend_capture_score_short": 0.0,
            "trend_capture_breakout_long": True,
            "trend_capture_cvd_align_long": True,
            "trend_capture_depth_align_long": True,
            "trend_capture_breakout_short": True,
            "trend_capture_pullback_resume_short": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": False,
            "confluence_macd_trigger_short": True,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.SELL
    assert resolved.metadata["trend_capture_score_long"] == 0.0
    assert resolved.metadata["trend_capture_side"] in {"SHORT", "NONE"}
    assert resolved.metadata["trend_capture_directional_prune"] is True
    assert resolved.metadata["trend_capture_pruned_side"] == "LONG"


def test_resolve_entry_mode_blocks_short_without_3m_confirm_when_required():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["short_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "short_require_confirm_3m": True,
        "short_min_score_boost": 0.02,
        "short_min_gap_boost": 0.01,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="SOLUSDT",
        regime_info={"regime": "TREND"},
        base_scores={"long_score": 0.0, "short_score": 0.34},
        trend_pending={"trend_pending_side": "NONE", "trend_pending_score": 0.0},
        trend_capture={
            "trend_capture_score_long": 0.0,
            "trend_capture_score_short": 1.0,
            "trend_capture_confirm_3m_short": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": False,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.HOLD
    assert resolved.metadata["decision_source"] == "trend_short_confirm_blocked"
    assert resolved.metadata["short_entry_confirm_3m_required"] is True
    assert resolved.metadata["short_entry_confirm_gate_pass"] is False


def test_resolve_entry_mode_injects_long_confluence_fallback_into_entry_score():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["short_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="BNBUSDT",
        regime_info={
            "regime": "TREND",
            "guide_direction": "LONG_ONLY",
            "allow_entry_window": True,
            "flow_confirm": 0.5,
            "consistency_3bars": 0,
            "cvd_norm": 0.25,
            "combo_compare": {"feature_snapshot": {"macd_hist_sign": 1, "kdj_cross_sign": 1}},
            "lw": {
                "components": {
                    "primary_flat": True,
                    "backup_source": "ma10_macd_confluence_5m",
                    "backup_long_score": 1.0,
                    "backup_short_score": 0.0,
                }
            },
            "ev": {
                "components": {
                    "primary_flat": True,
                    "backup_source": "ma10_macd_confluence_5m",
                    "backup_long_score": 1.0,
                    "backup_short_score": 0.0,
                }
            },
        },
        base_scores={"long_score": 0.0001, "short_score": 0.0384},
        trend_pending={"trend_pending_side": "LONG", "trend_pending_score": 0.6},
        trend_capture={
            "trend_capture_side": "SHORT",
            "trend_capture_score_long": 0.0,
            "trend_capture_score_short": 0.2,
            "trend_capture_breakout_short": True,
            "trend_capture_cvd_align_short": True,
            "trend_capture_depth_align_short": True,
            "trend_capture_breakout_long": True,
            "trend_capture_pullback_resume_long": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.08,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": True,
            "confluence_macd_trigger_long": True,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.BUY
    assert resolved.metadata["trend_capture_score_long"] == 1.0
    assert resolved.metadata["trend_capture_confluence_injected"] is True
    assert resolved.metadata["trend_capture_confluence_injected_side"] == "LONG"
    assert resolved.metadata["trend_capture_confluence_injected_score"] == 1.0
    assert resolved.metadata["trend_capture_injection_confirm_pass"] is True
    assert resolved.metadata["trend_capture_injection_gate_pass"] is True
    assert resolved.metadata["final_long_score"] >= 0.07


def test_resolve_entry_mode_injects_short_confluence_fallback_into_entry_score():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["short_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="BNBUSDT",
        regime_info={
            "regime": "TREND",
            "guide_direction": "SHORT_ONLY",
            "allow_entry_window": True,
            "flow_confirm": 0.5,
            "consistency_3bars": 0,
            "combo_compare": {"feature_snapshot": {"macd_hist_sign": 1, "kdj_cross_sign": 1}},
            "lw": {
                "components": {
                    "primary_flat": True,
                    "backup_source": "ma10_macd_confluence_5m",
                    "backup_long_score": 0.0,
                    "backup_short_score": 1.0,
                }
            },
            "ev": {
                "components": {
                    "primary_flat": True,
                    "backup_source": "ma10_macd_confluence_5m",
                    "backup_long_score": 0.0,
                    "backup_short_score": 1.0,
                }
            },
        },
        base_scores={"long_score": 0.0384, "short_score": 0.0001},
        trend_pending={"trend_pending_side": "SHORT", "trend_pending_score": 0.6},
        trend_capture={
            "trend_capture_side": "LONG",
            "trend_capture_score_long": 0.2,
            "trend_capture_score_short": 0.0,
            "trend_capture_breakout_long": True,
            "trend_capture_cvd_align_long": True,
            "trend_capture_depth_align_long": True,
            "trend_capture_breakout_short": True,
            "trend_capture_pullback_resume_short": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.08,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": True,
            "confluence_hard_block_short": False,
            "confluence_macd_trigger_short": True,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.SELL
    assert resolved.metadata["trend_capture_score_short"] == 1.0
    assert resolved.metadata["trend_capture_confluence_injected"] is True
    assert resolved.metadata["trend_capture_confluence_injected_side"] == "SHORT"
    assert resolved.metadata["trend_capture_confluence_injected_score"] == 1.0
    assert resolved.metadata["trend_capture_injection_confirm_pass"] is True
    assert resolved.metadata["trend_capture_injection_gate_pass"] is True
    assert resolved.metadata["final_short_score"] >= 0.07


def test_resolve_entry_mode_does_not_inject_fallback_when_entry_window_closed():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="BNBUSDT",
        regime_info={
            "regime": "TREND",
            "guide_direction": "LONG_ONLY",
            "allow_entry_window": False,
            "flow_confirm": 1.0,
            "consistency_3bars": 1,
            "lw": {"components": {"primary_flat": True, "backup_source": "ma10_macd_confluence_5m", "backup_long_score": 1.0}},
            "ev": {"components": {"primary_flat": True, "backup_source": "ma10_macd_confluence_5m", "backup_long_score": 1.0}},
        },
        base_scores={"long_score": 0.0001, "short_score": 0.0384},
        trend_pending={"trend_pending_side": "LONG", "trend_pending_score": 0.6},
        trend_capture={"trend_capture_score_long": 0.0, "trend_capture_score_short": 0.2, "trend_capture_side": "SHORT"},
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.08,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": True,
            "confluence_macd_trigger_long": True,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.HOLD
    assert resolved.metadata["trend_capture_confluence_injected"] is False
    assert resolved.metadata["trend_capture_injection_confirm_pass"] is True
    assert resolved.metadata["trend_capture_injection_gate_pass"] is False


def test_detect_regime_primary_flat_ignores_ma10_kdj_fallback_and_uses_cvd():
    engine = FundFlowDecisionEngine(_cfg())
    regime_info = engine._detect_regime(
        {
            "cvd_momentum": -0.8,
            "imbalance": -0.7,
            "timeframes": {
                "15m": {
                    "adx": 20.0,
                    "atr_pct": 0.004,
                    "ema_fast": 101.0,
                    "ema_slow": 100.0,
                    "last_open": 100.0,
                    "last_close": 100.2,
                    "macd_hist_norm": 0.0,
                    "macd_cross": "NONE",
                    "macd_hist_delta": 0.0,
                    "kdj_j": 50.0,
                    "kdj_cross": "NONE",
                    "kdj_zone": "MID",
                    "bb_pos_norm": 0.0,
                    "bb_width_norm": 0.0,
                    "bb_break": "NONE",
                    "bb_trend": "MID",
                    "bb_squeeze": False,
                },
                "5m": {},
                "1h": {},
            },
            "_ma10_macd_confluence": {
                "last_close_1h": 101.5,
                "ma10_1h": 100.0,
                "ma10_1h_bias": 1,
                "macd_5m": 0.6,
                "macd_5m_signal": 0.2,
                "macd_5m_hist": 0.4,
                "macd_5m_hist_delta": 0.1,
                "macd_5m_cross": "NONE",
                "macd_5m_zone": "ABOVE_ZERO",
                "kdj_k": 62.0,
                "kdj_d": 55.0,
                "kdj_j": 76.0,
                "kdj_cross": "NONE",
                "kdj_zone": "HIGH",
            },
        }
    )
    assert regime_info["guide_direction"] == "SHORT_ONLY"
    assert regime_info["lw"]["components"]["backup_source"] == "cvd_imbalance"
    assert regime_info["ev"]["components"]["backup_source"] == "cvd_imbalance"


def test_detect_regime_primary_flat_still_uses_cvd_fallback_when_no_confluence():
    engine = FundFlowDecisionEngine(_cfg())
    regime_info = engine._detect_regime(
        {
            "cvd_momentum": -0.8,
            "imbalance": -0.7,
            "timeframes": {
                "15m": {
                    "adx": 20.0,
                    "atr_pct": 0.004,
                    "ema_fast": 101.0,
                    "ema_slow": 100.0,
                    "last_open": 100.0,
                    "last_close": 99.8,
                    "macd_hist_norm": 0.0,
                    "macd_cross": "NONE",
                    "macd_hist_delta": 0.0,
                    "kdj_j": 50.0,
                    "kdj_cross": "NONE",
                    "kdj_zone": "MID",
                    "bb_pos_norm": 0.0,
                    "bb_width_norm": 0.0,
                    "bb_break": "NONE",
                    "bb_trend": "MID",
                    "bb_squeeze": False,
                },
                "5m": {},
            },
        }
    )
    assert regime_info["guide_direction"] == "SHORT_ONLY"
    assert regime_info["lw"]["components"]["backup_source"] == "cvd_imbalance"


def test_detect_regime_conflicting_near_zero_guide_is_neutralized_to_both(monkeypatch):
    engine = FundFlowDecisionEngine(_cfg())
    monkeypatch.setattr(
        engine,
        "_score_lw",
        lambda *_args, **_kwargs: {
            "dir": "SHORT_ONLY",
            "score": -0.024,
            "components": {},
            "combo_compare": {"score_macd_bb": -0.024},
            "active_model": "MACD+BB",
        },
    )
    monkeypatch.setattr(
        engine,
        "_score_ev",
        lambda *_args, **_kwargs: {
            "dir": "LONG_ONLY",
            "score": 0.31,
            "components": {},
            "combo_compare": {"score_macd_bb": -0.024},
            "active_model": "MACD+BB",
        },
    )

    regime_info = engine._detect_regime(
        {
            "cvd_momentum": 0.1,
            "imbalance": 0.1,
            "timeframes": {
                "15m": {
                    "adx": 26.0,
                    "atr_pct": 0.006,
                    "ema_fast": 101.0,
                    "ema_slow": 100.0,
                    "last_open": 100.0,
                    "last_close": 100.2,
                    "macd_hist_norm": -0.024,
                    "macd_cross": "NONE",
                    "macd_hist_delta": 0.0,
                    "bb_pos_norm": -0.02,
                    "bb_width_norm": 0.01,
                    "bb_break": "NONE",
                    "bb_trend": "MID",
                    "bb_squeeze": False,
                },
            },
        }
    )

    assert regime_info["ev_direction"] == "LONG_ONLY"
    assert regime_info["guide_direction"] == "BOTH"
    assert regime_info["direction"] == "BOTH"
    assert regime_info["guide_score_source"] == "macd_bb_only"


def test_direction_guide_snapshot_defaults_to_macd_bb():
    engine = FundFlowDecisionEngine(_cfg())
    snapshot = engine.get_direction_guide_snapshot()

    assert snapshot["model"] == "MACD_BB"
    assert snapshot["enhanced_fallback_enabled"] is False


def test_detect_regime_does_not_keep_long_only_on_monotonic_15m_selloff():
    engine = FundFlowDecisionEngine(_cfg())

    regime_info = engine._detect_regime(
        {
            "cvd_momentum": 0.12,
            "imbalance": 0.18,
            "timeframes": {
                "15m": {
                    "adx": 40.0,
                    "atr_pct": 0.008,
                    "ema_fast": 1.80,
                    "ema_slow": 1.77,
                    "last_open": 1.826,
                    "last_close": 1.819,
                    "macd_hist": 0.0010,
                    "macd_hist_delta": -0.0003,
                    "bb_upper": 1.84,
                    "bb_lower": 1.76,
                    "bb_middle": 1.80,
                    "bb_break": "NONE",
                    "bb_trend": "MID",
                    "bb_squeeze": False,
                    "close_series": [1.835, 1.831, 1.826, 1.819],
                    "macd_hist_series": [0.0018, 0.0015, 0.0012, 0.0010],
                },
                "5m": {},
                "1h": {},
            },
        }
    )

    assert regime_info["guide_direction"] == "SHORT_ONLY"
    assert regime_info["direction"] == "SHORT_ONLY"
    assert regime_info["final"]["method"] == "price_action_override"


def test_detect_regime_flips_short_one_bar_earlier_on_peak_reversal_selloff():
    engine = FundFlowDecisionEngine(_cfg())

    regime_info = engine._detect_regime(
        {
            "cvd_momentum": 0.10,
            "imbalance": 0.12,
            "timeframes": {
                "15m": {
                    "adx": 40.0,
                    "atr_pct": 0.008,
                    "ema_fast": 1.80,
                    "ema_slow": 1.77,
                    "last_open": 1.820,
                    "last_close": 1.820,
                    "macd_hist": 0.0010,
                    "macd_hist_delta": -0.0002,
                    "bb_upper": 1.84,
                    "bb_lower": 1.76,
                    "bb_middle": 1.80,
                    "bb_break": "NONE",
                    "bb_trend": "MID",
                    "bb_squeeze": False,
                    "close_series": [1.821, 1.825, 1.820],
                    "macd_hist_series": [0.0014, 0.0012, 0.0010],
                },
                "5m": {},
                "1h": {},
            },
        }
    )

    assert regime_info["guide_direction"] == "SHORT_ONLY"
    assert regime_info["direction"] == "SHORT_ONLY"
    assert regime_info["final"]["method"] == "price_action_override"
    assert "peak_reversal_selloff" in regime_info["price_action_override"]["reason"]


def test_resolve_entry_mode_blocks_long_when_symbol_override_is_short_only():
    cfg = _cfg()
    cfg["fund_flow"]["symbol_side_overrides"] = {"APTUSDT": "SHORT_ONLY"}
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="APTUSDT",
        regime_info={
            "regime": "TREND",
            "guide_direction": "LONG_ONLY",
            "allow_entry_window": True,
            "flow_confirm": 0.6,
            "consistency_3bars": 1,
            "cvd_norm": 0.25,
            "combo_compare": {"feature_snapshot": {"macd_hist_sign": 1, "kdj_cross_sign": 1}},
        },
        base_scores={"long_score": 0.12, "short_score": 0.01},
        trend_pending={"trend_pending_side": "LONG", "trend_pending_score": 0.6},
        trend_capture={
            "trend_capture_score_long": 0.12,
            "trend_capture_score_short": 0.0,
            "trend_capture_side": "LONG",
            "trend_capture_breakout_long": True,
            "trend_capture_pullback_resume_long": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": False,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.HOLD
    assert resolved.metadata["symbol_side_override_mode"] == "SHORT_ONLY"
    assert resolved.metadata["symbol_side_override_allowed"] is False
    assert resolved.metadata["blocked_operation"] == Operation.BUY.value


def test_decide_blocks_range_long_when_symbol_override_is_short_only(monkeypatch):
    cfg = _cfg()
    cfg["fund_flow"]["symbol_side_overrides"] = {"SUIUSDT": "SHORT_ONLY"}
    engine = FundFlowDecisionEngine(cfg)

    monkeypatch.setattr(engine, "_detect_regime", lambda _ctx: {"regime": "RANGE", "direction": "BOTH", "reason": "test"})
    monkeypatch.setattr(engine, "_compute_trend_pending", lambda *args, **kwargs: {"trend_pending_side": "NONE", "trend_pending_score": 0.0})
    monkeypatch.setattr(
        engine,
        "_engine_params_for",
        lambda _regime: {
            "default_leverage": 2,
            "default_target_portion": 0.2,
            "long_open_threshold": 0.07,
            "short_open_threshold": 0.07,
            "close_threshold": 0.3,
        },
    )
    monkeypatch.setattr(
        engine,
        "_extract_range_quantiles",
        lambda _ctx: {
            "ready": True,
            "imb_hi": 0.4,
            "imb_lo": -0.4,
            "cvd_hi": 0.3,
            "cvd_lo": -0.3,
            "trap_guard_enabled": False,
            "n": 64,
        },
    )
    monkeypatch.setattr(engine, "_extract_15m_context", lambda _ctx: {})
    monkeypatch.setattr(engine, "_extract_5m_context", lambda _ctx: {})
    monkeypatch.setattr(engine, "_score_range", lambda _ctx: {"long_score": 0.6, "short_score": 0.1})
    monkeypatch.setattr(engine, "_score_trend", lambda _ctx: {"long_score": 0.0, "short_score": 0.0})
    monkeypatch.setattr(
        engine,
        "_fuse_scores",
        lambda *_args, **_kwargs: {"long_score": 0.6, "short_score": 0.1, "fusion_applied": False},
    )
    monkeypatch.setattr(engine, "_record_15m_score", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_compute_flow_consistency", lambda *_args, **_kwargs: (0.0, 0))
    monkeypatch.setattr(engine, "_compute_trend_capture", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(engine, "_compute_entry_confluence_v2", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(engine, "_compute_range_veto_by_trend", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(engine, "_extract_range_turn_values", lambda _ctx: {})
    monkeypatch.setattr(
        engine,
        "_evaluate_range_turn_confirm",
        lambda _vals: {
            "turned_up": True,
            "turned_down": False,
            "pass_count_long": 2,
            "pass_count_short": 0,
            "min_pass_count": 2,
            "mode": "1bar",
            "ready": True,
        },
    )

    decision = engine.decide(
        symbol="SUIUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context={
            "imbalance": -0.5,
            "cvd_momentum": -0.4,
            "oi_delta_ratio": 0.0,
        },
        use_weight_router=False,
        use_ai_weights=False,
    )

    assert decision.operation == Operation.HOLD
    assert decision.reason == "macd_mtf_missing_timeframes"


def test_resolve_entry_mode_blocks_entry_when_feature_snapshot_is_all_zero():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="BTCUSDT",
        regime_info={
            "regime": "TREND",
            "guide_direction": "LONG_ONLY",
            "allow_entry_window": True,
            "flow_confirm": 1.0,
            "consistency_3bars": 2,
            "cvd_norm": 0.35,
            "combo_compare": {
                "feature_snapshot": {
                    "macd_hist_sign": 0,
                    "macd_cross_sign": 0,
                    "kdj_cross_sign": 0,
                    "kdj_zone_sign": 0,
                }
            },
        },
        base_scores={"long_score": 0.12, "short_score": 0.0},
        trend_pending={"trend_pending_side": "LONG", "trend_pending_score": 0.6},
        trend_capture={
            "trend_capture_score_long": 0.12,
            "trend_capture_score_short": 0.0,
            "trend_capture_breakout_long": True,
            "trend_capture_pullback_resume_long": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": False,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.HOLD
    assert resolved.metadata["decision_source"] == "entry_hard_filter_blocked"
    assert resolved.metadata["entry_hard_filter_blocked"] is True
    assert "feature_snapshot_all_zero" in resolved.metadata["entry_hard_filters"]


def test_resolve_entry_mode_allows_primary_flat_ma10_fallback_when_feature_snapshot_is_all_zero():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="BTCUSDT",
        regime_info={
            "regime": "TREND",
            "guide_direction": "LONG_ONLY",
            "allow_entry_window": True,
            "flow_confirm": 1.0,
            "consistency_3bars": 2,
            "cvd_norm": 0.35,
            "combo_compare": {
                "feature_snapshot": {
                    "macd_hist_sign": 0,
                    "macd_cross_sign": 0,
                    "kdj_cross_sign": 0,
                    "kdj_zone_sign": 0,
                }
            },
            "lw": {"components": {"primary_flat": True, "backup_source": "ma10_macd_confluence_5m"}},
            "ev": {"components": {"primary_flat": True, "backup_source": "ma10_macd_confluence_5m"}},
        },
        base_scores={"long_score": 0.12, "short_score": 0.0},
        trend_pending={"trend_pending_side": "LONG", "trend_pending_score": 0.6},
        trend_capture={
            "trend_capture_score_long": 0.12,
            "trend_capture_score_short": 0.0,
            "trend_capture_breakout_long": True,
            "trend_capture_pullback_resume_long": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": False,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.BUY
    assert resolved.metadata["entry_feature_snapshot_all_zero"] is True
    assert resolved.metadata["entry_feature_snapshot_zero_soft_bypass"] is True
    assert "feature_snapshot_all_zero" not in resolved.metadata["entry_hard_filters"]


def test_macd_v2_hold_metadata_surfaces_structured_reject_and_regime_fallback_fields():
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "macd_mtf_strategy_v2"
    cfg["fund_flow"]["macd_mtf_strategy_v2"] = {
        "entry_filters": {
            "min_signal_score": 0.85,
        }
    }
    engine = FundFlowDecisionEngine(cfg)
    engine._detect_regime = lambda *_args, **_kwargs: {
        "regime": "TREND",
        "direction": "LONG_ONLY",
        "guide_direction": "LONG_ONLY",
        "adx": 25.0,
        "atr_pct": 0.01,
        "last_open": 100.0,
        "last_close": 101.0,
    }

    fake_signal = MACDSignalV2(
        direction="neutral",
        signal_score=0.83,
        signal_type_1h="red_bar_growing",
        is_4h_enhanced=False,
        enhancement_score=0.0,
        entry_type_15m="pullback",
        entry_score_15m=0.22,
        vwap_score=0.11,
        vwap_deviation=0.005,
        vwap_state="long_dual_support",
        vwap_location_score=0.0,
        ema_multiplier=1.0,
        ema_structure_status="normal",
        details={
            "reason": "pocket_min_entry_score_block",
            "reject_reason_code": "pocket_min_entry_score_block",
            "reject_stage": "pocket_entry_requirements",
            "pocket_entry_override_label": "ld_support_e2_cvd_vwap_score",
            "signal_score_threshold_used": 0.84,
            "min_vwap_score_used": 0.12,
            "min_entry_score_used": 0.35,
            "direction_lock_applied": True,
            "entry_hard_filter_blocked": True,
            "entry_hard_filters": ["pending_side_short_blocks_long"],
            "regime_fallback_allowed": False,
            "regime_fallback_score": 0.61,
            "stable_continuation_active": False,
        },
    )

    class _FakeMacdEngine:
        def analyze(self, **_kwargs):
            return fake_signal

        def resolve_4h_shrink_exit_policy(self, **_kwargs):
            return {"active": False}

    engine._macd_v2_engine_for_symbol = lambda _symbol: (_FakeMacdEngine(), None)
    engine._build_macd_v2_4h_regime_state = lambda **_kwargs: {
        "state": "neutral",
        "phase": "neutral",
        "side": "neutral",
        "score": 0.61,
        "entry_allowed": False,
        "entry_scale": 0.35,
    }

    decision = engine.decide(
        symbol="TRUMPUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context={
            "timeframes": {
                "15m": {"timestamp": 1, "close": 100.0, "volume": 10.0, "avg_volume": 10.0},
                "1h": {"timestamp": 1, "close": 100.0, "vwap": 99.8, "structural_vwap": 99.7, "atr": 1.0},
                "4h": {"timestamp": 1, "close": 100.0},
            }
        },
        trigger_context={"trigger_type": "signal"},
        use_weight_router=False,
        use_ai_weights=False,
    )

    assert decision.operation == Operation.HOLD
    assert decision.metadata["reject_reason_code"] == "pocket_min_entry_score_block"
    assert decision.metadata["reject_stage"] == "pocket_entry_requirements"
    assert decision.metadata["pocket_entry_override_label"] == "ld_support_e2_cvd_vwap_score"
    assert decision.metadata["signal_score_threshold_used"] == 0.84
    assert decision.metadata["min_vwap_score_used"] == 0.12
    assert decision.metadata["min_entry_score_used"] == 0.35
    assert decision.metadata["direction_lock_applied"] is True
    assert decision.metadata["entry_hard_filter_blocked"] is True
    assert decision.metadata["entry_hard_filters"] == ["pending_side_short_blocks_long"]
    assert decision.metadata["regime_fallback_allowed"] is False
    assert decision.metadata["regime_fallback_score"] == 0.61


def test_resolve_entry_mode_blocks_long_when_pending_side_is_short():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="ETHUSDT",
        regime_info={
            "regime": "TREND",
            "guide_direction": "LONG_ONLY",
            "allow_entry_window": True,
            "flow_confirm": 1.0,
            "consistency_3bars": 2,
            "cvd_norm": 0.28,
            "combo_compare": {"feature_snapshot": {"macd_hist_sign": 1, "kdj_cross_sign": 1}},
        },
        base_scores={"long_score": 0.12, "short_score": 0.0},
        trend_pending={"trend_pending_side": "SHORT", "trend_pending_score": 0.6},
        trend_capture={
            "trend_capture_score_long": 0.12,
            "trend_capture_score_short": 0.0,
            "trend_capture_breakout_long": True,
            "trend_capture_pullback_resume_long": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": False,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.HOLD
    assert resolved.metadata["decision_source"] == "entry_hard_filter_blocked"
    assert "pending_side_short_blocks_long" in resolved.metadata["entry_hard_filters"]


def test_resolve_entry_mode_blocks_long_when_cvd_norm_is_non_positive():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "required_long_cvd_norm": 0.12,
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="SOLUSDT",
        regime_info={
            "regime": "TREND",
            "guide_direction": "LONG_ONLY",
            "allow_entry_window": True,
            "flow_confirm": 1.0,
            "consistency_3bars": 2,
            "cvd_norm": 0.0,
            "combo_compare": {"feature_snapshot": {"macd_hist_sign": 1, "kdj_cross_sign": 1}},
        },
        base_scores={"long_score": 0.12, "short_score": 0.0},
        trend_pending={"trend_pending_side": "LONG", "trend_pending_score": 0.6},
        trend_capture={
            "trend_capture_score_long": 0.12,
            "trend_capture_score_short": 0.0,
            "trend_capture_breakout_long": True,
            "trend_capture_pullback_resume_long": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": False,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.HOLD
    assert resolved.metadata["decision_source"] == "entry_hard_filter_blocked"
    assert "cvd_norm_below_required_long" in resolved.metadata["entry_hard_filters"]


def test_resolve_entry_mode_blocks_entry_without_breakout_or_pullback():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="BNBUSDT",
        regime_info={
            "regime": "TREND",
            "guide_direction": "LONG_ONLY",
            "allow_entry_window": True,
            "flow_confirm": 1.0,
            "consistency_3bars": 2,
            "cvd_norm": 0.31,
            "combo_compare": {"feature_snapshot": {"macd_hist_sign": 1, "kdj_cross_sign": 1}},
        },
        base_scores={"long_score": 0.12, "short_score": 0.0},
        trend_pending={"trend_pending_side": "LONG", "trend_pending_score": 0.6},
        trend_capture={
            "trend_capture_score_long": 0.12,
            "trend_capture_score_short": 0.0,
            "trend_capture_breakout_long": False,
            "trend_capture_pullback_resume_long": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": False,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.HOLD
    assert resolved.metadata["decision_source"] == "entry_hard_filter_blocked"
    assert "no_breakout_no_pullback_long" in resolved.metadata["entry_hard_filters"]


def test_resolve_entry_mode_blocks_long_when_strict_trend_requirements_fail():
    cfg = _cfg()
    cfg["fund_flow"]["long_open_threshold"] = 0.07
    cfg["fund_flow"]["trend_capture"] = {
        "trend_only_mode": True,
        "required_flow_confirm": 1.0,
        "required_long_cvd_norm": 0.12,
        "required_price_oi_alignment_15m": 1.0,
        "required_adx_slope": 0.05,
        "required_long_ema_spread_expand": 0.0,
        "min_score": 0.08,
        "min_gap": 0.02,
        "base_score_floor_mult": 0.85,
    }
    engine = FundFlowDecisionEngine(cfg)
    resolved = engine._resolve_entry_mode(
        symbol="BTCUSDT",
        regime_info={
            "regime": "TREND",
            "flow_confirm": 0.5,
            "cvd_norm": 0.10,
            "combo_compare": {"feature_snapshot": {"macd_hist_sign": 1, "kdj_cross_sign": 1}},
        },
        base_scores={"long_score": 0.15, "short_score": 0.0},
        trend_pending={
            "trend_pending_side": "LONG",
            "trend_pending_score": 0.8,
            "trend_pending_price_oi_align": 0.0,
            "trend_pending_adx_slope": 0.01,
            "trend_pending_ema_spread_expand": 0.0,
        },
        trend_capture={
            "trend_capture_score_long": 0.2,
            "trend_capture_score_short": 0.0,
            "trend_capture_breakout_long": True,
            "trend_capture_pullback_resume_long": False,
        },
        confluence={
            "confluence_soft_penalty_long": 0.0,
            "confluence_soft_penalty_short": 0.0,
            "confluence_hard_block_long": False,
            "confluence_hard_block_short": False,
        },
        range_veto={},
        cfg=engine._trend_capture_config(),
    )
    assert resolved.operation == Operation.HOLD
    assert "flow_confirm_below_required" in resolved.metadata["entry_hard_filters"]
    assert "cvd_norm_below_required_long" in resolved.metadata["entry_hard_filters"]
    assert "price_oi_alignment_below_required" in resolved.metadata["entry_hard_filters"]
    assert "adx_slope_below_required" in resolved.metadata["entry_hard_filters"]
    assert "ema_spread_expand_not_positive_long" in resolved.metadata["entry_hard_filters"]


def test_decide_uses_dynamic_short_term_stop_loss_for_trend_entries():
    cfg = _cfg()
    cfg["fund_flow"].update(
        {
            "default_target_portion": 0.08,
            "long_open_threshold": 0.07,
            "default_leverage": 5,
            "min_leverage": 4,
            "max_leverage": 5,
            "engine_params": {
                "TREND": {
                    "default_target_portion": 0.08,
                    "default_leverage": 5,
                    "min_leverage": 4,
                    "max_leverage": 5,
                    "long_open_threshold": 0.07,
                    "dynamic_stop_loss_enabled": True,
                    "short_stop_loss_min_pct": 0.0035,
                    "short_stop_loss_max_pct": 0.0045,
                    "short_stop_loss_atr_mult": 1.2,
                    "take_profit_pct_levels": [0.0025, 0.0055],
                    "take_profit_reduce_pct_levels": [0.4, 0.3],
                }
            },
            "trend_capture": {
                "trend_only_mode": True,
                "required_flow_confirm": 1.0,
                "required_long_cvd_norm": 0.12,
                "required_price_oi_alignment_15m": 1.0,
                "required_adx_slope": 0.05,
                "required_long_ema_spread_expand": 0.0,
                "min_score": 0.08,
                "min_gap": 0.02,
                "base_score_floor_mult": 0.85,
            },
        }
    )
    engine = FundFlowDecisionEngine(cfg)
    engine._detect_regime = lambda *_args, **_kwargs: {
        "regime": "TREND",
        "direction": "LONG_ONLY",
        "guide_direction": "LONG_ONLY",
        "adx": 25.0,
        "atr_pct": 0.002,
        "cvd_norm": 0.2,
        "combo_compare": {"feature_snapshot": {"macd_hist_sign": 1, "kdj_cross_sign": 1}},
    }
    engine._compute_trend_pending = lambda *_args, **_kwargs: {
        "trend_pending_side": "LONG",
        "trend_pending_score": 0.8,
        "trend_pending_price_oi_align": 1.0,
        "trend_pending_adx_slope": 0.08,
        "trend_pending_ema_spread": 1.0,
        "trend_pending_ema_spread_expand": 0.01,
    }
    engine._compute_flow_consistency = lambda *_args, **_kwargs: (1.0, 2)
    engine._compute_trend_capture = lambda *_args, **_kwargs: {
        "trend_capture_side": "LONG",
        "trend_capture_score_long": 0.43,
        "trend_capture_score_short": 0.0,
        "trend_capture_breakout_long": True,
        "trend_capture_breakout_short": False,
        "trend_capture_pullback_resume_long": False,
        "trend_capture_pullback_resume_short": False,
    }
    engine._compute_entry_confluence_v2 = lambda *_args, **_kwargs: {
        "confluence_side": "LONG",
        "confluence_hard_block_long": False,
        "confluence_hard_block_short": False,
        "confluence_soft_penalty_long": 0.0,
        "confluence_soft_penalty_short": 0.0,
    }
    engine._compute_range_veto_by_trend = lambda *_args, **_kwargs: {}
    decision = engine.decide(
        symbol="BTCUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context={
            "timeframes": {
                "15m": {
                    "adx": 25.0,
                    "atr_pct": 0.002,
                    "ema_fast": 101.0,
                    "ema_slow": 100.0,
                    "ret_period": 0.01,
                    "oi_delta_ratio": 0.02,
                },
                "5m": {
                    "close": 105.0,
                    "hh_n": 105.0,
                    "ll_n": 100.0,
                    "ema_fast": 104.0,
                    "ema_slow": 102.0,
                    "ret_period": 0.01,
                    "cvd_momentum": 0.02,
                    "oi_delta_ratio": 0.02,
                    "depth_ratio": 1.02,
                    "imbalance": 0.03,
                },
                "3m": {
                    "ret_period": 0.02,
                },
            },
            "fund_flow_features": {"15m": {"oi_delta_ratio": 0.02}, "5m": {"oi_delta_ratio": 0.02}},
            "microstructure_features": {"micro_delta": 0.02, "microprice_bias": 0.02},
        },
        trigger_context={"trigger_type": "signal"},
        use_weight_router=False,
        use_ai_weights=False,
    )
    assert decision.operation == Operation.HOLD
    assert decision.reason == "macd_mtf_missing_tf_data"


def _rule_cfg():
    cfg = _cfg()
    cfg["fund_flow"]["strategy_mode"] = "ema10_ema30_1h_15m_rule"
    cfg["fund_flow"]["decision_timeframe"] = "15m"
    cfg["fund_flow"]["rule_strategy"] = {
        "enabled": True,
        "primary_trend_timeframe": "1h",
        "entry_timeframe": "15m",
        "min_stop_pct": 0.02,
        "max_stop_pct": 0.05,
        "tp1_min_pct": 0.05,
        "tp1_max_pct": 0.10,
        "runner_activate_pct": 0.05,
        "tp1_reduce_pct": 0.5,
        "stop_break_buffer_pct": 0.0,
    }
    return cfg


def _rule_entry_context(*, direction: str, overrides=None):
    entry_tf = {
        "last_open": 100.0,
        "last_close": 101.0 if direction == "LONG_ONLY" else 99.0,
        "ema_10": 101.0 if direction == "LONG_ONLY" else 99.0,
        "ema_30": 100.0,
        "ema_cross": "GOLDEN" if direction == "LONG_ONLY" else "DEAD",
        "macd_cross": "GOLDEN" if direction == "LONG_ONLY" else "DEAD",
        "macd_zone": "ABOVE_ZERO" if direction == "LONG_ONLY" else "BELOW_ZERO",
        "macd_hist": 0.2 if direction == "LONG_ONLY" else -0.2,
        "macd_hist_expand_up": direction == "LONG_ONLY",
        "macd_hist_expand_down": direction == "SHORT_ONLY",
        "bb_middle": 100.0,
        "bb_upper": 101.2,
        "bb_lower": 98.8,
        "bb_break": "NONE",
        "bb_width_expand": False,
    }
    if isinstance(overrides, dict):
        entry_tf.update(overrides)
    return {"timeframes": {"15m": entry_tf}}


def test_rule_entry_confluence_macd_long_requires_ema_cross():
    engine = FundFlowDecisionEngine(_rule_cfg())
    confluence = engine._rule_entry_confluence(
        _rule_entry_context(direction="LONG_ONLY", overrides={"ema_cross": "NONE"}),
        {"direction": "LONG_ONLY"},
    )
    assert confluence["long_ok"] is False
    assert confluence["long_models"] == []



def test_rule_entry_confluence_bollinger_long_allows_without_ema_cross():
    engine = FundFlowDecisionEngine(_rule_cfg())
    confluence = engine._rule_entry_confluence(
        _rule_entry_context(
            direction="LONG_ONLY",
            overrides={
                "ema_cross": "NONE",
                "macd_cross": "NONE",
                "macd_hist_expand_up": False,
                "bb_break": "UPPER",
                "bb_width_expand": True,
            },
        ),
        {"direction": "LONG_ONLY"},
    )
    assert confluence["long_ok"] is True
    assert confluence["long_models"] == ["EMA_BB"]



def test_rule_entry_confluence_macd_short_requires_ema_cross():
    engine = FundFlowDecisionEngine(_rule_cfg())
    confluence = engine._rule_entry_confluence(
        _rule_entry_context(direction="SHORT_ONLY", overrides={"ema_cross": "NONE"}),
        {"direction": "SHORT_ONLY"},
    )
    assert confluence["short_ok"] is False
    assert confluence["short_models"] == []


def test_rule_entry_confluence_blocks_long_when_4h_macd_risk_is_bearish():
    cfg = _rule_cfg()
    cfg["dual_timeframe"] = {
        "enabled": True,
        "risk_filter": {
            "enable_4h_macd": True,
            "block_on_4h_divergence": True,
        },
    }
    engine = FundFlowDecisionEngine(cfg)
    ctx = _rule_entry_context(direction="LONG_ONLY")
    ctx["timeframes"]["4h"] = {
        "last_open": 101.0,
        "last_close": 99.0,
        "macd_cross": "DEAD",
        "macd_zone": "BELOW_ZERO",
        "macd_hist": -0.15,
        "macd_hist_delta": -0.04,
        "macd_hist_expand_up": False,
        "macd_hist_expand_down": True,
    }
    confluence = engine._rule_entry_confluence(ctx, {"direction": "LONG_ONLY"})
    assert confluence["long_ok"] is False
    assert confluence["long_models"] == []
    assert confluence["risk_filter_allow_long"] is False
    assert "4h_macd_risk_block" in confluence["reason"]


def test_rule_entry_confluence_blocks_short_when_4h_macd_risk_is_bullish():
    cfg = _rule_cfg()
    cfg["dual_timeframe"] = {
        "enabled": True,
        "risk_filter": {
            "enable_4h_macd": True,
            "block_on_4h_divergence": True,
        },
    }
    engine = FundFlowDecisionEngine(cfg)
    ctx = _rule_entry_context(direction="SHORT_ONLY")
    ctx["timeframes"]["4h"] = {
        "last_open": 99.0,
        "last_close": 101.0,
        "macd_cross": "GOLDEN",
        "macd_zone": "ABOVE_ZERO",
        "macd_hist": 0.15,
        "macd_hist_delta": 0.04,
        "macd_hist_expand_up": True,
        "macd_hist_expand_down": False,
    }
    confluence = engine._rule_entry_confluence(ctx, {"direction": "SHORT_ONLY"})
    assert confluence["short_ok"] is False
    assert confluence["short_models"] == []
    assert confluence["risk_filter_allow_short"] is False
    assert "4h_macd_risk_block" in confluence["reason"]


def test_rule_entry_confluence_keeps_entry_when_4h_risk_context_is_missing():
    cfg = _rule_cfg()
    cfg["dual_timeframe"] = {
        "enabled": True,
        "risk_filter": {
            "enable_4h_macd": True,
        },
    }
    engine = FundFlowDecisionEngine(cfg)
    confluence = engine._rule_entry_confluence(
        _rule_entry_context(direction="LONG_ONLY"),
        {"direction": "LONG_ONLY"},
    )
    assert confluence["long_ok"] is True
    assert confluence["risk_filter_allow_long"] is True
    assert confluence["risk_filter_reason"] == "missing_4h_context"


def test_rule_risk_plan_respects_2_to_5_pct_stop_band():
    engine = FundFlowDecisionEngine(_rule_cfg())
    risk_plan = engine._rule_build_risk_plan(
        direction="LONG",
        entry_price=100.0,
        stop_anchor=99.8,
        entry_models=["EMA_MACD"],
    )
    assert risk_plan["valid"] is True
    assert round((100.0 - float(risk_plan["stop_trigger_price"])) / 100.0, 4) == 0.02

    too_wide = engine._rule_build_risk_plan(
        direction="LONG",
        entry_price=100.0,
        stop_anchor=94.0,
        entry_models=["EMA_MACD"],
    )
    assert too_wide["valid"] is False
    assert "stop_too_wide" in too_wide["reason"]


def test_rule_stop_trigger_long_stops_on_macd_dead_cross_before_ema30_break():
    engine = FundFlowDecisionEngine(_rule_cfg())
    stop_state = engine._rule_stop_trigger(
        "LONG",
        _rule_entry_context(
            direction="LONG_ONLY",
            overrides={
                "last_close": 101.0,
                "ema_10": 102.0,
                "ema_30": 100.0,
                "macd_cross": "DEAD",
                "macd_zone": "ABOVE_ZERO",
            },
        ),
        fallback_price=101.0,
        current_pos={"entry_price": 100.0},
    )
    assert stop_state["triggered"] is True
    assert stop_state["runner_active"] is False
    assert stop_state["exit_trigger"] == "macd_dead_cross"


def test_rule_stop_trigger_short_stops_on_below_zero_golden_cross_before_ema30_break():
    engine = FundFlowDecisionEngine(_rule_cfg())
    stop_state = engine._rule_stop_trigger(
        "SHORT",
        _rule_entry_context(
            direction="SHORT_ONLY",
            overrides={
                "last_close": 99.0,
                "ema_10": 98.5,
                "ema_30": 100.0,
                "macd_cross": "GOLDEN",
                "macd_zone": "BELOW_ZERO",
            },
        ),
        fallback_price=99.0,
        current_pos={"entry_price": 100.0},
    )
    assert stop_state["triggered"] is True
    assert stop_state["runner_active"] is False
    assert stop_state["exit_trigger"] == "macd_golden_cross_below_zero"


def test_rule_stop_trigger_long_runner_exits_on_ema10_break():
    engine = FundFlowDecisionEngine(_rule_cfg())
    stop_state = engine._rule_stop_trigger(
        "LONG",
        _rule_entry_context(
            direction="LONG_ONLY",
            overrides={
                "last_close": 106.0,
                "ema_10": 107.0,
                "ema_30": 100.0,
                "macd_cross": "NONE",
            },
        ),
        fallback_price=106.0,
        current_pos={"entry_price": 100.0},
    )
    assert stop_state["triggered"] is True
    assert stop_state["runner_active"] is True
    assert stop_state["exit_stage"] == "RUNNER"
    assert stop_state["exit_trigger"] == "ema10_break"


def test_rule_stop_trigger_short_runner_exits_on_below_zero_golden_cross():
    engine = FundFlowDecisionEngine(_rule_cfg())
    stop_state = engine._rule_stop_trigger(
        "SHORT",
        _rule_entry_context(
            direction="SHORT_ONLY",
            overrides={
                "last_close": 94.0,
                "ema_10": 95.0,
                "ema_30": 100.0,
                "macd_cross": "GOLDEN",
                "macd_zone": "BELOW_ZERO",
            },
        ),
        fallback_price=94.0,
        current_pos={"entry_price": 100.0},
    )
    assert stop_state["triggered"] is True
    assert stop_state["runner_active"] is True
    assert stop_state["exit_stage"] == "RUNNER"
    assert stop_state["exit_trigger"] == "macd_golden_cross_below_zero"

