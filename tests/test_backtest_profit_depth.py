from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.backtest_macd_v2 import BacktestConfig, BacktestEngine, build_backtest_config
from src.fund_flow.macd_strategy_v2 import MACDSignalV2, MACDStrategyV2Config


def _signal(
    *,
    direction: str = "long",
    signal_type_1h: str = "red_bar_growing",
    vwap_state: str = "long_dual_support",
) -> MACDSignalV2:
    return MACDSignalV2(
        direction=direction,
        signal_score=0.91,
        signal_type_1h=signal_type_1h,
        entry_type_15m=signal_type_1h,
        entry_score_15m=0.35,
        vwap_score=0.16,
        vwap_deviation=0.001,
        vwap_state=vwap_state,
        vwap_location_score=0.7,
        ema_multiplier=1.0,
        ema_structure_status="normal",
        details={},
    )


def _pending_order(
    *,
    side: str = "long",
    limit_price: float = 100.0,
    stop_price: float = 99.0,
    take_profit: float = 104.0,
    entry_time: pd.Timestamp | None = None,
    time_in_force: str = "GTC",
) -> dict:
    return {
        "side": side,
        "limit_price": limit_price,
        "margin": 400.0,
        "leverage": 5,
        "position_value": 400.0,
        "stop_price": stop_price,
        "take_profit": take_profit,
        "take_profit_levels": [
            {"price": 100.8, "reduce_pct": 0.25, "filled": False},
            {"price": 101.2, "reduce_pct": 0.30, "filled": False},
        ],
        "signal_score": 0.91,
        "signal_type_1h": "green_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "short_retest_reject",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "entry_time": entry_time or pd.Timestamp("2026-03-01 00:00:00"),
        "time_in_force": time_in_force,
        "entry_initial_time_in_force": time_in_force,
        "bars_waited": 0,
    }


def test_execute_trade_applies_long_dual_support_risk_overrides() -> None:
    runtime_cfg = {
        "fund_flow": {
            "vwap_structure_overrides": {
                "long_dual_support": {
                    "position_scale_override": 0.80,
                    "stop_loss_pct_override": 0.012,
                    "breakeven_trigger_pnl_ratio_override": 0.005,
                    "breakeven_lock_ratio_override": 0.003,
                    "take_profit_pct_levels_override": [0.008, 0.012, 0.020],
                    "take_profit_reduce_pct_levels_override": [0.25, 0.30, 0.20],
                }
            }
        }
    }
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        take_profit_pct_levels=[],
        take_profit_reduce_pct_levels=[],
        breakeven_enabled=True,
        breakeven_trigger_pnl_ratio=0.008,
        breakeven_lock_ratio=0.0025,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config=runtime_cfg)
    engine._is_entry_cooldown_active = lambda _time: False
    engine._strategy_engine_for_symbol = lambda _symbol: SimpleNamespace(
        resolve_session_position_scale=lambda *_args, **_kwargs: 1.0,
        resolve_symbol_risk_session_scale=lambda *_args, **_kwargs: 1.0,
        is_watchlist_symbol=lambda _symbol: False,
    )
    engine.calculate_position_size = lambda **_kwargs: (1000.0, 2)

    analysis = {
        "signal": _signal(),
        "time": pd.Timestamp("2026-03-01 00:00:00"),
        "price": 100.0,
        "row_1h": pd.Series({"atr": 1.0}),
        "cvd_veto_context": {},
        "cvd_context": {},
    }

    engine.execute_trade("SOLUSDT", analysis, {})

    order = engine.pending_orders["SOLUSDT"]
    assert order["margin"] == 800.0
    assert round(order["stop_price"], 4) == 98.8
    assert order["breakeven_trigger_pnl_ratio"] == 0.005
    assert order["breakeven_lock_ratio"] == 0.003
    assert [round(level["price"], 4) for level in order["take_profit_levels"]] == [100.8, 101.2, 102.0]


def test_execute_trade_applies_take_profit_pct_override_without_tp_levels() -> None:
    runtime_cfg = {
        "fund_flow": {
            "vwap_structure_overrides": {
                "long_dual_support": {
                    "take_profit_pct_override": 0.02,
                }
            }
        }
    }
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        take_profit_pct_levels=[],
        take_profit_reduce_pct_levels=[],
        breakeven_enabled=True,
        breakeven_trigger_pnl_ratio=0.008,
        breakeven_lock_ratio=0.0025,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config=runtime_cfg)
    engine._is_entry_cooldown_active = lambda _time: False
    engine._strategy_engine_for_symbol = lambda _symbol: SimpleNamespace(
        resolve_session_position_scale=lambda *_args, **_kwargs: 1.0,
        resolve_symbol_risk_session_scale=lambda *_args, **_kwargs: 1.0,
        is_watchlist_symbol=lambda _symbol: False,
    )
    engine.calculate_position_size = lambda **_kwargs: (1000.0, 2)

    analysis = {
        "signal": _signal(),
        "time": pd.Timestamp("2026-03-01 00:00:00"),
        "price": 100.0,
        "row_1h": pd.Series({"atr": 1.0}),
        "cvd_veto_context": {},
        "cvd_context": {},
    }

    engine.execute_trade("SOLUSDT", analysis, {})

    order = engine.pending_orders["SOLUSDT"]
    assert order["take_profit"] == pytest.approx(102.0, rel=1e-9)
    assert order["take_profit_levels"] == []


def test_execute_trade_uses_passive_limit_below_reference_price_for_long() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        take_profit_pct_levels=[],
        take_profit_reduce_pct_levels=[],
        entry_slippage=0.003,
        entry_passive_offset_pct=0.002,
        entry_passive_pricing_atr_fraction=0.0,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine._is_entry_cooldown_active = lambda _time: False
    engine._strategy_engine_for_symbol = lambda _symbol: SimpleNamespace(
        resolve_session_position_scale=lambda *_args, **_kwargs: 1.0,
        resolve_symbol_risk_session_scale=lambda *_args, **_kwargs: 1.0,
        is_watchlist_symbol=lambda _symbol: False,
    )
    engine.calculate_position_size = lambda **_kwargs: (1000.0, 2)

    analysis = {
        "signal": _signal(direction="long"),
        "time": pd.Timestamp("2026-03-01 00:00:00"),
        "price": 100.0,
        "row_1h": pd.Series({"atr": 1.0}),
        "cvd_veto_context": {},
        "cvd_context": {},
    }

    engine.execute_trade("SOLUSDT", analysis, {})

    order = engine.pending_orders["SOLUSDT"]
    assert order["limit_price"] == pytest.approx(99.8, rel=1e-9)
    assert order["limit_price"] < analysis["price"]


