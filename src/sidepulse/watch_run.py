"""Invocation-scoped native-provider observation.

The Watch This Run boundary deliberately owns no provider configuration.  It
passes Claude a short-lived ``--settings`` document, launches one child, and
removes that document before returning or re-raising an exception.  The module
does not log process arguments, hook payloads, settings, or filesystem names.
"""

from __future__ import annotations

import json
import os
import shlex
import signal as signal_module
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from .provider_contracts import (
    AdapterIdentifier,
    LocalRuntimeSurfaceIdentifier,
    ProductCapability,
    ProductCapabilityInvocation,
    ProviderIdentifier,
    SourceInstanceIdentifier,
)

CLAUDE_WATCH_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "PreCompact",
    "PostCompact",
    "SubagentStop",
    "Stop",
    "SessionEnd",
)

WATCH_RUN_RUNTIME_SURFACE = LocalRuntimeSurfaceIdentifier(
    "local.invocation_scoped_monitoring"
)


class WatchRunProvider(str, Enum):
    CLAUDE = "claude"


class WatchRunRefusal(str, Enum):
    INVALID_PROVIDER = "invalid_provider"
    UNSUPPORTED_PROVIDER = "unsupported_provider"
    INVALID_COMMAND = "invalid_command"
    SETTINGS_OVERRIDE = "settings_override"
    INVALID_OBSERVER = "invalid_observer"


class WatchRunPlanError(ValueError):
    """A Watch This Run request was rejected before any mutation."""

    def __init__(self, refusal: WatchRunRefusal, message: str = "") -> None:
        self.refusal = refusal
        super().__init__(message or refusal.value)


class WatchRunPlanner:
    """Small object facade for callers that prefer dependency injection."""

    @staticmethod
    def plan(
        provider: WatchRunProvider | str,
        command: Sequence[str],
        *,
        observer_command: Sequence[str],
    ) -> WatchRunPlan:
        return plan_watch_run(
            provider,
            command,
            observer_command=observer_command,
        )


class CleanupStatus(str, Enum):
    RESTORED = "restored"
    NOT_ATTEMPTED = "not_attempted"
    FAILED = "failed"


class SignalRestoreStatus(str, Enum):
    RESTORED = "restored"
    NOT_ATTEMPTED = "not_attempted"
    FAILED = "failed"


class CleanupError(str, Enum):
    TEMPORARY_SETTINGS_REMOVE_FAILED = "temporary_settings_remove_failed"
    SIGNAL_HANDLER_RESTORE_FAILED = "signal_handler_restore_failed"


@dataclass(frozen=True, slots=True)
class WatchRunCleanupReceipt:
    """Typed, content-free result for one cleanup operation."""

    resource: str
    status: CleanupStatus
    signal_status: SignalRestoreStatus
    error: CleanupError | None = None

    def __post_init__(self) -> None:
        if self.resource not in {"temporary_settings", "signal_handlers"}:
            raise ValueError("unknown Watch This Run cleanup resource")
        if type(self.status) is not CleanupStatus:
            raise ValueError("invalid cleanup status")
        if type(self.signal_status) is not SignalRestoreStatus:
            raise ValueError("invalid signal status")
        if self.resource == "signal_handlers":
            expected = {
                SignalRestoreStatus.RESTORED: CleanupStatus.RESTORED,
                SignalRestoreStatus.NOT_ATTEMPTED: CleanupStatus.NOT_ATTEMPTED,
                SignalRestoreStatus.FAILED: CleanupError.SIGNAL_HANDLER_RESTORE_FAILED,
            }[self.signal_status]
            expected_error = (
                expected if isinstance(expected, CleanupError) else None
            )
            expected_status = (
                CleanupStatus.FAILED
                if self.signal_status is SignalRestoreStatus.FAILED
                else expected
            )
            if self.status is not expected_status or self.error is not expected_error:
                raise ValueError("invalid signal cleanup error")
        else:
            if self.signal_status is not SignalRestoreStatus.NOT_ATTEMPTED:
                raise ValueError("settings cleanup cannot report signal status")
            expected = (
                CleanupError.TEMPORARY_SETTINGS_REMOVE_FAILED
                if self.status is CleanupStatus.FAILED
                else None
            )
            if self.error is not expected:
                raise ValueError("invalid settings cleanup error")


