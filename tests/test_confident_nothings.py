"""A sweep for the Alcove defect, everywhere else it already lived.

The Alcove bug was never really about Alcove. It was a call site where a
missing permission, a real failure, a not-yet-loaded state and a genuine
empty result all came back as the SAME falsy value, so no surface could
tell them apart and the user was shown a confident-looking nothing --
a switch reading ON over a feature that had never once run.

These pin the other instances of it:

* Auto-Brightness claims to be on while the screen-brightness technique
  it depends on is unavailable, and the LED sync has silently been using
  the manual value on every tick.
* The Calendar and Reminders switches stay ON forever after macOS
  refuses (or after a refusal a year ago that macOS will never re-ask),
  and the only mention of it was a status line that is gone by the next
  time anyone opens the window.
* "Extend glow along the menu bar" does nothing at all on any display
  without a notch -- every external monitor, every non-notched Mac.
* Cloud agents said "Enabled -- starts with the app." in the one case
  where the bind had ALREADY failed minutes ago.
* "No peers found yet." was printed when Tailscale was not installed,
  which is not a "yet".
* The Codex installer wrote a hook and reported success when the trust
  handshake never happened, leaving a hook Codex refuses to execute.
* A doctor probe that raised before reading anything still rendered a
  denominator, e.g. "unavailable [0/32]" out of 32 paths never examined.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_sidepulse import isolate_controller

from sidepulse import doctor as doctor_module
from sidepulse import settings_window
from sidepulse.doctor import (
    DiagnosticCheck,
    DiagnosticCode,
    DiagnosticProbe,
    collect_diagnostics,
    render_diagnostic_result,
)
from sidepulse.install import (
    CodexHookTrust,
    CodexHookTrustStatus,
    InstallResult,
    install_codex_hooks,
    resolve_codex_hook_trust,
)
from sidepulse.virtual_device import ScreenBarWingState, screen_bar_wing_state

# --- Auto-Brightness -----------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_probe_caches():
    settings_window.reset_screen_brightness_cache()
    settings_window.reset_event_access_cache()
    yield
    settings_window.reset_screen_brightness_cache()
    settings_window.reset_event_access_cache()


def test_auto_brightness_does_not_claim_to_work_without_a_reading() -> None:
    """The checkbox was the only evidence, and it is not evidence.

    display_brightness.py says in its own first paragraph that Apple can
    remove this technique without notice; when that happens every
    auto-brightness device keeps its MANUAL brightness on every tick and
    nothing anywhere says so.
    """
    honest = settings_window.calibration_summary_text(
        True, 1.0, 1.0, 1.0, brightness_readable=False
    )
    working = settings_window.calibration_summary_text(
        True, 1.0, 1.0, 1.0, brightness_readable=True
    )

    assert honest != working
    assert "won't report screen brightness" in honest
    assert working == "Auto-Brightness on"


def test_auto_brightness_summary_resolves_the_reading_for_its_caller() -> None:
    """status_bar's refresh path calls this positionally and cannot be
    edited from here, so the probe has to happen inside the function."""
    with patch.object(
        settings_window.display_brightness,
        "current_screen_brightness_fraction",
        side_effect=settings_window.display_brightness.DisplayBrightnessUnavailableError(
            "gone"
        ),
    ):
        text = settings_window.calibration_summary_text(True, 1.0, 1.0, 1.0)

    assert "won't report screen brightness" in text


def test_a_switched_off_auto_brightness_says_nothing_about_the_reading() -> None:
    """An unavailable reading is only news while the switch is ON."""
    with patch.object(
        settings_window, "screen_brightness_readable", return_value=False
    ):
        text = settings_window.calibration_summary_text(False, 1.0, 1.0, 1.0)

    assert text == "Auto-Brightness off"


def test_calibration_percentages_survive_the_honest_prefix() -> None:
    text = settings_window.calibration_summary_text(
        True, 1.0, 0.5, 1.0, brightness_readable=False
    )

    assert text.endswith("R100% G50% B100%")


# --- Calendar and Reminders permissions ----------------------------------


def _controller_settings(**changes):
    values = {"calendar_alerts_enabled": True, "reminder_alerts_enabled": True}
    values.update(changes)
    return SimpleNamespace(settings=SimpleNamespace(**values))


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        ("authorized", "Granted"),
        ("denied", "Denied"),
        ("not_determined", "has not been asked"),
        ("unavailable", "unavailable on this Mac"),
    ),
)
def test_every_calendar_access_answer_reads_differently(status, expected) -> None:
    """Four EventKit answers, four sentences.

    Before this row the switch said ON for all four, and a denial that
    macOS will never re-ask looked exactly like a working feature with
    no events today.
    """
    target = _controller_settings()
    with patch.object(settings_window, "_event_access_status", return_value=status):
        text = settings_window.calendar_access_status_text(target)

    assert expected in text


def test_eventkit_missing_is_not_reported_as_a_denial() -> None:
    """calendar_watch RAISES for "EventKit cannot be used here".

    Collapsing that into "denied" would send the owner to a Privacy pane
    to switch on a row that will never appear.
    """
    watch = SimpleNamespace(
        authorization_status=lambda: (_ for _ in ()).throw(RuntimeError("no EventKit"))
    )

    assert settings_window._event_access_status("probe", watch) == "unavailable"


def test_reminders_access_names_its_own_privacy_pane() -> None:
    target = _controller_settings()
    with patch.object(settings_window, "_event_access_status", return_value="denied"):
        text = settings_window.reminders_access_status_text(target)

    assert "Reminders" in text
    assert "Calendars" not in text


def test_an_off_switch_does_not_report_a_permission_at_all() -> None:
    """And does not import EventKit to find that out.

    `authorization_status` pulls the framework in on first use; a pane
    read for a feature the owner turned off has no business doing that.
    """
    target = _controller_settings(calendar_alerts_enabled=False)
    with patch.object(
        settings_window,
        "_event_access_status",
        side_effect=AssertionError("probed a switched-off feature"),
    ):
        text = settings_window.calendar_access_status_text(target)

    assert text == "Not used while this is off."


def test_the_event_access_rows_refresh_with_the_rest_of_the_window() -> None:
    """Panes build once. Granting access in System Settings stales them."""

    class _Label:
        def __init__(self) -> None:
            self.value = "stale"

        def setStringValue_(self, value: str) -> None:
            self.value = value

    calendar, reminders = _Label(), _Label()
    target = SimpleNamespace(
        settings=SimpleNamespace(
            calendar_alerts_enabled=True, reminder_alerts_enabled=True
        ),
        settings_fields={
            "calendar_access_status": calendar,
            "reminders_access_status": reminders,
        },
    )

    with patch.object(settings_window, "_event_access_status", return_value="denied"):
        settings_window.refresh_event_access_controls(target)

    assert "Denied" in calendar.value
    assert "Denied" in reminders.value


# --- Extend glow along the menu bar --------------------------------------


class _Area:
    def __init__(self, x: float, width: float) -> None:
        self.origin = SimpleNamespace(x=x)
        self.size = SimpleNamespace(width=width)


class _Screen:
    """A screen with (or without) the auxiliary areas beside a notch."""

    def __init__(self, *, left: float, right: float) -> None:
        self._left = _Area(0.0, left)
        self._right = _Area(left + 200.0, right)

    def auxiliaryTopLeftArea(self):
        return self._left

    def auxiliaryTopRightArea(self):
        return self._right


class _NotchlessScreen(_Screen):
    def __init__(self) -> None:
        super().__init__(left=0.0, right=0.0)


class _UnreadableScreen:
    def auxiliaryTopLeftArea(self):
        raise AttributeError("no auxiliary areas on this macOS")

    def auxiliaryTopRightArea(self):  # pragma: no cover - never reached
        raise AttributeError("no auxiliary areas on this macOS")


def test_a_display_with_no_notch_is_not_a_full_menu_bar() -> None:
    """Both produce a zero-wide wing; only one is ever going to change.

    An external monitor has no menu-bar area beside a notch it does not
    have, so this switch has never done anything there -- which is a
    permanent fact, not a crowded menu bar the owner could tidy.
    """
    notchless = screen_bar_wing_state(
        _NotchlessScreen(), 200.0, wrap_menu_bar=True
    )
    crowded = screen_bar_wing_state(
        _Screen(left=30.0, right=30.0), 200.0, wrap_menu_bar=True
    )

    assert notchless is ScreenBarWingState.NO_SAFE_AREA
    assert crowded is ScreenBarWingState.MENU_BAR_FULL


def test_a_screen_that_will_not_report_is_not_a_screen_with_no_notch() -> None:
    state = screen_bar_wing_state(_UnreadableScreen(), 200.0, wrap_menu_bar=True)

    assert state is ScreenBarWingState.UNREADABLE


def test_room_beside_the_notch_reports_as_extended() -> None:
    state = screen_bar_wing_state(
        _Screen(left=180.0, right=180.0), 200.0, wrap_menu_bar=True
    )

    assert state is ScreenBarWingState.EXTENDED


def test_a_manual_wing_length_is_not_a_measurement() -> None:
    state = screen_bar_wing_state(
        _NotchlessScreen(), 200.0, wrap_menu_bar=True, wing_length=18.0
    )

    assert state is ScreenBarWingState.MANUAL


def test_the_switch_being_off_outranks_every_measurement() -> None:
    state = screen_bar_wing_state(
        _NotchlessScreen(), 200.0, wrap_menu_bar=False
    )

    assert state is ScreenBarWingState.NOT_EXTENDING


def test_every_wing_state_has_its_own_sentence() -> None:
    messages = {
        settings_window.SCREEN_BAR_WING_MESSAGES[state] for state in ScreenBarWingState
    }

    assert len(messages) == len(ScreenBarWingState)


# --- Cloud agents and peers ----------------------------------------------


def test_a_failed_bind_is_not_reported_as_a_pending_start() -> None:
    """"Enabled -- starts with the app." was printed AFTER it had failed.

    start_cloud_ingest_server sets cloud_ingest back to None and logs
    when the bind raises; a port already in use is the ordinary way. The
    row promised a server that had already not arrived.
    """
    target = SimpleNamespace(
        settings=SimpleNamespace(cloud_ingest_enabled=True), cloud_ingest=None
    )

    text = settings_window.cloud_ingest_status_text(target)

    assert "Nothing is listening" in text
    assert "starts with the app" not in text


def test_a_listening_server_still_publishes_its_address() -> None:
    target = SimpleNamespace(
        settings=SimpleNamespace(cloud_ingest_enabled=True),
        cloud_ingest=SimpleNamespace(address=("127.0.0.1", 8123)),
    )

    assert (
        settings_window.cloud_ingest_status_text(target)
        == "http://127.0.0.1:8123/v1/agent-event"
    )


def _peer_target(*, attempted: int = 0, health: tuple = ()):
    return SimpleNamespace(
        settings=SimpleNamespace(remote_peers=SimpleNamespace(enabled=True)),
        _remote_refresh=SimpleNamespace(health=health, attempted=attempted),
    )


def test_a_missing_tailscale_is_named_instead_of_promised_peers() -> None:
    """"No peers found yet." argued the opposite of the truth.

    "yet" promises a peer is still coming. With no CLI to discover one
    with, no peer is ever coming, and that is the one fact here the
    owner can act on.
    """
    with patch.object(
        settings_window.remote_peers, "tailscale_available", return_value=False
    ):
        text = settings_window.remote_peer_status_text(_peer_target())

    assert "Tailscale is not installed" in text
    assert "yet" not in text


def test_no_round_trip_yet_is_not_an_empty_answer() -> None:
    with patch.object(
        settings_window.remote_peers, "tailscale_available", return_value=True
    ):
        unchecked = settings_window.remote_peer_status_text(_peer_target())
        checked = settings_window.remote_peer_status_text(
            _peer_target(attempted=3)
        )

    assert unchecked == "No peers checked yet."
    assert checked == "No other Macs are running JR-Bar right now."
    assert unchecked != checked


# --- Codex hook trust ----------------------------------------------------


def test_no_codex_binary_is_not_the_same_as_nothing_to_trust() -> None:
    """Both used to be an empty dict, and the installer returned on it.

    Codex refuses to run a hook whose hash it has not trusted, so this
    is the difference between "installed" and "installed, and it will
    not run until you approve it".
    """
    with patch("sidepulse.install.codex_cli_path", return_value=None):
        missing = resolve_codex_hook_trust(Path("/tmp/config.toml"))
    with (
        patch("sidepulse.install.codex_cli_path", return_value=Path("/usr/bin/codex")),
        patch("sidepulse.install.resolve_codex_hook_hashes", return_value={}),
    ):
        silent = resolve_codex_hook_trust(Path("/tmp/config.toml"))

    assert missing.status is CodexHookTrustStatus.CLI_NOT_FOUND
    assert silent.status is CodexHookTrustStatus.NOT_CONFIRMED
    assert missing.status is not silent.status
    assert missing.hashes == {} and silent.hashes == {}


def test_a_confirmed_handshake_carries_its_hashes() -> None:
    with (
        patch("sidepulse.install.codex_cli_path", return_value=Path("/usr/bin/codex")),
        patch(
            "sidepulse.install.resolve_codex_hook_hashes",
            return_value={"key": "sha256:abc"},
        ),
    ):
        trust = resolve_codex_hook_trust(Path("/tmp/config.toml"))

    assert trust.status is CodexHookTrustStatus.TRUSTED
    assert trust.hashes == {"key": "sha256:abc"}


def test_a_trust_status_cannot_disagree_with_its_payload() -> None:
    with pytest.raises(ValueError):
        CodexHookTrust(CodexHookTrustStatus.TRUSTED)
    with pytest.raises(ValueError):
        CodexHookTrust(CodexHookTrustStatus.CLI_NOT_FOUND, {"key": "sha256:abc"})


def test_an_install_that_could_not_get_trusted_says_so() -> None:
    """`changed` was the installer's only bit, and it was True here."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = base / "config.toml"
        log = base / "codex.jsonl"
        config.write_text("[features]\nhooks = true\n")

        with (
            patch("sidepulse.install.should_refresh_codex_hook_trust", return_value=True),
            patch("sidepulse.install.codex_cli_path", return_value=None),
        ):
            result = install_codex_hooks(
                log_path=log, config_path=config, python_executable="python3"
            )

    assert result.changed
    assert result.codex_trust is CodexHookTrustStatus.CLI_NOT_FOUND
    assert "Codex will ask you to trust it" in result.public_warning
    assert result.to_dict()["codex_trust"] == "cli_not_found"


