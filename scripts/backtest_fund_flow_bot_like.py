"""
Bot-like fund flow replay backtest.

目标：
1. 复用现有 BacktestEngine 的仓位/PnL/权益曲线统计
2. 用 FundFlowDecisionEngine + TradingBot 的排序/容量逻辑替代纯策略回测入口
3. 更贴近 fund_flow_bot 的开仓链路，验证 signal_pool / pretrade gate / max_active_symbols 等外层影响
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from backtest_macd_v2 import (  # type: ignore
        BacktestConfig,
        BacktestEngine,
        apply_backtest_profile,
        build_backtest_config,
        build_backtest_summary,
        build_strategy_config,
        load_symbol_data,
    )
except ModuleNotFoundError:
    from scripts.backtest_macd_v2 import (  # type: ignore
        BacktestConfig,
        BacktestEngine,
        apply_backtest_profile,
        build_backtest_config,
        build_backtest_summary,
        build_strategy_config,
        load_symbol_data,
    )
from src.app.fund_flow_bot import TradingBot
from src.fund_flow.replay_window import apply_market_data_window, timestamp_in_trade_window
from src.fund_flow.candidate_filter import pre_ai_candidate_filter
from src.fund_flow.decision_engine import FundFlowDecisionEngine
from src.fund_flow.dynamic_leverage import get_max_leverage_by_recent_performance
from src.fund_flow.models import FundFlowDecision, Operation
from src.fund_flow.replay_utils import (
    build_flow_context_with_proxy,
    build_microstructure_stats_from_records,
    iter_attribution_microstructure_records,
    _normalize_utc,
)
from src.fund_flow.signal_funnel_logger import SignalFunnelLogger


class BotLikeReplayEngine(BacktestEngine):
    """更贴近 fund_flow_bot 外层开仓链的回放引擎。"""

    MIN_15M_WARMUP_BARS = 50
    MIN_HIGHER_TF_INDEX = 5

    @staticmethod
    def pick_open_candidates(candidates: List[Dict[str, Any]], active_count: int) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        current_active = int(active_count)
        group_counts: Dict[str, int] = {}
        for item in sorted(
            candidates,
            key=lambda row: float(row.get("_priority_score", row.get("score", 0.0)) or 0.0),
            reverse=True,
        ):
            cap = max(1, int(float(item.get("max_active_symbols", 1) or 1)))
            if current_active >= cap:
                continue
            group_key = str(item.get("_capacity_group_key", "") or "")
            group_cap = max(0, int(float(item.get("_capacity_group_cap", 0) or 0)))
            if group_key and group_cap > 0 and group_counts.get(group_key, 0) >= group_cap:
                continue
            selected.append(item)
            current_active += 1
            if group_key and group_cap > 0:
                group_counts[group_key] = group_counts.get(group_key, 0) + 1
        return selected

    def _decision_timeframe(self) -> str:
        config = getattr(self, "config", None)
        tf = str(getattr(config, "decision_timeframe", "") or "15m").strip().lower()
        if tf in {"1m", "3m", "5m", "15m"}:
            return tf
        return "15m"

    def __init__(self, config: BacktestConfig, strategy_config, runtime_config: Dict[str, Any]):
        super().__init__(config, strategy_config, runtime_config)
        self.runtime_config = self._prepare_replay_runtime_config(runtime_config)
        self.decision_engine = FundFlowDecisionEngine(self.runtime_config)
        self.bot_logic = TradingBot.__new__(TradingBot)
        self.bot_logic.config = self.runtime_config
        gate_logs_dir = (Path("output") / "backtest" / "logs").resolve()
        gate_logs_dir.mkdir(parents=True, exist_ok=True)
        self.bot_logic.logs_dir = str(gate_logs_dir)
        self.bot_logic.log_root_dir = str(gate_logs_dir)
        self.bot_logic._position_first_seen_ts = {}
        self.bot_logic._position_last_direction_eval_ts = {}
        self.bot_logic._pre_risk_exit_streak_by_pos = {}
        self.bot_logic._position_extrema_by_pos = {}
        self.bot_logic._protection_missing_since_ts = {}
        self.bot_logic._protection_last_alert_ts = {}
        self.bot_logic._partial_tp_state_by_pos = {}
        self.bot_logic._risk_state_path = str((gate_logs_dir / "bot_like_risk_state.json").resolve())
        self.bot_logic._save_risk_state = lambda: None
        self._last_price_map: Dict[str, float] = {}
        self.ai_advice_logs: List[Dict[str, Any]] = []
        self.candidate_ledger_rows: List[Dict[str, Any]] = []
        self._candidate_ledger_index: Dict[str, int] = {}
        self._candidate_cluster_state: Dict[str, Dict[str, Any]] = {}

        ff_cfg = self.runtime_config.get("fund_flow", {}) if isinstance(self.runtime_config.get("fund_flow"), dict) else {}
        pure_runtime_cfg = ff_cfg.get("pure_strategy_runtime", {}) if isinstance(ff_cfg.get("pure_strategy_runtime"), dict) else {}
        self.pure_strategy_runtime_enabled = bool(pure_runtime_cfg.get("enabled", False))
        ai_cfg = ff_cfg.get("ai_review", {}) if isinstance(ff_cfg.get("ai_review"), dict) else {}
        self.ai_review_enabled = bool(ai_cfg.get("enabled", True)) and not self.pure_strategy_runtime_enabled
        self.ai_flat_top_n = max(1, int(self.bot_logic._to_float(ai_cfg.get("flat_top_n", 3), 3)))
        self.ai_review_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
        candidate_pre_filter_cfg = ff_cfg.get("candidate_pre_filter", {}) if isinstance(ff_cfg.get("candidate_pre_filter"), dict) else {}
        self.candidate_pre_filter_cfg = candidate_pre_filter_cfg
        self.candidate_pre_filter_enabled = bool(candidate_pre_filter_cfg.get("enabled", False)) and not self.pure_strategy_runtime_enabled
        dynamic_leverage_cfg = ff_cfg.get("dynamic_leverage", {}) if isinstance(ff_cfg.get("dynamic_leverage"), dict) else {}
        self.dynamic_leverage_cfg = dynamic_leverage_cfg
        self.dynamic_leverage_enabled = bool(dynamic_leverage_cfg.get("enabled", False)) and not self.pure_strategy_runtime_enabled
        self.bot_logic._tighten_protection_for_conflict = self._backtest_tighten_protection_for_conflict
        replay_micro_cfg = ff_cfg.get("replay_microstructure", {}) if isinstance(ff_cfg.get("replay_microstructure"), dict) else {}
        self.replay_microstructure_enabled = bool(replay_micro_cfg.get("enabled", True))
        self.replay_microstructure_mode = str(replay_micro_cfg.get("mode", "historical_proxy") or "historical_proxy")
        self.replay_microstructure_stats = self._load_replay_microstructure_stats(config)
        self.replay_microstructure_stats_summary = self._summarize_replay_microstructure_stats(self.replay_microstructure_stats)
        self.signal_funnel = SignalFunnelLogger()
        self._last_open_reject_reason = ""
        backtest_cfg = ff_cfg.get("backtest", {}) if isinstance(ff_cfg.get("backtest"), dict) else {}
        self.entry_bar_same_bar_enabled = bool(backtest_cfg.get("entry_bar_same_bar_enabled", False))
        self.entry_bar_same_bar_priority_mode = str(backtest_cfg.get("same_bar_tp_priority_mode", "stop_first") or "stop_first").strip().lower()

    def _load_replay_microstructure_stats(self, config: BacktestConfig) -> Dict[str, Any]:
        if not self.replay_microstructure_enabled or self.replay_microstructure_mode != "historical_proxy":
            return {}
        start_dt = _normalize_utc(pd.Timestamp(config.window_start_iso).to_pydatetime()) if config.window_start_iso else None
        end_dt = _normalize_utc(pd.Timestamp(config.window_end_iso).to_pydatetime()) if config.window_end_iso else None
        records = list(
            iter_attribution_microstructure_records(
                Path("logs"),
                start_time=start_dt,
                end_time=end_dt,
            )
        )
        return build_microstructure_stats_from_records(records)

    @staticmethod
    def _summarize_replay_microstructure_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(stats, dict) or not stats:
            return {"symbol_count": 0, "sessions": {}, "has_default": False}
        sessions: Counter[str] = Counter()
        symbol_count = 0
        for symbol, session_map in stats.items():
            if symbol == "_default" or not isinstance(session_map, dict):
                continue
            symbol_count += 1
            for session_name in session_map.keys():
                sessions[str(session_name)] += 1
        return {
            "symbol_count": symbol_count,
            "sessions": dict(sessions),
            "has_default": "_default" in stats,
        }

    @staticmethod
    def _prepare_replay_runtime_config(runtime_config: Dict[str, Any]) -> Dict[str, Any]:
        prepared = copy.deepcopy(runtime_config or {})
        ff_cfg = prepared.setdefault("fund_flow", {})
        if not isinstance(ff_cfg, dict):
            ff_cfg = {}
            prepared["fund_flow"] = ff_cfg

        replay_micro_cfg = ff_cfg.get("replay_microstructure", {})
        if not isinstance(replay_micro_cfg, dict):
            replay_micro_cfg = {}
        replay_enabled = bool(replay_micro_cfg.get("enabled", True))
        replay_mode = str(replay_micro_cfg.get("mode", "historical_proxy") or "historical_proxy")
        ff_cfg["replay_microstructure"] = {
            "enabled": replay_enabled,
            "mode": replay_mode,
        }
        if replay_enabled and replay_mode == "degraded_unavailable":
            ff_cfg["entry_hard_gate_skip_spread_if_missing"] = True
        return prepared

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_optional_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            result = float(value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(result):
            return None
        return result

    @staticmethod
    def _current_positions_payload(positions: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        payload: Dict[str, Dict[str, Any]] = {}
        for symbol, pos in positions.items():
            side = str(pos.get("side", "")).lower()
            payload[symbol] = {
                "side": "LONG" if side == "long" else "SHORT",
                "amount": float(pos.get("entry_notional", 0.0)),
                "entry_price": float(pos.get("entry_price", 0.0)),
                "signal_type_1h": str(pos.get("signal_type_1h", "") or ""),
                "vwap_state": str(pos.get("vwap_state", "") or ""),
                "entry_tier": str(pos.get("entry_tier", "") or ""),
                "signal_score": float(pos.get("signal_score", 0.0) or 0.0),
                "pocket_management_override": dict(pos.get("pocket_management_override", {}))
                if isinstance(pos.get("pocket_management_override"), dict)
                else {},
            }
        return payload

    def _current_active_positions_snapshot(self, analyses: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        snapshot: Dict[str, Dict[str, Any]] = {}
        for symbol, pos in self.positions.items():
            if not isinstance(pos, dict):
                continue
            current_price = self._safe_float(
                analyses.get(symbol, {}).get("price"),
                self._safe_float(self._last_price_map.get(symbol), self._safe_float(pos.get("entry_price"), 0.0)),
            )
            entry_price = self._safe_float(pos.get("entry_price"), 0.0)
            if current_price > 0 and entry_price > 0:
                if str(pos.get("side", "")).lower() == "long":
                    pnl_ratio = (current_price - entry_price) / entry_price
                else:
                    pnl_ratio = (entry_price - current_price) / entry_price
            else:
                pnl_ratio = 0.0
            snapshot[str(symbol).upper()] = {
                "entry_tier": str(pos.get("entry_tier", "") or ""),
                "signal_score": self._safe_float(pos.get("signal_score"), 0.0),
                "unrealized_pnl_ratio": float(pnl_ratio),
            }
        return snapshot

    @staticmethod
    def _upper_wick_ratio_from_row(row: pd.Series) -> float:
        high = float(row.get("high", 0.0))
        open_price = float(row.get("open", 0.0))
        close = float(row.get("close", 0.0))
        low = float(row.get("low", 0.0))
        price_range = max(high - low, 1e-9)
        return max(0.0, min(1.0, (high - max(open_price, close)) / price_range))

    def _timeframe_context(self, df: pd.DataFrame, idx: int) -> Dict[str, Any]:
        row = df.iloc[idx]
        prev_row = df.iloc[idx - 1] if idx > 0 else row
        close_price = self._safe_float(row.get("close"), 0.0)
        prev_close = self._safe_float(prev_row.get("close"), close_price)
        atr = self._safe_float(row.get("atr"), 0.0)
        ctx = dict(row.to_dict())
        ctx.update(
            {
                "timestamp": row.get("timestamp"),
                "last_open": self._safe_float(row.get("open"), 0.0),
                "last_close": close_price,
                "ema_fast": self._safe_float(row.get("ema21"), 0.0),
                "ema_slow": self._safe_float(row.get("ema55"), 0.0),
                "atr_pct": (atr / close_price) if close_price > 0 else 0.0,
                "ret_period": ((close_price / prev_close) - 1.0) if prev_close > 0 else 0.0,
                "cvd_ratio": self._safe_optional_float(row.get("cvd_delta_ratio")),
                "cvd_momentum": self._safe_optional_float(row.get("cvd_pressure")),
                "upper_wick_ratio": self._upper_wick_ratio_from_row(row),
                "macd_hist_prev": self._safe_float(prev_row.get("macd_hist"), self._safe_float(row.get("macd_hist"), 0.0)),
                "close_series": df.iloc[: idx + 1]["close"].tolist(),
                "close_array": df.iloc[: idx + 1]["close"].tolist(),
                "vwap_series": df.iloc[: idx + 1]["vwap"].tolist() if "vwap" in df.columns else None,
                "vwap_array": df.iloc[: idx + 1]["vwap"].tolist() if "vwap" in df.columns else None,
                "structural_vwap_series": (
                    df.iloc[: idx + 1]["structural_vwap"].tolist() if "structural_vwap" in df.columns else None
                ),
                "macd_hist_series": df.iloc[: idx + 1]["macd_hist"].dropna().tolist(),
                "macd_hist_array": df.iloc[: idx + 1]["macd_hist"].dropna().tolist(),
            }
        )
        return ctx

    def _build_flow_context(self, symbol: str, data: Dict[str, pd.DataFrame], idx_active: int) -> Optional[Dict[str, Any]]:
        active_tf = self._decision_timeframe()
        if active_tf not in data:
            active_tf = "15m"
        tf_active = data[active_tf]
        current_time = tf_active.iloc[idx_active]["timestamp"]
        idx_15m = self._find_tf_index(data, "15m", current_time)
        idx_1h = self._find_tf_index(data, "1h", current_time)
        idx_4h = self._find_tf_index(data, "4h", current_time)
        if idx_15m < 0 or idx_1h < self.MIN_HIGHER_TF_INDEX or idx_4h < self.MIN_HIGHER_TF_INDEX:
            return None
        tf_active_ctx = self._timeframe_context(tf_active, idx_active)
        tf15 = self._timeframe_context(data["15m"], idx_15m)
        tf1h = self._timeframe_context(data["1h"], idx_1h)
        tf4h = self._timeframe_context(data["4h"], idx_4h)

        flow_context: Dict[str, Any] = {
            "timeframes": {
                "15m": tf15,
                "1h": tf1h,
                "4h": tf4h,
            },
            "active_timeframe": active_tf,
        }
        if active_tf != "15m":
            flow_context["timeframes"][active_tf] = tf_active_ctx

        for key in (
            "cvd_ratio",
            "cvd_momentum",
            "oi_delta_ratio",
            "funding_rate",
            "depth_ratio",
            "imbalance",
            "spread_bps",
            "liquidity_delta_norm",
            "signal_strength",
        ):
            flow_context[key] = self._safe_optional_float(tf_active_ctx.get(key, tf15.get(key)))

        missing_fields = [
            field_name
            for field_name in ("depth_ratio", "imbalance", "spread_bps")
            if flow_context.get(field_name) is None
        ]
        proxy_filled_fields: List[str] = []
        if self.replay_microstructure_enabled and self.replay_microstructure_mode == "historical_proxy" and missing_fields:
            flow_context = build_flow_context_with_proxy(
                symbol=symbol,
                bar_time=pd.Timestamp(current_time).to_pydatetime(),
                raw_flow_context=flow_context,
                historical_stats=self.replay_microstructure_stats,
            )
            proxy_filled_fields = [
                field_name
                for field_name in ("depth_ratio", "imbalance", "spread_bps")
                if flow_context.get(f"{field_name}_is_proxy")
            ]
            missing_fields = [
                field_name
                for field_name in ("depth_ratio", "imbalance", "spread_bps")
                if flow_context.get(field_name) is None
            ]
        flow_context["replay_microstructure"] = {
            "enabled": bool(getattr(self, "replay_microstructure_enabled", True)),
            "mode": str(getattr(self, "replay_microstructure_mode", "degraded_unavailable")),
            "spread_gate_mode": "skip_if_missing" if flow_context.get("spread_bps") is None else "strict",
            "missing_fields": missing_fields,
            "proxy_filled_fields": proxy_filled_fields,
        }

        return flow_context

    def _build_analysis(self, symbol: str, data: Dict[str, pd.DataFrame], idx_active: int) -> Optional[Dict[str, Any]]:
        analysis, _ = self._build_analysis_with_status(symbol, data, idx_active)
        return analysis

    def _build_analysis_with_status(
        self,
        symbol: str,
        data: Dict[str, pd.DataFrame],
        idx_active: int,
    ) -> tuple[Optional[Dict[str, Any]], str]:
        active_tf = self._decision_timeframe()
        if active_tf not in data:
            active_tf = "15m"
        if idx_active < self.MIN_15M_WARMUP_BARS:
            return None, f"warmup_{active_tf}"
        flow_context = self._build_flow_context(symbol, data, idx_active)
        if flow_context is None:
            return None, "missing_flow_context"
        row_active = data[active_tf].iloc[idx_active]
        return (
            {
                "symbol": symbol,
                "time": row_active["timestamp"],
                "price": self._safe_float(row_active.get("close"), 0.0),
                "row_15m": row_active,
                "flow_context": flow_context,
            },
            "ready",
        )

    def _build_portfolio(self) -> Dict[str, Any]:
        return {
            "cash": float(self.capital),
            "positions": self._current_positions_payload(self.positions),
            "total_assets": float(self._mark_to_market_equity(self._last_price_map)),
        }

    def _build_account_summary(self) -> Dict[str, Any]:
        max_leverage = float(
            self.config.fixed_leverage
            or getattr(self.config, "max_leverage", None)
            or self.config.default_leverage
            or 1
        )
        return {
            "equity": float(self._mark_to_market_equity(self._last_price_map)),
            "available_balance": float(self.capital),
            "max_leverage": max(1.0, max_leverage),
        }

    def _clear_position_tracking(self, symbol: str) -> None:
        prefix = f"{str(symbol).upper()}:"
        for store_name in (
            "_position_first_seen_ts",
            "_position_extrema_by_pos",
            "_pre_risk_exit_streak_by_pos",
            "_partial_tp_state_by_pos",
        ):
            store = getattr(self.bot_logic, store_name, None)
            if not isinstance(store, dict):
                continue
            for key in [k for k in list(store.keys()) if str(k).startswith(prefix)]:
                store.pop(key, None)

    def _sync_position_tracking(self, symbol: str, analysis: Dict[str, Any]) -> None:
        position = self.positions.get(symbol)
        if not isinstance(position, dict):
            return
        side = str(position.get("side", "")).upper()
        if side not in ("LONG", "SHORT"):
            return
        pos_key = self.bot_logic._position_track_key(symbol, side)
        first_seen = getattr(self.bot_logic, "_position_first_seen_ts", None)
        if isinstance(first_seen, dict) and pos_key not in first_seen:
            first_seen[pos_key] = float(pd.Timestamp(analysis["time"]).timestamp())
        self.bot_logic._update_position_extrema(symbol, position, float(analysis["price"]))
        if bool(position.get("trailing_activated")):
            extrema = self.bot_logic._position_extrema_by_pos.get(pos_key, {})
            if isinstance(extrema, dict):
                position["trailing_peak_ratio"] = max(
                    float(position.get("trailing_peak_ratio", 0.0) or 0.0),
                    float(extrema.get("max_favorable_ratio", 0.0) or 0.0),
                )

    def _set_backtest_now_ts(self, timestamp: Any) -> None:
        self.bot_logic._backtest_now_ts = float(pd.Timestamp(timestamp).timestamp())

    def _backtest_tighten_protection_for_conflict(
        self,
        *,
        symbol: str,
        position: Dict[str, Any],
        current_price: float,
        force_break_even: bool = False,
        tighten_ratio: float = 0.5,
        atr_pct: Optional[float] = None,
        min_atr_multiple: float = 1.8,
        cooldown_sec: float = 60.0,
        breakeven_mode: str = "",
        breakeven_fee_buffer: Optional[float] = None,
        sl_distance_ratio_override: Optional[float] = None,
    ) -> Dict[str, Any]:
        del tighten_ratio, atr_pct, min_atr_multiple, cooldown_sec, breakeven_mode
        pos = self.positions.get(symbol)
        if not isinstance(pos, dict):
            return {"status": "skipped", "message": "missing_position"}

        side = str(position.get("side", pos.get("side", ""))).upper()
        entry_price = self._safe_float(position.get("entry_price", pos.get("entry_price")), 0.0)
        old_sl = self._safe_float(pos.get("stop_price"), 0.0)
        if current_price <= 0 or entry_price <= 0 or side not in ("LONG", "SHORT"):
            return {"status": "skipped", "message": "invalid_inputs"}

        if force_break_even:
            fee_buffer = self._safe_float(breakeven_fee_buffer, 0.0)
            proposed = entry_price * (1.0 + fee_buffer) if side == "LONG" else entry_price * (1.0 - fee_buffer)
            stop_reason = "breakeven_stop"
        else:
            trail_distance = self._safe_float(sl_distance_ratio_override, 0.0)
            if trail_distance <= 0:
                trail_distance = 0.01
            proposed = current_price * (1.0 - trail_distance) if side == "LONG" else current_price * (1.0 + trail_distance)
            stop_reason = "trailing_stop"

        if side == "LONG":
            new_sl = max(old_sl, proposed)
            tighter = new_sl > old_sl + 1e-12
        else:
            baseline = old_sl if old_sl > 0 else proposed
            new_sl = min(baseline, proposed)
            tighter = (old_sl <= 0) or (new_sl < old_sl - 1e-12)
        if not tighter:
            return {"status": "skipped", "message": "not_tighter", "old_sl": old_sl, "new_sl": old_sl}

        pos["stop_price"] = float(new_sl)
        pos["stop_reason"] = stop_reason
        if stop_reason == "trailing_stop":
            if not bool(pos.get("trailing_activated", False)):
                pos["trailing_activated"] = True
                if side == "LONG":
                    activation_ratio = (current_price - entry_price) / entry_price
                else:
                    activation_ratio = (entry_price - current_price) / entry_price
                pos["trailing_activation_ratio"] = float(max(0.0, activation_ratio))
                pos["trailing_peak_ratio"] = float(max(0.0, activation_ratio))
            else:
                pos["trailing_peak_ratio"] = max(
                    float(pos.get("trailing_peak_ratio", 0.0) or 0.0),
                    float(pos.get("trailing_activation_ratio", 0.0) or 0.0),
                )
        return {"status": "success", "old_sl": old_sl, "new_sl": new_sl}

    def _decide(self, symbol: str, analysis: Dict[str, Any]) -> FundFlowDecision:
        decision = self.decision_engine.decide(
            symbol=symbol,
            portfolio=self._build_portfolio(),
            price=float(analysis["price"]),
            market_flow_context=analysis["flow_context"],
            trigger_context={
                "trigger_type": "scheduled",
                "signal_pool_id": None,
                "allow_entry_window": True,
            },
            use_weight_router=False,
            use_ai_weights=False,
        )
        metadata = decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}
        replay_micro = (
            analysis.get("flow_context", {}).get("replay_microstructure")
            if isinstance(analysis.get("flow_context"), dict)
            else None
        )
        if isinstance(replay_micro, dict):
            metadata = dict(metadata)
            metadata["replay_microstructure_mode"] = str(replay_micro.get("mode", "disabled"))
            metadata["replay_spread_gate_mode"] = str(replay_micro.get("spread_gate_mode", "strict"))
            metadata["replay_microstructure_missing_fields"] = list(replay_micro.get("missing_fields", []))
            metadata["replay_microstructure_proxy_filled_fields"] = list(replay_micro.get("proxy_filled_fields", []))
            metadata["replay_microstructure_unavailable"] = bool(metadata["replay_microstructure_missing_fields"])
            decision.metadata = metadata
        return decision

    def _ensure_signal_funnel(self) -> SignalFunnelLogger:
        funnel = getattr(self, "signal_funnel", None)
        if isinstance(funnel, SignalFunnelLogger):
            return funnel
        funnel = SignalFunnelLogger()
        self.signal_funnel = funnel
        return funnel

    @staticmethod
    def _decision_metadata(decision: FundFlowDecision) -> Dict[str, Any]:
        return decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}

    def _decision_signal_score(self, decision: FundFlowDecision) -> float:
        return self._safe_float(self._decision_metadata(decision).get("signal_score"), 0.0)

    def _decision_vwap_score(self, decision: FundFlowDecision) -> float:
        return self._safe_float(self._decision_metadata(decision).get("vwap_score"), 0.0)

    def _decision_text_blob(self, decision: FundFlowDecision) -> str:
        metadata = self._decision_metadata(decision)
        debug_payload = metadata.get("macd_v2_debug") if isinstance(metadata.get("macd_v2_debug"), dict) else {}
        parts = [str(decision.reason or "")]
        for key in ("hold_attribution", "hold_reason", "reject_reason", "reason"):
            value = debug_payload.get(key)
            if value:
                parts.append(str(value))
        notes = debug_payload.get("notes")
        if isinstance(notes, list):
            parts.extend(str(item) for item in notes if item)
        return " | ".join(parts).lower()

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    def _log_decision_funnel(self, decision: FundFlowDecision) -> None:
        funnel = self._ensure_signal_funnel()
        metadata = self._decision_metadata(decision)
        text_blob = self._decision_text_blob(decision)
        signal_score = self._decision_signal_score(decision)
        vwap_score = self._decision_vwap_score(decision)

        funnel.log("0_raw_signal", True, score=signal_score)

        score_blocked = self._contains_any(
            text_blob,
            ["signal_score_threshold", "评分低于阈值", "macd_v2_hold_none_score_"],
        )
        funnel.log(
            "1_score_threshold",
            passed=not score_blocked,
            reason=str(decision.reason or ""),
            score=signal_score,
        )

        vwap_blocked = self._contains_any(
            text_blob,
            ["vwap_score_filter", "vwap_hard_block", "volume_vwap_both_low"],
        )
        funnel.log(
            "2_vwap_threshold",
            passed=not vwap_blocked,
            reason=str(decision.reason or ""),
            score=vwap_score,
        )

        shrink_score = self._safe_float(
            metadata.get("macd_4h_shrink_pct"),
            self._safe_float((metadata.get("macd_v2_debug") or {}).get("macd_4h_shrink_pct"), 0.0),
        )
        preflip_blocked = self._contains_any(text_blob, ["4h预翻转缩短不足", "preflip_shrink", "shrink不足"])
        funnel.log(
            "3_4h_preflip_shrink",
            passed=not preflip_blocked,
            reason=str(decision.reason or ""),
            score=shrink_score,
        )

        pocket_gate = metadata.get("pocket_entry_gate") if isinstance(metadata.get("pocket_entry_gate"), dict) else {}
        if pocket_gate:
            funnel.log(
                "4_pocket_entry_override",
                passed=bool(pocket_gate.get("passed")),
                reason=str(pocket_gate.get("reason") or decision.reason or ""),
                score=signal_score,
            )

        gate_reason = str(metadata.get("entry_hard_gate_reason") or "")
        gate_evaluated = bool(gate_reason) or bool(pocket_gate) or decision.operation in (Operation.BUY, Operation.SELL)
        if gate_evaluated:
            funnel.log(
                "6_L1_structural",
                passed=not gate_reason.startswith("L1_"),
                reason=gate_reason or "pass",
                score=signal_score,
            )
            funnel.log(
                "7_L2_flow",
                passed=not gate_reason.startswith("L2_"),
                reason=gate_reason or "pass",
                score=vwap_score,
            )
            funnel.log(
                "8_L3_micro",
                passed=not gate_reason.startswith("L3_"),
                reason=gate_reason or "pass",
                score=vwap_score,
            )

    def _log_pretrade_funnel(self, before: FundFlowDecision, after: FundFlowDecision) -> None:
        if before.operation not in (Operation.BUY, Operation.SELL, Operation.CLOSE):
            return
        blocked = after.operation != before.operation
        self._ensure_signal_funnel().log(
            "9_pretrade_gate",
            passed=not blocked,
            reason=str(after.reason or before.reason or ""),
            score=self._decision_signal_score(before),
        )

    def _log_ai_review_funnel(
        self,
        *,
        shortlisted: List[Dict[str, Any]],
        approved: List[Dict[str, Any]],
        skipped: List[Dict[str, Any]],
    ) -> None:
        funnel = self._ensure_signal_funnel()
        approved_keys = {(
            str(item.get("symbol") or ""),
            str(getattr(item.get("decision"), "operation", "")),
            str(item.get("analysis", {}).get("time") or ""),
        ) for item in approved}
        for item in shortlisted:
            key = (
                str(item.get("symbol") or ""),
                str(getattr(item.get("decision"), "operation", "")),
                str(item.get("analysis", {}).get("time") or ""),
            )
            passed = key in approved_keys
            decision = item.get("decision")
            score = float(item.get("score", 0.0) or 0.0)
            reason = "ai_review_pass" if passed else str(getattr(decision, "reason", "") or "ai_review_block")
            funnel.log("10_ai_review", passed=passed, reason=reason, score=score)
        for item in skipped:
            funnel.log(
                "10_ai_review",
                passed=False,
                reason="ai_shortlist_topn",
                score=float(item.get("score", 0.0) or 0.0),
            )

    def _append_ai_advice_log(
        self,
        *,
        item: Dict[str, Any],
        rank: int,
        review_mode: str,
        allowed: bool,
        block_reason: str,
        ai_operation: str,
        ds_source: str,
        ds_confidence: float,
    ) -> None:
        analysis = item.get("analysis", {}) if isinstance(item.get("analysis"), dict) else {}
        decision = item.get("decision")
        metadata = self._decision_metadata(decision)
        candidate_pre_filter = item.get("candidate_pre_filter", {}) if isinstance(item.get("candidate_pre_filter"), dict) else {}
        self.ai_advice_logs.append(
            {
                "timestamp": pd.Timestamp(analysis.get("time")).strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": str(item.get("symbol") or ""),
                "rank": int(rank),
                "score": float(item.get("score", 0.0) or 0.0),
                "review_mode": review_mode,
                "local_operation": str(getattr(decision, "operation", "") or ""),
                "ai_operation": ai_operation,
                "allowed": bool(allowed),
                "block_reason": str(block_reason or ""),
                "ds_source": str(ds_source or "-"),
                "ds_confidence": float(ds_confidence or 0.0),
                "signal_type_1h": str(item.get("signal_type_1h") or metadata.get("signal_type_1h", "") or ""),
                "signal_score": self._safe_float(metadata.get("signal_score"), 0.0),
                "vwap_score": self._safe_float(metadata.get("vwap_score"), 0.0),
                "vwap_state": str(metadata.get("vwap_state", "") or ""),
                "pre_filter_passed": bool(candidate_pre_filter.get("passed", False)),
                "pre_filter_reason": str(candidate_pre_filter.get("reason", "") or ""),
            }
        )

    @staticmethod
    def _candidate_ledger_key(item: Dict[str, Any]) -> str:
        decision = item.get("decision")
        analysis = item.get("analysis", {}) if isinstance(item.get("analysis"), dict) else {}
        return "|".join(
            [
                str(item.get("symbol") or ""),
                str(getattr(decision, "operation", "") or ""),
                str(analysis.get("time") or ""),
            ]
        )

    def _ensure_candidate_ledger_row(self, item: Dict[str, Any]) -> Dict[str, Any]:
        if not hasattr(self, "candidate_ledger_rows") or not isinstance(self.candidate_ledger_rows, list):
            self.candidate_ledger_rows = []
        if not hasattr(self, "_candidate_ledger_index") or not isinstance(self._candidate_ledger_index, dict):
            self._candidate_ledger_index = {}
        key = self._candidate_ledger_key(item)
        existing_idx = self._candidate_ledger_index.get(key)
        if existing_idx is not None:
            return self.candidate_ledger_rows[existing_idx]

        decision = item.get("decision")
        metadata = self._decision_metadata(decision)
        analysis = item.get("analysis", {}) if isinstance(item.get("analysis"), dict) else {}
        analysis_ts = pd.Timestamp(analysis.get("time")) if analysis.get("time") is not None else pd.NaT
        tp_plan = self._decision_tp_plan(decision=decision, analysis=analysis)
        row = {
            "candidate_key": key,
            "timestamp": analysis_ts.strftime("%Y-%m-%d %H:%M:%S") if pd.notna(analysis_ts) else "",
            "symbol": str(item.get("symbol") or ""),
            "local_operation": str(getattr(decision, "operation", "") or ""),
            "local_reason": str(getattr(decision, "reason", "") or ""),
            "score": float(item.get("score", 0.0) or 0.0),
            "signal_type_1h": str(metadata.get("signal_type_1h", "") or ""),
            "signal_score": self._safe_float(metadata.get("signal_score"), 0.0),
            "vwap_score": self._safe_float(metadata.get("vwap_score"), 0.0),
            "vwap_state": str(metadata.get("vwap_state", "") or ""),
            "entry_tier": str(metadata.get("entry_tier", "") or ""),
            "market_quadrant": str(metadata.get("market_quadrant", "") or ""),
            "macd_home_side": str(metadata.get("macd_home_side", "") or ""),
            "boll_value_zone": str(metadata.get("boll_value_zone", "") or ""),
            "boll_position_score": self._safe_float(metadata.get("boll_position_score"), 0.0),
            "vwap_execution_state": str(metadata.get("vwap_execution_state", "") or ""),
            "decision_target_portion": self._safe_float(
                getattr(decision, "target_portion_of_balance", 0.0),
                0.0,
            ),
            "session_position_scale": self._safe_float(
                (metadata.get("session_risk") or {}).get("position_scale")
                if isinstance(metadata.get("session_risk"), dict)
                else None,
                1.0,
            ),
            "effective_session_scale": self._safe_float(
                (metadata.get("symbol_risk") or {}).get("effective_session_scale")
                if isinstance(metadata.get("symbol_risk"), dict)
                else None,
                1.0,
            ),
            "vwap_structure_position_scale": self._safe_float(
                (metadata.get("vwap_structure_scale") or {}).get("position_scale")
                if isinstance(metadata.get("vwap_structure_scale"), dict)
                else None,
                1.0,
            ),
            "pocket_position_scale": self._safe_float(
                (metadata.get("pocket_management_override") or {}).get("position_scale")
                if isinstance(metadata.get("pocket_management_override"), dict)
                else None,
                1.0,
            ),
            "pocket_max_target_portion": self._safe_float(
                (metadata.get("pocket_management_override") or {}).get("max_target_portion")
                if isinstance(metadata.get("pocket_management_override"), dict)
                else None,
                0.0,
            ),
            "direction_lock": str(metadata.get("direction_lock", "") or ""),
            "is_trial_entry": bool(metadata.get("is_trial_entry", False)),
            "max_active_symbols": int(item.get("max_active_symbols", 0) or 0),
            "entry_price": self._safe_float(analysis.get("price"), 0.0),
            "tp1_price": float(tp_plan.get("tp1_price", 0.0) or 0.0),
            "tp1_pct": float(tp_plan.get("tp1_pct", 0.0) or 0.0),
            "tp1_reduce_pct": float(tp_plan.get("tp1_reduce_pct", 0.0) or 0.0),
            "take_profit_price": float(tp_plan.get("take_profit_price", 0.0) or 0.0),
            "entry_bar_same_bar_checked": False,
            "entry_bar_same_bar_stop_hit": False,
            "entry_bar_same_bar_target_hit": False,
            "entry_bar_same_bar_tp1_hit": False,
            "entry_bar_same_bar_resolution": "",
            "generated_open_candidate": True,
            "cluster_key": "",
            "cluster_rank": None,
            "cluster_age_minutes": None,
            "pre_filter_passed": None,
            "pre_filter_reason": "",
            "ai_shortlisted": False,
            "shortlist_rank": None,
            "shortlist_block_reason": "",
            "ai_reviewed": False,
            "ai_allowed": None,
            "ai_operation": "",
            "ai_block_reason": "",
            "ai_ds_source": "",
            "ai_ds_confidence": 0.0,
            "capacity_selected": False,
            "capacity_replacement_candidate": False,
            "capacity_replacement_target": "",
            "capacity_block_reason": "",
            "final_opened": False,
            "final_reject_reason": "",
        }
        self._candidate_ledger_index[key] = len(self.candidate_ledger_rows)
        self.candidate_ledger_rows.append(row)
        return row

    def _update_candidate_ledger(self, item: Dict[str, Any], **updates: Any) -> None:
        row = self._ensure_candidate_ledger_row(item)
        row.update(updates)

    def _matching_cluster_gate_rules(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        cfg = getattr(self, "candidate_pre_filter_cfg", {})
        rules = cfg.get("candidate_filter_cluster_gates", []) if isinstance(cfg, dict) else []
        if not isinstance(rules, list):
            return []
        matched: List[Dict[str, Any]] = []
        symbol = str(payload.get("symbol") or "").upper()
        signal_type = str(payload.get("signal_type") or "")
        vwap_state = str(payload.get("vwap_state") or "")
        side = str(payload.get("side") or "").lower()
        for item in rules:
            if not isinstance(item, dict):
                continue
            gate_symbol = str(item.get("symbol") or "").strip().upper()
            gate_signal_type = str(item.get("signal_type") or "").strip()
            gate_vwap_state = str(item.get("vwap_state") or "").strip()
            gate_side = str(item.get("side") or "").strip().lower()
            if gate_symbol and gate_symbol != symbol:
                continue
            if gate_signal_type and gate_signal_type != signal_type:
                continue
            if gate_vwap_state and gate_vwap_state != vwap_state:
                continue
            if gate_side and gate_side != side:
                continue
            matched.append(item)
        return matched

    def _candidate_cluster_context(self, payload: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
        matched_rules = self._matching_cluster_gate_rules(payload)
        if not matched_rules:
            return {"cluster_key": "", "cluster_rank": 1, "cluster_age_minutes": 0.0}

        analysis = item.get("analysis", {}) if isinstance(item.get("analysis"), dict) else {}
        current_ts = pd.Timestamp(analysis.get("time")) if analysis.get("time") is not None else pd.NaT
        if pd.isna(current_ts):
            return {"cluster_key": "", "cluster_rank": 1, "cluster_age_minutes": 0.0}

        cluster_key = "|".join(
            [
                str(payload.get("symbol") or ""),
                str(payload.get("side") or ""),
                str(payload.get("signal_type") or ""),
                str(payload.get("vwap_state") or ""),
            ]
        )
        max_window_minutes = max(self._safe_float(rule.get("cluster_window_minutes"), 60.0) for rule in matched_rules)
        state = self._candidate_cluster_state.get(cluster_key, {})
        first_ts = pd.Timestamp(state.get("first_ts")) if state.get("first_ts") is not None else pd.NaT
        last_ts = pd.Timestamp(state.get("last_ts")) if state.get("last_ts") is not None else pd.NaT
        candidate_count = int(state.get("candidate_count", 0) or 0)

        if pd.isna(first_ts) or pd.isna(last_ts):
            cluster_rank = 1
            cluster_age_minutes = 0.0
            first_ts = current_ts
        else:
            gap_minutes = (current_ts - last_ts).total_seconds() / 60.0
            if gap_minutes > max_window_minutes:
                cluster_rank = 1
                cluster_age_minutes = 0.0
                first_ts = current_ts
            else:
                cluster_rank = candidate_count + 1
                cluster_age_minutes = max(0.0, (current_ts - first_ts).total_seconds() / 60.0)

        self._candidate_cluster_state[cluster_key] = {
            "first_ts": first_ts.isoformat(),
            "last_ts": current_ts.isoformat(),
            "candidate_count": cluster_rank,
        }
        return {
            "cluster_key": cluster_key,
            "cluster_rank": int(cluster_rank),
            "cluster_age_minutes": float(cluster_age_minutes),
        }

    def _candidate_filter_payload(self, item: Dict[str, Any]) -> Dict[str, Any]:
        decision = item.get("decision")
        metadata = self._decision_metadata(decision)
        regime_state = metadata.get("macd_4h_regime_state") if isinstance(metadata.get("macd_4h_regime_state"), dict) else {}
        regime_side = str(regime_state.get("side", "") or "").lower()
        regime_score = self._safe_float(regime_state.get("score"), 0.0)
        if regime_side == "short":
            score_4h_direction_bear = regime_score
        else:
            score_4h_direction_bear = 0.0
        payload = {
            "symbol": str(item.get("symbol", "") or ""),
            "signal_type": str(metadata.get("signal_type_1h", "") or ""),
            "vwap_state": str(metadata.get("vwap_state", "") or ""),
            "signal_score": self._safe_float(metadata.get("signal_score"), 0.0),
            "vwap_score": self._safe_float(metadata.get("vwap_score"), 0.0),
            "side": "long" if getattr(decision, "operation", None) == Operation.BUY else "short",
            "is_trial_entry": bool(metadata.get("is_trial_entry", False)),
            "score_4h_direction_bear": score_4h_direction_bear,
            "macd_4h_shrink_pct": self._safe_float(
                metadata.get("macd_4h_shrink_pct"),
                self._safe_float((metadata.get("macd_v2_debug") or {}).get("macd_4h_shrink_pct"), 0.0),
            ),
        }
        payload.update(self._candidate_cluster_context(payload, item))
        return payload

    def _apply_candidate_pre_filter(self, open_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self.candidate_pre_filter_enabled:
            for item in open_candidates:
                self._update_candidate_ledger(
                    item,
                    pre_filter_passed=True,
                    pre_filter_reason="disabled",
                )
            return open_candidates

        filtered: List[Dict[str, Any]] = []
        funnel = self._ensure_signal_funnel()
        for item in open_candidates:
            payload = self._candidate_filter_payload(item)
            self._update_candidate_ledger(
                item,
                cluster_key=str(payload.get("cluster_key") or ""),
                cluster_rank=int(payload.get("cluster_rank", 1) or 1),
                cluster_age_minutes=float(payload.get("cluster_age_minutes", 0.0) or 0.0),
            )
            result = pre_ai_candidate_filter(payload, self.candidate_pre_filter_cfg)
            item["candidate_pre_filter"] = {"passed": bool(result.passed), "reason": result.reason}
            self._update_candidate_ledger(
                item,
                pre_filter_passed=bool(result.passed),
                pre_filter_reason=str(result.reason or ""),
            )
            funnel.log(
                "5_pre_ai_candidate_filter",
                passed=bool(result.passed),
                reason=result.reason,
                score=float(item.get("score", 0.0) or 0.0),
            )
            if result.passed:
                filtered.append(item)
        return filtered

    def _dynamic_leverage_cap(self) -> Optional[int]:
        if not bool(getattr(self, "dynamic_leverage_enabled", False)):
            return None
        trades = getattr(self, "trades", [])
        cfg = getattr(self, "dynamic_leverage_cfg", {})
        return get_max_leverage_by_recent_performance(trades, cfg=cfg)

    def _apply_pretrade_gate(
        self,
        *,
        symbol: str,
        decision: FundFlowDecision,
        analysis: Dict[str, Any],
    ) -> FundFlowDecision:
        position = self.positions.get(symbol)
        gated_decision, _ = self.bot_logic._apply_pretrade_risk_gate(
            symbol=symbol,
            decision=decision,
            position=position if isinstance(position, dict) else None,
            flow_context=analysis["flow_context"],
            current_price=float(analysis["price"]),
            account_summary=self._build_account_summary(),
        )
        return gated_decision

    def _apply_position_management_override(
        self,
        *,
        symbol: str,
        decision: FundFlowDecision,
        analysis: Dict[str, Any],
    ) -> FundFlowDecision:
        position = self.positions.get(symbol)
        if not isinstance(position, dict):
            return decision
        return self.bot_logic._apply_position_management_overrides(
            symbol=symbol,
            position=position,
            current_price=float(analysis["price"]),
            decision=decision,
            flow_context=analysis["flow_context"],
        )

    def _enforce_ai_final_review(self, open_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not open_candidates:
            return []

        shortlisted = sorted(open_candidates, key=self._priority_score, reverse=True)
        skipped = shortlisted[self.ai_flat_top_n :]
        shortlisted = shortlisted[: self.ai_flat_top_n]
        if skipped:
            skipped_symbols = [str(item.get("symbol") or "") for item in skipped if str(item.get("symbol") or "")]
            print(
                f"AI Bot-like空仓候选收敛: 仅保留前{self.ai_flat_top_n}个标的进入终审, "
                f"跳过={','.join(skipped_symbols)}"
            )
            for rank, item in enumerate(skipped, start=self.ai_flat_top_n + 1):
                decision = item.get("decision")
                self._update_candidate_ledger(
                    item,
                    ai_shortlisted=False,
                    shortlist_rank=int(rank),
                    shortlist_block_reason="ai_shortlist_topn",
                    ai_reviewed=False,
                    ai_allowed=False,
                    ai_operation=str(getattr(decision, "operation", "") or ""),
                    ai_block_reason="ai_shortlist_topn",
                    ai_ds_source="-",
                    ai_ds_confidence=0.0,
                )
                self._append_ai_advice_log(
                    item=item,
                    rank=rank,
                    review_mode="skipped_topn",
                    allowed=False,
                    block_reason="ai_shortlist_topn",
                    ai_operation=str(getattr(decision, "operation", "") or ""),
                    ds_source="-",
                    ds_confidence=0.0,
                )

        approved: List[Dict[str, Any]] = []
        for rank, item in enumerate(shortlisted, start=1):
            symbol = str(item["symbol"])
            analysis = item["analysis"]
            decision = item["decision"]
            local_score = self._priority_score(item)
            current_price = float(analysis["price"])
            flow_context = analysis["flow_context"]
            ai_trigger_context = {
                "trigger_type": "scheduled",
                "signal_pool_id": None,
                "allow_entry_window": True,
                "ai_gate": "final",
                "local_operation": decision.operation.value,
                "candidate_rank": rank,
                "candidate_score": local_score,
            }
            ai_decision = self.decision_engine.decide(
                symbol=symbol,
                portfolio=self._build_portfolio(),
                price=current_price,
                market_flow_context=flow_context,
                trigger_context=ai_trigger_context,
                use_weight_router=True,
                use_ai_weights=True,
            )
            ai_md = ai_decision.metadata if isinstance(getattr(ai_decision, "metadata", None), dict) else {}
            ai_source = str(ai_md.get("ds_source") or "-")
            ai_conf = self._safe_float(ai_md.get("ds_confidence"), 0.0)
            allow_ai_entry, block_reason = self.bot_logic._ai_entry_guard(
                decision=ai_decision,
                local_score=local_score,
                flow_context=flow_context,
                ai_review_cfg=self.ai_review_cfg,
                position=None,
            )
            review_log = {
                "mode": "enforced",
                "shortlist_rank": rank,
                "candidate_rank": rank,
                "local_operation": decision.operation.value,
                "ai_operation": ai_decision.operation.value,
                "local_score": local_score,
                "allow_ai_entry": bool(allow_ai_entry),
                "block_reason": block_reason,
                "ds_source": ai_source,
                "ds_confidence": ai_conf,
            }
            self._update_candidate_ledger(
                item,
                ai_shortlisted=True,
                shortlist_rank=int(rank),
                shortlist_block_reason="",
                ai_reviewed=True,
                ai_allowed=bool(allow_ai_entry and ai_decision.operation == decision.operation),
                ai_operation=ai_decision.operation.value,
                ai_block_reason=str(block_reason or ""),
                ai_ds_source=ai_source,
                ai_ds_confidence=float(ai_conf),
            )
            self._append_ai_advice_log(
                item=item,
                rank=rank,
                review_mode="enforced",
                allowed=bool(allow_ai_entry and ai_decision.operation == decision.operation),
                block_reason=block_reason,
                ai_operation=ai_decision.operation.value,
                ds_source=ai_source,
                ds_confidence=ai_conf,
            )
            if ai_decision.operation != decision.operation:
                print(
                    f"AI_BLOCK {symbol} Bot-like终审未通过: rank={rank} "
                    f"local={decision.operation.value.upper()} "
                    f"ai={ai_decision.operation.value.upper()} "
                    f"source={ai_source} conf={ai_conf:.3f}"
                )
                continue
            if not allow_ai_entry:
                print(
                    f"AI_BLOCK {symbol} Bot-like终审结构拦截: rank={rank} "
                    f"local={decision.operation.value.upper()} "
                    f"reason={block_reason} "
                    f"source={ai_source} conf={ai_conf:.3f}"
                )
                continue

            original_md = self._decision_metadata(decision)
            exec_md = dict(original_md)
            if isinstance(ai_md, dict):
                exec_md.update(ai_md)
            exec_md["ai_final_review"] = review_log
            ai_decision.metadata = exec_md
            item["decision"] = ai_decision
            item["score"] = float(self._decision_signal_score(ai_decision) or self.bot_logic._decision_signal_score(ai_decision, flow_context))
            approved.append(item)
        self._log_ai_review_funnel(shortlisted=shortlisted, approved=approved, skipped=skipped)
        return approved

    def close_position(
        self,
        symbol: str,
        price: float,
        time: float,
        reason: str,
        *,
        reduce_margin: Optional[float] = None,
        reduce_pct_original: Optional[float] = None,
    ):
        pos_before = dict(self.positions.get(symbol, {})) if isinstance(self.positions.get(symbol), dict) else {}
        trade_count_before = len(self.trades)
        super().close_position(
            symbol,
            price,
            time,
            reason,
            reduce_margin=reduce_margin,
            reduce_pct_original=reduce_pct_original,
        )
        if len(self.trades) <= trade_count_before:
            return
        trade = self.trades[-1]
        trailing_activated = bool(pos_before.get("trailing_activated", False))
        trailing_peak_ratio = float(pos_before.get("trailing_peak_ratio", 0.0) or 0.0)
        trade["partial_close"] = bool(reduce_pct_original is not None and float(reduce_pct_original or 0.0) < 1.0)
        trade["partial_close_ratio"] = float(reduce_pct_original or 0.0)
        trade["trailing_activated"] = trailing_activated
        trade["trailing_peak_pnl_pct"] = trailing_peak_ratio * 100.0
        self._refresh_trade_post_close_audit(trade=trade, symbol=symbol)
        if reason == "trailing_stop" and trailing_peak_ratio > 0:
            pnl_ratio = float(trade.get("pnl_pct", 0.0) or 0.0) / 100.0
            trade["trailing_retention_rate"] = pnl_ratio / trailing_peak_ratio
        else:
            trade["trailing_retention_rate"] = None
        if symbol not in self.positions:
            self._clear_position_tracking(symbol)

    def _refresh_trade_post_close_audit(self, *, trade: Dict[str, Any], symbol: str) -> None:
        post_pos = self.positions.get(symbol) if isinstance(self.positions.get(symbol), dict) else None
        post_tp_levels = list(post_pos.get("take_profit_levels") or []) if isinstance(post_pos, dict) else []
        post_tp_levels_filled_count = sum(
            1
            for level in post_tp_levels
            if isinstance(level, dict) and bool(level.get("filled"))
        )
        trade["position_still_open_after"] = isinstance(post_pos, dict)
        trade["post_close_remaining_fraction"] = (
            float(post_pos.get("remaining_fraction", 0.0) or 0.0)
            if isinstance(post_pos, dict)
            else 0.0
        )
        trade["post_close_margin"] = (
            float(post_pos.get("margin", 0.0) or 0.0)
            if isinstance(post_pos, dict)
            else 0.0
        )
        trade["post_close_stop_price"] = (
            float(post_pos.get("stop_price", 0.0) or 0.0)
            if isinstance(post_pos, dict)
            else 0.0
        )
        trade["post_close_tp_levels_filled_count"] = int(post_tp_levels_filled_count)
        trade["post_close_tp_levels_remaining_count"] = int(
            max(0, len(post_tp_levels) - post_tp_levels_filled_count)
        )

    def _close_position_bot_like(
        self,
        symbol: str,
        analysis: Dict[str, Any],
        reason: str,
        *,
        reduce_pct_original: Optional[float] = None,
        exit_price: Optional[float] = None,
        trade_updates: Optional[Dict[str, Any]] = None,
    ) -> None:
        trade_count_before = len(self.trades)
        self.close_position(
            symbol,
            float(exit_price if exit_price is not None else analysis["price"]),
            analysis["time"],
            reason,
            reduce_pct_original=reduce_pct_original,
        )
        if trade_updates and len(self.trades) > trade_count_before:
            self.trades[-1].update(dict(trade_updates))

    @staticmethod
    def _intrabar_exit_hits(pos: Dict[str, Any], row: pd.Series) -> Dict[str, Any]:
        high_price = float(row["high"])
        low_price = float(row["low"])
        stop_hit = False
        target_hit = False
        if pos["side"] == "long":
            stop_hit = low_price <= float(pos["stop_price"])
            target_hit = pos.get("take_profit") is not None and high_price >= float(pos["take_profit"])
        else:
            stop_hit = high_price >= float(pos["stop_price"])
            target_hit = pos.get("take_profit") is not None and low_price <= float(pos["take_profit"])

        hit_levels: List[dict] = []
        tp_levels = pos.get("take_profit_levels") or []
        if tp_levels:
            for level in tp_levels:
                if not isinstance(level, dict) or bool(level.get("filled")):
                    continue
                level_price = float(level.get("price", 0.0) or 0.0)
                if level_price <= 0:
                    continue
                if pos["side"] == "long" and high_price >= level_price:
                    hit_levels.append(level)
                elif pos["side"] == "short" and low_price <= level_price:
                    hit_levels.append(level)
            if hit_levels:
                if pos["side"] == "long":
                    hit_levels.sort(key=lambda item: float(item.get("price", 0.0)))
                else:
                    hit_levels.sort(key=lambda item: float(item.get("price", 0.0)), reverse=True)
        return {
            "stop_hit": bool(stop_hit),
            "target_hit": bool(target_hit),
            "hit_levels": hit_levels,
        }

    def _resolve_same_bar_priority_mode(self) -> str:
        mode = str(getattr(self, "entry_bar_same_bar_priority_mode", "") or "").strip().lower()
        if mode:
            return mode
        return str(getattr(self.config, "same_bar_tp_priority_mode", "stop_first") or "stop_first").strip().lower()

    def _handle_entry_bar_same_bar_after_open(self, item: Dict[str, Any]) -> None:
        if not bool(getattr(self, "entry_bar_same_bar_enabled", False)):
            return
        symbol = str(item.get("symbol") or "")
        if not symbol or symbol not in self.positions:
            return
        analysis = item.get("analysis", {}) if isinstance(item.get("analysis"), dict) else {}
        row = analysis.get("row_15m")
        if row is None:
            return

        pos = self.positions[symbol]
        hits = self._intrabar_exit_hits(pos, row)
        stop_hit = bool(hits.get("stop_hit"))
        target_hit = bool(hits.get("target_hit"))
        hit_levels = list(hits.get("hit_levels") or [])
        tp1_hit = bool(hit_levels)
        mode = self._resolve_same_bar_priority_mode()
        trade_updates = {
            "entry_bar_same_bar": True,
            "entry_bar_same_bar_priority_mode": mode,
        }
        ledger_updates = {
            "entry_bar_same_bar_checked": True,
            "entry_bar_same_bar_stop_hit": stop_hit,
            "entry_bar_same_bar_target_hit": target_hit,
            "entry_bar_same_bar_tp1_hit": tp1_hit,
            "entry_bar_same_bar_resolution": "no_hit",
        }

        if stop_hit and hit_levels and mode == "tp1_before_stop":
            first_level = hit_levels[0]
            reduce_pct_original = max(0.0, min(1.0, float(first_level.get("reduce_pct", 0.0) or 0.0)))
            if reduce_pct_original > 0:
                level_price = float(first_level.get("price", 0.0) or 0.0)
                exit_price = self._target_fill_price(pos, row, level_price)
                self._close_position_bot_like(
                    symbol,
                    analysis,
                    "take_profit_level_intrabar",
                    reduce_pct_original=reduce_pct_original,
                    exit_price=exit_price,
                    trade_updates={**trade_updates, "entry_bar_same_bar_resolution": "tp1_before_stop_same_bar"},
                )
                if symbol in self.positions:
                    hit_levels[0]["filled"] = True
                    if self.trades:
                        self._refresh_trade_post_close_audit(trade=self.trades[-1], symbol=symbol)
                    pos = self.positions[symbol]
                    stop_exit_price = self._stop_fill_price(pos, row, float(pos["stop_price"]))
                    stop_reason = "stop_loss_intrabar_after_tp1_same_bar"
                    if target_hit:
                        stop_reason = "stop_loss_intrabar_both_hit_after_tp1_same_bar"
                    self._close_position_bot_like(
                        symbol,
                        analysis,
                        stop_reason,
                        exit_price=stop_exit_price,
                        trade_updates={**trade_updates, "entry_bar_same_bar_resolution": "tp1_before_stop_same_bar"},
                    )
                ledger_updates["entry_bar_same_bar_resolution"] = "tp1_before_stop_same_bar"
                self._update_candidate_ledger(item, **ledger_updates)
                return

        if stop_hit:
            self._close_position_bot_like(
                symbol,
                analysis,
                "stop_loss_intrabar_both_hit" if target_hit else "stop_loss_intrabar",
                exit_price=self._stop_fill_price(pos, row, float(pos["stop_price"])),
                trade_updates={**trade_updates, "entry_bar_same_bar_resolution": "stop_first_same_bar"},
            )
            ledger_updates["entry_bar_same_bar_resolution"] = "stop_first_same_bar"
            self._update_candidate_ledger(item, **ledger_updates)
            return

        if hit_levels:
            for level in hit_levels:
                if symbol not in self.positions:
                    break
                level_price = float(level.get("price", 0.0) or 0.0)
                reduce_pct_original = max(0.0, min(1.0, float(level.get("reduce_pct", 0.0) or 0.0)))
                if reduce_pct_original <= 0:
                    continue
                self._close_position_bot_like(
                    symbol,
                    analysis,
                    "take_profit_level_intrabar",
                    reduce_pct_original=reduce_pct_original,
                    exit_price=self._target_fill_price(self.positions[symbol], row, level_price),
                    trade_updates={**trade_updates, "entry_bar_same_bar_resolution": "tp_levels_same_bar"},
                )
                level["filled"] = True
                if self.trades and symbol in self.positions:
                    self._refresh_trade_post_close_audit(trade=self.trades[-1], symbol=symbol)
            ledger_updates["entry_bar_same_bar_resolution"] = "tp_levels_same_bar"
            self._update_candidate_ledger(item, **ledger_updates)
            return

        if target_hit:
            self._close_position_bot_like(
                symbol,
                analysis,
                "take_profit_intrabar",
                exit_price=self._target_fill_price(pos, row, float(pos["take_profit"])),
                trade_updates={**trade_updates, "entry_bar_same_bar_resolution": "take_profit_same_bar"},
            )
            ledger_updates["entry_bar_same_bar_resolution"] = "take_profit_same_bar"
            self._update_candidate_ledger(item, **ledger_updates)
            return

        self._update_candidate_ledger(item, **ledger_updates)

    def _check_stops_bot_like(self, symbol: str, analysis: Dict[str, Any]) -> bool:
        if symbol not in self.positions:
            return False

        pos = self.positions[symbol]
        row = analysis["row_15m"]
        price = float(analysis["price"])
        time = analysis["time"]
        high_price = float(row["high"])
        low_price = float(row["low"])

        if self.config.breakeven_enabled:
            if pos["side"] == "long":
                best_pnl_pct = (high_price - pos["entry_price"]) / pos["entry_price"]
                if best_pnl_pct >= self.config.breakeven_trigger_pnl_ratio:
                    pos["stop_price"] = max(
                        float(pos["stop_price"]),
                        float(pos["entry_price"]) * (1.0 + self.config.breakeven_lock_ratio),
                    )
            else:
                best_pnl_pct = (pos["entry_price"] - low_price) / pos["entry_price"]
                if best_pnl_pct >= self.config.breakeven_trigger_pnl_ratio:
                    pos["stop_price"] = min(
                        float(pos["stop_price"]),
                        float(pos["entry_price"]) * (1.0 - self.config.breakeven_lock_ratio),
                    )

        hits = self._intrabar_exit_hits(pos, row)
        stop_hit = bool(hits.get("stop_hit"))
        target_hit = bool(hits.get("target_hit"))
        hit_levels = list(hits.get("hit_levels") or [])

        same_bar_tp_priority_mode = self._resolve_same_bar_priority_mode()
        if stop_hit and hit_levels and same_bar_tp_priority_mode == "tp1_before_stop":
            first_level = hit_levels[0]
            reduce_pct_original = max(0.0, min(1.0, float(first_level.get("reduce_pct", 0.0) or 0.0)))
            if reduce_pct_original > 0:
                level_price = float(first_level.get("price", 0.0) or 0.0)
                exit_price = self._target_fill_price(pos, row, level_price)
                self.close_position(
                    symbol,
                    exit_price,
                    time,
                    "take_profit_level_intrabar",
                    reduce_pct_original=reduce_pct_original,
                )
                first_level["filled"] = True
                if symbol not in self.positions:
                    return True
                pos = self.positions[symbol]
                stop_exit_price = self._stop_fill_price(pos, row, float(pos["stop_price"]))
                stop_reason = "stop_loss_intrabar_after_tp1_same_bar"
                if target_hit:
                    stop_reason = "stop_loss_intrabar_both_hit_after_tp1_same_bar"
                self.close_position(symbol, stop_exit_price, time, stop_reason)
                return True

        if stop_hit:
            exit_price = self._stop_fill_price(pos, row, float(pos["stop_price"]))
            reason = str(pos.get("stop_reason", "stop_loss_intrabar") or "stop_loss_intrabar")
            if target_hit:
                reason = "stop_loss_intrabar_both_hit"
            self.close_position(symbol, exit_price, time, reason)
            return True

        if hit_levels:
            for level in hit_levels:
                if symbol not in self.positions:
                    break
                level_price = float(level.get("price", 0.0) or 0.0)
                reduce_pct_original = max(0.0, min(1.0, float(level.get("reduce_pct", 0.0) or 0.0)))
                if reduce_pct_original <= 0:
                    continue
                exit_price = self._target_fill_price(self.positions[symbol], row, level_price)
                self.close_position(
                    symbol,
                    exit_price,
                    time,
                    "take_profit_level_intrabar",
                    reduce_pct_original=reduce_pct_original,
                )
                level["filled"] = True
            return symbol not in self.positions

        if target_hit:
            exit_price = self._target_fill_price(pos, row, float(pos["take_profit"]))
            self.close_position(symbol, exit_price, time, "take_profit_intrabar")
            return True

        return False

    def _dynamic_cap(self, decision: FundFlowDecision) -> int:
        md = decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}
        engine_override = md.get("params_override")
        engine_override = engine_override if isinstance(engine_override, dict) else {}
        cap, _ = self.bot_logic._resolve_dynamic_max_active_symbols(
            decision=decision,
            engine_override=engine_override,
            base_max_active_symbols=self.config.max_positions,
        )
        return int(cap)

    def _reject_open_position(self, reason: str) -> bool:
        self._last_open_reject_reason = str(reason or "open_position_rejected")
        return False

    @staticmethod
    def _decision_take_profit_levels(metadata: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        raw_levels = metadata.get("tp_levels") if isinstance(metadata, dict) else None
        if not isinstance(raw_levels, list):
            return None

        levels: List[Dict[str, Any]] = []
        for item in raw_levels:
            if not isinstance(item, dict):
                continue
            price = BotLikeReplayEngine._safe_float(item.get("price"), 0.0)
            reduce_pct = max(0.0, min(1.0, BotLikeReplayEngine._safe_float(item.get("reduce_pct"), 0.0)))
            if price <= 0 or reduce_pct <= 0:
                continue
            levels.append(
                {
                    "price": float(price),
                    "reduce_pct": float(reduce_pct),
                    "filled": bool(item.get("filled", False)),
                }
            )
        return levels

    def _decision_tp_plan(
        self,
        *,
        decision: FundFlowDecision,
        analysis: Dict[str, Any],
    ) -> Dict[str, float]:
        md = self._decision_metadata(decision)
        price = float(analysis.get("price", 0.0) or 0.0)
        side = "long" if getattr(decision, "operation", None) == Operation.BUY else "short"
        config = getattr(self, "config", None)
        default_take_profit_pct = self._safe_float(getattr(config, "default_take_profit_pct", 0.0), 0.0)
        default_tp_pct_levels = list(getattr(config, "take_profit_pct_levels", []) or [])
        default_tp_reduce_levels = list(getattr(config, "take_profit_reduce_pct_levels", []) or [])
        tp_state = md.get("tp_sl") if isinstance(md.get("tp_sl"), dict) else {}
        explicit_tp_disabled = "tp_enabled" in tp_state and (not bool(tp_state.get("tp_enabled")))
        decision_tp_levels = self._decision_take_profit_levels(md)
        explicit_tp_plan = explicit_tp_disabled or decision_tp_levels is not None

        take_profit = self._safe_float(getattr(decision, "take_profit_price", None), 0.0)
        if take_profit <= 0 and (not explicit_tp_plan) and default_take_profit_pct > 0:
            take_profit = price * (1.0 + default_take_profit_pct) if side == "long" else price * (1.0 - default_take_profit_pct)
        if take_profit <= 0:
            take_profit = 0.0

        take_profit_levels = (
            decision_tp_levels
            if decision_tp_levels is not None
            else self._normalize_tp_levels(
                price=price,
                side=side,
                pct_levels=default_tp_pct_levels,
                reduce_levels=default_tp_reduce_levels,
            )
        )
        tp1_price = 0.0
        tp1_pct = 0.0
        tp1_reduce_pct = 0.0
        if isinstance(take_profit_levels, list) and take_profit_levels:
            first = take_profit_levels[0] if isinstance(take_profit_levels[0], dict) else {}
            tp1_price = self._safe_float(first.get("price"), 0.0)
            tp1_reduce_pct = self._safe_float(first.get("reduce_pct"), 0.0)
            if tp1_price > 0 and price > 0:
                tp1_pct = abs(tp1_price - price) / price
        return {
            "tp1_price": float(tp1_price),
            "tp1_pct": float(tp1_pct),
            "tp1_reduce_pct": float(tp1_reduce_pct),
            "take_profit_price": float(take_profit),
        }

    def _priority_score(self, item: Dict[str, Any]) -> float:
        if hasattr(self.bot_logic, "_candidate_priority_score"):
            try:
                return float(self.bot_logic._candidate_priority_score(item, self.ai_review_cfg))
            except Exception:
                pass
        return float(item.get("score", 0.0) or 0.0)

    def _capacity_group_cap(self, item: Dict[str, Any]) -> Tuple[str, int]:
        if hasattr(self.bot_logic, "_capacity_group_cap"):
            try:
                return self.bot_logic._capacity_group_cap(item, self.ai_review_cfg)
            except Exception:
                pass
        return "", 0

    def _apply_min_open_floor_override(
        self,
        *,
        decision: FundFlowDecision,
        target_portion: float,
    ) -> float:
        md = decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}
        override = md.get("pocket_management_override") if isinstance(md.get("pocket_management_override"), dict) else {}
        if not override or not bool(override.get("promote_to_min_open", False)):
            return target_portion
        entry_tier = str(md.get("entry_tier") or "").strip().lower()
        if entry_tier not in {"tier1", "tier2"}:
            return target_portion
        min_score = self._safe_float(override.get("promote_to_min_open_min_signal_score"), 0.0)
        signal_score = self._safe_float(md.get("signal_score"), 0.0)
        if signal_score < min_score:
            return target_portion
        return max(target_portion, float(self.config.min_open_portion))

    def _open_position_from_decision(self, symbol: str, decision: FundFlowDecision, analysis: Dict[str, Any]) -> bool:
        self._last_open_reject_reason = ""
        if symbol in self.positions or decision.operation not in (Operation.BUY, Operation.SELL):
            return self._reject_open_position("position_exists_or_invalid_operation")

        md = decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}
        entry_tier = str(md.get("entry_tier") or "").strip().lower()
        if entry_tier == "blocked":
            return self._reject_open_position("entry_tier_blocked")
        leverage = int(self.config.fixed_leverage or decision.leverage or self.config.default_leverage)
        leverage = max(self.config.min_leverage, min(self.config.max_leverage, leverage))
        dynamic_cap = self._dynamic_leverage_cap()
        if dynamic_cap is not None:
            leverage = min(leverage, int(dynamic_cap))
        if leverage <= 0:
            return self._reject_open_position("dynamic_leverage_blocked")

        target_portion = max(0.0, float(decision.target_portion_of_balance or 0.0))
        target_portion = self._apply_min_open_floor_override(
            decision=decision,
            target_portion=target_portion,
        )
        target_portion = min(target_portion, float(self.config.max_symbol_position_portion))
        if target_portion < float(self.config.min_open_portion):
            return self._reject_open_position("target_portion_below_min_open")

        deployable_capital = self.capital * max(0.0, 1.0 - self.config.reserve_pct)
        required_margin = deployable_capital * target_portion
        max_affordable_margin = self.capital / max(1.0, (1.0 + leverage * self.config.fee_rate))
        required_margin = min(required_margin, max_affordable_margin)
        if required_margin < 100.0:
            return self._reject_open_position("required_margin_too_small")

        side = "long" if decision.operation == Operation.BUY else "short"
        price = float(analysis["price"])
        fill_price = float(decision.max_price if side == "long" else decision.min_price or 0.0)
        if fill_price <= 0:
            fill_price = price * (1.0 + self.config.entry_slippage) if side == "long" else price * (1.0 - self.config.entry_slippage)

        stop_price = self._safe_float(decision.stop_loss_price, 0.0)
        if stop_price <= 0:
            stop_price = price * (1.0 - self.config.default_stop_loss_pct) if side == "long" else price * (1.0 + self.config.default_stop_loss_pct)

        tp_state = md.get("tp_sl") if isinstance(md.get("tp_sl"), dict) else {}
        explicit_tp_disabled = "tp_enabled" in tp_state and (not bool(tp_state.get("tp_enabled")))
        decision_tp_levels = self._decision_take_profit_levels(md)
        explicit_tp_plan = explicit_tp_disabled or decision_tp_levels is not None

        take_profit = self._safe_float(decision.take_profit_price, 0.0)
        if take_profit <= 0 and (not explicit_tp_plan) and self.config.default_take_profit_pct > 0:
            take_profit = price * (1.0 + self.config.default_take_profit_pct) if side == "long" else price * (1.0 - self.config.default_take_profit_pct)
        if take_profit <= 0:
            take_profit = None

        take_profit_levels = (
            decision_tp_levels
            if decision_tp_levels is not None
            else self._normalize_tp_levels(
                price=price,
                side=side,
                pct_levels=self.config.take_profit_pct_levels,
                reduce_levels=self.config.take_profit_reduce_pct_levels,
            )
        )

        order = {
            "side": side,
            "margin": float(required_margin),
            "position_value": float(required_margin),
            "leverage": leverage,
            "dynamic_leverage_cap": dynamic_cap,
            "stop_price": float(stop_price),
            "take_profit": take_profit,
            "take_profit_levels": take_profit_levels,
            "signal_score": self._safe_float(md.get("signal_score"), 0.0),
            "signal_type_1h": str(md.get("signal_type_1h", "") or ""),
            "entry_tier": str(md.get("entry_tier", "") or ""),
            "capacity_replacement_candidate": bool(md.get("capacity_replacement_candidate", False)),
            "capacity_replacement_target": str(
                (md.get("capacity_replacement_target") or {}).get("symbol", "")
                if isinstance(md.get("capacity_replacement_target"), dict)
                else md.get("capacity_replacement_target", "") or ""
            ),
            "pocket_management_override": dict(md.get("pocket_management_override", {}))
            if isinstance(md.get("pocket_management_override"), dict)
            else {},
            "is_trial_entry": bool(md.get("is_trial_entry", False)),
            "entry_scale": self._safe_float(md.get("entry_scale"), 1.0),
            "session_position_scale": 1.0,
            "vwap_score": self._safe_float(md.get("vwap_score"), 0.0),
            "vwap_state": str(md.get("vwap_state", "") or ""),
            "vwap_location_score": self._safe_float(md.get("vwap_location_score"), 0.0),
            "ema_multiplier": self._safe_float(md.get("ema_multiplier"), 1.0),
            "ema_status": str(md.get("ema_structure_status", "") or ""),
            "adx_1h": self._safe_float(md.get("adx_1h"), 0.0),
            "adx_4h": self._safe_float(md.get("adx_4h"), 0.0),
            "cvd_veto_state": "inactive",
            "cvd_veto_triggered": False,
            "cvd_veto_reason": "",
            "cvd_bonus_state": "inactive",
            "cvd_bonus_multiplier": 1.0,
        }

        self.capital -= float(required_margin)
        opened = bool(self._fill_pending_order(symbol, order, fill_price, analysis["time"]))
        if opened and symbol in self.positions:
            self._sync_position_tracking(symbol, analysis)
            self._last_open_reject_reason = ""
        elif not opened:
            self._last_open_reject_reason = "fill_pending_order_failed"
        return opened

    def run_backtest(self, market_data_map: Dict[str, Dict[str, pd.DataFrame]]) -> dict:
        if not market_data_map:
            return {
                "signals_generated": 0,
                "timeline_points": 0,
                "open_candidates": 0,
                "analysis_attempts": 0,
                "analysis_ready": 0,
                "analysis_skipped": {},
                "decision_counts": {},
                "replay_microstructure_degraded_samples": 0,
                "replay_microstructure_proxy_samples": 0,
                "replay_spread_missing_samples": 0,
                "replay_microstructure_missing_fields": {},
                "replay_microstructure_proxy_fields": {},
                "replay_microstructure_stats_summary": {},
            }

        timeline: set[int] = set()
        idx_maps: Dict[str, Dict[int, int]] = {}
        active_tf = self._decision_timeframe()
        for symbol, data in market_data_map.items():
            symbol_tf = active_tf if active_tf in data else "15m"
            tf_active = data[symbol_tf]
            ts_values = [self._timestamp_key(ts) for ts in tf_active["timestamp"].tolist()]
            idx_maps[symbol] = {ts: idx for idx, ts in enumerate(ts_values)}
            timeline.update(ts_values)
            print(f"  Bot-like回放 {symbol}: {len(tf_active)} 根{symbol_tf} K线")

        ordered_timeline = sorted(timeline)
        print(f"\n统一时间轴 Bot-like 回放: {len(ordered_timeline)} 个{active_tf}时间点")

        signals_generated = 0
        open_candidates_seen = 0
        last_timestamp: Optional[Any] = None
        analysis_attempts = 0
        analysis_ready = 0
        analysis_skipped: Counter[str] = Counter()
        decision_counts: Counter[str] = Counter()
        replay_micro_missing_fields: Counter[str] = Counter()
        replay_micro_degraded_samples = 0
        replay_micro_proxy_fields: Counter[str] = Counter()
        replay_micro_proxy_samples = 0
        replay_spread_missing_samples = 0
        funnel = self._ensure_signal_funnel()
        config = getattr(self, "config", None)
        window_start_iso = getattr(config, "window_start_iso", "")
        window_end_iso = getattr(config, "window_end_iso", "")

        for current_ts in ordered_timeline:
            analyses: Dict[str, Dict[str, Any]] = {}
            for symbol, data in market_data_map.items():
                idx_active = idx_maps[symbol].get(current_ts)
                if idx_active is None:
                    continue
                analysis_attempts += 1
                analysis, analysis_status = self._build_analysis_with_status(symbol, data, idx_active)
                if analysis is None:
                    analysis_skipped[analysis_status] += 1
                    continue
                analysis_ready += 1
                analyses[symbol] = analysis
                self._last_price_map[symbol] = float(analysis["price"])
                last_timestamp = analysis["time"]
                replay_micro = analysis["flow_context"].get("replay_microstructure")
                if isinstance(replay_micro, dict):
                    missing_fields = replay_micro.get("missing_fields", [])
                    proxy_filled_fields = replay_micro.get("proxy_filled_fields", [])
                    if missing_fields:
                        replay_micro_degraded_samples += 1
                        for field_name in missing_fields:
                            replay_micro_missing_fields[str(field_name)] += 1
                    if proxy_filled_fields:
                        replay_micro_proxy_samples += 1
                        for field_name in proxy_filled_fields:
                            replay_micro_proxy_fields[str(field_name)] += 1
                    if "spread_bps" in missing_fields:
                        replay_spread_missing_samples += 1

            current_time = pd.Timestamp(current_ts)
            if not timestamp_in_trade_window(
                current_time,
                trade_window_start_iso=window_start_iso,
                trade_window_end_iso=window_end_iso,
            ):
                continue

            for symbol in list(self.positions.keys()):
                analysis = analyses.get(symbol)
                if analysis is not None:
                    self._set_backtest_now_ts(analysis["time"])
                    self._sync_position_tracking(symbol, analysis)
                    self._check_stops_bot_like(symbol, analysis)

            close_candidates: List[Dict[str, Any]] = []
            open_candidates: List[Dict[str, Any]] = []

            for symbol, analysis in analyses.items():
                self._set_backtest_now_ts(analysis["time"])
                decision = self._decide(symbol, analysis)
                self._log_decision_funnel(decision)
                decision_before_pretrade = decision
                decision = self._apply_position_management_override(
                    symbol=symbol,
                    decision=decision,
                    analysis=analysis,
                )
                decision = self._apply_pretrade_gate(
                    symbol=symbol,
                    decision=decision,
                    analysis=analysis,
                )
                self._log_pretrade_funnel(decision_before_pretrade, decision)
                decision_counts[decision.operation.value] += 1
                if decision.operation in (Operation.BUY, Operation.SELL, Operation.CLOSE):
                    signals_generated += 1

                md = decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}
                score = float(self._decision_signal_score(decision) or self.bot_logic._decision_signal_score(decision, analysis["flow_context"]))
                item = {
                    "symbol": symbol,
                    "analysis": analysis,
                    "decision": decision,
                    "score": score,
                    "max_active_symbols": self._dynamic_cap(decision),
                    "signal_type_1h": str(md.get("signal_type_1h", "") or ""),
                }

                if symbol in self.positions:
                    if decision.operation == Operation.CLOSE:
                        close_candidates.append(item)
                elif decision.operation in (Operation.BUY, Operation.SELL):
                    self._ensure_candidate_ledger_row(item)
                    open_candidates.append(item)

            for item in close_candidates:
                close_decision = item["decision"]
                close_ratio = float(getattr(close_decision, "target_portion_of_balance", 1.0) or 1.0)
                self._close_position_bot_like(
                    str(item["symbol"]),
                    item["analysis"],
                    reason=f"decision_close:{close_decision.reason}",
                    reduce_pct_original=close_ratio if close_ratio < 0.999999 else None,
                )

            if open_candidates:
                open_candidates_seen += len(open_candidates)
                open_candidates = self._apply_candidate_pre_filter(open_candidates)
                if self.ai_review_enabled:
                    open_candidates = self._enforce_ai_final_review(open_candidates)
                for item in open_candidates:
                    item["_priority_score"] = self._priority_score(item)
                    group_key, group_cap = self._capacity_group_cap(item)
                    item["_capacity_group_key"] = group_key
                    item["_capacity_group_cap"] = group_cap
                selected_candidates = self.bot_logic.select_open_candidates_with_replacement(
                    open_candidates,
                    active_positions=self._current_active_positions_snapshot(analyses),
                    active_count=len(self.positions),
                    ai_review_cfg=self.ai_review_cfg,
                )
                selected_keys = {
                    (
                        str(item.get("symbol") or ""),
                        str(getattr(item.get("decision"), "operation", "")),
                        str(item.get("analysis", {}).get("time") or ""),
                    )
                    for item in selected_candidates
                }
                for item in open_candidates:
                    key = (
                        str(item.get("symbol") or ""),
                        str(getattr(item.get("decision"), "operation", "")),
                        str(item.get("analysis", {}).get("time") or ""),
                    )
                    is_selected = key in selected_keys
                    self._update_candidate_ledger(
                        item,
                        capacity_selected=bool(is_selected),
                        capacity_replacement_candidate=bool(item.get("capacity_replacement_candidate", False)),
                        capacity_replacement_target=str((item.get("capacity_replacement_target") or {}).get("symbol", "") or ""),
                        capacity_block_reason="" if is_selected else f"capacity_block:max_active={item.get('max_active_symbols', 0)}",
                    )
                    funnel.log(
                        "11_capacity",
                        passed=is_selected,
                        reason="capacity_pass" if is_selected else f"capacity_block:max_active={item.get('max_active_symbols', 0)}",
                        score=float(item.get("score", 0.0) or 0.0),
                    )
                for item in selected_candidates:
                    replacement_target = item.get("capacity_replacement_target") if isinstance(item.get("capacity_replacement_target"), dict) else {}
                    replacement_symbol = str(replacement_target.get("symbol") or "")
                    if replacement_symbol and replacement_symbol in self.positions:
                        replacement_price = self._safe_float(
                            analyses.get(replacement_symbol, {}).get("price"),
                            self._safe_float(self._last_price_map.get(replacement_symbol), self._safe_float(self.positions[replacement_symbol].get("entry_price"), 0.0)),
                        )
                        self._close_position_bot_like(
                            replacement_symbol,
                            analyses.get(replacement_symbol, {"price": replacement_price, "time": item["analysis"]["time"]}),
                            reason=f"capacity_replacement_for:{item.get('symbol')}",
                            exit_price=replacement_price,
                            trade_updates={"capacity_replacement_target": replacement_symbol},
                        )
                    opened = self._open_position_from_decision(str(item["symbol"]), item["decision"], item["analysis"])
                    self._update_candidate_ledger(
                        item,
                        final_opened=bool(opened),
                        final_reject_reason="" if opened else str(getattr(self, "_last_open_reject_reason", "") or "open_position_rejected"),
                    )
                    if opened:
                        self._handle_entry_bar_same_bar_after_open(item)
                    funnel.log(
                        "12_final_fill",
                        passed=bool(opened),
                        reason="opened" if opened else str(getattr(self, "_last_open_reject_reason", "") or "open_position_rejected"),
                        score=float(item.get("score", 0.0) or 0.0),
                    )

            self._record_equity_snapshot(
                timestamp=analyses[next(iter(analyses))]["time"] if analyses else pd.Timestamp(current_ts),
                price_map=self._last_price_map,
            )

        if last_timestamp is not None:
            for symbol in list(self.positions.keys()):
                exit_price = float(self._last_price_map.get(symbol, self.positions[symbol].get("entry_price", 0.0)))
                self.close_position(symbol, exit_price, last_timestamp, "final_mark_to_market")
            self._record_equity_snapshot(last_timestamp, self._last_price_map)

        self._finalize_drawdown_recovery()
        print(
            "Bot-like早期计数: "
            f"analysis_attempts={analysis_attempts}, "
            f"analysis_ready={analysis_ready}, "
            f"analysis_skipped={dict(analysis_skipped)}, "
            f"decision_counts={dict(decision_counts)}, "
            f"replay_micro_missing={dict(replay_micro_missing_fields)}"
        )
        return {
            "signals_generated": signals_generated,
            "timeline_points": len(ordered_timeline),
            "open_candidates": open_candidates_seen,
            "ai_advice_logs": len(self.ai_advice_logs),
            "candidate_ledger_rows": len(getattr(self, "candidate_ledger_rows", [])),
            "analysis_attempts": analysis_attempts,
            "analysis_ready": analysis_ready,
            "analysis_skipped": dict(analysis_skipped),
            "decision_counts": dict(decision_counts),
            "replay_microstructure_degraded_samples": replay_micro_degraded_samples,
            "replay_microstructure_proxy_samples": replay_micro_proxy_samples,
            "replay_spread_missing_samples": replay_spread_missing_samples,
            "replay_microstructure_missing_fields": dict(replay_micro_missing_fields),
            "replay_microstructure_proxy_fields": dict(replay_micro_proxy_fields),
            "replay_microstructure_stats_summary": dict(getattr(self, "replay_microstructure_stats_summary", {}) or {}),
            "signal_funnel": funnel.report(),
        }


def run_backtest(
    config_path: str = "config/trading_config_fund_flow.json",
    initial_capital: float = 10000.0,
    fee_rate: float = 0.0004,
    max_positions_override: Optional[int] = None,
    fixed_leverage: Optional[int] = None,
    profile_name: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
):
    runtime_cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    runtime_cfg, applied_profile = apply_backtest_profile(runtime_cfg, profile_name)
    config = build_backtest_config(
        runtime_cfg=runtime_cfg,
        config_path=config_path,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        max_positions_override=max_positions_override,
        fixed_leverage=fixed_leverage,
        profile_name=applied_profile,
        window_start_iso=start_time or "",
        window_end_iso=end_time or "",
    )
    strategy_config = build_strategy_config(runtime_cfg)

    print("=" * 70)
    print("Bot-Like Fund Flow Replay")
    print("=" * 70)
    print(f"config_path: {config_path}")
    print(f"trade_window: {config.window_start_iso or '(open)'} -> {config.window_end_iso or '(open)'}")
    print(f"data_window: {config.data_window_start_iso or '(open)'} -> {config.data_window_end_iso or '(open)'}")
    print(f"warmup_hours: {config.warmup_hours}")
    print(f"symbols: {len(config.symbols)}")
    print(f"max_positions: {config.max_positions}")

    engine = BotLikeReplayEngine(config, strategy_config, runtime_cfg)
    market_data_map: Dict[str, Dict[str, pd.DataFrame]] = {}
    available_symbols: List[str] = []
    missing_symbols: List[str] = []

    for symbol in config.symbols:
        data = load_symbol_data(
            config.data_dir,
            symbol,
            strategy_config,
            decision_timeframe=config.decision_timeframe,
        )
        if data:
            available_symbols.append(symbol)
            market_data_map[symbol] = data
        else:
            missing_symbols.append(symbol)

    dropped_by_window: List[str] = []
    data_window_start = config.data_window_start_iso or start_time
    data_window_end = config.data_window_end_iso or end_time
    if market_data_map and (data_window_start or data_window_end):
        market_data_map, dropped_by_window = apply_market_data_window(
            market_data_map,
            start_time=data_window_start,
            end_time=data_window_end,
        )
        available_symbols = list(market_data_map.keys())
        if dropped_by_window:
            missing_symbols.extend(dropped_by_window)

    stats = engine.run_backtest(market_data_map) if market_data_map else {"signals_generated": 0, "timeline_points": 0}

    output_dir = Path("output/backtest")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    trades_file = output_dir / f"bot_like_trades_{timestamp}.csv"
    equity_curve_file = output_dir / f"bot_like_equity_curve_{timestamp}.csv"
    summary_file = output_dir / f"bot_like_summary_{timestamp}.json"
    ai_advice_file = output_dir / f"bot_like_ai_advice_{timestamp}.csv"
    candidate_ledger_file = output_dir / f"bot_like_candidate_ledger_{timestamp}.csv"
    analysis_dir = Path("output/analysis")
    analysis_dir.mkdir(parents=True, exist_ok=True)
    signal_funnel_file = analysis_dir / "signal_funnel_30d.json"

    trades_df = pd.DataFrame(engine.trades)
    trades_df.to_csv(trades_file, index=False)
    pd.DataFrame(engine.equity_curve).to_csv(equity_curve_file, index=False)
    if engine.ai_advice_logs:
        pd.DataFrame(engine.ai_advice_logs).to_csv(ai_advice_file, index=False)
    if engine.candidate_ledger_rows:
        pd.DataFrame(engine.candidate_ledger_rows).to_csv(candidate_ledger_file, index=False)

    summary = build_backtest_summary(
        config=config,
        strategy_config=strategy_config,
        engine=engine,
        available_symbols=available_symbols,
        missing_symbols=missing_symbols,
        stats=stats,
    )
    summary["engine_mode"] = "bot_like_replay"
    summary["stats"] = stats
    if isinstance(stats.get("signal_funnel"), dict):
        signal_funnel_file.write_text(
            json.dumps(stats["signal_funnel"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    summary["files"] = {
        "trades": str(trades_file),
        "equity_curve": str(equity_curve_file),
        "ai_advice": str(ai_advice_file) if engine.ai_advice_logs else "",
        "candidate_ledger": str(candidate_ledger_file) if engine.candidate_ledger_rows else "",
        "signal_funnel": str(signal_funnel_file),
    }
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n结果:")
    print(f"  final_capital: ${engine.capital:,.2f}")
    print(f"  return_pct: {summary['return_pct']:+.2f}%")
    print(f"  total_trades: {summary['total_trades']}")
    print(f"  win_rate_pct: {summary['win_rate_pct']:.2f}%")
    print(f"  max_drawdown_pct: {summary['risk_metrics']['max_drawdown_pct']:.2f}%")
    print(f"  open_candidates_seen: {stats.get('open_candidates', 0)}")
    print(f"  ai_advice_logs: {stats.get('ai_advice_logs', 0)}")
    print(f"  candidate_ledger_rows: {stats.get('candidate_ledger_rows', 0)}")
    print(f"  replay_micro_degraded_samples: {stats.get('replay_microstructure_degraded_samples', 0)}")
    print(f"  replay_micro_missing_fields: {stats.get('replay_microstructure_missing_fields', {})}")
    print(f"  trades_file: {trades_file}")
    print(f"  equity_curve_file: {equity_curve_file}")
    print(f"  summary_file: {summary_file}")
    if engine.ai_advice_logs:
        print(f"  ai_advice_file: {ai_advice_file}")
    if engine.candidate_ledger_rows:
        print(f"  candidate_ledger_file: {candidate_ledger_file}")

    engine.last_summary = summary
    engine.last_summary_file = str(summary_file)
    engine.last_trades_file = str(trades_file)
    engine.last_equity_curve_file = str(equity_curve_file)
    engine.last_ai_advice_file = str(ai_advice_file) if engine.ai_advice_logs else None
    engine.last_candidate_ledger_file = str(candidate_ledger_file) if engine.candidate_ledger_rows else None
    return engine


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run bot-like fund flow replay backtest")
    parser.add_argument("--config", default="config/trading_config_fund_flow.json", help="runtime config path")
    parser.add_argument("--initial-capital", type=float, default=10000.0, help="initial capital in USDT")
    parser.add_argument("--fee-rate", type=float, default=0.0004, help="fee rate per side")
    parser.add_argument("--max-positions", type=int, default=None, help="override max concurrent positions")
    parser.add_argument("--fixed-leverage", type=int, default=None, help="force a fixed leverage for all entries")
    parser.add_argument("--profile", default=None, help="optional backtest profile name from fund_flow.backtest.profiles")
    parser.add_argument("--start", default=None, help="optional inclusive backtest window start")
    parser.add_argument("--end", default=None, help="optional inclusive backtest window end")
    args = parser.parse_args()
    run_backtest(
        config_path=args.config,
        initial_capital=args.initial_capital,
        fee_rate=args.fee_rate,
        max_positions_override=args.max_positions,
        fixed_leverage=args.fixed_leverage,
        profile_name=args.profile,
        start_time=args.start,
        end_time=args.end,
    )
