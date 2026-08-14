"""A registered hook must be layout-proof and proven before it ships.

The outage this guards against: the installer wrote a hardcoded
absolute path into site-packages, nobody ever executed it, and the
package was later installed editable -- so `import sidepulse` kept
working (the app looked healthy, logs kept growing) while that exact
file no longer existed. Claude Code treats a failing hook as a hard
block, so one wrong path took down every prompt in every session
across every harness at once.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sidepulse.install import hook_command_arguments, verify_hook_command
from sidepulse.providers import _is_sidepulse_hook_invocation


def test_hook_command_does_not_bake_a_package_file_path() -> None:
    arguments = hook_command_arguments("claude", Path("/tmp/claude.jsonl"))
    assert arguments[1:3] == ["-m", "sidepulse.hook_entry"]
    # No argument may be a filesystem path INTO the package: that is the
    # assumption that broke when the install layout changed.
    assert not any(argument.endswith("hook_entry.py") for argument in arguments)


def test_registered_command_actually_runs() -> None:
    """The gate that was missing: prove it before writing it anywhere."""
    arguments = hook_command_arguments(
        "claude", Path("/tmp/sidepulse-verify-probe.jsonl"), python_executable=sys.executable
    )
    assert verify_hook_command(arguments) is None


def test_verification_catches_a_broken_command() -> None:
    broken = [sys.executable, "/nonexistent/hook_entry.py", "--provider", "claude"]
    assert verify_hook_command(broken) is not None
    assert verify_hook_command([]) is not None
    assert verify_hook_command(["/nonexistent/python", "-m", "sidepulse.hook_entry"]) is not None


def test_every_shape_we_have_ever_registered_is_recognized_as_ours() -> None:
    """A recognizer that knows only one shape reports a working install
    as 'not installed' and re-registers duplicates over it."""
    legacy = ["/venv/bin/python", "/venv/lib/python3.13/site-packages/sidepulse/hook_entry.py",
              "--provider", "claude", "--log", "/tmp/claude.jsonl"]
    module = ["/venv/bin/python", "-m", "sidepulse.hook_entry",
              "--provider", "claude", "--log", "/tmp/claude.jsonl"]
    frozen = ["/Applications/SidePulse.app/Contents/MacOS/SidePulse", "agent-monitor", "hook-log",
              "--provider", "claude", "--log", "/tmp/claude.jsonl"]
    for shape in (legacy, module, frozen):
        assert _is_sidepulse_hook_invocation(shape) is True, shape
    assert _is_sidepulse_hook_invocation(["/bin/echo", "hello"]) is False