@dataclass(frozen=True, slots=True)
class WatchRunPlan:
    provider: WatchRunProvider
    capability_invocation: ProductCapabilityInvocation
    command: tuple[str, ...] = field(repr=False)
    observer_command: tuple[str, ...] = field(repr=False)
    events: tuple[str, ...] = CLAUDE_WATCH_EVENTS
    settings_document: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if type(self.provider) is not WatchRunProvider:
            raise ValueError("invalid Watch This Run provider")
        if not (
            type(self.capability_invocation) is ProductCapabilityInvocation
            and self.capability_invocation.product_capability
            is ProductCapability.INVOCATION_SCOPED_MONITORING
            and self.capability_invocation.local_runtime_surface
            == WATCH_RUN_RUNTIME_SURFACE
            and self.capability_invocation.provider_id.value == self.provider.value
        ):
            raise ValueError("invalid Watch This Run capability binding")
        if not self.command or not all(type(part) is str and part for part in self.command):
            raise ValueError("invalid Watch This Run command")
        if not self.observer_command or not all(
            type(part) is str and part and "\x00" not in part for part in self.observer_command
        ):
            raise ValueError("invalid Watch This Run observer command")
        if type(self.events) is not tuple or not self.events:
            raise ValueError("invalid Watch This Run event set")
        if type(self.settings_document) is not str or not self.settings_document:
            raise ValueError("missing Watch This Run settings")

    def command_with_settings(self, settings_path: str) -> tuple[str, ...]:
        if type(settings_path) is not str or not settings_path or "\x00" in settings_path:
            raise ValueError("invalid temporary settings path")
        return (self.command[0], "--settings", settings_path, *self.command[1:])


@dataclass(frozen=True, slots=True)
class WatchRunExecutionReceipt:
    provider: WatchRunProvider
    exit_code: int
    signal_number: int | None
    cleanup_receipts: tuple[WatchRunCleanupReceipt, ...]

    def __post_init__(self) -> None:
        if type(self.provider) is not WatchRunProvider or type(self.exit_code) is not int:
            raise ValueError("invalid Watch This Run execution receipt")
        if self.signal_number is not None and type(self.signal_number) is not int:
            raise ValueError("invalid Watch This Run signal")
        if type(self.cleanup_receipts) is not tuple or not all(
            type(item) is WatchRunCleanupReceipt for item in self.cleanup_receipts
        ):
            raise ValueError("invalid Watch This Run cleanup receipts")


class WatchRunProcess(Protocol):
    def wait(self) -> int: ...

    def send_signal(self, signum: int) -> None: ...

    def terminate(self) -> None: ...


class WatchRunFileSystem(Protocol):
    def create_temporary_settings(self, payload: str) -> str: ...

    def remove_temporary_settings(self, path: str) -> None: ...


class WatchRunSignals(Protocol):
    def getsignal(self, signum: int) -> object: ...

    def signal(self, signum: int, handler: Callable[[int, Any], None]) -> object: ...


class LocalWatchRunFileSystem:
    """Private temporary-file seam used by the real executor."""

    def create_temporary_settings(self, payload: str) -> str:
        fd, path = tempfile.mkstemp(prefix=".sidepulse-watch-", suffix=".json")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        return path

    def remove_temporary_settings(self, path: str) -> None:
        os.unlink(path)


def _argv(value: object, *, refusal: WatchRunRefusal) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WatchRunPlanError(refusal, "argument vector must be a sequence")
    parts = tuple(value)
    if not parts or not all(type(part) is str and part and "\x00" not in part for part in parts):
        raise WatchRunPlanError(refusal, "argument vector is invalid")
    return parts


def _settings_document(events: tuple[str, ...], observer_command: tuple[str, ...]) -> str:
    command = shlex.join(observer_command)
    hooks = {
        event: [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]
        for event in events
    }
    return json.dumps({"hooks": hooks}, sort_keys=True, separators=(",", ":"))


