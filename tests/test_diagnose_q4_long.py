from scripts.diagnose_q4_long import _classify_downstream_bucket, _summarize_execution_events


def test_classify_downstream_bucket_marks_generated_long_not_executed() -> None:
    row = {
        "order_filled": False,
        "signal_direction": "long",
        "reject_reason_code": "",
        "reject_stage": "",
        "stage": "final",
        "stage_path_text": (
            "1h_direction > q4_rsi_lead_preflip_long > threshold_check > "
            "pocket_entry_requirements > final"
        ),
    }

    assert _classify_downstream_bucket(row) == "long_signal_not_executed"


def test_classify_downstream_bucket_keeps_threshold_blocks_distinct() -> None:
    row = {
        "order_filled": False,
        "signal_direction": "neutral",
        "reject_reason_code": "信号评分低于阈值",
        "reject_stage": "pocket_entry_requirements",
        "stage": "pocket_entry_requirements",
        "stage_path_text": "score_aggregation > threshold_check > pocket_entry_requirements",
    }

    assert _classify_downstream_bucket(row) == "threshold_check"


def test_summarize_execution_events_marks_not_submitted() -> None:
    summary = _summarize_execution_events(
        [
            {
                "event_type": "skipped",
                "reason": "entry_cooldown_active",
                "event_time": "2026-03-09 03:00:00",
                "submit_time": "2026-03-09 03:00:00",
                "position_blocked_reason": "score_tier_unmatched",
                "position_lowest_score_tier_min": 0.68,
            }
        ]
    )

    assert summary["execution_layer_bucket"] == "execution_not_submitted"
    assert summary["execution_submit_reason"] == "entry_cooldown_active"
    assert summary["execution_followup_status"] == "not_applicable"
    assert summary["execution_position_blocked_reason"] == "score_tier_unmatched"
    assert summary["execution_position_lowest_score_tier_min"] == 0.68


def test_summarize_execution_events_marks_submitted_but_not_filled() -> None:
    summary = _summarize_execution_events(
        [
            {
                "event_type": "submitted",
                "reason": "order_submitted",
                "event_time": "2026-03-09 03:00:00",
                "submit_time": "2026-03-09 03:00:00",
            },
            {
                "event_type": "canceled",
                "reason": "ioc_unfilled",
                "event_time": "2026-03-09 03:15:00",
                "submit_time": "2026-03-09 03:00:00",
            },
        ]
    )

    assert summary["execution_layer_bucket"] == "execution_submitted_but_not_filled"
    assert summary["execution_submit_reason"] == "order_submitted"
    assert summary["execution_followup_status"] == "canceled"
    assert summary["execution_followup_reason"] == "ioc_unfilled"
