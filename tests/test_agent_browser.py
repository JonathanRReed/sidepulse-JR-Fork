from __future__ import annotations

from dataclasses import asdict, fields, replace
from statistics import quantiles
from time import perf_counter

import pytest

from sidepulse.agent_browser import (
    AgentBrowserDocument,
    AgentBrowserProjection,
    AgentBrowserQuery,
    ApprovedSearchLabel,
    SearchLabelSource,
    build_agent_browser_documents,
    normalize_agent_query,
    project_agent_browser,
)
from sidepulse.capacity_types import SourceKey
from sidepulse.local_triage import LocalAcknowledgement, LocalTriageState
from sidepulse.mailbox import (
    AgentMailboxProjection,
    MailboxRow,
    MailboxSection,
    MailboxSectionKind,
    project_canonical_mailbox,
)
from sidepulse.mailbox_preferences import (
    MailboxPreference,
    MailboxPreferenceMode,
    MailboxPreferenceProjection,
)
from sidepulse.operator_state import (
    AcknowledgementEligibility,
    CanonicalOperatorState,
    CanonicalRequestTruth,
    CanonicalWorkTruth,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
    empty_operator_state,
)
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderWatermark,
    RequestIdentifier,
    RequestKey,
    RequestKind,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
    WorkLifecycle,
)


def _source(instance: str = "local:01", provider: str = "codex") -> SourceKey:
    return SourceKey(provider, "hooks", instance, "live_agent_events")


def _work_key(value: str, *, source: SourceKey | None = None) -> WorkKey:
    return WorkKey(source or _source(), WorkIdentifier(value))


def _watermark(key: WorkKey, rank: int) -> ProviderWatermark:
    return ProviderWatermark(
        source_key=key.source_key,
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=1_800_000_000.0 + rank,
        event_token=EventToken(f"event:{rank}"),
        sequence=None,
        tie_break_rank=rank % 256,
    )


def _work(
    key: WorkKey,
    *,
    rank: int,
    parent_key: WorkKey | None = None,
    lifecycle: WorkLifecycle = WorkLifecycle.ACTIVE,
    safe_label: str | None = None,
    request_keys: tuple[RequestKey, ...] = (),
    freshness: SourceFreshness = SourceFreshness.FRESH,
    timing_uncertain: bool = False,
) -> CanonicalWorkTruth:
    return CanonicalWorkTruth(
        key=key,
        lifecycle=lifecycle,
        watermark=_watermark(key, rank),
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        source_health=SourceHealth.HEALTHY,
        source_freshness=freshness,
        next_actor=NextActor.PROVIDER,
        safe_label=safe_label or f"Codex {key.work_id.value}",
        parent_key=parent_key,
        request_keys=request_keys,
        timing_uncertain=timing_uncertain,
    )


def _request(key: RequestKey, *, rank: int) -> CanonicalRequestTruth:
    watermark = _watermark(key.work_key, rank)
    event_key = SemanticEventKey(key, TransitionKind.REQUEST_OPENED, watermark)
    return CanonicalRequestTruth(
        key=key,
        phase=RequestPhase.LIVE_UNACKNOWLEDGED,
        request_kind=RequestKind.PERMISSION,
        next_actor=NextActor.USER,
        watermark=watermark,
        source_freshness=SourceFreshness.FRESH,
        acknowledgement_eligibility=AcknowledgementEligibility.ELIGIBLE,
        semantic_event_key=event_key,
        opened_at_epoch=watermark.occurred_at_epoch,
        eligible_elapsed_seconds=1.0,
        _observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    )


def _state(
    works: tuple[CanonicalWorkTruth, ...],
    requests: tuple[CanonicalRequestTruth, ...] = (),
    *,
    generation: int = 7,
) -> CanonicalOperatorState:
    return replace(
        empty_operator_state(),
        generation=generation,
        works=works,
        requests=requests,
    )


