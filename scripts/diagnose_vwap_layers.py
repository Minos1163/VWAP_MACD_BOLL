from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from backtest_fund_flow_bot_like import BotLikeReplayEngine  # type: ignore
    from backtest_macd_v2 import (  # type: ignore
        apply_backtest_profile,
        build_backtest_config,
        build_strategy_config,
        filter_market_data_by_time_range,
        load_symbol_data,
    )
except ModuleNotFoundError:
    from scripts.backtest_fund_flow_bot_like import BotLikeReplayEngine  # type: ignore
    from scripts.backtest_macd_v2 import (  # type: ignore
        apply_backtest_profile,
        build_backtest_config,
        build_strategy_config,
        filter_market_data_by_time_range,
        load_symbol_data,
    )


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _metadata(decision: Any) -> Dict[str, Any]:
    return decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}


def _text_blob(reason: str, metadata: Dict[str, Any]) -> str:
    debug_payload = metadata.get("macd_v2_debug") if isinstance(metadata.get("macd_v2_debug"), dict) else {}
    parts = [str(reason or "")]
    for key in ("hold_attribution", "hold_reason", "reject_reason", "reason"):
        value = debug_payload.get(key)
        if value:
            parts.append(str(value))
    notes = debug_payload.get("notes")
    if isinstance(notes, list):
        parts.extend(str(item) for item in notes if item)
    return " | ".join(parts).lower()


def _categorize_block(reason: str, metadata: Dict[str, Any]) -> str:
    text = _text_blob(reason, metadata)
    if "vwap_score_filter" in text:
        return "vwap_score_filter"
    if "vwap_hard_block" in text:
        return "vwap_hard_block"
    if "volume_vwap_both_low" in text:
        return "volume_vwap_both_low"
    if "preflip" in text or "shrink" in text or "缩短不足" in text:
        return "preflip_shrink"
    return "other"


def _percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(float(ordered[0]), 4)
    pos = (len(ordered) - 1) * pct
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return round(float(ordered[low]), 4)
    fraction = pos - low
    result = ordered[low] + (ordered[high] - ordered[low]) * fraction
    return round(float(result), 4)


def _summarize_rows(rows: List[Dict[str, Any]], *, top_n: int = 10) -> Dict[str, Any]:
    scores = [float(row["vwap_score"]) for row in rows]
    symbol_counts = Counter(str(row.get("symbol") or "") for row in rows)
    pocket_counts = Counter(str(row.get("pocket_key") or "") for row in rows)
    signal_type_counts = Counter(str(row.get("signal_type_1h") or "") for row in rows)
    pocket_scores: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        pocket_scores[str(row.get("pocket_key") or "")].append(float(row["vwap_score"]))

    pocket_breakdown = []
    for pocket_key, count in pocket_counts.most_common(top_n):
        series = pocket_scores[pocket_key]
        pocket_breakdown.append(
            {
                "pocket_key": pocket_key,
                "count": count,
                "vwap_score_p50": _percentile(series, 0.50),
                "vwap_score_p90": _percentile(series, 0.90),
                "vwap_score_max": _percentile(series, 1.00),
            }
        )

    near_threshold = sorted(
        rows,
        key=lambda row: (-float(row["vwap_score"]), str(row.get("time") or ""), str(row.get("symbol") or "")),
    )[:top_n]

    return {
        "count": len(rows),
        "vwap_score_p50": _percentile(scores, 0.50),
        "vwap_score_p90": _percentile(scores, 0.90),
        "vwap_score_max": _percentile(scores, 1.00),
        "top_symbols": dict(symbol_counts.most_common(top_n)),
        "top_signal_types": dict(signal_type_counts.most_common(top_n)),
        "top_pockets": pocket_breakdown,
        "near_threshold_examples": near_threshold,
    }


def _build_row(symbol: str, analysis: Dict[str, Any], decision: Any) -> Dict[str, Any]:
    metadata = _metadata(decision)
    debug_payload = metadata.get("macd_v2_debug") if isinstance(metadata.get("macd_v2_debug"), dict) else {}
    signal_type_1h = str(metadata.get("signal_type_1h", "") or "")
    vwap_state = str(metadata.get("vwap_state", "") or "")
    return {
        "symbol": symbol,
        "time": str(analysis["time"]),
        "reason": str(decision.reason or ""),
        "signal_score": round(_to_float(metadata.get("signal_score"), 0.0), 4),
        "vwap_score": round(_to_float(metadata.get("vwap_score"), 0.0), 4),
        "signal_type_1h": signal_type_1h,
        "vwap_state": vwap_state,
        "pocket_key": f"{signal_type_1h}|{vwap_state}",
        "entry_type_15m": str(metadata.get("entry_type_15m", "") or ""),
        "is_trial_entry": bool(metadata.get("is_trial_entry", False)),
        "macd_4h_shrink_pct": round(
            _to_float(metadata.get("macd_4h_shrink_pct"), _to_float(debug_payload.get("macd_4h_shrink_pct"), 0.0)),
            4,
        ),
    }


