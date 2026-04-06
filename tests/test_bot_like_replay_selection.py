from types import SimpleNamespace

import pandas as pd

from scripts.backtest_fund_flow_bot_like import BotLikeReplayEngine
from src.fund_flow.models import FundFlowDecision, Operation


def test_pick_open_candidates_respects_score_order_and_capacity():
    candidates = [
        {"symbol": "A", "score": 1.20, "max_active_symbols": 3},
        {"symbol": "B", "score": 1.80, "max_active_symbols": 3},
        {"symbol": "C", "score": 1.50, "max_active_symbols": 3},
        {"symbol": "D", "score": 1.10, "max_active_symbols": 3},
    ]

    selected = BotLikeReplayEngine.pick_open_candidates(candidates, active_count=1)

    assert [item["symbol"] for item in selected] == ["B", "C"]


def test_pick_open_candidates_allows_dynamic_cap_expansion_per_candidate():
    candidates = [
        {"symbol": "A", "score": 1.80, "max_active_symbols": 3},
        {"symbol": "B", "score": 1.50, "max_active_symbols": 4},
        {"symbol": "C", "score": 1.20, "max_active_symbols": 3},
    ]

    selected = BotLikeReplayEngine.pick_open_candidates(candidates, active_count=2)

    assert [item["symbol"] for item in selected] == ["A", "B"]


def test_open_position_from_decision_respects_explicit_tp_disable_over_global_defaults():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.positions = {}
    engine.pending_orders = {}
    engine.capital = 10000.0
    engine.config = SimpleNamespace(
        fixed_leverage=None,
        default_leverage=3,
        min_leverage=2,
        max_leverage=4,
        max_symbol_position_portion=0.6,
        min_open_portion=0.06,
        reserve_pct=0.2,
        fee_rate=0.0004,
        entry_slippage=0.0015,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.02,
        take_profit_pct_levels=[0.02, 0.04],
        take_profit_reduce_pct_levels=[0.4, 0.3],
    )
    engine._sync_position_tracking = lambda *_args, **_kwargs: None
    engine._fill_pending_order = lambda symbol, order, fill_price, fill_time: engine.positions.setdefault(symbol, dict(order)) or True

    decision = FundFlowDecision(
        operation=Operation.BUY,
        symbol="BTCUSDT",
        target_portion_of_balance=0.2,
        leverage=3,
        max_price=100.0,
        stop_loss_price=98.0,
        take_profit_price=None,
        metadata={
            "tp_sl": {"tp_enabled": False, "sl_enabled": True},
            "tp_levels": [],
        },
    )

    opened = engine._open_position_from_decision(
        symbol="BTCUSDT",
        decision=decision,
        analysis={"price": 100.0, "time": "2026-03-25 00:00:00"},
    )

    assert opened is True
    assert engine.positions["BTCUSDT"]["take_profit"] is None
    assert engine.positions["BTCUSDT"]["take_profit_levels"] == []


def test_ensure_candidate_ledger_row_records_decision_specific_tp1_plan():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.candidate_ledger_rows = []
    engine._candidate_ledger_index = {}
    engine.config = SimpleNamespace(
        default_take_profit_pct=0.04,
        take_profit_pct_levels=[0.008, 0.012, 0.02],
        take_profit_reduce_pct_levels=[0.25, 0.3, 0.2],
    )

    decision = FundFlowDecision(
        operation=Operation.BUY,
        symbol="BTCUSDT",
        target_portion_of_balance=0.2,
        leverage=3,
        max_price=100.0,
        stop_loss_price=98.0,
        take_profit_price=None,
        metadata={
            "tp_levels": [
                {"price": 101.2, "reduce_pct": 0.15},
                {"price": 103.0, "reduce_pct": 0.20},
            ],
            "signal_type_1h": "red_bar_growing",
            "vwap_state": "long_reclaim_confirmed",
        },
    )
    item = {
        "symbol": "BTCUSDT",
        "analysis": {"price": 100.0, "time": "2026-03-25 00:00:00"},
        "decision": decision,
        "score": 1.2,
        "max_active_symbols": 3,
    }

    row = engine._ensure_candidate_ledger_row(item)

    assert row["tp1_price"] == 101.2
    assert abs(row["tp1_pct"] - 0.012) < 1e-9
    assert row["tp1_reduce_pct"] == 0.15


