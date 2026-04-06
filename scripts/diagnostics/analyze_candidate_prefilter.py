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
    from scripts.diagnostics.analyze_ai_shortlist_effectiveness import (
        _aggregate_pure_entries,
        _match_to_pure_entries,
        _prepare_candidate_ledger,
    )
except ModuleNotFoundError:
    from scripts.diagnostics.analyze_ai_shortlist_effectiveness import (  # type: ignore
        _aggregate_pure_entries,
        _match_to_pure_entries,
        _prepare_candidate_ledger,
    )


def _reason_family(reason: object) -> str:
    text = str(reason or "")
    if text.startswith("PRE_AI_SCORE:"):
        return "score_threshold"
    if text.startswith("PRE_AI_VWAP:"):
        return "vwap_threshold"
    if text.startswith("PRE_AI_REJECT_COMBO:"):
        return "reject_combo"
    if text.startswith("PRE_AI_CLUSTER_REPEAT:"):
        return "cluster_repeat"
    if text.startswith("PRE_AI_4H_BEAR:"):
        return "4h_bear_guard"
    if text.startswith("PRE_AI_TRIAL_SHRINK:"):
        return "trial_shrink"
    if text == "PRE_AI_PASS":
        return "pass"
    if text == "disabled":
        return "disabled"
    if not text:
        return "missing"
    return "other"


def _matched_unique_summary(group: pd.DataFrame) -> Dict[str, object]:
    matched = group[group["matched"] == True].copy()
    if matched.empty:
        return {
            "matched_candidate_count": 0,
            "matched_unique_entry_count": 0,
            "unique_pure_win_rate_pct": 0.0,
            "unique_pure_total_pnl": 0.0,
            "unique_pure_avg_pnl": 0.0,
        }
    matched["pure_entry_key"] = (
        matched["symbol"].fillna("").astype(str)
        + "|"
        + matched["side"].fillna("").astype(str)
        + "|"
        + matched["signal_type_1h"].fillna("").astype(str)
        + "|"
        + matched["vwap_state"].fillna("").astype(str)
        + "|"
        + matched["matched_entry_time"].astype(str)
    )
    unique_entries = matched.sort_values("timestamp").drop_duplicates("pure_entry_key", keep="first")
    return {
        "matched_candidate_count": int(len(matched)),
        "matched_unique_entry_count": int(len(unique_entries)),
        "unique_pure_win_rate_pct": round(float(unique_entries["pure_win"].mean() * 100.0), 4) if len(unique_entries) else 0.0,
        "unique_pure_total_pnl": round(float(unique_entries["pure_pnl"].sum()), 4) if len(unique_entries) else 0.0,
        "unique_pure_avg_pnl": round(float(unique_entries["pure_pnl"].mean()), 4) if len(unique_entries) else 0.0,
    }


