#!/usr/bin/env python
"""Diagnose MACD V2 Q4 preflip long decisions for a single symbol and time window."""

from __future__ import annotations

import argparse
import json
from glob import glob
from pathlib import Path
import sys
from typing import Any, Dict

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config.config_loader import ConfigLoader
from scripts.backtest_macd_v2 import (
    BacktestEngine,
    build_backtest_config,
    build_strategy_config,
    load_symbol_data,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose Q4 preflip long decisions over a single window.")
    parser.add_argument("--symbol", required=True, help="Trading symbol, for example ETHUSDT")
    parser.add_argument("--start", required=True, help="Window start timestamp, for example 2026-03-08T20:00")
    parser.add_argument("--end", required=True, help="Window end timestamp, for example 2026-03-17T08:00")
    parser.add_argument("--config", default="config/trading_config_fund_flow.json", help="Runtime config path")
    parser.add_argument("--data-dir", default="data/backtest_cache", help="Backtest cache directory")
    parser.add_argument("--trades-csv", default="", help="Optional backtest trades CSV to match fills")
    return parser.parse_args()


def _flatten_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(detail, dict):
        return {}
    return {f"q4_detail_{key}": value for key, value in detail.items()}


def _latest_csv(pattern: str) -> Path | None:
    candidates = [Path(p) for p in glob(pattern)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _build_trade_entry_map(trades_path: Path | None) -> Dict[tuple[str, pd.Timestamp], Dict[str, Any]]:
    if trades_path is None or not trades_path.exists():
        return {}
    trades_df = pd.read_csv(trades_path)
    if trades_df.empty or "entry_time" not in trades_df.columns:
        return {}

    entry_map: Dict[tuple[str, pd.Timestamp], Dict[str, Any]] = {}
    for _, row in trades_df.iterrows():
        symbol = str(row.get("symbol", "") or "").strip().upper()
        entry_time_raw = row.get("entry_time")
        if not symbol or entry_time_raw is None:
            continue
        entry_time = pd.Timestamp(entry_time_raw)
        if pd.isna(entry_time):
            continue
        entry_map[(symbol, entry_time)] = row.to_dict()
    return entry_map


def _classify_downstream_bucket(row: Dict[str, Any]) -> str:
    if bool(row.get("order_filled", False)):
        return "order_fill"
    if bool(row.get("execution_order_filled", False)):
        return "order_fill"

    execution_layer_bucket = str(row.get("execution_layer_bucket", "") or "")
    signal_direction = str(row.get("signal_direction", "") or "").lower()
    reject_stage = str(row.get("reject_stage", "") or "").lower()
    reject_reason = str(row.get("reject_reason_code", "") or "").lower()
    stage = str(row.get("stage", "") or "").lower()
    stage_path = str(row.get("stage_path_text", "") or "").lower()

    if execution_layer_bucket in {"execution_not_submitted", "execution_submitted_but_not_filled", "execution_submitted_pending"}:
        return execution_layer_bucket
    if signal_direction == "long" and not reject_stage and not reject_reason and stage == "final":
        return "long_signal_not_executed"
    if "whitelist" in reject_stage or "whitelist" in reject_reason or "whitelist" in stage_path:
        return "whitelist_fail"
    if "threshold" in reject_stage or "threshold" in reject_reason or "threshold" in stage_path:
        return "threshold_check"
    if "pocket_entry_requirements" in reject_stage or "pocket_entry_requirements" in stage_path:
        return "threshold_check"
    return "other_block"


def _summarize_execution_events(events: list[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {
        "execution_layer_bucket": "",
        "execution_submit_status": "not_evaluated",
        "execution_submit_reason": "",
        "execution_followup_status": "not_applicable",
        "execution_followup_reason": "",
        "execution_event_types": "",
        "execution_last_event_time": "",
        "execution_order_filled": False,
        "execution_position_blocked_reason": "",
        "execution_position_final_portion": 0.0,
        "execution_position_pre_scale_portion": 0.0,
        "execution_position_target_portion": 0.0,
        "execution_position_max_symbol_portion": 0.0,
        "execution_position_portion_multiplier": 0.0,
        "execution_position_vwap_multiplier": 1.0,
        "execution_position_entry_scale_applied": 1.0,
        "execution_position_session_scale_input": 1.0,
        "execution_position_effective_session_scale": 1.0,
        "execution_position_lowest_score_tier_min": 0.0,
        "execution_position_matched_score_tier_min": 0.0,
        "execution_position_matched_score_tier_target": 0.0,
        "execution_position_min_open_portion": 0.0,
    }
    if not events:
        return summary

    ordered = sorted(
        events,
        key=lambda item: (pd.Timestamp(item.get("event_time")), str(item.get("event_type", ""))),
    )
    summary["execution_event_types"] = ">".join(str(item.get("event_type", "") or "") for item in ordered)
    summary["execution_last_event_time"] = str(pd.Timestamp(ordered[-1].get("event_time")))

    first = ordered[0]
    first_type = str(first.get("event_type", "") or "")
    first_reason = str(first.get("reason", "") or "")
    summary["execution_submit_reason"] = first_reason
    summary["execution_position_blocked_reason"] = str(first.get("position_blocked_reason", "") or "")
    summary["execution_position_final_portion"] = float(first.get("position_final_portion", 0.0) or 0.0)
    summary["execution_position_pre_scale_portion"] = float(first.get("position_pre_scale_portion", 0.0) or 0.0)
    summary["execution_position_target_portion"] = float(first.get("position_target_portion", 0.0) or 0.0)
    summary["execution_position_max_symbol_portion"] = float(first.get("position_max_symbol_portion", 0.0) or 0.0)
    summary["execution_position_portion_multiplier"] = float(first.get("position_portion_multiplier", 0.0) or 0.0)
    summary["execution_position_vwap_multiplier"] = float(first.get("position_vwap_multiplier", 1.0) or 1.0)
    summary["execution_position_entry_scale_applied"] = float(first.get("position_entry_scale_applied", 1.0) or 1.0)
    summary["execution_position_session_scale_input"] = float(first.get("position_session_scale_input", 1.0) or 1.0)
    summary["execution_position_effective_session_scale"] = float(first.get("position_effective_session_scale", 1.0) or 1.0)
    summary["execution_position_lowest_score_tier_min"] = float(first.get("position_lowest_score_tier_min", 0.0) or 0.0)
    summary["execution_position_matched_score_tier_min"] = float(first.get("position_matched_score_tier_min", 0.0) or 0.0)
    summary["execution_position_matched_score_tier_target"] = float(first.get("position_matched_score_tier_target", 0.0) or 0.0)
    summary["execution_position_min_open_portion"] = float(first.get("position_min_open_portion", 0.0) or 0.0)

    if first_type == "skipped":
        summary["execution_layer_bucket"] = "execution_not_submitted"
        summary["execution_submit_status"] = "skipped"
        return summary

    if first_type == "submitted":
        summary["execution_submit_status"] = "submitted"
        followups = ordered[1:]
        if not followups:
            summary["execution_layer_bucket"] = "execution_submitted_pending"
            summary["execution_followup_status"] = "pending"
            return summary
        last = followups[-1]
        last_type = str(last.get("event_type", "") or "")
        last_reason = str(last.get("reason", "") or "")
        summary["execution_followup_reason"] = last_reason
        if last_type == "filled":
            summary["execution_layer_bucket"] = "execution_filled"
            summary["execution_followup_status"] = "filled"
            summary["execution_order_filled"] = True
            return summary
        if last_type == "canceled":
            summary["execution_layer_bucket"] = "execution_submitted_but_not_filled"
            summary["execution_followup_status"] = "canceled"
            return summary

    summary["execution_layer_bucket"] = "execution_unknown"
    return summary


def main() -> int:
    args = parse_args()
    symbol = str(args.symbol or "").strip().upper()
    start_ts = pd.Timestamp(args.start)
    end_ts = pd.Timestamp(args.end)
    if end_ts < start_ts:
        raise SystemExit("--end must be later than or equal to --start")

    runtime_cfg = ConfigLoader.load_trading_config(args.config)
    strategy_config = build_strategy_config(runtime_cfg)
    backtest_config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path=args.config,
        data_dir=args.data_dir,
        window_start_iso=str(start_ts),
        window_end_iso=str(end_ts),
    )
    engine = BacktestEngine(backtest_config, strategy_config, runtime_cfg)
    trades_path = (
        Path(args.trades_csv)
        if str(args.trades_csv or "").strip()
        else _latest_csv("output/backtest/v2_trades_*.csv")
    )
    trade_entry_map = _build_trade_entry_map(trades_path)

    data = load_symbol_data(
        args.data_dir,
        symbol,
        strategy_config,
        decision_timeframe=backtest_config.decision_timeframe,
    )
    if not data:
        raise SystemExit(f"No cached data found for {symbol}")

    tf_15m = data["15m"]
    rows = []
    for idx in range(len(tf_15m)):
        if idx < 50:
            continue
        current_time = pd.Timestamp(tf_15m.iloc[idx]["timestamp"])
        if current_time < start_ts or current_time > end_ts:
            continue

        analysis = engine.analyze_bar(symbol, data, idx)
        if analysis is None:
            continue

        signal = analysis["signal"]
        signal_details = signal.details if isinstance(signal.details, dict) else {}
        q4_detail = analysis.get("q4_rejection_detail") or {}
        q4_reason = analysis.get("q4_rsi_lead_preflip_reason")
        if q4_reason is None:
            q4_reason = "not_evaluated"
        entry_match = trade_entry_map.get((symbol, current_time))
        row = {
            "timestamp": current_time,
            "symbol": symbol,
            "price": float(analysis.get("price", 0.0) or 0.0),
            "signal_direction": str(signal.direction),
            "signal_type_1h": str(signal.signal_type_1h or ""),
            "signal_score": float(signal.signal_score),
            "q4_rsi_lead_preflip_active": bool(analysis.get("q4_rsi_lead_preflip_active", False)),
            "q4_rsi_lead_preflip_passed": bool(analysis.get("q4_rsi_lead_preflip_passed", False)),
            "q4_rsi_lead_preflip_reason": str(q4_reason),
            "q4_rsi_lead_preflip_bonus_score": float(analysis.get("q4_rsi_lead_preflip_bonus_score", 0.0) or 0.0),
            "q4_rsi_lead_preflip_dynamic_multiplier": float(
                analysis.get("q4_rsi_lead_preflip_dynamic_multiplier", 0.0) or 0.0
            ),
            "q4_rsi_lead_preflip_rsi_gate_exempted": bool(
                analysis.get("q4_rsi_lead_preflip_rsi_gate_exempted", False)
            ),
            "reject_reason_code": str(signal_details.get("reject_reason_code", "") or ""),
            "reject_stage": str(signal_details.get("reject_stage", "") or ""),
            "reject_reason_detail": str(signal_details.get("reject_reason_detail", "") or ""),
            "stage": str(signal_details.get("stage", "") or ""),
            "stage_path_text": str(signal_details.get("stage_path_text", "") or ""),
            "signal_score_threshold_used": float(signal_details.get("signal_score_threshold_used", 0.0) or 0.0),
            "min_entry_score_used": float(signal_details.get("min_entry_score_used", 0.0) or 0.0),
            "entry_tier": str(signal_details.get("entry_tier", "") or ""),
            "entry_confirmation_passed": bool(signal_details.get("entry_confirmation_passed", False)),
            "entry_confirmation_gate_enabled": bool(signal_details.get("entry_confirmation_gate_enabled", False)),
            "entry_confirmation_gate_bypassed": bool(signal_details.get("entry_confirmation_gate_bypassed", False)),
            "entry_score_15m": float(signal_details.get("entry_score_15m", 0.0) or 0.0),
            "pocket_entry_override_label": str(signal_details.get("pocket_entry_override_label", "") or ""),
            "pocket_scoring_override_label": str(signal_details.get("pocket_scoring_override_label", "") or ""),
            "order_filled": bool(entry_match),
            "order_entry_time": str(entry_match.get("entry_time", "")) if entry_match else "",
            "order_side": str(entry_match.get("side", "")) if entry_match else "",
            "order_signal_score": float(entry_match.get("signal_score", 0.0) or 0.0) if entry_match else 0.0,
            "order_entry_tier": str(entry_match.get("entry_tier", "") or "") if entry_match else "",
            "order_fill_reason": str(entry_match.get("entry_fill_direct_ioc_reason", "") or "") if entry_match else "",
            "order_entry_degradation_path": (
                str(entry_match.get("entry_degradation_path", "") or "") if entry_match else ""
            ),
            "downstream_bucket": "",
            "q4_rejection_detail_json": json.dumps(q4_detail, ensure_ascii=False, sort_keys=True)
            if q4_detail
            else "",
        }
        row.update(_flatten_detail(q4_detail))
        rows.append(row)

        analyses = {symbol: analysis}
        newly_filled_symbols = engine.process_pending_orders(analyses)
        closed_symbols_this_bar: set[str] = set()
        if symbol in engine.positions and symbol not in newly_filled_symbols:
            if engine.check_stops(symbol, analysis):
                closed_symbols_this_bar.add(symbol)
        signal_direction = str(signal.direction or "")
        if signal_direction:
            if symbol in engine.positions:
                engine._record_entry_attempt_event(
                    symbol=symbol,
                    event_type="skipped",
                    event_time=current_time,
                    reason="already_in_position",
                    signal_direction=signal_direction,
                    signal_type_1h=str(signal.signal_type_1h or ""),
                    signal_score=float(signal.signal_score or 0.0),
                )
            elif symbol in closed_symbols_this_bar:
                engine._record_entry_attempt_event(
                    symbol=symbol,
                    event_type="skipped",
                    event_time=current_time,
                    reason="closed_this_bar",
                    signal_direction=signal_direction,
                    signal_type_1h=str(signal.signal_type_1h or ""),
                    signal_score=float(signal.signal_score or 0.0),
                )
            elif symbol in engine.pending_orders:
                engine._record_entry_attempt_event(
                    symbol=symbol,
                    event_type="skipped",
                    event_time=current_time,
                    reason="pending_order_exists",
                    signal_direction=signal_direction,
                    signal_type_1h=str(signal.signal_type_1h or ""),
                    signal_score=float(signal.signal_score or 0.0),
                )
            else:
                engine.execute_trade(symbol, analysis, data)

    if rows:
        final_time = pd.Timestamp(rows[-1]["timestamp"])
        for pending_symbol in list(engine.pending_orders.keys()):
            engine._cancel_pending_order(pending_symbol, reason="diagnostic_window_end", event_time=final_time)

    events_by_submit: Dict[tuple[str, pd.Timestamp], list[Dict[str, Any]]] = {}
    for event in engine.entry_attempt_events:
        event_symbol = str(event.get("symbol", "") or "").strip().upper()
        submit_time = pd.Timestamp(event.get("submit_time"))
        events_by_submit.setdefault((event_symbol, submit_time), []).append(event)

    for row in rows:
        event_summary = _summarize_execution_events(
            events_by_submit.get((str(row["symbol"]).upper(), pd.Timestamp(row["timestamp"])), [])
        )
        row.update(event_summary)
        row["downstream_bucket"] = _classify_downstream_bucket(row)

    df = pd.DataFrame(rows)
    output_dir = Path("output/analysis")
    output_dir.mkdir(parents=True, exist_ok=True)
    window_label = f"{start_ts.strftime('%Y%m%dT%H%M')}_{end_ts.strftime('%Y%m%dT%H%M')}"
    output_path = output_dir / f"q4_diag_{symbol}_{window_label}.csv"
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    attribution_path = output_dir / f"q4_passed_not_opened_{symbol}_{window_label}.csv"
    passed_not_opened = df[
        (df["q4_rsi_lead_preflip_reason"] == "passed")
        & (~df["order_filled"])
        & (~df["execution_order_filled"])
    ]
    passed_not_opened.to_csv(attribution_path, index=False, encoding="utf-8-sig")

    print(f"diagnostic_rows: {len(df)}")
    print(f"output_csv: {output_path}")
    print(f"attribution_csv: {attribution_path}")
    if df.empty:
        print("reason_counts: {}")
        return 0

    print("reason_counts:")
    reason_counts = df["q4_rsi_lead_preflip_reason"].value_counts(dropna=False)
    for reason, count in reason_counts.items():
        print(f"  {reason}: {count}")

    rejected = df[df["q4_rsi_lead_preflip_passed"] == False]
    if not rejected.empty:
        print("rejection_reason_counts:")
        reject_counts = rejected["q4_rsi_lead_preflip_reason"].value_counts(dropna=False)
        for reason, count in reject_counts.items():
            print(f"  {reason}: {count}")

    if not passed_not_opened.empty:
        print("passed_not_opened_bucket_counts:")
        bucket_counts = passed_not_opened["downstream_bucket"].value_counts(dropna=False)
        for bucket, count in bucket_counts.items():
            print(f"  {bucket}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
