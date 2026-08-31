"""A fresh install must not say "Idle" when it means "nobody connected me"
or "I cannot hear anything".

The failure that opened this project: installed hooks spoke a stale wire
format, every event died in a TypeError, and the menu bar said Idle for an
hour. These tests hold the three cases apart, hold the per-provider
"last heard from" ledger honest, and hold the "why is it doing that" panel
to the rule ladder it claims to explain.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.attention import AttentionProjection, LifecycleMode, ProjectedAgentRow
from sidepulse.doctor import DiagnosticCheck, DiagnosticCode
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.presentation_policy import (
    CapacityGlance,
    FiniteCue,
    GlanceInputs,
    GlanceOverrideReason,
    GlanceSemantic,
    ResolvedGlance,
    SemanticGlyph,
    resolve_glance,
)
from sidepulse import decision_trace, intake_health

NOW = 1_800_000_000.0


def probe(
    provider: str,
    label: str,
    *,
    installed: bool = True,
    wire: float | None = None,
    probed: bool = True,
) -> intake_health.ProviderProbe:
    return intake_health.ProviderProbe(
        provider=provider,
        label=label,
        probed=probed,
        installed=installed,
        wire_written_at=wire,
    )


def report(probes, accepted=None, *, now: float = NOW) -> intake_health.IntakeReport:
    return intake_health.build_intake_report(
        tuple(probes),
        accepted_by_provider=accepted or {},
        now_epoch=now,
    )


class FakeButton:
    def __init__(self) -> None:
        self.title = None
        self.tooltip = None
        self.image = "unset"
        self.accessibility: dict[str, str] = {}

    def setTitle_(self, value):
        self.title = value

    def setImage_(self, value):
        self.image = value

    def setToolTip_(self, value):
        self.tooltip = value

    def setAccessibilityLabel_(self, value):
        self.accessibility["label"] = value

    def setAccessibilityValue_(self, value):
        self.accessibility["value"] = value

    def setAccessibilityHelp_(self, value):
        self.accessibility["help"] = value


class FakeStatusItem:
    def __init__(self, button: FakeButton) -> None:
        self._button = button
        self.menu = None

    def button(self):
        return self._button

    def setMenu_(self, menu):
        self.menu = menu


def isolated_controller(case):
    """A real controller whose every disk touch lands in a temp dir."""
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    case.addCleanup(tmp.cleanup)
    for target in (
        "sidepulse.settings.default_settings_path",
        "sidepulse.status_bar.default_settings_path",
    ):
        patcher = patch(target, return_value=Path(tmp.name) / "settings.json")
        patcher.start()
        case.addCleanup(patcher.stop)
    for target, value in (
        ("sidepulse.status_bar.default_latest_state_path", Path(tmp.name) / "latest.json"),
        ("sidepulse.status_bar.discover_devices", []),
    ):
        patcher = patch(target, return_value=value)
        patcher.start()
        case.addCleanup(patcher.stop)
    try:
        from sidepulse import status_bar
    except SystemExit as exc:  # pragma: no cover - non-macOS runners
        case.skipTest(str(exc))
    controller = status_bar.StatusBarController.alloc().init()
    button = FakeButton()
    controller.status_item = FakeStatusItem(button)
    return status_bar, controller, button


class IdleIsThreeDifferentThingsTests(unittest.TestCase):
    """A menu bar that says Idle three ways is a menu bar that lies twice."""

    def setUp(self) -> None:
        self.status_bar, self.controller, self.button = isolated_controller(self)

    def test_a_mac_nobody_connected_is_not_called_idle(self) -> None:
        self.controller.current_intake_report = report(
            [probe("claude", "Claude", installed=False), probe("codex", "Codex", installed=False)]
        )
        self.controller.set_status(self.status_bar.STATE_IDLE)
        self.assertEqual(self.button.title, " Not set up")
        self.assertEqual(self.button.tooltip, "JR Bar Agent Monitor: Not set up")
        # The state machine is untouched: idle-dim and every equality
        # test downstream still see plain Idle.
        self.assertEqual(self.controller.current_state, self.status_bar.STATE_IDLE)
        self.assertIsNotNone(self.controller.idle_since_monotonic)

    def test_a_mac_whose_hooks_write_into_a_void_is_not_called_idle(self) -> None:
        self.controller.current_intake_report = report(
            [probe("claude", "Claude", wire=NOW - 30.0)]
        )
        self.controller.set_status(self.status_bar.STATE_IDLE)
        self.assertEqual(self.button.title, " Not hearing agents")
        self.assertEqual(
            self.button.tooltip, "JR Bar Agent Monitor: Not hearing agents"
        )

    def test_a_genuinely_idle_mac_is_still_called_idle(self) -> None:
        self.controller.current_intake_report = report(
            [probe("claude", "Claude", wire=NOW - 60.0)],
            {"claude": NOW - 60.0},
        )
        self.controller.set_status(self.status_bar.STATE_IDLE)
        self.assertEqual(self.button.tooltip, "JR Bar Agent Monitor: Idle")
        self.assertNotIn("Not", str(self.button.title))

    def test_a_provider_connected_ten_seconds_ago_is_never_called_broken(self) -> None:
        """The one message a status bar must never cry wolf with.

        Setup just finished. Nothing has run yet. Nothing has been lost.
        """
        self.controller.current_intake_report = report([probe("claude", "Claude")])
        self.controller.set_status(self.status_bar.STATE_IDLE)
        self.assertEqual(self.button.tooltip, "JR Bar Agent Monitor: Idle")
        self.assertIsNone(
            intake_health.intake_alert_title(self.controller.current_intake_report)
        )

    def test_one_stuck_provider_beside_one_live_one_does_not_claim_deafness(self) -> None:
        """SidePulse can hear Claude. Saying it hears nothing would be a lie
        in the other direction -- the dropdown names Codex instead."""
        current = report(
            [probe("claude", "Claude", wire=NOW - 60.0), probe("codex", "Codex", wire=NOW - 5.0)],
            {"claude": NOW - 60.0},
        )
        self.controller.current_intake_report = current
        self.controller.set_status(self.status_bar.STATE_IDLE)
        self.assertEqual(self.button.tooltip, "JR Bar Agent Monitor: Idle")
        self.assertIn("Codex", intake_health.intake_alert_title(current))

    def test_an_unavailable_symbol_never_blanks_the_menu_bar(self) -> None:
        """A status item whose image resolves to None disappears from the
        menu bar entirely. The honest state must never cost the icon."""
        with patch.object(self.status_bar, "image_for_symbol", side_effect=[None, "idle-image"]):
            self.controller.current_intake_report = report(
                [probe("claude", "Claude", installed=False)]
            )
            self.controller.set_status(self.status_bar.STATE_IDLE)
        self.assertEqual(self.button.image, "idle-image")

    def test_a_working_agent_is_never_relabelled(self) -> None:
        """Only Idle is ambiguous. Working carries its own evidence."""
        self.controller.current_intake_report = report(
            [probe("claude", "Claude", installed=False)]
        )
        self.controller.set_status(self.status_bar.STATE_WORKING)
        self.assertEqual(self.button.tooltip, "JR Bar Agent Monitor: Working")

    def test_the_honest_label_survives_the_accessibility_repaint(self) -> None:
        """set_status writes the title once and the accessibility pass
        writes it again. Both must agree, or the reassuring version wins
        by being second."""
        from sidepulse.operator_state import empty_operator_state

        self.controller.current_intake_report = report(
            [probe("claude", "Claude", installed=False)]
        )
        self.controller.current_operator_state = empty_operator_state()
        self.controller.current_state = self.status_bar.STATE_IDLE
        glance = ResolvedGlance(
            semantic=GlanceSemantic.REST,
            glyph=SemanticGlyph.REST,
            cue=None,
            override_reason=GlanceOverrideReason.NONE,
            relay_epoch=1.0,
            next_visual_change_at=None,
        )
        self.controller._apply_status_accessibility_text(
            glance,
            self.controller._status_finite_cues,
        )
        self.assertEqual(self.button.title, " Not set up")
        self.assertEqual(self.button.accessibility["value"], "Not set up")
        self.assertNotIn("No agents need attention", str(self.button.title))


class IntakeVerdictTests(unittest.TestCase):
    """The evidence ladder, one rung at a time."""

    def test_nothing_installed_reads_as_not_configured(self) -> None:
        current = report([probe("claude", "Claude", installed=False)])
        self.assertIs(current.hook_state.check, DiagnosticCheck.HOOK_DETECTOR_STATE)
        self.assertIs(current.hook_state.code, DiagnosticCode.NOT_CONFIGURED)
        self.assertEqual(intake_health.idle_disclosure(current), "Not set up")

    def test_a_hook_writing_ahead_of_canonical_state_reads_as_partial(self) -> None:
        current = report([probe("claude", "Claude", wire=NOW - 10.0)], {"claude": NOW - 4_000.0})
        self.assertIs(current.providers[0].code, DiagnosticCode.PARTIAL)
        self.assertIs(current.source_health.check, DiagnosticCheck.NEGOTIATED_SOURCE_HEALTH)
        self.assertIs(current.source_health.code, DiagnosticCode.UNAVAILABLE)

    def test_a_write_inside_the_ingest_lag_is_not_yet_a_fault(self) -> None:
        """A hook that fired two seconds ago has not been ingested yet."""
        current = report(
            [probe("claude", "Claude", wire=NOW - 2.0)],
            {"claude": NOW - 3.0},
        )
        self.assertIs(current.providers[0].code, DiagnosticCode.HEALTHY)

    def test_an_unlanded_write_from_last_week_is_silence_not_activity(self) -> None:
        current = report([probe("claude", "Claude", wire=NOW - 8 * 86_400.0)])
        self.assertIs(current.providers[0].code, DiagnosticCode.UNAVAILABLE)
        self.assertIn(
            "No agent event has ever arrived",
            intake_health.intake_alert_title(current),
        )

    def test_a_clock_from_the_future_convicts_nobody(self) -> None:
        current = report(
            [probe("claude", "Claude", wire=NOW + 90_000.0)],
            {"claude": NOW - 60.0},
        )
        self.assertIs(current.providers[0].code, DiagnosticCode.HEALTHY)

    def test_a_long_silence_reads_as_unavailable(self) -> None:
        current = report(
            [probe("claude", "Claude", wire=NOW - 300_000.0)],
            {"claude": NOW - 300_000.0},
        )
        self.assertIs(current.providers[0].code, DiagnosticCode.UNAVAILABLE)
        self.assertEqual(intake_health.idle_disclosure(current), "Not hearing agents")
        self.assertIn("No agent events for 3d", intake_health.intake_alert_title(current))

    def test_installed_and_never_spoken_is_configured_not_broken(self) -> None:
        current = report([probe("claude", "Claude")])
        self.assertIs(current.providers[0].code, DiagnosticCode.CONFIGURED)
        self.assertIs(current.source_health.code, DiagnosticCode.HEALTHY)
        self.assertIsNone(intake_health.idle_disclosure(current))

    def test_an_unreadable_config_is_not_reported_as_an_absent_one(self) -> None:
        current = report([probe("claude", "Claude", installed=False, probed=False)])
        self.assertIs(current.hook_state.code, DiagnosticCode.UNAVAILABLE)

    def test_some_installed_reads_as_partial_installation(self) -> None:
        current = report(
            [probe("claude", "Claude"), probe("codex", "Codex", installed=False)]
        )
        self.assertIs(current.hook_state.code, DiagnosticCode.PARTIAL)
        self.assertEqual(current.hook_state.count, 1)
        self.assertEqual(current.hook_state.limit, 2)

    def test_a_real_filesystem_with_no_configs_reads_as_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            probes = intake_health.probe_providers(home=Path(home))
        self.assertTrue(probes)
        self.assertTrue(all(item.probed for item in probes))
        self.assertIs(report(probes).hook_state.code, DiagnosticCode.NOT_CONFIGURED)


class LastHeardFromTests(unittest.TestCase):
    """A dead hook must be visible instead of looking like an idle agent."""

    def test_every_provider_this_mac_has_reports_when_it_was_last_heard(self) -> None:
        current = report(
            [
                probe("claude", "Claude", wire=NOW - 240.0),
                probe("codex", "Codex", wire=NOW - 200_000.0),
                probe("grok", "Grok"),
            ],
            {"claude": NOW - 240.0, "codex": NOW - 200_000.0},
        )
        rows = intake_health.last_heard_rows(current)
        self.assertEqual(
            rows,
            (
                "Claude · 4m ago",
                "⚠ Codex · 2d ago",
                "Grok · connected, nothing yet",
            ),
        )
        self.assertEqual(intake_health.last_heard_summary(current), "Last heard from · 4m ago")

    def test_a_provider_that_was_never_installed_and_never_spoke_gets_no_row(self) -> None:
        current = report(
            [probe("claude", "Claude"), probe("hermes", "Hermes Agent", installed=False)]
        )
        self.assertEqual(
            intake_health.last_heard_rows(current), ("Claude · connected, nothing yet",)
        )

    def test_a_provider_heard_after_its_hook_was_removed_still_reports(self) -> None:
        current = report(
            [probe("codex", "Codex", installed=False)],
            {"codex": NOW - 3_600.0},
        )
        self.assertEqual(
            intake_health.last_heard_rows(current),
            ("Codex · not connected · last heard 1h ago",),
        )

    def test_the_wire_is_read_from_the_last_record_not_the_files_mtime(self) -> None:
        """Hook logs are compacted in place. mtime says "a janitor rewrote
        this file", not "an agent said something"."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claude.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps({"record_kind": "normalized", "occurred_at_epoch": epoch})
                    for epoch in (NOW - 900.0, NOW - 600.0)
                )
                + "\n",
                encoding="utf-8",
            )
            os.utime(path, (NOW + 100_000.0, NOW + 100_000.0))
            found = intake_health._newest_record_epoch(
                path, tail_bytes=intake_health.MAX_LOG_TAIL_BYTES
            )
        self.assertEqual(found, NOW - 600.0)

    def test_a_truncated_tail_never_reports_a_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claude.jsonl"
            filler = json.dumps(
                {"record_kind": "normalized", "occurred_at_epoch": NOW - 900.0, "pad": "x" * 200}
            )
            path.write_text(
                "\n".join([filler] * 40)
                + "\n"
                + json.dumps({"record_kind": "normalized", "occurred_at_epoch": NOW - 5.0})
                + "\n",
                encoding="utf-8",
            )
            found = intake_health._newest_record_epoch(path, tail_bytes=512)
        self.assertEqual(found, NOW - 5.0)

    def test_a_missing_or_empty_log_reports_nothing_rather_than_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "absent.jsonl"
            empty = Path(directory) / "empty.jsonl"
            empty.write_text("", encoding="utf-8")
            self.assertIsNone(intake_health._newest_record_epoch(missing, tail_bytes=512))
            self.assertIsNone(intake_health._newest_record_epoch(empty, tail_bytes=512))


