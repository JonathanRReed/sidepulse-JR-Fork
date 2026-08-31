"""Regression tests for the confirmed Colour / Animation Studio defects.

Each test here failed against the tree it was written for. They are grouped by
the defect they pin, and the group that matters most is the first one: the
THROWAWAY-LOCAL class of bug, where a view is built, added to a stack, wired
to nothing, and its reference dropped. This pane is built once and cached for
the window's lifetime, so a dropped reference is not a flicker -- it is a
string that is permanently wrong, sitting beside a control that is right.

The model half of every fix lives in ``sidepulse.colors`` and is asserted
without instantiating a single NSView; the view half asserts only that AppKit
holds the model's objects and re-reads them.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sidepulse import colors as colors_module
from sidepulse.colors import (
    BRAND_SEED_COLORS,
    CURATED_PALETTE,
    PROVIDER_ANIMATION_CHOICES,
    PROVIDER_ANIMATION_DESCRIPTIONS,
    PROVIDER_BRAND_COLORS,
    STATE_SEED_COLORS,
    SWATCH_GROUP_CUSTOM,
    SWATCH_GROUP_DEFAULT,
    ColorSettings,
    default_agent_color,
    mode_color_row,
    mode_color_rows,
    provider_color_row,
    provider_color_rows,
    swatch_name,
)
from sidepulse.led_status import ASK_AMBER, DONE_GREEN, IDLE_DIM, WORKING_CYAN
from sidepulse.providers import PROVIDER_SPECS

MOTION_CHASE = colors_module.MOTION_CHASE
MOTION_BLINK = colors_module.MOTION_BLINK


# --- Defect 4: the throwaway-local class ------------------------------------


def is_brand_color(hex_value):
    """Local guard over the live brand table (the src helper was deleted
    2026-08-26: tests were its only callers; the TABLE is load-bearing)."""
    from sidepulse.colors import _BRAND_NAME_BY_HEX, normalize_hex

    return normalize_hex(hex_value, "#000000").upper() in _BRAND_NAME_BY_HEX



def test_the_model_carries_the_animation_sentence_not_just_its_key() -> None:
    """The description the view shows is a property of the row, so a view
    that renders the row cannot disagree with the popup beside it."""
    colors = ColorSettings.defaults()
    for motion in PROVIDER_ANIMATION_CHOICES:
        row = provider_color_row("codex", colors.with_agent_animation("codex", motion))
        assert row.animation == motion
        assert row.animation_description == PROVIDER_ANIMATION_DESCRIPTIONS[motion]
        assert row.animation_description.strip()


class ThrowawayLocalTests(unittest.TestCase):
    """The reported bug and its siblings: views built as locals and dropped.

    Reproduced before the fix: after ``apply_provider_animation("codex",
    CHASE)`` the popup read "Chase" while all eight description labels still
    read "Follows the state: breathe when idle, chase while working", the
    thumb's tooltip was stale the same way, and recolouring a provider in the
    Colors section left the Animations section's identity line showing the
    provider's old hex.
    """

    def setUp(self) -> None:
        from tests.test_sidepulse import isolate_controller

        isolate_controller(self)
        self.controller.show_settings_window()
        self.controller.ensure_settings_pane("color_studio")
        self.actions = self.controller.studio_actions
        self.pane = self.controller.settings_panes["color_studio"]
        self.controller.refresh_ = lambda _sender: None

    # --- the generic guard --------------------------------------------------

    def _strings(self) -> list[str]:
        found: list[str] = []

        def walk(view):
            for sub in view.subviews():
                try:
                    value = sub.stringValue()
                except Exception:
                    value = None
                if isinstance(value, str) and value:
                    found.append(value)
                walk(sub)

        walk(self.pane)
        return found

    def test_every_syncable_view_is_held_by_its_row_and_still_on_screen(self) -> None:
        """A live reference, not a copy: each registered view must still have
        a superview, which is what distinguishes "the object the stack is
        showing" from "an object that happens to look like it"."""
        providers = {spec.provider for spec in PROVIDER_SPECS}
        for row_key, sync in self.controller.color_hex_labels.items():
            kind, _key = row_key
            self.assertTrue(sync.identity_labels, f"{row_key}: no identity labels held")
            self.assertTrue(sync.pickers, f"{row_key}: picker dropped")
            for name_label, hex_label in sync.identity_labels:
                self.assertIsNotNone(name_label.superview(), row_key)
                self.assertIsNotNone(hex_label.superview(), row_key)
            for picker in sync.pickers:
                self.assertIsNotNone(picker.superview(), row_key)
            if kind == "agent":
                self.assertTrue(
                    sync.description_labels, f"{row_key}: animation sentence dropped"
                )
                for label in sync.description_labels:
                    self.assertIsNotNone(label.superview(), row_key)
        self.assertEqual(
            {key for kind, key in self.controller.color_hex_labels if kind == "agent"},
            providers,
        )

    def test_a_provider_drawn_in_two_sections_keeps_both_copies_in_sync(self) -> None:
        """Colors and Animations both draw every provider's identity line.
        The Animations copy used to be discarded as ``_name_label,
        _hex_label`` and went stale the moment a colour changed."""
        sync = self.controller.color_hex_labels[("agent", "claude")]
        self.assertGreaterEqual(len(sync.identity_labels), 2)

        self.assertTrue(self.actions.apply_provider_color("claude", "#10A37F"))
        for name_label, hex_label in sync.identity_labels:
            self.assertEqual(hex_label.stringValue(), "#10A37F")
            self.assertEqual(name_label.stringValue(), "OpenAI")

    def test_nothing_in_the_pane_still_shows_a_colour_no_row_is_wearing(self) -> None:
        """The blunt instrument, and the one that would catch a NEW dropped
        reference anywhere in this pane: recolour every row to a hex nothing
        else uses, then assert no label anywhere still prints an old one."""
        replacements = {
            spec.provider: f"#0{index}1{index}2{index}"
            for index, spec in enumerate(PROVIDER_SPECS)
        }
        stale = {
            default_agent_color(spec.provider).upper() for spec in PROVIDER_SPECS
        }
        for provider, hex_value in replacements.items():
            self.assertTrue(self.actions.apply_provider_color(provider, hex_value))

        shown = {value.upper() for value in self._strings()}
        self.assertFalse(
            stale & shown, f"stale hexes still on screen: {sorted(stale & shown)}"
        )
        for hex_value in replacements.values():
            self.assertIn(hex_value.upper(), shown, hex_value)

    def test_a_refreshed_pane_says_exactly_what_a_freshly_built_one_says(self) -> None:
        """The invariant behind every bug in this class, stated once.

        This pane is built once and cached, so "refreshed" and "rebuilt" are
        the only two states it can be in, and a view whose reference was
        dropped is precisely a view where those two disagree. Mutate every
        setting the Studio renders, refresh in place, then rebuild from
        scratch and diff every label. No knowledge of WHICH view was dropped
        is needed -- which is what makes this catch the next one.
        """
        from AppKit import NSTextField

        def labels(view, out=None):
            """Every word the pane says: label text AND tooltips. Tooltips
            are in scope on purpose -- the animation thumb's tooltip was one
            of the strings that went stale, and it has no label to betray
            it."""
            out = [] if out is None else out
            for sub in view.subviews():
                if isinstance(sub, NSTextField):
                    value = sub.stringValue()
                    if isinstance(value, str) and value:
                        out.append(value)
                try:
                    tip = sub.toolTip()
                except Exception:
                    tip = None
                if isinstance(tip, str) and tip:
                    out.append(f"tip:{tip}")
                labels(sub, out)
            return out

        colors = self.controller.settings.colors
        for index, spec in enumerate(PROVIDER_SPECS):
            colors = colors.with_agent_color(spec.provider, f"#0{index}1{index}2{index}")
            colors = colors.with_agent_animation(
                spec.provider, PROVIDER_ANIMATION_CHOICES[index % len(PROVIDER_ANIMATION_CHOICES)]
            )
        for index, key in enumerate(colors_module.MODE_COLOR_KEYS):
            # One state deliberately lands on a hex no group names, so the
            # state row's picker chip has to become the ringed "Custom".
            colors = colors.with_mode_color(
                key, "#4433AA" if key == "ask" else CURATED_PALETTE[index + 1]
            )
        for key in colors_module.ANIMATION_MODE_KEYS:
            colors = colors.with_mode_animation(key, "blink")
        self.controller.settings = self.controller.settings.with_colors(colors)
        self.controller.refresh_colors_window()
        refreshed = sorted(labels(self.pane))

        self.controller.settings_panes.pop("color_studio")
        self.controller.ensure_settings_pane("color_studio")
        # Same refresh on both, so the only difference left can be a view
        # that the first pane failed to re-read.
        self.controller.refresh_colors_window()
        rebuilt = sorted(labels(self.controller.settings_panes["color_studio"]))

        self.assertEqual(refreshed, rebuilt)

    # --- the reported instance ---------------------------------------------

    def test_choosing_an_animation_updates_the_sentence_and_the_tooltip(self) -> None:
        sync = self.controller.color_hex_labels[("agent", "codex")]
        before = [label.stringValue() for label in sync.description_labels]
        self.assertEqual(
            before, [PROVIDER_ANIMATION_DESCRIPTIONS[colors_module.PROVIDER_ANIMATION_AUTO]]
        )

        self.assertTrue(self.actions.apply_provider_animation("codex", MOTION_CHASE))

        self.assertEqual(
            [label.stringValue() for label in sync.description_labels],
            [PROVIDER_ANIMATION_DESCRIPTIONS[MOTION_CHASE]],
        )
        self.assertEqual(
            self.actions.animation_thumbs["codex"].toolTip(),
            PROVIDER_ANIMATION_DESCRIPTIONS[MOTION_CHASE],
        )
        # And only that provider moved.
        other = self.controller.color_hex_labels[("agent", "claude")]
        self.assertEqual(
            [label.stringValue() for label in other.description_labels],
            [PROVIDER_ANIMATION_DESCRIPTIONS[colors_module.PROVIDER_ANIMATION_AUTO]],
        )

    def test_an_animation_changed_elsewhere_also_updates_the_sentence(self) -> None:
        """Reset to Defaults and the palette buttons write settings directly
        and then ask the window to catch up -- the sentence is part of what
        has to catch up, not just the popup."""
        self.controller.settings = self.controller.settings.with_colors(
            self.controller.settings.colors.with_agent_animation("grok", MOTION_BLINK)
        )
        self.controller.refresh_colors_window()
        sync = self.controller.color_hex_labels[("agent", "grok")]
        self.assertEqual(
            [label.stringValue() for label in sync.description_labels],
            [PROVIDER_ANIMATION_DESCRIPTIONS[MOTION_BLINK]],
        )

    def test_custom_preset_is_a_controller_no_op_except_refreshing_sync_fields(self) -> None:
        sender = SimpleNamespace(
            selectedItem=lambda: SimpleNamespace(
                representedObject=lambda: {"preset": "custom"}
            )
        )
        before = self.controller.settings
        with (
            patch("sidepulse.status_bar_legacy.save_settings") as save_settings,
            patch(
                "sidepulse.status_bar_legacy.refresh_blend_and_speed_fields"
            ) as refresh_fields,
            patch.object(self.controller, "refresh_colors_window") as refresh_colors_window,
            patch.object(self.controller, "refresh_colors_preview") as refresh_colors_preview,
            patch.object(self.controller, "refresh_") as refresh_controller,
            patch.object(
                self.controller, "push_colors_preview_to_device"
            ) as push_colors_preview_to_device,
        ):
            self.controller.setColorPreset_(sender)

        self.assertIs(self.controller.settings, before)
        save_settings.assert_not_called()
        refresh_fields.assert_called_once_with(self.controller)
        refresh_colors_window.assert_not_called()
        refresh_colors_preview.assert_not_called()
        refresh_controller.assert_not_called()
        push_colors_preview_to_device.assert_not_called()


