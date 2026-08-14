"""Pure fail-closed navigation and shared operator action descriptors."""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath
from typing import Final
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from .operator_state import (
    AcknowledgementEligibility,
    CanonicalRequestTruth,
    CanonicalWorkTruth,
    RequestPhase,
)
from .provider_facts import SourceFreshness, WorkKey

MAX_NAVIGATION_CANDIDATES: Final = 1_000
MAX_ACTION_ID_LENGTH: Final = 128
MAX_CANDIDATE_TARGET_LENGTH: Final = 8_192
MAX_EXECUTABLE_TARGET_LENGTH: Final = 2_048
MAX_TERMINAL_CWD_LENGTH: Final = 1_024
MAX_SESSION_ID_LENGTH: Final = 256
MAX_PIN_COUNT: Final = 100
MAX_SOURCE_GENERATION: Final = (1 << 63) - 1

_NOT_AVAILABLE: Final = "Not available"
_SOURCE_STALE: Final = "Source is stale"
_TARGET_CHANGED: Final = "Target changed"
_MULTIPLE_SOURCES: Final = "Multiple sources match"
_REFUSAL_REASONS: Final = frozenset(
    {_NOT_AVAILABLE, _SOURCE_STALE, _TARGET_CHANGED, _MULTIPLE_SOURCES}
)
_ACTION_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]*\Z")
_TERMINAL_OPENERS: Final = {
    "codex": ("codex", "resume"),
    "claude": ("claude", "--resume"),
    "devin": ("devin", "--resume"),
    "grok": ("grok", "--resume"),
    "cursor": ("cursor-agent", "--resume"),
    "hermes": ("hermes", "--resume"),
}


class NavigationResolutionKind(str, Enum):
    READY = "ready"
    DISABLED = "disabled"
    MISSING = "missing"
    STALE = "stale"
    AMBIGUOUS = "ambiguous"


class OperatorActionKind(str, Enum):
    OPEN = "open"
    WATCH = "watch"
    UNWATCH = "unwatch"
    PIN = "pin"
    UNPIN = "unpin"
    MOVE_PIN_UP = "move-pin-up"
    MOVE_PIN_DOWN = "move-pin-down"
    SNOOZE = "snooze"
    UNSNOOZE = "unsnooze"
    ACKNOWLEDGE = "acknowledge"
    RESUME_ESCALATION = "resume-escalation"


def _valid_bounded_text(value: object, *, maximum: int) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= maximum
        and value.isprintable()
    )


