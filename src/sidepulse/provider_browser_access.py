"""The staged browser-access flow behind provider card action buttons.

"Enable Devin browser access" was a dead button: every non-claude action
fell through to a plain refresh, which changed nothing visible and read
as broken (it was). The flow is now three explicit, honest stages, each
one click, each saying exactly what happens next:

  1. "Enable <X> browser access"   -> flips the provider's
     browser_sources flag and says what the next click will do.
  2. "Import <X> browser session"  -> first click opens the provider's
     own token page and says "copy your key, then click Import again".
  3. second Import click           -> reads the token FROM THE CLIPBOARD
     (an explicit user action, never a background read), stores it in
     the credential store, and forces a refresh.

Organization-style options ("Choose Devin organization") use the same
copy-then-click pattern.
"""

from __future__ import annotations

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


def handle_provider_usage_action(
    provider_id: str,
    action_label: str,
    *,
    credential_store,
    clipboard_reader=_clipboard_text,
    url_opener=_open_url,
) -> str | None:
    """Perform one staged action. Returns the user-facing message, or
    None when this action is not part of the browser-access flow (the
    caller falls through to its previous behavior)."""
    label = str(action_label or "")
    title = provider_id.title()
    if label.startswith("Enable ") and label.endswith("browser access"):
        try:
            loaded = load_provider_usage_settings()
            updated = loaded.settings.with_browser_sources(provider_id, True)
            save_provider_usage_settings(updated, loaded=loaded)
        except Exception:
            return (
                f"{title} does not support browser sources in this build."
            )
        return (
            f"{title} browser access enabled — next, click "
            f"'Import {title} browser session' on the same card."
        )
    if label.startswith("Import ") and "browser session" in label:
        clipboard = clipboard_reader()
        if plausible_token(clipboard):
            try:
                credential_store.set(provider_id, "token", clipboard.strip())
            except Exception as exc:
                return f"Could not store the {title} session: {exc}"
            return f"{title} session imported — refreshing usage now."
        url = PROVIDER_TOKEN_PAGES.get(provider_id)
        if url is not None:
            url_opener(url)
        return (
            f"Copy your {title} API key"
            + (" (page opened)" if url is not None else "")
            + f", then click 'Import {title} browser session' again — "
            "SidePulse reads it from the clipboard only when you click."
        )
    if label.startswith("Choose ") and "organization" in label:
        clipboard = clipboard_reader().strip()
        if clipboard and "\n" not in clipboard and len(clipboard) <= 120:
            try:
                loaded = load_provider_usage_settings()
                updated = loaded.settings.with_option(
                    provider_id, "organization", clipboard
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


def run_provider_usage_action(controller, provider_id: str) -> bool:
    """Controller-level wrapper: resolve the provider's CURRENT action
    label, run the staged flow, surface the message, force a refresh.
    False when the label is not part of this flow (caller falls back)."""
    state = getattr(controller, "provider_usage_state", None)
    snapshot = next(
        (
            item
            for item in getattr(state, "snapshots", ())
            if item.provider_id == provider_id
        ),
        None,
    )
    label = getattr(snapshot, "action_label", None)
    if not label:
        return False
    from .provider_credential_store import ProviderCredentialStore

    message = handle_provider_usage_action(
        provider_id, label, credential_store=ProviderCredentialStore()
    )
    if message is None:
        return False
    try:
        controller.set_settings_message(message)
    except Exception:
        pass
    try:
        controller._request_provider_usage(force=True)
    except Exception:
        pass
    return True


__all__ = [
    "PROVIDER_TOKEN_PAGES",
    "handle_provider_usage_action",
    "plausible_token",
    "run_provider_usage_action",
]
