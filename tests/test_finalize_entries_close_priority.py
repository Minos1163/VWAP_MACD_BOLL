from types import SimpleNamespace

from src.app.fund_flow_bot import TradingBot
from src.fund_flow.models import FundFlowDecision, Operation as FundFlowOperation


def _make_bot(active_symbols=None):
    bot = TradingBot.__new__(TradingBot)
    active_symbols = active_symbols or {"ETHUSDT": {}, "SOLUSDT": {}}
    bot.config = {}
    bot.position_data = SimpleNamespace(get_all_positions=lambda: dict(active_symbols))
    bot._opened_symbols_this_cycle = set()
    bot._ai_review_mode_supports_flat_candidates = lambda _mode: False
    bot._decision_signal_score = lambda decision: float((decision.metadata or {}).get("test_score", 0.0))
    bot._record_alpha_dilution_stage = lambda *args, **kwargs: None
    bot._executed = []

    def _record_execution(**kwargs):
        bot._executed.append(kwargs)

    bot._execute_and_log_decision = _record_execution
    return bot


def _decision(symbol: str, operation: FundFlowOperation) -> FundFlowDecision:
    return FundFlowDecision(
        operation=operation,
        symbol=symbol,
        target_portion_of_balance=1.0 if operation == FundFlowOperation.CLOSE else 0.2,
        leverage=5,
        reason="test",
        metadata={},
    )


def _decision_with_md(symbol: str, operation: FundFlowOperation, metadata) -> FundFlowDecision:
    return FundFlowDecision(
        operation=operation,
        symbol=symbol,
        target_portion_of_balance=1.0 if operation == FundFlowOperation.CLOSE else 0.2,
        leverage=5,
        reason="test",
        metadata=dict(metadata),
    )


def test_finalize_entries_executes_close_even_when_capacity_is_full():
    bot = _make_bot()
    context = {
        "pending_new_entries": [
            {
                # Simulate legacy close candidate without an explicit symbol field.
                "decision": _decision("BTCUSDT", FundFlowOperation.CLOSE),
                "position": {"side": "LONG", "amount": 1.0},
                "current_price": 100.0,
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            },
            {
                "symbol": "XRPUSDT",
                "score": 0.9,
                "max_active_symbols": 2,
                "decision": _decision("XRPUSDT", FundFlowOperation.BUY),
                "position": None,
                "current_price": 2.0,
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            },
        ],
        "block_new_entries_due_to_protection_gap": False,
        "protection_gap_symbols": [],
        "max_active_symbols": 2,
        "account_summary": {"available_balance": 1000.0},
        "ai_gate_enabled": False,
        "ai_review_cfg": {},
        "ai_review_mode": "disabled",
    }

    bot._finalize_entries(context)

    assert len(bot._executed) == 1
    assert bot._executed[0]["symbol"] == "BTCUSDT"
    assert bot._executed[0]["decision"].operation == FundFlowOperation.CLOSE


def test_finalize_entries_keeps_close_when_protection_gap_blocks_new_entries():
    bot = _make_bot(active_symbols={"ETHUSDT": {}})
    context = {
        "pending_new_entries": [
            {
                "symbol": "BTCUSDT",
                "score": 1.0,
                "bypass_capacity_guard": True,
                "bypass_ai_final_review": True,
                "decision": _decision("BTCUSDT", FundFlowOperation.CLOSE),
                "position": {"side": "SHORT", "amount": 1.0},
                "current_price": 100.0,
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            },
            {
                "symbol": "XRPUSDT",
                "score": 0.8,
                "max_active_symbols": 3,
                "decision": _decision("XRPUSDT", FundFlowOperation.BUY),
                "position": None,
                "current_price": 2.0,
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            },
        ],
        "block_new_entries_due_to_protection_gap": True,
        "protection_gap_symbols": ["BTCUSDT"],
        "max_active_symbols": 3,
        "account_summary": {"available_balance": 1000.0},
        "ai_gate_enabled": False,
        "ai_review_cfg": {},
        "ai_review_mode": "disabled",
    }

    bot._finalize_entries(context)

    assert len(bot._executed) == 1
    assert bot._executed[0]["symbol"] == "BTCUSDT"
    assert bot._executed[0]["decision"].operation == FundFlowOperation.CLOSE


