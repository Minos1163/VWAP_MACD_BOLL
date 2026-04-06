from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from scripts.diagnostics.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
except ModuleNotFoundError:
    from scripts.diagnostics.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger  # type: ignore


def _load_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path.resolve(), low_memory=False)


def _first_numeric(group: pd.DataFrame, candidates: List[str], default: float = 0.0) -> float:
    for column in candidates:
        if column in group.columns:
            series = pd.to_numeric(group[column], errors="coerce").dropna()
            if not series.empty:
                return float(series.iloc[0])
    return float(default)


def _aggregate_rank1_entries(ledger_df: pd.DataFrame, trades_df: pd.DataFrame, signal_type: str, vwap_state: str) -> pd.DataFrame:
    ledger = ledger_df.copy()
    ledger["timestamp"] = pd.to_datetime(ledger["timestamp"], errors="coerce", utc=True)
    if "cluster_key" in ledger.columns:
        cluster_key = ledger["cluster_key"].fillna("").astype(str)
        valid_cluster_mask = cluster_key.ne("")
        ledger["cluster_size"] = 1
        if valid_cluster_mask.any():
            ledger.loc[valid_cluster_mask, "cluster_size"] = (
                ledger.loc[valid_cluster_mask]
                .groupby(cluster_key[valid_cluster_mask], dropna=False)["timestamp"]
                .transform("size")
                .astype(int)
            )
    elif "cluster_size" not in ledger.columns:
        ledger["cluster_size"] = 1
    rank1 = ledger[
        (ledger["signal_type_1h"].astype(str) == str(signal_type))
        & (ledger["vwap_state"].astype(str) == str(vwap_state))
        & (ledger["cluster_rank"] == 1)
        & (ledger["final_opened"] == True)
    ].copy()
    if rank1.empty:
        return pd.DataFrame()

    trades = trades_df.copy()
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], errors="coerce", utc=True)
    trades["exit_time"] = pd.to_datetime(trades.get("exit_time"), errors="coerce", utc=True)
    trades["pnl"] = pd.to_numeric(trades.get("pnl", 0.0), errors="coerce").fillna(0.0)
    trades = trades[
        (trades["signal_type_1h"].astype(str) == str(signal_type))
        & (trades["vwap_state"].astype(str) == str(vwap_state))
    ].copy()

    merged = rank1.merge(
        trades,
        how="inner",
        left_on=["symbol", "timestamp", "signal_type_1h", "vwap_state"],
        right_on=["symbol", "entry_time", "signal_type_1h", "vwap_state"],
        suffixes=("_ledger", "_trade"),
    )
    if merged.empty:
        return pd.DataFrame()
    if "side" not in merged.columns:
        if "side_trade" in merged.columns:
            merged["side"] = merged["side_trade"]
        elif "side_ledger" in merged.columns:
            merged["side"] = merged["side_ledger"]

    grouping = ["symbol", "entry_time", "side", "signal_type_1h", "vwap_state"]
    rows: List[Dict[str, object]] = []
    for keys, group in merged.groupby(grouping, dropna=False):
        symbol, entry_time, side, signal_type_1h, vwap_state = keys
        reasons = [str(x or "") for x in group.get("reason", pd.Series(dtype=str)).tolist() if str(x or "")]
        pnl = float(group["pnl"].sum())
        has_time_exit = any("time_exit" in text for text in reasons)
        has_stop = any("stop_loss_intrabar" in text for text in reasons)
        has_tp = any(
            marker in text
            for text in reasons
            for marker in ("take_profit_level_intrabar", "take_profit_intrabar", "partial_tp_level")
        )
        if has_time_exit and has_tp:
            exit_family = "tp_then_time_exit"
        elif has_time_exit:
            exit_family = "time_exit_only"
        elif has_stop and has_tp:
            exit_family = "tp_then_stop"
        elif has_stop:
            exit_family = "stop_only"
        elif has_tp:
            exit_family = "pure_tp"
        else:
            exit_family = "other"
        rows.append(
            {
                "symbol": symbol,
                "entry_time": entry_time,
                "side": side,
                "signal_type_1h": signal_type_1h,
                "vwap_state": vwap_state,
                "cluster_rank": int(_first_numeric(group, ["cluster_rank"], default=1.0)),
                "cluster_size": int(_first_numeric(group, ["cluster_size"], default=1.0)),
                "cluster_age_minutes": _first_numeric(group, ["cluster_age_minutes"], default=0.0),
                "signal_score": _first_numeric(group, ["signal_score_ledger", "signal_score"], default=0.0),
                "vwap_score": _first_numeric(group, ["vwap_score_ledger", "vwap_score"], default=0.0),
                "adx_1h": _first_numeric(group, ["adx_1h"], default=0.0),
                "entry_scale": _first_numeric(group, ["entry_scale"], default=0.0),
                "hold_minutes": float(
                    ((group["exit_time"].max() - group["entry_time"].min()).total_seconds() / 60.0)
                    if pd.notna(group["exit_time"].max()) and pd.notna(group["entry_time"].min())
                    else 0.0
                ),
                "pnl": pnl,
                "win": bool(pnl > 0.0),
                "has_time_exit": has_time_exit,
                "has_stop_loss_intrabar": has_stop,
                "has_take_profit": has_tp,
                "exit_family": exit_family,
                "reasons": reasons,
            }
        )
    return pd.DataFrame(rows).sort_values(["entry_time", "symbol"]).reset_index(drop=True)