# --- Defects 5 and 6: the two Codex colours ---------------------------------


def test_a_brand_hex_is_asserted_as_a_literal() -> None:
    """The audit's finding: every brand test compared the model to the
    constant it is built from, so nothing could catch the constant being
    wrong -- and it was. Codex's brand colour is OpenAI's documented Azure,
    the same hex the provider table has always used."""
    brands = dict(BRAND_SEED_COLORS)
    assert brands["Codex"] == "#2B8FFF"
    assert brands["Claude"] == "#D97757"
    assert brands["OpenAI"] == "#10A37F"
    assert brands["Gemini"] == "#4796E3"


def test_the_two_brand_tables_cannot_disagree() -> None:
    """BRAND_SEED_COLORS said Codex was #FF3A00 while PROVIDER_BRAND_COLORS
    said codex was #2B8FFF, so the Codex row drew a "Default" chip AND a
    "Codex" chip, wearing different colours."""
    brands = {name.lower(): hex_value for name, hex_value in BRAND_SEED_COLORS}
    for provider, hex_value in PROVIDER_BRAND_COLORS.items():
        if provider in brands:
            assert brands[provider].upper() == hex_value.upper(), provider


def test_no_state_signal_colour_is_claimed_as_a_brand() -> None:
    """#FF3A00 is this app's ask/blocked colour. It was globally named
    "Codex" and reported as a brand, so the State Colors card's Ask row was
    named after a provider and clicking the chip captioned "Codex" painted
    Codex the alert red."""
    for name, hex_value in STATE_SEED_COLORS:
        assert not is_brand_color(hex_value), hex_value
        assert swatch_name(hex_value) == name, hex_value
    assert swatch_name(ASK_AMBER) == "Ask"
    assert swatch_name(WORKING_CYAN) == "Working"
    assert swatch_name(DONE_GREEN) == "Done"
    assert swatch_name(IDLE_DIM) == "Idle"


