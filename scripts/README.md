# Scripts Layout

`scripts/` now keeps only primary replay/backtest entrypoints at the top level:

- `backtest_macd_v2.py`
- `backtest_fund_flow_bot_like.py`

Everything else is grouped by responsibility:

- `scripts/backtests/`
  Secondary backtest runners, comparison drivers, and experimental replay entrypoints.
- `scripts/diagnostics/`
  Analysis, audit, compare, diagnose, and validate scripts used to inspect behavior and regressions.
- `scripts/reports/`
  Report and artifact generators that transform existing outputs into summaries or review docs.
- `scripts/utils/`
  One-off maintenance and data utility helpers.

Rules:

- Do not add new temporary analysis scripts to top-level `scripts/`.
- If a script inspects or diagnoses behavior, put it under `scripts/diagnostics/`.
- If a script runs or orchestrates a backtest, put it under `scripts/backtests/` unless it is a primary entrypoint.
- If a script only generates a report or derived artifact, put it under `scripts/reports/`.
- If a script is a maintenance/data helper, put it under `scripts/utils/`.
