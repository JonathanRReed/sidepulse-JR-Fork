from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.install import (
    install_claude_hooks,
    install_codex_hooks,
    install_openclaw_hooks,
    install_provider_hooks,
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
        if b"# JR Bar Status" in data:
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
        # sys.executable, not "python3": the registration gate probe-runs
        # the command first, and the system python3 (no sidepulse) is now
        # correctly refused before validation ever sees the config.
        install_provider_hooks(
            "claude",
            log_path=claude_log,
            config_path=claude_config,
            python_executable=sys.executable,
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
