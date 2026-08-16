from __future__ import annotations

import hashlib
import json
import os
import plistlib
import stat
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.install import (
    RuntimeInstallCandidate,
    directory_tree_sha256,
    install_claude_hooks,
    install_codex_hooks,
    install_openclaw_hooks,
    install_provider_hooks,
    install_runtime_candidate,
    verify_runtime_candidate_identity,
)
from sidepulse.status_bar_launch import install_launch_agent

MAX_CONFIG_BYTES = 1024 * 1024


def _private_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.parent.chmod(0o700)
    path.chmod(0o600)


def _private_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text("")
    path.chmod(0o600)


def _assert_no_installer_scratch(root: Path) -> None:
    assert list(root.rglob("*.tmp")) == []


@pytest.mark.parametrize(
    ("stage", "patch_target"),
    (
        ("create scratch", "sidepulse.private_io.os.open"),
        ("write", "sidepulse.private_io._write_all"),
        ("fsync", "sidepulse.private_io.os.fsync"),
        ("replace", "sidepulse.private_io._replace_private_leaf"),
        ("directory fsync", "sidepulse.install._fsync_provider_parent"),
    ),
)
def test_claude_install_failure_before_commit_preserves_original_config(
    tmp_path: Path,
    stage: str,
    patch_target: str,
) -> None:
    """Deleting rollback or reordering a write would expose a partial install."""
    config = tmp_path / "claude" / "settings.json"
    log = tmp_path / "state" / "claude.jsonl"
    original = '{\n  "permissions": {"allow": ["Bash(date)"]}\n}\n'
    _private_file(config, original)
    _private_log(log)

    if stage == "create scratch":
        real_open = os.open

        def fail_scratch(path, flags, mode=0o777, *, dir_fd=None):
            if str(path).startswith("settings.json.") and str(path).endswith(".tmp"):
                raise OSError(stage)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        effect = fail_scratch
    elif stage == "write":
        from sidepulse.private_io import _write_all as real_write_all

        def fail_config_write(descriptor: int, data: bytes):
            if b'"hooks"' in data:
                raise OSError(stage)
            return real_write_all(descriptor, data)

        effect = fail_config_write
    elif stage == "fsync":
        real_fsync = os.fsync

        def fail_config_fsync(descriptor: int):
            info = os.fstat(descriptor)
            if stat.S_ISREG(info.st_mode) and info.st_size > len(original):
                raise OSError(stage)
            return real_fsync(descriptor)

        effect = fail_config_fsync
    elif stage == "replace":
        from sidepulse.private_io import _replace_private_leaf as real_replace

        def fail_config_replace(scratch_name: str, target_name: str, parent_descriptor: int):
            if target_name == config.name:
                raise OSError(stage)
            return real_replace(scratch_name, target_name, parent_descriptor)

        effect = fail_config_replace
    else:
        effect = OSError(stage)

    patch_options = {"create": True} if patch_target.startswith("sidepulse.install") else {}
    with (
        patch(patch_target, side_effect=effect, **patch_options),
        pytest.raises(OSError, match=stage),
    ):
        install_claude_hooks(log, config, python_executable="python3")

    assert config.read_text() == original
    assert log.read_text() == ""
    assert list(config.parent.glob(f"{config.name}.bak.*")) == []
    _assert_no_installer_scratch(tmp_path)


def test_codex_trust_refresh_failure_rolls_back_only_this_provider(tmp_path: Path) -> None:
    """Publishing hooks before trust refresh must not leave an untrusted partial config."""
    config = tmp_path / "codex" / "config.toml"
    log = tmp_path / "state" / "codex.jsonl"
    sibling = tmp_path / "claude" / "settings.json"
    original = "# user comment\n[features]\njs_repl = false\n"
    sibling_original = '{"unrelated": true}\n'
    _private_file(config, original)
    _private_file(sibling, sibling_original)
    _private_log(log)

    with (
        patch("sidepulse.install.should_refresh_codex_hook_trust", return_value=True),
        patch("sidepulse.install.resolve_codex_hook_hashes", side_effect=OSError("trust refresh")),
        pytest.raises(OSError, match="trust refresh"),
    ):
        install_codex_hooks(log, config, python_executable="python3")

    assert config.read_text() == original
    assert sibling.read_text() == sibling_original
    assert log.read_text() == ""
    _assert_no_installer_scratch(tmp_path)


