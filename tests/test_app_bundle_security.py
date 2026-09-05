from __future__ import annotations

import importlib.util
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from sidepulse.status_bar_launch import (
    build_launch_agent_plist,
    development_python_executable,
    install_launch_agent,
    launch_agent_path_env,
    production_bundle_executable,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def load_verifier_module():
    module_path = REPO_ROOT / "packaging" / "verify_macos_app.py"
    spec = importlib.util.spec_from_file_location("sidepulse_macos_app_verifier", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load verifier from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_bundle(
    root: Path,
    *,
    identifier: str = "io.sidepulse.app",
    environment: dict[str, str] | None = None,
    include_runtime: bool = True,
) -> Path:
    bundle = root / "SidePulse.app"
    executable = bundle / "Contents" / "MacOS" / "SidePulse"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"main-mach-o")
    executable.chmod(0o755)
    if include_runtime:
        runtime = bundle / "Contents" / "Frameworks" / "Python.framework" / "Python"
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b"python-mach-o")
        runtime.chmod(0o755)
    info = {
        "CFBundleIdentifier": identifier,
        "CFBundleExecutable": "SidePulse",
        "CFBundlePackageType": "APPL",
    }
    if environment is not None:
        info["LSEnvironment"] = environment
    info_path = bundle / "Contents" / "Info.plist"
    info_path.write_bytes(plistlib.dumps(info))
    return bundle


