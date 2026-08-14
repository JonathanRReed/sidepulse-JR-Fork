"""The Colour / Animation Studio: its model, its rendering, and the thin
renderer over both.

The bug this suite exists for was reported as "we have a thing somewhere in
the menu that lets us choose brand colors for all of the providers we have
selected. I don't know where to find it or how to do it." What was actually
there: rows with no provider identity, swatch strips of anonymous coloured
squares, and a sentence of body text claiming the first four squares were the
Claude/OpenAI/Codex/Gemini brand colours -- which they were not, because the
strip being drawn was CURATED_PALETTE (system red/blue/green/purple).

So the interesting half is now a MODEL: which swatches, what they are named,
what order they come in, which ones are brands. That is what this file tests
exhaustively. The view tests below only assert that AppKit renders the model
faithfully and wires its actions, because PyObjC is a bad place to hold logic.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sidepulse import colors as colors_module
from sidepulse.colors import (
    BRAND_SEED_COLORS,
    CURATED_PALETTE,
    CUSTOM_SWATCH_NAME,
    MOTION_BEAT,
    MOTION_BLINK,
    MOTION_BREATHE,
    MOTION_CHASE,
    MOTION_STEADY,
    PROVIDER_ANIMATION_AUTO,
    PROVIDER_ANIMATION_CHOICES,
    PROVIDER_ANIMATION_LABELS,
    PROVIDER_PALETTES,
    STUDIO_SECTION_CHOICES,
    SWATCH_GROUP_BRAND,
    SWATCH_GROUP_CUSTOM,
    SWATCH_GROUP_PALETTE,
    ColorSettings,
    StudioPreviewSession,
    agent_motion,
    is_brand_color,
    normalize_studio_section,
    program_for_snapshot,
    provider_color_row,
    provider_color_rows,
    studio_preview_program,
    swatch_name,
)
from sidepulse.led_status import LedDisplayState
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.providers import PROVIDER_SPECS


def _status(provider: str, mode: AgentMode, *, agent_id: str | None = None) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=agent_id or f"{provider}:1",
        display_name=provider.title(),
        mode=mode,
        updated_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        event_name="test",
    )


# --- Named brand groups ----------------------------------------------------


def test_every_swatch_on_every_provider_row_has_a_name() -> None:
    """"No anonymous colour squares anywhere." A coloured rectangle with no
    word attached is a guess, not a choice, and it is what made the old
    card's brand-colour claim unverifiable by anyone but its author."""
    for row in provider_color_rows(ColorSettings.defaults()):
        for group in row.groups:
            assert group.label, f"{row.provider}: unlabelled group {group.key}"
            assert group.swatches, f"{row.provider}: empty {group.key} group"
            for swatch in group.swatches:
                assert swatch.name.strip(), f"{row.provider}: anonymous swatch {swatch.hex}"
                assert swatch.hex.startswith("#") and len(swatch.hex) == 7
                assert swatch.tooltip.strip()


def test_each_row_leads_with_a_labelled_brand_group_naming_all_four_brands() -> None:
    """Identity first, palette second -- and the Brand group says "Brand"."""
    for row in provider_color_rows(ColorSettings.defaults()):
        first = row.groups[0]
        assert first.key == SWATCH_GROUP_BRAND
        assert first.label == "Brand"
        brand_pairs = [
            (swatch.name, swatch.hex) for swatch in first.swatches if swatch.name != "Default"
        ]
        assert brand_pairs == list(BRAND_SEED_COLORS), row.provider
        assert all(swatch.is_brand for swatch in first.swatches)


def test_the_row_leads_with_the_providers_own_identity() -> None:
    rows = provider_color_rows(ColorSettings.defaults())
    assert [row.provider for row in rows] == [spec.provider for spec in PROVIDER_SPECS]
    assert [row.label for row in rows] == [spec.label for spec in PROVIDER_SPECS]
    # Row order is the renderer's own LED-assignment order, so the window and
    # the strip agree about who comes first.
    assert rows[0].provider == PROVIDER_SPECS[0].provider


