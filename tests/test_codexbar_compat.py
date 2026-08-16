from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from sidepulse import codexbar_compat
from sidepulse.codexbar_compat import (
    CODEXBAR_REASON_SCHEMA,
    CodexBarClient,
    CodexBarCompatibilityError,
    CodexBarServeSupervisor,
    _CommandResult,
    parse_codexbar_snapshot,
    read_codexbar_dashboard,
    run_bounded_command,
)


def _snapshot_document() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "generatedAt": "2026-08-16T12:00:00Z",
        "staleAfterSeconds": 180,
        "host": {
            "codexBarVersion": "0.50.0",
            "refreshIntervalSeconds": 60,
        },
        "providers": [
            {
                "id": "codex",
                "name": "Codex",
                "enabled": True,
                "source": "oauth",
                "status": {
                    "level": "ok",
                    "label": "Operational",
                    "updatedAt": "2026-08-16T11:59:00Z",
                },
                "identity": {
                    "accountEmail": "redacted@example.com",
                    "plan": "Pro 20x",
                },
                "windows": [
                    {
                        "kind": "session",
                        "label": "Session",
                        "usedPercent": 28,
                        "remainingPercent": 72,
                        "resetAt": "2026-08-16T17:15:00Z",
                    }
                ],
                "credits": {"remaining": 112.4, "unit": "credits"},
                "cost": {"todayUSD": 1.04, "last30DaysUSD": 18.22},
                "display": {
                    "accentColor": "#49A3B0",
                    "sortKey": 0,
                    "priority": "normal",
                },
                "error": None,
                "updatedAt": "2026-08-16T11:59:45Z",
            },
            {
                "id": "claude",
                "name": "Claude",
                "enabled": True,
                "source": "web",
                "identity": None,
                "windows": [],
                "accounts": [
                    {
                        "id": "claude-swap:2",
                        "label": "redacted@example.com",
                        "active": True,
                        "identity": {
                            "accountEmail": "redacted@example.com",
                            "plan": None,
                        },
                        "windows": [
                            {
                                "kind": "weekly",
                                "label": "Weekly",
                                "usedPercent": 91,
                                "remainingPercent": 9,
                                "resetAt": "2026-08-18T12:00:00Z",
                            }
                        ],
                        "error": None,
                        "updatedAt": "2026-08-16T12:00:00Z",
                    }
                ],
                "display": {"sortKey": 1, "priority": "normal"},
                "error": None,
                "updatedAt": "2026-08-16T12:00:00Z",
            },
        ],
    }


def _snapshot_bytes() -> bytes:
    return json.dumps(_snapshot_document()).encode("utf-8")


def test_dashboard_v1_parser_preserves_rows_accounts_and_windows() -> None:
    snapshot = parse_codexbar_snapshot(
        _snapshot_bytes(),
        connection_mode="serve",
    )

    assert snapshot.codexbar_version == "0.50.0"
    assert snapshot.connection_mode == "serve"
    assert [row.provider_id for row in snapshot.providers] == ["codex", "claude"]
    assert snapshot.providers[0].identity is not None
    assert snapshot.providers[0].identity.plan == "Pro 20x"
    assert snapshot.providers[0].windows[0].remaining_percent == 72.0
    assert snapshot.providers[1].accounts[0].active is True
    assert snapshot.providers[1].accounts[0].windows[0].remaining_percent == 9.0
    assert snapshot.most_constrained is not None
    assert snapshot.most_constrained[0].provider_id == "claude"
    assert snapshot.most_constrained[1] == 9.0


def test_dashboard_parser_rejects_unknown_schema_and_duplicate_keys() -> None:
    document = _snapshot_document()
    document["schemaVersion"] = 2
    with pytest.raises(CodexBarCompatibilityError) as unsupported:
        parse_codexbar_snapshot(json.dumps(document), connection_mode="dashboard")
    assert unsupported.value.reason == CODEXBAR_REASON_SCHEMA

    with pytest.raises(CodexBarCompatibilityError) as duplicate:
        parse_codexbar_snapshot(
            '{"schemaVersion":1,"schemaVersion":1}',
            connection_mode="dashboard",
        )
    assert duplicate.value.reason == CODEXBAR_REASON_SCHEMA


def test_one_shot_dashboard_uses_only_documented_noninteractive_flags(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "codexbar"
    binary.write_text("binary", encoding="utf-8")
    binary.chmod(0o755)
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return _CommandResult(0, _snapshot_bytes(), b"")

    snapshot = read_codexbar_dashboard(
        binary,
        identity="redacted",
        runner=runner,
    )

    assert snapshot.connection_mode == "dashboard"
    assert calls[0][0] == (
        str(binary),
        "dashboard",
        "--timeout",
        "30",
        "--identity",
        "redacted",
    )
    assert "cookie" not in " ".join(calls[0][0]).casefold()
    assert "refresh" not in " ".join(calls[0][0]).casefold()


def test_serve_supervisor_keeps_token_out_of_argv_and_binds_loopback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "codexbar"
    binary.write_text("binary", encoding="utf-8")
    binary.chmod(0o755)
    starts = []

    class Process:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def wait(self, timeout=None):
            del timeout
            self.terminated = True
            return 0

    def popen(args, **kwargs):
        starts.append((args, kwargs))
        return Process()

    def http_reader(url, **kwargs):
        if url.endswith("/health"):
            return b'{"status":"ok","version":"0.50.0"}'
        assert kwargs["token"]
        return _snapshot_bytes()

    monkeypatch.setattr(codexbar_compat, "_free_loopback_port", lambda: 49152)
    supervisor = CodexBarServeSupervisor(
        binary,
        identity="redacted",
        popen=popen,
        http_reader=http_reader,
    )

    snapshot = supervisor.snapshot()
    supervisor.close()

    args, kwargs = starts[0]
    assert snapshot.connection_mode == "serve"
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--port") + 1] == "49152"
    token = kwargs["env"]["CODEXBAR_DASHBOARD_TOKEN"]
    assert len(token) == 64
    assert token not in args
    assert "--dashboard-token" not in args


def test_auto_mode_falls_back_to_one_shot_when_serve_is_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "codexbar"
    binary.write_text("binary", encoding="utf-8")
    binary.chmod(0o755)
    expected = parse_codexbar_snapshot(
        _snapshot_bytes(),
        connection_mode="dashboard",
    )

    class FailingSupervisor:
        def __init__(self, *_args, **_kwargs):
            pass

        def snapshot(self):
            raise CodexBarCompatibilityError("codexbar_serve_failed")

        def close(self):
            pass

    monkeypatch.setattr(codexbar_compat, "codexbar_version", lambda _binary: "0.50.0")
    client = CodexBarClient(
        binary=binary,
        connection_mode="auto",
        supervisor_factory=FailingSupervisor,
        dashboard_reader=lambda _binary, *, identity: expected,
    )

    assert client.fetch() == expected
    client.close()


def test_bounded_command_rejects_oversized_stdout() -> None:
    with pytest.raises(CodexBarCompatibilityError) as overflow:
        run_bounded_command(
            (
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 10000)",
            ),
            timeout=5.0,
            maximum_stdout=100,
        )
    assert overflow.value.reason == CODEXBAR_REASON_SCHEMA
