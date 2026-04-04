import time

import pytest

from src.app.fund_flow_bot import TradingBot
from src.fund_flow.models import FundFlowDecision, Operation as FundFlowOperation


def _make_bot():
    bot = TradingBot.__new__(TradingBot)
    bot.config = {
        "fund_flow": {
            "stop_loss_pct": 0.012,
            "time_exit_enabled": True,
            "time_exit_minutes": 30,
            "time_exit_min_profit_pct": 0.0035,
            "runner_time_exit_enabled": False,
            "runner_time_exit_minutes": 15,
            "runner_time_exit_min_completed_levels": 1,
            "partial_tp_enabled": True,
            "partial_tp_levels": [
                {"close_ratio": 0.30, "trigger_r_multiple": 1.0},
                {"close_ratio": 0.40, "trigger_r_multiple": 2.0},
            ],
            "trailing_stop_enabled": True,
            "trailing_stop_atr_multiplier": 1.0,
            "trailing_stop_min_distance": 0.007,
            "trailing_stop_max_distance": 0.015,
            "partial_tp_mode": "dynamic",
            "partial_tp_volatile": {
                "level_0": {"ratio": 0.45, "at_r": 0.7},
                "level_1": {"ratio": 0.30, "at_r": 1.5},
            },
            "partial_tp_trending": {
                "level_0": {"ratio": 0.15, "at_r": 1.5},
                "level_1": {"ratio": 0.20, "at_r": 3.0},
            },
            "trailing_stop_mode": "dynamic",
            "trailing_volatile": {
                "activation_pct": 0.008,
                "atr_multiplier": 0.6,
                "min_distance": 0.005,
                "max_distance": 0.010,
                "breakeven_trigger": 0.005,
                "breakeven_lock": 0.002,
            },
            "trailing_trending": {
                "activation_pct": 0.018,
                "atr_multiplier": 1.8,
                "min_distance": 0.012,
                "max_distance": 0.030,
                "breakeven_trigger": 0.012,
                "breakeven_lock": 0.003,
            },
            "atr_position_scale_enabled": True,
            "atr_position_scale_bands": [
                {"atr_pct_max": 0.014, "scale": 1.0},
                {"atr_pct_max": 0.018, "scale": 0.85},
                {"atr_pct_max": 0.022, "scale": 0.70},
                {"atr_pct_max": 0.025, "scale": 0.55},
            ],
            "pretrade_risk_gate": {
                "enabled": True,
                "use_hard_rules_only": True,
                "atr_ratio_hard_block": 0.03,
                "equity_usage_block": 0.95,
                "dd_exit_threshold": 0.1,
            },
        }
    }
    bot.logs_dir = "."
    bot.log_root_dir = "."
    bot._position_first_seen_ts = {}
    bot._position_last_direction_eval_ts = {}
    bot._position_extrema_by_pos = {}
    bot._protection_missing_since_ts = {}
    bot._protection_last_alert_ts = {}
    bot._pre_risk_exit_streak_by_pos = {}
    bot._partial_tp_state_by_pos = {}
    bot._exit_audit_events = []
    bot._risk_state_path = "NUL"
    bot._save_risk_state = lambda: None
    bot._append_exit_protection_audit_log = lambda payload: bot._exit_audit_events.append(payload)
    bot._open_protection_orders = lambda *args, **kwargs: []
    bot._get_existing_sl_price = lambda _orders: 0.0
    bot._is_new_sl_tighter = lambda *_args, **_kwargs: True
    bot.client = type(
        "_Client",
        (),
        {
            "_execute_protection_v2": staticmethod(lambda **_kwargs: {"status": "success"}),
            "cancel_order": staticmethod(lambda *_args, **_kwargs: None),
        },
    )()
    return bot


def _position(side="LONG", entry_price=100.0):
    return {"side": side, "entry_price": entry_price, "amount": 1.0}


def _decision(reason="hold"):
    return FundFlowDecision(
        operation=FundFlowOperation.HOLD,
        symbol="BTCUSDT",
        target_portion_of_balance=0.0,
        leverage=2,
        reason=reason,
        metadata={},
    )