def test_palette_swatches_never_claim_to_be_brand_colours() -> None:
    """The exact regression: the card announced "the first four swatches on
    every row are Claude, OpenAI, Codex and Gemini's official brand colors"
    while rendering CURATED_PALETTE, whose first four are system red, blue,
    green and purple. Nothing in the palette group may claim brandhood."""
    assert not any(is_brand_color(hex_value) for hex_value in CURATED_PALETTE[:4])
    for row in provider_color_rows(ColorSettings.defaults()):
        palette = row.group(SWATCH_GROUP_PALETTE)
        assert palette.label == "Palette"
        assert not any(swatch.is_brand for swatch in palette.swatches)
        assert [swatch.hex for swatch in palette.swatches] == list(CURATED_PALETTE)


def test_brand_seeds_and_brand_palettes_cannot_drift_apart() -> None:
    """One source of truth: the "Claude" look and the "Claude" swatch are
    seeded from the same hex, so renaming or recolouring one moves both."""
    assert list(PROVIDER_PALETTES) == [name for name, _hex in BRAND_SEED_COLORS]
    for name, seed in BRAND_SEED_COLORS:
        assert PROVIDER_PALETTES[name] == colors_module.derive_palette(seed)


def test_a_row_names_the_swatch_it_is_actually_wearing() -> None:
    colors = ColorSettings.defaults()
    row = provider_color_row("claude", colors)
    assert row.current_name == "Claude"

    openai = provider_color_row("claude", colors.with_agent_color("claude", "#10A37F"))
    assert openai.current_name == "OpenAI"

    grey = provider_color_row("claude", colors.with_agent_color("claude", "#8E8E93"))
    assert grey.current_name == "Gray"


def test_a_hand_picked_colour_stays_a_named_selected_swatch() -> None:
    colors = ColorSettings.defaults().with_agent_color("claude", "#123456")
    row = provider_color_row("claude", colors)
    custom = row.group(SWATCH_GROUP_CUSTOM)
    assert [swatch.name for swatch in custom.swatches] == [CUSTOM_SWATCH_NAME]
    assert custom.swatches[0].hex == "#123456"
    assert custom.swatches[0].selected
    assert custom.swatches[0].opens_picker
    assert row.current_name == CUSTOM_SWATCH_NAME


def test_a_providers_own_shipped_colour_is_named_default_not_repeated() -> None:
    """Devin ships #1D3461, which is in neither named set. It leads the row
    as "Default" -- and must not ALSO show up as an unnamed custom chip."""
    row = provider_color_row("devin", ColorSettings.defaults())
    brand = row.group(SWATCH_GROUP_BRAND)
    assert brand.swatches[0].name == "Default"
    assert brand.swatches[0].hex == "#1D3461"
    assert brand.swatches[0].selected
    assert row.current_name == "Default"
    picker = row.group(SWATCH_GROUP_CUSTOM).swatches[0]
    assert picker.name == "Pick…"
    # The picker is a BUTTON here, not a second copy of the row's colour --
    # otherwise #1D3461 appears twice on one row and reads as a duplicate.
    assert picker.is_control
    assert not picker.selected

    offered = [swatch.hex.upper() for swatch in row.all_swatches if not swatch.is_control]
    assert len(offered) == len(set(offered)), "a colour appears twice on one row"


def test_a_provider_whose_default_is_already_named_gets_no_default_chip() -> None:
    claude = provider_color_row("claude", ColorSettings.defaults())
    assert "Default" not in [swatch.name for swatch in claude.group(SWATCH_GROUP_BRAND).swatches]
    grok = provider_color_row("grok", ColorSettings.defaults())
    assert grok.current_name == "Gray"


def test_swatch_name_resolves_brand_then_palette_then_identity_then_custom() -> None:
    assert swatch_name("#D97757") == "Claude"
    assert swatch_name("#ff3b30") == "Red"
    assert swatch_name("#4C8DFF") == "Azure"
    assert swatch_name("#010203") == CUSTOM_SWATCH_NAME


def test_exactly_one_swatch_per_row_is_selected() -> None:
    for provider in ("claude", "codex", "devin", "grok", "cursor"):
        row = provider_color_row(provider, ColorSettings.defaults())
        selected = [swatch for swatch in row.all_swatches if swatch.selected]
        assert len(selected) == 1, f"{provider}: {[s.name for s in selected]}"
        assert selected[0].hex.upper() == row.current_hex.upper()


# --- Studio sections -------------------------------------------------------


