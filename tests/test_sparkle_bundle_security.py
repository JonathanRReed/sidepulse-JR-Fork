from __future__ import annotations

import base64
import importlib.util
import plistlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "packaging" / "verify_sparkle_bundle.py"
FEED_URL = (
    "https://github.com/JonathanRReed/sidepulse-JR-Fork/"
    "releases/download/updates/appcast.xml"
)


def _verifier() -> ModuleType:
    assert VERIFIER_PATH.is_file(), "Sparkle bundle verifier is not implemented"
    spec = importlib.util.spec_from_file_location("verify_sparkle_bundle", VERIFIER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_plist(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(value))


def _candidate(tmp_path: Path) -> tuple[Path, tuple[Path, ...], tuple[Path, ...]]:
    app = tmp_path / "SidePulse.app"
    framework = app / "Contents/Frameworks/Sparkle.framework"
    version = framework / "Versions/B"
    binaries = (
        version / "Sparkle",
        version / "Autoupdate",
        version / "Updater.app/Contents/MacOS/Updater",
        version / "XPCServices/Downloader.xpc/Contents/MacOS/Downloader",
        version / "XPCServices/Installer.xpc/Contents/MacOS/Installer",
    )
    bundles = (
        version / "XPCServices/Downloader.xpc",
        version / "XPCServices/Installer.xpc",
        version / "Updater.app",
        framework,
    )
    for binary in binaries:
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"macho")
        binary.chmod(0o755)
    _write_plist(
        version / "Resources/Info.plist",
        {"CFBundleShortVersionString": "2.9.6", "CFBundleVersion": "2061"},
    )
    for bundle in bundles[:-1]:
        _write_plist(bundle / "Contents/Info.plist", {"CFBundleVersion": "2061"})
    _write_plist(
        app / "Contents/Info.plist",
        {
            "SUFeedURL": FEED_URL,
            "SUPublicEDKey": base64.b64encode(bytes(range(32))).decode("ascii"),
            "SURequireSignedFeed": True,
            "SUVerifyUpdateBeforeExtraction": True,
        },
    )
    license_path = app / "Contents/Resources/ThirdPartyLicenses/Sparkle.txt"
    license_path.parent.mkdir(parents=True, exist_ok=True)
    license_path.write_text("Sparkle MIT license", encoding="utf-8")
    (framework / "Versions/Current").symlink_to("B")
    for name in ("Sparkle", "Autoupdate", "Updater.app", "Resources", "XPCServices"):
        (framework / name).symlink_to(f"Versions/Current/{name}")
    return app, binaries, bundles


class FakeMacTools:
    def __init__(self, *, team: str = "TEAM123456") -> None:
        self.team = team
        self.invalid_signature: Path | None = None
        self.missing_runtime: Path | None = None
        self.team_mismatch: Path | None = None
        self.entitled: Path | None = None
        self.external_dependency: Path | None = None
        self.external_rpath: Path | None = None
        self.verified: list[Path] = []

    def __call__(self, argv, **kwargs):
        target = Path(argv[-1])
        if argv[0] == "/usr/bin/codesign" and "--verify" in argv:
            self.verified.append(target)
            if target == self.invalid_signature:
                return subprocess.CompletedProcess(argv, 1, "", "invalid signature")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "/usr/bin/codesign" and "-dv" in argv:
            team = "OTHERTEAM1" if target == self.team_mismatch else self.team
            flags = "0x0(none)" if target == self.missing_runtime else "0x10000(runtime)"
            return subprocess.CompletedProcess(
                argv,
                0,
                "",
                (
                    "CodeDirectory v=20500 size=1127 "
                    f"flags={flags} hashes=28+3 location=embedded\n"
                    f"TeamIdentifier={team}\n"
                ),
            )
        if argv[0] == "/usr/bin/codesign" and "--entitlements" in argv:
            entitlements = (
                {"com.apple.security.network.client": True}
                if target == self.entitled
                else {}
            )
            return subprocess.CompletedProcess(argv, 0, plistlib.dumps(entitlements), b"")
        if argv[:2] == ["/usr/bin/otool", "-L"]:
            dependency = (
                "/tmp/Injected.framework/Injected"
                if target == self.external_dependency
                else "/System/Library/Frameworks/Foundation.framework/Versions/C/Foundation"
            )
            return subprocess.CompletedProcess(
                argv,
                0,
                f"{target}:\n\t{dependency} (compatibility version 1.0.0, current version 1.0.0)\n",
                "",
            )
        if argv[:2] == ["/usr/bin/otool", "-l"]:
            output = (
                "Load command 1\n          cmd LC_RPATH\n      cmdsize 40\n"
                "         path /tmp/injected (offset 12)\n"
                if target == self.external_rpath
                else ""
            )
            return subprocess.CompletedProcess(argv, 0, output, "")
        raise AssertionError(f"unexpected command: {argv}")


