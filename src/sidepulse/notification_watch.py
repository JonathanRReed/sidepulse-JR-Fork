"""Watches the macOS Notification Center store for newly delivered
notifications, powering the "blink the LEDs in the app's color" signal.

macOS offers no public API to observe OTHER apps' notifications; the
realistic route (and the one this module takes) is polling the
Notification Center store:

    ~/Library/Group Containers/group.com.apple.usernoted/db2/db

an SQLite database maintained by usernoted, with an ``app`` table
(bundle identifiers) and a ``record`` table (one row per delivered
notification, monotonically increasing ``rec_id``). Reading it requires
Full Disk Access -- the same grant the Focus rules use -- and the
schema is Apple-private, so every query here is wrapped: any failure
(no FDA, schema drift, database locked) raises
NotificationWatchUnavailableError and the caller stays silently inert
rather than erroring the app.

The polling contract: call ``latest_record_id`` once to prime a cursor,
then ``delivered_after(cursor)`` on a timer -- it returns the new
cursor plus the bundle ids that delivered since, oldest first.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_RELATIVE_PATH = "Library/Group Containers/group.com.apple.usernoted/db2/db"


class NotificationWatchUnavailableError(RuntimeError):
    """The store can't be read right now (most commonly: no Full Disk
    Access). Callers treat this as "no notifications", never an error."""


def notification_db_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / DB_RELATIVE_PATH


def _connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise NotificationWatchUnavailableError(f"no notification store at {path}")
    try:
        # Read-only URI: never take a write lock on a system database.
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error as exc:
        raise NotificationWatchUnavailableError(str(exc)) from exc


def latest_record_id(path: Path | None = None) -> int:
    """The current newest rec_id -- the cursor to start watching from,
    so pre-existing notifications never replay as fresh blinks."""
    target = path or notification_db_path()
    connection = _connect(target)
    try:
        row = connection.execute("SELECT COALESCE(MAX(rec_id), 0) FROM record").fetchone()
        return int(row[0]) if row else 0
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise NotificationWatchUnavailableError(str(exc)) from exc
    finally:
        connection.close()


def delivered_after(
    record_id: int, path: Path | None = None
) -> tuple[int, list[str]]:
    """(new cursor, bundle ids delivered since ``record_id``, oldest
    first). Duplicate bundle ids are preserved -- three iMessages are
    three entries -- the caller decides how to coalesce."""
    target = path or notification_db_path()
    connection = _connect(target)
    try:
        rows = connection.execute(
            "SELECT record.rec_id, app.identifier "
            "FROM record JOIN app ON app.app_id = record.app_id "
            "WHERE record.rec_id > ? ORDER BY record.rec_id",
            (int(record_id),),
        ).fetchall()
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise NotificationWatchUnavailableError(str(exc)) from exc
    finally:
        connection.close()
    cursor = int(record_id)
    identifiers: list[str] = []
    for rec_id, identifier in rows:
        cursor = max(cursor, int(rec_id))
        if isinstance(identifier, str) and identifier:
            identifiers.append(identifier)
    return cursor, identifiers
