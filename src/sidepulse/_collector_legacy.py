from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .capacity_types import SourceKey
from .freshness import bounded_age_seconds, is_recent
from .models import (
    _CODEX_TRANSCRIPT_USAGE_LIMIT_PROVENANCE,
    MODE_PRIORITY,
    AgentMode,
    AgentStatus,
    AggregateStatus,
    HookEvent,
    parse_datetime,
    provider_label,
)
from .operator_state import (
    AcknowledgementEligibility,
    BootIdentifier,
    CanonicalOperatorEvent,
    CanonicalOperatorState,
    CanonicalRequestTruth,
    CanonicalWorkTruth,
    ClockContinuityState,
    ClockContinuityStatus,
    ClockSample,
    RequestPhase,
    SemanticEventKey,
    empty_operator_state,
    reduce_operator_state,
    semantic_event_key_from_payload,
    semantic_event_key_to_payload,
)
from .origin import origin_label_from_payload
from .private_io import (
    atomic_private_write,
    read_private_text,
)
from .provider_adapters import (
    minimize_hook_event,
    normalized_provider_record_from_payload,
    provider_facts_for_record,
)
from .provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderFactBatch,
    ProviderWatermark,
    RequestKey,
    RequestKind,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WatermarkOrder,
    WorkKey,
    WorkLifecycle,
    compare_watermarks,
    request_key_from_payload,
    request_key_to_payload,
    work_key_from_payload,
    work_key_to_payload,
)
from .providers import (
    HOOK_PROVIDERS,
    NegotiatedProviderSource,
    detect_log_path,
    negotiated_provider_sources,
    parse_log_line,
)
from .settings import AgentMonitorSettings, load_settings

CODEX_TRANSCRIPT_PROVIDER = "codex-transcripts"
CLAUDE_TRANSCRIPT_PROVIDER = "claude-transcripts"
CODEX_TRANSCRIPT_MAX_FILES = 12
CODEX_TRANSCRIPT_MAX_LINES = 500
CLAUDE_TRANSCRIPT_MAX_FILES = 24
CLAUDE_TRANSCRIPT_MAX_LINES = 500
# 45s: the rglob behind this walked 2,445 files on a real install, on the
# main thread, at the top of every refresh. New transcript FILES appear
# rarely; new LINES in known files are caught by per-file signatures.
TRANSCRIPT_FILE_LIST_CACHE_SECONDS = 45.0
TRANSCRIPT_RECORDS_CACHE_MAX_ENTRIES = (
    CODEX_TRANSCRIPT_MAX_FILES + CLAUDE_TRANSCRIPT_MAX_FILES
) * 4
TRANSCRIPT_FILE_LIST_CACHE_MAX_ENTRIES = 16
# Keep at most a day of finished sessions: latest.json accumulated every
# session ever seen (118 statuses / 367KB on a real install) and was
# re-serialized on every hook event.
STATUS_RETENTION_SECONDS = 24 * 3600.0
LATEST_STATE_WRITE_INTERVAL_SECONDS = 1.0
# Transcript-derived detail text is capped before it reaches any UI
# surface (T3 caps at 160 and redacts -- long tool output in a menu row
# or notification is noise at best and a leak at worst).
DETAIL_TEXT_CAP = 160
CLAUDE_TRANSCRIPT_MTIME_HEARTBEAT_SKEW_SECONDS = 30.0
CODEX_SESSION_INDEX_MAX_LINES = 5000
COMPLETED_VISIBLE_SECONDS = 20 * 60.0
IDLE_VISIBLE_SECONDS = 0.0
POST_TOOL_WORKING_VISIBLE_SECONDS = 2 * 60.0
CODEX_USAGE_LIMIT_TERMINAL_CLASSIFICATIONS = frozenset({"usage_limit_exceeded"})
LATEST_STATE_MAX_BYTES = 4 * 1_024 * 1_024
MAX_PENDING_OPERATOR_EVENTS = 2_000
_BOOT_EPOCH_BUCKET_SECONDS = 10
_LOCAL_BOOT_IDENTIFIER = BootIdentifier(
    hashlib.sha256(
        str(
            int(
                (time.time() - time.monotonic())
                // _BOOT_EPOCH_BUCKET_SECONDS
            )
        ).encode("ascii")
    ).hexdigest()
)


