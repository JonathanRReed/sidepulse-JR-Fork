"""Kiro CLI provider: dedicated agent file, managed-file safety, canonical events."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sidepulse.install import install_kiro_hooks, uninstall_kiro_hooks
from sidepulse.models import AgentMode
from sidepulse.providers import (
    KIRO_EVENTS,
    KIRO_MANAGED_DESCRIPTION,
    KIRO_NATIVE_EVENT_NAMES,
    canonical_event_name,
    detect_kiro_config,
    parse_log_line,
)


def test_install_detect_round_trip(tmp_path: Path) -> None:
    config = tmp_path / ".kiro" / "agents" / "sidepulse.json"
    log = tmp_path / "kiro.jsonl"

    result = install_kiro_hooks(log_path=log, config_path=config)

    assert result.changed
    data = json.loads(config.read_text())
    assert data["description"] == KIRO_MANAGED_DESCRIPTION
    assert set(data["hooks"]) == set(KIRO_NATIVE_EVENT_NAMES.values())
    # Tool hooks carry the wildcard matcher Kiro requires; lifecycle hooks
    # must not, or Kiro rejects the agent file.
    assert data["hooks"]["preToolUse"][0]["matcher"] == "*"
    assert "matcher" not in data["hooks"]["agentSpawn"][0]

    detected = detect_kiro_config(tmp_path)
    assert detected.exists and detected.hooks_enabled
    assert detected.hook_events == tuple(sorted(KIRO_EVENTS))
    assert detected.log_paths == (log,)

    second = install_kiro_hooks(log_path=log, config_path=config)
    assert not second.changed


def test_install_refuses_unmanaged_agent_file(tmp_path: Path) -> None:
    config = tmp_path / ".kiro" / "agents" / "sidepulse.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"name": "sidepulse", "description": "mine"}))

    with pytest.raises(ValueError, match="unmanaged"):
        install_kiro_hooks(log_path=tmp_path / "kiro.jsonl", config_path=config)
    assert json.loads(config.read_text())["description"] == "mine"

    removal = uninstall_kiro_hooks(
        log_path=tmp_path / "kiro.jsonl", config_path=config
    )
    assert not removal.changed
    assert config.exists()


def test_uninstall_removes_only_the_managed_file(tmp_path: Path) -> None:
    config = tmp_path / ".kiro" / "agents" / "sidepulse.json"
    install_kiro_hooks(log_path=tmp_path / "kiro.jsonl", config_path=config)

    result = uninstall_kiro_hooks(
        log_path=tmp_path / "kiro.jsonl", config_path=config
    )

    assert result.changed
    assert not config.exists()


def test_kiro_native_names_normalize_to_canonical_events() -> None:
    for canonical, native in KIRO_NATIVE_EVENT_NAMES.items():
        assert canonical_event_name(native) == canonical


def test_kiro_log_lines_reach_the_collector_with_working_semantics() -> None:
    from sidepulse.collector import mode_for_event

    line = json.dumps(
        {
            "hook_event_name": "agentSpawn",
            "session_id": "kiro-session",
            "timestamp": "2026-08-18T12:00:00Z",
        }
    )
    record = parse_log_line("kiro", line)
    assert record is not None
    assert record.provider == "kiro"
    assert record.event_name == "SessionStart"
    assert mode_for_event(record) is AgentMode.IDLE_READY
