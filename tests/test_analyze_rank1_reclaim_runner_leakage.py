from pathlib import Path

import pandas as pd

from scripts.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
from scripts.analyze_rank1_reclaim_runner_leakage import build_runner_leakage_report


def test_runner_leakage_report_quantifies_last_tp_and_peak_gap(tmp_path: Path):
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
                }
            ]
        )
    )
    trades = pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "entry_time": "2026-03-02 01:00:00+00:00",
                "exit_time": "2026-03-02 01:15:00+00:00",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.89,
                "vwap_score": 0.17,
                "adx_1h": 35.0,
                "entry_scale": 1.0,
                "margin": 250.0,
                "entry_notional": 1000.0,
                "leverage": 4,
                "pnl": 9.0,
                "reason": "take_profit_level_intrabar",
            },
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "entry_time": "2026-03-02 01:00:00+00:00",
                "exit_time": "2026-03-02 01:45:00+00:00",
                "entry_price": 100.0,
                "exit_price": 100.2,
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.89,
                "vwap_score": 0.17,
                "adx_1h": 35.0,
                "entry_scale": 1.0,
                "margin": 750.0,
                "entry_notional": 3000.0,
                "leverage": 4,
                "pnl": 4.2,
                "reason": "decision_close:time_exit hold=45m pnl=0.0007",
            },
        ]
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    pd.DataFrame(
        [
            {"timestamp": "2026-03-02 01:00:00+00:00", "open": 100.0, "high": 100.4, "low": 99.8, "close": 100.2},
            {"timestamp": "2026-03-02 01:15:00+00:00", "open": 100.2, "high": 101.1, "low": 100.1, "close": 100.8},
            {"timestamp": "2026-03-02 01:30:00+00:00", "open": 100.8, "high": 101.6, "low": 100.7, "close": 101.2},
            {"timestamp": "2026-03-02 01:45:00+00:00", "open": 101.2, "high": 101.3, "low": 100.1, "close": 100.2},
        ]
    ).to_parquet(cache_dir / "BTCUSDT_15m_60d_20260401.parquet")

    report = build_runner_leakage_report(
        ledger_df=ledger,
        trades_df=trades,
        cache_dir=cache_dir,
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
    )

    assert report["count"] == 1
    case = report["cases"][0]
    assert round(case["runner_actual_pnl"], 4) == 4.2
    assert round(case["runner_hypothetical_pnl_flatten_last_tp"], 4) == 24.0
    assert round(case["runner_hypothetical_pnl_peak_after_last_tp"], 4) == 36.0
    assert round(case["runner_leakage_vs_last_tp"], 4) == 19.8
    assert round(case["runner_leakage_vs_peak"], 4) == 31.8


def test_runner_leakage_report_handles_no_tp_then_time_exit_cases(tmp_path: Path):
    ledger = _prepare_candidate_ledger(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-03-02 01:00:00+00:00",
                    "symbol": "ETHUSDT",
                    "local_operation": "Operation.BUY",
                    "generated_open_candidate": True,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "cluster_rank": 1,
                    "final_opened": True,
                }
            ]
        )
    )
    trades = pd.DataFrame(
        [
            {
                "symbol": "ETHUSDT",
                "side": "long",
                "entry_time": "2026-03-02 01:00:00+00:00",
                "exit_time": "2026-03-02 01:30:00+00:00",
                "entry_price": 100.0,
                "exit_price": 99.5,
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.85,
                "vwap_score": 0.15,
                "adx_1h": 25.0,
                "entry_scale": 1.0,
                "margin": 1000.0,
                "entry_notional": 5000.0,
                "leverage": 5,
                "pnl": -30.0,
                "reason": "decision_close:time_exit hold=30m pnl=-0.0060",
            }
        ]
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    pd.DataFrame(
        [
            {"timestamp": "2026-03-02 01:00:00+00:00", "open": 100.0, "high": 100.2, "low": 99.3, "close": 99.5},
            {"timestamp": "2026-03-02 01:15:00+00:00", "open": 99.5, "high": 99.7, "low": 99.2, "close": 99.4},
            {"timestamp": "2026-03-02 01:30:00+00:00", "open": 99.4, "high": 99.6, "low": 99.1, "close": 99.5},
        ]
    ).to_parquet(cache_dir / "ETHUSDT_15m_60d_20260401.parquet")

    report = build_runner_leakage_report(
        ledger_df=ledger,
        trades_df=trades,
        cache_dir=cache_dir,
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
    )

    assert report["count"] == 0
    assert "no tp_then_time_exit" in report["note"]