def test_finalize_entries_ai_review_rejects_weak_trend_candidate_without_structure():
    bot = _make_bot(active_symbols={})
    bot.position_data = SimpleNamespace(get_all_positions=lambda: {})
    bot._ai_review_mode_supports_flat_candidates = lambda _mode: True
    bot.fund_flow_decision_engine = SimpleNamespace(
        decide=lambda **kwargs: _decision_with_md(
            "XLMUSDT",
            FundFlowOperation.BUY,
            {
                "engine": "TREND",
                "ds_source": "ai_weight_router",
                "ds_confidence": 0.82,
                "test_score": 0.068,
                "final": {"need_confirm": True},
            },
        )
    )
    context = {
        "pending_new_entries": [
            {
                "symbol": "XLMUSDT",
                "score": 0.068,
                "max_active_symbols": 2,
                "decision": _decision_with_md(
                    "XLMUSDT",
                    FundFlowOperation.BUY,
                    {"engine": "TREND", "test_score": 0.068, "final": {"need_confirm": True}},
                ),
                "position": None,
                "current_price": 0.165,
                "flow_context": {
                    "regime": "TREND",
                    "flow_confirm": False,
                    "trap_score": 0.52,
                    "trap_confirmed": False,
                    "capture_confirm_3m_side": "NONE",
                    "ma10_bias_1h": "FLAT",
                    "macd_cross_5m": "NONE",
                    "macd_zone_5m": "BELOW_ZERO",
                },
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            }
        ],
        "block_new_entries_due_to_protection_gap": False,
        "protection_gap_symbols": [],
        "max_active_symbols": 2,
        "account_summary": {"available_balance": 1000.0},
        "ai_gate_enabled": True,
        "ai_review_cfg": {"enabled": True},
        "ai_review_mode": "flat_candidates",
    }

    bot._finalize_entries(context)

    assert bot._executed == []


def test_finalize_entries_ai_review_log_only_keeps_candidate():
    bot = _make_bot(active_symbols={})
    bot.position_data = SimpleNamespace(get_all_positions=lambda: {})
    bot._ai_review_mode_supports_flat_candidates = lambda _mode: True
    bot.fund_flow_decision_engine = SimpleNamespace(
        decide=lambda **kwargs: _decision_with_md(
            "XLMUSDT",
            FundFlowOperation.BUY,
            {
                "engine": "TREND",
                "ds_source": "ai_weight_router",
                "ds_confidence": 0.82,
                "test_score": 0.068,
                "final": {"need_confirm": True},
            },
        )
    )
    context = {
        "pending_new_entries": [
            {
                "symbol": "XLMUSDT",
                "score": 0.068,
                "max_active_symbols": 2,
                "ai_shortlist_rank": 1,
                "decision": _decision_with_md(
                    "XLMUSDT",
                    FundFlowOperation.BUY,
                    {"engine": "TREND", "test_score": 0.068, "final": {"need_confirm": True}},
                ),
                "position": None,
                "current_price": 0.165,
                "flow_context": {
                    "regime": "TREND",
                    "flow_confirm": False,
                    "trap_score": 0.52,
                    "trap_confirmed": False,
                    "capture_confirm_3m_side": "NONE",
                    "ma10_bias_1h": "FLAT",
                    "macd_cross_5m": "NONE",
                    "macd_zone_5m": "BELOW_ZERO",
                },
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            }
        ],
        "block_new_entries_due_to_protection_gap": False,
        "protection_gap_symbols": [],
        "max_active_symbols": 2,
        "account_summary": {"available_balance": 1000.0},
        "ai_gate_enabled": True,
        "ai_review_cfg": {"enabled": True, "enforced": False, "flat_top_n": 3},
        "ai_review_mode": "flat_candidates",
    }

    bot._finalize_entries(context)

    assert len(bot._executed) == 1
    assert bot._executed[0]["symbol"] == "XLMUSDT"
    review_md = bot._executed[0]["decision"].metadata.get("ai_final_review", {})
    assert review_md.get("mode") == "log_only"
    assert review_md.get("allow_ai_entry") is False


