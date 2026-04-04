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
    from scripts.analyze_ai_shortlist_effectiveness import (
        _aggregate_pure_entries,
        _match_to_pure_entries,
        _prepare_candidate_ledger,
    )
except ModuleNotFoundError:
    from analyze_ai_shortlist_effectiveness import (  # type: ignore
        _aggregate_pure_entries,
        _match_to_pure_entries,
        _prepare_candidate_ledger,
    )


def _load_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path.resolve(), low_memory=False)


def _aggregate_botlike_entries(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    working["entry_time"] = pd.to_datetime(working["entry_time"], errors="coerce", utc=True)
    working["exit_time"] = pd.to_datetime(working.get("exit_time"), errors="coerce", utc=True)
    working["pnl"] = pd.to_numeric(working.get("pnl", 0.0), errors="coerce").fillna(0.0)
    grouping = ["symbol", "side", "entry_time", "signal_type_1h", "vwap_state"]

    rows: List[Dict[str, object]] = []
    for keys, group in working.groupby(grouping, dropna=False):
        symbol, side, entry_time, signal_type_1h, vwap_state = keys
        reasons = [str(x or "") for x in group.get("reason", pd.Series(dtype=str)).tolist() if str(x or "")]
        rows.append(
            {
                "symbol": symbol,
                "side": side,
                "entry_time": entry_time,
                "signal_type_1h": signal_type_1h,
                "vwap_state": vwap_state,
                "bot_pnl": float(group["pnl"].sum()),
                "bot_win": bool(float(group["pnl"].sum()) > 0.0),
                "bot_reason": " | ".join(reasons[:3]),
            }
        )
    return pd.DataFrame(rows)


def _rank_summary(df: pd.DataFrame, value_col: str, win_col: str) -> List[Dict[str, object]]:
    if df.empty:
        return []
    working = df.copy()
    working["rank_bucket"] = working["cluster_rank"].clip(upper=4).map(
        {1: "rank_1", 2: "rank_2", 3: "rank_3", 4: "rank_4_plus"}
    )
    rows: List[Dict[str, object]] = []
    for bucket, group in working.groupby("rank_bucket", sort=False):
        rows.append(
            {
                "rank_bucket": str(bucket),
                "count": int(len(group)),
                "win_rate_pct": round(float(group[win_col].mean() * 100.0), 4) if len(group) else 0.0,
                "total_pnl": round(float(group[value_col].sum()), 4) if len(group) else 0.0,
                "avg_pnl": round(float(group[value_col].mean()), 4) if len(group) else 0.0,
            }
        )
    return rows


def build_report(
    ledger_df: pd.DataFrame,
    pure_df: pd.DataFrame,
    bot_df: pd.DataFrame,
    signal_type: str,
    vwap_state: str,
    tolerance_minutes: int,
    label: str,
) -> Dict[str, object]:
    merged = _match_to_pure_entries(ledger_df, pure_df, tolerance_minutes=tolerance_minutes)
    candidates = merged[
        (merged["generated_open_candidate"] == True)
        & (merged["signal_type_1h"].astype(str) == str(signal_type))
        & (merged["vwap_state"].astype(str) == str(vwap_state))
        & (merged["matched"] == True)
    ].copy()
    if candidates.empty:
        return {
            "label": label,
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "cluster_count": 0,
            "candidate_count": 0,
            "note": "no matched reclaim clusters",
        }

    candidates["pure_entry_key"] = (
        candidates["symbol"].fillna("").astype(str)
        + "|"
        + candidates["side"].fillna("").astype(str)
        + "|"
        + candidates["signal_type_1h"].fillna("").astype(str)
        + "|"
        + candidates["vwap_state"].fillna("").astype(str)
        + "|"
        + candidates["matched_entry_time"].astype(str)
    )
    candidates = candidates.sort_values(["pure_entry_key", "timestamp"]).reset_index(drop=True)
    candidates["cluster_rank"] = candidates.groupby("pure_entry_key").cumcount() + 1
    candidates["cluster_size"] = candidates.groupby("pure_entry_key")["pure_entry_key"].transform("size")
    candidates["minutes_from_first_candidate"] = candidates.groupby("pure_entry_key")["timestamp"].transform(
        lambda s: (s - s.min()).dt.total_seconds().div(60.0)
    )
    candidates["minutes_from_pure_entry"] = (
        candidates["timestamp"] - candidates["matched_entry_time"]
    ).dt.total_seconds().div(60.0)

    bot_entries = _aggregate_botlike_entries(bot_df)
    candidates = candidates.merge(
        bot_entries,
        how="left",
        left_on=["symbol", "side", "timestamp", "signal_type_1h", "vwap_state"],
        right_on=["symbol", "side", "entry_time", "signal_type_1h", "vwap_state"],
    )
    candidates["opened"] = candidates["bot_pnl"].notna()

    opened = candidates[candidates["opened"] == True].copy()
    opened["repeat_open"] = opened["cluster_rank"] > 1

    first_candidates = candidates[candidates["cluster_rank"] == 1].copy()
    first_candidates["first_candidate_outcome"] = "blocked"
    first_candidates.loc[first_candidates["opened"] == True, "first_candidate_outcome"] = "opened"

    cluster_shape = (
        candidates.groupby("pure_entry_key")
        .agg(
            candidate_count=("pure_entry_key", "size"),
            opened_count=("opened", "sum"),
            first_timestamp=("timestamp", "min"),
        )
        .reset_index()
    )
    cluster_shape["has_repeat_candidates"] = cluster_shape["candidate_count"] > 1

    report = {
        "label": label,
        "signal_type": signal_type,
        "vwap_state": vwap_state,
        "match_tolerance_minutes": tolerance_minutes,
        "cluster_count": int(candidates["pure_entry_key"].nunique()),
        "candidate_count": int(len(candidates)),
        "opened_count": int(opened["opened"].sum()) if not opened.empty else 0,
        "avg_cluster_size": round(float(cluster_shape["candidate_count"].mean()), 4),
        "repeat_cluster_rate_pct": round(float(cluster_shape["has_repeat_candidates"].mean() * 100.0), 4),
        "first_candidate_open_rate_pct": round(float(first_candidates["opened"].mean() * 100.0), 4),
        "opened_rank_summary": _rank_summary(opened, "bot_pnl", "bot_win"),
        "candidate_rank_summary": [
            {
                "rank_bucket": row["rank_bucket"],
                "count": row["count"],
                "open_rate_pct": round(
                    float(
                        candidates.loc[
                            candidates["cluster_rank"].clip(upper=4).map(
                                {1: "rank_1", 2: "rank_2", 3: "rank_3", 4: "rank_4_plus"}
                            )
                            == row["rank_bucket"],
                            "opened",
                        ].mean()
                        * 100.0
                    ),
                    4,
                ),
            }
            for row in _rank_summary(candidates.assign(opened_win=candidates["opened"]), "minutes_from_first_candidate", "opened")
        ],
        "first_vs_repeat_opened": {
            "first": {
                "count": int((opened["cluster_rank"] == 1).sum()),
                "win_rate_pct": round(float(opened.loc[opened["cluster_rank"] == 1, "bot_win"].mean() * 100.0), 4)
                if (opened["cluster_rank"] == 1).any()
                else 0.0,
                "total_pnl": round(float(opened.loc[opened["cluster_rank"] == 1, "bot_pnl"].sum()), 4),
            },
            "repeat": {
                "count": int((opened["cluster_rank"] > 1).sum()),
                "win_rate_pct": round(float(opened.loc[opened["cluster_rank"] > 1, "bot_win"].mean() * 100.0), 4)
                if (opened["cluster_rank"] > 1).any()
                else 0.0,
                "total_pnl": round(float(opened.loc[opened["cluster_rank"] > 1, "bot_pnl"].sum()), 4),
            },
        },
        "first_candidate_outcomes": first_candidates["first_candidate_outcome"].value_counts().to_dict(),
        "worst_repeat_opened": (
            opened.loc[opened["cluster_rank"] > 1, [
                "symbol",
                "timestamp",
                "cluster_rank",
                "cluster_size",
                "minutes_from_first_candidate",
                "minutes_from_pure_entry",
                "signal_score",
                "vwap_score",
                "bot_pnl",
                "bot_reason",
            ]]
            .sort_values("bot_pnl", ascending=True)
            .head(12)
            .assign(timestamp=lambda x: x["timestamp"].astype(str))
            .to_dict(orient="records")
        ),
        "worst_first_opened": (
            opened.loc[opened["cluster_rank"] == 1, [
                "symbol",
                "timestamp",
                "cluster_rank",
                "cluster_size",
                "minutes_from_pure_entry",
                "signal_score",
                "vwap_score",
                "bot_pnl",
                "bot_reason",
            ]]
            .sort_values("bot_pnl", ascending=True)
            .head(12)
            .assign(timestamp=lambda x: x["timestamp"].astype(str))
            .to_dict(orient="records")
        ),
    }
    return report


def write_md(path: Path, report: Dict[str, object], compare: Dict[str, object] | None = None) -> None:
    def section(title: str, item: Dict[str, object]) -> List[str]:
        lines = [
            f"## {title}",
            "",
            f"- cluster_count: `{item.get('cluster_count', 0)}`",
            f"- candidate_count: `{item.get('candidate_count', 0)}`",
            f"- opened_count: `{item.get('opened_count', 0)}`",
            f"- avg_cluster_size: `{item.get('avg_cluster_size', 0.0)}`",
            f"- repeat_cluster_rate_pct: `{item.get('repeat_cluster_rate_pct', 0.0):.2f}%`",
            f"- first_candidate_open_rate_pct: `{item.get('first_candidate_open_rate_pct', 0.0):.2f}%`",
            "",
            "### Opened Rank Summary",
            "",
            "| rank | count | WR | total_pnl | avg_pnl |",
            "|---|---:|---:|---:|---:|",
        ]
        for row in item.get("opened_rank_summary", []):
            lines.append(
                f"| {row['rank_bucket']} | {row['count']} | {row['win_rate_pct']:.2f}% | "
                f"{row['total_pnl']:+.2f} | {row['avg_pnl']:+.2f} |"
            )
        first_vs_repeat = item.get("first_vs_repeat_opened", {})
        lines.extend(
            [
                "",
                "### First vs Repeat Opened",
                "",
                f"- first: `{first_vs_repeat.get('first', {}).get('count', 0)}` / wr `{first_vs_repeat.get('first', {}).get('win_rate_pct', 0.0):.2f}%` / pnl `{first_vs_repeat.get('first', {}).get('total_pnl', 0.0):+.2f}`",
                f"- repeat: `{first_vs_repeat.get('repeat', {}).get('count', 0)}` / wr `{first_vs_repeat.get('repeat', {}).get('win_rate_pct', 0.0):.2f}%` / pnl `{first_vs_repeat.get('repeat', {}).get('total_pnl', 0.0):+.2f}`",
                "",
            ]
        )
        return lines

    lines = [
        "# Reclaim Candidate Cluster Audit",
        "",
        f"- signal_type: `{report.get('signal_type')}`",
        f"- vwap_state: `{report.get('vwap_state')}`",
        f"- match_tolerance_minutes: `{report.get('match_tolerance_minutes')}`",
        "",
    ]
    lines.extend(section(str(report.get("label", "primary")), report))
    if compare is not None:
        lines.append("")
        lines.extend(section(str(compare.get("label", "compare")), compare))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit bot-like reclaim candidate clusters around the same pure entry.")
    parser.add_argument("--candidate-ledger", required=True)
    parser.add_argument("--pure-trades", required=True)
    parser.add_argument("--bot-trades", required=True)
    parser.add_argument("--signal-type", default="red_bar_growing")
    parser.add_argument("--vwap-state", default="long_reclaim_confirmed")
    parser.add_argument("--match-tolerance-minutes", type=int, default=60)
    parser.add_argument("--label", default="primary")
    parser.add_argument("--compare-candidate-ledger", default="")
    parser.add_argument("--compare-bot-trades", default="")
    parser.add_argument("--compare-label", default="compare")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    pure_df = _aggregate_pure_entries(_load_csv(Path(args.pure_trades)))
    primary = build_report(
        ledger_df=_prepare_candidate_ledger(_load_csv(Path(args.candidate_ledger))),
        pure_df=pure_df,
        bot_df=_load_csv(Path(args.bot_trades)),
        signal_type=str(args.signal_type),
        vwap_state=str(args.vwap_state),
        tolerance_minutes=int(args.match_tolerance_minutes),
        label=str(args.label),
    )

    compare_report = None
    if args.compare_candidate_ledger and args.compare_bot_trades:
        compare_report = build_report(
            ledger_df=_prepare_candidate_ledger(_load_csv(Path(args.compare_candidate_ledger))),
            pure_df=pure_df,
            bot_df=_load_csv(Path(args.compare_bot_trades)),
            signal_type=str(args.signal_type),
            vwap_state=str(args.vwap_state),
            tolerance_minutes=int(args.match_tolerance_minutes),
            label=str(args.compare_label),
        )

    payload = {"primary": primary, "compare": compare_report}
    Path(args.output_json).resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(Path(args.output_md).resolve(), primary, compare_report)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