def test_execute_trade_uses_passive_limit_above_reference_price_for_short() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        take_profit_pct_levels=[],
        take_profit_reduce_pct_levels=[],
        entry_slippage=0.003,
        entry_passive_offset_pct=0.002,
        entry_passive_pricing_atr_fraction=0.0,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine._is_entry_cooldown_active = lambda _time: False
    engine._strategy_engine_for_symbol = lambda _symbol: SimpleNamespace(
        resolve_session_position_scale=lambda *_args, **_kwargs: 1.0,
        resolve_symbol_risk_session_scale=lambda *_args, **_kwargs: 1.0,
        is_watchlist_symbol=lambda _symbol: False,
    )
    engine.calculate_position_size = lambda **_kwargs: (1000.0, 2)

    analysis = {
        "signal": _signal(direction="short", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
        "time": pd.Timestamp("2026-03-01 00:00:00"),
        "price": 100.0,
        "row_1h": pd.Series({"atr": 1.0}),
        "cvd_veto_context": {},
        "cvd_context": {},
    }

    engine.execute_trade("SOLUSDT", analysis, {})

    order = engine.pending_orders["SOLUSDT"]
    assert order["limit_price"] == pytest.approx(100.2, rel=1e-9)
    assert order["limit_price"] > analysis["price"]


def test_execute_trade_passive_ioc_fills_on_intrabar_touch_not_open_take() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        take_profit_pct_levels=[],
        take_profit_reduce_pct_levels=[],
        entry_passive_offset_pct=0.002,
        entry_passive_pricing_atr_fraction=0.0,
        entry_time_in_force="IOC",
        open_gtc_fallback_enabled=True,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine._is_entry_cooldown_active = lambda _time: False
    engine._strategy_engine_for_symbol = lambda _symbol: SimpleNamespace(
        resolve_session_position_scale=lambda *_args, **_kwargs: 1.0,
        resolve_symbol_risk_session_scale=lambda *_args, **_kwargs: 1.0,
        is_watchlist_symbol=lambda _symbol: False,
    )
    engine.calculate_position_size = lambda **_kwargs: (1000.0, 2)

    engine.execute_trade(
        "SOLUSDT",
        {
            "signal": _signal(direction="long"),
            "time": pd.Timestamp("2026-03-01 00:00:00"),
            "price": 100.0,
            "row_1h": pd.Series({"atr": 1.0}),
            "cvd_veto_context": {},
            "cvd_context": {},
        },
        {},
    )

    filled = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="long"),
                "row_15m": pd.Series({"open": 99.9, "high": 100.3, "low": 99.79, "close": 100.1}),
                "time": pd.Timestamp("2026-03-01 00:15:00"),
                "price": 100.1,
            }
        }
    )

    assert filled == {"SOLUSDT"}
    assert "SOLUSDT" in engine.positions
    assert engine.positions["SOLUSDT"]["entry_time_in_force"] == "IOC"
    assert engine.positions["SOLUSDT"]["entry_price"] == pytest.approx(99.8, rel=1e-9)


def test_check_stops_uses_position_specific_breakeven_override() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=True,
        breakeven_trigger_pnl_ratio=0.008,
        breakeven_lock_ratio=0.0025,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    entry_time = pd.Timestamp("2026-03-01 00:00:00")
    engine.positions["SOLUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 1600.0,
        "position_value": 800.0,
        "margin": 800.0,
        "initial_margin": 800.0,
        "remaining_fraction": 1.0,
        "leverage": 2,
        "stop_price": 98.8,
        "take_profit": 104.0,
        "take_profit_levels": [],
        "entry_time": entry_time,
        "signal_score": 0.91,
        "signal_type_1h": "red_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "long_dual_support",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
        "breakeven_trigger_pnl_ratio": 0.005,
        "breakeven_lock_ratio": 0.003,
    }
    analysis = {
        "signal": _signal(),
        "row_15m": pd.Series({"open": 100.4, "high": 100.7, "low": 100.35, "close": 100.4}),
        "price": 100.4,
        "time": pd.Timestamp("2026-03-01 00:15:00"),
    }

    closed = engine.check_stops("SOLUSDT", analysis)

    assert closed is False
    assert engine.positions["SOLUSDT"]["stop_price"] == pytest.approx(100.3, rel=1e-9)