def test_no_row_names_the_same_thing_twice() -> None:
    """``brand_swatches_for_provider``'s own docstring promises "never a
    second swatch confusingly wearing the provider's own name next to a
    different hex". For Codex it produced exactly that."""
    for row in provider_color_rows(ColorSettings.defaults()) + mode_color_rows(
        ColorSettings.defaults()
    ):
        offered = [
            swatch
            for group in row.groups
            for swatch in group.swatches
            if not swatch.opens_picker
        ]
        names = [swatch.name for swatch in offered]
        hexes = [swatch.hex.upper() for swatch in offered]
        assert len(names) == len(set(names)), f"{row.key}: duplicate names {names}"
        assert len(hexes) == len(set(hexes)), f"{row.key}: duplicate hexes {hexes}"


def test_a_provider_whose_colour_is_a_brand_does_not_also_get_a_default_chip() -> None:
    row = provider_color_row("codex", ColorSettings.defaults())
    assert [swatch.name for swatch in row.group("brand").swatches] == [
        name for name, _hex in BRAND_SEED_COLORS
    ]
    assert row.current_name == "Codex"
    # Devin's navy really is in neither named set, so it keeps its Default.
    devin = provider_color_row("devin", ColorSettings.defaults())
    assert devin.group("brand").swatches[0].name == "Default"
    assert devin.current_name == "Default"


