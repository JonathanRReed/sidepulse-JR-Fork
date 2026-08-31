from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from sidepulse.agent_browser import (
    AgentBrowserDocument,
    ApprovedSearchLabel,
    SearchLabelSource,
)
from sidepulse.capacity_types import SourceKey
from sidepulse.mailbox import MailboxRow
from sidepulse.navigation_policy import (
    OperatorActionDescriptor,
    OperatorActionKind,
)
from sidepulse.operator_accessibility import (
    MAX_ACCESSIBILITY_HELP_LENGTH,
    MAX_ACCESSIBILITY_LABEL_LENGTH,
    MAX_ACCESSIBILITY_VALUE_LENGTH,
    AccessibilityAnnouncement,
    AccessibilityText,
    AnnouncementPriority,
    FocusSnapshot,
    action_accessibility,
    announcement_for_transition,
    browser_row_accessibility,
    mailbox_row_accessibility,
    normalize_semantic_text_scale,
    status_item_accessibility,
)
from sidepulse.operator_state import (
    AcknowledgementEligibility,
    CanonicalOperatorEvent,
    CanonicalRequestTruth,
    CanonicalWorkTruth,
    InterruptionClass,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
    empty_operator_state,
)
from sidepulse.presentation_policy import (
    FiniteCue,
    FiniteCueState,
    GlanceOverrideReason,
    GlanceSemantic,
    ResolvedGlance,
    SemanticGlyph,
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

_GLYPH_BY_SEMANTIC = {
    GlanceSemantic.ATTENTION: SemanticGlyph.FULL_ANCHOR,
    GlanceSemantic.FRESH_FAILURE: SemanticGlyph.LEFT_ANCHOR,
    GlanceSemantic.FRESH_COMPLETION: SemanticGlyph.RIGHT_ANCHOR,
    GlanceSemantic.ACTIVE: SemanticGlyph.CENTER_PAIR,
    GlanceSemantic.UNRESOLVED_FAILURE: SemanticGlyph.LEFT_ANCHOR,
    GlanceSemantic.CAPACITY: SemanticGlyph.CAPACITY_FILL,
    GlanceSemantic.REST: SemanticGlyph.REST,
}


def _source(*, provider: str = "codex", instance: str = "local.01") -> SourceKey:
    return SourceKey(provider, "local", instance, "sessions")


def _watermark(source: SourceKey, *, rank: int = 1) -> ProviderWatermark:
    return ProviderWatermark(
        source_key=source,
        basis=WatermarkBasis.OCCURRED_AT_TIE_BREAK,
        occurred_at_epoch=1_800_000_000.0 + rank,
        event_token=EventToken(f"event:{rank}"),
        sequence=None,
        tie_break_rank=rank,
    )


def _operator_state(
    *,
    lifecycle: WorkLifecycle = WorkLifecycle.IDLE,
    freshness: SourceFreshness = SourceFreshness.FRESH,
    request_phase: RequestPhase | None = None,
    safe_label: str = "Codex work:01",
    timing_uncertain: bool = False,
):
    source = _source()
    work_key = WorkKey(source, WorkIdentifier("work:01"))
    watermark = _watermark(source)
    requests = ()
    request_keys = ()
    if request_phase is not None:
        request_key = RequestKey(work_key, RequestIdentifier("request:01"))
        eligibility = {
            RequestPhase.LIVE_UNACKNOWLEDGED: AcknowledgementEligibility.ELIGIBLE,
            RequestPhase.LIVE_ACKNOWLEDGED: AcknowledgementEligibility.ALREADY_ACKNOWLEDGED,
            RequestPhase.STALE_HOLD: AcknowledgementEligibility.STALE_HOLD,
            RequestPhase.RESOLVED: AcknowledgementEligibility.RESOLVED,
            RequestPhase.UNKNOWN_EXPIRED: AcknowledgementEligibility.RESOLVED,
        }[request_phase]
        semantic_key = SemanticEventKey(
            request_key,
            TransitionKind.REQUEST_OPENED,
            watermark,
        )
        requests = (
            CanonicalRequestTruth(
                key=request_key,
                phase=request_phase,
                request_kind=RequestKind.INPUT,
                next_actor=NextActor.USER,
                watermark=watermark,
                source_freshness=freshness,
                acknowledgement_eligibility=eligibility,
                semantic_event_key=semantic_key,
                opened_at_epoch=1_800_000_000.0,
                eligible_elapsed_seconds=5.0,
            ),
        )
        request_keys = (request_key,)
    work = CanonicalWorkTruth(
        key=work_key,
        lifecycle=lifecycle,
        watermark=watermark,
        observation_authority=ObservationAuthority.AUTHORITATIVE_PROVIDER,
        source_health=(SourceHealth.HEALTHY if freshness is SourceFreshness.FRESH else SourceHealth.UNAVAILABLE),
        source_freshness=freshness,
        next_actor=(NextActor.USER if request_phase is not None else NextActor.PROVIDER),
        safe_label=safe_label,
        parent_key=None,
        request_keys=request_keys,
        timing_uncertain=timing_uncertain,
    )
    state = replace(
        empty_operator_state(),
        generation=1,
        works=(work,),
        requests=requests,
    )
    return state, work, (requests[0] if requests else None)


def _glance(
    semantic: GlanceSemantic,
    *,
    override: GlanceOverrideReason = GlanceOverrideReason.NONE,
    cue: FiniteCue | None = None,
) -> ResolvedGlance:
    return ResolvedGlance(
        semantic=semantic,
        glyph=_GLYPH_BY_SEMANTIC[semantic],
        cue=cue,
        override_reason=override,
        relay_epoch=10.0,
        next_visual_change_at=None,
    )


def _mailbox_row(
    work: CanonicalWorkTruth,
    request: CanonicalRequestTruth | None = None,
    *,
    worker_count: int = 0,
    safe_label: str | None = None,
) -> MailboxRow:
    return MailboxRow(
        work_key=work.key,
        request_key=None if request is None else request.key,
        safe_label=work.safe_label if safe_label is None else safe_label,
        lifecycle=work.lifecycle,
        next_actor=work.next_actor,
        source_freshness=work.source_freshness,
        request_keys=work.request_keys,
        actionable=(
            request is not None
            and request.phase
            in {
                RequestPhase.LIVE_UNACKNOWLEDGED,
                RequestPhase.LIVE_ACKNOWLEDGED,
                RequestPhase.STALE_HOLD,
            }
        ),
        worker_count=worker_count,
        updated_at_epoch=1_800_000_001.0,
        stable_order=0,
        timing_uncertain=work.timing_uncertain,
    )


def _browser_row(
    work: CanonicalWorkTruth,
    request: CanonicalRequestTruth | None = None,
    *,
    safe_label: str | None = None,
    worker_count: int = 0,
    pinned: bool = False,
    watched: bool = False,
    snoozed: bool = False,
    woke: bool = False,
    acknowledged: bool = False,
) -> AgentBrowserDocument:
    provider = ApprovedSearchLabel("Codex", SearchLabelSource.PROVIDER)
    return AgentBrowserDocument(
        work_key=work.key,
        provider_label=provider,
        safe_family_label=work.safe_label if safe_label is None else safe_label,
        search_labels=(provider,),
        lifecycle_label=("needs you" if request is not None else work.lifecycle.value),
        actionable=request is not None,
        request_phase=None if request is None else request.phase,
        source_freshness=work.source_freshness,
        worker_count=worker_count,
        pinned=pinned,
        watched=watched,
        snoozed=snoozed,
        woke=woke,
        acknowledged=acknowledged,
        timing_uncertain=work.timing_uncertain,
    )


def _event(
    kind: TransitionKind,
    *,
    interruption: InterruptionClass,
    rank: int = 1,
    freshness: SourceFreshness = SourceFreshness.FRESH,
    private_identity: bool = False,
) -> CanonicalOperatorEvent:
    source = _source(instance="prompt:delete-files" if private_identity else "local.01")
    work_key = WorkKey(
        source,
        WorkIdentifier("raw-error:permission-denied" if private_identity else "work:01"),
    )
    watermark = _watermark(source, rank=rank)
    subject_key: WorkKey | RequestKey = work_key
    if kind in {TransitionKind.REQUEST_OPENED, TransitionKind.REQUEST_RESOLVED}:
        subject_key = RequestKey(work_key, RequestIdentifier(f"request:{rank}"))
    semantic_key = SemanticEventKey(subject_key, kind, watermark)
    return CanonicalOperatorEvent(
        key=semantic_key,
        subject_key=subject_key,
        kind=kind,
        interruption_class=interruption,
        occurred_at_epoch=watermark.occurred_at_epoch,
        source_freshness=freshness,
    )


def _all_text(text: AccessibilityText) -> str:
    return " ".join((text.label, text.value, text.help))


def test_accessibility_records_are_frozen_and_reject_blank_or_unbounded_text() -> None:
    text = AccessibilityText("JR Bar", "No agents need attention", "Open JR Bar status")
    focus = FocusSnapshot("agent-browser", "search", None, (3, 2), "agents")
    announcement = AccessibilityAnnouncement(
        "announcement:abc",
        "An agent completed",
        AnnouncementPriority.SUCCESS,
    )

    with pytest.raises(FrozenInstanceError):
        text.value = ""  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        focus.control_key = None  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        announcement.text = ""  # type: ignore[misc]
    with pytest.raises(ValueError):
        AccessibilityText("", "value", "help")
    with pytest.raises(ValueError):
        AccessibilityText("x" * (MAX_ACCESSIBILITY_LABEL_LENGTH + 1), "value", "help")
    with pytest.raises(ValueError):
        AccessibilityAnnouncement(
            "announcement:private",
            "Bearer secret-token",
            AnnouncementPriority.ERROR,
        )


@pytest.mark.parametrize(
    ("choice", "expected"),
    ((100, 1.0), (125, 1.25), (150, 1.5), (175, 1.75), (200, 2.0)),
)
def test_semantic_text_scale_accepts_only_the_five_exact_percentage_choices(
    choice: object,
    expected: float,
) -> None:
    assert normalize_semantic_text_scale(choice) == expected


@pytest.mark.parametrize(
    "invalid",
    (None, True, False, 0, 99, 201, 125.0, 1.25, "125", float("nan"), object()),
)
def test_invalid_semantic_text_scale_normalizes_to_one_hundred_percent(
    invalid: object,
) -> None:
    assert normalize_semantic_text_scale(invalid) == 1.0


@pytest.mark.parametrize(
    ("semantic", "expected_phrase"),
    (
        (GlanceSemantic.ATTENTION, "Needs your attention"),
        (GlanceSemantic.FRESH_FAILURE, "New failure"),
        (GlanceSemantic.FRESH_COMPLETION, "Agent completed"),
        (GlanceSemantic.ACTIVE, "Agents active"),
        (GlanceSemantic.UNRESOLVED_FAILURE, "Failure needs review"),
        (GlanceSemantic.CAPACITY, "Capacity status available"),
        (GlanceSemantic.REST, "No agents need attention"),
    ),
)
def test_status_item_has_stable_role_text_and_nonblank_value_for_every_glance(
    semantic: GlanceSemantic,
    expected_phrase: str,
) -> None:
    state, _, _ = _operator_state()

    result = status_item_accessibility(state, _glance(semantic))

    assert result.label == "JR Bar"
    assert result.help == "Open JR Bar status"
    assert expected_phrase in result.value
    assert result.value.strip()
    assert len(result.value) <= MAX_ACCESSIBILITY_VALUE_LENGTH


def test_status_item_preserves_stale_acknowledged_quiet_and_finite_cue_truth() -> None:
    state, _, _ = _operator_state(
        lifecycle=WorkLifecycle.WAITING,
        freshness=SourceFreshness.STALE,
        request_phase=RequestPhase.LIVE_ACKNOWLEDGED,
    )
    active = FiniteCue("attention:1", GlanceSemantic.ATTENTION, 1, 1.0)
    pending = FiniteCue("completion:1", GlanceSemantic.FRESH_COMPLETION, 1, 1.0)
    finite = FiniteCueState(active, pending, 11.0, True)

    result = status_item_accessibility(
        state,
        _glance(
            GlanceSemantic.ATTENTION,
            override=GlanceOverrideReason.SHARED_SPACE_PRIVACY,
            cue=active,
        ),
        finite_cues=finite,
    )

    assert result.label == "JR Bar"
    assert result.help == "Open JR Bar status"
    assert "Acknowledged locally" in result.value
    assert "Source stale" in result.value
    assert "Quiet presentation" in result.value
    assert "Brief status cue" in result.value
    assert "Additional updates waiting" in result.value
    assert result.value.strip()


@pytest.mark.parametrize(
    ("lifecycle", "expected"),
    (
        (WorkLifecycle.IDLE, "Idle"),
        (WorkLifecycle.ACTIVE, "Active"),
        (WorkLifecycle.WAITING, "Waiting"),
        (WorkLifecycle.COMPLETED, "Completed"),
        (WorkLifecycle.FAILED, "Failed"),
        (WorkLifecycle.UNKNOWN, "Unknown"),
    ),
)
def test_mailbox_row_names_every_lifecycle_without_color(
    lifecycle: WorkLifecycle,
    expected: str,
) -> None:
    state, work, _ = _operator_state(lifecycle=lifecycle)
    del state

    result = mailbox_row_accessibility(_mailbox_row(work, worker_count=2))

    assert result.label == "Codex work:01"
    assert expected in result.value
    assert "Source fresh" in result.value
    assert "2 workers" in result.value
    assert "Open actions" in result.help
    assert "red" not in _all_text(result).casefold()
    assert "green" not in _all_text(result).casefold()


@pytest.mark.parametrize(
    ("freshness", "expected"),
    (
        (SourceFreshness.FRESH, "Source fresh"),
        (SourceFreshness.STALE, "Source stale"),
        (SourceFreshness.TIMING_UNCERTAIN, "Source timing uncertain"),
        (SourceFreshness.PARTIAL, "Source partially available"),
        (SourceFreshness.UNAVAILABLE, "Source unavailable"),
        (SourceFreshness.RESTORED, "Source restored"),
    ),
)
def test_mailbox_row_names_every_source_freshness_state(
    freshness: SourceFreshness,
    expected: str,
) -> None:
    _, work, _ = _operator_state(freshness=freshness)

    result = mailbox_row_accessibility(_mailbox_row(work))

    assert expected in result.value


def test_mailbox_local_states_augment_instead_of_replace_lifecycle_truth() -> None:
    _, work, request = _operator_state(
        lifecycle=WorkLifecycle.WAITING,
        request_phase=RequestPhase.STALE_HOLD,
        freshness=SourceFreshness.STALE,
    )

    result = mailbox_row_accessibility(
        _mailbox_row(work, request, worker_count=1),
        pinned=True,
        watched=True,
        snoozed_until=0.0,
        woke=True,
        acknowledged_locally=True,
    )

    assert result.value == (
        "Waiting, Needs you, Pinned, Watching, Snoozed until 1970-01-01 00:00 UTC, "
        "Woke, Acknowledged locally, Source stale, 1 worker"
    )


@pytest.mark.parametrize(
    "unsafe_label",
    (
        "",
        "/Users/alice/private/project",
        "Codex person@example.com",
        "Codex https://example.com/private",
        "Codex prompt:delete-files",
        "Codex Bearer secret-token",
        "Codex Traceback: PermissionError",
        "Codex session_01HZX5BX8J8DG6YF7JMV2J0E2G",
        "Codex 123e4567-e89b-12d3-a456-426614174000",
    ),
)
def test_mailbox_row_replaces_missing_or_private_shaped_family_copy(
    unsafe_label: str,
) -> None:
    _, work, _ = _operator_state()

    result = mailbox_row_accessibility(_mailbox_row(work, safe_label=unsafe_label))

    assert result.label == "Codex agent family"
    if unsafe_label:
        assert unsafe_label not in _all_text(result)
    assert len(result.label) <= MAX_ACCESSIBILITY_LABEL_LENGTH
    assert len(result.help) <= MAX_ACCESSIBILITY_HELP_LENGTH


def test_browser_row_exposes_provider_family_lifecycle_triage_freshness_and_workers() -> None:
    _, work, request = _operator_state(
        lifecycle=WorkLifecycle.ACTIVE,
        request_phase=RequestPhase.LIVE_ACKNOWLEDGED,
        timing_uncertain=True,
    )
    row = _browser_row(
        work,
        request,
        worker_count=3,
        pinned=True,
        watched=True,
        snoozed=True,
        woke=True,
        acknowledged=True,
    )

    result = browser_row_accessibility(
        row,
        lifecycle=work.lifecycle,
        snoozed_until=0.0,
    )

    assert result.label == "Codex work:01"
    assert result.value == (
        "Active, Needs you, Pinned, Watching, Snoozed until 1970-01-01 00:00 UTC, "
        "Woke, Acknowledged locally, Source timing uncertain, 3 workers"
    )
    assert "Open actions" in result.help


def test_actionable_browser_row_requires_explicit_canonical_lifecycle() -> None:
    _, work, request = _operator_state(
        lifecycle=WorkLifecycle.ACTIVE,
        request_phase=RequestPhase.LIVE_UNACKNOWLEDGED,
    )
    row = _browser_row(work, request)

    with pytest.raises(TypeError):
        browser_row_accessibility(row)  # type: ignore[call-arg]


def test_browser_row_keeps_full_semantics_when_navigation_is_disabled() -> None:
    _, work, _ = _operator_state(lifecycle=WorkLifecycle.COMPLETED)

    result = browser_row_accessibility(
        _browser_row(work, safe_label=""),
        lifecycle=work.lifecycle,
        disabled_reason="Source is stale",
    )

    assert result.label == "Codex agent family"
    assert "Completed" in result.value
    assert "Source fresh" in result.value
    assert result.help == "Open unavailable. Source is stale"


def test_action_accessibility_exposes_enabled_state_reason_and_key_equivalent() -> None:
    enabled = OperatorActionDescriptor(
        OperatorActionKind.OPEN,
        "Open",
        True,
        None,
        "o",
    )
    disabled = OperatorActionDescriptor(
        OperatorActionKind.OPEN,
        "Open",
        False,
        "Source is stale",
        "",
    )

    assert action_accessibility(enabled) == AccessibilityText(
        "Open",
        "Available, keyboard shortcut O",
        "Activate Open",
    )
    assert action_accessibility(disabled) == AccessibilityText(
        "Open",
        "Unavailable",
        "Source is stale",
    )


@pytest.mark.parametrize(
    ("kind", "interruption", "text", "priority"),
    (
        (
            TransitionKind.REQUEST_OPENED,
            InterruptionClass.ACTION_REQUIRED,
            "An agent needs your attention",
            AnnouncementPriority.ACTIONABLE,
        ),
        (
            TransitionKind.FAILED,
            InterruptionClass.IMPORTANT_OUTCOME,
            "An agent failed",
            AnnouncementPriority.ERROR,
        ),
        (
            TransitionKind.REQUEST_RESOLVED,
            InterruptionClass.IMPORTANT_OUTCOME,
            "An agent request was resolved",
            AnnouncementPriority.OUTCOME,
        ),
        (
            TransitionKind.COMPLETED,
            InterruptionClass.COURTESY,
            "An agent completed",
            AnnouncementPriority.SUCCESS,
        ),
    ),
)
def test_fresh_actionable_and_terminal_edges_announce_once(
    kind: TransitionKind,
    interruption: InterruptionClass,
    text: str,
    priority: AnnouncementPriority,
) -> None:
    event = _event(kind, interruption=interruption)

    announcement = announcement_for_transition(event)

    assert announcement is not None
    assert announcement.text == text
    assert announcement.priority is priority
    assert announcement.key.startswith("announcement:")
    assert len(announcement.key) <= 128
    assert (
        announcement_for_transition(
            event,
            announced_event_keys=frozenset({event.key}),
        )
        is None
    )


@pytest.mark.parametrize(
    "kind",
    (
        TransitionKind.BECAME_ACTIVE,
        TransitionKind.BECAME_IDLE,
        TransitionKind.SOURCE_DEGRADED,
        TransitionKind.SOURCE_RECOVERED,
    ),
)
def test_poll_and_ambient_edges_do_not_announce(kind: TransitionKind) -> None:
    event = _event(kind, interruption=InterruptionClass.AMBIENT)
    assert announcement_for_transition(event) is None


def test_stale_quiet_and_locally_acknowledged_transitions_do_not_announce() -> None:
    stale = _event(
        TransitionKind.FAILED,
        interruption=InterruptionClass.IMPORTANT_OUTCOME,
        freshness=SourceFreshness.STALE,
    )
    actionable = _event(
        TransitionKind.REQUEST_OPENED,
        interruption=InterruptionClass.ACTION_REQUIRED,
    )

    assert announcement_for_transition(stale) is None
    assert announcement_for_transition(actionable, quiet=True) is None
    assert announcement_for_transition(actionable, acknowledged_locally=True) is None


def test_announcement_key_is_exact_but_does_not_echo_private_identity() -> None:
    first = _event(
        TransitionKind.COMPLETED,
        interruption=InterruptionClass.COURTESY,
        rank=1,
        private_identity=True,
    )
    second = _event(
        TransitionKind.COMPLETED,
        interruption=InterruptionClass.COURTESY,
        rank=2,
        private_identity=True,
    )

    first_announcement = announcement_for_transition(first)
    second_announcement = announcement_for_transition(second)

    assert first_announcement is not None
    assert second_announcement is not None
    assert first_announcement.key != second_announcement.key
    rendered = " ".join(
        (
            first_announcement.key,
            first_announcement.text,
            second_announcement.key,
            second_announcement.text,
        )
    ).casefold()
    for forbidden in ("prompt", "delete-files", "raw-error", "permission-denied"):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    "invalid_selection",
    ((-1, 0), (0, -1), (True, 0), (0, 1.5), (0,), [0, 0]),
)
def test_focus_snapshot_rejects_invalid_text_selection(
    invalid_selection: object,
) -> None:
    with pytest.raises(ValueError):
        FocusSnapshot(
            "agent-browser",
            "search",
            None,
            invalid_selection,  # type: ignore[arg-type]
            "agents",
        )


