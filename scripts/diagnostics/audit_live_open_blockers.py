from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable


def _iter_runtime_log_files(inputs: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_file():
            files.append(path.resolve())
            continue
        if path.is_dir():
            files.extend(sorted(p.resolve() for p in path.rglob("runtime.out.00.log")))
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def collect_runtime_blocker_evidence(runtime_files: Iterable[Path]) -> Dict[str, Dict[str, Any]]:
    evidence: defaultdict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "reject_codes": Counter(),
            "extreme_volatility_skips": 0,
        }
    )
    for path in runtime_files:
        current_symbol = "UNKNOWN"
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("[") and "]" in line:
                    current_symbol = line.split("]", 1)[0].lstrip("[").strip().upper() or "UNKNOWN"
                if "HOLD归因:" in line and "code=" in line:
                    code = line.split("code=", 1)[1].split(",", 1)[0].strip()
                    if code and code != "-":
                        evidence[current_symbol]["reject_codes"][code] += 1
                if "极端波动冷却中，跳过新开仓" in line:
                    evidence[current_symbol]["extreme_volatility_skips"] += 1
    return dict(evidence)


def classify_symbol_override_policy(
    *,
    symbol: str,
    override_payload: Dict[str, Any],
    evidence: Dict[str, Any],
) -> Dict[str, Any]:
    payload = dict(override_payload or {})
    reject_codes = evidence.get("reject_codes", {}) if isinstance(evidence, dict) else {}
    if isinstance(reject_codes, Counter):
        reject_codes = dict(reject_codes)
    hard_disable_flip = bool(payload.get("disable_flip_bullish"))
    hard_disable_green = bool(payload.get("disable_green_bar_growing"))
    has_threshold_override = any(
        payload.get(key) is not None
        for key in ("min_signal_score_override", "min_vwap_score_override", "preflip_trial_min_signal_score_override")
    )
    flip_disabled_evidence = int(reject_codes.get("flip_bullish_disabled", 0)) + int(
        reject_codes.get("flip_bullish_trial_disabled", 0)
    )
    green_disabled_evidence = int(reject_codes.get("green_bar_growing_disabled", 0))

    action = "KEEP"
    if (hard_disable_flip and flip_disabled_evidence <= 0) or (hard_disable_green and green_disabled_evidence <= 0):
        action = "DOWNGRADE"
    elif not hard_disable_flip and not hard_disable_green and not has_threshold_override:
        action = "REMOVE"

    result: Dict[str, Any] = {
        "symbol": str(symbol or "").upper(),
        "action": action,
        "evidence": {
            "flip_disabled_rejects": flip_disabled_evidence,
            "green_disabled_rejects": green_disabled_evidence,
            "extreme_volatility_skips": int(evidence.get("extreme_volatility_skips", 0) or 0)
            if isinstance(evidence, dict)
            else 0,
        },
    }
    if action == "DOWNGRADE":
        if hard_disable_flip:
            result["flip_bullish_mode"] = "trial_only"
        if hard_disable_green:
            result["green_bar_growing_mode"] = "enabled_with_strict_threshold"
    return result


def diagnose_replay_data_gap(summary_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(summary_payload or {})
    available = [str(x).upper() for x in (payload.get("available_symbols") or []) if str(x).strip()]
    missing = [str(x).upper() for x in (payload.get("missing_symbols") or []) if str(x).strip()]
    if missing and not available:
        return {
            "issue": "data_gap_missing_symbol_files",
            "is_data_gap": True,
            "available_symbols": available,
            "missing_symbols": missing,
        }
    if missing:
        return {
            "issue": "partial_data_gap_missing_symbol_files",
            "is_data_gap": True,
            "available_symbols": available,
            "missing_symbols": missing,
        }
    return {
        "issue": "no_replay_data_gap_detected",
        "is_data_gap": False,
        "available_symbols": available,
        "missing_symbols": missing,
    }


def _load_symbol_overrides(config_payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    ff_cfg = config_payload.get("fund_flow", {}) if isinstance(config_payload, dict) else {}
    raw_sources = [
        ff_cfg.get("symbol_signal_overrides"),
        ff_cfg.get("symbol_overrides"),
    ]
    v2_cfg = ff_cfg.get("macd_mtf_strategy_v2", {}) if isinstance(ff_cfg.get("macd_mtf_strategy_v2"), dict) else {}
    entry_filters = v2_cfg.get("entry_filters", {}) if isinstance(v2_cfg.get("entry_filters"), dict) else {}
    raw_sources.append(entry_filters.get("symbol_signal_overrides"))

    merged: Dict[str, Dict[str, Any]] = {}
    for raw in raw_sources:
        if isinstance(raw, dict):
            for symbol, payload in raw.items():
                if isinstance(payload, dict):
                    merged[str(symbol).upper()] = dict(payload)
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and str(item.get("symbol") or "").strip():
                    payload = dict(item)
                    symbol = str(payload.pop("symbol")).upper()
                    merged[symbol] = payload
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit live open blockers and replay data gaps.")
    parser.add_argument("--config", required=True, help="Path to live runtime config JSON.")
    parser.add_argument("--runtime", nargs="*", default=[], help="Runtime log files or date directories.")
    parser.add_argument("--replay-summary", help="Optional bot-like replay summary JSON.")
    args = parser.parse_args()

    config_payload = json.loads(Path(args.config).read_text(encoding="utf-8"))
    symbol_overrides = _load_symbol_overrides(config_payload)
    runtime_files = _iter_runtime_log_files(args.runtime)
    evidence_map = collect_runtime_blocker_evidence(runtime_files)
    override_audit = [
        classify_symbol_override_policy(
            symbol=symbol,
            override_payload=payload,
            evidence=evidence_map.get(symbol, {}),
        )
        for symbol, payload in sorted(symbol_overrides.items())
    ]
    replay_diagnostic = None
    if args.replay_summary:
        replay_payload = json.loads(Path(args.replay_summary).read_text(encoding="utf-8"))
        replay_diagnostic = diagnose_replay_data_gap(replay_payload)

    print(
        json.dumps(
            {
                "override_audit": override_audit,
                "replay_diagnostic": replay_diagnostic,
                "runtime_files": [str(path) for path in runtime_files],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
