from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import screen_bar_profile_evidence
from sidepulse.screen_bar_pipeline import (
    PresentationMetricKind,
    PresentationMetrics,
)
from sidepulse.screen_bar_profile import (
    REQUIRED_SCENARIOS,
    ProfileEvidenceError,
    ProfileScenarioTracker,
    ProfileStateSample,
    build_profile_matrix,
    create_instruments_profile,
    create_runtime_profile,
    validate_profile_matrix,
    validate_runtime_profile,
)


def _runtime_profile(
    scenario: str = "static",
    *,
    visible: bool = True,
    low_power: bool = False,
    focus_state: str = "inactive",
) -> dict[str, object]:
    metrics = PresentationMetrics()
    metrics.record_duration(PresentationMetricKind.DISPLAY_CALLBACK_NS, 200_000)
    metrics.increment(PresentationMetricKind.JSC_STEP_BATCH_CALL)
    metrics.increment(PresentationMetricKind.JSC_BATCH_SUCCESS)
    metrics.increment(PresentationMetricKind.BATCH_INVALIDATED)
    metrics.increment(PresentationMetricKind.BATCH_TRUNCATED)
    metrics.increment(PresentationMetricKind.PROCESSED_CALLBACK)
    metrics.increment(PresentationMetricKind.PRESENTED_FRAME)
    if not visible:
        metrics = PresentationMetrics()
        metrics.increment(PresentationMetricKind.SUPPRESSED_CALLBACK)
    return create_runtime_profile(
        scenario=scenario,
        started_at=100.0,
        ended_at=400.0,
        metrics=metrics.snapshot(),
        screen_identity="built-in:1",
        panel_refresh_hz=60.0,
        visible=visible,
        display_asleep=False,
        low_power=low_power,
        thermal="nominal",
        focus_state=focus_state,
        state_samples=60,
        state_violations=0,
    )


def _instruments_metrics() -> dict[str, float]:
    return {
        "measurement_duration_seconds": 300.0,
        "wakeups_per_second": 1.5,
        "energy_impact": 2.0,
        "peak_resident_memory_mb": 80.0,
        "average_cpu_percent": 1.0,
        "cpu_time_seconds": 3.0,
    }


def _complete_profile(tmp_path: Path, scenario: str) -> dict[str, object]:
    trace = tmp_path / f"{scenario}.trace"
    trace.write_bytes(f"trace:{scenario}".encode())
    runtime = _runtime_profile(
        scenario,
        visible=scenario != "hidden",
        low_power=scenario == "low-power",
        focus_state="active" if scenario == "dnd" else "inactive",
    )
    return create_instruments_profile(
        root=tmp_path,
        runtime=runtime,
        instruments=_instruments_metrics(),
        trace=trace,
    )


def test_runtime_profile_summarizes_existing_content_free_metrics() -> None:
    profile = _runtime_profile()

    assert profile["document"] == "jr-bar-screen-bar-profile"
    assert profile["kind"] == "runtime"
    assert profile["metrics"]["callback_ns"]["count"] == 1
    assert profile["metrics"]["jsc_step_batch_calls"] == 1
    assert profile["metrics"]["batch_successes"] == 1
    assert profile["metrics"]["batch_invalidations"] == 1
    assert profile["metrics"]["batch_truncations"] == 1
    assert profile["metrics"]["presented_frames"] == 1
    assert isinstance(profile["capture_id"], str)


def test_runtime_export_is_explicit_and_writes_one_private_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidepulse import virtual_device

    output = tmp_path / "profile.json"
    monkeypatch.setenv("SIDEPULSE_SCREEN_BAR_PROFILE_OUTPUT", str(output))
    monkeypatch.setenv("SIDEPULSE_SCREEN_BAR_PROFILE_SCENARIO", "static")
    device = virtual_device.VirtualStatusDevice.alloc().init()
    device._profile_stop.set()
    device._profile_wake.set()
    device._profile_thread.join(2.0)
    device._panel_refresh_hz = lambda: 60.0
    device.view = SimpleNamespace(render_screen_identity="built-in:1")
    ended_at = time.monotonic()
    summary = {
        "started_at": ended_at - 300.0,
        "ended_at": ended_at,
        "state_samples": 60,
        "state_violations": 0,
        "visible": True,
        "display_asleep": False,
        "low_power": False,
        "thermal": "nominal",
        "focus_state": "inactive",
    }

    assert device._write_profile_if_requested(summary) == output

    document = json.loads(output.read_text(encoding="utf-8"))
    validate_runtime_profile(document)
    assert document["scenario"] == "static"
    assert output.stat().st_mode & 0o777 == 0o600


