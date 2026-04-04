from __future__ import annotations

import argparse
import gzip
import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

HEADER_RE = re.compile(r"^=== FUND_FLOW cycle \d+ @ ([0-9:\- ]+) UTC ===")
DECISION_LINE_RE = re.compile(r"^\[([A-Z0-9]+USDT)\] 决策=([A-Z]+)")
PRICE_LINE_RE = re.compile(r"close=([0-9.]+)")
VWAP_LINE_RE = re.compile(r"VWAP=([0-9.]+)")
HOLD_ATTR_RE = re.compile(
    r"code=([^,]+), detail=([^,]+), signal_1h=([^,]+), entry_15m=([^,]+), veto=([^,]+), lock=([^,\s]+)"
)


def _normalize_ts(text: str) -> pd.Timestamp:
    return pd.to_datetime(text, errors="coerce", utc=True)


def _category_from_text(decision_reason: str, hold_code: str, hold_reason: str) -> str:
    reason_text = str(decision_reason or "")
    code_text = str(hold_code or "")
    hold_text = str(hold_reason or "")
    if "vwap_score_filter" in reason_text:
        return "vwap_score_filter"
    if "vwap_hard_block" in reason_text:
        return "vwap_hard_block"
    if "volume_vwap_both_low" in code_text or "volume_vwap_both_low" in hold_text:
        return "volume_vwap_both_low"
    if "4H预翻转缩短不足" in code_text or "4H预翻转缩短不足" in hold_text or "preflip" in hold_text:
        return "preflip_shrink"
    if "vwap_hard_block" in code_text or "vwap_hard_block" in hold_text:
        return "vwap_hard_block"
    return ""


def _direction_from_lock(lock: str) -> str:
    text = str(lock or "").upper()
    if text == "LONG_ONLY":
        return "long"
    if text == "SHORT_ONLY":
        return "short"
    return ""


