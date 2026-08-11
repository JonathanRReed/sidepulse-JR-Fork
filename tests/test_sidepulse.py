from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sidepulse.audit import (
    append_status_audit_record,
    export_status_audit_csv,
    export_status_audit_html,
    read_status_audit_records,
)
from sidepulse.battery import (
    BATTERY_CHARGING_MINT,
    BatteryLedController,
    BatterySnapshot,
    parse_ioreg_battery_plist,
    program_for_battery,
)
from sidepulse import collector as collector_module
from sidepulse import cli as cli_module
from sidepulse import colors as colors_module
from sidepulse.colors import (
    BLEND_MODE_CHOICES,
    BLEND_MODE_CLASSIC,
    BLEND_MODE_COLOR,
    BLEND_MODE_CYCLE,
    BLEND_MODE_RELAY,
    BLEND_MODE_ROUND_ROBIN,
    BLEND_MODE_SPATIAL,
    CURATED_PALETTE,
    DEFAULT_CYCLE_SPEED_SECONDS,
    MAX_CYCLE_SPEED_SECONDS,
    MIN_CYCLE_SPEED_SECONDS,
    AgentLayoutStabilizer,
    ColorSettings,
    default_agent_color,
    normalize_hex,
    program_for_snapshot,
    urgency_weight,
)
from sidepulse.collector import (
    AgentMonitor,
    LiveAgentMonitor,
    MonitorSnapshot,
    SourceSpec,
    default_sources,
)
from sidepulse.cli import build_parser, visible_watch_statuses
from sidepulse.device_writer import (
    DeviceWriteError,
    discover_devices,
    normalize_led_text,
    validate_led_text,
    write_led_program,
)
from sidepulse.hook import format_hook_payload, routed_hook_payload, write_hook_payload
from sidepulse.ipc import HookEventServer, send_hook_event
from sidepulse.install import (
    hook_command,
    install_claude_hooks,
    install_codex_hooks,
    install_devin_hooks,
    install_grok_hooks,
    uninstall_claude_hooks,
    uninstall_codex_hooks,
    uninstall_devin_hooks,
    uninstall_grok_hooks,
    update_codex_trusted_hashes,
)
from sidepulse.keep_awake import KeepAwakeController, status_file_for_target
from sidepulse.led_status import (
    ANIMATION_STYLE_BLINK,
    ANIMATION_STYLE_CHOICES,
    ANIMATION_STYLE_PULSE,
    ANIMATION_STYLE_ROLL,
    ANIMATION_STYLE_SOLID,
    AgentLedController,
    LedDisplayState,
    display_state_for_mode,
    led_count_for_target,
    program_for_display_state,
    write_mode_to_leds,
)
from sidepulse.lid_sleep import (
    ClosedLidAwakeController,
    SleepHelperRequiredError,
    closed_lid_awake_should_hold,
    parse_bool_ioreg_property,
    run_sudo_pmset_disablesleep,
    sleep_helper_sudoers_rule,
)
from sidepulse.models import AgentMode, AgentStatus, AggregateStatus
from sidepulse.origin import ProcessInfo, origin_from_processes
from sidepulse.providers import (
    DEVIN_EVENTS,
    HOOK_PROVIDERS,
    PROVIDER_REGISTRY,
    PROVIDER_SPECS,
    detect_devin_config,
    detect_log_path,
    detect_grok_config,
    default_log_path,
    default_state_dir,
    parse_log_line,
    provider_spec,
)
from sidepulse.sd_eject_guard_launch import (
    SD_EJECT_GUARD_BINARY_NAME,
    SD_EJECT_GUARD_DISPLAY_NAME,
    SD_EJECT_GUARD_LABEL,
    SdEjectGuardInstallError,
    SdEjectGuardPaths,
    build_sd_eject_guard_plist,
    install_sd_eject_guard,
    sd_eject_guard_installed,
    stop_sd_eject_guard,
    uninstall_sd_eject_guard,
)
from sidepulse.session_actions import (
    SESSION_OPEN_APP,
    SESSION_OPEN_TERMINAL,
    SESSION_OPEN_VSCODE,
    default_session_open_action,
    provider_session_opener_providers,
    session_deep_link,
    session_open_target,
    session_resume_command,
    session_vscode_link,
)
from sidepulse.settings import (
    ALCOVE_COMPAT_ALWAYS,
    ALCOVE_COMPAT_AUTO,
    CLOSED_LID_AWAKE_AGENTS,
    CLOSED_LID_AWAKE_ALWAYS,
    CLOSED_LID_AWAKE_NEVER,
    LID_ANIMATION_CLOSED,
    LID_ANIMATION_OPEN,
    LED_DISPLAY_BATTERY,
    AgentMonitorSettings,
    DeviceDisplaySetting,
    default_config_dir,
    default_lid_animation,
    default_settings_path,
    load_settings,
    save_settings,
)
from sidepulse.status_bar_launch import (
    LAUNCH_AGENT_LABEL,
    build_launch_agent_plist,
    install_launch_agent,
    launch_agent_installed,
)


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def poll(self):
        return 0 if self.terminated or self.killed else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self) -> None:
        self.killed = True


