"""Race-resistant writes into an existing user-selected ambient directory.

Identity checks prevent redirected writes and false success claims. They cannot
provide confidentiality after publication against a concurrent same-effective-
UID process that can mutate the chosen directory, because that process can
hard-link the published inode before a final check detects it.
"""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path
from typing import Final

from .private_io import (
    PRIVATE_FILE_MODE,
    _leaf_stat,
    _private_parent,
    _require_opened_leaf,
    _require_private_leaf,
    _require_regular_file,
)

_MAX_EXPORT_LEAF_BYTES: Final = 200
PUBLIC_EXPORT_ERROR_MESSAGE: Final = "Export could not be saved."


class PrivateExportError(OSError):
    """A local export destination or transaction failed closed."""

    @property
    def public_message(self) -> str:
        """Return path-free copy for AppKit announcements and alerts."""
        return PUBLIC_EXPORT_ERROR_MESSAGE


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("write made no progress")
        remaining = remaining[written:]


def _scratch_flags() -> int:
    return os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _read_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _require_parent_identity(target: Path, parent_descriptor: int) -> None:
    opened = os.fstat(parent_descriptor)
    try:
        current = os.stat(target.parent, follow_symlinks=False)
    except OSError as error:
        raise PrivateExportError("export destination changed") from error
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode) or not _same_identity(current, opened):
        raise PrivateExportError("export destination changed")


def _require_expected_target(
    target: Path,
    parent_descriptor: int,
    name: str,
    expected: os.stat_result | None,
) -> None:
    try:
        current = _require_private_leaf(target, parent_descriptor, name)
    except OSError as error:
        raise PrivateExportError("export destination changed") from error
    if expected is None:
        if current is not None:
            raise PrivateExportError("export destination changed")
        return
    if current is None or not _same_identity(current, expected):
        raise PrivateExportError("export destination changed")


def _require_scratch_identity(
    target: Path,
    parent_descriptor: int,
    scratch_name: str,
    scratch_identity: tuple[int, int],
) -> os.stat_result:
    current = _leaf_stat(parent_descriptor, scratch_name)
    if current is None or (current.st_dev, current.st_ino) != scratch_identity:
        raise PrivateExportError("export scratch changed")
    try:
        _require_regular_file(target.with_name(scratch_name), current)
    except OSError as error:
        raise PrivateExportError("export scratch changed") from error
    return current


def _require_published_scratch(
    target: Path,
    parent_descriptor: int,
    name: str,
    scratch_identity: tuple[int, int],
) -> None:
    current = _leaf_stat(parent_descriptor, name)
    if current is None or (current.st_dev, current.st_ino) != scratch_identity:
        raise PrivateExportError("export scratch changed")
    try:
        _require_regular_file(target, current)
    except OSError as error:
        raise PrivateExportError("export scratch changed") from error
    if stat.S_IMODE(current.st_mode) != PRIVATE_FILE_MODE:
        raise PrivateExportError("export scratch changed")


def _cleanup_owned_scratch(
    target: Path,
    parent_descriptor: int,
    scratch_name: str,
    scratch_identity: tuple[int, int] | None,
) -> None:
    if scratch_identity is None:
        return
    current = _leaf_stat(parent_descriptor, scratch_name)
    if current is None:
        return
    if (current.st_dev, current.st_ino) != scratch_identity:
        raise PrivateExportError("export scratch changed")
    try:
        _require_regular_file(target.with_name(scratch_name), current)
        os.unlink(scratch_name, dir_fd=parent_descriptor)
    except OSError as error:
        raise PrivateExportError("export scratch cleanup failed") from error


