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

from src.utils.passive_fill import classify_passive_limit_fill, direct_ioc_fill_is_valid


def _load_trades(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    raise ValueError(f"unsupported file type: {path.suffix}")


def _resolve_latest_cache_file(cache_dir: Path, symbol: str, timeframe: str = "15m") -> Path:
    matches = sorted(cache_dir.glob(f"{symbol}_{timeframe}_*.parquet"))
    if not matches:
        raise FileNotFoundError(f"missing cache for {symbol} {timeframe}")
    return matches[-1]


def _load_symbol_bars(cache_dir: Path, symbol: str) -> pd.DataFrame:
    path = _resolve_latest_cache_file(cache_dir, symbol, timeframe="15m")
    bars = pd.read_parquet(path)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], errors="coerce", utc=True)
    return bars.sort_values("timestamp").reset_index(drop=True)


def _aggregate_logical_entries(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    for col in ("entry_time", "exit_time"):
        working[col] = pd.to_datetime(working[col], errors="coerce", utc=True)
    working["pnl"] = pd.to_numeric(working.get("pnl", 0.0), errors="coerce").fillna(0.0)
    grouping = ["symbol", "side", "entry_time", "entry_price", "signal_type_1h", "vwap_state"]

    rows: List[Dict[str, object]] = []
    for keys, group in working.groupby(grouping, dropna=False):
        symbol, side, entry_time, entry_price, signal_type_1h, vwap_state = keys
        reasons = [str(x or "") for x in group.get("reason", pd.Series(dtype=str)).tolist() if str(x or "")]
        signal_score_series = (
            pd.to_numeric(group["signal_score"], errors="coerce").fillna(0.0)
            if "signal_score" in group.columns
            else pd.Series([0.0])
        )
        vwap_score_series = (
            pd.to_numeric(group["vwap_score"], errors="coerce").fillna(0.0)
            if "vwap_score" in group.columns
            else pd.Series([0.0])
        )
        adx_series = (
            pd.to_numeric(group["adx_1h"], errors="coerce").fillna(0.0)
            if "adx_1h" in group.columns
            else pd.Series([0.0])
        )
        entry_scale_series = (
            pd.to_numeric(group["entry_scale"], errors="coerce").fillna(0.0)
            if "entry_scale" in group.columns
            else pd.Series([0.0])
        )
        is_trial_series = group["is_trial_entry"] if "is_trial_entry" in group.columns else pd.Series(["False"])
        rows.append(
            {
                "symbol": symbol,
                "side": side,
                "entry_time": entry_time,
                "exit_time": group["exit_time"].max(),
                "entry_price": float(entry_price),
                "signal_type_1h": signal_type_1h,
                "vwap_state": vwap_state,
                "legs": int(len(group)),
                "pnl": float(group["pnl"].sum()),
                "signal_score": float(signal_score_series.iloc[0]),
                "vwap_score": float(vwap_score_series.iloc[0]),
                "adx_1h": float(adx_series.iloc[0]),
                "entry_scale": float(entry_scale_series.iloc[0]),
                "is_trial_entry": bool(str(is_trial_series.iloc[0]).lower() == "true"),
                "reasons": reasons,
                "has_time_exit": any("time_exit" in x for x in reasons),
                "has_stop_loss_intrabar": any("stop_loss_intrabar" in x for x in reasons),
                "has_take_profit": any("take_profit" in x for x in reasons),
                "has_signal_reverse": any("signal_reverse" in x for x in reasons),
            }
        )
    result = pd.DataFrame(rows)
    if not result.empty:
        result["win"] = result["pnl"] > 0
        result["hold_minutes"] = (result["exit_time"] - result["entry_time"]).dt.total_seconds() / 60.0
    return result


def _bucket_summary(series: pd.Series, bins: list[float], labels: list[str]) -> Dict[str, Dict[str, float]]:
    if series.empty:
        return {}
    cats = pd.cut(series, bins=bins, labels=labels, include_lowest=True, right=False)
    out: Dict[str, Dict[str, float]] = {}
    for key, group_idx in pd.Series(range(len(series))).groupby(cats):
        idx = list(group_idx.values)
        if not idx:
            continue
        out[str(key)] = {"count": len(idx)}
    return out


def build_report(df: pd.DataFrame) -> Dict[str, object]:
    if df.empty:
        return {"count": 0, "note": "no matching logical entries"}

    exit_reason_counts = {
        "time_exit": int(df["has_time_exit"].sum()),
        "stop_loss_intrabar": int(df["has_stop_loss_intrabar"].sum()),
        "take_profit": int(df["has_take_profit"].sum()),
        "signal_reverse": int(df["has_signal_reverse"].sum()),
    }

    by_symbol = []
    for symbol, group in df.groupby("symbol", dropna=False):
        by_symbol.append(
            {
                "symbol": str(symbol),
                "count": int(len(group)),
                "pnl": round(float(group["pnl"].sum()), 4),
                "win_rate_pct": round(float(group["win"].mean() * 100.0), 4),
                "time_exit_rate_pct": round(float(group["has_time_exit"].mean() * 100.0), 4),
                "stop_loss_rate_pct": round(float(group["has_stop_loss_intrabar"].mean() * 100.0), 4),
                "avg_signal_score": round(float(group["signal_score"].mean()), 4),
                "avg_vwap_score": round(float(group["vwap_score"].mean()), 4),
                "avg_adx_1h": round(float(group["adx_1h"].mean()), 4),
                "avg_hold_minutes": round(float(group["hold_minutes"].mean()), 2),
            }
        )
    by_symbol.sort(key=lambda row: (row["pnl"], row["count"]))

    return {
        "count": int(len(df)),
        "pnl": round(float(df["pnl"].sum()), 4),
        "win_rate_pct": round(float(df["win"].mean() * 100.0), 4),
        "avg_signal_score": round(float(df["signal_score"].mean()), 4),
        "avg_vwap_score": round(float(df["vwap_score"].mean()), 4),
        "avg_adx_1h": round(float(df["adx_1h"].mean()), 4),
        "avg_hold_minutes": round(float(df["hold_minutes"].mean()), 2),
        "time_exit_rate_pct": round(float(df["has_time_exit"].mean() * 100.0), 4),
        "stop_loss_intrabar_rate_pct": round(float(df["has_stop_loss_intrabar"].mean() * 100.0), 4),
        "take_profit_rate_pct": round(float(df["has_take_profit"].mean() * 100.0), 4),
        "exit_reason_counts": exit_reason_counts,
        "by_symbol": by_symbol,
        "top_worst_entries": (
            df.sort_values("pnl", ascending=True)[
                ["symbol", "entry_time", "pnl", "signal_score", "vwap_score", "adx_1h", "reasons"]
            ]
            .head(10)
            .assign(entry_time=lambda x: x["entry_time"].astype(str))
            .to_dict(orient="records")
        ),
    }


def build_fill_composition(
    df: pd.DataFrame,
    *,
    cache_dir: Path,
    strict_penetration_bps: float = 5.0,
) -> Dict[str, object]:
    if df.empty:
        return {"entry_count": 0, "note": "no matching logical entries"}

    cache: Dict[str, pd.DataFrame] = {}
    rows: List[Dict[str, object]] = []
    for _, row in df.iterrows():
        symbol = str(row.get("symbol", ""))
        if not symbol:
            continue
        if symbol not in cache:
            cache[symbol] = _load_symbol_bars(cache_dir, symbol)
        bars = cache[symbol]
        entry_time = pd.to_datetime(row.get("entry_time"), errors="coerce", utc=True)
        match = bars.loc[bars["timestamp"] == entry_time]
        if match.empty:
            continue
        bar = match.iloc[0]
        classification = classify_passive_limit_fill(
            side=str(row.get("side", "")).lower(),
            limit_price=float(row.get("entry_price", 0.0) or 0.0),
            open_price=float(bar["open"]),
            high_price=float(bar["high"]),
            low_price=float(bar["low"]),
            close_price=float(bar["close"]),
        )
        strict_valid, strict_reason = direct_ioc_fill_is_valid(
            classification,
            mode="close_through_or_penetration",
            min_penetration_bps=float(strict_penetration_bps),
        )
        rows.append(
            {
                "symbol": symbol,
                "touch_class": str(classification["touch_class"]),
                "close_through": bool(classification["close_through"]),
                "wick_only_touch": bool(classification["wick_only_touch"]),
                "penetration_bps": float(classification["penetration_bps"]),
                "strict_fill_valid": bool(strict_valid),
                "strict_fill_reason": strict_reason,
            }
        )

    detailed = pd.DataFrame(rows)
    if detailed.empty:
        return {"entry_count": 0, "note": "matching trades found but no entry bars in cache"}

    by_symbol = []
    for symbol, group in detailed.groupby("symbol", sort=False):
        by_symbol.append(
            {
                "symbol": str(symbol),
                "entry_count": int(len(group)),
                "wick_only_touch_rate_pct": float(group["wick_only_touch"].mean() * 100.0),
                "close_through_rate_pct": float(group["close_through"].mean() * 100.0),
                "strict_fill_valid_count": int(group["strict_fill_valid"].sum()),
                "avg_penetration_bps": float(group["penetration_bps"].mean()),
            }
        )

    by_symbol.sort(key=lambda item: (-item["entry_count"], item["symbol"]))
    return {
        "entry_count": int(len(detailed)),
        "wick_only_touch_rate_pct": float(detailed["wick_only_touch"].mean() * 100.0),
        "close_through_rate_pct": float(detailed["close_through"].mean() * 100.0),
        "strict_fill_valid_count": int(detailed["strict_fill_valid"].sum()),
        "avg_penetration_bps": float(detailed["penetration_bps"].mean()),
        "by_symbol": by_symbol,
    }


def write_md(path: Path, report: Dict[str, object], signal_type: str, vwap_state: str, symbol: str | None) -> None:
    lines = [
        "# Reclaim Pocket Mechanism Review",
        "",
        f"- signal_type: `{signal_type}`",
        f"- vwap_state: `{vwap_state}`",
        f"- symbol_filter: `{symbol or 'ALL'}`",
        "",
        "## Summary",
        "",
        f"- logical_entries: `{report.get('count', 0)}`",
        f"- pnl: `{report.get('pnl', 0.0):+.2f}`",
        f"- win_rate_pct: `{report.get('win_rate_pct', 0.0):.2f}%`",
        f"- avg_signal_score: `{report.get('avg_signal_score', 0.0):.4f}`",
        f"- avg_vwap_score: `{report.get('avg_vwap_score', 0.0):.4f}`",
        f"- avg_adx_1h: `{report.get('avg_adx_1h', 0.0):.2f}`",
        f"- time_exit_rate_pct: `{report.get('time_exit_rate_pct', 0.0):.2f}%`",
        f"- stop_loss_intrabar_rate_pct: `{report.get('stop_loss_intrabar_rate_pct', 0.0):.2f}%`",
        "",
        "## By Symbol",
        "",
        "| symbol | count | pnl | WR | time_exit | stop_loss | avg_signal | avg_vwap | avg_adx |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("by_symbol", []):
        lines.append(
            f"| {row['symbol']} | {row['count']} | {row['pnl']:+.2f} | {row['win_rate_pct']:.2f}% | "
            f"{row['time_exit_rate_pct']:.2f}% | {row['stop_loss_rate_pct']:.2f}% | {row['avg_signal_score']:.4f} | "
            f"{row['avg_vwap_score']:.4f} | {row['avg_adx_1h']:.2f} |"
        )
    fill_report = report.get("fill_composition")
    if isinstance(fill_report, dict) and int(fill_report.get("entry_count", 0)) > 0:
        lines.extend(
            [
                "",
                "## Fill Composition",
                "",
                f"- entry_count: `{fill_report.get('entry_count', 0)}`",
                f"- wick_only_touch_rate_pct: `{float(fill_report.get('wick_only_touch_rate_pct', 0.0)):.2f}%`",
                f"- close_through_rate_pct: `{float(fill_report.get('close_through_rate_pct', 0.0)):.2f}%`",
                f"- strict_fill_valid_count: `{int(fill_report.get('strict_fill_valid_count', 0))}`",
                f"- avg_penetration_bps: `{float(fill_report.get('avg_penetration_bps', 0.0)):.2f}`",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze pure-strategy reclaim pocket mechanism.")
    parser.add_argument("--trades", required=True)
    parser.add_argument("--signal-type", default="red_bar_growing")
    parser.add_argument("--vwap-state", default="long_reclaim_confirmed")
    parser.add_argument("--symbol", default="")
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--strict-penetration-bps", type=float, default=5.0)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    trades = _load_trades(Path(args.trades).resolve())
    logical = _aggregate_logical_entries(trades)
    signal_type_filter = str(args.signal_type or "").strip()
    match_all_signal_types = signal_type_filter.lower() in {"", "all", "*"}
    filtered = logical[logical["vwap_state"].astype(str) == str(args.vwap_state)].copy()
    if not match_all_signal_types:
        filtered = filtered[filtered["signal_type_1h"].astype(str) == signal_type_filter].copy()
    if args.symbol:
        filtered = filtered[filtered["symbol"].astype(str).str.upper() == str(args.symbol).upper()]

    report = build_report(filtered)
    if args.cache_dir:
        report["fill_composition"] = build_fill_composition(
            filtered,
            cache_dir=Path(args.cache_dir).resolve(),
            strict_penetration_bps=float(args.strict_penetration_bps),
        )
    Path(args.output_json).resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(
        Path(args.output_md).resolve(),
        report,
        signal_type_filter if not match_all_signal_types else "ALL",
        str(args.vwap_state),
        str(args.symbol or ""),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

