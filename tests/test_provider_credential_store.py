from __future__ import annotations

from sidepulse.provider_credential_store import ProviderCredentialStore


class FakeBackend:
    def __init__(self) -> None:
        self.values = {}

    def set_password(self, service, account, secret) -> None:
        self.values[(service, account)] = secret

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account) -> None:
        if (service, account) not in self.values:
            raise KeyError(account)
        del self.values[(service, account)]


def test_secret_round_trip_uses_provider_scoped_keychain_service() -> None:
    backend = FakeBackend()
    store = ProviderCredentialStore(backend=backend)

    store.set("devin", "token", "auth1_secret")
    result = store.get("devin", "token")

    assert result.secret == "auth1_secret"
    assert result.available is True
    assert ("io.sidepulse.provider.devin", "token") in backend.values
    assert "auth1_secret" not in repr(result)


def test_delete_is_idempotent() -> None:
    backend = FakeBackend()
    store = ProviderCredentialStore(backend=backend)

    assert store.delete("openai-api", "admin-key") is False
    store.set("openai-api", "admin-key", "sk-admin")
    assert store.delete("openai-api", "admin-key") is True
    assert store.delete("openai-api", "admin-key") is False


def test_invalid_provider_or_empty_secret_is_rejected() -> None:
    store = ProviderCredentialStore(backend=FakeBackend())
    for provider, secret in (("CodexBar", "x"), ("devin", "")):
        try:
            store.set(provider, "token", secret)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid credential accepted")
