"""The staged browser-access flow behind provider card action buttons.

"Enable Devin browser access" was a dead button: every non-claude action
fell through to a plain refresh, which changed nothing visible and read
as broken (it was). The flow is now three explicit, honest stages, each
one click, each saying exactly what happens next:

  1. "Enable <X> browser access"   -> flips the provider's
     browser_sources flag and says what the next click will do.
  2. "Import <X> browser session"  -> takes the session the user's own
     browser already holds (browser_session_import), stores it, and
     refreshes. No key, no page, no clipboard.
  3. only if there is no such session -> falls back to the manual
     route: open the provider's token page, then read a pasted key FROM
     THE CLIPBOARD on an explicit second click.

Stage 2 is the point. Asking for an API key was never the design, it
was the absence of one -- "Enable browser access" flipped a flag and
NOTHING in this app ever read a browser, so the manual paste was the
only path that existed. Reported as "Why do I need an API key? The
implementation inside of CodexBar doesn't require an API key."

Organization-style options ("Choose Devin organization") keep the
copy-then-click pattern, but an imported session fills the organization
in on its own, so that stage is normally never reached.
"""

from __future__ import annotations

import time

from .product_identity import PRODUCT_DISPLAY_NAME
from .provider_usage_settings import (
    load_provider_usage_settings,
    save_provider_usage_settings,
)

#: Where each provider keeps the token/session the collector needs.
PROVIDER_TOKEN_PAGES: dict[str, str] = {
    "devin": "https://app.devin.ai/settings/api-keys",
    "cursor": "https://cursor.com/settings",
}

MIN_TOKEN_LENGTH = 16
MAX_TOKEN_LENGTH = 4096


def _clipboard_text() -> str:
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        value = NSPasteboard.generalPasteboard().stringForType_(
            NSPasteboardTypeString
        )
        return str(value or "")
    except Exception:
        return ""


def plausible_token(text: str) -> bool:
    """A pasted credential, not prose: bounded, single-line, no spaces."""
    cleaned = text.strip()
    return (
        MIN_TOKEN_LENGTH <= len(cleaned) <= MAX_TOKEN_LENGTH
        and "\n" not in cleaned
        and " " not in cleaned
    )


def _open_url(url: str) -> None:
    try:
        from AppKit import NSURL, NSWorkspace

        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))
    except Exception:
        pass


def _consented_devin_session(
    *,
    home,
    consents,
    session_reader,
    source_instance_id: str = "default",
    require_background_repair: bool = False,
):
    """Read only when one exact persisted Devin grant selects a profile."""
    matches = []
    for consent in consents.consents:
        if consent.provider_id != "devin":
            continue
        if consent.source_instance_id != source_instance_id:
            continue
        if require_background_repair and not consent.background_repair:
            continue
        if not (
            consents.allows(
                provider_id="devin",
                browser=consent.browser,
                profile=consent.profile,
                domain="app.devin.ai",
                field="auth1_session",
                source_instance_id=source_instance_id,
            )
            and consents.allows(
                provider_id="devin",
                browser=consent.browser,
                profile=consent.profile,
                domain="app.devin.ai",
                field="organization",
                source_instance_id=source_instance_id,
            )
        ):
            continue
        matches.append(consent)
    if len(matches) != 1:
        return None
    selected = matches[0]
    return session_reader(
        home=home,
        browser=selected.browser,
        profile=selected.profile,
    )


def _load_consented_devin_session(
    *,
    source_instance_id: str = "default",
    require_background_repair: bool = False,
):
    """Load one exact persisted grant, then read only its selected profile."""
    try:
        from pathlib import Path

        from .browser_session_import import import_devin_session_from_profile
        from .provider_browser_consent import load_browser_consents

        loaded_consents = load_browser_consents()
        if loaded_consents.read_only:
            return None
        return _consented_devin_session(
            home=Path.home(),
            consents=loaded_consents.store,
            session_reader=import_devin_session_from_profile,
            source_instance_id=source_instance_id,
            require_background_repair=require_background_repair,
        )
    except Exception:
        return None