def test_the_studio_offers_colours_animations_and_preview_as_peers() -> None:
    assert STUDIO_SECTION_CHOICES == ("colors", "animations", "preview")
    assert normalize_studio_section("animations") == "animations"
    assert normalize_studio_section("nonsense") == "colors"
    assert normalize_studio_section(None) == "colors"


# --- Per-provider animation ------------------------------------------------


def test_provider_animation_defaults_to_automatic_and_round_trips() -> None:
    colors = ColorSettings.defaults()
    assert colors.agent_animation("claude") == PROVIDER_ANIMATION_AUTO
    assert colors.provider_animation == {}

    chased = colors.with_agent_animation("claude", MOTION_CHASE)
    assert chased.agent_animation("claude") == MOTION_CHASE
    assert colors.agent_animation("claude") == PROVIDER_ANIMATION_AUTO  # frozen
    assert ColorSettings.from_dict(chased.to_dict()) == chased


def test_automatic_removes_the_stored_choice_rather_than_storing_auto() -> None:
    colors = ColorSettings.defaults().with_agent_animation("claude", MOTION_STEADY)
    back = colors.with_agent_animation("claude", PROVIDER_ANIMATION_AUTO)
    assert back.provider_animation == {}
    assert back == ColorSettings.from_dict(back.to_dict())


def test_a_settings_file_from_before_this_feature_still_loads() -> None:
    legacy = ColorSettings.defaults().to_dict()
    legacy.pop("provider_animation")
    assert ColorSettings.from_dict(legacy) == ColorSettings.defaults()


def test_garbage_provider_animation_reads_as_automatic_and_is_not_persisted() -> None:
    payload = ColorSettings.defaults().to_dict()
    payload["provider_animation"] = {
        "claude": "interpretive-dance",
        "codex": PROVIDER_ANIMATION_AUTO,
        "": MOTION_CHASE,
        "devin": MOTION_BLINK,
    }
    loaded = ColorSettings.from_dict(payload)
    assert loaded.provider_animation == {"devin": MOTION_BLINK}
    assert loaded.agent_animation("claude") == PROVIDER_ANIMATION_AUTO
    with pytest.raises(ValueError):
        loaded.with_agent_animation("claude", "interpretive-dance")


def test_every_offered_animation_has_a_label_and_a_description() -> None:
    for motion in PROVIDER_ANIMATION_CHOICES:
        assert PROVIDER_ANIMATION_LABELS[motion].strip()
        assert colors_module.PROVIDER_ANIMATION_DESCRIPTIONS[motion].strip()
    assert PROVIDER_ANIMATION_LABELS[PROVIDER_ANIMATION_AUTO] == "Automatic"
    # The vocabulary the owner asked for, and nothing invented alongside it.
    assert set(PROVIDER_ANIMATION_CHOICES) - {PROVIDER_ANIMATION_AUTO} == {
        MOTION_BREATHE,
        MOTION_CHASE,
        MOTION_STEADY,
        MOTION_BLINK,
    }
    assert MOTION_BEAT not in PROVIDER_ANIMATION_CHOICES


def test_a_providers_animation_replaces_the_states_rhythm() -> None:
    colors = ColorSettings.defaults().with_agent_animation("claude", MOTION_STEADY)
    assert agent_motion(LedDisplayState.WORKING, cycle_ms=1600) == MOTION_CHASE
    assert (
        agent_motion(
            LedDisplayState.WORKING, cycle_ms=1600, provider="claude", settings=colors
        )
        == MOTION_STEADY
    )
    # A provider left on Automatic is untouched.
    assert (
        agent_motion(
            LedDisplayState.WORKING, cycle_ms=1600, provider="codex", settings=colors
        )
        == MOTION_CHASE
    )


def test_urgency_keeps_its_beat_whatever_the_provider_was_set_to() -> None:
    """A per-provider animation is for telling two busy agents apart. It is
    not a switch for turning "a human is needed" into something calmer."""
    colors = ColorSettings.defaults().with_agent_animation("claude", MOTION_STEADY)
    for state in (LedDisplayState.ASK, LedDisplayState.FAILED):
        assert agent_motion(
            state, cycle_ms=1600, provider="claude", settings=colors
        ) == agent_motion(state, cycle_ms=1600)


