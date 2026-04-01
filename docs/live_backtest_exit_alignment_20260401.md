# Live / Backtest Exit Alignment Audit

- Date: 2026-04-01
- Backtest config: `config/trading_config_fund_flow.json`
- Live config: `config/trading_config_fund_flow_live_production.json`
- Backtest profile: `<none>`

## Executive Summary

This audit confirms that fixed TP/SL, breakeven, trailing, and partial TP all exist in both runtime paths, but they are not yet proven semantically identical.

- Trailing currently exists in both live and backtest, so it is not a dead config parameter.
- Backtest trigger order is explicit and deterministic.
- Live trigger order is richer and multi-layered, so parity is currently ambiguous.
- Intrabar hit logic is not equivalent: backtest uses bar OHLC simulation, live uses exchange protection fills.
- Protection priority is also not equivalent by proof, only by partial capability overlap.

## Config Alignment

- Config mismatch count: `2`
- [core_threshold] `fund_flow.long_open_threshold`: backtest=`0.1` live=`0.09`
- [core_threshold] `fund_flow.short_open_threshold`: backtest=`0.08` live=`0.07`

## Execution Audit Matrix

| Feature | Backtest | Live | Tests | Logs | Status |
|---|---|---|---|---|---|
| `fixed_stop_loss` | `YES` | `YES` | `PARTIAL` | `PARTIAL` | `ALIGNED_WITH_GAPS` |
| `fixed_take_profit` | `YES` | `YES` | `PARTIAL` | `PARTIAL` | `ALIGNED_WITH_GAPS` |
| `breakeven` | `YES` | `YES` | `YES` | `YES` | `ALIGNED_WITH_GAPS` |
| `trailing_stop` | `YES` | `YES` | `YES` | `YES` | `AMBIGUOUS_PRIORITY` |
| `partial_take_profit` | `YES` | `YES` | `PARTIAL` | `YES` | `ALIGNED_WITH_GAPS` |
| `intrabar_hit_logic` | `YES` | `NO_EXACT_MATCH` | `NO` | `NO` | `MISMATCH_RISK` |
| `stop_vs_tp_same_bar_priority` | `STOP_WINS` | `UNKNOWN` | `NO` | `YES` | `UNVERIFIED` |
| `protection_priority_chain` | `SIMPLIFIED_CHAIN` | `MULTI_LAYER_CHAIN` | `NO` | `YES` | `MISMATCH_RISK` |
| `4h_shrink_exit` | `YES` | `YES` | `YES` | `PARTIAL` | `ALIGNED_WITH_GAPS` |

## Detailed Findings

### fixed_stop_loss

- Config keys: `fund_flow.stop_loss_pct`
- Status: `ALIGNED_WITH_GAPS`
- Notes: Backtest uses per-bar intrabar stop checks in check_stops(); live places exchange protection via _execute_protection_v2(). Backtest trade logs show stop_loss_intrabar reasons, while live has fill/protection logs but no dedicated stop-trigger audit record.

### fixed_take_profit

- Config keys: `fund_flow.take_profit_pct`
- Status: `ALIGNED_WITH_GAPS`
- Notes: Backtest simulates take_profit_intrabar inside check_stops(); live sends TP protection orders at entry and can also reduce/close from protection logic. Execution exists on both sides, but no single shared event schema proves exact parity.

### breakeven

- Config keys: `fund_flow.breakeven_trigger_pnl_ratio, fund_flow.breakeven_lock_ratio`
- Status: `ALIGNED_WITH_GAPS`
- Notes: Backtest mutates stop_price before hit checks; live breakeven appears via trailing/partial TP profiles and _tighten_protection_for_conflict(... force_break_even=True ...). Live now emits explicit breakeven activation audit events, but backtest still lacks a symmetric event row.

### trailing_stop

- Config keys: `fund_flow.trailing_stop_enabled, fund_flow.trailing_stop_profiles, fund_flow.trailing_stop_profile_map`
- Status: `AMBIGUOUS_PRIORITY`
- Notes: Backtest trailing is explicit in check_stops(): update trailing_stop then test intrabar stop/TP. Live trailing exists through _update_partial_tp_trailing_stop() and conflict-driven protection tightening, and live now emits explicit trailing activation / priority audit events. Current code inspection confirms existence, not strict order parity.

### partial_take_profit

- Config keys: `fund_flow.take_profit_pct_levels, fund_flow.take_profit_reduce_pct_levels`
- Status: `ALIGNED_WITH_GAPS`
- Notes: Both sides execute partial reductions. Backtest logs take_profit_level_intrabar; live uses _evaluate_partial_tp() and records reduce/breakeven/tighten actions. However, accounting cadence differs because backtest materializes extra trade rows.

### intrabar_hit_logic

- Config keys: `15m OHLC intrabar approximation, exchange protection order matching`
- Status: `MISMATCH_RISK`
- Notes: Backtest explicitly approximates intrabar hits with the same 15m bar (low/high against stop/TP). Live relies on exchange-side protection order matching and runtime callbacks, so the fill path is not the same model. This is the biggest semantic mismatch risk.

### stop_vs_tp_same_bar_priority

- Config keys: `backtest.check_stops priority, live exchange matching priority`
- Status: `UNVERIFIED`
- Notes: Backtest explicitly closes on stop_loss_intrabar_both_hit when stop and TP are touched in the same bar. Live order priority under same-tick protection hits depends on exchange behavior and local reconciliation; current repo now emits same_bar_priority_evidence logs, but still has no deterministic parity check.

### protection_priority_chain

- Config keys: `fund_flow.protection_sla_*, fund_flow.trailing_*, fund_flow.take_profit_*`
- Status: `MISMATCH_RISK`
- Notes: Backtest priority is simplified: breakeven -> trailing -> stop -> partial TP -> fixed TP -> 4h shrink. Live includes exchange protection orders plus conflict tighten/reduce, partial TP, trailing updates, SLA force checks, and risk-manager protection actions. Live now emits explicit protection-priority audit events, but ordering is still not proven equivalent.

### 4h_shrink_exit

- Config keys: `fund_flow.stop_loss_config.enable_4h_shrink_exit`
- Status: `ALIGNED_WITH_GAPS`
- Notes: Backtest has an explicit 4h_shrink_exit branch after stop/TP checks. Live has an equivalent close path in decision_engine._decide_macd_v2_strategy() via resolve_4h_shrink_exit_policy() -> reason=macd_v2_4h_shrink_exit_<side>. The semantics are now verified at unit-test level, but execution still happens in different layers.

## Verified Backtest Priority

1. Breakeven mutates `stop_price` first.
2. Trailing mutates `trailing_stop` and then `stop_price`.
3. Intrabar stop/TP hit check runs on the same 15m bar.
4. If stop and TP both hit in the same bar, stop wins.
5. Partial TP levels are checked after the stop branch.
6. Fixed TP is checked after partial TP.
7. `4h_shrink_exit` is checked after stop/TP logic.

## Live Runtime Risks

- Live protection is layered: exchange TP/SL orders, conflict tightening, partial TP, trailing updates, and protection SLA.
- Because these layers are not collapsed into one explicit priority function, execution order parity with backtest is not yet proven.
- Live has protection action logs, but not a dedicated one-row audit trail describing the full trigger chain for each exit.

## Required Next Steps

- Add unit tests for same-bar stop-vs-TP priority and live-facing priority contract assumptions.
- Add explicit event logs for trailing activation, breakeven activation, and protection-priority decisions.
- Treat `4h_shrink_exit` as a live-gap item until a runtime-equivalent branch is confirmed.
- Do not continue profitability tuning based on trailing assumptions until this audit is accepted.
