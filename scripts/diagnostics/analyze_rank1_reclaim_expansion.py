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
    from scripts.diagnostics.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
    from scripts.diagnostics.analyze_rank1_reclaim_quality import _aggregate_rank1_entries, _load_csv
except ModuleNotFoundError:
    from scripts.diagnostics.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger  # type: ignore
    from scripts.diagnostics.analyze_rank1_reclaim_quality import _aggregate_rank1_entries, _load_csv  # type: ignore


def _resolve_latest_cache_file(cache_dir: Path, symbol: str, timeframe: str = "15m") -> Path:
    matches = sorted(cache_dir.glob(f"{symbol}_{timeframe}_*.parquet"))
    if not matches:
        raise FileNotFoundError(f"missing cache for {symbol} {timeframe}")
    return matches[-1]


def _load_symbol_bars(cache_dir: Path, symbol: str, timeframe: str = "15m") -> pd.DataFrame:
    path = _resolve_latest_cache_file(cache_dir, symbol, timeframe=timeframe)
    bars = pd.read_parquet(path)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], errors="coerce", utc=True)
    return bars.sort_values("timestamp").reset_index(drop=True)


def _resolve_tp1_pct(config_path: Path | None, explicit_tp1_pct: float | None) -> float:
    if explicit_tp1_pct is not None and explicit_tp1_pct > 0:
        return float(explicit_tp1_pct)
    if config_path is not None and config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        fund = cfg.get("fund_flow", {}) if isinstance(cfg, dict) else {}
        levels = fund.get("take_profit_pct_levels")
        if isinstance(levels, list) and levels:
            try:
                first = float(levels[0])
            except Exception:
                first = 0.0
            if first > 0:
                return first
    raise ValueError("unable to resolve TP1 pct; provide --tp1-pct or a config with take_profit_pct_levels")


def _max_favorable_excursion_pct(entry: pd.Series, bars: pd.DataFrame) -> float:
    entry_time = pd.to_datetime(entry["entry_time"], errors="coerce", utc=True)
    exit_time = pd.to_datetime(entry["exit_time"], errors="coerce", utc=True)
    entry_price = float(pd.to_numeric(entry["entry_price"], errors="coerce"))
    side = str(entry.get("side", "")).lower()
    window = bars.loc[(bars["timestamp"] >= entry_time) & (bars["timestamp"] <= exit_time)].copy()
    if window.empty or entry_price <= 0.0:
        return 0.0
    if side == "short":
        best_price = float(pd.to_numeric(window["low"], errors="coerce").min())
        return max(0.0, (entry_price - best_price) / entry_price)
    best_price = float(pd.to_numeric(window["high"], errors="coerce").max())
    return max(0.0, (best_price - entry_price) / entry_price)


def _annotate_expansion(entries_df: pd.DataFrame, trades_df: pd.DataFrame, cache_dir: Path, tp1_pct: float) -> pd.DataFrame:
    if entries_df.empty:
        return pd.DataFrame()

    trade_meta = (
        trades_df.copy()
        .assign(
            entry_time=lambda x: pd.to_datetime(x["entry_time"], errors="coerce", utc=True),
            exit_time=lambda x: pd.to_datetime(x["exit_time"], errors="coerce", utc=True),
        )
        .loc[:, ["symbol", "side", "entry_time", "entry_price", "exit_time"]]
        .drop_duplicates(subset=["symbol", "side", "entry_time"])
    )
    merged = entries_df.merge(
        trade_meta,
        how="left",
        on=["symbol", "side", "entry_time"],
    )

    cache: Dict[str, pd.DataFrame] = {}
    rows: List[Dict[str, object]] = []
    for _, row in merged.iterrows():
        symbol = str(row["symbol"])
        if symbol not in cache:
            cache[symbol] = _load_symbol_bars(cache_dir, symbol, timeframe="15m")
        mfe_pct = _max_favorable_excursion_pct(row, cache[symbol])
        progress = (mfe_pct / tp1_pct) if tp1_pct > 0 else 0.0
        enriched = row.to_dict()
        enriched["tp1_pct"] = float(tp1_pct)
        enriched["mfe_pct"] = float(mfe_pct)
        enriched["tp1_progress_ratio"] = float(progress)
        enriched["tp1_progress_pct"] = float(progress * 100.0)
        enriched["tp1_hit"] = bool(mfe_pct >= tp1_pct)
        enriched["near_tp1_90"] = bool(progress >= 0.90)
        enriched["near_tp1_75"] = bool(progress >= 0.75)
        enriched["near_tp1_50"] = bool(progress >= 0.50)
        rows.append(enriched)
    return pd.DataFrame(rows)


