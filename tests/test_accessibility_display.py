from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidepulse.accessibility_display import (
    AccessibilityDisplayPreferences,
    read_accessibility_display_preferences,
    refresh_accessibility_display_preferences,
)


class FakeWorkspace:
    def __init__(self, values: tuple[bool, bool, bool, bool]) -> None:
        self._values = values
        self.calls: list[str] = []

    def _read(self, selector: str, index: int) -> bool:
        self.calls.append(selector)
        return self._values[index]

    def accessibilityDisplayShouldReduceMotion(self) -> bool:
        return self._read("reduce_motion", 0)

    def accessibilityDisplayShouldReduceTransparency(self) -> bool:
        return self._read("reduce_transparency", 1)

    def accessibilityDisplayShouldIncreaseContrast(self) -> bool:
        return self._read("increase_contrast", 2)

    def accessibilityDisplayShouldDifferentiateWithoutColor(self) -> bool:
        return self._read("differentiate_without_color", 3)


def test_preferences_default_to_standard_display_behavior_and_are_frozen() -> None:
    preferences = AccessibilityDisplayPreferences()

    assert preferences == AccessibilityDisplayPreferences(
        reduce_motion=False,
        reduce_transparency=False,
        increase_contrast=False,
        differentiate_without_color=False,
    )
    with pytest.raises(FrozenInstanceError):
        preferences.reduce_motion = True  # type: ignore[misc]


def test_reader_maps_each_workspace_selector_once() -> None:
    workspace = FakeWorkspace((True, False, True, False))

    preferences = read_accessibility_display_preferences(workspace)

    assert preferences == AccessibilityDisplayPreferences(
        reduce_motion=True,
        reduce_transparency=False,
        increase_contrast=True,
        differentiate_without_color=False,
    )
    assert workspace.calls == [
        "reduce_motion",
        "reduce_transparency",
        "increase_contrast",
        "differentiate_without_color",
    ]


class MissingSelectorWorkspace:
    def accessibilityDisplayShouldReduceMotion(self) -> bool:
        return False


class ThrowingSelectorWorkspace(FakeWorkspace):
    def accessibilityDisplayShouldIncreaseContrast(self) -> bool:
        raise RuntimeError("selector failed")


class NonBooleanSelectorWorkspace(FakeWorkspace):
    def accessibilityDisplayShouldReduceTransparency(self) -> int:
        return 1


@pytest.mark.parametrize(
    "workspace",
    [
        MissingSelectorWorkspace(),
        ThrowingSelectorWorkspace((False, False, False, False)),
        NonBooleanSelectorWorkspace((False, False, False, False)),
    ],
)
def test_initial_read_failure_uses_conservative_preferences(workspace: object) -> None:
    assert refresh_accessibility_display_preferences(None, workspace) == (
        AccessibilityDisplayPreferences(
            reduce_motion=True,
            reduce_transparency=True,
            increase_contrast=True,
            differentiate_without_color=True,
        )
    )


@pytest.mark.parametrize(
    "workspace",
    [
        MissingSelectorWorkspace(),
        ThrowingSelectorWorkspace((False, False, False, False)),
        NonBooleanSelectorWorkspace((False, False, False, False)),
    ],
)
def test_later_read_failure_retains_last_known_preferences(workspace: object) -> None:
    previous = AccessibilityDisplayPreferences(
        reduce_motion=False,
        reduce_transparency=True,
        increase_contrast=False,
        differentiate_without_color=True,
    )

    assert refresh_accessibility_display_preferences(previous, workspace) is previous


def test_successful_refresh_returns_the_new_snapshot() -> None:
    previous = AccessibilityDisplayPreferences(reduce_motion=True)

    refreshed = refresh_accessibility_display_preferences(
        previous,
        FakeWorkspace((False, True, True, False)),
    )

    assert refreshed == AccessibilityDisplayPreferences(
        reduce_motion=False,
        reduce_transparency=True,
        increase_contrast=True,
        differentiate_without_color=False,
    )

