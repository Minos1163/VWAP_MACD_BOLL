from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path
from typing import Dict, List

import pandas as pd


def _load_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False)


def _normalize_operation_to_side(value: object) -> str:
    text = str(value or "").upper()
    if "BUY" in text:
        return "long"
    if "SELL" in text:
        return "short"
    return ""


def _prepare_candidate_ledger(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    for column, default in (
        ("timestamp", pd.NaT),
        ("symbol", ""),
        ("local_operation", ""),
        ("signal_type_1h", ""),
        ("vwap_state", ""),
        ("cluster_rank", 0),
        ("cluster_age_minutes", 0.0),
        ("signal_score", 0.0),
        ("vwap_score", 0.0),
        ("capacity_block_reason", ""),
        ("final_reject_reason", ""),
        ("ai_block_reason", ""),
        ("generated_open_candidate", True),
    ):
        if column not in working.columns:
            working[column] = default
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)
    working["side"] = working.get("local_operation", "").map(_normalize_operation_to_side)
    working["signal_type_1h"] = working.get("signal_type_1h", "").fillna("").astype(str)
    working["vwap_state"] = working.get("vwap_state", "").fillna("").astype(str)
    working["pre_filter_passed"] = working.get("pre_filter_passed")
    if "ai_shortlisted" not in working.columns:
        working["ai_shortlisted"] = False
    if "ai_reviewed" not in working.columns:
        working["ai_reviewed"] = False
    if "ai_allowed" not in working.columns:
        working["ai_allowed"] = pd.NA
    if "capacity_selected" not in working.columns:
        working["capacity_selected"] = False
    if "final_opened" not in working.columns:
        working["final_opened"] = False
    working["ai_shortlisted"] = working["ai_shortlisted"].fillna(False).astype(bool)
    working["ai_reviewed"] = working["ai_reviewed"].fillna(False).astype(bool)
    working["ai_allowed"] = working.get("ai_allowed")
    working["capacity_selected"] = working["capacity_selected"].fillna(False).astype(bool)
    working["final_opened"] = working["final_opened"].fillna(False).astype(bool)
    working["match_key"] = (
        working["symbol"].fillna("").astype(str)
        + "|"
        + working["side"].fillna("").astype(str)
        + "|"
        + working["signal_type_1h"].fillna("").astype(str)
        + "|"
        + working["vwap_state"].fillna("").astype(str)
    )
    return working.sort_values(["match_key", "timestamp"]).reset_index(drop=True)


