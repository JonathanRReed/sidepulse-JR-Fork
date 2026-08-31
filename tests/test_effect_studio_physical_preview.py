from __future__ import annotations

from pathlib import Path

from sidepulse import status_bar_legacy as legacy
from sidepulse.effect_registry import EFFECT_REGISTRY
from sidepulse.effect_studio import MAX_PHYSICAL_PREVIEW_SECONDS, SyntheticScenario
from sidepulse.effect_studio_physical_preview import (
    EffectStudioPhysicalPreviewAdapter,
    PreviewReleaseReason,
    compile_effect_studio_physical_preview,
)
from sidepulse.presentation_compiler import compile_presentation_program
from sidepulse.runtime_scheduler import SubmissionDisposition


class _Worker:
    def __init__(self) -> None:
        self.commands = []
        self.discarded = []
        self.discarded_prefixes = []
        self.pending = {"device-preview-test:latest": "live-status"}
        self.submit_result = None

    def submit(self, command):
        self.commands.append(command)
        if self.submit_result is not SubmissionDisposition.REFUSED:
            self.pending[command.coalesce_key] = command
        return self.submit_result

    def discard_pending(self, key: str) -> None:
        self.discarded.append(key)
        self.pending.pop(key, None)

    def discard_pending_prefix(self, prefix: str) -> None:
        self.discarded_prefixes.append(prefix)
        for key in tuple(self.pending):
            if key.startswith(prefix):
                del self.pending[key]


class _LedController:
    brightness = 128


class _Owner:
    def __init__(self, device: legacy.StatusBarDevice) -> None:
        self.device = device
        self.leds_enabled = True
        self._hardware_write_active = True
        self._hardware_write_generation = 7
        self._hardware_write_worker = _Worker()
        self.scheduled = []
        self.cancelled = 0
        self.restored = []

    def status_bar_devices(self, *, remember: bool = False):
        assert remember is False
        return [self.device]

    def agent_controller_for_device(self, device):
        assert device is self.device
        return _LedController()

    def _hardware_worker_key(self, device) -> str:
        assert device is self.device
        return "device-preview-test"

    def _runtime_worker_monotonic(self) -> float:
        return 100.0

    def _schedule_effect_studio_preview_timeout(
        self,
        session_id: str,
        duration_seconds: float,
    ) -> None:
        self.scheduled.append((session_id, duration_seconds))

    def _cancel_effect_studio_preview_timeout(self) -> None:
        self.cancelled += 1

    def _restore_effect_studio_physical_output(self, device_id: str) -> None:
        self.restored.append(device_id)


def _device(tmp_path: Path, *, name: str = "SidePulse Pro") -> legacy.StatusBarDevice:
    root = tmp_path / name
    return legacy.StatusBarDevice(
        device_id=f"sidepulse:test:{name.casefold().replace(' ', '-')}",
        name=name,
        root=root,
        target=root / "LEDS.LED",
        connected=True,
        display=legacy.LED_DISPLAY_AGENT,
    )


def test_compile_preview_honors_reduce_motion_and_the_existing_safety_compiler() -> None:
    animated = compile_effect_studio_physical_preview(
        "pulse",
        led_count=8,
        scenario=SyntheticScenario.ONE_AGENT,
        reduce_motion=False,
        registry=EFFECT_REGISTRY,
    )
    reduced = compile_effect_studio_physical_preview(
        "pulse",
        led_count=8,
        scenario=SyntheticScenario.ONE_AGENT,
        reduce_motion=True,
        registry=EFFECT_REGISTRY,
    )

    assert animated.rendered_effect_id == "pulse"
    assert animated.animated is True
    assert compile_presentation_program(animated.program, led_count=8).accepted
    assert reduced.rendered_effect_id == "none"
    assert reduced.animated is False
    assert "repeat" not in reduced.program
    assert compile_presentation_program(reduced.program, led_count=8).accepted


def test_reduce_motion_never_leaves_an_animated_fallback_on_hardware() -> None:
    reduced_alert = compile_effect_studio_physical_preview(
        "alert",
        led_count=2,
        scenario=SyntheticScenario.ASKING,
        reduce_motion=True,
        registry=EFFECT_REGISTRY,
    )

    assert reduced_alert.rendered_effect_id == "pulse"
    assert reduced_alert.animated is False
    assert "repeat" not in reduced_alert.program
    assert " pulse" not in reduced_alert.program
    assert "#FF3A00" in reduced_alert.program
    assert reduced_alert.state is legacy.LedDisplayState.ASK


def test_adapter_submits_one_bounded_explicit_request_to_the_existing_writer(
    tmp_path,
) -> None:
    owner = _Owner(_device(tmp_path))
    adapter = EffectStudioPhysicalPreviewAdapter(owner)
    option = adapter.devices()[0]

    receipt = adapter.start(
        effect_id="pulse",
        preview_device_id=option.preview_device_id,
        consent_granted=True,
        duration_seconds=MAX_PHYSICAL_PREVIEW_SECONDS,
        reduce_motion=False,
        scenario=SyntheticScenario.ONE_AGENT,
        registry=EFFECT_REGISTRY,
    )

    assert receipt.accepted is True
    assert receipt.status_label == "Previewing, not saved"
    assert receipt.duration_seconds == MAX_PHYSICAL_PREVIEW_SECONDS
    assert len(owner._hardware_write_worker.commands) == 1
    command = owner._hardware_write_worker.commands[0]
    request = command.payload
    assert request.device is owner.device
    assert request.display_kind == legacy.LED_DISPLAY_TEST
    assert request.write_priority is legacy.RuntimeWorkPriority.EXPLICIT
    assert request.coalesce_identity == "preview-effect-studio"
    assert request.preview_session_id == receipt.session_id
    assert request.override_program.startswith("brightness 128\n")
    assert compile_presentation_program(request.override_program, led_count=8).accepted
    assert owner.scheduled == [
        (receipt.session_id, MAX_PHYSICAL_PREVIEW_SECONDS)
    ]


