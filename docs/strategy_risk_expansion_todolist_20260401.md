# MACD V2 Risk Expansion Todolist

Date: 2026-04-01

Objective:
- Re-run the current production strategy under higher concurrency, larger per-symbol allocation, and higher fixed leverage
- Use the current production config as the base
- Keep the strategy logic unchanged and isolate only risk-capacity expansion

Requested scenario family:
- `max_active_symbols = 4`
- per-position target portion in the `20% ~ 30%` range
- fixed leverage at `3x / 4x / 5x`

Checklist:

- [x] Read current production config
- [x] Confirm current production baseline is candidate C
- [x] Generate risk-pack candidate with:
  - `4 positions`
  - `20% target portion`
  - `3x leverage`
- [x] Generate risk-pack candidate with:
  - `4 positions`
  - `25% target portion`
  - `4x leverage`
- [x] Generate risk-pack candidate with:
  - `4 positions`
  - `30% target portion`
  - `5x leverage`
- [x] Re-run 30d backtest for all 3 candidates
- [x] Freeze result files under stable names

Stable candidate configs:

- [trading_config_fund_flow_risk_pack_20pct_3x.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_risk_pack_20pct_3x.json)
- [trading_config_fund_flow_risk_pack_25pct_4x.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_risk_pack_25pct_4x.json)
- [trading_config_fund_flow_risk_pack_30pct_5x.json](D:\AIDCA\AI8\config\candidates\trading_config_fund_flow_risk_pack_30pct_5x.json)

Stable result files:

- [v2_summary_20260401_risk_pack_20pct_3x.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_risk_pack_20pct_3x.json)
- [v2_summary_20260401_risk_pack_25pct_4x.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_risk_pack_25pct_4x.json)
- [v2_summary_20260401_risk_pack_30pct_5x.json](D:\AIDCA\AI8\output\backtest\v2_summary_20260401_risk_pack_30pct_5x.json)

Result snapshot:

- `20% / 3x / 4 positions`
  - `471 trades`
  - `86.0% WR`
  - `+36.04% return`
  - `PF 3.34`
  - `MDD 2.09%`

- `25% / 4x / 4 positions`
  - `590 trades`
  - `85.1% WR`
  - `+60.33% return`
  - `PF 2.82`
  - `MDD 2.39%`

- `30% / 5x / 4 positions`
  - `590 trades`
  - `85.1% WR`
  - `+97.55% return`
  - `PF 2.83`
  - `MDD 3.46%`

Key conclusions:

- The current production logic scales materially under higher risk capacity.
- The `30% / 5x` pack is the first configuration in this family to push the 30d return close to `+100%`.
- As risk is increased, the remaining weak pockets also scale up:
  - `short_dual_pressure`
  - `long_dual_support`
  - `long_above_session_below_structure`
- Trade count increases sharply once capacity rises from 2 positions to 4.

Current recommendation:

- If the goal is aggressive growth, `30% / 5x / 4 positions` is the strongest tested profile so far.
- If the goal is a stronger balance between growth and drawdown, `25% / 4x / 4 positions` is the more conservative expansion point.