def test_close_position_bot_like_uses_exit_price_override_and_trade_updates():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.trades = []

    def fake_close_position(symbol, price, time, reason, reduce_margin=None, reduce_pct_original=None):
        del reduce_margin
        engine.trades.append(
            {
                "symbol": symbol,
                "price": float(price),
                "time": time,
                "reason": reason,
                "reduce_pct_original": reduce_pct_original,
            }
        )

    engine.close_position = fake_close_position

    engine._close_position_bot_like(
        "BTCUSDT",
        {"price": 100.0, "time": "2026-03-25 00:00:00"},
        "take_profit_level_intrabar",
        reduce_pct_original=0.25,
        exit_price=101.2,
        trade_updates={"entry_bar_same_bar": True, "entry_bar_same_bar_resolution": "tp_levels_same_bar"},
    )

    assert len(engine.trades) == 1
    assert engine.trades[0]["price"] == 101.2
    assert engine.trades[0]["reduce_pct_original"] == 0.25
    assert engine.trades[0]["entry_bar_same_bar"] is True
    assert engine.trades[0]["entry_bar_same_bar_resolution"] == "tp_levels_same_bar"


def test_handle_entry_bar_same_bar_after_open_executes_tp1_before_stop_with_intrabar_prices():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.entry_bar_same_bar_enabled = True
    engine.entry_bar_same_bar_priority_mode = "tp1_before_stop"
    engine.positions = {
        "BTCUSDT": {
            "side": "long",
            "entry_price": 100.0,
            "stop_price": 99.0,
            "take_profit": 104.0,
            "take_profit_levels": [{"price": 101.0, "reduce_pct": 0.25, "filled": False}],
        }
    }
    engine.candidate_ledger_rows = []
    engine._candidate_ledger_index = {}
    engine.config = SimpleNamespace(
        default_take_profit_pct=0.04,
        take_profit_pct_levels=[0.008, 0.012, 0.02],
        take_profit_reduce_pct_levels=[0.25, 0.3, 0.2],
        same_bar_tp_priority_mode="tp1_before_stop",
    )
    engine._safe_float = lambda value, default=0.0: BotLikeReplayEngine._safe_float(value, default)
    engine._decision_metadata = lambda decision: decision.metadata if isinstance(decision.metadata, dict) else {}
    engine._target_fill_price = lambda pos, row, level_price: float(level_price)
    engine._stop_fill_price = lambda pos, row, stop_price: float(stop_price)
    engine.trades = []

    close_calls = []

    def fake_close_position_bot_like(symbol, analysis, reason, *, reduce_pct_original=None, exit_price=None, trade_updates=None):
        close_calls.append(
            {
                "symbol": symbol,
                "reason": reason,
                "reduce_pct_original": reduce_pct_original,
                "exit_price": exit_price,
                "trade_updates": dict(trade_updates or {}),
            }
        )
        if reduce_pct_original is None or reduce_pct_original >= 1.0:
            engine.positions.pop(symbol, None)

    engine._close_position_bot_like = fake_close_position_bot_like

    decision = FundFlowDecision(
        operation=Operation.BUY,
        symbol="BTCUSDT",
        reason="buy",
        metadata={
            "tp_levels": [{"price": 101.0, "reduce_pct": 0.25}],
            "signal_type_1h": "red_bar_growing",
            "vwap_state": "long_reclaim_confirmed",
        },
    )
    item = {
        "symbol": "BTCUSDT",
        "decision": decision,
        "analysis": {
            "price": 100.0,
            "time": "2026-03-25 00:00:00",
            "row_15m": pd.Series({"high": 101.5, "low": 98.8}),
        },
        "score": 1.0,
        "max_active_symbols": 3,
    }

    engine._handle_entry_bar_same_bar_after_open(item)

    assert [call["reason"] for call in close_calls] == [
        "take_profit_level_intrabar",
        "stop_loss_intrabar_after_tp1_same_bar",
    ]
    assert close_calls[0]["exit_price"] == 101.0
    assert close_calls[1]["exit_price"] == 99.0
    assert close_calls[0]["trade_updates"]["entry_bar_same_bar_resolution"] == "tp1_before_stop_same_bar"
    assert close_calls[1]["trade_updates"]["entry_bar_same_bar_resolution"] == "tp1_before_stop_same_bar"
    row = engine.candidate_ledger_rows[0]
    assert row["entry_bar_same_bar_checked"] is True
    assert row["entry_bar_same_bar_tp1_hit"] is True
    assert row["entry_bar_same_bar_stop_hit"] is True
    assert row["entry_bar_same_bar_resolution"] == "tp1_before_stop_same_bar"


