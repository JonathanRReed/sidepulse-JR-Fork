from __future__ import annotations

from types import SimpleNamespace

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
    monkeypatch.setattr(hook, "write_hook_status_audit", lambda value: None)
    monkeypatch.setattr(hook.sys, "stdin", SimpleNamespace(read=lambda: "{}"))

    assert hook.hook_log_main("grok", log_path) == 0
    assert hook.hook_log_main("grok", log_path) == 0

    assert writes == [record]
    assert hints == [hint]
    assert hook.hook_dedupe_path(log_path).is_file()
