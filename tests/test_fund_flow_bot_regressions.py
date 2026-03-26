import json
from pathlib import Path
import re
from types import SimpleNamespace

from src.app.fund_flow_bot import FundFlowDecision, FundFlowOperation, TradingBot


def test_trading_bot_does_not_use_missing_private_config_attr():
    source = Path("src/app/fund_flow_bot.py").read_text(encoding="utf-8")
    assert re.search(r"self\._config(?![A-Za-z0-9_])", source) is None


def test_live_config_stage2_ablation_disables_outer_entry_filters_and_ai_review():
    cfg = json.loads(Path("config/trading_config_fund_flow.json").read_text(encoding="utf-8"))
    ff = cfg["fund_flow"]

    assert ff["ma10_macd_confluence"]["enabled"] is False
    assert ff["ma10_macd_confluence"]["entry_hard_filter"] is False
    assert ff["pretrade_risk_gate"]["enabled"] is False
    assert ff["ai_review"]["enabled"] is False


def test_soften_conflict_exit_for_small_mae_downgrades_to_reduce():
    bot = TradingBot.__new__(TradingBot)
    out = bot._soften_conflict_exit_for_small_mae(
        protection={
            "risk_state": "CIRCUIT_EXIT",
            "reason": "熔断 test",
            "state_deep_break": False,
            "reduce_position_pct": 1.0,
            "force_break_even": False,
        },
        drawdown_ratio=0.0013,
        conflict_cfg_hard={"hard_exit_min_mae": 0.002, "state_reduce_pct": 0.35},
    )

    assert out["softened"] is True
    assert out["risk_state"] == "REDUCE"
    assert out["force_break_even"] is True
    assert out["force_reduce_signal"] is True
    assert out["reduce_pct"] == 0.35


def test_soften_conflict_exit_for_small_mae_keeps_circuit_exit_on_deep_break():
    bot = TradingBot.__new__(TradingBot)
    out = bot._soften_conflict_exit_for_small_mae(
        protection={
            "risk_state": "CIRCUIT_EXIT",
            "reason": "熔断 test",
            "state_deep_break": True,
            "reduce_position_pct": 1.0,
            "force_break_even": False,
        },
        drawdown_ratio=0.0013,
        conflict_cfg_hard={"hard_exit_min_mae": 0.002, "state_reduce_pct": 0.35},
    )

    assert out["softened"] is False
    assert out["risk_state"] == "CIRCUIT_EXIT"


def test_symbols_for_current_cycle_prioritizes_positions_without_truncation():
    bot = TradingBot.__new__(TradingBot)
    bot.config = {
        "schedule": {
            "symbols_per_cycle": 7,
            "symbols_per_cycle_prioritize_positions": True,
        }
    }

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
    ordered = bot._symbols_for_current_cycle(symbols, {"SOLUSDT"})

    assert ordered == ["SOLUSDT", "BTCUSDT", "ETHUSDT", "DOGEUSDT"]


def test_diff_counter_dict_only_keeps_positive_deltas():
    delta = TradingBot._diff_counter_dict(
        after={"200": 12, "429": 3, "500": 1},
        before={"200": 10, "429": 3, "418": 2},
    )

    assert delta == {"200": 2, "500": 1}


