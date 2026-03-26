from types import SimpleNamespace

import pandas as pd

from scripts.backtest_macd_v2 import BacktestEngine, build_strategy_config
from src.fund_flow.macd_strategy_v2 import MACDSignalV2, VetoType


def _runtime_cfg_with_cvd_enabled() -> dict:
    return {
        "fund_flow": {
            "macd_mtf_strategy_v2": {
                "entry_filters": {
                    "enable_flip_bullish_cvd_context_filter": True,
                    "flip_bullish_max_cvd_upper_wick_ratio": 0.2,
                    "flip_bullish_min_cvd_1h_delta_ratio": 0.03,
                },
                "leverage_config": {
                    "use_cvd_bonus_filter": True,
                },
                "cvd_filter_config": {
                    "enabled": True,
                },
            }
        }
    }


def test_build_strategy_config_disables_cvd_decision_logic_by_default():
    strategy_config = build_strategy_config(_runtime_cfg_with_cvd_enabled())

    assert strategy_config.enable_flip_bullish_cvd_context_filter is False
    assert strategy_config.use_cvd_bonus_filter is False
    assert strategy_config.use_cvd_veto_filter is False


def test_analyze_bar_skips_cvd_veto_and_bonus_when_no_cvd_mode():
    engine = BacktestEngine.__new__(BacktestEngine)
    engine.disable_cvd_decision_logic = True
    engine.time_window_filter = SimpleNamespace(should_allow_entry=lambda **_kwargs: (True, ""))
    engine._find_tf_index = lambda _data, tf, _time: 5 if tf in {"1h", "4h"} else -1
    engine.get_macd_hist_series = lambda df: [0.1] * len(df)
    engine._strategy_engine_for_symbol = lambda _symbol: SimpleNamespace(
        analyze=lambda **_kwargs: MACDSignalV2(
            direction="long",
            signal_score=0.91,
            signal_type_1h="flip_bullish",
            entry_type_15m="red_bar_growing",
            entry_score_15m=0.35,
            vwap_score=0.16,
            vwap_deviation=0.001,
            vwap_state="long_dual_support",
            vwap_location_score=0.7,
            ema_multiplier=1.0,
            ema_structure_status="normal",
            veto_type=VetoType.NONE,
            details={},
        )
    )
    engine.build_cvd_veto_context = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("build_cvd_veto_context should not be called")
    )
    engine.build_cvd_bonus_context = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("build_cvd_bonus_context should not be called")
    )

    ts_15m = pd.date_range("2026-02-23 00:00:00", periods=8, freq="15min")
    ts_1h = pd.date_range("2026-02-22 19:00:00", periods=8, freq="1h")
    ts_4h = pd.date_range("2026-02-21 20:00:00", periods=8, freq="4h")
    data = {
        "15m": pd.DataFrame(
            {
                "timestamp": ts_15m,
                "close": [100.0] * 8,
                "volume": [10.0] * 8,
                "avg_volume": [5.0] * 8,
                "bb_middle": [100.0] * 8,
                "bb_upper": [101.0] * 8,
                "bb_lower": [99.0] * 8,
                "upper_wick_ratio": [0.6] * 8,
            }
        ),
        "1h": pd.DataFrame(
            {
                "timestamp": ts_1h,
                "close": [100.0] * 8,
                "vwap": [100.0] * 8,
                "structural_vwap": [100.0] * 8,
                "bb_middle": [100.0] * 8,
                "bb_upper": [101.0] * 8,
                "bb_lower": [99.0] * 8,
                "adx": [20.0] * 8,
                "atr": [1.0] * 8,
                "funding_rate": [0.0] * 8,
                "oi_delta_ratio": [0.0] * 8,
                "cvd_delta_ratio": [-0.2] * 8,
            }
        ),
        "4h": pd.DataFrame(
            {
                "timestamp": ts_4h,
                "close": [100.0] * 8,
                "bb_middle": [100.0] * 8,
                "bb_upper": [101.0] * 8,
                "bb_lower": [99.0] * 8,
                "adx": [20.0] * 8,
            }
        ),
    }

    analysis = BacktestEngine.analyze_bar(engine, "BTCUSDT", data, 5)

    assert analysis is not None
    assert analysis["signal"].direction == "long"
    assert analysis["cvd_veto_context"]["cvd_veto_state"] == "inactive"
    assert analysis["cvd_veto_context"]["cvd_veto_triggered"] is False
    assert analysis["cvd_context"]["cvd_bonus_state"] == "inactive"
    assert analysis["cvd_context"]["cvd_bonus_multiplier"] == 1.0


