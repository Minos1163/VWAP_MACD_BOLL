import pandas as pd

from src.fund_flow.replay_window import apply_market_data_window, resolve_replay_window_bounds


def test_resolve_replay_window_bounds_expands_trade_window_by_warmup_hours() -> None:
    data_start, data_end = resolve_replay_window_bounds(
        trade_window_start_iso="2026-03-05T03:00:00",
        trade_window_end_iso="2026-04-04T03:00:00",
        warmup_hours=24,
    )

    assert data_start == "2026-03-04 03:00:00"
    assert data_end == "2026-04-04T03:00:00"


def test_apply_market_data_window_filters_all_timeframes() -> None:
    ts = pd.to_datetime(
        [
            "2026-03-04 02:45:00",
            "2026-03-04 03:00:00",
            "2026-03-05 03:00:00",
            "2026-04-04 03:00:00",
            "2026-04-04 03:15:00",
        ]
    )
    df = pd.DataFrame({"timestamp": ts, "close": [1, 2, 3, 4, 5]})
    market_data = {
        "SOLUSDT": {
            "15m": df.copy(),
            "1h": df.copy(),
            "4h": df.copy(),
        }
    }

    filtered, dropped = apply_market_data_window(
        market_data,
        start_time="2026-03-04 03:00:00",
        end_time="2026-04-04T03:00:00",
    )

    assert dropped == []
    assert list(filtered["SOLUSDT"]["15m"]["timestamp"]) == [
        pd.Timestamp("2026-03-04 03:00:00"),
        pd.Timestamp("2026-03-05 03:00:00"),
        pd.Timestamp("2026-04-04 03:00:00"),
    ]
