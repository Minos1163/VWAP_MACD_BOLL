# Pure Strategy Runtime Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor live `fund_flow` runtime so entry decisions, thresholds, weights, sizing, leverage, and core exits are sourced from `MACDStrategyV2Engine`, with execution-layer filters downgraded to optional compatibility shells.

**Architecture:** Add an explicit `pure_strategy_runtime` mode under `fund_flow`. In this mode, `FundFlowDecisionEngine` remains the single strategy truth source, while `TradingBot` bypasses `signal_pool`, `ma10_macd`, `pretrade_risk_gate`, AI shortlist/final review, and dynamic capacity throttles for new entries. Essential execution realism remains in the bot: order placement, position state, same-bar handling, TP/SL, and account-level protection.

**Tech Stack:** Python, pytest, existing `MACDStrategyV2Engine`, `FundFlowDecisionEngine`, `TradingBot`, JSON config.

---

### Task 1: Add failing tests for pure runtime bypass in decision engine

**Files:**
- Modify: `D:\AIDCA\AI8\tests\test_fund_flow_decision_engine.py`
- Modify: `D:\AIDCA\AI8\src\fund_flow\decision_engine.py`

**Step 1: Write the failing test**

Add tests that assert:
- pure runtime mode disables `entry_hard_gate`
- pure runtime mode suppresses runtime `signal_pool_id`
- pure runtime mode advertises itself in decision metadata

**Step 2: Run test to verify it fails**

Run:
```powershell
pytest D:\AIDCA\AI8\tests\test_fund_flow_decision_engine.py -q
```

**Step 3: Implement minimal decision-engine bypass**

Add config parsing and helper methods in `FundFlowDecisionEngine`.

**Step 4: Re-run targeted tests**

Run the same pytest command and confirm PASS.

### Task 2: Add failing tests for pure runtime bypass in live bot entry chain

**Files:**
- Modify: `D:\AIDCA\AI8\tests\test_fund_flow_bot_regressions.py`
- Modify: `D:\AIDCA\AI8\src\app\fund_flow_bot.py`

**Step 1: Write the failing test**

Add tests that assert:
- pure runtime mode bypasses `signal_pool`
- pure runtime mode bypasses `pretrade_risk_gate`
- pure runtime mode bypasses AI final review
- pure runtime mode bypasses capacity guard for new entries

**Step 2: Run test to verify it fails**

Run:
```powershell
pytest D:\AIDCA\AI8\tests\test_fund_flow_bot_regressions.py -q
```

**Step 3: Implement minimal bot bypass**

Gate the entry pipeline by `pure_strategy_runtime`.

**Step 4: Re-run targeted tests**

Run the same pytest command and confirm PASS.

### Task 3: Wire config and metadata for explicit pure runtime mode

**Files:**
- Modify: `D:\AIDCA\AI8\config\trading_config_fund_flow.json`
- Modify: `D:\AIDCA\AI8\tests\test_fund_flow_bot_regressions.py`
- Modify: `D:\AIDCA\AI8\tests\test_fund_flow_decision_engine.py`

**Step 1: Add config block**

Add:
```json
"pure_strategy_runtime": {
  "enabled": true,
  "bypass_signal_pool": true,
  "bypass_ma10_macd_filter": true,
  "bypass_pretrade_risk_gate": true,
  "bypass_ai_review": true,
  "bypass_capacity_guard": true,
  "bypass_entry_hard_gate": true,
  "bypass_runtime_pocket_gate": true
}
```

**Step 2: Add config guardrail tests**

Confirm main config explicitly enables the pure runtime block.

**Step 3: Validate JSON**

Run:
```powershell
@'
import json
json.load(open(r'D:\AIDCA\AI8\config\trading_config_fund_flow.json','r',encoding='utf-8'))
print('json_ok')
'@ | python -
```

### Task 4: Preserve execution realism while removing decision-layer drift

**Files:**
- Modify: `D:\AIDCA\AI8\src\app\fund_flow_bot.py`
- Modify: `D:\AIDCA\AI8\src\fund_flow\decision_engine.py`

**Step 1: Keep execution-only responsibilities**

Do not remove:
- same-bar TP/SL handling
- position/account state tracking
- protection order execution
- close execution
- account-level cooldown / hard circuit

**Step 2: Remove only live-only entry drift**

Bypass only:
- `signal_pool`
- `ma10_macd_confluence`
- `pretrade_risk_gate`
- AI review
- dynamic capacity gating for new opens
- runtime `entry_hard_gate`
- runtime pocket gate

**Step 3: Add metadata**

Add explicit flags like:
- `pure_strategy_runtime_enabled`
- `pure_strategy_runtime_bypasses`

### Task 5: Verification and handoff

**Files:**
- Modify: `D:\AIDCA\AI8\docs\reports\2026-04-02-strategy-realignment-todolist.md`

**Step 1: Run focused regression suite**

Run:
```powershell
pytest D:\AIDCA\AI8\tests\test_fund_flow_decision_engine.py D:\AIDCA\AI8\tests\test_fund_flow_bot_regressions.py D:\AIDCA\AI8\tests\test_bot_like_replay_selection.py -q
```

**Step 2: Document what was actually aligned and what still remains execution-only**

Update the strategy realignment todo/report.

**Step 3: Deliver with self-audit**

Call out:
- what drift is removed
- what realism is preserved
- which remaining differences still prevent “live == pure backtest” in a strict sense