def _mailbox_row(
    work: CanonicalWorkTruth,
    *,
    stable_order: int,
    actionable: bool = False,
    request_key: RequestKey | None = None,
    request_keys: tuple[RequestKey, ...] = (),
    worker_count: int = 0,
) -> MailboxRow:
    return MailboxRow(
        work_key=work.key,
        request_key=request_key,
        safe_label=work.safe_label,
        lifecycle=WorkLifecycle.WAITING if actionable else work.lifecycle,
        next_actor=NextActor.USER if actionable else work.next_actor,
        source_freshness=work.source_freshness,
        request_keys=request_keys,
        actionable=actionable,
        worker_count=worker_count,
        updated_at_epoch=work.watermark.occurred_at_epoch,
        stable_order=stable_order,
        timing_uncertain=work.timing_uncertain,
    )


def _mailbox(
    roots: tuple[CanonicalWorkTruth, ...],
    *,
    visible_limit: int = 12,
) -> AgentMailboxProjection:
    rows = tuple(_mailbox_row(work, stable_order=index) for index, work in enumerate(roots[:visible_limit]))
    return AgentMailboxProjection(
        sections=(
            MailboxSection(MailboxSectionKind.NEEDS_YOU, (), 0),
            MailboxSection(
                MailboxSectionKind.IN_PROGRESS,
                rows,
                max(0, len(roots) - visible_limit),
            ),
            MailboxSection(MailboxSectionKind.READY_FOR_REVIEW, (), 0),
            MailboxSection(MailboxSectionKind.RECENT, (), 0),
        ),
        active_count=len(roots),
        needs_you_count=0,
        ready_count=0,
        retained_order=tuple((work.key, index) for index, work in enumerate(roots)),
    )


def _preference_projection(
    mailbox: AgentMailboxProjection,
    preferences: tuple[MailboxPreference, ...] = (),
    *,
    woke: tuple[WorkKey, ...] = (),
) -> MailboxPreferenceProjection:
    return MailboxPreferenceProjection(mailbox, preferences, None, woke)


def _document(
    value: str,
    *labels: ApprovedSearchLabel,
    source: SourceKey | None = None,
) -> AgentBrowserDocument:
    provider = ApprovedSearchLabel("Codex", SearchLabelSource.PROVIDER)
    return AgentBrowserDocument(
        work_key=_work_key(value, source=source),
        provider_label=provider,
        safe_family_label=f"Visible {value}",
        search_labels=(provider, *labels),
        lifecycle_label="active",
        actionable=False,
        request_phase=None,
        source_freshness=SourceFreshness.FRESH,
        worker_count=0,
        pinned=False,
        watched=False,
        snoozed=False,
        woke=False,
        acknowledged=False,
        timing_uncertain=False,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("  \uff23\uff2f\uff24\uff25\uff38\u2003Active  ", "codex active"),
        ("Straße", "strasse"),
        ("", ""),
        (None, ""),
        (7, ""),
        ("x" * 201, "x" * 200),
        ("ß" * 200, "s" * 200),
        ("\ufb00" * 200, "f" * 200),
        ("codex\nactive", ""),
        ("codex\u0000active", ""),
        ("codex\u200bactive", ""),
    ),
)
def test_query_normalization_is_nfkc_casefolded_bounded_and_control_safe(
    raw: object,
    expected: str,
) -> None:
    """Removing one normalization or rejection branch would expose unstable query behavior."""
    assert normalize_agent_query(raw) == expected


@pytest.mark.parametrize(
    ("query", "label", "matches"),
    (
        ("active", "active", True),
        ("act", "active", True),
        ("you", "needs you", True),
        ("tiv", "active", True),
        ("cde", "codex", True),
        ("czd", "codex", False),
    ),
)
def test_each_bounded_ranking_tier_is_searchable(
    query: str,
    label: str,
    matches: bool,
) -> None:
    """Breaking any exact-to-subsequence matcher would lose a documented search tier."""
    source = SearchLabelSource.PROVIDER if label == "codex" else SearchLabelSource.PRODUCT_STATE
    document = _document("rank:01", ApprovedSearchLabel(label, source))

    result = project_agent_browser(
        (document,),
        AgentBrowserQuery(query),
        generation=1,
        selected_work_key=None,
    )

    assert bool(result.rows) is matches


