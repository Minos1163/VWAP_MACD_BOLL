from types import SimpleNamespace

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