def plan_watch_run(
    provider: WatchRunProvider | str,
    command: Sequence[str],
    *,
    observer_command: Sequence[str],
) -> WatchRunPlan:
    """Validate and plan one Claude invocation without touching the host."""
    if isinstance(provider, WatchRunProvider):
        selected = provider
    elif type(provider) is str:
        try:
            selected = WatchRunProvider(provider)
        except ValueError as error:
            refusal = (
                WatchRunRefusal.UNSUPPORTED_PROVIDER
                if provider
                else WatchRunRefusal.INVALID_PROVIDER
            )
            raise WatchRunPlanError(refusal) from error
    else:
        raise WatchRunPlanError(WatchRunRefusal.INVALID_PROVIDER)

    argv = _argv(command, refusal=WatchRunRefusal.INVALID_COMMAND)
    observer = _argv(observer_command, refusal=WatchRunRefusal.INVALID_OBSERVER)
    if Path(argv[0]).name not in {"claude", "claude-code"}:
        raise WatchRunPlanError(WatchRunRefusal.INVALID_COMMAND)
    if any(part == "--settings" or part.startswith("--settings=") for part in argv):
        raise WatchRunPlanError(WatchRunRefusal.SETTINGS_OVERRIDE)
    document = _settings_document(CLAUDE_WATCH_EVENTS, observer)
    invocation = ProductCapabilityInvocation(
        product_capability=ProductCapability.INVOCATION_SCOPED_MONITORING,
        provider_id=ProviderIdentifier(selected.value),
        adapter_id=AdapterIdentifier("hooks"),
        source_instance_id=SourceInstanceIdentifier("invocation"),
        local_runtime_surface=WATCH_RUN_RUNTIME_SURFACE,
    )
    return WatchRunPlan(
        selected,
        invocation,
        argv,
        observer,
        settings_document=document,
    )


def _default_process_factory(argv: Sequence[str]) -> WatchRunProcess:
    return subprocess.Popen(tuple(argv))


def _restore_signals(
    signals: WatchRunSignals,
    originals: dict[int, object],
) -> WatchRunCleanupReceipt:
    failed = False
    for number, handler in originals.items():
        try:
            signals.signal(number, handler)  # type: ignore[arg-type]
        except BaseException:
            failed = True
    return WatchRunCleanupReceipt(
        "signal_handlers",
        CleanupStatus.FAILED if failed else CleanupStatus.RESTORED,
        SignalRestoreStatus.FAILED if failed else SignalRestoreStatus.RESTORED,
        CleanupError.SIGNAL_HANDLER_RESTORE_FAILED if failed else None,
    )


