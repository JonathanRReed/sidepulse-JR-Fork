from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from sidepulse.signal_selection import SIGNAL_CLAIM_PRECEDENCE

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "sidepulse"
STATUS_BAR_LEGACY = SRC / "status_bar_legacy.py"

# These are the audited byte sizes at the start of the production rescue or at
# the point a compatibility implementation was frozen behind a facade.
# They may shrink. Increasing one requires extracting behavior.
LEGACY_SIZE_CEILINGS = {
    # P3.39 adds the bounded AppKit coordinator and persistence-result selector
    # for Safe Clear Agents. Exact receipt, store, and popover behavior remain
    # outside this file; 811 KB is the new no-growth ceiling.
    SRC / "status_bar_legacy.py": 811_000,
    SRC / "_collector_legacy.py": 117_707,
    SRC / "_integration_settings_legacy.py": 12_000,
    SRC / "settings_window.py": 220_568,
    ROOT / "tests" / "test_sidepulse.py": 1_065_785,
}

NON_LEGACY_MODULE_MAX_BYTES = 184_320
EXEMPT_SOURCE_MODULES = {
    "status_bar_legacy.py",
    "settings_window.py",
}

PURE_PRODUCTION_MODULES = {
    "announcer_content.py",
    "announcer_stack.py",
    "application_composition.py",
    "battery_runtime.py",
    "brightness_policy.py",
    "clear_agents.py",
    "completion_visibility.py",
    "core_state.py",
    "device_identity.py",
    "device_inventory.py",
    "effect_selection.py",
    "firmware_validation.py",
    "hook_dedupe.py",
    "integration_compatibility.py",
    "integration_settings.py",
    "intake_runtime.py",
    "ledger_runtime.py",
    "menu_projection.py",
    "notification_arbitration.py",
    "performance_metrics.py",
    "presentation_compiler.py",
    "refresh_admission.py",
    "screen_bar_design.py",
    "settings_navigation.py",
    "signal_selection.py",
    "t3_compat.py",
    "transcript_runtime.py",
    "webhook_delivery.py",
}