# --- Defect 11: two agents shipping the same colour -------------------------


def test_no_two_agents_ship_the_same_default_colour() -> None:
    """grok and opencode both shipped systemGray #8E8E93 -- indistinguishable
    on the strip out of the box."""
    assigned = {
        spec.provider: default_agent_color(spec.provider).upper()
        for spec in PROVIDER_SPECS
    }
    duplicates = {
        hex_value
        for hex_value in assigned.values()
        if list(assigned.values()).count(hex_value) > 1
    }
    assert not duplicates, f"providers sharing a colour: {duplicates}"


def test_a_reassigned_default_lands_somewhere_actually_distinct() -> None:
    """Taking merely the next free index would have given opencode systemRed
    #FF3B30 -- ten degrees of hue from the ask signal #FF3A00. Distinct as a
    string, the same light on the strip."""
    reserved = [hex_value for _name, hex_value in STATE_SEED_COLORS]
    reserved += list(PROVIDER_BRAND_COLORS.values())
    for spec in PROVIDER_SPECS:
        colour = default_agent_color(spec.provider)
        if colour.upper() in {value.upper() for value in PROVIDER_BRAND_COLORS.values()}:
            continue  # its own brand colour; distinctness is not the goal there
        for other in reserved:
            assert colors_module._hue_gap(colour, other) > 20.0, (
                f"{spec.provider} {colour} sits {colors_module._hue_gap(colour, other):.1f} "
                f"degrees from {other}"
            )


