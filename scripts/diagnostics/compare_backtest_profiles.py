"""
Batch-run MACD V2 backtests across multiple profiles and write a comparison summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.config_loader import ConfigLoader
from scripts.backtest_macd_v2 import run_backtest


def resolve_profiles(config_path: str, requested_profiles: List[str] | None) -> List[str]:
    runtime_cfg = ConfigLoader.load_trading_config(config_path)
    fund_flow_cfg = runtime_cfg.get("fund_flow", {}) if isinstance(runtime_cfg.get("fund_flow"), dict) else {}
    backtest_cfg = fund_flow_cfg.get("backtest", {}) if isinstance(fund_flow_cfg.get("backtest"), dict) else {}
    profiles_cfg = backtest_cfg.get("profiles", {}) if isinstance(backtest_cfg.get("profiles"), dict) else {}
    default_profile = str(backtest_cfg.get("default_profile", "") or "").strip()

    if requested_profiles:
        profiles = [str(name).strip() for name in requested_profiles if str(name).strip()]
    else:
        profiles = []
        if default_profile:
            profiles.append(default_profile)
        if "macd_v2_full_filters" in profiles_cfg:
            profiles.append("macd_v2_full_filters")
        elif "macd_v2_disable_short_filter" in profiles_cfg:
            profiles.append("macd_v2_disable_short_filter")

    deduped: List[str] = []
    for profile in profiles:
        if profile not in deduped:
            deduped.append(profile)

    missing = [profile for profile in deduped if profile not in profiles_cfg]
    if missing:
        raise ValueError(f"unknown backtest profiles: {', '.join(missing)}")
    if not deduped:
        raise ValueError("no backtest profiles selected")
    return deduped


def safe_number(value) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isinf(numeric) or math.isnan(numeric):
        return numeric
    return numeric


def compare_profiles(
    config_path: str,
    profiles: List[str],
    initial_capital: float,
    fee_rate: float,
    max_positions: int | None,
    fixed_leverage: int | None,
) -> list[dict]:
    rows: list[dict] = []
    baseline_return: float | None = None

    for profile in profiles:
        engine = run_backtest(
            config_path=config_path,
            initial_capital=initial_capital,
            fee_rate=fee_rate,
            max_positions_override=max_positions,
            fixed_leverage=fixed_leverage,
            profile_name=profile,
        )
        summary = dict(getattr(engine, "last_summary", {}) or {})
        if not summary:
            raise RuntimeError(f"missing summary after backtest profile: {profile}")

        signal_breakdown = summary.get("signal_type_breakdown", {}) if isinstance(summary.get("signal_type_breakdown"), dict) else {}
        flip_bearish = signal_breakdown.get("flip_bearish", {}) if isinstance(signal_breakdown.get("flip_bearish"), dict) else {}
        red_bar = signal_breakdown.get("red_bar_growing", {}) if isinstance(signal_breakdown.get("red_bar_growing"), dict) else {}
        risk_metrics = summary.get("risk_metrics", {}) if isinstance(summary.get("risk_metrics"), dict) else {}

        row = {
            "profile": summary.get("backtest_profile") or profile,
            "return_pct": safe_number(summary.get("return_pct")),
            "final_capital": safe_number(summary.get("final_capital")),
            "total_trades": int(summary.get("total_trades", 0) or 0),
            "win_rate_pct": safe_number(summary.get("win_rate_pct")),
            "profit_factor": safe_number(summary.get("profit_factor")),
            "signals_generated": int(summary.get("signals_generated", 0) or 0),
            "timeline_points": int(summary.get("timeline_points", 0) or 0),
            "max_drawdown_value": safe_number(risk_metrics.get("max_drawdown_value")),
            "max_drawdown_pct": safe_number(risk_metrics.get("max_drawdown_pct")),
            "flip_bearish_trades": int(flip_bearish.get("count", 0) or 0),
            "flip_bearish_win_rate_pct": safe_number(flip_bearish.get("win_rate_pct")),
            "red_bar_growing_trades": int(red_bar.get("count", 0) or 0),
            "summary_file": getattr(engine, "last_summary_file", None),
            "trades_file": getattr(engine, "last_trades_file", None),
        }

        if baseline_return is None:
            baseline_return = row["return_pct"]
        row["return_delta_vs_first_pct"] = (
            row["return_pct"] - baseline_return
            if row["return_pct"] is not None and baseline_return is not None
            else None
        )
        rows.append(row)

    return rows


def render_console_table(rows: list[dict]) -> str:
    headers = [
        "profile",
        "return_pct",
        "delta_vs_first",
        "trades",
        "win_rate_pct",
        "profit_factor",
        "max_dd_pct",
        "flip_bearish_trades",
    ]
    table_rows = []
    for row in rows:
        profit_factor = row["profit_factor"]
        if isinstance(profit_factor, float) and math.isinf(profit_factor):
            profit_factor_text = "inf"
        elif profit_factor is None:
            profit_factor_text = ""
        else:
            profit_factor_text = f"{profit_factor:.2f}"

        table_rows.append([
            str(row["profile"]),
            f"{row['return_pct']:.2f}" if row["return_pct"] is not None else "",
            f"{row['return_delta_vs_first_pct']:+.2f}" if row["return_delta_vs_first_pct"] is not None else "",
            str(row["total_trades"]),
            f"{row['win_rate_pct']:.1f}" if row["win_rate_pct"] is not None else "",
            profit_factor_text,
            f"{row['max_drawdown_pct']:.2f}" if row["max_drawdown_pct"] is not None else "",
            str(row["flip_bearish_trades"]),
        ])

    widths = [len(header) for header in headers]
    for values in table_rows:
        for idx, value in enumerate(values):
            widths[idx] = max(widths[idx], len(value))

    def format_row(values: list[str]) -> str:
        return " | ".join(value.ljust(widths[idx]) for idx, value in enumerate(values))

    lines = [
        format_row(headers),
        "-+-".join("-" * width for width in widths),
    ]
    lines.extend(format_row(values) for values in table_rows)
    return "\n".join(lines)


def write_outputs(rows: list[dict], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"profile_compare_{timestamp}.json"
    csv_path = output_dir / f"profile_compare_{timestamp}.csv"

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "profile",
        "return_pct",
        "return_delta_vs_first_pct",
        "final_capital",
        "total_trades",
        "win_rate_pct",
        "profit_factor",
        "signals_generated",
        "timeline_points",
        "max_drawdown_value",
        "max_drawdown_pct",
        "flip_bearish_trades",
        "flip_bearish_win_rate_pct",
        "red_bar_growing_trades",
        "summary_file",
        "trades_file",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})

    return json_path, csv_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-run MACD V2 backtest profiles and write a comparison summary.")
    parser.add_argument("--config", default="config/trading_config_fund_flow.json", help="runtime config path")
    parser.add_argument("--profiles", nargs="*", default=None, help="profile names to compare; defaults to default_profile + macd_v2_full_filters")
    parser.add_argument("--initial-capital", type=float, default=10000.0, help="initial capital in USDT")
    parser.add_argument("--fee-rate", type=float, default=0.0004, help="fee rate per side")
    parser.add_argument("--max-positions", type=int, default=None, help="override max concurrent positions")
    parser.add_argument("--fixed-leverage", type=int, default=None, help="force a fixed leverage for all entries")
    parser.add_argument("--output-dir", default="output/backtest", help="directory for comparison outputs")
    args = parser.parse_args()

    profiles = resolve_profiles(args.config, args.profiles)
    print(f"Comparing profiles: {', '.join(profiles)}")
    rows = compare_profiles(
        config_path=args.config,
        profiles=profiles,
        initial_capital=args.initial_capital,
        fee_rate=args.fee_rate,
        max_positions=args.max_positions,
        fixed_leverage=args.fixed_leverage,
    )
    print("\nProfile Comparison")
    print(render_console_table(rows))

    json_path, csv_path = write_outputs(rows, Path(args.output_dir))
    print(f"\nComparison JSON: {json_path}")
    print(f"Comparison CSV: {csv_path}")


if __name__ == "__main__":
    main()

