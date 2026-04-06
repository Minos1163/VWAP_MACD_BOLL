from __future__ import annotations

import pandas as pd

from src.config.config_loader import ConfigLoader
from scripts.generate_backtest_config import build_backtest_copy
from scripts.backtest_macd_v2 import BacktestConfig, BacktestEngine, apply_backtest_profile
from src.fund_flow.macd_strategy_v2 import MACDSignalV2, MACDStrategyV2Config


def test_apply_backtest_profile_disables_short_filter_only_for_backtest() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["BTCUSDT", "ETHUSDT"]},
        "fund_flow": {
            "allowed_entry_hours_utc": [],
            "macd_mtf_strategy_v2": {
                "short_quality_filter": {
                    "enabled": True,
                    "min_funding_rate": 0.0005,
                }
            },
            "backtest": {
                "default_profile": "",
                "profiles": {
                    "macd_v2_disable_short_filter": {
                        "config_overrides": {
                            "fund_flow": {
                                "macd_mtf_strategy_v2": {
                                    "short_quality_filter": {"enabled": False}
                                }
                            }
                        }
                    }
                },
            },
        },
    }

    merged, active_profile = apply_backtest_profile(
        runtime_cfg,
        profile_name="macd_v2_disable_short_filter",
    )

    assert active_profile == "macd_v2_disable_short_filter"
    assert runtime_cfg["fund_flow"]["macd_mtf_strategy_v2"]["short_quality_filter"]["enabled"] is True
    assert merged["fund_flow"]["macd_mtf_strategy_v2"]["short_quality_filter"]["enabled"] is False
    assert merged["fund_flow"]["macd_mtf_strategy_v2"]["short_quality_filter"]["min_funding_rate"] == 0.0005


def test_apply_backtest_profile_maps_symbols_and_allowed_hours() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["BTCUSDT"]},
        "fund_flow": {
            "allowed_entry_hours_utc": [],
            "backtest": {
                "default_profile": "",
                "profiles": {
                    "focus": {
                        "symbols": ["solusdt", "atomusdt"],
                        "allowed_entry_hours": [0, 6, 12],
                    }
                },
            },
        },
    }

    merged, active_profile = apply_backtest_profile(runtime_cfg, profile_name="focus")

    assert active_profile == "focus"
    assert merged["trading"]["symbols"] == ["SOLUSDT", "ATOMUSDT"]
    assert merged["fund_flow"]["allowed_entry_hours_utc"] == [0, 6, 12]


def test_apply_backtest_profile_uses_default_profile_when_requested() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["BTCUSDT"]},
        "fund_flow": {
            "macd_mtf_strategy_v2": {
                "short_quality_filter": {"enabled": True}
            },
            "backtest": {
                "default_profile": "macd_v2_disable_short_filter",
                "profiles": {
                    "macd_v2_disable_short_filter": {
                        "config_overrides": {
                            "fund_flow": {
                                "macd_mtf_strategy_v2": {
                                    "short_quality_filter": {"enabled": False}
                                }
                            }
                        }
                    }
                },
            },
        },
    }

    merged, active_profile = apply_backtest_profile(runtime_cfg)

    assert active_profile == "macd_v2_disable_short_filter"
    assert merged["fund_flow"]["macd_mtf_strategy_v2"]["short_quality_filter"]["enabled"] is False


def test_trading_symbols_respect_symbol_blacklist() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["BTCUSDT", "QNTUSDT", "ethusdt", "BTCUSDT"]},
        "fund_flow": {"symbol_blacklist": ["qntusdt", "XMRUSDT"]},
    }

    assert ConfigLoader.get_symbol_blacklist(runtime_cfg) == ["QNTUSDT", "XMRUSDT"]
    assert ConfigLoader.get_trading_symbols(runtime_cfg) == ["BTCUSDT", "ETHUSDT"]


