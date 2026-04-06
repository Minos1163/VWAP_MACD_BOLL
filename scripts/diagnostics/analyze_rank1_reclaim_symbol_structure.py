from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from scripts.diagnostics.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
    from scripts.diagnostics.analyze_rank1_reclaim_quality import _aggregate_rank1_entries, _load_csv
    from scripts.diagnostics.analyze_rank1_reclaim_expansion import _load_symbol_bars
    from scripts.diagnostics.analyze_rank1_reclaim_samebar_conflicts import _classify_tp1_hit_timing, _merge_rank1_tp_plan
except ModuleNotFoundError:
    from scripts.diagnostics.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger  # type: ignore
    from scripts.diagnostics.analyze_rank1_reclaim_quality import _aggregate_rank1_entries, _load_csv  # type: ignore
    from scripts.diagnostics.analyze_rank1_reclaim_expansion import _load_symbol_bars  # type: ignore
    from scripts.diagnostics.analyze_rank1_reclaim_samebar_conflicts import _classify_tp1_hit_timing, _merge_rank1_tp_plan  # type: ignore


def _build_first_45m_path(row: pd.Series, bars: pd.DataFrame) -> List[Dict[str, object]]:
    entry_time = pd.to_datetime(row["entry_time"], errors="coerce", utc=True)
    exit_time = pd.to_datetime(row["exit_time"], errors="coerce", utc=True)
    limit_time = min(exit_time, entry_time + pd.Timedelta(minutes=45))
    window = bars.loc[(bars["timestamp"] > entry_time) & (bars["timestamp"] <= limit_time)].copy()
    entry_price = float(pd.to_numeric(row.get("entry_price"), errors="coerce") or 0.0)
    tp1_pct = float(pd.to_numeric(row.get("tp1_pct"), errors="coerce") or 0.0)
    if window.empty or entry_price <= 0.0:
        return []

    side = str(row.get("side", "")).lower()
    path: List[Dict[str, object]] = []
    for _, bar in window.sort_values("timestamp").iterrows():
        high_price = float(pd.to_numeric(bar.get("high"), errors="coerce") or 0.0)
        low_price = float(pd.to_numeric(bar.get("low"), errors="coerce") or 0.0)
        close_price = float(pd.to_numeric(bar.get("close"), errors="coerce") or 0.0)
        if side == "short":
            mfe_pct = max(0.0, (entry_price - low_price) / entry_price * 100.0)
            mae_pct = max(0.0, (high_price - entry_price) / entry_price * 100.0)
            close_return_pct = (entry_price - close_price) / entry_price * 100.0
        else:
            mfe_pct = max(0.0, (high_price - entry_price) / entry_price * 100.0)
            mae_pct = max(0.0, (entry_price - low_price) / entry_price * 100.0)
            close_return_pct = (close_price - entry_price) / entry_price * 100.0
        tp1_progress_pct = (mfe_pct / (tp1_pct * 100.0) * 100.0) if tp1_pct > 0 else 0.0
        path.append(
            {
                "timestamp": pd.to_datetime(bar["timestamp"], errors="coerce", utc=True).isoformat(),
                "minutes_from_entry": int((pd.to_datetime(bar["timestamp"], errors="coerce", utc=True) - entry_time).total_seconds() / 60.0),
                "open": float(pd.to_numeric(bar.get("open"), errors="coerce") or 0.0),
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "mfe_pct": round(mfe_pct, 4),
                "mae_pct": round(mae_pct, 4),
                "close_return_pct": round(close_return_pct, 4),
                "tp1_progress_pct": round(tp1_progress_pct, 4),
            }
        )
    return path


def _json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        ts = pd.to_datetime(value, errors="coerce", utc=True)
        return None if pd.isna(ts) else ts.isoformat()
    if pd.isna(value) if not isinstance(value, (dict, list, tuple, set)) else False:
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return value