def test_runtime_export_does_nothing_without_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidepulse import virtual_device

    monkeypatch.delenv("SIDEPULSE_SCREEN_BAR_PROFILE_OUTPUT", raising=False)
    monkeypatch.delenv("SIDEPULSE_SCREEN_BAR_PROFILE_SCENARIO", raising=False)
    device = virtual_device.VirtualStatusDevice.alloc().init()

    assert device._profile_tracker is None
    assert device._write_profile_if_requested() is None


def test_termination_quiesces_frame_and_sampler_work_before_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidepulse import virtual_device

    monkeypatch.delenv("SIDEPULSE_SCREEN_BAR_PROFILE_OUTPUT", raising=False)
    device = virtual_device.VirtualStatusDevice.alloc().init()
    events: list[str] = []
    device._invalidate_frame_driver = lambda: events.append("driver-stopped")
    device._stop_sampler = lambda: events.append("sampler-stopped")
    device._stop_alcove_observer = lambda: events.append("alcove-stopped")

    def finish_profile():
        events.append("metrics-snapshotted")
        return None

    device._finish_profile_monitor = finish_profile
    device._write_profile_if_requested = lambda _summary: events.append("profile-written")

    device.terminate()

    assert events[:5] == [
        "driver-stopped",
        "sampler-stopped",
        "alcove-stopped",
        "metrics-snapshotted",
        "profile-written",
    ]


def test_runtime_profile_rejects_impossible_counter_relationships() -> None:
    profile = _runtime_profile()
    profile["metrics"]["batch_successes"] = 2

    with pytest.raises(ProfileEvidenceError, match="batch successes"):
        validate_runtime_profile(profile)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("batch_cache_hits", 24, "cache hits"),
        ("batch_invalidations", 2, "invalidations"),
        ("batch_truncations", 2, "truncations"),
    ),
)
def test_runtime_profile_rejects_impossible_prefetch_counters(
    field: str,
    value: int,
    message: str,
) -> None:
    profile = _runtime_profile()
    profile["metrics"][field] = value

    with pytest.raises(ProfileEvidenceError, match=message):
        validate_runtime_profile(profile)


@pytest.mark.parametrize("value", (-1.0, float("inf"), float("nan")))
def test_instruments_profile_rejects_invalid_numeric_evidence(
    tmp_path: Path,
    value: float,
) -> None:
    trace = tmp_path / "static.trace"
    trace.write_bytes(b"trace")
    instruments = _instruments_metrics()
    instruments["energy_impact"] = value

    with pytest.raises(ProfileEvidenceError, match="energy_impact"):
        create_instruments_profile(
            root=tmp_path,
            runtime=_runtime_profile(),
            instruments=instruments,
            trace=trace,
        )


def test_runtime_profile_cannot_pose_as_instruments_evidence() -> None:
    profile = _runtime_profile()
    profile["instruments"] = _instruments_metrics()

    with pytest.raises(ProfileEvidenceError, match="runtime profile fields"):
        validate_runtime_profile(profile)


def test_dnd_profile_requires_observed_focus_state() -> None:
    with pytest.raises(ProfileEvidenceError, match="active Focus"):
        _runtime_profile("dnd", focus_state="unknown")


def test_profile_rejects_secret_shaped_text() -> None:
    with pytest.raises(ProfileEvidenceError, match="secret"):
        metrics = PresentationMetrics().snapshot()
        create_runtime_profile(
            scenario="static",
            started_at=100.0,
            ended_at=400.0,
            metrics=metrics,
            screen_identity="ghp_" + "A" * 36,
            panel_refresh_hz=60.0,
            visible=True,
            display_asleep=False,
            low_power=False,
            thermal="nominal",
            focus_state="inactive",
            state_samples=60,
            state_violations=0,
        )