class IntakeMenuTests(unittest.TestCase):
    """The dropdown half: the fault, the one click, and the ledger."""

    def _menu(self, current):
        from sidepulse import status_bar
        from AppKit import NSMenu

        menu = NSMenu.alloc().init()
        status_bar.add_intake_menu_items(
            menu, SimpleNamespace(current_intake_report=current)
        )
        return menu, [
            menu.itemAtIndex_(index) for index in range(menu.numberOfItems())
        ]

    def test_an_unconnected_mac_gets_the_one_click_that_fixes_it(self) -> None:
        _menu, items = self._menu(report([probe("claude", "Claude", installed=False)]))
        titles = [item.title() for item in items]
        self.assertIn("⚠ Not set up — connect your agents in Setup…", titles)
        self.assertEqual(items[0].action(), "openSetup:")

    def test_a_stuck_hook_gets_the_same_one_click(self) -> None:
        _menu, items = self._menu(report([probe("claude", "Claude", wire=NOW - 30.0)]))
        self.assertIn("nothing arriving", items[0].title())
        self.assertEqual(items[0].action(), "openSetup:")

    def test_a_healthy_mac_is_never_given_an_alarm_row(self) -> None:
        current = report([probe("claude", "Claude", wire=NOW - 60.0)], {"claude": NOW - 60.0})
        _menu, items = self._menu(current)
        titles = [item.title() for item in items]
        self.assertFalse([title for title in titles if title.startswith("⚠")])
        self.assertIn("Last heard from · 1m ago", titles)

    def test_every_provider_row_is_reachable_from_the_dropdown(self) -> None:
        current = report(
            [probe("claude", "Claude", wire=NOW - 60.0), probe("codex", "Codex")],
            {"claude": NOW - 60.0},
        )
        _menu, items = self._menu(current)
        parent = next(item for item in items if item.title().startswith("Last heard from"))
        submenu = parent.submenu()
        rows = [
            submenu.itemAtIndex_(index).title() for index in range(submenu.numberOfItems())
        ]
        self.assertEqual(rows, ["Claude · 1m ago", "Codex · connected, nothing yet"])

    def test_a_menu_built_without_a_report_never_probes_the_machine(self) -> None:
        """Rendering must not depend on the filesystem of whoever opens it."""
        _menu, items = self._menu(None)
        self.assertEqual(items, [])

    def test_connecting_an_agent_changes_what_the_dropdown_would_render(self) -> None:
        """The menu only rebuilds when its signature changes. If intake is
        not in that signature, "Not set up" survives Setup for 30 seconds
        -- the worst possible half-minute to still be calling the user
        unconnected."""
        unconnected = intake_health.intake_content_signature(
            report([probe("claude", "Claude", installed=False)])
        )
        connected = intake_health.intake_content_signature(
            report([probe("claude", "Claude")])
        )
        self.assertNotEqual(unconnected, connected)
        self.assertEqual(
            connected,
            intake_health.intake_content_signature(report([probe("claude", "Claude")])),
        )

    def test_a_second_of_ageing_is_not_a_menu_rebuild(self) -> None:
        rows = [probe("claude", "Claude", wire=NOW - 60.0)]
        self.assertEqual(
            intake_health.intake_content_signature(report(rows, {"claude": NOW - 60.0})),
            intake_health.intake_content_signature(
                report(rows, {"claude": NOW - 61.0}, now=NOW + 1.0)
            ),
        )

    def _build_menu(self, current=None):
        from sidepulse import status_bar
        from sidepulse.settings import AgentMonitorSettings

        snapshot = SimpleNamespace(
            statuses=[],
            stale_statuses=[],
            collected_at=datetime.now(timezone.utc),
        )
        target = SimpleNamespace(
            settings=AgentMonitorSettings(),
            closed_lid_awake=SimpleNamespace(last_error=None),
            status_bar_devices=list,
            _menu_hooks_probe=(float("inf"), True),
            current_intake_report=current,
        )
        return status_bar.build_menu(snapshot, status_bar.STATE_IDLE, target)

    def test_the_real_dropdown_carries_the_alarm_and_the_ledger(self) -> None:
        """Wiring, not just rendering: build_menu must actually call it."""
        menu = self._build_menu(report([probe("claude", "Claude", installed=False)]))
        titles = [
            menu.itemAtIndex_(index).title() for index in range(menu.numberOfItems())
        ]
        self.assertIn("⚠ Not set up — connect your agents in Setup…", titles)

    def test_the_real_dropdown_stays_clean_when_there_is_nothing_to_say(self) -> None:
        menu = self._build_menu(
            report([probe("claude", "Claude", wire=NOW - 60.0)], {"claude": NOW - 60.0})
        )
        titles = [
            menu.itemAtIndex_(index).title() for index in range(menu.numberOfItems())
        ]
        self.assertFalse([title for title in titles if title.startswith("⚠")])
        self.assertIn("Last heard from · 1m ago", titles)

    def test_connecting_an_agent_forces_the_dropdown_to_rebuild(self) -> None:
        """menu_content_signature decides whether the menu is rebuilt at
        all. Intake has to be in it, or the alarm outlives its cause."""
        from sidepulse import status_bar
        from sidepulse.settings import AgentMonitorSettings

        snapshot = SimpleNamespace(
            statuses=[],
            stale_statuses=[],
            collected_at=datetime.now(timezone.utc),
        )

        def signature(current):
            target = SimpleNamespace(
                settings=AgentMonitorSettings(),
                closed_lid_awake=SimpleNamespace(last_error=None),
                status_bar_devices=lambda remember=True: [],
                timer_fill_fraction=lambda: 0.0,
                timebox_active=lambda: False,
                current_intake_report=current,
            )
            return status_bar.menu_content_signature(
                snapshot, status_bar.STATE_IDLE, target
            )

        self.assertNotEqual(
            signature(report([probe("claude", "Claude", installed=False)])),
            signature(report([probe("claude", "Claude")])),
        )

    def test_the_dropdown_offers_the_why_panel(self) -> None:
        # The compact menu carries the panel under "Diagnostics…"; the
        # window itself still bears decision_trace.PANEL_TITLE.
        menu = self._build_menu()
        item = next(
            menu.itemAtIndex_(index)
            for index in range(menu.numberOfItems())
            if menu.itemAtIndex_(index).title()
            in ("Diagnostics…", decision_trace.MENU_ITEM_TITLE)
        )
        self.assertEqual(item.action(), "openWhyPanel:")


