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
    from scripts.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
    from scripts.analyze_rank1_reclaim_quality import _aggregate_rank1_entries, _load_csv
    from scripts.analyze_rank1_reclaim_expansion import _load_symbol_bars
except ModuleNotFoundError:
    from analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger  # type: ignore
    from analyze_rank1_reclaim_quality import _aggregate_rank1_entries, _load_csv  # type: ignore
    from analyze_rank1_reclaim_expansion import _load_symbol_bars  # type: ignore


def _price_return(entry_price: float, exit_price: float, side: str) -> float:
    if entry_price <= 0.0:
        return 0.0
    if str(side).lower() == "short":
        return (entry_price - exit_price) / entry_price
    return (exit_price - entry_price) / entry_price


def _conservative_close_price(window: pd.DataFrame, side: str) -> float:
    if window.empty:
        return 0.0
    closes = pd.to_numeric(window["close"], errors="coerce").dropna()
    if closes.empty:
        return 0.0
    if str(side).lower() == "short":
        return float(closes.min())
    return float(closes.max())


def _load_entry_trades(trades_df: pd.DataFrame, signal_type: str, vwap_state: str) -> pd.DataFrame:
    trades = trades_df.copy()
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], errors="coerce", utc=True)
    trades["exit_time"] = pd.to_datetime(trades["exit_time"], errors="coerce", utc=True)
    trades["pnl"] = pd.to_numeric(trades.get("pnl", 0.0), errors="coerce").fillna(0.0)
    trades["margin"] = pd.to_numeric(trades.get("margin", 0.0), errors="coerce").fillna(0.0)
    trades["entry_price"] = pd.to_numeric(trades.get("entry_price", 0.0), errors="coerce").fillna(0.0)
    trades["exit_price"] = pd.to_numeric(trades.get("exit_price", 0.0), errors="coerce").fillna(0.0)
    trades["leverage"] = pd.to_numeric(trades.get("leverage", 0.0), errors="coerce").fillna(0.0)
    return trades[
        (trades["signal_type_1h"].astype(str) == str(signal_type))
        & (trades["vwap_state"].astype(str) == str(vwap_state))
    ].copy()


def build_runner_leakage_report(
    ledger_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    cache_dir: Path,
    signal_type: str,
    vwap_state: str,
) -> Dict[str, object]:
    entries_df = _aggregate_rank1_entries(ledger_df, trades_df, signal_type, vwap_state)
    if entries_df.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "count": 0,
            "note": "no rank1 reclaim entries found",
        }

    relevant_entries = entries_df.loc[entries_df["exit_family"] == "tp_then_time_exit"].copy()
    if relevant_entries.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "count": 0,
            "note": "no tp_then_time_exit rank1 reclaim entries found",
        }

    trades = _load_entry_trades(trades_df, signal_type, vwap_state)
    cache: Dict[str, pd.DataFrame] = {}
    rows: List[Dict[str, object]] = []

    for _, entry in relevant_entries.iterrows():
        symbol = str(entry["symbol"])
        side = str(entry["side"])
        entry_time = pd.to_datetime(entry["entry_time"], errors="coerce", utc=True)
        trade_group = trades[
            (trades["symbol"].astype(str) == symbol)
            & (trades["side"].astype(str) == side)
            & (trades["entry_time"] == entry_time)
        ].sort_values("exit_time")
        if trade_group.empty:
            continue

        tp_rows = trade_group[trade_group["reason"].astype(str).str.contains("take_profit_level_intrabar|partial_tp_level", na=False)].copy()
        time_exit_rows = trade_group[trade_group["reason"].astype(str).str.contains("time_exit", na=False)].copy()
        if tp_rows.empty or time_exit_rows.empty:
            continue

        last_tp = tp_rows.sort_values("exit_time").iloc[-1]
        runner = time_exit_rows.sort_values("exit_time").iloc[-1]
        if symbol not in cache:
            cache[symbol] = _load_symbol_bars(cache_dir, symbol, timeframe="15m")
        bars = cache[symbol]

        start = pd.to_datetime(last_tp["exit_time"], errors="coerce", utc=True)
        end = pd.to_datetime(runner["exit_time"], errors="coerce", utc=True)
        window = bars.loc[(bars["timestamp"] >= start) & (bars["timestamp"] <= end)].copy()
        if window.empty:
            last_tp_flat_price = float(runner["exit_price"])
            peak_price = float(runner["exit_price"])
        else:
            last_tp_bar = window.loc[window["timestamp"] == start].copy()
            if last_tp_bar.empty:
                last_tp_bar = window.sort_values("timestamp").head(1)
            last_tp_flat_price = float(pd.to_numeric(last_tp_bar["close"], errors="coerce").dropna().iloc[-1])
            peak_price = _conservative_close_price(window, side)

        runner_entry_price = float(runner["entry_price"])
        runner_exit_price = float(runner["exit_price"])
        last_tp_price = float(last_tp["exit_price"])
        runner_margin = float(runner["margin"])
        leverage = float(runner["leverage"])
        actual_runner_pnl = float(runner["pnl"])
        runner_hypo_last_tp = runner_margin * leverage * _price_return(runner_entry_price, last_tp_flat_price, side)
        runner_hypo_peak = runner_margin * leverage * _price_return(runner_entry_price, peak_price, side)
        rows.append(
            {
                "symbol": symbol,
                "entry_time": entry_time.isoformat(),
                "exit_time": end.isoformat() if pd.notna(end) else None,
                "entry_price": runner_entry_price,
                "last_tp_time": start.isoformat() if pd.notna(start) else None,
                "last_tp_price": last_tp_price,
                "last_tp_flat_price": last_tp_flat_price,
                "runner_exit_price": runner_exit_price,
                "post_tp_peak_price": peak_price,
                "runner_margin": runner_margin,
                "runner_leverage": leverage,
                "runner_actual_pnl": actual_runner_pnl,
                "runner_hypothetical_pnl_flatten_last_tp": runner_hypo_last_tp,
                "runner_hypothetical_pnl_peak_after_last_tp": runner_hypo_peak,
                "runner_leakage_vs_last_tp": runner_hypo_last_tp - actual_runner_pnl,
                "runner_leakage_vs_peak": runner_hypo_peak - actual_runner_pnl,
                "entry_total_pnl": float(entry["pnl"]),
                "runner_share_of_total_pct": round((actual_runner_pnl / float(entry["pnl"]) * 100.0) if float(entry["pnl"]) != 0.0 else 0.0, 4),
                "signal_score": float(entry["signal_score"]),
                "vwap_score": float(entry["vwap_score"]),
                "adx_1h": float(entry["adx_1h"]),
                "hold_minutes": float(entry["hold_minutes"]),
                "reasons": list(entry["reasons"]),
            }
        )

    report_rows = pd.DataFrame(rows)
    if report_rows.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "count": 0,
            "note": "no tp_then_time_exit rank1 reclaim entries found",
        }

    return {
        "signal_type": signal_type,
        "vwap_state": vwap_state,
        "count": int(len(report_rows)),
        "actual_runner_pnl_total": round(float(report_rows["runner_actual_pnl"].sum()), 4),
        "runner_hypothetical_pnl_flatten_last_tp_total": round(float(report_rows["runner_hypothetical_pnl_flatten_last_tp"].sum()), 4),
        "runner_hypothetical_pnl_peak_after_last_tp_total": round(float(report_rows["runner_hypothetical_pnl_peak_after_last_tp"].sum()), 4),
        "runner_leakage_vs_last_tp_total": round(float(report_rows["runner_leakage_vs_last_tp"].sum()), 4),
        "runner_leakage_vs_peak_total": round(float(report_rows["runner_leakage_vs_peak"].sum()), 4),
        "avg_runner_actual_pnl": round(float(report_rows["runner_actual_pnl"].mean()), 4),
        "avg_runner_leakage_vs_last_tp": round(float(report_rows["runner_leakage_vs_last_tp"].mean()), 4),
        "avg_runner_leakage_vs_peak": round(float(report_rows["runner_leakage_vs_peak"].mean()), 4),
        "cases": report_rows.sort_values("runner_leakage_vs_peak", ascending=False).to_dict(orient="records"),
    }


