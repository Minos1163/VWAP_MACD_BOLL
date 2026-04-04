from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from scripts.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
    from scripts.analyze_rank1_reclaim_quality import _aggregate_rank1_entries, _load_csv
    from scripts.analyze_rank1_reclaim_expansion import _load_symbol_bars
except ModuleNotFoundError:
    from analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger  # type: ignore
    from analyze_rank1_reclaim_quality import _aggregate_rank1_entries, _load_csv  # type: ignore
    from analyze_rank1_reclaim_expansion import _load_symbol_bars  # type: ignore


def _merge_rank1_tp_plan(
    entries_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    ledger_df: pd.DataFrame,
    signal_type: str,
    vwap_state: str,
) -> pd.DataFrame:
    ledger = ledger_df.copy()
    ledger["timestamp"] = pd.to_datetime(ledger["timestamp"], errors="coerce", utc=True)
    rank1 = ledger[
        (ledger["signal_type_1h"].astype(str) == str(signal_type))
        & (ledger["vwap_state"].astype(str) == str(vwap_state))
        & (pd.to_numeric(ledger["cluster_rank"], errors="coerce").fillna(0).astype(int) == 1)
        & (ledger["final_opened"] == True)
    ].copy()
    cols = ["symbol", "timestamp", "tp1_price", "tp1_pct", "tp1_reduce_pct"]
    available = [c for c in cols if c in rank1.columns]
    if not available:
        return entries_df.copy()
    merged = entries_df.merge(
        rank1[available],
        how="left",
        left_on=["symbol", "entry_time"],
        right_on=["symbol", "timestamp"],
    )
    trade_times = (
        trades_df.copy()
        .assign(
            entry_time=lambda x: pd.to_datetime(x["entry_time"], errors="coerce", utc=True),
            exit_time=lambda x: pd.to_datetime(x["exit_time"], errors="coerce", utc=True),
        )
        .loc[:, ["symbol", "side", "entry_time", "exit_time"]]
        .groupby(["symbol", "side", "entry_time"], dropna=False, as_index=False)
        .agg(exit_time=("exit_time", "max"))
    )
    return merged.merge(
        trade_times,
        how="left",
        on=["symbol", "side", "entry_time"],
    )


def _classify_tp1_hit_timing(row: pd.Series, bars: pd.DataFrame) -> Dict[str, object]:
    entry_time = pd.to_datetime(row["entry_time"], errors="coerce", utc=True)
    exit_time = pd.to_datetime(row["exit_time"], errors="coerce", utc=True)
    tp1_price = float(pd.to_numeric(row.get("tp1_price"), errors="coerce") or 0.0)
    side = str(row.get("side", "")).lower()
    if tp1_price <= 0.0:
        return {
            "tp1_hit_timing": "missing_tp1_plan",
            "tp1_first_hit_time": None,
            "tp1_hit_before_exit_bar": False,
            "tp1_hit_on_exit_bar_only": False,
            "tp1_never_hit": True,
        }

    window = bars.loc[(bars["timestamp"] >= entry_time) & (bars["timestamp"] <= exit_time)].copy()
    if window.empty:
        return {
            "tp1_hit_timing": "no_bars",
            "tp1_first_hit_time": None,
            "tp1_hit_before_exit_bar": False,
            "tp1_hit_on_exit_bar_only": False,
            "tp1_never_hit": True,
        }

    if side == "short":
        hit_mask = pd.to_numeric(window["low"], errors="coerce").fillna(float("inf")) <= tp1_price
    else:
        hit_mask = pd.to_numeric(window["high"], errors="coerce").fillna(float("-inf")) >= tp1_price

    if not hit_mask.any():
        return {
            "tp1_hit_timing": "tp1_never_hit",
            "tp1_first_hit_time": None,
            "tp1_hit_before_exit_bar": False,
            "tp1_hit_on_exit_bar_only": False,
            "tp1_never_hit": True,
        }

    first_hit_time = pd.to_datetime(window.loc[hit_mask, "timestamp"].iloc[0], errors="coerce", utc=True)
    before_exit_bar = bool(pd.notna(first_hit_time) and pd.notna(exit_time) and first_hit_time < exit_time)
    on_exit_bar_only = bool(pd.notna(first_hit_time) and pd.notna(exit_time) and first_hit_time == exit_time)
    if before_exit_bar:
        timing = "tp1_hit_before_exit_bar"
    elif on_exit_bar_only:
        timing = "tp1_hit_on_exit_bar_only"
    else:
        timing = "tp1_never_hit"
    return {
        "tp1_hit_timing": timing,
        "tp1_first_hit_time": first_hit_time.isoformat() if pd.notna(first_hit_time) else None,
        "tp1_hit_before_exit_bar": before_exit_bar,
        "tp1_hit_on_exit_bar_only": on_exit_bar_only,
        "tp1_never_hit": timing == "tp1_never_hit",
    }


