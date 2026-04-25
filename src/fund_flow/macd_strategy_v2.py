"""
MACD多时间框架交易策略模块 V2.0 - VWAP + BOLL 增强版

策略架构：
- BOLL结构层（4H + 1H）→ 过滤逆势交易，确认价格在布林带中的位置
- MACD_1H 定方向 → 负责执行方向与过滤
- MACD_4H 主评分（权重55%）→ 负责主趋势打分，降低噪音
- MACD_4H 确认增强（默认关闭）→ 预留附加趋势验证
- VWAP 价值中枢层（权重20%）→ 判断多空偏向，偏离过滤
- MACD_15M 软确认（权重5%）→ 仅用于入场微调，不再作为 4H 主周期下的硬门槛
- 成交量确认（权重20%）→ 入场质量验证

扫描周期：每15分钟
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import logging
import math
import numpy as np


logger = logging.getLogger(__name__)


def check_pocket_entry_override(
    signal_type: str,
    vwap_state: str,
    is_trial_entry: bool,
    signal_score: float,
    vwap_score: float,
    entry_score: float,
    bar_1h_direction: str,
    flow_cvd_ok: bool,
    micro_cvd_momentum_ok: bool,
    pocket_entry_overrides: dict,
    rsi_val: float = 50.0,
    trade_direction: str = "",
) -> tuple[bool, str]:
    """
    在 entry_hard_gates 通过后执行 pocket 级独立准入检查。
    """
    overrides = pocket_entry_overrides if isinstance(pocket_entry_overrides, dict) else {}
    pocket_key = f"{str(signal_type or '').strip()}|{str(vwap_state or '').strip()}"
    pocket_cfg = overrides.get(pocket_key)

    if not isinstance(pocket_cfg, dict) or not pocket_cfg:
        return True, "POCKET_GATE:NO_OVERRIDE"

    if pocket_cfg.get("disabled", False):
        return False, f"POCKET_GATE[{pocket_key}]:DISABLED"

    if pocket_cfg.get("disallow_trial_entry", False) and is_trial_entry:
        return False, (
            f"POCKET_GATE[{pocket_key}]:TRIAL_DISALLOWED "
            f"is_trial_entry={is_trial_entry}"
        )

    bar_1h_direction = str(bar_1h_direction or "").strip().upper()
    if pocket_cfg.get("require_strict_1h_confirmation", False):
        strict_direction = str(pocket_cfg.get("strict_1h_direction", "bullish") or "bullish").strip().lower()
        if strict_direction == "bearish":
            allowed = {"BEARISH", "WEAKLY_BEARISH"}
            reason_key = "1H_NOT_BEARISH"
        else:
            allowed = {"BULLISH", "WEAKLY_BULLISH"}
            reason_key = "1H_NOT_BULLISH"
        if bar_1h_direction not in allowed:
            return False, (
                f"POCKET_GATE[{pocket_key}]:{reason_key} "
                f"bar_1h_direction={bar_1h_direction} "
                f"allowed={sorted(allowed)}"
            )
    elif pocket_cfg.get("allow_neutral_1h_confirmation") is False:
        if bar_1h_direction == "NEUTRAL":
            return False, (
                f"POCKET_GATE[{pocket_key}]:1H_NEUTRAL_BLOCKED "
                f"bar_1h_direction={bar_1h_direction}"
            )

    min_signal_score = pocket_cfg.get("min_signal_score")
    if min_signal_score is not None and float(signal_score) < float(min_signal_score):
        return False, (
            f"POCKET_GATE[{pocket_key}]:SIGNAL_SCORE_LOW "
            f"{float(signal_score):.4f} < {min_signal_score}"
        )

    min_vwap_score = pocket_cfg.get("min_vwap_score")
    if min_vwap_score is not None and float(vwap_score) < float(min_vwap_score):
        return False, (
            f"POCKET_GATE[{pocket_key}]:VWAP_SCORE_LOW "
            f"{float(vwap_score):.4f} < {min_vwap_score}"
        )

    min_entry_score = pocket_cfg.get("min_entry_score")
    if min_entry_score is not None and float(entry_score) < float(min_entry_score):
        return False, (
            f"POCKET_GATE[{pocket_key}]:ENTRY_SCORE_LOW "
            f"{float(entry_score):.4f} < {min_entry_score}"
        )

    if pocket_cfg.get("require_cvd_ok", False) and not flow_cvd_ok:
        return False, (
            f"POCKET_GATE[{pocket_key}]:CVD_NOT_OK "
            f"flow_cvd_ok={flow_cvd_ok}"
        )

    if pocket_cfg.get("require_cvd_momentum_ok", False) and not micro_cvd_momentum_ok:
        return False, (
            f"POCKET_GATE[{pocket_key}]:CVD_MOMENTUM_NOT_OK "
            f"micro_cvd_momentum_ok={micro_cvd_momentum_ok}"
        )

    # RSI确认检查
    if pocket_cfg.get("require_rsi_ok", False):
        trade_dir = str(trade_direction or "").strip().lower()
        if trade_dir == "long" and rsi_val < 50:
            return False, (
                f"POCKET_GATE[{pocket_key}]:RSI_LOW "
                f"rsi={rsi_val:.1f}<50 (long)"
            )
        elif trade_dir == "short" and rsi_val > 50:
            return False, (
                f"POCKET_GATE[{pocket_key}]:RSI_HIGH "
                f"rsi={rsi_val:.1f}>50 (short)"
            )

    return True, f"POCKET_GATE[{pocket_key}]:PASS"


class VetoType(Enum):
    """否决类型"""
    NONE = "none"
    VWAP_HARD_BLOCK = "vwap_hard_block"  # VWAP偏离超过3%
    VWAP_SCORE_FILTER = "vwap_score_filter"  # VWAP评分低于入场阈值
    VWAP_OPPOSITE_DAYS = "vwap_opposite_days"  # VWAP方向连续相反
    RSI_GATE_FAIL = "rsi_gate_fail"  # RSI 门槛不通过
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
    
    # RSI配置
    rsi_period: int = 14
    rsi_oversold_threshold: float = 30.0
    rsi_overbought_threshold: float = 70.0
    rsi_require_ok_for_growing: bool = True   # 对growing家族要求RSI确认
    rsi_require_ok_for_flip: bool = False     # 对flip家族可选确认
    rsi_adx_threshold_strong: float = 25.0    # ADX >= 此值使用BOLL体系
    rsi_adx_threshold_weak: float = 15.0      # ADX < 此值禁止交易

    # VWAP参数
    vwap_deviation_optimal: float = 0.005  # 最优偏离区间 ±0.5%
    vwap_deviation_warning: float = 0.015  # 警告偏离 ±1.5%
    vwap_deviation_hard_block: float = 999.0  # 硬性否决偏离 ±3.0%
    structural_vwap_mode: str = "anchored_daily"
    structural_vwap_rolling_window: int = 20
    vwap_retest_tolerance: float = 0.003
    
    # 评分权重
    weight_1h_direction: float = 0.40  # 1H方向辅助评分权重
    weight_4h_direction: float = 0.20  # 4H主趋势评分权重
    weight_4h_enhancement: float = 0.00  # 4H附加增强权重（默认关闭，避免重复计分）
    weight_boll_position: float = 0.0
    weight_boll_rsi_resonance: float = 0.0  # BOLL+RSI 共振评分权重
    weight_vwap: float = 0.0  # 兼容旧配置键；保留但不参与实际门槛
    weight_15m_entry: float = 0.05  # 15M入场时机评分权重（软确认）
    weight_volume: float = 0.15  # 成交量确认评分权重
    
    # 入场阈值
    min_entry_score: float = 0.25
    min_signal_score: float = 0.80
    red_bar_growing_min_signal_score: float = 0.92
    red_bar_shrinking_min_signal_score: float = 0.84
    flip_bearish_min_signal_score: float = 0.80
    flip_bullish_min_signal_score: float = 0.80

    # 1H flip_bullish 严格过滤
    enable_flip_bullish_strict_filter: bool = True
    disable_flip_bullish_entries: bool = False
    force_disable_flip_bullish_entries: bool = False
    disable_flip_bearish_entries: bool = False
    disable_flip_bullish_trial_entries: bool = False
    flip_bullish_min_vwap_score: float = 0.0
    flip_bullish_require_pullback_bounce: bool = True
    flip_bullish_require_15m_growing: bool = True
    enable_flip_bullish_cvd_context_filter: bool = False
    flip_bullish_max_cvd_upper_wick_ratio: float = 0.0
    flip_bullish_min_cvd_1h_delta_ratio: float = 0.0
    flip_bullish_trial_score_window_enabled: bool = False
    flip_bullish_trial_score_min: float = 0.80
    flip_bullish_trial_score_max: float = 0.87
    green_bar_growing_score_window_enabled: bool = False
    green_bar_growing_score_min: float = 0.0
    green_bar_growing_score_max: float = 1.0
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
    disable_red_bar_shrinking_entries: bool = False
    # flip_bearish 专属共振门控（解锁后替代硬性禁用的保护措施）
    flip_bearish_resonance_guards: Dict[str, Any] = field(default_factory=lambda: {
        "min_boll_zone_score": 0.60,
        "require_4h_histogram_negative": True,
        "rsi_1h_range_max": 60,
    })
    # red_bar_growing 做多专属门控
    red_bar_growing_long_guards: Dict[str, Any] = field(default_factory=lambda: {
        "min_boll_zone_score_long": 0.65,
        "require_4h_histogram_positive": True,
        "rsi_1h_range_min": 42,
        "rsi_1h_range_max": 65,
    })
    long_entry_mode: str = "all"
    long_whitelist_signal_types: List[str] = field(default_factory=list)
    long_whitelist_vwap_states: List[str] = field(default_factory=list)
    long_whitelist_pockets: List[str] = field(default_factory=list)
    disable_green_bar_growing_entries: bool = True
    disable_green_bar_growing_short_entries: bool = False
    disable_green_bar_shrinking_entries: bool = False
    disable_green_bar_shrinking_short_dual_pressure_entries: bool = True
    disable_red_bar_shrinking_long_dual_support_entries: bool = True
    require_macd_home_advantage: bool = False
    vwap_execution_penalty_only: bool = False
    primary_direction_timeframe: str = "4h"  # 默认使用4H主趋势，兼容旧配置时可显式切回1h
    require_1h_confirmation_when_4h_primary: bool = False
    allow_neutral_1h_confirmation: bool = False
    light_1h_confirmation_when_4h_primary: bool = False
    enable_soft_15m_confirmation_when_4h_primary: bool = True
    require_15m_confirmation_gate: bool = False
    soft_15m_entry_score: float = 0.28
    soft_15m_neutral_hist_multiple: float = 3.0
    soft_15m_max_adverse_hist_multiple: float = 8.0
    enable_green_bar_growing_short_adx_1h_range_filter: bool = False
    green_bar_growing_short_min_adx_1h: float = 0.0
    green_bar_growing_short_max_adx_1h: float = 0.0
    enable_4h_preflip_trial_entries: bool = False
    preflip_trial_min_shrink_pct_long: float = 0.45
    preflip_trial_min_shrink_pct_short: float = 0.22
    preflip_trial_min_signal_score: float = 0.70
    preflip_trial_min_vwap_score: float = 0.0
    preflip_trial_entry_scale: float = 0.35
    preflip_trial_max_leverage: int = 2
    enable_q4_rsi_lead_preflip_long: bool = False
    q4_rsi_lead_preflip_min_4h_shrink_pct: float = 0.75
    q4_rsi_lead_preflip_rsi_1h_min: float = 50.0
    q4_rsi_lead_preflip_rsi_4h_min: float = 50.0
    q4_rsi_lead_preflip_rsi_4h_near_buffer: float = 2.0
    q4_rsi_lead_preflip_bonus_score: float = 0.18
    q4_rsi_lead_preflip_entry_scale: float = 0.35
    q4_rsi_lead_preflip_strict_eth_only: bool = True
    q4_rsi_lead_preflip_allowed_symbols: List[str] = field(default_factory=lambda: ["ETHUSDT"])
    q4_rsi_lead_preflip_allowed_categories: List[str] = field(default_factory=list)
    q4_rsi_lead_preflip_min_atr_pct_1h: float = 0.0
    q4_rsi_lead_preflip_max_atr_pct_1h: float = 1.0
    q4_rsi_lead_preflip_min_signal_score_override: float = 0.0
    rsi_gate_q4_exempt: bool = False
    enable_q4_rsi_lead_preflip_hold: bool = False
    q4_rsi_lead_preflip_hold_exit_metric: str = "rsi_21"
    q4_rsi_lead_preflip_hold_rsi_4h_exit_threshold: float = 70.0
    q4_rsi_lead_preflip_hold_rsi_4h_pullback: float = 1.0
    enable_trial_short_below_structure_continuation_promotion: bool = False
    trial_short_below_structure_promotion_min_signal_score: float = 0.82
    trial_short_below_structure_promotion_min_vwap_score: float = 0.0
    trial_short_below_structure_promotion_min_adx_1h: float = 25.0
    trial_short_below_structure_promotion_min_4h_shrink_pct: float = 0.80
    trial_short_below_structure_promotion_min_4h_shrink_bars: int = 6
    enable_stable_bear_continuation: bool = True
    stable_bear_continuation_min_signal_score: float = 0.80
    stable_bear_continuation_min_vwap_score: float = 0.0
    stable_bear_continuation_min_adx_1h: float = 20.0
    stable_bear_continuation_min_4h_bars: int = 2
    enable_stable_bull_continuation: bool = False
    stable_bull_continuation_min_signal_score: float = 0.80
    stable_bull_continuation_min_vwap_score: float = 0.0
    stable_bull_continuation_min_adx_1h: float = 20.0
    stable_bull_continuation_min_4h_bars: int = 2
    pocket_entry_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pocket_scoring_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pocket_management_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    enable_stable_continuation_slow_4h_shrink_exit: bool = True
    stable_continuation_exit_4h_shrink_bars: int = 3
    stable_continuation_exit_4h_min_shrink_pct: float = 0.35
    enable_4h_shrink_exit: bool = False
    exit_4h_shrink_bars: int = 2
    exit_4h_min_shrink_pct: float = 0.20
    exit_4h_require_profit: bool = True
    exit_4h_weak_loss_threshold: float = -1.0
    shrink_exit_loss_mitigation_enabled: bool = False
    shrink_exit_loss_mitigation_pnl_threshold: float = -0.005
    shrink_exit_loss_mitigation_exit_ratio: float = 0.60
    shrink_exit_loss_mitigation_ignore_if_pnl_gt: float = 0.01
    session_risk_control_enabled: bool = False
    session_risk_high_risk_sessions: List[Dict[str, Any]] = field(default_factory=list)
    session_risk_apply_to_states: List[str] = field(default_factory=list)
    vwap_score_tier_apply_to_states: List[str] = field(default_factory=list)
    vwap_score_position_tiers: List[Dict[str, Any]] = field(default_factory=list)
    position_score_tiers: List[Dict[str, Any]] = field(default_factory=list)
    symbol_risk_watchlist_symbols: List[str] = field(default_factory=list)
    symbol_risk_watchlist_max_position_portion: float = 0.0
    symbol_risk_watchlist_max_leverage: int = 0
    symbol_risk_watchlist_apply_session_scale_double: bool = False
    symbol_risk_watchlist_session_scale_multiplier: float = 0.80
    leverage_score_tiers: List[Dict[str, Any]] = field(default_factory=list)

    # 过热惩罚
    overheat_growing_penalty: float = 0.12
    overheat_ema_multiplier_threshold: float = 1.2
    overheat_vwap_score_threshold: float = 0.10
    min_vwap_score_for_entry: float = 0.0  # 已禁用
    disable_vwap_thresholds: bool = True
    
    # 止损配置
    use_dynamic_stop: bool = True
    ema_stop_atr_multiplier: float = 0.5
    max_stop_loss_pct: float = 0.025
    max_stop_distance_pct: float = 0.025
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

    # ── 双套件配置 ──
    enable_dual_suite: bool = False  # 启用双套件自适应模式
    indicator_suite: str = "adaptive"  # adaptive / RSI_MRV / BOLL_MBV
    # 市场状态分类器参数
    suite_adx_trending_threshold: float = 25.0
    suite_adx_ranging_threshold: float = 22.0
    suite_boll_bandwidth_trending_ratio: float = 1.3
    suite_switch_cooldown_bars_1h: int = 4
    suite_atr_volatile_threshold: float = 0.025
    # RSI_MRV 套件评分门槛
    rsi_mrv_min_score: float = 0.55
    # BOLL_MBV 套件评分门槛
    boll_mbv_min_score: float = 0.45
    # 信号族白名单（启用双套件后生效）
    signal_family_whitelist: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # RSI 门控配置（三时间框架联合门控）
    rsi_gate_enabled: bool = False
    rsi_gate_long_rsi_4h_min: float = 45.0
    rsi_gate_long_rsi_1h_min: float = 45.0
    rsi_gate_long_rsi_1h_max: float = 70.0
    rsi_gate_long_rsi_15m_min: float = 48.0
    rsi_gate_short_rsi_4h_max: float = 55.0
    rsi_gate_short_rsi_1h_max: float = 55.0
    rsi_gate_short_rsi_1h_min: float = 30.0
    rsi_gate_short_rsi_15m_max: float = 52.0
    # RSI flip 豁免
    rsi_flip_bullish_rsi_4h_min: float = 42.0
    rsi_flip_bullish_rsi_1h_min: float = 42.0
    rsi_flip_bearish_rsi_4h_max: float = 58.0
    rsi_flip_bearish_rsi_1h_max: float = 58.0
    # RSI 极值否决
    rsi_extreme_overbought: float = 78.0
    rsi_extreme_oversold: float = 22.0
    # RSI 多时间框架数据（传入参数）
    rsi_4h: float = 50.0
    rsi_15m: float = 50.0
    rsi_slope_1h: float = 0.0

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
            "red_bar_shrinking": self.red_bar_shrinking_min_signal_score,
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

    @staticmethod
    def normalize_pocket_key(signal_type_1h: Optional[str], vwap_state: Optional[str]) -> str:
        signal_key = str(signal_type_1h or "*").strip().lower() or "*"
        state_key = str(vwap_state or "*").strip().lower() or "*"
        return f"{signal_key}|{state_key}"

    def is_long_entry_whitelisted(
        self,
        signal_type_1h: Optional[str],
        vwap_state: Optional[str],
    ) -> bool:
        pocket_whitelist = {
            self.normalize_pocket_key(*str(item or "").split("|", 1))
            if "|" in str(item or "")
            else self.normalize_pocket_key(str(item or "").strip(), "*")
            for item in (self.long_whitelist_pockets or [])
            if str(item or "").strip()
        }
        if pocket_whitelist:
            return self.normalize_pocket_key(signal_type_1h, vwap_state) in pocket_whitelist

        signal_type = str(signal_type_1h or "").strip().lower()
        state = str(vwap_state or "").strip().lower()
        signal_whitelist = {
            str(item or "").strip().lower()
            for item in (self.long_whitelist_signal_types or [])
            if str(item or "").strip()
        }
        state_whitelist = {
            str(item or "").strip().lower()
            for item in (self.long_whitelist_vwap_states or [])
            if str(item or "").strip()
        }
        return signal_type in signal_whitelist or state in state_whitelist

    def resolve_pocket_entry_override(
        self,
        signal_type_1h: Optional[str],
        vwap_state: Optional[str],
    ) -> Dict[str, Any]:
        overrides = self.pocket_entry_overrides or {}
        if not isinstance(overrides, dict) or not overrides:
            return {}

        candidates = [
            self.normalize_pocket_key(signal_type_1h, vwap_state),
            self.normalize_pocket_key("*", vwap_state),
            self.normalize_pocket_key(signal_type_1h, "*"),
            self.normalize_pocket_key("*", "*"),
        ]
        for key in candidates:
            override = overrides.get(key)
            if isinstance(override, dict) and override:
                return dict(override)
        return {}

    def resolve_pocket_scoring_override(
        self,
        signal_type_1h: Optional[str],
        vwap_state: Optional[str],
    ) -> Dict[str, Any]:
        overrides = self.pocket_scoring_overrides or {}
        if not isinstance(overrides, dict) or not overrides:
            return {}

        candidates = [
            self.normalize_pocket_key(signal_type_1h, vwap_state),
            self.normalize_pocket_key("*", vwap_state),
            self.normalize_pocket_key(signal_type_1h, "*"),
            self.normalize_pocket_key("*", "*"),
        ]
        for key in candidates:
            override = overrides.get(key)
            if isinstance(override, dict) and override:
                return dict(override)
        return {}

    def resolve_pocket_management_override(
        self,
        signal_type_1h: Optional[str],
        vwap_state: Optional[str],
    ) -> Dict[str, Any]:
        overrides = self.pocket_management_overrides or {}
        if not isinstance(overrides, dict) or not overrides:
            return {}

        candidates = [
            self.normalize_pocket_key(signal_type_1h, vwap_state),
            self.normalize_pocket_key("*", vwap_state),
            self.normalize_pocket_key(signal_type_1h, "*"),
            self.normalize_pocket_key("*", "*"),
        ]
        for key in candidates:
            value = overrides.get(key)
            if isinstance(value, dict):
                return copy.deepcopy(value)
        return {}


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

    # 双套件信息
    selected_suite: str = ""  # "RSI_MRV" / "BOLL_MBV" / ""
    regime: str = ""  # "TRENDING_BULL" / "TRENDING_BEAR" / "RANGING" / "BREAKOUT_WATCH" / "VOLATILE"
    suite_score: float = 0.0
    rsi_gate_pass: bool = True
    rsi_4h: float = 50.0
    rsi_15m: float = 50.0
    rsi_slope_1h: float = 0.0
    divergence: Dict = field(default_factory=dict)

    details: Dict = field(default_factory=dict)


@dataclass
class Q4PreflipRejectionDetail:
    """Q4 preflip 失败时的逐门输入快照，供离线诊断使用。"""

    passed: bool = False
    reason: str = ""
    symbol: str = ""
    quadrant: str = ""
    signal_type_1h: str = ""
    symbol_gate_pass: bool = False
    symbol_gate_reason: str = ""
    macd_line_4h: float = 0.0
    macd_4h_shrink_pct: float = 0.0
    shrink_threshold: float = 0.0
    hist_4h_current: float = 0.0
    hist_4h_prev: float = 0.0
    hist_narrowing: bool = False
    close_price: float = 0.0
    bb_middle_1h: float = 0.0
    rsi_1h: float = 0.0
    rsi_1h_min: float = 0.0
    rsi_4h: float = 0.0
    rsi_4h_floor: float = 0.0
    atr_pct_1h: float = 0.0


def build_macd_v2_config_from_runtime(
    runtime_cfg: Dict[str, Any],
    *,
    disable_cvd_decision_logic: bool = False,
) -> MACDStrategyV2Config:
    """Build MACDStrategyV2Config from runtime config with the same semantics as pure backtest."""
    runtime_cfg = runtime_cfg if isinstance(runtime_cfg, dict) else {}
    ff_cfg = runtime_cfg.get("fund_flow", {}) if isinstance(runtime_cfg.get("fund_flow"), dict) else {}
    v2_cfg = ff_cfg.get("macd_mtf_strategy_v2", {}) if isinstance(ff_cfg.get("macd_mtf_strategy_v2"), dict) else {}
    boll_cfg = v2_cfg.get("boll_config", {}) if isinstance(v2_cfg.get("boll_config"), dict) else {}
    ema_cfg = v2_cfg.get("ema_config", {}) if isinstance(v2_cfg.get("ema_config"), dict) else {}
    leverage_cfg = v2_cfg.get("leverage_config", {}) if isinstance(v2_cfg.get("leverage_config"), dict) else {}
    cvd_filter_cfg = v2_cfg.get("cvd_filter_config", {}) if isinstance(v2_cfg.get("cvd_filter_config"), dict) else {}
    vwap_cfg = v2_cfg.get("vwap_config", {}) if isinstance(v2_cfg.get("vwap_config"), dict) else {}
    weights_cfg = v2_cfg.get("scoring_weights", {}) if isinstance(v2_cfg.get("scoring_weights"), dict) else {}
    thresholds_cfg = v2_cfg.get("entry_thresholds", {}) if isinstance(v2_cfg.get("entry_thresholds"), dict) else {}
    stop_cfg = v2_cfg.get("stop_loss_config", {}) if isinstance(v2_cfg.get("stop_loss_config"), dict) else {}
    session_risk_cfg = v2_cfg.get("session_risk_control", {}) if isinstance(v2_cfg.get("session_risk_control"), dict) else {}
    vwap_score_tier_cfg = v2_cfg.get("vwap_score_position_tiers", {}) if isinstance(v2_cfg.get("vwap_score_position_tiers"), dict) else {}
    position_size_cfg = v2_cfg.get("position_size_config", {}) if isinstance(v2_cfg.get("position_size_config"), dict) else {}
    symbol_risk_cfg = v2_cfg.get("symbol_risk_tiers", {}) if isinstance(v2_cfg.get("symbol_risk_tiers"), dict) else {}
    macd_cfg = v2_cfg.get("macd_config", {}) if isinstance(v2_cfg.get("macd_config"), dict) else {}
    filter_cfg = v2_cfg.get("entry_filters", {}) if isinstance(v2_cfg.get("entry_filters"), dict) else {}
    penalty_cfg = v2_cfg.get("penalty_config", {}) if isinstance(v2_cfg.get("penalty_config"), dict) else {}
    default_signal_threshold = float(thresholds_cfg.get("default", thresholds_cfg.get("min_signal_score", 0.850)))

    config_obj = MACDStrategyV2Config(
        macd_1h_fast=int(macd_cfg.get("macd_1h_fast", 12)),
        macd_1h_slow=int(macd_cfg.get("macd_1h_slow", 26)),
        macd_1h_signal=int(macd_cfg.get("macd_1h_signal", 9)),
        macd_4h_fast=int(macd_cfg.get("macd_4h_fast", 12)),
        macd_4h_slow=int(macd_cfg.get("macd_4h_slow", 26)),
        macd_4h_signal=int(macd_cfg.get("macd_4h_signal", 9)),
        macd_15m_fast=int(macd_cfg.get("macd_15m_fast", 12)),
        macd_15m_slow=int(macd_cfg.get("macd_15m_slow", 26)),
        macd_15m_signal=int(macd_cfg.get("macd_15m_signal", 9)),
        macd_threshold=float(macd_cfg.get("macd_threshold", 0.00005)),
        boll_period=int(float(boll_cfg.get("period", 20))),
        boll_std_dev=float(boll_cfg.get("std_dev", 2.0)),
        ema_multiplier_strong=float(boll_cfg.get("multiplier_strong", ema_cfg.get("ema_multiplier_strong", 1.2))),
        ema_multiplier_normal=float(boll_cfg.get("multiplier_normal", ema_cfg.get("ema_multiplier_normal", 1.0))),
        ema_multiplier_weak=float(boll_cfg.get("multiplier_weak", ema_cfg.get("ema_multiplier_weak", 0.6))),
        ema_55_1h_hard_block=bool(boll_cfg.get("middle_hard_block", ema_cfg.get("ema_55_1h_hard_block", True))),
        ema_strong_trend_leverage_mult=float(
            boll_cfg.get("strong_trend_leverage_mult", ema_cfg.get("ema_strong_trend_leverage_mult", 0.8))
        ),
        vwap_deviation_optimal=float(vwap_cfg.get("vwap_deviation_optimal", 0.005)),
        vwap_deviation_warning=float(vwap_cfg.get("vwap_deviation_warning", 0.015)),
        vwap_deviation_hard_block=float(vwap_cfg.get("vwap_deviation_hard_block", 0.030)),
        structural_vwap_mode=str(vwap_cfg.get("structural_vwap_mode", "anchored_daily")),
        structural_vwap_rolling_window=int(float(vwap_cfg.get("structural_vwap_rolling_window", 20))),
        vwap_retest_tolerance=float(vwap_cfg.get("vwap_retest_tolerance", 0.003)),
        weight_1h_direction=float(weights_cfg.get("weight_1h_direction", 0.00)),
        weight_4h_direction=float(weights_cfg.get("weight_4h_direction", weights_cfg.get("weight_1h_direction", 0.55))),
        weight_4h_enhancement=float(weights_cfg.get("weight_4h_enhancement", 0.10)),
        weight_boll_position=float(weights_cfg.get("weight_boll_position", 0.0)),
        weight_boll_rsi_resonance=float(
            weights_cfg.get("weight_boll_rsi_resonance", 0.0)
        ),
        weight_vwap=0.0,
        weight_15m_entry=float(weights_cfg.get("weight_15m_entry", 0.05)),
        weight_volume=float(weights_cfg.get("weight_volume", 0.20)),
        min_entry_score=float(thresholds_cfg.get("min_entry_score", 0.25)),
        min_signal_score=default_signal_threshold,
        red_bar_growing_min_signal_score=float(thresholds_cfg.get("red_bar_growing", default_signal_threshold)),
        red_bar_shrinking_min_signal_score=float(thresholds_cfg.get("red_bar_shrinking", default_signal_threshold)),
        flip_bearish_min_signal_score=float(thresholds_cfg.get("flip_bearish", default_signal_threshold)),
        flip_bullish_min_signal_score=float(thresholds_cfg.get("flip_bullish", default_signal_threshold)),
        enable_flip_bullish_strict_filter=bool(filter_cfg.get("enable_flip_bullish_strict_filter", True)),
        disable_flip_bullish_entries=bool(filter_cfg.get("disable_flip_bullish_entries", False)),
        disable_flip_bearish_entries=bool(filter_cfg.get("disable_flip_bearish_entries", False)),
        disable_flip_bullish_trial_entries=bool(filter_cfg.get("disable_flip_bullish_trial_entries", False)),
        flip_bullish_min_vwap_score=0.0,
        flip_bullish_require_pullback_bounce=bool(filter_cfg.get("flip_bullish_require_pullback_bounce", True)),
        flip_bullish_require_15m_growing=bool(filter_cfg.get("flip_bullish_require_15m_growing", True)),
        enable_flip_bullish_cvd_context_filter=(
            False if disable_cvd_decision_logic else bool(filter_cfg.get("enable_flip_bullish_cvd_context_filter", False))
        ),
        flip_bullish_max_cvd_upper_wick_ratio=(
            0.0 if disable_cvd_decision_logic else float(filter_cfg.get("flip_bullish_max_cvd_upper_wick_ratio", 0.0))
        ),
        flip_bullish_min_cvd_1h_delta_ratio=(
            0.0 if disable_cvd_decision_logic else float(filter_cfg.get("flip_bullish_min_cvd_1h_delta_ratio", 0.0))
        ),
        flip_bullish_trial_score_window_enabled=bool(filter_cfg.get("flip_bullish_trial_score_window_enabled", False)),
        flip_bullish_trial_score_min=float(filter_cfg.get("flip_bullish_trial_score_min", 0.80)),
        flip_bullish_trial_score_max=float(filter_cfg.get("flip_bullish_trial_score_max", 0.87)),
        green_bar_growing_score_window_enabled=bool(filter_cfg.get("green_bar_growing_score_window_enabled", False)),
        green_bar_growing_score_min=float(filter_cfg.get("green_bar_growing_score_min", 0.0)),
        green_bar_growing_score_max=float(filter_cfg.get("green_bar_growing_score_max", 1.0)),
        flip_bearish_min_ema_multiplier=float(
            filter_cfg.get("flip_bearish_min_boll_multiplier", filter_cfg.get("flip_bearish_min_ema_multiplier", 0.0))
        ),
        flip_bearish_normal_ema_min_signal_score=float(
            filter_cfg.get("flip_bearish_normal_boll_min_signal_score", filter_cfg.get("flip_bearish_normal_ema_min_signal_score", 0.0))
        ),
        flip_bearish_normal_ema_max_leverage=int(
            float(filter_cfg.get("flip_bearish_normal_boll_max_leverage", filter_cfg.get("flip_bearish_normal_ema_max_leverage", 0.0)))
        ),
        flip_bearish_min_adx_1h=float(filter_cfg.get("flip_bearish_min_adx_1h", 18.0)),
        flip_bearish_retest_reject_min_vwap_score=float(filter_cfg.get("flip_bearish_retest_reject_min_vwap_score", 0.0)),
        flip_bearish_max_ema21_slope_1h=float(
            filter_cfg.get("flip_bearish_max_bb_middle_slope_1h", filter_cfg.get("flip_bearish_max_ema21_slope_1h", 0.0))
        ),
        flip_bearish_max_ema21_slope_4h=float(
            filter_cfg.get("flip_bearish_max_bb_middle_slope_4h", filter_cfg.get("flip_bearish_max_ema21_slope_4h", 0.0001))
        ),
        ema_slope_lookback_1h=int(float(filter_cfg.get("bb_slope_lookback_1h", filter_cfg.get("ema_slope_lookback_1h", 3)))),
        ema_slope_lookback_4h=int(float(filter_cfg.get("bb_slope_lookback_4h", filter_cfg.get("ema_slope_lookback_4h", 2)))),
        disable_red_bar_growing_long_entries=bool(filter_cfg.get("disable_red_bar_growing_long_entries", False)),
        disable_red_bar_shrinking_entries=bool(filter_cfg.get("disable_red_bar_shrinking_entries", False)),
        flip_bearish_resonance_guards=dict(filter_cfg.get("flip_bearish_resonance_guards", {
            "min_boll_zone_score": 0.60,
            "require_4h_histogram_negative": True,
            "rsi_1h_range_max": 60,
        })),
        red_bar_growing_long_guards=dict(filter_cfg.get("red_bar_growing_long_guards", {
            "min_boll_zone_score_long": 0.65,
            "require_4h_histogram_positive": True,
            "rsi_1h_range_min": 42,
            "rsi_1h_range_max": 65,
        })),
        long_entry_mode=str(filter_cfg.get("long_entry_mode", "all") or "all").strip().lower(),
        long_whitelist_signal_types=[
            str(x).strip().lower()
            for x in (filter_cfg.get("long_whitelist_signal_types", []) or [])
            if str(x).strip()
        ] if isinstance(filter_cfg.get("long_whitelist_signal_types"), list) else [],
        long_whitelist_vwap_states=[
            str(x).strip().lower()
            for x in (filter_cfg.get("long_whitelist_vwap_states", []) or [])
            if str(x).strip()
        ] if isinstance(filter_cfg.get("long_whitelist_vwap_states"), list) else [],
        long_whitelist_pockets=[
            MACDStrategyV2Config.normalize_pocket_key(*str(x).split("|", 1))
            for x in (filter_cfg.get("long_whitelist_pockets", []) or [])
            if str(x).strip() and "|" in str(x)
        ] if isinstance(filter_cfg.get("long_whitelist_pockets"), list) else [],
        disable_green_bar_growing_entries=bool(filter_cfg.get("disable_green_bar_growing_entries", True)),
        disable_green_bar_growing_short_entries=bool(filter_cfg.get("disable_green_bar_growing_short_entries", False)),
        disable_green_bar_shrinking_entries=bool(filter_cfg.get("disable_green_bar_shrinking_entries", False)),
        disable_green_bar_shrinking_short_dual_pressure_entries=bool(filter_cfg.get("disable_green_bar_shrinking_short_dual_pressure_entries", True)),
        disable_red_bar_shrinking_long_dual_support_entries=bool(filter_cfg.get("disable_red_bar_shrinking_long_dual_support_entries", True)),
        require_macd_home_advantage=bool(filter_cfg.get("require_macd_home_advantage", False)),
        vwap_execution_penalty_only=bool(filter_cfg.get("vwap_execution_penalty_only", False)),
        primary_direction_timeframe=str(filter_cfg.get("primary_direction_timeframe", "4h")),
        require_1h_confirmation_when_4h_primary=bool(filter_cfg.get("require_1h_confirmation_when_4h_primary", False)),
        allow_neutral_1h_confirmation=bool(filter_cfg.get("allow_neutral_1h_confirmation", False)),
        light_1h_confirmation_when_4h_primary=bool(filter_cfg.get("light_1h_confirmation_when_4h_primary", False)),
        enable_soft_15m_confirmation_when_4h_primary=bool(filter_cfg.get("enable_soft_15m_confirmation_when_4h_primary", True)),
        require_15m_confirmation_gate=bool(filter_cfg.get("require_15m_confirmation_gate", False)),
        soft_15m_entry_score=float(filter_cfg.get("soft_15m_entry_score", 0.28)),
        soft_15m_neutral_hist_multiple=float(filter_cfg.get("soft_15m_neutral_hist_multiple", 3.0)),
        soft_15m_max_adverse_hist_multiple=float(filter_cfg.get("soft_15m_max_adverse_hist_multiple", 8.0)),
        enable_green_bar_growing_short_adx_1h_range_filter=bool(filter_cfg.get("enable_green_bar_growing_short_adx_1h_range_filter", False)),
        green_bar_growing_short_min_adx_1h=float(filter_cfg.get("green_bar_growing_short_min_adx_1h", 0.0)),
        green_bar_growing_short_max_adx_1h=float(filter_cfg.get("green_bar_growing_short_max_adx_1h", 0.0)),
        enable_4h_preflip_trial_entries=bool(filter_cfg.get("enable_4h_preflip_trial_entries", False)),
        preflip_trial_min_shrink_pct_long=float(filter_cfg.get("preflip_trial_min_shrink_pct_long", 0.75)),
        preflip_trial_min_shrink_pct_short=float(filter_cfg.get("preflip_trial_min_shrink_pct_short", 0.30)),
        preflip_trial_min_signal_score=float(filter_cfg.get("preflip_trial_min_signal_score", 0.78)),
        preflip_trial_min_vwap_score=0.0,
        preflip_trial_entry_scale=float(filter_cfg.get("preflip_trial_entry_scale", 0.35)),
        preflip_trial_max_leverage=int(float(filter_cfg.get("preflip_trial_max_leverage", 2))),
        enable_q4_rsi_lead_preflip_long=bool(filter_cfg.get("enable_q4_rsi_lead_preflip_long", False)),
        q4_rsi_lead_preflip_min_4h_shrink_pct=float(filter_cfg.get("q4_rsi_lead_preflip_min_4h_shrink_pct", 0.75)),
        q4_rsi_lead_preflip_rsi_1h_min=float(filter_cfg.get("q4_rsi_lead_preflip_rsi_1h_min", 50.0)),
        q4_rsi_lead_preflip_rsi_4h_min=float(filter_cfg.get("q4_rsi_lead_preflip_rsi_4h_min", 50.0)),
        q4_rsi_lead_preflip_rsi_4h_near_buffer=float(filter_cfg.get("q4_rsi_lead_preflip_rsi_4h_near_buffer", 2.0)),
        q4_rsi_lead_preflip_bonus_score=float(filter_cfg.get("q4_rsi_lead_preflip_bonus_score", 0.18)),
        q4_rsi_lead_preflip_entry_scale=float(filter_cfg.get("q4_rsi_lead_preflip_entry_scale", 0.35)),
        q4_rsi_lead_preflip_strict_eth_only=bool(filter_cfg.get("q4_rsi_lead_preflip_strict_eth_only", True)),
        q4_rsi_lead_preflip_allowed_symbols=[
            str(x).strip().upper()
            for x in (filter_cfg.get("q4_rsi_lead_preflip_allowed_symbols", ["ETHUSDT"]) or [])
            if str(x).strip()
        ] if isinstance(filter_cfg.get("q4_rsi_lead_preflip_allowed_symbols", ["ETHUSDT"]), list) else ["ETHUSDT"],
        q4_rsi_lead_preflip_allowed_categories=[
            str(x).strip().lower()
            for x in (filter_cfg.get("q4_rsi_lead_preflip_allowed_categories", []) or [])
            if str(x).strip()
        ] if isinstance(filter_cfg.get("q4_rsi_lead_preflip_allowed_categories"), list) else [],
        q4_rsi_lead_preflip_min_atr_pct_1h=float(filter_cfg.get("q4_rsi_lead_preflip_min_atr_pct_1h", 0.0)),
        q4_rsi_lead_preflip_max_atr_pct_1h=float(filter_cfg.get("q4_rsi_lead_preflip_max_atr_pct_1h", 1.0)),
        q4_rsi_lead_preflip_min_signal_score_override=float(
            filter_cfg.get("q4_rsi_lead_preflip_min_signal_score_override", 0.0)
        ),
        rsi_gate_q4_exempt=bool(filter_cfg.get("rsi_gate_q4_exempt", False)),
        enable_q4_rsi_lead_preflip_hold=bool(filter_cfg.get("enable_q4_rsi_lead_preflip_hold", False)),
        q4_rsi_lead_preflip_hold_exit_metric=str(
            filter_cfg.get("q4_rsi_lead_preflip_hold_exit_metric", "rsi_21") or "rsi_21"
        ).strip().lower(),
        q4_rsi_lead_preflip_hold_rsi_4h_exit_threshold=float(
            filter_cfg.get("q4_rsi_lead_preflip_hold_rsi_4h_exit_threshold", 70.0)
        ),
        q4_rsi_lead_preflip_hold_rsi_4h_pullback=float(
            filter_cfg.get("q4_rsi_lead_preflip_hold_rsi_4h_pullback", 1.0)
        ),
        enable_trial_short_below_structure_continuation_promotion=bool(
            filter_cfg.get("enable_trial_short_below_structure_continuation_promotion", False)
        ),
        trial_short_below_structure_promotion_min_signal_score=float(
            filter_cfg.get("trial_short_below_structure_promotion_min_signal_score", 0.82)
        ),
        trial_short_below_structure_promotion_min_vwap_score=0.0,
        trial_short_below_structure_promotion_min_adx_1h=float(
            filter_cfg.get("trial_short_below_structure_promotion_min_adx_1h", 25.0)
        ),
        trial_short_below_structure_promotion_min_4h_shrink_pct=float(
            filter_cfg.get("trial_short_below_structure_promotion_min_4h_shrink_pct", 0.80)
        ),
        trial_short_below_structure_promotion_min_4h_shrink_bars=int(
            float(filter_cfg.get("trial_short_below_structure_promotion_min_4h_shrink_bars", 6))
        ),
        enable_stable_bear_continuation=bool(filter_cfg.get("enable_stable_bear_continuation", True)),
        stable_bear_continuation_min_signal_score=float(
            thresholds_cfg.get("stable_bear_continuation_min_signal_score", filter_cfg.get("stable_bear_continuation_min_signal_score", 0.82))
        ),
        stable_bear_continuation_min_vwap_score=0.0,
        stable_bear_continuation_min_adx_1h=float(filter_cfg.get("stable_bear_continuation_min_adx_1h", 20.0)),
        stable_bear_continuation_min_4h_bars=int(float(filter_cfg.get("stable_bear_continuation_min_4h_bars", 2))),
        enable_stable_bull_continuation=bool(filter_cfg.get("enable_stable_bull_continuation", False)),
        stable_bull_continuation_min_signal_score=float(
            thresholds_cfg.get("stable_bull_continuation_min_signal_score", filter_cfg.get("stable_bull_continuation_min_signal_score", 0.82))
        ),
        stable_bull_continuation_min_vwap_score=0.0,
        stable_bull_continuation_min_adx_1h=float(filter_cfg.get("stable_bull_continuation_min_adx_1h", 20.0)),
        stable_bull_continuation_min_4h_bars=int(float(filter_cfg.get("stable_bull_continuation_min_4h_bars", 2))),
        pocket_entry_overrides=copy.deepcopy(filter_cfg.get("pocket_entry_overrides", {}))
        if isinstance(filter_cfg.get("pocket_entry_overrides"), dict) else {},
        rsi_period=int((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("period", 14)),
        rsi_oversold_threshold=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("oversold", 30.0)),
        rsi_overbought_threshold=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("overbought", 70.0)),
        rsi_require_ok_for_growing=bool((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("require_ok_for_growing", True)),
        rsi_require_ok_for_flip=bool((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("require_ok_for_flip", False)),
        rsi_adx_threshold_strong=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("adx_threshold_strong", 25.0)),
        rsi_adx_threshold_weak=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("adx_threshold_weak", 15.0)),
        pocket_scoring_overrides=copy.deepcopy(v2_cfg.get("pocket_scoring_overrides", {}))
        if isinstance(v2_cfg.get("pocket_scoring_overrides"), dict) else {},
        pocket_management_overrides=copy.deepcopy(v2_cfg.get("pocket_management_overrides", {}))
        if isinstance(v2_cfg.get("pocket_management_overrides"), dict) else {},
        overheat_growing_penalty=float(penalty_cfg.get("overheat_growing_penalty", 0.12)),
        overheat_ema_multiplier_threshold=float(
            penalty_cfg.get("overheat_boll_multiplier_threshold", penalty_cfg.get("overheat_ema_multiplier_threshold", 1.2))
        ),
        overheat_vwap_score_threshold=float(penalty_cfg.get("overheat_vwap_score_threshold", 0.10)),
        min_vwap_score_for_entry=0.0,
        disable_vwap_thresholds=True,
        use_dynamic_stop=bool(stop_cfg.get("use_dynamic_stop", True)),
        ema_stop_atr_multiplier=float(stop_cfg.get("boll_stop_atr_multiplier", stop_cfg.get("ema_stop_atr_multiplier", 0.5))),
        max_stop_loss_pct=float(stop_cfg.get("max_stop_loss_pct", 0.025)),
        max_stop_distance_pct=float(stop_cfg.get("max_stop_distance_pct", stop_cfg.get("max_stop_loss_pct", 0.025))),
        vwap_alert_deviation=float(stop_cfg.get("vwap_alert_deviation", 0.005)),
        enable_4h_shrink_exit=bool(stop_cfg.get("enable_4h_shrink_exit", False)),
        exit_4h_shrink_bars=int(float(stop_cfg.get("exit_4h_shrink_bars", 2))),
        exit_4h_min_shrink_pct=float(stop_cfg.get("exit_4h_min_shrink_pct", 0.20)),
        exit_4h_require_profit=bool(stop_cfg.get("exit_4h_require_profit", True)),
        exit_4h_weak_loss_threshold=float(stop_cfg.get("exit_4h_weak_loss_threshold", -1.0)),
        shrink_exit_loss_mitigation_enabled=bool(stop_cfg.get("shrink_exit_loss_mitigation_enabled", False)),
        shrink_exit_loss_mitigation_pnl_threshold=float(stop_cfg.get("shrink_exit_loss_mitigation_pnl_threshold", -0.005)),
        shrink_exit_loss_mitigation_exit_ratio=float(stop_cfg.get("shrink_exit_loss_mitigation_exit_ratio", 0.60)),
        shrink_exit_loss_mitigation_ignore_if_pnl_gt=float(stop_cfg.get("shrink_exit_loss_mitigation_ignore_if_pnl_gt", 0.01)),
        session_risk_control_enabled=bool(session_risk_cfg.get("enabled", False)),
        session_risk_high_risk_sessions=copy.deepcopy(session_risk_cfg.get("high_risk_sessions", []))
        if isinstance(session_risk_cfg.get("high_risk_sessions"), list) else [],
        session_risk_apply_to_states=[
            str(x).strip() for x in (session_risk_cfg.get("apply_to_states", []) or []) if str(x).strip()
        ] if isinstance(session_risk_cfg.get("apply_to_states"), list) else [],
        vwap_score_tier_apply_to_states=[
            str(x).strip() for x in (vwap_score_tier_cfg.get("apply_to_states", []) or []) if str(x).strip()
        ] if isinstance(vwap_score_tier_cfg.get("apply_to_states"), list) else [],
        vwap_score_position_tiers=copy.deepcopy(vwap_score_tier_cfg.get("tiers", []))
        if isinstance(vwap_score_tier_cfg.get("tiers"), list) else [],
        position_score_tiers=copy.deepcopy(position_size_cfg.get("score_tiers", []))
        if isinstance(position_size_cfg.get("score_tiers"), list) else [],
        symbol_risk_watchlist_symbols=[
            str(x).strip().upper() for x in (symbol_risk_cfg.get("watchlist_symbols", []) or []) if str(x).strip()
        ] if isinstance(symbol_risk_cfg.get("watchlist_symbols"), list) else [],
        symbol_risk_watchlist_max_position_portion=float(symbol_risk_cfg.get("watchlist_max_position_portion", 0.0)),
        symbol_risk_watchlist_max_leverage=int(float(symbol_risk_cfg.get("watchlist_max_leverage", 0))),
        symbol_risk_watchlist_apply_session_scale_double=bool(symbol_risk_cfg.get("watchlist_apply_session_scale_double", False)),
        symbol_risk_watchlist_session_scale_multiplier=float(symbol_risk_cfg.get("watchlist_session_scale_multiplier", 0.80)),
        leverage_score_tiers=copy.deepcopy(leverage_cfg.get("score_tiers", []))
        if isinstance(leverage_cfg.get("score_tiers"), list) else [],
        dual_pressure_target_portion_bonus=float(leverage_cfg.get("dual_pressure_target_portion_bonus", 0.0)),
        dual_pressure_max_symbol_position_portion=float(leverage_cfg.get("dual_pressure_max_symbol_position_portion", 0.0)),
        use_cvd_bonus_filter=False if disable_cvd_decision_logic else bool(leverage_cfg.get("use_cvd_bonus_filter", False)),
        cvd_1h_slope_lookback=int(float(leverage_cfg.get("cvd_1h_slope_lookback", 3))),
        cvd_15m_slope_lookback=int(float(leverage_cfg.get("cvd_15m_slope_lookback", 3))),
        cvd_positive_delta_ratio_threshold=float(leverage_cfg.get("cvd_positive_delta_ratio_threshold", 0.05)),
        cvd_negative_delta_ratio_threshold=float(leverage_cfg.get("cvd_negative_delta_ratio_threshold", -0.05)),
        cvd_bullish_bonus_multiplier=float(leverage_cfg.get("cvd_bullish_bonus_multiplier", 0.0)),
        cvd_neutral_bonus_multiplier=float(leverage_cfg.get("cvd_neutral_bonus_multiplier", 0.7)),
        cvd_bearish_bonus_multiplier=float(leverage_cfg.get("cvd_bearish_bonus_multiplier", 1.0)),
        use_cvd_veto_filter=False if disable_cvd_decision_logic else bool(cvd_filter_cfg.get("enabled", False)),
        cvd_veto_session_reset=str(cvd_filter_cfg.get("session_reset", "daily_utc0")),
        cvd_veto_lookback_15m=int(float(cvd_filter_cfg.get("lookback_15m", 3))),
        cvd_veto_positive_delta_ratio_threshold=float(cvd_filter_cfg.get("positive_delta_ratio_threshold", 0.10)),
        cvd_veto_positive_pressure_threshold=float(cvd_filter_cfg.get("positive_pressure_threshold", 0.0)),
        cvd_veto_session_ratio_change_threshold=float(cvd_filter_cfg.get("session_ratio_change_threshold", -0.003)),
        cvd_veto_session_price_change_threshold=float(cvd_filter_cfg.get("session_price_change_threshold", -0.005)),
        cvd_veto_strong_close_pos_threshold=float(cvd_filter_cfg.get("strong_close_pos_threshold", 0.72)),
        cvd_veto_upper_wick_ratio_max=float(cvd_filter_cfg.get("upper_wick_ratio_max", 0.25)),
        cvd_absorption_delta_ratio_threshold=float(cvd_filter_cfg.get("absorption_delta_ratio_threshold", 0.05)),
        cvd_absorption_close_pos_threshold=float(cvd_filter_cfg.get("absorption_close_pos_threshold", 0.45)),
        cvd_absorption_upper_wick_ratio_threshold=float(cvd_filter_cfg.get("absorption_upper_wick_ratio_threshold", 0.35)),
        cvd_absorption_structure_gap_threshold=float(cvd_filter_cfg.get("absorption_structure_gap_threshold", 0.002)),
        cvd_divergence_price_change_threshold=float(cvd_filter_cfg.get("divergence_price_change_threshold", 0.003)),
        cvd_divergence_session_change_threshold=float(cvd_filter_cfg.get("divergence_session_change_threshold", -0.005)),
        enable_short_quality_filter=bool(v2_cfg.get("short_quality_filter", {}).get("enabled", False)),
        short_filter_min_funding_rate=float(v2_cfg.get("short_quality_filter", {}).get("min_funding_rate", 0.0005)),
        short_filter_max_oi_delta_ratio=float(v2_cfg.get("short_quality_filter", {}).get("max_oi_delta_ratio", 0.0)),
        short_filter_min_vwap_deviation=float(v2_cfg.get("short_quality_filter", {}).get("min_vwap_deviation", 0.005)),
        # 双套件配置
        enable_dual_suite=bool(v2_cfg.get("enable_dual_suite", False)),
        indicator_suite=str(v2_cfg.get("indicator_suite", "adaptive")),
        suite_adx_trending_threshold=float(v2_cfg.get("indicator_suite_config", {}).get("adx_trending_threshold", 25.0)),
        suite_adx_ranging_threshold=float(v2_cfg.get("indicator_suite_config", {}).get("adx_ranging_threshold", 22.0)),
        suite_boll_bandwidth_trending_ratio=float(v2_cfg.get("indicator_suite_config", {}).get("boll_bandwidth_trending_ratio", 1.3)),
        suite_switch_cooldown_bars_1h=int(float(v2_cfg.get("indicator_suite_config", {}).get("suite_switch_cooldown_bars_1h", 4))),
        suite_atr_volatile_threshold=float(v2_cfg.get("indicator_suite_config", {}).get("atr_volatile_threshold", 0.025)),
        rsi_mrv_min_score=float(v2_cfg.get("rsi_mrv_min_score", 0.70)),
        boll_mbv_min_score=float(v2_cfg.get("boll_mbv_min_score", 0.65)),
        signal_family_whitelist=copy.deepcopy(v2_cfg.get("signal_family_whitelist", {}))
        if isinstance(v2_cfg.get("signal_family_whitelist"), dict) else {},
        rsi_gate_enabled=bool((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_gate", {}).get("enabled", False)),
        rsi_gate_long_rsi_4h_min=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_gate", {}).get("long_rsi_4h_min", 45.0)),
        rsi_gate_long_rsi_1h_min=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_gate", {}).get("long_rsi_1h_min", 45.0)),
        rsi_gate_long_rsi_1h_max=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_gate", {}).get("long_rsi_1h_max", 70.0)),
        rsi_gate_long_rsi_15m_min=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_gate", {}).get("long_rsi_15m_min", 48.0)),
        rsi_gate_short_rsi_4h_max=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_gate", {}).get("short_rsi_4h_max", 55.0)),
        rsi_gate_short_rsi_1h_max=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_gate", {}).get("short_rsi_1h_max", 55.0)),
        rsi_gate_short_rsi_1h_min=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_gate", {}).get("short_rsi_1h_min", 30.0)),
        rsi_gate_short_rsi_15m_max=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_gate", {}).get("short_rsi_15m_max", 52.0)),
        rsi_flip_bullish_rsi_4h_min=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_flip_override", {}).get("flip_bullish_rsi_4h_min", 42.0)),
        rsi_flip_bullish_rsi_1h_min=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_flip_override", {}).get("flip_bullish_rsi_1h_min", 42.0)),
        rsi_flip_bearish_rsi_4h_max=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_flip_override", {}).get("flip_bearish_rsi_4h_max", 58.0)),
        rsi_flip_bearish_rsi_1h_max=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("rsi_flip_override", {}).get("flip_bearish_rsi_1h_max", 58.0)),
        rsi_extreme_overbought=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("extreme_overbought", 78.0)),
        rsi_extreme_oversold=float((filter_cfg.get("rsi_config", {}) or v2_cfg.get("rsi_config", {})).get("extreme_oversold", 22.0)),
    )

    # 保存原始配置以便三共振系统使用
    config_obj._raw_config = runtime_cfg

    return config_obj


class MACDStrategyV2Engine:
    """MACD策略V2.0引擎"""
    
    def __init__(self, config: MACDStrategyV2Config = None):
        self.config = config or MACDStrategyV2Config()
        self._last_analysis: Dict = {}
        self._suite_selector = None  # 延迟初始化
        # 三共振组件 (延迟初始化, 替代 VWAP)
        self._boll_structure_analyzer = None
        self._rsi_analyzer = None
        self._resonance_scorer = None

        # 如果enable_dual_suite,立即初始化三共振组件
        if self.config.enable_dual_suite:
            try:
                from src.indicators.boll_structure_analyzer import BOLLStructureAnalyzer
                from src.indicators.rsi_analyzer import RSIAnalyzer
                from src.fund_flow.resonance_scorer import ResonanceScorer

                # BOLL配置
                boll_cfg = {
                    'period': self.config.boll_period,
                    'std_dev': self.config.boll_std_dev,
                }

                # RSI配置
                rsi_cfg = {
                    'period_15m': 7,
                    'period_1h': self.config.rsi_period,
                    'period_4h': 21,
                }

                # 共振配置
                if hasattr(self.config, '_raw_config') and self.config._raw_config:
                    resonance_cfg = self.config._raw_config
                else:
                    resonance_cfg = {
                        'fund_flow': {
                            'macd_mtf_strategy_v2': {
                                'entry_filters': {
                                    'resonance_scoring': {
                                        'enabled': True,
                                        'weights': {
                                            'macd_base': 0.4,
                                            'boll_structure': 0.35,
                                            'rsi_momentum': 0.25,
                                        },
                                    },
                                },
                            },
                        },
                    }

                self._boll_structure_analyzer = BOLLStructureAnalyzer(boll_cfg)
                self._rsi_analyzer = RSIAnalyzer({'rsi_config': rsi_cfg})
                self._resonance_scorer = ResonanceScorer(resonance_cfg)
            except Exception as e:
                logger.warning(f"三共振组件初始化失败: {e}")
                self._resonance_scorer = None

    def _init_suite_selector(self):
        """延迟初始化双套件选择器（仅在 enable_dual_suite=True 时）"""
        if not self.config.enable_dual_suite:
            self._suite_selector = None
            return

        from src.indicators.rsi_indicator import RSIIndicator
        from src.indicators.market_regime_classifier import MarketRegimeClassifier
        from src.indicators.rsi_mrv_analyzer import RSIMRVAnalyzer
        from src.indicators.boll_mbv_analyzer import BOLLMBVAnalyzer
        from src.fund_flow.adaptive_suite_selector import AdaptiveSuiteSelector

        rsi_cfg = {
            "period_15m": 7,
            "period_1h": self.config.rsi_period,
            "period_4h": 21,
            "divergence_lookback_bars": 10,
            "divergence_min_price_diff_pct": 0.005,
            "divergence_min_rsi_diff": 3.0,
        }
        suite_cfg = {
            "adx_trending_threshold": self.config.suite_adx_trending_threshold,
            "adx_ranging_threshold": self.config.suite_adx_ranging_threshold,
            "boll_bandwidth_trending_ratio": self.config.suite_boll_bandwidth_trending_ratio,
            "suite_switch_cooldown_bars_1h": self.config.suite_switch_cooldown_bars_1h,
            "atr_volatile_threshold": self.config.suite_atr_volatile_threshold,
        }
        # 构建 scoring_weights 配置
        scoring_cfg = {}
        if hasattr(self.config, 'weight_4h_direction'):
            scoring_cfg["RSI_MRV_suite"] = {
                "weight_4h_direction": self.config.weight_4h_direction,
                "weight_1h_direction": self.config.weight_1h_direction,
                "weight_rsi_4h": 0.10,
                "weight_rsi_1h": 0.20,
                "weight_rsi_divergence": 0.05,
                "weight_boll_rsi_resonance": self.resolve_boll_rsi_resonance_weight(),
                "weight_vwap": self.config.weight_vwap,
                "weight_volume": self.config.weight_volume,
            }
            scoring_cfg["BOLL_MBV_suite"] = {
                "weight_4h_direction": self.config.weight_4h_direction,
                "weight_1h_direction": self.config.weight_1h_direction,
                "weight_boll_position": self.config.weight_boll_position if self.config.weight_boll_position > 0 else 0.25,
                "weight_boll_bandwidth": 0.05,
                "weight_boll_rsi_resonance": self.resolve_boll_rsi_resonance_weight(),
                "weight_vwap": self.config.weight_vwap,
                "weight_volume": self.config.weight_volume,
            }
        # RSI gate 配置
        rsi_gate_cfg = {
            "enabled": self.config.rsi_gate_enabled,
            "long_rsi_4h_min": self.config.rsi_gate_long_rsi_4h_min,
            "long_rsi_1h_min": self.config.rsi_gate_long_rsi_1h_min,
            "long_rsi_1h_max": self.config.rsi_gate_long_rsi_1h_max,
            "long_rsi_15m_min": self.config.rsi_gate_long_rsi_15m_min,
            "short_rsi_4h_max": self.config.rsi_gate_short_rsi_4h_max,
            "short_rsi_1h_max": self.config.rsi_gate_short_rsi_1h_max,
            "short_rsi_1h_min": self.config.rsi_gate_short_rsi_1h_min,
            "short_rsi_15m_max": self.config.rsi_gate_short_rsi_15m_max,
        }
        rsi_flip_cfg = {
            "flip_bullish_rsi_4h_min": self.config.rsi_flip_bullish_rsi_4h_min,
            "flip_bullish_rsi_1h_min": self.config.rsi_flip_bullish_rsi_1h_min,
            "flip_bearish_rsi_4h_max": self.config.rsi_flip_bearish_rsi_4h_max,
            "flip_bearish_rsi_1h_max": self.config.rsi_flip_bearish_rsi_1h_max,
        }
        full_config = {
            "scoring_weights": scoring_cfg,
            "rsi_config": {
                **rsi_cfg,
                "rsi_gate": rsi_gate_cfg,
                "rsi_flip_override": rsi_flip_cfg,
            },
        }

        rsi_indicator = RSIIndicator(rsi_cfg)
        regime_classifier = MarketRegimeClassifier(suite_cfg)
        rsi_mrv_analyzer = RSIMRVAnalyzer(full_config, rsi_indicator)
        boll_mbv_analyzer = BOLLMBVAnalyzer(full_config)

        self._suite_selector = AdaptiveSuiteSelector(
            config=full_config,
            rsi_indicator=rsi_indicator,
            regime_classifier=regime_classifier,
            rsi_mrv_analyzer=rsi_mrv_analyzer,
            boll_mbv_analyzer=boll_mbv_analyzer,
        )

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
    def _classify_market_quadrant(macd_line_4h: float, close_price: float, bb_middle_1h: float) -> str:
        if not math.isfinite(float(macd_line_4h or 0.0)):
            return "unknown"
        if bb_middle_1h <= 0:
            return "unknown"
        if macd_line_4h > 0 and close_price >= bb_middle_1h:
            return "I"
        if macd_line_4h > 0 and close_price < bb_middle_1h:
            return "II"
        if macd_line_4h < 0 and close_price < bb_middle_1h:
            return "III"
        if macd_line_4h < 0 and close_price >= bb_middle_1h:
            return "IV"
        return "unknown"

    def _calc_boll_position_score(
        self,
        *,
        close_price: float,
        bb_upper: float,
        bb_lower: float,
        bb_middle: float,
        direction: str,
    ) -> float:
        if bb_upper <= bb_lower or bb_middle <= 0 or close_price <= 0:
            return 0.0
        ratio = (close_price - bb_lower) / max(bb_upper - bb_lower, 1e-12)
        ratio = self._clamp(ratio, 0.0, 1.0)
        if str(direction).lower() == "long":
            if ratio < 0.50:
                return 0.0
            if ratio <= 0.55:
                return float(self.config.weight_boll_position)
            if ratio <= 0.80:
                return float(self.config.weight_boll_position) * 0.80
            if ratio <= 0.90:
                return float(self.config.weight_boll_position) * 0.60
            return float(self.config.weight_boll_position) * 0.40
        if str(direction).lower() == "short":
            mirrored = 1.0 - ratio
            if mirrored < 0.50:
                return 0.0
            if mirrored <= 0.55:
                return float(self.config.weight_boll_position)
            if mirrored <= 0.80:
                return float(self.config.weight_boll_position) * 0.80
            if mirrored <= 0.90:
                return float(self.config.weight_boll_position) * 0.60
            return float(self.config.weight_boll_position) * 0.40
        return 0.0

    def _init_resonance_components(self):
        """
        延迟初始化三共振组件 (BOLLStructureAnalyzer + RSIAnalyzer + ResonanceScorer)
        替代 VWAP 过滤器，在首次使用时初始化
        """
        if self._boll_structure_analyzer is not None:
            return  # 已初始化

        from src.indicators.boll_structure_analyzer import BOLLStructureAnalyzer
        from src.indicators.rsi_analyzer import RSIAnalyzer
        from src.fund_flow.resonance_scorer import ResonanceScorer

        # BOLL 结构分析器配置
        boll_cfg = {
            "period": 20,
            "std_dev": 2.0,
            "min_structure_score_for_entry": 0.55,
            "hard_block_threshold": 0.20,
            "bandwidth": {
                "squeeze_threshold": 0.025,
                "tight_threshold": 0.045,
                "normal_threshold": 0.080,
                "wide_threshold": 0.120,
                "position_mult_by_state": {
                    "squeeze": 0.00,
                    "tight": 0.70,
                    "normal": 1.00,
                    "wide": 0.90,
                    "expanding": 0.70,
                },
            },
            "mid_slope_bonus": {
                "strong_aligned": 0.05,
                "weak_aligned": 0.02,
                "neutral": 0.00,
                "opposed": -0.10,
            },
        }

        # RSI 分析器配置
        rsi_cfg = {
            "period_15m": 7,
            "period_1h": self.config.rsi_period,
            "period_4h": 21,
            "overbought": 70,
            "oversold": 30,
            "extreme_overbought": 78,
            "extreme_oversold": 22,
            "gate": {
                "long_rsi_4h_min": 43,
                "long_rsi_1h_min": 43,
                "long_rsi_1h_max": 70,
                "long_rsi_15m_min": 47,
                "long_rsi_15m_slope_positive": True,
                "short_rsi_4h_max": 57,
                "short_rsi_1h_max": 57,
                "short_rsi_1h_min": 30,
                "short_rsi_15m_max": 53,
                "short_rsi_15m_slope_negative": True,
            },
            "flip_override": {
                "flip_bullish_rsi_1h_min": 40,
                "flip_bullish_rsi_4h_min": 40,
                "flip_bearish_rsi_1h_max": 60,
                "flip_bearish_rsi_4h_max": 60,
            },
            "hard_block": {
                "long_rsi_1h_max": 70,
                "short_rsi_1h_min": 30,
            },
            "divergence": {
                "enabled": True,
                "lookback_bars": 10,
                "min_price_diff_pct": 0.004,
                "min_rsi_diff": 3.0,
                "regular_bullish_bonus": 0.12,
                "regular_bearish_bonus": 0.12,
                "hidden_bullish_bonus": 0.08,
                "hidden_bearish_bonus": 0.08,
            },
        }

        # 三共振评分配置 (从实际配置读取)
        # 尝试从 multiple sources 读取配置
        resonance_scoring_cfg = None

        # 方法1: 从 filter_cfg 读取 (如果是从 build_macd_v2_config_from_runtime 传入)
        if hasattr(self.config, '_raw_config') and isinstance(self.config._raw_config, dict):
            resonance_scoring_cfg = (
                self.config._raw_config
                .get('fund_flow', {})
                .get('macd_mtf_strategy_v2', {})
                .get('entry_filters', {})
                .get('resonance_scoring', {})
            )

        # 方法2: 使用硬编码的默认配置作为后备
        if not resonance_scoring_cfg:
            resonance_scoring_cfg = {
                "enabled": True,
                "weights": {
                    "macd_base": 0.40,
                    "boll_structure": 0.35,
                    "rsi_momentum": 0.25,
                },
                "market_adaptive_weights": {
                    "trending": {
                        "macd_base": 0.50,
                        "boll_structure": 0.30,
                        "rsi_momentum": 0.20,
                    },
                    "ranging": {
                        "macd_base": 0.35,
                        "boll_structure": 0.40,
                        "rsi_momentum": 0.25,
                    },
                    "volatile": {
                        "macd_base": 0.45,
                        "boll_structure": 0.30,
                        "rsi_momentum": 0.25,
                    },
                },
                "min_resonance_score_for_entry": 0.55,
                "strong_resonance_threshold": 0.75,
                "entry_thresholds_by_signal_type": {
                    "flip_bullish": 0.60,
                    "flip_bearish": 0.65,
                    "green_bar_growing": 0.70,
                    "red_bar_growing": 0.70,
                    "green_bar_shrinking": 0.55,
                    "red_bar_shrinking": 0.55,
                },
            }

        # 包装成完整配置结构
        resonance_cfg = {
            "fund_flow": {
                "macd_mtf_strategy_v2": {
                    "entry_filters": {
                        "resonance_scoring": resonance_scoring_cfg,
                    },
                },
            },
        }

        try:
            logger.info("初始化三共振组件...")
            self._boll_structure_analyzer = BOLLStructureAnalyzer(boll_cfg)
            logger.info(f"BOLLStructureAnalyzer initialized: {self._boll_structure_analyzer is not None}")
            self._rsi_analyzer = RSIAnalyzer({"rsi_config": rsi_cfg})
            logger.info(f"RSIAnalyzer initialized: {self._rsi_analyzer is not None}")
            self._resonance_scorer = ResonanceScorer(resonance_cfg)
            logger.info(f"ResonanceScorer initialized: {self._resonance_scorer is not None}")
            logger.info("三共振组件初始化成功!")
        except Exception as e:
            logger.warning(f"三共振组件初始化失败，将使用降级模式: {e}")
            import traceback
            logger.warning(traceback.format_exc())
            self._boll_structure_analyzer = None
            self._rsi_analyzer = None
            self._resonance_scorer = None

    def _calc_boll_rsi_resonance_score(
        self,
        *,
        close_price: float,
        bb_upper_1h: float,
        bb_lower_1h: float,
        bb_middle_1h: float,
        bb_upper_4h: float = 0.0,
        bb_lower_4h: float = 0.0,
        bb_middle_4h: float = 0.0,
        rsi_1h: float = 50.0,
        rsi_4h: float = 50.0,
        bb_middle_slope_1h: float = 0.0,
        weight: float = 0.20,
        direction: str = "",
        signal_type_1h: str = "",
    ) -> tuple:
        """
        BOLL+RSI 共振评分（替换原VWAP评分位）

        基于 RSI＋布林带期市共振战法：
        - 多头共振: 价格站上中轨(中轨走平/向上) + RSI从30以下回升至30以上 → 1.0
        - 空头共振: 价格跌破中轨(中轨走平/向下) + RSI从70以上回落至70以下 → 1.0
        - 超卖反弹: 价格触及下轨 + RSI<30 → 0.85
        - 超买回调: 价格触及上轨 + RSI>70 → 0.85
        - 趋势加速: 价格沿上/下轨运行 + RSI在50以上(多)/以下(空)持续 → 1.0
        - 无效信号: BOLL严重缩口 + RSI在40-60 → 0.0 (直接否决)
        - 部分共振: 仅满足BOLL或RSI之一 → 0.5

        返回: (score: float, resonance_type: str, detail: dict)
        """
        if bb_upper_1h <= bb_lower_1h or bb_middle_1h <= 0 or close_price <= 0:
            return 0.0, "no_data", {"boll_rsi_resonance": "no_boll_data"}

        band_width_1h = bb_upper_1h - bb_lower_1h
        relative_pos_1h = (close_price - bb_lower_1h) / max(band_width_1h, 1e-12)
        relative_pos_1h = self._clamp(relative_pos_1h, -0.2, 1.2)  # 允许带外一点

        # BOLL带宽状态
        bandwidth_pct = band_width_1h / bb_middle_1h if bb_middle_1h > 0 else 0.05
        is_squeeze = bandwidth_pct < 0.02  # 严重缩口

        # 中轨方向判断
        mid_slope_bullish = bb_middle_slope_1h >= 0  # 中轨走平或向上
        mid_slope_bearish = bb_middle_slope_1h <= 0  # 中轨走平或向下

        # 4H BOLL位置
        above_mid_4h = bb_middle_4h > 0 and close_price >= bb_middle_4h
        below_mid_4h = bb_middle_4h > 0 and close_price < bb_middle_4h

        detail = {
            "relative_pos_1h": round(relative_pos_1h, 3),
            "bandwidth_pct": round(bandwidth_pct, 4),
            "is_squeeze": is_squeeze,
            "rsi_1h": round(rsi_1h, 1),
            "rsi_4h": round(rsi_4h, 1),
            "mid_slope_bullish": mid_slope_bullish,
            "mid_slope_bearish": mid_slope_bearish,
        }

        # 无效信号：BOLL严重缩口 + RSI在40-60波动 → 直接否决
        if is_squeeze and 40 <= rsi_1h <= 60:
            detail["resonance"] = "invalid_squeeze_neutral_rsi"
            return 0.0, "invalid", detail

        direction_lower = str(direction).lower()
        raw_score = 0.0
        resonance_type = "none"

        if direction_lower == "long":
            # 多头共振：价格站上中轨 + 中轨走平/向上 + RSI从低位回升至30以上
            above_mid_1h = close_price >= bb_middle_1h
            rsi_recovering = rsi_1h >= 30  # RSI从30以下回升

            # 触及下轨（超卖区）
            near_lower = relative_pos_1h < 0.15
            # 沿上轨运行（趋势加速）
            along_upper = relative_pos_1h > 0.75

            if above_mid_1h and mid_slope_bullish and rsi_recovering and rsi_1h <= 70:
                # 完美多头共振
                raw_score = 1.0
                resonance_type = "bullish_resonance"
            elif near_lower and rsi_1h < 30:
                # 超卖反弹
                raw_score = 0.85
                resonance_type = "oversold_bounce"
            elif along_upper and rsi_1h > 50 and mid_slope_bullish:
                # 趋势加速
                raw_score = 1.0
                resonance_type = "trend_acceleration_long"
            elif above_mid_1h and rsi_recovering:
                # 部分共振（价格站上中轨但中轨未走平/向上）
                raw_score = 0.45
                resonance_type = "partial_bullish_resonance"
            elif near_lower and rsi_1h <= 40:
                # 接近下轨+RSI偏低，轻仓关注
                raw_score = 0.3
                resonance_type = "near_lower_low_rsi"
            elif above_mid_1h and rsi_1h > 50:
                # 价格在中轨上方+RSI偏多
                raw_score = 0.35
                resonance_type = "above_mid_rsi_ok"
            elif rsi_1h > 70:
                # RSI超买，减分
                raw_score = 0.05
                resonance_type = "overbought_caution"
            elif above_mid_1h:
                # 仅价格站上中轨
                raw_score = 0.15
                resonance_type = "above_mid_only"
            else:
                raw_score = 0.0
                resonance_type = "weak_long_signal"

        elif direction_lower == "short":
            # 空头共振：价格跌破中轨 + 中轨走平/向下 + RSI从高位回落至70以下
            below_mid_1h = close_price < bb_middle_1h
            rsi_declining = rsi_1h <= 70  # RSI从70以上回落

            # 触及上轨（超买区）
            near_upper = relative_pos_1h > 0.85
            # 沿下轨运行（趋势加速）
            along_lower = relative_pos_1h < 0.25

            if below_mid_1h and mid_slope_bearish and rsi_declining and rsi_1h >= 30:
                # 完美空头共振
                raw_score = 1.0
                resonance_type = "bearish_resonance"
            elif near_upper and rsi_1h > 70:
                # 超买回调
                raw_score = 0.85
                resonance_type = "overbought_pullback"
            elif along_lower and rsi_1h < 50 and mid_slope_bearish:
                # 趋势加速
                raw_score = 1.0
                resonance_type = "trend_acceleration_short"
            elif below_mid_1h and rsi_declining:
                # 部分共振
                raw_score = 0.45
                resonance_type = "partial_bearish_resonance"
            elif near_upper and rsi_1h >= 60:
                # 接近上轨+RSI偏高
                raw_score = 0.3
                resonance_type = "near_upper_high_rsi"
            elif below_mid_1h and rsi_1h < 50:
                # 价格在中轨下方+RSI偏空
                raw_score = 0.35
                resonance_type = "below_mid_rsi_ok"
            elif rsi_1h < 30:
                # RSI超卖，减分（做空风险）
                raw_score = 0.05
                resonance_type = "oversold_caution"
            elif below_mid_1h:
                raw_score = 0.15
                resonance_type = "below_mid_only"
            else:
                raw_score = 0.0
                resonance_type = "weak_short_signal"

        # 4H BOLL位置加成/减分
        if direction_lower == "long" and above_mid_4h:
            raw_score = min(1.0, raw_score * 1.1)  # 4H也在中轨上方加分
        elif direction_lower == "long" and below_mid_4h:
            raw_score *= 0.85  # 4H在中轨下方减分
        elif direction_lower == "short" and below_mid_4h:
            raw_score = min(1.0, raw_score * 1.1)  # 4H也在中轨下方加分
        elif direction_lower == "short" and above_mid_4h:
            raw_score *= 0.85  # 4H在中轨上方减分

        # 4H RSI方向加成
        if direction_lower == "long" and rsi_4h > 50:
            raw_score = min(1.0, raw_score * 1.05)
        elif direction_lower == "short" and rsi_4h < 50:
            raw_score = min(1.0, raw_score * 1.05)

        final_score = self._clamp(raw_score, 0.0, 1.0)
        detail["resonance"] = resonance_type
        detail["raw_score"] = round(raw_score, 3)
        detail["final_score"] = round(final_score, 3)

        return min(weight * final_score, weight), resonance_type, detail

    @staticmethod
    def _check_flip_bullish_bottom_structure(
        *,
        macd_line_current: float,
        macd_line_series: Optional[np.ndarray],
    ) -> Tuple[bool, int]:
        if macd_line_series is None:
            return False, 0
        values = np.asarray(macd_line_series, dtype=float)
        if values.size < 5:
            return False, 0
        last_idx = values.size - 1
        while last_idx >= 0 and values[last_idx] >= 0:
            last_idx -= 1
        if last_idx < 1:
            return False, 0
        bars = 0
        for idx in range(last_idx, 0, -1):
            if values[idx] >= 0 or values[idx - 1] >= 0:
                break
            if abs(values[idx]) < abs(values[idx - 1]):
                bars += 1
            else:
                break
        return bars >= 2, bars

    def _build_q4_preflip_rejection_detail(
        self,
        *,
        reason: str,
        market_quadrant: str,
        signal_type_1h: Optional[str],
        macd_line_4h: float,
        macd_4h_shrink_pct: float,
        macd_hist_4h: Optional[float],
        macd_hist_4h_prev: Optional[float],
        close_price: float,
        bb_middle_1h: float,
        rsi_1h: float,
        rsi_4h: float,
        atr_pct_1h: float,
        symbol: Optional[str],
        symbol_gate_pass: bool,
        symbol_gate_reason: str,
    ) -> Dict[str, Any]:
        shrink_threshold = float(self.config.q4_rsi_lead_preflip_min_4h_shrink_pct)
        rsi_4h_floor = float(self.config.q4_rsi_lead_preflip_rsi_4h_min) - float(
            self.config.q4_rsi_lead_preflip_rsi_4h_near_buffer
        )
        hist_current = float(macd_hist_4h or 0.0)
        hist_prev = float(macd_hist_4h_prev) if macd_hist_4h_prev is not None else 0.0
        hist_positive = hist_current >= 0.0
        hist_narrowing = hist_positive
        if not hist_positive:
            if macd_hist_4h_prev is not None:
                hist_narrowing = hist_current > hist_prev
            else:
                hist_narrowing = float(macd_4h_shrink_pct or 0.0) >= shrink_threshold

        return asdict(
            Q4PreflipRejectionDetail(
                passed=False,
                reason=str(reason or ""),
                symbol=self._normalize_symbol(symbol),
                quadrant=str(market_quadrant or ""),
                signal_type_1h=str(signal_type_1h or ""),
                symbol_gate_pass=bool(symbol_gate_pass),
                symbol_gate_reason=str(symbol_gate_reason or ""),
                macd_line_4h=float(macd_line_4h or 0.0),
                macd_4h_shrink_pct=float(macd_4h_shrink_pct or 0.0),
                shrink_threshold=shrink_threshold,
                hist_4h_current=hist_current,
                hist_4h_prev=hist_prev,
                hist_narrowing=bool(hist_narrowing),
                close_price=float(close_price or 0.0),
                bb_middle_1h=float(bb_middle_1h or 0.0),
                rsi_1h=float(rsi_1h or 0.0),
                rsi_1h_min=float(self.config.q4_rsi_lead_preflip_rsi_1h_min),
                rsi_4h=float(rsi_4h or 0.0),
                rsi_4h_floor=rsi_4h_floor,
                atr_pct_1h=float(atr_pct_1h or 0.0),
            )
        )

    def _set_q4_preflip_rejection(
        self,
        result: Dict[str, Any],
        *,
        reason: str,
        market_quadrant: str,
        signal_type_1h: Optional[str],
        macd_line_4h: float,
        macd_4h_shrink_pct: float,
        macd_hist_4h: Optional[float],
        macd_hist_4h_prev: Optional[float],
        close_price: float,
        bb_middle_1h: float,
        rsi_1h: float,
        rsi_4h: float,
        atr_pct_1h: float,
        symbol: Optional[str],
    ) -> Dict[str, Any]:
        result["q4_rsi_lead_preflip_reason"] = str(reason or "")
        result["q4_rejection_detail"] = self._build_q4_preflip_rejection_detail(
            reason=reason,
            market_quadrant=market_quadrant,
            signal_type_1h=signal_type_1h,
            macd_line_4h=macd_line_4h,
            macd_4h_shrink_pct=macd_4h_shrink_pct,
            macd_hist_4h=macd_hist_4h,
            macd_hist_4h_prev=macd_hist_4h_prev,
            close_price=close_price,
            bb_middle_1h=bb_middle_1h,
            rsi_1h=rsi_1h,
            rsi_4h=rsi_4h,
            atr_pct_1h=atr_pct_1h,
            symbol=symbol,
            symbol_gate_pass=bool(result.get("q4_rsi_lead_preflip_symbol_gate_pass", False)),
            symbol_gate_reason=str(result.get("q4_rsi_lead_preflip_symbol_gate_reason", "")),
        )
        return result

    def _evaluate_q4_rsi_lead_preflip_long(
        self,
        *,
        market_quadrant: str,
        trade_direction: Optional[str],
        signal_type_1h: Optional[str],
        macd_line_4h: float,
        macd_4h_shrink_pct: float,
        macd_hist_4h: Optional[float] = None,
        macd_hist_4h_prev: Optional[float] = None,
        close_price: float,
        bb_middle_1h: float,
        rsi_1h: float,
        rsi_4h: float,
        atr_pct_1h: float = 0.0,
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = {
            "q4_rsi_lead_preflip_active": False,
            "q4_rsi_lead_preflip_passed": False,
            "q4_rsi_lead_preflip_reason": "disabled",
            "q4_rejection_detail": None,
            "q4_rsi_lead_preflip_bonus_score": 0.0,
            "q4_rsi_lead_preflip_entry_scale": 1.0,
            "q4_rsi_lead_preflip_symbol": self._normalize_symbol(symbol),
            "q4_rsi_lead_preflip_allowed_symbols": self._q4_rsi_lead_preflip_allowed_symbols(),
            "q4_rsi_lead_preflip_allowed_categories": self._q4_rsi_lead_preflip_allowed_categories(),
            "q4_rsi_lead_preflip_symbol_gate_pass": False,
            "q4_rsi_lead_preflip_symbol_gate_reason": "disabled",
            "q4_rsi_lead_preflip_atr_pct_1h": float(atr_pct_1h or 0.0),
            "q4_rsi_lead_preflip_rsi_gate_exempted": False,
        }
        if not bool(self.config.enable_q4_rsi_lead_preflip_long):
            return result

        result["q4_rsi_lead_preflip_active"] = True
        result["q4_rsi_lead_preflip_entry_scale"] = float(self.config.q4_rsi_lead_preflip_entry_scale)
        symbol_allowed, gate_reason = self._check_q4_rsi_lead_preflip_symbol_allowed(symbol, atr_pct_1h=atr_pct_1h)
        if not symbol_allowed:
            result["q4_rsi_lead_preflip_symbol_gate_reason"] = gate_reason
            return self._set_q4_preflip_rejection(
                result,
                reason="symbol_not_allowed",
                market_quadrant=market_quadrant,
                signal_type_1h=signal_type_1h,
                macd_line_4h=macd_line_4h,
                macd_4h_shrink_pct=macd_4h_shrink_pct,
                macd_hist_4h=macd_hist_4h,
                macd_hist_4h_prev=macd_hist_4h_prev,
                close_price=close_price,
                bb_middle_1h=bb_middle_1h,
                rsi_1h=rsi_1h,
                rsi_4h=rsi_4h,
                atr_pct_1h=atr_pct_1h,
                symbol=symbol,
            )
        result["q4_rsi_lead_preflip_symbol_gate_pass"] = True
        result["q4_rsi_lead_preflip_symbol_gate_reason"] = gate_reason

        if str(signal_type_1h or "").strip().lower() != "red_bar_growing":
            return self._set_q4_preflip_rejection(
                result,
                reason="signal_type_not_red_bar_growing",
                market_quadrant=market_quadrant,
                signal_type_1h=signal_type_1h,
                macd_line_4h=macd_line_4h,
                macd_4h_shrink_pct=macd_4h_shrink_pct,
                macd_hist_4h=macd_hist_4h,
                macd_hist_4h_prev=macd_hist_4h_prev,
                close_price=close_price,
                bb_middle_1h=bb_middle_1h,
                rsi_1h=rsi_1h,
                rsi_4h=rsi_4h,
                atr_pct_1h=atr_pct_1h,
                symbol=symbol,
            )
        trade_direction_norm = str(trade_direction or "").strip().lower()
        if trade_direction_norm and trade_direction_norm != "long":
            return self._set_q4_preflip_rejection(
                result,
                reason="trade_direction_not_long",
                market_quadrant=market_quadrant,
                signal_type_1h=signal_type_1h,
                macd_line_4h=macd_line_4h,
                macd_4h_shrink_pct=macd_4h_shrink_pct,
                macd_hist_4h=macd_hist_4h,
                macd_hist_4h_prev=macd_hist_4h_prev,
                close_price=close_price,
                bb_middle_1h=bb_middle_1h,
                rsi_1h=rsi_1h,
                rsi_4h=rsi_4h,
                atr_pct_1h=atr_pct_1h,
                symbol=symbol,
            )
        if str(market_quadrant or "").strip().upper() != "IV":
            return self._set_q4_preflip_rejection(
                result,
                reason="quadrant_not_iv",
                market_quadrant=market_quadrant,
                signal_type_1h=signal_type_1h,
                macd_line_4h=macd_line_4h,
                macd_4h_shrink_pct=macd_4h_shrink_pct,
                macd_hist_4h=macd_hist_4h,
                macd_hist_4h_prev=macd_hist_4h_prev,
                close_price=close_price,
                bb_middle_1h=bb_middle_1h,
                rsi_1h=rsi_1h,
                rsi_4h=rsi_4h,
                atr_pct_1h=atr_pct_1h,
                symbol=symbol,
            )
        if float(macd_line_4h or 0.0) >= 0:
            return self._set_q4_preflip_rejection(
                result,
                reason="macd_line_4h_not_negative",
                market_quadrant=market_quadrant,
                signal_type_1h=signal_type_1h,
                macd_line_4h=macd_line_4h,
                macd_4h_shrink_pct=macd_4h_shrink_pct,
                macd_hist_4h=macd_hist_4h,
                macd_hist_4h_prev=macd_hist_4h_prev,
                close_price=close_price,
                bb_middle_1h=bb_middle_1h,
                rsi_1h=rsi_1h,
                rsi_4h=rsi_4h,
                atr_pct_1h=atr_pct_1h,
                symbol=symbol,
            )
        hist_positive = float(macd_hist_4h or 0.0) >= 0.0
        hist_narrowing = hist_positive
        if not hist_positive:
            if macd_hist_4h_prev is not None:
                hist_narrowing = float(macd_hist_4h or 0.0) > float(macd_hist_4h_prev or 0.0)
            else:
                hist_narrowing = (
                    float(macd_4h_shrink_pct or 0.0) >= float(self.config.q4_rsi_lead_preflip_min_4h_shrink_pct)
                )
        result["q4_rsi_lead_preflip_hist_4h_prev"] = float(macd_hist_4h_prev or 0.0)
        result["q4_rsi_lead_preflip_hist_4h_narrowing"] = bool(hist_narrowing)
        if not hist_narrowing:
            return self._set_q4_preflip_rejection(
                result,
                reason="macd_hist_not_narrowing",
                market_quadrant=market_quadrant,
                signal_type_1h=signal_type_1h,
                macd_line_4h=macd_line_4h,
                macd_4h_shrink_pct=macd_4h_shrink_pct,
                macd_hist_4h=macd_hist_4h,
                macd_hist_4h_prev=macd_hist_4h_prev,
                close_price=close_price,
                bb_middle_1h=bb_middle_1h,
                rsi_1h=rsi_1h,
                rsi_4h=rsi_4h,
                atr_pct_1h=atr_pct_1h,
                symbol=symbol,
            )
        if (
            not hist_positive
            and float(macd_4h_shrink_pct or 0.0) < float(self.config.q4_rsi_lead_preflip_min_4h_shrink_pct)
        ):
            return self._set_q4_preflip_rejection(
                result,
                reason="insufficient_4h_shrink",
                market_quadrant=market_quadrant,
                signal_type_1h=signal_type_1h,
                macd_line_4h=macd_line_4h,
                macd_4h_shrink_pct=macd_4h_shrink_pct,
                macd_hist_4h=macd_hist_4h,
                macd_hist_4h_prev=macd_hist_4h_prev,
                close_price=close_price,
                bb_middle_1h=bb_middle_1h,
                rsi_1h=rsi_1h,
                rsi_4h=rsi_4h,
                atr_pct_1h=atr_pct_1h,
                symbol=symbol,
            )
        if float(close_price or 0.0) < float(bb_middle_1h or 0.0):
            return self._set_q4_preflip_rejection(
                result,
                reason="price_below_1h_midline",
                market_quadrant=market_quadrant,
                signal_type_1h=signal_type_1h,
                macd_line_4h=macd_line_4h,
                macd_4h_shrink_pct=macd_4h_shrink_pct,
                macd_hist_4h=macd_hist_4h,
                macd_hist_4h_prev=macd_hist_4h_prev,
                close_price=close_price,
                bb_middle_1h=bb_middle_1h,
                rsi_1h=rsi_1h,
                rsi_4h=rsi_4h,
                atr_pct_1h=atr_pct_1h,
                symbol=symbol,
            )
        if float(rsi_1h or 0.0) < float(self.config.q4_rsi_lead_preflip_rsi_1h_min):
            return self._set_q4_preflip_rejection(
                result,
                reason="rsi_1h_below_min",
                market_quadrant=market_quadrant,
                signal_type_1h=signal_type_1h,
                macd_line_4h=macd_line_4h,
                macd_4h_shrink_pct=macd_4h_shrink_pct,
                macd_hist_4h=macd_hist_4h,
                macd_hist_4h_prev=macd_hist_4h_prev,
                close_price=close_price,
                bb_middle_1h=bb_middle_1h,
                rsi_1h=rsi_1h,
                rsi_4h=rsi_4h,
                atr_pct_1h=atr_pct_1h,
                symbol=symbol,
            )

        rsi_4h_floor = float(self.config.q4_rsi_lead_preflip_rsi_4h_min) - float(
            self.config.q4_rsi_lead_preflip_rsi_4h_near_buffer
        )
        if float(rsi_4h or 0.0) < rsi_4h_floor:
            return self._set_q4_preflip_rejection(
                result,
                reason="rsi_4h_not_near_50",
                market_quadrant=market_quadrant,
                signal_type_1h=signal_type_1h,
                macd_line_4h=macd_line_4h,
                macd_4h_shrink_pct=macd_4h_shrink_pct,
                macd_hist_4h=macd_hist_4h,
                macd_hist_4h_prev=macd_hist_4h_prev,
                close_price=close_price,
                bb_middle_1h=bb_middle_1h,
                rsi_1h=rsi_1h,
                rsi_4h=rsi_4h,
                atr_pct_1h=atr_pct_1h,
                symbol=symbol,
            )

        rsi_1h_component = self._clamp(
            (float(rsi_1h or 0.0) - float(self.config.q4_rsi_lead_preflip_rsi_1h_min)) / 10.0,
            0.0,
            1.0,
        )
        rsi_4h_component = self._clamp(
            (float(rsi_4h or 0.0) - rsi_4h_floor)
            / max(float(self.config.q4_rsi_lead_preflip_rsi_4h_near_buffer), 1.0),
            0.0,
            1.0,
        )
        # Q4 -> Q1 预翻转的专属共振分数：RSI 领先 + 重新站上 1H BOLL 中轨
        boll_reclaim_component = 1.0
        hist_strength_component = 1.0 if hist_positive else self._clamp(
            float(macd_4h_shrink_pct or 0.0) / max(float(self.config.q4_rsi_lead_preflip_min_4h_shrink_pct), 1e-6),
            0.0,
            1.0,
        )
        raw_score = self._clamp(
            0.40 * rsi_1h_component
            + 0.30 * rsi_4h_component
            + 0.30 * boll_reclaim_component,
            0.0,
            1.0,
        )
        dynamic_multiplier = self._clamp(
            0.45 * hist_strength_component
            + 0.35 * rsi_4h_component
            + 0.20 * rsi_1h_component,
            0.0,
            1.0,
        )
        bonus_score = min(
            float(self.config.q4_rsi_lead_preflip_bonus_score),
            raw_score * float(self.config.q4_rsi_lead_preflip_bonus_score) * dynamic_multiplier,
        )

        result.update(
            q4_rsi_lead_preflip_passed=True,
            q4_rsi_lead_preflip_reason="passed",
            q4_rsi_lead_preflip_bonus_score=bonus_score,
            q4_rsi_lead_preflip_raw_score=raw_score,
            q4_rsi_lead_preflip_dynamic_multiplier=dynamic_multiplier,
            q4_rsi_lead_preflip_boll_reclaim_score=boll_reclaim_component,
            q4_rsi_lead_preflip_rsi_1h=float(rsi_1h or 0.0),
            q4_rsi_lead_preflip_rsi_4h=float(rsi_4h or 0.0),
            q4_rsi_lead_preflip_macd_4h_shrink_pct=float(macd_4h_shrink_pct or 0.0),
            q4_rsi_lead_preflip_macd_line_4h=float(macd_line_4h or 0.0),
            q4_rsi_lead_preflip_macd_hist_4h=float(macd_hist_4h or 0.0),
            q4_rsi_lead_preflip_hist_4h_positive=hist_positive,
            q4_rsi_lead_preflip_hist_4h_narrowing=bool(hist_narrowing),
            q4_rsi_lead_preflip_bb_middle_1h=float(bb_middle_1h or 0.0),
        )
        return result

    @staticmethod
    def _resolve_vwap_execution_state(
        *,
        direction: str,
        close_price: float,
        vwap: float,
        max_gap: float = 0.01,
    ) -> str:
        if vwap <= 0 or close_price <= 0:
            return "unknown"
        gap = abs(close_price - vwap) / vwap
        if str(direction).lower() == "long":
            if close_price > vwap:
                return "favorable"
            return "discount_reclaim_ok" if gap < max_gap else "discount_reclaim_too_far"
        if str(direction).lower() == "short":
            if close_price < vwap:
                return "favorable"
            return "premium_reject_ok" if gap < max_gap else "premium_reject_too_far"
        return "unknown"

    @staticmethod
    def _resolve_entry_tier(
        *,
        market_quadrant: str,
        signal_type_1h: str,
        vwap_execution_state: str,
    ) -> str:
        quadrant = str(market_quadrant or "").strip().upper()
        signal_type = str(signal_type_1h or "").strip().lower()
        # VWAP 仅保留 telemetry，不再参与 entry tier 判定
        if quadrant in {"I", "III"} and signal_type in {"flip_bullish", "flip_bearish"}:
            return "tier1"
        if quadrant in {"II", "IV"} and signal_type in {"flip_bullish", "flip_bearish"}:
            return "tier2"
        if quadrant in {"I", "III"} and signal_type in {"red_bar_growing", "green_bar_growing"}:
            return "tier2"
        if quadrant in {"I", "III"} and signal_type in {"red_bar_shrinking", "green_bar_shrinking"}:
            return "tier2"
        return "blocked"

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

    def resolve_pocket_entry_requirements(
        self,
        *,
        signal_type_1h: Optional[str],
        vwap_state: Optional[str],
        is_trial_entry: bool,
        q4_rsi_lead_preflip_passed: bool = False,
        stable_continuation_side: Optional[str] = None,
    ) -> Dict[str, Any]:
        pocket_override = self.config.resolve_pocket_entry_override(signal_type_1h, vwap_state)
        threshold = self.config.resolve_signal_score_threshold(
            signal_type_1h,
            stable_continuation_side=stable_continuation_side,
        )
        if is_trial_entry:
            threshold = float(self.config.preflip_trial_min_signal_score)
        threshold = self._resolve_q4_preflip_signal_threshold(
            threshold,
            q4_rsi_lead_preflip_passed=q4_rsi_lead_preflip_passed,
        )

        effective = {
            "signal_score_threshold": float(pocket_override.get("min_signal_score", threshold)),
            "min_vwap_score_for_entry": 0.0,
            "min_entry_score": (
                float(pocket_override["min_entry_score"])
                if "min_entry_score" in pocket_override and pocket_override.get("min_entry_score") is not None
                else None
            ),
            "allow_neutral_1h_confirmation": bool(
                pocket_override.get("allow_neutral_1h_confirmation", self.config.allow_neutral_1h_confirmation)
            ),
            "require_strict_1h_confirmation": bool(pocket_override.get("require_strict_1h_confirmation", False)),
            "disallow_trial_entry": bool(pocket_override.get("disallow_trial_entry", False)),
            "disabled": bool(pocket_override.get("disabled", False)),
            "override_label": str(
                pocket_override.get(
                    "label",
                    self.config.normalize_pocket_key(signal_type_1h, vwap_state),
                )
            ),
            "raw_override": pocket_override,
        }
        return effective

    def _vwap_thresholds_disabled(self) -> bool:
        return True

    def _resolve_q4_preflip_signal_threshold(
        self,
        threshold: float,
        *,
        q4_rsi_lead_preflip_passed: bool,
    ) -> float:
        effective_threshold = float(threshold)
        q4_override = float(self.config.q4_rsi_lead_preflip_min_signal_score_override or 0.0)
        if q4_rsi_lead_preflip_passed and q4_override > 0.0:
            effective_threshold = min(effective_threshold, q4_override)
        return effective_threshold

    def resolve_boll_rsi_resonance_weight(self) -> float:
        return float(self.config.weight_boll_rsi_resonance or 0.0)

    def resolve_pocket_scoring_weights(
        self,
        *,
        signal_type_1h: Optional[str],
        vwap_state: Optional[str],
    ) -> Dict[str, Any]:
        pocket_override = self.config.resolve_pocket_scoring_override(signal_type_1h, vwap_state)
        effective = {
            "weight_1h_direction": float(
                pocket_override.get("weight_1h_direction", self.config.weight_1h_direction)
            ),
            "weight_4h_direction": float(
                pocket_override.get("weight_4h_direction", self.config.weight_4h_direction)
            ),
            "weight_4h_enhancement": float(
                pocket_override.get("weight_4h_enhancement", self.config.weight_4h_enhancement)
            ),
            "weight_boll_rsi_resonance": float(
                pocket_override.get(
                    "weight_boll_rsi_resonance",
                    self.resolve_boll_rsi_resonance_weight(),
                )
            ),
            "weight_vwap": 0.0,
            "weight_15m_entry": float(
                pocket_override.get("weight_15m_entry", self.config.weight_15m_entry)
            ),
            "weight_volume": float(
                pocket_override.get("weight_volume", self.config.weight_volume)
            ),
            "override_label": str(
                pocket_override.get(
                    "label",
                    self.config.normalize_pocket_key(signal_type_1h, vwap_state),
                )
            ),
            "raw_override": pocket_override,
        }
        return effective

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
        payload["pocket_entry_override_label"] = str(
            payload.get("pocket_entry_override_label")
            or payload.get("override_label")
            or ""
        )
        payload["signal_score_threshold_used"] = float(
            payload.get("signal_score_threshold_used", payload.get("signal_score_threshold", self.config.min_signal_score))
        )
        payload["min_vwap_score_used"] = float(
            payload.get(
                "min_vwap_score_used",
                payload.get("min_vwap_score_for_entry", self.config.min_vwap_score_for_entry),
            )
        )
        min_entry_score_used = payload.get("min_entry_score_used")
        if min_entry_score_used is None:
            min_entry_score_used = payload.get("pocket_min_entry_score")
        if min_entry_score_used is None:
            min_entry_score_used = payload.get("min_entry_score", self.config.min_entry_score)
        payload["min_entry_score_used"] = float(min_entry_score_used)
        payload["direction_lock_applied"] = bool(payload.get("direction_lock_applied", False))
        payload["entry_hard_filter_blocked"] = bool(payload.get("entry_hard_filter_blocked", False))
        entry_hard_filters = payload.get("entry_hard_filters", [])
        payload["entry_hard_filters"] = list(entry_hard_filters) if isinstance(entry_hard_filters, list) else []
        payload["regime_fallback_allowed"] = bool(payload.get("regime_fallback_allowed", False))
        payload["regime_fallback_score"] = float(payload.get("regime_fallback_score", 0.0) or 0.0)
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

    def _q4_rsi_lead_preflip_allowed_symbols(self) -> List[str]:
        return [
            self._normalize_symbol(item)
            for item in (self.config.q4_rsi_lead_preflip_allowed_symbols or [])
            if self._normalize_symbol(item)
        ]

    def _q4_rsi_lead_preflip_allowed_categories(self) -> List[str]:
        return [
            str(item or "").strip().lower()
            for item in (self.config.q4_rsi_lead_preflip_allowed_categories or [])
            if str(item or "").strip()
        ]

    def _is_major_large_cap_symbol(self, symbol: object) -> bool:
        symbol_up = self._normalize_symbol(symbol)
        if not symbol_up:
            return False
        return not self.is_watchlist_symbol(symbol_up)

    def _check_q4_rsi_lead_preflip_symbol_allowed(
        self,
        symbol: object,
        atr_pct_1h: float = 0.0,
    ) -> Tuple[bool, str]:
        symbol_up = self._normalize_symbol(symbol)
        if not symbol_up:
            return False, "missing_symbol"
        if bool(self.config.q4_rsi_lead_preflip_strict_eth_only):
            return (symbol_up == "ETHUSDT"), (
                "passed" if symbol_up == "ETHUSDT" else f"strict_eth_only: symbol={symbol_up}"
            )
        allowed_symbols = self._q4_rsi_lead_preflip_allowed_symbols()
        if allowed_symbols:
            return (symbol_up in allowed_symbols), (
                "passed" if symbol_up in allowed_symbols else f"symbol={symbol_up} not in whitelist={allowed_symbols}"
            )

        categories = self._q4_rsi_lead_preflip_allowed_categories()
        if categories:
            category_pass = False
            if "major_large_cap" in categories and self._is_major_large_cap_symbol(symbol_up):
                category_pass = True
            if not category_pass:
                return False, f"symbol={symbol_up} not in categories={categories}"

        atr_pct = max(0.0, float(atr_pct_1h or 0.0))
        min_atr = max(0.0, float(self.config.q4_rsi_lead_preflip_min_atr_pct_1h or 0.0))
        max_atr = max(min_atr, float(self.config.q4_rsi_lead_preflip_max_atr_pct_1h or 1.0))
        if atr_pct < min_atr or atr_pct > max_atr:
            return False, f"atr_pct_1h={atr_pct:.4f} outside [{min_atr:.4f}, {max_atr:.4f}]"

        return True, "passed"

    def is_q4_rsi_lead_preflip_symbol_allowed(self, symbol: object, atr_pct_1h: float = 0.0) -> bool:
        allowed, _reason = self._check_q4_rsi_lead_preflip_symbol_allowed(symbol, atr_pct_1h=atr_pct_1h)
        return allowed

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
        # VWAP 仓位乘数已退役，统一返回 1.0，避免遗留配置继续影响仓位。
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
        current_window = values[-period:]
        current = float(np.mean(current_window))
        prev_end = len(values) - lookback
        prev_start = max(0, prev_end - period)
        previous_window = values[prev_start:prev_end]
        if previous_window.size <= 0:
            return 0.0
        previous = float(np.mean(previous_window))
        return self._normalized_change(current, previous)

    @staticmethod
    def _latest_bollinger_values(
        prices: Optional[np.ndarray],
        period: int,
        std_dev: float,
    ) -> Tuple[float, float, float]:
        if prices is None:
            return 0.0, 0.0, 0.0
        values = np.asarray(prices, dtype=float)
        if values.size <= 0:
            return 0.0, 0.0, 0.0
        window = values[-min(period, values.size):]
        mid = float(np.mean(window))
        sigma = float(np.std(window))
        return mid, mid + sigma * std_dev, mid - sigma * std_dev

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
            min_vwap_score = 0.0 if self._vwap_thresholds_disabled() else float(self.config.stable_bear_continuation_min_vwap_score)
            min_adx_1h = float(self.config.stable_bear_continuation_min_adx_1h)
            min_4h_bars = max(1, int(self.config.stable_bear_continuation_min_4h_bars))
            hist_bars = int(stable_trend_context.get("negative_bars", 0) or 0)
            stable_active = bool(stable_trend_context.get("bear_active", False))
            allowed_states = {
                "short_retest_reject",
            }
            allowed_signal_types = {"flip_bearish"}
            allowed_entry_types = {"green_bar_growing"}
        else:
            enabled = bool(self.config.enable_stable_bull_continuation)
            min_vwap_score = 0.0 if self._vwap_thresholds_disabled() else float(self.config.stable_bull_continuation_min_vwap_score)
            min_adx_1h = float(self.config.stable_bull_continuation_min_adx_1h)
            min_4h_bars = max(1, int(self.config.stable_bull_continuation_min_4h_bars))
            hist_bars = int(stable_trend_context.get("positive_bars", 0) or 0)
            stable_active = bool(stable_trend_context.get("bull_active", False))
            allowed_states = {"long_reclaim_confirmed"}
            allowed_signal_types = {"flip_bullish"}
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
        min_vwap_score = 0.0 if self._vwap_thresholds_disabled() else float(self.config.trial_short_below_structure_promotion_min_vwap_score)
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
        mode = str(self.config.primary_direction_timeframe or "4h").strip().lower()
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
        mode = str(self.config.primary_direction_timeframe or "4h").strip().lower()
        return mode == "4h" and bool(self.config.light_1h_confirmation_when_4h_primary)

    def use_soft_15m_confirmation(self) -> bool:
        mode = str(self.config.primary_direction_timeframe or "4h").strip().lower()
        return mode == "4h" and bool(self.config.enable_soft_15m_confirmation_when_4h_primary)

    def soften_15m_entry_when_4h_primary(
        self,
        can_enter: bool,
        entry_score_15m: float,
        details_15m: Dict[str, Any],
        direction: str,
    ) -> Tuple[bool, float, Dict[str, Any]]:
        """4H 主周期下放宽 15m 准入，只保留弱逆势和近零轴容忍。"""
        if can_enter or not self.use_soft_15m_confirmation():
            return can_enter, entry_score_15m, details_15m

        details = dict(details_15m or {})
        hist_0 = float(details.get("hist_current", 0.0) or 0.0)
        hist_1 = float(details.get("hist_prev", hist_0) or hist_0)
        neutral_band = max(
            abs(self.config.macd_threshold) * max(float(self.config.soft_15m_neutral_hist_multiple), 1.0),
            1e-9,
        )
        max_adverse = neutral_band * max(float(self.config.soft_15m_max_adverse_hist_multiple), 1.0)
        soft_score = max(entry_score_15m, float(self.config.soft_15m_entry_score))

        if direction == "long":
            near_neutral = hist_0 >= -neutral_band
            recovering = hist_0 > hist_1 and hist_0 >= -max_adverse
            if near_neutral or recovering:
                details["soft_15m_confirmation"] = True
                details["soft_15m_confirmation_reason"] = "near_neutral" if near_neutral else "recovering"
                details["entry_type"] = "soft_long_neutral" if near_neutral else "soft_long_recovery"
                details["base_entry_score"] = soft_score
                return True, soft_score, details

        elif direction == "short":
            near_neutral = hist_0 <= neutral_band
            recovering = hist_0 < hist_1 and hist_0 <= max_adverse
            if near_neutral or recovering:
                details["soft_15m_confirmation"] = True
                details["soft_15m_confirmation_reason"] = "near_neutral" if near_neutral else "recovering"
                details["entry_type"] = "soft_short_neutral" if near_neutral else "soft_short_recovery"
                details["base_entry_score"] = soft_score
                return True, soft_score, details

        return can_enter, entry_score_15m, details
    
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
        deviation = (price - vwap) / vwap if vwap > 0 else 0.0
        return 0.0, VetoType.NONE, {
            "state": "vwap_disabled",
            "location_score": 0.0,
            "entry_edge": 0.0,
            "directional_extension": 0.0,
            "session_vwap": vwap,
            "structural_vwap": structural_vwap or 0.0,
            "session_deviation": deviation,
            "structural_deviation": 0.0,
        }

        # VWAP权重=0时直接跳过所有计算，避免不必要的副作用
        if self.config.weight_vwap <= 0:
            deviation = (price - vwap) / vwap if vwap > 0 else 0.0
            return 0.0, VetoType.NONE, {
                "state": "vwap_disabled",
                "location_score": 0.0,
                "entry_edge": 0.0,
                "directional_extension": 0.0,
                "session_vwap": vwap,
                "structural_vwap": structural_vwap or 0.0,
                "session_deviation": deviation,
                "structural_deviation": 0.0,
            }
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
        market_quadrant: str,
        vwap_state: str,
        vwap_execution_state: str,
        vwap_score: float,
        structural_vwap: float,
        session_deviation: float,
        structural_deviation: float,
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        if signal_type_1h != 'flip_bearish':
            return True, [], {}
        if self._vwap_thresholds_disabled():
            return True, [], {
                "vwap_thresholds_disabled": True,
                "market_quadrant": str(market_quadrant or "").strip().upper(),
                "vwap_state": vwap_state,
                "vwap_execution_state": vwap_execution_state,
                "vwap_score": vwap_score,
            }

        reasons: List[str] = []
        quadrant = str(market_quadrant or "").strip().upper()
        execution_state = str(vwap_execution_state or "").strip().lower()
        allowed_states = {"short_retest_reject"}
        if quadrant == "III":
            allowed_states.add("favorable")
        favorable_allowed = quadrant == "III" and execution_state == "favorable"
        if structural_vwap <= 0 and not favorable_allowed:
            reasons.append("structural_vwap_missing")
        if not favorable_allowed and vwap_state not in allowed_states:
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
            "market_quadrant": quadrant,
            "vwap_state": vwap_state,
            "vwap_execution_state": vwap_execution_state,
            "vwap_score": vwap_score,
            "flip_bearish_retest_reject_min_vwap_score": retest_reject_min_vwap_score,
            "structural_vwap": structural_vwap,
            "session_vwap_deviation": session_deviation,
            "structural_vwap_deviation": structural_deviation,
        }
        return len(reasons) == 0, reasons, details

    def check_flip_bullish_vwap_context(
        self,
        signal_type_1h: str,
        vwap_state: str,
        vwap_score: float,
        structural_vwap: float,
        session_deviation: float,
        structural_deviation: float,
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        if signal_type_1h != 'flip_bullish':
            return True, [], {}
        if self._vwap_thresholds_disabled():
            return True, [], {
                "vwap_thresholds_disabled": True,
                "vwap_state": vwap_state,
                "vwap_score": vwap_score,
                "structural_vwap": structural_vwap,
                "session_vwap_deviation": session_deviation,
                "structural_vwap_deviation": structural_deviation,
            }

        reasons: List[str] = []
        allowed_states = {"long_reclaim_confirmed"}
        if structural_vwap <= 0:
            reasons.append("structural_vwap_missing")
        if vwap_state not in allowed_states:
            reasons.append(f"vwap_state={vwap_state}")
        if vwap_score < self.config.flip_bullish_min_vwap_score:
            reasons.append(
                f"vwap_score={vwap_score:.2f}<{self.config.flip_bullish_min_vwap_score:.2f}"
            )

        details = {
            "flip_bullish_allowed_vwap_states": sorted(allowed_states),
            "vwap_state": vwap_state,
            "vwap_score": vwap_score,
            "flip_bullish_min_vwap_score": self.config.flip_bullish_min_vwap_score,
            "structural_vwap": structural_vwap,
            "session_vwap_deviation": session_deviation,
            "structural_vwap_deviation": structural_deviation,
        }
        return len(reasons) == 0, reasons, details

    def check_flip_bullish_trial_score_window(
        self,
        *,
        signal_type_1h: str,
        is_trial_entry: bool,
        signal_score: float,
    ) -> Tuple[bool, str]:
        if not self.config.flip_bullish_trial_score_window_enabled:
            return True, ""
        if signal_type_1h != "flip_bullish" or not is_trial_entry:
            return True, ""

        min_score = float(self.config.flip_bullish_trial_score_min)
        max_score = float(self.config.flip_bullish_trial_score_max)
        if min_score <= signal_score <= max_score:
            return True, ""
        return (
            False,
            f"flip_bullish_trial_score_window({signal_score:.3f} not in [{min_score:.3f},{max_score:.3f}])",
        )

    def check_green_bar_growing_score_window(
        self,
        *,
        signal_type_1h: str,
        signal_score: float,
    ) -> Tuple[bool, str]:
        if not self.config.green_bar_growing_score_window_enabled:
            return True, ""
        if signal_type_1h != "green_bar_growing":
            return True, ""

        min_score = float(self.config.green_bar_growing_score_min)
        max_score = float(self.config.green_bar_growing_score_max)
        if min_score <= signal_score <= max_score:
            return True, ""
        return (
            False,
            f"green_bar_growing_score_window({signal_score:.3f} not in [{min_score:.3f},{max_score:.3f}])",
        )

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
            if bb_lower_1h > 0:
                stop = bb_lower_1h - atr_1h * 0.2
                details['stop_anchor'] = 'bb_lower'
                if atr_1h > 0:
                    stop = bb_lower_1h - atr_1h * self.config.ema_stop_atr_multiplier
            else:
                stop = entry_price * (1 - self.config.max_stop_distance_pct)
                details['stop_anchor'] = 'default'
            
            max_stop = entry_price * (1 - self.config.max_stop_distance_pct)
            stop = max(stop, max_stop)
            
            if vwap > 0:
                vwap_alert = vwap * (1 - self.config.vwap_alert_deviation)
                details['vwap_alert_price'] = vwap_alert
            
        elif direction == 'short':
            if bb_upper_1h > 0:
                stop = bb_upper_1h + atr_1h * 0.2
                details['stop_anchor'] = 'bb_upper'
                if atr_1h > 0:
                    stop = bb_upper_1h + atr_1h * self.config.ema_stop_atr_multiplier
            else:
                stop = entry_price * (1 + self.config.max_stop_distance_pct)
                details['stop_anchor'] = 'default'
            
            max_stop = entry_price * (1 + self.config.max_stop_distance_pct)
            stop = min(stop, max_stop)
            
            if vwap > 0:
                vwap_alert = vwap * (1 + self.config.vwap_alert_deviation)
                details['vwap_alert_price'] = vwap_alert
        else:
            stop = entry_price * (1 - self.config.max_stop_distance_pct)
            details['stop_anchor'] = 'default'
        
        stop_pct = abs(entry_price - stop) / entry_price
        details['stop_price'] = stop
        details['stop_pct'] = stop_pct
        
        return stop, stop_pct, details
    
    # ==================== 综合分析 ====================
    
    def analyze_with_resonance(
        self,
        signal: MACDSignalV2,
        market_regime: str = "TRENDING_BEAR",
    ) -> MACDSignalV2:
        """
        三共振增强分析 (MACD + BOLL + RSI)
        
        在原有 analyze() 结果基础上，叠加三共振评分:
          1. BOLL 结构评分 (替代 VWAP)
          2. RSI 三时间框架门控
          3. 信号族白名单细化检查
          4. 三共振评分聚合
        
        如果三共振组件未初始化或评分失败，返回原始 signal (降级模式)
        """
        # 初始化三共振组件
        self._init_resonance_components()
        
        if self._resonance_scorer is None:
            return signal  # 降级: 三共振不可用

        direction = signal.direction
        if direction not in ("long", "short"):
            return signal  # neutral 信号不增强

        # 从 signal.details 中提取 BOLL 数据
        details = signal.details if isinstance(signal.details, dict) else {}
        debug_details = self._build_debug_details(**details)
        boll_upper_4h = float(details.get('bb_upper_4h', 0) or 0)
        boll_lower_4h = float(details.get('bb_lower_4h', 0) or 0)
        boll_mid_4h = float(details.get('bb_middle_4h', 0) or 0)
        close_price = float(details.get('close_price', 0) or 0)
        bb_bandwidth_4h = float(details.get('bb_bandwidth_4h', 0.05) or 0.05)

        # 检查 BOLL 数据是否完整
        if boll_upper_4h <= 0 or boll_lower_4h <= 0 or boll_mid_4h <= 0:
            # BOLL 数据不完整，跳过三共振增强
            return signal

        # BOLL 结构分析（使用已有数据计算真实评分）
        from src.indicators.boll_structure_analyzer import BOLLResult
        bandwidth = (boll_upper_4h - boll_lower_4h) / max(boll_mid_4h, 1e-12)
        relative_position = (close_price - boll_lower_4h) / max(boll_upper_4h - boll_lower_4h, 1e-12)
        relative_position = max(0.0, min(1.0, relative_position))

        # 区域定位（与 BOLLStructureAnalyzer.locate_zone 对齐）
        if close_price > boll_upper_4h:
            zone = 0  # 上轨外
        elif close_price >= boll_upper_4h:
            zone = 1  # 上轨区
        elif close_price >= boll_mid_4h:
            zone = 2  # 中上区
        elif close_price >= boll_lower_4h:
            zone = 4 if close_price < (boll_mid_4h + boll_lower_4h) / 2 else 3  # 中下区/中性区
        else:
            zone = 6  # 下轨外

        # 带宽状态分类（与 BOLLStructureAnalyzer.classify_bandwidth 对齐）
        if bandwidth < 0.025:
            bw_state = "squeeze"
        elif bandwidth < 0.045:
            bw_state = "tight"
        elif bandwidth < 0.080:
            bw_state = "normal"
        elif bandwidth < 0.120:
            bw_state = "wide"
        else:
            bw_state = "expanding"

        # 使用 BOLLStructureAnalyzer 计算真实 structure_score（如果可用）
        boll_structure_score = 0.5  # 后备默认值
        boll_gate_pass = True
        boll_gate_fail_reason = ""
        mid_slope_bonus = 0.0
        if self._boll_structure_analyzer is not None:
            try:
                # 使用 analyzer 的评分矩阵计算 structure_score
                if direction == "long":
                    zone_score = self._boll_structure_analyzer.long_zone_scores.get(zone, 0.0)
                elif direction == "short":
                    zone_score = self._boll_structure_analyzer.short_zone_scores.get(zone, 0.0)
                else:
                    zone_score = 0.0

                # 硬性反向禁止
                if zone_score < 0:
                    boll_structure_score = 0.0
                    boll_gate_pass = False
                    boll_gate_fail_reason = f"BOLL_ZONE_HARD_BLOCK: zone={zone}, dir={direction}"
                else:
                    # squeeze 禁止
                    if bw_state == "squeeze":
                        boll_structure_score = 0.0
                        boll_gate_pass = False
                        boll_gate_fail_reason = "BOLL_SQUEEZE"
                    else:
                        boll_structure_score = max(0.0, min(1.0, zone_score + mid_slope_bonus))
                        boll_gate_pass = boll_structure_score >= self._boll_structure_analyzer.min_score
                        if not boll_gate_pass:
                            boll_gate_fail_reason = f"BOLL_GATE_FAIL: {boll_structure_score:.3f} < {self._boll_structure_analyzer.min_score}"
            except Exception:
                pass  # 降级使用默认 0.5

        # 构建真实的 BOLLResult
        boll_result = BOLLResult(
            upper=boll_upper_4h,
            mid=boll_mid_4h,
            lower=boll_lower_4h,
            bandwidth=bandwidth,
            bandwidth_state=bw_state,
            relative_position=relative_position,
            zone=zone,
            structure_score=boll_structure_score,
            gate_pass=boll_gate_pass,
            gate_fail_reason=boll_gate_fail_reason,
            mid_slope=0,
            mid_slope_bonus=mid_slope_bonus,
        )

        # RSI 分析（使用已有数据计算真实评分）
        rsi_4h = signal.rsi_4h if hasattr(signal, 'rsi_4h') else 50.0
        rsi_1h = signal.rsi_val if hasattr(signal, 'rsi_val') else 50.0
        rsi_15m = signal.rsi_15m if hasattr(signal, 'rsi_15m') else 50.0

        # 构建真实的 RSIResult
        from src.indicators.rsi_analyzer import RSIResult
        rsi_gate_pass = True
        rsi_fail_reason = ""

        # 简化的 RSI 门控检查
        if direction == "long" and rsi_4h > 75:
            rsi_gate_pass = False
            rsi_fail_reason = "rsi_4h_overbought"
        elif direction == "short" and rsi_4h < 25:
            rsi_gate_pass = False
            rsi_fail_reason = "rsi_4h_oversold"

        # 使用 RSIAnalyzer 计算真实 momentum_score（如果可用）
        rsi_momentum_score = 0.5  # 后备默认值
        if self._rsi_analyzer is not None:
            try:
                score_1h = self._rsi_analyzer.score_rsi(rsi_1h, direction)
                score_4h = self._rsi_analyzer.score_rsi(rsi_4h, direction)
                rsi_momentum_score = score_1h * 0.70 + score_4h * 0.30
            except Exception:
                pass  # 降级使用默认 0.5

        rsi_result = RSIResult(
            rsi_4h=rsi_4h,
            rsi_1h=rsi_1h,
            rsi_15m=rsi_15m,
            rsi_slope_1h=signal.rsi_slope_1h if hasattr(signal, 'rsi_slope_1h') else 0.0,
            rsi_slope_15m=0.0,
            momentum_score=rsi_momentum_score,
            gate_pass=rsi_gate_pass,
            fail_reason=rsi_fail_reason,
            divergence_type="",
            divergence_bonus=0.0,
            rsi_recently_crossed_above_50=False,
            rsi_recently_crossed_below_50=False,
            extreme_status=None,
        )

        if not rsi_gate_pass:
            return self._neutral_signal(
                reason=f"rsi_gate_fail:{rsi_fail_reason}",
                veto_type=VetoType.RSI_GATE_FAIL,
            )

        # 信号族白名单检查
        from src.fund_flow.signal_whitelist import check_signal_family_whitelist
        # 将 BOLLResult 和 RSIResult 转换为字典
        boll_dict = {
            'upper': boll_result.upper,
            'mid': boll_result.mid,
            'lower': boll_result.lower,
            'bandwidth': boll_result.bandwidth,
            'bandwidth_state': boll_result.bandwidth_state,
            'relative_position': boll_result.relative_position,
            'zone': boll_result.zone,
            'structure_score': boll_result.structure_score,
            'gate_pass': boll_result.gate_pass,
        }
        rsi_dict = {
            'rsi_4h': rsi_result.rsi_4h,
            'rsi_1h': rsi_result.rsi_1h,
            'rsi_15m': rsi_result.rsi_15m,
            'rsi_slope_1h': rsi_result.rsi_slope_1h,
            'gate_pass': rsi_result.gate_pass,
        }
        q4_rsi_lead_preflip_passed = bool(details.get("q4_rsi_lead_preflip_passed", False))
        q4_whitelist_bypass = (
            bool(self.config.enable_q4_rsi_lead_preflip_long)
            and bool(q4_rsi_lead_preflip_passed)
            and str(signal.signal_type_1h or "").strip().lower() == "red_bar_growing"
        )
        whitelist_result = check_signal_family_whitelist(
            signal_type=signal.signal_type_1h or "",
            direction=direction,
            boll_result=boll_dict,
            rsi_result=rsi_dict,
            market_regime=market_regime,
        )
        debug_details = self._set_stage(
            debug_details,
            "signal_family_whitelist",
            signal_family_whitelist_allowed=bool(whitelist_result["allowed"]),
            signal_family_whitelist_reason=whitelist_result.get("reason"),
            q4_rsi_lead_preflip_whitelist_bypass=bool(q4_whitelist_bypass),
        )
        if not whitelist_result["allowed"] and not q4_whitelist_bypass:
            return self._neutral_signal(
                reason=f"whitelist_fail:{whitelist_result['reason']}",
                signal_type_1h=signal.signal_type_1h,
                details=self._build_debug_details(
                    **debug_details,
                ),
            )
        if q4_whitelist_bypass:
            details["q4_rsi_lead_preflip_whitelist_bypass"] = True

        # 专属共振门控（P0: 解锁信号族的精确保护，此处有完整 BOLL/RSI 数据）
        macd_hist_4h_val = float(details.get('macd_4h_hist_current', 0) or 0)
        signal_type = signal.signal_type_1h or ""

        if signal_type == "flip_bearish":
            guard_ok, guard_reason = self._check_flip_bearish_resonance_guards(
                boll_result=boll_result,
                rsi_result=rsi_result,
                macd_hist_4h_current=macd_hist_4h_val,
            )
            if not guard_ok:
                return self._neutral_signal(
                    reason=guard_reason,
                )

        if signal_type == "red_bar_growing" and direction == "long":
            guard_ok, guard_reason = self._check_red_bar_growing_long_guards(
                boll_result=boll_result,
                rsi_result=rsi_result,
                macd_hist_4h_current=macd_hist_4h_val,
            )
            if not guard_ok:
                if q4_whitelist_bypass:
                    details["q4_rsi_lead_preflip_guard_bypass"] = True
                else:
                    return self._neutral_signal(
                        reason=guard_reason,
                    )

        # 三共振评分
        resonance_result = self._resonance_scorer.compute(
            direction=direction,
            macd_base_score=signal.signal_score,
            boll_result=boll_result,
            rsi_result=rsi_result,
            market_regime=market_regime,
        )

        if not resonance_result["gate_pass"]:
            if q4_whitelist_bypass:
                details["q4_rsi_lead_preflip_resonance_bypass"] = True
                resonance_result = dict(resonance_result)
                resonance_result["gate_pass"] = True
                resonance_result["resonance_score"] = float(signal.signal_score)
            else:
                return self._neutral_signal(
                    reason=f"resonance_fail:{resonance_result['reason']}",
                )

        # 更新 signal 的评分和元数据
        resonance_score = resonance_result["resonance_score"]
        
        # 创建增强后的 signal (保留原始字段，更新评分)
        enhanced = copy.deepcopy(signal)
        enhanced.signal_score = resonance_score
        
        # 更新 details
        if enhanced.details is None:
            enhanced.details = {}
        enhanced.details.update({
            "resonance_score": resonance_score,
            "resonance_position_mult": resonance_result.get("position_mult", 1.0),
            "resonance_leverage": resonance_result.get("leverage", 4),
            "boll_zone": boll_result.zone,
            "boll_bw_state": boll_result.bandwidth_state,
            "boll_structure_score": boll_result.structure_score,
            "boll_bandwidth_position_mult": getattr(boll_result, 'boll_bandwidth_position_mult', 1.0),
            "rsi_momentum_score": rsi_result.momentum_score,
            "rsi_divergence_type": rsi_result.divergence_type,
            "rsi_extreme_status": getattr(rsi_result, 'extreme_status', None),
            "rsi_position_adjustment": getattr(rsi_result, 'rsi_position_adjustment', 0.0),
        })
        
        return enhanced

    def _check_flip_bearish_resonance_guards(
        self,
        boll_result: Optional[Any],
        rsi_result: Optional[Any],
        macd_hist_4h_current: float,
    ) -> Tuple[bool, Optional[str]]:
        """
        flip_bearish 解锁后的专属保护门控
        替代旧的 disable_flip_bearish_entries = True 的过度限制
        """
        guards = self.config.flip_bearish_resonance_guards or {}

        # BOLL 区域评分检查: 必须在中上区或上轨区（做空有利位置）
        min_boll_score = float(guards.get("min_boll_zone_score", 0.60))
        if boll_result is not None:
            boll_score = getattr(boll_result, "structure_score", None)
            if boll_score is None and isinstance(boll_result, dict):
                boll_score = boll_result.get("structure_score", 0)
            if boll_score is not None and float(boll_score) < min_boll_score:
                return False, f"FLIP_BEARISH_GUARD_BOLL: score={float(boll_score):.3f} < {min_boll_score}"

        # 4H MACD 柱方向检查
        if guards.get("require_4h_histogram_negative", True):
            if macd_hist_4h_current >= 0:
                return False, f"FLIP_BEARISH_GUARD_4H_HIST: histogram={macd_hist_4h_current:.6f} >= 0"

        # RSI 上限检查（不在超买区做空）
        rsi_1h_max = float(guards.get("rsi_1h_range_max", 60))
        if rsi_result is not None:
            rsi_1h = getattr(rsi_result, "rsi_1h", None)
            if rsi_1h is None and isinstance(rsi_result, dict):
                rsi_1h = rsi_result.get("rsi_1h", 50)
            if rsi_1h is not None and float(rsi_1h) > rsi_1h_max:
                return False, f"FLIP_BEARISH_GUARD_RSI: RSI_1h={float(rsi_1h):.1f} > {rsi_1h_max}"

        return True, None

    def _check_red_bar_growing_long_guards(
        self,
        boll_result: Optional[Any],
        rsi_result: Optional[Any],
        macd_hist_4h_current: float,
    ) -> Tuple[bool, Optional[str]]:
        """
        red_bar_growing 做多解锁后的专属保护门控
        """
        guards = self.config.red_bar_growing_long_guards or {}

        # BOLL 区域检查: 必须在中下区或下轨区（做多有利位置）
        min_boll_score = float(guards.get("min_boll_zone_score_long", 0.65))
        if boll_result is not None:
            boll_score = getattr(boll_result, "structure_score", None)
            if boll_score is None and isinstance(boll_result, dict):
                boll_score = boll_result.get("structure_score", 0)
            if boll_score is not None and float(boll_score) < min_boll_score:
                return False, f"RBG_LONG_GUARD_BOLL: score={float(boll_score):.3f} < {min_boll_score}"

        # 4H MACD 柱方向检查（做多需要 4H 柱为正）
        if guards.get("require_4h_histogram_positive", True):
            if macd_hist_4h_current <= 0:
                return False, f"RBG_LONG_GUARD_4H_HIST: histogram={macd_hist_4h_current:.6f} <= 0"

        # RSI 范围检查
        rsi_1h_min = float(guards.get("rsi_1h_range_min", 42))
        rsi_1h_max = float(guards.get("rsi_1h_range_max", 65))
        if rsi_result is not None:
            rsi_1h = getattr(rsi_result, "rsi_1h", None)
            if rsi_1h is None and isinstance(rsi_result, dict):
                rsi_1h = rsi_result.get("rsi_1h", 50)
            if rsi_1h is not None and not (rsi_1h_min <= float(rsi_1h) <= rsi_1h_max):
                return False, f"RBG_LONG_GUARD_RSI: RSI_1h={float(rsi_1h):.1f} not in [{rsi_1h_min},{rsi_1h_max}]"

        return True, None

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
        macd_line_1h: Optional[float] = None,
        macd_line_4h: Optional[float] = None,
        bb_middle_slope_1h: Optional[float] = None,
        bb_middle_slope_4h: Optional[float] = None,
        cvd_upper_wick_ratio: Optional[float] = None,
        cvd_1h_delta_ratio: Optional[float] = None,
        cvd_15m_delta_ratio: Optional[float] = None,
        # ATR
        atr_1h: float = 0.0,
        # 空头质量过滤参数（V3专家组建议）
        funding_rate: float = 0.0,
        oi_delta_ratio: float = 0.0,
        symbol: Optional[str] = None,
        # RSI数据
        rsi_val: float = 50.0,
        # RSI 多时间框架数据（双套件模式）
        rsi_4h: float = 50.0,
        rsi_15m: float = 50.0,
        rsi_slope_1h: float = 0.0,
    ) -> MACDSignalV2:
        """
        V2.0综合分析
        """
        debug_details = self._build_debug_details(
            strategy="macd_mtf_strategy_v2",
            stage="1h_direction",
            symbol=self._normalize_symbol(symbol),
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
        if cvd_15m_delta_ratio is not None:
            debug_details["cvd_15m_delta_ratio"] = float(cvd_15m_delta_ratio)
        if structural_vwap <= 0 and structural_vwap_1h_series is not None:
            structural_vwap = self._series_value(structural_vwap_1h_series, default=0.0)
        if bb_middle_1h <= 0 and close_1h_series is not None:
            bb_middle_1h, bb_upper_1h, bb_lower_1h = self._latest_bollinger_values(
                close_1h_series,
                period=self.config.boll_period,
                std_dev=self.config.boll_std_dev,
            )
        if bb_middle_4h <= 0 and close_4h_series is not None:
            bb_middle_4h, bb_upper_4h, bb_lower_4h = self._latest_bollinger_values(
                close_4h_series,
                period=self.config.boll_period,
                std_dev=self.config.boll_std_dev,
            )
        if macd_line_1h is not None and not math.isfinite(float(macd_line_1h)):
            macd_line_1h = None
        if macd_line_4h is not None and not math.isfinite(float(macd_line_4h)):
            macd_line_4h = None
        macd_line_series_1h: Optional[np.ndarray] = None
        if macd_line_1h is None and close_1h_series is not None:
            macd_line_series_1h, _signal_line_series_1h, _hist_series_1h = self.calculate_macd(
                np.asarray(close_1h_series, dtype=float),
                self.config.macd_1h_fast,
                self.config.macd_1h_slow,
                self.config.macd_1h_signal,
            )
            macd_line_1h = self._series_value(macd_line_series_1h, default=0.0)
        if macd_line_4h is None and close_4h_series is not None:
            macd_line_series_4h, _signal_line_series_4h, _hist_series_4h = self.calculate_macd(
                np.asarray(close_4h_series, dtype=float),
                self.config.macd_4h_fast,
                self.config.macd_4h_slow,
                self.config.macd_4h_signal,
            )
            macd_line_4h = self._series_value(macd_line_series_4h, default=0.0)
        market_quadrant = self._classify_market_quadrant(
            float(macd_line_4h or 0.0),
            float(close_price or 0.0),
            float(bb_middle_1h or 0.0),
        )
        macd_home_side = "long" if float(macd_line_4h or 0.0) > 0 else ("short" if float(macd_line_4h or 0.0) < 0 else "neutral")
        boll_value_zone = "strong" if float(close_price or 0.0) >= float(bb_middle_1h or 0.0) else "weak"
        debug_details.update(
            market_quadrant=market_quadrant,
            macd_home_side=macd_home_side,
            boll_value_zone=boll_value_zone,
            macd_line_1h=float(macd_line_1h or 0.0),
            macd_line_4h=float(macd_line_4h or 0.0),
        )

        primary_mode = str(self.config.primary_direction_timeframe or "4h").strip().lower()
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
        signal_type_1h = str(details_1h.get("signal_type") or "").strip().lower()
        q4_rsi_lead_preflip_eval = self._evaluate_q4_rsi_lead_preflip_long(
            market_quadrant=market_quadrant,
            trade_direction=trade_direction,
            signal_type_1h=signal_type_1h,
            symbol=symbol,
            macd_line_4h=float(macd_line_4h or 0.0),
            macd_4h_shrink_pct=float(shrink_4h_context["shrink_pct"]),
            macd_hist_4h=float(details_4h.get("hist_current", macd_hist_4h[idx_4h] if idx_4h < len(macd_hist_4h) else 0.0)),
            macd_hist_4h_prev=float(macd_hist_4h[idx_4h - 1]) if idx_4h > 0 and idx_4h - 1 < len(macd_hist_4h) else None,
            close_price=float(close_price or 0.0),
            bb_middle_1h=float(bb_middle_1h or 0.0),
            rsi_1h=float(rsi_val if rsi_val is not None else 50.0),
            rsi_4h=float(rsi_4h if rsi_4h is not None else 50.0),
            atr_pct_1h=float(atr_1h / close_price) if float(close_price or 0.0) > 0 else 0.0,
        )
        q4_rsi_lead_preflip_passed = bool(q4_rsi_lead_preflip_eval.get("q4_rsi_lead_preflip_passed", False))
        debug_details.update(**q4_rsi_lead_preflip_eval)
        state_machine_enabled = bool(
            self.config.require_macd_home_advantage
            or self.config.weight_boll_position > 0
            or self.config.disable_green_bar_shrinking_entries
        )
        if (
            signal_type_1h == "red_bar_shrinking"
            and bool(self.config.disable_red_bar_shrinking_entries)
        ):
            debug_details = self._set_stage(
                debug_details,
                "signal_type_family_filter",
                shrinking_state_filter_reason="red_bar_shrinking_family",
            )
            return self._neutral_signal(
                reason="red_bar_shrinking_disabled",
                signal_type_1h=details_1h.get("signal_type"),
                details=self._build_debug_details(
                    **debug_details,
                ),
            )
        if signal_type_1h == "green_bar_shrinking" and bool(self.config.disable_green_bar_shrinking_entries):
            debug_details = self._set_stage(
                debug_details,
                "signal_type_family_filter",
                shrinking_state_filter_reason="green_bar_shrinking_family",
            )
            return self._neutral_signal(
                reason="green_bar_shrinking_disabled",
                signal_type_1h=details_1h.get("signal_type"),
                details=self._build_debug_details(
                    **debug_details,
                ),
            )
        state_machine_reason = ""
        vwap_execution_state = "legacy"
        # 仅当显式启用 quadrant 严格门控时才重置 trade_direction
        # 原逻辑会将 weight_boll_position > 0 触发严格门控，导致所有非 flip 信号被阻止
        # 修复：quadrant 门控只在 require_macd_home_advantage=True 时启用
        use_quadrant_gating = bool(self.config.require_macd_home_advantage)
        if use_quadrant_gating:
            trade_direction = None
            if bool(self.config.require_macd_home_advantage) and macd_home_side == "neutral":
                state_machine_reason = "macd_home_advantage_block"
            elif market_quadrant == "I" and signal_type_1h == "flip_bullish":
                trade_direction = "long"
            elif market_quadrant == "II" and signal_type_1h == "flip_bullish":
                trade_direction = "long"
            elif market_quadrant == "III" and signal_type_1h == "flip_bearish":
                trade_direction = "short"
            elif market_quadrant == "IV" and signal_type_1h == "flip_bearish":
                trade_direction = "short"
            else:
                state_machine_reason = "quadrant_signal_block"
            if trade_direction == "long" and signal_type_1h == "flip_bullish":
                structure_ok, structure_bars = self._check_flip_bullish_bottom_structure(
                    macd_line_current=float(macd_line_1h or 0.0),
                    macd_line_series=macd_line_series_1h,
                )
                debug_details["flip_bullish_bottom_structure_bars"] = int(structure_bars)
                if not structure_ok:
                    trade_direction = None
                    state_machine_reason = "flip_bullish_bottom_structure_block"
            vwap_execution_state = self._resolve_vwap_execution_state(
                direction=trade_direction or "",
                close_price=float(close_price or 0.0),
                vwap=float(vwap or 0.0),
                max_gap=0.01,
            )
            debug_details["vwap_execution_state"] = vwap_execution_state
        if trade_direction is None and q4_rsi_lead_preflip_passed:
            trade_direction = "long"
            is_trial_entry = True
            entry_scale = min(entry_scale, float(q4_rsi_lead_preflip_eval.get("q4_rsi_lead_preflip_entry_scale", 1.0)))
            vwap_execution_state = self._resolve_vwap_execution_state(
                direction=trade_direction,
                close_price=float(close_price or 0.0),
                vwap=float(vwap or 0.0),
                max_gap=0.01,
            )
            debug_details = self._set_stage(
                debug_details,
                "q4_rsi_lead_preflip_direction_override",
                q4_rsi_lead_preflip_direction_override=True,
                trade_direction=trade_direction,
                is_trial_entry=is_trial_entry,
                entry_scale=entry_scale,
                vwap_execution_state=vwap_execution_state,
            )
            state_machine_reason = ""
        if trade_direction is None:
            return self._neutral_signal(
                reason=state_machine_reason or direction_reject_reason or "主方向无明确结论",
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
        
        # VWAP硬性否决 — 已禁用VWAP门槛，跳过hard block
        if False and vwap_veto == VetoType.VWAP_HARD_BLOCK:
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
                market_quadrant=market_quadrant,
                vwap_state=vwap_state,
                vwap_execution_state=vwap_execution_state,
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

        # ========== Step 3.5: RSI动态确认 ==========
        rsi_used = False
        boll_used = False
        rsi_veto = False
        rsi_score_penalty = 0.0

        # 当双套件模式启用时，RSI门控由套件选择器处理，此处跳过
        if not self.config.enable_dual_suite and trade_direction and rsi_val is not None:
            adx_strong = self.config.rsi_adx_threshold_strong
            adx_weak = self.config.rsi_adx_threshold_weak
            use_rsi_mode = adx_weak <= adx_1h < adx_strong  # 趋势回调区间
            use_boll_mode = adx_1h >= adx_strong             # 强趋势区间
            rsi_used = use_rsi_mode
            boll_used = use_boll_mode

            if use_rsi_mode:
                # RSI否决：多头时RSI超买、空头时RSI超卖
                if trade_direction == 'long' and rsi_val > self.config.rsi_overbought_threshold:
                    rsi_veto = True
                elif trade_direction == 'short' and rsi_val < self.config.rsi_oversold_threshold:
                    rsi_veto = True

                # RSI评分惩罚：对growing家族，RSI不配合时降分
                signal_type_name = details_1h.get('signal_type') or ''
                if self.config.rsi_require_ok_for_growing and 'growing' in signal_type_name:
                    if trade_direction == 'long' and rsi_val < 50:
                        rsi_score_penalty = 0.15
                    elif trade_direction == 'short' and rsi_val > 50:
                        rsi_score_penalty = 0.15

                # 对flip家族的可选确认
                if self.config.rsi_require_ok_for_flip and 'flip' in signal_type_name:
                    if trade_direction == 'long' and rsi_val < 40:
                        rsi_score_penalty = 0.10
                    elif trade_direction == 'short' and rsi_val > 60:
                        rsi_score_penalty = 0.10

        if rsi_veto:
            if q4_rsi_lead_preflip_passed and bool(self.config.rsi_gate_q4_exempt):
                q4_extreme_overbought = float(rsi_val if rsi_val is not None else 50.0) > float(
                    self.config.rsi_extreme_overbought
                )
                debug_details = self._set_stage(
                    debug_details,
                    "rsi_gate",
                    q4_rsi_gate_exempted=not q4_extreme_overbought,
                    q4_rsi_gate_extreme_overbought=q4_extreme_overbought,
                )
                debug_details["q4_rsi_lead_preflip_rsi_gate_exempted"] = not q4_extreme_overbought
                q4_rsi_lead_preflip_eval["q4_rsi_lead_preflip_rsi_gate_exempted"] = not q4_extreme_overbought
                if q4_extreme_overbought:
                    return self._neutral_signal(
                        reason='rsi_extreme_overbought_q4',
                        signal_type_1h=details_1h.get('signal_type'),
                        vwap_score=vwap_score,
                        vwap_deviation=vwap_deviation,
                        vwap_state=vwap_state,
                        vwap_location_score=vwap_location_score,
                        ema_multiplier=ema_multiplier,
                        ema_structure_status=ema_status,
                        details=self._build_debug_details(
                            rsi_val=rsi_val,
                            rsi_used=rsi_used,
                            boll_used=boll_used,
                            **debug_details,
                        ),
                    )
                rsi_veto = False
                rsi_score_penalty = 0.0
            else:
                return self._neutral_signal(
                    reason='rsi_veto',
                    signal_type_1h=details_1h.get('signal_type'),
                    vwap_score=vwap_score,
                    vwap_deviation=vwap_deviation,
                    vwap_state=vwap_state,
                    vwap_location_score=vwap_location_score,
                    ema_multiplier=ema_multiplier,
                    ema_structure_status=ema_status,
                    details=self._build_debug_details(
                        rsi_val=rsi_val,
                        rsi_used=rsi_used,
                        boll_used=boll_used,
                        **debug_details,
                    ),
                )

        debug_details.update(
            rsi_val=rsi_val,
            rsi_used=rsi_used,
            boll_used=boll_used,
            rsi_score_penalty=rsi_score_penalty,
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
        can_enter, entry_score_15m, details_15m = self.soften_15m_entry_when_4h_primary(
            can_enter=can_enter,
            entry_score_15m=entry_score_15m,
            details_15m=details_15m,
            direction=trade_direction,
        )
        entry_confirmation_passed = bool(can_enter)
        entry_type_15m = details_15m.get('entry_type', '')
        debug_details = self._set_stage(
            debug_details,
            "15m_entry",
            entry_type_15m=entry_type_15m,
            entry_score_15m=entry_score_15m,
            entry_confirmation_passed=entry_confirmation_passed,
            entry_confirmation_gate_enabled=bool(self.config.require_15m_confirmation_gate),
            entry_confirmation_gate_bypassed=bool((not entry_confirmation_passed) and (not self.config.require_15m_confirmation_gate)),
            entry_refine_15m=details_15m.get("ema_15m_refine"),
            macd_15m_hist_current=details_15m.get("hist_current"),
            macd_15m_hist_prev=details_15m.get("hist_prev"),
            bb_middle_15m=bb_middle_15m,
            bb_upper_15m=bb_upper_15m,
            bb_lower_15m=bb_lower_15m,
        )
        
        if not can_enter and self.config.require_15m_confirmation_gate:
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
        if q4_rsi_lead_preflip_passed:
            entry_scale = min(entry_scale, float(q4_rsi_lead_preflip_eval.get("q4_rsi_lead_preflip_entry_scale", 1.0)))
        if self.config.disable_green_bar_growing_entries and signal_type_1h == 'green_bar_growing':
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
        # 禁止green_bar_growing做空（MACD看涨信号做空风险高）
        if (
            self.config.disable_green_bar_growing_short_entries
            and signal_type_1h == 'green_bar_growing'
            and trade_direction == 'short'
        ):
            debug_details = self._set_stage(
                debug_details,
                "green_bar_growing_short_disabled",
                green_bar_growing_short_disabled=True,
            )
            return self._neutral_signal(
                reason='green_bar_growing_short_disabled',
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
            self.config.disable_red_bar_growing_long_entries
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
        # P0: red_bar_growing_long 已解锁，应用专属门控保护
        if (
            not self.config.disable_red_bar_growing_long_entries
            and signal_type_1h == 'red_bar_growing'
            and trade_direction == 'long'
        ):
            macd_hist_4h_val = float(details_4h.get("hist_current", macd_hist_4h[idx_4h] if idx_4h < len(macd_hist_4h) else 0))
            guard_ok, guard_reason = self._check_red_bar_growing_long_guards(
                boll_result=None,
                rsi_result=None,
                macd_hist_4h_current=macd_hist_4h_val,
            )
            if not guard_ok:
                if q4_rsi_lead_preflip_passed:
                    debug_details = self._set_stage(
                        debug_details,
                        "q4_rsi_lead_preflip_long",
                        q4_rsi_lead_preflip_guard_bypass=True,
                    )
                else:
                    debug_details = self._set_stage(
                        debug_details,
                        "red_bar_growing_long_guard",
                        rbg_long_guard_reason=guard_reason,
                    )
                    return self._neutral_signal(
                        reason=guard_reason,
                        signal_type_1h=signal_type_1h,
                        entry_type_15m=entry_type_15m,
                        entry_score_15m=entry_score_15m,
                        vwap_score=vwap_score,
                        vwap_deviation=vwap_deviation,
                        ema_multiplier=ema_multiplier,
                        ema_structure_status=ema_status,
                        enhancement_score=enhancement_score,
                        is_4h_enhanced=is_4h_enhanced,
                        entry_scale=entry_scale,
                        details=self._build_debug_details(
                            **debug_details,
                        ),
                    )
        if signal_type_1h == 'flip_bullish':
            bullish_vwap_ok, bullish_vwap_reasons, bullish_vwap_details = self.check_flip_bullish_vwap_context(
                signal_type_1h=signal_type_1h,
                vwap_state=vwap_state,
                vwap_score=vwap_score,
                structural_vwap=float(vwap_details.get("structural_vwap", structural_vwap)),
                session_deviation=float(vwap_details.get("session_deviation", vwap_deviation)),
                structural_deviation=structural_vwap_deviation,
            )
            if not bullish_vwap_ok:
                debug_details = self._set_stage(
                    debug_details,
                    "flip_bullish_vwap_context_filter",
                    flip_bullish_vwap_context_filter_reasons=bullish_vwap_reasons,
                    **bullish_vwap_details,
                )
                return self._neutral_signal(
                    reason=f'flip_bullish_vwap_context_filter({"; ".join(bullish_vwap_reasons)})',
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
        if signal_type_1h == 'flip_bearish':
            bearish_vwap_ok, bearish_vwap_reasons, bearish_vwap_details = self.check_flip_bearish_vwap_context(
                signal_type_1h=signal_type_1h,
                market_quadrant=market_quadrant,
                vwap_state=vwap_state,
                vwap_execution_state=vwap_execution_state,
                vwap_score=vwap_score,
                structural_vwap=float(vwap_details.get("structural_vwap", structural_vwap)),
                session_deviation=float(vwap_details.get("session_deviation", vwap_deviation)),
                structural_deviation=structural_vwap_deviation,
            )
            if not bearish_vwap_ok:
                debug_details = self._set_stage(
                    debug_details,
                    "flip_bearish_vwap_context_filter",
                    flip_bearish_vwap_context_filter_reasons=bearish_vwap_reasons,
                    **bearish_vwap_details,
                )
                return self._neutral_signal(
                    reason=f'flip_bearish_vwap_context_filter({"; ".join(bearish_vwap_reasons)})',
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
        debug_details = self._set_stage(
            debug_details,
            "15m_entry_score_gate",
            min_entry_score=self.config.min_entry_score,
            entry_score_gate_enabled=bool(self.config.require_15m_confirmation_gate),
            entry_score_gate_bypassed=bool(
                entry_score_15m < self.config.min_entry_score and not self.config.require_15m_confirmation_gate
            ),
        )

        if entry_score_15m < self.config.min_entry_score and self.config.require_15m_confirmation_gate:
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
            (strict_1h_filters_enabled or self.config.force_disable_flip_bullish_entries)
            and self.config.disable_flip_bullish_entries
            and signal_type_1h == 'flip_bullish'
            and (
                self.config.force_disable_flip_bullish_entries
                or not stable_continuation_active
            )
        ):
            debug_details = self._set_stage(
                debug_details,
                "flip_bullish_disabled",
                flip_bullish_disabled=True,
                flip_bullish_force_disabled=bool(self.config.force_disable_flip_bullish_entries),
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

        # flip_bearish 入场检查（P0: 已解锁，应用专属共振门控保护）
        if signal_type_1h == 'flip_bearish':
            if self.config.disable_flip_bearish_entries:
                debug_details = self._set_stage(
                    debug_details,
                    "flip_bearish_disabled",
                    flip_bearish_disabled=True,
                )
                return self._neutral_signal(
                    reason='flip_bearish_disabled',
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
            # flip_bearish 已解锁，执行专属共振门控
            macd_hist_4h_val = float(details_4h.get("hist_current", macd_hist_4h[idx_4h] if idx_4h < len(macd_hist_4h) else 0))
            guard_ok, guard_reason = self._check_flip_bearish_resonance_guards(
                boll_result=None,  # BOLL 在 analyze_with_resonance 层处理
                rsi_result=None,   # RSI 在 analyze_with_resonance 层处理
                macd_hist_4h_current=macd_hist_4h_val,
            )
            if not guard_ok:
                debug_details = self._set_stage(
                    debug_details,
                    "flip_bearish_guard",
                    flip_bearish_guard_reason=guard_reason,
                )
                return self._neutral_signal(
                    reason=guard_reason,
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
            trade_direction == "long"
            and str(self.config.long_entry_mode or "all").strip().lower() == "whitelist_only"
            and not self.config.is_long_entry_whitelisted(signal_type_1h, vwap_state)
        ):
            debug_details = self._set_stage(
                debug_details,
                "long_whitelist_blocked",
                long_entry_mode=self.config.long_entry_mode,
                long_whitelist_signal_types=list(self.config.long_whitelist_signal_types),
                long_whitelist_vwap_states=list(self.config.long_whitelist_vwap_states),
                long_whitelist_pockets=list(self.config.long_whitelist_pockets),
            )
            return self._neutral_signal(
                reason="long_whitelist_blocked",
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
            bullish_vwap_details: Dict[str, Any] = {}
            if self.config.flip_bullish_require_15m_growing and entry_type_15m != 'red_bar_growing':
                strict_filter_reasons.append(f"15m_entry={entry_type_15m or 'none'}")
            if self.config.flip_bullish_require_pullback_bounce and entry_refine_15m != 'pullback_bounce':
                strict_filter_reasons.append(f"15m_refine={entry_refine_15m or 'none'}")
            bullish_vwap_ok, bullish_vwap_reasons, bullish_vwap_details = self.check_flip_bullish_vwap_context(
                signal_type_1h=signal_type_1h,
                vwap_state=vwap_state,
                vwap_score=vwap_score,
                structural_vwap=float(vwap_details.get("structural_vwap", structural_vwap)),
                session_deviation=float(vwap_details.get("session_deviation", vwap_deviation)),
                structural_deviation=structural_vwap_deviation,
            )
            if not bullish_vwap_ok:
                strict_filter_reasons.extend(bullish_vwap_reasons)
            if strict_filter_reasons:
                debug_details = self._set_stage(
                    debug_details,
                    "flip_bullish_strict_filter",
                    flip_bullish_filter_reasons=strict_filter_reasons,
                    entry_refine_15m=entry_refine_15m,
                    **bullish_vwap_details,
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
        # P0: strict 模式下 rbg_long 也应用专属门控
        if (
            strict_1h_filters_enabled
            and not stable_continuation_active
            and not self.config.disable_red_bar_growing_long_entries
            and signal_type_1h == 'red_bar_growing'
            and trade_direction == 'long'
        ):
            macd_hist_4h_val = float(details_4h.get("hist_current", macd_hist_4h[idx_4h] if idx_4h < len(macd_hist_4h) else 0))
            guard_ok, guard_reason = self._check_red_bar_growing_long_guards(
                boll_result=None,
                rsi_result=None,
                macd_hist_4h_current=macd_hist_4h_val,
            )
            if not guard_ok:
                debug_details = self._set_stage(
                    debug_details,
                    "red_bar_growing_long_guard",
                    rbg_long_guard_reason=guard_reason,
                )
                return self._neutral_signal(
                    reason=guard_reason,
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

        pocket_entry_requirements = self.resolve_pocket_entry_requirements(
            signal_type_1h=signal_type_1h,
            vwap_state=vwap_state,
            is_trial_entry=is_trial_entry,
            q4_rsi_lead_preflip_passed=q4_rsi_lead_preflip_passed,
            stable_continuation_side=stable_continuation_side if stable_continuation_active else None,
        )
        pocket_scoring_weights = self.resolve_pocket_scoring_weights(
            signal_type_1h=signal_type_1h,
            vwap_state=vwap_state,
        )
        min_vwap_score_for_entry = 0.0
        vwap_soft_penalty = 0.0
        debug_details = self._set_stage(
            debug_details,
            "vwap_score_soft_penalty",
            vwap_thresholds_disabled=True,
            min_vwap_score_for_entry=min_vwap_score_for_entry,
            vwap_score=vwap_score,
            penalty_applied=False,
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
            debug_details = self._set_stage(
                debug_details,
                "red_bar_long_filter",
                red_bar_long_filter_reason="vwap_telemetry_only",
            )
        
        # ========== Step 6: 综合评分 ==========
        score = 0.0
        ema_score_multiplier = min(float(ema_multiplier), float(self.config.ema_multiplier_normal))
        weight_1h_direction = float(pocket_scoring_weights["weight_1h_direction"])
        weight_4h_direction = float(pocket_scoring_weights["weight_4h_direction"])
        weight_4h_enhancement = float(pocket_scoring_weights["weight_4h_enhancement"])
        weight_boll_position = float(self.config.weight_boll_position)
        weight_boll_rsi_resonance = float(
            pocket_scoring_weights.get("weight_boll_rsi_resonance", self.config.weight_boll_rsi_resonance)
        )
        weight_15m_entry = float(pocket_scoring_weights["weight_15m_entry"])
        weight_volume = float(pocket_scoring_weights["weight_volume"])
        
        signal_strength_1h = details_1h.get('signal_strength', 0.5)
        score_1h_base = 0.0
        score_1h = 0.0
        score_1h_source = "no_direction_credit"
        score_boll_position = 0.0
        score_boll_rsi_resonance = 0.0
        boll_rsi_resonance_type = "none"
        boll_rsi_resonance_type_sm = "none"
        boll_rsi_detail = {}
        boll_rsi_detail_sm = {}
        entry_tier = "blocked"

        neutral_1h_light_credit = (
            primary_mode == "4h"
            and direction_1h is None
            and bool(self.config.require_1h_confirmation_when_4h_primary)
            and bool(self.config.allow_neutral_1h_confirmation)
            and self.use_light_1h_confirmation()
            and debug_details.get("confirmation_status") == "neutral_allowed"
        )

        if not state_machine_enabled:
            if neutral_1h_light_credit:
                score_1h_base = weight_1h_direction * 0.75
                score_1h_source = "neutral_allowed_light_credit"
                debug_details["light_1h_neutral_credit_applied"] = True
            elif signal_type_1h in ['flip_bullish', 'flip_bearish']:
                score_1h_base = weight_1h_direction
                score_1h_source = "signal_type_flip"
            elif signal_type_1h in ['red_bar_growing', 'green_bar_growing']:
                score_1h_base = weight_1h_direction * 0.875
                score_1h_source = "signal_type_growing"
            elif trade_direction == direction_1h:
                score_1h_base = weight_1h_direction * signal_strength_1h
                score_1h_source = "aligned_direction_strength"
            else:
                score_1h_base = 0.0
                score_1h_source = "no_direction_credit"

            score_1h = min(score_1h_base * ema_score_multiplier, weight_1h_direction)
            debug_details["score_1h_source"] = score_1h_source
            score += score_1h

            if is_trial_entry:
                shrink_pct = max(0.0, float(shrink_4h_context["shrink_pct"]))
                preflip_4h_strength = self._clamp(0.56 + shrink_pct * 0.38, 0.0, 0.92)
                score_4h_base = weight_4h_direction * preflip_4h_strength
            elif direction_4h == trade_direction and signal_type_4h in ['flip_bullish', 'flip_bearish']:
                score_4h_base = weight_4h_direction
            elif direction_4h == trade_direction and signal_type_4h in ['red_bar_growing', 'green_bar_growing']:
                score_4h_base = weight_4h_direction * 0.875
            elif direction_4h == trade_direction:
                score_4h_base = weight_4h_direction * signal_strength_4h
            else:
                score_4h_base = 0.0

            score_4h = min(score_4h_base * ema_score_multiplier, weight_4h_direction)
            score += score_4h

            legacy_4h_boost = entry_type_15m not in ['flip_bullish', 'flip_bearish']
            effective_4h_score = enhancement_score
            if legacy_4h_boost:
                if is_4h_enhanced:
                    effective_4h_score = 0.84
                elif enhancement_score > 0:
                    effective_4h_score = 2.0 / 3.0

            if is_4h_enhanced:
                score_4h_enhancement_base = weight_4h_enhancement * effective_4h_score
            elif enhancement_score > 0:
                score_4h_enhancement_base = weight_4h_enhancement * 0.5 * effective_4h_score
            else:
                score_4h_enhancement_base = 0

            score_4h_enhancement = min(
                score_4h_enhancement_base * ema_score_multiplier,
                weight_4h_enhancement,
            )
            score += score_4h_enhancement

            # BOLL+RSI 共振评分（替换原VWAP评分）
            score_boll_rsi, boll_rsi_resonance_type, boll_rsi_detail = self._calc_boll_rsi_resonance_score(
                close_price=float(close_price or 0.0),
                bb_upper_1h=float(bb_upper_1h or 0.0),
                bb_lower_1h=float(bb_lower_1h or 0.0),
                bb_middle_1h=float(bb_middle_1h or 0.0),
                bb_upper_4h=float(bb_upper_4h or 0.0),
                bb_lower_4h=float(bb_lower_4h or 0.0),
                bb_middle_4h=float(bb_middle_4h or 0.0),
                rsi_1h=rsi_val if rsi_val is not None else 50.0,
                rsi_4h=rsi_4h,
                bb_middle_slope_1h=bb_middle_slope_1h,
                weight=weight_boll_rsi_resonance,
                direction=trade_direction,
                signal_type_1h=signal_type_1h or "",
            )
            score_boll_rsi_resonance = score_boll_rsi
            score += score_boll_rsi_resonance

            if entry_type_15m in ['flip_bullish', 'flip_bearish']:
                score_15m = weight_15m_entry
            elif entry_type_15m in ['red_bar_growing', 'green_bar_growing']:
                score_15m = weight_15m_entry * 0.85
            elif entry_type_15m in ['red_bar_stable', 'green_bar_stable']:
                score_15m = min(weight_15m_entry * 0.6 * 1.15, weight_15m_entry)
            else:
                score_15m = weight_15m_entry * 0.3
            score += score_15m
        else:
            if neutral_1h_light_credit:
                score_1h_base = weight_1h_direction * 0.50
                score_1h_source = "neutral_allowed_light_credit"
                debug_details["light_1h_neutral_credit_applied"] = True
            elif signal_type_1h in ['flip_bullish', 'flip_bearish']:
                score_1h_base = weight_1h_direction
                score_1h_source = "signal_type_flip"
            elif signal_type_1h in ['red_bar_growing', 'green_bar_growing']:
                score_1h_base = weight_1h_direction * 0.75
                score_1h_source = "signal_type_growing"
            elif trade_direction == direction_1h:
                score_1h_base = weight_1h_direction * signal_strength_1h
                score_1h_source = "aligned_direction_strength"

            score_1h = min(score_1h_base * ema_score_multiplier, weight_1h_direction)
            debug_details["score_1h_source"] = score_1h_source
            score += score_1h

            score_4h_base = weight_4h_direction if macd_home_side == trade_direction else 0.0
            score_4h = min(score_4h_base * ema_score_multiplier, weight_4h_direction)
            score += score_4h

            legacy_4h_boost = False
            effective_4h_score = 0.0
            score_4h_enhancement_base = 0.0
            score_4h_enhancement = 0.0
            score += score_4h_enhancement

            score_boll_position = self._calc_boll_position_score(
                close_price=float(close_price or 0.0),
                bb_upper=float(bb_upper_1h or 0.0),
                bb_lower=float(bb_lower_1h or 0.0),
                bb_middle=float(bb_middle_1h or 0.0),
                direction=trade_direction,
            )
            score += score_boll_position

            # BOLL+RSI 共振评分（替换原VWAP评分，状态机路径也使用）
            score_boll_rsi_sm, boll_rsi_resonance_type_sm, boll_rsi_detail_sm = self._calc_boll_rsi_resonance_score(
                close_price=float(close_price or 0.0),
                bb_upper_1h=float(bb_upper_1h or 0.0),
                bb_lower_1h=float(bb_lower_1h or 0.0),
                bb_middle_1h=float(bb_middle_1h or 0.0),
                bb_upper_4h=float(bb_upper_4h or 0.0),
                bb_lower_4h=float(bb_lower_4h or 0.0),
                bb_middle_4h=float(bb_middle_4h or 0.0),
                rsi_1h=rsi_val if rsi_val is not None else 50.0,
                rsi_4h=rsi_4h,
                bb_middle_slope_1h=bb_middle_slope_1h,
                weight=weight_boll_rsi_resonance,
                direction=trade_direction,
                signal_type_1h=signal_type_1h or "",
            )
            score_boll_rsi_resonance = score_boll_rsi_sm
            score += score_boll_rsi_resonance

            score_15m = 0.0
            score += score_15m

            entry_tier = self._resolve_entry_tier(
                market_quadrant=market_quadrant,
                signal_type_1h=signal_type_1h,
                vwap_execution_state=vwap_execution_state,
            )
        
        # 成交量评分 (15%)
        if volume_ratio > 1.5:
            score_vol = weight_volume
        elif volume_ratio > 1.0:
            score_vol = weight_volume * 0.67
        else:
            score_vol = weight_volume * 0.33
        score += score_vol

        overheat_penalty = 0.0
        confirmation_signal_type = signal_type_4h if light_1h_confirmation else signal_type_1h
        growing_signal = confirmation_signal_type in ['red_bar_growing', 'green_bar_growing']
        growing_entry = entry_type_15m in ['red_bar_growing', 'green_bar_growing']
        if (
            self.config.overheat_growing_penalty > 0
            and ema_multiplier >= self.config.overheat_ema_multiplier_threshold
            and (growing_signal or growing_entry)
        ):
            overheat_penalty = self.config.overheat_growing_penalty
            score = max(0.0, score - overheat_penalty)

        debug_details = self._set_stage(
            debug_details,
            "vwap_execution_penalty",
            vwap_execution_penalty_only=False,
            vwap_soft_penalty=vwap_soft_penalty,
            vwap_execution_state=vwap_execution_state,
        )

        entry_tier = self._resolve_entry_tier(
            market_quadrant=market_quadrant,
            signal_type_1h=signal_type_1h,
            vwap_execution_state=vwap_execution_state,
        )

        score_q4_rsi_lead_preflip = 0.0
        if q4_rsi_lead_preflip_passed:
            score_q4_rsi_lead_preflip = float(q4_rsi_lead_preflip_eval.get("q4_rsi_lead_preflip_bonus_score", 0.0))
            score += score_q4_rsi_lead_preflip

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
            score_boll_position=score_boll_position,
            score_boll_rsi_resonance=score_boll_rsi_resonance,
            score_vwap=score_boll_rsi_resonance,
            score_15m=score_15m,
            score_volume=score_vol,
            score_q4_rsi_lead_preflip=score_q4_rsi_lead_preflip,
            boll_rsi_resonance_type=boll_rsi_resonance_type if not state_machine_enabled else boll_rsi_resonance_type_sm,
            boll_rsi_detail=boll_rsi_detail if not state_machine_enabled else boll_rsi_detail_sm,
            pocket_scoring_override_label=pocket_scoring_weights["override_label"],
            pocket_scoring_override=pocket_scoring_weights["raw_override"],
            pocket_weight_1h_direction=weight_1h_direction,
            pocket_weight_4h_direction=weight_4h_direction,
            pocket_weight_4h_enhancement=weight_4h_enhancement,
            pocket_weight_boll_position=weight_boll_position,
            pocket_weight_boll_rsi_resonance=weight_boll_rsi_resonance,
            pocket_weight_vwap=self.config.weight_vwap,
            pocket_weight_15m_entry=weight_15m_entry,
            pocket_weight_volume=weight_volume,
            overheat_penalty=overheat_penalty,
            overheat_triggered=bool(overheat_penalty > 0),
            total_score=score,
            legacy_4h_boost=legacy_4h_boost,
            effective_4h_score=effective_4h_score,
            market_quadrant=market_quadrant,
            macd_home_side=macd_home_side,
            boll_value_zone=boll_value_zone,
            vwap_execution_state=vwap_execution_state,
            entry_tier=entry_tier,
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
        
        # ========== Step 7: 组合否决检查（改为软惩罚） ========== 
        # V6: 成交量 + VWAP 双低 — 已禁用VWAP，跳过此检查
        if False and score_vol < 0.05 and vwap_score <= 0.10:
            vwap_soft_penalty = max(vwap_soft_penalty, 0.25)
            debug_details = self._set_stage(
                debug_details,
                "volume_vwap_both_low_soft_penalty",
                score_vol=score_vol,
                vwap_score=vwap_score,
                penalty_applied=True,
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
        threshold = self._resolve_q4_preflip_signal_threshold(
            threshold,
            q4_rsi_lead_preflip_passed=q4_rsi_lead_preflip_passed,
        )
        debug_details = self._set_stage(
            debug_details,
            "threshold_check",
            signal_score_threshold=threshold,
            q4_rsi_lead_preflip_signal_threshold_cap=(
                float(self.config.q4_rsi_lead_preflip_min_signal_score_override)
                if q4_rsi_lead_preflip_passed and float(self.config.q4_rsi_lead_preflip_min_signal_score_override or 0.0) > 0.0
                else 0.0
            ),
        )
        trial_window_ok, trial_window_reason = self.check_flip_bullish_trial_score_window(
            signal_type_1h=signal_type_1h,
            is_trial_entry=is_trial_entry,
            signal_score=score,
        )
        if not trial_window_ok:
            return self._neutral_signal(
                reason=trial_window_reason,
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
                    flip_bullish_trial_score_window_reason=trial_window_reason,
                    flip_bullish_trial_score_min=float(self.config.flip_bullish_trial_score_min),
                    flip_bullish_trial_score_max=float(self.config.flip_bullish_trial_score_max),
                ),
            )
        green_window_ok, green_window_reason = self.check_green_bar_growing_score_window(
            signal_type_1h=signal_type_1h,
            signal_score=score,
        )
        if not green_window_ok:
            return self._neutral_signal(
                reason=green_window_reason,
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
                    green_bar_growing_score_window_reason=green_window_reason,
                    green_bar_growing_score_min=float(self.config.green_bar_growing_score_min),
                    green_bar_growing_score_max=float(self.config.green_bar_growing_score_max),
                ),
            )
        threshold = float(pocket_entry_requirements["signal_score_threshold"])
        debug_details = self._set_stage(
            debug_details,
            "pocket_entry_requirements",
            pocket_entry_override_label=pocket_entry_requirements["override_label"],
            pocket_entry_override=pocket_entry_requirements["raw_override"],
            signal_score_threshold=threshold,
            q4_rsi_lead_preflip_signal_threshold_cap=(
                float(self.config.q4_rsi_lead_preflip_min_signal_score_override)
                if q4_rsi_lead_preflip_passed and float(self.config.q4_rsi_lead_preflip_min_signal_score_override or 0.0) > 0.0
                else 0.0
            ),
            min_vwap_score_for_entry=min_vwap_score_for_entry,
            pocket_min_entry_score=(
                float(pocket_entry_requirements["min_entry_score"])
                if pocket_entry_requirements["min_entry_score"] is not None
                else None
            ),
            pocket_allow_neutral_1h_confirmation=bool(
                pocket_entry_requirements["allow_neutral_1h_confirmation"]
            ),
            pocket_require_strict_1h_confirmation=bool(
                pocket_entry_requirements["require_strict_1h_confirmation"]
            ),
            pocket_disallow_trial_entry=bool(pocket_entry_requirements["disallow_trial_entry"]),
            pocket_disabled=bool(pocket_entry_requirements["disabled"]),
        )
        if bool(pocket_entry_requirements["disabled"]):
            return self._neutral_signal(
                reason="pocket_entry_disabled",
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
                details=self._build_debug_details(**debug_details),
            )
        if (
            is_trial_entry
            and direction_1h is None
            and not bool(pocket_entry_requirements["allow_neutral_1h_confirmation"])
        ):
            return self._neutral_signal(
                reason="pocket_neutral_1h_confirmation_block",
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
                details=self._build_debug_details(**debug_details),
            )
        if (
            bool(pocket_entry_requirements["require_strict_1h_confirmation"])
            and direction_1h != trade_direction
        ):
            return self._neutral_signal(
                reason="pocket_strict_1h_confirmation_block",
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
                    pocket_confirmation_direction_1h=direction_1h,
                    pocket_confirmation_trade_direction=trade_direction,
                ),
            )
        if is_trial_entry and bool(pocket_entry_requirements["disallow_trial_entry"]):
            return self._neutral_signal(
                reason="pocket_trial_entry_disabled",
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
                details=self._build_debug_details(**debug_details),
            )
        if (
            pocket_entry_requirements["min_entry_score"] is not None
            and entry_score_15m < float(pocket_entry_requirements["min_entry_score"])
        ):
            return self._neutral_signal(
                reason="pocket_min_entry_score_block",
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
                details=self._build_debug_details(**debug_details),
            )
        if bool(pocket_entry_requirements["raw_override"].get("require_cvd_ok", False)):
            cvd_1h_delta_ratio_value = cvd_1h_delta_ratio if cvd_1h_delta_ratio is not None else 0.0
            if float(cvd_1h_delta_ratio_value) <= 0.0:
                return self._neutral_signal(
                    reason="pocket_cvd_confirmation_block",
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
                        pocket_cvd_1h_delta_ratio=float(cvd_1h_delta_ratio_value),
                    ),
                )
        if bool(pocket_entry_requirements["raw_override"].get("require_cvd_momentum_ok", False)):
            momentum_floor = float(self.config.soft_15m_entry_score)
            cvd_momentum_blocked = (
                cvd_15m_delta_ratio is not None and float(cvd_15m_delta_ratio) <= 0.0
            )
            if cvd_15m_delta_ratio is None:
                entry_floor = pocket_entry_requirements["min_entry_score"]
                if entry_floor is not None:
                    momentum_floor = max(float(entry_floor), momentum_floor)
                cvd_momentum_blocked = entry_score_15m < momentum_floor
            if cvd_momentum_blocked:
                return self._neutral_signal(
                    reason="pocket_cvd_momentum_block",
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
                        pocket_15m_momentum_floor=momentum_floor,
                        pocket_15m_entry_score=float(entry_score_15m),
                        pocket_cvd_15m_delta_ratio=(
                            float(cvd_15m_delta_ratio) if cvd_15m_delta_ratio is not None else None
                        ),
                    ),
                )
        # Pocket RSI确认
        if bool(pocket_entry_requirements["raw_override"].get("require_rsi_ok", False)):
            if trade_direction == 'long' and rsi_val < 50:
                return self._neutral_signal(
                    reason="pocket_rsi_block",
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
                        pocket_rsi_val=rsi_val,
                        pocket_rsi_direction=trade_direction,
                    ),
                )
            elif trade_direction == 'short' and rsi_val > 50:
                return self._neutral_signal(
                    reason="pocket_rsi_block",
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
                        pocket_rsi_val=rsi_val,
                        pocket_rsi_direction=trade_direction,
                    ),
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
        # 应用RSI评分惩罚
        score = max(0.0, score * (1.0 - rsi_score_penalty))

        # ── 双套件分析（Step 10.5）──
        suite_result_data = {}
        selected_suite = ""
        regime = ""
        suite_score = 0.0
        rsi_gate_pass = True
        divergence_data = {}

        # 强制初始化三共振组件 (不依赖 suite_selector)
        if self._resonance_scorer is None and self.config.enable_dual_suite:
            # 简化的三共振初始化,不依赖 suite_selector
            try:
                from src.indicators.boll_structure_analyzer import BOLLStructureAnalyzer
                from src.indicators.rsi_analyzer import RSIAnalyzer
                from src.fund_flow.resonance_scorer import ResonanceScorer

                # BOLL配置
                boll_cfg_simple = {
                    'period': self.config.boll_period,
                    'std_dev': self.config.boll_std_dev,
                }

                # RSI配置
                rsi_cfg_simple = {
                    'period_15m': 7,
                    'period_1h': self.config.rsi_period,
                    'period_4h': 21,
                }

                # 共振配置 (从 _raw_config 读取或使用默认值)
                if hasattr(self.config, '_raw_config') and self.config._raw_config:
                    resonance_cfg = self.config._raw_config
                    print(f"[DEBUG] Using _raw_config for resonance: {type(resonance_cfg)}")
                else:
                    resonance_cfg = {
                        'fund_flow': {
                            'macd_mtf_strategy_v2': {
                                'entry_filters': {
                                    'resonance_scoring': {
                                        'enabled': True,
                                        'weights': {
                                            'macd_base': 0.4,
                                            'boll_structure': 0.35,
                                            'rsi_momentum': 0.25,
                                        },
                                    },
                                },
                            },
                        },
                    }
                    print(f"[DEBUG] Using default resonance config")

                print(f"[DEBUG] Initializing BOLLStructureAnalyzer...")
                self._boll_structure_analyzer = BOLLStructureAnalyzer(boll_cfg_simple)
                print(f"[DEBUG] Initializing RSIAnalyzer...")
                self._rsi_analyzer = RSIAnalyzer({'rsi_config': rsi_cfg_simple})
                print(f"[DEBUG] Initializing ResonanceScorer...")
                self._resonance_scorer = ResonanceScorer(resonance_cfg)
                print(f"[DEBUG] ResonanceScorer initialized: {self._resonance_scorer is not None}")
                print(f"[DEBUG] Resonance weights: {self._resonance_scorer.weights}")
            except Exception as e:
                import traceback
                logger.warning(f"三共振组件初始化失败: {e}")
                logger.warning(traceback.format_exc())
                print(f"[ERROR] 三共振组件初始化失败: {e}")
                traceback.print_exc()
                self._resonance_scorer = None

        if self.config.enable_dual_suite:
            if self._suite_selector is None:
                self._init_suite_selector()

            if self._suite_selector is not None:
                # 构建 market_data
                boll_bandwidth_4h = (bb_upper_4h - bb_lower_4h) / bb_middle_4h if bb_middle_4h > 0 else 0.05
                boll_bandwidth_4h_mean = boll_bandwidth_4h  # 简化：使用当前值（理想情况传入历史均值）
                macd_hist_4h_val = float(macd_hist_4h[idx_4h]) if idx_4h < len(macd_hist_4h) else 0
                macd_hist_4h_prev = float(macd_hist_4h[idx_4h - 1]) if idx_4h > 0 and idx_4h - 1 < len(macd_hist_4h) else 0
                macd_hist_1h_val = float(macd_hist_1h[idx_1h]) if idx_1h < len(macd_hist_1h) else 0
                atr_pct_1h = atr_1h / close_price if close_price > 0 else 0.01

                market_data = {
                    "adx_1h": adx_1h,
                    "atr_pct_1h": atr_pct_1h,
                    "boll_upper_4h": bb_upper_4h,
                    "boll_lower_4h": bb_lower_4h,
                    "boll_mid_4h": bb_middle_4h,
                    "boll_bandwidth_4h": boll_bandwidth_4h,
                    "boll_bandwidth_4h_mean": boll_bandwidth_4h_mean,
                    "macd_histogram_4h": macd_hist_4h_val,
                    "macd_histogram_4h_prev": macd_hist_4h_prev,
                    "macd_histogram_1h": macd_hist_1h_val,
                    "vwap_score": vwap_score,
                    "volume_ratio": volume_ratio,
                    "current_price": close_price,
                    "rsi_1h": rsi_val,
                    "rsi_4h": rsi_4h,
                    "rsi_15m": rsi_15m,
                    "rsi_slope_1h": rsi_slope_1h,
                }

                suite_analysis = self._suite_selector.select_and_analyze(
                    symbol="",
                    direction=trade_direction,
                    signal_type_1h=signal_type_1h or "",
                    market_data=market_data,
                )

                selected_suite = suite_analysis.get("selected_suite", "")
                regime = suite_analysis.get("regime", "")
                suite_score = suite_analysis.get("entry_score", 0.0)
                block_reason = suite_analysis.get("block_reason")

                sr = suite_analysis.get("suite_result") or {}
                rsi_gate_pass = sr.get("rsi_gate_pass", True)
                divergence_data = sr.get("divergence", {})

                if not suite_analysis.get("final_gate_pass", False):
                    return self._neutral_signal(
                        reason=f"suite_gate:{block_reason}",
                        score=score,
                        signal_type_1h=signal_type_1h,
                        vwap_score=vwap_score,
                        vwap_deviation=vwap_deviation,
                        vwap_state=vwap_state,
                        ema_multiplier=ema_multiplier,
                        ema_structure_status=ema_status,
                        details=self._build_debug_details(
                            **debug_details,
                            selected_suite=selected_suite,
                            regime=regime,
                            suite_score=suite_score,
                        ),
                    )

                # 融合评分: MACD 基础分 × 0.7 + 套件分 × 0.3
                score = score * 0.7 + suite_score * 0.3

        final_score = min(score, 1.0)
        
        final_stage_path = self._normalize_stage_path(debug_details.get("stage_path"))
        final_stage_path = self._set_stage({"stage_path": final_stage_path}, "final")["stage_path"]
        self._last_analysis = {
            'strategy': 'macd_mtf_strategy_v2',
            'stage': 'final',
            'stage_path': final_stage_path,
            'stage_path_text': " > ".join(final_stage_path),
            'primary_timeframe': primary_mode,
            'symbol': self._normalize_symbol(symbol),
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
            'market_quadrant': market_quadrant,
            'macd_home_side': macd_home_side,
            'boll_value_zone': boll_value_zone,
            'vwap_execution_state': vwap_execution_state,
            'entry_tier': entry_tier,
            'enhancement_score': enhancement_score,
            'entry_type_15m': entry_type_15m,
            'entry_score_15m': entry_score_15m,
            'macd_4h_hist_current': details_4h.get("hist_current"),
            'macd_4h_hist_prev': details_4h.get("hist_prev"),
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
            'score_1h_source': score_1h_source,
            'score_4h_base': score_4h_base,
            'score_4h': score_4h,
            'score_4h_enhancement_base': score_4h_enhancement_base,
            'score_4h_enhancement': score_4h_enhancement,
            'score_boll_position': score_boll_position,
            'score_boll_rsi_resonance': score_boll_rsi_resonance,
            'score_vwap': score_boll_rsi_resonance,
            'score_15m': score_15m,
            'score_volume': score_vol,
            'score_q4_rsi_lead_preflip': score_q4_rsi_lead_preflip,
            **q4_rsi_lead_preflip_eval,
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

        # 将 BOLL 数据添加到 details 中供三共振分析使用
        self._last_analysis['close_price'] = close_price
        self._last_analysis['bb_middle_4h'] = bb_middle_4h
        self._last_analysis['bb_upper_4h'] = bb_upper_4h
        self._last_analysis['bb_lower_4h'] = bb_lower_4h

        # 构建基础信号
        base_signal = MACDSignalV2(
            direction=trade_direction,
            signal_score=max(0.0, final_score * (1.0 - vwap_soft_penalty)),
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
            selected_suite=selected_suite,
            regime=regime,
            suite_score=suite_score,
            rsi_gate_pass=rsi_gate_pass,
            rsi_4h=rsi_4h,
            rsi_15m=rsi_15m,
            rsi_slope_1h=rsi_slope_1h,
            divergence=divergence_data,
            details=self._last_analysis,
        )

        # 三共振增强处理（不再仅依赖 enable_dual_suite，通过 _init_resonance_components 延迟初始化）
        if trade_direction in ("long", "short"):
            self._init_resonance_components()
        if self._resonance_scorer is not None and trade_direction in ("long", "short"):
            try:
                market_regime_str = regime or "TRENDING_BEAR"
                enhanced_signal = self.analyze_with_resonance(base_signal, market_regime=market_regime_str)
                # 更新 details 中的共振信息
                if hasattr(enhanced_signal, 'details') and enhanced_signal.details:
                    self._last_analysis["resonance_applied"] = True
                    self._last_analysis["resonance_score"] = getattr(enhanced_signal, 'signal_score', 0.0)
                return enhanced_signal
            except Exception as e:
                logger.warning(f"三共振增强失败，返回基础信号: {e}")
                self._last_analysis["resonance_error"] = str(e)

        return base_signal

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
        entry_tier: Optional[str] = None,
    ) -> int:
        """
        根据评分计算杠杆（实盘配置：2X/3X/4X）
        
        Args:
            score: 信号评分
            ema_multiplier: BOLL结构修正系数，强趋势(1.2)时降杠杆
        
        Returns:
            杠杆倍数
        """
        tier = str(entry_tier or "").strip().lower()
        if tier == "tier1":
            base_leverage = 5
        elif tier == "tier2":
            base_leverage = 4
        elif tier == "tier3":
            base_leverage = 3
        else:
            base_leverage = 0
        raw_tiers = self.config.leverage_score_tiers or []
        if base_leverage <= 0 and raw_tiers:
            normalized_tiers: List[Dict[str, float]] = []
            for raw_tier in raw_tiers:
                if not isinstance(raw_tier, dict):
                    continue
                try:
                    tier_leverage = int(float(raw_tier.get("leverage", 0) or 0))
                    tier_min = float(raw_tier.get("score_min", 0.0) or 0.0)
                    tier_max_raw = raw_tier.get("score_max", None)
                    tier_max = float(tier_max_raw) if tier_max_raw is not None else 1.0
                except (TypeError, ValueError):
                    continue
                if tier_leverage <= 0 or tier_max < tier_min:
                    continue
                normalized_tiers.append(
                    {
                        "score_min": tier_min,
                        "score_max": tier_max,
                        "leverage": float(tier_leverage),
                    }
                )
            normalized_tiers.sort(
                key=lambda item: (item["score_min"], item["score_max"], item["leverage"]),
                reverse=True,
            )
            for tier in normalized_tiers:
                if float(score) >= float(tier["score_min"]) and float(score) <= float(tier["score_max"]):
                    base_leverage = int(tier["leverage"])
                    break
            if base_leverage <= 0:
                return 0
        elif base_leverage <= 0 and score >= 0.95:
            base_leverage = 5
        elif base_leverage <= 0 and score >= 0.90:
            base_leverage = 5
        elif base_leverage <= 0 and score >= 0.85:
            base_leverage = 5
        elif base_leverage <= 0 and score >= 0.75:
            base_leverage = 2
        elif base_leverage <= 0:
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
        """根据评分计算仓位乘数（高分信号仓位回落，避免过度集中）"""
        if score >= 0.95:
            return 1.2
        elif score >= 0.90:
            return 1.2
        elif score >= 0.85:
            return 1.0
        elif score >= 0.75:
            return 1.0
        return 0.0

    def describe_position_portion(
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
        entry_tier: Optional[str] = None,
    ) -> Dict[str, float | str | bool]:
        detail: Dict[str, float | str | bool] = {
            "score": float(score),
            "base_default_portion": float(base_default_portion),
            "base_max_symbol_position_portion": float(base_max_symbol_position_portion),
            "target_portion": 0.0,
            "max_symbol_position_portion": float(base_max_symbol_position_portion),
            "portion_multiplier": 0.0,
            "vwap_score_multiplier": 1.0,
            "entry_scale_applied": self._clamp(entry_scale, 0.05, 1.0) if is_trial_entry else 1.0,
            "session_scale_input": float(session_scale),
            "effective_session_scale": float(self.resolve_symbol_risk_session_scale(symbol, session_scale)),
            "pre_scale_portion": 0.0,
            "final_portion": 0.0,
            "blocked_reason": "",
            "matched_score_tier_min": 0.0,
            "matched_score_tier_target": 0.0,
            "lowest_score_tier_min": 0.0,
            "used_configured_score_tiers": False,
            "watchlist_cap_applied": False,
        }
        tier = str(entry_tier or "").strip().lower()
        if tier == "tier1":
            target_portion = 0.30
        elif tier == "tier2":
            target_portion = 0.25
        elif tier == "tier3":
            target_portion = 0.20
        else:
            target_portion = float(base_default_portion)
        raw_tiers = self.config.position_score_tiers or []
        if tier not in {"tier1", "tier2", "tier3"} and raw_tiers:
            detail["used_configured_score_tiers"] = True
            normalized_tiers: List[Dict[str, float]] = []
            for raw_tier in raw_tiers:
                if not isinstance(raw_tier, dict):
                    continue
                try:
                    tier_target = float(raw_tier.get("target_portion", 0.0) or 0.0)
                    tier_min = float(raw_tier.get("score_min", 0.0) or 0.0)
                    tier_max_raw = raw_tier.get("score_max", None)
                    tier_max = float(tier_max_raw) if tier_max_raw is not None else 1.0
                except (TypeError, ValueError):
                    continue
                if tier_target <= 0 or tier_max < tier_min:
                    continue
                normalized_tiers.append(
                    {
                        "score_min": tier_min,
                        "score_max": tier_max,
                        "target_portion": tier_target,
                    }
                )
            if normalized_tiers:
                detail["lowest_score_tier_min"] = float(
                    min(item["score_min"] for item in normalized_tiers)
                )
            normalized_tiers.sort(
                key=lambda item: (item["score_min"], item["score_max"], item["target_portion"]),
                reverse=True,
            )
            matched_target = None
            matched_score_min = None
            for tier in normalized_tiers:
                if float(score) >= float(tier["score_min"]) and float(score) <= float(tier["score_max"]):
                    matched_target = float(tier["target_portion"])
                    matched_score_min = float(tier["score_min"])
                    break
            if matched_target is None:
                detail["blocked_reason"] = "score_tier_unmatched"
                return detail
            target_portion = matched_target
            detail["matched_score_tier_min"] = float(matched_score_min or 0.0)
            detail["matched_score_tier_target"] = float(matched_target)
            portion_mult = 1.0
        elif tier not in {"tier1", "tier2", "tier3"}:
            portion_mult = self.calculate_portion_multiplier(score)
            if portion_mult <= 0:
                detail["blocked_reason"] = "portion_multiplier_nonpositive"
                return detail
        else:
            portion_mult = 1.0

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
            detail["watchlist_cap_applied"] = True

        portion = min(max_symbol_position_portion, target_portion * portion_mult)
        vwap_mult = self.resolve_vwap_score_position_multiplier(
            vwap_score=vwap_score,
            signal_type_1h=signal_type_1h,
            vwap_state=vwap_state,
        )
        portion *= vwap_mult
        portion = min(max_symbol_position_portion, portion)
        if is_trial_entry:
            portion *= float(detail["entry_scale_applied"])
        detail["target_portion"] = float(target_portion)
        detail["max_symbol_position_portion"] = float(max_symbol_position_portion)
        detail["portion_multiplier"] = float(portion_mult)
        detail["vwap_score_multiplier"] = float(vwap_mult)
        detail["pre_scale_portion"] = float(portion)
        portion *= float(detail["effective_session_scale"])
        detail["final_portion"] = float(portion)
        return detail

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
        entry_tier: Optional[str] = None,
    ) -> float:
        detail = self.describe_position_portion(
            score=score,
            base_default_portion=base_default_portion,
            base_max_symbol_position_portion=base_max_symbol_position_portion,
            symbol=symbol,
            signal_type_1h=signal_type_1h,
            vwap_score=vwap_score,
            vwap_state=vwap_state,
            bonus_multiplier=bonus_multiplier,
            is_trial_entry=is_trial_entry,
            entry_scale=entry_scale,
            session_scale=session_scale,
            entry_tier=entry_tier,
        )
        return float(detail["final_portion"])
