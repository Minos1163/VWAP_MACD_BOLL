import pandas as pd

from scripts.analyze_ai_shortlist_effectiveness import _aggregate_pure_entries, _prepare_candidate_ledger
from scripts.analyze_candidate_prefilter import build_report


def test_candidate_prefilter_report_dedupes_unique_entries():
    ledger = _prepare_candidate_ledger(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-03-02 01:00:00+00:00",
                    "symbol": "BTCUSDT",
                    "local_operation": "Operation.BUY",
                    "generated_open_candidate": True,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "signal_score": 0.8,
                    "vwap_score": 0.14,
                    "pre_filter_passed": False,
                    "pre_filter_reason": "PRE_AI_SCORE:0.8000<0.8550 [red_bar_growing|long_reclaim_confirmed]",
                },
                {
                    "timestamp": "2026-03-02 01:15:00+00:00",
                    "symbol": "BTCUSDT",
                    "local_operation": "Operation.BUY",
                    "generated_open_candidate": True,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "signal_score": 0.81,
                    "vwap_score": 0.14,
                    "pre_filter_passed": False,
                    "pre_filter_reason": "PRE_AI_SCORE:0.8100<0.8550 [red_bar_growing|long_reclaim_confirmed]",
                },
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
                    "pnl": 15.0,
                }
            ]
        )
    )

    report = build_report(ledger, pure, tolerance_minutes=30)

    assert report["overall"]["blocked"]["matched_candidate_count"] == 2
    assert report["overall"]["blocked"]["matched_unique_entry_count"] == 1
    assert report["overall"]["blocked"]["unique_pure_total_pnl"] == 15.0