def build_samebar_report(
    ledger_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    cache_dir: Path,
    signal_type: str,
    vwap_state: str,
) -> Dict[str, object]:
    entries_df = _aggregate_rank1_entries(ledger_df, trades_df, signal_type, vwap_state)
    entries_df = _merge_rank1_tp_plan(entries_df, trades_df, ledger_df, signal_type, vwap_state)
    if entries_df.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "count": 0,
            "note": "no rank1 reclaim entries found",
        }

    time_exit_df = entries_df.loc[entries_df["has_time_exit"] == True].copy()
    if time_exit_df.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "count": 0,
            "note": "no rank1 reclaim time_exit entries found",
        }

    cache: Dict[str, pd.DataFrame] = {}
    rows: List[Dict[str, object]] = []
    for _, row in time_exit_df.iterrows():
        symbol = str(row["symbol"])
        if symbol not in cache:
            cache[symbol] = _load_symbol_bars(cache_dir, symbol, timeframe="15m")
        timing = _classify_tp1_hit_timing(row, cache[symbol])
        enriched = row.to_dict()
        enriched.update(timing)
        rows.append(enriched)
    result_df = pd.DataFrame(rows)

    def summarize(df: pd.DataFrame) -> Dict[str, object]:
        if df.empty:
            return {"count": 0, "win_rate_pct": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}
        return {
            "count": int(len(df)),
            "win_rate_pct": round(float(df["win"].mean() * 100.0), 4),
            "total_pnl": round(float(df["pnl"].sum()), 4),
            "avg_pnl": round(float(df["pnl"].mean()), 4),
            "avg_hold_minutes": round(float(df["hold_minutes"].mean()), 2),
            "avg_signal_score": round(float(df["signal_score"].mean()), 4),
            "avg_vwap_score": round(float(df["vwap_score"].mean()), 4),
        }

    by_timing = []
    for timing, group in result_df.groupby("tp1_hit_timing", dropna=False):
        item = {"tp1_hit_timing": str(timing)}
        item.update(summarize(group))
        by_timing.append(item)
    by_timing.sort(key=lambda row: (-int(row["count"]), row["tp1_hit_timing"]))

    suspicious = result_df.loc[result_df["tp1_hit_timing"] == "tp1_hit_before_exit_bar"].copy()
    suspicious_examples = (
        suspicious.loc[:, [
            "symbol",
            "entry_time",
            "exit_family",
            "pnl",
            "hold_minutes",
            "signal_score",
            "vwap_score",
            "tp1_price",
            "tp1_pct",
            "tp1_first_hit_time",
            "reasons",
        ]]
        .sort_values("pnl", ascending=True)
        .head(12)
        .assign(entry_time=lambda x: x["entry_time"].astype(str))
        .to_dict(orient="records")
    )

    return {
        "signal_type": signal_type,
        "vwap_state": vwap_state,
        "overall_time_exit": summarize(result_df),
        "by_tp1_hit_timing": by_timing,
        "tp1_hit_before_exit_bar_examples": suspicious_examples,
    }


def write_md(path: Path, report: Dict[str, object]) -> None:
    overall = report.get("overall_time_exit", {})
    lines = [
        "# Rank-1 Reclaim Same-Bar TP1 Audit",
        "",
        f"- signal_type: `{report.get('signal_type')}`",
        f"- vwap_state: `{report.get('vwap_state')}`",
        "",
        "## Overall Time-Exit",
        "",
        f"- count: `{overall.get('count', 0)}`",
        f"- win_rate_pct: `{overall.get('win_rate_pct', 0.0):.2f}%`",
        f"- total_pnl: `{overall.get('total_pnl', 0.0):+.2f}`",
        f"- avg_pnl: `{overall.get('avg_pnl', 0.0):+.2f}`",
        "",
        "## By TP1 Hit Timing",
        "",
        "| timing | count | WR | total_pnl | avg_pnl | avg_hold | avg_signal | avg_vwap |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("by_tp1_hit_timing", []):
        lines.append(
            f"| {row['tp1_hit_timing']} | {row['count']} | {row['win_rate_pct']:.2f}% | {row['total_pnl']:+.2f} | {row['avg_pnl']:+.2f} | {row['avg_hold_minutes']:.2f} | {row['avg_signal_score']:.4f} | {row['avg_vwap_score']:.4f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit whether rank-1 reclaim time-exit trades had TP1 hit before the exit bar.")
    parser.add_argument("--candidate-ledger", required=True)
    parser.add_argument("--bot-trades", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--signal-type", default="red_bar_growing")
    parser.add_argument("--vwap-state", default="long_reclaim_confirmed")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    ledger_df = _prepare_candidate_ledger(_load_csv(Path(args.candidate_ledger)))
    trades_df = _load_csv(Path(args.bot_trades))
    report = build_samebar_report(
        ledger_df=ledger_df,
        trades_df=trades_df,
        cache_dir=Path(args.cache_dir).resolve(),
        signal_type=str(args.signal_type),
        vwap_state=str(args.vwap_state),
    )
    Path(args.output_json).resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(Path(args.output_md).resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
