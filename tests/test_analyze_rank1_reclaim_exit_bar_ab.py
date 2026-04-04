from pathlib import Path

import pandas as pd

from scripts.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
from scripts.analyze_rank1_reclaim_exit_bar_ab import build_exit_bar_ab_report


def test_exit_bar_ab_report_returns_zero_when_final_exit_is_after_first_tp1_hit(tmp_path: Path):
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
                "exit_time": "2026-03-02 01:30:00+00:00",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.90,
                "vwap_score": 0.17,
                "adx_1h": 35.0,
                "entry_scale": 1.0,
                "exit_price": 100.8,
                "margin": 1000.0,
                "entry_notional": 5000.0,
                "leverage": 5,
                "pnl": 7.0,
                "reason": "take_profit_level_intrabar",
            },
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "entry_price": 100.0,
                "entry_time": "2026-03-02 01:00:00+00:00",
                "exit_time": "2026-03-02 02:00:00+00:00",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.90,
                "vwap_score": 0.17,
                "adx_1h": 35.0,
                "entry_scale": 1.0,
                "exit_price": 100.1,
                "margin": 750.0,
                "entry_notional": 3750.0,
                "leverage": 5,
                "pnl": 3.0,
                "reason": "decision_close:time_exit hold=60m pnl=0.0010",
            },
        ]
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    pd.DataFrame(
        [
            {"timestamp": "2026-03-02 01:00:00+00:00", "open": 100.0, "high": 100.4, "low": 99.8, "close": 100.1},
            {"timestamp": "2026-03-02 01:15:00+00:00", "open": 100.1, "high": 100.85, "low": 100.0, "close": 100.7},
            {"timestamp": "2026-03-02 01:30:00+00:00", "open": 100.7, "high": 100.7, "low": 100.2, "close": 100.4},
            {"timestamp": "2026-03-02 01:45:00+00:00", "open": 100.4, "high": 100.5, "low": 100.0, "close": 100.2},
            {"timestamp": "2026-03-02 02:00:00+00:00", "open": 100.2, "high": 100.3, "low": 99.9, "close": 100.1},
        ]
    ).to_parquet(cache_dir / "BTCUSDT_15m_60d_20260401.parquet")

    report = build_exit_bar_ab_report(
        ledger_df=ledger,
        trades_df=trades,
        cache_dir=cache_dir,
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
    )

    assert report["count"] == 0
    assert "no tp1_hit_on_exit_bar_only cases remain" in report["note"]


def test_exit_bar_ab_report_computes_tp1_before_exit_delta(tmp_path: Path):
    ledger = _prepare_candidate_ledger(
        pd.DataFrame(
            [
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
                }
            ]
        )
    )
    trades = pd.DataFrame(
        [
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
                "exit_price": 100.4,
                "margin": 1000.0,
                "entry_notional": 5000.0,
                "leverage": 5,
                "pnl": 17.0,
                "reason": "decision_close:time_exit hold=30m pnl=0.0014",
            },
        ]
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    pd.DataFrame(
        [
            {"timestamp": "2026-03-02 02:00:00+00:00", "open": 100.0, "high": 100.4, "low": 99.9, "close": 100.1},
            {"timestamp": "2026-03-02 02:15:00+00:00", "open": 100.1, "high": 100.75, "low": 100.0, "close": 100.2},
            {"timestamp": "2026-03-02 02:30:00+00:00", "open": 100.2, "high": 100.9, "low": 99.95, "close": 100.4},
        ]
    ).to_parquet(cache_dir / "ETHUSDT_15m_60d_20260401.parquet")

    report = build_exit_bar_ab_report(
        ledger_df=ledger,
        trades_df=trades,
        cache_dir=cache_dir,
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
    )

    assert report["count"] == 1
    case = report["cases"][0]
    assert round(case["actual_entry_total_pnl"], 4) == 17.0
    assert round(case["hypothetical_entry_total_pnl_tp1_before_exit"], 4) == 22.0
    assert round(case["pnl_delta_tp1_before_exit"], 4) == 5.0
