from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath
from urllib.parse import quote, urlencode

from .models import AgentStatus
from .navigation_policy import (
    NavigationResolution,
    NavigationResolutionKind,
    navigation_target_allowed,
)
from .provider_facts import WorkKey
from .provider_feature_settings import ProviderInstanceSessionActionProjection
from .provider_instances import (
    DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
    ProviderInstanceError,
    ProviderInstanceKey,
)
from .providers import HOOK_PROVIDERS

SESSION_OPEN_APP = "app"
SESSION_OPEN_TERMINAL = "terminal"
SESSION_OPEN_VSCODE = "vscode"
SESSION_OPEN_CHOICES = (SESSION_OPEN_APP, SESSION_OPEN_TERMINAL, SESSION_OPEN_VSCODE)
SESSION_OPEN_APP_SURFACES = ("app", "ui", "transcript")
SESSION_OPEN_TERMINAL_SURFACES = ("cli", "terminal", "command line")
SESSION_OPEN_VSCODE_SURFACES = ("vscode", "vs code", "visual studio code")
SESSION_TERMINAL_OPENERS = {
    "codex": ("codex", "resume"),
    "claude": ("claude", "--resume"),
    "devin": ("devin", "--resume"),
    "grok": ("grok", "--resume"),
    "cursor": ("cursor-agent", "--resume"),
    "hermes": ("hermes", "--resume"),
}
MAX_SESSION_ID_LENGTH = 256
MAX_SESSION_CWD_LENGTH = 1_024


class ProfileSessionActionResolutionKind(str, Enum):
    """Why a provider profile did or did not override legacy session routing."""

    PROFILE_OVERRIDE = "profile_override"
    LEGACY_DEFAULT = "legacy_default"
    MISSING_IDENTITY = "missing_identity"
    INVALID_IDENTITY = "invalid_identity"
    UNKNOWN_INSTANCE = "unknown_instance"


@dataclass(frozen=True, slots=True)
class ProfileSessionActionResolution:
    """Pure exact-instance result consumed before the legacy action ladder."""

    kind: ProfileSessionActionResolutionKind
    action: str | None = None
    provider_id: str | None = None
    source_instance_id: str | None = None

    def __post_init__(self) -> None:
        has_identity = self.provider_id is not None and self.source_instance_id is not None
        if (
            type(self.kind) is not ProfileSessionActionResolutionKind
            or (self.provider_id is None) != (self.source_instance_id is None)
            or (has_identity and not all(isinstance(value, str) for value in self.identity or ()))
            or (
                self.kind is ProfileSessionActionResolutionKind.PROFILE_OVERRIDE
                and self.action not in SESSION_OPEN_CHOICES
            )
            or (
                self.kind is not ProfileSessionActionResolutionKind.PROFILE_OVERRIDE
                and self.action is not None
            )
        ):
            raise ValueError("invalid profile session action resolution")

    @property
    def identity(self) -> tuple[str, str] | None:
        if self.provider_id is None or self.source_instance_id is None:
            return None
        return self.provider_id, self.source_instance_id

    @property
    def has_override(self) -> bool:
        return self.kind is ProfileSessionActionResolutionKind.PROFILE_OVERRIDE


def resolve_profile_session_action(
    projection: ProviderInstanceSessionActionProjection,
    provider_id: object,
    source_instance_id: object,
) -> ProfileSessionActionResolution:
    """Resolve only an exact nondefault profile, never a provider fallback."""

    if type(projection) is not ProviderInstanceSessionActionProjection:
        raise TypeError("expected ProviderInstanceSessionActionProjection")
    if type(provider_id) is not str or type(source_instance_id) is not str:
        return ProfileSessionActionResolution(
            ProfileSessionActionResolutionKind.INVALID_IDENTITY
        )
    try:
        identity = ProviderInstanceKey(provider_id, source_instance_id).value
    except ProviderInstanceError:
        return ProfileSessionActionResolution(
            ProfileSessionActionResolutionKind.INVALID_IDENTITY
        )
    if identity[1] == DEFAULT_PROVIDER_INSTANCE_SOURCE_ID:
        return ProfileSessionActionResolution(
            ProfileSessionActionResolutionKind.LEGACY_DEFAULT,
            provider_id=identity[0],
            source_instance_id=identity[1],
        )
    try:
        policy = projection.provider(*identity)
    except StopIteration:
        return ProfileSessionActionResolution(
            ProfileSessionActionResolutionKind.UNKNOWN_INSTANCE,
            provider_id=identity[0],
            source_instance_id=identity[1],
        )
    return ProfileSessionActionResolution(
        ProfileSessionActionResolutionKind.PROFILE_OVERRIDE,
        action=policy.open_session_action,
        provider_id=identity[0],
        source_instance_id=identity[1],
    )


def resolve_profile_session_action_for_status(
    projection: ProviderInstanceSessionActionProjection,
    status: AgentStatus | None,
) -> ProfileSessionActionResolution:
    """Resolve from canonical work identity, failing closed to legacy behavior."""

    if type(projection) is not ProviderInstanceSessionActionProjection:
        raise TypeError("expected ProviderInstanceSessionActionProjection")
    if status is None:
        return ProfileSessionActionResolution(
            ProfileSessionActionResolutionKind.MISSING_IDENTITY
        )
    if type(status) is not AgentStatus:
        return ProfileSessionActionResolution(
            ProfileSessionActionResolutionKind.INVALID_IDENTITY
        )
    if status.work_key is None:
        return ProfileSessionActionResolution(
            ProfileSessionActionResolutionKind.MISSING_IDENTITY
        )
    if type(status.work_key) is not WorkKey:
        return ProfileSessionActionResolution(
            ProfileSessionActionResolutionKind.INVALID_IDENTITY
        )
    source_key = status.work_key.source_key
    if type(status.provider) is not str or status.provider != source_key.provider_id:
        return ProfileSessionActionResolution(
            ProfileSessionActionResolutionKind.INVALID_IDENTITY
        )
    return resolve_profile_session_action(
        projection,
        source_key.provider_id,
        source_key.source_instance_id,
    )


