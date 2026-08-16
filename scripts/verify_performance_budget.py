#!/usr/bin/env python3
"""Validate measured SidePulse performance evidence against release budgets."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class PerformanceBudget:
    warm_launch_ms: float = 500.0
    menu_open_p95_ms: float = 50.0
    pane_switch_p95_ms: float = 100.0
    longest_main_thread_task_ms: float = 16.0
    idle_cpu_hidden_percent: float = 1.0
    idle_cpu_static_bar_percent: float = 1.5
    idle_cpu_motion_percent: float = 3.0


_REQUIRED_FIELDS = tuple(PerformanceBudget.__dataclass_fields__)


def _number(document: Mapping[str, object], key: str) -> float:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"missing numeric performance field: {key}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"invalid performance field: {key}")
    return number


def validate_performance_evidence(
    document: Mapping[str, object],
    budget: PerformanceBudget = PerformanceBudget(),
) -> tuple[str, ...]:
    failures = []
    for key in _REQUIRED_FIELDS:
        measured = _number(document, key)
        limit = float(getattr(budget, key))
        if measured > limit:
            failures.append(f"{key}: measured {measured:g}, budget {limit:g}")
    duration = _number(document, "measurement_duration_seconds")
    if duration < 300.0:
        failures.append(
            "measurement_duration_seconds: at least 300 seconds is required"
        )
    if document.get("instruments_trace_reviewed") is not True:
        failures.append("instruments_trace_reviewed must be true")
    if document.get("menu_tracking_io_observed") is not False:
        failures.append("menu_tracking_io_observed must be false")
    return tuple(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    try:
        document = json.loads(args.evidence.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("performance evidence must be a JSON object")
        failures = validate_performance_evidence(document)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"performance evidence rejected: {exc}")
        return 2
    if failures:
        print("performance budget failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("performance budget passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