def _import_browser_session(
    provider_id: str,
    source_instance_id: str = "default",
) -> str | None:
    """Lift the provider's own session out of the user's browser.

    This is what "browser access" was always supposed to mean. Returns
    the success message, or None when there is no session to take -- in
    which case the caller falls back to the manual paste. Devin only for
    now: it is the provider whose session is a plain token in
    Firefox-family local storage.
    """
    if provider_id != "devin":
        return None
    try:
        from .provider_credential_store import ProviderCredentialStore

        session = _load_consented_devin_session(
            source_instance_id=source_instance_id,
        )
        if session is None:
            return None
        credential_store = ProviderCredentialStore()
        try:
            if source_instance_id == "default":
                credential_store.set(provider_id, "token", session.token)
            else:
                from .provider_instances import ProviderInstanceKey

                credential_store.set_for_instance(
                    ProviderInstanceKey(provider_id, source_instance_id),
                    "token",
                    session.token,
                )
        except Exception:
            pass
        loaded = load_provider_usage_settings()
        settings = loaded.settings
        if session.organization:
            settings = settings.with_option(
                provider_id,
                "organization",
                session.organization,
                source_instance_id=source_instance_id,
            )
        if session.internal_organization_id:
            settings = settings.with_option(
                provider_id,
                "organization_id",
                session.internal_organization_id,
                source_instance_id=source_instance_id,
            )
        save_provider_usage_settings(settings, loaded=loaded)
    except Exception:
        return None
    where = session.source_label.split(" ")[0]
    return f"Signed in as your {where} session — no API key needed. Refreshing usage now."


def _signed_out_hint(title: str, url: str) -> str:
    """The token page opens in the DEFAULT browser, which is not
    necessarily where the user is signed in to the provider (reported
    live: page opened in Zen, Devin lives in Chrome, flow dead-ended at
    a login wall). Name the literal address so it can be pasted into
    whichever browser actually holds the session."""
    bare = url.removeprefix("https://").removeprefix("http://")
    return (
        f" If that page isn't signed in, open {bare} in the browser "
        f"you normally use for {title}."
    )


