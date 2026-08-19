"""Claude is Claude's colour, everywhere, and stays put.

Reported live: "the screen bar color and the color on the side aren't the
brand colours. It's purple for some reason when Claude's running."

There were two purples and neither came from the provider->colour
resolution, because on both surfaces that resolution was unreachable:

  * LED strip -- ``settings.agent_color(provider)`` is the THIRD term of
    an or-chain whose second term is populated whenever more than one row
    is visible. With 87 leaked sub-agents there was always more than one
    row, so the strip drew IDENTITY_PALETTE slots (#E44CFF magenta for
    the owner's live Claude session) and Claude's declared #D97757 never
    appeared at all.
  * Screen Bar -- ``color_for_resolved_glance`` had NO provider input of
    any kind. It painted ``mode_color`` and nothing else.

And a third defect underneath both: the provider->slot assignment keyed
on absolute registry position, so registering one new provider anywhere
but the end silently repainted four existing ones.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from sidepulse import colors as colors_module
from sidepulse.colors import (
    PROVIDER_BRAND_COLORS,
    ColorSettings,
    default_agent_color,
    hex_to_oklch,
    palette_defaults_by_provider,
    provider_identity_colors_for_agents,
)
from sidepulse.presentation_policy import (
    GlanceOverrideReason,
    GlanceSemantic,
    ResolvedGlance,
    SemanticGlyph,
)
from sidepulse.status_bar import color_for_resolved_glance


def _glance(semantic: GlanceSemantic) -> ResolvedGlance:
    return ResolvedGlance(
        semantic=semantic,
        glyph=SemanticGlyph.CENTER_PAIR,
        cue=None,
        override_reason=GlanceOverrideReason.NONE,
        relay_epoch=0.0,
        next_visual_change_at=None,
    )


# --- stability -------------------------------------------------------------


def test_registering_a_tenth_provider_moves_nobody(monkeypatch) -> None:
    """Identity that moves is not identity.

    Inserting one spec in the MIDDLE of the registry used to shift every
    brandless provider after it by one palette slot: cursor #FFCC00 ->
    #FF9500, hermes #FF9500 -> #FF2D55, openclaw #FF2D55 -> #AF52DE,
    opencode #AF52DE -> #FF3B30. Four agents change colour on a machine
    where the user has learned which light is which.
    """
    before = {
        spec.provider: default_agent_color(spec.provider)
        for spec in colors_module.PROVIDER_SPECS
    }
    assert len(before) == 10

    newcomer = replace(colors_module.PROVIDER_SPECS[0], provider="newcomer")
    specs = (
        *colors_module.PROVIDER_SPECS[:4],
        newcomer,
        *colors_module.PROVIDER_SPECS[4:],
    )
    monkeypatch.setattr(colors_module, "PROVIDER_SPECS", specs)
    monkeypatch.setattr(colors_module, "_PALETTE_DEFAULTS_BY_PROVIDER", None)

    after = {provider: default_agent_color(provider) for provider in before}

    assert after == before
    assert default_agent_color("newcomer") not in set(before.values())


def test_every_shipped_brandless_provider_has_a_pinned_slot() -> None:
    """A pin is the only thing that makes the guarantee above true.

    Left to derivation, the answer depends on who else is registered.
    """
    registered = {spec.provider for spec in colors_module.PROVIDER_SPECS}
    brandless = registered - set(PROVIDER_BRAND_COLORS)

    assert brandless <= set(colors_module.PROVIDER_PALETTE_SLOTS)
    assigned = palette_defaults_by_provider()
    assert len(set(assigned.values())) == len(assigned)
    assert not set(assigned.values()) & {
        hex_value.upper() for hex_value in PROVIDER_BRAND_COLORS.values()
    }


# --- brand-correctness -----------------------------------------------------


def test_a_crowd_of_providers_wears_its_own_brands() -> None:
    settings = ColorSettings.defaults()

    assignment = provider_identity_colors_for_agents(
        [
            ("claude:session:a", "claude"),
            ("codex:session:b", "codex"),
            ("devin:session:c", "devin"),
        ],
        colors=settings,
    )

    assert assignment["claude:session:a"] == PROVIDER_BRAND_COLORS["claude"]
    assert assignment["codex:session:b"] == PROVIDER_BRAND_COLORS["codex"]
    assert assignment["devin:session:c"] == PROVIDER_BRAND_COLORS["devin"]


def test_sessions_of_one_provider_differ_by_lightness_not_by_hue() -> None:
    settings = ColorSettings.defaults()
    ids = [(f"claude:session:{name}", "claude") for name in ("a", "b", "c", "d")]

    assignment = provider_identity_colors_for_agents(ids, colors=settings)

    assert len(set(assignment.values())) == len(ids)
    brand_lightness, _chroma, brand_hue = hex_to_oklch(
        PROVIDER_BRAND_COLORS["claude"]
    )
    lightnesses = []
    for colour in assignment.values():
        lightness, _c, hue = hex_to_oklch(colour)
        assert min(abs(hue - brand_hue), 360.0 - abs(hue - brand_hue)) < 6.0
        lightnesses.append(lightness)
    assert len(set(round(value, 3) for value in lightnesses)) == len(ids)
    assert any(abs(value - brand_lightness) < 1e-6 for value in lightnesses)


def test_the_screen_bar_paints_the_working_agents_brand() -> None:
    """The announcer had no provider input at all before this."""
    settings = ColorSettings.defaults()

    assert (
        color_for_resolved_glance(
            settings, _glance(GlanceSemantic.ACTIVE), provider="claude"
        )
        == PROVIDER_BRAND_COLORS["claude"]
    )
    assert (
        color_for_resolved_glance(
            settings, _glance(GlanceSemantic.ACTIVE), provider="codex"
        )
        == PROVIDER_BRAND_COLORS["codex"]
    )


@pytest.mark.parametrize(
    "semantic",
    [
        GlanceSemantic.ATTENTION,
        GlanceSemantic.FRESH_FAILURE,
        GlanceSemantic.FRESH_COMPLETION,
        GlanceSemantic.UNRESOLVED_FAILURE,
        GlanceSemantic.REST,
    ],
)
def test_the_apps_own_signals_never_become_a_provider_guessing_game(semantic) -> None:
    """Ask, failure, done and rest stay one unmistakable colour each."""
    settings = ColorSettings.defaults()

    assert color_for_resolved_glance(
        settings, _glance(semantic), provider="claude"
    ) == color_for_resolved_glance(settings, _glance(semantic))


# --- the settings layer ----------------------------------------------------


def test_applying_a_palette_never_repaints_a_declared_brand() -> None:
    """How the live install came to hold ``claude: #10A37F``.

    That is OpenAI's own green, written by clicking the "OpenAI" palette.
    From then on nothing anywhere could render Claude as Claude, because
    every path reads ``agent_color`` and ``agent_color`` reads settings.
    """
    palette = colors_module.PROVIDER_PALETTES["OpenAI"]

    colors = colors_module.apply_palette(ColorSettings.defaults(), palette)

    assert colors.mode_color("done") == palette["modes"]["done"]
    assert colors.agent_color("claude") == PROVIDER_BRAND_COLORS["claude"]
    assert colors.agent_color("codex") == PROVIDER_BRAND_COLORS["codex"]
    # A provider with no declared brand still follows the look.
    assert colors.agent_color("gemini") == palette["agents"]["gemini"]


def test_the_settings_windows_brand_chips_are_the_brand_colours() -> None:
    """One table cannot disagree with the other if there is one table."""
    from sidepulse import settings_window

    assert settings_window.BRAND_SWATCHES is colors_module.BRAND_SEED_COLORS
    assert dict(settings_window.BRAND_SWATCHES)["Codex"] == PROVIDER_BRAND_COLORS["codex"]
    assert dict(settings_window.BRAND_SWATCHES)["Claude"] == PROVIDER_BRAND_COLORS["claude"]


def test_both_surfaces_route_a_crowd_the_same_way() -> None:
    """The notch and the strip must not speak two colour languages.

    ``should_render_multi_agent`` was called at exactly ONE site -- the
    hardware write path. The Screen Bar tested ``resolved_glance is not
    None`` first, and ``resolve_presentation_glance`` never returns None,
    so its multi-agent branch was unreachable: the strip painted per-agent
    identity while the notch beside it painted one fleet-wide state hue,
    for the same instant.
    """
    import inspect

    from sidepulse.status_bar import StatusBarController

    def _combined_source(name: str) -> str:
        # The production facade wraps these methods; the routing lives in
        # whichever layer of the controller stack defines the behaviour.
        return "\n".join(
            inspect.getsource(vars(base)[name])
            for base in StatusBarController.__mro__
            if name in vars(base)
        )

    hardware = _combined_source("_sync_hardware_device")
    screen_bar = _combined_source("sync_virtual_status_device")

    assert "should_render_multi_agent" in hardware
    assert "should_render_multi_agent" in screen_bar
    # And the routing order is the same on both: crowd first, then glance.
    assert screen_bar.index("should_render_multi_agent") < screen_bar.index(
        "color_for_resolved_glance"
    )


def test_a_palette_that_already_ate_a_brand_colour_is_repaired_on_load() -> None:
    """The live install still holds the damage; a fix must undo it.

    ``/Users/.../settings.json`` carried ``claude: #10A37F`` -- OpenAI's
    own green, byte-exact from PROVIDER_PALETTES["OpenAI"] -- written
    before applyPalette_ learned to leave brands alone. Stopping the bug
    does not repaint what it already painted, and the owner would still
    see the wrong colour after installing the fix.
    """
    palette = colors_module.PROVIDER_PALETTES["OpenAI"]
    damaged = {
        "colors": {
            "agent_colors": {
                "claude": "#10A37F",
                "devin": palette["agents"]["devin"],
                "gemini": palette["agents"]["gemini"],
            }
        }
    }

    repaired = ColorSettings.from_dict(damaged["colors"])

    assert repaired.agent_color("claude") == PROVIDER_BRAND_COLORS["claude"]
    assert repaired.agent_color("devin") == PROVIDER_BRAND_COLORS["devin"]
    # gemini has no declared brand, so the chosen look is the user's.
    assert repaired.agent_color("gemini") == palette["agents"]["gemini"]


def test_a_hand_picked_provider_colour_is_never_touched() -> None:
    """The repair is narrow on purpose: only a palette's own output."""
    chosen = ColorSettings.from_dict({"agent_colors": {"claude": "#123456"}})

    assert chosen.agent_color("claude") == "#123456"
