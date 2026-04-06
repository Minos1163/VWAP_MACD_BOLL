from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _load_trades(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"trades file not found: {path}")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    raise ValueError(f"unsupported input file type: {path.suffix}")


def _compute_hold_hours(df: pd.DataFrame) -> pd.DataFrame:
    if "entry_time" not in df.columns or "exit_time" not in df.columns:
        df["hold_hours"] = pd.NA
        return df
    entry = pd.to_datetime(df["entry_time"], errors="coerce", utc=True)
    exit_ = pd.to_datetime(df["exit_time"], errors="coerce", utc=True)
    df["hold_hours"] = (exit_ - entry).dt.total_seconds() / 3600.0
    return df


def _group_metrics(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    working["signal_type_1h"] = working.get("signal_type_1h", "unknown").fillna("unknown").astype(str)
    working["vwap_state"] = working.get("vwap_state", "unknown").fillna("unknown").astype(str)
    working["symbol"] = working.get("symbol", "UNKNOWN").fillna("UNKNOWN").astype(str)
    working["pnl"] = pd.to_numeric(working.get("pnl", 0.0), errors="coerce").fillna(0.0)
    working = _compute_hold_hours(working)

    rows = []
    for (signal_type_1h, vwap_state, symbol), group in working.groupby(
        ["signal_type_1h", "vwap_state", "symbol"], dropna=False
    ):
        rows.append(
            {
                "signal_type_1h": signal_type_1h,
                "vwap_state": vwap_state,
                "symbol": symbol,
                "count": int(len(group)),
                "pnl": float(group["pnl"].sum()),
                "win_rate_pct": float((group["pnl"] > 0).mean() * 100.0) if len(group) else 0.0,
                "avg_pnl": float(group["pnl"].mean()) if len(group) else 0.0,
                "avg_win": float(group.loc[group["pnl"] > 0, "pnl"].mean()) if (group["pnl"] > 0).any() else 0.0,
                "avg_loss": float(group.loc[group["pnl"] <= 0, "pnl"].mean()) if (group["pnl"] <= 0).any() else 0.0,
                "avg_hold_hours": float(group["hold_hours"].dropna().mean()) if group["hold_hours"].notna().any() else 0.0,
            }
        )
    grouped = pd.DataFrame(rows)
    grouped["pocket"] = grouped["signal_type_1h"] + " + " + grouped["vwap_state"]
    grouped["payoff_ratio"] = grouped.apply(
        lambda row: (row["avg_win"] / abs(row["avg_loss"])) if row["avg_loss"] < 0 else 0.0,
        axis=1,
    )
    return grouped.sort_values(["pnl", "count"], ascending=[True, False]).reset_index(drop=True)


def _write_markdown(df: pd.DataFrame, output_path: Path, top_n: int) -> None:
    lines = [
        "# Symbol x Pocket Attribution",
        "",
        f"- Total combinations: `{len(df)}`",
        f"- Generated at: `{pd.Timestamp.utcnow().isoformat()}`",
        "",
        "## Worst Pocket x Symbol",
        "",
        "| pocket | symbol | count | pnl | win_rate | avg_win | avg_loss | payoff | avg_hold_h |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in df.head(top_n).iterrows():
        lines.append(
            f"| {row['pocket']} | {row['symbol']} | {int(row['count'])} | "
            f"{row['pnl']:+.2f} | {row['win_rate_pct']:.2f}% | {row['avg_win']:+.2f} | "
            f"{row['avg_loss']:+.2f} | {row['payoff_ratio']:.2f} | {row['avg_hold_hours']:.2f} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze symbol x pocket attribution from backtest trades.")
    parser.add_argument("--trades", required=True, help="Path to trades csv/parquet")
    parser.add_argument("--output-csv", help="Optional output CSV path")
    parser.add_argument("--output-md", help="Optional output markdown path")
    parser.add_argument("--top-n", type=int, default=30, help="Rows to include in markdown summary")
    parser.add_argument("--signal-type", help="Optional signal_type_1h filter")
    parser.add_argument("--vwap-state", help="Optional vwap_state filter")
    args = parser.parse_args()

    trades_path = Path(args.trades).expanduser().resolve()
    trades = _load_trades(trades_path)
    if args.signal_type:
        trades = trades[trades.get("signal_type_1h", "").astype(str) == str(args.signal_type)]
    if args.vwap_state:
        trades = trades[trades.get("vwap_state", "").astype(str) == str(args.vwap_state)]

    result = _group_metrics(trades)
    print(result.head(args.top_n).to_string(index=False))

    if args.output_csv:
        output_csv = Path(args.output_csv).expanduser().resolve()
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(output_csv, index=False, encoding="utf-8-sig")
        print(f"\nSaved CSV: {output_csv}")

    if args.output_md:
        output_md = Path(args.output_md).expanduser().resolve()
        output_md.parent.mkdir(parents=True, exist_ok=True)
        _write_markdown(result, output_md, max(1, args.top_n))
        print(f"Saved Markdown: {output_md}")


if __name__ == "__main__":
    main()