def test_finalize_entries_skips_open_candidate_with_blocked_entry_tier():
    bot = _make_bot(active_symbols={})
    bot.position_data = SimpleNamespace(get_all_positions=lambda: {})
    context = {
        "pending_new_entries": [
            {
                "symbol": "XRPUSDT",
                "score": 0.61,
                "max_active_symbols": 3,
                "decision": _decision_with_md(
                    "XRPUSDT",
                    FundFlowOperation.BUY,
                    {
                        "entry_tier": "blocked",
                        "signal_type_1h": "green_bar_growing",
                        "vwap_state": "long_above_structure_wait_reclaim",
                    },
                ),
                "position": None,
                "current_price": 2.0,
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            }
        ],
        "block_new_entries_due_to_protection_gap": False,
        "protection_gap_symbols": [],
        "max_active_symbols": 3,
        "account_summary": {"available_balance": 1000.0},
        "ai_gate_enabled": False,
        "ai_review_cfg": {},
        "ai_review_mode": "disabled",
    }

    bot._finalize_entries(context)

    assert bot._executed == []


def test_finalize_entries_blocks_new_candidate_when_position_snapshot_already_hits_capacity():
    bot = _make_bot(active_symbols={})
    bot._position_snapshot_by_symbol = lambda *_args, **_kwargs: {}
    context = {
        "pending_new_entries": [
            {
                "symbol": "XRPUSDT",
                "score": 0.9,
                "max_active_symbols": 4,
                "decision": _decision("XRPUSDT", FundFlowOperation.BUY),
                "position": None,
                "current_price": 2.0,
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            },
        ],
        "position_snapshot": {
            "ATOMUSDT": {"side": "LONG"},
            "DOGEUSDT": {"side": "LONG"},
            "ICPUSDT": {"side": "LONG"},
            "ADAUSDT": {"side": "LONG"},
        },
        "block_new_entries_due_to_protection_gap": False,
        "protection_gap_symbols": [],
        "max_active_symbols": 4,
        "account_summary": {"available_balance": 1000.0},
        "ai_gate_enabled": False,
        "ai_review_cfg": {},
        "ai_review_mode": "disabled",
    }

    bot._finalize_entries(context)

    assert bot._executed == []


def test_select_open_candidates_with_replacement_prefers_worst_tier3_position():
    bot = _make_bot(active_symbols={})
    candidate = {
        "symbol": "BTCUSDT",
        "score": 0.96,
        "max_active_symbols": 2,
        "decision": _decision_with_md(
            "BTCUSDT",
            FundFlowOperation.BUY,
            {
                "entry_tier": "tier1",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "test_score": 0.96,
            },
        ),
    }

    selected = bot.select_open_candidates_with_replacement(
        [candidate],
        active_positions={
            "ETHUSDT": {"entry_tier": "tier2", "signal_score": 0.40, "unrealized_pnl_ratio": -0.10},
            "DOGEUSDT": {"entry_tier": "tier3", "signal_score": 0.55, "unrealized_pnl_ratio": -0.08},
            "XRPUSDT": {"entry_tier": "tier3", "signal_score": 0.60, "unrealized_pnl_ratio": -0.21},
        },
        active_count=2,
        ai_review_cfg={},
    )

    assert len(selected) == 1
    assert selected[0]["capacity_replacement_candidate"] is True
    assert selected[0]["capacity_replacement_target"]["symbol"] == "XRPUSDT"