def _summarize_group(df: pd.DataFrame) -> Dict[str, object]:
    if df.empty:
        return {"count": 0, "win_rate_pct": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}
    return {
        "count": int(len(df)),
        "win_rate_pct": round(float(df["win"].mean() * 100.0), 4),
        "total_pnl": round(float(df["pnl"].sum()), 4),
        "avg_pnl": round(float(df["pnl"].mean()), 4),
        "avg_signal_score": round(float(df["signal_score"].mean()), 4),
        "avg_vwap_score": round(float(df["vwap_score"].mean()), 4),
        "avg_adx_1h": round(float(df["adx_1h"].mean()), 4),
        "avg_hold_minutes": round(float(df["hold_minutes"].mean()), 2),
        "avg_entry_scale": round(float(df["entry_scale"].mean()), 4),
    }


def build_report(entries_df: pd.DataFrame, signal_type: str, vwap_state: str) -> Dict[str, object]:
    if entries_df.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "count": 0,
            "note": "no rank1 opened entries found",
        }

    by_exit_family = []
    for family, group in entries_df.groupby("exit_family", dropna=False):
        item = {"exit_family": str(family)}
        item.update(_summarize_group(group))
        by_exit_family.append(item)
    by_exit_family.sort(key=lambda row: (-int(row["count"]), str(row["exit_family"])))

    return {
        "signal_type": signal_type,
        "vwap_state": vwap_state,
        "overall": _summarize_group(entries_df),
        "by_exit_family": by_exit_family,
        "worst_time_exit_entries": (
            entries_df.loc[entries_df["has_time_exit"], [
                "symbol",
                "entry_time",
                "pnl",
                "signal_score",
                "vwap_score",
                "adx_1h",
                "entry_scale",
                "hold_minutes",
                "exit_family",
                "reasons",
            ]]
            .sort_values("pnl", ascending=True)
            .head(12)
            .assign(entry_time=lambda x: x["entry_time"].astype(str))
            .to_dict(orient="records")
        ),
        "worst_stop_entries": (
            entries_df.loc[entries_df["has_stop_loss_intrabar"], [
                "symbol",
                "entry_time",
                "pnl",
                "signal_score",
                "vwap_score",
                "adx_1h",
                "entry_scale",
                "hold_minutes",
                "exit_family",
                "reasons",
            ]]
            .sort_values("pnl", ascending=True)
            .head(12)
            .assign(entry_time=lambda x: x["entry_time"].astype(str))
            .to_dict(orient="records")
        ),
    }


def write_md(path: Path, report: Dict[str, object]) -> None:
    overall = report.get("overall", {})
    lines = [
        "# Rank-1 Reclaim Quality Audit",
        "",
        f"- signal_type: `{report.get('signal_type')}`",
        f"- vwap_state: `{report.get('vwap_state')}`",
        "",
        "## Overall",
        "",
        f"- count: `{overall.get('count', 0)}`",
        f"- win_rate_pct: `{overall.get('win_rate_pct', 0.0):.2f}%`",
        f"- total_pnl: `{overall.get('total_pnl', 0.0):+.2f}`",
        f"- avg_pnl: `{overall.get('avg_pnl', 0.0):+.2f}`",
        f"- avg_signal_score: `{overall.get('avg_signal_score', 0.0):.4f}`",
        f"- avg_vwap_score: `{overall.get('avg_vwap_score', 0.0):.4f}`",
        f"- avg_adx_1h: `{overall.get('avg_adx_1h', 0.0):.2f}`",
        f"- avg_hold_minutes: `{overall.get('avg_hold_minutes', 0.0):.2f}`",
        "",
        "## By Exit Family",
        "",
        "| family | count | WR | total_pnl | avg_pnl | avg_signal | avg_vwap | avg_adx | avg_hold | avg_scale |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("by_exit_family", []):
        lines.append(
            f"| {row['exit_family']} | {row['count']} | {row['win_rate_pct']:.2f}% | {row['total_pnl']:+.2f} | "
            f"{row['avg_pnl']:+.2f} | {row['avg_signal_score']:.4f} | {row['avg_vwap_score']:.4f} | "
            f"{row['avg_adx_1h']:.2f} | {row['avg_hold_minutes']:.2f} | {row['avg_entry_scale']:.2f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit rank-1 reclaim entries and split time_exit vs stop_loss_intrabar quality.")
    parser.add_argument("--candidate-ledger", required=True)
    parser.add_argument("--bot-trades", required=True)
    parser.add_argument("--signal-type", default="red_bar_growing")
    parser.add_argument("--vwap-state", default="long_reclaim_confirmed")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    ledger_df = _prepare_candidate_ledger(_load_csv(Path(args.candidate_ledger)))
    trades_df = _load_csv(Path(args.bot_trades))
    entries_df = _aggregate_rank1_entries(ledger_df, trades_df, str(args.signal_type), str(args.vwap_state))
    report = build_report(entries_df, str(args.signal_type), str(args.vwap_state))
    Path(args.output_json).resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(Path(args.output_md).resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

