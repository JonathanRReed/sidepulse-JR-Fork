from __future__ import annotations

import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AgentStatus, HookEvent
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
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    status_audit_record(event, status),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    except OSError:
        pass


def status_audit_record(event: HookEvent, status: AgentStatus | None) -> dict[str, str]:
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
        "message": truncate_preview(event.message or raw_message(event.raw), MESSAGE_PREVIEW_LIMIT),
        "raw_preview": truncate_preview(json_preview(event.raw), RAW_PREVIEW_LIMIT),
    }


def raw_message(raw: dict[str, Any]) -> str:
    for key in ("message", "last_assistant_message", "prompt"):
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
        lines = source.read_text(encoding="utf-8").splitlines()
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_COLUMNS)
        writer.writeheader()
        writer.writerows(records)
    return len(records)


def export_status_audit_html(
    destination: Path,
    *,
    source: Path | None = None,
) -> int:
    records = read_status_audit_records(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(table_row(record) for record in records)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    destination.write_text(
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
        encoding="utf-8",
    )
    return len(records)


def table_row(record: dict[str, str]) -> str:
    cells = []
    for column in AUDIT_COLUMNS:
        css_class = ' class="raw"' if column == "raw_preview" else ""
        cells.append(f"<td{css_class}>{html.escape(record.get(column, ''))}</td>")
    return "<tr>" + "".join(cells) + "</tr>"
