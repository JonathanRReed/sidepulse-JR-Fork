from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .battery import (
    BatteryLedController,
    format_watts,
    parse_full_watts,
    read_battery_snapshot,
    render_battery_snapshot,
)
from .collector import AgentMonitor, SourceSpec, default_sources
from .demo_sandbox import available_scenarios, build_demo_run
from .device_writer import DEFAULT_FILE_NAME, DeviceWriteError, write_led_program
from .doctor import (
    PUBLIC_COLLECTION_ERROR_MESSAGE,
    DoctorExportError,
    collect_diagnostics,
    encode_diagnostic_result,
    render_diagnostic_result,
    write_diagnostic_export,
)
from .hook import hook_log_main
from .hook_client import hook_client_main
from .install import (
    HookVerificationError,
    hook_command_arguments,
    install_provider_hooks,
    uninstall_provider_hooks,
)
from .led_status import AgentLedController, LedStatusWrite
from .lid_sleep import (
    install_sleep_helper,
    sleep_helper_install_command,
    sleep_helper_installed,
    uninstall_sleep_helper,
)
from .models import AgentStatus
from .product_identity import PRODUCT_DISPLAY_NAME
from .providers import (
    HOOK_PROVIDERS,
    default_log_path,
    provider_spec,
)
from .settings import (
    LED_DISPLAY_BATTERY,
    LED_DISPLAY_CHOICES,
    load_settings,
    save_settings,
)
from .trusted_tools import trusted_system_tool
from .watch_run import WatchRunPlanError, execute_watch_run, plan_watch_run

PHONE_GLANCE_SECRET_ENV = "SIDEPULSE_PHONE_GLANCE_SECRET"
PHONE_GLANCE_ACCESS_TOKEN_ENV = "SIDEPULSE_PHONE_GLANCE_ACCESS_TOKEN"
SERVE_ACCESS_TOKEN_ENV = "SIDEPULSE_SERVE_ACCESS_TOKEN"


class _StoreAndRequestSdEjectGuard(argparse.Action):
    """Treat existing guard configuration flags as an explicit install request."""

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        setattr(namespace, self.dest, values)
        namespace.sd_eject_guard = True


def main(argv: list[str] | None = None, *, prog: str = "agent-monitor") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    return args.func(args)


def sidepulse_main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["agent-monitor"]:
        return main(args[1:], prog="sidepulse agent-monitor")

    parser = build_sidepulse_parser()
    if not args:
        parser.print_help()
        return 0

    parsed = parser.parse_args(args)
    return parsed.func(parsed)


def build_sidepulse_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sidepulse",
        description=f"{PRODUCT_DISPLAY_NAME} command line tools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser(
        "doctor",
        help=f"Show privacy-safe local {PRODUCT_DISPLAY_NAME} diagnostics.",
    )
    add_doctor_arguments(doctor)
    doctor.set_defaults(func=cmd_doctor)
    serve_parser = subparsers.add_parser(
        "serve",
        help="Serve redacted agent + quota state on loopback (Stream Deck, Waybar, scripts).",
    )
    add_serve_arguments(serve_parser)
    serve_parser.set_defaults(func=cmd_serve)
    add_glance_arguments(subparsers)
    subparsers.add_parser(
        "agent-monitor",
        help="Install hooks and show live AI agent statuses.",
    )
    add_watch_run_parser(subparsers)
    add_demo_parser(subparsers)
    add_effects_parser(subparsers)
    setup = subparsers.add_parser(
        "setup",
        help="Install agent hooks and start the macOS status-bar app.",
    )
    setup.add_argument(
        "provider",
        choices=("all", *HOOK_PROVIDERS),
        nargs="?",
        default="all",
        help="Agent hooks to install. Default: all.",
    )
    setup.add_argument("--log-dir", type=Path, help="Directory for provider JSONL files.")
    add_provider_log_arguments(setup)
    setup.add_argument("--dry-run", action="store_true", help="Show what would change.")
    setup.add_argument(
        "--sd-eject-guard",
        action="store_true",
        help="Install SidePulse Pro Eject Prevention for a physical LED bar.",
    )
    setup.add_argument(
        "--sd-eject-guard-scope",
        choices=("auto", "system", "user"),
        default="auto",
        action=_StoreAndRequestSdEjectGuard,
        help="Install the SD eject guard as a system service when possible, or as a user agent.",
    )
    setup.add_argument(
        "--sd-eject-guard-volume-uuid",
        action=_StoreAndRequestSdEjectGuard,
        help="Exact SidePulse volume UUID to protect. Without it the guard is installed disabled.",
    )
    setup.add_argument(
        "--no-status-bar",
        action="store_true",
        help="Do not install or start the status-bar app.",
    )
    setup.add_argument(
        "--no-sd-eject-guard",
        action="store_true",
        help="Skip the SD eject guard (needs clang; only useful with the LED bar).",
    )
    setup.set_defaults(func=cmd_sidepulse_setup)

    write = subparsers.add_parser(
        "write",
        help=f"Write an LED program to {DEFAULT_FILE_NAME} on a mounted SidePulse Pro or SidePulse Dot device.",
    )
    write.add_argument("text", help=r"LED program text. Backslash escapes like \n are decoded.")
    write.add_argument(
        "--device",
        type=Path,
        help="Mounted device folder or LED program file path. Defaults to auto-detecting /Volumes.",
    )
    write.add_argument(
        "--file-name",
        default=DEFAULT_FILE_NAME,
        help=f"Target file name when --device is a folder. Default: {DEFAULT_FILE_NAME}.",
    )
    write.add_argument("--dry-run", action="store_true", help="Show the target without writing.")
    write.set_defaults(func=cmd_sidepulse_write)

    add_sidepulse_status_bar_parser(subparsers)
    add_sidepulse_sdejectguard_parser(subparsers)
    add_sidepulse_battery_parser(subparsers)
    return parser


def add_sidepulse_status_bar_parser(subparsers: argparse._SubParsersAction) -> None:
    status_bar = subparsers.add_parser(
        "status-bar",
        help=f"Start or stop the macOS {PRODUCT_DISPLAY_NAME} menu-bar app.",
    )
    status_bar.add_argument(
        "status_bar_command",
        choices=(
            "start",
            "stop",
            "install-sleep-helper",
            "uninstall-sleep-helper",
            "sleep-helper-status",
        ),
        nargs="?",
        default="start",
        help="Start/stop the menu-bar app, or manage the closed-lid sleep helper. Default: start.",
    )
    status_bar.add_argument(
        "--foreground",
        action="store_true",
        help="Run the menu-bar app in the foreground instead of installing a LaunchAgent.",
    )
    status_bar.add_argument(
        "--dry-run",
        action="store_true",
        help="Show sleep-helper changes without writing them.",
    )
    status_bar.set_defaults(func=cmd_sidepulse_status_bar)


