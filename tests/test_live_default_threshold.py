import json
from pathlib import Path


def test_live_config_default_entry_threshold_is_085() -> None:
    cfg = json.loads(Path("config/trading_config_fund_flow.json").read_text(encoding="utf-8"))
    default_threshold = cfg["fund_flow"]["macd_mtf_strategy_v2"]["entry_thresholds"]["default"]
    assert default_threshold == 0.85
