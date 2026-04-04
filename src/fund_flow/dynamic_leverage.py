from __future__ import annotations

from typing import Any, Iterable, Mapping


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def get_max_leverage_by_recent_performance(
    recent_trades: Iterable[Mapping[str, Any]],
    *,
    cfg: Mapping[str, Any] | None = None,
) -> int:
    config = cfg or {}
    window = max(1, int(_to_float(config.get("dynamic_leverage_window"), 20)))
    min_samples = max(1, int(_to_float(config.get("dynamic_leverage_min_samples"), 10)))
    default_cap = max(0, int(_to_float(config.get("dynamic_leverage_default_max_leverage"), 2)))

    trades = list(recent_trades)[-window:]
    if len(trades) < min_samples:
        return default_cap

    wins = 0
    for trade in trades:
        if _to_float(trade.get("pnl"), 0.0) > 0:
            wins += 1
    win_rate = wins / max(1, len(trades))

    raw_map = config.get("dynamic_leverage_map", [])
    rules = []
    if isinstance(raw_map, list):
        for item in raw_map:
            if not isinstance(item, Mapping):
                continue
            rules.append(
                (
                    _to_float(item.get("min_win_rate"), 0.0),
                    max(0, int(_to_float(item.get("max_leverage"), 0))),
                )
            )
    rules.sort(key=lambda item: item[0], reverse=True)
    for min_win_rate, max_leverage in rules:
        if win_rate >= min_win_rate:
            return max_leverage
    return default_cap