def handle_provider_usage_action(
    provider_id: str,
    action_label: str,
    *,
    credential_store,
    clipboard_reader=_clipboard_text,
    url_opener=_open_url,
    session_importer=None,
    reason_code: str | None = None,
    source_instance_id: str = "default",
) -> str | None:
    """Perform one staged action. Returns the user-facing message, or
    None when this action is not part of the browser-access flow (the
    caller falls through to its previous behavior)."""
    label = str(action_label or "")
    title = provider_id.title()
    if label.startswith("Enable ") and label.endswith("browser access"):
        try:
            loaded = load_provider_usage_settings()
            updated = loaded.settings.with_browser_sources(
                provider_id,
                True,
                source_instance_id=source_instance_id,
            )
            save_provider_usage_settings(updated, loaded=loaded)
        except Exception:
            return (
                f"{title} does not support browser sources in this build."
            )
        return (
            f"{title} browser access enabled. Browser data is still off-limits "
            f"until an exact browser/profile grant exists. Then click "
            f"'Import {title} browser session' on the same card."
        )
    if label.startswith("Import ") and "browser session" in label:
        importer = session_importer or _import_browser_session
        imported = (
            importer(provider_id)
            if source_instance_id == "default"
            else importer(provider_id, source_instance_id)
        )
        if imported is not None:
            return imported
        clipboard = clipboard_reader()
        if plausible_token(clipboard):
            try:
                if source_instance_id == "default":
                    credential_store.set(provider_id, "token", clipboard.strip())
                else:
                    from .provider_instances import ProviderInstanceKey

                    credential_store.set_for_instance(
                        ProviderInstanceKey(provider_id, source_instance_id),
                        "token",
                        clipboard.strip(),
                    )
            except Exception as exc:
                return f"Could not store the {title} session: {exc}"
            return f"{title} session imported — refreshing usage now."
        url = PROVIDER_TOKEN_PAGES.get(provider_id)
        if url is not None:
            url_opener(url)
        return (
            f"Grant one exact browser/profile with `sidepulse providers "
            f"browser-consent grant {provider_id} --browser <browser> "
            "--profile <profile>` to use browser import. Or copy your "
            f"{title} API key"
            + (" (page opened)" if url is not None else "")
            + f", then click 'Import {title} browser session' again — "
            f"{PRODUCT_DISPLAY_NAME} reads it from the clipboard only when you click."
            + (_signed_out_hint(title, url) if url is not None else "")
        )
    if label == f"Reconnect {title}" and provider_id in PROVIDER_TOKEN_PAGES:
        # A wrong-but-plausible token wedged here forever: the label
        # matched nothing, fell through to a bare refresh, and the same
        # 401 came back -- the dead-button pattern all over again.
        # Clear the bad credential and re-enter the import stage.
        try:
            if source_instance_id == "default":
                credential_store.delete(provider_id, "token")
            else:
                from .provider_instances import ProviderInstanceKey

                credential_store.delete_for_instance(
                    ProviderInstanceKey(provider_id, source_instance_id),
                    "token",
                )
        except Exception:
            pass
        # A rotated session is the COMMON case, not a wrong key: Devin
        # reissues its web token routinely. Re-read the browser before
        # sending the user off to hunt for a credential.
        importer = session_importer or _import_browser_session
        reimported = (
            importer(provider_id)
            if source_instance_id == "default"
            else importer(provider_id, source_instance_id)
        )
        if reimported is not None:
            return reimported
        url = PROVIDER_TOKEN_PAGES.get(provider_id)
        if url is not None:
            url_opener(url)
        return (
            f"The stored {title} session was rejected and has been "
            f"cleared. Copy a fresh API key (page opened), then click "
            f"'Import {title} browser session'."
            + (_signed_out_hint(title, url) if url is not None else "")
        )
    if label in ("Run grok login", "Reconnect Grok"):
        # Grok's sign-in belongs to the grok CLI, but "run grok login"
        # was being said to users whose CLI was ALREADY signed in --
        # the collector was wedged on a stale stored token instead.
        # Actually probe the auth file and repair the wedge, then say
        # what was found.
        import time as _time
        from pathlib import Path as _Path

        from .provider_reconnect import repair_grok_credential

        try:
            result = repair_grok_credential(
                credential_store,
                home=_Path.home(),
                now=_time.time(),
                # The collector's last verdict rides along: a 401 means
                # the file token the repair is about to bless is the
                # very token the server just rejected.
                server_rejected=reason_code == "authentication_required",
            )
            return result.message
        except Exception:
            return (
                f"Run `grok login` in a terminal — {PRODUCT_DISPLAY_NAME} reads the "
                "CLI's sign-in automatically on the next refresh."
            )
    if provider_id == "codex" and (
        label.startswith("Last read")
        or "run Codex" in label
        or label.startswith("Use Codex once")
        or label == "Reconnect Codex"
    ):
        # Codex usage comes from the CLI's own transcripts; the honest
        # action is "look again and say what the evidence shows" --
        # including the case where the user DID just run Codex but the
        # run never completed a turn, so nothing was written.
        import time as _time
        from pathlib import Path as _Path

        from .provider_reconnect import codex_activity_report

        try:
            return codex_activity_report(_Path.home(), _time.time())
        except Exception:
            return "Rescanning Codex CLI activity now."
    if provider_id == "antigravity" and (
        "Antigravity" in label or "agy" in label
    ):
        # Antigravity's quota comes from its own loopback service; the
        # only "reconnect" is making sure that service is up. This
        # label used to match nothing -- a dead button (audit).
        return (
            "Antigravity's usage comes from its local service. Open the "
            "Antigravity app (or run `agy` in a terminal) so its "
            f"endpoint is running — {PRODUCT_DISPLAY_NAME} reads it on the next "
            "refresh."
        )
    if provider_id == "openai-api" and (
        "Admin key" in label or label.lower().startswith("reconnect openai")
    ):
        # Clipboard flow, same contract as the Import stage: the key is
        # read only when clicked. This label also used to match nothing.
        clipboard = clipboard_reader()
        if plausible_token(clipboard):
            try:
                credential_store.set("openai-api", "admin-key", clipboard.strip())
            except Exception as exc:
                return f"Could not store the OpenAI Admin key: {exc}"
            return "OpenAI Admin key stored — refreshing usage now."
        return (
            "Copy an OpenAI ADMIN key (platform.openai.com → Settings → "
            f"Admin keys), then click this again — {PRODUCT_DISPLAY_NAME} reads it "
            "from the clipboard only when you click."
        )
    if label.startswith("Choose ") and "organization" in label:
        clipboard = clipboard_reader().strip()
        if clipboard and "\n" not in clipboard and len(clipboard) <= 120:
            try:
                loaded = load_provider_usage_settings()
                updated = loaded.settings.with_option(
                    provider_id,
                    "organization",
                    clipboard,
                    source_instance_id=source_instance_id,
                )
                save_provider_usage_settings(updated, loaded=loaded)
            except Exception as exc:
                return f"Could not save the organization: {exc}"
            return f"{title} organization set to {clipboard!r} — refreshing."
        return (
            f"Copy your {title} organization name, then click "
            f"'Choose {title} organization' again."
        )
    return None


