from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

from sidepulse import cli_entry

ROOT = Path(__file__).resolve().parents[1]


def _source_tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_public_cli_routes_provider_integration_and_foreground_status_commands(
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        cli_entry,
        "integration_main",
        lambda args: calls.append(("integrations", args)) or 17,
    )
    monkeypatch.setattr(
        cli_entry,
        "provider_main",
        lambda args: calls.append(("providers", args)) or 19,
    )
    monkeypatch.setattr(
        cli_entry,
        "_legacy_sidepulse_main",
        lambda args: calls.append(("legacy", args)) or 23,
    )
    monkeypatch.setitem(
        sys.modules,
        "sidepulse.provider_usage_status_bar",
        SimpleNamespace(main=lambda: calls.append(("status-bar", ())) or 29),
    )

    assert cli_entry.sidepulse_main(["integrations", "status", "--json"]) == 17
    assert cli_entry.sidepulse_main(["providers", "status", "--json"]) == 19
    assert cli_entry.sidepulse_main(["status-bar", "--foreground"]) == 29
    assert cli_entry.sidepulse_main(["status-bar", "start", "--foreground"]) == 29
    assert cli_entry.sidepulse_main(["doctor", "--json"]) == 23
    assert calls == [
        ("integrations", ["status", "--json"]),
        ("providers", ["status", "--json"]),
        ("status-bar", ()),
        ("status-bar", ()),
        ("legacy", ["doctor", "--json"]),
    ]


def test_cli_entrypoint_keeps_the_foreground_status_bar_import_inside_the_branch() -> None:
    tree = _source_tree(ROOT / "src" / "sidepulse" / "cli_entry.py")

    top_level_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "provider_usage_status_bar"
    ]
    nested_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "provider_usage_status_bar"
    ]

    assert top_level_imports == []
    assert len(nested_imports) == 1
    assert nested_imports[0].names[0].name == "main"
    assert nested_imports[0].names[0].asname == "status_bar_main"


def test_packaged_application_uses_public_router_and_native_usage_host() -> None:
    source = (ROOT / "packaging" / "sidepulse_entry.py").read_text(encoding="utf-8")

    assert "from sidepulse.cli_entry import sidepulse_main" in source
    assert "from sidepulse.cli import sidepulse_main" not in source
    assert (
        "from sidepulse.provider_usage_status_bar import main as status_bar_main"
        in source
    )


def test_packaged_application_defers_status_bar_import_out_of_module_scope() -> None:
    tree = _source_tree(ROOT / "packaging" / "sidepulse_entry.py")

    top_level_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "sidepulse.provider_usage_status_bar"
    ]

    assert top_level_imports == []


def test_provider_usage_status_bar_supports_direct_module_startup() -> None:
    tree = _source_tree(ROOT / "src" / "sidepulse" / "provider_usage_status_bar.py")

    guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    ]

    assert len(guards) == 1
    guard = guards[0]
    system_exit_calls = [
        node
        for node in ast.walk(guard)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SystemExit"
    ]
    assert system_exit_calls, "expected a direct module guard to raise SystemExit(main())"
