"""Accessible Alcove controls shared by the native Settings window.

The legacy Settings module remains the compatibility boundary for status-bar
callers and test injection. This module owns the cohesive AppKit presentation
seam: constructing the Alcove rows and applying a resolved confidence
projection to controls that already exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import native_ui
from .alcove_observation import AlcoveCaptureStatus, AlcoveConfidenceProjection

ALCOVE_PERMISSION_BUTTON_LABEL = "Open Screen Recording Settings"
ALCOVE_PERMISSION_BUTTON_HELP = (
    "Grant Screen Recording access so JR-Bar can follow Alcove's capsule."
)


@dataclass(frozen=True)
class AlcoveSettingsControls:
    """The rows and controls the parent Screen Bar pane arranges and retains."""

    wing_status_row: object
    wing_status_label: object
    follow_row: object
    follow_switch: object
    status_row: object
    status_label: object
    permission_row: object
    permission_button: object


def _apply_projection_metadata(label, projection: AlcoveConfidenceProjection) -> None:
    label.setAccessibilityLabel_(projection.accessibility_value)
    label.setAccessibilityValue_(projection.accessibility_value)
    label.setAccessibilityHelp_(projection.accessibility_help)


def _apply_permission_button_metadata(button) -> None:
    button.setAccessibilityLabel_(ALCOVE_PERMISSION_BUTTON_LABEL)
    button.setAccessibilityHelp_(ALCOVE_PERMISSION_BUTTON_HELP)


def build_alcove_settings_controls(
    target,
    *,
    actions,
    projection: AlcoveConfidenceProjection,
    wing_status_text: str,
) -> AlcoveSettingsControls:
    """Build the complete Alcove Settings control group.

    The parent owns the surrounding Screen Bar card and inserts the full-screen
    row between ``follow_row`` and ``status_row``. Keeping construction here
    ensures visible copy, action visibility, and accessibility metadata cannot
    drift between initial rendering and refreshes.
    """
    wing_status_label = native_ui.make_wrapping_label(
        wing_status_text,
        secondary=True,
        size=11.0,
        max_width=360.0,
    )
    wing_status_row = native_ui.make_row("Menu bar glow", wing_status_label)

    follow_row, follow_switch = native_ui.make_switch_row(
        "Match Alcove's width automatically",
        target,
        "toggleScreenBarFollowAlcove:",
        help_text=(
            "The bracket tracks Alcove's visible capsule — widening "
            "for a timer or now-playing pill and easing back when it "
            "collapses, hugging it within a couple of points. While a "
            "capsule is visible this supersedes the Bar Size gap, so "
            "Automatic stays automatic; a manual wing length still "
            "wins. Needs Screen Recording permission; without it the "
            "bar quietly keeps its classic size."
        ),
    )

    status_label = native_ui.make_wrapping_label(
        projection.message,
        secondary=True,
        size=11.0,
        max_width=360.0,
    )
    _apply_projection_metadata(status_label, projection)
    status_row = native_ui.make_row("Alcove following", status_label)
    # make_row names the control after its row subject. Restore the dynamic
    # state label so VoiceOver announces the confidence state as well.
    _apply_projection_metadata(status_label, projection)

    permission_button = native_ui.make_button(
        "Open Screen Recording Settings…",
        actions,
        "grantScreenRecording:",
    )
    _apply_permission_button_metadata(permission_button)
    permission_button.setHidden_(not projection.needs_permission_action)
    permission_row = native_ui.make_row("Screen Recording", permission_button)
    # make_row also names buttons after their row subjects.
    _apply_permission_button_metadata(permission_button)
    permission_row.setHidden_(not projection.needs_permission_action)

    actions.status_label = status_label
    actions.permission_button = permission_button
    return AlcoveSettingsControls(
        wing_status_row=wing_status_row,
        wing_status_label=wing_status_label,
        follow_row=follow_row,
        follow_switch=follow_switch,
        status_row=status_row,
        status_label=status_label,
        permission_row=permission_row,
        permission_button=permission_button,
    )


def refresh_alcove_settings_controls(
    target,
    *,
    projection: AlcoveConfidenceProjection,
    wing_status_text: str,
) -> None:
    """Apply one resolved confidence projection to retained Settings controls."""
    fields = getattr(target, "settings_fields", None) or {}
    buttons = getattr(target, "settings_buttons", None) or {}
    label = fields.get("alcove_follow_status")
    if label is not None:
        label.setStringValue_(projection.message)
        _apply_projection_metadata(label, projection)
    button = buttons.get("alcove_screen_recording_permission")
    if button is not None:
        button.setHidden_(not projection.needs_permission_action)
    action_row = fields.get("alcove_permission_row")
    if action_row is not None:
        action_row.setHidden_(not projection.needs_permission_action)
    wing_label = fields.get("screen_bar_wing_status")
    if wing_label is not None:
        wing_label.setStringValue_(wing_status_text)


def projection_compatibility_status(
    projection: AlcoveConfidenceProjection,
) -> AlcoveCaptureStatus | None:
    """Translate the confidence ladder for legacy raw-status callers."""
    return {
        "fresh": AlcoveCaptureStatus.CAPTURED,
        "stale": AlcoveCaptureStatus.CAPTURED,
        "permission_denied": AlcoveCaptureStatus.SCREEN_RECORDING_DENIED,
        "disconnected": AlcoveCaptureStatus.WINDOW_UNAVAILABLE,
        "unsupported": AlcoveCaptureStatus.IMAGE_UNUSABLE,
        "not_following": AlcoveCaptureStatus.NOT_FOLLOWING,
        "recovering": AlcoveCaptureStatus.CAPTURE_FAILED,
    }.get(projection.state.value)


def permission_alert_title(*, needs_permission: bool) -> str:
    """Return the single Alcove failure row that has a direct menu action."""
    if not needs_permission:
        return ""
    return "⚠ Alcove following needs Screen Recording — grant it in Settings…"
