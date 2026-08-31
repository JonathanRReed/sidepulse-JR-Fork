"""Typed, native-framework-free answer projection and controller routing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from .announcer_stack import (
    AnnouncerAlertIdentity,
    AnnouncerStackAction,
    AnnouncerStackIntent,
    AnnouncerStackPlan,
    AnnouncerStackState,
    announcer_alert_identity,
    empty_announcer_stack_state,
    legacy_announcer_alert_identity,
    legacy_announcer_status_is_answerable,
    project_announcer_stack,
    reduce_announcer_stack_intent,
)
from .answer_in_place import (
    AnswerActionKind,
    AnswerCapability,
    AnswerControlPlan,
    answer_capability_for_request,
    project_answer_controls,
)
from .answer_runtime import AnswerHandlerRegistry, AnswerRuntime
from .capacity_types import SourceKey
from .models import AgentStatus
from .operator_state import CanonicalOperatorState, CanonicalRequestTruth
from .provider_contracts import NegotiatedProviderContract
from .provider_facts import RequestKey, WorkKey


@dataclass(frozen=True, slots=True)
class AnswerBrowserCommand:
    work_key: WorkKey
    generation: int
    request_identity: AnnouncerAlertIdentity
    action: AnswerActionKind
    reply_text: str | None

    def __post_init__(self) -> None:
        if not (
            type(self.work_key) is WorkKey
            and type(self.generation) is int
            and self.generation >= 0
            and type(self.request_identity) is AnnouncerAlertIdentity
            and type(self.action) is AnswerActionKind
            and (self.reply_text is None or type(self.reply_text) is str)
        ):
            raise ValueError("invalid browser answer command")


@dataclass(frozen=True, slots=True)
class AnswerRequestAttemptKey:
    request_identity: AnnouncerAlertIdentity
    generation: int

    def __post_init__(self) -> None:
        if not (
            type(self.request_identity) is AnnouncerAlertIdentity
            and type(self.generation) is int
            and self.generation >= 0
        ):
            raise ValueError("invalid answer request attempt key")


@dataclass(frozen=True, slots=True)
class AnswerSurfacePresentation:
    plan: AnnouncerStackPlan
    answer_plan: AnswerControlPlan | None

    def __post_init__(self) -> None:
        if not (
            type(self.plan) is AnnouncerStackPlan
            and (
                self.answer_plan is None
                or type(self.answer_plan) is AnswerControlPlan
            )
        ):
            raise ValueError("invalid answer surface presentation")


@dataclass(frozen=True, slots=True)
class AnswerStackUpdate:
    state: AnnouncerStackState
    presentation: AnswerSurfacePresentation
    open_route: AgentStatus | None

    def __post_init__(self) -> None:
        if not (
            type(self.state) is AnnouncerStackState
            and type(self.presentation) is AnswerSurfacePresentation
            and (self.open_route is None or type(self.open_route) is AgentStatus)
        ):
            raise ValueError("invalid answer stack update")


class AnswerController:
    """One answer authority shared by Screen Bar and Agent Browser routes."""

    def __init__(
        self,
        *,
        contracts_by_source: dict[SourceKey, NegotiatedProviderContract],
        dispatch_main: Callable[[Callable[[], None]], None],
        on_refresh: Callable[[AnswerSurfacePresentation], None],
        open_route: Callable[[AgentStatus], None],
    ) -> None:
        if not (
            type(contracts_by_source) is dict
            and callable(dispatch_main)
            and callable(on_refresh)
            and callable(open_route)
        ):
            raise ValueError("invalid answer controller dependency")
        self.contracts_by_source = contracts_by_source
        self.handler_registry = AnswerHandlerRegistry()
        self.runtime = AnswerRuntime(
            registry=self.handler_registry,
            dispatch_main=dispatch_main,
            on_change=self._runtime_changed,
        )
        self.routes: dict[AnnouncerAlertIdentity, AgentStatus] = {}
        self.requests_by_identity: dict[
            AnnouncerAlertIdentity, CanonicalRequestTruth
        ] = {}
        self.attempt_key: AnswerRequestAttemptKey | None = None
        self._next_attempt_generation = 0
        self.stack_state = empty_announcer_stack_state()
        self.context: tuple[
            CanonicalOperatorState | None,
            tuple[object, ...],
            tuple[AgentStatus, ...],
        ] | None = None
        self._on_refresh = on_refresh
        self._open_route = open_route

    def _attempt_key_for(
        self,
        request_identity: AnnouncerAlertIdentity,
    ) -> AnswerRequestAttemptKey:
        key = self.attempt_key
        if key is not None and key.request_identity == request_identity:
            return key
        key = AnswerRequestAttemptKey(
            request_identity,
            self._next_attempt_generation,
        )
        self._next_attempt_generation += 1
        self.attempt_key = key
        return key

    def _clear_attempt(self) -> None:
        self.runtime.clear()
        self.attempt_key = None

    def _capability_for_request(
        self,
        request: CanonicalRequestTruth | None,
    ) -> AnswerCapability:
        if request is None or type(getattr(request, "key", None)) is not RequestKey:
            return AnswerCapability(
                supported=False,
                supports_reply_text=False,
                supports_binary_decision=False,
                invocation=None,
                disabled_reason="Jump to session",
            )
        contract = self.contracts_by_source.get(request.key.work_key.source_key)
        capability = answer_capability_for_request(contract, request.request_kind)
        invocation = capability.invocation
        if (
            capability.supported
            and invocation is not None
            and not self.runtime.has_handler(invocation)
        ):
            return AnswerCapability(
                supported=False,
                supports_reply_text=False,
                supports_binary_decision=False,
                invocation=None,
                disabled_reason="Answer handler unavailable",
            )
        return capability

    @staticmethod
    def _selected_identity(plan: AnnouncerStackPlan) -> AnnouncerAlertIdentity | None:
        return (
            plan.alerts[plan.selected_index].identity
            if plan.selected_index is not None
            else None
        )

    def _answer_plan(self, plan: AnnouncerStackPlan) -> AnswerControlPlan | None:
        selected_identity = self._selected_identity(plan)
        request = self.requests_by_identity.get(selected_identity)
        if selected_identity is None or request is None:
            self._clear_attempt()
            return None
        attempt_key = self._attempt_key_for(selected_identity)
        try:
            attempt = self.runtime.reconcile(
                attempt_key.request_identity,
                attempt_key.generation,
            )
        except RuntimeError:
            return None
        return replace(
            project_answer_controls(
                request.request_kind,
                self._capability_for_request(request),
                attempt,
            ),
            generation=plan.generation,
        )

    def _presentation(self, plan: AnnouncerStackPlan) -> AnswerSurfacePresentation:
        selected_identity = self._selected_identity(plan)
        presented = replace(
            plan,
            can_open=(
                selected_identity is not None and selected_identity in self.routes
            ),
        )
        return AnswerSurfacePresentation(presented, self._answer_plan(presented))

    @staticmethod
    def _route_identity(status: AgentStatus) -> AnnouncerAlertIdentity | None:
        if type(status.request_key) is RequestKey:
            return announcer_alert_identity(status.request_key)
        return legacy_announcer_alert_identity(
            status.provider,
            status.agent_id,
            status.session_id,
            status.event_name,
            status.work_key,
            status.tool_name,
        )

    @staticmethod
    def _identity_work_keys(
        operator_state: CanonicalOperatorState | None,
        actionable_rows: tuple[object, ...],
    ) -> dict[AnnouncerAlertIdentity, WorkKey | None]:
        result: dict[AnnouncerAlertIdentity, WorkKey | None] = {}
        if operator_state is not None:
            result.update(
                (announcer_alert_identity(request.key), request.key.work_key)
                for request in operator_state.requests
            )
        for row in actionable_rows:
            status = getattr(row, "source_status", None)
            request_key = getattr(row, "request_key", None)
            if type(request_key) is not RequestKey and type(status) is AgentStatus:
                request_key = status.request_key
            if type(request_key) is RequestKey:
                identity = announcer_alert_identity(request_key)
            elif type(status) is AgentStatus:
                identity = AnswerController._route_identity(status)
            else:
                identity = None
            if identity is None:
                continue
            work_key = getattr(row, "work_key", None)
            if type(work_key) is not WorkKey and type(status) is AgentStatus:
                work_key = status.work_key
            result.setdefault(
                identity,
                work_key if type(work_key) is WorkKey else None,
            )
        return result

    @classmethod
    def _routes_for(
        cls,
        plan: AnnouncerStackPlan,
        operator_state: CanonicalOperatorState | None,
        actionable_rows: tuple[object, ...],
        current_statuses: tuple[AgentStatus, ...],
    ) -> dict[AnnouncerAlertIdentity, AgentStatus]:
        alert_identities = {alert.identity for alert in plan.alerts}
        identity_work_keys = cls._identity_work_keys(operator_state, actionable_rows)
        routes: dict[AnnouncerAlertIdentity, AgentStatus] = {}
        for status in current_statuses:
            identity = cls._route_identity(status)
            if identity in alert_identities:
                routes.setdefault(identity, status)
        for identity in alert_identities - routes.keys():
            work_key = identity_work_keys.get(identity)
            if type(work_key) is not WorkKey:
                continue
            owning_row = next(
                (
                    row
                    for row in actionable_rows
                    if getattr(row, "work_key", None) == work_key
                    or getattr(
                        getattr(row, "source_status", None), "work_key", None
                    )
                    == work_key
                ),
                None,
            )
            if owning_row is None:
                continue
            owning_agent_id = getattr(owning_row, "agent_id", None)
            route = next(
                (
                    status
                    for status in current_statuses
                    if status.work_key == work_key
                    and status.agent_id == owning_agent_id
                    and legacy_announcer_status_is_answerable(status)
                ),
                None,
            )
            if route is not None:
                routes[identity] = route
        return routes

    def present(
        self,
        stack_state: AnnouncerStackState,
        plan: AnnouncerStackPlan,
        operator_state: CanonicalOperatorState | None,
        actionable_rows: tuple[object, ...],
        current_statuses: tuple[AgentStatus, ...],
    ) -> AnswerSurfacePresentation:
        if not (
            type(stack_state) is AnnouncerStackState
            and type(plan) is AnnouncerStackPlan
            and plan.generation == stack_state.generation
            and (
                operator_state is None
                or type(operator_state) is CanonicalOperatorState
            )
            and type(actionable_rows) is tuple
            and type(current_statuses) is tuple
            and all(type(status) is AgentStatus for status in current_statuses)
        ):
            raise ValueError("invalid answer presentation inputs")
        self.stack_state = stack_state
        self.context = (operator_state, actionable_rows, current_statuses)
        self.routes.clear()
        self.routes.update(
            self._routes_for(plan, operator_state, actionable_rows, current_statuses)
        )
        self.requests_by_identity.clear()
        if operator_state is not None:
            alert_identities = {alert.identity for alert in plan.alerts}
            self.requests_by_identity.update(
                {
                    announcer_alert_identity(request.key): request
                    for request in operator_state.requests
                    if announcer_alert_identity(request.key) in alert_identities
                }
            )
        return self._presentation(plan)

    def handle_stack_intent(
        self,
        intent: object,
    ) -> AnswerStackUpdate | None:
        if type(intent) is not AnnouncerStackIntent:
            return None
        state = self.stack_state
        if (
            intent.generation != state.generation
            or intent.selected_identity != state.selected_identity
        ):
            return None
        next_state = reduce_announcer_stack_intent(state, intent)
        if next_state.generation == state.generation:
            return None
        context = self.context
        if context is None:
            return None
        self.stack_state = next_state
        operator_state, actionable_rows, current_statuses = context
        plan = project_announcer_stack(
            next_state,
            operator_state,
            actionable_rows,
            current_statuses,
        )
        route = (
            self.routes.get(intent.selected_identity)
            if intent.action is AnnouncerStackAction.OPEN
            else None
        )
        return AnswerStackUpdate(next_state, self._presentation(plan), route)

    def _dispatch_answer(
        self,
        action: AnswerActionKind,
        request_identity: AnnouncerAlertIdentity,
        reply_text: str | None,
        request: CanonicalRequestTruth,
    ) -> bool:
        capability = self._capability_for_request(request)
        if not capability.supported or capability.invocation is None:
            return False
        attempt_key = self._attempt_key_for(request_identity)
        try:
            attempt = self.runtime.reconcile(
                attempt_key.request_identity,
                attempt_key.generation,
                draft_text=reply_text or "",
            )
            controls = project_answer_controls(
                request.request_kind,
                capability,
                attempt,
            )
        except (RuntimeError, ValueError):
            return False
        if action not in controls.primary_actions:
            return False
        if action is AnswerActionKind.CANCEL:
            return self.runtime.cancel(
                attempt_key.request_identity,
                attempt_key.generation,
            )
        if action is AnswerActionKind.RETRY:
            return self.runtime.retry(
                attempt_key.request_identity,
                attempt_key.generation,
            )
        if action not in {
            AnswerActionKind.APPROVE,
            AnswerActionKind.DENY,
            AnswerActionKind.REPLY,
        } or not controls.can_send:
            return False
        return self.runtime.submit(
            capability.invocation,
            request_identity=attempt_key.request_identity,
            generation=attempt_key.generation,
            request_kind=request.request_kind,
            action=action,
            reply_text=reply_text,
        )

    def handle_answer_intent(
        self,
        action: AnswerActionKind,
        generation: int,
        request_identity: AnnouncerAlertIdentity,
        reply_text: str | None,
    ) -> None:
        state = self.stack_state
        if not (
            type(action) is AnswerActionKind
            and type(generation) is int
            and type(request_identity) is AnnouncerAlertIdentity
            and generation == state.generation
            and request_identity == state.selected_identity
        ):
            return
        if action is AnswerActionKind.JUMP:
            route = self.routes.get(request_identity)
            if route is not None:
                self._open_route(route)
            return
        request = self.requests_by_identity.get(request_identity)
        if request is None:
            return
        if self._dispatch_answer(
            action,
            request_identity,
            reply_text,
            request,
        ):
            self._emit_current()

    def _route_for_browser(
        self,
        request_identity: AnnouncerAlertIdentity,
        work_key: WorkKey,
        current_statuses: tuple[AgentStatus, ...],
    ) -> AgentStatus | None:
        for status in current_statuses:
            if status.work_key != work_key:
                continue
            if (
                type(status.request_key) is RequestKey
                and announcer_alert_identity(status.request_key) == request_identity
            ):
                return status
        route = self.routes.get(request_identity)
        return route if route is not None and route.work_key == work_key else None

    def perform_browser_answer(
        self,
        command: object,
        operator_state: CanonicalOperatorState | None,
        current_statuses: tuple[AgentStatus, ...],
    ) -> bool:
        if not (
            type(command) is AnswerBrowserCommand
            and type(operator_state) is CanonicalOperatorState
            and command.generation == operator_state.generation
            and type(current_statuses) is tuple
            and all(type(status) is AgentStatus for status in current_statuses)
        ):
            return False
        request = next(
            (
                candidate
                for candidate in operator_state.requests
                if candidate.key.work_key == command.work_key
                and announcer_alert_identity(candidate.key)
                == command.request_identity
            ),
            None,
        )
        if request is None:
            return False
        if command.action is AnswerActionKind.JUMP:
            route = self._route_for_browser(
                command.request_identity,
                command.work_key,
                current_statuses,
            )
            if route is None:
                return False
            self._open_route(route)
            return True
        return self._dispatch_answer(
            command.action,
            command.request_identity,
            command.reply_text,
            request,
        )

    def _emit_current(self) -> None:
        context = self.context
        if context is None:
            return
        operator_state, actionable_rows, current_statuses = context
        plan = project_announcer_stack(
            self.stack_state,
            operator_state,
            actionable_rows,
            current_statuses,
        )
        self._on_refresh(self._presentation(plan))

    def _runtime_changed(self, attempt) -> None:
        state = self.stack_state
        attempt_key = self.attempt_key
        if (
            attempt_key is None
            or attempt.request_identity != attempt_key.request_identity
            or attempt.generation != attempt_key.generation
            or attempt.request_identity != state.selected_identity
        ):
            return
        self._emit_current()

    def reset(self) -> None:
        self.runtime.clear()
        self.attempt_key = None
        self.stack_state = empty_announcer_stack_state()
        self.routes.clear()
        self.requests_by_identity.clear()
        self.context = None


__all__ = [
    "AnswerBrowserCommand",
    "AnswerController",
    "AnswerRequestAttemptKey",
    "AnswerStackUpdate",
    "AnswerSurfacePresentation",
]
