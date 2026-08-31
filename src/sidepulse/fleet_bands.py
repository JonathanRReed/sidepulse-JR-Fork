"""Pure, bounded planning for sticky fleet bands.

This module deliberately stops at a layout contract.  It does not know about
providers, hardware writes, animation programs, or lifecycle reducers.  A
runtime projector can turn its main rows into :class:`FleetMember` values,
pass the previous :class:`FleetPlan`, and compile the returned bands for the
Screen Bar and the eight-LED Pro strip.

The important boundary is that a band belongs to a stable project or machine
identity, not to a worker invocation.  Workers are ignored before identities
are deduplicated or semantic state is compared.  A uniform fleet is returned
as one full-width shared band, while the private ``member_slots`` ledger keeps
the sticky positions available for the next non-uniform frame.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final, Literal

LED_COUNT: Final = 8
MAX_INPUT_ROWS: Final = 4_096
MAX_FLEET_MEMBERS: Final = LED_COUNT
DEFAULT_SCREEN_BAR_WIDTH: Final = 1.0

FleetMode = Literal["empty", "segmented", "shared", "refused"]


class FleetBandPlanningError(ValueError):
    """Raised only for a malformed planner input, never for fleet overflow."""


@dataclass(frozen=True, slots=True)
class FleetMember:
    """The small, content-free input contract for one main fleet row.

    ``identity`` is useful when the upstream projection already has a
    canonical stable key.  Otherwise use exactly one of ``project_id`` or
    ``machine_id``.  When both are present, the project key is preferred,
    because this planner assigns one band to a project even when several
    sessions for that project are visible.

    ``is_worker`` and ``is_main`` are intentionally explicit.  A worker is
    never promoted to a fleet member based on a name or a provider-specific
    heuristic.
    """

    identity: str | None = None
    semantic: object = "idle"
    project_id: str | None = None
    machine_id: str | None = None
    is_worker: bool = False
    is_main: bool = True

    @property
    def stable_identity(self) -> str:
        """Return the stable key used by sticky assignment."""
        candidates = (self.identity, self.project_id, self.machine_id)
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        raise FleetBandPlanningError(
            "fleet member needs a non-empty identity, project_id, or machine_id"
        )


@dataclass(frozen=True, slots=True)
class FleetBand:
    """One planned band in both LED and Screen Bar coordinate systems.

    LED bounds use half-open indexing, ``[led_start, led_end)``.  Screen Bar
    bounds use the same half-open convention in the caller's width units.
    Therefore every renderer can use the exact same partition without a
    second rounding policy.
    """

    identity: str | None
    semantic: object
    led_start: int
    led_end: int
    screen_start: float
    screen_end: float
    shared: bool = False

    @property
    def led_count(self) -> int:
        return self.led_end - self.led_start

    @property
    def screen_width(self) -> float:
        return self.screen_end - self.screen_start

    # Short aliases make the contract convenient for renderers while keeping
    # the coordinate names unambiguous in reprs and tests.
    @property
    def start(self) -> int:
        return self.led_start

    @property
    def end(self) -> int:
        return self.led_end

    @property
    def x(self) -> float:
        return self.screen_start

    @property
    def width(self) -> float:
        return self.screen_width


@dataclass(frozen=True, slots=True)
class FleetPlan:
    """A bounded planner result and its integration seams.

    ``member_slots`` is retained even in ``shared`` mode.  It is not a second
    display output.  It is the previous-layout handoff that lets a later
    divergent state return each identity to its old segment instead of
    reshuffling its neighbours.
    """

    mode: FleetMode
    bands: tuple[FleetBand, ...] = ()
    member_slots: tuple[tuple[str, int, int], ...] = ()
    refusal: str | None = None
    led_count: int = LED_COUNT
    screen_bar_width: float = DEFAULT_SCREEN_BAR_WIDTH
    shared_semantic: object | None = None

    @property
    def accepted(self) -> bool:
        return self.refusal is None

    @property
    def refused(self) -> bool:
        return self.refusal is not None

    @property
    def layout(self) -> FleetPlan:
        """Alias for callers that store a previous layout separately."""
        return self

    def slot_for(self, identity: str) -> tuple[int, int] | None:
        """Return an identity's sticky LED interval, if it was retained."""
        for candidate, start, end in self.member_slots:
            if candidate == identity:
                return (start, end)
        return None


def _semantic_key(value: object) -> tuple[str, object]:
    """Produce a stable comparison key without conflating arbitrary objects."""
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str):
        if not value.strip():
            raise FleetBandPlanningError("fleet semantic state must be non-empty")
        return ("text", value)
    raise FleetBandPlanningError("fleet semantic state must be a string or Enum")


