from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_package_versions_match() -> None:
    init_text = (ROOT / "src" / "sidepulse" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.MULTILINE)

    assert match is not None
    assert match.group(1) == _project()["version"]


def test_fork_metadata_points_at_the_fork_and_preserves_upstream_link() -> None:
    urls = _project()["urls"]

    assert urls["Repository"].endswith("JonathanRReed/sidepulse-JR-Fork")
    assert urls["Issues"].endswith("JonathanRReed/sidepulse-JR-Fork/issues")
    assert urls["Upstream"].endswith("inteliwear/sidepulse")


def test_declared_pyobjc_frameworks_match_reachable_features() -> None:
    dependencies = "\n".join(_project()["dependencies"])

    for framework in ("Cocoa", "EventKit", "Quartz", "WebKit"):
        assert f"pyobjc-framework-{framework}" in dependencies
    assert "pyobjc-framework-ScriptingBridge" not in dependencies


def test_compatibility_packages_are_included() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"agent_monitor*"' in text
    assert '"sidepulse_cli*"' in text