def test_ranking_keeps_mailbox_order_and_work_key_as_the_final_tie_break() -> None:
    """Sorting ties by input permutation would make refreshes reorder equivalent matches."""
    first = _document("work:01", ApprovedSearchLabel("active", SearchLabelSource.PRODUCT_STATE))
    second = _document("work:02", ApprovedSearchLabel("active", SearchLabelSource.PRODUCT_STATE))

    forward = project_agent_browser(
        (second, first),
        AgentBrowserQuery("active"),
        generation=2,
        selected_work_key=None,
    )
    reverse = project_agent_browser(
        (first, second),
        AgentBrowserQuery("active"),
        generation=2,
        selected_work_key=None,
    )

    expected = (first.work_key, second.work_key)
    assert tuple(row.work_key for row in forward.rows) == expected
    assert tuple(row.work_key for row in reverse.rows) == expected


def test_duplicate_work_keys_and_source_scoped_identity_are_exact() -> None:
    """A lossy ID or duplicate branch would merge separate sources or show one key twice."""
    source_a = _source("local:01")
    source_b = _source("local:02")
    key_a = _document("work:shared", source=source_a)
    duplicate_a = _document("work:shared", source=source_a)
    key_b = _document("work:shared", source=source_b)

    result = project_agent_browser(
        (key_b, duplicate_a, key_a),
        AgentBrowserQuery(""),
        generation=3,
        selected_work_key=None,
    )

    assert tuple(row.work_key for row in result.rows) == (key_a.work_key, key_b.work_key)
    assert result.total_count == 2
    assert result.scoped_count == 2


def test_only_source_correct_product_vocabulary_enters_the_index() -> None:
    """Trusting typed-but-unapproved copy would index prompts, paths, secrets, or opaque IDs."""
    unsafe = (
        ApprovedSearchLabel("Fix customer login prompt", SearchLabelSource.PRODUCT_STATE),
        ApprovedSearchLabel("/Users/alice/private/project", SearchLabelSource.PROVIDER),
        ApprovedSearchLabel("sk-live-secret-value", SearchLabelSource.PROVIDER),
        ApprovedSearchLabel("01J9QZ8W6R7 opaque", SearchLabelSource.PRODUCT_STATE),
        ApprovedSearchLabel("Project Nightingale", SearchLabelSource.PRODUCT_STATE),
        ApprovedSearchLabel("Codex", SearchLabelSource.PRODUCT_STATE),
        ApprovedSearchLabel("active", SearchLabelSource.PROVIDER),
    )
    document = _document(
        "opaque:work:9f8e7d",
        *unsafe,
        ApprovedSearchLabel("pinned", SearchLabelSource.PRODUCT_STATE),
    )

    for query in ("customer", "alice", "secret", "01j9", "nightingale", "opaque"):
        result = project_agent_browser(
            (document,),
            AgentBrowserQuery(query),
            generation=4,
            selected_work_key=None,
        )
        assert result.rows == ()

    assert project_agent_browser(
        (document,),
        AgentBrowserQuery("pinned"),
        generation=4,
        selected_work_key=None,
    ).rows == (document,)


def test_unknown_search_label_source_is_ignored() -> None:
    """Accepting an unrecognized authority marker would widen the privacy boundary."""
    forged = object.__new__(ApprovedSearchLabel)
    object.__setattr__(forged, "text", "Codex")
    object.__setattr__(forged, "source", "provider")
    document = _document("work:unknown-source", forged)

    result = project_agent_browser(
        (document,),
        AgentBrowserQuery("codex"),
        generation=5,
        selected_work_key=None,
    )

    assert result.rows == (document,)
    providerless = replace(document, provider_label=forged, search_labels=(forged,))
    assert (
        project_agent_browser(
            (providerless,),
            AgentBrowserQuery("codex"),
            generation=5,
            selected_work_key=None,
        ).rows
        == ()
    )