def _valid_generation(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_SOURCE_GENERATION


def _valid_action_id(value: object) -> bool:
    return (
        _valid_bounded_text(value, maximum=MAX_ACTION_ID_LENGTH)
        and _ACTION_ID.fullmatch(value) is not None
    )


@dataclass(frozen=True, slots=True)
class NavigationCandidate:
    work_key: WorkKey
    source_generation: int
    action_id: str
    target_kind: str
    target_value: str
    source_freshness: SourceFreshness
    navigation_authority: bool

    def __post_init__(self) -> None:
        if not (
            type(self.work_key) is WorkKey
            and _valid_generation(self.source_generation)
            and _valid_action_id(self.action_id)
            and _valid_bounded_text(self.target_kind, maximum=32)
            and _valid_bounded_text(
                self.target_value,
                maximum=MAX_CANDIDATE_TARGET_LENGTH,
            )
            and type(self.source_freshness) is SourceFreshness
            and type(self.navigation_authority) is bool
        ):
            raise ValueError("invalid navigation candidate")


@dataclass(frozen=True, slots=True)
class NavigationResolution:
    kind: NavigationResolutionKind
    work_key: WorkKey
    action_id: str
    target_kind: str | None
    target_value: str | None
    source_generation: int | None
    reason: str | None

    def __post_init__(self) -> None:
        generation_valid = self.source_generation is None or _valid_generation(
            self.source_generation
        )
        if not (
            type(self.kind) is NavigationResolutionKind
            and type(self.work_key) is WorkKey
            and _valid_action_id(self.action_id)
            and generation_valid
        ):
            raise ValueError("invalid navigation resolution")
        if self.kind is NavigationResolutionKind.READY:
            if not (
                self.target_kind in {"url", "terminal"}
                and _valid_bounded_text(
                    self.target_value,
                    maximum=MAX_EXECUTABLE_TARGET_LENGTH,
                )
                and self.source_generation is not None
                and self.reason is None
                and navigation_target_allowed(
                    self.work_key,
                    self.target_kind,
                    self.target_value,
                )
            ):
                raise ValueError("invalid ready navigation resolution")
            return
        if not (
            self.target_kind is None
            and self.target_value is None
            and self.reason in _REFUSAL_REASONS
        ):
            raise ValueError("invalid refused navigation resolution")


@dataclass(frozen=True, slots=True)
class OperatorActionDescriptor:
    kind: OperatorActionKind
    title: str
    enabled: bool
    disabled_reason: str | None
    key_equivalent: str

    def __post_init__(self) -> None:
        if not (
            type(self.kind) is OperatorActionKind
            and _valid_bounded_text(self.title, maximum=64)
            and type(self.enabled) is bool
            and type(self.key_equivalent) is str
            and len(self.key_equivalent) <= 1
            and self.key_equivalent.isprintable()
            and (
                (self.enabled and self.disabled_reason is None)
                or (
                    not self.enabled
                    and self.disabled_reason in _REFUSAL_REASONS
                )
            )
        ):
            raise ValueError("invalid operator action descriptor")


@dataclass(frozen=True, slots=True)
class OperatorLocalActionState:
    watched: bool
    pinned: bool
    snoozed: bool
    acknowledged: bool
    pin_position: int | None
    pin_count: int

    def __post_init__(self) -> None:
        booleans_valid = all(
            type(value) is bool
            for value in (
                self.watched,
                self.pinned,
                self.snoozed,
                self.acknowledged,
            )
        )
        pin_count_valid = (
            type(self.pin_count) is int and 0 <= self.pin_count <= MAX_PIN_COUNT
        )
        if self.pinned:
            position_valid = (
                type(self.pin_position) is int
                and 1 <= self.pin_count
                and 0 <= self.pin_position < self.pin_count
            )
        else:
            position_valid = self.pin_position is None
        if not (booleans_valid and pin_count_valid and position_valid):
            raise ValueError("invalid local pin state")


def _valid_session_identifier(value: str) -> bool:
    return (
        1 <= len(value) <= MAX_SESSION_ID_LENGTH
        and value.isprintable()
        and "/" not in value
        and "\\" not in value
    )


def _valid_url_target(work_key: WorkKey, target: str) -> bool:
    provider = work_key.source_key.provider_id
    try:
        parsed = urlsplit(target)
    except ValueError:
        return False

    if provider == "codex":
        if not (
            parsed.scheme == "codex"
            and parsed.netloc == "threads"
            and parsed.query == ""
            and parsed.fragment == ""
            and parsed.path.startswith("/")
        ):
            return False
        encoded_session = parsed.path[1:]
        if not encoded_session or "/" in encoded_session:
            return False
        session_id = unquote(encoded_session)
        return _valid_session_identifier(session_id) and target == (
            f"codex://threads/{quote(session_id, safe='')}"
        )

    if provider == "claude" and target == "claude://":
        return True
    if provider != "claude" or not (
        parsed.scheme == "vscode"
        and parsed.netloc == "anthropic.claude-code"
        and parsed.path == "/open"
        and parsed.fragment == ""
    ):
        return False
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    if len(query) != 1 or query[0][0] != "session":
        return False
    session_id = query[0][1]
    return _valid_session_identifier(session_id) and target == (
        "vscode://anthropic.claude-code/open?"
        + urlencode({"session": session_id}, quote_via=quote)
    )


def _valid_terminal_target(work_key: WorkKey, target: str) -> bool:
    opener = _TERMINAL_OPENERS.get(work_key.source_key.provider_id)
    if opener is None:
        return False
    try:
        tokens = shlex.split(target, posix=True)
    except ValueError:
        return False
    if len(tokens) != 6:
        return False
    cwd = tokens[1]
    executable, resume_argument = opener
    session_id = tokens[5]
    if not (
        tokens[:1] == ["cd"]
        and tokens[2:5] == ["&&", executable, resume_argument]
        and 1 <= len(cwd) <= MAX_TERMINAL_CWD_LENGTH
        and cwd.isprintable()
        and PurePath(cwd).is_absolute()
        and _valid_session_identifier(session_id)
    ):
        return False
    canonical = (
        f"cd {shlex.quote(cwd)} && {executable} {resume_argument} "
        f"{shlex.quote(session_id)}"
    )
    return target == canonical


def navigation_target_allowed(
    work_key: WorkKey,
    target_kind: str,
    target_value: str,
) -> bool:
    """Return whether an executable target is exact, bounded, and allowlisted."""
    if not (
        type(work_key) is WorkKey
        and target_kind in {"url", "terminal"}
        and _valid_bounded_text(
            target_value,
            maximum=MAX_EXECUTABLE_TARGET_LENGTH,
        )
    ):
        return False
    if target_kind == "url":
        return _valid_url_target(work_key, target_value)
    return _valid_terminal_target(work_key, target_value)


def _refusal(
    kind: NavigationResolutionKind,
    work_key: WorkKey,
    action_id: str,
    reason: str,
    *,
    source_generation: int | None = None,
) -> NavigationResolution:
    return NavigationResolution(
        kind=kind,
        work_key=work_key,
        action_id=action_id,
        target_kind=None,
        target_value=None,
        source_generation=source_generation,
        reason=reason,
    )


def resolve_navigation(
    work_key: WorkKey,
    action_id: str,
    candidates: Iterable[NavigationCandidate],
    *,
    expected_source_generation: int | None = None,
) -> NavigationResolution:
    """Resolve exactly one current, fresh, authorized navigation candidate."""
    if type(work_key) is not WorkKey:
        raise ValueError("invalid navigation work key")
    if not _valid_action_id(action_id):
        raise ValueError("invalid navigation action id")
    if expected_source_generation is not None and not _valid_generation(
        expected_source_generation
    ):
        raise ValueError("invalid expected source generation")

    matches: list[NavigationCandidate] = []
    try:
        for index, candidate in enumerate(candidates):
            if index >= MAX_NAVIGATION_CANDIDATES:
                return _refusal(
                    NavigationResolutionKind.DISABLED,
                    work_key,
                    action_id,
                    _NOT_AVAILABLE,
                )
            if (
                type(candidate) is NavigationCandidate
                and candidate.work_key == work_key
                and candidate.action_id == action_id
            ):
                matches.append(candidate)
                if len(matches) > 1:
                    return _refusal(
                        NavigationResolutionKind.AMBIGUOUS,
                        work_key,
                        action_id,
                        _MULTIPLE_SOURCES,
                    )
    except (TypeError, ValueError):
        return _refusal(
            NavigationResolutionKind.DISABLED,
            work_key,
            action_id,
            _NOT_AVAILABLE,
        )

    if not matches:
        return _refusal(
            NavigationResolutionKind.MISSING,
            work_key,
            action_id,
            _NOT_AVAILABLE,
        )
    candidate = matches[0]
    if (
        expected_source_generation is not None
        and candidate.source_generation != expected_source_generation
    ):
        return _refusal(
            NavigationResolutionKind.STALE,
            work_key,
            action_id,
            _TARGET_CHANGED,
            source_generation=candidate.source_generation,
        )
    if candidate.source_freshness is not SourceFreshness.FRESH:
        return _refusal(
            NavigationResolutionKind.STALE,
            work_key,
            action_id,
            _SOURCE_STALE,
            source_generation=candidate.source_generation,
        )
    if not candidate.navigation_authority or not navigation_target_allowed(
        work_key,
        candidate.target_kind,
        candidate.target_value,
    ):
        return _refusal(
            NavigationResolutionKind.DISABLED,
            work_key,
            action_id,
            _NOT_AVAILABLE,
            source_generation=candidate.source_generation,
        )
    return NavigationResolution(
        kind=NavigationResolutionKind.READY,
        work_key=work_key,
        action_id=action_id,
        target_kind=candidate.target_kind,
        target_value=candidate.target_value,
        source_generation=candidate.source_generation,
        reason=None,
    )


def _descriptor(
    kind: OperatorActionKind,
    title: str,
    *,
    enabled: bool = True,
    disabled_reason: str | None = None,
) -> OperatorActionDescriptor:
    return OperatorActionDescriptor(
        kind=kind,
        title=title,
        enabled=enabled,
        disabled_reason=disabled_reason,
        key_equivalent="",
    )


def _request_is_actionable(request: CanonicalRequestTruth | None) -> bool:
    if request is None:
        return False
    if request.phase is RequestPhase.STALE_HOLD:
        return True
    return request.phase in {
        RequestPhase.LIVE_UNACKNOWLEDGED,
        RequestPhase.LIVE_ACKNOWLEDGED,
    } and request.acknowledgement_eligibility in {
        AcknowledgementEligibility.ELIGIBLE,
        AcknowledgementEligibility.ALREADY_ACKNOWLEDGED,
    }


def build_operator_actions(
    *,
    work: CanonicalWorkTruth,
    request: CanonicalRequestTruth | None,
    local: OperatorLocalActionState,
    navigation: NavigationResolution,
) -> tuple[OperatorActionDescriptor, ...]:
    """Build the shared shallow operator action list in stable product order."""
    if type(work) is not CanonicalWorkTruth:
        raise ValueError("invalid work truth")
    if type(local) is not OperatorLocalActionState:
        raise ValueError("invalid local action state")
    if type(navigation) is not NavigationResolution or navigation.work_key != work.key:
        raise ValueError("navigation does not match work")
    if request is not None and (
        type(request) is not CanonicalRequestTruth or request.key.work_key != work.key
    ):
        raise ValueError("request does not match work")

    actions = [
        _descriptor(
            OperatorActionKind.OPEN,
            "Open",
            enabled=navigation.kind is NavigationResolutionKind.READY,
            disabled_reason=navigation.reason,
        )
    ]

    if request is not None:
        acknowledged = local.acknowledged or (
            request.phase is RequestPhase.LIVE_ACKNOWLEDGED
            and request.acknowledgement_eligibility
            is AcknowledgementEligibility.ALREADY_ACKNOWLEDGED
        )
        if acknowledged and request.phase in {
            RequestPhase.LIVE_UNACKNOWLEDGED,
            RequestPhase.LIVE_ACKNOWLEDGED,
            RequestPhase.STALE_HOLD,
        }:
            actions.append(
                _descriptor(
                    OperatorActionKind.RESUME_ESCALATION,
                    "Resume Escalation",
                )
            )
        elif (
            request.phase is RequestPhase.LIVE_UNACKNOWLEDGED
            and request.acknowledgement_eligibility
            is AcknowledgementEligibility.ELIGIBLE
        ):
            actions.append(_descriptor(OperatorActionKind.ACKNOWLEDGE, "I'm on It"))

    actions.append(
        _descriptor(
            OperatorActionKind.UNWATCH if local.watched else OperatorActionKind.WATCH,
            "Unwatch" if local.watched else "Watch",
        )
    )
    actions.append(
        _descriptor(
            OperatorActionKind.UNPIN if local.pinned else OperatorActionKind.PIN,
            "Unpin" if local.pinned else "Pin",
        )
    )
    if local.pinned and local.pin_position is not None:
        if local.pin_position > 0:
            actions.append(_descriptor(OperatorActionKind.MOVE_PIN_UP, "Move Up"))
        if local.pin_position < local.pin_count - 1:
            actions.append(_descriptor(OperatorActionKind.MOVE_PIN_DOWN, "Move Down"))

    if not _request_is_actionable(request):
        actions.append(
            _descriptor(
                (
                    OperatorActionKind.UNSNOOZE
                    if local.snoozed
                    else OperatorActionKind.SNOOZE
                ),
                "Unsnooze" if local.snoozed else "Snooze",
            )
        )
    return tuple(actions)