def add_sidepulse_sdejectguard_parser(subparsers: argparse._SubParsersAction) -> None:
    guard = subparsers.add_parser(
        "sdejectguard",
        help="Start, stop, uninstall, or inspect SidePulse Pro Eject Prevention.",
    )
    guard_subparsers = guard.add_subparsers(dest="sdejectguard_command", required=True)

    start = guard_subparsers.add_parser("start", help="Install and start SidePulse Pro Eject Prevention.")
    add_sdejectguard_scope_arg(start)
    start.add_argument("--dry-run", action="store_true", help="Show what would change.")
    start.add_argument(
        "-it",
        "--interactive",
        action="store_true",
        help="Run the guard in this terminal instead of launchd.",
    )
    start.add_argument(
        "--volume-uuid",
        required=True,
        help="Exact SidePulse volume UUID to protect.",
    )
    start.set_defaults(func=cmd_sidepulse_sdejectguard_start)

    stop = guard_subparsers.add_parser("stop", help="Stop SidePulse Pro Eject Prevention.")
    add_sdejectguard_scope_arg(stop)
    stop.add_argument("--dry-run", action="store_true", help="Show what would stop.")
    stop.set_defaults(func=cmd_sidepulse_sdejectguard_stop)

    uninstall = guard_subparsers.add_parser("uninstall", help="Remove SidePulse Pro Eject Prevention.")
    add_sdejectguard_scope_arg(uninstall)
    uninstall.add_argument("--dry-run", action="store_true", help="Show what would be removed.")
    uninstall.set_defaults(func=cmd_sidepulse_sdejectguard_uninstall)

    logs = guard_subparsers.add_parser("logs", help="Show SidePulse Pro Eject Prevention logs.")
    add_sdejectguard_scope_arg(logs)
    logs.add_argument("--lines", type=int, default=80, help="Lines to show per log file.")
    logs.add_argument("-f", "--follow", action="store_true", help="Follow existing log files.")
    logs.set_defaults(func=cmd_sidepulse_sdejectguard_logs)


def add_sdejectguard_scope_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scope",
        choices=("auto", "system", "user"),
        default="auto",
        help="Use system scope when possible, or target one scope explicitly.",
    )


def add_sidepulse_battery_parser(subparsers: argparse._SubParsersAction) -> None:
    battery = subparsers.add_parser(
        "battery",
        help="Show or mirror Mac battery state to SidePulse Pro/SidePulse Dot LEDs.",
    )
    battery_subparsers = battery.add_subparsers(dest="battery_command", required=True)

    status = battery_subparsers.add_parser("status", help="Show current Mac battery state.")
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    status.add_argument(
        "--full-watts",
        help="Full-speed charger wattage baseline, or 'auto'. Defaults to saved settings.",
    )
    status.set_defaults(func=cmd_sidepulse_battery_status)

    leds = battery_subparsers.add_parser("leds", help="Mirror Mac battery state to LEDs.")
    leds.add_argument("--interval", type=float, default=1.0, help="Refresh interval in seconds.")
    leds.add_argument(
        "--device",
        type=Path,
        help="Mounted device folder or LED program file path. Defaults to auto-detecting /Volumes.",
    )
    leds.add_argument(
        "--file-name",
        default=DEFAULT_FILE_NAME,
        help=f"Target file name when --device is a folder. Default: {DEFAULT_FILE_NAME}.",
    )
    leds.add_argument("--dry-run", action="store_true", help="Show writes without touching the device.")
    leds.add_argument("--once", action="store_true", help="Write the current battery state once and exit.")
    leds.add_argument(
        "--full-watts",
        help="Full-speed charger wattage baseline, or 'auto'. Defaults to saved settings.",
    )
    leds.set_defaults(func=cmd_sidepulse_battery_leds)

    configure = battery_subparsers.add_parser("configure", help="Save battery LED settings.")
    configure.add_argument("--display", choices=LED_DISPLAY_CHOICES, help="Status-bar LED display source.")
    configure.add_argument(
        "--full-watts",
        help="Full-speed charger wattage baseline, or 'auto' to use laptop defaults.",
    )
    configure.add_argument(
        "--show-on-power-change",
        choices=("yes", "no"),
        help="Briefly show battery LEDs when power is plugged/unplugged.",
    )
    configure.add_argument(
        "--power-change-preview-seconds",
        type=float,
        help="Seconds to show battery LEDs after plug/unplug.",
    )
    configure.set_defaults(func=cmd_sidepulse_battery_configure)


