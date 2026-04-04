from __future__ import annotations

from typing import Any, Mapping, NamedTuple


class FilterResult(NamedTuple):
    passed: bool
    reason: str


def _candidate_get(candidate: Any, key: str, default: Any = None) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def _candidate_float(candidate: Any, key: str, default: float = 0.0) -> float:
    try:
        value = _candidate_get(candidate, key, default)
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _candidate_bool(candidate: Any, key: str, default: bool = False) -> bool:
    return bool(_candidate_get(candidate, key, default))


def _candidate_str(candidate: Any, key: str, default: str = "") -> str:
    value = _candidate_get(candidate, key, default)
    return str(value or default)


def pre_ai_candidate_filter(signal: Any, cfg: Mapping[str, Any]) -> FilterResult:
    pocket_key = f"{_candidate_str(signal, 'signal_type')}|{_candidate_str(signal, 'vwap_state')}"
    signal_type = _candidate_str(signal, "signal_type")
    signal_score = _candidate_float(signal, "signal_score", 0.0)
    vwap_score = _candidate_float(signal, "vwap_score", 0.0)
    side = _candidate_str(signal, "side").lower()

    min_signal_cfg = cfg.get("candidate_filter_min_signal_scores", {})
    if not isinstance(min_signal_cfg, Mapping):
        min_signal_cfg = {}
    min_score = float(
        min_signal_cfg.get(
            pocket_key,
            min_signal_cfg.get(signal_type, min_signal_cfg.get("_default", 0.87)),
        )
    )
    if signal_score < min_score:
        return FilterResult(False, f"PRE_AI_SCORE:{signal_score:.4f}<{min_score:.4f} [{pocket_key}]")

    min_vwap_cfg = cfg.get("candidate_filter_min_vwap_scores", {})
    if not isinstance(min_vwap_cfg, Mapping):
        min_vwap_cfg = {}
    min_vwap = float(
        min_vwap_cfg.get(
            pocket_key,
            min_vwap_cfg.get(_candidate_str(signal, "vwap_state"), min_vwap_cfg.get("_default", 0.10)),
        )
    )
    if vwap_score < min_vwap:
        return FilterResult(False, f"PRE_AI_VWAP:{vwap_score:.4f}<{min_vwap:.4f} [{pocket_key}]")

    reject_combos = cfg.get("candidate_filter_reject_combos", [])
    if isinstance(reject_combos, list):
        for item in reject_combos:
            if not isinstance(item, Mapping):
                continue
            combo_symbol = str(item.get("symbol") or "").strip().upper()
            combo_signal_type = str(item.get("signal_type") or "").strip()
            combo_vwap_state = str(item.get("vwap_state") or "").strip()
            if combo_symbol and combo_symbol != _candidate_str(signal, "symbol").upper():
                continue
            if combo_signal_type and combo_signal_type != signal_type:
                continue
            if combo_vwap_state and combo_vwap_state != _candidate_str(signal, "vwap_state"):
                continue
            min_combo_signal = float(item.get("min_signal_score", 0.0) or 0.0)
            min_combo_vwap = float(item.get("min_vwap_score", 0.0) or 0.0)
            if signal_score >= min_combo_signal and vwap_score >= min_combo_vwap:
                label = str(item.get("reason_label") or "combo_reject")
                return FilterResult(
                    False,
                    f"PRE_AI_REJECT_COMBO:{label}:{signal_score:.4f}/{vwap_score:.4f} [{pocket_key}]",
                )

    cluster_gates = cfg.get("candidate_filter_cluster_gates", [])
    if isinstance(cluster_gates, list):
        cluster_rank = int(_candidate_float(signal, "cluster_rank", 1.0))
        cluster_age_minutes = _candidate_float(signal, "cluster_age_minutes", 0.0)
        symbol = _candidate_str(signal, "symbol").upper()
        vwap_state = _candidate_str(signal, "vwap_state")
        for item in cluster_gates:
            if not isinstance(item, Mapping):
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
            max_candidate_rank = int(_candidate_float(item, "max_candidate_rank", 1.0))
            cluster_window_minutes = _candidate_float(item, "cluster_window_minutes", 60.0)
            if cluster_rank > max_candidate_rank and cluster_age_minutes <= cluster_window_minutes:
                label = str(item.get("reason_label") or "cluster_repeat")
                return FilterResult(
                    False,
                    f"PRE_AI_CLUSTER_REPEAT:{label}:rank={cluster_rank},age={cluster_age_minutes:.1f} [{pocket_key}]",
                )

    if side == "long":
        max_4h_bear_score = float(cfg.get("candidate_filter_max_4h_bear_score_for_long", 0.85))
        bear_score = _candidate_float(signal, "score_4h_direction_bear", 0.0)
        if bear_score > max_4h_bear_score:
            return FilterResult(False, f"PRE_AI_4H_BEAR:{bear_score:.4f}>{max_4h_bear_score:.4f}")

    if _candidate_bool(signal, "is_trial_entry", False):
        min_shrink = float(cfg.get("candidate_filter_trial_min_4h_shrink_pct", 0.45))
        shrink_pct = _candidate_float(signal, "macd_4h_shrink_pct", 0.0)
        if shrink_pct < min_shrink:
            return FilterResult(False, f"PRE_AI_TRIAL_SHRINK:{shrink_pct:.4f}<{min_shrink:.4f}")

    return FilterResult(True, "PRE_AI_PASS")