def test_a_trusted_install_carries_no_warning() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = base / "config.toml"
        log = base / "codex.jsonl"
        key = f"{config}:pre_tool_use:0:0"
        config.write_text("[features]\nhooks = true\n")

        with (
            patch("sidepulse.install.should_refresh_codex_hook_trust", return_value=True),
            patch(
                "sidepulse.install.codex_cli_path", return_value=Path("/usr/bin/codex")
            ),
            patch(
                "sidepulse.install.resolve_codex_hook_hashes",
                return_value={key: "sha256:new"},
            ),
        ):
            result = install_codex_hooks(
                log_path=log, config_path=config, python_executable="python3"
            )

    assert result.codex_trust is CodexHookTrustStatus.TRUSTED
    assert result.public_warning == ""


def test_a_provider_without_a_handshake_is_not_reported_as_untrusted() -> None:
    """None means "this provider has no trust step", not "it failed"."""
    result = InstallResult("claude", Path("/tmp/c"), Path("/tmp/l"), True)

    assert result.codex_trust is None
    assert result.public_warning == ""
    assert result.to_dict()["warning"] == ""


# --- Doctor ---------------------------------------------------------------


def test_a_failed_probe_does_not_render_a_total_it_never_counted() -> None:
    """"unavailable [0/32]" reads as 32 paths checked and none private.

    The probe raised before reading one of them. The manifest ceiling is
    a bound on what MAY be reported, not a count of what was.
    """

    def exploding() -> None:
        raise PermissionError("denied")

    probes = tuple(
        DiagnosticProbe(
            check,
            exploding
            if check is DiagnosticCheck.PRIVATE_PATH_MODES
            else (
                lambda c=check: doctor_module._finding(
                    c,
                    (
                        DiagnosticCode.RECOVERING
                        if c is DiagnosticCheck.ALCOVE_FOLLOW_STATE
                        else DiagnosticCode.UNAVAILABLE
                    ),
                    0,
                    0,
                )
            ),
        )
        for check in DiagnosticCheck
    )

    result = collect_diagnostics(probes=probes)
    finding = result.finding(DiagnosticCheck.PRIVATE_PATH_MODES)

    assert finding.code is DiagnosticCode.UNAVAILABLE
    assert (finding.count, finding.limit) == (0, 0)
    assert "private path modes: unavailable [0/0]" in render_diagnostic_result(result)


