"""
Bot-like fund flow replay backtest.

目标：
1. 复用现有 BacktestEngine 的仓位/PnL/权益曲线统计
2. 用 FundFlowDecisionEngine + TradingBot 的排序/容量逻辑替代纯策略回测入口
3. 更贴近 fund_flow_bot 的开仓链路，验证 signal_pool / pretrade gate / max_active_symbols 等外层影响
"""

from __future__ import annotations

import argparse
import json
import sys
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
        filter_market_data_by_time_range,
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
        filter_market_data_by_time_range,
        load_symbol_data,
    )
from src.app.fund_flow_bot import TradingBot
from src.fund_flow.decision_engine import FundFlowDecisionEngine
from src.fund_flow.models import FundFlowDecision, Operation


class BotLikeReplayEngine(BacktestEngine):
    """更贴近 fund_flow_bot 外层开仓链的回放引擎。"""

    def __init__(self, config: BacktestConfig, strategy_config, runtime_config: Dict[str, Any]):
        super().__init__(config, strategy_config, runtime_config)
        self.decision_engine = FundFlowDecisionEngine(runtime_config)
        self.bot_logic = TradingBot.__new__(TradingBot)
        self.bot_logic.config = runtime_config
        gate_logs_dir = (Path("output") / "backtest" / "logs").resolve()
        gate_logs_dir.mkdir(parents=True, exist_ok=True)
        self.bot_logic.logs_dir = str(gate_logs_dir)
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

        ff_cfg = runtime_config.get("fund_flow", {}) if isinstance(runtime_config.get("fund_flow"), dict) else {}
        ai_cfg = ff_cfg.get("ai_review", {}) if isinstance(ff_cfg.get("ai_review"), dict) else {}
        self.ai_review_enabled = bool(ai_cfg.get("enabled", True))
        self.ai_flat_top_n = max(1, int(self.bot_logic._to_float(ai_cfg.get("flat_top_n", 3), 3)))
        self.ai_review_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
        self.bot_logic._tighten_protection_for_conflict = self._backtest_tighten_protection_for_conflict

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
            }
        return payload

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

    def _build_flow_context(self, data: Dict[str, pd.DataFrame], idx_15m: int) -> Optional[Dict[str, Any]]:
        tf_15m = data["15m"]
        current_time = tf_15m.iloc[idx_15m]["timestamp"]
        idx_1h = self._find_tf_index(data, "1h", current_time)
        idx_4h = self._find_tf_index(data, "4h", current_time)
        if idx_1h < 5 or idx_4h < 5:
            return None

        tf15 = self._timeframe_context(data["15m"], idx_15m)
        tf1h = self._timeframe_context(data["1h"], idx_1h)
        tf4h = self._timeframe_context(data["4h"], idx_4h)

        flow_context: Dict[str, Any] = {
            "timeframes": {
                "15m": tf15,
                "1h": tf1h,
                "4h": tf4h,
            },
            "active_timeframe": "15m",
        }

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
            flow_context[key] = self._safe_optional_float(tf15.get(key))

        return flow_context

    def _build_analysis(self, symbol: str, data: Dict[str, pd.DataFrame], idx_15m: int) -> Optional[Dict[str, Any]]:
        if idx_15m < 50:
            return None
        flow_context = self._build_flow_context(data, idx_15m)
        if flow_context is None:
            return None
        row_15m = data["15m"].iloc[idx_15m]
        return {
            "symbol": symbol,
            "time": row_15m["timestamp"],
            "price": self._safe_float(row_15m.get("close"), 0.0),
            "row_15m": row_15m,
            "flow_context": flow_context,
        }

    def _build_portfolio(self) -> Dict[str, Any]:
        return {
            "cash": float(self.capital),
            "positions": self._current_positions_payload(self.positions),
            "total_assets": float(self._mark_to_market_equity(self._last_price_map)),
        }

    def _build_account_summary(self) -> Dict[str, Any]:
        max_leverage = float(self.config.fixed_leverage or self.config.default_leverage or 1)
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
        return self.decision_engine.decide(
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

        shortlisted = sorted(open_candidates, key=lambda x: float(x.get("score", 0.0)), reverse=True)
        skipped = shortlisted[self.ai_flat_top_n :]
        shortlisted = shortlisted[: self.ai_flat_top_n]
        if skipped:
            skipped_symbols = [str(item.get("symbol") or "") for item in skipped if str(item.get("symbol") or "")]
            print(
                f"AI Bot-like空仓候选收敛: 仅保留前{self.ai_flat_top_n}个标的进入终审, "
                f"跳过={','.join(skipped_symbols)}"
            )

        approved: List[Dict[str, Any]] = []
        for rank, item in enumerate(shortlisted, start=1):
            symbol = str(item["symbol"])
            analysis = item["analysis"]
            decision = item["decision"]
            local_score = float(item["score"])
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
            self.ai_advice_logs.append(
                {
                    "timestamp": pd.Timestamp(analysis["time"]).strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": symbol,
                    "rank": rank,
                    "score": local_score,
                    "review_mode": "enforced",
                    "local_operation": decision.operation.value,
                    "ai_operation": ai_decision.operation.value,
                    "allowed": bool(allow_ai_entry and ai_decision.operation == decision.operation),
                    "block_reason": block_reason,
                    "ds_source": ai_source,
                    "ds_confidence": ai_conf,
                    "signal_type_1h": item["signal_type_1h"],
                }
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

            exec_md = ai_md if isinstance(ai_md, dict) else {}
            exec_md["ai_final_review"] = review_log
            ai_decision.metadata = exec_md
            item["decision"] = ai_decision
            item["score"] = max(1.0, float(self.bot_logic._decision_signal_score(ai_decision, flow_context)))
            approved.append(item)
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
        if reason == "trailing_stop" and trailing_peak_ratio > 0:
            pnl_ratio = float(trade.get("pnl_pct", 0.0) or 0.0) / 100.0
            trade["trailing_retention_rate"] = pnl_ratio / trailing_peak_ratio
        else:
            trade["trailing_retention_rate"] = None
        if symbol not in self.positions:
            self._clear_position_tracking(symbol)

    def _close_position_bot_like(
        self,
        symbol: str,
        analysis: Dict[str, Any],
        reason: str,
        *,
        reduce_pct_original: Optional[float] = None,
    ) -> None:
        self.close_position(
            symbol,
            float(analysis["price"]),
            analysis["time"],
            reason,
            reduce_pct_original=reduce_pct_original,
        )

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

        stop_hit = False
        target_hit = False
        if pos["side"] == "long":
            stop_hit = low_price <= float(pos["stop_price"])
            target_hit = pos.get("take_profit") is not None and high_price >= float(pos["take_profit"])
        else:
            stop_hit = high_price >= float(pos["stop_price"])
            target_hit = pos.get("take_profit") is not None and low_price <= float(pos["take_profit"])

        if stop_hit:
            exit_price = self._stop_fill_price(pos, row, float(pos["stop_price"]))
            reason = str(pos.get("stop_reason", "stop_loss_intrabar") or "stop_loss_intrabar")
            if target_hit:
                reason = "stop_loss_intrabar_both_hit"
            self.close_position(symbol, exit_price, time, reason)
            return True

        tp_levels = pos.get("take_profit_levels") or []
        if tp_levels:
            hit_levels: List[dict] = []
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

    def _open_position_from_decision(self, symbol: str, decision: FundFlowDecision, analysis: Dict[str, Any]) -> bool:
        if symbol in self.positions or decision.operation not in (Operation.BUY, Operation.SELL):
            return False

        md = decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}
        leverage = int(self.config.fixed_leverage or decision.leverage or self.config.default_leverage)
        leverage = max(self.config.min_leverage, min(self.config.max_leverage, leverage))

        target_portion = max(0.0, float(decision.target_portion_of_balance or 0.0))
        target_portion = min(target_portion, float(self.config.max_symbol_position_portion))
        if target_portion < float(self.config.min_open_portion):
            return False

        deployable_capital = self.capital * max(0.0, 1.0 - self.config.reserve_pct)
        required_margin = deployable_capital * target_portion
        max_affordable_margin = self.capital / max(1.0, (1.0 + leverage * self.config.fee_rate))
        required_margin = min(required_margin, max_affordable_margin)
        if required_margin < 100.0:
            return False

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
            "stop_price": float(stop_price),
            "take_profit": take_profit,
            "take_profit_levels": take_profit_levels,
            "signal_score": self._safe_float(md.get("signal_score"), 0.0),
            "signal_type_1h": str(md.get("signal_type_1h", "") or ""),
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
        return opened

    @staticmethod
    def pick_open_candidates(open_candidates: List[Dict[str, Any]], active_count: int) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        current_active = int(active_count)
        for item in sorted(open_candidates, key=lambda x: float(x.get("score", 0.0)), reverse=True):
            cap = max(1, int(item.get("max_active_symbols", 1) or 1))
            if current_active >= cap:
                continue
            selected.append(item)
            current_active += 1
        return selected

    def run_backtest(self, market_data_map: Dict[str, Dict[str, pd.DataFrame]]) -> dict:
        if not market_data_map:
            return {"signals_generated": 0, "timeline_points": 0, "open_candidates": 0}

        timeline: set[int] = set()
        idx_maps: Dict[str, Dict[int, int]] = {}
        for symbol, data in market_data_map.items():
            tf_15m = data["15m"]
            ts_values = [self._timestamp_key(ts) for ts in tf_15m["timestamp"].tolist()]
            idx_maps[symbol] = {ts: idx for idx, ts in enumerate(ts_values)}
            timeline.update(ts_values)
            print(f"  Bot-like回放 {symbol}: {len(tf_15m)} 根15M K线")

        ordered_timeline = sorted(timeline)
        print(f"\n统一时间轴 Bot-like 回放: {len(ordered_timeline)} 个15M时间点")

        signals_generated = 0
        open_candidates_seen = 0
        last_timestamp: Optional[Any] = None

        for current_ts in ordered_timeline:
            analyses: Dict[str, Dict[str, Any]] = {}
            for symbol, data in market_data_map.items():
                idx_15m = idx_maps[symbol].get(current_ts)
                if idx_15m is None:
                    continue
                analysis = self._build_analysis(symbol, data, idx_15m)
                if analysis is None:
                    continue
                analyses[symbol] = analysis
                self._last_price_map[symbol] = float(analysis["price"])
                last_timestamp = analysis["time"]

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
                if decision.operation in (Operation.BUY, Operation.SELL, Operation.CLOSE):
                    signals_generated += 1

                md = decision.metadata if isinstance(getattr(decision, "metadata", None), dict) else {}
                score = max(1.0, float(self.bot_logic._decision_signal_score(decision, analysis["flow_context"])))
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
                if self.ai_review_enabled:
                    open_candidates = self._enforce_ai_final_review(open_candidates)
                selected_candidates = self.pick_open_candidates(open_candidates, active_count=len(self.positions))
                for item in selected_candidates:
                    self._open_position_from_decision(str(item["symbol"]), item["decision"], item["analysis"])

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
        return {
            "signals_generated": signals_generated,
            "timeline_points": len(ordered_timeline),
            "open_candidates": open_candidates_seen,
            "ai_advice_logs": len(self.ai_advice_logs),
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
    print(f"time_window: {start_time or '(open)'} -> {end_time or '(open)'}")
    print(f"symbols: {len(config.symbols)}")
    print(f"max_positions: {config.max_positions}")

    engine = BotLikeReplayEngine(config, strategy_config, runtime_cfg)
    market_data_map: Dict[str, Dict[str, pd.DataFrame]] = {}
    available_symbols: List[str] = []
    missing_symbols: List[str] = []

    for symbol in config.symbols:
        data = load_symbol_data(config.data_dir, symbol, strategy_config)
        if data:
            available_symbols.append(symbol)
            market_data_map[symbol] = data
        else:
            missing_symbols.append(symbol)

    dropped_by_window: List[str] = []
    if market_data_map and (start_time or end_time):
        market_data_map, dropped_by_window = filter_market_data_by_time_range(
            market_data_map,
            start_time=start_time,
            end_time=end_time,
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

    trades_df = pd.DataFrame(engine.trades)
    trades_df.to_csv(trades_file, index=False)
    pd.DataFrame(engine.equity_curve).to_csv(equity_curve_file, index=False)
    if engine.ai_advice_logs:
        pd.DataFrame(engine.ai_advice_logs).to_csv(ai_advice_file, index=False)

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
    summary["files"] = {
        "trades": str(trades_file),
        "equity_curve": str(equity_curve_file),
        "ai_advice": str(ai_advice_file) if engine.ai_advice_logs else "",
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
    print(f"  trades_file: {trades_file}")
    print(f"  equity_curve_file: {equity_curve_file}")
    print(f"  summary_file: {summary_file}")
    if engine.ai_advice_logs:
        print(f"  ai_advice_file: {ai_advice_file}")

    engine.last_summary = summary
    engine.last_summary_file = str(summary_file)
    engine.last_trades_file = str(trades_file)
    engine.last_equity_curve_file = str(equity_curve_file)
    engine.last_ai_advice_file = str(ai_advice_file) if engine.ai_advice_logs else None
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
