from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

MAX_SOURCE_FRAMES = 4096
MAX_EXPANDED_FRAMES = 4096
MAX_TOTAL_DURATION_SECONDS = 300.0

_ONE_SECOND = 1.0
_BOUNDARY_TOLERANCE_SECONDS = 1e-12
_SEMANTIC_KEY = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


class TemporalSafetyReason(str, Enum):
    SAFE = "safe"
    FLASH_RATE_EXCEEDED = "flash_rate_exceeded"
    UNCALIBRATED_FLASH_RATE = "uncalibrated_flash_rate"
    INVALID_PROGRAM = "invalid_program"
    EMPTY_PROGRAM = "empty_program"
    UNBOUNDED_INPUT = "unbounded_input"
    UNBOUNDED_REPEAT = "unbounded_repeat"
    INVALID_REPEAT = "invalid_repeat"
    OVERSIZED_PROGRAM = "oversized_program"
    INVALID_FRAME = "invalid_frame"
    NONFINITE_FRAME = "nonfinite_frame"
    INVALID_LUMINANCE = "invalid_luminance"
    NEGATIVE_DURATION = "negative_duration"
    ZERO_DURATION = "zero_duration"
    INVALID_CALIBRATION = "invalid_calibration"
    INVALID_STATIC_FALLBACK = "invalid_static_fallback"


@dataclass(frozen=True, slots=True)
class TemporalFrame:
    luminance: float
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class StaticSemanticFallback:
    semantic_key: str = "safe_static_state"
    luminance: float = 0.0

    @property
    def is_static(self) -> bool:
        return True


_CANONICAL_STATIC_FALLBACK = StaticSemanticFallback()


@dataclass(frozen=True, slots=True)
class TemporalProgram:
    frames: Sequence[TemporalFrame]
    repeat_count: int | None = 1
    static_fallback: StaticSemanticFallback = field(
        default_factory=StaticSemanticFallback
    )


@dataclass(frozen=True, slots=True)
class CalibrationState:
    physical_luminance_calibrated: bool = False
    flash_area_calibrated: bool = False

    @property
    def complete(self) -> bool:
        return (
            self.physical_luminance_calibrated is True
            and self.flash_area_calibrated is True
        )


@dataclass(frozen=True, slots=True)
class TemporalSafetyResult:
    static_fallback: StaticSemanticFallback
    reason: TemporalSafetyReason
    max_flashes_per_second: int


@dataclass(frozen=True, slots=True)
class SafeTemporalProgram(TemporalSafetyResult):
    pass


@dataclass(frozen=True, slots=True)
class TransformRequiredTemporalProgram(TemporalSafetyResult):
    pass


@dataclass(frozen=True, slots=True)
class RefusedTemporalProgram(TemporalSafetyResult):
    pass


TemporalSafetyOutcome = (
    SafeTemporalProgram
    | TransformRequiredTemporalProgram
    | RefusedTemporalProgram
)


def analyze_temporal_safety(
    program: object,
    *,
    calibration: CalibrationState = CalibrationState(),
) -> TemporalSafetyOutcome:
    """Analyze finite relative-luminance frames over sliding one-second windows.

    A flash is one non-overlapping pair of opposing luminance changes. Tiny
    opposing changes are deliberately included because normalized values alone
    do not prove physical luminance or affected display area.
    """
    if not isinstance(program, TemporalProgram):
        return _refused(
            TemporalSafetyReason.INVALID_PROGRAM,
            _CANONICAL_STATIC_FALLBACK,
        )

    fallback = _valid_fallback(program.static_fallback)
    if fallback is None:
        return _refused(
            TemporalSafetyReason.INVALID_STATIC_FALLBACK,
            _CANONICAL_STATIC_FALLBACK,
        )

    if not isinstance(calibration, CalibrationState) or not all(
        type(value) is bool
        for value in (
            calibration.physical_luminance_calibrated,
            calibration.flash_area_calibrated,
        )
    ):
        return _refused(TemporalSafetyReason.INVALID_CALIBRATION, fallback)

    frames = program.frames
    if not isinstance(frames, Sequence) or isinstance(
        frames, (str, bytes, bytearray)
    ):
        return _refused(TemporalSafetyReason.UNBOUNDED_INPUT, fallback)

    try:
        frame_count = len(frames)
    except Exception:
        return _refused(TemporalSafetyReason.INVALID_PROGRAM, fallback)
    if frame_count == 0:
        return _refused(TemporalSafetyReason.EMPTY_PROGRAM, fallback)
    if frame_count > MAX_SOURCE_FRAMES:
        return _refused(TemporalSafetyReason.OVERSIZED_PROGRAM, fallback)

    repeat_count = program.repeat_count
    if repeat_count is None:
        return _refused(TemporalSafetyReason.UNBOUNDED_REPEAT, fallback)
    if type(repeat_count) is not int or repeat_count <= 0:
        return _refused(TemporalSafetyReason.INVALID_REPEAT, fallback)
    if frame_count * repeat_count > MAX_EXPANDED_FRAMES:
        return _refused(TemporalSafetyReason.OVERSIZED_PROGRAM, fallback)

    validated_frames: list[TemporalFrame] = []
    try:
        for candidate in frames:
            reason = _invalid_frame_reason(candidate)
            if reason is not None:
                return _refused(reason, fallback)
            validated_frames.append(candidate)
    except Exception:
        return _refused(TemporalSafetyReason.INVALID_PROGRAM, fallback)

    source_duration = math.fsum(
        candidate.duration_seconds for candidate in validated_frames
    )
    if (
        not math.isfinite(source_duration)
        or source_duration * repeat_count > MAX_TOTAL_DURATION_SECONDS
    ):
        return _refused(TemporalSafetyReason.OVERSIZED_PROGRAM, fallback)

    max_flashes = _max_flashes_in_sliding_second(
        validated_frames,
        repeat_count,
    )
    if max_flashes <= 3:
        return SafeTemporalProgram(
            static_fallback=fallback,
            reason=TemporalSafetyReason.SAFE,
            max_flashes_per_second=max_flashes,
        )
    if not calibration.complete:
        return _refused(
            TemporalSafetyReason.UNCALIBRATED_FLASH_RATE,
            fallback,
            max_flashes=max_flashes,
        )
    return TransformRequiredTemporalProgram(
        static_fallback=fallback,
        reason=TemporalSafetyReason.FLASH_RATE_EXCEEDED,
        max_flashes_per_second=max_flashes,
    )