class RestoreHealth(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    HEALTHY = "healthy"
    MISSING = "missing"
    DEGRADED = "degraded"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SourceSpec:
    provider: str
    path: Path


@dataclass
class StatusMetadata:
    cwd: str | None = None
    title: str | None = None
    origin: str | None = None


@dataclass(frozen=True)
class CachedTranscriptRecords:
    mtime: float
    size: int
    records: tuple[HookEvent, ...]


@dataclass(frozen=True)
class CachedTranscriptFileList:
    expires_at: float
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class CachedCodexSessionIndex:
    path: Path
    mtime: float
    size: int
    titles: dict[str, str]


@dataclass(frozen=True)
class CanonicalStatusOverlay:
    watermark: ProviderWatermark
    status: AgentStatus
    preserve_display_name: bool = False
    preserve_details: bool = False


_codex_session_index_cache: CachedCodexSessionIndex | None = None
_codex_session_index_lock = threading.RLock()


def _default_clock_sample() -> ClockSample:
    now = datetime.now(timezone.utc)
    return ClockSample(
        now.timestamp(),
        time.monotonic(),
        _LOCAL_BOOT_IDENTIFIER,
    )


def _registered_hook_source(provider: str) -> NegotiatedProviderSource | None:
    return next(
        (
            source
            for source in negotiated_provider_sources()
            if source.source_key.provider_id == provider
            and source.source_key.adapter_id == "hooks"
            and source.source_key.capability_id == "live_agent_events"
            and source.observation_invocation_allowed
        ),
        None,
    )


def _batch_for_hook_record(
    record: HookEvent,
    *,
    clock: ClockSample,
) -> ProviderFactBatch | None:
    source = _registered_hook_source(record.provider)
    if source is None:
        return None
    observation_authority = (
        ObservationAuthority.FALLBACK_OBSERVATION
        if record.raw.get("source")
        in {CODEX_TRANSCRIPT_PROVIDER, CLAUDE_TRANSCRIPT_PROVIDER}
        else source.registration.observation_authority
    )
    normalized = minimize_hook_event(
        record,
        source_key=source.source_key,
        contract=source.contract,
        observation_authority=observation_authority,
    )
    return provider_facts_for_record(
        normalized,
        contract=source.contract,
        observation_authority=observation_authority,
        observed_at_epoch=clock.wall_epoch,
    )


def _source_sort_key(source: SourceKey) -> tuple[str, str, str, str]:
    return (
        source.provider_id,
        source.adapter_id,
        source.source_instance_id,
        source.capability_id,
    )


def _batch_sort_key(batch: ProviderFactBatch) -> tuple[object, ...]:
    watermark = batch.watermark
    return (
        _source_sort_key(batch.source_key),
        0 if watermark.sequence is not None else 1,
        -1 if watermark.sequence is None else watermark.sequence,
        watermark.occurred_at_epoch,
        watermark.tie_break_rank,
        watermark.event_token.value,
    )


def _canonical_datetime(epoch: float) -> datetime:
    try:
        return datetime.fromtimestamp(epoch, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return datetime.max.replace(tzinfo=timezone.utc)


def agent_status_from_canonical_work(
    work: CanonicalWorkTruth,
    *,
    overlay: CanonicalStatusOverlay | None = None,
) -> AgentStatus:
    """Project one canonical work row into the temporary AgentStatus facade."""
    if type(work) is not CanonicalWorkTruth:
        raise ValueError("invalid canonical work truth")
    if overlay is not None and type(overlay) is not CanonicalStatusOverlay:
        raise ValueError("invalid canonical status overlay")
    mode, event_name = {
        WorkLifecycle.IDLE: (AgentMode.IDLE_READY, "SessionStart"),
        WorkLifecycle.ACTIVE: (AgentMode.WORKING, "UserPromptSubmit"),
        WorkLifecycle.WAITING: (
            AgentMode.WAITING_FOR_INPUT,
            "PermissionRequest" if work.request_keys else "Waiting",
        ),
        WorkLifecycle.COMPLETED: (AgentMode.COMPLETED, "Stop"),
        WorkLifecycle.FAILED: (AgentMode.BLOCKED_ERROR, "StopFailure"),
        WorkLifecycle.UNKNOWN: (AgentMode.UNKNOWN, "Unknown"),
    }[work.lifecycle]
    provider = work.key.source_key.provider_id
    is_worker = work.parent_key is not None
    matching_overlay = overlay is not None and (
        overlay.watermark == work.watermark
        or overlay.status.updated_at
        >= _canonical_datetime(work.watermark.occurred_at_epoch)
    )
    projected_mode = (
        mode
        if work.request_keys
        else overlay.status.mode
        if matching_overlay
        else mode
    )
    projected_event_name = overlay.status.event_name if matching_overlay else event_name
    projected_updated_at = (
        overlay.status.updated_at
        if matching_overlay
        else _canonical_datetime(work.watermark.occurred_at_epoch)
    )
    session_id = (
        work.parent_key.work_id.value if work.parent_key is not None else work.key.work_id.value
    )
    return AgentStatus(
        provider=provider,
        agent_id=f"{provider}:{'agent' if is_worker else 'session'}:{work.key.work_id.value}",
        display_name=(
            overlay.status.display_name
            if matching_overlay and overlay.preserve_display_name
            else work.safe_label
        ),
        mode=projected_mode,
        updated_at=projected_updated_at,
        event_name=projected_event_name,
        session_id=session_id,
        cwd=None,
        tool_name=(
            overlay.status.tool_name if matching_overlay and overlay.preserve_details else None
        ),
        message=overlay.status.message if matching_overlay and overlay.preserve_details else None,
        origin=overlay.status.origin if matching_overlay and overlay.preserve_details else None,
        stale=work.source_freshness
        not in {SourceFreshness.FRESH, SourceFreshness.PARTIAL}
        and not (
            work.source_freshness is SourceFreshness.RESTORED
            and matching_overlay
        ),
        work_key=work.key,
        request_key=work.request_keys[0] if work.request_keys else None,
    )


def _snapshot_from_operator_state(
    state: CanonicalOperatorState,
    *,
    events: tuple[CanonicalOperatorEvent, ...],
    sources: tuple[SourceSpec, ...],
    collected_at: datetime,
    restore_health: RestoreHealth,
    status_overlays: Mapping[WorkKey, CanonicalStatusOverlay] = MappingProxyType({}),
    supplemental_statuses: tuple[AgentStatus, ...] = (),
    stale_after_seconds: float,
    tool_running_timeout_seconds: float,
    completed_visible_seconds: float,
    idle_visible_seconds: float,
    post_tool_working_visible_seconds: float,
    canonical_projected_uses_age_windows: bool,
) -> MonitorSnapshot:
    projected = tuple(
        agent_status_from_canonical_work(
            work,
            overlay=status_overlays.get(work.key),
        )
        for work in state.works
    )
    projected_by_agent_id = {status.agent_id: status for status in projected}
    merged = _merged_status_candidates(
        (
            *projected,
            *(
                status
                for status in supplemental_statuses
                if (
                    (projected := projected_by_agent_id.get(status.agent_id)) is None
                    or status.priority < projected.priority
                    or (
                        status.updated_at > projected.updated_at
                        and (
                            status.priority <= projected.priority
                            or status.event_name == "Notification"
                        )
                    )
                )
            ),
        )
    )
    fresh: list[AgentStatus] = []
    stale: list[AgentStatus] = []
    for status in merged:
        effective = status_for_snapshot(
            status,
            collected_at,
            post_tool_working_visible_seconds=post_tool_working_visible_seconds,
        )
        projected_status = projected_by_agent_id.get(status.agent_id)
        is_stale = (
            status_is_stale(
                effective,
                collected_at,
                stale_after_seconds=stale_after_seconds,
                tool_running_timeout_seconds=tool_running_timeout_seconds,
                completed_visible_seconds=completed_visible_seconds,
                idle_visible_seconds=idle_visible_seconds,
            )
            if projected_status is not None and canonical_projected_uses_age_windows
            else projected_status.stale
            if projected_status is not None
            else status_is_stale(
                effective,
                collected_at,
                stale_after_seconds=stale_after_seconds,
                tool_running_timeout_seconds=tool_running_timeout_seconds,
                completed_visible_seconds=completed_visible_seconds,
                idle_visible_seconds=idle_visible_seconds,
            )
        )
        current = _replace_stale(effective, is_stale)
        if is_stale:
            stale.append(current)
        else:
            fresh.append(current)

    if any(status_counts_active(status) for status in fresh):
        inactive = [status for status in fresh if not status_counts_active(status)]
        fresh = [status for status in fresh if status_counts_active(status)]
        stale.extend(_replace_stale(status, True) for status in inactive)

    fresh.sort(key=lambda status: (status.priority, -status.updated_at.timestamp()))
    stale.sort(key=lambda status: (status.priority, -status.updated_at.timestamp()))
    aggregate = aggregate_status(tuple(fresh), tuple(stale))
    if not fresh and not stale and restore_health in {
        RestoreHealth.DEGRADED,
        RestoreHealth.CORRUPT,
        RestoreHealth.UNSUPPORTED,
        RestoreHealth.UNAVAILABLE,
    }:
        aggregate = AggregateStatus(AgentMode.UNKNOWN, 0, 0, None)
    return MonitorSnapshot(
        aggregate=aggregate,
        statuses=tuple(fresh),
        stale_statuses=tuple(stale),
        sources=sources,
        collected_at=collected_at,
        operator_state=state,
        operator_events=events,
        restore_health=restore_health,
    )


@dataclass(frozen=True)
class MonitorSnapshot:
    aggregate: AggregateStatus
    statuses: tuple[AgentStatus, ...]
    stale_statuses: tuple[AgentStatus, ...]
    sources: tuple[SourceSpec, ...]
    collected_at: datetime
    operator_state: CanonicalOperatorState | None = None
    operator_events: tuple[CanonicalOperatorEvent, ...] = ()
    restore_health: RestoreHealth = RestoreHealth.NOT_ATTEMPTED

    def to_dict(self) -> dict:
        return {
            "collected_at": self.collected_at.isoformat(),
            "sources": [
                {"provider": source.provider, "path": str(source.path)}
                for source in self.sources
            ],
            "aggregate": self.aggregate.to_dict(self.collected_at),
            "statuses": [
                status.to_dict(self.collected_at) for status in self.statuses
            ],
            "stale_statuses": [
                status.to_dict(self.collected_at) for status in self.stale_statuses
            ],
            "operator_generation": (
                self.operator_state.generation
                if self.operator_state is not None
                else None
            ),
            "operator_event_count": len(self.operator_events),
            "restore_health": self.restore_health.value,
        }


class AgentMonitor:
    def __init__(
        self,
        sources: Iterable[SourceSpec] | None = None,
        stale_after_seconds: float = 3600.0,
        tool_running_timeout_seconds: float = 0.0,
        completed_visible_seconds: float = COMPLETED_VISIBLE_SECONDS,
        idle_visible_seconds: float = IDLE_VISIBLE_SECONDS,
        post_tool_working_visible_seconds: float = POST_TOOL_WORKING_VISIBLE_SECONDS,
        max_lines_per_source: int = 5000,
        transcript_records_cache_max_entries: int = TRANSCRIPT_RECORDS_CACHE_MAX_ENTRIES,
        transcript_file_list_cache_max_entries: int = TRANSCRIPT_FILE_LIST_CACHE_MAX_ENTRIES,
        clock_sampler: Callable[[], ClockSample] = _default_clock_sample,
    ) -> None:
        self.sources = tuple(sources) if sources is not None else default_sources()
        self.stale_after_seconds = stale_after_seconds
        self.tool_running_timeout_seconds = tool_running_timeout_seconds
        self.completed_visible_seconds = completed_visible_seconds
        self.idle_visible_seconds = idle_visible_seconds
        self.post_tool_working_visible_seconds = post_tool_working_visible_seconds
        self.max_lines_per_source = max_lines_per_source
        self.transcript_records_cache_max_entries = max(
            0, transcript_records_cache_max_entries
        )
        self.transcript_file_list_cache_max_entries = max(
            0, transcript_file_list_cache_max_entries
        )
        self._log_records_cache: dict[tuple[str, str, int], CachedTranscriptRecords] = {}
        self._transcript_records_cache: dict[tuple[str, str], CachedTranscriptRecords] = {}
        self._transcript_file_list_cache: dict[
            tuple[str, str, int], CachedTranscriptFileList
        ] = {}
        self._latest_status_signature: tuple[Any, ...] | None = None
        self._latest_statuses_by_key: dict[str, AgentStatus] | None = None
        self._clock_sampler = clock_sampler
        self.operator_state = empty_operator_state()
        self._canonical_signature: tuple[Any, ...] | None = None
        self._pending_operator_events: tuple[CanonicalOperatorEvent, ...] = ()
        self._canonical_records_seen = False
        self._canonical_status_overlays_by_work_key: dict[WorkKey, CanonicalStatusOverlay] = {}
        self._canonical_statuses_by_agent_id: dict[str, AgentStatus] = {}

    @classmethod
    def from_default_sources(
        cls,
        stale_after_seconds: float = 3600.0,
        tool_running_timeout_seconds: float = 0.0,
        completed_visible_seconds: float = COMPLETED_VISIBLE_SECONDS,
        idle_visible_seconds: float = IDLE_VISIBLE_SECONDS,
        post_tool_working_visible_seconds: float = POST_TOOL_WORKING_VISIBLE_SECONDS,
        max_lines_per_source: int = 5000,
    ) -> AgentMonitor:
        return cls(
            stale_after_seconds=stale_after_seconds,
            tool_running_timeout_seconds=tool_running_timeout_seconds,
            completed_visible_seconds=completed_visible_seconds,
            idle_visible_seconds=idle_visible_seconds,
            post_tool_working_visible_seconds=post_tool_working_visible_seconds,
            max_lines_per_source=max_lines_per_source,
        )

    def snapshot(self) -> MonitorSnapshot:
        now = _canonical_datetime(self._clock_sampler().wall_epoch)
        if self._refresh_canonical_state():
            events = self._pending_operator_events
            self._pending_operator_events = ()
            return _snapshot_from_operator_state(
                self.operator_state,
                events=events,
                sources=self.sources,
                collected_at=now,
                restore_health=RestoreHealth.NOT_ATTEMPTED,
                status_overlays=MappingProxyType(
                    dict(self._canonical_status_overlays_by_work_key)
                ),
                supplemental_statuses=tuple(self._canonical_statuses_by_agent_id.values()),
                stale_after_seconds=self.stale_after_seconds,
                tool_running_timeout_seconds=self.tool_running_timeout_seconds,
                completed_visible_seconds=self.completed_visible_seconds,
                idle_visible_seconds=self.idle_visible_seconds,
                post_tool_working_visible_seconds=self.post_tool_working_visible_seconds,
                canonical_projected_uses_age_windows=True,
            )
        statuses_by_key = self._latest_statuses()

        fresh: list[AgentStatus] = []
        stale: list[AgentStatus] = []
        for status in statuses_by_key.values():
            effective = status_for_snapshot(
                status,
                now,
                post_tool_working_visible_seconds=self.post_tool_working_visible_seconds,
            )
            is_stale = self.is_stale_status(effective, now)
            current = _replace_stale(effective, is_stale)
            if is_stale:
                stale.append(current)
            else:
                fresh.append(current)

        if any(status_counts_active(status) for status in fresh):
            inactive = [status for status in fresh if not status_counts_active(status)]
            fresh = [status for status in fresh if status_counts_active(status)]
            stale.extend(_replace_stale(status, True) for status in inactive)

        fresh.sort(key=lambda status: (status.priority, -status.updated_at.timestamp()))
        stale.sort(key=lambda status: (status.priority, -status.updated_at.timestamp()))

        visible = tuple(fresh)
        # The snapshot ALWAYS carries stale statuses; consumers decide
        # visibility (the dropdown falls back to them when idle, the CLI
        # gates them behind --all at render time).
        stale_visible = tuple(stale)
        aggregate = aggregate_status(visible, stale_visible)

        return MonitorSnapshot(
            aggregate=aggregate,
            statuses=visible,
            stale_statuses=stale_visible,
            sources=self.sources,
            collected_at=now,
        )

    def _refresh_canonical_state(self) -> bool:
        signature = self._input_signature()
        if signature == self._canonical_signature:
            return self._canonical_records_seen
        clock = self._clock_sampler()
        metadata_by_session: dict[str, StatusMetadata] = {}
        metadata_by_status: dict[str, StatusMetadata] = {}
        pending_permissions_by_key: dict[str, set[str]] = {}
        status_overlays: dict[WorkKey, CanonicalStatusOverlay] = {}
        compatibility_statuses_by_agent_id: dict[str, AgentStatus] = {}
        suppressed_work_keys: set[WorkKey] = set()
        batches: list[ProviderFactBatch] = []
        for record in sorted(self._iter_records(), key=lambda candidate: candidate.logged_at):
            metadata = metadata_for_record(
                record,
                metadata_by_session,
                metadata_by_status,
            )
            ignored = mode_for_event(record) is not None and should_ignore_record(record, metadata)
            status = None if ignored else status_from_event(record, metadata)
            keep_status = False
            if status is not None:
                track_pending_permissions(record, pending_permissions_by_key)
                previous = compatibility_statuses_by_agent_id.get(status.agent_id)
                keep_status = not should_ignore_status_transition(
                    previous,
                    status,
                    pending_permissions_by_key.get(status.agent_id, set()),
                )
                if keep_status:
                    compatibility_statuses_by_agent_id[status.agent_id] = status
            batch = _batch_for_hook_record(record, clock=clock)
            if batch is None:
                continue
            if ignored:
                suppressed_work_keys.update(fact.key for fact in batch.work_facts)
                continue
            if status is not None and keep_status:
                transcript_source = record.raw.get("source") in {
                    CODEX_TRANSCRIPT_PROVIDER,
                    CLAUDE_TRANSCRIPT_PROVIDER,
                }
                for fact in batch.work_facts:
                    status_overlays[fact.key] = CanonicalStatusOverlay(
                        watermark=fact.watermark,
                        status=status,
                        preserve_display_name=True,
                        preserve_details=transcript_source,
                    )
            batches.append(batch)
        batches = sorted(batches, key=_batch_sort_key)
        self._canonical_signature = signature
        self._canonical_records_seen = bool(batches)
        self._canonical_status_overlays_by_work_key = status_overlays
        self._canonical_statuses_by_agent_id = compatibility_statuses_by_agent_id
        if not batches:
            self.operator_state = empty_operator_state()
            self._pending_operator_events = ()
            return False
        previous_watermarks = dict(self.operator_state.source_watermarks)
        state = empty_operator_state()
        events: dict[SemanticEventKey, CanonicalOperatorEvent] = {}
        for batch in batches:
            reduced = reduce_operator_state(state, batch, clock=clock)
            state = reduced.state
            events.update((event.key, event) for event in reduced.events)
        if suppressed_work_keys:
            remaining_works = tuple(
                work for work in state.works if work.key not in suppressed_work_keys
            )
            remaining_requests = tuple(
                request
                for request in state.requests
                if request.key.work_key not in suppressed_work_keys
            )
            remaining_sources = {work.key.source_key for work in remaining_works}
            state = replace(
                state,
                works=remaining_works,
                requests=remaining_requests,
                source_watermarks=tuple(
                    item for item in state.source_watermarks if item[0] in remaining_sources
                ),
            )
        self.operator_state = state
        self._pending_operator_events = tuple(
            event
            for key, event in sorted(events.items())
            if (
                (retained := previous_watermarks.get(key.provider_watermark.source_key))
                is None
                or compare_watermarks(key.provider_watermark, retained)
                is WatermarkOrder.NEWER
            )
        )[:MAX_PENDING_OPERATOR_EVENTS]
        return True

    def _latest_statuses(self) -> dict[str, AgentStatus]:
        signature = self._input_signature()
        if (
            self._latest_status_signature == signature
            and self._latest_statuses_by_key is not None
        ):
            return dict(self._latest_statuses_by_key)

        statuses_by_key: dict[str, AgentStatus] = {}
        metadata_by_session: dict[str, StatusMetadata] = {}
        metadata_by_status: dict[str, StatusMetadata] = {}
        pending_permissions_by_key: dict[str, set[str]] = {}

        records = sorted(
            self._iter_records(),
            key=lambda record: record.logged_at,
        )

        for record in records:
            metadata = metadata_for_record(
                record,
                metadata_by_session,
                metadata_by_status,
            )
            status = status_from_event(record, metadata)
            if status is not None:
                track_pending_permissions(record, pending_permissions_by_key)
                previous = statuses_by_key.get(status.agent_id)
                if should_ignore_status_transition(
                    previous,
                    status,
                    pending_permissions_by_key.get(status.agent_id, set()),
                ):
                    continue
                statuses_by_key[status.agent_id] = status

        self._latest_status_signature = signature
        self._latest_statuses_by_key = dict(statuses_by_key)
        return statuses_by_key

    def input_signature(self) -> tuple[Any, ...]:
        """Cheap change-detection handle for callers that feed this
        monitor's records elsewhere (the status bar's transcript
        fallback): if this hasn't changed, iter_records() won't yield
        anything new."""
        return self._input_signature()

    def _input_signature(self) -> tuple[Any, ...]:
        parts: list[Any] = []
        for source in self.sources:
            if source.provider == CODEX_TRANSCRIPT_PROVIDER:
                parts.append(
                    (
                        source.provider,
                        str(source.path),
                        self._transcript_source_signature(
                            source.path,
                            limit=CODEX_TRANSCRIPT_MAX_FILES,
                            provider=source.provider,
                        ),
                    )
                )
                continue
            if source.provider == CLAUDE_TRANSCRIPT_PROVIDER:
                parts.append(
                    (
                        source.provider,
                        str(source.path),
                        self._transcript_source_signature(
                            source.path,
                            limit=CLAUDE_TRANSCRIPT_MAX_FILES,
                            provider=source.provider,
                        ),
                    )
                )
                continue

            parts.append((source.provider, str(source.path), file_signature(source.path)))
        return tuple(parts)

    def _transcript_source_signature(
        self,
        root: Path,
        *,
        limit: int,
        provider: str,
    ) -> tuple[tuple[str, tuple[float, int] | None], ...]:
        return tuple(
            (str(path), file_signature(path))
            for path in self._recent_transcript_files(
                root,
                limit=limit,
                provider=provider,
            )
        )

    def iter_records(self) -> Iterable[HookEvent]:
        """All records this monitor's sources currently contain --
        public so the status bar's transcript-fallback path can feed
        them into LiveAgentMonitor's own state machine."""
        return self._iter_records()

    def _iter_records(self) -> Iterable[HookEvent]:
        for source in self.sources:
            if not source.path.exists():
                continue
            if source.provider == CODEX_TRANSCRIPT_PROVIDER:
                yield from self._iter_codex_transcript_records(source.path)
                continue
            if source.provider == CLAUDE_TRANSCRIPT_PROVIDER:
                yield from self._iter_claude_transcript_records(source.path)
                continue
            yield from self._cached_log_records(source)

    def _cached_log_records(self, source: SourceSpec) -> tuple[HookEvent, ...]:
        try:
            stat = source.path.stat()
        except OSError:
            return ()

        key = (source.provider, str(source.path), self.max_lines_per_source)
        cached = self._log_records_cache.get(key)
        if cached is not None and cached.mtime == stat.st_mtime and cached.size == stat.st_size:
            return cached.records

        records: list[HookEvent] = []
        for line in read_recent_lines(source.path, self.max_lines_per_source):
            record = parse_log_line(source.provider, line)
            if record is not None:
                records.append(record)

        cached_records = tuple(records)
        self._log_records_cache[key] = CachedTranscriptRecords(
            mtime=stat.st_mtime,
            size=stat.st_size,
            records=cached_records,
        )
        return cached_records

    def _iter_codex_transcript_records(self, root: Path) -> Iterable[HookEvent]:
        for path in self._recent_transcript_files(
            root,
            limit=CODEX_TRANSCRIPT_MAX_FILES,
            provider=CODEX_TRANSCRIPT_PROVIDER,
        ):
            yield from self._cached_transcript_records(
                CODEX_TRANSCRIPT_PROVIDER,
                path,
                iter_codex_transcript_file,
            )

    def _iter_claude_transcript_records(self, root: Path) -> Iterable[HookEvent]:
        for path in self._recent_transcript_files(
            root,
            limit=CLAUDE_TRANSCRIPT_MAX_FILES,
            provider=CLAUDE_TRANSCRIPT_PROVIDER,
        ):
            yield from self._cached_transcript_records(
                CLAUDE_TRANSCRIPT_PROVIDER,
                path,
                iter_claude_transcript_file,
            )

    def _recent_transcript_files(
        self,
        root: Path,
        *,
        limit: int,
        provider: str,
    ) -> tuple[Path, ...]:
        key = (provider, str(root), limit)
        now = time.monotonic()
        cached = self._transcript_file_list_cache.get(key)
        if cached is not None and now < cached.expires_at:
            return cached.paths

        paths = tuple(recent_transcript_files(root, limit=limit))
        root_prefix = str(root)
        eligible_keys = {(provider, str(path)) for path in paths}
        for record_key in list(self._transcript_records_cache):
            cached_provider, cached_path = record_key
            if cached_provider != provider:
                continue
            try:
                Path(cached_path).relative_to(root_prefix)
            except ValueError:
                continue
            if record_key not in eligible_keys:
                del self._transcript_records_cache[record_key]
        self._transcript_file_list_cache[key] = CachedTranscriptFileList(
            expires_at=now + TRANSCRIPT_FILE_LIST_CACHE_SECONDS,
            paths=paths,
        )
        while (
            len(self._transcript_file_list_cache)
            > self.transcript_file_list_cache_max_entries
        ):
            oldest_key = next(iter(self._transcript_file_list_cache))
            del self._transcript_file_list_cache[oldest_key]
        return paths

    def _cached_transcript_records(
        self,
        provider: str,
        path: Path,
        parser: Callable[[Path], Iterable[HookEvent]],
    ) -> tuple[HookEvent, ...]:
        try:
            stat = path.stat()
        except OSError:
            return ()

        key = (provider, str(path))
        cached = self._transcript_records_cache.get(key)
        if cached is not None and cached.mtime == stat.st_mtime and cached.size == stat.st_size:
            return cached.records

        records = tuple(parser(path))
        self._transcript_records_cache[key] = CachedTranscriptRecords(
            mtime=stat.st_mtime,
            size=stat.st_size,
            records=records,
        )
        while len(self._transcript_records_cache) > self.transcript_records_cache_max_entries:
            oldest_key = next(iter(self._transcript_records_cache))
            del self._transcript_records_cache[oldest_key]
        return records

    def is_stale_status(self, status: AgentStatus, now: datetime) -> bool:
        age = bounded_age_seconds(now, status.updated_at)
        if status.mode == AgentMode.COMPLETED and self.completed_visible_seconds >= 0:
            return age > self.completed_visible_seconds
        if status.mode == AgentMode.IDLE_READY and self.idle_visible_seconds >= 0:
            return age > self.idle_visible_seconds
        return (
            age > self.stale_after_seconds
            or self.is_expired_tool_running(status, now)
        )

    def is_expired_tool_running(self, status: AgentStatus, now: datetime) -> bool:
        return (
            status.mode == AgentMode.TOOL_RUNNING
            and self.tool_running_timeout_seconds > 0
            and not is_recent(
                now,
                status.updated_at,
                self.tool_running_timeout_seconds,
            )
        )


class LiveAgentMonitor:
    """Own the sole mutable canonical reducer state for live production flow."""

    def __init__(
        self,
        *,
        sources: Iterable[SourceSpec] = (),
        stale_after_seconds: float = 3600.0,
        tool_running_timeout_seconds: float = 0.0,
        completed_visible_seconds: float = COMPLETED_VISIBLE_SECONDS,
        idle_visible_seconds: float = IDLE_VISIBLE_SECONDS,
        post_tool_working_visible_seconds: float = POST_TOOL_WORKING_VISIBLE_SECONDS,
        latest_state_path: Path | None = None,
        restore_work_keys: tuple[WorkKey, ...] = (),
        clock_sampler: Callable[[], ClockSample] = _default_clock_sample,
    ) -> None:
        self.sources = tuple(sources)
        self.stale_after_seconds = stale_after_seconds
        self.tool_running_timeout_seconds = tool_running_timeout_seconds
        self.completed_visible_seconds = completed_visible_seconds
        self.idle_visible_seconds = idle_visible_seconds
        self.post_tool_working_visible_seconds = post_tool_working_visible_seconds
        self.latest_state_path = latest_state_path
        self.restore_work_keys = restore_work_keys
        self._clock_sampler = clock_sampler
        self.lock = threading.RLock()
        self.operator_state = empty_operator_state()
        self._pending_operator_events: tuple[CanonicalOperatorEvent, ...] = ()
        self._status_metadata_by_session: dict[str, StatusMetadata] = {}
        self._status_metadata_by_status: dict[str, StatusMetadata] = {}
        self._status_overlays_by_work_key: dict[WorkKey, CanonicalStatusOverlay] = {}
        self._compatibility_statuses_by_agent_id: dict[str, AgentStatus] = {}
        self._compatibility_status_authority_by_agent_id: dict[
            str, ObservationAuthority
        ] = {}
        self._compatibility_status_watermark_by_agent_id: dict[
            str, ProviderWatermark
        ] = {}
        self._pending_permissions_by_key: dict[str, set[str]] = {}
        self.restore_health = RestoreHealth.NOT_ATTEMPTED
        self._latest_state_dirty = False
        self._latest_state_written_at = 0.0
        self._latest_state_write_lock = threading.Lock()
        self.load_latest_state()

    def ingest_record(self, record: HookEvent) -> None:
        clock = self._clock_sampler()
        batch = _batch_for_hook_record(record, clock=clock)
        with self.lock:
            metadata = metadata_for_record(
                record,
                self._status_metadata_by_session,
                self._status_metadata_by_status,
            )
            ignored = mode_for_event(record) is not None and should_ignore_record(record, metadata)
            status = None if ignored else status_from_event(record, metadata)
            keep_status = False
            if status is not None:
                track_pending_permissions(record, self._pending_permissions_by_key)
                previous = self._compatibility_statuses_by_agent_id.get(status.agent_id)
                keep_status = not should_ignore_status_transition(
                    previous,
                    status,
                    self._pending_permissions_by_key.get(status.agent_id, set()),
                )
                transcript_source = record.raw.get("source") in {
                    CODEX_TRANSCRIPT_PROVIDER,
                    CLAUDE_TRANSCRIPT_PROVIDER,
                }
                previous_authority = (
                    self._compatibility_status_authority_by_agent_id.get(status.agent_id)
                )
                previous_watermark = self._compatibility_status_watermark_by_agent_id.get(
                    status.agent_id
                )
                if (
                    transcript_source
                    and previous is not None
                    and batch is not None
                    and previous_authority is not None
                    and (
                        status.event_name in {"Stop", "StopFailure", "SessionEnd"}
                    )
                    and (
                        previous_authority > batch.observation_authority
                        or (
                            previous_authority == batch.observation_authority
                            and previous_watermark is not None
                            and compare_watermarks(batch.watermark, previous_watermark)
                            is not WatermarkOrder.NEWER
                        )
                    )
                ):
                    keep_status = False
                if keep_status:
                    self._compatibility_statuses_by_agent_id[status.agent_id] = status
                    if batch is not None:
                        self._compatibility_status_authority_by_agent_id[status.agent_id] = (
                            batch.observation_authority
                        )
                        self._compatibility_status_watermark_by_agent_id[status.agent_id] = (
                            batch.watermark
                        )
                if batch is not None and keep_status:
                    for fact in batch.work_facts:
                        self._status_overlays_by_work_key[fact.key] = CanonicalStatusOverlay(
                            watermark=fact.watermark,
                            status=status,
                            preserve_display_name=transcript_source,
                            preserve_details=True,
                        )
            elif record.event_name in {"Stop", "SessionEnd", "UserPromptSubmit"}:
                self._pending_permissions_by_key.pop(record.status_key, None)
        if batch is not None and not ignored:
            self.ingest_batch(batch, clock=clock)

    def ingest_batch(
        self,
        batch: ProviderFactBatch,
        *,
        clock: ClockSample | None = None,
    ) -> None:
        sampled_clock = self._clock_sampler() if clock is None else clock
        with self.lock:
            reduced = reduce_operator_state(
                self.operator_state,
                batch,
                clock=sampled_clock,
            )
            self.operator_state = reduced.state
            events = {
                event.key: event
                for event in (*self._pending_operator_events, *reduced.events)
            }
            self._pending_operator_events = tuple(
                event for _key, event in sorted(events.items())
            )[:MAX_PENDING_OPERATOR_EVENTS]
            current_keys = {work.key for work in self.operator_state.works}
            self._status_overlays_by_work_key = {
                key: overlay
                for key, overlay in self._status_overlays_by_work_key.items()
                if key in current_keys
            }
            self._latest_state_dirty = True
        self.maybe_write_latest_state()

    def reconcile_refresh_hint(
        self,
        hint: object,
        *,
        log_path: Path,
    ) -> None:
        """Reconcile a hint only by rereading the registered normalized log."""
        from .ipc import ProviderRefreshHint

        if type(hint) is not ProviderRefreshHint:
            return
        clock = self._clock_sampler()
        batches: list[ProviderFactBatch] = []
        source = next(
            (
                registered
                for registered in negotiated_provider_sources()
                if registered.source_key == hint.source_key
                and registered.observation_invocation_allowed
            ),
            None,
        )
        if source is None:
            return
        try:
            lines = read_private_text(
                Path(log_path),
                max_bytes=LATEST_STATE_MAX_BYTES,
            ).splitlines()
        except OSError:
            return
        for line in lines[-5_000:]:
            normalized = None
            try:
                payload = _decode_strict_json_document(line)
            except (RecursionError, TypeError, UnicodeError, ValueError):
                payload = None
            if payload is not None:
                normalized = normalized_provider_record_from_payload(payload)
                if (
                    normalized is None
                    and type(payload) is dict
                    and "record_kind" in payload
                ):
                    continue
            if normalized is not None:
                if normalized.source_key != hint.source_key:
                    continue
            else:
                try:
                    legacy = parse_log_line(hint.source_key.provider_id, line)
                except (RecursionError, TypeError, UnicodeError, ValueError):
                    legacy = None
                if legacy is None:
                    continue
                normalized = minimize_hook_event(
                    legacy,
                    source_key=source.source_key,
                    contract=source.contract,
                    observation_authority=source.registration.observation_authority,
                )
            batch = provider_facts_for_record(
                normalized,
                contract=source.contract,
                observation_authority=source.registration.observation_authority,
                observed_at_epoch=clock.wall_epoch,
            )
            batches.append(batch)
        for batch in sorted(batches, key=_batch_sort_key):
            self.ingest_batch(batch, clock=clock)

    def snapshot(self) -> MonitorSnapshot:
        now = _canonical_datetime(self._clock_sampler().wall_epoch)
        with self.lock:
            state = self.operator_state
            events = self._pending_operator_events
            self._pending_operator_events = ()
            health = self.restore_health
        return _snapshot_from_operator_state(
            state,
            events=events,
            sources=self.sources,
            collected_at=now,
            restore_health=health,
            status_overlays=MappingProxyType(dict(self._status_overlays_by_work_key)),
            supplemental_statuses=tuple(self._compatibility_statuses_by_agent_id.values()),
            stale_after_seconds=self.stale_after_seconds,
            tool_running_timeout_seconds=self.tool_running_timeout_seconds,
            completed_visible_seconds=self.completed_visible_seconds,
            idle_visible_seconds=self.idle_visible_seconds,
            post_tool_working_visible_seconds=self.post_tool_working_visible_seconds,
            canonical_projected_uses_age_windows=False,
        )

    def load_latest_state(self) -> None:
        if self.latest_state_path is None:
            return
        try:
            raw = read_private_text(
                self.latest_state_path,
                max_bytes=LATEST_STATE_MAX_BYTES,
            )
            document = _decode_strict_json_document(raw)
            state, health = _operator_state_from_document(
                document,
                restore_work_keys=self.restore_work_keys,
            )
        except FileNotFoundError:
            self.restore_health = RestoreHealth.MISSING
            return
        except _UnsupportedLatestState:
            self.restore_health = RestoreHealth.UNSUPPORTED
            return
        except OSError:
            self.restore_health = RestoreHealth.UNAVAILABLE
            return
        except (RecursionError, TypeError, UnicodeError, ValueError):
            self.restore_health = RestoreHealth.CORRUPT
            return
        self.operator_state = state
        self._status_overlays_by_work_key = (
            _presentation_overlays_from_document(document)
            if type(document) is dict and document.get("version") == 2
            else {}
        )
        if (
            type(document) is dict
            and "statuses" in document
            and not state.works
        ):
            statuses = tuple(
                status
                for row in document.get("statuses", ())
                if (status := agent_status_from_dict(row)) is not None
            )
            self._compatibility_statuses_by_agent_id = {
                status.agent_id: status for status in statuses
            }
        else:
            self._compatibility_statuses_by_agent_id = {}
        self._pending_permissions_by_key = {}
        self.restore_health = health
        self._pending_operator_events = ()

    def current_statuses_by_key(self) -> dict[str, AgentStatus]:
        with self.lock:
            state = self.operator_state
            overlays = MappingProxyType(dict(self._status_overlays_by_work_key))
        snapshot = _snapshot_from_operator_state(
            state,
            events=(),
            sources=self.sources,
            collected_at=_canonical_datetime(self._clock_sampler().wall_epoch),
            restore_health=self.restore_health,
            status_overlays=overlays,
            supplemental_statuses=tuple(self._compatibility_statuses_by_agent_id.values()),
            stale_after_seconds=self.stale_after_seconds,
            tool_running_timeout_seconds=self.tool_running_timeout_seconds,
            completed_visible_seconds=self.completed_visible_seconds,
            idle_visible_seconds=self.idle_visible_seconds,
            post_tool_working_visible_seconds=self.post_tool_working_visible_seconds,
            canonical_projected_uses_age_windows=False,
        )
        return {
            status.agent_id: status
            for status in (*snapshot.statuses, *snapshot.stale_statuses)
        }

    def write_latest_state(self) -> None:
        """Unconditionally flush one exact metadata-only v2 snapshot."""
        self._write_latest_state(force=True)

    def maybe_write_latest_state(self) -> None:
        """Debounce private persistence without holding the reducer lock."""
        self._write_latest_state(force=False)

    def _write_latest_state(self, *, force: bool) -> None:
        if self.latest_state_path is None:
            return
        with self._latest_state_write_lock:
            now_monotonic = time.monotonic()
            if not force:
                if not self._latest_state_dirty:
                    return
                if (
                    now_monotonic - self._latest_state_written_at
                    < LATEST_STATE_WRITE_INTERVAL_SECONDS
                ):
                    return
            with self.lock:
                state = self.operator_state
                overlays = dict(self._status_overlays_by_work_key)
            try:
                serialized = _serialize_latest_state(
                    state,
                    overlays=MappingProxyType(overlays),
                )
                atomic_private_write(self.latest_state_path, serialized)
            except (OSError, ValueError):
                return
            with self.lock:
                if self.operator_state == state:
                    self._latest_state_dirty = False
            self._latest_state_written_at = now_monotonic


class _UnsupportedLatestState(ValueError):
    pass


_LATEST_DOCUMENT_FIELDS = frozenset(
    {
        "version",
        "generation",
        "works",
        "requests",
        "source_watermarks",
        "timing_uncertain_sources",
        "clock_continuity",
        "last_clock",
        "presentation_hints",
    }
)
_SOURCE_KEY_FIELDS = frozenset(
    {"provider_id", "adapter_id", "source_instance_id", "capability_id"}
)
_WATERMARK_FIELDS = frozenset(
    {
        "source_key",
        "basis",
        "occurred_at_epoch",
        "event_token",
        "sequence",
        "tie_break_rank",
    }
)
_WORK_FIELDS = frozenset(
    {
        "key",
        "lifecycle",
        "watermark",
        "observation_authority",
        "source_health",
        "source_freshness",
        "next_actor",
        "safe_label",
        "parent_key",
        "request_keys",
        "timing_uncertain",
    }
)
_REQUEST_FIELDS = frozenset(
    {
        "key",
        "phase",
        "request_kind",
        "next_actor",
        "watermark",
        "source_freshness",
        "acknowledgement_eligibility",
        "semantic_event_key",
        "opened_at_epoch",
        "eligible_elapsed_seconds",
        "observation_authority",
    }
)
_CLOCK_CONTINUITY_FIELDS = frozenset(
    {"status", "uncertain_since_monotonic", "recovery_confirmations"}
)
_CLOCK_FIELDS = frozenset({"wall_epoch", "monotonic_seconds", "boot_id"})
_PRESENTATION_HINT_FIELDS = frozenset(
    {"key", "mode", "event_name", "updated_at", "source_label"}
)
_PRODUCT_PROVIDER_LABELS = {
    "codex": "Codex",
    "claude": "Claude",
    "devin": "Devin",
    "grok": "Grok",
    "cursor": "Cursor",
    "hermes": "Hermes",
    "openclaw": "OpenClaw",
    "opencode": "OpenCode",
}


def _has_exact_fields(value: object, fields: frozenset[str]) -> bool:
    return type(value) is dict and frozenset(value) == fields


def _strict_json_object(
    pairs: list[tuple[object, object]],
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("invalid latest-state document")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid latest-state number")


def _decode_strict_json_document(raw: str) -> object:
    return json.loads(
        raw,
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_json_constant,
    )


def _source_key_to_payload(source: SourceKey) -> dict[str, object]:
    return {
        "provider_id": source.provider_id,
        "adapter_id": source.adapter_id,
        "source_instance_id": source.source_instance_id,
        "capability_id": source.capability_id,
    }


def _source_key_from_payload(payload: object) -> SourceKey | None:
    if not _has_exact_fields(payload, _SOURCE_KEY_FIELDS):
        return None
    if not all(type(payload[field]) is str for field in _SOURCE_KEY_FIELDS):
        return None
    try:
        return SourceKey(
            payload["provider_id"],
            payload["adapter_id"],
            payload["source_instance_id"],
            payload["capability_id"],
        )
    except ValueError:
        return None


def _watermark_to_payload(watermark: ProviderWatermark) -> dict[str, object]:
    return {
        "source_key": _source_key_to_payload(watermark.source_key),
        "basis": watermark.basis.value,
        "occurred_at_epoch": watermark.occurred_at_epoch,
        "event_token": watermark.event_token.value,
        "sequence": watermark.sequence,
        "tie_break_rank": watermark.tie_break_rank,
    }


def _watermark_from_payload(payload: object) -> ProviderWatermark | None:
    if not _has_exact_fields(payload, _WATERMARK_FIELDS):
        return None
    source = _source_key_from_payload(payload["source_key"])
    if source is None:
        return None
    if not (
        type(payload["basis"]) is str
        and type(payload["event_token"]) is str
        and type(payload["occurred_at_epoch"]) in {int, float}
        and (
            payload["sequence"] is None
            or type(payload["sequence"]) is int
        )
        and type(payload["tie_break_rank"]) is int
    ):
        return None
    try:
        return ProviderWatermark(
            source,
            WatermarkBasis(payload["basis"]),
            payload["occurred_at_epoch"],
            EventToken(payload["event_token"]),
            payload["sequence"],
            payload["tie_break_rank"],
        )
    except ValueError:
        return None


def _work_to_payload(work: CanonicalWorkTruth) -> dict[str, object]:
    return {
        "key": work_key_to_payload(work.key),
        "lifecycle": work.lifecycle.value,
        "watermark": _watermark_to_payload(work.watermark),
        "observation_authority": int(work.observation_authority),
        "source_health": work.source_health.value,
        "source_freshness": work.source_freshness.value,
        "next_actor": work.next_actor.value,
        "safe_label": work.safe_label,
        "parent_key": (
            None if work.parent_key is None else work_key_to_payload(work.parent_key)
        ),
        "request_keys": [request_key_to_payload(key) for key in work.request_keys],
        "timing_uncertain": work.timing_uncertain,
    }


def _request_to_payload(request: CanonicalRequestTruth) -> dict[str, object]:
    return {
        "key": request_key_to_payload(request.key),
        "phase": request.phase.value,
        "request_kind": request.request_kind.value,
        "next_actor": request.next_actor.value,
        "watermark": _watermark_to_payload(request.watermark),
        "source_freshness": request.source_freshness.value,
        "acknowledgement_eligibility": request.acknowledgement_eligibility.value,
        "semantic_event_key": semantic_event_key_to_payload(
            request.semantic_event_key
        ),
        "opened_at_epoch": request.opened_at_epoch,
        "eligible_elapsed_seconds": request.eligible_elapsed_seconds,
        "observation_authority": int(request._observation_authority),
    }


def _clock_continuity_to_payload(
    continuity: ClockContinuityState,
) -> dict[str, object]:
    return {
        "status": continuity.status.value,
        "uncertain_since_monotonic": continuity.uncertain_since_monotonic,
        "recovery_confirmations": continuity.recovery_confirmations,
    }


def _clock_to_payload(clock: ClockSample) -> dict[str, object]:
    return {
        "wall_epoch": clock.wall_epoch,
        "monotonic_seconds": clock.monotonic_seconds,
        "boot_id": clock.boot_id.value,
    }


def _presentation_hint_payloads(
    overlays: Mapping[WorkKey, CanonicalStatusOverlay],
) -> list[dict[str, object]]:
    return [
        {
            "key": work_key_to_payload(key),
            "mode": overlay.status.mode.value,
            "event_name": overlay.status.event_name,
            "updated_at": overlay.status.updated_at.isoformat(),
            "source_label": overlay.status.origin,
        }
        for key, overlay in sorted(
            overlays.items(),
            key=lambda item: (
                item[0].source_key.provider_id,
                item[0].source_key.adapter_id,
                item[0].source_key.source_instance_id,
                item[0].source_key.capability_id,
                item[0].work_id.value,
            ),
        )
    ]


def _state_to_document(
    state: CanonicalOperatorState,
    *,
    overlays: Mapping[WorkKey, CanonicalStatusOverlay] = MappingProxyType({}),
) -> dict[str, object]:
    return {
        "version": 2,
        "generation": state.generation,
        "works": [_work_to_payload(work) for work in state.works],
        "requests": [_request_to_payload(request) for request in state.requests],
        "source_watermarks": [
            _watermark_to_payload(watermark)
            for _source, watermark in state.source_watermarks
        ],
        "timing_uncertain_sources": [
            _source_key_to_payload(source)
            for source in state.timing_uncertain_sources
        ],
        "clock_continuity": _clock_continuity_to_payload(state.clock_continuity),
        "last_clock": (
            None if state.last_clock is None else _clock_to_payload(state.last_clock)
        ),
        "presentation_hints": _presentation_hint_payloads(overlays),
    }


def _serialize_latest_state(
    state: CanonicalOperatorState,
    *,
    overlays: Mapping[WorkKey, CanonicalStatusOverlay] = MappingProxyType({}),
) -> str:
    serialized = json.dumps(
        _state_to_document(state, overlays=overlays),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    encoded = f"{serialized}\n"
    if len(encoded.encode("utf-8")) > LATEST_STATE_MAX_BYTES:
        raise ValueError("latest state exceeds maximum size")
    return encoded


def _safe_label_for_key(key: WorkKey) -> str:
    label = _PRODUCT_PROVIDER_LABELS.get(key.source_key.provider_id, "Provider")
    return f"{label} {key.work_id.value}"


def _work_from_payload(payload: object) -> CanonicalWorkTruth:
    if not _has_exact_fields(payload, _WORK_FIELDS):
        raise ValueError("invalid work restore row")
    key = work_key_from_payload(payload["key"])
    watermark = _watermark_from_payload(payload["watermark"])
    parent_payload = payload["parent_key"]
    parent = None if parent_payload is None else work_key_from_payload(parent_payload)
    request_payloads = payload["request_keys"]
    if (
        key is None
        or watermark is None
        or watermark.source_key != key.source_key
        or (parent_payload is not None and parent is None)
        or type(request_payloads) is not list
        or len(request_payloads) > 1_000
        or type(payload["lifecycle"]) is not str
        or type(payload["observation_authority"]) is not int
        or type(payload["source_health"]) is not str
        or type(payload["source_freshness"]) is not str
        or type(payload["next_actor"]) is not str
        or type(payload["safe_label"]) is not str
        or payload["safe_label"] != _safe_label_for_key(key)
        or type(payload["timing_uncertain"]) is not bool
    ):
        raise ValueError("invalid work restore row")
    request_keys = tuple(request_key_from_payload(item) for item in request_payloads)
    if any(item is None for item in request_keys):
        raise ValueError("invalid work restore row")
    ObservationAuthority(payload["observation_authority"])
    SourceFreshness(payload["source_freshness"])
    return CanonicalWorkTruth(
        key=key,
        lifecycle=WorkLifecycle(payload["lifecycle"]),
        watermark=watermark,
        observation_authority=ObservationAuthority.RESTORED_LAST_KNOWN,
        source_health=SourceHealth(payload["source_health"]),
        source_freshness=SourceFreshness.RESTORED,
        next_actor=NextActor(payload["next_actor"]),
        safe_label=payload["safe_label"],
        parent_key=parent,
        request_keys=request_keys,
        timing_uncertain=payload["timing_uncertain"],
    )


def _restored_request_eligibility(phase: RequestPhase) -> AcknowledgementEligibility:
    if phase is RequestPhase.RESOLVED:
        return AcknowledgementEligibility.RESOLVED
    if phase is RequestPhase.STALE_HOLD:
        return AcknowledgementEligibility.STALE_HOLD
    return AcknowledgementEligibility.NOT_ACTIONABLE


def _request_from_payload(payload: object) -> CanonicalRequestTruth:
    if not _has_exact_fields(payload, _REQUEST_FIELDS):
        raise ValueError("invalid request restore row")
    key = request_key_from_payload(payload["key"])
    watermark = _watermark_from_payload(payload["watermark"])
    event_key = semantic_event_key_from_payload(payload["semantic_event_key"])
    opened = payload["opened_at_epoch"]
    elapsed = payload["eligible_elapsed_seconds"]
    if (
        key is None
        or watermark is None
        or watermark.source_key != key.work_key.source_key
        or event_key is None
        or event_key.subject_key != key
        or type(payload["phase"]) is not str
        or type(payload["request_kind"]) is not str
        or type(payload["next_actor"]) is not str
        or type(payload["source_freshness"]) is not str
        or type(payload["acknowledgement_eligibility"]) is not str
        or type(payload["observation_authority"]) is not int
        or (opened is not None and type(opened) not in {int, float})
        or type(elapsed) not in {int, float}
    ):
        raise ValueError("invalid request restore row")
    persisted_phase = RequestPhase(payload["phase"])
    AcknowledgementEligibility(payload["acknowledgement_eligibility"])
    ObservationAuthority(payload["observation_authority"])
    SourceFreshness(payload["source_freshness"])
    phase = (
        RequestPhase.STALE_HOLD
        if persisted_phase
        in {RequestPhase.LIVE_UNACKNOWLEDGED, RequestPhase.LIVE_ACKNOWLEDGED}
        else persisted_phase
    )
    return CanonicalRequestTruth(
        key=key,
        phase=phase,
        request_kind=RequestKind(payload["request_kind"]),
        next_actor=NextActor(payload["next_actor"]),
        watermark=watermark,
        source_freshness=SourceFreshness.RESTORED,
        acknowledgement_eligibility=_restored_request_eligibility(phase),
        semantic_event_key=event_key,
        opened_at_epoch=opened,
        eligible_elapsed_seconds=elapsed,
        _observation_authority=ObservationAuthority.RESTORED_LAST_KNOWN,
    )


def _clock_continuity_from_payload(payload: object) -> ClockContinuityState:
    if not _has_exact_fields(payload, _CLOCK_CONTINUITY_FIELDS):
        raise ValueError("invalid clock continuity")
    since = payload["uncertain_since_monotonic"]
    confirmations = payload["recovery_confirmations"]
    if not (
        type(payload["status"]) is str
        and (since is None or type(since) in {int, float})
        and type(confirmations) is int
    ):
        raise ValueError("invalid clock continuity")
    return ClockContinuityState(
        ClockContinuityStatus(payload["status"]),
        since,
        confirmations,
    )


def _clock_from_payload(payload: object) -> ClockSample | None:
    if payload is None:
        return None
    if not _has_exact_fields(payload, _CLOCK_FIELDS):
        raise ValueError("invalid clock sample")
    if not (
        type(payload["wall_epoch"]) in {int, float}
        and type(payload["monotonic_seconds"]) in {int, float}
        and type(payload["boot_id"]) is str
    ):
        raise ValueError("invalid clock sample")
    return ClockSample(
        payload["wall_epoch"],
        payload["monotonic_seconds"],
        BootIdentifier(payload["boot_id"]),
    )


def _presentation_overlays_from_document(
    document: object,
) -> dict[WorkKey, CanonicalStatusOverlay]:
    if type(document) is not dict:
        raise ValueError("invalid latest-state document")
    payloads = document.get("presentation_hints")
    if type(payloads) is not list or len(payloads) > 1_000:
        raise ValueError("invalid latest-state document")
    overlays: dict[WorkKey, CanonicalStatusOverlay] = {}
    for payload in payloads:
        if not _has_exact_fields(payload, _PRESENTATION_HINT_FIELDS):
            raise ValueError("invalid latest-state document")
        key = work_key_from_payload(payload["key"])
        if (
            key is None
            or type(payload["mode"]) is not str
            or type(payload["event_name"]) is not str
            or (payload["source_label"] is not None and type(payload["source_label"]) is not str)
        ):
            raise ValueError("invalid latest-state document")
        status = AgentStatus(
            provider=key.source_key.provider_id,
            agent_id=f"{key.source_key.provider_id}:session:{key.work_id.value}",
            display_name=_safe_label_for_key(key),
            mode=AgentMode(payload["mode"]),
            updated_at=parse_datetime(payload["updated_at"]),
            event_name=payload["event_name"],
            session_id=key.work_id.value,
            origin=payload["source_label"],
            work_key=key,
        )
        overlays[key] = CanonicalStatusOverlay(
            watermark=ProviderWatermark(
                key.source_key,
                WatermarkBasis.PROVIDER_EVENT_ID,
                status.updated_at.timestamp(),
                EventToken(hashlib.sha256(json.dumps(work_key_to_payload(key), sort_keys=True).encode()).hexdigest()),
                None,
                0,
            ),
            status=status,
            preserve_details=True,
        )
    return overlays


def _v2_state_from_document(document: object) -> CanonicalOperatorState:
    if not _has_exact_fields(document, _LATEST_DOCUMENT_FIELDS):
        raise ValueError("invalid latest-state document")
    if type(document["version"]) is not int or document["version"] != 2:
        raise _UnsupportedLatestState
    works_payload = document["works"]
    requests_payload = document["requests"]
    watermarks_payload = document["source_watermarks"]
    uncertain_payload = document["timing_uncertain_sources"]
    if not (
        type(document["generation"]) is int
        and document["generation"] >= 0
        and type(works_payload) is list
        and len(works_payload) <= 1_000
        and type(requests_payload) is list
        and len(requests_payload) <= 1_000
        and type(watermarks_payload) is list
        and len(watermarks_payload) <= 1_000
        and type(uncertain_payload) is list
        and len(uncertain_payload) <= 1_000
        and type(document["presentation_hints"]) is list
        and len(document["presentation_hints"]) <= 1_000
    ):
        raise ValueError("invalid latest-state document")
    works = tuple(_work_from_payload(item) for item in works_payload)
    requests = tuple(_request_from_payload(item) for item in requests_payload)
    watermarks = tuple(_watermark_from_payload(item) for item in watermarks_payload)
    uncertain = tuple(_source_key_from_payload(item) for item in uncertain_payload)
    if any(item is None for item in watermarks) or any(item is None for item in uncertain):
        raise ValueError("invalid latest-state document")
    state = CanonicalOperatorState(
        schema_version=1,
        generation=document["generation"],
        works=works,
        requests=requests,
        source_watermarks=tuple(
            (watermark.source_key, watermark) for watermark in watermarks
        ),
        timing_uncertain_sources=uncertain,
        clock_continuity=_clock_continuity_from_payload(document["clock_continuity"]),
        last_clock=_clock_from_payload(document["last_clock"]),
    )
    request_keys_by_work: dict[WorkKey, list[RequestKey]] = {}
    for request in state.requests:
        request_keys_by_work.setdefault(request.key.work_key, []).append(request.key)
    if any(
        work.request_keys != tuple(request_keys_by_work.get(work.key, ()))
        for work in state.works
    ):
        raise ValueError("invalid latest-state request linkage")
    return state


def _legacy_lifecycle(value: object) -> WorkLifecycle | None:
    if type(value) is not str:
        return None
    return {
        AgentMode.IDLE_READY.value: WorkLifecycle.IDLE,
        AgentMode.WORKING.value: WorkLifecycle.ACTIVE,
        AgentMode.TOOL_RUNNING.value: WorkLifecycle.ACTIVE,
        AgentMode.LONG_TASK_PROGRESS.value: WorkLifecycle.ACTIVE,
        AgentMode.WAITING_FOR_INPUT.value: WorkLifecycle.WAITING,
        AgentMode.BLOCKED_ERROR.value: WorkLifecycle.FAILED,
        AgentMode.COMPLETED.value: WorkLifecycle.COMPLETED,
        AgentMode.UNKNOWN.value: WorkLifecycle.UNKNOWN,
    }.get(value)


def _legacy_timestamp(value: object) -> float | None:
    if type(value) is not str or not value:
        return None
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    epoch = parsed.timestamp()
    return epoch if epoch >= 0.0 else None


def _legacy_matches(
    provider: str,
    agent_id: str,
    keys: tuple[WorkKey, ...],
) -> tuple[WorkKey, ...]:
    return tuple(
        key
        for key in keys
        if key.source_key.provider_id == provider
        and agent_id
        in {
            key.work_id.value,
            f"{provider}:session:{key.work_id.value}",
            f"{provider}:agent:{key.work_id.value}",
        }
    )


def _v1_state_from_document(
    document: object,
    *,
    restore_work_keys: tuple[WorkKey, ...],
) -> tuple[CanonicalOperatorState, RestoreHealth]:
    if type(document) is not dict or frozenset(document) != {
        "updated_at",
        "statuses",
    }:
        raise ValueError("invalid legacy latest-state document")
    if _legacy_timestamp(document["updated_at"]) is None:
        raise ValueError("invalid legacy latest-state document")
    rows = document["statuses"]
    if type(rows) is not list or len(rows) > 1_000:
        raise ValueError("invalid legacy latest-state document")
    works: list[CanonicalWorkTruth] = []
    degraded = False
    for row in rows:
        if type(row) is not dict:
            raise ValueError("invalid legacy latest-state row")
        provider = row.get("provider")
        agent_id = row.get("agent_id")
        lifecycle = _legacy_lifecycle(row.get("mode"))
        epoch = _legacy_timestamp(row.get("updated_at"))
        if not (
            type(provider) is str
            and type(agent_id) is str
            and lifecycle is not None
            and epoch is not None
        ):
            raise ValueError("invalid legacy latest-state row")
        matches = _legacy_matches(provider, agent_id, restore_work_keys)
        if len(matches) != 1:
            degraded = True
            continue
        key = matches[0]
        token_payload = json.dumps(
            work_key_to_payload(key),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        watermark = ProviderWatermark(
            key.source_key,
            WatermarkBasis.PROVIDER_EVENT_ID,
            epoch,
            EventToken(hashlib.sha256(token_payload).hexdigest()),
            None,
            0,
        )
        next_actor = (
            NextActor.USER
            if lifecycle is WorkLifecycle.WAITING
            else NextActor.PROVIDER
            if lifecycle is WorkLifecycle.ACTIVE
            else NextActor.UNKNOWN
            if lifecycle is WorkLifecycle.UNKNOWN
            else NextActor.NONE
        )
        works.append(
            CanonicalWorkTruth(
                key,
                lifecycle,
                watermark,
                ObservationAuthority.RESTORED_LAST_KNOWN,
                SourceHealth.PARTIAL,
                SourceFreshness.RESTORED,
                next_actor,
                _safe_label_for_key(key),
                None,
                (),
                False,
            )
        )
    work_by_key = {work.key: work for work in works}
    source_watermarks: dict[SourceKey, ProviderWatermark] = {}
    for work in work_by_key.values():
        previous = source_watermarks.get(work.key.source_key)
        if previous is None or (
            work.watermark.occurred_at_epoch,
            work.watermark.event_token.value,
        ) > (previous.occurred_at_epoch, previous.event_token.value):
            source_watermarks[work.key.source_key] = work.watermark
    state = CanonicalOperatorState(
        schema_version=1,
        generation=1 if work_by_key else 0,
        works=tuple(work_by_key.values()),
        requests=(),
        source_watermarks=tuple(source_watermarks.items()),
        timing_uncertain_sources=(),
        clock_continuity=ClockContinuityState(
            ClockContinuityStatus.STABLE,
            None,
            0,
        ),
        last_clock=None,
    )
    return state, RestoreHealth.DEGRADED if degraded else RestoreHealth.HEALTHY


def _operator_state_from_document(
    document: object,
    *,
    restore_work_keys: tuple[WorkKey, ...],
) -> tuple[CanonicalOperatorState, RestoreHealth]:
    if type(document) is not dict:
        raise ValueError("invalid latest-state document")
    if frozenset(document) == {"updated_at", "statuses"}:
        return _v1_state_from_document(
            document,
            restore_work_keys=restore_work_keys,
        )
    version = document.get("version")
    if type(version) is not int:
        raise ValueError("invalid latest-state version")
    if version == 2:
        return _v2_state_from_document(document), RestoreHealth.HEALTHY
    if version == 1:
        return _v1_state_from_document(
            document,
            restore_work_keys=restore_work_keys,
        )
    raise _UnsupportedLatestState


def default_sources(settings: AgentMonitorSettings | None = None) -> tuple[SourceSpec, ...]:
    active_settings = load_settings() if settings is None else settings
    sources: list[SourceSpec] = []
    for provider in HOOK_PROVIDERS:
        sources.append(SourceSpec(provider, detect_log_path(provider)))
        if provider == "codex" and active_settings.codex_transcripts_enabled:
            sources.append(SourceSpec(CODEX_TRANSCRIPT_PROVIDER, Path.home() / ".codex" / "sessions"))
        if provider == "claude" and active_settings.claude_transcripts_enabled:
            sources.append(SourceSpec(CLAUDE_TRANSCRIPT_PROVIDER, Path.home() / ".claude" / "projects"))
    return unique_sources(sources)


def unique_sources(sources: Iterable[SourceSpec]) -> tuple[SourceSpec, ...]:
    seen: set[tuple[str, str]] = set()
    result: list[SourceSpec] = []
    for source in sources:
        key = (source.provider, str(source.path.expanduser()))
        if key in seen:
            continue
        seen.add(key)
        result.append(SourceSpec(source.provider, source.path.expanduser()))
    return tuple(result)


def iter_codex_transcript_records(root: Path) -> Iterable[HookEvent]:
    for path in recent_transcript_files(root):
        yield from iter_codex_transcript_file(path)


def recent_transcript_files(
    root: Path,
    *,
    limit: int = CODEX_TRANSCRIPT_MAX_FILES,
) -> list[Path]:
    try:
        files = [path for path in root.rglob("*.jsonl") if path.is_file()]
    except OSError:
        return []

    files.sort(key=lambda path: safe_mtime(path), reverse=True)
    return files[:limit]


def safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def file_signature(path: Path) -> tuple[float, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime, stat.st_size)


def iter_codex_transcript_file(path: Path) -> Iterable[HookEvent]:
    session_id = codex_session_id_from_path(path)
    if session_id is None:
        return

    cwd = None
    turn_id = None
    for line in read_recent_lines(path, CODEX_TRANSCRIPT_MAX_LINES):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue

        timestamp = parse_transcript_timestamp(row)
        row_type = row.get("type")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue

        if row_type == "turn_context":
            cwd = _string_or_none(payload.get("cwd")) or cwd
            turn_id = _string_or_none(payload.get("turn_id")) or turn_id
            continue

        event = codex_transcript_event(
            payload,
            session_id=session_id,
            turn_id=turn_id,
            cwd=cwd,
            timestamp=timestamp,
            path=path,
        )
        if event is not None:
            yield event


def codex_session_id_from_path(path: Path) -> str | None:
    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        path.name,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def parse_transcript_timestamp(row: dict[str, Any]) -> datetime:
    from .models import parse_datetime

    return parse_datetime(row.get("timestamp"))


def codex_transcript_event(
    payload: dict[str, Any],
    *,
    session_id: str,
    turn_id: str | None,
    cwd: str | None,
    timestamp: datetime,
    path: Path,
) -> HookEvent | None:
    payload_type = payload.get("type")

    if payload_type == "message":
        role = payload.get("role")
        if role == "user":
            prompt = message_text_from_content(payload.get("content"))
            return HookEvent(
                provider="codex",
                logged_at=timestamp,
                event_name="UserPromptSubmit",
                raw={
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "cwd": cwd,
                    "prompt": prompt,
                    "transcript_path": str(path),
                    "source": CODEX_TRANSCRIPT_PROVIDER,
                },
                session_id=session_id,
                turn_id=turn_id,
                cwd=cwd,
                message=prompt,
            )
        if role == "assistant":
            message = message_text_from_content(payload.get("content"))
            return HookEvent(
                provider="codex",
                logged_at=timestamp,
                event_name="Stop",
                raw={
                    "hook_event_name": "Stop",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "cwd": cwd,
                    "last_assistant_message": message,
                    "transcript_path": str(path),
                    "source": CODEX_TRANSCRIPT_PROVIDER,
                },
                session_id=session_id,
                turn_id=turn_id,
                cwd=cwd,
                message=message,
            )

    if payload_type == "function_call":
        tool_name = _string_or_none(payload.get("name"))
        return HookEvent(
            provider="codex",
            logged_at=timestamp,
            event_name="PreToolUse",
            raw={
                "hook_event_name": "PreToolUse",
                "session_id": session_id,
                "turn_id": turn_id,
                "cwd": cwd,
                "tool_name": tool_name,
                "tool_input": payload.get("arguments"),
                "tool_use_id": payload.get("call_id"),
                "transcript_path": str(path),
                "source": CODEX_TRANSCRIPT_PROVIDER,
            },
            session_id=session_id,
            turn_id=turn_id,
            cwd=cwd,
            tool_name=tool_name,
        )

    if payload_type == "function_call_output":
        return HookEvent(
            provider="codex",
            logged_at=timestamp,
            event_name="PostToolUse",
            raw={
                "hook_event_name": "PostToolUse",
                "session_id": session_id,
                "turn_id": turn_id,
                "cwd": cwd,
                "tool_response": payload.get("output"),
                "tool_use_id": payload.get("call_id"),
                "transcript_path": str(path),
                "source": CODEX_TRANSCRIPT_PROVIDER,
            },
            session_id=session_id,
            turn_id=turn_id,
            cwd=cwd,
        )

    if payload_type == "task_complete":
        event_name = (
            "StopFailure"
            if codex_usage_limit_terminal(payload)
            else "Stop"
        )
        message = (
            None
            if event_name == "StopFailure"
            else _string_or_none(payload.get("last_agent_message")) or ""
        )
        raw = {
            "hook_event_name": event_name,
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": cwd,
            "transcript_path": str(path),
            "source": CODEX_TRANSCRIPT_PROVIDER,
        }
        if message:
            raw["last_assistant_message"] = message
        return HookEvent(
            provider="codex",
            logged_at=timestamp,
            event_name=event_name,
            raw=raw,
            session_id=session_id,
            turn_id=turn_id,
            cwd=cwd,
            message=message,
            _terminal_provenance=(
                _CODEX_TRANSCRIPT_USAGE_LIMIT_PROVENANCE
                if event_name == "StopFailure"
                else None
            ),
        )

    return None


def codex_usage_limit_terminal(payload: Mapping[str, Any]) -> bool:
    """Recognize only the exact structured Codex usage-limit terminal forms."""
    error = payload.get("error")
    if type(error) is not dict:
        return False
    return any(
        _string_or_none(error.get(field)) in CODEX_USAGE_LIMIT_TERMINAL_CLASSIFICATIONS
        for field in ("code", "message")
    )


def iter_claude_transcript_records(root: Path) -> Iterable[HookEvent]:
    for path in recent_transcript_files(root, limit=CLAUDE_TRANSCRIPT_MAX_FILES):
        yield from iter_claude_transcript_file(path)


def iter_claude_transcript_file(path: Path) -> Iterable[HookEvent]:
    session_id = claude_session_id_from_path(path)
    if session_id is None:
        return

    last_event_at = None
    last_event_name = None
    last_cwd = None
    emitted_event = False
    for line in read_recent_lines(path, CLAUDE_TRANSCRIPT_MAX_LINES):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue

        last_cwd = _string_or_none(row.get("cwd")) or last_cwd
        event = claude_transcript_event(
            row,
            session_id=session_id,
            timestamp=parse_transcript_timestamp(row),
            path=path,
        )
        if event is not None:
            emitted_event = True
            last_event_at = event.logged_at
            last_event_name = event.event_name
            last_cwd = event.cwd or last_cwd
            yield event

    if not emitted_event or last_event_at is None:
        return
    if not claude_mtime_can_extend_event(last_event_name):
        return

    mtime = datetime.fromtimestamp(safe_mtime(path), timezone.utc)
    if (mtime - last_event_at).total_seconds() <= CLAUDE_TRANSCRIPT_MTIME_HEARTBEAT_SKEW_SECONDS:
        return

    yield HookEvent(
        provider="claude",
        logged_at=mtime,
        event_name="Notification",
        raw={
            "hook_event_name": "Notification",
            "session_id": session_id,
            "cwd": last_cwd,
            "notification_type": "transcript_mtime",
            "message": "Claude transcript file changed after the last embedded event.",
            "transcript_path": str(path),
            "source": CLAUDE_TRANSCRIPT_PROVIDER,
        },
        session_id=session_id,
        cwd=last_cwd,
        message="Claude transcript file changed after the last embedded event.",
    )


def claude_mtime_can_extend_event(event_name: str | None) -> bool:
    return event_name in {
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
    }


def claude_session_id_from_path(path: Path) -> str | None:
    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        path.name,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def claude_transcript_event(
    row: dict[str, Any],
    *,
    session_id: str,
    timestamp: datetime,
    path: Path,
) -> HookEvent | None:
    row_type = row.get("type")
    cwd = _string_or_none(row.get("cwd"))

    if row_type == "user":
        if row.get("isMeta") is True:
            return None

        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if claude_content_has_tool_result(content) or row.get("toolUseResult") is not None:
            failed = claude_tool_result_failed(content, row.get("toolUseResult"))
            event_name = "PostToolUseFailure" if failed else "PostToolUse"
            return HookEvent(
                provider="claude",
                logged_at=timestamp,
                event_name=event_name,
                raw={
                    "hook_event_name": event_name,
                    "session_id": session_id,
                    "cwd": cwd,
                    "tool_response": row.get("toolUseResult") or content,
                    "tool_use_id": row.get("sourceToolAssistantUUID"),
                    "transcript_path": str(path),
                    "source": CLAUDE_TRANSCRIPT_PROVIDER,
                },
                session_id=session_id,
                cwd=cwd,
            )

        prompt = message_text_from_content(content)
        if not prompt:
            return None
        if prompt.strip().startswith("<task-notification>"):
            return None
        return HookEvent(
            provider="claude",
            logged_at=timestamp,
            event_name="UserPromptSubmit",
            raw={
                "hook_event_name": "UserPromptSubmit",
                "session_id": session_id,
                "cwd": cwd,
                "prompt": prompt,
                "transcript_path": str(path),
                "source": CLAUDE_TRANSCRIPT_PROVIDER,
            },
            session_id=session_id,
            cwd=cwd,
            message=prompt,
        )

    if row_type == "assistant":
        message = row.get("message")
        if not isinstance(message, dict):
            return None

        content = message.get("content")
        tool_use = first_claude_tool_use(content)
        if tool_use is not None:
            tool_name = _string_or_none(tool_use.get("name"))
            return HookEvent(
                provider="claude",
                logged_at=timestamp,
                event_name="PreToolUse",
                raw={
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "cwd": cwd,
                    "tool_name": tool_name,
                    "tool_input": tool_use.get("input"),
                    "tool_use_id": tool_use.get("id"),
                    "transcript_path": str(path),
                    "source": CLAUDE_TRANSCRIPT_PROVIDER,
                },
                session_id=session_id,
                cwd=cwd,
                tool_name=tool_name,
            )

        if message.get("stop_reason") == "end_turn":
            text = message_text_from_content(content)
            return HookEvent(
                provider="claude",
                logged_at=timestamp,
                event_name="Stop",
                raw={
                    "hook_event_name": "Stop",
                    "session_id": session_id,
                    "cwd": cwd,
                    "last_assistant_message": text,
                    "transcript_path": str(path),
                    "source": CLAUDE_TRANSCRIPT_PROVIDER,
                },
                session_id=session_id,
                cwd=cwd,
                message=text,
            )

    return None


def claude_content_has_tool_result(content: object) -> bool:
    if not isinstance(content, list):
        return False
    return any(
        isinstance(item, dict) and item.get("type") == "tool_result"
        for item in content
    )


def claude_tool_result_failed(content: object, tool_use_result: object) -> bool:
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                if item.get("is_error") is True:
                    return True
                if _tool_response_looks_failed(item.get("content")):
                    return True
    return _tool_response_looks_failed(tool_use_result)


def first_claude_tool_use(content: object) -> dict[str, Any] | None:
    if not isinstance(content, list):
        return None
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            return item
    return None


def message_text_from_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def metadata_for_record(
    record: HookEvent,
    metadata_by_session: dict[str, StatusMetadata],
    metadata_by_status: dict[str, StatusMetadata],
) -> StatusMetadata:
    session_metadata = None
    if record.session_id:
        session_metadata = metadata_by_session.setdefault(
            f"{record.provider}:session:{record.session_id}",
            StatusMetadata(),
        )
        update_metadata(session_metadata, record)

    status_metadata = metadata_by_status.setdefault(record.status_key, StatusMetadata())
    update_metadata(status_metadata, record)

    if session_metadata is None:
        return status_metadata
    return StatusMetadata(
        cwd=status_metadata.cwd or session_metadata.cwd,
        title=status_metadata.title or session_metadata.title,
        origin=status_metadata.origin or session_metadata.origin,
    )


def update_metadata(metadata: StatusMetadata, record: HookEvent) -> None:
    if record.cwd:
        metadata.cwd = record.cwd

    title = title_from_event(record)
    if title:
        metadata.title = title

    origin = record.origin or origin_label_from_payload(record.provider, record.raw)
    if origin:
        metadata.origin = origin


def _capped_detail(text: str | None) -> str | None:
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    if len(stripped) <= DETAIL_TEXT_CAP:
        return stripped
    return stripped[: DETAIL_TEXT_CAP - 1] + "\u2026"


def status_from_event(record: HookEvent, metadata: StatusMetadata | None = None) -> AgentStatus | None:
    mode = mode_for_event(record)
    if mode is None:
        return None

    metadata = metadata or StatusMetadata(cwd=record.cwd)
    if should_ignore_record(record, metadata):
        return None

    if record.agent_id:
        short_id = record.agent_id[:8]
        fallback = f"{provider_label(record.provider)} agent {short_id}"
        display_name = display_name_for_record(record, metadata, f"agent {short_id}", fallback)
    elif record.session_id:
        short_id = record.session_id[:8]
        fallback = f"{provider_label(record.provider)} session {short_id}"
        display_name = display_name_for_record(record, metadata, short_id, fallback)
    else:
        display_name = provider_label(record.provider)

    return AgentStatus(
        provider=record.provider,
        agent_id=record.status_key,
        display_name=display_name,
        mode=mode,
        updated_at=record.logged_at,
        event_name=record.event_name,
        session_id=record.session_id,
        cwd=record.cwd,
        tool_name=record.tool_name,
        message=_capped_detail(record.message),
        origin=record.origin or metadata.origin or origin_label_from_payload(record.provider, record.raw),
    )


def mode_for_event(record: HookEvent) -> AgentMode | None:
    event = record.event_name
    raw = record.raw
    explicit_mode = explicit_mode_for_record(record)
    if explicit_mode is not None:
        return explicit_mode

    if event in {"PostToolUseFailure", "PermissionDenied", "StopFailure"}:
        return AgentMode.BLOCKED_ERROR
    if event in {"PermissionRequest"}:
        return AgentMode.WAITING_FOR_INPUT
    if event == "Notification":
        notification_type = str(raw.get("notification_type", "")).strip().lower()
        message = str(raw.get("message", "")).strip().lower()
        text = " ".join(
            str(raw.get(key, ""))
            for key in ("notification_type", "message")
        ).lower()
        if notification_text_indicates_completion(notification_type, message):
            return AgentMode.COMPLETED
        if notification_text_indicates_input_needed(text):
            return AgentMode.WAITING_FOR_INPUT
        return AgentMode.WORKING
    if event in {"PreToolUse"}:
        return AgentMode.TOOL_RUNNING
    if event in {"PostToolUse"}:
        if _tool_response_looks_failed(raw.get("tool_response")):
            return AgentMode.BLOCKED_ERROR
        return AgentMode.WORKING
    if event in {"UserPromptSubmit", "PreCompact", "PostCompact", "SubagentStart"}:
        return AgentMode.WORKING
    if event == "SubagentStop":
        # A finished sub-agent can't be answered -- their reports often
        # END with question-shaped text, but there is nobody to ask, so
        # mapping them to an ask left a phantom "Needs You" glowing.
        return AgentMode.COMPLETED
    if event == "Stop":
        if _assistant_message_asks_question(raw.get("last_assistant_message")):
            return AgentMode.WAITING_FOR_INPUT
        return AgentMode.COMPLETED
    if event in {"SessionEnd"}:
        return AgentMode.COMPLETED
    if event == "SessionStart":
        return AgentMode.IDLE_READY
    return None


def explicit_mode_for_record(record: HookEvent) -> AgentMode | None:
    raw = record.raw
    for key in ("sidepulse_status", "sidepulse_mode", "sidepulse_status", "sidepulse_mode"):
        mode = explicit_mode_from_value(raw.get(key))
        if mode is not None:
            return mode

    return explicit_mode_from_message(
        raw.get("last_assistant_message") or raw.get("message")
    )


def notification_text_indicates_completion(notification_type: str, message: str) -> bool:
    text = f"{notification_type} {message}".strip()
    completion_phrases = (
        "turn complete",
        "turn completed",
        "task complete",
        "task completed",
        "completed successfully",
        "work complete",
        "work completed",
    )
    if any(phrase in text for phrase in completion_phrases):
        return True
    return notification_type == "idle_prompt" and message in {"done", "complete", "completed"}


def notification_text_indicates_input_needed(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "waiting for your input",
            "waiting for input",
            "needs your input",
            "needs input",
            "permission",
            "approval",
            "confirm",
        )
    )


def explicit_mode_from_value(value: object) -> AgentMode | None:
    if not isinstance(value, str):
        return None

    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return {
        "ask": AgentMode.WAITING_FOR_INPUT,
        "question": AgentMode.WAITING_FOR_INPUT,
        "waiting": AgentMode.WAITING_FOR_INPUT,
        "waiting_for_input": AgentMode.WAITING_FOR_INPUT,
        "input": AgentMode.WAITING_FOR_INPUT,
        "blocked": AgentMode.BLOCKED_ERROR,
        "error": AgentMode.BLOCKED_ERROR,
        "blocked_error": AgentMode.BLOCKED_ERROR,
        "working": AgentMode.WORKING,
        "tool_running": AgentMode.TOOL_RUNNING,
        "progress": AgentMode.LONG_TASK_PROGRESS,
        "long_task_progress": AgentMode.LONG_TASK_PROGRESS,
        "done": AgentMode.COMPLETED,
        "complete": AgentMode.COMPLETED,
        "completed": AgentMode.COMPLETED,
        "idle": AgentMode.IDLE_READY,
        "ready": AgentMode.IDLE_READY,
        "idle_ready": AgentMode.IDLE_READY,
    }.get(normalized)


def explicit_mode_from_message(message: object) -> AgentMode | None:
    if not isinstance(message, str):
        return None

    text = strip_markdown_code_blocks(message)
    patterns = (
        r"(?im)^\s*<!--\s*(?:sidepulse|agent[-_ ]monitor)\s*:\s*([a-z0-9_ -]+)\s*-->\s*$",
        r"(?im)^\s*<!--\s*(?:sidepulse|agent[-_ ]monitor)\s+(?:status|mode)\s*:\s*([a-z0-9_ -]+)\s*-->\s*$",
        r"(?im)^\s*\[(?:sidepulse|agent[-_ ]monitor)\s+(?:status|mode)\s*:\s*([a-z0-9_ -]+)\]\s*$",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            mode = explicit_mode_from_value(match.group(1))
            if mode is not None:
                return mode

    return None


def strip_markdown_code_blocks(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def strip_markdown_inline_code(text: str) -> str:
    return re.sub(r"`[^`\n]*`", "", text)


def aggregate_status(
    statuses: tuple[AgentStatus, ...],
    stale_statuses: tuple[AgentStatus, ...] = (),
) -> AggregateStatus:
    if not statuses:
        return AggregateStatus(
            mode=AgentMode.IDLE_READY,
            active_count=0,
            stale_count=len(stale_statuses),
            representative=None,
        )

    representative = min(
        statuses,
        key=lambda status: (
            MODE_PRIORITY.get(status.mode, MODE_PRIORITY[AgentMode.UNKNOWN]),
            -status.updated_at.timestamp(),
        ),
    )

    return AggregateStatus(
        mode=representative.mode,
        # Main agents only. `sidepulse status` reported 38 here against 3
        # real ones because every Task worker counted; a count is a count
        # wherever it is printed.
        active_count=sum(
            1
            for status in statuses
            if not status.is_subagent and status_counts_active(status)
        ),
        stale_count=len(stale_statuses),
        representative=representative,
    )


def _status_merge_key(status: AgentStatus) -> tuple[datetime, bool, bool, str]:
    return (
        -status.priority,
        status.updated_at,
        status.mode != AgentMode.COMPLETED,
        status.event_name == "SessionEnd",
        status.event_name,
    )


def _merged_status_candidates(
    statuses: Iterable[AgentStatus],
) -> tuple[AgentStatus, ...]:
    merged: dict[str, AgentStatus] = {}
    for status in statuses:
        existing = merged.get(status.agent_id)
        if existing is None or _status_merge_key(status) > _status_merge_key(existing):
            merged[status.agent_id] = status
    return tuple(merged[agent_id] for agent_id in sorted(merged))


def snapshot_from_statuses(
    statuses: tuple[AgentStatus, ...],
    *,
    sources: tuple[SourceSpec, ...],
    collected_at: datetime,
    stale_after_seconds: float,
    tool_running_timeout_seconds: float,
    completed_visible_seconds: float,
    idle_visible_seconds: float,
    post_tool_working_visible_seconds: float = POST_TOOL_WORKING_VISIBLE_SECONDS,
) -> MonitorSnapshot:
    fresh: list[AgentStatus] = []
    stale: list[AgentStatus] = []
    for status in statuses:
        status = status_for_snapshot(
            status,
            collected_at,
            post_tool_working_visible_seconds=post_tool_working_visible_seconds,
        )
        is_stale = status_is_stale(
            status,
            collected_at,
            stale_after_seconds=stale_after_seconds,
            tool_running_timeout_seconds=tool_running_timeout_seconds,
            completed_visible_seconds=completed_visible_seconds,
            idle_visible_seconds=idle_visible_seconds,
        )
        current = _replace_stale(status, is_stale)
        if is_stale:
            stale.append(current)
        else:
            fresh.append(current)

    if any(status_counts_active(status) for status in fresh):
        inactive = [status for status in fresh if not status_counts_active(status)]
        fresh = [status for status in fresh if status_counts_active(status)]
        stale.extend(_replace_stale(status, True) for status in inactive)

    fresh.sort(key=lambda status: (status.priority, -status.updated_at.timestamp()))
    stale.sort(key=lambda status: (status.priority, -status.updated_at.timestamp()))

    visible = tuple(fresh)
    stale_visible = tuple(stale)
    return MonitorSnapshot(
        aggregate=aggregate_status(visible, stale_visible),
        statuses=visible,
        stale_statuses=stale_visible,
        sources=sources,
        collected_at=collected_at,
    )


def status_is_stale(
    status: AgentStatus,
    now: datetime,
    *,
    stale_after_seconds: float,
    tool_running_timeout_seconds: float,
    completed_visible_seconds: float,
    idle_visible_seconds: float,
) -> bool:
    age = bounded_age_seconds(now, status.updated_at)
    if status.mode == AgentMode.COMPLETED and completed_visible_seconds >= 0:
        return age > completed_visible_seconds
    if status.mode == AgentMode.IDLE_READY and idle_visible_seconds >= 0:
        return age > idle_visible_seconds
    return (
        age > stale_after_seconds
        or (
            status.mode == AgentMode.TOOL_RUNNING
            and tool_running_timeout_seconds > 0
            and age > tool_running_timeout_seconds
        )
    )


def status_for_snapshot(
    status: AgentStatus,
    now: datetime,
    *,
    post_tool_working_visible_seconds: float,
) -> AgentStatus:
    if (
        status.mode == AgentMode.WORKING
        and status.event_name == "PostToolUse"
        and post_tool_working_visible_seconds >= 0
        and not is_recent(
            now,
            status.updated_at,
            post_tool_working_visible_seconds,
        )
    ):
        return _replace_mode(status, AgentMode.COMPLETED)
    return status


def agent_status_from_dict(data: object) -> AgentStatus | None:
    if not isinstance(data, dict):
        return None
    try:
        provider = str(data["provider"])
        agent_id = str(data["agent_id"])
        session_id = _string_or_none(data.get("session_id"))
        cwd = _string_or_none(data.get("cwd"))
        display_name = str(data["display_name"])
        if provider == "codex" and session_id:
            title = codex_session_title(session_id)
            if title:
                display_name = display_name_from_parts(
                    project_name(cwd),
                    title,
                    session_id[:8],
                    display_name,
                )

        mode = AgentMode(str(data["mode"]))
        updated_at = parse_datetime(data["updated_at"])
        return AgentStatus(
            provider=provider,
            agent_id=agent_id,
            display_name=display_name,
            mode=mode,
            updated_at=updated_at,
            event_name=str(data["event_name"]),
            session_id=session_id,
            cwd=cwd,
            tool_name=_string_or_none(data.get("tool_name")),
            message=_string_or_none(data.get("message")),
            origin=_string_or_none(data.get("origin")),
            stale=bool(data.get("stale", False)),
        )
    except Exception:
        return None


def status_counts_active(status: AgentStatus) -> bool:
    return status.mode not in {AgentMode.COMPLETED, AgentMode.IDLE_READY}


def track_pending_permissions(
    record: HookEvent,
    pending_permissions_by_key: dict[str, set[str]],
) -> None:
    signature = permission_signature(record)
    if record.event_name == "PermissionRequest" and signature:
        pending_permissions_by_key.setdefault(record.status_key, set()).add(signature)
        return

    if record.event_name == "PostToolUse" and signature:
        pending = pending_permissions_by_key.get(record.status_key)
        if pending is not None:
            pending.discard(signature)
            if not pending:
                pending_permissions_by_key.pop(record.status_key, None)
        return

    if record.event_name in {"Stop", "SessionEnd", "UserPromptSubmit"}:
        pending_permissions_by_key.pop(record.status_key, None)


def permission_signature(record: HookEvent) -> str | None:
    raw = record.raw
    tool_name = _string_or_none(raw.get("tool_name")) or record.tool_name
    tool_input = raw.get("tool_input")
    if not isinstance(tool_input, dict):
        return None

    command = _string_or_none(tool_input.get("command"))
    if command:
        return f"{tool_name or ''}\0{command}"

    return None


def should_ignore_status_transition(
    previous: AgentStatus | None,
    current: AgentStatus,
    pending_permission_signatures: set[str],
) -> bool:
    if (
        previous is not None
        and previous.mode == AgentMode.COMPLETED
        and current.event_name == "Notification"
    ):
        return True

    return (
        previous is not None
        and previous.mode == AgentMode.WAITING_FOR_INPUT
        and previous.event_name == "PermissionRequest"
        and current.event_name != "PermissionRequest"
        and bool(pending_permission_signatures)
    )


def should_ignore_record(record: HookEvent, metadata: StatusMetadata) -> bool:
    if record.provider != "codex":
        return False

    raw = record.raw
    text = " ".join(
        part
        for part in (
            metadata.title,
            _string_or_none(raw.get("prompt")),
            _string_or_none(raw.get("message")),
            _string_or_none(raw.get("last_assistant_message")),
        )
        if part
    ).lower()
    if not text:
        return False

    internal_prompts = (
        "generate 0 to 3 hyperpersonalized suggestions",
        "you are an expert at upholding safety and compliance standards",
    )
    return any(prompt in text for prompt in internal_prompts)


def read_recent_lines(path: Path, max_lines: int) -> list[str]:
    if max_lines <= 0:
        return []

    chunk_size = 8192
    chunks: list[bytes] = []
    newline_count = 0

    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        while position > 0 and newline_count <= max_lines:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")

    data = b"".join(reversed(chunks))
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-max_lines:]


def _replace_stale(status: AgentStatus, stale: bool) -> AgentStatus:
    if status.stale == stale:
        return status
    return AgentStatus(
        provider=status.provider,
        agent_id=status.agent_id,
        display_name=status.display_name,
        mode=status.mode,
        updated_at=status.updated_at,
        event_name=status.event_name,
        session_id=status.session_id,
        cwd=status.cwd,
        tool_name=status.tool_name,
        message=status.message,
        origin=status.origin,
        stale=stale,
        work_key=status.work_key,
        request_key=status.request_key,
    )


def _replace_mode(status: AgentStatus, mode: AgentMode) -> AgentStatus:
    if status.mode == mode:
        return status
    return AgentStatus(
        provider=status.provider,
        agent_id=status.agent_id,
        display_name=status.display_name,
        mode=mode,
        updated_at=status.updated_at,
        event_name=status.event_name,
        session_id=status.session_id,
        cwd=status.cwd,
        tool_name=status.tool_name,
        message=status.message,
        origin=status.origin,
        stale=status.stale,
        work_key=status.work_key,
        request_key=status.request_key,
    )


def title_from_event(record: HookEvent) -> str | None:
    if record.provider == "codex":
        title = codex_session_title(record.session_id)
        if title:
            return title

    if record.event_name != "UserPromptSubmit":
        return None
    return summarize_prompt(record.raw.get("prompt"))


def codex_session_title(session_id: str | None) -> str | None:
    if not session_id:
        return None
    return codex_session_titles().get(session_id)


def codex_session_titles(path: Path | None = None) -> dict[str, str]:
    index_path = path or codex_session_index_path()
    try:
        stat = index_path.stat()
    except OSError:
        return {}

    with _codex_session_index_lock:
        global _codex_session_index_cache
        cached = _codex_session_index_cache
        if (
            cached is not None
            and cached.path == index_path
            and cached.mtime == stat.st_mtime
            and cached.size == stat.st_size
        ):
            return cached.titles

        titles: dict[str, str] = {}
        for line in read_recent_lines(index_path, CODEX_SESSION_INDEX_MAX_LINES):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue

            session_id = _string_or_none(row.get("id"))
            title = _string_or_none(row.get("thread_name"))
            if session_id and title:
                titles[session_id] = truncate_text(title.strip(), 72)

        _codex_session_index_cache = CachedCodexSessionIndex(
            path=index_path,
            mtime=stat.st_mtime,
            size=stat.st_size,
            titles=titles,
        )
        return titles


def codex_session_index_path() -> Path:
    return Path.home() / ".codex" / "session_index.jsonl"


def summarize_prompt(value: object, max_len: int = 72) -> str | None:
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text or text.startswith("<task-notification>"):
        return None

    marker = re.search(
        r"##\s+My request for [^:\n]+:\s*(.*)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if marker:
        text = marker.group(1)

    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(
        r"(['\"])(?:~|/Users|/var|/private|/tmp)[^'\"]+\1",
        r"\1...\1",
        text,
    )
    text = re.sub(r"(?:~|/Users|/var|/private|/tmp)/[^\s,;)'\"`]+", "...", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"#+\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -:\n\t")

    if not text:
        return None
    return truncate_text(text, max_len)


def display_name_for_record(
    record: HookEvent,
    metadata: StatusMetadata,
    short_id: str,
    fallback: str,
) -> str:
    project = project_name(metadata.cwd or record.cwd)
    title = metadata.title

    return display_name_from_parts(project, title, short_id, fallback)


def display_name_from_parts(
    project: str | None,
    title: str | None,
    short_id: str,
    fallback: str,
) -> str:
    if project and title:
        if normalized_name_part(project) == normalized_name_part(title):
            return truncate_text(f"{title} ({short_id})", 96)
        return truncate_text(f"{project}: {title} ({short_id})", 96)
    if title:
        return truncate_text(f"{title} ({short_id})", 96)
    if project:
        return truncate_text(f"{project} ({short_id})", 96)
    return fallback


def normalized_name_part(text: str) -> str:
    return " ".join(text.replace("_", " ").replace("-", " ").split()).casefold()


def project_name(cwd: str | None) -> str | None:
    if not cwd:
        return None
    path = Path(cwd)
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate.name or str(candidate)
    return path.name or cwd


def truncate_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    trimmed = text[: max_len - 1].rstrip()
    boundary = max(trimmed.rfind(" "), trimmed.rfind(","), trimmed.rfind(";"))
    if boundary >= max_len // 2:
        trimmed = trimmed[:boundary].rstrip()
    return f"{trimmed}..."


def _tool_response_looks_failed(response: object) -> bool:
    if isinstance(response, dict):
        if response.get("interrupted") is True:
            return True
        if response.get("success") is False:
            return True
        if response.get("exit_code") not in (None, 0):
            return True
        return False

    if isinstance(response, str):
        text = response.lower()
        return "exit code: 1" in text or "traceback" in text

    return False


def _assistant_message_asks_question(message: object) -> bool:
    if not isinstance(message, str):
        return False

    text = strip_markdown_inline_code(strip_markdown_code_blocks(message))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False

    # Only the CLOSING lines: a question aimed at the user sits at the
    # end of the turn. Scanning eight lines deep flagged summaries whose
    # bullets merely started with "how"/"what" -- the phantom ask.
    for line in reversed(lines[-3:]):
        if _assistant_status_line(line):
            continue
        if _assistant_line_asks_question(line):
            return True

    return False


def _assistant_status_line(line: str) -> bool:
    text = line.strip().lower()
    return text.startswith(
        (
            "* cogitated ",
            "* recap:",
            "※ recap:",
            "recap:",
        )
    )


def _assistant_line_asks_question(line: str) -> bool:
    text = line.strip()
    if not text:
        return False

    lowered = text.lower()
    if text.endswith(":"):
        return False
    if _assistant_line_is_casual_closing_question(lowered):
        return False
    if re.search(
        r"(?:^|[.!?]\s+)(?:want me to|need me to|should i|should we|do you want me to)\b",
        lowered,
    ):
        return True

    required_question_prefixes = (
        "which ",
        "what ",
        "where ",
        "when ",
        "who ",
        "why ",
        "how ",
        "can you ",
        "could you ",
        "please confirm",
        "please choose",
        "choose ",
        "need me to ",
        "want me to ",
        "should i ",
        "should we ",
        "do you want me to ",
    )
    if text.endswith("?"):
        return lowered.startswith(required_question_prefixes)

    return lowered.startswith(
        (
            "please confirm",
            "please choose",
            "choose ",
            "need me to ",
            "want me to ",
            "should i ",
            "should we ",
            "do you want me to ",
        )
    )


def _assistant_line_is_casual_closing_question(lowered: str) -> bool:
    return lowered.startswith(
        (
            "anything else",
            "any other",
            "all good",
            "need anything else",
            "want anything else",
            "anything you want",
            "anything you'd like",
            "anything else you want",
            "anything else you'd like",
        )
    )
