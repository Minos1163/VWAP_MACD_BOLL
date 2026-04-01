# Claude Review Package: MACD V2 Round 2 Optimization

Date: 2026-03-31
Workspace: `D:\AIDCA\AI8`
Primary config: `config/trading_config_fund_flow.json`
Backtest window: `2026-03-01 00:00:00` to `2026-03-31 23:59:59`

## 1. Review Objective

Round 1 successfully removed the major loss pockets, but Round 2 aimed to answer a narrower question:

- Can we keep the cleaner risk profile
- while restoring or preserving trade opportunity
- without letting MDD drift materially higher

The main technical uncertainty was whether `flip_bullish` CVD context filtering should be applied to `trial` entries, because the surviving 30-day alpha stream is almost entirely `flip_bullish trial`.

## 2. Root-Cause Findings

### 2.1 TP config hygiene

Before this round, the config still had:

- `fund_flow.take_profit_pct = 0.04`
- `risk.take_profit_default_percent = 0.02`

Runtime decision code prefers `fund_flow.take_profit_pct`, but the default-layer mismatch is still a governance risk because some fallback and management paths still inspect `risk.*`.

Final decision:

- Align `risk.take_profit_default_percent` to `0.04`

### 2.2 Trial-path CVD filter was not actually wired

Code inspection showed `flip_bullish` CVD context filter existed, but was guarded by `not is_trial_entry`, so it only filtered full-size entries and never affected the current dominant `trial` path.

This was corrected in code so future experiments can test the filter honestly.

## 3. Round 2 Changes Tested

### 3.1 Implemented and kept

- `risk.take_profit_default_percent: 0.02 -> 0.04`
- `stable_bear_continuation_min_adx_1h: 30.0 -> 25.0`
- `boll_stop_atr_multiplier: 0.5 -> 0.8`

### 3.2 Implemented for ablation only, then rejected from main config

- `enable_flip_bullish_cvd_context_filter: false -> true`
- CVD strict threshold:
  - `flip_bullish_max_cvd_upper_wick_ratio = 0.20`
  - `flip_bullish_min_cvd_1h_delta_ratio = 0.03`
- Relaxed follow-up threshold:
  - `flip_bullish_min_cvd_1h_delta_ratio = 0.02`

## 4. Backtest Results

### 4.1 Round 1 baseline before this round

Source:

- `output/backtest/bot_like_summary_20260331_174523.json`

Metrics:

- return: `+0.87%`
- trades: `4`
- win rate: `75.00%`
- MDD: `0.32%`

### 4.2 Round 2 strict CVD on trial path

Source:

- `output/backtest/bot_like_summary_20260331_180956.json`

Metrics:

- return: `+0.84%`
- trades: `1`
- win rate: `100.00%`
- MDD: `0.08%`

Interpretation:

- too sparse
- not acceptable as the new main config
- overfilters the surviving alpha stream

### 4.3 Round 2 relaxed CVD delta (`0.03 -> 0.02`)

Source:

- `output/backtest/bot_like_summary_20260331_181642.json`

Metrics:

- return: `+0.84%`
- trades: `1`
- win rate: `100.00%`
- MDD: `0.08%`

Interpretation:

- lowering the CVD delta threshold does not recover opportunity
- the issue is structural, not just boundary sensitivity

### 4.4 Final selected main config after Round 2

Source:

- `output/backtest/bot_like_summary_20260331_183034.json`

Metrics:

- return: `+0.87%`
- trades: `4`
- win rate: `75.00%`
- MDD: `0.32%`

Interpretation:

- TP alignment and stop/threshold hygiene are safe to keep
- CVD-on-trial should remain available in code, but disabled in production config until a broader sample validates it

## 5. Final Main Config State

### 5.1 Retained guards from Round 1

- `flip_bullish_trial_score_window_enabled = true`
- `flip_bullish_trial_score_min = 0.80`
- `flip_bullish_trial_score_max = 0.87`
- `symbol_overrides.DOGEUSDT.disable_flip_bullish_trial = true`
- `symbol_overrides.BCHUSDT.disable_flip_bullish_trial = true`
- `symbol_overrides.AAVEUSDT.disable_flip_bullish_trial = true`
- `pretrade_risk_gate.enabled = true`
- `pretrade_risk_gate.use_hard_rules_only = true`
- `pretrade_risk_gate.equity_usage_block = 0.60`

