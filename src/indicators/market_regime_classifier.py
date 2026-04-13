"""
市场状态分类器

功能:
  - 基于 ADX + BOLL 带宽 + ATR 判断市场状态
  - 输出: TRENDING_BULL / TRENDING_BEAR / RANGING / BREAKOUT_WATCH / VOLATILE
  - 状态切换冷却（防止频繁摇摆）
  - 套件选择建议

依赖: time, logging
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class MarketRegimeClassifier:
    """
    市场状态分类器
    基于 ADX + BOLL 带宽 + ATR 综合判断
    """

    REGIME_TRENDING_BULL = "TRENDING_BULL"
    REGIME_TRENDING_BEAR = "TRENDING_BEAR"
    REGIME_RANGING = "RANGING"
    REGIME_BREAKOUT_WATCH = "BREAKOUT_WATCH"
    REGIME_VOLATILE = "VOLATILE"

    def __init__(self, config: dict):
        self.adx_trending_threshold = config.get("adx_trending_threshold", 25)
        self.adx_ranging_threshold = config.get("adx_ranging_threshold", 22)
        self.boll_trending_bandwidth_ratio = config.get(
            "boll_bandwidth_trending_ratio", 1.3
        )
        self.switch_cooldown_bars = config.get("suite_switch_cooldown_bars_1h", 4)
        self.atr_volatile_threshold = config.get("atr_volatile_threshold", 0.025)

        # 状态记忆（防止频繁切换）
        self._last_regime: Optional[str] = None
        self._last_regime_bar_count: int = 0
        self._regime_history: list = []

    def classify(
        self,
        symbol: str,
        adx_1h: float,
        atr_pct_1h: float,
        boll_bandwidth_4h: float,
        boll_bandwidth_4h_mean: float,
        macd_histogram_4h: float,
        macd_histogram_4h_prev: float = 0,
    ) -> str:
        """
        分类当前市场状态

        Args:
            symbol: 交易对
            adx_1h: 1H ADX 值
            atr_pct_1h: 1H ATR 百分比
            boll_bandwidth_4h: 4H BOLL 带宽
            boll_bandwidth_4h_mean: 4H BOLL 带宽历史均值
            macd_histogram_4h: 4H MACD histogram
            macd_histogram_4h_prev: 4H MACD histogram 前一根
        """
        # 极度波动检测（优先级最高）
        if atr_pct_1h > self.atr_volatile_threshold:
            return self._update_regime(self.REGIME_VOLATILE, symbol)

        # BOLL squeeze 检测（潜在爆发前）
        if boll_bandwidth_4h < 0.02:
            return self._update_regime(self.REGIME_BREAKOUT_WATCH, symbol)

        # 趋势检测
        is_trending = adx_1h >= self.adx_trending_threshold
        bandwidth_ratio = (
            boll_bandwidth_4h / boll_bandwidth_4h_mean
            if boll_bandwidth_4h_mean > 0
            else 1.0
        )
        is_expanding = bandwidth_ratio >= self.boll_trending_bandwidth_ratio

        if is_trending and is_expanding:
            # 判断趋势方向
            if macd_histogram_4h > 0:
                return self._update_regime(self.REGIME_TRENDING_BULL, symbol)
            else:
                return self._update_regime(self.REGIME_TRENDING_BEAR, symbol)

        # 震荡检测
        is_ranging = adx_1h <= self.adx_ranging_threshold
        if is_ranging:
            return self._update_regime(self.REGIME_RANGING, symbol)

        # 过渡状态（ADX 在 ranging_threshold ~ trending_threshold 之间）
        # 保持上一状态或默认震荡
        if self._last_regime and self._last_regime_bar_count < self.switch_cooldown_bars:
            self._last_regime_bar_count += 1
            return self._last_regime

        return self._update_regime(self.REGIME_RANGING, symbol)

    def _update_regime(self, new_regime: str, symbol: str) -> str:
        if new_regime != self._last_regime:
            logger.debug(
                f"[{symbol}] Regime changed: {self._last_regime} → {new_regime}"
            )
            self._last_regime = new_regime
            self._last_regime_bar_count = 0
            self._regime_history.append(new_regime)
        else:
            self._last_regime_bar_count += 1
        return new_regime

    def select_suite(self, regime: str) -> str:
        """
        根据市场状态选择指标套件
        返回: "RSI_MRV" / "BOLL_MBV" / "NONE"
        """
        suite_map = {
            self.REGIME_TRENDING_BULL: "RSI_MRV",
            self.REGIME_TRENDING_BEAR: "RSI_MRV",
            self.REGIME_RANGING: "BOLL_MBV",
            self.REGIME_BREAKOUT_WATCH: "BOLL_MBV",
            self.REGIME_VOLATILE: "NONE",  # 禁止开仓
        }
        return suite_map.get(regime, "BOLL_MBV")

    def is_entry_allowed(self, regime: str) -> bool:
        """VOLATILE 状态禁止入场"""
        return regime not in [self.REGIME_VOLATILE]

    @staticmethod
    def is_trending(regime: str) -> bool:
        """判断是否为趋势型市场"""
        return regime in [
            MarketRegimeClassifier.REGIME_TRENDING_BULL,
            MarketRegimeClassifier.REGIME_TRENDING_BEAR,
        ]

    @staticmethod
    def is_ranging(regime: str) -> bool:
        """判断是否为震荡型市场"""
        return regime in [
            MarketRegimeClassifier.REGIME_RANGING,
            MarketRegimeClassifier.REGIME_BREAKOUT_WATCH,
        ]