def test_handle_entry_bar_same_bar_after_open_stop_first_uses_stop_price():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.entry_bar_same_bar_enabled = True
    engine.entry_bar_same_bar_priority_mode = "stop_first"
    engine.positions = {
        "BTCUSDT": {
            "side": "long",
            "entry_price": 100.0,
            "stop_price": 99.0,
            "take_profit": 104.0,
            "take_profit_levels": [{"price": 101.0, "reduce_pct": 0.25, "filled": False}],
        }
    }
    engine.candidate_ledger_rows = []
    engine._candidate_ledger_index = {}
    engine.config = SimpleNamespace(
        default_take_profit_pct=0.04,
        take_profit_pct_levels=[0.008, 0.012, 0.02],
        take_profit_reduce_pct_levels=[0.25, 0.3, 0.2],
        same_bar_tp_priority_mode="stop_first",
    )
    engine._safe_float = lambda value, default=0.0: BotLikeReplayEngine._safe_float(value, default)
    engine._decision_metadata = lambda decision: decision.metadata if isinstance(decision.metadata, dict) else {}
    engine._target_fill_price = lambda pos, row, level_price: float(level_price)
    engine._stop_fill_price = lambda pos, row, stop_price: float(stop_price)
    engine.trades = []

    close_calls = []

    def fake_close_position_bot_like(symbol, analysis, reason, *, reduce_pct_original=None, exit_price=None, trade_updates=None):
        close_calls.append(
            {
                "symbol": symbol,
                "reason": reason,
                "reduce_pct_original": reduce_pct_original,
                "exit_price": exit_price,
                "trade_updates": dict(trade_updates or {}),
            }
        )
        engine.positions.pop(symbol, None)

    engine._close_position_bot_like = fake_close_position_bot_like

    decision = FundFlowDecision(
        operation=Operation.BUY,
        symbol="BTCUSDT",
        reason="buy",
        metadata={
            "tp_levels": [{"price": 101.0, "reduce_pct": 0.25}],
            "signal_type_1h": "red_bar_growing",
            "vwap_state": "long_reclaim_confirmed",
        },
    )
    item = {
        "symbol": "BTCUSDT",
        "decision": decision,
        "analysis": {
            "price": 100.0,
            "time": "2026-03-25 00:00:00",
            "row_15m": pd.Series({"high": 101.5, "low": 98.8}),
        },
        "score": 1.0,
        "max_active_symbols": 3,
    }

    engine._handle_entry_bar_same_bar_after_open(item)

    assert len(close_calls) == 1
    assert close_calls[0]["reason"] == "stop_loss_intrabar"
    assert close_calls[0]["exit_price"] == 99.0
    assert close_calls[0]["trade_updates"]["entry_bar_same_bar_resolution"] == "stop_first_same_bar"
    row = engine.candidate_ledger_rows[0]
    assert row["entry_bar_same_bar_resolution"] == "stop_first_same_bar"


