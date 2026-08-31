from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidepulse import colors as colors_module
from sidepulse import settings_window_controls
from sidepulse.effect_registry import provider_animation_effects
from sidepulse.effect_selection import (
    BLEND_MODE_OPTIONS,
    COLOR_PRESET_OPTIONS,
    PREVIEW_SCENARIO_OPTIONS,
    PROVIDER_ANIMATION_OPTIONS,
    EffectOption,
    EffectSelectionDisposition,
    effect_pack_options,
    plan_blend_mode_selection,
    plan_color_preset_selection,
    plan_provider_animation_selection,
    preview_scenario_from_payload,
    selected_option_index,
)


def _option_rows(options: tuple[EffectOption, ...]) -> list[tuple[str, str, str]]:
    return [(option.value, option.label, option.description) for option in options]


class _DictSubclass(dict):
    pass


class _FakePopup:
    def __init__(self, selected_index: int | None = None) -> None:
        self.titles: list[str] = []
        self.represented_objects: list[object] = []
        self.tooltips: list[str] = []
        self.selected_index = selected_index

    def addItemWithTitle_(self, title) -> None:
        self.titles.append(str(title))
        self.represented_objects.append(None)
        self.tooltips.append("")

    def lastItem(self):
        popup = self
        index = len(self.titles) - 1

        class _Item:
            def setRepresentedObject_(self, value) -> None:
                popup.represented_objects[index] = value

            def setToolTip_(self, value) -> None:
                popup.tooltips[index] = str(value)

            def representedObject(self):
                return popup.represented_objects[index]

        return _Item()

    def numberOfItems(self) -> int:
        return len(self.titles)

    def itemAtIndex_(self, index: int):
        popup = self

        class _Item:
            def representedObject(self):
                return popup.represented_objects[index]

        return _Item()

    def selectItemAtIndex_(self, index: int) -> None:
        self.selected_index = index


def test_effect_options_are_immutable_and_catalogs_pin_current_order_and_copy() -> None:
    with pytest.raises(FrozenInstanceError):
        EffectOption("value", "Label").value = "changed"  # type: ignore[misc]

    assert _option_rows(BLEND_MODE_OPTIONS) == [
        (
            value,
            colors_module.BLEND_MODE_LABELS[value],
            colors_module.BLEND_MODE_DESCRIPTIONS[value],
        )
        for value in (
            "round_robin",
            "relay",
            "spatial_split",
            "color_blend",
            "cycle",
            "classic",
        )
    ]
    assert _option_rows(COLOR_PRESET_OPTIONS) == [
        ("custom", "Custom", ""),
        ("calm", "Calm", "Slow, dim, no celebrations. Light you can work next to."),
        (
            "informative",
            "Balanced",
            "Clear motion at a normal brightness. The default.",
        ),
        ("everything", "Lively", "Fast, bright, and it celebrates. Full show."),
    ]
    assert _option_rows(PREVIEW_SCENARIO_OPTIONS) == [
        ("live", "Live Activity", ""),
        ("quiet", "Quiet (nothing running)", ""),
        ("one_working", "One Agent Working", ""),
        ("one_needs_you", "One Agent Needs You", ""),
        ("same_provider_duo", "Two Sessions, Same Agent", ""),
        ("pair", "Two Different Agents", ""),
        ("full_team", "Full Team, Mixed States", ""),
        ("busy_team", "Busy Team (more agents than LEDs)", ""),
    ]
    assert [(option.value, option.label) for option in PROVIDER_ANIMATION_OPTIONS] == [
        ("auto", "Automatic"),
        ("breathe", "Breathe"),
        ("duotone", "Duotone"),
        ("chase", "Chase"),
        ("gradient", "Gradient"),
        ("heartbeat", "Heartbeat"),
        ("scanner", "Scanner"),
        ("kitt", "Knight Rider"),
        ("comet", "Comet"),
        ("flicker", "Flicker"),
        ("stack", "Stack"),
        ("twinkle", "Twinkle"),
        ("drift", "Drift"),
        ("converge", "Converge"),
        ("aurora", "Aurora"),
        ("tide", "Tide"),
        ("marquee", "Marquee"),
        ("steady", "Steady"),
        ("blink", "Blink"),
    ]
    assert [option.description for option in PROVIDER_ANIMATION_OPTIONS] == [
        colors_module.PROVIDER_ANIMATION_DESCRIPTIONS[value]
        for value in colors_module.PROVIDER_ANIMATION_CHOICES
    ]