class AgentMonitorTests(unittest.TestCase):
    def test_provider_registry_includes_devin_as_first_class_provider(self) -> None:
        self.assertEqual(HOOK_PROVIDERS, ("codex", "claude", "devin", "grok"))
        self.assertEqual(provider_spec("devin").label, "Devin")
        self.assertEqual(provider_spec("devin").config_kind, "devin-json")
        self.assertEqual(provider_spec("devin").events, DEVIN_EVENTS)
        self.assertIs(PROVIDER_REGISTRY["devin"], provider_spec("devin"))

    def test_detect_devin_config_reads_global_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".config" / "devin" / "config.json"
            log = home / "state" / "devin.jsonl"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": f"python hook_entry.py --provider devin --log {log}",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                )
            )

            detected = detect_devin_config(home)

            self.assertEqual(detected.provider, "devin")
            self.assertTrue(detected.hooks_enabled)
            self.assertIn("PreToolUse", detected.hook_events)
            self.assertIn(log, detected.log_paths)

    def test_detect_devin_config_ignores_unrelated_log_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".config" / "devin" / "config.json"
            unrelated_log = Path("/private/tmp/agent-deck-debug.jsonl")
            sidepulse_log = home / ".local" / "state" / "sidepulse" / "agent-monitor" / "devin.jsonl"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": f"bun agent-deck-hook --log {unrelated_log};",
                                        },
                                        {
                                            "type": "command",
                                            "command": (
                                                "python hook_entry.py --provider devin "
                                                f"--log {sidepulse_log};"
                                            ),
                                        },
                                    ]
                                }
                            ]
                        }
                    }
                )
            )

            detected = detect_devin_config(home)

            self.assertEqual(detected.log_paths, (sidepulse_log,))
            self.assertEqual(detect_log_path("devin", home), sidepulse_log)

    def test_detect_devin_config_ignores_unrelated_only_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".config" / "devin" / "config.json"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "bun agent-deck-hook --log /private/tmp/debug.jsonl",
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                )
            )

            detected = detect_devin_config(home)

            self.assertFalse(detected.hooks_enabled)
            self.assertEqual(detected.hook_events, ())
            self.assertEqual(detected.log_paths, ())

    def test_detect_devin_config_reads_packaged_hook_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".config" / "devin" / "config.json"
            log = home / "state" / "devin.jsonl"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": (
                                                "agent-monitor hook-log --provider devin "
                                                f"--log {log}"
                                            ),
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                )
            )

            detected = detect_devin_config(home)

            self.assertTrue(detected.hooks_enabled)
            self.assertEqual(detected.hook_events, ("Stop",))
            self.assertEqual(detected.log_paths, (log,))

    def test_detect_devin_config_normalizes_post_compaction_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".config" / "devin" / "config.json"
            log = home / "state" / "devin-post-compaction.jsonl"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PostCompaction": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": f"python hook_entry.py --provider devin --log {log}",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                )
            )

            detected = detect_devin_config(home)

            self.assertIn("PostCompact", detected.hook_events)
            self.assertIn(log, detected.log_paths)

    def test_devin_post_compaction_and_prompt_id_are_normalized(self) -> None:
        record = parse_log_line(
            "devin",
            json.dumps(
                {
                    "hook_event_name": "PostCompaction",
                    "session_id": "devin-session",
                    "prompt_id": "devin-turn",
                    "summary": "compacted",
                }
            ),
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.provider, "devin")
        self.assertEqual(record.event_name, "PostCompact")
        self.assertEqual(record.turn_id, "devin-turn")

    def test_aggregates_highest_priority_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            codex = base / "codex.jsonl"
            claude = base / "claude.jsonl"

            codex.write_text(
                json.dumps(
                    {
                        "logged_at": "2026-06-20T06:00:00Z",
                        "event": {
                            "hook_event_name": "PreToolUse",
                            "session_id": "codex-session",
                            "tool_name": "Bash",
                        },
                    }
                )
                + "\n"
            )
            claude.write_text(
                json.dumps(
                    {
                        "logged_at": "2026-06-20T06:00:01Z",
                        "hook_event_name": "Notification",
                        "session_id": "claude-session",
                        "notification_type": "idle_prompt",
                        "message": "Claude is waiting for your input",
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", codex), SourceSpec("claude", claude)),
                stale_after_seconds=999999999,
                tool_running_timeout_seconds=0,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.WAITING_FOR_INPUT)
            self.assertEqual(len(snapshot.statuses), 2)

    def test_hook_log_writes_provider_formats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            codex = base / "codex.jsonl"
            claude = base / "claude.jsonl"
            grok = base / "grok.jsonl"

            write_hook_payload(
                "codex",
                codex,
                '{"hook_event_name":"Stop","session_id":"abc"}',
            )
            write_hook_payload(
                "claude",
                claude,
                '{"hook_event_name":"Stop","session_id":"xyz"}',
            )
            write_hook_payload(
                "grok",
                grok,
                '{"hookEventName":"stop","sessionId":"grok-session"}',
            )

            codex_obj = json.loads(codex.read_text())
            claude_obj = json.loads(claude.read_text())
            grok_obj = json.loads(grok.read_text())

            self.assertIn("event", codex_obj)
            self.assertEqual(codex_obj["event"]["session_id"], "abc")
            self.assertNotIn("event", claude_obj)
            self.assertEqual(claude_obj["session_id"], "xyz")
            self.assertNotIn("event", grok_obj)
            self.assertEqual(grok_obj["sessionId"], "grok-session")
            self.assertTrue(
                datetime.fromisoformat(codex_obj["logged_at"].replace("Z", "+00:00")).tzinfo
                is not None
            )

    def test_hook_payload_stamps_origin_from_vscode_environment(self) -> None:
        with patch.dict(os.environ, {"TERM_PROGRAM": "vscode"}, clear=True):
            line = format_hook_payload(
                "claude",
                '{"hook_event_name":"UserPromptSubmit","session_id":"claude-session","prompt":"hi"}',
            )

        self.assertEqual(line["agent_origin"], "Claude in VS Code")
        record = parse_log_line("claude", json.dumps(line))
        self.assertIsNotNone(record)
        self.assertEqual(record.origin, "Claude in VS Code")
        status = collector_module.status_from_event(record)
        self.assertIsNotNone(status)
        self.assertEqual(status.origin, "Claude in VS Code")

    def test_codex_hook_payload_stamps_origin_from_app_process(self) -> None:
        processes = (
            ProcessInfo(
                pid=100,
                ppid=1,
                comm="/Applications/ChatGPT.app/Contents/MacOS/ChatGPT",
                command="/Applications/ChatGPT.app/Contents/MacOS/ChatGPT",
            ),
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("sidepulse.origin.process_ancestry", return_value=processes),
        ):
            line = format_hook_payload(
                "codex",
                '{"hook_event_name":"UserPromptSubmit","session_id":"codex-session","prompt":"hi"}',
            )

        self.assertEqual(line["event"]["agent_origin"], "Codex UI")
        record = parse_log_line("codex", json.dumps(line))
        self.assertIsNotNone(record)
        self.assertEqual(record.origin, "Codex UI")

    def test_origin_process_detection_distinguishes_claude_surfaces(self) -> None:
        self.assertEqual(
            origin_from_processes(
                "claude",
                (
                    ProcessInfo(
                        pid=100,
                        ppid=1,
                        comm="/Applications/Visual Studio Code.app/Contents/MacOS/Electron",
                        command="/Applications/Visual Studio Code.app/Contents/MacOS/Electron",
                    ),
                ),
            ).label,
            "Claude in VS Code",
        )
        self.assertEqual(
            origin_from_processes(
                "claude",
                (ProcessInfo(pid=100, ppid=1, comm="/opt/homebrew/bin/claude", command="claude"),),
            ).label,
            "Claude Code CLI",
        )

    def test_origin_process_detection_identifies_devin_cli(self) -> None:
        origin = origin_from_processes(
            "devin",
            (ProcessInfo(pid=100, ppid=1, comm="/Users/me/.local/bin/devin", command="devin"),),
        )

        self.assertIsNotNone(origin)
        self.assertEqual(origin.label, "Devin CLI")

    def test_grok_log_line_normalizes_camel_case_payload(self) -> None:
        record = parse_log_line(
            "grok",
            json.dumps(
                {
                    "hookEventName": "pre_tool_use",
                    "sessionId": "grok-session",
                    "workspaceRoot": "/tmp/project",
                    "toolName": "run_terminal_command",
                    "toolInput": {"command": "date"},
                    "timestamp": "2026-07-18T12:00:00Z",
                }
            ),
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.event_name, "PreToolUse")
        self.assertEqual(record.session_id, "grok-session")
        self.assertEqual(record.cwd, "/tmp/project")
        self.assertEqual(record.tool_name, "run_terminal_command")
        self.assertEqual(record.raw["tool_input"], {"command": "date"})

    def test_claude_compat_grok_payload_is_inferred_as_grok(self) -> None:
        record = parse_log_line(
            "claude",
            json.dumps(
                {
                    "hookEventName": "notification",
                    "sessionId": "019f7724-da8c-7df0-b41d-bda99e0cac9f",
                    "workspaceRoot": "/Users/pero/git/ai_food/",
                    "transcriptPath": (
                        "/Users/pero/.grok/sessions/%2FUsers%2Fpero%2Fgit%2Fai_food/"
                        "019f7724-da8c-7df0-b41d-bda99e0cac9f/updates.jsonl"
                    ),
                    "notificationType": "idle_prompt",
                    "message": "Turn complete",
                    "timestamp": "2026-07-18T21:55:14Z",
                }
            ),
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.provider, "grok")
        self.assertEqual(record.event_name, "Notification")
        status = collector_module.status_from_event(
            record,
            collector_module.StatusMetadata(cwd=record.cwd, title="ai_food"),
        )
        self.assertIsNotNone(status)
        self.assertEqual(status.mode, AgentMode.COMPLETED)
        self.assertEqual(status.display_name, "ai_food (019f7724)")

    def test_claude_waiting_notification_still_requires_input(self) -> None:
        record = parse_log_line(
            "claude",
            json.dumps(
                {
                    "hook_event_name": "Notification",
                    "session_id": "claude-session",
                    "notification_type": "idle_prompt",
                    "message": "Claude is waiting for your input",
                    "logged_at": "2026-07-18T21:55:14Z",
                }
            ),
        )

        self.assertIsNotNone(record)
        status = collector_module.status_from_event(record)
        self.assertIsNotNone(status)
        self.assertEqual(status.mode, AgentMode.WAITING_FOR_INPUT)

    def test_claude_compat_grok_payload_routes_to_grok_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            claude = base / "claude.jsonl"
            grok = base / "grok.jsonl"
            payload = json.dumps(
                {
                    "hookEventName": "notification",
                    "sessionId": "grok-session",
                    "workspaceRoot": "/tmp/project",
                    "transcriptPath": "/Users/pero/.grok/sessions/project/grok-session/updates.jsonl",
                    "notificationType": "idle_prompt",
                    "message": "Turn complete",
                }
            )

            with (
                patch("sidepulse.hook.detect_log_path", return_value=grok),
                patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal"}, clear=True),
            ):
                provider, path, line = routed_hook_payload("claude", claude, payload)

            self.assertEqual(provider, "grok")
            self.assertEqual(path, grok)
            self.assertEqual(line["sessionId"], "grok-session")
            self.assertEqual(line["agent_origin"], "Grok CLI")

    def test_status_audit_log_exports_csv_and_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            log = base / "event-status.jsonl"
            record = parse_log_line(
                "grok",
                json.dumps(
                    {
                        "hookEventName": "notification",
                        "sessionId": "grok-session",
                        "workspaceRoot": "/tmp/project",
                        "notificationType": "idle_prompt",
                        "message": "Turn complete",
                        "timestamp": "2026-07-18T21:55:14Z",
                    }
                ),
            )
            self.assertIsNotNone(record)
            status = collector_module.status_from_event(record)
            self.assertIsNotNone(status)

            append_status_audit_record(record, status, path=log)
            records = read_status_audit_records(log)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["hook_event"], "Notification")
            self.assertEqual(records[0]["status"], "completed")
            csv_path = base / "debug.csv"
            html_path = base / "debug.html"
            self.assertEqual(export_status_audit_csv(csv_path, source=log), 1)
            self.assertEqual(export_status_audit_html(html_path, source=log), 1)
            self.assertIn("hook_event,status", csv_path.read_text())
            self.assertIn("SidePulse Agent Debug Log", html_path.read_text())

    def test_hook_event_server_receives_socket_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            received: list[tuple[str, dict]] = []
            server = HookEventServer(
                lambda provider, line: received.append((provider, line)),
                socket_path=Path(tmp) / "events.sock",
            )
            try:
                server.start()
                sent = send_hook_event(
                    "codex",
                    {
                        "logged_at": "2026-06-20T06:00:00Z",
                        "event": {
                            "hook_event_name": "Stop",
                            "session_id": "codex-session",
                            "last_assistant_message": "Done.",
                        },
                    },
                    socket_path=server.socket_path,
                    timeout=0.5,
                )

                deadline = time.time() + 1
                while sent and not received and time.time() < deadline:
                    time.sleep(0.01)

                self.assertTrue(sent)
                self.assertTrue(received)
                self.assertEqual(received[0][0], "codex")
                self.assertEqual(
                    received[0][1]["event"]["hook_event_name"],
                    "Stop",
                )
            finally:
                server.stop()

    def test_live_sidepulse_ingests_events_and_persists_latest_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            latest = base / "latest.json"
            source = SourceSpec("event-bus", base / "events.sock")
            monitor = LiveAgentMonitor(
                sources=(source,),
                stale_after_seconds=3600,
                latest_state_path=latest,
            )
            line = {
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "event": {
                    "hook_event_name": "PreToolUse",
                    "session_id": "codex-session",
                    "cwd": "/tmp/project",
                    "tool_name": "Bash",
                    "agent_origin": "Codex UI",
                },
            }
            record = parse_log_line("codex", json.dumps(line))

            self.assertIsNotNone(record)
            monitor.ingest_record(record)
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.TOOL_RUNNING)
            self.assertEqual(snapshot.statuses[0].tool_name, "Bash")
            self.assertEqual(snapshot.statuses[0].origin, "Codex UI")
            self.assertTrue(latest.exists())

            reloaded = LiveAgentMonitor(
                sources=(source,),
                stale_after_seconds=3600,
                latest_state_path=latest,
            )
            self.assertEqual(reloaded.snapshot().aggregate.mode, AgentMode.TOOL_RUNNING)
            self.assertEqual(reloaded.snapshot().statuses[0].origin, "Codex UI")

    def test_status_bar_startup_replay_ingests_recent_debug_logs(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            log = base / "codex.jsonl"
            session_id = "eeeeeeee-ffff-7aaa-8bbb-cccccccccccc"
            log.write_text(
                json.dumps(
                    {
                        "logged_at": datetime.now(timezone.utc).isoformat(),
                        "event": {
                            "hook_event_name": "UserPromptSubmit",
                            "session_id": session_id,
                            "cwd": "/tmp/project",
                            "prompt": "startup replay should restore this",
                        },
                    }
                )
                + "\n"
            )
            monitor = LiveAgentMonitor()

            with patch(
                "sidepulse.status_bar.detect_log_path",
                return_value=log,
            ):
                replayed = status_bar.replay_recent_debug_logs(
                    monitor,
                    providers=("codex",),
                    max_lines=20,
                )

            snapshot = monitor.snapshot()
            self.assertEqual(replayed, 1)
            self.assertEqual(snapshot.aggregate.mode, AgentMode.WORKING)
            self.assertIn("startup replay", snapshot.statuses[0].display_name)

    def test_status_bar_session_menu_title_is_task_and_project(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        now = datetime.now(timezone.utc)
        status = AgentStatus(
            provider="codex",
            agent_id="codex:session:019ee395",
            display_name="sidepulse: Refine README agent status modes (019ee395)",
            mode=AgentMode.COMPLETED,
            updated_at=now,
            event_name="Stop",
            session_id="019ee395",
            cwd="/Users/pero/pgit/sidepulse",
        )

        self.assertEqual(
            status_bar.menu_title_for_status(status, now),
            "Done  Refine README agent status modes\nsidepulse",
        )
        self.assertEqual(
            status_bar.session_detail_for_status(status, now).split(" · ")[0],
            "Done",
        )

    def test_status_bar_session_menu_title_suppresses_duplicate_project(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        now = datetime.now(timezone.utc)
        status = AgentStatus(
            provider="grok",
            agent_id="grok:session:019f7724",
            display_name="ai_food (019f7724)",
            mode=AgentMode.WAITING_FOR_INPUT,
            updated_at=now,
            event_name="Notification",
            session_id="019f7724",
            cwd="/Users/pero/git/ai_food",
        )

        self.assertEqual(status_bar.menu_title_for_status(status, now), "Ask  ai_food")

    def test_status_bar_grok_provider_uses_badge_icon(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        status = AgentStatus(
            provider="grok",
            agent_id="grok:session:abc",
            display_name="Grok abc",
            mode=AgentMode.WORKING,
            updated_at=datetime.now(timezone.utc),
            event_name="PreToolUse",
        )

        image = status_bar.provider_icon_for_status(status)

        self.assertIsNotNone(image)
        self.assertEqual(image.size().width, 18)
        self.assertEqual(image.size().height, 18)

    def test_status_bar_vscode_origin_uses_composite_app_icon(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        status = AgentStatus(
            provider="claude",
            agent_id="claude:session:abc",
            display_name="Claude abc",
            mode=AgentMode.WORKING,
            updated_at=datetime.now(timezone.utc),
            event_name="PreToolUse",
            origin="Claude in VS Code",
        )

        image = status_bar.session_origin_icon_for_status(status)

        self.assertIsNotNone(image)
        self.assertEqual(image.size().width, 24)
        self.assertEqual(image.size().height, 18)

    def test_status_bar_session_row_icon_combines_status_and_origin(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        status = AgentStatus(
            provider="claude",
            agent_id="claude:session:abc",
            display_name="Claude abc",
            mode=AgentMode.COMPLETED,
            updated_at=datetime.now(timezone.utc),
            event_name="Stop",
            origin="Claude in VS Code",
        )

        image = status_bar.session_row_icon_for_status(status)

        self.assertIsNotNone(image)
        self.assertGreater(image.size().width, 38)
        self.assertEqual(image.size().height, 18)

    def test_virtual_screen_bar_frame_covers_notch_plus_led_band(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        screen = SimpleNamespace(
            frame=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=1512.0, height=982.0),
            ),
            safeAreaInsets=lambda: SimpleNamespace(top=32.0),
            auxiliaryTopLeftArea=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=640.0, height=24.0),
            ),
            auxiliaryTopRightArea=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=872.0, y=0.0),
                size=SimpleNamespace(width=640.0, height=24.0),
            ),
        )

        self.assertEqual(
            virtual_device.virtual_window_frame_for_screen(screen),
            ((640.0, 945.0), (232.0, 37.0)),
        )
        self.assertEqual(
            virtual_device.led_band_rect(232.0),
            ((0.0, 0.0), (232.0, 5.0)),
        )

    def test_virtual_screen_bar_on_notchless_display_is_led_band_only(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        screen = SimpleNamespace(
            frame=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=1920.0, height=1080.0),
            ),
            safeAreaInsets=lambda: SimpleNamespace(top=0.0),
            auxiliaryTopLeftArea=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=0.0, height=0.0),
            ),
            auxiliaryTopRightArea=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=0.0, height=0.0),
            ),
        )

        self.assertFalse(virtual_device.screen_has_notch(screen))
        self.assertEqual(
            virtual_device.virtual_window_frame_for_screen(screen),
            ((850.0, 1075.0), (220.0, 5.0)),
        )

    def test_virtual_screen_bar_redraws_at_60fps(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        self.assertEqual(virtual_device.FRAME_RATE, 60.0)
        self.assertAlmostEqual(virtual_device.FRAME_INTERVAL, 1.0 / 60.0)

    def test_compact_mode_keeps_the_same_frame_as_normal_mode(self) -> None:
        # Alcove compatibility changes drawing style, not position -- an
        # earlier attempt moved the window to an offset frame and it read
        # as a disconnected floating widget rather than an integrated
        # accent. Position/size must be identical either way; only
        # VirtualLedView.compact_mode (tested below) changes.
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        screen = SimpleNamespace(
            frame=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=1512.0, height=982.0),
            ),
            safeAreaInsets=lambda: SimpleNamespace(top=32.0),
            auxiliaryTopLeftArea=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=640.0, height=24.0),
            ),
            auxiliaryTopRightArea=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=872.0, y=0.0),
                size=SimpleNamespace(width=640.0, height=24.0),
            ),
        )

        self.assertFalse(hasattr(virtual_device, "compact_window_frame_for_screen"))
        frame = virtual_device.virtual_window_frame_for_screen(screen)
        self.assertEqual(frame, virtual_device.virtual_window_frame_for_screen(screen))

    def test_compact_mode_toggle_updates_the_view_and_redraws(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        view = virtual_device.VirtualLedView.alloc().initWithFrame_(((0, 0), (220.0, 37.0)))
        self.assertFalse(view.compact_mode)
        view.setCompactMode_(True)
        self.assertTrue(view.compact_mode)
        # Must not raise: compact drawing path renders without the normal
        # black-backdrop body shape.
        view.setState_brightness_(virtual_device.LedDisplayState.WORKING, 255)
        view._draw_compact_accent()
        view.setCompactMode_(False)
        self.assertFalse(view.compact_mode)

    def _screen_with_notch(self, *, left_width=640.0, right_width=640.0):
        return SimpleNamespace(
            frame=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=1512.0, height=982.0),
            ),
            safeAreaInsets=lambda: SimpleNamespace(top=32.0),
            auxiliaryTopLeftArea=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=left_width, height=24.0),
            ),
            auxiliaryTopRightArea=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=1512.0 - right_width, y=0.0),
                size=SimpleNamespace(width=right_width, height=24.0),
            ),
        )

    def test_wrap_menu_bar_off_matches_todays_exact_frame(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        screen = self._screen_with_notch()
        # Omitting wrap_menu_bar (the default) must be byte-for-byte
        # identical to explicitly passing False -- no behavior change for
        # anyone who never opts in.
        self.assertEqual(
            virtual_device.virtual_window_frame_for_screen(screen),
            virtual_device.virtual_window_frame_for_screen(screen, wrap_menu_bar=False),
        )

    def test_wrap_menu_bar_widens_the_frame_but_stays_centered(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        screen = self._screen_with_notch()
        plain = virtual_device.virtual_window_frame_for_screen(screen)
        wrapped = virtual_device.virtual_window_frame_for_screen(screen, wrap_menu_bar=True)
        (plain_x, plain_y), (plain_w, plain_h) = plain
        (wrapped_x, wrapped_y), (wrapped_w, wrapped_h) = wrapped
        self.assertGreater(wrapped_w, plain_w)
        self.assertEqual(wrapped_h, plain_h)
        self.assertEqual(wrapped_y, plain_y)
        # Centered on the screen either way -- the extra width is symmetric.
        screen_center = screen.frame().origin.x + screen.frame().size.width / 2.0
        self.assertAlmostEqual(wrapped_x + wrapped_w / 2.0, screen_center)
        self.assertAlmostEqual(plain_x + plain_w / 2.0, screen_center)

    def test_wing_width_is_bounded_by_the_narrower_side(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        # A cluttered right side (lots of menu-bar app icons eating into the
        # reported safe area) must constrain the wing on *both* sides --
        # a lopsided wing (wide on the left, clipped on the right) would
        # look broken, not native.
        narrow_screen = self._screen_with_notch(left_width=640.0, right_width=50.0)
        roomy_screen = self._screen_with_notch(left_width=640.0, right_width=640.0)
        narrow_width = virtual_device.wing_width_for_screen(
            narrow_screen, virtual_device.slot_width_for_screen(narrow_screen)
        )
        roomy_width = virtual_device.wing_width_for_screen(
            roomy_screen, virtual_device.slot_width_for_screen(roomy_screen)
        )
        self.assertEqual(narrow_width, 0.0, "not enough safe room on the right for any wing")
        self.assertGreater(roomy_width, 0.0)
        self.assertLessEqual(roomy_width, virtual_device.WING_MAX_WIDTH)

    def test_wing_width_is_zero_on_a_notchless_display(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        screen = SimpleNamespace(
            frame=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=1920.0, height=1080.0),
            ),
            safeAreaInsets=lambda: SimpleNamespace(top=0.0),
            auxiliaryTopLeftArea=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0), size=SimpleNamespace(width=0.0, height=0.0)
            ),
            auxiliaryTopRightArea=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0), size=SimpleNamespace(width=0.0, height=0.0)
            ),
        )
        self.assertEqual(virtual_device.wing_width_for_screen(screen, 220.0), 0.0)
        # And therefore wrap_menu_bar=True is a no-op on this display.
        self.assertEqual(
            virtual_device.virtual_window_frame_for_screen(screen),
            virtual_device.virtual_window_frame_for_screen(screen, wrap_menu_bar=True),
        )

    def test_notch_width_none_uses_full_view_width_unchanged(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        view = virtual_device.VirtualLedView.alloc().initWithFrame_(((0, 0), (220.0, 37.0)))
        self.assertIsNone(view.notch_width)
        notch_width, wing_offset = view._notch_geometry()
        self.assertEqual(notch_width, 220.0)
        self.assertEqual(wing_offset, 0.0)

    def test_set_notch_width_insets_the_body_and_computes_wing_offset(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        view = virtual_device.VirtualLedView.alloc().initWithFrame_(((0, 0), (400.0, 37.0)))
        view.setNotchWidth_(220.0)
        notch_width, wing_offset = view._notch_geometry()
        self.assertEqual(notch_width, 220.0)
        self.assertEqual(wing_offset, 90.0)  # (400 - 220) / 2

    def test_notch_width_never_exceeds_the_views_own_bounds(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        # A stale notch_width from before a screen change (narrower new
        # screen) must not produce a negative wing_offset.
        view = virtual_device.VirtualLedView.alloc().initWithFrame_(((0, 0), (200.0, 37.0)))
        view.setNotchWidth_(320.0)
        notch_width, wing_offset = view._notch_geometry()
        self.assertEqual(notch_width, 200.0)
        self.assertEqual(wing_offset, 0.0)

    def test_wrap_mode_drawing_does_not_raise(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        view = virtual_device.VirtualLedView.alloc().initWithFrame_(((0, 0), (400.0, 37.0)))
        view.setNotchWidth_(220.0)
        view.setState_brightness_(virtual_device.LedDisplayState.WORKING, 255)
        # Must not raise with a real, wider-than-notch bounds and wing
        # geometry in play -- exercises both the notch-clipped pass and the
        # two wing passes in drawRect_.
        view.drawRect_(((0, 0), (400.0, 37.0)))
        view.setCompactMode_(True)
        view._draw_compact_accent()

    def test_settings_persist_wraps_menu_bar_flag(self) -> None:
        settings = AgentMonitorSettings().with_virtual_status_device_wraps_menu_bar(True)
        self.assertTrue(settings.virtual_status_device_wraps_menu_bar)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(settings, path)
            reloaded = load_settings(path)
        self.assertTrue(reloaded.virtual_status_device_wraps_menu_bar)

    def test_wraps_menu_bar_defaults_false_when_absent_from_saved_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(AgentMonitorSettings(), path)
            data = json.loads(path.read_text())
            data.pop("virtual_status_device_wraps_menu_bar", None)
            path.write_text(json.dumps(data))
            reloaded = load_settings(path)
        self.assertFalse(reloaded.virtual_status_device_wraps_menu_bar)

    def test_virtual_status_device_set_wraps_menu_bar(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        device = virtual_device.VirtualStatusDevice.alloc().init()
        self.assertFalse(device.wraps_menu_bar)
        device.set_wraps_menu_bar(True)
        self.assertTrue(device.wraps_menu_bar)

    def test_should_use_compact_layout_respects_explicit_override(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        with patch.object(virtual_device, "is_alcove_running", return_value=False):
            self.assertTrue(virtual_device.should_use_compact_layout("always"))
        with patch.object(virtual_device, "is_alcove_running", return_value=True):
            self.assertFalse(virtual_device.should_use_compact_layout("never"))

    def test_should_use_compact_layout_auto_follows_detection(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        with patch.object(virtual_device, "is_alcove_running", return_value=True):
            self.assertTrue(virtual_device.should_use_compact_layout("auto"))
        with patch.object(virtual_device, "is_alcove_running", return_value=False):
            self.assertFalse(virtual_device.should_use_compact_layout("auto"))

    def test_is_alcove_running_fails_safe_on_workspace_error(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        class ExplodingWorkspace:
            @staticmethod
            def sharedWorkspace():
                raise RuntimeError("boom")

        # Patch the name as imported into virtual_device's own namespace, not
        # the real AppKit class object -- NSWorkspace is a bridged
        # Objective-C class shared with the rest of the process (e.g.
        # status_bar.py's own icon lookups), and mutating it directly would
        # leak into unrelated tests.
        with patch.object(virtual_device, "NSWorkspace", ExplodingWorkspace):
            self.assertFalse(virtual_device.is_alcove_running())

    def test_led_wasm_controller_uses_packaged_firmware_engine(self) -> None:
        try:
            from sidepulse.led_wasm import LedWasmUnavailableError, SdLedWasmController
        except ImportError as exc:
            self.skipTest(str(exc))

        try:
            controller = SdLedWasmController(led_count=8)
        except LedWasmUnavailableError as exc:
            self.skipTest(str(exc))

        result = controller.parse("brightness 128\n#00FF66", 0)

        self.assertTrue(result.ok)
        self.assertEqual(controller.step(0), [(0, 128, 51)] * 8)

    def test_led_wasm_controller_supports_sidepulse_dot_led_count(self) -> None:
        try:
            from sidepulse.led_wasm import LedWasmUnavailableError, SdLedWasmController
        except ImportError as exc:
            self.skipTest(str(exc))

        try:
            controller = SdLedWasmController(led_count=2)
        except LedWasmUnavailableError as exc:
            self.skipTest(str(exc))

        result = controller.parse("0:#FF0000; 1:#00FF00; 7:#FFFFFF", 0)

        self.assertTrue(result.ok)
        self.assertEqual(controller.step(0), [(255, 0, 0), (0, 255, 0)])

    def test_virtual_screen_bar_led_blend_spans_three_leds(self) -> None:
        try:
            from sidepulse import virtual_device
        except (ImportError, SystemExit) as exc:
            self.skipTest(str(exc))

        led_width = 10.0
        target_center = 35.0
        colors = [(0.0, 0.0, 0.0, 0.0)] * 8
        colors[3] = (0.0, 1.0, 0.0, 1.0)

        self.assertAlmostEqual(
            virtual_device.blended_led_color_at_x(colors, target_center, led_width)[1],
            1.0,
        )
        self.assertGreater(
            virtual_device.blended_led_color_at_x(
                colors, target_center - led_width, led_width
            )[1],
            0.0,
        )
        self.assertGreater(
            virtual_device.blended_led_color_at_x(
                colors, target_center + led_width, led_width
            )[1],
            0.0,
        )
        self.assertAlmostEqual(
            virtual_device.blended_led_color_at_x(
                colors, target_center - led_width * 1.5, led_width
            )[1],
            0.0,
        )
        self.assertAlmostEqual(
            virtual_device.blended_led_color_at_x(
                colors, target_center + led_width * 1.5, led_width
            )[1],
            0.0,
        )

    def test_status_bar_session_row_has_inline_options(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        now = datetime.now(timezone.utc)
        status = AgentStatus(
            provider="claude",
            agent_id="claude:session:abc",
            display_name="Claude abc",
            mode=AgentMode.WAITING_FOR_INPUT,
            updated_at=now,
            event_name="Notification",
            session_id="1ca4348e-2aec-4147-9e81-d7d56364d257",
            cwd="/Users/pero/pgit/sdstatus_bitbang",
        )
        target = SimpleNamespace(settings=AgentMonitorSettings())

        row = status_bar.build_session_menu_item(status, now, target)
        options = status_bar.build_session_options_menu(status, now, target)
        titles = [
            options.itemAtIndex_(index).title()
            for index in range(options.numberOfItems())
            if options.itemAtIndex_(index).title()
        ]

        self.assertEqual(row.title(), status_bar.native_session_menu_title(status))
        self.assertIsNotNone(row.image())
        self.assertIsNone(row.submenu())
        self.assertIsNone(row.view())
        self.assertEqual(row.representedObject(), status)
        self.assertTrue(any(title.startswith("Ask  Claude abc") for title in titles))
        self.assertIn("Open in VS Code", titles)
        self.assertIn("Resume in Terminal", titles)

    def test_status_bar_native_session_row_uses_task_title(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "peterkuhar.com"
            cwd = project / "functions"
            (project / ".git").mkdir(parents=True)
            cwd.mkdir()
            status = AgentStatus(
                provider="claude",
                agent_id="claude:session:b64a0d4b",
                display_name=(
                    "functions: allow me to chose timeframe "
                    "http://localhost:5001/pkuhar-com/us-central... (b64a0d4b)"
                ),
                mode=AgentMode.WORKING,
                updated_at=datetime.now(timezone.utc),
                event_name="PostToolUse",
                session_id="b64a0d4b-d828-4133-abb3-bdb4fafa7719",
                cwd=str(cwd),
                origin="Claude in VS Code",
            )

            title = status_bar.native_session_menu_title(status)

        self.assertIn("allow me to chose timeframe", title)
        self.assertIn("peterkuhar.com", title)
        self.assertNotIn("Working", title)
        self.assertNotIn("Claude in VS Code", title)
        self.assertNotEqual(title, "Working  Claude in VS Code  functions")

    def test_status_bar_session_row_shows_origin_when_known(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        now = datetime.now(timezone.utc)
        status = AgentStatus(
            provider="claude",
            agent_id="claude:session:abc",
            display_name="Claude abc",
            mode=AgentMode.WAITING_FOR_INPUT,
            updated_at=now,
            event_name="Notification",
            session_id="1ca4348e-2aec-4147-9e81-d7d56364d257",
            cwd="/Users/pero/pgit/sdstatus_bitbang",
            origin="Claude in VS Code",
        )

        self.assertTrue(
            status_bar.menu_title_for_status(status, now).startswith(
                "Ask  Claude in VS Code  Claude abc"
            )
        )
        self.assertIn("Claude in VS Code", status_bar.session_detail_for_status(status, now))
        self.assertEqual(status_bar.primary_session_open_action(status), SESSION_OPEN_VSCODE)

        target = SimpleNamespace(
            settings=AgentMonitorSettings().with_session_open_action(
                "claude",
                SESSION_OPEN_TERMINAL,
                "Claude in VS Code",
            )
        )
        options = status_bar.build_session_options_menu(status, now, target)
        by_title = {
            options.itemAtIndex_(index).title(): options.itemAtIndex_(index)
            for index in range(options.numberOfItems())
            if options.itemAtIndex_(index).title()
        }
        self.assertEqual(by_title["Resume in Terminal"].state(), 1)
        self.assertEqual(by_title["Open in VS Code"].state(), 0)

    def test_codex_session_options_are_codex_specific(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        now = datetime.now(timezone.utc)
        status = AgentStatus(
            provider="codex",
            agent_id="codex:session:abc",
            display_name="Codex abc",
            mode=AgentMode.WORKING,
            updated_at=now,
            event_name="PreToolUse",
            session_id="019ee395-2f64-7cc3-b566-afcc1d626160",
            cwd="/tmp/project with spaces",
        )
        target = SimpleNamespace(settings=AgentMonitorSettings())

        row = status_bar.build_session_menu_item(status, now, target)
        options = status_bar.build_session_options_menu(status, now, target)
        titles = [
            options.itemAtIndex_(index).title()
            for index in range(options.numberOfItems())
            if options.itemAtIndex_(index).title()
        ]

        self.assertEqual(row.title(), status_bar.native_session_menu_title(status))
        self.assertIsNotNone(row.image())
        self.assertIsNone(row.submenu())
        self.assertTrue(any(title.startswith("Working  Codex abc") for title in titles))
        self.assertIn("Open in Codex", titles)
        self.assertIn("Resume in Terminal", titles)
        self.assertNotIn("Open in VS Code", titles)
        self.assertNotIn("Open Claude App", titles)

    def test_status_bar_device_submenu_has_brightness_slider(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        device = status_bar.StatusBarDevice(
            device_id="/Volumes/SidePulseDot",
            name="SidePulse Dot",
            root=Path("/Volumes/SidePulseDot"),
            target=Path("/Volumes/SidePulseDot/LEDS.LED"),
            connected=True,
            display="agent",
            brightness=128,
        )

        item = status_bar.build_device_menu_item(device, SimpleNamespace())
        submenu = item.submenu()
        titles = [
            submenu.itemAtIndex_(index).title()
            for index in range(submenu.numberOfItems())
            if submenu.itemAtIndex_(index).title()
        ]
        custom_view_count = sum(
            1
            for index in range(submenu.numberOfItems())
            if submenu.itemAtIndex_(index).view() is not None
        )

        self.assertIn("Brightness 50%", titles)
        # Brightness slider + Red/Green/Blue calibration sliders.
        self.assertEqual(custom_view_count, 4)

    def test_status_bar_observe_connected_device_resets_on_new_mount(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            device = status_bar.StatusBarDevice(
                device_id="/Volumes/SidePulsePro",
                name="SidePulse Pro",
                root=root,
                target=root / "LEDS.LED",
                connected=True,
                display="agent",
                brightness=255,
            )
            reset_ids: list[str] = []
            devices: list[status_bar.StatusBarDevice] = []
            target = SimpleNamespace(
                last_connected_device_signature=None,
                status_bar_devices=lambda: devices,
                reset_led_controllers_for_device=lambda device_id: reset_ids.append(device_id),
            )

            self.assertFalse(status_bar.StatusBarController.observe_connected_devices(target))

            devices.append(device)
            self.assertTrue(status_bar.StatusBarController.observe_connected_devices(target))
            self.assertEqual(reset_ids, ["/Volumes/SidePulsePro"])

            self.assertFalse(status_bar.StatusBarController.observe_connected_devices(target))
            self.assertEqual(reset_ids, ["/Volumes/SidePulsePro"])

    def test_status_bar_poll_devices_refreshes_on_connection_change(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        calls: list[object] = []
        target = SimpleNamespace(
            observe_connected_devices=lambda: True,
            last_snapshot=object(),
            refresh_=lambda sender: calls.append(sender),
        )

        status_bar.StatusBarController.poll_devices_once(target)

        self.assertEqual(calls, [None])

    def test_status_bar_menu_has_closed_lid_awake_policy_choices(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        snapshot = SimpleNamespace(
            statuses=[],
            stale_statuses=[],
            collected_at=datetime.now(timezone.utc),
        )
        target = SimpleNamespace(
            settings=AgentMonitorSettings(
                closed_lid_awake_policy=CLOSED_LID_AWAKE_AGENTS,
            ),
            closed_lid_awake=SimpleNamespace(last_error=None),
            status_bar_devices=lambda: [],
        )

        menu = status_bar.build_menu(snapshot, status_bar.STATE_IDLE, target)
        items = [menu.itemAtIndex_(index) for index in range(menu.numberOfItems())]
        by_title = {item.title(): item for item in items if item.title()}
        titles = [item.title() for item in items if item.title()]

        self.assertLess(titles.index("Agents"), titles.index("Devices"))
        self.assertIn("Keep Awake With Lid Closed", by_title)
        self.assertEqual(by_title["Never"].state(), 0)
        self.assertEqual(by_title["When Agents Work"].state(), 1)
        self.assertEqual(by_title["Always"].state(), 0)
        self.assertNotIn("Strong Sleep Override...", by_title)
        self.assertNotIn("Sleep Helper Missing", by_title)
        self.assertIn("Setup...", by_title)

    def test_status_bar_menu_shows_stale_statuses_when_no_fresh_statuses(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        status = AgentStatus(
            provider="codex",
            agent_id="codex:session:abc",
            display_name="Codex abc",
            mode=AgentMode.WORKING,
            updated_at=datetime.now(timezone.utc),
            event_name="PreToolUse",
            session_id="abc",
            stale=True,
        )
        snapshot = SimpleNamespace(
            statuses=(),
            stale_statuses=(status,),
            collected_at=datetime.now(timezone.utc),
        )
        target = SimpleNamespace(
            settings=AgentMonitorSettings(),
            closed_lid_awake=SimpleNamespace(last_error=None),
            status_bar_devices=lambda: [],
        )

        menu = status_bar.build_menu(snapshot, status_bar.STATE_IDLE, target)
        items = [
            menu.itemAtIndex_(index).title()
            for index in range(menu.numberOfItems())
            if menu.itemAtIndex_(index).title()
        ]

        self.assertNotIn("No recent sessions", items)
        # native_session_menu_title() is intentionally mode-free -- mode is
        # conveyed by the row's icon (session_row_icon_for_status), not text
        # -- per test_status_bar_native_session_row_uses_task_title, which
        # explicitly asserts "Working" never appears in a row title. This
        # just confirms the stale session renders as a real row using that
        # same, already-tested title format, not "Working  Codex abc".
        self.assertIn(status_bar.native_session_menu_title(status), items)

    def test_lid_animation_program_uses_device_brightness(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        animation = default_lid_animation(LID_ANIMATION_CLOSED)
        program = status_bar.program_for_lid_animation(animation, brightness=128)

        validate_led_text(program)
        self.assertTrue(program.startswith("brightness 128\n"))

    def test_lid_animation_restore_forces_led_resync(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        calls: list[tuple[str, object]] = []
        fake = SimpleNamespace(
            led_animation_token=42,
            led_animation_until_monotonic=100.0,
            last_snapshot=SimpleNamespace(
                aggregate=SimpleNamespace(mode=AgentMode.WORKING),
            ),
            last_battery_snapshot=object(),
            reset_led_controllers_for_display_change=lambda: calls.append(("reset", None)),
            active_led_display_kind=lambda snapshot: "agent",
            sync_leds=lambda mode, snapshot, display: calls.append(
                ("sync", (mode, snapshot, display))
            ),
            refresh_=lambda sender: calls.append(("refresh", sender)),
        )

        status_bar.restore_led_display(fake, "41")
        self.assertEqual(calls, [])
        self.assertEqual(fake.led_animation_until_monotonic, 100.0)

        status_bar.restore_led_display(fake, "42")
        self.assertEqual(fake.led_animation_until_monotonic, 0.0)
        self.assertEqual(calls[0], ("reset", None))
        self.assertEqual(calls[1][0], "sync")
        self.assertEqual(calls[1][1][0], AgentMode.WORKING)

    def test_status_bar_settings_window_has_lid_animation_controls(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        # build_settings_window now also reads target.settings and
        # target.status_bar_devices() (for the per-device controls
        # section), so this needs a real controller rather than a bare
        # SimpleNamespace -- isolated from the real settings file per the
        # usual rule (patch default_settings_path before construction).
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            with (
                patch("sidepulse.settings.default_settings_path", return_value=settings_path),
                patch("sidepulse.status_bar.default_settings_path", return_value=settings_path),
            ):
                target = status_bar.StatusBarController.alloc().init()
                window = status_bar.build_settings_window(target)

        self.assertEqual(window.title(), "SidePulse Agent Monitor Settings")
        self.assertIn("debug_log_status", target.settings_fields)
        self.assertIn("devin_session_opener", target.settings_fields)
        self.assertIn("closed_animation_program", target.settings_fields)
        self.assertIn("closed_animation_duration", target.settings_fields)
        self.assertIn("open_animation_program", target.settings_fields)
        self.assertIn("open_animation_duration", target.settings_fields)
        self.assertNotIn("closed_lid_system_override", target.settings_buttons)

    def test_status_bar_setup_window_has_first_launch_controls(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        target = SimpleNamespace(setup_fields={}, setup_buttons={})

        window = status_bar.build_setup_window(target)

        self.assertEqual(window.title(), "SidePulse Setup")
        self.assertIn("launch", target.setup_buttons)
        self.assertIn("eject_guard", target.setup_buttons)
        self.assertIn("eject_guard_uninstall", target.setup_buttons)
        self.assertIn("sleep_helper", target.setup_buttons)
        self.assertIn("launch_status", target.setup_fields)
        self.assertIn("eject_status", target.setup_fields)
        self.assertIn("sleep_status", target.setup_fields)

    def test_first_launch_setup_window_only_shows_until_completed(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        self.assertTrue(status_bar.should_show_setup_window(AgentMonitorSettings()))
        self.assertFalse(
            status_bar.should_show_setup_window(
                AgentMonitorSettings(setup_screen_completed=True)
            )
        )

    def test_setup_terminal_installer_opens_command_file(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            with (
                patch("sidepulse.status_bar.default_state_dir", return_value=state_dir),
                patch("sidepulse.status_bar.subprocess.Popen") as popen,
            ):
                script = status_bar.open_terminal_setup_command("echo hello")

            self.assertEqual(script, state_dir / "install-sleep-helper.command")
            self.assertIn("echo hello", script.read_text())
            self.assertEqual(script.stat().st_mode & 0o777, 0o700)
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0][0], "/usr/bin/open")

    def test_status_bar_open_session_remembers_action_by_origin(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        status = AgentStatus(
            provider="claude",
            agent_id="claude:session:abc",
            display_name="Claude abc",
            mode=AgentMode.WAITING_FOR_INPUT,
            updated_at=datetime.now(timezone.utc),
            event_name="Notification",
            session_id="1ca4348e-2aec-4147-9e81-d7d56364d257",
            cwd="/Users/pero/pgit/sdstatus_bitbang",
            origin="Claude in VS Code",
        )
        fake = SimpleNamespace(
            settings=AgentMonitorSettings(),
            messages=[],
            set_settings_message=lambda message: None,
        )

        with (
            patch("sidepulse.status_bar.open_terminal_command") as open_terminal,
            patch("sidepulse.status_bar.save_settings") as save,
        ):
            status_bar.StatusBarController.open_session(
                fake,
                status,
                SESSION_OPEN_TERMINAL,
                remember=True,
            )

        open_terminal.assert_called_once_with(
            "cd /Users/pero/pgit/sdstatus_bitbang && claude --resume 1ca4348e-2aec-4147-9e81-d7d56364d257"
        )
        self.assertEqual(
            fake.settings.session_open_action("claude", "Claude in VS Code"),
            SESSION_OPEN_TERMINAL,
        )
        self.assertIsNone(fake.settings.session_open_action("claude"))
        save.assert_called_once_with(fake.settings)

    def test_status_bar_primary_session_click_uses_saved_origin_preference(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        status = AgentStatus(
            provider="claude",
            agent_id="claude:session:abc",
            display_name="Claude abc",
            mode=AgentMode.WAITING_FOR_INPUT,
            updated_at=datetime.now(timezone.utc),
            event_name="Notification",
            session_id="1ca4348e-2aec-4147-9e81-d7d56364d257",
            cwd="/Users/pero/pgit/sdstatus_bitbang",
            origin="Claude in VS Code",
        )
        controller = status_bar.StatusBarController.alloc().init()
        sender = SimpleNamespace(representedObject=lambda: status)

        with (
            patch.object(status_bar.StatusBarController, "open_session", autospec=True) as open_session,
            patch.object(status_bar.StatusBarController, "close_status_menu", autospec=True) as close_menu,
        ):
            controller.openSessionPrimary_(sender)

        open_session.assert_called_once_with(controller, status, None, remember=False)
        close_menu.assert_called_once_with(controller)

    def test_codex_installer_replaces_monitor_hook_and_preserves_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "config.toml"
            log = base / "codex.jsonl"
            config.write_text(
                "\n".join(
                    [
                        '[features]',
                        'js_repl = false',
                        '',
                        '[[hooks.PreToolUse]]',
                        '[[hooks.PreToolUse.hooks]]',
                        'type = "command"',
                        f"command = '''echo old >> {log}'''",
                        '',
                        '[hooks.state]',
                        'source = "keep-me"',
                        '',
                    ]
                )
            )

            result = install_codex_hooks(
                log_path=log,
                config_path=config,
                python_executable="python3",
            )

            self.assertTrue(result.changed)
            text = config.read_text()
            self.assertIn("hooks = true", text)
            self.assertIn('[hooks.state]', text)
            self.assertIn('source = "keep-me"', text)
            self.assertIn("hook_entry.py", text)
            self.assertIn("--provider codex", text)
            self.assertIn(str(log), text)
            self.assertNotIn("echo old", text)

    def test_codex_installer_repeat_preserves_managed_block_before_trust_state(self) -> None:
        """An installed Codex config must not be rewritten only to move hook tables."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "config.toml"
            log = base / "codex.jsonl"
            current_command = f"fixture hook_entry.py --provider codex --log {log}"
            events = (
                "SessionStart",
                "UserPromptSubmit",
                "PreToolUse",
                "PostToolUse",
                "PermissionRequest",
                "PreCompact",
                "PostCompact",
                "SubagentStart",
                "SubagentStop",
                "Stop",
            )
            lines = [
                "[features]",
                "hooks = true",
                "",
                "# >>> agent-monitor hooks >>>",
                "# Provider-neutral status collection. Do not edit inside this block.",
            ]
            for event in events:
                lines.extend(
                    [
                        f"[[hooks.{event}]]",
                        'matcher = "*"',
                        f"[[hooks.{event}.hooks]]",
                        'type = "command"',
                        f"command = '''{current_command}'''",
                        "",
                    ]
                )
            lines.extend(
                [
                    "# <<< agent-monitor hooks <<<",
                    "",
                    "[hooks.state]",
                    'source = "preserve-me"',
                    "",
                    '[hooks.state."fixture:pre_tool_use:0:0"]',
                    'trusted_hash = "fixture-trusted-hash"',
                    "",
                ]
            )
            config.write_text("\n".join(lines))
            original = config.read_bytes()

            with patch("sidepulse.install.hook_command", return_value=current_command):
                result = install_codex_hooks(log_path=log, config_path=config)

            self.assertFalse(result.changed)
            self.assertIsNone(result.backup_path)
            self.assertEqual(config.read_bytes(), original)
            self.assertEqual(list(base.glob("config.toml.bak.*")), [])

    def test_codex_installer_replaces_stale_hook_alongside_exact_managed_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "config.toml"
            log = base / "codex.jsonl"
            current_command = f"fixture hook_entry.py --provider codex --log {log}"
            events = (
                "SessionStart",
                "UserPromptSubmit",
                "PreToolUse",
                "PostToolUse",
                "PermissionRequest",
                "PreCompact",
                "PostCompact",
                "SubagentStart",
                "SubagentStop",
                "Stop",
            )
            lines = [
                "[features]",
                "hooks = true",
                "",
                "# >>> agent-monitor hooks >>>",
                "# Provider-neutral status collection. Do not edit inside this block.",
            ]
            for event in events:
                lines.extend(
                    [
                        f"[[hooks.{event}]]",
                        'matcher = "*"',
                        f"[[hooks.{event}.hooks]]",
                        'type = "command"',
                        f"command = '''{current_command}'''",
                        "",
                    ]
                )
            lines.extend(
                [
                    "# <<< agent-monitor hooks <<<",
                    "",
                    "[[hooks.PreToolUse]]",
                    'matcher = "*"',
                    "[[hooks.PreToolUse.hooks]]",
                    'type = "command"',
                    "command = '''legacy hook_entry.py --provider codex'''",
                    "",
                    "[[hooks.PreToolUse]]",
                    'matcher = "*"',
                    "[[hooks.PreToolUse.hooks]]",
                    'type = "command"',
                    "command = '''echo preserve-unrelated-hook'''",
                    "",
                    "[hooks.state]",
                    'source = "preserve-me"',
                    "",
                ]
            )
            config.write_text("\n".join(lines))

            with patch("sidepulse.install.hook_command", return_value=current_command):
                result = install_codex_hooks(log_path=log, config_path=config)

            self.assertTrue(result.changed)
            text = config.read_text()
            self.assertNotIn("legacy hook_entry.py", text)
            self.assertIn("echo preserve-unrelated-hook", text)
            self.assertEqual(text.count(current_command), len(events))
            self.assertEqual(text.count("# >>> agent-monitor hooks >>>"), 1)
            self.assertEqual(text.count("# <<< agent-monitor hooks <<<"), 1)

            first_update = config.read_bytes()
            with patch("sidepulse.install.hook_command", return_value=current_command):
                repeat = install_codex_hooks(log_path=log, config_path=config)

            self.assertFalse(repeat.changed)
            self.assertIsNone(repeat.backup_path)
            self.assertEqual(config.read_bytes(), first_update)

    def test_codex_installer_refreshes_managed_hook_trust_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "config.toml"
            log = base / "codex.jsonl"
            key = f"{config}:pre_tool_use:0:0"
            config.write_text("[features]\nhooks = true\n")

            with patch("sidepulse.install.should_refresh_codex_hook_trust", return_value=True):
                with patch(
                    "sidepulse.install.resolve_codex_hook_hashes",
                    return_value={key: "sha256:new-current-hash"},
                ):
                    result = install_codex_hooks(
                        log_path=log,
                        config_path=config,
                        python_executable="python3",
                    )

            self.assertTrue(result.changed)
            text = config.read_text()
            self.assertIn(f'[hooks.state."{key}"]', text)
            self.assertIn('trusted_hash = "sha256:new-current-hash"', text)

    def test_update_codex_trusted_hashes_preserves_other_state(self) -> None:
        text = "\n".join(
            [
                "[hooks.state]",
                'source = "keep-me"',
                "",
                '[hooks.state."/tmp/config.toml:pre_tool_use:0:0"]',
                'trusted_hash = "sha256:old"',
                "",
            ]
        )

        updated = update_codex_trusted_hashes(
            text,
            {
                "/tmp/config.toml:pre_tool_use:0:0": "sha256:new",
                "/tmp/config.toml:stop:0:0": "sha256:stop",
            },
        )

        self.assertIn('source = "keep-me"', updated)
        self.assertIn('trusted_hash = "sha256:new"', updated)
        self.assertIn('[hooks.state."/tmp/config.toml:stop:0:0"]', updated)
        self.assertIn('trusted_hash = "sha256:stop"', updated)
        self.assertNotIn("sha256:old", updated)

    def test_claude_installer_replaces_sidepulse_hook_and_preserves_same_log_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "settings.json"
            log = base / "claude.jsonl"
            config.write_text(
                json.dumps(
                    {
                        "permissions": {"allow": ["Bash(date)"]},
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "*",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": f"jq -c . >> {log}",
                                        },
                                        {
                                            "type": "command",
                                            "command": (
                                                f"python hook_entry.py --provider claude --log {log}"
                                            ),
                                        },
                                        {
                                            "type": "command",
                                            "command": "echo keep >> /tmp/other.log",
                                        },
                                    ],
                                }
                            ]
                        },
                    }
                )
            )

            result = install_claude_hooks(
                log_path=log,
                config_path=config,
                python_executable="python3",
            )

            self.assertTrue(result.changed)
            data = json.loads(config.read_text())
            commands = [
                hook["command"]
                for entry in data["hooks"]["PreToolUse"]
                for hook in entry["hooks"]
            ]
            self.assertIn("echo keep >> /tmp/other.log", commands)
            self.assertIn(f"jq -c . >> {log}", commands)
            self.assertTrue(any("hook_entry.py" in command for command in commands))
            self.assertEqual(sum("--provider claude" in command for command in commands), 1)
            self.assertEqual(data["permissions"]["allow"], ["Bash(date)"])

    def test_grok_installer_writes_global_hook_file_without_lifecycle_matchers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "hooks" / "sidepulse.json"
            log = base / "grok.jsonl"
            config.parent.mkdir()
            config.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "run_terminal_command",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "echo keep >> /tmp/other.log",
                                        },
                                        {
                                            "type": "command",
                                            "command": f"jq -c . >> {log}",
                                        },
                                        {
                                            "type": "command",
                                            "command": (
                                                "agent-monitor hook-log --provider grok "
                                                f"--log {log}"
                                            ),
                                        },
                                    ],
                                }
                            ]
                        }
                    }
                )
            )

            result = install_grok_hooks(
                log_path=log,
                config_path=config,
                python_executable="python3",
            )

            self.assertTrue(result.changed)
            data = json.loads(config.read_text())
            pre_tool_commands = [
                hook["command"]
                for entry in data["hooks"]["PreToolUse"]
                for hook in entry["hooks"]
            ]
            self.assertIn("echo keep >> /tmp/other.log", pre_tool_commands)
            self.assertIn(f"jq -c . >> {log}", pre_tool_commands)
            self.assertTrue(any("--provider grok" in command for command in pre_tool_commands))
            self.assertEqual(sum("--provider grok" in command for command in pre_tool_commands), 1)
            self.assertIn("matcher", data["hooks"]["PreToolUse"][-1])
            self.assertNotIn("matcher", data["hooks"]["SessionStart"][-1])

    def test_devin_installer_preserves_agent_deck_hooks_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "config.json"
            log = base / "devin.jsonl"
            agent_deck = "/opt/homebrew/bin/bun /tmp/agent-deck-hook.ts"
            config.write_text(
                json.dumps(
                    {
                        "theme_mode": "dark",
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "^exec$",
                                    "hooks": [{"type": "command", "command": agent_deck}],
                                }
                            ],
                        },
                    }
                )
            )

            first = install_devin_hooks(log, config, python_executable="python3")
            first_text = config.read_text()
            second = install_devin_hooks(log, config, python_executable="python3")

            data = json.loads(config.read_text())
            commands = [
                hook["command"]
                for entry in data["hooks"]["PreToolUse"]
                for hook in entry["hooks"]
            ]
            self.assertTrue(first.changed)
            self.assertIsNotNone(first.backup_path)
            self.assertFalse(second.changed)
            self.assertEqual(config.read_text(), first_text)
            self.assertEqual(commands.count(agent_deck), 1)
            self.assertEqual(sum("--provider devin" in command for command in commands), 1)
            self.assertEqual(data["theme_mode"], "dark")

    def test_devin_installer_preserves_same_log_agent_deck_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "config.json"
            log = base / "devin.jsonl"
            agent_deck_commands = {
                event: f"bun /tmp/agent-deck-hook.ts --log {log} --event {event}"
                for event in DEVIN_EVENTS
            }
            source_command = f"python hook_entry.py --provider devin --log {log}"
            packaged_command = f"agent-monitor hook-log --provider devin --log {log}"
            config.write_text(
                json.dumps(
                    {
                        "hooks": {
                            event: [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": agent_deck_commands[event],
                                        }
                                    ]
                                }
                            ]
                            for event in DEVIN_EVENTS
                        }
                    }
                )
            )
            data = json.loads(config.read_text())
            data["hooks"]["PreToolUse"][0]["hooks"].extend(
                [
                    {"type": "command", "command": source_command},
                    {"type": "command", "command": packaged_command},
                ]
            )
            config.write_text(json.dumps(data))

            install_devin_hooks(log, config, python_executable="python3")

            commands = [
                hook["command"]
                for entries in json.loads(config.read_text())["hooks"].values()
                for entry in entries
                for hook in entry["hooks"]
            ]
            for command in agent_deck_commands.values():
                self.assertEqual(commands.count(command), 1)
            self.assertNotIn(source_command, commands)
            self.assertNotIn(packaged_command, commands)
            self.assertEqual(sum("--provider devin" in command for command in commands), len(DEVIN_EVENTS))

    def test_devin_uninstaller_preserves_same_log_agent_deck_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "config.json"
            log = base / "devin.jsonl"
            agent_deck = f"bun /tmp/agent-deck-hook.ts --log {log}"
            source_command = f"python hook_entry.py --provider devin --log {log}"
            packaged_command = f"agent-monitor hook-log --provider devin --log {log}"
            config.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [
                                {
                                    "hooks": [
                                        {"type": "command", "command": agent_deck},
                                        {"type": "command", "command": source_command},
                                        {"type": "command", "command": packaged_command},
                                    ]
                                }
                            ]
                        }
                    }
                )
            )

            result = uninstall_devin_hooks(log, config)

            data = json.loads(config.read_text())
            commands = [
                hook["command"]
                for entry in data.get("hooks", {}).get("Stop", [])
                for hook in entry["hooks"]
            ]
            self.assertTrue(result.changed)
            self.assertEqual(commands, [agent_deck])

    def test_devin_uninstaller_removes_only_sidepulse_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "config.json"
            log = base / "devin.jsonl"
            config.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [
                                {
                                    "hooks": [
                                        {"type": "command", "command": "echo keep-agent-deck"}
                                    ]
                                }
                            ]
                        }
                    }
                )
            )
            install_devin_hooks(log, config, python_executable="python3")

            result = uninstall_devin_hooks(log, config)

            self.assertTrue(result.changed)
            self.assertIn("keep-agent-deck", config.read_text())
            self.assertNotIn("--provider devin", config.read_text())

    def test_codex_uninstaller_removes_monitor_hooks_and_preserves_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "config.toml"
            log = base / "codex.jsonl"
            config.write_text(
                "\n".join(
                    [
                        "[features]",
                        "js_repl = false",
                        "",
                        "[hooks.state]",
                        'source = "keep-me"',
                        "",
                    ]
                )
            )
            install_codex_hooks(log_path=log, config_path=config, python_executable="python3")

            result = uninstall_codex_hooks(log_path=log, config_path=config)

            self.assertTrue(result.changed)
            text = config.read_text()
            self.assertIn("[features]", text)
            self.assertIn("js_repl = false", text)
            self.assertIn("[hooks.state]", text)
            self.assertIn('source = "keep-me"', text)
            self.assertNotIn("agent-monitor hooks", text)
            self.assertNotIn(str(log), text)

    def test_claude_uninstaller_removes_monitor_hooks_and_preserves_other_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "settings.json"
            log = base / "claude.jsonl"
            config.write_text(
                json.dumps(
                    {
                        "permissions": {"allow": ["Bash(date)"]},
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "*",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "echo keep >> /tmp/other.log",
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                )
            )
            install_claude_hooks(log_path=log, config_path=config, python_executable="python3")

            result = uninstall_claude_hooks(log_path=log, config_path=config)

            self.assertTrue(result.changed)
            data = json.loads(config.read_text())
            commands = [
                hook["command"]
                for entry in data["hooks"]["PreToolUse"]
                for hook in entry["hooks"]
            ]
            self.assertEqual(commands, ["echo keep >> /tmp/other.log"])
            self.assertEqual(data["permissions"]["allow"], ["Bash(date)"])

    def test_grok_uninstaller_removes_monitor_hooks_and_preserves_other_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "hooks" / "sidepulse.json"
            log = base / "grok.jsonl"
            config.parent.mkdir()
            config.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "run_terminal_command",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "echo keep >> /tmp/other.log",
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                )
            )
            install_grok_hooks(log_path=log, config_path=config, python_executable="python3")

            result = uninstall_grok_hooks(log_path=log, config_path=config)

            self.assertTrue(result.changed)
            data = json.loads(config.read_text())
            commands = [
                hook["command"]
                for entry in data["hooks"]["PreToolUse"]
                for hook in entry["hooks"]
            ]
            self.assertEqual(commands, ["echo keep >> /tmp/other.log"])

    def test_detect_grok_config_reads_managed_hook_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".grok" / "hooks" / "sidepulse.json"
            log = home / "state" / "grok.jsonl"
            install_grok_hooks(log_path=log, config_path=config, python_executable="python3")

            detected = detect_grok_config(home)

            self.assertEqual(detected.provider, "grok")
            self.assertTrue(detected.exists)
            self.assertTrue(detected.hooks_enabled)
            self.assertIn("PreToolUse", detected.hook_events)
            self.assertIn(log, detected.log_paths)

    def test_sidepulse_sidepulse_command_shape(self) -> None:
        parser = build_parser(prog="sidepulse agent-monitor")

        install = parser.parse_args(["install"])
        live = parser.parse_args(["live", "--recent-seconds", "120"])
        leds = parser.parse_args(["leds", "--once", "--dry-run"])
        uninstall = parser.parse_args(["uninstall"])
        status_bar = parser.parse_args(["status-bar"])
        status_bar_foreground = parser.parse_args(["status-bar", "--foreground"])
        grok_install = parser.parse_args(["install", "grok"])
        grok_hook_log = parser.parse_args(["hook-log", "--provider", "grok", "--log", "/tmp/grok.jsonl"])

        self.assertEqual(install.provider, "all")
        self.assertEqual(grok_install.provider, "grok")
        self.assertEqual(live.command, "live")
        self.assertEqual(live.recent_seconds, 120)
        self.assertEqual(leds.command, "leds")
        self.assertTrue(leds.once)
        self.assertTrue(leds.dry_run)
        self.assertEqual(uninstall.provider, "all")
        self.assertEqual(status_bar.command, "status-bar")
        self.assertFalse(status_bar.foreground)
        self.assertFalse(status_bar.uninstall)
        self.assertTrue(status_bar_foreground.foreground)
        self.assertEqual(grok_hook_log.provider, "grok")
        self.assertIn("sidepulse agent-monitor", parser.format_usage())

    def test_devin_cli_install_and_log_arguments_are_available(self) -> None:
        parser = build_parser(prog="sidepulse agent-monitor")

        install = parser.parse_args(["install", "devin", "--devin-log", "/tmp/devin.jsonl"])
        hook_log = parser.parse_args(
            ["hook-log", "--provider", "devin", "--log", "/tmp/devin.jsonl"]
        )

        self.assertEqual(install.provider, "devin")
        self.assertEqual(install.devin_log, Path("/tmp/devin.jsonl"))
        self.assertEqual(hook_log.provider, "devin")

    def test_sidepulse_entrypoint_dispatches_to_sidepulse(self) -> None:
        with patch.object(cli_module, "main", return_value=17) as main:
            result = cli_module.sidepulse_main(["agent-monitor", "live"])

        self.assertEqual(result, 17)
        main.assert_called_once_with(["live"], prog="sidepulse agent-monitor")

    def test_sidepulse_battery_command_shape(self) -> None:
        parser = cli_module.build_sidepulse_parser()

        status = parser.parse_args(["battery", "status", "--json"])
        leds = parser.parse_args(["battery", "leds", "--once", "--dry-run", "--full-watts", "140"])
        configure = parser.parse_args(["battery", "configure", "--display", "battery"])

        self.assertEqual(status.command, "battery")
        self.assertEqual(status.battery_command, "status")
        self.assertTrue(status.json)
        self.assertEqual(leds.battery_command, "leds")
        self.assertTrue(leds.once)
        self.assertTrue(leds.dry_run)
        self.assertEqual(leds.full_watts, "140")
        self.assertEqual(configure.battery_command, "configure")
        self.assertEqual(configure.display, "battery")

    def test_sidepulse_status_bar_root_command_shape(self) -> None:
        parser = cli_module.build_sidepulse_parser()

        default = parser.parse_args(["status-bar"])
        start = parser.parse_args(["status-bar", "start", "--foreground"])
        stop = parser.parse_args(["status-bar", "stop"])
        helper = parser.parse_args(["status-bar", "install-sleep-helper", "--dry-run"])

        self.assertEqual(default.command, "status-bar")
        self.assertEqual(default.status_bar_command, "start")
        self.assertFalse(default.foreground)
        self.assertEqual(start.status_bar_command, "start")
        self.assertTrue(start.foreground)
        self.assertEqual(stop.status_bar_command, "stop")
        self.assertEqual(helper.status_bar_command, "install-sleep-helper")
        self.assertTrue(helper.dry_run)

    def test_sidepulse_sdejectguard_command_shape(self) -> None:
        parser = cli_module.build_sidepulse_parser()

        start = parser.parse_args(["sdejectguard", "start"])
        interactive = parser.parse_args(["sdejectguard", "start", "-it", "--scope", "user"])
        stop = parser.parse_args(["sdejectguard", "stop", "--scope", "system"])
        uninstall = parser.parse_args(["sdejectguard", "uninstall", "--scope", "user", "--dry-run"])
        logs = parser.parse_args(["sdejectguard", "logs", "--lines", "12", "--follow"])

        self.assertEqual(start.command, "sdejectguard")
        self.assertEqual(start.sdejectguard_command, "start")
        self.assertEqual(start.scope, "auto")
        self.assertFalse(start.interactive)
        self.assertTrue(interactive.interactive)
        self.assertEqual(interactive.scope, "user")
        self.assertEqual(stop.sdejectguard_command, "stop")
        self.assertEqual(stop.scope, "system")
        self.assertEqual(uninstall.sdejectguard_command, "uninstall")
        self.assertEqual(uninstall.scope, "user")
        self.assertTrue(uninstall.dry_run)
        self.assertEqual(logs.sdejectguard_command, "logs")
        self.assertEqual(logs.lines, 12)
        self.assertTrue(logs.follow)

    def test_sidepulse_sdejectguard_start_uses_launchd_installer(self) -> None:
        parser = cli_module.build_sidepulse_parser()
        args = parser.parse_args(["sdejectguard", "start", "--scope", "user"])
        guard_result = SimpleNamespace(
            dry_run=False,
            changed=True,
            started=True,
            scope="user",
            plist_path=Path("/tmp/io.sidepulse.sdejectguard.plist"),
            binary_path=Path("/tmp/sd_eject_guard"),
            cleanup_removed=None,
            cleanup_skipped=None,
        )

        with patch(
            "sidepulse.sd_eject_guard_launch.install_sd_eject_guard",
            return_value=guard_result,
        ) as install:
            result = cli_module.cmd_sidepulse_sdejectguard_start(args)

        self.assertEqual(result, 0)
        install.assert_called_once_with(scope="user", dry_run=False)

    def test_sidepulse_sdejectguard_start_interactive_runs_foreground(self) -> None:
        parser = cli_module.build_sidepulse_parser()
        args = parser.parse_args(["sdejectguard", "start", "-it", "--scope", "user"])

        with patch(
            "sidepulse.sd_eject_guard_launch.run_sd_eject_guard_interactive",
            return_value=0,
        ) as run:
            result = cli_module.cmd_sidepulse_sdejectguard_start(args)

        self.assertEqual(result, 0)
        run.assert_called_once_with(scope="user")

    def test_sidepulse_sdejectguard_stop_calls_guard_stop(self) -> None:
        parser = cli_module.build_sidepulse_parser()
        args = parser.parse_args(["sdejectguard", "stop", "--scope", "user", "--dry-run"])
        stop_result = SimpleNamespace(
            scope="user",
            plist_path=Path("/tmp/io.sidepulse.sdejectguard.plist"),
            stopped=True,
            skipped=None,
        )

        with patch(
            "sidepulse.sd_eject_guard_launch.stop_sd_eject_guard",
            return_value=(stop_result,),
        ) as stop:
            result = cli_module.cmd_sidepulse_sdejectguard_stop(args)

        self.assertEqual(result, 0)
        stop.assert_called_once_with(scope="user", dry_run=True)

    def test_sidepulse_sdejectguard_uninstall_calls_guard_uninstall(self) -> None:
        parser = cli_module.build_sidepulse_parser()
        args = parser.parse_args(["sdejectguard", "uninstall", "--scope", "user", "--dry-run"])
        uninstall_result = SimpleNamespace(
            scope="user",
            plist_path=Path("/tmp/io.sidepulse.sdejectguard.plist"),
            removed_paths=(Path("/tmp/io.sidepulse.sdejectguard.plist"),),
            skipped=None,
            dry_run=True,
        )

        with patch(
            "sidepulse.sd_eject_guard_launch.uninstall_sd_eject_guard",
            return_value=(uninstall_result,),
        ) as uninstall:
            result = cli_module.cmd_sidepulse_sdejectguard_uninstall(args)

        self.assertEqual(result, 0)
        uninstall.assert_called_once_with(scope="user", dry_run=True)

    def test_sidepulse_setup_command_shape(self) -> None:
        parser = cli_module.build_sidepulse_parser()

        default = parser.parse_args(["setup"])
        codex_only = parser.parse_args(
            [
                "setup",
                "codex",
                "--no-status-bar",
                "--dry-run",
                "--sd-eject-guard-scope",
                "user",
            ]
        )

        self.assertEqual(default.command, "setup")
        self.assertEqual(default.provider, "all")
        self.assertEqual(default.sd_eject_guard_scope, "auto")
        self.assertFalse(default.no_status_bar)
        self.assertFalse(default.dry_run)
        self.assertEqual(codex_only.provider, "codex")
        self.assertEqual(codex_only.sd_eject_guard_scope, "user")
        self.assertTrue(codex_only.no_status_bar)
        self.assertTrue(codex_only.dry_run)

    def test_sidepulse_setup_installs_hooks_guard_and_status_bar(self) -> None:
        parser = cli_module.build_sidepulse_parser()
        args = parser.parse_args(["setup"])
        codex_result = SimpleNamespace(
            provider="codex",
            config_path=Path("/tmp/codex.toml"),
            log_path=Path("/tmp/codex.jsonl"),
            changed=True,
            backup_path=None,
        )
        claude_result = SimpleNamespace(
            provider="claude",
            config_path=Path("/tmp/settings.json"),
            log_path=Path("/tmp/claude.jsonl"),
            changed=False,
            backup_path=None,
        )
        devin_result = SimpleNamespace(
            provider="devin",
            config_path=Path("/tmp/devin-config.json"),
            log_path=Path("/tmp/devin.jsonl"),
            changed=True,
            backup_path=None,
        )
        grok_result = SimpleNamespace(
            provider="grok",
            config_path=Path("/tmp/grok-hook.json"),
            log_path=Path("/tmp/grok.jsonl"),
            changed=True,
            backup_path=None,
        )
        launch_result = SimpleNamespace(
            plist_path=Path("/tmp/io.sidepulse.agentstatus.plist"),
            changed=True,
            started=True,
        )
        guard_result = SimpleNamespace(
            dry_run=False,
            changed=True,
            started=True,
            scope="user",
            plist_path=Path("/tmp/io.sidepulse.sdejectguard.plist"),
            binary_path=Path("/tmp/sd_eject_guard"),
            cleanup_removed=None,
            cleanup_skipped=None,
        )

        with (
            patch.object(
                cli_module,
                "install_provider_hooks",
                side_effect=(codex_result, claude_result, devin_result, grok_result),
            ) as install,
            patch(
                "sidepulse.sd_eject_guard_launch.install_sd_eject_guard",
                return_value=guard_result,
            ) as guard,
            patch(
                "sidepulse.status_bar_launch.install_launch_agent",
                return_value=launch_result,
            ) as launch,
        ):
            result = cli_module.cmd_sidepulse_setup(args)

        self.assertEqual(result, 0)
        self.assertEqual(
            [call.args[0] for call in install.call_args_list],
            ["codex", "claude", "devin", "grok"],
        )
        guard.assert_called_once_with(scope="auto", dry_run=False)
        launch.assert_called_once_with(start=True)

    def test_sidepulse_setup_no_status_bar_still_installs_guard(self) -> None:
        parser = cli_module.build_sidepulse_parser()
        args = parser.parse_args(["setup", "--no-status-bar", "--sd-eject-guard-scope", "user"])
        hook_result = SimpleNamespace(
            provider="codex",
            config_path=Path("/tmp/codex.toml"),
            log_path=Path("/tmp/codex.jsonl"),
            changed=False,
            backup_path=None,
        )
        guard_result = SimpleNamespace(
            dry_run=False,
            changed=False,
            started=True,
            scope="user",
            plist_path=Path("/tmp/io.sidepulse.sdejectguard.plist"),
            binary_path=Path("/tmp/sd_eject_guard"),
            cleanup_removed=None,
            cleanup_skipped=None,
        )

        with (
            patch.object(cli_module, "install_hook_results", return_value=[hook_result]),
            patch(
                "sidepulse.sd_eject_guard_launch.install_sd_eject_guard",
                return_value=guard_result,
            ) as guard,
            patch("sidepulse.status_bar_launch.install_launch_agent") as launch,
        ):
            result = cli_module.cmd_sidepulse_setup(args)

        self.assertEqual(result, 0)
        guard.assert_called_once_with(scope="user", dry_run=False)
        launch.assert_not_called()

    def test_sidepulse_write_decodes_escaped_newlines_and_writes_leds_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / "SidePulsePro"
            device.mkdir()

            target = write_led_program(
                r"off\n#FF00FF pulse",
                device_path=device,
            )

            self.assertEqual(target, device / "LEDS.LED")
            self.assertEqual(target.read_text(), "off\n#FF00FF pulse")

    def test_sidepulse_write_uses_leds_led_even_when_old_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / "SidePulseDot"
            device.mkdir()
            (device / "LEDS.TXT").write_text("off")

            target = write_led_program(
                r"off\n#FF00FF pulse",
                device_path=device,
            )

            self.assertEqual(target, device / "LEDS.LED")
            self.assertEqual(target.read_text(), "off\n#FF00FF pulse")
            self.assertEqual((device / "LEDS.TXT").read_text(), "off")

    def test_sidepulse_write_discovers_sidepulse_dot_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mount_root = Path(tmp)
            device = mount_root / "SidePulseDot"
            device.mkdir()

            candidates = discover_devices(mount_root=mount_root)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].root, device)
            self.assertEqual(candidates[0].target, device / "LEDS.LED")

    def test_sidepulse_write_prefers_leds_led_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mount_root = Path(tmp)
            device = mount_root / "SidePulsePro"
            device.mkdir()
            (device / "LEDS.LED").write_text("off")

            candidates = discover_devices(mount_root=mount_root)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].target, device / "LEDS.LED")

    def test_device_discovery_ignores_old_leds_txt_on_unnamed_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mount_root = Path(tmp)
            device = mount_root / "USB Drive"
            device.mkdir()
            (device / "LEDS.TXT").write_text("off")

            candidates = discover_devices(mount_root=mount_root)

            self.assertEqual(candidates, [])

    def test_device_discovery_skips_mount_io_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mount_root = Path(tmp)
            good = mount_root / "SidePulseDot"
            bad = mount_root / "SidePulsePro"
            good.mkdir()
            bad.mkdir()
            (good / "LEDS.LED").write_text("off")
            original_is_dir = Path.is_dir

            def flaky_is_dir(path: Path) -> bool:
                if path == bad:
                    raise OSError("offline")
                return original_is_dir(path)

            with patch.object(Path, "is_dir", flaky_is_dir):
                candidates = discover_devices(mount_root=mount_root)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].root, good)

    def test_sidepulse_write_validates_device_limits(self) -> None:
        self.assertEqual(normalize_led_text(r"off\n#FF00FF pulse"), "off\n#FF00FF pulse")
        with self.assertRaises(DeviceWriteError):
            write_led_program("x" * 513, device_path=Path("/tmp/device"), dry_run=True)
        write_led_program("\n".join(["off"] * 20), device_path=Path("/tmp/device"), dry_run=True)
        with self.assertRaises(DeviceWriteError):
            write_led_program("\n".join(["off"] * 21), device_path=Path("/tmp/device"), dry_run=True)

    def test_sidepulse_write_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / "SidePulseDot"
            device.mkdir()
            result = cli_module.sidepulse_main(
                ["write", r"off\n#FF00FF pulse", "--device", str(device)]
            )

            self.assertEqual(result, 0)
            self.assertEqual((device / "LEDS.LED").read_text(), "off\n#FF00FF pulse")

    def test_led_status_maps_agent_modes_to_programs(self) -> None:
        self.assertEqual(
            display_state_for_mode(AgentMode.WAITING_FOR_INPUT),
            LedDisplayState.ASK,
        )
        self.assertEqual(
            display_state_for_mode(AgentMode.TOOL_RUNNING),
            LedDisplayState.WORKING,
        )
        self.assertEqual(
            display_state_for_mode(AgentMode.COMPLETED),
            LedDisplayState.DONE,
        )
        self.assertEqual(
            display_state_for_mode(AgentMode.IDLE_READY),
            LedDisplayState.IDLE,
        )

        # The leading settle line eases to "off"/floor via a short cosine
        # transition (settle_duration_ms()) rather than a bare, un-eased
        # snap -- see led_status.settle_duration_ms for why: a bare
        # assignment reads as the animation abruptly stopping whenever a
        # real status change interrupts an in-progress pulse.
        self.assertEqual(
            program_for_display_state(LedDisplayState.IDLE),
            "off 160ms cosine\n#020204 6s pulse\nrepeat",
        )
        self.assertEqual(program_for_display_state(LedDisplayState.DONE), "#00FF66")
        self.assertIn("#FF3A00 1.6s pulse", program_for_display_state(LedDisplayState.ASK))
        self.assertEqual(
            program_for_display_state(LedDisplayState.WORKING, led_count=2).splitlines(),
            [
                "off 91ms cosine",
                "0:#00E5FF 760ms pulse 0ms; 1:#00E5FF 760ms pulse 260ms",
                "repeat",
            ],
        )
        self.assertEqual(
            len(program_for_display_state(LedDisplayState.WORKING, led_count=8).splitlines()),
            3,
        )
        self.assertEqual(
            program_for_display_state(LedDisplayState.DONE, brightness=128),
            "brightness 128\n#00FF66",
        )

    def test_write_mode_to_leds_uses_device_specific_program(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / "SidePulseDot"
            device.mkdir()

            result = write_mode_to_leds(AgentMode.WORKING, device_path=device)

            self.assertEqual(result.state, LedDisplayState.WORKING)
            self.assertEqual(result.target, device / "LEDS.LED")
            self.assertEqual(
                (device / "LEDS.LED").read_text(),
                "off 91ms cosine\n"
                "0:#00E5FF 760ms pulse 0ms; 1:#00E5FF 760ms pulse 260ms\n"
                "repeat",
            )

            write_mode_to_leds(AgentMode.IDLE_READY, device_path=device)

            self.assertEqual(
                (device / "LEDS.LED").read_text(),
                "off 160ms cosine\n#020204 6s pulse\nrepeat",
            )

            write_mode_to_leds(AgentMode.COMPLETED, device_path=device, brightness=64)

            self.assertEqual((device / "LEDS.LED").read_text(), "brightness 64\n#00FF66")

    def test_led_count_uses_product_name(self) -> None:
        self.assertEqual(led_count_for_target(Path("/Volumes/SidePulseDot/LEDS.LED")), 2)
        self.assertEqual(led_count_for_target(Path("/Volumes/SidePulsePro/LEDS.LED")), 8)

    def test_sidepulse_working_program_uses_eight_leds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / "SidePulsePro"
            device.mkdir()

            write_mode_to_leds(AgentMode.WORKING, device_path=device)

            lines = (device / "LEDS.LED").read_text().splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[0], "off 91ms cosine")
            self.assertIn("0:#00E5FF 760ms pulse 0ms", lines[1])
            self.assertIn("5:#00E5FF 760ms pulse 475ms", lines[1])
            self.assertIn("7:#00E5FF 760ms pulse 665ms", lines[1])
            self.assertEqual(lines[-1], "repeat")

    def test_agent_led_controller_skips_unchanged_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / "SidePulsePro"
            device.mkdir()
            controller = AgentLedController(device_path=device)

            first = controller.sync_mode(AgentMode.COMPLETED)
            second = controller.sync_mode(AgentMode.COMPLETED)
            third = controller.sync_mode(AgentMode.WAITING_FOR_INPUT)

            self.assertTrue(first.changed)
            self.assertFalse(second.changed)
            self.assertTrue(third.changed)
            self.assertIn("#FF3A00 1.6s pulse", (device / "LEDS.LED").read_text())

    def test_battery_parser_uses_adapter_watts_and_raw_capacity(self) -> None:
        payload = plistlib.dumps(
            [
                {
                    "CurrentCapacity": 50,
                    "ExternalConnected": True,
                    "IsCharging": True,
                    "FullyCharged": False,
                    "Voltage": 12000,
                    "Amperage": 1000,
                    "AppleRawCurrentCapacity": 4000,
                    "AppleRawMaxCapacity": 8000,
                    "DesignCapacity": 10000,
                    "CycleCount": 12,
                    "AdapterDetails": {
                        "Watts": 96,
                        "AdapterVoltage": 20000,
                        "Current": 4800,
                        "UsbHvcMenu": [
                            {"MaxVoltage": 5000, "MaxCurrent": 3000},
                            {"MaxVoltage": 20000, "MaxCurrent": 4800},
                        ],
                    },
                }
            ]
        )

        snapshot = parse_ioreg_battery_plist(payload)

        self.assertEqual(snapshot.percent, 50)
        self.assertTrue(snapshot.is_plugged)
        self.assertTrue(snapshot.is_charging)
        self.assertEqual(snapshot.adapter_power, 96)
        self.assertEqual(snapshot.health_percent, 80)
        self.assertEqual(snapshot.current_capacity_mah, 4000)
        self.assertEqual(len(snapshot.pd_profiles), 2)

    def test_battery_program_matches_simulator_frontier_pulse(self) -> None:
        snapshot = BatterySnapshot(
            percent=50,
            is_plugged=True,
            is_charging=True,
            adapter_watts=70,
            full_charge_watts=140,
        )

        program = program_for_battery(snapshot, led_count=8)

        validate_led_text(program)
        lines = program.splitlines()
        self.assertIn(f"0:{BATTERY_CHARGING_MINT} 360ms ease", lines[0])
        self.assertIn(f"3:{BATTERY_CHARGING_MINT} 360ms ease", lines[0])
        self.assertIn("4:#000000 360ms ease", lines[0])
        self.assertEqual(lines[1], f"4:{BATTERY_CHARGING_MINT} 790ms pulse")
        self.assertEqual(len(lines), 2)
        self.assertNotIn("repeat", program)
        self.assertNotIn("\noff", program)

    def test_unplugged_battery_program_eases_to_static_level(self) -> None:
        snapshot = BatterySnapshot(percent=50, is_plugged=False)

        program = program_for_battery(snapshot, led_count=8)

        validate_led_text(program)
        self.assertEqual(len(program.splitlines()), 1)
        self.assertIn("0:#FFB000 360ms ease", program)
        self.assertIn("3:#FFB000 360ms ease", program)
        self.assertIn("4:#000000 360ms ease", program)
        self.assertNotIn("repeat", program)

    def test_battery_program_uses_partial_next_led(self) -> None:
        snapshot = BatterySnapshot(percent=57, is_plugged=False)

        program = program_for_battery(snapshot, led_count=8)

        validate_led_text(program)
        segments = program.split(";")
        self.assertEqual(segments[0], "0:#00FF66 360ms ease")
        self.assertEqual(segments[3], "3:#00FF66 360ms ease")
        self.assertEqual(segments[4], "4:#008F39 360ms ease")
        self.assertEqual(segments[5], "5:#000000 360ms ease")

    def test_battery_program_uses_brightness_command(self) -> None:
        snapshot = BatterySnapshot(percent=57, is_plugged=False)

        program = program_for_battery(snapshot, led_count=8, brightness=128)

        validate_led_text(program)
        self.assertTrue(program.startswith("brightness 128\n"))

    def test_battery_program_uses_full_speed_steady_pulse(self) -> None:
        snapshot = BatterySnapshot(
            percent=80,
            is_plugged=True,
            is_charging=True,
            adapter_watts=140,
            full_charge_watts=140,
        )

        program = program_for_battery(snapshot, led_count=8)

        validate_led_text(program)
        self.assertIn(f"6:{BATTERY_CHARGING_MINT} 1400ms pulse", program)
        self.assertNotIn("repeat", program)
        self.assertNotIn("none", program)

    def test_battery_led_controller_animates_charging_on_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / "SidePulsePro"
            device.mkdir()
            controller = BatteryLedController(device_path=device)
            snapshot = BatterySnapshot(
                percent=50,
                is_plugged=True,
                is_charging=True,
                adapter_watts=70,
                full_charge_watts=140,
            )

            with patch(
                "sidepulse.battery.time.monotonic",
                side_effect=[0.0, 0.5, 2.0],
            ):
                first = controller.sync_snapshot(snapshot)
                second = controller.sync_snapshot(snapshot)
                third = controller.sync_snapshot(snapshot)

            self.assertTrue(first.changed)
            self.assertFalse(second.changed)
            self.assertTrue(third.changed)

    def test_battery_led_controller_skips_unchanged_static_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / "SidePulsePro"
            device.mkdir()
            controller = BatteryLedController(device_path=device)
            snapshot = BatterySnapshot(percent=50, is_plugged=False)

            with patch(
                "sidepulse.battery.time.monotonic",
                side_effect=[0.0, 10.0],
            ):
                first = controller.sync_snapshot(snapshot)
                second = controller.sync_snapshot(snapshot)

            self.assertTrue(first.changed)
            self.assertFalse(second.changed)

    def test_keep_awake_holds_working_then_graces_done(self) -> None:
        processes: list[FakeProcess] = []

        def factory(*_args, **_kwargs):
            process = FakeProcess()
            processes.append(process)
            return process

        controller = KeepAwakeController(
            grace_seconds=300,
            process_factory=factory,
        )

        self.assertTrue(controller.update(AgentMode.WORKING, now=100))
        self.assertEqual(len(processes), 1)
        self.assertTrue(controller.process_running())

        self.assertTrue(controller.update(AgentMode.COMPLETED, now=110))
        self.assertIn("grace", controller.detail(now=110))

        self.assertTrue(controller.update(AgentMode.IDLE_READY, now=200))
        self.assertTrue(controller.process_running())

        # Completed -> Idle Ready is a real mode change (a momentary "looks
        # idle" blip between tool calls, say), so it re-arms a fresh grace
        # window from t=200 -- still held at t=411 (200+300=500), where it
        # would have incorrectly released under the old behavior that only
        # re-armed for the three explicitly-tracked "looks done" modes.
        self.assertTrue(controller.update(AgentMode.IDLE_READY, now=411))
        self.assertTrue(controller.process_running())

        # No further mode changes -- the window from the last change (500)
        # eventually does expire.
        self.assertFalse(controller.update(AgentMode.IDLE_READY, now=501))
        self.assertFalse(controller.process_running())
        self.assertTrue(processes[0].terminated)

    def test_keep_awake_grants_grace_on_a_bare_idle_blip_never_seeing_a_terminal_mode(self) -> None:
        # Regression guard for the reported bug: a momentary "looks idle"
        # reading (e.g. a brief gap between tool calls, reported as a bare
        # IDLE_READY fallback rather than an explicit Completed) must get
        # the same grace period a Completed/Waiting/Blocked transition
        # gets -- previously it got *zero* grace at all (immediate
        # release) since IDLE_READY was never in the tracked "looks done"
        # set that started the grace window.
        controller = KeepAwakeController(grace_seconds=300, process_factory=lambda *a, **k: FakeProcess())
        self.assertTrue(controller.update(AgentMode.WORKING, now=0))
        self.assertTrue(controller.update(AgentMode.IDLE_READY, now=1))
        self.assertTrue(controller.process_running(), "a bare idle blip must still get the grace window")
        self.assertTrue(controller.update(AgentMode.IDLE_READY, now=299))
        self.assertFalse(controller.update(AgentMode.IDLE_READY, now=302))

    def test_keep_awake_set_grace_seconds_takes_effect_on_the_next_update(self) -> None:
        controller = KeepAwakeController(grace_seconds=300, process_factory=lambda *a, **k: FakeProcess())
        controller.set_grace_seconds(30)
        self.assertTrue(controller.update(AgentMode.WORKING, now=0))
        self.assertTrue(controller.update(AgentMode.IDLE_READY, now=1))
        self.assertFalse(controller.update(AgentMode.IDLE_READY, now=32))

    def test_keep_awake_ask_grace_expires_without_refresh_extension(self) -> None:
        processes: list[FakeProcess] = []

        def factory(*_args, **_kwargs):
            process = FakeProcess()
            processes.append(process)
            return process

        controller = KeepAwakeController(
            grace_seconds=300,
            process_factory=factory,
        )

        self.assertTrue(controller.update(AgentMode.WAITING_FOR_INPUT, now=100))
        self.assertTrue(controller.update(AgentMode.WAITING_FOR_INPUT, now=350))
        self.assertFalse(controller.update(AgentMode.WAITING_FOR_INPUT, now=401))
        self.assertEqual(len(processes), 1)
        self.assertTrue(processes[0].terminated)

    def test_keep_awake_touches_keepalive_file_once_per_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / "SidePulsePro"
            device.mkdir()
            status_path = device / "keepalive"
            reads: list[Path] = []

            controller = KeepAwakeController(
                status_read_seconds=60,
                status_reader=lambda path: reads.append(path),
                status_read_async=False,
            )

            self.assertEqual(status_file_for_target(device / "LEDS.LED"), status_path)
            self.assertEqual(status_file_for_target(device / "STATUS.TXT"), status_path)
            self.assertEqual(
                controller.poke_status_file(device / "LEDS.LED", now=0),
                status_path,
            )
            self.assertIsNone(controller.poke_status_file(device / "LEDS.LED", now=30))
            self.assertEqual(
                controller.poke_status_file(device / "LEDS.LED", now=61),
                status_path,
            )
            self.assertEqual(reads, [status_path, status_path])

    def test_closed_lid_awake_policy_decisions(self) -> None:
        self.assertFalse(
            closed_lid_awake_should_hold(CLOSED_LID_AWAKE_NEVER, agents_active=True)
        )
        self.assertFalse(
            closed_lid_awake_should_hold(CLOSED_LID_AWAKE_AGENTS, agents_active=False)
        )
        self.assertTrue(
            closed_lid_awake_should_hold(CLOSED_LID_AWAKE_AGENTS, agents_active=True)
        )
        self.assertTrue(
            closed_lid_awake_should_hold(CLOSED_LID_AWAKE_ALWAYS, agents_active=False)
        )

    def test_closed_lid_awake_controller_sets_and_restores_system_disable(self) -> None:
        processes: list[FakeProcess] = []
        disabled_calls: list[bool] = []

        def factory(*_args, **_kwargs):
            process = FakeProcess()
            processes.append(process)
            return process

        controller = ClosedLidAwakeController(
            process_factory=factory,
            sleep_disabled_reader=lambda: False,
            sleep_disabled_setter=disabled_calls.append,
            use_system_disable=True,
        )

        self.assertTrue(
            controller.update(CLOSED_LID_AWAKE_ALWAYS, agents_active=False)
        )
        self.assertEqual(disabled_calls, [True])
        self.assertTrue(controller.changed_system_disable)
        self.assertEqual(len(processes), 1)

        controller.update(CLOSED_LID_AWAKE_ALWAYS, agents_active=False)
        self.assertEqual(disabled_calls, [True])

        self.assertFalse(
            controller.update(CLOSED_LID_AWAKE_NEVER, agents_active=False)
        )
        self.assertEqual(disabled_calls, [True, False])
        self.assertTrue(processes[0].terminated)

    def test_closed_lid_awake_controller_defaults_to_user_mode_only(self) -> None:
        disabled_calls: list[bool] = []
        controller = ClosedLidAwakeController(
            process_factory=lambda *_args, **_kwargs: FakeProcess(),
            sleep_disabled_reader=lambda: False,
            sleep_disabled_setter=disabled_calls.append,
        )

        self.assertTrue(
            controller.update(CLOSED_LID_AWAKE_ALWAYS, agents_active=False)
        )

        self.assertEqual(disabled_calls, [])
        self.assertTrue(controller.process_running())
        self.assertFalse(controller.changed_system_disable)

    def test_closed_lid_awake_controller_preserves_existing_system_disable(self) -> None:
        disabled_calls: list[bool] = []
        controller = ClosedLidAwakeController(
            process_factory=lambda *_args, **_kwargs: FakeProcess(),
            sleep_disabled_reader=lambda: True,
            sleep_disabled_setter=disabled_calls.append,
            use_system_disable=True,
        )

        controller.update(CLOSED_LID_AWAKE_ALWAYS, agents_active=False)
        controller.update(CLOSED_LID_AWAKE_NEVER, agents_active=False)

        self.assertEqual(disabled_calls, [])

    def test_sleep_override_uses_noninteractive_sudo(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "", "")

        run_sudo_pmset_disablesleep(True, runner=runner)

        self.assertEqual(
            calls[0][0],
            ["/usr/bin/sudo", "-n", "/usr/bin/pmset", "-a", "disablesleep", "1"],
        )
        self.assertEqual(calls[0][1]["check"], False)

    def test_sleep_override_reports_missing_helper_without_prompting(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                "sudo: a password is required",
            )

        with self.assertRaises(SleepHelperRequiredError) as ctx:
            run_sudo_pmset_disablesleep(False, runner=runner)

        self.assertIn("install-sleep-helper", str(ctx.exception))
        self.assertNotIn("/usr/bin/osascript", calls[0])

    def test_sleep_helper_sudoers_rule_is_narrow(self) -> None:
        self.assertEqual(
            sleep_helper_sudoers_rule("pero"),
            "pero ALL=(root) NOPASSWD: "
            "/usr/bin/pmset -a disablesleep 0, "
            "/usr/bin/pmset -a disablesleep 1\n",
        )
        with self.assertRaises(ValueError):
            sleep_helper_sudoers_rule("bad user")

    def test_lid_state_parser_reads_ioreg_booleans(self) -> None:
        self.assertTrue(
            parse_bool_ioreg_property('"AppleClamshellState" = Yes', "AppleClamshellState")
        )
        self.assertFalse(
            parse_bool_ioreg_property('"AppleClamshellState" = No', "AppleClamshellState")
        )
        self.assertTrue(parse_bool_ioreg_property('"SleepDisabled" = true', "SleepDisabled"))
        self.assertIsNone(parse_bool_ioreg_property('"Other" = Yes', "SleepDisabled"))

    def test_default_logs_use_sidepulse_xdg_state_dir(self) -> None:
        home = Path("/Users/example")

        self.assertEqual(
            default_state_dir(home),
            home / ".local" / "state" / "sidepulse" / "agent-monitor",
        )
        self.assertEqual(
            default_log_path("codex", home),
            home / ".local" / "state" / "sidepulse" / "agent-monitor" / "codex.jsonl",
        )

        with patch.dict(os.environ, {"XDG_STATE_HOME": "/tmp/xdg-state"}):
            self.assertEqual(
                default_state_dir(),
                Path("/tmp/xdg-state") / "sidepulse" / "agent-monitor",
            )

    def test_install_defaults_to_standard_state_log_path(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["install", "codex"])

        with patch.object(
            cli_module,
            "default_log_path",
            return_value=Path("/tmp/state/sidepulse/agent-monitor/codex.jsonl"),
        ):
            self.assertEqual(
                cli_module.install_log_path("codex", args),
                Path("/tmp/state/sidepulse/agent-monitor/codex.jsonl"),
            )

    def test_settings_use_xdg_config_dir_and_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_home = Path(tmp) / "xdg-config"
            settings_path = config_home / "sidepulse" / "agent-monitor" / "settings.json"

            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}):
                self.assertEqual(default_config_dir(), settings_path.parent)
                self.assertEqual(default_settings_path(), settings_path)

                saved = AgentMonitorSettings(
                    codex_transcripts_enabled=False,
                    claude_transcripts_enabled=True,
                )
                self.assertEqual(save_settings(saved), settings_path)
                self.assertEqual(load_settings(), saved)

    def test_default_sources_respect_transcript_settings(self) -> None:
        settings = AgentMonitorSettings(
            codex_transcripts_enabled=False,
            claude_transcripts_enabled=True,
        )

        providers = [source.provider for source in default_sources(settings)]

        self.assertNotIn("codex-transcripts", providers)
        self.assertIn("claude-transcripts", providers)

    def test_default_sources_are_hook_only_by_default(self) -> None:
        providers = [source.provider for source in default_sources(AgentMonitorSettings())]

        self.assertIn("codex", providers)
        self.assertIn("claude", providers)
        self.assertIn("grok", providers)
        self.assertNotIn("codex-transcripts", providers)
        self.assertNotIn("claude-transcripts", providers)

    def test_default_sources_include_registered_hook_providers(self) -> None:
        with patch("sidepulse.collector.load_settings", return_value=AgentMonitorSettings()):
            sources = default_sources()

        providers = tuple(
            source.provider for source in sources if not source.provider.endswith("-transcript")
        )
        self.assertEqual(providers, HOOK_PROVIDERS)

    def test_settings_round_trip_remembered_device_display_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings = AgentMonitorSettings(
                devices=(
                    DeviceDisplaySetting(
                        device_id="/Volumes/SidePulsePro",
                        name="SidePulse Pro",
                        path="/Volumes/SidePulsePro",
                        led_display="agent",
                    ),
                    DeviceDisplaySetting(
                        device_id="/Volumes/SidePulseDot",
                        name="SidePulse Dot",
                        path="/Volumes/SidePulseDot",
                        led_display="battery",
                        brightness=128,
                    ),
                )
            )

            save_settings(settings, settings_path)
            loaded = load_settings(settings_path)

            self.assertEqual(loaded.devices, settings.devices)
            self.assertEqual(loaded.display_for_device("/Volumes/SidePulsePro"), "agent")
            self.assertEqual(loaded.display_for_device("/Volumes/SidePulseDot"), "battery")
            self.assertEqual(loaded.brightness_for_device("/Volumes/SidePulseDot"), 128)

    def test_settings_round_trip_remembered_device_brightness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings = AgentMonitorSettings().with_device_brightness(
                "/Volumes/SidePulseDot",
                96,
                name="SidePulse Dot",
                path="/Volumes/SidePulseDot",
            )

            save_settings(settings, settings_path)
            loaded = load_settings(settings_path)

            self.assertEqual(loaded.brightness_for_device("/Volumes/SidePulseDot"), 96)

    def test_settings_round_trip_session_open_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings = AgentMonitorSettings().with_session_open_action(
                "Claude",
                SESSION_OPEN_TERMINAL,
                "Claude in VS Code",
            )

            save_settings(settings, settings_path)
            loaded = load_settings(settings_path)

            self.assertEqual(
                loaded.session_open_action("claude", "Claude in VS Code"),
                SESSION_OPEN_TERMINAL,
            )
            self.assertIsNone(loaded.session_open_action("claude"))

    def test_settings_round_trip_closed_lid_policy_and_animations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings = AgentMonitorSettings().with_closed_lid_awake_policy(
                CLOSED_LID_AWAKE_AGENTS
            )
            settings = settings.with_lid_animation(
                LID_ANIMATION_CLOSED,
                program="off\n#FF3A00 200ms ease",
                duration_seconds=1.4,
            )
            settings = settings.with_lid_animation(
                LID_ANIMATION_OPEN,
                program="off\n#00FF66 200ms ease",
                duration_seconds=1.6,
            )

            save_settings(settings, settings_path)
            loaded = load_settings(settings_path)

            self.assertEqual(loaded.closed_lid_awake_policy, CLOSED_LID_AWAKE_AGENTS)
            self.assertEqual(
                loaded.lid_animation(LID_ANIMATION_CLOSED).program,
                "off\n#FF3A00 200ms ease",
            )
            self.assertEqual(
                loaded.lid_animation(LID_ANIMATION_OPEN).duration_seconds,
                1.6,
            )

            enabled = settings.with_closed_lid_system_override(True)
            save_settings(enabled, settings_path)
            loaded_enabled = load_settings(settings_path)
            self.assertTrue(loaded_enabled.closed_lid_system_override_enabled)

            completed = settings.with_setup_screen_completed(True)
            save_settings(completed, settings_path)
            loaded_completed = load_settings(settings_path)
            self.assertTrue(loaded_completed.setup_screen_completed)

    def test_settings_migrate_missing_lid_fields_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(json.dumps({"led_display": "agent"}))

            loaded = load_settings(settings_path)

            self.assertEqual(loaded.closed_lid_awake_policy, CLOSED_LID_AWAKE_NEVER)
            self.assertFalse(loaded.closed_lid_system_override_enabled)
            self.assertFalse(loaded.setup_screen_completed)
            self.assertEqual(
                loaded.lid_animation(LID_ANIMATION_CLOSED),
                default_lid_animation(LID_ANIMATION_CLOSED),
            )

    def test_settings_remember_device_preserves_existing_display_choice(self) -> None:
        settings = AgentMonitorSettings().with_device_display(
            "/Volumes/SidePulseDot",
            "battery",
            name="SidePulse Dot",
            path="/Volumes/SidePulseDot",
        )

        remembered = settings.with_remembered_device(
            device_id="/Volumes/SidePulseDot",
            name="SidePulse Dot",
            path="/Volumes/SidePulseDot",
        )

        self.assertEqual(remembered.display_for_device("/Volumes/SidePulseDot"), "battery")
        self.assertEqual(remembered.brightness_for_device("/Volumes/SidePulseDot"), 255)

    def test_settings_remove_remembered_device(self) -> None:
        settings = AgentMonitorSettings(
            devices=(
                DeviceDisplaySetting(
                    device_id="/Volumes/SidePulsePro",
                    name="SidePulse Pro",
                    path="/Volumes/SidePulsePro",
                    led_display="agent",
                ),
                DeviceDisplaySetting(
                    device_id="/Volumes/SidePulseDot",
                    name="SidePulse Dot",
                    path="/Volumes/SidePulseDot",
                    led_display="battery",
                ),
            )
        )

        updated = settings.without_device("/Volumes/SidePulseDot")

        self.assertEqual([device.device_id for device in updated.devices], ["/Volumes/SidePulsePro"])
        self.assertEqual(updated.display_for_device("/Volumes/SidePulseDot"), "agent")

    def test_disconnected_device_menu_has_remove_option(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        device = status_bar.StatusBarDevice(
            device_id="/Volumes/SidePulsePro",
            name="SidePulse Pro",
            root=Path("/Volumes/SidePulsePro"),
            target=Path("/Volumes/SidePulsePro/LEDS.LED"),
            connected=False,
            display="agent",
        )
        item = status_bar.build_device_menu_item(device, None)
        submenu = item.submenu()
        titles = [
            submenu.itemAtIndex_(index).title()
            for index in range(submenu.numberOfItems())
            if submenu.itemAtIndex_(index).title()
        ]

        self.assertIn("Not connected", titles)
        self.assertIn("Remove", titles)

    def test_status_bar_launch_agent_plist_runs_foreground_command(self) -> None:
        plist = build_launch_agent_plist(
            python_executable="/usr/bin/python3",
            stdout_path=Path("/tmp/sidepulse.out.log"),
            stderr_path=Path("/tmp/sidepulse.err.log"),
        )

        self.assertEqual(plist["Label"], LAUNCH_AGENT_LABEL)
        self.assertEqual(
            plist["ProgramArguments"],
            [
                "/usr/bin/python3",
                "-m",
                "sidepulse",
                "status-bar",
                "--foreground",
            ],
        )
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["StandardOutPath"], "/tmp/sidepulse.out.log")
        self.assertEqual(plist["StandardErrorPath"], "/tmp/sidepulse.err.log")
        self.assertNotIn("KeepAlive", plist)

    def test_status_bar_launch_agent_installed_checks_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plist = Path(tmp) / "io.sidepulse.agentstatus.plist"

            self.assertFalse(launch_agent_installed(plist))
            plist.write_bytes(b"plist")
            self.assertTrue(launch_agent_installed(plist))

    def test_frozen_status_bar_launch_agent_uses_sidepulse_executable(self) -> None:
        with patch("sidepulse.status_bar_launch.sys.frozen", True, create=True):
            plist = build_launch_agent_plist(
                stdout_path=Path("/tmp/sidepulse.out.log"),
                stderr_path=Path("/tmp/sidepulse.err.log"),
            )

        self.assertEqual(
            plist["ProgramArguments"],
            [sys.executable, "status-bar", "start", "--foreground"],
        )

    def test_frozen_hook_command_uses_internal_cli(self) -> None:
        with patch("sidepulse.install.sys.frozen", True, create=True):
            command = hook_command("codex", Path("/tmp/codex events.jsonl"))

        self.assertEqual(
            command,
            f"{sys.executable} agent-monitor hook-log --provider codex "
            "--log '/tmp/codex events.jsonl'",
        )

    def test_status_bar_install_removes_legacy_com_sidepulse_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "io.sidepulse.agentstatus.plist"
            legacy = base / "com.sidepulse.agentstatus.plist"
            legacy.write_bytes(b"old")

            with (
                patch("sidepulse.status_bar_launch.default_state_dir", return_value=base / "state"),
                patch("sidepulse.status_bar_launch.subprocess.run") as run,
            ):
                result = install_launch_agent(
                    start=False,
                    plist_path=target,
                    legacy_plist_path=legacy,
                    python_executable="/usr/bin/python3",
                )

            self.assertTrue(result.changed)
            self.assertTrue(target.exists())
            self.assertFalse(legacy.exists())
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][0:2], ["launchctl", "bootout"])

    def test_sd_eject_guard_plist_shapes_for_user_and_system_scopes(self) -> None:
        for scope in ("user", "system"):
            paths = SdEjectGuardPaths(
                scope=scope,
                plist_path=Path(f"/tmp/{scope}/io.sidepulse.sdejectguard.plist"),
                binary_path=Path(f"/tmp/{scope}/sd_eject_guard"),
                stdout_path=Path(f"/tmp/{scope}/sd-eject-guard.out.log"),
                stderr_path=Path(f"/tmp/{scope}/sd-eject-guard.err.log"),
            )

            plist = build_sd_eject_guard_plist(paths)

            self.assertEqual(plist["Label"], SD_EJECT_GUARD_LABEL)
            self.assertEqual(plist["ProgramArguments"], [str(paths.binary_path)])
            self.assertTrue(plist["RunAtLoad"])
            self.assertTrue(plist["KeepAlive"])
            self.assertEqual(plist["StandardOutPath"], str(paths.stdout_path))
            self.assertEqual(plist["StandardErrorPath"], str(paths.stderr_path))

    def test_sd_eject_guard_default_binary_uses_background_item_name(self) -> None:
        self.assertEqual(SD_EJECT_GUARD_BINARY_NAME, SD_EJECT_GUARD_DISPLAY_NAME)

    def test_sd_eject_guard_installed_checks_user_and_system_plists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            user_paths = SdEjectGuardPaths(
                scope="user",
                plist_path=base / "user" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "user" / "sd_eject_guard",
                stdout_path=base / "user" / "out.log",
                stderr_path=base / "user" / "err.log",
            )
            system_paths = SdEjectGuardPaths(
                scope="system",
                plist_path=base / "system" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "system" / "sd_eject_guard",
                stdout_path=base / "system" / "out.log",
                stderr_path=base / "system" / "err.log",
            )

            self.assertFalse(
                sd_eject_guard_installed(
                    user_paths=user_paths,
                    system_paths=system_paths,
                )
            )
            user_paths.plist_path.parent.mkdir(parents=True)
            user_paths.plist_path.write_bytes(b"plist")

            self.assertTrue(
                sd_eject_guard_installed(
                    user_paths=user_paths,
                    system_paths=system_paths,
                )
            )
            self.assertTrue(
                sd_eject_guard_installed(
                    "user",
                    user_paths=user_paths,
                    system_paths=system_paths,
                )
            )
            self.assertFalse(
                sd_eject_guard_installed(
                    "system",
                    user_paths=user_paths,
                    system_paths=system_paths,
                )
            )

    def test_sd_eject_guard_auto_falls_back_to_user_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "sd_eject_guard.c"
            source.write_text("int main(void) { return 0; }\n")
            user_paths = SdEjectGuardPaths(
                scope="user",
                plist_path=base / "user" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "user" / "sd_eject_guard",
                stdout_path=base / "user" / "out.log",
                stderr_path=base / "user" / "err.log",
            )
            system_paths = SdEjectGuardPaths(
                scope="system",
                plist_path=base / "system" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "system" / "sd_eject_guard",
                stdout_path=base / "system" / "out.log",
                stderr_path=base / "system" / "err.log",
            )
            calls = []

            def fake_run(command, *args, **kwargs):
                calls.append(command)
                if command[0] == "clang":
                    Path(command[3]).write_bytes(b"binary")
                return subprocess.CompletedProcess(command, 0)

            with (
                patch("sidepulse.sd_eject_guard_launch.os.geteuid", return_value=501),
                patch("sidepulse.sd_eject_guard_launch.os.getuid", return_value=501),
                patch("sidepulse.sd_eject_guard_launch.subprocess.run", side_effect=fake_run),
            ):
                result = install_sd_eject_guard(
                    scope="auto",
                    source_path=source,
                    user_paths=user_paths,
                    system_paths=system_paths,
                )

            self.assertEqual(result.scope, "user")
            self.assertTrue(result.compiled)
            self.assertTrue(result.started)
            self.assertTrue(user_paths.binary_path.exists())
            self.assertTrue(user_paths.plist_path.exists())
            self.assertEqual(calls[0][0:4], ["clang", "-O2", "-o", str(user_paths.binary_path.with_name("sd_eject_guard.tmp"))])
            self.assertIn("-framework", calls[0])
            self.assertIn(["launchctl", "bootstrap", "gui/501", str(user_paths.plist_path)], calls)

    def test_sd_eject_guard_install_removes_legacy_binary_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "sd_eject_guard.c"
            source.write_text("int main(void) { return 0; }\n")
            user_paths = SdEjectGuardPaths(
                scope="user",
                plist_path=base / "user" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "user" / SD_EJECT_GUARD_BINARY_NAME,
                stdout_path=base / "user" / "out.log",
                stderr_path=base / "user" / "err.log",
            )
            system_paths = SdEjectGuardPaths(
                scope="system",
                plist_path=base / "system" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "system" / SD_EJECT_GUARD_BINARY_NAME,
                stdout_path=base / "system" / "out.log",
                stderr_path=base / "system" / "err.log",
            )
            legacy = user_paths.binary_path.with_name("sd_eject_guard")
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"legacy")

            def fake_run(command, *args, **kwargs):
                if command[0] == "clang":
                    Path(command[3]).write_bytes(b"binary")
                return subprocess.CompletedProcess(command, 0)

            with (
                patch("sidepulse.sd_eject_guard_launch.os.geteuid", return_value=501),
                patch("sidepulse.sd_eject_guard_launch.os.getuid", return_value=501),
                patch("sidepulse.sd_eject_guard_launch.subprocess.run", side_effect=fake_run),
            ):
                result = install_sd_eject_guard(
                    scope="user",
                    source_path=source,
                    user_paths=user_paths,
                    system_paths=system_paths,
                )

            self.assertTrue(user_paths.binary_path.exists())
            self.assertFalse(legacy.exists())
            self.assertEqual(result.legacy_removed, (legacy,))

    def test_sd_eject_guard_user_install_reports_skipped_system_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "sd_eject_guard.c"
            source.write_text("int main(void) { return 0; }\n")
            user_paths = SdEjectGuardPaths(
                scope="user",
                plist_path=base / "user" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "user" / "sd_eject_guard",
                stdout_path=base / "user" / "out.log",
                stderr_path=base / "user" / "err.log",
            )
            system_paths = SdEjectGuardPaths(
                scope="system",
                plist_path=base / "system" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "system" / "sd_eject_guard",
                stdout_path=base / "system" / "out.log",
                stderr_path=base / "system" / "err.log",
            )
            system_paths.plist_path.parent.mkdir(parents=True)
            system_paths.plist_path.write_bytes(b"old")

            def fake_run(command, *args, **kwargs):
                if command[0] == "clang":
                    Path(command[3]).write_bytes(b"binary")
                return subprocess.CompletedProcess(command, 0)

            with (
                patch("sidepulse.sd_eject_guard_launch.os.geteuid", return_value=501),
                patch("sidepulse.sd_eject_guard_launch.os.getuid", return_value=501),
                patch("sidepulse.sd_eject_guard_launch.subprocess.run", side_effect=fake_run),
            ):
                result = install_sd_eject_guard(
                    scope="user",
                    source_path=source,
                    user_paths=user_paths,
                    system_paths=system_paths,
                )

            self.assertIsNone(result.cleanup_removed)
            self.assertIn(str(system_paths.plist_path), result.cleanup_skipped or "")
            self.assertTrue(system_paths.plist_path.exists())

    def test_sd_eject_guard_stop_auto_stops_user_and_skips_system_without_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            user_paths = SdEjectGuardPaths(
                scope="user",
                plist_path=base / "user" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "user" / "sd_eject_guard",
                stdout_path=base / "user" / "out.log",
                stderr_path=base / "user" / "err.log",
            )
            system_paths = SdEjectGuardPaths(
                scope="system",
                plist_path=base / "system" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "system" / "sd_eject_guard",
                stdout_path=base / "system" / "out.log",
                stderr_path=base / "system" / "err.log",
            )
            user_paths.plist_path.parent.mkdir(parents=True)
            system_paths.plist_path.parent.mkdir(parents=True)
            user_paths.plist_path.write_bytes(b"user")
            system_paths.plist_path.write_bytes(b"system")

            with (
                patch("sidepulse.sd_eject_guard_launch.os.geteuid", return_value=501),
                patch("sidepulse.sd_eject_guard_launch.os.getuid", return_value=501),
                patch("sidepulse.sd_eject_guard_launch.subprocess.run") as run,
            ):
                results = stop_sd_eject_guard(
                    scope="auto",
                    user_paths=user_paths,
                    system_paths=system_paths,
                )

            self.assertEqual(len(results), 2)
            self.assertTrue(results[0].stopped)
            self.assertFalse(results[1].stopped)
            self.assertIn("missing permissions", results[1].skipped or "")
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][0:3], ["launchctl", "bootout", "gui/501"])

    def test_sd_eject_guard_uninstall_removes_plist_binary_and_legacy_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            paths = SdEjectGuardPaths(
                scope="user",
                plist_path=base / "user" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "user" / SD_EJECT_GUARD_BINARY_NAME,
                stdout_path=base / "user" / "out.log",
                stderr_path=base / "user" / "err.log",
            )
            system_paths = SdEjectGuardPaths(
                scope="system",
                plist_path=base / "system" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "system" / SD_EJECT_GUARD_BINARY_NAME,
                stdout_path=base / "system" / "out.log",
                stderr_path=base / "system" / "err.log",
            )
            legacy = paths.binary_path.with_name("sd_eject_guard")
            paths.plist_path.parent.mkdir(parents=True)
            paths.plist_path.write_bytes(b"plist")
            paths.binary_path.write_bytes(b"binary")
            legacy.write_bytes(b"legacy")

            with (
                patch("sidepulse.sd_eject_guard_launch.os.geteuid", return_value=501),
                patch("sidepulse.sd_eject_guard_launch.os.getuid", return_value=501),
                patch("sidepulse.sd_eject_guard_launch.subprocess.run") as run,
            ):
                results = uninstall_sd_eject_guard(
                    scope="user",
                    user_paths=paths,
                    system_paths=system_paths,
                )

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].stopped)
            self.assertEqual(
                set(results[0].removed_paths),
                {paths.plist_path, paths.binary_path, legacy},
            )
            self.assertFalse(paths.plist_path.exists())
            self.assertFalse(paths.binary_path.exists())
            self.assertFalse(legacy.exists())
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][0:3], ["launchctl", "bootout", "gui/501"])

    def test_sd_eject_guard_system_scope_requires_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "sd_eject_guard.c"
            source.write_text("int main(void) { return 0; }\n")
            system_paths = SdEjectGuardPaths(
                scope="system",
                plist_path=base / "system" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "system" / "sd_eject_guard",
                stdout_path=base / "system" / "out.log",
                stderr_path=base / "system" / "err.log",
            )

            with patch("sidepulse.sd_eject_guard_launch.os.geteuid", return_value=501):
                with self.assertRaisesRegex(SdEjectGuardInstallError, "requires root"):
                    install_sd_eject_guard(
                        scope="system",
                        source_path=source,
                        system_paths=system_paths,
                    )

    def test_sd_eject_guard_system_install_cleans_user_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "sd_eject_guard.c"
            source.write_text("int main(void) { return 0; }\n")
            user_paths = SdEjectGuardPaths(
                scope="user",
                plist_path=base / "user" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "user" / "sd_eject_guard",
                stdout_path=base / "user" / "out.log",
                stderr_path=base / "user" / "err.log",
            )
            system_paths = SdEjectGuardPaths(
                scope="system",
                plist_path=base / "system" / "io.sidepulse.sdejectguard.plist",
                binary_path=base / "system" / "sd_eject_guard",
                stdout_path=base / "system" / "out.log",
                stderr_path=base / "system" / "err.log",
            )
            user_paths.plist_path.parent.mkdir(parents=True)
            user_paths.plist_path.write_bytes(b"old")
            calls = []

            def fake_run(command, *args, **kwargs):
                calls.append(command)
                if command[0] == "clang":
                    Path(command[3]).write_bytes(b"binary")
                return subprocess.CompletedProcess(command, 0)

            with (
                patch("sidepulse.sd_eject_guard_launch.os.geteuid", return_value=0),
                patch("sidepulse.sd_eject_guard_launch.os.getuid", return_value=501),
                patch("sidepulse.sd_eject_guard_launch.os.chown") as chown,
                patch("sidepulse.sd_eject_guard_launch.subprocess.run", side_effect=fake_run),
            ):
                result = install_sd_eject_guard(
                    scope="system",
                    source_path=source,
                    user_paths=user_paths,
                    system_paths=system_paths,
                )

            self.assertEqual(result.scope, "system")
            self.assertEqual(result.cleanup_removed, user_paths.plist_path)
            self.assertFalse(user_paths.plist_path.exists())
            self.assertTrue(system_paths.plist_path.exists())
            self.assertIn(["launchctl", "bootstrap", "system", str(system_paths.plist_path)], calls)
            chown.assert_any_call(system_paths.binary_path, 0, 0)
            chown.assert_any_call(system_paths.plist_path, 0, 0)

    def test_watch_filters_to_recent_statuses(self) -> None:
        now = datetime.now(timezone.utc)
        recent = AgentStatus(
            provider="codex",
            agent_id="recent",
            display_name="Recent",
            mode=AgentMode.WORKING,
            updated_at=now - timedelta(seconds=20),
            event_name="PostToolUse",
        )
        older = AgentStatus(
            provider="claude",
            agent_id="older",
            display_name="Older",
            mode=AgentMode.COMPLETED,
            updated_at=now - timedelta(seconds=600),
            event_name="Stop",
        )
        snapshot = MonitorSnapshot(
            aggregate=AggregateStatus(AgentMode.WORKING, 2, 0, recent),
            statuses=(recent, older),
            stale_statuses=(),
            sources=(),
            collected_at=now,
        )

        visible = visible_watch_statuses(snapshot, recent_seconds=120, include_stale=False)

        self.assertEqual([status.agent_id for status in visible], ["recent"])

    def test_orphaned_tool_running_expires_before_session_stale_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            old = datetime.now(timezone.utc) - timedelta(seconds=180)
            log.write_text(
                json.dumps(
                    {
                        "logged_at": old.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "event": {
                            "hook_event_name": "PreToolUse",
                            "session_id": "codex-session",
                            "tool_name": "Bash",
                        },
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=300,
                tool_running_timeout_seconds=120,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.IDLE_READY)
            self.assertEqual(snapshot.statuses, ())
            self.assertEqual(len(snapshot.stale_statuses), 1)
            self.assertEqual(snapshot.stale_statuses[0].mode, AgentMode.TOOL_RUNNING)

    def test_completed_status_expires_before_session_stale_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            old = datetime.now(timezone.utc) - timedelta(seconds=60)
            log.write_text(
                json.dumps(
                    {
                        "logged_at": old.isoformat(),
                        "event": {
                            "hook_event_name": "Stop",
                            "session_id": "codex-session",
                            "last_assistant_message": "Done.",
                        },
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=3600,
                completed_visible_seconds=15,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.IDLE_READY)
            self.assertEqual(snapshot.statuses, ())
            self.assertEqual(len(snapshot.stale_statuses), 1)
            self.assertEqual(snapshot.stale_statuses[0].mode, AgentMode.COMPLETED)

    def test_completed_status_stays_visible_for_twenty_minutes_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            recent_done = datetime.now(timezone.utc) - timedelta(minutes=19)
            log.write_text(
                json.dumps(
                    {
                        "logged_at": recent_done.isoformat(),
                        "event": {
                            "hook_event_name": "Stop",
                            "session_id": "codex-session",
                            "last_assistant_message": "Done.",
                        },
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=3600,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.COMPLETED)
            self.assertEqual(len(snapshot.statuses), 1)

    def test_completed_status_is_hidden_when_active_work_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            log.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "logged_at": now,
                                "event": {
                                    "hook_event_name": "Stop",
                                    "session_id": "done-session",
                                    "last_assistant_message": "Done.",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "logged_at": now,
                                "event": {
                                    "hook_event_name": "PreToolUse",
                                    "session_id": "working-session",
                                    "tool_name": "Bash",
                                },
                            }
                        ),
                    ]
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=3600,
                completed_visible_seconds=15,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.active_count, 1)
            self.assertEqual([status.session_id for status in snapshot.statuses], ["working-session"])
            self.assertEqual(snapshot.stale_statuses[0].session_id, "done-session")

    def test_idle_notification_does_not_resurrect_completed_claude_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "claude.jsonl"
            old = datetime.now(timezone.utc) - timedelta(minutes=25)
            log.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "logged_at": old.isoformat(),
                                "hook_event_name": "Stop",
                                "session_id": "claude-session",
                                "cwd": "/tmp/project",
                                "last_assistant_message": "Done and verified.",
                                "background_tasks": [],
                                "session_crons": [],
                            }
                        ),
                        json.dumps(
                            {
                                "logged_at": (old + timedelta(seconds=60)).isoformat(),
                                "hook_event_name": "Notification",
                                "session_id": "claude-session",
                                "cwd": "/tmp/project",
                                "notification_type": "idle_prompt",
                                "message": "Claude is waiting for your input",
                            }
                        ),
                    ]
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("claude", log),),
                stale_after_seconds=3600,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.IDLE_READY)
            self.assertEqual(snapshot.statuses, ())
            self.assertEqual(snapshot.stale_statuses[0].mode, AgentMode.COMPLETED)

    def test_codex_permission_request_stays_ask_during_unrelated_tool_activity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            now = datetime.now(timezone.utc)
            session_id = "019f179b-7fdc-7eb0-a3af-1ca3eb128eee"
            server_command = ".venv/bin/bambucuts server --host 127.0.0.1 --port 5425"
            curl_command = "curl -s http://127.0.0.1:5425/api/status | head -c 1000"
            events = [
                {
                    "logged_at": now.isoformat(),
                    "event": {
                        "hook_event_name": "PreToolUse",
                        "session_id": session_id,
                        "cwd": "/Users/pero/pgit/a1plotter",
                        "tool_name": "Bash",
                        "tool_input": {"command": server_command},
                    },
                },
                {
                    "logged_at": (now + timedelta(seconds=1)).isoformat(),
                    "event": {
                        "hook_event_name": "PermissionRequest",
                        "session_id": session_id,
                        "cwd": "/Users/pero/pgit/a1plotter",
                        "tool_name": "Bash",
                        "tool_input": {"command": server_command},
                    },
                },
                {
                    "logged_at": (now + timedelta(seconds=2)).isoformat(),
                    "event": {
                        "hook_event_name": "PreToolUse",
                        "session_id": session_id,
                        "cwd": "/Users/pero/pgit/a1plotter",
                        "tool_name": "Bash",
                        "tool_input": {"command": curl_command},
                    },
                },
                {
                    "logged_at": (now + timedelta(seconds=3)).isoformat(),
                    "event": {
                        "hook_event_name": "PostToolUse",
                        "session_id": session_id,
                        "cwd": "/Users/pero/pgit/a1plotter",
                        "tool_name": "Bash",
                        "tool_input": {"command": curl_command},
                        "tool_response": "{}",
                    },
                },
            ]
            log.write_text("\n".join(json.dumps(event) for event in events) + "\n")

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=3600,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.WAITING_FOR_INPUT)
            self.assertEqual(snapshot.statuses[0].event_name, "PermissionRequest")

    def test_codex_permission_request_clears_when_matching_tool_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            now = datetime.now(timezone.utc)
            session_id = "019f179b-7fdc-7eb0-a3af-1ca3eb128eee"
            command = "curl -s http://127.0.0.1:5425/api/status | head -c 1000"
            events = [
                {
                    "logged_at": now.isoformat(),
                    "event": {
                        "hook_event_name": "PreToolUse",
                        "session_id": session_id,
                        "cwd": "/Users/pero/pgit/a1plotter",
                        "tool_name": "Bash",
                        "tool_input": {"command": command},
                    },
                },
                {
                    "logged_at": (now + timedelta(seconds=1)).isoformat(),
                    "event": {
                        "hook_event_name": "PermissionRequest",
                        "session_id": session_id,
                        "cwd": "/Users/pero/pgit/a1plotter",
                        "tool_name": "Bash",
                        "tool_input": {"command": command},
                    },
                },
                {
                    "logged_at": (now + timedelta(seconds=2)).isoformat(),
                    "event": {
                        "hook_event_name": "PostToolUse",
                        "session_id": session_id,
                        "cwd": "/Users/pero/pgit/a1plotter",
                        "tool_name": "Bash",
                        "tool_input": {"command": command},
                        "tool_response": "{}",
                    },
                },
            ]
            log.write_text("\n".join(json.dumps(event) for event in events) + "\n")

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=3600,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.WORKING)
            self.assertEqual(snapshot.statuses[0].event_name, "PostToolUse")

    def test_post_tool_use_does_not_stay_working_indefinitely(self) -> None:
        now = datetime.now(timezone.utc)
        status = AgentStatus(
            provider="codex",
            agent_id="codex:session:tool-session",
            display_name="tool-session",
            mode=AgentMode.WORKING,
            updated_at=now
            - timedelta(seconds=collector_module.POST_TOOL_WORKING_VISIBLE_SECONDS + 1),
            event_name="PostToolUse",
            session_id="tool-session",
            cwd="/tmp/project",
            tool_name="webrun",
        )

        snapshot = collector_module.snapshot_from_statuses(
            (status,),
            sources=(),
            collected_at=now,
            stale_after_seconds=3600,
            tool_running_timeout_seconds=0,
            completed_visible_seconds=20 * 60,
            idle_visible_seconds=0,
        )

        self.assertEqual(snapshot.aggregate.mode, AgentMode.COMPLETED)
        self.assertEqual(snapshot.aggregate.active_count, 0)
        self.assertEqual(snapshot.statuses[0].event_name, "PostToolUse")

    def test_internal_codex_helper_sessions_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            log.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "logged_at": now,
                                "event": {
                                    "hook_event_name": "UserPromptSubmit",
                                    "session_id": "codex-helper",
                                    "cwd": "/Users/example/pgit/sidepulse",
                                    "prompt": "Overview\nGenerate 0 to 3 hyperpersonalized suggestions for what this user might do.",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "logged_at": now,
                                "event": {
                                    "hook_event_name": "PostToolUse",
                                    "session_id": "codex-helper",
                                    "cwd": "/Users/example/pgit/sidepulse",
                                    "tool_name": "mcp__codex_apps__gmail__batch_read_email",
                                },
                            }
                        ),
                    ]
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=3600,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.IDLE_READY)
            self.assertEqual(snapshot.statuses, ())

    def test_codex_transcript_fallback_marks_recent_user_turn_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "aaaaaaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
            path = root / "2026" / "06" / "29" / f"rollout-2026-06-29T08-27-42-{session_id}.jsonl"
            path.parent.mkdir(parents=True)
            now = datetime.now(timezone.utc).isoformat()
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": now,
                                "type": "turn_context",
                                "payload": {
                                    "turn_id": "turn-1",
                                    "cwd": "/Users/pero/pgit/sidepulse",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": now,
                                "type": "response_item",
                                "payload": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "input_text",
                                            "text": "it didnt catch this conversation",
                                        }
                                    ],
                                },
                            }
                        ),
                    ]
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex-transcripts", root),),
                stale_after_seconds=3600,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.WORKING)
            self.assertEqual(snapshot.statuses[0].session_id, session_id)
            self.assertIn("sidepulse", snapshot.statuses[0].display_name)
            self.assertIn("it didnt catch", snapshot.statuses[0].display_name)

    def test_transcript_records_are_cached_until_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "019ee395-2f64-7cc3-b566-afcc1d626160"
            path = root / f"rollout-2026-06-29T08-27-42-{session_id}.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            path.write_text(
                json.dumps(
                    {
                        "timestamp": now,
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "exec_command",
                            "call_id": "call-1",
                            "arguments": "{}",
                        },
                    }
                )
                + "\n"
            )
            monitor = AgentMonitor(
                sources=(SourceSpec("codex-transcripts", root),),
                stale_after_seconds=3600,
            )
            calls: list[Path] = []
            original_read_recent_lines = collector_module.read_recent_lines

            def counting_read_recent_lines(read_path: Path, max_lines: int) -> list[str]:
                if read_path == path:
                    calls.append(read_path)
                return original_read_recent_lines(read_path, max_lines)

            with patch(
                "sidepulse.collector.read_recent_lines",
                side_effect=counting_read_recent_lines,
            ):
                self.assertEqual(monitor.snapshot().aggregate.mode, AgentMode.TOOL_RUNNING)
                self.assertEqual(monitor.snapshot().aggregate.mode, AgentMode.TOOL_RUNNING)
                self.assertEqual(calls, [path])

                with path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "type": "response_item",
                                "payload": {
                                    "type": "function_call_output",
                                    "call_id": "call-1",
                                    "output": "{}",
                                },
                            }
                        )
                        + "\n"
                    )

                self.assertEqual(monitor.snapshot().aggregate.mode, AgentMode.WORKING)
                self.assertEqual(calls, [path, path])

    def test_hook_log_records_are_cached_until_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            log.write_text(
                json.dumps(
                    {
                        "logged_at": now,
                        "event": {
                            "hook_event_name": "PreToolUse",
                            "session_id": "codex-session",
                            "tool_name": "Bash",
                        },
                    }
                )
                + "\n"
            )
            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=3600,
            )
            calls: list[Path] = []
            original_read_recent_lines = collector_module.read_recent_lines

            def counting_read_recent_lines(read_path: Path, max_lines: int) -> list[str]:
                if read_path == log:
                    calls.append(read_path)
                return original_read_recent_lines(read_path, max_lines)

            with patch(
                "sidepulse.collector.read_recent_lines",
                side_effect=counting_read_recent_lines,
            ):
                self.assertEqual(monitor.snapshot().aggregate.mode, AgentMode.TOOL_RUNNING)
                self.assertEqual(monitor.snapshot().aggregate.mode, AgentMode.TOOL_RUNNING)
                self.assertEqual(calls, [log])

                with log.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "logged_at": datetime.now(timezone.utc).isoformat(),
                                "event": {
                                    "hook_event_name": "PostToolUse",
                                    "session_id": "codex-session",
                                    "tool_name": "Bash",
                                },
                            }
                        )
                        + "\n"
                    )

                self.assertEqual(monitor.snapshot().aggregate.mode, AgentMode.WORKING)
                self.assertEqual(calls, [log, log])

    def test_snapshot_reuses_latest_statuses_when_inputs_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            log.write_text(
                json.dumps(
                    {
                        "logged_at": datetime.now(timezone.utc).isoformat(),
                        "event": {
                            "hook_event_name": "PreToolUse",
                            "session_id": "codex-session",
                            "tool_name": "Bash",
                        },
                    }
                )
                + "\n"
            )
            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=3600,
            )

            with patch(
                "sidepulse.collector.status_from_event",
                wraps=collector_module.status_from_event,
            ) as status_from_event:
                self.assertEqual(monitor.snapshot().aggregate.mode, AgentMode.TOOL_RUNNING)
                first_count = status_from_event.call_count
                self.assertGreater(first_count, 0)

                self.assertEqual(monitor.snapshot().aggregate.mode, AgentMode.TOOL_RUNNING)
                self.assertEqual(status_from_event.call_count, first_count)

    def test_codex_transcript_fallback_marks_tool_calls_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "019ee395-2f64-7cc3-b566-afcc1d626160"
            path = root / "rollout-2026-06-29T08-27-42-" / f"{session_id}.jsonl"
            path.parent.mkdir(parents=True)
            now = datetime.now(timezone.utc).isoformat()
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": now,
                                "type": "turn_context",
                                "payload": {
                                    "turn_id": "turn-1",
                                    "cwd": "/tmp/project",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": now,
                                "type": "response_item",
                                "payload": {
                                    "type": "function_call",
                                    "name": "exec_command",
                                    "call_id": "call-1",
                                    "arguments": "{}",
                                },
                            }
                        ),
                    ]
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex-transcripts", root),),
                stale_after_seconds=3600,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.TOOL_RUNNING)
            self.assertEqual(snapshot.statuses[0].tool_name, "exec_command")

    def test_codex_transcript_task_complete_overrides_last_tool_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "019f179b-7fdc-7eb0-a3af-1ca3eb128eee"
            path = root / f"rollout-2026-06-30T01-18-14-{session_id}.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": now,
                                "type": "turn_context",
                                "payload": {"cwd": "/tmp/project"},
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": now,
                                "type": "response_item",
                                "payload": {
                                    "type": "function_call_output",
                                    "call_id": "call-1",
                                    "output": "ok",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": now,
                                "type": "event_msg",
                                "payload": {
                                    "type": "task_complete",
                                    "last_agent_message": "All set.",
                                },
                            }
                        ),
                    ]
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex-transcripts", root),),
                stale_after_seconds=3600,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.COMPLETED)
            self.assertEqual(snapshot.statuses[0].event_name, "Stop")

    def test_claude_transcript_fallback_marks_tool_calls_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "e289f361-f64f-415e-8dd3-01ed835f7869"
            path = root / "-Users-pero-pgit-sdrgb" / f"{session_id}.jsonl"
            path.parent.mkdir(parents=True)
            now = datetime.now(timezone.utc).isoformat()
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": now,
                                "type": "user",
                                "sessionId": session_id,
                                "cwd": "/Users/pero/pgit/sdrgb",
                                "message": {
                                    "role": "user",
                                    "content": "make a pull request",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": now,
                                "type": "assistant",
                                "sessionId": session_id,
                                "cwd": "/Users/pero/pgit/sdrgb",
                                "message": {
                                    "role": "assistant",
                                    "stop_reason": "tool_use",
                                    "content": [
                                        {
                                            "type": "tool_use",
                                            "id": "toolu_1",
                                            "name": "Edit",
                                            "input": {"file_path": "README.md"},
                                        }
                                    ],
                                },
                            }
                        ),
                    ]
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("claude-transcripts", root),),
                stale_after_seconds=3600,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.TOOL_RUNNING)
            self.assertEqual(snapshot.statuses[0].provider, "claude")
            self.assertEqual(snapshot.statuses[0].tool_name, "Edit")

    def test_claude_transcript_mtime_extends_active_file_activity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "e289f361-f64f-415e-8dd3-01ed835f7869"
            path = root / f"{session_id}.jsonl"
            old = datetime.now(timezone.utc) - timedelta(minutes=25)
            now = datetime.now(timezone.utc)
            path.write_text(
                json.dumps(
                    {
                        "timestamp": old.isoformat(),
                        "type": "user",
                        "sessionId": session_id,
                        "cwd": "/Users/pero/pgit/sdrgb",
                        "message": {
                            "role": "user",
                            "content": "keep going",
                        },
                    }
                )
                + "\n"
            )
            os.utime(path, (now.timestamp(), now.timestamp()))

            monitor = AgentMonitor(
                sources=(SourceSpec("claude-transcripts", root),),
                stale_after_seconds=3600,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.WORKING)
            self.assertEqual(snapshot.statuses[0].event_name, "Notification")

    def test_claude_transcript_mtime_does_not_resurrect_completed_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "e289f361-f64f-415e-8dd3-01ed835f7869"
            path = root / f"{session_id}.jsonl"
            old = datetime.now(timezone.utc) - timedelta(minutes=25)
            now = datetime.now(timezone.utc)
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": old.isoformat(),
                                "type": "user",
                                "sessionId": session_id,
                                "cwd": "/Users/pero/pgit/sdrgb",
                                "message": {
                                    "role": "user",
                                    "content": "it's ok, done",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": (old + timedelta(seconds=10)).isoformat(),
                                "type": "assistant",
                                "sessionId": session_id,
                                "cwd": "/Users/pero/pgit/sdrgb",
                                "message": {
                                    "role": "assistant",
                                    "stop_reason": "end_turn",
                                    "content": [{"type": "text", "text": "Great, thanks for handling it."}],
                                },
                            }
                        ),
                    ]
                )
                + "\n"
            )
            os.utime(path, (now.timestamp(), now.timestamp()))

            monitor = AgentMonitor(
                sources=(SourceSpec("claude-transcripts", root),),
                stale_after_seconds=3600,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.IDLE_READY)
            self.assertEqual(snapshot.statuses, ())
            self.assertEqual(snapshot.stale_statuses[0].mode, AgentMode.COMPLETED)

    def test_final_question_maps_to_waiting_for_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            log.write_text(
                json.dumps(
                    {
                        "logged_at": "2026-06-20T06:00:00Z",
                        "event": {
                            "hook_event_name": "Stop",
                            "session_id": "codex-session",
                            "last_assistant_message": "Which mode do you see now?",
                        },
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=999999999,
                tool_running_timeout_seconds=0,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.WAITING_FOR_INPUT)

    def test_anything_else_prompt_maps_to_completed_before_recaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            log.write_text(
                json.dumps(
                    {
                        "logged_at": now,
                        "event": {
                            "hook_event_name": "Stop",
                            "session_id": "codex-session",
                            "last_assistant_message": "\n".join(
                                [
                                    "Anything else you want to tweak?",
                                    "",
                                    "* Cogitated for 40s - 1 shell still running",
                                    "※ recap: We built and deployed the SidePulse Pro/SidePulse Dot product status.",
                                ]
                            ),
                        },
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=999999999,
                tool_running_timeout_seconds=0,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.COMPLETED)

    def test_concrete_followup_question_maps_to_waiting_for_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "claude.jsonl"
            log.write_text(
                json.dumps(
                    {
                        "logged_at": "2026-06-20T06:00:00Z",
                        "hook_event_name": "Stop",
                        "session_id": "claude-session",
                        "last_assistant_message": (
                            "Committed as `67b0208` but not pushed. "
                            "Want me to push?"
                        ),
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("claude", log),),
                stale_after_seconds=999999999,
                tool_running_timeout_seconds=0,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.WAITING_FOR_INPUT)

    def test_question_examples_in_inline_code_do_not_map_to_waiting_for_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            log.write_text(
                json.dumps(
                    {
                        "logged_at": now,
                        "event": {
                            "hook_event_name": "Stop",
                            "session_id": "codex-session",
                            "last_assistant_message": "\n".join(
                                [
                                    "Now:",
                                    "- `Committed but not pushed. Want me to push?` => `Ask`",
                                    "- `Which mode do you see now?` => `Ask`",
                                    "",
                                    "Verified: `42` tests pass.",
                                ]
                            ),
                        },
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=999999999,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.COMPLETED)

    def test_real_question_with_inline_code_maps_to_waiting_for_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            log.write_text(
                json.dumps(
                    {
                        "logged_at": "2026-06-20T06:00:00Z",
                        "event": {
                            "hook_event_name": "Stop",
                            "session_id": "codex-session",
                            "last_assistant_message": "Want me to run `git push`?",
                        },
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=999999999,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.WAITING_FOR_INPUT)

    def test_answer_heading_does_not_map_to_waiting_for_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            log.write_text(
                json.dumps(
                    {
                        "logged_at": now,
                        "event": {
                            "hook_event_name": "Stop",
                            "session_id": "codex-session",
                            "last_assistant_message": "\n".join(
                                [
                                    "No. Nothing in this payload exposes live XYZ.",
                                    "",
                                    "What we can infer from this:",
                                    "",
                                    "- MQTT print status is useful for uploaded jobs.",
                                ]
                            ),
                        },
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=999999999,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.COMPLETED)

    def test_explicit_sidepulse_marker_maps_to_waiting_for_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            log.write_text(
                json.dumps(
                    {
                        "logged_at": "2026-06-20T06:00:00Z",
                        "event": {
                            "hook_event_name": "Stop",
                            "session_id": "codex-session",
                            "last_assistant_message": "I need your choice.\n<!-- sidepulse:ask -->",
                        },
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=999999999,
                tool_running_timeout_seconds=0,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.WAITING_FOR_INPUT)

    def test_explicit_sidepulse_marker_overrides_question_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            log.write_text(
                json.dumps(
                    {
                        "logged_at": now,
                        "event": {
                            "hook_event_name": "Stop",
                            "session_id": "codex-session",
                            "last_assistant_message": "Anything else to tweak?\n<!-- sidepulse:done -->",
                        },
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=999999999,
                tool_running_timeout_seconds=0,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.COMPLETED)

    def test_explicit_sidepulse_field_maps_to_waiting_for_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "claude.jsonl"
            log.write_text(
                json.dumps(
                    {
                        "logged_at": "2026-06-20T06:00:00Z",
                        "hook_event_name": "Stop",
                        "session_id": "claude-session",
                        "last_assistant_message": "Done-ish.",
                        "sidepulse_status": "ask",
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("claude", log),),
                stale_after_seconds=999999999,
                tool_running_timeout_seconds=0,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.WAITING_FOR_INPUT)

    def test_explicit_marker_inside_code_block_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            log.write_text(
                json.dumps(
                    {
                        "logged_at": now,
                        "event": {
                            "hook_event_name": "Stop",
                            "session_id": "codex-session",
                            "last_assistant_message": "Use:\n```text\n<!-- sidepulse:ask -->\n```",
                        },
                    }
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=999999999,
                tool_running_timeout_seconds=0,
            )
            snapshot = monitor.snapshot()

            self.assertEqual(snapshot.aggregate.mode, AgentMode.COMPLETED)

    def test_session_display_name_uses_prompt_context_after_later_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "codex.jsonl"
            session_id = "dddddddd-eeee-7fff-8aaa-bbbbbbbbbbbb"
            prompt = """
# Files mentioned by the user:

## codex-clipboard.png: /var/folders/tmp/codex-clipboard.png

## My request for Codex:
team id YOUR_TEAM_ID, push key '/path/to/AuthKey_YOUR_KEY_ID.p8'
"""
            log.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "logged_at": "2026-06-22T19:50:58Z",
                                "event": {
                                    "hook_event_name": "UserPromptSubmit",
                                    "session_id": session_id,
                                    "cwd": "/Users/pero/pgit/sidepulse",
                                    "prompt": prompt,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "logged_at": "2026-06-22T19:51:09Z",
                                "event": {
                                    "hook_event_name": "PreToolUse",
                                    "session_id": session_id,
                                    "cwd": "/Users/pero/pgit/sidepulse",
                                    "tool_name": "Bash",
                                },
                            }
                        ),
                    ]
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("codex", log),),
                stale_after_seconds=999999999,
                tool_running_timeout_seconds=0,
            )
            snapshot = monitor.snapshot()
            status = snapshot.statuses[0]

            self.assertIn("sidepulse", status.display_name)
            self.assertIn("team id YOUR_TEAM_ID", status.display_name)
            self.assertIn(session_id[:8], status.display_name)
            self.assertNotIn("/Users/example", status.display_name)

    def test_codex_display_name_uses_session_index_thread_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            codex_home = home / ".codex"
            codex_home.mkdir(parents=True)
            session_id = "bbbbbbbb-cccc-7ddd-8eee-ffffffffffff"
            (codex_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": session_id,
                        "thread_name": "Refine README agent status modes",
                        "updated_at": "2026-06-20T05:52:21.985091Z",
                    }
                )
                + "\n"
            )
            log = base / "codex.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            log.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "logged_at": now,
                                "event": {
                                    "hook_event_name": "UserPromptSubmit",
                                    "session_id": session_id,
                                    "cwd": "/Users/pero/pgit/sidepulse",
                                    "prompt": "Why are we burning so much CPU",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "logged_at": now,
                                "event": {
                                    "hook_event_name": "PreToolUse",
                                    "session_id": session_id,
                                    "cwd": "/Users/pero/pgit/sidepulse",
                                    "tool_name": "Bash",
                                },
                            }
                        ),
                    ]
                )
                + "\n"
            )

            with patch("sidepulse.collector.Path.home", return_value=home):
                monitor = AgentMonitor(
                    sources=(SourceSpec("codex", log),),
                    stale_after_seconds=999999999,
                )
                snapshot = monitor.snapshot()

            name = snapshot.statuses[0].display_name
            self.assertIn("sidepulse", name)
            self.assertIn("Refine README agent status modes", name)
            self.assertNotIn("Why are we burning", name)

    def test_live_monitor_refreshes_loaded_codex_display_name_from_session_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            codex_home = home / ".codex"
            codex_home.mkdir(parents=True)
            session_id = "cccccccc-dddd-7eee-8fff-aaaaaaaaaaaa"
            (codex_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": session_id,
                        "thread_name": "Refine README agent status modes",
                    }
                )
                + "\n"
            )
            latest = base / "latest.json"
            now = datetime.now(timezone.utc)
            latest.write_text(
                json.dumps(
                    {
                        "updated_at": now.isoformat(),
                        "statuses": [
                            {
                                "provider": "codex",
                                "agent_id": f"codex:session:{session_id}",
                                "display_name": (
                                    "sidepulse: Why are we burning so much CPU "
                                    f"({session_id[:8]})"
                                ),
                                "mode": "working",
                                "updated_at": now.isoformat(),
                                "event_name": "UserPromptSubmit",
                                "session_id": session_id,
                                "cwd": "/Users/pero/pgit/sidepulse",
                            }
                        ],
                    }
                )
                + "\n"
            )

            with patch("sidepulse.collector.Path.home", return_value=home):
                monitor = LiveAgentMonitor(latest_state_path=latest)
                snapshot = monitor.snapshot()

            name = snapshot.statuses[0].display_name
            self.assertIn("Refine README agent status modes", name)
            self.assertNotIn("Why are we burning", name)

    def test_task_notification_does_not_replace_session_display_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "claude.jsonl"
            session_id = "1ca4348e-2aec-4147-9e81-d7d56364d257"
            log.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "logged_at": "2026-06-22T07:20:00Z",
                                "hook_event_name": "UserPromptSubmit",
                                "session_id": session_id,
                                "cwd": "/Users/pero/pgit/sdstatus_bitbang",
                                "prompt": "convert these videos to mp4",
                            }
                        ),
                        json.dumps(
                            {
                                "logged_at": "2026-06-22T07:24:05Z",
                                "hook_event_name": "UserPromptSubmit",
                                "session_id": session_id,
                                "cwd": "/Users/pero/pgit/sdstatus_bitbang",
                                "prompt": "<task-notification><status>completed</status></task-notification>",
                            }
                        ),
                    ]
                )
                + "\n"
            )

            monitor = AgentMonitor(
                sources=(SourceSpec("claude", log),),
                stale_after_seconds=999999999,
            )
            snapshot = monitor.snapshot()

            self.assertIn("convert these videos", snapshot.statuses[0].display_name)
            self.assertNotIn("task-notification", snapshot.statuses[0].display_name)

    def test_codex_session_actions_build_deeplink_and_resume_command(self) -> None:
        status = AgentStatus(
            provider="codex",
            agent_id="codex:session:abc",
            display_name="Codex abc",
            mode=AgentMode.WORKING,
            updated_at=datetime.now(timezone.utc),
            event_name="PreToolUse",
            session_id="019ee395-2f64-7cc3-b566-afcc1d626160",
            cwd="/tmp/project with spaces",
        )

        self.assertEqual(
            session_deep_link(status),
            "codex://threads/019ee395-2f64-7cc3-b566-afcc1d626160",
        )
        self.assertEqual(
            session_resume_command(status),
            "cd '/tmp/project with spaces' && codex resume 019ee395-2f64-7cc3-b566-afcc1d626160",
        )

    def test_session_default_open_action_follows_origin(self) -> None:
        def status_for(provider: str, origin: str) -> AgentStatus:
            return AgentStatus(
                provider=provider,
                agent_id=f"{provider}:session:abc",
                display_name=f"{provider} abc",
                mode=AgentMode.WORKING,
                updated_at=datetime.now(timezone.utc),
                event_name="PreToolUse",
                session_id="1ca4348e-2aec-4147-9e81-d7d56364d257",
                cwd="/Users/pero/pgit/example",
                origin=origin,
            )

        self.assertEqual(
            default_session_open_action(status_for("claude", "Claude in VS Code")),
            SESSION_OPEN_VSCODE,
        )
        self.assertEqual(
            default_session_open_action(status_for("claude", "Claude Code CLI")),
            SESSION_OPEN_TERMINAL,
        )
        self.assertEqual(
            default_session_open_action(status_for("claude", "Claude App")),
            SESSION_OPEN_APP,
        )
        self.assertEqual(
            default_session_open_action(status_for("codex", "Codex CLI")),
            SESSION_OPEN_TERMINAL,
        )
        self.assertEqual(
            default_session_open_action(status_for("codex", "Codex UI")),
            SESSION_OPEN_APP,
        )

    def test_claude_session_actions_build_app_link_and_resume_command(self) -> None:
        status = AgentStatus(
            provider="claude",
            agent_id="claude:session:abc",
            display_name="Claude abc",
            mode=AgentMode.WAITING_FOR_INPUT,
            updated_at=datetime.now(timezone.utc),
            event_name="Notification",
            session_id="1ca4348e-2aec-4147-9e81-d7d56364d257",
            cwd="/Users/pero/pgit/sdstatus_bitbang",
        )

        self.assertEqual(session_deep_link(status), "claude://")
        self.assertEqual(
            session_resume_command(status),
            "cd /Users/pero/pgit/sdstatus_bitbang && claude --resume 1ca4348e-2aec-4147-9e81-d7d56364d257",
        )
        self.assertEqual(
            session_vscode_link(status),
            "vscode://anthropic.claude-code/open?session=1ca4348e-2aec-4147-9e81-d7d56364d257",
        )
        self.assertEqual(default_session_open_action(status), "vscode")
        self.assertEqual(
            session_open_target(status, "vscode"),
            (
                "url",
                "vscode://anthropic.claude-code/open?session=1ca4348e-2aec-4147-9e81-d7d56364d257",
            ),
        )

    def test_devin_session_actions_build_terminal_resume_command(self) -> None:
        status = AgentStatus(
            provider="devin",
            agent_id="devin:session:abc",
            display_name="Devin abc",
            mode=AgentMode.WORKING,
            updated_at=datetime.now(timezone.utc),
            event_name="PreToolUse",
            session_id="devin-session-123",
            cwd="/tmp/project with spaces",
        )

        command = "cd '/tmp/project with spaces' && devin --resume devin-session-123"
        self.assertEqual(session_resume_command(status), command)
        self.assertEqual(session_open_target(status, SESSION_OPEN_TERMINAL), ("terminal", command))

    def test_session_opener_providers_follow_hook_registry(self) -> None:
        self.assertEqual(provider_session_opener_providers(), HOOK_PROVIDERS)


def _status(provider: str, mode: AgentMode, *, when: datetime | None = None) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=provider,
        display_name=provider.title(),
        mode=mode,
        updated_at=when or datetime.now(timezone.utc),
        event_name="Test",
    )


class ColorSettingsTests(unittest.TestCase):
    def test_defaults_seed_mode_colors_from_led_status_constants(self) -> None:
        from sidepulse.led_status import ASK_AMBER, DONE_GREEN, IDLE_DIM, WORKING_CYAN

        defaults = ColorSettings.defaults()
        self.assertEqual(defaults.mode_color(colors_module.MODE_IDLE), IDLE_DIM)
        self.assertEqual(defaults.mode_color(colors_module.MODE_WORKING), WORKING_CYAN)
        self.assertEqual(defaults.mode_color(colors_module.MODE_DONE), DONE_GREEN)
        self.assertEqual(defaults.mode_color(colors_module.MODE_ASK), ASK_AMBER)

    def test_default_agent_color_is_deterministic(self) -> None:
        # Registered providers get their brand color (see PROVIDER_BRAND_COLORS
        # tests below); this just checks stability/determinism generically.
        for spec in PROVIDER_SPECS:
            self.assertEqual(default_agent_color(spec.provider), default_agent_color(spec.provider))
        # unknown provider still resolves deterministically (same input -> same output)
        self.assertEqual(default_agent_color("future-provider"), default_agent_color("future-provider"))

    def test_color_settings_json_round_trip(self) -> None:
        settings = (
            ColorSettings.defaults()
            .with_agent_color("codex", "#123456")
            .with_mode_color(colors_module.MODE_ASK, "#abcdef")
            .with_blend_mode(BLEND_MODE_COLOR)
        )
        restored = ColorSettings.from_dict(settings.to_dict())
        self.assertEqual(restored.agent_color("codex"), "#123456")
        self.assertEqual(restored.mode_color(colors_module.MODE_ASK), "#ABCDEF")
        self.assertEqual(restored.blend_mode, BLEND_MODE_COLOR)

    def test_color_settings_from_dict_rejects_malformed_input_without_raising(self) -> None:
        self.assertEqual(ColorSettings.from_dict(None).to_dict(), ColorSettings.defaults().to_dict())
        self.assertEqual(ColorSettings.from_dict("not-a-dict").to_dict(), ColorSettings.defaults().to_dict())
        malformed = ColorSettings.from_dict(
            {"mode_colors": "nope", "agent_colors": ["bad"], "blend_mode": "nonsense"}
        )
        self.assertEqual(malformed.to_dict(), ColorSettings.defaults().to_dict())

    def test_with_agent_color_rejects_bad_hex_and_keeps_default(self) -> None:
        settings = ColorSettings.defaults().with_agent_color("codex", "not-a-color")
        self.assertEqual(settings.agent_color("codex"), default_agent_color("codex"))

    def test_normalize_hex_accepts_and_rejects_expected_shapes(self) -> None:
        self.assertEqual(normalize_hex("#3aa0ff", "#000000"), "#3AA0FF")
        self.assertEqual(normalize_hex("3aa0ff", "#000000"), "#3AA0FF")
        self.assertEqual(normalize_hex("#fff", "#000000"), "#000000")
        self.assertEqual(normalize_hex("not-a-color", "#000000"), "#000000")
        self.assertEqual(normalize_hex(None, "#000000"), "#000000")

    def test_urgency_weight_orders_blocked_above_idle(self) -> None:
        self.assertGreater(urgency_weight(AgentMode.BLOCKED_ERROR), urgency_weight(AgentMode.WAITING_FOR_INPUT))
        self.assertGreater(urgency_weight(AgentMode.WAITING_FOR_INPUT), urgency_weight(AgentMode.WORKING))
        self.assertGreater(urgency_weight(AgentMode.WORKING), urgency_weight(AgentMode.IDLE_READY))
        self.assertEqual(urgency_weight(AgentMode.UNKNOWN), 1)

    def test_program_for_snapshot_empty_statuses_uses_fallback_mode(self) -> None:
        settings = ColorSettings.defaults()
        state, program = program_for_snapshot((), led_count=8, colors=settings)
        self.assertEqual(state, LedDisplayState.IDLE)

        state, program = program_for_snapshot(
            (), led_count=8, colors=settings, fallback_mode=AgentMode.BLOCKED_ERROR
        )
        self.assertEqual(state, LedDisplayState.ASK)
        # The default fade ceiling (50%) scales the configured Ask color down
        # for the pulse's peak, so the raw configured hex won't appear
        # verbatim -- the scaled peak color should.
        floor, ceiling = settings.fade_range(colors_module.MODE_ASK)
        self.assertEqual(ceiling, colors_module.DEFAULT_FADE_CEILING)
        peak = colors_module.scale_hex_brightness(settings.mode_color(colors_module.MODE_ASK), ceiling)
        self.assertIn(peak, program)

    def test_classic_blend_mode_matches_program_for_display_state_exactly(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_CLASSIC)
        fade_kwargs = {
            "idle_floor": colors_module.DEFAULT_FADE_FLOOR,
            "idle_ceiling": colors_module.DEFAULT_FADE_CEILING,
            "working_floor": colors_module.DEFAULT_FADE_FLOOR,
            "working_ceiling": colors_module.DEFAULT_FADE_CEILING,
            "ask_floor": colors_module.DEFAULT_FADE_FLOOR,
            "ask_ceiling": colors_module.DEFAULT_FADE_CEILING,
        }
        for mode, expected_state in (
            (AgentMode.WORKING, LedDisplayState.WORKING),
            (AgentMode.BLOCKED_ERROR, LedDisplayState.ASK),
            (AgentMode.COMPLETED, LedDisplayState.DONE),
            (AgentMode.IDLE_READY, LedDisplayState.IDLE),
        ):
            for led_count in (2, 8):
                state, program = program_for_snapshot(
                    (_status("codex", mode),), led_count=led_count, colors=settings
                )
                expected = program_for_display_state(
                    expected_state,
                    led_count=led_count,
                    brightness=255,
                    done_celebrate=settings.done_celebration_enabled,
                    **fade_kwargs,
                )
                self.assertEqual(state, expected_state)
                self.assertEqual(program, expected)

    def test_classic_blend_mode_matches_default_off_to_full_pulse_when_fade_disabled(self) -> None:
        # Confirms the underlying primitive (program_for_display_state) is
        # still exactly today's original off-to-full pulse when floor=0/
        # ceiling=1 -- i.e. the gentler default lives in ColorSettings, not
        # baked irreversibly into the renderer.
        settings = (
            ColorSettings.defaults()
            .with_blend_mode(BLEND_MODE_CLASSIC)
        )
        for key in colors_module.FADE_MODE_KEYS:
            settings = settings.with_fade_floor(key, 0.0).with_fade_ceiling(key, 1.0)

        for mode, expected_state in (
            (AgentMode.WORKING, LedDisplayState.WORKING),
            (AgentMode.BLOCKED_ERROR, LedDisplayState.ASK),
            (AgentMode.IDLE_READY, LedDisplayState.IDLE),
        ):
            state, program = program_for_snapshot((_status("codex", mode),), led_count=8, colors=settings)
            expected = program_for_display_state(expected_state, led_count=8, brightness=255)
            self.assertEqual(program, expected)

    def test_spatial_split_assigns_every_led_exactly_once_across_agent_counts(self) -> None:
        settings = ColorSettings.defaults()
        modes = [
            AgentMode.BLOCKED_ERROR,
            AgentMode.WAITING_FOR_INPUT,
            AgentMode.TOOL_RUNNING,
            AgentMode.WORKING,
            AgentMode.IDLE_READY,
        ]
        for agent_count in range(1, len(modes) + 1):
            statuses = tuple(
                _status(f"agent{i}", modes[i]) for i in range(agent_count)
            )
            agents = colors_module._active_agents(statuses, settings)
            blocks = colors_module._spatial_split_blocks(agents, 8)
            self.assertEqual(sum(count for _, count in blocks), 8)
            self.assertTrue(all(count >= 1 for _, count in blocks))

    def test_spatial_split_falls_back_to_color_blend_when_agents_exceed_leds(self) -> None:
        settings = ColorSettings.defaults()
        statuses = (
            _status("codex", AgentMode.BLOCKED_ERROR),
            _status("claude", AgentMode.WORKING),
            _status("devin", AgentMode.IDLE_READY),
        )
        state, program = program_for_snapshot(statuses, led_count=2, colors=settings)
        # Falls through to a single blended color across the 2-LED Dot rather
        # than a spatial per-index assignment (which would need indices 0-2).
        self.assertNotIn("2:", program)
        self.assertEqual(state, LedDisplayState.ASK)

    def test_color_blend_mode_produces_weighted_average(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_COLOR)
        statuses = (_status("codex", AgentMode.WORKING), _status("claude", AgentMode.WORKING))
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        blended = colors_module.weighted_blend(
            [
                (settings.agent_color("codex"), float(urgency_weight(AgentMode.WORKING))),
                (settings.agent_color("claude"), float(urgency_weight(AgentMode.WORKING))),
            ]
        )
        # WORKING pulses between its fade floor/ceiling of the blended color,
        # not the raw blended color at full brightness.
        _, ceiling = settings.fade_range(colors_module.MODE_WORKING)
        expected = colors_module.scale_hex_brightness(blended, ceiling)
        self.assertIn(expected, program)

    def test_cycle_mode_lists_every_active_agent_color(self) -> None:
        # Claude is Waiting for Input here (an Ask state), so with the
        # urgency alert on (the default) its own color is swapped for the
        # Ask mode color -- verified separately below. Use Working/Idle so
        # this test is purely about "every agent's own color appears."
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_CYCLE)
        statuses = (_status("codex", AgentMode.WORKING), _status("claude", AgentMode.IDLE_READY))
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        _floor, codex_ceiling = settings.fade_range(colors_module.MODE_WORKING)
        _floor, claude_ceiling = settings.fade_range(colors_module.MODE_IDLE)
        self.assertIn(colors_module.scale_hex_brightness(settings.agent_color("codex"), codex_ceiling), program)
        self.assertIn(colors_module.scale_hex_brightness(settings.agent_color("claude"), claude_ceiling), program)
        self.assertIn("repeat", program)
        self.assertIn("pulse", program)

    def test_all_blend_modes_produce_valid_dsl_line_and_byte_limits(self) -> None:
        settings_base = ColorSettings.defaults()
        statuses = (
            _status("codex", AgentMode.BLOCKED_ERROR),
            _status("claude", AgentMode.WORKING),
            _status("devin", AgentMode.IDLE_READY),
            _status("grok", AgentMode.COMPLETED),
        )
        for blend_mode in BLEND_MODE_CHOICES:
            settings = settings_base.with_blend_mode(blend_mode)
            for led_count in (2, 8):
                _, program = program_for_snapshot(statuses, led_count=led_count, colors=settings)
                lines = [line for line in program.splitlines() if line.strip()]
                self.assertLessEqual(len(lines), 20, f"{blend_mode}/{led_count}: too many lines")
                self.assertLessEqual(len(program.encode()), 512, f"{blend_mode}/{led_count}: too many bytes")

    def test_scale_hex_brightness_preserves_hue_scales_channels(self) -> None:
        from sidepulse.led_status import scale_hex_brightness

        self.assertEqual(scale_hex_brightness("#00E5FF", 1.0), "#00E5FF")
        self.assertEqual(scale_hex_brightness("#00E5FF", 0.0), "#000000")
        self.assertEqual(scale_hex_brightness("#00E5FF", 0.5), "#007280")
        # out-of-range fractions are clamped, not rejected
        self.assertEqual(scale_hex_brightness("#00E5FF", 2.0), "#00E5FF")
        self.assertEqual(scale_hex_brightness("#00E5FF", -1.0), "#000000")

    def test_default_fade_range_is_gentler_than_off_to_full(self) -> None:
        settings = ColorSettings.defaults()
        for key in colors_module.FADE_MODE_KEYS:
            floor, ceiling = settings.fade_range(key)
            self.assertEqual(floor, colors_module.DEFAULT_FADE_FLOOR)
            self.assertEqual(ceiling, colors_module.DEFAULT_FADE_CEILING)
        # Done doesn't pulse -- always reports the "no fade" range regardless
        # of what's stored (there's nothing stored for it anyway).
        self.assertEqual(settings.fade_range(colors_module.MODE_DONE), (0.0, 1.0))

    def test_fade_range_swaps_inverted_floor_and_ceiling(self) -> None:
        settings = (
            ColorSettings.defaults()
            .with_fade_floor(colors_module.MODE_WORKING, 0.9)
            .with_fade_ceiling(colors_module.MODE_WORKING, 0.2)
        )
        floor, ceiling = settings.fade_range(colors_module.MODE_WORKING)
        self.assertEqual((floor, ceiling), (0.2, 0.9))

    def test_fade_floor_and_ceiling_are_clamped_to_0_1(self) -> None:
        settings = (
            ColorSettings.defaults()
            .with_fade_floor(colors_module.MODE_ASK, 5.0)
            .with_fade_ceiling(colors_module.MODE_IDLE, -3.0)
        )
        # Floor clamps to 1.0, which then exceeds the still-default 0.5
        # ceiling -- fade_range() swaps them back into a valid (low, high)
        # order rather than returning an inverted range.
        self.assertEqual(settings.fade_range(colors_module.MODE_ASK), (colors_module.DEFAULT_FADE_CEILING, 1.0))
        self.assertEqual(settings.fade_range(colors_module.MODE_IDLE), (0.0, colors_module.DEFAULT_FADE_FLOOR))

    def test_with_fade_floor_rejects_non_pulsing_mode(self) -> None:
        with self.assertRaises(ValueError):
            ColorSettings.defaults().with_fade_floor(colors_module.MODE_DONE, 0.1)

    def test_fade_settings_json_round_trip(self) -> None:
        settings = (
            ColorSettings.defaults()
            .with_fade_floor(colors_module.MODE_ASK, 0.05)
            .with_fade_ceiling(colors_module.MODE_ASK, 0.8)
        )
        restored = ColorSettings.from_dict(settings.to_dict())
        self.assertEqual(restored.fade_range(colors_module.MODE_ASK), (0.05, 0.8))

    def test_fade_settings_from_dict_rejects_malformed_input(self) -> None:
        restored = ColorSettings.from_dict({"fade_floor": "nope", "fade_ceiling": ["also nope"]})
        self.assertEqual(restored.fade_floor, ColorSettings.defaults().fade_floor)
        self.assertEqual(restored.fade_ceiling, ColorSettings.defaults().fade_ceiling)

    def test_spatial_split_reset_segment_uses_each_agents_own_floor(self) -> None:
        settings = (
            ColorSettings.defaults()
            .with_fade_floor(colors_module.MODE_ASK, 0.0)
            .with_fade_floor(colors_module.MODE_WORKING, 0.2)
        )
        statuses = (_status("devin", AgentMode.BLOCKED_ERROR), _status("codex", AgentMode.WORKING))
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        reset_line = program.splitlines()[0]
        # Devin (Ask, floor 0) resets to literal "off"; Codex (Working, floor
        # 0.2) resets to a scaled, non-off color.
        self.assertIn("off", reset_line)
        working_floor_color = colors_module.scale_hex_brightness(settings.agent_color("codex"), 0.2)
        self.assertIn(working_floor_color, reset_line)


class AgentLayoutStabilizerTests(unittest.TestCase):
    def _clock(self):
        state = {"t": 0.0}

        def now() -> float:
            return state["t"]

        def advance(dt: float) -> None:
            state["t"] += dt

        now.advance = advance  # type: ignore[attr-defined]
        return now

    def test_first_layout_commits_immediately(self) -> None:
        clock = self._clock()
        stabilizer = AgentLayoutStabilizer(clock=clock)
        layout = (_status("codex", AgentMode.WORKING),)
        result = stabilizer.stabilize(layout)
        self.assertEqual([status.provider for status in result], ["codex"])

    def test_reshuffle_is_debounced_until_it_holds(self) -> None:
        clock = self._clock()
        stabilizer = AgentLayoutStabilizer(clock=clock, debounce_seconds=1.5)
        a = (_status("codex", AgentMode.WORKING),)
        b = (_status("codex", AgentMode.WORKING), _status("devin", AgentMode.BLOCKED_ERROR))

        stabilizer.stabilize(a)
        result = stabilizer.stabilize(b)
        self.assertEqual([s.provider for s in result], ["codex"], "should not reshuffle immediately")

        clock.advance(1.0)
        result = stabilizer.stabilize(b)
        self.assertEqual([s.provider for s in result], ["codex"], "still within debounce window")

        clock.advance(0.6)
        result = stabilizer.stabilize(b)
        self.assertEqual(
            [s.provider for s in result], ["codex", "devin"], "should commit after debounce window elapses"
        )

    def test_brief_blip_and_revert_within_window_does_not_commit(self) -> None:
        clock = self._clock()
        stabilizer = AgentLayoutStabilizer(clock=clock, debounce_seconds=1.5)
        a = (_status("codex", AgentMode.WORKING),)
        b = (_status("codex", AgentMode.WORKING), _status("devin", AgentMode.BLOCKED_ERROR))

        stabilizer.stabilize(a)
        stabilizer.stabilize(b)
        clock.advance(1.5)
        stabilizer.stabilize(b)
        result_after_commit = stabilizer.stabilize(b)
        self.assertEqual([s.provider for s in result_after_commit], ["codex", "devin"])

        # blip back to `a`, then straight back to `b` -- should not have
        # reshuffled away from the already-committed `b` in between.
        stabilizer.stabilize(a)
        clock.advance(0.3)
        result = stabilizer.stabilize(b)
        self.assertEqual([s.provider for s in result], ["codex", "devin"])


class RoundRobinAndPaletteTests(unittest.TestCase):
    def test_default_blend_mode_is_round_robin(self) -> None:
        self.assertEqual(ColorSettings.defaults().blend_mode, BLEND_MODE_ROUND_ROBIN)

    def test_curated_palette_skips_the_blue_adjacent_cluster(self) -> None:
        # Confirmed live: a teal agent color was mistaken for green next to
        # a blue one. Guard against ever reintroducing teal/cyan/indigo,
        # which sit in the same crowded hue region as blue.
        near_blue_hues = {"#30B0C7", "#32ADE6", "#5856D6", "#3AD6C9", "#3AA0FF", "#7A5CFF"}
        for color in CURATED_PALETTE:
            self.assertNotIn(color.upper(), {h.upper() for h in near_blue_hues})

    def test_default_agent_colors_match_each_providers_brand(self) -> None:
        # Explicit brand-color request: Codex blue, Claude orange (terracotta,
        # Anthropic's real documented brand color), Grok grey, Devin a deep
        # blue -- not the generic maximally-distinct palette.
        self.assertEqual(default_agent_color("codex"), colors_module.PROVIDER_BRAND_COLORS["codex"])
        self.assertEqual(default_agent_color("claude"), colors_module.PROVIDER_BRAND_COLORS["claude"])
        self.assertEqual(default_agent_color("devin"), colors_module.PROVIDER_BRAND_COLORS["devin"])
        self.assertEqual(default_agent_color("grok"), colors_module.PROVIDER_BRAND_COLORS["grok"])

    def test_brand_colors_all_four_are_pairwise_distinct(self) -> None:
        values = list(colors_module.PROVIDER_BRAND_COLORS.values())
        self.assertEqual(len(values), len(set(v.upper() for v in values)))

    def test_unknown_future_provider_still_falls_back_to_curated_palette(self) -> None:
        # A provider with no brand-color entry still gets a deterministic,
        # distinct color rather than erroring or colliding.
        color = default_agent_color("some-future-provider")
        self.assertIn(color, CURATED_PALETTE)

    def test_round_robin_repeats_agent_sequence_across_every_led(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_ROUND_ROBIN)
        statuses = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.WORKING),
            _status("devin", AgentMode.WORKING),
        )
        preview = colors_module.preview_led_colors(statuses, led_count=8, colors=settings)
        expected_agents = ["codex", "claude", "devin"]
        expected = [
            colors_module.scale_hex_brightness(
                settings.agent_color(expected_agents[i % 3]),
                settings.fade_range(colors_module.MODE_WORKING)[1],
            )
            for i in range(8)
        ]
        self.assertEqual(preview, expected)

    def test_round_robin_led_assignment_is_invariant_to_input_status_order(self) -> None:
        # Regression guard: the collector sorts statuses most-recently-
        # updated-first, which reorders on nearly every poll once two-plus
        # agents are active. If the LED renderer inherited that order
        # directly, every heartbeat from any agent would reshuffle which LED
        # shows which agent's color and force a full strip rewrite -- a
        # visible "restart" of the breathing loop even though nothing about
        # the actual statuses changed. The renderer must use a fixed,
        # content-independent ordering instead.
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_ROUND_ROBIN)
        forward = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.WORKING),
            _status("devin", AgentMode.WORKING),
        )
        reversed_order = tuple(reversed(forward))
        _, program_forward = program_for_snapshot(forward, led_count=8, colors=settings)
        _, program_reversed = program_for_snapshot(reversed_order, led_count=8, colors=settings)
        self.assertEqual(program_forward, program_reversed)

    def test_cycle_led_assignment_is_invariant_to_input_status_order(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_CYCLE)
        forward = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.WORKING),
            _status("devin", AgentMode.WORKING),
        )
        reversed_order = tuple(reversed(forward))
        _, program_forward = program_for_snapshot(forward, led_count=8, colors=settings)
        _, program_reversed = program_for_snapshot(reversed_order, led_count=8, colors=settings)
        self.assertEqual(program_forward, program_reversed)

    def test_spatial_split_led_assignment_is_invariant_to_input_status_order(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_SPATIAL)
        forward = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.WORKING),
        )
        reversed_order = tuple(reversed(forward))
        _, program_forward = program_for_snapshot(forward, led_count=8, colors=settings)
        _, program_reversed = program_for_snapshot(reversed_order, led_count=8, colors=settings)
        self.assertEqual(program_forward, program_reversed)

    def test_round_robin_single_agent_matches_ordinary_single_agent_rendering(self) -> None:
        settings_rr = ColorSettings.defaults().with_blend_mode(BLEND_MODE_ROUND_ROBIN)
        settings_spatial = ColorSettings.defaults().with_blend_mode(BLEND_MODE_SPATIAL)
        statuses = (_status("codex", AgentMode.WORKING),)
        state, rr_program = program_for_snapshot(statuses, led_count=8, colors=settings_rr)
        _, spatial_program = program_for_snapshot(statuses, led_count=8, colors=settings_spatial)
        # With exactly one active agent, blend mode shouldn't matter --
        # both take the single-agent shortcut and render identically.
        self.assertEqual(state, LedDisplayState.WORKING)
        self.assertEqual(rr_program, spatial_program)

    def test_round_robin_assigns_every_led_exactly_once(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_ROUND_ROBIN)
        statuses = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.BLOCKED_ERROR),
        )
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        pulse_line = program.splitlines()[1]
        indices = {segment.split(":")[0] for segment in pulse_line.split("; ")}
        self.assertEqual(indices, {str(i) for i in range(8)})

    def test_round_robin_works_even_with_more_agents_than_leds(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_ROUND_ROBIN)
        statuses = tuple(
            _status(f"agent{i}", AgentMode.WORKING) for i in range(5)
        )
        # Should not raise, and should still produce a valid 2-LED program.
        state, program = program_for_snapshot(statuses, led_count=2, colors=settings)
        lines = [line for line in program.splitlines() if line.strip()]
        self.assertLessEqual(len(lines), 20)
        self.assertLessEqual(len(program.encode()), 512)

    def test_relay_is_in_blend_mode_choices(self) -> None:
        self.assertIn(BLEND_MODE_RELAY, BLEND_MODE_CHOICES)

    def test_relay_assigns_every_led_exactly_once(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_RELAY)
        statuses = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.BLOCKED_ERROR),
        )
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        pulse_line = program.splitlines()[1]
        indices = {segment.split(":")[0] for segment in pulse_line.split("; ")}
        self.assertEqual(indices, {str(i) for i in range(8)})

    def test_relay_fully_staggers_so_only_one_led_is_ever_mid_flare(self) -> None:
        # The defining difference from Round-Robin: each LED's delay is a
        # full multiple of the per-turn duration, not a small fraction of
        # it, so turns never overlap.
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_RELAY)
        statuses = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.WORKING),
        )
        _, program = program_for_snapshot(statuses, led_count=4, colors=settings)
        pulse_line = program.splitlines()[1]
        duration_ms = int(settings.effective_speed_seconds(BLEND_MODE_RELAY) * 1000)
        delays = []
        for segment in pulse_line.split("; "):
            # "<index>:<color> <duration>ms pulse <delay>ms"
            delay_token = segment.split()[-1]
            self.assertTrue(delay_token.endswith("ms"))
            delays.append(int(delay_token[:-2]))
        self.assertEqual(delays, [index * duration_ms for index in range(4)])

    def test_relay_single_agent_matches_ordinary_single_agent_rendering(self) -> None:
        settings_relay = ColorSettings.defaults().with_blend_mode(BLEND_MODE_RELAY)
        settings_spatial = ColorSettings.defaults().with_blend_mode(BLEND_MODE_SPATIAL)
        statuses = (_status("codex", AgentMode.WORKING),)
        state, relay_program = program_for_snapshot(statuses, led_count=8, colors=settings_relay)
        _, spatial_program = program_for_snapshot(statuses, led_count=8, colors=settings_spatial)
        self.assertEqual(state, LedDisplayState.WORKING)
        self.assertEqual(relay_program, spatial_program)

    def test_relay_led_assignment_is_invariant_to_input_status_order(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_RELAY)
        forward = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.WORKING),
            _status("devin", AgentMode.WORKING),
        )
        reversed_order = tuple(reversed(forward))
        _, program_forward = program_for_snapshot(forward, led_count=8, colors=settings)
        _, program_reversed = program_for_snapshot(reversed_order, led_count=8, colors=settings)
        self.assertEqual(program_forward, program_reversed)

    def test_relay_respects_the_global_cycle_speed(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_RELAY).with_cycle_speed(2.0)
        self.assertEqual(settings.effective_speed_seconds(BLEND_MODE_RELAY), 2.0)

    def test_relay_reset_and_pulse_lines_are_eased_not_bare(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_RELAY)
        statuses = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.COMPLETED),
        )
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        reset_line = program.splitlines()[0]
        for segment in reset_line.split("; "):
            self.assertRegex(segment, r"\d+ms cosine$", f"bare reset segment: {segment!r}")

    def test_cycle_speed_is_configurable_and_clamped(self) -> None:
        settings = ColorSettings.defaults()
        self.assertEqual(settings.cycle_speed_seconds, DEFAULT_CYCLE_SPEED_SECONDS)
        fast = settings.with_cycle_speed(0.05)
        self.assertEqual(fast.cycle_speed_seconds, MIN_CYCLE_SPEED_SECONDS)
        slow = settings.with_cycle_speed(999)
        self.assertEqual(slow.cycle_speed_seconds, MAX_CYCLE_SPEED_SECONDS)

    def test_cycle_speed_changes_the_rendered_duration(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_CYCLE).with_cycle_speed(3.0)
        statuses = (_status("codex", AgentMode.WORKING), _status("claude", AgentMode.WORKING))
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        self.assertIn("3000ms", program)

    def test_cycle_speed_json_round_trip(self) -> None:
        settings = ColorSettings.defaults().with_cycle_speed(4.2)
        restored = ColorSettings.from_dict(settings.to_dict())
        self.assertEqual(restored.cycle_speed_seconds, 4.2)

    def test_cycle_speed_from_dict_rejects_malformed_input(self) -> None:
        restored = ColorSettings.from_dict({"cycle_speed_seconds": "not-a-number"})
        self.assertEqual(restored.cycle_speed_seconds, DEFAULT_CYCLE_SPEED_SECONDS)

    def test_spatial_split_still_available_and_distinct_from_round_robin(self) -> None:
        settings_rr = ColorSettings.defaults().with_blend_mode(BLEND_MODE_ROUND_ROBIN)
        settings_spatial = ColorSettings.defaults().with_blend_mode(BLEND_MODE_SPATIAL)
        statuses = (
            _status("codex", AgentMode.BLOCKED_ERROR),
            _status("claude", AgentMode.WORKING),
            _status("devin", AgentMode.IDLE_READY),
        )
        _, rr_program = program_for_snapshot(statuses, led_count=8, colors=settings_rr)
        _, spatial_program = program_for_snapshot(statuses, led_count=8, colors=settings_spatial)
        self.assertNotEqual(rr_program, spatial_program)

    def test_all_blend_modes_including_round_robin_stay_within_dsl_limits(self) -> None:
        settings_base = ColorSettings.defaults()
        statuses = (
            _status("codex", AgentMode.BLOCKED_ERROR),
            _status("claude", AgentMode.WORKING),
            _status("devin", AgentMode.IDLE_READY),
            _status("grok", AgentMode.COMPLETED),
        )
        for blend_mode in BLEND_MODE_CHOICES:
            settings = settings_base.with_blend_mode(blend_mode)
            for led_count in (2, 8):
                _, program = program_for_snapshot(statuses, led_count=led_count, colors=settings)
                lines = [line for line in program.splitlines() if line.strip()]
                self.assertLessEqual(len(lines), 20, f"{blend_mode}/{led_count}: too many lines")
                self.assertLessEqual(len(program.encode()), 512, f"{blend_mode}/{led_count}: too many bytes")


class AnimationSmoothnessTests(unittest.TestCase):
    """No generated program should ever contain a bare, un-eased color
    assignment used as a settle/reset line -- see
    led_status.settle_duration_ms for why a hard snap there reads as the
    animation "stopping" the moment a real status change interrupts an
    in-progress pulse."""

    def test_settle_duration_ms_is_bounded_and_proportional(self) -> None:
        from sidepulse.led_status import SETTLE_MAX_MS, SETTLE_MIN_MS, settle_duration_ms

        self.assertEqual(settle_duration_ms(0), SETTLE_MIN_MS)
        self.assertEqual(settle_duration_ms(100_000), SETTLE_MAX_MS)
        # A fast Round-Robin cycle (300ms, the slider's minimum) should get
        # a proportionally short settle, not the same fixed cost as a lazy
        # 6s idle breathe.
        fast = settle_duration_ms(300)
        slow = settle_duration_ms(6000)
        self.assertLess(fast, slow)
        self.assertLessEqual(fast, 160)
        self.assertGreaterEqual(fast, SETTLE_MIN_MS)

    def test_idle_and_working_full_strip_programs_have_no_bare_reset_line(self) -> None:
        for state in (LedDisplayState.IDLE, LedDisplayState.ASK, LedDisplayState.WORKING):
            program = program_for_display_state(state, led_count=8)
            first_line = program.splitlines()[0]
            # A bare color (no duration/easing suffix) is exactly the
            # instant-snap shape this is guarding against.
            self.assertRegex(first_line, r"\d+ms cosine$", f"{state}: {first_line!r} is a bare snap")

    def test_cycle_program_settle_line_is_eased_not_bare(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_CYCLE)
        statuses = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.WORKING),
        )
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        first_line = program.splitlines()[0]
        self.assertNotEqual(first_line, "off")
        self.assertRegex(first_line, r"^off \d+ms cosine$")

    def test_round_robin_settle_line_scales_down_at_fast_speeds(self) -> None:
        settings = (
            ColorSettings.defaults()
            .with_blend_mode(BLEND_MODE_ROUND_ROBIN)
            .with_speed_override(BLEND_MODE_ROUND_ROBIN, MIN_CYCLE_SPEED_SECONDS)
        )
        statuses = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.WORKING),
        )
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        reset_line = program.splitlines()[0]
        # At the fastest user-configurable speed, the settle line must not
        # regress to the old fixed 160ms -- that would burn over half of a
        # 300ms cycle as dead time.
        self.assertNotIn("160ms", reset_line)

    def test_spatial_split_reset_segments_are_eased_not_bare(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_SPATIAL)
        statuses = (
            _status("codex", AgentMode.WORKING),
            _status("claude", AgentMode.COMPLETED),
        )
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        reset_line = program.splitlines()[0]
        for segment in reset_line.split("; "):
            self.assertRegex(segment, r"\d+ms cosine$", f"bare reset segment: {segment!r}")


class DoneCelebrationTests(unittest.TestCase):
    """A one-shot twinkle-then-bloom flourish plays when settling into
    Done, instead of an instant snap to the solid color -- scoped to the
    single-agent/aggregate rendering path, since a multi-agent blend
    mode's Done segments live inside a shared repeat loop with other
    agents' active animations and would replay the twinkle every cycle."""

    def test_default_settings_have_celebration_enabled(self) -> None:
        self.assertTrue(ColorSettings.defaults().done_celebration_enabled)

    def test_done_celebration_is_off_by_default_in_program_for_display_state(self) -> None:
        # The underlying primitive defaults to today's exact plain solid
        # color -- opting in is the caller's (colors.py's) job, not this
        # function's default.
        self.assertEqual(
            program_for_display_state(LedDisplayState.DONE, done_color="#00FF66"),
            "#00FF66",
        )

    def test_done_celebration_plays_once_then_settles_with_no_repeat(self) -> None:
        program = program_for_display_state(
            LedDisplayState.DONE, done_color="#00FF66", led_count=8, done_celebrate=True
        )
        self.assertNotIn("repeat", program)
        self.assertTrue(program.rstrip().endswith("#00FF66 280ms cosine"))

    def test_done_celebration_starts_with_an_eased_not_bare_transition(self) -> None:
        program = program_for_display_state(
            LedDisplayState.DONE, done_color="#00FF66", led_count=8, done_celebrate=True
        )
        first_line = program.splitlines()[0]
        self.assertRegex(first_line, r"^off \d+ms cosine$")

    def test_done_celebration_covers_every_led(self) -> None:
        for led_count in (2, 8):
            program = program_for_display_state(
                LedDisplayState.DONE, done_color="#00FF66", led_count=led_count, done_celebrate=True
            )
            twinkle_line = program.splitlines()[1]
            indices = {segment.split(":")[0] for segment in twinkle_line.split("; ")}
            self.assertEqual(indices, {str(i) for i in range(led_count)})

    def test_done_celebration_respects_brightness_scaling(self) -> None:
        program = program_for_display_state(
            LedDisplayState.DONE, done_color="#00FF66", led_count=2, done_celebrate=True, brightness=128
        )
        self.assertTrue(program.startswith("brightness 128\n"))

    def test_single_agent_done_uses_settings_celebration_flag(self) -> None:
        statuses = (_status("codex", AgentMode.COMPLETED),)
        celebrating = ColorSettings.defaults().with_done_celebration_enabled(True)
        plain = ColorSettings.defaults().with_done_celebration_enabled(False)
        _, program_with = program_for_snapshot(statuses, led_count=8, colors=celebrating)
        _, program_without = program_for_snapshot(statuses, led_count=8, colors=plain)
        self.assertNotIn("repeat", program_with)
        self.assertGreater(len(program_with.splitlines()), 1)
        self.assertEqual(program_without.splitlines(), [colors_module.default_agent_color("codex")])

    def test_classic_blend_mode_aggregate_done_uses_settings_celebration_flag(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_CLASSIC)
        statuses = (_status("codex", AgentMode.COMPLETED),)
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        self.assertNotIn("repeat", program)
        self.assertGreater(len(program.splitlines()), 1)

    def test_no_active_agents_fallback_done_uses_settings_celebration_flag(self) -> None:
        settings = ColorSettings.defaults()
        _, program = program_for_snapshot((), led_count=8, colors=settings, fallback_mode=AgentMode.COMPLETED)
        self.assertNotIn("repeat", program)
        self.assertGreater(len(program.splitlines()), 1)

    def test_multi_agent_blend_modes_keep_done_plain_inside_the_shared_loop(self) -> None:
        # Round-Robin/Relay/Spatial/Cycle all wrap a Done agent's segment in
        # a shared "repeat" loop with other active agents -- a one-shot
        # celebration there would replay every cycle, so these intentionally
        # do NOT get the flourish regardless of the settings flag.
        settings = ColorSettings.defaults().with_done_celebration_enabled(True)
        statuses = (
            _status("codex", AgentMode.COMPLETED),
            _status("claude", AgentMode.WORKING),
        )
        for blend_mode in (BLEND_MODE_ROUND_ROBIN, BLEND_MODE_RELAY, BLEND_MODE_SPATIAL, BLEND_MODE_CYCLE):
            mode_settings = settings.with_blend_mode(blend_mode)
            _, program = program_for_snapshot(statuses, led_count=8, colors=mode_settings)
            self.assertIn("repeat", program, f"{blend_mode} should still repeat (Claude is still working)")

    def test_program_stays_within_device_limits_with_celebration_on(self) -> None:
        settings = ColorSettings.defaults().with_done_celebration_enabled(True)
        for led_count in (2, 8):
            statuses = (_status("codex", AgentMode.COMPLETED),)
            _, program = program_for_snapshot(statuses, led_count=led_count, colors=settings)
            lines = [line for line in program.splitlines() if line.strip()]
            self.assertLessEqual(len(lines), 20)
            self.assertLessEqual(len(program.encode()), 512)

    def test_settings_persist_done_celebration_flag(self) -> None:
        settings = ColorSettings.defaults().with_done_celebration_enabled(False)
        restored = ColorSettings.from_dict(settings.to_dict())
        self.assertFalse(restored.done_celebration_enabled)

    def test_done_celebration_defaults_true_when_absent_from_saved_json(self) -> None:
        restored = ColorSettings.from_dict({})
        self.assertTrue(restored.done_celebration_enabled)


class SpeedOverrideAndUrgencyAlertTests(unittest.TestCase):
    def test_effective_speed_falls_back_to_global_by_default(self) -> None:
        settings = ColorSettings.defaults().with_cycle_speed(4.0)
        self.assertEqual(settings.effective_speed_seconds(BLEND_MODE_ROUND_ROBIN), 4.0)
        self.assertEqual(settings.effective_speed_seconds(BLEND_MODE_CYCLE), 4.0)
        self.assertTrue(settings.uses_global_speed(BLEND_MODE_ROUND_ROBIN))
        self.assertTrue(settings.uses_global_speed(BLEND_MODE_CYCLE))

    def test_per_mode_override_is_independent_of_global_and_other_mode(self) -> None:
        settings = (
            ColorSettings.defaults()
            .with_cycle_speed(4.0)
            .with_speed_override(BLEND_MODE_ROUND_ROBIN, 0.8)
        )
        self.assertEqual(settings.effective_speed_seconds(BLEND_MODE_ROUND_ROBIN), 0.8)
        self.assertEqual(settings.effective_speed_seconds(BLEND_MODE_CYCLE), 4.0)
        self.assertFalse(settings.uses_global_speed(BLEND_MODE_ROUND_ROBIN))
        self.assertTrue(settings.uses_global_speed(BLEND_MODE_CYCLE))

    def test_with_global_speed_for_mode_clears_the_override(self) -> None:
        settings = ColorSettings.defaults().with_speed_override(BLEND_MODE_ROUND_ROBIN, 0.8)
        reverted = settings.with_global_speed_for_mode(BLEND_MODE_ROUND_ROBIN)
        self.assertTrue(reverted.uses_global_speed(BLEND_MODE_ROUND_ROBIN))
        self.assertEqual(reverted.effective_speed_seconds(BLEND_MODE_ROUND_ROBIN), reverted.cycle_speed_seconds)

    def test_speed_override_rejects_unknown_mode(self) -> None:
        settings = ColorSettings.defaults()
        with self.assertRaises(ValueError):
            settings.with_speed_override(BLEND_MODE_CLASSIC, 1.0)
        with self.assertRaises(ValueError):
            settings.with_global_speed_for_mode(BLEND_MODE_CLASSIC)

    def test_speed_override_is_clamped(self) -> None:
        settings = ColorSettings.defaults().with_speed_override(BLEND_MODE_CYCLE, 999)
        self.assertEqual(settings.effective_speed_seconds(BLEND_MODE_CYCLE), MAX_CYCLE_SPEED_SECONDS)

    def test_speed_override_json_round_trip(self) -> None:
        settings = ColorSettings.defaults().with_speed_override(BLEND_MODE_ROUND_ROBIN, 2.5)
        restored = ColorSettings.from_dict(settings.to_dict())
        self.assertEqual(restored.effective_speed_seconds(BLEND_MODE_ROUND_ROBIN), 2.5)
        self.assertTrue(restored.uses_global_speed(BLEND_MODE_CYCLE))

    def test_speed_override_from_dict_rejects_malformed_input(self) -> None:
        restored = ColorSettings.from_dict({"speed_overrides": {"round_robin": "nope", "classic": 1.0}})
        # Malformed value dropped, unknown mode key dropped -- falls back to global.
        self.assertTrue(restored.uses_global_speed(BLEND_MODE_ROUND_ROBIN))

    def test_round_robin_program_uses_its_own_override_not_global(self) -> None:
        settings = (
            ColorSettings.defaults()
            .with_cycle_speed(5.0)
            .with_speed_override(BLEND_MODE_ROUND_ROBIN, 0.5)
        )
        statuses = (_status("codex", AgentMode.WORKING), _status("claude", AgentMode.WORKING))
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        self.assertIn("500ms", program)
        self.assertNotIn("5000ms", program)

    def test_cycle_program_uses_its_own_override_not_global(self) -> None:
        settings = (
            ColorSettings.defaults()
            .with_blend_mode(BLEND_MODE_CYCLE)
            .with_cycle_speed(5.0)
            .with_speed_override(BLEND_MODE_CYCLE, 0.7)
        )
        statuses = (_status("codex", AgentMode.WORKING), _status("claude", AgentMode.WORKING))
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        self.assertIn("700ms", program)

    def test_urgency_alert_enabled_by_default(self) -> None:
        self.assertTrue(ColorSettings.defaults().round_robin_urgency_alert)

    def test_urgency_alert_swaps_blocked_agent_to_ask_mode_color(self) -> None:
        settings = ColorSettings.defaults()
        statuses = (_status("codex", AgentMode.WORKING), _status("claude", AgentMode.BLOCKED_ERROR))
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        _floor, ask_ceiling = settings.fade_range(colors_module.MODE_ASK)
        expected_alert_color = colors_module.scale_hex_brightness(
            settings.mode_color(colors_module.MODE_ASK), ask_ceiling
        )
        claude_own_color = colors_module.scale_hex_brightness(settings.agent_color("claude"), ask_ceiling)
        self.assertIn(expected_alert_color, program)
        if expected_alert_color != claude_own_color:
            self.assertNotIn(claude_own_color, program)

    def test_urgency_alert_disabled_keeps_agents_own_color(self) -> None:
        settings = ColorSettings.defaults().with_round_robin_urgency_alert(False)
        statuses = (_status("codex", AgentMode.WORKING), _status("claude", AgentMode.BLOCKED_ERROR))
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        _floor, ask_ceiling = settings.fade_range(colors_module.MODE_ASK)
        claude_own_color = colors_module.scale_hex_brightness(settings.agent_color("claude"), ask_ceiling)
        self.assertIn(claude_own_color, program)

    def test_urgency_alert_does_not_affect_spatial_split(self) -> None:
        # Spatial Split already signals urgency via block size -- the alert
        # color swap is scoped to Round-Robin/Cycle only.
        settings_on = ColorSettings.defaults().with_blend_mode(BLEND_MODE_SPATIAL)
        settings_off = settings_on.with_round_robin_urgency_alert(False)
        statuses = (_status("codex", AgentMode.WORKING), _status("claude", AgentMode.BLOCKED_ERROR))
        _, program_on = program_for_snapshot(statuses, led_count=8, colors=settings_on)
        _, program_off = program_for_snapshot(statuses, led_count=8, colors=settings_off)
        self.assertEqual(program_on, program_off)


class AnimationStyleTests(unittest.TestCase):
    def test_defaults_match_todays_original_animation_shapes(self) -> None:
        settings = ColorSettings.defaults()
        self.assertEqual(settings.animation_style(colors_module.MODE_IDLE), ANIMATION_STYLE_PULSE)
        self.assertEqual(settings.animation_style(colors_module.MODE_ASK), ANIMATION_STYLE_PULSE)
        self.assertEqual(settings.animation_style(colors_module.MODE_WORKING), ANIMATION_STYLE_ROLL)
        # Done isn't customizable -- always reports solid regardless of storage.
        self.assertEqual(settings.animation_style(colors_module.MODE_DONE), ANIMATION_STYLE_SOLID)

    def test_with_mode_animation_rejects_done_and_unknown_style(self) -> None:
        settings = ColorSettings.defaults()
        with self.assertRaises(ValueError):
            settings.with_mode_animation(colors_module.MODE_DONE, ANIMATION_STYLE_BLINK)
        with self.assertRaises(ValueError):
            settings.with_mode_animation(colors_module.MODE_ASK, "not-a-style")

    def test_mode_animation_json_round_trip(self) -> None:
        settings = ColorSettings.defaults().with_mode_animation(colors_module.MODE_WORKING, ANIMATION_STYLE_SOLID)
        restored = ColorSettings.from_dict(settings.to_dict())
        self.assertEqual(restored.animation_style(colors_module.MODE_WORKING), ANIMATION_STYLE_SOLID)

    def test_mode_animation_from_dict_rejects_malformed_input(self) -> None:
        restored = ColorSettings.from_dict({"mode_animation": {"working": "nonsense", "idle": 42}})
        self.assertEqual(restored.animation_style(colors_module.MODE_WORKING), ANIMATION_STYLE_ROLL)
        self.assertEqual(restored.animation_style(colors_module.MODE_IDLE), ANIMATION_STYLE_PULSE)

    def test_solid_style_produces_single_color_no_animation(self) -> None:
        settings = ColorSettings.defaults().with_mode_animation(colors_module.MODE_ASK, ANIMATION_STYLE_SOLID)
        _, program = program_for_snapshot(
            (_status("codex", AgentMode.BLOCKED_ERROR),), led_count=8, colors=settings
        )
        self.assertNotIn("pulse", program)
        self.assertNotIn("repeat", program)

    def test_blink_style_produces_two_phase_none_eased_program(self) -> None:
        settings = ColorSettings.defaults().with_mode_animation(colors_module.MODE_WORKING, ANIMATION_STYLE_BLINK)
        _, program = program_for_snapshot((_status("codex", AgentMode.WORKING),), led_count=8, colors=settings)
        lines = [line for line in program.splitlines() if line.strip()]
        self.assertEqual(sum(1 for line in lines if "none" in line), 2)
        self.assertIn("repeat", program)

    def test_roll_style_applies_to_idle_not_just_working(self) -> None:
        settings = ColorSettings.defaults().with_mode_animation(colors_module.MODE_IDLE, ANIMATION_STYLE_ROLL)
        _, program = program_for_snapshot((_status("codex", AgentMode.IDLE_READY),), led_count=8, colors=settings)
        self.assertIn("0:", program)
        self.assertIn("7:", program)

    def test_all_animation_styles_produce_valid_dsl_line_and_byte_limits(self) -> None:
        base = ColorSettings.defaults()
        for style in ANIMATION_STYLE_CHOICES:
            settings = base.with_mode_animation(colors_module.MODE_WORKING, style)
            for led_count in (2, 8):
                _, program = program_for_snapshot(
                    (_status("codex", AgentMode.WORKING),), led_count=led_count, colors=settings
                )
                lines = [line for line in program.splitlines() if line.strip()]
                self.assertLessEqual(len(lines), 20, f"{style}/{led_count}: too many lines")
                self.assertLessEqual(len(program.encode()), 512, f"{style}/{led_count}: too many bytes")

    def test_program_for_display_state_defaults_unchanged_without_style_kwargs(self) -> None:
        # Backward compatibility: any caller that doesn't pass *_style still
        # gets exactly today's original shapes.
        working = program_for_display_state(LedDisplayState.WORKING, led_count=8, brightness=255)
        self.assertIn("pulse", working)
        idle = program_for_display_state(LedDisplayState.IDLE, led_count=8, brightness=255)
        self.assertIn("pulse", idle)


class PreviewLedColorsTests(unittest.TestCase):
    def test_demo_statuses_cover_three_distinct_modes(self) -> None:
        demo = colors_module.demo_statuses_for_preview()
        modes = {status.mode for status in demo}
        self.assertEqual(len(modes), 3)

    def test_preview_matches_program_for_snapshot_agent_count_per_led(self) -> None:
        settings = ColorSettings.defaults()
        demo = colors_module.demo_statuses_for_preview()
        preview = colors_module.preview_led_colors(demo, led_count=8, colors=settings)
        self.assertEqual(len(preview), 8)
        # Spatial split -- same number of distinct colors as active agents.
        self.assertEqual(len(set(preview)), len(demo))

    def test_preview_color_blend_is_uniform_and_matches_weighted_blend(self) -> None:
        settings = ColorSettings.defaults().with_blend_mode(BLEND_MODE_COLOR)
        demo = colors_module.demo_statuses_for_preview()
        preview = colors_module.preview_led_colors(demo, led_count=8, colors=settings)
        self.assertEqual(len(set(preview)), 1, "color_blend should paint every LED the same color")


class PreviewScenarioTests(unittest.TestCase):
    """A brand-new user with nothing running yet should still be able to
    see, in the Colors window, what every situation looks like -- one
    agent, several sessions of the same provider, a full mixed team, more
    agents than LEDs, and so on."""

    def test_every_scenario_choice_has_a_label(self) -> None:
        for scenario in colors_module.PREVIEW_SCENARIO_CHOICES:
            self.assertIn(scenario, colors_module.PREVIEW_SCENARIO_LABELS)
            self.assertTrue(colors_module.PREVIEW_SCENARIO_LABELS[scenario])

    def test_live_scenario_falls_back_to_the_fixed_demo(self) -> None:
        # PREVIEW_SCENARIO_LIVE has no builder of its own -- callers are
        # expected to prefer the real snapshot themselves and only reach
        # here once nothing is actually running, at which point it must
        # match today's existing demo fallback exactly.
        statuses = colors_module.preview_statuses_for_scenario(colors_module.PREVIEW_SCENARIO_LIVE)
        demo = colors_module.demo_statuses_for_preview()
        self.assertEqual([s.provider for s in statuses], [s.provider for s in demo])
        self.assertEqual([s.mode for s in statuses], [s.mode for s in demo])

    def test_quiet_scenario_has_no_active_agents(self) -> None:
        statuses = colors_module.preview_statuses_for_scenario(colors_module.PREVIEW_SCENARIO_QUIET)
        self.assertEqual(statuses, ())

    def test_same_provider_duo_uses_one_provider_twice_with_distinct_agent_ids(self) -> None:
        statuses = colors_module.preview_statuses_for_scenario(
            colors_module.PREVIEW_SCENARIO_SAME_PROVIDER_DUO
        )
        providers = {status.provider for status in statuses}
        agent_ids = {status.agent_id for status in statuses}
        self.assertEqual(providers, {"codex"})
        self.assertEqual(len(agent_ids), len(statuses), "each session needs a distinct agent_id")

    def test_busy_team_scenario_has_more_agents_than_either_device_has_leds(self) -> None:
        statuses = colors_module.preview_statuses_for_scenario(colors_module.PREVIEW_SCENARIO_BUSY_TEAM)
        self.assertGreater(len(statuses), 2)  # more than a SidePulse Dot's LED count

    def test_full_team_scenario_covers_every_registered_provider(self) -> None:
        statuses = colors_module.preview_statuses_for_scenario(colors_module.PREVIEW_SCENARIO_FULL_TEAM)
        providers = {status.provider for status in statuses}
        self.assertEqual(providers, {spec.provider for spec in PROVIDER_SPECS})

    def test_every_scenario_renders_without_error_on_both_device_sizes(self) -> None:
        settings = ColorSettings.defaults()
        for scenario in colors_module.PREVIEW_SCENARIO_CHOICES:
            statuses = colors_module.preview_statuses_for_scenario(scenario)
            for led_count in (2, 8):
                for blend_mode in BLEND_MODE_CHOICES:
                    mode_settings = settings.with_blend_mode(blend_mode)
                    _, program = program_for_snapshot(statuses, led_count=led_count, colors=mode_settings)
                    self.assertLessEqual(len(program.encode()), 512, f"{scenario}/{blend_mode}/{led_count}")

    def test_preview_idle_uses_fallback_mode_color(self) -> None:
        settings = ColorSettings.defaults()
        preview = colors_module.preview_led_colors((), led_count=4, colors=settings)
        self.assertEqual(len(set(preview)), 1)
        self.assertEqual(len(preview), 4)

    def test_preview_single_agent_matches_program_for_snapshot_peak_color(self) -> None:
        settings = ColorSettings.defaults()
        statuses = (_status("codex", AgentMode.WORKING),)
        preview = colors_module.preview_led_colors(statuses, led_count=8, colors=settings)
        _, program = program_for_snapshot(statuses, led_count=8, colors=settings)
        self.assertIn(preview[0], program)


class AgentLedControllerSnapshotTests(unittest.TestCase):
    def test_sync_snapshot_writes_program_and_dedups_unchanged_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device_dir = Path(tmp) / "SidePulseDot"
            device_dir.mkdir()
            (device_dir / "LEDS.LED").touch()

            controller = AgentLedController(device_path=device_dir)
            settings = ColorSettings.defaults()
            statuses = (_status("codex", AgentMode.WORKING),)

            first = controller.sync_snapshot(statuses, settings)
            self.assertTrue(first.changed)
            self.assertIsNone(first.error)

            second = controller.sync_snapshot(statuses, settings)
            self.assertFalse(second.changed, "identical snapshot should not rewrite the device")

    def test_sync_snapshot_fallback_mode_used_when_statuses_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device_dir = Path(tmp) / "SidePulseDot"
            device_dir.mkdir()
            (device_dir / "LEDS.LED").touch()

            controller = AgentLedController(device_path=device_dir)
            settings = ColorSettings.defaults()

            result = controller.sync_snapshot((), settings, fallback_mode=AgentMode.BLOCKED_ERROR)
            self.assertEqual(result.state, LedDisplayState.ASK)


class ChannelGainCalibrationTests(unittest.TestCase):
    """Per-device R/G/B write-time correction, for hardware whose LED dies
    don't render a hex color the way it looks on a calibrated screen (e.g.
    an over-bright green die making a mostly-blue color read greenish)."""

    def test_apply_channel_gain_to_hex_scales_each_channel_independently(self) -> None:
        from sidepulse.led_status import apply_channel_gain_to_hex

        # Halve green, leave red/blue alone.
        self.assertEqual(apply_channel_gain_to_hex("#2B8FFF", (1.0, 0.5, 1.0)), "#2B48FF")

    def test_apply_channel_gain_to_hex_clamps_to_byte_range(self) -> None:
        from sidepulse.led_status import apply_channel_gain_to_hex

        self.assertEqual(apply_channel_gain_to_hex("#FFFFFF", (1.5, 1.5, 1.5)), "#FFFFFF")
        self.assertEqual(apply_channel_gain_to_hex("#000000", (0.3, 0.3, 0.3)), "#000000")

    def test_apply_channel_gain_to_hex_invalid_input_passes_through(self) -> None:
        from sidepulse.led_status import apply_channel_gain_to_hex

        self.assertEqual(apply_channel_gain_to_hex("off", (0.5, 0.5, 0.5)), "off")

    def test_apply_channel_gain_to_program_is_a_no_op_at_neutral_gains(self) -> None:
        from sidepulse.led_status import NEUTRAL_CHANNEL_GAINS, apply_channel_gain_to_program

        program = "off 160ms cosine\n#2B8FFF 1600ms pulse\nrepeat"
        self.assertEqual(apply_channel_gain_to_program(program, NEUTRAL_CHANNEL_GAINS), program)

    def test_apply_channel_gain_to_program_rewrites_every_hex_occurrence(self) -> None:
        from sidepulse.led_status import apply_channel_gain_to_program

        program = "0:#2B8FFF 160ms cosine; 1:#6C3C2C 160ms cosine\n0:#2B8FFF 1600ms pulse\nrepeat"
        result = apply_channel_gain_to_program(program, (1.0, 0.5, 1.0))
        self.assertNotIn("#2B8FFF", result)
        self.assertEqual(result.count("#2B48FF"), 2)  # both occurrences rewritten
        # Non-color DSL syntax (durations, easings, "repeat") untouched.
        self.assertIn("160ms cosine", result)
        self.assertIn("1600ms pulse", result)
        self.assertIn("repeat", result)

    def test_apply_channel_gain_to_program_leaves_off_and_brightness_alone(self) -> None:
        from sidepulse.led_status import apply_channel_gain_to_program

        program = "brightness 128\noff\n#2B8FFF 1600ms pulse\nrepeat"
        result = apply_channel_gain_to_program(program, (1.0, 0.5, 1.0))
        self.assertIn("brightness 128", result)
        self.assertIn("off\n", result)

    def test_normalize_channel_gain_clamps_and_defaults(self) -> None:
        from sidepulse.led_status import (
            DEFAULT_CHANNEL_GAIN,
            MAX_CHANNEL_GAIN,
            MIN_CHANNEL_GAIN,
            normalize_channel_gain,
        )

        self.assertEqual(normalize_channel_gain(None), DEFAULT_CHANNEL_GAIN)
        self.assertEqual(normalize_channel_gain(0.0), MIN_CHANNEL_GAIN)
        self.assertEqual(normalize_channel_gain(99.0), MAX_CHANNEL_GAIN)
        self.assertEqual(normalize_channel_gain("garbage"), DEFAULT_CHANNEL_GAIN)
        self.assertEqual(normalize_channel_gain(0.8), 0.8)

    def test_agent_led_controller_applies_gain_to_physical_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device_dir = Path(tmp) / "SidePulsePro"
            device_dir.mkdir()
            (device_dir / "LEDS.LED").touch()

            controller = AgentLedController(device_path=device_dir)
            controller.channel_gains = (1.0, 0.5, 1.0)
            settings = ColorSettings.defaults().with_agent_color("codex", "#2B8FFF")
            statuses = (_status("codex", AgentMode.WORKING),)

            controller.sync_snapshot(statuses, settings)
            written = (device_dir / "LEDS.LED").read_text()
            self.assertNotIn("#2B8FFF", written)

    def test_agent_led_controller_dedup_accounts_for_gain_change(self) -> None:
        # Regression guard: sync_snapshot's dedup compares the *final*
        # (already-gain-corrected) program string, so a calibration change
        # alone -- statuses and colors unchanged -- still triggers a
        # rewrite instead of being silently swallowed by the dedup check.
        with tempfile.TemporaryDirectory() as tmp:
            device_dir = Path(tmp) / "SidePulsePro"
            device_dir.mkdir()
            (device_dir / "LEDS.LED").touch()

            controller = AgentLedController(device_path=device_dir)
            settings = ColorSettings.defaults().with_agent_color("codex", "#2B8FFF")
            statuses = (_status("codex", AgentMode.WORKING),)

            first = controller.sync_snapshot(statuses, settings)
            self.assertTrue(first.changed)
            unchanged = controller.sync_snapshot(statuses, settings)
            self.assertFalse(unchanged.changed)

            controller.channel_gains = (1.0, 0.5, 1.0)
            after_calibration = controller.sync_snapshot(statuses, settings)
            self.assertTrue(after_calibration.changed)

    def test_agent_led_controller_sync_mode_dedup_accounts_for_gain_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            device_dir = Path(tmp) / "SidePulsePro"
            device_dir.mkdir()
            (device_dir / "LEDS.LED").touch()

            controller = AgentLedController(device_path=device_dir)
            first = controller.sync_mode(AgentMode.WORKING)
            self.assertTrue(first.changed)
            unchanged = controller.sync_mode(AgentMode.WORKING)
            self.assertFalse(unchanged.changed)

            controller.channel_gains = (1.0, 0.5, 1.0)
            after_calibration = controller.sync_mode(AgentMode.WORKING)
            self.assertTrue(after_calibration.changed)

    def test_battery_led_controller_applies_gain_to_physical_write(self) -> None:
        from sidepulse.battery import BatteryLedController, BatterySnapshot

        with tempfile.TemporaryDirectory() as tmp:
            device_dir = Path(tmp) / "SidePulsePro"
            device_dir.mkdir()
            (device_dir / "LEDS.LED").touch()

            controller = BatteryLedController(device_path=device_dir)
            controller.channel_gains = (1.0, 0.5, 1.0)
            snapshot = BatterySnapshot(percent=80)
            controller.sync_snapshot(snapshot)
            written = (device_dir / "LEDS.LED").read_text()
            self.assertNotIn("#00FF66", written)  # BATTERY_HIGH_GREEN, green-halved

    def test_settings_persist_channel_gains(self) -> None:
        settings = AgentMonitorSettings().with_device_channel_gain("SidePulseDot", "green", 0.5)
        self.assertEqual(settings.channel_gains_for_device("SidePulseDot"), (1.0, 0.5, 1.0))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(settings, path)
            reloaded = load_settings(path)
        self.assertEqual(reloaded.channel_gains_for_device("SidePulseDot"), (1.0, 0.5, 1.0))

    def test_channel_gains_default_neutral_when_absent_from_saved_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(AgentMonitorSettings().with_device_brightness("SidePulseDot", 128), path)
            data = json.loads(path.read_text())
            for key in ("red_gain", "green_gain", "blue_gain"):
                data["devices"][0].pop(key, None)
            path.write_text(json.dumps(data))
            reloaded = load_settings(path)
        self.assertEqual(reloaded.channel_gains_for_device("SidePulseDot"), (1.0, 1.0, 1.0))

    def test_with_device_channel_gain_preserves_other_channels_and_brightness(self) -> None:
        settings = (
            AgentMonitorSettings()
            .with_device_brightness("SidePulseDot", 200)
            .with_device_channel_gain("SidePulseDot", "red", 0.7)
            .with_device_channel_gain("SidePulseDot", "blue", 1.2)
        )
        self.assertEqual(settings.channel_gains_for_device("SidePulseDot"), (0.7, 1.0, 1.2))
        self.assertEqual(settings.brightness_for_device("SidePulseDot"), 200)

    def test_with_device_channel_gain_rejects_unknown_channel(self) -> None:
        with self.assertRaises(ValueError):
            AgentMonitorSettings().with_device_channel_gain("SidePulseDot", "purple", 1.0)

    def test_with_device_channel_gains_reset(self) -> None:
        settings = AgentMonitorSettings().with_device_channel_gain("SidePulseDot", "green", 0.4)
        reset = settings.with_device_channel_gains_reset("SidePulseDot")
        self.assertEqual(reset.channel_gains_for_device("SidePulseDot"), (1.0, 1.0, 1.0))

    def test_brightness_change_preserves_existing_channel_gains(self) -> None:
        # Same field-preservation class of bug as auto_brightness_enabled --
        # with_device_brightness must not silently reset calibration back
        # to neutral.
        settings = AgentMonitorSettings().with_device_channel_gain("SidePulseDot", "green", 0.5)
        settings = settings.with_device_brightness("SidePulseDot", 90)
        self.assertEqual(settings.channel_gains_for_device("SidePulseDot"), (1.0, 0.5, 1.0))

    def test_status_bar_device_submenu_shows_calibration_sliders(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        device = status_bar.StatusBarDevice(
            device_id="/Volumes/SidePulseDot",
            name="SidePulse Dot",
            root=Path("/Volumes/SidePulseDot"),
            target=Path("/Volumes/SidePulseDot/LEDS.LED"),
            connected=True,
            display="agent",
            channel_gains=(1.0, 0.5, 1.0),
        )
        item = status_bar.build_device_menu_item(device, SimpleNamespace())
        submenu = item.submenu()
        titles = [
            submenu.itemAtIndex_(index).title()
            for index in range(submenu.numberOfItems())
            if submenu.itemAtIndex_(index).title()
        ]
        self.assertTrue(any("Color Calibration" in title for title in titles))
        self.assertIn("Reset Calibration", titles)


class FocusSyncTests(unittest.TestCase):
    """Detects an active macOS Focus via ~/Library/DoNotDisturb/DB/
    Assertions.json -- unverified against a real live Focus session (that
    file is TCC-protected and reading it during development would have
    required first granting Full Disk Access to a background process), so
    these test the parsing logic against synthetic JSON shaped like the
    documented format rather than a real capture."""

    def test_no_assertions_present_is_not_active(self) -> None:
        from sidepulse.focus_sync import _has_active_assertion

        self.assertFalse(_has_active_assertion({"data": [{"storeAssertionRecords": []}]}))

    def test_a_populated_assertion_record_is_active(self) -> None:
        from sidepulse.focus_sync import _has_active_assertion

        data = {
            "data": [
                {
                    "storeAssertionRecords": [
                        {"assertionDetails": {"assertionDetailsModeIdentifier": "com.apple.focus.work"}}
                    ]
                }
            ]
        }
        self.assertTrue(_has_active_assertion(data))

    def test_active_assertion_found_regardless_of_nesting_depth(self) -> None:
        # The exact schema isn't documented and has shifted across macOS
        # releases -- the search must not depend on one exact key path.
        from sidepulse.focus_sync import _has_active_assertion

        deeply_nested = {"a": {"b": [{"c": {"storeAssertionRecords": [{"x": 1}]}}]}}
        self.assertTrue(_has_active_assertion(deeply_nested))

    def test_is_focus_active_reads_the_real_expected_path(self) -> None:
        from sidepulse.focus_sync import ASSERTIONS_PATH

        self.assertEqual(str(ASSERTIONS_PATH), str(Path.home() / "Library/DoNotDisturb/DB/Assertions.json"))

    def test_is_focus_active_raises_unavailable_on_permission_error(self) -> None:
        from sidepulse import focus_sync

        with patch.object(focus_sync.Path, "read_text", side_effect=PermissionError("no FDA")):
            with self.assertRaises(focus_sync.FocusSyncUnavailableError):
                focus_sync.is_focus_active()

    def test_is_focus_active_raises_unavailable_on_unparseable_json(self) -> None:
        from sidepulse import focus_sync

        with patch.object(focus_sync.Path, "read_text", return_value="not json"):
            with self.assertRaises(focus_sync.FocusSyncUnavailableError):
                focus_sync.is_focus_active()

    def test_is_focus_active_false_on_empty_file(self) -> None:
        from sidepulse import focus_sync

        with patch.object(focus_sync.Path, "read_text", return_value=""):
            self.assertFalse(focus_sync.is_focus_active())

    def test_is_focus_active_true_end_to_end_on_realistic_json(self) -> None:
        from sidepulse import focus_sync

        payload = json.dumps(
            {"data": [{"storeAssertionRecords": [{"assertionDetails": {"id": "abc"}}]}]}
        )
        with patch.object(focus_sync.Path, "read_text", return_value=payload):
            self.assertTrue(focus_sync.is_focus_active())


class FocusSyncScaleFactorTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))
        self.status_bar = status_bar
        self.controller = status_bar.StatusBarController.alloc().init()

    def test_neutral_when_disabled(self) -> None:
        self.controller.settings = self.controller.settings.with_focus_sync_enabled(False)
        with patch.object(self.status_bar.focus_sync, "is_focus_active", return_value=True):
            self.assertEqual(self.controller.focus_sync_scale_factor(), 1.0)

    def test_dims_when_enabled_and_focus_is_active(self) -> None:
        settings = self.controller.settings.with_focus_sync_enabled(True).with_idle_dim_fraction(0.4)
        self.controller.settings = settings
        with patch.object(self.status_bar.focus_sync, "is_focus_active", return_value=True):
            self.assertEqual(self.controller.focus_sync_scale_factor(), 0.4)

    def test_neutral_when_enabled_but_no_focus_active(self) -> None:
        self.controller.settings = self.controller.settings.with_focus_sync_enabled(True)
        with patch.object(self.status_bar.focus_sync, "is_focus_active", return_value=False):
            self.assertEqual(self.controller.focus_sync_scale_factor(), 1.0)

    def test_fails_safe_to_neutral_when_detection_unavailable(self) -> None:
        self.controller.settings = self.controller.settings.with_focus_sync_enabled(True)
        with patch.object(
            self.status_bar.focus_sync,
            "is_focus_active",
            side_effect=self.status_bar.focus_sync.FocusSyncUnavailableError("no FDA"),
        ):
            self.assertEqual(self.controller.focus_sync_scale_factor(), 1.0)

    def test_effective_brightness_combines_idle_dim_and_focus_sync_multiplicatively(self) -> None:
        settings = (
            self.controller.settings.with_idle_dim_after_minutes(1.0)
            .with_idle_dim_fraction(0.5)
            .with_focus_sync_enabled(True)
        )
        self.controller.settings = settings
        self.controller.idle_since_monotonic = time.monotonic() - 120
        device = self.status_bar.StatusBarDevice(
            device_id="SidePulseDot",
            name="SidePulseDot",
            root=Path("/Volumes/SidePulseDot"),
            target=Path("/Volumes/SidePulseDot/LEDS.LED"),
            connected=True,
            display=LED_DISPLAY_BATTERY,
            brightness=200,
        )
        with patch.object(self.status_bar.focus_sync, "is_focus_active", return_value=True):
            # 200 * 0.5 (idle) * 0.5 (focus) = 50
            self.assertEqual(self.controller.effective_brightness_for_device(device), 50)

    def test_settings_persist_focus_sync_enabled(self) -> None:
        settings = AgentMonitorSettings().with_focus_sync_enabled(True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(settings, path)
            reloaded = load_settings(path)
        self.assertTrue(reloaded.focus_sync_enabled)

    def test_focus_sync_defaults_false_when_absent_from_saved_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(AgentMonitorSettings(), path)
            data = json.loads(path.read_text())
            data.pop("focus_sync_enabled", None)
            path.write_text(json.dumps(data))
            reloaded = load_settings(path)
        self.assertFalse(reloaded.focus_sync_enabled)


class DisplayBrightnessTests(unittest.TestCase):
    def test_current_screen_brightness_returns_a_valid_fraction_on_this_mac(self) -> None:
        try:
            from sidepulse.display_brightness import current_screen_brightness_fraction
        except ImportError as exc:
            self.skipTest(str(exc))
        try:
            value = current_screen_brightness_fraction()
        except Exception as exc:
            self.skipTest(f"CoreDisplay unavailable in this environment: {exc}")
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 1.0)

    def test_auto_led_brightness_respects_the_minimum_floor(self) -> None:
        from sidepulse import display_brightness

        with patch.object(display_brightness, "current_screen_brightness_fraction", return_value=0.0):
            self.assertEqual(display_brightness.auto_led_brightness(), display_brightness.MIN_AUTO_BRIGHTNESS)

    def test_auto_led_brightness_scales_with_screen_fraction(self) -> None:
        from sidepulse import display_brightness

        with patch.object(display_brightness, "current_screen_brightness_fraction", return_value=1.0):
            self.assertEqual(display_brightness.auto_led_brightness(), 255)
        with patch.object(display_brightness, "current_screen_brightness_fraction", return_value=0.5):
            self.assertEqual(display_brightness.auto_led_brightness(), 128)

    def test_unavailable_error_propagates_from_auto_led_brightness(self) -> None:
        from sidepulse import display_brightness

        with patch.object(
            display_brightness,
            "current_screen_brightness_fraction",
            side_effect=display_brightness.DisplayBrightnessUnavailableError("nope"),
        ):
            with self.assertRaises(display_brightness.DisplayBrightnessUnavailableError):
                display_brightness.auto_led_brightness()


class DeviceAutoBrightnessSettingsTests(unittest.TestCase):
    def test_auto_brightness_defaults_to_disabled(self) -> None:
        settings = AgentMonitorSettings()
        self.assertFalse(settings.auto_brightness_enabled_for_device("SidePulseDot"))

    def test_with_device_auto_brightness_enables_for_new_device(self) -> None:
        settings = AgentMonitorSettings().with_device_auto_brightness("SidePulseDot", True)
        self.assertTrue(settings.auto_brightness_enabled_for_device("SidePulseDot"))

    def test_with_device_auto_brightness_preserves_existing_brightness(self) -> None:
        settings = AgentMonitorSettings().with_device_brightness("SidePulseDot", 128)
        settings = settings.with_device_auto_brightness("SidePulseDot", True)
        self.assertEqual(settings.brightness_for_device("SidePulseDot"), 128)
        self.assertTrue(settings.auto_brightness_enabled_for_device("SidePulseDot"))

    def test_with_device_brightness_preserves_existing_auto_brightness_flag(self) -> None:
        # Regression guard: an earlier version of with_device_brightness/
        # with_device_display rebuilt DeviceDisplaySetting from scratch
        # instead of dataclasses.replace(), silently dropping
        # auto_brightness_enabled back to False on any brightness change.
        settings = AgentMonitorSettings().with_device_auto_brightness("SidePulseDot", True)
        settings = settings.with_device_brightness("SidePulseDot", 200)
        self.assertTrue(settings.auto_brightness_enabled_for_device("SidePulseDot"))
        self.assertEqual(settings.brightness_for_device("SidePulseDot"), 200)

    def test_with_device_display_preserves_existing_auto_brightness_flag(self) -> None:
        settings = AgentMonitorSettings().with_device_auto_brightness("SidePulseDot", True)
        settings = settings.with_device_display("SidePulseDot", LED_DISPLAY_BATTERY)
        self.assertTrue(settings.auto_brightness_enabled_for_device("SidePulseDot"))

    def test_auto_brightness_json_round_trip(self) -> None:
        settings = AgentMonitorSettings().with_device_auto_brightness("SidePulseDot", True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(settings, path)
            reloaded = load_settings(path)
        self.assertTrue(reloaded.auto_brightness_enabled_for_device("SidePulseDot"))

    def test_auto_brightness_defaults_false_when_missing_from_saved_json(self) -> None:
        # Simulates loading a settings.json written before this field
        # existed -- devices lack the key entirely, and that must not crash
        # the loader or default to enabled.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(AgentMonitorSettings().with_device_brightness("SidePulseDot", 128), path)
            data = json.loads(path.read_text())
            del data["devices"][0]["auto_brightness_enabled"]
            path.write_text(json.dumps(data))
            reloaded = load_settings(path)
        self.assertFalse(reloaded.auto_brightness_enabled_for_device("SidePulseDot"))



class RememberConnectedDevicesRaceTests(unittest.TestCase):
    """remember_connected_devices runs on the background LED-sync worker
    thread and can race against a settings change made from a UI action on
    the main thread -- a blind read-modify-write would silently discard
    whichever one wrote second. The fix is an optimistic compare-and-set:
    detect that self.settings changed underneath the computation and retry
    against the fresh value instead of clobbering it."""

    def setUp(self) -> None:
        # This exercises remember_connected_devices, which calls
        # save_settings() -- MUST be isolated from the real settings file
        # (patched before construction, per the established rule) or it
        # writes real device entries and flips real flags on disk.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        settings_path = Path(self._tmp.name) / "settings.json"
        patcher_settings = patch("sidepulse.settings.default_settings_path", return_value=settings_path)
        patcher_status_bar = patch("sidepulse.status_bar.default_settings_path", return_value=settings_path)
        patcher_settings.start()
        patcher_status_bar.start()
        self.addCleanup(patcher_settings.stop)
        self.addCleanup(patcher_status_bar.stop)
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))
        self.status_bar = status_bar
        self.controller = status_bar.StatusBarController.alloc().init()

    def _device(self, device_id="SidePulseDot"):
        return self.status_bar.StatusBarDevice(
            device_id=device_id,
            name="SidePulse Dot",
            root=Path(f"/Volumes/{device_id}"),
            target=Path(f"/Volumes/{device_id}/LEDS.LED"),
            connected=True,
            display=LED_DISPLAY_BATTERY,
        )

    def test_a_concurrent_settings_change_is_not_discarded(self) -> None:
        original_with_remembered_device = type(self.controller.settings).with_remembered_device
        call_count = {"n": 0}

        def racing_with_remembered_device(self_settings, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Simulate a concurrent UI action reassigning self.settings
                # to something else *while* remember_connected_devices is
                # still mid-computation, derived from the pre-race value.
                self.controller.settings = self.controller.settings.with_focus_sync_enabled(True)
            return original_with_remembered_device(self_settings, **kwargs)

        self.assertFalse(self.controller.settings.focus_sync_enabled)

        with patch.object(
            type(self.controller.settings), "with_remembered_device", racing_with_remembered_device
        ):
            self.controller.remember_connected_devices([self._device()])

        # Both changes survived: the concurrent focus_sync_enabled change
        # (made "during" the computation) and the device-remembering
        # update itself (retried against the post-race value).
        self.assertTrue(self.controller.settings.focus_sync_enabled)
        self.assertIn("SidePulseDot", {d.device_id for d in self.controller.settings.devices})
        self.assertGreaterEqual(call_count["n"], 2, "should have retried at least once")

    def test_no_race_still_commits_normally(self) -> None:
        self.controller.remember_connected_devices([self._device()])
        self.assertIn("SidePulseDot", {d.device_id for d in self.controller.settings.devices})

    def test_no_op_for_a_disconnected_device(self) -> None:
        device = self.status_bar.StatusBarDevice(
            device_id="SidePulseDot",
            name="SidePulse Dot",
            root=Path("/Volumes/SidePulseDot"),
            target=Path("/Volumes/SidePulseDot/LEDS.LED"),
            connected=False,
            display=LED_DISPLAY_BATTERY,
        )
        before = self.controller.settings
        self.controller.remember_connected_devices([device])
        self.assertIs(self.controller.settings, before)


class EffectiveDeviceBrightnessTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))
        self.status_bar = status_bar

    def _device(self, **overrides):
        defaults = dict(
            device_id="SidePulseDot",
            name="SidePulseDot",
            root=Path("/Volumes/SidePulseDot"),
            target=Path("/Volumes/SidePulseDot/LEDS.LED"),
            connected=True,
            display=LED_DISPLAY_BATTERY,
            brightness=180,
            auto_brightness_enabled=False,
        )
        defaults.update(overrides)
        return self.status_bar.StatusBarDevice(**defaults)

    def test_manual_brightness_used_when_auto_brightness_is_off(self) -> None:
        controller = self.status_bar.StatusBarController.alloc().init()
        device = self._device(auto_brightness_enabled=False, brightness=180)
        self.assertEqual(controller.effective_brightness_for_device(device), 180)

    def test_auto_brightness_used_when_reading_succeeds(self) -> None:
        controller = self.status_bar.StatusBarController.alloc().init()
        device = self._device(auto_brightness_enabled=True, brightness=180)
        with patch.object(self.status_bar.display_brightness, "auto_led_brightness", return_value=90):
            self.assertEqual(controller.effective_brightness_for_device(device), 90)

    def test_falls_back_to_manual_brightness_when_reading_unavailable(self) -> None:
        controller = self.status_bar.StatusBarController.alloc().init()
        device = self._device(auto_brightness_enabled=True, brightness=180)
        with patch.object(
            self.status_bar.display_brightness,
            "auto_led_brightness",
            side_effect=self.status_bar.display_brightness.DisplayBrightnessUnavailableError("nope"),
        ):
            self.assertEqual(controller.effective_brightness_for_device(device), 180)


class IdleTimeoutDimmingTests(unittest.TestCase):
    """After idle_dim_after_minutes of continuous Idle, effective
    brightness scales down by idle_dim_fraction -- a long-idle Mac
    shouldn't keep a bright light going on the desk."""

    def setUp(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))
        self.status_bar = status_bar
        self.controller = status_bar.StatusBarController.alloc().init()

    def _device(self, **overrides):
        defaults = dict(
            device_id="SidePulseDot",
            name="SidePulseDot",
            root=Path("/Volumes/SidePulseDot"),
            target=Path("/Volumes/SidePulseDot/LEDS.LED"),
            connected=True,
            display=LED_DISPLAY_BATTERY,
            brightness=200,
            auto_brightness_enabled=False,
        )
        defaults.update(overrides)
        return self.status_bar.StatusBarDevice(**defaults)

    def test_scale_factor_is_neutral_before_going_idle(self) -> None:
        self.assertIsNone(self.controller.idle_since_monotonic)
        self.assertEqual(self.controller.idle_dim_scale_factor(), 1.0)

    def test_scale_factor_is_neutral_before_the_threshold_elapses(self) -> None:
        self.controller.settings = self.controller.settings.with_idle_dim_after_minutes(10.0)
        self.controller.idle_since_monotonic = time.monotonic()  # just went idle
        self.assertEqual(self.controller.idle_dim_scale_factor(), 1.0)

    def test_scale_factor_dims_after_the_threshold_elapses(self) -> None:
        settings = self.controller.settings.with_idle_dim_after_minutes(5.0).with_idle_dim_fraction(0.25)
        self.controller.settings = settings
        self.controller.idle_since_monotonic = time.monotonic() - (6 * 60)  # 6 minutes idle
        self.assertEqual(self.controller.idle_dim_scale_factor(), 0.25)

    def test_scale_factor_neutral_when_disabled(self) -> None:
        settings = (
            self.controller.settings.with_idle_dim_enabled(False)
            .with_idle_dim_after_minutes(1.0)
            .with_idle_dim_fraction(0.1)
        )
        self.controller.settings = settings
        self.controller.idle_since_monotonic = time.monotonic() - 3600
        self.assertEqual(self.controller.idle_dim_scale_factor(), 1.0)

    def test_effective_brightness_applies_idle_dim_on_top_of_manual_brightness(self) -> None:
        settings = self.controller.settings.with_idle_dim_after_minutes(1.0).with_idle_dim_fraction(0.5)
        self.controller.settings = settings
        self.controller.idle_since_monotonic = time.monotonic() - 120  # 2 minutes idle
        device = self._device(brightness=200, auto_brightness_enabled=False)
        self.assertEqual(self.controller.effective_brightness_for_device(device), 100)

    def test_effective_brightness_applies_idle_dim_on_top_of_auto_brightness(self) -> None:
        settings = self.controller.settings.with_idle_dim_after_minutes(1.0).with_idle_dim_fraction(0.5)
        self.controller.settings = settings
        self.controller.idle_since_monotonic = time.monotonic() - 120
        device = self._device(auto_brightness_enabled=True)
        with patch.object(self.status_bar.display_brightness, "auto_led_brightness", return_value=200):
            self.assertEqual(self.controller.effective_brightness_for_device(device), 100)

    def test_set_status_starts_the_idle_clock_on_transition_into_idle(self) -> None:
        self.controller.set_status(self.status_bar.STATE_WORKING)
        self.assertIsNone(self.controller.idle_since_monotonic)
        self.controller.set_status(self.status_bar.STATE_IDLE)
        self.assertIsNotNone(self.controller.idle_since_monotonic)

    def test_set_status_clears_the_idle_clock_on_transition_away_from_idle(self) -> None:
        self.controller.set_status(self.status_bar.STATE_IDLE)
        self.assertIsNotNone(self.controller.idle_since_monotonic)
        self.controller.set_status(self.status_bar.STATE_WORKING)
        self.assertIsNone(self.controller.idle_since_monotonic)

    def test_set_status_does_not_reset_the_clock_on_repeated_idle_calls(self) -> None:
        # Re-confirming "still idle" (e.g. on every poll tick) must not
        # keep pushing the idle-since timestamp forward, or dimming would
        # never actually trigger.
        self.controller.set_status(self.status_bar.STATE_IDLE)
        first = self.controller.idle_since_monotonic
        self.controller.set_status(self.status_bar.STATE_IDLE)
        self.assertEqual(self.controller.idle_since_monotonic, first)

    def test_settings_persist_idle_dim_fields(self) -> None:
        settings = (
            AgentMonitorSettings()
            .with_idle_dim_enabled(False)
            .with_idle_dim_after_minutes(20.0)
            .with_idle_dim_fraction(0.4)
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(settings, path)
            reloaded = load_settings(path)
        self.assertFalse(reloaded.idle_dim_enabled)
        self.assertEqual(reloaded.idle_dim_after_minutes, 20.0)
        self.assertEqual(reloaded.idle_dim_fraction, 0.4)

    def test_idle_dim_defaults_on_when_absent_from_saved_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(AgentMonitorSettings(), path)
            data = json.loads(path.read_text())
            for key in ("idle_dim_enabled", "idle_dim_after_minutes", "idle_dim_fraction"):
                data.pop(key, None)
            path.write_text(json.dumps(data))
            reloaded = load_settings(path)
        self.assertTrue(reloaded.idle_dim_enabled)
        self.assertEqual(reloaded.idle_dim_after_minutes, 10.0)
        self.assertEqual(reloaded.idle_dim_fraction, 0.3)

    def test_idle_dim_after_minutes_is_clamped(self) -> None:
        from sidepulse.settings import MAX_IDLE_DIM_AFTER_MINUTES, MIN_IDLE_DIM_AFTER_MINUTES

        settings = AgentMonitorSettings().with_idle_dim_after_minutes(0.0)
        self.assertEqual(settings.idle_dim_after_minutes, MIN_IDLE_DIM_AFTER_MINUTES)
        settings = AgentMonitorSettings().with_idle_dim_after_minutes(9999.0)
        self.assertEqual(settings.idle_dim_after_minutes, MAX_IDLE_DIM_AFTER_MINUTES)

    def test_idle_dim_fraction_is_clamped(self) -> None:
        from sidepulse.settings import MAX_IDLE_DIM_FRACTION, MIN_IDLE_DIM_FRACTION

        settings = AgentMonitorSettings().with_idle_dim_fraction(-1.0)
        self.assertEqual(settings.idle_dim_fraction, MIN_IDLE_DIM_FRACTION)
        settings = AgentMonitorSettings().with_idle_dim_fraction(5.0)
        self.assertEqual(settings.idle_dim_fraction, MAX_IDLE_DIM_FRACTION)


class ClosedLidGracePeriodTests(unittest.TestCase):
    """A buffer against a false "done" reading (e.g. a command still
    running with no events for a stretch) closing the lid into sleep and
    losing the agent's work."""

    def test_default_matches_keep_awakes_own_long_standing_default(self) -> None:
        from sidepulse.keep_awake import AWAKE_GRACE_SECONDS

        settings = AgentMonitorSettings()
        self.assertEqual(settings.closed_lid_grace_minutes * 60.0, AWAKE_GRACE_SECONDS)

    def test_settings_persist_closed_lid_grace_minutes(self) -> None:
        settings = AgentMonitorSettings().with_closed_lid_grace_minutes(15.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(settings, path)
            reloaded = load_settings(path)
        self.assertEqual(reloaded.closed_lid_grace_minutes, 15.0)

    def test_closed_lid_grace_minutes_is_clamped(self) -> None:
        from sidepulse.settings import MAX_CLOSED_LID_GRACE_MINUTES, MIN_CLOSED_LID_GRACE_MINUTES

        settings = AgentMonitorSettings().with_closed_lid_grace_minutes(-5.0)
        self.assertEqual(settings.closed_lid_grace_minutes, MIN_CLOSED_LID_GRACE_MINUTES)
        settings = AgentMonitorSettings().with_closed_lid_grace_minutes(9999.0)
        self.assertEqual(settings.closed_lid_grace_minutes, MAX_CLOSED_LID_GRACE_MINUTES)

    def test_closed_lid_grace_minutes_defaults_when_absent_from_saved_json(self) -> None:
        from sidepulse.settings import DEFAULT_CLOSED_LID_GRACE_MINUTES

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(AgentMonitorSettings(), path)
            data = json.loads(path.read_text())
            data.pop("closed_lid_grace_minutes", None)
            path.write_text(json.dumps(data))
            reloaded = load_settings(path)
        self.assertEqual(reloaded.closed_lid_grace_minutes, DEFAULT_CLOSED_LID_GRACE_MINUTES)

    def test_sync_keep_awake_applies_the_configured_grace_seconds(self) -> None:
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        controller = status_bar.StatusBarController.alloc().init()
        controller.settings = controller.settings.with_closed_lid_grace_minutes(2.0)
        # Disabled so update() doesn't try to spawn a real caffeinate
        # process -- only set_grace_seconds()'s propagation is under test.
        controller.keep_awake.set_enabled(False)
        controller.sync_keep_awake(AgentMode.IDLE_READY)
        self.assertEqual(controller.keep_awake.grace_seconds, 120.0)


class SettingsWindowDeviceSectionTests(unittest.TestCase):
    """The Settings window's Devices section duplicates the per-device
    Brightness/Auto-Brightness/Color Calibration controls that live in the
    menu bar icon's own device submenu -- a real user went looking for
    Auto-Brightness in Settings and couldn't find it there at all."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        settings_path = Path(self._tmp.name) / "settings.json"
        patcher_settings = patch("sidepulse.settings.default_settings_path", return_value=settings_path)
        patcher_status_bar = patch("sidepulse.status_bar.default_settings_path", return_value=settings_path)
        patcher_settings.start()
        patcher_status_bar.start()
        self.addCleanup(patcher_settings.stop)
        self.addCleanup(patcher_status_bar.stop)
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))
        self.status_bar = status_bar
        self.controller = status_bar.StatusBarController.alloc().init()

    def test_settings_window_layout_has_no_overlapping_controls(self) -> None:
        # Regression guard for the exact bug class reported: a label that
        # wrapped to two lines inside a box sized for one, spilling into
        # the checkbox below it. A benign 1px separator/header overlap is
        # pre-existing and expected; anything beyond that is a real bug.
        self.controller.show_settings_window()
        doc = self.controller.settings_window.contentView().documentView()
        frames = [
            v.frame()
            for v in doc.subviews()
            if v.frame().size.width > 0 and v.frame().size.height > 0
        ]

        def overlaps(a, b):
            ax0, ay0 = a.origin.x, a.origin.y
            ax1, ay1 = ax0 + a.size.width, ay0 + a.size.height
            bx0, by0 = b.origin.x, b.origin.y
            bx1, by1 = bx0 + b.size.width, by0 + b.size.height
            return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1

        overlap_count = sum(
            1
            for i in range(len(frames))
            for j in range(i + 1, len(frames))
            if overlaps(frames[i], frames[j])
        )
        self.assertLessEqual(overlap_count, 1, "unexpected overlapping controls in the Settings window")

    def test_settings_window_content_never_overflows_the_document_view(self) -> None:
        self.controller.show_settings_window()
        doc = self.controller.settings_window.contentView().documentView()
        subviews = list(doc.subviews())
        max_y = max(v.frame().origin.y + v.frame().size.height for v in subviews)
        min_y = min(v.frame().origin.y for v in subviews)
        self.assertLessEqual(max_y, doc.frame().size.height + 2)
        self.assertGreater(min_y, -5)

    def test_devices_section_exists_for_each_connected_device(self) -> None:
        self.controller.show_settings_window()
        devices = self.controller.status_bar_devices(remember=False)
        self.assertEqual(set(self.controller.device_settings_controls.keys()), {d.device_id for d in devices})

    def test_device_controls_include_brightness_auto_brightness_and_calibration(self) -> None:
        self.controller.show_settings_window()
        for controls in self.controller.device_settings_controls.values():
            for key in (
                "brightness_slider",
                "brightness_label",
                "auto_brightness_checkbox",
                "calibration_label",
                "reset_button",
                "red_slider",
                "green_slider",
                "blue_slider",
            ):
                self.assertIn(key, controls)

    def test_menu_bar_change_is_reflected_in_the_open_settings_window(self) -> None:
        self.controller.show_settings_window()
        device_id = next(iter(self.controller.device_settings_controls))
        controls = self.controller.device_settings_controls[device_id]

        self.controller.set_device_brightness(device_id, 90)
        self.controller.refresh_settings_window()

        self.assertEqual(controls["brightness_slider"].doubleValue(), 90.0)
        self.assertEqual(controls["brightness_label"].stringValue(), "35%")

    def test_settings_window_change_is_reflected_in_a_freshly_built_menu(self) -> None:
        self.controller.show_settings_window()
        device_id = next(iter(self.controller.device_settings_controls))
        controls = self.controller.device_settings_controls[device_id]

        controls["brightness_slider"].setDoubleValue_(200.0)
        self.controller.setDeviceBrightness_(controls["brightness_slider"])

        device = next(
            d for d in self.controller.status_bar_devices(remember=False) if d.device_id == device_id
        )
        self.assertEqual(device.brightness, 200)

    def test_calibration_change_via_settings_window_persists(self) -> None:
        self.controller.show_settings_window()
        device_id = next(iter(self.controller.device_settings_controls))
        controls = self.controller.device_settings_controls[device_id]

        controls["green_slider"].setDoubleValue_(60.0)
        self.controller.setDeviceGreenGain_(controls["green_slider"])

        self.assertEqual(self.controller.settings.channel_gains_for_device(device_id), (1.0, 0.6, 1.0))

    def test_closed_lid_awake_policy_popup_reflects_current_setting(self) -> None:
        from sidepulse.settings import CLOSED_LID_AWAKE_ALWAYS

        self.controller.settings = self.controller.settings.with_closed_lid_awake_policy(
            CLOSED_LID_AWAKE_ALWAYS
        )
        self.controller.show_settings_window()
        popup = self.controller.settings_fields["closed_lid_awake_policy_popup"]
        self.assertEqual(popup.titleOfSelectedItem(), "Always")

    def test_closed_lid_awake_policy_popup_sets_the_policy(self) -> None:
        from sidepulse.settings import CLOSED_LID_AWAKE_ALWAYS

        self.controller.show_settings_window()
        popup = self.controller.settings_fields["closed_lid_awake_policy_popup"]
        for index in range(popup.numberOfItems()):
            if popup.itemAtIndex_(index).representedObject().get("policy") == CLOSED_LID_AWAKE_ALWAYS:
                popup.selectItemAtIndex_(index)
                break
        self.controller.setClosedLidAwakePolicyFromPopup_(popup)
        self.assertEqual(self.controller.settings.closed_lid_awake_policy, CLOSED_LID_AWAKE_ALWAYS)

    def test_refresh_does_not_crash_before_the_status_item_exists(self) -> None:
        # Regression guard: set_closed_lid_awake_policy (and several other
        # settings actions) call refresh_() as their final step, which
        # unconditionally touched self.status_item.setMenu_(...) -- a
        # crash in any context where the app hasn't finished launching
        # yet (status_item is only created in
        # applicationDidFinishLaunching_), which a Settings-window action
        # invoked in a headless/test context hits directly.
        self.assertIsNone(self.controller.status_item)
        self.controller.refresh_(None)  # must not raise


class ScreenBarSettingsTakeEffectImmediatelyTests(unittest.TestCase):
    """Regression guard: toggling a Screen Bar geometry/drawing setting
    (Alcove compatibility, wraps-menu-bar) must reposition the Screen Bar
    right away. Before this fix, both settings were only ever re-read the
    next time sync_virtual_status_device happened to run -- which, with a
    physical device connected, only happens on a genuine LED write. Once
    agent layout stopped thrashing (see the ordering-stability fix), that
    could be a very long wait: the setting would visibly "not work" even
    though it saved correctly."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._settings_path = Path(self._tmp.name) / "settings.json"
        patcher_settings = patch(
            "sidepulse.settings.default_settings_path", return_value=self._settings_path
        )
        patcher_status_bar = patch(
            "sidepulse.status_bar.default_settings_path", return_value=self._settings_path
        )
        patcher_settings.start()
        patcher_status_bar.start()
        self.addCleanup(patcher_settings.stop)
        self.addCleanup(patcher_status_bar.stop)
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))
        self.status_bar = status_bar
        self.controller = status_bar.StatusBarController.alloc().init()
        # The bug only manifests once the Screen Bar window already exists
        # (reposition() is a no-op before that) -- matches the real
        # scenario: the user already has it visible and flips a setting.
        self.controller.virtual_status_device.show()

    def test_toggling_wraps_menu_bar_repositions_without_any_led_write(self) -> None:
        before = self.controller.virtual_status_device.window.frame().size.width
        checkbox = self.status_bar.NSButton.alloc().init()
        self.status_bar.set_checkbox_state(checkbox, True)
        with patch.object(self.status_bar.StatusBarController, "sync_leds") as sync_leds:
            self.controller.toggleScreenBarWrapsMenuBar_(checkbox)
            sync_leds.assert_not_called()
        after = self.controller.virtual_status_device.window.frame().size.width
        self.assertTrue(self.controller.settings.virtual_status_device_wraps_menu_bar)
        self.assertTrue(self.controller.virtual_status_device.wraps_menu_bar)
        self.assertGreaterEqual(after, before)

    def test_toggling_alcove_mode_repositions_immediately(self) -> None:
        popup = self.status_bar.NSPopUpButton.alloc().init()
        popup.addItemWithTitle_("Always")
        popup.lastItem().setRepresentedObject_({"alcove_mode": ALCOVE_COMPAT_ALWAYS})
        with patch.object(self.status_bar.StatusBarController, "sync_leds") as sync_leds:
            self.controller.setAlcoveCompatibilityMode_(popup)
            sync_leds.assert_not_called()
        self.assertEqual(self.controller.settings.alcove_compatibility_mode, ALCOVE_COMPAT_ALWAYS)
        self.assertEqual(self.controller.virtual_status_device.alcove_compatibility_mode, ALCOVE_COMPAT_ALWAYS)


class SettingsColorPersistenceTests(unittest.TestCase):
    def test_settings_round_trip_persists_colors_and_alcove_mode(self) -> None:
        settings = AgentMonitorSettings().with_colors(
            ColorSettings.defaults().with_agent_color("codex", "#123456")
        ).with_alcove_compatibility_mode(ALCOVE_COMPAT_ALWAYS)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(settings, path)
            loaded = load_settings(path)

        self.assertEqual(loaded.colors.agent_color("codex"), "#123456")
        self.assertEqual(loaded.alcove_compatibility_mode, ALCOVE_COMPAT_ALWAYS)

    def test_corrupt_colors_block_falls_back_without_breaking_other_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(
                AgentMonitorSettings().with_setup_screen_completed(True),
                path,
            )
            data = json.loads(path.read_text())
            data["colors"] = "not-a-dict"
            data["alcove_compatibility_mode"] = "bogus"
            path.write_text(json.dumps(data))

            loaded = load_settings(path)

        self.assertEqual(loaded.colors.to_dict(), ColorSettings.defaults().to_dict())
        self.assertEqual(loaded.alcove_compatibility_mode, ALCOVE_COMPAT_AUTO)
        self.assertTrue(loaded.setup_screen_completed)


if __name__ == "__main__":
    unittest.main()