def test_claude_post_verify_failure_rolls_back_config_and_preserves_log(tmp_path: Path) -> None:
    """Removing post-verify or rollback would report or retain an unverified install."""
    config = tmp_path / "claude" / "settings.json"
    log = tmp_path / "state" / "claude.jsonl"
    original = '{"permissions": {"allow": ["Bash(date)"]}}\n'
    _private_file(config, original)
    _private_log(log)

    with (
        patch(
            "sidepulse.install._verify_provider_install",
            side_effect=OSError("post-verify"),
            create=True,
        ),
        pytest.raises(OSError, match="post-verify"),
    ):
        install_claude_hooks(log, config, python_executable="python3")

    assert config.read_text() == original
    assert log.read_text() == ""
    _assert_no_installer_scratch(tmp_path)


def test_rollback_failure_is_reported_without_overwriting_replacement(tmp_path: Path) -> None:
    """A rollback must never clobber a leaf another actor replaced after publish."""
    config = tmp_path / "claude" / "settings.json"
    log = tmp_path / "state" / "claude.jsonl"
    original = '{"permissions": {"allow": ["Bash(date)"]}}\n'
    replacement = '{"replacement": true}\n'
    _private_file(config, original)
    _private_log(log)

    def replace_before_verification(*_args, **_kwargs):
        config.unlink()
        config.write_text(replacement)
        config.chmod(0o600)
        raise OSError("post-verify")

    with (
        patch(
            "sidepulse.install._verify_provider_install",
            side_effect=replace_before_verification,
            create=True,
        ),
        pytest.raises(OSError, match="rollback failed"),
    ):
        install_claude_hooks(log, config, python_executable="python3")

    assert config.read_text() == replacement
    assert log.read_text() == ""
    _assert_no_installer_scratch(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    (
        "symlink",
        "hardlink",
        "non_owner",
        "permissive_file",
        "permissive_parent",
        "oversize",
        "duplicate_key",
        "unexpected_schema",
    ),
)
def test_json_installer_validate_stage_refuses_unsafe_config_without_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Weakening config validation would let unsafe or ambiguous input be rewritten."""
    config = tmp_path / "claude" / "settings.json"
    log = tmp_path / "state" / "claude.jsonl"
    _private_file(config, '{"permissions": {"allow": ["Bash(date)"]}}\n')
    _private_log(log)
    outside = tmp_path / "outside.json"
    outside.write_text("outside stays")

    if mutation == "symlink":
        config.unlink()
        config.symlink_to(outside)
    elif mutation == "hardlink":
        config.unlink()
        os.link(outside, config)
    elif mutation == "non_owner":
        real_lstat = Path.lstat

        def wrong_owner(path: Path):
            info = real_lstat(path)
            if Path(path) == config:
                values = list(info)
                values[4] = os.getuid() + 1
                return os.stat_result(values)
            return info

        context = patch.object(Path, "lstat", autospec=True, side_effect=wrong_owner)
    elif mutation == "permissive_file":
        config.chmod(0o666)
    elif mutation == "permissive_parent":
        config.parent.chmod(0o777)
    elif mutation == "oversize":
        config.write_bytes(b" " * (MAX_CONFIG_BYTES + 1))
    elif mutation == "duplicate_key":
        config.write_text('{"hooks": {}, "hooks": {}}\n')
    else:
        config.write_text('[]\n')

    original = config.lstat()
    original_bytes = None if config.is_symlink() else config.read_bytes()
    context = context if mutation == "non_owner" else patch("sidepulse.install._NOOP", create=True)
    with context, pytest.raises((OSError, ValueError, json.JSONDecodeError)):
        install_claude_hooks(log, config, python_executable="python3")

    current = config.lstat()
    assert (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)
    if original_bytes is not None:
        assert config.read_bytes() == original_bytes
    assert outside.read_text() == "outside stays"
    assert log.read_text() == ""


def test_config_growth_during_bounded_read_is_refused_before_mutation(tmp_path: Path) -> None:
    """Removing the growth cap would allow an attacker-expanded config to be installed."""
    config = tmp_path / "claude" / "settings.json"
    log = tmp_path / "state" / "claude.jsonl"
    _private_file(config, "{}\n")
    _private_log(log)
    real_read = os.read
    grew = False

    def grow_then_read(descriptor: int, size: int) -> bytes:
        nonlocal grew
        if not grew:
            with config.open("ab") as stream:
                stream.write(b" " * (MAX_CONFIG_BYTES + 1))
            grew = True
        return real_read(descriptor, size)

    with (
        patch("sidepulse.private_io.os.read", side_effect=grow_then_read),
        pytest.raises(OSError, match="maximum size"),
    ):
        install_claude_hooks(log, config, python_executable="python3")

    assert log.read_text() == ""


def test_parent_swap_after_publish_is_rolled_back_through_the_held_parent(
    tmp_path: Path,
) -> None:
    """Path-based rollback after a parent swap would miss or overwrite the wrong leaf."""
    parent = tmp_path / "claude"
    held_parent = tmp_path / "claude-held"
    outside = tmp_path / "outside"
    config = parent / "settings.json"
    log = tmp_path / "state" / "claude.jsonl"
    original = '{"permissions": {"allow": ["Bash(date)"]}}\n'
    _private_file(config, original)
    _private_log(log)
    outside.mkdir(mode=0o700)
    swapped = False

    def swap_before_directory_fsync(*_args) -> None:
        nonlocal swapped
        if not swapped:
            parent.rename(held_parent)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        raise OSError("parent swap")

    with (
        patch(
            "sidepulse.install._fsync_provider_parent",
            side_effect=swap_before_directory_fsync,
            create=True,
        ),
        pytest.raises(OSError, match="parent swap"),
    ):
        install_claude_hooks(log, config, python_executable="python3")

    assert (held_parent / config.name).read_text() == original
    assert list(outside.iterdir()) == []
    assert parent.is_symlink()


def test_openclaw_late_write_failure_rolls_back_all_provider_owned_files(tmp_path: Path) -> None:
    """Treating coordinated OpenClaw leaves independently would retain a partial provider install."""
    config = tmp_path / "openclaw" / "openclaw.json"
    log = tmp_path / "state" / "openclaw.jsonl"
    hook_dir = config.parent / "hooks" / "sidepulse-status"
    handler = hook_dir / "handler.ts"
    hook_md = hook_dir / "HOOK.md"
    original = '{"gateway": {"port": 18789}, "unrelated": true}\n'
    _private_file(config, original)
    _private_log(log)

    from sidepulse.private_io import _write_all as real_write_all

    def fail_hook_md(descriptor: int, data: bytes):
        if b"# SidePulse Status" in data:
            raise OSError("late write")
        return real_write_all(descriptor, data)

    with (
        patch("sidepulse.private_io._write_all", side_effect=fail_hook_md),
        pytest.raises(OSError, match="late write"),
    ):
        install_openclaw_hooks(log, config, python_executable="python3")

    assert config.read_text() == original
    assert not handler.exists()
    assert not hook_md.exists()
    assert log.read_text() == ""
    _assert_no_installer_scratch(tmp_path)


def test_provider_install_failure_does_not_mutate_sibling_provider(tmp_path: Path) -> None:
    """Sharing one install transaction across providers would couple their mutations."""
    claude_config = tmp_path / "claude" / "settings.json"
    codex_config = tmp_path / "codex" / "config.toml"
    claude_log = tmp_path / "state" / "claude.jsonl"
    original_codex = "# sibling provider remains byte-identical\n"
    _private_file(claude_config, '{"hooks": {}, "hooks": {}}\n')
    _private_file(codex_config, original_codex)
    _private_log(claude_log)

    with pytest.raises(ValueError, match="duplicate"):
        install_provider_hooks(
            "claude",
            log_path=claude_log,
            config_path=claude_config,
            python_executable="python3",
        )

    assert codex_config.read_text() == original_codex


def test_provider_backups_are_bounded_and_retain_exact_preinstall_bytes(
    tmp_path: Path,
) -> None:
    """Dropping retention would grow private provider backups without bound."""
    config = tmp_path / "claude" / "settings.json"
    log = tmp_path / "state" / "claude.jsonl"
    _private_log(log)
    expected_snapshots: list[bytes] = []

    for revision in range(8):
        original = json.dumps({"unrelated": revision}, indent=2).encode() + b"\n"
        _private_file(config, original.decode())
        expected_snapshots.append(original)
        install_claude_hooks(log, config, python_executable="python3")

    backups = sorted(config.parent.glob(f"{config.name}.bak.*"))
    assert len(backups) == 5
    assert {backup.read_bytes() for backup in backups}.issubset(set(expected_snapshots))
    assert json.loads(config.read_text())["unrelated"] == 7


@pytest.mark.parametrize(
    ("provider", "filename", "original"),
    (
        ("codex", "config.toml", "# preserve codex comment\n"),
        ("claude", "settings.json", '{"unrelated": true}\n'),
        ("grok", "sidepulse.json", '{"unrelated": true}\n'),
        ("devin", "hooks.json", '{"unrelated": true}\n'),
        ("cursor", "hooks.json", '{"unrelated": true}\n'),
        ("hermes", "config.yaml", "# preserve hermes comment\nunrelated: true\n"),
        ("openclaw", "openclaw.json", '{"unrelated": true}\n'),
        ("opencode", "sidepulse.js", None),
    ),
)
def test_each_provider_post_verify_failure_rolls_back_only_its_owned_files(
    tmp_path: Path,
    provider: str,
    filename: str,
    original: str | None,
) -> None:
    """Bypassing the transaction in any provider would leave its partial install."""
    target = tmp_path / provider / filename
    log = tmp_path / "state" / f"{provider}.jsonl"
    if original is not None:
        _private_file(target, original)
    _private_log(log)
    kwargs = {
        "log_path": log,
        "python_executable": sys.executable,
        "plugin_path" if provider == "opencode" else "config_path": target,
    }

    with (
        patch("sidepulse.install._verify_provider_install", side_effect=OSError("post-verify")),
        pytest.raises(OSError, match="post-verify"),
    ):
        install_provider_hooks(provider, **kwargs)

    if original is None:
        assert not target.exists()
    else:
        assert target.read_text() == original
    if provider == "openclaw":
        assert not (target.parent / "hooks" / "sidepulse-status").exists()
    assert log.read_text() == ""


def test_launch_agent_trust_refresh_failure_restores_and_restarts_previous_job(
    tmp_path: Path,
) -> None:
    """A rejected new job must restore both the old plist and its running state."""
    plist = tmp_path / "LaunchAgents" / "io.sidepulse.agentstatus.plist"
    legacy = tmp_path / "LaunchAgents" / "com.sidepulse.agentstatus.plist"
    original = b"last-known-working-plist\n"
    _private_file(plist, original.decode("utf-8"))
    restart_payloads: list[bytes] = []

    def restart(candidate: Path) -> None:
        restart_payloads.append(candidate.read_bytes())
        if len(restart_payloads) == 1:
            raise OSError("trust refresh")

    with (
        patch("sidepulse._status_bar_launch_legacy.default_state_dir", return_value=tmp_path / "state"),
        patch("sidepulse._status_bar_launch_legacy.launch_agent_running", return_value=True),
        patch("sidepulse._status_bar_launch_legacy.restart_launch_agent", side_effect=restart),
        pytest.raises(OSError, match="trust refresh"),
    ):
        install_launch_agent(
            start=True,
            plist_path=plist,
            python_executable=Path(os.sys.executable),
            legacy_plist_path=legacy,
        )

    assert plist.read_bytes() == original
    assert len(restart_payloads) == 2
    assert restart_payloads[0] != original
    assert restart_payloads[1] == original
    assert not legacy.exists()
    _assert_no_installer_scratch(tmp_path)


def _runtime_candidate(tmp_path: Path):
    stage = tmp_path / "stage"
    payload = stage / "payload"
    package = payload / "sidepulse"
    package.mkdir(parents=True)
    package_init = package / "__init__.py"
    package_init.write_text('CANDIDATE_ID = "task-6-candidate"\n')
    package_init.chmod(0o600)
    payload.chmod(0o700)
    package.chmod(0o700)

    bundle = stage / "SidePulse.app"
    executable = bundle / "Contents" / "MacOS" / "SidePulse"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    for directory in (bundle, bundle / "Contents", executable.parent):
        directory.chmod(0o700)

    framework_versions = bundle / "Contents" / "Frameworks" / "Python.framework" / "Versions"
    framework_resources = framework_versions / "3.13" / "Resources"
    framework_resources.mkdir(parents=True)
    framework_marker = framework_resources / "Info.plist"
    framework_marker.write_text("candidate framework resource\n")
    framework_marker.chmod(0o600)
    for directory in (
        bundle / "Contents" / "Frameworks",
        bundle / "Contents" / "Frameworks" / "Python.framework",
        framework_versions,
        framework_versions / "3.13",
        framework_resources,
    ):
        directory.chmod(0o700)
    (framework_versions / "Current").symlink_to("3.13", target_is_directory=True)

    prefix = tmp_path / "prefix"
    payload_target = prefix / "runtime" / "payload"
    bundle_target = prefix / "Applications" / "SidePulse.app"
    plist_target = prefix / "Library" / "LaunchAgents" / "io.sidepulse.agentstatus.plist"
    for parent in (payload_target.parent, bundle_target.parent, plist_target.parent):
        parent.mkdir(parents=True, exist_ok=True)
        parent.chmod(0o700)
    plist = plistlib.dumps(
        {
            "Label": "io.sidepulse.agentstatus",
            "ProgramArguments": [
                str(bundle_target / "Contents" / "MacOS" / "SidePulse"),
                "status-bar",
                "start",
                "--foreground",
            ],
            "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        },
        sort_keys=False,
    )
    interpreter = Path(sys.executable).resolve(strict=True)
    return RuntimeInstallCandidate(
        interpreter=interpreter,
        interpreter_sha256=hashlib.sha256(interpreter.read_bytes()).hexdigest(),
        staged_payload=payload,
        payload_sha256=directory_tree_sha256(payload),
        staged_bundle=bundle,
        bundle_sha256=directory_tree_sha256(bundle),
        payload_destination=payload_target,
        bundle_destination=bundle_target,
        launch_agent_destination=plist_target,
        launch_agent_bytes=plist,
        replace_existing=True,
    )


def test_candidate_identity_imports_sidepulse_from_exact_staged_payload(tmp_path: Path) -> None:
    """Ambient site-packages must not satisfy the candidate import receipt."""
    candidate = _runtime_candidate(tmp_path)

    receipt = verify_runtime_candidate_identity(
        candidate.interpreter,
        candidate.interpreter_sha256,
        candidate.staged_payload,
        candidate.payload_sha256,
    )

    assert receipt.imported_package == candidate.staged_payload / "sidepulse" / "__init__.py"
    assert receipt.imported_package_sha256 == hashlib.sha256(
        receipt.imported_package.read_bytes()
    ).hexdigest()
    assert str(Path.home() / ".local" / "share" / "sidepulse") not in repr(receipt)


def test_runtime_tree_hash_accepts_and_hashes_relative_in_root_symlinks(
    tmp_path: Path,
) -> None:
    """A real PyInstaller bundle may use exact relative links within its own root."""
    root = tmp_path / "bundle"
    target = root / "Frameworks" / "Python.framework" / "Versions" / "3.13"
    target.mkdir(parents=True)
    marker = target / "marker"
    marker.write_text("runtime\n")
    marker.chmod(0o600)
    for directory in (root, root / "Frameworks", root / "Frameworks" / "Python.framework", target.parent, target):
        directory.chmod(0o700)
    link = target.parent / "Current"
    link.symlink_to("3.13", target_is_directory=True)

    first_hash = directory_tree_sha256(root)
    link.unlink()
    link.symlink_to("./3.13", target_is_directory=True)
    second_hash = directory_tree_sha256(root)

    assert first_hash != second_hash


@pytest.mark.parametrize(
    "mutation",
    ("absolute", "dangling", "escape", "non_owner_link", "non_owner_target"),
)
def test_runtime_tree_hash_refuses_unsafe_symlinks(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Only owner-held, resolving, root-confined relative links are package data."""
    root = tmp_path / "bundle"
    target = root / "Frameworks" / "target"
    target.mkdir(parents=True)
    target.chmod(0o700)
    root.chmod(0o700)
    target.parent.chmod(0o700)
    marker = target / "marker"
    marker.write_text("runtime\n")
    marker.chmod(0o600)
    link = root / "Current"
    if mutation == "absolute":
        link.symlink_to(target, target_is_directory=True)
    elif mutation == "dangling":
        link.symlink_to("missing", target_is_directory=True)
    elif mutation == "escape":
        outside = tmp_path / "outside"
        outside.mkdir(mode=0o700)
        link.symlink_to("../outside", target_is_directory=True)
    else:
        link.symlink_to("Frameworks/target", target_is_directory=True)

    real_lstat = Path.lstat

    def unsafe_owner(path: Path):
        info = real_lstat(path)
        selected = link if mutation == "non_owner_link" else marker
        if Path(path) == selected:
            values = list(info)
            values[4] = os.getuid() + 1
            return os.stat_result(values)
        return info

    context = (
        patch.object(Path, "lstat", autospec=True, side_effect=unsafe_owner)
        if mutation.startswith("non_owner")
        else patch("sidepulse.install._NOOP", create=True)
    )
    with context, pytest.raises(OSError, match=r"runtime|symlink|owner"):
        directory_tree_sha256(root)


