import pandas as pd

from scripts.reports.generate_live_backtest_audit_md import build_report, render_markdown_report


def test_render_markdown_report_contains_required_sections() -> None:
    summary = {
        "config_path": "config/live.json",
        "window_start_iso": "2026-03-05T03:00:00",
        "window_end_iso": "2026-04-04T03:00:00",
        "return_pct": 0.72,
        "final_capital": 10072.04,
        "total_trades": 150,
        "winning_trades": 115,
        "losing_trades": 35,
        "win_rate_pct": 76.67,
        "profit_factor": 1.17,
        "risk_metrics": {
            "max_drawdown_pct": 8.29,
            "max_drawdown_start_time": "2026-03-08 19:15:00",
            "max_drawdown_trough_time": "2026-03-18 07:00:00",
            "max_drawdown_recovery_time": "2026-04-02 13:00:00",
        },
        "stats": {
            "signals_generated": 188,
            "analysis_attempts": 110149,
            "analysis_ready": 107041,
            "signal_funnel": {
                "1_score_threshold": {"passed": 8695, "blocked": 97902, "pass_rate": 0.081569},
                "6_L1_structural": {"passed": 635, "blocked": 3927, "pass_rate": 0.139193},
                "8_L3_micro": {"passed": 4246, "blocked": 316, "pass_rate": 0.930732},
                "9_pretrade_gate": {"passed": 140, "blocked": 13, "pass_rate": 0.915033},
                "10_ai_review": {"passed": 87, "blocked": 2, "pass_rate": 0.977528},
                "12_final_fill": {"passed": 74, "blocked": 13, "pass_rate": 0.850575},
            },
        },
    }
    attribution = {
        "hold_category_counts": {"other_hold": 10, "L1_structure_failed": 3, "L3_microstructure_failed": 1},
        "hold_category_ratios_pct": {"other_hold": 71.4, "L1_structure_failed": 21.4, "L3_microstructure_failed": 7.1},
        "top_hold_reasons": [["macd_v2_hold_none_score_0.00", 10]],
        "l1_subreasons": {"atr_in_range": 3},
        "l2_subreasons": {"vwap_ok=0": 1},
        "l3_subreasons": {"imbalance_ok=0": 1},
    }
    runtime_cfg = {
        "fund_flow": {
            "entry_hard_gates_enabled": True,
            "entry_hard_gate_adx_min": 22,
            "entry_hard_gate_atr_min": 0.006,
            "entry_hard_gate_atr_max": 0.02,
            "entry_hard_gate_spread_bps_max": 0.0008,
            "pretrade_risk_gate": {
                "enabled": True,
                "atr_ratio_hard_block": 3.5,
                "equity_usage_block": 0.85,
                "dd_exit_threshold": 0.1,
            },
            "stop_loss_pct": 0.02,
            "take_profit_pct": 0.04,
            "breakeven_trigger_pnl_ratio": 0.012,
            "breakeven_lock_ratio": 0.0025,
            "time_exit_minutes": 30,
            "time_exit_min_profit_pct": 0.0035,
            "backtest": {"same_bar_tp_priority_mode": "tp1_before_stop"},
            "macd_mtf_strategy_v2": {
                "scoring_weights": {
                    "weight_1h_direction": 0.4,
                    "weight_4h_direction": 0.2,
                    "weight_vwap": 0.2,
                    "weight_15m_entry": 0.05,
                    "weight_volume": 0.15,
                },
                "entry_thresholds": {
                    "default": 0.845,
                    "red_bar_growing": 0.855,
                    "flip_bullish": 0.84,
                },
                "entry_filters": {
                    "min_signal_score": 0.87,
                    "min_vwap_score_for_entry": 0.12,
                    "preflip_trial_min_signal_score": 0.7,
                    "soft_15m_entry_score": 0.3,
                },
            },
        }
    }
    trades = pd.DataFrame(
        [
            {
                "symbol": "KASUSDT",
                "side": "long",
                "pnl": -100.0,
                "reason": "stop_loss_intrabar",
                "signal_type_1h": "red_bar_growing",
                "vwap_state": "long_reclaim_confirmed",
                "is_trial_entry": False,
                "signal_score": 0.88,
            },
            {
                "symbol": "BTCUSDT",
                "side": "short",
                "pnl": 50.0,
                "reason": "take_profit_level_intrabar",
                "signal_type_1h": "green_bar_growing",
                "vwap_state": "short_retest_reject",
                "is_trial_entry": True,
                "signal_score": 0.79,
            },
        ]
    )
    candidate_ledger = pd.DataFrame(
        [
            {"signal_type_1h": "red_bar_growing", "vwap_state": "long_reclaim_confirmed", "is_trial_entry": False, "final_opened": True},
            {"signal_type_1h": "green_bar_growing", "vwap_state": "short_retest_reject", "is_trial_entry": True, "final_opened": False, "final_reject_reason": "target_portion_below_min_open"},
        ]
    )
    alignment_report = {
        "config_mismatch_count": 0,
        "execution_status_counts": {"MISMATCH_RISK": 2, "UNVERIFIED": 1},
        "headline_findings": ["same-bar priority remains unverified"],
    }

    report = build_report(
        summary=summary,
        attribution=attribution,
        runtime_cfg=runtime_cfg,
        trades=trades,
        candidate_ledger=candidate_ledger,
        alignment_report=alignment_report,
    )
    md = render_markdown_report(report)

    assert "# 最新30天实盘配置回测审计" in md
    assert "## 2. 亏损归因" in md
    assert "## 3. 开仓链路" in md
    assert "## 4. 打分权重与门槛" in md
    assert "## 5. 风控与执行现实性" in md
    assert "same_bar_tp_priority_mode" in md
    assert "trial entry" in md
