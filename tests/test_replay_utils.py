from datetime import datetime, timezone

from src.fund_flow.replay_utils import (
    build_flow_context_with_proxy,
    build_microstructure_stats_from_records,
    get_micro_structure_proxy,
)


def test_build_microstructure_stats_from_records_groups_by_symbol_and_session():
    records = [
        {
            "ts": "2026-03-24T01:00:00+00:00",
            "symbol": "RENDERUSDT",
            "spread_bps": 0.00011,
            "depth_ratio": 1.10,
            "imbalance": 0.04,
        },
        {
            "ts": "2026-03-24T02:00:00+00:00",
            "symbol": "RENDERUSDT",
            "spread_bps": 0.00013,
            "depth_ratio": 1.00,
            "imbalance": 0.02,
        },
        {
            "ts": "2026-03-24T10:00:00+00:00",
            "symbol": "RENDERUSDT",
            "spread_bps": 0.00021,
            "depth_ratio": 0.96,
            "imbalance": -0.01,
        },
    ]

    stats = build_microstructure_stats_from_records(records)

    assert stats["RENDERUSDT"]["asia"]["spread_bps_p50"] == 0.00012
    assert stats["RENDERUSDT"]["asia"]["depth_ratio_p50"] == 1.05
    assert stats["RENDERUSDT"]["europe"]["imbalance_p50"] == -0.01


def test_get_micro_structure_proxy_uses_symbol_session_then_default():
    stats = {
        "RENDERUSDT": {
            "asia": {
                "spread_bps_p50": 0.00012,
                "depth_ratio_p50": 1.05,
                "imbalance_p50": 0.03,
            }
        },
        "_default": {
            "asia": {
                "spread_bps_p50": 0.0004,
                "depth_ratio_p50": 1.0,
                "imbalance_p50": 0.0,
            }
        },
    }

    proxy = get_micro_structure_proxy(
        "RENDERUSDT",
        datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
        historical_stats=stats,
    )
    assert proxy.spread_bps == 0.00012

    fallback = get_micro_structure_proxy(
        "UNKNOWN",
        datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
        historical_stats=stats,
    )
    assert fallback.spread_bps == 0.0004


def test_build_flow_context_with_proxy_only_fills_missing_fields():
    stats = {
        "RENDERUSDT": {
            "asia": {
                "spread_bps_p50": 0.00012,
                "depth_ratio_p50": 1.05,
                "imbalance_p50": 0.03,
            }
        }
    }

    ctx = build_flow_context_with_proxy(
        symbol="RENDERUSDT",
        bar_time=datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
        raw_flow_context={
            "spread_bps": None,
            "depth_ratio": 1.2,
            "imbalance": None,
        },
        historical_stats=stats,
    )

    assert ctx["spread_bps"] == 0.00012
    assert ctx["spread_bps_is_proxy"] is True
    assert ctx["depth_ratio"] == 1.2
    assert "depth_ratio_is_proxy" not in ctx
    assert ctx["imbalance"] == 0.03
    assert ctx["imbalance_is_proxy"] is True
