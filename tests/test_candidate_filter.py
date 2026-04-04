from src.fund_flow.candidate_filter import pre_ai_candidate_filter


CFG = {
    "candidate_filter_min_signal_scores": {
        "red_bar_growing|long_dual_support": 0.88,
        "_default": 0.87,
    },
    "candidate_filter_min_vwap_scores": {
        "red_bar_growing|long_dual_support": 0.16,
        "_default": 0.10,
    },
    "candidate_filter_max_4h_bear_score_for_long": 0.85,
    "candidate_filter_trial_min_4h_shrink_pct": 0.45,
    "candidate_filter_reject_combos": [
        {
            "signal_type": "red_bar_growing",
            "vwap_state": "long_reclaim_confirmed",
            "min_signal_score": 0.93,
            "min_vwap_score": 0.16,
            "reason_label": "RBG_RECLAIM_OVERHEAT",
        }
    ],
    "candidate_filter_cluster_gates": [
        {
            "signal_type": "red_bar_growing",
            "vwap_state": "long_reclaim_confirmed",
            "side": "long",
            "max_candidate_rank": 1,
            "reason_label": "RBG_RECLAIM_REPEAT",
        }
    ],
}


class FakeSignal:
    def __init__(self, **kwargs):
        self.symbol = kwargs.get("symbol", "RENDERUSDT")
        self.signal_type = kwargs.get("signal_type", "red_bar_growing")
        self.vwap_state = kwargs.get("vwap_state", "long_dual_support")
        self.signal_score = kwargs.get("signal_score", 0.89)
        self.vwap_score = kwargs.get("vwap_score", 0.17)
        self.side = kwargs.get("side", "long")
        self.is_trial_entry = kwargs.get("is_trial_entry", False)
        self.score_4h_direction_bear = kwargs.get("score_4h_direction_bear", 0.5)
        self.macd_4h_shrink_pct = kwargs.get("macd_4h_shrink_pct", 0.5)
        self.cluster_rank = kwargs.get("cluster_rank", 1)
        self.cluster_age_minutes = kwargs.get("cluster_age_minutes", 0.0)


def test_pocket_specific_score_threshold_blocks():
    signal = FakeSignal(signal_score=0.879)
    result = pre_ai_candidate_filter(signal, CFG)
    assert not result.passed and "PRE_AI_SCORE" in result.reason


def test_pocket_specific_vwap_threshold_blocks():
    signal = FakeSignal(vwap_score=0.155)
    result = pre_ai_candidate_filter(signal, CFG)
    assert not result.passed and "PRE_AI_VWAP" in result.reason


def test_4h_bear_score_blocks_long():
    signal = FakeSignal(score_4h_direction_bear=0.90)
    result = pre_ai_candidate_filter(signal, CFG)
    assert not result.passed and "PRE_AI_4H_BEAR" in result.reason


def test_trial_shrink_blocks():
    signal = FakeSignal(is_trial_entry=True, macd_4h_shrink_pct=0.30)
    result = pre_ai_candidate_filter(signal, CFG)
    assert not result.passed and "PRE_AI_TRIAL_SHRINK" in result.reason


def test_all_pass():
    signal = FakeSignal()
    result = pre_ai_candidate_filter(signal, CFG)
    assert result.passed and "PRE_AI_PASS" in result.reason


def test_non_long_dual_support_uses_default_threshold():
    signal = FakeSignal(
        signal_type="green_bar_growing",
        vwap_state="short_dual_pressure",
        signal_score=0.869,
        vwap_score=0.15,
    )
    result = pre_ai_candidate_filter(signal, CFG)
    assert not result.passed


def test_reject_combo_blocks_high_score_high_vwap_reclaim_candidate():
    signal = FakeSignal(
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
        signal_score=0.95,
        vwap_score=0.18,
    )
    result = pre_ai_candidate_filter(signal, CFG)
    assert not result.passed
    assert "PRE_AI_REJECT_COMBO:RBG_RECLAIM_OVERHEAT" in result.reason


def test_reject_combo_does_not_block_when_only_one_threshold_is_high():
    signal = FakeSignal(
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
        signal_score=0.95,
        vwap_score=0.15,
    )
    result = pre_ai_candidate_filter(signal, CFG)
    assert result.passed


def test_reject_combo_can_target_specific_symbol_only():
    cfg = dict(CFG)
    cfg["candidate_filter_reject_combos"] = [
        {
            "symbol": "ONDOUSDT",
            "signal_type": "red_bar_growing",
            "vwap_state": "long_reclaim_confirmed",
            "min_signal_score": 0.88,
            "min_vwap_score": 0.15,
            "reason_label": "ONDO_RBG_RECLAIM",
        }
    ]
    blocked_signal = FakeSignal(
        symbol="ONDOUSDT",
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
        signal_score=0.95,
        vwap_score=0.18,
    )
    passed_signal = FakeSignal(
        symbol="RENDERUSDT",
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
        signal_score=0.95,
        vwap_score=0.18,
    )

    blocked = pre_ai_candidate_filter(blocked_signal, cfg)
    passed = pre_ai_candidate_filter(passed_signal, cfg)

    assert not blocked.passed
    assert "ONDO_RBG_RECLAIM" in blocked.reason
    assert passed.passed


def test_cluster_gate_blocks_repeat_reclaim_candidate():
    signal = FakeSignal(
        signal_type="red_bar_growing",
        vwap_state="long_reclaim_confirmed",
        signal_score=0.90,
        vwap_score=0.17,
        cluster_rank=2,
        cluster_age_minutes=15.0,
    )
    result = pre_ai_candidate_filter(signal, CFG)
    assert not result.passed
    assert "PRE_AI_CLUSTER_REPEAT:RBG_RECLAIM_REPEAT" in result.reason