def test_close_position_records_post_close_state_after_partial_reduction():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.positions = {
        "BTCUSDT": {
            "side": "long",
            "entry_price": 100.0,
            "entry_time": "2026-03-25 00:00:00",
            "leverage": 3,
            "position_value": 300.0,
            "margin": 100.0,
            "initial_margin": 100.0,
            "entry_notional": 300.0,
            "signal_score": 0.9,
            "signal_type_1h": "red_bar_growing",
            "is_trial_entry": False,
            "entry_scale": 1.0,
            "session_position_scale": 1.0,
            "vwap_score": 0.16,
            "vwap_state": "long_reclaim_confirmed",
            "vwap_location_score": 0.2,
            "ema_multiplier": 1.0,
            "stop_price": 99.0,
            "take_profit_levels": [
                {"price": 101.0, "reduce_pct": 0.25, "filled": True},
                {"price": 102.0, "reduce_pct": 0.30, "filled": False},
            ],
            "take_profit": 104.0,
            "remaining_fraction": 1.0,
            "realized_pnl_accum": 0.0,
            "trailing_activated": False,
            "trailing_peak_ratio": 0.0,
        }
    }
    engine.pending_orders = {}
    engine.trades = []
    engine.capital = 10000.0
    engine.config = SimpleNamespace(fee_rate=0.0004)
    engine._clear_position_tracking = lambda *_args, **_kwargs: None
    engine._update_loss_streak_after_trade_close = lambda *_args, **_kwargs: None

    engine.close_position(
        "BTCUSDT",
        101.0,
        "2026-03-25 00:15:00",
        "take_profit_level_intrabar",
        reduce_pct_original=0.25,
    )

    assert "BTCUSDT" in engine.positions
    assert abs(engine.positions["BTCUSDT"]["remaining_fraction"] - 0.75) < 1e-9
    assert abs(engine.positions["BTCUSDT"]["margin"] - 75.0) < 1e-9
    trade = engine.trades[-1]
    assert trade["position_still_open_after"] is True
    assert abs(trade["post_close_remaining_fraction"] - 0.75) < 1e-9
    assert abs(trade["post_close_margin"] - 75.0) < 1e-9
    assert trade["post_close_stop_price"] == 99.0
    assert trade["post_close_tp_levels_filled_count"] == 1
    assert trade["post_close_tp_levels_remaining_count"] == 1


def test_refresh_trade_post_close_audit_reflects_same_bar_filled_level_update():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.positions = {
        "BTCUSDT": {
            "margin": 75.0,
            "remaining_fraction": 0.75,
            "stop_price": 99.0,
            "take_profit_levels": [
                {"price": 101.0, "reduce_pct": 0.25, "filled": True},
                {"price": 102.0, "reduce_pct": 0.30, "filled": False},
            ],
        }
    }
    trade = {}

    engine._refresh_trade_post_close_audit(trade=trade, symbol="BTCUSDT")

    assert trade["position_still_open_after"] is True
    assert abs(trade["post_close_remaining_fraction"] - 0.75) < 1e-9
    assert abs(trade["post_close_margin"] - 75.0) < 1e-9
    assert trade["post_close_stop_price"] == 99.0
    assert trade["post_close_tp_levels_filled_count"] == 1
    assert trade["post_close_tp_levels_remaining_count"] == 1


def test_open_position_from_decision_records_dynamic_leverage_reject_reason():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.positions = {}
    engine.pending_orders = {}
    engine.capital = 10000.0
    engine.dynamic_leverage_enabled = True
    engine.dynamic_leverage_cfg = {
        "dynamic_leverage_window": 20,
        "dynamic_leverage_min_samples": 10,
        "dynamic_leverage_default_max_leverage": 0,
        "dynamic_leverage_map": [
            {"min_win_rate": 0.0, "max_leverage": 0},
        ],
    }
    engine.trades = [{"pnl": -1.0}] * 20
    engine.config = SimpleNamespace(
        fixed_leverage=None,
        default_leverage=5,
        min_leverage=0,
        max_leverage=5,
        max_symbol_position_portion=0.6,
        min_open_portion=0.06,
        reserve_pct=0.2,
        fee_rate=0.0004,
        entry_slippage=0.0015,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.02,
        take_profit_pct_levels=[],
        take_profit_reduce_pct_levels=[],
    )

    decision = FundFlowDecision(
        operation=Operation.BUY,
        symbol="BTCUSDT",
        target_portion_of_balance=0.2,
        leverage=5,
        max_price=100.0,
        stop_loss_price=98.0,
        take_profit_price=104.0,
        metadata={},
    )

    opened = engine._open_position_from_decision(
        symbol="BTCUSDT",
        decision=decision,
        analysis={"price": 100.0, "time": "2026-03-25 00:00:00"},
    )

    assert opened is False
    assert engine._last_open_reject_reason == "dynamic_leverage_blocked"


