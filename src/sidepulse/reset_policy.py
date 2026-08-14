"""Pure reset normalization, countdown, and refresh-boundary planning."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .capacity_types import (
    QuotaEffect,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SampleDisposition,
    SourceKey,
)
from .provider_facts import (
    ProviderQuotaWindow,
    WatermarkOrder,
    compare_watermarks,
)

MAX_RESET_FUTURE_SECONDS = 366 * 24 * 60 * 60
MILLISECONDS_EPOCH_THRESHOLD = 100_000_000_000
DEFAULT_RESET_GRACE_SECONDS = 2.0
MINIMUM_RESET_REFRESH_DELAY_SECONDS = 5.0
COUNTDOWN_PRECISION_DIGITS = 6


@dataclass(frozen=True, slots=True)
class ResetBoundaryPlan:
    deadline: float | None
    source_keys: tuple[SourceKey, ...]
    lane_keys: tuple[QuotaLaneKey, ...]
    _compatibility_aliases: tuple[tuple[str, ...], ...] = field(
        default=(),
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not (
            (self.deadline is None or _finite_number(self.deadline) is not None)
            and type(self.source_keys) is tuple
            and all(type(key) is SourceKey for key in self.source_keys)
            and len(self.source_keys) == len(set(self.source_keys))
            and type(self.lane_keys) is tuple
            and all(type(key) is QuotaLaneKey for key in self.lane_keys)
            and len(self.lane_keys) == len(set(self.lane_keys))
            and {key.source for key in self.lane_keys} == set(self.source_keys)
            and ((self.deadline is None) == (not self.lane_keys))
        ):
            raise ValueError("invalid reset boundary plan")
        if self.deadline is not None:
            object.__setattr__(self, "deadline", float(self.deadline))

    @property
    def provider_ids(self) -> tuple[str, ...]:
        """Temporary provider-only projection for the legacy controller."""
        return tuple(dict.fromkeys(key.provider_id for key in self.source_keys))

    @property
    def boundary_keys(self) -> tuple[str, ...]:
        """Temporary structured attempt projection, never an authority key."""
        return _CompatibilityBoundaryKeys(
            tuple(_lane_attempt_key(key) for key in self.lane_keys),
            self._compatibility_aliases,
        )


class _CompatibilityBoundaryKeys(tuple):
    """Tuple-compatible bridge for already scheduled provider-string plans."""

    def __new__(cls, values, aliases):
        instance = super().__new__(cls, values)
        instance.aliases = tuple(aliases)
        return instance

    def __eq__(self, other) -> bool:
        return tuple.__eq__(self, other) or other in self.aliases

    def __ne__(self, other) -> bool:
        return not self.__eq__(other)

    __hash__ = tuple.__hash__


@dataclass(frozen=True, slots=True)
class ResetCountdown:
    """Typed reset truth derived from one reset fact and one wall-clock value."""

    state: ResetState
    remaining_seconds: float | None
    days: int | None
    hours: int | None
    minutes: int | None


@dataclass(frozen=True, slots=True)
class ResetContinuityIdentity:
    lane_key: QuotaLaneKey
    account_discriminator: str
    source_generation: int


@dataclass(frozen=True, slots=True)
class ResetContinuitySample:
    identity: ResetContinuityIdentity
    observed_at: float
    remaining: float | None
    reset: ResetFact


@dataclass(frozen=True, slots=True)
class ResetContinuityState:
    confirmed: ResetContinuitySample | None = None
    pending: ResetContinuitySample | None = None
    identity: ResetContinuityIdentity | None = None
    latest_observed_at: float | None = None


@dataclass(frozen=True, slots=True)
class ResetContinuityDecision:
    state: ResetContinuityState
    reset: ResetFact
    remaining: float | None
    disposition: SampleDisposition
    forecast_eligible: bool
    reason_code: str


def accept_newer_quota_windows(
    previous: tuple[ProviderQuotaWindow, ...],
    incoming: tuple[ProviderQuotaWindow, ...],
) -> tuple[ProviderQuotaWindow, ...]:
    """Merge quota lanes only when exact source watermark authority advances."""
    if not (
        type(previous) is tuple
        and type(incoming) is tuple
        and len(previous) <= 128
        and len(incoming) <= 128
        and all(type(window) is ProviderQuotaWindow for window in (*previous, *incoming))
    ):
        raise ValueError("invalid quota window batch")
    accepted: dict[QuotaLaneKey, ProviderQuotaWindow] = {}
    for window in previous:
        existing = accepted.get(window.lane_key)
        if existing is None or compare_watermarks(
            window.watermark,
            existing.watermark,
        ) is WatermarkOrder.NEWER:
            accepted[window.lane_key] = window
    for window in incoming:
        existing = accepted.get(window.lane_key)
        if existing is None or compare_watermarks(
            window.watermark,
            existing.watermark,
        ) is WatermarkOrder.NEWER:
            accepted[window.lane_key] = window
    return tuple(accepted[key] for key in sorted(accepted))


def derive_reset_countdown(reset: ResetFact, *, now: float) -> ResetCountdown:
    """Derive typed countdown parts without reading a process clock.

    Provider reset states that do not authorize a countdown remain unchanged.
    A previously-future epoch that has passed becomes ``DUE`` at presentation
    time. The boundary is never rolled forward.
    """
    if not isinstance(reset, ResetFact):
        raise TypeError("reset must be a ResetFact")
    current = _finite_number(now)
    if current is None:
        raise ValueError("now must be finite")
    if reset.state not in {ResetState.FUTURE, ResetState.DUE}:
        return ResetCountdown(reset.state, None, None, None, None)
    if reset.reset_epoch is None or reset.reset_epoch <= current:
        return ResetCountdown(ResetState.DUE, 0.0, 0, 0, 0)

    remaining = round(reset.reset_epoch - current, COUNTDOWN_PRECISION_DIGITS)
    whole_minutes = int(math.ceil(remaining / 60.0))
    days, day_minutes = divmod(whole_minutes, 24 * 60)
    hours, minutes = divmod(day_minutes, 60)
    return ResetCountdown(
        ResetState.FUTURE,
        remaining,
        days,
        hours,
        minutes,
    )


def _continuity_sample(
    observation: QuotaLaneObservation,
    *,
    source_generation: int,
) -> ResetContinuitySample | None:
    account = observation.account_discriminator
    if account is None or type(source_generation) is not int or source_generation < 0:
        return None
    return ResetContinuitySample(
        identity=ResetContinuityIdentity(
            lane_key=observation.key,
            account_discriminator=account,
            source_generation=source_generation,
        ),
        observed_at=observation.observed_at,
        remaining=observation.value.remaining,
        reset=observation.reset,
    )


def _decision(
    state: ResetContinuityState,
    observation: QuotaLaneObservation,
    disposition: SampleDisposition,
    reason_code: str,
    *,
    reset: ResetFact | None = None,
) -> ResetContinuityDecision:
    result_reset = reset or observation.reset
    eligible = (
        disposition is SampleDisposition.ACCEPTED
        and result_reset.state is ResetState.FUTURE
        and observation.value.remaining is not None
    )
    return ResetContinuityDecision(
        state=state,
        reset=result_reset,
        remaining=observation.value.remaining,
        disposition=disposition,
        forecast_eligible=eligible,
        reason_code=reason_code,
    )


def _disputed_reset(reset: ResetFact) -> ResetFact:
    return ResetFact(
        state=ResetState.DISPUTED,
        reset_epoch=reset.reset_epoch,
        window_minutes=reset.window_minutes,
        observed_at=reset.observed_at,
    )


def _with_latest(
    state: ResetContinuityState,
    sample: ResetContinuitySample,
) -> ResetContinuityState:
    return ResetContinuityState(
        confirmed=state.confirmed,
        pending=state.pending,
        identity=state.identity or sample.identity,
        latest_observed_at=sample.observed_at,
    )


def _corroborates_pending(
    confirmed: ResetContinuitySample,
    pending: ResetContinuitySample,
    candidate: ResetContinuitySample,
) -> bool:
    if pending.identity != candidate.identity:
        return False
    if pending.reset.reset_epoch != candidate.reset.reset_epoch:
        return False
    if candidate.observed_at <= pending.observed_at:
        return False
    if pending.remaining is None or candidate.remaining is None:
        return pending.remaining == candidate.remaining
    if candidate.remaining > pending.remaining:
        return False
    disagreement = _continuity_disagreement(confirmed, pending)
    if disagreement == "remaining_recovered_without_reset":
        return (
            confirmed.remaining is not None
            and candidate.remaining > confirmed.remaining
        )
    return disagreement == "reset_advance_without_recovery"


def _corroborates_unconfirmed(
    pending: ResetContinuitySample,
    candidate: ResetContinuitySample,
) -> bool:
    if pending.identity != candidate.identity:
        return False
    if pending.reset.reset_epoch != candidate.reset.reset_epoch:
        return False
    if candidate.observed_at <= pending.observed_at:
        return False
    if pending.remaining is None or candidate.remaining is None:
        return pending.remaining == candidate.remaining
    return candidate.remaining <= pending.remaining


def _continuity_disagreement(
    confirmed: ResetContinuitySample,
    candidate: ResetContinuitySample,
) -> str | None:
    previous_epoch = confirmed.reset.reset_epoch
    candidate_epoch = candidate.reset.reset_epoch
    if previous_epoch is None or candidate_epoch is None:
        return None
    if candidate_epoch < previous_epoch:
        return "reset_epoch_moved_backward"

    previous_remaining = confirmed.remaining
    candidate_remaining = candidate.remaining
    if previous_remaining is None or candidate_remaining is None:
        return None
    recovered = candidate_remaining > previous_remaining
    advanced = candidate_epoch > previous_epoch
    if advanced and not recovered:
        return "reset_advance_without_recovery"
    if recovered and not advanced:
        return "remaining_recovered_without_reset"
    return None


def evaluate_reset_continuity(
    previous: ResetContinuityState | None,
    observation: QuotaLaneObservation,
    *,
    source_generation: int,
) -> ResetContinuityDecision:
    """Validate one reset observation within an exact identity scope.

    Disputes affect reset and forecast authority only. The candidate's current
    remaining value is always returned for truthful present-capacity display.
    """
    if not isinstance(observation, QuotaLaneObservation):
        raise TypeError("observation must be a QuotaLaneObservation")
    state = previous if isinstance(previous, ResetContinuityState) else ResetContinuityState()
    sample = _continuity_sample(observation, source_generation=source_generation)
    if sample is None:
        if observation.account_discriminator is None:
            disposition = SampleDisposition.IDENTITY_AMBIGUOUS
            reason = "reset_identity_unavailable"
        else:
            disposition = SampleDisposition.INVALID
            reason = "reset_source_generation_invalid"
        return _decision(state, observation, disposition, reason)

    confirmed = state.confirmed
    scope_identity = state.identity or (
        confirmed.identity if confirmed is not None else None
    )
    if (
        state.latest_observed_at is not None
        and sample.observed_at < state.latest_observed_at
    ):
        return _decision(
            state,
            observation,
            SampleDisposition.OUT_OF_ORDER,
            "reset_observation_out_of_order",
        )
    if scope_identity is not None and sample.identity != scope_identity:
        reason = (
            "reset_source_generation_changed"
            if (
                sample.identity.lane_key == scope_identity.lane_key
                and sample.identity.account_discriminator
                == scope_identity.account_discriminator
            )
            else "reset_identity_changed"
        )
        return _decision(
            ResetContinuityState(
                pending=sample,
                identity=sample.identity,
                latest_observed_at=sample.observed_at,
            ),
            observation,
            SampleDisposition.IDENTITY_AMBIGUOUS,
            reason,
        )

    if observation.reset.state is ResetState.DISPUTED:
        return _decision(
            _with_latest(state, sample),
            observation,
            SampleDisposition.RESET_DISPUTED,
            "reset_source_disputed",
        )
    if observation.reset.state is ResetState.STALE:
        return _decision(
            _with_latest(state, sample),
            observation,
            SampleDisposition.SOURCE_STALE,
            "reset_source_stale",
        )
    if observation.reset.state in {ResetState.UNKNOWN, ResetState.UNAVAILABLE}:
        return _decision(
            _with_latest(state, sample),
            observation,
            SampleDisposition.ACCEPTED,
            f"reset_{observation.reset.state.value}",
        )

    if confirmed is None and state.pending is not None:
        if _corroborates_unconfirmed(state.pending, sample):
            return _decision(
                ResetContinuityState(
                    confirmed=sample,
                    identity=sample.identity,
                    latest_observed_at=sample.observed_at,
                ),
                observation,
                SampleDisposition.ACCEPTED,
                "reset_identity_corroborated",
            )
        return _decision(
            ResetContinuityState(
                pending=sample,
                identity=sample.identity,
                latest_observed_at=sample.observed_at,
            ),
            observation,
            SampleDisposition.IDENTITY_AMBIGUOUS,
            "reset_identity_unconfirmed",
        )

    if confirmed is None:
        return _decision(
            ResetContinuityState(
                confirmed=sample,
                identity=sample.identity,
                latest_observed_at=sample.observed_at,
            ),
            observation,
            SampleDisposition.ACCEPTED,
            "reset_baseline_established",
        )

    latest = state.pending or confirmed
    if state.latest_observed_at == sample.observed_at:
        disposition = (
            SampleDisposition.DUPLICATE
            if sample == latest
            else SampleDisposition.OUT_OF_ORDER
        )
        reason = (
            "reset_observation_duplicate"
            if disposition is SampleDisposition.DUPLICATE
            else "reset_observation_out_of_order"
        )
        return _decision(state, observation, disposition, reason)

    if state.pending is not None and _corroborates_pending(
        confirmed,
        state.pending,
        sample,
    ):
        return _decision(
            ResetContinuityState(
                confirmed=sample,
                identity=sample.identity,
                latest_observed_at=sample.observed_at,
            ),
            observation,
            SampleDisposition.ACCEPTED,
            "reset_cycle_corroborated",
        )

    disagreement = _continuity_disagreement(confirmed, sample)
    if disagreement == "reset_epoch_moved_backward":
        return _decision(
            _with_latest(state, sample),
            observation,
            SampleDisposition.RESET_DISPUTED,
            disagreement,
            reset=_disputed_reset(observation.reset),
        )
    if disagreement is not None:
        return _decision(
            ResetContinuityState(
                confirmed=confirmed,
                pending=sample,
                identity=sample.identity,
                latest_observed_at=sample.observed_at,
            ),
            observation,
            SampleDisposition.RESET_DISPUTED,
            disagreement,
            reset=_disputed_reset(observation.reset),
        )

    previous_epoch = confirmed.reset.reset_epoch
    cycle_advanced = (
        previous_epoch is not None
        and sample.reset.reset_epoch is not None
        and sample.reset.reset_epoch > previous_epoch
    )
    reason = "reset_cycle_confirmed" if cycle_advanced else "reset_continuity_confirmed"
    return _decision(
        ResetContinuityState(
            confirmed=sample,
            identity=sample.identity,
            latest_observed_at=sample.observed_at,
        ),
        observation,
        SampleDisposition.ACCEPTED,
        reason,
    )


def _finite_number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_reset_epoch(value, *, now: float) -> float | None:
    """Normalize one plausible future reset to Unix epoch seconds."""
    now_epoch = _finite_number(now)
    if now_epoch is None:
        return None

    epoch = _finite_number(value)
    if epoch is not None:
        if epoch >= MILLISECONDS_EPOCH_THRESHOLD:
            epoch /= 1_000.0
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        try:
            epoch = parsed.timestamp()
        except (OSError, OverflowError, ValueError):
            return None
    else:
        return None

    if not math.isfinite(epoch):
        return None
    if epoch <= now_epoch:
        return None
    if epoch - now_epoch > MAX_RESET_FUTURE_SECONDS:
        return None
    return epoch


def format_reset_countdown(
    reset_epoch: float | None,
    *,
    now: float,
) -> str | None:
    """Return a compact future-only countdown for a normalized reset."""
    reset = _finite_number(reset_epoch)
    current = _finite_number(now)
    if reset is None or current is None:
        return None
    remaining = reset - current
    if remaining <= 0.0:
        return None
    if remaining < 60.0:
        return "now"

    total_minutes = max(1, int(math.ceil(remaining / 60.0)))
    if total_minutes >= 24 * 60:
        total_hours = int(math.ceil(total_minutes / 60.0))
        days, hours = divmod(total_hours, 24)
        return f"in {days}d {hours}h" if hours else f"in {days}d"

    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"in {hours}h {minutes}m" if minutes else f"in {hours}h"
    return f"in {total_minutes}m"


def next_countdown_deadline(reset_epochs, *, now: float) -> float | None:
    """Return the next exact minute-display transition across reset epochs."""
    current = _finite_number(now)
    if current is None:
        return None
    deadlines: list[float] = []
    for value in reset_epochs or ():
        reset = parse_reset_epoch(value, now=current)
        if reset is None:
            continue
        remaining = reset - current
        if remaining < 60.0:
            deadlines.append(reset)
            continue
        displayed_minutes = int(math.ceil(remaining / 60.0))
        if displayed_minutes >= 24 * 60:
            displayed_hours = int(math.ceil(displayed_minutes / 60.0))
            next_boundary_remaining = (
                (displayed_hours - 1) * 60.0 * 60.0
                if displayed_hours > 24
                else (displayed_minutes - 1) * 60.0
            )
        else:
            next_boundary_remaining = (displayed_minutes - 1) * 60.0
        deadlines.append(reset - next_boundary_remaining)
    return min(deadlines) if deadlines else None


def _mapping_or_attribute(value, name: str, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _provider_groups(provider_windows) -> Iterable[tuple[str, object]]:
    if isinstance(provider_windows, Mapping):
        for provider_id, windows in provider_windows.items():
            nested = _mapping_or_attribute(windows, "windows", windows)
            yield str(provider_id).strip(), nested
        return
    for group in provider_windows or ():
        lane_key = _mapping_or_attribute(group, "lane_key", None)
        if type(lane_key) is QuotaLaneKey:
            yield lane_key.source.provider_id, (group,)
            continue
        provider_id = _mapping_or_attribute(group, "provider_id", "")
        windows = _mapping_or_attribute(group, "windows", ())
        yield str(provider_id).strip(), windows


def _window_semantic_key(window) -> str:
    label = _mapping_or_attribute(window, "label", None)
    if label is None:
        label = _mapping_or_attribute(window, "name", None)
    text = _boundary_component(label)
    if text:
        return text[:80]
    minutes = _finite_number(_mapping_or_attribute(window, "window_minutes", None))
    if minutes is not None and minutes > 0.0:
        return f"{max(1, int(round(minutes)))}m"
    return "limit"


def _normalized_epoch_text(epoch: float) -> str:
    if epoch.is_integer():
        return str(int(epoch))
    return format(epoch, ".6f").rstrip("0").rstrip(".")


def _legacy_lane_key(provider_id: str, window, index: int) -> QuotaLaneKey | None:
    try:
        source = SourceKey(
            provider_id,
            "legacy",
            "unspecified",
            "remote_quota_windows",
        )
        minutes = _finite_number(_mapping_or_attribute(window, "window_minutes", None))
        window_id = (
            f"minutes-{max(1, int(round(minutes)))}"
            if minutes is not None and minutes > 0.0
            else "unspecified"
        )
        return QuotaLaneKey(
            source,
            f"legacy:{index}",
            "unspecified",
            None,
            window_id,
            QuotaEffect.ALL_WORKLOADS,
        )
    except ValueError:
        return None


def _window_lane_key(
    window,
    *,
    provider_id: str,
    index: int,
) -> QuotaLaneKey | None:
    lane_key = _mapping_or_attribute(window, "lane_key", None)
    if type(lane_key) is QuotaLaneKey:
        if provider_id and lane_key.source.provider_id != provider_id:
            return None
        return lane_key
    return _legacy_lane_key(provider_id, window, index)


def _lane_attempt_key(lane_key: QuotaLaneKey) -> str:
    source = lane_key.source
    components = (
        source.provider_id,
        source.adapter_id,
        source.source_instance_id,
        source.capability_id,
        lane_key.opaque_scope,
        lane_key.pool,
        lane_key.model or "unspecified",
        lane_key.window,
        lane_key.effect.value,
    )
    return "|".join(_boundary_component(component) for component in components)


def _legacy_boundary_alias(
    provider_id: str,
    window,
    reset_epoch: float,
) -> str:
    return (
        f"{_boundary_component(provider_id)}|{_window_semantic_key(window)}|"
        f"{_normalized_epoch_text(reset_epoch)}"
    )


def _window_reset_epoch(window, *, now: float) -> float | None:
    typed_state = _mapping_or_attribute(window, "reset_state", None)
    if typed_state is not None and typed_state is not ResetState.FUTURE:
        return None
    for name in ("reset_epoch", "resets_at", "reset_at"):
        value = _mapping_or_attribute(window, name, None)
        if value is not None:
            reset_epoch = parse_reset_epoch(value, now=now)
            if reset_epoch is not None:
                return reset_epoch
    return None


def _boundary_component(value) -> str:
    return " ".join(str(value or "").split()).replace("%", "%25").replace("|", "%7C")


def plan_reset_boundary_refresh(
    provider_windows,
    *,
    now: float,
    normal_refresh_deadline: float | None,
    attempted_boundary_keys: AbstractSet[str] = frozenset(),
    attempted_lane_keys: AbstractSet[QuotaLaneKey] = frozenset(),
    grace_seconds: float = DEFAULT_RESET_GRACE_SECONDS,
    minimum_delay_seconds: float = MINIMUM_RESET_REFRESH_DELAY_SECONDS,
) -> ResetBoundaryPlan:
    """Plan one provider-aware refresh after the earliest untried reset."""
    empty = ResetBoundaryPlan(deadline=None, source_keys=(), lane_keys=())
    current = _finite_number(now)
    if current is None:
        return empty

    attempted = {str(key) for key in attempted_boundary_keys}
    attempted_lanes = {
        key for key in attempted_lane_keys if type(key) is QuotaLaneKey
    }
    candidates: list[tuple[float, QuotaLaneKey, str | None]] = []
    seen_lanes: set[QuotaLaneKey] = set()
    for provider_id, windows in _provider_groups(provider_windows):
        if not provider_id:
            continue
        for index, window in enumerate(windows or ()):
            reset_epoch = _window_reset_epoch(window, now=current)
            if reset_epoch is None:
                continue
            lane_key = _window_lane_key(
                window,
                provider_id=provider_id,
                index=index,
            )
            if lane_key is None:
                continue
            attempt_key = _lane_attempt_key(lane_key)
            if (
                lane_key in attempted_lanes
                or attempt_key in attempted
                or lane_key in seen_lanes
            ):
                continue
            seen_lanes.add(lane_key)
            legacy_alias = (
                _legacy_boundary_alias(provider_id, window, reset_epoch)
                if lane_key.opaque_scope.startswith("legacy:")
                else None
            )
            candidates.append((reset_epoch, lane_key, legacy_alias))
    if not candidates:
        return empty

    earliest_epoch = min(candidate[0] for candidate in candidates)
    boundary = [candidate for candidate in candidates if candidate[0] == earliest_epoch]
    grace = _finite_number(grace_seconds)
    grace = max(0.0, grace if grace is not None else DEFAULT_RESET_GRACE_SECONDS)
    minimum_delay = _finite_number(minimum_delay_seconds)
    minimum_delay = max(
        MINIMUM_RESET_REFRESH_DELAY_SECONDS,
        minimum_delay
        if minimum_delay is not None
        else MINIMUM_RESET_REFRESH_DELAY_SECONDS,
    )
    deadline = max(earliest_epoch + grace, current + minimum_delay)

    if normal_refresh_deadline is not None:
        normal_deadline = _finite_number(normal_refresh_deadline)
        if normal_deadline is None or normal_deadline <= deadline:
            return empty

    source_keys: list[SourceKey] = []
    seen_sources: set[SourceKey] = set()
    lane_keys: list[QuotaLaneKey] = []
    compatibility_alias: list[str] = []
    for _, lane_key, legacy_alias in boundary:
        lane_keys.append(lane_key)
        if legacy_alias is not None:
            compatibility_alias.append(legacy_alias)
        if lane_key.source not in seen_sources:
            seen_sources.add(lane_key.source)
            source_keys.append(lane_key.source)
    plan = ResetBoundaryPlan(
        deadline=deadline,
        source_keys=tuple(source_keys),
        lane_keys=tuple(lane_keys),
    )
    if len(compatibility_alias) == len(lane_keys):
        object.__setattr__(
            plan,
            "_compatibility_aliases",
            (tuple(compatibility_alias),),
        )
    return plan