def test_trading_symbols_merge_trading_and_fund_flow_blacklists() -> None:
    runtime_cfg = {
        "trading": {
            "symbols": ["BTCUSDT", "QNTUSDT", "ETHUSDT", "KASUSDT"],
            "symbol_blacklist": ["kasusdt", "uniusdt"],
        },
        "fund_flow": {"symbol_blacklist": ["qntusdt", "XMRUSDT"]},
    }

    assert ConfigLoader.get_symbol_blacklist(runtime_cfg) == ["QNTUSDT", "XMRUSDT", "KASUSDT", "UNIUSDT"]
    assert ConfigLoader.get_trading_symbols(runtime_cfg) == ["BTCUSDT", "ETHUSDT"]


def test_apply_backtest_profile_filters_blacklisted_symbols() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["BTCUSDT"]},
        "fund_flow": {
            "symbol_blacklist": ["QNTUSDT"],
            "allowed_entry_hours_utc": [],
            "backtest": {
                "profiles": {
                    "focus": {
                        "symbols": ["QNTUSDT", "SOLUSDT"],
                    }
                }
            },
        },
    }

    merged, active_profile = apply_backtest_profile(runtime_cfg, profile_name="focus")

    assert active_profile == "focus"
    assert merged["trading"]["symbols"] == ["SOLUSDT"]


def test_build_backtest_copy_persists_and_applies_symbol_blacklist() -> None:
    base_cfg = {
        "trading": {"symbols": ["BTCUSDT", "QNTUSDT", "ETHUSDT"]},
        "fund_flow": {
            "backtest": {
                "profiles": {
                    "top50": {"symbols": ["BTCUSDT", "QNTUSDT", "SOLUSDT"]},
                }
            }
        },
    }

    copied = build_backtest_copy(
        base_cfg,
        source_path="config/trading_config_fund_flow.json",
        default_profile="top50",
        symbol_blacklist=["QNTUSDT", "UNIUSDT"],
    )

    assert copied["fund_flow"]["symbol_blacklist"] == ["QNTUSDT", "UNIUSDT"]
    assert copied["trading"]["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert copied["fund_flow"]["backtest"]["profiles"]["top50"]["symbols"] == ["BTCUSDT", "SOLUSDT"]


def test_apply_backtest_profile_enables_preflip_trial_and_shrink_exit() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "fund_flow": {
            "macd_mtf_strategy_v2": {
                "entry_filters": {
                    "primary_direction_timeframe": "1h",
                    "enable_4h_preflip_trial_entries": False,
                },
                "stop_loss_config": {
                    "enable_4h_shrink_exit": False,
                },
                "short_quality_filter": {"enabled": True},
            },
            "backtest": {
                "profiles": {
                    "macd_v2_4h_preflip_trial_exit": {
                        "config_overrides": {
                            "fund_flow": {
                                "macd_mtf_strategy_v2": {
                                    "entry_filters": {
                                        "primary_direction_timeframe": "4h",
                                        "enable_4h_preflip_trial_entries": True,
                                        "preflip_trial_entry_scale": 0.35,
                                    },
                                    "stop_loss_config": {
                                        "enable_4h_shrink_exit": True,
                                        "exit_4h_shrink_bars": 2,
                                    },
                                    "short_quality_filter": {"enabled": False},
                                }
                            }
                        }
                    }
                }
            },
        },
    }

    merged, active_profile = apply_backtest_profile(runtime_cfg, profile_name="macd_v2_4h_preflip_trial_exit")

    assert active_profile == "macd_v2_4h_preflip_trial_exit"
    assert merged["fund_flow"]["macd_mtf_strategy_v2"]["entry_filters"]["primary_direction_timeframe"] == "4h"
    assert merged["fund_flow"]["macd_mtf_strategy_v2"]["entry_filters"]["enable_4h_preflip_trial_entries"] is True
    assert merged["fund_flow"]["macd_mtf_strategy_v2"]["entry_filters"]["preflip_trial_entry_scale"] == 0.35
    assert merged["fund_flow"]["macd_mtf_strategy_v2"]["stop_loss_config"]["enable_4h_shrink_exit"] is True
    assert merged["fund_flow"]["macd_mtf_strategy_v2"]["stop_loss_config"]["exit_4h_shrink_bars"] == 2
    assert merged["fund_flow"]["macd_mtf_strategy_v2"]["short_quality_filter"]["enabled"] is False


