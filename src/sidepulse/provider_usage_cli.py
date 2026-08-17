"""Command-line control surface for SidePulse's native provider platform."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable, TextIO

from .provider_browser_consent import (
    BrowserConsentStore,
    default_browser_consent_path,
    load_browser_consents,
    save_browser_consents,
)
from .provider_browser_import import import_devin_browser_session
from .provider_credential_store import ProviderCredentialStore
from .provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    provider_descriptor,
    provider_descriptors,
    provider_status_line,
)
from .provider_usage_runtime import ProviderUsageService, ProviderUsageState
from .provider_usage_settings import (
    default_provider_usage_settings_path,
    load_provider_usage_settings,
    save_provider_usage_settings,
)
from .provider_usage_store import (
    default_provider_usage_state_path,
    load_provider_usage_state,
    save_provider_usage_state,
)

_BROWSER_SCOPES = {
    "devin": {
        "domains": ("app.devin.ai",),
        "fields": ("auth1_session", "organization"),
    },
    "cursor": {
        "domains": ("cursor.com",),
        "fields": ("session",),
    },
}
_CREDENTIAL_ACCOUNTS = {
    "claude": ("oauth-token",),
    "devin": ("token",),
    "grok": ("token",),
    "openai-api": ("admin-key",),
}


def _on_off(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "on":
        return True
    if normalized == "off":
        return False
    raise argparse.ArgumentTypeError("expected on or off")


def _option(value: str) -> tuple[str, str]:
    key, separator, raw = value.partition("=")
    if not separator or not key or len(key) > 64 or len(raw) > 4096:
        raise argparse.ArgumentTypeError("expected key=value")
    return key, raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sidepulse providers")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="Show native provider state")
    status.add_argument("--json", action="store_true")

    for command in ("enable", "disable"):
        item = commands.add_parser(command)
        item.add_argument("provider", choices=[row.provider_id for row in provider_descriptors()])

    configure = commands.add_parser("configure")
    configure.add_argument("provider", choices=[row.provider_id for row in provider_descriptors()])
    configure.add_argument("--browser-sources", type=_on_off)
    configure.add_argument("--reset-celebrations", type=_on_off)
    configure.add_argument("--threshold-remaining", type=float)
    configure.add_argument("--option", action="append", type=_option, default=[])

    refresh = commands.add_parser("refresh")
    refresh.add_argument(
        "provider",
        nargs="?",
        choices=[row.provider_id for row in provider_descriptors()],
    )
    refresh.add_argument("--json", action="store_true")

    credentials = commands.add_parser("credential")
    credential_commands = credentials.add_subparsers(dest="credential_command", required=True)
    credential_set = credential_commands.add_parser("set")
    credential_set.add_argument("provider", choices=sorted(_CREDENTIAL_ACCOUNTS))
    credential_set.add_argument("account")
    credential_set.add_argument("--stdin", action="store_true", required=True)
    credential_remove = credential_commands.add_parser("remove")
    credential_remove.add_argument("provider", choices=sorted(_CREDENTIAL_ACCOUNTS))
    credential_remove.add_argument("account")
    credential_commands.add_parser("list")

    browser = commands.add_parser("browser-consent")
    browser_commands = browser.add_subparsers(dest="browser_command", required=True)
    grant = browser_commands.add_parser("grant")
    grant.add_argument("provider", choices=sorted(_BROWSER_SCOPES))
    grant.add_argument("--browser", required=True)
    grant.add_argument("--profile", required=True)
    grant.add_argument("--background-repair", action="store_true")
    revoke = browser_commands.add_parser("revoke")
    revoke.add_argument("provider", choices=sorted(_BROWSER_SCOPES))
    revoke.add_argument("--browser", required=True)
    revoke.add_argument("--profile", required=True)
    browser_commands.add_parser("list")
    import_session = browser_commands.add_parser("import")
    import_session.add_argument("provider", choices=("devin",))
    import_session.add_argument("--browser", required=True)
    import_session.add_argument("--profile", required=True)
    import_session.add_argument("--profile-root", type=Path, required=True)

    return parser


def _snapshot_document(snapshot: ProviderUsageSnapshot) -> dict[str, object]:
    return {
        "provider_id": snapshot.provider_id,
        "label": provider_descriptor(snapshot.provider_id).label,
        "account": snapshot.account_label,
        "observed_at": snapshot.observed_at,
        "state": snapshot.state.value,
        "reason": snapshot.reason_code,
        "action": snapshot.action_label,
        "lanes": [
            {
                "id": lane.lane_id,
                "label": lane.label,
                "remaining_percent": lane.remaining_percent,
                "reset_at": lane.reset_at,
                "scope": lane.scope,
                "model": lane.model,
                "feature": lane.feature,
                "bindable": lane.bindable,
                "source": lane.source_id,
            }
            for lane in snapshot.lanes
        ],
        "input_tokens": snapshot.input_tokens,
        "cached_input_tokens": snapshot.cached_input_tokens,
        "output_tokens": snapshot.output_tokens,
        "model_count": snapshot.model_count,
        "estimated_cost_usd": snapshot.estimated_cost_usd,
        "cache_savings_usd": snapshot.cache_savings_usd,
        "credits_remaining": snapshot.credits_remaining,
        "incident": snapshot.incident,
    }


def _state_document(state: ProviderUsageState) -> dict[str, object]:
    return {
        "refreshed_at": state.refreshed_at,
        "next_refresh_at": state.next_refresh_at,
        "refreshing": state.refreshing,
        "providers": [_snapshot_document(snapshot) for snapshot in state.snapshots],
    }


def _print_state(state: ProviderUsageState, *, output: TextIO, as_json: bool) -> None:
    if as_json:
        json.dump(_state_document(state), output, ensure_ascii=False, sort_keys=True)
        output.write("\n")
        return
    if not state.snapshots:
        output.write("Provider usage has not been collected yet. Run `sidepulse providers refresh`.\n")
        return
    for snapshot in state.snapshots:
        output.write(provider_status_line(snapshot) + "\n")
        if snapshot.action_label:
            output.write(f"  Action: {snapshot.action_label}\n")
        for lane in snapshot.lanes:
            remaining = (
                "unknown"
                if lane.remaining_percent is None
                else f"{lane.remaining_percent:.0f}% left"
            )
            output.write(f"  {lane.label}: {remaining}\n")
        token_total = (
            snapshot.input_tokens
            + snapshot.cached_input_tokens
            + snapshot.output_tokens
        )
        if token_total:
            output.write(
                "  Tokens: "
                f"{token_total:,} · {snapshot.model_count} model"
                f"{'s' if snapshot.model_count != 1 else ''}\n"
            )
        if snapshot.estimated_cost_usd is not None:
            output.write(f"  Estimated cost: ${snapshot.estimated_cost_usd:.2f}\n")


def _initial_state(settings, now: float) -> ProviderUsageState:
    snapshots = []
    actions = {
        "codex": "Use Codex once or sign in",
        "claude": "Connect Claude usage",
        "cursor": "Enable Cursor browser access",
        "devin": "Enable Devin browser access",
        "grok": "Run grok login",
        "antigravity": "Open Antigravity or run agy",
        "openai-api": "Add OpenAI Admin key",
    }
    for preference in settings.providers:
        if not preference.enabled:
            state = ProviderSourceState.DISABLED
            reason = None
            action = None
        else:
            state = ProviderSourceState.SOURCE_NOT_FOUND
            reason = "not_collected"
            action = actions[preference.provider_id]
        snapshots.append(
            ProviderUsageSnapshot(
                provider_id=preference.provider_id,
                account_label=None,
                observed_at=now,
                state=state,
                reason_code=reason,
                action_label=action,
                lanes=(),
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
                model_count=0,
                estimated_cost_usd=None,
                cache_savings_usd=None,
                credits_remaining=None,
                incident=None,
            )
        )
    return ProviderUsageState(tuple(snapshots), None, None, False)


def _load_state(
    loader: Callable[[], ProviderUsageState] | None,
    *,
    path: Path,
    settings,
    now: float,
) -> ProviderUsageState:
    state = loader() if loader is not None else load_provider_usage_state(path)
    return state if state.snapshots else _initial_state(settings, now)


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    home: Path | None = None,
    settings_path: Path | None = None,
    consent_path: Path | None = None,
    state_path: Path | None = None,
    credentials=None,
    state_loader: Callable[[], ProviderUsageState] | None = None,
    state_saver: Callable[[ProviderUsageState], object] | None = None,
    service_factory: Callable[..., object] = ProviderUsageService,
    clock: Callable[[], float] = time.time,
) -> int:
    args = build_parser().parse_args(argv)
    input_stream = sys.stdin if stdin is None else stdin
    output = sys.stdout if stdout is None else stdout
    root = Path.home() if home is None else Path(home)
    settings_target = (
        default_provider_usage_settings_path(root)
        if settings_path is None
        else Path(settings_path)
    )
    consent_target = (
        default_browser_consent_path(root)
        if consent_path is None
        else Path(consent_path)
    )
    state_target = (
        default_provider_usage_state_path(root)
        if state_path is None
        else Path(state_path)
    )
    credential_store = credentials or ProviderCredentialStore()
    loaded_settings = load_provider_usage_settings(settings_target)
    settings = loaded_settings.settings

    if args.command == "status":
        state = _load_state(
            state_loader,
            path=state_target,
            settings=settings,
            now=float(clock()),
        )
        _print_state(state, output=output, as_json=args.json)
        return 0

    if args.command in {"enable", "disable"}:
        updated = settings.with_enabled(args.provider, args.command == "enable")
        save_provider_usage_settings(updated, settings_target, loaded=loaded_settings)
        output.write(
            f"{provider_descriptor(args.provider).label} "
            f"{'enabled' if args.command == 'enable' else 'disabled'}.\n"
        )
        return 0

    if args.command == "configure":
        updated = settings
        if args.browser_sources is not None:
            updated = updated.with_browser_sources(args.provider, args.browser_sources)
        if args.reset_celebrations is not None:
            updated = updated.with_reset_celebrations(
                args.provider,
                args.reset_celebrations,
            )
        if args.threshold_remaining is not None:
            updated = updated.with_threshold_remaining(
                args.provider,
                args.threshold_remaining,
            )
        for key, value in args.option:
            updated = updated.with_option(args.provider, key, value)
        save_provider_usage_settings(updated, settings_target, loaded=loaded_settings)
        output.write(f"{provider_descriptor(args.provider).label} settings updated.\n")
        return 0

    if args.command == "credential":
        accounts = _CREDENTIAL_ACCOUNTS.get(getattr(args, "provider", ""), ())
        account = getattr(args, "account", "")
        if args.credential_command in {"set", "remove"} and account not in accounts:
            output.write("Unsupported credential account.\n")
            return 2
        if args.credential_command == "set":
            secret = input_stream.read()
            credential_store.set(args.provider, account, secret.strip())
            output.write(
                f"{provider_descriptor(args.provider).label} credential stored in Keychain.\n"
            )
            return 0
        if args.credential_command == "remove":
            removed = credential_store.delete(args.provider, account)
            output.write("Credential removed.\n" if removed else "Credential was not present.\n")
            return 0
        rows = []
        for provider_id, provider_accounts in _CREDENTIAL_ACCOUNTS.items():
            for provider_account in provider_accounts:
                result = credential_store.get(provider_id, provider_account)
                rows.append(
                    {
                        "provider_id": provider_id,
                        "account": provider_account,
                        "available": bool(result.available),
                    }
                )
        json.dump({"credentials": rows}, output, sort_keys=True)
        output.write("\n")
        return 0

    if args.command == "browser-consent":
        loaded_consents = load_browser_consents(consent_target)
        store = loaded_consents.store
        if args.browser_command == "grant":
            scope = _BROWSER_SCOPES[args.provider]
            updated = store.grant(
                provider_id=args.provider,
                browser=args.browser,
                profile=args.profile,
                domains=scope["domains"],
                fields=scope["fields"],
                background_repair=args.background_repair,
                granted_at=float(clock()),
            )
            save_browser_consents(updated, consent_target, loaded=loaded_consents)
            output.write("Browser consent granted. Import remains a separate action.\n")
            return 0
        if args.browser_command == "revoke":
            updated = store.revoke(args.provider, args.browser, args.profile)
            save_browser_consents(updated, consent_target, loaded=loaded_consents)
            output.write("Browser consent revoked.\n")
            return 0
        if args.browser_command == "list":
            json.dump(
                {
                    "consents": [
                        {
                            "provider_id": item.provider_id,
                            "browser": item.browser,
                            "profile": item.profile,
                            "domains": list(item.domains),
                            "fields": list(item.fields),
                            "background_repair": item.background_repair,
                            "granted_at": item.granted_at,
                        }
                        for item in store.consents
                    ]
                },
                output,
                sort_keys=True,
            )
            output.write("\n")
            return 0
        result = import_devin_browser_session(
            browser=args.browser,
            profile=args.profile,
            profile_root=args.profile_root,
            consents=store,
            credentials=credential_store,
        )
        if result.organization:
            updated_settings = settings.with_option(
                "devin",
                "organization",
                result.organization,
            ).with_browser_sources("devin", True)
            save_provider_usage_settings(
                updated_settings,
                settings_target,
                loaded=loaded_settings,
            )
        output.write(
            f"Devin browser import: {result.state.value}"
            + (f" ({result.reason})" if result.reason else "")
            + "\n"
        )
        return 0 if result.state.value == "imported" else 1

    if args.command == "refresh":
        saver = state_saver or (lambda value: save_provider_usage_state(value, state_target))
        service = service_factory(
            settings_loader=lambda: load_provider_usage_settings(settings_target),
            credentials=credential_store,
            home=root,
            state_loader=(state_loader or (lambda: load_provider_usage_state(state_target))),
            state_saver=None,
        )
        try:
            providers = None if args.provider is None else (args.provider,)
            state = service.refresh_now(providers=providers, force=True)
            saver(state)
        finally:
            service.close()
        _print_state(state, output=output, as_json=args.json)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
