"""SidePulse-owned Keychain entries for provider credentials.

Every read is explicitly user initiated. Secrets are supplied to the `security`
process over stdin and are never placed in argv, settings, diagnostics, or
object representations.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

SERVICE = "io.sidepulse.provider-credentials"
COMMAND_TIMEOUT_SECONDS = 30.0
_ALLOWED_PROVIDERS = frozenset({"devin", "openai-api"})


def _provider(provider_id: str) -> str:
    if not isinstance(provider_id, str):
        raise ValueError("unknown provider credential")
    value = provider_id.strip().lower()
    if value not in _ALLOWED_PROVIDERS:
        raise ValueError("unknown provider credential")
    return value


@dataclass(frozen=True, slots=True)
class ProviderCredentialResult:
    reason: str
    secret: str | None = None

    def __repr__(self) -> str:
        return (
            "ProviderCredentialResult("
            f"reason={self.reason!r}, secret={'<redacted>' if self.secret else None})"
        )

    def __str__(self) -> str:
        return self.__repr__()


def _run(arguments, *, runner=None, **kwargs):
    return (runner or subprocess.run)(
        arguments,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
        **kwargs,
    )


def write_provider_credential(
    provider_id: str,
    secret: str,
    *,
    runner=None,
) -> bool:
    provider = _provider(provider_id)
    if not isinstance(secret, str) or not secret.strip() or len(secret) > 64 * 1024:
        raise ValueError("invalid provider secret")
    completed = _run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-s",
            SERVICE,
            "-a",
            provider,
            "-w",
        ],
        runner=runner,
        input=secret.strip(),
    )
    return completed.returncode == 0


def read_provider_credential(
    provider_id: str,
    *,
    allow_prompt: bool,
    runner=None,
) -> ProviderCredentialResult:
    provider = _provider(provider_id)
    if not allow_prompt:
        return ProviderCredentialResult("prompt_not_allowed")
    try:
        completed = _run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-w",
                "-s",
                SERVICE,
                "-a",
                provider,
            ],
            runner=runner,
        )
    except (OSError, subprocess.SubprocessError):
        return ProviderCredentialResult("unavailable")
    if completed.returncode != 0:
        return ProviderCredentialResult(
            "denied" if completed.returncode == 128 else "not_found"
        )
    secret = (completed.stdout or "").strip()
    return (
        ProviderCredentialResult("ok", secret)
        if secret
        else ProviderCredentialResult("not_found")
    )


def delete_provider_credential(
    provider_id: str,
    *,
    runner=None,
) -> bool:
    provider = _provider(provider_id)
    try:
        completed = _run(
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-s",
                SERVICE,
                "-a",
                provider,
            ],
            runner=runner,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode in {0, 44}


__all__ = [
    "ProviderCredentialResult",
    "delete_provider_credential",
    "read_provider_credential",
    "write_provider_credential",
]