# The audited retained controller surface was captured on 2026-08-29 from the
# current 591-method class body. This snapshot is the contract anchor for a
# shrink-only ratchet: removal is allowed, new retained business behavior is not.
AUDITED_STATUS_BAR_CONTROLLER_METHODS_TEXT = """\
_activate_notification_action
_advance_capacity_source_generation
_advance_weather_observation_generation
_apply_accessibility_display_preferences
_apply_calendar_observation_result
_apply_device_inventory_result
_apply_display_environment_result
_apply_hardware_write_result
_apply_lid_observation
_apply_mode_speed
_apply_os_poll_result
_apply_preference_action
_apply_reminders_observation_result
_apply_remote_peer_setting
_apply_screen_bar_geometry_from_sliders
_apply_status_accessibility_text
_apply_studio_builder
_apply_triage_action
_apply_weather_observation_result
_apply_weather_result
_build_presentation_timer_registry
_build_runtime_worker_registry
_calendar_observation_should_run
_calendar_observation_timer_fired
_capacity_refresh_state
_capacity_row_enabled
_capacity_source_enabled
_capacity_source_generation
_claude_capacity_observations
_clear_tip_highlight
_codex_capacity_observations
_commit_colors_and_refresh
_complete_timebox
_contract_capacity_observations
_current_signal_style
_deliver_semantic_notification
_device_inventory_should_run
_device_inventory_timer_fired
_devices_pane_requests_inventory
_discard_status_emphasis_plan
_dispatch_runtime_worker_result
_display_environment_should_run
_display_environment_timer_fired
_enqueue_operator_history_events
_escalation_deadline
_escalation_deadline_fired
_execute_hardware_write_command
_execute_os_poll_command
_execute_weather_command
_finish_capacity_refresh_timer
_finite_ui_deadline
_finite_ui_deadline_fired
_flush_capacity_history_store
_handle_announcer_stack_intent
_hardware_worker_key
_hardware_write_should_run
_install_accessibility_display_observer
_installed_agents_pane_visible
_issue_notification_action
_lid_observation_relevant
_lid_observation_should_run
_lid_observation_timer_fired
_local_usage_result
_mark_reminders_permission_failed
_mutate_device_setting
_normal_capacity_refresh_deadline
_notification_client_for_use
_operator_debug_export
_operator_history_timezone_offset_minutes
_persist_studio_editor_text
_presentation_alcove_observation_fired
_presentation_frame_fallback_fired
_presentation_pointer_peek_fired
_presentation_static_deadline_fired
_preview_should_run
_project_capacity_refresh_state
_prune_notification_action_bindings
_prune_resolved_triage
_publish_mailbox_preferences
_publish_notification_authorization_state
_read_battery_snapshot_uncached
_reconcile_current_presentation_inputs
_record_hook_ingress_receipt
_record_persistence_receipt
_refresh_lid_thumb_selection
_refresh_studio_library_popup
_register_capacity_refresh_start
_reminders_observation_should_run
_reminders_observation_timer_fired
_remove_accessibility_display_observer
_render_signal_card
_republish_operator_surfaces
_reset_celebration_color
_retire_elapsed_ui_deadlines
_save_mailbox_preferences
_save_operator_triage
_save_signal_style
_schedule_capacity_refresh_retry
_schedule_capacity_reset_retry
_schedule_capacity_timer
_send_calibration_test
_set_calendar_observation_active
_set_device_inventory_active
_set_display_environment_active
_set_hardware_write_active
_set_lid_observation_active
_set_operator_history_status
_set_preview_active
_set_reminders_observation_active
_set_signal_color
_set_weather_observation_active
_settings_color_preview_fired
_settings_message_deadline
_settings_message_deadline_fired
_settings_signal_preview_fired
_setup_demo_fired
_slider_event_is_drag
_store_activity_ledger
_studio_builder_rebuild
_studio_builder_state
_sync_hardware_device
_timebox_deadline
_timebox_deadline_fired
_toggle_speed_override
_usage_refresh_source_worker
_usage_refresh_worker
_weather_observation_should_run
_weather_observation_timer_fired
accessibilityDisplayOptionsDidChange_
active_failure_signal
active_focus_ids_cached
active_focus_policy
active_focus_summary
active_led_display_kind
active_led_display_kind_for_device
activity_entries_for_statuses
agent_controller_for_device
agent_render_colors
agents_active_now
animateColorsPreviewTick_
animate_colors_preview_once
animation_library_path
append_operator_history_reel
applicationDidFinishLaunching_
applicationWillTerminate_
applyAccessibilityDisplayOptions_
applyCalendarLead_
applyCalibrationProfile_
applyClosedLidGraceMinutes_
applyCustomColorFromPanel_
applyCycleModeSpeed_
applyCycleSpeed_
applyEscalationThresholds_
applyEscalationWebhook_
applyFadeIntensity_
applyFadePreset_
applyIdleDimSettings_
applyLowBatteryThreshold_
applyNotificationAuthorizationState_
applyOperatorHistoryPersistenceFailure_
applyOperatorHistoryProjection_
applyOperatorHistoryRestore_
applyOperatorHistoryRetentionResult_
applyPalette_
applyRemotePeerRefresh_
applyRoundRobinSpeed_
applyScreenBarSync_
applyStudioAsPowerUp_
applyTimeboxShortcuts_
applyTimerMinutes_
applyUsageSummary_
applyWeatherLocation_
apply_color_change
apply_escalation
apply_night_warmth
battery_controller_for_device
budgeted_signal_style
build_monitor
build_transcript_monitor
burnStudioLookAsPowerUp_
burn_saved_look_as_power_up
calendarAccessResolved_
calibrationLooksWhite_
capacityCountdown_
capacityRefreshDeadline_
capacityRefreshRetry_
capacityResetBoundary_
capacity_detail_models
capacity_history_presentation
capacity_history_store
captureStudioProgram_
changeOperatorHistoryRange_
changeOperatorHistoryRetention_
claude_access_token
clearFinished_
clearOperatorHistory_
clear_capacity_timers
close_status_menu
collect_remote_peer_ledgers
complete_first_launch_setup
confirmClearOperatorHistory_
courtesy_signals_held
current_decision_trace
current_escalation_stage
current_led_targets
current_why_light_context
deleteStudioLook_
device_connected
disableTips_
discover_device_candidates
dismissSettingsMessage_
dismissTip_
display_aggregate_mode
displayed_status_state
drainRuntimeWorker_
effective_brightness_for_device
effective_signal_brightness_for_device
ensure_activity_ledger
ensure_all_settings_panes
ensure_animation_library
ensure_colors_preview_wasm
ensure_settings_pane
escalation_takeover_active
escalation_takeover_program
exportOperatorDiagnostics_
exportOperatorHistory_
failureSignalExpired_
fire_escalation_webhook
fire_timebox_off_shortcut
flash_view
focus_is_active
focus_sync_scale_factor
handle_hook_event_message
hard_ask_live
hard_ask_renders_on_device
has_connected_physical_device
hooksUpdated_
idle_dim_scale_factor
ingest_transcript_fallback
init
installAntigravityHooks_
installClaudeHooks_
installCodexHooks_
installCursorHooks_
installDevinHooks_
installGrokHooks_
installHermesHooks_
installKiroHooks_
installOpenclawHooks_
installOpencodeHooks_
interrupt_budget
interrupt_grant
interrupt_hold_seconds
invalidate_usage_providers
jr_capacity_settings_text
jr_plane_owns_capacity
jr_plane_owns_usage_menu_item
lid_animation_from_fields
light_driver_description
loadStudioLook_
load_operator_local_state
low_power_active
mailboxBoundary_
mark_activity_seen_now
may_interrupt
maybe_refresh_usage_summary
menuDidClose_
menuWillOpen_
merged_ledger_for
migrate_studio_library
night_dim_scale_factor
night_warmth_active
note_glance_decision
note_legacy_hook
notification_authorization_status_text
nudgeCalibrationWarmth_
numberOfRowsInTableView_
observe_connected_devices
observe_operator_history_events
observe_operator_history_triage
openAgentBrowser_
openColorsWindow_
openCustomAgentColor_
openCustomModeColor_
openDeviceCalibrationPopover_
openFullDiskAccessSettings_
openNotificationSystemSettings_
openProjectPage_
openSessionPrimary_
openSettings_
openSetup_
openSignalColorPanel_
openTipPane_
openWhyPanel_
open_custom_color_panel
open_session
peekTick_
peek_program
performAgentBrowserPayload_
performBrowserAction_
pickSignalSwatch_
play_lid_animation
play_transition_flourish
play_transition_flourish_worker
pollLid_
poll_devices_once
popoverDidClose_
post_completion_notification
post_webhook
presentation_capacity_glance
previewLidClosedAnimation_
previewLidOpenAnimation_
previewStudioProgram_
projected_rows_for_device
projection_for_device
publish_local_ledger_now
push_colors_preview_to_device
quiet_active
quit_
quota_runway_state
read_battery_snapshot
rebuild_capacity_refresh_coordinator
rebuild_devices_pane
reconcile_device_runtime
reconcile_installed_agent_inventory
reconcile_lid_observation
reconcile_presentation_timers
reconcile_status_emphasis
recordAgentBrowserVisit_
record_activity_entries
record_capacity_history
record_capacity_threshold_crossings
redrawSetupDemo_
redrawSignalPreviews_
refreshCapacitySources_
refreshFromEvent_
refreshInstalledAgents_
refreshRemotePeersNow_
refreshRemotePeers_
refreshUsageCenterTick_
refresh_
refresh_agent_animation_popups
refresh_capacity_settings_projection
refresh_color_row
refresh_colors_preview
refresh_colors_window
refresh_device_settings_controls
refresh_installed_agent_inventory
refresh_installed_agents_settings_projection
refresh_intake_report
refresh_notification_authorization_controls
refresh_operator_history_projection
refresh_remote_and_cloud_controls
refresh_remote_peers
refresh_screen_bar_preview
refresh_settings_window
refresh_setup_window
refresh_signal_card
refresh_studio_problem_label
refresh_why_panel
release_preview_engines
reload_monitor
remember_connected_devices
reminderAccessResolved_
removeRememberedDevice_
remove_published_ledger
remove_remembered_device
renameStudioLook_
replay_debug_logs
reposition_virtual_status_device_now
requestNotificationPermission_
request_jr_usage_refresh
request_usage_refresh
resetColorsToDefaults_
resetDeviceColorCalibration_
resetLidClosedAnimation_
resetLidOpenAnimation_
resetScreenBarGeometry_
resetStripDevice_
reset_led_controllers_for_device
reset_led_controllers_for_display_change
reset_lid_animation
resolve_presentation_glance
restoreLedDisplay_
revealFocusBinaryInFinder_
runFirstLaunchSetup_
run_first_launch_setup
run_shortcut_named
runtimeTimerFired_
saveCalibrationProfile_
saveLidAnimations_
saveStudioLook_
save_lid_animations_from_fields
schedule_capacity_timers
schedule_event_refresh
schedule_failure_signal_refresh
schedule_mailbox_boundary
schedule_screen_bar_sync
screen_bar_blend_override
screen_bar_channel_gains
screen_bar_click_status
screen_bar_quota_ember_level
selectLidPresetThumb_
selectModeAnimationThumb_
selectModeColorSwatch_
selectSignalPattern_
select_settings_pane
selected_studio_look_name
setAgentAnimation_
setBatteryChargingIdleFromCheckbox_
setBatteryLedDisplayFromCheckbox_
setBatteryPowerPreviewFromCheckbox_
setBlendMode_
setBracketStyle_
setCapacityHistoryRetention_
setClosedLidAwakePolicyFromPopup_
setClosedLidAwakePolicy_
setColorPreset_
setDeviceBlendMode_
setDeviceBlueGain_
setDeviceBrightness_
setDeviceDisplayAgent_
setDeviceDisplayBattery_
setDeviceDisplay_
setDeviceGreenGain_
setDeviceProviderPin_
setDeviceRedGain_
setDeviceRestingGlow_
setDeviceSignalPolicy_
setEscalationTier_
setFocusDimRule_
setFocusProfileRule_
setFocusSignalPolicy_
setGlobalBrightness_
setNightDimFraction_
setPreviewScenario_
setProviderOpenPreference_
setScreenBarGapWidth_
setScreenBarMinGlow_
setSignalIntensity_
setSignalSpeed_
setUsageDisplayMode_
setUsageGraphRange_
set_battery_charging_idle
set_battery_led_display
set_battery_power_preview
set_brightness_preview_dots
set_closed_lid_awake_policy
set_device_auto_brightness
set_device_brightness
set_device_channel_gain
set_device_channel_gains_reset
set_device_display
set_settings_message
set_setup_checkbox
set_status
set_status_emphasis_plan
set_transcript_monitoring
set_virtual_status_device
should_render_multi_agent
showSettingsMessage_
show_colors_window
show_settings_window
show_setup_window
show_setup_window_if_needed
show_why_panel
signalPanelColorChanged_
signal_display_entries
skipFirstLaunchSetup_
startCalibrationTest_
startQuiet_
startTimebox_
start_cloud_ingest_server
start_colors_preview_animation
start_event_server
start_hook_ingress
start_notification_authorization_refresh
start_operator_history_restore
start_operator_history_retention_change
start_remote_peer_refresh
start_remote_peer_timer
status_bar_devices
status_keepalive_targets
stopStudioProgram_
stopTimebox_
stop_cloud_ingest_server
stop_colors_preview_animation
stop_event_server
stop_hook_ingress
stop_remote_peer_timer
store_animation_library
studioBuilderAddStep_
studioBuilderColorChanged_
studioBuilderDurationChanged_
studioBuilderEaseChanged_
studioBuilderLoopToggled_
studioBuilderRemoveStep_
studioValidationDebounceFired_
studio_display_program
studio_led_count
studio_problem_summary
studio_program_problems
sync_closed_lid_awake
sync_keep_awake
sync_leds
sync_virtual_status_device
tableViewSelectionDidChange_
tableView_isGroupRow_
tableView_shouldSelectRow_
tableView_viewForTableColumn_row_
testSignal_
test_signal_program
textDidChange_
textDidEndEditing_
timebox_active
timebox_overtime
timebox_overtime_minutes
timer_display_program
timer_fill_fraction
toggleCalendarAlerts_
toggleCalibrationCompare_
toggleCalibrationFineTune_
toggleCapacityHistory_
toggleClaudePlanLimits_
toggleClaudeTranscripts_
toggleCloudIngest_
toggleCodexPercent_
toggleCodexTranscripts_
toggleColorByProject_
toggleColorPreviewLive_
toggleCompletionNotification_
toggleCompletionSweep_
toggleCycleUseGlobalSpeed_
toggleDeviceAutoBrightness_
toggleDoneCelebration_
toggleFocusSync_
toggleIdleDim_
toggleKeepAwakeOnBattery_
toggleLinkScreenBarToHardware_
toggleLowBatteryAlert_
toggleMenuBarLabel_
toggleNightWarmth_
toggleQuietHour_
toggleQuotaAlerts_
toggleReminderAlerts_
toggleRemoteInterrupts_
toggleRemoteMachineInterrupt_
toggleRemoteMessages_
toggleRemotePeers_
toggleRemotePublish_
toggleRoundRobinUseGlobalSpeed_
toggleScreenBarFollowAlcove_
toggleScreenBarFullScreen_
toggleScreenBarGauges_
toggleScreenBarWrapsMenuBar_
toggleSubagentAsksAlert_
toggleUrgencyAlert_
toggleUsageGraphProvider_
toggleVirtualStatusDevice_
toggleWeatherAlerts_
toggleWebhookEvent_
track_ask_blocked
track_completions
track_quota_thresholds
track_working
trailingRefreshFire_
trim_oversized_state_logs
uninstallAntigravityHooks_
uninstallClaudeHooks_
uninstallCodexHooks_
uninstallCursorHooks_
uninstallDevinHooks_
uninstallGrokHooks_
uninstallHermesHooks_
uninstallKiroHooks_
uninstallOpenclawHooks_
uninstallOpencodeHooks_
uninstallSdEjectGuard_
uninstall_sd_eject_guard_from_setup
update_attention_projection
update_battery_power_preview
update_hooks
update_status_menu
update_usage_menu_fields
userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_
userNotificationCenter_willPresentNotification_withCompletionHandler_
validate_studio_program
virtual_display_state
webhook_event_enabled
why_panel_body
windowWillClose_
"""
AUDITED_STATUS_BAR_CONTROLLER_METHODS = tuple(
    line
    for line in AUDITED_STATUS_BAR_CONTROLLER_METHODS_TEXT.splitlines()
    if line.strip()
)
AUDITED_STATUS_BAR_CONTROLLER_METHOD_COUNT = 591
AUDITED_STATUS_BAR_CONTROLLER_METHOD_DIGEST = (
    "f3ee22949fad8b51fb99ff78b1cb556538270ad4c790abe5641fcd6688ecb01d"
)
AUDITED_STATUS_BAR_CONTROLLER_METHOD_SET = frozenset(
    AUDITED_STATUS_BAR_CONTROLLER_METHODS
)

