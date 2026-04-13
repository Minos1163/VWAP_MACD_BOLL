"""
MACD多时间框架策略 V2.0 回测脚本
VWAP + BOLL 增强版

功能：
1. 从本地parquet文件加载历史K线数据
2. 计算MACD、BOLL、VWAP等技术指标
3. 使用V2.0策略引擎进行信号分析
4. 模拟交易执行并生成回测报告
"""

import os
import sys
import json
import argparse
import copy
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, replace
import warnings
warnings.filterwarnings('ignore')

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config_loader import ConfigLoader
from src.fund_flow.macd_strategy_v2 import (
    MACDStrategyV2Engine,
    MACDStrategyV2Config,
    MACDSignalV2,
    VetoType,
    build_macd_v2_config_from_runtime,
)
from src.fund_flow.filters.symbol_signal_override import create_override_registry
from src.fund_flow.filters.time_window_filter import TimeWindowFilter, TimeWindowFilterConfig
from src.fund_flow.replay_window import (
    apply_market_data_window,
    resolve_replay_window_bounds,
    timestamp_in_trade_window,
)
from src.utils.indicators import calculate_macd_histogram_series
from src.utils.passive_fill import classify_passive_limit_fill, direct_ioc_fill_is_valid


# ==================== 配置 ====================

@dataclass
class BacktestConfig:
    """回测配置"""
    # 回测币种
    symbols: List[str] = field(default_factory=list)
    
    # 回测参数
    initial_capital: float = 10000.0  # USDT
    fee_rate: float = 0.0004  # 0.04% 手续费
    max_positions: int = 2    # 最大持仓数
    
    # 数据目录
    data_dir: str = "data/backtest_cache"
    
    # 当前运行配置来源
    config_path: str = "config/trading_config_fund_flow.json"
    profile_name: str = ""
    available_symbols_only: bool = True
    allowed_entry_hours_utc: List[int] = field(default_factory=list)
    window_start_iso: str = ""
    window_end_iso: str = ""
    data_window_start_iso: str = ""
    data_window_end_iso: str = ""
    warmup_hours: int = 0
    decision_timeframe: str = "15m"
    
    # 仓位配置
    min_position_pct: float = 0.12
    max_position_pct: float = 0.60
    reserve_pct: float = 0.20
    default_target_portion: float = 0.60
    max_symbol_position_portion: float = 0.60
    min_open_portion: float = 0.06
    
    # 杠杆配置
    min_leverage: int = 2
    default_leverage: int = 3
    max_leverage: int = 4
    fixed_leverage: Optional[int] = None
    
    # 止损止盈
    default_stop_loss_pct: float = 0.02   # 默认止损2%
    default_take_profit_pct: float = 0.04  # 默认止盈4%
    take_profit_pct_levels: List[float] = field(default_factory=list)
    take_profit_reduce_pct_levels: List[float] = field(default_factory=list)
    breakeven_enabled: bool = True
    breakeven_trigger_pnl_ratio: float = 0.003
    breakeven_lock_ratio: float = 0.001
    trailing_stop_enabled: bool = False
    trailing_stop_activation_pct: float = 0.0
    trailing_stop_atr_multiplier: float = 0.0
    trailing_stop_min_distance: float = 0.0
    trailing_stop_max_distance: float = 0.0
    entry_bar_same_bar_enabled: bool = False
    same_bar_tp_priority_mode: str = "stop_first"
    partial_aware_breakeven_enabled: bool = False
    partial_aware_no_partial_trigger_pnl_ratio: float = 0.0
    runner_only_trailing_enabled: bool = False
    runner_only_trailing_min_completed_levels: int = 0
    shrink_exit_loss_mitigation_enabled: bool = False
    shrink_exit_loss_mitigation_pnl_threshold: float = -0.005
    shrink_exit_loss_mitigation_exit_ratio: float = 0.60
    shrink_exit_loss_mitigation_ignore_if_pnl_gt: float = 0.01
    max_hold_hours: float = 0.0
    entry_slippage: float = 0.0015
    entry_passive_offset_pct: float = 0.0015
    entry_passive_pricing_atr_fraction: float = 0.25
    entry_passive_pricing_min_offset_pct: float = 0.0008
    entry_passive_pricing_max_offset_pct: float = 0.0060
    entry_passive_pricing_signal_type_multipliers: Dict[str, float] = field(default_factory=dict)
    entry_passive_pricing_vwap_state_multipliers: Dict[str, float] = field(default_factory=dict)
    signal_type_tp_config: Dict[str, dict] = field(default_factory=dict)
    dynamic_position_config: Dict[str, object] = field(default_factory=dict)
    entry_time_in_force: str = "IOC"
    direct_ioc_fill_mode: str = "touch"
    direct_ioc_min_penetration_bps: float = 0.0
    gtc_expire_bars: int = 0
    gtc_cancel_on_signal_reversal: bool = False
    open_gtc_fallback_enabled: bool = True
    max_consecutive_losses: int = 0
    consecutive_loss_cooldown_seconds: int = 0


def _deep_merge_dict(base: dict, overrides: dict) -> dict:
    """递归合并 profile 覆盖项，不污染原始配置对象。"""
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def apply_backtest_profile(runtime_cfg: dict, profile_name: Optional[str] = None) -> tuple[dict, str]:
    """将 backtest profile 覆盖到运行配置，仅用于回测。"""
    if not isinstance(runtime_cfg, dict):
        raise TypeError("runtime_cfg must be a dict")

    merged_cfg = copy.deepcopy(runtime_cfg)
    fund_flow_cfg = merged_cfg.setdefault("fund_flow", {})
    if not isinstance(fund_flow_cfg, dict):
        raise ValueError("fund_flow config must be a dict")

    backtest_cfg = fund_flow_cfg.get("backtest", {})
    if not isinstance(backtest_cfg, dict):
        return merged_cfg, ""

    raw_profile_name = str(profile_name or "").strip()
    if raw_profile_name.lower() in {"none", "off", "null", "disabled", "disable"}:
        return merged_cfg, ""

    selected_profile = str(raw_profile_name or backtest_cfg.get("default_profile", "") or "").strip()
    if not selected_profile:
        return merged_cfg, ""

    profiles = backtest_cfg.get("profiles", {})
    if not isinstance(profiles, dict) or selected_profile not in profiles:
        raise ValueError(f"backtest profile not found: {selected_profile}")

    profile = profiles[selected_profile]
    if not isinstance(profile, dict):
        raise ValueError(f"backtest profile must be a dict: {selected_profile}")

    trading_cfg = merged_cfg.setdefault("trading", {})
    if not isinstance(trading_cfg, dict):
        raise ValueError("trading config must be a dict")

    if isinstance(profile.get("symbols"), list) and profile["symbols"]:
        profile_symbols = [str(symbol).upper() for symbol in profile["symbols"] if str(symbol).strip()]
        trading_cfg["symbols"] = ConfigLoader.filter_symbols_with_blacklist(profile_symbols, merged_cfg)

    allowed_hours = profile.get("allowed_entry_hours_utc", profile.get("allowed_entry_hours"))
    if isinstance(allowed_hours, list):
        fund_flow_cfg["allowed_entry_hours_utc"] = [
            int(hour) for hour in allowed_hours if isinstance(hour, (int, float))
        ]

    if "max_active_symbols" in profile:
        fund_flow_cfg["max_active_symbols"] = int(profile["max_active_symbols"])

    config_overrides = profile.get("config_overrides", {})
    if isinstance(config_overrides, dict) and config_overrides:
        merged_cfg = _deep_merge_dict(merged_cfg, config_overrides)

    return merged_cfg, selected_profile


def build_backtest_summary(
    config: BacktestConfig,
    strategy_config: MACDStrategyV2Config,
    engine,
    available_symbols: List[str],
    missing_symbols: List[str],
    stats: dict,
) -> dict:
    """构建机器可读的回测摘要，供批量对比脚本复用。"""
    initial = float(config.initial_capital)
    capital = float(engine.capital)
    trades = list(engine.trades)
    wins = [t for t in trades if float(t.get("pnl", 0.0)) > 0]
    losses = [t for t in trades if float(t.get("pnl", 0.0)) <= 0]
    win_pnl = sum(float(t.get("pnl", 0.0)) for t in wins)
    loss_pnl = sum(float(t.get("pnl", 0.0)) for t in losses)
    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0
    profit_factor = (win_pnl / abs(loss_pnl)) if loss_pnl < 0 else (float("inf") if win_pnl > 0 else 0.0)

    signal_type_breakdown: Dict[str, dict] = {}
    cvd_bonus_breakdown: Dict[str, dict] = {}
    cvd_veto_breakdown: Dict[str, dict] = {}
    entry_tif_breakdown: Dict[str, dict] = {}
    entry_degradation_breakdown: Dict[str, dict] = {}
    for trade in trades:
        signal_type = str(trade.get("signal_type_1h", "unknown"))
        bucket = signal_type_breakdown.setdefault(
            signal_type,
            {"count": 0, "wins": 0, "pnl": 0.0},
        )
        bucket["count"] += 1
        pnl_value = float(trade.get("pnl", 0.0))
        bucket["pnl"] += pnl_value
        if pnl_value > 0:
            bucket["wins"] += 1

        cvd_state = str(trade.get("cvd_bonus_state", "inactive") or "inactive")
        cvd_bucket = cvd_bonus_breakdown.setdefault(
            cvd_state,
            {"count": 0, "wins": 0, "pnl": 0.0},
        )
        cvd_bucket["count"] += 1
        cvd_bucket["pnl"] += pnl_value
        if pnl_value > 0:
            cvd_bucket["wins"] += 1

        cvd_veto_state = str(trade.get("cvd_veto_state", "inactive") or "inactive")
        cvd_veto_bucket = cvd_veto_breakdown.setdefault(
            cvd_veto_state,
            {"count": 0, "wins": 0, "pnl": 0.0},
        )
        cvd_veto_bucket["count"] += 1
        cvd_veto_bucket["pnl"] += pnl_value
        if pnl_value > 0:
            cvd_veto_bucket["wins"] += 1

        initial_tif = str(trade.get("entry_initial_time_in_force", "") or "")
        final_tif = str(trade.get("entry_time_in_force", "") or "")
        tif_key = f"{initial_tif}->{final_tif}" if initial_tif or final_tif else "unknown"
        tif_bucket = entry_tif_breakdown.setdefault(
            tif_key,
            {"count": 0, "wins": 0, "pnl": 0.0},
        )
        tif_bucket["count"] += 1
        tif_bucket["pnl"] += pnl_value
        if pnl_value > 0:
            tif_bucket["wins"] += 1

        degradation_path = trade.get("entry_degradation_path")
        degradation_key = "direct_fill"
        if isinstance(degradation_path, list) and degradation_path:
            last_step = degradation_path[-1] if isinstance(degradation_path[-1], dict) else {}
            degradation_key = str(last_step.get("step", "degraded") or "degraded")
        deg_bucket = entry_degradation_breakdown.setdefault(
            degradation_key,
            {"count": 0, "wins": 0, "pnl": 0.0},
        )
        deg_bucket["count"] += 1
        deg_bucket["pnl"] += pnl_value
        if pnl_value > 0:
            deg_bucket["wins"] += 1

    for bucket in signal_type_breakdown.values():
        count = int(bucket["count"])
        bucket["win_rate_pct"] = (bucket["wins"] / count * 100.0) if count > 0 else 0.0
    for bucket in cvd_bonus_breakdown.values():
        count = int(bucket["count"])
        bucket["win_rate_pct"] = (bucket["wins"] / count * 100.0) if count > 0 else 0.0
    for bucket in cvd_veto_breakdown.values():
        count = int(bucket["count"])
        bucket["win_rate_pct"] = (bucket["wins"] / count * 100.0) if count > 0 else 0.0
    for bucket in entry_tif_breakdown.values():
        count = int(bucket["count"])
        bucket["win_rate_pct"] = (bucket["wins"] / count * 100.0) if count > 0 else 0.0
    for bucket in entry_degradation_breakdown.values():
        count = int(bucket["count"])
        bucket["win_rate_pct"] = (bucket["wins"] / count * 100.0) if count > 0 else 0.0

    return {
        "config_path": config.config_path,
        "backtest_profile": config.profile_name,
        "window_start_iso": config.window_start_iso,
        "window_end_iso": config.window_end_iso,
        "data_window_start_iso": config.data_window_start_iso,
        "data_window_end_iso": config.data_window_end_iso,
        "warmup_hours": int(config.warmup_hours),
        "initial_capital": initial,
        "final_capital": capital,
        "return_pct": (capital - initial) / initial * 100.0 if initial > 0 else 0.0,
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "available_symbols": available_symbols,
        "missing_symbols": missing_symbols,
        "timeline_points": int(stats.get("timeline_points", 0)),
        "signals_generated": int(stats.get("signals_generated", stats.get("signals", 0))),
        "signal_type_breakdown": signal_type_breakdown,
        "cvd_bonus_breakdown": cvd_bonus_breakdown,
        "cvd_veto_breakdown": cvd_veto_breakdown,
        "entry_tif_breakdown": entry_tif_breakdown,
        "entry_degradation_breakdown": entry_degradation_breakdown,
        "risk_metrics": {
            "equity_curve_points": len(getattr(engine, "equity_curve", []) or []),
            "max_drawdown_value": float(getattr(engine, "max_drawdown_value", 0.0) or 0.0),
            "max_drawdown_pct": float(getattr(engine, "max_drawdown_pct", 0.0) or 0.0),
            "max_drawdown_start_time": str(getattr(engine, "max_drawdown_start_time", "") or ""),
            "max_drawdown_trough_time": str(getattr(engine, "max_drawdown_trough_time", "") or ""),
            "max_drawdown_recovery_time": str(getattr(engine, "max_drawdown_recovery_time", "") or ""),
        },
        "strategy_config": {
            "min_signal_score": strategy_config.min_signal_score,
            "min_entry_score": strategy_config.min_entry_score,
            "max_stop_loss_pct": strategy_config.max_stop_loss_pct,
            "ema_strong_trend_leverage_mult": strategy_config.ema_strong_trend_leverage_mult,
            "dual_pressure_target_portion_bonus": strategy_config.dual_pressure_target_portion_bonus,
            "dual_pressure_max_symbol_position_portion": strategy_config.dual_pressure_max_symbol_position_portion,
            "use_cvd_bonus_filter": strategy_config.use_cvd_bonus_filter,
            "cvd_1h_slope_lookback": strategy_config.cvd_1h_slope_lookback,
            "cvd_15m_slope_lookback": strategy_config.cvd_15m_slope_lookback,
            "cvd_positive_delta_ratio_threshold": strategy_config.cvd_positive_delta_ratio_threshold,
            "cvd_negative_delta_ratio_threshold": strategy_config.cvd_negative_delta_ratio_threshold,
            "cvd_bullish_bonus_multiplier": strategy_config.cvd_bullish_bonus_multiplier,
            "cvd_neutral_bonus_multiplier": strategy_config.cvd_neutral_bonus_multiplier,
            "cvd_bearish_bonus_multiplier": strategy_config.cvd_bearish_bonus_multiplier,
            "use_cvd_veto_filter": strategy_config.use_cvd_veto_filter,
            "cvd_veto_session_reset": strategy_config.cvd_veto_session_reset,
            "cvd_veto_lookback_15m": strategy_config.cvd_veto_lookback_15m,
            "cvd_veto_positive_delta_ratio_threshold": strategy_config.cvd_veto_positive_delta_ratio_threshold,
            "cvd_veto_positive_pressure_threshold": strategy_config.cvd_veto_positive_pressure_threshold,
            "cvd_veto_session_ratio_change_threshold": strategy_config.cvd_veto_session_ratio_change_threshold,
            "cvd_veto_session_price_change_threshold": strategy_config.cvd_veto_session_price_change_threshold,
            "cvd_veto_strong_close_pos_threshold": strategy_config.cvd_veto_strong_close_pos_threshold,
            "cvd_veto_upper_wick_ratio_max": strategy_config.cvd_veto_upper_wick_ratio_max,
            "cvd_absorption_delta_ratio_threshold": strategy_config.cvd_absorption_delta_ratio_threshold,
            "cvd_absorption_close_pos_threshold": strategy_config.cvd_absorption_close_pos_threshold,
            "cvd_absorption_upper_wick_ratio_threshold": strategy_config.cvd_absorption_upper_wick_ratio_threshold,
            "cvd_absorption_structure_gap_threshold": strategy_config.cvd_absorption_structure_gap_threshold,
            "cvd_divergence_price_change_threshold": strategy_config.cvd_divergence_price_change_threshold,
            "cvd_divergence_session_change_threshold": strategy_config.cvd_divergence_session_change_threshold,
        },
        "runtime_limits": {
            "max_positions": config.max_positions,
            "default_target_portion": config.default_target_portion,
            "max_symbol_position_portion": config.max_symbol_position_portion,
            "min_open_portion": config.min_open_portion,
            "reserve_pct": config.reserve_pct,
            "min_leverage": config.min_leverage,
            "default_leverage": config.default_leverage,
            "max_leverage": config.max_leverage,
            "fixed_leverage": config.fixed_leverage,
            "stop_loss_pct": config.default_stop_loss_pct,
            "take_profit_pct": config.default_take_profit_pct,
            "take_profit_pct_levels": config.take_profit_pct_levels,
            "take_profit_reduce_pct_levels": config.take_profit_reduce_pct_levels,
            "breakeven_enabled": config.breakeven_enabled,
            "breakeven_trigger_pnl_ratio": config.breakeven_trigger_pnl_ratio,
            "breakeven_lock_ratio": config.breakeven_lock_ratio,
            "entry_slippage": config.entry_slippage,
            "entry_passive_offset_pct": config.entry_passive_offset_pct,
            "entry_passive_pricing_atr_fraction": config.entry_passive_pricing_atr_fraction,
            "entry_passive_pricing_min_offset_pct": config.entry_passive_pricing_min_offset_pct,
            "entry_passive_pricing_max_offset_pct": config.entry_passive_pricing_max_offset_pct,
            "entry_passive_pricing_signal_type_multipliers": config.entry_passive_pricing_signal_type_multipliers,
            "entry_passive_pricing_vwap_state_multipliers": config.entry_passive_pricing_vwap_state_multipliers,
            "entry_time_in_force": config.entry_time_in_force,
            "direct_ioc_fill_mode": config.direct_ioc_fill_mode,
            "direct_ioc_min_penetration_bps": config.direct_ioc_min_penetration_bps,
            "gtc_expire_bars": config.gtc_expire_bars,
        },
    }