class RuleLadderTests(unittest.TestCase):
    """The panel explains presentation_policy's ladder. It must be the
    same ladder, in the same order, or the panel is fiction."""

    def _inputs(self, satisfied: set[GlanceSemantic]) -> GlanceInputs:
        return GlanceInputs(
            actionable_episode_key=(
                "attention:one" if GlanceSemantic.ATTENTION in satisfied else None
            ),
            fresh_failure=(
                FiniteCue("failure:one", GlanceSemantic.FRESH_FAILURE, 2, 0.4)
                if GlanceSemantic.FRESH_FAILURE in satisfied
                else None
            ),
            fresh_completion=(
                FiniteCue("completion:one", GlanceSemantic.FRESH_COMPLETION, 2, 0.4)
                if GlanceSemantic.FRESH_COMPLETION in satisfied
                else None
            ),
            active=GlanceSemantic.ACTIVE in satisfied,
            unresolved_failure=GlanceSemantic.UNRESOLVED_FAILURE in satisfied,
            capacity=(
                CapacityGlance("provider:one", 0.5)
                if GlanceSemantic.CAPACITY in satisfied
                else None
            ),
        )

    def _resolve(self, satisfied: set[GlanceSemantic]) -> ResolvedGlance:
        return resolve_glance(
            self._inputs(satisfied),
            presentation_time=100.0,
            relay_epoch=10.0,
            preferences=AccessibilityDisplayPreferences(
                reduce_motion=False,
                reduce_transparency=False,
                increase_contrast=False,
                differentiate_without_color=False,
            ),
        )

    def test_the_panels_ladder_is_the_policys_ladder_in_order(self) -> None:
        ladder = [semantic for semantic, _name in decision_trace.RULE_LADDER]
        for index, semantic in enumerate(ladder):
            # Satisfy this rung and every rung below it: the higher rung
            # must still win, which is what "first rule that applies"
            # means and what the panel renders.
            satisfied = set(ladder[index:])
            self.assertIs(
                self._resolve(satisfied).semantic,
                semantic,
                f"rung {index + 1} ({semantic.value}) is not where the panel claims",
            )

    def test_the_panel_covers_every_semantic_the_policy_can_produce(self) -> None:
        self.assertEqual(
            {semantic for semantic, _name in decision_trace.RULE_LADDER},
            set(GlanceSemantic),
        )

    def test_rungs_below_the_winner_are_reported_as_never_reached(self) -> None:
        rungs = decision_trace.rungs_for_semantic(
            GlanceSemantic.ACTIVE, overridden=False
        )
        outcomes = [rung.outcome for rung in rungs]
        self.assertEqual(
            outcomes,
            [decision_trace.RungOutcome.NOT_MET] * 3
            + [decision_trace.RungOutcome.MET]
            + [decision_trace.RungOutcome.NOT_REACHED] * 3,
        )

    def test_an_override_is_never_reported_as_a_ladder_result(self) -> None:
        rungs = decision_trace.rungs_for_semantic(GlanceSemantic.REST, overridden=True)
        self.assertTrue(
            all(rung.outcome is decision_trace.RungOutcome.NOT_REACHED for rung in rungs)
        )