P338_DND_RUNTIME_METHODS = frozenset(
    {
        "_consume_restricted_finite_cues",
        "_dnd_popup_value",
        "_dnd_projection_changed",
        "_finish_dnd_change",
        "_install_dnd_environment_observers",
        "_refresh_dnd_environment",
        "_refresh_dnd_settings_controls",
        "_remove_dnd_environment_observers",
        "_replace_dnd_schedule",
        "_set_dnd_for_duration",
        "_set_dnd_for_one_hour",
        "applicationDidBecomeActive_",
        "current_dnd_projection",
        "dndScreensDidSleep_",
        "dndScreensDidWake_",
        "dndSessionDidBecomeActive_",
        "dndSessionDidResignActive_",
        "dndSystemClockDidChange_",
        "dndSystemTimeZoneDidChange_",
        "dndWorkspaceDidWake_",
        "dndWorkspaceWillSleep_",
        "dnd_display_admits",
        "endDndOverride_",
        "openDndSettings_",
        "requestDndFocusAuthorization_",
        "resumeDndUntilNextChange_",
        "setDndAsksOnlyForHour_",
        "setDndDarkForHour_",
        "setDndDimForHour_",
        "setDndDimFraction_",
        "setDndFocusMode_",
        "setDndMuteForHour_",
        "setDndPauseForHour_",
        "setDndScheduleEndTime_",
        "setDndScheduleMode_",
        "setDndScheduleStartTime_",
        "standing_display_admission",
        "startDndOneHour_",
        "toggleDndSchedule_",
        "webhook_effect_allowed",
        "webhook_interrupt_kind",
    }
)

