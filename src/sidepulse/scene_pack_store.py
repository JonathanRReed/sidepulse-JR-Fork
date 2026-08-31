"""Owner-private, bounded persistence for data-only Scene packs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .effect_pack_store import PackMutationReceipt, PackMutationStatus
from .private_export import write_private_export
from .private_io import (
    PrivateWriteTransaction,
    ensure_private_directory,
    ensure_private_file,
    read_private_bytes,
    read_private_bytes_with_identity,
    unlink_private_file_if_unchanged,
)
from .scene_pack_preview import ScenePackImportPlan, plan_scene_pack_import
from .scene_packs import (
    MAX_SCENE_PACK_BYTES,
    ScenePack,
    ScenePackError,
    export_scene_pack,
)
from .state_paths import default_state_dir

SCENE_PACK_STORE_DIRECTORY: Final = "scene-packs"
MAX_STORED_SCENE_PACKS: Final = 64
MAX_SCENE_PACK_STORE_BYTES: Final = 4 * 1024 * 1024
MAX_SCENE_PACK_FILENAME_BYTES: Final = 96
_STORE_LOCK_NAME: Final = ".store.lock"
_PACK_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\Z")


class ScenePackStoreError(ValueError):
    """Raised when a local Scene pack operation cannot complete safely."""


@dataclass(frozen=True, slots=True)
class _StoredLeaf:
    path: Path
    size: int


def default_scene_pack_store_path(home: Path | None = None) -> Path:
    """Return the Scene pack directory in JR Bar's private state root."""

    return default_state_dir(home) / SCENE_PACK_STORE_DIRECTORY


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _pack_identifier(value: object) -> str:
    if (
        type(value) is not str
        or _PACK_IDENTIFIER.fullmatch(value) is None
        or len(f"{value}.json".encode()) > MAX_SCENE_PACK_FILENAME_BYTES
    ):
        raise ScenePackStoreError("invalid Scene pack identifier")
    return value