def test_q4_rsi_lead_hold_skips_breakeven_and_tp_before_4h_exit_turn() -> None:
    config = BacktestConfig(
        symbols=["ETHUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=True,
        breakeven_trigger_pnl_ratio=0.008,
        breakeven_lock_ratio=0.004,
    )
    strategy_config = MACDStrategyV2Config(
        enable_q4_rsi_lead_preflip_long=True,
        enable_q4_rsi_lead_preflip_hold=True,
        q4_rsi_lead_preflip_hold_rsi_4h_exit_threshold=70.0,
        q4_rsi_lead_preflip_hold_rsi_4h_pullback=1.0,
    )
    engine = BacktestEngine(config, strategy_config, runtime_config={})
    engine.positions["ETHUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 1600.0,
        "position_value": 800.0,
        "margin": 800.0,
        "initial_margin": 800.0,
        "remaining_fraction": 1.0,
        "leverage": 2,
        "stop_price": 98.0,
        "take_profit": 104.0,
        "take_profit_levels": [
            {"price": 101.5, "reduce_pct": 0.25, "filled": False},
        ],
        "entry_time": pd.Timestamp("2026-03-09 13:30:00"),
        "signal_score": 0.91,
        "signal_type_1h": "red_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 0.35,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "vwap_disabled",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
        "q4_rsi_lead_preflip_hold_active": True,
        "q4_rsi_lead_preflip_hold_peak_rsi_4h": 59.0,
        "q4_rsi_lead_preflip_hold_exit_armed": False,
        "q4_rsi_lead_preflip_hold_rsi_4h_exit_threshold": 70.0,
        "q4_rsi_lead_preflip_hold_rsi_4h_pullback": 1.0,
    }
    signal = _signal(direction="long", signal_type_1h="red_bar_growing", vwap_state="vwap_disabled")
    signal.rsi_4h = 64.0
    signal.details = {
        "q4_rsi_lead_preflip_passed": True,
    }
    analysis = {
        "signal": signal,
        "row_15m": pd.Series({"open": 101.8, "high": 102.2, "low": 100.5, "close": 101.1, "atr": 0.3}),
        "price": 101.1,
        "time": pd.Timestamp("2026-03-09 13:45:00"),
    }

    closed = engine.check_stops("ETHUSDT", analysis)

    assert closed is False
    assert "ETHUSDT" in engine.positions
    assert engine.positions["ETHUSDT"]["stop_price"] == pytest.approx(98.0, rel=1e-9)
    assert engine.positions["ETHUSDT"]["take_profit_levels"][0]["filled"] is False
    assert engine.positions["ETHUSDT"]["q4_rsi_lead_preflip_hold_peak_rsi_4h"] == pytest.approx(64.0, rel=1e-9)


def test_q4_rsi_lead_hold_exits_on_4h_rsi_pullback_after_threshold() -> None:
    config = BacktestConfig(
        symbols=["ETHUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=True,
        breakeven_trigger_pnl_ratio=0.008,
        breakeven_lock_ratio=0.004,
    )
    strategy_config = MACDStrategyV2Config(
        enable_q4_rsi_lead_preflip_long=True,
        enable_q4_rsi_lead_preflip_hold=True,
        q4_rsi_lead_preflip_hold_rsi_4h_exit_threshold=70.0,
        q4_rsi_lead_preflip_hold_rsi_4h_pullback=1.0,
    )
    engine = BacktestEngine(config, strategy_config, runtime_config={})
    engine.positions["ETHUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 1600.0,
        "position_value": 800.0,
        "margin": 800.0,
        "initial_margin": 800.0,
        "remaining_fraction": 1.0,
        "leverage": 2,
        "stop_price": 98.0,
        "take_profit": 104.0,
        "take_profit_levels": [],
        "entry_time": pd.Timestamp("2026-03-09 13:30:00"),
        "signal_score": 0.91,
        "signal_type_1h": "red_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 0.35,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "vwap_disabled",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
        "q4_rsi_lead_preflip_hold_active": True,
        "q4_rsi_lead_preflip_hold_peak_rsi_4h": 72.4,
        "q4_rsi_lead_preflip_hold_exit_armed": True,
        "q4_rsi_lead_preflip_hold_rsi_4h_exit_threshold": 70.0,
        "q4_rsi_lead_preflip_hold_rsi_4h_pullback": 1.0,
    }
    signal = _signal(direction="long", signal_type_1h="red_bar_growing", vwap_state="vwap_disabled")
    signal.rsi_4h = 70.9
    signal.details = {
        "q4_rsi_lead_preflip_passed": True,
    }
    analysis = {
        "signal": signal,
        "row_15m": pd.Series({"open": 108.0, "high": 108.5, "low": 107.4, "close": 108.1, "atr": 0.4}),
        "price": 108.1,
        "time": pd.Timestamp("2026-03-17 04:00:00"),
    }

    closed = engine.check_stops("ETHUSDT", analysis)

    assert closed is True
    assert "ETHUSDT" not in engine.positions
    assert engine.trades[-1]["reason"] == "q4_rsi_lead_4h_rsi_turn_exit"


def test_q4_rsi_lead_hold_exit_metric_rsi_is_faster_than_rsi21() -> None:
    config = BacktestConfig(
        symbols=["ETHUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=True,
        breakeven_trigger_pnl_ratio=0.008,
        breakeven_lock_ratio=0.004,
    )
    strategy_config = MACDStrategyV2Config(
        enable_q4_rsi_lead_preflip_long=True,
        enable_q4_rsi_lead_preflip_hold=True,
        q4_rsi_lead_preflip_hold_exit_metric="rsi",
        q4_rsi_lead_preflip_hold_rsi_4h_exit_threshold=70.0,
        q4_rsi_lead_preflip_hold_rsi_4h_pullback=1.0,
    )
    engine = BacktestEngine(config, strategy_config, runtime_config={})
    base_pos = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 1600.0,
        "position_value": 800.0,
        "margin": 800.0,
        "initial_margin": 800.0,
        "remaining_fraction": 1.0,
        "leverage": 2,
        "stop_price": 98.0,
        "take_profit": 104.0,
        "take_profit_levels": [],
        "entry_time": pd.Timestamp("2026-03-09 13:30:00"),
        "signal_score": 0.91,
        "signal_type_1h": "red_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 0.35,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "vwap_disabled",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
        "q4_rsi_lead_preflip_hold_active": True,
        "q4_rsi_lead_preflip_hold_peak_rsi_4h": 71.8,
        "q4_rsi_lead_preflip_hold_exit_armed": True,
        "q4_rsi_lead_preflip_hold_exit_metric": "rsi",
        "q4_rsi_lead_preflip_hold_rsi_4h_exit_threshold": 70.0,
        "q4_rsi_lead_preflip_hold_rsi_4h_pullback": 1.0,
    }
    engine.positions["ETHUSDT"] = dict(base_pos)
    signal = _signal(direction="long", signal_type_1h="red_bar_growing", vwap_state="vwap_disabled")
    signal.details = {"q4_rsi_lead_preflip_passed": True}
    analysis = {
        "signal": signal,
        "row_4h": pd.Series({"rsi": 63.2, "rsi_21": 71.1}),
        "row_15m": pd.Series({"open": 103.0, "high": 103.2, "low": 102.8, "close": 103.1, "atr": 0.2}),
        "price": 103.1,
        "time": pd.Timestamp("2026-03-10 16:00:00"),
    }

    closed = engine.check_stops("ETHUSDT", analysis)

    assert closed is True
    assert engine.trades[-1]["reason"] == "q4_rsi_lead_4h_rsi_turn_exit"

    strategy_config_slow = MACDStrategyV2Config(
        enable_q4_rsi_lead_preflip_long=True,
        enable_q4_rsi_lead_preflip_hold=True,
        q4_rsi_lead_preflip_hold_exit_metric="rsi_21",
        q4_rsi_lead_preflip_hold_rsi_4h_exit_threshold=70.0,
        q4_rsi_lead_preflip_hold_rsi_4h_pullback=1.0,
    )
    engine_slow = BacktestEngine(config, strategy_config_slow, runtime_config={})
    slow_pos = dict(base_pos)
    slow_pos["q4_rsi_lead_preflip_hold_exit_metric"] = "rsi_21"
    engine_slow.positions["ETHUSDT"] = slow_pos

    closed_slow = engine_slow.check_stops("ETHUSDT", analysis)

    assert closed_slow is False
    assert "ETHUSDT" in engine_slow.positions
    assert engine_slow.positions["ETHUSDT"]["q4_rsi_lead_preflip_hold_exit_metric"] == "rsi_21"
    assert engine_slow.positions["ETHUSDT"]["q4_rsi_lead_preflip_hold_current_exit_value"] == pytest.approx(71.1, rel=1e-9)


def test_check_stops_applies_oscillation_trailing_profile() -> None:
    runtime_cfg = {
        "fund_flow": {
            "trailing_stop_enabled": True,
            "trailing_stop_profiles": {
                "oscillation": {
                    "activation_pnl_ratio": 0.006,
                    "atr_multiplier": 0.5,
                    "min_distance_pct": 0.004,
                    "max_distance_pct": 0.008,
                }
            },
            "trailing_stop_profile_map": {
                "long_dual_support": "oscillation",
                "_default": "oscillation",
            },
        }
    }
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config=runtime_cfg)
    engine.positions["SOLUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 1600.0,
        "position_value": 800.0,
        "margin": 800.0,
        "initial_margin": 800.0,
        "remaining_fraction": 1.0,
        "leverage": 2,
        "stop_price": 98.8,
        "take_profit": 104.0,
        "take_profit_levels": [],
        "entry_time": pd.Timestamp("2026-03-01 00:00:00"),
        "signal_score": 0.91,
        "signal_type_1h": "red_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "long_dual_support",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
    }
    analysis = {
        "signal": _signal(),
        "row_15m": pd.Series({"open": 100.3, "high": 100.9, "low": 100.55, "close": 100.7, "atr": 0.2}),
        "price": 100.7,
        "time": pd.Timestamp("2026-03-01 00:15:00"),
    }

    closed = engine.check_stops("SOLUSDT", analysis)

    assert closed is False
    assert engine.positions["SOLUSDT"]["trailing_stop"] == pytest.approx(100.4964, rel=1e-9)
    assert engine.positions["SOLUSDT"]["stop_price"] == pytest.approx(100.4964, rel=1e-9)


def test_check_stops_applies_partial_4h_shrink_loss_mitigation() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
    )
    strategy_config = MACDStrategyV2Config(
        enable_4h_shrink_exit=True,
        exit_4h_require_profit=False,
        exit_4h_weak_loss_threshold=-1.0,
        shrink_exit_loss_mitigation_enabled=True,
        shrink_exit_loss_mitigation_pnl_threshold=-0.005,
        shrink_exit_loss_mitigation_exit_ratio=0.60,
        shrink_exit_loss_mitigation_ignore_if_pnl_gt=0.01,
    )
    engine = BacktestEngine(config, strategy_config, runtime_config={})
    entry_time = pd.Timestamp("2026-03-01 00:00:00")
    engine.positions["SOLUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 1600.0,
        "position_value": 800.0,
        "margin": 800.0,
        "initial_margin": 800.0,
        "remaining_fraction": 1.0,
        "leverage": 2,
        "stop_price": 98.8,
        "take_profit": 104.0,
        "take_profit_levels": [],
        "entry_time": entry_time,
        "signal_score": 0.91,
        "signal_type_1h": "green_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.18,
        "vwap_state": "short_dual_pressure",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
        "tp_levels_filled": 0,
        "signal": _signal(signal_type_1h="green_bar_growing", vwap_state="short_dual_pressure"),
    }
    engine.positions["SOLUSDT"]["signal"].direction = "neutral"
    engine.positions["SOLUSDT"]["signal"].details = {
        "shrink_exit_direction": "long",
        "shrink_exit_ready": True,
    }
    analysis = {
        "signal": engine.positions["SOLUSDT"]["signal"],
        "row_15m": pd.Series({"open": 99.5, "high": 99.6, "low": 99.2, "close": 99.4}),
        "price": 99.4,
        "time": pd.Timestamp("2026-03-01 00:15:00"),
    }

    closed = engine.check_stops("SOLUSDT", analysis)

    assert closed is False
    assert "SOLUSDT" in engine.positions
    assert engine.positions["SOLUSDT"]["remaining_fraction"] == pytest.approx(0.4, rel=1e-9)
    assert engine.trades[-1]["reason"] == "4h_shrink_reduce"