def test_a_chosen_animation_still_obeys_nothing_above_two_hertz() -> None:
    colors = ColorSettings.defaults().with_agent_animation("claude", MOTION_BLINK)
    fast = colors_module.MIN_FLASH_CYCLE_MS - 1
    assert (
        agent_motion(LedDisplayState.WORKING, cycle_ms=fast, provider="claude", settings=colors)
        == MOTION_STEADY
    )
    assert (
        agent_motion(
            LedDisplayState.WORKING,
            cycle_ms=colors_module.MIN_FLASH_CYCLE_MS,
            provider="claude",
            settings=colors,
        )
        == MOTION_BLINK
    )


def _round_robin(colors: ColorSettings) -> str:
    statuses = (
        _status("claude", AgentMode.WORKING),
        _status("codex", AgentMode.WORKING),
    )
    _state, program = program_for_snapshot(
        statuses, led_count=8, colors=colors.with_blend_mode(colors_module.BLEND_MODE_ROUND_ROBIN)
    )
    return program


def test_one_provider_on_steady_changes_only_its_own_leds() -> None:
    """Two agents working at once: put Claude on Steady and its LEDs hold
    while Codex's keep chasing. Without the per-provider wiring both keep
    pulsing and the two programs are byte-identical."""
    base = ColorSettings.defaults()
    changed = base.with_agent_animation("claude", MOTION_STEADY)

    before = _round_robin(base)
    after = _round_robin(changed)
    assert before != after

    # Claude sorts after Codex in PROVIDER_SPECS order, so it owns the odd
    # LEDs; those lose their pulse while the even ones keep theirs.
    motion_line = after.splitlines()[1]
    segments = motion_line.split("; ")
    assert "pulse" not in segments[1]
    assert "pulse" in segments[0]


def test_a_lone_agent_also_gets_its_providers_animation() -> None:
    base = ColorSettings.defaults()
    changed = base.with_agent_animation("claude", MOTION_STEADY)
    statuses = (_status("claude", AgentMode.WORKING),)

    _state, before = program_for_snapshot(statuses, led_count=8, colors=base)
    _state, after = program_for_snapshot(statuses, led_count=8, colors=changed)
    assert before != after
    assert "pulse" not in after


def _segments(program: str, line: int) -> list[str]:
    return program.splitlines()[line].split("; ")


def test_spatial_split_blocks_follow_the_providers_animation() -> None:
    base = ColorSettings.defaults().with_blend_mode(colors_module.BLEND_MODE_SPATIAL)
    changed = base.with_agent_animation("claude", MOTION_STEADY)
    statuses = (
        _status("claude", AgentMode.WORKING),
        _status("codex", AgentMode.WORKING),
    )
    _state, before = program_for_snapshot(statuses, led_count=8, colors=base)
    _state, after = program_for_snapshot(statuses, led_count=8, colors=changed)
    assert before != after

    # Blocks are laid out in PROVIDER_SPECS order, so codex owns 0-3 and
    # claude 4-7. Only claude's block loses its pulse.
    motion = _segments(after, 1)
    assert all("pulse" in segment for segment in motion[:4])
    assert not any("pulse" in segment for segment in motion[4:])

    # And its RESET line has to agree it is not pulsing: a reset that still
    # dims to the fade floor while the motion line holds solid parks the
    # whole block at its floor colour.
    def reset_color(program: str, index: int) -> str:
        return _segments(program, 0)[index].split(":")[1].split()[0]

    for index in range(4, 8):
        assert colors_module.relative_luminance(
            reset_color(after, index)
        ) > colors_module.relative_luminance(reset_color(before, index))


def test_relay_stays_byte_stable_when_every_provider_holds_still() -> None:
    """Relay skips its baton rotation when nothing is actually moving -- the
    property that keeps it from rewriting the device for motion that does not
    exist. A provider parked on Steady counts as still."""
    colors = (
        ColorSettings.defaults()
        .with_blend_mode(colors_module.BLEND_MODE_RELAY)
        .with_agent_animation("claude", MOTION_STEADY)
        .with_agent_animation("codex", MOTION_STEADY)
    )
    statuses = (
        _status("claude", AgentMode.WORKING),
        _status("codex", AgentMode.WORKING),
    )
    programs = {
        program_for_snapshot(
            statuses, led_count=8, colors=colors, relay_elapsed_seconds=elapsed
        )[1]
        for elapsed in (0.0, 0.4, 0.9, 1.7)
    }
    assert len(programs) == 1


# --- Uncommitted preview ---------------------------------------------------


