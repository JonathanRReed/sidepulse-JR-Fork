"""Unreachable code is how this project keeps losing features.

Every serious bug found in this round was reachability, not logic: all six
blend modes unreachable, two log janitors with zero callers, a 1,139-line
capacity presentation layer nothing imports, and a threshold detector I
duplicated because I did not find the one that already existed.

So the set of unwired modules is pinned. Adding to it requires editing this
list, which is the moment to ask "why is this not reachable yet?". Removing
from it -- by wiring a module up -- is always allowed.

These are NOT deletion candidates. They are designed, tested domain code
that Waves 3-4 depend on. The cost of leaving them unwired is that readers
(including future me) cannot tell live code from dormant code.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "sidepulse"

# module -> why it is not reachable yet
KNOWN_UNWIRED = {
    "capacity_view": "presentation for the capacity plane; needs declared Claude lanes",
    "capacity_sources": "adapter registry for the capacity plane",
    "capacity_history_store": "Wave 4 persisted usage history",
    "provider_runtime": "Wave 4 per-provider runtime",
    "delivery_ledger_store": "notification delivery ledger",
    "reply_classifier": "reply classification for the agent inbox",
}

# Legitimate separate entry points -- not imported by the app by design.
ENTRY_POINTS = {"__init__", "__main__", "cli", "doctor", "hook", "hook_entry"}


def _imported_siblings(path: Path) -> set[str]:
    """Sibling modules this file imports, in any of the shapes we use.

    String matching missed `from . import claude_quota` and reported eight
    live modules as dead, so this parses instead of guessing.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module is None:
                # from . import a, b
                names.update(alias.name for alias in node.names)
            elif node.level == 1 and node.module:
                names.add(node.module.split(".")[0])
            elif node.module and node.module.startswith("sidepulse."):
                names.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sidepulse."):
                    names.add(alias.name.split(".")[1])
    return names


def _module_importers(name: str) -> set[str]:
    return {
        path.stem
        for path in SRC.glob("*.py")
        if path.stem != name and name in _imported_siblings(path)
    }


def test_no_new_module_becomes_unreachable() -> None:
    """A module nothing imports is a feature nobody can use."""
    unwired = {
        path.stem
        for path in SRC.glob("*.py")
        if path.stem not in ENTRY_POINTS and not _module_importers(path.stem)
    }
    surprises = sorted(unwired - set(KNOWN_UNWIRED))
    assert not surprises, (
        f"these modules are unreachable and undeclared: {surprises}. "
        "Wire them up, or add them to KNOWN_UNWIRED with the reason."
    )


def test_the_unwired_list_does_not_go_stale() -> None:
    """Wiring a module up should retire it from this list."""
    still_unwired = {
        name for name in KNOWN_UNWIRED if not _module_importers(name)
    }
    now_wired = sorted(set(KNOWN_UNWIRED) - still_unwired)
    assert not now_wired, (
        f"{now_wired} are wired up now -- remove them from KNOWN_UNWIRED"
    )


def test_every_listed_module_actually_exists() -> None:
    missing = sorted(
        name for name in KNOWN_UNWIRED if not (SRC / f"{name}.py").exists()
    )
    assert not missing, f"KNOWN_UNWIRED names modules that are gone: {missing}"