def _valid_fallback(
    fallback: object,
) -> StaticSemanticFallback | None:
    if not isinstance(fallback, StaticSemanticFallback):
        return None
    if not isinstance(fallback.semantic_key, str) or _SEMANTIC_KEY.fullmatch(
        fallback.semantic_key
    ) is None:
        return None
    luminance = fallback.luminance
    if type(luminance) not in {int, float}:
        return None
    if not math.isfinite(luminance) or not 0.0 <= luminance <= 1.0:
        return None
    return fallback


def _invalid_frame_reason(candidate: object) -> TemporalSafetyReason | None:
    if not isinstance(candidate, TemporalFrame):
        return TemporalSafetyReason.INVALID_FRAME
    if type(candidate.luminance) not in {int, float} or type(
        candidate.duration_seconds
    ) not in {int, float}:
        return TemporalSafetyReason.INVALID_FRAME
    if not math.isfinite(candidate.luminance) or not math.isfinite(
        candidate.duration_seconds
    ):
        return TemporalSafetyReason.NONFINITE_FRAME
    if not 0.0 <= candidate.luminance <= 1.0:
        return TemporalSafetyReason.INVALID_LUMINANCE
    if candidate.duration_seconds < 0.0:
        return TemporalSafetyReason.NEGATIVE_DURATION
    if candidate.duration_seconds == 0.0:
        return TemporalSafetyReason.ZERO_DURATION
    return None


def _max_flashes_in_sliding_second(
    frames: Sequence[TemporalFrame],
    repeat_count: int,
) -> int:
    flash_times: list[float] = []
    previous_luminance: float | None = None
    previous_duration = 0.0
    pending_direction = 0
    elapsed = 0.0
    compensation = 0.0

    for _ in range(repeat_count):
        for current in frames:
            if previous_luminance is not None:
                elapsed, compensation = _compensated_add(
                    elapsed,
                    compensation,
                    previous_duration,
                )
                direction = _direction(current.luminance - previous_luminance)
                if direction != 0:
                    if pending_direction == 0:
                        pending_direction = direction
                    elif direction != pending_direction:
                        flash_times.append(elapsed)
                        pending_direction = 0
            previous_luminance = current.luminance
            previous_duration = current.duration_seconds

    maximum = 0
    left = 0
    for right, end_time in enumerate(flash_times):
        while left <= right and not _within_one_second(
            end_time - flash_times[left]
        ):
            left += 1
        maximum = max(maximum, right - left + 1)
    return maximum


def _compensated_add(
    total: float,
    compensation: float,
    value: float,
) -> tuple[float, float]:
    adjusted = value - compensation
    updated = total + adjusted
    return updated, (updated - total) - adjusted


def _direction(change: float) -> int:
    if change > 0.0:
        return 1
    if change < 0.0:
        return -1
    return 0


def _within_one_second(span: float) -> bool:
    return span <= _ONE_SECOND or math.isclose(
        span,
        _ONE_SECOND,
        rel_tol=0.0,
        abs_tol=_BOUNDARY_TOLERANCE_SECONDS,
    )


def _refused(
    reason: TemporalSafetyReason,
    fallback: StaticSemanticFallback,
    *,
    max_flashes: int = 0,
) -> RefusedTemporalProgram:
    return RefusedTemporalProgram(
        static_fallback=fallback,
        reason=reason,
        max_flashes_per_second=max_flashes,
    )
