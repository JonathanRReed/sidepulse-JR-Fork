"""Pure, bounded, content-safe agent browser projection.

The browser owns no persistence, clocks, provider calls, or query history. It
indexes only a closed vocabulary of product-owned labels. Canonical family
labels remain display-only, and worker documents are materialized only while a
caller projects one exact selected family.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .local_triage import LocalTriageState
from .mailbox import AgentMailboxProjection, MailboxRow, MailboxSectionKind
from .mailbox_preferences import (
    MailboxPreference,
    MailboxPreferenceMode,
    MailboxPreferenceProjection,
)
from .operator_state import (
    CanonicalOperatorState,
    CanonicalRequestTruth,
    CanonicalWorkTruth,
    RequestPhase,
)
from .provider_facts import (
    NextActor,
    RequestKey,
    SourceFreshness,
    WorkKey,
    WorkLifecycle,
)

MAX_AGENT_QUERY_SCALARS: Final = 200
MAX_AGENT_BROWSER_DOCUMENTS: Final = 100
MAX_AGENT_BROWSER_RESULTS: Final = 100
MAX_AGENT_BROWSER_CATALOG: Final = 1_000

_PROVIDER_LABELS: Final = {
    "claude": "Claude",
    "codex": "Codex",
    "cursor": "Cursor",
    "devin": "Devin",
    "grok": "Grok",
    "hermes": "Hermes",
    "openclaw": "OpenClaw",
    "opencode": "OpenCode",
}
_PROVIDER_SEARCH_LABELS: Final = frozenset({"provider", *(label.casefold() for label in _PROVIDER_LABELS.values())})
_PRODUCT_STATE_SEARCH_LABELS: Final = frozenset(
    {
        "acknowledged",
        "active",
        "completed",
        "failed",
        "idle",
        "needs you",
        "pinned",
        "snoozed",
        "stale",
        "timing uncertain",
        "unknown",
        "waiting",
        "watched",
        "woke",
    }
)
_SECTION_ORDER: Final = {
    MailboxSectionKind.NEEDS_YOU: 0,
    MailboxSectionKind.IN_PROGRESS: 1,
    MailboxSectionKind.READY_FOR_REVIEW: 2,
    MailboxSectionKind.RECENT: 3,
}
_REQUEST_PHASE_ORDER: Final = {
    RequestPhase.LIVE_UNACKNOWLEDGED: 0,
    RequestPhase.LIVE_ACKNOWLEDGED: 1,
    RequestPhase.STALE_HOLD: 2,
    RequestPhase.RESOLVED: 3,
    RequestPhase.UNKNOWN_EXPIRED: 4,
}
_DEFAULT_MAILBOX_ORDER: Final = 2_147_483_647
_DISPLAY_WORKER_FRESHNESS: Final = frozenset(
    {
        SourceFreshness.FRESH,
        SourceFreshness.PARTIAL,
        SourceFreshness.TIMING_UNCERTAIN,
    }
)


class SearchLabelSource(str, Enum):
    PROVIDER = "provider"
    PRODUCT_STATE = "product-state"


@dataclass(frozen=True, slots=True)
class ApprovedSearchLabel:
    text: str
    source: SearchLabelSource


@dataclass(frozen=True, slots=True)
class _WorkerSeed:
    work: CanonicalWorkTruth
    requests: tuple[CanonicalRequestTruth, ...]
    acknowledged_request_keys: frozenset[RequestKey]
    mailbox_order: int


@dataclass(frozen=True, slots=True)
class _PrimarySeed:
    work_key: WorkKey
    provider_label: ApprovedSearchLabel
    safe_family_label: str
    search_labels: tuple[ApprovedSearchLabel, ...]
    lifecycle_label: str
    actionable: bool
    request_phase: RequestPhase | None
    source_freshness: SourceFreshness
    worker_count: int
    pinned: bool
    watched: bool
    snoozed: bool
    woke: bool
    acknowledged: bool
    timing_uncertain: bool
    _mailbox_order: int
    _shelf: MailboxSectionKind | None
    _worker_seeds: tuple[_WorkerSeed, ...]


@dataclass(frozen=True, slots=True)
class AgentBrowserDocument:
    work_key: WorkKey
    provider_label: ApprovedSearchLabel
    safe_family_label: str
    search_labels: tuple[ApprovedSearchLabel, ...]
    lifecycle_label: str
    actionable: bool
    request_phase: RequestPhase | None
    source_freshness: SourceFreshness
    worker_count: int
    pinned: bool
    watched: bool
    snoozed: bool
    woke: bool
    acknowledged: bool
    timing_uncertain: bool


class _AgentBrowserDocuments(tuple):
    def __new__(
        cls,
        documents: tuple[AgentBrowserDocument, ...],
        primary_catalog: tuple[_PrimarySeed, ...],
    ):
        instance = super().__new__(cls, documents)
        instance._primary_catalog = primary_catalog
        return instance


@dataclass(frozen=True, slots=True)
class AgentBrowserQuery:
    text: str
    shelf: MailboxSectionKind | None = None
    family_key: WorkKey | None = None


@dataclass(frozen=True, slots=True)
class AgentBrowserProjection:
    generation: int
    rows: tuple[AgentBrowserDocument, ...]
    total_count: int
    scoped_count: int
    selected_work_key: WorkKey | None


def normalize_agent_query(text: object) -> str:
    """Return the bounded NFKC/casefold query, or empty for rejected input."""
    if type(text) is not str or _has_control_character(text):
        return ""
    bounded = text[:MAX_AGENT_QUERY_SCALARS]
    normalized = unicodedata.normalize("NFKC", bounded).casefold()
    if _has_control_character(normalized):
        return ""
    return " ".join(normalized.split())[:MAX_AGENT_QUERY_SCALARS]


def build_agent_browser_documents(
    state: CanonicalOperatorState,
    mailbox: AgentMailboxProjection,
    preference_projection: MailboxPreferenceProjection,
    local_triage: LocalTriageState,
) -> tuple[AgentBrowserDocument, ...]:
    """Build at most one global document for each retained primary family."""
    if type(state) is not CanonicalOperatorState:
        raise ValueError("invalid canonical operator state")
    if type(mailbox) is not AgentMailboxProjection:
        raise ValueError("invalid canonical mailbox projection")
    if type(preference_projection) is not MailboxPreferenceProjection:
        raise ValueError("invalid mailbox preference projection")
    if type(local_triage) is not LocalTriageState:
        raise ValueError("invalid local triage state")

    roots = {work.key: work for work in state.works if work.parent_key is None}
    children: dict[WorkKey, list[CanonicalWorkTruth]] = {key: [] for key in roots}
    for work in state.works:
        if (
            work.parent_key in children
            and work.source_freshness in _DISPLAY_WORKER_FRESHNESS
        ):
            children[work.parent_key].append(work)

    retained_order = _retained_root_order(mailbox, roots)
    if not retained_order:
        retained_order = {key: index for index, key in enumerate(sorted(roots, key=_work_key_sort_key))}
    preferences = {
        preference.work_key: preference
        for preference in preference_projection.retained_preferences
        if type(preference) is MailboxPreference and preference.work_key in roots
    }
    woke_keys = frozenset(key for key in preference_projection.woke_work_keys if type(key) is WorkKey and key in roots)
    acknowledged_keys = frozenset(item.request_key for item in local_triage.acknowledgements)
    request_by_work: dict[WorkKey, list[CanonicalRequestTruth]] = {}
    for request in state.requests:
        request_by_work.setdefault(request.key.work_key, []).append(request)

    visible_positions, visible_shelves = _visible_mailbox_positions(preference_projection.projection)
    candidates = []
    for key, stable_order in retained_order.items():
        work = roots.get(key)
        if work is None:
            continue
        family_workers = tuple(sorted(children[key], key=lambda item: _work_key_sort_key(item.key)))
        family = (work, *family_workers)
        family_requests = tuple(
            sorted(
                (request for member in family for request in request_by_work.get(member.key, ())),
                key=lambda item: (
                    _REQUEST_PHASE_ORDER[item.phase],
                    item.watermark.occurred_at_epoch,
                    _work_key_sort_key(item.key.work_key),
                    item.key.request_id.value,
                ),
            )
        )
        actionable = _requests_are_actionable(family_requests)
        shelf = visible_shelves.get(key) or _shelf_for(work.lifecycle, actionable)
        preference = preferences.get(key)
        order_key = _family_order_key(
            key,
            shelf=shelf,
            preference=preference,
            visible_position=visible_positions.get(key),
            stable_order=stable_order,
        )
        candidates.append(
            (
                order_key,
                work,
                family_workers,
                family_requests,
                shelf,
                preference,
            )
        )

    primary_seeds = []
    for mailbox_order, candidate in enumerate(sorted(candidates, key=lambda item: item[0])):
        _key, work, family_workers, family_requests, shelf, preference = candidate
        worker_seeds = tuple(
            _WorkerSeed(
                worker,
                tuple(request_by_work.get(worker.key, ())),
                acknowledged_keys,
                index,
            )
            for index, worker in enumerate(family_workers)
        )
        primary_seeds.append(
            _primary_seed_for_work(
                work,
                requests=family_requests,
                acknowledged_request_keys=acknowledged_keys,
                worker_count=len(family_workers),
                pinned=(preference is not None and preference.mode is MailboxPreferenceMode.PINNED),
                watched=(preference is not None and preference.mode is MailboxPreferenceMode.WATCHED),
                snoozed=(
                    preference is not None
                    and preference.snoozed_at is not None
                    and preference.snoozed_until is not None
                ),
                woke=work.key in woke_keys,
                timing_uncertain=any(member.timing_uncertain for member in (work, *family_workers)),
                mailbox_order=mailbox_order,
                shelf=shelf,
                worker_seeds=worker_seeds,
            )
        )
    catalog = tuple(primary_seeds)
    return _AgentBrowserDocuments(
        tuple(_document_from_primary_seed(seed) for seed in catalog[:MAX_AGENT_BROWSER_DOCUMENTS]),
        catalog,
    )


def project_agent_browser(
    documents: Iterable[AgentBrowserDocument],
    query: AgentBrowserQuery,
    *,
    generation: int,
    selected_work_key: WorkKey | None,
    max_results: int = MAX_AGENT_BROWSER_RESULTS,
) -> AgentBrowserProjection:
    """Project one ephemeral root, shelf, search, or selected-family view."""
    if type(query) is not AgentBrowserQuery:
        raise ValueError("invalid agent browser query")
    if type(generation) is not int or generation < 0:
        raise ValueError("invalid agent browser generation")
    if type(max_results) is not int or max_results < 0:
        raise ValueError("max_results must be non-negative")
    if query.shelf is not None and type(query.shelf) is not MailboxSectionKind:
        raise ValueError("invalid agent browser shelf")
    if query.family_key is not None and type(query.family_key) is not WorkKey:
        raise ValueError("invalid agent browser family")

    primary_catalog = _canonical_primary_catalog(documents)
    total_count = len(primary_catalog)
    scoped: tuple[AgentBrowserDocument | _PrimarySeed, ...]
    if query.family_key is not None:
        family = next(
            (seed for seed in primary_catalog if seed.work_key == query.family_key),
            None,
        )
        scoped = () if family is None else tuple(_primary_seed_for_worker(seed) for seed in family._worker_seeds)
    elif query.shelf is not None:
        scoped = tuple(seed for seed in primary_catalog if seed._shelf is query.shelf)
    else:
        scoped = primary_catalog

    rejected_query = type(query.text) is not str or _has_control_character(query.text)
    normalized_query = normalize_agent_query(query.text)
    if rejected_query:
        ranked: tuple[AgentBrowserDocument | _PrimarySeed, ...] = ()
    elif not normalized_query:
        ranked = tuple(
            sorted(
                scoped,
                key=lambda document: (
                    document._mailbox_order,
                    _work_key_sort_key(document.work_key),
                ),
            )
        )
    else:
        matches = []
        for document in scoped:
            tier = _document_match_tier(document, normalized_query)
            if tier is not None:
                matches.append(
                    (
                        tier,
                        document._mailbox_order,
                        _work_key_sort_key(document.work_key),
                        document,
                    )
                )
        ranked = tuple(item[-1] for item in sorted(matches, key=lambda item: item[:-1]))

    selected = (
        selected_work_key
        if type(selected_work_key) is WorkKey and any(document.work_key == selected_work_key for document in ranked)
        else None
    )
    result_limit = min(max_results, MAX_AGENT_BROWSER_RESULTS)
    start = 0
    if selected is not None and result_limit > 0:
        selected_index = next(index for index, document in enumerate(ranked) if document.work_key == selected)
        if selected_index >= result_limit:
            start = selected_index - result_limit + 1
    visible_rows = ranked[start : start + result_limit]
    return AgentBrowserProjection(
        generation=generation,
        rows=tuple(_materialize_primary_row(row) for row in visible_rows),
        total_count=total_count,
        scoped_count=len(ranked),
        selected_work_key=selected,
    )


def _has_control_character(text: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in text)


def _source_key_sort_key(source) -> tuple[str, str, str, str]:
    return (
        source.provider_id,
        source.adapter_id,
        source.source_instance_id,
        source.capability_id,
    )


def _work_key_sort_key(key: WorkKey) -> tuple[tuple[str, str, str, str], str]:
    return (_source_key_sort_key(key.source_key), key.work_id.value)


def _retained_root_order(
    mailbox: AgentMailboxProjection,
    roots: dict[WorkKey, CanonicalWorkTruth],
) -> dict[WorkKey, int]:
    retained: dict[WorkKey, int] = {}
    for item in mailbox.retained_order:
        if not (
            type(item) is tuple
            and len(item) == 2
            and type(item[0]) is WorkKey
            and type(item[1]) is int
            and item[1] >= 0
            and item[0] in roots
        ):
            continue
        retained.setdefault(item[0], item[1])
    return retained


def _visible_mailbox_positions(
    mailbox: AgentMailboxProjection,
) -> tuple[dict[WorkKey, int], dict[WorkKey, MailboxSectionKind]]:
    positions: dict[WorkKey, int] = {}
    shelves: dict[WorkKey, MailboxSectionKind] = {}
    position = 0
    for section in mailbox.sections:
        if type(section.kind) is not MailboxSectionKind:
            continue
        for row in section.rows:
            if type(row) is MailboxRow and row.work_key not in positions:
                positions[row.work_key] = position
                shelves[row.work_key] = section.kind
            position += 1
    return positions, shelves


def _family_order_key(
    key: WorkKey,
    *,
    shelf: MailboxSectionKind,
    preference: MailboxPreference | None,
    visible_position: int | None,
    stable_order: int,
) -> tuple[object, ...]:
    if preference is not None and preference.mode is MailboxPreferenceMode.PINNED:
        preference_order = (0, preference.pin_order if preference.pin_order is not None else 0)
    elif preference is not None and preference.mode is MailboxPreferenceMode.WATCHED:
        preference_order = (1, 0)
    else:
        preference_order = (2, 0)
    existing_order = visible_position if visible_position is not None else stable_order
    return (
        _SECTION_ORDER[shelf],
        *preference_order,
        existing_order,
        _work_key_sort_key(key),
    )


def _shelf_for(
    lifecycle: WorkLifecycle,
    actionable: bool,
) -> MailboxSectionKind:
    if actionable:
        return MailboxSectionKind.NEEDS_YOU
    if lifecycle in {WorkLifecycle.ACTIVE, WorkLifecycle.WAITING}:
        return MailboxSectionKind.IN_PROGRESS
    if lifecycle in {WorkLifecycle.COMPLETED, WorkLifecycle.FAILED}:
        return MailboxSectionKind.READY_FOR_REVIEW
    return MailboxSectionKind.RECENT


def _requests_are_actionable(requests: tuple[CanonicalRequestTruth, ...]) -> bool:
    return any(
        request.phase in {RequestPhase.LIVE_UNACKNOWLEDGED, RequestPhase.LIVE_ACKNOWLEDGED}
        and request.next_actor is NextActor.USER
        for request in requests
    )


def _authoritative_request_phase(
    requests: tuple[CanonicalRequestTruth, ...],
) -> RequestPhase | None:
    return min(
        (request.phase for request in requests),
        key=lambda phase: _REQUEST_PHASE_ORDER[phase],
        default=None,
    )


def _provider_label(work_key: WorkKey) -> ApprovedSearchLabel:
    text = _PROVIDER_LABELS.get(work_key.source_key.provider_id, "Provider")
    return ApprovedSearchLabel(text, SearchLabelSource.PROVIDER)


def _primary_seed_for_work(
    work: CanonicalWorkTruth,
    *,
    requests: tuple[CanonicalRequestTruth, ...],
    acknowledged_request_keys: frozenset[RequestKey],
    worker_count: int,
    pinned: bool,
    watched: bool,
    snoozed: bool,
    woke: bool,
    timing_uncertain: bool,
    mailbox_order: int,
    shelf: MailboxSectionKind | None,
    worker_seeds: tuple[_WorkerSeed, ...],
) -> _PrimarySeed:
    actionable = _requests_are_actionable(requests)
    lifecycle_label = "needs you" if actionable else work.lifecycle.value
    acknowledged = any(request.key in acknowledged_request_keys for request in requests)
    stale = work.source_freshness is SourceFreshness.STALE
    provider_label = _provider_label(work.key)
    state_values = [lifecycle_label]
    if pinned:
        state_values.append("pinned")
    if watched:
        state_values.append("watched")
    if snoozed:
        state_values.append("snoozed")
    if woke:
        state_values.append("woke")
    if acknowledged:
        state_values.append("acknowledged")
    if stale:
        state_values.append("stale")
    if timing_uncertain:
        state_values.append("timing uncertain")
    search_labels = (
        provider_label,
        *(ApprovedSearchLabel(value, SearchLabelSource.PRODUCT_STATE) for value in state_values),
    )
    return _PrimarySeed(
        work_key=work.key,
        provider_label=provider_label,
        safe_family_label=work.safe_label,
        search_labels=search_labels,
        lifecycle_label=lifecycle_label,
        actionable=actionable,
        request_phase=_authoritative_request_phase(requests),
        source_freshness=work.source_freshness,
        worker_count=worker_count,
        pinned=pinned,
        watched=watched,
        snoozed=snoozed,
        woke=woke,
        acknowledged=acknowledged,
        timing_uncertain=timing_uncertain,
        _mailbox_order=mailbox_order,
        _shelf=shelf,
        _worker_seeds=worker_seeds,
    )


def _document_from_primary_seed(seed: _PrimarySeed) -> AgentBrowserDocument:
    return AgentBrowserDocument(
        work_key=seed.work_key,
        provider_label=seed.provider_label,
        safe_family_label=seed.safe_family_label,
        search_labels=seed.search_labels,
        lifecycle_label=seed.lifecycle_label,
        actionable=seed.actionable,
        request_phase=seed.request_phase,
        source_freshness=seed.source_freshness,
        worker_count=seed.worker_count,
        pinned=seed.pinned,
        watched=seed.watched,
        snoozed=seed.snoozed,
        woke=seed.woke,
        acknowledged=seed.acknowledged,
        timing_uncertain=seed.timing_uncertain,
    )


def _primary_seed_for_worker(seed: _WorkerSeed) -> _PrimarySeed:
    return _primary_seed_for_work(
        seed.work,
        requests=seed.requests,
        acknowledged_request_keys=seed.acknowledged_request_keys,
        worker_count=0,
        pinned=False,
        watched=False,
        snoozed=False,
        woke=False,
        timing_uncertain=seed.work.timing_uncertain,
        mailbox_order=seed.mailbox_order,
        shelf=None,
        worker_seeds=(),
    )


def _primary_seed_from_document(
    document: AgentBrowserDocument,
    *,
    mailbox_order: int,
) -> _PrimarySeed:
    return _PrimarySeed(
        work_key=document.work_key,
        provider_label=document.provider_label,
        safe_family_label=document.safe_family_label,
        search_labels=document.search_labels,
        lifecycle_label=document.lifecycle_label,
        actionable=document.actionable,
        request_phase=document.request_phase,
        source_freshness=document.source_freshness,
        worker_count=document.worker_count,
        pinned=document.pinned,
        watched=document.watched,
        snoozed=document.snoozed,
        woke=document.woke,
        acknowledged=document.acknowledged,
        timing_uncertain=document.timing_uncertain,
        _mailbox_order=mailbox_order,
        _shelf=None,
        _worker_seeds=(),
    )


def _canonical_primary_catalog(
    documents: Iterable[AgentBrowserDocument],
) -> tuple[_PrimarySeed, ...]:
    if type(documents) is _AgentBrowserDocuments:
        raw_catalog = documents._primary_catalog
        if not (
            type(raw_catalog) is tuple
            and len(raw_catalog) <= MAX_AGENT_BROWSER_CATALOG
            and all(type(seed) is _PrimarySeed for seed in raw_catalog)
        ):
            raise ValueError("invalid agent browser catalog")
        seeds = raw_catalog
    else:
        try:
            values = tuple(documents)
        except TypeError as error:
            raise ValueError("invalid agent browser documents") from error
        valid = [
            document
            for document in values
            if type(document) is AgentBrowserDocument and type(document.work_key) is WorkKey
        ]
        valid.sort(
            key=lambda document: (
                _work_key_sort_key(document.work_key),
                _document_choice_key(document),
            )
        )
        seeds = tuple(
            _primary_seed_from_document(document, mailbox_order=index)
            for index, document in enumerate(valid[:MAX_AGENT_BROWSER_CATALOG])
        )

    unique: dict[WorkKey, _PrimarySeed] = {}
    for seed in seeds:
        if type(seed.work_key) is WorkKey:
            unique.setdefault(seed.work_key, seed)
    return tuple(
        sorted(
            unique.values(),
            key=lambda seed: (
                seed._mailbox_order,
                _work_key_sort_key(seed.work_key),
            ),
        )
    )


def _materialize_primary_row(
    row: AgentBrowserDocument | _PrimarySeed,
) -> AgentBrowserDocument:
    if type(row) is AgentBrowserDocument:
        return row
    return _document_from_primary_seed(row)


def _document_choice_key(document: AgentBrowserDocument) -> tuple[object, ...]:
    labels = tuple(
        sorted(
            (
                label.source.value,
                label.text,
            )
            for label in document.search_labels
            if type(label) is ApprovedSearchLabel
            and type(label.source) is SearchLabelSource
            and type(label.text) is str
        )
    )
    return (
        document.safe_family_label,
        document.lifecycle_label,
        document.actionable,
        document.worker_count,
        document.pinned,
        document.watched,
        document.snoozed,
        document.woke,
        document.acknowledged,
        document.timing_uncertain,
        labels,
    )


def _approved_label_text(label: object) -> str | None:
    if not (type(label) is ApprovedSearchLabel and type(label.text) is str and type(label.source) is SearchLabelSource):
        return None
    text = normalize_agent_query(label.text)
    if not text or len(text) > 32:
        return None
    allowed = _PROVIDER_SEARCH_LABELS if label.source is SearchLabelSource.PROVIDER else _PRODUCT_STATE_SEARCH_LABELS
    return text if text in allowed else None


def _document_match_tier(
    document: AgentBrowserDocument | _PrimarySeed,
    query: str,
) -> int | None:
    labels = (document.provider_label, *document.search_labels)
    return min(
        (
            tier
            for label in labels
            if (text := _approved_label_text(label)) is not None
            if (tier := _match_tier(text, query)) is not None
        ),
        default=None,
    )


def _match_tier(text: str, query: str) -> int | None:
    if text == query:
        return 0
    if text.startswith(query):
        return 1
    offset = text.find(query)
    while offset >= 0:
        if offset > 0 and not text[offset - 1].isalnum():
            return 2
        offset = text.find(query, offset + 1)
    if query in text:
        return 3
    query_index = 0
    for character in text:
        if character == query[query_index]:
            query_index += 1
            if query_index == len(query):
                return 4
    return None