def test_candidate_identity_refuses_payload_swap_during_import(tmp_path: Path) -> None:
    """A payload changed after preflight must not receive the expected identity receipt."""
    candidate = _runtime_candidate(tmp_path)
    package_init = candidate.staged_payload / "sidepulse" / "__init__.py"
    real_run = __import__("subprocess").run

    def swap_then_import(*args, **kwargs):
        package_init.write_text('CANDIDATE_ID = "swapped-after-preflight"\n')
        package_init.chmod(0o600)
        return real_run(*args, **kwargs)

    with (
        patch("sidepulse.install.subprocess.run", side_effect=swap_then_import),
        pytest.raises(OSError, match="payload changed"),
    ):
        verify_runtime_candidate_identity(
            candidate.interpreter,
            candidate.interpreter_sha256,
            candidate.staged_payload,
            candidate.payload_sha256,
        )


def test_runtime_publish_rolls_back_payload_bundle_and_launch_agent_together(
    tmp_path: Path,
) -> None:
    """Activation failure must not mix old payload, new bundle, or new plist."""
    candidate = _runtime_candidate(tmp_path)
    old_payload = candidate.payload_destination
    old_bundle = candidate.bundle_destination
    old_plist = candidate.launch_agent_destination
    _private_file(old_payload / "sidepulse" / "__init__.py", 'CANDIDATE_ID = "old"\n')
    old_payload.chmod(0o700)
    (old_bundle / "Contents" / "MacOS").mkdir(parents=True)
    old_executable = old_bundle / "Contents" / "MacOS" / "SidePulse"
    old_executable.write_text("#!/bin/sh\n# old bundle\n")
    old_executable.chmod(0o700)
    for directory in (old_bundle, old_bundle / "Contents", old_executable.parent):
        directory.chmod(0o700)
    old_plist_bytes = b"old launch agent\n"
    _private_file(old_plist, old_plist_bytes.decode("utf-8"))
    recovery_observations: list[tuple[str, str, bytes]] = []

    def recover_activation() -> None:
        recovery_observations.append(
            (
                (old_payload / "sidepulse" / "__init__.py").read_text(),
                old_executable.read_text(),
                old_plist.read_bytes(),
            )
        )

    with pytest.raises(OSError, match="activation"):
        install_runtime_candidate(
            candidate,
            activate=lambda: (_ for _ in ()).throw(OSError("activation")),
            recover_activation=recover_activation,
        )

    assert (old_payload / "sidepulse" / "__init__.py").read_text() == 'CANDIDATE_ID = "old"\n'
    assert old_executable.read_text() == "#!/bin/sh\n# old bundle\n"
    assert old_plist.read_bytes() == old_plist_bytes
    assert recovery_observations == [
        ('CANDIDATE_ID = "old"\n', "#!/bin/sh\n# old bundle\n", old_plist_bytes)
    ]
    assert candidate.staged_payload.exists()
    assert candidate.staged_bundle.exists()
    _assert_no_installer_scratch(tmp_path)