def execute_watch_run(
    plan: WatchRunPlan,
    *,
    process_factory: Callable[[Sequence[str]], WatchRunProcess] = _default_process_factory,
    file_system: WatchRunFileSystem | None = None,
    signals: WatchRunSignals | None = None,
) -> WatchRunExecutionReceipt:
    """Run the planned child and always restore invocation-local state.

    Child failures are ordinary results, including negative signal return
    codes.  Exceptions from the wrapper or child wait are re-raised after the
    child is asked to terminate and cleanup is attempted.
    """
    if type(plan) is not WatchRunPlan:
        raise TypeError("expected WatchRunPlan")
    fs = file_system or LocalWatchRunFileSystem()
    signal_api = signals or signal_module
    process: WatchRunProcess | None = None
    settings_path: str | None = None
    originals: dict[int, object] = {}
    installed = False
    received_signal: int | None = None
    cleanup: list[WatchRunCleanupReceipt] = []
    execute_watch_run.last_cleanup_receipts = ()

    def forward(signum: int, _frame: Any) -> None:
        nonlocal received_signal
        received_signal = signum
        if process is not None:
            process.send_signal(signum)

    try:
        settings_path = fs.create_temporary_settings(plan.settings_document)
        if type(settings_path) is not str or not settings_path or "\x00" in settings_path:
            raise RuntimeError("temporary settings seam returned an invalid handle")
        for number in (signal_module.SIGINT, signal_module.SIGTERM):
            originals[number] = signal_api.getsignal(number)
            signal_api.signal(number, forward)
        installed = True
        process = process_factory(plan.command_with_settings(settings_path))
        exit_code = process.wait()
        if type(exit_code) is not int:
            raise RuntimeError("child process returned an invalid exit code")
    except BaseException as error:
        if process is not None:
            try:
                process.terminate()
            except BaseException:
                pass
            try:
                process.wait()
            except BaseException:
                pass
        try:
            if installed or originals:
                cleanup.append(_restore_signals(signal_api, originals))
        finally:
            if settings_path is not None:
                try:
                    fs.remove_temporary_settings(settings_path)
                    cleanup.append(
                        WatchRunCleanupReceipt(
                            "temporary_settings",
                            CleanupStatus.RESTORED,
                            SignalRestoreStatus.NOT_ATTEMPTED,
                        )
                    )
                except BaseException:
                    cleanup.append(
                        WatchRunCleanupReceipt(
                            "temporary_settings",
                            CleanupStatus.FAILED,
                            SignalRestoreStatus.NOT_ATTEMPTED,
                            CleanupError.TEMPORARY_SETTINGS_REMOVE_FAILED,
                        )
                    )
            execute_watch_run.last_cleanup_receipts = tuple(cleanup)
            try:
                setattr(error, "watch_run_cleanup_receipts", tuple(cleanup))
            except BaseException:
                pass
        raise
    else:
        if installed or originals:
            cleanup.append(_restore_signals(signal_api, originals))
        if settings_path is not None:
            try:
                fs.remove_temporary_settings(settings_path)
                cleanup.append(
                    WatchRunCleanupReceipt(
                        "temporary_settings",
                        CleanupStatus.RESTORED,
                        SignalRestoreStatus.NOT_ATTEMPTED,
                    )
                )
            except BaseException:
                cleanup.append(
                    WatchRunCleanupReceipt(
                        "temporary_settings",
                        CleanupStatus.FAILED,
                        SignalRestoreStatus.NOT_ATTEMPTED,
                        CleanupError.TEMPORARY_SETTINGS_REMOVE_FAILED,
                    )
                )
        result = WatchRunExecutionReceipt(
            plan.provider,
            exit_code,
            received_signal,
            tuple(cleanup),
        )
        execute_watch_run.last_cleanup_receipts = result.cleanup_receipts
        return result


execute_watch_run.last_cleanup_receipts: tuple[WatchRunCleanupReceipt, ...] = ()


class WatchRunExecutor:
    """Object facade retaining the last cleanup receipt for exception paths."""

    def __init__(
        self,
        *,
        process_factory: Callable[[Sequence[str]], WatchRunProcess] = _default_process_factory,
        file_system: WatchRunFileSystem | None = None,
        signals: WatchRunSignals | None = None,
    ) -> None:
        self.process_factory = process_factory
        self.file_system = file_system
        self.signals = signals
        self.last_cleanup_receipts: tuple[WatchRunCleanupReceipt, ...] = ()

    def execute(self, plan: WatchRunPlan) -> WatchRunExecutionReceipt:
        try:
            return execute_watch_run(
                plan,
                process_factory=self.process_factory,
                file_system=self.file_system,
                signals=self.signals,
            )
        finally:
            self.last_cleanup_receipts = execute_watch_run.last_cleanup_receipts


__all__ = [
    "CLAUDE_WATCH_EVENTS",
    "WATCH_RUN_RUNTIME_SURFACE",
    "CleanupError",
    "CleanupStatus",
    "LocalWatchRunFileSystem",
    "SignalRestoreStatus",
    "WatchRunCleanupReceipt",
    "WatchRunExecutionReceipt",
    "WatchRunExecutor",
    "WatchRunPlan",
    "WatchRunPlanError",
    "WatchRunPlanner",
    "WatchRunProvider",
    "WatchRunRefusal",
    "execute_watch_run",
    "plan_watch_run",
]
