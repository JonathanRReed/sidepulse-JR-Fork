from __future__ import annotations

import signal

import pytest

from sidepulse.provider_contracts import ProductCapability
from sidepulse.watch_run import (
    CleanupStatus,
    SignalRestoreStatus,
    WatchRunCleanupReceipt,
    WatchRunPlanError,
    WatchRunProvider,
    execute_watch_run,
    plan_watch_run,
)


class FakeFileSystem:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []
        self.removed: list[str] = []

    def create_temporary_settings(self, payload: str) -> str:
        path = f"/private/tmp/watch-{len(self.created)}.json"
        self.created.append((path, payload))
        return path

    def remove_temporary_settings(self, path: str) -> None:
        self.removed.append(path)


class FakeSignals:
    def __init__(self) -> None:
        self.handlers = {signal.SIGINT: "old-int", signal.SIGTERM: "old-term"}
        self.calls: list[tuple[int, object]] = []

    def getsignal(self, signum: int) -> object:
        return self.handlers[signum]

    def signal(self, signum: int, handler: object) -> object:
        self.calls.append((signum, handler))
        self.handlers[signum] = handler
        return handler


class FakeProcess:
    def __init__(self, result: int = 7) -> None:
        self.result = result
        self.sent: list[int] = []
        self.terminated = False
        self.wait_calls = 0

    def send_signal(self, signum: int) -> None:
        self.sent.append(signum)

    def terminate(self) -> None:
        self.terminated = True

    def wait(self) -> int:
        self.wait_calls += 1
        return self.result


def test_plan_is_claude_only_and_uses_invocation_local_settings() -> None:
    plan = plan_watch_run(
        WatchRunProvider.CLAUDE,
        ("claude", "-p", "private prompt"),
        observer_command=("sidepulse", "watch-event"),
    )

    assert plan.provider is WatchRunProvider.CLAUDE
    assert (
        plan.capability_invocation.product_capability
        is ProductCapability.INVOCATION_SCOPED_MONITORING
    )
    assert (
        plan.capability_invocation.local_runtime_surface.value
        == "local.invocation_scoped_monitoring"
    )
    assert plan.command == ("claude", "-p", "private prompt")
    assert plan.events
    assert "hooks" in plan.settings_document
    assert "private prompt" not in plan.settings_document
    assert "--settings" not in plan.command

    with pytest.raises(WatchRunPlanError):
        plan_watch_run("codex", ("codex", "exec"), observer_command=("observer",))


def test_watch_run_is_reachable_from_both_cli_surfaces() -> None:
    from sidepulse.cli import build_parser, build_sidepulse_parser, cmd_watch_run

    for parser in (build_sidepulse_parser(), build_parser()):
        parsed = parser.parse_args(
            ["watch-run", "claude", "--", "claude", "--version"]
        )
        assert parsed.func is cmd_watch_run
        assert parsed.provider == "claude"
        assert parsed.provider_command[-2:] == ["claude", "--version"]


def test_unsupported_or_unsafe_plan_fails_before_filesystem_mutation() -> None:
    file_system = FakeFileSystem()
    with pytest.raises(WatchRunPlanError):
        plan_watch_run("claude", ("claude", "--settings", "/user/settings.json"), observer_command=("observer",))
    assert file_system.created == []


def test_execution_preserves_child_exit_code_and_removes_temp_settings() -> None:
    file_system = FakeFileSystem()
    signals = FakeSignals()
    process = FakeProcess(result=23)
    seen_argv: list[tuple[str, ...]] = []

    plan = plan_watch_run("claude", ("claude", "-p", "prompt"), observer_command=("observer",))
    result = execute_watch_run(
        plan,
        process_factory=lambda argv: (seen_argv.append(tuple(argv)) or process),
        file_system=file_system,
        signals=signals,
    )

    assert result.exit_code == 23
    assert seen_argv == [("claude", "--settings", file_system.created[0][0], "-p", "prompt")]
    assert file_system.removed == [file_system.created[0][0]]
    assert all(isinstance(receipt, WatchRunCleanupReceipt) for receipt in result.cleanup_receipts)
    assert {receipt.status for receipt in result.cleanup_receipts} == {CleanupStatus.RESTORED}
    assert signals.handlers == {signal.SIGINT: "old-int", signal.SIGTERM: "old-term"}


def test_signal_handler_forwards_to_child_and_is_restored() -> None:
    file_system = FakeFileSystem()
    signals = FakeSignals()
    process = FakeProcess(result=130)
    plan = plan_watch_run("claude", ("claude",), observer_command=("observer",))

    def spawn(_argv: tuple[str, ...]) -> FakeProcess:
        return process

    result = execute_watch_run(plan, process_factory=spawn, file_system=file_system, signals=signals)
    installed = {number: handler for number, handler in signals.calls[:2]}
    installed[signal.SIGINT](signal.SIGINT, None)
    assert process.sent == [signal.SIGINT]
    assert result.exit_code == 130


def test_cleanup_runs_when_child_wait_raises_and_original_exception_escapes() -> None:
    file_system = FakeFileSystem()
    signals = FakeSignals()
    process = FakeProcess()

    def wait_raises() -> int:
        process.wait_calls += 1
        raise RuntimeError("wrapper failure")

    process.wait = wait_raises  # type: ignore[method-assign]
    plan = plan_watch_run("claude", ("claude",), observer_command=("observer",))
    with pytest.raises(RuntimeError, match="wrapper failure"):
        execute_watch_run(plan, process_factory=lambda _argv: process, file_system=file_system, signals=signals)
    assert process.terminated
    assert file_system.removed
    assert signals.handlers == {signal.SIGINT: "old-int", signal.SIGTERM: "old-term"}


def test_keyboard_interrupt_is_cleaned_up_without_sensitive_receipt_fields() -> None:
    file_system = FakeFileSystem()
    signals = FakeSignals()
    process = FakeProcess()

    def interrupt() -> int:
        raise KeyboardInterrupt

    process.wait = interrupt  # type: ignore[method-assign]
    plan = plan_watch_run("claude", ("claude", "-p", "secret prompt"), observer_command=("observer",))
    with pytest.raises(KeyboardInterrupt):
        execute_watch_run(plan, process_factory=lambda _argv: process, file_system=file_system, signals=signals)
    assert file_system.removed
    assert all("secret prompt" not in repr(receipt) for receipt in execute_watch_run.last_cleanup_receipts)
    assert SignalRestoreStatus.RESTORED in {
        receipt.signal_status for receipt in execute_watch_run.last_cleanup_receipts
    }
