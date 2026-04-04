from scripts.diagnose_vwap_layers import _categorize_block, _summarize_rows


def test_categorize_block_recognizes_vwap_and_preflip_reasons():
    assert _categorize_block("macd_v2_hold_vwap_score_filter_score_0.00", {}) == "vwap_score_filter"
    assert _categorize_block("macd_v2_hold_vwap_hard_block_score_0.00", {}) == "vwap_hard_block"
    assert _categorize_block("macd_v2_hold_volume_vwap_both_low_score_0.61", {}) == "volume_vwap_both_low"
    assert _categorize_block("macd_v2_hold_none_score_0.00", {"macd_v2_debug": {"notes": ["4H预翻转缩短不足"]}}) == "preflip_shrink"


def test_summarize_rows_reports_percentiles_and_pockets():
    rows = [
        {"symbol": "A", "time": "t1", "vwap_score": 0.11, "signal_type_1h": "red_bar_growing", "vwap_state": "long_dual_support", "pocket_key": "red_bar_growing|long_dual_support"},
        {"symbol": "A", "time": "t2", "vwap_score": 0.15, "signal_type_1h": "red_bar_growing", "vwap_state": "long_dual_support", "pocket_key": "red_bar_growing|long_dual_support"},
        {"symbol": "B", "time": "t3", "vwap_score": 0.09, "signal_type_1h": "flip_bullish", "vwap_state": "flip_bullish", "pocket_key": "flip_bullish|flip_bullish"},
    ]

    summary = _summarize_rows(rows, top_n=2)

    assert summary["count"] == 3
    assert summary["vwap_score_p50"] == 0.11
    assert summary["vwap_score_max"] == 0.15
    assert summary["top_pockets"][0]["pocket_key"] == "red_bar_growing|long_dual_support"