def test_secure_sparkle_bundle_verifies_every_nested_target(tmp_path: Path) -> None:
    verifier = _verifier()
    app, binaries, bundles = _candidate(tmp_path)
    tools = FakeMacTools()

    report = verifier.verify_sparkle_bundle(
        app,
        production=True,
        expected_team="TEAM123456",
        runner=tools,
    )

    assert report.version == "2.9.6"
    assert report.team_identifier == "TEAM123456"
    assert set(tools.verified) == {*binaries, *bundles}


def test_sparkle_bundle_rejects_wrong_version(tmp_path: Path) -> None:
    verifier = _verifier()
    app, _binaries, _bundles = _candidate(tmp_path)
    info = app / "Contents/Frameworks/Sparkle.framework/Versions/B/Resources/Info.plist"
    _write_plist(info, {"CFBundleShortVersionString": "2.9.5", "CFBundleVersion": "2059"})

    with pytest.raises(verifier.SparkleBundleError, match=r"expected 2\.9\.6"):
        verifier.verify_sparkle_bundle(app, production=False, runner=FakeMacTools())


def test_sparkle_bundle_rejects_missing_nested_member(tmp_path: Path) -> None:
    verifier = _verifier()
    app, binaries, _bundles = _candidate(tmp_path)
    binaries[-1].unlink()

    with pytest.raises(verifier.SparkleBundleError, match="missing required Sparkle member"):
        verifier.verify_sparkle_bundle(app, production=False, runner=FakeMacTools())


def test_sparkle_bundle_rejects_symlink_escape(tmp_path: Path) -> None:
    verifier = _verifier()
    app, _binaries, _bundles = _candidate(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    (app / "Contents/Frameworks/Sparkle.framework/escape").symlink_to(outside)

    with pytest.raises(verifier.SparkleBundleError, match="symlink escapes"):
        verifier.verify_sparkle_bundle(app, production=False, runner=FakeMacTools())


def test_sparkle_bundle_rejects_missing_framework_alias(tmp_path: Path) -> None:
    verifier = _verifier()
    app, _binaries, _bundles = _candidate(tmp_path)
    (app / "Contents/Frameworks/Sparkle.framework/Sparkle").unlink()

    with pytest.raises(verifier.SparkleBundleError, match="missing Sparkle symlink"):
        verifier.verify_sparkle_bundle(app, production=False, runner=FakeMacTools())


def test_sparkle_bundle_rejects_invalid_nested_signature(tmp_path: Path) -> None:
    verifier = _verifier()
    app, binaries, _bundles = _candidate(tmp_path)
    tools = FakeMacTools()
    tools.invalid_signature = binaries[2]

    with pytest.raises(verifier.SparkleBundleError, match="invalid nested signature"):
        verifier.verify_sparkle_bundle(app, production=False, runner=tools)


def test_sparkle_bundle_rejects_missing_hardened_runtime(tmp_path: Path) -> None:
    verifier = _verifier()
    app, binaries, _bundles = _candidate(tmp_path)
    tools = FakeMacTools()
    tools.missing_runtime = binaries[0]

    with pytest.raises(verifier.SparkleBundleError, match="hardened runtime"):
        verifier.verify_sparkle_bundle(app, production=False, runner=tools)


def test_sparkle_bundle_rejects_production_team_mismatch(tmp_path: Path) -> None:
    verifier = _verifier()
    app, _binaries, bundles = _candidate(tmp_path)
    tools = FakeMacTools()
    tools.team_mismatch = bundles[0]

    with pytest.raises(verifier.SparkleBundleError, match="TeamIdentifier mismatch"):
        verifier.verify_sparkle_bundle(
            app,
            production=True,
            expected_team="TEAM123456",
            runner=tools,
        )


def test_sparkle_bundle_rejects_unreviewed_nested_entitlements(tmp_path: Path) -> None:
    verifier = _verifier()
    app, _binaries, bundles = _candidate(tmp_path)
    tools = FakeMacTools()
    tools.entitled = bundles[1]

    with pytest.raises(verifier.SparkleBundleError, match="unreviewed entitlements"):
        verifier.verify_sparkle_bundle(app, production=False, runner=tools)


def test_sparkle_bundle_rejects_external_dependency(tmp_path: Path) -> None:
    verifier = _verifier()
    app, binaries, _bundles = _candidate(tmp_path)
    tools = FakeMacTools()
    tools.external_dependency = binaries[3]

    with pytest.raises(verifier.SparkleBundleError, match="external dependency"):
        verifier.verify_sparkle_bundle(app, production=False, runner=tools)


def test_sparkle_bundle_rejects_external_rpath(tmp_path: Path) -> None:
    verifier = _verifier()
    app, binaries, _bundles = _candidate(tmp_path)
    tools = FakeMacTools()
    tools.external_rpath = binaries[4]

    with pytest.raises(verifier.SparkleBundleError, match="external rpath"):
        verifier.verify_sparkle_bundle(app, production=False, runner=tools)