def test_time_exit_closes_position_after_hold_window_without_progress():
    bot = _make_bot()
    pos_key = bot._position_track_key("BTCUSDT", "LONG")
    bot._position_first_seen_ts[pos_key] = time.time() - (31 * 60)

    decision = bot._evaluate_time_exit(
        symbol="BTCUSDT",
        position=_position(),
        current_price=100.2,
        flow_context={"cvd_momentum": 0.0},
        base_decision=_decision(),
    )

    assert decision is not None
    assert decision.operation == FundFlowOperation.CLOSE
    assert "time_exit" in decision.reason


def test_time_exit_uses_injected_backtest_clock_instead_of_wall_clock():
    bot = _make_bot()
    pos_key = bot._position_track_key("BTCUSDT", "LONG")
    bot._position_first_seen_ts[pos_key] = 1000.0
    bot._backtest_now_ts = 1000.0 + (5 * 60)

    decision = bot._evaluate_time_exit(
        symbol="BTCUSDT",
        position=_position(),
        current_price=99.8,
        flow_context={"cvd_momentum": 0.0},
        base_decision=_decision(),
    )

    assert decision is None


def test_runner_time_exit_closes_partial_runner_after_stall_window():
    bot = _make_bot()
    bot.config["fund_flow"]["runner_time_exit_enabled"] = True
    pos_key = bot._position_track_key("BTCUSDT", "LONG")
    state = bot._get_or_create_partial_tp_state("BTCUSDT", "LONG")
    state["levels_completed"] = [0]
    state["last_tp_trigger_ts"] = 1000.0
    bot._backtest_now_ts = 1000.0 + (16 * 60)
    bot._position_first_seen_ts[pos_key] = 1000.0 - (60 * 60)

    decision = bot._evaluate_time_exit(
        symbol="BTCUSDT",
        position=_position(),
        current_price=100.1,
        flow_context={"cvd_momentum": 0.0},
        base_decision=_decision(),
    )

    assert decision is not None
    assert decision.operation == FundFlowOperation.CLOSE
    assert "runner_time_exit" in decision.reason


def test_runner_time_exit_skips_when_flow_still_expanding():
    bot = _make_bot()
    bot.config["fund_flow"]["runner_time_exit_enabled"] = True
    pos_key = bot._position_track_key("BTCUSDT", "LONG")
    state = bot._get_or_create_partial_tp_state("BTCUSDT", "LONG")
    state["levels_completed"] = [0]
    state["last_tp_trigger_ts"] = 1000.0
    bot._backtest_now_ts = 1000.0 + (16 * 60)
    bot._position_first_seen_ts[pos_key] = 1000.0 - (60 * 60)

    decision = bot._evaluate_time_exit(
        symbol="BTCUSDT",
        position=_position(),
        current_price=100.1,
        flow_context={"cvd_momentum": 0.02},
        base_decision=_decision(),
    )

    assert decision is None


def test_partial_tp_fires_first_level_in_volatile_mode_at_point_seven_r():
    bot = _make_bot()

    decision = bot._evaluate_partial_tp(
        symbol="BTCUSDT",
        position=_position(),
        current_price=100.85,
        flow_context={"atr_pct": 0.017, "adx": 24.0},
        base_decision=_decision(),
    )

    assert decision is not None
    assert decision.operation == FundFlowOperation.CLOSE
    assert decision.target_portion_of_balance == 0.45
    state = bot._get_or_create_partial_tp_state("BTCUSDT", "LONG")
    assert state["levels_completed"] == [0]
    assert state["last_tp_trigger_ts"] > 0