def test_same_bar_tp1_before_stop_mode_realizes_tp1_then_stops_remainder() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        same_bar_tp_priority_mode="tp1_before_stop",
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.positions["SOLUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 2000.0,
        "position_value": 400.0,
        "margin": 400.0,
        "initial_margin": 400.0,
        "remaining_fraction": 1.0,
        "leverage": 5,
        "stop_price": 99.0,
        "take_profit": 104.0,
        "take_profit_levels": [
            {"price": 100.8, "reduce_pct": 0.25, "filled": False},
            {"price": 101.2, "reduce_pct": 0.30, "filled": False},
            {"price": 102.0, "reduce_pct": 0.20, "filled": False},
        ],
        "entry_time": pd.Timestamp("2026-03-01 00:00:00"),
        "signal_score": 0.91,
        "signal_type_1h": "green_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "short_retest_reject",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
    }
    analysis = {
        "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
        "row_15m": pd.Series({"open": 100.1, "high": 101.3, "low": 98.9, "close": 99.4}),
        "price": 99.4,
        "time": pd.Timestamp("2026-03-01 00:15:00"),
    }

    closed = engine.check_stops("SOLUSDT", analysis)

    assert closed is True
    assert "SOLUSDT" not in engine.positions
    assert len(engine.trades) == 2
    assert engine.trades[0]["reason"] == "take_profit_level_intrabar"
    assert engine.trades[0]["pnl_pct"] == pytest.approx(0.8, rel=1e-9)
    assert engine.trades[1]["reason"] == "stop_loss_intrabar_after_tp1_same_bar"


