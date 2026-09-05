"""Command-line configuration and diagnostics for optional integrations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .integration_compatibility import load_integration_compatibility_manifest
from .integration_settings import (
    IntegrationSettingsError,
    IntegrationSettingsWriteRefusedError,
    default_integration_settings_path,
    load_integration_settings,
    save_integration_settings,
)
from .product_identity import PRODUCT_DISPLAY_NAME
from .t3_compat import read_t3_snapshot

INTEGRATION_NAMES = frozenset({"agent-deck", "creator-micro", "t3code"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sidepulse-integrations",
        description=f"Configure and inspect {PRODUCT_DISPLAY_NAME} compatibility integrations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Show integration configuration.")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    for command, enabled in (("enable", True), ("disable", False)):
        mutation = subparsers.add_parser(
            command,
            help=f"{command.title()} an integration.",
        )
        mutation.add_argument("integration", choices=tuple(sorted(INTEGRATION_NAMES)))
        mutation.set_defaults(func=cmd_enabled, enabled=enabled)

    configure = subparsers.add_parser(
        "configure",
        help="Configure an integration.",
    )
    configure_subparsers = configure.add_subparsers(
        dest="integration",
        required=True,
    )
    t3code = configure_subparsers.add_parser("t3code")
    t3code.add_argument("--base-dir", type=Path)
    t3code.add_argument("--environment-id")
    t3code.add_argument("--clear-base-dir", action="store_true")
    t3code.add_argument("--clear-environment-id", action="store_true")
    t3code.add_argument(
        "--activity-statistics",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Include bounded T3 task-completion activity in usage history.",
    )
    t3code.set_defaults(func=cmd_configure_t3code)
    agent_deck = configure_subparsers.add_parser("agent-deck")
    agent_deck.add_argument("--snapshot-path", type=Path)
    agent_deck.add_argument("--clear-snapshot-path", action="store_true")
    agent_deck.set_defaults(func=cmd_configure_agent_deck)

    probe = subparsers.add_parser(
        "probe",
        help="Run one bounded read-only T3 compatibility probe.",
    )
    probe.add_argument("integration", choices=("t3code",))
    probe.add_argument("--json", action="store_true")
    probe.set_defaults(func=cmd_probe)
    return parser


def _status_document(loaded) -> dict[str, object]:
    settings = loaded.settings
    manifest = load_integration_compatibility_manifest()
    compatibility = manifest.entry("t3code")
    return {
        "settingsPath": str(default_integration_settings_path()),
        "settingsFileReadOnly": loaded.compatibility.read_only,
        "t3code": {
            "enabled": settings.t3code_enabled,
            "activityStatisticsEnabled": (settings.t3code_activity_statistics_enabled),
            "baseDir": settings.t3code_base_dir,
            "environmentId": settings.t3code_environment_id,
            "minimumVersion": (compatibility.minimum_version if compatibility is not None else None),
            "maximumTestedVersion": (compatibility.maximum_tested_version if compatibility is not None else None),
            "connectionMode": (compatibility.connection_mode if compatibility is not None else None),
        },
        "agent-deck": {
            "enabled": settings.agent_deck_enabled,
            "snapshotPath": settings.agent_deck_snapshot_path,
            "connectionMode": "configured-readonly-deck-snapshot-v1",
        },
        "creator-micro": {
            "enabled": settings.creator_micro_enabled,
            "connectionMode": "hidapi-explicit-identity-bound-output",
        },
    }


def _print_status(document: dict[str, object], *, machine: bool) -> None:
    if machine:
        print(json.dumps(document, indent=2, sort_keys=True))
        return
    print(f"settings: {document['settingsPath']}")
    print(
        "settings file read-only: "
        f"{'yes' if document['settingsFileReadOnly'] else 'no'}"
    )
    row = document["t3code"]
    assert isinstance(row, dict)
    print(f"t3code: {'enabled' if row['enabled'] else 'disabled'}")
    for key, value in row.items():
        if key != "enabled":
            print(f"  {key}: {value if value is not None else 'auto'}")


def cmd_status(args: argparse.Namespace) -> int:
    _print_status(_status_document(load_integration_settings()), machine=args.json)
    return 0


def _save_updated(loaded, settings) -> int:
    try:
        path = save_integration_settings(settings, loaded=loaded)
    except (
        IntegrationSettingsError,
        IntegrationSettingsWriteRefusedError,
        OSError,
    ) as exc:
        print(f"sidepulse-integrations: {exc}", file=sys.stderr)
        return 1
    print(f"settings: {path}")
    print(f"apply: restart the {PRODUCT_DISPLAY_NAME} status-bar app")
    return 0


def cmd_enabled(args: argparse.Namespace) -> int:
    loaded = load_integration_settings()
    settings = loaded.settings
    if (
        args.integration == "creator-micro"
        and args.enabled
        and settings.creator_micro_device_serial is None
    ):
        from .creator_micro_hidapi import (
            DeviceIdentityError,
            HidApiTransport,
            select_unique_stable_serial,
        )

        try:
            serial = select_unique_stable_serial(HidApiTransport().enumerate())
        except (DeviceIdentityError, OSError) as exc:
            print(f"sidepulse-integrations: {exc}", file=sys.stderr)
            return 1
        settings = settings.with_creator_micro(
            enabled=True,
            device_serial=serial,
        )
    else:
        settings = settings.with_enabled(args.integration, args.enabled)
    return _save_updated(
        loaded,
        settings,
    )


def cmd_configure_t3code(args: argparse.Namespace) -> int:
    if args.base_dir is not None and args.clear_base_dir:
        print("sidepulse-integrations: choose --base-dir or --clear-base-dir", file=sys.stderr)
        return 2
    if args.environment_id is not None and args.clear_environment_id:
        print(
            "sidepulse-integrations: choose --environment-id or --clear-environment-id",
            file=sys.stderr,
        )
        return 2
    loaded = load_integration_settings()
    settings = loaded.settings
    if args.base_dir is not None or args.clear_base_dir:
        settings = settings.with_t3code(
            base_dir=None if args.clear_base_dir else str(args.base_dir.expanduser()),
        )
    if args.environment_id is not None or args.clear_environment_id:
        settings = settings.with_t3code(
            environment_id=(None if args.clear_environment_id else args.environment_id),
        )
    if args.activity_statistics is not None:
        settings = settings.with_t3code(
            activity_statistics_enabled=args.activity_statistics,
        )
    return _save_updated(loaded, settings)


def cmd_configure_agent_deck(args: argparse.Namespace) -> int:
    if args.snapshot_path is not None and args.clear_snapshot_path:
        print(
            "sidepulse-integrations: choose --snapshot-path or --clear-snapshot-path",
            file=sys.stderr,
        )
        return 2
    loaded = load_integration_settings()
    path = None if args.clear_snapshot_path else args.snapshot_path
    settings = loaded.settings.with_agent_deck(
        snapshot_path=str(path.expanduser()) if path is not None else None,
    )
    return _save_updated(loaded, settings)


def _t3_probe_document(settings) -> dict[str, object]:
    snapshot = read_t3_snapshot(
        base_dir=settings.t3code_base_dir,
        environment_id=settings.t3code_environment_id,
    )
    return {
        "integration": "t3code",
        "available": snapshot.compatible,
        "reason": snapshot.reason,
        "database": str(snapshot.database_path),
        "schemaFingerprint": snapshot.schema_fingerprint,
        "sqliteUserVersion": snapshot.sqlite_user_version,
        "truncated": snapshot.truncated,
        "threadCount": len(snapshot.threads),
        "activeCount": snapshot.active_count,
        "needsUserCount": snapshot.needs_user_count,
        "threads": [
            {
                "threadId": row.thread_id,
                "projectId": row.project_id,
                "projectTitle": row.project_title,
                "threadTitle": row.thread_title,
                "provider": row.provider,
                "providerInstance": row.provider_instance,
                "branch": row.branch,
                "worktreePath": row.worktree_path,
                "model": row.model,
                "runtimeMode": row.runtime_mode,
                "interactionMode": row.interaction_mode,
                "sessionStatus": row.session_status,
                "needsUser": row.needs_user,
                "deepLink": row.deep_link,
                "updatedAt": row.updated_at.isoformat(),
            }
            for row in snapshot.threads
        ],
    }


def _print_probe(document: dict[str, object], *, machine: bool) -> None:
    if machine:
        print(json.dumps(document, indent=2, sort_keys=True))
        return
    print(f"{document['integration']}: {'available' if document['available'] else 'unavailable'}")
    for key in (
        "reason",
        "threadCount",
        "activeCount",
        "needsUserCount",
    ):
        if key in document and document[key] is not None:
            print(f"  {key}: {document[key]}")


def cmd_probe(args: argparse.Namespace) -> int:
    settings = load_integration_settings().settings
    if settings.t3code_enabled is not True:
        _print_probe(
            {
                "integration": "t3code",
                "available": False,
                "reason": "integration_disabled",
            },
            machine=args.json,
        )
        return 1
    try:
        document = _t3_probe_document(settings)
    except (FileNotFoundError, OSError, ValueError) as exc:
        document = {
            "integration": "t3code",
            "available": False,
            "reason": type(exc).__name__,
        }
        _print_probe(document, machine=args.json)
        return 1
    _print_probe(document, machine=args.json)
    return 0 if document.get("available") is True else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