def build_report(ledger_df: pd.DataFrame, pure_df: pd.DataFrame, tolerance_minutes: int) -> Dict[str, object]:
    merged = _match_to_pure_entries(ledger_df, pure_df, tolerance_minutes=tolerance_minutes)
    candidates = merged[merged["generated_open_candidate"] == True].copy()
    candidates["reason_family"] = candidates["pre_filter_reason"].map(_reason_family)

    passed = candidates[candidates["pre_filter_passed"] == True].copy()
    blocked = candidates[candidates["pre_filter_passed"] == False].copy()

    overall = {
        "candidate_count": int(len(candidates)),
        "passed_count": int(len(passed)),
        "blocked_count": int(len(blocked)),
        "pass_rate_pct": round(float((len(passed) / len(candidates) * 100.0) if len(candidates) else 0.0), 4),
    }
    overall.update(
        {
            "passed": _matched_unique_summary(passed),
            "blocked": _matched_unique_summary(blocked),
        }
    )

    by_family: List[Dict[str, object]] = []
    for family, group in blocked.groupby("reason_family", dropna=False):
        row = {
            "reason_family": str(family),
            "blocked_candidate_count": int(len(group)),
            "top_pre_filter_reasons": group["pre_filter_reason"].fillna("").value_counts().head(5).to_dict(),
        }
        row.update(_matched_unique_summary(group))
        by_family.append(row)
    by_family.sort(key=lambda item: (-int(item["blocked_candidate_count"]), str(item["reason_family"])))

    by_pocket: List[Dict[str, object]] = []
    pocket_grouped = (
        blocked.groupby(["signal_type_1h", "vwap_state", "reason_family"], dropna=False)
        .size()
        .reset_index(name="blocked_candidate_count")
        .sort_values("blocked_candidate_count", ascending=False)
    )
    for _, row in pocket_grouped.head(20).iterrows():
        signal_type = row["signal_type_1h"]
        vwap_state = row["vwap_state"]
        family = row["reason_family"]
        subset = blocked[
            (blocked["signal_type_1h"] == signal_type)
            & (blocked["vwap_state"] == vwap_state)
            & (blocked["reason_family"] == family)
        ]
        item = {
            "signal_type_1h": str(signal_type),
            "vwap_state": str(vwap_state),
            "reason_family": str(family),
            "blocked_candidate_count": int(len(subset)),
        }
        item.update(_matched_unique_summary(subset))
        by_pocket.append(item)

    return {
        "match_tolerance_minutes": tolerance_minutes,
        "overall": overall,
        "by_reason_family": by_family,
        "top_blocked_pockets": by_pocket,
    }


def write_md(path: Path, report: Dict[str, object]) -> None:
    overall = report["overall"]
    lines = [
        "# Candidate Pre-Filter Audit",
        "",
        f"- match_tolerance_minutes: `{report.get('match_tolerance_minutes')}`",
        f"- candidate_count: `{overall['candidate_count']}`",
        f"- pass_rate_pct: `{overall['pass_rate_pct']}`",
        "",
        "## Overall",
        "",
        f"- passed unique entries: `{overall['passed']['matched_unique_entry_count']}` / pnl `{overall['passed']['unique_pure_total_pnl']:+.2f}` / wr `{overall['passed']['unique_pure_win_rate_pct']:.2f}%`",
        f"- blocked unique entries: `{overall['blocked']['matched_unique_entry_count']}` / pnl `{overall['blocked']['unique_pure_total_pnl']:+.2f}` / wr `{overall['blocked']['unique_pure_win_rate_pct']:.2f}%`",
        "",
        "## By Reason Family",
        "",
        "| family | blocked_candidates | unique_entries | unique_wr | unique_total_pnl | unique_avg_pnl |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("by_reason_family", []):
        lines.append(
            f"| {row['reason_family']} | {row['blocked_candidate_count']} | {row['matched_unique_entry_count']} | "
            f"{row['unique_pure_win_rate_pct']:.2f}% | {row['unique_pure_total_pnl']:+.2f} | {row['unique_pure_avg_pnl']:+.2f} |"
        )
    lines.extend(
        [
            "",
            "## Top Blocked Pockets",
            "",
            "| signal_type_1h | vwap_state | family | blocked_candidates | unique_entries | unique_wr | unique_total_pnl |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in report.get("top_blocked_pockets", []):
        lines.append(
            f"| {row['signal_type_1h']} | {row['vwap_state']} | {row['reason_family']} | "
            f"{row['blocked_candidate_count']} | {row['matched_unique_entry_count']} | "
            f"{row['unique_pure_win_rate_pct']:.2f}% | {row['unique_pure_total_pnl']:+.2f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reverse-audit candidate_pre_filter against pure-strategy entries.")
    parser.add_argument("--candidate-ledger", required=True)
    parser.add_argument("--pure-trades", required=True)
    parser.add_argument("--match-tolerance-minutes", type=int, default=60)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    ledger_df = _prepare_candidate_ledger(pd.read_csv(Path(args.candidate_ledger).resolve(), low_memory=False))
    pure_df = _aggregate_pure_entries(pd.read_csv(Path(args.pure_trades).resolve(), low_memory=False))
    report = build_report(ledger_df, pure_df, tolerance_minutes=int(args.match_tolerance_minutes))
    Path(args.output_json).resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(Path(args.output_md).resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

