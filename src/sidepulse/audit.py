from __future__ import annotations

import csv
import html
import io
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AgentStatus, HookEvent
from .private_io import (
    append_private_text,
    atomic_private_write,
    read_private_text,
)
from .providers import default_state_dir

STATUS_AUDIT_LOG_NAME = "event-status.jsonl"
RAW_PREVIEW_LIMIT = 2000
MESSAGE_PREVIEW_LIMIT = 240
AUDIT_COLUMNS = (
    "audited_at",
    "logged_at",
    "provider",
    "hook_event",
    "status",
    "status_label",
    "origin",
    "display_name",
    "session_id",
    "agent_id",
    "cwd",
    "tool_name",
    "message",
    "raw_preview",
)


def default_status_audit_log_path(home: Path | None = None) -> Path:
    return default_state_dir(home) / STATUS_AUDIT_LOG_NAME


def append_status_audit_record(
    event: HookEvent,
    status: AgentStatus | None,
    *,
    path: Path | None = None,
) -> None:
    target = path or default_status_audit_log_path()
    try:
        append_private_text(
            target,
            json.dumps(
                status_audit_record(event, status),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n",
        )
        compact_jsonl_file(target)
    except OSError:
        pass


def status_audit_record(event: HookEvent, status: AgentStatus | None) -> dict[str, str]:
    message = ""
    if event.event_name == "Notification":
        message = event.message or raw_message(event.raw)
    return {
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "logged_at": event.logged_at.isoformat(),
        "provider": event.provider,
        "hook_event": event.event_name,
        "status": status.mode.value if status is not None else "",
        "status_label": status.mode_label if status is not None else "",
        "origin": status.origin if status is not None and status.origin else event.origin or "",
        "display_name": status.display_name if status is not None else "",
        "session_id": event.session_id or "",
        "agent_id": event.status_key,
        "cwd": event.cwd or "",
        "tool_name": event.tool_name or "",
        "message": truncate_preview(message, MESSAGE_PREVIEW_LIMIT),
        "raw_preview": "",
    }


def raw_message(raw: dict[str, Any]) -> str:
    for key in ("message", "last_assistant_message"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def json_preview(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def truncate_preview(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def read_status_audit_records(path: Path | None = None) -> list[dict[str, str]]:
    source = path or default_status_audit_log_path()
    try:
        lines = read_private_text(source).splitlines()
    except OSError:
        return []

    records: list[dict[str, str]] = []
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append({column: str(obj.get(column, "")) for column in AUDIT_COLUMNS})
    return records


def export_status_audit_csv(
    destination: Path,
    *,
    source: Path | None = None,
) -> int:
    records = read_status_audit_records(source)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=AUDIT_COLUMNS)
    writer.writeheader()
    writer.writerows(records)
    atomic_private_write(destination, output.getvalue())
    return len(records)


def export_status_audit_html(
    destination: Path,
    *,
    source: Path | None = None,
) -> int:
    records = read_status_audit_records(source)
    body = "\n".join(table_row(record) for record in records)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    atomic_private_write(
        destination,
        "\n".join(
            [
                "<!doctype html>",
                '<meta charset="utf-8">',
                "<title>SidePulse Agent Debug Log</title>",
                "<style>",
                "body{font:14px -apple-system,BlinkMacSystemFont,sans-serif;margin:24px;color:#1d1d1f}",
                "h1{font-size:22px;margin:0 0 4px}",
                "p{color:#6e6e73;margin:0 0 20px}",
                "table{border-collapse:collapse;width:100%;table-layout:fixed}",
                "th,td{border-bottom:1px solid #ddd;padding:7px 8px;text-align:left;vertical-align:top;word-wrap:break-word}",
                "th{position:sticky;top:0;background:#fff;font-weight:600}",
                "td.raw{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}",
                "</style>",
                "<h1>SidePulse Agent Debug Log</h1>",
                f"<p>{len(records)} events exported {html.escape(generated_at)}</p>",
                "<table>",
                "<thead><tr>",
                "".join(f"<th>{html.escape(column)}</th>" for column in AUDIT_COLUMNS),
                "</tr></thead>",
                f"<tbody>{body}</tbody>",
                "</table>",
            ]
        )
        + "\n",
    )
    return len(records)


def table_row(record: dict[str, str]) -> str:
    cells = []
    for column in AUDIT_COLUMNS:
        css_class = ' class="raw"' if column == "raw_preview" else ""
        cells.append(f"<td{css_class}>{html.escape(record.get(column, ''))}</td>")
    return "<tr>" + "".join(cells) + "</tr>"


TRIM_THRESHOLD_BYTES = 5 * 1024 * 1024
TRIM_KEEP_LINES = 4000
# A line COUNT alone cannot bound a file: records vary from ~500 bytes
# (claude) to ~5,800 (codex), so 4,000 lines is anywhere from 1 MB to 22 MB.
# Measured live, three logs sat at exactly 4,000 lines and 23.1 / 10.7 /
# 7.8 MB -- pinned at the line cap and permanently above the byte threshold,
# so compaction re-ran on EVERY hook write: read the whole file, rebuild it,
# atomically replace it, fsync. 46 MB of I/O per event, forever.
#
# So the post-trim size is also bounded, strictly below the threshold that
# triggers a trim. One compaction now actually ends the need to compact.
TRIM_TARGET_BYTES = 2 * 1024 * 1024


# State files left behind by features that no longer exist. Nothing in the
# tree reads or writes these; they are pure residue from a removed debug
# path, and one of them was 17.7 MB on the owner's machine. Matched by exact
# stem so a rename can never turn this into a wildcard delete.
ORPHANED_STATE_STEMS = ("usage-debug-cache.json",)


def remove_orphaned_state_files(state_dir: Path) -> int:
    """Delete state files whose owning feature was removed. Never raises.

    Deliberately narrow: exact names only, only directly inside the state
    directory, and only regular files. A cache with no reader is dead weight,
    but a janitor that guesses is worse than the weight.
    """
    removed = 0
    base = Path(state_dir)
    for stem in ORPHANED_STATE_STEMS:
        for path in (base / stem, *base.glob(f"{stem}.*")):
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
    return removed


def trim_process_log(
    path: Path,
    *,
    max_bytes: int = TRIM_THRESHOLD_BYTES,
    target_bytes: int = TRIM_TARGET_BYTES,
) -> bool:
    """Bound a log that a live process holds open, keeping the same inode.

    stdout/stderr are redirected by launchd, which opens these paths once and
    holds an O_APPEND descriptor for the life of the process. Replacing the
    file atomically -- the way every other trim here works -- would leave the
    app writing into an unlinked inode: the log on disk would freeze, the
    output would go nowhere, and nothing would report it.

    So this truncates in place instead. Racing an append can cost a few bytes
    of a single line; a silently dead log costs every line after it.
    """
    target = Path(path)
    try:
        info = target.lstat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size <= max_bytes:
        return False
    try:
        descriptor = os.open(target, os.O_RDWR | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        size = os.lseek(descriptor, 0, os.SEEK_END)
        start = max(0, size - target_bytes)
        os.lseek(descriptor, start, os.SEEK_SET)
        tail = os.read(descriptor, target_bytes)
        if start > 0:
            # Drop the partial first line left by seeking into the middle.
            newline = tail.find(b"\n")
            tail = tail[newline + 1 :] if newline >= 0 else b""
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, tail)
        os.ftruncate(descriptor, len(tail))
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return True


def trim_oversized_process_logs(state_dir: Path) -> int:
    """Bound the app's own stdout/stderr logs. Never raises.

    Measured here: 8.6 MB of stderr (one hour of a since-fixed hook crash
    loop) and 7.1 MB of stdout, neither of which anything ever bounded.
    """
    trimmed = 0
    try:
        candidates = sorted(Path(state_dir).glob("*.log"))
    except OSError:
        return 0
    for path in candidates:
        try:
            if trim_process_log(path):
                trimmed += 1
        except OSError:
            continue
    return trimmed


def trim_oversized_logs(state_dir: Path) -> int:
    """Rotates every .jsonl under the state dir that has grown past
    TRIM_THRESHOLD_BYTES down to its last TRIM_KEEP_LINES lines
    (atomic replace). Hook logs and the event ledger appended forever
    with no cap -- a long-lived install accumulated unbounded disk and
    made every export read the whole history into memory. Returns how
    many files were trimmed; never raises."""
    trimmed = 0
    try:
        candidates = list(Path(state_dir).rglob("*.jsonl"))
    except OSError:
        return 0
    for path in candidates:
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            if compact_jsonl_file(path):
                trimmed += 1
        except OSError:
            continue
    return trimmed


def compact_jsonl_file(path: Path) -> bool:
    """Atomically bound one active JSONL file to its newest useful tail."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size <= TRIM_THRESHOLD_BYTES:
        return False
    lines = read_private_text(path, errors="replace").splitlines(keepends=True)
    atomic_private_write(path, "".join(_bounded_tail(lines)))
    return True


def _bounded_tail(lines: list[str]) -> list[str]:
    """Newest lines within BOTH the line cap and the byte budget.

    Always keeps at least one line: an empty log would read as "no history"
    to the collector rather than "history was trimmed".
    """
    kept = lines[-TRIM_KEEP_LINES:]
    if not kept:
        return kept
    total = 0
    first = len(kept) - 1
    for index in range(len(kept) - 1, -1, -1):
        total += len(kept[index].encode("utf-8", errors="replace"))
        if total > TRIM_TARGET_BYTES and index < len(kept) - 1:
            break
        first = index
    return kept[first:]
