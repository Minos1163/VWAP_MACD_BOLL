"""
三共振评分聚合器
整合 MACD + BOLL + RSI 三层评分
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ResonanceScorer:
    """
    三共振评分聚合器
    整合 MACD + BOLL + RSI 三层评分
    """

    def __init__(self, config: dict):
        """
        初始化三共振评分器

        Args:
            config: 配置字典，包含:
                - resonance_scoring: 三共振评分配置
        """
        # 从完整配置中提取三共振配置
        resonance_cfg = (
            config
            .get("fund_flow", {})
            .get("macd_mtf_strategy_v2", {})
            .get("entry_filters", {})
            .get("resonance_scoring", {})
        )

        # 基础权重
        self.weights = resonance_cfg.get("weights", {
            "macd_base": 0.40,
            "boll_structure": 0.35,
            "rsi_momentum": 0.25,
        })

        # 市场自适应权重
        market_weights = resonance_cfg.get("market_adaptive_weights", {})
        self.trending_weights = market_weights.get("trending", self.weights)
        self.ranging_weights = market_weights.get("ranging", self.weights)
        self.volatile_weights = market_weights.get("volatile", self.weights)

        # 最低共振门槛
        self.min_resonance_score_for_entry = resonance_cfg.get("min_resonance_score_for_entry", 0.55)
        self.min_score = self.min_resonance_score_for_entry  # 兼容旧代码

        # 强共振阈值
        self.strong_resonance_threshold = resonance_cfg.get("strong_resonance_threshold", 0.75)

        # 信号类型特定阈值
        self.entry_thresholds_by_signal_type = resonance_cfg.get("entry_thresholds_by_signal_type", {})

    def compute(
        self,
        direction: str,
        macd_base_score: float,
        boll_result,         # BOLLResult
        rsi_result,          # RSIResult
        market_regime: str,
    ) -> dict:
        """
        计算三共振综合评分
        
        Args:
            direction: 方向 (long/short)
            macd_base_score: MACD 基础评分
            boll_result: BOLL 分析结果
            rsi_result: RSI 分析结果
            market_regime: 市场状态
            
        Returns:
            评分结果字典
        """
        # 选择权重（基于市场状态）
        if market_regime in ("TRENDING_BULL", "TRENDING_BEAR"):
            W = self.trending_weights
        elif market_regime in ("RANGING", "BREAKOUT_WATCH"):
            W = self.ranging_weights
        else:
            return {
                "resonance_score": 0.0,
                "gate_pass": False,
                "reason": f"VOLATILE_REGIME:{market_regime}"
            }

        W_macd = W.get("weight_macd_layer", W.get("macd_base", 0.40))
        W_boll = W.get("weight_boll_layer", W.get("boll_structure", 0.35))
        W_rsi = W.get("weight_rsi_layer", W.get("rsi_momentum", 0.25))

        # 提取各层评分
        boll_score = boll_result.structure_score
        rsi_score = rsi_result.momentum_score
        div_bonus = rsi_result.divergence_bonus

        # 检查 BOLL 门控
        if not boll_result.gate_pass:
            return {
                "resonance_score": 0.0,
                "gate_pass": False,
                "reason": f"BOLL_GATE_FAIL: {boll_result.gate_fail_reason}"
            }

        # 检查 RSI 门控
        if not rsi_result.gate_pass:
            return {
                "resonance_score": 0.0,
                "gate_pass": False,
                "reason": f"RSI_GATE_FAIL: {rsi_result.fail_reason}"
            }

        # 综合评分计算
        raw_score = (
            macd_base_score * W_macd +
            boll_score * W_boll +
            rsi_score * W_rsi +
            div_bonus
        )

        resonance_score = max(0.0, min(1.0, raw_score))

        # 最低门槛检查
        if resonance_score < self.min_score:
            return {
                "resonance_score": resonance_score,
                "gate_pass": False,
                "reason": (
                    f"RESONANCE_BELOW_MIN: {resonance_score:.3f} < {self.min_score} "
                    f"(macd={macd_base_score*W_macd:.3f}, "
                    f"boll={boll_score*W_boll:.3f}, "
                    f"rsi={rsi_score*W_rsi:.3f})"
                )
            }

        logger.debug(
            f"Resonance score computed: {resonance_score:.3f} "
            f"(macd={macd_base_score:.3f}*{W_macd}, "
            f"boll={boll_score:.3f}*{W_boll}, "
            f"rsi={rsi_score:.3f}*{W_rsi}, "
            f"div={div_bonus:.3f})"
        )

        return {
            "resonance_score": resonance_score,
            "gate_pass": True,
            "reason": "PASS",
            "detail": {
                "macd_contribution": macd_base_score * W_macd,
                "boll_contribution": boll_score * W_boll,
                "rsi_contribution": rsi_score * W_rsi,
                "div_bonus": div_bonus,
                "weights_used": market_regime,
            }
        }

    def get_leverage_tier(self, resonance_score: float, config: dict) -> int:
        """
        根据共振评分获取杠杆等级
        
        Args:
            resonance_score: 共振评分
            config: 配置字典
            
        Returns:
            杠杆倍数
        """
        tiers = config.get("leverage_score_tiers", [
            {"score_min": 0.85, "leverage": 5},
            {"score_min": 0.78, "leverage": 4},
            {"score_min": 0.65, "leverage": 3},
        ])
        
        for tier in sorted(tiers, key=lambda x: x["score_min"], reverse=True):
            if resonance_score >= tier["score_min"]:
                return tier["leverage"]
        
        return config.get("min_leverage", 3)

    def get_position_mult(self, resonance_score: float, config: dict) -> float:
        """
        根据共振评分获取仓位乘数
        
        Args:
            resonance_score: 共振评分
            config: 配置字典
            
        Returns:
            仓位乘数
        """
        tiers = config.get("resonance_scoring", {}).get("score_tier_position_mults", {
            "0.92": 1.20,
            "0.85": 1.10,
            "0.78": 1.00,
            "0.70": 0.90,
            "0.65": 0.80,
        })
        
        thresholds = sorted([(float(k), v) for k, v in tiers.items()], reverse=True)
        
        for threshold, mult in thresholds:
            if resonance_score >= threshold:
                return mult
        
        return 0.80