def test_apply_backtest_profile_enables_targeted_loss_pocket_filters() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "fund_flow": {
            "macd_mtf_strategy_v2": {
                "entry_filters": {
                    "enable_green_bar_growing_short_adx_1h_range_filter": False,
                    "green_bar_growing_short_min_adx_1h": 0.0,
                    "green_bar_growing_short_max_adx_1h": 0.0,
                    "enable_flip_bullish_cvd_context_filter": False,
                    "flip_bullish_max_cvd_upper_wick_ratio": 0.0,
                    "flip_bullish_min_cvd_1h_delta_ratio": 0.0,
                }
            },
            "backtest": {
                "profiles": {
                    "macd_v2_4h_preflip_trial_exit_filter_a": {
                        "config_overrides": {
                            "fund_flow": {
                                "macd_mtf_strategy_v2": {
                                    "entry_filters": {
                                        "enable_green_bar_growing_short_adx_1h_range_filter": True,
                                        "green_bar_growing_short_min_adx_1h": 25.0,
                                        "green_bar_growing_short_max_adx_1h": 30.0,
                                        "enable_flip_bullish_cvd_context_filter": True,
                                        "flip_bullish_max_cvd_upper_wick_ratio": 0.20,
                                        "flip_bullish_min_cvd_1h_delta_ratio": 0.03,
                                    }
                                }
                            }
                        }
                    }
                }
            },
        },
    }

    merged, active_profile = apply_backtest_profile(runtime_cfg, profile_name="macd_v2_4h_preflip_trial_exit_filter_a")

    filters = merged["fund_flow"]["macd_mtf_strategy_v2"]["entry_filters"]
    assert active_profile == "macd_v2_4h_preflip_trial_exit_filter_a"
    assert filters["enable_green_bar_growing_short_adx_1h_range_filter"] is True
    assert filters["green_bar_growing_short_min_adx_1h"] == 25.0
    assert filters["green_bar_growing_short_max_adx_1h"] == 30.0
    assert filters["enable_flip_bullish_cvd_context_filter"] is True
    assert filters["flip_bullish_max_cvd_upper_wick_ratio"] == 0.20
    assert filters["flip_bullish_min_cvd_1h_delta_ratio"] == 0.03


def test_apply_backtest_profile_enables_v9_controls() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "risk": {
            "max_consecutive_losses": 2,
            "consecutive_loss_cooldown_seconds": 1800,
        },
        "fund_flow": {
            "macd_mtf_strategy_v2": {
                "stop_loss_config": {
                    "enable_4h_shrink_exit": True,
                    "exit_4h_require_profit": True,
                }
            },
            "backtest": {
                "profiles": {
                    "macd_v2_4h_preflip_trial_exit_filter_a_v9": {
                        "config_overrides": {
                            "risk": {
                                "max_consecutive_losses": 2,
                                "consecutive_loss_cooldown_seconds": 5400,
                            },
                            "fund_flow": {
                                "macd_mtf_strategy_v2": {
                                    "stop_loss_config": {
                                        "enable_4h_shrink_exit": True,
                                        "exit_4h_min_shrink_pct": 0.30,
                                        "exit_4h_require_profit": False,
                                        "exit_4h_weak_loss_threshold": -0.001,
                                    },
                                    "session_risk_control": {
                                        "enabled": True,
                                        "high_risk_sessions": [
                                            {"utc_start": "14:30", "utc_end": "16:00", "position_scale": 0.65}
                                        ],
                                        "apply_to_states": ["short_dual_pressure", "flip_bullish"],
                                    },
                                }
                            },
                        }
                    }
                }
            },
        },
    }

    merged, active_profile = apply_backtest_profile(runtime_cfg, profile_name="macd_v2_4h_preflip_trial_exit_filter_a_v9")

    assert active_profile == "macd_v2_4h_preflip_trial_exit_filter_a_v9"
    assert merged["risk"]["consecutive_loss_cooldown_seconds"] == 5400
    stop_cfg = merged["fund_flow"]["macd_mtf_strategy_v2"]["stop_loss_config"]
    assert stop_cfg["exit_4h_min_shrink_pct"] == 0.30
    assert stop_cfg["exit_4h_require_profit"] is False
    assert stop_cfg["exit_4h_weak_loss_threshold"] == -0.001
    session_cfg = merged["fund_flow"]["macd_mtf_strategy_v2"]["session_risk_control"]
    assert session_cfg["enabled"] is True
    assert session_cfg["high_risk_sessions"][0]["position_scale"] == 0.65
    assert session_cfg["apply_to_states"] == ["short_dual_pressure", "flip_bullish"]


