from __future__ import annotations

from sidepulse.provider_credential_store import ProviderCredentialStore
from sidepulse.provider_instances import ProviderInstanceKey


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


def test_same_provider_credentials_are_scoped_to_the_exact_source_instance() -> None:
    backend = FakeBackend()
    store = ProviderCredentialStore(backend=backend)
    work = ProviderInstanceKey("devin", "work")
    personal = ProviderInstanceKey("devin", "personal")

    store.set_for_instance(work, "token", "work-secret")
    store.set_for_instance(personal, "token", "personal-secret")

    assert store.get_for_instance(work, "token").secret == "work-secret"
    assert store.get_for_instance(personal, "token").secret == "personal-secret"
    assert (
        "io.sidepulse.provider.devin.work",
        "token",
    ) in backend.values
    assert (
        "io.sidepulse.provider.devin.personal",
        "token",
    ) in backend.values


def test_default_instance_credential_methods_reuse_legacy_keychain_identity() -> None:
    backend = FakeBackend()
    store = ProviderCredentialStore(backend=backend)
    default = ProviderInstanceKey("devin", "default")

    store.set("devin", "token", "legacy-secret")

    assert store.get_for_instance(default, "token").secret == "legacy-secret"