P339_CLEAR_AGENTS_RUNTIME_METHODS = frozenset(
    {
        "_clear_agents_popover_closed",
        "_confirm_clear_agents_preview",
        "_handle_clear_agents_popover_action",
        "_refresh_clear_agents_preview",
        "_show_clear_agents_preview_state",
        "_start_clear_agents_undo",
        "_submit_clear_agents_plan",
        "applyClearAgentsPersistenceResult_",
        "clearAgents_",
    }
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class_methods(path: Path, class_name: str) -> tuple[str, ...]:
    tree = _tree(path)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return tuple(
        sorted(
            node.name
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
    )


def _call_names(node: ast.AST) -> tuple[str, ...]:
    calls: list[str] = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        function = call.func
        if isinstance(function, ast.Name):
            calls.append(function.id)
            continue
        if not isinstance(function, ast.Attribute):
            continue
        parts = [function.attr]
        owner = function.value
        while isinstance(owner, ast.Attribute):
            parts.append(owner.attr)
            owner = owner.value
        if isinstance(owner, ast.Name):
            parts.append(owner.id)
        calls.append(".".join(reversed(parts)))
    return tuple(calls)


def _digest(names: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()


def test_legacy_monoliths_can_only_shrink() -> None:
    for path, ceiling in LEGACY_SIZE_CEILINGS.items():
        assert path.stat().st_size <= ceiling, (
            f"{path.relative_to(ROOT)} grew beyond its audited ceiling of "
            f"{ceiling:,} bytes. Extract behavior into a typed module."
        )


def test_non_legacy_source_modules_stay_below_the_monolith_threshold() -> None:
    oversized = {
        path.name: path.stat().st_size
        for path in SRC.glob("*.py")
        if path.name not in EXEMPT_SOURCE_MODULES
        and path.stat().st_size > NON_LEGACY_MODULE_MAX_BYTES
    }
    assert not oversized, f"production modules are too large: {oversized}"


def test_pure_production_modules_do_not_use_baseexception_or_dynamic_exec() -> None:
    failures = []
    for name in sorted(PURE_PRODUCTION_MODULES):
        path = SRC / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                handler = node.type
                if isinstance(handler, ast.Name) and handler.id == "BaseException":
                    failures.append(f"{name}:{node.lineno}: BaseException")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"eval", "exec", "compile"}:
                    failures.append(f"{name}:{node.lineno}: {node.func.id}")
    assert not failures, f"unsafe production constructs: {failures}"


def test_production_facades_do_not_grow_into_second_monoliths() -> None:
    ceilings = {
        SRC / "status_bar.py": 80_000,
        SRC / "collector.py": 20_000,
        SRC / "integration_settings.py": 12_000,
        SRC / "provider_usage_status_bar.py": 40_000,
        SRC / "screen_bar_runtime.py": 24_000,
        SRC / "settings_category_runtime.py": 32_000,
    }
    oversized = {
        path.name: path.stat().st_size
        for path, ceiling in ceilings.items()
        if path.stat().st_size > ceiling
    }
    assert not oversized, f"production facades are too large: {oversized}"


def test_hook_ingress_keeps_one_bounded_fifo_worker() -> None:
    text = (SRC / "hook_ingress.py").read_text(encoding="utf-8")

    assert "MAX_HOOK_INGRESS_ACCEPTED: Final = 32" in text
    assert text.count("target=self._run") == 1
    assert text.count('name="JRBarHookIngressWorker"') == 1
    assert "deque[_AcceptedHook]" in text


def test_generated_provider_bridges_cannot_detach_or_untrack_children() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SRC / "providers.py", SRC / "install.py")
    )

    assert "child.unref" not in text
    assert "detached: true" not in text
    assert "await child.exited" in text
    assert 'child.once("close", resolve)' in text


