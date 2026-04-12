from scripts.diagnostics.audit_live_open_blockers import (
    classify_symbol_override_policy,
    diagnose_replay_data_gap,
)


def test_classify_symbol_override_policy_downgrades_blanket_hard_disable_without_evidence() -> None:
    result = classify_symbol_override_policy(
        symbol="RENDERUSDT",
        override_payload={"disable_flip_bullish": True},
        evidence={},
    )

    assert result["action"] == "DOWNGRADE"
    assert result["flip_bullish_mode"] == "trial_only"


def test_diagnose_replay_data_gap_detects_missing_symbol_files() -> None:
    result = diagnose_replay_data_gap(
        {
            "available_symbols": [],
            "missing_symbols": ["BTCUSDT", "ETHUSDT"],
            "open_candidates_seen": 0,
            "total_trades": 0,
        }
    )

    assert result["issue"] == "data_gap_missing_symbol_files"
    assert result["is_data_gap"] is True
