from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

try:
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover - plotting is optional at runtime
    plt = None


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backtest_macd_v2 import (  # noqa: E402
    BacktestEngine,
    apply_backtest_profile,
    build_backtest_config,
    build_strategy_config,
    load_symbol_data,
)
from src.fund_flow.macd_strategy_v2 import MACDStrategyV2Engine  # noqa: E402
from src.fund_flow.timeframe_conflict_resolver import TimeframeConflictResolver  # noqa: E402


def _load_runtime_config(config_path: Path, profile: str) -> tuple[dict, str]:
    with config_path.open("r", encoding="utf-8") as fp:
        runtime_cfg = json.load(fp)
    return apply_backtest_profile(runtime_cfg, profile)


def _metric_window(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, side: str) -> Dict[str, float]:
    window = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)].copy()
    entry_row = df.loc[df["timestamp"] == start]
    exit_row = df.loc[df["timestamp"] == end]
    if entry_row.empty or exit_row.empty or window.empty:
        raise ValueError(f"missing data for window {start} -> {end}")

    entry_price = float(entry_row.iloc[0]["close"])
    exit_price = float(exit_row.iloc[0]["close"])
    if side == "long":
        mae = (float(window["low"].min()) - entry_price) / entry_price
        mfe = (float(window["high"].max()) - entry_price) / entry_price
        pnl = (exit_price - entry_price) / entry_price
    else:
        mae = (entry_price - float(window["high"].max())) / entry_price
        mfe = (entry_price - float(window["low"].min())) / entry_price
        pnl = (entry_price - exit_price) / entry_price

    return {
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_pct": pnl * 100.0,
        "mae_pct": mae * 100.0,
        "mfe_pct": mfe * 100.0,
    }


def _analyze_signal_window(
    engine: BacktestEngine,
    data: Dict[str, pd.DataFrame],
    symbol: str,
    side: str,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
) -> pd.DataFrame:
    tf_15m = data["15m"]
    rows = tf_15m[(tf_15m["timestamp"] >= window_start) & (tf_15m["timestamp"] <= window_end)]
    signal_rows = []
    for idx, row in rows.iterrows():
        analysis = engine.analyze_bar(symbol, data, int(idx))
        if not analysis:
            continue
        signal = analysis["signal"]
        details = signal.details or {}
        threshold = float(engine._signal_threshold(signal))
        signal_rows.append(
            {
                "timestamp": row["timestamp"],
                "signal_direction": signal.direction,
                "signal_score": float(signal.signal_score),
                "signal_threshold": threshold,
                "signal_pass": bool(signal.direction == side and float(signal.signal_score) >= threshold),
                "signal_type_1h": signal.signal_type_1h,
                "entry_type_15m": signal.entry_type_15m,
                "reason": details.get("reason", ""),
                "trade_direction": details.get("trade_direction", ""),
                "direction_1h": details.get("direction_1h", ""),
                "direction_4h": details.get("direction_4h", ""),
                "signal_type_4h": details.get("signal_type_4h", ""),
                "vwap_state": signal.vwap_state,
                "vwap_score": float(signal.vwap_score),
                "ema_multiplier": float(signal.ema_multiplier),
                "cvd_veto_state": analysis["cvd_veto_context"].get("cvd_veto_state", ""),
            }
        )
    return pd.DataFrame(signal_rows)


def _decorate_macd_states(df: pd.DataFrame, engine: MACDStrategyV2Engine) -> pd.DataFrame:
    result = df.copy()
    states = []
    for i in range(len(result)):
        if i < 1:
            states.append((None, None, 0.0))
            continue
        hist = result.iloc[: i + 1]["macd_hist"].to_numpy()
        direction, details = engine.detect_macd_direction(hist, len(hist) - 1)
        states.append((direction, details.get("signal_type"), float(details.get("signal_strength", 0.0) or 0.0)))
    result["macd_direction"] = [x[0] for x in states]
    result["macd_signal_type"] = [x[1] for x in states]
    result["signal_strength"] = [x[2] for x in states]
    return result


def _decorate_shrink_pct(df: pd.DataFrame) -> pd.DataFrame:
    resolver = TimeframeConflictResolver()
    result = df.copy()
    shrink_values = []
    for i in range(len(result)):
        if i < 9:
            shrink_values.append(0.0)
            continue
        hist = result.iloc[: i + 1]["macd_hist"].to_numpy()
        shrink_values.append(float(resolver._calculate_macd_shrink_pct(hist)))
    result["macd_shrink_pct"] = shrink_values
    return result


