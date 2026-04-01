from scripts.validate_live_backtest_alignment import (
    build_alignment_report,
    build_execution_audit_rows,
    render_markdown_report,
)


def test_execution_audit_rows_include_trailing_and_intrabar_risks() -> None:
    rows = build_execution_audit_rows()
    by_feature = {row.feature: row for row in rows}

    assert by_feature["trailing_stop"].live_exec == "YES"
    assert by_feature["trailing_stop"].status == "AMBIGUOUS_PRIORITY"
    assert by_feature["intrabar_hit_logic"].status == "MISMATCH_RISK"
    assert by_feature["stop_vs_tp_same_bar_priority"].backtest_exec == "STOP_WINS"
    assert by_feature["4h_shrink_exit"].live_exec == "YES"
    assert by_feature["4h_shrink_exit"].status == "ALIGNED_WITH_GAPS"


def test_render_markdown_report_contains_required_alignment_sections() -> None:
    rows = build_execution_audit_rows()
    report = build_alignment_report(
        backtest_config_path="config/trading_config_fund_flow.json",
        live_config_path="config/trading_config_fund_flow_live_production.json",
        applied_profile="",
        mismatches=[],
        execution_rows=rows,
    )

    md = render_markdown_report(report)

    assert "# Live / Backtest Exit Alignment Audit" in md
    assert "## Verified Backtest Priority" in md
    assert "## Live Runtime Risks" in md
    assert "`intrabar_hit_logic`" in md
    assert "`MISMATCH_RISK`" in md
