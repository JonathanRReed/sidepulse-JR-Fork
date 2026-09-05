"""Privacy-safe provider account labels for presentation surfaces."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .provider_usage_platform import provider_descriptor

_EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+\Z"
)
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_LONG_HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{16,}(?![0-9a-f])", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"(?<!\d)\d{8,}(?!\d)")
_INTERNAL_PREFIX = re.compile(
    r"(?:^|\b)(?:acct|account|org|organization|profile|source|tenant|workspace)"
    r"[-_:][A-Za-z0-9._~:-]{6,}",
    re.IGNORECASE,
)
_PATH_FRAGMENT = re.compile(r"(?:^|[\s(])(?:/|~[/\\]|[A-Za-z]:[/\\])")
_OPAQUE_TOKEN = re.compile(r"[A-Za-z0-9._~:+/=-]{32,}\Z")


@dataclass(frozen=True, slots=True)
class ProviderAccountIdentityPresentation:
    """Only labels that are safe to hand to a renderer."""

    primary_label: str
    account_detail: str | None
    full_label: str
    collision_suffix: str


def human_readable_account_label(value: str | None) -> str | None:
    """Return a bounded human label, never an internal ID or profile path."""

    if not isinstance(value, str):
        return None
    label = " ".join(value.strip().split())
    if not label or len(label) > 96 or any(ord(character) < 32 for character in label):
        return None
    if _EMAIL.fullmatch(label):
        return label
    if _PATH_FRAGMENT.search(label) or _UUID.search(label):
        return None
    if _LONG_HEX.search(label) or _LONG_NUMBER.search(label):
        return None
    if _INTERNAL_PREFIX.search(label):
        return None
    if _OPAQUE_TOKEN.fullmatch(label):
        return None
    if sum(character.isalpha() for character in label) < 2:
        return None
    return label


def configured_user_alias(
    *,
    provider_id: str,
    source_instance_id: str,
    visual_label: str | None,
) -> str | None:
    """Distinguish an explicit visual alias from legacy generated labels."""

    alias = human_readable_account_label(visual_label)
    if alias is None:
        return None
    provider_label = provider_descriptor(provider_id).label
    generated = {provider_label, f"{provider_label} · {source_instance_id}"}
    return None if alias in generated else alias


def _collision_suffix(
    provider_id: str,
    source_instance_id: str,
    account_label: str | None,
) -> str:
    material = "\0".join((provider_id, source_instance_id, account_label or ""))
    return hashlib.blake2s(
        material.encode("utf-8", "surrogatepass"),
        digest_size=4,
        person=b"SidePuls",
    ).hexdigest()


def project_provider_account_identity(
    *,
    provider_id: str,
    source_instance_id: str,
    account_label: str | None,
    user_alias: str | None = None,
    privacy_mode: bool = False,
) -> ProviderAccountIdentityPresentation:
    """Apply the alias, account-label, and opaque-fallback display policy."""

    provider_label = provider_descriptor(provider_id).label
    if privacy_mode:
        return ProviderAccountIdentityPresentation(
            provider_label,
            None,
            provider_label,
            "private",
        )

    suffix = _collision_suffix(provider_id, source_instance_id, account_label)
    fallback = f"{provider_label} #{suffix}"

    alias = human_readable_account_label(user_alias)
    account = human_readable_account_label(account_label)
    if alias is not None:
        full_label = alias if account is None or account == alias else f"{alias} · {account}"
        return ProviderAccountIdentityPresentation(alias, None, full_label, suffix)
    if account is not None:
        return ProviderAccountIdentityPresentation(
            provider_label,
            account,
            f"{provider_label} · {account}",
            suffix,
        )
    if source_instance_id == "default" and account_label is None:
        return ProviderAccountIdentityPresentation(
            provider_label,
            None,
            provider_label,
            suffix,
        )
    return ProviderAccountIdentityPresentation(fallback, None, fallback, suffix)


__all__ = [
    "ProviderAccountIdentityPresentation",
    "configured_user_alias",
    "human_readable_account_label",
    "project_provider_account_identity",
]
