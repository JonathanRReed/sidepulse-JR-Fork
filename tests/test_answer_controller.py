from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from sidepulse.announcer_stack import (
    AnnouncerStackAction,
    AnnouncerStackIntent,
    announcer_alert_identity,
    empty_announcer_stack_state,
    project_announcer_stack,
    reconcile_announcer_stack,
)
from sidepulse.answer_controller import AnswerBrowserCommand, AnswerController
from sidepulse.answer_in_place import AnswerActionKind, AnswerAttemptState
from sidepulse.attention import LifecycleMode, ProjectedAgentRow
from sidepulse.capacity_types import SourceKey
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

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _truth():
    source = SourceKey("codex", "hook", "source:main", "agent")
    work = WorkKey(source, WorkIdentifier("work:one"))
    request = RequestKey(work, RequestIdentifier("request:one"))
    watermark = ProviderWatermark(
        source,
        WatermarkBasis.PROVIDER_EVENT_ID,
        1_800_000_000,
        EventToken("event:one"),
        None,
        0,
    )
    batch = ProviderFactBatch(
        source_key=source,
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        source_health=SourceHealth.HEALTHY,
        source_freshness=SourceFreshness.FRESH,
        observed_at_epoch=1_800_000_000,
        watermark=watermark,
        work_facts=(
            ProviderWorkFact(
                key=work,
                lifecycle=WorkLifecycle.WAITING,
                watermark=watermark,
                safe_label="Codex work:one",
                parent_key=None,
                next_actor=NextActor.USER,
            ),
        ),
        request_facts=(
            ProviderRequestFact(
                key=request,
                state=ProviderRequestState.LIVE,
                request_kind=RequestKind.PERMISSION,
                next_actor=NextActor.USER,
                watermark=watermark,
            ),
        ),
        diagnostics=(),
    )
    state = reduce_operator_state(
        empty_operator_state(),
        batch,
        clock=ClockSample(1_800_000_000, 1, BootIdentifier("boot:one")),
    ).state
    status = AgentStatus(
        provider="codex",
        agent_id="codex:session:one",
        display_name="Codex one",
        mode=AgentMode.WAITING_FOR_INPUT,
        updated_at=NOW,
        event_name="PermissionRequest",
        session_id="one",
        tool_name=None,
        message="Approve access?",
        work_key=work,
        request_key=request,
    )
    row = ProjectedAgentRow(
        agent_id=status.agent_id,
        provider=status.provider,
        display_name=status.display_name,
        lifecycle_mode=LifecycleMode.WAITING,
        actionable=True,
        is_subagent=False,
        updated_at=status.updated_at,
        source_status=status,
        work_key=work,
        request_key=request,
    )
    return source, work, request, state, status, row


def _contract(source: SourceKey) -> NegotiatedProviderContract:
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


def test_answer_controller_module_is_appkit_free() -> None:
    source = (
        Path(__file__).parents[1] / "src" / "sidepulse" / "answer_controller.py"
    ).read_text(encoding="utf-8")

    assert "AppKit" not in source
    assert "import objc" not in source
    assert "agent_browser_window" not in source


def test_controller_projects_exact_capability_and_dispatches_exact_handler() -> None:
    source, _work, _request, operator_state, status, row = _truth()
    refreshes = []
    opened = []
    called = Event()
    received = []
    controller = AnswerController(
        contracts_by_source={source: _contract(source)},
        dispatch_main=lambda callback: callback(),
        on_refresh=refreshes.append,
        open_route=opened.append,
    )
    invocation = _contract(source).product_invocation_for(ProductCapability.ANSWERING)

    def handler(invocation, *, request_kind, answer_kind, reply_text) -> None:
        received.append((invocation, request_kind, answer_kind, reply_text))
        called.set()

    controller.handler_registry.register(invocation, handler)
    stack_state = reconcile_announcer_stack(
        empty_announcer_stack_state(), operator_state, (row,), (status,)
    )
    plan = project_announcer_stack(stack_state, operator_state, (row,), (status,))

    presentation = controller.present(
        stack_state,
        plan,
        operator_state,
        (row,),
        (status,),
    )
    identity = announcer_alert_identity(_request)
    assert presentation.plan.can_open is True
    assert presentation.answer_plan is not None
    assert presentation.answer_plan.capability.invocation == invocation

    controller.handle_answer_intent(
        AnswerActionKind.APPROVE,
        plan.generation,
        identity,
        None,
    )

    assert called.wait(1.0)
    assert received == [
        (invocation, RequestKind.PERMISSION, AnswerActionKind.APPROVE, None)
    ]
    assert refreshes
    assert opened == []
    controller.runtime.close(timeout_seconds=1.0)