def cmd_sidepulse_write(args: argparse.Namespace) -> int:
    try:
        target = write_led_program(
            args.text,
            device_path=args.device,
            file_name=args.file_name,
            dry_run=args.dry_run,
        )
    except DeviceWriteError as exc:
        print(f"sidepulse write: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"sidepulse write: {exc}", file=sys.stderr)
        return 1

    action = "would write" if args.dry_run else "wrote"
    print(f"{action}: {target}")
    return 0


def cmd_sidepulse_battery_status(args: argparse.Namespace) -> int:
    try:
        snapshot = read_battery_snapshot(full_charge_watts=full_watts_from_args(args))
    except Exception as exc:
        print(f"sidepulse battery status: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(snapshot.to_dict(), indent=2))
    else:
        print(render_battery_snapshot(snapshot))
    return 0


def cmd_sidepulse_battery_leds(args: argparse.Namespace) -> int:
    leds = BatteryLedController(
        device_path=args.device,
        file_name=args.file_name,
        dry_run=args.dry_run,
    )
    full_watts = full_watts_from_args(args)

    try:
        while True:
            snapshot = read_battery_snapshot(full_charge_watts=full_watts)
            result = leds.sync_snapshot(snapshot)
            if result.changed or result.error or args.once:
                print(render_battery_led_result(result, snapshot, dry_run=args.dry_run))
                sys.stdout.flush()

            if args.once:
                return 2 if result.error else 0

            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"sidepulse battery leds: {exc}", file=sys.stderr)
        return 1


def cmd_sidepulse_battery_configure(args: argparse.Namespace) -> int:
    settings = load_settings()
    try:
        if args.display is not None:
            settings = settings.with_led_display(args.display)
        if args.full_watts is not None:
            settings = settings.with_battery_full_charge_watts(parse_full_watts(args.full_watts))
        if args.show_on_power_change is not None:
            settings = settings.with_battery_power_change_preview(
                enabled=args.show_on_power_change == "yes",
            )
        if args.power_change_preview_seconds is not None:
            settings = settings.with_battery_power_change_preview(
                seconds=args.power_change_preview_seconds,
            )
        target = save_settings(settings)
    except Exception as exc:
        print(f"sidepulse battery configure: {exc}", file=sys.stderr)
        return 1

    full_watts = (
        "auto"
        if settings.battery_full_charge_watts is None
        else format_watts(settings.battery_full_charge_watts)
    )
    preview = "on" if settings.battery_show_on_power_change else "off"
    print(f"settings: {target}")
    print(f"  led display: {settings.led_display}")
    print(f"  full charge watts: {full_watts}")
    print(
        "  power-change preview: "
        f"{preview} ({settings.battery_power_change_preview_seconds:g}s)"
    )
    if settings.led_display == LED_DISPLAY_BATTERY:
        print("  status bar LEDs will show battery.")
    return 0


def cmd_sidepulse_status_bar(args: argparse.Namespace) -> int:
    if args.status_bar_command == "install-sleep-helper":
        return cmd_sidepulse_sleep_helper_install(args)
    if args.status_bar_command == "uninstall-sleep-helper":
        return cmd_sidepulse_sleep_helper_uninstall(args)
    if args.status_bar_command == "sleep-helper-status":
        return cmd_sidepulse_sleep_helper_status(args)

    args.uninstall = args.status_bar_command == "stop"
    args.no_start = False
    if args.uninstall:
        args.foreground = False
    return cmd_status_bar(args)


def cmd_sidepulse_sleep_helper_install(args: argparse.Namespace) -> int:
    try:
        result = install_sleep_helper(dry_run=args.dry_run)
    except (PermissionError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"sleep-helper: {exc}", file=sys.stderr)
        return 1

    action = "would install" if result.dry_run and result.changed else "installed"
    if not result.changed:
        action = "already installed"
    print(f"sleep-helper: {action}")
    print(f"  user: {result.user}")
    print(f"  sudoers: {result.path}")
    return 0


def cmd_sidepulse_sleep_helper_uninstall(args: argparse.Namespace) -> int:
    try:
        result = uninstall_sleep_helper(dry_run=args.dry_run)
    except (PermissionError, OSError) as exc:
        print(f"sleep-helper: {exc}", file=sys.stderr)
        return 1

    action = "would remove" if result.dry_run and result.changed else "removed"
    if not result.changed:
        action = "not installed"
    print(f"sleep-helper: {action}")
    print(f"  sudoers: {result.path}")
    return 0


def cmd_sidepulse_sleep_helper_status(_args: argparse.Namespace) -> int:
    installed = sleep_helper_installed()
    print(f"sleep-helper: {'installed' if installed else 'not installed'}")
    if not installed:
        print(f"  install: {sleep_helper_install_command()}")
    return 0


def cmd_sidepulse_sdejectguard_start(args: argparse.Namespace) -> int:
    from .sd_eject_guard_launch import (
        SD_EJECT_GUARD_DISPLAY_NAME,
        SdEjectGuardInstallError,
        install_sd_eject_guard,
        run_sd_eject_guard_interactive,
    )

    try:
        if args.interactive:
            if args.dry_run:
                print(f"{SD_EJECT_GUARD_DISPLAY_NAME}: would run interactively ({args.scope})")
                return 0
            return run_sd_eject_guard_interactive(
                scope=args.scope,
                volume_uuid=args.volume_uuid,
            )

        result = install_sd_eject_guard(
            scope=args.scope,
            dry_run=args.dry_run,
            volume_uuid=args.volume_uuid,
        )
    except (SdEjectGuardInstallError, OSError, subprocess.CalledProcessError) as exc:
        print(f"{SD_EJECT_GUARD_DISPLAY_NAME}: {exc}", file=sys.stderr)
        return 1

    print_sd_eject_guard_result(result)
    return 0


def cmd_sidepulse_sdejectguard_stop(args: argparse.Namespace) -> int:
    from .sd_eject_guard_launch import (
        SD_EJECT_GUARD_DISPLAY_NAME,
        SdEjectGuardInstallError,
        stop_sd_eject_guard,
    )

    try:
        results = stop_sd_eject_guard(scope=args.scope, dry_run=args.dry_run)
    except (SdEjectGuardInstallError, OSError, subprocess.CalledProcessError) as exc:
        print(f"{SD_EJECT_GUARD_DISPLAY_NAME}: {exc}", file=sys.stderr)
        return 1

    for result in results:
        if result.skipped:
            action = f"skipped ({result.skipped})"
        elif result.stopped:
            action = "would stop" if args.dry_run else "stopped"
        else:
            action = "not installed"
        print(f"{SD_EJECT_GUARD_DISPLAY_NAME}: {action} ({result.scope})")
        print(f"  plist: {result.plist_path}")
    return 0


def cmd_sidepulse_sdejectguard_uninstall(args: argparse.Namespace) -> int:
    from .sd_eject_guard_launch import (
        SD_EJECT_GUARD_DISPLAY_NAME,
        SdEjectGuardInstallError,
        uninstall_sd_eject_guard,
    )

    try:
        results = uninstall_sd_eject_guard(scope=args.scope, dry_run=args.dry_run)
    except (SdEjectGuardInstallError, OSError, subprocess.CalledProcessError) as exc:
        print(f"{SD_EJECT_GUARD_DISPLAY_NAME}: {exc}", file=sys.stderr)
        return 1

    for result in results:
        if result.skipped:
            action = f"skipped ({result.skipped})"
        elif result.removed_paths:
            action = "would uninstall" if result.dry_run else "uninstalled"
        else:
            action = "not installed"
        print(f"{SD_EJECT_GUARD_DISPLAY_NAME}: {action} ({result.scope})")
        print(f"  plist: {result.plist_path}")
        for path in result.removed_paths:
            print(f"  removed: {path}")
    return 0


def cmd_sidepulse_sdejectguard_logs(args: argparse.Namespace) -> int:
    from .sd_eject_guard_launch import log_paths_for_requested_scope, read_log_tail

    try:
        paths = log_paths_for_requested_scope(args.scope)
    except Exception as exc:
        print(f"SidePulse Pro Eject Prevention logs: {exc}", file=sys.stderr)
        return 1

    existing_paths = [path for path in paths if path.exists()]
    if args.follow:
        if not existing_paths:
            for path in paths:
                print(f"{path}: missing")
            return 1
        try:
            return subprocess.run(
                [
                    str(trusted_system_tool("tail")),
                    "-n",
                    str(args.lines),
                    "-f",
                    *(str(path) for path in existing_paths),
                ],
                check=False,
            ).returncode
        except KeyboardInterrupt:
            return 130

    for path in paths:
        print(f"==> {path} <==")
        if not path.exists():
            print("(missing)")
            continue
        text = read_log_tail(path, args.lines)
        print(text if text else "(empty)")
    return 0


def cmd_sidepulse_setup(args: argparse.Namespace) -> int:
    results = install_hook_results(args)
    print_install_results(results, dry_run=args.dry_run)

    # The menu-bar app installs FIRST and the eject guard is best-effort:
    # the guard needs clang (Xcode Command Line Tools), and a fresh Mac
    # without them used to abort setup right here -- hooks installed, no
    # app, a confusing half-state (2026-08-27 readiness audit).
    if not args.no_status_bar:
        if args.dry_run:
            print("status-bar: would install and start")
        else:
            from .status_bar_launch import install_launch_agent

            result = install_launch_agent(start=True)
            action = "installed" if result.changed else "already installed"
            if result.started:
                action += " and started"
            print(f"status-bar: {action}")
            print(f"  plist: {result.plist_path}")

    if args.no_sd_eject_guard or not args.sd_eject_guard:
        return 0

    from .sd_eject_guard_launch import (
        SD_EJECT_GUARD_DISPLAY_NAME,
        SdEjectGuardInstallError,
        install_sd_eject_guard,
    )

    try:
        guard_result = install_sd_eject_guard(
            scope=args.sd_eject_guard_scope,
            dry_run=args.dry_run,
            volume_uuid=args.sd_eject_guard_volume_uuid,
        )
    except (SdEjectGuardInstallError, OSError, subprocess.CalledProcessError) as exc:
        print(f"{SD_EJECT_GUARD_DISPLAY_NAME}: {exc}", file=sys.stderr)
        print(
            f"{SD_EJECT_GUARD_DISPLAY_NAME}: skipped (it protects the "
            "LED bar's SD card during ejects; install Xcode Command "
            "Line Tools and re-run `sidepulse setup --sd-eject-guard` "
            "with your selected guard options to add it).",
            file=sys.stderr,
        )
        return 0
    print_sd_eject_guard_result(guard_result)
    return 0


def print_sd_eject_guard_result(result) -> None:
    from .sd_eject_guard_launch import SD_EJECT_GUARD_DISPLAY_NAME

    if result.dry_run:
        action = "would install and start" if result.changed else "would start (already configured)"
    else:
        action = "installed" if result.changed else "already installed"
        if result.started:
            action += " and started"
    print(f"{SD_EJECT_GUARD_DISPLAY_NAME}: {action} ({result.scope})")
    print(f"  plist: {result.plist_path}")
    print(f"  binary: {result.binary_path}")
    if result.cleanup_removed:
        print(f"  removed other scope: {result.cleanup_removed}")
    if result.cleanup_skipped:
        print(f"  cleanup skipped: {result.cleanup_skipped}")
    for path in getattr(result, "legacy_removed", ()):
        print(f"  removed legacy helper: {path}")


def build_parser(prog: str = "agent-monitor") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            f"Collect and aggregate local AI agent statuses for {PRODUCT_DISPLAY_NAME}."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor",
        help=f"Show privacy-safe local {PRODUCT_DISPLAY_NAME} diagnostics.",
    )
    add_doctor_arguments(doctor)
    doctor.set_defaults(func=cmd_doctor)

    monitor_serve = subparsers.add_parser(
        "serve",
        help="Serve redacted agent + quota state on loopback (Stream Deck, Waybar, scripts).",
    )
    add_serve_arguments(monitor_serve)
    monitor_serve.set_defaults(func=cmd_serve)
    add_glance_arguments(subparsers)

    status = subparsers.add_parser("status", help="Show current aggregate status once.")
    add_status_args(status)
    status.set_defaults(func=cmd_status)

    add_live_parser(subparsers, "live", "Show live statuses in the terminal.")
    add_live_parser(subparsers, "watch", "Alias for live.")
    add_watch_run_parser(subparsers)
    add_demo_parser(subparsers)

    leds = subparsers.add_parser("leds", help="Mirror aggregate agent status to SidePulse Pro/SidePulse Dot LEDs.")
    add_status_args(leds, include_json=False)
    leds.add_argument("--interval", type=float, default=1.0, help="Refresh interval in seconds.")
    leds.add_argument(
        "--device",
        type=Path,
        help="Mounted device folder or LED program file path. Defaults to auto-detecting /Volumes.",
    )
    leds.add_argument(
        "--file-name",
        default=DEFAULT_FILE_NAME,
        help=f"Target file name when --device is a folder. Default: {DEFAULT_FILE_NAME}.",
    )
    leds.add_argument("--dry-run", action="store_true", help="Show writes without touching the device.")
    leds.add_argument("--once", action="store_true", help="Write the current status once and exit.")
    leds.set_defaults(func=cmd_leds)

    status_bar = subparsers.add_parser("status-bar", help="Install and start the macOS menu-bar app.")
    status_bar_mode = status_bar.add_mutually_exclusive_group()
    status_bar_mode.add_argument(
        "--foreground",
        action="store_true",
        help="Run the menu-bar app in the foreground instead of installing a LaunchAgent.",
    )
    status_bar_mode.add_argument(
        "--uninstall",
        action="store_true",
        help="Stop and remove the status-bar LaunchAgent.",
    )
    status_bar.add_argument(
        "--no-start",
        action="store_true",
        help="Install the LaunchAgent without starting it immediately.",
    )
    status_bar.set_defaults(func=cmd_status_bar)

    install = subparsers.add_parser("install", help="Install selected agent-monitor hooks.")
    install.add_argument("provider", choices=("all", *HOOK_PROVIDERS), nargs="?", default="all")
    install.add_argument("--log-dir", type=Path, help="Directory for provider JSONL files.")
    add_provider_log_arguments(install)
    install.add_argument("--dry-run", action="store_true", help="Show what would change.")
    install.set_defaults(func=cmd_install)

    uninstall = subparsers.add_parser("uninstall", help="Remove selected agent-monitor hooks.")
    uninstall.add_argument("provider", choices=("all", *HOOK_PROVIDERS), nargs="?", default="all")
    add_provider_log_arguments(uninstall)
    uninstall.add_argument("--dry-run", action="store_true", help="Show what would change.")
    uninstall.set_defaults(func=cmd_uninstall)

    hook_log = subparsers.add_parser("hook-log", help="Internal hook logging entry point.")
    hook_log.add_argument("--provider", choices=HOOK_PROVIDERS, required=True)
    hook_log.add_argument("--log", type=Path, required=True)
    hook_log.set_defaults(func=cmd_hook_log)

    hook_client = subparsers.add_parser(
        "hook-client",
        help="Internal bounded hook-ingress entry point.",
    )
    hook_client.add_argument("--provider", choices=HOOK_PROVIDERS, required=True)
    hook_client.add_argument("--log", type=Path, required=True)
    hook_client.set_defaults(func=cmd_hook_client)

    return parser


