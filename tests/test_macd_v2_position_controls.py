from types import SimpleNamespace

from src.fund_flow.decision_engine import FundFlowDecisionEngine
from src.fund_flow.models import Operation


class _StubMacdV2Engine:
    def __init__(self, signal):
        self._signal = signal

    def analyze(self, **_kwargs):
        return self._signal

    def resolve_4h_shrink_exit_policy(self, **_kwargs):
        return {"active": False}

    def calculate_leverage(self, *_args, **_kwargs):
        return 2

    def resolve_session_position_scale(self, *_args, **_kwargs):
        return 1.0

    def calculate_position_portion(self, **_kwargs):
        return 0.2

    def is_watchlist_symbol(self, _symbol):
        return False

    def resolve_symbol_risk_session_scale(self, _symbol, session_scale):
        return session_scale


class _StubMacdV2ShrinkEngine(_StubMacdV2Engine):
    def resolve_4h_shrink_exit_policy(self, **_kwargs):
        return {
            "active": True,
            "shrink_exit_direction": "long",
            "mode": "default",
            "required_bars": 2,
            "required_pct": 0.2,
            "shrink_bars": 2,
            "shrink_pct": 0.3,
            "stable_continuation_active": False,
        }


def _cfg():
    return {
        "trading": {"default_leverage": 2},
        "risk": {"max_position_pct": 0.2},
        "fund_flow": {
            "default_target_portion": 0.2,
            "close_threshold": 0.3,
            "entry_slippage": 0.001,
            "deepseek_weight_router": {"enabled": False},
            "strategy_mode": "macd_mtf_strategy_v2",
            "entry_hard_gates_enabled": True,
            "entry_hard_gate_adx_min": 22,
            "entry_hard_gate_atr_min": 0.006,
            "entry_hard_gate_atr_max": 0.020,
            "entry_hard_gate_spread_bps_max": 0.0008,
            "entry_hard_gate_skip_spread_if_missing": False,
            "entry_hard_gate_flow_min_pass": 2,
            "entry_hard_gate_micro_min_pass": 2,
            "fast_exit_enabled": True,
            "fast_exit_direction_lock_bars": 2,
            "fast_exit_trap_high_confidence": 0.85,
            "macd_mtf_strategy_v2": {},
        },
    }


def _signal(direction: str = "long"):
    return SimpleNamespace(
        direction=direction,
        signal_score=0.92,
        signal_type_1h="flip_bullish" if direction == "long" else "flip_bearish",
        is_trial_entry=False,
        entry_scale=1.0,
        is_4h_enhanced=False,
        entry_type_15m="flip_bullish" if direction == "long" else "flip_bearish",
        vwap_score=0.18,
        vwap_deviation=0.004,
        vwap_state="above" if direction == "long" else "below",
        vwap_location_score=0.12,
        details={"reason": "stub"},
        ema_structure_status="strong",
        ema_multiplier=1.0,
        veto_type=None,
        suggested_stop_price=None,
        stop_loss_pct=0.012,
    )


def _context(*, spread_bps: float = 0.0002):
    tf_15m = {
        "timestamp": "2026-03-30T00:00:00Z",
        "macd_hist": 0.2,
        "macd_hist_prev": 0.1,
        "volume": 1000.0,
        "avg_volume": 500.0,
        "close": 100.0,
        "bb_middle": 99.0,
        "bb_upper": 102.0,
        "bb_lower": 97.0,
        "spread_bps": spread_bps,
        "depth_ratio": 1.03,
        "imbalance": 0.04,
        "cvd_ratio": 0.10,
        "cvd_momentum": 0.03,
        "upper_wick_ratio": 0.1,
    }
    tf_1h = {
        "timestamp": "2026-03-30T00:00:00Z",
        "macd_hist": 0.3,
        "macd_hist_prev": 0.2,
        "vwap": 99.5,
        "structural_vwap": 99.0,
        "close": 100.0,
        "bb_middle": 99.0,
        "bb_upper": 103.0,
        "bb_lower": 97.0,
        "atr": 0.8,
        "adx": 28.0,
        "oi_delta_ratio": 0.02,
        "cvd_delta_ratio": 0.03,
    }
    tf_4h = {
        "timestamp": "2026-03-30T00:00:00Z",
        "macd_hist": 0.4,
        "macd_hist_prev": 0.35,
        "bb_middle": 98.0,
        "bb_upper": 104.0,
        "bb_lower": 94.0,
        "adx": 30.0,
    }
    return {
        "timeframes": {"15m": tf_15m, "1h": tf_1h, "4h": tf_4h},
        "spread_bps": spread_bps,
        "depth_ratio": 1.03,
        "imbalance": 0.04,
        "cvd_ratio": 0.10,
        "cvd_momentum": 0.03,
        "oi_delta_ratio": 0.02,
    }


def test_macd_v2_entry_hard_gates_block_long_when_spread_too_wide():
    engine = FundFlowDecisionEngine(_cfg())
    engine.macd_v2_engine = _StubMacdV2Engine(_signal("long"))

    decision = engine._decide_macd_v2_strategy(
        symbol="BTCUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context=_context(spread_bps=0.0012),
        regime_info={"regime": "TREND", "direction": "LONG_ONLY", "adx": 28.0, "atr_pct": 0.01},
    )

    assert decision.operation == Operation.HOLD
    assert "entry_hard_gate" in decision.reason
    assert "spread_ok" in decision.metadata["entry_hard_gate_reason"]