def test_runtime_directory_fsync_failure_restores_staged_payload(tmp_path: Path) -> None:
    """A failed durable directory publish must not strand an untracked payload."""
    candidate = _runtime_candidate(tmp_path)
    injected = False

    def fail_once(descriptor: int) -> None:
        nonlocal injected
        if not injected:
            injected = True
            raise OSError("runtime directory fsync")
        os.fsync(descriptor)

    with (
        patch(
            "sidepulse.install._fsync_runtime_parent",
            side_effect=fail_once,
        ),
        pytest.raises(OSError, match="runtime directory fsync"),
    ):
        install_runtime_candidate(
            candidate,
            activate=lambda: None,
            recover_activation=lambda: None,
        )

    assert candidate.staged_payload.exists()
    assert candidate.staged_bundle.exists()
    assert not candidate.payload_destination.exists()
    assert not candidate.bundle_destination.exists()
    assert not candidate.launch_agent_destination.exists()


def test_runtime_parent_swap_rolls_back_through_held_directory(tmp_path: Path) -> None:
    """Runtime rollback must remain anchored if a destination parent is renamed."""
    candidate = _runtime_candidate(tmp_path)
    original_parent = candidate.payload_destination.parent
    held_parent = original_parent.with_name("runtime-held")
    outside = original_parent.with_name("runtime-outside")
    old_payload = candidate.payload_destination / "sidepulse" / "__init__.py"
    _private_file(old_payload, 'CANDIDATE_ID = "old"\n')
    candidate.payload_destination.chmod(0o700)
    outside.mkdir(mode=0o700)

    def swap_then_fail() -> None:
        original_parent.rename(held_parent)
        original_parent.symlink_to(outside, target_is_directory=True)
        raise OSError("activation after parent swap")

    with pytest.raises(OSError, match="activation after parent swap"):
        install_runtime_candidate(
            candidate,
            activate=swap_then_fail,
            recover_activation=lambda: None,
        )

    assert (held_parent / "payload" / "sidepulse" / "__init__.py").read_text() == (
        'CANDIDATE_ID = "old"\n'
    )
    assert candidate.staged_payload.exists()
    assert list(outside.iterdir()) == []


