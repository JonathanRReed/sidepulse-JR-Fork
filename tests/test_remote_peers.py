"""Two Macs, one ledger -- and every bound that keeps the second Mac from
becoming a new way for this app to hang, leak, or lie.

Every test here was written by deleting the behaviour it names from
`remote_peers.py`, watching it fail, and restoring it. The ones that
matter most, and what they refuse:

  * A peer is a VIEWER target. No argv this module can build carries a
    remote command, so "read the other Mac" can never become "drive the
    other Mac".
  * A hostname reaches argv. `-oProxyCommand=...` is a hostname-shaped
    string that runs a LOCAL command; it is rejected before use.
  * An unreachable Mac must never stall the refresh. Deadline, per-peer
    timeout, and a breaker each independently prove it.
  * Remote rows show in the ledger and stay OUT of the interrupt budget
    until the owner says otherwise. A light on this desk must mean
    something on this desk.
  * No capacity number rides this wire. A remote provider's reading has
    no binding lane here, and capacity_authority is the only door.
  * tailscale's stderr can contain `tskey-` auth keys. It is never read.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sidepulse import remote_peers
from sidepulse.models import AgentMode, AgentStatus

NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)


# --- helpers ----------------------------------------------------------


class Clock:
    """A monotonic clock the test drives by hand."""

    def __init__(self, start: float = 100.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def status(
    agent_id: str = "claude:session:aaa",
    *,
    mode: AgentMode = AgentMode.WORKING,
    display_name: str = "sidepulse",
    provider: str = "claude",
    updated_at: datetime | None = None,
    event_name: str = "PreToolUse",
    message: str | None = None,
    tool_name: str | None = None,
    cwd: str | None = None,
    origin: str | None = None,
    session_id: str | None = None,
) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=agent_id,
        display_name=display_name,
        mode=mode,
        updated_at=updated_at or NOW,
        event_name=event_name,
        session_id=session_id,
        cwd=cwd,
        tool_name=tool_name,
        message=message,
        origin=origin,
    )


def peer(host: str = "mac-b", machine: str = "mac-b") -> remote_peers.TailscalePeer:
    return remote_peers.TailscalePeer(host=host, machine=machine, operating_system="macos")


def document(
    *,
    machine: str = "mac-b",
    statuses: tuple[AgentStatus, ...] | None = None,
    generated_at: datetime | None = None,
    include_messages: bool = False,
) -> str:
    return remote_peers.build_remote_ledger_document(
        machine=machine,
        statuses=statuses if statuses is not None else (status(),),
        generated_at=generated_at or NOW,
        include_messages=include_messages,
    )


def reader_returning(payload: str):
    def read(host: str, remote_path: str, *, timeout: float, max_bytes: int) -> str:
        return payload

    return read


def reader_raising(failure: str):
    def read(host: str, remote_path: str, *, timeout: float, max_bytes: int) -> str:
        raise remote_peers.RemotePeerError(failure)

    return read


# --- A. Optional and inert -------------------------------------------


def test_discovery_is_empty_when_tailscale_is_absent():
    """Tailscale is not a dependency. Absent CLI == empty tailnet."""

    def missing_runner(arguments, timeout):
        raise FileNotFoundError("/usr/local/bin/tailscale")

    assert remote_peers.discover_peers(runner=missing_runner) == ()


def test_discovery_never_raises_when_the_cli_misbehaves():
    def hostile_runner(arguments, timeout):
        raise RuntimeError("tailscaled is having a day")

    assert remote_peers.discover_peers(runner=hostile_runner) == ()


def test_merge_with_no_peers_returns_exactly_the_local_rows():
    """Switching this feature off must not perturb the local ledger."""
    local = (status("claude:session:a"), status("codex:session:b"))
    merged = remote_peers.merge_ledger(
        local_statuses=local,
        local_machine="mac-a",
        peer_ledgers=(),
        now=NOW,
    )
    assert [row.status.agent_id for row in merged.rows] == [
        "claude:session:a",
        "codex:session:b",
    ]
    assert merged.remote_rows == ()
    assert all(row.is_remote is False for row in merged.rows)


def test_collect_is_inert_when_disabled():
    """Disabled means no discovery call at all, not a filtered result."""
    calls: list[object] = []

    def runner(arguments, timeout):
        calls.append(arguments)
        return "{}"

    result = remote_peers.collect_remote_ledgers(
        runner=runner,
        settings=remote_peers.RemotePeerSettings(enabled=False),
    )
    assert result == remote_peers.PeerRefreshResult()
    assert calls == []


def test_refresh_without_a_reader_reports_no_transport_and_calls_nothing():
    result = remote_peers.refresh_peers((peer(),), reader=None)
    assert result.ledgers == ()
    assert result.attempted == 0
    assert [health.failure for health in result.health] == [remote_peers.FAILURE_NO_TRANSPORT]


# --- B. A hostname reaches argv --------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "-oProxyCommand=/bin/sh",  # runs a LOCAL command via ssh option
        "-l",
        "--",
        "mac-b;rm -rf /",
        "mac b",
        "mac-b\nrm",
        "mac-b\x00",
        "mac-b:2222",
        "user@mac-b",
        "mac-b/../etc",
        "$(whoami)",
        "`id`",
        "",
        "mac-b.",
        "a" * 254,
    ],
)
def test_hostile_hostnames_are_rejected(hostile):
    assert remote_peers.peer_host_is_safe(hostile) is False


@pytest.mark.parametrize("safe", ["mac-b", "mac-b.tailnet.ts.net", "MacBookPro-2", "a"])
def test_ordinary_hostnames_are_accepted(safe):
    assert remote_peers.peer_host_is_safe(safe) is True


def test_a_hostile_hostname_cannot_reach_argv():
    """The rejection is at the command builder, not only at discovery."""
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        remote_peers.sftp_command(
            "-oProxyCommand=/bin/sh",
            "/tmp/x.json",
            Path("/tmp/out.json"),
            timeout=2.0,
            max_bytes=1024,
        )
    assert caught.value.failure == remote_peers.FAILURE_UNSAFE_HOST


def test_refresh_refuses_an_unsafe_host_without_calling_the_reader():
    called: list[str] = []

    def read(host, remote_path, *, timeout, max_bytes):
        called.append(host)
        return document()

    unsafe = remote_peers.TailscalePeer.__new__(remote_peers.TailscalePeer)
    object.__setattr__(unsafe, "host", "-oProxyCommand=/bin/sh")
    object.__setattr__(unsafe, "machine", "mac-b")
    object.__setattr__(unsafe, "operating_system", "macos")

    result = remote_peers.refresh_peers((unsafe,), reader=read)
    assert called == []
    assert [health.failure for health in result.health] == [remote_peers.FAILURE_UNSAFE_HOST]


@pytest.mark.parametrize(
    "hostile",
    [
        "relative/path.json",
        "/tmp/*.json",
        "/tmp/a b.json",
        "/tmp/../etc/shadow",
        "/tmp/x:y.json",
        "/tmp/x\nz.json",
        "",
    ],
)
def test_hostile_remote_paths_are_rejected(hostile):
    assert remote_peers.remote_path_is_safe(hostile) is False


def test_default_remote_path_is_safe():
    assert remote_peers.remote_path_is_safe(remote_peers.DEFAULT_REMOTE_LEDGER_PATH) is True


# --- C. Discovery ----------------------------------------------------


def _status_json(**overrides) -> str:
    payload = {
        "Self": {"HostName": "mac-a", "DNSName": "mac-a.tailnet.ts.net.", "OS": "macOS", "Online": True},
        "Peer": {
            "nodekey:1": {
                "HostName": "mac-b",
                "DNSName": "mac-b.tailnet.ts.net.",
                "OS": "macOS",
                "Online": True,
            },
            "nodekey:2": {
                "HostName": "linux-box",
                "DNSName": "linux-box.tailnet.ts.net.",
                "OS": "linux",
                "Online": True,
            },
            "nodekey:3": {
                "HostName": "iphone",
                "DNSName": "iphone.tailnet.ts.net.",
                "OS": "iOS",
                "Online": True,
            },
            "nodekey:4": {
                "HostName": "mac-c",
                "DNSName": "mac-c.tailnet.ts.net.",
                "OS": "macOS",
                "Online": False,
            },
            "nodekey:5": {
                "HostName": "mac-a",
                "DNSName": "mac-a.tailnet.ts.net.",
                "OS": "macOS",
                "Online": True,
            },
        },
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_discovery_selects_online_agent_capable_peers_only():
    peers = remote_peers.parse_tailscale_status(_status_json())
    assert [item.host for item in peers] == ["linux-box", "mac-b"]


def test_discovery_excludes_self_by_dns_label():
    """Nodekey 5 is this machine under a second entry. It must not become
    a peer -- fetching ourselves would double every local row."""
    peers = remote_peers.parse_tailscale_status(_status_json())
    assert "mac-a" not in {item.host for item in peers}


def test_discovery_is_capped():
    entries = {
        f"nodekey:{index}": {
            "HostName": f"mac-{index}",
            "DNSName": f"mac-{index}.tailnet.ts.net.",
            "OS": "macOS",
            "Online": True,
        }
        for index in range(50)
    }
    peers = remote_peers.parse_tailscale_status(_status_json(Peer=entries))
    assert len(peers) == remote_peers.MAX_PEERS


def test_discovery_rejects_a_peer_whose_dns_label_is_argv_hostile():
    entries = {
        "nodekey:9": {
            "HostName": "evil",
            "DNSName": "-oProxyCommand=id.tailnet.ts.net.",
            "OS": "macOS",
            "Online": True,
        }
    }
    assert remote_peers.parse_tailscale_status(_status_json(Peer=entries)) == ()


def test_discovery_tolerates_unknown_tailscale_fields():
    """Tailscale's schema is theirs, not ours. New keys must not blank
    the peer list."""
    entries = {
        "nodekey:1": {
            "HostName": "mac-b",
            "DNSName": "mac-b.tailnet.ts.net.",
            "OS": "macOS",
            "Online": True,
            "SomeFutureField": {"nested": [1, 2, 3]},
        }
    }
    assert [item.host for item in remote_peers.parse_tailscale_status(_status_json(Peer=entries))] == [
        "mac-b"
    ]


def test_discovery_never_reads_tailscale_stderr(monkeypatch):
    """tailscale writes `tskey-...` auth keys to stderr. Reading it is how
    a secret ends up in a log."""
    secret = "tskey-auth-kSECRETVALUE12345"
    seen: dict[str, object] = {}

    class Completed:
        returncode = 1
        stdout = ""

        @property
        def stderr(self):
            seen["stderr_read"] = True
            return secret

    monkeypatch.setattr(remote_peers, "tailscale_cli_path", lambda: Path("/usr/bin/true"))
    monkeypatch.setattr(remote_peers.subprocess, "run", lambda *a, **k: Completed())

    with pytest.raises(remote_peers.RemotePeerError) as caught:
        remote_peers._run_tailscale_cli(("status", "--json"), 1.0)

    assert "stderr_read" not in seen
    assert caught.value.failure == remote_peers.FAILURE_UNREACHABLE
    assert secret not in str(caught.value) and secret not in repr(caught.value)


def test_discovery_output_is_size_capped(monkeypatch):
    class Completed:
        returncode = 0
        stdout = "x" * (remote_peers.MAX_DISCOVERY_BYTES + 1)
        stderr = ""

    monkeypatch.setattr(remote_peers, "tailscale_cli_path", lambda: Path("/usr/bin/true"))
    monkeypatch.setattr(remote_peers.subprocess, "run", lambda *a, **k: Completed())

    with pytest.raises(remote_peers.RemotePeerError) as caught:
        remote_peers._run_tailscale_cli(("status", "--json"), 1.0)
    assert caught.value.failure == remote_peers.FAILURE_TOO_LARGE


# --- D. Publishing ---------------------------------------------------


def test_sub_agents_are_absent_from_the_payload_entirely():
    """Not filtered at the receiver -- never sent. Sub-agents are never a
    row, never a light, never an interrupt."""
    payload = json.loads(
        document(
            statuses=(
                status("claude:session:main"),
                status("claude:agent:worker-1"),
                status("claude:agent:worker-2"),
            )
        )
    )
    assert [row["agent_id"] for row in payload["rows"]] == ["claude:session:main"]


def test_published_rows_are_capped_and_the_drop_is_declared():
    statuses = tuple(status(f"claude:session:{index:03d}") for index in range(120))
    payload = json.loads(document(statuses=statuses))
    assert len(payload["rows"]) == remote_peers.MAX_ROWS_PER_PEER
    assert payload["truncated_rows"] == 120 - remote_peers.MAX_ROWS_PER_PEER


def test_published_document_respects_the_byte_ceiling():
    statuses = tuple(
        status(f"claude:session:{index:03d}", display_name="n" * remote_peers.MAX_DISPLAY_NAME_CHARS)
        for index in range(64)
    )
    payload = remote_peers.build_remote_ledger_document(
        machine="mac-b",
        statuses=statuses,
        generated_at=NOW,
        max_bytes=2048,
    )
    assert len(payload.encode("utf-8")) <= 2048
    assert json.loads(payload)["truncated_rows"] > 0


def test_urgent_rows_survive_truncation():
    """Truncation drops the calm, never the blocked.

    The blocked session is deliberately named so it sorts LAST by id: if
    urgency were not the primary sort key, alphabetical order alone would
    drop the one row that matters.
    """
    statuses = (
        *[status(f"claude:session:aaa-idle{index:03d}", mode=AgentMode.IDLE_READY) for index in range(80)],
        status("claude:session:zzz-blocked", mode=AgentMode.BLOCKED_ERROR),
    )
    payload = json.loads(document(statuses=statuses))
    assert "claude:session:zzz-blocked" in {row["agent_id"] for row in payload["rows"]}


def test_messages_are_withheld_by_default_and_sendable_on_request():
    withheld = json.loads(document(statuses=(status(message="rm -rf the database?"),)))
    assert withheld["rows"][0]["message"] is None

    shared = json.loads(
        document(statuses=(status(message="rm -rf the database?"),), include_messages=True)
    )
    assert shared["rows"][0]["message"] == "rm -rf the database?"


def test_published_document_carries_no_local_paths_or_launch_origin():
    payload = json.loads(
        document(statuses=(status(cwd="/Users/jonathanreed/secret-project", origin="iTerm"),))
    )
    encoded = json.dumps(payload)
    assert "secret-project" not in encoded
    assert "iTerm" not in encoded


def test_the_wire_schema_has_no_capacity_field():
    """A capacity reading may only reach a user-visible consumer through
    capacity_authority.select_binding_lanes. A remote provider's raw
    number has no binding lane on this machine, so it has no field here
    -- and the exact-field parser refuses a document that grows one."""
    assert not any(
        "capacity" in field or "percent" in field or "quota" in field
        for field in (*remote_peers._ROW_FIELDS, *remote_peers._DOCUMENT_FIELDS)
    )

    payload = json.loads(document())
    payload["rows"][0]["capacity_percent"] = 91.0
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        remote_peers.parse_remote_ledger_document(json.dumps(payload))
    assert caught.value.failure == remote_peers.FAILURE_MALFORMED


def test_publishing_is_off_by_default_and_writes_nothing(tmp_path):
    """A second Mac reading this file is something the owner turns on."""
    target = tmp_path / "remote-ledger.json"
    assert remote_peers.publish_local_ledger((status(),), path=target) is None
    assert not target.exists()


def test_publishing_when_enabled_writes_a_parseable_private_document(tmp_path):
    target = tmp_path / "remote-ledger.json"
    written = remote_peers.publish_local_ledger(
        (status(),),
        machine="mac-a",
        generated_at=NOW,
        path=target,
        settings=remote_peers.RemotePeerSettings(publish_enabled=True),
    )
    assert written == target
    assert target.stat().st_mode & 0o777 == 0o600
    ledger = remote_peers.parse_remote_ledger_document(target.read_text(encoding="utf-8"))
    assert ledger.machine == "mac-a"
    assert ledger.rows[0].source_agent_id == "claude:session:aaa"


def test_publishing_honours_the_message_setting(tmp_path):
    target = tmp_path / "remote-ledger.json"
    remote_peers.publish_local_ledger(
        (status(message="delete production?"),),
        machine="mac-a",
        generated_at=NOW,
        path=target,
        settings=remote_peers.RemotePeerSettings(publish_enabled=True),
    )
    assert "delete production?" not in target.read_text(encoding="utf-8")


def test_the_local_machine_name_is_held_to_the_peer_rules(monkeypatch):
    """A name we would refuse from a peer is a name we do not publish."""
    monkeypatch.setattr(remote_peers.socket, "gethostname", lambda: "Jonathans-MacBook.local")
    assert remote_peers.local_machine_name() == "Jonathans-MacBook"

    monkeypatch.setattr(remote_peers.socket, "gethostname", lambda: "; rm -rf /")
    assert remote_peers.local_machine_name() == remote_peers.DEFAULT_MACHINE_NAME

    def unavailable():
        raise OSError("no hostname")

    monkeypatch.setattr(remote_peers.socket, "gethostname", unavailable)
    assert remote_peers.local_machine_name("fallback-name") == "fallback-name"


def test_publisher_rejects_an_unsafe_machine_name():
    with pytest.raises(ValueError):
        remote_peers.build_remote_ledger_document(
            machine="mac b; rm -rf /",
            statuses=(status(),),
            generated_at=NOW,
        )


# --- E. Parsing ------------------------------------------------------


def test_round_trip_preserves_the_row():
    ledger = remote_peers.parse_remote_ledger_document(
        document(statuses=(status("claude:session:aaa", mode=AgentMode.WAITING_FOR_INPUT),))
    )
    assert ledger.machine == "mac-b"
    assert len(ledger.rows) == 1
    row = ledger.rows[0]
    assert row.is_remote is True
    assert row.machine == "mac-b"
    assert row.source_agent_id == "claude:session:aaa"
    assert row.status.mode is AgentMode.WAITING_FOR_INPUT


def test_remote_agent_ids_are_namespaced_and_cannot_collide_with_local():
    ledger = remote_peers.parse_remote_ledger_document(
        document(statuses=(status("claude:session:aaa"),))
    )
    merged = remote_peers.merge_ledger(
        local_statuses=(status("claude:session:aaa"),),
        local_machine="mac-a",
        peer_ledgers=(ledger,),
        now=NOW,
    )
    keys = [row.key for row in merged.rows]
    assert len(keys) == len(set(keys)) == 2
    assert "remote:mac-b:claude:session:aaa" in keys


def test_a_namespaced_remote_row_is_not_mistaken_for_a_sub_agent():
    ledger = remote_peers.parse_remote_ledger_document(
        document(statuses=(status("claude:session:aaa"),))
    )
    assert ledger.rows[0].status.is_subagent is False


def test_parser_drops_a_sub_agent_row_a_stale_peer_build_might_send():
    payload = json.loads(document())
    payload["rows"].append(dict(payload["rows"][0], agent_id="claude:agent:worker"))
    ledger = remote_peers.parse_remote_ledger_document(json.dumps(payload))
    assert [row.source_agent_id for row in ledger.rows] == ["claude:session:aaa"]
    assert ledger.dropped_rows == 1


@pytest.mark.parametrize(
    ("mutate", "failure"),
    [
        (lambda d: d.__setitem__("version", 2), remote_peers.FAILURE_UNSUPPORTED_VERSION),
        (lambda d: d.__setitem__("document", "something-else"), remote_peers.FAILURE_MALFORMED),
        (lambda d: d.__setitem__("machine", "mac b; id"), remote_peers.FAILURE_MALFORMED),
        (lambda d: d.__setitem__("generated_at", "not-a-time"), remote_peers.FAILURE_MALFORMED),
        (lambda d: d.__setitem__("rows", "not-a-list"), remote_peers.FAILURE_TOO_LARGE),
        (lambda d: d.pop("truncated_rows"), remote_peers.FAILURE_MALFORMED),
        (lambda d: d.__setitem__("extra", 1), remote_peers.FAILURE_MALFORMED),
    ],
)
def test_malformed_documents_are_refused_by_name(mutate, failure):
    payload = json.loads(document())
    mutate(payload)
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        remote_peers.parse_remote_ledger_document(json.dumps(payload))
    assert caught.value.failure == failure


def test_an_unreadable_timestamp_is_malformed_not_now():
    """models.parse_datetime falls back to *now* for junk. Here that
    would render a long-dead remote row as fresh."""
    payload = json.loads(document())
    payload["rows"][0]["updated_at"] = "garbage"
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        remote_peers.parse_remote_ledger_document(json.dumps(payload))
    assert caught.value.failure == remote_peers.FAILURE_MALFORMED


def test_oversize_payloads_are_refused_before_parsing():
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        remote_peers.parse_remote_ledger_document("x" * (remote_peers.MAX_PAYLOAD_BYTES + 1))
    assert caught.value.failure == remote_peers.FAILURE_TOO_LARGE


def test_row_count_is_capped_at_parse_time_too():
    payload = json.loads(document())
    payload["rows"] = [
        dict(payload["rows"][0], agent_id=f"claude:session:{index:04d}")
        for index in range(remote_peers.MAX_ROWS_PER_PEER + 1)
    ]
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        remote_peers.parse_remote_ledger_document(json.dumps(payload))
    assert caught.value.failure == remote_peers.FAILURE_TOO_LARGE


def test_duplicate_json_keys_are_refused():
    hostile = '{"document":"sidepulse-remote-ledger","version":1,"version":9,"machine":"mac-b",'
    hostile += '"generated_at":"2026-08-14T12:00:00+00:00","rows":[],"truncated_rows":0}'
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        remote_peers.parse_remote_ledger_document(hostile)
    assert caught.value.failure == remote_peers.FAILURE_MALFORMED


def test_json_constants_are_refused_at_the_json_layer(monkeypatch):
    """`type(float("nan")) is float` is True, so a numeric field's own
    type check would wave a NaN straight through. Constants are refused
    during decode, before any field is examined -- which is what makes
    this schema safe to grow a numeric field later.

    Asserting the *mechanism* on purpose: the outcome alone is currently
    also produced by the int check on `truncated_rows`, so an
    outcome-only test here could not fail.
    """
    seen: list[str] = []

    def spy(value: str):
        seen.append(value)
        raise ValueError("json constant")

    monkeypatch.setattr(remote_peers, "_reject_constant", spy)
    payload = document().replace('"truncated_rows":0', '"truncated_rows":NaN')
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        remote_peers.parse_remote_ledger_document(payload)
    assert seen == ["NaN"]
    assert caught.value.failure == remote_peers.FAILURE_MALFORMED


def test_a_nan_field_is_refused_however_it_arrives():
    payload = document().replace('"truncated_rows":0', '"truncated_rows":NaN')
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        remote_peers.parse_remote_ledger_document(payload)
    assert caught.value.failure == remote_peers.FAILURE_MALFORMED


def test_a_peer_cannot_publish_rows_under_another_machines_name():
    """Identity comes from the channel, never from the payload. A peer
    that calls itself `mac-c` still appears as the peer we contacted --
    otherwise one Mac's rows appear under another Mac's name and the
    ledger stops being a ledger."""
    ledger = remote_peers.parse_remote_ledger_document(
        document(machine="mac-c"), machine="mac-b"
    )
    assert ledger.machine == "mac-b"
    assert ledger.claimed_machine == "mac-c"
    assert ledger.rows[0].machine == "mac-b"
    assert ledger.rows[0].key.startswith("remote:mac-b:")


def test_the_channel_identity_reaches_the_merged_ledger():
    """The whole point, through refresh_peers: a lying payload cannot
    relabel a row."""
    result = remote_peers.refresh_peers(
        (peer("mac-b", "mac-b"),),
        reader=reader_returning(document(machine="mac-c")),
        now_monotonic=Clock(),
    )
    merged = remote_peers.merge_ledger(
        local_statuses=(),
        local_machine="mac-a",
        peer_ledgers=result.ledgers,
        now=NOW,
    )
    assert [row.machine for row in merged.remote_rows] == ["mac-b"]


def test_a_hostname_and_a_local_hostname_may_disagree_without_losing_the_peer():
    """Tailscale's HostName and a Mac's own hostname routinely differ.
    That must not silently blank the peer's rows."""
    result = remote_peers.refresh_peers(
        (peer("mac-b", "mac-b"),),
        reader=reader_returning(document(machine="MacBookPro-2")),
        now_monotonic=Clock(),
    )
    assert len(result.ledgers) == 1
    assert result.ledgers[0].rows and result.health[0].reachable is True