def test_open_position_from_decision_records_required_margin_reject_reason():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.positions = {}
    engine.pending_orders = {}
    engine.capital = 120.0
    engine.dynamic_leverage_enabled = False
    engine.dynamic_leverage_cfg = {}
    engine.config = SimpleNamespace(
        fixed_leverage=None,
        default_leverage=5,
        min_leverage=0,
        max_leverage=5,
        max_symbol_position_portion=0.6,
        min_open_portion=0.06,
        reserve_pct=0.2,
        fee_rate=0.0004,
        entry_slippage=0.0015,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.02,
        take_profit_pct_levels=[],
        take_profit_reduce_pct_levels=[],
    )

    decision = FundFlowDecision(
        operation=Operation.BUY,
        symbol="BTCUSDT",
        target_portion_of_balance=0.2,
        leverage=5,
        max_price=100.0,
        stop_loss_price=98.0,
        take_profit_price=104.0,
        metadata={},
    )

    opened = engine._open_position_from_decision(
        symbol="BTCUSDT",
        decision=decision,
        analysis={"price": 100.0, "time": "2026-03-25 00:00:00"},
    )

    assert opened is False
    assert engine._last_open_reject_reason == "required_margin_too_small"


def test_build_analysis_with_status_reports_15m_warmup_block():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    data = {
        "15m": pd.DataFrame({"timestamp": pd.date_range("2026-04-01 04:00:00", periods=10, freq="15min")}),
        "1h": pd.DataFrame({"timestamp": pd.date_range("2026-04-01 00:00:00", periods=10, freq="1h")}),
        "4h": pd.DataFrame({"timestamp": pd.date_range("2026-03-31 00:00:00", periods=10, freq="4h")}),
    }

    analysis, status = engine._build_analysis_with_status("BTCUSDT", data, 9)

    assert analysis is None
    assert status == "warmup_15m"


def test_build_analysis_with_status_uses_5m_decision_timeframe():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.config = SimpleNamespace(decision_timeframe="5m")
    engine.replay_microstructure_enabled = False
    engine.replay_microstructure_mode = "historical_proxy"
    engine._safe_float = lambda value, default=0.0: BotLikeReplayEngine._safe_float(value, default)
    engine._safe_optional_float = lambda value: BotLikeReplayEngine._safe_optional_float(value)
    data = {
        "5m": pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-04-01 04:00:00", periods=60, freq="5min"),
                "open": [100.0] * 60,
                "high": [101.0] * 60,
                "low": [99.0] * 60,
                "close": [100.5] * 60,
                "atr": [0.5] * 60,
                "ema21": [100.2] * 60,
                "ema55": [100.0] * 60,
                "macd_hist": [0.1] * 60,
            }
        ),
        "15m": pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-04-01 00:00:00", periods=30, freq="15min"),
                "open": [100.0] * 30,
                "high": [101.0] * 30,
                "low": [99.0] * 30,
                "close": [100.5] * 30,
                "atr": [0.5] * 30,
                "ema21": [100.2] * 30,
                "ema55": [100.0] * 30,
                "macd_hist": [0.1] * 30,
            }
        ),
        "1h": pd.DataFrame({"timestamp": pd.date_range("2026-04-01 00:00:00", periods=20, freq="1h"), "open": [100.0] * 20, "high": [101.0] * 20, "low": [99.0] * 20, "close": [100.5] * 20, "atr": [0.5] * 20, "ema21": [100.2] * 20, "ema55": [100.0] * 20, "macd_hist": [0.1] * 20}),
        "4h": pd.DataFrame({"timestamp": pd.date_range("2026-03-31 00:00:00", periods=20, freq="4h"), "open": [100.0] * 20, "high": [101.0] * 20, "low": [99.0] * 20, "close": [100.5] * 20, "atr": [0.5] * 20, "ema21": [100.2] * 20, "ema55": [100.0] * 20, "macd_hist": [0.1] * 20}),
    }

    analysis, status = engine._build_analysis_with_status("BTCUSDT", data, 55)

    assert status == "ready"
    assert analysis is not None
    assert analysis["flow_context"]["active_timeframe"] == "5m"
    assert analysis["time"] == data["5m"].iloc[55]["timestamp"]
    assert analysis["row_15m"]["timestamp"] == data["5m"].iloc[55]["timestamp"]


