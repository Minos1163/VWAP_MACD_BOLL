from src.fund_flow.signal_funnel_logger import SignalFunnelLogger


def test_funnel_counts_correctly():
    funnel = SignalFunnelLogger()
    funnel.log("1_score_threshold", True)
    funnel.log("1_score_threshold", True)
    funnel.log("1_score_threshold", False, reason="low_score", score=0.82)

    layer = funnel.layers["1_score_threshold"]
    assert layer.passed == 2
    assert layer.blocked == 1
    assert layer.block_reasons["low_score"] == 1
    assert 0.82 in layer.block_score_samples


def test_pass_rate():
    funnel = SignalFunnelLogger()
    for _ in range(3):
        funnel.log("2_vwap_threshold", True)
    funnel.log("2_vwap_threshold", False, reason="vwap_low", score=0.09)

    assert abs(funnel.layers["2_vwap_threshold"].pass_rate - 0.75) < 0.001


def test_report_generates_dict():
    funnel = SignalFunnelLogger()
    funnel.log("0_raw_signal", True)

    report = funnel.report()

    assert "0_raw_signal" in report
    assert "passed" in report["0_raw_signal"]
    assert "blocked" in report["0_raw_signal"]
    assert "pass_rate" in report["0_raw_signal"]
