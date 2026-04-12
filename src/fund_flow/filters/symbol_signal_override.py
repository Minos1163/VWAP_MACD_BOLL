"""
Phase 3D: Symbol 级信号 Override

核心逻辑：
- 为特定交易对提供信号配置覆盖
- 只覆盖显式指定的字段，其余继承全局配置
- 用于为 ATOMUSDT / SOLUSDT 等白名单 symbol 有条件恢复 flip_bullish
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import logging


VALID_SIGNAL_MODES = {"disabled", "trial_only", "enabled_with_strict_threshold"}


def _normalize_signal_mode(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text if text in VALID_SIGNAL_MODES else None


@dataclass
class SymbolSignalOverride:
    """单 symbol 的信号配置覆盖"""
    symbol: str
    
    # 信号禁用覆盖（None = 继承全局配置）
    disable_flip_bullish: Optional[bool] = None
    disable_flip_bullish_trial: Optional[bool] = None
    disable_green_bar_growing: Optional[bool] = None
    disable_long_dual_support: Optional[bool] = None
    flip_bullish_mode: Optional[str] = None
    green_bar_growing_mode: Optional[str] = None
    
    # 评分门槛覆盖
    min_signal_score_override: Optional[float] = None
    preflip_trial_min_signal_score_override: Optional[float] = None
    
    # VWAP 评分门槛覆盖
    min_vwap_score_override: Optional[float] = None
    
    # EMA 乘数覆盖
    ema_multiplier_override: Optional[float] = None
    
    # 额外配置
    extra_config: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SymbolSignalOverride":
        """从字典创建"""
        return cls(
            symbol=str(data.get("symbol", "")).strip().upper(),
            disable_flip_bullish=data.get("disable_flip_bullish"),
            disable_flip_bullish_trial=data.get("disable_flip_bullish_trial"),
            disable_green_bar_growing=data.get("disable_green_bar_growing"),
            disable_long_dual_support=data.get("disable_long_dual_support"),
            flip_bullish_mode=_normalize_signal_mode(data.get("flip_bullish_mode")),
            green_bar_growing_mode=_normalize_signal_mode(data.get("green_bar_growing_mode")),
            min_signal_score_override=data.get("min_signal_score_override"),
            preflip_trial_min_signal_score_override=data.get("preflip_trial_min_signal_score_override"),
            min_vwap_score_override=data.get("min_vwap_score_override"),
            ema_multiplier_override=data.get("ema_multiplier_override"),
            extra_config=data.get("extra_config", {}),
        )

    def get_signal_mode(self, signal_type: str) -> Optional[str]:
        signal = str(signal_type or "").strip().lower()
        if signal == "flip_bullish":
            if self.flip_bullish_mode:
                return self.flip_bullish_mode
            if self.disable_flip_bullish is True:
                return "disabled"
            if self.disable_flip_bullish is False and (
                self.min_signal_score_override is not None
                or self.min_vwap_score_override is not None
            ):
                return "enabled_with_strict_threshold"
            return None
        if signal == "green_bar_growing":
            if self.green_bar_growing_mode:
                return self.green_bar_growing_mode
            if self.disable_green_bar_growing is True:
                return "disabled"
            if self.disable_green_bar_growing is False and (
                self.min_signal_score_override is not None
                or self.min_vwap_score_override is not None
            ):
                return "enabled_with_strict_threshold"
            return None
        return None


class SymbolSignalOverrideRegistry:
    """
    symbol 级信号 override 注册表
    
    功能：
    1. 管理各 symbol 的信号配置覆盖
    2. 只覆盖显式指定的字段，其余继承全局配置
    3. 支持动态添加/移除覆盖
    
    使用场景：
    - 为特定币种恢复被禁用的信号类型
    - 为特定币种调整评分门槛
    - 为特定币种设置特殊参数
    """
    
    def __init__(
        self,
        overrides: Optional[List[Dict[str, Any]]] = None,
        global_config: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化注册表
        
        Args:
            overrides: 覆盖配置列表
            global_config: 全局配置（用于提供默认值）
        """
        self.global_config = global_config or {}
        self._registry: Dict[str, SymbolSignalOverride] = {}
        self.logger = logging.getLogger("SymbolSignalOverride")
        
        # 加载覆盖配置
        if overrides:
            for item in overrides:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol", "")).strip().upper()
                if symbol:
                    self._registry[symbol] = SymbolSignalOverride.from_dict(item)
                    self.logger.debug(f"加载 symbol 级覆盖: {symbol}")
        
        # 统计信息
        self._stats = {
            "total_checks": 0,
            "override_applied": 0,
            "global_applied": 0,
        }
    
    def is_signal_disabled(self, symbol: str, signal_type: str) -> bool:
        """
        判断指定 symbol 的指定信号是否被禁用
        symbol 级配置优先于全局配置
        
        Args:
            symbol: 交易对
            signal_type: 信号类型
            
        Returns:
            是否禁用
        """
        self._stats["total_checks"] += 1
        override = self._registry.get(str(symbol or "").strip().upper())
        mode = override.get_signal_mode(signal_type) if override is not None else None
        if mode == "disabled":
            self._stats["override_applied"] += 1
            return True
        if mode in {"trial_only", "enabled_with_strict_threshold"}:
            self._stats["override_applied"] += 1
            return False
        
        if signal_type == "flip_bullish":
            # symbol 级覆盖优先
            if override is not None and override.disable_flip_bullish is not None:
                self._stats["override_applied"] += 1
                return override.disable_flip_bullish
            # 回退到全局配置
            self._stats["global_applied"] += 1
            return self.global_config.get("disable_flip_bullish_entries", True)
        
        if signal_type == "green_bar_growing":
            # symbol 级覆盖优先
            if override is not None and override.disable_green_bar_growing is not None:
                self._stats["override_applied"] += 1
                return override.disable_green_bar_growing
            # 回退到全局配置
            self._stats["global_applied"] += 1
            return self.global_config.get("disable_green_bar_growing_entries", True)
        
        # 其他信号类型默认不禁用
        return False
    
    def get_min_signal_score(self, symbol: str) -> float:
        """
        获取指定 symbol 的入场评分门槛
        symbol 级别可覆盖全局配置
        
        Args:
            symbol: 交易对
            
        Returns:
            评分门槛
        """
        override = self._registry.get(str(symbol or "").strip().upper())
        
        if override and override.min_signal_score_override is not None:
            return override.min_signal_score_override
        
        return float(self.global_config.get("min_signal_score", 0.85))
    
    def get_min_vwap_score(self, symbol: str) -> float:
        """
        获取指定 symbol 的 VWAP 评分门槛
        
        Args:
            symbol: 交易对
            
        Returns:
            VWAP 评分门槛
        """
        override = self._registry.get(str(symbol or "").strip().upper())
        
        if override and override.min_vwap_score_override is not None:
            return override.min_vwap_score_override
        
        return float(self.global_config.get("flip_bullish_min_vwap_score", 0.12))
    
    def get_ema_multiplier(self, symbol: str, ema_structure: str = "normal") -> float:
        """
        获取指定 symbol 的 EMA 乘数
        
        Args:
            symbol: 交易对
            ema_structure: EMA 结构状态
            
        Returns:
            EMA 乘数
        """
        override = self._registry.get(str(symbol or "").strip().upper())
        
        # symbol 级覆盖
        if override and override.ema_multiplier_override is not None:
            return override.ema_multiplier_override
        
        # 全局配置
        if ema_structure == "strong":
            return float(self.global_config.get("ema_multiplier_strong", 1.2))
        elif ema_structure == "weak":
            return float(self.global_config.get("ema_multiplier_weak", 0.6))
        else:
            return float(self.global_config.get("ema_multiplier_normal", 1.0))
    
    def get_extra_config(self, symbol: str, key: str, default: Any = None) -> Any:
        """
        获取 symbol 级额外配置
        
        Args:
            symbol: 交易对
            key: 配置键
            default: 默认值
            
        Returns:
            配置值
        """
        override = self._registry.get(str(symbol or "").strip().upper())
        
        if override and key in override.extra_config:
            return override.extra_config[key]
        
        return default
    
    def register_override(self, override: SymbolSignalOverride) -> None:
        """
        注册 symbol 级覆盖
        
        Args:
            override: 覆盖配置
        """
        symbol = str(override.symbol or "").strip().upper()
        if not symbol:
            return
        override.symbol = symbol
        self._registry[symbol] = override
        self.logger.info(f"注册 symbol 级覆盖: {symbol}")
    
    def unregister_override(self, symbol: str) -> bool:
        """
        移除 symbol 级覆盖
        
        Args:
            symbol: 交易对
            
        Returns:
            是否成功移除
        """
        normalized_symbol = str(symbol or "").strip().upper()
        if normalized_symbol in self._registry:
            del self._registry[normalized_symbol]
            self.logger.info(f"移除 symbol 级覆盖: {normalized_symbol}")
            return True
        return False
    
    def get_override(self, symbol: str) -> Optional[SymbolSignalOverride]:
        """
        获取 symbol 的覆盖配置
        
        Args:
            symbol: 交易对
            
        Returns:
            覆盖配置（如果存在）
        """
        return self._registry.get(str(symbol or "").strip().upper())
    
    def list_overrides(self) -> List[str]:
        """
        列出所有有覆盖配置的 symbol
        
        Returns:
            symbol 列表
        """
        return list(self._registry.keys())
    
    def update_global_config(self, config: Dict[str, Any]) -> None:
        """
        更新全局配置
        
        Args:
            config: 新的全局配置
        """
        self.global_config = config
    
    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        return self._stats.copy()
    
    def reset_stats(self) -> None:
        """重置统计信息"""
        for key in self._stats:
            self._stats[key] = 0


def create_override_registry(
    config: Dict[str, Any],
) -> SymbolSignalOverrideRegistry:
    """
    便捷函数：从配置创建 Override 注册表
    
    Args:
        config: 完整配置字典
        
    Returns:
        SymbolSignalOverrideRegistry 实例
    """
    # 提取全局信号配置
    fund_flow_cfg = config.get("fund_flow", {}).get("macd_mtf_strategy_v2", {})
    global_signal_config = dict(fund_flow_cfg.get("entry_filters", {}))
    if "min_signal_score" not in global_signal_config:
        thresholds_cfg = fund_flow_cfg.get("entry_thresholds", {})
        if isinstance(thresholds_cfg, dict) and "min_signal_score" in thresholds_cfg:
            global_signal_config["min_signal_score"] = thresholds_cfg.get("min_signal_score")
    
    # 提取 symbol 级覆盖
    overrides = fund_flow_cfg.get("entry_filters", {}).get("symbol_signal_overrides", [])
    if isinstance(overrides, dict):
        # 如果是字典格式，转换为列表
        overrides = [{"symbol": k, **v} for k, v in overrides.items()]
    
    return SymbolSignalOverrideRegistry(
        overrides=overrides,
        global_config=global_signal_config,
    )