def test_retained_status_bar_controller_surface_matches_the_audited_snapshot() -> None:
    assert len(AUDITED_STATUS_BAR_CONTROLLER_METHODS) == (
        AUDITED_STATUS_BAR_CONTROLLER_METHOD_COUNT
    )
    assert (
        _digest(AUDITED_STATUS_BAR_CONTROLLER_METHODS)
        == AUDITED_STATUS_BAR_CONTROLLER_METHOD_DIGEST
    )


def test_retained_status_bar_controller_can_shrink_but_not_grow() -> None:
    current_methods = _class_methods(STATUS_BAR_LEGACY, "StatusBarController")
    allowed = (
        AUDITED_STATUS_BAR_CONTROLLER_METHOD_SET
        | P338_DND_RUNTIME_METHODS
        | P339_CLEAR_AGENTS_RUNTIME_METHODS
    )
    assert set(current_methods) <= allowed
    assert len(current_methods) <= (
        AUDITED_STATUS_BAR_CONTROLLER_METHOD_COUNT
        + len(P338_DND_RUNTIME_METHODS)
        + len(P339_CLEAR_AGENTS_RUNTIME_METHODS)
    )


def test_main_remains_composition_only() -> None:
    main_node = next(
        node
        for node in _tree(STATUS_BAR_LEGACY).body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    calls = _call_names(main_node)

    assert set(calls) == {
        "another_instance_alive",
        "compose_status_bar_application",
        "print",
        "run_status_bar",
    }
    assert "NSApplication.sharedApplication" not in calls
    assert "StatusBarController.alloc" not in calls
    assert "setDelegate_" not in calls
    assert "app.run" not in calls


def test_run_status_bar_remains_the_single_appkit_delegate_handoff() -> None:
    run_node = next(
        node
        for node in _tree(STATUS_BAR_LEGACY).body
        if isinstance(node, ast.FunctionDef) and node.name == "run_status_bar"
    )
    calls = _call_names(run_node)

    assert "NSApplication.sharedApplication" in calls
    assert "StatusBarController.alloc" in calls
    assert "app.setDelegate_" in calls
    assert "app.run" in calls
    assert "another_instance_alive" not in calls
    assert "compose_status_bar_application" not in calls


def test_retained_signal_selection_delegates_without_owning_policy() -> None:
    class_node = next(
        node
        for node in _tree(STATUS_BAR_LEGACY).body
        if isinstance(node, ast.ClassDef) and node.name == "StatusBarController"
    )
    method = next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "active_led_display_kind_for_device"
    )
    calls = _call_names(method)
    source = ast.get_source_segment(
        STATUS_BAR_LEGACY.read_text(encoding="utf-8"),
        method,
    )

    assert calls.count("select_active_led_display_kind") == 1
    assert source is not None
    assert "claims = (" not in source
    assert "DEVICE_MUTABLE_SIGNAL_KINDS" not in source
    assert '== "asks_only"' not in source


