import argparse
import contextlib
import csv
import io
import json
from pathlib import Path
from typing import Dict, List, Tuple
import sys

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.backtest_macd_v2 import apply_backtest_profile, build_backtest_config, load_symbol_data, run_backtest
from src.config.config_loader import ConfigLoader


def parse_profiles(raw_profiles: List[str]) -> List[str]:
    result: List[str] = []
    for item in raw_profiles:
        for part in str(item).split(","):
            profile = part.strip()
            if profile:
                result.append(profile)
    if not result:
        raise ValueError("at least one profile is required")
    return result


def resolve_profile_symbols(config_path: str, profile_name: str) -> List[str]:
    runtime_cfg = ConfigLoader.load_trading_config(config_path)
    runtime_cfg, active_profile = apply_backtest_profile(runtime_cfg, profile_name=profile_name)
    config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path=config_path,
        profile_name=active_profile,
    )
    return list(config.symbols)


def get_symbol_time_range(data_dir: str, symbol: str) -> Tuple[pd.Timestamp, pd.Timestamp]:
    data = load_symbol_data(data_dir, symbol)
    if not data or "15m" not in data or data["15m"].empty:
        raise ValueError(f"15m data unavailable for {symbol}")
    tf_15m = data["15m"]
    return pd.Timestamp(tf_15m["timestamp"].iloc[0]), pd.Timestamp(tf_15m["timestamp"].iloc[-1])


def compute_common_range(config_path: str, profiles: List[str], data_dir: str) -> Tuple[pd.Timestamp, pd.Timestamp, Dict[str, List[str]]]:
    profile_symbols: Dict[str, List[str]] = {}
    union_symbols: List[str] = []
    seen = set()
    for profile in profiles:
        symbols = resolve_profile_symbols(config_path, profile)
        profile_symbols[profile] = symbols
        for symbol in symbols:
            if symbol not in seen:
                union_symbols.append(symbol)
                seen.add(symbol)

    starts: List[pd.Timestamp] = []
    ends: List[pd.Timestamp] = []
    for symbol in union_symbols:
        start, end = get_symbol_time_range(data_dir, symbol)
        starts.append(start)
        ends.append(end)

    return max(starts), min(ends), profile_symbols


def build_windows(common_start: pd.Timestamp, common_end: pd.Timestamp, window_days: int, step_days: int) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    windows: List[Tuple[pd.Timestamp, pd.Timestamp]] = []
    current_start = common_start
    window_delta = pd.Timedelta(days=window_days)
    step_delta = pd.Timedelta(days=step_days)

    while current_start + window_delta <= common_end:
        current_end = current_start + window_delta
        windows.append((current_start, current_end))
        current_start = current_start + step_delta

    return windows


def render_table(rows: List[dict]) -> str:
    headers = ["profile", "window_start", "window_end", "symbols", "return_pct", "trades", "win_rate_pct", "profit_factor"]
    values = []
    for row in rows:
        values.append(
            [
                row["profile"],
                row["window_start"],
                row["window_end"],
                str(row["available_symbols"]),
                f"{row['return_pct']:.2f}",
                str(row["total_trades"]),
                f"{row['win_rate_pct']:.1f}",
                f"{row['profit_factor']:.2f}",
            ]
        )
    widths = [len(h) for h in headers]
    for row in values:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    out = []
    out.append(" | ".join(h.ljust(widths[idx]) for idx, h in enumerate(headers)))
    out.append("-+-".join("-" * w for w in widths))
    for row in values:
        out.append(" | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row)))
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run rolling-window out-of-sample backtests for multiple profiles.")
    parser.add_argument("--config", required=True, help="backtest config path")
    parser.add_argument("--profiles", nargs="+", required=True, help="one or more profile names, supports comma-separated values")
    parser.add_argument("--window-days", type=int, default=7, help="rolling window length in days")
    parser.add_argument("--step-days", type=int, default=7, help="step between windows in days")
    parser.add_argument("--data-dir", default="data/backtest_cache", help="parquet cache directory")
    parser.add_argument("--output-prefix", default="output/backtest/rolling_validation", help="output file prefix")
    parser.add_argument("--quiet-backtests", action="store_true", help="suppress verbose logs from individual backtests")
    args = parser.parse_args()

    profiles = parse_profiles(args.profiles)
    common_start, common_end, profile_symbols = compute_common_range(args.config, profiles, args.data_dir)
    windows = build_windows(common_start, common_end, args.window_days, args.step_days)
    if not windows:
        raise ValueError("no rolling windows available for the requested range")

    print(f"common_range: {common_start} -> {common_end}")
    print(f"windows: {len(windows)} (window_days={args.window_days}, step_days={args.step_days})")
    for profile, symbols in profile_symbols.items():
        print(f"profile {profile}: {len(symbols)} symbols")

    rows: List[dict] = []
    output_prefix = Path(args.output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)

    for window_start, window_end in windows:
        for profile in profiles:
            if args.quiet_backtests:
                sink = io.StringIO()
                with contextlib.redirect_stdout(sink):
                    engine = run_backtest(
                        config_path=args.config,
                        profile_name=profile,
                        start_time=window_start.isoformat(),
                        end_time=window_end.isoformat(),
                    )
            else:
                engine = run_backtest(
                    config_path=args.config,
                    profile_name=profile,
                    start_time=window_start.isoformat(),
                    end_time=window_end.isoformat(),
                )

            summary = dict(engine.last_summary or {})
            rows.append(
                {
                    "profile": profile,
                    "window_start": window_start.strftime("%Y-%m-%d %H:%M"),
                    "window_end": window_end.strftime("%Y-%m-%d %H:%M"),
                    "available_symbols": len(summary.get("available_symbols", [])),
                    "return_pct": float(summary.get("return_pct", 0.0)),
                    "total_trades": int(summary.get("total_trades", 0)),
                    "win_rate_pct": float(summary.get("win_rate_pct", 0.0)),
                    "profit_factor": float(summary.get("profit_factor", 0.0)),
                    "signals_generated": int(summary.get("signals_generated", 0)),
                    "summary_file": engine.last_summary_file,
                    "trades_file": engine.last_trades_file,
                }
            )

    timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_prefix.parent / f"{output_prefix.name}_{timestamp}.json"
    csv_path = output_prefix.parent / f"{output_prefix.name}_{timestamp}.csv"

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print()
    print(render_table(rows))
    print()
    print(f"json: {json_path}")
    print(f"csv: {csv_path}")


if __name__ == "__main__":
    main()