def test_defaults_still_come_from_the_curated_palette() -> None:
    for spec in PROVIDER_SPECS:
        colour = default_agent_color(spec.provider)
        assert colour in CURATED_PALETTE or colour in PROVIDER_BRAND_COLORS.values()
    assert default_agent_color("some-future-provider") in CURATED_PALETTE


# --- Defect 10: state rows that showed nothing selected ---------------------


def test_every_state_row_rings_the_colour_it_is_wearing() -> None:
    """On a fresh install every State Colors row had ZERO chips ringed: it
    drew CURATED_PALETTE[:6] and not one of the four shipped state colours
    is in that strip."""
    for row in mode_color_rows(ColorSettings.defaults()):
        selected = [
            swatch
            for group in row.groups
            for swatch in group.swatches
            if swatch.selected and not swatch.opens_picker
        ]
        assert len(selected) == 1, f"{row.key}: {len(selected)} chips ringed"
        assert selected[0].group == SWATCH_GROUP_DEFAULT
        assert row.current_name == selected[0].name


def test_a_hand_picked_state_colour_becomes_a_named_ringed_custom_chip() -> None:
    """The picker chip was hardcoded ``name="Pick…"`` with ``selected`` never
    set, so unlike a provider row it could never become "Custom"."""
    colors = ColorSettings.defaults().with_mode_color("done", "#123456")
    row = mode_color_row("done", colors)
    picker = row.picker_swatch
    assert picker.name == "Custom"
    assert picker.selected is True
    assert picker.hex == "#123456"
    assert row.current_name == "Custom"

    # And back on a named colour it is the neutral opener again.
    named = mode_color_row("done", ColorSettings.defaults().with_mode_color("done", CURATED_PALETTE[1]))
    assert named.picker_swatch.name == "Pick…"
    assert named.picker_swatch.selected is False
    assert named.current_name == "Blue"


def test_every_state_swatch_has_a_name_and_a_labelled_group() -> None:
    for row in mode_color_rows(ColorSettings.defaults()):
        assert row.label.strip()
        assert [group.key for group in row.groups] == [
            SWATCH_GROUP_DEFAULT,
            "palette",
            SWATCH_GROUP_CUSTOM,
        ]
        for group in row.groups:
            assert group.label.strip() and group.hint.strip()
            for swatch in group.swatches:
                assert swatch.name.strip()


class StateRowRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests.test_sidepulse import isolate_controller

        isolate_controller(self)
        self.controller.show_settings_window()
        self.controller.ensure_settings_pane("color_studio")
        self.actions = self.controller.studio_actions
        self.controller.refresh_ = lambda _sender: None

    def _ringed(self, key: str) -> list[str]:
        ringed = []
        for (row_key, hex_value), button in self.controller.color_swatches.items():
            if row_key != ("mode", key):
                continue
            try:
                width = button.layer().borderWidth()
            except Exception:
                width = 0.0
            if width:
                ringed.append(hex_value)
        return ringed

    def test_a_fresh_install_shows_which_state_colour_is_selected(self) -> None:
        for key in colors_module.MODE_COLOR_KEYS:
            self.assertEqual(
                self._ringed(key),
                [self.controller.settings.colors.mode_color(key)],
                key,
            )

    def test_the_state_picker_chip_is_held_and_repainted(self) -> None:
        """It was a throwaway local -- built, added, dropped -- so nothing
        could repaint it after a palette button or Reset."""
        sync = self.controller.color_hex_labels[("mode", "ask")]
        self.assertTrue(sync.pickers)
        self.assertEqual(
            [picker.studio_caption.stringValue() for picker in sync.pickers], ["Pick…"]
        )

        self.controller.settings = self.controller.settings.with_colors(
            self.controller.settings.colors.with_mode_color("ask", "#123456")
        )
        self.controller.refresh_colors_window()

        self.assertEqual(
            [picker.studio_caption.stringValue() for picker in sync.pickers], ["Custom"]
        )
        name_label, hex_label = sync.identity_labels[0]
        self.assertEqual(hex_label.stringValue(), "#123456")
        self.assertEqual(name_label.stringValue(), "Custom")

    def test_state_swatches_still_reach_the_controllers_own_selector(self) -> None:
        """These chips moved into the shared group renderer, whose default
        action target is the Studio's NSObject -- but selectModeColorSwatch:
        lives on StatusBarController."""
        button = self.controller.color_swatches[(("mode", "working"), CURATED_PALETTE[1])]
        self.assertIs(button.target(), self.controller)
        self.assertEqual(button.action(), "selectModeColorSwatch:")
        self.assertEqual(button.representedObject()["key"], "working")


