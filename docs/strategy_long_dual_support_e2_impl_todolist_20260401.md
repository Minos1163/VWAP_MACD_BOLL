# MACD V2 E2 Implementation Todolist

Date: 2026-04-01

- [x] Read E2 candidate config and baseline config
- [x] Implement `check_pocket_entry_override()` in [macd_strategy_v2.py](D:/AIDCA/AI8/src/fund_flow/macd_strategy_v2.py)
- [x] Implement `get_vwap_structure_position_scale()` in [decision_engine.py](D:/AIDCA/AI8/src/fund_flow/decision_engine.py)
- [x] Load `pocket_entry_overrides` from `fund_flow.macd_mtf_strategy_v2.entry_filters`
- [x] Load `vwap_structure_overrides` from top-level `fund_flow`
- [x] Insert pocket gate after MACD V2 L1/L2/L3 hard gates and before BUY/SELL creation
- [x] Insert `vwap_structure` position scaling into live target portion path
- [x] Create [test_pocket_entry_override.py](D:/AIDCA/AI8/tests/test_pocket_entry_override.py) with 12 required cases
- [x] Harden minimal `TradingBot.__new__()` regression path for alpha-dilution monitor
- [x] Promote E2 candidate to [trading_config_fund_flow.json](D:/AIDCA/AI8/config/trading_config_fund_flow.json)
- [x] Append `_e2_promotion_date`, `_e2_baseline`, `_e2_result` metadata
- [x] Run required regression suite and reach `0 failure / 0 error`

Verification:

- `pytest tests/test_pocket_entry_override.py tests/test_macd_strategy_v2_4h_scoring.py tests/test_fund_flow_decision_engine.py tests/test_fund_flow_bot_regressions.py -v --tb=short`
- Result: `90 passed`
- `python -m py_compile src/fund_flow/macd_strategy_v2.py src/fund_flow/decision_engine.py src/app/fund_flow_bot.py tests/test_pocket_entry_override.py`
- Result: passed
