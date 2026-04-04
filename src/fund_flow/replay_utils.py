from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class MicroStructureProxy:
    spread_bps: float
    depth_ratio: float
    imbalance: float


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _session_key(bar_time: datetime) -> str:
    hour = int(bar_time.hour)
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 16:
        return "europe"
    return "us"


def _normalize_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iter_attribution_microstructure_records(
    logs_root: str | Path,
    *,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> Iterable[Dict[str, Any]]:
    root = Path(logs_root)
    for path in sorted(root.rglob("fund_flow_attribution.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_raw = payload.get("ts")
                context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
                flow_context = context.get("flow_context") if isinstance(context.get("flow_context"), dict) else {}
                symbol = str(context.get("symbol") or "")
                if not symbol or not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(str(ts_raw))
                except ValueError:
                    continue
                ts = _normalize_utc(ts)
                if start_time and ts < start_time:
                    continue
                if end_time and ts > end_time:
                    continue
                spread_bps = _to_float(flow_context.get("spread_bps"))
                depth_ratio = _to_float(flow_context.get("depth_ratio"))
                imbalance = _to_float(flow_context.get("imbalance"))
                if spread_bps is None and depth_ratio is None and imbalance is None:
                    continue
                yield {
                    "ts": ts.isoformat(),
                    "symbol": symbol.upper(),
                    "spread_bps": spread_bps,
                    "depth_ratio": depth_ratio,
                    "imbalance": imbalance,
                }


def build_microstructure_stats_from_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, float]]]:
    grouped: Dict[str, Dict[str, Dict[str, list[float]]]] = {}
    for record in records:
        symbol = str(record.get("symbol") or "").upper()
        ts_raw = record.get("ts")
        if not symbol or not ts_raw:
            continue
        ts = ts_raw if isinstance(ts_raw, datetime) else datetime.fromisoformat(str(ts_raw))
        session = _session_key(ts)
        symbol_bucket = grouped.setdefault(symbol, {})
        session_bucket = symbol_bucket.setdefault(
            session,
            {
                "spread_bps": [],
                "depth_ratio": [],
                "imbalance": [],
            },
        )
        for field in ("spread_bps", "depth_ratio", "imbalance"):
            value = _to_float(record.get(field))
            if value is not None:
                session_bucket[field].append(value)

    stats: Dict[str, Dict[str, Dict[str, float]]] = {}
    all_sessions: Dict[str, Dict[str, list[float]]] = {}
    for symbol, session_map in grouped.items():
        stats[symbol] = {}
        for session, values in session_map.items():
            stats[symbol][session] = {}
            for field, samples in values.items():
                if samples:
                    stats[symbol][session][f"{field}_p50"] = round(float(median(samples)), 6)
                    all_sessions.setdefault(session, {}).setdefault(field, []).extend(samples)

    if all_sessions:
        stats["_default"] = {}
        for session, values in all_sessions.items():
            stats["_default"][session] = {}
            for field, samples in values.items():
                if samples:
                    stats["_default"][session][f"{field}_p50"] = round(float(median(samples)), 6)
    return stats


def get_micro_structure_proxy(
    symbol: str,
    bar_time: datetime,
    *,
    historical_stats: Optional[Dict[str, Dict[str, Dict[str, float]]]] = None,
) -> Optional[MicroStructureProxy]:
    stats = historical_stats or {}
    session = _session_key(bar_time)
    symbol_key = str(symbol or "").upper()

    def _pick(bucket_symbol: str) -> Optional[MicroStructureProxy]:
        symbol_map = stats.get(bucket_symbol)
        if not isinstance(symbol_map, dict):
            return None
        session_map = symbol_map.get(session)
        if not isinstance(session_map, dict):
            return None
        spread = _to_float(session_map.get("spread_bps_p50"))
        depth = _to_float(session_map.get("depth_ratio_p50"))
        imbalance = _to_float(session_map.get("imbalance_p50"))
        if spread is None or depth is None or imbalance is None:
            return None
        return MicroStructureProxy(spread_bps=spread, depth_ratio=depth, imbalance=imbalance)

    direct = _pick(symbol_key)
    if direct is not None:
        return direct
    fallback = _pick("_default")
    if fallback is not None:
        return fallback
    return None


def build_flow_context_with_proxy(
    *,
    symbol: str,
    bar_time: datetime,
    raw_flow_context: Dict[str, Any],
    historical_stats: Optional[Dict[str, Dict[str, Dict[str, float]]]] = None,
) -> Dict[str, Any]:
    ctx = dict(raw_flow_context)
    proxy = get_micro_structure_proxy(symbol, bar_time, historical_stats=historical_stats)
    if proxy is None:
        return ctx
    if ctx.get("spread_bps") is None:
        ctx["spread_bps"] = proxy.spread_bps
        ctx["spread_bps_is_proxy"] = True
    if ctx.get("depth_ratio") is None:
        ctx["depth_ratio"] = proxy.depth_ratio
        ctx["depth_ratio_is_proxy"] = True
    if ctx.get("imbalance") is None:
        ctx["imbalance"] = proxy.imbalance
        ctx["imbalance_is_proxy"] = True
    return ctx