def _valid_session_id(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_SESSION_ID_LENGTH
        and value.isprintable()
        and "/" not in value
        and "\\" not in value
    )


def _valid_session_cwd(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_SESSION_CWD_LENGTH
        and value.isprintable()
        and PurePath(value).is_absolute()
    )


def session_deep_link(status: AgentStatus) -> str | None:
    provider = status.provider.lower()
    session_id = status.session_id

    if provider == "codex" and _valid_session_id(session_id):
        return f"codex://threads/{quote(session_id, safe='')}"
    if provider == "claude":
        return "claude://"
    return None


def session_vscode_link(status: AgentStatus) -> str | None:
    if status.provider.lower() != "claude" or not _valid_session_id(status.session_id):
        return None
    return "vscode://anthropic.claude-code/open?" + urlencode(
        {"session": status.session_id},
        quote_via=quote,
    )


def session_resume_command(status: AgentStatus) -> str | None:
    if not _valid_session_id(status.session_id) or not _valid_session_cwd(status.cwd):
        return None

    provider = status.provider.lower()
    opener = SESSION_TERMINAL_OPENERS.get(provider)
    if opener is None:
        return None
    cwd = shlex.quote(status.cwd)
    session_id = shlex.quote(status.session_id)
    executable, resume_argument = opener
    return f"cd {cwd} && {executable} {resume_argument} {session_id}"


def provider_session_opener_providers() -> tuple[str, ...]:
    return HOOK_PROVIDERS


def default_session_open_action(status: AgentStatus) -> str:
    for action in preferred_session_open_actions(status):
        if session_open_target(status, action):
            return action
    return SESSION_OPEN_TERMINAL


def preferred_session_open_actions(status: AgentStatus) -> tuple[str, ...]:
    origin = normalized_origin(status.origin)
    if origin:
        if any(surface in origin for surface in SESSION_OPEN_VSCODE_SURFACES):
            return (SESSION_OPEN_VSCODE, SESSION_OPEN_APP, SESSION_OPEN_TERMINAL)
        if any(surface in origin for surface in SESSION_OPEN_TERMINAL_SURFACES):
            return (SESSION_OPEN_TERMINAL, SESSION_OPEN_APP, SESSION_OPEN_VSCODE)
        if any(surface in origin for surface in SESSION_OPEN_APP_SURFACES):
            return (SESSION_OPEN_APP, SESSION_OPEN_VSCODE, SESSION_OPEN_TERMINAL)
        if "cursor" in origin or "windsurf" in origin:
            return (SESSION_OPEN_APP, SESSION_OPEN_TERMINAL, SESSION_OPEN_VSCODE)

    if status.provider.lower() == "claude":
        return (SESSION_OPEN_VSCODE, SESSION_OPEN_APP, SESSION_OPEN_TERMINAL)
    return (SESSION_OPEN_APP, SESSION_OPEN_TERMINAL, SESSION_OPEN_VSCODE)


def normalized_origin(origin: str | None) -> str:
    return " ".join(str(origin or "").strip().lower().replace("-", " ").split())


def session_open_target(status: AgentStatus, action: str) -> tuple[str, str] | None:
    if action == SESSION_OPEN_APP:
        url = session_deep_link(status)
        return ("url", url) if url else None
    if action == SESSION_OPEN_VSCODE:
        url = session_vscode_link(status)
        return ("url", url) if url else None
    if action == SESSION_OPEN_TERMINAL:
        command = session_resume_command(status)
        return ("terminal", command) if command else None
    return None


def available_session_open_actions(status: AgentStatus) -> tuple[str, ...]:
    return tuple(action for action in SESSION_OPEN_CHOICES if session_open_target(status, action))


def session_open_action_label(status: AgentStatus, action: str) -> str:
    provider = status.provider.lower()
    if action == SESSION_OPEN_APP:
        if provider == "codex":
            return "Open in Codex"
        if provider == "claude":
            return "Open Claude App"
        return "Open App"
    if action == SESSION_OPEN_VSCODE:
        return "Open in VS Code"
    if action == SESSION_OPEN_TERMINAL:
        return "Resume in Terminal"
    return action


def activate_navigation_resolution(
    resolution: NavigationResolution,
    *,
    open_url: Callable[[str], None],
    open_terminal_command: Callable[[str], None],
) -> bool:
    """Activate only a ready resolution whose target still passes its allowlist."""
    if not (
        type(resolution) is NavigationResolution
        and resolution.kind is NavigationResolutionKind.READY
        and resolution.target_kind is not None
        and resolution.target_value is not None
        and navigation_target_allowed(
            resolution.work_key,
            resolution.target_kind,
            resolution.target_value,
        )
    ):
        return False
    if resolution.target_kind == "url":
        open_url(resolution.target_value)
        return True
    if resolution.target_kind == "terminal":
        open_terminal_command(resolution.target_value)
        return True
    return False
