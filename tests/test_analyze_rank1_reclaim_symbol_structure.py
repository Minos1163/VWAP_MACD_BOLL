from pathlib import Path
import json

import pandas as pd

from scripts.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
from scripts.analyze_rank1_reclaim_symbol_structure import build_symbol_structure_report


def test_symbol_structure_report_extracts_first_45m_bar_path(tmp_path: Path):
    ledger = _prepare_candidate_ledger(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-03-02 01:00:00+00:00",
                    "symbol": "SOLUSDT",
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
                "symbol": "SOLUSDT",
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
    ).to_parquet(cache_dir / "SOLUSDT_15m_60d_20260401.parquet")

    report = build_symbol_structure_report(
        ledger_df=ledger,
        trades_df=trades,
        cache_dir=cache_dir,
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
        symbol="SOLUSDT",
    )

    assert report["count"] == 1
    case = report["cases"][0]
    assert case["symbol"] == "SOLUSDT"
    assert len(case["bars_first_45m"]) == 3
    assert round(case["bars_first_45m"][0]["mfe_pct"], 4) == 0.3
    assert round(case["bars_first_45m"][1]["mfe_pct"], 4) == 0.6
    assert round(case["bars_first_45m"][2]["close_return_pct"], 4) == -0.1
    json.dumps(report, ensure_ascii=False)


def test_symbol_structure_report_handles_missing_cases(tmp_path: Path):
    report = build_symbol_structure_report(
        ledger_df=_prepare_candidate_ledger(pd.DataFrame()),
        trades_df=pd.DataFrame(),
        cache_dir=tmp_path,
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
        symbol="SOLUSDT",
    )

    assert report["count"] == 0
    assert "no matching tp1_never_hit" in report["note"]
