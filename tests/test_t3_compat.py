from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sidepulse.models import AgentMode
from sidepulse.t3_compat import (
    T3_REASON_FAILED,
    T3Snapshot,
    T3SnapshotService,
    _open_read_only,
    read_t3_snapshot,
)


def _database(base: Path, *, supported: bool = True) -> Path:
    target = base / "userdata" / "state.sqlite"
    target.parent.mkdir(parents=True)
    connection = sqlite3.connect(target)
    connection.executescript(
        """
        CREATE TABLE projection_projects (
          project_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          workspace_root TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          deleted_at TEXT
        );
        CREATE TABLE projection_threads (
          thread_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          title TEXT NOT NULL,
          model_selection_json TEXT NOT NULL,
          runtime_mode TEXT NOT NULL,
          interaction_mode TEXT NOT NULL,
          branch TEXT,
          worktree_path TEXT,
          latest_turn_id TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          archived_at TEXT,
          settled_at TEXT,
          pending_approval_count INTEGER NOT NULL,
          pending_user_input_count INTEGER NOT NULL,
          has_actionable_proposed_plan INTEGER NOT NULL,
          deleted_at TEXT,
          additive_future_column TEXT
        );
        """
    )
    session_columns = """
          thread_id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          provider_name TEXT,
          provider_instance_id TEXT,
          {provider_thread_id}
          runtime_mode TEXT NOT NULL,
          active_turn_id TEXT,
          last_error TEXT,
          updated_at TEXT NOT NULL
    """.format(
        provider_thread_id=(
            "provider_thread_id TEXT," if supported else ""
        )
    )
    connection.execute(
        f"CREATE TABLE projection_thread_sessions ({session_columns})"
    )
    connection.commit()
    connection.close()
    return target


def _insert_thread(database: Path) -> None:
    now = "2026-08-16T12:00:00Z"
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO projection_projects VALUES (?, ?, ?, ?, ?, ?)",
        ("project-1", "SidePulse", "/repo/sidepulse", now, now, None),
    )
    connection.execute(
        """
        INSERT INTO projection_threads (
          thread_id, project_id, title, model_selection_json, runtime_mode,
          interaction_mode, branch, worktree_path, latest_turn_id, created_at,
          updated_at, archived_at, settled_at, pending_approval_count,
          pending_user_input_count, has_actionable_proposed_plan, deleted_at,
          additive_future_column
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "thread-1",
            "project-1",
            "Fix the release blockers",
            json.dumps(
                {
                    "instanceId": "codex-main",
                    "model": "gpt-5.6-codex",
                    "options": {"reasoningEffort": "high"},
                }
            ),
            "full-access",
            "default",
            "fix/release",
            "/repo/sidepulse/.worktrees/release",
            "turn-1",
            now,
            now,
            None,
            None,
            1,
            0,
            0,
            None,
            "ignored",
        ),
    )
    connection.execute(
        """
        INSERT INTO projection_thread_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "thread-1",
            "running",
            "Codex",
            "codex-main",
            "provider-thread-42",
            "full-access",
            "turn-1",
            None,
            now,
        ),
    )
    connection.commit()
    connection.close()


def test_t3_read_only_projection_preserves_provider_and_thread_identity(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _insert_thread(database)

    snapshot = read_t3_snapshot(
        base_dir=tmp_path,
        environment_id="local-env",
    )

    assert snapshot.compatible is True
    assert snapshot.truncated is False
    assert len(snapshot.threads) == 1
    thread = snapshot.threads[0]
    assert thread.provider == "codex"
    assert thread.provider_instance == "codex-main"
    assert thread.provider_thread_id == "provider-thread-42"
    assert thread.branch == "fix/release"
    assert thread.worktree_path == "/repo/sidepulse/.worktrees/release"
    assert thread.model == "gpt-5.6-codex"
    assert thread.reasoning_effort == "high"
    assert thread.deep_link == "t3code://threads/local-env/thread-1"

    status = snapshot.agent_statuses()[0]
    assert status.provider == "codex"
    assert status.agent_id == "codex:session:provider-thread-42"
    assert status.mode is AgentMode.WAITING_FOR_INPUT
    assert status.event_name == "PermissionRequest"
    assert status.origin == "T3 Code · SidePulse · fix/release"


def test_t3_projection_database_is_opened_query_only(tmp_path: Path) -> None:
    database = _database(tmp_path)
    connection = _open_read_only(database)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "INSERT INTO projection_projects VALUES (?, ?, ?, ?, ?, ?)",
                ("p", "P", "/tmp", "x", "x", None),
            )
    finally:
        connection.close()


def test_t3_missing_required_column_is_visible_as_unsupported(
    tmp_path: Path,
) -> None:
    _database(tmp_path, supported=False)

    snapshot = read_t3_snapshot(base_dir=tmp_path)

    assert snapshot.compatible is False
    assert snapshot.reason == "t3_schema_unsupported"
    assert snapshot.threads == ()


def test_t3_service_retains_last_known_good_on_failure(tmp_path: Path) -> None:
    snapshot = T3Snapshot(
        observed_at=datetime.now(timezone.utc),
        database_path=tmp_path / "state.sqlite",
        schema_fingerprint="sha256:" + "0" * 64,
        sqlite_user_version=0,
        threads=(),
    )
    calls = 0

    def reader(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return snapshot
        raise OSError("database disappeared")

    service = T3SnapshotService(reader=reader, minimum_interval=0.01)
    first_ready = threading.Event()
    second_ready = threading.Event()
    first = []
    second = []

    service.request(
        lambda observation: (first.append(observation), first_ready.set()),
        force=True,
    )
    assert first_ready.wait(1.0)
    time.sleep(0.02)
    service.request(
        lambda observation: (second.append(observation), second_ready.set()),
        force=True,
    )
    assert second_ready.wait(1.0)
    service.close()

    assert first[0].snapshot == snapshot
    assert first[0].reason is None
    assert second[0].snapshot == snapshot
    assert second[0].reason == T3_REASON_FAILED
