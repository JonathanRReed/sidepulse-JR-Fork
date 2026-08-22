"""The staged browser-access flow: three honest clicks, never a dead one."""

from __future__ import annotations

from sidepulse.provider_browser_access import (
    handle_provider_usage_action,
    plausible_token,
)


class FakeStore:
    def __init__(self):
        self.saved = {}

    def set(self, provider_id, account, secret):
        self.saved[(provider_id, account)] = secret


def test_import_with_token_on_clipboard_stores_it() -> None:
    store = FakeStore()
    message = handle_provider_usage_action(
        "devin",
        "Import Devin browser session",
        credential_store=store,
        clipboard_reader=lambda: "  devin_api_key_abcdef1234567890  ",
        url_opener=lambda _url: None,
    )
    assert store.saved[("devin", "token")] == "devin_api_key_abcdef1234567890"
    assert "imported" in message


def test_import_without_token_opens_the_page_and_explains() -> None:
    opened = []
    store = FakeStore()
    message = handle_provider_usage_action(
        "devin",
        "Import Devin browser session",
        credential_store=store,
        clipboard_reader=lambda: "some prose that is not a token",
        url_opener=opened.append,
    )
    assert store.saved == {}
    assert opened and opened[0].startswith("https://app.devin.ai")
    assert "clipboard" in message and "click" in message


def test_unrelated_actions_fall_through() -> None:
    assert (
        handle_provider_usage_action(
            "grok", "Retry", credential_store=FakeStore(),
            clipboard_reader=lambda: "", url_opener=lambda _u: None,
        )
        is None
    )


def test_plausible_token_rejects_prose_and_fragments() -> None:
    assert plausible_token("sk-abc123def456ghi789")
    assert not plausible_token("short")
    assert not plausible_token("two words here padding padding")
    assert not plausible_token("line\nbreak" + "x" * 30)


def test_grok_reconnect_names_the_real_fix() -> None:
    message = handle_provider_usage_action(
        "grok", "Run grok login", credential_store=FakeStore(),
        clipboard_reader=lambda: "", url_opener=lambda _u: None,
    )
    assert "grok login" in message


def test_reconnect_clears_the_bad_token_and_reopens_import() -> None:
    """A wrong-but-plausible token must not wedge the provider forever:
    Reconnect clears the credential and re-enters the import stage."""
    opened = []

    class DeletingStore(FakeStore):
        def __init__(self):
            super().__init__()
            self.deleted = []

        def delete(self, provider_id, account):
            self.deleted.append((provider_id, account))

    store = DeletingStore()
    message = handle_provider_usage_action(
        "devin", "Reconnect Devin", credential_store=store,
        clipboard_reader=lambda: "", url_opener=opened.append,
    )
    assert store.deleted == [("devin", "token")]
    assert opened and "devin" in opened[0]
    assert "cleared" in message and "Import Devin" in message
