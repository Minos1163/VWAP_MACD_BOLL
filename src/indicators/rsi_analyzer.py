"""
RSI 多时间框架分析器
功能:
  - 三时间框架 RSI 计算
  - RSI 动量评分
  - 三时间框架门控
  - 背离检测
  - 极值检测
  - RSI 穿越 50 检测
"""

import math
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RSIResult:
    """RSI 分析结果"""
    rsi_4h: float
    rsi_1h: float
    rsi_15m: float
    rsi_slope_1h: float
    rsi_slope_15m: float
    momentum_score: float           # 0.0 ~ 1.0
    gate_pass: bool
    fail_reason: Optional[str]
    divergence_type: Optional[str]  # regular_bullish/regular_bearish/hidden_bullish/hidden_bearish
    divergence_bonus: float
    rsi_recently_crossed_above_50: bool
    rsi_recently_crossed_below_50: bool
    extreme_status: Optional[str]   # extreme_overbought/extreme_oversold/None


class RSIAnalyzer:
    """
    RSI 多时间框架分析器
    提供 RSI 计算、评分、门控和背离检测功能
    """

    def __init__(self, config: dict):
        """
        初始化 RSI 分析器
        
        Args:
            config: 配置字典，包含:
                - rsi_config: RSI 配置字典
        """
        rsi_cfg = config.get("rsi_config", {})
        
        # 各时间框架周期
        self.period_map = {
            "4h":  rsi_cfg.get("period_4h", 21),
            "1h":  rsi_cfg.get("period_1h", 14),
            "15m": rsi_cfg.get("period_15m", 7),
        }
        
        # 超买超卖阈值
        self.overbought  = rsi_cfg.get("overbought", 70)
        self.oversold    = rsi_cfg.get("oversold", 30)
        self.extreme_ob  = rsi_cfg.get("extreme_overbought", 78)
        self.extreme_os  = rsi_cfg.get("extreme_oversold", 22)
        self.neutral_h   = rsi_cfg.get("neutral_high", 55)
        self.neutral_l   = rsi_cfg.get("neutral_low", 45)

        # 门控配置
        self.gate_cfg = rsi_cfg.get("gate", {})
        
        # flip 族豁免配置
        self.flip_cfg = rsi_cfg.get("flip_override", {})
        
        # 背离检测配置
        self.div_cfg = rsi_cfg.get("divergence", {})

        # 做多 RSI 评分映射（阈值, 分数）
        self._long_score_thresholds = [
            (25,  0.0),   # 极度超卖
            (35,  0.6),   # 超卖反弹区
            (45,  0.8),   # 弱势恢复区
            (60,  1.0),   # 动量健康区
            (65,  0.8),   # 偏热区
            (70,  0.4),   # 过热区
            (999, 0.0),   # 超买禁止
        ]

        # 做空 RSI 评分映射
        self._short_score_thresholds = [
            (30,  0.0),   # 超卖禁止
            (35,  0.4),   # 过冷区
            (40,  0.8),   # 偏弱区
            (55,  1.0),   # 动量健康区
            (65,  0.8),   # 偏强区
            (75,  0.6),   # 超买回落区
            (999, 0.0),   # 极度超买
        ]

    # ──────────────────────────────────────
    # RSI 计算核心
    # ──────────────────────────────────────

    def _wilder_rsi(self, closes: list, period: int) -> list:
        """
        Wilder 平滑 RSI 计算
        
        Args:
            closes: 收盘价列表
            period: RSI 周期
            
        Returns:
            RSI 序列列表
        """
        if len(closes) < period + 1:
            return [float("nan")] * len(closes)

        gains  = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]

        rsi = [float("nan")] * len(closes)
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            rs = avg_g / avg_l if avg_l > 0 else float("inf")
            rsi[i + 1] = 100.0 if math.isinf(rs) else 100 - (100 / (1 + rs))

        return rsi

    def get_rsi(self, closes: list, timeframe: str) -> float:
        """
        获取最新 RSI 值
        
        Args:
            closes: 收盘价列表
            timeframe: 时间框架
            
        Returns:
            最新 RSI 值
        """
        period = self.period_map.get(timeframe, 14)
        series = self._wilder_rsi(closes, period)
        for v in reversed(series):
            if not math.isnan(v):
                return v
        return float("nan")

    def get_slope(self, closes: list, timeframe: str, bars: int = 3) -> float:
        """
        RSI 斜率（最近 bars 根的变化）
        
        Args:
            closes: 收盘价列表
            timeframe: 时间框架
            bars: 回望周期
            
        Returns:
            RSI 斜率
        """
        period = self.period_map.get(timeframe, 14)
        series = self._wilder_rsi(closes, period)
        valid = [v for v in series if not math.isnan(v)]
        
        if len(valid) < bars + 1:
            return 0.0
        
        return valid[-1] - valid[-(bars+1)]

    # ──────────────────────────────────────
    # RSI 评分
    # ──────────────────────────────────────

    def score_rsi(self, rsi_value: float, direction: str) -> float:
        """
        RSI 动量评分
        
        Args:
            rsi_value: RSI 值
            direction: 方向 (long/short)
            
        Returns:
            评分 (0.0 ~ 1.0)
        """
        if math.isnan(rsi_value):
            return 0.0

        thresholds = (
            self._long_score_thresholds if direction == "long"
            else self._short_score_thresholds
        )

        for threshold, score in thresholds:
            if rsi_value < threshold:
                return score

        return 0.0

    # ──────────────────────────────────────
    # 三时间框架门控
    # ──────────────────────────────────────

    def check_gate(
        self,
        direction: str,
        signal_type: str,
        rsi_4h: float,
        rsi_1h: float,
        rsi_15m: float,
        slope_15m: float,
    ) -> tuple:
        """
        RSI 三时间框架联合门控
        
        Args:
            direction: 方向 (long/short)
            signal_type: 信号类型
            rsi_4h: 4h RSI
            rsi_1h: 1h RSI
            rsi_15m: 15m RSI
            slope_15m: 15m RSI 斜率
            
        Returns:
            (gate_pass, fail_reason) 元组
        """
        is_flip = signal_type in ("flip_bullish", "flip_bearish")
        cfg = self.gate_cfg
        fcfg = self.flip_cfg

        # 硬性极值屏蔽（最高优先级）
        if direction == "long" and rsi_1h >= self.overbought:
            return False, f"RSI_HARD_BLOCK_LONG: RSI_1h={rsi_1h:.1f} >= {self.overbought}"
        if direction == "short" and rsi_1h <= self.oversold:
            return False, f"RSI_HARD_BLOCK_SHORT: RSI_1h={rsi_1h:.1f} <= {self.oversold}"

        if direction == "long":
            # flip 族豁免
            rsi_4h_min = fcfg.get("flip_bullish_rsi_4h_min", 40) if is_flip else cfg.get("long_rsi_4h_min", 43)
            rsi_1h_min = fcfg.get("flip_bullish_rsi_1h_min", 40) if is_flip else cfg.get("long_rsi_1h_min", 43)
            rsi_15m_min = cfg.get("long_rsi_15m_min", 47)
            slope_req = cfg.get("long_rsi_15m_slope_positive", True)

            if rsi_4h < rsi_4h_min:
                return False, f"RSI_4H_GATE_LONG: {rsi_4h:.1f} < {rsi_4h_min}"
            if rsi_1h < rsi_1h_min:
                return False, f"RSI_1H_GATE_LONG: {rsi_1h:.1f} < {rsi_1h_min}"
            if rsi_15m < rsi_15m_min:
                return False, f"RSI_15M_GATE_LONG: {rsi_15m:.1f} < {rsi_15m_min}"
            if slope_req and slope_15m <= 0:
                return False, f"RSI_15M_SLOPE_GATE_LONG: slope={slope_15m:.3f}"

        elif direction == "short":
            # flip 族豁免
            rsi_4h_max = fcfg.get("flip_bearish_rsi_4h_max", 60) if is_flip else cfg.get("short_rsi_4h_max", 57)
            rsi_1h_max = fcfg.get("flip_bearish_rsi_1h_max", 60) if is_flip else cfg.get("short_rsi_1h_max", 57)
            rsi_15m_max = cfg.get("short_rsi_15m_max", 53)
            slope_req = cfg.get("short_rsi_15m_slope_negative", True)

            if rsi_4h > rsi_4h_max:
                return False, f"RSI_4H_GATE_SHORT: {rsi_4h:.1f} > {rsi_4h_max}"
            if rsi_1h > rsi_1h_max:
                return False, f"RSI_1H_GATE_SHORT: {rsi_1h:.1f} > {rsi_1h_max}"
            if rsi_15m > rsi_15m_max:
                return False, f"RSI_15M_GATE_SHORT: {rsi_15m:.1f} > {rsi_15m_max}"
            if slope_req and slope_15m >= 0:
                return False, f"RSI_15M_SLOPE_GATE_SHORT: slope={slope_15m:.3f}"

        return True, None

    # ──────────────────────────────────────
    # 背离检测
    # ──────────────────────────────────────

    def detect_divergence(
        self,
        candles_1h: list,
        direction: str,
    ) -> tuple:
        """
        背离检测
        
        Args:
            candles_1h: 1h K线数据列表 [{'close': float, 'high': float, 'low': float}, ...]
            direction: 方向 (long/short)
            
        Returns:
            (divergence_type, bonus) 元组
        """
        if not self.div_cfg.get("enabled", True):
            return None, 0.0

        lookback = self.div_cfg.get("lookback_bars", 10)
        min_price_diff = self.div_cfg.get("min_price_diff_pct", 0.004)
        min_rsi_diff = self.div_cfg.get("min_rsi_diff", 3.0)

        if len(candles_1h) < lookback + 2:
            return None, 0.0

        closes = [c["close"] for c in candles_1h]
        highs = [c["high"] for c in candles_1h]
        lows = [c["low"] for c in candles_1h]

        rsi_series = self._wilder_rsi(closes, self.period_map["1h"])
        valid_rsi = [(i, v) for i, v in enumerate(rsi_series) if not math.isnan(v)]
        recent = valid_rsi[-lookback:]

        if len(recent) < 4:
            return None, 0.0

        curr_idx, curr_rsi = recent[-1]
        curr_high = highs[curr_idx]
        curr_low = lows[curr_idx]

        hist = recent[:-2]
        min_rsi_idx, min_rsi_val = min(hist, key=lambda x: x[1])
        max_rsi_idx, max_rsi_val = max(hist, key=lambda x: x[1])

        hist_low = lows[min_rsi_idx]
        hist_high = highs[max_rsi_idx]

        # 正则看多背离（价格新低，RSI 未新低）
        if (curr_low < hist_low * (1 - min_price_diff)
                and curr_rsi > min_rsi_val + min_rsi_diff):
            bonus = self.div_cfg.get("regular_bullish_bonus", 0.12)
            return "regular_bullish", bonus if direction == "long" else -bonus

        # 正则看空背离（价格新高，RSI 未新高）
        if (curr_high > hist_high * (1 + min_price_diff)
                and curr_rsi < max_rsi_val - min_rsi_diff):
            bonus = self.div_cfg.get("regular_bearish_bonus", 0.12)
            return "regular_bearish", bonus if direction == "short" else -bonus

        # 隐藏看多背离
        if (curr_high > hist_high * (1 + min_price_diff)
                and curr_rsi < max_rsi_val - min_rsi_diff
                and curr_rsi > 45):
            bonus = self.div_cfg.get("hidden_bullish_bonus", 0.08)
            return "hidden_bullish", bonus if direction == "long" else 0.0

        # 隐藏看空背离
        if (curr_low < hist_low * (1 - min_price_diff)
                and curr_rsi > min_rsi_val + min_rsi_diff
                and curr_rsi < 55):
            bonus = self.div_cfg.get("hidden_bearish_bonus", 0.08)
            return "hidden_bearish", bonus if direction == "short" else 0.0

        return None, 0.0

    # ──────────────────────────────────────
    # 穿越检测
    # ──────────────────────────────────────

    def check_rsi_cross_50(
        self,
        closes_1h: list,
        cross_window_bars: int = 4,
    ) -> dict:
        """
        检测 RSI 是否最近穿越 50
        
        Args:
            closes_1h: 1h 收盘价列表
            cross_window_bars: 穿越窗口
            
        Returns:
            {'crossed_above': bool, 'crossed_below': bool}
        """
        series = self._wilder_rsi(closes_1h, self.period_map["1h"])
        valid = [v for v in series if not math.isnan(v)]

        if len(valid) < cross_window_bars + 1:
            return {"crossed_above": False, "crossed_below": False}

        recent = valid[-(cross_window_bars+1):]
        prev = recent[:-1]
        curr = recent[-1]

        crossed_above = any(p < 50 for p in prev) and curr >= 50
        crossed_below = any(p > 50 for p in prev) and curr <= 50

        return {
            "crossed_above": crossed_above,
            "crossed_below": crossed_below,
        }

    # ──────────────────────────────────────
    # 主分析入口
    # ──────────────────────────────────────

    def analyze(
        self,
        symbol: str,
        direction: str,
        signal_type: str,
        closes_4h: list,
        closes_1h: list,
        closes_15m: list,
        candles_1h: list,
    ) -> RSIResult:
        """
        RSI 综合分析
        
        Args:
            symbol: 交易对符号
            direction: 方向 (long/short)
            signal_type: 信号类型
            closes_4h: 4h 收盘价列表
            closes_1h: 1h 收盘价列表
            closes_15m: 15m 收盘价列表
            candles_1h: 1h K线数据列表
            
        Returns:
            RSIResult 对象
        """
        # 计算各时间框架 RSI
        rsi_4h = self.get_rsi(closes_4h, "4h")
        rsi_1h = self.get_rsi(closes_1h, "1h")
        rsi_15m = self.get_rsi(closes_15m, "15m")

        slope_1h = self.get_slope(closes_1h, "1h", bars=3)
        slope_15m = self.get_slope(closes_15m, "15m", bars=3)

        # 极值状态
        if rsi_1h >= self.extreme_ob:
            extreme_status = "extreme_overbought"
        elif rsi_1h <= self.extreme_os:
            extreme_status = "extreme_oversold"
        else:
            extreme_status = None

        # 背离检测
        div_type, div_bonus = self.detect_divergence(candles_1h, direction)

        # 穿越检测
        cross = self.check_rsi_cross_50(closes_1h)

        # 三时间框架门控
        gate_pass, fail_reason = self.check_gate(
            direction, signal_type,
            rsi_4h, rsi_1h, rsi_15m, slope_15m
        )

        # 动量评分（仅用 RSI_1h，4h 作为加权因子）
        score_1h = self.score_rsi(rsi_1h, direction)
        score_4h = self.score_rsi(rsi_4h, direction)
        momentum_score = score_1h * 0.70 + score_4h * 0.30

        logger.debug(
            f"[{symbol}] RSI analysis: "
            f"rsi_4h={rsi_4h:.1f}, rsi_1h={rsi_1h:.1f}, rsi_15m={rsi_15m:.1f}, "
            f"momentum_score={momentum_score:.3f}, gate_pass={gate_pass}"
        )

        return RSIResult(
            rsi_4h=rsi_4h,
            rsi_1h=rsi_1h,
            rsi_15m=rsi_15m,
            rsi_slope_1h=slope_1h,
            rsi_slope_15m=slope_15m,
            momentum_score=momentum_score,
            gate_pass=gate_pass,
            fail_reason=fail_reason,
            divergence_type=div_type,
            divergence_bonus=div_bonus,
            rsi_recently_crossed_above_50=cross["crossed_above"],
            rsi_recently_crossed_below_50=cross["crossed_below"],
            extreme_status=extreme_status,
        )
