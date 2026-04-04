import pandas as pd

from scripts.analyze_ai_shortlist_effectiveness import (
    _aggregate_pure_entries,
    _match_to_pure_entries,
    _prepare_candidate_ledger,
    build_report,
)


def test_match_to_pure_entries_uses_fuzzy_time_tolerance():
    ledger = _prepare_candidate_ledger(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-03-02 01:15:00+00:00",
                    "symbol": "BTCUSDT",
                    "local_operation": "Operation.BUY",
                    "generated_open_candidate": True,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "signal_score": 0.9,
                    "vwap_score": 0.16,
                    "pre_filter_passed": True,
                    "ai_shortlisted": True,
                    "ai_reviewed": True,
                    "ai_allowed": True,
                    "capacity_selected": True,
                    "final_opened": True,
                }
            ]
        )
    )
    pure = _aggregate_pure_entries(
        pd.DataFrame(
            [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "entry_time": "2026-03-02 01:00:00+00:00",
                    "exit_time": "2026-03-02 02:00:00+00:00",
                    "entry_price": 100.0,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "pnl": 12.5,
                }
            ]
        )
    )

    matched = _match_to_pure_entries(ledger, pure, tolerance_minutes=30)

    assert bool(matched.iloc[0]["matched"]) is True
    assert matched.iloc[0]["pure_pnl"] == 12.5
    assert matched.iloc[0]["match_delta_minutes"] == 15.0


def test_build_report_summarizes_shortlist_and_skipped_groups():
    ledger = _prepare_candidate_ledger(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-03-02 01:00:00+00:00",
                    "symbol": "AAAUSDT",
                    "local_operation": "Operation.BUY",
                    "generated_open_candidate": True,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "pre_filter_passed": True,
                    "ai_shortlisted": True,
                    "ai_reviewed": True,
                    "ai_allowed": True,
                    "capacity_selected": True,
                    "final_opened": True,
                },
                {
                    "timestamp": "2026-03-02 02:00:00+00:00",
                    "symbol": "BBBUSDT",
                    "local_operation": "Operation.BUY",
                    "generated_open_candidate": True,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "pre_filter_passed": True,
                    "ai_shortlisted": False,
                    "ai_reviewed": False,
                    "ai_allowed": False,
                    "ai_block_reason": "ai_shortlist_topn",
                    "capacity_selected": False,
                    "final_opened": False,
                },
            ]
        )
    )
    pure = _aggregate_pure_entries(
        pd.DataFrame(
            [
                {
                    "symbol": "AAAUSDT",
                    "side": "long",
                    "entry_time": "2026-03-02 01:00:00+00:00",
                    "exit_time": "2026-03-02 01:30:00+00:00",
                    "entry_price": 100.0,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "pnl": 20.0,
                },
                {
                    "symbol": "BBBUSDT",
                    "side": "long",
                    "entry_time": "2026-03-02 02:00:00+00:00",
                    "exit_time": "2026-03-02 02:30:00+00:00",
                    "entry_price": 100.0,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "pnl": -10.0,
                },
            ]
        )
    )

    report = build_report(ledger, pure, tolerance_minutes=0)

    assert report["groups"]["shortlisted"]["candidate_count"] == 1
    assert report["groups"]["shortlisted"]["pure_total_pnl"] == 20.0
    assert report["groups"]["skipped_topn"]["candidate_count"] == 1
    assert report["groups"]["skipped_topn"]["pure_total_pnl"] == -10.0
