from pathlib import Path

import pandas as pd

from scripts.compare_pure_bot_fill_paths import build_report


def test_build_report_flags_wick_only_pure_fill_against_unopened_bot_candidate(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2026-03-02 01:00:00+00:00")],
            "open": [100.3],
            "high": [100.5],
            "low": [99.99],
            "close": [100.2],
        }
    ).to_parquet(cache_dir / "BTCUSDT_15m_60d_test.parquet", index=False)

    pure_trades = pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "entry_time": "2026-03-02 01:00:00+00:00",
                "exit_time": "2026-03-02 02:00:00+00:00",
                "entry_price": 100.0,
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "pnl": 25.0,
                "entry_initial_time_in_force": "IOC",
                "entry_time_in_force": "IOC",
                "entry_degradation_path": "[]",
            }
        ]
    )
    bot_trades = pd.DataFrame(
        columns=[
            "symbol",
            "side",
            "entry_time",
            "exit_time",
            "entry_price",
            "signal_type_1h",
            "vwap_state",
            "pnl",
            "reason",
        ]
    )
    candidate_ledger = pd.DataFrame(
        [
            {
                "timestamp": "2026-03-02 01:00:00+00:00",
                "symbol": "BTCUSDT",
                "local_operation": "Operation.BUY",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "generated_open_candidate": True,
                "final_opened": False,
                "final_reject_reason": "target_portion_below_min_open",
            }
        ]
    )

    report = build_report(
        pure_trades_df=pure_trades,
        bot_trades_df=bot_trades,
        candidate_ledger_df=candidate_ledger,
        cache_dir=cache_dir,
        tolerance_minutes=0,
        direct_ioc_min_penetration_bps=5.0,
    )

    assert report["summary"]["pure_entry_count"] == 1
    assert report["summary"]["divergence_stage_counts"]["bot_candidate_not_opened"] == 1
    assert report["summary"]["wick_only_pure_entries"] == 1
    assert report["rows"][0]["pure_fill_wick_only_touch"] is True
    assert report["rows"][0]["pure_strict_fill_valid"] is False
    assert report["rows"][0]["bot_final_opened"] is False


def test_build_report_compares_matched_opened_bot_trade() -> None:
    pure_trades = pd.DataFrame(
        [
            {
                "symbol": "ETHUSDT",
                "side": "short",
                "entry_time": "2026-03-02 02:00:00+00:00",
                "exit_time": "2026-03-02 02:30:00+00:00",
                "entry_price": 101.0,
                "signal_type_1h": "green_bar_growing",
                "vwap_state": "short_retest_reject",
                "pnl": 10.0,
                "entry_initial_time_in_force": "IOC",
                "entry_time_in_force": "IOC",
                "entry_degradation_path": "[]",
                "entry_fill_close_through": True,
                "entry_fill_penetration_bps": 8.0,
                "entry_fill_wick_only_touch": False,
            }
        ]
    )
    bot_trades = pd.DataFrame(
        [
            {
                "symbol": "ETHUSDT",
                "side": "short",
                "entry_time": "2026-03-02 02:15:00+00:00",
                "exit_time": "2026-03-02 03:00:00+00:00",
                "entry_price": 101.3,
                "signal_type_1h": "green_bar_growing",
                "vwap_state": "short_retest_reject",
                "pnl": -4.0,
                "reason": "time_exit",
            }
        ]
    )

    report = build_report(
        pure_trades_df=pure_trades,
        bot_trades_df=bot_trades,
        candidate_ledger_df=pd.DataFrame(),
        cache_dir=None,
        tolerance_minutes=20,
        direct_ioc_min_penetration_bps=5.0,
    )

    assert report["summary"]["matched_bot_trade_count"] == 1
    assert report["summary"]["divergence_stage_counts"]["bot_trade_opened"] == 1
    assert report["rows"][0]["entry_time_delta_minutes"] == 15.0
    assert report["rows"][0]["entry_price_delta_bps"] > 0
    assert report["rows"][0]["bot_trade_pnl"] == -4.0
