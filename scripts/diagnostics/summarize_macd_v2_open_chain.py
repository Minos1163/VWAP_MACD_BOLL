from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


def _iter_input_files(inputs: list[str], *, default_name: str | None = None) -> list[Path]:
    files: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_file():
            files.append(path)
            continue
        if path.is_dir():
            if default_name:
                files.extend(sorted(path.rglob(default_name)))
            else:
                files.extend(sorted(p for p in path.rglob("*") if p.is_file()))
            continue
        files.extend(sorted(Path().glob(raw)))
    deduped: list[Path] = []
    seen: set[Path] = set()
    for file_path in files:
        resolved = file_path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return deduped


def _to_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nested_get(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _parse_stage_bucket(reason: str) -> str:
    text = str(reason or "").strip()
    if not text:
        return "unknown"
    if ":" in text:
        text = text.split(":", 1)[0]
    if text.startswith("L1_"):
        return "L1"
    if text.startswith("L2_"):
        return "L2"
    if text.startswith("L3_"):
        return "L3"
    return "other"


def _summarize_attribution(files: list[Path]) -> dict[str, Any]:
    reject_codes: Counter[str] = Counter()
    primary_blockers: Counter[str] = Counter()
    pocket_hits: Counter[str] = Counter()
    entry_hard_gate_failures: Counter[str] = Counter()
    execution_quality_vs_entries: defaultdict[str, Counter[str]] = defaultdict(Counter)
    fallback_attempts = 0
    fallback_allowed = 0
    fallback_success = 0
    decision_events = 0
    execution_events = 0

    for path in files:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = str(payload.get("event") or "").strip().lower()
                decision = _to_dict(payload.get("decision"))
                metadata = _to_dict(decision.get("metadata"))
                operation = str(decision.get("operation") or "").strip().lower()

                if event == "decision":
                    decision_events += 1
                    reject_code = str(
                        metadata.get("reject_reason_code")
                        or _nested_get(metadata, "macd_v2_debug", "reject_reason_code", default="")
                        or ""
                    ).strip()
                    if reject_code:
                        reject_codes[reject_code] += 1
                    primary_blocker = str(metadata.get("primary_open_blocker") or "").strip()
                    if primary_blocker:
                        primary_blockers[primary_blocker] += 1

                    pocket_label = str(
                        metadata.get("pocket_entry_override_label")
                        or _nested_get(metadata, "macd_v2_debug", "pocket_entry_override_label", default="")
                        or ""
                    ).strip()
                    if not pocket_label:
                        signal_type = str(metadata.get("signal_type_1h") or "").strip()
                        vwap_state = str(metadata.get("vwap_state") or "").strip()
                        if signal_type or vwap_state:
                            pocket_label = f"{signal_type}|{vwap_state}".strip("|")
                    if pocket_label:
                        pocket_hits[pocket_label] += 1

                    fallback_flag = metadata.get("regime_fallback_allowed")
                    if fallback_flag is None:
                        fallback_flag = _nested_get(metadata, "macd_v2_debug", "regime_fallback_allowed", default=None)
                    if fallback_flag is not None:
                        fallback_attempts += 1
                        if bool(fallback_flag):
                            fallback_allowed += 1
                    if bool(metadata.get("macd_4h_regime_entry", False)):
                        fallback_success += 1

                    gate_reason = str(
                        metadata.get("entry_hard_gate_reason")
                        or _nested_get(metadata, "macd_v2_debug", "entry_hard_gate_reason", default="")
                        or ""
                    ).strip()
                    if gate_reason:
                        entry_hard_gate_failures[_parse_stage_bucket(gate_reason)] += 1

                elif event == "execution":
                    execution_events += 1
                    if operation not in {"buy", "sell"}:
                        continue
                    quality_mode = str(
                        _nested_get(metadata, "pretrade_risk_gate", "execution_quality_1m", "mode", default="")
                        or _nested_get(payload, "context", "trigger_context", "execution_quality_1m", "mode", default="")
                        or "UNKNOWN"
                    ).strip().upper()
                    execution_quality_vs_entries[quality_mode][operation.upper()] += 1

    return {
        "decision_events": decision_events,
        "execution_events": execution_events,
        "reject_codes": reject_codes,
        "primary_blockers": primary_blockers,
        "pocket_hits": pocket_hits,
        "fallback_attempts": fallback_attempts,
        "fallback_allowed": fallback_allowed,
        "fallback_success": fallback_success,
        "entry_hard_gate_failures": entry_hard_gate_failures,
        "execution_quality_vs_entries": execution_quality_vs_entries,
    }


def _summarize_runtime(files: list[Path]) -> dict[str, Any]:
    reject_codes: Counter[str] = Counter()
    extreme_volatility_skips: Counter[str] = Counter()

    for path in files:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            current_symbol = ""
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("[") and "]" in line:
                    current_symbol = line.split("]", 1)[0].lstrip("[").strip().upper()
                if "HOLD归因:" in line and "code=" in line:
                    code = line.split("code=", 1)[1].split(",", 1)[0].strip()
                    if code and code != "-":
                        reject_codes[code] += 1
                if "极端波动冷却中，跳过新开仓" in line:
                    symbol = current_symbol or "UNKNOWN"
                    extreme_volatility_skips[symbol] += 1

    return {
        "runtime_reject_codes": reject_codes,
        "extreme_volatility_skips": extreme_volatility_skips,
    }


def _print_counter(title: str, counter: Counter[str]) -> None:
    print(title)
    if not counter:
        print("  - none")
        return
    for key, value in counter.most_common():
        print(f"  - {key}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize MACD V2 open-chain attribution over log windows.")
    parser.add_argument(
        "--attribution",
        nargs="+",
        required=True,
        help="Attribution jsonl files, date directories, or glob patterns.",
    )
    parser.add_argument(
        "--runtime",
        nargs="*",
        default=[],
        help="Runtime log files, date directories, or glob patterns.",
    )
    args = parser.parse_args()

    attribution_files = _iter_input_files(args.attribution, default_name="fund_flow_attribution.jsonl")
    runtime_files = _iter_input_files(args.runtime, default_name="runtime.out.00.log")

    attribution = _summarize_attribution(attribution_files)
    runtime = _summarize_runtime(runtime_files)

    print("MACD V2 48H Open-Chain Summary")
    print(f"Attribution files: {len(attribution_files)}")
    print(f"Runtime files: {len(runtime_files)}")
    print(f"Decision events: {attribution['decision_events']}")
    print(f"Execution events: {attribution['execution_events']}")
    print(
        "Regime fallback: "
        f"attempts={attribution['fallback_attempts']} "
        f"allowed={attribution['fallback_allowed']} "
        f"success={attribution['fallback_success']}"
    )
    _print_counter("Reject codes", attribution["reject_codes"])
    _print_counter("Primary open blockers", attribution["primary_blockers"])
    _print_counter("Pocket hits", attribution["pocket_hits"])
    _print_counter("Entry hard gate failure buckets", attribution["entry_hard_gate_failures"])
    print("Executed entries x execution_quality_1m.mode")
    if attribution["execution_quality_vs_entries"]:
        for mode, side_counts in sorted(attribution["execution_quality_vs_entries"].items()):
            side_text = ", ".join(f"{side}={count}" for side, count in side_counts.items())
            print(f"  - {mode}: {side_text}")
    else:
        print("  - none")
    _print_counter("Runtime reject codes", runtime["runtime_reject_codes"])
    _print_counter("Extreme volatility skips", runtime["extreme_volatility_skips"])


if __name__ == "__main__":
    main()
