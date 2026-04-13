"""
自适应套件选择器

根据市场状态选择最合适的指标套件并执行分析:
  - 趋势型市场 → RSI_MRV 套件 (MACD+RSI+BOLL共振)
  - 震荡型市场 → BOLL_MBV 套件 (MACD+BOLL+RSI共振)
  - 极度波动 → 禁止入场
"""

from __future__ import annotations

import logging
import math
from typing import Dict, Optional

from src.indicators.rsi_indicator import RSIIndicator
from src.indicators.market_regime_classifier import MarketRegimeClassifier
from src.indicators.rsi_mrv_analyzer import RSIMRVAnalyzer
from src.indicators.boll_mbv_analyzer import BOLLMBVAnalyzer

logger = logging.getLogger(__name__)


class AdaptiveSuiteSelector:
    """
    根据市场状态选择最合适的指标套件并执行分析
    """

    def __init__(
        self,
        config: dict,
        rsi_indicator: RSIIndicator,
        regime_classifier: MarketRegimeClassifier,
        rsi_mrv_analyzer: RSIMRVAnalyzer,
        boll_mbv_analyzer: BOLLMBVAnalyzer,
    ):
        self.config = config
        self.rsi = rsi_indicator
        self.classifier = regime_classifier
        self.rsi_mrv = rsi_mrv_analyzer
        self.boll_mbv = boll_mbv_analyzer

        # 套件评分最低门槛
        self.min_score_RSI_MRV = 0.55
        self.min_score_BOLL_MBV = 0.45

    def select_and_analyze(
        self,
        symbol: str,
        direction: str,
        signal_type_1h: str,
        market_data: dict,
    ) -> dict:
        """
        主入口：选择套件并执行分析

        Args:
            symbol: 交易对
            direction: 交易方向 (long/short)
            signal_type_1h: 1H 信号类型
            market_data: 市场数据包 (包含所有需要的指标)

        返回: {
            "selected_suite": str,
            "regime": str,
            "suite_result": dict | None,
            "final_gate_pass": bool,
            "entry_score": float,
            "block_reason": str | None
        }
        """
        # Step 1: 市场状态分类
        regime = self.classifier.classify(
            symbol=symbol,
            adx_1h=market_data.get("adx_1h", 20),
            atr_pct_1h=market_data.get("atr_pct_1h", 0.01),
            boll_bandwidth_4h=market_data.get("boll_bandwidth_4h", 0.05),
            boll_bandwidth_4h_mean=market_data.get("boll_bandwidth_4h_mean", 0.05),
            macd_histogram_4h=market_data.get("macd_histogram_4h", 0),
            macd_histogram_4h_prev=market_data.get("macd_histogram_4h_prev", 0),
        )

        # Step 2: 检查是否允许入场
        if not self.classifier.is_entry_allowed(regime):
            return {
                "selected_suite": "NONE",
                "regime": regime,
                "suite_result": None,
                "final_gate_pass": False,
                "entry_score": 0.0,
                "block_reason": f"VOLATILE_REGIME: {regime}",
            }

        # Step 3: 选择套件
        selected_suite = self.classifier.select_suite(regime)

        # Step 4: 执行套件分析
        if selected_suite == "RSI_MRV":
            suite_result = self._analyze_rsi_mrv(
                symbol, direction, signal_type_1h, market_data
            )

            # RSI gate 必须通过
            if not suite_result.get("rsi_gate_pass", False):
                return {
                    "selected_suite": "RSI_MRV",
                    "regime": regime,
                    "suite_result": suite_result,
                    "final_gate_pass": False,
                    "entry_score": suite_result.get("suite_score", 0.0),
                    "block_reason": (
                        f"RSI_GATE_FAIL: "
                        f"RSI_4h={suite_result.get('rsi_4h', 0):.1f}, "
                        f"RSI_1h={suite_result.get('rsi_1h', 0):.1f}, "
                        f"RSI_15m={suite_result.get('rsi_15m', 0):.1f}"
                    ),
                }

            # 评分门槛
            score = suite_result.get("suite_score", 0.0)
            if score < self.min_score_RSI_MRV:
                return {
                    "selected_suite": "RSI_MRV",
                    "regime": regime,
                    "suite_result": suite_result,
                    "final_gate_pass": False,
                    "entry_score": score,
                    "block_reason": (
                        f"RSI_MRV_SCORE_LOW: {score:.3f} < {self.min_score_RSI_MRV}"
                    ),
                }

        elif selected_suite == "BOLL_MBV":
            suite_result = self._analyze_boll_mbv(
                symbol, direction, market_data
            )

            # BOLL squeeze/expanding 或无效信号禁止入场
            if not suite_result.get("entry_allowed", True):
                return {
                    "selected_suite": "BOLL_MBV",
                    "regime": regime,
                    "suite_result": suite_result,
                    "final_gate_pass": False,
                    "entry_score": suite_result.get("suite_score", 0.0),
                    "block_reason": (
                        f"BOLL_BANDWIDTH_STATE: "
                        f"{suite_result.get('bandwidth_state', 'unknown')} "
                        f"resonance={suite_result.get('resonance_type', 'unknown')}"
                    ),
                }

            # 评分门槛
            score = suite_result.get("suite_score", 0.0)
            if score < self.min_score_BOLL_MBV:
                return {
                    "selected_suite": "BOLL_MBV",
                    "regime": regime,
                    "suite_result": suite_result,
                    "final_gate_pass": False,
                    "entry_score": score,
                    "block_reason": (
                        f"BOLL_MBV_SCORE_LOW: {score:.3f} < {self.min_score_BOLL_MBV}"
                    ),
                }
        else:
            # NONE - 不允许入场
            return {
                "selected_suite": "NONE",
                "regime": regime,
                "suite_result": None,
                "final_gate_pass": False,
                "entry_score": 0.0,
                "block_reason": f"SUITE_NONE: regime={regime}",
            }

        return {
            "selected_suite": selected_suite,
            "regime": regime,
            "suite_result": suite_result,
            "final_gate_pass": True,
            "entry_score": suite_result.get("suite_score", 0.0),
            "block_reason": None,
        }

    def _analyze_rsi_mrv(
        self,
        symbol: str,
        direction: str,
        signal_type_1h: str,
        market_data: dict,
    ) -> dict:
        """执行 RSI_MRV 套件分析"""
        # 从 market_data 提取 RSI 值
        rsi_4h = market_data.get("rsi_4h", float("nan"))
        rsi_1h = market_data.get("rsi_1h", 50.0)
        rsi_15m = market_data.get("rsi_15m", 50.0)
        rsi_slope_1h = market_data.get("rsi_slope_1h", 0.0)

        # 如果没有预计算的 RSI，尝试从 candles 计算
        if math.isnan(rsi_4h) and market_data.get("candles_4h"):
            rsi_4h = self.rsi.get_rsi_for_timeframe(symbol, "4h", market_data["candles_4h"])
        if math.isnan(rsi_1h) and market_data.get("candles_1h"):
            rsi_1h = self.rsi.get_rsi_for_timeframe(symbol, "1h", market_data["candles_1h"])
        if math.isnan(rsi_15m) and market_data.get("candles_15m"):
            rsi_15m = self.rsi.get_rsi_for_timeframe(symbol, "15m", market_data["candles_15m"])

        # RSI slope
        if rsi_slope_1h == 0.0 and market_data.get("candles_1h"):
            rsi_slope_1h = self.rsi.get_rsi_slope(symbol, "1h", market_data["candles_1h"])

        # 背离检测
        divergence = {}
        if market_data.get("candles_1h"):
            divergence = self.rsi.detect_divergence(
                market_data["candles_1h"], "1h", direction
            )

        return self.rsi_mrv.analyze(
            symbol=symbol,
            direction=direction,
            signal_type_1h=signal_type_1h,
            rsi_4h=rsi_4h,
            rsi_1h=rsi_1h,
            rsi_15m=rsi_15m,
            rsi_slope_1h=rsi_slope_1h,
            divergence=divergence,
            macd_histogram_4h=market_data.get("macd_histogram_4h", 0),
            macd_histogram_1h=market_data.get("macd_histogram_1h", 0),
            current_price=market_data.get("current_price", 0),
            boll_upper_4h=market_data.get("boll_upper_4h", 0),
            boll_lower_4h=market_data.get("boll_lower_4h", 0),
            boll_mid_4h=market_data.get("boll_mid_4h", 0),
            volume_ratio=market_data.get("volume_ratio", 1.0),
        )

    def _analyze_boll_mbv(
        self,
        symbol: str,
        direction: str,
        market_data: dict,
    ) -> dict:
        """执行 BOLL_MBV 套件分析"""
        return self.boll_mbv.analyze(
            symbol=symbol,
            direction=direction,
            current_price=market_data.get("current_price", 0),
            boll_upper_4h=market_data.get("boll_upper_4h", 0),
            boll_lower_4h=market_data.get("boll_lower_4h", 0),
            boll_mid_4h=market_data.get("boll_mid_4h", 0),
            boll_bandwidth_4h=market_data.get("boll_bandwidth_4h", 0.05),
            boll_bandwidth_4h_mean=market_data.get("boll_bandwidth_4h_mean", 0.05),
            macd_histogram_4h=market_data.get("macd_histogram_4h", 0),
            macd_histogram_1h=market_data.get("macd_histogram_1h", 0),
            rsi_1h=market_data.get("rsi_1h", 50.0),
            rsi_4h=market_data.get("rsi_4h", 50.0),
            volume_ratio=market_data.get("volume_ratio", 1.0),
        )