def test_retained_effect_actions_delegate_without_owning_selection_policy() -> None:
    class_node = next(
        node
        for node in _tree(STATUS_BAR_LEGACY).body
        if isinstance(node, ast.ClassDef) and node.name == "StatusBarController"
    )
    required_calls = {
        "setAgentAnimation_": "plan_provider_animation_selection",
        "setPreviewScenario_": "preview_scenario_from_payload",
        "setColorPreset_": "plan_color_preset_selection",
        "setBlendMode_": "plan_blend_mode_selection",
    }
    forbidden_policy = {
        "PREVIEW_SCENARIO_CHOICES",
        "PROVIDER_ANIMATION_CHOICES",
        "with_agent_animation",
        "with_blend_mode",
        "apply_preset",
    }

    for method_name, required_call in required_calls.items():
        method = next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )
        calls = _call_names(method)
        source = ast.get_source_segment(
            STATUS_BAR_LEGACY.read_text(encoding="utf-8"), method
        )
        assert calls.count(required_call) == 1
        assert source is not None
        assert all(token not in source for token in forbidden_policy)


def test_retained_brightness_methods_delegate_without_owning_policy() -> None:
    class_node = next(
        node
        for node in _tree(STATUS_BAR_LEGACY).body
        if isinstance(node, ast.ClassDef) and node.name == "StatusBarController"
    )
    contracts = {
        "effective_signal_brightness_for_device": (
            "plan_signal_brightness",
            (
                "normalize_brightness(",
                "max(",
                "focus_sync_scale_factor() <= 0.0",
            ),
        ),
        "effective_brightness_for_device": (
            "plan_ambient_brightness",
            (
                "* self.idle_dim_scale_factor()",
                "* self.focus_sync_scale_factor()",
                "* self.night_dim_scale_factor()",
                "max(",
                "MIN_ESCALATION_VISIBLE_BRIGHTNESS",
                "255.0 * float(self.settings.screen_bar_min_glow)",
            ),
        ),
    }

    for method_name, (required_call, forbidden_tokens) in contracts.items():
        method = next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )
        calls = _call_names(method)
        source = ast.get_source_segment(
            STATUS_BAR_LEGACY.read_text(encoding="utf-8"), method
        )
        assert calls.count(required_call) == 1
        assert not any(
            isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult)
            for node in ast.walk(method)
        )
        assert source is not None
        assert all(token not in source for token in forbidden_tokens)

    retained_source = STATUS_BAR_LEGACY.read_text(encoding="utf-8")
    assert "MIN_ESCALATION_VISIBLE_BRIGHTNESS =" not in retained_source


