"""
One-click backtest comparison for baseline vs patch configs.

Example:
python scripts/run_backtest_compare_patch.py ^
  --start 2026-02-28T00:00:00Z ^
  --end 2026-03-29T00:00:00Z
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.backtest_macd_v2 import run_backtest


def _collect_metrics(label: str, config_path: Path, engine: Any) -> Dict[str, Any]:
    summary = dict(getattr(engine, "last_summary", {}) or {})
    risk_metrics = summary.get("risk_metrics", {}) if isinstance(summary.get("risk_metrics"), dict) else {}
    return {
        "label": label,
        "config_path": str(config_path),
        "return_pct": float(summary.get("return_pct", 0.0) or 0.0),
        "win_rate_pct": float(summary.get("win_rate_pct", 0.0) or 0.0),
        "profit_factor": float(summary.get("profit_factor", 0.0) or 0.0),
        "total_trades": int(summary.get("total_trades", 0) or 0),
        "signals_generated": int(summary.get("signals_generated", 0) or 0),
        "available_symbols": len(summary.get("available_symbols", []) or []),
        "max_drawdown_pct": float(risk_metrics.get("max_drawdown_pct", 0.0) or 0.0),
        "summary_file": getattr(engine, "last_summary_file", None),
        "trades_file": getattr(engine, "last_trades_file", None),
    }


def _run_one(
    label: str,
    config_path: Path,
    start_time: str,
    end_time: str,
    quiet_backtests: bool,
    initial_capital: float,
    fee_rate: float,
    profile_name: str,
) -> Dict[str, Any]:
    if quiet_backtests:
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            engine = run_backtest(
                config_path=str(config_path),
                start_time=start_time,
                end_time=end_time,
                initial_capital=initial_capital,
                fee_rate=fee_rate,
                profile_name=profile_name,
            )
    else:
        engine = run_backtest(
            config_path=str(config_path),
            start_time=start_time,
            end_time=end_time,
            initial_capital=initial_capital,
            fee_rate=fee_rate,
            profile_name=profile_name,
        )
    return _collect_metrics(label=label, config_path=config_path, engine=engine)


def _build_delta(baseline: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "label": "delta_patch_minus_baseline",
        "config_path": "-",
        "return_pct": patch["return_pct"] - baseline["return_pct"],
        "win_rate_pct": patch["win_rate_pct"] - baseline["win_rate_pct"],
        "profit_factor": patch["profit_factor"] - baseline["profit_factor"],
        "total_trades": patch["total_trades"] - baseline["total_trades"],
        "signals_generated": patch["signals_generated"] - baseline["signals_generated"],
        "available_symbols": patch["available_symbols"] - baseline["available_symbols"],
        "max_drawdown_pct": patch["max_drawdown_pct"] - baseline["max_drawdown_pct"],
        "summary_file": "-",
        "trades_file": "-",
    }


def _render_markdown(rows: List[Dict[str, Any]], start_time: str, end_time: str) -> str:
    lines = [
        "# Baseline vs Patch Backtest",
        "",
        f"- Window: `{start_time}` -> `{end_time}`",
        "",
        "| Label | Return % | Win Rate % | Profit Factor | Trades | Signals | Max DD % | Symbols |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['label']} | {row['return_pct']:.2f} | {row['win_rate_pct']:.2f} | "
            f"{row['profit_factor']:.2f} | {row['total_trades']} | {row['signals_generated']} | "
            f"{row['max_drawdown_pct']:.2f} | {row['available_symbols']} |"
        )
    lines.append("")
    lines.append("## Configs")
    for row in rows:
        if row["label"] == "delta_patch_minus_baseline":
            continue
        lines.append(f"- `{row['label']}`: `{row['config_path']}`")
    lines.append("")
    return "\n".join(lines)


def _write_outputs(rows: List[Dict[str, Any]], output_dir: Path, start_time: str, end_time: str) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"backtest_compare_patch_{stamp}.json"
    csv_path = output_dir / f"backtest_compare_patch_{stamp}.csv"
    md_path = output_dir / f"backtest_compare_patch_{stamp}.md"

    json_payload = {
        "generated_at": datetime.now().isoformat(),
        "start": start_time,
        "end": end_time,
        "rows": rows,
    }
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "label",
        "config_path",
        "return_pct",
        "win_rate_pct",
        "profit_factor",
        "total_trades",
        "signals_generated",
        "available_symbols",
        "max_drawdown_pct",
        "summary_file",
        "trades_file",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    md_path.write_text(_render_markdown(rows, start_time=start_time, end_time=end_time), encoding="utf-8")
    return json_path, csv_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run baseline vs patch one-click backtest comparison.")
    parser.add_argument(
        "--baseline-config",
        default="config/trading_config_fund_flow.json",
        help="baseline config path",
    )
    parser.add_argument(
        "--patch-config",
        default="config/trading_config_fund_flow_patch_20260329_fee_control.json",
        help="patch config path",
    )
    parser.add_argument("--start", required=True, help="window start ISO timestamp")
    parser.add_argument("--end", required=True, help="window end ISO timestamp")
    parser.add_argument(
        "--output-dir",
        default="output/backtest/patch_compare",
        help="output directory for compare reports",
    )
    parser.add_argument(
        "--quiet-backtests",
        action="store_true",
        help="suppress verbose logs from backtest engine",
    )
    parser.add_argument("--initial-capital", type=float, default=10000.0, help="initial capital")
    parser.add_argument("--fee-rate", type=float, default=0.0004, help="fee rate")
    parser.add_argument(
        "--profile",
        default="off",
        help="backtest profile name; use 'off' to disable profile overlays for apples-to-apples compare",
    )
    args = parser.parse_args()

    baseline_path = ROOT_DIR / args.baseline_config
    patch_path = ROOT_DIR / args.patch_config
    output_dir = ROOT_DIR / args.output_dir

    if not baseline_path.exists():
        raise FileNotFoundError(f"baseline config not found: {baseline_path}")
    if not patch_path.exists():
        raise FileNotFoundError(f"patch config not found: {patch_path}")

    print(f"[compare] baseline: {baseline_path}")
    baseline_row = _run_one(
        label="baseline",
        config_path=baseline_path,
        start_time=args.start,
        end_time=args.end,
        quiet_backtests=args.quiet_backtests,
        initial_capital=args.initial_capital,
        fee_rate=args.fee_rate,
        profile_name=args.profile,
    )
    print(
        f"[compare] baseline return={baseline_row['return_pct']:.2f}% "
        f"win_rate={baseline_row['win_rate_pct']:.2f}% trades={baseline_row['total_trades']} "
        f"mdd={baseline_row['max_drawdown_pct']:.2f}%"
    )

    print(f"[compare] patch: {patch_path}")
    patch_row = _run_one(
        label="patch",
        config_path=patch_path,
        start_time=args.start,
        end_time=args.end,
        quiet_backtests=args.quiet_backtests,
        initial_capital=args.initial_capital,
        fee_rate=args.fee_rate,
        profile_name=args.profile,
    )
    print(
        f"[compare] patch return={patch_row['return_pct']:.2f}% "
        f"win_rate={patch_row['win_rate_pct']:.2f}% trades={patch_row['total_trades']} "
        f"mdd={patch_row['max_drawdown_pct']:.2f}%"
    )

    delta_row = _build_delta(baseline=baseline_row, patch=patch_row)
    rows = [baseline_row, patch_row, delta_row]

    json_path, csv_path, md_path = _write_outputs(
        rows=rows,
        output_dir=output_dir,
        start_time=args.start,
        end_time=args.end,
    )
    print(f"[compare] json: {json_path}")
    print(f"[compare] csv:  {csv_path}")
    print(f"[compare] md:   {md_path}")


if __name__ == "__main__":
    main()