# --- Defects 8 and 9: animations you could neither name nor try -------------


class AnimationControlTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests.test_sidepulse import isolate_controller

        isolate_controller(self)
        self.controller.show_settings_window()
        self.controller.ensure_settings_pane("color_studio")
        self.actions = self.controller.studio_actions
        self.controller.refresh_ = lambda _sender: None
        self.held: list[object] = []
        self.controller.virtual_status_device = SimpleNamespace(
            hold_preview_program=lambda program, **kwargs: self.held.append(program),
            release_preview_program=lambda: (self.held.append("release"), True)[1],
        )

    def test_every_state_animation_thumb_carries_a_visible_name(self) -> None:
        """They were identified by setToolTip_ alone and told apart only by
        position -- the exact failure the Studio exists to end."""
        from sidepulse.status_bar import ANIMATION_STYLE_DISPLAY_LABELS

        found = 0
        for key, thumbs in self.controller.colors_animation_thumbs.items():
            for style, thumb in thumbs.items():
                caption = getattr(thumb, "studio_caption", None)
                self.assertIsNotNone(caption, f"{key}/{style} has no caption")
                self.assertEqual(
                    caption.stringValue(),
                    ANIMATION_STYLE_DISPLAY_LABELS.get(style, style.title()),
                )
                self.assertIsNotNone(caption.superview())
                found += 1
        self.assertGreater(found, 0)

    def test_hover_any_animation_is_true_of_every_animation_control(self) -> None:
        """Two body strings promised "hover any color or animation to try it
        here first" while zero animation controls had hover wiring."""
        self.assertEqual(
            sorted(self.actions.animation_hover_areas),
            sorted(spec.provider for spec in PROVIDER_SPECS),
        )
        expected = {
            (key, style)
            for key, thumbs in self.controller.colors_animation_thumbs.items()
            for style in thumbs
        }
        self.assertEqual(set(self.actions.mode_animation_hover_areas), expected)
        for area in list(self.actions.animation_hover_areas.values()) + list(
            self.actions.mode_animation_hover_areas.values()
        ):
            self.assertTrue(callable(area.hover_enter))
            self.assertTrue(callable(area.hover_exit))

    def test_hovering_a_state_animation_plays_it_and_leaving_takes_it_back(self) -> None:
        area = self.actions.mode_animation_hover_areas[("working", "roll")]
        area.hover_enter(area)
        self.assertTrue(self.held)
        self.assertTrue(self.actions.preview_session.previewing)
        self.assertEqual(
            self.controller.studio_compare["caption"].stringValue(),
            "Trying Working: Chase",
        )
        # Hovering is not choosing.
        self.assertEqual(
            self.controller.settings.colors.animation_style("working"),
            ColorSettings.defaults().animation_style("working"),
        )
        area.hover_exit(area)
        self.assertEqual(self.held[-1], "release")
        self.assertFalse(self.actions.preview_session.previewing)

    def test_hovering_a_providers_rhythm_plays_that_provider_alone(self) -> None:
        area = self.actions.animation_hover_areas["claude"]
        area.hover_enter(area)
        self.assertEqual(
            self.controller.studio_compare["caption"].stringValue(),
            "Trying Claude: Automatic",
        )
        self.assertEqual(
            self.held[0],
            colors_module.studio_preview_program(
                self.controller.settings.colors,
                statuses=colors_module.provider_preview_statuses("claude"),
            ),
        )
        area.hover_exit(area)


