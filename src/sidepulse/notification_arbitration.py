"""Pure notification-arbitration decisions for the retained AppKit runtime."""

from __future__ import annotations

from .interruption_policy import (
    ActionTokenBinding,
    InterruptionRoute,
    action_token_metadata,
    generic_notification_copy,
    issue_action_token,
    resolve_action_token,
)
from .operator_state import InterruptionClass, SemanticEventKey
from .provider_facts import RequestKey, WorkKey

NotificationActionBindings = dict[str, tuple[ActionTokenBinding, SemanticEventKey]]


def _validated_bindings(bindings: object) -> NotificationActionBindings | None:
    if type(bindings) is not dict:
        return None
    validated: NotificationActionBindings = {}
    for token, pair in bindings.items():
        if type(token) is not str or type(pair) is not tuple or len(pair) != 2:
            return None
        binding, event_key = pair
        if (
            type(binding) is not ActionTokenBinding
            or type(event_key) is not SemanticEventKey
        ):
            return None
        validated[token] = (binding, event_key)
    return validated


def prune_notification_action_bindings(
    bindings: object,
    *,
    now: float,
    current_generation: int,
    max_bindings: int,
) -> NotificationActionBindings:
    validated = _validated_bindings(bindings)
    if (
        validated is None
        or type(now) not in {int, float}
        or type(current_generation) is not int
        or type(max_bindings) is not int
        or max_bindings <= 0
    ):
        return {}
    retained = {
        token: value
        for token, value in validated.items()
        if value[0].expires_at_epoch > float(now)
        and value[0].operator_generation == current_generation
    }
    if len(retained) <= max_bindings:
        return retained
    ordered = sorted(
        retained.items(),
        key=lambda item: (
            item[1][0].expires_at_epoch,
            item[0],
        ),
        reverse=True,
    )
    return dict(ordered[:max_bindings])


def issue_notification_action_binding(
    *,
    event_key: SemanticEventKey,
    operator_generation: int,
    now: float,
    randomness: bytes,
    existing_bindings: object,
    max_bindings: int,
    ttl_seconds: float,
) -> tuple[NotificationActionBindings, ActionTokenBinding] | None:
    if (
        type(event_key) is not SemanticEventKey
        or type(operator_generation) is not int
        or type(max_bindings) is not int
        or max_bindings <= 0
    ):
        return None
    bindings = prune_notification_action_bindings(
        existing_bindings,
        now=now,
        current_generation=operator_generation,
        max_bindings=max_bindings,
    )
    try:
        binding = issue_action_token(
            randomness=randomness,
            event_key=event_key,
            operator_generation=operator_generation,
            now=now,
            ttl_seconds=ttl_seconds,
        )
    except Exception:
        return None
    if len(bindings) >= max_bindings:
        oldest = min(
            bindings,
            key=lambda token: (
                bindings[token][0].expires_at_epoch,
                token,
            ),
        )
        bindings.pop(oldest, None)
    bindings[binding.token] = (binding, event_key)
    return bindings, binding


def plan_semantic_notification(
    *,
    event_key: SemanticEventKey,
    interruption_class: InterruptionClass,
    prefix: str,
    request_key: RequestKey | None,
    operator_generation: int,
    now: float,
    randomness: bytes,
    existing_bindings: object,
    max_bindings: int,
    ttl_seconds: float,
) -> tuple[NotificationActionBindings, str, str, str, dict[str, str]] | None:
    if (
        type(interruption_class) is not InterruptionClass
        or type(prefix) is not str
        or prefix not in {"completion", "attention"}
        or (request_key is not None and type(request_key) is not RequestKey)
    ):
        return None
    planned_binding = issue_notification_action_binding(
        event_key=event_key,
        operator_generation=operator_generation,
        now=now,
        randomness=randomness,
        existing_bindings=existing_bindings,
        max_bindings=max_bindings,
        ttl_seconds=ttl_seconds,
    )
    if planned_binding is None:
        return None
    bindings, binding = planned_binding
    try:
        route = InterruptionRoute(
            event_key=event_key,
            interruption_class=interruption_class,
            request_key=request_key,
        )
        copy = generic_notification_copy(route)
        metadata = action_token_metadata(binding)
    except Exception:
        bindings.pop(binding.token, None)
        return None
    return (
        bindings,
        f"{prefix}.{binding.event_fingerprint}",
        copy.title,
        copy.body,
        metadata,
    )


def resolve_notification_work_key(
    *,
    presented_token: object,
    bindings: object,
    current_generation: int,
    now: float,
) -> tuple[NotificationActionBindings, WorkKey | None]:
    validated = _validated_bindings(bindings)
    if validated is None:
        return {}, None
    updated = dict(validated)
    if type(presented_token) is not str or type(current_generation) is not int:
        return updated, None
    stored = updated.pop(presented_token, None)
    if stored is None:
        return updated, None
    binding, event_key = stored
    resolved = resolve_action_token(
        binding,
        presented_token=presented_token,
        candidate_event_keys=(event_key,),
        current_generation=current_generation,
        now=now,
    )
    if resolved is None:
        return updated, None
    subject = resolved.subject_key
    return updated, subject if type(subject) is WorkKey else subject.work_key


def should_post_completion_notification(
    *,
    status_present: bool,
    completion_notifications_enabled: bool,
    may_interrupt: bool,
    snoozed: bool,
    event_present: bool,
    banner_allowed: bool = True,
) -> bool:
    if not all(
        type(value) is bool
        for value in (
            status_present,
            completion_notifications_enabled,
            may_interrupt,
            snoozed,
            event_present,
            banner_allowed,
        )
    ):
        return False
    return (
        status_present
        and completion_notifications_enabled
        and may_interrupt
        and not snoozed
        and event_present
        and banner_allowed
    )


__all__ = [
    "NotificationActionBindings",
    "issue_notification_action_binding",
    "plan_semantic_notification",
    "prune_notification_action_bindings",
    "resolve_notification_work_key",
    "should_post_completion_notification",
]
