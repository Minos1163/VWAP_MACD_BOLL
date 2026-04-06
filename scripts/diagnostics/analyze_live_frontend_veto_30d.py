from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from scripts.diagnostics.analyze_live_vwap_block_followthrough import (
        _calc_followthrough,
        _iter_attribution_events,
        _iter_runtime_events,
        _load_latest_cache,
    )
except ModuleNotFoundError:
    from scripts.diagnostics.analyze_live_vwap_block_followthrough import (  # type: ignore
        _calc_followthrough,
        _iter_attribution_events,
        _iter_runtime_events,
        _load_latest_cache,
    )


VWAP_BANDS = [
    ("lt_0.08", 0.0, 0.08),
    ("0.08_0.10", 0.08, 0.10),
    ("0.10_0.12", 0.10, 0.12),
    ("ge_0.12", 0.12, float("inf")),
]


def _collect_event_frame(logs_root: Path, cache_dir: Path) -> pd.DataFrame:
    deduped: Dict[str, dict] = {}
    for event in _iter_runtime_events(logs_root):
        key = f"{event['symbol']}|{event['ts']}|{event['category']}|{event['direction']}"
        deduped[key] = event
    for event in _iter_attribution_events(logs_root):
        key = f"{event['symbol']}|{event['ts']}|{event['category']}|{event['direction']}"
        deduped[key] = event

    rows: List[Dict[str, object]] = []
    cache_map: Dict[str, Optional[pd.DataFrame]] = {}
    for event in deduped.values():
        symbol = str(event.get("symbol") or "")
        if not symbol:
            continue
        if symbol not in cache_map:
            cache_map[symbol] = _load_latest_cache(cache_dir, symbol)
        bars = cache_map[symbol]
        if bars is None or bars.empty:
            continue
        ts = pd.to_datetime(event.get("ts"), utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        price = float(event.get("price") or 0.0)
        if price <= 0.0:
            continue
        record = dict(event)
        record["ts"] = ts
        record["day_utc"] = ts.strftime("%Y-%m-%d")
        for hours in (1, 2, 4):
            stats = _calc_followthrough(bars, ts, price, str(event.get("direction") or ""), hours)
            if stats is None:
                continue
            for key, value in stats.items():
                record[f"{key}_{hours}h"] = value
        rows.append(record)
    return pd.DataFrame(rows)


def _daily_distribution(df: pd.DataFrame) -> List[Dict[str, object]]:
    if df.empty:
        return []
    records: List[Dict[str, object]] = []
    for day, day_df in df.groupby("day_utc", dropna=False):
        total = int(len(day_df))
        for category, category_df in day_df.groupby("category", dropna=False):
            count = int(len(category_df))
            records.append(
                {
                    "day_utc": str(day),
                    "category": str(category),
                    "count": count,
                    "pct_of_day": round(count * 100.0 / total, 4) if total else 0.0,
                }
            )
    return sorted(records, key=lambda x: (x["day_utc"], -x["count"], x["category"]))


def _overall_frontend_summary(df: pd.DataFrame) -> List[Dict[str, object]]:
    if df.empty:
        return []
    rows: List[Dict[str, object]] = []
    total = int(len(df))
    for (category, direction), group in df.groupby(["category", "direction"], dropna=False):
        row: Dict[str, object] = {
            "category": str(category),
            "direction": str(direction),
            "count": int(len(group)),
            "pct_of_all": round(len(group) * 100.0 / total, 4) if total else 0.0,
        }
        for hours in (2, 4):
            col = f"close_ret_pct_{hours}h"
            series = pd.to_numeric(group.get(col), errors="coerce")
            valid = series.dropna()
            if not valid.empty:
                row[f"avg_close_ret_{hours}h_pct"] = round(float(valid.mean() * 100.0), 4)
                row[f"median_close_ret_{hours}h_pct"] = round(float(valid.median() * 100.0), 4)
                row[f"followthrough_rate_{hours}h_pct"] = round(float((valid > 0).mean() * 100.0), 4)
        rows.append(row)
    return sorted(rows, key=lambda x: (-int(x["count"]), str(x["category"]), str(x["direction"])))


def _vwap_long_band_report(df: pd.DataFrame) -> List[Dict[str, object]]:
    if df.empty:
        return []
    target = df.loc[(df["category"].isin(["vwap_hard_block", "vwap_score_filter"])) & (df["direction"] == "long")].copy()
    if target.empty:
        return []
    scores = pd.to_numeric(target.get("vwap_score"), errors="coerce")
    target = target.assign(vwap_score_num=scores)
    rows: List[Dict[str, object]] = []
    for band_name, low, high in VWAP_BANDS:
        if high == float("inf"):
            subset = target.loc[target["vwap_score_num"] >= low].copy()
        else:
            subset = target.loc[(target["vwap_score_num"] >= low) & (target["vwap_score_num"] < high)].copy()
        if subset.empty:
            continue
        row: Dict[str, object] = {
            "band": band_name,
            "low": low,
            "high": None if high == float("inf") else high,
            "count": int(len(subset)),
            "avg_vwap_score": round(float(subset["vwap_score_num"].mean()), 4),
            "categories": subset["category"].value_counts().to_dict(),
        }
        for hours in (2, 4):
            col = f"close_ret_pct_{hours}h"
            series = pd.to_numeric(subset.get(col), errors="coerce")
            valid = series.dropna()
            if not valid.empty:
                row[f"avg_close_ret_{hours}h_pct"] = round(float(valid.mean() * 100.0), 4)
                row[f"median_close_ret_{hours}h_pct"] = round(float(valid.median() * 100.0), 4)
                row[f"followthrough_rate_{hours}h_pct"] = round(float((valid > 0).mean() * 100.0), 4)
        rows.append(row)
    return rows


def build_report(logs_root: Path, cache_dir: Path) -> Dict[str, object]:
    df = _collect_event_frame(logs_root, cache_dir)
    if df.empty:
        return {"count": 0, "note": "no usable live frontend veto samples after cache alignment"}

    available_days = sorted(df["day_utc"].dropna().astype(str).unique().tolist())
    return {
        "count": int(len(df)),
        "available_days": available_days,
        "available_day_count": int(len(available_days)),
        "coverage_note": "Available log coverage is limited to present folders under logs/2026-03 and logs/2026-04; this is not guaranteed to be a literal full 30-calendar-day sample.",
        "daily_distribution": _daily_distribution(df),
        "overall_frontend_summary": _overall_frontend_summary(df),
        "vwap_long_band_report": _vwap_long_band_report(df),
    }


def write_md(path: Path, report: Dict[str, object]) -> None:
    lines = [
        "# Live Frontend Veto 30D Audit",
        "",
        f"- samples: `{report.get('count', 0)}`",
        f"- available_day_count: `{report.get('available_day_count', 0)}`",
        f"- available_days: `{', '.join(report.get('available_days', []))}`",
        f"- note: `{report.get('coverage_note', '')}`",
        "",
        "## Overall",
        "",
        "| category | direction | count | pct_of_all | avg_close_2h | median_close_2h | follow_2h | avg_close_4h | median_close_4h | follow_4h |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("overall_frontend_summary", []):
        lines.append(
            f"| {row['category']} | {row['direction']} | {row['count']} | {row['pct_of_all']:.2f}% | "
            f"{row.get('avg_close_ret_2h_pct', 0.0):+.4f}% | {row.get('median_close_ret_2h_pct', 0.0):+.4f}% | "
            f"{row.get('followthrough_rate_2h_pct', 0.0):.2f}% | {row.get('avg_close_ret_4h_pct', 0.0):+.4f}% | "
            f"{row.get('median_close_ret_4h_pct', 0.0):+.4f}% | {row.get('followthrough_rate_4h_pct', 0.0):.2f}% |"
        )
    lines.extend(
        [
            "",
            "## VWAP Long Bands",
            "",
            "| band | count | avg_vwap_score | avg_close_2h | median_close_2h | follow_2h | avg_close_4h | median_close_4h | follow_4h | categories |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in report.get("vwap_long_band_report", []):
        lines.append(
            f"| {row['band']} | {row['count']} | {row['avg_vwap_score']:.4f} | "
            f"{row.get('avg_close_ret_2h_pct', 0.0):+.4f}% | {row.get('median_close_ret_2h_pct', 0.0):+.4f}% | "
            f"{row.get('followthrough_rate_2h_pct', 0.0):.2f}% | {row.get('avg_close_ret_4h_pct', 0.0):+.4f}% | "
            f"{row.get('median_close_ret_4h_pct', 0.0):+.4f}% | {row.get('followthrough_rate_4h_pct', 0.0):.2f}% | "
            f"`{row.get('categories', {})}` |"
        )
    lines.extend(
        [
            "",
            "## Daily Distribution",
            "",
            "| day_utc | category | count | pct_of_day |",
            "|---|---|---:|---:|",
        ]
    )
    for row in report.get("daily_distribution", []):
        lines.append(f"| {row['day_utc']} | {row['category']} | {row['count']} | {row['pct_of_day']:.2f}% |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit 30d live frontend veto distribution and posterior by category.")
    parser.add_argument("--logs-root", default="logs")
    parser.add_argument("--cache-dir", default="data/backtest_cache")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    report = build_report(Path(args.logs_root).resolve(), Path(args.cache_dir).resolve())
    Path(args.output_json).resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(Path(args.output_md).resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

