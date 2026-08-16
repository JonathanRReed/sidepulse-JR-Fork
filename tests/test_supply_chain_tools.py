import json
from pathlib import Path
from types import SimpleNamespace

from scripts import generate_sbom, scan_secrets


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


def test_release_gate_generates_and_publisher_requires_evidence_artifacts() -> None:
    root = Path(__file__).resolve().parents[1]
    gate = (root / "scripts" / "verify_macos_release.sh").read_text()
    publish = (root / "scripts" / "publish_release.sh").read_text()

    assert "generate_sbom.py" in gate
    assert "generate_release_manifest.py" in gate
    assert "sidepulse-sbom.cdx.json" in publish
    assert "release-verification.json" in publish
    assert "SHA256SUMS" in publish