def test_apply_backtest_profile_enables_v9_1_session_only_controls() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "risk": {
            "max_consecutive_losses": 2,
            "consecutive_loss_cooldown_seconds": 1800,
        },
        "fund_flow": {
            "macd_mtf_strategy_v2": {
                "stop_loss_config": {
                    "enable_4h_shrink_exit": True,
                    "exit_4h_min_shrink_pct": 0.20,
                    "exit_4h_require_profit": True,
                }
            },
            "backtest": {
                "profiles": {
                    "macd_v2_4h_preflip_trial_exit_filter_a_v9_1_session_only": {
                        "config_overrides": {
                            "risk": {
                                "max_consecutive_losses": 2,
                                "consecutive_loss_cooldown_seconds": 1800,
                            },
                            "fund_flow": {
                                "macd_mtf_strategy_v2": {
                                    "stop_loss_config": {
                                        "enable_4h_shrink_exit": True,
                                        "exit_4h_min_shrink_pct": 0.20,
                                        "exit_4h_require_profit": True,
                                    },
                                    "session_risk_control": {
                                        "enabled": True,
                                        "high_risk_sessions": [
                                            {"utc_start": "14:30", "utc_end": "16:00", "position_scale": 0.65}
                                        ],
                                        "apply_to_states": ["short_dual_pressure", "flip_bullish"],
                                    },
                                }
                            },
                        }
                    }
                }
            },
        },
    }

    merged, active_profile = apply_backtest_profile(
        runtime_cfg,
        profile_name="macd_v2_4h_preflip_trial_exit_filter_a_v9_1_session_only",
    )

    assert active_profile == "macd_v2_4h_preflip_trial_exit_filter_a_v9_1_session_only"
    assert merged["risk"]["consecutive_loss_cooldown_seconds"] == 1800
    stop_cfg = merged["fund_flow"]["macd_mtf_strategy_v2"]["stop_loss_config"]
    assert stop_cfg["exit_4h_min_shrink_pct"] == 0.20
    assert stop_cfg["exit_4h_require_profit"] is True
    assert "exit_4h_weak_loss_threshold" not in stop_cfg
    session_cfg = merged["fund_flow"]["macd_mtf_strategy_v2"]["session_risk_control"]
    assert session_cfg["enabled"] is True
    assert session_cfg["high_risk_sessions"][0]["position_scale"] == 0.65


