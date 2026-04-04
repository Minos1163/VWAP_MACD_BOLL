import pandas as pd

from scripts.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
from scripts.analyze_rank1_reclaim_quality import _aggregate_rank1_entries, build_report


def test_rank1_reclaim_quality_classifies_time_exit_and_stop_paths():
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
                    "cluster_rank": 1,
                    "final_opened": True,
                },
                {
                    "timestamp": "2026-03-02 02:00:00+00:00",
                    "symbol": "ETHUSDT",
                    "local_operation": "Operation.BUY",
                    "generated_open_candidate": True,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "cluster_rank": 1,
                    "final_opened": True,
                },
            ]
        )
    )
    trades = pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "entry_time": "2026-03-02 01:00:00+00:00",
                "exit_time": "2026-03-02 01:30:00+00:00",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.9,
                "vwap_score": 0.17,
                "adx_1h": 35.0,
                "entry_scale": 1.0,
                "pnl": -10.0,
                "reason": "decision_close:time_exit hold=30m pnl=-0.0100",
            },
            {
                "symbol": "ETHUSDT",
                "side": "long",
                "entry_time": "2026-03-02 02:00:00+00:00",
                "exit_time": "2026-03-02 02:15:00+00:00",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.92,
                "vwap_score": 0.18,
                "adx_1h": 40.0,
                "entry_scale": 1.0,
                "pnl": -5.0,
                "reason": "stop_loss_intrabar",
            },
        ]
    )

    entries = _aggregate_rank1_entries(ledger, trades, "red_bar_growing", "long_reclaim_confirmed")
    report = build_report(entries, "red_bar_growing", "long_reclaim_confirmed")

    families = {row["exit_family"]: row for row in report["by_exit_family"]}
    assert families["time_exit_only"]["count"] == 1
    assert families["stop_only"]["count"] == 1