def test_macd_v2_entry_hard_gates_skip_missing_spread_when_config_enabled():
    cfg = _cfg()
    cfg["fund_flow"]["entry_hard_gate_skip_spread_if_missing"] = True

    engine = FundFlowDecisionEngine(cfg)
    engine.macd_v2_engine = _StubMacdV2Engine(_signal("long"))

    flow_context = _context()
    flow_context["timeframes"]["15m"].pop("spread_bps", None)
    flow_context.pop("spread_bps", None)

    decision = engine._decide_macd_v2_strategy(
        symbol="BTCUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context=flow_context,
        regime_info={"regime": "TREND", "direction": "LONG_ONLY", "adx": 28.0, "atr_pct": 0.01},
    )

    assert decision.operation == Operation.BUY


def test_macd_v2_entry_hard_gates_skip_missing_l3_microstructure_and_mark_metadata():
    engine = FundFlowDecisionEngine(_cfg())
    engine.macd_v2_engine = _StubMacdV2Engine(_signal("long"))

    flow_context = _context()
    flow_context["timeframes"]["15m"].pop("depth_ratio", None)
    flow_context["timeframes"]["15m"].pop("imbalance", None)
    flow_context.pop("depth_ratio", None)
    flow_context.pop("imbalance", None)
    flow_context["cvd_momentum"] = 0.03
    flow_context["timeframes"]["15m"]["cvd_momentum"] = 0.03

    decision = engine._decide_macd_v2_strategy(
        symbol="BTCUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context=flow_context,
        regime_info={"regime": "TREND", "direction": "LONG_ONLY", "adx": 28.0, "atr_pct": 0.01},
    )

    assert decision.operation == Operation.BUY
    assert decision.metadata["entry_hard_gate_missing_reason"] == "L3_microstructure_missing:depth_ok,imbalance_ok"


def test_macd_v2_entry_hard_gates_fail_when_only_available_l3_check_fails():
    engine = FundFlowDecisionEngine(_cfg())
    engine.macd_v2_engine = _StubMacdV2Engine(_signal("long"))

    flow_context = _context()
    flow_context["timeframes"]["15m"].pop("depth_ratio", None)
    flow_context["timeframes"]["15m"].pop("imbalance", None)
    flow_context.pop("depth_ratio", None)
    flow_context.pop("imbalance", None)
    flow_context["cvd_momentum"] = -0.03
    flow_context["timeframes"]["15m"]["cvd_momentum"] = -0.03

    decision = engine._decide_macd_v2_strategy(
        symbol="BTCUSDT",
        portfolio={"positions": {}},
        price=100.0,
        market_flow_context=flow_context,
        regime_info={"regime": "TREND", "direction": "LONG_ONLY", "adx": 28.0, "atr_pct": 0.01},
    )

    assert decision.operation == Operation.HOLD
    assert decision.reason == "entry_hard_gate_block:L3_microstructure_failed:cvd_momentum_ok=0"
    assert decision.metadata["entry_hard_gate_missing_reason"] == "L3_microstructure_missing:depth_ok,imbalance_ok"


def test_macd_v2_live_path_supports_4h_shrink_exit_close():
    cfg = _cfg()
    engine = FundFlowDecisionEngine(cfg)
    engine.macd_v2_engine = _StubMacdV2ShrinkEngine(_signal("long"))
    engine.macd_v2_config = SimpleNamespace(
        enable_4h_shrink_exit=True,
        exit_4h_require_profit=False,
        exit_4h_weak_loss_threshold=-0.02,
    )

    neutral_signal = _signal("long")
    neutral_signal.direction = "neutral"
    neutral_signal.details = {
        "reason": "neutral_after_shrink",
        "macd_4h_shrink_pct": 0.3,
        "macd_4h_shrink_bars": 2,
        "shrink_exit_direction": "long",
        "shrink_exit_ready": True,
    }
    engine.macd_v2_engine._signal = neutral_signal

    decision = engine._decide_macd_v2_strategy(
        symbol="BTCUSDT",
        portfolio={"positions": {"BTCUSDT": {"side": "LONG", "entry_price": 100.0, "amount": 1.0}}},
        price=99.5,
        market_flow_context=_context(),
        regime_info={"regime": "TREND", "direction": "BOTH", "adx": 28.0, "atr_pct": 0.01},
    )

    assert decision.operation == Operation.CLOSE
    assert decision.reason == "macd_v2_4h_shrink_exit_long"
    assert decision.metadata["shrink_exit"]["ready"] is True


def test_macd_v2_fast_exit_closes_long_when_direction_lock_reverses():
    engine = FundFlowDecisionEngine(_cfg())
    engine.macd_v2_engine = _StubMacdV2Engine(_signal("neutral"))

    flow_context = _context()
    flow_context["direction_lock_confirmed_bars"] = 2

    decision = engine._decide_macd_v2_strategy(
        symbol="BTCUSDT",
        portfolio={"positions": {"BTCUSDT": {"side": "LONG", "entry_price": 100.0, "amount": 1.0}}},
        price=99.4,
        market_flow_context=flow_context,
        regime_info={"regime": "TREND", "direction": "SHORT_ONLY", "adx": 28.0, "atr_pct": 0.01},
    )

    assert decision.operation == Operation.CLOSE
    assert "fast_exit" in decision.reason
    assert decision.metadata["fast_exit_reason"] == "direction_lock_reversed"