def verifier_runner(
    bundle: Path,
    *,
    dependencies: dict[str, tuple[str, ...]] | None = None,
    rpaths: dict[str, tuple[str, ...]] | None = None,
    signature_valid: bool = True,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    executable = bundle / "Contents" / "MacOS" / "SidePulse"
    runtime = bundle / "Contents" / "Frameworks" / "Python.framework" / "Python"
    dependency_map = dependencies or {
        str(executable): (
            "@rpath/Python.framework/Versions/3.13/Python",
            "/System/Library/Frameworks/Cocoa.framework/Versions/A/Cocoa",
            "/usr/lib/libSystem.B.dylib",
        ),
        str(runtime): ("/usr/lib/libSystem.B.dylib",),
    }
    rpath_map = rpaths or {str(executable): ("@executable_path/../Frameworks",)}

    def run(command: Sequence[str | os.PathLike[str]], **_kwargs):
        arguments = [str(part) for part in command]
        if arguments[0].endswith("codesign"):
            return subprocess.CompletedProcess(
                arguments,
                0 if signature_valid else 1,
                "",
                "" if signature_valid else "invalid signature",
            )
        if arguments[0].endswith("file"):
            target = arguments[-1]
            kind = "Mach-O 64-bit executable arm64" if target in dependency_map else "data"
            return subprocess.CompletedProcess(arguments, 0, f"{target}: {kind}\n", "")
        if arguments[0].endswith("otool") and arguments[1] == "-L":
            target = arguments[-1]
            lines = [f"{target}:"]
            lines.extend(
                f"\t{dependency} (compatibility version 1.0.0, current version 1.0.0)"
                for dependency in dependency_map.get(target, ())
            )
            return subprocess.CompletedProcess(arguments, 0, "\n".join(lines) + "\n", "")
        if arguments[0].endswith("otool") and arguments[1] == "-l":
            target = arguments[-1]
            lines: list[str] = []
            for rpath in rpath_map.get(target, ()):
                lines.extend(("          cmd LC_RPATH", f"         path {rpath} (offset 12)"))
            return subprocess.CompletedProcess(arguments, 0, "\n".join(lines) + "\n", "")
        raise AssertionError(f"unexpected verifier command: {arguments}")

    return run


def verify(bundle: Path, **runner_options):
    module = load_verifier_module()
    return module.verify_packaged_app(
        bundle,
        command_runner=verifier_runner(bundle, **runner_options),
    )


def test_source_default_launch_agent_fails_closed_without_mutable_wrapper() -> None:
    with patch("sidepulse.status_bar_launch.sys.frozen", False, create=True):
        with pytest.raises(RuntimeError, match=r"packaged SidePulse\.app"):
            build_launch_agent_plist()


def test_source_install_launch_agent_uses_current_interpreter(tmp_path: Path) -> None:
    plist_path = tmp_path / "LaunchAgents" / "io.sidepulse.agentstatus.plist"
    state_dir = tmp_path / "state"
    with (
        patch("sidepulse.status_bar_launch.sys.frozen", False, create=True),
        patch("sidepulse._status_bar_launch_legacy.default_state_dir", return_value=state_dir),
        patch("sidepulse._status_bar_launch_legacy.restart_launch_agent"),
        patch("sidepulse._status_bar_launch_legacy.launch_agent_running", return_value=False),
        patch("sidepulse._status_bar_launch_legacy.remove_legacy_launch_agent", return_value=False),
    ):
        result = install_launch_agent(start=False, plist_path=plist_path)

    assert result.changed
    payload = plistlib.loads(plist_path.read_bytes())
    assert payload["ProgramArguments"][0] == str(Path(sys.executable))


def test_development_python_is_absent_when_frozen() -> None:
    with patch("sidepulse.status_bar_launch.sys.frozen", True, create=True):
        assert development_python_executable() is None


def test_explicit_development_interpreter_remains_available_with_system_path() -> None:
    plist = build_launch_agent_plist(
        python_executable="/usr/bin/python3",
        stdout_path=Path("/tmp/sidepulse.out.log"),
        stderr_path=Path("/tmp/sidepulse.err.log"),
    )

    assert plist["ProgramArguments"] == [
        "/usr/bin/python3",
        "-m",
        "sidepulse",
        "status-bar",
        "--foreground",
    ]
    assert plist["EnvironmentVariables"] == {
        "PYTHONUNBUFFERED": "1",
        "PATH": SYSTEM_PATH,
    }


def test_launch_agent_path_is_only_apple_system_directories() -> None:
    assert launch_agent_path_env("/opt/homebrew/bin/python3") == SYSTEM_PATH


def test_frozen_launch_agent_uses_packaged_argument_shape_without_python_overrides(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "SidePulse.app" / "Contents" / "MacOS" / "SidePulse"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"frozen")
    executable.chmod(0o755)

    with (
        patch("sidepulse.status_bar_launch.sys.frozen", True, create=True),
        patch("sidepulse.status_bar_launch.sys.executable", str(executable)),
    ):
        plist = build_launch_agent_plist(
            stdout_path=tmp_path / "out.log",
            stderr_path=tmp_path / "err.log",
        )

    assert plist["ProgramArguments"] == [
        str(executable),
        "status-bar",
        "start",
        "--foreground",
    ]
    environment = plist["EnvironmentVariables"]
    assert environment["PATH"] == SYSTEM_PATH
    assert "PYTHONHOME" not in environment
    assert "PYTHONPATH" not in environment


def test_production_bundle_executable_rejects_symlinked_executable(tmp_path: Path) -> None:
    executable = tmp_path / "SidePulse.app" / "Contents" / "MacOS" / "SidePulse"
    executable.parent.mkdir(parents=True)
    executable.symlink_to("/bin/ls")

    with pytest.raises(RuntimeError, match="symlink"):
        production_bundle_executable(executable)


@pytest.mark.parametrize("symlinked_ancestor", ["SidePulse.app", "Contents", "MacOS"])
def test_production_bundle_executable_rejects_symlinked_bundle_ancestor(
    tmp_path: Path,
    symlinked_ancestor: str,
) -> None:
    launch_root = tmp_path / "launch"
    target_root = tmp_path / "target"
    bundle = launch_root / "SidePulse.app"
    executable = bundle / "Contents" / "MacOS" / "SidePulse"

    if symlinked_ancestor == "SidePulse.app":
        target_bundle = target_root / "SidePulse.app"
        real_executable = target_bundle / "Contents" / "MacOS" / "SidePulse"
        real_executable.parent.mkdir(parents=True)
        launch_root.mkdir()
        bundle.symlink_to(target_bundle, target_is_directory=True)
    elif symlinked_ancestor == "Contents":
        target_contents = target_root / "Contents"
        real_executable = target_contents / "MacOS" / "SidePulse"
        real_executable.parent.mkdir(parents=True)
        bundle.mkdir(parents=True)
        (bundle / "Contents").symlink_to(target_contents, target_is_directory=True)
    else:
        target_macos = target_root / "MacOS"
        real_executable = target_macos / "SidePulse"
        real_executable.parent.mkdir(parents=True)
        (bundle / "Contents").mkdir(parents=True)
        (bundle / "Contents" / "MacOS").symlink_to(
            target_macos,
            target_is_directory=True,
        )
    real_executable.write_bytes(b"frozen")
    real_executable.chmod(0o755)

    with pytest.raises(RuntimeError, match="symlink"):
        production_bundle_executable(executable)


@pytest.mark.parametrize("variable", ["PYTHONHOME", "PYTHONPATH", "DYLD_LIBRARY_PATH"])
def test_packaged_bundle_rejects_dangerous_environment(
    tmp_path: Path,
    variable: str,
) -> None:
    bundle = make_bundle(tmp_path, environment={variable: "/tmp/substitute"})

    result = verify(bundle)

    assert not result.accepted
    assert any(variable in error for error in result.errors)


def test_packaged_bundle_rejects_external_macho_dependency(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    executable = bundle / "Contents" / "MacOS" / "SidePulse"
    runtime = bundle / "Contents" / "Frameworks" / "Python.framework" / "Python"

    result = verify(
        bundle,
        dependencies={
            str(executable): ("/opt/homebrew/lib/libpython3.13.dylib",),
            str(runtime): ("/usr/lib/libSystem.B.dylib",),
        },
    )

    assert not result.accepted
    assert any("/opt/homebrew/lib/libpython3.13.dylib" in error for error in result.errors)
    assert "/opt/homebrew/lib/libpython3.13.dylib" in result.dependencies


def test_packaged_bundle_rejects_external_rpath(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    executable = bundle / "Contents" / "MacOS" / "SidePulse"

    result = verify(bundle, rpaths={str(executable): ("/Users/example/work/runtime",)})

    assert not result.accepted
    assert any("/Users/example/work/runtime" in error for error in result.errors)


def test_packaged_bundle_rejects_rpath_traversal(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    executable = bundle / "Contents" / "MacOS" / "SidePulse"

    result = verify(
        bundle,
        rpaths={str(executable): ("@loader_path/../../../../Users/example/work",)},
    )

    assert not result.accepted
    assert any("@loader_path/../../../../Users/example/work" in error for error in result.errors)


def test_packaged_bundle_rejects_external_import_root(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    import_file = bundle / "Contents" / "Resources" / "external.pth"
    import_file.parent.mkdir(parents=True)
    import_file.write_text("/Users/example/.local/lib/python3.13/site-packages\n")

    result = verify(bundle)

    assert not result.accepted
    assert "/Users/example/.local/lib/python3.13/site-packages" in result.import_roots


def test_packaged_bundle_rejects_executable_pth_directive(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    import_file = bundle / "Contents" / "Resources" / "external.pth"
    import_file.parent.mkdir(parents=True)
    import_file.write_text("import sys; sys.path.insert(0, '/Users/example/work')\n")

    result = verify(bundle)

    assert not result.accepted
    assert any("executable .pth directive" in error for error in result.errors)


def test_packaged_bundle_rejects_tab_prefixed_executable_pth_directive(
    tmp_path: Path,
) -> None:
    bundle = make_bundle(tmp_path)
    import_file = bundle / "Contents" / "Resources" / "external.pth"
    import_file.parent.mkdir(parents=True)
    import_file.write_text("import\tsys; sys.path.insert(0, '/Users/example/work')\n")

    result = verify(bundle)

    assert not result.accepted
    assert any("executable .pth directive" in error for error in result.errors)


def test_packaged_bundle_rejects_relative_pth_escape(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    import_file = bundle / "Contents" / "Resources" / "external.pth"
    import_file.parent.mkdir(parents=True)
    import_file.write_text("../../../../Users/example/work\n")

    result = verify(bundle)

    assert not result.accepted
    assert any("external Python import root" in error for error in result.errors)


def test_packaged_bundle_requires_runtime_binary_not_only_base_library(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path, include_runtime=False)
    base_library = bundle / "Contents" / "Resources" / "base_library.zip"
    base_library.parent.mkdir(parents=True)
    base_library.write_bytes(b"python bytecode only")

    result = verify(bundle)

    assert not result.accepted
    assert any("internal Python runtime" in error for error in result.errors)


def test_packaged_bundle_requires_runtime_payload_to_be_macho(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    executable = bundle / "Contents" / "MacOS" / "SidePulse"

    result = verify(
        bundle,
        dependencies={
            str(executable): ("/usr/lib/libSystem.B.dylib",),
        },
    )

    assert not result.accepted
    assert any("internal Python runtime is not Mach-O" in error for error in result.errors)


def test_packaged_bundle_rejects_hard_linked_internal_runtime(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    runtime = bundle / "Contents" / "Frameworks" / "Python.framework" / "Python"
    external_runtime = tmp_path / "external-python"
    external_runtime.write_bytes(b"python-mach-o")
    external_runtime.chmod(0o755)
    runtime.unlink()
    os.link(external_runtime, runtime)
    assert runtime.stat().st_nlink == 2

    result = verify(bundle)

    assert not result.accepted
    assert any(
        str(runtime) in error and "hard-link count" in error
        for error in result.errors
    )


def test_packaged_bundle_rejects_hard_linked_nested_macho_payload(
    tmp_path: Path,
) -> None:
    bundle = make_bundle(tmp_path)
    executable = bundle / "Contents" / "MacOS" / "SidePulse"
    runtime = bundle / "Contents" / "Frameworks" / "Python.framework" / "Python"
    external_payload = tmp_path / "external-addon.dylib"
    external_payload.write_bytes(b"addon-mach-o")
    nested_payload = bundle / "Contents" / "Frameworks" / "addon.dylib"
    os.link(external_payload, nested_payload)
    assert nested_payload.stat().st_nlink == 2

    result = verify(
        bundle,
        dependencies={
            str(executable): ("/usr/lib/libSystem.B.dylib",),
            str(runtime): ("/usr/lib/libSystem.B.dylib",),
            str(nested_payload): ("/usr/lib/libSystem.B.dylib",),
        },
    )

    assert not result.accepted
    assert any(
        str(nested_payload) in error and "hard-link count" in error
        for error in result.errors
    )


@pytest.mark.parametrize(
    "dependency",
    [
        "@loader_path/../../../Users/example/libevil.dylib",
        "/System/Library/../../../Users/example/libevil.dylib",
    ],
)
def test_packaged_bundle_rejects_dependency_path_traversal(
    tmp_path: Path,
    dependency: str,
) -> None:
    bundle = make_bundle(tmp_path)
    executable = bundle / "Contents" / "MacOS" / "SidePulse"
    runtime = bundle / "Contents" / "Frameworks" / "Python.framework" / "Python"

    result = verify(
        bundle,
        dependencies={
            str(executable): (dependency,),
            str(runtime): ("/usr/lib/libSystem.B.dylib",),
        },
    )

    assert not result.accepted
    assert any(dependency in error for error in result.errors)


@pytest.mark.parametrize(
    ("bundle_options", "runner_options", "error_fragment"),
    [
        ({"identifier": "io.sidepulse.cli"}, {}, "CFBundleIdentifier"),
        ({"include_runtime": False}, {}, "internal Python runtime"),
        ({}, {"signature_valid": False}, "signature"),
    ],
)
def test_packaged_bundle_rejects_identity_runtime_or_signature_failure(
    tmp_path: Path,
    bundle_options: dict[str, object],
    runner_options: dict[str, object],
    error_fragment: str,
) -> None:
    bundle = make_bundle(tmp_path, **bundle_options)

    result = verify(bundle, **runner_options)

    assert not result.accepted
    assert any(error_fragment in error for error in result.errors)


def test_packaged_bundle_accepts_internal_runtime_and_apple_dependencies(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    executable = bundle / "Contents" / "MacOS" / "SidePulse"
    runtime = bundle / "Contents" / "Frameworks" / "Python.framework" / "Python"

    result = verify(
        bundle,
        dependencies={
            str(executable): (
                "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/JavaScriptCore",
                "/usr/lib/libSystem.B.dylib",
            ),
            str(runtime): ("/usr/lib/libSystem.B.dylib",),
        },
        rpaths={str(executable): ("@loader_path",)},
    )

    assert result.accepted, result.errors
    assert result.bundle_path == bundle
    assert result.executable_path == bundle / "Contents" / "MacOS" / "SidePulse"
    assert "/usr/lib/libSystem.B.dylib" in result.dependencies
    assert "@loader_path" in result.rpaths


def test_packaged_bundle_accepts_loader_relative_rpath_that_stays_inside_bundle(
    tmp_path: Path,
) -> None:
    bundle = make_bundle(tmp_path)
    executable = bundle / "Contents" / "MacOS" / "SidePulse"
    runtime = bundle / "Contents" / "Frameworks" / "Python.framework" / "Python"

    result = verify(
        bundle,
        rpaths={
            str(executable): ("@executable_path/../Frameworks",),
            str(runtime): ("@loader_path/../..",),
        },
    )

    assert result.accepted, result.errors
    assert "@loader_path/../.." in result.rpaths


def test_trusted_tool_allowlist_returns_canonical_apple_paths() -> None:
    from sidepulse.trusted_tools import trusted_system_tool

    expected = {
        "security": Path("/usr/bin/security"),
        "ioreg": Path("/usr/sbin/ioreg"),
        "system_profiler": Path("/usr/sbin/system_profiler"),
        "shortcuts": Path("/usr/bin/shortcuts"),
        "launchctl": Path("/bin/launchctl"),
        "codesign": Path("/usr/bin/codesign"),
        "clang": Path("/usr/bin/clang"),
        "tail": Path("/usr/bin/tail"),
        "open": Path("/usr/bin/open"),
        "osascript": Path("/usr/bin/osascript"),
    }
    assert {name: trusted_system_tool(name) for name in expected} == expected


@pytest.mark.parametrize("name", ["codex", "python3", "bash", "unknown"])
def test_trusted_tool_rejects_user_or_unknown_tools(name: str) -> None:
    from sidepulse.trusted_tools import trusted_system_tool

    with pytest.raises(ValueError, match="not an allowed Apple system tool"):
        trusted_system_tool(name)


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "not-executable", "substituted"])
def test_trusted_tool_rejects_untrusted_filesystem_objects(tmp_path: Path, kind: str) -> None:
    from sidepulse import trusted_tools

    candidate = tmp_path / "security"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"tool")
        target.chmod(0o755)
        candidate.symlink_to(target)
    elif kind == "directory":
        candidate.mkdir()
    elif kind == "not-executable":
        candidate.write_bytes(b"tool")
        candidate.chmod(0o644)
    elif kind == "substituted":
        candidate.write_bytes(b"tool")
        candidate.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    with patch.dict(trusted_tools.TRUSTED_SYSTEM_TOOL_PATHS, {"security": candidate}):
        with pytest.raises((OSError, ValueError)):
            trusted_tools.trusted_system_tool("security")


def test_battery_reader_uses_trusted_ioreg_path() -> None:
    from sidepulse.battery import read_battery_snapshot

    commands: list[list[str]] = []

    def runner(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, plistlib.dumps([{}]), b"")

    read_battery_snapshot(runner=runner)

    assert commands == [["/usr/sbin/ioreg", "-r", "-n", "AppleSmartBattery", "-a"]]


def test_claude_quota_exposes_no_credential_or_subprocess_route() -> None:
    from sidepulse import claude_quota

    assert not hasattr(claude_quota, "subprocess")
    assert not hasattr(claude_quota, "access_token")
    assert not hasattr(claude_quota, "_token_from_keychain")
    assert not hasattr(claude_quota, "_token_from_file")

    with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
        with pytest.raises(
            claude_quota.ClaudeQuotaUnavailableError,
            match="claude_remote_quota_unsupported",
        ):
            claude_quota.fetch_windows()


def test_log_follow_uses_trusted_tail_path(tmp_path: Path) -> None:
    from sidepulse import cli

    log = tmp_path / "guard.log"
    log.write_text("line\n")
    args = SimpleNamespace(scope="user", follow=True, lines=12)
    with (
        patch(
            "sidepulse.sd_eject_guard_launch.log_paths_for_requested_scope",
            return_value=(log,),
        ),
        patch.object(
            cli.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as run,
    ):
        assert cli.cmd_sidepulse_sdejectguard_logs(args) == 0

    assert run.call_args.args[0][0] == "/usr/bin/tail"


def test_sd_guard_compile_and_launch_use_trusted_system_paths(tmp_path: Path) -> None:
    from sidepulse import sd_eject_guard_launch

    source = tmp_path / "guard.c"
    target = tmp_path / "guard"
    source.write_text("int main(void) { return 0; }\n")
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        if Path(command[0]).name == "clang":
            Path(command[3]).write_bytes(b"binary")
        return subprocess.CompletedProcess(command, 0)

    with patch.object(sd_eject_guard_launch.subprocess, "run", side_effect=run):
        sd_eject_guard_launch.compile_sd_eject_guard(source, target)
        sd_eject_guard_launch.restart_sd_eject_guard(tmp_path / "guard.plist", "user")

    assert commands[0][0] == "/usr/bin/clang"
    assert [command[0] for command in commands[1:]] == [
        "/bin/launchctl",
        "/bin/launchctl",
    ]


def test_sd_guard_requires_an_explicit_volume_uuid_before_registration() -> None:
    source = (REPO_ROOT / "src" / "sidepulse" / "resources" / "sd_eject_guard.c").read_text()

    assert "--volume-uuid" in source
    assert "g_selected_volume_uuid" in source
    assert "kDADiskDescriptionVolumeUUIDKey" in source
    assert 'CFStringHasPrefix(name, CFSTR("SidePulse"))' in source
    assert "if (!g_selected_volume_uuid)" in source
    assert source.index("if (!g_selected_volume_uuid)") < source.index("DARegisterDiskEjectApprovalCallback")
    assert "is_builtin_sd" not in source


def test_field_diagnostics_redacts_paths_and_device_serials() -> None:
    source = (REPO_ROOT / "scripts" / "field-diagnostics.sh").read_text()

    assert 'APP_LABEL="user Applications/SidePulse.app"' in source
    assert 'echo "app: $APP (' not in source
    assert 'echo "$CONFIG:' not in source
    assert "ls -d /Volumes/SidePulse" not in source
    assert 'grep -E "serial|' not in source
    assert "retains file names, sizes, ages, and filtered operational log lines" in source


def test_status_bar_shortcut_quit_and_openers_use_trusted_system_paths() -> None:
    try:
        from sidepulse import status_bar
    except (ImportError, SystemExit) as exc:
        pytest.skip(str(exc))

    controller = SimpleNamespace(
        closed_lid_awake=SimpleNamespace(release=lambda: None),
        keep_awake=SimpleNamespace(release=lambda: None),
    )
    commands: list[list[str]] = []

    def popen(command, **_kwargs):
        commands.append(command)
        return Mock()

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    def immediate_thread(*, target, daemon):
        assert daemon is True
        return SimpleNamespace(start=target)

    with (
        patch.object(status_bar.subprocess, "Popen", side_effect=popen),
        patch.object(status_bar.subprocess, "run", side_effect=run),
        patch.object(status_bar.threading, "Thread", side_effect=immediate_thread),
        patch.object(status_bar.os, "getppid", return_value=1),
    ):
        status_bar.StatusBarController.run_shortcut_named(controller, "Focus")
        status_bar.StatusBarController.quit_.callable(controller, None)
        status_bar.open_terminal_command("echo safe")

    assert [command[0] for command in commands] == [
        "/usr/bin/shortcuts",
        "/bin/launchctl",
        "/usr/bin/osascript",
    ]


def test_package_builder_removes_candidate_metadata_before_codesign() -> None:
    # Signing moved into packaging/sign_macos_app.py (inside-out plan); the
    # builder must still sanitize Finder metadata before handing over.
    source = (REPO_ROOT / "packaging" / "build_macos_pkg.sh").read_text()
    sanitize = source.index('/usr/bin/xattr -cr "$APP_PATH"')
    signed = source.index("packaging/sign_macos_app.py")

    assert sanitize < signed


def test_package_builder_strictly_verifies_ad_hoc_signatures() -> None:
    # The signer performs its own strict deep verification after signing,
    # for Developer ID and ad-hoc identities alike.
    source = (REPO_ROOT / "packaging" / "sign_macos_app.py").read_text()
    signed = source.index('"--sign"')
    strict_verify = source.index('"--strict"', signed)

    assert strict_verify > signed
    assert '"--verify"' in source
    assert '"--deep"' in source


def test_package_builder_collects_the_resource_package_for_installed_manifests() -> None:
    source = (REPO_ROOT / "packaging" / "build_macos_pkg.sh").read_text()

    assert "--collect-data sidepulse.resources" in source


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(0o755)


def test_package_builder_uses_isolated_roots_identity_and_pre_pkg_verifier(tmp_path: Path) -> None:
    project = tmp_path / "project"
    packaging_dir = project / "packaging"
    scripts_dir = packaging_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "packaging" / "build_macos_pkg.sh", packaging_dir)
    shutil.copy2(REPO_ROOT / "packaging" / "entitlements.plist", packaging_dir)
    shutil.copy2(REPO_ROOT / "packaging" / "sidepulse_entry.py", packaging_dir)
    shutil.copy2(REPO_ROOT / "packaging" / "verify_macos_app.py", packaging_dir)
    shutil.copy2(REPO_ROOT / "packaging" / "scripts" / "postinstall", scripts_dir)
    shutil.copy2(REPO_ROOT / "packaging" / "sign_macos_app.py", packaging_dir)
    shutil.copy2(
        REPO_ROOT / "packaging" / "sparkle_public_ed_key.txt",
        packaging_dir,
    )
    shutil.copy2(REPO_ROOT / "pyproject.toml", project)
    project_scripts = project / "scripts"
    project_scripts.mkdir()
    shutil.copy2(
        REPO_ROOT / "scripts" / "release_artifact_contract.py",
        project_scripts,
    )
    shutil.copy2(
        REPO_ROOT / "scripts" / "package_macos_artifact.py",
        project_scripts,
    )
    requirements_dir = project / "requirements"
    requirements_dir.mkdir()
    shutil.copy2(
        REPO_ROOT / "requirements" / "release-constraints.txt",
        requirements_dir,
    )
    shutil.copy2(
        REPO_ROOT / "requirements" / "release-lock.txt",
        requirements_dir,
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    event_log = tmp_path / "events.log"
    python_template = tmp_path / "venv-python"
    pyinstaller_template = tmp_path / "pyinstaller"
    _write_executable(
        python_template,
        """#!/bin/sh
set -eu
if [ "${1:-}" = "-m" ]; then
    exit 0
fi
case "${1:-}" in
    */prepare_sparkle.py)
        output=""
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --output) output="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        [ -n "$output" ]
        /bin/mkdir -p "$output/Sparkle.framework"
        printf 'fixture license\n' > "$output/LICENSE"
        exit 0
        ;;
    */package_macos_artifact.py)
        output=""
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --output-pkg) output="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        [ -n "$output" ]
        : > "$output"
        echo package >> "$PACKAGE_TEST_EVENT_LOG"
        exit 0
        ;;
esac
echo verify >> "$PACKAGE_TEST_EVENT_LOG"
exit 0
""",
    )
    _write_executable(
        pyinstaller_template,
        """#!/bin/sh
set -eu
dist=""
identifier=""
case "${PYINSTALLER_CONFIG_DIR:-}" in
    "$PACKAGE_TEST_BUILD_ROOT"/*) ;;
    *) exit 92 ;;
esac
while [ "$#" -gt 0 ]; do
    case "$1" in
        --distpath) dist="$2"; shift 2 ;;
        --osx-bundle-identifier) identifier="$2"; shift 2 ;;
        *) shift ;;
    esac
done
app="$dist/SidePulse.app"
/bin/mkdir -p "$app/Contents/MacOS" "$app/Contents/Frameworks/Python.framework"
/bin/cp /usr/bin/true "$app/Contents/MacOS/SidePulse"
/bin/cp /usr/bin/true "$app/Contents/Frameworks/Python.framework/Python"
/bin/cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>$identifier</string>
<key>CFBundleExecutable</key><string>SidePulse</string>
</dict></plist>
EOF
""",
    )
    build_python = tmp_path / "build-python"
    _write_executable(
        build_python,
        """#!/bin/sh
set -eu
# Answer the builder's interpreter version probe as a supported Python. The
# three-argument form is the public-key validation seam used by the builder.
if [ "$1" = "-c" ]; then
    if [ "$#" -eq 3 ]; then
        /bin/cat "$3"
    fi
    exit 0
fi
case "$1" in
    */validate_release_version.py)
        printf '0.5.0\n'
        exit 0
        ;;
    */release_artifact_contract.py)
        version=""
        architecture=""
        dist_dir=""
        shift
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --version) version="$2"; shift 2 ;;
                --architecture) architecture="$2"; shift 2 ;;
                --dist-dir) dist_dir="$2"; shift 2 ;;
                --format) shift 2 ;;
                *) exit 91 ;;
            esac
        done
        printf '%s/SidePulse-%s-%s.pkg\n' "$dist_dir" "$version" "$architecture"
        exit 0
        ;;
esac
if [ "$1" != "-m" ] || [ "$2" != "venv" ]; then exit 90; fi
/bin/mkdir -p "$3/bin"
/bin/cp "$PACKAGE_TEST_PYTHON_TEMPLATE" "$3/bin/python"
/bin/cp "$PACKAGE_TEST_PYINSTALLER_TEMPLATE" "$3/bin/pyinstaller"
/bin/chmod 755 "$3/bin/python" "$3/bin/pyinstaller"
""",
    )
    for command_name in ("dirname", "head", "mkdir", "pwd", "python3", "rm", "sed", "uname"):
        _write_executable(fake_bin / command_name, "#!/bin/sh\nexit 93\n")
    build_root = tmp_path / "isolated-build"
    output_root = tmp_path / "isolated-output"
    environment = {
        **os.environ,
        "ALLOW_UNSIGNED": "1",
        "BUILD_PYTHON": str(build_python),
        "BUILD_ROOT": str(build_root),
        "OUTPUT_ROOT": str(output_root),
        "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "PACKAGE_TEST_EVENT_LOG": str(event_log),
        "PACKAGE_TEST_BUILD_ROOT": str(build_root),
        "PACKAGE_TEST_PYTHON_TEMPLATE": str(python_template),
        "PACKAGE_TEST_PYINSTALLER_TEMPLATE": str(pyinstaller_template),
    }
    result = subprocess.run(
        ["/bin/bash", str(packaging_dir / "build_macos_pkg.sh")],
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    app = build_root / "pyinstaller" / "SidePulse.app"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "io.sidepulse.app"
    assert info["CFBundleDisplayName"] == "JR-Bar"
    assert info["NSAppleEventsUsageDescription"] == (
        "JR-Bar uses Automation only to open a reviewed resume command in "
        "Terminal or iTerm2 when you choose Open."
    )
    assert info["NSFocusStatusUsageDescription"] == (
        "JR-Bar uses Focus Status only when you choose Allow Focus Status, "
        "so Do Not Disturb can follow whether a macOS Focus is active."
    )
    entitlements = plistlib.loads((packaging_dir / "entitlements.plist").read_bytes())
    assert entitlements["com.apple.security.automation.apple-events"] is True
    # Three script-file invocations verify the app, then the isolated package
    # assembly seam creates the exact PKG output.
    assert event_log.read_text().splitlines() == [
        "verify",
        "verify",
        "verify",
        "verify",
        "package",
    ]
    assert list(output_root.glob("SidePulse-*.pkg"))
    assert not list(output_root.glob("*.zip"))
    assert not list(output_root.glob("*appcast*"))