def test_hovering_never_changes_what_is_saved() -> None:
    committed = ColorSettings.defaults()
    session = StudioPreviewSession(committed)
    assert not session.previewing

    candidate = session.preview_agent_color("claude", "#10A37F")
    assert session.previewing
    assert session.effective is candidate
    assert candidate.agent_color("claude") == "#10A37F"
    assert session.committed is committed
    assert session.committed.agent_color("claude") == "#D97757"

    assert session.revert() is committed
    assert not session.previewing
    assert session.effective is committed


def test_committing_keeps_whatever_is_being_previewed() -> None:
    session = StudioPreviewSession(ColorSettings.defaults())
    session.preview_agent_animation("claude", MOTION_CHASE)
    kept = session.commit()
    assert kept.agent_animation("claude") == MOTION_CHASE
    assert not session.previewing
    assert session.effective is kept


def test_rebasing_drops_an_in_flight_hover() -> None:
    """A palette button or Reset changes the ground under an open hover. The
    candidate is stale the moment that happens and must not repaint over it."""
    session = StudioPreviewSession(ColorSettings.defaults())
    session.preview_agent_color("claude", "#10A37F")
    fresh = ColorSettings.defaults().with_agent_color("claude", "#FF3A00")
    assert session.rebase(fresh) is fresh
    assert not session.previewing
    assert session.effective is fresh


def test_a_preview_program_reflects_the_candidate_not_the_saved_settings() -> None:
    committed = ColorSettings.defaults()
    candidate = committed.with_agent_color("claude", "#10A37F")
    statuses = (_status("claude", AgentMode.WORKING),)

    before = studio_preview_program(committed, statuses=statuses)
    after = studio_preview_program(candidate, statuses=statuses)
    assert before != after
    assert after == program_for_snapshot(
        statuses, led_count=8, colors=candidate, brightness=1.0
    )[1]
    assert committed.agent_color("claude") == "#D97757"


def test_a_provider_preview_puts_that_provider_alone_on_the_strip() -> None:
    """With two or more sessions the renderer switches to session identity
    colours, so previewing a PROVIDER colour against a crowd would show no
    change at all -- the swatch would look broken."""
    statuses = colors_module.provider_preview_statuses("claude")
    assert [status.provider for status in statuses] == ["claude"]

    committed = ColorSettings.defaults()
    candidate = committed.with_agent_color("claude", "#10A37F")
    assert studio_preview_program(committed, statuses=statuses) != studio_preview_program(
        candidate, statuses=statuses
    )

    crowd = (_status("claude", AgentMode.WORKING), _status("codex", AgentMode.WORKING))
    assert studio_preview_program(committed, statuses=crowd) == studio_preview_program(
        candidate, statuses=crowd
    )


def test_a_preview_with_nothing_running_still_shows_something() -> None:
    program = studio_preview_program(ColorSettings.defaults(), statuses=())
    assert program.strip()


# --- The Screen Bar hands the surface back --------------------------------


class _RecordingDevice:
    """A VirtualStatusDevice with its one AppKit-touching method replaced."""

    def __init__(self):
        from sidepulse.virtual_device import VirtualStatusDevice

        self.device = VirtualStatusDevice.alloc().init()
        self.applied: list[tuple[str, dict]] = []
        self.device._apply_program = self._apply

    def _apply(self, program, **kwargs):
        self.applied.append((str(program), dict(kwargs)))

    @property
    def shown(self) -> str | None:
        return self.applied[-1][0] if self.applied else None


def test_a_held_preview_owns_the_screen_bar_and_hides_live_updates() -> None:
    recorder = _RecordingDevice()
    device = recorder.device

    device.set_program("live-1")
    assert recorder.shown == "live-1"

    device.hold_preview_program("candidate")
    assert device.preview_is_held()
    assert recorder.shown == "candidate"

    device.set_program("live-2")
    assert recorder.shown == "candidate", "a live update repainted over the preview"


def test_releasing_a_preview_reverts_to_the_CURRENT_live_program() -> None:
    """Not the frame from before the hover: the world moved on underneath."""
    recorder = _RecordingDevice()
    device = recorder.device

    device.set_program("live-1")
    device.hold_preview_program("candidate")
    device.set_program("live-2")

    assert device.release_preview_program() is True
    assert recorder.shown == "live-2"
    assert device.preview_is_held() is False


