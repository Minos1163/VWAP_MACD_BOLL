"""
Validate live trading config alignment against the backtest baseline config,
and audit whether exit / protection semantics are consistently implemented.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


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
    yield (
        "fund_flow.ma10_macd_confluence.entry_hard_filter",
        "fund_flow.ma10_macd_confluence.entry_hard_filter",
        "outer_filters",
    )
    yield ("fund_flow.protection_sla_enabled", "fund_flow.protection_sla_enabled", "protection_sla")
    yield ("fund_flow.protection_sla_force_flatten", "fund_flow.protection_sla_force_flatten", "protection_sla")
    yield (
        "fund_flow.macd_mtf_strategy_v2.entry_filters.enable_flip_bullish_cvd_context_filter",
        "fund_flow.macd_mtf_strategy_v2.entry_filters.enable_flip_bullish_cvd_context_filter",
        "cvd_alignment",
    )
    yield (
        "fund_flow.macd_mtf_strategy_v2.leverage_config.use_cvd_veto_filter",
        "fund_flow.macd_mtf_strategy_v2.leverage_config.use_cvd_veto_filter",
        "cvd_alignment",
    )
    yield (
        "fund_flow.macd_mtf_strategy_v2.cvd_filter_config.enabled",
        "fund_flow.macd_mtf_strategy_v2.cvd_filter_config.enabled",
        "cvd_alignment",
    )


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


@dataclass(frozen=True)
class ExecutionAuditRow:
    feature: str
    config_keys: tuple[str, ...]
    backtest_exec: str
    live_exec: str
    unit_test_covered: str
    event_log_traceable: str
    status: str
    notes: str


def _build_config_mismatches(
    backtest_cfg: dict[str, Any],
    live_cfg: dict[str, Any],
    backtest_runtime: Any,
) -> list[dict[str, Any]]:
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
    return mismatches


def build_execution_audit_rows() -> list[ExecutionAuditRow]:
    return [
        ExecutionAuditRow(
            feature="fixed_stop_loss",
            config_keys=("fund_flow.stop_loss_pct",),
            backtest_exec="YES",
            live_exec="YES",
            unit_test_covered="PARTIAL",
            event_log_traceable="PARTIAL",
            status="ALIGNED_WITH_GAPS",
            notes=(
                "Backtest uses per-bar intrabar stop checks in check_stops(); "
                "live places exchange protection via _execute_protection_v2(). "
                "Backtest trade logs show stop_loss_intrabar reasons, while live has fill/protection "
                "logs but no dedicated stop-trigger audit record."
            ),
        ),
        ExecutionAuditRow(
            feature="fixed_take_profit",
            config_keys=("fund_flow.take_profit_pct",),
            backtest_exec="YES",
            live_exec="YES",
            unit_test_covered="PARTIAL",
            event_log_traceable="PARTIAL",
            status="ALIGNED_WITH_GAPS",
            notes=(
                "Backtest simulates take_profit_intrabar inside check_stops(); "
                "live sends TP protection orders at entry and can also reduce/close from protection logic. "
                "Execution exists on both sides, but no single shared event schema proves exact parity."
            ),
        ),
        ExecutionAuditRow(
            feature="breakeven",
            config_keys=("fund_flow.breakeven_trigger_pnl_ratio", "fund_flow.breakeven_lock_ratio"),
            backtest_exec="YES",
            live_exec="YES",
            unit_test_covered="YES",
            event_log_traceable="YES",
            status="ALIGNED_WITH_GAPS",
            notes=(
                "Backtest mutates stop_price before hit checks; live breakeven appears via trailing/partial TP "
                "profiles and _tighten_protection_for_conflict(... force_break_even=True ...). "
                "Live now emits explicit breakeven activation audit events, but backtest still lacks a symmetric event row."
            ),
        ),
        ExecutionAuditRow(
            feature="trailing_stop",
            config_keys=(
                "fund_flow.trailing_stop_enabled",
                "fund_flow.trailing_stop_profiles",
                "fund_flow.trailing_stop_profile_map",
            ),
            backtest_exec="YES",
            live_exec="YES",
            unit_test_covered="YES",
            event_log_traceable="YES",
            status="AMBIGUOUS_PRIORITY",
            notes=(
                "Backtest trailing is explicit in check_stops(): update trailing_stop then test intrabar stop/TP. "
                "Live trailing exists through _update_partial_tp_trailing_stop() and conflict-driven protection "
                "tightening, and live now emits explicit trailing activation / priority audit events. "
                "Current code inspection confirms existence, not strict order parity."
            ),
        ),
        ExecutionAuditRow(
            feature="partial_take_profit",
            config_keys=(
                "fund_flow.take_profit_pct_levels",
                "fund_flow.take_profit_reduce_pct_levels",
            ),
            backtest_exec="YES",
            live_exec="YES",
            unit_test_covered="PARTIAL",
            event_log_traceable="YES",
            status="ALIGNED_WITH_GAPS",
            notes=(
                "Both sides execute partial reductions. Backtest logs take_profit_level_intrabar; "
                "live uses _evaluate_partial_tp() and records reduce/breakeven/tighten actions. "
                "However, accounting cadence differs because backtest materializes extra trade rows."
            ),
        ),
        ExecutionAuditRow(
            feature="intrabar_hit_logic",
            config_keys=("15m OHLC intrabar approximation", "exchange protection order matching"),
            backtest_exec="YES",
            live_exec="NO_EXACT_MATCH",
            unit_test_covered="NO",
            event_log_traceable="NO",
            status="MISMATCH_RISK",
            notes=(
                "Backtest explicitly approximates intrabar hits with the same 15m bar (low/high against stop/TP). "
                "Live relies on exchange-side protection order matching and runtime callbacks, so the fill path is "
                "not the same model. This is the biggest semantic mismatch risk."
            ),
        ),
        ExecutionAuditRow(
            feature="stop_vs_tp_same_bar_priority",
            config_keys=("backtest.check_stops priority", "live exchange matching priority"),
            backtest_exec="STOP_WINS",
            live_exec="UNKNOWN",
            unit_test_covered="NO",
            event_log_traceable="YES",
            status="UNVERIFIED",
            notes=(
                "Backtest explicitly closes on stop_loss_intrabar_both_hit when stop and TP are touched in the same "
                "bar. Live order priority under same-tick protection hits depends on exchange behavior and local "
                "reconciliation; current repo now emits same_bar_priority_evidence logs, but still has no deterministic parity check."
            ),
        ),
        ExecutionAuditRow(
            feature="protection_priority_chain",
            config_keys=(
                "fund_flow.protection_sla_*",
                "fund_flow.trailing_*",
                "fund_flow.take_profit_*",
            ),
            backtest_exec="SIMPLIFIED_CHAIN",
            live_exec="MULTI_LAYER_CHAIN",
            unit_test_covered="NO",
            event_log_traceable="YES",
            status="MISMATCH_RISK",
            notes=(
                "Backtest priority is simplified: breakeven -> trailing -> stop -> partial TP -> fixed TP -> 4h shrink. "
                "Live includes exchange protection orders plus conflict tighten/reduce, partial TP, trailing updates, "
                "SLA force checks, and risk-manager protection actions. Live now emits explicit protection-priority "
                "audit events, but ordering is still not proven equivalent."
            ),
        ),
        ExecutionAuditRow(
            feature="4h_shrink_exit",
            config_keys=("fund_flow.stop_loss_config.enable_4h_shrink_exit",),
            backtest_exec="YES",
            live_exec="YES",
            unit_test_covered="YES",
            event_log_traceable="PARTIAL",
            status="ALIGNED_WITH_GAPS",
            notes=(
                "Backtest has an explicit 4h_shrink_exit branch after stop/TP checks. "
                "Live has an equivalent close path in decision_engine._decide_macd_v2_strategy() via "
                "resolve_4h_shrink_exit_policy() -> reason=macd_v2_4h_shrink_exit_<side>. "
                "The semantics are now verified at unit-test level, but execution still happens in different layers."
            ),
        ),
    ]


def build_alignment_report(
    *,
    backtest_config_path: str,
    live_config_path: str,
    applied_profile: str,
    mismatches: list[dict[str, Any]],
    execution_rows: list[ExecutionAuditRow],
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for row in execution_rows:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
    return {
        "backtest_config": backtest_config_path,
        "live_config": live_config_path,
        "backtest_profile": applied_profile or "",
        "config_alignment_ok": len(mismatches) == 0,
        "config_mismatch_count": len(mismatches),
        "config_mismatches": mismatches,
        "execution_audit": [row.__dict__ for row in execution_rows],
        "execution_status_counts": status_counts,
        "headline_findings": [
            "Trailing exists in both live and backtest, but trigger order parity is not proven.",
            "Backtest intrabar matching is explicit 15m OHLC simulation; live uses exchange protection behavior, so semantics are not identical by construction.",
            "Protection priority in live is multi-layered and richer than the simplified backtest chain.",
            "Config-value alignment alone is insufficient for close-risk optimization decisions.",
        ],
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Live / Backtest Exit Alignment Audit")
    lines.append("")
    lines.append(f"- Date: 2026-04-01")
    lines.append(f"- Backtest config: `{report['backtest_config']}`")
    lines.append(f"- Live config: `{report['live_config']}`")
    lines.append(f"- Backtest profile: `{report['backtest_profile'] or '<none>'}`")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(
        "This audit confirms that fixed TP/SL, breakeven, trailing, and partial TP all exist in both runtime paths, "
        "but they are not yet proven semantically identical."
    )
    lines.append("")
    lines.append("- Trailing currently exists in both live and backtest, so it is not a dead config parameter.")
    lines.append("- Backtest trigger order is explicit and deterministic.")
    lines.append("- Live trigger order is richer and multi-layered, so parity is currently ambiguous.")
    lines.append("- Intrabar hit logic is not equivalent: backtest uses bar OHLC simulation, live uses exchange protection fills.")
    lines.append("- Protection priority is also not equivalent by proof, only by partial capability overlap.")
    lines.append("")
    lines.append("## Config Alignment")
    lines.append("")
    lines.append(
        f"- Config mismatch count: `{report['config_mismatch_count']}`"
    )
    if report["config_mismatches"]:
        for item in report["config_mismatches"]:
            lines.append(
                f"- [{item['category']}] `{item['live_path']}`: "
                f"backtest=`{item['backtest_value']}` live=`{item['live_value']}`"
            )
    else:
        lines.append("- No config-value mismatches were found for the audited alignment set.")
    lines.append("")
    lines.append("## Execution Audit Matrix")
    lines.append("")
    lines.append("| Feature | Backtest | Live | Tests | Logs | Status |")
    lines.append("|---|---|---|---|---|---|")
    for row in report["execution_audit"]:
        lines.append(
            f"| `{row['feature']}` | `{row['backtest_exec']}` | `{row['live_exec']}` | "
            f"`{row['unit_test_covered']}` | `{row['event_log_traceable']}` | `{row['status']}` |"
        )
    lines.append("")
    lines.append("## Detailed Findings")
    lines.append("")
    for row in report["execution_audit"]:
        lines.append(f"### {row['feature']}")
        lines.append("")
        lines.append(f"- Config keys: `{', '.join(row['config_keys'])}`")
        lines.append(f"- Status: `{row['status']}`")
        lines.append(f"- Notes: {row['notes']}")
        lines.append("")
    lines.append("## Verified Backtest Priority")
    lines.append("")
    lines.append("1. Breakeven mutates `stop_price` first.")
    lines.append("2. Trailing mutates `trailing_stop` and then `stop_price`.")
    lines.append("3. Intrabar stop/TP hit check runs on the same 15m bar.")
    lines.append("4. If stop and TP both hit in the same bar, stop wins.")
    lines.append("5. Partial TP levels are checked after the stop branch.")
    lines.append("6. Fixed TP is checked after partial TP.")
    lines.append("7. `4h_shrink_exit` is checked after stop/TP logic.")
    lines.append("")
    lines.append("## Live Runtime Risks")
    lines.append("")
    lines.append("- Live protection is layered: exchange TP/SL orders, conflict tightening, partial TP, trailing updates, and protection SLA.")
    lines.append("- Because these layers are not collapsed into one explicit priority function, execution order parity with backtest is not yet proven.")
    lines.append("- Live has protection action logs, but not a dedicated one-row audit trail describing the full trigger chain for each exit.")
    lines.append("")
    lines.append("## Required Next Steps")
    lines.append("")
    lines.append("- Add unit tests for same-bar stop-vs-TP priority and live-facing priority contract assumptions.")
    lines.append("- Add explicit event logs for trailing activation, breakeven activation, and protection-priority decisions.")
    lines.append("- Treat `4h_shrink_exit` as a live-gap item until a runtime-equivalent branch is confirmed.")
    lines.append("- Do not continue profitability tuning based on trailing assumptions until this audit is accepted.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate live config alignment and audit live/backtest exit semantics."
    )
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
    parser.add_argument(
        "--report-json",
        default="",
        help="Optional JSON report path.",
    )
    parser.add_argument(
        "--report-md",
        default="",
        help="Optional Markdown report path.",
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

    mismatches = _build_config_mismatches(backtest_cfg, live_cfg, backtest_runtime)
    execution_rows = build_execution_audit_rows()
    report = build_alignment_report(
        backtest_config_path=args.backtest_config,
        live_config_path=args.live_config,
        applied_profile=applied_profile,
        mismatches=mismatches,
        execution_rows=execution_rows,
    )

    print(f"[alignment] backtest_config={args.backtest_config}")
    print(f"[alignment] backtest_profile={applied_profile or '<none>'}")
    print(f"[alignment] live_config={args.live_config}")
    print(f"[alignment] config_mismatches={len(mismatches)}")
    print(f"[alignment] execution_status_counts={json.dumps(report['execution_status_counts'], ensure_ascii=False)}")
    for headline in report["headline_findings"]:
        print(f"[alignment] {headline}")

    if args.report_json:
        json_path = Path(args.report_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[alignment] wrote_json={json_path}")

    if args.report_md:
        md_path = Path(args.report_md)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown_report(report), encoding="utf-8")
        print(f"[alignment] wrote_md={md_path}")

    mismatches_ok = len(mismatches) == 0
    semantic_ok = all(row.status == "ALIGNED_WITH_GAPS" for row in execution_rows)
    return 0 if mismatches_ok and semantic_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