def add_live_parser(
    subparsers: argparse._SubParsersAction,
    name: str,
    help_text: str,
) -> None:
    live = subparsers.add_parser(name, help=help_text)
    add_status_args(live, include_json=False)
    live.add_argument("--interval", type=float, default=1.0, help="Refresh interval in seconds.")
    live.add_argument(
        "--recent-seconds",
        type=float,
        default=3600.0,
        help="Only show agents updated within this many seconds unless --all is set.",
    )
    live.add_argument("--no-color", action="store_true", help="Disable ANSI color output.")
    live.set_defaults(func=cmd_watch)


def add_watch_run_parser(subparsers: argparse._SubParsersAction) -> None:
    watch_run = subparsers.add_parser(
        "watch-run",
        help="Observe one Claude invocation without changing its saved configuration.",
    )
    watch_run.add_argument(
        "provider",
        choices=("claude",),
        help="Native provider to observe. Claude is the first supported provider.",
    )
    watch_run.add_argument(
        "provider_command",
        nargs=argparse.REMAINDER,
        help="Provider command after --, for example: -- claude -p 'task'.",
    )
    watch_run.set_defaults(func=cmd_watch_run)


def add_demo_parser(subparsers: argparse._SubParsersAction) -> None:
    demo = subparsers.add_parser(
        "demo",
        help="Run a deterministic, no-I/O JR-Bar preview scenario.",
    )
    demo.add_argument(
        "scenario",
        choices=available_scenarios(),
        nargs="?",
        default="overview",
        help="Synthetic scenario to preview. Default: overview.",
    )
    demo.add_argument("--seed", type=int, default=0, help="Deterministic fixture seed.")
    demo.add_argument(
        "--max-events",
        type=int,
        default=128,
        help="Maximum synthetic events, from 1 to 128.",
    )
    demo.add_argument("--json", action="store_true", help="Print a machine-readable summary.")
    demo.set_defaults(func=cmd_demo)


