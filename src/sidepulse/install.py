from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Final

import tomllib

from .private_io import (
    PrivateWriteTransaction,
    RetentionPolicy,
    atomic_private_write,
    enforce_retention,
    ensure_private_directory,
    ensure_private_file,
    read_private_bytes,
    read_private_bytes_with_identity,
    read_private_text,
    read_private_text_with_identity,
    unlink_private_file_if_unchanged,
)
from .providers import (
    ANTIGRAVITY_CANONICAL_EVENTS,
    ANTIGRAVITY_ENVELOPE_KEY,
    ANTIGRAVITY_EVENTS,
    ANTIGRAVITY_GROUPED_EVENTS,
    ANTIGRAVITY_HOOK_NAME,
    CLAUDE_EVENTS,
    CODEX_EVENTS,
    CURSOR_EVENTS,
    DEVIN_EVENTS,
    GROK_EVENTS,
    HERMES_EVENTS,
    OPENCLAW_HOOK_NAME,
    default_antigravity_config_path,
    default_cursor_config_path,
    default_devin_config_path,
    default_grok_hook_config_path,
    default_hermes_config_path,
    default_openclaw_config_path,
    default_opencode_plugin_path,
    detect_log_path,
    is_sidepulse_hook_command,
    managed_opencode_plugin_log_path,
    openclaw_hook_dir,
    opencode_plugin_source_for_arguments,
)

MANAGED_START = "# >>> agent-monitor hooks >>>"
MANAGED_END = "# <<< agent-monitor hooks <<<"
BACKUP_MAX_FILES = 5
MAX_CONFIG_BYTES = 1024 * 1024
MAX_RUNTIME_TREE_FILES = 4096
MAX_RUNTIME_TREE_BYTES = 256 * 1024 * 1024
RUNTIME_BACKUP_MAX_FILES = 2


@dataclass(frozen=True, slots=True)
class _ValidatedInstallLeaf:
    path: Path
    contents: bytes | None
    identity: tuple[int, int] | None
    parent_identity: tuple[int, int]


def _validate_install_parent(path: Path) -> tuple[int, int]:
    parent = Path(path).expanduser().parent
    info = parent.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"refusing non-directory provider parent: {parent}")
    if info.st_uid != os.getuid():
        raise OSError(f"refusing non-owner provider parent: {parent}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise OSError(f"refusing permissive provider parent: {parent}")
    return (info.st_dev, info.st_ino)


def _prepare_install_parent(path: Path) -> tuple[int, int]:
    parent = Path(path).expanduser().parent
    try:
        return _validate_install_parent(path)
    except FileNotFoundError:
        ensure_private_directory(parent)
        return _validate_install_parent(path)


def _validate_install_leaf(
    path: Path,
    *,
    max_bytes: int,
    allow_missing: bool = True,
) -> _ValidatedInstallLeaf:
    target = Path(path).expanduser()
    parent_identity = _validate_install_parent(target)
    try:
        contents, identity = read_private_bytes_with_identity(
            target,
            tighten=False,
            max_bytes=max_bytes,
        )
    except FileNotFoundError:
        if not allow_missing:
            raise
        return _ValidatedInstallLeaf(target, None, None, parent_identity)
    info = target.lstat()
    if info.st_uid != os.getuid():
        raise OSError(f"refusing non-owner provider file: {target}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise OSError(f"refusing permissive provider file: {target}")
    if (info.st_dev, info.st_ino) != identity:
        raise OSError(f"provider file changed during validation: {target}")
    return _ValidatedInstallLeaf(target, contents, identity, parent_identity)


def _validated_optional_config(path: Path, *, dry_run: bool) -> _ValidatedInstallLeaf:
    target = Path(path).expanduser()
    if dry_run and not target.parent.exists():
        ancestor = target.parent
        while not ancestor.exists():
            if ancestor.parent == ancestor:
                raise OSError(f"no existing provider parent for {target}")
            ancestor = ancestor.parent
        info = ancestor.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise OSError(f"refusing unsafe provider ancestor: {ancestor}")
        return _ValidatedInstallLeaf(target, None, None, (info.st_dev, info.st_ino))
    _prepare_install_parent(target)
    return _validate_install_leaf(target, max_bytes=MAX_CONFIG_BYTES)


def _validate_install_marker(path: Path) -> _ValidatedInstallLeaf:
    target = Path(path).expanduser()
    parent_identity = _validate_install_parent(target)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return _ValidatedInstallLeaf(target, None, None, parent_identity)
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
    ):
        raise OSError(f"refusing non-regular provider file: {target}")
    if info.st_uid != os.getuid():
        raise OSError(f"refusing non-owner provider file: {target}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise OSError(f"refusing permissive provider file: {target}")
    return _ValidatedInstallLeaf(
        target,
        None,
        (info.st_dev, info.st_ino),
        parent_identity,
    )


def _decode_config(leaf: _ValidatedInstallLeaf) -> str:
    return "" if leaf.contents is None else leaf.contents.decode("utf-8")


def _strict_json_object(text: str, *, path: Path) -> dict[str, Any]:
    if not text:
        return {}

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key in provider config: {key}")
            result[key] = value
        return result

    data = json.loads(text, object_pairs_hook=reject_duplicates)
    if not isinstance(data, dict):
        raise ValueError(f"Expected an object at the top level of {path}")
    return data


def _strict_hooks_object(data: dict[str, Any], *, path: Path) -> dict[str, Any]:
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"Expected hooks object in {path}")
    return hooks


def _backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return path.with_name(
        f"{path.name}.bak.{stamp}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}"
    )


def _finish_backup_retention(path: Path) -> None:
    enforce_retention(
        path.parent,
        RetentionPolicy(
            max_files=BACKUP_MAX_FILES,
            patterns=(f"{path.name}.bak.*",),
            recursive=False,
        ),
    )


def _fsync_provider_parent(transaction: PrivateWriteTransaction, path: Path) -> None:
    transaction.fsync_parent(path)


def _verify_provider_install(
    transaction: PrivateWriteTransaction,
    expected_writes: dict[Path, bytes],
) -> None:
    for path, expected in expected_writes.items():
        transaction.verify(path, expected, max_bytes=max(MAX_CONFIG_BYTES, len(expected)))


def _transactional_provider_publish(
    *,
    config_leaf: _ValidatedInstallLeaf,
    target_log: Path,
    writes: dict[Path, str | bytes],
    backup_config: bool,
    after_publish=None,
) -> Path | None:
    validated: dict[Path, _ValidatedInstallLeaf] = {config_leaf.path: config_leaf}
    for path in writes:
        target = Path(path).expanduser()
        if target not in validated:
            _prepare_install_parent(target)
            validated[target] = _validate_install_leaf(target, max_bytes=MAX_CONFIG_BYTES)
    _prepare_install_parent(target_log)
    log_leaf = _validate_install_marker(target_log)
    backup = (
        _backup_path(config_leaf.path)
        if backup_config and config_leaf.contents is not None
        else None
    )
    expected_writes = {
        Path(path).expanduser(): data.encode("utf-8") if isinstance(data, str) else bytes(data)
        for path, data in writes.items()
    }
    with PrivateWriteTransaction() as transaction:
        if backup is not None:
            backup_parent = _prepare_install_parent(backup)
            transaction.write(
                backup,
                config_leaf.contents or b"",
                max_original_bytes=0,
                expected_identity=None,
                expected_parent_identity=backup_parent,
            )
        for path, payload in expected_writes.items():
            leaf = validated[path]
            transaction.write(
                path,
                payload,
                max_original_bytes=MAX_CONFIG_BYTES,
                expected_identity=leaf.identity,
                expected_parent_identity=leaf.parent_identity,
            )
        if after_publish is not None:
            after_publish(transaction, expected_writes)
        transaction.ensure_empty_file(
            target_log,
            expected_identity=log_leaf.identity,
            expected_parent_identity=log_leaf.parent_identity,
        )
        for path in expected_writes:
            _fsync_provider_parent(transaction, path)
        _verify_provider_install(transaction, expected_writes)
    if backup is not None:
        _finish_backup_retention(config_leaf.path)
    return backup


def _read_optional_text(path: Path, *, tighten: bool) -> str:
    try:
        return read_private_text(path, tighten=tighten)
    except FileNotFoundError:
        return ""


class CodexHookTrustStatus(str, Enum):
    """Whether Codex was pre-approved to RUN the hook we just wrote.

    Writing the hook into config.toml is half the install. Codex refuses
    to execute a hook whose hash it has not trusted, so without this
    handshake the user gets "Codex hooks installed." and a Codex that
    never calls SidePulse. Every ending here used to be one empty dict:
    no Codex binary to ask, a handshake that never answered, and a
    handshake that answered without our hook in it -- all `{}`, all
    swallowed by ``if not trusted_hashes: return``.
    """

    TRUSTED = "trusted"
    #: No `codex` binary anywhere we look, so nothing could be asked.
    CLI_NOT_FOUND = "cli_not_found"
    #: The binary ran and did not come back with our hook's hash.
    NOT_CONFIRMED = "not_confirmed"
    #: A non-default config path. Trust belongs to whoever owns that file.
    NOT_ATTEMPTED = "not_attempted"


#: One fixed product sentence per non-trusted ending. Content-free: no
#: paths, no binary locations, no stderr.
CODEX_TRUST_WARNINGS: Final[dict[CodexHookTrustStatus, str]] = {
    CodexHookTrustStatus.CLI_NOT_FOUND: (
        "The Codex CLI is not installed here, so SidePulse could not "
        "pre-approve its hook. Codex will ask you to trust it the first "
        "time it runs."
    ),
    CodexHookTrustStatus.NOT_CONFIRMED: (
        "Codex did not confirm the hook, so SidePulse could not "
        "pre-approve it. Codex will ask you to trust it the first time "
        "it runs."
    ),
}


@dataclass(frozen=True)
class CodexHookTrust:
    """The handshake's outcome and the hashes it produced, if any."""

    status: CodexHookTrustStatus
    hashes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.status) is not CodexHookTrustStatus:
            raise ValueError("invalid codex hook trust status")
        # The pairing is enforced rather than documented, for the same
        # reason AlcoveCaptureOutcome enforces its own: a status that can
        # disagree with its payload is the defect coming back.
        if (self.status is CodexHookTrustStatus.TRUSTED) != bool(self.hashes):
            raise ValueError("only a trusted handshake carries hashes")


