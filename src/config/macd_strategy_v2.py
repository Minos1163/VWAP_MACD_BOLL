"""
MACD多时间框架交易策略模块 V2.0 - VWAP + BOLL 增强版

策略架构：
- BOLL结构层（4H + 1H）→ 过滤逆势交易，确认价格在布林带中的位置
- MACD_1H 定方向 → 负责执行方向与过滤
- MACD_4H 主评分（权重50%）→ 负责主趋势打分，降低噪音
- MACD_4H 确认增强（默认关闭）→ 预留附加趋势验证
- VWAP 价值中枢层（权重15%）→ 判断多空偏向，偏离过滤
- MACD_15M 跟随入场（权重20%）→ 入场时机
- 成交量确认（权重15%）→ 入场质量验证

扫描周期：每15分钟
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import logging
import numpy as np


logger = logging.getLogger(__name__)


class VetoType(Enum):
    """否决类型"""
    NONE = "none"
    VWAP_HARD_BLOCK = "vwap_hard_block"  # VWAP偏离超过3%
    VWAP_SCORE_FILTER = "vwap_score_filter"  # VWAP评分低于入场阈值
    VWAP_OPPOSITE_DAYS = "vwap_opposite_days"  # VWAP方向连续相反
    EMA_1H_BREAK = "ema_1h_break"  # 兼容旧枚举值：1H跌破/突破BOLL中轨
    EMA_4H_REVERSE = "ema_4h_reverse"  # 兼容旧枚举值：4H结构完全反向
    MACD_HIGH_DEVIATION = "macd_high_deviation"  # 兼容旧枚举值：MACD翻色时价格远离BOLL中轨
    VOLUME_VWAP_BOTH_LOW = "volume_vwap_both_low"  # 量价双低
    SHORT_QUALITY_FILTER = "short_quality_filter"  # 空头质量过滤（V3专家组建议）
    CVD_CONTINUATION_RISK = "cvd_continuation_risk"  # session-reset CVD 显示短线买盘延续风险


@dataclass
class MACDStrategyV2Config:
    """MACD策略V2.0配置"""
    # MACD参数
    macd_1h_fast: int = 12
    macd_1h_slow: int = 26
    macd_1h_signal: int = 9
    macd_4h_fast: int = 12
    macd_4h_slow: int = 26
    macd_4h_signal: int = 9
    macd_15m_fast: int = 12
    macd_15m_slow: int = 26
    macd_15m_signal: int = 9
    macd_threshold: float = 0.00005
    
    # BOLL参数
    boll_period: int = 20
    boll_std_dev: float = 2.0
    ema_multiplier_strong: float = 1.2
    ema_multiplier_normal: float = 1.0
    ema_multiplier_weak: float = 0.6
    ema_55_1h_hard_block: bool = True  # 兼容旧配置：等价于1H跌破/突破BOLL中轨硬性否决
    
    # VWAP参数
    vwap_deviation_optimal: float = 0.005  # 最优偏离区间 ±0.5%
    vwap_deviation_warning: float = 0.015  # 警告偏离 ±1.5%
    vwap_deviation_hard_block: float = 0.030  # 硬性否决偏离 ±3.0%
    structural_vwap_mode: str = "anchored_weekly"
    structural_vwap_rolling_window: int = 20
    vwap_retest_tolerance: float = 0.003
    
    # 评分权重
    weight_1h_direction: float = 0.35  # 兼容旧配置键：未显式提供 weight_4h_direction 时作为回退
    weight_4h_direction: float = 0.35  # 4H主趋势评分权重
    weight_4h_enhancement: float = 0.00  # 4H附加增强权重（默认关闭，避免重复计分）
    weight_vwap: float = 0.15  # VWAP评分权重
    weight_15m_entry: float = 0.10  # 15M入场时机评分权重
    weight_volume: float = 0.15  # 成交量确认评分权重
    
    # 入场阈值
    min_entry_score: float = 0.25
    min_signal_score: float = 0.850
    red_bar_growing_min_signal_score: float = 0.870
    flip_bearish_min_signal_score: float = 0.840
    flip_bullish_min_signal_score: float = 0.840

    # 1H flip_bullish 严格过滤
    enable_flip_bullish_strict_filter: bool = True
    disable_flip_bullish_entries: bool = False
    disable_flip_bullish_trial_entries: bool = False
    flip_bullish_min_vwap_score: float = 0.12
    flip_bullish_require_pullback_bounce: bool = True
    flip_bullish_require_15m_growing: bool = True
    enable_flip_bullish_cvd_context_filter: bool = False
    flip_bullish_max_cvd_upper_wick_ratio: float = 0.0
    flip_bullish_min_cvd_1h_delta_ratio: float = 0.0
    flip_bullish_trial_score_window_enabled: bool = False
    flip_bullish_trial_score_min: float = 0.80
    flip_bullish_trial_score_max: float = 0.87
    flip_bearish_min_ema_multiplier: float = 0.0
    flip_bearish_normal_ema_min_signal_score: float = 0.0
    flip_bearish_normal_ema_max_leverage: int = 0
    flip_bearish_min_adx_1h: float = 18.0
    flip_bearish_retest_reject_min_vwap_score: float = 0.0
    flip_bearish_max_ema21_slope_1h: float = 0.0  # 兼容旧配置：等价于BOLL中轨斜率
    flip_bearish_max_ema21_slope_4h: float = 0.0001  # 兼容旧配置：等价于BOLL中轨斜率
    ema_slope_lookback_1h: int = 3  # 兼容旧配置：等价于BOLL中轨斜率lookback
    ema_slope_lookback_4h: int = 2  # 兼容旧配置：等价于BOLL中轨斜率lookback
    disable_red_bar_growing_long_entries: bool = False
    disable_green_bar_growing_entries: bool = True
    disable_green_bar_shrinking_short_dual_pressure_entries: bool = True
    disable_red_bar_shrinking_long_dual_support_entries: bool = True
    primary_direction_timeframe: str = "1h"  # 1h=兼容旧逻辑, 4h=纯4H主趋势
    require_1h_confirmation_when_4h_primary: bool = False
    allow_neutral_1h_confirmation: bool = False
    light_1h_confirmation_when_4h_primary: bool = False
    enable_green_bar_growing_short_adx_1h_range_filter: bool = False
    green_bar_growing_short_min_adx_1h: float = 0.0
    green_bar_growing_short_max_adx_1h: float = 0.0
    enable_4h_preflip_trial_entries: bool = False
    preflip_trial_min_shrink_pct_long: float = 0.75
    preflip_trial_min_shrink_pct_short: float = 0.30
    preflip_trial_min_signal_score: float = 0.78
    preflip_trial_min_vwap_score: float = 0.06
    preflip_trial_entry_scale: float = 0.35
    preflip_trial_max_leverage: int = 2
    enable_trial_short_below_structure_continuation_promotion: bool = False
    trial_short_below_structure_promotion_min_signal_score: float = 0.82
    trial_short_below_structure_promotion_min_vwap_score: float = 0.075
    trial_short_below_structure_promotion_min_adx_1h: float = 25.0
    trial_short_below_structure_promotion_min_4h_shrink_pct: float = 0.80
    trial_short_below_structure_promotion_min_4h_shrink_bars: int = 6
    enable_stable_bear_continuation: bool = True
    stable_bear_continuation_min_signal_score: float = 0.82
    stable_bear_continuation_min_vwap_score: float = 0.10
    stable_bear_continuation_min_adx_1h: float = 20.0
    stable_bear_continuation_min_4h_bars: int = 2
    enable_stable_bull_continuation: bool = False
    stable_bull_continuation_min_signal_score: float = 0.82
    stable_bull_continuation_min_vwap_score: float = 0.10
    stable_bull_continuation_min_adx_1h: float = 20.0
    stable_bull_continuation_min_4h_bars: int = 2
    enable_stable_continuation_slow_4h_shrink_exit: bool = True
    stable_continuation_exit_4h_shrink_bars: int = 3
    stable_continuation_exit_4h_min_shrink_pct: float = 0.35
    enable_4h_shrink_exit: bool = False
    exit_4h_shrink_bars: int = 2
    exit_4h_min_shrink_pct: float = 0.20
    exit_4h_require_profit: bool = True
    exit_4h_weak_loss_threshold: float = -1.0
    session_risk_control_enabled: bool = False
    session_risk_high_risk_sessions: List[Dict[str, Any]] = field(default_factory=list)
    session_risk_apply_to_states: List[str] = field(default_factory=list)
    vwap_score_tier_apply_to_states: List[str] = field(default_factory=list)
    vwap_score_position_tiers: List[Dict[str, Any]] = field(default_factory=list)
    symbol_risk_watchlist_symbols: List[str] = field(default_factory=list)
    symbol_risk_watchlist_max_position_portion: float = 0.0
    symbol_risk_watchlist_max_leverage: int = 0
    symbol_risk_watchlist_apply_session_scale_double: bool = False
    symbol_risk_watchlist_session_scale_multiplier: float = 0.80

    # 过热惩罚
    overheat_growing_penalty: float = 0.12
    overheat_ema_multiplier_threshold: float = 1.2
    overheat_vwap_score_threshold: float = 0.10
    min_vwap_score_for_entry: float = 0.0  # VWAP全局过滤，0=禁用
    
    # 止损配置
    use_dynamic_stop: bool = True
    ema_stop_atr_multiplier: float = 0.5
    max_stop_loss_pct: float = 0.025
    vwap_alert_deviation: float = 0.005

    # BOLL强趋势处理（新增）
    ema_strong_trend_leverage_mult: float = 0.8  # 强趋势时杠杆降低20%
    dual_pressure_target_portion_bonus: float = 0.0
    dual_pressure_max_symbol_position_portion: float = 0.0
    use_cvd_bonus_filter: bool = False
    cvd_1h_slope_lookback: int = 3
    cvd_15m_slope_lookback: int = 3
    cvd_positive_delta_ratio_threshold: float = 0.05
    cvd_negative_delta_ratio_threshold: float = -0.05
    cvd_bullish_bonus_multiplier: float = 0.0
    cvd_neutral_bonus_multiplier: float = 0.7
    cvd_bearish_bonus_multiplier: float = 1.0
    use_cvd_veto_filter: bool = False
    cvd_veto_session_reset: str = "daily_utc0"
    cvd_veto_lookback_15m: int = 3
    cvd_veto_positive_delta_ratio_threshold: float = 0.10
    cvd_veto_positive_pressure_threshold: float = 0.0
    cvd_veto_session_ratio_change_threshold: float = -0.003
    cvd_veto_session_price_change_threshold: float = -0.005
    cvd_veto_strong_close_pos_threshold: float = 0.72
    cvd_veto_upper_wick_ratio_max: float = 0.25
    cvd_absorption_delta_ratio_threshold: float = 0.05
    cvd_absorption_close_pos_threshold: float = 0.45
    cvd_absorption_upper_wick_ratio_threshold: float = 0.35
    cvd_absorption_structure_gap_threshold: float = 0.002
    cvd_divergence_price_change_threshold: float = 0.003
    cvd_divergence_session_change_threshold: float = -0.005
    
    # 空头质量过滤器（V3专家组建议）
    enable_short_quality_filter: bool = True  # 启用空头质量过滤
    short_filter_min_funding_rate: float = 0.0005  # funding_rate > 0.05%
    short_filter_max_oi_delta_ratio: float = 0.0  # oi_delta_ratio < 0 (多头减仓)
    short_filter_min_vwap_deviation: float = 0.005  # price > vwap * 1.005

    def resolve_signal_score_threshold(
        self,
        signal_type_1h: Optional[str],
        stable_continuation_side: Optional[str] = None,
    ) -> float:
        continuation_side = str(stable_continuation_side or "").strip().lower()
        if continuation_side == "short" and self.enable_stable_bear_continuation:
            threshold = float(self.stable_bear_continuation_min_signal_score)
            if threshold > 0:
                return threshold
        if continuation_side == "long" and self.enable_stable_bull_continuation:
            threshold = float(self.stable_bull_continuation_min_signal_score)
            if threshold > 0:
                return threshold
        signal_type = str(signal_type_1h or "").strip().lower()
        thresholds = {
            "red_bar_growing": self.red_bar_growing_min_signal_score,
            "flip_bearish": self.flip_bearish_min_signal_score,
            "flip_bullish": self.flip_bullish_min_signal_score,
        }
        threshold = thresholds.get(signal_type)
        if threshold is None or threshold <= 0:
            return self.min_signal_score
        return threshold

    def is_flip_bearish_normal_ema(self, signal_type_1h: Optional[str], ema_multiplier: float) -> bool:
        if str(signal_type_1h or "").strip().lower() != "flip_bearish":
            return False
        return abs(float(ema_multiplier) - float(self.ema_multiplier_normal)) < 1e-9


@dataclass
class MACDSignalV2:
    """MACD信号V2.0"""
    direction: str  # 'long', 'short', 'neutral'
    signal_score: float  # 0-1
    signal_type_1h: Optional[str] = None
    signal_strength_1h: float = 0.0
    is_4h_enhanced: bool = False
    enhancement_score: float = 0.0
    entry_type_15m: Optional[str] = None
    entry_score_15m: float = 0.0
    
    # V2.0 新增字段
    vwap_score: float = 0.0
    vwap_deviation: float = 0.0
    vwap_state: str = "unknown"
    vwap_location_score: float = 0.0
    ema_multiplier: float = 1.0
    ema_structure_status: str = "normal"  # strong/normal/weak/against
    
    # 否决信息
    veto_type: VetoType = VetoType.NONE
    veto_reason: str = ""
    
    # 止损信息
    suggested_stop_price: Optional[float] = None
    stop_loss_pct: float = 0.02
    is_trial_entry: bool = False
    entry_scale: float = 1.0

    details: Dict = field(default_factory=dict)


class MACDStrategyV2Engine:
    """MACD策略V2.0引擎"""
    
    def __init__(self, config: MACDStrategyV2Config = None):
        self.config = config or MACDStrategyV2Config()
        self._last_analysis: Dict = {}

    def _build_debug_details(self, **kwargs: Any) -> Dict[str, Any]:
        details = dict(kwargs)
        details["stage_path"] = self._normalize_stage_path(details.get("stage_path"))
        stage = str(details.get("stage") or "").strip()
        if stage and (not details["stage_path"] or details["stage_path"][-1] != stage):
            details["stage_path"].append(stage)
        details["stage_path_text"] = " > ".join(details["stage_path"]) if details["stage_path"] else ""
        details["min_signal_score"] = self.config.min_signal_score
        details["min_entry_score"] = self.config.min_entry_score
        details["min_vwap_score_for_entry"] = self.config.min_vwap_score_for_entry
        return details

    @staticmethod
    def _normalize_stage_path(stage_path: Any) -> List[str]:
        if isinstance(stage_path, list):
            seen: List[str] = []
            for item in stage_path:
                stage = str(item or "").strip()
                if stage and (not seen or seen[-1] != stage):
                    seen.append(stage)
            return seen
        return []

    def _set_stage(self, details: Dict[str, Any], stage: str, **extra: Any) -> Dict[str, Any]:
        payload = dict(details or {})
        normalized = self._normalize_stage_path(payload.get("stage_path"))
        stage_name = str(stage or "").strip()
        if stage_name and (not normalized or normalized[-1] != stage_name):
            normalized.append(stage_name)
        payload["stage"] = stage_name
        payload["stage_path"] = normalized
        payload["stage_path_text"] = " > ".join(normalized) if normalized else ""
        if extra:
            payload.update(extra)
        return payload

    @staticmethod
    def _extract_reject_reason_metadata(reason: str) -> Tuple[str, str]:
        text = str(reason or "").strip()
        if not text:
            return "unknown_reject", ""
        open_idx = text.find("(")
        close_idx = text.rfind(")")
        if open_idx > 0 and close_idx > open_idx:
            return text[:open_idx].strip(), text[open_idx + 1:close_idx].strip()
        if ":" in text:
            code, detail = text.split(":", 1)
            return code.strip(), detail.strip()
        return text, ""

    def _neutral_signal(
        self,
        *,
        reason: str,
        score: float = 0.0,
        details: Optional[Dict[str, Any]] = None,
        veto_type: VetoType = VetoType.NONE,
        veto_reason: str = "",
        signal_type_1h: Optional[str] = None,
        entry_type_15m: Optional[str] = None,
        entry_score_15m: float = 0.0,
        vwap_score: float = 0.0,
        vwap_deviation: float = 0.0,
        vwap_state: str = "unknown",
        vwap_location_score: float = 0.0,
        ema_multiplier: float = 1.0,
        ema_structure_status: str = "normal",
        enhancement_score: float = 0.0,
        is_4h_enhanced: bool = False,
        is_trial_entry: bool = False,
        entry_scale: float = 1.0,
    ) -> MACDSignalV2:
        payload = dict(details or {})
        payload["reason"] = reason
        payload["reject_stage"] = payload.get("stage") or "unknown"
        reject_code, reject_detail = self._extract_reject_reason_metadata(reason)
        payload["reject_reason_code"] = reject_code
        payload["reject_reason_detail"] = reject_detail
        payload["stage_path"] = self._normalize_stage_path(payload.get("stage_path"))
        payload["stage_path_text"] = " > ".join(payload["stage_path"]) if payload["stage_path"] else ""
        if veto_type != VetoType.NONE and "veto_type" not in payload:
            payload["veto_type"] = veto_type.value
        self._last_analysis = payload.copy()
        return MACDSignalV2(
            direction='neutral',
            signal_score=score,
            signal_type_1h=signal_type_1h,
            is_4h_enhanced=is_4h_enhanced,
            enhancement_score=enhancement_score,
            entry_type_15m=entry_type_15m,
            entry_score_15m=entry_score_15m,
            vwap_score=vwap_score,
            vwap_deviation=vwap_deviation,
            vwap_state=vwap_state,
            vwap_location_score=vwap_location_score,
            ema_multiplier=ema_multiplier,
            ema_structure_status=ema_structure_status,
            veto_type=veto_type,
            veto_reason=veto_reason,
            is_trial_entry=is_trial_entry,
            entry_scale=entry_scale,
            details=payload,
        )
    
    @staticmethod
    def calculate_ema(prices: np.ndarray, period: int) -> np.ndarray:
        """计算EMA"""
        multiplier = 2 / (period + 1)
        ema = np.zeros_like(prices, dtype=float)
        ema[0] = prices[0]
        for i in range(1, len(prices)):
            ema[i] = (prices[i] * multiplier) + (ema[i-1] * (1 - multiplier))
        return ema
    
    @staticmethod
    def calculate_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
        """计算ATR"""
        tr = np.zeros(len(close))
        tr[0] = high[0] - low[0]
        for i in range(1, len(close)):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i-1]),
                abs(low[i] - close[i-1])
            )
        atr = np.zeros(len(close))
        atr[:period] = np.mean(tr[:period])
        for i in range(period, len(close)):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
        return atr
    
    @staticmethod
    def calculate_macd(
        prices: np.ndarray,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """计算MACD"""
        ema_fast = MACDStrategyV2Engine.calculate_ema(prices, fast)
        ema_slow = MACDStrategyV2Engine.calculate_ema(prices, slow)
        macd_line = ema_fast - ema_slow
        signal_line = MACDStrategyV2Engine.calculate_ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def calculate_bollinger_bands(
        prices: np.ndarray,
        period: int = 20,
        std_dev: float = 2.0,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """计算布林带（中轨/上轨/下轨）"""
        values = np.asarray(prices, dtype=float)
        middle = np.zeros_like(values, dtype=float)
        upper = np.zeros_like(values, dtype=float)
        lower = np.zeros_like(values, dtype=float)
        for i in range(len(values)):
            start = max(0, i - period + 1)
            window = values[start:i + 1]
            mid = float(np.mean(window))
            sigma = float(np.std(window))
            middle[i] = mid
            upper[i] = mid + sigma * std_dev
            lower[i] = mid - sigma * std_dev
        return middle, upper, lower

    @staticmethod
    def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, float(value)))

    @staticmethod
    def _parse_hhmm_to_minutes(value: object) -> Optional[int]:
        text = str(value or "").strip()
        if not text or ":" not in text:
            return None
        hour_raw, minute_raw = text.split(":", 1)
        try:
            hour = int(hour_raw)
            minute = int(minute_raw)
        except (TypeError, ValueError):
            return None
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        return hour * 60 + minute

    @staticmethod
    def _timestamp_to_utc_minutes(timestamp: object) -> Optional[int]:
        if timestamp is None:
            return None
        dt_obj: Optional[datetime] = None
        if isinstance(timestamp, datetime):
            dt_obj = timestamp
        elif hasattr(timestamp, "to_pydatetime"):
            try:
                dt_obj = timestamp.to_pydatetime()
            except Exception:
                dt_obj = None
        if dt_obj is None:
            hour = getattr(timestamp, "hour", None)
            minute = getattr(timestamp, "minute", None)
            if hour is None or minute is None:
                return None
            return int(hour) * 60 + int(minute)
        if dt_obj.tzinfo is not None:
            dt_obj = dt_obj.astimezone(timezone.utc)
        return int(dt_obj.hour) * 60 + int(dt_obj.minute)

    @staticmethod
    def _minute_in_window(current_minute: int, start_minute: int, end_minute: int) -> bool:
        if start_minute == end_minute:
            return True
        if start_minute < end_minute:
            return start_minute <= current_minute < end_minute
        return current_minute >= start_minute or current_minute < end_minute

    def resolve_session_position_scale(
        self,
        timestamp: object,
        signal_type_1h: Optional[str] = None,
        vwap_state: Optional[str] = None,
    ) -> float:
        if not bool(self.config.session_risk_control_enabled):
            return 1.0

        target_states = {
            str(state or "").strip().lower()
            for state in (self.config.session_risk_apply_to_states or [])
            if str(state or "").strip()
        }
        signal_state = str(signal_type_1h or "").strip().lower()
        vwap_state_norm = str(vwap_state or "").strip().lower()
        if target_states and signal_state not in target_states and vwap_state_norm not in target_states:
            return 1.0

        current_minute = self._timestamp_to_utc_minutes(timestamp)
        if current_minute is None:
            return 1.0

        matched_scale = 1.0
        for session in self.config.session_risk_high_risk_sessions or []:
            if not isinstance(session, dict):
                continue
            start_minute = self._parse_hhmm_to_minutes(session.get("utc_start"))
            end_minute = self._parse_hhmm_to_minutes(session.get("utc_end"))
            if start_minute is None or end_minute is None:
                continue
            if not self._minute_in_window(current_minute, start_minute, end_minute):
                continue
            scale = self._clamp(float(session.get("position_scale", 1.0) or 1.0), 0.05, 1.0)
            matched_scale = min(matched_scale, scale)
        return matched_scale

    @staticmethod
    def _normalize_symbol(symbol: object) -> str:
        return str(symbol or "").strip().upper()

    def is_watchlist_symbol(self, symbol: object) -> bool:
        symbol_up = self._normalize_symbol(symbol)
        if not symbol_up:
            return False
        watchlist = {
            self._normalize_symbol(item)
            for item in (self.config.symbol_risk_watchlist_symbols or [])
            if self._normalize_symbol(item)
        }
        return symbol_up in watchlist

    def resolve_symbol_risk_session_scale(
        self,
        symbol: object,
        session_scale: float,
    ) -> float:
        scale = self._clamp(session_scale, 0.05, 1.0)
        if not self.is_watchlist_symbol(symbol):
            return scale
        if self.config.symbol_risk_watchlist_apply_session_scale_double and scale < 0.999999:
            scale *= self._clamp(
                float(self.config.symbol_risk_watchlist_session_scale_multiplier or 1.0),
                0.05,
                1.0,
            )
        return self._clamp(scale, 0.05, 1.0)

    def resolve_vwap_score_position_multiplier(
        self,
        vwap_score: float,
        signal_type_1h: Optional[str] = None,
        vwap_state: Optional[str] = None,
    ) -> float:
        apply_states = {
            str(item or "").strip().lower()
            for item in (self.config.vwap_score_tier_apply_to_states or [])
            if str(item or "").strip()
        }
        signal_state = str(signal_type_1h or "").strip().lower()
        vwap_state_norm = str(vwap_state or "").strip().lower()
        if apply_states and signal_state not in apply_states and vwap_state_norm not in apply_states:
            return 1.0

        score = float(vwap_score or 0.0)
        for raw_tier in self.config.vwap_score_position_tiers or []:
            if not isinstance(raw_tier, dict):
                continue
            tier_min = float(raw_tier.get("min", 0.0) or 0.0)
            tier_max = float(raw_tier.get("max", 1.0) or 1.0)
            if tier_max <= tier_min:
                continue
            is_last = abs(tier_max - 1.0) < 1e-12 or tier_max >= 0.999999
            in_tier = (tier_min <= score <= tier_max) if is_last else (tier_min <= score < tier_max)
            if not in_tier:
                continue
            return self._clamp(float(raw_tier.get("position_mult", 1.0) or 1.0), 0.05, 1.5)
        return 1.0

    @classmethod
    def _normalized_change(cls, current: float, previous: float) -> float:
        if abs(previous) < 1e-12:
            return 0.0
        return cls._clamp((current - previous) / abs(previous), -1.0, 1.0)

    def _ema_slope_from_series(
        self,
        prices: Optional[np.ndarray],
        period: int,
        lookback: int,
    ) -> float:
        if prices is None:
            return 0.0
        values = np.asarray(prices, dtype=float)
        if len(values) < max(period + 1, lookback + 1):
            return 0.0
        ema_series = self.calculate_ema(values, period)
        current = float(ema_series[-1])
        previous = float(ema_series[-1 - lookback])
        return self._normalized_change(current, previous)

    def _boll_middle_slope_from_series(
        self,
        prices: Optional[np.ndarray],
        period: int,
        std_dev: float,
        lookback: int,
    ) -> float:
        if prices is None:
            return 0.0
        values = np.asarray(prices, dtype=float)
        if len(values) < max(period, lookback + 1):
            return 0.0
        middle, _, _ = self.calculate_bollinger_bands(values, period=period, std_dev=std_dev)
        current = float(middle[-1])
        previous = float(middle[-1 - lookback])
        return self._normalized_change(current, previous)

    @staticmethod
    def _series_value(series: Optional[np.ndarray], offset: int = -1, default: float = 0.0) -> float:
        if series is None:
            return default
        values = np.asarray(series, dtype=float)
        if values.size == 0:
            return default
        try:
            return float(values[offset])
        except (IndexError, ValueError, TypeError):
            return default

    @staticmethod
    def _calculate_macd_shrink_pct(macd_hist: np.ndarray, idx: int) -> float:
        values = np.asarray(macd_hist[: idx + 1], dtype=float)
        if values.size < 10:
            return 0.0
        recent = values[-10:-2] if values.size >= 12 else values[:-2]
        if recent.size == 0:
            return 0.0
        peak_value = float(np.max(np.abs(recent)))
        current_value = abs(float(values[-1]))
        if peak_value <= 1e-12:
            return 0.0
        if current_value > peak_value:
            return -(current_value / peak_value - 1.0)
        return 1.0 - (current_value / peak_value)

    @staticmethod
    def _count_consecutive_macd_shrinking_bars(macd_hist: np.ndarray, idx: int) -> int:
        values = np.asarray(macd_hist[: idx + 1], dtype=float)
        if values.size < 2:
            return 0

        count = 0
        cursor = values.size - 1
        while cursor > 0:
            current = float(values[cursor])
            previous = float(values[cursor - 1])
            if abs(current) >= abs(previous):
                break
            if abs(current) <= 1e-12 or abs(previous) <= 1e-12:
                break
            if np.sign(current) != np.sign(previous):
                break
            count += 1
            cursor -= 1
        return count

    @staticmethod
    def _count_consecutive_macd_same_sign_bars(
        macd_hist: np.ndarray,
        idx: int,
        *,
        positive: bool,
        min_abs_value: float = 0.0,
    ) -> int:
        values = np.asarray(macd_hist[: idx + 1], dtype=float)
        if values.size <= 0:
            return 0

        count = 0
        cursor = values.size - 1
        while cursor >= 0:
            current = float(values[cursor])
            if abs(current) <= min_abs_value:
                break
            if positive and current <= 0:
                break
            if not positive and current >= 0:
                break
            count += 1
            cursor -= 1
        return count

    def _build_4h_shrink_context(
        self,
        macd_hist_4h: np.ndarray,
        idx_4h: int,
        signal_type_4h: str,
    ) -> Dict[str, Any]:
        signal_type = str(signal_type_4h or "").strip().lower()
        shrink_pct = self._calculate_macd_shrink_pct(macd_hist_4h, idx_4h)
        shrink_bars = self._count_consecutive_macd_shrinking_bars(macd_hist_4h, idx_4h)
        preflip_direction: Optional[str] = None
        exit_direction: Optional[str] = None
        if signal_type == "green_bar_shrinking":
            preflip_direction = "long"
            exit_direction = "short"
        elif signal_type == "red_bar_shrinking":
            preflip_direction = "short"
            exit_direction = "long"

        return {
            "signal_type_4h": signal_type,
            "shrink_pct": shrink_pct,
            "shrink_bars": shrink_bars,
            "preflip_direction": preflip_direction,
            "exit_direction": exit_direction,
            "shrink_exit_ready": bool(
                exit_direction
                and shrink_bars >= max(1, int(self.config.exit_4h_shrink_bars))
                and shrink_pct >= max(0.0, float(self.config.exit_4h_min_shrink_pct))
            ),
        }

    def _build_stable_trend_context(
        self,
        macd_hist_4h: np.ndarray,
        idx_4h: int,
    ) -> Dict[str, Any]:
        values = np.asarray(macd_hist_4h[: idx_4h + 1], dtype=float)
        current_hist = float(values[-1]) if values.size > 0 else 0.0
        min_abs_value = max(float(self.config.macd_threshold), 1e-8)
        positive_bars = self._count_consecutive_macd_same_sign_bars(
            macd_hist_4h,
            idx_4h,
            positive=True,
            min_abs_value=min_abs_value,
        )
        negative_bars = self._count_consecutive_macd_same_sign_bars(
            macd_hist_4h,
            idx_4h,
            positive=False,
            min_abs_value=min_abs_value,
        )
        return {
            "hist_current": current_hist,
            "positive_bars": positive_bars,
            "negative_bars": negative_bars,
            "bull_active": bool(
                current_hist > min_abs_value
                and positive_bars >= max(1, int(self.config.stable_bull_continuation_min_4h_bars))
            ),
            "bear_active": bool(
                current_hist < -min_abs_value
                and negative_bars >= max(1, int(self.config.stable_bear_continuation_min_4h_bars))
            ),
        }

    def _resolve_stable_continuation_direction(
        self,
        *,
        primary_mode: str,
        direction_1h: Optional[str],
        details_1h: Dict[str, Any],
        stable_trend_context: Dict[str, Any],
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        debug: Dict[str, Any] = {
            "stable_continuation_direction_recovered": False,
        }
        if primary_mode != "4h" or direction_1h not in {"long", "short"}:
            debug["stable_continuation_direction_reason"] = "primary_mode_or_1h_not_eligible"
            return None, debug

        signal_type_1h = str(details_1h.get("signal_type") or "").strip().lower()
        if direction_1h == "short":
            if not self.config.enable_stable_bear_continuation:
                debug["stable_continuation_direction_reason"] = "stable_bear_disabled"
                return None, debug
            if not bool(stable_trend_context.get("bear_active", False)):
                debug["stable_continuation_direction_reason"] = "4h_bear_not_persistent"
                return None, debug
            if signal_type_1h not in {"flip_bearish", "green_bar_growing"}:
                debug["stable_continuation_direction_reason"] = "1h_short_signal_not_supported"
                return None, debug
        else:
            if not self.config.enable_stable_bull_continuation:
                debug["stable_continuation_direction_reason"] = "stable_bull_disabled"
                return None, debug
            if not bool(stable_trend_context.get("bull_active", False)):
                debug["stable_continuation_direction_reason"] = "4h_bull_not_persistent"
                return None, debug
            if signal_type_1h not in {"flip_bullish", "red_bar_growing"}:
                debug["stable_continuation_direction_reason"] = "1h_long_signal_not_supported"
                return None, debug

        debug.update(
            stable_continuation_direction_recovered=True,
            stable_continuation_direction_reason="stable_4h_hist_persistence",
            stable_continuation_direction_side=direction_1h,
        )
        return direction_1h, debug

    def _evaluate_stable_continuation(
        self,
        *,
        primary_mode: str,
        trade_direction: Optional[str],
        signal_type_1h: Optional[str],
        entry_type_15m: Optional[str],
        vwap_score: float,
        vwap_state: str,
        adx_1h: float,
        stable_trend_context: Dict[str, Any],
        is_trial_entry: bool,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "stable_continuation_active": False,
            "stable_continuation_side": None,
            "stable_continuation_reason": "inactive",
        }
        if primary_mode != "4h" or trade_direction not in {"long", "short"} or is_trial_entry:
            result["stable_continuation_reason"] = "primary_mode_or_trial_not_eligible"
            return result

        side = str(trade_direction)
        signal_type = str(signal_type_1h or "").strip().lower()
        entry_type = str(entry_type_15m or "").strip().lower()
        state = str(vwap_state or "").strip().lower()

        if side == "short":
            enabled = bool(self.config.enable_stable_bear_continuation)
            min_vwap_score = float(self.config.stable_bear_continuation_min_vwap_score)
            min_adx_1h = float(self.config.stable_bear_continuation_min_adx_1h)
            min_4h_bars = max(1, int(self.config.stable_bear_continuation_min_4h_bars))
            hist_bars = int(stable_trend_context.get("negative_bars", 0) or 0)
            stable_active = bool(stable_trend_context.get("bear_active", False))
            allowed_states = {
                "short_dual_pressure",
                "short_retest_reject",
                "short_below_session_above_structure",
            }
            allowed_signal_types = {"flip_bearish", "green_bar_growing"}
            allowed_entry_types = {"green_bar_growing"}
        else:
            enabled = bool(self.config.enable_stable_bull_continuation)
            min_vwap_score = float(self.config.stable_bull_continuation_min_vwap_score)
            min_adx_1h = float(self.config.stable_bull_continuation_min_adx_1h)
            min_4h_bars = max(1, int(self.config.stable_bull_continuation_min_4h_bars))
            hist_bars = int(stable_trend_context.get("positive_bars", 0) or 0)
            stable_active = bool(stable_trend_context.get("bull_active", False))
            allowed_states = {"long_dual_support", "long_reclaim_confirmed"}
            allowed_signal_types = {"flip_bullish", "red_bar_growing"}
            allowed_entry_types = {"red_bar_growing"}

        result.update(
            stable_continuation_side=side,
            stable_continuation_hist_bars=hist_bars,
            stable_continuation_min_4h_bars=min_4h_bars,
            stable_continuation_min_adx_1h=min_adx_1h,
            stable_continuation_min_vwap_score=min_vwap_score,
            stable_continuation_signal_type_1h=signal_type,
            stable_continuation_entry_type_15m=entry_type,
            stable_continuation_vwap_state=state,
        )

        if not enabled:
            result["stable_continuation_reason"] = "continuation_disabled"
            return result
        if not stable_active or hist_bars < min_4h_bars:
            result["stable_continuation_reason"] = "4h_hist_not_persistent"
            return result
        if signal_type not in allowed_signal_types:
            result["stable_continuation_reason"] = "1h_signal_not_supported"
            return result
        if entry_type not in allowed_entry_types:
            result["stable_continuation_reason"] = "15m_entry_not_supported"
            return result
        if float(adx_1h) < min_adx_1h:
            result["stable_continuation_reason"] = "adx_1h_too_low"
            return result
        if float(vwap_score) < min_vwap_score:
            result["stable_continuation_reason"] = "vwap_score_too_low"
            return result
        if state not in allowed_states:
            result["stable_continuation_reason"] = "vwap_state_not_supported"
            return result

        result.update(
            stable_continuation_active=True,
            stable_continuation_reason="stable_continuation_active",
        )
        return result

    def _evaluate_trial_short_below_structure_continuation_promotion(
        self,
        *,
        primary_mode: str,
        trade_direction: Optional[str],
        signal_type_1h: Optional[str],
        entry_type_15m: Optional[str],
        vwap_state: str,
        vwap_score: float,
        adx_1h: float,
        signal_score: float,
        shrink_4h_context: Dict[str, Any],
        is_trial_entry: bool,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "trial_short_below_structure_promotion_active": False,
            "trial_short_below_structure_promotion_reason": "inactive",
        }
        if not bool(self.config.enable_trial_short_below_structure_continuation_promotion):
            result["trial_short_below_structure_promotion_reason"] = "promotion_disabled"
            return result
        if not is_trial_entry:
            result["trial_short_below_structure_promotion_reason"] = "not_trial_entry"
            return result
        if primary_mode != "4h" or trade_direction != "short":
            result["trial_short_below_structure_promotion_reason"] = "direction_not_eligible"
            return result

        signal_type = str(signal_type_1h or "").strip().lower()
        entry_type = str(entry_type_15m or "").strip().lower()
        state = str(vwap_state or "").strip().lower()
        shrink_pct = max(0.0, float(shrink_4h_context.get("shrink_pct", 0.0) or 0.0))
        shrink_bars = max(0, int(shrink_4h_context.get("shrink_bars", 0) or 0))
        min_signal_score = float(self.config.trial_short_below_structure_promotion_min_signal_score)
        min_vwap_score = float(self.config.trial_short_below_structure_promotion_min_vwap_score)
        min_adx_1h = float(self.config.trial_short_below_structure_promotion_min_adx_1h)
        min_shrink_pct = float(self.config.trial_short_below_structure_promotion_min_4h_shrink_pct)
        min_shrink_bars = max(1, int(self.config.trial_short_below_structure_promotion_min_4h_shrink_bars))

        result.update(
            trial_short_below_structure_promotion_signal_type_1h=signal_type,
            trial_short_below_structure_promotion_entry_type_15m=entry_type,
            trial_short_below_structure_promotion_vwap_state=state,
            trial_short_below_structure_promotion_signal_score=signal_score,
            trial_short_below_structure_promotion_vwap_score=vwap_score,
            trial_short_below_structure_promotion_adx_1h=adx_1h,
            trial_short_below_structure_promotion_shrink_pct=shrink_pct,
            trial_short_below_structure_promotion_shrink_bars=shrink_bars,
            trial_short_below_structure_promotion_min_signal_score=min_signal_score,
            trial_short_below_structure_promotion_min_vwap_score=min_vwap_score,
            trial_short_below_structure_promotion_min_adx_1h=min_adx_1h,
            trial_short_below_structure_promotion_min_shrink_pct=min_shrink_pct,
            trial_short_below_structure_promotion_min_shrink_bars=min_shrink_bars,
        )

        if signal_type != "green_bar_growing":
            result["trial_short_below_structure_promotion_reason"] = "1h_signal_not_supported"
            return result
        if entry_type not in {"flip_bearish", "green_bar_growing"}:
            result["trial_short_below_structure_promotion_reason"] = "15m_entry_not_supported"
            return result
        if state != "short_below_session_above_structure":
            result["trial_short_below_structure_promotion_reason"] = "vwap_state_not_supported"
            return result
        if float(signal_score) < min_signal_score:
            result["trial_short_below_structure_promotion_reason"] = "signal_score_too_low"
            return result
        if float(vwap_score) < min_vwap_score:
            result["trial_short_below_structure_promotion_reason"] = "vwap_score_too_low"
            return result
        if float(adx_1h) < min_adx_1h:
            result["trial_short_below_structure_promotion_reason"] = "adx_1h_too_low"
            return result
        if shrink_pct < min_shrink_pct:
            result["trial_short_below_structure_promotion_reason"] = "4h_shrink_pct_too_low"
            return result
        if shrink_bars < min_shrink_bars:
            result["trial_short_below_structure_promotion_reason"] = "4h_shrink_bars_too_low"
            return result

        result.update(
            trial_short_below_structure_promotion_active=True,
            trial_short_below_structure_promotion_reason="trial_short_below_structure_promoted",
        )
        return result

    def resolve_4h_shrink_exit_policy(
        self,
        *,
        signal_details: Optional[Dict[str, Any]],
        position_side: str,
        position_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        details = signal_details if isinstance(signal_details, dict) else {}
        pos = position_context if isinstance(position_context, dict) else {}
        side = str(position_side or "").strip().upper()
        shrink_exit_direction = str(details.get("shrink_exit_direction", "") or "").strip().upper()
        shrink_pct = max(0.0, float(details.get("macd_4h_shrink_pct", 0.0) or 0.0))
        shrink_bars = max(0, int(details.get("macd_4h_shrink_bars", 0) or 0))

        default_required_bars = max(1, int(self.config.exit_4h_shrink_bars))
        default_required_pct = max(0.0, float(self.config.exit_4h_min_shrink_pct))
        continuation_required_bars = max(
            default_required_bars,
            int(self.config.stable_continuation_exit_4h_shrink_bars),
        )
        continuation_required_pct = max(
            default_required_pct,
            float(self.config.stable_continuation_exit_4h_min_shrink_pct),
        )

        stable_continuation_active = False
        continuation_source = ""
        pos_continuation_active = bool(pos.get("stable_continuation_active", False))
        pos_continuation_side = str(pos.get("stable_continuation_side", "") or "").strip().upper()
        if pos_continuation_active and pos_continuation_side == side:
            stable_continuation_active = True
            continuation_source = "position_context"

        required_bars = default_required_bars
        required_pct = default_required_pct
        mode = "default"
        if self.config.enable_stable_continuation_slow_4h_shrink_exit and stable_continuation_active:
            required_bars = continuation_required_bars
            required_pct = continuation_required_pct
            mode = "stable_continuation_slow"

        direction_match = bool(side and shrink_exit_direction == side)
        active = bool(
            direction_match
            and shrink_bars >= required_bars
            and shrink_pct >= required_pct
        )
        return {
            "active": active,
            "mode": mode,
            "direction_match": direction_match,
            "shrink_exit_direction": shrink_exit_direction,
            "shrink_pct": shrink_pct,
            "shrink_bars": shrink_bars,
            "required_bars": required_bars,
            "required_pct": required_pct,
            "stable_continuation_active": stable_continuation_active,
            "continuation_source": continuation_source,
        }
    
    # ==================== MACD 方向检测 ====================
    
    def detect_macd_direction(
        self,
        macd_hist: np.ndarray,
        idx: int
    ) -> Tuple[Optional[str], Dict]:
        """
        通用MACD方向检测。

        信号类型：
        - flip_bullish: 翻红（负转正），强度1.0
        - flip_bearish: 翻绿（正转负），强度1.0
        - red_bar_growing: 红柱增长，强度0.8
        - green_bar_growing: 绿柱增长，强度0.8
        - red_bar_shrinking: 红柱缩短，不入场
        - green_bar_shrinking: 绿柱缩短，不入场
        """
        if idx < 3:
            return None, {}
        
        details = {}
        hist_0 = macd_hist[idx]
        hist_1 = macd_hist[idx - 1]
        
        direction = None
        signal_type = None
        signal_strength = 0.0
        
        # 翻红检测（负转正）
        if hist_1 <= 0 and hist_0 > 0:
            direction = 'long'
            signal_type = 'flip_bullish'
            signal_strength = 1.0
            
        # 翻绿检测（正转负）
        elif hist_1 >= 0 and hist_0 < 0:
            direction = 'short'
            signal_type = 'flip_bearish'
            signal_strength = 1.0
            
        # 红柱增长
        elif hist_0 > 0 and hist_1 > 0 and hist_0 > hist_1:
            direction = 'long'
            signal_type = 'red_bar_growing'
            signal_strength = 0.8
            
        # 绿柱增长
        elif hist_0 < 0 and hist_1 < 0 and hist_0 < hist_1:
            direction = 'short'
            signal_type = 'green_bar_growing'
            signal_strength = 0.8
            
        # 红柱缩短（不入场）
        elif hist_0 > 0 and hist_1 > 0 and hist_0 < hist_1:
            direction = None
            signal_type = 'red_bar_shrinking'
            signal_strength = 0.0
            
        # 绿柱缩短（不入场）
        elif hist_0 < 0 and hist_1 < 0 and hist_0 > hist_1:
            direction = None
            signal_type = 'green_bar_shrinking'
            signal_strength = 0.0
        
        details['signal_type'] = signal_type
        details['signal_strength'] = signal_strength
        details['hist_current'] = hist_0
        details['hist_prev'] = hist_1
        
        return direction, details

    def detect_1h_macd_direction(
        self,
        macd_hist_1h: np.ndarray,
        idx: int
    ) -> Tuple[Optional[str], Dict]:
        """兼容旧调用：1H MACD方向检测。"""
        return self.detect_macd_direction(macd_hist_1h, idx)

    def resolve_primary_direction(
        self,
        *,
        direction_1h: Optional[str],
        details_1h: Dict[str, Any],
        direction_4h: Optional[str],
        details_4h: Dict[str, Any],
    ) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
        """根据配置决定交易主方向，并在 4H 主方向模式下检查 1H 辅助确认。"""
        mode = str(self.config.primary_direction_timeframe or "1h").strip().lower()
        debug: Dict[str, Any] = {
            "primary_direction_timeframe": mode,
            "direction_1h": direction_1h or "neutral",
            "direction_4h": direction_4h or "neutral",
        }

        if mode != "4h":
            return direction_1h, None, debug

        if direction_4h is None:
            return None, "4H无明确方向", debug

        if not self.config.require_1h_confirmation_when_4h_primary:
            return direction_4h, None, debug

        if direction_1h is None:
            if self.config.allow_neutral_1h_confirmation:
                debug["confirmation_status"] = "neutral_allowed"
                return direction_4h, None, debug
            debug["confirmation_status"] = "missing"
            return None, "1H无确认信号", debug

        if direction_1h != direction_4h:
            if self.use_light_1h_confirmation():
                debug["confirmation_status"] = "opposite_light_allowed"
                debug["confirmation_signal_type_1h"] = details_1h.get("signal_type")
                debug["light_confirmation_soft_pass"] = True
                debug["light_confirmation_opposite_direction"] = direction_1h
                return direction_4h, None, debug
            debug["confirmation_status"] = "opposite"
            return None, f"1H方向反向({direction_1h}->{direction_4h})", debug

        debug["confirmation_status"] = "aligned"
        debug["confirmation_signal_type_1h"] = details_1h.get("signal_type")
        return direction_4h, None, debug

    def use_light_1h_confirmation(self) -> bool:
        mode = str(self.config.primary_direction_timeframe or "1h").strip().lower()
        return mode == "4h" and bool(self.config.light_1h_confirmation_when_4h_primary)
    
    # ==================== MACD_4H 确认增强 ====================
    
    def check_4h_macd_enhancement(
        self,
        macd_hist_4h: np.ndarray,
        idx: int,
        direction: str
    ) -> Tuple[bool, float]:
        """MACD_4H确认方向增强"""
        if idx < 2:
            return False, 0.0
        
        hist_0 = macd_hist_4h[idx]
        hist_1 = macd_hist_4h[idx - 1]
        
        enhancement_score = 0.0
        is_enhanced = False
        
        if direction == 'long':
            if hist_0 > 0:
                enhancement_score += 0.5
                if hist_0 > hist_1:
                    enhancement_score += 0.3
                    is_enhanced = True
                elif hist_0 < hist_1:
                    enhancement_score += 0.1
                    
        elif direction == 'short':
            if hist_0 < 0:
                enhancement_score += 0.5
                if hist_0 < hist_1:
                    enhancement_score += 0.3
                    is_enhanced = True
                elif hist_0 > hist_1:
                    enhancement_score += 0.1
        
        return is_enhanced, enhancement_score
    
    # ==================== MACD_15M 跟随入场 ====================
    
    def check_15m_macd_follow(
        self,
        macd_hist_15m: np.ndarray,
        idx: int,
        direction: str,
        bb_middle_15m: float = None,
        bb_upper_15m: float = None,
        bb_lower_15m: float = None,
        close_15m: float = None
    ) -> Tuple[bool, float, Dict]:
        """
        MACD_15M跟随1H方向执行买卖
        
        V2.0新增：BOLL中轨精化入场点位
        """
        if idx < 2:
            return False, 0.0, {}
        
        details = {}
        hist_0 = macd_hist_15m[idx]
        hist_1 = macd_hist_15m[idx - 1]
        
        entry_score = 0.0
        can_enter = False
        
        if direction == 'long':
            # 刚翻红（最强入场）
            if hist_1 <= 0 and hist_0 > 0:
                entry_score = 1.0
                can_enter = True
                details['entry_type'] = 'flip_bullish'
                
            # 红柱增长
            elif hist_0 > 0 and hist_1 > 0 and hist_0 > hist_1:
                entry_score = 0.85
                can_enter = True
                details['entry_type'] = 'red_bar_growing'
                
            # 红柱稳定
            elif hist_0 > self.config.macd_threshold and hist_0 > 0:
                entry_score = 0.6
                can_enter = True
                details['entry_type'] = 'red_bar_stable'
                
            # 红柱缩短
            elif hist_0 > 0 and hist_1 > 0 and hist_0 < hist_1:
                entry_score = 0.3
                can_enter = True
                details['entry_type'] = 'red_bar_shrinking'
                
        elif direction == 'short':
            # 刚翻绿
            if hist_1 >= 0 and hist_0 < 0:
                entry_score = 1.0
                can_enter = True
                details['entry_type'] = 'flip_bearish'
                
            # 绿柱增长
            elif hist_0 < 0 and hist_1 < 0 and hist_0 < hist_1:
                entry_score = 0.85
                can_enter = True
                details['entry_type'] = 'green_bar_growing'
                
            # 绿柱稳定
            elif hist_0 < -self.config.macd_threshold and hist_0 < 0:
                entry_score = 0.6
                can_enter = True
                details['entry_type'] = 'green_bar_stable'
                
            # 绿柱缩短
            elif hist_0 < 0 and hist_1 < 0 and hist_0 > hist_1:
                entry_score = 0.3
                can_enter = True
                details['entry_type'] = 'green_bar_shrinking'
        
        # V2.0新增：BOLL中轨精化
        if can_enter and bb_middle_15m is not None and bb_middle_15m > 0 and close_15m is not None:
            band_half_width = 0.0
            if bb_upper_15m is not None and bb_lower_15m is not None and bb_upper_15m > bb_lower_15m:
                band_half_width = max((bb_upper_15m - bb_lower_15m) * 0.5, bb_middle_15m * 0.002)
            else:
                band_half_width = bb_middle_15m * 0.003
            near_middle = abs(close_15m - bb_middle_15m) <= band_half_width
            if direction == 'long':
                # 回踩BOLL中轨后反弹（最优）
                if near_middle:
                    entry_score = min(entry_score * 1.15, 1.0)
                    details['ema_15m_refine'] = 'midline_bounce'
                # 价格低于中轨（逆短期趋势）
                elif close_15m < bb_middle_15m:
                    entry_score *= 0.7
                    details['ema_15m_refine'] = 'below_midline'
                    
            elif direction == 'short':
                # 反弹至BOLL中轨后回落
                if near_middle:
                    entry_score = min(entry_score * 1.15, 1.0)
                    details['ema_15m_refine'] = 'midline_reject'
                # 价格高于中轨
                elif close_15m > bb_middle_15m:
                    entry_score *= 0.7
                    details['ema_15m_refine'] = 'above_midline'
        
        details['hist_current'] = hist_0
        details['hist_prev'] = hist_1
        details['base_entry_score'] = entry_score
        
        return can_enter, entry_score, details
    
    # ==================== VWAP 过滤层（新增） ====================
    
    def calculate_vwap_score(
        self,
        price: float,
        vwap: float,
        direction: str,
        *,
        structural_vwap: Optional[float] = None,
        price_series: Optional[np.ndarray] = None,
        session_vwap_series: Optional[np.ndarray] = None,
        structural_vwap_series: Optional[np.ndarray] = None,
    ) -> Tuple[float, VetoType, Dict[str, Any]]:
        """
        VWAP评分计算（位置状态 + 连续分数）
        
        返回：
        - score: 0-weight_vwap
        - veto_type: 是否触发否决
        - details: 位置状态与连续评分细节
        """
        structural_vwap_value = float(structural_vwap) if structural_vwap is not None else 0.0
        if structural_vwap_value <= 0.0:
            structural_vwap_value = self._series_value(structural_vwap_series, default=0.0)

        if vwap <= 0:
            location_score = 0.5
            return round(self.config.weight_vwap * location_score, 4), VetoType.NONE, {
                "state": "no_vwap",
                "location_score": location_score,
                "entry_edge": 0.0,
                "directional_extension": 0.0,
                "entry_edge_quality": 0.5,
                "value_proximity_quality": 0.5,
                "extension_quality": 0.5,
                "session_vwap": vwap,
                "structural_vwap": structural_vwap_value,
                "session_deviation": 0.0,
                "structural_deviation": 0.0,
            }

        deviation = (price - vwap) / vwap
        if structural_vwap_value <= 0:
            if direction == 'long':
                directional_extension = deviation
                entry_edge = -deviation
                if directional_extension > self.config.vwap_deviation_hard_block:
                    return 0.0, VetoType.VWAP_HARD_BLOCK, {
                        "state": "long_overextended_above_value",
                        "location_score": 0.0,
                        "entry_edge": entry_edge,
                        "directional_extension": directional_extension,
                        "session_vwap": vwap,
                        "structural_vwap": structural_vwap_value,
                        "session_deviation": deviation,
                        "structural_deviation": deviation,
                    }
                if deviation >= self.config.vwap_deviation_warning:
                    state = "long_above_value_extension"
                elif deviation >= self.config.vwap_deviation_optimal:
                    state = "long_above_value"
                elif deviation >= -self.config.vwap_deviation_optimal:
                    state = "long_value_reclaim"
                elif deviation >= -self.config.vwap_deviation_warning:
                    state = "long_discount_pullback"
                else:
                    state = "long_deep_discount"
            elif direction == 'short':
                directional_extension = -deviation
                entry_edge = deviation
                if directional_extension > self.config.vwap_deviation_hard_block:
                    return 0.0, VetoType.VWAP_HARD_BLOCK, {
                        "state": "short_overextended_below_value",
                        "location_score": 0.0,
                        "entry_edge": entry_edge,
                        "directional_extension": directional_extension,
                        "session_vwap": vwap,
                        "structural_vwap": structural_vwap_value,
                        "session_deviation": deviation,
                        "structural_deviation": deviation,
                    }
                if deviation <= -self.config.vwap_deviation_warning:
                    state = "short_below_value_extension"
                elif deviation <= -self.config.vwap_deviation_optimal:
                    state = "short_below_value"
                elif deviation <= self.config.vwap_deviation_optimal:
                    state = "short_value_reject"
                elif deviation <= self.config.vwap_deviation_warning:
                    state = "short_premium_retest"
                else:
                    state = "short_stretched_premium"
            else:
                location_score = 0.5
                return round(self.config.weight_vwap * location_score, 4), VetoType.NONE, {
                    "state": "neutral",
                    "location_score": location_score,
                    "entry_edge": 0.0,
                    "directional_extension": 0.0,
                    "entry_edge_quality": 0.5,
                    "value_proximity_quality": 0.5,
                    "extension_quality": 0.5,
                    "session_vwap": vwap,
                    "structural_vwap": structural_vwap_value,
                    "session_deviation": deviation,
                    "structural_deviation": deviation,
                }

            hard_block = max(self.config.vwap_deviation_hard_block, 1e-9)
            warning = max(self.config.vwap_deviation_warning, 1e-9)
            entry_edge_quality = 0.5 + 0.5 * self._clamp(entry_edge / warning, -1.0, 1.0)
            value_proximity_quality = 1.0 - self._clamp(abs(deviation) / hard_block, 0.0, 1.0)
            extension_quality = 1.0 - self._clamp(max(0.0, directional_extension) / hard_block, 0.0, 1.0)
            location_score = self._clamp(
                0.75 * entry_edge_quality
                + 0.15 * value_proximity_quality
                + 0.10 * extension_quality,
                0.0,
                1.0,
            )
            score = round(self.config.weight_vwap * location_score, 4)
            return score, VetoType.NONE, {
                "state": state,
                "location_score": location_score,
                "entry_edge": entry_edge,
                "directional_extension": directional_extension,
                "entry_edge_quality": entry_edge_quality,
                "value_proximity_quality": value_proximity_quality,
                "extension_quality": extension_quality,
                "session_vwap": vwap,
                "structural_vwap": structural_vwap_value,
                "session_deviation": deviation,
                "structural_deviation": deviation,
            }

        structural_deviation = (price - structural_vwap_value) / structural_vwap_value
        tolerance = max(self.config.vwap_retest_tolerance, 1e-4)
        warning = max(self.config.vwap_deviation_warning, tolerance * 2.0)
        hard_block = max(self.config.vwap_deviation_hard_block, warning + 1e-6)
        prev_price = self._series_value(price_series, offset=-2, default=price)
        prev_session_vwap = self._series_value(session_vwap_series, offset=-2, default=vwap)
        prev_structural_vwap = self._series_value(
            structural_vwap_series,
            offset=-2,
            default=structural_vwap_value,
        )

        session_below = deviation < -tolerance
        session_above = deviation > tolerance
        structure_below = structural_deviation < -tolerance
        structure_above = structural_deviation > tolerance
        session_near = abs(deviation) <= tolerance
        structure_near = abs(structural_deviation) <= tolerance

        short_retest_reject = (
            prev_session_vwap > 0
            and prev_price >= prev_session_vwap * (1.0 - tolerance)
            and price < vwap * (1.0 - tolerance)
            and (structure_below or structure_near)
            and prev_price <= prev_structural_vwap * (1.0 + warning)
        )
        long_reclaim_confirmed = (
            prev_session_vwap > 0
            and prev_price <= prev_session_vwap * (1.0 + tolerance)
            and price > vwap * (1.0 + tolerance)
            and (not structure_above or price >= structural_vwap_value * (1.0 - tolerance))
            and prev_price >= prev_structural_vwap * (1.0 - warning)
        )

        if direction == 'short':
            directional_extension = -deviation
            entry_edge = deviation
            if directional_extension > self.config.vwap_deviation_hard_block:
                return 0.0, VetoType.VWAP_HARD_BLOCK, {
                    "state": "short_overextended_below_value",
                    "location_score": 0.0,
                    "entry_edge": entry_edge,
                    "directional_extension": directional_extension,
                    "session_vwap": vwap,
                    "structural_vwap": structural_vwap_value,
                    "session_deviation": deviation,
                    "structural_deviation": structural_deviation,
                }
            if short_retest_reject:
                state = "short_retest_reject"
            elif session_below and structure_below:
                state = "short_dual_pressure"
            elif structure_below and (session_near or session_above):
                state = "short_under_structure_wait_reject"
            elif session_below and (structure_near or structure_above):
                state = "short_below_session_above_structure"
            else:
                state = "short_above_both"

            session_pressure_quality = self._clamp((-deviation + tolerance) / (warning + tolerance), 0.0, 1.0)
            structural_pressure_quality = self._clamp(
                (-structural_deviation + tolerance) / (warning + tolerance),
                0.0,
                1.0,
            )
            extension_penalty = self._clamp(
                max(0.0, -deviation - warning) / max(hard_block - warning, 1e-9),
                0.0,
                1.0,
            )
            retest_quality = 1.0 if short_retest_reject else 0.0
            dual_pressure_quality = 1.0 if (session_below and structure_below) else 0.0
            value_proximity_quality = 1.0 - self._clamp(abs(deviation) / hard_block, 0.0, 1.0)
            extension_quality = 1.0 - extension_penalty
            location_score = self._clamp(
                0.36 * session_pressure_quality
                + 0.26 * structural_pressure_quality
                + 0.18 * retest_quality
                + 0.12 * dual_pressure_quality
                + 0.08 * value_proximity_quality
                - 0.12 * extension_penalty,
                0.0,
                1.0,
            )
            score = round(self.config.weight_vwap * location_score, 4)
            return score, VetoType.NONE, {
                "state": state,
                "location_score": location_score,
                "entry_edge": entry_edge,
                "directional_extension": directional_extension,
                "entry_edge_quality": session_pressure_quality,
                "value_proximity_quality": value_proximity_quality,
                "extension_quality": extension_quality,
                "structure_quality": structural_pressure_quality,
                "retest_quality": retest_quality,
                "dual_pressure_quality": dual_pressure_quality,
                "session_vwap": vwap,
                "structural_vwap": structural_vwap_value,
                "session_deviation": deviation,
                "structural_deviation": structural_deviation,
            }

        if direction == 'long':
            directional_extension = deviation
            entry_edge = -deviation
            if directional_extension > self.config.vwap_deviation_hard_block:
                return 0.0, VetoType.VWAP_HARD_BLOCK, {
                    "state": "long_overextended_above_value",
                    "location_score": 0.0,
                    "entry_edge": entry_edge,
                    "directional_extension": directional_extension,
                    "session_vwap": vwap,
                    "structural_vwap": structural_vwap_value,
                    "session_deviation": deviation,
                    "structural_deviation": structural_deviation,
                }
            if long_reclaim_confirmed:
                state = "long_reclaim_confirmed"
            elif session_above and structure_above:
                state = "long_dual_support"
            elif structure_above and (session_near or session_below):
                state = "long_above_structure_wait_reclaim"
            elif session_above and (structure_near or structure_below):
                state = "long_above_session_below_structure"
            else:
                state = "long_below_both"

            session_support_quality = self._clamp((deviation + tolerance) / (warning + tolerance), 0.0, 1.0)
            structural_support_quality = self._clamp(
                (structural_deviation + tolerance) / (warning + tolerance),
                0.0,
                1.0,
            )
            extension_penalty = self._clamp(
                max(0.0, deviation - warning) / max(hard_block - warning, 1e-9),
                0.0,
                1.0,
            )
            reclaim_quality = 1.0 if long_reclaim_confirmed else 0.0
            dual_support_quality = 1.0 if (session_above and structure_above) else 0.0
            value_proximity_quality = 1.0 - self._clamp(abs(deviation) / hard_block, 0.0, 1.0)
            extension_quality = 1.0 - extension_penalty
            location_score = self._clamp(
                0.36 * session_support_quality
                + 0.26 * structural_support_quality
                + 0.18 * reclaim_quality
                + 0.12 * dual_support_quality
                + 0.08 * value_proximity_quality
                - 0.12 * extension_penalty,
                0.0,
                1.0,
            )
            score = round(self.config.weight_vwap * location_score, 4)
            return score, VetoType.NONE, {
                "state": state,
                "location_score": location_score,
                "entry_edge": entry_edge,
                "directional_extension": directional_extension,
                "entry_edge_quality": session_support_quality,
                "value_proximity_quality": value_proximity_quality,
                "extension_quality": extension_quality,
                "structure_quality": structural_support_quality,
                "reclaim_quality": reclaim_quality,
                "dual_support_quality": dual_support_quality,
                "session_vwap": vwap,
                "structural_vwap": structural_vwap_value,
                "session_deviation": deviation,
                "structural_deviation": structural_deviation,
            }

        location_score = 0.5
        return round(self.config.weight_vwap * location_score, 4), VetoType.NONE, {
            "state": "neutral",
            "location_score": location_score,
            "entry_edge": 0.0,
            "directional_extension": 0.0,
            "entry_edge_quality": 0.5,
            "value_proximity_quality": 0.5,
            "extension_quality": 0.5,
            "session_vwap": vwap,
            "structural_vwap": structural_vwap_value,
            "session_deviation": deviation,
            "structural_deviation": structural_deviation,
        }

    def check_flip_bearish_structure(
        self,
        signal_type_1h: str,
        adx_1h: float,
        bb_middle_slope_1h: float,
        bb_middle_slope_4h: float,
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        if signal_type_1h != 'flip_bearish':
            return True, [], {}

        reasons: List[str] = []
        details = {
            "adx_1h": adx_1h,
            "bb_middle_slope_1h": bb_middle_slope_1h,
            "bb_middle_slope_4h": bb_middle_slope_4h,
            "flip_bearish_min_adx_1h": self.config.flip_bearish_min_adx_1h,
            "flip_bearish_max_bb_middle_slope_1h": self.config.flip_bearish_max_ema21_slope_1h,
            "flip_bearish_max_bb_middle_slope_4h": self.config.flip_bearish_max_ema21_slope_4h,
        }

        if self.config.flip_bearish_min_adx_1h > 0 and adx_1h < self.config.flip_bearish_min_adx_1h:
            reasons.append(f"adx_1h={adx_1h:.2f}<{self.config.flip_bearish_min_adx_1h:.2f}")
        if bb_middle_slope_1h > self.config.flip_bearish_max_ema21_slope_1h:
            reasons.append(
                f"bb_middle_slope_1h={bb_middle_slope_1h:.4f}>{self.config.flip_bearish_max_ema21_slope_1h:.4f}"
            )
        if bb_middle_slope_4h > self.config.flip_bearish_max_ema21_slope_4h:
            reasons.append(
                f"bb_middle_slope_4h={bb_middle_slope_4h:.4f}>{self.config.flip_bearish_max_ema21_slope_4h:.4f}"
            )

        return len(reasons) == 0, reasons, details

    def check_flip_bearish_vwap_context(
        self,
        signal_type_1h: str,
        vwap_state: str,
        vwap_score: float,
        structural_vwap: float,
        session_deviation: float,
        structural_deviation: float,
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        if signal_type_1h != 'flip_bearish':
            return True, [], {}

        reasons: List[str] = []
        allowed_states = {"short_retest_reject", "short_dual_pressure"}
        if structural_vwap <= 0:
            reasons.append("structural_vwap_missing")
        if vwap_state not in allowed_states:
            reasons.append(f"vwap_state={vwap_state}")
        retest_reject_min_vwap_score = max(0.0, self.config.flip_bearish_retest_reject_min_vwap_score)
        if (
            vwap_state == "short_retest_reject"
            and retest_reject_min_vwap_score > 0
            and vwap_score < retest_reject_min_vwap_score
        ):
            reasons.append(
                f"short_retest_reject_vwap_score={vwap_score:.2f}<{retest_reject_min_vwap_score:.2f}"
            )

        details = {
            "flip_bearish_allowed_vwap_states": sorted(allowed_states),
            "vwap_state": vwap_state,
            "vwap_score": vwap_score,
            "flip_bearish_retest_reject_min_vwap_score": retest_reject_min_vwap_score,
            "structural_vwap": structural_vwap,
            "session_vwap_deviation": session_deviation,
            "structural_vwap_deviation": structural_deviation,
        }
        return len(reasons) == 0, reasons, details

    # ==================== BOLL 结构层（新增） ====================
    
    def check_boll_structure(
        self,
        close_1h: float,
        bb_middle_1h: float,
        bb_upper_1h: float,
        bb_lower_1h: float,
        bb_middle_4h: float = 0.0,
        bb_upper_4h: float = 0.0,
        bb_lower_4h: float = 0.0,
        direction: str = None
    ) -> Tuple[float, str, VetoType]:
        """
        BOLL结构检查

        返回：
        - multiplier: 结构修正系数 (0.6/1.0/1.2)
        - status: 结构状态
        - veto_type: 否决类型
        """
        if bb_middle_1h <= 0 or bb_upper_1h <= bb_lower_1h:
            return self.config.ema_multiplier_normal, 'neutral', VetoType.NONE

        band_half_1h = max((bb_upper_1h - bb_lower_1h) * 0.5, 1e-9)
        pos_1h = (close_1h - bb_middle_1h) / band_half_1h

        pos_4h = 0.0
        if bb_middle_4h > 0 and bb_upper_4h > bb_lower_4h:
            band_half_4h = max((bb_upper_4h - bb_lower_4h) * 0.5, 1e-9)
            pos_4h = (close_1h - bb_middle_4h) / band_half_4h

        if direction == 'long':
            if pos_1h >= 0.20 and pos_4h >= 0.10:
                return self.config.ema_multiplier_strong, 'strong', VetoType.NONE
            if pos_1h >= 0.0:
                return self.config.ema_multiplier_normal, 'normal', VetoType.NONE
            if close_1h >= bb_lower_1h:
                return self.config.ema_multiplier_weak, 'weak', VetoType.NONE
            return self.config.ema_multiplier_weak, 'against', VetoType.NONE

        if direction == 'short':
            if pos_1h <= -0.20 and pos_4h <= -0.10:
                return self.config.ema_multiplier_strong, 'strong', VetoType.NONE
            if pos_1h <= 0.0:
                return self.config.ema_multiplier_normal, 'normal', VetoType.NONE
            if close_1h <= bb_upper_1h:
                return self.config.ema_multiplier_weak, 'weak', VetoType.NONE
            return self.config.ema_multiplier_weak, 'against', VetoType.NONE

        return self.config.ema_multiplier_normal, 'neutral', VetoType.NONE
    
    def check_boll_deviation_veto(
        self,
        close_1h: float,
        bb_middle_1h: float,
        bb_upper_1h: float,
        bb_lower_1h: float,
        signal_type_1h: str
    ) -> VetoType:
        """
        否决项V5：MACD翻色时价格已远离BOLL中轨且明显超带，不追单
        """
        if signal_type_1h not in ['flip_bullish', 'flip_bearish']:
            return VetoType.NONE
        
        if bb_middle_1h <= 0 or bb_upper_1h <= bb_lower_1h:
            return VetoType.NONE
        
        band_half = max((bb_upper_1h - bb_lower_1h) * 0.5, 1e-9)
        band_extension = abs(close_1h - bb_middle_1h) / band_half
        
        if band_extension > 1.8:
            return VetoType.MACD_HIGH_DEVIATION
        
        return VetoType.NONE

    # ==================== 止损计算 ====================
    
    def calculate_dynamic_stop(
        self,
        entry_price: float,
        close_1h: float,
        bb_middle_1h: float,
        bb_upper_1h: float,
        bb_lower_1h: float,
        atr_1h: float,
        vwap: float,
        direction: str
    ) -> Tuple[float, float, Dict]:
        """
        计算动态止损
        
        返回：
        - stop_price: 止损价格
        - stop_pct: 止损百分比
        - details: 详情
        """
        details = {}
        
        if direction == 'long':
            if bb_middle_1h > 0 and close_1h >= bb_middle_1h:
                stop = bb_middle_1h - atr_1h * self.config.ema_stop_atr_multiplier
                details['stop_anchor'] = 'bb_middle'
            elif bb_lower_1h > 0:
                stop = bb_lower_1h - atr_1h * 0.2
                details['stop_anchor'] = 'bb_lower'
            else:
                stop = entry_price * (1 - self.config.max_stop_loss_pct)
                details['stop_anchor'] = 'default'
            
            max_stop = entry_price * (1 - self.config.max_stop_loss_pct)
            stop = max(stop, max_stop)
            
            if vwap > 0:
                vwap_alert = vwap * (1 - self.config.vwap_alert_deviation)
                details['vwap_alert_price'] = vwap_alert
            
        elif direction == 'short':
            if bb_middle_1h > 0 and close_1h <= bb_middle_1h:
                stop = bb_middle_1h + atr_1h * self.config.ema_stop_atr_multiplier
                details['stop_anchor'] = 'bb_middle'
            elif bb_upper_1h > 0:
                stop = bb_upper_1h + atr_1h * 0.2
                details['stop_anchor'] = 'bb_upper'
            else:
                stop = entry_price * (1 + self.config.max_stop_loss_pct)
                details['stop_anchor'] = 'default'
            
            max_stop = entry_price * (1 + self.config.max_stop_loss_pct)
            stop = min(stop, max_stop)
            
            if vwap > 0:
                vwap_alert = vwap * (1 + self.config.vwap_alert_deviation)
                details['vwap_alert_price'] = vwap_alert
        else:
            stop = entry_price * (1 - self.config.max_stop_loss_pct)
            details['stop_anchor'] = 'default'
        
        stop_pct = abs(entry_price - stop) / entry_price
        details['stop_price'] = stop
        details['stop_pct'] = stop_pct
        
        return stop, stop_pct, details
    
    # ==================== 综合分析 ====================
    
    def analyze(
        self,
        macd_hist_15m: np.ndarray,
        macd_hist_1h: np.ndarray,
        macd_hist_4h: np.ndarray,
        idx_15m: int,
        idx_1h: int,
        idx_4h: int,
        volume_ratio: float = 1.0,
        # VWAP数据
        vwap: float = 0.0,
        structural_vwap: float = 0.0,
        close_price: float = 0.0,
        # BOLL数据
        bb_middle_1h: float = 0.0,
        bb_upper_1h: float = 0.0,
        bb_lower_1h: float = 0.0,
        bb_middle_4h: float = 0.0,
        bb_upper_4h: float = 0.0,
        bb_lower_4h: float = 0.0,
        bb_middle_15m: float = 0.0,
        bb_upper_15m: float = 0.0,
        bb_lower_15m: float = 0.0,
        close_15m: float = 0.0,
        close_1h_series: Optional[np.ndarray] = None,
        close_4h_series: Optional[np.ndarray] = None,
        vwap_1h_series: Optional[np.ndarray] = None,
        structural_vwap_1h_series: Optional[np.ndarray] = None,
        adx_1h: float = 0.0,
        adx_4h: float = 0.0,
        bb_middle_slope_1h: Optional[float] = None,
        bb_middle_slope_4h: Optional[float] = None,
        cvd_upper_wick_ratio: Optional[float] = None,
        cvd_1h_delta_ratio: Optional[float] = None,
        # ATR
        atr_1h: float = 0.0,
        # 空头质量过滤参数（V3专家组建议）
        funding_rate: float = 0.0,
        oi_delta_ratio: float = 0.0
    ) -> MACDSignalV2:
        """
        V2.0综合分析
        """
        debug_details = self._build_debug_details(
            strategy="macd_mtf_strategy_v2",
            stage="1h_direction",
            close_price=close_price,
            close_15m=close_15m,
            vwap=vwap,
            session_vwap=vwap,
            structural_vwap=structural_vwap,
            atr_1h=atr_1h,
            volume_ratio=volume_ratio,
            adx_1h=adx_1h,
            adx_4h=adx_4h,
        )
        if cvd_upper_wick_ratio is not None:
            debug_details["cvd_upper_wick_ratio"] = float(cvd_upper_wick_ratio)
        if cvd_1h_delta_ratio is not None:
            debug_details["cvd_1h_delta_ratio"] = float(cvd_1h_delta_ratio)
        if structural_vwap <= 0 and structural_vwap_1h_series is not None:
            structural_vwap = self._series_value(structural_vwap_1h_series, default=0.0)
        if bb_middle_1h <= 0 and close_1h_series is not None:
            middle_1h, upper_1h, lower_1h = self.calculate_bollinger_bands(
                np.asarray(close_1h_series, dtype=float),
                period=self.config.boll_period,
                std_dev=self.config.boll_std_dev,
            )
            bb_middle_1h = float(middle_1h[-1])
            bb_upper_1h = float(upper_1h[-1])
            bb_lower_1h = float(lower_1h[-1])
        if bb_middle_4h <= 0 and close_4h_series is not None:
            middle_4h, upper_4h, lower_4h = self.calculate_bollinger_bands(
                np.asarray(close_4h_series, dtype=float),
                period=self.config.boll_period,
                std_dev=self.config.boll_std_dev,
            )
            bb_middle_4h = float(middle_4h[-1])
            bb_upper_4h = float(upper_4h[-1])
            bb_lower_4h = float(lower_4h[-1])

        primary_mode = str(self.config.primary_direction_timeframe or "1h").strip().lower()
        light_1h_confirmation = self.use_light_1h_confirmation()

        # ========== Step 1: MACD_1H 辅助方向状态 ==========
        direction_1h, details_1h = self.detect_1h_macd_direction(macd_hist_1h, idx_1h)
        debug_details.update(
            direction_1h=direction_1h or "neutral",
            signal_type_1h=details_1h.get("signal_type"),
            signal_strength_1h=details_1h.get("signal_strength", 0.0),
            macd_1h_hist_current=details_1h.get("hist_current"),
            macd_1h_hist_prev=details_1h.get("hist_prev"),
        )

        # ========== Step 1.5: MACD_4H 主方向/主评分状态 ==========
        direction_4h, details_4h = self.detect_macd_direction(macd_hist_4h, idx_4h)
        signal_type_4h = str(details_4h.get("signal_type", ""))
        signal_strength_4h = float(details_4h.get("signal_strength", 0.0) or 0.0)
        shrink_4h_context = self._build_4h_shrink_context(macd_hist_4h, idx_4h, signal_type_4h)
        stable_trend_context = self._build_stable_trend_context(macd_hist_4h, idx_4h)
        debug_details.update(
            primary_timeframe=primary_mode,
            direction_4h=direction_4h or "neutral",
            signal_type_4h=signal_type_4h,
            signal_strength_4h=signal_strength_4h,
            macd_4h_hist_current=details_4h.get("hist_current"),
            macd_4h_hist_prev=details_4h.get("hist_prev"),
            macd_4h_shrink_pct=shrink_4h_context["shrink_pct"],
            macd_4h_shrink_bars=shrink_4h_context["shrink_bars"],
            macd_4h_preflip_direction=shrink_4h_context["preflip_direction"],
            macd_4h_exit_direction=shrink_4h_context["exit_direction"],
            macd_4h_shrink_exit_ready=shrink_4h_context["shrink_exit_ready"],
            shrink_exit_direction=shrink_4h_context["exit_direction"],
            shrink_exit_ready=shrink_4h_context["shrink_exit_ready"],
            stable_4h_hist_current=stable_trend_context["hist_current"],
            stable_4h_positive_bars=stable_trend_context["positive_bars"],
            stable_4h_negative_bars=stable_trend_context["negative_bars"],
            stable_4h_bull_active=stable_trend_context["bull_active"],
            stable_4h_bear_active=stable_trend_context["bear_active"],
        )

        trade_direction: Optional[str] = None
        direction_reject_reason: Optional[str] = None
        direction_debug: Dict[str, Any] = {}
        is_trial_entry = False
        entry_scale = 1.0
        stable_continuation_active = False
        stable_continuation_side: Optional[str] = None

        preflip_candidate_direction = shrink_4h_context.get("preflip_direction")
        preflip_enabled = (
            primary_mode == "4h"
            and bool(self.config.enable_4h_preflip_trial_entries)
            and preflip_candidate_direction is not None
        )
        if preflip_enabled and direction_4h is None:
            shrink_pct = float(shrink_4h_context["shrink_pct"])
            min_preflip_shrink_pct = (
                float(self.config.preflip_trial_min_shrink_pct_long)
                if preflip_candidate_direction == "long"
                else float(self.config.preflip_trial_min_shrink_pct_short)
            )
            direction_debug = {
                "preflip_trial_enabled": True,
                "preflip_candidate_direction": preflip_candidate_direction,
                "preflip_min_shrink_pct": min_preflip_shrink_pct,
            }
            if direction_1h is None:
                if self.config.allow_neutral_1h_confirmation:
                    direction_debug["preflip_confirmation_status"] = "neutral_allowed"
                else:
                    direction_reject_reason = "1H无预翻转确认信号"
                    direction_debug["preflip_confirmation_status"] = "missing"
            elif direction_1h != preflip_candidate_direction:
                direction_reject_reason = f"1H预翻转方向不一致({direction_1h}->{preflip_candidate_direction})"
                direction_debug["preflip_confirmation_status"] = "opposite"
            if direction_reject_reason is None and shrink_pct < min_preflip_shrink_pct:
                direction_reject_reason = f"4H预翻转缩短不足({shrink_pct:.2f}<{min_preflip_shrink_pct:.2f})"
                direction_debug["preflip_confirmation_status"] = "insufficient_shrink"
            elif direction_reject_reason is None:
                trade_direction = preflip_candidate_direction
                is_trial_entry = True
                entry_scale = self._clamp(self.config.preflip_trial_entry_scale, 0.05, 1.0)
                direction_debug.update(
                    preflip_confirmation_status="aligned",
                    preflip_trial_entry=True,
                    preflip_entry_scale=entry_scale,
                )
        else:
            trade_direction, direction_reject_reason, direction_debug = self.resolve_primary_direction(
                direction_1h=direction_1h,
                details_1h=details_1h,
                direction_4h=direction_4h,
                details_4h=details_4h,
            )
            if trade_direction is None:
                recovered_direction, continuation_direction_debug = self._resolve_stable_continuation_direction(
                    primary_mode=primary_mode,
                    direction_1h=direction_1h,
                    details_1h=details_1h,
                    stable_trend_context=stable_trend_context,
                )
                direction_debug.update(continuation_direction_debug)
                if recovered_direction is not None:
                    trade_direction = recovered_direction
                    direction_reject_reason = None
        debug_details.update(
            **direction_debug,
            trade_direction=trade_direction or "neutral",
            light_1h_confirmation=light_1h_confirmation,
            is_trial_entry=is_trial_entry,
            entry_scale=entry_scale,
        )
        if trade_direction is None:
            return self._neutral_signal(
                reason=direction_reject_reason or "主方向无明确结论",
                signal_type_1h=details_1h.get('signal_type'),
                details=self._build_debug_details(
                    **debug_details,
                ),
            )
        strict_1h_filters_enabled = not (light_1h_confirmation or is_trial_entry)
        
        # ========== Step 2: BOLL结构检查 ==========
        ema_multiplier, ema_status, ema_veto = self.check_boll_structure(
            close_1h=close_price,
            bb_middle_1h=bb_middle_1h,
            bb_upper_1h=bb_upper_1h,
            bb_lower_1h=bb_lower_1h,
            bb_middle_4h=bb_middle_4h,
            bb_upper_4h=bb_upper_4h,
            bb_lower_4h=bb_lower_4h,
            direction=trade_direction
        )
        debug_details = self._set_stage(
            debug_details,
            "boll_structure",
            ema_multiplier=ema_multiplier,
            ema_status=ema_status,
            ema_veto=ema_veto.value if ema_veto else VetoType.NONE.value,
            bb_middle_1h=bb_middle_1h,
            bb_upper_1h=bb_upper_1h,
            bb_lower_1h=bb_lower_1h,
            bb_middle_4h=bb_middle_4h,
            bb_upper_4h=bb_upper_4h,
            bb_lower_4h=bb_lower_4h,
        )
        
        # BOLL硬性否决
        if ema_veto == VetoType.EMA_1H_BREAK and self.config.ema_55_1h_hard_block:
            return self._neutral_signal(
                reason='boll_structure_veto',
                veto_type=ema_veto,
                veto_reason=f"BOLL结构反向：价格{'跌破' if trade_direction == 'long' else '突破'}BOLL中轨",
                ema_structure_status=ema_status,
                signal_type_1h=details_1h.get('signal_type'),
                ema_multiplier=ema_multiplier,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )
        
        # 检查高偏离否决
        deviation_veto = VetoType.NONE
        if strict_1h_filters_enabled:
            deviation_veto = self.check_boll_deviation_veto(
                close_price,
                bb_middle_1h,
                bb_upper_1h,
                bb_lower_1h,
                details_1h.get('signal_type', '')
            )
            debug_details = self._set_stage(
                debug_details,
                "boll_deviation",
                bb_middle_deviation=abs(close_price - bb_middle_1h) / bb_middle_1h if bb_middle_1h > 0 else 0.0,
                deviation_veto=deviation_veto.value if deviation_veto else VetoType.NONE.value,
            )
            if deviation_veto != VetoType.NONE:
                return self._neutral_signal(
                    reason='high_deviation_veto',
                    veto_type=deviation_veto,
                    veto_reason="MACD翻色时价格已远离BOLL中轨",
                    signal_type_1h=details_1h.get('signal_type'),
                    ema_multiplier=ema_multiplier,
                    ema_structure_status=ema_status,
                    details=self._build_debug_details(
                        **debug_details,
                    ),
                )
        else:
            debug_details = self._set_stage(
                debug_details,
                "boll_deviation",
                bb_middle_deviation=abs(close_price - bb_middle_1h) / bb_middle_1h if bb_middle_1h > 0 else 0.0,
                deviation_veto=VetoType.NONE.value,
                deviation_filter_skipped=True,
            )
        
        bb_middle_slope_1h = (
            float(bb_middle_slope_1h)
            if bb_middle_slope_1h is not None
            else self._boll_middle_slope_from_series(
                close_1h_series,
                self.config.boll_period,
                self.config.boll_std_dev,
                max(1, int(self.config.ema_slope_lookback_1h)),
            )
        )
        bb_middle_slope_4h = (
            float(bb_middle_slope_4h)
            if bb_middle_slope_4h is not None
            else self._boll_middle_slope_from_series(
                close_4h_series,
                self.config.boll_period,
                self.config.boll_std_dev,
                max(1, int(self.config.ema_slope_lookback_4h)),
            )
        )
        debug_details.update(
            bb_middle_slope_1h=bb_middle_slope_1h,
            bb_middle_slope_4h=bb_middle_slope_4h,
        )

        # ========== Step 3: VWAP评分 ==========
        vwap_score, vwap_veto, vwap_details = self.calculate_vwap_score(
            close_price,
            vwap,
            trade_direction,
            structural_vwap=structural_vwap,
            price_series=close_1h_series,
            session_vwap_series=vwap_1h_series,
            structural_vwap_series=structural_vwap_1h_series,
        )
        vwap_deviation = (close_price - vwap) / vwap if vwap > 0 else 0.0
        vwap_state = str(vwap_details.get("state", "unknown"))
        vwap_location_score = float(vwap_details.get("location_score", 0.0))
        structural_vwap_deviation = float(vwap_details.get("structural_deviation", 0.0))
        debug_details = self._set_stage(
            debug_details,
            "vwap",
            vwap_score=vwap_score,
            vwap_deviation=vwap_deviation,
            vwap_state=vwap_state,
            vwap_location_score=vwap_location_score,
            session_vwap=vwap_details.get("session_vwap", vwap),
            structural_vwap=vwap_details.get("structural_vwap", structural_vwap),
            session_vwap_deviation=vwap_details.get("session_deviation", vwap_deviation),
            structural_vwap_deviation=structural_vwap_deviation,
            vwap_entry_edge=vwap_details.get("entry_edge", 0.0),
            vwap_directional_extension=vwap_details.get("directional_extension", 0.0),
            vwap_entry_edge_quality=vwap_details.get("entry_edge_quality", 0.0),
            vwap_value_proximity_quality=vwap_details.get("value_proximity_quality", 0.0),
            vwap_extension_quality=vwap_details.get("extension_quality", 0.0),
            vwap_structure_quality=vwap_details.get("structure_quality", 0.0),
            vwap_retest_quality=vwap_details.get("retest_quality", vwap_details.get("reclaim_quality", 0.0)),
            vwap_dual_pressure_quality=vwap_details.get(
                "dual_pressure_quality",
                vwap_details.get("dual_support_quality", 0.0),
            ),
            vwap_veto=vwap_veto.value if vwap_veto else VetoType.NONE.value,
        )
        
        # VWAP硬性否决
        if vwap_veto == VetoType.VWAP_HARD_BLOCK:
            return self._neutral_signal(
                reason='vwap_hard_block',
                veto_type=vwap_veto,
                veto_reason=f"VWAP偏离超过{self.config.vwap_deviation_hard_block*100:.1f}%",
                signal_type_1h=details_1h.get('signal_type'),
                vwap_score=0.0,
                vwap_deviation=vwap_deviation,
                vwap_state=vwap_state,
                vwap_location_score=vwap_location_score,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )

        if strict_1h_filters_enabled:
            vwap_context_ok, vwap_context_reasons, vwap_context_details = self.check_flip_bearish_vwap_context(
                signal_type_1h=details_1h.get('signal_type', ''),
                vwap_state=vwap_state,
                vwap_score=vwap_score,
                structural_vwap=float(vwap_details.get("structural_vwap", structural_vwap)),
                session_deviation=float(vwap_details.get("session_deviation", vwap_deviation)),
                structural_deviation=structural_vwap_deviation,
            )
            if not vwap_context_ok:
                debug_details = self._set_stage(
                    debug_details,
                    "flip_bearish_vwap_filter",
                    flip_bearish_vwap_context_passed=False,
                    flip_bearish_vwap_context_reasons=vwap_context_reasons,
                    **vwap_context_details,
                )
                return self._neutral_signal(
                    reason=f'flip_bearish_vwap_filter({"; ".join(vwap_context_reasons)})',
                    signal_type_1h=details_1h.get('signal_type'),
                    vwap_score=vwap_score,
                    vwap_deviation=vwap_deviation,
                    vwap_state=vwap_state,
                    vwap_location_score=vwap_location_score,
                    ema_multiplier=ema_multiplier,
                    ema_structure_status=ema_status,
                    details=self._build_debug_details(
                        **debug_details,
                    ),
                )
        else:
            debug_details = self._set_stage(
                debug_details,
                "flip_bearish_vwap_filter",
                flip_bearish_vwap_context_skipped=True,
            )

        if (
            trade_direction == 'short'
            and details_1h.get('signal_type') == 'green_bar_shrinking'
            and vwap_state == 'short_dual_pressure'
            and self.config.disable_green_bar_shrinking_short_dual_pressure_entries
        ):
            debug_details = self._set_stage(
                debug_details,
                "shrinking_state_filter",
                shrinking_state_filter_reason="green_bar_shrinking_short_dual_pressure",
            )
            return self._neutral_signal(
                reason='green_bar_shrinking_short_dual_pressure_disabled',
                signal_type_1h=details_1h.get('signal_type'),
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                vwap_state=vwap_state,
                vwap_location_score=vwap_location_score,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )

        if (
            trade_direction == 'long'
            and details_1h.get('signal_type') == 'red_bar_shrinking'
            and vwap_state == 'long_dual_support'
            and self.config.disable_red_bar_shrinking_long_dual_support_entries
        ):
            debug_details = self._set_stage(
                debug_details,
                "shrinking_state_filter",
                shrinking_state_filter_reason="red_bar_shrinking_long_dual_support",
            )
            return self._neutral_signal(
                reason='red_bar_shrinking_long_dual_support_disabled',
                signal_type_1h=details_1h.get('signal_type'),
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                vwap_state=vwap_state,
                vwap_location_score=vwap_location_score,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )

        # ========== Step 4: MACD_4H 确认增强 ==========
        is_4h_enhanced, enhancement_score = self.check_4h_macd_enhancement(
            macd_hist_4h, idx_4h, trade_direction
        )
        debug_details = self._set_stage(
            debug_details,
            "4h_enhancement",
            is_4h_enhanced=is_4h_enhanced,
            enhancement_score=enhancement_score,
        )
        
        # ========== Step 5: MACD_15M 跟随入场 ==========
        can_enter, entry_score_15m, details_15m = self.check_15m_macd_follow(
            macd_hist_15m, idx_15m, trade_direction,
            bb_middle_15m=bb_middle_15m,
            bb_upper_15m=bb_upper_15m,
            bb_lower_15m=bb_lower_15m,
            close_15m=close_15m
        )
        entry_type_15m = details_15m.get('entry_type', '')
        debug_details = self._set_stage(
            debug_details,
            "15m_entry",
            entry_type_15m=entry_type_15m,
            entry_score_15m=entry_score_15m,
            entry_refine_15m=details_15m.get("ema_15m_refine"),
            macd_15m_hist_current=details_15m.get("hist_current"),
            macd_15m_hist_prev=details_15m.get("hist_prev"),
            bb_middle_15m=bb_middle_15m,
            bb_upper_15m=bb_upper_15m,
            bb_lower_15m=bb_lower_15m,
        )
        
        if not can_enter:
            return self._neutral_signal(
                reason='15M未确认入场',
                signal_type_1h=details_1h.get('signal_type'),
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                enhancement_score=enhancement_score,
                is_4h_enhanced=is_4h_enhanced,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )

        signal_type_1h = details_1h.get('signal_type', '')
        entry_refine_15m = details_15m.get("ema_15m_refine")
        stable_continuation_eval = self._evaluate_stable_continuation(
            primary_mode=primary_mode,
            trade_direction=trade_direction,
            signal_type_1h=signal_type_1h,
            entry_type_15m=entry_type_15m,
            vwap_score=vwap_score,
            vwap_state=vwap_state,
            adx_1h=adx_1h,
            stable_trend_context=stable_trend_context,
            is_trial_entry=is_trial_entry,
        )
        stable_continuation_active = bool(stable_continuation_eval.get("stable_continuation_active", False))
        stable_continuation_side = (
            str(stable_continuation_eval.get("stable_continuation_side") or "").strip().lower() or None
        )
        debug_details.update(**stable_continuation_eval)

        if entry_score_15m < self.config.min_entry_score:
            return self._neutral_signal(
                reason=f'15M入场评分过低: {entry_score_15m:.2f}',
                entry_score_15m=entry_score_15m,
                signal_type_1h=details_1h.get('signal_type'),
                entry_type_15m=entry_type_15m,
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                enhancement_score=enhancement_score,
                is_4h_enhanced=is_4h_enhanced,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )

        if (
            strict_1h_filters_enabled
            and not stable_continuation_active
            and self.config.disable_flip_bullish_entries
            and signal_type_1h == 'flip_bullish'
        ):
            debug_details = self._set_stage(
                debug_details,
                "flip_bullish_disabled",
                flip_bullish_disabled=True,
            )
            return self._neutral_signal(
                reason='flip_bullish_disabled',
                signal_type_1h=signal_type_1h,
                entry_type_15m=entry_type_15m,
                entry_score_15m=entry_score_15m,
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                enhancement_score=enhancement_score,
                is_4h_enhanced=is_4h_enhanced,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )

        if (
            signal_type_1h == 'flip_bullish'
            and is_trial_entry
            and self.config.disable_flip_bullish_trial_entries
        ):
            debug_details = self._set_stage(
                debug_details,
                "flip_bullish_trial_disabled",
                flip_bullish_trial_disabled=True,
            )
            return self._neutral_signal(
                reason='flip_bullish_trial_disabled',
                signal_type_1h=signal_type_1h,
                entry_type_15m=entry_type_15m,
                entry_score_15m=entry_score_15m,
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                enhancement_score=enhancement_score,
                is_4h_enhanced=is_4h_enhanced,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )

        if (
            strict_1h_filters_enabled
            and not stable_continuation_active
            and self.config.enable_flip_bullish_strict_filter
            and signal_type_1h == 'flip_bullish'
        ):
            strict_filter_reasons: List[str] = []
            if self.config.flip_bullish_require_15m_growing and entry_type_15m != 'red_bar_growing':
                strict_filter_reasons.append(f"15m_entry={entry_type_15m or 'none'}")
            if self.config.flip_bullish_require_pullback_bounce and entry_refine_15m != 'pullback_bounce':
                strict_filter_reasons.append(f"15m_refine={entry_refine_15m or 'none'}")
            if vwap_score < self.config.flip_bullish_min_vwap_score:
                strict_filter_reasons.append(
                    f"vwap_score={vwap_score:.2f}<{self.config.flip_bullish_min_vwap_score:.2f}"
                )
            if strict_filter_reasons:
                debug_details = self._set_stage(
                    debug_details,
                    "flip_bullish_strict_filter",
                    flip_bullish_filter_reasons=strict_filter_reasons,
                    entry_refine_15m=entry_refine_15m,
                )
                return self._neutral_signal(
                    reason='flip_bullish_strict_filter',
                    signal_type_1h=signal_type_1h,
                    entry_type_15m=entry_type_15m,
                    entry_score_15m=entry_score_15m,
                    vwap_score=vwap_score,
                    vwap_deviation=vwap_deviation,
                    ema_multiplier=ema_multiplier,
                    ema_structure_status=ema_status,
                    enhancement_score=enhancement_score,
                    is_4h_enhanced=is_4h_enhanced,
                    details=self._build_debug_details(
                        **debug_details,
                    ),
                )

        if (
            trade_direction == 'long'
            and signal_type_1h == 'flip_bullish'
            and self.config.enable_flip_bullish_cvd_context_filter
            and not stable_continuation_active
        ):
            cvd_context_reasons: List[str] = []
            max_upper_wick_ratio = float(self.config.flip_bullish_max_cvd_upper_wick_ratio)
            min_cvd_1h_delta_ratio = float(self.config.flip_bullish_min_cvd_1h_delta_ratio)
            if (
                max_upper_wick_ratio > 0
                and cvd_upper_wick_ratio is not None
                and float(cvd_upper_wick_ratio) >= max_upper_wick_ratio
            ):
                cvd_context_reasons.append(
                    f"cvd_upper_wick_ratio={float(cvd_upper_wick_ratio):.4f}>={max_upper_wick_ratio:.4f}"
                )
            if (
                min_cvd_1h_delta_ratio > 0
                and cvd_1h_delta_ratio is not None
                and float(cvd_1h_delta_ratio) < min_cvd_1h_delta_ratio
            ):
                cvd_context_reasons.append(
                    f"cvd_1h_delta_ratio={float(cvd_1h_delta_ratio):.4f}<{min_cvd_1h_delta_ratio:.4f}"
                )
            if cvd_context_reasons:
                debug_details = self._set_stage(
                    debug_details,
                    "flip_bullish_cvd_context_filter",
                    flip_bullish_cvd_context_filter_reasons=cvd_context_reasons,
                )
                return self._neutral_signal(
                    reason=f'flip_bullish_cvd_context_filter({"; ".join(cvd_context_reasons)})',
                    signal_type_1h=signal_type_1h,
                    entry_type_15m=entry_type_15m,
                    entry_score_15m=entry_score_15m,
                    vwap_score=vwap_score,
                    vwap_deviation=vwap_deviation,
                    ema_multiplier=ema_multiplier,
                    ema_structure_status=ema_status,
                    enhancement_score=enhancement_score,
                    is_4h_enhanced=is_4h_enhanced,
                    details=self._build_debug_details(
                        **debug_details,
                    ),
                )

        if (
            strict_1h_filters_enabled
            and not stable_continuation_active
            and self.config.disable_green_bar_growing_entries
            and signal_type_1h == 'green_bar_growing'
        ):
            debug_details = self._set_stage(
                debug_details,
                "green_bar_growing_disabled",
                green_bar_growing_disabled=True,
            )
            return self._neutral_signal(
                reason='green_bar_growing_disabled',
                signal_type_1h=signal_type_1h,
                entry_type_15m=entry_type_15m,
                entry_score_15m=entry_score_15m,
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                enhancement_score=enhancement_score,
                is_4h_enhanced=is_4h_enhanced,
                    details=self._build_debug_details(
                        **debug_details,
                    ),
                )

        if (
            not is_trial_entry
            and trade_direction == 'short'
            and signal_type_1h == 'green_bar_growing'
            and self.config.enable_green_bar_growing_short_adx_1h_range_filter
            and not stable_continuation_active
        ):
            min_adx_1h = float(self.config.green_bar_growing_short_min_adx_1h)
            max_adx_1h = float(self.config.green_bar_growing_short_max_adx_1h)
            if max_adx_1h > min_adx_1h and min_adx_1h <= float(adx_1h) < max_adx_1h:
                debug_details = self._set_stage(
                    debug_details,
                    "green_bar_growing_short_adx_1h_range_filter",
                    green_bar_growing_short_min_adx_1h=min_adx_1h,
                    green_bar_growing_short_max_adx_1h=max_adx_1h,
                )
                return self._neutral_signal(
                    reason=(
                        f'green_bar_growing_short_adx_1h_range_filter('
                        f'{adx_1h:.2f} in [{min_adx_1h:.2f}, {max_adx_1h:.2f}))'
                    ),
                    signal_type_1h=signal_type_1h,
                    entry_type_15m=entry_type_15m,
                    entry_score_15m=entry_score_15m,
                    vwap_score=vwap_score,
                    vwap_deviation=vwap_deviation,
                    ema_multiplier=ema_multiplier,
                    ema_structure_status=ema_status,
                    enhancement_score=enhancement_score,
                    is_4h_enhanced=is_4h_enhanced,
                    details=self._build_debug_details(
                        **debug_details,
                    ),
                )

        if (
            strict_1h_filters_enabled
            and
            not stable_continuation_active
            and self.config.disable_red_bar_growing_long_entries
            and signal_type_1h == 'red_bar_growing'
            and trade_direction == 'long'
        ):
            debug_details = self._set_stage(
                debug_details,
                "red_bar_growing_long_disabled",
                red_bar_growing_long_disabled=True,
            )
            return self._neutral_signal(
                reason='red_bar_growing_long_disabled',
                signal_type_1h=signal_type_1h,
                entry_type_15m=entry_type_15m,
                entry_score_15m=entry_score_15m,
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                enhancement_score=enhancement_score,
                is_4h_enhanced=is_4h_enhanced,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )

        if (
            strict_1h_filters_enabled
            and signal_type_1h == 'flip_bearish'
            and ema_multiplier < self.config.flip_bearish_min_ema_multiplier
        ):
            debug_details = self._set_stage(
                debug_details,
                "flip_bearish_ema_filter",
                flip_bearish_min_ema_multiplier=self.config.flip_bearish_min_ema_multiplier,
            )
            return self._neutral_signal(
                reason=(
                    f'flip_bearish_ema_filter('
                    f'{ema_multiplier:.1f}<'
                    f'{self.config.flip_bearish_min_ema_multiplier:.1f})'
                ),
                signal_type_1h=signal_type_1h,
                entry_type_15m=entry_type_15m,
                entry_score_15m=entry_score_15m,
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                enhancement_score=enhancement_score,
                is_4h_enhanced=is_4h_enhanced,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )

        if strict_1h_filters_enabled:
            structure_ok, structure_reasons, structure_details = self.check_flip_bearish_structure(
                signal_type_1h,
                adx_1h=adx_1h,
                bb_middle_slope_1h=bb_middle_slope_1h,
                bb_middle_slope_4h=bb_middle_slope_4h,
            )
            if not structure_ok:
                debug_details = self._set_stage(
                    debug_details,
                    "flip_bearish_structure_filter",
                    flip_bearish_structure_passed=False,
                    flip_bearish_structure_reasons=structure_reasons,
                    **structure_details,
                )
                return self._neutral_signal(
                    reason=f'flip_bearish_structure_filter({"; ".join(structure_reasons)})',
                    signal_type_1h=signal_type_1h,
                    entry_type_15m=entry_type_15m,
                    entry_score_15m=entry_score_15m,
                    vwap_score=vwap_score,
                    vwap_deviation=vwap_deviation,
                    vwap_state=vwap_state,
                    vwap_location_score=vwap_location_score,
                    ema_multiplier=ema_multiplier,
                    ema_structure_status=ema_status,
                    enhancement_score=enhancement_score,
                    is_4h_enhanced=is_4h_enhanced,
                    details=self._build_debug_details(
                        **debug_details,
                    ),
                )
        else:
            debug_details = self._set_stage(
                debug_details,
                "flip_bearish_structure_filter",
                flip_bearish_structure_skipped=True,
            )

        min_vwap_score_for_entry = max(
            0.0,
            self.config.preflip_trial_min_vwap_score if is_trial_entry else self.config.min_vwap_score_for_entry,
        )
        if min_vwap_score_for_entry > 0 and vwap_score < min_vwap_score_for_entry:
            debug_details = self._set_stage(
                debug_details,
                "vwap_score_filter",
                min_vwap_score_for_entry=min_vwap_score_for_entry,
            )
            return self._neutral_signal(
                reason=f'vwap_hard_block({vwap_score:.2f}<{min_vwap_score_for_entry:.2f})',
                score=0.0,
                veto_type=VetoType.VWAP_SCORE_FILTER,
                veto_reason="VWAP评分低于入场阈值",
                signal_type_1h=signal_type_1h,
                entry_type_15m=entry_type_15m,
                entry_score_15m=entry_score_15m,
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                enhancement_score=enhancement_score,
                is_4h_enhanced=is_4h_enhanced,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )

        if strict_1h_filters_enabled and signal_type_1h == 'red_bar_growing' and trade_direction == 'long':
            if ema_status == 'against':
                debug_details = self._set_stage(
                    debug_details,
                    "red_bar_long_filter",
                    red_bar_long_filter_reason="ema_against",
                )
                return self._neutral_signal(
                    reason='red_bar_long_blocked(ema_against)',
                    signal_type_1h=signal_type_1h,
                    entry_type_15m=entry_type_15m,
                    entry_score_15m=entry_score_15m,
                    vwap_score=vwap_score,
                    vwap_deviation=vwap_deviation,
                    ema_multiplier=ema_multiplier,
                    ema_structure_status=ema_status,
                    enhancement_score=enhancement_score,
                    is_4h_enhanced=is_4h_enhanced,
                    details=self._build_debug_details(
                        **debug_details,
                    ),
                )
            if vwap_deviation < -self.config.vwap_deviation_hard_block:
                debug_details = self._set_stage(
                    debug_details,
                    "red_bar_long_filter",
                    red_bar_long_filter_reason="vwap_dev_too_low",
                )
                return self._neutral_signal(
                    reason=(
                        f'red_bar_long_blocked('
                        f'vwap_dev={vwap_deviation * 100:.1f}%<'
                        f'-{self.config.vwap_deviation_hard_block * 100:.1f}%)'
                    ),
                    signal_type_1h=signal_type_1h,
                    entry_type_15m=entry_type_15m,
                    entry_score_15m=entry_score_15m,
                    vwap_score=vwap_score,
                    vwap_deviation=vwap_deviation,
                    ema_multiplier=ema_multiplier,
                    ema_structure_status=ema_status,
                    enhancement_score=enhancement_score,
                    is_4h_enhanced=is_4h_enhanced,
                    details=self._build_debug_details(
                        **debug_details,
                    ),
                )
        
        # ========== Step 6: 综合评分 ==========
        score = 0.0
        
        # 4H主趋势评分 × BOLL结构修正
        signal_strength_1h = details_1h.get('signal_strength', 0.5)
        score_1h_base = 0.0
        score_1h = 0.0

        if is_trial_entry:
            shrink_pct = max(0.0, float(shrink_4h_context["shrink_pct"]))
            preflip_4h_strength = self._clamp(0.56 + shrink_pct * 0.38, 0.0, 0.92)
            score_4h_base = self.config.weight_4h_direction * preflip_4h_strength
        elif direction_4h == trade_direction and signal_type_4h in ['flip_bullish', 'flip_bearish']:
            score_4h_base = self.config.weight_4h_direction
        elif direction_4h == trade_direction and signal_type_4h in ['red_bar_growing', 'green_bar_growing']:
            score_4h_base = self.config.weight_4h_direction * 0.875
        elif direction_4h == trade_direction:
            score_4h_base = self.config.weight_4h_direction * signal_strength_4h
        else:
            score_4h_base = 0.0

        score_4h = min(score_4h_base * ema_multiplier, self.config.weight_4h_direction)
        score += score_4h
        
        # 4H附加增强评分（默认关闭） × BOLL结构修正
        legacy_4h_boost = entry_type_15m not in ['flip_bullish', 'flip_bearish']
        effective_4h_score = enhancement_score
        if legacy_4h_boost:
            if is_4h_enhanced:
                effective_4h_score = 0.84
            elif enhancement_score > 0:
                effective_4h_score = 2.0 / 3.0

        if is_4h_enhanced:
            score_4h_enhancement_base = self.config.weight_4h_enhancement * effective_4h_score
        elif enhancement_score > 0:
            score_4h_enhancement_base = self.config.weight_4h_enhancement * 0.5 * effective_4h_score
        else:
            score_4h_enhancement_base = 0
        
        score_4h_enhancement = min(score_4h_enhancement_base * ema_multiplier, self.config.weight_4h_enhancement)
        score += score_4h_enhancement
        
        # VWAP评分 (15%)
        score_vwap = vwap_score
        score += score_vwap
        
        # 15M入场评分 (20%)
        if entry_type_15m in ['flip_bullish', 'flip_bearish']:
            score_15m = self.config.weight_15m_entry
        elif entry_type_15m in ['red_bar_growing', 'green_bar_growing']:
            score_15m = self.config.weight_15m_entry * 0.85
        elif entry_type_15m in ['red_bar_stable', 'green_bar_stable']:
            score_15m = min(self.config.weight_15m_entry * 0.6 * 1.15, self.config.weight_15m_entry)
        else:
            score_15m = self.config.weight_15m_entry * 0.3
        score += score_15m
        
        # 成交量评分 (15%)
        if volume_ratio > 1.5:
            score_vol = self.config.weight_volume
        elif volume_ratio > 1.0:
            score_vol = self.config.weight_volume * 0.67
        else:
            score_vol = self.config.weight_volume * 0.33
        score += score_vol

        overheat_penalty = 0.0
        confirmation_signal_type = signal_type_4h if light_1h_confirmation else signal_type_1h
        growing_signal = confirmation_signal_type in ['red_bar_growing', 'green_bar_growing']
        growing_entry = entry_type_15m in ['red_bar_growing', 'green_bar_growing']
        if (
            self.config.overheat_growing_penalty > 0
            and ema_multiplier >= self.config.overheat_ema_multiplier_threshold
            and vwap_score <= self.config.overheat_vwap_score_threshold
            and (growing_signal or growing_entry)
        ):
            overheat_penalty = self.config.overheat_growing_penalty
            score = max(0.0, score - overheat_penalty)

        debug_details = self._set_stage(
            debug_details,
            "score_aggregation",
            primary_timeframe="4h",
            primary_signal_type=signal_type_4h,
            primary_signal_strength=signal_strength_4h,
            score_1h_base=score_1h_base,
            score_1h=score_1h,
            score_4h_base=score_4h_base,
            score_4h=score_4h,
            score_4h_enhancement_base=score_4h_enhancement_base,
            score_4h_enhancement=score_4h_enhancement,
            score_vwap=score_vwap,
            score_15m=score_15m,
            score_volume=score_vol,
            overheat_penalty=overheat_penalty,
            overheat_triggered=bool(overheat_penalty > 0),
            total_score=score,
            legacy_4h_boost=legacy_4h_boost,
            effective_4h_score=effective_4h_score,
        )
        trial_short_promotion_eval = self._evaluate_trial_short_below_structure_continuation_promotion(
            primary_mode=primary_mode,
            trade_direction=trade_direction,
            signal_type_1h=signal_type_1h,
            entry_type_15m=entry_type_15m,
            vwap_state=vwap_state,
            vwap_score=vwap_score,
            adx_1h=adx_1h,
            signal_score=score,
            shrink_4h_context=shrink_4h_context,
            is_trial_entry=is_trial_entry,
        )
        debug_details.update(**trial_short_promotion_eval)
        if bool(trial_short_promotion_eval.get("trial_short_below_structure_promotion_active", False)):
            stable_continuation_active = True
            stable_continuation_side = "short"
            stable_continuation_eval.update(
                stable_continuation_active=True,
                stable_continuation_side="short",
                stable_continuation_reason="trial_short_below_structure_promoted",
                stable_continuation_hist_bars=int(stable_trend_context.get("negative_bars", 0) or 0),
                stable_continuation_promoted_from_trial=True,
                stable_continuation_promoted_vwap_state=vwap_state,
            )
            debug_details.update(**stable_continuation_eval)
        if self.config.weight_4h_enhancement > 0 and score_4h_enhancement == 0.0:
            logger.debug(
                "[MACD_V2_SCORE] 4H enhancement=0.0 but weight=%.2f, check 4H data source",
                self.config.weight_4h_enhancement,
            )

        if (
            strict_1h_filters_enabled
            and
            self.config.is_flip_bearish_normal_ema(signal_type_1h, ema_multiplier)
            and self.config.flip_bearish_normal_ema_min_signal_score > 0
            and score < self.config.flip_bearish_normal_ema_min_signal_score
        ):
            debug_details = self._set_stage(
                debug_details,
                "flip_bearish_normal_ema_score_filter",
                flip_bearish_normal_ema_min_signal_score=self.config.flip_bearish_normal_ema_min_signal_score,
            )
            return self._neutral_signal(
                reason=(
                    f'flip_bearish_normal_ema_score_filter('
                    f'{score:.2f}<'
                    f'{self.config.flip_bearish_normal_ema_min_signal_score:.2f})'
                ),
                score=score,
                signal_type_1h=signal_type_1h,
                entry_type_15m=entry_type_15m,
                entry_score_15m=entry_score_15m,
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                enhancement_score=enhancement_score,
                is_4h_enhanced=is_4h_enhanced,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )
        
        # ========== Step 7: 组合否决检查 ==========
        # V6: 成交量 + VWAP 双低
        if score_vol < 0.05 and vwap_score <= 0.10:
            return self._neutral_signal(
                reason='volume_vwap_both_low',
                score=score,
                veto_type=VetoType.VOLUME_VWAP_BOTH_LOW,
                veto_reason="成交量与VWAP评分双低",
                signal_type_1h=signal_type_1h,
                entry_type_15m=entry_type_15m,
                entry_score_15m=entry_score_15m,
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                enhancement_score=enhancement_score,
                is_4h_enhanced=is_4h_enhanced,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )
        
        # ========== Step 7.5: 空头质量过滤（V3专家组建议）==========
        if (
            strict_1h_filters_enabled
            and signal_type_1h == 'flip_bearish'
            and self.config.enable_short_quality_filter
        ):
            short_filter_passed = True
            short_filter_reasons = []
            
            # 条件1: funding_rate > 0.0005 (市场偏多，反转空头有挤多背景)
            if funding_rate <= self.config.short_filter_min_funding_rate:
                short_filter_passed = False
                short_filter_reasons.append(f'funding_rate({funding_rate:.6f}) <= {self.config.short_filter_min_funding_rate}')
            
            # 条件2: oi_delta_ratio < 0 (多头减仓，而非低位追空)
            if oi_delta_ratio >= self.config.short_filter_max_oi_delta_ratio:
                short_filter_passed = False
                short_filter_reasons.append(f'oi_delta_ratio({oi_delta_ratio:.4f}) >= {self.config.short_filter_max_oi_delta_ratio}')
            
            # 条件3: price > vwap * 1.005 (价格偏高位置，不在过低位置追击)
            if vwap > 0 and close_price <= vwap * (1 + self.config.short_filter_min_vwap_deviation):
                short_filter_passed = False
                short_filter_reasons.append(f'price({close_price:.2f}) <= vwap*{1+self.config.short_filter_min_vwap_deviation:.3f}')
            
            if not short_filter_passed:
                return self._neutral_signal(
                    reason=f'空头质量过滤未通过: {"; ".join(short_filter_reasons)}',
                    score=score,
                    veto_type=VetoType.SHORT_QUALITY_FILTER,
                    veto_reason='空头质量过滤',
                    signal_type_1h=signal_type_1h,
                    entry_type_15m=entry_type_15m,
                    entry_score_15m=entry_score_15m,
                    vwap_score=vwap_score,
                    vwap_deviation=vwap_deviation,
                    ema_multiplier=ema_multiplier,
                    ema_structure_status=ema_status,
                    enhancement_score=enhancement_score,
                    is_4h_enhanced=is_4h_enhanced,
                    details=self._build_debug_details(
                        **debug_details,
                        short_filter_passed=False,
                        short_filter_reasons=short_filter_reasons,
                        funding_rate=funding_rate,
                        oi_delta_ratio=oi_delta_ratio,
                    ),
                )
        
        # ========== Step 8: 入场阈值检查 ==========
        threshold = self.config.resolve_signal_score_threshold(
            signal_type_1h,
            stable_continuation_side=stable_continuation_side if stable_continuation_active else None,
        )
        if primary_mode == "4h" and not stable_continuation_active:
            threshold = self.config.resolve_signal_score_threshold(signal_type_4h or signal_type_1h)
        if is_trial_entry:
            threshold = float(self.config.preflip_trial_min_signal_score)
        debug_details = self._set_stage(
            debug_details,
            "threshold_check",
            signal_score_threshold=threshold,
        )
        if score < threshold:
            return self._neutral_signal(
                reason=f'信号评分低于阈值: {score:.2f} < {threshold:.2f}',
                score=score,
                signal_type_1h=signal_type_1h,
                entry_type_15m=entry_type_15m,
                entry_score_15m=entry_score_15m,
                vwap_score=vwap_score,
                vwap_deviation=vwap_deviation,
                ema_multiplier=ema_multiplier,
                ema_structure_status=ema_status,
                enhancement_score=enhancement_score,
                is_4h_enhanced=is_4h_enhanced,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )
        
        # ========== Step 9: 计算止损 ==========
        stop_price, stop_pct, stop_details = self.calculate_dynamic_stop(
            entry_price=close_price,
            close_1h=close_price,
            bb_middle_1h=bb_middle_1h,
            bb_upper_1h=bb_upper_1h,
            bb_lower_1h=bb_lower_1h,
            atr_1h=atr_1h,
            vwap=vwap,
            direction=trade_direction
        )
        
        # ========== Step 10: 返回结果 ==========
        final_score = min(score, 1.0)
        
        final_stage_path = self._normalize_stage_path(debug_details.get("stage_path"))
        final_stage_path = self._set_stage({"stage_path": final_stage_path}, "final")["stage_path"]
        self._last_analysis = {
            'strategy': 'macd_mtf_strategy_v2',
            'stage': 'final',
            'stage_path': final_stage_path,
            'stage_path_text': " > ".join(final_stage_path),
            'primary_timeframe': primary_mode,
            'trade_direction': trade_direction,
            'direction_1h': direction_1h,
            'signal_type_1h': signal_type_1h,
            'signal_strength_1h': signal_strength_1h,
            'direction_4h': direction_4h,
            'signal_type_4h': signal_type_4h,
            'signal_strength_4h': signal_strength_4h,
            'is_4h_enhanced': is_4h_enhanced,
            'is_trial_entry': is_trial_entry,
            'entry_scale': entry_scale,
            'stable_continuation_active': stable_continuation_active,
            'stable_continuation_side': stable_continuation_side,
            'stable_continuation_reason': stable_continuation_eval.get("stable_continuation_reason"),
            'stable_continuation_hist_bars': stable_continuation_eval.get("stable_continuation_hist_bars"),
            'enhancement_score': enhancement_score,
            'entry_type_15m': entry_type_15m,
            'entry_score_15m': entry_score_15m,
            'macd_4h_shrink_pct': shrink_4h_context["shrink_pct"],
            'macd_4h_shrink_bars': shrink_4h_context["shrink_bars"],
            'shrink_exit_direction': shrink_4h_context["exit_direction"],
            'shrink_exit_ready': shrink_4h_context["shrink_exit_ready"],
            'vwap_score': vwap_score,
            'vwap_deviation': (close_price - vwap) / vwap if vwap > 0 else 0,
            'vwap_state': vwap_state,
            'vwap_location_score': vwap_location_score,
            'session_vwap': vwap_details.get("session_vwap", vwap),
            'structural_vwap': vwap_details.get("structural_vwap", structural_vwap),
            'session_vwap_deviation': vwap_details.get("session_deviation", vwap_deviation),
            'structural_vwap_deviation': structural_vwap_deviation,
            'ema_multiplier': ema_multiplier,
            'ema_status': ema_status,
            'adx_1h': adx_1h,
            'adx_4h': adx_4h,
            'bb_middle_slope_1h': bb_middle_slope_1h,
            'bb_middle_slope_4h': bb_middle_slope_4h,
            'volume_ratio': volume_ratio,
            'score_1h_base': score_1h_base,
            'score_1h': score_1h,
            'score_4h_base': score_4h_base,
            'score_4h': score_4h,
            'score_4h_enhancement_base': score_4h_enhancement_base,
            'score_4h_enhancement': score_4h_enhancement,
            'score_vwap': score_vwap,
            'score_15m': score_15m,
            'score_volume': score_vol,
            'overheat_penalty': overheat_penalty,
            'legacy_4h_boost': legacy_4h_boost,
            'effective_4h_score': effective_4h_score,
            'entry_refine_15m': entry_refine_15m,
            'veto_type': VetoType.NONE.value,
            'reject_stage': '',
            'reject_reason_code': '',
            'reject_reason_detail': '',
            'total_score': final_score,
            'stop_price': stop_price,
            'stop_loss_pct': stop_pct,
            'stop_details': stop_details,
        }
        
        return MACDSignalV2(
            direction=trade_direction,
            signal_score=final_score,
            signal_type_1h=signal_type_1h,
            signal_strength_1h=signal_strength_1h,
            is_4h_enhanced=is_4h_enhanced,
            enhancement_score=enhancement_score,
            entry_type_15m=entry_type_15m,
            entry_score_15m=entry_score_15m,
            vwap_score=vwap_score,
            vwap_deviation=(close_price - vwap) / vwap if vwap > 0 else 0,
            vwap_state=vwap_state,
            vwap_location_score=vwap_location_score,
            ema_multiplier=ema_multiplier,
            ema_structure_status=ema_status,
            veto_type=VetoType.NONE,
            veto_reason="",
            suggested_stop_price=stop_price,
            stop_loss_pct=stop_pct,
            is_trial_entry=is_trial_entry,
            entry_scale=entry_scale,
            details=self._last_analysis
        )
    
    def get_last_analysis(self) -> Dict:
        """获取最近一次分析结果"""
        return self._last_analysis.copy()
    
    def calculate_leverage(
        self,
        score: float,
        ema_multiplier: float = 1.0,
        signal_type_1h: Optional[str] = None,
        symbol: Optional[str] = None,
        is_trial_entry: bool = False,
    ) -> int:
        """
        根据评分计算杠杆（实盘配置：2X/3X/4X）
        
        Args:
            score: 信号评分
            ema_multiplier: BOLL结构修正系数，强趋势(1.2)时降杠杆
        
        Returns:
            杠杆倍数
        """
        if score >= 0.90:
            base_leverage = 4
        elif score >= 0.85:
            base_leverage = 3
        elif score >= 0.75:
            base_leverage = 2
        else:
            return 0
        
        # BOLL强趋势降杠杆（强趋势可能已运行较长时间）
        if ema_multiplier >= 1.2:
            leverage = max(2, int(base_leverage * self.config.ema_strong_trend_leverage_mult))
        else:
            leverage = base_leverage

        if (
            self.config.is_flip_bearish_normal_ema(signal_type_1h, ema_multiplier)
            and self.config.flip_bearish_normal_ema_max_leverage > 0
        ):
            leverage = min(leverage, int(self.config.flip_bearish_normal_ema_max_leverage))

        if is_trial_entry and self.config.preflip_trial_max_leverage > 0:
            leverage = min(leverage, int(self.config.preflip_trial_max_leverage))

        if self.is_watchlist_symbol(symbol) and self.config.symbol_risk_watchlist_max_leverage > 0:
            leverage = min(leverage, int(self.config.symbol_risk_watchlist_max_leverage))
        
        return leverage
    
    def calculate_portion_multiplier(self, score: float) -> float:
        """根据评分计算仓位乘数"""
        if score >= 0.90:
            return 1.2
        elif score >= 0.85:
            return 1.0
        elif score >= 0.75:
            return 0.8
        return 0.0

    def calculate_position_portion(
        self,
        score: float,
        base_default_portion: float,
        base_max_symbol_position_portion: float,
        symbol: Optional[str] = None,
        signal_type_1h: Optional[str] = None,
        vwap_score: float = 0.0,
        vwap_state: Optional[str] = None,
        bonus_multiplier: float = 1.0,
        is_trial_entry: bool = False,
        entry_scale: float = 1.0,
        session_scale: float = 1.0,
    ) -> float:
        portion_mult = self.calculate_portion_multiplier(score)
        if portion_mult <= 0:
            return 0.0

        target_portion = float(base_default_portion)
        max_symbol_position_portion = float(base_max_symbol_position_portion)

        if str(vwap_state or "").strip().lower() == "short_dual_pressure":
            target_portion += float(self.config.dual_pressure_target_portion_bonus) * max(0.0, float(bonus_multiplier))
            if self.config.dual_pressure_max_symbol_position_portion > 0:
                max_symbol_position_portion = max(
                    max_symbol_position_portion,
                    float(self.config.dual_pressure_max_symbol_position_portion),
                )

        if self.is_watchlist_symbol(symbol) and self.config.symbol_risk_watchlist_max_position_portion > 0:
            max_symbol_position_portion = min(
                max_symbol_position_portion,
                float(self.config.symbol_risk_watchlist_max_position_portion),
            )

        portion = min(max_symbol_position_portion, target_portion * portion_mult)
        portion *= self.resolve_vwap_score_position_multiplier(
            vwap_score=vwap_score,
            signal_type_1h=signal_type_1h,
            vwap_state=vwap_state,
        )
        portion = min(max_symbol_position_portion, portion)
        if is_trial_entry:
            portion *= self._clamp(entry_scale, 0.05, 1.0)
        portion *= self.resolve_symbol_risk_session_scale(symbol, session_scale)
        return portion