def add_status_args(parser: argparse.ArgumentParser, include_json: bool = True) -> None:
    if include_json:
        parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--all", action="store_true", help="Include stale statuses in table output.")
    parser.add_argument("--stale-after", type=float, default=3600.0, help="Seconds before a status is stale.")
    parser.add_argument(
        "--tool-running-timeout",
        type=float,
        default=0.0,
        help="Seconds before an unmatched Tool Running event is treated as stale; 0 disables this.",
    )
    parser.add_argument("--max-lines", type=int, default=5000, help="Recent JSONL lines to scan per source.")
    add_provider_log_arguments(parser)


def add_provider_log_arguments(parser: argparse.ArgumentParser) -> None:
    for provider in HOOK_PROVIDERS:
        parser.add_argument(
            f"--{provider}-log",
            type=Path,
            help=f"{provider_spec(provider).label} JSONL log path.",
        )


def add_doctor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--export",
        type=Path,
        help="Save one bounded private JSON diagnostic file.",
    )


def add_serve_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--port",
        type=int,
        default=8737,
        help="Loopback port (default 8737).",
    )
    parser.add_argument(
        "--allow-anonymous-status",
        action="store_true",
        help="Allow unauthenticated loopback status reads for legacy clients.",
    )
    parser.add_argument(
        "--phone-glance",
        action="store_true",
        help=(
            "Enable signed /glance.json using the secret in "
            f"{PHONE_GLANCE_SECRET_ENV}."
        ),
    )
    parser.add_argument(
        "--phone-glance-source-id",
        default="sidepulse",
        help="Bounded opaque source identity for signed phone glances.",
    )


def add_glance_arguments(subparsers: argparse._SubParsersAction) -> None:
    glance = subparsers.add_parser(
        "glance",
        aliases=("phone-glance",),
        help="Serve only a signed phone glance on an explicit private IP.",
    )
    glance.add_argument(
        "--bind-address",
        "--bind",
        dest="bind_address",
        required=True,
        help="Private or link-local IP literal to bind, never a hostname or wildcard.",
    )
    glance.add_argument(
        "--port",
        type=_glance_port,
        default=8738,
        help="Private glance port (default 8738).",
    )
    glance.add_argument(
        "--source-id",
        "--phone-glance-source-id",
        dest="glance_source_id",
        default="sidepulse",
        help="Bounded opaque source identity for the signed glance.",
    )
    glance.add_argument(
        "--tls-cert",
        type=Path,
        required=True,
        help="PEM certificate chain trusted by the client, with this private IP in its SAN.",
    )
    glance.add_argument(
        "--tls-key",
        type=Path,
        required=True,
        help="Protected PEM private key for the TLS certificate, without a passphrase.",
    )
    glance.set_defaults(func=cmd_glance)


