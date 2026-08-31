"""Typed, side-effect-free adaptation of staged ambient outputs for renderers."""

from __future__ import annotations

from dataclasses import dataclass

from .ambient_effect_dispatch import AmbientEffectSurface, AmbientEffectSurfaceOutput
from .ambient_effect_runtime import (
    active_ambient_surface_output,
    active_device_ambient_surface_output,
)
from .hardware_write_policy import HardwareWritePolicy
from .led_status import LedDisplayState, apply_brightness
from .presentation_policy import (
    GlanceSemantic,
    MotionClass,
    PresentationProgram,
    SemanticGlyph,
)
from .runtime_scheduler import RuntimeWorkPriority


@dataclass(frozen=True, slots=True)
class AmbientConsumerPresentation:
    output: AmbientEffectSurfaceOutput
    started_at: float
    nominal_program: str
    nominal_static_fallback_program: str
    program: str
    static_fallback_program: str
    led_state: LedDisplayState
    write_policy: HardwareWritePolicy
    motion: MotionClass
    next_visual_change_at: float
    dedupe_token: tuple[str, str, float]

    @property
    def accessibility_text(self) -> str:
        return self.output.accessibility_text

    @property
    def screen_bar_program(self) -> PresentationProgram:
        semantic = {
            "ask": GlanceSemantic.ATTENTION,
            "failure": GlanceSemantic.FRESH_FAILURE,
            "completion": GlanceSemantic.FRESH_COMPLETION,
            "work": GlanceSemantic.ACTIVE,
        }.get(getattr(self.output.semantic, "value", None), GlanceSemantic.REST)
        glyph = {
            GlanceSemantic.ATTENTION: SemanticGlyph.FULL_ANCHOR,
            GlanceSemantic.FRESH_FAILURE: SemanticGlyph.LEFT_ANCHOR,
            GlanceSemantic.FRESH_COMPLETION: SemanticGlyph.RIGHT_ANCHOR,
            GlanceSemantic.ACTIVE: SemanticGlyph.CENTER_PAIR,
            GlanceSemantic.REST: SemanticGlyph.REST,
        }[semantic]
        return PresentationProgram(
            semantic=semantic,
            glyph=glyph,
            motion=self.motion,
            dsl=self.nominal_program,
            static_fallback_dsl=self.nominal_static_fallback_program,
            temporal=None,
            trusted_period_seconds=None,
            relay_epoch=self.started_at,
            next_visual_change_at=self.next_visual_change_at,
            playback_anchor=self.started_at,
        )


def _led_state(output: AmbientEffectSurfaceOutput) -> LedDisplayState:
    semantic = getattr(output.semantic, "value", None)
    return {
        "ask": LedDisplayState.ASK,
        "failure": LedDisplayState.FAILED,
        "completion": LedDisplayState.DONE,
        "recovery": LedDisplayState.DONE,
        "idle": LedDisplayState.IDLE,
    }.get(semantic, LedDisplayState.WORKING)


def _write_policy(output: AmbientEffectSurfaceOutput) -> HardwareWritePolicy:
    semantic = getattr(output.semantic, "value", None)
    priority = (
        RuntimeWorkPriority.URGENT
        if semantic in {"ask", "failure"}
        else RuntimeWorkPriority.IMPORTANT
        if output.animated
        else RuntimeWorkPriority.COALESCIBLE
    )
    return HardwareWritePolicy(
        priority=priority,
        coalesce_identity=f"ambient-{output.family.value}",
    )


def _presentation(
    output: AmbientEffectSurfaceOutput,
    started_at: float,
    *,
    reduce_motion: bool,
    brightness: float,
) -> AmbientConsumerPresentation:
    selected_program = (
        output.static_fallback_program if reduce_motion else output.program
    )
    return AmbientConsumerPresentation(
        output=output,
        started_at=started_at,
        nominal_program=selected_program,
        nominal_static_fallback_program=output.static_fallback_program,
        program=apply_brightness(selected_program, brightness),
        static_fallback_program=apply_brightness(
            output.static_fallback_program,
            brightness,
        ),
        led_state=_led_state(output),
        write_policy=_write_policy(output),
        motion=(
            MotionClass.FINITE
            if output.animated and not reduce_motion
            else MotionClass.STATIC
        ),
        next_visual_change_at=started_at + output.expires_after_ms / 1_000.0,
        dedupe_token=("ambient", output.effect_identity, started_at),
    )


def active_ambient_presentation(
    controller: object,
    surface: AmbientEffectSurface,
    *,
    reduce_motion: bool,
    brightness: float,
) -> AmbientConsumerPresentation | None:
    """Adapt one unexpired staged output without writing or retaining state."""

    staged = active_ambient_surface_output(controller, surface)
    if staged is None:
        return None
    output, started_at = staged
    return _presentation(
        output,
        started_at,
        reduce_motion=reduce_motion,
        brightness=brightness,
    )


def active_hardware_ambient_presentation(
    controller: object,
    *,
    device_id: str,
    led_count: int,
    reduce_motion: bool,
    brightness: float,
) -> AmbientConsumerPresentation | None:
    surface = (
        AmbientEffectSurface.SIDEPULSE_DOT
        if led_count == 2
        else AmbientEffectSurface.SIDEPULSE_PRO
    )
    staged = active_device_ambient_surface_output(
        controller,
        surface,
        device_id=device_id,
    )
    if staged is None:
        return None
    output, started_at = staged
    return _presentation(
        output,
        started_at,
        reduce_motion=reduce_motion,
        brightness=brightness,
    )


def active_screen_bar_ambient_presentation(
    controller: object,
    *,
    reduce_motion: bool,
    brightness: float,
) -> AmbientConsumerPresentation | None:
    return active_ambient_presentation(
        controller,
        AmbientEffectSurface.SCREEN_BAR,
        reduce_motion=reduce_motion,
        brightness=brightness,
    )


__all__ = [
    "AmbientConsumerPresentation",
    "active_ambient_presentation",
    "active_hardware_ambient_presentation",
    "active_screen_bar_ambient_presentation",
]