def test_build_indexes_product_tokens_but_never_visible_family_or_identity_copy() -> None:
    """Indexing visible/provider-derived copy would disclose prompt, path, project, or ID text."""
    root_key = _work_key("opaque:root:9f8e7d")
    request_key = RequestKey(root_key, RequestIdentifier("request:01"))
    request = _request(request_key, rank=2)
    root = _work(
        root_key,
        rank=1,
        safe_label="Project Nightingale /Users/alice sk-live-secret",
        request_keys=(request_key,),
        freshness=SourceFreshness.STALE,
        timing_uncertain=True,
    )
    row = _mailbox_row(
        root,
        stable_order=0,
        actionable=True,
        request_key=request_key,
        request_keys=(request_key,),
    )
    mailbox = AgentMailboxProjection(
        sections=(
            MailboxSection(MailboxSectionKind.NEEDS_YOU, (row,), 0),
            MailboxSection(MailboxSectionKind.IN_PROGRESS, (), 0),
            MailboxSection(MailboxSectionKind.READY_FOR_REVIEW, (), 0),
            MailboxSection(MailboxSectionKind.RECENT, (), 0),
        ),
        active_count=1,
        needs_you_count=1,
        ready_count=0,
        retained_order=((root_key, 0),),
    )
    preference = MailboxPreference(
        root_key,
        MailboxPreferenceMode.PINNED,
        0,
        1_800_000_000.0,
        1_800_000_100.0,
        None,
    )
    preferences = _preference_projection(mailbox, (preference,), woke=(root_key,))
    triage = LocalTriageState((LocalAcknowledgement(request_key, 1_800_000_001.0),))

    documents = build_agent_browser_documents(
        _state((root,), (request,)),
        mailbox,
        preferences,
        triage,
    )

    assert len(documents) == 1
    document = documents[0]
    assert document.safe_family_label == root.safe_label
    assert document.actionable
    assert document.request_phase is RequestPhase.LIVE_UNACKNOWLEDGED
    assert document.pinned and document.snoozed and document.woke
    assert document.acknowledged and document.timing_uncertain
    assert document.source_freshness is SourceFreshness.STALE
    for query in ("nightingale", "alice", "secret", "9f8e7d", "request"):
        assert (
            project_agent_browser(
                documents,
                AgentBrowserQuery(query),
                generation=7,
                selected_work_key=None,
            ).rows
            == ()
        )
    for query in ("codex", "needs you", "pinned", "snoozed", "woke", "acknowledged", "stale"):
        assert (
            project_agent_browser(
                documents,
                AgentBrowserQuery(query),
                generation=7,
                selected_work_key=None,
            ).rows
            == documents
        )


def test_query_and_projection_have_no_recent_or_persistent_search_state() -> None:
    """Adding history fields would turn an ephemeral query into retained sensitive state."""
    assert {field.name for field in fields(AgentBrowserQuery)} == {"text", "shelf", "family_key"}
    assert {field.name for field in fields(AgentBrowserProjection)} == {
        "generation",
        "rows",
        "total_count",
        "scoped_count",
        # A bounded count of working families, not a query or a history.
        "active_count",
        "selected_work_key",
    }
    document = _document("work:history")
    first = project_agent_browser(
        (document,),
        AgentBrowserQuery("codex"),
        generation=1,
        selected_work_key=document.work_key,
    )
    second = project_agent_browser(
        (document,),
        AgentBrowserQuery("no match"),
        generation=2,
        selected_work_key=None,
    )
    assert first.rows == (document,)
    assert second.rows == ()
    assert not hasattr(second, "recent_queries")


@pytest.fixture(scope="module")
def bounded_browser_fixture() -> tuple[
    tuple[AgentBrowserDocument, ...],
    tuple[CanonicalWorkTruth, ...],
]:
    root_keys = tuple(_work_key(f"family:{index:03d}") for index in range(100))
    roots = tuple(_work(key, rank=index + 1) for index, key in enumerate(root_keys))
    workers = tuple(
        _work(
            _work_key(f"worker:{index:03d}"),
            rank=100 + index,
            parent_key=root_keys[0],
        )
        for index in range(900)
    )
    mailbox = _mailbox(roots)
    documents = build_agent_browser_documents(
        _state((*roots, *workers)),
        mailbox,
        _preference_projection(mailbox),
        LocalTriageState(()),
    )
    return documents, workers


