from pathlib import Path

from scripts.analyze_live_vwap_block_followthrough import _iter_runtime_events


def test_iter_runtime_events_parses_vwap_block_runtime_log(tmp_path: Path):
    log_path = tmp_path / "runtime.out.00.log"
    log_path.write_text(
        "\n".join(
            [
                "=== FUND_FLOW cycle 1 @ 2026-03-26 00:00:03 UTC === [mode=MIXED_AI_REVIEW]",
                "[ICPUSDT] 决策=HOLD | 状态=noop | 目标占比=0.00 | 当前占比=0.00 | 杠杆(请求/实际)=2x/2x",
                "   K线价格(15m): open=2.4080 | close=2.4130 | change=+0.21%",
                "   MACD_V2评分: stage=vwap_score_filter, dir=neutral, primary=4H:0.0000(red_bar_growing), 1H=0.0000(flip_bullish), 4H_enh=0.0000(raw=0.80), VWAP=0.0997(dev=+0.38%), 15M=0.0000(red_bar_growing/midline_bounce, raw=0.98), VOL=0.0000(r=0.00), EMA=1.20x/strong, total=0.0000/0.8200, veto=vwap_score_filter",
                "   决策原因: macd_v2_hold_vwap_score_filter_score_0.00",
                "   HOLD归因: stage=vwap_score_filter, path=1h_direction > vwap_score_filter, reason=vwap_hard_block(0.10<0.10), code=vwap_hard_block, detail=0.10<0.10, signal_1h=flip_bullish, entry_15m=red_bar_growing, veto=vwap_score_filter, lock=LONG_ONLY",
            ]
        ),
        encoding="utf-8",
    )

    rows = list(_iter_runtime_events(tmp_path))

    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "ICPUSDT"
    assert row["category"] == "vwap_score_filter"
    assert row["direction"] == "long"
    assert row["price"] == 2.4130
    assert row["signal_type_1h"] == "flip_bullish"
    assert row["entry_15m"] == "red_bar_growing"
    assert row["vwap_score"] == 0.0997