def test_an_unsafe_remote_path_stops_the_refresh_before_any_reader_runs():
    called: list[str] = []

    def read(host, remote_path, *, timeout, max_bytes):
        called.append(host)
        return document()

    result = remote_peers.refresh_peers(
        (peer(),), reader=read, remote_path="/tmp/*; rm -rf /", now_monotonic=Clock()
    )
    assert called == []
    assert result.health[0].failure == remote_peers.FAILURE_UNSAFE_HOST


def test_control_characters_in_a_display_name_are_scrubbed():
    payload = json.loads(document())
    payload["rows"][0]["display_name"] = "ledger\x1b[2Jwiped\n\r"
    ledger = remote_peers.parse_remote_ledger_document(json.dumps(payload))
    assert ledger.rows[0].status.display_name == "ledger [2Jwiped"


# --- F. The transport is a read, not a shell -------------------------


def test_the_fetch_argv_contains_no_remote_command():
    """`sftp host:path local` uses the SFTP subsystem. There is no place
    in this argv for `cat`, our CLI, or anything else the peer would
    execute -- which is what makes this a viewer and not an orchestrator."""
    argv = remote_peers.sftp_command(
        "mac-b",
        "/home/x/remote-ledger.json",
        Path("/tmp/out.json"),
        timeout=4.0,
        max_bytes=remote_peers.MAX_PAYLOAD_BYTES,
    )
    assert argv[0].endswith("/sftp")
    assert argv[-2] == "mac-b:/home/x/remote-ledger.json"
    assert argv[-1] == "/tmp/out.json"

    # Walk the argv exactly: the only positional tokens are the binary,
    # the `host:path` source and the local destination. There is nowhere
    # for a remote command to be, which is the property under test.
    positional: list[str] = []
    index = 1
    while index < len(argv):
        token = argv[index]
        if token in {"-o", "-l"}:
            index += 2
            continue
        if token == "-q":
            index += 1
            continue
        positional.append(token)
        index += 1
    assert positional == ["mac-b:/home/x/remote-ledger.json", "/tmp/out.json"]

    # sftp's own command-carrying switches are absent: -s names an
    # alternative remote subsystem/command, -b a batch script.
    assert "-s" not in argv and "-b" not in argv
    options = {argv[position + 1] for position, value in enumerate(argv) if value == "-o"}
    assert not any(option.startswith(("RemoteCommand", "ProxyCommand", "LocalCommand")) for option in options)