def test_finalize_entries_executes_capacity_replacement_for_tier1_candidate():
    bot = _make_bot(active_symbols={})
    bot._position_snapshot_by_symbol = lambda *_args, **_kwargs: {
        "DOGEUSDT": {"entry_tier": "tier3", "signal_score": 0.55, "unrealized_pnl_ratio": -0.18},
        "ETHUSDT": {"entry_tier": "tier2", "signal_score": 0.72, "unrealized_pnl_ratio": 0.03},
    }
    context = {
        "pending_new_entries": [
            {
                "symbol": "BTCUSDT",
                "score": 0.96,
                "max_active_symbols": 2,
                "decision": _decision_with_md(
                    "BTCUSDT",
                    FundFlowOperation.BUY,
                    {
                        "entry_tier": "tier1",
                        "signal_type_1h": "red_bar_growing",
                        "vwap_state": "long_reclaim_confirmed",
                        "test_score": 0.96,
                    },
                ),
                "position": None,
                "current_price": 100.0,
                "trigger_context": {},
                "portfolio": {},
                "flow_context": {},
                "account_summary": {"available_balance": 1000.0},
            },
        ],
        "position_snapshot": {
            "DOGEUSDT": {"side": "LONG"},
            "ETHUSDT": {"side": "LONG"},
        },
        "block_new_entries_due_to_protection_gap": False,
        "protection_gap_symbols": [],
        "max_active_symbols": 2,
        "account_summary": {"available_balance": 1000.0},
        "ai_gate_enabled": False,
        "ai_review_cfg": {},
        "ai_review_mode": "disabled",
    }

    bot._finalize_entries(context)

    assert len(bot._executed) == 2
    assert bot._executed[0]["symbol"] == "DOGEUSDT"
    assert bot._executed[0]["decision"].operation == FundFlowOperation.CLOSE
    assert bot._executed[1]["symbol"] == "BTCUSDT"
    assert bot._executed[1]["decision"].operation == FundFlowOperation.BUY
    assert bot._executed[1]["decision"].metadata["capacity_replacement_candidate"] is True
    assert bot._executed[1]["decision"].metadata["capacity_replacement_target"]["symbol"] == "DOGEUSDT"


def test_finalize_entries_priority_bonus_can_push_short_retest_reject_ahead_in_sort():
    bot = _make_bot(active_symbols={})
    bot.position_data = SimpleNamespace(get_all_positions=lambda: {})
    bot._ai_review_mode_supports_flat_candidates = lambda _mode: False
    context = {
        "pending_new_entries": [
            {
                "symbol": "AAAUSDT",
                "score": 0.90,
                "max_active_symbols": 5,
                "decision": _decision_with_md(
                    "AAAUSDT",
                    FundFlowOperation.BUY,
                    {
                        "test_score": 0.90,
                        "signal_type_1h": "flip_bullish",
                        "vwap_state": "long_reclaim_confirmed",
                    },
                ),
                "position": None,
                "current_price": 1.0,
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            },
            {
                "symbol": "BBBUSDT",
                "score": 0.86,
                "max_active_symbols": 5,
                "decision": _decision_with_md(
                    "BBBUSDT",
                    FundFlowOperation.SELL,
                    {
                        "test_score": 0.86,
                        "signal_type_1h": "green_bar_growing",
                        "vwap_state": "short_retest_reject",
                    },
                ),
                "position": None,
                "current_price": 1.0,
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            },
        ],
        "block_new_entries_due_to_protection_gap": False,
        "protection_gap_symbols": [],
        "max_active_symbols": 5,
        "account_summary": {"available_balance": 1000.0},
        "ai_gate_enabled": False,
        "ai_review_cfg": {
            "shortlist_priority_overrides": {
                "green_bar_growing|short_retest_reject": 0.10
            }
        },
        "ai_review_mode": "disabled",
    }

    bot._finalize_entries(context)

    assert len(bot._executed) == 2
    assert bot._executed[0]["symbol"] == "BBBUSDT"


