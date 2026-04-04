import pandas as pd

from scripts.analyze_ai_shortlist_effectiveness import _aggregate_pure_entries, _prepare_candidate_ledger
from scripts.analyze_reclaim_candidate_clusters import build_report


def test_reclaim_cluster_audit_separates_first_and_repeat_opens():
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
                    "signal_score": 0.95,
                    "vwap_score": 0.18,
                    "pre_filter_passed": True,
                },
                {
                    "timestamp": "2026-03-02 01:15:00+00:00",
                    "symbol": "BTCUSDT",
                    "local_operation": "Operation.BUY",
                    "generated_open_candidate": True,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "signal_score": 0.97,
                    "vwap_score": 0.19,
                    "pre_filter_passed": True,
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
                    "pnl": 20.0,
                }
            ]
        )
    )
    bot = pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "entry_time": "2026-03-02 01:00:00+00:00",
                "exit_time": "2026-03-02 01:30:00+00:00",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "pnl": 10.0,
                "reason": "take_profit",
            },
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "entry_time": "2026-03-02 01:15:00+00:00",
                "exit_time": "2026-03-02 01:45:00+00:00",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "pnl": -5.0,
                "reason": "time_exit",
            },
        ]
    )

    report = build_report(
        ledger_df=ledger,
        pure_df=pure,
        bot_df=bot,
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
        tolerance_minutes=30,
        label="test",
    )

    assert report["cluster_count"] == 1
    assert report["opened_count"] == 2
    assert report["first_vs_repeat_opened"]["first"]["count"] == 1
    assert report["first_vs_repeat_opened"]["first"]["total_pnl"] == 10.0
    assert report["first_vs_repeat_opened"]["repeat"]["count"] == 1
    assert report["first_vs_repeat_opened"]["repeat"]["total_pnl"] == -5.0
