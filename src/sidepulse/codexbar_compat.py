"""Hardened facade over the reviewed CodexBar dashboard-v1 adapter."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from typing import Callable

from . import _codexbar_compat_legacy as _legacy

_ORIGINAL_PARSE_CODEXBAR_SNAPSHOT = _legacy.parse_codexbar_snapshot
_CommandResult = _legacy._CommandResult
_free_loopback_port = _legacy._free_loopback_port


def _codexbar_environment(*, token: str | None = None) -> dict[str, str]:
    """Forward only process context CodexBar needs, never ambient secrets."""
    allowed = (
        "HOME",
        "USER",
        "LOGNAME",
        "PATH",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "XDG_CONFIG_HOME",
        "CODEXBAR_CONFIG",
    )
    environment = {
        key: value
        for key in allowed
        if (value := os.environ.get(key)) is not None
    }
    if token is not None:
        environment["CODEXBAR_DASHBOARD_TOKEN"] = token
    return environment


def _strict_provider_rows(payload: bytes | str) -> None:
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    if not raw or len(raw) > _legacy.CODEXBAR_MAX_SNAPSHOT_BYTES:
        raise _legacy.CodexBarCompatibilityError(_legacy.CODEXBAR_REASON_SCHEMA)
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_legacy._strict_object,
            parse_constant=_legacy._reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise _legacy.CodexBarCompatibilityError(
            _legacy.CODEXBAR_REASON_SCHEMA
        ) from exc
    if not isinstance(document, dict):
        raise _legacy.CodexBarCompatibilityError(_legacy.CODEXBAR_REASON_SCHEMA)
    providers = document.get("providers")
    if not isinstance(providers, list) or len(providers) > _legacy.CODEXBAR_MAX_PROVIDERS:
        raise _legacy.CodexBarCompatibilityError(_legacy.CODEXBAR_REASON_SCHEMA)
    parsed = []
    for index, raw_provider in enumerate(providers):
        provider = _legacy._provider(raw_provider, order=index)
        if provider is None:
            raise _legacy.CodexBarCompatibilityError(
                _legacy.CODEXBAR_REASON_SCHEMA
            )
        parsed.append(provider)
    if len({provider.provider_id for provider in parsed}) != len(parsed):
        raise _legacy.CodexBarCompatibilityError(_legacy.CODEXBAR_REASON_SCHEMA)


def parse_codexbar_snapshot(
    payload: bytes | str,
    *,
    connection_mode: str,
):
    _strict_provider_rows(payload)
    return _ORIGINAL_PARSE_CODEXBAR_SNAPSHOT(
        payload,
        connection_mode=connection_mode,
    )


def codexbar_version(
    binary: Path,
    *,
    runner: Callable[..., _CommandResult] = _legacy.run_bounded_command,
) -> str:
    result = runner(
        (str(binary), "--version"),
        timeout=5.0,
        environment=_codexbar_environment(),
        maximum_stdout=4096,
    )
    version = _legacy._semver(
        (result.stdout + b"\n" + result.stderr).decode(
            "utf-8",
            errors="replace",
        )
    )
    if result.returncode != 0 or version is None:
        raise _legacy.CodexBarCompatibilityError(_legacy.CODEXBAR_REASON_VERSION)
    minimum = _legacy._semver(_legacy.CODEXBAR_MINIMUM_VERSION)
    if minimum is None or version < minimum:
        raise _legacy.CodexBarCompatibilityError(_legacy.CODEXBAR_REASON_VERSION)
    return ".".join(str(part) for part in version)


def read_codexbar_dashboard(
    binary: Path,
    *,
    identity: str,
    runner: Callable[..., _CommandResult] = _legacy.run_bounded_command,
):
    result = runner(
        (
            str(binary),
            "dashboard",
            "--timeout",
            "30",
            "--identity",
            identity,
        ),
        timeout=_legacy.CODEXBAR_COMMAND_TIMEOUT_SECONDS,
        environment=_codexbar_environment(),
    )
    if result.returncode != 0:
        raise _legacy.CodexBarCompatibilityError(_legacy.CODEXBAR_REASON_FAILED)
    return parse_codexbar_snapshot(
        result.stdout,
        connection_mode="dashboard",
    )


class CodexBarServeSupervisor(_legacy.CodexBarServeSupervisor):
    """Run the loopback child without forwarding unrelated process secrets."""

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self.close()
        for _attempt in range(4):
            port = _free_loopback_port()
            token = secrets.token_hex(32)
            environment = _codexbar_environment(token=token)
            process = self._popen(
                (
                    str(self._binary),
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--refresh-interval",
                    "60",
                    "--request-timeout",
                    "30",
                    "--identity",
                    self._identity,
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                close_fds=True,
                start_new_session=True,
            )
            deadline = time.monotonic() + _legacy.CODEXBAR_START_TIMEOUT_SECONDS
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    health = json.loads(
                        self._http_reader(
                            f"http://127.0.0.1:{port}/health",
                            maximum_bytes=64 * 1024,
                        )
                    )
                    if isinstance(health, dict) and health.get("status") == "ok":
                        self._process = process
                        self._port = port
                        self._token = token
                        self._version = _legacy._text(
                            health.get("version"),
                            maximum=64,
                        )
                        return
                except (ValueError, _legacy.CodexBarCompatibilityError):
                    pass
                time.sleep(0.1)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
        raise _legacy.CodexBarCompatibilityError(_legacy.CODEXBAR_REASON_START)

    def snapshot(self):
        self.start()
        if self._port is None or self._token is None:
            raise _legacy.CodexBarCompatibilityError(_legacy.CODEXBAR_REASON_START)
        body = self._http_reader(
            f"http://127.0.0.1:{self._port}/dashboard/v1/snapshot",
            token=self._token,
        )
        snapshot = parse_codexbar_snapshot(body, connection_mode="serve")
        if snapshot.codexbar_version is None and self._version is not None:
            snapshot = _legacy.replace(
                snapshot,
                codexbar_version=self._version,
            )
        return snapshot


class CodexBarClient(_legacy.CodexBarClient):
    def __init__(
        self,
        *,
        binary: Path | None = None,
        identity: str = "redacted",
        connection_mode: str = "auto",
        dashboard_reader=read_codexbar_dashboard,
        supervisor_factory=CodexBarServeSupervisor,
    ) -> None:
        super().__init__(
            binary=binary,
            identity=identity,
            connection_mode=connection_mode,
            dashboard_reader=dashboard_reader,
            supervisor_factory=supervisor_factory,
        )

    def _resolved_binary(self) -> Path:
        binary = (
            _legacy._trusted_binary(self._binary)
            if self._binary is not None
            else _legacy.discover_codexbar_binary()
        )
        if binary is None:
            raise _legacy.CodexBarCompatibilityError(
                _legacy.CODEXBAR_REASON_MISSING
            )
        if self._version is None:
            self._version = codexbar_version(binary)
        return binary


_legacy.parse_codexbar_snapshot = parse_codexbar_snapshot
_legacy.codexbar_version = codexbar_version
_legacy.read_codexbar_dashboard = read_codexbar_dashboard
_legacy.CodexBarServeSupervisor = CodexBarServeSupervisor
_legacy.CodexBarClient = CodexBarClient
CodexBarSnapshotService = _legacy.CodexBarSnapshotService

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)

__all__ = tuple(sorted(name for name in globals() if not name.startswith("_")))
