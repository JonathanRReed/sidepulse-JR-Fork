"""SidePulse-owned provider secrets stored in the operating-system keychain."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .provider_instances import DEFAULT_PROVIDER_INSTANCE_SOURCE_ID, ProviderInstanceKey

_PROVIDER = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
_ACCOUNT = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_MAX_SECRET_BYTES = 64 * 1024


class _KeyringBackend:
    def set_password(self, service: str, account: str, secret: str) -> None:
        import keyring

        keyring.set_password(service, account, secret)

    def get_password(self, service: str, account: str) -> str | None:
        import keyring

        return keyring.get_password(service, account)

    def delete_password(self, service: str, account: str) -> None:
        import keyring

        keyring.delete_password(service, account)


@dataclass(frozen=True, slots=True)
class CredentialRead:
    provider_id: str
    account: str
    available: bool
    secret: str | None
    reason: str | None = None
    source_instance_id: str | None = None

    def __repr__(self) -> str:
        held = "<redacted>" if self.secret is not None else "None"
        return (
            "CredentialRead("
            f"provider_id={self.provider_id!r}, account={self.account!r}, "
            f"available={self.available!r}, secret={held}, reason={self.reason!r}, "
            f"source_instance_id={self.source_instance_id!r})"
        )


class ProviderCredentialStore:
    def __init__(self, *, backend=None) -> None:
        self._backend = backend or _KeyringBackend()

    @staticmethod
    def _identity(provider_id: str, account: str) -> tuple[str, str]:
        if not isinstance(provider_id, str) or _PROVIDER.fullmatch(provider_id) is None:
            raise ValueError("invalid provider credential identity")
        if not isinstance(account, str) or _ACCOUNT.fullmatch(account) is None:
            raise ValueError("invalid provider credential account")
        return f"io.sidepulse.provider.{provider_id}", account

    def set(self, provider_id: str, account: str, secret: str) -> None:
        service, normalized_account = self._identity(provider_id, account)
        if (
            not isinstance(secret, str)
            or not secret
            or "\x00" in secret
            or len(secret.encode("utf-8")) > _MAX_SECRET_BYTES
        ):
            raise ValueError("invalid provider credential secret")
        self._backend.set_password(service, normalized_account, secret)

    @staticmethod
    def _instance_identity(
        key: ProviderInstanceKey,
        account: str,
    ) -> tuple[str, str, str, str]:
        if not isinstance(key, ProviderInstanceKey):
            raise ValueError("invalid provider credential instance")
        provider_id, source_instance_id = key.value
        legacy_service, normalized_account = ProviderCredentialStore._identity(
            provider_id,
            account,
        )
        service = (
            legacy_service
            if source_instance_id == DEFAULT_PROVIDER_INSTANCE_SOURCE_ID
            else f"{legacy_service}.{source_instance_id}"
        )
        return provider_id, source_instance_id, service, normalized_account

    @staticmethod
    def _validate_secret(secret: str) -> None:
        if (
            not isinstance(secret, str)
            or not secret
            or "\x00" in secret
            or len(secret.encode("utf-8")) > _MAX_SECRET_BYTES
        ):
            raise ValueError("invalid provider credential secret")

    def set_for_instance(
        self,
        key: ProviderInstanceKey,
        account: str,
        secret: str,
    ) -> None:
        _provider_id, _source_instance_id, service, normalized_account = (
            self._instance_identity(key, account)
        )
        self._validate_secret(secret)
        self._backend.set_password(service, normalized_account, secret)

    def get(self, provider_id: str, account: str) -> CredentialRead:
        service, normalized_account = self._identity(provider_id, account)
        try:
            secret = self._backend.get_password(service, normalized_account)
        except Exception:
            return CredentialRead(
                provider_id,
                normalized_account,
                False,
                None,
                "keychain_unavailable",
            )
        if not isinstance(secret, str) or not secret:
            return CredentialRead(
                provider_id,
                normalized_account,
                False,
                None,
                "credential_not_found",
            )
        return CredentialRead(provider_id, normalized_account, True, secret)

    def get_for_instance(
        self,
        key: ProviderInstanceKey,
        account: str,
    ) -> CredentialRead:
        provider_id, source_instance_id, service, normalized_account = (
            self._instance_identity(key, account)
        )
        try:
            secret = self._backend.get_password(service, normalized_account)
        except Exception:
            return CredentialRead(
                provider_id,
                normalized_account,
                False,
                None,
                "keychain_unavailable",
                source_instance_id,
            )
        if not isinstance(secret, str) or not secret:
            return CredentialRead(
                provider_id,
                normalized_account,
                False,
                None,
                "credential_not_found",
                source_instance_id,
            )
        return CredentialRead(
            provider_id,
            normalized_account,
            True,
            secret,
            None,
            source_instance_id,
        )

    def delete(self, provider_id: str, account: str) -> bool:
        service, normalized_account = self._identity(provider_id, account)
        existing = self.get(provider_id, normalized_account)
        if not existing.available:
            return False
        try:
            self._backend.delete_password(service, normalized_account)
        except Exception:
            return False
        return True

    def delete_for_instance(
        self,
        key: ProviderInstanceKey,
        account: str,
    ) -> bool:
        _provider_id, _source_instance_id, service, normalized_account = (
            self._instance_identity(key, account)
        )
        existing = self.get_for_instance(key, normalized_account)
        if not existing.available:
            return False
        try:
            self._backend.delete_password(service, normalized_account)
        except Exception:
            return False
        return True


__all__ = ["CredentialRead", "ProviderCredentialStore"]
