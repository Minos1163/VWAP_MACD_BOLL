import pandas as pd
import pytest
from pathlib import Path

from scripts.backtest_macd_v2 import BacktestConfig, BacktestEngine, build_backtest_config, load_symbol_data
from src.fund_flow.macd_strategy_v2 import MACDSignalV2, MACDStrategyV2Config


def _runtime_cfg_with_fet_campaign() -> dict:
    return {
        "fund_flow": {
            "macd_mtf_strategy_v2": {
                "symbol_timed_campaigns": [
                    {
                        "symbol": "FETUSDT",
                        "exclusive": True,
                        "active_start": "2026-03-28 22:00:00",
                        "active_end": "2026-03-30 19:00:00",
                        "stages": [
                            {
                                "side": "long",
                                "entry_start": "2026-03-29 22:30:00",
                                "entry_end": "2026-03-29 23:30:00",
                                "exit_start": "2026-03-30 07:30:00",
                                "exit_end": "2026-03-30 08:30:00",
                                "min_return_15m": 0.005,
                                "min_return_1h": 0.0,
                                "return_lookback_bars_15m": 2,
                            },
                            {
                                "side": "short",
                                "entry_start": "2026-03-30 08:30:00",
                                "entry_end": "2026-03-30 09:30:00",
                                "exit_start": "2026-03-30 18:00:00",
                                "exit_end": "2026-03-30 19:00:00",
                                "min_return_15m": -0.005,
                                "min_return_1h": 0.0,
                                "return_lookback_bars_15m": 2,
                            },
                        ]
                    }
                ]
            }
        }
    }


def _neutral_signal() -> MACDSignalV2:
    return MACDSignalV2(direction="neutral", signal_score=0.0)


def test_symbol_timed_campaign_forces_long_then_short_sequence() -> None:
    engine = BacktestEngine(
        BacktestConfig(symbols=["FETUSDT"], initial_capital=10000.0),
        MACDStrategyV2Config(),
        runtime_config=_runtime_cfg_with_fet_campaign(),
    )

    campaign = engine._resolve_symbol_timed_campaign("FETUSDT")
    assert campaign is not None
    assert campaign["stages"][0]["side"] == "long"
    assert campaign["stages"][1]["side"] == "short"

    long_signal = engine._apply_symbol_timed_campaign_entry_signal(
        symbol="FETUSDT",
        current_time=pd.Timestamp("2026-03-29 23:00:00"),
        row_15m=pd.Series({"close": 1.0055}),
        prev_close_15m=1.0,
        row_1h=pd.Series({"close": 1.012}),
        prev_close_1h=1.0,
        signal=_neutral_signal(),
    )
    assert long_signal.direction == "long"
    assert long_signal.details["timed_campaign_stage_index"] == 0

    engine._mark_symbol_timed_campaign_filled("FETUSDT", "long")

    blocked_short_signal = engine._apply_symbol_timed_campaign_entry_signal(
        symbol="FETUSDT",
        current_time=pd.Timestamp("2026-03-30 09:00:00"),
        row_15m=pd.Series({"close": 0.994}),
        prev_close_15m=1.0,
        row_1h=pd.Series({"close": 0.998}),
        prev_close_1h=1.0,
        signal=_neutral_signal(),
    )
    assert blocked_short_signal.direction == "neutral"

    engine._mark_symbol_timed_campaign_closed("FETUSDT", "long")

    short_signal = engine._apply_symbol_timed_campaign_entry_signal(
        symbol="FETUSDT",
        current_time=pd.Timestamp("2026-03-30 09:00:00"),
        row_15m=pd.Series({"close": 0.994}),
        prev_close_15m=1.0,
        row_1h=pd.Series({"close": 0.998}),
        prev_close_1h=1.0,
        signal=_neutral_signal(),
    )
    assert short_signal.direction == "short"
    assert short_signal.details["timed_campaign_stage_index"] == 1