def _iter_attribution_events(logs_root: Path) -> Iterable[dict]:
    for path in sorted(logs_root.rglob("fund_flow_attribution.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("event") != "decision":
                    continue
                decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
                context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
                metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
                reason = str(decision.get("reason") or "")
                category = _category_from_text(reason, "", "")
                direction = _direction_from_lock(metadata.get("direction_lock"))
                symbol = str(context.get("symbol") or decision.get("symbol") or "").upper()
                ts = _normalize_ts(str(payload.get("ts") or ""))
                price = float(context.get("price") or 0.0)
                if not symbol or not category or not direction or pd.isna(ts) or price <= 0:
                    continue
                yield {
                    "source": "attribution",
                    "symbol": symbol,
                    "ts": ts,
                    "category": category,
                    "direction": direction,
                    "price": price,
                    "signal_type_1h": "",
                    "entry_15m": "",
                    "vwap_score": None,
                    "detail": "",
                }


def _iter_runtime_events(logs_root: Path) -> Iterable[dict]:
    for path in sorted(logs_root.rglob("runtime.out*.log")):
        current_ts: Optional[pd.Timestamp] = None
        current: Optional[Dict[str, object]] = None

        def flush() -> Optional[dict]:
            if not current:
                return None
            category = _category_from_text(
                str(current.get("decision_reason") or ""),
                str(current.get("hold_code") or ""),
                str(current.get("hold_reason") or ""),
            )
            direction = _direction_from_lock(str(current.get("lock") or ""))
            price = float(current.get("price") or 0.0)
            ts = current.get("ts")
            if not category or not direction or not price or not isinstance(ts, pd.Timestamp) or pd.isna(ts):
                return None
            return {
                "source": "runtime",
                "symbol": str(current.get("symbol") or ""),
                "ts": ts,
                "category": category,
                "direction": direction,
                "price": price,
                "signal_type_1h": str(current.get("signal_type_1h") or ""),
                "entry_15m": str(current.get("entry_15m") or ""),
                "vwap_score": current.get("vwap_score"),
                "detail": str(current.get("detail") or ""),
            }

        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\n")
                header_match = HEADER_RE.match(line)
                if header_match:
                    flushed = flush()
                    if flushed:
                        yield flushed
                    current = None
                    current_ts = _normalize_ts(header_match.group(1) + "+00:00")
                    continue

                decision_match = DECISION_LINE_RE.match(line)
                if decision_match:
                    flushed = flush()
                    if flushed:
                        yield flushed
                    current = {
                        "symbol": decision_match.group(1),
                        "ts": current_ts,
                        "price": 0.0,
                        "decision_reason": "",
                        "hold_reason": "",
                        "hold_code": "",
                        "detail": "",
                        "signal_type_1h": "",
                        "entry_15m": "",
                        "lock": "",
                        "vwap_score": None,
                    }
                    continue

                if current is None:
                    continue

                if "K线价格(15m):" in line:
                    match = PRICE_LINE_RE.search(line)
                    if match:
                        current["price"] = float(match.group(1))
                    continue

                if "MACD_V2评分:" in line:
                    match = VWAP_LINE_RE.search(line)
                    if match:
                        current["vwap_score"] = float(match.group(1))
                    continue

                if "决策原因:" in line:
                    current["decision_reason"] = line.split("决策原因:", 1)[1].strip()
                    continue

                if "HOLD归因:" in line:
                    current["hold_reason"] = line.split("HOLD归因:", 1)[1].strip()
                    hold_match = HOLD_ATTR_RE.search(line)
                    if hold_match:
                        current["hold_code"] = hold_match.group(1).strip()
                        current["detail"] = hold_match.group(2).strip()
                        current["signal_type_1h"] = hold_match.group(3).strip()
                        current["entry_15m"] = hold_match.group(4).strip()
                        current["lock"] = hold_match.group(6).strip()
                    continue

        flushed = flush()
        if flushed:
            yield flushed


def _load_latest_cache(cache_dir: Path, symbol: str) -> Optional[pd.DataFrame]:
    files = sorted(cache_dir.glob(f"{symbol}_15m_*.parquet"))
    if not files:
        return None
    df = pd.read_parquet(files[-1])
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def _calc_followthrough(df: pd.DataFrame, ts: pd.Timestamp, price: float, direction: str, horizon_hours: int) -> Optional[Dict[str, float]]:
    end_ts = ts + timedelta(hours=horizon_hours)
    future = df[(df["timestamp"] > ts) & (df["timestamp"] <= end_ts)].copy()
    if future.empty:
        return None
    future_close = float(future.iloc[-1]["close"])
    max_high = float(future["high"].max())
    min_low = float(future["low"].min())
    if direction == "long":
        return {
            "close_ret_pct": (future_close - price) / price,
            "mfe_pct": (max_high - price) / price,
            "mae_pct": (min_low - price) / price,
        }
    if direction == "short":
        return {
            "close_ret_pct": (price - future_close) / price,
            "mfe_pct": (price - min_low) / price,
            "mae_pct": (price - max_high) / price,
        }
    return None


def build_report(logs_root: Path, cache_dir: Path) -> Dict[str, object]:
    deduped: Dict[str, dict] = {}
    for event in _iter_runtime_events(logs_root):
        key = f"{event['symbol']}|{event['ts']}|{event['category']}|{event['direction']}"
        deduped[key] = event
    for event in _iter_attribution_events(logs_root):
        key = f"{event['symbol']}|{event['ts']}|{event['category']}|{event['direction']}"
        deduped[key] = event

    cache_map: Dict[str, Optional[pd.DataFrame]] = {}
    rows: List[Dict[str, object]] = []
    for event in deduped.values():
        symbol = str(event["symbol"])
        if symbol not in cache_map:
            cache_map[symbol] = _load_latest_cache(cache_dir, symbol)
        df = cache_map[symbol]
        if df is None or df.empty:
            continue
        ts = event["ts"]
        price = float(event["price"])
        record = dict(event)
        for hours in (1, 2, 4):
            stats = _calc_followthrough(df, ts, price, str(event["direction"]), hours)
            if stats is None:
                continue
            for key, value in stats.items():
                record[f"{key}_{hours}h"] = value
        rows.append(record)

    df = pd.DataFrame(rows)
    if df.empty:
        return {"count": 0, "note": "no usable blocked samples after cache alignment"}

    groups = []
    for (category, direction), group in df.groupby(["category", "direction"], dropna=False):
        item = {"category": category, "direction": direction, "count": int(len(group))}
        for hours in (1, 2, 4):
            col = f"close_ret_pct_{hours}h"
            mfe = f"mfe_pct_{hours}h"
            if col in group:
                item[f"avg_close_ret_{hours}h_pct"] = round(float(group[col].mean() * 100.0), 4)
                item[f"avg_mfe_{hours}h_pct"] = round(float(group[mfe].mean() * 100.0), 4)
                item[f"followthrough_rate_{hours}h_pct"] = round(float((group[col] > 0).mean() * 100.0), 4)
        groups.append(item)
    groups.sort(key=lambda row: (row["category"], row["direction"]))

    source_counts = {
        source: int(count)
        for source, count in df["source"].value_counts(dropna=False).sort_index().items()
    }

    long_vwap_mask = df["category"].isin(["vwap_hard_block", "vwap_score_filter"]) & (df["direction"] == "long")
    long_vwap_breakdown: List[Dict[str, object]] = []
    long_candidates = df.loc[long_vwap_mask & df["signal_type_1h"].astype(str).ne("")]
    if not long_candidates.empty:
        grouped = (
            long_candidates.groupby(["signal_type_1h", "entry_15m"], dropna=False)
            .size()
            .reset_index(name="count")
            .sort_values("count", ascending=False)
            .head(12)
        )
        for _, row in grouped.iterrows():
            signal_type = row["signal_type_1h"]
            entry_15m = row["entry_15m"]
            subset = long_candidates[
                (long_candidates["signal_type_1h"] == signal_type)
                & (long_candidates["entry_15m"] == entry_15m)
            ]
            long_vwap_breakdown.append(
                {
                    "signal_type_1h": str(signal_type),
                    "entry_15m": str(entry_15m),
                    "count": int(len(subset)),
                    "avg_vwap_score": round(float(pd.to_numeric(subset["vwap_score"], errors="coerce").dropna().mean()), 4)
                    if subset["vwap_score"].notna().any()
                    else None,
                    "avg_close_ret_2h_pct": round(float(subset["close_ret_pct_2h"].mean() * 100.0), 4)
                    if "close_ret_pct_2h" in subset
                    else None,
                    "followthrough_rate_2h_pct": round(float((subset["close_ret_pct_2h"] > 0).mean() * 100.0), 4)
                    if "close_ret_pct_2h" in subset
                    else None,
                }
            )

    return {
        "count": int(len(df)),
        "source_counts": source_counts,
        "coverage_note": "Expanded from structured attribution logs plus parsed runtime logs; follow-through is limited by available 15m cache horizon.",
        "groups": groups,
        "long_vwap_breakdown": long_vwap_breakdown,
        "sample_rows": df.head(20).assign(ts=lambda x: x["ts"].astype(str)).to_dict(orient="records"),
    }


def write_md(path: Path, report: Dict[str, object]) -> None:
    lines = [
        "# Live VWAP Block Follow-Through",
        "",
        f"- samples: `{report.get('count', 0)}`",
        f"- sources: `{report.get('source_counts', {})}`",
        f"- note: `{report.get('coverage_note', '')}`",
        "",
        "| category | direction | count | avg_close_1h | avg_close_2h | avg_close_4h | follow_2h | avg_mfe_2h |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("groups", []):
        lines.append(
            f"| {row['category']} | {row['direction']} | {row['count']} | "
            f"{row.get('avg_close_ret_1h_pct', 0.0):+.2f}% | {row.get('avg_close_ret_2h_pct', 0.0):+.2f}% | "
            f"{row.get('avg_close_ret_4h_pct', 0.0):+.2f}% | {row.get('followthrough_rate_2h_pct', 0.0):.2f}% | "
            f"{row.get('avg_mfe_2h_pct', 0.0):+.2f}% |"
        )
    if report.get("long_vwap_breakdown"):
        lines.extend(
            [
                "",
                "## Long VWAP Breakdown",
                "",
                "| signal_type_1h | entry_15m | count | avg_vwap_score | avg_close_2h | follow_2h |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for row in report["long_vwap_breakdown"]:
            lines.append(
                f"| {row['signal_type_1h']} | {row['entry_15m']} | {row['count']} | "
                f"{row['avg_vwap_score'] if row['avg_vwap_score'] is not None else '-'} | "
                f"{row['avg_close_ret_2h_pct'] if row['avg_close_ret_2h_pct'] is not None else '-'} | "
                f"{row['followthrough_rate_2h_pct'] if row['followthrough_rate_2h_pct'] is not None else '-'} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze live VWAP/4H blocked decision follow-through from attribution and runtime logs.")
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