def test_profile_tracker_rejects_a_late_focus_toggle() -> None:
    tracker = ProfileScenarioTracker("dnd")
    tracker.observe(ProfileStateSample(0.0, True, False, False, "nominal", "inactive"))
    for observed_at in range(100, 401, 5):
        tracker.observe(
            ProfileStateSample(
                float(observed_at),
                True,
                False,
                False,
                "nominal",
                "active",
            )
        )
    tracker.observe(ProfileStateSample(405.0, True, False, False, "nominal", "inactive"))
    tracker.observe(ProfileStateSample(410.0, True, False, False, "nominal", "active"))
    summary = tracker.summary()

    with pytest.raises(ProfileEvidenceError, match="state changed"):
        create_runtime_profile(
            scenario="dnd",
            started_at=float(summary["started_at"]),
            ended_at=float(summary["ended_at"]),
            metrics=PresentationMetrics().snapshot(),
            screen_identity="built-in:1",
            panel_refresh_hz=60.0,
            visible=bool(summary["visible"]),
            display_asleep=bool(summary["display_asleep"]),
            low_power=bool(summary["low_power"]),
            thermal=str(summary["thermal"]),
            focus_state=str(summary["focus_state"]),
            state_samples=int(summary["state_samples"]),
            state_violations=int(summary["state_violations"]),
        )


def test_instruments_profile_rejects_a_symlinked_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    trace = real_root / "static.trace"
    trace.write_bytes(b"trace")
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ProfileEvidenceError, match="root must not be a symlink"):
        create_instruments_profile(
            root=linked_root,
            runtime=_runtime_profile(),
            instruments=_instruments_metrics(),
            trace=linked_root / "static.trace",
        )


def test_matrix_rejects_every_missing_required_scenario(tmp_path: Path) -> None:
    profiles = [_complete_profile(tmp_path, scenario) for scenario in REQUIRED_SCENARIOS]

    for missing in REQUIRED_SCENARIOS:
        with pytest.raises(ProfileEvidenceError, match=missing):
            build_profile_matrix(
                root=tmp_path,
                profiles=[profile for profile in profiles if profile["scenario"] != missing],
            )


def test_matrix_rehashes_and_rejects_a_substituted_trace(tmp_path: Path) -> None:
    profiles = [_complete_profile(tmp_path, scenario) for scenario in REQUIRED_SCENARIOS]
    trace = tmp_path / "asking.trace"
    trace.write_bytes(b"substituted")

    with pytest.raises(ProfileEvidenceError, match="trace"):
        build_profile_matrix(root=tmp_path, profiles=profiles)


def test_complete_matrix_contains_each_scenario_once(tmp_path: Path) -> None:
    profiles = [_complete_profile(tmp_path, scenario) for scenario in REQUIRED_SCENARIOS]

    matrix = build_profile_matrix(root=tmp_path, profiles=profiles)

    assert matrix["document"] == "jr-bar-screen-bar-profile-matrix"
    assert [profile["scenario"] for profile in matrix["profiles"]] == list(REQUIRED_SCENARIOS)
    assert isinstance(matrix["matrix_id"], str)
    validate_profile_matrix(matrix, root=tmp_path)


def test_matrix_rejects_a_tampered_runtime_capture(tmp_path: Path) -> None:
    profiles = [_complete_profile(tmp_path, scenario) for scenario in REQUIRED_SCENARIOS]
    tampered = copy.deepcopy(profiles)
    tampered[0]["runtime"]["environment"]["thermal"] = "critical"

    with pytest.raises(ProfileEvidenceError, match="capture id"):
        build_profile_matrix(root=tmp_path, profiles=tampered)


def test_cli_finalizes_runtime_and_external_trace_evidence(tmp_path: Path) -> None:
    runtime_path = tmp_path / "runtime.json"
    instruments_path = tmp_path / "instruments.json"
    trace_path = tmp_path / "static.trace"
    output_path = tmp_path / "complete.json"
    runtime_path.write_text(json.dumps(_runtime_profile()), encoding="utf-8")
    instruments_path.write_text(json.dumps(_instruments_metrics()), encoding="utf-8")
    trace_path.write_bytes(b"trace")

    result = screen_bar_profile_evidence.main(
        [
            "finalize",
            "--root",
            str(tmp_path),
            "--runtime",
            str(runtime_path),
            "--instruments",
            str(instruments_path),
            "--trace",
            str(trace_path),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    document = json.loads(output_path.read_text(encoding="utf-8"))
    assert document["kind"] == "instruments"
    assert document["trace"]["sha256"]