def test_releasing_with_no_live_update_restores_what_was_there_before() -> None:
    recorder = _RecordingDevice()
    device = recorder.device

    device.set_program("live-1")
    device.hold_preview_program("candidate")
    assert device.release_preview_program() is True
    assert recorder.shown == "live-1"


def test_releasing_when_nothing_is_held_says_so_and_paints_nothing() -> None:
    recorder = _RecordingDevice()
    recorder.device.set_program("live-1")
    assert recorder.device.release_preview_program() is False
    assert len(recorder.applied) == 1


def test_a_hold_nobody_released_expires_instead_of_owning_the_bar_forever(
    monkeypatch,
) -> None:
    """The backstop for a preview whose exit event never arrived -- the
    window closed under the pointer, the pane was torn down mid-hover."""
    from sidepulse import virtual_device

    clock = [1000.0]
    monkeypatch.setattr(virtual_device.time, "monotonic", lambda: clock[0])
    recorder = _RecordingDevice()
    device = recorder.device

    device.set_program("live-1")
    device.hold_preview_program("candidate")
    clock[0] += virtual_device.PREVIEW_HOLD_MAX_SECONDS + 1.0

    device.set_program("live-2")

    assert device.preview_is_held() is False
    assert recorder.shown == "live-2"


# --- The view is a thin renderer over the model ---------------------------


class StudioPaneTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests.test_sidepulse import isolate_controller

        isolate_controller(self)
        self.controller.show_settings_window()
        self.controller.ensure_settings_pane("color_studio")
        self.pane = self.controller.settings_panes["color_studio"]
        self.actions = self.controller.studio_actions
        # refresh_() walks real device discovery and the keepalive path; the
        # Studio's own behaviour is what is under test here.
        self.controller.refresh_ = lambda _sender: None

    def test_the_pane_builds_three_peer_sections_and_shows_one(self) -> None:
        self.assertEqual(
            sorted(self.actions.section_views), sorted(STUDIO_SECTION_CHOICES)
        )
        visible = [
            key for key, view in self.actions.section_views.items() if not view.isHidden()
        ]
        self.assertEqual(visible, ["colors"])

        self.actions.select_section("animations")
        visible = [
            key for key, view in self.actions.section_views.items() if not view.isHidden()
        ]
        self.assertEqual(visible, ["animations"])
        self.assertEqual(self.controller.studio_section, "animations")

    def test_switching_section_hands_the_screen_bar_back(self) -> None:
        held = []
        self.controller.virtual_status_device = SimpleNamespace(
            hold_preview_program=lambda program, **kwargs: held.append(program),
            release_preview_program=lambda: (held.append("released"), True)[1],
        )
        self.actions.preview_colors(self.controller.settings.colors, "trying")
        self.actions.select_section("preview")
        self.assertIn("released", held)
        self.assertFalse(self.actions.preview_session.previewing)

    def test_every_rendered_swatch_carries_a_visible_name(self) -> None:
        """Not a tooltip: a word, on screen, under the chip."""
        found = 0
        for (_row_key, _hex), button in self.controller.color_swatches.items():
            caption = getattr(button, "studio_caption", None)
            self.assertIsNotNone(caption)
            self.assertTrue(caption.stringValue().strip())
            self.assertTrue(button.toolTip().strip())
            found += 1
        self.assertGreater(found, len(PROVIDER_SPECS) * 4)

    def test_each_provider_has_an_animation_control_wired_to_its_own_provider(
        self,
    ) -> None:
        self.assertEqual(
            sorted(self.actions.animation_popups),
            sorted(spec.provider for spec in PROVIDER_SPECS),
        )
        popup = self.actions.animation_popups["claude"]
        self.assertEqual(
            [popup.itemAtIndex_(i).title() for i in range(popup.numberOfItems())],
            [PROVIDER_ANIMATION_LABELS[m] for m in PROVIDER_ANIMATION_CHOICES],
        )
        for index in range(popup.numberOfItems()):
            self.assertEqual(
                popup.itemAtIndex_(index).representedObject()["provider"], "claude"
            )

    def test_choosing_an_animation_saves_it_and_resyncs_the_control(self) -> None:
        self.assertTrue(self.actions.apply_provider_animation("claude", MOTION_CHASE))
        self.assertEqual(
            self.controller.settings.colors.agent_animation("claude"), MOTION_CHASE
        )
        self.assertEqual(
            self.actions.animation_popups["claude"].titleOfSelectedItem(),
            PROVIDER_ANIMATION_LABELS[MOTION_CHASE],
        )
        from sidepulse.settings import load_settings

        self.assertEqual(
            load_settings(self._settings_path).colors.agent_animation("claude"),
            MOTION_CHASE,
        )

    def test_choosing_a_colour_saves_it_and_renames_the_row(self) -> None:
        self.assertTrue(self.actions.apply_provider_color("claude", "#10A37F"))
        self.assertEqual(self.controller.settings.colors.agent_color("claude"), "#10A37F")
        sync = self.controller.color_hex_labels[("agent", "claude")]
        self.assertEqual(sync.name_label.stringValue(), "OpenAI")
        self.assertEqual(sync.hex_label.stringValue(), "#10A37F")

    def test_a_colour_changed_elsewhere_still_renames_the_row(self) -> None:
        """A palette button writes settings directly and then asks the window
        to catch up; the row's NAME has to be part of what catches up."""
        self.controller.settings = self.controller.settings.with_colors(
            self.controller.settings.colors.with_agent_color("claude", "#FF3A00")
        )
        self.controller.refresh_colors_window()
        sync = self.controller.color_hex_labels[("agent", "claude")]
        self.assertEqual(sync.name_label.stringValue(), "Codex")
        self.assertEqual(
            self.actions.animation_popups["claude"].titleOfSelectedItem(),
            PROVIDER_ANIMATION_LABELS[PROVIDER_ANIMATION_AUTO],
        )

    def test_hovering_a_swatch_previews_on_the_screen_bar_and_leaving_reverts(
        self,
    ) -> None:
        events = []
        self.controller.virtual_status_device = SimpleNamespace(
            hold_preview_program=lambda program, **kwargs: events.append(("hold", program)),
            release_preview_program=lambda: (events.append(("release", None)), True)[1],
        )
        button = self.controller.color_swatches[(("agent", "claude"), "#10A37F")]
        self.assertTrue(callable(button.hover_enter))

        button.hover_enter(button)
        self.assertEqual(events[0][0], "hold")
        # The view is a renderer: what it holds is exactly what the model says.
        candidate = self.controller.settings.colors.with_agent_color("claude", "#10A37F")
        self.assertEqual(
            events[0][1],
            studio_preview_program(
                candidate,
                statuses=colors_module.provider_preview_statuses("claude"),
            ),
        )
        self.assertTrue(self.actions.preview_session.previewing)
        # Hovering is not choosing.
        self.assertEqual(self.controller.settings.colors.agent_color("claude"), "#D97757")

        button.hover_exit(button)
        self.assertEqual(events[-1][0], "release")
        self.assertFalse(self.actions.preview_session.previewing)

    def test_the_before_after_strip_shows_the_candidate_next_to_the_saved_look(
        self,
    ) -> None:
        compare = self.controller.studio_compare
        self.assertEqual(len(compare["before"]), 8)
        self.assertEqual(len(compare["after"]), 8)

        self.controller.virtual_status_device = None
        candidate = self.controller.settings.colors.with_agent_color("claude", "#10A37F")
        self.actions.preview_colors(candidate, "Trying Claude: OpenAI")
        self.assertEqual(compare["caption"].stringValue(), "Trying Claude: OpenAI")

        self.actions.end_preview()
        self.assertIn("Hover", compare["caption"].stringValue())

    def test_hardware_preview_is_a_control_you_have_to_find_and_flip(self) -> None:
        toggle = self.controller.color_fields["live_toggle"]
        self.assertIsNotNone(toggle)
        self.assertEqual(
            bool(toggle.state()), bool(self.controller.color_preview_enabled)
        )
        # Hovering reaches the Screen Bar only -- never a physical device.
        pushed = []
        self.controller.push_colors_preview_to_device = lambda: pushed.append(True)
        self.controller.virtual_status_device = SimpleNamespace(
            hold_preview_program=lambda program, **kwargs: None,
            release_preview_program=lambda: False,
        )
        button = self.controller.color_swatches[(("agent", "claude"), "#10A37F")]
        button.hover_enter(button)
        button.hover_exit(button)
        self.assertEqual(pushed, [])