def _summarize_subset(df: pd.DataFrame) -> Dict[str, object]:
    if df.empty:
        return {
            "count": 0,
            "win_rate_pct": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "avg_mfe_pct": 0.0,
            "avg_tp1_progress_pct": 0.0,
            "tp1_hit_rate_pct": 0.0,
            "near_tp1_90_rate_pct": 0.0,
            "near_tp1_75_rate_pct": 0.0,
            "near_tp1_50_rate_pct": 0.0,
        }
    return {
        "count": int(len(df)),
        "win_rate_pct": round(float(df["win"].mean() * 100.0), 4),
        "total_pnl": round(float(df["pnl"].sum()), 4),
        "avg_pnl": round(float(df["pnl"].mean()), 4),
        "avg_mfe_pct": round(float(df["mfe_pct"].mean() * 100.0), 4),
        "avg_tp1_progress_pct": round(float(df["tp1_progress_pct"].mean()), 4),
        "tp1_hit_rate_pct": round(float(df["tp1_hit"].mean() * 100.0), 4),
        "near_tp1_90_rate_pct": round(float(df["near_tp1_90"].mean() * 100.0), 4),
        "near_tp1_75_rate_pct": round(float(df["near_tp1_75"].mean() * 100.0), 4),
        "near_tp1_50_rate_pct": round(float(df["near_tp1_50"].mean() * 100.0), 4),
        "avg_hold_minutes": round(float(df["hold_minutes"].mean()), 2),
        "avg_signal_score": round(float(df["signal_score"].mean()), 4),
        "avg_vwap_score": round(float(df["vwap_score"].mean()), 4),
        "avg_adx_1h": round(float(df["adx_1h"].mean()), 4),
    }


def build_report(expansion_df: pd.DataFrame, signal_type: str, vwap_state: str, tp1_pct: float) -> Dict[str, object]:
    if expansion_df.empty:
        return {
            "signal_type": signal_type,
            "vwap_state": vwap_state,
            "tp1_pct": round(tp1_pct * 100.0, 4),
            "count": 0,
            "note": "no rank1 reclaim opened entries found",
        }

    time_exit_mask = expansion_df["has_time_exit"] == True
    time_exit_only_mask = expansion_df["exit_family"] == "time_exit_only"
    tp_then_time_exit_mask = expansion_df["exit_family"] == "tp_then_time_exit"

    return {
        "signal_type": signal_type,
        "vwap_state": vwap_state,
        "tp1_pct": round(tp1_pct * 100.0, 4),
        "overall_rank1": _summarize_subset(expansion_df),
        "time_exit_all": _summarize_subset(expansion_df.loc[time_exit_mask].copy()),
        "time_exit_only": _summarize_subset(expansion_df.loc[time_exit_only_mask].copy()),
        "tp_then_time_exit": _summarize_subset(expansion_df.loc[tp_then_time_exit_mask].copy()),
        "worst_time_exit_examples": (
            expansion_df.loc[time_exit_mask, [
                "symbol",
                "entry_time",
                "pnl",
                "hold_minutes",
                "signal_score",
                "vwap_score",
                "adx_1h",
                "mfe_pct",
                "tp1_progress_pct",
                "tp1_hit",
                "near_tp1_90",
                "near_tp1_75",
                "near_tp1_50",
                "exit_family",
                "reasons",
            ]]
            .sort_values("pnl", ascending=True)
            .head(12)
            .assign(
                entry_time=lambda x: x["entry_time"].astype(str),
                mfe_pct=lambda x: (pd.to_numeric(x["mfe_pct"], errors="coerce").fillna(0.0) * 100.0).round(4),
                tp1_progress_pct=lambda x: pd.to_numeric(x["tp1_progress_pct"], errors="coerce").fillna(0.0).round(4),
            )
            .to_dict(orient="records")
        ),
    }


