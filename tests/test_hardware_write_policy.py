from __future__ import annotations

from sidepulse.hardware_write_policy import hardware_write_policy
from sidepulse.presentation_policy import (
    FiniteCue,
    GlanceOverrideReason,
    GlanceSemantic,
    ResolvedGlance,
    SemanticGlyph,
)
from sidepulse.runtime_scheduler import RuntimeWorkPriority


def _glance(
    semantic: GlanceSemantic,
    *,
    cue: FiniteCue | None = None,
) -> ResolvedGlance:
    return ResolvedGlance(
        semantic=semantic,
        glyph=SemanticGlyph.FULL_ANCHOR,
        cue=cue,
        override_reason=GlanceOverrideReason.NONE,
        relay_epoch=100.0,
        next_visual_change_at=None,
    )


def test_ordinary_agent_frames_share_one_latest_slot() -> None:
    policy = hardware_write_policy("agent", _glance(GlanceSemantic.ACTIVE))

    assert policy.priority is RuntimeWorkPriority.COALESCIBLE
    assert policy.coalesce_identity == "latest"


def test_persistent_attention_and_failure_use_protected_semantic_slots() -> None:
    attention = hardware_write_policy(
        "agent",
        _glance(GlanceSemantic.ATTENTION),
    )
    failure = hardware_write_policy("failure", None)

    assert attention.priority is RuntimeWorkPriority.URGENT
    assert attention.coalesce_identity == "semantic-attention"
    assert failure.priority is RuntimeWorkPriority.URGENT
    assert failure.coalesce_identity == "signal-failure"


def test_finite_cue_slot_is_stable_and_contains_no_event_text() -> None:
    event_key = "provider:private-session-name"
    first = hardware_write_policy(
        "agent",
        _glance(
            GlanceSemantic.FRESH_COMPLETION,
            cue=FiniteCue(event_key, GlanceSemantic.FRESH_COMPLETION, 1, 0.5),
        ),
    )
    repeated = hardware_write_policy(
        "agent",
        _glance(
            GlanceSemantic.FRESH_COMPLETION,
            cue=FiniteCue(event_key, GlanceSemantic.FRESH_COMPLETION, 1, 0.5),
        ),
    )
    other = hardware_write_policy(
        "agent",
        _glance(
            GlanceSemantic.FRESH_COMPLETION,
            cue=FiniteCue("provider:other", GlanceSemantic.FRESH_COMPLETION, 1, 0.5),
        ),
    )

    assert first.priority is RuntimeWorkPriority.IMPORTANT
    assert first.coalesce_identity == repeated.coalesce_identity
    assert first.coalesce_identity != other.coalesce_identity
    assert event_key not in first.coalesce_identity
    assert first.coalesce_identity.startswith("cue-")


def test_explicit_signal_preview_outranks_routine_signals() -> None:
    preview = hardware_write_policy("signal_test", None)
    completion = hardware_write_policy("completion", None)

    assert preview.priority is RuntimeWorkPriority.EXPLICIT
    assert preview.coalesce_identity == "preview-signal-test"
    assert completion.priority is RuntimeWorkPriority.IMPORTANT
    assert completion.coalesce_identity == "signal-completion"