def _bool_field(value: object, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    if type(value) is not bool:
        raise FleetBandPlanningError(f"fleet member {field} must be boolean")
    return value


def _text_field(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FleetBandPlanningError(f"fleet member {field} must be a string")
    return value


def _member_from_mapping(value: Mapping[str, object]) -> FleetMember:
    semantic = value.get("semantic", value.get("semantic_state", value.get("state")))
    if semantic is None:
        semantic = value.get("lifecycle", value.get("status"))
    if semantic is None:
        raise FleetBandPlanningError("fleet member is missing semantic state")
    return FleetMember(
        identity=_text_field(value.get("identity", value.get("stable_identity")), "identity"),
        project_id=_text_field(value.get("project_id", value.get("project")), "project_id"),
        machine_id=_text_field(value.get("machine_id", value.get("machine")), "machine_id"),
        semantic=semantic,
        is_worker=_bool_field(
            value.get("is_worker", value.get("worker", value.get("is_subagent"))),
            "is_worker",
            default=False,
        ),
        is_main=_bool_field(value.get("is_main", value.get("main")), "is_main", default=True),
    )


def _coerce_member(value: object) -> FleetMember:
    if isinstance(value, FleetMember):
        return value
    if isinstance(value, Mapping):
        return _member_from_mapping(value)
    raise FleetBandPlanningError("fleet members must be FleetMember values or mappings")


def _collect_members(members: Iterable[object]) -> tuple[FleetMember, ...]:
    if isinstance(members, (str, bytes, bytearray)):
        raise FleetBandPlanningError("fleet members must be an iterable of rows")
    collected: list[FleetMember] = []
    iterator = iter(members)
    for _ in range(MAX_INPUT_ROWS + 1):
        try:
            value = next(iterator)
        except StopIteration:
            break
        collected.append(_coerce_member(value))
    if len(collected) > MAX_INPUT_ROWS:
        raise FleetBandPlanningError("fleet input exceeds bounded row limit")
    return tuple(collected)


def _previous_slots(previous_layout: object, led_count: int) -> dict[str, tuple[int, int]]:
    if previous_layout is None:
        return {}
    if isinstance(previous_layout, FleetPlan):
        entries: Iterable[object] = previous_layout.member_slots
        if not entries:
            entries = previous_layout.bands
    elif isinstance(previous_layout, FleetBand):
        entries = (previous_layout,)
    elif isinstance(previous_layout, Mapping):
        entries = previous_layout.get("member_slots", previous_layout.get("bands", ()))
    elif isinstance(previous_layout, Sequence) and not isinstance(
        previous_layout, (str, bytes, bytearray)
    ):
        entries = previous_layout
    else:
        raise FleetBandPlanningError("previous_layout is not a FleetPlan or band sequence")

    slots: dict[str, tuple[int, int]] = {}
    occupied: set[int] = set()
    for entry in entries:
        if isinstance(entry, FleetBand):
            identity = entry.identity
            start, end = entry.led_start, entry.led_end
        elif isinstance(entry, Mapping):
            identity = entry.get("identity")
            start = entry.get("led_start", entry.get("start"))
            end = entry.get("led_end", entry.get("end"))
        else:
            try:
                identity, start, end = entry  # type: ignore[misc]
            except (TypeError, ValueError) as exc:
                raise FleetBandPlanningError("previous_layout contains an invalid slot") from exc
        if not isinstance(identity, str) or not identity:
            # A shared band has no identity.  Its member_slots ledger, when
            # available, is the only useful sticky source.
            continue
        if type(start) is not int or type(end) is not int:
            raise FleetBandPlanningError("previous_layout LED bounds must be integers")
        if not (0 <= start < end <= led_count):
            raise FleetBandPlanningError("previous_layout LED bounds are out of range")
        if identity in slots or any(index in occupied for index in range(start, end)):
            raise FleetBandPlanningError("previous_layout contains overlapping identity slots")
        slots[identity] = (start, end)
        occupied.update(range(start, end))
    return slots


def _screen_bounds(start: int, end: int, *, led_count: int, width: float) -> tuple[float, float]:
    return (width * start / led_count, width * end / led_count)


def _refused(
    reason: str,
    *,
    led_count: int = LED_COUNT,
    screen_bar_width: float = DEFAULT_SCREEN_BAR_WIDTH,
) -> FleetPlan:
    return FleetPlan(
        mode="refused",
        refusal=reason,
        led_count=led_count,
        screen_bar_width=screen_bar_width,
    )


def plan_fleet_bands(
    members: Iterable[object],
    *,
    previous_layout: object | None = None,
    led_count: int = LED_COUNT,
    screen_bar_width: float = DEFAULT_SCREEN_BAR_WIDTH,
) -> FleetPlan:
    """Plan a deterministic, sticky fleet layout.

    The planner is bounded to eight member bands because every accepted
    member must own at least one LED.  More than eight *main* identities is a
    named refusal, never an implicit merge.  Worker rows do not count toward
    that limit.  ``previous_layout`` may be a prior :class:`FleetPlan`, a
    sequence of :class:`FleetBand` values, or a sequence of
    ``(identity, led_start, led_end)`` tuples.
    """
    if type(led_count) is not int or led_count < 1 or led_count > LED_COUNT:
        return _refused("unsupported_led_count", led_count=led_count)
    if isinstance(screen_bar_width, bool) or not isinstance(screen_bar_width, (int, float)):
        return _refused("invalid_screen_bar_width", led_count=led_count)
    width = float(screen_bar_width)
    if not math.isfinite(width) or width <= 0.0:
        return _refused("invalid_screen_bar_width", led_count=led_count)

    try:
        rows = _collect_members(members)
        previous = _previous_slots(previous_layout, led_count)
    except FleetBandPlanningError as exc:
        return _refused(str(exc), led_count=led_count, screen_bar_width=width)

    # Exclude workers before identity or state processing.  This is the
    # source-level no-inflation guarantee: a fan-out cannot create bands.
    main_rows = tuple(row for row in rows if row.is_main and not row.is_worker)
    by_identity: dict[str, FleetMember] = {}
    for row in main_rows:
        try:
            identity = row.stable_identity
            semantic_key = _semantic_key(row.semantic)
        except FleetBandPlanningError as exc:
            return _refused(str(exc), led_count=led_count, screen_bar_width=width)
        prior = by_identity.get(identity)
        if prior is not None:
            if _semantic_key(prior.semantic) != semantic_key:
                return _refused(
                    "conflicting_main_rows_for_identity",
                    led_count=led_count,
                    screen_bar_width=width,
                )
            continue
        by_identity[identity] = row

    if len(by_identity) > led_count:
        return _refused("fleet_member_overflow", led_count=led_count, screen_bar_width=width)
    if not by_identity:
        return FleetPlan(
            mode="empty",
            led_count=led_count,
            screen_bar_width=width,
        )

    current = tuple(by_identity)
    # Compare normalized semantic keys, not display colors or lifecycle
    # labels.  A shared effect is valid only when every retained main member
    # genuinely has the same semantic state.
    semantic_keys = {_semantic_key(by_identity[identity].semantic) for identity in current}
    shared = len(semantic_keys) == 1

    # Preserve exact intervals when the fleet membership itself is unchanged.
    # For additions/removals, retain relative old order and allocate the new
    # balanced partition in that order.  This keeps surviving neighbours from
    # being sorted by a newly arriving identity.
    old_current = [identity for identity in previous if identity in by_identity]
    old_current.sort(key=lambda identity: (previous[identity][0], identity))
    newcomers = sorted(identity for identity in current if identity not in previous)
    order = tuple(old_current + newcomers)
    same_membership = set(previous) == set(current) and len(previous) == len(current)
    if same_membership and all(identity in previous for identity in current):
        slots = {identity: previous[identity] for identity in current}
    else:
        base, remainder = divmod(led_count, len(order))
        slots = {}
        cursor = 0
        for index, identity in enumerate(order):
            length = base + (1 if index < remainder else 0)
            slots[identity] = (cursor, cursor + length)
            cursor += length

    member_slots = tuple(
        (identity, slots[identity][0], slots[identity][1])
        for identity in sorted(slots, key=lambda key: (slots[key][0], key))
    )

    if shared:
        semantic = by_identity[order[0]].semantic
        full_start, full_end = _screen_bounds(0, led_count, led_count=led_count, width=width)
        return FleetPlan(
            mode="shared",
            bands=(
                FleetBand(
                    identity=None,
                    semantic=semantic,
                    led_start=0,
                    led_end=led_count,
                    screen_start=full_start,
                    screen_end=full_end,
                    shared=True,
                ),
            ),
            member_slots=member_slots,
            led_count=led_count,
            screen_bar_width=width,
            shared_semantic=semantic,
        )

    bands = tuple(
        FleetBand(
            identity=identity,
            semantic=by_identity[identity].semantic,
            led_start=slots[identity][0],
            led_end=slots[identity][1],
            screen_start=_screen_bounds(
                slots[identity][0], slots[identity][1], led_count=led_count, width=width
            )[0],
            screen_end=_screen_bounds(
                slots[identity][0], slots[identity][1], led_count=led_count, width=width
            )[1],
        )
        for identity in sorted(slots, key=lambda key: (slots[key][0], key))
    )
    return FleetPlan(
        mode="segmented",
        bands=bands,
        member_slots=member_slots,
        led_count=led_count,
        screen_bar_width=width,
    )


FleetLayout = FleetPlan


__all__ = [
    "DEFAULT_SCREEN_BAR_WIDTH",
    "LED_COUNT",
    "MAX_FLEET_MEMBERS",
    "MAX_INPUT_ROWS",
    "FleetBand",
    "FleetBandPlanningError",
    "FleetLayout",
    "FleetMember",
    "FleetPlan",
    "plan_fleet_bands",
]
