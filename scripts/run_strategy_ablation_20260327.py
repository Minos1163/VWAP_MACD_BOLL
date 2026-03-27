"""
Run targeted 30-day strategy ablations between the 132% baseline config and the
current live-converged backtest config.

This script focuses on the differences that are actually present in config files,
instead of assuming the drop came from TP/SL or leverage changes.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.backtest_macd_v2 import run_backtest


VariantMutator = Callable[[dict], None]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def mutate_noop(_: dict) -> None:
    return


def mutate_remove_blacklist(cfg: dict) -> None:
    trading_cfg = cfg.setdefault("trading", {})
    if isinstance(trading_cfg, dict):
        trading_cfg.pop("symbol_blacklist", None)


def mutate_restore_entry_blocks(cfg: dict) -> None:
    entry_filters = (
        cfg.setdefault("fund_flow", {})
        .setdefault("macd_mtf_strategy_v2", {})
        .setdefault("entry_filters", {})
    )
    entry_filters["disable_flip_bullish_entries"] = False
    entry_filters["disable_green_bar_growing_entries"] = False
    entry_filters["disable_red_bar_growing_long_entries"] = False


def mutate_restore_secondary_signal_path(cfg: dict) -> None:
    entry_filters = (
        cfg.setdefault("fund_flow", {})
        .setdefault("macd_mtf_strategy_v2", {})
        .setdefault("entry_filters", {})
    )
    entry_thresholds = (
        cfg.setdefault("fund_flow", {})
        .setdefault("macd_mtf_strategy_v2", {})
        .setdefault("entry_thresholds", {})
    )
    scoring_weights = (
        cfg.setdefault("fund_flow", {})
        .setdefault("macd_mtf_strategy_v2", {})
        .setdefault("scoring_weights", {})
    )
    stop_loss_cfg = (
        cfg.setdefault("fund_flow", {})
        .setdefault("macd_mtf_strategy_v2", {})
        .setdefault("stop_loss_config", {})
    )
    session_risk = (
        cfg.setdefault("fund_flow", {})
        .setdefault("macd_mtf_strategy_v2", {})
        .setdefault("session_risk_control", {})
    )

    entry_filters["min_vwap_score_for_entry"] = 0.14
    entry_filters["preflip_trial_min_shrink_pct_long"] = 0.60
    entry_filters["preflip_trial_min_signal_score"] = 0.75
    entry_thresholds["red_bar_growing"] = 0.86
    entry_thresholds["stable_bear_continuation_min_signal_score"] = 0.83
    entry_thresholds["stable_bull_continuation_min_signal_score"] = 0.83
    scoring_weights["weight_4h_direction"] = 0.55
    scoring_weights["weight_15m_entry"] = 0.05
    stop_loss_cfg["exit_4h_min_shrink_pct"] = 0.15
    stop_loss_cfg["exit_4h_require_profit"] = False
    session_risk["high_risk_sessions"] = [
        {"utc_start": "03:00", "utc_end": "05:30", "position_scale": 0.60},
        {"utc_start": "14:30", "utc_end": "16:00", "position_scale": 0.55},
    ]


def mutate_expand_max_active_symbols_5(cfg: dict) -> None:
    trading_cfg = cfg.setdefault("trading", {})
    fund_flow_cfg = cfg.setdefault("fund_flow", {})
    trading_cfg["max_active_symbols"] = 5
    fund_flow_cfg["max_active_symbols"] = 5


def mutate_asymmetric_tp_sl(cfg: dict) -> None:
    fund_flow_cfg = cfg.setdefault("fund_flow", {})
    fund_flow_cfg["take_profit_pct"] = 0.03
    fund_flow_cfg["stop_loss_pct"] = 0.018


VARIANTS: List[dict[str, Any]] = [
    {
        "name": "current_live_converged",
        "source": "current",
        "description": "Current live-converged backtest config baseline.",
        "mutators": [mutate_noop],
    },
    {
        "name": "restore_blacklist",
        "source": "current",
        "description": "Remove trading.symbol_blacklist only.",
        "mutators": [mutate_remove_blacklist],
    },
    {
        "name": "restore_entry_blocks",
        "source": "current",
        "description": "Re-enable flip_bullish / green_bar_growing / red_bar_growing_long entries only.",
        "mutators": [mutate_restore_entry_blocks],
    },
    {
        "name": "restore_blacklist_and_entry_blocks",
        "source": "current",
        "description": "Remove blacklist and re-enable the three blocked entry families.",
        "mutators": [mutate_remove_blacklist, mutate_restore_entry_blocks],
    },
    {
        "name": "restore_primary_signal_path",
        "source": "current",
        "description": "Remove blacklist, re-enable blocked entries, then restore the main baseline signal-path thresholds and exits.",
        "mutators": [
            mutate_remove_blacklist,
            mutate_restore_entry_blocks,
            mutate_restore_secondary_signal_path,
        ],
    },
    {
        "name": "opt_capacity_5",
        "source": "current",
        "description": "Current config with max_active_symbols expanded to 5.",
        "mutators": [mutate_expand_max_active_symbols_5],
    },
    {
        "name": "opt_asymmetric_tp_sl",
        "source": "current",
        "description": "Current config with take_profit_pct=0.03 and stop_loss_pct=0.018.",
        "mutators": [mutate_asymmetric_tp_sl],
    },
    {
        "name": "baseline_132_reference",
        "source": "baseline",
        "description": "Reference run of config/trading_config_fund_flow.json.",
        "mutators": [mutate_noop],
    },
]


def build_variant_config(base_cfg: dict, mutators: List[VariantMutator]) -> dict:
    variant_cfg = deepcopy(base_cfg)
    for mutator in mutators:
        mutator(variant_cfg)
    return variant_cfg


def collect_row(
    name: str,
    description: str,
    engine,
    config_file: Path,
    source: str,
) -> dict:
    summary = dict(getattr(engine, "last_summary", {}) or {})
    risk_metrics = summary.get("risk_metrics", {}) if isinstance(summary.get("risk_metrics"), dict) else {}
    return {
        "variant": name,
        "source": source,
        "description": description,
        "config_path": str(config_file),
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


def run_variant(
    variant: dict[str, Any],
    baseline_cfg: dict,
    current_cfg: dict,
    generated_config_dir: Path,
    start_time: str,
    end_time: str,
    quiet: bool,
) -> dict:
    source_name = str(variant["source"])
    source_cfg = baseline_cfg if source_name == "baseline" else current_cfg
    variant_cfg = build_variant_config(source_cfg, list(variant["mutators"]))

    variant_file = generated_config_dir / f"{variant['name']}.json"
    write_json(variant_file, variant_cfg)

    if quiet:
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            engine = run_backtest(
                config_path=str(variant_file),
                start_time=start_time,
                end_time=end_time,
            )
    else:
        engine = run_backtest(
            config_path=str(variant_file),
            start_time=start_time,
            end_time=end_time,
        )

    return collect_row(
        name=str(variant["name"]),
        description=str(variant["description"]),
        engine=engine,
        config_file=variant_file,
        source=source_name,
    )


def render_markdown(rows: List[dict]) -> str:
    lines = [
        "# Strategy Ablation Report 2026-03-27",
        "",
        "| Variant | Source | Return % | Win Rate % | Profit Factor | Trades | Max DD % | Available Symbols |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['variant']} | {row['source']} | "
            f"{row['return_pct']:.2f} | {row['win_rate_pct']:.2f} | {row['profit_factor']:.2f} | "
            f"{row['total_trades']} | {row['max_drawdown_pct']:.2f} | {row['available_symbols']} |"
        )
    lines.append("")
    lines.append("## Variant Notes")
    lines.append("")
    for row in rows:
        lines.append(f"- `{row['variant']}`: {row['description']}")
    return "\n".join(lines) + "\n"


def write_outputs(rows: List[dict], output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"strategy_ablation_{timestamp}.json"
    csv_path = output_dir / f"strategy_ablation_{timestamp}.csv"
    md_path = output_dir / f"strategy_ablation_{timestamp}.md"

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "variant",
        "source",
        "description",
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

    md_path.write_text(render_markdown(rows), encoding="utf-8")
    return json_path, csv_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run targeted MACD V2 strategy ablations.")
    parser.add_argument(
        "--baseline-config",
        default="config/trading_config_fund_flow.json",
        help="132%% baseline config path",
    )
    parser.add_argument(
        "--current-config",
        default="config/trading_config_fund_flow_live_backtest_converged_20260327.json",
        help="current live-converged backtest config path",
    )
    parser.add_argument("--start", required=True, help="window start ISO timestamp")
    parser.add_argument("--end", required=True, help="window end ISO timestamp")
    parser.add_argument(
        "--variants",
        nargs="*",
        default=None,
        help="optional subset of variants to run; defaults to all",
    )
    parser.add_argument(
        "--output-dir",
        default="output/backtest/strategy_ablation_20260327",
        help="directory for generated configs and reports",
    )
    parser.add_argument(
        "--quiet-backtests",
        action="store_true",
        help="suppress verbose logs from individual backtests",
    )
    args = parser.parse_args()

    baseline_path = ROOT_DIR / args.baseline_config
    current_path = ROOT_DIR / args.current_config
    output_dir = ROOT_DIR / args.output_dir
    generated_config_dir = output_dir / "generated_configs"

    baseline_cfg = load_json(baseline_path)
    current_cfg = load_json(current_path)

    variants = VARIANTS
    if args.variants:
        requested = {name.strip() for name in args.variants if name.strip()}
        variants = [variant for variant in VARIANTS if variant["name"] in requested]
        if not variants:
            raise ValueError("no matching variants selected")

    rows: List[dict] = []
    for variant in variants:
        print(f"[ablation] running {variant['name']}")
        row = run_variant(
            variant=variant,
            baseline_cfg=baseline_cfg,
            current_cfg=current_cfg,
            generated_config_dir=generated_config_dir,
            start_time=args.start,
            end_time=args.end,
            quiet=args.quiet_backtests,
        )
        rows.append(row)
        print(
            f"[ablation] {row['variant']}: return={row['return_pct']:.2f}% "
            f"win_rate={row['win_rate_pct']:.2f}% trades={row['total_trades']} "
            f"mdd={row['max_drawdown_pct']:.2f}%"
        )

    rows.sort(key=lambda item: item["return_pct"], reverse=True)
    json_path, csv_path, md_path = write_outputs(rows, output_dir)
    print(f"[ablation] json: {json_path}")
    print(f"[ablation] csv: {csv_path}")
    print(f"[ablation] md: {md_path}")


if __name__ == "__main__":
    main()
