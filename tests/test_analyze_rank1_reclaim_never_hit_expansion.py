from pathlib import Path

import pandas as pd

from scripts.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
from scripts.analyze_rank1_reclaim_never_hit_expansion import build_never_hit_expansion_report


def test_never_hit_expansion_report_computes_15_30_45m_path_quality(tmp_path: Path):
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
                "exit_time": "2026-03-02 01:45:00+00:00",
                "exit_price": 99.9,
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "signal_score": 0.88,
                "vwap_score": 0.15,
                "adx_1h": 32.0,
                "entry_scale": 1.0,
                "margin": 1000.0,
                "entry_notional": 5000.0,
                "leverage": 5,
                "pnl": -15.0,
                "reason": "decision_close:time_exit hold=45m pnl=-0.0015",
            }
        ]
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    pd.DataFrame(
        [
            {"timestamp": "2026-03-02 01:00:00+00:00", "open": 100.0, "high": 100.2, "low": 99.7, "close": 100.1},
            {"timestamp": "2026-03-02 01:15:00+00:00", "open": 100.1, "high": 100.3, "low": 99.6, "close": 99.8},
            {"timestamp": "2026-03-02 01:30:00+00:00", "open": 99.8, "high": 100.6, "low": 99.5, "close": 100.4},
            {"timestamp": "2026-03-02 01:45:00+00:00", "open": 100.4, "high": 100.5, "low": 99.4, "close": 99.9},
        ]
    ).to_parquet(cache_dir / "BTCUSDT_15m_60d_20260401.parquet")

    report = build_never_hit_expansion_report(
        ledger_df=ledger,
        trades_df=trades,
        cache_dir=cache_dir,
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
    )

    assert report["count"] == 1
    by_h = {int(row["horizon_minutes"]): row for row in report["by_horizon"]}
    assert round(by_h[15]["avg_mfe_pct"], 4) == 0.3
    assert round(by_h[15]["avg_mae_pct"], 4) == 0.4
    assert round(by_h[15]["avg_close_return_pct"], 4) == -0.2
    assert round(by_h[30]["avg_mfe_pct"], 4) == 0.6
    assert round(by_h[30]["avg_mae_pct"], 4) == 0.5
    assert round(by_h[30]["avg_close_return_pct"], 4) == 0.4
    assert round(by_h[45]["avg_mfe_pct"], 4) == 0.6
    assert round(by_h[45]["avg_mae_pct"], 4) == 0.6
    assert round(by_h[45]["avg_close_return_pct"], 4) == -0.1
    assert by_h[30]["tp1_hit_rate_pct"] == 0.0
    assert by_h[30]["near_tp1_75_rate_pct"] == 100.0


def test_never_hit_expansion_report_handles_no_cases(tmp_path: Path):
    ledger = _prepare_candidate_ledger(pd.DataFrame())
    trades = pd.DataFrame()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    report = build_never_hit_expansion_report(
        ledger_df=ledger,
        trades_df=trades,
        cache_dir=cache_dir,
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
    )

    assert report["count"] == 0
    assert "no tp1_never_hit" in report["note"]
