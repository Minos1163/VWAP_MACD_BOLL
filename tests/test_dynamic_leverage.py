from types import SimpleNamespace

from scripts.backtest_fund_flow_bot_like import BotLikeReplayEngine
from src.fund_flow.models import FundFlowDecision, Operation
from src.fund_flow.dynamic_leverage import get_max_leverage_by_recent_performance


def test_get_max_leverage_by_recent_performance_uses_recent_win_rate_bands():
    recent_trades = [{"pnl": 1.0}] * 16 + [{"pnl": -1.0}] * 4
    cfg = {
        "dynamic_leverage_window": 20,
        "dynamic_leverage_map": [
            {"min_win_rate": 0.75, "max_leverage": 5},
            {"min_win_rate": 0.65, "max_leverage": 3},
            {"min_win_rate": 0.55, "max_leverage": 2},
            {"min_win_rate": 0.0, "max_leverage": 0},
        ],
    }

    result = get_max_leverage_by_recent_performance(recent_trades, cfg=cfg)

    assert result == 5


def test_get_max_leverage_by_recent_performance_defaults_conservative_when_sample_small():
    recent_trades = [{"pnl": 1.0}] * 5
    cfg = {
        "dynamic_leverage_window": 20,
        "dynamic_leverage_min_samples": 10,
        "dynamic_leverage_default_max_leverage": 2,
        "dynamic_leverage_map": [
            {"min_win_rate": 0.75, "max_leverage": 5},
            {"min_win_rate": 0.65, "max_leverage": 3},
            {"min_win_rate": 0.55, "max_leverage": 2},
            {"min_win_rate": 0.0, "max_leverage": 0},
        ],
    }

    result = get_max_leverage_by_recent_performance(recent_trades, cfg=cfg)

    assert result == 2


def test_open_position_from_decision_caps_new_entry_leverage_with_dynamic_limit():
    engine = BotLikeReplayEngine.__new__(BotLikeReplayEngine)
    engine.positions = {}
    engine.pending_orders = {}
    engine.trades = [{"pnl": 1.0}] * 11 + [{"pnl": -1.0}] * 9
    engine.capital = 10000.0
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
    engine.dynamic_leverage_enabled = True
    engine.dynamic_leverage_cfg = {
        "dynamic_leverage_window": 20,
        "dynamic_leverage_map": [
            {"min_win_rate": 0.75, "max_leverage": 5},
            {"min_win_rate": 0.65, "max_leverage": 3},
            {"min_win_rate": 0.55, "max_leverage": 2},
            {"min_win_rate": 0.0, "max_leverage": 0},
        ],
    }
    engine._sync_position_tracking = lambda *_args, **_kwargs: None

    def fake_fill(symbol, order, fill_price, fill_time):
        engine.positions[symbol] = dict(order)
        return True

    engine._fill_pending_order = fake_fill

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

    assert opened is True
    assert engine.positions["BTCUSDT"]["leverage"] == 2
