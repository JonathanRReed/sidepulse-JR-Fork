#!/usr/bin/env python3
"""Measure JR-Bar hook admission and synchronous fallback on this source tree."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[1]
SRC: Final = ROOT / "src"
MINIMUM_SAMPLES: Final = 50
DEFAULT_SAMPLES: Final = 100
CHILD_TIMEOUT_SECONDS: Final = 10.0

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sidepulse.hook_ingress import HookIngressService  # noqa: E402


def _sample_count(value: str) -> int:
    try:
        samples = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("samples must be an integer") from exc
    if samples < MINIMUM_SAMPLES:
        raise argparse.ArgumentTypeError(
            f"samples must be at least {MINIMUM_SAMPLES}"
        )
    return samples


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeError):
        return 0


def _child_environment(root: Path) -> dict[str, str]:
    home = root / "home"
    state = root / "state"
    home.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    existing_python_path = os.environ.get("PYTHONPATH")
    python_path = str(SRC)
    if existing_python_path:
        python_path = os.pathsep.join((python_path, existing_python_path))
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(home),
            "PYTHONPATH": python_path,
            "XDG_STATE_HOME": str(state),
        }
    )
    return environment


def _invoke_sample(
    *,
    index: int,
    log_path: Path,
    environment: dict[str, str],
) -> tuple[float, bool]:
    document = json.dumps(
        {
            "hook_event_name": "SessionStart",
            "session_id": f"hook-benchmark-{index}",
            "sequence": index,
        },
        separators=(",", ":"),
    )
    command = [
        sys.executable,
        "-m",
        "sidepulse.hook_client",
        "--provider",
        "claude",
        "--log",
        str(log_path),
    ]
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            input=document,
            capture_output=True,
            text=True,
            timeout=CHILD_TIMEOUT_SECONDS,
            check=False,
            env=environment,
        )
        succeeded = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        succeeded = False
    return (time.perf_counter() - started, succeeded)


def _timing_fields(durations: list[float]) -> dict[str, float]:
    return {
        "median_ms": round(statistics.median(durations) * 1000.0, 3),
        "p95_ms": round(_p95(durations) * 1000.0, 3),
    }


def _run_mode(mode: str, samples: int, root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    environment = _child_environment(root)
    state_dir = Path(environment["XDG_STATE_HOME"]) / "sidepulse" / "agent-monitor"
    state_dir.mkdir(parents=True, mode=0o700)
    state_dir.chmod(0o700)
    log_path = state_dir / f"benchmark-{mode}.jsonl"
    socket_path = state_dir / "hook-ingress.sock"
    service: HookIngressService | None = None
    if mode == "server-up":
        service = HookIngressService(
            process=lambda _request: None,
            socket_path=socket_path,
            rejection_path=state_dir / "benchmark-rejections.jsonl",
        )
        service.start()

    durations: list[float] = []
    child_failures = 0
    try:
        for index in range(samples):
            duration, succeeded = _invoke_sample(
                index=index,
                log_path=log_path,
                environment=environment,
            )
            durations.append(duration)
            if not succeeded:
                child_failures += 1
    finally:
        if service is not None:
            service.close(timeout_seconds=10.0)

    fallback = _line_count(log_path)
    if service is None:
        accepted = 0
        refused = 0
        failed = max(0, samples - fallback)
    else:
        snapshot = service.snapshot()
        accepted = snapshot.accepted
        refused = (
            snapshot.refused_full
            + snapshot.refused_closed
            + snapshot.refused_invalid
        )
        failed = snapshot.failed + snapshot.shutdown_timeout + child_failures

    return {
        "server": "up" if service is not None else "down",
        "sample_count": samples,
        **_timing_fields(durations),
        "accepted": accepted,
        "refused": refused,
        "failed": failed,
        "fallback": fallback,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure source-tree hook admission and fallback latency without "
            "retaining event content."
        )
    )
    parser.add_argument(
        "--samples",
        type=_sample_count,
        default=DEFAULT_SAMPLES,
        help=f"samples per mode, at least {MINIMUM_SAMPLES} (default: {DEFAULT_SAMPLES})",
    )
    parser.add_argument(
        "--mode",
        choices=("both", "server-up", "server-down"),
        default="both",
        help="measure admission, fallback, or both (default: both)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selected = (
        ("server-up", "server-down")
        if args.mode == "both"
        else (args.mode,)
    )
    with TemporaryDirectory(prefix="jrbar-hook-benchmark-", dir="/tmp") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        modes = [
            _run_mode(mode, args.samples, root / mode)
            for mode in selected
        ]
    report = {
        "version": 1,
        "evidence_scope": "current_source_machine_only",
        "modes": modes,
    }
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