def test_the_fetch_never_offers_or_prompts_for_a_credential():
    argv = remote_peers.sftp_command(
        "mac-b", "/x.json", Path("/tmp/out"), timeout=4.0, max_bytes=1024
    )
    options = {argv[index + 1] for index, value in enumerate(argv) if value == "-o"}
    assert "BatchMode=yes" in options
    assert "NumberOfPasswordPrompts=0" in options
    assert "PasswordAuthentication=no" in options
    assert "KbdInteractiveAuthentication=no" in options
    # No identity file of our choosing: the user's own agent/config decides.
    assert "-i" not in argv
    assert not any(argument.startswith("IdentityFile") for argument in argv)


def test_the_child_environment_is_an_allowlist(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/test")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET")
    monkeypatch.setenv("TS_AUTHKEY", "tskey-auth-SECRET")

    environment = remote_peers._child_environment()
    assert environment["HOME"] == "/Users/test"
    assert environment["SSH_AUTH_SOCK"] == "/tmp/agent.sock"
    assert "ANTHROPIC_API_KEY" not in environment
    assert "TS_AUTHKEY" not in environment


def test_bytes_on_the_wire_are_bounded_by_the_bandwidth_limit():
    """Timeout alone bounds TIME, not BYTES. The `-l` limit is what stops
    a broken peer from filling this disk inside the window."""
    for timeout in (0.5, 1.0, 4.0, 16.0):
        limit = remote_peers.transfer_limit_kbits(remote_peers.MAX_PAYLOAD_BYTES, timeout)
        worst = remote_peers.worst_case_transfer_bytes(limit, timeout)
        assert worst <= remote_peers.MAX_TRANSFER_BYTES
        assert worst >= remote_peers.MAX_PAYLOAD_BYTES

    argv = remote_peers.sftp_command(
        "mac-b", "/x.json", Path("/tmp/out"), timeout=4.0, max_bytes=remote_peers.MAX_PAYLOAD_BYTES
    )
    assert "-l" in argv