def build_symbol_structure_report(
    ledger_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    cache_dir: Path,
    signal_type: str,
    vwap_state: str,
    symbol: str,
) -> Dict[str, object]:
    entries_df = _aggregate_rank1_entries(ledger_df, trades_df, signal_type, vwap_state)
    if entries_df.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "symbol": symbol,
            "count": 0,
            "note": "no matching tp1_never_hit symbol cases found",
        }
    entries_df = _merge_rank1_tp_plan(entries_df, trades_df, ledger_df, signal_type, vwap_state)
    trades_meta = (
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
        trades_meta,
        how="left",
        on=["symbol", "side", "entry_time"],
    )
    filtered = entries_df.loc[
        (entries_df["symbol"].astype(str) == str(symbol))
        & (entries_df["exit_family"] == "time_exit_only")
    ].copy()
    if filtered.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "symbol": symbol,
            "count": 0,
            "note": "no matching tp1_never_hit symbol cases found",
        }

    bars = _load_symbol_bars(cache_dir, str(symbol), timeframe="15m")
    cases: List[Dict[str, object]] = []
    for _, row in filtered.iterrows():
        timing = _classify_tp1_hit_timing(row, bars)
        if str(timing.get("tp1_hit_timing")) != "tp1_never_hit":
            continue
        case = row.to_dict()
        case.update(timing)
        case["entry_time"] = pd.to_datetime(case["entry_time"], errors="coerce", utc=True).isoformat()
        case["exit_time"] = pd.to_datetime(case["exit_time"], errors="coerce", utc=True).isoformat()
        case["bars_first_45m"] = _build_first_45m_path(row, bars)
        cases.append(_json_safe(case))

    if not cases:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "symbol": symbol,
            "count": 0,
            "note": "no matching tp1_never_hit symbol cases found",
        }

    return {
        "signal_type": signal_type,
        "vwap_state": vwap_state,
        "symbol": symbol,
        "count": int(len(cases)),
        "cases": cases,
    }


def write_md(path: Path, report: Dict[str, object]) -> None:
    lines = [
        "# Rank-1 Reclaim Symbol Structure Audit",
        "",
        f"- signal_type: `{report.get('signal_type')}`",
        f"- vwap_state: `{report.get('vwap_state')}`",
        f"- symbol: `{report.get('symbol')}`",
        "",
    ]
    if int(report.get("count", 0) or 0) <= 0:
        lines.append(f"- note: `{report.get('note', 'no cases')}`")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    lines.append(f"- count: `{report.get('count')}`")
    for case in report.get("cases", []):
        lines.extend(
            [
                "",
                f"## {case['symbol']} {case['entry_time']}",
                "",
                f"- signal_score: `{float(case.get('signal_score', 0.0)):.4f}`",
                f"- vwap_score: `{float(case.get('vwap_score', 0.0)):.4f}`",
                f"- adx_1h: `{float(case.get('adx_1h', 0.0)):.2f}`",
                f"- pnl: `{float(case.get('pnl', 0.0)):+.4f}`",
                "",
                "| min_from_entry | high | low | close | mfe_pct | mae_pct | close_return_pct | tp1_progress_pct |",
                "|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for bar in case.get("bars_first_45m", []):
            lines.append(
                f"| {int(bar['minutes_from_entry'])} | {float(bar['high']):.6f} | {float(bar['low']):.6f} | "
                f"{float(bar['close']):.6f} | {float(bar['mfe_pct']):.4f}% | {float(bar['mae_pct']):.4f}% | "
                f"{float(bar['close_return_pct']):+.4f}% | {float(bar['tp1_progress_pct']):.2f}% |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit symbol-specific structure for rank1 reclaim tp1_never_hit cases.")
    parser.add_argument("--candidate-ledger", required=True)
    parser.add_argument("--bot-trades", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--signal-type", default="red_bar_growing")
    parser.add_argument("--vwap-state", default="long_reclaim_confirmed")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    ledger_df = _prepare_candidate_ledger(_load_csv(Path(args.candidate_ledger)))
    trades_df = _load_csv(Path(args.bot_trades))
    report = build_symbol_structure_report(
        ledger_df=ledger_df,
        trades_df=trades_df,
        cache_dir=Path(args.cache_dir).resolve(),
        signal_type=str(args.signal_type),
        vwap_state=str(args.vwap_state),
        symbol=str(args.symbol),
    )
    report = _json_safe(report)
    Path(args.output_json).resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(Path(args.output_md).resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

