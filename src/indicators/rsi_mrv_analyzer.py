"""
MACD + RSI + BOLL 共振套件分析器 (RSI_MRV)

适用于趋势型市场 (ADX > 25, 波动有序)
核心思路: RSI 确认动量延续, MACD 提供方向, BOLL 确认价格位置
基于 RSI＋布林带期市共振战法:
  - 趋势加速: 价格沿BOLL上/下轨运行 + RSI在50以上/以下持续
  - 多头共振: 价格站上BOLL中轨 + RSI从30以下回升
  - 空头共振: 价格跌破BOLL中轨 + RSI从70以上回落
"""

from __future__ import annotations

import logging
import math
from typing import Dict, Optional

from src.indicators.rsi_indicator import RSIIndicator

logger = logging.getLogger(__name__)


class RSIMRVAnalyzer:
    """
    MACD + RSI + BOLL 共振套件分析器
    适用于趋势型市场
    """

    def __init__(self, config: dict, rsi_indicator: RSIIndicator):
        self.config = config
        self.rsi = rsi_indicator

        # 权重 - 从 RSI_MRV_suite 读取
        scoring = config.get("scoring_weights", {}).get("RSI_MRV_suite", {})
        self.w = {
            "macd_4h": scoring.get("weight_4h_direction", 0.25),
            "macd_1h": scoring.get("weight_1h_direction", 0.10),
            "rsi_4h": scoring.get("weight_rsi_4h", 0.10),
            "rsi_1h": scoring.get("weight_rsi_1h", 0.15),
            "rsi_div": scoring.get("weight_rsi_divergence", 0.05),
            "boll_rsi": scoring.get("weight_boll_rsi", 0.20),  # 替换原VWAP权重
            "volume": scoring.get("weight_volume", 0.15),
        }

        self.rsi_gate_cfg = config.get("rsi_config", {}).get("rsi_gate", {})
        self.rsi_flip_cfg = config.get("rsi_config", {}).get("rsi_flip_override", {})

    def analyze(
        self,
        symbol: str,
        direction: str,
        signal_type_1h: str,
        rsi_4h: float,
        rsi_1h: float,
        rsi_15m: float,
        rsi_slope_1h: float,
        divergence: dict,
        macd_histogram_4h: float,
        macd_histogram_1h: float,
        current_price: float = 0.0,
        boll_upper_4h: float = 0.0,
        boll_lower_4h: float = 0.0,
        boll_mid_4h: float = 0.0,
        volume_ratio: float = 1.0,
    ) -> dict:
        """
        RSI_MRV 套件分析（使用BOLL+RSI共振替代VWAP）

        返回: {
            "suite": "RSI_MRV",
            "suite_score": float,
            "rsi_gate_pass": bool,
            "rsi_4h": float,
            "rsi_1h": float,
            "rsi_15m": float,
            "rsi_slope_1h": float,
            "divergence": dict,
            "resonance_type": str,
            "detail": dict
        }
        """
        # RSI 三时间框架门控
        rsi_gate_pass = self._check_rsi_gate(
            direction, signal_type_1h, rsi_4h, rsi_1h, rsi_15m
        )

        # 分项评分
        macd_4h_score = self._score_macd(macd_histogram_4h, direction)
        macd_1h_score = self._score_macd(macd_histogram_1h, direction)
        rsi_4h_score = self.rsi.compute_rsi_score(rsi_4h, direction)
        rsi_1h_score = self.rsi.compute_rsi_score(rsi_1h, direction)

        # 背离加分/减分
        div_bonus = self._compute_divergence_bonus(divergence, direction)

        # BOLL+RSI 共振评分（替换原VWAP评分）
        boll_rsi_score, resonance_type = self._score_boll_rsi_resonance(
            current_price, boll_upper_4h, boll_lower_4h, boll_mid_4h,
            rsi_1h, rsi_4h, direction,
        )

        # 成交量评分
        volume_score = min(volume_ratio / 1.5, 1.0) if volume_ratio > 0 else 0.0

        # 综合评分
        suite_score = (
            self.w["macd_4h"] * macd_4h_score
            + self.w["macd_1h"] * macd_1h_score
            + self.w["rsi_4h"] * rsi_4h_score
            + self.w["rsi_1h"] * rsi_1h_score
            + self.w["rsi_div"] * max(div_bonus, -0.20)
            + self.w["boll_rsi"] * boll_rsi_score
            + self.w["volume"] * volume_score
        )

        suite_score = max(0.0, min(1.0, suite_score))

        return {
            "suite": "RSI_MRV",
            "suite_score": suite_score,
            "rsi_gate_pass": rsi_gate_pass,
            "rsi_4h": rsi_4h,
            "rsi_1h": rsi_1h,
            "rsi_15m": rsi_15m,
            "rsi_slope_1h": rsi_slope_1h,
            "divergence": divergence,
            "resonance_type": resonance_type,
            "detail": {
                "macd_4h_score": macd_4h_score,
                "macd_1h_score": macd_1h_score,
                "rsi_4h_score": rsi_4h_score,
                "rsi_1h_score": rsi_1h_score,
                "div_bonus": div_bonus,
                "boll_rsi_score": boll_rsi_score,
                "resonance_type": resonance_type,
                "volume_score": volume_score,
            },
        }

    def _score_boll_rsi_resonance(
        self,
        price: float,
        upper: float,
        lower: float,
        mid: float,
        rsi_1h: float,
        rsi_4h: float,
        direction: str,
    ) -> tuple:
        """BOLL+RSI 共振评分（趋势型市场版本）"""
        if upper <= lower or mid <= 0 or price <= 0:
            return 0.5, "no_boll_data"  # 无BOLL数据时给中等分

        band_width = upper - lower
        relative_pos = (price - lower) / max(band_width, 1e-12)
        relative_pos = max(-0.2, min(1.2, relative_pos))

        raw_score = 0.0
        resonance_type = "none"

        if direction == "long":
            above_mid = price >= mid
            along_upper = relative_pos > 0.75
            near_lower = relative_pos < 0.15

            if along_upper and rsi_1h > 50:
                # 趋势加速（最强信号）
                raw_score = 1.0
                resonance_type = "trend_acceleration_long"
            elif above_mid and rsi_1h >= 30 and rsi_1h <= 70:
                # 多头共振
                raw_score = 1.0
                resonance_type = "bullish_resonance"
            elif near_lower and rsi_1h < 30:
                # 超卖反弹
                raw_score = 0.85
                resonance_type = "oversold_bounce"
            elif above_mid and rsi_1h > 50:
                raw_score = 0.65
                resonance_type = "above_mid_rsi_ok"
            elif above_mid and rsi_1h >= 30:
                raw_score = 0.5
                resonance_type = "partial_bullish_resonance"
            elif rsi_1h > 70:
                raw_score = 0.2
                resonance_type = "overbought_caution"
            elif above_mid:
                raw_score = 0.35
                resonance_type = "above_mid_only"
            else:
                raw_score = 0.1
                resonance_type = "weak_long_signal"

        elif direction == "short":
            below_mid = price < mid
            along_lower = relative_pos < 0.25
            near_upper = relative_pos > 0.85

            if along_lower and rsi_1h < 50:
                # 趋势加速
                raw_score = 1.0
                resonance_type = "trend_acceleration_short"
            elif below_mid and rsi_1h <= 70 and rsi_1h >= 30:
                # 空头共振
                raw_score = 1.0
                resonance_type = "bearish_resonance"
            elif near_upper and rsi_1h > 70:
                # 超买回调
                raw_score = 0.85
                resonance_type = "overbought_pullback"
            elif below_mid and rsi_1h < 50:
                raw_score = 0.65
                resonance_type = "below_mid_rsi_ok"
            elif below_mid and rsi_1h <= 70:
                raw_score = 0.5
                resonance_type = "partial_bearish_resonance"
            elif rsi_1h < 30:
                raw_score = 0.2
                resonance_type = "oversold_caution"
            elif below_mid:
                raw_score = 0.35
                resonance_type = "below_mid_only"
            else:
                raw_score = 0.1
                resonance_type = "weak_short_signal"

        # 4H RSI方向加成
        if direction == "long" and rsi_4h > 50:
            raw_score = min(1.0, raw_score * 1.05)
        elif direction == "short" and rsi_4h < 50:
            raw_score = min(1.0, raw_score * 1.05)

        return max(0.0, min(1.0, raw_score)), resonance_type

    def _check_rsi_gate(
        self,
        direction: str,
        signal_type: str,
        rsi_4h: float,
        rsi_1h: float,
        rsi_15m: float,
    ) -> bool:
        """三时间框架 RSI 联合门控"""
        cfg = self.rsi_gate_cfg
        flip_cfg = self.rsi_flip_cfg
        is_flip = signal_type in ["flip_bullish", "flip_bearish"]

        if direction == "long":
            rsi_4h_min = (
                flip_cfg.get("flip_bullish_rsi_4h_min", 42)
                if is_flip
                else cfg.get("long_rsi_4h_min", 45)
            )
            rsi_1h_min = (
                flip_cfg.get("flip_bullish_rsi_1h_min", 42)
                if is_flip
                else cfg.get("long_rsi_1h_min", 45)
            )
            rsi_1h_max = cfg.get("long_rsi_1h_max", 70)
            rsi_15m_min = cfg.get("long_rsi_15m_min", 48)

            if math.isnan(rsi_4h) or math.isnan(rsi_1h) or math.isnan(rsi_15m):
                return False

            return (
                rsi_4h >= rsi_4h_min
                and rsi_1h >= rsi_1h_min
                and rsi_1h <= rsi_1h_max
                and rsi_15m >= rsi_15m_min
            )

        elif direction == "short":
            rsi_4h_max = (
                flip_cfg.get("flip_bearish_rsi_4h_max", 58)
                if is_flip
                else cfg.get("short_rsi_4h_max", 55)
            )
            rsi_1h_max = (
                flip_cfg.get("flip_bearish_rsi_1h_max", 58)
                if is_flip
                else cfg.get("short_rsi_1h_max", 55)
            )
            rsi_1h_min = cfg.get("short_rsi_1h_min", 30)
            rsi_15m_max = cfg.get("short_rsi_15m_max", 52)

            if math.isnan(rsi_4h) or math.isnan(rsi_1h) or math.isnan(rsi_15m):
                return False

            return (
                rsi_4h <= rsi_4h_max
                and rsi_1h <= rsi_1h_max
                and rsi_1h >= rsi_1h_min
                and rsi_15m <= rsi_15m_max
            )

        return False

    @staticmethod
    def _score_macd(histogram: float, direction: str) -> float:
        """MACD histogram 方向评分"""
        if direction == "long":
            if histogram > 0:
                return 1.0
            elif histogram > -0.0002:
                return 0.3  # 接近 0
            else:
                return 0.0
        elif direction == "short":
            if histogram < 0:
                return 1.0
            elif histogram < 0.0002:
                return 0.3
            else:
                return 0.0
        return 0.0

    @staticmethod
    def _compute_divergence_bonus(divergence: dict, direction: str) -> float:
        """计算背离加分/减分"""
        if not divergence:
            return 0.0

        div_bonus = 0.0
        strength = divergence.get("divergence_strength", 0.0)

        if direction == "long":
            if divergence.get("regular_bullish"):
                div_bonus = +0.15 * strength
            elif divergence.get("regular_bearish"):
                div_bonus = -0.20  # 看空背离，对做多是减分
            elif divergence.get("hidden_bullish"):
                div_bonus = +0.10

        elif direction == "short":
            if divergence.get("regular_bearish"):
                div_bonus = +0.15 * strength
            elif divergence.get("regular_bullish"):
                div_bonus = -0.20
            elif divergence.get("hidden_bearish"):
                div_bonus = +0.10

        return div_bonus
