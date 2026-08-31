"""Pure, immutable Do Not Disturb policy and persistence validation."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from enum import Enum
from itertools import islice

from .local_time_boundary import resolve_local_epoch

DEFAULT_DND_SCHEDULE_START_MINUTES = 22 * 60
DEFAULT_DND_SCHEDULE_END_MINUTES = 7 * 60
DEFAULT_DND_DIM_FRACTION = 0.15
MAX_DND_OVERRIDE_SECONDS = 7 * 24 * 60 * 60
MAX_DND_CONTRIBUTIONS = 4
MAX_DND_REFUSALS = 9


class DndMode(str, Enum):
    MUTE = "mute"
    DIM = "dim"
    PAUSE = "pause"
    ASKS_ONLY = "asks_only"
    DARK = "dark"


class DndSource(str, Enum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    MACOS_FOCUS = "macos_focus"
    NAMED_FOCUS = "named_focus"


class DisplayAdmission(str, Enum):
    NONE = "none"
    ASKS = "asks"
    CRITICAL = "critical"
    ALL = "all"


class OutboundAdmission(str, Enum):
    NONE = "none"
    ASKS = "asks"
    CRITICAL = "critical"
    ALL = "all"


class DndRefusalCode(str, Enum):
    WRONG_TYPE = "wrong_type"
    OUT_OF_RANGE = "out_of_range"
    UNKNOWN_MODE = "unknown_mode"
    EQUAL_BOUNDARIES = "equal_boundaries"
    INCOMPLETE_OVERRIDE = "incomplete_override"
    INVALID_OVERRIDE = "invalid_override"
    OVERRIDE_TOO_LONG = "override_too_long"


@dataclass(frozen=True, slots=True)
class DndPersistedRefusal:
    field: str
    code: DndRefusalCode

    def __post_init__(self) -> None:
        if type(self.field) is not str or not self.field:
            raise ValueError("DND refusal field must be a nonempty string")
        if type(self.code) is not DndRefusalCode:
            raise ValueError("DND refusal code must be known")


@dataclass(frozen=True, slots=True)
class DndSchedule:
    enabled: bool = False
    start_minutes: int = DEFAULT_DND_SCHEDULE_START_MINUTES
    end_minutes: int = DEFAULT_DND_SCHEDULE_END_MINUTES
    mode: DndMode = DndMode.DARK

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("DND schedule enabled must be a boolean")
        if not _valid_minute(self.start_minutes) or not _valid_minute(self.end_minutes):
            raise ValueError("DND schedule minutes must be integers from 0 through 1439")
        if self.start_minutes == self.end_minutes:
            raise ValueError("DND schedule boundaries must differ")
        if type(self.mode) is not DndMode:
            raise ValueError("DND schedule mode must be known")


@dataclass(frozen=True, slots=True)
class DndOverride:
    mode: DndMode | None
    resume: bool
    created_epoch: float
    until_epoch: float

    def __post_init__(self) -> None:
        if type(self.resume) is not bool:
            raise ValueError("DND override resume must be a boolean")
        if self.resume:
            if self.mode is not None:
                raise ValueError("a resume override cannot also select a DND mode")
        elif type(self.mode) is not DndMode:
            raise ValueError("a manual DND override requires a known mode")
        created = _finite_epoch(self.created_epoch)
        until = _finite_epoch(self.until_epoch)
        if created is None or until is None:
            raise ValueError("DND override epochs must be finite numbers")
        if until <= created:
            raise ValueError("DND override expiry must follow its creation")
        if until - created > MAX_DND_OVERRIDE_SECONDS:
            raise ValueError("DND override exceeds the bounded duration")
        object.__setattr__(self, "created_epoch", created)
        object.__setattr__(self, "until_epoch", until)

    @classmethod
    def for_mode(
        cls,
        mode: DndMode,
        *,
        created_epoch: float,
        until_epoch: float,
    ) -> DndOverride:
        return cls(mode, False, created_epoch, until_epoch)

    @classmethod
    def for_resume(
        cls,
        *,
        created_epoch: float,
        until_epoch: float,
    ) -> DndOverride:
        return cls(None, True, created_epoch, until_epoch)

    def active_at(self, epoch: float) -> bool:
        now = _finite_epoch(epoch)
        return now is not None and self.created_epoch <= now < self.until_epoch


@dataclass(frozen=True, slots=True)
class DndContribution:
    source: DndSource
    mode: DndMode | None
    display_admission: DisplayAdmission
    brightness_factor: float
    outbound_admission: OutboundAdmission
    banner_allowed: bool = True
    audible_allowed: bool = True
    webhook_allowed: bool = True

    def __post_init__(self) -> None:
        if type(self.source) is not DndSource:
            raise ValueError("DND contribution source must be known")
        if self.mode is not None and type(self.mode) is not DndMode:
            raise ValueError("DND contribution mode must be known when present")
        if type(self.display_admission) is not DisplayAdmission:
            raise ValueError("DND display admission must be known")
        if type(self.outbound_admission) is not OutboundAdmission:
            raise ValueError("DND outbound admission must be known")
        factor = _finite_fraction(self.brightness_factor, allow_zero=True)
        if factor is None:
            raise ValueError("DND brightness factor must be finite from 0 through 1")
        object.__setattr__(self, "brightness_factor", factor)
        if not all(
            type(value) is bool
            for value in (
                self.banner_allowed,
                self.audible_allowed,
                self.webhook_allowed,
            )
        ):
            raise ValueError("DND effect admissions must be booleans")


@dataclass(frozen=True, slots=True)
class DndProjection:
    contributions: tuple[DndContribution, ...]
    active_sources: tuple[DndSource, ...]
    display_admission: DisplayAdmission
    brightness_factor: float
    outbound_admission: OutboundAdmission
    banner_allowed: bool
    audible_allowed: bool
    webhook_allowed: bool
    summary: str
    reason: str
    next_transition_epoch: float | None

    def __post_init__(self) -> None:
        if type(self.contributions) is not tuple or type(self.active_sources) is not tuple:
            raise ValueError("DND projection collections must be immutable")
        if not all(type(item) is DndContribution for item in self.contributions):
            raise ValueError("DND projection contributions must be typed")
        if not all(type(item) is DndSource for item in self.active_sources):
            raise ValueError("DND projection sources must be typed")
        if tuple(item.source for item in self.contributions) != self.active_sources:
            raise ValueError("DND projection sources must match its contributions")
        if len(self.contributions) > MAX_DND_CONTRIBUTIONS:
            raise ValueError("DND projection contribution collection is unbounded")
        if len(set(self.active_sources)) != len(self.active_sources):
            raise ValueError("DND projection sources must be unique")
        if type(self.display_admission) is not DisplayAdmission:
            raise ValueError("DND projection display admission must be known")
        if type(self.outbound_admission) is not OutboundAdmission:
            raise ValueError("DND projection outbound admission must be known")
        factor = _finite_fraction(self.brightness_factor, allow_zero=True)
        if factor is None:
            raise ValueError("DND projection brightness must be finite from 0 through 1")
        object.__setattr__(self, "brightness_factor", factor)
        if not all(
            type(value) is bool
            for value in (
                self.banner_allowed,
                self.audible_allowed,
                self.webhook_allowed,
            )
        ):
            raise ValueError("DND projection effect admissions must be booleans")
        if type(self.summary) is not str or not 1 <= len(self.summary) <= 256:
            raise ValueError("DND projection summary must be bounded text")
        if type(self.reason) is not str or not 1 <= len(self.reason) <= 512:
            raise ValueError("DND projection reason must be bounded text")
        next_epoch = _optional_finite_epoch(self.next_transition_epoch)
        if self.next_transition_epoch is not None and next_epoch is None:
            raise ValueError("DND next transition must be a finite epoch")
        object.__setattr__(self, "next_transition_epoch", next_epoch)

    @property
    def mode(self) -> DndMode | None:
        modes = tuple(item.mode for item in self.contributions)
        return modes[0] if len(modes) == 1 else None

    @property
    def source(self) -> DndSource | None:
        return self.active_sources[0] if len(self.active_sources) == 1 else None

    @property
    def active_modes(self) -> tuple[DndMode, ...]:
        return tuple(
            item.mode for item in self.contributions if item.mode is not None
        )


@dataclass(frozen=True, slots=True)
class DndScheduleEvaluation:
    active: bool
    next_transition_epoch: float | None

    def __post_init__(self) -> None:
        if type(self.active) is not bool:
            raise ValueError("DND schedule activity must be a boolean")
        epoch = _optional_finite_epoch(self.next_transition_epoch)
        if self.next_transition_epoch is not None and epoch is None:
            raise ValueError("DND schedule transition must be finite")
        object.__setattr__(self, "next_transition_epoch", epoch)


@dataclass(frozen=True, slots=True)
class ParsedDndSettings:
    schedule: DndSchedule
    override: DndOverride | None
    dim_fraction: float
    focus_mode: DndMode
    refusals: tuple[DndPersistedRefusal, ...] = ()

    def __post_init__(self) -> None:
        if type(self.schedule) is not DndSchedule:
            raise ValueError("parsed DND schedule must be canonical")
        if self.override is not None and type(self.override) is not DndOverride:
            raise ValueError("parsed DND override must be canonical")
        fraction = _finite_fraction(self.dim_fraction, allow_zero=False)
        if fraction is None:
            raise ValueError("parsed DND dim fraction must be positive and finite")
        object.__setattr__(self, "dim_fraction", fraction)
        if type(self.focus_mode) is not DndMode:
            raise ValueError("parsed DND Focus mode must be known")
        if type(self.refusals) is not tuple or not all(
            type(item) is DndPersistedRefusal for item in self.refusals
        ):
            raise ValueError("parsed DND refusals must be immutable and typed")
        if len(self.refusals) > MAX_DND_REFUSALS:
            raise ValueError("parsed DND refusals must remain bounded")
        if len({item.field for item in self.refusals}) != len(self.refusals):
            raise ValueError("parsed DND refusals must be field-unique")


_DISPLAY_RANK = {
    DisplayAdmission.NONE: 0,
    DisplayAdmission.ASKS: 1,
    DisplayAdmission.CRITICAL: 2,
    DisplayAdmission.ALL: 3,
}
_OUTBOUND_RANK = {
    OutboundAdmission.NONE: 0,
    OutboundAdmission.ASKS: 1,
    OutboundAdmission.CRITICAL: 2,
    OutboundAdmission.ALL: 3,
}
_SOURCE_LABEL = {
    DndSource.MANUAL: "Manual",
    DndSource.SCHEDULE: "Scheduled",
    DndSource.MACOS_FOCUS: "macOS Focus",
    DndSource.NAMED_FOCUS: "Named Focus",
}
_MODE_LABEL = {
    DndMode.MUTE: "Mute",
    DndMode.DIM: "Dim",
    DndMode.PAUSE: "Pause",
    DndMode.ASKS_ONLY: "Asks Only",
    DndMode.DARK: "Fully Dark",
}


def contribution_for_mode(
    source: DndSource,
    mode: DndMode,
    *,
    dim_fraction: float = DEFAULT_DND_DIM_FRACTION,
) -> DndContribution:
    """Project one product mode into its independent policy axes."""
    if type(source) is not DndSource or type(mode) is not DndMode:
        raise ValueError("DND mode contribution requires known identities")
    fraction = _finite_fraction(dim_fraction, allow_zero=False)
    if fraction is None:
        raise ValueError("DND dim fraction must be finite, positive, and at most 1")
    if mode is DndMode.MUTE:
        return DndContribution(
            source,
            mode,
            DisplayAdmission.ALL,
            1.0,
            OutboundAdmission.NONE,
            False,
            False,
            False,
        )
    if mode is DndMode.DIM:
        return DndContribution(
            source,
            mode,
            DisplayAdmission.ALL,
            fraction,
            OutboundAdmission.ALL,
        )
    if mode is DndMode.PAUSE:
        return DndContribution(
            source,
            mode,
            DisplayAdmission.CRITICAL,
            1.0,
            OutboundAdmission.CRITICAL,
        )
    if mode is DndMode.ASKS_ONLY:
        return DndContribution(
            source,
            mode,
            DisplayAdmission.ASKS,
            1.0,
            OutboundAdmission.ASKS,
        )
    return DndContribution(
        source,
        mode,
        DisplayAdmission.NONE,
        0.0,
        OutboundAdmission.NONE,
        False,
        False,
        False,
    )


def compose_dnd_contributions(
    contributions: Iterable[DndContribution],
    *,
    next_transition_epoch: float | None = None,
) -> DndProjection:
    """Compose independent DND sources by taking the strictest value per axis."""
    try:
        items = tuple(islice(iter(contributions), MAX_DND_CONTRIBUTIONS + 1))
    except TypeError as exc:
        raise ValueError("DND contributions must be a bounded sequence") from exc
    if len(items) > MAX_DND_CONTRIBUTIONS:
        raise ValueError("DND contribution collection must remain bounded")
    if not all(type(item) is DndContribution for item in items):
        raise ValueError("DND contributions must be typed")
    sources = tuple(item.source for item in items)
    if len(set(sources)) != len(sources):
        raise ValueError("duplicate DND source contribution")
    next_epoch = _optional_finite_epoch(next_transition_epoch)
    if next_transition_epoch is not None and next_epoch is None:
        raise ValueError("DND next transition must be a finite epoch")
    if not items:
        return DndProjection(
            (),
            (),
            DisplayAdmission.ALL,
            1.0,
            OutboundAdmission.ALL,
            True,
            True,
            True,
            "DND: Off",
            "No DND source is active.",
            next_epoch,
        )

    display = min(items, key=lambda item: _DISPLAY_RANK[item.display_admission])
    outbound = min(items, key=lambda item: _OUTBOUND_RANK[item.outbound_admission])
    labels = tuple(_contribution_label(item) for item in items)
    detail = " + ".join(labels)
    reason_subject = _natural_join(labels)
    reason = (
        f"{reason_subject} compose on independent presentation axes."
        if len(labels) > 1
        else f"{reason_subject} is active."
    )
    return DndProjection(
        items,
        sources,
        display.display_admission,
        min(item.brightness_factor for item in items),
        outbound.outbound_admission,
        all(item.banner_allowed for item in items),
        all(item.audible_allowed for item in items),
        all(item.webhook_allowed for item in items),
        f"DND: {detail}",
        reason,
        next_epoch,
    )


def evaluate_dnd_schedule(
    schedule: DndSchedule,
    *,
    now: float,
    local_timezone: tzinfo,
) -> DndScheduleEvaluation:
    """Evaluate one local daily interval and its next real boundary."""
    if type(schedule) is not DndSchedule:
        raise ValueError("DND schedule must be canonical")
    epoch = _finite_epoch(now)
    if epoch is None or not isinstance(local_timezone, tzinfo):
        raise ValueError("DND schedule evaluation requires a finite clock and timezone")
    if not schedule.enabled:
        return DndScheduleEvaluation(False, None)
    try:
        local_date = datetime.fromtimestamp(epoch, local_timezone).date()
    except (OSError, OverflowError, TypeError, ValueError) as exc:
        raise ValueError("DND schedule clock cannot be represented") from exc

    intervals: list[tuple[float, float]] = []
    for offset in range(-2, 4):
        try:
            anchor = local_date + timedelta(days=offset)
            interval = _schedule_interval(schedule, anchor, local_timezone)
        except OverflowError:
            continue
        if interval is not None:
            intervals.append(interval)
    active_ends = sorted(end for start, end in intervals if start <= epoch < end)
    if active_ends:
        return DndScheduleEvaluation(True, active_ends[0])
    future_starts = sorted(start for start, _end in intervals if start > epoch)
    return DndScheduleEvaluation(False, future_starts[0] if future_starts else None)


def evaluate_dnd_policy(
    *,
    schedule: DndSchedule,
    override: DndOverride | None,
    dim_fraction: float,
    focus_mode: DndMode,
    now: float,
    local_timezone: tzinfo,
    macos_focus_active: bool = False,
    named_focus: DndContribution | None = None,
) -> DndProjection:
    """Evaluate durable local and injected Focus truth into one projection."""
    epoch = _finite_epoch(now)
    if epoch is None:
        raise ValueError("DND policy clock must be finite")
    fraction = _finite_fraction(dim_fraction, allow_zero=False)
    if fraction is None:
        raise ValueError("DND dim fraction must be finite, positive, and at most 1")
    if override is not None and type(override) is not DndOverride:
        raise ValueError("DND override must be canonical")
    if type(focus_mode) is not DndMode or type(macos_focus_active) is not bool:
        raise ValueError("DND Focus truth must be typed")
    if named_focus is not None and (
        type(named_focus) is not DndContribution
        or named_focus.source is not DndSource.NAMED_FOCUS
    ):
        raise ValueError("named Focus DND contribution has the wrong source")
    schedule_state = evaluate_dnd_schedule(
        schedule,
        now=epoch,
        local_timezone=local_timezone,
    )
    override_active = override is not None and override.active_at(epoch)
    resume_active = bool(override_active and override is not None and override.resume)
    contributions: list[DndContribution] = []
    if override_active and override is not None and not override.resume:
        assert override.mode is not None
        contributions.append(
            contribution_for_mode(
                DndSource.MANUAL,
                override.mode,
                dim_fraction=fraction,
            )
        )
    if schedule_state.active and not resume_active:
        contributions.append(
            contribution_for_mode(
                DndSource.SCHEDULE,
                schedule.mode,
                dim_fraction=fraction,
            )
        )
    if macos_focus_active:
        contributions.append(
            contribution_for_mode(
                DndSource.MACOS_FOCUS,
                focus_mode,
                dim_fraction=fraction,
            )
        )
    if macos_focus_active and named_focus is not None:
        contributions.append(named_focus)

    transitions: list[float] = []
    if override is not None:
        if override_active:
            transitions.append(override.until_epoch)
        elif epoch < override.created_epoch:
            transitions.append(override.created_epoch)
    if not resume_active and schedule_state.next_transition_epoch is not None:
        transitions.append(schedule_state.next_transition_epoch)
    next_transition = min((item for item in transitions if item > epoch), default=None)
    return compose_dnd_contributions(
        tuple(contributions),
        next_transition_epoch=next_transition,
    )


def parse_dnd_settings(raw: object) -> ParsedDndSettings:
    """Parse the nine bounded persisted scalars, refusing bad fields separately."""
    mapping = raw if isinstance(raw, Mapping) else {}
    refusals: list[DndPersistedRefusal] = []

    enabled = _parsed_bool(
        mapping,
        "dnd_schedule_enabled",
        False,
        refusals,
    )
    start = _parsed_minute(
        mapping,
        "dnd_schedule_start_minutes",
        DEFAULT_DND_SCHEDULE_START_MINUTES,
        refusals,
    )
    end = _parsed_minute(
        mapping,
        "dnd_schedule_end_minutes",
        DEFAULT_DND_SCHEDULE_END_MINUTES,
        refusals,
    )
    schedule_mode = _parsed_mode(
        mapping,
        "dnd_schedule_mode",
        DndMode.DARK,
        refusals,
    )
    if start == end:
        _record_refusal(
            refusals,
            "dnd_schedule_start_minutes",
            DndRefusalCode.EQUAL_BOUNDARIES,
        )
        _record_refusal(
            refusals,
            "dnd_schedule_end_minutes",
            DndRefusalCode.EQUAL_BOUNDARIES,
        )
        enabled = False
        start = DEFAULT_DND_SCHEDULE_START_MINUTES
        end = DEFAULT_DND_SCHEDULE_END_MINUTES
    schedule = DndSchedule(enabled, start, end, schedule_mode)

    dim_fraction = _parsed_fraction(
        mapping,
        "dnd_dim_fraction",
        DEFAULT_DND_DIM_FRACTION,
        refusals,
    )
    focus_mode = _parsed_mode(
        mapping,
        "dnd_focus_mode",
        DndMode.PAUSE,
        refusals,
    )
    override = _parsed_override(mapping, refusals)
    return ParsedDndSettings(
        schedule,
        override,
        dim_fraction,
        focus_mode,
        tuple(refusals),
    )


def serialize_dnd_settings(parsed: ParsedDndSettings) -> dict[str, object]:
    """Serialize only the bounded, canonical DND scalar contract."""
    if type(parsed) is not ParsedDndSettings:
        raise ValueError("DND settings must be parsed and canonical")
    override = parsed.override
    return {
        "dnd_schedule_enabled": parsed.schedule.enabled,
        "dnd_schedule_start_minutes": parsed.schedule.start_minutes,
        "dnd_schedule_end_minutes": parsed.schedule.end_minutes,
        "dnd_schedule_mode": parsed.schedule.mode.value,
        "dnd_dim_fraction": parsed.dim_fraction,
        "dnd_override_mode": (
            None
            if override is None
            else "resume"
            if override.resume
            else override.mode.value  # type: ignore[union-attr]
        ),
        "dnd_override_created_epoch": (
            None if override is None else override.created_epoch
        ),
        "dnd_override_until_epoch": None if override is None else override.until_epoch,
        "dnd_focus_mode": parsed.focus_mode.value,
    }


def _schedule_interval(
    schedule: DndSchedule,
    anchor: date,
    zone: tzinfo,
) -> tuple[float, float] | None:
    start_time = time(schedule.start_minutes // 60, schedule.start_minutes % 60)
    end_time = time(schedule.end_minutes // 60, schedule.end_minutes % 60)
    end_date = anchor
    if schedule.start_minutes > schedule.end_minutes:
        try:
            end_date += timedelta(days=1)
        except OverflowError:
            return None
    start = resolve_local_epoch(anchor, start_time, zone)
    if start is None:
        return None
    end = resolve_local_epoch(
        end_date,
        end_time,
        zone,
        not_before_epoch=start,
    )
    if end is None or end <= start:
        return None
    return start, end


def _contribution_label(contribution: DndContribution) -> str:
    source = _SOURCE_LABEL[contribution.source]
    if contribution.mode is None:
        return f"{source} policy"
    return f"{source} {_MODE_LABEL[contribution.mode]}"


def _natural_join(values: tuple[str, ...]) -> str:
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _parsed_bool(
    mapping: Mapping[object, object],
    field: str,
    default: bool,
    refusals: list[DndPersistedRefusal],
) -> bool:
    if field not in mapping:
        return default
    value = mapping[field]
    if type(value) is bool:
        return value
    _record_refusal(refusals, field, DndRefusalCode.WRONG_TYPE)
    return default


def _parsed_minute(
    mapping: Mapping[object, object],
    field: str,
    default: int,
    refusals: list[DndPersistedRefusal],
) -> int:
    if field not in mapping:
        return default
    value = mapping[field]
    if _valid_minute(value):
        return value
    code = DndRefusalCode.WRONG_TYPE if type(value) is not int else DndRefusalCode.OUT_OF_RANGE
    _record_refusal(refusals, field, code)
    return default


def _parsed_mode(
    mapping: Mapping[object, object],
    field: str,
    default: DndMode,
    refusals: list[DndPersistedRefusal],
) -> DndMode:
    if field not in mapping:
        return default
    value = mapping[field]
    try:
        if type(value) is not str:
            raise ValueError
        return DndMode(value)
    except ValueError:
        code = DndRefusalCode.WRONG_TYPE if type(value) is not str else DndRefusalCode.UNKNOWN_MODE
        _record_refusal(refusals, field, code)
        return default


def _parsed_fraction(
    mapping: Mapping[object, object],
    field: str,
    default: float,
    refusals: list[DndPersistedRefusal],
) -> float:
    if field not in mapping:
        return default
    value = _finite_fraction(mapping[field], allow_zero=False)
    if value is not None:
        return value
    raw = mapping[field]
    code = (
        DndRefusalCode.WRONG_TYPE
        if isinstance(raw, bool) or not isinstance(raw, (int, float))
        else DndRefusalCode.OUT_OF_RANGE
    )
    _record_refusal(refusals, field, code)
    return default


def _parsed_override(
    mapping: Mapping[object, object],
    refusals: list[DndPersistedRefusal],
) -> DndOverride | None:
    fields = (
        "dnd_override_mode",
        "dnd_override_created_epoch",
        "dnd_override_until_epoch",
    )
    values = tuple(mapping.get(field) for field in fields)
    if all(value is None for value in values):
        return None
    mode_value, created_value, until_value = values
    if any(value is None for value in values):
        _record_refusal(
            refusals,
            "dnd_override",
            DndRefusalCode.INCOMPLETE_OVERRIDE,
        )
        return None
    if type(mode_value) is not str:
        _record_refusal(
            refusals,
            "dnd_override",
            DndRefusalCode.INVALID_OVERRIDE,
        )
        return None
    try:
        mode = None if mode_value == "resume" else DndMode(mode_value)
    except ValueError:
        _record_refusal(
            refusals,
            "dnd_override",
            DndRefusalCode.UNKNOWN_MODE,
        )
        return None
    created = _finite_epoch(created_value)
    until = _finite_epoch(until_value)
    if created is None or until is None or until <= created:
        _record_refusal(
            refusals,
            "dnd_override",
            DndRefusalCode.INVALID_OVERRIDE,
        )
        return None
    if until - created > MAX_DND_OVERRIDE_SECONDS:
        _record_refusal(
            refusals,
            "dnd_override",
            DndRefusalCode.OVERRIDE_TOO_LONG,
        )
        return None
    return DndOverride(mode, mode_value == "resume", created, until)


def _valid_minute(value: object) -> bool:
    return type(value) is int and 0 <= value < 24 * 60


def _record_refusal(
    refusals: list[DndPersistedRefusal],
    field: str,
    code: DndRefusalCode,
) -> None:
    if any(item.field == field for item in refusals):
        return
    if len(refusals) >= MAX_DND_REFUSALS:
        return
    refusals.append(DndPersistedRefusal(field, code))


def _finite_epoch(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


def _optional_finite_epoch(value: object) -> float | None:
    return None if value is None else _finite_epoch(value)


def _finite_fraction(value: object, *, allow_zero: bool) -> float | None:
    result = _finite_epoch(value)
    if result is None or result > 1.0:
        return None
    if result < 0.0 or (result == 0.0 and not allow_zero):
        return None
    return result
