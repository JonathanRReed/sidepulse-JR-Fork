"""Bounded Effect Studio previews through JR-Bar's single hardware writer.

The adapter owns no settings and performs no direct device I/O. It validates
one explicit preview plan, compiles data-only effect metadata through the
shared presentation safety compiler, and submits the result to the status
controller's existing serial hardware worker. Releasing a preview always
hands restoration back to that same controller.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Final

from . import status_bar_legacy as _legacy
from .effect_registry import EFFECT_REGISTRY, EffectDefinition, EffectRegistry
from .effect_studio import (
    MAX_PHYSICAL_PREVIEW_SECONDS,
    PhysicalPreviewDecision,
    SyntheticScenario,
    build_gallery_rows,
    plan_physical_preview,
)
from .presentation_compiler import compile_presentation_program
from .runtime_scheduler import SubmissionDisposition

_PREVIEW_COALESCE_IDENTITY: Final = "preview-effect-studio"
_PHYSICAL_SURFACE_KEYS: Final = {
    2: frozenset({"sidepulse_dot", "dot"}),
    8: frozenset({"sidepulse_pro", "pro"}),
}
_SEMANTIC_COLORS: Final = {
    "working": "#00E5FF",
    "asking": "#FF3A00",
    "completion": "#00FF66",
    "failure": "#FF3A00",
    "recovery": "#12E3B0",
    "notification": "#A45CFF",
    "quota": "#36C5F0",
    "environment": "#FFB340",
    "idle": "#8B93A7",
    "transition": "#A45CFF",
}


class PreviewReleaseReason(str, Enum):
    CLOSE = "close"
    SLEEP = "sleep"
    APP_TERMINATION = "app_termination"
    ERROR = "error"
    TIMEOUT = "timeout"
    REPLACED = "replaced"
    SELECTION_CHANGED = "selection_changed"


@dataclass(frozen=True, slots=True)
class PhysicalPreviewDevice:
    preview_device_id: str
    name: str
    led_count: int

    def __post_init__(self) -> None:
        if (
            type(self.preview_device_id) is not str
            or not self.preview_device_id.startswith("preview-")
            or len(self.preview_device_id) > 64
            or type(self.name) is not str
            or not self.name
            or len(self.name) > 160
            or self.led_count not in _PHYSICAL_SURFACE_KEYS
        ):
            raise ValueError("invalid physical preview device")


@dataclass(frozen=True, slots=True)
class PhysicalPreviewAvailability:
    available: bool
    reason: str

    def __post_init__(self) -> None:
        if (
            type(self.available) is not bool
            or type(self.reason) is not str
            or not self.reason
            or len(self.reason) > 256
        ):
            raise ValueError("invalid physical preview availability")


@dataclass(frozen=True, slots=True)
class CompiledPhysicalPreview:
    effect_id: str
    rendered_effect_id: str
    program: str
    state: _legacy.LedDisplayState
    animated: bool


@dataclass(frozen=True, slots=True)
class ActivePhysicalPreview:
    session_id: str
    preview_device_id: str
    hardware_device_id: str
    worker_key: str
    coalesce_key: str
    effect_id: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class PhysicalPreviewStartReceipt:
    accepted: bool
    session_id: str | None
    status_label: str
    duration_seconds: float
    reason: str | None

    def __post_init__(self) -> None:
        if (
            type(self.accepted) is not bool
            or (self.accepted != (self.session_id is not None))
            or type(self.status_label) is not str
            or not self.status_label
            or not 0.0 <= float(self.duration_seconds) <= MAX_PHYSICAL_PREVIEW_SECONDS
            or (self.reason is not None and (type(self.reason) is not str or not self.reason))
        ):
            raise ValueError("invalid physical preview receipt")


def _preview_device_id(hardware_device_id: str) -> str:
    digest = hashlib.sha256(hardware_device_id.encode("utf-8")).hexdigest()[:24]
    return f"preview-{digest}"


def _physical_effect_supported(effect: EffectDefinition, led_count: int) -> bool:
    keys = _PHYSICAL_SURFACE_KEYS.get(led_count)
    return bool(keys and keys.intersection(effect.surfaces))


def _effect_state(semantic: str) -> _legacy.LedDisplayState:
    return {
        "asking": _legacy.LedDisplayState.ASK,
        "failure": _legacy.LedDisplayState.FAILED,
        "completion": _legacy.LedDisplayState.DONE,
        "notification": _legacy.LedDisplayState.DONE,
        "working": _legacy.LedDisplayState.WORKING,
    }.get(semantic, _legacy.LedDisplayState.IDLE)


def _program_for_effect(
    effect: EffectDefinition,
    color: str,
    led_count: int,
    semantic: str,
) -> tuple[str, bool]:
    identifier = effect.identifier
    if identifier == "none":
        return color, False
    if identifier == "pulse":
        return f"off 400ms cosine\n{color} 1600ms pulse\nrepeat", True
    if identifier == "rainbow":
        return (
            "#FF3B30 600ms cosine\n"
            "#FF9500 600ms cosine\n"
            "#34C759 600ms cosine\n"
            "#0A84FF 600ms cosine\n"
            "#AF52DE 600ms cosine\nrepeat",
            True,
        )
    if identifier == "alert":
        return f"{color} 500ms none\noff 500ms none\nrepeat", True
    if identifier == "notification":
        return f"off 300ms cosine\n{color} 900ms pulse\n{color}", True

    # Data-only community effects intentionally cannot introduce executable
    # renderers. Preserve their declared semantic with one registered, safe
    # primitive while keeping the pack identifier visible in the receipt.
    semantic_primitive = {
        "working": "pulse",
        "asking": "alert",
        "failure": "alert",
        "completion": "notification",
        "recovery": "notification",
        "notification": "notification",
        "transition": "pulse",
    }.get(semantic, "none")
    return _program_for_effect(
        EFFECT_REGISTRY.require(semantic_primitive),
        color,
        led_count,
        semantic,
    )


def compile_effect_studio_physical_preview(
    effect_id: str,
    *,
    led_count: int,
    scenario: SyntheticScenario,
    reduce_motion: bool,
    registry: EffectRegistry = EFFECT_REGISTRY,
) -> CompiledPhysicalPreview:
    """Compile one selected effect into safe firmware DSL without I/O."""

    if type(registry) is not EffectRegistry:
        raise TypeError("registry must be EffectRegistry")
    if type(scenario) is not SyntheticScenario:
        raise TypeError("scenario must be SyntheticScenario")
    if type(reduce_motion) is not bool:
        raise TypeError("reduce_motion must be bool")
    if led_count not in _PHYSICAL_SURFACE_KEYS:
        raise ValueError("unsupported physical LED topology")
    effect = registry.require(effect_id)
    if not _physical_effect_supported(effect, led_count):
        raise ValueError("selected effect is not supported on this physical device")
    rendered = registry.reduced_motion(effect.identifier) if reduce_motion else effect
    if not _physical_effect_supported(rendered, led_count):
        raise ValueError("Reduce Motion fallback is not supported on this device")
    row = next(
        item for item in build_gallery_rows(registry) if item.effect_id == effect.identifier
    )
    semantic = row.semantic_family.value
    color = _SEMANTIC_COLORS.get(semantic, "#8B93A7")
    if reduce_motion:
        candidate, animated = color, False
    else:
        candidate, animated = _program_for_effect(
            rendered,
            color,
            led_count,
            semantic,
        )
    compiled = compile_presentation_program(candidate, led_count=led_count, fallback=color)
    if not compiled.accepted:
        raise ValueError("selected effect failed the presentation safety compiler")
    return CompiledPhysicalPreview(
        effect_id=effect.identifier,
        rendered_effect_id=rendered.identifier,
        program=compiled.program,
        state=_effect_state(semantic),
        animated=bool(animated and not reduce_motion),
    )


class EffectStudioPhysicalPreviewAdapter:
    """Translate Studio consent into one request on the existing writer."""

    def __init__(self, owner: object) -> None:
        self._owner = owner
        self._active_session: ActivePhysicalPreview | None = None

    @property
    def active_session(self) -> ActivePhysicalPreview | None:
        return self._active_session

    def _hardware_devices(self):
        if not bool(getattr(self._owner, "leds_enabled", False)):
            return ()
        try:
            devices = self._owner.status_bar_devices(remember=False)
        except Exception:
            return ()
        return tuple(
            device
            for device in devices
            if type(device) is _legacy.StatusBarDevice
            and device.connected
            and device.device_id != _legacy.VIRTUAL_DEVICE_ID
        )[: _legacy.MAX_RUNTIME_PHYSICAL_DEVICES]

    def devices(self) -> tuple[PhysicalPreviewDevice, ...]:
        return tuple(
            PhysicalPreviewDevice(
                _preview_device_id(device.device_id),
                device.name,
                _legacy.led_count_for_target(device.target),
            )
            for device in self._hardware_devices()
            if _legacy.led_count_for_target(device.target) in _PHYSICAL_SURFACE_KEYS
        )

    def _device_for_preview_id(self, preview_device_id: str):
        return next(
            (
                device
                for device in self._hardware_devices()
                if _preview_device_id(device.device_id) == preview_device_id
            ),
            None,
        )

    def availability(
        self,
        effect_id: str,
        preview_device_id: str,
        *,
        reduce_motion: bool,
        registry: EffectRegistry = EFFECT_REGISTRY,
    ) -> PhysicalPreviewAvailability:
        if not bool(getattr(self._owner, "_hardware_write_active", False)):
            return PhysicalPreviewAvailability(False, "Physical preview is unavailable")
        device = self._device_for_preview_id(preview_device_id)
        if device is None:
            return PhysicalPreviewAvailability(False, "Select a connected SidePulse device")
        try:
            compile_effect_studio_physical_preview(
                effect_id,
                led_count=_legacy.led_count_for_target(device.target),
                scenario=SyntheticScenario.ONE_AGENT,
                reduce_motion=reduce_motion,
                registry=registry,
            )
        except (KeyError, TypeError, ValueError) as error:
            return PhysicalPreviewAvailability(False, str(error))
        return PhysicalPreviewAvailability(True, "Ready for a temporary preview")

    def start(
        self,
        *,
        effect_id: str,
        preview_device_id: str,
        consent_granted: bool,
        duration_seconds: float,
        reduce_motion: bool,
        scenario: SyntheticScenario,
        registry: EffectRegistry = EFFECT_REGISTRY,
    ) -> PhysicalPreviewStartReceipt:
        try:
            plan = plan_physical_preview(
                effect_id,
                preview_device_id,
                consent_granted=consent_granted,
                duration_seconds=duration_seconds,
                registry=registry,
            )
        except (KeyError, TypeError, ValueError) as error:
            return PhysicalPreviewStartReceipt(False, None, "Preview unavailable", 0.0, str(error))
        if plan.decision is PhysicalPreviewDecision.CONSENT_REQUIRED:
            return PhysicalPreviewStartReceipt(
                False,
                None,
                plan.status_label,
                0.0,
                "Explicit consent is required",
            )
        availability = self.availability(
            effect_id,
            preview_device_id,
            reduce_motion=reduce_motion,
            registry=registry,
        )
        if not availability.available:
            return PhysicalPreviewStartReceipt(
                False,
                None,
                "Physical preview unavailable",
                0.0,
                availability.reason,
            )
        device = self._device_for_preview_id(preview_device_id)
        assert device is not None
        led_count = _legacy.led_count_for_target(device.target)
        try:
            compiled = compile_effect_studio_physical_preview(
                effect_id,
                led_count=led_count,
                scenario=scenario,
                reduce_motion=reduce_motion,
                registry=registry,
            )
            controller = self._owner.agent_controller_for_device(device)
            rendered = _legacy.apply_brightness(compiled.program, controller.brightness)
            final = compile_presentation_program(
                rendered,
                led_count=led_count,
                fallback=_legacy.apply_brightness("off", controller.brightness),
            )
            if not final.accepted:
                raise ValueError("preview failed the final presentation safety gate")
        except (KeyError, OSError, TypeError, ValueError) as error:
            self.release(PreviewReleaseReason.ERROR)
            return PhysicalPreviewStartReceipt(False, None, "Preview failed", 0.0, str(error))

        self.release(PreviewReleaseReason.REPLACED)
        worker_key = self._owner._hardware_worker_key(device)
        coalesce_key = _legacy.hardware_coalesce_key(
            worker_key,
            _PREVIEW_COALESCE_IDENTITY,
        )
        snapshot = getattr(self._owner, "last_snapshot", None)
        session_id = secrets.token_hex(8)
        request = _legacy.HardwareWriteRequest(
            device=device,
            mode=(
                snapshot.aggregate.mode
                if snapshot is not None
                else _legacy.AgentMode.IDLE_READY
            ),
            battery_snapshot=getattr(self._owner, "last_battery_snapshot", None),
            statuses=(tuple(snapshot.statuses) if snapshot is not None else ()),
            projection=getattr(self._owner, "current_attention_projection", None),
            relay_elapsed_seconds=max(
                0.0,
                float(self._owner._runtime_worker_monotonic())
                - float(getattr(self._owner, "_relay_epoch", 0.0)),
            ),
            accessibility_preferences=getattr(
                self._owner,
                "_accessibility_display_preferences",
                None,
            ),
            display_kind=_legacy.LED_DISPLAY_TEST,
            write_priority=_legacy.RuntimeWorkPriority.EXPLICIT,
            coalesce_identity=_PREVIEW_COALESCE_IDENTITY,
            preview_session_id=session_id,
            override_program=final.program,
            override_state=compiled.state,
        )
        active = ActivePhysicalPreview(
            session_id,
            preview_device_id,
            device.device_id,
            worker_key,
            coalesce_key,
            effect_id,
            plan.duration_seconds,
        )
        try:
            disposition = self._owner._hardware_write_worker.submit(
                _legacy.RuntimeWorkCommand(
                    domain=_legacy.RuntimeWorkerDomain.HARDWARE_WRITE,
                    key=worker_key,
                    generation=self._owner._hardware_write_generation,
                    deadline=float(self._owner._runtime_worker_monotonic())
                    + plan.duration_seconds,
                    payload=request,
                    priority=_legacy.RuntimeWorkPriority.EXPLICIT,
                    coalesce_key=coalesce_key,
                )
            )
            if disposition is SubmissionDisposition.REFUSED:
                return PhysicalPreviewStartReceipt(
                    False,
                    None,
                    "Preview failed",
                    0.0,
                    "hardware writer refused the preview",
                )
            self._active_session = active
            self._owner._schedule_effect_studio_preview_timeout(
                session_id,
                plan.duration_seconds,
            )
        except Exception as error:
            if self._active_session is active:
                self.release(PreviewReleaseReason.ERROR)
            return PhysicalPreviewStartReceipt(False, None, "Preview failed", 0.0, str(error))
        return PhysicalPreviewStartReceipt(
            True,
            session_id,
            plan.status_label,
            plan.duration_seconds,
            None,
        )

    def release(
        self,
        reason: PreviewReleaseReason,
        *,
        restore: bool = True,
    ) -> bool:
        if type(reason) is not PreviewReleaseReason:
            raise TypeError("reason must be PreviewReleaseReason")
        active = self._active_session
        if active is None:
            return False
        self._active_session = None
        try:
            self._owner._cancel_effect_studio_preview_timeout()
        finally:
            self._owner._hardware_write_worker.discard_pending(active.coalesce_key)
        if restore and bool(getattr(self._owner, "_hardware_write_active", False)):
            self._owner._restore_effect_studio_physical_output(
                active.hardware_device_id
            )
        return True

    def handle_write_result(self, request: object, *, error: str | None) -> bool:
        active = self._active_session
        if (
            active is None
            or type(request) is not _legacy.HardwareWriteRequest
            or request.device.device_id != active.hardware_device_id
            or request.coalesce_identity != _PREVIEW_COALESCE_IDENTITY
            or request.preview_session_id != active.session_id
            or error is None
        ):
            return False
        return self.release(PreviewReleaseReason.ERROR)


__all__ = [
    "ActivePhysicalPreview",
    "CompiledPhysicalPreview",
    "EffectStudioPhysicalPreviewAdapter",
    "PhysicalPreviewAvailability",
    "PhysicalPreviewDevice",
    "PhysicalPreviewStartReceipt",
    "PreviewReleaseReason",
    "compile_effect_studio_physical_preview",
]
