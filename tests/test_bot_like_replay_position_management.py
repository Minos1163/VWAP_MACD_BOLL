import pandas as pd

from src.app.fund_flow_bot import TradingBot
from src.fund_flow.models import FundFlowDecision, Operation as FundFlowOperation
from scripts import backtest_fund_flow_bot_like as replay_module
from scripts.backtest_fund_flow_bot_like import BotLikeReplayEngine


def _make_bot():
    bot = TradingBot.__new__(TradingBot)
    bot.config = {
        "fund_flow": {
            "stop_loss_pct": 0.012,
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
            "time_exit_enabled": True,
            "time_exit_minutes": 30,
            "time_exit_min_profit_pct": 0.0035,
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
    bot._risk_state_path = "NUL"
    bot._save_risk_state = lambda: None
    return bot


def test_bot_like_replay_applies_position_management_overrides_for_existing_position():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.bot_logic = _make_bot()
    engine.positions = {
        "BTCUSDT": {
            "side": "LONG",
            "entry_price": 100.0,
            "amount": 1.0,
        }
    }

    base_decision = FundFlowDecision(
        operation=FundFlowOperation.HOLD,
        symbol="BTCUSDT",
        target_portion_of_balance=0.0,
        leverage=2,
        reason="hold",
        metadata={},
    )

    result = engine._apply_position_management_override(
        symbol="BTCUSDT",
        decision=base_decision,
        analysis={
            "price": 100.85,
            "flow_context": {"atr_pct": 0.017, "adx": 24.0, "cvd_momentum": 0.02},
        },
    )

    assert result.operation == FundFlowOperation.CLOSE
    assert result.target_portion_of_balance == 0.45
    assert result.reason == "partial_tp_level_1"


def test_bot_like_replay_clears_position_tracking_after_full_close(monkeypatch):
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.bot_logic = _make_bot()
    engine.positions = {
        "BTCUSDT": {
            "side": "long",
            "entry_price": 100.0,
            "amount": 1.0,
            "margin": 100.0,
        }
    }
    engine.trades = []
    pos_key = engine.bot_logic._position_track_key("BTCUSDT", "LONG")
    engine.bot_logic._position_first_seen_ts[pos_key] = 1000.0

    def fake_parent_close(self, symbol, price, time, reason, **kwargs):
        del price, time, reason, kwargs
        self.positions.pop(symbol, None)
        self.trades.append({"symbol": symbol, "reason": "closed"})

    monkeypatch.setattr(replay_module.BacktestEngine, "close_position", fake_parent_close)

    engine.close_position("BTCUSDT", 101.0, "2026-03-30 00:00:00", "stop_loss_intrabar")

    assert pos_key not in engine.bot_logic._position_first_seen_ts


def test_bot_like_replay_preserves_missing_microstructure_as_unknown():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.replay_microstructure_enabled = True
    engine.replay_microstructure_mode = "historical_proxy"
    engine.replay_microstructure_stats = {}

    timestamps = pd.date_range("2026-03-30 00:00:00", periods=6, freq="15min")
    base_df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100, 101, 102, 103, 104, 105],
            "high": [101, 102, 103, 104, 105, 106],
            "low": [99, 100, 101, 102, 103, 104],
            "close": [100, 101, 102, 103, 104, 105],
            "volume": [10, 10, 10, 10, 10, 10],
            "atr": [1, 1, 1, 1, 1, 1],
            "macd_hist": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
        }
    )
    data = {"15m": base_df.copy(), "1h": base_df.copy(), "4h": base_df.copy()}
    engine._find_tf_index = lambda data_map, timeframe, current_time: 5

    flow_context = engine._build_flow_context("BTCUSDT", data, 5)

    assert flow_context is not None
    assert flow_context["depth_ratio"] is None
    assert flow_context["imbalance"] is None
    assert flow_context["spread_bps"] is None
    assert flow_context["timeframes"]["15m"]["cvd_momentum"] is None
    assert flow_context["replay_microstructure"]["mode"] == "historical_proxy"
    assert flow_context["replay_microstructure"]["spread_gate_mode"] == "skip_if_missing"
    assert flow_context["replay_microstructure"]["missing_fields"] == [
        "depth_ratio",
        "imbalance",
        "spread_bps",
    ]


def test_bot_like_replay_prepares_runtime_config_without_mutating_live_config():
    runtime_config = {
        "fund_flow": {
            "entry_hard_gates_enabled": True,
        }
    }

    prepared = BotLikeReplayEngine._prepare_replay_runtime_config(runtime_config)

    assert runtime_config["fund_flow"].get("entry_hard_gate_skip_spread_if_missing") is None
    assert prepared["fund_flow"]["replay_microstructure"]["enabled"] is True
    assert prepared["fund_flow"]["replay_microstructure"]["mode"] == "historical_proxy"
