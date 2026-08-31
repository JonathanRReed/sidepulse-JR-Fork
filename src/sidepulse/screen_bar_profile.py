"""Fail-closed, content-free evidence for Screen Bar performance profiles."""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .private_io import atomic_private_write
from .screen_bar_pipeline import (
    MAX_BATCH_FRAMES,
    PresentationMetricKind,
    PresentationMetricsSnapshot,
)

PROFILE_DOCUMENT = "jr-bar-screen-bar-profile"
MATRIX_DOCUMENT = "jr-bar-screen-bar-profile-matrix"
SCHEMA_VERSION = 1
MIN_CAPTURE_SECONDS = 300.0
MIN_STATE_SAMPLES = 30
REQUIRED_SCENARIOS = (
    "static",
    "working",
    "asking",
    "multi-agent",
    "dnd",
    "low-power",
    "hidden",
)
THERMAL_STATES = frozenset({"nominal", "fair", "serious", "critical"})
FOCUS_STATES = frozenset({"active", "inactive", "unknown"})
INSTRUMENT_FIELDS = (
    "measurement_duration_seconds",
    "wakeups_per_second",
    "energy_impact",
    "peak_resident_memory_mb",
    "average_cpu_percent",
    "cpu_time_seconds",
)

_RUNTIME_FIELDS = frozenset(
    {
        "document",
        "schema_version",
        "kind",
        "scenario",
        "duration_seconds",
        "environment",
        "metrics",
        "capture_id",
    }
)
_INSTRUMENTS_FIELDS = frozenset(
    {
        "document",
        "schema_version",
        "kind",
        "scenario",
        "runtime",
        "instruments",
        "trace",
        "capture_id",
    }
)
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_SECRET_PATTERNS = (
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class ProfileEvidenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProfileStateSample:
    observed_at: float
    visible: bool
    display_asleep: bool
    low_power: bool
    thermal: str
    focus_state: str


class ProfileScenarioTracker:
    """Track scenario-defining state without retaining labels or Focus names."""

    def __init__(self, scenario: str) -> None:
        if scenario not in REQUIRED_SCENARIOS:
            raise ProfileEvidenceError("invalid Screen Bar profile scenario")
        self.scenario = scenario
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self._last_at: float | None = None
        self._samples = 0
        self._violations = 0
        self._latest: ProfileStateSample | None = None

    def _matches(self, sample: ProfileStateSample) -> bool:
        if sample.display_asleep:
            return False
        if self.scenario == "hidden":
            return not sample.visible
        if not sample.visible:
            return False
        if self.scenario == "low-power" and not sample.low_power:
            return False
        if self.scenario == "dnd" and sample.focus_state != "active":
            return False
        return True

    def observe(self, sample: ProfileStateSample) -> None:
        observed_at = _require_number(sample.observed_at, "observed_at")
        if sample.thermal not in THERMAL_STATES:
            raise ProfileEvidenceError("invalid thermal state")
        if sample.focus_state not in FOCUS_STATES:
            raise ProfileEvidenceError("invalid Focus state")
        with self._lock:
            if self._last_at is not None and observed_at < self._last_at:
                raise ProfileEvidenceError("profile state samples are not monotonic")
            matches = self._matches(sample)
            if self._started_at is None:
                if not matches:
                    self._latest = sample
                    self._last_at = observed_at
                    return
                self._started_at = observed_at
            self._samples += 1
            if not matches:
                self._violations += 1
            self._latest = sample
            self._last_at = observed_at

    def summary(self) -> dict[str, object]:
        with self._lock:
            started = self._started_at
            latest = self._latest
            ended = self._last_at
            samples = self._samples
            violations = self._violations
        if started is None or latest is None or ended is None:
            raise ProfileEvidenceError("profile scenario never entered its required state")
        return {
            "started_at": started,
            "ended_at": ended,
            "state_samples": samples,
            "state_violations": violations,
            "visible": latest.visible,
            "display_asleep": latest.display_asleep,
            "low_power": latest.low_power,
            "thermal": latest.thermal,
            "focus_state": latest.focus_state,
        }


def _canonical_bytes(document: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProfileEvidenceError("profile is not canonical JSON") from exc


def _document_id(document: Mapping[str, object], field: str) -> str:
    body = {key: value for key, value in document.items() if key != field}
    return hashlib.sha256(_canonical_bytes(body)).hexdigest()


def _require_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileEvidenceError(f"missing numeric profile field: {field}")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ProfileEvidenceError(f"invalid profile field: {field}")
    return number


def _require_integer(value: object, field: str) -> int:
    number = _require_number(value, field)
    if not number.is_integer():
        raise ProfileEvidenceError(f"profile field must be an integer: {field}")
    return int(number)


def _reject_secret_material(value: object) -> None:
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
            raise ProfileEvidenceError("profile contains secret-shaped material")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _reject_secret_material(str(key))
            _reject_secret_material(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_secret_material(item)


def _percentile(values: Sequence[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return int(ordered[index])


def summarize_metrics(snapshot: PresentationMetricsSnapshot) -> dict[str, object]:
    callbacks = snapshot.durations(PresentationMetricKind.DISPLAY_CALLBACK_NS)
    return {
        "callback_ns": {
            "count": len(callbacks),
            "p50": _percentile(callbacks, 0.50),
            "p95": _percentile(callbacks, 0.95),
            "maximum": max(callbacks, default=0),
        },
        "processed_callbacks": snapshot.counter(PresentationMetricKind.PROCESSED_CALLBACK),
        "suppressed_callbacks": snapshot.counter(PresentationMetricKind.SUPPRESSED_CALLBACK),
        "presented_frames": snapshot.counter(PresentationMetricKind.PRESENTED_FRAME),
        "jsc_step_calls": snapshot.counter(PresentationMetricKind.JSC_STEP_CALL),
        "jsc_step_batch_calls": snapshot.counter(PresentationMetricKind.JSC_STEP_BATCH_CALL),
        "batch_successes": snapshot.counter(PresentationMetricKind.JSC_BATCH_SUCCESS),
        "batch_cache_hits": snapshot.counter(PresentationMetricKind.BATCH_CACHE_HIT),
        "batch_fallbacks": snapshot.counter(PresentationMetricKind.BATCH_FALLBACK),
        "batch_invalidations": snapshot.counter(PresentationMetricKind.BATCH_INVALIDATED),
        "batch_truncations": snapshot.counter(PresentationMetricKind.BATCH_TRUNCATED),
    }


def current_focus_state() -> str:
    """Return content-free Focus state without converting unreadable to inactive."""
    try:
        from .focus_sync import FocusSyncUnavailableError, is_focus_active

        return "active" if is_focus_active() else "inactive"
    except FocusSyncUnavailableError:
        return "unknown"


def create_runtime_profile(
    *,
    scenario: str,
    started_at: float,
    ended_at: float,
    metrics: PresentationMetricsSnapshot,
    screen_identity: str,
    panel_refresh_hz: float,
    visible: bool,
    display_asleep: bool,
    low_power: bool,
    thermal: str,
    focus_state: str,
    state_samples: int,
    state_violations: int,
) -> dict[str, object]:
    started = _require_number(started_at, "started_at")
    ended = _require_number(ended_at, "ended_at")
    if ended < started:
        raise ProfileEvidenceError("profile end precedes its start")
    document: dict[str, object] = {
        "document": PROFILE_DOCUMENT,
        "schema_version": SCHEMA_VERSION,
        "kind": "runtime",
        "scenario": str(scenario),
        "duration_seconds": ended - started,
        "environment": {
            "screen_identity": str(screen_identity),
            "panel_refresh_hz": panel_refresh_hz,
            "visible": visible,
            "display_asleep": display_asleep,
            "low_power": low_power,
            "thermal": str(thermal),
            "focus_state": str(focus_state),
            "scenario_samples": state_samples,
            "scenario_violations": state_violations,
        },
        "metrics": summarize_metrics(metrics),
    }
    document["capture_id"] = _document_id(document, "capture_id")
    validate_runtime_profile(document)
    return document


def validate_runtime_profile(document: Mapping[str, object]) -> None:
    if set(document) != _RUNTIME_FIELDS:
        raise ProfileEvidenceError("invalid runtime profile fields")
    if document.get("document") != PROFILE_DOCUMENT:
        raise ProfileEvidenceError("invalid runtime profile document")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ProfileEvidenceError("unsupported runtime profile schema")
    if document.get("kind") != "runtime":
        raise ProfileEvidenceError("invalid runtime profile kind")
    scenario = document.get("scenario")
    if scenario not in REQUIRED_SCENARIOS:
        raise ProfileEvidenceError("invalid Screen Bar profile scenario")
    duration = _require_number(document.get("duration_seconds"), "duration_seconds")
    if duration < MIN_CAPTURE_SECONDS:
        raise ProfileEvidenceError("Screen Bar profile must cover at least 300 seconds")

    environment = document.get("environment")
    if not isinstance(environment, Mapping) or set(environment) != {
        "screen_identity",
        "panel_refresh_hz",
        "visible",
        "display_asleep",
        "low_power",
        "thermal",
        "focus_state",
        "scenario_samples",
        "scenario_violations",
    }:
        raise ProfileEvidenceError("invalid Screen Bar environment")
    screen_identity = environment.get("screen_identity")
    if not isinstance(screen_identity, str) or not screen_identity.strip():
        raise ProfileEvidenceError("missing screen identity")
    if _require_number(environment.get("panel_refresh_hz"), "panel_refresh_hz") <= 0.0:
        raise ProfileEvidenceError("panel_refresh_hz must be positive")
    for field in ("visible", "display_asleep", "low_power"):
        if type(environment.get(field)) is not bool:
            raise ProfileEvidenceError(f"invalid boolean environment field: {field}")
    thermal = environment.get("thermal")
    if thermal not in THERMAL_STATES:
        raise ProfileEvidenceError("invalid thermal state")
    focus_state = environment.get("focus_state")
    if focus_state not in FOCUS_STATES:
        raise ProfileEvidenceError("invalid Focus state")
    if scenario == "hidden" and environment.get("visible") is not False:
        raise ProfileEvidenceError("hidden scenario must observe a hidden Screen Bar")
    if scenario == "low-power" and environment.get("low_power") is not True:
        raise ProfileEvidenceError("low-power scenario must observe low-power mode")
    if scenario == "dnd" and focus_state != "active":
        raise ProfileEvidenceError("DND scenario requires an observed active Focus")
    samples = _require_integer(environment.get("scenario_samples"), "scenario_samples")
    violations = _require_integer(environment.get("scenario_violations"), "scenario_violations")
    if samples < MIN_STATE_SAMPLES:
        raise ProfileEvidenceError("Screen Bar scenario has too few state samples")
    if violations:
        raise ProfileEvidenceError("Screen Bar scenario state changed during capture")

    metrics = document.get("metrics")
    expected_metric_fields = {
        "callback_ns",
        "processed_callbacks",
        "suppressed_callbacks",
        "presented_frames",
        "jsc_step_calls",
        "jsc_step_batch_calls",
        "batch_successes",
        "batch_cache_hits",
        "batch_fallbacks",
        "batch_invalidations",
        "batch_truncations",
    }
    if not isinstance(metrics, Mapping) or set(metrics) != expected_metric_fields:
        raise ProfileEvidenceError("invalid Screen Bar metrics")
    callback = metrics.get("callback_ns")
    if not isinstance(callback, Mapping) or set(callback) != {
        "count",
        "p50",
        "p95",
        "maximum",
    }:
        raise ProfileEvidenceError("invalid callback metrics")
    for field in ("count", "p50", "p95", "maximum"):
        _require_integer(callback.get(field), f"callback_ns.{field}")
    counters = {
        field: _require_integer(metrics.get(field), field) for field in expected_metric_fields if field != "callback_ns"
    }
    attempts = counters["jsc_step_batch_calls"]
    successes = counters["batch_successes"]
    fallbacks = counters["batch_fallbacks"]
    if successes > attempts:
        raise ProfileEvidenceError("batch successes exceed batch attempts")
    if successes + fallbacks != attempts:
        raise ProfileEvidenceError("batch attempts do not resolve to success or fallback")
    if counters["batch_truncations"] > attempts:
        raise ProfileEvidenceError("batch truncations exceed batch attempts")
    if counters["batch_invalidations"] > successes:
        raise ProfileEvidenceError("batch invalidations exceed successful batches")
    if counters["batch_cache_hits"] > successes * (MAX_BATCH_FRAMES - 1):
        raise ProfileEvidenceError("batch cache hits exceed prefetched frames")
    if counters["presented_frames"] > counters["processed_callbacks"]:
        raise ProfileEvidenceError("presented frames exceed processed callbacks")
    if scenario == "hidden" and counters["presented_frames"] != 0:
        raise ProfileEvidenceError("hidden scenario presented a Screen Bar frame")

    capture_id = document.get("capture_id")
    if not isinstance(capture_id, str) or not _HEX_64.fullmatch(capture_id):
        raise ProfileEvidenceError("invalid runtime capture id")
    if capture_id != _document_id(document, "capture_id"):
        raise ProfileEvidenceError("runtime capture id does not match its content")
    _reject_secret_material(document)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trace_record(root: Path, trace: Path) -> dict[str, object]:
    root_path = root.expanduser()
    root_info = root_path.lstat()
    if stat.S_ISLNK(root_info.st_mode):
        raise ProfileEvidenceError("profile root must not be a symlink")
    if not stat.S_ISDIR(root_info.st_mode):
        raise ProfileEvidenceError("profile root must be a directory")
    release_root = root_path.resolve(strict=True)
    source = trace.expanduser()
    info = source.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ProfileEvidenceError("Instruments trace must be a regular file")
    resolved = source.resolve(strict=True)
    if not resolved.is_relative_to(release_root):
        raise ProfileEvidenceError("Instruments trace is outside the profile root")
    return {
        "path": resolved.relative_to(release_root).as_posix(),
        "bytes": info.st_size,
        "sha256": _sha256_file(resolved),
    }


def create_instruments_profile(
    *,
    root: Path,
    runtime: Mapping[str, object],
    instruments: Mapping[str, object],
    trace: Path,
) -> dict[str, object]:
    validate_runtime_profile(runtime)
    normalized = {field: _require_number(instruments.get(field), field) for field in INSTRUMENT_FIELDS}
    runtime_duration = _require_number(runtime.get("duration_seconds"), "duration_seconds")
    if normalized["measurement_duration_seconds"] + 5.0 < runtime_duration:
        raise ProfileEvidenceError("Instruments duration does not cover runtime capture")
    document: dict[str, object] = {
        "document": PROFILE_DOCUMENT,
        "schema_version": SCHEMA_VERSION,
        "kind": "instruments",
        "scenario": runtime["scenario"],
        "runtime": dict(runtime),
        "instruments": normalized,
        "trace": _trace_record(root, trace),
    }
    document["capture_id"] = _document_id(document, "capture_id")
    validate_instruments_profile(document, root=root)
    return document


def validate_instruments_profile(
    document: Mapping[str, object],
    *,
    root: Path,
) -> None:
    if set(document) != _INSTRUMENTS_FIELDS:
        raise ProfileEvidenceError("invalid Instruments profile fields")
    if document.get("document") != PROFILE_DOCUMENT:
        raise ProfileEvidenceError("invalid Instruments profile document")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ProfileEvidenceError("unsupported Instruments profile schema")
    if document.get("kind") != "instruments":
        raise ProfileEvidenceError("invalid Instruments profile kind")
    runtime = document.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ProfileEvidenceError("missing runtime profile")
    validate_runtime_profile(runtime)
    if document.get("scenario") != runtime.get("scenario"):
        raise ProfileEvidenceError("Instruments scenario does not match runtime")
    instruments = document.get("instruments")
    if not isinstance(instruments, Mapping) or set(instruments) != set(INSTRUMENT_FIELDS):
        raise ProfileEvidenceError("invalid Instruments metrics")
    normalized = {field: _require_number(instruments.get(field), field) for field in INSTRUMENT_FIELDS}
    runtime_duration = _require_number(runtime.get("duration_seconds"), "duration_seconds")
    if normalized["measurement_duration_seconds"] + 5.0 < runtime_duration:
        raise ProfileEvidenceError("Instruments duration does not cover runtime capture")
    trace = document.get("trace")
    if not isinstance(trace, Mapping) or set(trace) != {"path", "bytes", "sha256"}:
        raise ProfileEvidenceError("invalid Instruments trace record")
    path = trace.get("path")
    if not isinstance(path, str) or not path:
        raise ProfileEvidenceError("missing Instruments trace path")
    current = _trace_record(root, root / path)
    if current != dict(trace):
        raise ProfileEvidenceError("Instruments trace no longer matches its receipt")
    capture_id = document.get("capture_id")
    if not isinstance(capture_id, str) or not _HEX_64.fullmatch(capture_id):
        raise ProfileEvidenceError("invalid Instruments capture id")
    if capture_id != _document_id(document, "capture_id"):
        raise ProfileEvidenceError("Instruments capture id does not match its content")
    _reject_secret_material(document)


def build_profile_matrix(
    *,
    root: Path,
    profiles: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    by_scenario: dict[str, Mapping[str, object]] = {}
    for profile in profiles:
        validate_instruments_profile(profile, root=root)
        scenario = profile.get("scenario")
        assert isinstance(scenario, str)
        if scenario in by_scenario:
            raise ProfileEvidenceError(f"duplicate Screen Bar scenario: {scenario}")
        by_scenario[scenario] = profile
    missing = [scenario for scenario in REQUIRED_SCENARIOS if scenario not in by_scenario]
    unknown = sorted(set(by_scenario) - set(REQUIRED_SCENARIOS))
    if missing:
        raise ProfileEvidenceError("missing Screen Bar scenarios: " + ", ".join(missing))
    if unknown:
        raise ProfileEvidenceError("unknown Screen Bar scenarios: " + ", ".join(unknown))
    document: dict[str, object] = {
        "document": MATRIX_DOCUMENT,
        "schema_version": SCHEMA_VERSION,
        "profiles": [dict(by_scenario[scenario]) for scenario in REQUIRED_SCENARIOS],
    }
    document["matrix_id"] = _document_id(document, "matrix_id")
    _reject_secret_material(document)
    return document


def validate_profile_matrix(
    document: Mapping[str, object],
    *,
    root: Path,
) -> None:
    if set(document) != {"document", "schema_version", "profiles", "matrix_id"}:
        raise ProfileEvidenceError("invalid Screen Bar profile matrix fields")
    if document.get("document") != MATRIX_DOCUMENT:
        raise ProfileEvidenceError("invalid Screen Bar profile matrix document")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ProfileEvidenceError("unsupported Screen Bar profile matrix schema")
    profiles = document.get("profiles")
    if not isinstance(profiles, list) or not all(isinstance(profile, Mapping) for profile in profiles):
        raise ProfileEvidenceError("invalid Screen Bar profile matrix entries")
    rebuilt = build_profile_matrix(root=root, profiles=profiles)
    if rebuilt != dict(document):
        raise ProfileEvidenceError("Screen Bar profile matrix id does not match its content")


def write_json(path: Path, document: Mapping[str, object]) -> None:
    payload = json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
    atomic_private_write(path, payload)