def test_adapter_refuses_missing_consent_unavailable_hardware_and_unsupported_dot(
    tmp_path,
) -> None:
    owner = _Owner(_device(tmp_path, name="SidePulse Dot"))
    adapter = EffectStudioPhysicalPreviewAdapter(owner)
    option = adapter.devices()[0]

    denied = adapter.start(
        effect_id="pulse",
        preview_device_id=option.preview_device_id,
        consent_granted=False,
        duration_seconds=10.0,
        reduce_motion=False,
        scenario=SyntheticScenario.ONE_AGENT,
        registry=EFFECT_REGISTRY,
    )
    unsupported = adapter.availability(
        "rainbow",
        option.preview_device_id,
        reduce_motion=False,
        registry=EFFECT_REGISTRY,
    )
    owner._hardware_write_active = False
    unavailable = adapter.start(
        effect_id="pulse",
        preview_device_id=option.preview_device_id,
        consent_granted=True,
        duration_seconds=10.0,
        reduce_motion=False,
        scenario=SyntheticScenario.ONE_AGENT,
        registry=EFFECT_REGISTRY,
    )

    assert denied.accepted is False
    assert denied.status_label == "Physical preview requires consent"
    assert unsupported.available is False
    assert "not supported" in unsupported.reason
    assert unavailable.accepted is False
    assert "unavailable" in unavailable.status_label.casefold()
    assert owner._hardware_write_worker.commands == []


def test_refused_preview_submission_retains_pending_live_hardware_work(tmp_path) -> None:
    owner = _Owner(_device(tmp_path))
    owner._hardware_write_worker.submit_result = SubmissionDisposition.REFUSED
    adapter = EffectStudioPhysicalPreviewAdapter(owner)
    option = adapter.devices()[0]

    receipt = adapter.start(
        effect_id="pulse",
        preview_device_id=option.preview_device_id,
        consent_granted=True,
        duration_seconds=10.0,
        reduce_motion=False,
        scenario=SyntheticScenario.ONE_AGENT,
        registry=EFFECT_REGISTRY,
    )

    assert receipt.accepted is False
    assert owner._hardware_write_worker.discarded_prefixes == []
    assert owner._hardware_write_worker.pending == {
        "device-preview-test:latest": "live-status"
    }
    assert owner._hardware_write_worker.discarded == []
    assert owner.restored == []


def test_release_cancels_the_preview_and_restores_committed_output(tmp_path) -> None:
    owner = _Owner(_device(tmp_path))
    adapter = EffectStudioPhysicalPreviewAdapter(owner)
    option = adapter.devices()[0]
    receipt = adapter.start(
        effect_id="notification",
        preview_device_id=option.preview_device_id,
        consent_granted=True,
        duration_seconds=10.0,
        reduce_motion=False,
        scenario=SyntheticScenario.COMPLETION,
        registry=EFFECT_REGISTRY,
    )

    released = adapter.release(PreviewReleaseReason.CLOSE)

    assert receipt.accepted is True
    assert released is True
    assert adapter.active_session is None
    assert owner.cancelled == 1
    assert len(owner._hardware_write_worker.discarded) == 1
    assert owner.restored == [owner.device.device_id]


def test_write_error_releases_and_restores_the_active_preview(tmp_path) -> None:
    owner = _Owner(_device(tmp_path))
    adapter = EffectStudioPhysicalPreviewAdapter(owner)
    option = adapter.devices()[0]
    adapter.start(
        effect_id="alert",
        preview_device_id=option.preview_device_id,
        consent_granted=True,
        duration_seconds=10.0,
        reduce_motion=False,
        scenario=SyntheticScenario.ASKING,
        registry=EFFECT_REGISTRY,
    )
    request = owner._hardware_write_worker.commands[0].payload

    handled = adapter.handle_write_result(request, error="device was removed")

    assert handled is True
    assert adapter.active_session is None
    assert owner.restored == [owner.device.device_id]


def test_stale_write_result_cannot_release_a_newer_preview_session(tmp_path) -> None:
    owner = _Owner(_device(tmp_path))
    adapter = EffectStudioPhysicalPreviewAdapter(owner)
    option = adapter.devices()[0]
    first = adapter.start(
        effect_id="alert",
        preview_device_id=option.preview_device_id,
        consent_granted=True,
        duration_seconds=10.0,
        reduce_motion=False,
        scenario=SyntheticScenario.ASKING,
        registry=EFFECT_REGISTRY,
    )
    first_request = owner._hardware_write_worker.commands[-1].payload
    second = adapter.start(
        effect_id="notification",
        preview_device_id=option.preview_device_id,
        consent_granted=True,
        duration_seconds=10.0,
        reduce_motion=False,
        scenario=SyntheticScenario.COMPLETION,
        registry=EFFECT_REGISTRY,
    )
    restored_before_stale_result = tuple(owner.restored)

    handled = adapter.handle_write_result(first_request, error="late failure")

    assert first.session_id != second.session_id
    assert handled is False
    assert adapter.active_session is not None
    assert adapter.active_session.session_id == second.session_id
    assert tuple(owner.restored) == restored_before_stale_result
