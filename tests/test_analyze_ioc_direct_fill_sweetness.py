from __future__ import annotations

import pandas as pd
import pytest

from scripts.analyze_ioc_direct_fill_sweetness import (
    _classify_fill,
    _prepare_direct_ioc_entries,
    build_fill_sweetness_report,
)


def test_classify_fill_marks_long_wick_only_touch() -> None:
    row = pd.Series({"side": "long", "entry_price": 100.0})
    bar = pd.Series({"open": 100.3, "high": 100.6, "low": 99.98, "close": 100.2})

    result = _classify_fill(row, bar)

    assert result["touched"] is True
    assert result["marketable_at_open"] is False
    assert result["wick_only_touch"] is True
    assert result["close_through"] is False
    assert result["penetration_bps"] == pytest.approx(2.0, rel=1e-9)


def test_prepare_direct_ioc_entries_aggregates_partial_rows() -> None:
    trades = pd.DataFrame(
        [
            {
                "symbol": "SOLUSDT",
                "side": "long",
                "entry_time": "2026-03-01 00:15:00+00:00",
                "exit_time": "2026-03-01 00:30:00+00:00",
                "entry_price": 100.0,
                "pnl": 10.0,
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "vwap_score": 0.18,
                "leverage": 5,
                "entry_initial_time_in_force": "IOC",
                "entry_time_in_force": "IOC",
                "entry_degradation_path": "[]",
            },
            {
                "symbol": "SOLUSDT",
                "side": "long",
                "entry_time": "2026-03-01 00:15:00+00:00",
                "exit_time": "2026-03-01 00:45:00+00:00",
                "entry_price": 100.0,
                "pnl": -2.0,
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "vwap_score": 0.18,
                "leverage": 5,
                "entry_initial_time_in_force": "IOC",
                "entry_time_in_force": "IOC",
                "entry_degradation_path": "[]",
            },
        ]
    )
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
    trades["exit_time"] = pd.to_datetime(trades["exit_time"], utc=True)

    result = _prepare_direct_ioc_entries(trades)

    assert len(result) == 1
    assert float(result.iloc[0]["total_pnl"]) == 8.0
    assert bool(result.iloc[0]["win"]) is True
    assert pd.Timestamp(result.iloc[0]["exit_time"]) == pd.Timestamp("2026-03-01 00:45:00+00:00")


def test_build_fill_sweetness_report_summarizes_strict_scenarios(tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    bars = pd.DataFrame(
        {
            "timestamp": [
                pd.Timestamp("2026-03-01 00:15:00+00:00"),
                pd.Timestamp("2026-03-01 00:30:00+00:00"),
            ],
            "open": [100.3, 100.1],
            "high": [100.5, 100.2],
            "low": [99.99, 99.80],
            "close": [100.25, 99.95],
        }
    )
    bars.to_parquet(cache_dir / "SOLUSDT_15m_60d_20260401.parquet", index=False)
    trades = pd.DataFrame(
        [
            {
                "symbol": "SOLUSDT",
                "side": "long",
                "entry_time": "2026-03-01 00:15:00+00:00",
                "exit_time": "2026-03-01 00:45:00+00:00",
                "entry_price": 100.0,
                "pnl": 12.0,
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "vwap_score": 0.18,
                "leverage": 5,
                "entry_initial_time_in_force": "IOC",
                "entry_time_in_force": "IOC",
                "entry_degradation_path": "[]",
            },
            {
                "symbol": "SOLUSDT",
                "side": "long",
                "entry_time": "2026-03-01 00:30:00+00:00",
                "exit_time": "2026-03-01 00:45:00+00:00",
                "entry_price": 100.0,
                "pnl": -5.0,
                "signal_type_1h": "flip_bullish",
                "vwap_state": "long_reclaim_confirmed",
                "vwap_score": 0.19,
                "leverage": 5,
                "entry_initial_time_in_force": "IOC",
                "entry_time_in_force": "IOC",
                "entry_degradation_path": "[]",
            },
        ]
    )
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
    trades["exit_time"] = pd.to_datetime(trades["exit_time"], utc=True)

    report = build_fill_sweetness_report(trades_df=trades, cache_dir=cache_dir)

    assert report["entry_count"] == 2
    assert report["wick_only_touch_count"] == 1
    assert report["close_through_count"] == 1
    assert report["scenarios"]["penetration_ge_2bps"]["count"] == 1
    assert report["scenarios"]["close_through"]["count"] == 1