class WhyPanelTests(unittest.TestCase):
    """One place that answers which agent, which state, which rule, and
    when it last changed -- without ever showing what an agent said."""

    def setUp(self) -> None:
        self.status_bar, self.controller, self.button = isolated_controller(self)

    def _glance(self, semantic, *, override=GlanceOverrideReason.NONE, cue=None):
        return ResolvedGlance(
            semantic=semantic,
            glyph=SemanticGlyph.REST,
            cue=cue,
            override_reason=override,
            relay_epoch=1.0,
            next_visual_change_at=None,
        )

    def _projection(self, status: AgentStatus, *, actionable: bool):
        row = ProjectedAgentRow(
            agent_id=status.agent_id,
            provider=status.provider,
            display_name=status.display_name,
            lifecycle_mode=(
                LifecycleMode.WAITING if actionable else LifecycleMode.ACTIVE
            ),
            actionable=actionable,
            is_subagent=False,
            updated_at=status.updated_at,
            source_status=status,
        )
        return AttentionProjection(
            lifecycle_mode=row.lifecycle_mode,
            actionable_attention=(row,) if actionable else (),
            visible_rows=(row,),
            transient_signals=(),
            dominant_provider=status.provider,
            click_target_agent_id=status.agent_id,
        )

    def _status(self, *, mode=AgentMode.WAITING_FOR_INPUT) -> AgentStatus:
        return AgentStatus(
            provider="claude",
            agent_id="claude:session:9f2c41aa",
            display_name="refactor the parser",
            mode=mode,
            updated_at=datetime.now(timezone.utc),
            event_name="Notification",
            session_id="9f2c41aa-secret-session",
            cwd="/Users/someone/Private/very-secret-project",
            tool_name="Bash",
            message="please approve rm -rf on the staging database",
        )

    def test_the_panel_names_the_rule_that_won_and_the_ones_it_beat(self) -> None:
        self.controller._current_resolved_glance = self._glance(GlanceSemantic.ACTIVE)
        self.controller.current_attention_projection = self._projection(
            self._status(mode=AgentMode.WORKING), actionable=False
        )
        self.controller.current_intake_report = report(
            [probe("claude", "Claude", wire=NOW - 60.0)], {"claude": NOW - 60.0}
        )
        text = decision_trace.decision_trace_text(self.controller.current_decision_trace())
        self.assertIn("4. An agent is working", text)
        self.assertIn("THIS ONE", text)
        self.assertIn("1. Someone is waiting on you", text)
        self.assertIn("not reached", text)
        self.assertIn("refactor the parser — working", text)

    def test_the_panel_reports_doctor_codes_rather_than_new_ones(self) -> None:
        self.controller._current_resolved_glance = self._glance(GlanceSemantic.REST)
        self.controller.current_intake_report = report(
            [probe("claude", "Claude", wire=NOW - 60.0)], {"claude": NOW - 60.0}
        )
        text = decision_trace.decision_trace_text(self.controller.current_decision_trace())
        self.assertIn(DiagnosticCheck.HOOK_DETECTOR_STATE.value, text)
        self.assertIn(DiagnosticCheck.NEGOTIATED_SOURCE_HEALTH.value, text)
        self.assertIn(DiagnosticCode.HEALTHY.value, text)

    def test_the_panel_explains_the_decision_and_never_the_payload(self) -> None:
        status = self._status()
        self.controller._current_resolved_glance = self._glance(GlanceSemantic.ATTENTION)
        self.controller.current_attention_projection = self._projection(
            status, actionable=True
        )
        self.controller.current_intake_report = report([probe("claude", "Claude")])
        text = decision_trace.decision_trace_text(self.controller.current_decision_trace())
        self.assertIn("refactor the parser — waiting on you", text)
        for secret in (
            status.session_id,
            status.cwd,
            status.message,
            status.tool_name,
            status.event_name,
            status.agent_id,
        ):
            self.assertNotIn(secret, text)

    def test_the_panel_says_which_light_and_when_it_last_changed(self) -> None:
        self.controller.note_glance_decision(self._glance(GlanceSemantic.REST))
        self.controller._current_resolved_glance = self._glance(GlanceSemantic.REST)
        text = decision_trace.decision_trace_text(self.controller.current_decision_trace())
        self.assertIn("THE LIGHT RIGHT NOW", text)
        self.assertIn("Rest · idle", text)
        self.assertIn("Unchanged for", text)

    def test_a_repaint_is_not_a_change(self) -> None:
        """Refresh rewrites the same program every 15 seconds. A panel that
        counted those as changes would answer "just now" forever."""
        glance = self._glance(GlanceSemantic.REST)
        self.assertTrue(self.controller.note_glance_decision(glance))
        first = self.controller._glance_changed_at_epoch
        self.assertFalse(self.controller.note_glance_decision(glance))
        self.assertEqual(self.controller._glance_changed_at_epoch, first)
        self.assertTrue(
            self.controller.note_glance_decision(self._glance(GlanceSemantic.ACTIVE))
        )
        self.assertNotEqual(self.controller._glance_changed_at_epoch, None)

    def test_an_override_is_named_instead_of_a_rule(self) -> None:
        self.controller._current_resolved_glance = self._glance(
            GlanceSemantic.REST, override=GlanceOverrideReason.FOCUS
        )
        text = decision_trace.decision_trace_text(self.controller.current_decision_trace())
        self.assertIn("Overridden: a Focus is active", text)
        self.assertNotIn("THIS ONE", text)

    def test_the_panel_opens_before_any_light_is_resolved(self) -> None:
        self.controller._current_resolved_glance = None
        text = decision_trace.decision_trace_text(self.controller.current_decision_trace())
        self.assertIn("No presentation has been resolved yet.", text)

    def test_an_open_panel_follows_the_light_it_explains(self) -> None:
        """Left alone, a panel answers for whichever light was on when it
        opened -- the exact failure it exists to end."""
        written: list[str] = []
        self.controller.why_panel_window = SimpleNamespace(isVisible=lambda: True)
        self.controller.why_panel_text_view = SimpleNamespace(
            setString_=written.append
        )
        self.controller._current_resolved_glance = self._glance(GlanceSemantic.REST)
        self.assertTrue(self.controller.refresh_why_panel())
        self.controller._current_resolved_glance = self._glance(GlanceSemantic.ATTENTION)
        self.assertTrue(self.controller.refresh_why_panel())
        self.assertIn("Rest ·", written[0])
        self.assertIn("Ask ·", written[1])

    def test_the_panel_window_actually_builds_and_shows_the_body(self) -> None:
        window = self.status_bar.build_why_panel_window(self.controller)
        self.assertEqual(window.title(), decision_trace.PANEL_TITLE)
        self.controller.why_panel_window = window
        self.controller._current_resolved_glance = self._glance(GlanceSemantic.REST)
        self.controller.current_intake_report = report([probe("claude", "Claude")])
        self.status_bar.set_text_control_value(
            self.controller.why_panel_text_view, self.controller.why_panel_body()
        )
        body = self.status_bar.text_control_value(self.controller.why_panel_text_view)
        self.assertIn("THE RULE THAT PRODUCED IT", body)
        # Copyable into a bug report in one gesture.
        self.assertTrue(self.controller.why_panel_text_view.isSelectable())
        self.assertFalse(self.controller.why_panel_text_view.isEditable())

    def test_a_closed_panel_is_never_repainted(self) -> None:
        self.controller.why_panel_window = SimpleNamespace(isVisible=lambda: False)
        self.assertFalse(self.controller.refresh_why_panel())
        self.controller.why_panel_window = None
        self.assertFalse(self.controller.refresh_why_panel())

    def test_the_panel_reports_the_intake_it_would_otherwise_hide(self) -> None:
        self.controller._current_resolved_glance = self._glance(GlanceSemantic.REST)
        with patch.object(
            self.status_bar.StatusBarController,
            "refresh_intake_report",
            autospec=True,
            side_effect=lambda controller, **_kwargs: None,
        ):
            self.controller.current_intake_report = report(
                [probe("claude", "Claude", wire=NOW - 30.0)]
            )
            body = self.controller.why_panel_body()
        self.assertIn("writing to the log, nothing arriving", body)
        self.assertIn("This panel explains the decision.", body)