def test_runtime_publish_verifies_one_installed_payload_identity_for_app_and_plist(
    tmp_path: Path,
) -> None:
    """Omitting installed post-verification would permit an old ambient payload to survive."""
    candidate = _runtime_candidate(tmp_path)

    receipt = install_runtime_candidate(
        candidate,
        activate=lambda: None,
        recover_activation=lambda: None,
    )

    assert receipt.identity.imported_package == (
        candidate.payload_destination / "sidepulse" / "__init__.py"
    )
    assert directory_tree_sha256(candidate.payload_destination) == candidate.payload_sha256
    assert directory_tree_sha256(candidate.bundle_destination) == candidate.bundle_sha256
    installed_plist = plistlib.loads(candidate.launch_agent_destination.read_bytes())
    assert installed_plist["ProgramArguments"][0] == str(
        candidate.bundle_destination / "Contents" / "MacOS" / "SidePulse"
    )
    assert "PYTHONPATH" not in installed_plist.get("EnvironmentVariables", {})
    assert "PYTHONHOME" not in installed_plist.get("EnvironmentVariables", {})


def test_runtime_publish_refuses_existing_destination_without_explicit_choice(
    tmp_path: Path,
) -> None:
    """Implicit replacement would overwrite an unrelated or last-known-good runtime."""
    candidate = replace(_runtime_candidate(tmp_path), replace_existing=False)
    _private_file(
        candidate.payload_destination / "sidepulse" / "__init__.py",
        'CANDIDATE_ID = "keep"\n',
    )
    candidate.payload_destination.chmod(0o700)

    with pytest.raises(OSError, match="already exists"):
        install_runtime_candidate(
            candidate,
            activate=lambda: None,
            recover_activation=lambda: None,
        )

    assert (
        candidate.payload_destination / "sidepulse" / "__init__.py"
    ).read_text() == 'CANDIDATE_ID = "keep"\n'
    assert candidate.staged_payload.exists()
    assert candidate.staged_bundle.exists()


