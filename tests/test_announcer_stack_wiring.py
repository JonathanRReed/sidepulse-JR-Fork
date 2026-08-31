from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sidepulse import status_bar
from sidepulse.agent_browser_window import AgentBrowserAnswerPayload
from sidepulse.announcer_stack import (
    AnnouncerStackAction,
    AnnouncerStackIntent,
    AnnouncerStackVisibility,
    announcer_alert_identity,
)
from sidepulse.answer_in_place import AnswerActionKind, AnswerAttemptState
from sidepulse.attention import AttentionProjection, LifecycleMode, ProjectedAgentRow
from sidepulse.capacity_types import SourceKey
from sidepulse.dnd_policy import (
    DndMode,
    DndSource,
    compose_dnd_contributions,
    contribution_for_mode,
)
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.operator_state import (
    BootIdentifier,
    ClockSample,
    empty_operator_state,
    reduce_operator_state,
)
from sidepulse.provider_contracts import (
    AdapterIdentifier,
    ContractStatus,
    LocalRuntimeSurfaceIdentifier,
    NegotiatedProviderContract,
    ProductCapability,
    ProductCapabilityBinding,
    ProductCapabilityDeclaration,
    ProviderIdentifier,
    SchemaVersion,
    SourceInstanceIdentifier,
)
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderFactBatch,
    ProviderRequestFact,
    ProviderRequestState,
    ProviderWatermark,
    ProviderWorkFact,
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
from sidepulse.virtual_device import VirtualStatusDevice

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _source(value: str = "source:main") -> SourceKey:
    return SourceKey("codex", "hook", value, "agent")


def _work(value: str, *, source: SourceKey | None = None) -> WorkKey:
    return WorkKey(source or _source(), WorkIdentifier(value))


def _request(value: str, work: WorkKey) -> RequestKey:
    return RequestKey(work, RequestIdentifier(value))


def _watermark(source: SourceKey, token: str = "event:one") -> ProviderWatermark:
    return ProviderWatermark(
        source,
        WatermarkBasis.PROVIDER_EVENT_ID,
        1_800_000_000,
        EventToken(token),
        None,
        0,
    )


def _canonical(requests: tuple[tuple[RequestKey, RequestKind], ...]):
    work_keys = tuple(dict.fromkeys(request.work_key for request, _kind in requests))
    source = work_keys[0].source_key
    batch = ProviderFactBatch(
        source_key=source,
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        source_health=SourceHealth.HEALTHY,
        source_freshness=SourceFreshness.FRESH,
        observed_at_epoch=1_800_000_000,
        watermark=_watermark(source),
        work_facts=tuple(
            ProviderWorkFact(
                key=work,
                lifecycle=WorkLifecycle.WAITING,
                watermark=_watermark(source, f"event:{work.work_id.value}"),
                safe_label=f"Codex {work.work_id.value}",
                parent_key=None,
                next_actor=NextActor.USER,
            )
            for work in work_keys
        ),
        request_facts=tuple(
            ProviderRequestFact(
                key=request,
                state=ProviderRequestState.LIVE,
                request_kind=kind,
                next_actor=NextActor.USER,
                watermark=_watermark(source, f"event:{request.request_id.value}"),
            )
            for request, kind in requests
        ),
        diagnostics=(),
    )
    return reduce_operator_state(
        empty_operator_state(),
        batch,
        clock=ClockSample(1_800_000_000, 1, BootIdentifier("boot:one")),
    ).state


def _status(
    work: WorkKey,
    *,
    request: RequestKey | None = None,
    agent_id: str | None = None,
    message: str = "Approve access?",
    mode: AgentMode = AgentMode.WAITING_FOR_INPUT,
    event_name: str = "PermissionRequest",
    tool_name: str | None = None,
) -> AgentStatus:
    identifier = agent_id or f"codex:session:{work.work_id.value}"
    return AgentStatus(
        provider="codex",
        agent_id=identifier,
        display_name=f"Codex {work.work_id.value}",
        mode=mode,
        updated_at=NOW,
        event_name=event_name,
        session_id=work.work_id.value,
        tool_name=tool_name,
        message=message,
        work_key=work,
        request_key=request,
    )


def _row(status: AgentStatus) -> ProjectedAgentRow:
    return ProjectedAgentRow(
        agent_id=status.agent_id,
        provider=status.provider,
        display_name=status.display_name,
        lifecycle_mode=LifecycleMode.WAITING,
        actionable=True,
        is_subagent=False,
        updated_at=status.updated_at,
        source_status=status,
        work_key=status.work_key,
        request_key=status.request_key,
    )


def _projection(*rows: ProjectedAgentRow) -> AttentionProjection:
    return AttentionProjection(
        lifecycle_mode=LifecycleMode.WAITING if rows else LifecycleMode.IDLE,
        actionable_attention=tuple(rows),
        visible_rows=tuple(rows),
        transient_signals=(),
        dominant_provider=rows[0].provider if rows else None,
        click_target_agent_id=rows[0].agent_id if rows else None,
    )


def _snapshot(operator_state, statuses: tuple[AgentStatus, ...]):
    return SimpleNamespace(operator_state=operator_state, statuses=statuses)


