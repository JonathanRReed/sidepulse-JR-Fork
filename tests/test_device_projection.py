from __future__ import annotations

from datetime import datetime, timezone

from sidepulse.attention import AttentionProjection, LifecycleMode, ProjectedAgentRow
from sidepulse.device_projection import light_rows_for_provider, projection_for_provider

_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _row(
    agent_id: str,
    provider: str,
    lifecycle: LifecycleMode = LifecycleMode.ACTIVE,
    *,
    worker: bool = False,
) -> ProjectedAgentRow:
    return ProjectedAgentRow(
        agent_id=agent_id,
        provider=provider,
        display_name=agent_id,
        lifecycle_mode=lifecycle,
        actionable=False,
        is_subagent=worker,
        updated_at=_NOW,
        source_status=None,
    )


def _projection() -> AttentionProjection:
    return AttentionProjection(
        lifecycle_mode=LifecycleMode.ACTIVE,
        actionable_attention=(),
        visible_rows=(
            _row("codex:main", "codex"),
            _row("claude:main", "claude", LifecycleMode.WAITING),
        ),
        worker_rows=(
            _row("codex:worker", "codex", worker=True),
            _row("claude:worker", "claude", worker=True),
        ),
        transient_signals=(),
        dominant_provider="codex",
        click_target_agent_id="codex:main",
    )


def test_provider_pin_filters_main_and_worker_rows() -> None:
    projection = projection_for_provider(_projection(), "claude")

    assert tuple(row.agent_id for row in projection.visible_rows) == ("claude:main",)
    assert tuple(row.agent_id for row in projection.worker_rows) == ("claude:worker",)
    assert projection.lifecycle_mode is LifecycleMode.WAITING
    assert projection.dominant_provider == "claude"
    assert projection.click_target_agent_id is None


def test_unpinned_projection_preserves_worker_rows() -> None:
    original = _projection()
    projection = projection_for_provider(original, None)

    assert projection.visible_rows == original.visible_rows
    assert projection.worker_rows == original.worker_rows
    assert projection.lifecycle_mode is LifecycleMode.WAITING


def test_actionable_attention_bypasses_provider_pin() -> None:
    row = _row("codex:ask", "codex", LifecycleMode.WAITING)
    original = AttentionProjection(
        lifecycle_mode=LifecycleMode.WAITING,
        actionable_attention=(row,),
        visible_rows=(row,),
        transient_signals=(),
        dominant_provider="codex",
        click_target_agent_id=row.agent_id,
    )

    assert projection_for_provider(original, "claude") is original


def test_pin_without_matching_rows_returns_idle_projection() -> None:
    projection = projection_for_provider(_projection(), "gemini")

    assert projection.lifecycle_mode is LifecycleMode.IDLE
    assert projection.visible_rows == ()
    assert projection.worker_rows == ()
    assert projection.dominant_provider is None
    assert projection.click_target_agent_id is None


def test_light_rows_uses_orphaned_worker_standin_before_filtering() -> None:
    projection = AttentionProjection(
        lifecycle_mode=LifecycleMode.ACTIVE,
        actionable_attention=(),
        visible_rows=(),
        worker_rows=(
            _row("codex:worker", "codex", worker=True),
            _row("claude:worker", "claude", LifecycleMode.WAITING, worker=True),
        ),
        transient_signals=(),
        dominant_provider=None,
        click_target_agent_id=None,
    )

    assert tuple(row.agent_id for row in light_rows_for_provider(projection, "claude")) == (
        "claude:worker",
    )
