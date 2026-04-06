from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd


def resolve_replay_window_bounds(
    *,
    trade_window_start_iso: str = "",
    trade_window_end_iso: str = "",
    warmup_hours: int = 0,
) -> tuple[str, str]:
    trade_start = str(trade_window_start_iso or "").strip()
    trade_end = str(trade_window_end_iso or "").strip()
    if not trade_start:
        return "", trade_end
    data_start = (
        pd.Timestamp(trade_start) - pd.Timedelta(hours=max(0, int(warmup_hours or 0)))
    ).strftime("%Y-%m-%d %H:%M:%S")
    return data_start, trade_end


def timestamp_in_trade_window(
    timestamp: object,
    *,
    trade_window_start_iso: str = "",
    trade_window_end_iso: str = "",
) -> bool:
    ts = pd.Timestamp(timestamp)
    start = pd.Timestamp(trade_window_start_iso) if str(trade_window_start_iso or "").strip() else None
    end = pd.Timestamp(trade_window_end_iso) if str(trade_window_end_iso or "").strip() else None
    if start is not None and ts < start:
        return False
    if end is not None and ts > end:
        return False
    return True


def apply_market_data_window(
    market_data_map: Dict[str, Dict[str, pd.DataFrame]],
    *,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Tuple[Dict[str, Dict[str, pd.DataFrame]], List[str]]:
    if not start_time and not end_time:
        return market_data_map, []

    start_ts = pd.Timestamp(start_time) if start_time else None
    end_ts = pd.Timestamp(end_time) if end_time else None
    filtered_map: Dict[str, Dict[str, pd.DataFrame]] = {}
    dropped_symbols: List[str] = []

    for symbol, tf_map in market_data_map.items():
        filtered_tf_map: Dict[str, pd.DataFrame] = {}
        keep_symbol = True
        for tf, df in tf_map.items():
            if not isinstance(df, pd.DataFrame):
                continue
            filtered_df = df
            if start_ts is not None:
                filtered_df = filtered_df[filtered_df["timestamp"] >= start_ts]
            if end_ts is not None:
                filtered_df = filtered_df[filtered_df["timestamp"] <= end_ts]
            filtered_df = filtered_df.reset_index(drop=True)
            if filtered_df.empty:
                keep_symbol = False
                break
            filtered_tf_map[tf] = filtered_df
        if keep_symbol:
            filtered_map[symbol] = filtered_tf_map
        else:
            dropped_symbols.append(symbol)

    return filtered_map, dropped_symbols