def test_provider_animation_options_are_projected_from_the_shared_registry() -> None:
    assert _option_rows(PROVIDER_ANIMATION_OPTIONS) == [
        (effect.identifier, effect.label, effect.description)
        for effect in provider_animation_effects()
    ]


def test_data_only_pack_projects_to_namespaced_preview_options() -> None:
    options = effect_pack_options(
        {
            "id": "ambient",
            "name": "Ambient",
            "version": 2,
            "effects": [
                {
                    "id": "slow-wave",
                    "label": "Slow Wave",
                    "description": "A quiet travelling glow.",
                }
            ],
            "safety": {"data_only": True, "network": False},
            "accessibility": {
                "reduced_motion": True,
                "high_contrast": True,
            },
        }
    )

    assert _option_rows(options) == [
        ("pack:ambient:slow-wave", "Slow Wave", "A quiet travelling glow.")
    ]


def test_effect_popup_construction_uses_the_shared_catalogs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, str, _FakePopup]] = []

    def fake_make_popup_button(target, selector: str) -> _FakePopup:
        popup = _FakePopup()
        calls.append((target, selector, popup))
        return popup

    monkeypatch.setattr(
        settings_window_controls.native_ui,
        "make_popup_button",
        fake_make_popup_button,
    )

    target = object()
    blend_popup = settings_window_controls.make_blend_mode_popup(target)
    preset_popup = settings_window_controls.make_color_preset_popup(target)
    preview_popup = settings_window_controls.make_preview_scenario_popup(target)

    assert [selector for _target, selector, _popup in calls] == [
        "setBlendMode:",
        "setColorPreset:",
        "setPreviewScenario:",
    ]
    assert [popup.titles for _target, _selector, popup in calls] == [
        [option.label for option in BLEND_MODE_OPTIONS],
        [option.label for option in COLOR_PRESET_OPTIONS],
        [option.label for option in PREVIEW_SCENARIO_OPTIONS],
    ]
    assert [popup.represented_objects for _target, _selector, popup in calls] == [
        [{"blend_mode": option.value} for option in BLEND_MODE_OPTIONS],
        [{"preset": option.value} for option in COLOR_PRESET_OPTIONS],
        [{"scenario": option.value} for option in PREVIEW_SCENARIO_OPTIONS],
    ]
    assert blend_popup is calls[0][2]
    assert preset_popup is calls[1][2]
    assert preview_popup is calls[2][2]
    assert preset_popup.tooltips == [
        "",
        *[option.description for option in COLOR_PRESET_OPTIONS[1:]],
    ]


@pytest.mark.parametrize("value, expected", [("b", 1), ("missing", None)])
def test_selected_option_index_returns_first_known_match_or_none(
    value: str, expected: int | None
) -> None:
    options = (
        EffectOption("a", "First"),
        EffectOption("b", "Second"),
        EffectOption("b", "Duplicate"),
    )
    assert selected_option_index(options, value) == expected


@pytest.mark.parametrize(
    "payload",
    [None, "live", [], {}, {"scenario": None}, {"scenario": 1}, {"scenario": "other"}],
)
def test_preview_scenario_validation_fails_closed(payload: object) -> None:
    assert preview_scenario_from_payload(payload) is None


def test_preview_scenario_validation_returns_value_without_touching_settings() -> None:
    colors = colors_module.ColorSettings.defaults()
    before = colors.to_dict()
    assert preview_scenario_from_payload({"scenario": "one_working"}) == "one_working"
    assert colors.to_dict() == before


def test_preview_scenario_validation_accepts_dict_subclasses() -> None:
    payload = _DictSubclass(scenario="one_working")
    assert preview_scenario_from_payload(payload) == "one_working"


@pytest.mark.parametrize(
    "selector, options, key, value, expected_index",
    [
        (settings_window_controls.select_blend_mode, BLEND_MODE_OPTIONS, "blend_mode", "relay", 1),
        (
            settings_window_controls.select_color_preset,
            COLOR_PRESET_OPTIONS,
            "preset",
            "everything",
            3,
        ),
        (
            settings_window_controls.select_preview_scenario,
            PREVIEW_SCENARIO_OPTIONS,
            "scenario",
            "full_team",
            6,
        ),
    ],
)
def test_effect_popup_selection_helpers_round_trip_known_values(
    selector,
    options: tuple[EffectOption, ...],
    key: str,
    value: str,
    expected_index: int,
) -> None:
    popup = _FakePopup(selected_index=0)
    for option in options:
        popup.addItemWithTitle_(option.label)
        popup.lastItem().setRepresentedObject_({key: option.value})

    selector(popup, value)

    assert popup.selected_index == expected_index