def test_finalize_entries_symbol_priority_bonus_can_break_tie_in_capacity_sort():
    bot = _make_bot(active_symbols={})
    bot.position_data = SimpleNamespace(get_all_positions=lambda: {})
    bot._ai_review_mode_supports_flat_candidates = lambda _mode: False
    context = {
        "pending_new_entries": [
            {
                "symbol": "AAAUSDT",
                "score": 0.90,
                "max_active_symbols": 5,
                "decision": _decision_with_md(
                    "AAAUSDT",
                    FundFlowOperation.SELL,
                    {
                        "test_score": 0.90,
                        "signal_type_1h": "green_bar_growing",
                        "vwap_state": "short_retest_reject",
                    },
                ),
                "position": None,
                "current_price": 1.0,
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            },
            {
                "symbol": "BBBUSDT",
                "score": 0.90,
                "max_active_symbols": 5,
                "decision": _decision_with_md(
                    "BBBUSDT",
                    FundFlowOperation.SELL,
                    {
                        "test_score": 0.90,
                        "signal_type_1h": "green_bar_growing",
                        "vwap_state": "short_retest_reject",
                    },
                ),
                "position": None,
                "current_price": 1.0,
                "trigger_context": {},
                "portfolio": {},
                "account_summary": {"available_balance": 1000.0},
            },
        ],
        "block_new_entries_due_to_protection_gap": False,
        "protection_gap_symbols": [],
        "max_active_symbols": 5,
        "account_summary": {"available_balance": 1000.0},
        "ai_gate_enabled": False,
        "ai_review_cfg": {
            "shortlist_symbol_priority_overrides": {
                "BBBUSDT": 0.05
            }
        },
        "ai_review_mode": "disabled",
    }

    bot._finalize_entries(context)

    assert len(bot._executed) == 2
    assert bot._executed[0]["symbol"] == "BBBUSDT"


def test_finalize_entries_respects_capacity_group_cap_for_same_pocket():
    bot = _make_bot(active_symbols={})
    bot.position_data = SimpleNamespace(get_all_positions=lambda: {})
    bot._ai_review_mode_supports_flat_candidates = lambda _mode: False
    base_item = {
        "max_active_symbols": 5,
        "position": None,
        "current_price": 1.0,
        "trigger_context": {},
        "portfolio": {},
        "account_summary": {"available_balance": 1000.0},
    }
    context = {
        "pending_new_entries": [
            {
                **base_item,
                "symbol": "AAAUSDT",
                "score": 0.93,
                "decision": _decision_with_md(
                    "AAAUSDT",
                    FundFlowOperation.SELL,
                    {"test_score": 0.93, "signal_type_1h": "green_bar_growing", "vwap_state": "short_retest_reject"},
                ),
            },
            {
                **base_item,
                "symbol": "BBBUSDT",
                "score": 0.92,
                "decision": _decision_with_md(
                    "BBBUSDT",
                    FundFlowOperation.SELL,
                    {"test_score": 0.92, "signal_type_1h": "green_bar_growing", "vwap_state": "short_retest_reject"},
                ),
            },
            {
                **base_item,
                "symbol": "CCCUSDT",
                "score": 0.91,
                "decision": _decision_with_md(
                    "CCCUSDT",
                    FundFlowOperation.SELL,
                    {"test_score": 0.91, "signal_type_1h": "green_bar_growing", "vwap_state": "short_retest_reject"},
                ),
            },
        ],
        "block_new_entries_due_to_protection_gap": False,
        "protection_gap_symbols": [],
        "max_active_symbols": 5,
        "account_summary": {"available_balance": 1000.0},
        "ai_gate_enabled": False,
        "ai_review_cfg": {
            "capacity_group_caps": {
                "green_bar_growing|short_retest_reject": 2
            }
        },
        "ai_review_mode": "disabled",
    }

    bot._finalize_entries(context)

    assert [item["symbol"] for item in bot._executed] == ["AAAUSDT", "BBBUSDT"]


def test_ai_entry_guard_blocks_weak_same_side_add():
    bot = TradingBot.__new__(TradingBot)
    allowed, reason = bot._ai_entry_guard(
        decision=_decision_with_md(
            "TRXUSDT",
            FundFlowOperation.BUY,
            {"engine": "TREND", "final": {"need_confirm": False}},
        ),
        local_score=0.076,
        flow_context={
            "regime": "TREND",
            "flow_confirm": True,
            "trap_score": 0.12,
            "trap_confirmed": False,
            "capture_confirm_3m_side": "LONG",
            "ma10_bias_1h": "UP",
            "macd_cross_5m": "GOLDEN",
            "macd_zone_5m": "ABOVE_ZERO",
        },
        ai_review_cfg={"final_same_side_add_min_score": 0.11},
        position={"side": "LONG"},
    )

    assert allowed is False
    assert "same_side_add_score" in reason