### 5.2 New retained settings from Round 2

- `risk.take_profit_default_percent = 0.04`
- `fund_flow.take_profit_pct = 0.04`
- `stable_bear_continuation_min_adx_1h = 25.0`
- `boll_stop_atr_multiplier = 0.8`

### 5.3 Explicitly rejected for now

- `enable_flip_bullish_cvd_context_filter = true`

Current status in main config:

- `enable_flip_bullish_cvd_context_filter = false`

## 6. Code Changes Relevant for Review

### 6.1 Trial-path CVD support added

File:

- `D:\AIDCA\AI8\src\fund_flow\macd_strategy_v2.py`

Behavior:

- `flip_bullish` CVD context filter can now apply to `trial` entries when enabled
- this was required to test the hypothesis fairly

Mirror sync also applied to:

- `D:\AIDCA\AI8\src\config\macd_strategy_v2.py`

### 6.2 Regression tests added

Files:

- `D:\AIDCA\AI8\tests\test_macd_strategy_v2_4h_scoring.py`
- `D:\AIDCA\AI8\tests\test_fund_flow_bot_regressions.py`

Verified command:

```powershell
pytest .\tests\test_macd_strategy_v2_4h_scoring.py::test_flip_bullish_trial_score_window_blocks_out_of_window_scores `
       .\tests\test_macd_strategy_v2_4h_scoring.py::test_flip_bullish_cvd_context_filter_blocks_full_size `
       .\tests\test_macd_strategy_v2_4h_scoring.py::test_flip_bullish_cvd_context_filter_blocks_preflip_trial_entry `
       .\tests\test_fund_flow_decision_engine.py::test_collect_symbol_signal_override_items_reads_top_level_symbol_overrides `
       .\tests\test_fund_flow_decision_engine.py::test_macd_v2_engine_for_symbol_applies_trial_specific_overrides `
       .\tests\test_fund_flow_bot_regressions.py::test_live_config_stage2_ablation_disables_outer_entry_filters_and_ai_review `
       .\tests\test_fund_flow_bot_regressions.py::test_fund_flow_main_config_applies_optimization_guardrails `
       .\tests\test_fund_flow_bot_regressions.py::test_fund_flow_main_config_applies_iteration2_quality_recovery_settings -q
```

Result:

- `8 passed`

## 7. Questions for Claude

Please audit the following:

1. Is the conclusion sound that `trial-path CVD filtering` is currently too expensive in opportunity cost for a 30-day production-like window?
2. Should CVD be reintroduced only as a symbol-specific gate for `DOGE/BCH/AAVE`, rather than globally on all `flip_bullish trial` entries?
3. Is `boll_stop_atr_multiplier = 0.8` a reasonable intermediate value, or should it be adaptive by market regime?
4. Does `stable_bear_continuation_min_adx_1h = 25.0` make sense as a next-step diversity lever even though it did not create incremental fills in this 30-day window?
5. Would you keep the code path for `trial` CVD support merged but disabled, or revert it until a larger experiment is scheduled?

## 8. Files to Review Together

- `D:\AIDCA\AI8\config\trading_config_fund_flow.json`
- `D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_opt_d_cvd_relaxed.json`
- `D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_opt_e_no_trial_cvd.json`
- `D:\AIDCA\AI8\src\fund_flow\macd_strategy_v2.py`
- `D:\AIDCA\AI8\tests\test_macd_strategy_v2_4h_scoring.py`
- `D:\AIDCA\AI8\tests\test_fund_flow_bot_regressions.py`
- `D:\AIDCA\AI8\output\backtest\bot_like_summary_20260331_183034.json`
- `D:\AIDCA\AI8\output\backtest\bot_like_summary_20260331_180956.json`
- `D:\AIDCA\AI8\output\backtest\bot_like_summary_20260331_181642.json`