def test_position_management_prioritizes_partial_tp_over_fast_exit_full_close():
    bot = _make_bot()
    base = FundFlowDecision(
        operation=FundFlowOperation.CLOSE,
        symbol="BTCUSDT",
        target_portion_of_balance=1.0,
        leverage=2,
        reason="fast_exit_direction_lock_reversed",
        metadata={"fast_exit_reason": "direction_lock_reversed"},
    )

    decision = bot._apply_position_management_overrides(
        symbol="BTCUSDT",
        position=_position(),
        current_price=100.85,
        decision=base,
        flow_context={"atr_pct": 0.017, "adx": 24.0, "cvd_momentum": 0.02},
    )

    assert decision.operation == FundFlowOperation.CLOSE
    assert decision.target_portion_of_balance == 0.45
    assert "partial_tp_level_1" in decision.reason


def test_trailing_distance_uses_atr_with_bounds():
    bot = _make_bot()

    assert bot._calculate_trailing_stop_distance(
        {"atr_pct": 0.010},
        {"trailing_atr_multiplier": 0.6, "trailing_min_distance": 0.005, "trailing_max_distance": 0.010},
    ) == pytest.approx(0.006, rel=1e-9)
    assert bot._calculate_trailing_stop_distance(
        {"atr_pct": 0.010},
        {"trailing_atr_multiplier": 1.8, "trailing_min_distance": 0.012, "trailing_max_distance": 0.030},
    ) == pytest.approx(0.018, rel=1e-9)
    assert bot._calculate_trailing_stop_distance(
        {"atr_pct": 0.030},
        {"trailing_atr_multiplier": 1.8, "trailing_min_distance": 0.012, "trailing_max_distance": 0.030},
    ) == pytest.approx(0.03, rel=1e-9)


def test_partial_tp_same_level_does_not_repeat():
    bot = _make_bot()

    first = bot._evaluate_partial_tp(
        symbol="BTCUSDT",
        position=_position(),
        current_price=100.85,
        flow_context={"atr_pct": 0.017, "adx": 24.0},
        base_decision=_decision(),
    )
    second = bot._evaluate_partial_tp(
        symbol="BTCUSDT",
        position=_position(),
        current_price=100.85,
        flow_context={"atr_pct": 0.017, "adx": 24.0},
        base_decision=_decision(),
    )

    assert first is not None
    assert second is None


def test_dynamic_partial_tp_uses_volatile_profile_for_early_lock():
    bot = _make_bot()

    decision = bot._evaluate_partial_tp(
        symbol="BTCUSDT",
        position=_position(),
        current_price=100.85,
        flow_context={"atr_pct": 0.017, "adx": 24.0},
        base_decision=_decision(),
    )

    assert decision is not None
    assert decision.target_portion_of_balance == 0.45
    state = bot._get_or_create_partial_tp_state("BTCUSDT", "LONG")
    assert state["market_mode"] == "VOLATILE"


def test_dynamic_partial_tp_uses_trending_profile_for_late_release():
    bot = _make_bot()

    decision = bot._evaluate_partial_tp(
        symbol="BTCUSDT",
        position=_position(),
        current_price=101.85,
        flow_context={"atr_pct": 0.010, "adx": 35.0},
        base_decision=_decision(),
    )

    assert decision is not None
    assert decision.target_portion_of_balance == 0.15
    state = bot._get_or_create_partial_tp_state("BTCUSDT", "LONG")
    assert state["market_mode"] == "TRENDING"


def test_partial_tp_trailing_logs_trailing_and_breakeven_activation_events():
    bot = _make_bot()
    state = bot._get_or_create_partial_tp_state("BTCUSDT", "LONG")
    state["levels_completed"] = [0]

    bot._tighten_protection_for_conflict = lambda **_kwargs: {"status": "success"}

    bot._update_partial_tp_trailing_stop(
        symbol="BTCUSDT",
        position=_position(side="LONG", entry_price=100.0),
        current_price=101.0,
        flow_context={"atr_pct": 0.017, "adx": 24.0},
        base_decision=_decision(),
    )

    event_types = [item["event_type"] for item in bot._exit_audit_events]
    assert "trailing_activated" in event_types
    assert "breakeven_activated" in event_types
    assert "protection_priority_applied" in event_types


