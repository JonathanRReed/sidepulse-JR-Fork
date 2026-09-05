"""Bounded, private, best-effort per-file usage cache.

``UsageFileIndex`` stores one self-contained JSON document per opaque file key.
It is an accelerator, never an authority: every filesystem, SQLite, validation,
locking, or capacity error becomes ``None``/``False`` and callers must parse the
source file instead. Documents are capped independently and are never loaded as
a group. Metadata binds the database to an exact source key and cache version;
opening it for a different source clears its rows without reusing them.

The database and SQLite sidecars must be owner-private regular files with one
link. The private-I/O identity checks narrow path races around ``sqlite3.connect``.
Python's SQLite API cannot connect through an already-open file descriptor, so
another process running as the same owner can still replace a validated path in
the small interval before SQLite opens it. Such a race is detected afterward,
but cannot be prevented completely with the standard library API.
"""

from __future__ import annotations

import json
import os
import sqlite3
import zlib
from collections.abc import Collection
from pathlib import Path
from typing import Any

from .private_io import (
    PRIVATE_FILE_MODE,
    _private_parent,
    _require_leaf_identity,
    _require_private_leaf,
    ensure_private_file,
)

MAX_DATABASE_BYTES = 128 * 1024 * 1024
MAX_ROWS = 8192
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_SOURCE_KEY_BYTES = 64 * 1024
BUSY_TIMEOUT_MS = 100
SQLITE_PROGRESS_INTERVAL = 1000
SQLITE_PROGRESS_CALLBACK_LIMIT = 100
_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")
_SCHEMA_VERSION = 1
_COMPRESSED_PREFIX = b"JRZ1"
_COMPRESSION_THRESHOLD = 4096


