# MACD V2 E2 Follow-up Todolist

Date: 2026-04-01

Goal:
- Keep the E2 `long_dual_support` structural repair intact
- Improve the low-efficiency `green_bar_growing|short_dual_pressure` pocket
- Mitigate oversized `4h_shrink_exit` losses without touching global TP/SL structure
- Separate alpha improvement from simple risk amplification

Checklist:

- [x] Re-read latest 30d production/E2 attribution
- [x] Confirm `green_bar_growing|short_dual_pressure` is the next low-efficiency high-count pocket
- [x] Implement config-driven pocket gate for additional pockets via existing `pocket_entry_overrides`
- [x] Add config-driven `4h_shrink_exit` loss-mitigation partial reduce path in live decision engine
- [x] Add matching `4h_shrink_reduce` behavior in backtest execution
- [x] Add/extend unit tests for:
  - partial `4h_shrink_reduce` in backtest
  - partial close decision in live engine
  - existing E2 pocket override logic remains green
- [x] Generate candidate A:
  - `green_bar_growing|short_dual_pressure`
  - `min_signal_score = 0.88`
  - `min_vwap_score = 0.18`
  - `require_cvd_ok = true`
  - `require_strict_1h_confirmation = true`
- [x] Generate candidate B:
  - candidate A
  - `shrink_exit_loss_mitigation_enabled = true`
  - `shrink_exit_loss_mitigation_pnl_threshold = -0.005`
  - `shrink_exit_loss_mitigation_exit_ratio = 0.60`
  - `shrink_exit_loss_mitigation_ignore_if_pnl_gt = 0.01`
- [x] Generate candidate C:
  - candidate B
  - `default_target_portion = 0.22`
- [x] Re-run 30d baseline with current production config
- [x] Re-run 30d backtests for candidates A/B/C
- [x] Freeze stable result files for later review
- [x] Run targeted regression suite
- [x] Run syntax compilation checks

Result snapshot:

- Baseline:
  - [v2_summary_20260401_baseline.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_baseline.json)
  - `438 trades / 84.02% WR / +12.31% / PF 1.76 / MDD 2.34%`
- Candidate A:
  - [v2_summary_20260401_cand_a.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_cand_a.json)
  - `321 trades / 87.23% WR / +17.90% / PF 3.55 / MDD 1.15%`
- Candidate B:
  - [v2_summary_20260401_cand_b.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_cand_b.json)
  - `324 trades / 86.42% WR / +18.06% / PF 3.62 / MDD 1.15%`
- Candidate C:
  - [v2_summary_20260401_cand_c.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_cand_c.json)
  - `401 trades / 85.54% WR / +21.62% / PF 3.12 / MDD 1.70%`

Key conclusions:

- Candidate A is the main alpha improvement.
  - It removes `green_bar_growing|short_dual_pressure` entirely in this 30d sample.
  - Return improves from `+12.31%` to `+17.90%`.
  - Drawdown drops from `2.34%` to `1.15%`.
- Candidate B adds a smaller but real exit-side improvement.
  - `4h_shrink_exit` shifts from `7 trades / -302.73` to `6 trades / -139.89`.
  - Return rises from `+17.90%` to `+18.06%`.
- Candidate C is mostly a risk-amplified version of B.
  - It pushes return to `+21.62%`.
  - It also raises trade count and drawdown versus B.

Verification:

- `pytest tests/test_pocket_entry_override.py tests/test_backtest_profit_depth.py tests/test_fund_flow_decision_engine.py tests/test_fund_flow_bot_regressions.py tests/test_macd_strategy_v2_4h_scoring.py -q`
- Result: `96 passed`
- `python -m py_compile src/fund_flow/macd_strategy_v2.py src/fund_flow/decision_engine.py scripts/backtest_macd_v2.py tests/test_backtest_profit_depth.py tests/test_fund_flow_decision_engine.py`
- Result: passed
