"""Two agents must look like two agents.

The single-color glance path was winning unconditionally in
``_hardware_write_task``, so the multi-agent renderer -- and with it
every blend mode the user can choose -- was unreachable in production:
a Codex session and a Claude session working at the same time lit the
whole strip one color.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sidepulse.attention import AttentionProjection, LifecycleMode, ProjectedAgentRow
from sidepulse.presentation_policy import (
    GlanceOverrideReason,
    GlanceSemantic,
    ResolvedGlance,
    SemanticGlyph,
)
from sidepulse.status_bar import StatusBarController


def _row(agent_id: str, provider: str) -> ProjectedAgentRow:
    return ProjectedAgentRow(
        agent_id=agent_id,
        provider=provider,
        display_name=agent_id,
        lifecycle_mode=LifecycleMode.ACTIVE,
        actionable=False,
        is_subagent=False,
        updated_at=datetime.now(timezone.utc),
        source_status=None,
    )


def _projection(*rows: ProjectedAgentRow) -> AttentionProjection:
    return AttentionProjection(
        lifecycle_mode=LifecycleMode.ACTIVE,
        actionable_attention=(),
        visible_rows=tuple(rows),
        transient_signals=(),
        dominant_provider=rows[0].provider if rows else None,
        click_target_agent_id=None,
    )


def _glance(semantic: GlanceSemantic) -> ResolvedGlance:
    return ResolvedGlance(
        semantic=semantic,
        glyph=SemanticGlyph.FULL_ANCHOR,
        cue=None,
        override_reason=GlanceOverrideReason.NONE,
        relay_epoch=0.0,
        next_visual_change_at=None,
    )


def _decide(glance, projection) -> bool:
    return StatusBarController.should_render_multi_agent(None, glance, projection)


def test_two_active_agents_render_as_a_crowd() -> None:
    projection = _projection(_row("codex:session:a", "codex"), _row("claude:session:b", "claude"))
    assert _decide(_glance(GlanceSemantic.ACTIVE), projection) is True


def test_a_single_agent_keeps_the_single_color_path() -> None:
    projection = _projection(_row("codex:session:a", "codex"))
    assert _decide(_glance(GlanceSemantic.ACTIVE), projection) is False


def test_whole_strip_moments_are_never_buried_under_a_crowd() -> None:
    """Attention, failures, completions, capacity and rest are designed
    as one unmistakable signal -- a crowd of colors would hide them."""
    projection = _projection(_row("codex:session:a", "codex"), _row("claude:session:b", "claude"))
    for semantic in (
        GlanceSemantic.ATTENTION,
        GlanceSemantic.FRESH_FAILURE,
        GlanceSemantic.FRESH_COMPLETION,
        GlanceSemantic.UNRESOLVED_FAILURE,
        GlanceSemantic.CAPACITY,
        GlanceSemantic.REST,
    ):
        assert _decide(_glance(semantic), projection) is False, semantic


def test_no_projection_falls_back() -> None:
    assert _decide(_glance(GlanceSemantic.ACTIVE), None) is False