# --- Defects 13, 14, 15 -----------------------------------------------------


class StudioChromeTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests.test_sidepulse import isolate_controller

        isolate_controller(self)
        self.controller.show_settings_window()
        self.controller.ensure_settings_pane("color_studio")
        self.actions = self.controller.studio_actions
        self.pane = self.controller.settings_panes["color_studio"]
        self.controller.refresh_ = lambda _sender: None

    def test_the_studio_has_no_display_size_headline(self) -> None:
        """make_section_title's docstring states the rule: "the sidebar
        selection already names the pane, so in-content titles are quiet
        organizers, not display headlines". This pane shipped the only 15pt
        title in the app, inside a glass panel."""
        oversized = []

        def walk(view):
            for sub in view.subviews():
                try:
                    text, font = sub.stringValue(), sub.font()
                except Exception:
                    text = font = None
                if isinstance(text, str) and text and font is not None:
                    if font.pointSize() > 13.0:
                        oversized.append((font.pointSize(), text))
                walk(sub)

        walk(self.pane)
        self.assertEqual(oversized, [])

    def test_the_pane_still_says_what_it_is(self) -> None:
        titles = []

        def walk(view):
            for sub in view.subviews():
                try:
                    text = sub.stringValue()
                except Exception:
                    text = None
                if isinstance(text, str) and text:
                    titles.append(text)
                walk(sub)

        walk(self.pane)
        self.assertIn("Color & Animation Studio", titles)

    def test_a_tip_anchor_reveals_its_own_section_before_being_flashed(self) -> None:
        """openTipPane_ scrolls the anchor into view and flashes it. With the
        Studio left on Animations, the brand-colors anchor was scrolled to and
        flashed while hidden -- the tip silently landed on nothing."""
        self.actions.select_section("animations")
        anchor = self.controller.tip_anchor_views["brand_colors"]
        self.assertTrue(self.actions.section_views["colors"].isHidden())

        anchor.scrollRectToVisible_(anchor.bounds())

        self.assertFalse(self.actions.section_views["colors"].isHidden())
        self.assertEqual(self.actions.section, "colors")

    def test_the_hardware_preview_flag_is_read_one_way_everywhere(self) -> None:
        """It was read with default False where the answer decided whether to
        push to a device, and default True where it decided how to draw the
        switch: a controller without the attribute would have shown a switch
        that was ON while behaving as OFF."""
        from sidepulse.settings_window import hardware_preview_enabled

        self.assertFalse(hardware_preview_enabled(SimpleNamespace()))
        self.assertFalse(hardware_preview_enabled(SimpleNamespace(color_preview_enabled=False)))
        self.assertTrue(hardware_preview_enabled(SimpleNamespace(color_preview_enabled=True)))

        del self.controller.color_preview_enabled
        pushed: list[bool] = []
        self.controller.push_colors_preview_to_device = lambda: pushed.append(True)
        self.controller.refresh_colors_window()
        self.assertEqual(self.controller.color_fields["live_toggle"].state(), 0)
        self.actions.apply_provider_color("claude", "#10A37F")
        self.assertEqual(pushed, [])

    def test_the_preview_copy_does_not_promise_a_default_it_does_not_set(self) -> None:
        """"Your hardware is never touched unless you ask for it here" sat
        directly above a switch that defaults ON. The copy now describes the
        switch, which is true whichever way the default goes."""
        strings = []

        def walk(view):
            for sub in view.subviews():
                try:
                    text = sub.stringValue()
                except Exception:
                    text = None
                if isinstance(text, str) and text:
                    strings.append(text)
                walk(sub)

        walk(self.pane)
        blob = " ".join(strings)
        self.assertNotIn("never touched unless you ask", blob)
        self.assertIn("only while the switch below is on", blob)
