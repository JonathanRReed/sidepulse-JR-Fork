import importlib.metadata
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import tomllib

from scripts import (
    generate_release_manifest,
    generate_sbom,
    scan_secrets,
    validate_release_version,
)
from sidepulse.waybar_client import main

ROOT = Path(__file__).resolve().parents[1]


def _run_gate(
    script_name: str,
    *arguments: str,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script_name), *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_release_version_gate_executes_titled_changelog_and_exits_zero() -> None:
    result = _run_gate("validate_release_version.py")
    expected_version = validate_release_version.pyproject_version()

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected_version
    assert result.stderr == ""


def test_release_version_gate_executes_wrong_tag_and_exits_nonzero() -> None:
    expected_version = validate_release_version.pyproject_version()
    wrong_tag = f"v{expected_version}.wrong"

    result = _run_gate("validate_release_version.py", "--tag", wrong_tag)

    assert result.returncode == 1
    assert f"tag {wrong_tag!r} must equal v{expected_version}" in result.stderr
    assert result.stdout == ""


def test_secret_gate_executes_clean_tracked_tree_and_exits_zero(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    target = tmp_path / "README.md"
    target.write_text("Clean fixture\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--", target.name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )

    result = _run_gate("scan_secrets.py", "--root", str(tmp_path), cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "secret scan passed (1 tracked files)\n"
    assert result.stderr == ""


def test_secret_gate_executes_tracked_secret_and_exits_one(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    secret = "ghp_" + "A" * 36
    target = tmp_path / "config.txt"
    target.write_text(f"token={secret}\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--", target.name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )

    result = _run_gate("scan_secrets.py", "--root", str(tmp_path), cwd=tmp_path)

    assert result.returncode == 1
    assert "config.txt:1: github-token" in result.stdout
    assert secret not in result.stdout
    assert result.stderr == ""


def _configure_release_version_fixture(
    tmp_path: Path,
    monkeypatch,
    *,
    changelog_heading: str,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    package_init = tmp_path / "__init__.py"
    changelog = tmp_path / "CHANGELOG.md"
    pyproject.write_text('[project]\nversion = "0.5.0"\n', encoding="utf-8")
    package_init.write_text('__version__ = "0.5.0"\n', encoding="utf-8")
    changelog.write_text(f"# Changelog\n\n{changelog_heading}\n", encoding="utf-8")
    monkeypatch.setattr(validate_release_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(validate_release_version, "PACKAGE_INIT", package_init)
    monkeypatch.setattr(validate_release_version, "CHANGELOG", changelog)


def test_release_version_accepts_descriptive_text_after_exact_heading(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_release_version_fixture(
        tmp_path,
        monkeypatch,
        changelog_heading="## 0.5.0: Coalescence",
    )

    assert validate_release_version.validate() == "0.5.0"


@pytest.mark.parametrize(
    "changelog_heading",
    ("## 0.5.0rc1: Preview", "## 0.5.0.1: Preview"),
)
def test_release_version_rejects_a_similar_version_heading(
    tmp_path: Path,
    monkeypatch,
    changelog_heading: str,
) -> None:
    _configure_release_version_fixture(
        tmp_path,
        monkeypatch,
        changelog_heading=changelog_heading,
    )

    with pytest.raises(RuntimeError, match=r"no release section for 0\.5\.0"):
        validate_release_version.validate()


def test_secret_scanner_detects_high_confidence_tokens_without_echoing_them(
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.txt"
    target.write_text("token=ghp_" + "A" * 36 + "\n", encoding="utf-8")

    findings = scan_secrets.scan_file(target)

    assert findings == (("github-token", 1),)
    assert all("ghp_" not in name for name, _line in findings)


def test_secret_scanner_ignores_documented_prefix_without_a_token(
    tmp_path: Path,
) -> None:
    target = tmp_path / "README.md"
    target.write_text("Never log a tskey- value.\n", encoding="utf-8")

    assert scan_secrets.scan_file(target) == ()


def test_sbom_is_cyclonedx_and_deduplicates_components(monkeypatch) -> None:
    class Metadata(dict):
        pass

    distributions = [
        SimpleNamespace(metadata=Metadata(Name="Example", License="MIT"), version="1.0"),
        SimpleNamespace(metadata=Metadata(Name="example", License="MIT"), version="1.0"),
    ]
    monkeypatch.setattr(
        generate_sbom.importlib.metadata,
        "distributions",
        lambda: distributions,
    )
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")

    document = generate_sbom.build_sbom(application_version="0.2.2")

    assert document["bomFormat"] == "CycloneDX"
    assert document["specVersion"] == "1.5"
    assert document["metadata"]["timestamp"] == "1970-01-01T00:00:00Z"
    assert len(document["components"]) == 1
    assert document["components"][0]["purl"] == "pkg:pypi/example@1.0"


def test_release_evidence_preserves_dist_relative_paths_for_same_named_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = tmp_path / "dist" / "primary" / "candidate.pkg"
    second = tmp_path / "dist" / "secondary" / "candidate.pkg"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"primary")
    second.write_bytes(b"secondary")
    monkeypatch.setattr(generate_sbom.importlib.metadata, "distributions", lambda: ())

    sbom = generate_sbom.build_sbom(
        application_version="0.5.0",
        artifacts=(first, second),
        root=tmp_path,
    )
    properties = {item["name"]: item["value"] for item in sbom["metadata"]["component"]["properties"]}
    assert properties["sidepulse:artifact:dist/primary/candidate.pkg:bytes"] == "7"
    assert properties["sidepulse:artifact:dist/secondary/candidate.pkg:bytes"] == "9"

    assert generate_release_manifest._artifact_record(first, root=tmp_path)["path"] == ("dist/primary/candidate.pkg")
    assert generate_release_manifest._artifact_record(second, root=tmp_path)["path"] == ("dist/secondary/candidate.pkg")


def test_release_gate_generates_and_publisher_requires_evidence_artifacts() -> None:
    root = Path(__file__).resolve().parents[1]
    gate = (root / "scripts" / "verify_macos_release.sh").read_text()
    publish = (root / "scripts" / "publish_release.sh").read_text()

    assert "generate_sbom.py" in gate
    assert "generate_release_manifest.py" in gate
    assert "sidepulse-sbom.cdx.json" in publish
    assert "release-verification.json" in publish
    assert "SHA256SUMS" in publish
    assert "--format updater-path" in gate
    assert "--format appcast-path" in gate
    assert "--format channel-metadata-path" in gate
    assert "--format updater-path" in publish
    assert "--format appcast-path" in publish
    assert "--format channel-metadata-path" in publish


def test_wheel_metadata_exposes_and_loads_the_sidepulse_waybar_entry_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    assert scripts["sidepulse-waybar"] == "sidepulse.waybar_client:main"

    dist_info = metadata_dir / f'{project["project"]["name"]}-{project["project"]["version"]}.dist-info'
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f'Metadata-Version: 2.1\nName: {project["project"]["name"]}\nVersion: {project["project"]["version"]}\n',
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[console_scripts]\nsidepulse-waybar = sidepulse.waybar_client:main\n",
        encoding="utf-8",
    )

    distribution = next(
        dist
        for dist in importlib.metadata.distributions(path=[str(metadata_dir)])
        if dist.metadata["Name"] == "sidepulse"
    )
    entry_point = next(
        entry_point
        for entry_point in distribution.entry_points
        if entry_point.group == "console_scripts"
        and entry_point.name == "sidepulse-waybar"
    )

    loaded = entry_point.load()
    assert entry_point.value == "sidepulse.waybar_client:main"
    assert loaded is main
    assert callable(loaded)