def write_md(path: Path, report: Dict[str, object]) -> None:
    lines = [
        "# Rank-1 Reclaim Runner Leakage Audit",
        "",
        f"- signal_type: `{report.get('signal_type')}`",
        f"- vwap_state: `{report.get('vwap_state')}`",
        "",
    ]
    if int(report.get("count", 0) or 0) <= 0:
        lines.append(f"- note: `{report.get('note', 'no cases')}`")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    lines.extend(
        [
            f"- count: `{report.get('count', 0)}`",
            f"- actual_runner_pnl_total: `{report.get('actual_runner_pnl_total', 0.0):+.2f}`",
            f"- flatten_last_tp_total: `{report.get('runner_hypothetical_pnl_flatten_last_tp_total', 0.0):+.2f}`",
            f"- peak_after_last_tp_total: `{report.get('runner_hypothetical_pnl_peak_after_last_tp_total', 0.0):+.2f}`",
            f"- leakage_vs_last_tp_total: `{report.get('runner_leakage_vs_last_tp_total', 0.0):+.2f}`",
            f"- leakage_vs_peak_total: `{report.get('runner_leakage_vs_peak_total', 0.0):+.2f}`",
            "",
            "| symbol | entry_time | runner_actual | flatten_last_tp | peak_after_last_tp | leak_vs_last_tp | leak_vs_peak |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report.get("cases", []):
        lines.append(
            f"| {row['symbol']} | {row['entry_time']} | {float(row['runner_actual_pnl']):+.2f} | "
            f"{float(row['runner_hypothetical_pnl_flatten_last_tp']):+.2f} | "
            f"{float(row['runner_hypothetical_pnl_peak_after_last_tp']):+.2f} | "
            f"{float(row['runner_leakage_vs_last_tp']):+.2f} | "
            f"{float(row['runner_leakage_vs_peak']):+.2f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit runner leakage for rank-1 reclaim tp_then_time_exit entries.")
    parser.add_argument("--candidate-ledger", required=True)
    parser.add_argument("--bot-trades", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--signal-type", default="red_bar_growing")
    parser.add_argument("--vwap-state", default="long_reclaim_confirmed")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    ledger_df = _prepare_candidate_ledger(_load_csv(Path(args.candidate_ledger)))
    trades_df = _load_csv(Path(args.bot_trades))
    report = build_runner_leakage_report(
        ledger_df=ledger_df,
        trades_df=trades_df,
        cache_dir=Path(args.cache_dir).resolve(),
        signal_type=str(args.signal_type),
        vwap_state=str(args.vwap_state),
    )
    Path(args.output_json).resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(Path(args.output_md).resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
