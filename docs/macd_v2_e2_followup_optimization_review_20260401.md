# MACD V2 E2 Follow-up Optimization Review

Date: 2026-04-01

Scope:
- Keep production E2 as the baseline
- Optimize the next weakest high-frequency pocket
- Avoid changing global stop-loss, take-profit, or long-side E2 structure

## Baseline

Source:
- [v2_summary_20260401_baseline.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_baseline.json)
- [v2_trades_20260401_baseline.csv](D:\AIDCA\AI8\output\backtest\v2_trades_20260401_baseline.csv)

Metrics:
- `438 trades`
- `84.02%` win rate
- `+12.31%` return
- `PF 1.76`
- `MDD 2.34%`
- `avg_win +10.07`
- `avg_loss -30.04`

Observed weak pocket:
- `green_bar_growing|short_dual_pressure`
- `192 trades`
- `81.25%` win rate
- `+315.97` pnl
- `avg_win +10.56`
- `avg_loss -36.98`

Observed exit leak:
- `4h_shrink_exit`
- `7 trades`
- `-302.73` pnl

## Candidate A

Config:
- [trading_config_fund_flow_e2_followup_a_short_pressure_gate.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_e2_followup_a_short_pressure_gate.json)

Logic change:
- add `pocket_entry_overrides["green_bar_growing|short_dual_pressure"]`
- `min_signal_score = 0.88`
- `min_vwap_score = 0.18`
- `require_cvd_ok = true`
- `require_strict_1h_confirmation = true`

Result:
- [v2_summary_20260401_cand_a.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_cand_a.json)
- `321 trades / 87.23% WR / +17.90% / PF 3.55 / MDD 1.15%`

Interpretation:
- This is the main alpha improvement.
- In the 30d sample, `green_bar_growing|short_dual_pressure` is reduced to `0 trades`.
- The freed capacity shifts mostly into:
  - `long_reclaim_confirmed: +1480.97`
  - `short_retest_reject: +785.06`

## Candidate B

Config:
- [trading_config_fund_flow_e2_followup_b_short_pressure_gate_shrink_reduce.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_e2_followup_b_short_pressure_gate_shrink_reduce.json)

Additional logic change:
- enable `4h_shrink` loss-time partial reduce
- `pnl_threshold = -0.005`
- `exit_ratio = 0.60`
- `ignore_if_pnl_gt = 0.01`

Result:
- [v2_summary_20260401_cand_b.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_cand_b.json)
- `324 trades / 86.42% WR / +18.06% / PF 3.62 / MDD 1.15%`

Interpretation:
- Relative to candidate A, this is a smaller but clean exit-side gain.
- `4h_shrink` loss bucket improves from:
  - baseline: `7 trades / -302.73`
  - candidate B: `6 trades / -139.89`
- Exit reason shifts from full `4h_shrink_exit` to partial `4h_shrink_reduce`.

## Candidate C

Config:
- [trading_config_fund_flow_e2_followup_c_short_pressure_gate_shrink_reduce_size022.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_e2_followup_c_short_pressure_gate_shrink_reduce_size022.json)

Additional logic change:
- `default_target_portion: 0.18 -> 0.22`

Result:
- [v2_summary_20260401_cand_c.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_cand_c.json)
- `401 trades / 85.54% WR / +21.62% / PF 3.12 / MDD 1.70%`

Interpretation:
- This is mostly a controlled risk amplification on top of candidate B.
- Return rises, but:
  - trade count rises from `324` to `401`
  - drawdown rises from `1.15%` to `1.70%`
- It is stronger than baseline, but less “pure” than A/B.

## Recommendation

If the goal is pure strategy quality:
- Candidate B is the best next baseline.

Why:
- pocket-level alpha improvement is preserved
- `4h_shrink` losses are materially reduced
- return improves to `+18.06%`
- drawdown stays very low at `1.15%`

If the goal is more aggressive profit capture:
- Candidate C is the best high-return candidate, but it is partly a sizing change rather than only alpha improvement.