def test_same_bar_stop_first_mode_preserves_existing_behavior() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        same_bar_tp_priority_mode="stop_first",
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.positions["SOLUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 2000.0,
        "position_value": 400.0,
        "margin": 400.0,
        "initial_margin": 400.0,
        "remaining_fraction": 1.0,
        "leverage": 5,
        "stop_price": 99.0,
        "take_profit": 104.0,
        "take_profit_levels": [
            {"price": 100.8, "reduce_pct": 0.25, "filled": False},
            {"price": 101.2, "reduce_pct": 0.30, "filled": False},
        ],
        "entry_time": pd.Timestamp("2026-03-01 00:00:00"),
        "signal_score": 0.91,
        "signal_type_1h": "green_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "short_retest_reject",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
    }
    analysis = {
        "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
        "row_15m": pd.Series({"open": 100.1, "high": 101.3, "low": 98.9, "close": 99.4}),
        "price": 99.4,
        "time": pd.Timestamp("2026-03-01 00:15:00"),
    }

    closed = engine.check_stops("SOLUSDT", analysis)

    assert closed is True
    assert "SOLUSDT" not in engine.positions
    assert len(engine.trades) == 1
    assert engine.trades[0]["reason"] == "stop_loss_intrabar"


def test_process_pending_orders_applies_entry_bar_same_bar_tp1_before_stop_after_fill() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        entry_bar_same_bar_enabled=True,
        same_bar_tp_priority_mode="tp1_before_stop",
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.pending_orders["SOLUSDT"] = _pending_order()

    filled = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
                "row_15m": pd.Series({"open": 100.0, "high": 101.3, "low": 98.9, "close": 99.4}),
                "time": pd.Timestamp("2026-03-01 00:15:00"),
                "price": 99.4,
            }
        }
    )

    assert "SOLUSDT" in filled
    assert "SOLUSDT" not in engine.positions
    assert len(engine.trades) == 2
    assert engine.trades[0]["reason"] == "take_profit_level_intrabar"
    assert engine.trades[1]["reason"] == "stop_loss_intrabar_after_tp1_same_bar"


def test_process_pending_orders_without_entry_bar_same_bar_keeps_new_fill_open() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        entry_bar_same_bar_enabled=False,
        same_bar_tp_priority_mode="tp1_before_stop",
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.pending_orders["SOLUSDT"] = _pending_order()

    filled = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
                "row_15m": pd.Series({"open": 100.0, "high": 101.3, "low": 98.9, "close": 99.4}),
                "time": pd.Timestamp("2026-03-01 00:15:00"),
                "price": 99.4,
            }
        }
    )

    assert "SOLUSDT" in filled
    assert "SOLUSDT" in engine.positions
    assert len(engine.trades) == 0


def test_process_pending_orders_stop_first_preserves_conservative_fill_bar_branch() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        entry_bar_same_bar_enabled=True,
        same_bar_tp_priority_mode="stop_first",
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.pending_orders["SOLUSDT"] = _pending_order()

    filled = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
                "row_15m": pd.Series({"open": 100.0, "high": 101.3, "low": 98.9, "close": 99.4}),
                "time": pd.Timestamp("2026-03-01 00:15:00"),
                "price": 99.4,
            }
        }
    )

    assert "SOLUSDT" in filled
    assert "SOLUSDT" not in engine.positions
    assert len(engine.trades) == 1
    assert engine.trades[0]["reason"] == "stop_loss_intrabar"


def test_build_backtest_config_prefers_nested_pending_order_settings() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "fund_flow": {
            "backtest": {
                "entry_time_in_force": "GTC",
                "gtc_expire_bars": 4,
                "gtc_cancel_on_signal_reversal": False,
            }
        },
    }

    config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path="config/trading_config_fund_flow.json",
    )

    assert config.entry_time_in_force == "GTC"
    assert config.gtc_expire_bars == 4
    assert config.gtc_cancel_on_signal_reversal is False


def test_process_pending_orders_gtc_preserves_runtime_like_open_order_on_signal_reversal() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        entry_time_in_force="GTC",
        gtc_expire_bars=3,
        gtc_cancel_on_signal_reversal=False,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.pending_orders["SOLUSDT"] = _pending_order(side="long")

    filled = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="short", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
                "row_15m": pd.Series({"open": 101.5, "high": 101.8, "low": 101.1, "close": 101.4}),
                "time": pd.Timestamp("2026-03-01 00:15:00"),
                "price": 101.4,
            }
        }
    )

    assert filled == set()
    assert "SOLUSDT" in engine.pending_orders
    assert engine.pending_orders["SOLUSDT"]["bars_waited"] == 1


def test_process_pending_orders_gtc_can_keep_legacy_reversal_cancel_when_enabled() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        entry_time_in_force="GTC",
        gtc_expire_bars=3,
        gtc_cancel_on_signal_reversal=True,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.pending_orders["SOLUSDT"] = _pending_order(side="long")

    filled = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="short", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
                "row_15m": pd.Series({"open": 101.5, "high": 101.8, "low": 101.1, "close": 101.4}),
                "time": pd.Timestamp("2026-03-01 00:15:00"),
                "price": 101.4,
            }
        }
    )

    assert filled == set()
    assert "SOLUSDT" not in engine.pending_orders


