from pathlib import Path

from scripts.analyze_same_bar_priority_evidence import (
    extract_same_bar_records,
    load_jsonl,
    render_markdown_report,
    summarize_same_bar_records,
)


def test_extract_and_summarize_same_bar_priority_records(tmp_path: Path) -> None:
    log_path = tmp_path / "exit_protection_audit_utc.jsonl"
    log_path.write_text(
        "\n".join(
            [
                '{"event_type":"same_bar_priority_evidence","symbol":"BTCUSDT","decision_reason":"take_profit_intrabar","same_bar_priority_candidate":true,"inferred_trigger_source":"tp_like_reason","evidence_strength":"medium","pre_snapshot":{"has_tp":true,"has_sl":true,"order_count":2},"fill_summary":{"fill_count":1,"source":"user_trades"}}',
                '{"event_type":"same_bar_priority_evidence","symbol":"ETHUSDT","decision_reason":"risk_protect_breakeven","same_bar_priority_candidate":true,"inferred_trigger_source":"sl_or_protection_like_reason","evidence_strength":"weak","pre_snapshot":{"has_tp":true,"has_sl":true,"order_count":2},"fill_summary":{"fill_count":1,"source":"order_fallback"}}',
                '{"event_type":"trailing_activated","symbol":"BTCUSDT"}',
            ]
        ),
        encoding="utf-8",
    )

    rows = load_jsonl(log_path)
    records = extract_same_bar_records(rows)
    summary = summarize_same_bar_records(records)

    assert len(records) == 2
    assert summary["candidate_count"] == 2
    assert summary["inferred_trigger_source_counts"]["tp_like_reason"] == 1
    assert summary["inferred_trigger_source_counts"]["sl_or_protection_like_reason"] == 1
    assert summary["symbol_counts"]["BTCUSDT"] == 1


def test_render_markdown_report_mentions_top_symbols_and_inference() -> None:
    records = [
        {
            "symbol": "BTCUSDT",
            "decision_reason": "take_profit_intrabar",
            "same_bar_priority_candidate": True,
            "inferred_trigger_source": "tp_like_reason",
            "evidence_strength": "medium",
            "pre_snapshot": {"has_tp": True, "has_sl": True, "order_count": 2},
            "fill_summary": {"fill_count": 1, "source": "user_trades"},
        },
        {
            "symbol": "ETHUSDT",
            "decision_reason": "risk_protect_breakeven",
            "same_bar_priority_candidate": True,
            "inferred_trigger_source": "sl_or_protection_like_reason",
            "evidence_strength": "weak",
            "pre_snapshot": {"has_tp": True, "has_sl": True, "order_count": 2},
            "fill_summary": {"fill_count": 1, "source": "order_fallback"},
        },
    ]
    summary = summarize_same_bar_records(records)
    md = render_markdown_report(summary, records)

    assert "# Same-Bar Stop vs TP Priority Evidence" in md
    assert "BTCUSDT" in md
    assert "tp_like_reason" in md
    assert "sl_or_protection_like_reason" in md