def test_runtime_publish_refuses_dangling_destination_symlink(tmp_path: Path) -> None:
    """A dangling link is a collision and must never be replaced by the installer."""
    candidate = _runtime_candidate(tmp_path)
    dangling_target = tmp_path / "does-not-exist"
    candidate.payload_destination.symlink_to(dangling_target, target_is_directory=True)

    with pytest.raises(OSError, match="runtime"):
        install_runtime_candidate(
            candidate,
            activate=lambda: None,
            recover_activation=lambda: None,
        )

    assert candidate.payload_destination.is_symlink()
    assert os.readlink(candidate.payload_destination) == str(dangling_target)
    assert candidate.staged_payload.exists()
    assert candidate.staged_bundle.exists()


def test_runtime_publish_refuses_overlapping_tree_destinations(tmp_path: Path) -> None:
    """Two candidate trees must not alias one publish destination."""
    candidate = _runtime_candidate(tmp_path)
    candidate = replace(candidate, bundle_destination=candidate.payload_destination)

    with pytest.raises(ValueError, match="overlap"):
        install_runtime_candidate(
            candidate,
            activate=lambda: None,
            recover_activation=lambda: None,
        )

    assert candidate.staged_payload.exists()
    assert candidate.staged_bundle.exists()


@pytest.mark.parametrize(
    ("plist_mutation", "value"),
    (
        ("Program", "/bin/sh"),
        ("ProgramArguments", ["/bin/sh", "-c", "exit 0"]),
        ("EnvironmentVariables", {"PYTHONSTARTUP": "/tmp/attacker.py"}),
    ),
)
def test_runtime_publish_refuses_launch_agent_execution_overrides(
    tmp_path: Path,
    plist_mutation: str,
    value: object,
) -> None:
    """The installed plist must have one exact bundle-owned execution identity."""
    candidate = _runtime_candidate(tmp_path)
    plist = plistlib.loads(candidate.launch_agent_bytes)
    if plist_mutation == "EnvironmentVariables":
        plist[plist_mutation].update(value)
    else:
        plist[plist_mutation] = value
    candidate = replace(candidate, launch_agent_bytes=plistlib.dumps(plist))

    with pytest.raises(ValueError, match=r"LaunchAgent|launch agent"):
        install_runtime_candidate(
            candidate,
            activate=lambda: None,
            recover_activation=lambda: None,
        )

    assert candidate.staged_payload.exists()
    assert candidate.staged_bundle.exists()
