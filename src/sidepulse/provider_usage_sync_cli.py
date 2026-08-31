"""Command-line configuration and refresh for cross-Mac provider usage sync."""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .provider_credential_store import ProviderCredentialStore
from .provider_feature_settings import project_instance_policies
from .provider_usage_pairing import export_pairing_document, import_pairing_document
from .provider_usage_settings import (
    default_provider_usage_settings_path,
    load_provider_usage_settings,
)
from .provider_usage_store import load_provider_usage_state
from .provider_usage_sync_runtime import ProviderSyncRuntime
from .provider_usage_sync_service import ProviderSyncService, ProviderSyncServiceState
from .provider_usage_sync_settings import (
    default_provider_sync_settings_path,
    load_provider_sync_settings,
    save_provider_sync_settings,
)


def _categories(value: str) -> tuple[str, ...]:
    rows = tuple(part.strip() for part in value.split(",") if part.strip())
    if not rows:
        raise argparse.ArgumentTypeError("at least one category is required")
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sidepulse providers sync")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    commands.add_parser("enable")
    commands.add_parser("disable")
    set_device = commands.add_parser("set-device")
    set_device.add_argument("device_id")
    set_categories = commands.add_parser("set-categories")
    set_categories.add_argument("categories", type=_categories)
    add_peer = commands.add_parser("add-peer")
    add_peer.add_argument("peer_id")
    add_peer.add_argument("--host", required=True)
    add_peer.add_argument("--remote-path", required=True)
    add_peer.add_argument("--known-hosts", required=True)
    add_peer.add_argument("--identity-file", required=True)
    add_peer.add_argument("--secret-account", required=True)
    remove_peer = commands.add_parser("remove-peer")
    remove_peer.add_argument("peer_id")
    export_pairing = commands.add_parser("export-pairing")
    export_pairing.add_argument("peer_id")
    export_pairing.add_argument("--output", type=Path, required=True)
    export_pairing.add_argument("--secret-account", required=True)
    import_pairing = commands.add_parser("import-pairing")
    import_pairing.add_argument("--input", type=Path, required=True)
    import_pairing.add_argument("--host", required=True)
    import_pairing.add_argument("--remote-path", required=True)
    import_pairing.add_argument("--known-hosts", required=True)
    import_pairing.add_argument("--identity-file", required=True)
    refresh = commands.add_parser("refresh")
    refresh.add_argument("--json", action="store_true")
    return parser


def _settings_document(settings) -> dict[str, object]:
    return {
        "enabled": settings.enabled,
        "device_id": settings.device_id,
        "categories": list(settings.categories),
        "peer_count": len(settings.peers),
        "peers": [
            {
                "peer_id": peer.peer_id,
                "host": peer.host,
                "remote_path": peer.remote_path,
                "secret_account": peer.secret_account,
            }
            for peer in settings.peers
        ],
    }


def _refresh_document(state: ProviderSyncServiceState) -> dict[str, object]:
    refresh = state.refresh
    if refresh is None:
        return {
            "enabled": False,
            "refreshing": state.refreshing,
            "reason": state.reason,
            "health": [],
        }
    merged = refresh.merged
    return {
        "enabled": refresh.enabled,
        "refreshing": state.refreshing,
        "reason": state.reason,
        "refreshed_at": refresh.refreshed_at,
        "remote_packet_count": len(refresh.remote_packets),
        "health": [
            {
                "peer_id": item.peer_id,
                "reachable": item.reachable,
                "reason": item.reason,
                "generated_at": item.generated_at,
            }
            for item in refresh.health
        ],
        "totals": (
            None
            if merged is None
            else {
                "input_tokens": merged.total_input_tokens,
                "cached_input_tokens": merged.total_cached_input_tokens,
                "output_tokens": merged.total_output_tokens,
                "estimated_cost_usd": merged.total_estimated_cost_usd,
                "cache_savings_usd": merged.total_cache_savings_usd,
            }
        ),
    }


