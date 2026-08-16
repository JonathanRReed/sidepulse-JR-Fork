"""Bounded CodexBar dashboard-v1 compatibility without credential duplication.

CodexBar remains the source of truth for provider accounting and credentials.
SidePulse consumes only CodexBar's documented display snapshot, either from a
supervised loopback child or the one-shot ``dashboard`` command.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

CODEXBAR_DASHBOARD_SCHEMA_VERSION = 1
CODEXBAR_SOURCE_COMMIT = "355f8443a55c0f6c9511f9969656fde10d68340f"
CODEXBAR_MINIMUM_VERSION = "0.37.2"
CODEXBAR_MAXIMUM_TESTED_VERSION = "0.50.0"
CODEXBAR_POLL_INTERVAL_SECONDS = 60.0
CODEXBAR_COMMAND_TIMEOUT_SECONDS = 35.0
CODEXBAR_START_TIMEOUT_SECONDS = 10.0
CODEXBAR_HTTP_TIMEOUT_SECONDS = 8.0
CODEXBAR_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
CODEXBAR_MAX_STDERR_BYTES = 64 * 1024
CODEXBAR_MAX_PROVIDERS = 128
CODEXBAR_MAX_ACCOUNTS = 64
CODEXBAR_MAX_WINDOWS = 32
CODEXBAR_MAX_TEXT = 512
CODEXBAR_REASON_MISSING = "codexbar_missing"
CODEXBAR_REASON_VERSION = "codexbar_version_unsupported"
CODEXBAR_REASON_SCHEMA = "codexbar_schema_unsupported"
CODEXBAR_REASON_TIMEOUT = "codexbar_timed_out"
CODEXBAR_REASON_START = "codexbar_serve_failed"
CODEXBAR_REASON_FAILED = "codexbar_snapshot_failed"

_REQUIRED_SCHEMA = {
    "snapshot": ["schemaVersion", "generatedAt", "staleAfterSeconds", "host", "providers"],
    "host": ["codexBarVersion", "refreshIntervalSeconds"],
    "provider": ["id", "name", "enabled", "windows", "display", "error", "updatedAt"],
    "window": ["kind", "label", "usedPercent", "remainingPercent", "resetAt"],
}
CODEXBAR_PROTOCOL_FINGERPRINT = "sha256:" + hashlib.sha256(
    json.dumps(_REQUIRED_SCHEMA, separators=(",", ":"), sort_keys=True).encode("utf-8")
).hexdigest()
_VERSION = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
_PROVIDER_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


class CodexBarCompatibilityError(RuntimeError):
    """CodexBar could not provide a compatible bounded snapshot."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class CodexBarWindow:
    kind: str
    label: str
    used_percent: float | None
    remaining_percent: float | None
    reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class CodexBarIdentity:
    account_email: str | None
    plan: str | None


@dataclass(frozen=True, slots=True)
class CodexBarAccount:
    account_id: str
    label: str
    active: bool
    identity: CodexBarIdentity | None
    windows: tuple[CodexBarWindow, ...]
    error_present: bool
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class CodexBarProvider:
    provider_id: str
    name: str
    enabled: bool
    source: str | None
    status_level: str | None
    status_label: str | None
    identity: CodexBarIdentity | None
    windows: tuple[CodexBarWindow, ...]
    accounts: tuple[CodexBarAccount, ...]
    credits_remaining: float | None
    credits_unit: str | None
    cost_today_usd: float | None
    cost_last_30_days_usd: float | None
    sort_key: float
    priority: str | None
    error_present: bool
    updated_at: datetime | None

    @property
    def most_constrained_remaining_percent(self) -> float | None:
        values = [
            window.remaining_percent
            for window in self.windows
            if window.remaining_percent is not None
        ]
        values.extend(
            window.remaining_percent
            for account in self.accounts
            for window in account.windows
            if window.remaining_percent is not None
        )
        return min(values) if values else None