@dataclass(frozen=True)
class InstallResult:
    provider: str
    config_path: Path
    log_path: Path
    changed: bool
    backup_path: Path | None = None
    dry_run: bool = False
    #: Codex only. None means "this provider has no trust handshake".
    codex_trust: CodexHookTrustStatus | None = None

    @property
    def public_warning(self) -> str:
        """What is still wrong after a "successful" install, or "".

        The installer reported one bit -- ``changed`` -- and an install
        that wrote the hook but could not get it trusted set that bit to
        True. This is the difference between "installed" and "installed,
        and it will not run yet".
        """
        if self.codex_trust is None:
            return ""
        return CODEX_TRUST_WARNINGS.get(self.codex_trust, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "config_path": str(self.config_path),
            "log_path": str(self.log_path),
            "changed": self.changed,
            "backup_path": str(self.backup_path) if self.backup_path else None,
            "dry_run": self.dry_run,
            "codex_trust": None if self.codex_trust is None else self.codex_trust.value,
            "warning": self.public_warning,
        }


@dataclass(frozen=True, slots=True)
class RuntimeIdentityReceipt:
    interpreter: Path
    interpreter_sha256: str
    payload_sha256: str
    imported_package: Path
    imported_package_sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeInstallCandidate:
    interpreter: Path
    interpreter_sha256: str
    staged_payload: Path
    payload_sha256: str
    staged_bundle: Path
    bundle_sha256: str
    payload_destination: Path
    bundle_destination: Path
    launch_agent_destination: Path
    launch_agent_bytes: bytes
    replace_existing: bool = False

    def __post_init__(self) -> None:
        paths = (
            self.interpreter,
            self.staged_payload,
            self.staged_bundle,
            self.payload_destination,
            self.bundle_destination,
            self.launch_agent_destination,
        )
        if not all(isinstance(path, Path) and path.is_absolute() for path in paths):
            raise ValueError("runtime installer paths must be absolute")
        hashes = (
            self.interpreter_sha256,
            self.payload_sha256,
            self.bundle_sha256,
        )
        if not all(
            type(value) is str
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in hashes
        ):
            raise ValueError("runtime installer hashes must be exact sha256 values")
        if not isinstance(self.launch_agent_bytes, bytes) or not self.launch_agent_bytes:
            raise ValueError("runtime installer LaunchAgent must be nonempty bytes")
        if type(self.replace_existing) is not bool:
            raise ValueError("runtime installer replacement choice must be explicit")


@dataclass(frozen=True, slots=True)
class RuntimeInstallReceipt:
    identity: RuntimeIdentityReceipt
    payload_destination: Path
    bundle_destination: Path
    launch_agent_destination: Path
    payload_backup: Path | None
    bundle_backup: Path | None


@dataclass(slots=True)
class _PublishedRuntimeTree:
    stage: Path
    destination: Path
    backup: Path | None
    published_identity: tuple[int, int]
    backup_identity: tuple[int, int] | None
    stage_parent_descriptor: int
    destination_parent_descriptor: int


def _validate_runtime_candidate_paths(candidate: RuntimeInstallCandidate) -> None:
    trees = (
        candidate.staged_payload,
        candidate.staged_bundle,
        candidate.payload_destination,
        candidate.bundle_destination,
    )
    normalized = tuple(Path(os.path.normpath(path)) for path in trees)
    for index, path in enumerate(normalized):
        for other in normalized[index + 1 :]:
            if path == other or path in other.parents or other in path.parents:
                raise ValueError("runtime candidate tree paths overlap")
    launch_agent = Path(os.path.normpath(candidate.launch_agent_destination))
    if any(launch_agent == tree or launch_agent in tree.parents for tree in normalized):
        raise ValueError("runtime LaunchAgent path overlaps a candidate tree")


def _sha256_file(path: Path, *, max_bytes: int = MAX_RUNTIME_TREE_BYTES) -> str:
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid not in {0, os.getuid()}
        or stat.S_IMODE(info.st_mode) & 0o022
        or info.st_size > max_bytes
    ):
        raise OSError(f"refusing unsafe runtime file: {path}")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            info.st_dev,
            info.st_ino,
            info.st_size,
        ):
            raise OSError(f"runtime file changed while opening: {path}")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise OSError("runtime file exceeds maximum size")
            digest.update(chunk)
    finally:
        os.close(descriptor)
    after = path.lstat()
    if (after.st_dev, after.st_ino, after.st_size) != (
        info.st_dev,
        info.st_ino,
        info.st_size,
    ):
        raise OSError(f"runtime file changed while hashing: {path}")
    return digest.hexdigest()


def directory_tree_sha256(root: Path) -> str:
    """Hash one bounded runtime tree, including safe internal relative links."""
    selected = Path(root)
    root_info = selected.lstat()
    if (
        stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.getuid()
        or stat.S_IMODE(root_info.st_mode) & 0o022
    ):
        raise OSError(f"refusing unsafe runtime tree: {selected}")
    selected_resolved = selected.resolve(strict=True)
    digest = hashlib.sha256()
    total_bytes = 0
    count = 0
    for path in sorted(selected.rglob("*"), key=lambda candidate: candidate.relative_to(selected).as_posix()):
        count += 1
        if count > MAX_RUNTIME_TREE_FILES:
            raise OSError("runtime tree exceeds maximum file count")
        relative = path.relative_to(selected).as_posix().encode("utf-8")
        info = path.lstat()
        if info.st_uid != os.getuid():
            raise OSError(f"refusing non-owner runtime path: {path}")
        if stat.S_ISLNK(info.st_mode):
            link_target_text = os.readlink(path)
            link_target = Path(link_target_text)
            if link_target.is_absolute():
                raise OSError(f"refusing absolute runtime symlink: {path}")
            target_bytes = os.fsencode(link_target_text)
            if not target_bytes or len(target_bytes) > 4096:
                raise OSError(f"refusing invalid runtime symlink target: {path}")
            try:
                resolved_target = (path.parent / link_target).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise OSError(f"refusing dangling runtime symlink: {path}") from exc
            if not (
                resolved_target == selected_resolved
                or selected_resolved in resolved_target.parents
            ):
                raise OSError(f"refusing escaping runtime symlink: {path}")
            target_info = resolved_target.lstat()
            if (
                target_info.st_uid != os.getuid()
                or stat.S_IMODE(target_info.st_mode) & 0o022
                or not (
                    stat.S_ISDIR(target_info.st_mode)
                    or (
                        stat.S_ISREG(target_info.st_mode)
                        and target_info.st_nlink == 1
                    )
                )
            ):
                raise OSError(f"refusing unsafe runtime symlink target: {path}")
            total_bytes += len(target_bytes)
            if total_bytes > MAX_RUNTIME_TREE_BYTES:
                raise OSError("runtime tree exceeds maximum byte size")
            digest.update(b"L\0" + relative + b"\0" + target_bytes + b"\0")
            continue
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o022:
            raise OSError(f"refusing permissive runtime path: {path}")
        if stat.S_ISDIR(info.st_mode):
            digest.update(b"D\0" + relative + b"\0" + str(mode).encode("ascii") + b"\0")
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError(f"refusing unexpected runtime path: {path}")
        total_bytes += info.st_size
        if total_bytes > MAX_RUNTIME_TREE_BYTES:
            raise OSError("runtime tree exceeds maximum byte size")
        digest.update(b"F\0" + relative + b"\0" + str(mode).encode("ascii") + b"\0")
        digest.update(bytes.fromhex(_sha256_file(path)))
    after = selected.lstat()
    if (after.st_dev, after.st_ino) != (root_info.st_dev, root_info.st_ino):
        raise OSError(f"runtime tree changed while hashing: {selected}")
    return digest.hexdigest()