def _write_status(settings, *, output: TextIO, as_json: bool) -> None:
    document = _settings_document(settings)
    if as_json:
        json.dump(document, output, sort_keys=True)
        output.write("\n")
        return
    output.write(
        f"Cross-Mac sync: {'on' if settings.enabled else 'off'}\n"
        f"Device: {settings.device_id or 'not set'}\n"
        f"Categories: {', '.join(settings.categories)}\n"
        f"Peers: {len(settings.peers)}\n"
    )
    for peer in settings.peers:
        output.write(f"  {peer.peer_id} · {peer.host}\n")


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    home: Path | None = None,
    settings_path: Path | None = None,
    credentials=None,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
    usage_state_loader: Callable[[], object] | None = None,
    service_factory: Callable[..., object] = ProviderSyncService,
) -> int:
    args = build_parser().parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    root = Path.home() if home is None else Path(home)
    target = (
        default_provider_sync_settings_path(root)
        if settings_path is None
        else Path(settings_path)
    )
    loaded = load_provider_sync_settings(target)
    settings = loaded.settings
    credential_store = credentials or ProviderCredentialStore()

    if args.command == "status":
        _write_status(settings, output=output, as_json=args.json)
        return 0
    if args.command == "set-device":
        updated = settings.with_device_id(args.device_id)
        save_provider_sync_settings(updated, target, loaded=loaded)
        output.write(f"Sync device set to {args.device_id}.\n")
        return 0
    if args.command == "set-categories":
        updated = settings.with_categories(args.categories)
        save_provider_sync_settings(updated, target, loaded=loaded)
        output.write("Sync categories updated.\n")
        return 0
    if args.command in {"enable", "disable"}:
        updated = settings.with_enabled(args.command == "enable")
        save_provider_sync_settings(updated, target, loaded=loaded)
        output.write(f"Cross-Mac sync {'enabled' if updated.enabled else 'disabled'}.\n")
        return 0
    if args.command == "add-peer":
        updated = settings.with_peer(
            peer_id=args.peer_id,
            host=args.host,
            remote_path=args.remote_path,
            known_hosts=args.known_hosts,
            identity_file=args.identity_file,
            secret_account=args.secret_account,
        )
        save_provider_sync_settings(updated, target, loaded=loaded)
        output.write(f"Peer {args.peer_id} configured.\n")
        return 0
    if args.command == "remove-peer":
        updated = settings.without_peer(args.peer_id)
        save_provider_sync_settings(updated, target, loaded=loaded)
        output.write(f"Peer {args.peer_id} removed.\n")
        return 0
    if args.command == "export-pairing":
        if settings.device_id is None:
            output.write("Set this Mac's sync device id first.\n")
            return 2
        secret = random_bytes(32)
        if not isinstance(secret, bytes) or len(secret) != 32:
            output.write("Could not generate pairing material.\n")
            return 1
        export_pairing_document(
            local_device_id=settings.device_id,
            peer_id=args.peer_id,
            secret_account=args.secret_account,
            target=args.output,
            random_bytes=lambda size: secret if size == 32 else b"",
        )
        credential_store.set(
            "sidepulse-sync",
            args.secret_account,
            base64.b64encode(secret).decode("ascii"),
        )
        output.write(
            f"Pairing document written to {args.output}. Transfer it privately and delete it after import.\n"
        )
        return 0
    if args.command == "import-pairing":
        result = import_pairing_document(args.input, credentials=credential_store)
        updated = settings
        if updated.device_id is None:
            updated = updated.with_device_id(result.peer_id)
        if updated.device_id != result.peer_id:
            output.write(
                "Pairing document targets a different device id. "
                f"Expected {updated.device_id}, got {result.peer_id}.\n"
            )
            return 2
        updated = updated.with_peer(
            peer_id=result.local_device_id,
            host=args.host,
            remote_path=args.remote_path,
            known_hosts=args.known_hosts,
            identity_file=args.identity_file,
            secret_account=result.secret_account,
        )
        save_provider_sync_settings(updated, target, loaded=loaded)
        output.write(f"Paired with {result.local_device_id}. Delete {args.input}.\n")
        return 0
    if args.command == "refresh":
        usage_loader = usage_state_loader or (
            lambda: load_provider_usage_state(
                root / ".local" / "state" / "sidepulse" / "provider-usage.json"
            )
        )
        runtime = ProviderSyncRuntime(
            settings_loader=lambda: load_provider_sync_settings(target),
            sharing_loader=lambda: project_instance_policies(
                load_provider_usage_settings(
                    default_provider_usage_settings_path(root)
                ).settings
            ).sharing,
            credentials=credential_store,
            local_directory=root
            / ".local"
            / "state"
            / "sidepulse"
            / "provider-sync",
        )
        service = service_factory(runtime=runtime)
        try:
            state = service.refresh_now(usage_loader())
        finally:
            service.close()
        document = _refresh_document(state)
        if args.json:
            json.dump(document, output, sort_keys=True)
            output.write("\n")
        else:
            output.write(
                f"Cross-Mac sync refresh: {'enabled' if document['enabled'] else 'disabled'}\n"
            )
            for item in document["health"]:
                output.write(
                    f"  {item['peer_id']}: "
                    f"{'reachable' if item['reachable'] else item['reason']}\n"
                )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
