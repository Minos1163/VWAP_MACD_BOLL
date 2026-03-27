"""
Validate live trading config alignment against the backtest baseline config.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.config_loader import ConfigLoader
from scripts.backtest_macd_v2 import apply_backtest_profile, build_backtest_config


def _get_path(mapping: Any, path: str, default: Any = None) -> Any:
    current = mapping
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _iter_alignment_specs() -> Iterable[tuple[str, str, str]]:
    yield ("fund_flow.long_open_threshold", "fund_flow.long_open_threshold", "core_threshold")
    yield ("fund_flow.short_open_threshold", "fund_flow.short_open_threshold", "core_threshold")
    yield ("fund_flow.close_threshold", "fund_flow.close_threshold", "core_threshold")
    yield ("fund_flow.take_profit_pct", "fund_flow.take_profit_pct", "core_threshold")
    yield ("fund_flow.stop_loss_pct", "fund_flow.stop_loss_pct", "core_threshold")
    yield ("fund_flow.entry_slippage", "fund_flow.entry_slippage", "core_threshold")
    yield ("fund_flow.reverse_close_confirm_bars", "fund_flow.reverse_close_confirm_bars", "core_threshold")
    yield ("fund_flow.default_target_portion", "fund_flow.default_target_portion", "position_sizing")
    yield ("fund_flow.add_position_portion", "fund_flow.add_position_portion", "position_sizing")
    yield ("fund_flow.max_symbol_position_portion", "fund_flow.max_symbol_position_portion", "position_sizing")
    yield ("fund_flow.min_open_portion", "fund_flow.min_open_portion", "position_sizing")
    yield ("fund_flow.default_leverage", "fund_flow.default_leverage", "leverage")
    yield ("fund_flow.min_leverage", "fund_flow.min_leverage", "leverage")
    yield ("fund_flow.max_leverage", "fund_flow.max_leverage", "leverage")
    yield ("fund_flow.allowed_entry_hours_utc", "fund_flow.allowed_entry_hours_utc", "entry_window")
    yield ("fund_flow.ma10_macd_confluence.enabled", "fund_flow.ma10_macd_confluence.enabled", "outer_filters")
    yield ("fund_flow.ma10_macd_confluence.entry_hard_filter", "fund_flow.ma10_macd_confluence.entry_hard_filter", "outer_filters")
    yield ("fund_flow.protection_sla_enabled", "fund_flow.protection_sla_enabled", "protection_sla")
    yield ("fund_flow.protection_sla_force_flatten", "fund_flow.protection_sla_force_flatten", "protection_sla")
    yield ("fund_flow.macd_mtf_strategy_v2.entry_filters.enable_flip_bullish_cvd_context_filter", "fund_flow.macd_mtf_strategy_v2.entry_filters.enable_flip_bullish_cvd_context_filter", "cvd_alignment")
    yield ("fund_flow.macd_mtf_strategy_v2.leverage_config.use_cvd_veto_filter", "fund_flow.macd_mtf_strategy_v2.leverage_config.use_cvd_veto_filter", "cvd_alignment")
    yield ("fund_flow.macd_mtf_strategy_v2.cvd_filter_config.enabled", "fund_flow.macd_mtf_strategy_v2.cvd_filter_config.enabled", "cvd_alignment")


def _iter_convergence_expectations() -> Iterable[tuple[str, Any, str]]:
    yield ("fund_flow.entry_window.enabled", False, "entry_window")
    yield ("fund_flow.dca_martingale_enabled", False, "position_management")
    yield ("fund_flow.dca_max_additions", 0, "position_management")
    yield ("fund_flow.winner_pyramiding.enabled", False, "position_management")
    yield ("fund_flow.pretrade_risk_gate.enabled", True, "pretrade_gate")
    yield ("fund_flow.pretrade_risk_gate.use_hard_rules_only", True, "pretrade_gate")
    yield ("fund_flow.pretrade_risk_gate.cvd_veto_enabled", False, "pretrade_gate")
    yield ("fund_flow.pretrade_risk_gate.atr_ratio_hard_block", 3.5, "pretrade_gate")
    yield ("fund_flow.pretrade_risk_gate.equity_usage_block", 0.85, "pretrade_gate")
    yield ("fund_flow.pretrade_risk_gate.dd_exit_threshold", 0.1, "pretrade_gate")
    yield ("fund_flow.protection_sla_seconds", 300, "protection_sla")
    yield ("fund_flow.protection_sla_pnl_grace_threshold", -0.005, "protection_sla")
    yield ("fund_flow.protection_sla_api_health_check_before_force", True, "protection_sla")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate live config alignment against backtest baseline.")
    parser.add_argument(
        "--backtest-config",
        default="config/trading_config_fund_flow.json",
        help="Backtest baseline config path.",
    )
    parser.add_argument(
        "--backtest-profile",
        default="",
        help="Optional backtest profile name applied on top of the baseline config.",
    )
    parser.add_argument(
        "--live-config",
        default="config/trading_config_fund_flow_live_production.json",
        help="Live production config path.",
    )
    args = parser.parse_args()

    backtest_raw = ConfigLoader.load_trading_config(args.backtest_config)
    backtest_cfg, applied_profile = apply_backtest_profile(backtest_raw, args.backtest_profile)
    live_cfg = ConfigLoader.load_trading_config(args.live_config)
    backtest_runtime = build_backtest_config(
        runtime_cfg=backtest_cfg,
        config_path=args.backtest_config,
        profile_name=applied_profile,
    )

    mismatches: list[dict[str, Any]] = []
    for backtest_path, live_path, category in _iter_alignment_specs():
        backtest_value = _get_path(backtest_cfg, backtest_path)
        live_value = _get_path(live_cfg, live_path)
        if backtest_value != live_value:
            mismatches.append(
                {
                    "category": category,
                    "backtest_path": backtest_path,
                    "live_path": live_path,
                    "backtest_value": backtest_value,
                    "live_value": live_value,
                }
            )

    for live_path, expected_value, category in _iter_convergence_expectations():
        live_value = _get_path(live_cfg, live_path)
        if live_value != expected_value:
            mismatches.append(
                {
                    "category": category,
                    "backtest_path": "expected_convergence_value",
                    "live_path": live_path,
                    "backtest_value": expected_value,
                    "live_value": live_value,
                }
            )

    live_max_active = _get_path(live_cfg, "fund_flow.max_active_symbols")
    if backtest_runtime.max_positions != live_max_active:
        mismatches.append(
            {
                "category": "core_threshold",
                "backtest_path": "BacktestConfig.max_positions",
                "live_path": "fund_flow.max_active_symbols",
                "backtest_value": backtest_runtime.max_positions,
                "live_value": live_max_active,
            }
        )

    print(f"[alignment] backtest_config={args.backtest_config}")
    print(f"[alignment] backtest_profile={applied_profile or '<none>'}")
    print(f"[alignment] live_config={args.live_config}")

    if not mismatches:
        print("[alignment] OK: live config matches the selected backtest baseline.")
        return 0

    print(f"[alignment] MISMATCHES={len(mismatches)}")
    for item in mismatches:
        print(
            f"- [{item['category']}] {item['live_path']}: "
            f"backtest={item['backtest_value']!r} live={item['live_value']!r}"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