def test_all_public_helpers_return_nonempty_bounded_privacy_safe_text() -> None:
    _, work, request = _operator_state(
        lifecycle=WorkLifecycle.ACTIVE,
        request_phase=RequestPhase.LIVE_UNACKNOWLEDGED,
    )
    unsafe = "Project Nightingale /Users/alice sk-live-secret prompt:delete-files"
    texts = (
        status_item_accessibility(empty_operator_state(), _glance(GlanceSemantic.REST)),
        mailbox_row_accessibility(_mailbox_row(work, request, safe_label=unsafe)),
        browser_row_accessibility(
            _browser_row(work, request, safe_label=unsafe),
            lifecycle=work.lifecycle,
        ),
        action_accessibility(
            OperatorActionDescriptor(
                OperatorActionKind.OPEN,
                "Open",
                False,
                "Not available",
                "",
            )
        ),
    )

    for semantic_text in texts:
        assert semantic_text.label.strip()
        assert semantic_text.value.strip()
        assert semantic_text.help.strip()
        assert len(semantic_text.label) <= MAX_ACCESSIBILITY_LABEL_LENGTH
        assert len(semantic_text.value) <= MAX_ACCESSIBILITY_VALUE_LENGTH
        assert len(semantic_text.help) <= MAX_ACCESSIBILITY_HELP_LENGTH
        rendered = _all_text(semantic_text).casefold()
        for forbidden in (
            "nightingale",
            "/users/",
            "sk-live-secret",
            "prompt:delete-files",
        ):
            assert forbidden not in rendered