def run_provider_usage_action(
    controller,
    provider_id: str,
    source_instance_id: str = "default",
) -> bool:
    """Controller-level wrapper: resolve the provider's CURRENT action
    label, run the staged flow, surface the message, force a refresh.
    False when the label is not part of this flow (caller falls back)."""
    state = getattr(controller, "provider_usage_state", None)
    snapshot = next(
        (
            item
            for item in getattr(state, "snapshots", ())
            if item.identity == (provider_id, source_instance_id)
        ),
        None,
    )
    label = getattr(snapshot, "action_label", None)
    if not label:
        return False
    from .provider_credential_store import ProviderCredentialStore

    message = handle_provider_usage_action(
        provider_id,
        label,
        credential_store=ProviderCredentialStore(),
        reason_code=getattr(snapshot, "reason_code", None),
        source_instance_id=source_instance_id,
    )
    if message is None:
        return False
    # The message MUST land somewhere visible. set_settings_message's
    # only sink is empty until Settings has been opened once, which is
    # how "Reconnect Grok" spent months answering into the void.
    feedback = getattr(controller, "_show_provider_usage_feedback", None)
    if callable(feedback):
        try:
            feedback(message)
        except Exception:
            pass
    else:
        try:
            controller.set_settings_message(message)
        except Exception:
            pass
    # Arm the outcome watcher before starting the asynchronous refresh so a
    # fast collector cannot publish before the click has an identity.
    try:
        controller._sidepulse_reconnect_watch = (
            provider_id,
            source_instance_id,
            time.time(),
        )
    except Exception:
        pass
    try:
        scope = (
            (provider_id,)
            if source_instance_id == "default"
            else ((provider_id, source_instance_id),)
        )
        controller._request_provider_usage(force=True, providers=scope)
    except Exception:
        pass
    # The click's REAL outcome arrives with the refresh it just forced;
    # arm the one-shot reporter so the banner tells the truth about what
    # actually happened instead of only what was attempted.
    return True


__all__ = [
    "PROVIDER_TOKEN_PAGES",
    "handle_provider_usage_action",
    "plausible_token",
    "run_provider_usage_action",
]
