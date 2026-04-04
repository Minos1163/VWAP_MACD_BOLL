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
    assert ff["pretrade_risk_gate"]["enabled"] is True
    assert ff["pretrade_risk_gate"]["use_hard_rules_only"] is True
    assert "ai_review" not in ff or ff["ai_review"]["enabled"] is False
    assert ff["pure_strategy_runtime"]["enabled"] is True


def test_fund_flow_main_config_applies_optimization_guardrails():
    cfg = json.loads(Path("config/trading_config_fund_flow.json").read_text(encoding="utf-8"))

    assert cfg["iflow"]["cwd"] == "d:\\AIDCA\\AI8"
    assert cfg["iflow"]["file_allowed_dirs"] == [
        "d:\\AIDCA\\AI8",
        "d:\\AIDCA\\AI8\\config",
        "d:\\AIDCA\\AI8\\src",
    ]

    ff = cfg["fund_flow"]
    entry_filters = ff["macd_mtf_strategy_v2"]["entry_filters"]
    pure_runtime = ff["pure_strategy_runtime"]

    assert ff["default_target_portion"] == 0.3
    assert ff["max_symbol_position_portion"] == 0.3
    assert ff["max_active_symbols"] == 4
    assert ff["max_leverage"] == 5
    assert pure_runtime["bypass_signal_pool"] is True
    assert pure_runtime["bypass_pretrade_risk_gate"] is True
    assert pure_runtime["bypass_entry_hard_gate"] is True
    assert ff["pretrade_risk_gate"]["equity_usage_block"] == 0.85
    pocket_overrides = entry_filters["pocket_entry_overrides"]
    assert "red_bar_growing|long_dual_support" in pocket_overrides
    pocket_cfg = pocket_overrides["red_bar_growing|long_dual_support"]
    assert pocket_cfg["min_signal_score"] == 0.88
    assert pocket_cfg["min_vwap_score"] == 0.16
    assert pocket_cfg["require_cvd_ok"] is True
    assert pocket_cfg["require_cvd_momentum_ok"] is True
    assert ff["vwap_structure_overrides"]["long_dual_support"]["position_scale_override"] == 0.80


def test_fund_flow_main_config_applies_iteration2_quality_recovery_settings():
    cfg = json.loads(Path("config/trading_config_fund_flow.json").read_text(encoding="utf-8"))

    assert cfg["risk"]["take_profit_default_percent"] == 0.04

    ff = cfg["fund_flow"]
    entry_filters = ff["macd_mtf_strategy_v2"]["entry_filters"]
    stop_cfg = ff["macd_mtf_strategy_v2"]["stop_loss_config"]

    assert ff["take_profit_pct"] == 0.04
    assert entry_filters["enable_flip_bullish_cvd_context_filter"] is False
    assert entry_filters["stable_bear_continuation_min_adx_1h"] == 30.0
    assert stop_cfg["boll_stop_atr_multiplier"] == 0.5


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


def test_pure_strategy_runtime_disables_ai_gate(monkeypatch):
    bot = TradingBot.__new__(TradingBot)
    bot.config = {
        "fund_flow": {
            "pure_strategy_runtime": {
                "enabled": True,
                "bypass_ai_review": True,
            },
            "deepseek_weight_router": {"enabled": True, "ai_enabled": True},
            "deepseek_ai": {"enabled": True, "api_key": "x"},
        }
    }
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")

    assert bot._is_ai_gate_enabled() is False


def test_pure_strategy_runtime_bypasses_pretrade_risk_gate():
    bot = TradingBot.__new__(TradingBot)
    bot.config = {
        "fund_flow": {
            "pure_strategy_runtime": {
                "enabled": True,
                "bypass_pretrade_risk_gate": True,
            },
            "pretrade_risk_gate": {"enabled": True},
        }
    }
    decision = FundFlowDecision(
        operation=FundFlowOperation.BUY,
        symbol="BTCUSDT",
        target_portion_of_balance=0.1,
        leverage=1.0,
        reason="test",
        metadata={},
    )

    out_decision, gate_meta = bot._apply_pretrade_risk_gate(
        symbol="BTCUSDT",
        decision=decision,
        position=None,
        flow_context={},
        current_price=100.0,
        account_summary={"equity": 1000.0},
    )

    assert out_decision.operation == FundFlowOperation.BUY
    assert gate_meta["action"] == "BYPASS_PURE_RUNTIME"
    assert out_decision.metadata["pretrade_risk_gate"]["action"] == "BYPASS_PURE_RUNTIME"


def test_pure_strategy_runtime_bypasses_dynamic_max_active_symbols():
    bot = TradingBot.__new__(TradingBot)
    bot.config = {
        "fund_flow": {
            "pure_strategy_runtime": {
                "enabled": True,
                "bypass_dynamic_max_active_symbols": True,
            }
        }
    }
    decision = FundFlowDecision(
        operation=FundFlowOperation.BUY,
        symbol="BTCUSDT",
        target_portion_of_balance=0.1,
        leverage=1.0,
        reason="test",
        metadata={"signal_score": 0.99, "vwap_score": 0.5, "engine": "TREND"},
    )

    cap, metadata = bot._resolve_dynamic_max_active_symbols(
        decision=decision,
        engine_override={"max_active_symbols": 4},
        base_max_active_symbols=4,
    )

    assert cap == 4
    assert metadata["reason"] == "pure_strategy_runtime_bypass"


def test_pure_strategy_runtime_bypasses_ma10_macd_filter():
    bot = TradingBot.__new__(TradingBot)
    bot.config = {
        "fund_flow": {
            "pure_strategy_runtime": {
                "enabled": True,
                "bypass_ma10_macd_filter": True,
            },
            "ma10_macd_confluence": {"enabled": True, "entry_hard_filter": True},
        }
    }
    decision = FundFlowDecision(
        operation=FundFlowOperation.BUY,
        symbol="BTCUSDT",
        target_portion_of_balance=0.1,
        leverage=1.0,
        reason="test",
        metadata={},
    )

    out = bot._apply_ma10_macd_entry_filter("BTCUSDT", decision)

    assert out.operation == FundFlowOperation.BUY
    assert out.metadata["ma10_macd_pure_strategy_runtime_bypassed"] is True
