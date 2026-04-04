from pathlib import Path

import pandas as pd

from scripts.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
from scripts.analyze_rank1_reclaim_samebar_conflicts import build_samebar_report


def test_samebar_audit_splits_tp1_hit_before_exit_bar_vs_exit_bar_only(tmp_path: Path):
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
                    "tp1_price": 100.8,
                    "tp1_pct": 0.008,
                    "tp1_reduce_pct": 0.25,
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
                    "tp1_price": 100.8,
                    "tp1_pct": 0.008,
                    "tp1_reduce_pct": 0.25,
                },
            ]
        )
    )
    trades = pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "entry_price": 100.0,
                "entry_time": "2026-03-02 01:00:00+00:00",
                "exit_time": "2026-03-02 01:30:00+00:00",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.90,
                "vwap_score": 0.17,
                "adx_1h": 35.0,
                "entry_scale": 1.0,
                "pnl": -10.0,
                "reason": "decision_close:time_exit hold=30m pnl=-0.0100",
            },
            {
                "symbol": "ETHUSDT",
                "side": "long",
                "entry_price": 100.0,
                "entry_time": "2026-03-02 02:00:00+00:00",
                "exit_time": "2026-03-02 02:30:00+00:00",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.92,
                "vwap_score": 0.18,
                "adx_1h": 40.0,
                "entry_scale": 1.0,
                "pnl": -5.0,
                "reason": "decision_close:time_exit hold=30m pnl=-0.0050",
            },
        ]
    )

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    pd.DataFrame(
        [
            {"timestamp": "2026-03-02 01:00:00+00:00", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.4},
            {"timestamp": "2026-03-02 01:15:00+00:00", "open": 100.4, "high": 100.7, "low": 100.1, "close": 100.6},
            {"timestamp": "2026-03-02 01:30:00+00:00", "open": 100.6, "high": 100.6, "low": 99.8, "close": 100.0},
        ]
    ).to_parquet(cache_dir / "BTCUSDT_15m_60d_20260401.parquet")
    pd.DataFrame(
        [
            {"timestamp": "2026-03-02 02:00:00+00:00", "open": 100.0, "high": 100.4, "low": 99.9, "close": 100.2},
            {"timestamp": "2026-03-02 02:15:00+00:00", "open": 100.2, "high": 100.5, "low": 100.0, "close": 100.3},
            {"timestamp": "2026-03-02 02:30:00+00:00", "open": 100.3, "high": 100.9, "low": 99.7, "close": 100.0},
        ]
    ).to_parquet(cache_dir / "ETHUSDT_15m_60d_20260401.parquet")

    report = build_samebar_report(
        ledger_df=ledger,
        trades_df=trades,
        cache_dir=cache_dir,
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
    )

    by_timing = {row["tp1_hit_timing"]: row for row in report["by_tp1_hit_timing"]}
    assert by_timing["tp1_hit_before_exit_bar"]["count"] == 1
    assert by_timing["tp1_hit_on_exit_bar_only"]["count"] == 1


def test_samebar_audit_uses_final_exit_time_not_first_partial_exit(tmp_path: Path):
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
                    "tp1_price": 100.8,
                    "tp1_pct": 0.008,
                    "tp1_reduce_pct": 0.25,
                }
            ]
        )
    )
    trades = pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "entry_price": 100.0,
                "entry_time": "2026-03-02 01:00:00+00:00",
                "exit_time": "2026-03-02 01:15:00+00:00",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.90,
                "vwap_score": 0.17,
                "adx_1h": 35.0,
                "entry_scale": 1.0,
                "pnl": 6.0,
                "reason": "take_profit_level_intrabar",
            },
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "entry_price": 100.0,
                "entry_time": "2026-03-02 01:00:00+00:00",
                "exit_time": "2026-03-02 01:45:00+00:00",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.90,
                "vwap_score": 0.17,
                "adx_1h": 35.0,
                "entry_scale": 1.0,
                "pnl": -2.0,
                "reason": "decision_close:time_exit hold=45m pnl=-0.0005",
            },
        ]
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    pd.DataFrame(
        [
            {"timestamp": "2026-03-02 01:00:00+00:00", "open": 100.0, "high": 100.4, "low": 99.8, "close": 100.1},
            {"timestamp": "2026-03-02 01:15:00+00:00", "open": 100.1, "high": 100.85, "low": 100.0, "close": 100.7},
            {"timestamp": "2026-03-02 01:30:00+00:00", "open": 100.7, "high": 100.75, "low": 100.1, "close": 100.3},
            {"timestamp": "2026-03-02 01:45:00+00:00", "open": 100.3, "high": 100.4, "low": 99.9, "close": 100.1},
        ]
    ).to_parquet(cache_dir / "BTCUSDT_15m_60d_20260401.parquet")

    report = build_samebar_report(
        ledger_df=ledger,
        trades_df=trades,
        cache_dir=cache_dir,
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
    )

    by_timing = {row["tp1_hit_timing"]: row for row in report["by_tp1_hit_timing"]}
    assert by_timing["tp1_hit_before_exit_bar"]["count"] == 1
