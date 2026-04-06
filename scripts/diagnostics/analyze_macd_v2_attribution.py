from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from scripts.backtest_fund_flow_bot_like import BotLikeReplayEngine  # type: ignore
    from scripts.backtest_macd_v2 import (  # type: ignore
        apply_backtest_profile,
        build_backtest_config,
        build_strategy_config,
        load_symbol_data,
    )
except ModuleNotFoundError:
    from scripts.backtest_fund_flow_bot_like import BotLikeReplayEngine  # type: ignore
    from scripts.backtest_macd_v2 import (  # type: ignore
        apply_backtest_profile,
        build_backtest_config,
        build_strategy_config,
        load_symbol_data,
    )
from src.fund_flow.models import Operation
from src.fund_flow.replay_window import apply_market_data_window, timestamp_in_trade_window


def _score_bucket(score: Optional[float]) -> str:
    if score is None:
        return "missing"
    if score < 0.70:
        return "<0.70"
    if score < 0.75:
        return "0.70-0.75"
    if score < 0.80:
        return "0.75-0.80"
    if score < 0.825:
        return "0.80-0.825"
    if score < 0.83:
        return "0.825-0.83"
    if score < 0.845:
        return "0.83-0.845"
    if score < 0.85:
        return "0.845-0.85"
    return ">=0.85"


def _extract_signal_score(metadata: Dict[str, Any]) -> Optional[float]:
    candidates = [
        metadata.get("signal_score"),
        metadata.get("final_long_score"),
        metadata.get("final_short_score"),
        metadata.get("long_score"),
        metadata.get("short_score"),
    ]
    numeric = []
    for value in candidates:
        try:
            if value is None:
                continue
            numeric.append(float(value))
        except (TypeError, ValueError):
            continue
    if not numeric:
        return None
    return max(numeric)


def _categorize_hold(reason: str, metadata: Dict[str, Any]) -> str:
    if reason == "no_signal":
        return "no_signal"
    if reason.startswith("threshold_check"):
        return "threshold_check"
    if reason.startswith("vwap_score_filter"):
        return "vwap_score_filter"
    if reason.startswith("vwap_hard_block"):
        return "vwap_hard_block"
    if "entry_hard_gate" in reason:
        parts = reason.split(":", 2)
        if len(parts) >= 2:
            stage = parts[1].strip()
            if stage == "L3_microstructure_failed":
                return "L3_microstructure_failed"
            if stage == "L2_flow_failed":
                return "L2_flow_failed"
            if stage == "L1_structure_failed":
                return "L1_structure_failed"
        hard_reason = str(metadata.get("entry_hard_gate_reason", "") or "")
        parts = {part.strip() for part in hard_reason.split(",") if part.strip()}
        micro = {"depth_ok", "imbalance_ok", "cvd_momentum_ok"}
        flow = {"vwap_ok", "cvd_ok", "oi_ok"}
        if parts & micro:
            return "L3_microstructure_failed"
        if parts & flow:
            return "L2_flow_failed"
        return "L1_structure_failed"
    return "other_hold"


def _update_subreason_counts(target: Counter, raw_reason: str) -> None:
    for part in [item.strip() for item in raw_reason.split(",") if item.strip()]:
        target[part] += 1


def _extract_hard_gate_subreason(reason: str, metadata: Dict[str, Any]) -> str:
    parts = reason.split(":", 2)
    if len(parts) >= 3 and parts[2].strip():
        return parts[2].strip()
    return str(metadata.get("entry_hard_gate_reason", "") or "")