def test_the_reader_reads_the_file_and_removes_its_scratch(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    payload = document()
    monkeypatch.setattr(remote_peers, "_validated_sftp_path", lambda path: path)

    def fake_run(argv, **kwargs):
        Path(argv[-1]).write_text(payload, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(remote_peers.subprocess, "run", fake_run)

    reader = remote_peers.SecureShellPeerReader(scratch_dir=scratch)
    assert reader("mac-b", "/x.json", timeout=2.0, max_bytes=remote_peers.MAX_PAYLOAD_BYTES) == payload
    assert list(scratch.iterdir()) == []


def test_the_reader_never_leaks_stderr_into_its_failure(tmp_path, monkeypatch):
    secret = "tskey-auth-kSECRET"
    monkeypatch.setattr(remote_peers, "_validated_sftp_path", lambda path: path)
    monkeypatch.setattr(
        remote_peers.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 255, "", f"mac-b: {secret}"),
    )
    reader = remote_peers.SecureShellPeerReader(scratch_dir=tmp_path / "scratch")
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        reader("mac-b", "/x.json", timeout=2.0, max_bytes=1024)
    assert caught.value.failure == remote_peers.FAILURE_UNREACHABLE
    assert secret not in str(caught.value) and secret not in repr(caught.value)


def test_the_reader_refuses_an_oversize_file_and_still_cleans_up(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(remote_peers, "_validated_sftp_path", lambda path: path)

    def fake_run(argv, **kwargs):
        Path(argv[-1]).write_text("x" * 5000, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(remote_peers.subprocess, "run", fake_run)
    reader = remote_peers.SecureShellPeerReader(scratch_dir=scratch)
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        reader("mac-b", "/x.json", timeout=2.0, max_bytes=1024)
    assert caught.value.failure == remote_peers.FAILURE_TOO_LARGE
    assert list(scratch.iterdir()) == []


def test_a_hung_transfer_becomes_a_timeout_not_a_hang(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_peers, "_validated_sftp_path", lambda path: path)

    def fake_run(argv, **kwargs):
        assert kwargs["timeout"] > 0
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(remote_peers.subprocess, "run", fake_run)
    reader = remote_peers.SecureShellPeerReader(scratch_dir=tmp_path / "scratch")
    with pytest.raises(remote_peers.RemotePeerError) as caught:
        reader("mac-b", "/x.json", timeout=2.0, max_bytes=1024)
    assert caught.value.failure == remote_peers.FAILURE_TIMED_OUT


# --- G. Breaker ------------------------------------------------------


def test_the_breaker_opens_after_repeated_failure_and_closes_on_cooldown():
    breaker = remote_peers.PeerBreaker(host="mac-b")
    for _ in range(remote_peers.PEER_BREAKER_TRIP_AFTER - 1):
        breaker = remote_peers.record_peer_failure(breaker, now=0.0)
        assert breaker.allows(0.0) is True

    breaker = remote_peers.record_peer_failure(breaker, now=0.0)
    assert breaker.is_open(0.0) is True
    assert breaker.is_open(remote_peers.PEER_BREAKER_COOLDOWN_SECONDS - 1) is True
    assert breaker.allows(remote_peers.PEER_BREAKER_COOLDOWN_SECONDS) is True


def test_one_success_forgives_the_breaker():
    breaker = remote_peers.PeerBreaker(host="mac-b", consecutive_failures=5, open_until_monotonic=999.0)
    assert remote_peers.record_peer_success(breaker).allows(0.0) is True


def test_an_open_breaker_skips_the_peer_without_contacting_it():
    called: list[str] = []

    def read(host, remote_path, *, timeout, max_bytes):
        called.append(host)
        return document()

    result = remote_peers.refresh_peers(
        (peer(),),
        reader=read,
        breakers=(remote_peers.PeerBreaker(host="mac-b", consecutive_failures=3, open_until_monotonic=1e9),),
        now_monotonic=Clock(),
    )
    assert called == []
    assert result.attempted == 0
    assert result.health[0].breaker_open is True
    assert result.health[0].failure == remote_peers.FAILURE_BREAKER_OPEN


def test_repeated_refresh_failures_trip_the_breaker_through_refresh():
    breakers: tuple[remote_peers.PeerBreaker, ...] = ()
    clock = Clock()
    for _ in range(remote_peers.PEER_BREAKER_TRIP_AFTER):
        result = remote_peers.refresh_peers(
            (peer(),),
            reader=reader_raising(remote_peers.FAILURE_UNREACHABLE),
            breakers=breakers,
            now_monotonic=clock,
        )
        breakers = result.breakers
    assert breakers[0].is_open(clock()) is True


# --- H. Nothing stalls a refresh -------------------------------------


def test_an_unreachable_peer_does_not_stop_the_reachable_ones():
    good = document(machine="mac-c", statuses=(status("claude:session:good"),))

    def read(host, remote_path, *, timeout, max_bytes):
        if host == "mac-b":
            raise remote_peers.RemotePeerError(remote_peers.FAILURE_UNREACHABLE)
        return good

    result = remote_peers.refresh_peers(
        (peer("mac-b", "mac-b"), peer("mac-c", "mac-c")),
        reader=read,
        now_monotonic=Clock(),
    )
    assert [ledger.machine for ledger in result.ledgers] == ["mac-c"]
    assert {health.host: health.reachable for health in result.health} == {
        "mac-b": False,
        "mac-c": True,
    }


def test_the_whole_refresh_stops_at_its_deadline_without_contacting_more_peers():
    """Eight peers times four seconds is thirty-two seconds of stalled
    menu. The deadline is the thing that forbids it."""
    clock = Clock()
    contacted: list[str] = []

    def slow(host, remote_path, *, timeout, max_bytes):
        contacted.append(host)
        clock.advance(4.0)
        raise remote_peers.RemotePeerError(remote_peers.FAILURE_TIMED_OUT)

    result = remote_peers.refresh_peers(
        (peer("mac-b", "mac-b"), peer("mac-c", "mac-c"), peer("mac-d", "mac-d")),
        reader=slow,
        now_monotonic=clock,
        per_peer_timeout_seconds=4.0,
        overall_deadline_seconds=8.0,
    )
    assert contacted == ["mac-b", "mac-c"]
    assert result.attempted == 2
    assert result.health[-1].failure == remote_peers.FAILURE_DEADLINE_EXCEEDED
    assert clock() - 100.0 <= 8.0


def test_a_late_peer_gets_only_the_remaining_budget_as_its_timeout():
    clock = Clock()
    offered: list[float] = []

    def read(host, remote_path, *, timeout, max_bytes):
        offered.append(timeout)
        clock.advance(6.0)
        raise remote_peers.RemotePeerError(remote_peers.FAILURE_TIMED_OUT)

    remote_peers.refresh_peers(
        (peer("mac-b", "mac-b"), peer("mac-c", "mac-c")),
        reader=read,
        now_monotonic=clock,
        per_peer_timeout_seconds=4.0,
        overall_deadline_seconds=8.0,
    )
    assert offered == [4.0, 2.0]


def test_peer_count_is_capped():
    contacted: list[str] = []

    def read(host, remote_path, *, timeout, max_bytes):
        contacted.append(host)
        raise remote_peers.RemotePeerError(remote_peers.FAILURE_UNREACHABLE)

    peers = tuple(peer(f"mac-{index}", f"mac-{index}") for index in range(30))
    remote_peers.refresh_peers(
        peers, reader=read, now_monotonic=Clock(), overall_deadline_seconds=1e6
    )
    assert len(contacted) == remote_peers.MAX_PEERS


def test_a_reader_that_explodes_is_contained():
    def exploding(host, remote_path, *, timeout, max_bytes):
        raise MemoryError("transport went wrong in a new way")

    result = remote_peers.refresh_peers((peer(),), reader=exploding, now_monotonic=Clock())
    assert result.health[0].failure == remote_peers.FAILURE_UNREACHABLE
    assert result.ledgers == ()


def test_every_reported_failure_is_from_the_closed_vocabulary():
    for failure in (
        remote_peers.FAILURE_MALFORMED,
        remote_peers.FAILURE_TOO_LARGE,
        remote_peers.FAILURE_TIMED_OUT,
        remote_peers.FAILURE_UNREACHABLE,
    ):
        result = remote_peers.refresh_peers(
            (peer(),), reader=reader_raising(failure), now_monotonic=Clock()
        )
        assert result.health[0].failure in remote_peers.PEER_FAILURES


# --- I. The merged view ----------------------------------------------


def test_remote_rows_are_marked_and_carry_their_machine():
    ledger = remote_peers.parse_remote_ledger_document(
        document(machine="mac-b", statuses=(status(display_name="sidepulse"),))
    )
    merged = remote_peers.merge_ledger(
        local_statuses=(status("claude:session:local", display_name="jr-bar"),),
        local_machine="mac-a",
        peer_ledgers=(ledger,),
        now=NOW,
    )
    remote_row = merged.remote_rows[0]
    assert remote_row.is_remote is True
    assert remote_row.machine == "mac-b"
    assert remote_row.remote_marker == "Remote"
    assert "mac-b" in remote_row.ledger_label
    assert merged.local_rows[0].remote_marker is None
    assert merged.local_rows[0].ledger_label == "jr-bar"


def test_a_remote_row_is_not_locally_actionable():
    """There is no local session behind it. `origin` stays empty so no
    local open-action can claim this row."""
    ledger = remote_peers.parse_remote_ledger_document(document())
    row = ledger.rows[0]
    assert row.is_actionable_locally is False
    assert row.status.origin is None
    assert row.status.session_id is None
    assert row.status.cwd is None


def test_urgency_orders_the_ledger_and_local_wins_a_tie():
    """Urgency first, then THIS desk. The local machine is deliberately
    named so it sorts last alphabetically and last by display name: only
    the local/remote key can put it above the tied remote row."""
    remote = remote_peers.parse_remote_ledger_document(
        document(
            machine="mac-b",
            statuses=(
                status("claude:session:r1", mode=AgentMode.BLOCKED_ERROR, display_name="a-remote-blocked"),
                status("claude:session:r2", mode=AgentMode.WORKING, display_name="a-remote-working"),
            ),
        )
    )
    merged = remote_peers.merge_ledger(
        local_statuses=(status("claude:session:l1", mode=AgentMode.WORKING, display_name="z-local-working"),),
        local_machine="mac-z",
        peer_ledgers=(remote,),
        now=NOW,
    )
    assert [row.status.display_name for row in merged.rows] == [
        "a-remote-blocked",
        "z-local-working",
        "a-remote-working",
    ]


def test_stale_rows_sink_below_live_ones_of_the_same_urgency():
    """A row we can no longer vouch for must not outrank one we can.

    The stale peer is named so that machine order and display name would
    both put it FIRST: only the staleness key can sink it.
    """
    stale = remote_peers.parse_remote_ledger_document(
        document(
            machine="mac-b",
            statuses=(status("claude:session:s", display_name="a-stale"),),
            generated_at=NOW - timedelta(hours=1),
        )
    )
    live = remote_peers.parse_remote_ledger_document(
        document(
            machine="mac-c",
            statuses=(status("claude:session:l", display_name="z-live"),),
            generated_at=NOW,
        )
    )
    merged = remote_peers.merge_ledger(
        local_statuses=(),
        local_machine="mac-a",
        peer_ledgers=(stale, live),
        now=NOW,
    )
    assert [row.status.display_name for row in merged.rows] == ["z-live", "a-stale"]


def test_a_stale_peer_document_marks_its_rows_stale():
    old = remote_peers.parse_remote_ledger_document(
        document(generated_at=NOW - timedelta(seconds=600))
    )
    merged = remote_peers.merge_ledger(
        local_statuses=(),
        local_machine="mac-a",
        peer_ledgers=(old,),
        now=NOW,
    )
    assert merged.remote_rows[0].status.stale is True


def test_a_fresh_peer_document_does_not_mark_its_rows_stale():
    fresh = remote_peers.parse_remote_ledger_document(
        document(generated_at=NOW - timedelta(seconds=5))
    )
    merged = remote_peers.merge_ledger(
        local_statuses=(),
        local_machine="mac-a",
        peer_ledgers=(fresh,),
        now=NOW,
    )
    assert merged.remote_rows[0].status.stale is False


def test_a_peer_clock_running_ahead_does_not_make_its_rows_immortal():
    """abs()-style ageing pinned a future timestamp at age zero once
    already. A document from the future is not more trustworthy."""
    future = remote_peers.parse_remote_ledger_document(
        document(generated_at=NOW + timedelta(hours=3))
    )
    merged = remote_peers.merge_ledger(
        local_statuses=(),
        local_machine="mac-a",
        peer_ledgers=(future,),
        now=NOW,
    )
    assert merged.remote_rows[0].status.stale is True


def test_merged_remote_rows_are_capped_across_all_peers():
    ledgers = []
    for index in range(4):
        machine = f"mac-{index}"
        ledgers.append(
            remote_peers.parse_remote_ledger_document(
                document(
                    machine=machine,
                    statuses=tuple(
                        status(f"claude:session:{index}-{row:03d}") for row in range(40)
                    ),
                )
            )
        )
    merged = remote_peers.merge_ledger(
        local_statuses=(),
        local_machine="mac-a",
        peer_ledgers=ledgers,
        now=NOW,
    )
    assert len(merged.remote_rows) == remote_peers.MAX_MERGED_REMOTE_ROWS
    assert merged.dropped_remote_rows == 4 * 40 - remote_peers.MAX_MERGED_REMOTE_ROWS


def test_merge_reports_which_machines_are_present():
    ledger = remote_peers.parse_remote_ledger_document(document(machine="mac-b"))
    merged = remote_peers.merge_ledger(
        local_statuses=(status(),),
        local_machine="mac-a",
        peer_ledgers=(ledger,),
        now=NOW,
    )
    assert set(merged.machines) == {"mac-a", "mac-b"}


# --- J. Muted on this desk -------------------------------------------


def _merged_with_remote_ask(**merge_kwargs):
    ledger = remote_peers.parse_remote_ledger_document(
        document(
            machine="mac-b",
            statuses=(
                status(
                    "claude:session:asking",
                    mode=AgentMode.WAITING_FOR_INPUT,
                    event_name="PermissionRequest",
                ),
            ),
        )
    )
    return remote_peers.merge_ledger(
        local_statuses=(status("claude:session:local", mode=AgentMode.WORKING),),
        local_machine="mac-a",
        peer_ledgers=(ledger,),
        now=NOW,
        **merge_kwargs,
    )


def test_remote_agents_are_muted_in_the_interrupt_budget_by_default():
    """The ledger shows machine B's blocked agent. The LEDs on machine A
    stay calm. A light on this desk must mean something on this desk."""
    merged = _merged_with_remote_ask()
    assert len(merged.remote_rows) == 1
    eligible = remote_peers.interrupt_eligible_rows(merged)
    assert [row.key for row in eligible] == ["claude:session:local"]


def test_the_owner_can_unmute_one_machine():
    merged = _merged_with_remote_ask()
    policy = remote_peers.RemoteInterruptPolicy(unmuted_machines=frozenset({"mac-b"}))
    keys = [row.key for row in remote_peers.interrupt_eligible_rows(merged, policy)]
    assert "remote:mac-b:claude:session:asking" in keys


def test_unmuting_globally_still_honours_a_per_machine_mute():
    merged = _merged_with_remote_ask()
    policy = remote_peers.RemoteInterruptPolicy(
        default_muted=False, muted_machines=frozenset({"mac-b"})
    )
    assert [row.key for row in remote_peers.interrupt_eligible_rows(merged, policy)] == [
        "claude:session:local"
    ]


def test_a_stale_remote_ask_never_interrupts_even_when_unmuted():
    """A stale row is not proof that anyone is still waiting."""
    ledger = remote_peers.parse_remote_ledger_document(
        document(
            machine="mac-b",
            statuses=(status("claude:session:asking", mode=AgentMode.WAITING_FOR_INPUT),),
            generated_at=NOW - timedelta(hours=1),
        )
    )
    merged = remote_peers.merge_ledger(
        local_statuses=(),
        local_machine="mac-a",
        peer_ledgers=(ledger,),
        now=NOW,
    )
    policy = remote_peers.RemoteInterruptPolicy(default_muted=False)
    assert remote_peers.interrupt_eligible_rows(merged, policy) == ()


def test_local_rows_are_never_muted_by_this_policy():
    merged = _merged_with_remote_ask()
    policy = remote_peers.RemoteInterruptPolicy(muted_machines=frozenset({"mac-a", "mac-b"}))
    assert [row.key for row in remote_peers.interrupt_eligible_rows(merged, policy)] == [
        "claude:session:local"
    ]


def test_eligible_statuses_projection_matches_eligible_rows():
    merged = _merged_with_remote_ask()
    rows = remote_peers.interrupt_eligible_rows(merged)
    statuses = remote_peers.interrupt_eligible_statuses(merged)
    assert [row.status for row in rows] == list(statuses)


def test_toggling_one_machine_is_reversible():
    policy = remote_peers.RemoteInterruptPolicy()
    unmuted = policy.with_machine_muted("mac-b", False)
    assert unmuted.allows_machine("mac-b") is True
    assert unmuted.with_machine_muted("mac-b", True).allows_machine("mac-b") is False


# --- K. Settings -----------------------------------------------------


def test_settings_default_to_off_and_muted():
    settings = remote_peers.RemotePeerSettings()
    assert settings.enabled is False
    assert settings.publish_enabled is False
    assert settings.include_messages is False
    assert settings.interrupt_policy().allows_machine("mac-b") is False


def test_settings_round_trip_through_a_dict():
    settings = remote_peers.RemotePeerSettings(
        enabled=True,
        publish_enabled=True,
        remote_interrupts_muted=False,
        unmuted_machines=("mac-b",),
    )
    assert remote_peers.RemotePeerSettings.from_dict(settings.to_dict()) == settings.normalized()


def test_settings_clamp_hostile_values():
    settings = remote_peers.RemotePeerSettings.from_dict(
        {
            "max_peers": 10_000,
            "per_peer_timeout_seconds": 1e9,
            "refresh_deadline_seconds": float("inf"),
            "remote_ledger_path": "/tmp/*; rm -rf /",
            "unmuted_machines": ["mac b; id", "mac-b", "mac-b"],
        }
    )
    assert settings.max_peers == remote_peers.MAX_PEERS
    assert settings.per_peer_timeout_seconds <= remote_peers.DEFAULT_FETCH_TIMEOUT_SECONDS * 4
    assert settings.refresh_deadline_seconds <= remote_peers.DEFAULT_REFRESH_DEADLINE_SECONDS * 4
    assert settings.remote_ledger_path == remote_peers.DEFAULT_REMOTE_LEDGER_PATH
    assert settings.unmuted_machines == ("mac-b",)


def test_settings_survive_garbage():
    assert remote_peers.RemotePeerSettings.from_dict("nonsense") == remote_peers.RemotePeerSettings()
    assert remote_peers.RemotePeerSettings.from_dict(None) == remote_peers.RemotePeerSettings()


def test_collect_uses_settings_bounds(monkeypatch):
    contacted: list[str] = []

    def read(host, remote_path, *, timeout, max_bytes):
        contacted.append(host)
        raise remote_peers.RemotePeerError(remote_peers.FAILURE_UNREACHABLE)

    entries = {
        f"nodekey:{index}": {
            "HostName": f"mac-{index}",
            "DNSName": f"mac-{index}.tailnet.ts.net.",
            "OS": "macOS",
            "Online": True,
        }
        for index in range(10)
    }
    remote_peers.collect_remote_ledgers(
        reader=read,
        runner=lambda arguments, timeout: _status_json(Peer=entries),
        settings=remote_peers.RemotePeerSettings(enabled=True, max_peers=2),
        now_monotonic=Clock(),
    )
    assert len(contacted) == 2


# --- L. The whole path, once -----------------------------------------


def test_machine_b_blocked_appears_on_machine_a_and_stays_out_of_its_leds():
    """The acceptance test for this wave, end to end with no network:
    B publishes, A discovers, fetches, merges, and shows it -- and A's
    lights stay calm until the owner says otherwise."""
    published = remote_peers.build_remote_ledger_document(
        machine="mac-b",
        statuses=(
            status("claude:session:blocked", mode=AgentMode.BLOCKED_ERROR, display_name="deploy"),
            status("claude:agent:worker", mode=AgentMode.WORKING),
        ),
        generated_at=NOW,
    )
    peers = remote_peers.parse_tailscale_status(_status_json())
    result = remote_peers.refresh_peers(
        tuple(item for item in peers if item.host == "mac-b"),
        reader=reader_returning(published),
        now_monotonic=Clock(),
    )
    merged = remote_peers.merge_ledger(
        local_statuses=(status("claude:session:local", mode=AgentMode.WORKING, display_name="jr-bar"),),
        local_machine="mac-a",
        peer_ledgers=result.ledgers,
        health=result.health,
        now=NOW,
    )

    labels = [row.ledger_label for row in merged.rows]
    assert labels == ["deploy (mac-b)", "jr-bar"]
    assert len(merged.rows) == 2  # the remote sub-agent never travelled
    assert [row.key for row in remote_peers.interrupt_eligible_rows(merged)] == [
        "claude:session:local"
    ]
    unmuted = remote_peers.RemoteInterruptPolicy(unmuted_machines=frozenset({"mac-b"}))
    assert len(remote_peers.interrupt_eligible_rows(merged, unmuted)) == 2


def test_the_ledger_can_say_which_peers_it_could_not_reach():
    result = remote_peers.refresh_peers(
        (peer("mac-b", "mac-b"),),
        reader=reader_raising(remote_peers.FAILURE_TIMED_OUT),
        now_monotonic=Clock(),
    )
    merged = remote_peers.merge_ledger(
        local_statuses=(status(),),
        local_machine="mac-a",
        peer_ledgers=result.ledgers,
        health=result.health,
        now=NOW,
    )
    assert merged.remote_rows == ()
    assert merged.health[0].reachable is False
    assert merged.health[0].failure == remote_peers.FAILURE_TIMED_OUT
    assert merged.local_rows and merged.local_rows[0].status.agent_id == "claude:session:aaa"


def test_ledger_rows_reject_an_unsafe_machine_name():
    with pytest.raises(ValueError):
        remote_peers.LedgerRow(status=status(), machine="mac b; id")


def test_merge_rejects_an_unsafe_local_machine_name():
    with pytest.raises(ValueError):
        remote_peers.merge_ledger(
            local_statuses=(), local_machine="$(id)", peer_ledgers=(), now=NOW
        )


def test_replacing_a_row_keeps_its_remote_marking():
    """merge_ledger rewrites rows to mark staleness; the rewrite must not
    quietly turn a remote row local."""
    ledger = remote_peers.parse_remote_ledger_document(document())
    marked = replace(ledger.rows[0], status=replace(ledger.rows[0].status, stale=True))
    assert marked.is_remote is True
    assert marked.machine == "mac-b"
