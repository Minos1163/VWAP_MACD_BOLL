from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import DefaultDict, Dict, List, Optional


@dataclass
class FunnelLayer:
    name: str
    passed: int = 0
    blocked: int = 0
    block_reasons: DefaultDict[str, int] = field(default_factory=lambda: defaultdict(int))
    block_score_samples: List[float] = field(default_factory=list)
    sample_limit: int = 100

    def log(self, passed: bool, reason: str = "", score: Optional[float] = None) -> None:
        if passed:
            self.passed += 1
            return
        self.blocked += 1
        reason_key = str(reason or "unspecified")
        self.block_reasons[reason_key] += 1
        if score is not None and len(self.block_score_samples) < self.sample_limit:
            self.block_score_samples.append(round(float(score), 4))

    @property
    def pass_rate(self) -> float:
        total = self.passed + self.blocked
        return (self.passed / total) if total > 0 else 0.0

    def to_dict(self) -> Dict[str, object]:
        top_reasons = dict(sorted(self.block_reasons.items(), key=lambda item: (-item[1], item[0]))[:5])
        score_p50 = round(float(median(self.block_score_samples)), 4) if self.block_score_samples else None
        score_max = max(self.block_score_samples) if self.block_score_samples else None
        return {
            "passed": self.passed,
            "blocked": self.blocked,
            "pass_rate": round(self.pass_rate, 6),
            "top_reasons": top_reasons,
            "score_p50": score_p50,
            "score_max": score_max,
        }


class SignalFunnelLogger:
    DEFAULT_LAYERS = [
        "0_raw_signal",
        "1_score_threshold",
        "2_vwap_threshold",
        "3_4h_preflip_shrink",
        "4_pocket_entry_override",
        "5_pre_ai_candidate_filter",
        "6_L1_structural",
        "7_L2_flow",
        "8_L3_micro",
        "9_pretrade_gate",
        "10_ai_review",
        "11_capacity",
        "12_final_fill",
    ]

    def __init__(self, layers: Optional[List[str]] = None):
        layer_names = list(layers or self.DEFAULT_LAYERS)
        self.layers: Dict[str, FunnelLayer] = {name: FunnelLayer(name=name) for name in layer_names}

    def log(self, layer: str, passed: bool, reason: str = "", score: Optional[float] = None) -> None:
        if layer not in self.layers:
            self.layers[layer] = FunnelLayer(name=layer)
        self.layers[layer].log(passed=passed, reason=reason, score=score)

    def report(self, output_path: Optional[str] = None) -> Dict[str, Dict[str, object]]:
        result = {name: layer.to_dict() for name, layer in self.layers.items()}
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def print_summary(self) -> None:
        print(f"\n{'Layer':<28} {'Passed':>8} {'Blocked':>8} {'PassRate':>10}")
        print("-" * 60)
        for name, layer in self.layers.items():
            print(f"{name:<28} {layer.passed:>8} {layer.blocked:>8} {layer.pass_rate:>9.1%}")
