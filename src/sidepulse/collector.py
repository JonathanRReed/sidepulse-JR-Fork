"""Compatibility facade with bounded supplemental integration statuses."""

from __future__ import annotations

import re
import sys
from itertools import islice
from types import MappingProxyType, ModuleType

from . import _collector_legacy as _legacy
from .models import AgentStatus

MAX_EXTERNAL_STATUS_SOURCES = 16
MAX_EXTERNAL_STATUSES_PER_SOURCE = 1_024
MAX_EXTERNAL_STATUSES_TOTAL = 4_096
_EXTERNAL_SOURCE_ID = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_LegacyLiveAgentMonitor = _legacy.LiveAgentMonitor


class LiveAgentMonitor(_LegacyLiveAgentMonitor):
    """Canonical monitor with replaceable, read-only integration projections."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._external_statuses_by_source: dict[str, tuple[AgentStatus, ...]] = {}

    def replace_external_statuses(
        self,
        source_id: str,
        statuses,
    ) -> None:
        """Atomically replace one integration's bounded supplemental projection."""
        if (
            type(source_id) is not str
            or _EXTERNAL_SOURCE_ID.fullmatch(source_id) is None
        ):
            raise ValueError("invalid external status source")
        try:
            normalized = tuple(
                islice(iter(statuses), MAX_EXTERNAL_STATUSES_PER_SOURCE + 1)
            )
        except TypeError as exc:
            raise ValueError("invalid external status projection") from exc
        if (
            len(normalized) > MAX_EXTERNAL_STATUSES_PER_SOURCE
            or not all(type(status) is AgentStatus for status in normalized)
            or len({status.agent_id for status in normalized}) != len(normalized)
        ):
            raise ValueError("invalid external status projection")
        with self.lock:
            if source_id not in self._external_statuses_by_source and (
                len(self._external_statuses_by_source) >= MAX_EXTERNAL_STATUS_SOURCES
            ):
                raise ValueError("too many external status sources")
            previous_count = len(self._external_statuses_by_source.get(source_id, ()))
            current_total = sum(
                len(rows) for rows in self._external_statuses_by_source.values()
            )
            if (
                current_total - previous_count + len(normalized)
                > MAX_EXTERNAL_STATUSES_TOTAL
            ):
                raise ValueError("too many external statuses")
            if normalized:
                self._external_statuses_by_source[source_id] = normalized
            else:
                self._external_statuses_by_source.pop(source_id, None)

    def external_statuses_by_source(self) -> dict[str, tuple[AgentStatus, ...]]:
        with self.lock:
            return dict(self._external_statuses_by_source)

    def _external_statuses_locked(self) -> tuple[AgentStatus, ...]:
        return tuple(
            status
            for source_id in sorted(self._external_statuses_by_source)
            for status in self._external_statuses_by_source[source_id]
        )

    def snapshot(self):
        now = _legacy._canonical_datetime(self._clock_sampler().wall_epoch)
        with self.lock:
            state = self.operator_state
            events = self._pending_operator_events
            self._pending_operator_events = ()
            health = self.restore_health
            overlays = MappingProxyType(dict(self._status_overlays_by_work_key))
            supplemental = (
                *self._compatibility_statuses_by_agent_id.values(),
                *self._external_statuses_locked(),
            )
        return _legacy._snapshot_from_operator_state(
            state,
            events=events,
            sources=self.sources,
            collected_at=now,
            restore_health=health,
            status_overlays=overlays,
            supplemental_statuses=tuple(supplemental),
            stale_after_seconds=self.stale_after_seconds,
            tool_running_timeout_seconds=self.tool_running_timeout_seconds,
            completed_visible_seconds=self.completed_visible_seconds,
            idle_visible_seconds=self.idle_visible_seconds,
            post_tool_working_visible_seconds=(
                self.post_tool_working_visible_seconds
            ),
            canonical_projected_uses_age_windows=False,
        )

    def current_statuses_by_key(self) -> dict[str, AgentStatus]:
        now = _legacy._canonical_datetime(self._clock_sampler().wall_epoch)
        with self.lock:
            state = self.operator_state
            health = self.restore_health
            overlays = MappingProxyType(dict(self._status_overlays_by_work_key))
            supplemental = (
                *self._compatibility_statuses_by_agent_id.values(),
                *self._external_statuses_locked(),
            )
        snapshot = _legacy._snapshot_from_operator_state(
            state,
            events=(),
            sources=self.sources,
            collected_at=now,
            restore_health=health,
            status_overlays=overlays,
            supplemental_statuses=tuple(supplemental),
            stale_after_seconds=self.stale_after_seconds,
            tool_running_timeout_seconds=self.tool_running_timeout_seconds,
            completed_visible_seconds=self.completed_visible_seconds,
            idle_visible_seconds=self.idle_visible_seconds,
            post_tool_working_visible_seconds=(
                self.post_tool_working_visible_seconds
            ),
            canonical_projected_uses_age_windows=False,
        )
        return {
            status.agent_id: status
            for status in (*snapshot.statuses, *snapshot.stale_statuses)
        }


_legacy.LiveAgentMonitor = LiveAgentMonitor

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)


class _CollectorFacade(ModuleType):
    """Forward test monkeypatches to the retained collector implementation."""

    def __getattr__(self, name: str):
        return getattr(_legacy, name)

    def __setattr__(self, name: str, value) -> None:
        if name in {
            "__all__",
            "__class__",
            "__doc__",
            "__file__",
            "__loader__",
            "__name__",
            "__package__",
            "__path__",
            "__spec__",
        } or name.startswith("_facade_"):
            super().__setattr__(name, value)
            return
        setattr(_legacy, name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in {"__all__", "__class__"} or name.startswith("_facade_"):
            super().__delattr__(name)
            return
        if hasattr(_legacy, name):
            delattr(_legacy, name)
        if name in self.__dict__:
            super().__delattr__(name)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(dir(_legacy)))


__all__ = tuple(sorted(name for name in globals() if not name.startswith("_")))
_facade_module = sys.modules[__name__]
_facade_module.__class__ = _CollectorFacade