def _write_in_parent(
    target: Path,
    parent_descriptor: int,
    name: str,
    payload: bytes,
) -> Path:
    parent_info = os.fstat(parent_descriptor)
    if stat.S_IMODE(parent_info.st_mode) & 0o022:
        raise PrivateExportError("export destination is broadly writable")
    try:
        expected = _require_private_leaf(target, parent_descriptor, name)
    except OSError as error:
        raise PrivateExportError("export destination unavailable") from error

    target_descriptor: int | None = None
    scratch_descriptor: int | None = None
    scratch_name = f".sidepulse-export-{uuid.uuid4().hex}.tmp"
    scratch_identity: tuple[int, int] | None = None
    try:
        if expected is not None:
            try:
                target_descriptor = os.open(
                    name,
                    _read_flags(),
                    dir_fd=parent_descriptor,
                )
                opened_target = os.fstat(target_descriptor)
                _require_opened_leaf(target, expected, opened_target)
            except OSError as error:
                raise PrivateExportError("export destination unavailable") from error

        try:
            scratch_descriptor = os.open(
                scratch_name,
                _scratch_flags(),
                PRIVATE_FILE_MODE,
                dir_fd=parent_descriptor,
            )
        except FileExistsError as error:
            raise PrivateExportError("export scratch unavailable") from error
        except OSError as error:
            raise PrivateExportError("export write failed") from error

        try:
            opened_scratch = os.fstat(scratch_descriptor)
            _require_opened_leaf(
                target.with_name(scratch_name),
                None,
                opened_scratch,
            )
            if opened_scratch.st_dev != os.fstat(parent_descriptor).st_dev:
                raise OSError("scratch is on another device")
            scratch_identity = (opened_scratch.st_dev, opened_scratch.st_ino)
            os.fchmod(scratch_descriptor, PRIVATE_FILE_MODE)
            _write_all(scratch_descriptor, payload)
            os.fsync(scratch_descriptor)
        except OSError as error:
            raise PrivateExportError("export write failed") from error
        finally:
            os.close(scratch_descriptor)
            scratch_descriptor = None

        _require_parent_identity(target, parent_descriptor)
        _require_expected_target(
            target,
            parent_descriptor,
            name,
            expected,
        )
        _require_scratch_identity(
            target,
            parent_descriptor,
            scratch_name,
            scratch_identity,
        )
        try:
            os.replace(
                scratch_name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        except OSError as error:
            raise PrivateExportError("export publish failed") from error

        _require_published_scratch(
            target,
            parent_descriptor,
            name,
            scratch_identity,
        )
        _require_parent_identity(target, parent_descriptor)
        if target_descriptor is not None and os.fstat(target_descriptor).st_nlink != 0:
            raise PrivateExportError("export destination changed")
        try:
            os.fsync(parent_descriptor)
        except OSError as error:
            raise PrivateExportError("export publish failed") from error
        _require_parent_identity(target, parent_descriptor)
        _require_published_scratch(
            target,
            parent_descriptor,
            name,
            scratch_identity,
        )
        return target
    finally:
        if scratch_descriptor is not None:
            os.close(scratch_descriptor)
        if target_descriptor is not None:
            os.close(target_descriptor)
        _cleanup_owned_scratch(
            target,
            parent_descriptor,
            scratch_name,
            scratch_identity,
        )


def write_private_export(
    path: Path,
    payload: bytes,
    *,
    max_bytes: int,
) -> Path:
    """Atomically publish bytes without changing the selected parent's mode."""
    if type(payload) is not bytes:
        raise PrivateExportError("export payload must be bytes")
    if type(max_bytes) is not int or max_bytes < 0:
        raise PrivateExportError("invalid export max_bytes")
    if len(payload) > max_bytes:
        raise PrivateExportError("export exceeds maximum size")
    if not isinstance(path, Path):
        raise PrivateExportError("export destination must be a Path")
    target = path
    if not target.is_absolute():
        raise PrivateExportError("export destination must be absolute")
    try:
        leaf_size = len(target.name.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise PrivateExportError("invalid export destination") from error
    if not 1 <= leaf_size <= _MAX_EXPORT_LEAF_BYTES:
        raise PrivateExportError("invalid export destination")

    try:
        with _private_parent(target, tighten=False) as (
            selected,
            parent_descriptor,
            name,
        ):
            return _write_in_parent(
                selected,
                parent_descriptor,
                name,
                payload,
            )
    except PrivateExportError:
        raise
    except OSError as error:
        raise PrivateExportError("export destination unavailable") from error


__all__ = [
    "PUBLIC_EXPORT_ERROR_MESSAGE",
    "PrivateExportError",
    "write_private_export",
]