def test_completion_visibility_and_announcer_adapters_delegate_to_pure_policy() -> None:
    tree = _tree(STATUS_BAR_LEGACY)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "StatusBarController"
    )
    module_contracts = {
        "eligible_mailbox_completion_statuses": "select_clearable_completions",
        "unseen_completions": "select_unseen_completions",
    }
    controller_contracts = {
        "menuWillOpen_": "plan_seen_completion_ids",
    }

    for function_name, required_call in module_contracts.items():
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        assert _call_names(function).count(required_call) == 1

    for method_name, required_call in controller_contracts.items():
        method = next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )
        assert _call_names(method).count(required_call) == 1

    announcer_sync = next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "sync_virtual_status_device"
    )
    announcer_calls = _call_names(announcer_sync)
    assert announcer_calls.count("reconcile_announcer_stack") == 1
    assert announcer_calls.count("project_announcer_stack") == 1
    assert announcer_calls.count("self.virtual_status_device.set_announcer_stack") == 1
    assert "project_announcer_content" not in announcer_calls

    virtual_tree = _tree(SRC / "virtual_device.py")
    announcer_adapter = next(
        node
        for node in virtual_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "announcer_text_for_attention"
    )
    assert _call_names(announcer_adapter).count("project_announcer_content") == 1


def test_safe_clear_agents_replaces_every_legacy_clear_finished_path() -> None:
    production_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SRC.rglob("*.py"))
    )

    assert "clearFinished_" not in production_source
    assert "plan_clear_finished" not in production_source
    assert "Clear Finished" not in production_source
    assert "clearCompleted:" not in production_source
    assert "cleared_session_ids" not in production_source

    menu_source = (SRC / "menu_projection.py").read_text(encoding="utf-8")
    assert '"Clear Agents…"' in menu_source
    assert 'action="clearAgents:"' in menu_source


