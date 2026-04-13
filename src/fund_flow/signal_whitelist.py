"""
信号族白名单检查
基于三共振分析结果对信号族进行精细化过滤
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# 信号族配置
SIGNAL_FAMILY_CONFIG = {
    "red_bar_growing": {
        "enabled": True,
        "direction": "short",
        "min_signal_score": 0.90,
        "boll_min_score": 0.60,
        "rsi_1h_max": 60,
        "rsi_1h_min": 28,
        "rsi_4h_max": 62,
        "preferred_regime": "TRENDING_BEAR",
        "leverage_min": 4,
        "notes": "核心做空族，扩宽 RSI 区间适配空头趋势市场"
    },
    "flip_bullish": {
        "enabled": True,
        "direction": "long",
        "min_signal_score": 0.80,
        "boll_min_score": 0.55,
        "rsi_1h_min": 38,
        "rsi_1h_max": 70,
        "rsi_4h_min": 40,
        "rsi_cross_above_50": True,
        "preferred_regime": "TRENDING_BULL",
        "leverage_min": 3
    },
    "flip_bearish": {
        "enabled": True,
        "direction": "short",
        "min_signal_score": 0.80,
        "boll_min_score": 0.55,
        "rsi_1h_max": 60,
        "rsi_1h_min": 30,
        "rsi_4h_max": 60,
        "rsi_cross_below_50": True,
        "preferred_regime": "TRENDING_BEAR",
        "leverage_min": 3
    },
    "green_bar_growing": {
        "enabled": True,
        "direction": "long",
        "min_signal_score": 0.92,
        "boll_min_score": 0.80,
        "boll_zone_required": [5, 6],
        "rsi_1h_max": 50,
        "rsi_1h_min": 25,
        "rsi_slope_positive_required": True,
        "rsi_divergence_bullish_preferred": True,
        "preferred_regime": "RANGING",
        "bandwidth_state_allowed": ["tight", "normal"],
        "leverage_max": 3,
        "notes": "仅允许 BOLL 下轨 + RSI 超卖反弹的精确组合，适度扩宽 RSI"
    },
    "red_bar_shrinking": {
        "enabled": False,
        "disable_reason": "confirmed_negative_alpha"
    },
    "green_bar_shrinking": {
        "enabled": False,
        "disable_reason": "confirmed_negative_alpha"
    }
}


def check_signal_family_whitelist(
    signal_type: str,
    direction: str,
    boll_result: dict,
    rsi_result: dict,
    market_regime: str = None,
) -> dict:
    """
    信号族白名单细化检查
    
    Args:
        signal_type: 信号类型
        direction: 方向 (long/short)
        boll_result: BOLL 分析结果字典
        rsi_result: RSI 分析结果字典
        market_regime: 市场状态（可选）
        
    Returns:
        {'allowed': bool, 'reason': str, 'regime_warning': str}
    """
    family_cfg = SIGNAL_FAMILY_CONFIG.get(signal_type)

    if family_cfg is None:
        return {"allowed": False, "reason": f"UNKNOWN_SIGNAL:{signal_type}"}

    if not family_cfg.get("enabled", False):
        return {
            "allowed": False,
            "reason": f"DISABLED:{signal_type}:{family_cfg.get('disable_reason')}"
        }

    # 检查信号方向匹配
    if family_cfg["direction"] != direction:
        return {
            "allowed": False,
            "reason": f"DIRECTION_MISMATCH:{signal_type} is {family_cfg['direction']}"
        }

    # BOLL 评分检查
    boll_score = boll_result.get("structure_score", 0)
    boll_min = family_cfg.get("boll_min_score", 0.55)
    
    if boll_score < boll_min:
        return {
            "allowed": False,
            "reason": f"BOLL_SCORE_LOW:{signal_type}: {boll_score:.3f} < {boll_min}"
        }

    # BOLL 区域检查（green_bar_growing 专用）
    required_zones = family_cfg.get("boll_zone_required")
    if required_zones is not None:
        current_zone = boll_result.get("zone")
        if current_zone not in required_zones:
            return {
                "allowed": False,
                "reason": f"BOLL_ZONE_REQUIRED:{signal_type}: zone={current_zone}, required={required_zones}"
            }

    # RSI 检查
    rsi_1h = rsi_result.get("rsi_1h", 50)
    rsi_1h_max = family_cfg.get("rsi_1h_max", 70)
    rsi_1h_min = family_cfg.get("rsi_1h_min", 30)

    if not (rsi_1h_min <= rsi_1h <= rsi_1h_max):
        return {
            "allowed": False,
            "reason": f"RSI_1H_OUT_OF_RANGE:{signal_type}: {rsi_1h:.1f} not in [{rsi_1h_min},{rsi_1h_max}]"
        }

    # RSI 斜率检查（green_bar_growing）
    if family_cfg.get("rsi_slope_positive_required"):
        if rsi_result.get("rsi_slope_1h", 0) <= 0:
            return {
                "allowed": False,
                "reason": f"RSI_SLOPE_NOT_POSITIVE:{signal_type}"
            }

    # RSI 穿越 50 检查（flip 族）
    if family_cfg.get("rsi_cross_above_50"):
        if not rsi_result.get("rsi_recently_crossed_above_50", False):
            # 豁免：RSI 当前在 50-58 且方向向上（刚穿越但还未记录）
            if not (50 <= rsi_1h <= 58 and rsi_result.get("rsi_slope_1h", 0) > 0):
                return {
                    "allowed": False,
                    "reason": f"RSI_NOT_CROSSED_50_UP:{signal_type}"
                }

    if family_cfg.get("rsi_cross_below_50"):
        if not rsi_result.get("rsi_recently_crossed_below_50", False):
            if not (42 <= rsi_1h <= 50 and rsi_result.get("rsi_slope_1h", 0) < 0):
                return {
                    "allowed": False,
                    "reason": f"RSI_NOT_CROSSED_50_DOWN:{signal_type}"
                }

    # 市场状态偏好检查（非强制，但记录）
    preferred_regime = family_cfg.get("preferred_regime")
    regime_warning = None
    if preferred_regime and market_regime and market_regime != preferred_regime:
        regime_warning = f"NON_PREFERRED_REGIME:{market_regime}!={preferred_regime}"

    logger.debug(
        f"Signal whitelist check passed: {signal_type}, "
        f"boll_score={boll_score:.3f}, rsi_1h={rsi_1h:.1f}"
    )

    return {
        "allowed": True,
        "reason": None,
        "regime_warning": regime_warning,
    }


def get_signal_family_config(signal_type: str) -> dict:
    """
    获取信号族配置
    
    Args:
        signal_type: 信号类型
        
    Returns:
        信号族配置字典
    """
    return SIGNAL_FAMILY_CONFIG.get(signal_type, {})


def is_signal_enabled(signal_type: str) -> bool:
    """
    检查信号是否启用
    
    Args:
        signal_type: 信号类型
        
    Returns:
        是否启用
    """
    cfg = SIGNAL_FAMILY_CONFIG.get(signal_type)
    return cfg is not None and cfg.get("enabled", False)
