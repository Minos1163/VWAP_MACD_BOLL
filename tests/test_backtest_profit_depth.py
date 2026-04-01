from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.backtest_macd_v2 import BacktestConfig, BacktestEngine
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
