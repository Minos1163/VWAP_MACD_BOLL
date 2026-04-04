# Pure Backtest Execution-Realism Plan

> Goal: stop pretending that removing live outer gates is enough. Rebuild the pure backtest so its execution path is harder to flatter and closer to what runtime can actually realize.

## Verdict

The first structural weak link is not VWAP, not AI, and not symbol filters.

It is this:

- pending orders can fill on a bar
- newly filled symbols are skipped by the stop/TP pass on that same bar
- therefore pure backtest can silently miss entry-bar TP/SL/partial interactions

That creates a real execution-model gap.

## Phase 1: Fix the new-fill same-bar gap

### Task 1

Add explicit config support in pure backtest:

- `entry_bar_same_bar_enabled`

Source of truth stays:

- `fund_flow.backtest.entry_bar_same_bar_enabled`

### Task 2

After a pending order fills, immediately run an entry-bar same-bar handler that only allows:

- partial TP
- full TP
- stop loss

Do **not** enable extra entry-bar:

- trailing updates
- breakeven promotion
- shrink exits
- time exits

Reason:

- first remove the known optimistic omission
- do not mix that fix with new bar-ordering assumptions

### Task 3

Add regression tests proving:

1. `tp1_before_stop` works on the fill bar for newly filled orders
2. disabling `entry_bar_same_bar_enabled` keeps the old behavior
3. `stop_first` preserves the conservative branch on the fill bar

## Phase 2: Re-measure pure vs pure-runtime bot-like

After Phase 1 passes:

- rerun pure 30d
- rerun pure-runtime bot-like 30d
- check whether the gap narrows honestly or just shifts shape

Primary questions:

- do trade counts converge
- does same-bar PnL distribution change materially
- does MDD move because of more realistic same-bar exits

## Phase 3: Attack remaining execution optimism

Only after Phase 1 is stable:

- audit entry/exit sequencing inside pure backtest
- compare pending-order fill semantics vs runtime semantics
- inspect whether pure backtest still grants runner/partial behavior that runtime cannot realize

## Pending-Order Sequencing Parity

The next structural mismatch after fill-bar same-bar is pending-order lifecycle:

- runtime decisions default to `IOC`, but execution router can degrade `IOC -> GTC`
- runtime keeps a live pending entry order on exchange and simply skips duplicate entries on later cycles
- pure backtest was still reading legacy root keys for `entry_time_in_force / gtc_expire_bars`
- pure backtest also canceled `GTC` orders immediately on signal reversal, which runtime does not do by default

Required parity baseline:

- source of truth for pending-order model stays under `fund_flow.backtest`
- `GTC` reversal cancellation must be explicit, not implicit
- default pure behavior should match runtime open-order persistence more closely than the old auto-cancel branch
- `IOC` that would take immediately at the next bar open should not be granted an optimistic open fill; it should degrade to `GTC` and only become fill-eligible on a later bar

## Phase 4: Fix fake-IOC pricing

The `IOC -> GTC` fallback exposed a deeper flaw:

- pure backtest was generating long entry limits as `price * (1 + entry_slippage)`
- and short entry limits as `price * (1 - entry_slippage)`
- that makes nominal `IOC` orders structurally aggressive / marketable instead of passive

This is not a gate problem. It is entry-price construction drift.

Required correction:

- pure pending-order entry price must be computed from an explicit passive offset
- long passive limit must sit below the reference price
- short passive limit must sit above the reference price
- passive offset config must live under `fund_flow.backtest.entry_passive_offset_pct`
- `entry_slippage` remains a separate concept; it must not continue doubling as passive order construction

Acceptance evidence for this phase:

- long entry orders no longer start life above the signal reference price
- short entry orders no longer start life below the signal reference price
- direct `IOC` fills must happen via intrabar touch, not because the next bar open was immediately marketable
- the backtest summary must keep reporting `entry_tif_breakdown / entry_degradation_breakdown` so degradation cannot be hidden

Current state after Phase 4:

- the fake `IOC` direction bug is fixed
- pure 30d no longer collapses to near-zero because of wrong-side fallback churn
- but the model still reports `IOC->IOC / direct_fill` for every completed trade

That means the next weak link is no longer gate logic and no longer the fixed-offset sign bug.
It is the remaining simplicity of passive pricing itself:

- state/volatility-aware passive offsets are now in place
- but they are still producing all fills as direct bar-touch IOC fills
- the next audit should focus on whether passive pricing should key off richer structure than `base offset + ATR floor + state multipliers`

Current fill-sweetness audit result:

- the latest pure baseline has `491` trade rows but only `222` unique direct-IOC entries
- `62.16%` of those unique entries are `wick-only touch`
- only `37.84%` close through the passive limit on the fill bar
- requiring `close_through OR penetration >= 5bps` shrinks the retained set from `222` to `179`
- retained PnL under that stricter filter would shrink from about `7105` to about `5316`

That is strong evidence that 15m bar-touch still overstates passive fill plausibility.

## Non-goals

Do not do these in this phase:

- no VWAP/4H tuning
- no symbol reject work
- no AI shortlist work
- no new alpha filters
- no cosmetic return optimization

## Acceptance standard

Phase 1 is accepted only if:

- tests prove newly filled orders can resolve same-bar TP/SL
- no new look-ahead is introduced
- no new trailing/breakeven optimism is smuggled into entry-bar handling
- pure backtest behavior changes for the right reason, not because we silently changed unrelated exits