def test_symbol_timed_campaign_supports_two_bar_15m_return_confirmation() -> None:
    engine = BacktestEngine(
        BacktestConfig(symbols=["FETUSDT"], initial_capital=10000.0),
        MACDStrategyV2Config(),
        runtime_config=_runtime_cfg_with_fet_campaign(),
    )

    signal = engine._apply_symbol_timed_campaign_entry_signal(
        symbol="FETUSDT",
        current_time=pd.Timestamp("2026-03-29 23:15:00"),
        row_15m=pd.Series({"close": 1.0055}),
        prev_close_15m=1.001,
        row_1h=pd.Series({"close": 1.002}),
        prev_close_1h=1.0,
        signal=_neutral_signal(),
        close_history_15m=[1.0, 1.001, 1.0055],
    )

    assert signal.direction == "long"


def test_symbol_timed_campaign_exclusive_mode_blocks_non_campaign_entries_inside_active_window() -> None:
    engine = BacktestEngine(
        BacktestConfig(symbols=["FETUSDT"], initial_capital=10000.0),
        MACDStrategyV2Config(),
        runtime_config=_runtime_cfg_with_fet_campaign(),
    )

    base_signal = MACDSignalV2(direction="short", signal_score=0.92, signal_type_1h="green_bar_growing")
    signal = engine._apply_symbol_timed_campaign_entry_signal(
        symbol="FETUSDT",
        current_time=pd.Timestamp("2026-03-29 07:30:00"),
        row_15m=pd.Series({"close": 0.99}),
        prev_close_15m=1.0,
        row_1h=pd.Series({"close": 0.99}),
        prev_close_1h=1.0,
        signal=base_signal,
    )

    assert signal.direction == "neutral"
    assert signal.details["timed_campaign_reason"] == "timed_campaign_wait_entry"


def test_symbol_timed_campaign_forces_exit_and_advances_stage() -> None:
    engine = BacktestEngine(
        BacktestConfig(symbols=["FETUSDT"], initial_capital=10000.0),
        MACDStrategyV2Config(),
        runtime_config=_runtime_cfg_with_fet_campaign(),
    )
    entry_time = pd.Timestamp("2026-03-29 23:15:00")
    engine.positions["FETUSDT"] = {
        "side": "long",
        "entry_price": 1.0,
        "entry_notional": 2000.0,
        "position_value": 1000.0,
        "margin": 1000.0,
        "initial_margin": 1000.0,
        "remaining_fraction": 1.0,
        "leverage": 2,
        "stop_price": 0.95,
        "take_profit": None,
        "take_profit_levels": [],
        "entry_time": entry_time,
        "signal_score": 0.98,
        "signal_type_1h": "timed_campaign_long",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.20,
        "vwap_state": "timed_campaign",
        "vwap_location_score": 0.0,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
    }
    engine.symbol_timed_campaign_states["FETUSDT"]["stage_index"] = 1
    analysis = {
        "signal": _neutral_signal(),
        "row_15m": pd.Series({"high": 1.05, "low": 1.03}),
        "price": 1.04,
        "time": pd.Timestamp("2026-03-30 08:00:00"),
    }

    closed = engine._check_symbol_timed_campaign_exit("FETUSDT", analysis)

    assert closed is True
    assert engine.trades[-1]["reason"] == "timed_campaign_exit_long"
    assert engine.symbol_timed_campaign_states["FETUSDT"]["stage_index"] == 2


def test_load_symbol_data_prefers_newer_csv_over_stale_parquet(tmp_path) -> None:
    for tf in ("15m", "1h", "4h"):
        old_df = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-03-29 10:00:00"),
                    "open": 1.0,
                    "high": 1.1,
                    "low": 0.9,
                    "close": 1.05,
                    "volume": 1000.0,
                    "taker_buy_base": 500.0,
                }
            ]
        )
        new_df = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-04-04 03:15:00"),
                    "open": 2.0,
                    "high": 2.1,
                    "low": 1.9,
                    "close": 2.05,
                    "volume": 2000.0,
                    "taker_buy_base": 1100.0,
                }
            ]
        )
        old_df.to_parquet(tmp_path / f"FETUSDT_{tf}_60d_20260329.parquet", index=False)
        new_df.to_csv(tmp_path / f"FETUSDT_{tf}_60d.csv", index=False)

    loaded = load_symbol_data(str(tmp_path), "FETUSDT", MACDStrategyV2Config())

    assert loaded is not None
    assert loaded["15m"]["timestamp"].max() == pd.Timestamp("2026-04-04 03:15:00")