def test_run_backtest_reports_early_analysis_skip_counters():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.positions = {}
    engine.ai_advice_logs = []
    engine._last_price_map = {}
    engine._timestamp_key = lambda ts: int(pd.Timestamp(ts).timestamp())
    engine._build_analysis_with_status = lambda symbol, data, idx: (None, "warmup_15m")
    engine._record_equity_snapshot = lambda *args, **kwargs: None
    engine._finalize_drawdown_recovery = lambda: None

    tf_15m = pd.DataFrame({"timestamp": pd.date_range("2026-04-01 04:00:00", periods=3, freq="15min")})
    market_data_map = {"BTCUSDT": {"15m": tf_15m, "1h": tf_15m.copy(), "4h": tf_15m.copy()}}

    result = engine.run_backtest(market_data_map)

    assert result["timeline_points"] == 3
    assert result["analysis_attempts"] == 3
    assert result["analysis_ready"] == 0
    assert result["analysis_skipped"]["warmup_15m"] == 3
    assert result["decision_counts"] == {}


def test_run_backtest_uses_5m_timeline_when_configured():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.config = SimpleNamespace(window_start_iso="", window_end_iso="", decision_timeframe="5m")
    engine.positions = {}
    engine.ai_advice_logs = []
    engine._last_price_map = {}
    engine._timestamp_key = lambda ts: int(pd.Timestamp(ts).timestamp())
    engine._record_equity_snapshot = lambda *args, **kwargs: None
    engine._finalize_drawdown_recovery = lambda: None
    engine.signal_funnel = None
    engine._build_analysis_with_status = lambda symbol, data, idx: (None, "warmup_5m")

    tf_5m = pd.DataFrame({"timestamp": pd.date_range("2026-04-01 04:00:00", periods=5, freq="5min")})
    tf_15m = pd.DataFrame({"timestamp": pd.date_range("2026-04-01 04:00:00", periods=2, freq="15min")})
    market_data_map = {"BTCUSDT": {"5m": tf_5m, "15m": tf_15m, "1h": tf_15m.copy(), "4h": tf_15m.copy()}}

    result = engine.run_backtest(market_data_map)

    assert result["timeline_points"] == 5
    assert result["analysis_attempts"] == 5
    assert result["analysis_skipped"]["warmup_5m"] == 5


def test_bot_like_replay_decide_annotates_replay_microstructure_downgrade():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.decision_engine = SimpleNamespace(
        decide=lambda **_kwargs: FundFlowDecision(
            operation=Operation.BUY,
            symbol="BTCUSDT",
            target_portion_of_balance=0.2,
            leverage=3,
            reason="buy",
            metadata={},
        )
    )
    engine._build_portfolio = lambda: {"positions": {}}

    decision = engine._decide(
        "BTCUSDT",
        {
            "price": 100.0,
            "flow_context": {
                "replay_microstructure": {
                    "mode": "degraded_unavailable",
                    "spread_gate_mode": "skip_if_missing",
                    "missing_fields": ["spread_bps", "depth_ratio", "imbalance"],
                }
            },
        },
    )

    assert decision.metadata["replay_microstructure_mode"] == "degraded_unavailable"
    assert decision.metadata["replay_spread_gate_mode"] == "skip_if_missing"
    assert decision.metadata["replay_microstructure_missing_fields"] == [
        "spread_bps",
        "depth_ratio",
        "imbalance",
    ]


