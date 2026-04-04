import pandas as pd

from scripts.analyze_live_frontend_veto_30d import (
    _daily_distribution,
    _overall_frontend_summary,
    _vwap_long_band_report,
)


def test_daily_distribution_calculates_pct_of_day():
    df = pd.DataFrame(
        [
            {"day_utc": "2026-04-01", "category": "vwap_hard_block"},
            {"day_utc": "2026-04-01", "category": "vwap_hard_block"},
            {"day_utc": "2026-04-01", "category": "volume_vwap_both_low"},
            {"day_utc": "2026-04-02", "category": "preflip_shrink"},
        ]
    )
    report = _daily_distribution(df)
    first = next(row for row in report if row["day_utc"] == "2026-04-01" and row["category"] == "vwap_hard_block")
    assert first["count"] == 2
    assert first["pct_of_day"] == round(2 * 100.0 / 3, 4)


def test_overall_frontend_summary_splits_categories_and_directions():
    df = pd.DataFrame(
        [
            {"category": "vwap_hard_block", "direction": "long", "close_ret_pct_2h": 0.01, "close_ret_pct_4h": 0.02},
            {"category": "vwap_hard_block", "direction": "long", "close_ret_pct_2h": -0.01, "close_ret_pct_4h": 0.00},
            {"category": "vwap_hard_block", "direction": "long", "close_ret_pct_2h": None, "close_ret_pct_4h": None},
            {"category": "preflip_shrink", "direction": "short", "close_ret_pct_2h": -0.02, "close_ret_pct_4h": -0.03},
        ]
    )
    report = _overall_frontend_summary(df)
    row = next(item for item in report if item["category"] == "vwap_hard_block" and item["direction"] == "long")
    assert row["count"] == 3
    assert row["pct_of_all"] == round(3 * 100.0 / 4, 4)
    assert row["avg_close_ret_2h_pct"] == 0.0
    assert row["followthrough_rate_2h_pct"] == 50.0


def test_vwap_long_band_report_buckets_scores():
    df = pd.DataFrame(
        [
            {"category": "vwap_hard_block", "direction": "long", "vwap_score": 0.10, "close_ret_pct_2h": 0.01, "close_ret_pct_4h": 0.02},
            {"category": "vwap_hard_block", "direction": "long", "vwap_score": 0.11, "close_ret_pct_2h": -0.02, "close_ret_pct_4h": 0.01},
            {"category": "vwap_score_filter", "direction": "long", "vwap_score": 0.07, "close_ret_pct_2h": 0.03, "close_ret_pct_4h": 0.04},
            {"category": "volume_vwap_both_low", "direction": "long", "vwap_score": 0.09, "close_ret_pct_2h": 0.05, "close_ret_pct_4h": 0.06},
        ]
    )
    report = _vwap_long_band_report(df)
    mid = next(item for item in report if item["band"] == "0.10_0.12")
    assert mid["count"] == 2
    assert mid["categories"]["vwap_hard_block"] == 2
    low = next(item for item in report if item["band"] == "lt_0.08")
    assert low["count"] == 1
    assert low["categories"]["vwap_score_filter"] == 1