def _glance_port(value: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("glance port must be an integer") from exc
    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("glance port must be between 1 and 65535")
    return port


def add_effects_parser(subparsers) -> None:
    effects = subparsers.add_parser(
        "effects",
        help="Manage data-only effect and Scene packs, gallery rows, and history.",
    )
    actions = effects.add_subparsers(dest="action", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--store-dir",
            type=Path,
            help="Use an explicit private pack store directory.",
        )
        command.add_argument(
            "--json",
            action="store_true",
            help="Print machine-readable JSON.",
        )
        command.set_defaults(func=cmd_effects)

    install = actions.add_parser("install", help="Install a validated effect pack.")
    install.add_argument("source", type=Path)
    common(install)

    update = actions.add_parser("update", help="Update an installed effect pack.")
    update.add_argument("source", type=Path)
    common(update)

    remove = actions.add_parser("remove", help="Remove an installed effect pack.")
    remove.add_argument("pack_id")
    common(remove)

    duplicate = actions.add_parser(
        "duplicate",
        help="Copy an installed effect pack under a new identity.",
    )
    duplicate.add_argument("pack_id")
    duplicate.add_argument("new_pack_id")
    duplicate.add_argument("new_name")
    common(duplicate)

    rename = actions.add_parser(
        "rename",
        help="Rename an installed effect pack and its identity.",
    )
    rename.add_argument("pack_id")
    rename.add_argument("new_pack_id")
    rename.add_argument("new_name")
    common(rename)

    list_parser = actions.add_parser("list", help="List installed effect packs.")
    common(list_parser)

    inspect = actions.add_parser("inspect", help="Inspect one installed effect pack.")
    inspect.add_argument("pack_id")
    common(inspect)

    export = actions.add_parser("export", help="Export one installed effect pack.")
    export.add_argument("pack_id")
    export.add_argument("target", type=Path, nargs="?")
    common(export)

    gallery = actions.add_parser("gallery", help="Browse installed or built-in effects.")
    gallery.add_argument("--builtin", action="store_true")
    gallery.add_argument("--query", default="")
    common(gallery)

    history = actions.add_parser("history", help="Show content-free effect history.")
    history.add_argument("--history-path", type=Path)
    common(history)

    scene_install = actions.add_parser(
        "scene-install",
        help="Install a validated data-only Scene pack.",
    )
    scene_install.add_argument("source", type=Path)
    common(scene_install)

    scene_update = actions.add_parser(
        "scene-update",
        help="Update an installed Scene pack.",
    )
    scene_update.add_argument("source", type=Path)
    common(scene_update)

    scene_remove = actions.add_parser(
        "scene-remove",
        help="Remove an installed Scene pack.",
    )
    scene_remove.add_argument("pack_id")
    common(scene_remove)

    scene_list = actions.add_parser(
        "scene-list",
        help="List installed Scene packs.",
    )
    common(scene_list)

    scene_inspect = actions.add_parser(
        "scene-inspect",
        help="Inspect one installed Scene pack.",
    )
    scene_inspect.add_argument("pack_id")
    common(scene_inspect)

    scene_export = actions.add_parser(
        "scene-export",
        help="Export one installed Scene pack.",
    )
    scene_export.add_argument("pack_id")
    scene_export.add_argument("target", type=Path, nargs="?")
    common(scene_export)

    scene_preview = actions.add_parser(
        "scene-preview",
        help="Validate and preview a Scene pack without importing it.",
    )
    scene_preview.add_argument("source", type=Path)
    common(scene_preview)


def cmd_serve(args: argparse.Namespace) -> int:
    from .serve import serve

    allow_anonymous_status = bool(getattr(args, "allow_anonymous_status", False))
    raw_status_token = os.environ.get(SERVE_ACCESS_TOKEN_ENV)
    if not allow_anonymous_status and not raw_status_token:
        print(
            f"sidepulse serve: {SERVE_ACCESS_TOKEN_ENV} is required unless "
            "--allow-anonymous-status is set",
            file=sys.stderr,
        )
        return 2
    glance_secret = None
    glance_access_token = None
    if bool(getattr(args, "phone_glance", False)):
        raw_secret = os.environ.get(PHONE_GLANCE_SECRET_ENV)
        if not raw_secret:
            print(
                f"sidepulse serve: {PHONE_GLANCE_SECRET_ENV} is required "
                "with --phone-glance",
                file=sys.stderr,
            )
            return 2
        glance_secret = raw_secret.encode("utf-8")
        raw_glance_access_token = os.environ.get(PHONE_GLANCE_ACCESS_TOKEN_ENV)
        if not raw_glance_access_token:
            print(
                f"sidepulse serve: {PHONE_GLANCE_ACCESS_TOKEN_ENV} is required "
                "with --phone-glance",
                file=sys.stderr,
            )
            return 2
        glance_access_token = raw_glance_access_token.encode("utf-8")
    try:
        serve(
            port=int(getattr(args, "port", 8737)),
            status_access_token=(
                raw_status_token.encode("utf-8") if raw_status_token else None
            ),
            allow_anonymous_status=allow_anonymous_status,
            glance_secret=glance_secret,
            glance_access_token=glance_access_token,
            glance_source_id=str(
                getattr(args, "phone_glance_source_id", "sidepulse")
            ),
        )
    except ValueError:
        print("sidepulse serve: invalid phone glance configuration", file=sys.stderr)
        return 2
    return 0


def cmd_glance(args: argparse.Namespace) -> int:
    from .glance_server import glance_serve, validate_bind_address

    raw_secret = os.environ.get(PHONE_GLANCE_SECRET_ENV)
    if not raw_secret:
        print(
            f"sidepulse glance: {PHONE_GLANCE_SECRET_ENV} is required",
            file=sys.stderr,
        )
        return 2
    raw_access_token = os.environ.get(PHONE_GLANCE_ACCESS_TOKEN_ENV)
    if not raw_access_token:
        print(
            f"sidepulse glance: {PHONE_GLANCE_ACCESS_TOKEN_ENV} is required",
            file=sys.stderr,
        )
        return 2
    try:
        bind_address = validate_bind_address(str(args.bind_address))
        glance_serve(
            bind_address=bind_address,
            port=int(args.port),
            glance_secret=raw_secret.encode("utf-8"),
            access_token=raw_access_token.encode("utf-8"),
            glance_source_id=str(args.glance_source_id),
            tls_cert=getattr(args, "tls_cert", None),
            tls_key=getattr(args, "tls_key", None),
        )
    except (OSError, ValueError):
        print("sidepulse glance: invalid private listener configuration", file=sys.stderr)
        return 2
    return 0


def cmd_effects(args: argparse.Namespace) -> int:
    from .effect_cli import dispatch_effect_command

    return dispatch_effect_command(args)


def cmd_doctor(args: argparse.Namespace) -> int:
    try:
        result = collect_diagnostics()
    except Exception:
        print(
            f"sidepulse doctor: {PUBLIC_COLLECTION_ERROR_MESSAGE}",
            file=sys.stderr,
        )
        return 1

    try:
        output = (
            encode_diagnostic_result(result).decode("ascii")
            if args.json
            else render_diagnostic_result(result) + "\n"
        )
    except Exception:
        print(
            f"sidepulse doctor: {PUBLIC_COLLECTION_ERROR_MESSAGE}",
            file=sys.stderr,
        )
        return 1
    sys.stdout.write(output)

    if args.export is not None:
        try:
            write_diagnostic_export(args.export, result)
        except DoctorExportError as error:
            print(f"sidepulse doctor: {error.public_message}", file=sys.stderr)
            return 1
        print("diagnostic export: saved")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    monitor = monitor_from_args(args)
    snapshot = monitor.snapshot()
    if args.json:
        print(json.dumps(snapshot.to_dict(), indent=2))
    else:
        print(render_snapshot(snapshot, include_stale=args.all))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    monitor = monitor_from_args(args)
    color = should_use_color(args.no_color)
    try:
        if sys.stdout.isatty():
            print("\033[?25l", end="")
        while True:
            snapshot = monitor.snapshot()
            print("\033[2J\033[H", end="")
            print(
                render_watch_dashboard(
                    snapshot,
                    interval=args.interval,
                    recent_seconds=args.recent_seconds,
                    include_stale=args.all,
                    color=color,
                )
            )
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        if sys.stdout.isatty():
            print("\033[?25h", end="")


def cmd_watch_run(args: argparse.Namespace) -> int:
    command = tuple(args.provider_command)
    if command[:1] == ("--",):
        command = command[1:]
    if not command:
        command = (args.provider,)
    observer = hook_command_arguments(
        args.provider,
        default_log_path(args.provider),
    )
    try:
        plan = plan_watch_run(
            args.provider,
            command,
            observer_command=observer,
        )
        result = execute_watch_run(plan)
    except WatchRunPlanError as error:
        print(f"watch-run: refused ({error.refusal.value})", file=sys.stderr)
        return 2
    except OSError:
        print("watch-run: provider invocation could not be started", file=sys.stderr)
        return 127
    return result.exit_code


def cmd_demo(args: argparse.Namespace) -> int:
    try:
        run = build_demo_run(
            args.scenario,
            seed=args.seed,
            max_events=args.max_events,
        )
    except (TypeError, ValueError) as error:
        print(f"demo: {error}", file=sys.stderr)
        return 2
    final = run.final_snapshot
    render = final.to_render_input()
    payload = {
        "scenario": run.scenario.value,
        "seed": run.seed,
        "event_count": len(run.events),
        "event_kinds": [event.kind for event in run.events],
        "snapshot_count": len(run.snapshots),
        "safety": run.safety.as_dict(),
        "final": {
            "at": final.at.isoformat(),
            "agents": list(final.to_projection_rows()),
            "quota_count": len(final.quotas),
            "device_count": len(final.devices),
            "remote_machine_count": len(final.machines),
            "weather": final.weather.condition if final.weather is not None else None,
            "dnd": final.dnd,
            "low_power": final.low_power,
            "light": {
                "mode": render.light_mode,
                "pattern": render.pattern,
                "color": render.color,
                "brightness": render.brightness,
            },
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0
    print(f"JR-Bar demo: {run.scenario.value}")
    print(
        f"  {len(run.events)} events, {len(final.agents)} agents, "
        f"{len(final.devices)} devices, {len(final.machines)} remote machines"
    )
    print(
        f"  light: {render.light_mode} / {render.pattern} / "
        f"{render.brightness}%"
    )
    print("  safety: no hooks, credentials, network, filesystem, or hardware access")
    return 0


def cmd_leds(args: argparse.Namespace) -> int:
    monitor = monitor_from_args(args)
    leds = AgentLedController(
        device_path=args.device,
        file_name=args.file_name,
        dry_run=args.dry_run,
    )

    try:
        while True:
            snapshot = monitor.snapshot()
            result = leds.sync_mode(snapshot.aggregate.mode)
            if result.changed or result.error or args.once:
                print(render_led_sync_result(result, snapshot, dry_run=args.dry_run))
                sys.stdout.flush()

            if args.once:
                return 2 if result.error else 0

            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_status_bar(args: argparse.Namespace) -> int:
    if args.foreground:
        from .status_bar import main as status_bar_main

        return status_bar_main()

    from .status_bar_launch import install_launch_agent, uninstall_launch_agent

    if args.uninstall:
        result = uninstall_launch_agent()
        action = "removed" if result.changed else "already removed"
        print(f"status-bar: {action}")
        print(f"  plist: {result.plist_path}")
        return 0

    result = install_launch_agent(start=not args.no_start)
    action = "installed" if result.changed else "already installed"
    if result.started:
        action += " and started"
    print(f"status-bar: {action}")
    print(f"  plist: {result.plist_path}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    results = install_hook_results(args)
    print_install_results(results, dry_run=args.dry_run)
    return 0


def selected_hook_providers(provider: str) -> tuple[str, ...]:
    return HOOK_PROVIDERS if provider == "all" else (provider,)


def install_hook_results(args: argparse.Namespace):
    providers = selected_hook_providers(args.provider)
    results = []
    for provider in providers:
        log_path = install_log_path(provider, args)
        try:
            results.append(
                install_provider_hooks(provider, log_path=log_path, dry_run=args.dry_run)
            )
        except HookVerificationError as exc:
            # Refused, not failed: nothing was written. Say so and keep
            # going -- one broken interpreter must not hide the other
            # providers' outcomes.
            print(f"{provider}: {exc}")
    return results


def print_install_results(results, *, dry_run: bool) -> None:
    for result in results:
        action = "would update" if dry_run and result.changed else "updated"
        if not result.changed:
            action = "already configured"
        print(f"{result.provider}: {action}")
        print(f"  config: {result.config_path}")
        print(f"  log: {result.log_path}")
        if result.backup_path:
            print(f"  backup: {result.backup_path}")


def cmd_uninstall(args: argparse.Namespace) -> int:
    providers = selected_hook_providers(args.provider)
    results = []
    for provider in providers:
        log_path = uninstall_log_path(provider, args)
        results.append(uninstall_provider_hooks(provider, log_path=log_path, dry_run=args.dry_run))

    for result in results:
        action = "would remove" if args.dry_run and result.changed else "removed"
        if not result.changed:
            action = "already uninstalled"
        print(f"{result.provider}: {action}")
        print(f"  config: {result.config_path}")
        print(f"  log: {result.log_path}")
        if result.backup_path:
            print(f"  backup: {result.backup_path}")
    return 0


def cmd_hook_log(args: argparse.Namespace) -> int:
    return hook_log_main(args.provider, args.log)


def cmd_hook_client(args: argparse.Namespace) -> int:
    return hook_client_main(args.provider, args.log)


def monitor_from_args(args: argparse.Namespace) -> AgentMonitor:
    if any(getattr(args, f"{provider}_log", None) for provider in HOOK_PROVIDERS):
        fallback_sources = default_sources()
        sources = []
        for provider in HOOK_PROVIDERS:
            explicit = getattr(args, f"{provider}_log", None)
            if explicit:
                sources.append(SourceSpec(provider, explicit.expanduser()))
            else:
                sources.extend(source for source in fallback_sources if source.provider == provider)
    else:
        sources = list(default_sources())

    return AgentMonitor(
        sources=sources,
        stale_after_seconds=args.stale_after,
        tool_running_timeout_seconds=args.tool_running_timeout,
        max_lines_per_source=args.max_lines,
    )


def install_log_path(provider: str, args: argparse.Namespace) -> Path:
    explicit = getattr(args, f"{provider}_log", None)
    if explicit:
        return explicit.expanduser()
    log_dir = getattr(args, "log_dir", None)
    if log_dir:
        return log_dir.expanduser() / f"{provider}.jsonl"
    return default_log_path(provider)


def uninstall_log_path(provider: str, args: argparse.Namespace) -> Path | None:
    explicit = getattr(args, f"{provider}_log", None)
    return explicit.expanduser() if explicit else None


def full_watts_from_args(args: argparse.Namespace) -> float | None:
    if getattr(args, "full_watts", None) is not None:
        return parse_full_watts(args.full_watts)
    return load_settings().battery_full_charge_watts


def render_snapshot(snapshot, include_stale: bool = False) -> str:
    lines = []
    aggregate = snapshot.aggregate
    lines.append(
        f"Aggregate: {aggregate.mode_label} "
        f"({aggregate.active_count} active, {aggregate.stale_count} stale)"
    )
    if aggregate.representative:
        lines.append(f"Reason: {describe_status(aggregate.representative, snapshot.collected_at)}")
    lines.append("")
    lines.append("Sources:")
    for source in snapshot.sources:
        marker = "ok" if source.path.exists() else "missing"
        lines.append(f"  {source.provider}: {source.path} [{marker}]")

    statuses = list(snapshot.statuses)
    if include_stale:
        statuses.extend(snapshot.stale_statuses)

    lines.append("")
    lines.append("Agents:")
    if not statuses:
        lines.append("  none")
    else:
        for status in statuses:
            lines.append(f"  {describe_status(status, snapshot.collected_at)}")
    return "\n".join(lines)


def render_watch_dashboard(
    snapshot,
    interval: float,
    recent_seconds: float,
    include_stale: bool = False,
    color: bool = False,
) -> str:
    width = max(80, shutil.get_terminal_size((120, 24)).columns)
    statuses = visible_watch_statuses(snapshot, recent_seconds, include_stale)
    aggregate = snapshot.aggregate
    timestamp = snapshot.collected_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    title = colorize("Agent Monitor", "1", color)
    aggregate_text = colorize(aggregate.mode_label, mode_color(aggregate.mode), color)
    recent_text = "all known agents" if include_stale else f"last {format_duration(recent_seconds)}"

    lines = [
        f"{title}  aggregate={aggregate_text}  agents={len(statuses)}  updated={timestamp}",
        f"refresh={interval:g}s  showing={recent_text}  active={aggregate.active_count}  stale={aggregate.stale_count}  quit=Ctrl-C",
        "=" * min(width, 120),
    ]

    if aggregate.representative:
        lines.append(
            "reason: "
            + colorize(
                describe_status(aggregate.representative, snapshot.collected_at),
                mode_color(aggregate.representative.mode),
                color,
            )
        )
    else:
        lines.append("reason: no recent agent status")

    lines.extend(["", "Sources"])
    for source in snapshot.sources:
        ok = source.path.exists()
        marker = colorize("OK", "32", color) if ok else colorize("MISS", "31", color)
        lines.append(f"  {marker:<4} {source.provider:<7} {source.path}")

    lines.extend(["", "Recently Active Agents"])
    if not statuses:
        lines.append("  none")
        return "\n".join(lines)

    table_width = min(width, 140)
    fixed_width = 9 + 22 + 18 + 20 + 8 + 18 + 16 + 18
    cwd_width = max(18, table_width - fixed_width)
    widths = [9, 22, 18, 20, 8, 18, 16, cwd_width]
    headers = ["Provider", "Agent", "Origin", "Mode", "Age", "Event", "Tool", "Cwd"]
    lines.append(table_separator(widths))
    lines.append(table_row(headers, widths))
    lines.append(table_separator(widths))
    for status in statuses:
        age = format_duration(status.age_seconds(snapshot.collected_at))
        row = [
            status.provider,
            status.display_name,
            status.origin or "-",
            status.mode_label,
            age,
            status.event_name,
            status.tool_name or "-",
            status.cwd or "-",
        ]
        lines.append(table_row(row, widths, mode_index=3, mode=status.mode, color=color))
    lines.append(table_separator(widths))
    return "\n".join(lines)


def visible_watch_statuses(snapshot, recent_seconds: float, include_stale: bool) -> list[AgentStatus]:
    statuses = list(snapshot.statuses)
    if include_stale:
        statuses.extend(snapshot.stale_statuses)
        return sorted(statuses, key=lambda status: (status.priority, -status.updated_at.timestamp()))

    if recent_seconds > 0:
        statuses = [
            status
            for status in statuses
            if status.age_seconds(snapshot.collected_at) <= recent_seconds
        ]

    return sorted(statuses, key=lambda status: (status.priority, -status.updated_at.timestamp()))


def describe_status(status: AgentStatus, now) -> str:
    age = int(status.age_seconds(now))
    stale = " stale" if status.stale else ""
    origin = f" origin={status.origin}" if status.origin else ""
    tool = f" tool={status.tool_name}" if status.tool_name else ""
    cwd = f" cwd={status.cwd}" if status.cwd else ""
    return (
        f"{status.display_name}: {status.mode_label}"
        f" event={status.event_name}{origin}{tool} age={age}s{stale}{cwd}"
    )


def render_led_sync_result(result: LedStatusWrite, snapshot, dry_run: bool = False) -> str:
    if result.error:
        return f"LEDs: {result.label} error={result.error}"

    action = "would write" if dry_run else "wrote"
    target = result.target if result.target is not None else "-"
    lines = [
        (
            f"LEDs: {action} {result.label} to {target} "
            f"(aggregate={snapshot.aggregate.mode_label}, active={snapshot.aggregate.active_count})"
        )
    ]
    if dry_run and result.program:
        lines.append(result.program)
    return "\n".join(lines)


def render_battery_led_result(result, snapshot, dry_run: bool = False) -> str:
    if result.error:
        return f"Battery LEDs: error={result.error}"

    action = "would write" if dry_run else "wrote"
    target = result.target if result.target is not None else "-"
    lines = [
        (
            f"Battery LEDs: {action} {snapshot.percent}% "
            f"({format_watts(snapshot.adapter_power)}/"
            f"{format_watts(snapshot.full_charge_watts)}, "
            f"{snapshot.charge_speed_ratio() * 100:.0f}% speed) to {target}"
        )
    ]
    if dry_run and result.program:
        lines.append(result.program)
    return "\n".join(lines)


def table_separator(widths: list[int]) -> str:
    return "+" + "+".join("-" * (width + 2) for width in widths) + "+"


def table_row(
    cells: list[str],
    widths: list[int],
    mode_index: int | None = None,
    mode=None,
    color: bool = False,
) -> str:
    padded = []
    for index, (cell, width) in enumerate(zip(cells, widths)):
        text = truncate(str(cell), width).ljust(width)
        if mode_index is not None and index == mode_index and mode is not None:
            text = colorize(text, mode_color(mode), color)
        padded.append(f" {text} ")
    return "|" + "|".join(padded) + "|"


def truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "."


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def should_use_color(no_color: bool) -> bool:
    return (
        not no_color
        and "NO_COLOR" not in os.environ
        and sys.stdout.isatty()
    )


def colorize(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def mode_color(mode) -> str:
    return {
        "blocked_error": "31;1",
        "waiting_for_input": "33;1",
        "tool_running": "36;1",
        "long_task_progress": "35;1",
        "working": "34;1",
        "completed": "32;1",
        "idle_ready": "37",
    }.get(getattr(mode, "value", str(mode)), "37")
