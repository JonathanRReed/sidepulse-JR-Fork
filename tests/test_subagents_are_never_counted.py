"""No count, row, light or interrupt ever includes a sub-agent.

The owner's locked rule, and the defect that broke it on three surfaces
at once. A live snapshot carried 114 works: 27 main agents and 87 Task
workers, of which exactly 3 mains were ACTIVE. The menu bar read
"Agents active, Active: 34", the dropdown read "Agent Mailbox · 24
active · 0 need you", and the LED strip's representative row -- the one
that decides the whole light language -- was
``claude:agent:a70f42924b7bb211d``, a worker.

Two different wrong numbers, because each surface filtered (or failed
to filter) at its own call site. So the rule is enforced at the SOURCE:
``AttentionProjection.visible_rows`` is structurally main-agents-only,
and a consumer cannot opt out of that by constructing one carelessly.

Sub-agents matter in exactly one way -- they hold their parent's
completion open -- so the mailbox still sees them, through
``worker_rows``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sidepulse.agent_browser import (
    AgentBrowserQuery,
    build_agent_browser_documents,
    project_agent_browser,
)
from sidepulse.attention import (
    AttentionProjection,
    LifecycleMode,
    ProjectedAgentRow,
    project_attention,
)
from sidepulse.capacity_types import SourceKey
from sidepulse.collector import aggregate_status
from sidepulse.colors import ColorSettings, program_for_projection
from sidepulse.local_triage import LocalTriageState
from sidepulse.mailbox import project_canonical_mailbox, project_mailbox
from sidepulse.mailbox_preferences import MailboxPreferenceProjection
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.operator_accessibility import status_item_accessibility
from sidepulse.operator_state import (
    CanonicalWorkTruth,
    empty_operator_state,
)
from sidepulse.presentation_policy import (
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
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
    WorkLifecycle,
)
from sidepulse.settings import AgentMonitorSettings
from sidepulse.status_bar import StatusBarController


_NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

#: The live fan-out that produced "Active: 34". One main agent can spawn
#: 100+ Task workers; 200 have been observed on this machine.
_OBSERVED_FANOUT = 40


def _main(agent_id: str, mode: AgentMode, *, provider: str = "claude") -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=f"{provider}:session:{agent_id}",
        display_name=agent_id,
        mode=mode,
        updated_at=_NOW,
        event_name="PreToolUse",
        session_id=agent_id,
    )


def _worker(
    index: int,
    mode: AgentMode,
    *,
    parent: str = "main",
    provider: str = "claude",
) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=f"{provider}:agent:w{index}",
        display_name=f"worker {index}",
        mode=mode,
        updated_at=_NOW + timedelta(seconds=index),
        event_name="PreToolUse",
        session_id=parent,
    )


class _Snapshot:
    def __init__(self, statuses):
        self.statuses = tuple(statuses)
        self.collected_at = _NOW + timedelta(seconds=_OBSERVED_FANOUT + 1)


def _project(statuses) -> AttentionProjection:
    return project_attention(_Snapshot(statuses), AgentMonitorSettings())


# --- the source ------------------------------------------------------------


def test_one_main_agent_fanning_out_is_still_one_row() -> None:
    statuses = [_main("main", AgentMode.WORKING)] + [
        _worker(index, AgentMode.WORKING) for index in range(_OBSERVED_FANOUT)
    ]

    projection = _project(statuses)

    assert [row.agent_id for row in projection.visible_rows] == ["claude:session:main"]
    assert len(projection.worker_rows) == _OBSERVED_FANOUT
    assert all(row.is_subagent for row in projection.worker_rows)


def test_visible_rows_refuses_a_worker_however_it_is_constructed() -> None:
    """The filter is structural, not a courtesy the projectors extend.

    Every consumer reads this field. Leaving it to each of them is what
    produced 34 on one surface and 24 on another.
    """
    worker = _project(
        [_main("main", AgentMode.WORKING), _worker(0, AgentMode.WORKING)]
    ).worker_rows[0]
    main = _project([_main("main", AgentMode.WORKING)]).visible_rows[0]

    hand_built = AttentionProjection(
        lifecycle_mode=LifecycleMode.ACTIVE,
        actionable_attention=(),
        visible_rows=(main, worker),
        transient_signals=(),
        dominant_provider="claude",
        click_target_agent_id=None,
    )
    assert hand_built.visible_rows == (main,)
    assert hand_built.worker_rows == (worker,)

    # dataclasses.replace is how the device-pin path builds its copy.
    replaced = replace(hand_built, visible_rows=(main, worker))
    assert replaced.visible_rows == (main,)


def test_a_worker_never_drives_the_light() -> None:
    """The light only ever talks about a MAIN the user can see.

    The original sin this file guards against was the representative
    being an anonymous worker row -- the strip announcing "working"
    about something the user cannot see, click, or answer. That stays
    forbidden. But a quiet main whose 40 workers are busy IS working
    (2026-08-27 owner call: Claude pauses the main thread while
    sub-agents carry the work, and "completed" was a lie) -- so the
    light says working, and the row that says it is the MAIN.
    """
    statuses = [_main("main", AgentMode.IDLE_READY)] + [
        _worker(index, AgentMode.WORKING) for index in range(_OBSERVED_FANOUT)
    ]

    projection = _project(statuses)

    assert projection.lifecycle_mode is LifecycleMode.ACTIVE
    (main_row,) = projection.visible_rows
    assert not main_row.is_subagent
    assert main_row.lifecycle_mode is LifecycleMode.ACTIVE
    assert all(row.is_subagent for row in projection.worker_rows)
    assert projection.dominant_provider == "claude"


def test_a_lone_main_agent_is_not_a_crowd() -> None:
    """``should_render_multi_agent`` gates on row count.

    With workers in ``visible_rows`` it saw 114 and the multi-agent
    renderer was permanently on, which is what replaced Claude's brand
    colour with palette slots.
    """
    statuses = [_main("main", AgentMode.WORKING)] + [
        _worker(index, AgentMode.WORKING) for index in range(_OBSERVED_FANOUT)
    ]
    glance = ResolvedGlance(
        semantic=GlanceSemantic.ACTIVE,
        glyph=SemanticGlyph.CENTER_PAIR,
        cue=None,
        override_reason=GlanceOverrideReason.NONE,
        relay_epoch=0.0,
        next_visual_change_at=None,
    )

    decided = StatusBarController.should_render_multi_agent(
        None, glance, _project(statuses)
    )

    assert decided is False


def test_the_strip_is_coloured_by_main_agents_only() -> None:
    """Two mains under 40 workers is a two-colour strip, in their brands.

    Live, 87 workers were fed straight into identity colouring: the
    multi-agent renderer could never switch off and the provider colour
    was structurally unreachable, so Claude drew palette magenta.
    """
    from sidepulse.colors import MODE_WORKING, scale_hex_brightness

    statuses = [
        _main("a", AgentMode.WORKING),
        _main("b", AgentMode.WORKING, provider="codex"),
    ] + [_worker(index, AgentMode.WORKING) for index in range(_OBSERVED_FANOUT)]
    colors = ColorSettings.defaults()

    _state, program = program_for_projection(
        _project(statuses),
        led_count=8,
        colors=colors,
    )

    _floor, ceiling = colors.fade_range(MODE_WORKING)
    pulses = [line for line in program.splitlines() if "pulse" in line][0]
    hues = {
        token.split(":", 1)[1]
        for token in pulses.replace(";", " ").split()
        if ":#" in token
    }
    assert hues == {
        scale_hex_brightness(colors.agent_color("claude"), ceiling),
        scale_hex_brightness(colors.agent_color("codex"), ceiling),
    }


# --- the mailbox still sees them -------------------------------------------


def test_the_mailbox_still_counts_a_family_s_workers() -> None:
    """The one legitimate consumer must not lose them in the split."""
    statuses = [_main("main", AgentMode.WORKING)] + [
        _worker(index, AgentMode.WORKING) for index in range(_OBSERVED_FANOUT)
    ]

    mailbox = project_mailbox(_project(statuses))
    rows = [row for section in mailbox.sections for row in section.rows]

    assert len(rows) == 1
    assert rows[0].worker_count == _OBSERVED_FANOUT
    assert mailbox.active_count == 1


# --- every other counted surface -------------------------------------------


def _source(provider: str = "claude") -> SourceKey:
    return SourceKey(provider, "local", "local.01", "sessions")


def _watermark(source: SourceKey, rank: int) -> ProviderWatermark:
    return ProviderWatermark(
        source_key=source,
        basis=WatermarkBasis.OCCURRED_AT_TIE_BREAK,
        occurred_at_epoch=1_800_000_000.0 + rank,
        event_token=EventToken(f"event:{rank}"),
        sequence=None,
        tie_break_rank=rank,
    )


def _work(
    work_id: str,
    lifecycle: WorkLifecycle,
    *,
    rank: int,
    parent_key: WorkKey | None = None,
) -> CanonicalWorkTruth:
    source = _source()
    return CanonicalWorkTruth(
        key=WorkKey(source, WorkIdentifier(work_id)),
        lifecycle=lifecycle,
        watermark=_watermark(source, rank),
        observation_authority=ObservationAuthority.AUTHORITATIVE_PROVIDER,
        source_health=SourceHealth.HEALTHY,
        source_freshness=SourceFreshness.FRESH,
        next_actor=NextActor.PROVIDER,
        safe_label=f"Claude {work_id}",
        parent_key=parent_key,
        request_keys=(),
        timing_uncertain=False,
    )


def _fanned_out_state():
    main = _work("main", WorkLifecycle.ACTIVE, rank=1)
    workers = tuple(
        _work(f"w{index}", WorkLifecycle.ACTIVE, rank=index + 2, parent_key=main.key)
        for index in range(_OBSERVED_FANOUT)
    )
    return replace(empty_operator_state(), generation=1, works=(main, *workers))


def test_the_menu_bar_title_counts_main_agents_only() -> None:
    state = _fanned_out_state()
    glance = ResolvedGlance(
        semantic=GlanceSemantic.ACTIVE,
        glyph=SemanticGlyph.CENTER_PAIR,
        cue=None,
        override_reason=GlanceOverrideReason.NONE,
        relay_epoch=10.0,
        next_visual_change_at=None,
    )

    value = status_item_accessibility(state, glance).value

    assert "Active: 1" in value
    assert f"Active: {_OBSERVED_FANOUT + 1}" not in value


def test_the_dropdown_header_counts_working_families_only() -> None:
    """"N active" must mean working, not retained.

    Live: 27 retained families, of which 16 completed and 8 idle. The
    header called all 27 "active".
    """
    state = replace(
        _fanned_out_state(),
        works=(
            _work("main", WorkLifecycle.ACTIVE, rank=1),
            _work("done-1", WorkLifecycle.COMPLETED, rank=2),
            _work("done-2", WorkLifecycle.COMPLETED, rank=3),
            _work("idle-1", WorkLifecycle.IDLE, rank=4),
        ),
    )
    mailbox = project_canonical_mailbox(state)
    documents = build_agent_browser_documents(
        state,
        mailbox,
        MailboxPreferenceProjection(
            projection=mailbox,
            retained_preferences=(),
            next_wake_epoch=None,
            woke_work_keys=(),
        ),
        LocalTriageState(acknowledgements=()),
    )

    projection = project_agent_browser(
        documents,
        AgentBrowserQuery(text=""),
        generation=1,
        selected_work_key=None,
    )

    assert projection.total_count == 4
    assert projection.active_count == 1
    assert projection.active_count == mailbox.active_count


def test_the_cli_active_count_excludes_workers() -> None:
    statuses = tuple(
        [_main("main", AgentMode.WORKING)]
        + [_worker(index, AgentMode.WORKING) for index in range(_OBSERVED_FANOUT)]
    )

    assert aggregate_status(statuses).active_count == 1


def test_the_menu_bar_title_is_a_ledger_not_a_spoken_sentence() -> None:
    """The eye gets one number; VoiceOver still gets the whole sentence.

    Both were the same string, so the menu bar rendered the screen-reader
    value verbatim and AppKit cut it off mid-word:
    "Agents active, Active: 34, Source partially availabl..."
    """
    from sidepulse.operator_accessibility import (
        MAX_STATUS_ITEM_TITLE_LENGTH,
        status_item_title,
    )

    state = _fanned_out_state()
    glance = ResolvedGlance(
        semantic=GlanceSemantic.ACTIVE,
        glyph=SemanticGlyph.CENTER_PAIR,
        cue=None,
        override_reason=GlanceOverrideReason.NONE,
        relay_epoch=10.0,
        next_visual_change_at=None,
    )

    title = status_item_title(state, glance)

    assert title == "1 working"
    assert len(title) <= MAX_STATUS_ITEM_TITLE_LENGTH
    assert "," not in title
    spoken = status_item_accessibility(state, glance).value
    assert "," in spoken and len(spoken) > len(title)


def test_collector_refreshes_a_delegating_parents_presence() -> None:
    """A fresh child event is evidence of the parent's presence.

    The projection-level promotion was not enough: active_count, the
    presence horizon and the staleness windows all read raw status
    modes at the COLLECTOR level, so a main silent for an hour while
    its workers streamed events aged out entirely -- the count said
    one, and the strip painted orphan murk (2026-08-27 owner report).
    """
    from sidepulse.collector import _reconcile_delegating_parents

    hour_old = _NOW - timedelta(seconds=3700)
    stopped_main = replace(
        _main("main", AgentMode.COMPLETED), updated_at=hour_old
    )
    busy_child = _worker(1, AgentMode.TOOL_RUNNING)

    reconciled = _reconcile_delegating_parents(
        (stopped_main, busy_child), _NOW + timedelta(seconds=5)
    )

    parent = next(row for row in reconciled if not row.is_subagent)
    assert parent.mode is AgentMode.WORKING
    assert parent.updated_at == busy_child.updated_at, (
        "the parent's clock advances to its freshest child, so the "
        "presence horizon and staleness windows see a live session"
    )
    assert aggregate_status(reconciled).active_count == 1


def test_collector_leaves_a_parent_with_finished_children_alone() -> None:
    from sidepulse.collector import _reconcile_delegating_parents

    stopped_main = _main("main", AgentMode.COMPLETED)
    finished_child = _worker(1, AgentMode.COMPLETED)

    reconciled = _reconcile_delegating_parents(
        (stopped_main, finished_child), _NOW + timedelta(seconds=5)
    )

    assert reconciled == (stopped_main, finished_child)


def test_collector_never_rewrites_an_asking_or_failed_parent() -> None:
    from sidepulse.collector import _reconcile_delegating_parents

    asking = _main("asker", AgentMode.WAITING_FOR_INPUT)
    failed = replace(
        _main("failer", AgentMode.BLOCKED_ERROR), session_id="failer"
    )
    children = (
        replace(_worker(1, AgentMode.TOOL_RUNNING), session_id="asker"),
        replace(_worker(2, AgentMode.TOOL_RUNNING), session_id="failer"),
    )

    reconciled = _reconcile_delegating_parents(
        (asking, failed, *children), _NOW + timedelta(seconds=5)
    )

    assert reconciled[0].mode is AgentMode.WAITING_FOR_INPUT
    assert reconciled[1].mode is AgentMode.BLOCKED_ERROR


def test_a_stale_child_stops_vouching_for_its_parent() -> None:
    from sidepulse.collector import (
        DELEGATION_CHILD_FRESH_SECONDS,
        _reconcile_delegating_parents,
    )

    stopped_main = _main("main", AgentMode.COMPLETED)
    wedged_child = replace(
        _worker(1, AgentMode.TOOL_RUNNING),
        updated_at=_NOW - timedelta(seconds=DELEGATION_CHILD_FRESH_SECONDS + 60),
    )

    reconciled = _reconcile_delegating_parents(
        (stopped_main, wedged_child), _NOW
    )

    assert reconciled[0].mode is AgentMode.COMPLETED