def test_apply_backtest_profile_enables_v9_2_short_pressure_focus() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "fund_flow": {
            "backtest": {
                "profiles": {
                    "macd_v2_4h_preflip_trial_exit_filter_a_v9_2_short_pressure_focus": {
                        "config_overrides": {
                            "fund_flow": {
                                "macd_mtf_strategy_v2": {
                                    "session_risk_control": {
                                        "enabled": True,
                                        "high_risk_sessions": [
                                            {"utc_start": "03:00", "utc_end": "05:30", "position_scale": 0.70},
                                            {"utc_start": "14:30", "utc_end": "16:00", "position_scale": 0.65},
                                        ],
                                        "apply_to_states": ["short_dual_pressure"],
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
    }

    merged, active_profile = apply_backtest_profile(
        runtime_cfg,
        profile_name="macd_v2_4h_preflip_trial_exit_filter_a_v9_2_short_pressure_focus",
    )

    assert active_profile == "macd_v2_4h_preflip_trial_exit_filter_a_v9_2_short_pressure_focus"
    session_cfg = merged["fund_flow"]["macd_mtf_strategy_v2"]["session_risk_control"]
    assert session_cfg["enabled"] is True
    assert len(session_cfg["high_risk_sessions"]) == 2
    assert session_cfg["high_risk_sessions"][0]["position_scale"] == 0.70
    assert session_cfg["apply_to_states"] == ["short_dual_pressure"]


def test_apply_backtest_profile_enables_v11_watchlist_throttle_and_preflip_symmetry() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT", "LINKUSDT"]},
        "fund_flow": {
            "backtest": {
                "profiles": {
                    "macd_v2_v11_watchlist_throttle_preflip_symmetric": {
                        "config_overrides": {
                            "fund_flow": {
                                "macd_mtf_strategy_v2": {
                                    "entry_filters": {
                                        "preflip_trial_min_shrink_pct_long": 0.60,
                                        "preflip_trial_min_shrink_pct_short": 0.40,
                                    },
                                    "symbol_risk_tiers": {
                                        "watchlist_symbols": [
                                            "LINKUSDT",
                                            "ONDOUSDT",
                                            "LTCUSDT",
                                            "MORPHOUSDT",
                                            "TRUMPUSDT",
                                        ],
                                        "watchlist_max_position_portion": 0.40,
                                        "watchlist_max_leverage": 2,
                                        "watchlist_apply_session_scale_double": True,
                                        "watchlist_session_scale_multiplier": 0.80,
                                    },
                                }
                            }
                        }
                    }
                }
            }
        },
    }

    merged, active_profile = apply_backtest_profile(
        runtime_cfg,
        profile_name="macd_v2_v11_watchlist_throttle_preflip_symmetric",
    )

    assert active_profile == "macd_v2_v11_watchlist_throttle_preflip_symmetric"
    filter_cfg = merged["fund_flow"]["macd_mtf_strategy_v2"]["entry_filters"]
    assert filter_cfg["preflip_trial_min_shrink_pct_long"] == 0.60
    assert filter_cfg["preflip_trial_min_shrink_pct_short"] == 0.40
    symbol_risk_cfg = merged["fund_flow"]["macd_mtf_strategy_v2"]["symbol_risk_tiers"]
    assert symbol_risk_cfg["watchlist_symbols"][0] == "LINKUSDT"
    assert symbol_risk_cfg["watchlist_max_position_portion"] == 0.40
    assert symbol_risk_cfg["watchlist_max_leverage"] == 2
    assert symbol_risk_cfg["watchlist_apply_session_scale_double"] is True


def test_apply_backtest_profile_enables_v11_vwap_tiers_and_standalone_b1() -> None:
    runtime_cfg = {
        "trading": {"symbols": ["SOLUSDT"]},
        "fund_flow": {
            "backtest": {
                "profiles": {
                    "macd_v2_v11_vwap_score_tiers": {
                        "config_overrides": {
                            "fund_flow": {
                                "macd_mtf_strategy_v2": {
                                    "vwap_score_position_tiers": {
                                        "apply_to_states": ["short_dual_pressure", "flip_bullish"],
                                        "tiers": [
                                            {"min": 0.12, "max": 0.20, "position_mult": 0.80},
                                            {"min": 0.20, "max": 0.30, "position_mult": 1.00},
                                            {"min": 0.30, "max": 1.00, "position_mult": 1.15},
                                        ],
                                    }
                                }
                            }
                        }
                    },
                    "macd_v2_v11_preflip_symmetric_only": {
                        "config_overrides": {
                            "fund_flow": {
                                "macd_mtf_strategy_v2": {
                                    "entry_filters": {
                                        "preflip_trial_min_shrink_pct_long": 0.60,
                                        "preflip_trial_min_shrink_pct_short": 0.40,
                                    }
                                }
                            }
                        }
                    },
                }
            }
        },
    }

    merged_c1, active_c1 = apply_backtest_profile(
        runtime_cfg,
        profile_name="macd_v2_v11_vwap_score_tiers",
    )
    assert active_c1 == "macd_v2_v11_vwap_score_tiers"
    tier_cfg = merged_c1["fund_flow"]["macd_mtf_strategy_v2"]["vwap_score_position_tiers"]
    assert tier_cfg["apply_to_states"] == ["short_dual_pressure", "flip_bullish"]
    assert tier_cfg["tiers"][0]["position_mult"] == 0.80

    merged_b1, active_b1 = apply_backtest_profile(
        runtime_cfg,
        profile_name="macd_v2_v11_preflip_symmetric_only",
    )
    assert active_b1 == "macd_v2_v11_preflip_symmetric_only"
    filter_cfg = merged_b1["fund_flow"]["macd_mtf_strategy_v2"]["entry_filters"]
    assert filter_cfg["preflip_trial_min_shrink_pct_long"] == 0.60
    assert filter_cfg["preflip_trial_min_shrink_pct_short"] == 0.40


def test_backtest_shrink_exit_allows_weak_loss_threshold() -> None:
    config = BacktestConfig(symbols=["SOLUSDT"], initial_capital=10000.0)
    strategy_config = MACDStrategyV2Config(
        enable_4h_shrink_exit=True,
        exit_4h_require_profit=False,
        exit_4h_weak_loss_threshold=-0.001,
    )
    engine = BacktestEngine(config, strategy_config, runtime_config={})
    entry_time = pd.Timestamp("2026-03-21 14:45:00")
    engine.positions["SOLUSDT"] = {
        "side": "long",
        "entry_price": 100.0,
        "entry_notional": 2000.0,
        "position_value": 1000.0,
        "margin": 1000.0,
        "initial_margin": 1000.0,
        "remaining_fraction": 1.0,
        "leverage": 2,
        "stop_price": 95.0,
        "take_profit": None,
        "take_profit_levels": [],
        "entry_time": entry_time,
        "signal_score": 0.92,
        "signal_type_1h": "flip_bullish",
        "is_trial_entry": False,
        "entry_scale": 1.0,
        "session_position_scale": 0.65,
        "vwap_score": 0.20,
        "vwap_state": "short_dual_pressure",
        "vwap_location_score": 0.0,
        "ema_multiplier": 1.0,
        "ema_status": "normal",
        "realized_pnl_accum": 0.0,
    }
    analysis = {
        "signal": MACDSignalV2(
            direction="neutral",
            signal_score=0.0,
            details={"shrink_exit_ready": True, "shrink_exit_direction": "long"},
        ),
        "row_15m": pd.Series({"high": 100.0, "low": 99.9}),
        "price": 99.95,
        "time": pd.Timestamp("2026-03-21 15:00:00"),
    }

    closed = engine.check_stops("SOLUSDT", analysis)

    assert closed is True
    assert engine.trades[-1]["reason"] == "4h_shrink_exit"


def test_backtest_entry_cooldown_triggers_after_two_losses() -> None:
    config = BacktestConfig(
        symbols=["SOLUSDT"],
        initial_capital=10000.0,
        max_consecutive_losses=2,
        consecutive_loss_cooldown_seconds=5400,
    )
    engine = BacktestEngine(config, MACDStrategyV2Config(), runtime_config={})
    first_close = pd.Timestamp("2026-03-21 15:00:00")
    second_close = pd.Timestamp("2026-03-21 15:30:00")

    engine._update_loss_streak_after_trade_close(first_close, -10.0)
    assert engine._is_entry_cooldown_active(first_close) is False

    engine._update_loss_streak_after_trade_close(second_close, -5.0)
    assert engine._is_entry_cooldown_active(pd.Timestamp("2026-03-21 16:00:00")) is True
    assert engine._is_entry_cooldown_active(pd.Timestamp("2026-03-21 17:01:00")) is False
