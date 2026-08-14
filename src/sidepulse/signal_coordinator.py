from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from .attention import AttentionProjection, TransientSignal
from .presentation_policy import (
    FiniteCue,
    FiniteCueBudget,
    FiniteCueState,
    GlanceSemantic,
    valid_finite_cue,
    valid_presentation_time,
)

DEFAULT_FAILURE_CYCLE_SECONDS = 0.9
DEFAULT_MAX_CONSUMED_KEYS = 256
DEFAULT_CONSUMED_TTL_SECONDS = 60.0 * 60.0
DEFAULT_MAX_SIGNALS_PER_BURST = 2


@dataclass(frozen=True)
class ActiveSignal:
    signal: TransientSignal
    started_at: float
    ends_at: float


class FiniteSignalCoordinator:
    """Coordinates finite failure cues without replay or unbounded queues."""

    def __init__(
        self,
        *,
        failure_cycle_seconds: float = DEFAULT_FAILURE_CYCLE_SECONDS,
        max_consumed_keys: int = DEFAULT_MAX_CONSUMED_KEYS,
        consumed_ttl_seconds: float = DEFAULT_CONSUMED_TTL_SECONDS,
        max_signals_per_burst: int = DEFAULT_MAX_SIGNALS_PER_BURST,
    ) -> None:
        self.failure_cycle_seconds = max(0.001, float(failure_cycle_seconds))
        self.max_consumed_keys = max(1, int(max_consumed_keys))
        self.consumed_ttl_seconds = max(0.001, float(consumed_ttl_seconds))
        self.max_signals_per_burst = max(1, int(max_signals_per_burst))
        self._consumed: OrderedDict[str, float] = OrderedDict()
        self._visible_event_keys: set[str] = set()
        self._active: ActiveSignal | None = None
        self._pending: TransientSignal | None = None
        self._signals_started_in_burst = 0

    @property
    def consumed_event_keys(self) -> tuple[str, ...]:
        return tuple(self._consumed)

    @property
    def next_deadline(self) -> float | None:
        return self._active.ends_at if self._active is not None else None

    def establish_watermark(
        self,
        projection: AttentionProjection,
        now: float = 0.0,
    ) -> None:
        """Consume restored failures without playing them on process start."""
        if not valid_presentation_time(now):
            return
        self._prune_consumed(now)
        signals = tuple(
            signal
            for signal in projection.transient_signals
            if _valid_transient_signal(signal)
        )
        for signal in signals:
            self._consume(signal.event_key, now)
        self._visible_event_keys = {signal.event_key for signal in signals}
        self._active = None
        self._pending = None
        self._signals_started_in_burst = 0

    def observe(self, projection: AttentionProjection, now: float) -> bool:
        if not valid_presentation_time(now):
            return False
        self._prune_consumed(now)
        before = (self._active, self._pending)
        fresh: list[TransientSignal] = []
        current_visible = {
            signal.event_key
            for signal in projection.transient_signals
            if _valid_transient_signal(signal)
        }
        for signal in projection.transient_signals:
            if not _valid_transient_signal(signal):
                continue
            if (
                signal.event_key in self._visible_event_keys
                or signal.event_key in self._consumed
            ):
                continue
            self._consume(signal.event_key, now)
            fresh.append(signal)
        self._visible_event_keys = current_visible

        if projection.actionable_attention:
            self._active = None
            self._pending = None
            self._signals_started_in_burst = 0
            return before != (self._active, self._pending)

        self.active(now)
        if self._active is None and fresh:
            self._start(fresh[0], now)
            fresh = fresh[1:]

        if (
            self._active is not None
            and self._pending is None
            and fresh
            and self._signals_started_in_burst < self.max_signals_per_burst
        ):
            self._pending = fresh[0]

        return before != (self._active, self._pending)

    def active(self, now: float) -> ActiveSignal | None:
        if not valid_presentation_time(now):
            return self._active
        if self._active is None:
            return None
        if now < self._active.ends_at:
            return self._active

        self._active = None
        if (
            self._pending is not None
            and self._signals_started_in_burst < self.max_signals_per_burst
        ):
            pending = self._pending
            self._pending = None
            self._start(pending, now)
            return self._active

        self._pending = None
        self._signals_started_in_burst = 0
        return None

    def _start(self, signal: TransientSignal, now: float) -> None:
        self._signals_started_in_burst += 1
        self._active = ActiveSignal(
            signal=signal,
            started_at=now,
            ends_at=now + self.failure_cycle_seconds * signal.repetitions,
        )

    def _consume(self, event_key: str, now: float) -> None:
        self._consumed[event_key] = now
        self._consumed.move_to_end(event_key)
        while len(self._consumed) > self.max_consumed_keys:
            self._consumed.popitem(last=False)

    def _prune_consumed(self, now: float) -> None:
        oldest_allowed = now - self.consumed_ttl_seconds
        while self._consumed:
            _event_key, consumed_at = next(iter(self._consumed.items()))
            if consumed_at >= oldest_allowed:
                break
            self._consumed.popitem(last=False)


def _valid_transient_signal(signal: object) -> bool:
    return (
        isinstance(signal, TransientSignal)
        and type(signal.repetitions) is int
        and 1 <= signal.repetitions <= DEFAULT_MAX_SIGNALS_PER_BURST
    )


