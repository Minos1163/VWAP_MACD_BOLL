from pathlib import Path


def test_diagnostic_scripts_import_replay_window_directly() -> None:
    scripts = [
        Path("D:/AIDCA/AI8/scripts/diagnostics/analyze_macd_v2_attribution.py"),
        Path("D:/AIDCA/AI8/scripts/diagnostics/audit_macd_v2_blocked_samples.py"),
        Path("D:/AIDCA/AI8/scripts/diagnostics/diagnose_vwap_layers.py"),
        Path("D:/AIDCA/AI8/scripts/backtest_fund_flow_bot_like.py"),
    ]

    for script in scripts:
        text = script.read_text(encoding="utf-8")
        assert "from src.fund_flow.replay_window import" in text
        assert "filter_market_data_by_time_range" not in text


def test_scripts_directory_has_no_backup_copy_python_files() -> None:
    script_dir = Path("D:/AIDCA/AI8/scripts")
    backup_like = [path.name for path in script_dir.glob("backtest_fund_flow_bot_like - *.py")]

    assert backup_like == []


def test_top_level_scripts_directory_keeps_only_core_entrypoints() -> None:
    script_dir = Path("D:/AIDCA/AI8/scripts")
    forbidden_prefixes = ("analyze_", "audit_", "diagnose_", "compare_", "validate_", "build_", "generate_")
    top_level_temp = [
        path.name
        for path in script_dir.glob("*.py")
        if path.name.startswith(forbidden_prefixes)
    ]

    assert top_level_temp == []
