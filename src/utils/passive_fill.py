from __future__ import annotations

from typing import Dict, Tuple


def classify_passive_limit_fill(
    *,
    side: str,
    limit_price: float,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
) -> Dict[str, float | bool | str]:
    side_lower = str(side or "").lower()
    entry_price = float(limit_price)
    if entry_price <= 0:
        raise ValueError("limit_price must be positive")

    if side_lower == "short":
        touched = high_price >= entry_price
        marketable_at_open = open_price >= entry_price
        penetration_bps = max(0.0, (high_price - entry_price) / entry_price * 10000.0)
        close_through = close_price >= entry_price
        wick_only_touch = touched and (max(open_price, close_price) < entry_price)
    else:
        touched = low_price <= entry_price
        marketable_at_open = open_price <= entry_price
        penetration_bps = max(0.0, (entry_price - low_price) / entry_price * 10000.0)
        close_through = close_price <= entry_price
        wick_only_touch = touched and (min(open_price, close_price) > entry_price)

    range_bps = max(0.0, (high_price - low_price) / entry_price * 10000.0)
    penetration_share_of_range = (penetration_bps / range_bps) if range_bps > 0 else 0.0
    touch_class = "untouched"
    if touched and close_through:
        touch_class = "close_through"
    elif touched and wick_only_touch:
        touch_class = "wick_only_touch"
    elif touched:
        touch_class = "body_touch"

    return {
        "touched": bool(touched),
        "marketable_at_open": bool(marketable_at_open),
        "penetration_bps": float(penetration_bps),
        "range_bps": float(range_bps),
        "penetration_share_of_range": float(penetration_share_of_range),
        "close_through": bool(close_through),
        "wick_only_touch": bool(wick_only_touch),
        "touch_class": touch_class,
    }


def direct_ioc_fill_is_valid(
    classification: Dict[str, float | bool | str],
    *,
    mode: str = "touch",
    min_penetration_bps: float = 0.0,
) -> Tuple[bool, str]:
    mode_key = str(mode or "touch").strip().lower()
    touched = bool(classification.get("touched", False))
    if not touched:
        return False, "untouched"

    if mode_key in {"touch", "legacy_touch", "any_touch"}:
        return True, "touched"

    close_through = bool(classification.get("close_through", False))
    penetration_bps = float(classification.get("penetration_bps", 0.0) or 0.0)
    threshold = max(0.0, float(min_penetration_bps or 0.0))

    if mode_key in {"close_through_or_penetration", "strict"}:
        if close_through:
            return True, "close_through"
        if penetration_bps >= threshold:
            return True, f"penetration_ge_{threshold:g}bps"
        return False, "wick_only_or_shallow_touch"

    raise ValueError(f"unsupported direct IOC fill mode: {mode}")
