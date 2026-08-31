"""Owner-private local persistence for inert effect-pack JSON.

The store accepts only the validated data contract from :mod:`effect_packs`.
It does not download packs, import modules, execute callbacks, or mutate the
built-in registry. Create, update, and remove are separate explicit actions.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Final

from .effect_history import EffectBrowserRow, project_effect_history
from .effect_history_store import (
    EffectHistoryRestoreHealth,
    default_effect_history_path,
    load_effect_history,
)
from .effect_packs import (
    MAX_PACK_BYTES,
    EffectPack,
    EffectPackError,
    export_pack,
    validate_pack,
)
from .effect_studio import (
    GalleryPackProjection,
    GalleryRow,
    SemanticFamily,
    build_gallery_index,
    build_gallery_rows,
    plan_pack_export,
    plan_pack_import,
)
from .private_export import write_private_export
from .private_io import (
    PrivateWriteTransaction,
    ensure_private_directory,
    ensure_private_file,
    read_private_bytes,
    read_private_bytes_with_identity,
    unlink_private_file_if_unchanged,
)
from .state_paths import default_state_dir

EFFECT_PACK_STORE_DIRECTORY: Final = "effect-packs"
MAX_STORED_EFFECT_PACKS: Final = 128
MAX_EFFECT_PACK_STORE_BYTES: Final = 8 * 1024 * 1024
MAX_EFFECT_PACK_FILENAME_BYTES: Final = 96
_STORE_LOCK_NAME: Final = ".store.lock"

_PACK_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\Z")


class EffectPackStoreError(ValueError):
    """Raised when a local pack operation cannot be completed safely."""


class PackMutationStatus(str, Enum):
    INSTALLED = "installed"
    UPDATED = "updated"
    REMOVED = "removed"
    DUPLICATED = "duplicated"
    RENAMED = "renamed"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class PackMutationReceipt:
    """Path-free receipt for one explicit local mutation decision."""

    status: PackMutationStatus
    pack_id: str
    digest: str | None = None
    previous_digest: str | None = None
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status is not PackMutationStatus.REFUSED


@dataclass(frozen=True, slots=True)
class EffectHistoryProjection:
    """Restore health and content-free browser rows for CLI presentation."""

    health: EffectHistoryRestoreHealth
    rows: tuple[EffectBrowserRow, ...]


@dataclass(frozen=True, slots=True)
class _StoredLeaf:
    path: Path
    size: int


def default_effect_pack_store_path(home: Path | None = None) -> Path:
    """Return the pack directory inside JR Bar's existing private state root."""

    return default_state_dir(home) / EFFECT_PACK_STORE_DIRECTORY


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _pack_identifier(value: object) -> str:
    if (
        type(value) is not str
        or _PACK_IDENTIFIER.fullmatch(value) is None
        or len(f"{value}.json".encode()) > MAX_EFFECT_PACK_FILENAME_BYTES
    ):
        raise EffectPackStoreError("invalid effect pack identifier")
    return value


def _pack_name(value: object) -> str:
    if type(value) is not str or not value.strip() or len(value) > 160:
        raise EffectPackStoreError("invalid effect pack name")
    return value.strip()


