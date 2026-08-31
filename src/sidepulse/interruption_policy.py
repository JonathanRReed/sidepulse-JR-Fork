"""Semantic notification identity: bounded copy and opaque action tokens.

This module owns the two things a delivered notification may carry: bounded
product-owned copy with no source display content, and an opaque HMAC action
token that re-resolves to exactly one current semantic event. It does not
mutate canonical truth, deliver notifications, or perform any I/O.

The channel/stage delivery planner that used to live here (plan_interruptions,
the quiet plane, and the delivery ledger it planned against) was deleted on
2026-08-26: production never constructed a DeliveryLedger, so the planner
routed for a ledger nobody kept. `InterruptionRoute` survives as the pure
notification identity the live notification path actually builds.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from .operator_state import (
    InterruptionClass,
    SemanticEventKey,
    semantic_event_key_to_payload,
)
from .product_identity import PRODUCT_DISPLAY_NAME
from .provider_facts import RequestKey

MAX_INTERRUPTION_EVENTS: Final = 2_000
MAX_ACTION_TOKEN_TTL_SECONDS: Final = 3_600.0
ACTION_TOKEN_RANDOM_BYTES_MIN: Final = 32
ACTION_TOKEN_RANDOM_BYTES_MAX: Final = 64

_PROVIDER_LABELS: Final = {
    "codex": "Codex",
    "claude": "Claude",
    "devin": "Devin",
    "grok": "Grok",
    "cursor": "Cursor",
    "hermes": "Hermes",
    "openclaw": "OpenClaw",
    "opencode": "OpenCode",
    "kiro": "Kiro",
}
_ACTION_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")


class InterruptionPolicyValidationError(ValueError):
    """Pure interruption input failed closed."""


@dataclass(frozen=True, slots=True)
class InterruptionRoute:
    event_key: SemanticEventKey
    interruption_class: InterruptionClass
    request_key: RequestKey | None

    def __post_init__(self) -> None:
        if not (
            type(self.event_key) is SemanticEventKey
            and type(self.interruption_class) is InterruptionClass
            and (self.request_key is None or type(self.request_key) is RequestKey)
        ):
            raise InterruptionPolicyValidationError("invalid interruption route")
        if self.request_key is not None and self.event_key.subject_key != self.request_key:
            raise InterruptionPolicyValidationError("invalid interruption route")


@dataclass(frozen=True, slots=True)
class GenericNotificationCopy:
    title: str
    body: str

    def __post_init__(self) -> None:
        if not (
            type(self.title) is str
            and type(self.body) is str
            and 1 <= len(self.title) <= 32
            and 1 <= len(self.body) <= 96
            and self.title.isprintable()
            and self.body.isprintable()
        ):
            raise InterruptionPolicyValidationError("invalid notification copy")


@dataclass(frozen=True, slots=True)
class ActionTokenBinding:
    token: str
    event_fingerprint: str
    operator_generation: int
    expires_at_epoch: float

    def __post_init__(self) -> None:
        if not (
            type(self.token) is str
            and _ACTION_TOKEN.fullmatch(self.token) is not None
            and type(self.event_fingerprint) is str
            and _FINGERPRINT.fullmatch(self.event_fingerprint) is not None
            and type(self.operator_generation) is int
            and self.operator_generation >= 0
            and _valid_epoch(self.expires_at_epoch)
        ):
            raise InterruptionPolicyValidationError("invalid action token binding")
        object.__setattr__(self, "expires_at_epoch", float(self.expires_at_epoch))


def generic_notification_copy(route: InterruptionRoute) -> GenericNotificationCopy:
    """Return bounded product-owned copy with no source display content."""
    if type(route) is not InterruptionRoute:
        raise InterruptionPolicyValidationError("invalid notification route")
    subject = route.event_key.subject_key
    source = subject.source_key if type(subject) is not RequestKey else subject.work_key.source_key
    provider = _PROVIDER_LABELS.get(source.provider_id, "Provider")
    article = "An" if provider[:1].upper() in "AEIOU" else "A"
    if route.interruption_class is InterruptionClass.ACTION_REQUIRED:
        body = f"{article} {provider} session needs you"
    elif route.interruption_class in {
        InterruptionClass.IMPORTANT_OUTCOME,
        InterruptionClass.COURTESY,
    }:
        body = f"{article} {provider} session finished"
    else:
        body = f"{PRODUCT_DISPLAY_NAME} has 1 update"
    return GenericNotificationCopy(PRODUCT_DISPLAY_NAME, body)


def issue_action_token(
    *,
    randomness: bytes,
    event_key: SemanticEventKey,
    operator_generation: int,
    now: float,
    ttl_seconds: float = 300.0,
) -> ActionTokenBinding:
    """Bind caller-supplied randomness to one in-memory event fingerprint."""
    if not (
        type(randomness) is bytes
        and ACTION_TOKEN_RANDOM_BYTES_MIN <= len(randomness) <= ACTION_TOKEN_RANDOM_BYTES_MAX
        and type(event_key) is SemanticEventKey
        and type(operator_generation) is int
        and operator_generation >= 0
        and _valid_epoch(now)
        and _valid_positive_duration(ttl_seconds)
        and float(ttl_seconds) <= MAX_ACTION_TOKEN_TTL_SECONDS
    ):
        raise InterruptionPolicyValidationError("invalid action token input")
    fingerprint = _event_fingerprint(event_key)
    expires_at = float(now) + float(ttl_seconds)
    binding_payload = json.dumps(
        {
            "event_fingerprint": fingerprint,
            "expires_at_epoch": expires_at,
            "operator_generation": operator_generation,
        },
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    token = (
        base64.urlsafe_b64encode(hmac.new(randomness, binding_payload, hashlib.sha256).digest())
        .decode("ascii")
        .rstrip("=")
    )
    return ActionTokenBinding(
        token=token,
        event_fingerprint=fingerprint,
        operator_generation=operator_generation,
        expires_at_epoch=expires_at,
    )


def resolve_action_token(
    binding: ActionTokenBinding,
    *,
    presented_token: str,
    candidate_event_keys: Iterable[SemanticEventKey],
    current_generation: int,
    now: float,
) -> SemanticEventKey | None:
    """Fail closed unless one current exact event matches the opaque binding."""
    if not (
        type(binding) is ActionTokenBinding
        and type(presented_token) is str
        and type(current_generation) is int
        and current_generation >= 0
        and _valid_epoch(now)
        and current_generation == binding.operator_generation
        and float(now) < binding.expires_at_epoch
        and hmac.compare_digest(presented_token, binding.token)
    ):
        return None
    try:
        candidates = tuple(candidate_event_keys)
    except TypeError:
        return None
    if not (
        len(candidates) <= MAX_INTERRUPTION_EVENTS
        and all(type(key) is SemanticEventKey for key in candidates)
        and len(candidates) == len(set(candidates))
    ):
        return None
    matches = tuple(
        key
        for key in candidates
        if hmac.compare_digest(
            _event_fingerprint(key),
            binding.event_fingerprint,
        )
    )
    return matches[0] if len(matches) == 1 else None


def action_token_metadata(binding: ActionTokenBinding) -> dict[str, str]:
    """Return the exact content-free Notification Center user-info payload."""
    if type(binding) is not ActionTokenBinding:
        raise InterruptionPolicyValidationError("invalid action token binding")
    return {"action_token": binding.token}


def _event_fingerprint(key: SemanticEventKey) -> str:
    encoded = json.dumps(
        semantic_event_key_to_payload(key),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_epoch(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and float(value) >= 0.0


def _valid_positive_duration(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and float(value) > 0.0
