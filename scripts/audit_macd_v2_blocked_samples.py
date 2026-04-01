from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from collections import Counter
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


def _extract_signal_score(metadata: Dict[str, Any]) -> Optional[float]:
    values = []
    for key in ("signal_score", "final_long_score", "final_short_score", "long_score", "short_score"):
        raw = metadata.get(key)
        try:
            if raw is not None:
                values.append(float(raw))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


def _optional_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_sample(symbol: str, analysis: Dict[str, Any], decision, score: float) -> Dict[str, Any]:
    metadata = decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}
    tf15 = analysis["flow_context"]["timeframes"]["15m"]
    tf1h = analysis["flow_context"]["timeframes"]["1h"]
    tf4h = analysis["flow_context"]["timeframes"]["4h"]
    return {
        "symbol": symbol,
        "time": str(analysis["time"]),
        "score": round(score, 4),
        "reason": str(decision.reason or ""),
        "missing_reason": str(metadata.get("entry_hard_gate_missing_reason", "") or ""),
        "signal_type_1h": str(metadata.get("signal_type_1h", "") or ""),
        "entry_mode": str(metadata.get("entry_mode", "") or ""),
        "price": float(analysis["price"]),
        "vwap_score": float(metadata.get("vwap_score", 0.0) or 0.0),
        "regime": str(metadata.get("regime") or metadata.get("engine") or ""),
        "atr_pct": float(tf1h.get("atr_pct", analysis["flow_context"].get("atr_pct", 0.0)) or 0.0),
        "adx_1h": float(tf1h.get("adx", 0.0) or 0.0),
        "adx_4h": float(tf4h.get("adx", 0.0) or 0.0),
        "spread_bps": _optional_float(tf15.get("spread_bps", analysis["flow_context"].get("spread_bps"))),
        "depth_ratio": _optional_float(tf15.get("depth_ratio", analysis["flow_context"].get("depth_ratio"))),
        "imbalance": _optional_float(tf15.get("imbalance", analysis["flow_context"].get("imbalance"))),
        "cvd_momentum": _optional_float(tf15.get("cvd_momentum", analysis["flow_context"].get("cvd_momentum"))),
        "cvd_ratio": _optional_float(tf15.get("cvd_ratio", analysis["flow_context"].get("cvd_ratio"))),
        "oi_delta_ratio": _optional_float(tf1h.get("oi_delta_ratio", analysis["flow_context"].get("oi_delta_ratio"))),
        "structural_vwap": float(tf1h.get("structural_vwap", 0.0) or 0.0),
        "close_1h": float(tf1h.get("close", 0.0) or 0.0),
        "close_4h": float(tf4h.get("close", 0.0) or 0.0),
    }


def _pick_diverse_samples(rows: List[Dict[str, Any]], sample_limit: int, per_symbol_limit: int) -> List[Dict[str, Any]]:
    by_priority = sorted(
        rows,
        key=lambda item: (-float(item.get("score", 0.0)), str(item.get("time", "")), str(item.get("symbol", ""))),
    )
    selected: List[Dict[str, Any]] = []
    symbol_counts: Counter[str] = Counter()
    for row in by_priority:
        symbol = str(row.get("symbol", ""))
        if symbol_counts[symbol] >= per_symbol_limit:
            continue
        selected.append(row)
        symbol_counts[symbol] += 1
        if len(selected) >= sample_limit:
            break
    if len(selected) >= sample_limit:
        return selected
    for row in by_priority:
        if row in selected:
            continue
        selected.append(row)
        if len(selected) >= sample_limit:
            break
    return selected


def _collect_samples(
    *,
    config_path: str,
    start_time: Optional[str],
    end_time: Optional[str],
    profile_name: Optional[str],
    min_score: float,
    sample_limit: int,
    per_symbol_limit: int,
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

    l1_rows: List[Dict[str, Any]] = []
    l3_rows: List[Dict[str, Any]] = []
    l3_imbalance_rows: List[Dict[str, Any]] = []

    l1_total = 0
    l3_total = 0
    l3_imbalance_total = 0

    for symbol, data in market_data_map.items():
        tf_15m = data["15m"]
        for idx in range(len(tf_15m)):
            analysis = engine._build_analysis(symbol, data, idx)
            if analysis is None:
                continue
            decision = engine._decide(symbol, analysis)
            if decision.operation.value != "hold":
                continue
            metadata = decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}
            score = _extract_signal_score(metadata)
            if score is None or score < min_score:
                continue
            reason = str(decision.reason or "")
            if reason.startswith("entry_hard_gate_block:L1_structure_failed:"):
                l1_total += 1
                l1_rows.append(_build_sample(symbol, analysis, decision, score))
            elif reason.startswith("entry_hard_gate_block:L3_microstructure_failed:"):
                l3_total += 1
                sample = _build_sample(symbol, analysis, decision, score)
                l3_rows.append(sample)
                if "imbalance_ok=0" in reason:
                    l3_imbalance_total += 1
                    l3_imbalance_rows.append(sample)

    l1_samples = _pick_diverse_samples(l1_rows, sample_limit, per_symbol_limit)
    l3_samples = _pick_diverse_samples(l3_rows, sample_limit, per_symbol_limit)
    l3_imbalance_samples = _pick_diverse_samples(l3_imbalance_rows, sample_limit, per_symbol_limit)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": {"start": start_time, "end": end_time},
        "config_path": config_path,
        "profile_name": applied_profile,
        "min_score": min_score,
        "sample_limit": sample_limit,
        "per_symbol_limit": per_symbol_limit,
        "counts": {
            "l1_high_score_blocked_total": l1_total,
            "l3_high_score_blocked_total": l3_total,
            "l3_high_score_imbalance_blocked_total": l3_imbalance_total,
        },
        "samples": {
            "l1_high_score_blocked": l1_samples,
            "l3_high_score_blocked": l3_samples,
            "l3_high_score_imbalance_blocked": l3_imbalance_samples,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit high-score MACD V2 blocked samples")
    parser.add_argument("--config", required=True, help="runtime config path")
    parser.add_argument("--start", default=None, help="window start")
    parser.add_argument("--end", default=None, help="window end")
    parser.add_argument("--profile", default=None, help="backtest profile")
    parser.add_argument("--min-score", type=float, default=0.85, help="minimum score for sample audit")
    parser.add_argument("--sample-limit", type=int, default=20, help="number of samples per bucket")
    parser.add_argument("--per-symbol-limit", type=int, default=2, help="max samples per symbol in exported sample set")
    parser.add_argument("--output", default=None, help="optional json output path")
    args = parser.parse_args()

    result = _collect_samples(
        config_path=args.config,
        start_time=args.start,
        end_time=args.end,
        profile_name=args.profile,
        min_score=args.min_score,
        sample_limit=args.sample_limit,
        per_symbol_limit=args.per_symbol_limit,
    )

    if args.output:
        output_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path("output/backtest") / f"macd_v2_blocked_samples_{timestamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
