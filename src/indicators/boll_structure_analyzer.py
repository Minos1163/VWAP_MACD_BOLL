"""
BOLL 结构分析器
功能:
  - 计算 BOLL 带 (upper/mid/lower)
  - 价格区域定位 (zone 0-6)
  - 带宽状态分类
  - 结构评分生成
  - 替代 VWAP 的过滤功能
"""

import math
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BOLLResult:
    """BOLL 分析结果"""
    upper: float
    mid: float
    lower: float
    bandwidth: float
    bandwidth_state: str        # squeeze/tight/normal/wide/expanding
    relative_position: float    # 0 (下轨) ~ 1 (上轨)
    zone: int                   # 0-6
    structure_score: float      # 0.0 ~ 1.0
    gate_pass: bool
    gate_fail_reason: Optional[str]
    mid_slope: float            # 中轨斜率
    mid_slope_bonus: float      # 斜率加成


class BOLLStructureAnalyzer:
    """
    BOLL 结构分析器
    替代 VWAP 的过滤功能，提供价格结构位置和带宽状态评估
    """

    ZONE_NAMES = {
        0: "above_upper",    # 上轨外
        1: "upper_zone",     # 上轨区
        2: "mid_upper_zone", # 中上区
        3: "neutral_zone",   # 中性区
        4: "mid_lower_zone", # 中下区
        5: "lower_zone",     # 下轨区
        6: "below_lower",    # 下轨外
    }

    def __init__(self, config: dict):
        """
        初始化 BOLL 分析器
        
        Args:
            config: 配置字典，包含:
                - period: BOLL 周期（默认 20）
                - std_dev: 标准差倍数（默认 2.0）
                - min_structure_score_for_entry: 入场最低结构评分（默认 0.55）
                - hard_block_threshold: 硬性屏蔽阈值（默认 0.20）
                - bandwidth: 带宽配置字典
                - mid_slope_bonus: 中轨斜率加成配置
        """
        self.period = config.get("period", 20)
        self.std_dev = config.get("std_dev", 2.0)
        self.min_score = config.get("min_structure_score_for_entry", 0.55)
        self.hard_block = config.get("hard_block_threshold", 0.20)

        # 带宽阈值
        bw_cfg = config.get("bandwidth", {})
        self.bw_thresholds = {
            "squeeze":   bw_cfg.get("squeeze_threshold", 0.025),
            "tight":     bw_cfg.get("tight_threshold", 0.045),
            "normal":    bw_cfg.get("normal_threshold", 0.080),
            "wide":      bw_cfg.get("wide_threshold", 0.120),
        }

        # 中轨斜率加成
        self.mid_slope_bonus_map = config.get("mid_slope_bonus", {
            "strong_aligned": 0.05,
            "weak_aligned":   0.02,
            "neutral":        0.00,
            "opposed":       -0.10,
        })

        # 做多评分矩阵（基于 zone）
        self.long_zone_scores = {
            0: -1.0,  # 上轨外: 硬性禁止做多
            1:  0.0,  # 上轨区: 不做多
            2:  0.10, # 中上区: 极弱
            3:  0.35, # 中性区: 弱
            4:  0.65, # 中下区: 良
            5:  0.90, # 下轨区: 优
            6:  1.00, # 下轨外: 极优（超卖反弹）
        }

        # 做空评分矩阵
        self.short_zone_scores = {
            0:  1.00, # 上轨外: 极优（超买回落）
            1:  0.90, # 上轨区: 优
            2:  0.65, # 中上区: 良
            3:  0.35, # 中性区: 弱
            4:  0.10, # 中下区: 极弱
            5:  0.0,  # 下轨区: 不做空
            6: -1.0,  # 下轨外: 硬性禁止做空
        }

    def compute_boll(self, closes: list) -> tuple:
        """
        计算 BOLL 带 (upper, mid, lower)
        
        Args:
            closes: 收盘价列表
            
        Returns:
            (upper, mid, lower) 元组
        """
        if len(closes) < self.period:
            logger.warning(f"Insufficient data for BOLL: {len(closes)} < {self.period}")
            return 0.0, 0.0, 0.0

        recent = closes[-self.period:]
        mid = sum(recent) / self.period
        variance = sum((x - mid) ** 2 for x in recent) / self.period
        std = math.sqrt(variance)

        upper = mid + self.std_dev * std
        lower = mid - self.std_dev * std
        
        return upper, mid, lower

    def classify_bandwidth(self, bandwidth: float) -> str:
        """
        带宽状态分类
        
        Args:
            bandwidth: 带宽值 (upper - lower) / mid
            
        Returns:
            带宽状态字符串
        """
        if bandwidth < self.bw_thresholds["squeeze"]:
            return "squeeze"
        elif bandwidth < self.bw_thresholds["tight"]:
            return "tight"
        elif bandwidth < self.bw_thresholds["normal"]:
            return "normal"
        elif bandwidth < self.bw_thresholds["wide"]:
            return "wide"
        else:
            return "expanding"

    def locate_zone(self, price: float, upper: float, mid: float, lower: float) -> tuple:
        """
        定位价格区域 (zone 0-6) 和相对位置 (0-1)
        
        Args:
            price: 当前价格
            upper: 上轨
            mid: 中轨
            lower: 下轨
            
        Returns:
            (zone, relative_position) 元组
        """
        # 价格在上轨外
        if price > upper:
            bandwidth = upper - lower
            if bandwidth > 0:
                rel_pos = 1.0 + (price - upper) / bandwidth
            else:
                rel_pos = 1.0
            return 0, rel_pos
        
        # 价格在下轨外
        if price < lower:
            bandwidth = upper - lower
            if bandwidth > 0:
                rel_pos = 0.0 - (lower - price) / bandwidth
            else:
                rel_pos = 0.0
            return 6, rel_pos

        # 价格在带内
        bandwidth = upper - lower
        if bandwidth <= 0:
            return 3, 0.5

        relative = (price - lower) / bandwidth  # 0=下轨, 1=上轨

        # 分区映射
        if relative >= 0.85:
            zone = 1   # 上轨区
        elif relative >= 0.65:
            zone = 2   # 中上区
        elif relative >= 0.45:
            zone = 3   # 中性区
        elif relative >= 0.25:
            zone = 4   # 中下区
        else:
            zone = 5   # 下轨区

        return zone, relative

    def compute_mid_slope(self, closes: list, slope_bars: int = 3) -> float:
        """
        计算 BOLL 中轨（SMA）斜率
        
        Args:
            closes: 收盘价列表
            slope_bars: 斜率回望周期
            
        Returns:
            斜率值（百分比）
        """
        if len(closes) < self.period + slope_bars:
            return 0.0

        mid_current = sum(closes[-self.period:]) / self.period
        mid_prev = sum(closes[-(self.period + slope_bars):-slope_bars]) / self.period

        return (mid_current - mid_prev) / mid_prev if mid_prev != 0 else 0.0

    def compute_mid_slope_bonus(self, slope: float, direction: str) -> float:
        """
        中轨斜率加成
        
        Args:
            slope: 中轨斜率
            direction: 方向 (long/short)
            
        Returns:
            加成分数
        """
        if direction == "long":
            aligned = slope > 0
        elif direction == "short":
            aligned = slope < 0
        else:
            return 0.0

        abs_slope = abs(slope)

        if aligned:
            if abs_slope > 0.002:
                return self.mid_slope_bonus_map["strong_aligned"]
            else:
                return self.mid_slope_bonus_map["weak_aligned"]
        else:
            if abs_slope > 0.001:
                return self.mid_slope_bonus_map["opposed"]
            else:
                return self.mid_slope_bonus_map["neutral"]

    def analyze(
        self,
        symbol: str,
        direction: str,
        closes_4h: list,
        closes_1h: list,
        current_price: float,
    ) -> BOLLResult:
        """
        BOLL 结构分析主入口
        
        Args:
            symbol: 交易对符号
            direction: 方向 (long/short)
            closes_4h: 4h 收盘价列表
            closes_1h: 1h 收盘价列表
            current_price: 当前价格
            
        Returns:
            BOLLResult 对象
        """
        # 4h BOLL（主结构）
        upper_4h, mid_4h, lower_4h = self.compute_boll(closes_4h)
        
        if mid_4h <= 0:
            return BOLLResult(
                upper=0, mid=0, lower=0, bandwidth=0,
                bandwidth_state="unknown", relative_position=0.5,
                zone=3, structure_score=0.0,
                gate_pass=False, gate_fail_reason="BOLL_DATA_MISSING",
                mid_slope=0, mid_slope_bonus=0,
            )

        bandwidth = (upper_4h - lower_4h) / mid_4h
        bw_state = self.classify_bandwidth(bandwidth)
        zone, rel_pos = self.locate_zone(current_price, upper_4h, mid_4h, lower_4h)

        # BOLL squeeze 硬性禁止
        if bw_state == "squeeze":
            logger.debug(f"[{symbol}] BOLL squeeze detected, blocking entry")
            return BOLLResult(
                upper=upper_4h, mid=mid_4h, lower=lower_4h,
                bandwidth=bandwidth, bandwidth_state=bw_state,
                relative_position=rel_pos, zone=zone,
                structure_score=0.0,
                gate_pass=False, gate_fail_reason="BOLL_SQUEEZE",
                mid_slope=0, mid_slope_bonus=0,
            )

        # 获取区域评分
        if direction == "long":
            zone_score = self.long_zone_scores.get(zone, 0.0)
        elif direction == "short":
            zone_score = self.short_zone_scores.get(zone, 0.0)
        else:
            zone_score = 0.0

        # 硬性反向禁止
        if zone_score < 0:
            logger.debug(
                f"[{symbol}] BOLL hard block: zone={zone}, "
                f"direction={direction}, zone_score={zone_score}"
            )
            return BOLLResult(
                upper=upper_4h, mid=mid_4h, lower=lower_4h,
                bandwidth=bandwidth, bandwidth_state=bw_state,
                relative_position=rel_pos, zone=zone,
                structure_score=0.0,
                gate_pass=False,
                gate_fail_reason=f"BOLL_ZONE_HARD_BLOCK: zone={zone}, dir={direction}",
                mid_slope=0, mid_slope_bonus=0,
            )

        # 中轨斜率计算
        mid_slope = self.compute_mid_slope(closes_4h)
        slope_bonus = self.compute_mid_slope_bonus(mid_slope, direction)

        # 最终结构评分
        structure_score = max(0.0, min(1.0, zone_score + slope_bonus))

        # 门控检查
        if structure_score < self.hard_block:
            logger.debug(
                f"[{symbol}] BOLL hard block threshold: "
                f"score={structure_score:.3f} < {self.hard_block}"
            )
            return BOLLResult(
                upper=upper_4h, mid=mid_4h, lower=lower_4h,
                bandwidth=bandwidth, bandwidth_state=bw_state,
                relative_position=rel_pos, zone=zone,
                structure_score=structure_score,
                gate_pass=False,
                gate_fail_reason=f"BOLL_HARD_BLOCK: score={structure_score:.3f} < {self.hard_block}",
                mid_slope=mid_slope, mid_slope_bonus=slope_bonus,
            )

        gate_pass = structure_score >= self.min_score
        
        logger.debug(
            f"[{symbol}] BOLL analysis: zone={zone}, "
            f"score={structure_score:.3f}, gate_pass={gate_pass}, "
            f"bandwidth={bw_state}"
        )

        return BOLLResult(
            upper=upper_4h, mid=mid_4h, lower=lower_4h,
            bandwidth=bandwidth, bandwidth_state=bw_state,
            relative_position=rel_pos, zone=zone,
            structure_score=structure_score,
            gate_pass=gate_pass,
            gate_fail_reason=None if gate_pass else (
                f"BOLL_GATE_FAIL: {structure_score:.3f} < {self.min_score}"
            ),
            mid_slope=mid_slope, mid_slope_bonus=slope_bonus,
        )

    def get_position_mult_by_bandwidth(self, bandwidth_state: str) -> float:
        """
        根据带宽状态获取仓位乘数
        
        Args:
            bandwidth_state: 带宽状态
            
        Returns:
            仓位乘数
        """
        mult_map = {
            "squeeze":   0.00,  # squeeze 禁止开仓
            "tight":     0.70,
            "normal":    1.00,
            "wide":      0.90,
            "expanding": 0.70,
        }
        return mult_map.get(bandwidth_state, 1.00)
