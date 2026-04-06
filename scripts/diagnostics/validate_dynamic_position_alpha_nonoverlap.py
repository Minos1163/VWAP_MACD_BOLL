from __future__ import annotations

import argparse
import csv
import glob
import math
from pathlib import Path
from typing import Iterable


def find_latest_trades_csv() -> str:
    matches = sorted(glob.glob(str(Path("output/backtest") / "v2_trades_*.csv")))
    if not matches:
        raise FileNotFoundError("未找到 output/backtest/v2_trades_*.csv")
    return matches[-1]


def load_trades(csv_path: str) -> list[dict]:
    trades: list[dict] = []
    with open(csv_path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            pnl = float(row.get("pnl") or 0.0)
            trades.append(
                {
                    "pnl": pnl,
                    "is_win": pnl > 0.0,
                    "dynamic_mult": float(row.get("dynamic_position_mult") or 1.0),
                    "signal_type": str(row.get("signal_type_1h") or ""),
                    "entry_time": str(row.get("entry_time") or ""),
                }
            )
    return trades


def baseline_stats(trades: list[dict]) -> tuple[float, float]:
    if not trades:
        return 0.0, 0.0
    win_rate = sum(1 for trade in trades if trade["is_win"]) / len(trades)
    avg_pnl = sum(trade["pnl"] for trade in trades) / len(trades)
    return win_rate, avg_pnl


def standard_error(p: float, n: int) -> float:
    if n <= 0:
        return 0.0
    return math.sqrt(max(p * (1.0 - p), 0.0) / n)


def overlapping_samples(trades: list[dict], streak_len: int, *, win_streak: bool) -> list[dict]:
    samples: list[dict] = []
    for idx in range(streak_len, len(trades)):
        window = trades[idx - streak_len:idx]
        if all(trade["is_win"] == win_streak for trade in window):
            samples.append(trades[idx])
    return samples


def non_overlapping_samples(trades: list[dict], streak_len: int, *, win_streak: bool) -> list[dict]:
    samples: list[dict] = []
    idx = streak_len
    while idx < len(trades):
        window = trades[idx - streak_len:idx]
        if all(trade["is_win"] == win_streak for trade in window):
            samples.append(trades[idx])
            idx += streak_len + 1
        else:
            idx += 1
    return samples


def summarize_samples(samples: Iterable[dict], baseline_wr: float) -> dict:
    sample_list = list(samples)
    n = len(sample_list)
    if n == 0:
        return {"count": 0, "win_rate": 0.0, "avg_pnl": 0.0, "diff": 0.0, "ci_low": 0.0, "ci_high": 0.0}
    wr = sum(1 for trade in sample_list if trade["is_win"]) / n
    avg_pnl = sum(trade["pnl"] for trade in sample_list) / n
    se = standard_error(wr, n)
    ci_low = wr - 1.96 * se
    ci_high = wr + 1.96 * se
    return {
        "count": n,
        "win_rate": wr,
        "avg_pnl": avg_pnl,
        "diff": wr - baseline_wr,
        "ci_low": ci_low,
        "ci_high": ci_high,
    }


def fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def fmt_pnl(value: float) -> str:
    return f"{value:.2f}"


def print_section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def print_table_row(label: str, summary: dict) -> None:
    if summary["count"] == 0:
        print(f"{label:<18} {'0':<8} {'-':<10} {'-':<10} {'-':<10} {'-':<20}")
        return
    ci = f"[{fmt_pct(summary['ci_low'])}, {fmt_pct(summary['ci_high'])}]"
    print(
        f"{label:<18} "
        f"{summary['count']:<8} "
        f"{fmt_pct(summary['win_rate']):<10} "
        f"{fmt_pct(summary['diff']):<10} "
        f"{fmt_pnl(summary['avg_pnl']):<10} "
        f"{ci:<20}"
    )


def run_validation(trades: list[dict]) -> int:
    baseline_wr, baseline_avg = baseline_stats(trades)
    print_section("动态仓位非重叠样本验证")
    print(f"总交易数: {len(trades)}")
    print(f"基准胜率: {fmt_pct(baseline_wr)}")
    print(f"基准平均PnL: {fmt_pnl(baseline_avg)}")

    verdict_failures = 0
    for streak_len in (3, 4, 5):
        print_section(f"连赢 {streak_len} 笔后的下一笔")
        print(f"{'method':<18} {'samples':<8} {'next_wr':<10} {'vs_base':<10} {'avg_pnl':<10} {'95% CI':<20}")
        overlap = summarize_samples(overlapping_samples(trades, streak_len, win_streak=True), baseline_wr)
        non_overlap = summarize_samples(non_overlapping_samples(trades, streak_len, win_streak=True), baseline_wr)
        print_table_row("overlap", overlap)
        print_table_row("non_overlap", non_overlap)
        if non_overlap["count"] >= 20 and non_overlap["diff"] < 0.015:
            verdict_failures += 1

    for streak_len in (3, 4, 5):
        print_section(f"连输 {streak_len} 笔后的下一笔")
        print(f"{'method':<18} {'samples':<8} {'next_wr':<10} {'vs_base':<10} {'avg_pnl':<10} {'95% CI':<20}")
        overlap = summarize_samples(overlapping_samples(trades, streak_len, win_streak=False), baseline_wr)
        non_overlap = summarize_samples(non_overlapping_samples(trades, streak_len, win_streak=False), baseline_wr)
        print_table_row("overlap", overlap)
        print_table_row("non_overlap", non_overlap)

    print_section("结论")
    if verdict_failures > 0:
        print("非重叠样本没有稳定给出 >=1.5pp 的顺风增益，动态仓位 alpha 证据不足。")
        return 1

    print("非重叠样本仍保留明显顺风增益，动态仓位不能被直接判死。")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate dynamic position alpha with non-overlapping streak samples")
    parser.add_argument("csv_path", nargs="?", default=None, help="path to v2_trades csv")
    args = parser.parse_args()

    csv_path = args.csv_path or find_latest_trades_csv()
    print(f"读取交易记录: {csv_path}")
    trades = load_trades(csv_path)
    raise SystemExit(run_validation(trades))


if __name__ == "__main__":
    main()

