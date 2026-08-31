from __future__ import annotations

import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import call, patch

import sidepulse.status_bar_legacy as status_bar_legacy
import sidepulse.why_panel as why_panel
from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.core_state import StateDelta
from sidepulse.dnd_policy import (
    DndMode,
    DndSource,
    compose_dnd_contributions,
    contribution_for_mode,
)
from sidepulse.focus_status import (
    FocusActivity,
    FocusAuthorization,
    FocusStatusObservation,
)
from sidepulse.local_health import LocalHealthTiming
from sidepulse.presentation_policy import (
    FiniteCue,
    FiniteCueState,
    GlanceOverrideReason,
    GlanceSemantic,
    ResolvedGlance,
    SemanticGlyph,
)
from sidepulse.why_light_context import (
    FocusObservation,
    FocusOutcome,
    GlobalSurfaceRole,
    LightSemantic,
    OutputTimingSource,
    ReduceMotionDecision,
    ValueAvailability,
    WinningPriority,
)
from tests.test_sidepulse import isolate_controller


class WhyLightWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_controller(self)
        self.controller._current_resolved_glance = self._glance(
            GlanceSemantic.ACTIVE
        )
        self.controller.last_snapshot = SimpleNamespace(
            statuses=(SimpleNamespace(age_seconds=lambda: 12.25),),
            operator_events=(),
        )
        self.controller._focus_observation_available = False
        self.controller._focus_ids_cache = (time.monotonic(), [])
        self.controller.leds_enabled = False
        self.controller._device_inventory_candidates = ()
        self.controller.settings = replace(
            self.controller.settings,
            virtual_status_device_enabled=False,
        )

    @staticmethod
    def _glance(
        semantic: GlanceSemantic,
        *,
        override: GlanceOverrideReason = GlanceOverrideReason.NONE,
        cue: FiniteCue | None = None,
    ) -> ResolvedGlance:
        return ResolvedGlance(
            semantic=semantic,
            glyph=SemanticGlyph.REST,
            cue=cue,
            override_reason=override,
            relay_epoch=1.0,
            next_visual_change_at=None,
        )

    def test_controller_projects_the_current_semantic_priority_and_source_age(
        self,
    ) -> None:
        context = self.controller.current_why_light_context()

        self.assertIs(context.selected_semantic, LightSemantic.ACTIVE)
        self.assertIs(context.winning_priority, WinningPriority.P4)
        self.assertIs(context.source_age.availability, ValueAvailability.AVAILABLE)
        self.assertEqual(context.source_age.seconds, 12.25)

    def test_context_counts_only_bounded_current_cue_suppressions_without_keys(
        self,
    ) -> None:
        private_key = "failure:private-session-and-project"
        pending = FiniteCue(
            private_key,
            GlanceSemantic.FRESH_FAILURE,
            2,
            0.4,
        )
        self.controller._status_cue_candidates = (pending,)
        self.controller._status_finite_cues = FiniteCueState(
            active=None,
            pending=pending,
            next_deadline=None,
            overflowed=False,
        )

        context = self.controller.current_why_light_context()
        body = self.controller.why_panel_body()

        self.assertEqual(context.suppressions.fresh_failure, 1)
        self.assertEqual(context.suppressions.total, 1)
        self.assertNotIn(private_key, body)

    def test_focus_observation_keeps_unavailable_distinct_from_inactive(self) -> None:
        unavailable = self.controller.current_why_light_context()
        projection = self.controller.current_dnd_projection()
        self.controller.dnd_controller = SimpleNamespace(
            projection=projection,
            focus_observation=FocusStatusObservation(
                FocusAuthorization.AUTHORIZED,
                FocusActivity.INACTIVE,
            ),
        )
        inactive = self.controller.current_why_light_context()
        self.controller.dnd_controller = SimpleNamespace(
            projection=projection,
            focus_observation=FocusStatusObservation(
                FocusAuthorization.AUTHORIZED,
                FocusActivity.ACTIVE,
            ),
        )
        self.controller._current_resolved_glance = self._glance(
            GlanceSemantic.REST,
            override=GlanceOverrideReason.FOCUS,
        )
        suppressed = self.controller.current_why_light_context()

        self.assertIs(unavailable.focus_dnd.observation, FocusObservation.UNAVAILABLE)
        self.assertIs(inactive.focus_dnd.observation, FocusObservation.INACTIVE)
        self.assertIs(inactive.focus_dnd.outcome, FocusOutcome.ALLOWED)
        self.assertIs(suppressed.focus_dnd.observation, FocusObservation.ACTIVE)
        self.assertIs(suppressed.focus_dnd.outcome, FocusOutcome.SUPPRESSED)

    def test_private_focus_cache_cannot_elevate_an_inactive_public_observation(self) -> None:
        self.controller._focus_ids_cache = (
            time.monotonic(),
            ["private-focus-id"],
        )
        self.controller.dnd_controller = SimpleNamespace(
            projection=self.controller.current_dnd_projection(),
            focus_observation=FocusStatusObservation(
                FocusAuthorization.AUTHORIZED,
                FocusActivity.INACTIVE,
            ),
        )
        self.controller._current_resolved_glance = self._glance(
            GlanceSemantic.REST,
            override=GlanceOverrideReason.FOCUS,
        )
        with patch.object(
            self.status_bar.focus_sync,
            "active_focus_mode_identifiers",
            side_effect=AssertionError("the panel must not refresh Focus state"),
        ):
            context = self.controller.current_why_light_context()

        self.assertIs(context.focus_dnd.observation, FocusObservation.INACTIVE)
        self.assertIs(context.focus_dnd.outcome, FocusOutcome.ALLOWED)

    def test_focus_observation_remains_valid_through_the_normal_poll_interval(
        self,
    ) -> None:
        self.controller.dnd_controller = SimpleNamespace(
            projection=self.controller.current_dnd_projection(),
            focus_observation=FocusStatusObservation(
                FocusAuthorization.AUTHORIZED,
                FocusActivity.ACTIVE,
            ),
        )

        context = self.controller.current_why_light_context()

        self.assertIs(context.focus_dnd.observation, FocusObservation.ACTIVE)

    def test_context_reuses_the_retained_bounded_dnd_projection(self) -> None:
        projection = compose_dnd_contributions(
            (contribution_for_mode(DndSource.MANUAL, DndMode.MUTE),),
            next_transition_epoch=1_800_000_000.0,
        )
        self.controller.dnd_controller = SimpleNamespace(
            projection=projection,
            focus_observation=FocusStatusObservation(
                FocusAuthorization.AUTHORIZED,
                FocusActivity.ACTIVE,
            ),
        )

        context = self.controller.current_why_light_context()
        body = self.controller.why_panel_body(why_context=context)

        self.assertEqual(context.focus_dnd.dnd_modes, (DndMode.MUTE,))
        self.assertEqual(context.focus_dnd.dnd_sources, (DndSource.MANUAL,))
        self.assertEqual(context.focus_dnd.dnd_return_epoch, 1_800_000_000.0)
        self.assertIn(
            "DND Mute; source Manual; returns 2027-01-15 08:00Z",
            body,
        )

    def test_context_reports_global_surface_scope_and_reduce_motion_substitution(
        self,
    ) -> None:
        cue = FiniteCue(
            "completion:private-event-key",
            GlanceSemantic.FRESH_COMPLETION,
            2,
            0.4,
        )
        self.controller.settings = replace(
            self.controller.settings,
            virtual_status_device_enabled=True,
        )
        self.controller.leds_enabled = True
        self.controller._device_inventory_candidates = (SimpleNamespace(),)
        self.controller._accessibility_display_preferences = (
            AccessibilityDisplayPreferences(reduce_motion=True)
        )
        self.controller._current_resolved_glance = self._glance(
            GlanceSemantic.FRESH_COMPLETION
        )
        self.controller._status_cue_candidates = (cue,)
        self.controller._status_finite_cues = FiniteCueState(
            active=None,
            pending=None,
            next_deadline=None,
            overflowed=True,
        )

        context = self.controller.current_why_light_context()

        self.assertIs(
            context.surface_role,
            GlobalSurfaceRole.SCREEN_BAR_AND_PHYSICAL,
        )
        self.assertIs(
            context.reduce_motion,
            ReduceMotionDecision.STATIC_SUBSTITUTED,
        )
        self.assertEqual(context.suppressions.fresh_completion, 1)

    def test_active_light_reports_continuous_motion_replaced_by_reduce_motion(
        self,
    ) -> None:
        self.controller._accessibility_display_preferences = (
            AccessibilityDisplayPreferences(reduce_motion=True)
        )
        self.controller._current_resolved_glance = self._glance(
            GlanceSemantic.ACTIVE
        )
        self.controller._status_cue_candidates = ()

        context = self.controller.current_why_light_context()

        self.assertIs(
            context.reduce_motion,
            ReduceMotionDecision.STATIC_SUBSTITUTED,
        )

    def test_panel_body_appends_the_fixed_context_without_refreshing_intake(
        self,
    ) -> None:
        with patch.object(
            self.controller,
            "refresh_intake_report",
            side_effect=AssertionError("panel projection must use cached intake"),
        ):
            body = self.controller.why_panel_body()

        self.assertIn("Current light context", body)
        self.assertIn("Semantic: Active work", body)
        self.assertIn("Winning priority: P4", body)
        self.assertIn("Source age: 12.2 seconds", body)
        self.assertIn("Scene: Unavailable", body)

    def test_refresh_and_show_preserve_the_panel_reading_position(self) -> None:
        self.controller.why_panel_window = SimpleNamespace(isVisible=lambda: True)
        self.controller.why_panel_text_view = SimpleNamespace(
            setString_=lambda _value: None
        )
        with (
            patch.object(
                self.controller,
                "why_panel_body",
                return_value="BODY",
            ),
            patch.object(
                why_panel,
                "set_text_preserving_position",
                autospec=True,
            ) as helper,
            patch.object(status_bar_legacy, "present_window", autospec=True),
            patch.object(status_bar_legacy, "activate_app", autospec=True),
        ):
            self.assertTrue(self.controller.refresh_why_panel())
            self.controller.show_why_panel()

        self.assertEqual(
            helper.call_args_list,
            [
                call(self.controller.why_panel_text_view, "BODY"),
                call(self.controller.why_panel_text_view, "BODY"),
            ],
        )

    def test_production_context_reuses_screen_bar_renderer_timing(self) -> None:
        renderer_timing = LocalHealthTiming(
            count=7,
            p50_ms=22.2,
            p95_ms=44.4,
            latest_ms=33.3,
        )
        health = SimpleNamespace(
            source_freshness_seconds=12.25,
            screen_bar_renderer_latency=renderer_timing,
            hardware_write_latency=None,
            refresh_duration=LocalHealthTiming(
                count=4,
                p50_ms=88.8,
                p95_ms=99.9,
                latest_ms=77.7,
            ),
        )
        self.controller.settings = replace(
            self.controller.settings,
            virtual_status_device_enabled=True,
        )
        with patch.object(
            self.controller,
            "local_health_snapshot",
            return_value=health,
        ) as snapshot:
            context = self.controller.current_why_light_context()

        self.assertIs(context.renderer_timing.availability, ValueAvailability.AVAILABLE)
        self.assertEqual(context.renderer_timing.sample_count, 7)
        self.assertEqual(context.renderer_timing.latest_ms, 33.3)
        self.assertEqual(context.renderer_timing.p50_ms, 22.2)
        self.assertEqual(context.renderer_timing.p95_ms, 44.4)
        self.assertIs(
            context.renderer_timing.source,
            OutputTimingSource.SCREEN_BAR_RENDERER,
        )
        snapshot.assert_called_once_with()

    def test_physical_only_timing_is_labeled_as_hardware_write_latency(
        self,
    ) -> None:
        hardware_timing = LocalHealthTiming(
            count=9,
            p50_ms=5.0,
            p95_ms=8.0,
            latest_ms=6.0,
        )
        health = SimpleNamespace(
            source_freshness_seconds=12.25,
            screen_bar_renderer_latency=None,
            hardware_write_latency=hardware_timing,
            refresh_duration=None,
        )
        self.controller.leds_enabled = True
        self.controller._device_inventory_candidates = (object(),)

        with patch.object(
            self.controller,
            "local_health_snapshot",
            return_value=health,
        ):
            context = self.controller.current_why_light_context()

        self.assertIs(
            context.renderer_timing.source,
            OutputTimingSource.PHYSICAL_HARDWARE_WRITE,
        )
        self.assertIn(
            "Hardware write latency: latest 6.0 ms",
            status_bar_legacy.format_why_light_context(context),
        )

    def test_renderer_timing_stays_unavailable_when_only_refresh_duration_exists(
        self,
    ) -> None:
        health = SimpleNamespace(
            source_freshness_seconds=12.25,
            screen_bar_renderer_latency=None,
            hardware_write_latency=None,
            refresh_duration=LocalHealthTiming(
                count=7,
                p50_ms=22.2,
                p95_ms=44.4,
                latest_ms=33.3,
            ),
        )
        with patch.object(
            self.controller,
            "local_health_snapshot",
            return_value=health,
        ):
            context = self.controller.current_why_light_context()

        self.assertIs(
            context.renderer_timing.availability,
            ValueAvailability.UNAVAILABLE,
        )

    def test_one_panel_render_samples_mutable_local_health_once(self) -> None:
        real_snapshot = self.controller.local_health_snapshot()
        with patch.object(
            self.status_bar.StatusBarController,
            "local_health_snapshot",
            autospec=True,
            return_value=real_snapshot,
        ) as snapshot:
            body = self.controller.why_panel_body()

        self.assertIn("Output timing:", body)
        self.assertIn("Local Health (current run, never sent)", body)
        self.assertEqual(snapshot.call_count, 1)

    def test_production_panel_preserves_injected_context_contract(self) -> None:
        context = self.controller.current_why_light_context()

        with patch.object(
            self.controller,
            "_why_light_context_from_health",
            side_effect=AssertionError("supplied context must be reused"),
        ):
            body = self.controller.why_panel_body(why_context=context)

        self.assertIn("Current light context", body)
        self.assertIn("Local Health (current run, never sent)", body)

    def test_live_refresh_repaints_panel_after_current_refresh_metric(self) -> None:
        events: list[tuple[str, bool]] = []
        delta = StateDelta(1, 0, 0, frozenset(), False)

        def legacy_refresh(controller, _sender):
            events.append(
                ("legacy", controller._performance().snapshot().metric("refresh") is not None)
            )
            controller.refresh_why_panel()

        def repaint(controller):
            events.append(
                ("panel", controller._performance().snapshot().metric("refresh") is not None)
            )
            controller.local_health_snapshot()
            return True

        self.controller._production_force_refresh = True
        self.controller._production_refresh_active = False
        refresh = self.controller.__class__.refresh_.callable
        with (
            patch.object(self.controller, "_observe_refresh_state", return_value=delta),
            patch.dict(
                refresh.__globals__,
                {
                    "_LegacyStatusBarController": SimpleNamespace(
                    refresh_=legacy_refresh,
                    refresh_why_panel=repaint,
                    )
                },
            ),
            patch.object(
                self.controller,
                "local_health_snapshot",
                wraps=self.controller.local_health_snapshot,
            ) as health,
        ):
            refresh(self.controller, None)

        self.assertEqual(events, [("legacy", False), ("panel", True)])
        self.assertEqual(health.call_count, 1)

    def test_real_panel_assigns_keyboard_focus_and_accessibility_copy(self) -> None:
        window = self.status_bar.build_why_panel_window(self.controller)
        text_view = self.controller.why_panel_text_view

        self.assertIs(window.initialFirstResponder(), text_view)
        self.assertEqual(
            text_view.accessibilityLabel(),
            "Why this light explanation",
        )
        self.assertIn("selectable", text_view.accessibilityHelp().lower())

    def test_display_poll_caches_focus_observation_availability(self) -> None:
        command = self.status_bar.RuntimeWorkCommand(
            self.status_bar.RuntimeWorkerDomain.OS_POLL,
            "display-environment",
            1,
            time.monotonic() + 10.0,
            self.status_bar.DisplayEnvironmentRequest(
                read_brightness=False,
                read_focus=True,
                read_accessibility=False,
            ),
        )
        with patch.object(
            self.status_bar.focus_sync,
            "active_focus_mode_identifiers",
            side_effect=self.status_bar.focus_sync.FocusSyncUnavailableError(
                "unavailable"
            ),
        ):
            result = self.controller._execute_os_poll_command(command)

        self.controller._apply_display_environment_result(result)

        self.assertFalse(result.focus_available)
        self.assertFalse(self.controller._focus_observation_available)