def _strict_object(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise EffectPackStoreError("effect pack JSON is invalid")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise EffectPackStoreError("effect pack JSON is invalid")


def _decode_pack(payload: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except EffectPackStoreError:
        raise
    except (RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise EffectPackStoreError("effect pack JSON is invalid") from error
    if not isinstance(value, Mapping):
        raise EffectPackStoreError("effect pack JSON must be an object")
    return value


class EffectPackStore:
    """Bounded, no-follow local store for canonical data-only effect packs."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (
            default_effect_pack_store_path()
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
                raise EffectPackStoreError(
                    "effect pack store is unavailable"
                ) from error
        except OSError as error:
            raise EffectPackStoreError("effect pack store is unavailable") from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise EffectPackStoreError("effect pack store is unavailable")
        try:
            return ensure_private_directory(self.root)
        except OSError as error:
            raise EffectPackStoreError("effect pack store is unavailable") from error

    def _target(self, pack_id: object) -> Path:
        identifier = _pack_identifier(pack_id)
        return self.root / f"{identifier}.json"

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        root = self._root(create=True)
        if root is None:
            raise EffectPackStoreError("effect pack store is unavailable")
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
            raise EffectPackStoreError("effect pack store is unavailable") from error
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise EffectPackStoreError("effect pack store is unavailable")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except OSError as error:
            raise EffectPackStoreError("effect pack store is unavailable") from error
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
            raise EffectPackStoreError("effect pack store is unavailable") from error
        leaves: list[_StoredLeaf] = []
        for path in entries:
            name = path.name
            if name == _STORE_LOCK_NAME:
                try:
                    lock_info = path.lstat()
                except OSError as error:
                    raise EffectPackStoreError(
                        "effect pack store is unavailable"
                    ) from error
                if (
                    stat.S_ISLNK(lock_info.st_mode)
                    or not stat.S_ISREG(lock_info.st_mode)
                    or lock_info.st_nlink != 1
                    or lock_info.st_size != 0
                ):
                    raise EffectPackStoreError(
                        "effect pack store contains an unsafe entry"
                    )
                continue
            if not name.endswith(".json"):
                raise EffectPackStoreError(
                    "effect pack store contains an invalid entry"
                )
            pack_id = name[:-5]
            if _pack_identifier(pack_id) != pack_id:
                raise EffectPackStoreError(
                    "effect pack store contains an invalid entry"
                )
            try:
                info = path.lstat()
            except OSError as error:
                raise EffectPackStoreError(
                    "effect pack store is unavailable"
                ) from error
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > MAX_PACK_BYTES
            ):
                raise EffectPackStoreError("effect pack store contains an unsafe entry")
            leaves.append(_StoredLeaf(path=path, size=info.st_size))
        self._require_store_bounds(tuple(leaves))
        return tuple(leaves)

    @staticmethod
    def _require_store_bounds(entries: tuple[_StoredLeaf, ...]) -> None:
        if len(entries) > MAX_STORED_EFFECT_PACKS:
            raise EffectPackStoreError("effect pack store exceeds pack count limit")
        if sum(entry.size for entry in entries) > MAX_EFFECT_PACK_STORE_BYTES:
            raise EffectPackStoreError("effect pack store exceeds total size limit")

    @staticmethod
    def _candidate(
        source: Path | str | os.PathLike[str] | EffectPack | Mapping[str, Any],
    ) -> tuple[EffectPack, bytes]:
        if isinstance(source, (str, os.PathLike)):
            try:
                raw = read_private_bytes(
                    Path(source).expanduser(),
                    tighten=False,
                    max_bytes=MAX_PACK_BYTES,
                )
            except OSError as error:
                raise EffectPackStoreError(
                    "effect pack source is unavailable"
                ) from error
            candidate: EffectPack | Mapping[str, Any] = _decode_pack(raw)
        elif isinstance(source, (EffectPack, Mapping)):
            candidate = source
        else:
            raise EffectPackStoreError("effect pack source is invalid")
        try:
            plan_pack_import(candidate)
            export_plan = plan_pack_export(candidate)
            pack = validate_pack(_decode_pack(export_plan.payload))
        except (EffectPackError, TypeError, ValueError) as error:
            raise EffectPackStoreError("effect pack source is invalid") from error
        return pack, export_plan.payload

    def _read_leaf(
        self,
        path: Path,
        *,
        expected_pack_id: str,
    ) -> tuple[EffectPack, bytes, tuple[int, int]]:
        try:
            payload, identity = read_private_bytes_with_identity(
                path,
                max_bytes=MAX_PACK_BYTES,
            )
        except OSError as error:
            raise EffectPackStoreError("stored effect pack is unavailable") from error
        candidate = _decode_pack(payload)
        try:
            plan_pack_import(candidate)
            pack = validate_pack(candidate)
            canonical = export_pack(pack)
        except (EffectPackError, TypeError, ValueError) as error:
            raise EffectPackStoreError("stored effect pack is invalid") from error
        if pack.pack_id != expected_pack_id or canonical != payload:
            raise EffectPackStoreError("stored effect pack is not canonical")
        return pack, payload, identity

    @staticmethod
    def _identity_candidate(
        pack: EffectPack,
        *,
        pack_id: str,
        name: str,
    ) -> tuple[EffectPack, bytes]:
        """Revalidate a data-only pack after changing only its identity."""

        try:
            return EffectPackStore._candidate(
                replace(pack, pack_id=pack_id, name=name)
            )
        except EffectPackStoreError as error:
            raise EffectPackStoreError("invalid effect pack name") from error

    def install(
        self,
        source: Path | str | os.PathLike[str] | EffectPack | Mapping[str, Any],
        *,
        update: bool = False,
    ) -> PackMutationReceipt:
        """Install a new pack, or update only when explicitly requested."""

        if update:
            return self.update(source)
        pack, payload = self._candidate(source)
        target = self._target(pack.pack_id)
        with self._mutation_lock():
            entries = self._entries()
            existing = next(
                (entry for entry in entries if entry.path == target),
                None,
            )
            if existing is not None:
                _existing_pack, existing_payload, _identity = self._read_leaf(
                    target,
                    expected_pack_id=pack.pack_id,
                )
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    pack.pack_id,
                    previous_digest=_digest(existing_payload),
                    reason="already_installed",
                )
            if len(entries) >= MAX_STORED_EFFECT_PACKS:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    pack.pack_id,
                    reason="pack_count_limit",
                )
            projected_size = sum(entry.size for entry in entries) + len(payload)
            if projected_size > MAX_EFFECT_PACK_STORE_BYTES:
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
                    transaction.verify(target, payload, max_bytes=MAX_PACK_BYTES)
                    self._entries()
            except OSError as error:
                raise EffectPackStoreError("effect pack install failed") from error
        return PackMutationReceipt(
            PackMutationStatus.INSTALLED,
            pack.pack_id,
            digest=_digest(payload),
        )

    def update(
        self,
        source: Path | str | os.PathLike[str] | EffectPack | Mapping[str, Any],
    ) -> PackMutationReceipt:
        """Replace exactly one existing pack after identity and bound checks."""

        pack, payload = self._candidate(source)
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
            _current_pack, current, identity = self._read_leaf(
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
            if projected_size > MAX_EFFECT_PACK_STORE_BYTES:
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
                        max_original_bytes=MAX_PACK_BYTES,
                        expected_identity=identity,
                    )
                    transaction.verify(target, payload, max_bytes=MAX_PACK_BYTES)
                    self._entries()
            except OSError as error:
                raise EffectPackStoreError("effect pack update failed") from error
        return PackMutationReceipt(
            PackMutationStatus.UPDATED,
            pack.pack_id,
            digest=next_digest,
            previous_digest=current_digest,
        )

    def remove(self, pack_id: object) -> PackMutationReceipt:
        """Remove one installed canonical pack without following links."""

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
                raise EffectPackStoreError("effect pack remove failed") from error
            if not removed:
                raise EffectPackStoreError("effect pack remove failed")
        return PackMutationReceipt(
            PackMutationStatus.REMOVED,
            identifier,
            previous_digest=_digest(payload),
        )

    def duplicate(
        self,
        pack_id: object,
        new_pack_id: object,
        new_name: object,
    ) -> PackMutationReceipt:
        """Copy one installed data-only pack under a new identity."""

        source_identifier = _pack_identifier(pack_id)
        target_identifier = _pack_identifier(new_pack_id)
        target_name = _pack_name(new_name)
        source = self._target(source_identifier)
        target = self._target(target_identifier)
        with self._mutation_lock():
            entries = self._entries()
            source_entry = next(
                (entry for entry in entries if entry.path == source),
                None,
            )
            if source_entry is None:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    target_identifier,
                    reason="not_installed",
                )
            if any(entry.path == target for entry in entries):
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    target_identifier,
                    reason="already_installed",
                )
            source_pack, source_payload, _source_identity = self._read_leaf(
                source,
                expected_pack_id=source_identifier,
            )
            _candidate, payload = self._identity_candidate(
                source_pack,
                pack_id=target_identifier,
                name=target_name,
            )
            if len(entries) >= MAX_STORED_EFFECT_PACKS:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    target_identifier,
                    reason="pack_count_limit",
                )
            projected_size = sum(entry.size for entry in entries) + len(payload)
            if projected_size > MAX_EFFECT_PACK_STORE_BYTES:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    target_identifier,
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
                    transaction.verify(target, payload, max_bytes=MAX_PACK_BYTES)
                    self._entries()
            except OSError as error:
                raise EffectPackStoreError("effect pack duplicate failed") from error
        return PackMutationReceipt(
            PackMutationStatus.DUPLICATED,
            target_identifier,
            digest=_digest(payload),
            previous_digest=_digest(source_payload),
        )

    def rename(
        self,
        pack_id: object,
        new_pack_id: object,
        new_name: object,
    ) -> PackMutationReceipt:
        """Move one installed pack to a revalidated identity transactionally."""

        source_identifier = _pack_identifier(pack_id)
        target_identifier = _pack_identifier(new_pack_id)
        target_name = _pack_name(new_name)
        source = self._target(source_identifier)
        target = self._target(target_identifier)
        with self._mutation_lock():
            entries = self._entries()
            source_entry = next(
                (entry for entry in entries if entry.path == source),
                None,
            )
            if source_entry is None:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    target_identifier,
                    reason="not_installed",
                )
            if target != source and any(entry.path == target for entry in entries):
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    target_identifier,
                    reason="already_installed",
                )
            source_pack, source_payload, source_identity = self._read_leaf(
                source,
                expected_pack_id=source_identifier,
            )
            _candidate, payload = self._identity_candidate(
                source_pack,
                pack_id=target_identifier,
                name=target_name,
            )
            previous_digest = _digest(source_payload)
            next_digest = _digest(payload)
            if source == target and source_payload == payload:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    target_identifier,
                    digest=previous_digest,
                    previous_digest=previous_digest,
                    reason="already_current",
                )
            projected_size = (
                sum(entry.size for entry in entries) - source_entry.size + len(payload)
            )
            if projected_size > MAX_EFFECT_PACK_STORE_BYTES:
                return PackMutationReceipt(
                    PackMutationStatus.REFUSED,
                    target_identifier,
                    digest=next_digest,
                    previous_digest=previous_digest,
                    reason="total_size_limit",
                )
            try:
                with PrivateWriteTransaction() as transaction:
                    transaction.write(
                        target,
                        payload,
                        max_original_bytes=(
                            MAX_PACK_BYTES if target == source else 0
                        ),
                        expected_identity=(
                            source_identity if target == source else None
                        ),
                    )
                    transaction.verify(target, payload, max_bytes=MAX_PACK_BYTES)
                    if target != source:
                        removed = unlink_private_file_if_unchanged(
                            source,
                            expected_identity=source_identity,
                        )
                        if not removed:
                            raise OSError("source effect pack changed during rename")
            except OSError as error:
                raise EffectPackStoreError("effect pack rename failed") from error
        return PackMutationReceipt(
            PackMutationStatus.RENAMED,
            target_identifier,
            digest=next_digest,
            previous_digest=previous_digest,
        )

    def list(self) -> tuple[EffectPack, ...]:
        """Return installed packs in deterministic identifier order."""

        packs: list[EffectPack] = []
        for entry in self._entries():
            pack_id = entry.path.name[:-5]
            pack, _payload, _identity = self._read_leaf(
                entry.path,
                expected_pack_id=pack_id,
            )
            packs.append(pack)
        return tuple(sorted(packs, key=lambda pack: pack.pack_id))

    def inspect(self, pack_id: object) -> EffectPack:
        """Load one installed pack after exact filename and canonical checks."""

        identifier = _pack_identifier(pack_id)
        target = self._target(identifier)
        entries = self._entries()
        if not any(entry.path == target for entry in entries):
            raise EffectPackStoreError("effect pack is not installed")
        pack, _payload, _identity = self._read_leaf(
            target,
            expected_pack_id=identifier,
        )
        return pack

    def canonical_export(self, pack_id: object) -> bytes:
        """Return the exact canonical JSON bytes for one installed pack."""

        return export_pack(self.inspect(pack_id))

    def export(self, pack_id: object, target: Path) -> Path:
        """Publish a canonical owner-private copy to an explicit local path."""

        payload = self.canonical_export(pack_id)
        try:
            return write_private_export(
                Path(target).expanduser().absolute(),
                payload,
                max_bytes=MAX_PACK_BYTES,
            )
        except OSError as error:
            raise EffectPackStoreError("effect pack export failed") from error

    def gallery_index(self) -> tuple[GalleryPackProjection, ...]:
        """Project installed manifests through Effect Studio's pack index."""

        return build_gallery_index(self.list())

    @staticmethod
    def built_in_gallery(
        *,
        query: object = "",
        semantic_family: SemanticFamily | None = None,
    ) -> tuple[GalleryRow, ...]:
        """Return Effect Studio's deterministic built-in effect gallery."""

        return build_gallery_rows(
            query=query,
            semantic_family=semantic_family,
        )

    @staticmethod
    def effect_history(
        path: Path | None = None,
    ) -> EffectHistoryProjection:
        """Load and project only the content-free effect-history contract."""

        target = default_effect_history_path() if path is None else Path(path)
        restored = load_effect_history(target)
        return EffectHistoryProjection(
            restored.health,
            project_effect_history(restored.history),
        )


__all__ = [
    "EFFECT_PACK_STORE_DIRECTORY",
    "MAX_EFFECT_PACK_FILENAME_BYTES",
    "MAX_EFFECT_PACK_STORE_BYTES",
    "MAX_STORED_EFFECT_PACKS",
    "EffectHistoryProjection",
    "EffectPackStore",
    "EffectPackStoreError",
    "PackMutationReceipt",
    "PackMutationStatus",
    "default_effect_pack_store_path",
]