def test_run_backtest_returns_signal_funnel_report():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.positions = {}
    engine.ai_advice_logs = []
    engine._last_price_map = {}
    engine._timestamp_key = lambda ts: int(pd.Timestamp(ts).timestamp())
    engine._record_equity_snapshot = lambda *args, **kwargs: None
    engine._finalize_drawdown_recovery = lambda: None
    engine._set_backtest_now_ts = lambda *args, **kwargs: None
    engine._sync_position_tracking = lambda *args, **kwargs: None
    engine._check_stops_bot_like = lambda *args, **kwargs: False
    engine._apply_position_management_override = lambda **kwargs: kwargs["decision"]
    engine._apply_pretrade_gate = lambda **kwargs: kwargs["decision"]
    engine._dynamic_cap = lambda decision: 1
    engine._enforce_ai_final_review = lambda candidates: candidates
    engine._open_position_from_decision = lambda symbol, decision, analysis: True
    engine.ai_review_enabled = False
    engine.bot_logic = SimpleNamespace(_decision_signal_score=lambda decision, flow_context: 0.9)

    analysis_payload = {
        "price": 100.0,
        "time": pd.Timestamp("2026-04-01 00:00:00"),
        "row_15m": pd.Series({"high": 101.0, "low": 99.0}),
        "flow_context": {
            "replay_microstructure": {
                "mode": "degraded_unavailable",
                "missing_fields": ["spread_bps"],
            }
        },
    }
    engine._build_analysis_with_status = lambda symbol, data, idx: (analysis_payload, "ready")
    engine._decide = lambda symbol, analysis: FundFlowDecision(
        operation=Operation.HOLD,
        symbol=symbol,
        reason="macd_v2_hold_vwap_score_filter_score_0.00",
        metadata={"signal_score": 0.81, "vwap_score": 0.09, "pocket_entry_gate": {"passed": True, "reason": ""}},
    )

    tf_15m = pd.DataFrame({"timestamp": pd.date_range("2026-04-01 00:00:00", periods=1, freq="15min")})
    market_data_map = {"BTCUSDT": {"15m": tf_15m, "1h": tf_15m.copy(), "4h": tf_15m.copy()}}

    result = engine.run_backtest(market_data_map)

    assert "signal_funnel" in result
    assert result["signal_funnel"]["0_raw_signal"]["passed"] == 1
    assert result["signal_funnel"]["2_vwap_threshold"]["blocked"] == 1


def test_apply_candidate_pre_filter_skips_when_disabled():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.candidate_pre_filter_enabled = False
    engine.candidate_pre_filter_cfg = {}
    engine.signal_funnel = None

    candidates = [{"symbol": "BTCUSDT", "score": 1.0}]

    result = engine._apply_candidate_pre_filter(candidates)

    assert result == candidates