def test_100_primary_and_900_worker_fixture_is_exactly_bounded_and_reachable(
    bounded_browser_fixture: tuple[
        tuple[AgentBrowserDocument, ...],
        tuple[CanonicalWorkTruth, ...],
    ],
) -> None:
    """Eager workers or lossy overflow would break the exact 100/900 navigation contract."""
    documents, workers = bounded_browser_fixture
    worker_keys = {worker.key for worker in workers}

    assert len(documents) == 100
    assert worker_keys.isdisjoint(document.work_key for document in documents)
    assert documents[0].worker_count == 900

    root = project_agent_browser(
        documents,
        AgentBrowserQuery(""),
        generation=7,
        selected_work_key=None,
    )
    assert len(root.rows) == root.scoped_count == root.total_count == 100

    shelf = project_agent_browser(
        documents,
        AgentBrowserQuery("", shelf=MailboxSectionKind.IN_PROGRESS),
        generation=7,
        selected_work_key=None,
        max_results=12,
    )
    assert len(shelf.rows) == 12
    assert shelf.scoped_count == 100
    assert shelf.scoped_count - len(shelf.rows) == 88

    last_worker_key = workers[-1].key
    family = project_agent_browser(
        documents,
        AgentBrowserQuery("", family_key=documents[0].work_key),
        generation=7,
        selected_work_key=last_worker_key,
    )
    assert len(family.rows) == 100
    assert family.scoped_count == 900
    assert family.total_count == 100
    assert family.selected_work_key == last_worker_key
    assert all(row.work_key in worker_keys for row in family.rows)
    assert all(row.worker_count == 0 for row in family.rows)

    roots_from_forward = tuple(row.work_key for row in root.rows)
    permuted = project_agent_browser(
        tuple(reversed(documents)),
        AgentBrowserQuery(""),
        generation=7,
        selected_work_key=None,
    )
    assert tuple(row.work_key for row in permuted.rows) == roots_from_forward
    assert {row.work_key for row in root.rows} == {document.work_key for document in documents}


def test_selected_family_retires_stale_worker_without_mutating_primary_watch() -> None:
    """A stale worker must leave display scope without rewriting primary truth or preference."""
    parent_key = _work_key("family:retirement")
    worker_key = _work_key("worker:terminal")
    parent = _work(
        parent_key,
        rank=1,
        lifecycle=WorkLifecycle.COMPLETED,
    )
    terminal_worker = _work(
        worker_key,
        rank=2,
        parent_key=parent_key,
        lifecycle=WorkLifecycle.FAILED,
    )
    preference = MailboxPreference(
        parent_key,
        MailboxPreferenceMode.WATCHED,
    )

    fresh_mailbox = project_canonical_mailbox(
        _state((parent, terminal_worker), generation=10)
    )
    fresh_preferences = _preference_projection(fresh_mailbox, (preference,))
    fresh_documents = build_agent_browser_documents(
        _state((parent, terminal_worker), generation=10),
        fresh_mailbox,
        fresh_preferences,
        LocalTriageState(()),
    )
    fresh_family = project_agent_browser(
        fresh_documents,
        AgentBrowserQuery("", family_key=parent_key),
        generation=10,
        selected_work_key=worker_key,
    )

    stale_worker = replace(
        terminal_worker,
        source_freshness=SourceFreshness.STALE,
    )
    stale_state = _state((parent, stale_worker), generation=11)
    stale_mailbox = project_canonical_mailbox(
        stale_state,
        previous_order=dict(fresh_mailbox.retained_order),
    )
    stale_preferences = _preference_projection(stale_mailbox, (preference,))
    stale_documents = build_agent_browser_documents(
        stale_state,
        stale_mailbox,
        stale_preferences,
        LocalTriageState(()),
    )
    stale_family = project_agent_browser(
        stale_documents,
        AgentBrowserQuery("", family_key=parent_key),
        generation=11,
        selected_work_key=worker_key,
    )

    assert fresh_documents[0].lifecycle_label == "completed"
    assert fresh_documents[0].watched is True
    assert fresh_documents[0].worker_count == 1
    assert tuple(row.work_key for row in fresh_family.rows) == (worker_key,)
    assert fresh_family.selected_work_key == worker_key
    assert stale_documents[0].lifecycle_label == "completed"
    assert stale_documents[0].watched is True
    assert stale_documents[0].worker_count == 0
    assert stale_family.rows == ()
    assert stale_family.selected_work_key is None