def test_tighten_protection_logs_breakeven_activation_event():
    bot = _make_bot()

    out = bot._tighten_protection_for_conflict(
        symbol="BTCUSDT",
        position=_position(side="LONG", entry_price=100.0),
        current_price=101.2,
        force_break_even=True,
        atr_pct=0.01,
        cooldown_sec=0.0,
    )

    assert out["status"] == "success"
    assert any(
        item["event_type"] == "breakeven_activated" and item["priority_stage"] == "conflict_tighten_applied"
        for item in bot._exit_audit_events
    )


def test_protection_snapshot_summarizes_tp_and_sl_orders():
    bot = _make_bot()
    bot._open_protection_orders = lambda *_args, **_kwargs: [
        {"orderId": 11, "type": "TAKE_PROFIT_MARKET", "stopPrice": "104.0", "origQty": "1.0", "side": "SELL"},
        {"orderId": 12, "type": "STOP_MARKET", "stopPrice": "98.0", "origQty": "1.0", "side": "SELL"},
    ]

    snap = bot._capture_protection_snapshot("BTCUSDT", "LONG")

    assert snap["has_tp"] is True
    assert snap["has_sl"] is True
    assert snap["tp_order_ids"] == ["11"]
    assert snap["sl_order_ids"] == ["12"]


def test_log_same_bar_priority_evidence_emits_candidate_when_tp_and_sl_coexist():
    bot = _make_bot()
    bot._open_protection_orders = lambda *_args, **_kwargs: [
        {"orderId": 11, "type": "TAKE_PROFIT_MARKET", "stopPrice": "104.0", "origQty": "1.0", "side": "SELL"},
        {"orderId": 12, "type": "STOP_MARKET", "stopPrice": "98.0", "origQty": "1.0", "side": "SELL"},
    ]

    decision = FundFlowDecision(
        operation=FundFlowOperation.CLOSE,
        symbol="BTCUSDT",
        target_portion_of_balance=1.0,
        leverage=2,
        reason="manual_close_for_test",
        metadata={},
    )
    fill_summary = {
        "fill_count": 1,
        "avg_price": 101.0,
        "quantity": 1.0,
        "quote_qty": 101.0,
        "source": "user_trades",
        "fill_time_utc": "2026-04-01 00:00:00",
    }

    bot._log_same_bar_priority_evidence(
        symbol="BTCUSDT",
        decision=decision,
        position=_position(side="LONG", entry_price=100.0),
        current_price=101.0,
        execution_result={"status": "success", "order": {"orderId": 99}},
        fill_summary=fill_summary,
        trigger_type="close",
    )

    evidence = [item for item in bot._exit_audit_events if item["event_type"] == "same_bar_priority_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["same_bar_priority_candidate"] is True
    assert evidence[0]["pre_snapshot"]["has_tp"] is True
    assert evidence[0]["pre_snapshot"]["has_sl"] is True


def test_pretrade_risk_gate_scales_entry_size_by_atr_band():
    bot = _make_bot()
    decision = FundFlowDecision(
        operation=FundFlowOperation.BUY,
        symbol="BTCUSDT",
        target_portion_of_balance=0.20,
        leverage=2,
        reason="entry",
        metadata={"regime_atr_pct": 0.021},
    )

    adjusted, gate_meta = bot._apply_pretrade_risk_gate(
        symbol="BTCUSDT",
        decision=decision,
        position=None,
        current_price=100.0,
        flow_context={},
        account_summary={"available_balance": 1000.0, "equity": 1000.0, "max_leverage": 2.0},
    )

    assert adjusted.operation == FundFlowOperation.BUY
    assert adjusted.target_portion_of_balance == pytest.approx(0.14, rel=1e-9)
    assert gate_meta["state"]["atr"] == 0.021
    assert adjusted.metadata["atr_position_scale"]["scale"] == pytest.approx(0.7, rel=1e-9)


def test_clear_sla_tracking_removes_partial_tp_state():
    bot = _make_bot()
    bot._get_or_create_partial_tp_state("BTCUSDT", "LONG")

    bot._clear_sla_tracking_for_symbol("BTCUSDT")

    assert bot._partial_tp_state_by_pos == {}