def _strict_object(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ScenePackStoreError("Scene pack JSON is invalid")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ScenePackStoreError("Scene pack JSON is invalid")


def _decode_pack(payload: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except ScenePackStoreError:
        raise
    except (RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise ScenePackStoreError("Scene pack JSON is invalid") from error
    if not isinstance(value, Mapping):
        raise ScenePackStoreError("Scene pack JSON must be an object")
    return value


class ScenePackStore:
    """No-follow store that imports only validated, previewed Scene packs."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (
            default_scene_pack_store_path()
            if root is None
            else Path(root).expanduser()
        )

    def _root(self, *, create: bool) -> Path | None:
        try:
            info = self.root.lstat()
        except FileNotFoundError:
            if not create:
                return None
            try:
                return ensure_private_directory(self.root)
            except OSError as error:
                raise ScenePackStoreError(
                    "Scene pack store is unavailable"
                ) from error
        except OSError as error:
            raise ScenePackStoreError("Scene pack store is unavailable") from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ScenePackStoreError("Scene pack store is unavailable")
        try:
            return ensure_private_directory(self.root)
        except OSError as error:
            raise ScenePackStoreError("Scene pack store is unavailable") from error

    def _target(self, pack_id: object) -> Path:
        return self.root / f"{_pack_identifier(pack_id)}.json"

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        root = self._root(create=True)
        if root is None:
            raise ScenePackStoreError("Scene pack store is unavailable")
        lock_path = root / _STORE_LOCK_NAME
        try:
            ensure_private_file(lock_path)
            descriptor = os.open(
                lock_path,
                os.O_RDWR
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError as error:
            raise ScenePackStoreError("Scene pack store is unavailable") from error
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise ScenePackStoreError("Scene pack store is unavailable")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except OSError as error:
            raise ScenePackStoreError("Scene pack store is unavailable") from error
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _entries(self, *, create: bool = False) -> tuple[_StoredLeaf, ...]:
        root = self._root(create=create)
        if root is None:
            return ()
        try:
            entries = tuple(sorted(root.iterdir(), key=lambda item: item.name))
        except OSError as error:
            raise ScenePackStoreError("Scene pack store is unavailable") from error
        leaves: list[_StoredLeaf] = []
        for path in entries:
            if path.name == _STORE_LOCK_NAME:
                self._require_lock(path)
                continue
            if not path.name.endswith(".json"):
                raise ScenePackStoreError(
                    "Scene pack store contains an invalid entry"
                )
            pack_id = path.name[:-5]
            if _pack_identifier(pack_id) != pack_id:
                raise ScenePackStoreError(
                    "Scene pack store contains an invalid entry"
                )
            try:
                info = path.lstat()
            except OSError as error:
                raise ScenePackStoreError(
                    "Scene pack store is unavailable"
                ) from error
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > MAX_SCENE_PACK_BYTES
            ):
                raise ScenePackStoreError(
                    "Scene pack store contains an unsafe entry"
                )
            leaves.append(_StoredLeaf(path=path, size=info.st_size))
        result = tuple(leaves)
        self._require_store_bounds(result)
        return result

    @staticmethod
    def _require_lock(path: Path) -> None:
        try:
            info = path.lstat()
        except OSError as error:
            raise ScenePackStoreError("Scene pack store is unavailable") from error
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size != 0
        ):
            raise ScenePackStoreError("Scene pack store contains an unsafe entry")

    @staticmethod
    def _require_store_bounds(entries: tuple[_StoredLeaf, ...]) -> None:
        if len(entries) > MAX_STORED_SCENE_PACKS:
            raise ScenePackStoreError("Scene pack store exceeds pack count limit")
        if sum(entry.size for entry in entries) > MAX_SCENE_PACK_STORE_BYTES:
            raise ScenePackStoreError("Scene pack store exceeds total size limit")

    @staticmethod
    def _candidate(
        source: Path | str | os.PathLike[str] | ScenePack | Mapping[str, Any],
    ) -> ScenePackImportPlan:
        if isinstance(source, (str, os.PathLike)):
            try:
                raw = read_private_bytes(
                    Path(source).expanduser(),
                    tighten=False,
                    max_bytes=MAX_SCENE_PACK_BYTES,
                )
            except OSError as error:
                raise ScenePackStoreError(
                    "Scene pack source is unavailable"
                ) from error
            candidate: ScenePack | Mapping[str, Any] = _decode_pack(raw)
        elif isinstance(source, (ScenePack, Mapping)):
            candidate = source
        else:
            raise ScenePackStoreError("Scene pack source is invalid")
        try:
            return plan_scene_pack_import(candidate)
        except (ScenePackError, TypeError, ValueError) as error:
            raise ScenePackStoreError("Scene pack source is invalid") from error

    def _read_leaf(
        self,
        path: Path,
        *,
        expected_pack_id: str,
    ) -> tuple[ScenePack, bytes, tuple[int, int]]:
        try:
            payload, identity = read_private_bytes_with_identity(
                path,
                max_bytes=MAX_SCENE_PACK_BYTES,
            )
        except OSError as error:
            raise ScenePackStoreError("stored Scene pack is unavailable") from error
        candidate = _decode_pack(payload)
        try:
            plan = plan_scene_pack_import(candidate)
        except (ScenePackError, TypeError, ValueError) as error:
            raise ScenePackStoreError("stored Scene pack is invalid") from error
        if plan.pack.pack_id != expected_pack_id or plan.payload != payload:
            raise ScenePackStoreError("stored Scene pack is not canonical")
        return plan.pack, payload, identity

    def install(
        self,
        source: Path | str | os.PathLike[str] | ScenePack | Mapping[str, Any],
    ) -> PackMutationReceipt:
        """Install one new pack only after successful validation and preview."""

        plan = self._candidate(source)
        pack = plan.pack
        payload = plan.payload
        target = self._target(pack.pack_id)
        with self._mutation_lock():
            entries = self._entries()
            existing = next(
                (entry for entry in entries if entry.path == target),
                None,
            )
            if existing is not None:
                _old_pack, old_payload, _identity = self._read_leaf(
                    target,
                    expected_pack_id=pack.pack_id,
                )
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    pack.pack_id,
                    previous_digest=_digest(old_payload),
                    reason="already_installed",
                )
            if len(entries) >= MAX_STORED_SCENE_PACKS:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    pack.pack_id,
                    reason="pack_count_limit",
                )
            if sum(entry.size for entry in entries) + len(payload) > MAX_SCENE_PACK_STORE_BYTES:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    pack.pack_id,
                    reason="total_size_limit",
                )
            try:
                with PrivateWriteTransaction() as transaction:
                    transaction.write(
                        target,
                        payload,
                        max_original_bytes=0,
                        expected_identity=None,
                    )
                    transaction.verify(
                        target,
                        payload,
                        max_bytes=MAX_SCENE_PACK_BYTES,
                    )
                    self._entries()
            except OSError as error:
                raise ScenePackStoreError("Scene pack install failed") from error
        return PackMutationReceipt(
            PackMutationStatus.INSTALLED,
            pack.pack_id,
            digest=_digest(payload),
        )

    def update(
        self,
        source: Path | str | os.PathLike[str] | ScenePack | Mapping[str, Any],
    ) -> PackMutationReceipt:
        """Replace exactly one installed pack after identity checks."""

        plan = self._candidate(source)
        pack = plan.pack
        payload = plan.payload
        target = self._target(pack.pack_id)
        with self._mutation_lock():
            entries = self._entries()
            existing = next(
                (entry for entry in entries if entry.path == target),
                None,
            )
            if existing is None:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    pack.pack_id,
                    reason="not_installed",
                )
            _old_pack, current, identity = self._read_leaf(
                target,
                expected_pack_id=pack.pack_id,
            )
            current_digest = _digest(current)
            next_digest = _digest(payload)
            if current == payload:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    pack.pack_id,
                    digest=current_digest,
                    previous_digest=current_digest,
                    reason="already_current",
                )
            projected_size = (
                sum(entry.size for entry in entries) - existing.size + len(payload)
            )
            if projected_size > MAX_SCENE_PACK_STORE_BYTES:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    pack.pack_id,
                    digest=next_digest,
                    previous_digest=current_digest,
                    reason="total_size_limit",
                )
            try:
                with PrivateWriteTransaction() as transaction:
                    transaction.write(
                        target,
                        payload,
                        max_original_bytes=MAX_SCENE_PACK_BYTES,
                        expected_identity=identity,
                    )
                    transaction.verify(
                        target,
                        payload,
                        max_bytes=MAX_SCENE_PACK_BYTES,
                    )
                    self._entries()
            except OSError as error:
                raise ScenePackStoreError("Scene pack update failed") from error
        return PackMutationReceipt(
            PackMutationStatus.UPDATED,
            pack.pack_id,
            digest=next_digest,
            previous_digest=current_digest,
        )

    def remove(self, pack_id: object) -> PackMutationReceipt:
        """Remove one canonical pack without following links."""

        identifier = _pack_identifier(pack_id)
        target = self._target(identifier)
        with self._mutation_lock():
            entries = self._entries()
            if not any(entry.path == target for entry in entries):
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    identifier,
                    reason="not_installed",
                )
            _pack, payload, identity = self._read_leaf(
                target,
                expected_pack_id=identifier,
            )
            try:
                removed = unlink_private_file_if_unchanged(
                    target,
                    expected_identity=identity,
                )
            except OSError as error:
                raise ScenePackStoreError("Scene pack remove failed") from error
            if not removed:
                raise ScenePackStoreError("Scene pack remove failed")
        return PackMutationReceipt(
            PackMutationStatus.REMOVED,
            identifier,
            previous_digest=_digest(payload),
        )

    def list(self) -> tuple[ScenePack, ...]:
        """Return installed packs in deterministic identifier order."""

        packs = [
            self._read_leaf(
                entry.path,
                expected_pack_id=entry.path.name[:-5],
            )[0]
            for entry in self._entries()
        ]
        return tuple(sorted(packs, key=lambda pack: pack.pack_id))

    def inspect(self, pack_id: object) -> ScenePack:
        """Load one installed canonical pack."""

        identifier = _pack_identifier(pack_id)
        target = self._target(identifier)
        if not any(entry.path == target for entry in self._entries()):
            raise ScenePackStoreError("Scene pack is not installed")
        return self._read_leaf(target, expected_pack_id=identifier)[0]

    def preview(self, pack_id: object) -> ScenePackImportPlan:
        """Return the validated import preview for one installed pack."""

        return plan_scene_pack_import(self.inspect(pack_id))

    def preview_source(
        self,
        source: Path | str | os.PathLike[str] | ScenePack | Mapping[str, Any],
    ) -> ScenePackImportPlan:
        """Preview a candidate without creating or mutating the store."""

        return self._candidate(source)

    def canonical_export(self, pack_id: object) -> bytes:
        """Return canonical JSON for one installed pack."""

        return export_scene_pack(self.inspect(pack_id))

    def export(self, pack_id: object, target: Path) -> Path:
        """Publish a canonical owner-private copy to an explicit path."""

        try:
            return write_private_export(
                Path(target).expanduser().absolute(),
                self.canonical_export(pack_id),
                max_bytes=MAX_SCENE_PACK_BYTES,
            )
        except OSError as error:
            raise ScenePackStoreError("Scene pack export failed") from error


__all__ = [
    "MAX_SCENE_PACK_FILENAME_BYTES",
    "MAX_SCENE_PACK_STORE_BYTES",
    "MAX_STORED_SCENE_PACKS",
    "SCENE_PACK_STORE_DIRECTORY",
    "ScenePackStore",
    "ScenePackStoreError",
    "default_scene_pack_store_path",
]