def test_retained_safe_clear_agents_delegates_to_typed_components() -> None:
    tree = _tree(STATUS_BAR_LEGACY)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "StatusBarController"
    )
    controller_contracts = {
        "clearAgents_": "ClearAgentsPopoverPresenter",
        "_confirm_clear_agents_preview": "plan_clear_agents_commit",
        "_start_clear_agents_undo": "plan_clear_agents_undo",
        "_submit_clear_agents_plan": "save_clear_agents_state",
        "applyClearAgentsPersistenceResult_": "self.refresh_",
        "load_operator_local_state": "load_clear_agents_state",
    }
    for method_name, required_call in controller_contracts.items():
        method = next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )
        assert required_call in _call_names(method)

    preview_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "clear_agents_preview"
    )
    assert _call_names(preview_function).count("project_clear_agents_preview") == 1

    clear_method = next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == "clearAgents_"
    )
    clear_source = ast.get_source_segment(
        STATUS_BAR_LEGACY.read_text(encoding="utf-8"),
        clear_method,
    )
    assert clear_source is not None
    assert "on_close=self._clear_agents_popover_closed" in clear_source
    assert "popoverDidClose_" not in clear_source

    mailbox_rows = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "mailbox_projection_rows"
    )
    assert _call_names(mailbox_rows).count("completion_presentation_key") == 1

    unseen = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "unseen_completions"
    )
    unseen_source = ast.get_source_segment(
        STATUS_BAR_LEGACY.read_text(encoding="utf-8"),
        unseen,
    )
    assert unseen_source is not None
    assert (
        "acknowledged_keys=clear_agents_state_for_target(target).acknowledged_keys"
        in unseen_source
    )


def test_legacy_effect_popup_catalogs_are_explicit_delegations() -> None:
    tree = _tree(STATUS_BAR_LEGACY)
    local_functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    catalog_helpers = {
        "make_blend_mode_popup",
        "make_color_preset_popup",
        "make_preview_scenario_popup",
        "select_blend_mode",
        "select_color_preset",
        "select_preview_scenario",
    }

    assert catalog_helpers.isdisjoint(local_functions)
    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "settings_window_controls"
        for alias in node.names
    }
    assert catalog_helpers <= imported


def test_retained_status_controller_has_no_single_physical_device_path() -> None:
    tree = _tree(STATUS_BAR_LEGACY)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "StatusBarController"
    )
    class_methods = {
        node.name
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    module_functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    module_assignments = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else (node.target,)
        )
        if isinstance(target, ast.Name)
    }
    scalar_controller_attributes = {
        node.attr
        for node in ast.walk(class_node)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr
        in {
            "led_controller",
            "battery_led_controller",
            "last_led_display_kind",
        }
    }

    assert {
        "ensure_device_selection",
        "current_led_target",
    }.isdisjoint(class_methods)
    assert {
        "preferred_status_bar_device",
        "status_bar_device_sort_key",
    }.isdisjoint(module_functions)
    assert "STATUS_BAR_DEVICE_PRIORITY" not in module_assignments
    assert not scalar_controller_attributes


def test_retained_signal_fact_adapter_covers_every_claim_key() -> None:
    selector_tree = _tree(SRC / "signal_selection.py")
    enum_node = next(
        node
        for node in selector_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SignalClaimKey"
    )
    declared_keys = {
        node.targets[0].id
        for node in enum_node.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }

    class_node = next(
        node
        for node in _tree(STATUS_BAR_LEGACY).body
        if isinstance(node, ast.ClassDef) and node.name == "StatusBarController"
    )
    method = next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "active_led_display_kind_for_device"
    )
    claim_facts = next(
        node.value
        for node in ast.walk(method)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "claim_facts"
        and isinstance(node.value, ast.Dict)
    )
    adapted_keys = {
        key.attr
        for key in claim_facts.keys
        if isinstance(key, ast.Attribute)
        and isinstance(key.value, ast.Name)
        and key.value.id == "SignalClaimKey"
    }

    assert adapted_keys == declared_keys
    assert len(adapted_keys) == len(claim_facts.keys)


def test_retained_signal_selection_claim_adapter_covers_the_exact_pure_claim_set() -> None:
    class_node = next(
        node
        for node in _tree(STATUS_BAR_LEGACY).body
        if isinstance(node, ast.ClassDef) and node.name == "StatusBarController"
    )
    method = next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "active_led_display_kind_for_device"
    )
    claim_assignment = next(
        node
        for node in method.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "claim_facts"
            for target in node.targets
        )
    )

    assert isinstance(claim_assignment.value, ast.Dict)
    actual_keys = tuple(
        key.attr
        for key in claim_assignment.value.keys
        if isinstance(key, ast.Attribute)
        and isinstance(key.value, ast.Name)
        and key.value.id == "SignalClaimKey"
    )
    expected_keys = tuple(spec.key.name for spec in SIGNAL_CLAIM_PRECEDENCE)

    assert actual_keys
    assert set(actual_keys) == set(expected_keys)
