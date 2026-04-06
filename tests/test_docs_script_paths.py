from pathlib import Path


def test_docs_do_not_reference_old_top_level_script_paths() -> None:
    docs_root = Path("D:/AIDCA/AI8/docs")
    old_prefixes = (
        "scripts/analyze_",
        "scripts/audit_",
        "scripts/diagnose_",
        "scripts/compare_",
        "scripts/validate_",
        "scripts/build_",
        "scripts/generate_",
        "scripts/backtest_multi_symbol.py",
        "scripts/backtest_stabilization.py",
        "scripts/run_backtest_compare_patch.py",
        "scripts/run_strategy_ablation_20260327.py",
        "scripts/_convert_csv_to_parquet.py",
        "scripts/_download_all_klines.py",
        "scripts/fix_codex.py",
        r"scripts\analyze_",
        r"scripts\audit_",
        r"scripts\diagnose_",
        r"scripts\compare_",
        r"scripts\validate_",
        r"scripts\build_",
        r"scripts\generate_",
        r"scripts\backtest_multi_symbol.py",
        r"scripts\backtest_stabilization.py",
        r"scripts\run_backtest_compare_patch.py",
        r"scripts\run_strategy_ablation_20260327.py",
        r"scripts\_convert_csv_to_parquet.py",
        r"scripts\_download_all_klines.py",
        r"scripts\fix_codex.py",
    )

    offenders: list[str] = []
    for path in docs_root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if any(prefix in text for prefix in old_prefixes):
            offenders.append(str(path))

    assert offenders == []