def build_backtest_config(
    runtime_cfg: dict,
    config_path: str,
    initial_capital: float = 10000.0,
    fee_rate: float = 0.0004,
    data_dir: str = "data/backtest_cache",
    max_positions_override: Optional[int] = None,
    fixed_leverage: Optional[int] = None,
    profile_name: str = "",
    window_start_iso: str = "",
    window_end_iso: str = "",
) -> BacktestConfig:
    symbols = list(ConfigLoader.get_trading_symbols(runtime_cfg))
    leverage_cfg = ConfigLoader.get_leverage_settings(runtime_cfg, scope="fund_flow")
    position_limits = ConfigLoader.get_position_limits(runtime_cfg)
    fund_flow_cfg = runtime_cfg.get("fund_flow", {}) if isinstance(runtime_cfg.get("fund_flow"), dict) else {}
    risk_cfg = runtime_cfg.get("risk", {}) if isinstance(runtime_cfg.get("risk"), dict) else {}
    backtest_cfg = fund_flow_cfg.get("backtest", {}) if isinstance(fund_flow_cfg.get("backtest"), dict) else {}
    passive_pricing_cfg = backtest_cfg.get("passive_pricing", {}) if isinstance(backtest_cfg.get("passive_pricing"), dict) else {}
    explicit_window_start = str(window_start_iso or "").strip()
    explicit_window_end = str(window_end_iso or "").strip()
    decision_timeframe = str(
        fund_flow_cfg.get("decision_timeframe", fund_flow_cfg.get("signal_timeframe", "15m")) or "15m"
    ).strip().lower()
    if decision_timeframe not in {"1m", "3m", "5m", "15m"}:
        decision_timeframe = "15m"
    warmup_hours = int(backtest_cfg.get("warmup_hours", 24 if explicit_window_start else 0) or 0)
    data_window_start_iso, data_window_end_iso = resolve_replay_window_bounds(
        trade_window_start_iso=explicit_window_start,
        trade_window_end_iso=explicit_window_end,
        warmup_hours=warmup_hours,
    )

    return BacktestConfig(
        symbols=symbols,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        max_positions=max(
            1,
            int(
                max_positions_override
                if max_positions_override is not None
                else (fund_flow_cfg.get("max_active_symbols", 2) or 2)
            ),
        ),
        data_dir=data_dir,
        config_path=config_path,
        profile_name=profile_name,
        allowed_entry_hours_utc=[
            int(x) for x in (fund_flow_cfg.get("allowed_entry_hours_utc", []) or [])
            if isinstance(x, (int, float))
        ] if isinstance(fund_flow_cfg.get("allowed_entry_hours_utc", []), list) else [],
        window_start_iso=explicit_window_start,
        window_end_iso=explicit_window_end,
        data_window_start_iso=data_window_start_iso,
        data_window_end_iso=data_window_end_iso,
        warmup_hours=warmup_hours,
        decision_timeframe=decision_timeframe,
        min_position_pct=float(position_limits["min_percent"]),
        max_position_pct=float(position_limits["max_percent"]),
        reserve_pct=float(position_limits["reserve_percent"]),
        default_target_portion=float(fund_flow_cfg.get("default_target_portion", position_limits["max_percent"])),
        max_symbol_position_portion=float(fund_flow_cfg.get("max_symbol_position_portion", position_limits["max_percent"])),
        min_open_portion=float(fund_flow_cfg.get("min_open_portion", position_limits["min_percent"])),
        min_leverage=int(leverage_cfg["min_leverage"]),
        default_leverage=int(leverage_cfg["default_leverage"]),
        max_leverage=int(leverage_cfg["max_leverage"]),
        fixed_leverage=max(1, int(fixed_leverage)) if fixed_leverage is not None else None,
        default_stop_loss_pct=float(
            fund_flow_cfg.get(
                "stop_loss_pct",
                runtime_cfg.get("risk", {}).get("stop_loss_default_percent", 0.02),
            )
        ),
        default_take_profit_pct=float(
            fund_flow_cfg.get(
                "take_profit_pct",
                runtime_cfg.get("risk", {}).get("take_profit_default_percent", 0.04),
            )
        ),
        take_profit_pct_levels=[
            float(x)
            for x in (fund_flow_cfg.get("take_profit_pct_levels", []) or [])
            if float(x) > 0
        ] if isinstance(fund_flow_cfg.get("take_profit_pct_levels", []), list) else [],
        take_profit_reduce_pct_levels=[
            float(x)
            for x in (fund_flow_cfg.get("take_profit_reduce_pct_levels", []) or [])
            if float(x) > 0
        ] if isinstance(fund_flow_cfg.get("take_profit_reduce_pct_levels", []), list) else [],
        breakeven_enabled=bool(fund_flow_cfg.get("breakeven_enabled", True)),
        breakeven_trigger_pnl_ratio=float(fund_flow_cfg.get("breakeven_trigger_pnl_ratio", 0.003)),
        breakeven_lock_ratio=float(fund_flow_cfg.get("breakeven_lock_ratio", 0.001)),
        trailing_stop_enabled=bool(fund_flow_cfg.get("trailing_stop_enabled", False)),
        trailing_stop_activation_pct=float(fund_flow_cfg.get("trailing_stop_activation_pct", 0.0) or 0.0),
        trailing_stop_atr_multiplier=float(fund_flow_cfg.get("trailing_stop_atr_multiplier", 0.0) or 0.0),
        trailing_stop_min_distance=float(fund_flow_cfg.get("trailing_stop_min_distance", 0.0) or 0.0),
        trailing_stop_max_distance=float(fund_flow_cfg.get("trailing_stop_max_distance", 0.0) or 0.0),
        entry_bar_same_bar_enabled=bool(
            backtest_cfg.get("entry_bar_same_bar_enabled", False)
        ),
        same_bar_tp_priority_mode=str(
            backtest_cfg.get("same_bar_tp_priority_mode", "stop_first")
        ).strip().lower() or "stop_first",
        partial_aware_breakeven_enabled=bool(
            backtest_cfg.get("partial_aware_breakeven_enabled", False)
        ),
        partial_aware_no_partial_trigger_pnl_ratio=float(
            backtest_cfg.get("partial_aware_no_partial_trigger_pnl_ratio", 0.0)
        ),
        runner_only_trailing_enabled=bool(
            backtest_cfg.get("runner_only_trailing_enabled", False)
        ),
        runner_only_trailing_min_completed_levels=max(
            0,
            int(
                backtest_cfg.get("runner_only_trailing_min_completed_levels", 0)
            ),
        ),
        entry_slippage=float(fund_flow_cfg.get("entry_slippage", 0.0015)),
        entry_passive_offset_pct=float(
            backtest_cfg.get(
                "entry_passive_offset_pct",
                fund_flow_cfg.get(
                    "entry_passive_offset_pct",
                    fund_flow_cfg.get("entry_slippage", 0.0015),
                ),
            )
        ),
        entry_passive_pricing_atr_fraction=float(
            passive_pricing_cfg.get("atr_fraction", 0.25)
        ),
        entry_passive_pricing_min_offset_pct=float(
            passive_pricing_cfg.get("min_offset_pct", 0.0008)
        ),
        entry_passive_pricing_max_offset_pct=float(
            passive_pricing_cfg.get("max_offset_pct", 0.0060)
        ),
        entry_passive_pricing_signal_type_multipliers={
            str(k): float(v)
            for k, v in passive_pricing_cfg.get("signal_type_multipliers", {}).items()
            if isinstance(passive_pricing_cfg.get("signal_type_multipliers", {}), dict)
        } if isinstance(passive_pricing_cfg.get("signal_type_multipliers", {}), dict) else {},
        entry_passive_pricing_vwap_state_multipliers={
            str(k): float(v)
            for k, v in passive_pricing_cfg.get("vwap_state_multipliers", {}).items()
            if isinstance(passive_pricing_cfg.get("vwap_state_multipliers", {}), dict)
        } if isinstance(passive_pricing_cfg.get("vwap_state_multipliers", {}), dict) else {},
        signal_type_tp_config=dict(fund_flow_cfg.get("signal_type_tp_config", {})) if isinstance(fund_flow_cfg.get("signal_type_tp_config"), dict) else {},
        dynamic_position_config=dict(fund_flow_cfg.get("dynamic_position_config", {})) if isinstance(fund_flow_cfg.get("dynamic_position_config"), dict) else {},
        entry_time_in_force=str(
            backtest_cfg.get(
                "entry_time_in_force",
                fund_flow_cfg.get("backtest_entry_time_in_force", "IOC"),
            )
        ).upper(),
        direct_ioc_fill_mode=str(
            backtest_cfg.get("direct_ioc_fill_mode", "touch")
        ).strip().lower() or "touch",
        direct_ioc_min_penetration_bps=float(
            backtest_cfg.get("direct_ioc_min_penetration_bps", 0.0) or 0.0
        ),
        gtc_expire_bars=max(
            0,
            int(
                backtest_cfg.get(
                    "gtc_expire_bars",
                    fund_flow_cfg.get("backtest_gtc_expire_bars", 0),
                ) or 0
            ),
        ),
        gtc_cancel_on_signal_reversal=bool(backtest_cfg.get("gtc_cancel_on_signal_reversal", False)),
        open_gtc_fallback_enabled=bool(backtest_cfg.get("open_gtc_fallback_enabled", True)),
        max_consecutive_losses=max(0, int(risk_cfg.get("max_consecutive_losses", 0) or 0)),
        consecutive_loss_cooldown_seconds=max(0, int(risk_cfg.get("consecutive_loss_cooldown_seconds", 0) or 0)),
    )


def build_strategy_config(runtime_cfg: dict) -> MACDStrategyV2Config:
    ff_cfg = runtime_cfg.get("fund_flow", {}) if isinstance(runtime_cfg.get("fund_flow"), dict) else {}
    backtest_cfg = ff_cfg.get("backtest", {}) if isinstance(ff_cfg.get("backtest"), dict) else {}
    disable_cvd_decision_logic = bool(backtest_cfg.get("disable_cvd_decision_logic", True))
    return build_macd_v2_config_from_runtime(
        runtime_cfg,
        disable_cvd_decision_logic=disable_cvd_decision_logic,
    )


# ==================== 技术指标计算 ====================

def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """计算EMA"""
    return series.ewm(span=period, adjust=False).mean()


def calculate_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """计算布林带"""
    middle = series.rolling(window=period, min_periods=1).mean()
    std = series.rolling(window=period, min_periods=1).std(ddof=0).fillna(0.0)
    upper = middle + std * std_dev
    lower = middle - std * std_dev
    return middle, upper, lower


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """计算日内VWAP"""
    # 典型价格
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    # 成交量加权
    vwp = typical_price * df['volume']
    
    # 按日分组计算累计VWAP
    df_copy = df.copy()
    df_copy['date'] = pd.to_datetime(df_copy['timestamp'], unit='ms').dt.date
    
    vwap_list = []
    for date, group in df_copy.groupby('date'):
        cum_vwp = vwp[group.index].cumsum()
        cum_vol = group['volume'].cumsum()
        vwap_list.extend((cum_vwp / cum_vol).tolist())
    
    return pd.Series(vwap_list, index=df.index)


def calculate_anchored_vwap(df: pd.DataFrame, anchor: str = "weekly") -> pd.Series:
    """计算 Anchored VWAP，默认按 UTC 周锚定。"""
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    tpv = typical_price * df['volume']

    timestamp_series = df['timestamp']
    if pd.api.types.is_numeric_dtype(timestamp_series):
        ts = pd.to_datetime(timestamp_series, unit='ms', utc=True)
    else:
        ts = pd.to_datetime(timestamp_series, utc=True)

    anchor_name = str(anchor or "weekly").strip().lower()
    if anchor_name in {"weekly", "anchored_weekly", "week"}:
        anchor_key = (ts - pd.to_timedelta(ts.dt.dayofweek, unit='D')).dt.floor('D')
    else:
        anchor_key = ts.dt.floor('D')

    anchored = pd.Series(index=df.index, dtype=float)
    for _, group in df.groupby(anchor_key):
        idx = group.index
        cum_tpv = tpv.loc[idx].cumsum()
        cum_vol = group['volume'].cumsum()
        anchored.loc[idx] = cum_tpv / cum_vol.replace(0, pd.NA)
    return anchored.fillna(method='ffill').fillna(df['close'])


def calculate_rolling_vwap(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """计算滚动 VWAP。"""
    window = max(1, int(window))
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    tpv = typical_price * df['volume']
    rolling_tpv = tpv.rolling(window=window, min_periods=1).sum()
    rolling_vol = df['volume'].rolling(window=window, min_periods=1).sum()
    return (rolling_tpv / rolling_vol.replace(0, pd.NA)).fillna(df['close'])


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """计算ATR"""
    high = df['high']
    low = df['low']
    close = df['close'].shift(1)
    
    tr1 = high - low
    tr2 = abs(high - close)
    tr3 = abs(low - close)
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    
    return atr


def calculate_adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """计算ADX序列"""
    high = df['high']
    low = df['low']
    close = df['close']

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(window=period).mean()
    plus_di = 100.0 * (plus_dm.rolling(window=period).mean() / atr.replace(0, pd.NA))
    minus_di = 100.0 * (minus_dm.rolling(window=period).mean() / atr.replace(0, pd.NA))

    di_sum = (plus_di + minus_di).replace(0, pd.NA)
    dx = ((plus_di - minus_di).abs() / di_sum) * 100.0
    adx = dx.rolling(window=period).mean()
    return adx.fillna(0.0)


def prepare_timeframe_data(
    df: pd.DataFrame,
    *,
    boll_period: int = 20,
    boll_std_dev: float = 2.0,
    cvd_slope_lookback: int = 3,
    cvd_session_reset: str = "daily_utc0",
    cvd_session_lookback: int = 3,
    structural_vwap_mode: str = "anchored_weekly",
    structural_vwap_rolling_window: int = 20,
) -> pd.DataFrame:
    """准备单个时间框架数据，计算所有指标"""
    df = df.copy()
    
    # MACD
    macd_hist = calculate_macd_histogram_series(df['close'])
    df['macd_hist'] = macd_hist
    df['macd_hist_prev'] = macd_hist.shift(1)
    
    # EMA
    df['ema21'] = calculate_ema(df['close'], 21)
    df['ema55'] = calculate_ema(df['close'], 55)
    df['ema200'] = calculate_ema(df['close'], 200)

    # BOLL
    bb_middle, bb_upper, bb_lower = calculate_bollinger_bands(df['close'], boll_period, boll_std_dev)
    df['bb_middle'] = bb_middle
    df['bb_upper'] = bb_upper
    df['bb_lower'] = bb_lower
    
    # VWAP
    df['vwap'] = calculate_vwap(df)
    structural_mode = str(structural_vwap_mode or "anchored_weekly").strip().lower()
    if structural_mode in {"rolling", "rolling_vwap"}:
        df['structural_vwap'] = calculate_rolling_vwap(df, structural_vwap_rolling_window)
    else:
        anchor_name = "weekly" if structural_mode in {"anchored_weekly", "weekly", "anchored"} else structural_mode
        df['structural_vwap'] = calculate_anchored_vwap(df, anchor=anchor_name)
    
    # ATR
    df['atr'] = calculate_atr(df, 14)
    df['adx'] = calculate_adx_series(df, 14)
    df['adx_prev'] = df['adx'].shift(1).fillna(df['adx'])

    # RSI（向量化计算）
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, pd.NA)
    df['rsi'] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    # RSI 7 (15m用)
    gain_7 = delta.where(delta > 0, 0.0).rolling(window=7).mean()
    loss_7 = (-delta.where(delta < 0, 0.0)).rolling(window=7).mean()
    rs_7 = gain_7 / loss_7.replace(0, pd.NA)
    df['rsi_7'] = (100.0 - (100.0 / (1.0 + rs_7))).fillna(50.0)

    # RSI 21 (4h用)
    gain_21 = delta.where(delta > 0, 0.0).rolling(window=21).mean()
    loss_21 = (-delta.where(delta < 0, 0.0)).rolling(window=21).mean()
    rs_21 = gain_21 / loss_21.replace(0, pd.NA)
    df['rsi_21'] = (100.0 - (100.0 / (1.0 + rs_21))).fillna(50.0)

    # BOLL bandwidth (用于市场状态分类)
    boll_width = (df['bb_upper'] - df['bb_lower']) / df['bb_middle'].replace(0, pd.NA)
    df['boll_bandwidth'] = boll_width.fillna(0.05)
    df['boll_bandwidth_mean'] = df['boll_bandwidth'].rolling(window=20).mean().fillna(0.05)

    # 成交量均线
    df['avg_volume'] = df['volume'].rolling(window=20).mean()

    # CVD 代理（基于 taker buy / sell）
    taker_buy_base = df['taker_buy_base'] if 'taker_buy_base' in df.columns else pd.Series(0.0, index=df.index)
    df['taker_buy_base'] = pd.to_numeric(taker_buy_base, errors='coerce').fillna(0.0)
    df['volume'] = pd.to_numeric(df.get('volume', 0.0), errors='coerce').fillna(0.0)
    df['taker_sell_base'] = (df['volume'] - df['taker_buy_base']).clip(lower=0.0)
    df['cvd_delta_base'] = df['taker_buy_base'] - df['taker_sell_base']
    volume_safe = df['volume'].replace(0.0, pd.NA)
    df['cvd_delta_ratio'] = (df['cvd_delta_base'] / volume_safe).fillna(0.0)
    lookback = max(1, int(cvd_slope_lookback))
    df['cvd_pressure'] = df['cvd_delta_ratio'].rolling(window=lookback, min_periods=1).mean().fillna(0.0)

    timestamps = pd.to_datetime(df['timestamp'], errors='coerce')
    reset_mode = str(cvd_session_reset or "daily_utc0").strip().lower()
    if reset_mode in {"weekly", "anchored_weekly"}:
        session_key = timestamps.dt.to_period("W-MON").dt.start_time
    else:
        session_key = timestamps.dt.floor("D")
    session_lookback = max(1, int(cvd_session_lookback))
    session_cvd = df.groupby(session_key)['cvd_delta_base'].cumsum()
    session_volume = df.groupby(session_key)['volume'].cumsum().replace(0.0, pd.NA)
    df['cvd_session_ratio'] = (session_cvd / session_volume).fillna(0.0)
    df['cvd_session_pressure'] = (
        df.groupby(session_key)['cvd_delta_ratio']
        .transform(lambda s: s.rolling(window=session_lookback, min_periods=1).mean())
        .fillna(0.0)
    )
    df['cvd_session_ratio_change'] = (
        df.groupby(session_key)['cvd_session_ratio']
        .transform(lambda s: (s - s.shift(session_lookback).fillna(0.0)).fillna(0.0))
        .fillna(0.0)
    )
    df['session_price_change'] = (
        df.groupby(session_key)['close']
        .transform(lambda s: s.pct_change(periods=session_lookback).fillna(0.0))
        .fillna(0.0)
    )
    
    return df


# ==================== 数据加载 ====================

def load_symbol_data(
    data_dir: str,
    symbol: str,
    strategy_config: MACDStrategyV2Config,
    decision_timeframe: str = "15m",
) -> Optional[Dict[str, pd.DataFrame]]:
    """加载单个币种的多时间框架数据"""
    result = {}

    normalized_decision_tf = str(decision_timeframe or "15m").strip().lower()
    required_tfs = ['15m', '1h', '4h']
    if normalized_decision_tf in {"1m", "3m", "5m"} and normalized_decision_tf not in required_tfs:
        required_tfs.insert(0, normalized_decision_tf)

    for tf in required_tfs:
        # 查找文件
        pattern = f"{symbol}_{tf}_"
        matching_files = sorted(
            [
                f for f in os.listdir(data_dir)
                if f.startswith(pattern) and (f.endswith('.parquet') or f.endswith('.csv'))
            ],
            reverse=True,
        )
        
        if not matching_files:
            print(f"  [WARN] No data for {symbol} {tf}")
            return None

        # 按文件内最新时间戳选缓存，避免被旧 parquet 或旧文件名误导。
        selected_file = matching_files[0]
        selected_max_ts = pd.Timestamp.min
        for filename in matching_files:
            candidate_path = os.path.join(data_dir, filename)
            try:
                if filename.endswith('.parquet'):
                    ts_df = pd.read_parquet(candidate_path, columns=["timestamp"])
                else:
                    ts_df = pd.read_csv(candidate_path, usecols=["timestamp"])
                if ts_df.empty:
                    continue
                ts_series = pd.to_datetime(ts_df["timestamp"], errors="coerce")
                candidate_max_ts = ts_series.max()
                if pd.isna(candidate_max_ts):
                    continue
                if candidate_max_ts > selected_max_ts:
                    selected_file = filename
                    selected_max_ts = candidate_max_ts
            except Exception:
                continue

        filepath = os.path.join(data_dir, selected_file)
        if selected_file.endswith('.parquet'):
            df = pd.read_parquet(filepath)
        else:
            df = pd.read_csv(filepath)
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        
        # 准备指标
        df = prepare_timeframe_data(
            df,
            boll_period=strategy_config.boll_period,
            boll_std_dev=strategy_config.boll_std_dev,
              cvd_slope_lookback=(
                  strategy_config.cvd_1h_slope_lookback
                  if tf == '1h'
                  else strategy_config.cvd_15m_slope_lookback
              ),
              cvd_session_reset=strategy_config.cvd_veto_session_reset,
              cvd_session_lookback=(
                  strategy_config.cvd_veto_lookback_15m
                  if tf in {'1m', '3m', '5m', '15m'}
                  else strategy_config.cvd_1h_slope_lookback
              ),
              structural_vwap_mode=strategy_config.structural_vwap_mode,
              structural_vwap_rolling_window=strategy_config.structural_vwap_rolling_window,
        )
        result[tf] = df
    
    return result


