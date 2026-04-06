"""
动态仓位系统 Alpha 验证脚本
v4.1-GLM 泛化性审计核心工具

验证假设："连赢N笔后下一笔胜率是否显著高于基准"
如果连赢后胜率 ≈ 基准胜率，则动态仓位没有预测力，+7% 是历史偶然。

用法:
    python scripts/validate_dynamic_position_alpha.py [trades_csv_path]
    默认读取 output/backtest/ 中最新的 v2_trades_*.csv
"""

import sys
import glob
import os
from pathlib import Path
import numpy as np


def find_latest_trades_csv() -> str:
    pattern = str(Path("output/backtest") / "v2_trades_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        print("ERROR: 未找到 v2_trades_*.csv 文件")
        sys.exit(1)
    return files[-1]


def load_trades(csv_path: str) -> list:
    trades = []
    with open(csv_path, "r", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        pnl_idx = header.index("pnl")
        signal_type_idx = header.index("signal_type_1h")
        side_idx = header.index("side")
        mult_idx = header.index("dynamic_position_mult") if "dynamic_position_mult" in header else None
        entry_time_idx = header.index("entry_time")

        for line in f:
            parts = line.strip().split(",")
            if len(parts) <= max(pnl_idx, signal_type_idx):
                continue
            trade = {
                "pnl": float(parts[pnl_idx]) if parts[pnl_idx] else 0.0,
                "is_win": float(parts[pnl_idx] or 0) > 0,
                "signal_type": parts[signal_type_idx].strip(),
                "side": parts[side_idx].strip(),
                "entry_time": parts[entry_time_idx].strip(),
            }
            if mult_idx is not None:
                trade["dynamic_mult"] = float(parts[mult_idx] or 1.0)
            else:
                trade["dynamic_mult"] = 1.0
            trades.append(trade)
    return trades


def validate_streak_alpha(trades: list):
    print("=" * 70)
    print("动态仓位系统 Alpha 验证")
    print("=" * 70)

    n = len(trades)
    wins = sum(1 for t in trades if t["is_win"])
    baseline_wr = wins / n if n > 0 else 0
    print(f"\n总交易数: {n}")
    print(f"盈利交易: {wins}  亏损交易: {n - wins}")
    print(f"基准胜率: {baseline_wr:.4f} ({baseline_wr*100:.2f}%)")
    print(f"标准误差: {(baseline_wr * (1 - baseline_wr) / n) ** 0.5:.4f} ({(baseline_wr * (1 - baseline_wr) / n) ** 0.5 * 100:.2f}%)")

    # --- 核心检验1: 连赢N笔后下一笔胜率 ---
    print("\n" + "-" * 70)
    print("检验1: 连赢N笔后第N+1笔的胜率")
    print("-" * 70)

    print(f"\n{'连赢N笔':<10} {'样本数':<8} {'下一笔胜率':<12} {'vs基准差':<12} {'95%CI下界':<12} {'判断':<15}")
    print("-" * 70)

    alpha_pass = True
    for streak_len in range(2, 6):
        streak_count = 0
        next_wins = 0
        for i in range(streak_len, n):
            if all(trades[i - streak_len + j]["is_win"] for j in range(streak_len)):
                streak_count += 1
                if trades[i]["is_win"]:
                    next_wins += 1

        if streak_count < 5:
            print(f"{streak_len:<10} {streak_count:<8} {'样本不足':<12}")
            continue

        next_wr = next_wins / streak_count
        diff = next_wr - baseline_wr
        se = (next_wr * (1 - next_wr) / streak_count) ** 0.5
        ci_lower = next_wr - 1.96 * se

        if diff >= 0.015:
            verdict = "PASS - 有Alpha"
        elif diff >= -0.005:
            verdict = "WEAK - 边缘"
        else:
            verdict = "FAIL - 无Alpha"
            if streak_len >= 4:
                alpha_pass = False

        print(f"{streak_len:<10} {streak_count:<8} {next_wr*100:.2f}%{'':<5} {diff*100:+.2f}%{'':<5} {ci_lower*100:.2f}%{'':<5} {verdict}")

    # --- 核心检验2: 连输N笔后下一笔胜率 ---
    print("\n" + "-" * 70)
    print("检验2: 连输N笔后第N+1笔的胜率")
    print("-" * 70)

    print(f"\n{'连输N笔':<10} {'样本数':<8} {'下一笔胜率':<12} {'vs基准差':<12} {'95%CI下界':<12} {'判断':<15}")
    print("-" * 70)

    for streak_len in range(2, 6):
        streak_count = 0
        next_wins = 0
        for i in range(streak_len, n):
            if all(not trades[i - streak_len + j]["is_win"] for j in range(streak_len)):
                streak_count += 1
                if trades[i]["is_win"]:
                    next_wins += 1

        if streak_count < 5:
            print(f"{streak_len:<10} {streak_count:<8} {'样本不足':<12}")
            continue

        next_wr = next_wins / streak_count
        diff = next_wr - baseline_wr
        se = (next_wr * (1 - next_wr) / streak_count) ** 0.5
        ci_upper = next_wr + 1.96 * se

        # 连输后胜率应该低于基准
        if diff <= -0.015:
            verdict = "PASS - 有Alpha"
        elif diff <= 0.005:
            verdict = "WEAK - 边缘"
        else:
            verdict = "FAIL - 无Alpha"
            if streak_len >= 4:
                alpha_pass = False

        print(f"{streak_len:<10} {streak_count:<8} {next_wr*100:.2f}%{'':<5} {diff*100:+.2f}%{'':<5} {ci_upper*100:.2f}%{'':<5} {verdict}")

    # --- 核心检验3: 动态仓位乘数分布与 PnL 相关性 ---
    print("\n" + "-" * 70)
    print("检验3: 动态仓位乘数分布与 PnL 相关性")
    print("-" * 70)

    mults = [t["dynamic_mult"] for t in trades]
    pnls = [t["pnl"] for t in trades]
    unique_mults = sorted(set(mults))

    if len(unique_mults) > 1:
        corr = np.corrcoef(mults, pnls)[0, 1]
        print(f"\n乘数与PnL相关系数: {corr:.4f}")

        print(f"\n{'乘数值':<10} {'交易数':<8} {'均PnL':<12} {'均胜率':<12}")
        print("-" * 50)
        for m in unique_mults:
            subset = [t for t in trades if t["dynamic_mult"] == m]
            avg_pnl = np.mean([t["pnl"] for t in subset])
            avg_wr = sum(1 for t in subset if t["is_win"]) / len(subset)
            label = f"{m:.2f}"
            print(f"{label:<10} {len(subset):<8} ${avg_pnl:.2f}{'':<6} {avg_wr*100:.2f}%")

        if abs(corr) < 0.05:
            print(f"\n相关系数接近零 → 动态仓位与实际盈亏几乎不相关")
            alpha_pass = False
    else:
        print(f"\n所有交易乘数相同 ({unique_mults[0]:.2f}) → 动态仓位未生效")

    # --- 检验4: 顺序随机性检验 (runs test 简化版) ---
    print("\n" + "-" * 70)
    print("检验4: 输赢序列自相关性 (连赢是否聚集)")
    print("-" * 70)

    win_seq = [1 if t["is_win"] else 0 for t in trades]
    if len(win_seq) >= 10:
        # 简单自相关: lag-1
        arr = np.array(win_seq, dtype=float)
        mean = arr.mean()
        var = np.var(arr)
        if var > 0:
            autocorr = np.corrcoef(arr[:-1], arr[1:])[0, 1]
            print(f"\nLag-1 自相关系数: {autocorr:.4f}")
            if abs(autocorr) < 0.05:
                print("自相关 ≈ 0 → 输赢序列接近随机，连赢/连输无聚集效应")
                print("→ 动态仓位系统的前提假设（连赢=策略匹配期）不成立")
            elif autocorr > 0:
                print(f"正自相关 → 存在连赢/连输聚集效应，动态仓位有一定理论依据")
            else:
                print(f"负自相关 → 输赢交替为主，动态仓位可能反向放大风险")

    # --- 最终结论 ---
    print("\n" + "=" * 70)
    print("最终结论")
    print("=" * 70)

    if alpha_pass:
        print("\n[RESULT] 动态仓位系统具有统计学上的 Alpha 信号")
        print("→ +7.02% 增量部分可归因于仓位管理优化")
        print("→ 建议保留动态仓位，但在样本外数据上进一步验证")
    else:
        print("\n[RESULT] 动态仓位系统缺乏统计学上的 Alpha 信号")
        print("→ +7.02% 增量来自历史顺序效应（恰好顺风期多买了）")
        print("→ 建议禁用动态仓位，使用 v3.0b 基线配置")
        print("→ 实盘使用动态仓位可能在不应该加仓时加仓，造成超预期亏损")

    print(f"\n核心逻辑:")
    print(f"  基准胜率: {baseline_wr*100:.2f}%")
    print(f"  如果连赢4/5后下一笔胜率 ≤ {baseline_wr*100 + 1:.1f}% → 无预测力")
    print(f"  如果连输4/5后下一笔胜率 ≥ {baseline_wr*100 - 1:.1f}% → 无预测力")
    print(f"  如果两者都无差异 → 动态仓位 = 在随机序列上追涨杀跌")

    return alpha_pass


if __name__ == "__main__":
    csv_path = sys.argv[1] if len(sys.argv) > 1 else find_latest_trades_csv()
    print(f"读取交易记录: {csv_path}")
    trades = load_trades(csv_path)
    if not trades:
        print("ERROR: 未找到有效交易记录")
        sys.exit(1)
    result = validate_streak_alpha(trades)
    sys.exit(0 if result else 1)

