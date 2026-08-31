"""The hook path runs as its own process on every single hook event.

Whatever it imports, it imports thousands of times a day. This is a ratchet:
it fails the moment someone reintroduces an eager package-level import that
drags the LED stack, the collector, or PyObjC into a process whose entire job
is to append a line to a log.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


SRC = str(Path(__file__).resolve().parents[1] / "src")

# Modules the hook process has no business loading. Each one was measured
# costing tens of milliseconds, per event, for nothing.
FORBIDDEN = (
    "sidepulse.battery",
    "sidepulse.collector",
    "sidepulse.led_status",
    "sidepulse.lid_sleep",
    "sidepulse.status_bar",
    "sidepulse.settings",
    "sidepulse.device_writer",
    "sidepulse.usage_stats",
    "sidepulse.hook",
    "sidepulse.provider_adapters",
    "sidepulse.providers",
)


def _imported_modules(statement: str) -> set[str]:
    probe = (
        f"{statement}\n"
        "import sys, json\n"
        "print(json.dumps([m for m in sys.modules if m.startswith('sidepulse')]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PYTHONPATH": SRC, "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    import json

    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def test_hook_entry_does_not_drag_in_the_app() -> None:
    loaded = _imported_modules("import sidepulse.hook_entry")
    leaked = sorted(loaded & set(FORBIDDEN))
    assert not leaked, (
        f"importing sidepulse.hook_entry now also loads {leaked}; "
        "every hook event pays for this"
    )


def test_the_module_the_hook_actually_runs_stays_lean() -> None:
    """`hook_entry` defers `hook`, so this is the seam that really runs.

    Testing only `hook_entry` would pass while the module it loads a
    microsecond later drags in the entire app.
    """
    loaded = _imported_modules("from sidepulse.hook import hook_log_main")
    legacy_forbidden = set(FORBIDDEN) - {
        "sidepulse.hook",
        "sidepulse.provider_adapters",
        "sidepulse.providers",
    }
    leaked = sorted(loaded & legacy_forbidden)
    assert not leaked, f"the synchronous fallback path now loads {leaked}"


def test_thin_hook_client_does_not_load_processing_or_app_modules() -> None:
    loaded = _imported_modules("import sidepulse.hook_client")
    leaked = sorted(loaded & set(FORBIDDEN))
    assert not leaked, f"the hook admission client now loads {leaked}"


def test_package_import_is_lazy() -> None:
    """`import sidepulse` alone must not walk the whole package."""
    loaded = _imported_modules("import sidepulse")
    leaked = sorted(loaded & set(FORBIDDEN))
    assert not leaked, f"`import sidepulse` eagerly loads {leaked}"


@pytest.mark.parametrize("name", ["AgentMode", "HookEventServer", "AgentLedController"])
def test_lazy_exports_still_resolve(name: str) -> None:
    """Laziness must be invisible to callers."""
    import sidepulse

    assert getattr(sidepulse, name) is not None
    assert name in dir(sidepulse)


def test_unknown_attribute_still_raises_attribute_error() -> None:
    import sidepulse

    with pytest.raises(AttributeError):
        sidepulse.definitely_not_exported
