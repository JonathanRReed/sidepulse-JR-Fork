from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from packaging.sign_macos_app import build_sign_plan, sign_macos_app


def _candidate(tmp_path: Path) -> tuple[Path, Path, tuple[Path, ...]]:
    app = tmp_path / "SidePulse.app"
    executable = app / "Contents" / "MacOS" / "SidePulse"
    framework_binary = (
        app
        / "Contents"
        / "Frameworks"
        / "Python.framework"
        / "Versions"
        / "3.13"
        / "Python"
    )
    dylib = app / "Contents" / "Frameworks" / "libexample.dylib"
    sparkle_root = app / "Contents" / "Frameworks" / "Sparkle.framework"
    sparkle_version = sparkle_root / "Versions" / "B"
    sparkle_binary = sparkle_version / "Sparkle"
    autoupdate = sparkle_version / "Autoupdate"
    updater = sparkle_version / "Updater.app" / "Contents" / "MacOS" / "Updater"
    downloader = (
        sparkle_version
        / "XPCServices"
        / "Downloader.xpc"
        / "Contents"
        / "MacOS"
        / "Downloader"
    )
    installer = (
        sparkle_version
        / "XPCServices"
        / "Installer.xpc"
        / "Contents"
        / "MacOS"
        / "Installer"
    )
    all_macho = (
        executable,
        framework_binary,
        dylib,
        sparkle_binary,
        autoupdate,
        updater,
        downloader,
        installer,
    )
    for path in all_macho:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"macho")
        path.chmod(0o755)
    entitlements = tmp_path / "entitlements.plist"
    entitlements.write_text("<plist><dict/></plist>", encoding="utf-8")
    return app, entitlements, all_macho


def test_sign_plan_orders_code_and_nested_bundles_before_app(tmp_path: Path) -> None:
    app, _entitlements, macho = _candidate(tmp_path)

    plan = build_sign_plan(app, macho_detector=lambda path: path in macho)

    assert plan.ordered_targets[-1] == app.resolve()
    assert all(target != app.resolve() for target in plan.nested_code)
    assert any(target.suffix == ".framework" for target in plan.nested_bundles)


def test_signer_never_uses_deep_for_signing_and_entitles_only_root(
    tmp_path: Path,
) -> None:
    app, entitlements, macho = _candidate(tmp_path)
    calls = []

    def runner(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    plan = sign_macos_app(
        app,
        identity="Developer ID Application: Example",
        entitlements=entitlements,
        runner=runner,
        macho_detector=lambda path: path in macho,
    )

    signing = [argv for argv, _kwargs in calls if "--sign" in argv]
    verification = [argv for argv, _kwargs in calls if "--verify" in argv]
    assert signing
    assert verification
    assert all("--deep" not in argv for argv in signing)
    assert signing[-1][-1] == str(plan.app)
    assert "--entitlements" in signing[-1]
    assert all("--entitlements" not in argv for argv in signing[:-1])
    assert all(kwargs["check"] is True for _argv, kwargs in calls)
    assert all(kwargs["timeout"] == 120 for _argv, kwargs in calls)


def test_signer_signs_every_sparkle_target_inside_out(tmp_path: Path) -> None:
    app, entitlements, macho = _candidate(tmp_path)
    calls = []

    def runner(argv, **kwargs):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    sign_macos_app(
        app,
        identity="Developer ID Application: Example",
        entitlements=entitlements,
        runner=runner,
        macho_detector=lambda path: path in macho,
    )

    signing_targets = [Path(argv[-1]) for argv in calls if "--sign" in argv]
    sparkle_version = app.resolve() / "Contents/Frameworks/Sparkle.framework/Versions/B"
    expected_sparkle = (
        sparkle_version / "XPCServices/Downloader.xpc/Contents/MacOS/Downloader",
        sparkle_version / "XPCServices/Installer.xpc/Contents/MacOS/Installer",
        sparkle_version / "Updater.app/Contents/MacOS/Updater",
        sparkle_version / "Autoupdate",
        sparkle_version / "Sparkle",
        sparkle_version / "XPCServices/Downloader.xpc",
        sparkle_version / "XPCServices/Installer.xpc",
        sparkle_version / "Updater.app",
        app.resolve() / "Contents/Frameworks/Sparkle.framework",
    )
    sparkle_positions = [signing_targets.index(target) for target in expected_sparkle]

    assert sparkle_positions == sorted(sparkle_positions)
    assert signing_targets[-1] == app.resolve()


def test_adhoc_signing_omits_timestamp_but_keeps_hardened_runtime(
    tmp_path: Path,
) -> None:
    app, entitlements, macho = _candidate(tmp_path)
    calls = []

    def runner(argv, **kwargs):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    sign_macos_app(
        app,
        identity="-",
        entitlements=entitlements,
        runner=runner,
        macho_detector=lambda path: path in macho,
    )

    signing = [argv for argv in calls if "--sign" in argv]
    assert signing
    assert all("--timestamp" not in argv for argv in signing)
    assert all(("--options", "runtime") == argv[2:4] for argv in signing)


def test_sign_plan_rejects_symlink_that_escapes_bundle(tmp_path: Path) -> None:
    app, _entitlements, macho = _candidate(tmp_path)
    outside = tmp_path / "outside.dylib"
    outside.write_bytes(b"outside")
    link = app / "Contents" / "Frameworks" / "escape.dylib"
    link.symlink_to(outside)

    with pytest.raises(ValueError, match="escapes"):
        build_sign_plan(app, macho_detector=lambda path: path in macho)