class IntakeRefreshTests(unittest.TestCase):
    """The controller owns the probe so the menu never pays for it."""

    def setUp(self) -> None:
        self.status_bar, self.controller, self.button = isolated_controller(self)

    def test_the_filesystem_probe_is_cached_but_delivery_is_not(self) -> None:
        # The probe runs off-main in production; a synchronous stand-in
        # delivers its result inline so the caching contract is observable.
        probes = (probe("claude", "Claude", wire=NOW - 60.0),)
        from sidepulse.intake_runtime import IntakeProbeResult

        controller = self.controller
        status_bar = self.status_bar

        class _SyncIntakeService:
            def request(self, _callback):
                controller.applyIntakeProbeResult_(
                    IntakeProbeResult(1, tuple(status_bar.probe_providers()))
                )
                return 1

            def close(self):
                return None

        controller._production_intake_service = _SyncIntakeService()
        with patch.object(
            self.status_bar, "probe_providers", return_value=probes
        ) as probe_providers:
            first = self.controller.refresh_intake_report()
            second = self.controller.refresh_intake_report()
        self.assertEqual(probe_providers.call_count, 1)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNot(first, second)

    def test_installing_a_hook_reprobes_instead_of_waiting_out_the_cache(self) -> None:
        calls: list[str] = []

        def _probe(**_kwargs):
            calls.append("probe")
            return (probe("claude", "Claude"),)

        # Instance attributes, not class patches: refresh_ is an ObjC
        # selector and cannot be replaced on the class.
        for name in (
            "reload_monitor",
            "refresh_settings_window",
            "refresh_setup_window",
            "refresh_",
            "set_settings_message",
        ):
            setattr(self.controller, name, lambda *_args, **_kwargs: None)
        with patch.object(self.status_bar, "probe_providers", side_effect=_probe):
            self.controller.refresh_intake_report()
            self.controller.hooksUpdated_(
                {"ok": True, "changed": True, "provider": "claude", "install": True}
            )
        self.assertEqual(len(calls), 2)

    def test_a_probe_that_explodes_never_takes_the_menu_bar_with_it(self) -> None:
        with patch.object(
            self.status_bar, "probe_providers", side_effect=OSError("boom")
        ):
            self.assertIsNone(self.controller.refresh_intake_report())
        self.assertIsNone(self.controller.current_intake_report)

    def _run_refresh(self, order: list[str] | None = None) -> None:
        """One real refresh_ tick, with only the hardware tails stubbed.

        This machine has real SidePulse volumes mounted; the LED and
        keep-awake tails are not what these tests are about.
        """
        record = order if order is not None else []
        with (
            patch.object(
                self.status_bar.StatusBarController,
                "refresh_intake_report",
                autospec=True,
                side_effect=lambda _self, **_kwargs: record.append("intake"),
            ),
            patch.object(
                self.status_bar.StatusBarController,
                "set_status",
                autospec=True,
                side_effect=lambda _self, *_args, **_kwargs: record.append("status"),
            ),
            patch.object(
                self.status_bar.StatusBarController, "sync_keep_awake", autospec=True
            ),
            patch.object(self.status_bar.StatusBarController, "sync_leds", autospec=True),
        ):
            self.controller.refresh_(None)

    def test_the_report_is_current_before_the_title_is_written(self) -> None:
        """refresh_() must judge intake before set_status, or the menu bar
        keeps saying Idle for one more tick after it already knew better."""
        order: list[str] = []
        self._run_refresh(order)
        self.assertEqual(order[:2], ["intake", "status"])

    def test_every_refresh_repaints_an_open_why_panel(self) -> None:
        """The panel is only honest if the tick that changes the light also
        changes the explanation."""
        written: list[str] = []
        self.controller.why_panel_window = SimpleNamespace(isVisible=lambda: True)
        self.controller.why_panel_text_view = SimpleNamespace(setString_=written.append)
        self._run_refresh()
        self.assertEqual(len(written), 1)
        self.assertIn("THE LIGHT RIGHT NOW", written[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