def test_build_backtest_config_reads_nested_open_gtc_fallback_enabled() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "fund_flow": {
            "backtest": {
                "open_gtc_fallback_enabled": False,
            }
        },
    }

    config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path="config/trading_config_fund_flow.json",
    )

    assert config.open_gtc_fallback_enabled is False


def test_build_backtest_config_uses_24h_warmup_data_window() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "fund_flow": {
            "backtest": {
                "warmup_hours": 24,
            }
        },
    }

    config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path="config/trading_config_fund_flow.json",
        window_start_iso="2026-03-05T03:00:00",
        window_end_iso="2026-04-04T03:00:00",
    )

    assert config.window_start_iso == "2026-03-05T03:00:00"
    assert config.window_end_iso == "2026-04-04T03:00:00"
    assert config.warmup_hours == 24
    assert config.data_window_start_iso == "2026-03-04 03:00:00"
    assert config.data_window_end_iso == "2026-04-04T03:00:00"


def test_run_backtest_skips_trade_execution_during_warmup_window() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        window_start_iso="2026-03-05T03:00:00",
        window_end_iso="2026-03-05T03:30:00",
        data_window_start_iso="2026-03-04 03:00:00",
        data_window_end_iso="2026-03-05T03:30:00",
        warmup_hours=24,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})

    executed_times: list[pd.Timestamp] = []

    def _fake_analyze_bar(symbol: str, data: dict, idx_15m: int):
        row = data["15m"].iloc[idx_15m]
        return {
            "signal": _signal(direction="long"),
            "time": row["timestamp"],
            "price": float(row["close"]),
            "row_15m": row,
            "row_1h": data["1h"].iloc[min(idx_15m, len(data["1h"]) - 1)],
            "row_4h": data["4h"].iloc[min(idx_15m, len(data["4h"]) - 1)],
            "cvd_veto_context": {},
            "cvd_context": {},
        }

    engine.analyze_bar = _fake_analyze_bar  # type: ignore[assignment]
    engine.process_pending_orders = lambda analyses: set()  # type: ignore[assignment]
    engine.check_stops = lambda symbol, analysis: False  # type: ignore[assignment]
    engine.execute_trade = lambda symbol, analysis, data: executed_times.append(pd.Timestamp(analysis["time"]))  # type: ignore[assignment]

    timestamps = pd.date_range("2026-03-04 14:30:00", periods=53, freq="15min")
    tf_15m = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0 + i for i in range(len(timestamps))],
            "high": [101.0 + i for i in range(len(timestamps))],
            "low": [99.0 + i for i in range(len(timestamps))],
            "close": [100.5 + i for i in range(len(timestamps))],
            "volume": [1000.0 for _ in range(len(timestamps))],
        }
    )
    tf_1h = tf_15m.copy()
    tf_4h = tf_15m.copy()

    engine.run_backtest({"SOLUSDT": {"15m": tf_15m, "1h": tf_1h, "4h": tf_4h}})

    assert executed_times == [
        pd.Timestamp("2026-03-05 03:00:00"),
        pd.Timestamp("2026-03-05 03:15:00"),
        pd.Timestamp("2026-03-05 03:30:00"),
    ]


def test_build_backtest_config_reads_direct_ioc_fill_model_settings() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "fund_flow": {
            "backtest": {
                "direct_ioc_fill_mode": "close_through_or_penetration",
                "direct_ioc_min_penetration_bps": 5.0,
            }
        },
    }

    config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path="config/trading_config_fund_flow.json",
    )

    assert config.direct_ioc_fill_mode == "close_through_or_penetration"
    assert config.direct_ioc_min_penetration_bps == pytest.approx(5.0, rel=1e-9)


def test_build_backtest_config_prefers_nested_entry_passive_offset_pct() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "fund_flow": {
            "entry_slippage": 0.003,
            "backtest": {
                "entry_passive_offset_pct": 0.0012,
            },
        },
    }

    config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path="config/trading_config_fund_flow.json",
    )

    assert config.entry_passive_offset_pct == pytest.approx(0.0012, rel=1e-9)


def test_build_backtest_config_reads_nested_passive_pricing_model() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "fund_flow": {
            "backtest": {
                "passive_pricing": {
                    "atr_fraction": 0.25,
                    "min_offset_pct": 0.0009,
                    "max_offset_pct": 0.0045,
                    "signal_type_multipliers": {"flip_bullish": 0.85},
                    "vwap_state_multipliers": {"long_reclaim_confirmed": 1.2},
                }
            },
        },
    }

    config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path="config/trading_config_fund_flow.json",
    )

    assert config.entry_passive_pricing_atr_fraction == pytest.approx(0.25, rel=1e-9)
    assert config.entry_passive_pricing_min_offset_pct == pytest.approx(0.0009, rel=1e-9)
    assert config.entry_passive_pricing_max_offset_pct == pytest.approx(0.0045, rel=1e-9)
    assert config.entry_passive_pricing_signal_type_multipliers["flip_bullish"] == pytest.approx(0.85, rel=1e-9)
    assert config.entry_passive_pricing_vwap_state_multipliers["long_reclaim_confirmed"] == pytest.approx(1.2, rel=1e-9)


def test_resolve_entry_limit_price_uses_atr_floor_and_state_multiplier() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        entry_passive_offset_pct=0.0015,
        entry_passive_pricing_atr_fraction=0.25,
        entry_passive_pricing_min_offset_pct=0.0010,
        entry_passive_pricing_max_offset_pct=0.0060,
        entry_passive_pricing_signal_type_multipliers={"flip_bullish": 0.8},
        entry_passive_pricing_vwap_state_multipliers={"long_reclaim_confirmed": 1.5},
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    analysis = {
        "price": 100.0,
        "row_1h": pd.Series({"atr": 1.2}),
        "signal": _signal(direction="long", signal_type_1h="flip_bullish", vwap_state="long_reclaim_confirmed"),
    }

    limit_price = engine._resolve_entry_limit_price_from_analysis(analysis)

    # base 0.15% * 0.8 * 1.5 = 0.18%; atr floor 1.2% * 0.25 = 0.30%; final uses atr floor
    assert limit_price == pytest.approx(99.7, rel=1e-9)


