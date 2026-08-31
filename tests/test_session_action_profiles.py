from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.provider_facts import WorkIdentifier, WorkKey
from sidepulse.provider_feature_settings import (
    ProviderInstanceSessionActionPolicy,
    ProviderInstanceSessionActionProjection,
)
from sidepulse.session_actions import (
    ProfileSessionActionResolutionKind,
    resolve_profile_session_action,
    resolve_profile_session_action_for_status,
)


def _projection(
    *rows: tuple[str, str, str],
) -> ProviderInstanceSessionActionProjection:
    return ProviderInstanceSessionActionProjection(
        tuple(
            ProviderInstanceSessionActionPolicy(
                provider_id=provider_id,
                source_instance_id=source_instance_id,
                open_session_action=action,
            )
            for provider_id, source_instance_id, action in rows
        )
    )


def _status(
    provider_id: str = "claude",
    source_instance_id: str | None = "work",
) -> AgentStatus:
    work_key = None
    if source_instance_id is not None:
        work_key = WorkKey(
            SourceKey(
                provider_id=provider_id,
                adapter_id="hook",
                source_instance_id=source_instance_id,
                capability_id="sessions",
            ),
            WorkIdentifier("work:one"),
        )
    return AgentStatus(
        provider=provider_id,
        agent_id=f"{provider_id}:session:one",
        display_name="Agent one",
        mode=AgentMode.WORKING,
        updated_at=datetime.now(timezone.utc),
        event_name="PreToolUse",
        session_id="one",
        work_key=work_key,
    )


def test_nondefault_exact_profile_action_is_an_explicit_override() -> None:
    projection = _projection(
        ("claude", "default", "app"),
        ("claude", "work", "terminal"),
    )

    resolution = resolve_profile_session_action(projection, "claude", "work")

    assert resolution.kind is ProfileSessionActionResolutionKind.PROFILE_OVERRIDE
    assert resolution.action == "terminal"
    assert resolution.identity == ("claude", "work")
    assert resolution.has_override is True


def test_default_instance_explicitly_preserves_legacy_resolution() -> None:
    projection = _projection(("claude", "default", "terminal"))

    resolution = resolve_profile_session_action(projection, "claude", "default")

    assert resolution.kind is ProfileSessionActionResolutionKind.LEGACY_DEFAULT
    assert resolution.action is None
    assert resolution.identity == ("claude", "default")
    assert resolution.has_override is False


def test_status_resolution_uses_its_exact_work_source_identity() -> None:
    projection = _projection(
        ("claude", "personal", "app"),
        ("claude", "work", "vscode"),
    )

    resolution = resolve_profile_session_action_for_status(
        projection,
        _status("claude", "work"),
    )

    assert resolution.kind is ProfileSessionActionResolutionKind.PROFILE_OVERRIDE
    assert resolution.action == "vscode"
    assert resolution.identity == ("claude", "work")


def test_status_without_a_work_identity_fails_safe_to_legacy_behavior() -> None:
    resolution = resolve_profile_session_action_for_status(
        _projection(("claude", "work", "terminal")),
        _status("claude", None),
    )

    assert resolution.kind is ProfileSessionActionResolutionKind.MISSING_IDENTITY
    assert resolution.action is None
    assert resolution.identity is None


def test_status_provider_mismatch_never_applies_another_profile() -> None:
    status = _status("claude", "work")
    object.__setattr__(status, "provider", "codex")

    resolution = resolve_profile_session_action_for_status(
        _projection(("claude", "work", "terminal")),
        status,
    )

    assert resolution.kind is ProfileSessionActionResolutionKind.INVALID_IDENTITY
    assert resolution.action is None


def test_untyped_status_identity_fails_safe_instead_of_reading_attributes() -> None:
    resolution = resolve_profile_session_action_for_status(
        _projection(("claude", "work", "terminal")),
        object(),  # type: ignore[arg-type]
    )

    assert resolution.kind is ProfileSessionActionResolutionKind.INVALID_IDENTITY
    assert resolution.action is None


def test_unknown_or_invalid_explicit_identity_fails_safe_without_fallback() -> None:
    projection = _projection(("claude", "work", "terminal"))

    unknown = resolve_profile_session_action(projection, "claude", "personal")
    invalid = resolve_profile_session_action(projection, "claude", "../work")

    assert unknown.kind is ProfileSessionActionResolutionKind.UNKNOWN_INSTANCE
    assert unknown.action is None
    assert unknown.identity == ("claude", "personal")
    assert invalid.kind is ProfileSessionActionResolutionKind.INVALID_IDENTITY
    assert invalid.action is None
    assert invalid.identity is None


def test_resolver_rejects_an_untyped_projection_boundary() -> None:
    with pytest.raises(TypeError, match="ProviderInstanceSessionActionProjection"):
        resolve_profile_session_action(object(), "claude", "work")  # type: ignore[arg-type]