def _aggregate_pure_entries(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    working["entry_time"] = pd.to_datetime(working["entry_time"], errors="coerce", utc=True)
    working["exit_time"] = pd.to_datetime(working["exit_time"], errors="coerce", utc=True)
    working["pnl"] = pd.to_numeric(working.get("pnl", 0.0), errors="coerce").fillna(0.0)
    working["side"] = working.get("side", "").fillna("").astype(str).str.lower()
    working["signal_type_1h"] = working.get("signal_type_1h", "").fillna("").astype(str)
    working["vwap_state"] = working.get("vwap_state", "").fillna("").astype(str)

    grouping = ["symbol", "side", "entry_time", "entry_price", "signal_type_1h", "vwap_state"]
    rows: List[Dict[str, object]] = []
    for keys, group in working.groupby(grouping, dropna=False):
        symbol, side, entry_time, entry_price, signal_type_1h, vwap_state = keys
        pnl_sum = float(group["pnl"].sum())
        rows.append(
            {
                "symbol": symbol,
                "side": side,
                "entry_time": entry_time,
                "signal_type_1h": signal_type_1h,
                "vwap_state": vwap_state,
                "pure_pnl": pnl_sum,
                "pure_win": bool(pnl_sum > 0),
                "pure_hold_minutes": float(
                    ((group["exit_time"].max() - group["entry_time"].min()).total_seconds() / 60.0)
                    if pd.notna(group["exit_time"].max()) and pd.notna(group["entry_time"].min())
                    else 0.0
                ),
            }
        )

    result = pd.DataFrame(rows)
    result["match_key"] = (
        result["symbol"].fillna("").astype(str)
        + "|"
        + result["side"].fillna("").astype(str)
        + "|"
        + result["signal_type_1h"].fillna("").astype(str)
        + "|"
        + result["vwap_state"].fillna("").astype(str)
    )
    return result.sort_values(["match_key", "entry_time"]).reset_index(drop=True)


def _match_to_pure_entries(ledger_df: pd.DataFrame, pure_df: pd.DataFrame, tolerance_minutes: int) -> pd.DataFrame:
    if ledger_df.empty:
        return ledger_df.copy()
    if pure_df.empty:
        unmatched = ledger_df.copy()
        unmatched["pure_pnl"] = pd.NA
        unmatched["pure_win"] = pd.NA
        unmatched["pure_hold_minutes"] = pd.NA
        unmatched["matched_entry_time"] = pd.NaT
        unmatched["match_delta_minutes"] = pd.NA
        unmatched["match_tolerance_minutes"] = tolerance_minutes
        unmatched["matched"] = False
        return unmatched

    left = ledger_df.sort_values(["match_key", "timestamp"]).reset_index(drop=True)
    right = pure_df.sort_values(["match_key", "entry_time"]).reset_index(drop=True)
    chunks: List[pd.DataFrame] = []
    tolerance = pd.Timedelta(minutes=tolerance_minutes)
    for match_key, left_group in left.groupby("match_key", sort=False):
        right_group = right.loc[right["match_key"] == match_key]
        if right_group.empty:
            unmatched = left_group.copy()
            unmatched["pure_pnl"] = pd.NA
            unmatched["pure_win"] = pd.NA
            unmatched["pure_hold_minutes"] = pd.NA
            unmatched["entry_time"] = pd.NaT
            chunks.append(unmatched)
            continue
        merged_group = pd.merge_asof(
            left_group.sort_values("timestamp"),
            right_group.sort_values("entry_time"),
            left_on="timestamp",
            right_on="entry_time",
            direction="nearest",
            tolerance=tolerance,
            suffixes=("", "_pure"),
        )
        chunks.append(merged_group)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="The behavior of DataFrame concatenation with empty or all-NA entries is deprecated.",
            category=FutureWarning,
        )
        merged = pd.concat(chunks, ignore_index=True)
    merged["matched"] = merged["pure_pnl"].notna()
    merged["matched_entry_time"] = pd.to_datetime(merged["entry_time"], errors="coerce", utc=True)
    merged["match_delta_minutes"] = pd.NA
    matched_mask = merged["matched"] & merged["matched_entry_time"].notna()
    if matched_mask.any():
        delta = (
            merged.loc[matched_mask, "timestamp"] - merged.loc[matched_mask, "matched_entry_time"]
        ).abs()
        merged.loc[matched_mask, "match_delta_minutes"] = delta.dt.total_seconds().div(60.0)
    merged["match_tolerance_minutes"] = tolerance_minutes
    return merged