def test_resolve_entry_limit_price_clamps_to_max_offset_for_short() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        entry_passive_offset_pct=0.0015,
        entry_passive_pricing_atr_fraction=0.5,
        entry_passive_pricing_min_offset_pct=0.0010,
        entry_passive_pricing_max_offset_pct=0.0040,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    analysis = {
        "price": 100.0,
        "row_1h": pd.Series({"atr": 3.0}),
        "signal": _signal(direction="short", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
    }

    limit_price = engine._resolve_entry_limit_price_from_analysis(analysis)

    assert limit_price == pytest.approx(100.4, rel=1e-9)


def test_process_pending_orders_ioc_would_take_immediately_degrades_to_gtc_without_fill() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        entry_time_in_force="IOC",
        open_gtc_fallback_enabled=True,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.pending_orders["SOLUSDT"] = _pending_order(time_in_force="IOC")

    filled = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
                "row_15m": pd.Series({"open": 99.8, "high": 100.4, "low": 99.6, "close": 100.1}),
                "time": pd.Timestamp("2026-03-01 00:15:00"),
                "price": 100.1,
            }
        }
    )

    assert filled == set()
    assert "SOLUSDT" in engine.pending_orders
    assert engine.pending_orders["SOLUSDT"]["time_in_force"] == "GTC"
    assert engine.pending_orders["SOLUSDT"]["bars_waited"] == 1
    assert engine.pending_orders["SOLUSDT"]["entry_degradation_path"][-1]["step"] == "ioc_to_gtc_fallback"
    assert "SOLUSDT" not in engine.positions
    assert len(engine.trades) == 0


def test_process_pending_orders_ioc_degraded_gtc_can_fill_on_later_bar() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        entry_time_in_force="IOC",
        open_gtc_fallback_enabled=True,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.pending_orders["SOLUSDT"] = _pending_order(time_in_force="IOC")

    first = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
                "row_15m": pd.Series({"open": 99.8, "high": 100.4, "low": 99.6, "close": 100.1}),
                "time": pd.Timestamp("2026-03-01 00:15:00"),
                "price": 100.1,
            }
        }
    )
    second = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
                "row_15m": pd.Series({"open": 99.7, "high": 100.2, "low": 99.5, "close": 99.9}),
                "time": pd.Timestamp("2026-03-01 00:30:00"),
                "price": 99.9,
            }
        }
    )

    assert first == set()
    assert second == {"SOLUSDT"}
    assert "SOLUSDT" in engine.positions
    assert engine.positions["SOLUSDT"]["entry_price"] == pytest.approx(99.7, rel=1e-9)
    assert engine.positions["SOLUSDT"]["entry_initial_time_in_force"] == "IOC"
    assert engine.positions["SOLUSDT"]["entry_time_in_force"] == "GTC"
    assert engine.positions["SOLUSDT"]["entry_degradation_path"][-1]["step"] == "ioc_to_gtc_fallback"


def test_process_pending_orders_ioc_would_take_immediately_can_cancel_without_gtc_fallback() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        entry_time_in_force="IOC",
        open_gtc_fallback_enabled=False,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.pending_orders["SOLUSDT"] = _pending_order(time_in_force="IOC")

    filled = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
                "row_15m": pd.Series({"open": 99.8, "high": 100.4, "low": 99.6, "close": 100.1}),
                "time": pd.Timestamp("2026-03-01 00:15:00"),
                "price": 100.1,
            }
        }
    )

    assert filled == set()
    assert "SOLUSDT" not in engine.pending_orders


def test_process_pending_orders_ioc_strict_fill_rejects_wick_only_touch() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        entry_time_in_force="IOC",
        direct_ioc_fill_mode="close_through_or_penetration",
        direct_ioc_min_penetration_bps=5.0,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.pending_orders["SOLUSDT"] = _pending_order(time_in_force="IOC")

    filled = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
                "row_15m": pd.Series({"open": 100.3, "high": 100.4, "low": 99.99, "close": 100.2}),
                "time": pd.Timestamp("2026-03-01 00:15:00"),
                "price": 100.2,
            }
        }
    )

    assert filled == set()
    assert "SOLUSDT" not in engine.pending_orders
    assert "SOLUSDT" not in engine.positions


def test_process_pending_orders_ioc_strict_fill_accepts_close_through() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        entry_time_in_force="IOC",
        direct_ioc_fill_mode="close_through_or_penetration",
        direct_ioc_min_penetration_bps=5.0,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.pending_orders["SOLUSDT"] = _pending_order(time_in_force="IOC")

    filled = engine.process_pending_orders(
        {
            "SOLUSDT": {
                "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
                "row_15m": pd.Series({"open": 100.3, "high": 100.4, "low": 99.92, "close": 99.95}),
                "time": pd.Timestamp("2026-03-01 00:15:00"),
                "price": 99.95,
            }
        }
    )

    assert filled == {"SOLUSDT"}
    assert engine.positions["SOLUSDT"]["entry_price"] == pytest.approx(100.0, rel=1e-9)
    assert engine.positions["SOLUSDT"]["entry_fill_wick_only_touch"] is False
    assert engine.positions["SOLUSDT"]["entry_fill_close_through"] is True