def test_browser_does_not_promote_worker_whose_exact_parent_is_missing() -> None:
    """An unresolved parent link must not become a fabricated primary family."""
    orphan = _work(
        _work_key("worker:orphan"),
        rank=1,
        parent_key=_work_key("family:missing"),
    )
    state = _state((orphan,), generation=12)
    mailbox = project_canonical_mailbox(state)

    documents = build_agent_browser_documents(
        state,
        mailbox,
        _preference_projection(mailbox),
        LocalTriageState(()),
    )

    assert documents == ()


def test_browser_parent_swap_moves_worker_to_only_the_current_exact_family() -> None:
    """A cached parent assignment would expose one worker under two families."""
    first_key = _work_key("family:parent-a")
    second_key = _work_key("family:parent-b")
    worker_key = _work_key("worker:moving")
    first = _work(first_key, rank=1)
    second = _work(second_key, rank=2)
    under_first = _work(worker_key, rank=3, parent_key=first_key)

    initial_state = _state((first, second, under_first), generation=13)
    initial_mailbox = project_canonical_mailbox(initial_state)
    moved_state = _state(
        (first, second, replace(under_first, parent_key=second_key)),
        generation=14,
    )
    moved_mailbox = project_canonical_mailbox(
        moved_state,
        previous_order=dict(initial_mailbox.retained_order),
    )
    moved_documents = build_agent_browser_documents(
        moved_state,
        moved_mailbox,
        _preference_projection(moved_mailbox),
        LocalTriageState(()),
    )
    counts = {row.work_key: row.worker_count for row in moved_documents}

    assert counts == {first_key: 0, second_key: 1}
    assert project_agent_browser(
        moved_documents,
        AgentBrowserQuery("", family_key=first_key),
        generation=14,
        selected_work_key=worker_key,
    ).rows == ()
    assert tuple(
        row.work_key
        for row in project_agent_browser(
            moved_documents,
            AgentBrowserQuery("", family_key=second_key),
            generation=14,
            selected_work_key=worker_key,
        ).rows
    ) == (worker_key,)


def test_primary_overflow_keeps_exact_count_and_every_retained_family_reachable() -> None:
    """Discarding the 101st seed would make exact overflow and key-anchored navigation lie."""
    roots = tuple(_work(_work_key(f"overflow:{index:03d}"), rank=index + 1) for index in range(101))
    mailbox = _mailbox(roots)
    documents = build_agent_browser_documents(
        _state(roots),
        mailbox,
        _preference_projection(mailbox),
        LocalTriageState(()),
    )

    assert len(documents) == 100
    default_root = project_agent_browser(
        documents,
        AgentBrowserQuery(""),
        generation=7,
        selected_work_key=None,
    )
    assert len(default_root.rows) == 100
    assert default_root.total_count == 101
    assert default_root.scoped_count == 101
    assert default_root.scoped_count - len(default_root.rows) == 1

    last_key = roots[-1].key
    selected_root = project_agent_browser(
        documents,
        AgentBrowserQuery("", shelf=MailboxSectionKind.IN_PROGRESS),
        generation=7,
        selected_work_key=last_key,
    )
    assert len(selected_root.rows) == 100
    assert selected_root.total_count == 101
    assert selected_root.scoped_count == 101
    assert selected_root.selected_work_key == last_key
    assert selected_root.rows[-1].work_key == last_key

    reachable = set(default_root.rows)
    reachable.update(selected_root.rows)
    assert {row.work_key for row in reachable} == {root.key for root in roots}


def test_collection_owns_one_private_catalog_and_rows_serialize_only_public_fields() -> None:
    """Embedding catalog metadata on a row would leak hidden labels through generic serialization."""
    roots = tuple(
        _work(
            _work_key(f"catalog:{index:03d}"),
            rank=index + 1,
            safe_label=f"SAFE-family:{index:03d}",
        )
        for index in range(105)
    )
    mailbox = _mailbox(roots)
    documents = build_agent_browser_documents(
        _state(roots),
        mailbox,
        _preference_projection(mailbox),
        LocalTriageState(()),
    )

    assert len(documents) == 100
    assert len(documents._primary_catalog) == 105  # type: ignore[attr-defined]
    assert all(not hasattr(row, "_overflow_seeds") for row in documents)
    serialized = asdict(documents[0])
    assert set(serialized) == {
        "work_key",
        "provider_label",
        "safe_family_label",
        "search_labels",
        "lifecycle_label",
        "actionable",
        "request_phase",
        "source_freshness",
        "worker_count",
        "pinned",
        "watched",
        "snoozed",
        "woke",
        "acknowledged",
        "timing_uncertain",
    }
    assert "SAFE-family:100" not in repr(serialized)


