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
    from scripts.analyze_rank1_reclaim_samebar_conflicts import _classify_tp1_hit_timing, _merge_rank1_tp_plan
except ModuleNotFoundError:
    from analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger  # type: ignore
    from analyze_rank1_reclaim_quality import _aggregate_rank1_entries, _load_csv  # type: ignore
    from analyze_rank1_reclaim_expansion import _load_symbol_bars  # type: ignore
    from analyze_rank1_reclaim_samebar_conflicts import _classify_tp1_hit_timing, _merge_rank1_tp_plan  # type: ignore


def _window_metrics(row: pd.Series, bars: pd.DataFrame, horizon_minutes: int) -> Dict[str, object]:
    entry_time = pd.to_datetime(row["entry_time"], errors="coerce", utc=True)
    exit_time = pd.to_datetime(row["exit_time"], errors="coerce", utc=True)
    limit_time = min(exit_time, entry_time + pd.Timedelta(minutes=horizon_minutes))
    entry_bar_time = entry_time + pd.Timedelta(minutes=horizon_minutes)
    window = bars.loc[(bars["timestamp"] > entry_time) & (bars["timestamp"] <= limit_time)].copy()
    entry_price = float(pd.to_numeric(row.get("entry_price"), errors="coerce") or 0.0)
    tp1_pct = float(pd.to_numeric(row.get("tp1_pct"), errors="coerce") or 0.0)
    if window.empty or entry_price <= 0.0:
        return {
            "horizon_minutes": horizon_minutes,
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
            "close_return_pct": 0.0,
            "tp1_progress_pct": 0.0,
            "tp1_hit": False,
            "near_tp1_75": False,
            "near_tp1_50": False,
            "truncated_by_exit": bool(exit_time < entry_bar_time),
        }
    side = str(row.get("side", "")).lower()
    if side == "short":
        best_price = float(pd.to_numeric(window["low"], errors="coerce").min())
        worst_price = float(pd.to_numeric(window["high"], errors="coerce").max())
        last_close = float(pd.to_numeric(window["close"], errors="coerce").iloc[-1])
        mfe_pct = max(0.0, (entry_price - best_price) / entry_price * 100.0)
        mae_pct = max(0.0, (worst_price - entry_price) / entry_price * 100.0)
        close_return_pct = (entry_price - last_close) / entry_price * 100.0
    else:
        best_price = float(pd.to_numeric(window["high"], errors="coerce").max())
        worst_price = float(pd.to_numeric(window["low"], errors="coerce").min())
        last_close = float(pd.to_numeric(window["close"], errors="coerce").iloc[-1])
        mfe_pct = max(0.0, (best_price - entry_price) / entry_price * 100.0)
        mae_pct = max(0.0, (entry_price - worst_price) / entry_price * 100.0)
        close_return_pct = (last_close - entry_price) / entry_price * 100.0
    tp1_progress_pct = (mfe_pct / (tp1_pct * 100.0) * 100.0) if tp1_pct > 0 else 0.0
    progress_eps = 1e-9
    return {
        "horizon_minutes": horizon_minutes,
        "mfe_pct": round(mfe_pct, 4),
        "mae_pct": round(mae_pct, 4),
        "close_return_pct": round(close_return_pct, 4),
        "tp1_progress_pct": round(tp1_progress_pct, 4),
        "tp1_hit": bool(tp1_pct > 0 and mfe_pct + progress_eps >= tp1_pct * 100.0),
        "near_tp1_75": bool(tp1_progress_pct + progress_eps >= 75.0),
        "near_tp1_50": bool(tp1_progress_pct + progress_eps >= 50.0),
        "truncated_by_exit": bool(exit_time < entry_bar_time),
    }