@pytest.mark.parametrize(
    "selector, options, key",
    [
        (settings_window_controls.select_blend_mode, BLEND_MODE_OPTIONS, "blend_mode"),
        (settings_window_controls.select_color_preset, COLOR_PRESET_OPTIONS, "preset"),
        (settings_window_controls.select_preview_scenario, PREVIEW_SCENARIO_OPTIONS, "scenario"),
    ],
)
def test_effect_popup_selection_helpers_ignore_unknown_values(
    selector,
    options: tuple[EffectOption, ...],
    key: str,
) -> None:
    popup = _FakePopup(selected_index=2)
    for option in options:
        popup.addItemWithTitle_(option.label)
        popup.lastItem().setRepresentedObject_({key: option.value})

    selector(popup, "missing")

    assert popup.selected_index == 2


@pytest.mark.parametrize(
    "planner, payload",
    [
        (plan_color_preset_selection, None),
        (plan_color_preset_selection, "calm"),
        (plan_color_preset_selection, {}),
        (plan_color_preset_selection, {"preset": None}),
        (plan_color_preset_selection, {"preset": 1}),
        (plan_color_preset_selection, {"preset": "other"}),
        (plan_blend_mode_selection, None),
        (plan_blend_mode_selection, "round_robin"),
        (plan_blend_mode_selection, {}),
        (plan_blend_mode_selection, {"blend_mode": None}),
        (plan_blend_mode_selection, {"blend_mode": 1}),
        (plan_blend_mode_selection, {"blend_mode": "other"}),
        (plan_provider_animation_selection, None),
        (plan_provider_animation_selection, "auto"),
        (plan_provider_animation_selection, {}),
        (plan_provider_animation_selection, {"provider": None, "motion": "auto"}),
        (plan_provider_animation_selection, {"provider": "", "motion": "auto"}),
        (plan_provider_animation_selection, {"provider": 1, "motion": "auto"}),
        (plan_provider_animation_selection, {"provider": "unknown", "motion": "auto"}),
        (plan_provider_animation_selection, {"provider": "claude"}),
        (plan_provider_animation_selection, {"provider": "claude", "motion": None}),
        (plan_provider_animation_selection, {"provider": "claude", "motion": 1}),
        (plan_provider_animation_selection, {"provider": "claude", "motion": "other"}),
    ],
)
def test_selection_plans_fail_closed_on_malformed_payloads(planner, payload: object) -> None:
    colors = colors_module.ColorSettings.defaults()
    plan = planner(colors, payload)
    assert plan.disposition is EffectSelectionDisposition.INVALID
    assert plan.value is None
    assert plan.provider is None
    assert plan.colors is colors


def test_custom_preset_is_valid_no_change_while_real_preset_applies() -> None:
    colors = colors_module.ColorSettings.defaults()

    custom = plan_color_preset_selection(colors, {"preset": "custom"})
    assert custom.disposition is EffectSelectionDisposition.NO_CHANGE
    assert custom.value == "custom"
    assert custom.colors is colors

    calm = plan_color_preset_selection(colors, {"preset": "calm"})
    assert calm.disposition is EffectSelectionDisposition.APPLY
    assert calm.value == "calm"
    assert calm.colors == colors_module.apply_preset(colors, "calm")
    assert calm.colors is not colors


def test_valid_blend_and_provider_animation_plans_apply_immutable_settings() -> None:
    colors = colors_module.ColorSettings.defaults()

    blend = plan_blend_mode_selection(colors, {"blend_mode": "relay"})
    assert blend.disposition is EffectSelectionDisposition.APPLY
    assert blend.value == "relay"
    assert blend.colors == colors.with_blend_mode("relay")
    assert blend.colors is not colors

    motion = plan_provider_animation_selection(
        colors, {"provider": "claude", "motion": "chase"}
    )
    assert motion.disposition is EffectSelectionDisposition.APPLY
    assert motion.value == "chase"
    assert motion.provider == "claude"
    assert motion.colors == colors.with_agent_animation("claude", "chase")
    assert motion.colors is not colors
    assert colors.agent_animation("claude") == colors_module.PROVIDER_ANIMATION_AUTO


def test_provider_animation_plan_accepts_dict_subclasses() -> None:
    colors = colors_module.ColorSettings.defaults()
    payload = _DictSubclass(provider="claude", motion="chase")

    plan = plan_provider_animation_selection(colors, payload)

    assert plan.disposition is EffectSelectionDisposition.APPLY
    assert plan.provider == "claude"
    assert plan.value == "chase"
