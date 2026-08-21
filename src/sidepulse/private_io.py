"""Owner-only filesystem writes, event redaction, and bounded retention."""

from __future__ import annotations

import os
import stat
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
REDACTION_MARKER = "[REDACTED]"


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _chmod(path: Path, mode: int) -> None:
    os.chmod(path, mode, follow_symlinks=False)


def _require_directory(path: Path, info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"refusing non-directory path: {path}")


def _require_regular_file(path: Path, info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OSError(f"refusing non-regular file: {path}")
    if info.st_nlink != 1:
        raise OSError(f"refusing multiply-linked file: {path}")


def _validate_ancestors(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    parts = absolute.parts[1:] if absolute.anchor else absolute.parts
    for part in parts[:-1]:
        current /= part
        info = _lstat(current)
        if info is None:
            continue
        if stat.S_ISLNK(info.st_mode) and info.st_uid == 0:
            resolved = current.stat()
            if stat.S_ISDIR(resolved.st_mode):
                continue
        _require_directory(current, info)


def _create_directory_chain(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while True:
        info = _lstat(cursor)
        if info is not None:
            _require_directory(cursor, info)
            break
        parent = cursor.parent
        if parent == cursor:
            raise OSError(f"no existing directory ancestor for {path}")
        missing.append(cursor)
        cursor = parent
    for directory in reversed(missing):
        try:
            os.mkdir(directory, PRIVATE_DIRECTORY_MODE)
        except FileExistsError:
            info = directory.lstat()
            _require_directory(directory, info)
        _chmod(directory, PRIVATE_DIRECTORY_MODE)


def ensure_private_directory(path: Path) -> Path:
    """Create or tighten one sensitive directory without following symlinks."""
    target = Path(path).expanduser()
    _validate_ancestors(target)
    info = _lstat(target)
    if info is None:
        _create_directory_chain(target)
    else:
        _require_directory(target, info)
        _chmod(target, PRIVATE_DIRECTORY_MODE)
    return target


def _open_flags(*, append: bool = False, exclusive: bool = False) -> int:
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if append else os.O_TRUNC
    if exclusive:
        flags |= os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    return flags


def _open_directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


@contextmanager
def _private_parent(
    path: Path,
    *,
    tighten: bool = True,
) -> Iterator[tuple[Path, int, str]]:
    """Hold a stable no-follow descriptor for one private file's parent."""
    target = Path(path).expanduser()
    _validate_ancestors(target)
    if tighten:
        ensure_private_directory(target.parent)
        parent_info = _lstat(target.parent)
        if parent_info is None:
            raise FileNotFoundError(target)
        _require_directory(target.parent, parent_info)
    else:
        parent_info = _lstat(target.parent)
        if parent_info is None:
            raise FileNotFoundError(target)
        _require_directory(target.parent, parent_info)
    descriptor = os.open(target.parent, _open_directory_flags())
    try:
        opened = os.fstat(descriptor)
        _require_directory(target.parent, opened)
        if (
            opened.st_dev != parent_info.st_dev
            or opened.st_ino != parent_info.st_ino
        ):
            raise OSError(f"private parent changed while opening: {target.parent}")
        if tighten:
            os.fchmod(descriptor, PRIVATE_DIRECTORY_MODE)
        yield target, descriptor, target.name
    finally:
        os.close(descriptor)


def _leaf_stat(parent_descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _require_private_leaf(
    target: Path,
    parent_descriptor: int,
    name: str,
) -> os.stat_result | None:
    info = _leaf_stat(parent_descriptor, name)
    if info is not None:
        _require_regular_file(target, info)
    return info


def _require_opened_leaf(
    target: Path,
    expected: os.stat_result | None,
    opened: os.stat_result,
) -> None:
    _require_regular_file(target, opened)
    if expected is not None and (
        opened.st_dev != expected.st_dev or opened.st_ino != expected.st_ino
    ):
        raise OSError(f"private file changed while opening: {target}")


def _require_leaf_identity(
    target: Path,
    parent_descriptor: int,
    name: str,
    opened: os.stat_result,
) -> None:
    current = _require_private_leaf(target, parent_descriptor, name)
    if current is None or (
        current.st_dev != opened.st_dev or current.st_ino != opened.st_ino
    ):
        raise OSError(f"private file changed during operation: {target}")


def _replace_private_leaf(
    scratch_name: str,
    target_name: str,
    parent_descriptor: int,
) -> None:
    """Atomic dirfd-relative replace, kept narrow for fault injection."""
    os.replace(
        scratch_name,
        target_name,
        src_dir_fd=parent_descriptor,
        dst_dir_fd=parent_descriptor,
    )


def _fsync_private_parent(parent_descriptor: int) -> None:
    """Durably publish a directory entry, kept narrow for fault injection."""
    os.fsync(parent_descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("private write made no progress")
        view = view[written:]


def ensure_private_file(path: Path) -> Path:
    """Create or tighten one sensitive regular file without changing bytes."""
    with _private_parent(path) as (target, parent_descriptor, name):
        info = _require_private_leaf(target, parent_descriptor, name)
        if info is None:
            descriptor = os.open(
                name,
                _open_flags(exclusive=True),
                PRIVATE_FILE_MODE,
                dir_fd=parent_descriptor,
            )
            try:
                opened = os.fstat(descriptor)
                _require_opened_leaf(target, None, opened)
                os.fchmod(descriptor, PRIVATE_FILE_MODE)
                os.fsync(descriptor)
                _require_leaf_identity(
                    target,
                    parent_descriptor,
                    name,
                    opened,
                )
            finally:
                os.close(descriptor)
        else:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_descriptor,
            )
            try:
                opened = os.fstat(descriptor)
                _require_opened_leaf(target, info, opened)
                os.fchmod(descriptor, PRIVATE_FILE_MODE)
                os.fsync(descriptor)
                _require_leaf_identity(
                    target,
                    parent_descriptor,
                    name,
                    opened,
                )
            finally:
                os.close(descriptor)
        return target


def atomic_private_write(path: Path, data: str | bytes) -> Path:
    """Atomically replace a sensitive file through a unique private scratch."""
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    with _private_parent(path) as (target, parent_descriptor, name):
        _require_private_leaf(target, parent_descriptor, name)
        scratch_name = (
            f"{name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        descriptor = None
        scratch_identity: tuple[int, int] | None = None
        try:
            descriptor = os.open(
                scratch_name,
                _open_flags(exclusive=True),
                PRIVATE_FILE_MODE,
                dir_fd=parent_descriptor,
            )
            opened = os.fstat(descriptor)
            _require_opened_leaf(target.with_name(scratch_name), None, opened)
            scratch_identity = (opened.st_dev, opened.st_ino)
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None

            _require_private_leaf(target, parent_descriptor, name)
            _replace_private_leaf(scratch_name, name, parent_descriptor)
            _fsync_private_parent(parent_descriptor)
            return target
        finally:
            if descriptor is not None:
                os.close(descriptor)
            scratch_info = _leaf_stat(parent_descriptor, scratch_name)
            if scratch_info is not None:
                _require_regular_file(target.with_name(scratch_name), scratch_info)
                if scratch_identity is None or (
                    scratch_info.st_dev,
                    scratch_info.st_ino,
                ) != scratch_identity:
                    raise OSError("private scratch changed during operation")
                os.unlink(scratch_name, dir_fd=parent_descriptor)


@dataclass(slots=True)
class _TransactionLeaf:
    target: Path
    parent_descriptor: int
    name: str
    original: bytes | None
    original_mode: int | None
    original_identity: tuple[int, int] | None
    published_identity: tuple[int, int] | None = None


@dataclass(slots=True)
class PrivateWriteTransaction:
    """Recoverable multi-file publisher anchored to held parent descriptors.

    The first write snapshots one bounded original leaf. Later writes to the
    same leaf keep that original rollback point. Rollback restores or removes
    only the inode published by this transaction, so a concurrent replacement
    is preserved and reported instead of overwritten.
    """

    _stack: ExitStack = field(default_factory=ExitStack, init=False)
    _parents: dict[Path, tuple[int, tuple[int, int]]] = field(default_factory=dict, init=False)
    _leaves: dict[Path, _TransactionLeaf] = field(default_factory=dict, init=False)
    _order: list[Path] = field(default_factory=list, init=False)
    _closed: bool = field(default=False, init=False)

    def __enter__(self) -> PrivateWriteTransaction:
        if self._closed:
            raise RuntimeError("private write transaction is closed")
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            if exc is not None:
                try:
                    self.rollback()
                except OSError as rollback_error:
                    raise OSError("installer rollback failed") from rollback_error
        finally:
            self.close()
        return False

    def _parent(
        self,
        target: Path,
        expected_identity: tuple[int, int] | None,
    ) -> int:
        parent = target.parent
        cached = self._parents.get(parent)
        if cached is None:
            context = _private_parent(target, tighten=False)
            _, descriptor, _ = self._stack.enter_context(context)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            cached = (descriptor, identity)
            self._parents[parent] = cached
        descriptor, identity = cached
        if expected_identity is not None and identity != expected_identity:
            raise OSError(f"private parent changed before transaction: {parent}")
        return descriptor

    def _leaf(
        self,
        path: Path,
        *,
        max_original_bytes: int,
        expected_identity: tuple[int, int] | None,
        expected_parent_identity: tuple[int, int] | None,
    ) -> _TransactionLeaf:
        target = Path(path).expanduser()
        existing = self._leaves.get(target)
        if existing is not None:
            return existing
        if max_original_bytes < 0:
            raise ValueError("max_original_bytes must be nonnegative")
        parent_descriptor = self._parent(target, expected_parent_identity)
        info = _require_private_leaf(target, parent_descriptor, target.name)
        current_identity = None if info is None else (info.st_dev, info.st_ino)
        if current_identity != expected_identity:
            raise OSError(f"private file changed before transaction: {target}")
        original = None
        original_mode = None
        if info is not None:
            if info.st_size > max_original_bytes:
                raise OSError("private file exceeds maximum size")
            descriptor = os.open(
                target.name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_descriptor,
            )
            try:
                opened = os.fstat(descriptor)
                _require_opened_leaf(target, info, opened)
                original = _read_bounded_descriptor(descriptor, max_original_bytes)
                _require_leaf_identity(target, parent_descriptor, target.name, opened)
                original_mode = stat.S_IMODE(opened.st_mode)
            finally:
                os.close(descriptor)
        leaf = _TransactionLeaf(
            target=target,
            parent_descriptor=parent_descriptor,
            name=target.name,
            original=original,
            original_mode=original_mode,
            original_identity=current_identity,
        )
        self._leaves[target] = leaf
        self._order.append(target)
        return leaf

    def write(
        self,
        path: Path,
        data: str | bytes,
        *,
        max_original_bytes: int,
        expected_identity: tuple[int, int] | None = None,
        expected_parent_identity: tuple[int, int] | None = None,
    ) -> tuple[int, int]:
        """Publish one leaf and return the exact published inode identity."""
        if self._closed:
            raise RuntimeError("private write transaction is closed")
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        leaf = self._leaf(
            path,
            max_original_bytes=max_original_bytes,
            expected_identity=expected_identity,
            expected_parent_identity=expected_parent_identity,
        )
        current = _require_private_leaf(leaf.target, leaf.parent_descriptor, leaf.name)
        expected_current = leaf.published_identity or leaf.original_identity
        current_identity = None if current is None else (current.st_dev, current.st_ino)
        if current_identity != expected_current:
            raise OSError(f"private file changed during transaction: {leaf.target}")
        published = self._publish(leaf, payload, PRIVATE_FILE_MODE)
        leaf.published_identity = published
        return published

    def ensure_empty_file(
        self,
        path: Path,
        *,
        expected_identity: tuple[int, int] | None = None,
        expected_parent_identity: tuple[int, int] | None = None,
    ) -> None:
        target = Path(path).expanduser()
        parent_descriptor = self._parent(target, expected_parent_identity)
        info = _require_private_leaf(target, parent_descriptor, target.name)
        current_identity = None if info is None else (info.st_dev, info.st_ino)
        if current_identity != expected_identity:
            raise OSError(f"private file changed before transaction: {target}")
        if info is None:
            self.write(
                target,
                b"",
                max_original_bytes=0,
                expected_identity=None,
                expected_parent_identity=expected_parent_identity,
            )

    def verify(self, path: Path, expected: str | bytes, *, max_bytes: int) -> None:
        target = Path(path).expanduser()
        leaf = self._leaves.get(target)
        if leaf is None or leaf.published_identity is None:
            raise OSError(f"private transaction does not own published file: {target}")
        info = _require_private_leaf(target, leaf.parent_descriptor, leaf.name)
        if info is None or (info.st_dev, info.st_ino) != leaf.published_identity:
            raise OSError(f"private file changed before verification: {target}")
        descriptor = os.open(
            leaf.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=leaf.parent_descriptor,
        )
        try:
            opened = os.fstat(descriptor)
            _require_opened_leaf(target, info, opened)
            actual = _read_bounded_descriptor(descriptor, max_bytes)
            _require_leaf_identity(target, leaf.parent_descriptor, leaf.name, opened)
        finally:
            os.close(descriptor)
        payload = expected.encode("utf-8") if isinstance(expected, str) else bytes(expected)
        if actual != payload:
            raise OSError("private transaction post-verification failed")

    def fsync_parent(self, path: Path) -> None:
        target = Path(path).expanduser()
        leaf = self._leaves.get(target)
        if leaf is None:
            raise OSError(f"private transaction does not own file: {target}")
        _fsync_private_parent(leaf.parent_descriptor)

    def rollback(self) -> None:
        errors: list[OSError] = []
        for target in reversed(self._order):
            leaf = self._leaves[target]
            if leaf.published_identity is None:
                continue
            try:
                current = _require_private_leaf(target, leaf.parent_descriptor, leaf.name)
                current_identity = None if current is None else (current.st_dev, current.st_ino)
                if current_identity != leaf.published_identity:
                    raise OSError(f"private file changed before rollback: {target}")
                if leaf.original is None:
                    os.unlink(leaf.name, dir_fd=leaf.parent_descriptor)
                    _fsync_private_parent(leaf.parent_descriptor)
                else:
                    self._publish(
                        leaf,
                        leaf.original,
                        leaf.original_mode or PRIVATE_FILE_MODE,
                    )
                leaf.published_identity = None
            except OSError as exc:
                errors.append(exc)
        if errors:
            raise errors[0]

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._stack.close()

    def _publish(
        self,
        leaf: _TransactionLeaf,
        payload: bytes,
        mode: int,
    ) -> tuple[int, int]:
        scratch_name = (
            f"{leaf.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        descriptor = None
        scratch_identity: tuple[int, int] | None = None
        try:
            descriptor = os.open(
                scratch_name,
                _open_flags(exclusive=True),
                PRIVATE_FILE_MODE,
                dir_fd=leaf.parent_descriptor,
            )
            opened = os.fstat(descriptor)
            _require_opened_leaf(leaf.target.with_name(scratch_name), None, opened)
            scratch_identity = (opened.st_dev, opened.st_ino)
            os.fchmod(descriptor, mode)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            _replace_private_leaf(
                scratch_name,
                leaf.name,
                leaf.parent_descriptor,
            )
            leaf.published_identity = scratch_identity
            published = _require_private_leaf(
                leaf.target,
                leaf.parent_descriptor,
                leaf.name,
            )
            if published is None or (published.st_dev, published.st_ino) != scratch_identity:
                raise OSError(f"private transaction publish changed: {leaf.target}")
            leaf.published_identity = scratch_identity
            _fsync_private_parent(leaf.parent_descriptor)
            return scratch_identity
        finally:
            if descriptor is not None:
                os.close(descriptor)
            scratch = _leaf_stat(leaf.parent_descriptor, scratch_name)
            if scratch is not None:
                _require_regular_file(leaf.target.with_name(scratch_name), scratch)
                if scratch_identity is None or (scratch.st_dev, scratch.st_ino) != scratch_identity:
                    raise OSError("private transaction scratch changed")
                os.unlink(scratch_name, dir_fd=leaf.parent_descriptor)


def _read_bounded_descriptor(descriptor: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > max_bytes:
        raise OSError("private file exceeds maximum size")
    return payload


def unlink_private_file_if_unchanged(
    path: Path,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> bool:
    """Unlink a single private regular file through its held parent descriptor."""
    with _private_parent(path, tighten=False) as (target, parent_descriptor, name):
        expected = _require_private_leaf(target, parent_descriptor, name)
        if expected is None:
            return False
        if expected_identity is not None and (expected.st_dev, expected.st_ino) != expected_identity:
            return False
        current = _require_private_leaf(target, parent_descriptor, name)
        if current is None or (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
            raise OSError(f"private file changed before unlink: {target}")
        os.unlink(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        return True


def append_private_text(path: Path, text: str) -> Path:
    """Append text to a private regular file without following symlinks."""
    with _private_parent(path) as (target, parent_descriptor, name):
        expected = _require_private_leaf(target, parent_descriptor, name)
        descriptor = os.open(
            name,
            _open_flags(append=True, exclusive=expected is None),
            PRIVATE_FILE_MODE,
            dir_fd=parent_descriptor,
        )
        try:
            opened = os.fstat(descriptor)
            _require_opened_leaf(target, expected, opened)
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
            _write_all(descriptor, str(text).encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return target


def read_private_bytes_with_identity(
    path: Path,
    *,
    tighten: bool = True,
    max_bytes: int | None = None,
    tail: bool = False,
) -> tuple[bytes, tuple[int, int]]:
    """Read one private file and return bytes with its verified inode identity.

    With ``tail=True`` an over-``max_bytes`` file yields its NEWEST
    ``max_bytes`` instead of raising -- the mode for append-only logs
    whose freshest lines are the ones that matter. The first returned
    line may be a partial record; line-oriented callers skip it when it
    fails to parse. (The raising default stayed the claude outage of
    2026-08-21: the events log crossed the cap, every reconcile read
    raised, and the provider went silent while its log kept growing.)
    """
    if (
        max_bytes is not None
        and (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes < 0
        )
    ):
        raise ValueError("max_bytes must be a nonnegative integer or None")
    with _private_parent(path, tighten=tighten) as (
        target,
        parent_descriptor,
        name,
    ):
        expected = _require_private_leaf(target, parent_descriptor, name)
        if expected is None:
            raise FileNotFoundError(target)
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
        try:
            opened = os.fstat(descriptor)
            _require_opened_leaf(target, expected, opened)
            if tighten:
                os.fchmod(descriptor, PRIVATE_FILE_MODE)
            if max_bytes is not None and opened.st_size > max_bytes:
                if not tail:
                    raise OSError("private file exceeds maximum size")
                os.lseek(descriptor, opened.st_size - max_bytes, os.SEEK_SET)
            chunks: list[bytes] = []
            remaining = None if max_bytes is None else max_bytes + 1
            while remaining is None or remaining > 0:
                read_size = 65_536 if remaining is None else min(65_536, remaining)
                chunk = os.read(descriptor, read_size)
                if not chunk:
                    break
                chunks.append(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
            _require_leaf_identity(
                target,
                parent_descriptor,
                name,
                opened,
            )
            payload = b"".join(chunks)
            if max_bytes is not None and len(payload) > max_bytes:
                raise OSError("private file exceeds maximum size")
            return payload, (opened.st_dev, opened.st_ino)
        finally:
            os.close(descriptor)


def read_private_bytes(
    path: Path,
    *,
    tighten: bool = True,
    max_bytes: int | None = None,
    tail: bool = False,
) -> bytes:
    return read_private_bytes_with_identity(
        path,
        tighten=tighten,
        max_bytes=max_bytes,
        tail=tail,
    )[0]


def read_private_text(
    path: Path,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
    tighten: bool = True,
    max_bytes: int | None = None,
    tail: bool = False,
) -> str:
    return read_private_bytes(
        path,
        tighten=tighten,
        max_bytes=max_bytes,
        tail=tail,
    ).decode(encoding, errors)


def read_private_text_with_identity(
    path: Path,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
    tighten: bool = True,
    max_bytes: int | None = None,
) -> tuple[str, tuple[int, int]]:
    payload, identity = read_private_bytes_with_identity(
        path,
        tighten=tighten,
        max_bytes=max_bytes,
    )
    return payload.decode(encoding, errors), identity


_SENSITIVE_EXACT_KEYS = {
    "args",
    "arguments",
    "body",
    "command",
    "content",
    "input",
    "output",
    "payload",
    "prompt",
    "raw",
    "request",
    "response",
    "text",
    "toolargs",
    "toolarguments",
    "toolinput",
    "tooloutput",
    "toolresponse",
    "toolresult",
    "tooluseresult",
    "lastassistantmessage",
    "transcriptpath",
}
_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "apikey",
    "cookie",
    "credential",
    "password",
    "privatekey",
    "secret",
    "token",
    "webhook",
)


def _normalized_key(key: object) -> str:
    return "".join(character for character in str(key).casefold() if character.isalnum())


def _sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return normalized in _SENSITIVE_EXACT_KEYS or any(
        fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS
    ) or normalized.endswith("body")


def _event_name(mapping: Mapping) -> str:
    for key, value in mapping.items():
        if _normalized_key(key) in {"eventname", "hookeventname"}:
            return _normalized_key(value)
    return ""


def redact_event_payload(payload: object) -> object:
    """Return a recursively redacted copy of one untrusted event payload."""
    if isinstance(payload, Mapping):
        notification = _event_name(payload) == "notification"
        return {
            key: (
                REDACTION_MARKER
                if _sensitive_key(key)
                or (_normalized_key(key) == "message" and not notification)
                else redact_event_payload(value)
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_event_payload(value) for value in payload]
    if isinstance(payload, tuple):
        return tuple(redact_event_payload(value) for value in payload)
    return payload


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    max_age_seconds: float | None = None
    max_files: int | None = None
    max_total_bytes: int | None = None
    now_epoch: float | None = None
    patterns: tuple[str, ...] = ("*",)
    recursive: bool = True

    def __post_init__(self) -> None:
        for name in ("max_age_seconds", "max_files", "max_total_bytes"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be nonnegative")
        if not self.patterns:
            raise ValueError("patterns must not be empty")


@dataclass(frozen=True, slots=True)
class _RetentionEntry:
    path: Path
    relative: str
    modified: float
    size: int
    device: int
    inode: int


def _retention_entries(
    root: Path,
    patterns: tuple[str, ...],
    *,
    recursive: bool,
) -> list[_RetentionEntry]:
    entries: list[_RetentionEntry] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        base = Path(directory)
        if not recursive:
            directory_names[:] = []
        safe_directories = []
        for name in directory_names:
            child = base / name
            info = _lstat(child)
            if info is not None and stat.S_ISDIR(info.st_mode):
                _chmod(child, PRIVATE_DIRECTORY_MODE)
                safe_directories.append(name)
        directory_names[:] = safe_directories
        for name in file_names:
            path = base / name
            info = _lstat(path)
            if (
                info is None
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
            ):
                continue
            relative_path = path.relative_to(root)
            if not any(relative_path.match(pattern) for pattern in patterns):
                continue
            _chmod(path, PRIVATE_FILE_MODE)
            entries.append(
                _RetentionEntry(
                    path=path,
                    relative=str(relative_path),
                    modified=info.st_mtime,
                    size=info.st_size,
                    device=info.st_dev,
                    inode=info.st_ino,
                )
            )
    entries.sort(key=lambda entry: (entry.modified, entry.relative))
    return entries


def _unlink_unchanged_regular(entry: _RetentionEntry) -> bool:
    current = _lstat(entry.path)
    if (
        current is None
        or not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or current.st_dev != entry.device
        or current.st_ino != entry.inode
    ):
        return False
    entry.path.unlink()
    return True


def enforce_retention(root: Path, policy: RetentionPolicy) -> tuple[Path, ...]:
    """Remove expired, then oldest regular files without following symlinks."""
    selected_root = ensure_private_directory(Path(root).expanduser())
    entries = _retention_entries(
        selected_root,
        policy.patterns,
        recursive=policy.recursive,
    )
    removed: list[Path] = []
    remaining: list[_RetentionEntry] = []
    now = time.time() if policy.now_epoch is None else float(policy.now_epoch)
    for entry in entries:
        expired = (
            policy.max_age_seconds is not None
            and now - entry.modified > float(policy.max_age_seconds)
        )
        if expired and _unlink_unchanged_regular(entry):
            removed.append(entry.path)
        else:
            remaining.append(entry)

    total_bytes = sum(entry.size for entry in remaining)
    while remaining and (
        (policy.max_files is not None and len(remaining) > policy.max_files)
        or (
            policy.max_total_bytes is not None
            and total_bytes > policy.max_total_bytes
        )
    ):
        oldest = remaining.pop(0)
        if _unlink_unchanged_regular(oldest):
            removed.append(oldest.path)
            total_bytes -= oldest.size
    return tuple(removed)
