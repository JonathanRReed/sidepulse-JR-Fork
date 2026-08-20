"""No selector-shaped method may exist that nothing can ever invoke.

The class of bug this pins: a timer/selector migration moves the
invocation but leaves the method -- `animateColorsPreviewTick_` kept the
thumbnail-repaint half of the color preview while nothing invoked it
(every Settings thumb froze), and `pollDevices_` survived its own
migration test asserting the SELECTOR was gone. An orphaned callback is
invisible to ruff (it is "used" by being defined on the class) and to
the menu-action sweep (it is not in a menu). This test enumerates them
structurally.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "sidepulse"

CONTROLLER_FILES = (
    "status_bar_legacy.py",
    "_status_bar_production.py",
    "provider_usage_status_bar.py",
)

# Cocoa invokes these itself (delegate/datasource/view overrides); they
# legitimately have zero in-repo references. Exact names only -- a new
# orphan must not be able to hide behind a prefix.
FRAMEWORK_CALLBACKS = frozenset(
    {
        "drawRect_",
        "menuDidClose_",
        "numberOfRowsInTableView_",
        "popoverDidClose_",
        "tableViewSelectionDidChange_",
        "tableView_isGroupRow_",
        "tableView_shouldSelectRow_",
        "tableView_viewForTableColumn_row_",
        "textDidChange_",
        "textDidEndEditing_",
    }
)


def test_every_selector_shaped_callback_is_referenced() -> None:
    sources = {path.name: path.read_text() for path in SRC.glob("*.py")}
    blob = "\n".join(sources.values())

    defined: dict[str, str] = {}
    for name in CONTROLLER_FILES:
        text = sources[name]
        for match in re.finditer(r"^    def ([A-Za-z_]\w*?_)\(self", text, re.M):
            defined.setdefault(match.group(1), name)
        for match in re.finditer(r"^    def (_\w*_fired)\(self", text, re.M):
            defined.setdefault(match.group(1), name)

    # Selectors built dynamically from the provider registry
    # (settings_window and the setup window both do
    # f"install{provider.title()}Hooks:").
    from sidepulse.providers import HOOK_PROVIDERS

    dynamic = set()
    for provider in HOOK_PROVIDERS:
        dynamic.add(f"install{provider.title()}Hooks_")
        dynamic.add(f"uninstall{provider.title()}Hooks_")

    orphans: list[str] = []
    for method, home in sorted(defined.items()):
        if method.startswith("__") or method in FRAMEWORK_CALLBACKS:
            continue
        if method in dynamic:
            continue
        selector = method[:-1] + ":" if method.endswith("_") else None
        references = blob.count(f".{method}(") + blob.count(f"self.{method}")
        # Declarative tables (PRESENTATION_TIMER_BINDINGS) reference
        # callbacks by quoted name; that is a reference too.
        references += blob.count(f'"{method}"') + blob.count(f"'{method}'")
        if selector is not None:
            references += blob.count(f'"{selector}"') + blob.count(f"'{selector}'")
        if references == 0:
            orphans.append(f"{home}: {method}")

    assert not orphans, (
        "selector-shaped methods nothing invokes (delete them, or add the "
        "Cocoa callback to FRAMEWORK_CALLBACKS with a reason):\n  "
        + "\n  ".join(orphans)
    )


def test_every_timer_binding_names_a_real_callback() -> None:
    """The declarative half of the same invariant: every entry in
    PRESENTATION_TIMER_BINDINGS must name a method the controller
    actually defines -- a renamed callback must fail HERE, not as a
    silent getattr surprise at launch."""
    text = (SRC / "status_bar_legacy.py").read_text()
    names = re.findall(r'\(RuntimeFeature\.\w+, "(\w+)"\)', text)
    assert len(names) >= 17
    for name in names:
        assert re.search(rf"^    def {re.escape(name)}\(self", text, re.M), name