def _plot_case(
    df: pd.DataFrame,
    out_path: Path,
    title: str,
    user_entry: pd.Timestamp,
    user_exit: pd.Timestamp,
    actual_entry: Optional[pd.Timestamp],
) -> None:
    if plt is None:
        return

    fig, axes = plt.subplots(
        3,
        1,
        figsize=(14, 9),
        sharex=True,
        gridspec_kw={"height_ratios": [2.4, 1.2, 1.0]},
    )

    axes[0].plot(df["timestamp"], df["close"], color="black", linewidth=1.5, label="close")
    if {"bb_middle", "bb_upper", "bb_lower"}.issubset(df.columns):
        axes[0].plot(df["timestamp"], df["bb_middle"], color="#1f77b4", linewidth=1.0, label="bb_middle")
        axes[0].plot(df["timestamp"], df["bb_upper"], color="#9ecae1", linewidth=0.8, label="bb_upper")
        axes[0].plot(df["timestamp"], df["bb_lower"], color="#9ecae1", linewidth=0.8, label="bb_lower")
    if "vwap" in df.columns:
        axes[0].plot(df["timestamp"], df["vwap"], color="#ff7f0e", linewidth=1.0, label="vwap")
    if "structural_vwap" in df.columns:
        axes[0].plot(df["timestamp"], df["structural_vwap"], color="#2ca02c", linewidth=1.0, label="structural_vwap")
    axes[0].axvline(user_entry, color="green", linestyle="--", linewidth=1.0, label="user_entry")
    axes[0].axvline(user_exit, color="red", linestyle="--", linewidth=1.0, label="user_exit")
    if actual_entry is not None:
        axes[0].axvline(actual_entry, color="blue", linestyle="--", linewidth=1.0, label="strategy_entry")
    axes[0].set_title(title)
    axes[0].legend(loc="upper left", fontsize=8, ncol=6)
    axes[0].grid(alpha=0.25)

    colors = ["#d62728" if x >= 0 else "#2ca02c" for x in df["macd_hist"]]
    axes[1].bar(df["timestamp"], df["macd_hist"], color=colors, width=0.12 if len(df) > 50 else 0.6)
    axes[1].plot(df["timestamp"], df["macd_shrink_pct"], color="#9467bd", linewidth=1.0, label="shrink_pct")
    axes[1].axhline(0, color="gray", linewidth=0.8)
    axes[1].axvline(user_entry, color="green", linestyle="--", linewidth=1.0)
    axes[1].axvline(user_exit, color="red", linestyle="--", linewidth=1.0)
    if actual_entry is not None:
        axes[1].axvline(actual_entry, color="blue", linestyle="--", linewidth=1.0)
    axes[1].legend(loc="upper left", fontsize=8)
    axes[1].grid(alpha=0.25)

    if "cvd_session_ratio" in df.columns:
        axes[2].plot(df["timestamp"], df["cvd_session_ratio"], color="#8c564b", label="cvd_session_ratio")
    if "cvd_session_pressure" in df.columns:
        axes[2].plot(df["timestamp"], df["cvd_session_pressure"], color="#e377c2", label="cvd_session_pressure")
    if "cvd_delta_ratio" in df.columns:
        axes[2].bar(df["timestamp"], df["cvd_delta_ratio"], color="#7f7f7f", alpha=0.2, label="cvd_delta_ratio")
    axes[2].axhline(0, color="gray", linewidth=0.8)
    axes[2].axvline(user_entry, color="green", linestyle="--", linewidth=1.0)
    axes[2].axvline(user_exit, color="red", linestyle="--", linewidth=1.0)
    if actual_entry is not None:
        axes[2].axvline(actual_entry, color="blue", linestyle="--", linewidth=1.0)
    axes[2].legend(loc="upper left", fontsize=8)
    axes[2].grid(alpha=0.25)

    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a MACD V2 case window for one symbol.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", choices=["long", "short"], required=True)
    parser.add_argument("--entry", required=True, help="User expected entry time, e.g. 2026-02-24 20:00:00")
    parser.add_argument("--exit", required=True, help="User expected exit time")
    parser.add_argument("--profile", default="macd_v2_pure_4h_light_confirm_1h")
    parser.add_argument("--config", default="config/trading_config_fund_flow.json")
    parser.add_argument("--data-dir", default="data/backtest_cache")
    parser.add_argument("--signal-lookback-hours", type=int, default=24)
    parser.add_argument("--signal-forward-hours", type=int, default=24)
    args = parser.parse_args()

    config_path = Path(args.config)
    runtime_cfg, selected_profile = _load_runtime_config(config_path, args.profile)
    strategy_config = build_strategy_config(runtime_cfg)
    backtest_config = build_backtest_config(
        runtime_cfg,
        str(config_path),
        profile_name=selected_profile,
    )
    analysis_engine = BacktestEngine(backtest_config, strategy_config, runtime_cfg)
    symbol = args.symbol.upper()
    data = load_symbol_data(args.data_dir, symbol, strategy_config)
    if not data:
        raise SystemExit(f"missing data for {symbol}")

    user_entry = pd.Timestamp(args.entry)
    user_exit = pd.Timestamp(args.exit)
    out_dir = ROOT / "output" / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    user_stats = _metric_window(data["15m"], user_entry, user_exit, args.side)
    signal_window = _analyze_signal_window(
        analysis_engine,
        data,
        symbol,
        args.side,
        user_entry - pd.Timedelta(hours=args.signal_lookback_hours),
        min(user_exit, user_entry + pd.Timedelta(hours=args.signal_forward_hours)),
    )
    first_pass_time: Optional[pd.Timestamp] = None
    if not signal_window.empty:
        passed = signal_window[signal_window["signal_pass"]]
        if not passed.empty:
            first_pass_time = pd.Timestamp(passed.iloc[0]["timestamp"])

    tf4 = _decorate_shrink_pct(_decorate_macd_states(data["4h"], MACDStrategyV2Engine(strategy_config)))
    tf1 = _decorate_shrink_pct(_decorate_macd_states(data["1h"], MACDStrategyV2Engine(strategy_config)))
    tf4_case = tf4[(tf4["timestamp"] >= user_entry - pd.Timedelta(hours=80)) & (tf4["timestamp"] <= user_exit + pd.Timedelta(hours=4))]
    tf1_case = tf1[(tf1["timestamp"] >= user_entry - pd.Timedelta(hours=12)) & (tf1["timestamp"] <= user_exit + pd.Timedelta(hours=8))]

    safe_case_id = f"{symbol.lower()}_{args.side}_{user_entry.strftime('%Y%m%d_%H%M')}_{user_exit.strftime('%Y%m%d_%H%M')}"
    signal_csv = out_dir / f"{safe_case_id}_signals.csv"
    tf4_csv = out_dir / f"{safe_case_id}_4h.csv"
    tf1_csv = out_dir / f"{safe_case_id}_1h.csv"
    signal_window.to_csv(signal_csv, index=False, encoding="utf-8-sig")
    tf4_case.to_csv(tf4_csv, index=False, encoding="utf-8-sig")
    tf1_case.to_csv(tf1_csv, index=False, encoding="utf-8-sig")

    plot_4h = out_dir / f"{safe_case_id}_4h.png"
    plot_1h = out_dir / f"{safe_case_id}_1h.png"
    _plot_case(tf4_case, plot_4h, f"{symbol} {args.side.upper()} 4H case", user_entry, user_exit, first_pass_time)
    _plot_case(tf1_case, plot_1h, f"{symbol} {args.side.upper()} 1H case", user_entry, user_exit, first_pass_time)

    result = {
        "symbol": symbol,
        "side": args.side,
        "profile": selected_profile,
        "user_entry": str(user_entry),
        "user_exit": str(user_exit),
        "first_strategy_pass_time": str(first_pass_time) if first_pass_time is not None else "",
        "user_window_stats": user_stats,
        "signal_rows": int(len(signal_window)),
        "passed_rows": int(signal_window["signal_pass"].sum()) if not signal_window.empty else 0,
        "artifacts": {
            "signal_csv": str(signal_csv),
            "tf4_csv": str(tf4_csv),
            "tf1_csv": str(tf1_csv),
            "plot_4h": str(plot_4h) if plot_4h.exists() else "",
            "plot_1h": str(plot_1h) if plot_1h.exists() else "",
        },
        "strategy_config_snapshot": {
            "primary_direction_timeframe": strategy_config.primary_direction_timeframe,
            "require_1h_confirmation_when_4h_primary": strategy_config.require_1h_confirmation_when_4h_primary,
            "light_1h_confirmation_when_4h_primary": strategy_config.light_1h_confirmation_when_4h_primary,
            "min_signal_score": strategy_config.min_signal_score,
            "min_vwap_score_for_entry": strategy_config.min_vwap_score_for_entry,
            "disable_red_bar_growing_long_entries": strategy_config.disable_red_bar_growing_long_entries,
            "disable_green_bar_growing_entries": strategy_config.disable_green_bar_growing_entries,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

