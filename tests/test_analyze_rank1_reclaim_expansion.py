from pathlib import Path
import json

import pandas as pd

from scripts.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
from scripts.analyze_rank1_reclaim_expansion import _annotate_expansion, build_report, _resolve_tp1_pct
from scripts.analyze_rank1_reclaim_quality import _aggregate_rank1_entries


def test_rank1_reclaim_expansion_reports_tp1_progress_for_time_exit(tmp_path: Path):
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
                    "cluster_key": "btc-1",
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
                    "cluster_key": "eth-1",
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
            {"timestamp": "2026-03-02 01:15:00+00:00", "open": 100.4, "high": 101.4, "low": 100.1, "close": 100.6},
            {"timestamp": "2026-03-02 01:30:00+00:00", "open": 100.6, "high": 101.2, "low": 99.8, "close": 100.0},
        ]
    ).to_parquet(cache_dir / "BTCUSDT_15m_60d_20260401.parquet")
    pd.DataFrame(
        [
            {"timestamp": "2026-03-02 02:00:00+00:00", "open": 100.0, "high": 101.8, "low": 99.9, "close": 101.0},
            {"timestamp": "2026-03-02 02:15:00+00:00", "open": 101.0, "high": 101.95, "low": 100.5, "close": 100.8},
            {"timestamp": "2026-03-02 02:30:00+00:00", "open": 100.8, "high": 101.4, "low": 99.7, "close": 100.0},
        ]
    ).to_parquet(cache_dir / "ETHUSDT_15m_60d_20260401.parquet")

    entries = _aggregate_rank1_entries(ledger, trades, "red_bar_growing", "long_reclaim_confirmed")
    expansion = _annotate_expansion(entries, trades, cache_dir, tp1_pct=0.02)
    report = build_report(expansion, "red_bar_growing", "long_reclaim_confirmed", tp1_pct=0.02)

    time_exit_only = report["time_exit_only"]
    assert time_exit_only["count"] == 2
    assert time_exit_only["tp1_hit_rate_pct"] == 0.0
    assert time_exit_only["near_tp1_90_rate_pct"] == 50.0
    assert time_exit_only["near_tp1_75_rate_pct"] == 50.0
    assert time_exit_only["near_tp1_50_rate_pct"] == 100.0


def test_resolve_tp1_pct_reads_first_tp_level_from_config(tmp_path: Path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(
        json.dumps({"fund_flow": {"take_profit_pct_levels": [0.008, 0.012, 0.02]}}),
        encoding="utf-8",
    )
    assert _resolve_tp1_pct(cfg_path, None) == 0.008