def filter_market_data_by_time_range(
    market_data_map: Dict[str, Dict[str, pd.DataFrame]],
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Tuple[Dict[str, Dict[str, pd.DataFrame]], List[str]]:
    """兼容旧调用点，委托到共享 replay window 模块。"""
    return apply_market_data_window(
        market_data_map,
        start_time=start_time,
        end_time=end_time,
    )


# ==================== 回测引擎 ====================

class BacktestEngine:
    """V2.0策略回测引擎"""
    
    def __init__(self, config: BacktestConfig, strategy_config: MACDStrategyV2Config, runtime_config: Dict[str, object]):
        self.config = config
        self.strategy_config = strategy_config
        self.strategy_engine = MACDStrategyV2Engine(strategy_config)
        self.runtime_config = runtime_config
        ff_cfg = runtime_config.get("fund_flow", {}) if isinstance(runtime_config.get("fund_flow"), dict) else {}
        backtest_cfg = ff_cfg.get("backtest", {}) if isinstance(ff_cfg.get("backtest"), dict) else {}
        self.vwap_structure_overrides = (
            ff_cfg.get("vwap_structure_overrides", {})
            if isinstance(ff_cfg.get("vwap_structure_overrides"), dict)
            else {}
        )
        self.trailing_stop_profiles = (
            ff_cfg.get("trailing_stop_profiles", {})
            if isinstance(ff_cfg.get("trailing_stop_profiles"), dict)
            else {}
        )
        self.trailing_stop_profile_map = (
            ff_cfg.get("trailing_stop_profile_map", {})
            if isinstance(ff_cfg.get("trailing_stop_profile_map"), dict)
            else {}
        )
        self.disable_cvd_decision_logic = bool(backtest_cfg.get("disable_cvd_decision_logic", True))
        self.signal_override_registry = create_override_registry(runtime_config)
        self.symbol_timed_campaigns = self._load_symbol_timed_campaigns(runtime_config)
        self.symbol_timed_campaign_states: Dict[str, dict] = {
            symbol: {"stage_index": 0}
            for symbol in self.symbol_timed_campaigns
        }
        self.time_window_filter = TimeWindowFilter(
            TimeWindowFilterConfig.from_dict(
                {
                    "enabled": len(config.allowed_entry_hours_utc) > 0,
                    "allowed_hours_utc": config.allowed_entry_hours_utc,
                }
            )
        )
        
        # 账户状态
        self.capital = config.initial_capital  # free cash
        self.positions: Dict[str, dict] = {}  # symbol -> position info
        self.pending_orders: Dict[str, dict] = {}
        self.trades: List[dict] = []
        self.equity_curve: List[dict] = []
        self.max_drawdown_value: float = 0.0
        self.max_drawdown_pct: float = 0.0
        self.max_drawdown_start_time: str = ""
        self.max_drawdown_trough_time: str = ""
        self.max_drawdown_recovery_time: str = ""
        self._consecutive_losses: int = 0
        self._loss_streak_date_utc: str = ""
        self._entry_cooldown_until: Optional[pd.Timestamp] = None

    @staticmethod
    def _parse_campaign_timestamp(value: object) -> Optional[pd.Timestamp]:
        if value in {None, ""}:
            return None
        try:
            return pd.Timestamp(value)
        except Exception:
            return None

    def _load_symbol_timed_campaigns(self, runtime_config: Dict[str, object]) -> Dict[str, dict]:
        ff_cfg = runtime_config.get("fund_flow", {}) if isinstance(runtime_config.get("fund_flow"), dict) else {}
        v2_cfg = (
            ff_cfg.get("macd_mtf_strategy_v2", {})
            if isinstance(ff_cfg.get("macd_mtf_strategy_v2"), dict)
            else {}
        )
        raw_campaigns = v2_cfg.get("symbol_timed_campaigns", [])
        if isinstance(raw_campaigns, dict):
            raw_campaigns = [{"symbol": key, **value} for key, value in raw_campaigns.items() if isinstance(value, dict)]
        if not isinstance(raw_campaigns, list):
            return {}

        campaigns: Dict[str, dict] = {}
        for item in raw_campaigns:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).strip().upper()
            raw_stages = item.get("stages", [])
            if not symbol or not isinstance(raw_stages, list):
                continue

            stages: List[dict] = []
            for idx, raw_stage in enumerate(raw_stages):
                if not isinstance(raw_stage, dict):
                    continue
                side = str(raw_stage.get("side", "")).strip().lower()
                entry_start = self._parse_campaign_timestamp(raw_stage.get("entry_start"))
                entry_end = self._parse_campaign_timestamp(raw_stage.get("entry_end"))
                exit_start = self._parse_campaign_timestamp(raw_stage.get("exit_start"))
                exit_end = self._parse_campaign_timestamp(raw_stage.get("exit_end"))
                if (
                    side not in {"long", "short"}
                    or entry_start is None
                    or entry_end is None
                    or exit_start is None
                    or exit_end is None
                    or entry_end < entry_start
                    or exit_end < exit_start
                ):
                    continue
                stages.append(
                    {
                        "stage_index": idx,
                        "side": side,
                        "entry_start": entry_start,
                        "entry_end": entry_end,
                        "exit_start": exit_start,
                        "exit_end": exit_end,
                        "min_return_15m": float(raw_stage.get("min_return_15m", 0.0) or 0.0),
                        "min_return_1h": float(raw_stage.get("min_return_1h", 0.0) or 0.0),
                        "return_lookback_bars_15m": max(1, int(raw_stage.get("return_lookback_bars_15m", 1) or 1)),
                        "return_lookback_bars_1h": max(1, int(raw_stage.get("return_lookback_bars_1h", 1) or 1)),
                    }
                )

            if not stages:
                continue

            active_start = self._parse_campaign_timestamp(item.get("active_start")) or stages[0]["entry_start"]
            active_end = self._parse_campaign_timestamp(item.get("active_end")) or stages[-1]["exit_end"]
            if active_end < active_start:
                active_start = stages[0]["entry_start"]
                active_end = stages[-1]["exit_end"]

            campaigns[symbol] = {
                "symbol": symbol,
                "stages": stages,
                "campaign_start": stages[0]["entry_start"],
                "campaign_end": stages[-1]["exit_end"],
                "active_start": active_start,
                "active_end": active_end,
                "exclusive": bool(item.get("exclusive", False)),
            }

        return campaigns

    def _resolve_symbol_timed_campaign(self, symbol: str) -> Optional[dict]:
        return self.symbol_timed_campaigns.get(str(symbol or "").strip().upper())

    def _symbol_timed_campaign_state(self, symbol: str) -> dict:
        symbol_up = str(symbol or "").strip().upper()
        return self.symbol_timed_campaign_states.setdefault(symbol_up, {"stage_index": 0})

    def _build_timed_campaign_neutral_signal(
        self,
        signal: MACDSignalV2,
        *,
        stage_index: Optional[int],
        reason: str,
    ) -> MACDSignalV2:
        details = dict(signal.details or {})
        details["timed_campaign_blocked"] = True
        details["timed_campaign_reason"] = reason
        if stage_index is not None:
            details["timed_campaign_stage_index"] = int(stage_index)
        return replace(signal, direction="neutral", details=details)

    def _build_timed_campaign_forced_signal(
        self,
        signal: MACDSignalV2,
        *,
        stage: dict,
        return_15m: float,
        return_1h: float,
    ) -> MACDSignalV2:
        details = dict(signal.details or {})
        details.update(
            {
                "timed_campaign_active": True,
                "timed_campaign_stage_index": int(stage["stage_index"]),
                "timed_campaign_side": str(stage["side"]),
                "timed_campaign_return_15m": float(return_15m),
                "timed_campaign_return_1h": float(return_1h),
            }
        )
        forced_signal_type = f"timed_campaign_{stage['side']}"
        return MACDSignalV2(
            direction=str(stage["side"]),
            signal_score=max(float(signal.signal_score), float(self.strategy_config.min_signal_score), 0.95),
            signal_type_1h=forced_signal_type,
            entry_type_15m=forced_signal_type,
            entry_score_15m=1.0,
            vwap_score=float(signal.vwap_score),
            vwap_deviation=float(signal.vwap_deviation),
            vwap_state=str(signal.vwap_state or "timed_campaign"),
            vwap_location_score=float(signal.vwap_location_score),
            ema_multiplier=max(float(signal.ema_multiplier or 0.0), 1.0),
            ema_structure_status=str(signal.ema_structure_status or "normal"),
            suggested_stop_price=signal.suggested_stop_price,
            stop_loss_pct=max(float(signal.stop_loss_pct or 0.0), float(self.config.default_stop_loss_pct)),
            is_trial_entry=False,
            entry_scale=1.0,
            enhancement_score=float(signal.enhancement_score),
            is_4h_enhanced=bool(signal.is_4h_enhanced),
            details=details,
        )

    @staticmethod
    def _returns_match_timed_campaign(stage: dict, return_15m: float, return_1h: float) -> bool:
        threshold_15m = float(stage.get("min_return_15m", 0.0) or 0.0)
        threshold_1h = float(stage.get("min_return_1h", 0.0) or 0.0)
        if threshold_15m >= 0:
            return return_15m >= threshold_15m and return_1h >= threshold_1h
        return return_15m <= threshold_15m and return_1h <= -abs(threshold_1h)

    @staticmethod
    def _campaign_return_from_history(
        *,
        current_close: float,
        fallback_prev_close: Optional[float],
        close_history: Optional[List[float]],
        lookback_bars: int,
    ) -> Optional[float]:
        base_close: Optional[float] = None
        lookback = max(1, int(lookback_bars or 1))
        if isinstance(close_history, list) and len(close_history) > lookback:
            base_close = float(close_history[-(lookback + 1)])
        elif fallback_prev_close:
            base_close = float(fallback_prev_close)
        if not base_close or base_close <= 0:
            return None
        return float(current_close) / base_close - 1.0

    def _apply_symbol_timed_campaign_entry_signal(
        self,
        *,
        symbol: str,
        current_time: object,
        row_15m: pd.Series,
        prev_close_15m: Optional[float],
        row_1h: pd.Series,
        prev_close_1h: Optional[float],
        signal: MACDSignalV2,
        close_history_15m: Optional[List[float]] = None,
        close_history_1h: Optional[List[float]] = None,
    ) -> MACDSignalV2:
        campaign = self._resolve_symbol_timed_campaign(symbol)
        if campaign is None:
            return signal

        now = pd.Timestamp(current_time)
        active_start = campaign.get("active_start", campaign["campaign_start"])
        active_end = campaign.get("active_end", campaign["campaign_end"])
        if now < active_start or now > active_end:
            return signal

        state = self._symbol_timed_campaign_state(symbol)
        stage_index = int(state.get("stage_index", 0) or 0)
        completed_state = len(campaign["stages"]) * 2
        if stage_index >= completed_state:
            return self._build_timed_campaign_neutral_signal(
                signal,
                stage_index=None,
                reason="timed_campaign_completed",
            )
        if stage_index % 2 == 1:
            return self._build_timed_campaign_neutral_signal(
                signal,
                stage_index=stage_index // 2,
                reason="timed_campaign_position_open",
            )

        stage = campaign["stages"][stage_index // 2]
        if now < stage["entry_start"] or now > stage["entry_end"]:
            if bool(campaign.get("exclusive", False)):
                return self._build_timed_campaign_neutral_signal(
                    signal,
                    stage_index=stage["stage_index"],
                    reason="timed_campaign_wait_entry",
                )
            return self._build_timed_campaign_neutral_signal(
                signal,
                stage_index=stage["stage_index"],
                reason="timed_campaign_wait_entry",
            )

        if not prev_close_15m or not prev_close_1h:
            return self._build_timed_campaign_neutral_signal(
                signal,
                stage_index=stage["stage_index"],
                reason="timed_campaign_missing_warmup",
            )

        close_15m = float(row_15m.get("close", 0.0) or 0.0)
        close_1h = float(row_1h.get("close", 0.0) or 0.0)
        if close_15m <= 0 or close_1h <= 0:
            return self._build_timed_campaign_neutral_signal(
                signal,
                stage_index=stage["stage_index"],
                reason="timed_campaign_invalid_price",
            )

        return_15m = self._campaign_return_from_history(
            current_close=close_15m,
            fallback_prev_close=prev_close_15m,
            close_history=close_history_15m,
            lookback_bars=int(stage.get("return_lookback_bars_15m", 1) or 1),
        )
        return_1h = self._campaign_return_from_history(
            current_close=close_1h,
            fallback_prev_close=prev_close_1h,
            close_history=close_history_1h,
            lookback_bars=int(stage.get("return_lookback_bars_1h", 1) or 1),
        )
        if return_15m is None or return_1h is None:
            return self._build_timed_campaign_neutral_signal(
                signal,
                stage_index=stage["stage_index"],
                reason="timed_campaign_missing_return_history",
            )
        if not self._returns_match_timed_campaign(stage, return_15m, return_1h):
            return self._build_timed_campaign_neutral_signal(
                signal,
                stage_index=stage["stage_index"],
                reason="timed_campaign_threshold_block",
            )

        return self._build_timed_campaign_forced_signal(
            signal,
            stage=stage,
            return_15m=return_15m,
            return_1h=return_1h,
        )

    def _mark_symbol_timed_campaign_filled(self, symbol: str, side: str) -> None:
        campaign = self._resolve_symbol_timed_campaign(symbol)
        if campaign is None:
            return
        state = self._symbol_timed_campaign_state(symbol)
        stage_index = int(state.get("stage_index", 0) or 0)
        if stage_index % 2 == 1 or stage_index >= len(campaign["stages"]) * 2:
            return
        stage = campaign["stages"][stage_index // 2]
        if str(stage["side"]) == str(side):
            state["stage_index"] = stage_index + 1

    def _mark_symbol_timed_campaign_closed(self, symbol: str, side: str) -> None:
        campaign = self._resolve_symbol_timed_campaign(symbol)
        if campaign is None:
            return
        state = self._symbol_timed_campaign_state(symbol)
        stage_index = int(state.get("stage_index", 0) or 0)
        if stage_index % 2 == 0 or stage_index >= len(campaign["stages"]) * 2:
            return
        stage = campaign["stages"][stage_index // 2]
        if str(stage["side"]) == str(side):
            state["stage_index"] = stage_index + 1

    def _check_symbol_timed_campaign_exit(self, symbol: str, analysis: dict) -> bool:
        if symbol not in self.positions:
            return False
        campaign = self._resolve_symbol_timed_campaign(symbol)
        if campaign is None:
            return False

        state = self._symbol_timed_campaign_state(symbol)
        stage_index = int(state.get("stage_index", 0) or 0)
        if stage_index % 2 == 0 or stage_index >= len(campaign["stages"]) * 2:
            return False

        stage = campaign["stages"][stage_index // 2]
        now = pd.Timestamp(analysis["time"])
        if now < stage["exit_start"] or now > stage["exit_end"]:
            return False

        pos = self.positions.get(symbol)
        if not pos or str(pos.get("side")) != str(stage["side"]):
            return False

        self.close_position(
            symbol,
            float(analysis["price"]),
            analysis["time"],
            f"timed_campaign_exit_{stage['side']}",
        )
        return True

    def _symbol_timed_campaign_exit_window_active(self, symbol: str, current_time: object) -> bool:
        campaign = self._resolve_symbol_timed_campaign(symbol)
        if campaign is None:
            return False
        state = self._symbol_timed_campaign_state(symbol)
        stage_index = int(state.get("stage_index", 0) or 0)
        if stage_index % 2 == 0 or stage_index >= len(campaign["stages"]) * 2:
            return False
        stage = campaign["stages"][stage_index // 2]
        now = pd.Timestamp(current_time)
        return stage["exit_start"] <= now <= stage["exit_end"]

    def _resolve_vwap_structure_override(self, vwap_state: object) -> Dict[str, object]:
        state = str(vwap_state or "").strip()
        overrides = getattr(self, "vwap_structure_overrides", {})
        override = overrides.get(state, {}) if isinstance(overrides, dict) else {}
        return dict(override) if isinstance(override, dict) else {}

    def _resolve_trailing_profile(self, pos: dict) -> Dict[str, float]:
        trailing_profiles = getattr(self, "trailing_stop_profiles", {})
        trailing_profile_map = getattr(self, "trailing_stop_profile_map", {})

        # v4.0: 信号类型独立trailing stop优先
        signal_tp_trailing = pos.get('signal_tp_trailing')
        if isinstance(signal_tp_trailing, dict) and signal_tp_trailing.get("trailing_stop_enabled"):
            return {
                "activation_pnl_ratio": float(signal_tp_trailing.get("trailing_stop_activation_pct", 0.0) or 0.0),
                "atr_multiplier": float(self.config.trailing_stop_atr_multiplier or 0.0),
                "min_distance_pct": float(signal_tp_trailing.get("trailing_stop_min_distance_pct", 0.0) or 0.0),
                "max_distance_pct": float(signal_tp_trailing.get("trailing_stop_max_distance_pct", 0.0) or 0.0),
            }

        if not self.config.trailing_stop_enabled and not trailing_profiles:
            return {}
        key = str(
            trailing_profile_map.get(
                str(pos.get("vwap_state", "") or "").strip(),
                trailing_profile_map.get(
                    str(pos.get("signal_type_1h", "") or "").strip(),
                    trailing_profile_map.get("_default", ""),
                ),
            )
        ).strip()
        if key:
            profile = trailing_profiles.get(key, {})
            if isinstance(profile, dict) and profile:
                return {
                    "activation_pnl_ratio": float(profile.get("activation_pnl_ratio", 0.0) or 0.0),
                    "atr_multiplier": float(profile.get("atr_multiplier", 0.0) or 0.0),
                    "min_distance_pct": float(profile.get("min_distance_pct", 0.0) or 0.0),
                    "max_distance_pct": float(profile.get("max_distance_pct", 0.0) or 0.0),
                }
        return {
            "activation_pnl_ratio": float(self.config.trailing_stop_activation_pct or 0.0),
            "atr_multiplier": float(self.config.trailing_stop_atr_multiplier or 0.0),
            "min_distance_pct": float(self.config.trailing_stop_min_distance or 0.0),
            "max_distance_pct": float(self.config.trailing_stop_max_distance or 0.0),
        }

    @staticmethod
    def _inactive_cvd_veto_context() -> dict:
        return {
            'cvd_veto_state': 'inactive',
            'cvd_veto_triggered': False,
            'cvd_veto_reason': '',
            'cvd_delta_ratio': 0.0,
            'cvd_pressure': 0.0,
            'cvd_session_ratio': 0.0,
            'cvd_session_pressure': 0.0,
            'cvd_session_ratio_change': 0.0,
            'session_price_change': 0.0,
            'close_pos': 0.0,
            'upper_wick_ratio': 0.0,
            'structure_gap': 0.0,
        }

    @staticmethod
    def _inactive_cvd_bonus_context() -> dict:
        return {
            'cvd_bonus_state': 'inactive',
            'cvd_bonus_multiplier': 1.0,
            'cvd_1h_delta_ratio': 0.0,
            'cvd_1h_pressure': 0.0,
            'cvd_15m_delta_ratio': 0.0,
            'cvd_15m_pressure': 0.0,
        }

    def _strategy_engine_for_symbol(self, symbol: str) -> MACDStrategyV2Engine:
        override = self.signal_override_registry.get_override(symbol)
        if override is None:
            return self.strategy_engine

        updates: Dict[str, object] = {}
        if override.disable_flip_bullish is not None:
            updates["disable_flip_bullish_entries"] = bool(override.disable_flip_bullish)
            if bool(override.disable_flip_bullish):
                updates["force_disable_flip_bullish_entries"] = True
        if override.disable_green_bar_growing is not None:
            updates["disable_green_bar_growing_entries"] = bool(override.disable_green_bar_growing)
        if override.min_signal_score_override is not None:
            updates["min_signal_score"] = float(override.min_signal_score_override)
        if override.min_vwap_score_override is not None:
            updates["flip_bullish_min_vwap_score"] = float(override.min_vwap_score_override)
        if override.disable_long_dual_support is not None:
            merged_pocket_overrides = copy.deepcopy(self.strategy_config.pocket_entry_overrides)
            if not isinstance(merged_pocket_overrides, dict):
                merged_pocket_overrides = {}
            pocket_key = self.strategy_config.normalize_pocket_key("*", "long_dual_support")
            pocket_override = dict(merged_pocket_overrides.get(pocket_key, {}))
            pocket_override["disabled"] = bool(override.disable_long_dual_support)
            pocket_override.setdefault("label", "symbol_long_dual_support_override")
            merged_pocket_overrides[pocket_key] = pocket_override
            updates["pocket_entry_overrides"] = merged_pocket_overrides

        if not updates:
            return self.strategy_engine

        return MACDStrategyV2Engine(replace(self.strategy_config, **updates))

    @staticmethod
    def _timestamp_array(data: Dict[str, pd.DataFrame], tf: str) -> np.ndarray:
        cache_key = f"_ts_{tf}"
        if cache_key not in data:
            data[cache_key] = np.array(
                [BacktestEngine._timestamp_key(ts) for ts in data[tf]['timestamp'].tolist()],
                dtype=np.int64,
            )
        return data[cache_key]

    @staticmethod
    def _timestamp_key(ts: object) -> int:
        if isinstance(ts, pd.Timestamp):
            return int(ts.value)
        if isinstance(ts, np.datetime64):
            return int(pd.Timestamp(ts).value)
        return int(ts)

    def _find_tf_index(self, data: Dict[str, pd.DataFrame], tf: str, current_time: float) -> int:
        ts_array = self._timestamp_array(data, tf)
        current_key = self._timestamp_key(current_time)
        idx = int(np.searchsorted(ts_array, current_key, side='right') - 1)
        return idx if idx >= 0 else -1

    def _cancel_pending_order(self, symbol: str, reason: str = "") -> None:
        order = self.pending_orders.pop(symbol, None)
        if not order:
            return
        self.capital += float(order.get('margin', 0.0))

    @staticmethod
    def _utc_timestamp(ts: object) -> pd.Timestamp:
        stamp = pd.Timestamp(ts)
        if stamp.tzinfo is not None:
            return stamp.tz_convert("UTC")
        return stamp

    def _refresh_intraday_cooldown_state(self, current_time: object) -> None:
        ts = self._utc_timestamp(current_time)
        current_date = ts.strftime("%Y-%m-%d")
        if self._loss_streak_date_utc != current_date:
            self._loss_streak_date_utc = current_date
            self._consecutive_losses = 0
            self._entry_cooldown_until = None
            return
        if self._entry_cooldown_until is not None and ts >= self._entry_cooldown_until:
            self._entry_cooldown_until = None

    def _is_entry_cooldown_active(self, current_time: object) -> bool:
        self._refresh_intraday_cooldown_state(current_time)
        if self._entry_cooldown_until is None:
            return False
        return self._utc_timestamp(current_time) < self._entry_cooldown_until

    def _update_loss_streak_after_trade_close(self, close_time: object, total_trade_pnl: float) -> None:
        self._refresh_intraday_cooldown_state(close_time)
        if total_trade_pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        if (
            self.config.max_consecutive_losses > 0
            and self.config.consecutive_loss_cooldown_seconds > 0
            and self._consecutive_losses >= self.config.max_consecutive_losses
        ):
            self._entry_cooldown_until = self._utc_timestamp(close_time) + pd.Timedelta(
                seconds=int(self.config.consecutive_loss_cooldown_seconds)
            )

    def _entry_fill_decision(self, order: dict, row_15m: pd.Series) -> Tuple[Optional[float], dict]:
        classification = classify_passive_limit_fill(
            side=str(order.get('side', '')).lower(),
            limit_price=float(order.get('limit_price', 0.0)),
            open_price=float(row_15m['open']),
            high_price=float(row_15m['high']),
            low_price=float(row_15m['low']),
            close_price=float(row_15m['close']),
        )
        classification["direct_ioc_fill_mode"] = str(getattr(self.config, "direct_ioc_fill_mode", "touch") or "touch")
        classification["direct_ioc_min_penetration_bps"] = float(
            getattr(self.config, "direct_ioc_min_penetration_bps", 0.0) or 0.0
        )

        if not bool(classification["touched"]):
            classification["direct_ioc_fill_valid"] = False
            classification["direct_ioc_fill_reason"] = "untouched"
            return None, classification

        tif = str(order.get('time_in_force', 'IOC')).upper()
        if tif == "IOC":
            direct_fill_valid, direct_fill_reason = direct_ioc_fill_is_valid(
                classification,
                mode=str(getattr(self.config, "direct_ioc_fill_mode", "touch") or "touch"),
                min_penetration_bps=float(getattr(self.config, "direct_ioc_min_penetration_bps", 0.0) or 0.0),
            )
            classification["direct_ioc_fill_valid"] = bool(direct_fill_valid)
            classification["direct_ioc_fill_reason"] = direct_fill_reason
            if not direct_fill_valid:
                return None, classification
        else:
            classification["direct_ioc_fill_valid"] = True
            classification["direct_ioc_fill_reason"] = "non_ioc_touch"

        if bool(classification["marketable_at_open"]):
            return float(row_15m['open']), classification
        return float(order.get('limit_price', 0.0)), classification

    @staticmethod
    def _ioc_would_take_immediately(order: dict, row_15m: pd.Series) -> bool:
        order_side = str(order.get('side', '')).lower()
        limit_price = float(order.get('limit_price', 0.0))
        open_price = float(row_15m['open'])
        if order_side == 'long':
            return open_price <= limit_price
        if order_side == 'short':
            return open_price >= limit_price
        return False

    @staticmethod
    def _append_entry_degradation_step(order: dict, step: dict) -> None:
        path = order.get('entry_degradation_path')
        if not isinstance(path, list):
            path = []
        path.append(dict(step))
        order['entry_degradation_path'] = path

    def _resolve_entry_passive_offset_pct(self, analysis: dict) -> float:
        reference_price = max(float(analysis.get('price', 0.0) or 0.0), 1e-9)
        signal = analysis.get('signal')
        row_1h = analysis.get('row_1h')
        atr_value = 0.0
        if isinstance(row_1h, pd.Series):
            atr_value = float(row_1h.get('atr', 0.0) or 0.0)

        base_offset = max(
            0.0,
            abs(
                float(
                    getattr(
                        self.config,
                        "entry_passive_offset_pct",
                        getattr(self.config, "entry_slippage", 0.0015),
                    ) or 0.0
                )
            ),
        )
        atr_fraction = max(
            0.0,
            float(getattr(self.config, "entry_passive_pricing_atr_fraction", 0.25) or 0.0),
        )
        min_offset = max(
            0.0,
            float(getattr(self.config, "entry_passive_pricing_min_offset_pct", base_offset) or 0.0),
        )
        max_offset = max(
            min_offset,
            float(getattr(self.config, "entry_passive_pricing_max_offset_pct", max(base_offset, min_offset)) or 0.0),
        )

        atr_offset = 0.0
        if atr_value > 0 and reference_price > 0:
            atr_offset = (atr_value / reference_price) * atr_fraction

        signal_type = str(getattr(signal, 'signal_type_1h', '') or '')
        vwap_state = str(getattr(signal, 'vwap_state', '') or '')
        signal_type_mults = getattr(self.config, "entry_passive_pricing_signal_type_multipliers", {}) or {}
        vwap_state_mults = getattr(self.config, "entry_passive_pricing_vwap_state_multipliers", {}) or {}
        state_multiplier = 1.0
        state_multiplier *= float(signal_type_mults.get(signal_type, 1.0) or 1.0)
        state_multiplier *= float(vwap_state_mults.get(vwap_state, 1.0) or 1.0)
        state_multiplier = max(0.1, state_multiplier)

        effective_offset = max(base_offset * state_multiplier, atr_offset, min_offset)
        return min(max_offset, effective_offset)

    def _resolve_entry_limit_price_from_analysis(self, analysis: dict) -> float:
        reference_price = max(float(analysis.get('price', 0.0) or 0.0), 1e-9)
        signal = analysis.get('signal')
        side_lower = str(getattr(signal, 'direction', '') or '').lower()
        offset_pct = self._resolve_entry_passive_offset_pct(analysis)
        if side_lower == 'long':
            return reference_price * (1.0 - offset_pct)
        if side_lower == 'short':
            return reference_price * (1.0 + offset_pct)
        return reference_price

    @staticmethod
    def _stop_fill_price(pos: dict, row_15m: pd.Series, stop_price: float) -> float:
        open_price = float(row_15m['open'])
        if pos['side'] == 'long':
            return min(open_price, stop_price)
        return max(open_price, stop_price)

    @staticmethod
    def _target_fill_price(pos: dict, row_15m: pd.Series, target_price: float) -> float:
        open_price = float(row_15m['open'])
        if pos['side'] == 'long':
            return max(open_price, target_price)
        return min(open_price, target_price)

    @staticmethod
    def _normalize_tp_levels(
        price: float,
        side: str,
        pct_levels: List[float],
        reduce_levels: List[float],
    ) -> List[dict]:
        levels: List[dict] = []
        if side not in ('long', 'short'):
            return levels
        for idx, pct in enumerate(pct_levels):
            if idx >= len(reduce_levels):
                break
            reduce_pct = max(0.0, min(1.0, float(reduce_levels[idx])))
            if pct <= 0 or reduce_pct <= 0:
                continue
            level_price = price * (1.0 + pct) if side == 'long' else price * (1.0 - pct)
            levels.append(
                {
                    'price': float(level_price),
                    'reduce_pct': reduce_pct,
                    'filled': False,
                }
            )
        return levels
        
    def get_macd_hist_series(self, df: pd.DataFrame) -> np.ndarray:
        """获取MACD柱状图历史"""
        return df['macd_hist'].dropna().values

    @staticmethod
    def _bar_close_position(row: pd.Series) -> float:
        high = float(row.get('high', 0.0))
        low = float(row.get('low', 0.0))
        close = float(row.get('close', 0.0))
        price_range = max(high - low, 1e-9)
        return max(0.0, min(1.0, (close - low) / price_range))

    @staticmethod
    def _upper_wick_ratio(row: pd.Series) -> float:
        high = float(row.get('high', 0.0))
        open_price = float(row.get('open', 0.0))
        close = float(row.get('close', 0.0))
        low = float(row.get('low', 0.0))
        price_range = max(high - low, 1e-9)
        return max(0.0, min(1.0, (high - max(open_price, close)) / price_range))

    def build_cvd_veto_context(
        self,
        signal: MACDSignalV2,
        row_1h: pd.Series,
        row_15m: pd.Series,
    ) -> dict:
        """基于 session-reset CVD 判断 short_dual_pressure 的 continuation 风险。"""
        structural_vwap = float(row_1h.get('structural_vwap', 0.0))
        close_price = float(row_15m.get('close', 0.0))
        structure_gap = ((close_price - structural_vwap) / structural_vwap) if structural_vwap > 0 else 0.0
        close_pos = self._bar_close_position(row_15m)
        upper_wick_ratio = self._upper_wick_ratio(row_15m)
        context = {
            'cvd_veto_state': 'inactive',
            'cvd_veto_triggered': False,
            'cvd_veto_reason': '',
            'cvd_delta_ratio': float(row_15m.get('cvd_delta_ratio', 0.0)),
            'cvd_pressure': float(row_15m.get('cvd_pressure', 0.0)),
            'cvd_session_ratio': float(row_15m.get('cvd_session_ratio', 0.0)),
            'cvd_session_pressure': float(row_15m.get('cvd_session_pressure', 0.0)),
            'cvd_session_ratio_change': float(row_15m.get('cvd_session_ratio_change', 0.0)),
            'session_price_change': float(row_15m.get('session_price_change', 0.0)),
            'structure_gap': float(structure_gap),
            'close_pos': float(close_pos),
            'upper_wick_ratio': float(upper_wick_ratio),
        }

        if not self.strategy_config.use_cvd_veto_filter:
            return context
        if str(getattr(signal, 'direction', '') or '').strip().lower() != 'short':
            return context
        if str(getattr(signal, 'vwap_state', '') or '').strip().lower() != 'short_dual_pressure':
            return context

        delta_ratio = float(context['cvd_delta_ratio'])
        pressure = float(context['cvd_pressure'])
        session_change = float(context['cvd_session_ratio_change'])
        session_price_change = float(context['session_price_change'])

        bearish_divergence = (
            session_price_change >= float(self.strategy_config.cvd_divergence_price_change_threshold)
            and session_change <= float(self.strategy_config.cvd_divergence_session_change_threshold)
        )
        bullish_absorption = (
            delta_ratio >= float(self.strategy_config.cvd_absorption_delta_ratio_threshold)
            and structure_gap <= float(self.strategy_config.cvd_absorption_structure_gap_threshold)
            and (
                close_pos <= float(self.strategy_config.cvd_absorption_close_pos_threshold)
                or upper_wick_ratio >= float(self.strategy_config.cvd_absorption_upper_wick_ratio_threshold)
            )
        )
        bullish_continuation_risk = (
            delta_ratio >= float(self.strategy_config.cvd_veto_positive_delta_ratio_threshold)
            and pressure > float(self.strategy_config.cvd_veto_positive_pressure_threshold)
            and session_change >= float(self.strategy_config.cvd_veto_session_ratio_change_threshold)
            and session_price_change >= float(self.strategy_config.cvd_veto_session_price_change_threshold)
            and close_pos >= float(self.strategy_config.cvd_veto_strong_close_pos_threshold)
            and upper_wick_ratio <= float(self.strategy_config.cvd_veto_upper_wick_ratio_max)
            and not bearish_divergence
            and not bullish_absorption
        )

        if bullish_continuation_risk:
            context['cvd_veto_state'] = 'bullish_continuation_risk'
            context['cvd_veto_triggered'] = True
            context['cvd_veto_reason'] = 'session_reset_cvd_continuation_risk'
        elif bearish_divergence:
            context['cvd_veto_state'] = 'bearish_divergence'
        elif bullish_absorption:
            context['cvd_veto_state'] = 'bullish_absorption'
        else:
            context['cvd_veto_state'] = 'neutral_ok'

        return context

    def build_cvd_bonus_context(
        self,
        signal: MACDSignalV2,
        row_1h: pd.Series,
        row_15m: pd.Series,
    ) -> dict:
        """构建仅用于仓位 bonus 调节的 CVD 上下文，不直接阻断信号。"""
        context = {
            'cvd_bonus_state': 'inactive',
            'cvd_bonus_multiplier': 1.0,
            'cvd_1h_delta_ratio': float(row_1h.get('cvd_delta_ratio', 0.0)),
            'cvd_1h_pressure': float(row_1h.get('cvd_pressure', 0.0)),
            'cvd_15m_delta_ratio': float(row_15m.get('cvd_delta_ratio', 0.0)),
            'cvd_15m_pressure': float(row_15m.get('cvd_pressure', 0.0)),
        }

        if not self.strategy_config.use_cvd_bonus_filter:
            return context

        if str(getattr(signal, 'vwap_state', '') or '').strip().lower() != 'short_dual_pressure':
            return context

        delta_1h = float(context['cvd_1h_delta_ratio'])
        pressure_1h = float(context['cvd_1h_pressure'])
        delta_15m = float(context['cvd_15m_delta_ratio'])
        pressure_15m = float(context['cvd_15m_pressure'])
        pos_thr = float(self.strategy_config.cvd_positive_delta_ratio_threshold)
        neg_thr = float(self.strategy_config.cvd_negative_delta_ratio_threshold)

        if delta_15m >= pos_thr and pressure_15m > 0:
            context['cvd_bonus_state'] = 'bullish_flow'
            context['cvd_bonus_multiplier'] = float(self.strategy_config.cvd_bullish_bonus_multiplier)
        elif delta_15m <= neg_thr and pressure_15m < 0 and pressure_1h <= 0:
            context['cvd_bonus_state'] = 'bearish_flow'
            context['cvd_bonus_multiplier'] = float(self.strategy_config.cvd_bearish_bonus_multiplier)
        else:
            context['cvd_bonus_state'] = 'neutral_flow'
            context['cvd_bonus_multiplier'] = float(self.strategy_config.cvd_neutral_bonus_multiplier)

        return context
    
    def analyze_bar(self, symbol: str, data: Dict[str, pd.DataFrame], idx_15m: int) -> Optional[dict]:
        """分析单个K线"""
        tf_15m = data['15m']
        tf_1h = data['1h']
        tf_4h = data['4h']
        
        if idx_15m >= len(tf_15m):
            return None
        
        # 获取当前15M K线时间
        current_time = tf_15m.iloc[idx_15m]['timestamp']
        allowed_entry, _ = self.time_window_filter.should_allow_entry(timestamp=current_time, symbol=symbol)
        if not allowed_entry:
            return None
        
        # 找到对应的1H和4H索引
        idx_1h = self._find_tf_index(data, '1h', current_time) if len(tf_1h) > 0 else -1
        idx_4h = self._find_tf_index(data, '4h', current_time) if len(tf_4h) > 0 else -1
        
        if idx_1h < 0 or idx_4h < 0:
            return None
        
        # 获取MACD历史序列
        macd_hist_15m = self.get_macd_hist_series(tf_15m.iloc[:idx_15m+1])
        macd_hist_1h = self.get_macd_hist_series(tf_1h.iloc[:idx_1h+1])
        macd_hist_4h = self.get_macd_hist_series(tf_4h.iloc[:idx_4h+1])
        
        if len(macd_hist_15m) < 5 or len(macd_hist_1h) < 5 or len(macd_hist_4h) < 5:
            return None
        
        # 获取当前值
        row_15m = tf_15m.iloc[idx_15m]
        row_1h = tf_1h.iloc[idx_1h]
        row_4h = tf_4h.iloc[idx_4h]
        prev_close_15m = float(tf_15m.iloc[idx_15m - 1]['close']) if idx_15m > 0 else None
        prev_close_1h = float(tf_1h.iloc[idx_1h - 1]['close']) if idx_1h > 0 else None
        
        # 成交量比率
        volume_ratio = row_15m['volume'] / row_15m['avg_volume'] if row_15m['avg_volume'] > 0 else 1.0
        funding_rate = float(row_1h['funding_rate']) if 'funding_rate' in row_1h.index else 0.0
        oi_delta_ratio = float(row_1h['oi_delta_ratio']) if 'oi_delta_ratio' in row_1h.index else 0.0
        strategy_engine = self._strategy_engine_for_symbol(symbol)
        
        # 调用V2.0策略
        cvd_upper_wick_ratio = None
        cvd_1h_delta_ratio = None
        if not self.disable_cvd_decision_logic:
            cvd_upper_wick_ratio = float(row_15m['upper_wick_ratio']) if 'upper_wick_ratio' in row_15m.index else None
            cvd_1h_delta_ratio = float(row_1h['cvd_delta_ratio']) if 'cvd_delta_ratio' in row_1h.index else None

        signal = strategy_engine.analyze(
            macd_hist_15m=macd_hist_15m,
            macd_hist_1h=macd_hist_1h,
            macd_hist_4h=macd_hist_4h,
            idx_15m=len(macd_hist_15m) - 1,
            idx_1h=len(macd_hist_1h) - 1,
            idx_4h=len(macd_hist_4h) - 1,
            volume_ratio=volume_ratio,
            vwap=row_1h['vwap'],
            structural_vwap=row_1h['structural_vwap'] if 'structural_vwap' in row_1h.index else 0.0,
            close_price=row_1h['close'],
            bb_middle_1h=row_1h['bb_middle'] if 'bb_middle' in row_1h.index else 0.0,
            bb_upper_1h=row_1h['bb_upper'] if 'bb_upper' in row_1h.index else 0.0,
            bb_lower_1h=row_1h['bb_lower'] if 'bb_lower' in row_1h.index else 0.0,
            bb_middle_4h=row_4h['bb_middle'] if 'bb_middle' in row_4h.index else 0.0,
            bb_upper_4h=row_4h['bb_upper'] if 'bb_upper' in row_4h.index else 0.0,
            bb_lower_4h=row_4h['bb_lower'] if 'bb_lower' in row_4h.index else 0.0,
            bb_middle_15m=row_15m['bb_middle'] if 'bb_middle' in row_15m.index else 0.0,
            bb_upper_15m=row_15m['bb_upper'] if 'bb_upper' in row_15m.index else 0.0,
            bb_lower_15m=row_15m['bb_lower'] if 'bb_lower' in row_15m.index else 0.0,
            close_15m=row_15m['close'],
            close_1h_series=tf_1h.iloc[:idx_1h+1]['close'].to_numpy(),
            close_4h_series=tf_4h.iloc[:idx_4h+1]['close'].to_numpy(),
            vwap_1h_series=tf_1h.iloc[:idx_1h+1]['vwap'].to_numpy(),
            structural_vwap_1h_series=tf_1h.iloc[:idx_1h+1]['structural_vwap'].to_numpy()
            if 'structural_vwap' in tf_1h.columns else None,
            adx_1h=float(row_1h['adx']) if 'adx' in row_1h.index else 0.0,
            adx_4h=float(row_4h['adx']) if 'adx' in row_4h.index else 0.0,
            cvd_upper_wick_ratio=cvd_upper_wick_ratio,
            cvd_1h_delta_ratio=cvd_1h_delta_ratio,
            cvd_15m_delta_ratio=float(row_15m['cvd_delta_ratio']) if 'cvd_delta_ratio' in row_15m.index else None,
            atr_1h=row_1h['atr'],
            funding_rate=funding_rate,
            oi_delta_ratio=oi_delta_ratio,
            rsi_val=float(row_1h['rsi']) if 'rsi' in row_1h.index else 50.0,
            rsi_4h=float(row_4h['rsi_21'] if 'rsi_21' in row_4h.index else row_4h.get('rsi', 50.0)),
            rsi_15m=float(row_15m['rsi_7'] if 'rsi_7' in row_15m.index else row_15m.get('rsi', 50.0)),
        )
        if self.disable_cvd_decision_logic:
            cvd_veto_context = self._inactive_cvd_veto_context()
            cvd_context = self._inactive_cvd_bonus_context()
        else:
            cvd_veto_context = self.build_cvd_veto_context(signal, row_1h, row_15m)
            if cvd_veto_context.get('cvd_veto_triggered'):
                veto_details = dict(signal.details or {})
                veto_details.update(cvd_veto_context)
                signal = strategy_engine._neutral_signal(
                    reason='cvd_v71_veto',
                    score=float(signal.signal_score),
                    details=veto_details,
                    veto_type=VetoType.CVD_CONTINUATION_RISK,
                    veto_reason=str(cvd_veto_context.get('cvd_veto_reason', '')),
                    signal_type_1h=signal.signal_type_1h,
                    entry_type_15m=signal.entry_type_15m,
                    entry_score_15m=signal.entry_score_15m,
                    vwap_score=signal.vwap_score,
                    vwap_deviation=signal.vwap_deviation,
                    vwap_state=signal.vwap_state,
                    vwap_location_score=signal.vwap_location_score,
                    ema_multiplier=signal.ema_multiplier,
                    ema_structure_status=signal.ema_structure_status,
                    enhancement_score=signal.enhancement_score,
                    is_4h_enhanced=signal.is_4h_enhanced,
                )
            else:
                signal.details = dict(signal.details or {})
                signal.details.update(cvd_veto_context)

            cvd_context = self.build_cvd_bonus_context(signal, row_1h, row_15m)

        signal = self._apply_symbol_timed_campaign_entry_signal(
            symbol=symbol,
            current_time=current_time,
            row_15m=row_15m,
            prev_close_15m=prev_close_15m,
            row_1h=row_1h,
            prev_close_1h=prev_close_1h,
            signal=signal,
            close_history_15m=tf_15m.iloc[:idx_15m + 1]['close'].tolist(),
            close_history_1h=tf_1h.iloc[:idx_1h + 1]['close'].tolist(),
        )
        
        return {
            'signal': signal,
            'time': current_time,
            'price': row_15m['close'],
            'idx_15m': idx_15m,
            'row_15m': row_15m,
            'row_1h': row_1h,
            'row_4h': row_4h,
            'cvd_veto_context': cvd_veto_context,
            'cvd_context': cvd_context,
        }
    
    def calculate_position_size(
        self,
        symbol: str,
        signal_score: float,
        price: float,
        ema_multiplier: float,
        signal_type_1h: str | None = None,
        vwap_score: float = 0.0,
        vwap_state: str | None = None,
        bonus_multiplier: float = 1.0,
        is_trial_entry: bool = False,
        entry_scale: float = 1.0,
        session_scale: float = 1.0,
        signal_details: Optional[dict] = None,
    ) -> Tuple[float, int]:
        """计算仓位大小和杠杆"""
        signal_details = signal_details if isinstance(signal_details, dict) else {}
        entry_tier = str(signal_details.get("entry_tier", "") or "")
        if self.config.fixed_leverage is not None:
            leverage = int(self.config.fixed_leverage)
        else:
            leverage = self.strategy_engine.calculate_leverage(
                signal_score,
                ema_multiplier,
                signal_type_1h,
                symbol=symbol,
                is_trial_entry=is_trial_entry,
                entry_tier=entry_tier,
            )
            leverage = max(self.config.min_leverage, min(self.config.max_leverage, leverage or self.config.default_leverage))
        position_pct = self.strategy_engine.calculate_position_portion(
            score=signal_score,
            base_default_portion=self.config.default_target_portion,
            base_max_symbol_position_portion=self.config.max_symbol_position_portion,
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
        if position_pct < self.config.min_open_portion:
            return 0.0, leverage

        deployable_capital = self.capital * max(0.0, 1.0 - self.config.reserve_pct)
        position_value = deployable_capital * position_pct

        return position_value, leverage

    def _get_dynamic_position_multiplier(self) -> float:
        """v4.0: 基于近期交易盈亏动态调整仓位"""
        cfg = self.config.dynamic_position_config
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            return 1.0
        lookback = max(1, int(cfg.get("lookback_trades", 5) or 5))
        recent = self.trades[-lookback:] if len(self.trades) >= 3 else []
        if not recent:
            return 1.0
        wins = sum(1 for t in recent if float(t.get("pnl", 0.0)) > 0)
        total = len(recent)
        losses = total - wins
        if wins >= 4:
            mult = float(cfg.get("win_4_of_5_multiplier", 1.0))
        elif wins >= 3:
            mult = float(cfg.get("win_3_of_5_multiplier", 1.0))
        elif losses >= 4:
            mult = float(cfg.get("loss_4_of_5_multiplier", 1.0))
        elif losses >= 3:
            mult = float(cfg.get("loss_3_of_5_multiplier", 1.0))
        else:
            mult = 1.0
        return max(
            float(cfg.get("min_multiplier", 0.5)),
            min(float(cfg.get("max_multiplier", 1.3)), mult),
        )

    def _signal_threshold(self, signal: MACDSignalV2) -> float:
        if bool(getattr(signal, 'is_trial_entry', False)):
            return float(self.strategy_config.preflip_trial_min_signal_score)
        return self.strategy_config.resolve_signal_score_threshold(getattr(signal, 'signal_type_1h', None))
    
    def execute_trade(self, symbol: str, analysis: dict, data: Dict[str, pd.DataFrame]):
        """在当前bar收盘后生成待成交入场订单，下一根bar开始撮合"""
        signal = analysis['signal']
        price = analysis['price']
        time = analysis['time']
        if self.disable_cvd_decision_logic:
            cvd_veto_context = self._inactive_cvd_veto_context()
            cvd_context = self._inactive_cvd_bonus_context()
        else:
            cvd_veto_context = analysis.get('cvd_veto_context') or {}
            cvd_context = analysis.get('cvd_context') or {}
        
        # 无信号或信号分数不足
        if signal.direction == 'neutral':
            return
        
        # 严格检查信号分数阈值
        if signal.signal_score < self._signal_threshold(signal):
            return

        if symbol in self.positions or symbol in self.pending_orders:
            return

        if self._is_entry_cooldown_active(time):
            return

        if (len(self.positions) + len(self.pending_orders)) >= self.config.max_positions:
            return

        strategy_engine = self._strategy_engine_for_symbol(symbol)
        session_position_scale = strategy_engine.resolve_session_position_scale(
            time,
            signal.signal_type_1h,
            signal.vwap_state,
        )
        effective_symbol_session_scale = strategy_engine.resolve_symbol_risk_session_scale(
            symbol,
            session_position_scale,
        )
        vwap_structure_override = self._resolve_vwap_structure_override(signal.vwap_state)
        
        # 计算目标保证金与杠杆
        position_value, leverage = self.calculate_position_size(
            symbol=symbol,
            signal_score=signal.signal_score,
            price=price,
            ema_multiplier=signal.ema_multiplier,
            signal_type_1h=signal.signal_type_1h,
            vwap_score=signal.vwap_score,
            vwap_state=signal.vwap_state,
            bonus_multiplier=1.0 if self.disable_cvd_decision_logic else float(cvd_context.get('cvd_bonus_multiplier', 1.0)),
            is_trial_entry=bool(signal.is_trial_entry),
            entry_scale=float(signal.entry_scale or 1.0),
            session_scale=session_position_scale,
            signal_details=signal.details if isinstance(signal.details, dict) else {},
        )
        # VWAP结构覆盖：杠杆上限
        max_lev_override = vwap_structure_override.get("max_leverage_override")
        if max_lev_override is not None and isinstance(max_lev_override, (int, float)) and max_lev_override > 0:
            leverage = min(leverage, int(max_lev_override))
        position_scale_override = max(
            0.0,
            min(1.0, float(vwap_structure_override.get("position_scale_override", 1.0) or 1.0)),
        )
        position_value *= position_scale_override

        # v4.0: 动态仓位系统
        dynamic_mult = self._get_dynamic_position_multiplier()
        position_value *= dynamic_mult

        if position_value <= 0:
            return
        
        # 计算止损价（使用V2.0动态止损）
        if signal.suggested_stop_price and signal.suggested_stop_price > 0:
            stop_price = signal.suggested_stop_price
        else:
            # 备用：使用ATR或固定比例
            atr = analysis['row_1h']['atr']
            stop_loss_pct_override = vwap_structure_override.get("stop_loss_pct_override")
            if stop_loss_pct_override is not None:
                stop_loss_pct = float(stop_loss_pct_override)
            else:
                stop_loss_pct = max(float(signal.stop_loss_pct or 0.0), self.config.default_stop_loss_pct)
            if signal.direction == 'long':
                stop_price = max(price - atr * 1.5, price * (1 - stop_loss_pct))
            else:
                stop_price = min(price + atr * 1.5, price * (1 + stop_loss_pct))

        take_profit_pct_override = vwap_structure_override.get("take_profit_pct_override")
        take_profit_pct = float(
            take_profit_pct_override
            if take_profit_pct_override is not None
            else self.config.default_take_profit_pct
        )
        take_profit = None
        if take_profit_pct > 0:
            if signal.direction == 'long':
                take_profit = price * (1 + take_profit_pct)
            else:
                take_profit = price * (1 - take_profit_pct)

        # v4.0: 信号类型独立TP配置
        st_tp_cfg = self.config.signal_type_tp_config
        st_key = str(getattr(signal, 'signal_type_1h', '') or '').strip()
        signal_tp_override = None
        if st_tp_cfg.get("enabled") and st_key and st_key in st_tp_cfg:
            signal_tp_override = st_tp_cfg[st_key]

        # 优先级: vwap_structure_override > signal_type_tp > global
        _fallback_pct_levels = self.config.take_profit_pct_levels
        _fallback_reduce_levels = self.config.take_profit_reduce_pct_levels
        if signal_tp_override and isinstance(signal_tp_override, dict):
            st_pct = signal_tp_override.get("take_profit_pct_levels")
            st_reduce = signal_tp_override.get("take_profit_reduce_pct_levels")
            if isinstance(st_pct, list) and st_pct:
                _fallback_pct_levels = [float(x) for x in st_pct if x > 0]
            if isinstance(st_reduce, list) and st_reduce:
                _fallback_reduce_levels = [float(x) for x in st_reduce if x > 0]

        take_profit_levels = self._normalize_tp_levels(
            price=price,
            side=signal.direction,
            pct_levels=vwap_structure_override.get(
                "take_profit_pct_levels_override",
                _fallback_pct_levels,
            ),
            reduce_levels=vwap_structure_override.get(
                "take_profit_reduce_pct_levels_override",
                _fallback_reduce_levels,
            ),
        )
        default_breakeven_trigger = float(getattr(self.config, "breakeven_trigger_pnl_ratio", 0.0) or 0.0)
        default_breakeven_lock = float(getattr(self.config, "breakeven_lock_ratio", 0.0) or 0.0)
        breakeven_trigger_pnl_ratio = float(
            vwap_structure_override.get(
                "breakeven_trigger_pnl_ratio_override",
                default_breakeven_trigger,
            ) or default_breakeven_trigger
        )
        breakeven_lock_ratio = float(
            vwap_structure_override.get(
                "breakeven_lock_ratio_override",
                default_breakeven_lock,
            ) or default_breakeven_lock
        )

        required_margin = position_value
        estimated_entry_fee = position_value * leverage * self.config.fee_rate
        total_required = required_margin + estimated_entry_fee
        if self.capital < total_required:
            max_affordable_margin = self.capital / max(1.0, (1.0 + leverage * self.config.fee_rate))
            position_value = min(position_value, max_affordable_margin)
            required_margin = position_value
            if required_margin < 100:
                return

        limit_price = self._resolve_entry_limit_price_from_analysis(analysis)

        self.capital -= required_margin
        self.pending_orders[symbol] = {
            'side': signal.direction,
            'limit_price': limit_price,
            'margin': required_margin,
            'position_value': required_margin,
            'leverage': leverage,
            'stop_price': stop_price,
            'take_profit': take_profit,
            'take_profit_levels': take_profit_levels,
            'signal_tp_trailing': signal_tp_override,
            'dynamic_position_mult': float(dynamic_mult),
            'submit_time': time,
            'time_in_force': self.config.entry_time_in_force,
            'entry_initial_time_in_force': self.config.entry_time_in_force,
            'bars_waited': 0,
            'signal_score': signal.signal_score,
            'signal_type_1h': signal.signal_type_1h,
            'is_trial_entry': bool(signal.is_trial_entry),
            'entry_scale': float(signal.entry_scale or 1.0),
            'session_position_scale': float(session_position_scale),
            'effective_session_position_scale': float(effective_symbol_session_scale),
            'watchlist_throttle_applied': bool(strategy_engine.is_watchlist_symbol(symbol)),
            'vwap_score': signal.vwap_score,
            'vwap_state': signal.vwap_state,
            'vwap_location_score': signal.vwap_location_score,
            'ema_multiplier': signal.ema_multiplier,
            'ema_status': signal.ema_structure_status,
            'adx_1h': float((signal.details or {}).get('adx_1h', 0.0)),
            'adx_4h': float((signal.details or {}).get('adx_4h', 0.0)),
            'bb_middle_slope_1h': float((signal.details or {}).get('bb_middle_slope_1h', 0.0)),
            'bb_middle_slope_4h': float((signal.details or {}).get('bb_middle_slope_4h', 0.0)),
            'cvd_veto_state': str(cvd_veto_context.get('cvd_veto_state', 'inactive')),
            'cvd_veto_triggered': bool(cvd_veto_context.get('cvd_veto_triggered', False)),
            'cvd_veto_reason': str(cvd_veto_context.get('cvd_veto_reason', '')),
            'cvd_session_ratio': float(cvd_veto_context.get('cvd_session_ratio', 0.0)),
            'cvd_session_pressure': float(cvd_veto_context.get('cvd_session_pressure', 0.0)),
            'cvd_session_ratio_change': float(cvd_veto_context.get('cvd_session_ratio_change', 0.0)),
            'session_price_change': float(cvd_veto_context.get('session_price_change', 0.0)),
            'cvd_close_pos': float(cvd_veto_context.get('close_pos', 0.0)),
            'cvd_upper_wick_ratio': float(cvd_veto_context.get('upper_wick_ratio', 0.0)),
            'cvd_structure_gap': float(cvd_veto_context.get('structure_gap', 0.0)),
            'cvd_bonus_state': str(cvd_context.get('cvd_bonus_state', 'inactive')),
            'cvd_bonus_multiplier': float(cvd_context.get('cvd_bonus_multiplier', 1.0)),
            'cvd_1h_delta_ratio': float(cvd_context.get('cvd_1h_delta_ratio', 0.0)),
            'cvd_1h_pressure': float(cvd_context.get('cvd_1h_pressure', 0.0)),
            'cvd_15m_delta_ratio': float(cvd_context.get('cvd_15m_delta_ratio', 0.0)),
            'cvd_15m_pressure': float(cvd_context.get('cvd_15m_pressure', 0.0)),
            'shrink_exit_direction': str((signal.details or {}).get('shrink_exit_direction', '')),
            'shrink_exit_ready': bool((signal.details or {}).get('shrink_exit_ready', False)),
            'macd_4h_shrink_pct': float((signal.details or {}).get('macd_4h_shrink_pct', 0.0)),
            'macd_4h_shrink_bars': int((signal.details or {}).get('macd_4h_shrink_bars', 0) or 0),
            'breakeven_trigger_pnl_ratio': breakeven_trigger_pnl_ratio,
            'breakeven_lock_ratio': breakeven_lock_ratio,
            'position_scale_override': position_scale_override,
            'vwap_structure_override_applied': bool(vwap_structure_override),
            'entry_degradation_path': [],
            'timed_campaign_force_fill': bool((signal.details or {}).get('timed_campaign_active', False)),
            'timed_campaign_hold_until_exit': bool((signal.details or {}).get('timed_campaign_active', False)),
        }
    
    def close_position(
        self,
        symbol: str,
        price: float,
        time: float,
        reason: str,
        *,
        reduce_margin: Optional[float] = None,
        reduce_pct_original: Optional[float] = None,
    ):
        """平仓/减仓"""
        if symbol not in self.positions:
            return
        
        pos = self.positions[symbol]
        current_margin = float(pos['margin'])
        if current_margin <= 0:
            del self.positions[symbol]
            return

        initial_margin = float(pos.get('initial_margin', current_margin))
        requested_reduce_margin = reduce_margin
        if requested_reduce_margin is None and reduce_pct_original is not None:
            requested_reduce_margin = initial_margin * max(0.0, min(1.0, float(reduce_pct_original)))
        if requested_reduce_margin is None:
            requested_reduce_margin = current_margin
        requested_reduce_margin = max(0.0, float(requested_reduce_margin))
        if requested_reduce_margin <= 0:
            return

        realized_margin = min(current_margin, requested_reduce_margin)
        realized_fraction_current = realized_margin / current_margin if current_margin > 0 else 1.0
        remaining_margin = max(0.0, current_margin - realized_margin)
        
        # 计算盈亏（考虑杠杆）
        if pos['side'] == 'long':
            pnl_pct = (price - pos['entry_price']) / pos['entry_price']
        else:
            pnl_pct = (pos['entry_price'] - price) / pos['entry_price']
        
        # 实际盈亏 = 仓位价值 × 杠杆 × 收益率
        pnl = realized_margin * pos['leverage'] * pnl_pct
        
        # 扣除平仓手续费
        fee = realized_margin * pos['leverage'] * self.config.fee_rate
        pnl -= fee
        
        # 释放保证金并回收已实现盈亏
        self.capital += realized_margin + pnl

        realized_position_value = float(pos['position_value']) * realized_fraction_current
        realized_entry_notional = float(pos['entry_notional']) * realized_fraction_current
        realized_pnl_accum = float(pos.get('realized_pnl_accum', 0.0) or 0.0)
        
        # 记录交易
        self.trades.append({
            'symbol': symbol,
            'side': pos['side'],
            'entry_price': pos['entry_price'],
            'exit_price': price,
            'entry_time': pos['entry_time'],
            'exit_time': time,
            'entry_initial_time_in_force': str(pos.get('entry_initial_time_in_force', pos.get('entry_time_in_force', 'IOC'))),
            'entry_time_in_force': str(pos.get('entry_time_in_force', 'IOC')),
            'entry_degradation_path': list(pos.get('entry_degradation_path') or []),
            'leverage': pos['leverage'],
            'position_value': realized_position_value,
            'margin': realized_margin,
            'entry_notional': realized_entry_notional,
            'pnl': pnl,
            'pnl_pct': pnl_pct * 100,
            'reason': reason,
            'signal_score': pos['signal_score'],
            'signal_type_1h': pos['signal_type_1h'],
            'entry_tier': str(pos.get('entry_tier', '')),
            'capacity_replacement_candidate': bool(pos.get('capacity_replacement_candidate', False)),
            'capacity_replacement_target': str(pos.get('capacity_replacement_target', '') or ''),
            'is_trial_entry': bool(pos.get('is_trial_entry', False)),
            'entry_scale': float(pos.get('entry_scale', 1.0)),
            'session_position_scale': float(pos.get('session_position_scale', 1.0)),
            'vwap_score': pos['vwap_score'],
            'vwap_state': pos.get('vwap_state', ''),
            'vwap_location_score': float(pos.get('vwap_location_score', 0.0)),
            'ema_multiplier': pos['ema_multiplier'],
            'adx_1h': float(pos.get('adx_1h', 0.0)),
            'adx_4h': float(pos.get('adx_4h', 0.0)),
            'bb_middle_slope_1h': float(pos.get('bb_middle_slope_1h', 0.0)),
            'bb_middle_slope_4h': float(pos.get('bb_middle_slope_4h', 0.0)),
            'cvd_veto_state': str(pos.get('cvd_veto_state', 'inactive')),
            'cvd_veto_triggered': bool(pos.get('cvd_veto_triggered', False)),
            'cvd_veto_reason': str(pos.get('cvd_veto_reason', '')),
            'cvd_session_ratio': float(pos.get('cvd_session_ratio', 0.0)),
            'cvd_session_pressure': float(pos.get('cvd_session_pressure', 0.0)),
            'cvd_session_ratio_change': float(pos.get('cvd_session_ratio_change', 0.0)),
            'session_price_change': float(pos.get('session_price_change', 0.0)),
            'cvd_close_pos': float(pos.get('cvd_close_pos', 0.0)),
            'cvd_upper_wick_ratio': float(pos.get('cvd_upper_wick_ratio', 0.0)),
            'cvd_structure_gap': float(pos.get('cvd_structure_gap', 0.0)),
            'cvd_bonus_state': str(pos.get('cvd_bonus_state', 'inactive')),
            'cvd_bonus_multiplier': float(pos.get('cvd_bonus_multiplier', 1.0)),
            'cvd_1h_delta_ratio': float(pos.get('cvd_1h_delta_ratio', 0.0)),
            'cvd_1h_pressure': float(pos.get('cvd_1h_pressure', 0.0)),
            'cvd_15m_delta_ratio': float(pos.get('cvd_15m_delta_ratio', 0.0)),
            'cvd_15m_pressure': float(pos.get('cvd_15m_pressure', 0.0)),
            'shrink_exit_direction': str(pos.get('shrink_exit_direction', '')),
            'shrink_exit_ready': bool(pos.get('shrink_exit_ready', False)),
            'macd_4h_shrink_pct': float(pos.get('macd_4h_shrink_pct', 0.0)),
            'macd_4h_shrink_bars': int(pos.get('macd_4h_shrink_bars', 0) or 0),
            'entry_fill_touch_class': str(pos.get('entry_fill_touch_class', '')),
            'entry_fill_close_through': bool(pos.get('entry_fill_close_through', False)),
            'entry_fill_wick_only_touch': bool(pos.get('entry_fill_wick_only_touch', False)),
            'entry_fill_penetration_bps': float(pos.get('entry_fill_penetration_bps', 0.0)),
            'entry_fill_range_bps': float(pos.get('entry_fill_range_bps', 0.0)),
            'entry_fill_penetration_share_of_range': float(pos.get('entry_fill_penetration_share_of_range', 0.0)),
            'entry_fill_direct_ioc_valid': bool(pos.get('entry_fill_direct_ioc_valid', False)),
            'entry_fill_direct_ioc_reason': str(pos.get('entry_fill_direct_ioc_reason', '')),
            'dynamic_position_mult': float(pos.get('dynamic_position_mult', 1.0)),
            'remaining_margin_after': remaining_margin,
        })

        total_trade_pnl = realized_pnl_accum + pnl
        if remaining_margin <= 1e-12:
            self._update_loss_streak_after_trade_close(time, total_trade_pnl)
            self._mark_symbol_timed_campaign_closed(symbol, str(pos.get('side', '')))
            del self.positions[symbol]
            return

        pos['margin'] = remaining_margin
        pos['position_value'] = max(0.0, float(pos['position_value']) - realized_position_value)
        pos['entry_notional'] = max(0.0, float(pos['entry_notional']) - realized_entry_notional)
        pos['remaining_fraction'] = remaining_margin / initial_margin if initial_margin > 0 else 0.0
        pos['realized_pnl_accum'] = total_trade_pnl

    def _fill_pending_order(
        self,
        symbol: str,
        order: dict,
        fill_price: float,
        fill_time: float,
        fill_details: Optional[dict] = None,
    ) -> bool:
        entry_fee = float(order['margin']) * float(order['leverage']) * self.config.fee_rate
        if self.capital < entry_fee:
            self._cancel_pending_order(symbol, reason="insufficient_fee_cash")
            return False

        self.capital -= entry_fee
        self.positions[symbol] = {
            'side': order['side'],
            'entry_price': fill_price,
            'entry_notional': float(order['margin']) * float(order['leverage']),
            'position_value': float(order['position_value']),
            'margin': float(order['margin']),
            'initial_margin': float(order['margin']),
            'remaining_fraction': 1.0,
            'leverage': int(order['leverage']),
            'stop_price': float(order['stop_price']),
            'take_profit': order['take_profit'],
            'take_profit_levels': list(order.get('take_profit_levels') or []),
            'signal_tp_trailing': order.get('signal_tp_trailing'),
            'dynamic_position_mult': float(order.get('dynamic_position_mult', 1.0)),
            'entry_time': fill_time,
            'entry_initial_time_in_force': str(order.get('entry_initial_time_in_force', order.get('time_in_force', 'IOC'))),
            'entry_time_in_force': str(order.get('time_in_force', 'IOC')),
            'entry_degradation_path': list(order.get('entry_degradation_path') or []),
            'signal_score': float(order['signal_score']),
            'signal_type_1h': order['signal_type_1h'],
            'entry_tier': str(order.get('entry_tier', '')),
            'capacity_replacement_candidate': bool(order.get('capacity_replacement_candidate', False)),
            'capacity_replacement_target': str(order.get('capacity_replacement_target', '') or ''),
            'pocket_management_override': dict(order.get('pocket_management_override', {}))
            if isinstance(order.get('pocket_management_override'), dict)
            else {},
            'is_trial_entry': bool(order.get('is_trial_entry', False)),
            'entry_scale': float(order.get('entry_scale', 1.0)),
            'session_position_scale': float(order.get('session_position_scale', 1.0)),
            'vwap_score': float(order['vwap_score']),
            'vwap_state': order.get('vwap_state', ''),
            'vwap_location_score': float(order.get('vwap_location_score', 0.0)),
            'ema_multiplier': float(order['ema_multiplier']),
            'ema_status': order['ema_status'],
            'adx_1h': float(order.get('adx_1h', 0.0)),
            'adx_4h': float(order.get('adx_4h', 0.0)),
            'bb_middle_slope_1h': float(order.get('bb_middle_slope_1h', 0.0)),
            'bb_middle_slope_4h': float(order.get('bb_middle_slope_4h', 0.0)),
            'cvd_veto_state': str(order.get('cvd_veto_state', 'inactive')),
            'cvd_veto_triggered': bool(order.get('cvd_veto_triggered', False)),
            'cvd_veto_reason': str(order.get('cvd_veto_reason', '')),
            'cvd_session_ratio': float(order.get('cvd_session_ratio', 0.0)),
            'cvd_session_pressure': float(order.get('cvd_session_pressure', 0.0)),
            'cvd_session_ratio_change': float(order.get('cvd_session_ratio_change', 0.0)),
            'session_price_change': float(order.get('session_price_change', 0.0)),
            'cvd_close_pos': float(order.get('cvd_close_pos', 0.0)),
            'cvd_upper_wick_ratio': float(order.get('cvd_upper_wick_ratio', 0.0)),
            'cvd_structure_gap': float(order.get('cvd_structure_gap', 0.0)),
            'cvd_bonus_state': str(order.get('cvd_bonus_state', 'inactive')),
            'cvd_bonus_multiplier': float(order.get('cvd_bonus_multiplier', 1.0)),
            'cvd_1h_delta_ratio': float(order.get('cvd_1h_delta_ratio', 0.0)),
            'cvd_1h_pressure': float(order.get('cvd_1h_pressure', 0.0)),
            'cvd_15m_delta_ratio': float(order.get('cvd_15m_delta_ratio', 0.0)),
            'cvd_15m_pressure': float(order.get('cvd_15m_pressure', 0.0)),
            'shrink_exit_direction': str(order.get('shrink_exit_direction', '')),
            'shrink_exit_ready': bool(order.get('shrink_exit_ready', False)),
            'macd_4h_shrink_pct': float(order.get('macd_4h_shrink_pct', 0.0)),
            'macd_4h_shrink_bars': int(order.get('macd_4h_shrink_bars', 0) or 0),
            'breakeven_trigger_pnl_ratio': float(order.get('breakeven_trigger_pnl_ratio', self.config.breakeven_trigger_pnl_ratio)),
            'breakeven_lock_ratio': float(order.get('breakeven_lock_ratio', self.config.breakeven_lock_ratio)),
            'position_scale_override': float(order.get('position_scale_override', 1.0)),
            'vwap_structure_override_applied': bool(order.get('vwap_structure_override_applied', False)),
            'entry_fill_touch_class': str((fill_details or {}).get('touch_class', '')),
            'entry_fill_close_through': bool((fill_details or {}).get('close_through', False)),
            'entry_fill_wick_only_touch': bool((fill_details or {}).get('wick_only_touch', False)),
            'entry_fill_penetration_bps': float((fill_details or {}).get('penetration_bps', 0.0) or 0.0),
            'entry_fill_range_bps': float((fill_details or {}).get('range_bps', 0.0) or 0.0),
            'entry_fill_penetration_share_of_range': float(
                (fill_details or {}).get('penetration_share_of_range', 0.0) or 0.0
            ),
            'entry_fill_direct_ioc_valid': bool((fill_details or {}).get('direct_ioc_fill_valid', False)),
            'entry_fill_direct_ioc_reason': str((fill_details or {}).get('direct_ioc_fill_reason', '')),
            'trailing_stop': None,
            'realized_pnl_accum': 0.0,
            'timed_campaign_hold_until_exit': bool(order.get('timed_campaign_hold_until_exit', False)),
        }
        self.pending_orders.pop(symbol, None)
        self._mark_symbol_timed_campaign_filled(symbol, str(order.get('side', '')))
        return True

    def process_pending_orders(self, analyses: Dict[str, dict]) -> set[str]:
        filled_symbols: set[str] = set()
        for symbol in list(self.pending_orders.keys()):
            order = self.pending_orders.get(symbol)
            if not order:
                continue

            analysis = analyses.get(symbol)
            if analysis is None:
                continue

            signal = analysis['signal']
            tif = str(order.get('time_in_force', 'IOC')).upper()
            order['bars_waited'] = int(order.get('bars_waited', 0)) + 1
            timed_campaign_force_fill = bool(order.get('timed_campaign_force_fill', False))

            if symbol in self.positions:
                self._cancel_pending_order(symbol, reason="position_exists")
                continue

            if timed_campaign_force_fill:
                fill_price = float(analysis['row_15m']['open'])
                fill_details = {
                    'touch_class': 'timed_campaign_next_open',
                    'close_through': False,
                    'wick_only_touch': False,
                    'penetration_bps': 0.0,
                    'range_bps': 0.0,
                    'penetration_share_of_range': 0.0,
                    'direct_ioc_fill_valid': True,
                    'direct_ioc_fill_reason': 'timed_campaign_force_fill',
                }
                if self._fill_pending_order(symbol, order, fill_price, analysis['time'], fill_details=fill_details):
                    filled_symbols.add(symbol)
                    if self.config.entry_bar_same_bar_enabled and symbol in self.positions:
                        self._handle_entry_bar_same_bar_after_fill(symbol, analysis)
                continue

            if signal.direction == 'neutral' or signal.signal_score < self._signal_threshold(signal):
                if tif == "IOC":
                    self._cancel_pending_order(symbol, reason="ioc_no_fill")
                elif self.config.gtc_expire_bars > 0 and int(order['bars_waited']) >= self.config.gtc_expire_bars:
                    self._cancel_pending_order(symbol, reason="gtc_signal_expired")
                continue

            if str(signal.direction) != str(order.get('side')):
                if tif == "IOC" or bool(getattr(self.config, "gtc_cancel_on_signal_reversal", False)):
                    self._cancel_pending_order(symbol, reason="signal_reversed")
                elif self.config.gtc_expire_bars > 0 and int(order['bars_waited']) >= self.config.gtc_expire_bars:
                    self._cancel_pending_order(symbol, reason="gtc_signal_reversed_timeout")
                continue

            if tif == "IOC" and self._ioc_would_take_immediately(order, analysis['row_15m']):
                if bool(getattr(self.config, "open_gtc_fallback_enabled", True)):
                    self._append_entry_degradation_step(
                        order,
                        {
                            'step': 'ioc_to_gtc_fallback',
                            'time': pd.Timestamp(analysis['time']).strftime("%Y-%m-%d %H:%M:%S"),
                            'reason': 'ioc_would_take_immediately',
                            'from_tif': 'IOC',
                            'to_tif': 'GTC',
                            'open_price': float(analysis['row_15m']['open']),
                            'limit_price': float(order.get('limit_price', 0.0)),
                        },
                    )
                    order['time_in_force'] = 'GTC'
                    continue
                self._cancel_pending_order(symbol, reason="ioc_would_take_immediately")
                continue

            fill_price, fill_details = self._entry_fill_decision(order, analysis['row_15m'])
            if fill_price is not None:
                if self._fill_pending_order(symbol, order, fill_price, analysis['time'], fill_details=fill_details):
                    filled_symbols.add(symbol)
                    if self.config.entry_bar_same_bar_enabled and symbol in self.positions:
                        self._handle_entry_bar_same_bar_after_fill(symbol, analysis)
                continue

            if tif == "IOC":
                self._cancel_pending_order(symbol, reason="ioc_unfilled")
            elif self.config.gtc_expire_bars > 0 and int(order['bars_waited']) >= self.config.gtc_expire_bars:
                self._cancel_pending_order(symbol, reason="gtc_timeout")
        return filled_symbols

    def _handle_entry_bar_same_bar_after_fill(self, symbol: str, analysis: dict) -> bool:
        """新仓在成交当根 bar 上仅处理最基础的 TP/SL/partial，避免遗漏已证实的 same-bar 缺口。"""
        if not bool(getattr(self.config, "entry_bar_same_bar_enabled", False)):
            return False
        if symbol not in self.positions:
            return False

        pos = self.positions[symbol]
        row = analysis.get('row_15m')
        time = analysis.get('time')
        if row is None or time is None:
            return False

        high_price = float(row['high'])
        low_price = float(row['low'])
        stop_hit = False
        target_hit = False
        if pos['side'] == 'long':
            stop_hit = low_price <= pos['stop_price']
            target_hit = pos['take_profit'] is not None and high_price >= pos['take_profit']
        else:
            stop_hit = high_price >= pos['stop_price']
            target_hit = pos['take_profit'] is not None and low_price <= pos['take_profit']

        hit_levels: List[dict] = []
        tp_levels = pos.get('take_profit_levels') or []
        if tp_levels:
            for level in tp_levels:
                if not isinstance(level, dict) or bool(level.get('filled')):
                    continue
                level_price = float(level.get('price', 0.0) or 0.0)
                if level_price <= 0:
                    continue
                if pos['side'] == 'long' and high_price >= level_price:
                    hit_levels.append(level)
                elif pos['side'] == 'short' and low_price <= level_price:
                    hit_levels.append(level)
            if hit_levels:
                if pos['side'] == 'long':
                    hit_levels.sort(key=lambda item: float(item.get('price', 0.0)))
                else:
                    hit_levels.sort(key=lambda item: float(item.get('price', 0.0)), reverse=True)

        same_bar_tp_priority_mode = str(
            getattr(self.config, "same_bar_tp_priority_mode", "stop_first") or "stop_first"
        ).lower()
        if stop_hit and hit_levels and same_bar_tp_priority_mode == "tp1_before_stop":
            first_level = hit_levels[0]
            reduce_pct_original = max(0.0, min(1.0, float(first_level.get('reduce_pct', 0.0) or 0.0)))
            if reduce_pct_original > 0:
                level_price = float(first_level.get('price', 0.0) or 0.0)
                exit_price = self._target_fill_price(pos, row, level_price)
                self.close_position(
                    symbol,
                    exit_price,
                    time,
                    "take_profit_level_intrabar",
                    reduce_pct_original=reduce_pct_original,
                )
                first_level['filled'] = True
                if symbol not in self.positions:
                    return True
                pos = self.positions[symbol]
                stop_exit_price = self._stop_fill_price(pos, row, pos['stop_price'])
                stop_reason = "stop_loss_intrabar_after_tp1_same_bar"
                if target_hit:
                    stop_reason = "stop_loss_intrabar_both_hit_after_tp1_same_bar"
                self.close_position(symbol, stop_exit_price, time, stop_reason)
                return True

        if stop_hit:
            exit_price = self._stop_fill_price(pos, row, pos['stop_price'])
            reason = "stop_loss_intrabar"
            if target_hit:
                reason = "stop_loss_intrabar_both_hit"
            self.close_position(symbol, exit_price, time, reason)
            return True

        if hit_levels:
            for level in hit_levels:
                if symbol not in self.positions:
                    break
                level_price = float(level.get('price', 0.0) or 0.0)
                reduce_pct_original = max(0.0, min(1.0, float(level.get('reduce_pct', 0.0) or 0.0)))
                if reduce_pct_original <= 0:
                    continue
                exit_price = self._target_fill_price(self.positions[symbol], row, level_price)
                self.close_position(
                    symbol,
                    exit_price,
                    time,
                    "take_profit_level_intrabar",
                    reduce_pct_original=reduce_pct_original,
                )
                level['filled'] = True
            return symbol not in self.positions

        if target_hit:
            exit_price = self._target_fill_price(pos, row, float(pos['take_profit']))
            self.close_position(symbol, exit_price, time, "take_profit_intrabar")
            return True

        return False

    def _resolve_effective_breakeven_trigger(self, pos: dict) -> float:
        base_trigger = float(pos.get('breakeven_trigger_pnl_ratio', self.config.breakeven_trigger_pnl_ratio) or 0.0)
        if not bool(getattr(self.config, "partial_aware_breakeven_enabled", False)):
            return base_trigger
        no_partial_trigger = float(getattr(self.config, "partial_aware_no_partial_trigger_pnl_ratio", 0.0) or 0.0)
        if no_partial_trigger <= 0:
            return base_trigger
        levels = pos.get('take_profit_levels') or []
        if not levels:
            return base_trigger
        has_completed_partial = any(
            bool(level.get('filled'))
            for level in levels
            if isinstance(level, dict)
        )
        if has_completed_partial:
            return base_trigger
        return max(base_trigger, no_partial_trigger)

    def _runner_only_trailing_ready(self, pos: dict) -> bool:
        if not bool(getattr(self.config, "runner_only_trailing_enabled", False)):
            return True
        required_levels = max(0, int(getattr(self.config, "runner_only_trailing_min_completed_levels", 0) or 0))
        if required_levels <= 0:
            return True
        levels = pos.get('take_profit_levels') or []
        if not levels:
            return True
        completed_levels = sum(
            1
            for level in levels
            if isinstance(level, dict) and bool(level.get('filled'))
        )
        return completed_levels >= required_levels
    
    def check_stops(self, symbol: str, analysis: dict) -> bool:
        """用同一根15m的OHLC近似 intrabar 触发，返回是否已平仓"""
        if symbol not in self.positions:
            return False

        if self._check_symbol_timed_campaign_exit(symbol, analysis):
            return True
        
        pos = self.positions[symbol]
        signal = analysis['signal']
        row = analysis['row_15m']
        price = analysis['price']
        time = analysis['time']
        high_price = float(row['high'])
        low_price = float(row['low'])
        timed_campaign_hold = bool(pos.get('timed_campaign_hold_until_exit', False))
        timed_campaign_exit_window_active = self._symbol_timed_campaign_exit_window_active(symbol, time)

        # 计算当前盈亏比例
        if pos['side'] == 'long':
            pnl_pct = (price - pos['entry_price']) / pos['entry_price']
        else:
            pnl_pct = (pos['entry_price'] - price) / pos['entry_price']
        
        # 保本止损：与实盘配置对齐
        if self.config.breakeven_enabled and not timed_campaign_hold:
            breakeven_trigger = self._resolve_effective_breakeven_trigger(pos)
            breakeven_lock = float(pos.get('breakeven_lock_ratio', self.config.breakeven_lock_ratio))
            if pos['side'] == 'long':
                best_pnl_pct = (high_price - pos['entry_price']) / pos['entry_price']
                if best_pnl_pct >= breakeven_trigger:
                    pos['stop_price'] = max(
                        pos['stop_price'],
                        pos['entry_price'] * (1.0 + breakeven_lock),
                    )
            elif pos['side'] == 'short':
                best_pnl_pct = (pos['entry_price'] - low_price) / pos['entry_price']
                if best_pnl_pct >= breakeven_trigger:
                    pos['stop_price'] = min(
                        pos['stop_price'],
                        pos['entry_price'] * (1.0 - breakeven_lock),
                    )

        trailing_profile = self._resolve_trailing_profile(pos)
        activation_pnl_ratio = float(trailing_profile.get("activation_pnl_ratio", 0.0) or 0.0)
        if activation_pnl_ratio > 0 and self._runner_only_trailing_ready(pos) and not timed_campaign_hold:
            if pos['side'] == 'long':
                best_pnl_pct = (high_price - pos['entry_price']) / pos['entry_price']
            else:
                best_pnl_pct = (pos['entry_price'] - low_price) / pos['entry_price']
            if best_pnl_pct >= activation_pnl_ratio:
                atr_value = float(row.get('atr', row.get('atr_15m', row.get('atr_1h', 0.0))) or 0.0)
                ref_price = high_price if pos['side'] == 'long' else low_price
                raw_dist = atr_value * float(trailing_profile.get("atr_multiplier", 0.0) or 0.0)
                min_dist = max(0.0, float(trailing_profile.get("min_distance_pct", 0.0) or 0.0) * ref_price)
                max_dist = max(min_dist, float(trailing_profile.get("max_distance_pct", 0.0) or 0.0) * ref_price)
                trail_dist = max(min_dist, raw_dist)
                if max_dist > 0:
                    trail_dist = min(max_dist, trail_dist)
                if trail_dist > 0:
                    if pos['side'] == 'long':
                        candidate_stop = ref_price - trail_dist
                        if candidate_stop > float(pos.get('trailing_stop') or 0.0):
                            pos['trailing_stop'] = candidate_stop
                        pos['stop_price'] = max(pos['stop_price'], float(pos.get('trailing_stop') or candidate_stop))
                    else:
                        current_trailing = float(pos.get('trailing_stop') or 0.0)
                        candidate_stop = ref_price + trail_dist
                        if current_trailing <= 0 or candidate_stop < current_trailing:
                            pos['trailing_stop'] = candidate_stop
                        pos['stop_price'] = min(pos['stop_price'], float(pos.get('trailing_stop') or candidate_stop))

        stop_hit = False
        target_hit = False
        if pos['side'] == 'long':
            stop_hit = low_price <= pos['stop_price']
            target_hit = pos['take_profit'] is not None and high_price >= pos['take_profit']
        else:
            stop_hit = high_price >= pos['stop_price']
            target_hit = pos['take_profit'] is not None and low_price <= pos['take_profit']

        tp_levels = pos.get('take_profit_levels') or []
        hit_levels: List[dict] = []
        if tp_levels:
            for level in tp_levels:
                if not isinstance(level, dict) or bool(level.get('filled')):
                    continue
                level_price = float(level.get('price', 0.0) or 0.0)
                if level_price <= 0:
                    continue
                if pos['side'] == 'long' and high_price >= level_price:
                    hit_levels.append(level)
                elif pos['side'] == 'short' and low_price <= level_price:
                    hit_levels.append(level)
            if hit_levels:
                if pos['side'] == 'long':
                    hit_levels.sort(key=lambda item: float(item.get('price', 0.0)))
                else:
                    hit_levels.sort(key=lambda item: float(item.get('price', 0.0)), reverse=True)

        same_bar_tp_priority_mode = str(getattr(self.config, "same_bar_tp_priority_mode", "stop_first") or "stop_first").lower()
        if stop_hit and hit_levels and same_bar_tp_priority_mode == "tp1_before_stop":
            first_level = hit_levels[0]
            reduce_pct_original = max(0.0, min(1.0, float(first_level.get('reduce_pct', 0.0) or 0.0)))
            if reduce_pct_original > 0:
                level_price = float(first_level.get('price', 0.0) or 0.0)
                exit_price = self._target_fill_price(pos, row, level_price)
                self.close_position(
                    symbol,
                    exit_price,
                    time,
                    "take_profit_level_intrabar",
                    reduce_pct_original=reduce_pct_original,
                )
                first_level['filled'] = True
                if symbol not in self.positions:
                    return True
                pos = self.positions[symbol]
                stop_exit_price = self._stop_fill_price(pos, row, pos['stop_price'])
                stop_reason = "stop_loss_intrabar_after_tp1_same_bar"
                if target_hit:
                    stop_reason = "stop_loss_intrabar_both_hit_after_tp1_same_bar"
                self.close_position(symbol, stop_exit_price, time, stop_reason)
                return True
        
        if stop_hit:
            exit_price = self._stop_fill_price(pos, row, pos['stop_price'])
            reason = "stop_loss_intrabar"
            if target_hit:
                reason = "stop_loss_intrabar_both_hit"
            self.close_position(symbol, exit_price, time, reason)
            return True

        if timed_campaign_hold and not timed_campaign_exit_window_active:
            hit_levels = []
            target_hit = False

        if hit_levels:
            for level in hit_levels:
                if symbol not in self.positions:
                    break
                level_price = float(level.get('price', 0.0) or 0.0)
                reduce_pct_original = max(0.0, min(1.0, float(level.get('reduce_pct', 0.0) or 0.0)))
                if reduce_pct_original <= 0:
                    continue
                exit_price = self._target_fill_price(self.positions[symbol], row, level_price)
                self.close_position(
                    symbol,
                    exit_price,
                    time,
                    "take_profit_level_intrabar",
                    reduce_pct_original=reduce_pct_original,
                )
                level['filled'] = True
            return symbol not in self.positions

        if target_hit:
            exit_price = self._target_fill_price(pos, row, float(pos['take_profit']))
            self.close_position(symbol, exit_price, time, "take_profit_intrabar")
            return True

        shrink_exit_direction = str((signal.details or {}).get('shrink_exit_direction', '') or '')
        shrink_exit_ready = bool((signal.details or {}).get('shrink_exit_ready', False))
        if (
            self.strategy_config.enable_4h_shrink_exit
            and shrink_exit_ready
            and shrink_exit_direction == pos['side']
        ):
            shrink_loss_mitigation_active = bool(
                self.strategy_config.shrink_exit_loss_mitigation_enabled
                and pnl_pct <= float(self.strategy_config.shrink_exit_loss_mitigation_pnl_threshold)
                and pnl_pct <= float(self.strategy_config.shrink_exit_loss_mitigation_ignore_if_pnl_gt)
            )
            if shrink_loss_mitigation_active:
                reduce_pct_original = min(
                    1.0,
                    max(0.0, float(self.strategy_config.shrink_exit_loss_mitigation_exit_ratio)),
                )
                if reduce_pct_original > 0.0:
                    self.close_position(
                        symbol,
                        price,
                        time,
                        "4h_shrink_reduce",
                        reduce_pct_original=reduce_pct_original,
                    )
                    return symbol not in self.positions
            if self.strategy_config.exit_4h_require_profit:
                shrink_profit_ok = pnl_pct > 0
            else:
                shrink_profit_ok = pnl_pct >= float(self.strategy_config.exit_4h_weak_loss_threshold)
            if shrink_profit_ok:
                self.close_position(symbol, price, time, "4h_shrink_exit")
                return True
        
        # 反向有效信号触发离场，但不在同一根K线立即反手
        if (
            not timed_campaign_hold
            and
            signal.direction in ('long', 'short')
            and signal.signal_score >= self._signal_threshold(signal)
            and signal.direction != pos['side']
        ):
            self.close_position(symbol, price, time, "signal_reverse")
            return True
        
        # 最大持仓时间限制（默认关闭，避免偏离实盘）
        if self.config.max_hold_hours > 0 and 'entry_time' in pos and not timed_campaign_hold:
            hold_time = time - pos['entry_time']
            # 时间戳可能是Timedelta或数值
            if hasattr(hold_time, 'total_seconds'):
                hold_seconds = hold_time.total_seconds()
            else:
                hold_seconds = float(hold_time) / 1000 if hold_time > 100000 else float(hold_time)
            if hold_seconds > self.config.max_hold_hours * 60 * 60:
                self.close_position(symbol, price, time, "max_hold_time")
                return True
        return False

    def _mark_to_market_equity(self, price_map: Dict[str, float]) -> float:
        equity = float(self.capital)

        for order in self.pending_orders.values():
            equity += float(order.get("margin", 0.0))

        for symbol, pos in self.positions.items():
            current_price = float(price_map.get(symbol, pos.get("entry_price", 0.0)))
            entry_price = float(pos.get("entry_price", 0.0))
            margin = float(pos.get("margin", 0.0))
            leverage = float(pos.get("leverage", 0.0))
            if entry_price <= 0 or margin <= 0 or leverage <= 0:
                equity += margin
                continue

            if pos["side"] == "long":
                pnl_pct = (current_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - current_price) / entry_price
            unrealized_pnl = margin * leverage * pnl_pct
            equity += margin + unrealized_pnl

        return equity

    def _record_equity_snapshot(self, timestamp: object, price_map: Dict[str, float]) -> None:
        equity = self._mark_to_market_equity(price_map)
        snapshot = {
            "timestamp": pd.Timestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S"),
            "equity": float(equity),
            "cash": float(self.capital),
            "open_positions": int(len(self.positions)),
            "pending_orders": int(len(self.pending_orders)),
        }
        self.equity_curve.append(snapshot)

        if not self.equity_curve:
            return

        peak_snapshot = max(self.equity_curve, key=lambda item: float(item["equity"]))
        peak_equity = float(peak_snapshot["equity"])
        if peak_equity <= 0:
            return

        drawdown_value = peak_equity - float(equity)
        drawdown_pct = drawdown_value / peak_equity * 100.0
        if drawdown_value > self.max_drawdown_value:
            self.max_drawdown_value = drawdown_value
            self.max_drawdown_pct = drawdown_pct
            self.max_drawdown_start_time = str(peak_snapshot["timestamp"])
            self.max_drawdown_trough_time = str(snapshot["timestamp"])
            self.max_drawdown_recovery_time = ""

    def _finalize_drawdown_recovery(self) -> None:
        if not self.equity_curve or self.max_drawdown_value <= 0:
            return

        peak_equity = 0.0
        recovery_target_time = ""
        found_peak = False
        for snapshot in self.equity_curve:
            equity = float(snapshot["equity"])
            ts = str(snapshot["timestamp"])
            if ts == self.max_drawdown_start_time:
                peak_equity = equity
                found_peak = True
                continue
            if found_peak and equity >= peak_equity:
                recovery_target_time = ts
                break
        self.max_drawdown_recovery_time = recovery_target_time

    def run_backtest(self, market_data_map: Dict[str, Dict[str, pd.DataFrame]]) -> dict:
        """运行统一时间轴多币种回放"""
        if not market_data_map:
            return {'signals_generated': 0, 'vetoes': {v: 0 for v in VetoType}, 'timeline_points': 0}

        timeline: set[int] = set()
        idx_maps: Dict[str, Dict[int, int]] = {}
        for symbol, data in market_data_map.items():
            tf_15m = data['15m']
            ts_values = [self._timestamp_key(ts) for ts in tf_15m['timestamp'].tolist()]
            idx_maps[symbol] = {ts: idx for idx, ts in enumerate(ts_values)}
            timeline.update(ts_values)
            print(f"  回测 {symbol}: {len(tf_15m)} 根15M K线")

        ordered_timeline = sorted(timeline)
        print(f"\n统一时间轴回放: {len(ordered_timeline)} 个15M时间点")

        signals_generated = 0
        vetoes = {v: 0 for v in VetoType}
        last_price_map: Dict[str, float] = {}
        for current_ts in ordered_timeline:
            analyses: Dict[str, dict] = {}
            for symbol, data in market_data_map.items():
                idx_15m = idx_maps[symbol].get(current_ts)
                if idx_15m is None or idx_15m < 50:
                    continue
                analysis = self.analyze_bar(symbol, data, idx_15m)
                if analysis is None:
                    continue
                analyses[symbol] = analysis
                last_price_map[symbol] = float(analysis["price"])

            current_time = pd.Timestamp(current_ts)
            if not timestamp_in_trade_window(
                current_time,
                trade_window_start_iso=self.config.window_start_iso,
                trade_window_end_iso=self.config.window_end_iso,
            ):
                continue

            for analysis in analyses.values():
                signal = analysis['signal']
                if signal.veto_type and signal.veto_type != VetoType.NONE:
                    vetoes[signal.veto_type] += 1
                if signal.direction != 'neutral':
                    signals_generated += 1

            if not analyses and not self.positions:
                continue

            newly_filled_symbols = self.process_pending_orders(analyses)

            closed_symbols_this_bar = set()
            for symbol in list(self.positions.keys()):
                if symbol in newly_filled_symbols:
                    continue
                analysis = analyses.get(symbol)
                if analysis is None:
                    continue
                if self.check_stops(symbol, analysis):
                    closed_symbols_this_bar.add(symbol)

            if (len(self.positions) + len(self.pending_orders)) >= self.config.max_positions:
                continue

            candidates: List[Tuple[str, dict]] = []
            for symbol, analysis in analyses.items():
                if symbol in self.positions or symbol in closed_symbols_this_bar or symbol in self.pending_orders:
                    continue
                signal = analysis['signal']
                if signal.direction == 'neutral':
                    continue
                if signal.signal_score < self._signal_threshold(signal):
                    continue
                candidates.append((symbol, analysis))

            candidates.sort(
                key=lambda item: (
                    float(item[1]['signal'].signal_score),
                    float(item[1]['signal'].vwap_score),
                    float(item[1]['signal'].ema_multiplier),
                ),
                reverse=True,
            )

            for symbol, analysis in candidates:
                if (len(self.positions) + len(self.pending_orders)) >= self.config.max_positions:
                    break
                self.execute_trade(symbol, analysis, market_data_map[symbol])

            if analyses or self.positions or self.pending_orders:
                self._record_equity_snapshot(current_time, last_price_map)

        for symbol in list(self.pending_orders.keys()):
            self._cancel_pending_order(symbol, reason="backtest_end")

        for symbol, pos in list(self.positions.items()):
            data = market_data_map.get(symbol)
            if not data:
                continue
            last_row = data['15m'].iloc[-1]
            self.close_position(symbol, last_row['close'], last_row['timestamp'], "backtest_end")

        if ordered_timeline:
            final_timestamp = pd.Timestamp(ordered_timeline[-1])
            self._record_equity_snapshot(final_timestamp, last_price_map)
        self._finalize_drawdown_recovery()

        return {
            'signals_generated': signals_generated,
            'vetoes': vetoes,
            'timeline_points': len(ordered_timeline),
        }


# ==================== 主函数 ====================

def run_backtest(
    config_path: str,
    initial_capital: float = 10000.0,
    fee_rate: float = 0.0004,
    max_positions_override: Optional[int] = None,
    fixed_leverage: Optional[int] = None,
    profile_name: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
):
    """运行完整回测"""
    print("=" * 70)
    print("MACD多时间框架策略 V2.0 回测")
    print("VWAP + BOLL 增强版")
    print("=" * 70)
    
    runtime_cfg = ConfigLoader.load_trading_config(config_path)
    runtime_cfg, active_profile = apply_backtest_profile(runtime_cfg, profile_name=profile_name)

    # 加载配置
    config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path=config_path,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        max_positions_override=max_positions_override,
        fixed_leverage=fixed_leverage,
        profile_name=active_profile,
        window_start_iso=start_time or "",
        window_end_iso=end_time or "",
    )
    strategy_config = build_strategy_config(runtime_cfg)
    
    print(f"\n策略配置:")
    print(f"  min_signal_score: {strategy_config.min_signal_score}")
    print(f"  red_bar_growing_min_signal_score: {strategy_config.red_bar_growing_min_signal_score}")
    print(f"  flip_bearish_min_signal_score: {strategy_config.flip_bearish_min_signal_score}")
    print(f"  flip_bullish_min_signal_score: {strategy_config.flip_bullish_min_signal_score}")
    print(f"  min_entry_score: {strategy_config.min_entry_score}")
    print(f"  min_vwap_score_for_entry: {strategy_config.min_vwap_score_for_entry}")
    print(f"  weight_1h_direction(legacy): {strategy_config.weight_1h_direction}")
    print(f"  weight_4h_direction: {strategy_config.weight_4h_direction}")
    print(f"  weight_4h_enhancement: {strategy_config.weight_4h_enhancement}")
    print(f"  primary_direction_timeframe: {strategy_config.primary_direction_timeframe}")
    print(f"  require_1h_confirmation_when_4h_primary: {strategy_config.require_1h_confirmation_when_4h_primary}")
    print(f"  allow_neutral_1h_confirmation: {strategy_config.allow_neutral_1h_confirmation}")
    print(f"  light_1h_confirmation_when_4h_primary: {strategy_config.light_1h_confirmation_when_4h_primary}")
    print(f"  weight_vwap: {strategy_config.weight_vwap}")
    print(f"  boll_middle_hard_block: {strategy_config.ema_55_1h_hard_block}")
    print(f"  vwap_deviation_hard_block: {strategy_config.vwap_deviation_hard_block}")
    print(f"  flip_bearish_min_adx_1h: {strategy_config.flip_bearish_min_adx_1h}")
    print(f"  flip_bearish_retest_reject_min_vwap_score: {strategy_config.flip_bearish_retest_reject_min_vwap_score}")
    print(f"  flip_bearish_max_bb_middle_slope_1h: {strategy_config.flip_bearish_max_ema21_slope_1h}")
    print(f"  flip_bearish_max_bb_middle_slope_4h: {strategy_config.flip_bearish_max_ema21_slope_4h}")
    print(f"  structural_vwap_mode: {strategy_config.structural_vwap_mode}")
    print(f"  structural_vwap_rolling_window: {strategy_config.structural_vwap_rolling_window}")
    print(f"  vwap_retest_tolerance: {strategy_config.vwap_retest_tolerance}")
    print(f"  max_stop_loss_pct: {strategy_config.max_stop_loss_pct}")
    print(f"  dual_pressure_target_portion_bonus: {strategy_config.dual_pressure_target_portion_bonus:.2%}")
    print(f"  dual_pressure_max_symbol_position_portion: {strategy_config.dual_pressure_max_symbol_position_portion:.2%}")
    print(f"  use_cvd_bonus_filter: {int(strategy_config.use_cvd_bonus_filter)}")
    print(f"  cvd_1h_slope_lookback: {strategy_config.cvd_1h_slope_lookback}")
    print(f"  cvd_15m_slope_lookback: {strategy_config.cvd_15m_slope_lookback}")
    print(f"  cvd_positive_delta_ratio_threshold: {strategy_config.cvd_positive_delta_ratio_threshold:.3f}")
    print(f"  cvd_negative_delta_ratio_threshold: {strategy_config.cvd_negative_delta_ratio_threshold:.3f}")
    print(f"  cvd_bullish_bonus_multiplier: {strategy_config.cvd_bullish_bonus_multiplier:.2f}")
    print(f"  cvd_neutral_bonus_multiplier: {strategy_config.cvd_neutral_bonus_multiplier:.2f}")
    print(f"  cvd_bearish_bonus_multiplier: {strategy_config.cvd_bearish_bonus_multiplier:.2f}")
    print(f"  use_cvd_veto_filter: {int(strategy_config.use_cvd_veto_filter)}")
    print(f"  cvd_veto_session_reset: {strategy_config.cvd_veto_session_reset}")
    print(f"  cvd_veto_lookback_15m: {strategy_config.cvd_veto_lookback_15m}")
    print(f"  cvd_veto_positive_delta_ratio_threshold: {strategy_config.cvd_veto_positive_delta_ratio_threshold:.3f}")
    print(f"  cvd_veto_positive_pressure_threshold: {strategy_config.cvd_veto_positive_pressure_threshold:.3f}")
    print(f"  cvd_veto_session_ratio_change_threshold: {strategy_config.cvd_veto_session_ratio_change_threshold:.3f}")
    print(f"  cvd_veto_session_price_change_threshold: {strategy_config.cvd_veto_session_price_change_threshold:.3f}")
    print(f"  cvd_veto_strong_close_pos_threshold: {strategy_config.cvd_veto_strong_close_pos_threshold:.2f}")
    print(f"  cvd_veto_upper_wick_ratio_max: {strategy_config.cvd_veto_upper_wick_ratio_max:.2f}")
    print(f"  cvd_absorption_delta_ratio_threshold: {strategy_config.cvd_absorption_delta_ratio_threshold:.3f}")
    print(f"  cvd_absorption_close_pos_threshold: {strategy_config.cvd_absorption_close_pos_threshold:.2f}")
    print(f"  cvd_absorption_upper_wick_ratio_threshold: {strategy_config.cvd_absorption_upper_wick_ratio_threshold:.2f}")
    print(f"  cvd_absorption_structure_gap_threshold: {strategy_config.cvd_absorption_structure_gap_threshold:.3f}")
    print(f"  cvd_divergence_price_change_threshold: {strategy_config.cvd_divergence_price_change_threshold:.3f}")
    print(f"  cvd_divergence_session_change_threshold: {strategy_config.cvd_divergence_session_change_threshold:.3f}")
    print(f"  boll_strong_trend_leverage_mult: {strategy_config.ema_strong_trend_leverage_mult}")
    print(f"\n运行配置:")
    print(f"  config_path: {config.config_path}")
    print(f"  backtest_profile: {config.profile_name or '(none)'}")
    if config.window_start_iso or config.window_end_iso:
        print(f"  trade_window: {config.window_start_iso or '(open)'} -> {config.window_end_iso or '(open)'}")
    if config.data_window_start_iso or config.data_window_end_iso:
        print(f"  data_window: {config.data_window_start_iso or '(open)'} -> {config.data_window_end_iso or '(open)'}")
    if config.warmup_hours > 0:
        print(f"  warmup_hours: {config.warmup_hours}")
    print(f"  symbols: {len(config.symbols)}")
    print(f"  max_positions: {config.max_positions}")
    print(f"  default_target_portion: {config.default_target_portion:.2%}")
    print(f"  max_symbol_position_portion: {config.max_symbol_position_portion:.2%}")
    print(f"  min_open_portion: {config.min_open_portion:.2%}")
    print(f"  reserve_pct: {config.reserve_pct:.2%}")
    print(f"  leverage_range: {config.min_leverage}x ~ {config.max_leverage}x")
    if config.fixed_leverage is not None:
        print(f"  fixed_leverage: {config.fixed_leverage}x")
    if config.allowed_entry_hours_utc:
        print(f"  allowed_entry_hours_utc: {config.allowed_entry_hours_utc}")
    print(f"  stop_loss_pct: {config.default_stop_loss_pct:.2%}")
    print(f"  take_profit_pct: {config.default_take_profit_pct:.2%}")
    if config.take_profit_pct_levels and config.take_profit_reduce_pct_levels:
        print(f"  take_profit_pct_levels: {config.take_profit_pct_levels}")
        print(f"  take_profit_reduce_pct_levels: {config.take_profit_reduce_pct_levels}")
    print(f"  breakeven: enabled={int(config.breakeven_enabled)} trigger={config.breakeven_trigger_pnl_ratio:.2%} lock={config.breakeven_lock_ratio:.2%}")
    print(f"  entry_slippage: {config.entry_slippage:.2%}")
    print(f"  entry_passive_offset_pct: {config.entry_passive_offset_pct:.2%}")
    print(
        "  passive_pricing:"
        f" atr_fraction={config.entry_passive_pricing_atr_fraction:.2f}"
        f" min={config.entry_passive_pricing_min_offset_pct:.2%}"
        f" max={config.entry_passive_pricing_max_offset_pct:.2%}"
    )
    print(f"  entry_tif: {config.entry_time_in_force}")
    
    # 初始化回测引擎
    engine = BacktestEngine(config, strategy_config, runtime_cfg)
    
    # 加载数据并回测
    available_symbols = []
    market_data_map: Dict[str, Dict[str, pd.DataFrame]] = {}
    stats = {'signals': 0, 'vetoes': {v: 0 for v in VetoType}, 'timeline_points': 0}
    
    print(f"\n加载数据目录: {config.data_dir}")
    
    missing_symbols = []
    for symbol in config.symbols:
        data = load_symbol_data(config.data_dir, symbol, strategy_config)
        if data:
            available_symbols.append(symbol)
            market_data_map[symbol] = data
        else:
            missing_symbols.append(symbol)

    dropped_by_window: List[str] = []
    data_window_start = config.data_window_start_iso or start_time
    data_window_end = config.data_window_end_iso or end_time
    if market_data_map and (data_window_start or data_window_end):
        market_data_map, dropped_by_window = filter_market_data_by_time_range(
            market_data_map,
            start_time=data_window_start,
            end_time=data_window_end,
        )
        available_symbols = list(market_data_map.keys())
        if dropped_by_window:
            missing_symbols.extend(dropped_by_window)

    print(f"\n数据覆盖:")
    print(f"  可回测币种: {len(available_symbols)}/{len(config.symbols)}")
    if missing_symbols:
        print(f"  缺失数据币种: {', '.join(missing_symbols)}")
    if dropped_by_window:
        print(f"  时间窗过滤移除: {', '.join(dropped_by_window)}")

    if market_data_map:
        result = engine.run_backtest(market_data_map)
        stats['signals'] = result['signals_generated']
        stats['timeline_points'] = result.get('timeline_points', 0)
        for v, count in result['vetoes'].items():
            stats['vetoes'][v] += count
    
    # 生成报告
    print("\n" + "=" * 70)
    print("回测结果")
    print("=" * 70)
    
    # 基本统计
    trades = engine.trades
    capital = engine.capital
    initial = config.initial_capital
    
    print(f"\n资金:")
    print(f"  初始资金: ${initial:,.2f}")
    print(f"  最终权益: ${capital:,.2f}")
    print(f"  收益率: {(capital - initial) / initial * 100:+.2f}%")
    
    # 交易统计
    if trades:
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        
        total_pnl = sum(t['pnl'] for t in trades)
        win_pnl = sum(t['pnl'] for t in wins)
        loss_pnl = sum(t['pnl'] for t in losses)
        
        print(f"\n交易统计:")
        print(f"  总交易数: {len(trades)}")
        print(f"  盈利交易: {len(wins)} ({len(wins)/len(trades)*100:.1f}%)")
        print(f"  亏损交易: {len(losses)} ({len(losses)/len(trades)*100:.1f}%)")
        print(f"  胜率: {len(wins)/len(trades)*100:.1f}%")
        
        if wins and losses:
            avg_win = win_pnl / len(wins)
            avg_loss = abs(loss_pnl / len(losses))
            profit_factor = win_pnl / abs(loss_pnl) if loss_pnl != 0 else float('inf')
            
            print(f"\n盈亏分析:")
            print(f"  总盈亏: ${total_pnl:,.2f}")
            print(f"  平均盈利: ${avg_win:,.2f}")
            print(f"  平均亏损: ${avg_loss:,.2f}")
            print(f"  盈亏比: {avg_win / avg_loss:.2f}" if avg_loss > 0 else "  盈亏比: N/A")
            print(f"  盈利因子: {profit_factor:.2f}")
        
        # 按信号类型分析
        print(f"\n信号类型分析:")
        type_stats = {}
        for t in trades:
            st = t['signal_type_1h']
            if st not in type_stats:
                type_stats[st] = {'count': 0, 'pnl': 0, 'wins': 0}
            type_stats[st]['count'] += 1
            type_stats[st]['pnl'] += t['pnl']
            if t['pnl'] > 0:
                type_stats[st]['wins'] += 1
        
        for st, s in sorted(type_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
            win_rate = s['wins'] / s['count'] * 100 if s['count'] > 0 else 0
            print(f"  {st}: {s['count']}笔, 胜率{win_rate:.0f}%, 盈亏${s['pnl']:+.2f}")
        
        # VWAP评分分析
        print(f"\nVWAP评分分析:")
        vwap_stats = {}
        for t in trades:
            vs = round(t['vwap_score'], 2)
            bucket = f"{vs:.2f}"
            if bucket not in vwap_stats:
                vwap_stats[bucket] = {'count': 0, 'pnl': 0, 'wins': 0}
            vwap_stats[bucket]['count'] += 1
            vwap_stats[bucket]['pnl'] += t['pnl']
            if t['pnl'] > 0:
                vwap_stats[bucket]['wins'] += 1
        
        for vs, s in sorted(vwap_stats.items(), key=lambda x: float(x[0]), reverse=True):
            win_rate = s['wins'] / s['count'] * 100 if s['count'] > 0 else 0
            print(f"  VWAP={vs}: {s['count']}笔, 胜率{win_rate:.0f}%")

        vwap_state_stats = {}
        for t in trades:
            state = str(t.get('vwap_state', '') or 'unknown')
            if state not in vwap_state_stats:
                vwap_state_stats[state] = {'count': 0, 'pnl': 0.0, 'wins': 0}
            vwap_state_stats[state]['count'] += 1
            vwap_state_stats[state]['pnl'] += float(t.get('pnl', 0.0))
            if float(t.get('pnl', 0.0)) > 0:
                vwap_state_stats[state]['wins'] += 1

        print(f"\nVWAP位置状态分析:")
        for state, s in sorted(vwap_state_stats.items(), key=lambda x: x[1]['count'], reverse=True):
            win_rate = s['wins'] / s['count'] * 100 if s['count'] > 0 else 0
            print(f"  {state}: {s['count']}笔, 胜率{win_rate:.0f}%, 盈亏${s['pnl']:+.2f}")

        print(f"\nVWAP分段胜率:")
        vwap_buckets = {
            "0.00-0.15": {"win": 0, "loss": 0},
            "0.15-0.20": {"win": 0, "loss": 0},
            "0.20-0.25": {"win": 0, "loss": 0},
            "0.25+": {"win": 0, "loss": 0},
        }
        for t in trades:
            vwap_score = float(t.get('vwap_score', 0.0))
            if vwap_score < 0.15:
                bucket = "0.00-0.15"
            elif vwap_score < 0.20:
                bucket = "0.15-0.20"
            elif vwap_score < 0.25:
                bucket = "0.20-0.25"
            else:
                bucket = "0.25+"
            if t['pnl'] > 0:
                vwap_buckets[bucket]["win"] += 1
            else:
                vwap_buckets[bucket]["loss"] += 1

        for bucket, bucket_stats in vwap_buckets.items():
            total = bucket_stats["win"] + bucket_stats["loss"]
            win_rate = (bucket_stats["win"] / total * 100) if total else 0.0
            print(
                f"  vwap {bucket:12s} | 总计 {total:3d} | 胜率 {win_rate:5.1f}% | "
                f"盈{bucket_stats['win']} 亏{bucket_stats['loss']}"
            )
        
        # BOLL修正分析
        print(f"\nBOLL修正分析:")
        ema_stats = {}
        for t in trades:
            em = t['ema_multiplier']
            bucket = f"{em:.1f}x"
            if bucket not in ema_stats:
                ema_stats[bucket] = {'count': 0, 'pnl': 0, 'wins': 0}
            ema_stats[bucket]['count'] += 1
            ema_stats[bucket]['pnl'] += t['pnl']
            if t['pnl'] > 0:
                ema_stats[bucket]['wins'] += 1
        
        for em, s in sorted(ema_stats.items(), key=lambda x: float(x[0].replace('x', '')), reverse=True):
            win_rate = s['wins'] / s['count'] * 100 if s['count'] > 0 else 0
            print(f"  BOLL={em}: {s['count']}笔, 胜率{win_rate:.0f}%")

        cvd_bonus_stats = {}
        for t in trades:
            state = str(t.get('cvd_bonus_state', 'inactive') or 'inactive')
            if state not in cvd_bonus_stats:
                cvd_bonus_stats[state] = {'count': 0, 'pnl': 0.0, 'wins': 0, 'bonus_sum': 0.0}
            cvd_bonus_stats[state]['count'] += 1
            cvd_bonus_stats[state]['pnl'] += float(t.get('pnl', 0.0))
            cvd_bonus_stats[state]['bonus_sum'] += float(t.get('cvd_bonus_multiplier', 1.0))
            if float(t.get('pnl', 0.0)) > 0:
                cvd_bonus_stats[state]['wins'] += 1

        print(f"\nCVD bonus分析:")
        for state, s in sorted(cvd_bonus_stats.items(), key=lambda x: x[1]['count'], reverse=True):
            count = int(s['count'])
            win_rate = s['wins'] / count * 100 if count > 0 else 0
            avg_bonus = s['bonus_sum'] / count if count > 0 else 0.0
            print(
                f"  {state}: {count}笔, 胜率{win_rate:.0f}%, 盈亏${s['pnl']:+.2f}, "
                f"平均bonus乘数={avg_bonus:.2f}"
            )

        cvd_veto_stats = {}
        for t in trades:
            state = str(t.get('cvd_veto_state', 'inactive') or 'inactive')
            if state not in cvd_veto_stats:
                cvd_veto_stats[state] = {'count': 0, 'pnl': 0.0, 'wins': 0}
            cvd_veto_stats[state]['count'] += 1
            cvd_veto_stats[state]['pnl'] += float(t.get('pnl', 0.0))
            if float(t.get('pnl', 0.0)) > 0:
                cvd_veto_stats[state]['wins'] += 1

        print(f"\nCVD veto状态分析:")
        for state, s in sorted(cvd_veto_stats.items(), key=lambda x: x[1]['count'], reverse=True):
            count = int(s['count'])
            win_rate = s['wins'] / count * 100 if count > 0 else 0
            print(f"  {state}: {count}笔, 胜率{win_rate:.0f}%, 盈亏${s['pnl']:+.2f}")

    if engine.equity_curve:
        print(f"\n风险分析:")
        print(f"  逐bar权益点数: {len(engine.equity_curve)}")
        print(f"  真最大回撤: ${engine.max_drawdown_value:,.2f} ({engine.max_drawdown_pct:.2f}%)")
        if engine.max_drawdown_start_time and engine.max_drawdown_trough_time:
            print(f"  回撤区间: {engine.max_drawdown_start_time} -> {engine.max_drawdown_trough_time}")
        if engine.max_drawdown_recovery_time:
            print(f"  回撤恢复: {engine.max_drawdown_recovery_time}")
    
    # 否决统计
    print(f"\n否决统计:")
    print(f"  生成信号总数: {stats['signals']}")
    if stats['timeline_points'] > 0:
        print(f"  统一时间轴点数: {stats['timeline_points']}")
    for v, count in stats['vetoes'].items():
        if count > 0:
            print(f"  {v.value}: {count}次")
    
    # 保存交易记录
    if trades:
        trades_df = pd.DataFrame(trades)
        output_dir = Path("output/backtest")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = output_dir / f"v2_trades_{timestamp}.csv"
        trades_df.to_csv(output_file, index=False)
        print(f"\n交易记录已保存: {output_file}")

        equity_curve_file = None
        if engine.equity_curve:
            equity_curve_df = pd.DataFrame(engine.equity_curve)
            equity_curve_file = output_dir / f"v2_equity_curve_{timestamp}.csv"
            equity_curve_df.to_csv(equity_curve_file, index=False)
            print(f"权益曲线已保存: {equity_curve_file}")

        summary = build_backtest_summary(
            config=config,
            strategy_config=strategy_config,
            engine=engine,
            available_symbols=available_symbols,
            missing_symbols=missing_symbols,
            stats=stats,
        )
        summary_file = output_dir / f"v2_summary_{timestamp}.json"
        summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"摘要已保存: {summary_file}")
        engine.last_summary = summary
        engine.last_summary_file = str(summary_file)
        engine.last_trades_file = str(output_file)
        engine.last_equity_curve_file = str(equity_curve_file) if equity_curve_file else None
    else:
        engine.last_summary = build_backtest_summary(
            config=config,
            strategy_config=strategy_config,
            engine=engine,
            available_symbols=available_symbols,
            missing_symbols=missing_symbols,
            stats=stats,
        )
        engine.last_summary_file = None
        engine.last_trades_file = None
        engine.last_equity_curve_file = None
    
    print("\n" + "=" * 70)
    print("回测完成")
    print("=" * 70)
    
    return engine


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run MACD V2 backtest with current runtime config")
    parser.add_argument("--config", default="config/trading_config_fund_flow.json", help="runtime config path")
    parser.add_argument("--initial-capital", type=float, default=10000.0, help="initial capital in USDT")
    parser.add_argument("--fee-rate", type=float, default=0.0004, help="fee rate per side")
    parser.add_argument("--max-positions", type=int, default=None, help="override max concurrent positions")
    parser.add_argument("--fixed-leverage", type=int, default=None, help="force a fixed leverage for all entries")
    parser.add_argument("--profile", default=None, help="optional backtest profile name from fund_flow.backtest.profiles")
    parser.add_argument("--start", default=None, help="optional inclusive backtest window start, e.g. 2026-02-20 or 2026-02-20T00:00:00")
    parser.add_argument("--end", default=None, help="optional inclusive backtest window end, e.g. 2026-02-27 or 2026-02-27T23:59:59")
    args = parser.parse_args()
    run_backtest(
        config_path=args.config,
        initial_capital=args.initial_capital,
        fee_rate=args.fee_rate,
        max_positions_override=args.max_positions,
        fixed_leverage=args.fixed_leverage,
        profile_name=args.profile,
        start_time=args.start,
        end_time=args.end,
    )