def test_partial_aware_breakeven_delays_trigger_before_any_partial() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=True,
        breakeven_trigger_pnl_ratio=0.012,
        breakeven_lock_ratio=0.0025,
        partial_aware_breakeven_enabled=True,
        partial_aware_no_partial_trigger_pnl_ratio=0.015,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.positions["SOLUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 2000.0,
        "position_value": 400.0,
        "margin": 400.0,
        "initial_margin": 400.0,
        "remaining_fraction": 1.0,
        "leverage": 5,
        "stop_price": 98.0,
        "take_profit": 104.0,
        "take_profit_levels": [
            {"price": 100.8, "reduce_pct": 0.25, "filled": False},
            {"price": 101.2, "reduce_pct": 0.30, "filled": False},
        ],
        "entry_time": pd.Timestamp("2026-03-01 00:00:00"),
        "signal_score": 0.91,
        "signal_type_1h": "green_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "short_retest_reject",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
        "breakeven_trigger_pnl_ratio": 0.012,
        "breakeven_lock_ratio": 0.0025,
    }
    analysis = {
        "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
        "row_15m": pd.Series({"open": 100.2, "high": 101.3, "low": 100.5, "close": 101.0}),
        "price": 101.0,
        "time": pd.Timestamp("2026-03-01 00:15:00"),
    }

    closed = engine.check_stops("SOLUSDT", analysis)

    assert closed is False
    assert engine.positions["SOLUSDT"]["stop_price"] == pytest.approx(98.0, rel=1e-9)


def test_partial_aware_breakeven_reverts_to_base_after_partial() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=True,
        breakeven_trigger_pnl_ratio=0.012,
        breakeven_lock_ratio=0.0025,
        partial_aware_breakeven_enabled=True,
        partial_aware_no_partial_trigger_pnl_ratio=0.015,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    engine.positions["SOLUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 2000.0,
        "position_value": 300.0,
        "margin": 300.0,
        "initial_margin": 400.0,
        "remaining_fraction": 0.75,
        "leverage": 5,
        "stop_price": 98.0,
        "take_profit": 104.0,
        "take_profit_levels": [
            {"price": 100.8, "reduce_pct": 0.25, "filled": True},
            {"price": 101.2, "reduce_pct": 0.30, "filled": False},
        ],
        "entry_time": pd.Timestamp("2026-03-01 00:00:00"),
        "signal_score": 0.91,
        "signal_type_1h": "green_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "short_retest_reject",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
        "breakeven_trigger_pnl_ratio": 0.012,
        "breakeven_lock_ratio": 0.0025,
    }
    analysis = {
        "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
        "row_15m": pd.Series({"open": 100.2, "high": 101.3, "low": 100.5, "close": 101.0}),
        "price": 101.0,
        "time": pd.Timestamp("2026-03-01 00:15:00"),
    }

    closed = engine.check_stops("SOLUSDT", analysis)

    assert closed is False
    assert engine.positions["SOLUSDT"]["stop_price"] == pytest.approx(100.25, rel=1e-9)


def test_runner_only_trailing_blocks_trailing_before_any_partial() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        trailing_stop_enabled=True,
        runner_only_trailing_enabled=True,
        runner_only_trailing_min_completed_levels=1,
    )
    runtime_cfg = {
        "fund_flow": {
            "trailing_stop_profiles": {
                "trend": {
                    "activation_pnl_ratio": 0.006,
                    "atr_multiplier": 0.5,
                    "min_distance_pct": 0.004,
                    "max_distance_pct": 0.008,
                }
            },
            "trailing_stop_profile_map": {
                "_default": "trend",
            },
        }
    }
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config=runtime_cfg)
    engine.positions["SOLUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 1600.0,
        "position_value": 800.0,
        "margin": 800.0,
        "initial_margin": 800.0,
        "remaining_fraction": 1.0,
        "leverage": 2,
        "stop_price": 98.8,
        "take_profit": 104.0,
        "take_profit_levels": [
            {"price": 100.8, "reduce_pct": 0.25, "filled": False},
        ],
        "entry_time": pd.Timestamp("2026-03-01 00:00:00"),
        "signal_score": 0.91,
        "signal_type_1h": "green_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "short_retest_reject",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
    }
    analysis = {
        "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
        "row_15m": pd.Series({"open": 100.3, "high": 100.9, "low": 100.55, "close": 100.7, "atr": 0.2}),
        "price": 100.7,
        "time": pd.Timestamp("2026-03-01 00:15:00"),
    }

    closed = engine.check_stops("SOLUSDT", analysis)

    assert closed is False
    assert engine.positions["SOLUSDT"]["stop_price"] == pytest.approx(98.8, rel=1e-9)
    assert not engine.positions["SOLUSDT"].get("trailing_stop")


def test_runner_only_trailing_allows_trailing_after_partial() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        breakeven_enabled=False,
        trailing_stop_enabled=True,
        runner_only_trailing_enabled=True,
        runner_only_trailing_min_completed_levels=1,
    )
    runtime_cfg = {
        "fund_flow": {
            "trailing_stop_profiles": {
                "trend": {
                    "activation_pnl_ratio": 0.006,
                    "atr_multiplier": 0.5,
                    "min_distance_pct": 0.004,
                    "max_distance_pct": 0.008,
                }
            },
            "trailing_stop_profile_map": {
                "_default": "trend",
            },
        }
    }
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config=runtime_cfg)
    engine.positions["SOLUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 1200.0,
        "position_value": 600.0,
        "margin": 600.0,
        "initial_margin": 800.0,
        "remaining_fraction": 0.75,
        "leverage": 2,
        "stop_price": 98.8,
        "take_profit": 104.0,
        "take_profit_levels": [
            {"price": 100.8, "reduce_pct": 0.25, "filled": True},
            {"price": 101.2, "reduce_pct": 0.30, "filled": False},
        ],
        "entry_time": pd.Timestamp("2026-03-01 00:00:00"),
        "signal_score": 0.91,
        "signal_type_1h": "green_bar_growing",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.16,
        "vwap_state": "short_retest_reject",
        "vwap_location_score": 0.7,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
    }
    analysis = {
        "signal": _signal(direction="long", signal_type_1h="green_bar_growing", vwap_state="short_retest_reject"),
        "row_15m": pd.Series({"open": 100.3, "high": 100.9, "low": 100.55, "close": 100.7, "atr": 0.2}),
        "price": 100.7,
        "time": pd.Timestamp("2026-03-01 00:15:00"),
    }

    closed = engine.check_stops("SOLUSDT", analysis)

    assert closed is False
    assert engine.positions["SOLUSDT"]["trailing_stop"] == pytest.approx(100.4964, rel=1e-9)
    assert engine.positions["SOLUSDT"]["stop_price"] == pytest.approx(100.4964, rel=1e-9)