def build_report(ledger_df: pd.DataFrame, pure_df: pd.DataFrame, tolerance_minutes: int) -> Dict[str, object]:
    merged = _match_to_pure_entries(ledger_df, pure_df, tolerance_minutes=tolerance_minutes)

    def summarize(mask: pd.Series, label: str) -> Dict[str, object]:
        group = merged.loc[mask].copy()
        matched = group[group["matched"]].copy()
        return {
            "label": label,
            "candidate_count": int(len(group)),
            "matched_pure_count": int(len(matched)),
            "match_rate_pct": round(float((len(matched) / len(group) * 100.0) if len(group) else 0.0), 4),
            "pure_win_rate_pct": round(float(matched["pure_win"].mean() * 100.0), 4) if len(matched) else 0.0,
            "pure_total_pnl": round(float(matched["pure_pnl"].sum()), 4) if len(matched) else 0.0,
            "pure_avg_pnl": round(float(matched["pure_pnl"].mean()), 4) if len(matched) else 0.0,
            "avg_match_delta_minutes": round(float(matched["match_delta_minutes"].mean()), 4) if len(matched) else None,
        }

    groups = {
        "all_candidates": summarize(merged["generated_open_candidate"] == True, "all_candidates"),
        "prefilter_passed": summarize(merged["pre_filter_passed"] == True, "prefilter_passed"),
        "prefilter_blocked": summarize(merged["pre_filter_passed"] == False, "prefilter_blocked"),
        "shortlisted": summarize(merged["ai_shortlisted"] == True, "shortlisted"),
        "skipped_topn": summarize(merged["ai_block_reason"] == "ai_shortlist_topn", "skipped_topn"),
        "ai_allowed": summarize(merged["ai_allowed"] == True, "ai_allowed"),
        "ai_blocked": summarize((merged["ai_reviewed"] == True) & (merged["ai_allowed"] == False), "ai_blocked"),
        "capacity_selected": summarize(merged["capacity_selected"] == True, "capacity_selected"),
        "capacity_blocked": summarize((merged["ai_allowed"] == True) & (merged["capacity_selected"] == False), "capacity_blocked"),
        "final_opened": summarize(merged["final_opened"] == True, "final_opened"),
        "final_rejected": summarize((merged["capacity_selected"] == True) & (merged["final_opened"] == False), "final_rejected"),
    }

    shortlist_breakdown = (
        merged.loc[merged["generated_open_candidate"] == True, [
            "symbol",
            "timestamp",
            "signal_type_1h",
            "vwap_state",
            "signal_score",
            "vwap_score",
            "pre_filter_passed",
            "ai_shortlisted",
            "ai_allowed",
            "capacity_selected",
            "final_opened",
            "ai_block_reason",
            "capacity_block_reason",
            "final_reject_reason",
            "matched",
            "pure_pnl",
            "match_delta_minutes",
        ]]
        .sort_values(["timestamp", "symbol"])
        .head(40)
        .assign(timestamp=lambda x: x["timestamp"].astype(str))
        .to_dict(orient="records")
    )

    return {
        "match_tolerance_minutes": tolerance_minutes,
        "groups": groups,
        "sample_rows": shortlist_breakdown,
    }


def write_md(path: Path, report: Dict[str, object]) -> None:
    lines = [
        "# AI Shortlist Effectiveness",
        "",
        f"- match_tolerance_minutes: `{report.get('match_tolerance_minutes')}`",
        "",
        "| group | candidates | matched_pure | match_rate | pure_WR | pure_total_pnl | pure_avg_pnl | avg_match_delta_min |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, row in report.get("groups", {}).items():
        lines.append(
            f"| {key} | {row['candidate_count']} | {row['matched_pure_count']} | {row['match_rate_pct']:.2f}% | "
            f"{row['pure_win_rate_pct']:.2f}% | {row['pure_total_pnl']:+.2f} | {row['pure_avg_pnl']:+.2f} | "
            f"{row['avg_match_delta_minutes'] if row['avg_match_delta_minutes'] is not None else '-'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze AI shortlist effectiveness against pure-strategy trades using the full candidate ledger.")
    parser.add_argument("--candidate-ledger", required=True)
    parser.add_argument("--pure-trades", required=True)
    parser.add_argument("--match-tolerance-minutes", type=int, default=60)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    ledger_df = _prepare_candidate_ledger(_load_csv(Path(args.candidate_ledger).resolve()))
    pure_df = _aggregate_pure_entries(_load_csv(Path(args.pure_trades).resolve()))
    report = build_report(ledger_df, pure_df, tolerance_minutes=int(args.match_tolerance_minutes))
    Path(args.output_json).resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(Path(args.output_md).resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
