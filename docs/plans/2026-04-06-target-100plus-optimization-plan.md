# Target 100% Plus Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Align the 5m short-main / long-pockets strategy with the requested target structure, then iterate toward `100%+` 30d return, `80%+` win rate, and `300-500` 30d opens.

**Architecture:** Start by removing known simulation optimism and configuration drift, then align leverage/position sizing/active symbol limits to the target style, then widen opportunity count through pocket-tiered thresholds and targeted pocket expansion. Use fast screening where possible and confirm finalists with 30d bot-like replay.

**Tech Stack:** Python, JSON strategy configs, pytest, bot-like replay backtest, MACD V2 decision engine.

---

### Task 1: Freeze a trustworthy baseline

**Files:**
- Modify: `config/candidates/trading_config_fund_flow_review_5m_short_main_long_pockets_20260405.json`
- Create: `config/candidates/trading_config_fund_flow_review_5m_short_main_long_pockets_stopfirst_20260406.json`
- Test: existing replay verification commands

**Steps:**
1. Clone current 5m pocket config into a `stop_first` audit candidate.
2. Set `same_bar_tp_priority_mode=stop_first`.
3. Keep all other behavior unchanged.
4. Run 30d bot-like replay.
5. Record delta vs current `tp1_before_stop` result.

### Task 2: Align leverage and portfolio shape to target

**Files:**
- Modify: `src/fund_flow/macd_strategy_v2.py`
- Modify: `src/fund_flow/decision_engine.py`
- Modify: `src/config/decision_engine.py`
- Modify: `tests/test_macd_strategy_v2_4h_scoring.py`
- Create/Modify: target candidate config JSON

**Steps:**
1. Add failing tests for configurable leverage tiers.
2. Implement config-driven `2X/3X/4X` leverage mapping.
3. Keep watchlist and trial-entry caps intact.
4. Set candidate config to `min/default/max = 2/3/4`.
5. Set `max_active_symbols = 5`.
6. Set base position sizing toward `0.20 / 0.25 / 0.30`.

### Task 3: Eliminate final fill collapse

**Files:**
- Modify: candidate config JSONs
- Test: replay candidate ledger / final reject analysis

**Steps:**
1. Lower `min_open_portion`.
2. Raise base target portion into target range.
3. Re-run replay or screening backtest.
4. Check whether `target_portion_below_min_open` rejects fall materially.

### Task 4: Increase opportunity count without destroying quality

**Files:**
- Modify: candidate config JSONs
- Optionally create: diagnostics helper script under `scripts/diagnostics/`

**Steps:**
1. Relax score thresholds in controlled stages.
2. Demote or disable weak pockets.
3. Expand adjacent profitable pockets first.
4. Re-check trade count, PF, MDD, and pocket attribution after each move.

### Task 5: Decide whether L1 structure is over-blocking

**Files:**
- Optionally create: `scripts/diagnostics/...`
- Modify: config only if diagnostics justify change

**Steps:**
1. Inspect blocked-signal quality.
2. Only loosen L1 if false-positive blocking is clearly high.
3. Prefer pocket-specific bypasses over global weakening.

### Task 6: Validate against target metrics

**Files:**
- Output only: replay summaries and docs

**Steps:**
1. Run 30d bot-like replay on the best candidate.
2. Compare:
   - return
   - win rate
   - actual opens
   - max drawdown
   - capacity blocks
3. If targets still miss, continue only with the largest remaining bottleneck.

