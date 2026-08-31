from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from sidepulse.announcer_content import project_announcer_content
from sidepulse.attention import LifecycleMode, ProjectedAgentRow
from sidepulse.models import AgentMode, AgentStatus


def _row(
    agent_id: str,
    *,
    provider: str = "codex",
    display_name: str = "Codex",
    message: str | None = "Approve access?",
) -> ProjectedAgentRow:
    updated_at = datetime(2026, 8, 29, tzinfo=timezone.utc)
    status = AgentStatus(
        provider=provider,
        agent_id=agent_id,
        display_name=display_name,
        mode=AgentMode.WAITING_FOR_INPUT,
        updated_at=updated_at,
        event_name="PermissionRequest",
        message=message,
    )
    return ProjectedAgentRow(
        agent_id=agent_id,
        provider=provider,
        display_name=display_name,
        lifecycle_mode=LifecycleMode.WAITING,
        actionable=True,
        is_subagent=False,
        updated_at=updated_at,
        source_status=status,
    )


def test_no_actionable_rows_produce_no_announcer_content() -> None:
    assert project_announcer_content(()) is None


def test_primary_words_keep_current_normalization_and_caps() -> None:
    content = project_announcer_content(
        (
            _row(
                "codex:session:primary",
                display_name=f"  {'N' * 44}  ignored  ",
                message=f"  {'Q' * 85}  ignored  ",
            ),
        )
    )

    assert content is not None
    assert content.text == f"{'N' * 40} — {'Q' * 80}"
    assert content.total_actionable_count == 1
    assert content.primary_agent_id == "codex:session:primary"


def test_provider_name_and_needs_you_are_the_primary_fallbacks() -> None:
    content = project_announcer_content(
        (
            _row(
                "claude:session:primary",
                provider="claude",
                display_name="  ",
                message="  ",
            ),
        )
    )

    assert content is not None
    assert content.text == "Claude needs you"


def test_additional_actionable_rows_are_disclosed_in_the_pill() -> None:
    content = project_announcer_content(
        (
            _row("codex:session:primary", display_name="Codex", message="Approve access?"),
            _row("claude:session:second"),
            _row("devin:session:third"),
        )
    )

    assert content is not None
    assert content.text == "Codex — Approve access? · 2 more asks"
    assert content.total_actionable_count == 3
    assert content.primary_agent_id == "codex:session:primary"
    with pytest.raises(FrozenInstanceError):
        content.text = "mutated"  # type: ignore[misc]


def test_large_actionable_counts_stay_bounded_without_hiding_overflow() -> None:
    rows = tuple(
        _row(
            f"codex:session:{index}",
            display_name="N" * 50,
            message="Q" * 100,
        )
        for index in range(102)
    )

    content = project_announcer_content(rows)

    assert content is not None
    assert content.total_actionable_count == 102
    assert content.primary_agent_id == "codex:session:0"
    assert content.text.endswith(" · 99+ more asks")
    assert len(content.text) <= 140
    assert "\n" not in content.text


def test_long_fallback_provider_cannot_push_overflow_disclosure_out_of_view() -> None:
    content = project_announcer_content(
        (
            _row(
                "provider:session:primary",
                provider="provider" * 30,
                display_name=" ",
                message="Question?",
            ),
            _row("codex:session:second"),
        )
    )

    assert content is not None
    assert content.text.endswith(" · 1 more ask")
    assert len(content.text) <= 140
