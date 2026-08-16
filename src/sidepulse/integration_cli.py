"""Command-line configuration and diagnostics for optional integrations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .codexbar_compat import CodexBarClient, CodexBarCompatibilityError
from .integration_settings import (
    CODEXBAR_CONNECTION_MODES,
    CODEXBAR_IDENTITY_MODES,
    INTEGRATION_NAMES,
    IntegrationSettingsError,
    IntegrationSettingsWriteRefusedError,
    default_integration_settings_path,
    load_integration_settings,
    save_integration_settings,
)
from .t3_compat import read_t3_snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sidepulse-integrations",
        description="Configure and inspect SidePulse external integrations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Show integration configuration.")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    for command, enabled in (("enable", True), ("disable", False)):
        mutation = subparsers.add_parser(
            command,
            help=f"{command.title()} one integration.",
        )
        mutation.add_argument("integration", choices=sorted(INTEGRATION_NAMES))
        mutation.set_defaults(func=cmd_enabled, enabled=enabled)

    configure = subparsers.add_parser(
        "configure",
        help="Set integration-specific options.",
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
    t3code.set_defaults(func=cmd_configure_t3code)

    codexbar = configure_subparsers.add_parser("codexbar")
    codexbar.add_argument("--identity", choices=sorted(CODEXBAR_IDENTITY_MODES))
    codexbar.add_argument(
        "--connection-mode",
        choices=sorted(CODEXBAR_CONNECTION_MODES),
    )
    codexbar.set_defaults(func=cmd_configure_codexbar)

    probe = subparsers.add_parser(
        "probe",
        help="Run one bounded read-only compatibility probe.",
    )
    probe.add_argument("integration", choices=sorted(INTEGRATION_NAMES))
    probe.add_argument("--json", action="store_true")
    probe.set_defaults(func=cmd_probe)
    return parser


def _status_document(loaded) -> dict[str, object]:
    settings = loaded.settings
    return {
        "settingsPath": str(default_integration_settings_path()),
        "readOnly": loaded.compatibility.read_only,
        "t3code": {
            "enabled": settings.t3code_enabled,
            "baseDir": settings.t3code_base_dir,
            "environmentId": settings.t3code_environment_id,
        },
        "codexbar": {
            "enabled": settings.codexbar_enabled,
            "identity": settings.codexbar_identity,
            "connectionMode": settings.codexbar_connection_mode,
        },
    }


def _print_status(document: dict[str, object], *, machine: bool) -> None:
    if machine:
        print(json.dumps(document, indent=2, sort_keys=True))
        return
    print(f"settings: {document['settingsPath']}")
    print(f"read-only: {'yes' if document['readOnly'] else 'no'}")
    for name in ("t3code", "codexbar"):
        row = document[name]
        assert isinstance(row, dict)
        print(f"{name}: {'enabled' if row['enabled'] else 'disabled'}")
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
    print("apply: restart the SidePulse status-bar app")
    return 0


def cmd_enabled(args: argparse.Namespace) -> int:
    loaded = load_integration_settings()
    return _save_updated(
        loaded,
        loaded.settings.with_enabled(args.integration, args.enabled),
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
            environment_id=(
                None if args.clear_environment_id else args.environment_id
            ),
        )
    return _save_updated(loaded, settings)


def cmd_configure_codexbar(args: argparse.Namespace) -> int:
    loaded = load_integration_settings()
    settings = loaded.settings.with_codexbar(
        identity=args.identity,
        connection_mode=args.connection_mode,
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


def _codexbar_probe_document(settings) -> dict[str, object]:
    client = CodexBarClient(
        identity=settings.codexbar_identity,
        connection_mode=settings.codexbar_connection_mode,
    )
    try:
        snapshot = client.fetch()
    finally:
        client.close()
    constrained = snapshot.most_constrained
    return {
        "integration": "codexbar",
        "available": True,
        "connectionMode": snapshot.connection_mode,
        "codexBarVersion": snapshot.codexbar_version,
        "generatedAt": snapshot.generated_at.isoformat(),
        "stale": snapshot.stale,
        "providerCount": len(snapshot.providers),
        "errorCount": snapshot.error_count,
        "mostConstrained": (
            {
                "provider": constrained[0].provider_id,
                "remainingPercent": constrained[1],
            }
            if constrained is not None
            else None
        ),
        "providers": [
            {
                "id": row.provider_id,
                "name": row.name,
                "enabled": row.enabled,
                "source": row.source,
                "status": row.status_level,
                "account": (
                    row.identity.account_email if row.identity is not None else None
                ),
                "plan": row.identity.plan if row.identity is not None else None,
                "remainingPercent": row.most_constrained_remaining_percent,
                "creditsRemaining": row.credits_remaining,
                "error": row.error_present,
            }
            for row in snapshot.providers
        ],
    }


def _print_probe(document: dict[str, object], *, machine: bool) -> None:
    if machine:
        print(json.dumps(document, indent=2, sort_keys=True))
        return
    print(f"{document['integration']}: {'available' if document['available'] else 'unavailable'}")
    for key in (
        "reason",
        "connectionMode",
        "codexBarVersion",
        "threadCount",
        "activeCount",
        "needsUserCount",
        "providerCount",
        "errorCount",
        "stale",
    ):
        if key in document and document[key] is not None:
            print(f"  {key}: {document[key]}")


def cmd_probe(args: argparse.Namespace) -> int:
    settings = load_integration_settings().settings
    try:
        document = (
            _t3_probe_document(settings)
            if args.integration == "t3code"
            else _codexbar_probe_document(settings)
        )
    except (CodexBarCompatibilityError, FileNotFoundError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", type(exc).__name__)
        document = {
            "integration": args.integration,
            "available": False,
            "reason": reason,
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
