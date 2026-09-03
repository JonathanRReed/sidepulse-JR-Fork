"""Read-only T3 Code compatibility through its documented SQLite projection.

T3 remains the orchestrator and credential owner. SidePulse opens the local
projection database in query-only mode, preserves the underlying provider, and
publishes immutable supplemental statuses to its canonical monitor.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .provider_facts import SourceKey, WorkIdentifier, WorkKey
from .models import AgentMode, AgentStatus, parse_datetime

T3_DATABASE_RELATIVE_PATH = Path("userdata") / "state.sqlite"
T3_SQLITE_TIMEOUT_SECONDS = 0.75
T3_POLL_INTERVAL_SECONDS = 2.0
T3_MAX_THREADS = 512
T3_MAX_TITLE_LENGTH = 160
T3_SOURCE_COMMIT = "bab4b6f02b8bdaf15fd32636a97f69ff657cec50"
T3_MINIMUM_VERSION = "0.0.33"
T3_MAXIMUM_TESTED_VERSION = "0.0.33"
T3_REASON_MISSING = "t3_database_missing"
T3_REASON_UNSUPPORTED = "t3_schema_unsupported"
T3_REASON_BUSY = "t3_database_busy"
T3_REASON_FAILED = "t3_snapshot_failed"

_REQUIRED_COLUMNS = {
    "projection_projects": frozenset(
        {
            "project_id",
            "title",
            "workspace_root",
            "created_at",
            "updated_at",
            "deleted_at",
        }
    ),
    "projection_threads": frozenset(
        {
            "thread_id",
            "project_id",
            "title",
            "model_selection_json",
            "runtime_mode",
            "interaction_mode",
            "branch",
            "worktree_path",
            "latest_turn_id",
            "created_at",
            "updated_at",
            "archived_at",
            "settled_at",
            "pending_approval_count",
            "pending_user_input_count",
            "has_actionable_proposed_plan",
            "deleted_at",
        }
    ),
    "projection_thread_sessions": frozenset(
        {
            "thread_id",
            "status",
            "provider_name",
            "provider_instance_id",
            "provider_thread_id",
            "runtime_mode",
            "active_turn_id",
            "last_error",
            "updated_at",
        }
    ),
}
T3_PROTOCOL_FINGERPRINT = "sha256:" + hashlib.sha256(
    json.dumps(
        {table: sorted(columns) for table, columns in sorted(_REQUIRED_COLUMNS.items())},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()

_PROVIDER_ALIASES = {
    "codex": "codex",
    "openai": "codex",
    "claude": "claude",
    "anthropic": "claude",
    "cursor": "cursor",
    "grok": "grok",
    "xai": "grok",
    "opencode": "opencode",
    "open-code": "opencode",
    "antigravity": "antigravity",
    "gemini": "antigravity",
    "devin": "devin",
}
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]{0,255}\Z")


@dataclass(frozen=True, slots=True)
class T3ThreadView:
    thread_id: str
    project_id: str
    project_title: str
    thread_title: str
    workspace_root: str
    branch: str | None
    worktree_path: str | None
    provider: str
    provider_instance: str | None
    provider_thread_id: str | None
    model: str | None
    reasoning_effort: str | None
    runtime_mode: str
    interaction_mode: str
    session_status: str | None
    active_turn_id: str | None
    latest_turn_id: str | None
    pending_approval_count: int
    pending_user_input_count: int
    has_actionable_plan: bool
    last_error_present: bool
    updated_at: datetime
    deep_link: str | None

    @property
    def needs_user(self) -> bool:
        return bool(
            self.pending_approval_count
            or self.pending_user_input_count
            or self.has_actionable_plan
        )

    def to_agent_status(self, *, stale: bool = False) -> AgentStatus:
        mode, event_name, message = _agent_state_for_thread(self)
        session_id = _safe_session_identifier(
            self.provider_thread_id or self.thread_id
        )
        origin = _bounded_text(
            f"T3 Code · {self.project_title}"
            + (f" · {self.branch}" if self.branch else ""),
            T3_MAX_TITLE_LENGTH,
        )
        work_key = WorkKey(
            SourceKey(self.provider, "t3code", "default", "threads"),
            WorkIdentifier(session_id),
        )
        return AgentStatus(
            provider=self.provider,
            agent_id=f"{self.provider}:session:{session_id}",
            display_name=_bounded_text(self.thread_title, T3_MAX_TITLE_LENGTH),
            mode=mode,
            updated_at=self.updated_at,
            event_name=event_name,
            session_id=session_id,
            cwd=self.worktree_path or self.workspace_root,
            tool_name=self.model,
            message=message,
            origin=origin,
            stale=stale,
            work_key=work_key,
        )


@dataclass(frozen=True, slots=True)
class T3Snapshot:
    observed_at: datetime
    database_path: Path
    schema_fingerprint: str
    sqlite_user_version: int
    threads: tuple[T3ThreadView, ...]
    compatible: bool = True
    truncated: bool = False
    reason: str | None = None

    def agent_statuses(self, *, stale: bool = False) -> tuple[AgentStatus, ...]:
        return tuple(thread.to_agent_status(stale=stale) for thread in self.threads)

    @property
    def active_count(self) -> int:
        return sum(
            status.mode
            in {
                AgentMode.WORKING,
                AgentMode.TOOL_RUNNING,
                AgentMode.WAITING_FOR_INPUT,
                AgentMode.LONG_TASK_PROGRESS,
                AgentMode.BLOCKED_ERROR,
            }
            for status in self.agent_statuses()
        )

    @property
    def needs_user_count(self) -> int:
        return sum(thread.needs_user for thread in self.threads)


@dataclass(frozen=True, slots=True)
class T3Observation:
    snapshot: T3Snapshot | None
    attempted_at: float | None
    reason: str | None
    in_flight: bool

    @property
    def available(self) -> bool:
        return self.snapshot is not None and self.snapshot.compatible

    @property
    def statuses(self) -> tuple[AgentStatus, ...]:
        if self.snapshot is None:
            return ()
        return self.snapshot.agent_statuses(stale=self.reason is not None)


def default_t3_base_dir() -> Path:
    configured = os.environ.get("T3_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".t3"


def t3_database_path(base_dir: Path | str | None = None) -> Path:
    base = Path(base_dir).expanduser() if base_dir is not None else default_t3_base_dir()
    return base.absolute() / T3_DATABASE_RELATIVE_PATH


def _bounded_text(value: object, maximum: int) -> str:
    text = " ".join(
        "".join(character for character in str(value) if character.isprintable()).split()
    )
    return (text or "T3 Code session")[:maximum]


def _safe_session_identifier(value: str) -> str:
    text = str(value).strip()
    if _SAFE_IDENTIFIER.fullmatch(text) is not None:
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return f"t3-{digest[:32]}"


def _normalize_provider(*values: object) -> str:
    text = " ".join(str(value or "").casefold() for value in values)
    tokens = re.split(r"[^a-z0-9]+", text)
    for token in tokens:
        if token in _PROVIDER_ALIASES:
            return _PROVIDER_ALIASES[token]
    for alias, provider in _PROVIDER_ALIASES.items():
        if alias in text:
            return provider
    return "other"


def _parse_model_selection(raw: object) -> tuple[str | None, str | None, str | None]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        value = None
    if not isinstance(value, dict):
        return None, None, None
    instance = value.get("instanceId")
    model = value.get("model")
    options = value.get("options")
    reasoning = None
    if isinstance(options, dict):
        for key in ("reasoningEffort", "reasoning_effort", "effort"):
            candidate = options.get(key)
            if isinstance(candidate, str) and candidate.strip():
                reasoning = candidate.strip()[:64]
                break
    return (
        instance.strip()[:128]
        if isinstance(instance, str) and instance.strip()
        else None,
        model.strip()[:128] if isinstance(model, str) and model.strip() else None,
        reasoning,
    )


def _nonnegative_int(value: object) -> int:
    return int(value) if type(value) is int and value >= 0 else 0


def _latest_time(*values: object) -> datetime:
    parsed = [
        parse_datetime(value, fallback=datetime.fromtimestamp(0, timezone.utc))
        for value in values
        if isinstance(value, (str, datetime)) and value
    ]
    return max(parsed, default=datetime.now(timezone.utc))


def _deep_link(environment_id: str | None, thread_id: str) -> str | None:
    if not environment_id:
        return None
    return (
        "t3code://threads/"
        f"{quote(environment_id, safe='')}/{quote(thread_id, safe='')}"
    )


def _agent_state_for_thread(
    thread: T3ThreadView,
) -> tuple[AgentMode, str, str | None]:
    if thread.pending_approval_count > 0:
        return (
            AgentMode.WAITING_FOR_INPUT,
            "PermissionRequest",
            "Approval needed in T3 Code",
        )
    if thread.pending_user_input_count > 0:
        return (
            AgentMode.WAITING_FOR_INPUT,
            "Notification",
            "Input needed in T3 Code",
        )
    if thread.has_actionable_plan:
        return (
            AgentMode.WAITING_FOR_INPUT,
            "Notification",
            "Plan ready in T3 Code",
        )
    status = (thread.session_status or "").casefold()
    if status == "error":
        if thread.updated_at is not None:
            now_dt = datetime.now(timezone.utc)
            th_dt = (
                thread.updated_at
                if thread.updated_at.tzinfo is not None
                else thread.updated_at.replace(tzinfo=timezone.utc)
            )
            if (now_dt - th_dt).total_seconds() > 300:
                return AgentMode.IDLE_READY, "SessionStart", None
        return AgentMode.BLOCKED_ERROR, "StopFailure", "T3 Code session failed"
    if status in {"starting", "running"}:
        return AgentMode.WORKING, "UserPromptSubmit", None
    if status in {"ready", "idle"}:
        return AgentMode.COMPLETED, "Stop", "Review the completed T3 Code task"
    if status == "interrupted":
        return AgentMode.UNKNOWN, "StopInterrupted", "T3 Code session interrupted"
    if status == "stopped" and (thread.active_turn_id or thread.latest_turn_id):
        return AgentMode.COMPLETED, "Stop", "Review the completed T3 Code task"
    return AgentMode.IDLE_READY, "SessionStart", None


def _table_columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return frozenset(str(row[1]) for row in rows)


def _actual_schema_fingerprint(connection: sqlite3.Connection) -> str:
    document = {
        table: sorted(_table_columns(connection, table))
        for table in sorted(_REQUIRED_COLUMNS)
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _open_read_only(
    path: Path,
    *,
    connector: Callable[..., sqlite3.Connection] = sqlite3.connect,
) -> sqlite3.Connection:
    connection = connector(
        f"file:{quote(str(path), safe='/')}?mode=ro",
        uri=True,
        timeout=T3_SQLITE_TIMEOUT_SECONDS,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute(
        f"PRAGMA busy_timeout = {int(T3_SQLITE_TIMEOUT_SECONDS * 1000)}"
    )
    return connection


def read_t3_snapshot(
    *,
    base_dir: Path | str | None = None,
    environment_id: str | None = None,
    connector: Callable[..., sqlite3.Connection] = sqlite3.connect,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> T3Snapshot:
    database = t3_database_path(base_dir)
    if not database.is_file():
        raise FileNotFoundError(database)
    connection = _open_read_only(database, connector=connector)
    try:
        columns = {
            table: _table_columns(connection, table) for table in _REQUIRED_COLUMNS
        }
        if any(
            not required.issubset(columns.get(table, frozenset()))
            for table, required in _REQUIRED_COLUMNS.items()
        ):
            return T3Snapshot(
                observed_at=now(),
                database_path=database,
                schema_fingerprint=_actual_schema_fingerprint(connection),
                sqlite_user_version=int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                ),
                threads=(),
                compatible=False,
                reason=T3_REASON_UNSUPPORTED,
            )
        rows = connection.execute(
            """
            SELECT
              threads.thread_id,
              threads.project_id,
              projects.title AS project_title,
              threads.title AS thread_title,
              projects.workspace_root,
              threads.branch,
              threads.worktree_path,
              threads.model_selection_json,
              threads.runtime_mode AS thread_runtime_mode,
              threads.interaction_mode,
              threads.latest_turn_id,
              threads.updated_at AS thread_updated_at,
              threads.settled_at,
              threads.pending_approval_count,
              threads.pending_user_input_count,
              threads.has_actionable_proposed_plan,
              sessions.status AS session_status,
              sessions.provider_name,
              sessions.provider_instance_id,
              sessions.provider_thread_id,
              sessions.runtime_mode AS session_runtime_mode,
              sessions.active_turn_id,
              sessions.last_error,
              sessions.updated_at AS session_updated_at
            FROM projection_threads AS threads
            INNER JOIN projection_projects AS projects
              ON projects.project_id = threads.project_id
            LEFT JOIN projection_thread_sessions AS sessions
              ON sessions.thread_id = threads.thread_id
            WHERE threads.deleted_at IS NULL
              AND projects.deleted_at IS NULL
              AND threads.archived_at IS NULL
            ORDER BY threads.created_at ASC, threads.thread_id ASC
            LIMIT ?
            """,
            (T3_MAX_THREADS + 1,),
        ).fetchall()
        truncated = len(rows) > T3_MAX_THREADS
        rows = rows[:T3_MAX_THREADS]
        threads = []
        for row in rows:
            instance_from_model, model, reasoning = _parse_model_selection(
                row["model_selection_json"]
            )
            provider_instance = row["provider_instance_id"] or instance_from_model
            provider = _normalize_provider(
                row["provider_name"],
                provider_instance,
                model,
            )
            threads.append(
                T3ThreadView(
                    thread_id=str(row["thread_id"]),
                    project_id=str(row["project_id"]),
                    project_title=_bounded_text(
                        row["project_title"],
                        T3_MAX_TITLE_LENGTH,
                    ),
                    thread_title=_bounded_text(
                        row["thread_title"],
                        T3_MAX_TITLE_LENGTH,
                    ),
                    workspace_root=str(row["workspace_root"]),
                    branch=str(row["branch"]) if row["branch"] else None,
                    worktree_path=(
                        str(row["worktree_path"]) if row["worktree_path"] else None
                    ),
                    provider=provider,
                    provider_instance=(
                        str(provider_instance)[:128] if provider_instance else None
                    ),
                    provider_thread_id=(
                        str(row["provider_thread_id"])
                        if row["provider_thread_id"]
                        else None
                    ),
                    model=model,
                    reasoning_effort=reasoning,
                    runtime_mode=str(
                        row["session_runtime_mode"]
                        or row["thread_runtime_mode"]
                        or "unknown"
                    ),
                    interaction_mode=str(row["interaction_mode"] or "default"),
                    session_status=(
                        str(row["session_status"]) if row["session_status"] else None
                    ),
                    active_turn_id=(
                        str(row["active_turn_id"])
                        if row["active_turn_id"]
                        else None
                    ),
                    latest_turn_id=(
                        str(row["latest_turn_id"])
                        if row["latest_turn_id"]
                        else None
                    ),
                    pending_approval_count=_nonnegative_int(
                        row["pending_approval_count"]
                    ),
                    pending_user_input_count=_nonnegative_int(
                        row["pending_user_input_count"]
                    ),
                    has_actionable_plan=bool(row["has_actionable_proposed_plan"]),
                    last_error_present=bool(row["last_error"]),
                    updated_at=_latest_time(
                        row["thread_updated_at"],
                        row["session_updated_at"],
                        row["settled_at"],
                    ),
                    deep_link=_deep_link(environment_id, str(row["thread_id"])),
                )
            )
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        return T3Snapshot(
            observed_at=now(),
            database_path=database,
            schema_fingerprint=_actual_schema_fingerprint(connection),
            sqlite_user_version=user_version,
            threads=tuple(threads),
            truncated=truncated,
        )
    finally:
        connection.close()


class T3SnapshotService:
    """One daemon worker, latest-known-good state, and bounded polling."""

    def __init__(
        self,
        *,
        base_dir: Path | str | None = None,
        environment_id: str | None = None,
        reader: Callable[..., T3Snapshot] = read_t3_snapshot,
        monotonic: Callable[[], float] = time.monotonic,
        minimum_interval: float = T3_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._base_dir = base_dir
        self._environment_id = environment_id
        self._reader = reader
        self._monotonic = monotonic
        self._minimum_interval = max(0.25, float(minimum_interval))
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = False
        self._in_flight = False
        self._attempted_at: float | None = None
        self._snapshot: T3Snapshot | None = None
        self._reason: str | None = None

    def observation(self) -> T3Observation:
        with self._lock:
            return self._observation_locked()

    def request(
        self,
        callback: Callable[[T3Observation], None] | None = None,
        *,
        force: bool = False,
    ) -> T3Observation:
        now = self._monotonic()
        with self._lock:
            if self._closed:
                return self._observation_locked()
            due = (
                force
                or self._attempted_at is None
                or now - self._attempted_at >= self._minimum_interval
            )
            if not due or self._in_flight:
                return self._observation_locked()
            self._generation += 1
            generation = self._generation
            self._in_flight = True
            self._attempted_at = now
            threading.Thread(
                target=self._run,
                args=(generation, callback),
                name="SidePulseT3Compatibility",
                daemon=True,
            ).start()
            return self._observation_locked()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1

    def _observation_locked(self) -> T3Observation:
        return T3Observation(
            snapshot=self._snapshot,
            attempted_at=self._attempted_at,
            reason=self._reason,
            in_flight=self._in_flight,
        )

    def _run(
        self,
        generation: int,
        callback: Callable[[T3Observation], None] | None,
    ) -> None:
        snapshot = None
        reason = None
        try:
            snapshot = self._reader(
                base_dir=self._base_dir,
                environment_id=self._environment_id,
            )
            if not snapshot.compatible:
                reason = snapshot.reason or T3_REASON_UNSUPPORTED
        except FileNotFoundError:
            reason = T3_REASON_MISSING
        except sqlite3.OperationalError as exc:
            reason = (
                T3_REASON_BUSY
                if "locked" in str(exc).casefold()
                or "busy" in str(exc).casefold()
                else T3_REASON_FAILED
            )
        except Exception:
            reason = T3_REASON_FAILED

        observation = None
        with self._lock:
            if self._closed or generation != self._generation:
                self._in_flight = False
                return
            if snapshot is not None and snapshot.compatible:
                self._snapshot = snapshot
                self._reason = None
            else:
                self._reason = reason or T3_REASON_FAILED
            self._in_flight = False
            observation = self._observation_locked()
        if callback is not None and observation is not None:
            try:
                callback(observation)
            except Exception:
                pass


__all__ = [
    "T3_DATABASE_RELATIVE_PATH",
    "T3_MAXIMUM_TESTED_VERSION",
    "T3_MINIMUM_VERSION",
    "T3_PROTOCOL_FINGERPRINT",
    "T3_SOURCE_COMMIT",
    "T3Observation",
    "T3Snapshot",
    "T3SnapshotService",
    "T3ThreadView",
    "default_t3_base_dir",
    "read_t3_snapshot",
    "t3_database_path",
]
