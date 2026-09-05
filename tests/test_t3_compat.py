from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from sidepulse import t3_compat
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
    assert thread.deep_link_candidate is not None
    assert thread.deep_link_candidate.uri == thread.deep_link
    assert thread.deep_link_candidate.experimental is True
    assert thread.deep_link_candidate.desktop_handler_verified is False

    status = snapshot.agent_statuses()[0]
    assert status.provider == "codex"
    assert status.agent_id == "codex:session:provider-thread-42"
    assert status.mode is AgentMode.WAITING_FOR_INPUT
    assert status.event_name == "PermissionRequest"
    assert status.origin == "T3 Code · SidePulse · fix/release"
    assert status.work_key is not None
    assert status.work_key.source_key.source_instance_id == "codex-main"
    assert status.work_key.work_id.value == "thread-1"


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


def test_t3_snapshot_refuses_oversized_values_before_projecting_rows(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _insert_thread(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE projection_threads SET title = ?",
        ("x" * (128 * 1024 + 1),),
    )
    connection.commit()
    connection.close()

    with pytest.raises(sqlite3.DataError, match="string or blob too big"):
        read_t3_snapshot(base_dir=tmp_path)


def test_t3_snapshot_interrupts_an_expensive_compatible_query(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute("DROP TABLE projection_threads")
    connection.execute(
        """
        CREATE VIEW projection_threads AS
        WITH RECURSIVE generated(value) AS (
          SELECT 1
          UNION ALL
          SELECT value + 1 FROM generated WHERE value < 10000000
        )
        SELECT
          printf('thread-%08d', value) AS thread_id,
          'project-1' AS project_id,
          'Thread' AS title,
          '{}' AS model_selection_json,
          'full-access' AS runtime_mode,
          'default' AS interaction_mode,
          NULL AS branch,
          NULL AS worktree_path,
          NULL AS latest_turn_id,
          '2026-08-16T12:00:00Z' AS created_at,
          '2026-08-16T12:00:00Z' AS updated_at,
          NULL AS archived_at,
          NULL AS settled_at,
          0 AS pending_approval_count,
          0 AS pending_user_input_count,
          0 AS has_actionable_proposed_plan,
          NULL AS deleted_at
        FROM generated
        """
    )
    connection.execute(
        "INSERT INTO projection_projects VALUES (?, ?, ?, ?, ?, ?)",
        (
            "project-1",
            "SidePulse",
            "/repo/sidepulse",
            "2026-08-16T12:00:00Z",
            "2026-08-16T12:00:00Z",
            None,
        ),
    )
    connection.commit()
    connection.close()

    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        read_t3_snapshot(base_dir=tmp_path)


@pytest.mark.parametrize(
    ("session_status", "approval_count", "expected_state"),
    (
        ("running", 1, "attention"),
        ("running", 0, "active"),
        ("error", 0, "failed"),
        ("ready", 0, "idle"),
    ),
)
def test_t3_threads_expose_a_small_observation_state_vocabulary(
    tmp_path: Path,
    session_status: str,
    approval_count: int,
    expected_state: str,
) -> None:
    database = _database(tmp_path)
    _insert_thread(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE projection_thread_sessions SET status = ?",
        (session_status,),
    )
    connection.execute(
        "UPDATE projection_threads SET pending_approval_count = ?",
        (approval_count,),
    )
    connection.commit()
    connection.close()

    thread = read_t3_snapshot(base_dir=tmp_path).threads[0]

    assert thread.observation_state.value == expected_state


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

    clock = {"now": 0.0}
    service = T3SnapshotService(
        enabled=True,
        reader=reader,
        monotonic=lambda: clock["now"],
        minimum_interval=0.25,
    )
    first_ready = threading.Event()
    second_ready = threading.Event()
    first = []
    second = []

    service.request(
        lambda observation: (first.append(observation), first_ready.set()),
        force=True,
    )
    assert first_ready.wait(1.0)
    clock["now"] = 1.0
    service.request(
        lambda observation: (second.append(observation), second_ready.set()),
        force=False,
    )
    assert second_ready.wait(1.0)
    service.close()

    assert first[0].snapshot == snapshot
    assert first[0].reason is None
    assert second[0].snapshot == snapshot
    assert second[0].reason == T3_REASON_FAILED


def test_disabled_t3_service_does_not_start_a_worker_or_read(monkeypatch) -> None:
    calls = []

    def reader(**_kwargs):
        calls.append("read")
        raise AssertionError("disabled T3 observation must not read")

    monkeypatch.setattr(
        t3_compat.threading,
        "Thread",
        lambda **_kwargs: pytest.fail("disabled T3 service started a worker"),
    )
    service = T3SnapshotService(enabled=False, reader=reader)

    observation = service.request(force=True)

    assert calls == []
    assert observation.snapshot is None
    assert observation.attempted_at is None
    assert observation.in_flight is False


def test_failed_observation_marks_last_known_good_statuses_stale() -> None:
    snapshot = SimpleNamespace(
        agent_statuses=lambda *, stale=False: ("stale" if stale else "fresh",)
    )
    observation = t3_compat.T3Observation(
        snapshot=snapshot,
        attempted_at=1.0,
        reason=T3_REASON_FAILED,
        in_flight=False,
    )

    assert observation.statuses == ("stale",)


def test_t3_policy_requires_separate_activity_statistics_opt_in() -> None:
    settings = SimpleNamespace(
        t3code_enabled=True,
        t3code_base_dir="/configured/t3",
        t3code_environment_id="mac-studio",
    )

    observability_only = t3_compat.project_t3_read_only_policy(settings)
    with_statistics = t3_compat.project_t3_read_only_policy(
        settings,
        activity_statistics_enabled=True,
    )
    disabled = t3_compat.project_t3_read_only_policy(
        SimpleNamespace(t3code_enabled=False),
        activity_statistics_enabled=True,
    )

    assert observability_only.should_instantiate is True
    assert observability_only.should_poll is True
    assert observability_only.may_scan_activity_statistics is False
    assert observability_only.base_dir == "/configured/t3"
    assert observability_only.environment_id == "mac-studio"
    assert with_statistics.may_scan_activity_statistics is True
    assert disabled.should_instantiate is False
    assert disabled.should_poll is False
    assert disabled.may_scan_activity_statistics is False


def test_t3_policy_reads_the_separate_persisted_activity_opt_in() -> None:
    policy = t3_compat.project_t3_read_only_policy(
        SimpleNamespace(
            t3code_enabled=True,
            t3code_activity_statistics_enabled=True,
        )
    )

    assert policy.should_poll is True
    assert policy.may_scan_activity_statistics is True


def test_disabled_runtime_admission_closes_service_without_constructing_one() -> None:
    closed = []
    service = SimpleNamespace(close=lambda: closed.append(True))
    policy = t3_compat.project_t3_read_only_policy(
        SimpleNamespace(t3code_enabled=False)
    )

    admitted = t3_compat.reconcile_t3_snapshot_service(
        service,
        previous_policy=None,
        policy=policy,
        factory=lambda **_kwargs: pytest.fail("disabled mode constructed T3 service"),
    )

    assert admitted is None
    assert closed == [True]


def test_enabled_runtime_admission_constructs_the_configured_service_once() -> None:
    policy = t3_compat.project_t3_read_only_policy(
        SimpleNamespace(
            t3code_enabled=True,
            t3code_base_dir="/configured/t3",
            t3code_environment_id="studio",
        )
    )
    calls = []
    service = object()

    admitted = t3_compat.reconcile_t3_snapshot_service(
        None,
        previous_policy=None,
        policy=policy,
        factory=lambda **kwargs: (calls.append(kwargs), service)[1],
    )
    reused = t3_compat.reconcile_t3_snapshot_service(
        admitted,
        previous_policy=policy,
        policy=policy,
        factory=lambda **_kwargs: pytest.fail("unchanged policy rebuilt service"),
    )

    assert admitted is service
    assert reused is service
    assert calls == [
        {
            "enabled": True,
            "base_dir": "/configured/t3",
            "environment_id": "studio",
        }
    ]


def test_disabled_runtime_update_clears_statuses_without_constructing_or_polling() -> None:
    replacements = []
    target = SimpleNamespace(
        monitor=SimpleNamespace(
            replace_external_statuses=lambda source, rows: replacements.append(
                (source, rows)
            )
        )
    )

    policy = t3_compat.update_t3_snapshot_runtime(
        target,
        SimpleNamespace(t3code_enabled=False),
        factory=lambda **_kwargs: pytest.fail("disabled mode constructed T3 service"),
    )

    assert policy.should_instantiate is False
    assert target._t3_snapshot_service is None
    assert replacements == [("t3code", ())]


def test_enabled_runtime_update_polls_the_admitted_service() -> None:
    replacements = []
    requests = []
    service = SimpleNamespace(
        observation=lambda: SimpleNamespace(
            snapshot=None,
            in_flight=True,
            statuses=(),
        ),
        request=lambda: requests.append(True),
        close=lambda: None,
    )
    target = SimpleNamespace(
        monitor=SimpleNamespace(
            replace_external_statuses=lambda source, rows: replacements.append(
                (source, rows)
            )
        )
    )

    policy = t3_compat.update_t3_snapshot_runtime(
        target,
        SimpleNamespace(t3code_enabled=True),
        factory=lambda **_kwargs: service,
    )

    assert policy.should_poll is True
    assert target._t3_snapshot_service is service
    assert requests == [True]
    assert replacements == [("t3code", ())]