def write_md(path: Path, report: Dict[str, object]) -> None:
    overall = report.get("overall_rank1", {})
    time_exit_all = report.get("time_exit_all", {})
    time_exit_only = report.get("time_exit_only", {})
    tp_then_time_exit = report.get("tp_then_time_exit", {})
    lines = [
        "# Rank-1 Reclaim Expansion Audit",
        "",
        f"- signal_type: `{report.get('signal_type')}`",
        f"- vwap_state: `{report.get('vwap_state')}`",
        f"- TP1 threshold: `{report.get('tp1_pct', 0.0):.2f}%`",
        "",
        "## Overall Rank-1",
        "",
        f"- count: `{overall.get('count', 0)}`",
        f"- win_rate_pct: `{overall.get('win_rate_pct', 0.0):.2f}%`",
        f"- total_pnl: `{overall.get('total_pnl', 0.0):+.2f}`",
        f"- avg_mfe_pct: `{overall.get('avg_mfe_pct', 0.0):.2f}%`",
        f"- avg_tp1_progress_pct: `{overall.get('avg_tp1_progress_pct', 0.0):.2f}%`",
        "",
        "## Time Exit Families",
        "",
        "| group | count | WR | total_pnl | avg_mfe | avg_tp1_progress | tp1_hit | near90 | near75 | near50 | avg_hold |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| time_exit_all | {time_exit_all.get('count', 0)} | {time_exit_all.get('win_rate_pct', 0.0):.2f}% | {time_exit_all.get('total_pnl', 0.0):+.2f} | {time_exit_all.get('avg_mfe_pct', 0.0):.2f}% | {time_exit_all.get('avg_tp1_progress_pct', 0.0):.2f}% | {time_exit_all.get('tp1_hit_rate_pct', 0.0):.2f}% | {time_exit_all.get('near_tp1_90_rate_pct', 0.0):.2f}% | {time_exit_all.get('near_tp1_75_rate_pct', 0.0):.2f}% | {time_exit_all.get('near_tp1_50_rate_pct', 0.0):.2f}% | {time_exit_all.get('avg_hold_minutes', 0.0):.2f} |",
        f"| time_exit_only | {time_exit_only.get('count', 0)} | {time_exit_only.get('win_rate_pct', 0.0):.2f}% | {time_exit_only.get('total_pnl', 0.0):+.2f} | {time_exit_only.get('avg_mfe_pct', 0.0):.2f}% | {time_exit_only.get('avg_tp1_progress_pct', 0.0):.2f}% | {time_exit_only.get('tp1_hit_rate_pct', 0.0):.2f}% | {time_exit_only.get('near_tp1_90_rate_pct', 0.0):.2f}% | {time_exit_only.get('near_tp1_75_rate_pct', 0.0):.2f}% | {time_exit_only.get('near_tp1_50_rate_pct', 0.0):.2f}% | {time_exit_only.get('avg_hold_minutes', 0.0):.2f} |",
        f"| tp_then_time_exit | {tp_then_time_exit.get('count', 0)} | {tp_then_time_exit.get('win_rate_pct', 0.0):.2f}% | {tp_then_time_exit.get('total_pnl', 0.0):+.2f} | {tp_then_time_exit.get('avg_mfe_pct', 0.0):.2f}% | {tp_then_time_exit.get('avg_tp1_progress_pct', 0.0):.2f}% | {tp_then_time_exit.get('tp1_hit_rate_pct', 0.0):.2f}% | {tp_then_time_exit.get('near_tp1_90_rate_pct', 0.0):.2f}% | {tp_then_time_exit.get('near_tp1_75_rate_pct', 0.0):.2f}% | {tp_then_time_exit.get('near_tp1_50_rate_pct', 0.0):.2f}% | {tp_then_time_exit.get('avg_hold_minutes', 0.0):.2f} |",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit whether rank-1 reclaim time-exit trades ever got near TP1.")
    parser.add_argument("--candidate-ledger", required=True)
    parser.add_argument("--bot-trades", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--config")
    parser.add_argument("--signal-type", default="red_bar_growing")
    parser.add_argument("--vwap-state", default="long_reclaim_confirmed")
    parser.add_argument("--tp1-pct", type=float, default=None)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    ledger_df = _prepare_candidate_ledger(_load_csv(Path(args.candidate_ledger)))
    trades_df = _load_csv(Path(args.bot_trades))
    entries_df = _aggregate_rank1_entries(ledger_df, trades_df, str(args.signal_type), str(args.vwap_state))
    tp1_pct = _resolve_tp1_pct(Path(args.config).resolve() if args.config else None, args.tp1_pct)
    expansion_df = _annotate_expansion(entries_df, trades_df, Path(args.cache_dir).resolve(), tp1_pct)
    report = build_report(expansion_df, str(args.signal_type), str(args.vwap_state), tp1_pct)
    Path(args.output_json).resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(Path(args.output_md).resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

