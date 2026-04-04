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
    from scripts.analyze_rank1_reclaim_samebar_conflicts import _classify_tp1_hit_timing, _merge_rank1_tp_plan
except ModuleNotFoundError:
    from analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger  # type: ignore
    from analyze_rank1_reclaim_quality import _aggregate_rank1_entries, _load_csv  # type: ignore
    from analyze_rank1_reclaim_expansion import _load_symbol_bars  # type: ignore
    from analyze_rank1_reclaim_samebar_conflicts import _classify_tp1_hit_timing, _merge_rank1_tp_plan  # type: ignore


def _infer_fee_rate(entry_price: float, exit_price: float, pnl: float, margin: float, leverage: float, side: str) -> float:
    if margin <= 0.0 or leverage <= 0.0 or entry_price <= 0.0:
        return 0.0
    if str(side).lower() == "short":
        gross_return = (entry_price - exit_price) / entry_price
    else:
        gross_return = (exit_price - entry_price) / entry_price
    gross_pnl = margin * leverage * gross_return
    return max(0.0, (gross_pnl - pnl) / (margin * leverage))


def _hypothetical_tp1_before_exit_pnl(
    *,
    side: str,
    entry_price: float,
    exit_price: float,
    tp1_price: float,
    margin: float,
    leverage: float,
    reduce_pct: float,
    fee_rate: float,
) -> float:
    side_norm = str(side).lower()
    if entry_price <= 0.0 or margin <= 0.0 or leverage <= 0.0:
        return 0.0
    reduce = max(0.0, min(1.0, float(reduce_pct)))
    remain = max(0.0, 1.0 - reduce)
    if side_norm == "short":
        tp_return = (entry_price - tp1_price) / entry_price
        exit_return = (entry_price - exit_price) / entry_price
    else:
        tp_return = (tp1_price - entry_price) / entry_price
        exit_return = (exit_price - entry_price) / entry_price
    gross = margin * leverage * (reduce * tp_return + remain * exit_return)
    fees = margin * leverage * fee_rate
    return gross - fees