class UsageFileIndex:
    """A transactional per-file cache; successful writes commit at ``close``."""

    def __init__(
        self,
        path: Path,
        connection: sqlite3.Connection,
        parent_context: Any,
        parent_descriptor: int,
        name: str,
        opened: os.stat_result,
        dedupe_secret: str,
        progress_budget: _ProgressBudget,
        row_count: int,
        next_sequence: int,
    ) -> None:
        self._path = path
        self._connection = connection
        self._parent_context = parent_context
        self._parent_descriptor = parent_descriptor
        self._name = name
        self._opened = opened
        self._failed = False
        self._closed = False
        self._progress_budget = progress_budget
        self._row_count = row_count
        self._next_sequence = next_sequence
        self._page_cap_applied = False
        self.dedupe_secret = dedupe_secret

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        source_key: dict,
        cache_version: int,
        seed_secret: str,
    ) -> UsageFileIndex | None:
        """Open or initialize an index, returning ``None`` on any cache error."""
        connection: sqlite3.Connection | None = None
        parent_context = None
        try:
            if not _valid_secret(seed_secret) or not isinstance(cache_version, int):
                return None
            source_json = _canonical_json(source_key)
            if source_json is None or len(source_json) > MAX_SOURCE_KEY_BYTES:
                return None

            target = Path(path).expanduser()
            ensure_private_file(target)
            parent_context = _private_parent(target)
            target, parent_descriptor, name = parent_context.__enter__()
            expected = _require_private_leaf(target, parent_descriptor, name)
            if expected is None or expected.st_size > MAX_DATABASE_BYTES:
                raise OSError("usage index exceeds its disk cap")
            _validate_sidecars(target, parent_descriptor)

            connection = sqlite3.connect(
                target,
                timeout=BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,
            )
            opened = target.stat(follow_symlinks=False)
            _require_leaf_identity(target, parent_descriptor, name, opened)
            if opened.st_dev != expected.st_dev or opened.st_ino != expected.st_ino:
                raise OSError("usage index changed while connecting")
            os.chmod(target, PRIVATE_FILE_MODE, follow_symlinks=False)
            progress_budget = _configure_connection(connection)
            schema_state = _schema_state(connection, progress_budget)
            if schema_state == "missing":
                if expected.st_size != 0:
                    raise sqlite3.DatabaseError("usage index schema is missing")
                _apply_page_cap(connection)
                _initialize_schema(connection, progress_budget)
            elif schema_state != "valid":
                raise sqlite3.DatabaseError("usage index schema is not trusted")
            dedupe_secret = _bind_metadata(
                connection,
                progress_budget=progress_budget,
                source_json=source_json,
                cache_version=cache_version,
                seed_secret=seed_secret,
            )
            progress_budget.reset()
            row_count, next_sequence = connection.execute(
                "SELECT COUNT(*), COALESCE(MAX(sequence), 0) + 1 FROM files"
            ).fetchone()
            _validate_sidecars(target, parent_descriptor)
            return cls(
                target,
                connection,
                parent_context,
                parent_descriptor,
                name,
                opened,
                dedupe_secret,
                progress_budget,
                row_count,
                next_sequence,
            )
        except (OSError, ValueError, TypeError, sqlite3.Error):
            if connection is not None:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                connection.close()
            if parent_context is not None:
                parent_context.__exit__(None, None, None)
            return None

    def get(self, key: str) -> dict | None:
        if self._closed or self._failed or not isinstance(key, str):
            return None
        try:
            self._progress_budget.reset()
            row = self._connection.execute(
                "SELECT length(CAST(document AS BLOB)) FROM files WHERE file_key = ?",
                (key,),
            ).fetchone()
            if row is None or not isinstance(row[0], int) or row[0] > MAX_DOCUMENT_BYTES:
                return None
            self._progress_budget.reset()
            row = self._connection.execute(
                "SELECT document FROM files WHERE file_key = ?", (key,)
            ).fetchone()
            if row is None or not isinstance(row[0], (str, bytes)):
                return None
        except sqlite3.Error:
            self._abort()
            return None
        try:
            encoded = row[0]
            if isinstance(encoded, bytes):
                if not encoded.startswith(_COMPRESSED_PREFIX):
                    return None
                inflater = zlib.decompressobj()
                encoded = inflater.decompress(
                    encoded[len(_COMPRESSED_PREFIX):], MAX_DOCUMENT_BYTES + 1
                )
                if (
                    len(encoded) > MAX_DOCUMENT_BYTES
                    or not inflater.eof
                    or inflater.unused_data
                    or inflater.unconsumed_tail
                ):
                    return None
            document = json.loads(encoded)
        except (ValueError, TypeError, RecursionError, zlib.error):
            return None
        return document if isinstance(document, dict) else None

    def put(self, key: str, document: dict) -> bool:
        if self._closed or self._failed or not isinstance(key, str) or not key:
            return False
        encoded = _canonical_json(document)
        if encoded is None or len(encoded) > MAX_DOCUMENT_BYTES:
            return False
        stored: str | bytes = encoded.decode("utf-8")
        if len(encoded) >= _COMPRESSION_THRESHOLD:
            compressed = _COMPRESSED_PREFIX + zlib.compress(encoded, level=1)
            if len(compressed) < len(encoded):
                encoded = compressed
                stored = compressed
        try:
            self._begin()
            self._progress_budget.reset()
            existing = self._connection.execute(
                "SELECT length(CAST(document AS BLOB)) FROM files WHERE file_key = ?",
                (key,),
            ).fetchone()
            exists = existing is not None
            page_size = self._connection.execute("PRAGMA page_size").fetchone()[0]
            page_count = self._connection.execute("PRAGMA page_count").fetchone()[0]
            freelist = self._connection.execute("PRAGMA freelist_count").fetchone()[0]
            available = MAX_DATABASE_BYTES - ((page_count - freelist) * page_size)
            old_size = existing[0] if existing is not None else 0
            estimated_growth = max(0, len(encoded) - old_size)
            if not exists:
                estimated_growth += len(key.encode("utf-8")) + 512
            if estimated_growth > available:
                return False
            if not exists and self._row_count >= MAX_ROWS:
                return False
            self._connection.execute("SAVEPOINT usage_file_put")
            sequence = self._next_sequence
            self._progress_budget.reset()
            self._connection.execute(
                "INSERT INTO files(file_key, document, sequence) VALUES (?, ?, ?) "
                "ON CONFLICT(file_key) DO UPDATE SET document=excluded.document",
                (key, stored, sequence),
            )
            page_size = self._connection.execute("PRAGMA page_size").fetchone()[0]
            page_count = self._connection.execute("PRAGMA page_count").fetchone()[0]
            if page_size * page_count > MAX_DATABASE_BYTES:
                self._connection.execute("ROLLBACK TO usage_file_put")
                self._connection.execute("RELEASE usage_file_put")
                return False
            self._connection.execute("RELEASE usage_file_put")
            if not exists:
                self._row_count += 1
                self._next_sequence += 1
            return True
        except sqlite3.Error:
            try:
                self._connection.execute("ROLLBACK TO usage_file_put")
                self._connection.execute("RELEASE usage_file_put")
            except sqlite3.Error:
                self._abort()
            return False

    def prune(self, keys: Collection[str]) -> None:
        if self._closed or self._failed:
            return
        try:
            self._begin()
            cursor = self._connection.execute("SELECT file_key FROM files")
            while rows := cursor.fetchmany(256):
                stale = [(row[0],) for row in rows if row[0] not in keys]
                if stale:
                    self._connection.executemany("DELETE FROM files WHERE file_key = ?", stale)
                    self._row_count -= len(stale)
        except (TypeError, sqlite3.Error):
            self._abort()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            _require_leaf_identity(
                self._path,
                self._parent_descriptor,
                self._name,
                self._opened,
            )
            _validate_sidecars(self._path, self._parent_descriptor)
            if self._failed:
                self._connection.rollback()
            else:
                self._connection.commit()
        except (OSError, sqlite3.Error):
            try:
                self._connection.rollback()
            except sqlite3.Error:
                pass
        finally:
            self._connection.close()
            try:
                _validate_sidecars(self._path, self._parent_descriptor)
                current = _require_private_leaf(
                    self._path, self._parent_descriptor, self._name
                )
                if current is not None:
                    os.chmod(self._path, PRIVATE_FILE_MODE, follow_symlinks=False)
            except OSError:
                pass
            self._parent_context.__exit__(None, None, None)

    def _begin(self) -> None:
        if not self._connection.in_transaction:
            if not self._page_cap_applied:
                _apply_page_cap(self._connection)
                self._page_cap_applied = True
            self._connection.execute("BEGIN IMMEDIATE")
            # Another connection may have committed since this index opened.
            # Read admission counters only after owning the write transaction.
            self._progress_budget.reset()
            self._row_count, self._next_sequence = self._connection.execute(
                "SELECT count(*), coalesce(max(sequence), 0) + 1 FROM files"
            ).fetchone()

    def _abort(self) -> None:
        self._failed = True
        try:
            self._connection.rollback()
        except sqlite3.Error:
            pass


