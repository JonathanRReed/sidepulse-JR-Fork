from __future__ import annotations

from types import SimpleNamespace

import pytest

from sidepulse import hook


def test_hook_log_main_appends_and_notifies_once_per_event_token(
    tmp_path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "grok.jsonl"
    record = object()
    hint = SimpleNamespace(event_token=SimpleNamespace(value="same-event"))
    writes: list[object] = []
    hints: list[object] = []

    monkeypatch.setattr(
        hook,
        "routed_hook_payload",
        lambda provider, configured_path, payload: ("grok", log_path, {}),
    )
    monkeypatch.setattr(hook, "_normalized_hook_record", lambda provider, line: record)
    monkeypatch.setattr(hook, "_refresh_hint_for_record", lambda provider, value: hint)
    monkeypatch.setattr(
        hook,
        "write_normalized_hook_record",
        lambda path, value: writes.append(value),
    )
    monkeypatch.setattr(
        hook,
        "send_refresh_hint",
        lambda value, event_name=None: hints.append(value),
    )
    monkeypatch.setattr(hook.sys, "stdin", SimpleNamespace(read=lambda: "{}"))

    assert hook.hook_log_main("grok", log_path) == 0
    assert hook.hook_log_main("grok", log_path) == 0

    assert writes == [record]
    assert hints == [hint]
    assert hook.hook_dedupe_path(log_path).is_file()


def test_process_hook_payload_owns_no_stdio_and_reports_written(
    tmp_path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "grok.jsonl"
    record = object()
    hint = SimpleNamespace(event_token=SimpleNamespace(value="event"))
    writes: list[object] = []
    hints: list[object] = []
    monkeypatch.setattr(
        hook,
        "routed_hook_payload",
        lambda provider, configured_path, payload: ("grok", log_path, {}),
    )
    monkeypatch.setattr(hook, "_normalized_hook_record", lambda provider, line: record)
    monkeypatch.setattr(hook, "_refresh_hint_for_record", lambda provider, value: hint)
    monkeypatch.setattr(
        hook,
        "write_normalized_hook_record",
        lambda path, value: writes.append(value),
    )
    monkeypatch.setattr(
        hook,
        "send_refresh_hint",
        lambda value, event_name=None: hints.append(value),
    )
    monkeypatch.setattr(
        hook.sys,
        "stdin",
        SimpleNamespace(read=lambda: (_ for _ in ()).throw(AssertionError("stdin read"))),
    )

    outcome = hook.process_hook_payload("grok", log_path, "{}")

    assert outcome is hook.HookProcessingOutcome.WRITTEN
    assert writes == [record]
    assert hints == [hint]


def test_process_hook_payload_applies_in_process_hint_before_return(
    tmp_path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "grok.jsonl"
    record = object()
    hint = SimpleNamespace(event_token=SimpleNamespace(value="event"))
    lifecycle: list[str] = []
    monkeypatch.setattr(
        hook,
        "routed_hook_payload",
        lambda provider, configured_path, payload: ("grok", log_path, {}),
    )
    monkeypatch.setattr(hook, "_normalized_hook_record", lambda provider, line: record)
    monkeypatch.setattr(hook, "_refresh_hint_for_record", lambda provider, value: hint)
    monkeypatch.setattr(
        hook,
        "write_normalized_hook_record",
        lambda path, value: lifecycle.append("written"),
    )
    monkeypatch.setattr(
        hook,
        "send_refresh_hint",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("app-owned ingress must not use the hint socket")
        ),
    )

    outcome = hook.process_hook_payload(
        "grok",
        log_path,
        "{}",
        refresh_hint_handler=lambda value: lifecycle.append(
            "applied" if value is hint else "wrong-hint"
        ),
    )
    lifecycle.append("returned")

    assert outcome is hook.HookProcessingOutcome.WRITTEN
    assert lifecycle == ["written", "applied", "returned"]


def test_process_hook_payload_propagates_failure_for_ingress_receipt(
    tmp_path,
    monkeypatch,
) -> None:
    record = object()
    monkeypatch.setattr(
        hook,
        "routed_hook_payload",
        lambda provider, configured_path, payload: ("claude", configured_path, {}),
    )
    monkeypatch.setattr(hook, "_normalized_hook_record", lambda provider, line: record)
    monkeypatch.setattr(hook, "_refresh_hint_for_record", lambda provider, value: None)
    monkeypatch.setattr(
        hook,
        "write_normalized_hook_record",
        lambda path, value: (_ for _ in ()).throw(OSError("private detail")),
    )

    with pytest.raises(OSError, match="private detail"):
        hook.process_hook_payload("claude", tmp_path / "claude.jsonl", "{}")