def test_load_symbol_data_includes_decision_timeframe_when_requested(tmp_path) -> None:
    for tf in ("5m", "15m", "1h", "4h"):
        df = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-04-04 03:15:00"),
                    "open": 2.0,
                    "high": 2.1,
                    "low": 1.9,
                    "close": 2.05,
                    "volume": 2000.0,
                    "taker_buy_base": 1100.0,
                }
            ]
        )
        df.to_csv(tmp_path / f"FETUSDT_{tf}_60d.csv", index=False)

    loaded = load_symbol_data(
        str(tmp_path),
        "FETUSDT",
        MACDStrategyV2Config(),
        decision_timeframe="5m",
    )

    assert loaded is not None
    assert "5m" in loaded
    assert loaded["5m"]["timestamp"].max() == pd.Timestamp("2026-04-04 03:15:00")


def test_build_backtest_config_tracks_decision_timeframe() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["BTCUSDT"]},
        "fund_flow": {
            "decision_timeframe": "5m",
            "max_active_symbols": 4,
        },
    }

    config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path="config/test.json",
    )

    assert config.decision_timeframe == "5m"


def test_bot_like_run_backtest_passes_decision_timeframe_to_load_symbol_data(monkeypatch) -> None:
    import scripts.backtest_fund_flow_bot_like as bot_like

    captured: list[str] = []

    runtime_cfg = {
        "trading": {"symbols": ["BTCUSDT"]},
        "fund_flow": {"decision_timeframe": "5m", "max_active_symbols": 1},
    }

    monkeypatch.setattr(bot_like, "apply_backtest_profile", lambda runtime_cfg, profile_name: (runtime_cfg, profile_name or ""))
    monkeypatch.setattr(bot_like, "build_strategy_config", lambda runtime_cfg: MACDStrategyV2Config())
    monkeypatch.setattr(
        bot_like,
        "build_backtest_config",
        lambda **kwargs: BacktestConfig(
            symbols=["BTCUSDT"],
            config_path="config/test.json",
            decision_timeframe="5m",
            window_start_iso="2026-03-05T03:00:00",
            window_end_iso="2026-04-04T03:00:00",
            data_window_start_iso="2026-03-04 03:00:00",
            data_window_end_iso="2026-04-04T03:00:00",
        ),
    )
    monkeypatch.setattr(
        bot_like,
        "load_symbol_data",
        lambda data_dir, symbol, strategy_config, decision_timeframe="15m": captured.append(decision_timeframe) or None,
    )

    class _StubEngine:
        def __init__(self, config, strategy_config, runtime_cfg):
            self.trades = []
            self.equity_curve = []
            self.ai_advice_logs = []
            self.candidate_ledger_rows = []
            self.capital = 10000.0
            self.max_drawdown_value = 0.0
            self.max_drawdown_pct = 0.0
            self.max_drawdown_start_time = ""
            self.max_drawdown_trough_time = ""
            self.max_drawdown_recovery_time = ""

    monkeypatch.setattr(bot_like, "BotLikeReplayEngine", _StubEngine)
    monkeypatch.setattr(bot_like, "build_backtest_summary", lambda **kwargs: {"return_pct": 0.0, "total_trades": 0, "win_rate_pct": 0.0, "risk_metrics": {"max_drawdown_pct": 0.0}})
    monkeypatch.setattr(Path, "read_text", lambda self, encoding="utf-8": "{}")
    monkeypatch.setattr(bot_like.json, "loads", lambda _text: runtime_cfg)

    bot_like.run_backtest(config_path="config/test.json", start_time="2026-03-05T03:00:00", end_time="2026-04-04T03:00:00")

    assert captured == ["5m"]