def test_one_thousand_retained_families_remain_counted_and_key_reachable() -> None:
    """A catalog cap below canonical state capacity would silently orphan retained families."""
    roots = tuple(_work(_work_key(f"thousand:{index:03d}"), rank=index + 1) for index in range(1000))
    mailbox = _mailbox(roots)
    documents = build_agent_browser_documents(
        _state(roots),
        mailbox,
        _preference_projection(mailbox),
        LocalTriageState(()),
    )

    default_root = project_agent_browser(
        documents,
        AgentBrowserQuery(""),
        generation=7,
        selected_work_key=None,
    )
    assert len(documents) == len(default_root.rows) == 100
    assert default_root.total_count == default_root.scoped_count == 1000

    sampled_keys = (roots[0].key, roots[499].key, roots[-1].key)
    for work_key in sampled_keys:
        anchored = project_agent_browser(
            documents,
            AgentBrowserQuery(""),
            generation=7,
            selected_work_key=work_key,
        )
        assert anchored.selected_work_key == work_key
        assert work_key in {row.work_key for row in anchored.rows}
        assert len(anchored.rows) == 100
        assert anchored.total_count == anchored.scoped_count == 1000


def test_projection_and_warm_query_p95_meet_pure_model_targets(
    bounded_browser_fixture: tuple[
        tuple[AgentBrowserDocument, ...],
        tuple[CanonicalWorkTruth, ...],
    ],
) -> None:
    """An accidentally unbounded matcher or eager global worker build would miss the pure targets."""
    documents, _workers = bounded_browser_fixture
    family_query = AgentBrowserQuery("", family_key=documents[0].work_key)
    query = AgentBrowserQuery("cod")

    project_agent_browser(
        documents,
        family_query,
        generation=7,
        selected_work_key=None,
    )
    project_agent_browser(documents, query, generation=7, selected_work_key=None)

    projection_samples = []
    query_samples = []
    for _ in range(40):
        started = perf_counter()
        project_agent_browser(
            documents,
            family_query,
            generation=7,
            selected_work_key=None,
        )
        projection_samples.append(perf_counter() - started)

        started = perf_counter()
        project_agent_browser(documents, query, generation=7, selected_work_key=None)
        query_samples.append(perf_counter() - started)

    projection_p95 = quantiles(projection_samples, n=20)[18]
    query_p95 = quantiles(query_samples, n=20)[18]
    assert projection_p95 < 0.020
    assert query_p95 < 0.008


def test_hour_dead_active_work_demotes_to_stale_recent() -> None:
    """An "active" work whose newest event is over an hour old is a dead
    session the provider never closed -- it must not present as live
    ("Grok · active" with no grok process anywhere) nor count toward the
    header's active total."""
    root_key = _work_key("dead-active")
    root = _work(root_key, rank=1, lifecycle=WorkLifecycle.ACTIVE)
    state = _state((root,))
    mailbox = _mailbox((root,))
    preferences = _preference_projection(mailbox)
    base_epoch = root.watermark.occurred_at_epoch

    fresh_documents = build_agent_browser_documents(
        state,
        mailbox,
        preferences,
        LocalTriageState(()),
        now_epoch=base_epoch + 60.0,
    )
    aged_documents = build_agent_browser_documents(
        state,
        mailbox,
        preferences,
        LocalTriageState(()),
        now_epoch=base_epoch + 2 * 3_600.0,
    )

    assert fresh_documents[0].lifecycle_label == "active"
    assert aged_documents[0].lifecycle_label == "stale"

    fresh_projection = project_agent_browser(
        fresh_documents,
        AgentBrowserQuery(""),
        generation=7,
        selected_work_key=None,
    )
    aged_projection = project_agent_browser(
        aged_documents,
        AgentBrowserQuery(""),
        generation=7,
        selected_work_key=None,
    )
    assert fresh_projection.active_count == 1
    assert aged_projection.active_count == 0