def build_never_hit_expansion_report(
    ledger_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    cache_dir: Path,
    signal_type: str,
    vwap_state: str,
) -> Dict[str, object]:
    entries_df = _aggregate_rank1_entries(ledger_df, trades_df, signal_type, vwap_state)
    if entries_df.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "count": 0,
            "note": "no tp1_never_hit rank1 reclaim time_exit_only entries found",
        }
    entries_df = _merge_rank1_tp_plan(entries_df, trades_df, ledger_df, signal_type, vwap_state)

    trade_meta = (
        trades_df.copy()
        .assign(
            entry_time=lambda x: pd.to_datetime(x["entry_time"], errors="coerce", utc=True),
            exit_time=lambda x: pd.to_datetime(x.get("exit_time"), errors="coerce", utc=True),
            entry_price=lambda x: pd.to_numeric(x.get("entry_price"), errors="coerce"),
        )
        .loc[:, ["symbol", "side", "entry_time", "entry_price", "exit_time"]]
        .groupby(["symbol", "side", "entry_time"], dropna=False, as_index=False)
        .agg(entry_price=("entry_price", "first"), exit_time=("exit_time", "max"))
    )
    entries_df = entries_df.drop(columns=[c for c in ("entry_price", "exit_time") if c in entries_df.columns]).merge(
        trade_meta,
        how="left",
        on=["symbol", "side", "entry_time"],
    )

    cache: Dict[str, pd.DataFrame] = {}
    rows: List[Dict[str, object]] = []
    for _, row in entries_df.loc[entries_df["exit_family"] == "time_exit_only"].iterrows():
        symbol = str(row["symbol"])
        if symbol not in cache:
            cache[symbol] = _load_symbol_bars(cache_dir, symbol, timeframe="15m")
        timing = _classify_tp1_hit_timing(row, cache[symbol])
        if str(timing.get("tp1_hit_timing")) != "tp1_never_hit":
            continue
        base = row.to_dict()
        base.update(timing)
        for horizon in (15, 30, 45):
            metrics = _window_metrics(row, cache[symbol], horizon)
            enriched = dict(base)
            enriched.update(metrics)
            rows.append(enriched)

    report_rows = pd.DataFrame(rows)
    if report_rows.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "count": 0,
            "note": "no tp1_never_hit rank1 reclaim time_exit_only entries found",
        }

    by_horizon: List[Dict[str, object]] = []
    for horizon, group in report_rows.groupby("horizon_minutes", dropna=False):
        by_horizon.append(
            {
                "horizon_minutes": int(horizon),
                "count": int(len(group)),
                "avg_mfe_pct": round(float(group["mfe_pct"].mean()), 4),
                "avg_mae_pct": round(float(group["mae_pct"].mean()), 4),
                "avg_close_return_pct": round(float(group["close_return_pct"].mean()), 4),
                "avg_tp1_progress_pct": round(float(group["tp1_progress_pct"].mean()), 4),
                "tp1_hit_rate_pct": round(float(group["tp1_hit"].mean() * 100.0), 4),
                "near_tp1_75_rate_pct": round(float(group["near_tp1_75"].mean() * 100.0), 4),
                "near_tp1_50_rate_pct": round(float(group["near_tp1_50"].mean() * 100.0), 4),
                "truncated_rate_pct": round(float(group["truncated_by_exit"].mean() * 100.0), 4),
            }
        )
    by_horizon.sort(key=lambda x: int(x["horizon_minutes"]))

    examples = (
        report_rows.loc[:, [
            "symbol",
            "entry_time",
            "horizon_minutes",
            "mfe_pct",
            "mae_pct",
            "close_return_pct",
            "tp1_progress_pct",
            "signal_score",
            "vwap_score",
            "adx_1h",
            "reasons",
        ]]
        .sort_values(["symbol", "horizon_minutes"])
        .assign(entry_time=lambda x: x["entry_time"].astype(str))
        .to_dict(orient="records")
    )

    return {
        "signal_type": signal_type,
        "vwap_state": vwap_state,
        "count": int(
            report_rows.loc[:, ["symbol", "entry_time"]]
            .drop_duplicates()
            .shape[0]
        ),
        "by_horizon": by_horizon,
        "cases": examples,
    }


def write_md(path: Path, report: Dict[str, object]) -> None:
    lines = [
        "# Rank-1 Reclaim Never-Hit Expansion Audit",
        "",
        f"- signal_type: `{report.get('signal_type')}`",
        f"- vwap_state: `{report.get('vwap_state')}`",
        "",
    ]
    if int(report.get("count", 0) or 0) <= 0:
        lines.append(f"- note: `{report.get('note', 'no cases')}`")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    lines.extend(
        [
            f"- count: `{report.get('count', 0)}`",
            "",
            "| horizon | count | avg_mfe | avg_mae | avg_close_return | avg_tp1_progress | tp1_hit | near75 | near50 | truncated |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report.get("by_horizon", []):
        lines.append(
            f"| {int(row['horizon_minutes'])}m | {row['count']} | {float(row['avg_mfe_pct']):.4f}% | "
            f"{float(row['avg_mae_pct']):.4f}% | {float(row['avg_close_return_pct']):+.4f}% | "
            f"{float(row['avg_tp1_progress_pct']):.2f}% | {float(row['tp1_hit_rate_pct']):.2f}% | "
            f"{float(row['near_tp1_75_rate_pct']):.2f}% | {float(row['near_tp1_50_rate_pct']):.2f}% | "
            f"{float(row['truncated_rate_pct']):.2f}% |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit 15m/30m/45m expansion quality for rank-1 reclaim tp1_never_hit entries.")
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
    report = build_never_hit_expansion_report(
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
