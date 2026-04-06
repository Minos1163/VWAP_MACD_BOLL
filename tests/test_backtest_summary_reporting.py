from types import SimpleNamespace

from scripts.backtest_macd_v2 import BacktestConfig, build_backtest_summary
from src.fund_flow.macd_strategy_v2 import MACDStrategyV2Config


def test_build_backtest_summary_prefers_signals_generated_counter() -> None:
    config = BacktestConfig(
        config_path="config/test.json",
        profile_name="",
        initial_capital=1000.0,
    )
    strategy_config = MACDStrategyV2Config()
    engine = SimpleNamespace(
        capital=1010.0,
        trades=[],
        equity_curve=[],
        max_drawdown_value=0.0,
        max_drawdown_pct=0.0,
        max_drawdown_start_time="",
        max_drawdown_trough_time="",
        max_drawdown_recovery_time="",
    )

    summary = build_backtest_summary(
        config=config,
        strategy_config=strategy_config,
        engine=engine,
        available_symbols=[],
        missing_symbols=[],
        stats={"timeline_points": 12, "signals": 0, "signals_generated": 7},
    )

    assert summary["signals_generated"] == 7