@dataclass(frozen=True, slots=True)
class _ActiveCue:
    cue: FiniteCue
    started_at: float
    ends_at: float


_HARD_CUE_BUDGET = FiniteCueBudget()
_CUE_PRIORITY = {
    GlanceSemantic.ATTENTION: 0,
    GlanceSemantic.FRESH_FAILURE: 1,
    GlanceSemantic.FRESH_COMPLETION: 2,
}


class FiniteCueCoordinator:
    """Own one finite cue, one pending cue, and a bounded replay watermark."""

    def __init__(self, budget: FiniteCueBudget = _HARD_CUE_BUDGET) -> None:
        # The public budget is a typed statement of hard limits, not a route to
        # weaken them. Invalid or more permissive values fail to the defaults.
        self.budget = (
            budget
            if isinstance(budget, FiniteCueBudget)
            and budget == _HARD_CUE_BUDGET
            else _HARD_CUE_BUDGET
        )
        self._consumed: OrderedDict[str, float] = OrderedDict()
        self._visible_event_keys: set[str] = set()
        self._active: _ActiveCue | None = None
        self._pending: FiniteCue | None = None
        self._overflowed = False
        self._last_now: float | None = None

    @property
    def consumed_event_keys(self) -> tuple[str, ...]:
        return tuple(self._consumed)

    def establish_watermark(
        self,
        cues: tuple[FiniteCue, ...],
        *,
        now: float,
    ) -> FiniteCueState:
        if not self._accept_now(now):
            return self._state()
        valid, overflowed = self._validated_candidates(cues)
        for cue in valid:
            self._consume(cue.event_key, now)
        self._visible_event_keys = {
            cue.event_key for cue in valid[: self.budget.max_consumed_keys]
        }
        self._active = None
        self._pending = None
        self._overflowed = overflowed or len(valid) > (
            self.budget.max_active + self.budget.max_pending
        )
        return self._state()

    def observe(
        self,
        cues: tuple[FiniteCue, ...],
        *,
        now: float,
        play_motion: bool,
    ) -> FiniteCueState:
        if type(play_motion) is not bool or not self._accept_now(now):
            return self._state()

        valid, input_overflowed = self._validated_candidates(cues)
        current_visible = {
            cue.event_key for cue in valid[: self.budget.max_consumed_keys]
        }
        fresh: list[FiniteCue] = []
        for cue in valid:
            if (
                cue.event_key in self._visible_event_keys
                or cue.event_key in self._consumed
            ):
                continue
            self._consume(cue.event_key, now)
            fresh.append(cue)
        self._visible_event_keys = current_visible

        if not play_motion:
            self._active = None
            self._pending = None
            self._overflowed = input_overflowed or len(fresh) > 0
            return self._state()

        self._advance_validated(now)
        overflowed = input_overflowed
        for cue in fresh:
            if self._active is None:
                self._start(cue, now)
                continue
            if self._pending is None:
                self._pending = cue
                continue
            if self._priority(cue) < self._priority(self._pending):
                self._pending = cue
            overflowed = True
        self._overflowed = overflowed
        return self._state()

    def advance(self, *, now: float) -> FiniteCueState:
        if not self._accept_now(now):
            return self._state()
        self._advance_validated(now)
        return self._state()

    def _advance_validated(self, now: float) -> None:
        while self._active is not None and now >= self._active.ends_at:
            prior_deadline = self._active.ends_at
            self._active = None
            if self._pending is None:
                break
            pending = self._pending
            self._pending = None
            self._start(pending, prior_deadline)
    def _start(self, cue: FiniteCue, started_at: float) -> None:
        self._active = _ActiveCue(
            cue=cue,
            started_at=started_at,
            ends_at=started_at + cue.repetitions * cue.duration_seconds,
        )

    def _validated_candidates(
        self,
        cues: object,
    ) -> tuple[list[FiniteCue], bool]:
        if not isinstance(cues, tuple):
            return [], False
        unique: list[FiniteCue] = []
        seen: set[str] = set()
        overflowed = False
        for cue in cues:
            if not valid_finite_cue(cue) or cue.event_key in seen:
                continue
            seen.add(cue.event_key)
            if len(unique) > self.budget.max_consumed_keys:
                overflowed = True
                break
            unique.append(cue)
        unique.sort(key=self._priority)
        return (
            unique,
            overflowed
            or len(unique) > (self.budget.max_active + self.budget.max_pending),
        )

    def _consume(self, event_key: str, now: float) -> None:
        self._consumed[event_key] = now
        self._consumed.move_to_end(event_key)
        while len(self._consumed) > self.budget.max_consumed_keys:
            self._consumed.popitem(last=False)

    def _accept_now(self, now: object) -> bool:
        if not valid_presentation_time(now):
            return False
        value = float(now)
        if self._last_now is not None and value < self._last_now:
            return False
        self._last_now = value
        return True

    def _state(self) -> FiniteCueState:
        return FiniteCueState(
            active=self._active.cue if self._active is not None else None,
            pending=self._pending,
            next_deadline=(
                self._active.ends_at if self._active is not None else None
            ),
            overflowed=self._overflowed,
        )

    @staticmethod
    def _priority(cue: FiniteCue) -> int:
        return _CUE_PRIORITY[cue.semantic]