@dataclass(frozen=True, slots=True)
class CodexBarSnapshot:
    generated_at: datetime
    stale_after_seconds: float
    codexbar_version: str | None
    refresh_interval_seconds: float
    providers: tuple[CodexBarProvider, ...]
    connection_mode: str

    def __post_init__(self) -> None:
        if self.connection_mode not in {"serve", "dashboard"}:
            raise ValueError("invalid CodexBar connection mode")

    @property
    def stale(self) -> bool:
        age = abs((datetime.now(timezone.utc) - self.generated_at).total_seconds())
        return age > self.stale_after_seconds

    @property
    def error_count(self) -> int:
        return sum(provider.error_present for provider in self.providers)

    @property
    def most_constrained(self) -> tuple[CodexBarProvider, float] | None:
        candidates = [
            (provider, remaining)
            for provider in self.providers
            if (remaining := provider.most_constrained_remaining_percent) is not None
        ]
        return min(candidates, key=lambda item: item[1]) if candidates else None


@dataclass(frozen=True, slots=True)
class CodexBarObservation:
    snapshot: CodexBarSnapshot | None
    attempted_at: float | None
    reason: str | None
    in_flight: bool

    @property
    def available(self) -> bool:
        return self.snapshot is not None


@dataclass(frozen=True, slots=True)
class _CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _strict_object(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("invalid dashboard object")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("invalid dashboard number")


def _text(value: object, *, maximum: int = CODEXBAR_MAX_TEXT) -> str | None:
    if not isinstance(value, str):
        return None
    collapsed = " ".join(
        "".join(character for character in value if character.isprintable()).split()
    )
    return collapsed[:maximum] if collapsed else None


def _finite(value: object) -> float | None:
    if type(value) not in {int, float}:
        return None
    numeric = float(value)
    return numeric if numeric == numeric and abs(numeric) != float("inf") else None


def _percent(value: object) -> float | None:
    numeric = _finite(value)
    return numeric if numeric is not None and 0.0 <= numeric <= 100.0 else None


def _timestamp(value: object, *, required: bool = False) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        if required:
            raise ValueError("missing dashboard timestamp")
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        if required:
            raise
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _identity(value: object) -> CodexBarIdentity | None:
    if not isinstance(value, dict):
        return None
    email = _text(value.get("accountEmail"), maximum=320)
    plan = _text(value.get("plan"), maximum=160)
    return CodexBarIdentity(email, plan) if email or plan else None


def _windows(value: object) -> tuple[CodexBarWindow, ...]:
    if not isinstance(value, list):
        return ()
    rows = []
    for raw in value[:CODEXBAR_MAX_WINDOWS]:
        if not isinstance(raw, dict):
            continue
        kind = _text(raw.get("kind"), maximum=128)
        label = _text(raw.get("label"), maximum=160)
        if not kind or not label:
            continue
        used = _percent(raw.get("usedPercent"))
        remaining = _percent(raw.get("remainingPercent"))
        if remaining is None and used is not None:
            remaining = 100.0 - used
        if used is None and remaining is not None:
            used = 100.0 - remaining
        rows.append(
            CodexBarWindow(
                kind=kind,
                label=label,
                used_percent=used,
                remaining_percent=remaining,
                reset_at=_timestamp(raw.get("resetAt")),
            )
        )
    return tuple(rows)


def _accounts(value: object) -> tuple[CodexBarAccount, ...]:
    if not isinstance(value, list):
        return ()
    rows = []
    for raw in value[:CODEXBAR_MAX_ACCOUNTS]:
        if not isinstance(raw, dict):
            continue
        account_id = _text(raw.get("id"), maximum=160)
        label = _text(raw.get("label"), maximum=320)
        if not account_id or not label:
            continue
        rows.append(
            CodexBarAccount(
                account_id=account_id,
                label=label,
                active=raw.get("active") is True,
                identity=_identity(raw.get("identity")),
                windows=_windows(raw.get("windows")),
                error_present=raw.get("error") is not None,
                updated_at=_timestamp(raw.get("updatedAt")),
            )
        )
    return tuple(rows)


def _provider(value: object, *, order: int) -> CodexBarProvider | None:
    if not isinstance(value, dict):
        return None
    provider_id = _text(value.get("id"), maximum=128)
    name = _text(value.get("name"), maximum=160)
    if not provider_id or not name or _PROVIDER_ID.fullmatch(provider_id) is None:
        return None
    status = value.get("status") if isinstance(value.get("status"), dict) else {}
    credits = value.get("credits") if isinstance(value.get("credits"), dict) else {}
    cost = value.get("cost") if isinstance(value.get("cost"), dict) else {}
    display = value.get("display") if isinstance(value.get("display"), dict) else {}
    sort_key = _finite(display.get("sortKey"))
    return CodexBarProvider(
        provider_id=provider_id,
        name=name,
        enabled=value.get("enabled") is True,
        source=_text(value.get("source"), maximum=128),
        status_level=_text(status.get("level"), maximum=64),
        status_label=_text(status.get("label"), maximum=160),
        identity=_identity(value.get("identity")),
        windows=_windows(value.get("windows")),
        accounts=_accounts(value.get("accounts")),
        credits_remaining=_finite(credits.get("remaining")),
        credits_unit=_text(credits.get("unit"), maximum=64),
        cost_today_usd=_finite(cost.get("todayUSD")),
        cost_last_30_days_usd=_finite(cost.get("last30DaysUSD")),
        sort_key=float(order if sort_key is None else sort_key),
        priority=_text(display.get("priority"), maximum=64),
        error_present=value.get("error") is not None,
        updated_at=_timestamp(value.get("updatedAt")),
    )


def parse_codexbar_snapshot(
    payload: bytes | str,
    *,
    connection_mode: str,
) -> CodexBarSnapshot:
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    if not raw or len(raw) > CODEXBAR_MAX_SNAPSHOT_BYTES:
        raise CodexBarCompatibilityError(CODEXBAR_REASON_SCHEMA)
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CodexBarCompatibilityError(CODEXBAR_REASON_SCHEMA) from exc
    if not isinstance(document, dict):
        raise CodexBarCompatibilityError(CODEXBAR_REASON_SCHEMA)
    if document.get("schemaVersion") != CODEXBAR_DASHBOARD_SCHEMA_VERSION:
        raise CodexBarCompatibilityError(CODEXBAR_REASON_SCHEMA)
    generated_at = _timestamp(document.get("generatedAt"), required=True)
    stale_after = _finite(document.get("staleAfterSeconds"))
    host = document.get("host")
    providers_raw = document.get("providers")
    if (
        generated_at is None
        or stale_after is None
        or stale_after < 0.0
        or not isinstance(host, dict)
        or not isinstance(providers_raw, list)
        or len(providers_raw) > CODEXBAR_MAX_PROVIDERS
    ):
        raise CodexBarCompatibilityError(CODEXBAR_REASON_SCHEMA)
    providers = tuple(
        provider
        for index, raw_provider in enumerate(providers_raw)
        if (provider := _provider(raw_provider, order=index)) is not None
    )
    providers = tuple(sorted(providers, key=lambda row: (row.sort_key, row.provider_id)))
    refresh_interval = _finite(host.get("refreshIntervalSeconds"))
    return CodexBarSnapshot(
        generated_at=generated_at,
        stale_after_seconds=stale_after,
        codexbar_version=_text(host.get("codexBarVersion"), maximum=64),
        refresh_interval_seconds=max(0.0, refresh_interval or 0.0),
        providers=providers,
        connection_mode=connection_mode,
    )


def _semver(value: str | None) -> tuple[int, int, int] | None:
    match = _VERSION.search(value or "")
    return tuple(int(part) for part in match.groups()) if match else None


def _trusted_binary(path: Path) -> Path | None:
    try:
        resolved = path.expanduser().resolve(strict=True)
        info = resolved.stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or not os.access(resolved, os.X_OK):
        return None
    if info.st_mode & 0o022:
        return None
    return resolved


def discover_codexbar_binary() -> Path | None:
    candidates = [
        Path("/Applications/CodexBar.app/Contents/Helpers/CodexBarCLI"),
        Path.home() / "Applications/CodexBar.app/Contents/Helpers/CodexBarCLI",
        Path("/opt/homebrew/bin/codexbar"),
        Path("/usr/local/bin/codexbar"),
    ]
    located = shutil.which("codexbar")
    if located:
        candidates.insert(0, Path(located))
    for candidate in candidates:
        if (trusted := _trusted_binary(candidate)) is not None:
            return trusted
    return None


def _reader(pipe, buffer: bytearray, maximum: int, overflow: threading.Event) -> None:
    try:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                return
            before = len(buffer)
            if before + len(chunk) > maximum:
                overflow.set()
            if before < maximum:
                buffer.extend(chunk[: maximum - before])
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def run_bounded_command(
    args: tuple[str, ...],
    *,
    timeout: float,
    environment: Mapping[str, str] | None = None,
    maximum_stdout: int = CODEXBAR_MAX_SNAPSHOT_BYTES,
    maximum_stderr: int = CODEXBAR_MAX_STDERR_BYTES,
) -> _CommandResult:
    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(environment) if environment is not None else None,
        close_fds=True,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    readers = (
        threading.Thread(
            target=_reader,
            args=(process.stdout, stdout, maximum_stdout, overflow),
            daemon=True,
        ),
        threading.Thread(
            target=_reader,
            args=(process.stderr, stderr, maximum_stderr, overflow),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + max(0.1, float(timeout))
    timed_out = False
    while process.poll() is None:
        if overflow.is_set() or time.monotonic() >= deadline:
            timed_out = time.monotonic() >= deadline
            process.kill()
            break
        time.sleep(0.02)
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)
    for reader in readers:
        reader.join(timeout=1.0)
    if timed_out:
        raise CodexBarCompatibilityError(CODEXBAR_REASON_TIMEOUT)
    if overflow.is_set():
        raise CodexBarCompatibilityError(CODEXBAR_REASON_SCHEMA)
    return _CommandResult(process.returncode, bytes(stdout), bytes(stderr))


def codexbar_version(
    binary: Path,
    *,
    runner: Callable[..., _CommandResult] = run_bounded_command,
) -> str:
    result = runner((str(binary), "--version"), timeout=5.0, maximum_stdout=4096)
    version = _semver(
        (result.stdout + b"\n" + result.stderr).decode(
            "utf-8",
            errors="replace",
        )
    )
    if result.returncode != 0 or version is None:
        raise CodexBarCompatibilityError(CODEXBAR_REASON_VERSION)
    minimum = _semver(CODEXBAR_MINIMUM_VERSION)
    if minimum is None or version < minimum:
        raise CodexBarCompatibilityError(CODEXBAR_REASON_VERSION)
    return ".".join(str(part) for part in version)


def read_codexbar_dashboard(
    binary: Path,
    *,
    identity: str,
    runner: Callable[..., _CommandResult] = run_bounded_command,
) -> CodexBarSnapshot:
    result = runner(
        (
            str(binary),
            "dashboard",
            "--timeout",
            "30",
            "--identity",
            identity,
        ),
        timeout=CODEXBAR_COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise CodexBarCompatibilityError(CODEXBAR_REASON_FAILED)
    return parse_codexbar_snapshot(result.stdout, connection_mode="dashboard")


def _http_json(
    url: str,
    *,
    token: str | None = None,
    timeout: float = CODEXBAR_HTTP_TIMEOUT_SECONDS,
    maximum_bytes: int = CODEXBAR_MAX_SNAPSHOT_BYTES,
) -> bytes:
    if not url.startswith("http://127.0.0.1:"):
        raise CodexBarCompatibilityError(CODEXBAR_REASON_FAILED)
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise CodexBarCompatibilityError(CODEXBAR_REASON_FAILED)
            body = response.read(maximum_bytes + 1)
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise CodexBarCompatibilityError(CODEXBAR_REASON_FAILED) from exc
    if len(body) > maximum_bytes:
        raise CodexBarCompatibilityError(CODEXBAR_REASON_SCHEMA)
    return body


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class CodexBarServeSupervisor:
    """Own one loopback-only CodexBar child and its ephemeral bearer token."""

    def __init__(
        self,
        binary: Path,
        *,
        identity: str,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        http_reader: Callable[..., bytes] = _http_json,
    ) -> None:
        self._binary = binary
        self._identity = identity
        self._popen = popen
        self._http_reader = http_reader
        self._process: subprocess.Popen | None = None
        self._port: int | None = None
        self._token: str | None = None
        self._version: str | None = None

    @property
    def version(self) -> str | None:
        return self._version

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self.close()
        for _attempt in range(4):
            port = _free_loopback_port()
            token = secrets.token_hex(32)
            environment = {**os.environ, "CODEXBAR_DASHBOARD_TOKEN": token}
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
            deadline = time.monotonic() + CODEXBAR_START_TIMEOUT_SECONDS
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
                        self._version = _text(health.get("version"), maximum=64)
                        return
                except (ValueError, CodexBarCompatibilityError):
                    pass
                time.sleep(0.1)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
        raise CodexBarCompatibilityError(CODEXBAR_REASON_START)

    def snapshot(self) -> CodexBarSnapshot:
        self.start()
        if self._port is None or self._token is None:
            raise CodexBarCompatibilityError(CODEXBAR_REASON_START)
        body = self._http_reader(
            f"http://127.0.0.1:{self._port}/dashboard/v1/snapshot",
            token=self._token,
        )
        snapshot = parse_codexbar_snapshot(body, connection_mode="serve")
        if snapshot.codexbar_version is None and self._version is not None:
            snapshot = replace(snapshot, codexbar_version=self._version)
        return snapshot

    def close(self) -> None:
        process = self._process
        self._process = None
        self._port = None
        self._token = None
        self._version = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass


class CodexBarClient:
    def __init__(
        self,
        *,
        binary: Path | None = None,
        identity: str = "redacted",
        connection_mode: str = "auto",
        dashboard_reader: Callable[..., CodexBarSnapshot] = read_codexbar_dashboard,
        supervisor_factory: Callable[..., CodexBarServeSupervisor] = (
            CodexBarServeSupervisor
        ),
    ) -> None:
        if identity not in {"redacted", "full"}:
            raise ValueError("invalid CodexBar identity mode")
        if connection_mode not in {"auto", "serve", "dashboard"}:
            raise ValueError("invalid CodexBar connection mode")
        self._binary = binary
        self._identity = identity
        self._connection_mode = connection_mode
        self._dashboard_reader = dashboard_reader
        self._supervisor_factory = supervisor_factory
        self._supervisor: CodexBarServeSupervisor | None = None
        self._version: str | None = None

    @property
    def version(self) -> str | None:
        return self._version

    def _resolved_binary(self) -> Path:
        binary = (
            _trusted_binary(self._binary)
            if self._binary is not None
            else discover_codexbar_binary()
        )
        if binary is None:
            raise CodexBarCompatibilityError(CODEXBAR_REASON_MISSING)
        if self._version is None:
            self._version = codexbar_version(binary)
        return binary

    def fetch(self) -> CodexBarSnapshot:
        binary = self._resolved_binary()
        if self._connection_mode in {"auto", "serve"}:
            try:
                if self._supervisor is None:
                    self._supervisor = self._supervisor_factory(
                        binary,
                        identity=self._identity,
                    )
                return self._supervisor.snapshot()
            except CodexBarCompatibilityError:
                if self._connection_mode == "serve":
                    raise
                if self._supervisor is not None:
                    self._supervisor.close()
                    self._supervisor = None
        return self._dashboard_reader(binary, identity=self._identity)

    def close(self) -> None:
        if self._supervisor is not None:
            self._supervisor.close()
            self._supervisor = None


class CodexBarSnapshotService:
    """One worker, latest-known-good snapshot, and bounded polling."""

    def __init__(
        self,
        *,
        client: CodexBarClient | None = None,
        identity: str = "redacted",
        connection_mode: str = "auto",
        monotonic: Callable[[], float] = time.monotonic,
        minimum_interval: float = CODEXBAR_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._client = client or CodexBarClient(
            identity=identity,
            connection_mode=connection_mode,
        )
        self._monotonic = monotonic
        self._minimum_interval = max(1.0, float(minimum_interval))
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = False
        self._in_flight = False
        self._attempted_at: float | None = None
        self._snapshot: CodexBarSnapshot | None = None
        self._reason: str | None = None

    def observation(self) -> CodexBarObservation:
        with self._lock:
            return self._observation_locked()

    def request(
        self,
        callback: Callable[[CodexBarObservation], None] | None = None,
        *,
        force: bool = False,
    ) -> CodexBarObservation:
        now = self._monotonic()
        with self._lock:
            if self._closed:
                return self._observation_locked()
            due = (
                force
                or self._attempted_at is None
                or now - self._attempted_at >= self._minimum_interval
            )
            if not due or self._in_flight:
                return self._observation_locked()
            self._generation += 1
            generation = self._generation
            self._in_flight = True
            self._attempted_at = now
            threading.Thread(
                target=self._run,
                args=(generation, callback),
                name="SidePulseCodexBarCompatibility",
                daemon=True,
            ).start()
            return self._observation_locked()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
        self._client.close()

    def _observation_locked(self) -> CodexBarObservation:
        return CodexBarObservation(
            snapshot=self._snapshot,
            attempted_at=self._attempted_at,
            reason=self._reason,
            in_flight=self._in_flight,
        )

    def _run(
        self,
        generation: int,
        callback: Callable[[CodexBarObservation], None] | None,
    ) -> None:
        snapshot = None
        reason = None
        try:
            snapshot = self._client.fetch()
        except CodexBarCompatibilityError as exc:
            reason = exc.reason
        except Exception:
            reason = CODEXBAR_REASON_FAILED

        observation = None
        with self._lock:
            if self._closed or generation != self._generation:
                self._in_flight = False
                return
            if snapshot is not None:
                self._snapshot = snapshot
                self._reason = None
            else:
                self._reason = reason or CODEXBAR_REASON_FAILED
            self._in_flight = False
            observation = self._observation_locked()
        if callback is not None and observation is not None:
            try:
                callback(observation)
            except Exception:
                pass


__all__ = [
    "CODEXBAR_DASHBOARD_SCHEMA_VERSION",
    "CODEXBAR_MAXIMUM_TESTED_VERSION",
    "CODEXBAR_MINIMUM_VERSION",
    "CODEXBAR_PROTOCOL_FINGERPRINT",
    "CODEXBAR_SOURCE_COMMIT",
    "CodexBarAccount",
    "CodexBarClient",
    "CodexBarCompatibilityError",
    "CodexBarIdentity",
    "CodexBarObservation",
    "CodexBarProvider",
    "CodexBarServeSupervisor",
    "CodexBarSnapshot",
    "CodexBarSnapshotService",
    "CodexBarWindow",
    "codexbar_version",
    "discover_codexbar_binary",
    "parse_codexbar_snapshot",
    "read_codexbar_dashboard",
    "run_bounded_command",
]