def _answer_contract(source: SourceKey) -> NegotiatedProviderContract:
    return NegotiatedProviderContract(
        schema_version=SchemaVersion(1, 0),
        provider_id=ProviderIdentifier(source.provider_id),
        adapter_id=AdapterIdentifier(source.adapter_id),
        source_instance_id=SourceInstanceIdentifier(source.source_instance_id),
        status=ContractStatus.SUPPORTED,
        product_capabilities=(
            ProductCapabilityDeclaration(
                ProductCapability.ANSWERING,
                supported=True,
                binding=ProductCapabilityBinding.local(
                    LocalRuntimeSurfaceIdentifier("local.answer_in_place")
                ),
            ),
        ),
    )


def _enable_answering(target, source: SourceKey, handler):
    contract = _answer_contract(source)
    target._answer_contracts_by_source[source] = contract
    invocation = contract.product_invocation_for(ProductCapability.ANSWERING)
    target.answer_handler_registry.register(invocation, handler)
    return invocation


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(status_bar, "default_settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(status_bar, "default_latest_state_path", lambda: tmp_path / "latest.json")
    monkeypatch.setattr(
        status_bar,
        "default_activity_ledger_path",
        lambda: tmp_path / "activity-ledger.json",
    )
    monkeypatch.setattr(status_bar, "discover_devices", lambda: [])
    monkeypatch.setattr(status_bar.focus_sync, "active_focus_mode_identifiers", lambda: [])
    target = status_bar.StatusBarController.alloc().init()
    target.settings = replace(
        target.settings,
        virtual_status_device_enabled=True,
        screen_bar_gauges_enabled=False,
    )
    target.virtual_status_device = SimpleNamespace(
        can_present_announcer=MagicMock(return_value=True),
        set_wraps_menu_bar=MagicMock(),
        set_geometry_overrides=MagicMock(),
        set_bracket_style=MagicMock(),
        set_min_glow=MagicMock(),
        set_follow_alcove=MagicMock(),
        set_standing_gauges=MagicMock(),
        set_click_handler=MagicMock(),
        set_pointer_interaction_relevant=MagicMock(),
        set_announcer_stack=MagicMock(),
        hide=MagicMock(),
        terminate=MagicMock(),
    )
    monkeypatch.setattr(target, "status_bar_devices", lambda *, remember: [])
    monkeypatch.setattr(target, "screen_bar_click_status", lambda: None)
    yield target
    target.answer_runtime.close(timeout_seconds=0.0)
    target._persistence_writer.close(timeout_seconds=2.0)


def _sync(target, projection: AttentionProjection | None) -> tuple[object, object]:
    target.sync_virtual_status_device(
        AgentMode.WAITING_FOR_INPUT,
        None,
        (),
        projection=projection,
        relay_elapsed_seconds=0.0,
    )
    return target.virtual_status_device.set_announcer_stack.call_args.args


def test_fully_dark_hides_announcer_and_pointer_without_erasing_stack_truth(
    controller,
) -> None:
    source = _source()
    work = _work("one", source=source)
    request = _request("one", work)
    status = _status(work, request=request)
    projection = _projection(_row(status))
    controller.last_snapshot = _snapshot(
        _canonical(((request, RequestKind.PERMISSION),)),
        (status,),
    )
    controller.dnd_controller = SimpleNamespace(
        projection=compose_dnd_contributions(
            (contribution_for_mode(DndSource.MANUAL, DndMode.DARK),),
        )
    )

    presented = _sync(controller, projection)

    assert presented == (None, None)
    controller.virtual_status_device.set_pointer_interaction_relevant.assert_called_with(
        False
    )
    controller.virtual_status_device.set_standing_gauges.assert_called_with(0.0, False)
    assert controller._announcer_stack_state.ordered_identities


@pytest.mark.parametrize(
    ("changed", "expected"),
    (
        ({}, True),
        ({"_enabled": False}, False),
        ({"window": SimpleNamespace(isVisible=lambda: False)}, False),
        ({"_display_asleep": True}, False),
        ({"_fullscreen_hidden": True}, False),
        ({"_alcove_relevant": True}, False),
        ({"_compact_active": True}, False),
        ({"_terminating": True}, False),
    ),
)
def test_virtual_device_reports_exact_announcer_presentation_truth(
    changed,
    expected,
) -> None:
    device = VirtualStatusDevice.alloc().init()
    device.window = SimpleNamespace(isVisible=lambda: True)
    device._enabled = True
    device._display_asleep = False
    device._fullscreen_hidden = False
    device._alcove_relevant = False
    device._compact_active = False
    device._terminating = False
    for name, value in changed.items():
        setattr(device, name, value)

    assert device.can_present_announcer() is expected


def test_reveal_current_ask_expands_then_collapses_the_current_generation(
    controller,
) -> None:
    work = _work("work:reveal")
    request = _request("request:reveal", work)
    status = _status(work, request=request, message="Use current ask?")
    controller.last_snapshot = _snapshot(
        _canonical(((request, RequestKind.PERMISSION),)),
        (status,),
    )
    controller.current_attention_projection = _projection(_row(status))
    controller.openAgentBrowser_ = MagicMock(return_value=True)

    assert controller.performRevealCurrentAsk_(None) is True
    expanded = controller.virtual_status_device.set_announcer_stack.call_args.args[0]
    expanded_identity = expanded.alerts[expanded.selected_index].identity

    assert expanded.visibility is AnnouncerStackVisibility.EXPANDED
    assert expanded_identity == announcer_alert_identity(request)
    assert expanded.generation == controller._announcer_stack_state.generation

    assert controller.performRevealCurrentAsk_(None) is True
    collapsed = controller.virtual_status_device.set_announcer_stack.call_args.args[0]

    assert collapsed.visibility is AnnouncerStackVisibility.COLLAPSED
    assert collapsed.alerts[collapsed.selected_index].identity == expanded_identity
    assert collapsed.generation == controller._announcer_stack_state.generation
    assert collapsed.generation > expanded.generation
    controller.openAgentBrowser_.assert_not_called()


@pytest.mark.parametrize(
    ("has_actionable_ask", "can_present_announcer"),
    ((False, True), (False, False), (True, False)),
)
def test_reveal_current_ask_uses_agent_browser_without_a_presentable_ask(
    controller,
    has_actionable_ask,
    can_present_announcer,
) -> None:
    work = _work("work:fallback")
    request = _request("request:fallback", work)
    status = _status(work, request=request)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    controller.last_snapshot = _snapshot(canonical, (status,))
    controller.current_attention_projection = (
        _projection(_row(status)) if has_actionable_ask else _projection()
    )
    controller.virtual_status_device.can_present_announcer.return_value = (
        can_present_announcer
    )
    controller.openAgentBrowser_ = MagicMock(return_value=True)

    assert controller.performRevealCurrentAsk_(None) is True

    controller.openAgentBrowser_.assert_called_once_with(None)
    controller.virtual_status_device.set_announcer_stack.assert_not_called()


@pytest.mark.parametrize(
    ("has_actionable_ask", "can_present_announcer"),
    ((True, False), (False, True)),
)
def test_reveal_current_ask_fallback_preserves_in_flight_answer_attempt(
    controller,
    has_actionable_ask,
    can_present_announcer,
) -> None:
    work = _work("work:fallback-in-flight")
    request = _request("request:fallback-in-flight", work)
    status = _status(work, request=request)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    controller.last_snapshot = _snapshot(canonical, (status,))
    controller.current_attention_projection = _projection(_row(status))
    started = threading.Event()
    release = threading.Event()

    def handler(*_args, **_kwargs) -> None:
        started.set()
        release.wait(1.0)

    _enable_answering(controller, work.source_key, handler)
    plan, _stack_handler = _sync(controller, controller.current_attention_projection)
    answer_handler = controller.virtual_status_device.set_announcer_stack.call_args.kwargs[
        "answer_handler"
    ]
    identity = plan.alerts[plan.selected_index].identity
    answer_handler(AnswerActionKind.APPROVE, plan.generation, identity, None)
    assert started.wait(1.0)

    attempt_key = controller.answer_controller.attempt_key
    assert attempt_key is not None
    runtime_before = controller.answer_runtime.snapshot(
        attempt_key.request_identity,
        attempt_key.generation,
    )
    assert runtime_before is not None
    assert runtime_before.state is AnswerAttemptState.SENDING
    controller_stack_before = controller._announcer_stack_state
    answer_stack_before = controller.answer_controller.stack_state
    context_before = controller.answer_controller.context
    routes_before = dict(controller.answer_controller.routes)
    requests_before = dict(controller.answer_controller.requests_by_identity)

    controller.current_attention_projection = (
        _projection(_row(status)) if has_actionable_ask else _projection()
    )
    controller.virtual_status_device.can_present_announcer.return_value = (
        can_present_announcer
    )
    controller.openAgentBrowser_ = MagicMock(return_value=True)
    original_present = controller.answer_controller.present
    controller.answer_controller.present = MagicMock(wraps=original_present)
    controller.virtual_status_device.set_announcer_stack.reset_mock()

    try:
        assert controller.performRevealCurrentAsk_(None) is True

        controller.openAgentBrowser_.assert_called_once_with(None)
        controller.virtual_status_device.set_announcer_stack.assert_not_called()
        controller.answer_controller.present.assert_not_called()
        assert controller._announcer_stack_state == controller_stack_before
        assert controller.answer_controller.stack_state == answer_stack_before
        assert controller.answer_controller.context == context_before
        assert controller.answer_controller.routes == routes_before
        assert controller.answer_controller.requests_by_identity == requests_before
        assert controller.answer_controller.attempt_key == attempt_key
        assert controller.answer_runtime.snapshot(
            attempt_key.request_identity,
            attempt_key.generation,
        ) == runtime_before
    finally:
        release.set()


def test_reveal_current_ask_reconciles_new_identity_without_action_side_effects(
    controller,
) -> None:
    first_work = _work("work:stale")
    first_request = _request("request:stale", first_work)
    first_status = _status(first_work, request=first_request)
    controller.last_snapshot = _snapshot(
        _canonical(((first_request, RequestKind.INPUT),)),
        (first_status,),
    )
    _sync(controller, _projection(_row(first_status)))

    current_work = _work("work:current")
    current_request = _request("request:current", current_work)
    current_status = _status(current_work, request=current_request)
    controller.last_snapshot = _snapshot(
        _canonical(((current_request, RequestKind.PERMISSION),)),
        (current_status,),
    )
    controller.current_attention_projection = _projection(_row(current_status))
    controller._apply_triage_action = MagicMock()
    controller._publish_mailbox_preferences = MagicMock()
    controller.mark_activity_seen_now = MagicMock()
    controller.track_completions = MagicMock()
    controller._prune_notification_action_bindings = MagicMock()
    controller.sync_leds = MagicMock()
    controller._hardware_write_worker = MagicMock()
    controller.local_triage_state = object()
    controller.mailbox_preferences = (object(),)
    controller.mailbox_seen_completion_ids = {"completion:one"}
    controller._notification_action_bindings = {"notification:one": object()}
    local_triage = controller.local_triage_state
    mailbox_preferences = controller.mailbox_preferences
    completion_receipts = set(controller.mailbox_seen_completion_ids)
    notification_bindings = dict(controller._notification_action_bindings)

    assert controller.performRevealCurrentAsk_(None) is True

    revealed = controller.virtual_status_device.set_announcer_stack.call_args.args[0]
    assert revealed.alerts[revealed.selected_index].identity == announcer_alert_identity(
        current_request
    )
    assert announcer_alert_identity(first_request) not in {
        alert.identity for alert in revealed.alerts
    }
    assert controller.local_triage_state is local_triage
    assert controller.mailbox_preferences is mailbox_preferences
    assert controller.mailbox_seen_completion_ids == completion_receipts
    assert controller._notification_action_bindings == notification_bindings
    controller._apply_triage_action.assert_not_called()
    controller._publish_mailbox_preferences.assert_not_called()
    controller.mark_activity_seen_now.assert_not_called()
    controller.track_completions.assert_not_called()
    controller._prune_notification_action_bindings.assert_not_called()
    controller.sync_leds.assert_not_called()
    controller._hardware_write_worker.submit.assert_not_called()


def test_controller_uses_only_current_snapshot_truth_and_projection_none_is_empty(
    controller,
) -> None:
    work = _work("work:one")
    first = _request("request:first", work)
    second = _request("request:second", work)
    controller.current_operator_state = _canonical(
        ((first, RequestKind.INPUT), (second, RequestKind.PERMISSION))
    )
    current = _status(work, request=first, message="Current question")
    controller.last_snapshot = _snapshot(None, (current,))

    empty_plan, handler = _sync(controller, None)

    assert empty_plan.total_actionable_count == 0
    assert empty_plan.visibility is AnnouncerStackVisibility.HIDDEN
    assert callable(handler)

    plan, _handler = _sync(controller, _projection(_row(current)))

    assert plan.total_actionable_count == 1
    assert plan.alerts[0].question == "Current question"


def test_controller_keeps_stable_order_and_selects_new_permission_without_reordering(
    controller,
) -> None:
    work_a = _work("work:a")
    work_b = _work("work:b")
    work_c = _work("work:c")
    request_a = _request("request:a", work_a)
    request_b = _request("request:b", work_b)
    request_c = _request("request:c", work_c)
    status_a = _status(work_a, request=request_a)
    status_b = _status(work_b, request=request_b)
    status_c = _status(work_c, request=request_c)
    controller.last_snapshot = _snapshot(
        _canonical(
            ((request_a, RequestKind.INPUT), (request_c, RequestKind.REVIEW))
        ),
        (status_a, status_c),
    )
    first, _handler = _sync(controller, _projection(_row(status_c), _row(status_a)))
    assert first.alerts[first.selected_index].identity == first.alerts[1].identity

    controller.last_snapshot = _snapshot(
        _canonical(
            ((request_a, RequestKind.INPUT), (request_b, RequestKind.PERMISSION))
        ),
        (status_b, status_a),
    )
    refreshed, _handler = _sync(
        controller,
        _projection(_row(status_b), _row(status_a)),
    )

    assert [alert.agent_id for alert in refreshed.alerts] == [
        status_a.agent_id,
        status_b.agent_id,
    ]
    assert refreshed.alerts[refreshed.selected_index].agent_id == status_b.agent_id


def test_one_work_owns_multiple_requests_and_exact_current_status_route_wins(
    controller,
) -> None:
    work = _work("work:one")
    first = _request("request:first", work)
    second = _request("request:second", work)
    canonical = _canonical(
        ((first, RequestKind.INPUT), (second, RequestKind.PERMISSION))
    )
    owning_status = _status(work, request=first, agent_id="codex:session:owning")
    exact_status = _status(
        work,
        request=second,
        agent_id="codex:session:exact",
        message="Exact current question",
    )
    controller.last_snapshot = _snapshot(canonical, (owning_status, exact_status))
    controller.open_session = MagicMock()

    plan, handler = _sync(controller, _projection(_row(owning_status)))

    assert plan.total_actionable_count == 2
    assert plan.alerts[plan.selected_index].question == "Exact current question"
    generation = plan.generation

    def assert_generation_consumed(*_args, **_kwargs) -> None:
        assert controller._announcer_stack_state.generation == generation + 1

    controller.open_session.side_effect = assert_generation_consumed
    handler(
        AnnouncerStackIntent(
            AnnouncerStackAction.OPEN,
            generation,
            plan.alerts[plan.selected_index].identity,
        )
    )

    controller.open_session.assert_called_once_with(exact_status, None, remember=False)


def test_open_falls_back_to_current_owning_work_status_or_is_disabled(controller) -> None:
    work = _work("work:one")
    request = _request("request:one", work)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    owning_status = _status(work, request=None)
    controller.last_snapshot = _snapshot(canonical, (owning_status,))
    controller.open_session = MagicMock()

    plan, handler = _sync(controller, _projection(_row(owning_status)))
    assert plan.can_open is True
    handler(
        AnnouncerStackIntent(
            AnnouncerStackAction.OPEN,
            plan.generation,
            plan.alerts[0].identity,
        )
    )
    controller.open_session.assert_called_once_with(owning_status, None, remember=False)

    controller.last_snapshot = _snapshot(canonical, ())
    disabled, disabled_handler = _sync(controller, _projection(_row(owning_status)))
    assert disabled.can_open is False
    before = controller._announcer_stack_state.generation
    disabled_handler(
        AnnouncerStackIntent(
            AnnouncerStackAction.OPEN,
            disabled.generation,
            disabled.alerts[0].identity,
        )
    )
    assert controller._announcer_stack_state.generation == before + 1
    controller.open_session.assert_called_once()


def test_open_fallback_rejects_another_agent_with_the_same_work_key(controller) -> None:
    work = _work("work:shared")
    request = _request("request:one", work)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    owning_status = _status(work, request=None, agent_id="codex:session:owner")
    other_status = _status(work, request=None, agent_id="codex:session:other")
    controller.last_snapshot = _snapshot(canonical, (other_status,))
    controller.open_session = MagicMock()

    plan, handler = _sync(controller, _projection(_row(owning_status)))

    assert plan.can_open is False
    assert controller._announcer_status_routes == {}
    handler(
        AnnouncerStackIntent(
            AnnouncerStackAction.OPEN,
            plan.generation,
            plan.alerts[0].identity,
        )
    )
    controller.open_session.assert_not_called()


def test_open_fallback_rejects_non_actionable_current_status_for_owner(controller) -> None:
    work = _work("work:one")
    request = _request("request:one", work)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    owning_ask = _status(work, request=None)
    non_actionable = _status(
        work,
        request=None,
        mode=AgentMode.WORKING,
        event_name="PreToolUse",
    )
    controller.last_snapshot = _snapshot(canonical, (non_actionable,))
    controller.open_session = MagicMock()

    plan, handler = _sync(controller, _projection(_row(owning_ask)))

    assert plan.can_open is False
    assert controller._announcer_status_routes == {}
    handler(
        AnnouncerStackIntent(
            AnnouncerStackAction.OPEN,
            plan.generation,
            plan.alerts[0].identity,
        )
    )
    controller.open_session.assert_not_called()


@pytest.mark.parametrize(
    ("request_kind", "event_name", "tool_name"),
    (
        (RequestKind.APPROVAL, "PreToolUse", "ExitPlanMode"),
        (RequestKind.REVIEW, "ReviewRequest", None),
    ),
)
def test_open_fallback_accepts_current_owning_plan_and_review_asks(
    controller,
    request_kind,
    event_name,
    tool_name,
) -> None:
    work = _work("work:answerable")
    request = _request("request:answerable", work)
    canonical = _canonical(((request, request_kind),))
    owning_status = _status(
        work,
        request=None,
        event_name=event_name,
        tool_name=tool_name,
    )
    controller.last_snapshot = _snapshot(canonical, (owning_status,))
    controller.open_session = MagicMock()

    plan, handler = _sync(controller, _projection(_row(owning_status)))

    assert plan.can_open is True
    handler(
        AnnouncerStackIntent(
            AnnouncerStackAction.OPEN,
            plan.generation,
            plan.alerts[0].identity,
        )
    )
    controller.open_session.assert_called_once_with(
        owning_status,
        None,
        remember=False,
    )


def test_presentation_intents_resync_only_typed_announcer_and_stale_intent_is_noop(
    controller,
) -> None:
    work_a = _work("work:a")
    work_b = _work("work:b")
    request_a = _request("request:a", work_a)
    request_b = _request("request:b", work_b)
    canonical = _canonical(
        ((request_a, RequestKind.INPUT), (request_b, RequestKind.PERMISSION))
    )
    status_a = _status(work_a, request=request_a)
    status_b = _status(work_b, request=request_b)
    controller.last_snapshot = _snapshot(canonical, (status_a, status_b))
    controller.sync_leds = MagicMock(wraps=controller.sync_leds)
    controller._hardware_write_worker = MagicMock()

    plan, handler = _sync(controller, _projection(_row(status_a), _row(status_b)))
    stale_intent = AnnouncerStackIntent(
        AnnouncerStackAction.EXPAND,
        plan.generation,
        plan.alerts[plan.selected_index].identity,
    )
    _equivalent, _current_handler = _sync(
        controller,
        _projection(_row(status_b), _row(status_a)),
    )
    before_stale = controller._announcer_stack_state
    calls_before_stale = controller.virtual_status_device.set_announcer_stack.call_count
    handler(object())
    handler(stale_intent)
    assert controller._announcer_stack_state == before_stale
    assert controller.virtual_status_device.set_announcer_stack.call_count == calls_before_stale

    current_plan, current_handler = (
        controller.virtual_status_device.set_announcer_stack.call_args.args
    )
    for action in (
        AnnouncerStackAction.EXPAND,
        AnnouncerStackAction.NEXT,
        AnnouncerStackAction.PREVIOUS,
        AnnouncerStackAction.COLLAPSE,
    ):
        current_handler(
            AnnouncerStackIntent(
                action,
                current_plan.generation,
                current_plan.alerts[current_plan.selected_index].identity,
            )
        )
        current_plan, current_handler = (
            controller.virtual_status_device.set_announcer_stack.call_args.args
        )

    controller.sync_leds.assert_not_called()
    controller._hardware_write_worker.submit.assert_not_called()


def test_mark_seen_is_screen_bar_local_then_resolution_and_new_request_reconcile(
    controller,
) -> None:
    work_a = _work("work:a")
    work_b = _work("work:b")
    request_a = _request("request:a", work_a)
    request_b = _request("request:b", work_b)
    status_a = _status(work_a, request=request_a)
    status_b = _status(work_b, request=request_b)
    canonical_a = _canonical(((request_a, RequestKind.INPUT),))
    controller.last_snapshot = _snapshot(
        canonical_a,
        (status_a,),
    )
    controller.current_operator_state = canonical_a
    controller._apply_triage_action = MagicMock()
    controller.mark_activity_seen_now = MagicMock()
    controller.track_completions = MagicMock()
    controller._prune_notification_action_bindings = MagicMock()
    controller.sync_leds = MagicMock(wraps=controller.sync_leds)
    controller._hardware_write_worker = MagicMock()
    controller.local_triage_state = object()
    controller.mailbox_seen_completion_ids = {"completion:one"}
    controller._notification_action_bindings = {"notification:one": object()}
    local_triage = controller.local_triage_state
    completion_receipts = set(controller.mailbox_seen_completion_ids)
    notification_bindings = dict(controller._notification_action_bindings)
    canonical_requests = controller.current_operator_state.requests

    plan, handler = _sync(controller, _projection(_row(status_a)))
    handler(
        AnnouncerStackIntent(
            AnnouncerStackAction.MARK_SEEN,
            plan.generation,
            plan.alerts[0].identity,
        )
    )
    hidden, _handler = controller.virtual_status_device.set_announcer_stack.call_args.args

    assert hidden.visibility is AnnouncerStackVisibility.HIDDEN
    assert controller.local_triage_state is local_triage
    assert controller.mailbox_seen_completion_ids == completion_receipts
    assert controller._notification_action_bindings == notification_bindings
    assert controller.current_operator_state.requests == canonical_requests
    controller._apply_triage_action.assert_not_called()
    controller.mark_activity_seen_now.assert_not_called()
    controller.track_completions.assert_not_called()
    controller._prune_notification_action_bindings.assert_not_called()
    controller.sync_leds.assert_not_called()
    controller._hardware_write_worker.submit.assert_not_called()

    controller.last_snapshot = _snapshot(
        _canonical(
            ((request_a, RequestKind.INPUT), (request_b, RequestKind.PERMISSION))
        ),
        (status_a, status_b),
    )
    reannounced, _handler = _sync(
        controller,
        _projection(_row(status_b), _row(status_a)),
    )
    assert reannounced.visibility is AnnouncerStackVisibility.COLLAPSED
    assert reannounced.total_actionable_count == 2
    assert reannounced.unseen_count == 1

    controller.last_snapshot = _snapshot(
        _canonical(((request_b, RequestKind.PERMISSION),)),
        (status_b,),
    )
    resolved, _handler = _sync(controller, _projection(_row(status_b)))
    assert resolved.total_actionable_count == 1
    assert resolved.unseen_count == 1
    assert controller.sync_leds.call_count == 0
    controller.sync_leds(
        AgentMode.WAITING_FOR_INPUT,
        None,
        status_bar.LED_DISPLAY_AGENT,
        (status_b,),
        projection=_projection(_row(status_b)),
    )
    controller.sync_leds.assert_called_once()


def test_screen_bar_receives_current_capability_gated_answer_plan(controller) -> None:
    work = _work("work:answer")
    request = _request("request:answer", work)
    status = _status(work, request=request)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    controller.last_snapshot = _snapshot(canonical, (status,))
    invocation = _enable_answering(controller, work.source_key, lambda *_a, **_k: None)

    plan, _handler = _sync(controller, _projection(_row(status)))
    answer_plan = controller.virtual_status_device.set_announcer_stack.call_args.kwargs[
        "answer_plan"
    ]
    answer_handler = controller.virtual_status_device.set_announcer_stack.call_args.kwargs[
        "answer_handler"
    ]

    assert answer_plan.request_identity == plan.alerts[plan.selected_index].identity
    assert answer_plan.generation == plan.generation
    assert answer_plan.capability.invocation == invocation
    assert answer_plan.primary_actions == (
        AnswerActionKind.APPROVE,
        AnswerActionKind.DENY,
        AnswerActionKind.JUMP,
    )
    assert callable(answer_handler)


def test_missing_live_answer_handler_fails_closed_to_jump(controller) -> None:
    work = _work("work:no-handler")
    request = _request("request:no-handler", work)
    status = _status(work, request=request)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    controller._answer_contracts_by_source[work.source_key] = _answer_contract(
        work.source_key
    )
    controller.last_snapshot = _snapshot(canonical, (status,))

    _sync(controller, _projection(_row(status)))
    answer_plan = controller.virtual_status_device.set_announcer_stack.call_args.kwargs[
        "answer_plan"
    ]

    assert answer_plan.capability.supported is False
    assert answer_plan.primary_actions == (AnswerActionKind.JUMP,)
    assert answer_plan.status_text == "Answer handler unavailable"


def test_screen_bar_answer_runs_exact_route_without_acknowledgement_side_effects(
    controller,
) -> None:
    work = _work("work:isolated")
    request = _request("request:isolated", work)
    status = _status(work, request=request)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    controller.last_snapshot = _snapshot(canonical, (status,))
    controller.current_operator_state = canonical
    controller._apply_triage_action = MagicMock()
    controller._publish_mailbox_preferences = MagicMock()
    controller.mark_activity_seen_now = MagicMock()
    controller.track_completions = MagicMock()
    controller._prune_notification_action_bindings = MagicMock()
    controller.sync_leds = MagicMock()
    controller._hardware_write_worker = MagicMock()
    called = threading.Event()
    received: list[tuple] = []

    def handler(invocation, *, request_kind, answer_kind, reply_text) -> None:
        received.append((invocation, request_kind, answer_kind, reply_text))
        called.set()

    invocation = _enable_answering(controller, work.source_key, handler)
    plan, _stack_handler = _sync(controller, _projection(_row(status)))
    answer_handler = controller.virtual_status_device.set_announcer_stack.call_args.kwargs[
        "answer_handler"
    ]
    identity = plan.alerts[plan.selected_index].identity

    answer_handler(AnswerActionKind.APPROVE, plan.generation, identity, None)

    assert called.wait(1.0)
    assert received == [
        (invocation, RequestKind.PERMISSION, AnswerActionKind.APPROVE, None)
    ]
    controller._apply_triage_action.assert_not_called()
    controller._publish_mailbox_preferences.assert_not_called()
    controller.mark_activity_seen_now.assert_not_called()
    controller.track_completions.assert_not_called()
    controller._prune_notification_action_bindings.assert_not_called()
    controller.sync_leds.assert_not_called()
    controller._hardware_write_worker.submit.assert_not_called()


def test_agent_browser_answer_payload_uses_identity_generation_and_capability_fences(
    controller,
) -> None:
    work = _work("work:browser")
    request = _request("request:browser", work)
    status = _status(work, request=request)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    controller.last_snapshot = _snapshot(canonical, (status,))
    controller.current_operator_state = canonical
    called = threading.Event()
    received: list[tuple] = []

    def handler(invocation, *, request_kind, answer_kind, reply_text) -> None:
        received.append((invocation, request_kind, answer_kind, reply_text))
        called.set()

    invocation = _enable_answering(controller, work.source_key, handler)
    identity = announcer_alert_identity(request).value
    current = AgentBrowserAnswerPayload(
        work_key=work,
        generation=canonical.generation,
        request_identity=identity,
        action=AnswerActionKind.APPROVE,
    )

    assert controller.performAgentBrowserPayload_(
        AgentBrowserAnswerPayload(
            work_key=work,
            generation=canonical.generation + 1,
            request_identity=identity,
            action=AnswerActionKind.APPROVE,
        )
    ) is False
    assert controller.performAgentBrowserPayload_(
        AgentBrowserAnswerPayload(
            work_key=work,
            generation=canonical.generation,
            request_identity="request:not-current",
            action=AnswerActionKind.APPROVE,
        )
    ) is False
    assert controller.performAgentBrowserPayload_(current) is True
    assert called.wait(1.0)
    assert received == [
        (invocation, RequestKind.PERMISSION, AnswerActionKind.APPROVE, None)
    ]


def test_agent_browser_jump_opens_exact_route_without_scheduling_answer_work(
    controller,
) -> None:
    work = _work("work:jump")
    request = _request("request:jump", work)
    status = _status(work, request=request)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    controller.last_snapshot = _snapshot(canonical, (status,))
    controller.current_operator_state = canonical
    controller.open_session = MagicMock()
    controller.answer_runtime.submit = MagicMock()
    controller.answer_runtime.retry = MagicMock()
    controller.answer_runtime.cancel = MagicMock()
    identity = announcer_alert_identity(request)

    assert controller.performAgentBrowserPayload_(
        AgentBrowserAnswerPayload(
            work_key=work,
            generation=canonical.generation,
            request_identity=identity.value,
            action=AnswerActionKind.JUMP,
        )
    ) is True

    controller.open_session.assert_called_once_with(status, None, remember=False)
    controller.answer_runtime.submit.assert_not_called()
    controller.answer_runtime.retry.assert_not_called()
    controller.answer_runtime.cancel.assert_not_called()


def test_canonical_resolution_clears_answer_before_late_provider_completion(
    controller,
) -> None:
    work = _work("work:cleared")
    request = _request("request:cleared", work)
    status = _status(work, request=request)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    controller.last_snapshot = _snapshot(canonical, (status,))
    release = threading.Event()
    started = threading.Event()

    def handler(*_args, **_kwargs) -> None:
        started.set()
        release.wait(1.0)

    _enable_answering(controller, work.source_key, handler)
    plan, _stack_handler = _sync(controller, _projection(_row(status)))
    answer_handler = controller.virtual_status_device.set_announcer_stack.call_args.kwargs[
        "answer_handler"
    ]
    identity = plan.alerts[0].identity
    answer_handler(AnswerActionKind.APPROVE, plan.generation, identity, None)
    assert started.wait(1.0)
    attempt_key = controller.answer_controller.attempt_key
    assert attempt_key is not None
    assert controller.answer_runtime.snapshot(
        attempt_key.request_identity,
        attempt_key.generation,
    ).state is AnswerAttemptState.SENDING

    controller.last_snapshot = _snapshot(None, ())
    _sync(controller, None)
    assert controller.answer_runtime.snapshot(
        attempt_key.request_identity,
        attempt_key.generation,
    ) is None
    release.set()


def test_noop_screen_refresh_and_browser_share_one_request_attempt(controller) -> None:
    work = _work("work:stable-attempt")
    request = _request("request:stable-attempt", work)
    status = _status(work, request=request)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    controller.last_snapshot = _snapshot(canonical, (status,))
    controller.current_operator_state = canonical
    started = threading.Event()
    release = threading.Event()

    def handler(*_args, **_kwargs) -> None:
        started.set()
        release.wait(1.0)

    _enable_answering(controller, work.source_key, handler)
    first_plan, _stack_handler = _sync(controller, _projection(_row(status)))
    first_answer = controller.virtual_status_device.set_announcer_stack.call_args.kwargs[
        "answer_plan"
    ]
    answer_handler = controller.virtual_status_device.set_announcer_stack.call_args.kwargs[
        "answer_handler"
    ]
    identity = first_plan.alerts[first_plan.selected_index].identity
    answer_handler(
        AnswerActionKind.APPROVE,
        first_plan.generation,
        identity,
        None,
    )
    assert started.wait(1.0)
    attempt_key = controller.answer_controller.attempt_key

    second_plan, _stack_handler = _sync(controller, _projection(_row(status)))
    second_answer = controller.virtual_status_device.set_announcer_stack.call_args.kwargs[
        "answer_plan"
    ]

    assert second_plan.generation != first_plan.generation
    assert second_answer.generation == second_plan.generation
    assert second_answer.state is AnswerAttemptState.SENDING
    assert controller.answer_controller.attempt_key == attempt_key
    answer_handler(
        AnswerActionKind.CANCEL,
        first_answer.generation,
        identity,
        None,
    )
    assert controller.answer_runtime.snapshot(
        attempt_key.request_identity,
        attempt_key.generation,
    ).state is AnswerAttemptState.SENDING

    newer_canonical = replace(canonical, generation=canonical.generation + 1)
    controller.current_operator_state = newer_canonical
    assert controller.performAgentBrowserPayload_(
        AgentBrowserAnswerPayload(
            work_key=work,
            generation=canonical.generation,
            request_identity=identity.value,
            action=AnswerActionKind.CANCEL,
        )
    ) is False
    assert controller.answer_runtime.snapshot(
        attempt_key.request_identity,
        attempt_key.generation,
    ).state is AnswerAttemptState.SENDING
    assert controller.performAgentBrowserPayload_(
        AgentBrowserAnswerPayload(
            work_key=work,
            generation=newer_canonical.generation,
            request_identity=identity.value,
            action=AnswerActionKind.CANCEL,
        )
    ) is True
    assert controller.answer_runtime.snapshot(
        attempt_key.request_identity,
        attempt_key.generation,
    ).state is AnswerAttemptState.CANCELLED
    release.set()


def test_shutdown_closes_answer_runtime_before_virtual_surface(controller) -> None:
    order: list[str] = []
    controller.answer_runtime.close = MagicMock(
        side_effect=lambda **_kwargs: order.append("answer-runtime") or True
    )
    controller.virtual_status_device.terminate.side_effect = lambda: order.append(
        "virtual-device"
    )
    controller._usage_refresh_workers = MagicMock()
    controller._runtime_worker_registry = MagicMock()
    controller._persistence_writer = MagicMock()
    controller._persistence_writer.snapshot.return_value = SimpleNamespace(
        accepting=False
    )
    controller.monitor = None

    controller.applicationWillTerminate_(None)

    controller.answer_runtime.close.assert_called_once_with(timeout_seconds=1.0)
    assert order.index("answer-runtime") < order.index("virtual-device")


def test_shutdown_logs_when_answer_handler_outlives_close_budget(
    controller,
    monkeypatch,
) -> None:
    controller.answer_runtime.close = MagicMock(return_value=False)
    controller.virtual_status_device.terminate = MagicMock()
    controller._usage_refresh_workers = MagicMock()
    controller._runtime_worker_registry = MagicMock()
    controller._persistence_writer = MagicMock()
    controller._persistence_writer.snapshot.return_value = SimpleNamespace(
        accepting=False
    )
    controller.monitor = None
    log = MagicMock()
    monkeypatch.setattr(status_bar, "log_status_bar", log)

    controller.applicationWillTerminate_(None)

    controller.answer_runtime.close.assert_called_once_with(timeout_seconds=1.0)
    log.assert_any_call(
        "answer runtime shutdown timed out; provider work may still be running"
    )
    controller.virtual_status_device.terminate.assert_called_once_with()
