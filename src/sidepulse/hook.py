from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import audit
from .hook_dedupe import HookEventDeduplicator
from .ipc import ProviderRefreshHint, send_refresh_hint
from .origin import annotate_payload_with_origin
from .private_io import append_private_text, redact_event_payload
from .provider_adapters import (
    InertProviderRecord,
    NormalizedProviderRecord,
    minimize_hook_event,
    normalized_provider_record_to_payload,
    provider_facts_for_record,
)
from .providers import (
    NegotiatedProviderSource,
    detect_log_path,
    infer_provider_from_payload,
    negotiated_provider_sources,
    parse_log_line,
)


def format_hook_payload(
    provider: str,
    payload_text: str,
    *,
    logged_at: str | None = None,
    include_origin: bool = True,
) -> dict[str, Any]:
    timestamp = logged_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        payload: Any = json.loads(payload_text or "{}")
    except json.JSONDecodeError as exc:
        payload = {
            "hook_event_name": "ParseError",
            "raw": payload_text,
            "parse_error": str(exc),
        }

    if include_origin and isinstance(payload, dict):
        payload = annotate_payload_with_origin(provider, payload)
    if provider == "codex":
        return {"logged_at": timestamp, "event": payload}
    if isinstance(payload, dict):
        line = dict(payload)
        line["logged_at"] = line.get("logged_at") or timestamp
        return line
    return {"logged_at": timestamp, "event": payload}


def write_hook_line(log_path: Path, line: dict[str, Any]) -> None:
    log_path = log_path.expanduser()
    safe_line = redact_event_payload(line)
    append_private_text(
        log_path,
        json.dumps(safe_line, separators=(",", ":"), ensure_ascii=False) + "\n",
    )
    audit.compact_jsonl_file(log_path)


def write_hook_payload(provider: str, log_path: Path, payload_text: str) -> None:
    line = format_hook_payload(provider, payload_text)
    write_hook_line(log_path, line)


def routed_hook_payload(
    provider: str,
    log_path: Path,
    payload_text: str,
) -> tuple[str, Path, dict[str, Any]]:
    line = format_hook_payload(provider, payload_text, include_origin=False)
    actual_provider = infer_provider_from_hook_line(provider, line)
    line = annotate_hook_line(actual_provider, line)
    actual_log_path = log_path
    if actual_provider != provider:
        actual_log_path = detect_log_path(actual_provider)
    return actual_provider, actual_log_path, line


def annotate_hook_line(provider: str, line: dict[str, Any]) -> dict[str, Any]:
    if isinstance(line.get("event"), dict):
        annotated = dict(line)
        annotated["event"] = annotate_payload_with_origin(provider, line["event"])
        return annotated
    return annotate_payload_with_origin(provider, line)


def infer_provider_from_hook_line(provider: str, line: dict[str, Any]) -> str:
    raw = line.get("event") if provider == "codex" else line
    if isinstance(raw, dict):
        return infer_provider_from_payload(provider, raw)
    return provider


def write_hook_status_audit(
    _record: NormalizedProviderRecord | InertProviderRecord,
) -> None:
    """Compatibility no-op until history consumes canonical semantic events."""


def _hook_source(provider: str) -> NegotiatedProviderSource | None:
    return next(
        (
            source
            for source in negotiated_provider_sources()
            if source.source_key.provider_id == provider
            and source.source_key.adapter_id == "hooks"
            and source.source_key.capability_id == "live_agent_events"
            and source.observation_invocation_allowed
        ),
        None,
    )


def _normalized_hook_record(
    provider: str,
    line: dict[str, Any],
) -> NormalizedProviderRecord | InertProviderRecord | None:
    source = _hook_source(provider)
    if source is None:
        return None
    parsed = parse_log_line(
        provider,
        json.dumps(line, separators=(",", ":"), ensure_ascii=False),
    )
    if parsed is None:
        return None
    return minimize_hook_event(
        parsed,
        source_key=source.source_key,
        contract=source.contract,
        observation_authority=source.registration.observation_authority,
    )


def write_normalized_hook_record(
    log_path: Path,
    record: NormalizedProviderRecord | InertProviderRecord,
) -> None:
    payload = normalized_provider_record_to_payload(record)
    append_private_text(
        log_path.expanduser(),
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
    )
    audit.compact_jsonl_file(log_path.expanduser())


def hook_dedupe_path(log_path: Path) -> Path:
    target = Path(log_path).expanduser()
    return target.with_name(f"{target.name}.dedupe.json")


def _refresh_hint_for_record(
    provider: str,
    record: NormalizedProviderRecord | InertProviderRecord,
) -> ProviderRefreshHint | None:
    source = _hook_source(provider)
    if source is None or source.source_key != record.source_key:
        return None
    event_token = (
        record.event_token
        if type(record) is NormalizedProviderRecord
        else provider_facts_for_record(
            record,
            contract=source.contract,
            observation_authority=source.registration.observation_authority,
            observed_at_epoch=record.occurred_at_epoch,
        ).watermark.event_token
    )
    return ProviderRefreshHint(record.source_key, event_token)


def hook_log_main(provider: str, log_path: Path) -> int:
    try:
        actual_provider, actual_log_path, line = routed_hook_payload(
            provider,
            log_path,
            sys.stdin.read(),
        )
        record = _normalized_hook_record(actual_provider, line)
        if record is None:
            return 0
        hint = _refresh_hint_for_record(actual_provider, record)
        if hint is None:
            write_normalized_hook_record(actual_log_path, record)
        else:
            deduplicator = HookEventDeduplicator(hook_dedupe_path(actual_log_path))
            written = deduplicator.run_once(
                hint.event_token.value,
                lambda: write_normalized_hook_record(actual_log_path, record),
            )
            if not written:
                return 0
            send_refresh_hint(
                hint,
                event_name=str(line.get("hook_event_name") or "") or None,
            )
        write_hook_status_audit(record)
    except Exception:
        return 0
    finally:
        if provider == "cursor":
            try:
                sys.stdout.write("{}\n")
                sys.stdout.flush()
            except Exception:
                pass
    return 0