def test_timed_campaign_pending_order_fills_next_bar_open_even_if_followup_signal_is_neutral() -> None:
    engine = BacktestEngine(
        BacktestConfig(symbols=["FETUSDT"], initial_capital=10000.0),
        MACDStrategyV2Config(),
        runtime_config=_runtime_cfg_with_fet_campaign(),
    )
    engine.pending_orders["FETUSDT"] = {
        "side": "long",
        "limit_price": 1.0,
        "margin": 1000.0,
        "position_value": 1000.0,
        "leverage": 2,
        "stop_price": 0.95,
        "take_profit": None,
        "take_profit_levels": [],
        "signal_tp_trailing": None,
        "dynamic_position_mult": 1.0,
        "submit_time": pd.Timestamp("2026-03-29 23:00:00"),
        "time_in_force": "IOC",
        "entry_initial_time_in_force": "IOC",
        "bars_waited": 0,
        "signal_score": 0.95,
        "signal_type_1h": "timed_campaign_long",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "effective_session_position_scale": 1.0,
        "watchlist_throttle_applied": False,
        "vwap_score": 0.18,
        "vwap_state": "timed_campaign",
        "vwap_location_score": 0.0,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "adx_1h": 0.0,
        "adx_4h": 0.0,
        "bb_middle_slope_1h": 0.0,
        "bb_middle_slope_4h": 0.0,
        "cvd_veto_state": "inactive",
        "cvd_veto_triggered": False,
        "cvd_veto_reason": "",
        "cvd_session_ratio": 0.0,
        "cvd_session_pressure": 0.0,
        "cvd_session_ratio_change": 0.0,
        "session_price_change": 0.0,
        "cvd_close_pos": 0.0,
        "cvd_upper_wick_ratio": 0.0,
        "cvd_structure_gap": 0.0,
        "cvd_bonus_state": "inactive",
        "cvd_bonus_multiplier": 1.0,
        "cvd_1h_delta_ratio": 0.0,
        "cvd_1h_pressure": 0.0,
        "cvd_15m_delta_ratio": 0.0,
        "cvd_15m_pressure": 0.0,
        "shrink_exit_direction": "",
        "shrink_exit_ready": False,
        "macd_4h_shrink_pct": 0.0,
        "macd_4h_shrink_bars": 0,
        "breakeven_trigger_pnl_ratio": 0.003,
        "breakeven_lock_ratio": 0.001,
        "position_scale_override": 1.0,
        "vwap_structure_override_applied": False,
        "entry_degradation_path": [],
        "timed_campaign_force_fill": True,
    }

    filled = engine.process_pending_orders(
        {
            "FETUSDT": {
                "signal": _neutral_signal(),
                "row_15m": pd.Series({"open": 1.01, "high": 1.02, "low": 1.0, "close": 1.015}),
                "time": pd.Timestamp("2026-03-29 23:15:00"),
            }
        }
    )

    assert "FETUSDT" in filled
    assert "FETUSDT" in engine.positions
    assert engine.positions["FETUSDT"]["entry_price"] == 1.01


def test_timed_campaign_position_ignores_take_profit_before_exit_window() -> None:
    engine = BacktestEngine(
        BacktestConfig(symbols=["FETUSDT"], initial_capital=10000.0),
        MACDStrategyV2Config(),
        runtime_config=_runtime_cfg_with_fet_campaign(),
    )
    engine.positions["FETUSDT"] = {
        "side": "long",
        "entry_price": 1.0,
        "entry_notional": 2000.0,
        "position_value": 1000.0,
        "margin": 1000.0,
        "initial_margin": 1000.0,
        "remaining_fraction": 1.0,
        "leverage": 2,
        "stop_price": 0.95,
        "take_profit": 1.02,
        "take_profit_levels": [{"price": 1.01, "reduce_pct": 0.25, "filled": False}],
        "entry_time": pd.Timestamp("2026-03-29 23:15:00"),
        "signal_score": 0.95,
        "signal_type_1h": "timed_campaign_long",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 1.0,
        "vwap_score": 0.18,
        "vwap_state": "timed_campaign",
        "vwap_location_score": 0.0,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
        "timed_campaign_hold_until_exit": True,
    }
    engine.symbol_timed_campaign_states["FETUSDT"]["stage_index"] = 1

    closed = engine.check_stops(
        "FETUSDT",
        {
            "signal": _neutral_signal(),
            "row_15m": pd.Series({"high": 1.03, "low": 0.99, "open": 1.0, "close": 1.025}),
            "price": 1.025,
            "time": pd.Timestamp("2026-03-30 00:00:00"),
        },
    )

    assert closed is False
    assert "FETUSDT" in engine.positions
