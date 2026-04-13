"""
RSI 指标计算器 - 多时间框架版本

功能:
  - 多时间框架 RSI 计算 (7/14/21 周期)
  - RSI 斜率计算
  - 背离检测 (正则多空背离 + 隐藏多空背离)
  - RSI 评分映射（0~1）

依赖: math, logging
无外部 pip 依赖
"""

from __future__ import annotations

import math
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RSIIndicator:
    """
    多时间框架 RSI 计算器
    支持背离检测与评分映射
    """

    def __init__(self, config: dict):
        self.period_map = {
            "15m": config.get("period_15m", 7),
            "1h": config.get("period_1h", 14),
            "4h": config.get("period_4h", 21),
        }
        self.divergence_lookback = config.get("divergence_lookback_bars", 10)
        self.min_price_diff_pct = config.get("divergence_min_price_diff_pct", 0.005)
        self.min_rsi_diff = config.get("divergence_min_rsi_diff", 3.0)

    # ─────────────────────────────────────────
    # RSI 计算
    # ─────────────────────────────────────────

    def compute_rsi(self, closes: list, period: int) -> list:
        """
        标准 Wilder RSI 计算
        输入: 收盘价序列（时序升序）
        输出: RSI 序列，与输入等长（前 period 个为 NaN）
        """
        n = len(closes)
        if n < period + 1:
            return [float("nan")] * n

        gains = []
        losses = []
        for i in range(1, n):
            delta = closes[i] - closes[i - 1]
            gains.append(max(delta, 0.0))
            losses.append(max(-delta, 0.0))

        rsi_series = [float("nan")] * n

        # 初始 SMA
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

            if avg_loss == 0:
                rsi_series[i + 1] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_series[i + 1] = 100.0 - (100.0 / (1.0 + rs))

        return rsi_series

    @staticmethod
    def compute_rsi_from_array(closes, period: int = 14) -> float:
        """
        从 numpy 数组或列表计算最新 RSI 值
        返回: 最新 RSI float, 或 NaN
        """
        import numpy as np

        if len(closes) < period + 1:
            return float("nan")

        arr = np.asarray(closes, dtype=float)
        delta = np.diff(arr)
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)

        avg_gain = np.mean(gain[:period])
        avg_loss = np.mean(loss[:period])

        for i in range(period, len(gain)):
            avg_gain = (avg_gain * (period - 1) + gain[i]) / period
            avg_loss = (avg_loss * (period - 1) + loss[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def get_rsi_for_timeframe(
        self,
        symbol: str,
        timeframe: str,
        candles: list,
    ) -> float:
        """获取最新 RSI 值"""
        period = self.period_map.get(timeframe, 14)
        closes = [c["close"] for c in candles] if candles else []
        if not closes:
            return float("nan")
        rsi_series = self.compute_rsi(closes, period)

        # 返回最新非 NaN 值
        for val in reversed(rsi_series):
            if not math.isnan(val):
                return val
        return float("nan")

    def get_rsi_slope(
        self,
        symbol: str,
        timeframe: str,
        candles: list,
        slope_bars: int = 3,
    ) -> float:
        """
        计算 RSI 斜率（最近 slope_bars 根 K 线的 RSI 变化率）
        正值: RSI 上升
        负值: RSI 下降
        """
        period = self.period_map.get(timeframe, 14)
        closes = [c["close"] for c in candles] if candles else []
        if not closes:
            return 0.0
        rsi_series = self.compute_rsi(closes, period)

        valid = [v for v in rsi_series if not math.isnan(v)]
        if len(valid) < slope_bars + 1:
            return 0.0

        return valid[-1] - valid[-1 - slope_bars]

    # ─────────────────────────────────────────
    # 背离检测
    # ─────────────────────────────────────────

    def detect_divergence(
        self,
        candles: list,
        timeframe: str,
        direction: str,
    ) -> dict:
        """
        检测 RSI 背离

        返回: {
            "regular_bullish": bool,   # 看多背离
            "regular_bearish": bool,   # 看空背离
            "hidden_bullish": bool,    # 隐藏多头背离
            "hidden_bearish": bool,    # 隐藏空头背离
            "divergence_strength": float  # 0~1
        }
        """
        period = self.period_map.get(timeframe, 14)
        closes = [c["close"] for c in candles] if candles else []
        if not candles:
            return self._empty_divergence()

        highs = [c.get("high", c["close"]) for c in candles]
        lows = [c.get("low", c["close"]) for c in candles]
        rsi_series = self.compute_rsi(closes, period)

        # 取最近 lookback 个有效 RSI
        valid_rsi = [
            (i, v) for i, v in enumerate(rsi_series) if not math.isnan(v)
        ]
        recent = valid_rsi[-self.divergence_lookback:]

        if len(recent) < 4:
            return self._empty_divergence()

        # 最新点
        current_idx, current_rsi = recent[-1]
        current_price_low = lows[current_idx]
        current_price_high = highs[current_idx]

        # 历史极值（排除最近 2 根）
        historical = recent[:-2]
        if not historical:
            return self._empty_divergence()

        min_rsi_idx, min_rsi_val = min(historical, key=lambda x: x[1])
        max_rsi_idx, max_rsi_val = max(historical, key=lambda x: x[1])

        min_price_low = lows[min_rsi_idx]
        max_price_high = highs[max_rsi_idx]

        # 看多背离: 价格创新低，RSI 未创新低
        regular_bullish = (
            current_price_low < min_price_low * (1 - self.min_price_diff_pct)
            and current_rsi > min_rsi_val + self.min_rsi_diff
        )

        # 看空背离: 价格创新高，RSI 未创新高
        regular_bearish = (
            current_price_high > max_price_high * (1 + self.min_price_diff_pct)
            and current_rsi < max_rsi_val - self.min_rsi_diff
        )

        # 隐藏多头背离: 价格高点更高，RSI 高点更低
        hidden_bullish = (
            current_price_high > max_price_high * (1 + self.min_price_diff_pct)
            and current_rsi < max_rsi_val - self.min_rsi_diff
            and current_rsi > 45
        )

        # 隐藏空头背离: 价格低点更低，RSI 低点更高
        hidden_bearish = (
            current_price_low < min_price_low * (1 - self.min_price_diff_pct)
            and current_rsi > min_rsi_val + self.min_rsi_diff
            and current_rsi < 55
        )

        strength = 0.0
        if regular_bullish:
            price_diff = (min_price_low - current_price_low) / max(min_price_low, 1e-10)
            rsi_diff = (current_rsi - min_rsi_val) / 100
            strength = min(price_diff * 10 + rsi_diff * 5, 1.0)
        elif regular_bearish:
            price_diff = (current_price_high - max_price_high) / max(max_price_high, 1e-10)
            rsi_diff = (max_rsi_val - current_rsi) / 100
            strength = min(price_diff * 10 + rsi_diff * 5, 1.0)

        return {
            "regular_bullish": regular_bullish,
            "regular_bearish": regular_bearish,
            "hidden_bullish": hidden_bullish,
            "hidden_bearish": hidden_bearish,
            "divergence_strength": strength,
        }

    def _empty_divergence(self) -> dict:
        return {
            "regular_bullish": False,
            "regular_bearish": False,
            "hidden_bullish": False,
            "hidden_bearish": False,
            "divergence_strength": 0.0,
        }

    # ─────────────────────────────────────────
    # RSI 评分映射
    # ─────────────────────────────────────────

    @staticmethod
    def compute_rsi_score(rsi_value: float, direction: str) -> float:
        """
        将 RSI 值转换为 0~1 分数

        做多方向:
          RSI 55-65  → 0.9 (最优区间)
          RSI 45-55  → 0.8 (良好区间)
          RSI 65-70  → 0.3 (偏热)
          RSI 40-45  → 0.5 (勉强)
          RSI > 70   → 0.0 (超买)
          RSI < 40   → 0.0 (动量不足)

        做空方向:
          RSI 35-45  → 0.9 (最优区间)
          RSI 45-55  → 0.8 (良好区间)
          RSI 30-35  → 0.3 (偏冷)
          RSI 55-60  → 0.5 (勉强)
          RSI < 30   → 0.0 (超卖)
          RSI > 60   → 0.0 (动量过强)
        """
        if math.isnan(rsi_value):
            return 0.0

        if direction == "long":
            if rsi_value >= 70:
                return 0.0
            elif rsi_value >= 65:
                return 0.3
            elif rsi_value >= 55:
                return 0.9
            elif rsi_value >= 45:
                return 0.8
            elif rsi_value >= 40:
                return 0.5
            else:
                return 0.0

        elif direction == "short":
            if rsi_value <= 30:
                return 0.0
            elif rsi_value <= 35:
                return 0.3
            elif rsi_value <= 45:
                return 0.9
            elif rsi_value <= 55:
                return 0.8
            elif rsi_value <= 60:
                return 0.5
            else:
                return 0.0

        return 0.0
