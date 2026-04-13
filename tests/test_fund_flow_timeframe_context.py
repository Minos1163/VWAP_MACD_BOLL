from types import SimpleNamespace

from src.app.fund_flow_bot import TradingBot
from src.data.market_data import MarketDataManager


class _StubClient:
    def get_klines(self, _symbol: str, _interval: str, limit: int = 120):
        rows = []
        base = 100.0
        for i in range(limit):
            open_p = base + i * 0.6
            close_p = open_p + 0.45 + (0.05 if i % 3 == 0 else 0.0)
            high_p = close_p + 0.25
            low_p = open_p - 0.25
            ts = i * 60_000
            rows.append(
                [
                    ts,
                    f"{open_p:.4f}",
                    f"{high_p:.4f}",
                    f"{low_p:.4f}",
                    f"{close_p:.4f}",
                    "100",
                    ts + 59_999,
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                ]
            )
        return rows


class _StubBotMarketData:
    def __init__(self):
        self.trend_calls = []

    def get_realtime_market_data(self, _symbol: str):
        return {}

    def get_trend_filter_metrics(self, _symbol: str, interval: str = "15m", limit: int = 120):
        self.trend_calls.append((interval, limit))
        return {
            "ema_30": 100.0,
            "macd_cross": "NONE",
            "macd_zone": "NEAR_ZERO",
            "macd_hist_series": [0.1, 0.2, 0.3],
            "macd_hist_array": [0.1, 0.2, 0.3],
            "macd_hist_prev": 0.2,
            "close_series": [100.0, 100.5, 101.0],
        }

    def get_order_flow_snapshot(self, _symbol: str, interval: str = "1m", limit: int = 24):
        return {}


def test_get_trend_filter_metrics_includes_direction_feature_fields():
    manager = MarketDataManager(_StubClient())
    metrics = manager.get_trend_filter_metrics("BTCUSDT", interval="15m", limit=120)

    for key in (
        "macd_hist_norm",
        "macd_hist_delta",
        "macd_cross",
        "macd_cross_bias",
        "kdj_j_norm",
        "kdj_cross",
        "kdj_cross_bias",
        "kdj_zone",
        "bb_middle",
        "bb_upper",
        "bb_lower",
        "bb_width_norm",
        "bb_pos_norm",
        "bb_break",
        "bb_break_bias",
        "bb_trend",
        "bb_trend_bias",
        "bb_squeeze",
    ):
        assert key in metrics

    assert -1.0 <= float(metrics["macd_hist_norm"]) <= 1.0
    assert -1.0 <= float(metrics["kdj_j_norm"]) <= 1.0
    assert metrics["bb_break"] in {"NONE", "UPPER", "LOWER"}
    assert metrics["bb_trend"] in {"MID", "ALONG_UPPER", "ALONG_LOWER"}
    assert isinstance(metrics["macd_hist_series"], list)
    assert isinstance(metrics["macd_hist_array"], list)
    assert len(metrics["macd_hist_series"]) >= 10
    assert metrics["macd_hist_prev"] is not None


def test_apply_timeframe_context_injects_full_trend_filter_snapshot():
    bot = TradingBot.__new__(TradingBot)
    bot.config = {"fund_flow": {"decision_timeframe": "15m"}}

    raw_context = {
        "trend_filter": {
            "ema_fast": 101.0,
            "adx": 24.0,
            "macd_hist_norm": 0.38,
            "macd_cross": "GOLDEN",
            "kdj_j_norm": 0.82,
            "kdj_zone": "HIGH",
            "bb_break": "UPPER",
            "bb_trend": "ALONG_UPPER",
            "bb_squeeze": False,
        },
        "trend_filter_timeframe": "15m",
    }
    flow_snapshot = SimpleNamespace(timeframes={"15m": {"cvd_ratio": 0.12, "signal_strength": 0.33}})

    out = TradingBot._apply_timeframe_context(bot, raw_context, flow_snapshot)
    tf_ctx = out["timeframes"]["15m"]

    assert tf_ctx["macd_hist_norm"] == 0.38
    assert tf_ctx["macd_cross"] == "GOLDEN"
    assert tf_ctx["kdj_j_norm"] == 0.82
    assert tf_ctx["kdj_zone"] == "HIGH"
    assert tf_ctx["bb_break"] == "UPPER"
    assert tf_ctx["bb_trend"] == "ALONG_UPPER"
    assert tf_ctx["bb_squeeze"] is False
    assert out["cvd_ratio"] == 0.12
    assert out["active_timeframe"] == "15m"


def test_apply_timeframe_context_preserves_request_diagnostics_and_builds_context_diagnostics():
    bot = TradingBot.__new__(TradingBot)
    bot.config = {"fund_flow": {"decision_timeframe": "15m"}}
    bot._to_float = TradingBot._to_float

    raw_context = {
        "trend_filters_by_timeframe": {
            "15m": {
                "macd_hist_series": [0.1, 0.2, 0.3],
                "macd_hist_prev": 0.2,
                "close_series": [100.0, 100.5, 101.0],
            },
            "1h": {},
            "4h": {
                "close_series": [100.0, 100.5, 101.0],
            },
        },
        "timeframe_request_diagnostics": {
            "15m": {"requested": True, "snapshot_empty": False},
            "1h": {"requested": True, "snapshot_empty": True},
            "4h": {"requested": True, "snapshot_empty": False},
        },
    }
    flow_snapshot = SimpleNamespace(timeframes={})

    out = TradingBot._apply_timeframe_context(bot, raw_context, flow_snapshot)

    assert out["timeframe_request_diagnostics"]["1h"]["snapshot_empty"] is True
    context_diag = out["timeframe_context_diagnostics"]
    assert context_diag["15m"]["timeframe_present"] is True
    assert context_diag["15m"]["macd_hist_series_present"] is True
    assert context_diag["1h"]["timeframe_present"] is False
    assert context_diag["1h"]["snapshot_empty"] is True
    assert context_diag["4h"]["timeframe_present"] is True
    assert context_diag["4h"]["macd_hist_series_present"] is False
    assert context_diag["4h"]["close_series_present"] is True


def test_get_market_data_for_symbol_requests_4h_when_dual_risk_filter_enabled():
    bot = TradingBot.__new__(TradingBot)
    bot.config = {
        "fund_flow": {
            "decision_timeframe": "15m",
            "regime": {"timeframe": "15m"},
            "rule_strategy": {
                "primary_trend_timeframe": "1h",
                "entry_timeframe": "15m",
                "trend_limit": 120,
                "entry_limit": 120,
            },
        },
        "dual_timeframe": {
            "enabled": True,
            "risk_filter": {
                "enable_4h_macd": True,
            },
        },
    }
    bot.market_data = _StubBotMarketData()
    bot._startup_trend_filter_cache = {}
    bot._execution_quality_1m_config = lambda: {"timeframe": "1m", "trend_limit": 60, "orderflow_limit": 24}
    bot._extract_orderbook_flow = lambda _symbol: {}

    out = TradingBot.get_market_data_for_symbol(bot, "BTCUSDT")

    assert "4h" in out["trend_filters_by_timeframe"]
    request_diag = out["timeframe_request_diagnostics"]
    assert request_diag["1h"]["requested"] is True
    assert request_diag["1h"]["snapshot_empty"] is False
    assert request_diag["1h"]["macd_hist_series_present"] is True
    assert request_diag["4h"]["requested"] is True
    assert request_diag["4h"]["close_series_present"] is True
    requested_intervals = {interval for interval, _ in bot.market_data.trend_calls}
    assert {"1h", "15m", "4h"} <= requested_intervals
