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


def _load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    for col in ("entry_time", "exit_time"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    return df


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


def _prepare_direct_ioc_entries(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame()
    working = trades_df.copy()
    direct_mask = (
        working["entry_initial_time_in_force"].astype(str).eq("IOC")
        & working["entry_time_in_force"].astype(str).eq("IOC")
        & working["entry_degradation_path"].fillna("").astype(str).isin(["", "[]"])
    )
    working = working.loc[direct_mask].copy()
    if working.empty:
        return pd.DataFrame()

    grouped = (
        working.groupby(["symbol", "side", "entry_time"], dropna=False, as_index=False)
        .agg(
            entry_price=("entry_price", "first"),
            total_pnl=("pnl", "sum"),
            win=("pnl", lambda s: float(pd.to_numeric(s, errors="coerce").sum()) > 0),
            exit_time=("exit_time", "max"),
            signal_type_1h=("signal_type_1h", "first"),
            vwap_state=("vwap_state", "first"),
            vwap_score=("vwap_score", "first"),
            leverage=("leverage", "first"),
        )
    )
    grouped["win"] = grouped["win"].astype(bool)
    return grouped.sort_values(["entry_time", "symbol"]).reset_index(drop=True)


def _classify_fill(row: pd.Series, bar: pd.Series) -> Dict[str, object]:
    classified = classify_passive_limit_fill(
        side=str(row.get("side", "")).lower(),
        limit_price=float(pd.to_numeric(row.get("entry_price"), errors="coerce")),
        open_price=float(pd.to_numeric(bar.get("open"), errors="coerce")),
        high_price=float(pd.to_numeric(bar.get("high"), errors="coerce")),
        low_price=float(pd.to_numeric(bar.get("low"), errors="coerce")),
        close_price=float(pd.to_numeric(bar.get("close"), errors="coerce")),
    )
    strict_valid, strict_reason = direct_ioc_fill_is_valid(
        classified,
        mode="close_through_or_penetration",
        min_penetration_bps=5.0,
    )
    classified["direct_ioc_fill_valid"] = strict_valid
    classified["direct_ioc_fill_reason"] = strict_reason
    return classified


def _summarize_subset(df: pd.DataFrame) -> Dict[str, object]:
    if df.empty:
        return {"count": 0, "win_rate_pct": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}
    pnl = pd.to_numeric(df["total_pnl"], errors="coerce").fillna(0.0)
    wins = int((pnl > 0).sum())
    count = int(len(df))
    return {
        "count": count,
        "win_rate_pct": (wins / count * 100.0) if count else 0.0,
        "total_pnl": float(pnl.sum()),
        "avg_pnl": float(pnl.mean()) if count else 0.0,
    }


def build_fill_sweetness_report(trades_df: pd.DataFrame, cache_dir: Path) -> Dict[str, object]:
    entries = _prepare_direct_ioc_entries(trades_df)
    if entries.empty:
        return {
            "entry_count": 0,
            "note": "no direct IOC fills found",
            "scenarios": {},
            "by_signal_type": {},
            "by_vwap_state": {},
            "samples": [],
        }

    cache: Dict[str, pd.DataFrame] = {}
    rows: List[Dict[str, object]] = []
    for _, row in entries.iterrows():
        symbol = str(row["symbol"])
        if symbol not in cache:
            cache[symbol] = _load_symbol_bars(cache_dir, symbol)
        bars = cache[symbol]
        entry_time = pd.to_datetime(row["entry_time"], errors="coerce", utc=True)
        match = bars.loc[bars["timestamp"] == entry_time]
        if match.empty:
            continue
        bar = match.iloc[0]
        classified = _classify_fill(row, bar)
        enriched = row.to_dict()
        enriched.update(
            {
                "bar_open": float(bar["open"]),
                "bar_high": float(bar["high"]),
                "bar_low": float(bar["low"]),
                "bar_close": float(bar["close"]),
                **classified,
            }
        )
        rows.append(enriched)

    detailed = pd.DataFrame(rows)
    if detailed.empty:
        return {
            "entry_count": 0,
            "note": "direct IOC fills found in trades but no matching entry bars in cache",
            "scenarios": {},
            "by_signal_type": {},
            "by_vwap_state": {},
            "samples": [],
        }

    scenarios = {
        "baseline_direct_fill": _summarize_subset(detailed),
        "wick_only_touch": _summarize_subset(detailed.loc[detailed["wick_only_touch"]]),
        "close_through": _summarize_subset(detailed.loc[detailed["close_through"]]),
        "penetration_ge_2bps": _summarize_subset(detailed.loc[detailed["penetration_bps"] >= 2.0]),
        "penetration_ge_5bps": _summarize_subset(detailed.loc[detailed["penetration_bps"] >= 5.0]),
        "penetration_ge_10bps": _summarize_subset(detailed.loc[detailed["penetration_bps"] >= 10.0]),
        "close_through_or_penetration_ge_5bps": _summarize_subset(
            detailed.loc[detailed["close_through"] | (detailed["penetration_bps"] >= 5.0)]
        ),
    }

    by_signal_type = {}
    for key, subset in detailed.groupby("signal_type_1h", dropna=False):
        by_signal_type[str(key)] = {
            **_summarize_subset(subset),
            "wick_only_rate_pct": float(subset["wick_only_touch"].mean() * 100.0),
            "close_through_rate_pct": float(subset["close_through"].mean() * 100.0),
            "avg_penetration_bps": float(pd.to_numeric(subset["penetration_bps"], errors="coerce").mean()),
        }

    by_vwap_state = {}
    for key, subset in detailed.groupby("vwap_state", dropna=False):
        by_vwap_state[str(key)] = {
            **_summarize_subset(subset),
            "wick_only_rate_pct": float(subset["wick_only_touch"].mean() * 100.0),
            "close_through_rate_pct": float(subset["close_through"].mean() * 100.0),
            "avg_penetration_bps": float(pd.to_numeric(subset["penetration_bps"], errors="coerce").mean()),
        }

    top_wick = (
        detailed.loc[detailed["wick_only_touch"]]
        .sort_values(["penetration_bps", "entry_time", "symbol"], ascending=[True, True, True])
        .head(15)
        .copy()
    )

    summary = {
        "entry_count": int(len(detailed)),
        "marketable_at_open_count": int(detailed["marketable_at_open"].sum()),
        "wick_only_touch_count": int(detailed["wick_only_touch"].sum()),
        "wick_only_touch_rate_pct": float(detailed["wick_only_touch"].mean() * 100.0),
        "close_through_count": int(detailed["close_through"].sum()),
        "close_through_rate_pct": float(detailed["close_through"].mean() * 100.0),
        "avg_penetration_bps": float(pd.to_numeric(detailed["penetration_bps"], errors="coerce").mean()),
        "median_penetration_bps": float(pd.to_numeric(detailed["penetration_bps"], errors="coerce").median()),
        "scenarios": scenarios,
        "by_signal_type": by_signal_type,
        "by_vwap_state": by_vwap_state,
        "samples": top_wick.assign(entry_time=lambda x: x["entry_time"].astype(str)).to_dict(orient="records"),
    }
    return summary


def _to_markdown(report: Dict[str, object]) -> str:
    lines = ["# IOC Direct-Fill Sweetness Audit", ""]
    lines.append(f"- entry_count: `{report.get('entry_count', 0)}`")
    lines.append(f"- marketable_at_open_count: `{report.get('marketable_at_open_count', 0)}`")
    lines.append(f"- wick_only_touch_rate_pct: `{report.get('wick_only_touch_rate_pct', 0.0):.2f}`")
    lines.append(f"- close_through_rate_pct: `{report.get('close_through_rate_pct', 0.0):.2f}`")
    lines.append(f"- avg_penetration_bps: `{report.get('avg_penetration_bps', 0.0):.2f}`")
    lines.append(f"- median_penetration_bps: `{report.get('median_penetration_bps', 0.0):.2f}`")
    lines.append("")
    lines.append("## Scenarios")
    scenarios = report.get("scenarios", {})
    if isinstance(scenarios, dict):
        for key, bucket in scenarios.items():
            if not isinstance(bucket, dict):
                continue
            lines.append(
                f"- `{key}`: count={int(bucket.get('count', 0))}, "
                f"wr={float(bucket.get('win_rate_pct', 0.0)):.2f}%, "
                f"pnl={float(bucket.get('total_pnl', 0.0)):.2f}"
            )
    lines.append("")
    lines.append("## Top Wick-Only Samples")
    samples = report.get("samples", [])
    if isinstance(samples, list) and samples:
        for item in samples[:10]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('symbol')}` `{item.get('entry_time')}` "
                f"{item.get('signal_type_1h')}/{item.get('vwap_state')} "
                f"penetration={float(item.get('penetration_bps', 0.0)):.2f}bps "
                f"pnl={float(item.get('total_pnl', 0.0)):.2f}"
            )
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _json_safe(obj):
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit sweetness of pure backtest IOC direct fills.")
    parser.add_argument("--trades", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    trades_df = _load_csv(Path(args.trades).resolve())
    report = build_fill_sweetness_report(
        trades_df=trades_df,
        cache_dir=Path(args.cache_dir).resolve(),
    )

    output_json = Path(args.output_json).resolve()
    output_md = Path(args.output_md).resolve()
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(_to_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()