def verify_runtime_candidate_identity(
    interpreter: Path,
    interpreter_sha256: str,
    payload_root: Path,
    payload_sha256: str,
) -> RuntimeIdentityReceipt:
    """Import SidePulse in isolated mode from exactly one candidate payload."""
    selected_interpreter = Path(interpreter)
    if _sha256_file(selected_interpreter) != interpreter_sha256:
        raise OSError("runtime interpreter hash mismatch")
    selected_payload = Path(payload_root)
    if directory_tree_sha256(selected_payload) != payload_sha256:
        raise OSError("runtime payload hash mismatch")
    script = """
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve(strict=True)
sys.path.insert(0, str(root))
import sidepulse
module = pathlib.Path(sidepulse.__file__).resolve(strict=True)
print(json.dumps({"module": str(module), "sha256": hashlib.sha256(module.read_bytes()).hexdigest()}))
"""
    process = subprocess.run(
        [str(selected_interpreter), "-I", "-S", "-B", "-c", script, str(selected_payload)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    if process.returncode != 0:
        raise OSError("runtime candidate identity import failed")
    if directory_tree_sha256(selected_payload) != payload_sha256:
        raise OSError("runtime payload changed during identity import")
    try:
        result = json.loads(process.stdout)
        imported = Path(result["module"])
        imported_hash = result["sha256"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OSError("runtime candidate identity receipt was malformed") from exc
    expected_import = selected_payload.resolve(strict=True) / "sidepulse" / "__init__.py"
    if imported != expected_import or _sha256_file(imported) != imported_hash:
        raise OSError("runtime candidate imported an unexpected SidePulse payload")
    return RuntimeIdentityReceipt(
        interpreter=selected_interpreter,
        interpreter_sha256=interpreter_sha256,
        payload_sha256=payload_sha256,
        imported_package=imported,
        imported_package_sha256=imported_hash,
    )


def _validate_runtime_launch_agent(candidate: RuntimeInstallCandidate) -> None:
    try:
        plist = __import__("plistlib").loads(candidate.launch_agent_bytes)
    except Exception as exc:
        raise ValueError("runtime candidate LaunchAgent is invalid") from exc
    executable = candidate.bundle_destination / "Contents" / "MacOS" / "SidePulse"
    environment = plist.get("EnvironmentVariables", {}) if isinstance(plist, dict) else {}
    expected_arguments = [
        str(executable),
        "status-bar",
        "start",
        "--foreground",
    ]
    safe_environment = (
        isinstance(environment, dict)
        and set(environment).issubset({"PATH", "PYTHONUNBUFFERED"})
        and environment.get("PATH") == "/usr/bin:/bin:/usr/sbin:/sbin"
        and environment.get("PYTHONUNBUFFERED", "1") == "1"
        and all(type(key) is str and type(value) is str for key, value in environment.items())
    )
    if not (
        isinstance(plist, dict)
        and plist.get("Label") == "io.sidepulse.agentstatus"
        and plist.get("ProgramArguments") == expected_arguments
        and "Program" not in plist
        and safe_environment
    ):
        raise ValueError("runtime candidate LaunchAgent does not bind the candidate bundle")


def _fsync_directory_path(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _runtime_directory_info(
    descriptor: int,
    name: str,
    *,
    display_path: Path,
) -> os.stat_result | None:
    try:
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise OSError(f"refusing unsafe runtime directory: {display_path}")
    return info


def _open_runtime_parent(path: Path, expected_identity: tuple[int, int]) -> int:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022
            or (info.st_dev, info.st_ino) != expected_identity
        ):
            raise OSError(f"runtime parent changed before publish: {path}")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _fsync_runtime_parent(descriptor: int) -> None:
    """Durably publish a runtime directory rename, kept narrow for injection."""
    os.fsync(descriptor)


def _runtime_replace(
    source_name: str,
    destination_name: str,
    source_parent: int,
    destination_parent: int,
) -> None:
    os.replace(
        source_name,
        destination_name,
        src_dir_fd=source_parent,
        dst_dir_fd=destination_parent,
    )


def _publish_runtime_tree(
    stage: Path,
    destination: Path,
    *,
    expected_stage_identity: tuple[int, int],
    expected_destination_identity: tuple[int, int] | None,
    expected_stage_parent_identity: tuple[int, int],
    expected_destination_parent_identity: tuple[int, int],
) -> _PublishedRuntimeTree:
    stage_parent_descriptor = _open_runtime_parent(
        stage.parent,
        expected_stage_parent_identity,
    )
    try:
        destination_parent_descriptor = _open_runtime_parent(
            destination.parent,
            expected_destination_parent_identity,
        )
    except BaseException:
        os.close(stage_parent_descriptor)
        raise
    keep_descriptors = False
    backup = None
    backup_name = None
    backup_identity = None
    backup_moved = False
    published = False
    try:
        stage_info = _runtime_directory_info(
            stage_parent_descriptor,
            stage.name,
            display_path=stage,
        )
        stage_identity = (
            None if stage_info is None else (stage_info.st_dev, stage_info.st_ino)
        )
        if stage_identity != expected_stage_identity:
            raise OSError(f"runtime stage changed before publish: {stage}")
        destination_info = _runtime_directory_info(
            destination_parent_descriptor,
            destination.name,
            display_path=destination,
        )
        destination_identity = (
            None
            if destination_info is None
            else (destination_info.st_dev, destination_info.st_ino)
        )
        if destination_identity != expected_destination_identity:
            raise OSError(f"runtime destination changed before publish: {destination}")
        if stage_info is None or stage_info.st_dev != os.fstat(
            destination_parent_descriptor
        ).st_dev:
            raise OSError("runtime staging and destination must share a filesystem")
        if destination_info is not None:
            backup_name = (
                f"{destination.name}.bak."
                f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}."
                f"{uuid.uuid4().hex}"
            )
            if os.stat(
                backup_name,
                dir_fd=destination_parent_descriptor,
                follow_symlinks=False,
            ):
                raise OSError("runtime backup collision")
    except FileNotFoundError:
        pass
    try:
        if destination_info is not None:
            backup = destination.with_name(backup_name or "")
            backup_identity = destination_identity
            _runtime_replace(
                destination.name,
                backup_name or "",
                destination_parent_descriptor,
                destination_parent_descriptor,
            )
            backup_moved = True
            _fsync_runtime_parent(destination_parent_descriptor)
        _runtime_replace(
            stage.name,
            destination.name,
            stage_parent_descriptor,
            destination_parent_descriptor,
        )
        published = True
        current = _runtime_directory_info(
            destination_parent_descriptor,
            destination.name,
            display_path=destination,
        )
        if current is None or (current.st_dev, current.st_ino) != expected_stage_identity:
            raise OSError(f"runtime tree changed during publish: {destination}")
        _fsync_runtime_parent(destination_parent_descriptor)
        _fsync_runtime_parent(stage_parent_descriptor)
        keep_descriptors = True
        return _PublishedRuntimeTree(
            stage=stage,
            destination=destination,
            backup=backup,
            published_identity=expected_stage_identity,
            backup_identity=backup_identity,
            stage_parent_descriptor=stage_parent_descriptor,
            destination_parent_descriptor=destination_parent_descriptor,
        )
    except BaseException as primary_error:
        rollback_errors: list[OSError] = []
        try:
            if published:
                current = _runtime_directory_info(
                    destination_parent_descriptor,
                    destination.name,
                    display_path=destination,
                )
                staged = _runtime_directory_info(
                    stage_parent_descriptor,
                    stage.name,
                    display_path=stage,
                )
                if current is None or (
                    current.st_dev,
                    current.st_ino,
                ) != expected_stage_identity or staged is not None:
                    raise OSError("runtime publish changed before local rollback")
                _runtime_replace(
                    destination.name,
                    stage.name,
                    destination_parent_descriptor,
                    stage_parent_descriptor,
                )
            if backup_moved:
                current_destination = _runtime_directory_info(
                    destination_parent_descriptor,
                    destination.name,
                    display_path=destination,
                )
                backup_info = _runtime_directory_info(
                    destination_parent_descriptor,
                    backup_name or "",
                    display_path=backup or destination,
                )
                if current_destination is not None or backup_info is None or (
                    backup_info.st_dev,
                    backup_info.st_ino,
                ) != backup_identity:
                    raise OSError("runtime backup changed before local rollback")
                _runtime_replace(
                    backup_name or "",
                    destination.name,
                    destination_parent_descriptor,
                    destination_parent_descriptor,
                )
            _fsync_runtime_parent(destination_parent_descriptor)
            _fsync_runtime_parent(stage_parent_descriptor)
        except OSError as rollback_error:
            rollback_errors.append(rollback_error)
        if rollback_errors:
            raise OSError("runtime publish rollback failed") from rollback_errors[0]
        raise primary_error
    finally:
        if not keep_descriptors:
            os.close(destination_parent_descriptor)
            os.close(stage_parent_descriptor)


def _rollback_runtime_trees(published: list[_PublishedRuntimeTree]) -> None:
    errors: list[OSError] = []
    for record in reversed(published):
        try:
            current = _runtime_directory_info(
                record.destination_parent_descriptor,
                record.destination.name,
                display_path=record.destination,
            )
            staged = _runtime_directory_info(
                record.stage_parent_descriptor,
                record.stage.name,
                display_path=record.stage,
            )
            if current is None or (
                current.st_dev,
                current.st_ino,
            ) != record.published_identity or staged is not None:
                raise OSError(f"runtime destination changed before rollback: {record.destination}")
            _runtime_replace(
                record.destination.name,
                record.stage.name,
                record.destination_parent_descriptor,
                record.stage_parent_descriptor,
            )
            if record.backup is not None:
                backup_info = _runtime_directory_info(
                    record.destination_parent_descriptor,
                    record.backup.name,
                    display_path=record.backup,
                )
                if backup_info is None or (
                    backup_info.st_dev,
                    backup_info.st_ino,
                ) != record.backup_identity:
                    raise OSError(f"runtime backup changed before rollback: {record.backup}")
                _runtime_replace(
                    record.backup.name,
                    record.destination.name,
                    record.destination_parent_descriptor,
                    record.destination_parent_descriptor,
                )
            _fsync_runtime_parent(record.destination_parent_descriptor)
            _fsync_runtime_parent(record.stage_parent_descriptor)
        except OSError as exc:
            errors.append(exc)
    if errors:
        raise OSError("runtime rollback failed") from errors[0]


def _close_runtime_trees(published: list[_PublishedRuntimeTree]) -> None:
    for record in published:
        os.close(record.destination_parent_descriptor)
        os.close(record.stage_parent_descriptor)


def _prune_runtime_backups(destination: Path) -> None:
    backups = sorted(destination.parent.glob(f"{destination.name}.bak.*"))
    for backup in backups[:-RUNTIME_BACKUP_MAX_FILES]:
        _remove_owned_tree(backup)


def install_runtime_candidate(
    candidate: RuntimeInstallCandidate,
    *,
    activate,
    recover_activation,
) -> RuntimeInstallReceipt:
    """Publish one verified payload, app bundle, and LaunchAgent transaction."""
    if (
        type(candidate) is not RuntimeInstallCandidate
        or not callable(activate)
        or not callable(recover_activation)
    ):
        raise ValueError("invalid runtime install candidate")
    _validate_runtime_candidate_paths(candidate)
    _validate_runtime_launch_agent(candidate)
    verify_runtime_candidate_identity(
        candidate.interpreter,
        candidate.interpreter_sha256,
        candidate.staged_payload,
        candidate.payload_sha256,
    )
    if directory_tree_sha256(candidate.staged_bundle) != candidate.bundle_sha256:
        raise OSError("runtime bundle hash mismatch")
    bundle_executable = candidate.staged_bundle / "Contents" / "MacOS" / "SidePulse"
    executable_info = bundle_executable.lstat()
    if not stat.S_ISREG(executable_info.st_mode) or not executable_info.st_mode & 0o100:
        raise OSError("runtime bundle executable is missing or not executable")
    staged_identities: dict[Path, tuple[int, int]] = {}
    staged_parent_identities: dict[Path, tuple[int, int]] = {}
    destination_identities: dict[Path, tuple[int, int] | None] = {}
    destination_parent_identities: dict[Path, tuple[int, int]] = {}
    for stage in (candidate.staged_payload, candidate.staged_bundle):
        stage_info = stage.lstat()
        staged_identities[stage] = (stage_info.st_dev, stage_info.st_ino)
        staged_parent_identities[stage] = _validate_install_parent(stage)
    for destination in (candidate.payload_destination, candidate.bundle_destination):
        destination_parent_identities[destination] = _prepare_install_parent(destination)
        destination_info = _existing_directory_kind(destination)
        destination_identities[destination] = (
            None
            if destination_info is None
            else (destination_info.st_dev, destination_info.st_ino)
        )
        if destination_info is not None:
            if not candidate.replace_existing:
                raise OSError(f"runtime destination already exists: {destination}")
            directory_tree_sha256(destination)
    _prepare_install_parent(candidate.launch_agent_destination)
    launch_leaf = _validate_install_leaf(
        candidate.launch_agent_destination,
        max_bytes=MAX_CONFIG_BYTES,
    )
    if launch_leaf.identity is not None and not candidate.replace_existing:
        raise OSError("runtime LaunchAgent already exists")
    published: list[_PublishedRuntimeTree] = []
    activation_attempted = False
    try:
        with PrivateWriteTransaction() as transaction:
            published.append(
                _publish_runtime_tree(
                    candidate.staged_payload,
                    candidate.payload_destination,
                    expected_stage_identity=staged_identities[candidate.staged_payload],
                    expected_destination_identity=destination_identities[
                        candidate.payload_destination
                    ],
                    expected_stage_parent_identity=staged_parent_identities[
                        candidate.staged_payload
                    ],
                    expected_destination_parent_identity=destination_parent_identities[
                        candidate.payload_destination
                    ],
                )
            )
            published.append(
                _publish_runtime_tree(
                    candidate.staged_bundle,
                    candidate.bundle_destination,
                    expected_stage_identity=staged_identities[candidate.staged_bundle],
                    expected_destination_identity=destination_identities[
                        candidate.bundle_destination
                    ],
                    expected_stage_parent_identity=staged_parent_identities[
                        candidate.staged_bundle
                    ],
                    expected_destination_parent_identity=destination_parent_identities[
                        candidate.bundle_destination
                    ],
                )
            )
            transaction.write(
                candidate.launch_agent_destination,
                candidate.launch_agent_bytes,
                max_original_bytes=MAX_CONFIG_BYTES,
                expected_identity=launch_leaf.identity,
                expected_parent_identity=launch_leaf.parent_identity,
            )
            transaction.verify(
                candidate.launch_agent_destination,
                candidate.launch_agent_bytes,
                max_bytes=MAX_CONFIG_BYTES,
            )
            installed_identity = verify_runtime_candidate_identity(
                candidate.interpreter,
                candidate.interpreter_sha256,
                candidate.payload_destination,
                candidate.payload_sha256,
            )
            if directory_tree_sha256(candidate.bundle_destination) != candidate.bundle_sha256:
                raise OSError("installed runtime bundle hash mismatch")
            activation_attempted = True
            activate()
    except BaseException as primary_error:
        try:
            _rollback_runtime_trees(published)
        finally:
            _close_runtime_trees(published)
        if activation_attempted:
            try:
                recover_activation()
            except BaseException as recovery_error:
                raise OSError("runtime activation recovery failed") from recovery_error
        raise primary_error
    _close_runtime_trees(published)
    _prune_runtime_backups(candidate.payload_destination)
    _prune_runtime_backups(candidate.bundle_destination)
    return RuntimeInstallReceipt(
        identity=installed_identity,
        payload_destination=candidate.payload_destination,
        bundle_destination=candidate.bundle_destination,
        launch_agent_destination=candidate.launch_agent_destination,
        payload_backup=published[0].backup,
        bundle_backup=published[1].backup,
    )


def install_codex_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    config = config_path or Path.home() / ".codex" / "config.toml"
    target_log = (log_path or detect_log_path("codex")).expanduser()
    config_leaf = _validated_optional_config(config, dry_run=dry_run)
    original = _decode_config(config_leaf)
    if original:
        tomllib.loads(original)

    block = codex_hook_block(target_log, python_executable)
    if is_pristine_codex_hook_install(original, block, target_log):
        new_text = original
    else:
        text = strip_managed_block(original)
        text = remove_codex_hook_blocks_for_log(text, target_log)
        text = ensure_codex_hooks_feature(text)
        new_text = _ensure_trailing_newline(text) + "\n" + block
    changed = new_text != original

    backup = None
    # A non-default config path is the one ending that is not a failure:
    # trust for a file the user pointed us at is theirs to grant. It is
    # still said out loud rather than left as the absence of a warning.
    trust_status: CodexHookTrustStatus = CodexHookTrustStatus.NOT_ATTEMPTED
    if not dry_run:
        refresh_trust = should_refresh_codex_hook_trust(config, config_path)

        def after_publish(
            transaction: PrivateWriteTransaction,
            expected_writes: dict[Path, bytes],
        ) -> None:
            nonlocal changed, trust_status
            if not refresh_trust:
                return
            trust = resolve_codex_hook_trust(config)
            trust_status = trust.status
            trusted_hashes = trust.hashes
            if not trusted_hashes:
                # Still not a raise: the hook IS written and half-working
                # beats no hook. But the caller now learns WHICH of the
                # three reasons this was, instead of an empty dict that
                # looked exactly like "nothing needed doing".
                return
            current = expected_writes.get(config, original.encode("utf-8")).decode("utf-8")
            trusted_text = update_codex_trusted_hashes(current, trusted_hashes)
            if trusted_text == current:
                return
            payload = trusted_text.encode("utf-8")
            transaction.write(
                config,
                payload,
                max_original_bytes=MAX_CONFIG_BYTES,
                expected_identity=config_leaf.identity,
                expected_parent_identity=config_leaf.parent_identity,
            )
            expected_writes[config] = payload
            changed = True

        if changed:
            backup = _transactional_provider_publish(
                config_leaf=config_leaf,
                target_log=target_log,
                writes={config: new_text},
                backup_config=True,
                after_publish=after_publish,
            )
        elif refresh_trust:
            trust = resolve_codex_hook_trust(config)
            trust_status = trust.status
            trusted_hashes = trust.hashes
            trusted_text = update_codex_trusted_hashes(original, trusted_hashes)
            if trusted_text != original:
                changed = True
                backup = _transactional_provider_publish(
                    config_leaf=config_leaf,
                    target_log=target_log,
                    writes={config: trusted_text},
                    backup_config=True,
                )
            else:
                _prepare_install_parent(target_log)
                log_leaf = _validate_install_marker(target_log)
                with PrivateWriteTransaction() as transaction:
                    transaction.ensure_empty_file(
                        target_log,
                        expected_identity=log_leaf.identity,
                        expected_parent_identity=log_leaf.parent_identity,
                    )
        else:
            _prepare_install_parent(target_log)
            log_leaf = _validate_install_marker(target_log)
            with PrivateWriteTransaction() as transaction:
                transaction.ensure_empty_file(
                    target_log,
                    expected_identity=log_leaf.identity,
                    expected_parent_identity=log_leaf.parent_identity,
                )

    return InstallResult(
        "codex",
        config,
        target_log,
        changed,
        backup,
        dry_run,
        codex_trust=trust_status,
    )


def install_claude_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    config = config_path or Path.home() / ".claude" / "settings.json"
    target_log = (log_path or detect_log_path("claude")).expanduser()

    config_leaf = _validated_optional_config(config, dry_run=dry_run)
    original_text = _decode_config(config_leaf)
    data = _strict_json_object(original_text, path=config)

    original = json.dumps(data, sort_keys=True)
    hooks = _strict_hooks_object(data, path=config)
    command = hook_command("claude", target_log, python_executable)

    for event_name in CLAUDE_EVENTS:
        entries = hooks.get(event_name, [])
        if not isinstance(entries, list):
            raise ValueError(f"Expected hooks.{event_name} array in {config}")
        cleaned = remove_claude_hooks_for_log(entries, target_log)
        cleaned.append({"matcher": "*", "hooks": [{"type": "command", "command": command}]})
        hooks[event_name] = cleaned

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        backup = _transactional_provider_publish(
            config_leaf=config_leaf,
            target_log=target_log,
            writes={config: json.dumps(data, indent=2, sort_keys=False) + "\n"},
            backup_config=True,
        )

    return InstallResult("claude", config, target_log, changed, backup, dry_run)


def install_grok_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    config = config_path or default_grok_hook_config_path()
    target_log = (log_path or detect_log_path("grok")).expanduser()
    config_leaf = _validated_optional_config(config, dry_run=dry_run)
    data = _strict_json_object(_decode_config(config_leaf), path=config)

    original = json.dumps(data, sort_keys=True)
    hooks = _strict_hooks_object(data, path=config)
    command = hook_command("grok", target_log, python_executable)

    for event_name in GROK_EVENTS:
        entries = hooks.get(event_name, [])
        if not isinstance(entries, list):
            raise ValueError(f"Expected hooks.{event_name} array in {config}")
        cleaned = remove_json_command_hooks_for_log(entries, target_log, "grok")
        cleaned.append(grok_hook_entry(event_name, command))
        hooks[event_name] = cleaned

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        backup = _transactional_provider_publish(
            config_leaf=config_leaf,
            target_log=target_log,
            writes={config: json.dumps(data, indent=2, sort_keys=False) + "\n"},
            backup_config=True,
        )

    return InstallResult("grok", config, target_log, changed, backup, dry_run)


def install_devin_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    config = config_path or default_devin_config_path()
    target_log = (log_path or detect_log_path("devin")).expanduser()
    config_leaf = _validated_optional_config(config, dry_run=dry_run)
    data = _strict_json_object(_decode_config(config_leaf), path=config)

    original = json.dumps(data, sort_keys=True)
    hooks = _strict_hooks_object(data, path=config)
    command = hook_command("devin", target_log, python_executable)
    for event_name in DEVIN_EVENTS:
        entries = hooks.get(event_name, [])
        if not isinstance(entries, list):
            raise ValueError(f"Expected hooks.{event_name} array in {config}")
        cleaned = remove_json_command_hooks_for_log(entries, target_log, "devin")
        cleaned.append({"hooks": [{"type": "command", "command": command}]})
        hooks[event_name] = cleaned

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        backup = _transactional_provider_publish(
            config_leaf=config_leaf,
            target_log=target_log,
            writes={config: json.dumps(data, indent=2, sort_keys=False) + "\n"},
            backup_config=True,
        )

    return InstallResult("devin", config, target_log, changed, backup, dry_run)


def _remove_flat_sidepulse_hooks(entries: list[Any], provider: str) -> list[Any]:
    """Drops SidePulse's own flat {"command": ...} hook entries, keeping
    everything else byte-identical -- for configs (Cursor, Hermes) whose
    hook entries hold the command directly rather than Claude's nested
    {"hooks": [...]} shape."""
    cleaned: list[Any] = []
    for entry in entries:
        if isinstance(entry, dict):
            command = entry.get("command")
            if isinstance(command, str) and is_sidepulse_hook_command(command, provider):
                continue
        cleaned.append(entry)
    return cleaned


def install_cursor_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    """Adds SidePulse's command to ~/.cursor/hooks.json (shared user-level
    file: other tools' hooks and unknown keys are preserved untouched)."""
    config = config_path or default_cursor_config_path()
    target_log = (log_path or detect_log_path("cursor")).expanduser()
    config_leaf = _validated_optional_config(config, dry_run=dry_run)
    data = _strict_json_object(_decode_config(config_leaf), path=config)

    original = json.dumps(data, sort_keys=True)
    data.setdefault("version", 1)
    if not isinstance(data["version"], int) or isinstance(data["version"], bool):
        raise ValueError(f"Expected integer version in {config}")
    hooks = _strict_hooks_object(data, path=config)
    command = hook_command("cursor", target_log, python_executable)

    for event_name in CURSOR_EVENTS:
        entries = hooks.get(event_name, [])
        if not isinstance(entries, list):
            raise ValueError(f"Expected hooks.{event_name} array in {config}")
        cleaned = _remove_flat_sidepulse_hooks(entries, "cursor")
        cleaned.append({"command": command})
        hooks[event_name] = cleaned

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        backup = _transactional_provider_publish(
            config_leaf=config_leaf,
            target_log=target_log,
            writes={config: json.dumps(data, indent=2, sort_keys=False) + "\n"},
            backup_config=True,
        )

    return InstallResult("cursor", config, target_log, changed, backup, dry_run)


def uninstall_cursor_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or default_cursor_config_path()
    target_log = (log_path or detect_log_path("cursor")).expanduser()
    data = read_json_config(config, tighten=not dry_run)

    original = json.dumps(data, sort_keys=True)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event_name in list(hooks):
            entries = hooks.get(event_name)
            if event_name not in CURSOR_EVENTS or not isinstance(entries, list):
                continue
            cleaned = _remove_flat_sidepulse_hooks(entries, "cursor")
            if cleaned:
                hooks[event_name] = cleaned
            else:
                hooks.pop(event_name, None)
        if not hooks:
            data.pop("hooks", None)

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        backup = backup_file(config)
        _private_config_write(config, json.dumps(data, indent=2, sort_keys=False) + "\n")

    return InstallResult("cursor", config, target_log, changed, backup, dry_run)


def _hermes_yaml():
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    return yaml


def _hermes_dump(yaml, data) -> str:
    import io

    buffer = io.StringIO()
    yaml.dump(data, buffer)
    return buffer.getvalue()


def install_hermes_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    """Adds SidePulse's shell hooks to ~/.hermes/config.yaml's hooks:
    block. Edited with a round-trip YAML parser (ruamel) specifically so
    the user's own comments and formatting survive -- config.yaml is a
    hand-maintained file for most Hermes users."""
    config = config_path or default_hermes_config_path()
    target_log = (log_path or detect_log_path("hermes")).expanduser()
    yaml = _hermes_yaml()
    config_leaf = _validated_optional_config(config, dry_run=dry_run)
    original_text = _decode_config(config_leaf)
    if original_text:
        data = yaml.load(original_text)
        if data is None:
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping at the top level of {config}")

    original = _hermes_dump(yaml, data)
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise ValueError(f"Expected hooks mapping in {config}")
    command = hook_command("hermes", target_log, python_executable)

    for event_name in HERMES_EVENTS:
        entries = hooks.get(event_name)
        if not isinstance(entries, list):
            entries = []
        cleaned = _remove_flat_sidepulse_hooks(list(entries), "hermes")
        # Hooks time out per invocation; ours just appends a JSON line,
        # so a tight timeout keeps a wedged filesystem from ever
        # stalling the agent loop.
        cleaned.append({"command": command, "timeout": 10})
        hooks[event_name] = cleaned

    changed = _hermes_dump(yaml, data) != original
    backup = None
    if changed and not dry_run:
        backup = _transactional_provider_publish(
            config_leaf=config_leaf,
            target_log=target_log,
            writes={config: _hermes_dump(yaml, data)},
            backup_config=True,
        )

    return InstallResult("hermes", config, target_log, changed, backup, dry_run)


def uninstall_hermes_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or default_hermes_config_path()
    target_log = (log_path or detect_log_path("hermes")).expanduser()
    yaml = _hermes_yaml()
    original_text = _read_optional_text(config, tighten=not dry_run)
    if original_text:
        data = yaml.load(original_text)
        if data is None:
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        return InstallResult("hermes", config, target_log, False, None, dry_run)

    original = _hermes_dump(yaml, data)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event_name in list(hooks):
            entries = hooks.get(event_name)
            if event_name not in HERMES_EVENTS or not isinstance(entries, list):
                continue
            cleaned = _remove_flat_sidepulse_hooks(list(entries), "hermes")
            if cleaned:
                hooks[event_name] = cleaned
            else:
                del hooks[event_name]
        if not hooks:
            data.pop("hooks", None)

    changed = _hermes_dump(yaml, data) != original
    backup = None
    if changed and not dry_run:
        backup = backup_file(config)
        _private_config_write(config, _hermes_dump(yaml, data))

    return InstallResult("hermes", config, target_log, changed, backup, dry_run)


def openclaw_handler_source(log_path: Path, python_executable: str | None = None) -> str:
    """The in-gateway JS handler OpenClaw loads for SidePulse. It maps the
    gateway's own events to SidePulse's canonical hook events and forwards
    each as one short-lived detached process -- OpenClaw's hook contract
    forbids handlers owning long-lived resources, so each event is
    fire-and-forget."""
    executable = python_executable or sys.executable or "python3"
    entry_point = Path(__file__).with_name("hook_entry.py")
    return f"""// Managed by SidePulse -- reinstalling overwrites this file.
import {{ spawn }} from "node:child_process";

const EVENT_MAP = {{
  "command:new": "SessionStart",
  "message:received": "UserPromptSubmit",
  "message:sent": "Stop",
  "command:stop": "SessionEnd",
}};

const handler = async (event) => {{
  const mapped = EVENT_MAP[`${{event.type}}:${{event.action}}`];
  if (!mapped) return;
  const payload = JSON.stringify({{
    hook_event_name: mapped,
    session_id: event.sessionKey ?? null,
    logged_at: new Date().toISOString(),
  }});
  try {{
    const child = spawn(
      {json.dumps(str(executable))},
      [{json.dumps(str(entry_point))}, "--provider", "openclaw", "--log", {json.dumps(str(log_path.expanduser()))}],
      {{ stdio: ["pipe", "ignore", "ignore"], detached: true }},
    );
    child.stdin.end(payload);
    child.unref();
  }} catch {{}}
}};

export default handler;
"""


OPENCLAW_HOOK_MD = """---
name: {name}
description: "Forwards agent activity to SidePulse so the LEDs show live status."
metadata:
  openclaw:
    emoji: "\U0001F4A1"
    events: ["command:new", "command:stop", "message:received", "message:sent"]
    export: "default"
---

# SidePulse Status

Forwards OpenClaw gateway events to the SidePulse agent monitor. Managed
by SidePulse -- `sidepulse agent-monitor uninstall openclaw` removes it.
"""


def install_openclaw_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    """Two coordinated writes: the handler directory under
    ~/.openclaw/hooks/ (auto-discovered by the gateway) and the enabled
    entry in openclaw.json. Unknown config keys are preserved untouched;
    the gateway needs a restart to pick the hook up."""
    config = config_path or default_openclaw_config_path()
    target_log = (log_path or detect_log_path("openclaw")).expanduser()
    hook_dir = openclaw_hook_dir() if config_path is None else config.parent / "hooks" / OPENCLAW_HOOK_NAME
    hook_info = _existing_directory_kind(hook_dir)
    config_leaf = _validated_optional_config(config, dry_run=dry_run)
    data = _strict_json_object(_decode_config(config_leaf), path=config)

    original = json.dumps(data, sort_keys=True)
    hooks = _strict_hooks_object(data, path=config)
    internal = hooks.setdefault("internal", {})
    if not isinstance(internal, dict):
        raise ValueError(f"Expected hooks.internal object in {config}")
    internal["enabled"] = True
    entries = internal.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise ValueError(f"Expected hooks.internal.entries object in {config}")
    entries[OPENCLAW_HOOK_NAME] = {"enabled": True}

    handler_source = openclaw_handler_source(target_log, python_executable)
    handler_path = hook_dir / "handler.ts"
    hook_md_path = hook_dir / "HOOK.md"
    current_handler = ""
    current_hook_md = ""
    if hook_info is not None:
        if _existing_regular_kind(handler_path) is not None:
            current_handler = read_private_text(
                handler_path,
                tighten=False,
                max_bytes=MAX_CONFIG_BYTES,
            )
            if current_handler and not current_handler.startswith("// Managed by SidePulse"):
                raise OSError(f"refusing unowned OpenClaw handler: {handler_path}")
        if _existing_regular_kind(hook_md_path) is not None:
            current_hook_md = read_private_text(
                hook_md_path,
                tighten=False,
                max_bytes=MAX_CONFIG_BYTES,
            )
            if current_hook_md and "Managed\nby SidePulse" not in current_hook_md:
                raise OSError(f"refusing unowned OpenClaw metadata: {hook_md_path}")
    wanted_hook_md = OPENCLAW_HOOK_MD.format(name=OPENCLAW_HOOK_NAME)
    files_changed = (
        current_handler != handler_source
        or current_hook_md != wanted_hook_md
    )

    changed = json.dumps(data, sort_keys=True) != original or files_changed
    backup = None
    if changed and not dry_run:
        writes: dict[Path, str] = {}
        if current_handler != handler_source:
            writes[handler_path] = handler_source
        if current_hook_md != wanted_hook_md:
            writes[hook_md_path] = wanted_hook_md
        config_text = json.dumps(data, indent=2, sort_keys=False) + "\n"
        if json.dumps(data, sort_keys=True) != original:
            writes[config] = config_text
        try:
            backup = _transactional_provider_publish(
                config_leaf=config_leaf,
                target_log=target_log,
                writes=writes,
                backup_config=json.dumps(data, sort_keys=True) != original,
            )
        except BaseException:
            if hook_info is None:
                try:
                    hook_dir.rmdir()
                except OSError:
                    pass
            raise

    return InstallResult("openclaw", config, target_log, changed, backup, dry_run)


def uninstall_openclaw_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    """Removes only SidePulse's own entry and handler directory --
    hooks.internal.enabled stays as-is (other entries may rely on it)."""
    config = config_path or default_openclaw_config_path()
    target_log = (log_path or detect_log_path("openclaw")).expanduser()
    hook_dir = openclaw_hook_dir() if config_path is None else config.parent / "hooks" / OPENCLAW_HOOK_NAME
    hook_info = _existing_directory_kind(hook_dir)
    data = read_json_config(config, tighten=not dry_run)

    original = json.dumps(data, sort_keys=True)
    internal = (data.get("hooks") or {}).get("internal")
    if isinstance(internal, dict):
        entries = internal.get("entries")
        if isinstance(entries, dict):
            entries.pop(OPENCLAW_HOOK_NAME, None)
            if not entries:
                internal.pop("entries", None)

    changed = json.dumps(data, sort_keys=True) != original or hook_info is not None
    backup = None
    if changed and not dry_run:
        if json.dumps(data, sort_keys=True) != original:
            backup = backup_file(config)
            _private_config_write(config, json.dumps(data, indent=2, sort_keys=False) + "\n")
        if hook_info is not None:
            _remove_owned_tree(hook_dir)

    return InstallResult("openclaw", config, target_log, changed, backup, dry_run)


def hook_command_arguments(
    provider: str,
    log_path: Path,
    python_executable: str | None = None,
) -> list[str]:
    """Return the frozen hook entry as an argument array, never a shell string."""
    executable = python_executable or sys.executable or "python3"
    target_log = str(log_path.expanduser())
    if getattr(sys, "frozen", False) and python_executable is None:
        return [
            executable,
            "agent-monitor",
            "hook-log",
            "--provider",
            provider,
            "--log",
            target_log,
        ]
    # Module invocation, NOT a baked absolute path into site-packages.
    # The path form assumed a copied install: the moment the package was
    # installed editable, moved, or symlinked, that exact file stopped
    # existing while `import sidepulse` kept working -- so the app looked
    # healthy while EVERY registered hook died at the next prompt, and a
    # blocking hook takes every agent down with it. `-m` resolves the
    # package however it is laid out, so config and install cannot
    # disagree. verify_hook_command() below proves it before we write it.
    return [
        executable,
        "-m",
        "sidepulse.hook_entry",
        "--provider",
        provider,
        "--log",
        target_log,
    ]


def verify_hook_command(arguments: list[str]) -> str | None:
    """Run a candidate hook command once and return an error, or None.

    Registration used to write a path nobody had ever executed. This is
    the missing gate: a hook is only worth writing into a user's agent
    config if it actually runs, because the failure mode is not a
    degraded feature -- it is every prompt in every session blocked.
    """
    import subprocess

    if not arguments:
        return "empty hook command"
    probe = json.dumps(
        {"hook_event_name": "SessionStart", "session_id": "sidepulse-install-probe"}
    )
    try:
        result = subprocess.run(
            arguments,
            input=probe,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return f"interpreter not found: {arguments[0]}"
    except subprocess.TimeoutExpired:
        return "hook command timed out"
    except OSError as exc:
        return f"hook command could not run: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        return f"hook command exited {result.returncode}: {detail[-1] if detail else 'no output'}"
    return None


def opencode_plugin_source(
    log_path: Path,
    python_executable: str | None = None,
) -> str:
    return opencode_plugin_source_for_arguments(
        hook_command_arguments("opencode", log_path, python_executable)
    )


def _read_opencode_plugin_source(
    plugin_path: Path,
) -> tuple[str, tuple[int, int]] | None:
    try:
        return read_private_text_with_identity(
            plugin_path,
            tighten=False,
            max_bytes=32 * 1024,
        )
    except FileNotFoundError:
        return None


def install_opencode_plugin(
    log_path: Path | None = None,
    plugin_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    """Install only SidePulse's global OpenCode plugin, without touching config."""
    plugin = plugin_path or default_opencode_plugin_path()
    target_log = (log_path or detect_log_path("opencode")).expanduser()
    source = opencode_plugin_source(target_log, python_executable)
    if dry_run and not plugin.parent.exists():
        current = None
        plugin_leaf = None
    else:
        _prepare_install_parent(plugin)
        plugin_leaf = _validate_install_leaf(plugin, max_bytes=32 * 1024)
        current = None if plugin_leaf.contents is None else plugin_leaf.contents.decode("utf-8")
    if current is not None and current != source and managed_opencode_plugin_log_path(current) is None:
        raise OSError(f"refusing to replace unowned OpenCode plugin: {plugin}")
    changed = current != source
    if changed and not dry_run:
        if plugin_leaf is None:
            raise OSError("OpenCode plugin validation was not retained")
        _transactional_provider_publish(
            config_leaf=plugin_leaf,
            target_log=target_log,
            writes={plugin: source},
            backup_config=False,
        )
    elif not dry_run:
        _prepare_install_parent(target_log)
        log_leaf = _validate_install_marker(target_log)
        with PrivateWriteTransaction() as transaction:
            transaction.ensure_empty_file(
                target_log,
                expected_identity=log_leaf.identity,
                expected_parent_identity=log_leaf.parent_identity,
            )
    return InstallResult("opencode", plugin, target_log, changed, None, dry_run)


def uninstall_opencode_plugin(
    log_path: Path | None = None,
    plugin_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    """Remove only an exact SidePulse-managed OpenCode plugin file."""
    plugin = plugin_path or default_opencode_plugin_path()
    target_log = (log_path or detect_log_path("opencode")).expanduser()
    read_result = _read_opencode_plugin_source(plugin)
    if read_result is None:
        return InstallResult("opencode", plugin, target_log, False, None, dry_run)
    current, expected_identity = read_result
    if managed_opencode_plugin_log_path(current) is None:
        raise OSError(f"refusing to remove unowned OpenCode plugin: {plugin}")
    if not dry_run:
        if not unlink_private_file_if_unchanged(plugin, expected_identity=expected_identity):
            return InstallResult("opencode", plugin, target_log, False, None, dry_run)
    return InstallResult("opencode", plugin, target_log, True, None, dry_run)


# Antigravity documents its hook loop as synchronous and blocking, so this is
# a ceiling on how long a wedged filesystem may stall the user's agent. Ours
# only appends one JSON line.
ANTIGRAVITY_HOOK_TIMEOUT_SECONDS = 10


def antigravity_hook_command(
    canonical_event: str,
    log_path: Path,
    python_executable: str | None = None,
) -> str:
    """The shell command registered for one Antigravity hooks.json event.

    Three things this command does that a bare hook invocation cannot, each
    forced by Antigravity's own contract:

    1. It stamps the canonical event name on. Antigravity's payload says which
       conversation and which step, but never which hook fired, so the name has
       to come from the registration site -- the same job the OpenClaw handler
       and the OpenCode plugin do for their gateways.
    2. It sends SidePulse's own output to /dev/null and prints `{}` itself.
       Antigravity feeds a command hook's stdout back into the agent, and every
       event we register documents `{}` (or an absent decision) as the no-op
       result. Without this, SidePulse could put text into a user's session.
    3. It always exits 0. A hook error is reported to the agent, so a broken
       status bar must not become the agent's problem.

    `-m sidepulse.hook_entry`, not a baked path into site-packages: the path
    form stops existing the moment the package is moved, symlinked or installed
    editable, while `import sidepulse` keeps working -- which is how every
    registered hook once died at once.
    """
    if canonical_event not in ANTIGRAVITY_CANONICAL_EVENTS.values():
        raise ValueError(f"Unsupported Antigravity hook event: {canonical_event}")
    forward = " ".join(
        shlex.quote(argument)
        for argument in hook_command_arguments("antigravity", log_path, python_executable)
    )
    envelope = (
        f"{{{json.dumps('hook_event_name')}:{json.dumps(canonical_event)},"
        f"{json.dumps(ANTIGRAVITY_ENVELOPE_KEY)}:%s}}"
    )
    # `${payload:-null}` keeps the envelope valid JSON even when Antigravity
    # hands the hook nothing at all; the payload is printf's ARGUMENT, never
    # its format, so it cannot be read as format directives.
    return (
        f"payload=\"$(cat)\"; printf {shlex.quote(envelope)} \"${{payload:-null}}\""
        f" | {forward} >/dev/null 2>&1; printf '{{}}'"
    )


def _antigravity_handler(command: str) -> dict[str, Any]:
    return {
        "type": "command",
        "command": command,
        "timeout": ANTIGRAVITY_HOOK_TIMEOUT_SECONDS,
    }


def _antigravity_entry(
    target_log: Path,
    python_executable: str | None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"enabled": True}
    for event_name in ANTIGRAVITY_EVENTS:
        handler = _antigravity_handler(
            antigravity_hook_command(
                ANTIGRAVITY_CANONICAL_EVENTS[event_name],
                target_log,
                python_executable,
            )
        )
        entry[event_name] = (
            [{"matcher": "*", "hooks": [handler]}]
            if event_name in ANTIGRAVITY_GROUPED_EVENTS
            else [handler]
        )
    return entry


def _antigravity_entry_is_ours(entry: Any) -> bool:
    """True only when every command under our named hook is SidePulse's own."""
    if not isinstance(entry, dict):
        return False
    commands: list[str] = []
    for event_name, entries in entry.items():
        if event_name == "enabled" or not isinstance(entries, list):
            continue
        for candidate in entries:
            if not isinstance(candidate, dict):
                return False
            grouped = candidate.get("hooks")
            handlers = grouped if isinstance(grouped, list) else [candidate]
            for handler in handlers:
                if not isinstance(handler, dict):
                    return False
                command = handler.get("command")
                if not isinstance(command, str):
                    return False
                commands.append(command)
    return bool(commands) and all(
        is_sidepulse_hook_command(command, "antigravity") for command in commands
    )


def install_antigravity_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    """Claim one named hook in ~/.gemini/config/hooks.json.

    hooks.json is keyed by hook NAME rather than by event, and it is a shared
    user-level file: other tools' named hooks, and any key we do not own, are
    left byte-identical.
    """
    config = config_path or default_antigravity_config_path()
    target_log = (log_path or detect_log_path("antigravity")).expanduser()
    config_leaf = _validated_optional_config(config, dry_run=dry_run)
    data = _strict_json_object(_decode_config(config_leaf), path=config)

    original = json.dumps(data, sort_keys=True)
    existing = data.get(ANTIGRAVITY_HOOK_NAME)
    if existing is not None and not _antigravity_entry_is_ours(existing):
        raise OSError(f"refusing to replace unowned Antigravity hook: {config}")
    data[ANTIGRAVITY_HOOK_NAME] = _antigravity_entry(target_log, python_executable)

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        backup = _transactional_provider_publish(
            config_leaf=config_leaf,
            target_log=target_log,
            writes={config: json.dumps(data, indent=2, sort_keys=False) + "\n"},
            backup_config=True,
        )

    return InstallResult("antigravity", config, target_log, changed, backup, dry_run)


def uninstall_antigravity_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or default_antigravity_config_path()
    target_log = (log_path or detect_log_path("antigravity")).expanduser()
    data = read_json_config(config, tighten=not dry_run)

    original = json.dumps(data, sort_keys=True)
    entry = data.get(ANTIGRAVITY_HOOK_NAME)
    if entry is not None and not _antigravity_entry_is_ours(entry):
        raise OSError(f"refusing to remove unowned Antigravity hook: {config}")
    data.pop(ANTIGRAVITY_HOOK_NAME, None)

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        backup = backup_file(config)
        _private_config_write(config, json.dumps(data, indent=2, sort_keys=False) + "\n")

    return InstallResult("antigravity", config, target_log, changed, backup, dry_run)


def uninstall_codex_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or Path.home() / ".codex" / "config.toml"
    target_log = (log_path or detect_log_path("codex")).expanduser()
    original = _read_optional_text(config, tighten=not dry_run)

    text = strip_managed_block(original)
    text = remove_codex_hook_blocks_for_log(text, target_log)
    new_text = _normalize_config_text(text) if text != original else original
    changed = new_text != original

    backup = None
    if changed and not dry_run:
        backup = backup_file(config)
        _private_config_write(config, new_text)

    return InstallResult("codex", config, target_log, changed, backup, dry_run)


def uninstall_claude_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or Path.home() / ".claude" / "settings.json"
    target_log = (log_path or detect_log_path("claude")).expanduser()

    original_text = _read_optional_text(config, tighten=not dry_run)
    if original_text:
        data = json.loads(original_text)
    else:
        data = {}

    original = json.dumps(data, sort_keys=True)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event_name in list(hooks):
            entries = hooks.get(event_name)
            if event_name not in CLAUDE_EVENTS or not isinstance(entries, list):
                continue

            cleaned = remove_claude_hooks_for_log(entries, target_log)
            if cleaned:
                hooks[event_name] = cleaned
            else:
                hooks.pop(event_name, None)

        if not hooks:
            data.pop("hooks", None)

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        backup = backup_file(config)
        _private_config_write(config, json.dumps(data, indent=2, sort_keys=False) + "\n")

    return InstallResult("claude", config, target_log, changed, backup, dry_run)


def uninstall_grok_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or default_grok_hook_config_path()
    target_log = (log_path or detect_log_path("grok")).expanduser()
    data = read_json_config(config, tighten=not dry_run)

    original = json.dumps(data, sort_keys=True)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event_name in list(hooks):
            entries = hooks.get(event_name)
            if event_name not in GROK_EVENTS or not isinstance(entries, list):
                continue

            cleaned = remove_json_command_hooks_for_log(entries, target_log, "grok")
            if cleaned:
                hooks[event_name] = cleaned
            else:
                hooks.pop(event_name, None)

        if not hooks:
            data.pop("hooks", None)

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        backup = backup_file(config)
        if data:
            _private_config_write(config, json.dumps(data, indent=2, sort_keys=False) + "\n")
        else:
            try:
                if _existing_regular_kind(config) is not None:
                    config.unlink()
            except FileNotFoundError:
                pass

    return InstallResult("grok", config, target_log, changed, backup, dry_run)


def uninstall_devin_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or default_devin_config_path()
    target_log = (log_path or detect_log_path("devin")).expanduser()
    data = read_json_config(config, tighten=not dry_run)

    original = json.dumps(data, sort_keys=True)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event_name in list(hooks):
            entries = hooks.get(event_name)
            if event_name not in DEVIN_EVENTS or not isinstance(entries, list):
                continue

            cleaned = remove_json_command_hooks_for_log(entries, target_log, "devin")
            if cleaned:
                hooks[event_name] = cleaned
            else:
                hooks.pop(event_name, None)

        if not hooks:
            data.pop("hooks", None)

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        backup = backup_file(config)
        _private_config_write(config, json.dumps(data, indent=2, sort_keys=False) + "\n")

    return InstallResult("devin", config, target_log, changed, backup, dry_run)


INSTALLERS = {
    "codex": install_codex_hooks,
    "claude": install_claude_hooks,
    "devin": install_devin_hooks,
    "grok": install_grok_hooks,
    "cursor": install_cursor_hooks,
    "hermes": install_hermes_hooks,
    "openclaw": install_openclaw_hooks,
    "opencode": install_opencode_plugin,
    "antigravity": install_antigravity_hooks,
}

UNINSTALLERS = {
    "codex": uninstall_codex_hooks,
    "claude": uninstall_claude_hooks,
    "devin": uninstall_devin_hooks,
    "grok": uninstall_grok_hooks,
    "cursor": uninstall_cursor_hooks,
    "hermes": uninstall_hermes_hooks,
    "openclaw": uninstall_openclaw_hooks,
    "opencode": uninstall_opencode_plugin,
    "antigravity": uninstall_antigravity_hooks,
}


def install_provider_hooks(provider: str, **kwargs: Any) -> InstallResult:
    return INSTALLERS[provider](**kwargs)


def uninstall_provider_hooks(provider: str, **kwargs: Any) -> InstallResult:
    return UNINSTALLERS[provider](**kwargs)


def hook_command(
    provider: str,
    log_path: Path,
    python_executable: str | None = None,
) -> str:
    executable = python_executable or sys.executable or "python3"
    if getattr(sys, "frozen", False) and python_executable is None:
        return " ".join(
            [
                shlex.quote(executable),
                "agent-monitor",
                "hook-log",
                "--provider",
                shlex.quote(provider),
                "--log",
                shlex.quote(str(log_path.expanduser())),
            ]
        )
    entry_point = Path(__file__).with_name("hook_entry.py")
    command = " ".join(
        [
            shlex.quote(executable),
            shlex.quote(str(entry_point)),
            "--provider",
            shlex.quote(provider),
            "--log",
            shlex.quote(str(log_path.expanduser())),
        ]
    )
    return command


def read_json_config(config: Path, *, tighten: bool = True) -> dict[str, Any]:
    try:
        text = read_private_text(config, tighten=tighten)
    except FileNotFoundError:
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def grok_hook_entry(event_name: str, command: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"hooks": [{"type": "command", "command": command}]}
    if event_name in {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionDenied", "Notification"}:
        entry["matcher"] = "*"
    return entry


def hook_pythonpath_assignment() -> str:
    package_root = Path(__file__).resolve().parents[1]
    if not package_root.exists():
        return ""
    return f"PYTHONPATH={shlex.quote(str(package_root))} "


def codex_hook_block(
    log_path: Path,
    python_executable: str | None = None,
) -> str:
    command = hook_command("codex", log_path, python_executable)
    lines = [
        MANAGED_START,
        "# Provider-neutral status collection. Do not edit inside this block.",
    ]
    for event_name in CODEX_EVENTS:
        lines.extend(
            [
                f"[[hooks.{event_name}]]",
                'matcher = "*"',
                f"[[hooks.{event_name}.hooks]]",
                'type = "command"',
                f"command = '''{command}'''",
                "",
            ]
        )
    lines.append(MANAGED_END)
    return "\n".join(lines) + "\n"


def should_refresh_codex_hook_trust(config: Path, explicit_config: Path | None) -> bool:
    default_config = Path.home() / ".codex" / "config.toml"
    try:
        return config.expanduser().resolve() == default_config.expanduser().resolve()
    except OSError:
        return explicit_config is None


def resolve_codex_hook_trust(
    config_path: Path,
    cwd: Path | None = None,
    timeout_seconds: float = 8.0,
) -> CodexHookTrust:
    """Ask Codex for the hook's current hash, and say what came back.

    ``resolve_codex_hook_hashes`` answers the same question with a dict,
    and an empty dict is the same value for "no Codex binary exists",
    "the app-server never answered" and "it answered without our hook".
    The install path treated all three as "nothing to do", which is how
    an install could report success and leave a hook Codex will not run.
    """
    if codex_cli_path() is None:
        return CodexHookTrust(CodexHookTrustStatus.CLI_NOT_FOUND)
    hashes = resolve_codex_hook_hashes(config_path, cwd, timeout_seconds)
    if not hashes:
        return CodexHookTrust(CodexHookTrustStatus.NOT_CONFIRMED)
    return CodexHookTrust(CodexHookTrustStatus.TRUSTED, dict(hashes))


def resolve_codex_hook_hashes(
    config_path: Path,
    cwd: Path | None = None,
    timeout_seconds: float = 8.0,
) -> dict[str, str]:
    codex = codex_cli_path()
    if codex is None:
        return {}

    try:
        process = subprocess.Popen(
            [str(codex), "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(cwd or Path.cwd()),
        )
    except OSError:
        return {}

    messages: queue.Queue[tuple[str, str]] = queue.Queue()

    def read_stream(name: str, stream: Any) -> None:
        for line in stream:
            messages.put((name, line.rstrip("\n")))

    for name, stream in (("out", process.stdout), ("err", process.stderr)):
        if stream is not None:
            threading.Thread(target=read_stream, args=(name, stream), daemon=True).start()

    def send(payload: dict[str, Any]) -> bool:
        if process.stdin is None:
            return False
        try:
            process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except OSError:
            return False
        return True

    def wait_for_id(message_id: int) -> dict[str, Any] | None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                name, line = messages.get(timeout=0.1)
            except queue.Empty:
                continue
            if name != "out":
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") == message_id:
                return payload
        return None

    try:
        if not send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "sidepulse", "version": "0"},
                    "capabilities": None,
                },
            }
        ):
            return {}
        wait_for_id(1)
        if not send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "hooks/list",
                "params": {"cwds": [str(cwd or Path.cwd())]},
            }
        ):
            return {}
        response = wait_for_id(2)
    finally:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()

    if not response:
        return {}

    try:
        hooks = response["result"]["data"][0]["hooks"]
    except (KeyError, IndexError, TypeError):
        return {}

    source_path = str(config_path.expanduser())
    trusted_hashes: dict[str, str] = {}
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = hook.get("command")
        current_hash = hook.get("currentHash")
        key = hook.get("key")
        if hook.get("sourcePath") != source_path:
            continue
        if not isinstance(command, str) or not (
            "hook_entry.py" in command or "sidepulse.hook_entry" in command
        ):
            continue
        if not isinstance(current_hash, str) or not isinstance(key, str):
            continue
        trusted_hashes[key] = current_hash
    return trusted_hashes


def codex_cli_path() -> Path | None:
    env_path = os.environ.get("CODEX_CLI_PATH")
    candidates = [
        Path(env_path).expanduser() if env_path else None,
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path("/Applications/Codex.app/Contents/Resources/codex"),
        Path(shutil.which("codex")).expanduser() if shutil.which("codex") else None,
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def update_codex_trusted_hashes(text: str, trusted_hashes: dict[str, str]) -> str:
    if not trusted_hashes:
        return text

    result = _ensure_hooks_state_table(text)
    for key, trusted_hash in trusted_hashes.items():
        result = _set_codex_trusted_hash(result, key, trusted_hash)
    return result


def _ensure_hooks_state_table(text: str) -> str:
    if re.search(r"^\s*\[hooks\.state\]\s*$", text, re.MULTILINE):
        return text
    return _ensure_trailing_newline(text) + "\n[hooks.state]\n"


def _set_codex_trusted_hash(text: str, key: str, trusted_hash: str) -> str:
    header = f'[hooks.state."{toml_basic_string_escape(key)}"]'
    lines = text.splitlines(keepends=True)
    header_index = None
    for index, line in enumerate(lines):
        if line.strip() == header:
            header_index = index
            break

    if header_index is None:
        block = f'\n{header}\ntrusted_hash = "{toml_basic_string_escape(trusted_hash)}"\n'
        return _ensure_trailing_newline(text) + block

    end = len(lines)
    for index in range(header_index + 1, len(lines)):
        if re.match(r"\s*\[.*\]\s*$", lines[index]):
            end = index
            break

    trusted_line = f'trusted_hash = "{toml_basic_string_escape(trusted_hash)}"\n'
    for index in range(header_index + 1, end):
        if re.match(r"\s*trusted_hash\s*=", lines[index]):
            lines[index] = trusted_line
            return "".join(lines)

    lines.insert(header_index + 1, trusted_line)
    return "".join(lines)


def toml_basic_string_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def strip_managed_block(text: str) -> str:
    # Codex may append its own tables between these comments when it rewrites
    # config.toml.  Remove only the comments; hook tables are removed below.
    return "\n".join(
        line for line in text.splitlines() if line.strip() not in {MANAGED_START, MANAGED_END}
    ) + ("\n" if text.endswith("\n") else "")


def remove_codex_hook_blocks_for_log(text: str, log_path: Path) -> str:
    target = str(log_path)
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    index = 0

    while index < len(lines):
        event_match = re.match(r"\s*\[\[hooks\.([A-Za-z0-9_]+)\]\]\s*$", lines[index])
        if event_match and event_match.group(1) in CODEX_EVENTS:
            event_name = event_match.group(1)
            end = index + 1
            nested = re.compile(rf"\s*\[\[hooks\.{re.escape(event_name)}\.hooks\]\]\s*$")
            table = re.compile(r"\s*\[.*\]\s*$")
            while end < len(lines):
                if table.match(lines[end]) and not nested.match(lines[end]):
                    break
                end += 1
            block = "".join(lines[index:end])
            if (
                target in block
                or "sidepulse hook-log" in block
                or "hook_entry.py" in block
                or "sidepulse.hook_entry" in block
            ):
                index = end
                continue

        if "Event logging hooks:" in lines[index] and target in text:
            index += 1
            continue

        out.append(lines[index])
        index += 1

    return "".join(out)


def is_pristine_codex_hook_install(text: str, block: str, log_path: Path) -> bool:
    if ensure_codex_hooks_feature(text) != text or text.count(block) != 1:
        return False
    unmanaged_text = text.replace(block, "", 1)
    return remove_codex_hook_blocks_for_log(unmanaged_text, log_path) == unmanaged_text


def remove_claude_hooks_for_log(entries: list[Any], log_path: Path) -> list[dict[str, Any]]:
    return remove_json_command_hooks_for_log(entries, log_path, "claude")


def remove_json_command_hooks_for_log(
    entries: list[Any],
    log_path: Path,
    provider: str,
) -> list[dict[str, Any]]:
    cleaned_entries: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        cleaned_hooks = []
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            command = hook.get("command")
            if is_sidepulse_json_hook_command(command, log_path, provider):
                continue
            cleaned_hooks.append(hook)
        if cleaned_hooks:
            kept = dict(entry)
            kept["hooks"] = cleaned_hooks
            cleaned_entries.append(kept)
    return cleaned_entries


def is_sidepulse_json_hook_command(
    command: Any,
    log_path: Path,
    provider: str,
) -> bool:
    if not isinstance(command, str):
        return False
    try:
        arguments = shlex.split(command)
    except ValueError:
        return False

    if _command_option(arguments, "--provider") != provider:
        return False
    if _command_option(arguments, "--log") != str(log_path.expanduser()):
        return False

    source_entrypoint = any(
        Path(argument).name == "hook_entry.py" for argument in arguments
    ) or ("-m" in arguments and "sidepulse.hook_entry" in arguments)
    packaged_entrypoint = (
        any(Path(argument).name == "agent-monitor" for argument in arguments)
        and "hook-log" in arguments
    )
    return source_entrypoint or packaged_entrypoint


def _command_option(arguments: list[str], option: str) -> str | None:
    for index, argument in enumerate(arguments):
        if argument == option and index + 1 < len(arguments):
            return arguments[index + 1]
        prefix = f"{option}="
        if argument.startswith(prefix):
            return argument.removeprefix(prefix)
    return None


def ensure_codex_hooks_feature(text: str) -> str:
    lines = text.splitlines(keepends=True)
    features_index = None
    for index, line in enumerate(lines):
        if re.match(r"\s*\[features\]\s*$", line):
            features_index = index
            break

    if features_index is None:
        return _ensure_trailing_newline(text) + "\n[features]\nhooks = true\n"

    end = len(lines)
    for index in range(features_index + 1, len(lines)):
        if re.match(r"\s*\[.*\]\s*$", lines[index]):
            end = index
            break

    for index in range(features_index + 1, end):
        if re.match(r"\s*hooks\s*=", lines[index]):
            lines[index] = "hooks = true\n"
            return "".join(lines)

    lines.insert(end, "hooks = true\n")
    return "".join(lines)


def backup_file(path: Path) -> Path | None:
    try:
        contents = read_private_bytes(path)
    except FileNotFoundError:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = path.with_name(
        f"{path.name}.bak.{stamp}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}"
    )
    atomic_private_write(backup, contents)
    enforce_retention(
        path.parent,
        RetentionPolicy(
            max_files=BACKUP_MAX_FILES,
            patterns=(f"{path.name}.bak.*",),
            recursive=False,
        ),
    )
    return backup


def _ensure_hook_log(path: Path) -> None:
    ensure_private_file(path)


def _private_config_write(path: Path, text: str) -> None:
    atomic_private_write(path, text)


def _existing_directory_kind(path: Path) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise OSError(f"refusing non-directory hook path: {path}")
    return info


def _existing_regular_kind(path: Path) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise OSError(f"refusing non-regular hook path: {path}")
    return info


def _remove_owned_tree(path: Path) -> None:
    info = _existing_directory_kind(path)
    if info is None:
        return
    directories: list[Path] = []
    files: list[Path] = []
    for base, names, filenames in os.walk(path, followlinks=False):
        directory = Path(base)
        directories.append(directory)
        for name in names:
            child = directory / name
            child_info = child.lstat()
            if stat.S_ISLNK(child_info.st_mode) or not stat.S_ISDIR(
                child_info.st_mode
            ) or child_info.st_uid != os.getuid() or stat.S_IMODE(child_info.st_mode) & 0o022:
                raise OSError(f"refusing unexpected hook directory entry: {child}")
        for name in filenames:
            child = directory / name
            child_info = child.lstat()
            if stat.S_ISLNK(child_info.st_mode) or not stat.S_ISREG(
                child_info.st_mode
            ) or child_info.st_nlink != 1 or child_info.st_uid != os.getuid():
                raise OSError(f"refusing unexpected hook file entry: {child}")
            files.append(child)
    for file_path in files:
        file_path.unlink()
    for directory in reversed(directories):
        directory.rmdir()


def _ensure_trailing_newline(text: str) -> str:
    return text if not text or text.endswith("\n") else text + "\n"


def _normalize_config_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return stripped + "\n"
