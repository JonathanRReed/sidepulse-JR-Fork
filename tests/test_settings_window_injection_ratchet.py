"""The settings_window namespace-injection ratchet.

`settings_window._install()` copies the monolith's global namespace into
this module, so dozens of names resolve through a channel invisible to
ruff, to the import graph, and to grep-by-import -- an extraction that
removes a legacy global can NameError the Settings window at runtime
with zero static warning. This ratchet freezes the injected-name set:
it may only SHRINK (replace injections with explicit imports), and any
NEW injected name fails here instead of at the user's Settings pane.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "sidepulse"

# Frozen 2026-08-19 (60 names); shrunk to 31 on 2026-08-26 by the
# explicit-import tranche, then 30 when the calibration stepper moved
# out (every name importable without a cycle moved
# to a real import; what remains is defined in status_bar_legacy itself).
INJECTED_NAMES = frozenset(
    {
        "ANIMATION_STYLE_DISPLAY_LABELS",
        "DEFAULT_SETTINGS_PANE",
        "SCREEN_BAR_PREVIEW_HEIGHT",
        "StatusBarController",
        "StatusBarDevice",
        "TIMEBOX_PRESET_MINUTES",
        "UsageGraphView",
        "add_preview_dot",
        "focus_sync",
        "log_status_bar",
        "make_blend_mode_popup",
        "make_closed_lid_awake_policy_popup",
        "make_color_preset_popup",
        "make_preview_scenario_popup",
        "make_provider_opener_popup",
        "native_ui",
        "nscolor_from_hex",
        "open_url",
        "os",
        "provider_icon_for_provider",
        "select_blend_mode",
        "select_color_preset",
        "select_popup_item",
        "set_checkbox_state",
        "set_field_value",
        "set_preview_dot_color",
        "set_preview_dot_rgb",
        "signals_module",
        "sys",
        "usage_stats",
    }
)


def _injected_names() -> set[str]:
    tree = ast.parse((SRC / "settings_window.py").read_text())
    defined = set(dir(builtins)) | {"__name__", "__file__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for name_node in ast.walk(target):
                    if isinstance(name_node, ast.Name):
                        defined.add(name_node.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name):
                defined.add(node.target.id)

    loads: set[str] = set()

    def visit_function(node) -> None:
        local = {
            arg.arg
            for arg in (
                node.args.args + node.args.kwonlyargs + node.args.posonlyargs
            )
        }
        if node.args.vararg:
            local.add(node.args.vararg.arg)
        if node.args.kwarg:
            local.add(node.args.kwarg.arg)
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Store):
                local.add(inner.id)
            elif isinstance(
                inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                local.add(inner.name)
            elif isinstance(inner, ast.ExceptHandler) and inner.name:
                local.add(inner.name)
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Name)
                and isinstance(inner.ctx, ast.Load)
                and inner.id not in local
            ):
                loads.add(inner.id)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit_function(node)

    return {name for name in loads if name not in defined}


def test_namespace_injection_only_shrinks() -> None:
    current = _injected_names()
    new = current - INJECTED_NAMES
    assert not new, (
        "settings_window gained NEW names that resolve only through the "
        "_install() namespace injection -- import them explicitly instead: "
        f"{sorted(new)}"
    )
    retired = INJECTED_NAMES - current
    # Shrinking is the goal; keep the frozen list honest when it does.
    # (The original branch asserted `retired <= INJECTED_NAMES`, which is
    # true by construction -- a tautology found in the 2026-08-26 audit.
    # Now a retired name must actually leave the frozen list.)
    assert not retired, (
        "settings_window no longer relies on these injected names -- "
        f"remove them from INJECTED_NAMES: {sorted(retired)}"
    )