def test_apply_candidate_pre_filter_blocks_low_quality_candidate():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.candidate_pre_filter_enabled = True
    engine.candidate_pre_filter_cfg = {
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
    engine.signal_funnel = None

    decision = FundFlowDecision(
        operation=Operation.BUY,
        symbol="RENDERUSDT",
        reason="macd_v2_long_1h_red_bar_growing_15m_red_bar_growing_vwap_0.15",
        metadata={
            "signal_type_1h": "red_bar_growing",
            "vwap_state": "long_dual_support",
            "signal_score": 0.86,
            "vwap_score": 0.17,
            "is_trial_entry": False,
        },
    )
    candidates = [{"symbol": "RENDERUSDT", "decision": decision, "analysis": {"time": "2026-04-01 06:15:00"}, "score": 1.0}]

    result = engine._apply_candidate_pre_filter(candidates)

    assert result == []
    assert len(engine.candidate_ledger_rows) == 1
    assert engine.candidate_ledger_rows[0]["pre_filter_passed"] is False
    assert "PRE_AI_SCORE" in engine.candidate_ledger_rows[0]["pre_filter_reason"]


def test_candidate_filter_payload_includes_symbol_for_symbol_specific_rules():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine._safe_float = lambda value, default=0.0: BotLikeReplayEngine._safe_float(value, default)
    engine._decision_metadata = lambda _decision: {
        "signal_type_1h": "red_bar_growing",
        "vwap_state": "long_reclaim_confirmed",
        "signal_score": 0.95,
        "vwap_score": 0.18,
    }

    decision = FundFlowDecision(
        operation=Operation.BUY,
        symbol="ONDOUSDT",
        reason="buy",
        metadata={},
    )

    payload = engine._candidate_filter_payload({"symbol": "ONDOUSDT", "decision": decision})

    assert payload["symbol"] == "ONDOUSDT"


def test_apply_candidate_pre_filter_blocks_repeat_reclaim_cluster_candidate():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.candidate_pre_filter_enabled = True
    engine.candidate_pre_filter_cfg = {
        "candidate_filter_min_signal_scores": {
            "red_bar_growing": 0.855,
            "_default": 0.87,
        },
        "candidate_filter_min_vwap_scores": {
            "_default": 0.10,
        },
        "candidate_filter_cluster_gates": [
            {
                "signal_type": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "side": "long",
                "max_candidate_rank": 1,
                "reason_label": "RBG_RECLAIM_REPEAT",
            }
        ],
    }
    engine.signal_funnel = None
    engine._candidate_cluster_state = {}
    engine._safe_float = lambda value, default=0.0: BotLikeReplayEngine._safe_float(value, default)
    engine._decision_metadata = lambda decision: decision.metadata if isinstance(decision.metadata, dict) else {}

    def make_candidate(ts: str) -> dict:
        decision = FundFlowDecision(
            operation=Operation.BUY,
            symbol="RENDERUSDT",
            reason="buy",
            metadata={
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.90,
                "vwap_score": 0.17,
                "is_trial_entry": False,
            },
        )
        return {
            "symbol": "RENDERUSDT",
            "decision": decision,
            "analysis": {"time": ts, "price": 100.0},
            "score": 1.0,
        }

    first = engine._apply_candidate_pre_filter([make_candidate("2026-04-01 06:00:00")])
    second = engine._apply_candidate_pre_filter([make_candidate("2026-04-01 06:15:00")])

    assert len(first) == 1
    assert second == []
    assert engine.candidate_ledger_rows[-1]["cluster_rank"] == 2
    assert "PRE_AI_CLUSTER_REPEAT:RBG_RECLAIM_REPEAT" in engine.candidate_ledger_rows[-1]["pre_filter_reason"]


def test_enforce_ai_final_review_logs_skipped_topn_candidates():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.ai_flat_top_n = 1
    engine.ai_advice_logs = []
    engine.ai_review_cfg = {}
    engine.bot_logic = SimpleNamespace(
        _ai_entry_guard=lambda **kwargs: (True, ""),
        _decision_signal_score=lambda decision, flow_context: 1.0,
    )
    engine.decision_engine = SimpleNamespace(
        decide=lambda **kwargs: FundFlowDecision(
            operation=Operation.BUY,
            symbol=kwargs["symbol"],
            reason="buy",
            metadata={"ds_source": "-", "ds_confidence": 0.0},
        )
    )
    engine._build_portfolio = lambda: {"positions": {}}
    engine._decision_metadata = lambda decision: decision.metadata if isinstance(decision.metadata, dict) else {}
    engine._safe_float = lambda value, default=0.0: BotLikeReplayEngine._safe_float(value, default)
    engine.signal_funnel = None

    def make_item(symbol: str, score: float) -> dict:
        decision = FundFlowDecision(
            operation=Operation.BUY,
            symbol=symbol,
            reason="buy",
            metadata={
                "signal_type_1h": "red_bar_growing",
                "signal_score": 0.9,
                "vwap_score": 0.16,
                "vwap_state": "long_reclaim_confirmed",
            },
        )
        return {
            "symbol": symbol,
            "decision": decision,
            "analysis": {"time": "2026-04-01 00:00:00", "price": 100.0, "flow_context": {}},
            "score": score,
            "signal_type_1h": "red_bar_growing",
            "candidate_pre_filter": {"passed": True, "reason": "PRE_AI_PASS"},
        }

    approved = engine._enforce_ai_final_review([make_item("AAAUSDT", 2.0), make_item("BBBUSDT", 1.0)])

    assert len(approved) == 1
    assert any(row["symbol"] == "BBBUSDT" and row["review_mode"] == "skipped_topn" for row in engine.ai_advice_logs)
    ledger = {row["symbol"]: row for row in engine.candidate_ledger_rows}
    assert ledger["AAAUSDT"]["ai_shortlisted"] is True
    assert ledger["AAAUSDT"]["ai_allowed"] is True
    assert ledger["BBBUSDT"]["ai_shortlisted"] is False
    assert ledger["BBBUSDT"]["ai_block_reason"] == "ai_shortlist_topn"