def test_stack_and_browser_jump_return_or_open_only_the_exact_route() -> None:
    source, work, request, operator_state, status, row = _truth()
    opened = []
    controller = AnswerController(
        contracts_by_source={source: _contract(source)},
        dispatch_main=lambda callback: callback(),
        on_refresh=lambda _presentation: None,
        open_route=opened.append,
    )
    stack_state = reconcile_announcer_stack(
        empty_announcer_stack_state(), operator_state, (row,), (status,)
    )
    plan = project_announcer_stack(stack_state, operator_state, (row,), (status,))
    controller.present(stack_state, plan, operator_state, (row,), (status,))
    identity = announcer_alert_identity(request)

    update = controller.handle_stack_intent(
        AnnouncerStackIntent(AnnouncerStackAction.OPEN, plan.generation, identity)
    )

    assert update is not None
    assert update.open_route is status
    assert opened == []
    assert controller.perform_browser_answer(
        AnswerBrowserCommand(
            work_key=work,
            generation=operator_state.generation,
            request_identity=identity,
            action=AnswerActionKind.JUMP,
            reply_text=None,
        ),
        operator_state,
        (status,),
    ) is True
    assert opened == [status]
    controller.runtime.close(timeout_seconds=1.0)


def test_request_attempt_survives_ui_generation_changes_across_surfaces() -> None:
    source, work, request, operator_state, status, row = _truth()
    started = Event()
    release = Event()
    controller = AnswerController(
        contracts_by_source={source: _contract(source)},
        dispatch_main=lambda callback: callback(),
        on_refresh=lambda _presentation: None,
        open_route=lambda _route: None,
    )
    invocation = _contract(source).product_invocation_for(ProductCapability.ANSWERING)

    def handler(*_args, **_kwargs) -> None:
        started.set()
        release.wait(1.0)

    controller.handler_registry.register(invocation, handler)
    first_state = reconcile_announcer_stack(
        empty_announcer_stack_state(), operator_state, (row,), (status,)
    )
    first_plan = project_announcer_stack(
        first_state, operator_state, (row,), (status,)
    )
    first = controller.present(
        first_state, first_plan, operator_state, (row,), (status,)
    )
    identity = announcer_alert_identity(request)
    controller.handle_answer_intent(
        AnswerActionKind.APPROVE,
        first.plan.generation,
        identity,
        None,
    )
    assert started.wait(1.0)
    attempt_key = controller.attempt_key

    second_state = reconcile_announcer_stack(
        first_state, operator_state, (row,), (status,)
    )
    second_plan = project_announcer_stack(
        second_state, operator_state, (row,), (status,)
    )
    second = controller.present(
        second_state, second_plan, operator_state, (row,), (status,)
    )

    assert second.plan.generation != first.plan.generation
    assert second.answer_plan is not None
    assert second.answer_plan.generation == second.plan.generation
    assert second.answer_plan.state is AnswerAttemptState.SENDING
    assert controller.attempt_key == attempt_key
    controller.handle_answer_intent(
        AnswerActionKind.CANCEL,
        first.plan.generation,
        identity,
        None,
    )
    assert controller.runtime.snapshot(
        attempt_key.request_identity,
        attempt_key.generation,
    ).state is AnswerAttemptState.SENDING

    assert controller.perform_browser_answer(
        AnswerBrowserCommand(
            work_key=work,
            generation=operator_state.generation,
            request_identity=identity,
            action=AnswerActionKind.CANCEL,
            reply_text=None,
        ),
        operator_state,
        (status,),
    ) is True
    assert controller.runtime.snapshot(
        attempt_key.request_identity,
        attempt_key.generation,
    ).state is AnswerAttemptState.CANCELLED

    release.set()
    controller.runtime.close(timeout_seconds=1.0)