def _canonical_json(value: object) -> bytes | None:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        return None


def _valid_secret(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        return len(bytes.fromhex(value)) == 32
    except ValueError:
        return False


class _ProgressBudget:
    def __init__(self) -> None:
        self._callbacks = 0

    def reset(self) -> None:
        self._callbacks = 0

    def __call__(self) -> int:
        self._callbacks += 1
        return int(self._callbacks > SQLITE_PROGRESS_CALLBACK_LIMIT)


def _configure_connection(connection: sqlite3.Connection) -> _ProgressBudget:
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_DOCUMENT_BYTES + MAX_SOURCE_KEY_BYTES)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 64 * 1024)
    connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 32)
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA trusted_schema = OFF")
    if hasattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE") and hasattr(connection, "setconfig"):
        connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
    progress_budget = _ProgressBudget()
    connection.set_progress_handler(progress_budget, SQLITE_PROGRESS_INTERVAL)
    return progress_budget


def _schema_state(connection: sqlite3.Connection, progress_budget: _ProgressBudget) -> str:
    progress_budget.reset()
    objects = connection.execute(
        "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    if not objects:
        return "missing"
    if objects != [("table", "files"), ("table", "metadata")]:
        return "invalid"
    expected = {
        "metadata": [
            ("singleton", "INTEGER", 0, 1),
            ("schema_version", "INTEGER", 1, 0),
            ("cache_version", "INTEGER", 1, 0),
            ("source_key", "TEXT", 1, 0),
            ("dedupe_secret", "TEXT", 1, 0),
        ],
        "files": [
            ("file_key", "TEXT", 1, 1),
            ("document", "TEXT", 1, 0),
            ("sequence", "INTEGER", 1, 0),
        ],
    }
    for table, wanted in expected.items():
        progress_budget.reset()
        columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
        actual = [(row[1], row[2], row[3], row[5]) for row in columns]
        if actual != wanted:
            return "invalid"
    progress_budget.reset()
    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    return "valid" if journal_mode == "delete" else "invalid"


def _apply_page_cap(connection: sqlite3.Connection) -> None:
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    max_pages = MAX_DATABASE_BYTES // page_size
    if max_pages < max(page_count, 1):
        raise sqlite3.OperationalError("usage index disk cap is below its current page count")
    applied = connection.execute(f"PRAGMA max_page_count = {max_pages}").fetchone()[0]
    if applied > max_pages:
        raise sqlite3.OperationalError("could not apply usage index disk cap")


def _initialize_schema(
    connection: sqlite3.Connection, progress_budget: _ProgressBudget
) -> None:
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("BEGIN IMMEDIATE")
    progress_budget.reset()
    connection.execute(
        "CREATE TABLE IF NOT EXISTS metadata ("
        "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
        "schema_version INTEGER NOT NULL, cache_version INTEGER NOT NULL, "
        "source_key TEXT NOT NULL, dedupe_secret TEXT NOT NULL)"
    )
    progress_budget.reset()
    connection.execute(
        "CREATE TABLE IF NOT EXISTS files ("
        "file_key TEXT PRIMARY KEY, document TEXT NOT NULL, sequence INTEGER NOT NULL) WITHOUT ROWID"
    )
    connection.commit()


def _bind_metadata(
    connection: sqlite3.Connection,
    *,
    progress_budget: _ProgressBudget,
    source_json: bytes,
    cache_version: int,
    seed_secret: str,
) -> str:
    source_text = source_json.decode("utf-8")
    progress_budget.reset()
    row = connection.execute(
        "SELECT schema_version, cache_version, source_key, dedupe_secret FROM metadata WHERE singleton = 1"
    ).fetchone()
    secret = row[3] if row is not None and _valid_secret(row[3]) else seed_secret
    matches = row == (_SCHEMA_VERSION, cache_version, source_text, secret)
    if not matches:
        _apply_page_cap(connection)
        connection.execute("BEGIN IMMEDIATE")
        progress_budget.reset()
        connection.execute("DELETE FROM files")
        connection.execute("DELETE FROM metadata")
        connection.execute(
            "INSERT INTO metadata VALUES (1, ?, ?, ?, ?)",
            (_SCHEMA_VERSION, cache_version, source_text, secret),
        )
    connection.commit()
    return secret


def _validate_sidecars(target: Path, parent_descriptor: int) -> None:
    for suffix in _SIDECAR_SUFFIXES:
        sidecar = Path(f"{target}{suffix}")
        info = _require_private_leaf(sidecar, parent_descriptor, sidecar.name)
        if info is not None:
            if info.st_size > MAX_DATABASE_BYTES:
                raise OSError("SQLite sidecar exceeds usage index disk cap")
            os.chmod(sidecar, PRIVATE_FILE_MODE, follow_symlinks=False)