def _build_exit_bar_cases(
    ledger_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    cache_dir: Path,
    signal_type: str,
    vwap_state: str,
) -> pd.DataFrame:
    entries_df = _aggregate_rank1_entries(ledger_df, trades_df, signal_type, vwap_state)
    entries_df = _merge_rank1_tp_plan(entries_df, trades_df, ledger_df, signal_type, vwap_state)
    if entries_df.empty:
        return pd.DataFrame()

    trades = trades_df.copy()
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], errors="coerce", utc=True)
    trades["exit_time"] = pd.to_datetime(trades["exit_time"], errors="coerce", utc=True)
    time_exit = trades[
        (trades["signal_type_1h"].astype(str) == str(signal_type))
        & (trades["vwap_state"].astype(str) == str(vwap_state))
        & (trades["reason"].astype(str).str.contains("time_exit", na=False))
    ].copy()
    if time_exit.empty:
        return pd.DataFrame()
    time_exit = (
        time_exit.sort_values(["symbol", "side", "entry_time", "exit_time"])
        .groupby(["symbol", "side", "entry_time"], dropna=False, as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )
    time_exit = time_exit.rename(
        columns={
            "entry_price": "time_exit_trade_entry_price",
            "exit_time": "time_exit_trade_exit_time",
            "exit_price": "time_exit_trade_exit_price",
            "margin": "time_exit_trade_margin",
            "entry_notional": "time_exit_trade_entry_notional",
            "leverage": "time_exit_trade_leverage",
            "pnl": "time_exit_trade_pnl",
            "reason": "time_exit_trade_reason",
        }
    )
    cols = [
        "symbol",
        "side",
        "entry_time",
        "time_exit_trade_entry_price",
        "time_exit_trade_exit_time",
        "time_exit_trade_exit_price",
        "time_exit_trade_margin",
        "time_exit_trade_entry_notional",
        "time_exit_trade_leverage",
        "time_exit_trade_pnl",
        "time_exit_trade_reason",
    ]
    merged = entries_df.merge(time_exit[cols], how="left", on=["symbol", "side", "entry_time"])

    cache: Dict[str, pd.DataFrame] = {}
    rows: List[Dict[str, object]] = []
    for _, row in merged.loc[merged["has_time_exit"] == True].iterrows():
        symbol = str(row["symbol"])
        if symbol not in cache:
            cache[symbol] = _load_symbol_bars(cache_dir, symbol, timeframe="15m")
        timing = _classify_tp1_hit_timing(row, cache[symbol])
        enriched = row.to_dict()
        enriched.update(timing)
        if str(timing.get("tp1_hit_timing")) != "tp1_hit_on_exit_bar_only":
            continue
        exit_time = pd.to_datetime(row.get("exit_time"), errors="coerce", utc=True)
        exit_bar = cache[symbol].loc[cache[symbol]["timestamp"] == exit_time].copy()
        exit_bar_row = exit_bar.iloc[0].to_dict() if not exit_bar.empty else {}
        entry_price = float(pd.to_numeric(row.get("time_exit_trade_entry_price"), errors="coerce") or 0.0)
        exit_price = float(pd.to_numeric(row.get("time_exit_trade_exit_price"), errors="coerce") or 0.0)
        tp1_price = float(pd.to_numeric(row.get("tp1_price"), errors="coerce") or 0.0)
        margin = float(pd.to_numeric(row.get("time_exit_trade_margin"), errors="coerce") or 0.0)
        leverage = float(pd.to_numeric(row.get("time_exit_trade_leverage"), errors="coerce") or 0.0)
        actual_trade_pnl = float(pd.to_numeric(row.get("time_exit_trade_pnl"), errors="coerce") or 0.0)
        reduce_pct = float(pd.to_numeric(row.get("tp1_reduce_pct"), errors="coerce") or 0.0)
        fee_rate = _infer_fee_rate(entry_price, exit_price, actual_trade_pnl, margin, leverage, str(row.get("side", "")))
        hypo_trade_pnl = _hypothetical_tp1_before_exit_pnl(
            side=str(row.get("side", "")),
            entry_price=entry_price,
            exit_price=exit_price,
            tp1_price=tp1_price,
            margin=margin,
            leverage=leverage,
            reduce_pct=reduce_pct,
            fee_rate=fee_rate,
        )
        actual_entry_total_pnl = float(pd.to_numeric(row.get("pnl"), errors="coerce") or 0.0)
        hypothetical_entry_total_pnl = actual_entry_total_pnl - actual_trade_pnl + hypo_trade_pnl
        enriched.update(
            {
                "time_exit_trade_fee_rate_inferred": fee_rate,
                "actual_entry_total_pnl": actual_entry_total_pnl,
                "actual_time_exit_trade_pnl": actual_trade_pnl,
                "hypothetical_time_exit_trade_pnl_tp1_before_exit": hypo_trade_pnl,
                "hypothetical_entry_total_pnl_tp1_before_exit": hypothetical_entry_total_pnl,
                "pnl_delta_tp1_before_exit": hypothetical_entry_total_pnl - actual_entry_total_pnl,
                "exit_bar_open": float(pd.to_numeric(exit_bar_row.get("open"), errors="coerce") or 0.0),
                "exit_bar_high": float(pd.to_numeric(exit_bar_row.get("high"), errors="coerce") or 0.0),
                "exit_bar_low": float(pd.to_numeric(exit_bar_row.get("low"), errors="coerce") or 0.0),
                "exit_bar_close": float(pd.to_numeric(exit_bar_row.get("close"), errors="coerce") or 0.0),
            }
        )
        rows.append(enriched)
    return pd.DataFrame(rows)


def build_exit_bar_ab_report(
    ledger_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    cache_dir: Path,
    signal_type: str,
    vwap_state: str,
) -> Dict[str, object]:
    cases_df = _build_exit_bar_cases(ledger_df, trades_df, cache_dir, signal_type, vwap_state)
    if cases_df.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "count": 0,
            "note": "no tp1_hit_on_exit_bar_only cases remain after final-exit aggregation",
        }
    return {
        "signal_type": signal_type,
        "vwap_state": vwap_state,
        "count": int(len(cases_df)),
        "actual_entry_total_pnl": round(float(cases_df["actual_entry_total_pnl"].sum()), 4),
        "hypothetical_entry_total_pnl_tp1_before_exit": round(
            float(cases_df["hypothetical_entry_total_pnl_tp1_before_exit"].sum()), 4
        ),
        "pnl_delta_tp1_before_exit": round(float(cases_df["pnl_delta_tp1_before_exit"].sum()), 4),
        "avg_delta_per_case": round(float(cases_df["pnl_delta_tp1_before_exit"].mean()), 4),
        "cases": (
            cases_df.loc[
                :,
                [
                    "symbol",
                    "entry_time",
                    "exit_time",
                    "actual_entry_total_pnl",
                    "actual_time_exit_trade_pnl",
                    "hypothetical_time_exit_trade_pnl_tp1_before_exit",
                    "hypothetical_entry_total_pnl_tp1_before_exit",
                    "pnl_delta_tp1_before_exit",
                    "time_exit_trade_entry_price",
                    "tp1_price",
                    "time_exit_trade_exit_price",
                    "tp1_reduce_pct",
                    "time_exit_trade_margin",
                    "time_exit_trade_leverage",
                    "exit_bar_open",
                    "exit_bar_high",
                    "exit_bar_low",
                    "exit_bar_close",
                    "reasons",
                ],
            ]
            .sort_values("pnl_delta_tp1_before_exit", ascending=False)
            .assign(
                entry_time=lambda x: x["entry_time"].astype(str),
                exit_time=lambda x: x["exit_time"].astype(str),
            )
            .to_dict(orient="records")
        ),
    }


def write_md(path: Path, report: Dict[str, object]) -> None:
    lines = [
        "# Rank-1 Reclaim Exit-Bar TP1 A/B Audit",
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
            f"- actual_entry_total_pnl: `{report.get('actual_entry_total_pnl', 0.0):+.2f}`",
            f"- hypothetical_entry_total_pnl_tp1_before_exit: `{report.get('hypothetical_entry_total_pnl_tp1_before_exit', 0.0):+.2f}`",
            f"- pnl_delta_tp1_before_exit: `{report.get('pnl_delta_tp1_before_exit', 0.0):+.2f}`",
            "",
            "| symbol | entry_time | exit_time | actual_entry_pnl | hypo_entry_pnl | delta | tp1 | exit_price | reduce_pct | exit_bar_high | exit_bar_low |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report.get("cases", []):
        lines.append(
            f"| {row['symbol']} | {row['entry_time']} | {row['exit_time']} | "
            f"{float(row['actual_entry_total_pnl']):+.2f} | {float(row['hypothetical_entry_total_pnl_tp1_before_exit']):+.2f} | "
            f"{float(row['pnl_delta_tp1_before_exit']):+.2f} | {float(row['tp1_price']):.6f} | "
            f"{float(row['time_exit_trade_exit_price']):.6f} | {float(row['tp1_reduce_pct']):.2f} | "
            f"{float(row['exit_bar_high']):.6f} | {float(row['exit_bar_low']):.6f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="A/B audit exit-bar ordering for rank-1 reclaim tp1_hit_on_exit_bar_only cases.")
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
    report = build_exit_bar_ab_report(
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