class SweepSettingsSurfaceTests(unittest.TestCase):
    """Every fix above has to reach the real pane, not just its function."""

    def setUp(self) -> None:
        isolate_controller(self)
        settings_window.reset_event_access_cache()
        settings_window.reset_screen_brightness_cache()
        self.addCleanup(settings_window.reset_event_access_cache)
        self.addCleanup(settings_window.reset_screen_brightness_cache)

    def test_the_extras_pane_names_a_denied_calendar_permission(self) -> None:
        self.controller.settings = self.controller.settings.with_calendar_alerts_enabled(
            True
        )
        with patch.object(
            settings_window, "_event_access_status", return_value="denied"
        ):
            self.controller.show_settings_window()
            self.controller.ensure_settings_pane("extras")

        label = self.controller.settings_fields["calendar_access_status"]
        self.assertIn("Denied", label.stringValue())
        self.assertIn("Calendars", label.stringValue())
        # The switch itself is still ON. That gap is why the row exists.
        self.assertTrue(self.controller.settings.calendar_alerts_enabled)

    def test_the_extras_pane_names_a_denied_reminders_permission(self) -> None:
        self.controller.settings = self.controller.settings.with_reminder_alerts_enabled(
            True
        )
        with patch.object(
            settings_window, "_event_access_status", return_value="denied"
        ):
            self.controller.show_settings_window()
            self.controller.ensure_settings_pane("extras")

        label = self.controller.settings_fields["reminders_access_status"]
        self.assertIn("Reminders", label.stringValue())

    def test_the_screen_bar_pane_says_a_notchless_display_cannot_wrap(self) -> None:
        self.controller.settings = (
            self.controller.settings.with_virtual_status_device_wraps_menu_bar(True)
        )
        with patch.object(
            settings_window,
            "screen_bar_wing_state",
            return_value=ScreenBarWingState.NO_SAFE_AREA,
        ):
            self.controller.show_settings_window()
            self.controller.ensure_settings_pane("colors_screen_bar")

        label = self.controller.settings_fields["screen_bar_wing_status"]
        self.assertIn("no notch", label.stringValue())
        self.assertTrue(
            self.controller.settings.virtual_status_device_wraps_menu_bar,
            "the switch reads ON over a feature that cannot run here",
        )

    def test_the_bar_size_slider_falls_back_to_the_real_automatic_size(self) -> None:
        """The slider parked at a literal that stands for nothing.

        With the screen unreadable it showed 232.0 -- a number that
        appears nowhere in the geometry module -- so "Use Automatic Size"
        and the position the slider claimed WAS automatic were 12pt
        apart, on a control whose whole job is to show a measurement.
        """
        from sidepulse.screen_bar_design import WINDOW_WIDTH

        with patch.object(
            settings_window,
            "slot_width_for_screen",
            side_effect=RuntimeError("no screen"),
        ):
            self.controller.show_settings_window()
            self.controller.ensure_settings_pane("colors_screen_bar")

        slider = self.controller.settings_fields["screen_bar_gap_slider"]
        self.assertIsNone(self.controller.settings.screen_bar_gap_width)
        self.assertEqual(slider.doubleValue(), WINDOW_WIDTH)

    def test_use_automatic_size_keeps_the_design_width_when_screen_is_unreadable(
        self,
    ) -> None:
        from sidepulse.screen_bar_design import WINDOW_WIDTH

        self.controller.show_settings_window()
        self.controller.ensure_settings_pane("colors_screen_bar")
        slider = self.controller.settings_fields["screen_bar_gap_slider"]
        slider.setDoubleValue_(400.0)

        with patch.object(
            self.status_bar,
            "slot_width_for_screen",
            side_effect=RuntimeError("no screen"),
        ):
            self.controller.resetScreenBarGeometry_(None)

        self.assertIsNone(self.controller.settings.screen_bar_gap_width)
        self.assertEqual(slider.doubleValue(), WINDOW_WIDTH)

    def test_the_wing_row_refreshes_with_the_rest_of_the_window(self) -> None:
        """Panes build once. Docking the Mac is exactly when this stales."""
        self.controller.show_settings_window()
        self.controller.ensure_settings_pane("colors_screen_bar")
        label = self.controller.settings_fields["screen_bar_wing_status"]
        label.setStringValue_("stale")

        settings_window.refresh_alcove_follow_controls(self.controller)

        self.assertNotEqual(label.stringValue(), "stale")
