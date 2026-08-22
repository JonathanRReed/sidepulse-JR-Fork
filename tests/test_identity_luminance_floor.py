"""Identity colors must be colors the strip can actually show.

Devin's shipped navy #1D3461 sat at relative luminance 0.036 -- one
eighth of claude/codex/grok -- and drove a peak strip byte of 30/255.
Confirmed live: Devin was ON the strip in a three-agent Round-Robin
(LED 4, drive #01020E) while the user reported "Devin isn't interacting
with the light bulbs at all." An identity color that dark silently
disables the feature it configures, so identity colors are floored at
render time and the shipped navy itself was lifted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sidepulse.collector import AgentMode, AgentStatus
from sidepulse.colors import (
    IDENTITY_LUMINANCE_FLOOR,
    PROVIDER_BRAND_COLORS,
    ColorSettings,
    _active_agents,
    default_agent_color,
    hex_to_oklch,
    readable_identity_hex,
    relative_luminance,
)


def _status(provider: str, mode: AgentMode = AgentMode.WORKING) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=provider,
        display_name=provider.title(),
        mode=mode,
        updated_at=datetime.now(timezone.utc),
        event_name="Test",
    )


def test_every_shipped_brand_default_clears_the_floor() -> None:
    for provider in PROVIDER_BRAND_COLORS:
        color = default_agent_color(provider)
        assert relative_luminance(color) >= IDENTITY_LUMINANCE_FLOOR, (
            f"{provider} ships an identity color the strip cannot show: {color}"
        )


def test_devin_default_is_the_lifted_navy_not_the_invisible_one() -> None:
    assert default_agent_color("devin") == "#5C84B0"
    # Character preserved: still clearly darker than the bright brands.
    assert relative_luminance("#5C84B0") < relative_luminance(
        default_agent_color("codex")
    )


def test_dark_identity_is_lifted_with_its_hue_intact() -> None:
    lifted = readable_identity_hex("#1D3461")
    assert relative_luminance(lifted) >= IDENTITY_LUMINANCE_FLOOR * 0.98
    _, _, old_hue = hex_to_oklch("#1D3461")
    _, _, new_hue = hex_to_oklch(lifted)
    assert abs(old_hue - new_hue) < 8.0, "the lift changed the hue"


def test_bright_identities_pass_through_untouched() -> None:
    for color in ("#D97757", "#2B8FFF", "#8E8E93", "#FFFFFF"):
        assert readable_identity_hex(color) == color


def test_pure_black_stays_black() -> None:
    # No hue to preserve and no luminance to scale: an explicit black
    # pick stays an explicit "off," not an arbitrary invented gray.
    assert readable_identity_hex("#000000") == "#000000"


def test_a_custom_near_black_pick_still_lights_the_crowd_render() -> None:
    settings = ColorSettings.defaults().with_agent_color("devin", "#001423")
    statuses = (
        _status("codex"),
        _status("claude"),
        _status("devin"),
    )
    agents = _active_agents(statuses, settings)
    devin = next(agent for agent in agents if agent.provider == "devin")
    assert relative_luminance(devin.color) >= IDENTITY_LUMINANCE_FLOOR * 0.9


def test_solo_active_glyph_floors_a_dark_identity_but_rest_stays_dim() -> None:
    from sidepulse.accessibility_display import AccessibilityDisplayPreferences
    from sidepulse.presentation_policy import (
        GlanceOverrideReason,
        GlanceSemantic,
        ResolvedGlance,
        SemanticGlyph,
        compose_presentation_program,
    )

    def glance(semantic: GlanceSemantic) -> ResolvedGlance:
        return ResolvedGlance(
            semantic=semantic,
            glyph=SemanticGlyph.CENTER_PAIR,
            cue=None,
            override_reason=GlanceOverrideReason.NONE,
            relay_epoch=0.0,
            next_visual_change_at=None,
        )

    preferences = AccessibilityDisplayPreferences(
        reduce_motion=False,
        reduce_transparency=False,
        increase_contrast=False,
        differentiate_without_color=False,
    )
    active = compose_presentation_program(
        glance(GlanceSemantic.ACTIVE),
        presentation_time=100.0,
        led_count=8,
        color="#1D3461",
        preferences=preferences,
    )
    assert "#1D3461" not in active.dsl
    assert readable_identity_hex("#1D3461") in active.dsl

    rest = compose_presentation_program(
        glance(GlanceSemantic.REST),
        presentation_time=100.0,
        led_count=8,
        color="#1D3461",
        preferences=preferences,
    )
    # REST must not inherit the floor -- idle dim is deliberate.
    assert readable_identity_hex("#1D3461") not in rest.dsl


def test_a_persisted_snapshot_of_a_retired_default_tracks_the_repair() -> None:
    """The live install held devin: #1D3461 in settings -- written by an
    earlier default-snapshotting path, never chosen -- so fixing the
    brand table alone changed nothing there: agent_color reads settings
    before brands. Stored copies of RETIRED defaults migrate to the
    current default on load; anything else stays."""
    loaded = ColorSettings.from_dict(
        {
            "agent_colors": {
                "devin": "#1D3461",
                "cursor": "#FF2D55",
                "hermes": "#FFCC00",
                "claude": "#123456",
            }
        }
    )
    assert loaded.agent_color("devin") == default_agent_color("devin")
    assert loaded.agent_color("cursor") == default_agent_color("cursor")
    assert loaded.agent_color("hermes") == default_agent_color("hermes")
    # A hand-picked colour that never shipped as a default is untouched.
    assert loaded.agent_color("claude") == "#123456"