def test_range_dynamic_signal_pool_inherits_edge_trigger_flag_from_runtime_pool():
    bot = TradingBot.__new__(TradingBot)
    captured = {}

    bot._materialize_flow_snapshot = lambda symbol, market_data: (
        None,
        SimpleNamespace(signal_strength=1.0, timestamp=market_data["timestamp"]),
        {},
    )
    bot._update_extreme_volatility_state = lambda symbol, flow_context: {"blocked": False}
    bot._conflict_symbol_cooldown_state = lambda symbol: {"blocked": False}
    bot._entry_window_state = lambda: {"allowed": True}
    bot._ma10_macd_confluence_config = lambda: {"enabled": False}
    bot._resolve_runtime_signal_pool_config = lambda pool_id: {
        "pool_id": pool_id,
        "id": pool_id,
        "edge_trigger_enabled": False,
        "edge_cooldown_seconds": 600,
    }

    class _TriggerEngine:
        def should_trigger(self, **kwargs):
            return True

        def evaluate_signal_pool(self, **kwargs):
            captured.update(kwargs["signal_pool_config"])
            return {"passed": False, "reason": "test", "edge": {}}

    class _DecisionEngine:
        def decide(self, **kwargs):
            return FundFlowDecision(
                operation=FundFlowOperation.BUY,
                symbol=kwargs["symbol"],
                target_portion_of_balance=0.1,
                leverage=1.0,
                reason="test",
                metadata={"engine": "RANGE", "signal_pool_id": "range_pool"},
            )

    bot.fund_flow_trigger_engine = _TriggerEngine()
    bot.fund_flow_decision_engine = _DecisionEngine()

    bot._execute_symbol_signal_decision(
        symbol="PUMPUSDT",
        market_data={"timestamp": __import__("datetime").datetime(2026, 3, 25)},
        position=None,
        current_price=1.0,
        account_summary={"available_balance": 1000.0, "equity": 1000.0},
        pending_new_entries=[],
        protection_gap_symbols=[],
        block_new_entries_due_to_protection_gap=False,
        allow_new_entries=True,
        ff_cfg={"trigger_dedupe_seconds": 180},
        max_active_symbols=10,
        max_symbol_position_portion=0.1,
        add_position_portion=0.0,
        risk_guard_enabled=False,
        ai_review_mode="disabled",
        ai_review_cfg={"enabled": False},
    )

    assert captured["pool_id"] == "range_pool"
    assert captured["edge_trigger_enabled"] is False
    assert captured["edge_cooldown_seconds"] == 600


def test_global_signal_pool_disable_skips_outer_pool_evaluation():
    bot = TradingBot.__new__(TradingBot)

    class _ReachedNextStage(Exception):
        pass

    bot._materialize_flow_snapshot = lambda symbol, market_data: (
        None,
        SimpleNamespace(signal_strength=1.0, timestamp=market_data["timestamp"]),
        {},
    )
    bot._update_extreme_volatility_state = lambda symbol, flow_context: {"blocked": False}
    bot._conflict_symbol_cooldown_state = lambda symbol: {"blocked": False}
    bot._entry_window_state = lambda: {"allowed": True}
    bot._ma10_macd_confluence_config = lambda: {"enabled": False}
    bot._resolve_runtime_signal_pool_config = lambda pool_id: {
        "pool_id": pool_id,
        "id": pool_id,
        "enabled": True,
    }

    class _TriggerEngine:
        def should_trigger(self, **kwargs):
            return True

        def evaluate_signal_pool(self, **kwargs):
            raise AssertionError("signal_pool should be bypassed when globally disabled")

    class _DecisionEngine:
        def decide(self, **kwargs):
            return FundFlowDecision(
                operation=FundFlowOperation.BUY,
                symbol=kwargs["symbol"],
                target_portion_of_balance=0.1,
                leverage=1.0,
                reason="test",
                metadata={"engine": "TREND", "signal_pool_id": "trend_pool"},
            )

    def _stop_after_pool_bypass(symbol, decision):
        raise _ReachedNextStage()

    bot.fund_flow_trigger_engine = _TriggerEngine()
    bot.fund_flow_decision_engine = _DecisionEngine()
    bot._apply_ma10_macd_entry_filter = _stop_after_pool_bypass

    try:
        bot._execute_symbol_signal_decision(
            symbol="VETUSDT",
            market_data={"timestamp": __import__("datetime").datetime(2026, 3, 25)},
            position=None,
            current_price=1.0,
            account_summary={"available_balance": 1000.0, "equity": 1000.0},
            pending_new_entries=[],
            protection_gap_symbols=[],
            block_new_entries_due_to_protection_gap=False,
            allow_new_entries=True,
            ff_cfg={"trigger_dedupe_seconds": 180, "signal_pool": {"enabled": False}},
            max_active_symbols=10,
            max_symbol_position_portion=0.1,
            add_position_portion=0.0,
            risk_guard_enabled=False,
            ai_review_mode="disabled",
            ai_review_cfg={"enabled": False},
        )
    except _ReachedNextStage:
        pass
    else:
        raise AssertionError("expected to reach the next stage after signal_pool bypass")
