from __future__ import annotations

import subprocess

from sidepulse.provider_credentials import (
    ProviderCredentialResult,
    delete_provider_credential,
    read_provider_credential,
    write_provider_credential,
)


def completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_write_uses_stdin_not_process_arguments() -> None:
    calls = []

    def runner(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return completed()

    assert write_provider_credential("devin", "secret-token", runner=runner)
    arguments, kwargs = calls[0]
    assert "secret-token" not in arguments
    assert kwargs["input"] == "secret-token"
    assert arguments[:4] == ["/usr/bin/security", "add-generic-password", "-U"]
    assert kwargs["timeout"] <= 30


def test_read_is_explicit_and_repr_redacts_secret() -> None:
    calls = []

    def runner(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return completed(stdout="secret-token\n")

    denied = read_provider_credential("devin", allow_prompt=False, runner=runner)
    assert denied.secret is None
    assert denied.reason == "prompt_not_allowed"
    assert calls == []

    result = read_provider_credential("devin", allow_prompt=True, runner=runner)
    assert result.secret == "secret-token"
    assert "secret-token" not in repr(result)
    assert "secret-token" not in str(result)


def test_delete_is_bounded_and_provider_scoped() -> None:
    calls = []

    def runner(arguments, **kwargs):
        calls.append(arguments)
        return completed()

    assert delete_provider_credential("openai-api", runner=runner)
    assert calls[0][-2:] == ["-a", "openai-api"]


def test_unknown_provider_is_rejected() -> None:
    for action in (
        lambda: write_provider_credential("unknown", "x"),
        lambda: read_provider_credential("unknown", allow_prompt=True),
        lambda: delete_provider_credential("unknown"),
    ):
        try:
            action()
        except ValueError:
            pass
        else:
            raise AssertionError("unknown provider credential was accepted")


def test_result_constructor_never_exposes_secret() -> None:
    result = ProviderCredentialResult("ok", "sensitive")
    assert "sensitive" not in repr(result)