def build_diagnosis(
    *,
    config_path: str,
    start_time: Optional[str],
    end_time: Optional[str],
    profile_name: Optional[str],
) -> Dict[str, Any]:
    runtime_cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    runtime_cfg, applied_profile = apply_backtest_profile(runtime_cfg, profile_name)
    config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path=config_path,
        initial_capital=10000.0,
        fee_rate=0.0004,
        max_positions_override=None,
        fixed_leverage=None,
        profile_name=applied_profile,
        window_start_iso=start_time or "",
        window_end_iso=end_time or "",
    )
    strategy_config = build_strategy_config(runtime_cfg)
    engine = BotLikeReplayEngine(config, strategy_config, runtime_cfg)

    market_data_map: Dict[str, Dict[str, Any]] = {}
    for symbol in config.symbols:
        data = load_symbol_data(config.data_dir, symbol, strategy_config)
        if data:
            market_data_map[symbol] = data
    if market_data_map and (start_time or end_time):
        market_data_map, _ = filter_market_data_by_time_range(
            market_data_map,
            start_time=start_time,
            end_time=end_time,
        )

    analysis_attempts = 0
    analysis_ready = 0
    analysis_skipped = Counter()
    categorized_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    uncategorized_holds = Counter()

    for symbol, data in market_data_map.items():
        tf_15m = data["15m"]
        for idx in range(len(tf_15m)):
            analysis_attempts += 1
            analysis, analysis_status = engine._build_analysis_with_status(symbol, data, idx)
            if analysis is None:
                analysis_skipped[analysis_status] += 1
                continue
            analysis_ready += 1
            decision = engine._decide(symbol, analysis)
            if getattr(decision.operation, "value", "") != "hold":
                continue
            metadata = _metadata(decision)
            category = _categorize_block(str(decision.reason or ""), metadata)
            row = _build_row(symbol, analysis, decision)
            categorized_rows[category].append(row)
            if category == "other":
                uncategorized_holds[str(decision.reason or "")] += 1

    entry_filters = (
        runtime_cfg.get("fund_flow", {})
        .get("macd_mtf_strategy_v2", {})
        .get("entry_filters", {})
    )
    pocket_cfg = entry_filters.get("pocket_entry_overrides", {}).get("red_bar_growing|long_dual_support", {})

    categories = {}
    for category in ("vwap_score_filter", "vwap_hard_block", "volume_vwap_both_low", "preflip_shrink"):
        categories[category] = _summarize_rows(categorized_rows.get(category, []))

    categories["other_hold"] = {
        "count": len(categorized_rows.get("other", [])),
        "top_reasons": dict(uncategorized_holds.most_common(20)),
    }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": {"start": start_time, "end": end_time},
        "config_path": config_path,
        "profile_name": applied_profile,
        "symbols_loaded": len(market_data_map),
        "replay_input_counters": {
            "analysis_attempts": analysis_attempts,
            "analysis_ready": analysis_ready,
            "analysis_skipped": dict(analysis_skipped),
        },
        "active_thresholds": {
            "min_signal_score": entry_filters.get("min_signal_score"),
            "min_vwap_score_for_entry": entry_filters.get("min_vwap_score_for_entry"),
            "flip_bullish_min_vwap_score": entry_filters.get("flip_bullish_min_vwap_score"),
            "preflip_trial_min_shrink_pct_long": entry_filters.get("preflip_trial_min_shrink_pct_long"),
            "preflip_trial_min_signal_score": entry_filters.get("preflip_trial_min_signal_score"),
            "ld_support_min_signal_score": pocket_cfg.get("min_signal_score"),
            "ld_support_min_vwap_score": pocket_cfg.get("min_vwap_score"),
        },
        "categories": categories,
        "notes": [
            "vwap_score_filter/vwap_hard_block/volume_vwap_both_low are measured directly from bot-like replay hold reasons.",
            "preflip_shrink visibility depends on replay reason text and may undercount compared with live runtime logs if the engine compresses blockers into generic none_score holds.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose VWAP-layer and 4H preflip blockers from bot-like replay decisions")
    parser.add_argument("--config", required=True, help="runtime config path")
    parser.add_argument("--start", default=None, help="inclusive window start")
    parser.add_argument("--end", default=None, help="inclusive window end")
    parser.add_argument("--profile", default=None, help="optional backtest profile")
    parser.add_argument("--output", required=True, help="output json path")
    args = parser.parse_args()

    result = build_diagnosis(
        config_path=args.config,
        start_time=args.start,
        end_time=args.end,
        profile_name=args.profile,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
