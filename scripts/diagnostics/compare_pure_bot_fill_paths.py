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

from scripts.diagnostics.analyze_ai_shortlist_effectiveness import _prepare_candidate_ledger
from src.utils.passive_fill import classify_passive_limit_fill, direct_ioc_fill_is_valid


def _load_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path.resolve(), low_memory=False, encoding="utf-8-sig")


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


def _normalize_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes"}


def _aggregate_pure_entries(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    if working.empty:
        return pd.DataFrame()
    working["entry_time"] = pd.to_datetime(working["entry_time"], errors="coerce", utc=True)
    working["exit_time"] = pd.to_datetime(working.get("exit_time"), errors="coerce", utc=True)
    working["pnl"] = pd.to_numeric(working.get("pnl", 0.0), errors="coerce").fillna(0.0)
    working["entry_price"] = pd.to_numeric(working.get("entry_price", 0.0), errors="coerce").fillna(0.0)
    working["side"] = working.get("side", "").fillna("").astype(str).str.lower()
    working["signal_type_1h"] = working.get("signal_type_1h", "").fillna("").astype(str)
    working["vwap_state"] = working.get("vwap_state", "").fillna("").astype(str)
    grouping = ["symbol", "side", "entry_time", "signal_type_1h", "vwap_state"]

    rows: List[Dict[str, object]] = []
    for keys, group in working.groupby(grouping, dropna=False):
        symbol, side, entry_time, signal_type_1h, vwap_state = keys
        rows.append(
            {
                "symbol": symbol,
                "side": side,
                "entry_time": entry_time,
                "exit_time": group["exit_time"].max(),
                "entry_price": float(group["entry_price"].iloc[0]),
                "signal_type_1h": signal_type_1h,
                "vwap_state": vwap_state,
                "pure_pnl": float(group["pnl"].sum()),
                "entry_initial_time_in_force": str(group.get("entry_initial_time_in_force", pd.Series(["IOC"])).iloc[0] or "IOC"),
                "entry_time_in_force": str(group.get("entry_time_in_force", pd.Series(["IOC"])).iloc[0] or "IOC"),
                "entry_degradation_path": str(group.get("entry_degradation_path", pd.Series(["[]"])).iloc[0] or "[]"),
                "entry_fill_touch_class": str(group.get("entry_fill_touch_class", pd.Series([""])).iloc[0] or ""),
                "entry_fill_close_through": _normalize_bool(group.get("entry_fill_close_through", pd.Series([False])).iloc[0]),
                "entry_fill_wick_only_touch": _normalize_bool(group.get("entry_fill_wick_only_touch", pd.Series([False])).iloc[0]),
                "entry_fill_penetration_bps": float(
                    pd.to_numeric(group.get("entry_fill_penetration_bps", pd.Series([0.0])), errors="coerce").fillna(0.0).iloc[0]
                ),
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["match_key"] = (
        result["symbol"].fillna("").astype(str)
        + "|"
        + result["side"].fillna("").astype(str)
        + "|"
        + result["signal_type_1h"].fillna("").astype(str)
        + "|"
        + result["vwap_state"].fillna("").astype(str)
    )
    return result.sort_values(["match_key", "entry_time"]).reset_index(drop=True)


def _aggregate_bot_entries(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    if working.empty:
        return pd.DataFrame(columns=["match_key"])
    working["entry_time"] = pd.to_datetime(working["entry_time"], errors="coerce", utc=True)
    working["exit_time"] = pd.to_datetime(working.get("exit_time"), errors="coerce", utc=True)
    working["pnl"] = pd.to_numeric(working.get("pnl", 0.0), errors="coerce").fillna(0.0)
    working["entry_price"] = pd.to_numeric(working.get("entry_price", 0.0), errors="coerce").fillna(0.0)
    working["side"] = working.get("side", "").fillna("").astype(str).str.lower()
    working["signal_type_1h"] = working.get("signal_type_1h", "").fillna("").astype(str)
    working["vwap_state"] = working.get("vwap_state", "").fillna("").astype(str)
    grouping = ["symbol", "side", "entry_time", "signal_type_1h", "vwap_state"]

    rows: List[Dict[str, object]] = []
    for keys, group in working.groupby(grouping, dropna=False):
        symbol, side, entry_time, signal_type_1h, vwap_state = keys
        reasons = [str(x or "") for x in group.get("reason", pd.Series(dtype=str)).tolist() if str(x or "")]
        rows.append(
            {
                "symbol": symbol,
                "side": side,
                "bot_entry_time": entry_time,
                "bot_entry_price": float(group["entry_price"].iloc[0]),
                "signal_type_1h": signal_type_1h,
                "vwap_state": vwap_state,
                "bot_trade_pnl": float(group["pnl"].sum()),
                "bot_trade_reason": " | ".join(reasons[:3]),
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["match_key"] = (
        result["symbol"].fillna("").astype(str)
        + "|"
        + result["side"].fillna("").astype(str)
        + "|"
        + result["signal_type_1h"].fillna("").astype(str)
        + "|"
        + result["vwap_state"].fillna("").astype(str)
    )
    return result.sort_values(["match_key", "bot_entry_time"]).reset_index(drop=True)


def _merge_nearest(left: pd.DataFrame, right: pd.DataFrame, *, left_on: str, right_on: str, tolerance_minutes: int) -> pd.DataFrame:
    if left.empty:
        return left.copy()
    if right.empty:
        return left.copy()
    chunks: List[pd.DataFrame] = []
    tolerance = pd.Timedelta(minutes=tolerance_minutes)
    for match_key, left_group in left.groupby("match_key", sort=False):
        right_group = right.loc[right["match_key"] == match_key]
        if right_group.empty:
            chunks.append(left_group.copy().loc[:, ~left_group.columns.duplicated()])
            continue
        merged_group = pd.merge_asof(
            left_group.sort_values(left_on),
            right_group.sort_values(right_on),
            left_on=left_on,
            right_on=right_on,
            direction="nearest",
            tolerance=tolerance,
            suffixes=("", "_matched"),
        )
        chunks.append(merged_group.loc[:, ~merged_group.columns.duplicated()])
    return pd.concat(chunks, ignore_index=True)


def _enrich_pure_fill_metrics(
    pure_df: pd.DataFrame,
    *,
    cache_dir: Path | None,
    direct_ioc_min_penetration_bps: float,
) -> pd.DataFrame:
    if pure_df.empty:
        return pure_df.copy()
    enriched = pure_df.copy()
    cache: Dict[str, pd.DataFrame] = {}
    for idx, row in enriched.iterrows():
        if row.get("entry_fill_touch_class"):
            enriched.at[idx, "pure_fill_touch_class"] = str(row.get("entry_fill_touch_class", ""))
            enriched.at[idx, "pure_fill_close_through"] = bool(row.get("entry_fill_close_through", False))
            enriched.at[idx, "pure_fill_wick_only_touch"] = bool(row.get("entry_fill_wick_only_touch", False))
            enriched.at[idx, "pure_fill_penetration_bps"] = float(row.get("entry_fill_penetration_bps", 0.0) or 0.0)
            valid, _ = direct_ioc_fill_is_valid(
                {
                    "touched": True,
                    "close_through": bool(row.get("entry_fill_close_through", False)),
                    "penetration_bps": float(row.get("entry_fill_penetration_bps", 0.0) or 0.0),
                },
                mode="close_through_or_penetration",
                min_penetration_bps=direct_ioc_min_penetration_bps,
            )
            enriched.at[idx, "pure_strict_fill_valid"] = bool(valid)
            continue
        if cache_dir is None:
            enriched.at[idx, "pure_fill_touch_class"] = ""
            enriched.at[idx, "pure_fill_close_through"] = False
            enriched.at[idx, "pure_fill_wick_only_touch"] = False
            enriched.at[idx, "pure_fill_penetration_bps"] = 0.0
            enriched.at[idx, "pure_strict_fill_valid"] = False
            continue
        symbol = str(row["symbol"])
        if symbol not in cache:
            cache[symbol] = _load_symbol_bars(cache_dir, symbol)
        bars = cache[symbol]
        match = bars.loc[bars["timestamp"] == pd.Timestamp(row["entry_time"])]
        if match.empty:
            enriched.at[idx, "pure_fill_touch_class"] = ""
            enriched.at[idx, "pure_fill_close_through"] = False
            enriched.at[idx, "pure_fill_wick_only_touch"] = False
            enriched.at[idx, "pure_fill_penetration_bps"] = 0.0
            enriched.at[idx, "pure_strict_fill_valid"] = False
            continue
        bar = match.iloc[0]
        classification = classify_passive_limit_fill(
            side=str(row["side"]).lower(),
            limit_price=float(row["entry_price"]),
            open_price=float(bar["open"]),
            high_price=float(bar["high"]),
            low_price=float(bar["low"]),
            close_price=float(bar["close"]),
        )
        strict_valid, strict_reason = direct_ioc_fill_is_valid(
            classification,
            mode="close_through_or_penetration",
            min_penetration_bps=direct_ioc_min_penetration_bps,
        )
        enriched.at[idx, "pure_fill_touch_class"] = classification["touch_class"]
        enriched.at[idx, "pure_fill_close_through"] = bool(classification["close_through"])
        enriched.at[idx, "pure_fill_wick_only_touch"] = bool(classification["wick_only_touch"])
        enriched.at[idx, "pure_fill_penetration_bps"] = float(classification["penetration_bps"])
        enriched.at[idx, "pure_strict_fill_valid"] = bool(strict_valid)
        enriched.at[idx, "pure_strict_fill_reason"] = strict_reason

    if "pure_fill_touch_class" not in enriched.columns:
        enriched["pure_fill_touch_class"] = enriched["entry_fill_touch_class"].fillna("").astype(str)
    if "pure_fill_close_through" not in enriched.columns:
        enriched["pure_fill_close_through"] = enriched["entry_fill_close_through"].fillna(False).astype(bool)
    if "pure_fill_wick_only_touch" not in enriched.columns:
        enriched["pure_fill_wick_only_touch"] = enriched["entry_fill_wick_only_touch"].fillna(False).astype(bool)
    if "pure_fill_penetration_bps" not in enriched.columns:
        enriched["pure_fill_penetration_bps"] = pd.to_numeric(
            enriched["entry_fill_penetration_bps"], errors="coerce"
        ).fillna(0.0)
    if "pure_strict_fill_valid" not in enriched.columns:
        enriched["pure_strict_fill_valid"] = False
    return enriched


def build_report(
    *,
    pure_trades_df: pd.DataFrame,
    bot_trades_df: pd.DataFrame,
    candidate_ledger_df: pd.DataFrame,
    cache_dir: Path | None,
    tolerance_minutes: int,
    direct_ioc_min_penetration_bps: float,
) -> Dict[str, object]:
    pure_entries = _enrich_pure_fill_metrics(
        _aggregate_pure_entries(pure_trades_df),
        cache_dir=cache_dir,
        direct_ioc_min_penetration_bps=direct_ioc_min_penetration_bps,
    )
    bot_entries = _aggregate_bot_entries(bot_trades_df)
    ledger = _prepare_candidate_ledger(candidate_ledger_df) if not candidate_ledger_df.empty else pd.DataFrame()
    rows = pure_entries.copy()

    if not bot_entries.empty:
        bot_entries = bot_entries[["match_key", "bot_entry_time", "bot_entry_price", "bot_trade_pnl", "bot_trade_reason"]].copy()
        rows = _merge_nearest(rows, bot_entries, left_on="entry_time", right_on="bot_entry_time", tolerance_minutes=tolerance_minutes)
        rows["bot_trade_matched"] = rows["bot_trade_pnl"].notna()
        matched_mask = rows["bot_trade_matched"]
        rows.loc[matched_mask, "entry_time_delta_minutes"] = (
            rows.loc[matched_mask, "bot_entry_time"] - rows.loc[matched_mask, "entry_time"]
        ).dt.total_seconds().div(60.0)
        rows.loc[matched_mask, "entry_price_delta_bps"] = (
            (rows.loc[matched_mask, "bot_entry_price"] - rows.loc[matched_mask, "entry_price"])
            / rows.loc[matched_mask, "entry_price"]
            * 10000.0
        )
    else:
        rows["bot_trade_matched"] = False

    if not ledger.empty:
        ledger = ledger.copy()
        ledger["candidate_time"] = pd.to_datetime(ledger["timestamp"], errors="coerce", utc=True)
        keep_cols = [
            "match_key",
            "candidate_time",
            "timestamp",
            "final_opened",
            "final_reject_reason",
            "capacity_block_reason",
        ]
        ledger = ledger[[col for col in keep_cols if col in ledger.columns]].copy()
        rows = _merge_nearest(rows, ledger, left_on="entry_time", right_on="candidate_time", tolerance_minutes=tolerance_minutes)
        rows["bot_candidate_matched"] = rows["candidate_time"].notna()
        rows["bot_final_opened"] = rows.get("final_opened", False).map(
            lambda value: bool(value) if pd.notna(value) else False
        )
    else:
        rows["bot_candidate_matched"] = False
        rows["bot_final_opened"] = False

    rows["divergence_stage"] = "no_bot_candidate"
    rows.loc[rows["bot_candidate_matched"], "divergence_stage"] = "bot_candidate_not_opened"
    rows.loc[rows["bot_trade_matched"], "divergence_stage"] = "bot_trade_opened"

    result_rows = []
    for _, row in rows.sort_values(["entry_time", "symbol"]).iterrows():
        result_rows.append(
            {
                "symbol": str(row.get("symbol", "")),
                "side": str(row.get("side", "")),
                "entry_time": str(row.get("entry_time")),
                "signal_type_1h": str(row.get("signal_type_1h", "")),
                "vwap_state": str(row.get("vwap_state", "")),
                "pure_entry_price": float(row.get("entry_price", 0.0) or 0.0),
                "pure_pnl": float(row.get("pure_pnl", 0.0) or 0.0),
                "pure_fill_touch_class": str(row.get("pure_fill_touch_class", row.get("entry_fill_touch_class", ""))),
                "pure_fill_close_through": bool(row.get("pure_fill_close_through", False)),
                "pure_fill_wick_only_touch": bool(row.get("pure_fill_wick_only_touch", False)),
                "pure_fill_penetration_bps": float(row.get("pure_fill_penetration_bps", 0.0) or 0.0),
                "pure_strict_fill_valid": bool(row.get("pure_strict_fill_valid", False)),
                "bot_trade_matched": bool(row.get("bot_trade_matched", False)),
                "bot_trade_entry_time": str(row.get("bot_entry_time")) if pd.notna(row.get("bot_entry_time")) else "",
                "bot_trade_entry_price": float(row.get("bot_entry_price", 0.0) or 0.0) if bool(row.get("bot_trade_matched", False)) else 0.0,
                "bot_trade_pnl": float(row.get("bot_trade_pnl", 0.0) or 0.0) if bool(row.get("bot_trade_matched", False)) else 0.0,
                "bot_trade_reason": str(row.get("bot_trade_reason", "")),
                "bot_candidate_matched": bool(row.get("bot_candidate_matched", False)),
                "bot_final_opened": bool(row.get("bot_final_opened", False)),
                "bot_final_reject_reason": str(row.get("final_reject_reason", "")),
                "entry_time_delta_minutes": float(row.get("entry_time_delta_minutes", 0.0) or 0.0) if pd.notna(row.get("entry_time_delta_minutes")) else None,
                "entry_price_delta_bps": float(row.get("entry_price_delta_bps", 0.0) or 0.0) if pd.notna(row.get("entry_price_delta_bps")) else None,
                "divergence_stage": str(row.get("divergence_stage", "")),
            }
        )

    stage_counts = rows["divergence_stage"].value_counts().to_dict() if not rows.empty else {}
    strict_valid_mask = (
        rows["pure_strict_fill_valid"].map(lambda value: bool(value) if pd.notna(value) else False)
        if not rows.empty
        else pd.Series(dtype=bool)
    )
    return {
        "summary": {
            "pure_entry_count": int(len(rows)),
            "matched_bot_trade_count": int(rows["bot_trade_matched"].sum()) if not rows.empty else 0,
            "matched_bot_candidate_count": int(rows["bot_candidate_matched"].sum()) if not rows.empty else 0,
            "wick_only_pure_entries": int(rows["pure_fill_wick_only_touch"].sum()) if not rows.empty else 0,
            "strict_invalid_pure_entries": int((~strict_valid_mask).sum()) if not rows.empty else 0,
            "divergence_stage_counts": {str(k): int(v) for k, v in stage_counts.items()},
        },
        "rows": result_rows,
    }


def _write_md(path: Path, report: Dict[str, object]) -> None:
    summary = report.get("summary", {})
    lines = [
        "# Pure vs Bot Fill Path Compare",
        "",
        f"- pure_entry_count: `{summary.get('pure_entry_count', 0)}`",
        f"- matched_bot_trade_count: `{summary.get('matched_bot_trade_count', 0)}`",
        f"- matched_bot_candidate_count: `{summary.get('matched_bot_candidate_count', 0)}`",
        f"- wick_only_pure_entries: `{summary.get('wick_only_pure_entries', 0)}`",
        f"- strict_invalid_pure_entries: `{summary.get('strict_invalid_pure_entries', 0)}`",
        "",
        "## Stage Counts",
        "",
    ]
    for key, value in (summary.get("divergence_stage_counts", {}) or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    lines.append("## Sample Rows")
    for row in report.get("rows", [])[:20]:
        lines.append(
            f"- `{row['symbol']}` `{row['entry_time']}` stage=`{row['divergence_stage']}` "
            f"pure_fill=`{row['pure_fill_touch_class']}` strict_valid=`{row['pure_strict_fill_valid']}` "
            f"bot_opened=`{row['bot_final_opened']}` bot_pnl=`{row['bot_trade_pnl']:+.2f}`"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare pure backtest fill path against bot-like replay.")
    parser.add_argument("--pure-trades", required=True)
    parser.add_argument("--bot-trades", required=True)
    parser.add_argument("--candidate-ledger", default="")
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--tolerance-minutes", type=int, default=30)
    parser.add_argument("--direct-ioc-min-penetration-bps", type=float, default=5.0)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    report = build_report(
        pure_trades_df=_load_csv(Path(args.pure_trades)),
        bot_trades_df=_load_csv(Path(args.bot_trades)),
        candidate_ledger_df=_load_csv(Path(args.candidate_ledger)) if args.candidate_ledger else pd.DataFrame(),
        cache_dir=Path(args.cache_dir).resolve() if args.cache_dir else None,
        tolerance_minutes=int(args.tolerance_minutes),
        direct_ioc_min_penetration_bps=float(args.direct_ioc_min_penetration_bps),
    )
    output_json = Path(args.output_json).resolve()
    output_md = Path(args.output_md).resolve()
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(output_md, report)


if __name__ == "__main__":
    main()