def build_attribution(
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
    data_window_start = config.data_window_start_iso or start_time
    data_window_end = config.data_window_end_iso or end_time
    if market_data_map and (data_window_start or data_window_end):
        market_data_map, _ = apply_market_data_window(
            market_data_map,
            start_time=data_window_start,
            end_time=data_window_end,
        )

    decision_counts = Counter()
    hold_category_counts = Counter()
    hold_reason_counts = Counter()
    l1_subreasons = Counter()
    l2_subreasons = Counter()
    l3_subreasons = Counter()
    accepted_score_buckets = Counter()
    hold_score_buckets = Counter()
    category_score_buckets: Dict[str, Counter] = defaultdict(Counter)
    analysis_attempts = 0
    analysis_ready = 0
    analysis_skipped = Counter()

    for symbol, data in market_data_map.items():
        tf_15m = data["15m"]
        for idx in range(len(tf_15m)):
            current_time = pd.Timestamp(tf_15m.iloc[idx]["timestamp"])
            if not timestamp_in_trade_window(
                current_time,
                trade_window_start_iso=config.window_start_iso,
                trade_window_end_iso=config.window_end_iso,
            ):
                continue
            analysis_attempts += 1
            analysis, analysis_status = engine._build_analysis_with_status(symbol, data, idx)
            if analysis is None:
                analysis_skipped[analysis_status] += 1
                continue
            analysis_ready += 1
            decision = engine._decide(symbol, analysis)
            metadata = decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}
            reason = str(decision.reason or "")
            score = _extract_signal_score(metadata)
            bucket = _score_bucket(score)

            decision_counts[decision.operation.value] += 1
            if decision.operation in (Operation.BUY, Operation.SELL):
                accepted_score_buckets[bucket] += 1
                continue

            hold_reason_counts[reason] += 1
            category = _categorize_hold(reason, metadata)
            hold_category_counts[category] += 1
            hold_score_buckets[bucket] += 1
            category_score_buckets[category][bucket] += 1

            hard_reason = _extract_hard_gate_subreason(reason, metadata)
            if category == "L1_structure_failed":
                _update_subreason_counts(l1_subreasons, hard_reason)
            elif category == "L2_flow_failed":
                _update_subreason_counts(l2_subreasons, hard_reason)
            elif category == "L3_microstructure_failed":
                _update_subreason_counts(l3_subreasons, hard_reason)

    total_holds = sum(hold_category_counts.values())
    total_entries = decision_counts["buy"] + decision_counts["sell"]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": {
            "start": start_time,
            "end": end_time,
        },
        "config_path": config_path,
        "profile_name": applied_profile,
        "symbols_loaded": len(market_data_map),
        "replay_input_counters": {
            "analysis_attempts": analysis_attempts,
            "analysis_ready": analysis_ready,
            "analysis_skipped": dict(analysis_skipped),
        },
        "decision_counts": dict(decision_counts),
        "entry_count": total_entries,
        "hold_count": total_holds,
        "hold_category_counts": dict(hold_category_counts),
        "hold_category_ratios_pct": {
            key: round(value / total_holds * 100.0, 4) for key, value in hold_category_counts.items() if total_holds > 0
        },
        "top_hold_reasons": hold_reason_counts.most_common(20),
        "l1_subreasons": dict(l1_subreasons),
        "l2_subreasons": dict(l2_subreasons),
        "l3_subreasons": dict(l3_subreasons),
        "accepted_score_buckets": dict(accepted_score_buckets),
        "hold_score_buckets": dict(hold_score_buckets),
        "category_score_buckets": {
            key: dict(counter) for key, counter in category_score_buckets.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze MACD V2 hold / L3 / score-bucket attribution")
    parser.add_argument("--config", required=True, help="runtime config path")
    parser.add_argument("--start", default=None, help="window start")
    parser.add_argument("--end", default=None, help="window end")
    parser.add_argument("--profile", default=None, help="backtest profile")
    parser.add_argument("--output", default=None, help="optional json output path")
    args = parser.parse_args()

    result = build_attribution(
        config_path=args.config,
        start_time=args.start,
        end_time=args.end,
        profile_name=args.profile,
    )

    if args.output:
        output_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path("output/backtest") / f"macd_v2_attribution_{timestamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()

