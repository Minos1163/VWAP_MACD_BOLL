"""
MACD + BOLL + RSI 共振套件分析器 (BOLL_MBV)

适用于震荡型市场 (ADX ≤ 25, 价格在 BOLL 内振荡)
核心思路: BOLL 提供边界约束, MACD 判断局部动量, RSI 确认超买/超卖
基于 RSI＋布林带期市共振战法:
  - 多头共振: 价格站上中轨 + RSI从30以下回升
  - 空头共振: 价格跌破中轨 + RSI从70以上回落
  - 超卖反弹: 价格触及下轨 + RSI<30
  - 超买回调: 价格触及上轨 + RSI>70
  - 趋势加速: 价格沿轨运行 + RSI确认方向
  - 无效信号: BOLL缩口 + RSI在40-60 → 观望
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class BOLLMBVAnalyzer:
    """
    MACD + BOLL + RSI 共振套件分析器
    适用于震荡型市场
    """

    def __init__(self, config: dict):
        self.config = config

        # 权重 - 从 BOLL_MBV_suite 读取
        scoring = config.get("scoring_weights", {}).get("BOLL_MBV_suite", {})
        self.w = {
            "macd_4h": scoring.get("weight_4h_direction", 0.25),
            "macd_1h": scoring.get("weight_1h_direction", 0.15),
            "boll_pos": scoring.get("weight_boll_position", 0.20),
            "boll_bw": scoring.get("weight_boll_bandwidth", 0.05),
            "rsi": scoring.get("weight_rsi", 0.20),  # 替换原VWAP权重
            "volume": scoring.get("weight_volume", 0.15),
        }

    def analyze(
        self,
        symbol: str,
        direction: str,
        current_price: float,
        boll_upper_4h: float,
        boll_lower_4h: float,
        boll_mid_4h: float,
        boll_bandwidth_4h: float,
        boll_bandwidth_4h_mean: float,
        macd_histogram_4h: float,
        macd_histogram_1h: float,
        rsi_1h: float = 50.0,
        rsi_4h: float = 50.0,
        volume_ratio: float = 1.0,
    ) -> dict:
        """
        BOLL_MBV 套件分析（使用BOLL+RSI共振替代VWAP）

        返回: {
            "suite": "BOLL_MBV",
            "suite_score": float,
            "boll_pos_score": float,
            "bandwidth_state": str,
            "bw_quality_score": float,
            "entry_allowed": bool,
            "resonance_type": str,
            "detail": dict
        }
        """
        # BOLL 位置评分
        boll_pos_score = self._score_boll_position(
            current_price, boll_upper_4h, boll_lower_4h, boll_mid_4h, direction
        )

        # BOLL 带宽状态
        bandwidth_state = self._classify_bandwidth(boll_bandwidth_4h)
        bw_quality_score = self._score_bandwidth(bandwidth_state)

        # MACD 方向评分
        macd_4h_score = self._score_macd(macd_histogram_4h, direction)
        macd_1h_score = self._score_macd(macd_histogram_1h, direction)

        # RSI 共振评分（替换原VWAP评分）
        rsi_score, resonance_type = self._score_boll_rsi_resonance(
            current_price, boll_upper_4h, boll_lower_4h, boll_mid_4h,
            rsi_1h, rsi_4h, direction, boll_bandwidth_4h,
        )

        # 成交量评分
        volume_score = min(volume_ratio / 1.5, 1.0) if volume_ratio > 0 else 0.0

        # 综合评分
        suite_score = (
            self.w["macd_4h"] * macd_4h_score
            + self.w["macd_1h"] * macd_1h_score
            + self.w["boll_pos"] * boll_pos_score
            + self.w["boll_bw"] * bw_quality_score
            + self.w["rsi"] * rsi_score
            + self.w["volume"] * volume_score
        )

        # BOLL squeeze/expanding 时禁止入场
        entry_allowed = bandwidth_state not in ["squeeze", "expanding"]
        
        # 无效信号: BOLL缩口 + RSI在40-60 → 禁止入场
        if boll_bandwidth_4h < 0.02 and 40 <= rsi_1h <= 60:
            entry_allowed = False
            resonance_type = "invalid_squeeze_neutral_rsi"

        suite_score = max(0.0, min(1.0, suite_score))

        return {
            "suite": "BOLL_MBV",
            "suite_score": suite_score,
            "boll_pos_score": boll_pos_score,
            "bandwidth_state": bandwidth_state,
            "bw_quality_score": bw_quality_score,
            "entry_allowed": entry_allowed,
            "resonance_type": resonance_type,
            "detail": {
                "macd_4h_score": macd_4h_score,
                "macd_1h_score": macd_1h_score,
                "boll_pos_score": boll_pos_score,
                "bw_quality_score": bw_quality_score,
                "rsi_score": rsi_score,
                "resonance_type": resonance_type,
                "volume_score": volume_score,
                "bandwidth_state": bandwidth_state,
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
        bandwidth: float,
    ) -> tuple:
        """BOLL+RSI 共振评分"""
        if upper <= lower or mid <= 0 or price <= 0:
            return 0.0, "no_data"

        band_width = upper - lower
        relative_pos = (price - lower) / max(band_width, 1e-12)
        relative_pos = max(-0.2, min(1.2, relative_pos))

        raw_score = 0.0
        resonance_type = "none"

        if direction == "long":
            above_mid = price >= mid
            near_lower = relative_pos < 0.15
            along_upper = relative_pos > 0.75

            if above_mid and rsi_1h >= 30 and rsi_1h <= 70:
                # 多头共振
                raw_score = 1.0
                resonance_type = "bullish_resonance"
            elif near_lower and rsi_1h < 30:
                # 超卖反弹
                raw_score = 0.85
                resonance_type = "oversold_bounce"
            elif along_upper and rsi_1h > 50:
                # 趋势加速
                raw_score = 1.0
                resonance_type = "trend_acceleration_long"
            elif above_mid and rsi_1h >= 30:
                raw_score = 0.6
                resonance_type = "partial_bullish_resonance"
            elif near_lower and rsi_1h <= 40:
                raw_score = 0.4
                resonance_type = "near_lower_low_rsi"
            elif above_mid and rsi_1h > 50:
                raw_score = 0.45
                resonance_type = "above_mid_rsi_ok"
            elif rsi_1h > 70:
                raw_score = 0.15
                resonance_type = "overbought_caution"
            elif above_mid:
                raw_score = 0.3
                resonance_type = "above_mid_only"
            else:
                raw_score = 0.1
                resonance_type = "weak_long_signal"

        elif direction == "short":
            below_mid = price < mid
            near_upper = relative_pos > 0.85
            along_lower = relative_pos < 0.25

            if below_mid and rsi_1h <= 70 and rsi_1h >= 30:
                # 空头共振
                raw_score = 1.0
                resonance_type = "bearish_resonance"
            elif near_upper and rsi_1h > 70:
                # 超买回调
                raw_score = 0.85
                resonance_type = "overbought_pullback"
            elif along_lower and rsi_1h < 50:
                # 趋势加速
                raw_score = 1.0
                resonance_type = "trend_acceleration_short"
            elif below_mid and rsi_1h <= 70:
                raw_score = 0.6
                resonance_type = "partial_bearish_resonance"
            elif near_upper and rsi_1h >= 60:
                raw_score = 0.4
                resonance_type = "near_upper_high_rsi"
            elif below_mid and rsi_1h < 50:
                raw_score = 0.45
                resonance_type = "below_mid_rsi_ok"
            elif rsi_1h < 30:
                raw_score = 0.15
                resonance_type = "oversold_caution"
            elif below_mid:
                raw_score = 0.3
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

    @staticmethod
    def _score_boll_position(
        price: float,
        upper: float,
        lower: float,
        mid: float,
        direction: str,
    ) -> float:
        """BOLL 位置评分"""
        band_width = upper - lower
        if band_width <= 0:
            return 0.0

        # 价格在带内的相对位置 (0=下轨, 1=上轨)
        relative_pos = (price - lower) / band_width

        if direction == "long":
            # 做多：价格越接近下轨越好（支撑弹性区）
            if price < lower:
                return -1.0  # 下轨外
            elif relative_pos < 0.15:
                return 1.0  # 支撑弹性区
            elif relative_pos < 0.35:
                return 0.8  # 下半区
            elif relative_pos < 0.50:
                return 0.6  # 接近中线下方
            elif relative_pos < 0.65:
                return 0.3  # 接近中线上方
            elif relative_pos < 0.80:
                return 0.1  # 偏上
            else:
                return 0.0  # 接近上轨

        elif direction == "short":
            # 做空：价格越接近上轨越好（阻力回落区）
            if price > upper:
                return -1.0  # 上轨外
            elif relative_pos > 0.85:
                return 1.0  # 阻力回落区
            elif relative_pos > 0.65:
                return 0.8  # 上半区
            elif relative_pos > 0.50:
                return 0.6  # 接近中线上方
            elif relative_pos > 0.35:
                return 0.3  # 接近中线下方
            elif relative_pos > 0.20:
                return 0.1  # 偏下
            else:
                return 0.0  # 接近下轨

        return 0.0

    @staticmethod
    def _classify_bandwidth(bandwidth: float) -> str:
        """BOLL 带宽状态分类"""
        if bandwidth < 0.02:
            return "squeeze"
        elif bandwidth < 0.04:
            return "narrow"
        elif bandwidth < 0.08:
            return "normal"
        elif bandwidth < 0.12:
            return "wide"
        else:
            return "expanding"

    @staticmethod
    def _score_bandwidth(state: str) -> float:
        """带宽质量评分"""
        scores = {
            "normal": 1.0,
            "narrow": 0.6,
            "wide": 0.4,
            "squeeze": 0.0,
            "expanding": 0.2,
        }
        return scores.get(state, 0.0)

    @staticmethod
    def _score_macd(histogram: float, direction: str) -> float:
        if direction == "long":
            return 1.0 if histogram > 0 else (0.3 if histogram > -0.0002 else 0.0)
        elif direction == "short":
            return 1.0 if histogram < 0 else (0.3 if histogram < 0.0002 else 0.0)
        return 0.0

    @staticmethod
    def _score_vwap(vwap_score: float, direction: str) -> float:
        if vwap_score > 0.15:
            return 1.0
        elif vwap_score > 0.10:
            return 0.7
        elif vwap_score > 0.05:
            return 0.4
        else:
            return 0.0
