"""
Analyze live same-bar stop-vs-TP priority evidence from exit protection audit logs.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def extract_same_bar_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("event_type") or "") != "same_bar_priority_evidence":
            continue
        if not bool(row.get("same_bar_priority_candidate", False)):
            continue
        records.append(row)
    return records


def _top_counter(counter: Counter[str], limit: int = 10) -> dict[str, int]:
    return {key: int(value) for key, value in counter.most_common(limit)}


def summarize_same_bar_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    symbol_counts: Counter[str] = Counter()
    inferred_counts: Counter[str] = Counter()
    evidence_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    tp_sl_pair_counts: Counter[str] = Counter()

    for record in records:
        symbol = str(record.get("symbol") or "UNKNOWN")
        inferred = str(record.get("inferred_trigger_source") or "unknown")
        evidence = str(record.get("evidence_strength") or "unknown")
        reason = str(record.get("decision_reason") or "")
        pre_snapshot = record.get("pre_snapshot") if isinstance(record.get("pre_snapshot"), dict) else {}
        pair_key = (
            f"tp={int(bool(pre_snapshot.get('has_tp')))}|"
            f"sl={int(bool(pre_snapshot.get('has_sl')))}|"
            f"orders={int(pre_snapshot.get('order_count', 0) or 0)}"
        )

        symbol_counts[symbol] += 1
        inferred_counts[inferred] += 1
        evidence_counts[evidence] += 1
        if reason:
            reason_counts[reason] += 1
        tp_sl_pair_counts[pair_key] += 1

    medium_or_better = sum(
        1
        for record in records
        if str(record.get("evidence_strength") or "").lower() in {"medium", "strong"}
    )
    return {
        "candidate_count": len(records),
        "medium_or_better_count": medium_or_better,
        "medium_or_better_ratio": (medium_or_better / len(records)) if records else 0.0,
        "symbol_counts": _top_counter(symbol_counts),
        "inferred_trigger_source_counts": _top_counter(inferred_counts),
        "evidence_strength_counts": _top_counter(evidence_counts),
        "decision_reason_counts": _top_counter(reason_counts),
        "pre_snapshot_shape_counts": _top_counter(tp_sl_pair_counts),
    }


def render_markdown_report(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# Same-Bar Stop vs TP Priority Evidence")
    lines.append("")
    lines.append(f"- Candidate count: `{summary['candidate_count']}`")
    lines.append(f"- Medium-or-better evidence count: `{summary['medium_or_better_count']}`")
    lines.append(f"- Medium-or-better ratio: `{summary['medium_or_better_ratio']:.2%}`")
    lines.append("")
    lines.append("## Inference Distribution")
    lines.append("")
    for key, value in summary["inferred_trigger_source_counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    lines.append("## Evidence Strength")
    lines.append("")
    for key, value in summary["evidence_strength_counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    lines.append("## Top Symbols")
    lines.append("")
    for key, value in summary["symbol_counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    lines.append("## Top Reasons")
    lines.append("")
    for key, value in summary["decision_reason_counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    lines.append("## Snapshot Shapes")
    lines.append("")
    for key, value in summary["pre_snapshot_shape_counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    lines.append("## Sample Records")
    lines.append("")
    for record in records[:10]:
        lines.append(
            f"- `{record.get('symbol', 'UNKNOWN')}` "
            f"`{record.get('inferred_trigger_source', 'unknown')}` "
            f"`{record.get('evidence_strength', 'unknown')}` "
            f"`{record.get('decision_reason', '')}`"
        )
    lines.append("")
    return "\n".join(lines)


def _default_audit_log() -> Path:
    root = Path("D:/AIDCA/AI8/logs")
    candidates = sorted(root.glob("**/exit_protection_audit_utc.jsonl"))
    if not candidates:
        return root / "exit_protection_audit_utc.jsonl"
    return candidates[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze same-bar stop vs TP priority evidence from live logs.")
    parser.add_argument(
        "--audit-log",
        default=str(_default_audit_log()),
        help="Path to exit_protection_audit_utc.jsonl",
    )
    parser.add_argument(
        "--report-json",
        default="",
        help="Optional path to write JSON summary.",
    )
    parser.add_argument(
        "--report-md",
        default="",
        help="Optional path to write Markdown report.",
    )
    args = parser.parse_args()

    audit_log = Path(args.audit_log)
    rows = load_jsonl(audit_log)
    records = extract_same_bar_records(rows)
    summary = summarize_same_bar_records(records)

    print(f"[same-bar] audit_log={audit_log}")
    print(f"[same-bar] candidates={summary['candidate_count']}")
    print(
        f"[same-bar] inferred={json.dumps(summary['inferred_trigger_source_counts'], ensure_ascii=False)}"
    )

    if args.report_json:
        json_path = Path(args.report_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(
                {
                    "audit_log": str(audit_log),
                    "summary": summary,
                    "sample_records": records[:50],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[same-bar] wrote_json={json_path}")

    if args.report_md:
        md_path = Path(args.report_md)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown_report(summary, records), encoding="utf-8")
        print(f"[same-bar] wrote_md={md_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