def test_execute_trade_uses_neutral_bonus_and_inactive_cvd_fields_in_no_cvd_mode():
    engine = BacktestEngine.__new__(BacktestEngine)
    engine.disable_cvd_decision_logic = True
    engine.config = SimpleNamespace(
        fixed_leverage=None,
        default_leverage=3,
        min_leverage=2,
        max_leverage=4,
        max_positions=3,
        default_target_portion=0.6,
        max_symbol_position_portion=0.6,
        min_open_portion=0.06,
        reserve_pct=0.2,
        fee_rate=0.0004,
        default_stop_loss_pct=0.02,
        default_take_profit_pct=0.04,
        take_profit_pct_levels=[],
        take_profit_reduce_pct_levels=[],
        entry_slippage=0.0015,
        entry_time_in_force="IOC",
    )
    engine.strategy_config = SimpleNamespace(preflip_trial_min_signal_score=0.78, resolve_signal_score_threshold=lambda *_args, **_kwargs: 0.85)
    engine.positions = {}
    engine.pending_orders = {}
    engine.capital = 10000.0
    engine._is_entry_cooldown_active = lambda _time: False
    engine._strategy_engine_for_symbol = lambda _symbol: SimpleNamespace(
        resolve_session_position_scale=lambda *_args, **_kwargs: 1.0,
        resolve_symbol_risk_session_scale=lambda *_args, **_kwargs: 1.0,
        is_watchlist_symbol=lambda _symbol: False,
    )
    captured = {}

    def fake_calculate_position_size(**kwargs):
        captured.update(kwargs)
        return 1200.0, 3

    engine.calculate_position_size = fake_calculate_position_size

    signal = MACDSignalV2(
        direction="long",
        signal_score=0.91,
        signal_type_1h="flip_bullish",
        entry_type_15m="red_bar_growing",
        entry_score_15m=0.35,
        vwap_score=0.16,
        vwap_deviation=0.001,
        vwap_state="long_dual_support",
        vwap_location_score=0.7,
        ema_multiplier=1.0,
        ema_structure_status="normal",
        veto_type=VetoType.NONE,
        details={},
    )
    analysis = {
        "signal": signal,
        "time": pd.Timestamp("2026-02-23 12:00:00"),
        "price": 100.0,
        "row_1h": pd.Series({"atr": 1.0}),
        "cvd_veto_context": {
            "cvd_veto_state": "bullish_continuation_risk",
            "cvd_veto_triggered": True,
            "cvd_veto_reason": "should_be_ignored",
            "cvd_session_ratio": 1.0,
        },
        "cvd_context": {
            "cvd_bonus_state": "bullish_flow",
            "cvd_bonus_multiplier": 0.3,
            "cvd_1h_delta_ratio": 1.0,
        },
    }

    BacktestEngine.execute_trade(engine, "BTCUSDT", analysis, {})

    assert captured["bonus_multiplier"] == 1.0
    order = engine.pending_orders["BTCUSDT"]
    assert order["cvd_veto_state"] == "inactive"
    assert order["cvd_veto_triggered"] is False
    assert order["cvd_veto_reason"] == ""
    assert order["cvd_bonus_state"] == "inactive"
    assert order["cvd_bonus_multiplier"] == 1.0
    assert order["cvd_session_ratio"] == 0.0
    assert order["cvd_1h_delta_ratio"] == 0.0
