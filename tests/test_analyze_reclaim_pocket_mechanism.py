from pathlib import Path

import pandas as pd
import pytest

from scripts.analyze_reclaim_pocket_mechanism import _aggregate_logical_entries, build_fill_composition


def test_build_fill_composition_reports_wick_only_share_for_reclaim_pocket(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "timestamp": [
                pd.Timestamp("2026-03-02 01:00:00+00:00"),
                pd.Timestamp("2026-03-02 01:15:00+00:00"),
            ],
            "open": [100.3, 100.2],
            "high": [100.5, 100.3],
            "low": [99.99, 99.7],
            "close": [100.2, 99.9],
        }
    ).to_parquet(cache_dir / "BTCUSDT_15m_60d_test.parquet", index=False)

    entries = _aggregate_logical_entries(
        pd.DataFrame(
            [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "entry_time": "2026-03-02 01:00:00+00:00",
                    "exit_time": "2026-03-02 01:30:00+00:00",
                    "entry_price": 100.0,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "pnl": 20.0,
                    "reason": "take_profit",
                },
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "entry_time": "2026-03-02 01:15:00+00:00",
                    "exit_time": "2026-03-02 02:00:00+00:00",
                    "entry_price": 100.0,
                    "signal_type_1h": "red_bar_growing",
                    "vwap_state": "long_reclaim_confirmed",
                    "pnl": -5.0,
                    "reason": "stop_loss_intrabar",
                },
            ]
        )
    )

    report = build_fill_composition(entries, cache_dir=cache_dir, strict_penetration_bps=5.0)

    assert report["entry_count"] == 2
    assert report["wick_only_touch_rate_pct"] == pytest.approx(50.0, rel=1e-9)
    assert report["strict_fill_valid_count"] == 1
    assert report["by_symbol"][0]["symbol"] == "BTCUSDT"
    assert report["by_symbol"][0]["wick_only_touch_rate_pct"] == pytest.approx(50.0, rel=1e-9)
